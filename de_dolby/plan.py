"""Immutable conversion plans and pure pipeline-selection policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import AbstractSet, Callable

from de_dolby.codecs import ENCODERS, InputCodec, get_input_codec
from de_dolby.probe import FileInfo

PLAN_SCHEMA_VERSION = 3


class PipelineKind(str, Enum):
    LOSSLESS_RPU_STRIP = "lossless_rpu_strip"
    REENCODE = "reencode"


class MetadataSource(str, Enum):
    DOVI_RPU = "dovi_rpu"
    FFPROBE = "ffprobe"


class StreamAction(str, Enum):
    COPY = "copy"
    PRESERVE = "preserve"
    OMIT = "omit"
    REPLACE = "replace"


@dataclass(frozen=True)
class PlannedStream:
    source_index: int
    codec_type: str
    codec_name: str
    action: StreamAction
    language: str | None
    title: str | None
    default: bool
    forced: bool
    reason: str | None = None


@dataclass(frozen=True)
class StreamPolicy:
    """Treatment of non-video streams and container-level content."""

    audio: StreamAction
    subtitles: StreamAction
    attachments: StreamAction
    chapters: StreamAction
    tags: StreamAction


@dataclass(frozen=True)
class ConversionPlan:
    """A complete, immutable description of one conversion."""

    input_path: str
    output_path: str
    profile: int
    input_codec: str
    pipeline: PipelineKind
    encoder: str
    fallback_reason: str | None
    metadata_source: MetadataSource
    stream_policy: StreamPolicy
    stream_map: tuple[PlannedStream, ...]
    warnings: tuple[str, ...]
    estimated_temp_bytes: int | None
    steps: tuple[str, ...]
    quality: str = "balanced"
    crf: int | None = None
    bitrate: str | None = None
    sample_seconds: int | None = None
    temp_dir: str | None = None
    unsafe_skip_validation: bool = False


EncoderAvailable = Callable[[str], bool]


def select_encoder(
    input_codec: InputCodec,
    requested_encoder: str,
    is_available: EncoderAvailable,
) -> tuple[str, str | None]:
    """Purely select an encoder and explain an automatic fallback."""
    if requested_encoder != "auto":
        return requested_encoder, None

    priority = input_codec.auto_encoder_priority()
    for index, name in enumerate(priority):
        if is_available(name):
            if index == 0:
                return name, "selected first available encoder in auto priority"
            skipped = ", ".join(priority[:index])
            return name, f"higher-priority encoders unavailable: {skipped}"

    raise RuntimeError(
        f"No supported {input_codec.name} encoder is available in ffmpeg"
    )


def _estimated_temp_bytes(info: FileInfo, sample_seconds: int | None) -> int | None:
    if not info.overall_bitrate or not info.duration:
        return None
    duration = sample_seconds or info.duration
    return int((info.overall_bitrate * duration / 8) * 3)


def _validate_profile_codec(profile: int, codec_name: str) -> None:
    supported = {
        5: {"hevc", "h265"},
        7: {"hevc", "h265"},
        8: {"hevc", "h265"},
        10: {"av1"},
    }
    if profile not in supported:
        raise RuntimeError(f"Unsupported Dolby Vision profile: {profile}")
    if codec_name not in supported[profile]:
        raise RuntimeError(
            f"Dolby Vision profile {profile} with {codec_name} video is unsupported"
        )


def create_conversion_plan(
    info: FileInfo,
    output_path: str,
    *,
    requested_encoder: str = "auto",
    available_encoders: AbstractSet[str] = frozenset(),
    sample_seconds: int | None = None,
    quality: str = "balanced",
    crf: int | None = None,
    bitrate: str | None = None,
    temp_dir: str | None = None,
    unsafe_skip_validation: bool = False,
) -> ConversionPlan:
    """Create a plan from already-probed input facts without filesystem writes."""
    if not info.video_streams:
        raise RuntimeError("No video streams found in input file")
    if info.dv_profile is None:
        raise RuntimeError("No Dolby Vision metadata detected in input file")

    codec_name = info.video_streams[0].codec_name
    _validate_profile_codec(info.dv_profile, codec_name)
    input_codec = get_input_codec(codec_name)
    codec_family = "hevc" if codec_name == "h265" else codec_name

    fallback_reason: str | None
    if info.dv_profile in (7, 8) and requested_encoder in ("auto", "copy"):
        pipeline = PipelineKind.LOSSLESS_RPU_STRIP
        encoder = "copy"
        fallback_reason = (
            "lossless route selected for HEVC profile 7/8"
            if requested_encoder == "auto" else None
        )
    else:
        pipeline = PipelineKind.REENCODE
        encoder, fallback_reason = select_encoder(
            input_codec, requested_encoder, available_encoders.__contains__
        )
        if encoder == "copy":
            raise RuntimeError(
                f"Encoder copy is incompatible with Dolby Vision profile "
                f"{info.dv_profile} re-encoding"
            )
        selected = ENCODERS.get(encoder)
        if selected is None:
            raise RuntimeError(f"Unknown encoder: {encoder}")
        if selected.codec_family != codec_family:
            raise RuntimeError(
                f"Encoder {encoder} produces {selected.codec_family}, "
                f"incompatible with {codec_family} input"
            )
        if requested_encoder != "auto" and encoder not in available_encoders:
            raise RuntimeError(f"Encoder {encoder} is not available in ffmpeg")

    sampled = sample_seconds is not None
    stream_policy = StreamPolicy(
        audio=StreamAction.COPY,
        subtitles=StreamAction.COPY,
        attachments=StreamAction.OMIT if sampled else StreamAction.PRESERVE,
        chapters=StreamAction.OMIT if sampled else StreamAction.PRESERVE,
        tags=StreamAction.OMIT if sampled else StreamAction.PRESERVE,
    )
    stream_map: list[PlannedStream] = []
    warnings: list[str] = []
    all_streams = (
        info.video_streams + info.audio_streams + info.subtitle_streams
        + info.attachment_streams + info.other_streams
    )
    for stream in all_streams:
        reason = None
        if stream.codec_type == "video":
            action = (
                StreamAction.REPLACE
                if stream is info.video_streams[0] else StreamAction.OMIT
            )
            if action is StreamAction.OMIT:
                reason = "additional video tracks are not supported"
        elif stream.codec_type == "audio":
            action = StreamAction.COPY
        elif stream.codec_type == "subtitle":
            action = StreamAction.COPY
        elif stream.codec_type == "attachment":
            action = stream_policy.attachments
            if sampled:
                reason = "attachments are omitted from sample conversions"
        else:
            action = StreamAction.OMIT
            reason = f"unsupported stream type: {stream.codec_type or 'unknown'}"
        if action is StreamAction.OMIT and reason:
            warnings.append(f"Stream #{stream.index}: {reason}")
        stream_map.append(PlannedStream(
            source_index=stream.index,
            codec_type=stream.codec_type,
            codec_name=stream.codec_name,
            action=action,
            language=stream.language,
            title=stream.title,
            default=stream.default,
            forced=stream.forced,
            reason=reason,
        ))
    if sampled and info.chapters:
        warnings.append("Chapters are omitted from sample conversions")
    if sampled and info.tags:
        warnings.append("Container tags are omitted from sample conversions")
    if pipeline is PipelineKind.LOSSLESS_RPU_STRIP:
        steps = (
            "probe", "extract_video", "extract_rpu", "parse_metadata",
            "strip_rpu", "remux", "cleanup",
        )
    else:
        steps = (
            "probe", "extract_video", "extract_rpu", "parse_metadata",
            "encode", "remux", "cleanup",
        )

    return ConversionPlan(
        input_path=info.path,
        output_path=output_path,
        profile=info.dv_profile,
        input_codec=codec_name,
        pipeline=pipeline,
        encoder=encoder,
        fallback_reason=fallback_reason,
        metadata_source=(
            MetadataSource.DOVI_RPU
            if input_codec.supports_dovi_tool else MetadataSource.FFPROBE
        ),
        stream_policy=stream_policy,
        stream_map=tuple(stream_map),
        warnings=tuple(warnings),
        estimated_temp_bytes=_estimated_temp_bytes(info, sample_seconds),
        steps=steps,
        quality=quality,
        crf=crf,
        bitrate=bitrate,
        sample_seconds=sample_seconds,
        temp_dir=temp_dir,
        unsafe_skip_validation=unsafe_skip_validation,
    )


def plan_document(plan: ConversionPlan) -> dict:
    """Serialize a plan to the stable version 1 JSON contract."""
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "input_path": plan.input_path,
        "output_path": plan.output_path,
        "profile": plan.profile,
        "input_codec": plan.input_codec,
        "pipeline": plan.pipeline.value,
        "encoder": plan.encoder,
        "settings": {
            "quality": plan.quality,
            "crf": plan.crf,
            "bitrate": plan.bitrate,
            "sample_seconds": plan.sample_seconds,
            "temp_dir": plan.temp_dir,
            "unsafe_skip_validation": plan.unsafe_skip_validation,
        },
        "fallback_reason": plan.fallback_reason,
        "metadata_source": plan.metadata_source.value,
        "stream_policy": {
            "audio": plan.stream_policy.audio.value,
            "subtitles": plan.stream_policy.subtitles.value,
            "attachments": plan.stream_policy.attachments.value,
            "chapters": plan.stream_policy.chapters.value,
            "tags": plan.stream_policy.tags.value,
        },
        "stream_map": [
            {
                "source_index": stream.source_index,
                "codec_type": stream.codec_type,
                "codec_name": stream.codec_name,
                "action": stream.action.value,
                "language": stream.language,
                "title": stream.title,
                "default": stream.default,
                "forced": stream.forced,
                "reason": stream.reason,
            }
            for stream in plan.stream_map
        ],
        "warnings": list(plan.warnings),
        "estimated_temp_bytes": plan.estimated_temp_bytes,
        "steps": list(plan.steps),
    }
