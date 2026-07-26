"""Generate tiny deterministic media fixtures for integration tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

FIXTURE_DURATION = "0.25"


def ffmpeg_fixture_command(ffmpeg: str, output: Path) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=64x64:r=4:d={FIXTURE_DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={FIXTURE_DURATION}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-metadata:s:a:0", "language=eng",
        "-c:v", "ffv1", "-c:a", "pcm_s16le",
        "-threads", "1", str(output),
    ]


def generate_fixture(ffmpeg: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(ffmpeg_fixture_command(ffmpeg, output), check=True)
