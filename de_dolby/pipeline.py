"""Conversion pipelines: lossless strip (Profile 7/8) and re-encode (Profile 5/10)."""

import contextlib
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from de_dolby.codecs import Encoder, InputCodec, get_encoder, get_input_codec
from de_dolby.config import DEFAULT_MASTER_DISPLAY, DEFAULT_MAX_CLL, DEFAULT_MAX_FALL
from de_dolby.display import display_banner
from de_dolby.metadata import HDR10Metadata, extract_rpu, parse_rpu_metadata
from de_dolby.options import ConvertOptions
from de_dolby.probe import FileInfo, probe
from de_dolby.progress import (
    STEPS_LOSSLESS,
    STEPS_REENCODE,
    ProgressReporter,
    run_ffmpeg_with_progress,
)
from de_dolby.state import (
    ConversionState,
    create_initial_state,
    delete_state,
    get_resume_summary,
    is_step_completed,
    save_state,
    update_state_metadata,
    update_state_progress,
    validate_state_for_resume,
)
from de_dolby.tools import (
    check_encoder_available,
    run_dovi_tool,
    run_ffmpeg,
    run_mkvmerge,
    set_verbose,
)
from de_dolby.tracks import (
    TrackSelection,
    build_ffmpeg_audio_maps,
    build_ffmpeg_subtitle_maps,
    build_mkvmerge_track_args,
)
from de_dolby.utils import format_bytes
from de_dolby.validate import print_validation_result, validate_output

# ---------------------------------------------------------------------------
# Pipeline context — shared state passed through step functions
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """Mutable state bag passed through pipeline steps."""

    info: FileInfo
    input_codec: InputCodec
    output_path: str
    options: ConvertOptions
    tmp_dir: str
    # Paths set during pipeline execution
    raw_path: str = ""
    rpu_path: str = ""
    clean_path: str = ""
    encoded_path: str = ""
    audio_subs_path: str = ""
    # Metadata resolved during pipeline
    meta: HDR10Metadata = field(
        default_factory=lambda: HDR10Metadata(master_display="", max_cll=0, max_fall=0)
    )
    # Optional encoder (re-encode pipeline only)
    encoder: Encoder | None = None
    dv_profile5: bool = False
    # Internal state for encode step (used by _step_encode)
    _encode_cmd: list[str] = field(default_factory=list)
    _encode_duration: float | None = None
    # State management
    conversion_state: ConversionState | None = None
    skip_completed_steps: bool = False

    @property
    def sample_label(self) -> str:
        return f" (sample: {self.options.sample_seconds}s)" if self.options.sample_seconds else ""


# Step function type: takes context, does work (only called when not dry_run)
StepFn = Callable[[PipelineContext], None]


# ---------------------------------------------------------------------------
# Pipeline runner — eliminates duplicated scaffolding
# ---------------------------------------------------------------------------


def _run_pipeline(
    steps: Sequence[tuple[str, str, StepFn | None]],
    progress: ProgressReporter,
    ctx: PipelineContext,
) -> None:
    """Execute a sequence of pipeline steps with progress, dry-run, and cleanup.

    Each step is (step_name, extra_label, step_fn).
    step_fn is called only when not dry_run. If step_fn is None, the step
    is marked complete immediately (used for no-op steps like skip_rpu).
    """
    completed_successfully = False

    try:
        for step_name, extra, step_fn in steps:
            # Check if we should skip this step (resume mode)
            if ctx.skip_completed_steps and is_step_completed(ctx.conversion_state, step_name):
                # Mark as complete in progress without running
                progress.begin_step(step_name, extra)
                progress.complete_step()

                # Update state to ensure consistency
                if ctx.conversion_state:
                    update_state_progress(ctx.conversion_state, step_name, completed=True)
                    save_state(ctx.conversion_state, ctx.options.temp_dir)
                continue

            progress.begin_step(step_name, extra)

            if step_fn and not ctx.options.dry_run:
                step_fn(ctx)

            progress.complete_step()

            # Update and save state after successful step completion
            if ctx.conversion_state and not ctx.options.dry_run:
                update_state_progress(ctx.conversion_state, step_name, completed=True)
                save_state(ctx.conversion_state, ctx.options.temp_dir)

        output_size = Path(ctx.output_path).stat().st_size if not ctx.options.dry_run else 0
        progress.finish(f"Done! Output: {ctx.output_path} ({format_bytes(output_size)})")
        completed_successfully = True

    except BaseException:
        # Don't clean up temp files on error - preserve for resume
        raise
    finally:
        # Clean up temp files and state on success only
        if completed_successfully and not ctx.options.dry_run:
            _cleanup_temp(ctx.tmp_dir)
            if ctx.conversion_state:
                delete_state(ctx.conversion_state.input_path, ctx.options.temp_dir)


