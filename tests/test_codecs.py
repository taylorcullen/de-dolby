"""Tests for de_dolby.codecs — input codec and encoder strategies."""

import pytest

from de_dolby.codecs import (
    get_input_codec, get_encoder,
    HEVCCodec, AV1Codec, HardwareEncoder,
    HevcAmfEncoder, Libx265Encoder, Av1AmfEncoder, LibSvtAv1Encoder, CopyEncoder,
    INPUT_CODECS, ENCODERS, _HDR10_COLOR_ARGS,
)
from de_dolby.metadata import HDR10Metadata
from de_dolby.config import DEFAULT_MASTER_DISPLAY


# --- InputCodec strategy tests ---

class TestInputCodecs:
    def test_hevc_codec_properties(self):
        codec = get_input_codec("hevc")
        assert codec.name == "HEVC"
        assert codec.supports_dovi_tool is True
        assert codec.supports_lossless is True
        assert codec.raw_extension == ".hevc"

    def test_h265_alias(self):
        codec = get_input_codec("h265")
        assert codec.name == "HEVC"

    def test_av1_codec_properties(self):
        codec = get_input_codec("av1")
        assert codec.name == "AV1"
        assert codec.supports_dovi_tool is False
        assert codec.supports_lossless is False
        assert codec.raw_extension == ".ivf"

    def test_unknown_codec_raises(self):
        with pytest.raises(RuntimeError, match="vp9"):
            get_input_codec("vp9")

    def test_hevc_extraction_args(self):
        codec = HEVCCodec()
        args = codec.extraction_args("/tmp/video.hevc")
        assert "-bsf:v" in args
        assert "hevc_mp4toannexb" in args
        assert "-f" in args
        assert "hevc" in args

    def test_av1_extraction_args(self):
        codec = AV1Codec()
        args = codec.extraction_args("/tmp/video.ivf")
        assert "-f" in args
        assert "ivf" in args

    def test_hevc_auto_priority(self):
        codec = HEVCCodec()
        priority = codec.auto_encoder_priority()
        assert priority[0] == "hevc_amf"  # Windows AMD first
        assert "hevc_nvenc" in priority
        assert "hevc_vaapi" in priority
        assert priority[-1] == "libx265"  # CPU fallback last

    def test_av1_auto_priority(self):
        codec = AV1Codec()
        priority = codec.auto_encoder_priority()
        assert priority[0] == "av1_amf"
        assert "av1_nvenc" in priority
        assert "av1_vaapi" in priority
        assert priority[-1] == "libsvtav1"


# --- Encoder strategy tests ---

class TestEncoders:
    def _meta(self):
        return HDR10Metadata(master_display=DEFAULT_MASTER_DISPLAY, max_cll=1000, max_fall=400)

    def test_get_encoder_all_names(self):
        for name in ("copy", "hevc_amf", "libx265", "av1_amf", "libsvtav1"):
            enc = get_encoder(name)
            assert enc.ffmpeg_name == name

    def test_unknown_encoder_raises(self):
        with pytest.raises(RuntimeError, match="vp9_vaapi"):
            get_encoder("vp9_vaapi")

    def test_hevc_amf_family(self):
        enc = get_encoder("hevc_amf")
        assert enc.codec_family == "hevc"
        assert enc.output_format == "hevc"
        assert enc.output_extension == ".hevc"

    def test_av1_amf_family(self):
        enc = get_encoder("av1_amf")
        assert enc.codec_family == "av1"
        assert enc.output_format == "ivf"
        assert enc.output_extension == ".ivf"

    def test_hevc_amf_build_args(self):
        args = get_encoder("hevc_amf").build_args(self._meta(), "balanced", source_bitrate=25000000)
        assert "-c:v" in args
        assert "hevc_amf" in args
        assert "-b:v" in args

    def test_hevc_amf_bitrate_fallback(self):
        args = get_encoder("hevc_amf").build_args(self._meta(), "balanced", source_bitrate=None)
        idx = args.index("-b:v")
        assert args[idx + 1] == "40M"

    def test_libx265_build_args(self):
        args = get_encoder("libx265").build_args(self._meta(), "balanced")
        assert "-c:v" in args
        assert "libx265" in args
        assert "-crf" in args
        assert "-x265-params" in args

    def test_libx265_crf_override(self):
        args = get_encoder("libx265").build_args(self._meta(), "balanced", crf=22)
        idx = args.index("-crf")
        assert args[idx + 1] == "22"

    def test_av1_amf_build_args(self):
        args = get_encoder("av1_amf").build_args(self._meta(), "balanced", source_bitrate=25000000)
        assert "av1_amf" in args
        assert "-b:v" in args

    def test_libsvtav1_build_args(self):
        args = get_encoder("libsvtav1").build_args(self._meta(), "quality")
        assert "libsvtav1" in args
        assert "-crf" in args
        assert "-svtav1-params" in args

    def test_copy_build_args(self):
        args = get_encoder("copy").build_args(self._meta(), "balanced")
        assert args == ["-c:v", "copy"]

    def test_display_names(self):
        assert "AMD" in get_encoder("hevc_amf").display_name
        assert "CPU" in get_encoder("libx265").display_name
        assert "AMD" in get_encoder("av1_amf").display_name
        assert "CPU" in get_encoder("libsvtav1").display_name
        assert "no re-encode" in get_encoder("copy").display_name

    def test_all_encoders_registered(self):
        """Every encoder in ENCODERS should have consistent ffmpeg_name."""
        for name, enc in ENCODERS.items():
            assert enc.ffmpeg_name == name

    def test_all_input_codecs_have_auto_encoders(self):
        """Every encoder in auto_encoder_priority should be registered."""
        seen = set()
        for codec in INPUT_CODECS.values():
            if codec.name in seen:
                continue
            seen.add(codec.name)
            for name in codec.auto_encoder_priority():
                assert name in ENCODERS, f"{codec.name} encoder {name} not registered"

    def test_hardware_encoders_inherit_base(self):
        """All GPU encoders should use the HardwareEncoder base class."""
        hw_names = ["hevc_amf", "av1_amf", "hevc_vaapi", "av1_vaapi", "hevc_nvenc", "av1_nvenc"]
        for name in hw_names:
            enc = get_encoder(name)
            assert isinstance(enc, HardwareEncoder), f"{name} should be a HardwareEncoder"

    def test_hardware_encoders_include_hdr_flags(self):
        """All hardware encoders should include HDR10 color args."""
        meta = self._meta()
        hw_names = ["hevc_amf", "av1_amf", "hevc_vaapi", "av1_vaapi", "hevc_nvenc", "av1_nvenc"]
        for name in hw_names:
            args = get_encoder(name).build_args(meta, "balanced", source_bitrate=25000000)
            assert "-color_primaries" in args, f"{name} missing -color_primaries"
            assert "bt2020" in args, f"{name} missing bt2020"
            assert "-b:v" in args, f"{name} missing -b:v"

    def test_cpu_encoders_not_hardware(self):
        """CPU encoders should NOT be HardwareEncoder subclasses."""
        for name in ("libx265", "libsvtav1", "copy"):
            enc = get_encoder(name)
            assert not isinstance(enc, HardwareEncoder), f"{name} should not be HardwareEncoder"
