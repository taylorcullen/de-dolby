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
_verbose = False
_log_file = None


def set_verbose(enabled: bool) -> None:
    """Enable or disable verbose command logging for all subprocess calls."""
    global _verbose
    _verbose = enabled


def configure(
    *, ffmpeg: str | None = None, dovi_tool: str | None = None, mkvmerge: str | None = None
) -> None:
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


def configure_log_file(path: str | None) -> None:
    """Open a log file for writing all subprocess commands and output."""
    import atexit

    global _log_file
    if _log_file:
        _log_file.close()
    if path:
        _log_file = open(path, "w")  # noqa: SIM115 - file must stay open for logging
        atexit.register(lambda: _log_file.close() if _log_file else None)
    else:
        _log_file = None


def _log(msg: str) -> None:
    """Write a line to the log file if configured."""
    if _log_file:
        _log_file.write(msg + "\n")
        _log_file.flush()


def _run(
    cmd: list[str],
    *,
    capture: bool = True,
    check: bool = True,
    stdin_data: bytes | None = None,
    pipe_stdin: bool = False,
) -> subprocess.CompletedProcess:
    if _verbose:
        print(f"  [cmd] {' '.join(cmd)}", file=sys.stderr)
    _log(f"$ {' '.join(cmd)}")
    stdin = subprocess.PIPE if (stdin_data is not None or pipe_stdin) else None
    stdout = subprocess.PIPE if capture else None
    stderr = subprocess.PIPE
    try:
        result = subprocess.run(
            cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            input=stdin_data,
            check=False,
            timeout=_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _log(f"TIMEOUT after {_timeout_seconds}s")
        raise RuntimeError(
            f"Command timed out after {_timeout_seconds}s: {' '.join(cmd)}"
        ) from None
    _log(f"exit={result.returncode}")
    if result.stderr:
        stderr_text = result.stderr.decode(errors="replace").strip()
        if stderr_text:
            _log(stderr_text)
    if check and result.returncode != 0:
        err = result.stderr.decode(errors="replace") if result.stderr else ""
        out = result.stdout.decode(errors="replace") if result.stdout else ""
        detail = err or out
        raise RuntimeError(f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n{detail}")
    return result


def check_tools() -> dict[str, bool]:
    """Verify which required tools are available. Returns {name: available}."""
    status = {}
    for name, path in [
        ("ffmpeg", _paths.ffmpeg),
        ("ffprobe", _paths.ffprobe),
        ("dovi_tool", _paths.dovi_tool),
        ("mkvmerge", _paths.mkvmerge),
    ]:
        status[name] = shutil.which(path) is not None
    return status


_encoder_cache: dict[str, bool] = {}


def check_encoder_available(name: str) -> bool:
    """Check if ffmpeg supports a given encoder. Results are cached.

    Matches on whole-word encoder names to avoid false positives
    (e.g. 'hevc_amf' should not match 'hevc_amf_v2').
    """
    if name in _encoder_cache:
        return _encoder_cache[name]
    try:
        r = _run([_paths.ffmpeg, "-encoders", "-hide_banner"], check=False)
        output = r.stdout.decode(errors="replace") if r.stdout else ""
        # Each line is like: " V..... hevc_amf         AMD AMF HEVC encoder"
        # Match the encoder name as a whitespace-delimited token
        import re

        available = bool(re.search(rf"\b{re.escape(name)}\b", output))
    except FileNotFoundError:
        available = False
    _encoder_cache[name] = available
    return available


# Backwards-compatible aliases
def check_amf_support() -> bool:
    return check_encoder_available("hevc_amf")


def check_av1_amf_support() -> bool:
    return check_encoder_available("av1_amf")


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
