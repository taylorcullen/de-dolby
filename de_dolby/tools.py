"""Subprocess wrappers for external tools: ffmpeg, ffprobe, dovi_tool, mkvmerge."""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolPaths:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    dovi_tool: str = "dovi_tool"
    mkvmerge: str = "mkvmerge"


_paths = ToolPaths()


def configure(*, ffmpeg: str | None = None, dovi_tool: str | None = None,
              mkvmerge: str | None = None) -> None:
    if ffmpeg:
        _paths.ffmpeg = ffmpeg
        # Derive ffprobe from same directory
        p = Path(ffmpeg).parent / ("ffprobe" + Path(ffmpeg).suffix)
        if p.exists():
            _paths.ffprobe = str(p)
    if dovi_tool:
        _paths.dovi_tool = dovi_tool
    if mkvmerge:
        _paths.mkvmerge = mkvmerge


_timeout_seconds: int | None = None


def configure_timeout(minutes: int | None) -> None:
    """Set the global subprocess timeout (in minutes). None means no timeout."""
    global _timeout_seconds
    _timeout_seconds = minutes * 60 if minutes else None


def _run(cmd: list[str], *, capture: bool = True, check: bool = True,
         stdin_data: bytes | None = None, pipe_stdin: bool = False) -> subprocess.CompletedProcess:
    stdin = subprocess.PIPE if (stdin_data is not None or pipe_stdin) else None
    stdout = subprocess.PIPE if capture else None
    stderr = subprocess.PIPE
    try:
        result = subprocess.run(
            cmd, stdin=stdin, stdout=stdout, stderr=stderr,
            input=stdin_data, check=False, timeout=_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Command timed out after {_timeout_seconds}s: {' '.join(cmd)}"
        )
    if check and result.returncode != 0:
        err = result.stderr.decode(errors="replace") if result.stderr else ""
        out = result.stdout.decode(errors="replace") if result.stdout else ""
        detail = err or out
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n{detail}"
        )
    return result


def check_tools() -> dict[str, bool]:
    """Verify which required tools are available. Returns {name: available}."""
    status = {}
    for name, path in [("ffmpeg", _paths.ffmpeg), ("ffprobe", _paths.ffprobe),
                        ("dovi_tool", _paths.dovi_tool), ("mkvmerge", _paths.mkvmerge)]:
        status[name] = shutil.which(path) is not None
    return status


def check_amf_support() -> bool:
    """Check if ffmpeg supports hevc_amf encoder."""
    try:
        r = _run([_paths.ffmpeg, "-encoders", "-hide_banner"], check=False)
        output = r.stdout.decode(errors="replace") if r.stdout else ""
        return "hevc_amf" in output
    except FileNotFoundError:
        return False


def run_ffprobe(args: list[str]) -> subprocess.CompletedProcess:
    return _run([_paths.ffprobe] + args)


def run_ffmpeg(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return _run([_paths.ffmpeg, "-hide_banner", "-y"] + args, capture=capture)


def run_dovi_tool(args: list[str], stdin_data: bytes | None = None) -> subprocess.CompletedProcess:
    return _run([_paths.dovi_tool] + args, stdin_data=stdin_data)


def run_mkvmerge(args: list[str]) -> subprocess.CompletedProcess:
    return _run([_paths.mkvmerge] + args)


def require_tools(need_mkvmerge: bool = True) -> None:
    """Check that all required tools are on PATH, exit with error if not."""
    status = check_tools()
    missing = []
    for name, ok in status.items():
        if not ok:
            if name == "mkvmerge" and not need_mkvmerge:
                continue
            missing.append(name)
    if missing:
        print(f"Error: required tools not found on PATH: {', '.join(missing)}", file=sys.stderr)
        print("\nInstall them from:", file=sys.stderr)
        print("  ffmpeg:    https://www.gyan.dev/ffmpeg/builds/", file=sys.stderr)
        print("  dovi_tool: https://github.com/quietvoid/dovi_tool/releases", file=sys.stderr)
        print("  mkvmerge:  https://mkvtoolnix.download/", file=sys.stderr)
        sys.exit(1)
