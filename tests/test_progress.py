import math
import io
from unittest.mock import patch

import pytest

from de_dolby.progress import (
    EtaEstimator,
    ProgressReporter,
    Step,
    calculate_eta_seconds,
    effective_progress_duration,
    format_eta,
    parse_ffmpeg_progress,
)


def test_calculate_eta_seconds_uses_remaining_media_and_speed():
    assert calculate_eta_seconds(900.0, 120.0, 2.0) == 390.0
    assert calculate_eta_seconds(100.0, 100.0, 3.0) == 0.0
    assert calculate_eta_seconds(100.0, 110.0, 3.0) == 0.0


@pytest.mark.parametrize(
    ("duration", "processed", "speed"),
    [
        (None, 1, 1),
        (100, None, 1),
        (100, 1, None),
        (0, 1, 1),
        (100, -1, 1),
        (100, 1, 0),
        (100, 1, -1),
        (100, 1, "unknown"),
        (math.inf, 1, 1),
    ],
)
def test_calculate_eta_seconds_rejects_unavailable_values(
    duration, processed, speed
):
    assert calculate_eta_seconds(duration, processed, speed) is None


def test_format_eta_always_shows_minutes_and_seconds():
    assert format_eta(0) == "0m 00s"
    assert format_eta(12.1) == "0m 13s"
    assert format_eta(754) == "12m 34s"
    assert format_eta(3754) == "1h 02m 34s"


@pytest.mark.parametrize("value", [None, -1, math.inf, "unknown"])
def test_format_eta_rejects_unavailable_values(value):
    assert format_eta(value) is None


def test_ffmpeg_progress_preserves_speed_text_and_parses_multiplier():
    result = parse_ffmpeg_progress(
        "frame= 20 fps=12.5 time=00:00:10.00 speed= 3.34x", 100.0
    )
    assert result["speed"] == "3.34x"
    assert result["speed_multiplier"] == 3.34
    assert result["time_seconds"] == 10.0
    assert result["percent"] == 10.0


def test_effective_progress_duration_uses_bounded_sample_duration():
    assert effective_progress_duration(100.0, None) == 100.0
    assert effective_progress_duration(100.0, 30) == 30.0
    assert effective_progress_duration(10.0, 30) == 10.0
    assert effective_progress_duration(None, 30) == 30.0


def test_eta_estimator_smooths_speed_and_resets_between_steps():
    estimator = EtaEstimator(window_size=3)
    assert estimator.update(100, 20, 1.0) == 80.0
    assert estimator.update(100, 20, 3.0) == 40.0
    estimator.reset()
    assert estimator.update(100, 20, 4.0) == 20.0


def test_progress_reporter_renders_eta_with_existing_stats():
    reporter = ProgressReporter([Step("encode", "Encoding")])
    reporter.current_step = 0
    stderr = io.StringIO()
    with patch("de_dolby.progress.sys.stderr", stderr):
        reporter.update_encoding_progress(
            percent=25.0,
            fps=60.0,
            speed="2.00x",
            time_seconds=250.0,
            speed_multiplier=2.0,
            duration=1000.0,
        )
    rendered = stderr.getvalue()
    assert "25.0%" in rendered
    assert "60.0 fps" in rendered
    assert "2.00x" in rendered
    assert "ETA 6m 15s" in rendered


def test_progress_reporter_omits_eta_when_data_is_unavailable():
    reporter = ProgressReporter([Step("encode", "Encoding")])
    reporter.current_step = 0
    stderr = io.StringIO()
    with patch("de_dolby.progress.sys.stderr", stderr):
        reporter.update_encoding_progress(
            percent=0.0, speed="N/A", duration=1000.0
        )
    assert "ETA" not in stderr.getvalue()