# ---------------------------------------------------------------------------
# Step functions — each does one thing
# ---------------------------------------------------------------------------


def _step_extract_video(ctx: PipelineContext) -> None:
    """Extract raw video bitstream from input MKV."""
    extract_cmd = ["-i", ctx.info.path]
    if ctx.options.sample_seconds:
        extract_cmd += ["-t", str(ctx.options.sample_seconds)]
    extract_cmd += ["-map", "0:v:0", "-c:v", "copy"]
    extract_cmd += ctx.input_codec.extraction_args(ctx.raw_path)
    run_ffmpeg(extract_cmd)

    if ctx.options.sample_seconds:
        _extract_audio_subs(
            ctx.info.path,
            ctx.options.sample_seconds,
            ctx.audio_subs_path,
            ctx.info,
            ctx.options.track_selection,
        )


def _step_extract_video_if_dovi(ctx: PipelineContext) -> None:
    """Extract video for RPU only if codec supports dovi_tool; always extract audio in sample mode."""
    if ctx.input_codec.supports_dovi_tool:
        extract_cmd = ["-i", ctx.info.path]
        if ctx.options.sample_seconds:
            extract_cmd += ["-t", str(ctx.options.sample_seconds)]
        extract_cmd += ["-map", "0:v:0", "-c:v", "copy"]
        extract_cmd += ctx.input_codec.extraction_args(ctx.raw_path)
        run_ffmpeg(extract_cmd)

    if ctx.options.sample_seconds:
        _extract_audio_subs(
            ctx.info.path,
            ctx.options.sample_seconds,
            ctx.audio_subs_path,
            ctx.info,
            ctx.options.track_selection,
        )


def _step_extract_rpu(ctx: PipelineContext) -> None:
    extract_rpu(ctx.raw_path, ctx.rpu_path)


def _step_extract_rpu_if_dovi(ctx: PipelineContext) -> None:
    if ctx.input_codec.supports_dovi_tool:
        extract_rpu(ctx.raw_path, ctx.rpu_path)


def _step_parse_meta_rpu(ctx: PipelineContext) -> None:
    ctx.meta = _parse_meta_with_fallback(ctx.rpu_path, ctx.info)

    # Save metadata to state
    if ctx.conversion_state:
        update_state_metadata(ctx.conversion_state, ctx.meta)
        save_state(ctx.conversion_state, ctx.options.temp_dir)


def _step_parse_meta_auto(ctx: PipelineContext) -> None:
    if ctx.input_codec.supports_dovi_tool:
        ctx.meta = _parse_meta_with_fallback(ctx.rpu_path, ctx.info)
    else:
        ctx.meta = _build_meta_from_probe(ctx.info)

    # Save metadata to state
    if ctx.conversion_state:
        update_state_metadata(ctx.conversion_state, ctx.meta)
        save_state(ctx.conversion_state, ctx.options.temp_dir)


def _step_strip_rpu(ctx: PipelineContext) -> None:
    run_dovi_tool(["remove", ctx.raw_path, "-o", ctx.clean_path])


