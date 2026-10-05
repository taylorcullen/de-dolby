from array import array
import json
from unittest.mock import patch

import pytest

from de_dolby.cli import derive_output_name
from de_dolby.hdr10plus import FRAME_BYTES, frame_metadata, pq_to_nits, prepare_video, verify_metadata
from de_dolby.plan import create_conversion_plan, plan_document
from de_dolby.probe import FileInfo, StreamInfo
from de_dolby.settings import SettingsError, merge_settings, settings_from_mapping


def info(profile=8, codec="hevc"):
    return FileInfo(
        path="source.mkv", dv_profile=profile, has_hdr10=True,
        duration=60, overall_bitrate=8_000_000,
        video_streams=[StreamInfo(index=0, codec_type="video", codec_name=codec, frame_rate="24/1")],
    )


@pytest.mark.parametrize("value,expected", [(0, 0), (1, 10000), (0.5080784215, 100)])
def test_pq_eotf_reference_values(value, expected):
    assert pq_to_nits(value) == pytest.approx(expected, abs=0.0001)


@pytest.mark.parametrize("code,expected", [(0, 0), (65535, 100000)])
def test_constant_frame_has_measured_profile_a_statistics(code, expected):
    frame = array("H", [code] * (FRAME_BYTES // 2)).tobytes()
    data = frame_metadata(frame, 3)
    assert data["SequenceFrameIndex"] == 3
    assert data["TargetedSystemDisplayMaximumLuminance"] == 0
    assert "BezierCurveData" not in data
    stats = data["LuminanceParameters"]
    assert stats["MaxScl"] == [expected] * 3
    assert stats["AverageRGB"] == expected
    distribution = stats["LuminanceDistributions"]["DistributionValues"]
    assert distribution[0] == distribution[1] == distribution[-1] == expected
    assert distribution[2] == (100 if code == 0 else 0)


def test_partial_analysis_frame_fails():
    with pytest.raises(RuntimeError, match="Incomplete"):
        frame_metadata(b"\0", 0)


@pytest.mark.parametrize("mode", ["preserve", "generate"])
def test_hdr10plus_mode_is_planned_and_fingerprinted(mode):
    plan = create_conversion_plan(info(), "out.mkv", hdr10plus=mode)
    assert plan.encoder == "copy"
    assert plan.steps[-3:] == ("hdr10plus", "remux", "cleanup")
    assert plan_document(plan)["settings"]["hdr10plus"] == mode
    if mode == "generate":
        assert plan.warnings
        assert plan.estimated_temp_bytes > 180_000_000


def test_av1_hdr10plus_is_rejected():
    with pytest.raises(RuntimeError, match="HEVC only"):
        create_conversion_plan(info(10, "av1"), "out.mkv", hdr10plus="generate", available_encoders={"libsvtav1"})


def test_preserve_reencode_is_rejected():
    with pytest.raises(RuntimeError, match="lossless"):
        create_conversion_plan(info(5), "out.mkv", hdr10plus="preserve", available_encoders={"libx265"})


def test_non_hdr10_base_layer_is_rejected():
    source = info()
    source.has_hdr10 = False
    with pytest.raises(RuntimeError, match="base layer"):
        create_conversion_plan(source, "out.mkv", hdr10plus="generate")


@pytest.mark.parametrize("name,expected", [("movie.DV.mkv", "movie.HDR10Plus.mkv"), ("movie.mkv", "movie.HDR10Plus.mkv"), ("movie", "movie.HDR10Plus")])
def test_hdr10plus_output_names(name, expected):
    assert derive_output_name(name, "generate") == expected


def test_hdr10plus_settings_are_validated():
    layer = settings_from_mapping({"hdr10plus": "generate"}, source="preset")
    assert merge_settings(("preset", layer)).hdr10plus == "generate"
    with pytest.raises(SettingsError, match="hdr10plus"):
        settings_from_mapping({"hdr10plus": "fake"}, source="preset")


def test_cli_passes_hdr10plus_mode_and_derives_filename(tmp_path, monkeypatch):
    import sys
    from de_dolby.cli import main
    from de_dolby.settings import SettingsConfig

    source = tmp_path / "movie.DV.mkv"
    source.write_bytes(b"mock input")
    monkeypatch.setattr(sys, "argv", ["de-dolby", "convert", str(source), "--hdr10plus", "generate"])
    with patch("de_dolby.cli.require_tools"), patch("de_dolby.cli.load_settings_config", return_value=SettingsConfig()), patch("de_dolby.cli.convert") as convert:
        main()
    assert convert.call_args.args[1] == str(tmp_path / "movie.HDR10Plus.mkv")
    assert convert.call_args.args[2].hdr10plus == "generate"


def test_missing_hdr10plus_tool_fails_before_conversion():
    from de_dolby.pipeline import ConvertOptions, _plan_from_info
    with patch("de_dolby.tools.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="requires hdr10plus_tool"):
            _plan_from_info(info(), "out.mkv", ConvertOptions(hdr10plus="generate"))


def test_probe_reports_hdr10plus_side_data():
    from de_dolby.probe import _extract_side_data
    source = info()
    _extract_side_data([{"side_data_type": "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"}], source)
    assert source.has_hdr10plus


def test_missing_metadata_cannot_use_stale_json(tmp_path):
    (tmp_path / "hdr10plus-verified.json").write_text('{"SceneInfo":[{}]}')
    with patch("de_dolby.hdr10plus.run_hdr10plus_tool"):
        with pytest.raises(RuntimeError, match="No HDR10\\+"):
            verify_metadata("source.hevc", str(tmp_path))


def test_preserve_verifies_without_injecting_or_generating(tmp_path):
    with patch("de_dolby.hdr10plus.verify_metadata") as verify, patch("de_dolby.hdr10plus.generate_metadata") as generate:
        assert prepare_video("clean.hevc", str(tmp_path), "preserve") == "clean.hevc"
    verify.assert_called_once()
    generate.assert_not_called()


def test_generate_rejects_frame_count_mismatch(tmp_path):
    metadata = tmp_path / "generated.json"
    metadata.write_text(json.dumps({"SceneInfo": [{}, {}]}))
    with patch("de_dolby.hdr10plus.generate_metadata", return_value=str(metadata)), patch("de_dolby.hdr10plus.run_hdr10plus_tool"), patch("de_dolby.hdr10plus.verify_metadata", return_value={"SceneInfo": [{}]}):
        with pytest.raises(RuntimeError, match="frame count"):
            prepare_video("clean.hevc", str(tmp_path), "generate")


def test_hdr10plus_verification_failure_blocks_publication(tmp_path):
    from de_dolby.pipeline import ConvertOptions, execute_conversion_plan

    source = info()
    destination = tmp_path / "out.mkv"
    destination.write_bytes(b"existing output")
    plan = create_conversion_plan(source, str(destination), hdr10plus="generate")

    def stage(info, codec, output, options, **kwargs):
        from pathlib import Path
        Path(output).write_bytes(b"unverified replacement")

    with patch("de_dolby.pipeline._run_lossless", side_effect=stage), patch("de_dolby.pipeline.display_banner"), patch("de_dolby.pipeline._check_disk_space"), patch("de_dolby.pipeline.verify_metadata", side_effect=RuntimeError("invalid HDR10+")):
        with pytest.raises(RuntimeError, match="invalid HDR10"):
            execute_conversion_plan(plan, source, ConvertOptions(hdr10plus="generate", force=True, unsafe_skip_validation=True))
    assert destination.read_bytes() == b"existing output"
    assert list(tmp_path.glob(".*.staging.mkv")) == []


def test_validation_reports_hdr10plus_failure_as_data(tmp_path):
    from de_dolby.validation import ValidationCode, validate_staged_output
    source = info()
    plan = create_conversion_plan(source, "out.mkv", hdr10plus="generate")
    with patch("de_dolby.validation.probe", return_value=source), patch("de_dolby.hdr10plus.verify_metadata", side_effect=RuntimeError("missing metadata")):
        report = validate_staged_output(source, "out.mkv", plan)
    assert ValidationCode.HDR10PLUS_INVALID in {issue.code for issue in report.errors}
