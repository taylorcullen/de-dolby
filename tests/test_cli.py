"""Tests for de_dolby.cli — argument parsing, validation, and helper functions."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from de_dolby.cli import derive_output_name, _expand_globs, main
from de_dolby.diagnostics import (
    FfmpegCapabilities, TempDirectoryDiagnostic, ToolDiagnostic,
)
from de_dolby.plan import create_conversion_plan
from de_dolby.probe import FileInfo, StreamInfo
from de_dolby.validation import (
    ValidationCode, ValidationIssue, ValidationReport, ValidationSeverity,
)
from de_dolby.settings import SettingsConfig


@pytest.fixture(autouse=True)
def isolate_user_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


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


def test_resume_requires_manifest():
    code, err = _run_main_stderr("convert", "movie.mkv", "--resume")
    assert code == 2
    assert "--manifest" in err


def test_retry_failed_requires_resume():
    code, err = _run_main_stderr(
        "convert", "movie.mkv", "--manifest", "batch.json", "--retry-failed"
    )
    assert code == 2
    assert "--resume" in err


def test_named_preset_applies_and_cli_values_win(tmp_path):
    input_path = tmp_path / "movie.mkv"
    input_path.write_bytes(b"input")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
schema_version = 1
[defaults]
quality = "fast"
[presets.cpu]
encoder = "libx265"
quality = "quality"
crf = 20
""",
        encoding="utf-8",
    )
    with patch("de_dolby.cli.require_tools"), \
         patch("de_dolby.cli.check_encoder_available", return_value=True), \
         patch("de_dolby.cli.convert") as convert_mock, \
         patch("sys.argv", [
             "de-dolby", "convert", str(input_path),
             "--config", str(config_path), "--preset", "cpu", "--crf", "17",
         ]):
        main()
    options = convert_mock.call_args.args[2]
    assert options.encoder == "libx265"
    assert options.quality == "quality"
    assert options.crf == 17


def test_unknown_preset_reports_config_path(tmp_path):
    input_path = tmp_path / "movie.mkv"
    input_path.write_bytes(b"input")
    config_path = tmp_path / "config.toml"
    config_path.write_text("schema_version = 1\n", encoding="utf-8")
    code, error = _run_main_stderr(
        "convert", str(input_path), "--config", str(config_path),
        "--preset", "missing",
    )
    assert code == 2
    assert str(config_path) in error
    assert "presets.missing" in error


def _doctor_tools(available=True):
    return tuple(
        ToolDiagnostic(
            name, name, f"/tools/{name}" if available else None,
            f"{name} version 1" if available else None,
            None if available else f"{name} executable was not found",
        )
        for name in ("ffmpeg", "ffprobe", "dovi_tool", "mkvmerge")
    )


def _writable_temp(path="/tmp"):
    return TempDirectoryDiagnostic(path, True, True, 1_000_000)


def test_doctor_json_renders_schema_and_exits_zero(capsys):
    capabilities = FfmpegCapabilities(
        frozenset({"libx265"}), frozenset({"libplacebo"})
    )
    with patch("de_dolby.cli.probe_tool_versions", return_value=_doctor_tools()), \
         patch("de_dolby.cli.probe_ffmpeg_capabilities",
               return_value=capabilities), \
         patch("de_dolby.cli.probe_temp_directory",
               return_value=_writable_temp()):
        assert _run_main("doctor", "--json") == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 2
    assert document["usable"] is True
    assert document["ffmpeg"]["encoders"] == ["libx265"]


def test_doctor_json_custom_paths_are_reflected(capsys):
    captured_paths = []

    def probe_versions(paths):
        captured_paths.append(paths)
        return _doctor_tools()

    with patch("de_dolby.cli.probe_tool_versions", side_effect=probe_versions), \
         patch("de_dolby.cli.probe_ffmpeg_capabilities",
               return_value=FfmpegCapabilities(
                   frozenset({"libx265"}), frozenset()
               )), \
         patch("de_dolby.cli.probe_temp_directory",
               return_value=_writable_temp()):
        _run_main(
            "doctor", "--json",
            "--ffmpeg", "/custom/ffmpeg",
            "--dovi-tool", "/custom/dovi",
            "--mkvmerge", "/custom/mkvmerge",
        )
    assert captured_paths[0].ffmpeg == "/custom/ffmpeg"
    assert captured_paths[0].dovi_tool == "/custom/dovi"
    assert captured_paths[0].mkvmerge == "/custom/mkvmerge"


