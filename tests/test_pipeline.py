"""Tests for de_dolby.pipeline — ConvertOptions, encode commands, and utilities."""

import pytest
from unittest.mock import patch

from de_dolby.pipeline import ConvertOptions, _format_bytes, _build_encode_cmd, _check_disk_space
from de_dolby.metadata import HDR10Metadata
from de_dolby.probe import FileInfo, StreamInfo
from de_dolby.config import DEFAULT_MASTER_DISPLAY


def test_convert_options_defaults():
    opts = ConvertOptions()
    assert opts.encoder == "auto"
    assert opts.quality == "balanced"
    assert opts.crf is None
    assert opts.bitrate is None
    assert opts.sample_seconds is None
    assert opts.temp_dir is None
    assert opts.dry_run is False
    assert opts.verbose is False
    assert opts.force is False


def test_convert_options_temp_dir():
    opts = ConvertOptions(temp_dir="/tmp/custom")
    assert opts.temp_dir == "/tmp/custom"


def test_format_bytes():
    assert _format_bytes(500) == "500.0 B"
    assert _format_bytes(1024) == "1.0 KB"
    assert _format_bytes(1024 * 1024) == "1.0 MB"
    assert _format_bytes(1024 * 1024 * 1024) == "1.0 GB"


def test_build_encode_cmd_libx265():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "libx265", meta, opts,
                            video_only=True)
    assert "ffmpeg" in cmd[0]
    assert "-c:v" in cmd
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "libx265"
    assert "-crf" in cmd
    assert "-x265-params" in cmd
    assert "-an" in cmd  # video_only
    assert "-sn" in cmd


def test_build_encode_cmd_hevc_amf():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="hevc_amf", quality="fast")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "hevc_amf", meta, opts,
                            video_only=True, source_bitrate=25000000)
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "hevc_amf"
    assert "-quality" in cmd
    assert "-b:v" in cmd  # should have bitrate from source


def test_build_encode_cmd_with_sample():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced", sample_seconds=30)
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "libx265", meta, opts,
                            video_only=True)
    assert "-t" in cmd
    idx = cmd.index("-t")
    assert cmd[idx + 1] == "30"


def test_build_encode_cmd_copy():
    meta = HDR10Metadata(master_display="", max_cll=0, max_fall=0)
    opts = ConvertOptions(encoder="copy")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "copy", meta, opts,
                            video_only=True)
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "copy"


def test_build_encode_cmd_crf_override():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced", crf=22)
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "libx265", meta, opts,
                            video_only=True)
    idx = cmd.index("-crf")
    assert cmd[idx + 1] == "22"


def test_build_encode_cmd_dv_profile5_filter():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "libx265", meta, opts,
                            video_only=True, dv_profile5=True)
    assert "-vf" in cmd
    idx = cmd.index("-vf")
    assert "libplacebo" in cmd[idx + 1]


def test_build_encode_cmd_hevc_amf_bitrate_fallback():
    """hevc_amf should get 40M fallback when source bitrate is unknown."""
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="hevc_amf", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "hevc_amf", meta, opts,
                            video_only=True, source_bitrate=None)
    assert "-b:v" in cmd
    idx = cmd.index("-b:v")
    assert cmd[idx + 1] == "40M"


def test_build_encode_cmd_hevc_amf_explicit_bitrate():
    """Explicit --bitrate overrides both source and fallback."""
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="hevc_amf", quality="balanced", bitrate="60M")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", "hevc_amf", meta, opts,
                            video_only=True, source_bitrate=25000000)
    idx = cmd.index("-b:v")
    assert cmd[idx + 1] == "60M"


def test_check_disk_space_no_warning_when_enough(capsys):
    """No warning when plenty of space available."""
    info = FileInfo(path="test.mkv", duration=100.0, overall_bitrate=25000000)
    opts = ConvertOptions()
    with patch("de_dolby.pipeline.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = type("Usage", (), {"free": 100 * 1024**3})()  # 100 GB
        _check_disk_space(info, opts)
    captured = capsys.readouterr()
    assert "Warning" not in captured.err


def test_check_disk_space_warns_when_low(capsys):
    """Warning when free space is less than estimated need."""
    info = FileInfo(path="test.mkv", duration=3600.0, overall_bitrate=50000000)  # ~22 GB source
    opts = ConvertOptions()
    with patch("de_dolby.pipeline.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = type("Usage", (), {"free": 1 * 1024**3})()  # 1 GB free
        _check_disk_space(info, opts)
    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "--temp-dir" in captured.err


def test_check_disk_space_skips_without_bitrate(capsys):
    """No check when bitrate is unknown."""
    info = FileInfo(path="test.mkv", duration=100.0, overall_bitrate=None)
    opts = ConvertOptions()
    _check_disk_space(info, opts)
    captured = capsys.readouterr()
    assert captured.err == ""


# --- AV1 encoder tests ---

def test_build_encode_cmd_av1_amf():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="av1_amf", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.ivf", "av1_amf", meta, opts,
                            video_only=True, source_bitrate=25000000)
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "av1_amf"
    assert "-b:v" in cmd
    assert "-f" in cmd
    fidx = cmd.index("-f")
    assert cmd[fidx + 1] == "ivf"


def test_build_encode_cmd_av1_amf_bitrate_fallback():
    """av1_amf should get 40M fallback like hevc_amf."""
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="av1_amf", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.ivf", "av1_amf", meta, opts,
                            video_only=True, source_bitrate=None)
    idx = cmd.index("-b:v")
    assert cmd[idx + 1] == "40M"


def test_build_encode_cmd_libsvtav1():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libsvtav1", quality="quality")
    cmd = _build_encode_cmd("input.mkv", "output.ivf", "libsvtav1", meta, opts,
                            video_only=True)
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "libsvtav1"
    assert "-crf" in cmd
    assert "-svtav1-params" in cmd
    assert "-preset" in cmd
    fidx = cmd.index("-f")
    assert cmd[fidx + 1] == "ivf"


def test_build_encode_cmd_libsvtav1_crf_override():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libsvtav1", quality="balanced", crf=30)
    cmd = _build_encode_cmd("input.mkv", "output.ivf", "libsvtav1", meta, opts,
                            video_only=True)
    idx = cmd.index("-crf")
    assert cmd[idx + 1] == "30"


def test_build_encode_cmd_libsvtav1_hdr_color_flags():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libsvtav1", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.ivf", "libsvtav1", meta, opts,
                            video_only=True)
    assert "-color_primaries" in cmd
    idx = cmd.index("-color_primaries")
    assert cmd[idx + 1] == "bt2020"
    idx = cmd.index("-color_trc")
    assert cmd[idx + 1] == "smpte2084"
