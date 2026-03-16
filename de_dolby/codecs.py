"""Input codec and encoder strategies for extensible format support.

To add a new input codec:  subclass InputCodec
To add a new encoder:      subclass Encoder
Then register in the ENCODERS dict and INPUT_CODECS dict.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from de_dolby.config import (
    AV1_AMF_PRESETS, AV1_NVENC_PRESETS, AV1_VAAPI_PRESETS,
    HEVC_AMF_PRESETS, HEVC_NVENC_PRESETS, HEVC_VAAPI_PRESETS,
    LIBSVTAV1_PRESETS, LIBX265_PRESETS,
)
from de_dolby.metadata import HDR10Metadata


# ---------------------------------------------------------------------------
# Input codec strategies
# ---------------------------------------------------------------------------

class InputCodec(ABC):
    """How to extract, probe RPU, and strip DV from a given input codec."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable codec name (e.g. 'HEVC', 'AV1')."""

    @property
    @abstractmethod
    def supports_dovi_tool(self) -> bool:
        """Whether dovi_tool can extract/strip RPU from this codec."""

    @property
    @abstractmethod
    def supports_lossless(self) -> bool:
        """Whether lossless RPU stripping is possible."""

    @abstractmethod
    def extraction_args(self, output_path: str) -> list[str]:
        """ffmpeg args to extract raw video bitstream (after -map 0:v:0 -c:v copy)."""

    @property
    @abstractmethod
    def raw_extension(self) -> str:
        """File extension for the extracted raw bitstream."""

    @abstractmethod
    def auto_encoder_priority(self) -> list[str]:
        """Return encoder names in priority order for auto-detection.

        The first available encoder in the list is used. The last entry
        should always be a CPU fallback that requires no GPU.
        """


class HEVCCodec(InputCodec):
    @property
    def name(self) -> str:
        return "HEVC"

    @property
    def supports_dovi_tool(self) -> bool:
        return True

    @property
    def supports_lossless(self) -> bool:
        return True

    def extraction_args(self, output_path: str) -> list[str]:
        return ["-bsf:v", "hevc_mp4toannexb", "-f", "hevc", output_path]

    @property
    def raw_extension(self) -> str:
        return ".hevc"

    def auto_encoder_priority(self) -> list[str]:
        return ["hevc_amf", "hevc_nvenc", "hevc_vaapi", "libx265"]


class AV1Codec(InputCodec):
    @property
    def name(self) -> str:
        return "AV1"

    @property
    def supports_dovi_tool(self) -> bool:
        return False

    @property
    def supports_lossless(self) -> bool:
        return False  # dovi_tool can't strip RPU from AV1

    def extraction_args(self, output_path: str) -> list[str]:
        # Not used — AV1 skips raw extraction (dovi_tool can't read it)
        return ["-f", "ivf", output_path]

    @property
    def raw_extension(self) -> str:
        return ".ivf"

    def auto_encoder_priority(self) -> list[str]:
        return ["av1_amf", "av1_nvenc", "av1_vaapi", "libsvtav1"]


INPUT_CODECS: dict[str, InputCodec] = {
    "hevc": HEVCCodec(),
    "h265": HEVCCodec(),
    "av1": AV1Codec(),
}


def get_input_codec(codec_name: str) -> InputCodec:
    """Look up the InputCodec strategy for a given ffprobe codec name."""
    codec = INPUT_CODECS.get(codec_name)
    if codec is None:
        supported = ", ".join(sorted(set(c.name for c in INPUT_CODECS.values())))
        raise RuntimeError(f"Video codec is {codec_name}, expected {supported}")
    return codec


# ---------------------------------------------------------------------------
# Encoder strategies
# ---------------------------------------------------------------------------

