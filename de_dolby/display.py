"""Neofetch-style display banner for file info and conversion status."""

import os
import sys
from pathlib import Path

from de_dolby import __version__
from de_dolby.probe import FileInfo
from de_dolby.utils import Colors as _C


# Logo: DV ═══▶ HDR10
# Each line is a list of (color, text) segments for per-character coloring.
_LOGO_SEGMENTS: list[list[tuple[str, str]]] = [
    [("M", "██████╗ ██╗   ██╗"), ("W", " ██████╗ "), ("G", "██╗  ██╗██████╗ ██████╗  ██╗ ██████╗ ")],
    [("M", "██╔══██╗██║   ██║"), ("W", " ╚════██╗"), ("G", "██║  ██║██╔══██╗██╔══██╗███║██╔═████╗")],
    [("M", "██║  ██║██║   ██║"), ("W", "  █████╔╝"), ("G", "███████║██║  ██║██████╔╝╚██║██║██╔██║")],
    [("M", "██║  ██║╚██╗ ██╔╝"), ("W", " ██╔═══╝ "), ("G", "██╔══██║██║  ██║██╔══██╗ ██║████╔╝██║")],
    [("M", "██████╔╝ ╚████╔╝ "), ("W", " ███████╗"), ("G", "██║  ██║██████╔╝██║  ██║ ██║╚██████╔╝")],
    [("M", "╚═════╝   ╚═══╝  "), ("W", " ╚══════╝"), ("G", "╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝ ╚═╝ ╚═════╝ ")],
]

# Plain-text width of each logo line (all must be equal)
_LOGO_WIDTH = sum(len(seg[1]) for seg in _LOGO_SEGMENTS[0])


def _render_logo_line(segments: list[tuple[str, str]]) -> tuple[str, str]:
    """Render a logo line with ANSI colors. Returns (plain_text, colored_text)."""
    color_map = {"W": _C.WHITE, "R": _C.RED, "M": _C.BRIGHT_MAGENTA, "G": _C.BRIGHT_GREEN}
    plain = "".join(text for _, text in segments)
    colored = "".join(f"{color_map.get(c, '')}{text}{_C.RESET}" for c, text in segments)
    return plain, colored


from de_dolby.utils import format_bytes as _format_bytes, format_duration as _format_duration


def _file_size(path: str) -> str:
    """Get actual file size from disk."""
    try:
        return _format_bytes(Path(path).stat().st_size)
    except OSError:
        return "unknown"


def _video_summary(info: FileInfo) -> str:
    """Build a short video summary string like 'HEVC 3840x2160 10-bit'."""
    if not info.video_streams:
        return "none"
    vs = info.video_streams[0]
    parts = [vs.codec_name.upper()]
    if vs.width and vs.height:
        parts.append(f"{vs.width}x{vs.height}")
    if vs.bit_depth:
        parts.append(f"{vs.bit_depth}-bit")
    return " ".join(parts)


def _stream_summary(streams: list, max_width: int = 42) -> str:
    """Build a compact stream summary that fits within max_width.

    Groups by codec, shows languages. Truncates if too long.
    Example: 'eac3 [eng] | aac [jpn]' or 'subrip: eng, ara, bul +13 more'
    """
    if not streams:
        return "none"

    # Group streams by codec
    by_codec: dict[str, list[str]] = {}
    for s in streams:
        lang = s.language or "und"
        by_codec.setdefault(s.codec_name, []).append(lang)

    # If only one codec, use compact format: "codec: lang1, lang2, ..."
    # If multiple codecs, use: "codec [lang] | codec [lang]"
    if len(by_codec) == 1:
        codec, langs = next(iter(by_codec.items()))
        if len(langs) == 1:
            return f"{codec} [{langs[0]}]"
        # Build incrementally to fit within max_width
        prefix = f"{codec}: "
        remaining = max_width - len(prefix)
        shown = []
        for lang in langs:
            needed = len(", ".join(shown + [lang])) if shown else len(lang)
            overflow_suffix = f" +{len(langs) - len(shown)} more"
            if needed + len(overflow_suffix) > remaining and len(shown) > 0:
                return prefix + ", ".join(shown) + f" +{len(langs) - len(shown)} more"
            shown.append(lang)
        return prefix + ", ".join(shown)
    else:
        # Multiple codecs — show "codec [lang] | codec [lang]", truncate if needed
        items = []
        for codec, langs in by_codec.items():
            for lang in langs:
                items.append(f"{codec} [{lang}]")
        full = " | ".join(items)
        if len(full) <= max_width:
            return full
        # Truncate: show as many as fit, then "+ N more"
        shown = []
        total = len(items)
        for item in items:
            test = " | ".join(shown + [item])
            overflow = f" +{total - len(shown)} more"
            if len(test) + len(overflow) > max_width and shown:
                return " | ".join(shown) + f" +{total - len(shown)} more"
            shown.append(item)
        return " | ".join(shown)


