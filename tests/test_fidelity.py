"""Command-level tests for explicit stream preservation."""

from de_dolby.fidelity import build_source_remux_args
from de_dolby.plan import create_conversion_plan
from de_dolby.probe import ChapterInfo, FileInfo, StreamInfo


def fidelity_info():
    return FileInfo(
        path="source.mkv", duration=100.0, overall_bitrate=8_000_000,
        dv_profile=5,
        video_streams=[
            StreamInfo(index=0, codec_type="video", codec_name="hevc")
        ],
        audio_streams=[
            StreamInfo(
                index=1, codec_type="audio", codec_name="eac3",
                language="eng", title="Main audio", default=True,
            )
        ],
        subtitle_streams=[
            StreamInfo(
                index=2, codec_type="subtitle", codec_name="subrip",
                language="fra", title="Forced signs", forced=True,
            )
        ],
        attachment_streams=[
            StreamInfo(
                index=3, codec_type="attachment", codec_name="ttf",
                filename="font.ttf",
            )
        ],
        chapters=[ChapterInfo(id=0, title="Opening")],
        tags={"title": "Feature"},
    )


def test_full_remux_args_select_tracks_metadata_attachments_and_chapters():
    plan = create_conversion_plan(
        fidelity_info(), "output.mkv", available_encoders={"libx265"}
    )
    args = build_source_remux_args(plan, "source.mkv", sample_source=False)
    assert ["--audio-tracks", "1"] == args[1:3]
    assert args[args.index("--subtitle-tracks") + 1] == "2"
    assert args[args.index("--attachments") + 1] == "3"
    assert args[args.index("--chapters") + 1] == "all"
    assert "1:eng" in args
    assert "1:Main audio" in args
    assert "1:1" in args
    assert "2:fra" in args
    assert "2:Forced signs" in args
    assert "2:1" in args
    assert args[-1] == "source.mkv"


def test_sample_remux_args_explicitly_exclude_container_content():
    plan = create_conversion_plan(
        fidelity_info(), "sample.mkv",
        available_encoders={"libx265"}, sample_seconds=30,
    )
    args = build_source_remux_args(
        plan, "audio-subs.mkv", sample_source=True
    )
    assert "--audio-tracks" in args
    assert "--subtitle-tracks" in args
    assert "--no-attachments" in args
    assert "--no-chapters" in args
    assert "--no-global-tags" in args
    assert "--no-track-tags" in args
    assert args[-1] == "audio-subs.mkv"
