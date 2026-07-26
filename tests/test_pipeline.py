"""Tests for de_dolby.pipeline — ConvertOptions, encode commands, and utilities."""

from pathlib import Path

import pytest
from unittest.mock import patch

from de_dolby.codecs import get_encoder
from de_dolby.pipeline import (
    ConvertOptions,
    _build_encode_cmd,
    _check_disk_space,
    convert,
    execute_conversion_plan,
)
from de_dolby.plan import create_conversion_plan
from de_dolby.utils import format_bytes
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
    assert opts.unsafe_skip_validation is False


def test_convert_options_temp_dir():
    opts = ConvertOptions(temp_dir="/tmp/custom")
    assert opts.temp_dir == "/tmp/custom"


def test_format_bytes():
    assert format_bytes(500) == "500.0 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1024 * 1024) == "1.0 MB"
    assert format_bytes(1024 * 1024 * 1024) == "1.0 GB"


def test_build_encode_cmd_libx265():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("libx265"), meta, opts,
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
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("hevc_amf"), meta, opts,
                            video_only=True, source_bitrate=25000000)
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "hevc_amf"
    assert "-quality" in cmd
    assert "-b:v" in cmd  # should have bitrate from source


def test_build_encode_cmd_with_sample():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced", sample_seconds=30)
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("libx265"), meta, opts,
                            video_only=True)
    assert "-t" in cmd
    idx = cmd.index("-t")
    assert cmd[idx + 1] == "30"


def test_build_encode_cmd_copy():
    meta = HDR10Metadata(master_display="", max_cll=0, max_fall=0)
    opts = ConvertOptions(encoder="copy")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("copy"), meta, opts,
                            video_only=True)
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "copy"


def test_build_encode_cmd_crf_override():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced", crf=22)
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("libx265"), meta, opts,
                            video_only=True)
    idx = cmd.index("-crf")
    assert cmd[idx + 1] == "22"


def test_build_encode_cmd_dv_profile5_filter():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libx265", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("libx265"), meta, opts,
                            video_only=True, dv_profile5=True)
    assert "-vf" in cmd
    idx = cmd.index("-vf")
    assert "libplacebo" in cmd[idx + 1]


def test_build_encode_cmd_hevc_amf_bitrate_fallback():
    """hevc_amf should get 40M fallback when source bitrate is unknown."""
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="hevc_amf", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("hevc_amf"), meta, opts,
                            video_only=True, source_bitrate=None)
    assert "-b:v" in cmd
    idx = cmd.index("-b:v")
    assert cmd[idx + 1] == "40M"


