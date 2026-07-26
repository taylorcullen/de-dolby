"""Tests for immutable conversion planning and pure route selection."""

from dataclasses import FrozenInstanceError

import pytest

from de_dolby.plan import (
    PLAN_SCHEMA_VERSION,
    MetadataSource,
    PipelineKind,
    StreamAction,
    create_conversion_plan,
    plan_document,
)
from de_dolby.probe import FileInfo, StreamInfo


def media_info(profile: int, codec: str) -> FileInfo:
    return FileInfo(
        path=f"profile-{profile}.mkv",
        duration=100.0,
        overall_bitrate=8_000_000,
        dv_profile=profile,
        video_streams=[
            StreamInfo(index=0, codec_type="video", codec_name=codec)
        ],
    )


@pytest.mark.parametrize("profile", [7, 8])
def test_profile_7_and_8_auto_plan_lossless(profile):
    plan = create_conversion_plan(
        media_info(profile, "hevc"), "output.mkv",
        available_encoders={"hevc_nvenc", "libx265"},
    )
    assert plan.pipeline is PipelineKind.LOSSLESS_RPU_STRIP
    assert plan.encoder == "copy"
    assert plan.metadata_source is MetadataSource.DOVI_RPU
    assert plan.steps == (
        "probe", "extract_video", "extract_rpu", "parse_metadata",
        "strip_rpu", "remux", "cleanup",
    )


def test_profile_5_plan_reencodes_with_libplacebo_route():
    plan = create_conversion_plan(
        media_info(5, "hevc"), "output.mkv",
        available_encoders={"libx265"},
    )
    assert plan.pipeline is PipelineKind.REENCODE
    assert plan.encoder == "libx265"
    assert plan.metadata_source is MetadataSource.DOVI_RPU
    assert "higher-priority encoders unavailable" in plan.fallback_reason


def test_auto_selection_explains_first_priority_encoder():
    plan = create_conversion_plan(
        media_info(5, "hevc"), "output.mkv",
        available_encoders={"hevc_amf", "libx265"},
    )
    assert plan.encoder == "hevc_amf"
    assert plan.fallback_reason == (
        "selected first available encoder in auto priority"
    )


def test_auto_selection_fails_when_no_supported_encoder_is_available():
    with pytest.raises(RuntimeError, match="No supported HEVC encoder"):
        create_conversion_plan(
            media_info(5, "hevc"), "output.mkv", available_encoders=set()
        )


def test_profile_10_av1_plan_uses_probe_metadata():
    plan = create_conversion_plan(
        media_info(10, "av1"), "output.mkv",
        available_encoders={"libsvtav1"},
    )
    assert plan.pipeline is PipelineKind.REENCODE
    assert plan.encoder == "libsvtav1"
    assert plan.metadata_source is MetadataSource.FFPROBE
    assert "extract_rpu" in plan.steps


def test_plan_contains_stream_policy_and_space_estimate():
    plan = create_conversion_plan(
        media_info(5, "hevc"), "output.mkv",
        available_encoders={"libx265"}, sample_seconds=10,
    )
    assert plan.estimated_temp_bytes == 30_000_000
    assert plan.stream_policy.audio is StreamAction.COPY
    assert plan.stream_policy.subtitles is StreamAction.COPY
    assert plan.stream_policy.attachments is StreamAction.OMIT
    assert plan.stream_policy.chapters is StreamAction.OMIT
    assert plan.stream_policy.tags is StreamAction.OMIT


def test_plan_is_immutable():
    plan = create_conversion_plan(
        media_info(7, "hevc"), "output.mkv", available_encoders=set()
    )
    with pytest.raises(FrozenInstanceError):
        plan.encoder = "libx265"


@pytest.mark.parametrize(
    ("profile", "codec"),
    [(5, "av1"), (7, "av1"), (8, "av1"), (10, "hevc")],
)
def test_unsupported_profile_codec_combinations_fail(profile, codec):
    with pytest.raises(RuntimeError, match="unsupported"):
        create_conversion_plan(
            media_info(profile, codec), "output.mkv",
            available_encoders={"libx265", "libsvtav1"},
        )


def test_explicit_incompatible_encoder_fails_during_planning():
    with pytest.raises(RuntimeError, match="incompatible"):
        create_conversion_plan(
            media_info(5, "hevc"), "output.mkv",
            requested_encoder="libsvtav1",
        )


def test_explicit_compatible_but_unavailable_encoder_fails_during_planning():
    with pytest.raises(RuntimeError, match="not available"):
        create_conversion_plan(
            media_info(5, "hevc"), "output.mkv",
            requested_encoder="libx265", available_encoders=set(),
        )


