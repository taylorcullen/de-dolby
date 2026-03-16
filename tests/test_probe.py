"""Tests for de_dolby.probe — uses mocked ffprobe output."""

import json
from unittest.mock import patch, MagicMock

from de_dolby.probe import probe, format_info, StreamInfo, FileInfo


def _mock_ffprobe_result(data: dict) -> MagicMock:
    """Create a mock CompletedProcess with JSON-encoded stdout."""
    result = MagicMock()
    result.stdout = json.dumps(data).encode()
    result.returncode = 0
    return result


SAMPLE_FFPROBE_OUTPUT = {
    "format": {
        "duration": "5400.123",
        "bit_rate": "25000000",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            "color_transfer": "smpte2084",
            "color_primaries": "bt2020",
            "color_space": "bt2020nc",
            "r_frame_rate": "24000/1001",
            "bit_rate": "20000000",
            "disposition": {"default": 1},
            "tags": {},
            "side_data_list": [
                {
                    "side_data_type": "DOVI configuration record",
                    "dv_profile": 7,
                    "dv_bl_signal_compatibility_id": 6,
                }
            ],
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "eac3",
            "bit_rate": "640000",
            "disposition": {"default": 1},
            "tags": {"language": "eng", "title": "Surround 5.1"},
        },
        {
            "index": 2,
            "codec_type": "subtitle",
            "codec_name": "subrip",
            "disposition": {"default": 0},
            "tags": {"language": "eng"},
        },
    ],
    "frames": [],
}


@patch("de_dolby.probe.run_ffprobe")
def test_probe_basic(mock_ffprobe):
    mock_ffprobe.return_value = _mock_ffprobe_result(SAMPLE_FFPROBE_OUTPUT)
    info = probe("test.mkv")

    assert info.path == "test.mkv"
    assert info.duration is not None
    assert abs(info.duration - 5400.123) < 0.01
    assert info.overall_bitrate == 25000000
    assert info.dv_profile == 7
    assert info.dv_bl_signal_compatibility_id == 6
    assert info.has_hdr10 is True


@patch("de_dolby.probe.run_ffprobe")
def test_probe_video_stream(mock_ffprobe):
    mock_ffprobe.return_value = _mock_ffprobe_result(SAMPLE_FFPROBE_OUTPUT)
    info = probe("test.mkv")

    assert len(info.video_streams) == 1
    vs = info.video_streams[0]
    assert vs.codec_name == "hevc"
    assert vs.width == 3840
    assert vs.height == 2160
    assert vs.bit_depth == 10
    assert vs.color_transfer == "smpte2084"
    assert vs.bitrate == 20000000


@patch("de_dolby.probe.run_ffprobe")
def test_probe_audio_and_subs(mock_ffprobe):
    mock_ffprobe.return_value = _mock_ffprobe_result(SAMPLE_FFPROBE_OUTPUT)
    info = probe("test.mkv")

    assert len(info.audio_streams) == 1
    assert info.audio_streams[0].codec_name == "eac3"
    assert info.audio_streams[0].language == "eng"

    assert len(info.subtitle_streams) == 1
    assert info.subtitle_streams[0].codec_name == "subrip"


@patch("de_dolby.probe.run_ffprobe")
def test_probe_dv_from_frames(mock_ffprobe):
    """DV profile can be detected from frame side data when stream side data is absent."""
    data = {
        "format": {"duration": "100"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "disposition": {"default": 1},
                "tags": {},
            }
        ],
        "frames": [
            {
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 5,
                        "dv_bl_signal_compatibility_id": 0,
                    }
                ]
            }
        ],
    }
    mock_ffprobe.return_value = _mock_ffprobe_result(data)
    info = probe("test.mkv")
    assert info.dv_profile == 5


@patch("de_dolby.probe.run_ffprobe")
def test_probe_no_dv(mock_ffprobe):
    data = {
        "format": {"duration": "100"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "disposition": {"default": 1},
                "tags": {},
            }
        ],
        "frames": [],
    }
    mock_ffprobe.return_value = _mock_ffprobe_result(data)
    info = probe("test.mkv")
    assert info.dv_profile is None


def test_format_info():
    info = FileInfo(
        path="test.mkv",
        duration=3600.0,
        overall_bitrate=25000000,
        dv_profile=7,
        has_hdr10=True,
        video_streams=[
            StreamInfo(index=0, codec_type="video", codec_name="hevc",
                       width=3840, height=2160, frame_rate="24000/1001")
        ],
        audio_streams=[
            StreamInfo(index=1, codec_type="audio", codec_name="eac3", language="eng")
        ],
    )
    text = format_info(info)
    assert "test.mkv" in text
    assert "Profile 7" in text
    assert "hevc" in text.lower()
    assert "1:00:00" in text
