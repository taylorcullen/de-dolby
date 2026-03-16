"""Probe input files for Dolby Vision profile, HDR metadata, and stream info."""

import json
from dataclasses import dataclass, field

from de_dolby.tools import run_ffprobe


@dataclass
class StreamInfo:
    index: int
    codec_type: str  # video, audio, subtitle
    codec_name: str
    language: str | None = None
    title: str | None = None
    default: bool = False
    # Video-specific
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    bit_depth: int | None = None
    frame_rate: str | None = None
    bitrate: int | None = None


@dataclass
class FileInfo:
    path: str
    duration: float | None = None
    overall_bitrate: int | None = None
    dv_profile: int | None = None
    dv_bl_signal_compatibility_id: int | None = None
    has_hdr10: bool = False
    master_display: str | None = None  # ffmpeg format: G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)
    content_light_level: str | None = None  # "MaxCLL,MaxFALL"
    video_streams: list[StreamInfo] = field(default_factory=list)
    audio_streams: list[StreamInfo] = field(default_factory=list)
    subtitle_streams: list[StreamInfo] = field(default_factory=list)


def probe(path: str) -> FileInfo:
    """Analyze an MKV file and return structured info about its streams and DV profile."""
    r = run_ffprobe([
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_frames", "-read_intervals", "%+#1",  # read 1 frame for side data
        path,
    ])
    data = json.loads(r.stdout.decode())

    info = FileInfo(path=path)

    # Format-level info
    fmt = data.get("format", {})
    info.duration = float(fmt["duration"]) if "duration" in fmt else None
    info.overall_bitrate = int(fmt["bit_rate"]) if "bit_rate" in fmt else None

    # Parse streams
    for s in data.get("streams", []):
        codec_type = s.get("codec_type", "")
        tags = s.get("tags", {})
        si = StreamInfo(
            index=s.get("index", 0),
            codec_type=codec_type,
            codec_name=s.get("codec_name", ""),
            language=tags.get("language"),
            title=tags.get("title"),
            default=s.get("disposition", {}).get("default", 0) == 1,
        )
        if codec_type == "video":
            si.width = s.get("width")
            si.height = s.get("height")
            si.pix_fmt = s.get("pix_fmt")
            si.color_transfer = s.get("color_transfer")
            si.color_primaries = s.get("color_primaries")
            si.color_space = s.get("color_space")
            si.frame_rate = s.get("r_frame_rate")
            si.bitrate = int(s["bit_rate"]) if "bit_rate" in s else None
            if si.pix_fmt and "10" in si.pix_fmt:
                si.bit_depth = 10

            # Check for HDR10 via color metadata
            if si.color_transfer == "smpte2084" and si.color_primaries == "bt2020":
                info.has_hdr10 = True

            # Check side data for DV config and HDR10 metadata
            for sd in s.get("side_data_list", []):
                if sd.get("side_data_type") == "DOVI configuration record":
                    info.dv_profile = sd.get("dv_profile")
                    info.dv_bl_signal_compatibility_id = sd.get("dv_bl_signal_compatibility_id")
                elif sd.get("side_data_type") == "Mastering display metadata":
                    info.master_display = _parse_ffprobe_master_display(sd)
                elif sd.get("side_data_type") == "Content light level metadata":
                    max_cll = sd.get("max_content", 0)
                    max_fall = sd.get("max_average", 0)
                    if max_cll or max_fall:
                        info.content_light_level = f"{max_cll},{max_fall}"

            info.video_streams.append(si)
        elif codec_type == "audio":
            si.bitrate = int(s["bit_rate"]) if "bit_rate" in s else None
            info.audio_streams.append(si)
        elif codec_type == "subtitle":
            info.subtitle_streams.append(si)

    # Also check frames for DV side data and HDR10 metadata (more reliable for some files)
    for frame in data.get("frames", []):
        for sd in frame.get("side_data_list", []):
            if sd.get("side_data_type") == "DOVI configuration record" and info.dv_profile is None:
                info.dv_profile = sd.get("dv_profile")
                info.dv_bl_signal_compatibility_id = sd.get("dv_bl_signal_compatibility_id")
            elif sd.get("side_data_type") == "Mastering display metadata" and info.master_display is None:
                info.master_display = _parse_ffprobe_master_display(sd)
            elif sd.get("side_data_type") == "Content light level metadata" and info.content_light_level is None:
                max_cll = sd.get("max_content", 0)
                max_fall = sd.get("max_average", 0)
                if max_cll or max_fall:
                    info.content_light_level = f"{max_cll},{max_fall}"

    return info