def _visible_len(s: str) -> int:
    """Return the visible length of a string, ignoring ANSI escape codes."""
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def _build_info_rows(info: FileInfo, output_path: str | None = None,
                     encoder_name: str | None = None,
                     mode_str: str | None = None,
                     sample_seconds: int | None = None) -> list[tuple[str, str]]:
    """Build list of (label, value) rows for the banner."""
    rows: list[tuple[str, str]] = []
    rows.append(("File", Path(info.path).name))
    rows.append(("Size", _file_size(info.path)))
    rows.append(("Duration", _format_duration(info.duration)))
    rows.append(("Video", _video_summary(info)))

    dv_str = f"Profile {info.dv_profile}" if info.dv_profile else "not detected"
    if info.dv_bl_signal_compatibility_id is not None:
        dv_str += f" (compat {info.dv_bl_signal_compatibility_id})"
    rows.append(("DV", dv_str))

    if info.has_hdr10:
        rows.append(("HDR10", "yes (base layer)"))

    rows.append(("Audio", _stream_summary(info.audio_streams)))
    subs = _stream_summary(info.subtitle_streams)
    if subs != "none":
        rows.append(("Subs", subs))

    if encoder_name:
        from de_dolby.codecs import ENCODERS
        encoder = ENCODERS.get(encoder_name)
        encoder_label = encoder.display_name if encoder else encoder_name
        rows.append(("Encoder", encoder_label))

    if mode_str:
        rows.append(("Mode", mode_str))

    if sample_seconds:
        rows.append(("Sample", f"first {sample_seconds}s only"))

    if output_path:
        rows.append(("Output", Path(output_path).name))

    return rows


def display_banner(info: FileInfo, output_path: str | None = None,
                   encoder_name: str | None = None,
                   mode_str: str | None = None,
                   sample_seconds: int | None = None,
                   file: object = None) -> None:
    """Print a neofetch-style banner with file info.

    Args:
        info: FileInfo from probe
        output_path: output file path (for convert command)
        encoder_name: resolved encoder name
        mode_str: conversion mode description
        sample_seconds: if in sample mode
        file: output stream (default: sys.stderr)
    """
    out = file or sys.stderr

    rows = _build_info_rows(info, output_path, encoder_name, mode_str, sample_seconds)
    version_line = f"de-dolby v{__version__}"
    label_width = max(len(r[0]) for r in rows)

    # Build plain-text content lines to determine box width.
    # Each plain line is what the user sees (no ANSI codes).
    plain_rows: list[str] = []
    for label, value in rows:
        plain_rows.append(f"  {label + ':':<{label_width + 1}}  {value}")

    max_plain = max(
        _LOGO_WIDTH,
        len(version_line),
        *(len(r) for r in plain_rows),
    )
    # Inner width = content area between the two │ chars (includes 1 space padding each side)
    inner = max_plain + 4  # 2 spaces left + content + 2 spaces right

    # Helpers
    def hline(left: str, fill: str, right: str) -> str:
        return f"  {_C.DIM}{left}{fill * inner}{right}{_C.RESET}"

    def box_line_plain(text: str, text_len: int, colored_text: str | None = None) -> str:
        """Render a box line. text_len is the visible width of text."""
        content = colored_text if colored_text is not None else text
        pad = inner - 2 - text_len  # subtract the 1-space margins we add
        return f"  {_C.DIM}│{_C.RESET} {content}{' ' * pad} {_C.DIM}│{_C.RESET}"

    def centered(text: str, width: int, colored_text: str | None = None) -> str:
        """Center text within width, return box line."""
        pad_left = (width - len(text)) // 2
        pad_right = width - len(text) - pad_left
        display = colored_text if colored_text is not None else text
        full = f"{' ' * pad_left}{display}{' ' * pad_right}"
        return box_line_plain(full, width, full)

    lines: list[str] = [""]
    lines.append(hline("╭", "─", "╮"))

    # Logo (centered, with per-segment coloring)
    content_width = inner - 2  # usable width between the margin spaces
    for segments in _LOGO_SEGMENTS:
        plain, colored = _render_logo_line(segments)
        pad_l = (content_width - len(plain)) // 2
        pad_r = content_width - len(plain) - pad_l
        colored_padded = f"{' ' * pad_l}{colored}{' ' * pad_r}"
        lines.append(box_line_plain("x" * content_width, content_width, colored_padded))

    # Version centered
    pad_l = (content_width - len(version_line)) // 2
    pad_r = content_width - len(version_line) - pad_l
    ver_colored = f"{' ' * pad_l}{_C.DIM}{version_line}{_C.RESET}{' ' * pad_r}"
    lines.append(box_line_plain("x" * content_width, content_width, ver_colored))

    # Separator
    lines.append(hline("├", "─", "┤"))

    # Info rows
    for (label, value), plain in zip(rows, plain_rows):
        colored = f"  {_C.BRIGHT_CYAN}{_C.BOLD}{label + ':':<{label_width + 1}}{_C.RESET}  {value}"
        lines.append(box_line_plain(plain, len(plain), colored))

    lines.append(hline("╰", "─", "╯"))
    lines.append("")

    out.write("\n".join(lines) + "\n")
    out.flush()


def display_info(info: FileInfo) -> None:
    """Display file info in neofetch style (for the 'info' command).

    Uses stdout since this is the primary output of the info command.
    """
    display_banner(info, file=sys.stdout)