def test_explicit_available_encoder_has_no_fallback_reason():
    plan = create_conversion_plan(
        media_info(5, "hevc"), "output.mkv",
        requested_encoder="libx265", available_encoders={"libx265"},
    )
    assert plan.encoder == "libx265"
    assert plan.fallback_reason is None


def test_plan_rejects_input_without_video():
    with pytest.raises(RuntimeError, match="No video streams"):
        create_conversion_plan(FileInfo(path="audio.mkv"), "output.mkv")


def test_plan_rejects_input_without_dolby_vision():
    info = media_info(7, "hevc")
    info.dv_profile = None
    with pytest.raises(RuntimeError, match="No Dolby Vision"):
        create_conversion_plan(info, "output.mkv")


def test_planning_does_not_touch_process_or_filesystem_boundaries(monkeypatch):
    monkeypatch.setattr("de_dolby.tools._run", lambda *args, **kwargs: pytest.fail())
    monkeypatch.setattr("tempfile.mkdtemp", lambda *args, **kwargs: pytest.fail())
    plan = create_conversion_plan(
        media_info(7, "hevc"), "output.mkv", available_encoders=set()
    )
    assert plan.output_path == "output.mkv"


def test_plan_json_schema_is_explicit_and_stable():
    plan = create_conversion_plan(
        media_info(7, "hevc"), "output.mkv", available_encoders=set()
    )
    assert plan_document(plan) == {
        "schema_version": PLAN_SCHEMA_VERSION,
        "input_path": "profile-7.mkv",
        "output_path": "output.mkv",
        "profile": 7,
        "input_codec": "hevc",
        "pipeline": "lossless_rpu_strip",
        "encoder": "copy",
        "settings": {
            "quality": "balanced",
            "crf": None,
            "bitrate": None,
            "sample_seconds": None,
            "temp_dir": None,
            "unsafe_skip_validation": False,
        },
        "fallback_reason": "lossless route selected for HEVC profile 7/8",
        "metadata_source": "dovi_rpu",
        "stream_policy": {
            "audio": "copy",
            "subtitles": "copy",
            "attachments": "preserve",
            "chapters": "preserve",
            "tags": "preserve",
        },
        "stream_map": [
            {
                "source_index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "action": "replace",
                "language": None,
                "title": None,
                "default": False,
                "forced": False,
                "reason": None,
            }
        ],
        "warnings": [],
        "estimated_temp_bytes": 300_000_000,
        "steps": [
            "probe", "extract_video", "extract_rpu", "parse_metadata",
            "strip_rpu", "remux", "cleanup",
        ],
    }


def fidelity_info() -> FileInfo:
    info = media_info(5, "hevc")
    info.audio_streams = [
        StreamInfo(
            index=1, codec_type="audio", codec_name="eac3",
            language="eng", title="Main", default=True,
        )
    ]
    info.subtitle_streams = [
        StreamInfo(
            index=2, codec_type="subtitle", codec_name="subrip",
            language="fra", title="Signs", forced=True,
        )
    ]
    info.attachment_streams = [
        StreamInfo(
            index=3, codec_type="attachment", codec_name="ttf",
            filename="font.ttf",
        )
    ]
    info.tags = {"title": "Feature"}
    return info


def test_full_plan_preserves_non_video_contract():
    plan = create_conversion_plan(
        fidelity_info(), "output.mkv", available_encoders={"libx265"}
    )
    mapped = {stream.source_index: stream for stream in plan.stream_map}
    assert mapped[0].action is StreamAction.REPLACE
    assert mapped[1].action is StreamAction.COPY
    assert mapped[1].language == "eng"
    assert mapped[1].default is True
    assert mapped[2].action is StreamAction.COPY
    assert mapped[2].forced is True
    assert mapped[3].action is StreamAction.PRESERVE
    assert plan.stream_policy.tags is StreamAction.PRESERVE
    assert plan.warnings == ()


def test_sample_plan_explicitly_omits_container_content():
    plan = create_conversion_plan(
        fidelity_info(), "output.mkv",
        available_encoders={"libx265"}, sample_seconds=30,
    )
    mapped = {stream.source_index: stream for stream in plan.stream_map}
    assert mapped[1].action is StreamAction.COPY
    assert mapped[2].action is StreamAction.COPY
    assert mapped[3].action is StreamAction.OMIT
    assert "sample conversions" in mapped[3].reason
    assert plan.stream_policy.chapters is StreamAction.OMIT
    assert plan.stream_policy.tags is StreamAction.OMIT
    assert any(
        warning.startswith("Container tags are omitted")
        for warning in plan.warnings
    )
