"""Conversion pipelines: lossless strip (Profile 7/8) and re-encode (Profile 5/10)."""

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from de_dolby.codecs import Encoder, InputCodec, get_encoder, get_input_codec
from de_dolby.config import DEFAULT_MASTER_DISPLAY, DEFAULT_MAX_CLL, DEFAULT_MAX_FALL
from de_dolby.display import display_banner
from de_dolby.metadata import HDR10Metadata, extract_rpu, parse_rpu_metadata
from de_dolby.probe import FileInfo, probe
from de_dolby.progress import (
    ProgressReporter, STEPS_LOSSLESS, STEPS_REENCODE,
    run_ffmpeg_with_progress,
)
from de_dolby.tools import (
    check_encoder_available, run_dovi_tool, run_ffmpeg, run_mkvmerge, set_verbose,
)


@dataclass
class ConvertOptions:
    encoder: str = "auto"       # auto, hevc_amf, libx265, av1_amf, libsvtav1, copy
    quality: str = "balanced"   # fast, balanced, quality
    crf: int | None = None
    bitrate: str | None = None
    sample_seconds: int | None = None  # convert only first N seconds
    temp_dir: str | None = None  # custom temp directory for intermediate files
    dry_run: bool = False
    verbose: bool = False
    force: bool = False


# ---------------------------------------------------------------------------
# Encoder resolution
# ---------------------------------------------------------------------------

def _resolve_encoder(options: ConvertOptions, input_codec: InputCodec) -> str:
    """Resolve 'auto' encoder to a concrete encoder name.

    Tries each encoder in the codec's priority list, returning the first
    one available in the local ffmpeg build. The last entry is always a
    CPU fallback that doesn't require GPU hardware.
    """
    if options.encoder != "auto":
        return options.encoder
    for name in input_codec.auto_encoder_priority():
        if check_encoder_available(name):
            return name
    # Should never reach here — CPU fallback is always available
    return input_codec.auto_encoder_priority()[-1]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def convert(input_path: str, output_path: str, options: ConvertOptions) -> None:
    """Main conversion entry point."""
    info = probe(input_path)

    if not info.video_streams:
        raise RuntimeError("No video streams found in input file")

    codec_name = info.video_streams[0].codec_name
    input_codec = get_input_codec(codec_name)

    if info.dv_profile is None:
        raise RuntimeError("No Dolby Vision metadata detected in input file")

    if not options.force and Path(output_path).exists():
        raise RuntimeError(f"Output file already exists: {output_path} (use --force to overwrite)")

    # Determine encoder and pipeline mode
    encoder_name = _resolve_encoder(options, input_codec)
    encoder = get_encoder(encoder_name)
    use_lossless = (
        input_codec.supports_lossless
        and encoder_name == "copy"
        and info.dv_profile in (7, 8, 10)
    )

    # Force re-encode if input doesn't support lossless but user asked for copy
    if not input_codec.supports_lossless and encoder_name == "copy":
        encoder_name = _resolve_encoder(
            ConvertOptions(encoder="auto", quality=options.quality), input_codec
        )
        encoder = get_encoder(encoder_name)

    # Build display strings
    if use_lossless:
        mode_str = "Lossless RPU strip (no re-encode)"
    else:
        mode_str = f"Re-encode to {encoder.codec_family.upper()} (Profile {info.dv_profile})"

    display_banner(info, output_path, encoder_name, mode_str,
                   sample_seconds=options.sample_seconds)

    set_verbose(options.verbose)

    if not options.dry_run:
        _check_disk_space(info, options)

    if use_lossless:
        _pipeline_lossless(info, input_codec, output_path, options)
    else:
        dv_profile5 = (info.dv_profile == 5)
        _pipeline_reencode(info, input_codec, encoder, output_path, options,
                           dv_profile5=dv_profile5)


# ---------------------------------------------------------------------------
# Lossless pipeline (HEVC only — strip DV RPU without re-encoding)
# ---------------------------------------------------------------------------

