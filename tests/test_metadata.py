"""Tests for de_dolby.metadata."""

from de_dolby.metadata import HDR10Metadata, _find_l6, _parse_export_data


def test_hdr10_metadata_content_light_level():
    meta = HDR10Metadata(master_display="", max_cll=1000, max_fall=400)
    assert meta.content_light_level == "1000,400"


def test_hdr10_metadata_x265_master_display():
    md = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
    meta = HDR10Metadata(master_display=md, max_cll=1000, max_fall=400)
    assert meta.x265_master_display == md


def test_parse_master_display_bt2020():
    md = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
    meta = HDR10Metadata(master_display=md, max_cll=1000, max_fall=400)
    parsed = meta._parse_master_display()
    assert parsed is not None
    assert abs(parsed["gx"] - 0.265) < 0.001
    assert abs(parsed["gy"] - 0.690) < 0.001
    assert abs(parsed["lmax"] - 1000.0) < 0.01
    assert abs(parsed["lmin"] - 0.0001) < 0.0001


def test_parse_master_display_invalid():
    meta = HDR10Metadata(master_display="invalid", max_cll=0, max_fall=0)
    assert meta._parse_master_display() is None


def test_parse_master_display_empty():
    meta = HDR10Metadata(master_display="", max_cll=0, max_fall=0)
    assert meta._parse_master_display() is None


def test_mkvmerge_args_basic():
    meta = HDR10Metadata(master_display="", max_cll=1000, max_fall=400)
    args = meta.mkvmerge_args(track_id=0)
    assert "--colour-matrix-coefficients" in args
    assert "0:9" in args
    assert "--max-content-light" in args
    assert "0:1000" in args
    assert "--max-frame-light" in args
    assert "0:400" in args


def test_mkvmerge_args_with_valid_master_display():
    md = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
    meta = HDR10Metadata(master_display=md, max_cll=1000, max_fall=400)
    args = meta.mkvmerge_args(track_id=0)
    assert "--chromaticity-coordinates" in args
    assert "--white-colour-coordinates" in args
    assert "--max-luminance" in args
    assert "--min-luminance" in args


def test_find_l6_direct():
    rpu = {"level6": {"max_content_light_level": 800, "max_frame_average_light_level": 200}}
    l6 = _find_l6(rpu)
    assert l6 is not None
    assert l6["max_content_light_level"] == 800


def test_find_l6_nested_dm_data():
    rpu = {
        "dm_data": {
            "level6": {"max_content_light_level": 500, "max_frame_average_light_level": 100}
        }
    }
    l6 = _find_l6(rpu)
    assert l6 is not None
    assert l6["max_content_light_level"] == 500


def test_find_l6_cmv40():
    rpu = {
        "cmv40": {"level6": {"max_content_light_level": 600, "max_frame_average_light_level": 150}}
    }
    l6 = _find_l6(rpu)
    assert l6 is not None
    assert l6["max_content_light_level"] == 600


def test_find_l6_cmv40_metadata_blocks():
    rpu = {
        "cmv40": {
            "metadata_blocks": [
                {"max_content_light_level": 700, "max_frame_average_light_level": 250}
            ]
        }
    }
    l6 = _find_l6(rpu)
    assert l6 is not None
    assert l6["max_content_light_level"] == 700


def test_find_l6_missing():
    assert _find_l6({}) is None
    assert _find_l6({"other": "data"}) is None


def test_parse_export_data_with_l6_list():
    data = [{"level6": {"max_content_light_level": 900, "max_frame_average_light_level": 300}}]
    meta = _parse_export_data(data)
    assert meta.max_cll == 900
    assert meta.max_fall == 300


def test_parse_export_data_with_rpus_key():
    data = {
        "rpus": [
            {"level6": {"max_content_light_level": 1200, "max_frame_average_light_level": 500}}
        ]
    }
    meta = _parse_export_data(data)
    assert meta.max_cll == 1200
    assert meta.max_fall == 500


def test_parse_export_data_empty():
    meta = _parse_export_data({})
    # Should return defaults
    assert meta.max_cll > 0
    assert meta.max_fall > 0
