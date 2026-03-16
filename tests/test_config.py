"""Tests for de_dolby.config."""

from de_dolby.config import (
    DEFAULT_MASTER_DISPLAY,
    DEFAULT_MAX_CLL,
    DEFAULT_MAX_FALL,
    HEVC_AMF_PRESETS,
    LIBX265_PRESETS,
)


def test_hevc_amf_presets_have_required_keys():
    for tier in ("fast", "balanced", "quality"):
        preset = HEVC_AMF_PRESETS[tier]
        assert "quality" in preset
        assert "rc" in preset
        assert "profile" in preset
        assert preset["profile"] == "main10"


def test_libx265_presets_have_required_keys():
    for tier in ("fast", "balanced", "quality"):
        preset = LIBX265_PRESETS[tier]
        assert "preset" in preset
        assert "crf" in preset
        assert isinstance(preset["crf"], int)
        assert 0 <= preset["crf"] <= 51


def test_libx265_quality_ordering():
    """Higher quality tier should use lower CRF (better quality)."""
    assert LIBX265_PRESETS["fast"]["crf"] > LIBX265_PRESETS["balanced"]["crf"]
    assert LIBX265_PRESETS["balanced"]["crf"] > LIBX265_PRESETS["quality"]["crf"]


def test_defaults_are_reasonable():
    assert DEFAULT_MAX_CLL > 0
    assert DEFAULT_MAX_FALL > 0
    assert "G(" in DEFAULT_MASTER_DISPLAY
    assert "L(" in DEFAULT_MASTER_DISPLAY
