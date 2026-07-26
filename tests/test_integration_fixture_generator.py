from pathlib import Path

from tests.integration.fixture_generator import (
    FIXTURE_DURATION,
    ffmpeg_fixture_command,
)


def test_fixture_command_is_tiny_deterministic_and_overwrites_only_target():
    target = Path("fixture.mkv")
    first = ffmpeg_fixture_command("ffmpeg", target)
    second = ffmpeg_fixture_command("ffmpeg", target)
    assert first == second
    assert FIXTURE_DURATION == "0.25"
    assert "64x64" in " ".join(first)
    assert first[-1] == str(target)
    assert "-threads" in first