def _step_encode(ctx: PipelineContext) -> None:
    assert ctx.encoder is not None
    encode_duration: float | None = ctx.options.sample_seconds or ctx.info.duration
    ffmpeg_cmd = _build_encode_cmd(
        ctx.info.path,
        ctx.encoded_path,
        ctx.encoder,
        ctx.meta,
        ctx.options,
        video_only=True,
        source_bitrate=ctx.info.video_streams[0].bitrate if ctx.info.video_streams else None,
        dv_profile5=ctx.dv_profile5,
    )
    # Progress reporter is accessed via the closure in the pipeline builder
    # For the encode step, we need access to progress — pass via a wrapper
    ctx._encode_cmd = ffmpeg_cmd
    ctx._encode_duration = encode_duration


def _step_remux_lossless(ctx: PipelineContext) -> None:
    _remux(ctx.output_path, ctx.clean_path, ctx.meta, ctx.info, ctx.options, ctx.audio_subs_path)


def _step_remux_encoded(ctx: PipelineContext) -> None:
    _remux(ctx.output_path, ctx.encoded_path, ctx.meta, ctx.info, ctx.options, ctx.audio_subs_path)


def _step_cleanup(ctx: PipelineContext) -> None:
    # Cleanup is handled in _run_pipeline finally block
    pass


# ---------------------------------------------------------------------------
# Encoder resolution
# ---------------------------------------------------------------------------


def _resolve_encoder(options: ConvertOptions, input_codec: InputCodec) -> str:
    if options.encoder != "auto":
        return options.encoder
    for name in input_codec.auto_encoder_priority():
        if check_encoder_available(name):
            return name
    return input_codec.auto_encoder_priority()[-1]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def convert(input_path: str, output_path: str, options: ConvertOptions) -> None:
    """Main conversion entry point."""
    # Check for existing state and handle resume logic
    existing_state = None
    if options.resume:
        from de_dolby.state import load_state

        existing_state = load_state(input_path, options.temp_dir)

        if existing_state:
            is_valid, error_msg = validate_state_for_resume(existing_state)
            if not is_valid:
                raise RuntimeError(f"Cannot resume: {error_msg}")

            # Print resume summary
            print(f"\n  {get_resume_summary(existing_state)}\n", file=sys.stderr)

    info = probe(input_path)

    if not info.video_streams:
        raise RuntimeError("No video streams found in input file")

    codec_name = info.video_streams[0].codec_name
    input_codec = get_input_codec(codec_name)

    if info.dv_profile is None:
        raise RuntimeError("No Dolby Vision metadata detected in input file")

    if not options.force and Path(output_path).exists():
        raise RuntimeError(f"Output file already exists: {output_path} (use --force to overwrite)")

    encoder_name = _resolve_encoder(options, input_codec)
    encoder = get_encoder(encoder_name)
    use_lossless = (
        input_codec.supports_lossless and encoder_name == "copy" and info.dv_profile in (7, 8, 10)
    )

    if not input_codec.supports_lossless and encoder_name == "copy":
        encoder_name = _resolve_encoder(
            ConvertOptions(encoder="auto", quality=options.quality), input_codec
        )
        encoder = get_encoder(encoder_name)

    if use_lossless:
        mode_str = "Lossless RPU strip (no re-encode)"
    else:
        mode_str = f"Re-encode to {encoder.codec_family.upper()} (Profile {info.dv_profile})"

    display_banner(info, output_path, encoder_name, mode_str, sample_seconds=options.sample_seconds)
    set_verbose(options.verbose)

    if not options.dry_run:
        _check_disk_space(info, options)

    if use_lossless:
        _run_lossless(info, input_codec, output_path, options, existing_state)
    else:
        _run_reencode(
            info,
            input_codec,
            encoder,
            output_path,
            options,
            dv_profile5=(info.dv_profile == 5),
            existing_state=existing_state,
        )

    # Validate output after successful conversion (unless skipped)
    if not options.skip_validation and not options.dry_run:
        print("\n  Validating output...", file=sys.stderr)
        result = validate_output(input_path, output_path)
        print_validation_result(result, verbose=options.verbose)


# ---------------------------------------------------------------------------
# Pipeline definitions — each builds a step list and runs it
# ---------------------------------------------------------------------------


