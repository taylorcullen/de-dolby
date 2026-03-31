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

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON output."""
        result: dict = {
            "index": self.index,
            "codec": self.codec_name,
        }
        if self.codec_type == "video":
            if self.width is not None:
                result["width"] = self.width
            if self.height is not None:
                result["height"] = self.height
            if self.pix_fmt is not None:
                result["pix_fmt"] = self.pix_fmt
            if self.bit_depth is not None:
                result["bit_depth"] = self.bit_depth
            if self.color_transfer is not None:
                result["color_transfer"] = self.color_transfer
            if self.color_primaries is not None:
                result["color_primaries"] = self.color_primaries
            if self.color_space is not None:
                result["color_space"] = self.color_space
            if self.frame_rate is not None:
                result["frame_rate"] = self.frame_rate
            if self.bitrate is not None:
                result["bitrate_kbps"] = self.bitrate // 1000
        elif self.codec_type == "audio":
            if self.language is not None:
                result["language"] = self.language
            if self.title is not None:
                result["title"] = self.title
            result["default"] = self.default
            if self.bitrate is not None:
                result["bitrate_kbps"] = self.bitrate // 1000
        elif self.codec_type == "subtitle":
            if self.language is not None:
                result["language"] = self.language
            if self.title is not None:
                result["title"] = self.title
            result["default"] = self.default
        return result


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

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON output."""
        from pathlib import Path

        result: dict = {"file": self.path}

        if self.duration is not None:
            result["duration_seconds"] = round(self.duration, 1)
            # Format as H:MM:SS
            total_secs = int(self.duration)
            hours = total_secs // 3600
            mins = (total_secs % 3600) // 60
            secs = total_secs % 60
            result["duration_formatted"] = f"{hours}:{mins:02d}:{secs:02d}"

        if self.overall_bitrate is not None:
            result["bitrate_kbps"] = self.overall_bitrate // 1000

        # File size
        try:
            size = Path(self.path).stat().st_size
            result["size_bytes"] = size
        except OSError:
            pass

        # Dolby Vision info
        if self.dv_profile is not None:
            result["dolby_vision"] = {
                "profile": self.dv_profile,
            }
            if self.dv_bl_signal_compatibility_id is not None:
                result["dolby_vision"]["bl_signal_compatibility_id"] = (
                    self.dv_bl_signal_compatibility_id
                )

        # HDR10 info
        hdr10_info: dict = {"detected": self.has_hdr10}
        if self.master_display is not None:
            hdr10_info["master_display"] = self.master_display
        if self.content_light_level is not None:
            parts = self.content_light_level.split(",")
            if len(parts) == 2:
                hdr10_info["max_cll"] = int(parts[0])
                hdr10_info["max_fall"] = int(parts[1])
        result["hdr10"] = hdr10_info

        # Streams
        result["video"] = [s.to_dict() for s in self.video_streams]
        result["audio"] = [s.to_dict() for s in self.audio_streams]
        result["subtitles"] = [s.to_dict() for s in self.subtitle_streams]

        return result


def probe(path: str) -> FileInfo:
    """Analyze an MKV file and return structured info about its streams and DV profile."""
    r = run_ffprobe(
        [
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_frames",
            "-read_intervals",
            "%+#1",  # read 1 frame for side data
            path,
        ]
    )
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

            _extract_side_data(s.get("side_data_list", []), info)

            info.video_streams.append(si)
        elif codec_type == "audio":
            si.bitrate = int(s["bit_rate"]) if "bit_rate" in s else None
            info.audio_streams.append(si)
        elif codec_type == "subtitle":
            info.subtitle_streams.append(si)

    # Also check frames for side data (more reliable for some files)
    for frame in data.get("frames", []):
        _extract_side_data(frame.get("side_data_list", []), info, overwrite=False)

    return info


def _extract_side_data(sd_list: list[dict], info: FileInfo, overwrite: bool = True) -> None:
    """Process a list of side data entries, populating DV and HDR10 fields on info.

    When overwrite=False, only fills in fields that are still None (used for
    frame-level fallback after stream-level has been processed).
    """
    for sd in sd_list:
        sd_type = sd.get("side_data_type")

        if sd_type == "DOVI configuration record":
            if overwrite or info.dv_profile is None:
                info.dv_profile = sd.get("dv_profile")
                info.dv_bl_signal_compatibility_id = sd.get("dv_bl_signal_compatibility_id")

        elif sd_type == "Mastering display metadata":
            if overwrite or info.master_display is None:
                info.master_display = _parse_ffprobe_master_display(sd)

        elif sd_type == "Content light level metadata" and (
            overwrite or info.content_light_level is None
        ):
            max_cll = sd.get("max_content", 0)
            max_fall = sd.get("max_average", 0)
            if max_cll or max_fall:
                info.content_light_level = f"{max_cll},{max_fall}"


def _parse_rational(val: str) -> float:
    """Parse a rational string like '34000/50000' to a float (0.68)."""
    val_str = str(val)
    if "/" in val_str:
        num, den = val_str.split("/", 1)
        return int(num) / int(den) if int(den) != 0 else 0.0
    return float(val_str)


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

    lines.append(
        f"Dolby Vision: Profile {info.dv_profile}"
        if info.dv_profile
        else "Dolby Vision: not detected"
    )
    if info.dv_bl_signal_compatibility_id is not None:
        lines.append(f"  BL compatibility ID: {info.dv_bl_signal_compatibility_id}")
    lines.append(f"HDR10 base layer: {'yes' if info.has_hdr10 else 'no'}")

    for vs in info.video_streams:
        lines.append(
            f"\nVideo #{vs.index}: {vs.codec_name} {vs.width}x{vs.height} "
            f"{vs.pix_fmt or ''} {vs.frame_rate or ''}"
        )
        if vs.color_transfer:
            lines.append(
                f"  Transfer: {vs.color_transfer}  Primaries: {vs.color_primaries}  "
                f"Space: {vs.color_space}"
            )
        if vs.bitrate:
            lines.append(f"  Bitrate: {vs.bitrate // 1000} kbps")

    for a in info.audio_streams:
        lang = a.language or "und"
        title = f" ({a.title})" if a.title else ""
        br = f" {a.bitrate // 1000}kbps" if a.bitrate else ""
        lines.append(f"Audio #{a.index}: {a.codec_name} [{lang}]{title}{br}")

    for sub in info.subtitle_streams:
        lang = sub.language or "und"
        title = f" ({sub.title})" if sub.title else ""
        lines.append(f"Subtitle #{sub.index}: {sub.codec_name} [{lang}]{title}")

    return "\n".join(lines)
