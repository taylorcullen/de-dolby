"""Progress reporting for conversion pipeline with progress bars."""

import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


# ANSI color codes
class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    CYAN = "\033[36m"
    BRIGHT_CYAN = "\033[96m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    WHITE = "\033[97m"


# Progress bar characters
_BAR_FILL = "\u2588"   # █
_BAR_EMPTY = "\u2591"  # ░
_BAR_WIDTH = 30
_CHECK = "\u2713"      # ✓


@dataclass
class Step:
    name: str
    description: str


STEPS_REENCODE = [
    Step("probe", "Analyzing input file"),
    Step("extract_hevc", "Extracting HEVC stream"),
    Step("extract_rpu", "Extracting Dolby Vision RPU"),
    Step("parse_meta", "Reading HDR10 metadata from RPU"),
    Step("strip_rpu", "Stripping Dolby Vision RPU from HEVC"),
    Step("encode", "Re-encoding video to HDR10"),
    Step("remux", "Remuxing to MKV with HDR10 metadata"),
    Step("cleanup", "Cleaning up temp files"),
]

STEPS_LOSSLESS = [
    Step("probe", "Analyzing input file"),
    Step("extract_hevc", "Extracting HEVC stream"),
    Step("extract_rpu", "Extracting Dolby Vision RPU"),
    Step("parse_meta", "Reading HDR10 metadata from RPU"),
    Step("strip_rpu", "Stripping Dolby Vision RPU from HEVC"),
    Step("remux", "Remuxing to MKV with HDR10 metadata"),
    Step("cleanup", "Cleaning up temp files"),
]

# Steps that have no measurable progress (instant or non-streaming)
_INSTANT_STEPS = {"probe", "parse_meta", "cleanup"}


class ProgressReporter:
    """Displays step-by-step progress with progress bars."""

    def __init__(self, steps: list[Step], verbose: bool = False):
        self.steps = steps
        self.verbose = verbose
        self.current_step = -1
        self.total_steps = len(steps)
        self._pulse_thread: threading.Thread | None = None
        self._pulse_running = False
        self._pulse_idx = 0
        self._desc_width = max(len(s.description) for s in steps)

    def begin_step(self, step_name: str, extra: str = "") -> None:
        """Mark a step as started."""
        self._stop_pulse()

        # Find step index
        for i, s in enumerate(self.steps):
            if s.name == step_name:
                self.current_step = i
                break
        else:
            self.current_step += 1

        step = self.steps[self.current_step] if self.current_step < len(self.steps) else None
        desc = step.description if step else step_name
        name = step.name if step else step_name

        if name in _INSTANT_STEPS:
            # For instant steps, just show the prefix with "working..."
            self._clear_line()
            self._write_step_prefix(desc)
            sys.stderr.write(f" {_C.DIM}working...{_C.RESET}")
            sys.stderr.flush()
        else:
            # For long-running steps, show an indeterminate pulse animation
            self._clear_line()
            self._write_step_prefix(desc)
            sys.stderr.flush()
            self._start_pulse(desc)

    def update_encoding_progress(self, percent: float | None = None,
                                  fps: float | None = None,
                                  speed: str | None = None,
                                  time_str: str | None = None) -> None:
        """Update encoding progress with a progress bar on the current line."""
        self._stop_pulse()
        step = self.steps[self.current_step] if self.current_step < len(self.steps) else None
        desc = step.description if step else "Encoding"

        self._clear_line()
        self._write_step_prefix(desc)

        # Build bar
        pct = percent if percent is not None else 0.0
        filled = int(_BAR_WIDTH * pct / 100)
        filled = min(filled, _BAR_WIDTH)
        empty = _BAR_WIDTH - filled

        bar = f"{_C.GREEN}{_BAR_FILL * filled}{_C.RESET}{_C.DIM}{_BAR_EMPTY * empty}{_C.RESET}"
        sys.stderr.write(f" [{bar}] {pct:5.1f}%")

        # Extra stats
        parts = []
        if fps is not None and fps > 0:
            parts.append(f"{fps:.1f} fps")
        if speed:
            parts.append(speed)
        if parts:
            sys.stderr.write(f"  {_C.DIM}{'  '.join(parts)}{_C.RESET}")

        sys.stderr.flush()

    def complete_step(self, step_name: str | None = None) -> None:
        """Mark the current step as complete."""
        self._stop_pulse()
        step = self.steps[self.current_step] if self.current_step < len(self.steps) else None
        desc = step.description if step else (step_name or "Step")
        name = step.name if step else (step_name or "")

        self._clear_line()
        self._write_step_prefix(desc)

        if name in _INSTANT_STEPS:
            # Instant steps just get a checkmark
            sys.stderr.write(f" {_C.BRIGHT_GREEN}{_CHECK}{_C.RESET}")
        else:
            # Long steps get a full bar + checkmark
            bar = f"{_C.GREEN}{_BAR_FILL * _BAR_WIDTH}{_C.RESET}"
            sys.stderr.write(f" [{bar}] 100.0%  {_C.BRIGHT_GREEN}{_CHECK}{_C.RESET}")

        sys.stderr.write("\n")
        sys.stderr.flush()

    def finish(self, message: str = "Conversion complete!") -> None:
        self._stop_pulse()
        self._clear_line()
        sys.stderr.write(f"\n  {_C.BRIGHT_GREEN}{_CHECK}{_C.RESET} {_C.BOLD}{message}{_C.RESET}\n\n")
        sys.stderr.flush()

    def error(self, message: str) -> None:
        self._stop_pulse()
        self._clear_line()
        sys.stderr.write(f"\n  {_C.RED}Error:{_C.RESET} {message}\n\n")
        sys.stderr.flush()

    def _write_step_prefix(self, desc: str) -> None:
        """Write the step number and padded description."""
        num = self.current_step + 1
        sys.stderr.write(
            f"  {_C.DIM}[{num}/{self.total_steps}]{_C.RESET} "
            f"{_C.WHITE}{desc:<{self._desc_width}}{_C.RESET}"
        )

    def _clear_line(self) -> None:
        sys.stderr.write("\r\033[K")

    def _start_pulse(self, desc: str) -> None:
        """Start an indeterminate pulse animation for steps without percent progress."""
        self._pulse_running = True
        self._pulse_idx = 0
        self._pulse_thread = threading.Thread(target=self._pulse_loop, args=(desc,), daemon=True)
        self._pulse_thread.start()

    def _stop_pulse(self) -> None:
        self._pulse_running = False
        if self._pulse_thread:
            self._pulse_thread.join(timeout=1)
            self._pulse_thread = None

    def _pulse_loop(self, desc: str) -> None:
        """Animate a bouncing pulse bar while waiting."""
        while self._pulse_running:
            # Create a bouncing highlight effect
            pos = self._pulse_idx % (_BAR_WIDTH * 2)
            if pos >= _BAR_WIDTH:
                pos = _BAR_WIDTH * 2 - pos - 1

            bar_chars = []
            for i in range(_BAR_WIDTH):
                dist = abs(i - pos)
                if dist == 0:
                    bar_chars.append(f"{_C.CYAN}{_BAR_FILL}{_C.RESET}")
                elif dist == 1:
                    bar_chars.append(f"{_C.DIM}{_C.CYAN}{_BAR_FILL}{_C.RESET}")
                else:
                    bar_chars.append(f"{_C.DIM}{_BAR_EMPTY}{_C.RESET}")
            bar = "".join(bar_chars)

            self._clear_line()
            self._write_step_prefix(desc)
            sys.stderr.write(f" [{bar}]")
            sys.stderr.flush()

            self._pulse_idx += 1
            time.sleep(0.06)


def parse_ffmpeg_progress(line: str, duration: float | None) -> dict | None:
    """Parse an ffmpeg stderr progress line and return extracted info.

    Returns dict with keys: time, fps, speed, percent (if duration known).
    """
    if not line.startswith("frame=") and "time=" not in line:
        return None

    info: dict = {}

    # Extract time
    m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
    if m:
        info["time_str"] = m.group(1)
        parts = m.group(1).split(":")
        seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        info["time_seconds"] = seconds
        if duration and duration > 0:
            info["percent"] = min(100.0, (seconds / duration) * 100)

    # Extract fps
    m = re.search(r"fps=\s*([\d.]+)", line)
    if m:
        info["fps"] = float(m.group(1))

    # Extract speed
    m = re.search(r"speed=\s*([\d.]+x)", line)
    if m:
        info["speed"] = m.group(1)

    return info if info else None


def run_ffmpeg_with_progress(cmd: list[str], duration: float | None,
                              reporter: ProgressReporter) -> subprocess.CompletedProcess:
    """Run ffmpeg command while parsing stderr for progress updates."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=False,
    )

    stderr_data = b""
    line_buf = b""

    while True:
        chunk = process.stderr.read(1) if process.stderr else b""
        if not chunk:
            break
        stderr_data += chunk
        if chunk in (b"\r", b"\n"):
            line = line_buf.decode(errors="replace").strip()
            line_buf = b""
            if line:
                progress = parse_ffmpeg_progress(line, duration)
                if progress:
                    reporter.update_encoding_progress(
                        percent=progress.get("percent"),
                        fps=progress.get("fps"),
                        speed=progress.get("speed"),
                        time_str=progress.get("time_str"),
                    )
        else:
            line_buf += chunk

    process.wait()

    if process.returncode != 0:
        err = stderr_data.decode(errors="replace")
        raise RuntimeError(f"ffmpeg failed (exit {process.returncode}):\n{err}")

    return subprocess.CompletedProcess(
        args=cmd, returncode=process.returncode,
        stdout=process.stdout.read() if process.stdout else b"",
        stderr=stderr_data,
    )
