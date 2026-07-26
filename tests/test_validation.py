"""Tests for typed validation results and report serialization."""

from dataclasses import FrozenInstanceError

import pytest

from de_dolby.validation import (
    VALIDATION_SCHEMA_VERSION,
    ValidationCode,
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
    compare_output_to_plan,
    validation_document,
)
from de_dolby.plan import create_conversion_plan
from de_dolby.probe import ChapterInfo, FileInfo, StreamInfo


def test_report_is_valid_with_no_errors():
    warning = ValidationIssue(
        ValidationCode.STATIC_METADATA_MISSING,
        ValidationSeverity.WARNING,
        "Static metadata was not detected",
    )
    report = ValidationReport("input.mkv", "output.mkv", (warning,))
    assert report.valid is True
    assert report.errors == ()
    assert report.warnings == (warning,)


def test_error_makes_report_invalid():
    error = ValidationIssue(
        ValidationCode.DOLBY_VISION_PRESENT,
        ValidationSeverity.ERROR,
        "Dolby Vision signalling remains",
        expected="absent",
        actual="profile 7",
    )
    report = ValidationReport("input.mkv", "output.mkv", (error,))
    assert report.valid is False
    assert report.errors == (error,)


def test_report_and_issues_are_immutable():
    report = ValidationReport("input.mkv", "output.mkv")
    with pytest.raises(FrozenInstanceError):
        report.output_path = "changed.mkv"


def test_validation_json_schema_is_explicit_and_stable():
    report = ValidationReport(
        "input.mkv",
        "output.mkv",
        (
            ValidationIssue(
                ValidationCode.DURATION_MISMATCH,
                ValidationSeverity.ERROR,
                "Duration differs beyond tolerance",
                expected=100.0,
                actual=95.0,
            ),
        ),
    )
    assert validation_document(report) == {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "valid": False,
        "input_path": "input.mkv",
        "output_path": "output.mkv",
        "issues": [
            {
                "code": "duration_mismatch",
                "severity": "error",
                "message": "Duration differs beyond tolerance",
                "expected": 100.0,
                "actual": 95.0,
            }
        ],
    }


def test_validation_codes_are_unique_stable_strings():
    values = [code.value for code in ValidationCode]
    assert len(values) == len(set(values))
    assert all(value == value.lower() and " " not in value for value in values)


def fixture_info(path="input.mkv"):
    return FileInfo(
        path=path, duration=100.0, overall_bitrate=8_000_000,
        dv_profile=5 if path == "input.mkv" else None,
        master_display="G(...)",
        content_light_level="1000,400",
        video_streams=[
            StreamInfo(
                index=0, codec_type="video", codec_name="hevc",
                width=3840, height=2160, color_transfer="smpte2084",
                color_primaries="bt2020", color_space="bt2020nc",
            )
        ],
        audio_streams=[
            StreamInfo(
                index=1, codec_type="audio", codec_name="eac3",
                language="eng", title="Main", default=True,
            )
        ],
        subtitle_streams=[
            StreamInfo(
                index=2, codec_type="subtitle", codec_name="subrip",
                language="fra", forced=True,
            )
        ],
        chapters=[ChapterInfo(id=0, title="Opening")],
    )


def fixture_plan():
    return create_conversion_plan(
        fixture_info(), "output.mkv", available_encoders={"libx265"}
    )


def test_valid_output_passes_every_invariant():
    report = compare_output_to_plan(
        fixture_info(), fixture_info("output.mkv"), fixture_plan()
    )
    assert report.valid is True
    assert report.issues == ()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda output: output.video_streams.clear(), ValidationCode.VIDEO_MISSING),
        (lambda output: setattr(output.video_streams[0], "codec_name", "av1"),
         ValidationCode.CODEC_MISMATCH),
        (lambda output: setattr(output.video_streams[0], "width", 1920),
         ValidationCode.DIMENSIONS_MISMATCH),
        (lambda output: setattr(output, "duration", 90.0),
         ValidationCode.DURATION_MISMATCH),
        (lambda output: setattr(output.video_streams[0], "color_transfer", None),
         ValidationCode.HDR_TRANSFER_MISSING),
        (lambda output: setattr(output.video_streams[0], "color_primaries", None),
         ValidationCode.HDR_PRIMARIES_MISSING),
        (lambda output: setattr(output.video_streams[0], "color_space", None),
         ValidationCode.HDR_COLORSPACE_MISSING),
        (lambda output: setattr(output, "master_display", None),
         ValidationCode.STATIC_METADATA_MISSING),
        (lambda output: setattr(output, "dv_profile", 7),
         ValidationCode.DOLBY_VISION_PRESENT),
        (lambda output: output.audio_streams.clear(),
         ValidationCode.STREAM_MISSING),
        (lambda output: setattr(output.audio_streams[0], "language", "deu"),
         ValidationCode.STREAM_METADATA_MISMATCH),
        (lambda output: output.chapters.clear(),
         ValidationCode.STREAM_MISSING),
    ],
)
def test_negative_fixture_matrix_reports_stable_code(mutate, code):
    output = fixture_info("output.mkv")
    mutate(output)
    report = compare_output_to_plan(fixture_info(), output, fixture_plan())
    assert code in {issue.code for issue in report.errors}


def test_sample_duration_uses_stricter_absolute_tolerance():
    output = fixture_info("sample.mkv")
    output.duration = 30.6
    report = compare_output_to_plan(
        fixture_info(), output, fixture_plan(), sample_seconds=30
    )
    assert ValidationCode.DURATION_MISMATCH in {
        issue.code for issue in report.errors
    }


def test_full_duration_allows_one_second_tolerance():
    output = fixture_info("output.mkv")
    output.duration = 100.9
    report = compare_output_to_plan(
        fixture_info(), output, fixture_plan()
    )
    assert ValidationCode.DURATION_MISMATCH not in {
        issue.code for issue in report.errors
    }


@pytest.mark.parametrize("error", [OSError("missing"), ValueError("bad json")])
def test_unreadable_output_becomes_stable_report_issue(error):
    from unittest.mock import patch
    from de_dolby.validation import validate_staged_output
    with patch("de_dolby.validation.probe", side_effect=error):
        report = validate_staged_output(
            fixture_info(), "broken.mkv", fixture_plan()
        )
    assert report.valid is False
    assert report.errors[0].code is ValidationCode.OUTPUT_UNREADABLE
