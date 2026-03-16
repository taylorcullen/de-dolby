"""Tests for de_dolby.cli — argument parsing, validation, and helper functions."""

import sys
from unittest.mock import patch

import pytest

from de_dolby.cli import derive_output_name, _expand_globs, main


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
    with patch("de_dolby.cli.require_tools"), \
         patch("sys.argv", ["de-dolby"] + list(args)):
        try:
            main()
            return 0
        except SystemExit as e:
            return e.code


def _run_main_stderr(*args: str) -> tuple[int, str]:
    """Run main() with given args, return (exit_code, stderr)."""
    import io
    captured = io.StringIO()
    with patch("de_dolby.cli.require_tools"), \
         patch("sys.argv", ["de-dolby"] + list(args)), \
         patch("sys.stderr", captured):
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
