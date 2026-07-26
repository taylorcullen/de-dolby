"""Derive explicit mkvmerge source arguments from a conversion plan."""

from __future__ import annotations

from de_dolby.plan import ConversionPlan, StreamAction


def _track_metadata_args(plan: ConversionPlan) -> list[str]:
    args: list[str] = []
    for stream in plan.stream_map:
        if stream.action not in (StreamAction.COPY, StreamAction.PRESERVE):
            continue
        track_id = str(stream.source_index)
        if stream.language:
            args += ["--language", f"{track_id}:{stream.language}"]
        if stream.title:
            args += ["--track-name", f"{track_id}:{stream.title}"]
        args += [
            "--default-track-flag", f"{track_id}:{int(stream.default)}",
            "--forced-display-flag", f"{track_id}:{int(stream.forced)}",
        ]
    return args


def build_source_remux_args(
    plan: ConversionPlan,
    source_path: str,
    *,
    sample_source: bool,
) -> list[str]:
    """Build mkvmerge options for the non-video source input."""
    args = ["--no-video"]
    if sample_source:
        args += [
            "--audio-tracks", "all",
            "--subtitle-tracks", "all",
            "--no-attachments",
            "--no-chapters",
            "--no-global-tags",
            "--no-track-tags",
            source_path,
        ]
        return args

    audio = [
        str(stream.source_index) for stream in plan.stream_map
        if stream.codec_type == "audio" and stream.action is StreamAction.COPY
    ]
    subtitles = [
        str(stream.source_index) for stream in plan.stream_map
        if stream.codec_type == "subtitle" and stream.action is StreamAction.COPY
    ]
    attachments = [
        str(stream.source_index) for stream in plan.stream_map
        if (
            stream.codec_type == "attachment"
            and stream.action is StreamAction.PRESERVE
        )
    ]
    args += ["--audio-tracks", ",".join(audio)] if audio else ["--no-audio"]
    args += (
        ["--subtitle-tracks", ",".join(subtitles)]
        if subtitles else ["--no-subtitles"]
    )
    args += (
        ["--attachments", ",".join(attachments)]
        if attachments else ["--no-attachments"]
    )
    args += (
        ["--chapters", "all"]
        if plan.stream_policy.chapters is StreamAction.PRESERVE
        else ["--no-chapters"]
    )
    if plan.stream_policy.tags is StreamAction.OMIT:
        args += ["--no-global-tags", "--no-track-tags"]
    args += _track_metadata_args(plan)
    args.append(source_path)
    return args
