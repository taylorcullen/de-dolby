"""Conversion pipelines: lossless strip (Profile 7/8) and re-encode (Profile 5)."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from de_dolby.config import HEVC_AMF_PRESETS, LIBX265_PRESETS
from de_dolby.display import display_banner
from de_dolby.metadata import HDR10Metadata, extract_rpu, parse_rpu_metadata
from de_dolby.probe import FileInfo, probe
from de_dolby.progress import (
    ProgressReporter, STEPS_LOSSLESS, STEPS_REENCODE,
    run_ffmpeg_with_progress,
)
from de_dolby.tools import (
    check_amf_support, run_dovi_tool, run_ffmpeg, run_mkvmerge,
)


@dataclass
class ConvertOptions:
    encoder: str = "auto"       # auto, hevc_amf, libx265, copy
    quality: str = "balanced"   # fast, balanced, quality
    crf: int | None = None
    bitrate: str | None = None
    sample_seconds: int | None = None  # convert only first N seconds
    dry_run: bool = False
    verbose: bool = False
    force: bool = False


def convert(input_path: str, output_path: str, options: ConvertOptions) -> None:
    """Main conversion entry point."""
    info = probe(input_path)

    if not info.video_streams:
        raise RuntimeError("No video streams found in input file")

    if info.video_streams[0].codec_name not in ("hevc", "h265"):
        raise RuntimeError(f"Video codec is {info.video_streams[0].codec_name}, expected HEVC")

    if info.dv_profile is None:
        raise RuntimeError("No Dolby Vision metadata detected in input file")

    if not options.force and Path(output_path).exists():
        raise RuntimeError(f"Output file already exists: {output_path} (use --force to overwrite)")

    # Determine encoder and mode before displaying banner
    if info.dv_profile in (7, 8):
        mode_str = "Lossless RPU strip (no re-encode)"
        encoder_name = "copy"
    elif info.dv_profile == 5:
        mode_str = "Re-encode (Profile 5 color conversion)"
        encoder_name = options.encoder
        if encoder_name == "auto":
            encoder_name = "hevc_amf" if check_amf_support() else "libx265"
    else:
        raise RuntimeError(
            f"Unsupported Dolby Vision profile: {info.dv_profile}. "
            f"Supported profiles: 5, 7, 8"
        )

    display_banner(info, output_path, encoder_name, mode_str,
                   sample_seconds=options.sample_seconds)

    if info.dv_profile in (7, 8):
        _pipeline_lossless(info, output_path, options)
    elif info.dv_profile == 5:
        _pipeline_reencode(info, output_path, options, resolved_encoder=encoder_name)


def _pipeline_lossless(info: FileInfo, output_path: str, options: ConvertOptions) -> None:
    """Profile 7/8: strip DV RPU without re-encoding."""
    progress = ProgressReporter(STEPS_LOSSLESS, verbose=options.verbose)
    tmp_dir = tempfile.mkdtemp(prefix="de_dolby_")
    hevc_path = os.path.join(tmp_dir, "video.hevc")
    rpu_path = os.path.join(tmp_dir, "rpu.bin")
    clean_hevc_path = os.path.join(tmp_dir, "clean.hevc")
    audio_subs_path = os.path.join(tmp_dir, "audio_subs.mkv")

    try:
        # Step 1: Probe (already done)
        progress.begin_step("probe")
        progress.complete_step()

        # Step 2: Extract raw HEVC bitstream
        sample_label = f" (sample: {options.sample_seconds}s)" if options.sample_seconds else ""
        progress.begin_step("extract_hevc", f"({_format_size(info)}){sample_label}")
        if not options.dry_run:
            extract_cmd = ["-i", info.path]
            if options.sample_seconds:
                extract_cmd += ["-t", str(options.sample_seconds)]
            extract_cmd += [
                "-map", "0:v:0",
                "-c:v", "copy",
                "-bsf:v", "hevc_mp4toannexb",
                "-f", "hevc",
                hevc_path,
            ]
            run_ffmpeg(extract_cmd)

            # In sample mode, also extract truncated audio/subs so they
            # match the video duration (the original MKV is full-length)
            if options.sample_seconds:
                as_cmd = ["-i", info.path, "-t", str(options.sample_seconds),
                          "-vn", "-c:a", "copy", "-c:s", "copy", audio_subs_path]
                run_ffmpeg(as_cmd)
        progress.complete_step()

        # Step 3: Extract RPU
        progress.begin_step("extract_rpu")
        if not options.dry_run:
            extract_rpu(hevc_path, rpu_path)
        progress.complete_step()

        # Step 4: Parse HDR10 metadata from RPU
        progress.begin_step("parse_meta")
        if not options.dry_run:
            meta = parse_rpu_metadata(rpu_path)
        else:
            meta = HDR10Metadata(master_display="", max_cll=0, max_fall=0)
        progress.complete_step()

        # Step 5: Strip RPU from HEVC
        progress.begin_step("strip_rpu")
        if not options.dry_run:
            run_dovi_tool(["remove", hevc_path, "-o", clean_hevc_path])
        progress.complete_step()

        # Step 6: Remux with mkvmerge
        progress.begin_step("remux")
        if not options.dry_run:
            mkvmerge_cmd = ["-o", output_path]
            # Add HDR10 metadata flags for track 0
            mkvmerge_cmd += meta.mkvmerge_args(track_id=0)
            # Add clean HEVC video
            mkvmerge_cmd.append(clean_hevc_path)
            # Add audio and subtitle tracks — use truncated file in sample mode
            mkvmerge_cmd += ["-D"]
            if options.sample_seconds:
                mkvmerge_cmd.append(audio_subs_path)
            else:
                mkvmerge_cmd.append(info.path)

            run_mkvmerge(mkvmerge_cmd)
        progress.complete_step()

        # Step 7: Cleanup
        progress.begin_step("cleanup")
        _cleanup_temp(tmp_dir)
        progress.complete_step()

        output_size = Path(output_path).stat().st_size if not options.dry_run else 0
        progress.finish(f"Done! Output: {output_path} ({_format_bytes(output_size)})")

    except Exception:
        _cleanup_temp(tmp_dir)
        raise


def _pipeline_reencode(info: FileInfo, output_path: str, options: ConvertOptions,
                       resolved_encoder: str | None = None) -> None:
    """Profile 5: re-encode with color space conversion."""
    progress = ProgressReporter(STEPS_REENCODE, verbose=options.verbose)
    tmp_dir = tempfile.mkdtemp(prefix="de_dolby_")
    hevc_path = os.path.join(tmp_dir, "video.hevc")
    rpu_path = os.path.join(tmp_dir, "rpu.bin")
    encoded_hevc_path = os.path.join(tmp_dir, "encoded.hevc")
    audio_subs_path = os.path.join(tmp_dir, "audio_subs.mkv")

    # Use pre-resolved encoder from convert(), or resolve now if called directly
    encoder = resolved_encoder or options.encoder
    if encoder == "auto":
        encoder = "hevc_amf" if check_amf_support() else "libx265"

    try:
        # Step 1: Probe (already done)
        progress.begin_step("probe")
        progress.complete_step()

        # Step 2: Extract raw HEVC (for RPU extraction only)
        sample_label = f" (sample: {options.sample_seconds}s)" if options.sample_seconds else ""
        progress.begin_step("extract_hevc", f"({_format_size(info)}){sample_label}")
        if not options.dry_run:
            extract_cmd = ["-i", info.path]
            if options.sample_seconds:
                extract_cmd += ["-t", str(options.sample_seconds)]
            extract_cmd += [
                "-map", "0:v:0",
                "-c:v", "copy",
                "-bsf:v", "hevc_mp4toannexb",
                "-f", "hevc",
                hevc_path,
            ]
            run_ffmpeg(extract_cmd)

            # In sample mode, extract truncated audio/subs to match video duration
            if options.sample_seconds:
                as_cmd = ["-i", info.path, "-t", str(options.sample_seconds),
                          "-vn", "-c:a", "copy", "-c:s", "copy", audio_subs_path]
                run_ffmpeg(as_cmd)
        progress.complete_step()

        # Step 3: Extract RPU
        progress.begin_step("extract_rpu")
        if not options.dry_run:
            extract_rpu(hevc_path, rpu_path)
        progress.complete_step()

        # Step 4: Parse HDR10 metadata
        progress.begin_step("parse_meta")
        if not options.dry_run:
            meta = parse_rpu_metadata(rpu_path)
        else:
            meta = HDR10Metadata(master_display="", max_cll=0, max_fall=0)
        progress.complete_step()

        # Step 5: (no separate strip needed — ffmpeg decodes DV and outputs BT.2020)
        progress.begin_step("strip_rpu")
        progress.complete_step()

        # Step 6: Re-encode from ORIGINAL MKV with DV RPU intact.
        # ffmpeg's HEVC decoder processes the DV RPU during decode, converting
        # Profile 5 IPTPQc2 color space → BT.2020 PQ. The encoder then writes
        # a clean HEVC stream without any DV signaling.
        encode_duration = options.sample_seconds or info.duration
        encode_output = encoded_hevc_path
        progress.begin_step("encode", f"using {encoder}{sample_label}")
        if not options.dry_run:
            ffmpeg_cmd = _build_encode_cmd(
                info.path, encode_output, encoder, meta, options,
                video_only=True,
                source_bitrate=info.video_streams[0].bitrate if info.video_streams else None,
                dv_profile5=True,
            )
            if options.verbose:
                print(f"    cmd: {' '.join(ffmpeg_cmd)}")
            run_ffmpeg_with_progress(ffmpeg_cmd, encode_duration, progress)
        progress.complete_step()

        # Step 7: Remux with mkvmerge — add HDR10 metadata + audio/subs from original
        progress.begin_step("remux")
        if not options.dry_run:
            mkvmerge_cmd = ["-o", output_path]
            mkvmerge_cmd += meta.mkvmerge_args(track_id=0)
            mkvmerge_cmd.append(encoded_hevc_path)
            # Add audio and subtitle tracks — use truncated file in sample mode
            mkvmerge_cmd += ["-D"]
            if options.sample_seconds:
                mkvmerge_cmd.append(audio_subs_path)
            else:
                mkvmerge_cmd.append(info.path)
            run_mkvmerge(mkvmerge_cmd)
        progress.complete_step()

        # Step 8: Cleanup
        progress.begin_step("cleanup")
        _cleanup_temp(tmp_dir)
        progress.complete_step()

        output_size = Path(output_path).stat().st_size if not options.dry_run else 0
        progress.finish(f"Done! Output: {output_path} ({_format_bytes(output_size)})")

    except Exception:
        _cleanup_temp(tmp_dir)
        raise


def preview_frame(input_path: str, timestamp: str, output_path: str) -> None:
    """Extract a single frame at the given timestamp, applying DV→HDR10 color conversion.

    Uses libplacebo to convert DV IPTPQc2 to BT.2020 PQ, then tone-maps to SDR PNG
    so the user can quickly check if colors look correct.
    """
    print(f"\n  Extracting frame at {timestamp} from {input_path}")
    print(f"  Output: {output_path}")

    # Extract one frame using libplacebo for DV color conversion,
    # then tone-map to SDR so it's viewable as a normal PNG
    run_ffmpeg([
        "-ss", timestamp,
        "-i", input_path,
        "-vf", _libplacebo_tonemap_filter(),
        "-frames:v", "1",
        "-pix_fmt", "rgb24",
        output_path,
    ])

    print(f"  Done! Check {output_path} to verify colors look correct.\n")


def _libplacebo_dv_filter() -> str:
    """libplacebo filter for DV Profile 5 → HDR10 (keeps HDR, just fixes color space)."""
    return (
        "libplacebo="
        "colorspace=bt2020nc:"
        "color_primaries=bt2020:"
        "color_trc=smpte2084:"
        "tonemapping=clip:"
        "peak_detect=false:"
        "format=p010le"
    )


def _libplacebo_tonemap_filter() -> str:
    """libplacebo filter for DV → SDR tone-mapped preview (for PNG output)."""
    return (
        "libplacebo="
        "colorspace=bt709:"
        "color_primaries=bt709:"
        "color_trc=bt709:"
        "tonemapping=hable:"
        "peak_detect=true:"
        "format=yuv420p"
    )


def _build_encode_cmd(input_path: str, output_path: str, encoder: str,
                       meta: HDR10Metadata, options: ConvertOptions,
                       video_only: bool = False,
                       source_bitrate: int | None = None,
                       dv_profile5: bool = False) -> list[str]:
    """Build the ffmpeg re-encode command.

    input_path: path to the original MKV (DV intact for color conversion) or HEVC file.
    When video_only=True, outputs raw HEVC with no audio/subs.
    HDR10 metadata will be added later by mkvmerge.
    When dv_profile5=True, uses libplacebo to convert IPTPQc2 → BT.2020 PQ.
    """
    cmd = ["ffmpeg", "-hide_banner", "-y"]

    # Hardware-accelerated HEVC decoding (auto-select best method on Windows/AMD)
    cmd += ["-hwaccel", "auto"]

    cmd += ["-i", input_path]
    if options.sample_seconds:
        cmd += ["-t", str(options.sample_seconds)]
    cmd += ["-map", "0:v:0"]

    # For DV Profile 5: use libplacebo (Vulkan GPU) to convert IPTPQc2 → BT.2020 PQ
    if dv_profile5:
        cmd += ["-vf", _libplacebo_dv_filter()]

    if encoder == "hevc_amf":
        preset_cfg = HEVC_AMF_PRESETS.get(options.quality, HEVC_AMF_PRESETS["balanced"])
        cmd += [
            "-c:v", "hevc_amf",
            "-pix_fmt", "p010le",
            "-quality", preset_cfg["quality"],
            "-rc", preset_cfg["rc"],
            "-profile:v", preset_cfg["profile"],
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]
        # Bitrate
        bitrate = options.bitrate
        if not bitrate and source_bitrate:
            # Use ~80% of source bitrate as target
            target = int(source_bitrate * 0.8)
            bitrate = str(target)
        if bitrate:
            cmd += ["-b:v", bitrate]

    elif encoder == "libx265":
        preset_cfg = LIBX265_PRESETS.get(options.quality, LIBX265_PRESETS["balanced"])
        crf = options.crf if options.crf is not None else preset_cfg["crf"]
        x265_params = (
            f"hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
            f"colormatrix=bt2020nc:master-display={meta.x265_master_display}:"
            f"max-cll={meta.content_light_level}"
        )
        cmd += [
            "-c:v", "libx265",
            "-pix_fmt", "p010le",
            "-preset", preset_cfg["preset"],
            "-crf", str(crf),
            "-x265-params", x265_params,
        ]

    elif encoder == "copy":
        cmd += ["-c:v", "copy"]

    if video_only:
        # Output raw HEVC bitstream (no muxing, no audio/subs)
        cmd += ["-an", "-sn", "-f", "hevc"]
    else:
        # Copy audio and subtitles into final MKV
        cmd += ["-c:a", "copy", "-c:s", "copy",
                "-max_muxing_queue_size", "1024"]

    cmd.append(output_path)
    return cmd


def _cleanup_temp(tmp_dir: str) -> None:
    """Remove temp directory and its contents."""
    import shutil
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


def _format_size(info: FileInfo) -> str:
    """Format file size estimate from bitrate and duration."""
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
