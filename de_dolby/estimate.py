"""Estimation module for previewing conversion without executing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from de_dolby.codecs import ENCODERS, get_encoder, get_input_codec
from de_dolby.config import DEFAULT_MASTER_DISPLAY, DEFAULT_MAX_CLL, DEFAULT_MAX_FALL
from de_dolby.metadata import HDR10Metadata, extract_rpu, parse_rpu_metadata
from de_dolby.probe import FileInfo, probe
from de_dolby.tools import check_encoder_available
from de_dolby.utils import format_bytes, format_duration

if TYPE_CHECKING:
    pass


@dataclass
class ConversionEstimate:
    """Complete conversion estimation data."""

    input_path: str
    input_size: int
    info: FileInfo

    # Pipeline determination
    pipeline_type: str  # "lossless" or "reencode"
    encoder_name: str
    encoder_display: str
    quality: str

    # HDR metadata
    metadata: HDR10Metadata

    # Estimates
    estimated_output_size: int
    estimated_time_minutes: tuple[float, float]  # (min, max)
    temp_space_needed: int

    @property
    def output_path(self) -> str:
        """Derive the output filename from input."""
        p = Path(self.input_path)
        stem_with_ext = p.name
        import re

        replaced = re.sub(r"\.DV\.", ".HDR10.", stem_with_ext, count=1, flags=re.IGNORECASE)
        if replaced != stem_with_ext:
            return str(p.with_name(replaced))
        return str(p.with_suffix("")) + ".HDR10" + p.suffix


def _resolve_encoder_for_estimate(
    info: FileInfo,
    encoder_preference: str,
    quality: str,
) -> tuple[str, str]:
    """Resolve which encoder would be used for conversion.

    Returns (encoder_name, encoder_display_name).
    """
    if not info.video_streams:
        raise RuntimeError("No video streams found in input file")

    codec_name = info.video_streams[0].codec_name
    input_codec = get_input_codec(codec_name)

    # Determine if lossless is possible
    dv_profile = info.dv_profile
    use_lossless = (
        input_codec.supports_lossless
        and encoder_preference in ("auto", "copy")
        and dv_profile in (7, 8, 10)
    )

    if not input_codec.supports_lossless and encoder_preference == "copy":
        use_lossless = False

    if use_lossless:
        return "copy", ENCODERS["copy"].display_name

    # Re-encode path - resolve encoder
    if encoder_preference != "auto" and encoder_preference != "copy":
        encoder_name = encoder_preference
        if not check_encoder_available(encoder_name):
            raise RuntimeError(f"Encoder {encoder_name} not available in ffmpeg")
    else:
        # Auto-detect best available encoder
        for name in input_codec.auto_encoder_priority():
            if check_encoder_available(name):
                encoder_name = name
                break
        else:
            encoder_name = input_codec.auto_encoder_priority()[-1]

    encoder = get_encoder(encoder_name)
    return encoder_name, encoder.display_name


def _extract_metadata_for_estimate(info: FileInfo) -> HDR10Metadata:
    """Extract HDR10 metadata for estimation purposes.

    For lossless pipeline, we need to extract RPU and parse it.
    For re-encode, we can use probe data as fallback.
    """
    codec_name = info.video_streams[0].codec_name if info.video_streams else "hevc"
    input_codec = get_input_codec(codec_name)

    # If file has RPU that dovi_tool can process, try to extract it
    if input_codec.supports_dovi_tool and info.dv_profile in (7, 8, 10):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="de_dolby_est_") as tmp_dir:
            raw_path = os.path.join(tmp_dir, f"video{input_codec.raw_extension}")
            rpu_path = os.path.join(tmp_dir, "rpu.bin")

            # Quick extraction of first few MB to get RPU
            try:
                from de_dolby.tools import run_ffmpeg

                # Extract just first 30 seconds or use smaller sample
                run_ffmpeg(
                    [
                        "-i",
                        info.path,
                        "-t",
                        "5",  # Just 5 seconds for metadata extraction
                        "-map",
                        "0:v:0",
                        "-c:v",
                        "copy",
                    ]
                    + input_codec.extraction_args(raw_path)
                )

                extract_rpu(raw_path, rpu_path)
                return parse_rpu_metadata(rpu_path)
            except Exception:
                # Fall through to probe-based metadata
                pass

    # Build metadata from probe data
    master_display = info.master_display or DEFAULT_MASTER_DISPLAY
    max_cll = DEFAULT_MAX_CLL
    max_fall = DEFAULT_MAX_FALL

    if info.content_light_level:
        parts = info.content_light_level.split(",")
        if len(parts) == 2:
            max_cll = int(parts[0]) or DEFAULT_MAX_CLL
            max_fall = int(parts[1]) or DEFAULT_MAX_FALL

    return HDR10Metadata(
        master_display=master_display,
        max_cll=max_cll,
        max_fall=max_fall,
    )


def _estimate_output_size(
    info: FileInfo,
    pipeline_type: str,
    encoder_name: str,
) -> int:
    """Estimate output file size in bytes."""
    input_size = Path(info.path).stat().st_size if Path(info.path).exists() else 0

    if pipeline_type == "lossless":
        # Lossless pipeline: output is slightly smaller (RPU stripped, ~1-2% reduction)
        # but metadata overhead adds some, so roughly same size
        return int(input_size * 0.98)

    # Re-encode: estimate based on encoder type and typical compression ratios
    # Hardware encoders typically achieve 40-60% size reduction for 4K HDR content
    # Software encoders can achieve 50-70% with good quality settings
    video_stream = info.video_streams[0] if info.video_streams else None
    source_bitrate = video_stream.bitrate if video_stream else None

    if source_bitrate and info.duration:
        # Calculate based on target bitrate for re-encode
        if encoder_name in ("hevc_amf", "hevc_nvenc", "av1_amf", "av1_nvenc"):
            # Hardware encoders: typically 60-75% of source size for same quality
            factor = 0.65
        elif encoder_name in ("libx265", "libsvtav1"):
            # Software encoders: can achieve better compression
            factor = 0.55
        else:
            factor = 0.60

        # Estimate video size
        video_size = (source_bitrate * info.duration) / 8
        estimated_video = int(video_size * factor)

        # Add estimated audio/subtitle size (unchanged, ~10-20% of total)
        non_video_size = max(0, input_size - video_size)
        return estimated_video + int(non_video_size)

    # Fallback: percentage of input size
    if encoder_name in ("libx265", "libsvtav1"):
        return int(input_size * 0.50)  # 50% of input
    return int(input_size * 0.65)  # 65% of input for hardware


def _estimate_processing_time(
    info: FileInfo,
    pipeline_type: str,
    encoder_name: str,
    quality: str,
) -> tuple[float, float]:
    """Estimate processing time in minutes (min, max range).

    Time estimation heuristics:
    - Lossless strip: 2-5 minutes regardless of file size
    - Hardware encode (hevc_amf, hevc_nvenc): ~0.5-1x realtime
    - Software encode (libx265): ~0.05-0.2x realtime (5-20x slower)
    - Estimate formula: duration / speed_factor + overhead
    """
    if pipeline_type == "lossless":
        # Lossless strip is very fast, mostly I/O bound
        return (2.0, 5.0)

    duration_minutes = (info.duration or 0) / 60
    if duration_minutes <= 0:
        return (10.0, 30.0)  # Unknown duration fallback

    # Speed factors (as fraction of realtime)
    if encoder_name == "hevc_amf":
        # AMD AMF: 0.5-1.0x realtime depending on quality
        speed_map = {"fast": 1.0, "balanced": 0.7, "quality": 0.5}
        speed = speed_map.get(quality, 0.7)
    elif encoder_name == "hevc_nvenc":
        # NVENC: slightly faster than AMF
        speed_map = {"fast": 1.2, "balanced": 0.8, "quality": 0.6}
        speed = speed_map.get(quality, 0.8)
    elif encoder_name == "av1_amf":
        # AV1 AMF: slower than HEVC
        speed_map = {"fast": 0.6, "balanced": 0.4, "quality": 0.25}
        speed = speed_map.get(quality, 0.4)
    elif encoder_name == "av1_nvenc":
        speed_map = {"fast": 0.8, "balanced": 0.5, "quality": 0.35}
        speed = speed_map.get(quality, 0.5)
    elif encoder_name == "libx265":
        # CPU encoding is much slower
        speed_map = {"fast": 0.15, "balanced": 0.08, "quality": 0.04}
        speed = speed_map.get(quality, 0.08)
    elif encoder_name == "libsvtav1":
        speed_map = {"fast": 0.12, "balanced": 0.06, "quality": 0.03}
        speed = speed_map.get(quality, 0.06)
    else:
        speed = 0.5  # Default fallback

    # Calculate time with some variance
    base_time = duration_minutes / speed
    min_time = base_time * 0.8  # Could be 20% faster
    max_time = base_time * 1.3  # Could be 30% slower with overhead

    return (min_time, max_time)


def _estimate_temp_space(
    info: FileInfo,
    pipeline_type: str,
) -> int:
    """Estimate temporary space needed in bytes."""
    input_size = Path(info.path).stat().st_size if Path(info.path).exists() else 0

    if pipeline_type == "lossless":
        # Lossless: needs ~2x input size (raw bitstream + clean output)
        return int(input_size * 2.2)

    # Re-encode: needs more space for intermediate files
    # Raw bitstream + encoded output + audio extraction
    return int(input_size * 3.0)


def estimate_conversion(
    input_path: str,
    encoder_preference: str = "auto",
    quality: str = "balanced",
) -> ConversionEstimate:
    """Generate a conversion estimate without actually converting.

    Args:
        input_path: Path to the input MKV file
        encoder_preference: Preferred encoder ("auto", "copy", or specific encoder)
        quality: Quality preset ("fast", "balanced", "quality")

    Returns:
        ConversionEstimate with all estimation data

    Raises:
        RuntimeError: If file cannot be analyzed or encoder not available
    """
    if not Path(input_path).exists():
        raise RuntimeError(f"File not found: {input_path}")

    # Probe the file
    info = probe(input_path)

    if not info.video_streams:
        raise RuntimeError("No video streams found in input file")

    if info.dv_profile is None:
        raise RuntimeError("No Dolby Vision metadata detected in input file")

    # Resolve encoder
    encoder_name, encoder_display = _resolve_encoder_for_estimate(info, encoder_preference, quality)

    # Determine pipeline type
    codec_name = info.video_streams[0].codec_name
    input_codec = get_input_codec(codec_name)
    use_lossless = (
        input_codec.supports_lossless and encoder_name == "copy" and info.dv_profile in (7, 8, 10)
    )
    pipeline_type = "lossless" if use_lossless else "reencode"

    # Extract metadata
    metadata = _extract_metadata_for_estimate(info)

    # Get input size
    input_size = Path(input_path).stat().st_size

    # Calculate estimates
    output_size = _estimate_output_size(info, pipeline_type, encoder_name)
    time_range = _estimate_processing_time(info, pipeline_type, encoder_name, quality)
    temp_space = _estimate_temp_space(info, pipeline_type)

    return ConversionEstimate(
        input_path=input_path,
        input_size=input_size,
        info=info,
        pipeline_type=pipeline_type,
        encoder_name=encoder_name,
        encoder_display=encoder_display,
        quality=quality,
        metadata=metadata,
        estimated_output_size=output_size,
        estimated_time_minutes=time_range,
        temp_space_needed=temp_space,
    )


def format_estimate(estimate: ConversionEstimate) -> str:
    """Format a ConversionEstimate as human-readable text."""
    lines = []

    # Input section
    lines.append(f"Input: {estimate.input_path}")
    lines.append(f"  Size: {format_bytes(estimate.input_size)}")
    if estimate.info.duration:
        lines.append(f"  Duration: {format_duration(estimate.info.duration)}")
    lines.append(f"  Dolby Vision: Profile {estimate.info.dv_profile}")

    # Video info
    if estimate.info.video_streams:
        vs = estimate.info.video_streams[0]
        bit_depth_str = f"{vs.bit_depth}-bit" if vs.bit_depth else ""
        lines.append(f"  Video: {vs.codec_name} {vs.width}x{vs.height} {bit_depth_str}".rstrip())

    lines.append("")

    # Conversion Plan section
    lines.append("Conversion Plan:")
    if estimate.pipeline_type == "lossless":
        lines.append("  Pipeline: Lossless RPU strip (no re-encode)")
        lines.append("  Encoder: copy (base layer already HDR10 compatible)")
    else:
        lines.append(
            f"  Pipeline: Re-encode ({estimate.info.video_streams[0].codec_name.upper()} to HEVC)"
        )
        lines.append(f"  Encoder: {estimate.encoder_display}")
        lines.append(f"  Quality: {estimate.quality}")

    lines.append(f"  Output: {estimate.output_path}")
    lines.append("")

    # Estimates section
    lines.append("Estimates:")

    if estimate.pipeline_type == "lossless":
        lines.append(
            f"  Output size: ~{format_bytes(estimate.estimated_output_size)} (similar to input)"
        )
        lines.append(
            f"  Processing time: ~{int(estimate.estimated_time_minutes[0])}-{int(estimate.estimated_time_minutes[1])} minutes"
        )
    else:
        # Show size as range
        min_size = int(estimate.estimated_output_size * 0.85)
        max_size = int(estimate.estimated_output_size * 1.15)
        lines.append(
            f"  Output size: ~{format_bytes(min_size)} - {format_bytes(max_size)} (compressed)"
        )

        # Time estimate
        min_mins, max_mins = estimate.estimated_time_minutes
        if max_mins < 60:
            lines.append(f"  Processing time: ~{int(min_mins)}-{int(max_mins)} minutes")
        else:
            min_h = int(min_mins / 60)
            min_rem = int(min_mins % 60)
            max_h = int(max_mins / 60)
            max_rem = int(max_mins % 60)
            lines.append(f"  Processing time: ~{min_h}h {min_rem}m - {max_h}h {max_rem}m")

    lines.append(f"  Space needed: ~{format_bytes(estimate.temp_space_needed)} (temp files)")
    lines.append("")

    # HDR10 Metadata section
    lines.append("HDR10 Metadata:")
    lines.append(f"  Mastering display: {estimate.metadata.master_display}")
    lines.append(f"  MaxCLL: {estimate.metadata.max_cll} nits")
    lines.append(f"  MaxFALL: {estimate.metadata.max_fall} nits")

    return "\n".join(lines)


def display_estimate(estimate: ConversionEstimate) -> None:
    """Print a formatted estimate to stdout."""
    print()
    print(format_estimate(estimate))
    print()