def test_build_encode_cmd_hevc_amf_explicit_bitrate():
    """Explicit --bitrate overrides both source and fallback."""
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="hevc_amf", quality="balanced", bitrate="60M")
    cmd = _build_encode_cmd("input.mkv", "output.hevc", get_encoder("hevc_amf"), meta, opts,
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
    cmd = _build_encode_cmd("input.mkv", "output.ivf", get_encoder("av1_amf"), meta, opts,
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
    cmd = _build_encode_cmd("input.mkv", "output.ivf", get_encoder("av1_amf"), meta, opts,
                            video_only=True, source_bitrate=None)
    idx = cmd.index("-b:v")
    assert cmd[idx + 1] == "40M"


def test_build_encode_cmd_libsvtav1():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libsvtav1", quality="quality")
    cmd = _build_encode_cmd("input.mkv", "output.ivf", get_encoder("libsvtav1"), meta, opts,
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
    cmd = _build_encode_cmd("input.mkv", "output.ivf", get_encoder("libsvtav1"), meta, opts,
                            video_only=True)
    idx = cmd.index("-crf")
    assert cmd[idx + 1] == "30"


def test_build_encode_cmd_libsvtav1_hdr_color_flags():
    meta = HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)
    opts = ConvertOptions(encoder="libsvtav1", quality="balanced")
    cmd = _build_encode_cmd("input.mkv", "output.ivf", get_encoder("libsvtav1"), meta, opts,
                            video_only=True)
    assert "-color_primaries" in cmd
    idx = cmd.index("-color_primaries")
    assert cmd[idx + 1] == "bt2020"
    idx = cmd.index("-color_trc")
    assert cmd[idx + 1] == "smpte2084"


def _conversion_info(profile=7, codec="hevc"):
    return FileInfo(
        path="input.mkv", duration=100.0, overall_bitrate=8_000_000,
        dv_profile=profile,
        video_streams=[
            StreamInfo(index=0, codec_type="video", codec_name=codec)
        ],
    )


@patch("de_dolby.pipeline.execute_conversion_plan")
@patch("de_dolby.pipeline.Path.exists", return_value=False)
@patch("de_dolby.pipeline.probe")
def test_convert_passes_immutable_plan_to_executor(mock_probe, exists, execute):
    info = _conversion_info()
    mock_probe.return_value = info

    convert("input.mkv", "output.mkv", ConvertOptions())

    plan, passed_info, options = execute.call_args.args
    assert plan.input_path == "input.mkv"
    assert plan.output_path == "output.mkv"
    assert plan.encoder == "copy"
    assert passed_info is info
    assert options.encoder == "auto"


@patch("de_dolby.pipeline._run_lossless")
@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
def test_executor_uses_planned_lossless_route(
    display, disk, run_lossless, tmp_path
):
    info = _conversion_info()
    options = ConvertOptions(unsafe_skip_validation=True)
    plan = create_conversion_plan(info, str(tmp_path / "planned.mkv"))
    run_lossless.side_effect = (
        lambda info, codec, output, options, **kwargs:
        Path(output).write_bytes(b"lossless result")
    )

    execute_conversion_plan(plan, info, options)

    run_lossless.assert_called_once()
    staging = Path(run_lossless.call_args.args[2])
    assert staging.parent == tmp_path
    assert ".staging" in staging.name
    assert run_lossless.call_args.kwargs["final_output_path"] == plan.output_path
    assert Path(plan.output_path).read_bytes() == b"lossless result"
    assert not staging.exists()
    disk.assert_called_once_with(
        info, options, estimated_bytes=plan.estimated_temp_bytes
    )


@patch("de_dolby.pipeline._run_reencode")
@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
def test_executor_uses_planned_reencode_encoder(
    display, disk, run_reencode, tmp_path
):
    info = _conversion_info(profile=10, codec="av1")
    options = ConvertOptions(unsafe_skip_validation=True)
    plan = create_conversion_plan(
        info, str(tmp_path / "planned.mkv"),
        available_encoders={"libsvtav1"},
    )
    run_reencode.side_effect = (
        lambda info, codec, encoder, output, options, **kwargs:
        Path(output).write_bytes(b"encoded result")
    )

    execute_conversion_plan(plan, info, options)

    assert run_reencode.call_args.args[2].ffmpeg_name == "libsvtav1"
    staging = Path(run_reencode.call_args.args[3])
    assert ".staging" in staging.name
    assert run_reencode.call_args.kwargs["dv_profile5"] is False
    assert run_reencode.call_args.kwargs["final_output_path"] == plan.output_path
    assert Path(plan.output_path).read_bytes() == b"encoded result"
    assert not staging.exists()


@patch("de_dolby.pipeline.tempfile.mkdtemp")
@patch("de_dolby.pipeline._run_lossless")
@patch("de_dolby.pipeline.display_banner")
def test_dry_run_stops_after_plan_without_temp_files(display, run_lossless, mkdtemp):
    info = _conversion_info()
    plan = create_conversion_plan(info, "planned.mkv")

    execute_conversion_plan(plan, info, ConvertOptions(dry_run=True))

    mkdtemp.assert_not_called()
    run_lossless.assert_not_called()


@patch("de_dolby.pipeline.check_encoder_available", return_value=False)
def test_planning_rejects_explicit_encoder_missing_from_ffmpeg(available):
    with pytest.raises(RuntimeError, match="not available"):
        from de_dolby.pipeline import _plan_from_info
        _plan_from_info(
            _conversion_info(profile=5),
            "output.mkv",
            ConvertOptions(encoder="libx265"),
        )


def _staging_files(directory):
    return list(directory.glob(".*.de-dolby-*.staging.*"))


@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
@patch("de_dolby.pipeline._run_lossless")
def test_conversion_failure_removes_partial_staging(
    run_lossless, display, disk, tmp_path
):
    info = _conversion_info()
    destination = tmp_path / "output.mkv"
    plan = create_conversion_plan(info, str(destination))

    def fail_after_partial(info, codec, output, options, **kwargs):
        Path(output).write_bytes(b"partial")
        raise RuntimeError("remux failed")

    run_lossless.side_effect = fail_after_partial
    with pytest.raises(RuntimeError, match="remux failed"):
        execute_conversion_plan(
            plan, info, ConvertOptions(unsafe_skip_validation=True)
        )

    assert not destination.exists()
    assert _staging_files(tmp_path) == []


@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
@patch("de_dolby.pipeline._run_lossless")
def test_keyboard_interrupt_removes_partial_staging(
    run_lossless, display, disk, tmp_path
):
    info = _conversion_info()
    destination = tmp_path / "output.mkv"
    plan = create_conversion_plan(info, str(destination))

    def interrupt_after_partial(info, codec, output, options, **kwargs):
        Path(output).write_bytes(b"partial")
        raise KeyboardInterrupt()

    run_lossless.side_effect = interrupt_after_partial
    with pytest.raises(KeyboardInterrupt):
        execute_conversion_plan(plan, info, ConvertOptions())

    assert not destination.exists()
    assert _staging_files(tmp_path) == []


@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
@patch("de_dolby.pipeline._run_reencode")
def test_force_failure_preserves_existing_destination(
    run_reencode, display, disk, tmp_path
):
    info = _conversion_info(profile=10, codec="av1")
    destination = tmp_path / "output.mkv"
    destination.write_bytes(b"valid original")
    plan = create_conversion_plan(
        info, str(destination), available_encoders={"libsvtav1"}
    )

    def fail_after_partial(info, codec, encoder, output, options, **kwargs):
        Path(output).write_bytes(b"partial replacement")
        raise RuntimeError("encode failed")

    run_reencode.side_effect = fail_after_partial
    with pytest.raises(RuntimeError, match="encode failed"):
        execute_conversion_plan(plan, info, ConvertOptions(force=True))

    assert destination.read_bytes() == b"valid original"
    assert _staging_files(tmp_path) == []


@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
@patch("de_dolby.pipeline._run_lossless")
def test_no_force_publication_race_preserves_competing_destination(
    run_lossless, display, disk, tmp_path
):
    info = _conversion_info()
    destination = tmp_path / "output.mkv"
    plan = create_conversion_plan(info, str(destination))

    def create_race(info, codec, output, options, **kwargs):
        Path(output).write_bytes(b"complete staged output")
        destination.write_bytes(b"competing valid output")

    run_lossless.side_effect = create_race
    with pytest.raises(RuntimeError, match="already exists"):
        execute_conversion_plan(
            plan, info, ConvertOptions(unsafe_skip_validation=True)
        )

    assert destination.read_bytes() == b"competing valid output"
    assert _staging_files(tmp_path) == []


@patch("de_dolby.pipeline.display_banner")
def test_executor_reports_every_planned_omission(display, capsys):
    info = _conversion_info()
    info.other_streams = [
        StreamInfo(index=4, codec_type="data", codec_name="bin_data")
    ]
    plan = create_conversion_plan(info, "output.mkv")

    execute_conversion_plan(plan, info, ConvertOptions(dry_run=True))

    assert "Warning: Stream #4: unsupported stream type: data" in capsys.readouterr().err


@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
@patch("de_dolby.pipeline._run_lossless")
@patch("de_dolby.pipeline.validate_staged_output")
def test_validation_failure_prevents_publication(
    validate, run_lossless, display, disk, tmp_path
):
    from de_dolby.validation import (
        ValidationCode, ValidationIssue, ValidationReport, ValidationSeverity,
    )
    info = _conversion_info()
    destination = tmp_path / "output.mkv"
    plan = create_conversion_plan(info, str(destination))
    run_lossless.side_effect = (
        lambda info, codec, output, options, **kwargs:
        Path(output).write_bytes(b"invalid complete media")
    )
    validate.return_value = ValidationReport(
        info.path, str(destination),
        (
            ValidationIssue(
                ValidationCode.DOLBY_VISION_PRESENT,
                ValidationSeverity.ERROR,
                "Dolby Vision remains",
            ),
        ),
    )

    with pytest.raises(
        RuntimeError, match="dolby_vision_present: Dolby Vision remains"
    ):
        execute_conversion_plan(plan, info, ConvertOptions())

    assert not destination.exists()
    assert _staging_files(tmp_path) == []


@patch("de_dolby.pipeline._check_disk_space")
@patch("de_dolby.pipeline.display_banner")
@patch("de_dolby.pipeline._run_lossless")
@patch("de_dolby.pipeline.validate_staged_output")
def test_unsafe_skip_validation_is_the_only_bypass(
    validate, run_lossless, display, disk, tmp_path
):
    info = _conversion_info()
    destination = tmp_path / "output.mkv"
    plan = create_conversion_plan(info, str(destination))
    run_lossless.side_effect = (
        lambda info, codec, output, options, **kwargs:
        Path(output).write_bytes(b"unvalidated media")
    )

    execute_conversion_plan(
        plan, info, ConvertOptions(unsafe_skip_validation=True)
    )

    validate.assert_not_called()
    assert destination.read_bytes() == b"unvalidated media"