class Encoder(ABC):
    """How to build ffmpeg encode arguments for a given output encoder."""

    @property
    @abstractmethod
    def ffmpeg_name(self) -> str:
        """The ffmpeg encoder name (e.g. 'hevc_amf', 'libsvtav1')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable display name for banners."""

    @property
    @abstractmethod
    def codec_family(self) -> str:
        """'hevc' or 'av1' — used for output format selection."""

    @property
    def output_format(self) -> str:
        """ffmpeg output format for raw bitstream."""
        return "ivf" if self.codec_family == "av1" else "hevc"

    @property
    def output_extension(self) -> str:
        """File extension for encoded intermediate."""
        return ".ivf" if self.codec_family == "av1" else ".hevc"

    @abstractmethod
    def build_args(self, meta: HDR10Metadata, quality: str,
                   crf: int | None = None, bitrate: str | None = None,
                   source_bitrate: int | None = None) -> list[str]:
        """Build encoder-specific ffmpeg arguments (after -map, before output)."""


class CopyEncoder(Encoder):
    @property
    def ffmpeg_name(self) -> str:
        return "copy"

    @property
    def display_name(self) -> str:
        return "copy (no re-encode)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        return ["-c:v", "copy"]


class HevcAmfEncoder(Encoder):
    @property
    def ffmpeg_name(self) -> str:
        return "hevc_amf"

    @property
    def display_name(self) -> str:
        return "hevc_amf (AMD GPU)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = HEVC_AMF_PRESETS.get(quality, HEVC_AMF_PRESETS["balanced"])
        args = [
            "-c:v", "hevc_amf",
            "-pix_fmt", "p010le",
            "-quality", preset["quality"],
            "-rc", preset["rc"],
            "-profile:v", preset["profile"],
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]
        args += ["-b:v", _resolve_bitrate(bitrate, source_bitrate)]
        return args


class Libx265Encoder(Encoder):
    @property
    def ffmpeg_name(self) -> str:
        return "libx265"

    @property
    def display_name(self) -> str:
        return "libx265 (CPU)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = LIBX265_PRESETS.get(quality, LIBX265_PRESETS["balanced"])
        resolved_crf = crf if crf is not None else preset["crf"]
        x265_params = (
            f"hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
            f"colormatrix=bt2020nc:master-display={meta.x265_master_display}:"
            f"max-cll={meta.content_light_level}"
        )
        return [
            "-c:v", "libx265",
            "-pix_fmt", "p010le",
            "-preset", preset["preset"],
            "-crf", str(resolved_crf),
            "-x265-params", x265_params,
        ]


class Av1AmfEncoder(Encoder):
    @property
    def ffmpeg_name(self) -> str:
        return "av1_amf"

    @property
    def display_name(self) -> str:
        return "av1_amf (AMD GPU)"

    @property
    def codec_family(self) -> str:
        return "av1"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = AV1_AMF_PRESETS.get(quality, AV1_AMF_PRESETS["balanced"])
        args = [
            "-c:v", "av1_amf",
            "-pix_fmt", "p010le",
            "-quality", preset["quality"],
            "-rc", preset["rc"],
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]
        args += ["-b:v", _resolve_bitrate(bitrate, source_bitrate)]
        return args


def _resolve_bitrate(bitrate: str | None, source_bitrate: int | None,
                     fallback: str = "40M") -> str:
    """Shared bitrate resolution for hardware encoders."""
    if bitrate:
        return bitrate
    if source_bitrate:
        return str(int(source_bitrate * 0.8))
    return fallback


class HevcVaapiEncoder(Encoder):
    """VAAPI HEVC encoder (Linux AMD/Intel GPU)."""

    @property
    def ffmpeg_name(self) -> str:
        return "hevc_vaapi"

    @property
    def display_name(self) -> str:
        return "hevc_vaapi (Linux GPU)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = HEVC_VAAPI_PRESETS.get(quality, HEVC_VAAPI_PRESETS["balanced"])
        args = [
            "-vaapi_device", "/dev/dri/renderD128",
            "-vf", "format=p010,hwupload",
            "-c:v", "hevc_vaapi",
            "-profile:v", "main10",
            "-compression_level", str(preset["compression_level"]),
            "-rc_mode", preset["rc_mode"],
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]
        args += ["-b:v", _resolve_bitrate(bitrate, source_bitrate)]
        return args


