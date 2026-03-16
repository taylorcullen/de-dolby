"""Tests for de_dolby.probe — uses mocked ffprobe output."""

import json
from unittest.mock import patch, MagicMock

from de_dolby.probe import probe, format_info, StreamInfo, FileInfo, _parse_ffprobe_master_display, _parse_rational


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


# --- Master display parsing tests ---

def test_parse_rational_fraction():
    assert abs(_parse_rational("34000/50000") - 0.68) < 0.001


def test_parse_rational_integer():
    assert _parse_rational("1000") == 1000.0


def test_parse_ffprobe_master_display_bt2020():
    sd = {
        "red_x": "34000/50000", "red_y": "16000/50000",
        "green_x": "13250/50000", "green_y": "34500/50000",
        "blue_x": "7500/50000", "blue_y": "3000/50000",
        "white_point_x": "15635/50000", "white_point_y": "16450/50000",
        "min_luminance": "1/10000", "max_luminance": "10000000/10000",
    }
    result = _parse_ffprobe_master_display(sd)
    assert result is not None
    assert "G(13250,34500)" in result
    assert "R(34000,16000)" in result
    assert "L(10000000,1)" in result


def test_parse_ffprobe_master_display_missing_keys():
    assert _parse_ffprobe_master_display({"red_x": "100"}) is None
    assert _parse_ffprobe_master_display({}) is None


@patch("de_dolby.probe.run_ffprobe")
def test_probe_extracts_master_display(mock_ffprobe):
    """Master display from stream side data gets stored in FileInfo."""
    data = {
        "format": {"duration": "100"},
        "streams": [{
            "index": 0, "codec_type": "video", "codec_name": "hevc",
            "disposition": {"default": 1}, "tags": {},
            "side_data_list": [
                {
                    "side_data_type": "Mastering display metadata",
                    "red_x": "34000/50000", "red_y": "16000/50000",
                    "green_x": "13250/50000", "green_y": "34500/50000",
                    "blue_x": "7500/50000", "blue_y": "3000/50000",
                    "white_point_x": "15635/50000", "white_point_y": "16450/50000",
                    "min_luminance": "1/10000", "max_luminance": "10000000/10000",
                },
                {
                    "side_data_type": "Content light level metadata",
                    "max_content": 1000, "max_average": 400,
                },
            ],
        }],
        "frames": [],
    }
    mock_ffprobe.return_value = _mock_ffprobe_result(data)
    info = probe("test.mkv")
    assert info.master_display is not None
    assert "G(13250,34500)" in info.master_display
    assert info.content_light_level == "1000,400"


@patch("de_dolby.probe.run_ffprobe")
def test_probe_no_master_display(mock_ffprobe):
    data = {
        "format": {"duration": "100"},
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc",
                      "disposition": {"default": 1}, "tags": {}}],
        "frames": [],
    }
    mock_ffprobe.return_value = _mock_ffprobe_result(data)
    info = probe("test.mkv")
    assert info.master_display is None
    assert info.content_light_level is None