def test_doctor_human_output_distinguishes_missing_tool_and_capability(capsys):
    with patch("de_dolby.cli.probe_tool_versions",
               return_value=_doctor_tools(available=False)), \
         patch("de_dolby.cli.probe_ffmpeg_capabilities",
               return_value=FfmpegCapabilities(frozenset(), frozenset())), \
         patch("de_dolby.cli.probe_temp_directory",
               return_value=_writable_temp()):
        assert _run_main("doctor") == 1
    output = capsys.readouterr().out
    assert "[missing] ffmpeg" in output
    assert "ffmpeg encoders: none" in output
    assert "libplacebo filter: missing" in output
    assert "install an ffmpeg build with libplacebo support" in output
    assert "choose a writable directory with --temp-dir" not in output


def test_doctor_exits_nonzero_when_capability_probe_fails(capsys):
    with patch("de_dolby.cli.probe_tool_versions",
               return_value=_doctor_tools()), \
         patch("de_dolby.cli.probe_ffmpeg_capabilities",
               return_value=FfmpegCapabilities(
                   frozenset(), frozenset(), ("probe failed",)
               )), \
         patch("de_dolby.cli.probe_temp_directory",
               return_value=_writable_temp()):
        assert _run_main("doctor", "--json") == 1
    assert json.loads(capsys.readouterr().out)["usable"] is False


def test_doctor_custom_temp_and_requested_profile_control_exit(capsys):
    captured = []

    def temp_probe(path):
        captured.append(path)
        return _writable_temp(path)

    capabilities = FfmpegCapabilities(
        frozenset({"libsvtav1"}), frozenset()
    )
    tools = tuple(
        tool if tool.name != "dovi_tool" else
        ToolDiagnostic("dovi_tool", "dovi_tool", None, None, "missing")
        for tool in _doctor_tools()
    )
    with patch("de_dolby.cli.probe_tool_versions", return_value=tools), \
         patch("de_dolby.cli.probe_ffmpeg_capabilities",
               return_value=capabilities), \
         patch("de_dolby.cli.probe_temp_directory", side_effect=temp_probe):
        assert _run_main(
            "doctor", "--json", "--profile", "10", "--temp-dir", "/scratch"
        ) == 0
    document = json.loads(capsys.readouterr().out)
    assert captured == ["/scratch"]
    assert document["requested_profile"] == 10
    assert document["temp_directory"]["path"] == "/scratch"
    assert document["profiles"]["10"]["ready"] is True


def test_doctor_requested_unavailable_profile_exits_nonzero(capsys):
    with patch("de_dolby.cli.probe_tool_versions",
               return_value=_doctor_tools()), \
         patch("de_dolby.cli.probe_ffmpeg_capabilities",
               return_value=FfmpegCapabilities(
                   frozenset({"libsvtav1"}), frozenset()
               )), \
         patch("de_dolby.cli.probe_temp_directory",
               return_value=_writable_temp()):
        assert _run_main("doctor", "--json", "--profile", "5") == 1
    assert json.loads(capsys.readouterr().out)["profiles"]["5"]["ready"] is False


def _cli_plan():
    info = FileInfo(
        path="movie.DV.mkv", duration=100.0, overall_bitrate=8_000_000,
        dv_profile=7,
        video_streams=[
            StreamInfo(index=0, codec_type="video", codec_name="hevc")
        ],
    )
    return create_conversion_plan(info, "movie.HDR10.mkv")