class Av1VaapiEncoder(Encoder):
    """VAAPI AV1 encoder (Linux AMD/Intel GPU)."""

    @property
    def ffmpeg_name(self) -> str:
        return "av1_vaapi"

    @property
    def display_name(self) -> str:
        return "av1_vaapi (Linux GPU)"

    @property
    def codec_family(self) -> str:
        return "av1"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = AV1_VAAPI_PRESETS.get(quality, AV1_VAAPI_PRESETS["balanced"])
        args = [
            "-vaapi_device", "/dev/dri/renderD128",
            "-vf", "format=p010,hwupload",
            "-c:v", "av1_vaapi",
            "-compression_level", str(preset["compression_level"]),
            "-rc_mode", preset["rc_mode"],
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]
        args += ["-b:v", _resolve_bitrate(bitrate, source_bitrate)]
        return args


class HevcNvencEncoder(Encoder):
    """NVENC HEVC encoder (NVIDIA GPU, Linux/Windows)."""

    @property
    def ffmpeg_name(self) -> str:
        return "hevc_nvenc"

    @property
    def display_name(self) -> str:
        return "hevc_nvenc (NVIDIA GPU)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = HEVC_NVENC_PRESETS.get(quality, HEVC_NVENC_PRESETS["balanced"])
        args = [
            "-c:v", "hevc_nvenc",
            "-pix_fmt", "p010le",
            "-preset:v", preset["preset"],
            "-tune:v", preset["tune"],
            "-rc:v", preset["rc"],
            "-profile:v", "main10",
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]
        args += ["-b:v", _resolve_bitrate(bitrate, source_bitrate)]
        return args


class Av1NvencEncoder(Encoder):
    """NVENC AV1 encoder (NVIDIA GPU, Linux/Windows)."""

    @property
    def ffmpeg_name(self) -> str:
        return "av1_nvenc"

    @property
    def display_name(self) -> str:
        return "av1_nvenc (NVIDIA GPU)"

    @property
    def codec_family(self) -> str:
        return "av1"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = AV1_NVENC_PRESETS.get(quality, AV1_NVENC_PRESETS["balanced"])
        args = [
            "-c:v", "av1_nvenc",
            "-pix_fmt", "p010le",
            "-preset:v", preset["preset"],
            "-tune:v", preset["tune"],
            "-rc:v", preset["rc"],
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]
        args += ["-b:v", _resolve_bitrate(bitrate, source_bitrate)]
        return args


class LibSvtAv1Encoder(Encoder):
    @property
    def ffmpeg_name(self) -> str:
        return "libsvtav1"

    @property
    def display_name(self) -> str:
        return "libsvtav1 (CPU)"

    @property
    def codec_family(self) -> str:
        return "av1"

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = LIBSVTAV1_PRESETS.get(quality, LIBSVTAV1_PRESETS["balanced"])
        resolved_crf = crf if crf is not None else preset["crf"]
        return [
            "-c:v", "libsvtav1",
            "-pix_fmt", "yuv420p10le",
            "-preset", str(preset["preset"]),
            "-crf", str(resolved_crf),
            "-svtav1-params", "color-primaries=9:transfer-characteristics=16:matrix-coefficients=9",
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
        ]


ENCODERS: dict[str, Encoder] = {
    "copy": CopyEncoder(),
    # HEVC encoders
    "hevc_amf": HevcAmfEncoder(),
    "hevc_vaapi": HevcVaapiEncoder(),
    "hevc_nvenc": HevcNvencEncoder(),
    "libx265": Libx265Encoder(),
    # AV1 encoders
    "av1_amf": Av1AmfEncoder(),
    "av1_vaapi": Av1VaapiEncoder(),
    "av1_nvenc": Av1NvencEncoder(),
    "libsvtav1": LibSvtAv1Encoder(),
}


def get_encoder(name: str) -> Encoder:
    """Look up the Encoder strategy by name."""
    enc = ENCODERS.get(name)
    if enc is None:
        raise RuntimeError(f"Unknown encoder: {name}")
    return enc
