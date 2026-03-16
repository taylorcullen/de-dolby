"""Tests for de_dolby.cli — argument parsing and helper functions."""

from de_dolby.cli import derive_output_name, _expand_globs


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
