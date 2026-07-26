"""Typed post-conversion validation results and stable report schema."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from de_dolby.codecs import ENCODERS
from de_dolby.plan import ConversionPlan, StreamAction
from de_dolby.probe import FileInfo, StreamInfo, probe

VALIDATION_SCHEMA_VERSION = 1


class ValidationSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class ValidationCode(str, Enum):
    OUTPUT_UNREADABLE = "output_unreadable"
    VIDEO_MISSING = "video_missing"
    CODEC_MISMATCH = "codec_mismatch"
    DIMENSIONS_MISMATCH = "dimensions_mismatch"
    DURATION_MISMATCH = "duration_mismatch"
    HDR_TRANSFER_MISSING = "hdr_transfer_missing"
    HDR_PRIMARIES_MISSING = "hdr_primaries_missing"
    HDR_COLORSPACE_MISSING = "hdr_colorspace_missing"
    STATIC_METADATA_MISSING = "static_metadata_missing"
    DOLBY_VISION_PRESENT = "dolby_vision_present"
    STREAM_MISSING = "stream_missing"
    STREAM_METADATA_MISMATCH = "stream_metadata_mismatch"


@dataclass(frozen=True)
class ValidationIssue:
    code: ValidationCode
    severity: ValidationSeverity
    message: str
    expected: str | int | float | None = None
    actual: str | int | float | None = None


@dataclass(frozen=True)
class ValidationReport:
    input_path: str
    output_path: str
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not any(
            issue.severity is ValidationSeverity.ERROR for issue in self.issues
        )

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues
            if issue.severity is ValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues
            if issue.severity is ValidationSeverity.WARNING
        )


def validation_document(report: ValidationReport) -> dict:
    """Serialize a validation report to the stable version 1 contract."""
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "valid": report.valid,
        "input_path": report.input_path,
        "output_path": report.output_path,
        "issues": [
            {
                "code": issue.code.value,
                "severity": issue.severity.value,
                "message": issue.message,
                "expected": issue.expected,
                "actual": issue.actual,
            }
            for issue in report.issues
        ],
    }


def _issue(
    code: ValidationCode,
    message: str,
    expected=None,
    actual=None,
    severity: ValidationSeverity = ValidationSeverity.ERROR,
) -> ValidationIssue:
    return ValidationIssue(code, severity, message, expected, actual)


def _expected_codec(plan: ConversionPlan) -> str:
    return (
        "hevc" if plan.encoder == "copy"
        else ENCODERS[plan.encoder].codec_family
    )


def _matching_streams(
    output: FileInfo, codec_type: str, codec_name: str
) -> list[StreamInfo]:
    groups = {
        "audio": output.audio_streams,
        "subtitle": output.subtitle_streams,
        "attachment": output.attachment_streams,
    }
    return [
        stream for stream in groups.get(codec_type, output.other_streams)
        if stream.codec_name == codec_name
    ]


def compare_output_to_plan(
    input_info: FileInfo,
    output_info: FileInfo,
    plan: ConversionPlan,
    *,
    sample_seconds: int | None = None,
) -> ValidationReport:
    """Compare ffprobe facts with planned conversion invariants."""
    issues: list[ValidationIssue] = []
    if not output_info.video_streams:
        issues.append(_issue(
            ValidationCode.VIDEO_MISSING, "Output contains no video stream"
        ))
        return ValidationReport(
            input_info.path, output_info.path, tuple(issues)
        )

    source_video = input_info.video_streams[0]
    output_video = output_info.video_streams[0]
    expected_codec = _expected_codec(plan)
    if output_video.codec_name not in (
        {expected_codec, "h265"} if expected_codec == "hevc"
        else {expected_codec}
    ):
        issues.append(_issue(
            ValidationCode.CODEC_MISMATCH,
            "Output video codec differs from the plan",
            expected_codec,
            output_video.codec_name,
        ))
    expected_dimensions = f"{source_video.width}x{source_video.height}"
    actual_dimensions = f"{output_video.width}x{output_video.height}"
    if (
        source_video.width != output_video.width
        or source_video.height != output_video.height
    ):
        issues.append(_issue(
            ValidationCode.DIMENSIONS_MISMATCH,
            "Output dimensions differ from the input",
            expected_dimensions,
            actual_dimensions,
        ))

    expected_duration = (
        min(float(sample_seconds), input_info.duration)
        if sample_seconds is not None and input_info.duration is not None
        else input_info.duration
    )
    tolerance = 0.5 if sample_seconds is not None else (
        max(1.0, expected_duration * 0.001)
        if expected_duration is not None else None
    )
    if (
        expected_duration is not None and output_info.duration is not None
        and abs(output_info.duration - expected_duration) > tolerance
    ):
        issues.append(_issue(
            ValidationCode.DURATION_MISMATCH,
            f"Output duration differs by more than {tolerance:.3f} seconds",
            expected_duration,
            output_info.duration,
        ))

    if output_video.color_transfer != "smpte2084":
        issues.append(_issue(
            ValidationCode.HDR_TRANSFER_MISSING,
            "Output is not signalled with the PQ transfer function",
            "smpte2084",
            output_video.color_transfer,
        ))
    if output_video.color_primaries != "bt2020":
        issues.append(_issue(
            ValidationCode.HDR_PRIMARIES_MISSING,
            "Output is not signalled with BT.2020 primaries",
            "bt2020",
            output_video.color_primaries,
        ))
    if output_video.color_space != "bt2020nc":
        issues.append(_issue(
            ValidationCode.HDR_COLORSPACE_MISSING,
            "Output is not signalled with BT.2020 non-constant luminance",
            "bt2020nc",
            output_video.color_space,
        ))
    if not output_info.master_display or not output_info.content_light_level:
        issues.append(_issue(
            ValidationCode.STATIC_METADATA_MISSING,
            "Output is missing HDR10 static metadata",
            "master display and content light metadata",
            None,
        ))
    if output_info.dv_profile is not None:
        issues.append(_issue(
            ValidationCode.DOLBY_VISION_PRESENT,
            "Dolby Vision signalling remains in the output",
            "absent",
            output_info.dv_profile,
        ))

    for planned in plan.stream_map:
        if planned.action not in (StreamAction.COPY, StreamAction.PRESERVE):
            continue
        candidates = _matching_streams(
            output_info, planned.codec_type, planned.codec_name
        )
        if not candidates:
            issues.append(_issue(
                ValidationCode.STREAM_MISSING,
                f"Planned {planned.codec_type} stream is missing",
                f"{planned.codec_name} #{planned.source_index}",
                None,
            ))
            continue
        if planned.codec_type == "attachment":
            continue
        match = next((
            stream for stream in candidates
            if stream.language == planned.language and stream.title == planned.title
        ), candidates[0])
        if (
            match.language != planned.language
            or match.title != planned.title
            or match.default != planned.default
            or match.forced != planned.forced
        ):
            issues.append(_issue(
                ValidationCode.STREAM_METADATA_MISMATCH,
                f"Metadata differs for planned {planned.codec_type} stream",
                (
                    f"{planned.language}/{planned.title}/"
                    f"default={planned.default}/forced={planned.forced}"
                ),
                (
                    f"{match.language}/{match.title}/"
                    f"default={match.default}/forced={match.forced}"
                ),
            ))

    if (
        plan.stream_policy.chapters is StreamAction.PRESERVE
        and len(output_info.chapters) < len(input_info.chapters)
    ):
        issues.append(_issue(
            ValidationCode.STREAM_MISSING,
            "Planned chapters are missing",
            len(input_info.chapters),
            len(output_info.chapters),
        ))
    return ValidationReport(input_info.path, output_info.path, tuple(issues))


def validate_staged_output(
    input_info: FileInfo,
    output_path: str,
    plan: ConversionPlan,
    *,
    sample_seconds: int | None = None,
) -> ValidationReport:
    """Probe an output and compare it, reporting unreadability as data."""
    try:
        output_info = probe(output_path)
    except (OSError, RuntimeError, ValueError) as exc:
        return ValidationReport(
            input_info.path,
            output_path,
            (
                _issue(
                    ValidationCode.OUTPUT_UNREADABLE,
                    f"Output could not be probed: {exc}",
                    expected="readable media",
                    actual=None,
                ),
            ),
        )
    return compare_output_to_plan(
        input_info, output_info, plan, sample_seconds=sample_seconds
    )
