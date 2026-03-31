"""Tests for de_dolby.cli — argument parsing, validation, and helper functions."""

import json
from io import StringIO
from unittest.mock import MagicMock, patch

from de_dolby.cli import _expand_globs, derive_output_name, main


def test_derive_output_name_with_dv():
    assert derive_output_name("movie.DV.mkv") == "movie.HDR10.mkv"
    assert derive_output_name("movie.dv.mkv") == "movie.HDR10.mkv"
    assert derive_output_name("movie.Dv.mkv") == "movie.HDR10.mkv"


def test_derive_output_name_without_dv():
    assert derive_output_name("movie.mkv") == "movie.HDR10.mkv"


def test_derive_output_name_with_path():
    assert derive_output_name("/path/to/movie.DV.mkv") == "/path/to/movie.HDR10.mkv"


def test_derive_output_name_preserves_extension():
    result = derive_output_name("movie.mkv")
    assert result.endswith(".mkv")


def test_expand_globs_no_wildcards():
    """Paths without wildcards pass through unchanged."""
    result = _expand_globs(["file1.mkv", "file2.mkv"])
    assert result == ["file1.mkv", "file2.mkv"]


def test_expand_globs_nonexistent_pattern():
    """Non-matching glob patterns are kept as literals."""
    result = _expand_globs(["nonexistent_*.zzz"])
    assert result == ["nonexistent_*.zzz"]


# --- CLI validation tests (mock require_tools so missing tools don't interfere) ---


def _run_main(*args: str) -> int:
    """Run main() with given args, return exit code. Mocks require_tools."""
    with patch("de_dolby.cli.require_tools"), patch("sys.argv", ["de-dolby"] + list(args)):
        try:
            main()
            return 0
        except SystemExit as e:
            return e.code


def _run_main_stderr(*args: str) -> tuple[int, str]:
    """Run main() with given args, return (exit_code, stderr)."""
    import io

    captured = io.StringIO()
    with (
        patch("de_dolby.cli.require_tools"),
        patch("sys.argv", ["de-dolby"] + list(args)),
        patch("sys.stderr", captured),
    ):
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code
    return code, captured.getvalue()


def test_no_command_exits_2():
    assert _run_main() == 2


def test_crf_too_high_exits_2():
    code, err = _run_main_stderr("convert", "fake.mkv", "--crf", "99")
    assert code == 2
    assert "--crf" in err


def test_crf_negative_exits_2():
    code, err = _run_main_stderr("convert", "fake.mkv", "--crf", "-1")
    assert code == 2
    assert "--crf" in err


def test_crf_valid_passes_validation():
    """CRF 18 should pass validation (fails later on missing file, not on CRF)."""
    code, err = _run_main_stderr("convert", "fake.mkv", "--crf", "18")
    assert "--crf" not in err  # no CRF error
    assert code == 1  # fails on "file not found", not validation


def test_sample_negative_exits_2():
    code, err = _run_main_stderr("convert", "fake.mkv", "--sample", "-5")
    assert code == 2
    assert "--sample" in err


def test_temp_dir_nonexistent_exits_1():
    code, err = _run_main_stderr("convert", "fake.mkv", "--temp-dir", "/nonexistent_dir_abc123")
    assert code == 1
    assert "--temp-dir" in err


def test_multiple_files_with_output_exits_2():
    code, err = _run_main_stderr("convert", "a.mkv", "b.mkv", "-o", "out.mkv")
    assert code == 2
    assert "-o" in err or "--output" in err


# --- JSON output tests for info command ---


def _run_main_stdout(*args: str) -> tuple[int, str]:
    """Run main() with given args, return (exit_code, stdout)."""
    captured = StringIO()
    with (
        patch("de_dolby.cli.require_tools"),
        patch("sys.argv", ["de-dolby"] + list(args)),
        patch("sys.stdout", captured),
    ):
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code
    return code, captured.getvalue()


def test_info_json_flag_outputs_json():
    """Test that --json flag outputs valid JSON."""
    mock_info = MagicMock()
    mock_info.to_dict.return_value = {
        "file": "/path/to/test.mkv",
        "duration_seconds": 3600.0,
        "duration_formatted": "1:00:00",
        "bitrate_kbps": 25000,
        "size_bytes": 11274289152,
        "dolby_vision": {"profile": 7, "bl_signal_compatibility_id": 6},
        "hdr10": {"detected": True, "max_cll": 1000, "max_fall": 400},
        "video": [{"index": 0, "codec": "hevc", "width": 3840, "height": 2160}],
        "audio": [{"index": 1, "codec": "eac3", "language": "eng"}],
        "subtitles": [],
    }

    with (
        patch("de_dolby.cli.probe", return_value=mock_info),
        patch("pathlib.Path.exists", return_value=True),
    ):
        code, out = _run_main_stdout("info", "/path/to/test.mkv", "--json")
        assert code == 0
        # Parse the JSON output
        data = json.loads(out)
        assert data["file"] == "/path/to/test.mkv"
        assert data["dolby_vision"]["profile"] == 7


def test_info_json_pretty_print():
    """Test that --pretty flag adds indentation."""
    mock_info = MagicMock()
    mock_info.to_dict.return_value = {"file": "/path/to/test.mkv"}

    with (
        patch("de_dolby.cli.probe", return_value=mock_info),
        patch("pathlib.Path.exists", return_value=True),
    ):
        code, out = _run_main_stdout("info", "/path/to/test.mkv", "--json", "--pretty")
        assert code == 0
        # Pretty printed JSON should contain newlines
        assert "\n" in out
        # Parse to verify it's valid JSON
        data = json.loads(out)
        assert data["file"] == "/path/to/test.mkv"


def test_info_json_multiple_files():
    """Test that multiple files with --json outputs an array."""
    mock_info1 = MagicMock()
    mock_info1.to_dict.return_value = {"file": "/path/to/test1.mkv"}
    mock_info2 = MagicMock()
    mock_info2.to_dict.return_value = {"file": "/path/to/test2.mkv"}

    with (
        patch("de_dolby.cli.probe", side_effect=[mock_info1, mock_info2]),
        patch("pathlib.Path.exists", return_value=True),
    ):
        code, out = _run_main_stdout("info", "/path/to/test1.mkv", "/path/to/test2.mkv", "--json")
        assert code == 0
        data = json.loads(out)
        # Should be an array when multiple files
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["file"] == "/path/to/test1.mkv"
        assert data[1]["file"] == "/path/to/test2.mkv"


def test_info_json_with_processing_estimate():
    """Test that JSON output includes processing estimation when available."""
    mock_info = MagicMock()
    mock_info.to_dict.return_value = {"file": "/path/to/test.mkv"}

    mock_estimate = MagicMock()
    mock_estimate.pipeline_type = "lossless"
    mock_estimate.encoder_name = "copy"
    mock_estimate.estimated_time_minutes = (2.0, 5.0)
    mock_estimate.estimated_output_size = 10000000000

    with (
        patch("de_dolby.cli.probe", return_value=mock_info),
        patch("de_dolby.estimate.estimate_conversion", return_value=mock_estimate),
        patch("pathlib.Path.exists", return_value=True),
    ):
        code, out = _run_main_stdout("info", "/path/to/test.mkv", "--json")
        assert code == 0
        data = json.loads(out)
        assert "estimated_processing" in data
        assert data["estimated_processing"]["pipeline"] == "lossless"
        assert data["estimated_processing"]["encoder"] == "copy"