def _parse_rational(val: str) -> float:
    """Parse a rational string like '34000/50000' to a float (0.68)."""
    s = str(val)
    if "/" in s:
        num, den = s.split("/", 1)
        return int(num) / int(den) if int(den) != 0 else 0.0
    return float(s)


def _parse_ffprobe_master_display(sd: dict) -> str | None:
    """Parse ffprobe mastering display side data to ffmpeg master_display format.

    ffprobe reports chromaticity and luminance as rationals (e.g. '34000/50000').
    We evaluate the rational to a float, then scale to the integer format expected
    by ffmpeg/x265: chromaticity in 1/50000 units, luminance in 1/10000 cd/m².

    Returns: G(gx,gy)B(bx,by)R(rx,ry)WP(wpx,wpy)L(lmax,lmin)
    """
    try:
        # Chromaticity: evaluate rational → multiply by 50000 → round to int
        gx = round(_parse_rational(sd["green_x"]) * 50000)
        gy = round(_parse_rational(sd["green_y"]) * 50000)
        bx = round(_parse_rational(sd["blue_x"]) * 50000)
        by = round(_parse_rational(sd["blue_y"]) * 50000)
        rx = round(_parse_rational(sd["red_x"]) * 50000)
        ry = round(_parse_rational(sd["red_y"]) * 50000)
        wpx = round(_parse_rational(sd["white_point_x"]) * 50000)
        wpy = round(_parse_rational(sd["white_point_y"]) * 50000)
        # Luminance: evaluate rational → multiply by 10000 → round to int
        lmax = round(_parse_rational(sd["max_luminance"]) * 10000)
        lmin = round(_parse_rational(sd["min_luminance"]) * 10000)
        return f"G({gx},{gy})B({bx},{by})R({rx},{ry})WP({wpx},{wpy})L({lmax},{lmin})"
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def format_info(info: FileInfo) -> str:
    """Format FileInfo as a human-readable string."""
    lines = [f"File: {info.path}"]
    if info.duration:
        m, s = divmod(int(info.duration), 60)
        h, m = divmod(m, 60)
        lines.append(f"Duration: {h}:{m:02d}:{s:02d}")
    if info.overall_bitrate:
        lines.append(f"Bitrate: {info.overall_bitrate // 1000} kbps")

    lines.append(f"Dolby Vision: Profile {info.dv_profile}" if info.dv_profile else "Dolby Vision: not detected")
    if info.dv_bl_signal_compatibility_id is not None:
        lines.append(f"  BL compatibility ID: {info.dv_bl_signal_compatibility_id}")
    lines.append(f"HDR10 base layer: {'yes' if info.has_hdr10 else 'no'}")

    for vs in info.video_streams:
        lines.append(f"\nVideo #{vs.index}: {vs.codec_name} {vs.width}x{vs.height} "
                      f"{vs.pix_fmt or ''} {vs.frame_rate or ''}")
        if vs.color_transfer:
            lines.append(f"  Transfer: {vs.color_transfer}  Primaries: {vs.color_primaries}  "
                          f"Space: {vs.color_space}")
        if vs.bitrate:
            lines.append(f"  Bitrate: {vs.bitrate // 1000} kbps")

    for a in info.audio_streams:
        lang = a.language or "und"
        title = f" ({a.title})" if a.title else ""
        br = f" {a.bitrate // 1000}kbps" if a.bitrate else ""
        lines.append(f"Audio #{a.index}: {a.codec_name} [{lang}]{title}{br}")

    for s in info.subtitle_streams:
        lang = s.language or "und"
        title = f" ({s.title})" if s.title else ""
        lines.append(f"Subtitle #{s.index}: {s.codec_name} [{lang}]{title}")

    return "\n".join(lines)