def _pipeline_lossless(info: FileInfo, input_codec: InputCodec,
                       output_path: str, options: ConvertOptions) -> None:
    progress = ProgressReporter(STEPS_LOSSLESS, verbose=options.verbose)
    tmp_dir = tempfile.mkdtemp(prefix="de_dolby_", dir=options.temp_dir)
    raw_path = os.path.join(tmp_dir, f"video{input_codec.raw_extension}")
    rpu_path = os.path.join(tmp_dir, "rpu.bin")
    clean_path = os.path.join(tmp_dir, f"clean{input_codec.raw_extension}")
    audio_subs_path = os.path.join(tmp_dir, "audio_subs.mkv")

    try:
        # Step 1: Probe (already done)
        progress.begin_step("probe")
        progress.complete_step()

        # Step 2: Extract raw video bitstream
        sample_label = f" (sample: {options.sample_seconds}s)" if options.sample_seconds else ""
        progress.begin_step("extract_hevc", f"({_format_size(info)}){sample_label}")
        if not options.dry_run:
            extract_cmd = ["-i", info.path]
            if options.sample_seconds:
                extract_cmd += ["-t", str(options.sample_seconds)]
            extract_cmd += ["-map", "0:v:0", "-c:v", "copy"]
            extract_cmd += input_codec.extraction_args(raw_path)
            run_ffmpeg(extract_cmd)

            if options.sample_seconds:
                _extract_audio_subs(info.path, options.sample_seconds, audio_subs_path)
        progress.complete_step()

        # Step 3: Extract RPU
        progress.begin_step("extract_rpu")
        if not options.dry_run:
            extract_rpu(raw_path, rpu_path)
        progress.complete_step()

        # Step 4: Parse HDR10 metadata from RPU
        progress.begin_step("parse_meta")
        if not options.dry_run:
            meta = _parse_meta_with_fallback(rpu_path, info)
        else:
            meta = HDR10Metadata(master_display="", max_cll=0, max_fall=0)
        progress.complete_step()

        # Step 5: Strip RPU
        progress.begin_step("strip_rpu")
        if not options.dry_run:
            run_dovi_tool(["remove", raw_path, "-o", clean_path])
        progress.complete_step()

        # Step 6: Remux with mkvmerge
        progress.begin_step("remux")
        if not options.dry_run:
            _remux(output_path, clean_path, meta, info, options, audio_subs_path)
        progress.complete_step()

        # Step 7: Cleanup
        progress.begin_step("cleanup")
        _cleanup_temp(tmp_dir)
        progress.complete_step()

        output_size = Path(output_path).stat().st_size if not options.dry_run else 0
        progress.finish(f"Done! Output: {output_path} ({_format_bytes(output_size)})")

    except BaseException:
        _cleanup_temp(tmp_dir)
        raise


# ---------------------------------------------------------------------------
# Re-encode pipeline
# ---------------------------------------------------------------------------