def test_manifest_batch_run_then_resume_skips_validated_outputs(tmp_path, capsys):
    inputs = [tmp_path / "one.mkv", tmp_path / "two.mkv"]
    for input_path in inputs:
        input_path.write_bytes(b"input")
    manifest = tmp_path / "batch.json"

    def mocked_convert(input_path, output_path, options):
        Path(output_path).write_bytes(b"validated output")

    arguments = [
        "convert", *(str(path) for path in inputs), "--manifest", str(manifest)
    ]
    with patch("de_dolby.cli.require_tools"), \
         patch("de_dolby.cli.plan_conversion", return_value=_cli_plan()), \
         patch("de_dolby.cli.convert", side_effect=mocked_convert) as convert_mock, \
         patch("sys.argv", ["de-dolby", *arguments]):
        main()
    assert convert_mock.call_count == 2
    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert {entry["status"] for entry in document["entries"].values()} == {"completed"}

    with patch("de_dolby.cli.require_tools"), \
         patch("de_dolby.cli.plan_conversion", return_value=_cli_plan()), \
         patch("de_dolby.cli.convert") as resumed_convert, \
         patch("sys.argv", ["de-dolby", *arguments, "--resume"]):
        main()
    assert resumed_convert.call_count == 0
    assert capsys.readouterr().out.count("Skipping ") == 2


@patch("de_dolby.cli.Path.exists", return_value=True)
@patch("de_dolby.cli.load_settings_config", return_value=SettingsConfig())
@patch("de_dolby.cli.plan_conversion", return_value=_cli_plan())
def test_plan_json_command_renders_versioned_document(
    plan_conversion_mock, config, exists, capsys
):
    assert _run_main("plan", "movie.DV.mkv", "--json") == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 3
    assert document["pipeline"] == "lossless_rpu_strip"
    assert document["input_path"] == "movie.DV.mkv"
    assert document["output_path"] == "movie.HDR10.mkv"
    plan_conversion_mock.assert_called_once()


@patch("de_dolby.cli.Path.exists", return_value=True)
@patch("de_dolby.cli.load_settings_config", return_value=SettingsConfig())
@patch("de_dolby.cli.plan_conversion", return_value=_cli_plan())
def test_plan_human_command_renders_route_and_steps(
    plan_conversion_mock, config, exists, capsys
):
    assert _run_main("plan", "movie.DV.mkv") == 0
    output = capsys.readouterr().out
    assert "Pipeline: lossless_rpu_strip" in output
    assert "Encoder: copy" in output
    assert "1. probe" in output


def test_plan_missing_input_exits_nonzero():
    code, error = _run_main_stderr("plan", "missing-plan-input.mkv")
    assert code == 1
    assert "file not found" in error


@patch("de_dolby.cli.Path.exists", return_value=True)
@patch("de_dolby.cli.probe")
@patch("de_dolby.cli.plan_conversion", return_value=_cli_plan())
@patch("de_dolby.cli.validate_staged_output")
def test_validate_json_command_returns_stable_failure(
    validate, plan_conversion_mock, probe_mock, exists, capsys
):
    validate.return_value = ValidationReport(
        "movie.DV.mkv", "movie.HDR10.mkv",
        (
            ValidationIssue(
                ValidationCode.DOLBY_VISION_PRESENT,
                ValidationSeverity.ERROR,
                "Dolby Vision remains",
            ),
        ),
    )
    assert _run_main(
        "validate", "movie.DV.mkv", "movie.HDR10.mkv", "--json"
    ) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 1
    assert document["valid"] is False
    assert document["issues"][0]["code"] == "dolby_vision_present"


@patch("de_dolby.cli.Path.exists", return_value=True)
@patch("de_dolby.cli.probe")
@patch("de_dolby.cli.plan_conversion", return_value=_cli_plan())
@patch(
    "de_dolby.cli.validate_staged_output",
    return_value=ValidationReport("input.mkv", "output.mkv"),
)
def test_validate_human_command_succeeds(validate, plan_mock, probe_mock, exists, capsys):
    assert _run_main("validate", "input.mkv", "output.mkv") == 0
    assert "Validation: valid" in capsys.readouterr().out


def test_config_show_effective_json_is_secret_safe_and_resolved(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
schema_version = 1
[presets.nvidia]
encoder = "hevc_nvenc"
bitrate = "40M"
""",
        encoding="utf-8",
    )
    assert _run_main(
        "config", "show", "--effective", "--json",
        "--config", str(config_path), "--preset", "nvidia",
    ) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["encoder"] == "hevc_nvenc"
    assert document["bitrate"] == "40M"
    assert set(document) == {
        "encoder", "quality", "crf", "bitrate", "sample_seconds",
        "temp_dir", "timeout_minutes", "unsafe_skip_validation",
    }