def _run_lossless(
    info: FileInfo,
    input_codec: InputCodec,
    output_path: str,
    options: ConvertOptions,
    existing_state: ConversionState | None = None,
) -> None:
    tmp_dir = tempfile.mkdtemp(prefix="de_dolby_", dir=options.temp_dir)

    # Set up temp paths
    raw_path = os.path.join(tmp_dir, f"video{input_codec.raw_extension}")
    rpu_path = os.path.join(tmp_dir, "rpu.bin")
    clean_path = os.path.join(tmp_dir, f"clean{input_codec.raw_extension}")
    audio_subs_path = os.path.join(tmp_dir, "audio_subs.mkv")

    # Restore temp paths from state if resuming
    if existing_state and existing_state.temp_paths:
        raw_path = existing_state.temp_paths.get("raw_path", raw_path)
        rpu_path = existing_state.temp_paths.get("rpu_path", rpu_path)
        clean_path = existing_state.temp_paths.get("clean_path", clean_path)
        audio_subs_path = existing_state.temp_paths.get("audio_subs_path", audio_subs_path)

    ctx = PipelineContext(
        info=info,
        input_codec=input_codec,
        output_path=output_path,
        options=options,
        tmp_dir=tmp_dir,
        raw_path=raw_path,
        rpu_path=rpu_path,
        clean_path=clean_path,
        audio_subs_path=audio_subs_path,
        conversion_state=existing_state,
        skip_completed_steps=options.resume and existing_state is not None,
    )

    # Create state if not resuming
    if not existing_state and not options.dry_run:
        temp_paths = {
            "raw_path": raw_path,
            "rpu_path": rpu_path,
            "clean_path": clean_path,
            "audio_subs_path": audio_subs_path,
        }
        ctx.conversion_state = create_initial_state(info.path, output_path, options, temp_paths)
        save_state(ctx.conversion_state, options.temp_dir)

    sample_label = ctx.sample_label
    size_label = f"({_format_size(info)}){sample_label}"

    steps = [
        ("probe", "", None),
        ("extract_hevc", size_label, _step_extract_video),
        ("extract_rpu", "", _step_extract_rpu),
        ("parse_meta", "", _step_parse_meta_rpu),
        ("strip_rpu", "", _step_strip_rpu),
        ("remux", "", _step_remux_lossless),
        ("cleanup", "", _step_cleanup),
    ]

    progress = ProgressReporter(STEPS_LOSSLESS, verbose=options.verbose)
    _run_pipeline(steps, progress, ctx)