def _pipeline_reencode(info: FileInfo, input_codec: InputCodec, encoder: Encoder,
                       output_path: str, options: ConvertOptions,
                       dv_profile5: bool = True) -> None:
    progress = ProgressReporter(STEPS_REENCODE, verbose=options.verbose)
    tmp_dir = tempfile.mkdtemp(prefix="de_dolby_", dir=options.temp_dir)
    raw_path = os.path.join(tmp_dir, f"video{input_codec.raw_extension}")
    rpu_path = os.path.join(tmp_dir, "rpu.bin")
    encoded_path = os.path.join(tmp_dir, f"encoded{encoder.output_extension}")
    audio_subs_path = os.path.join(tmp_dir, "audio_subs.mkv")

    try:
        # Step 1: Probe (already done)
        progress.begin_step("probe")
        progress.complete_step()

        # Step 2: Extract video for RPU (skip if codec doesn't support dovi_tool)
        sample_label = f" (sample: {options.sample_seconds}s)" if options.sample_seconds else ""
        progress.begin_step("extract_hevc", f"({_format_size(info)}){sample_label}")
        if not options.dry_run:
            if input_codec.supports_dovi_tool:
                extract_cmd = ["-i", info.path]
                if options.sample_seconds:
                    extract_cmd += ["-t", str(options.sample_seconds)]
                extract_cmd += ["-map", "0:v:0", "-c:v", "copy"]
                extract_cmd += input_codec.extraction_args(raw_path)
                run_ffmpeg(extract_cmd)

            if options.sample_seconds:
                _extract_audio_subs(info.path, options.sample_seconds, audio_subs_path)
        progress.complete_step()

        # Step 3: Extract RPU (only if codec supports dovi_tool)
        progress.begin_step("extract_rpu")
        if not options.dry_run and input_codec.supports_dovi_tool:
            extract_rpu(raw_path, rpu_path)
        progress.complete_step()

        # Step 4: Parse HDR10 metadata
        progress.begin_step("parse_meta")
        if not options.dry_run:
            if input_codec.supports_dovi_tool:
                meta = _parse_meta_with_fallback(rpu_path, info)
            else:
                meta = _build_meta_from_probe(info)
        else:
            meta = HDR10Metadata(master_display="", max_cll=0, max_fall=0)
        progress.complete_step()

        # Step 5: (no separate strip — ffmpeg decodes DV during re-encode)
        progress.begin_step("strip_rpu")
        progress.complete_step()

        # Step 6: Re-encode from original MKV
        encode_duration = options.sample_seconds or info.duration
        progress.begin_step("encode", f"using {encoder.ffmpeg_name}{sample_label}")
        if not options.dry_run:
            ffmpeg_cmd = _build_encode_cmd(
                info.path, encoded_path, encoder, meta, options,
                video_only=True,
                source_bitrate=info.video_streams[0].bitrate if info.video_streams else None,
                dv_profile5=dv_profile5,
            )
            run_ffmpeg_with_progress(ffmpeg_cmd, encode_duration, progress)
        progress.complete_step()

        # Step 7: Remux with mkvmerge
        progress.begin_step("remux")
        if not options.dry_run:
            _remux(output_path, encoded_path, meta, info, options, audio_subs_path)
        progress.complete_step()

        # Step 8: Cleanup
        progress.begin_step("cleanup")
        _cleanup_temp(tmp_dir)
        progress.complete_step()

        output_size = Path(output_path).stat().st_size if not options.dry_run else 0
        progress.finish(f"Done! Output: {output_path} ({_format_bytes(output_size)})")

    except BaseException:
        _cleanup_temp(tmp_dir)
        raise


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
        vf = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
              "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")

    run_ffmpeg([
        "-ss", timestamp,
        "-i", input_path,
        "-vf", vf,
        "-frames:v", "1",
        "-pix_fmt", "rgb24",
        output_path,
    ])

    print(f"  Done! Check {output_path} to verify colors look correct.\n")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_encode_cmd(input_path: str, output_path: str, encoder: Encoder,
                       meta: HDR10Metadata, options: ConvertOptions,
                       video_only: bool = False,
                       source_bitrate: int | None = None,
                       dv_profile5: bool = False) -> list[str]:
    """Build the ffmpeg re-encode command using the encoder strategy."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-hwaccel", "auto"]
    cmd += ["-i", input_path]
    if options.sample_seconds:
        cmd += ["-t", str(options.sample_seconds)]
    cmd += ["-map", "0:v:0"]

    if dv_profile5:
        cmd += ["-vf", _libplacebo_dv_filter()]

    cmd += encoder.build_args(
        meta, options.quality,
        crf=options.crf, bitrate=options.bitrate, source_bitrate=source_bitrate,
    )

    if video_only:
        cmd += ["-an", "-sn", "-f", encoder.output_format]
    else:
        cmd += ["-c:a", "copy", "-c:s", "copy", "-max_muxing_queue_size", "1024"]

    cmd.append(output_path)
    return cmd


def _extract_audio_subs(input_path: str, sample_seconds: int, output_path: str) -> None:
    """Extract truncated audio/subtitle streams to match sample duration."""
    run_ffmpeg(["-i", input_path, "-t", str(sample_seconds),
                "-vn", "-c:a", "copy", "-c:s", "copy", output_path])


def _remux(output_path: str, video_path: str, meta: HDR10Metadata,
           info: FileInfo, options: ConvertOptions, audio_subs_path: str) -> None:
    """Remux video + audio/subs into final MKV with HDR10 metadata."""
    cmd = ["-o", output_path]
    cmd += meta.mkvmerge_args(track_id=0)
    cmd.append(video_path)
    cmd += ["-D"]
    if options.sample_seconds:
        cmd.append(audio_subs_path)
    else:
        cmd.append(info.path)
    run_mkvmerge(cmd)


def _parse_meta_with_fallback(rpu_path: str, info: FileInfo) -> HDR10Metadata:
    """Parse HDR10 metadata from RPU, falling back to ffprobe data."""
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
    """Build HDR10 metadata from ffprobe data (used when dovi_tool is unavailable)."""
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
        free_str = _format_bytes(usage.free)
        need_str = _format_bytes(estimated_bytes)
        print(f"Warning: temp directory may not have enough space "
              f"(free: {free_str}, estimated need: {need_str})",
              file=sys.stderr)
        print(f"  Use --temp-dir to specify a directory with more space.",
              file=sys.stderr)


def _cleanup_temp(tmp_dir: str) -> None:
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


def _format_size(info: FileInfo) -> str:
    if info.overall_bitrate and info.duration:
        size_bytes = (info.overall_bitrate * info.duration) / 8
        return _format_bytes(int(size_bytes))
    return "unknown size"


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