def _run_reencode(
    info: FileInfo,
    input_codec: InputCodec,
    encoder: Encoder,
    output_path: str,
    options: ConvertOptions,
    dv_profile5: bool = True,
    existing_state: ConversionState | None = None,
) -> None:
    tmp_dir = tempfile.mkdtemp(prefix="de_dolby_", dir=options.temp_dir)

    # Set up temp paths
    raw_path = os.path.join(tmp_dir, f"video{input_codec.raw_extension}")
    rpu_path = os.path.join(tmp_dir, "rpu.bin")
    encoded_path = os.path.join(tmp_dir, f"encoded{encoder.output_extension}")
    audio_subs_path = os.path.join(tmp_dir, "audio_subs.mkv")

    # Restore temp paths from state if resuming
    if existing_state and existing_state.temp_paths:
        raw_path = existing_state.temp_paths.get("raw_path", raw_path)
        rpu_path = existing_state.temp_paths.get("rpu_path", rpu_path)
        encoded_path = existing_state.temp_paths.get("encoded_path", encoded_path)
        audio_subs_path = existing_state.temp_paths.get("audio_subs_path", audio_subs_path)

    ctx = PipelineContext(
        info=info,
        input_codec=input_codec,
        output_path=output_path,
        options=options,
        tmp_dir=tmp_dir,
        raw_path=raw_path,
        rpu_path=rpu_path,
        encoded_path=encoded_path,
        audio_subs_path=audio_subs_path,
        encoder=encoder,
        dv_profile5=dv_profile5,
        conversion_state=existing_state,
        skip_completed_steps=options.resume and existing_state is not None,
    )

    # Create state if not resuming
    if not existing_state and not options.dry_run:
        temp_paths = {
            "raw_path": raw_path,
            "rpu_path": rpu_path,
            "encoded_path": encoded_path,
            "audio_subs_path": audio_subs_path,
        }
        ctx.conversion_state = create_initial_state(info.path, output_path, options, temp_paths)
        save_state(ctx.conversion_state, options.temp_dir)

    sample_label = ctx.sample_label
    size_label = f"({_format_size(info)}){sample_label}"

    # The encode step needs special handling for progress (uses run_ffmpeg_with_progress)
    progress = ProgressReporter(STEPS_REENCODE, verbose=options.verbose)

    def _step_encode_with_progress(ctx: PipelineContext) -> None:
        assert ctx.encoder is not None
        encode_duration = ctx.options.sample_seconds or ctx.info.duration
        ffmpeg_cmd = _build_encode_cmd(
            ctx.info.path,
            ctx.encoded_path,
            ctx.encoder,
            ctx.meta,
            ctx.options,
            video_only=True,
            source_bitrate=ctx.info.video_streams[0].bitrate if ctx.info.video_streams else None,
            dv_profile5=ctx.dv_profile5,
        )
        run_ffmpeg_with_progress(ffmpeg_cmd, encode_duration, progress, verbose=ctx.options.verbose)

    steps = [
        ("probe", "", None),
        ("extract_hevc", size_label, _step_extract_video_if_dovi),
        ("extract_rpu", "", _step_extract_rpu_if_dovi),
        ("parse_meta", "", _step_parse_meta_auto),
        ("strip_rpu", "", None),  # no-op: ffmpeg strips DV during decode
        ("encode", f"using {encoder.ffmpeg_name}{sample_label}", _step_encode_with_progress),
        ("remux", "", _step_remux_encoded),
        ("cleanup", "", _step_cleanup),
    ]

    _run_pipeline(steps, progress, ctx)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def preview_frame(input_path: str, timestamp: str, output_path: str) -> None:
    """Extract a single frame at the given timestamp, tone-mapped to SDR PNG."""
    info = probe(input_path)
    print(f"\n  Extracting frame at {timestamp} from {input_path}")
    print(f"  Output: {output_path}")

    if info.dv_profile == 5:
        vf = _libplacebo_tonemap_filter()
    else:
        vf = (
            "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
        )

    run_ffmpeg(
        [
            "-ss",
            timestamp,
            "-i",
            input_path,
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            output_path,
        ]
    )

    print(f"  Done! Check {output_path} to verify colors look correct.\n")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_encode_cmd(
    input_path: str,
    output_path: str,
    encoder: Encoder,
    meta: HDR10Metadata,
    options: ConvertOptions,
    video_only: bool = False,
    source_bitrate: int | None = None,
    dv_profile5: bool = False,
) -> list[str]:
    """Build the ffmpeg re-encode command using the encoder strategy."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-hwaccel", "auto"]
    cmd += ["-i", input_path]
    if options.sample_seconds:
        cmd += ["-t", str(options.sample_seconds)]
    cmd += ["-map", "0:v:0"]

    if dv_profile5:
        cmd += ["-vf", _libplacebo_dv_filter()]

    cmd += encoder.build_args(
        meta,
        options.quality,
        crf=options.crf,
        bitrate=options.bitrate,
        source_bitrate=source_bitrate,
    )

    if video_only:
        cmd += ["-an", "-sn", "-f", encoder.output_format]
    else:
        cmd += ["-c:a", "copy", "-c:s", "copy", "-max_muxing_queue_size", "1024"]

    cmd.append(output_path)
    return cmd


def _extract_audio_subs(
    input_path: str,
    sample_seconds: int,
    output_path: str,
    info: FileInfo,
    selection: TrackSelection,
) -> None:
    """Extract audio and subtitle tracks for sample mode, with track selection."""
    cmd = ["-i", input_path, "-t", str(sample_seconds), "-vn"]

    # Add track maps based on selection
    cmd.extend(build_ffmpeg_audio_maps(info, selection))
    cmd.extend(build_ffmpeg_subtitle_maps(info, selection))

    # Add codec copy options
    if not selection.no_audio:
        cmd.extend(["-c:a", "copy"])
    if not selection.no_subtitles:
        cmd.extend(["-c:s", "copy"])

    cmd.append(output_path)
    run_ffmpeg(cmd)


def _remux(
    output_path: str,
    video_path: str,
    meta: HDR10Metadata,
    info: FileInfo,
    options: ConvertOptions,
    audio_subs_path: str,
) -> None:
    cmd = ["-o", output_path]
    cmd += meta.mkvmerge_args(track_id=0)
    cmd.append(video_path)
    cmd += ["-D"]

    # Add track selection arguments
    cmd += build_mkvmerge_track_args(info, options.track_selection)

    if options.sample_seconds:
        cmd.append(audio_subs_path)
    else:
        cmd.append(info.path)
    run_mkvmerge(cmd)


def _parse_meta_with_fallback(rpu_path: str, info: FileInfo) -> HDR10Metadata:
    meta = parse_rpu_metadata(rpu_path)
    if meta.master_display == DEFAULT_MASTER_DISPLAY and info.master_display:
        meta = HDR10Metadata(
            master_display=info.master_display,
            max_cll=meta.max_cll,
            max_fall=meta.max_fall,
        )
    if info.content_light_level:
        parts = info.content_light_level.split(",")
        if len(parts) == 2:
            probe_cll, probe_fall = int(parts[0]), int(parts[1])
            if meta.max_cll == DEFAULT_MAX_CLL and probe_cll:
                meta = HDR10Metadata(
                    master_display=meta.master_display,
                    max_cll=probe_cll,
                    max_fall=probe_fall,
                )
    return meta


def _build_meta_from_probe(info: FileInfo) -> HDR10Metadata:
    master_display = info.master_display or DEFAULT_MASTER_DISPLAY
    max_cll = DEFAULT_MAX_CLL
    max_fall = DEFAULT_MAX_FALL
    if info.content_light_level:
        parts = info.content_light_level.split(",")
        if len(parts) == 2:
            max_cll = int(parts[0]) or DEFAULT_MAX_CLL
            max_fall = int(parts[1]) or DEFAULT_MAX_FALL
    return HDR10Metadata(master_display=master_display, max_cll=max_cll, max_fall=max_fall)


def _libplacebo_dv_filter() -> str:
    return (
        "libplacebo=colorspace=bt2020nc:color_primaries=bt2020:"
        "color_trc=smpte2084:tonemapping=clip:peak_detect=false:format=p010le"
    )


def _libplacebo_tonemap_filter() -> str:
    return (
        "libplacebo=colorspace=bt709:color_primaries=bt709:"
        "color_trc=bt709:tonemapping=hable:peak_detect=true:format=yuv420p"
    )


def _check_disk_space(info: FileInfo, options: ConvertOptions) -> None:
    if not info.overall_bitrate or not info.duration:
        return
    duration = options.sample_seconds or info.duration
    source_bytes = (info.overall_bitrate * duration) / 8
    estimated_bytes = int(source_bytes * 3)
    temp_dir = options.temp_dir or tempfile.gettempdir()
    try:
        usage = shutil.disk_usage(temp_dir)
    except OSError:
        return
    if usage.free < estimated_bytes:
        print(
            f"Warning: temp directory may not have enough space "
            f"(free: {format_bytes(usage.free)}, estimated need: {format_bytes(estimated_bytes)})",
            file=sys.stderr,
        )
        print("  Use --temp-dir to specify a directory with more space.", file=sys.stderr)


def _cleanup_temp(tmp_dir: str) -> None:
    with contextlib.suppress(Exception):
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _format_size(info: FileInfo) -> str:
    if info.overall_bitrate and info.duration:
        size_bytes = (info.overall_bitrate * info.duration) / 8
        return format_bytes(int(size_bytes))
    return "unknown size"
