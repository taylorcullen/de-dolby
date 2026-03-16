"""Input codec and encoder strategies for extensible format support.

To add a new input codec:  subclass InputCodec
To add a new encoder:      subclass Encoder (or HardwareEncoder for GPU encoders)
Then register in the ENCODERS dict and INPUT_CODECS dict.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

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

# Shared HDR10 color flags appended by all hardware encoders
_HDR10_COLOR_ARGS = [
    "-color_primaries", "bt2020",
    "-color_trc", "smpte2084",
    "-colorspace", "bt2020nc",
]


def _resolve_bitrate(bitrate: str | None, source_bitrate: int | None,
                     fallback: str = "40M") -> str:
    """Shared bitrate resolution for hardware encoders."""
    if bitrate:
        return bitrate
    if source_bitrate:
        return str(int(source_bitrate * 0.8))
    return fallback


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


# ---------------------------------------------------------------------------
# Hardware encoder base class — handles HDR color flags and bitrate
# ---------------------------------------------------------------------------

class HardwareEncoder(Encoder):
    """Base for hardware GPU encoders (AMF, VAAPI, NVENC).

    Subclasses provide:
    - _presets: dict mapping quality tier → preset config
    - _encoder_args(preset): encoder-specific ffmpeg flags
    - _pix_fmt: pixel format (default: p010le)
    - _pre_encoder_args(): args before -c:v (e.g. VAAPI device init)

    The base class handles: -c:v, pixel format, HDR color flags, bitrate.
    """

    @property
    @abstractmethod
    def _presets(self) -> dict:
        """Quality preset dict (e.g. HEVC_AMF_PRESETS)."""

    @abstractmethod
    def _encoder_args(self, preset: dict) -> list[str]:
        """Encoder-specific flags from the preset (e.g. -quality, -rc)."""

    @property
    def _pix_fmt(self) -> str:
        """Pixel format. Override for encoders that need something different."""
        return "p010le"

    def _pre_encoder_args(self) -> list[str]:
        """Args before -c:v (e.g. VAAPI device init). Override if needed."""
        return []

    def build_args(self, meta, quality, crf=None, bitrate=None, source_bitrate=None):
        preset = self._presets.get(quality, list(self._presets.values())[0])
        args = self._pre_encoder_args()
        args += ["-c:v", self.ffmpeg_name, "-pix_fmt", self._pix_fmt]
        args += self._encoder_args(preset)
        args += _HDR10_COLOR_ARGS
        args += ["-b:v", _resolve_bitrate(bitrate, source_bitrate)]
        return args


# ---------------------------------------------------------------------------
# AMF encoders (Windows AMD)
# ---------------------------------------------------------------------------

class HevcAmfEncoder(HardwareEncoder):
    @property
    def ffmpeg_name(self) -> str:
        return "hevc_amf"

    @property
    def display_name(self) -> str:
        return "hevc_amf (AMD GPU)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    @property
    def _presets(self) -> dict:
        return HEVC_AMF_PRESETS

    def _encoder_args(self, preset):
        return ["-quality", preset["quality"], "-rc", preset["rc"],
                "-profile:v", preset["profile"]]


class Av1AmfEncoder(HardwareEncoder):
    @property
    def ffmpeg_name(self) -> str:
        return "av1_amf"

    @property
    def display_name(self) -> str:
        return "av1_amf (AMD GPU)"

    @property
    def codec_family(self) -> str:
        return "av1"

    @property
    def _presets(self) -> dict:
        return AV1_AMF_PRESETS

    def _encoder_args(self, preset):
        return ["-quality", preset["quality"], "-rc", preset["rc"]]


# ---------------------------------------------------------------------------
# VAAPI encoders (Linux AMD/Intel)
# ---------------------------------------------------------------------------

class HevcVaapiEncoder(HardwareEncoder):
    @property
    def ffmpeg_name(self) -> str:
        return "hevc_vaapi"

    @property
    def display_name(self) -> str:
        return "hevc_vaapi (Linux GPU)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    @property
    def _presets(self) -> dict:
        return HEVC_VAAPI_PRESETS

    def _pre_encoder_args(self):
        return ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=p010,hwupload"]

    def _encoder_args(self, preset):
        return ["-profile:v", "main10",
                "-compression_level", str(preset["compression_level"]),
                "-rc_mode", preset["rc_mode"]]


class Av1VaapiEncoder(HardwareEncoder):
    @property
    def ffmpeg_name(self) -> str:
        return "av1_vaapi"

    @property
    def display_name(self) -> str:
        return "av1_vaapi (Linux GPU)"

    @property
    def codec_family(self) -> str:
        return "av1"

    @property
    def _presets(self) -> dict:
        return AV1_VAAPI_PRESETS

    def _pre_encoder_args(self):
        return ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=p010,hwupload"]

    def _encoder_args(self, preset):
        return ["-compression_level", str(preset["compression_level"]),
                "-rc_mode", preset["rc_mode"]]


# ---------------------------------------------------------------------------
# NVENC encoders (NVIDIA, Linux/Windows)
# ---------------------------------------------------------------------------

class HevcNvencEncoder(HardwareEncoder):
    @property
    def ffmpeg_name(self) -> str:
        return "hevc_nvenc"

    @property
    def display_name(self) -> str:
        return "hevc_nvenc (NVIDIA GPU)"

    @property
    def codec_family(self) -> str:
        return "hevc"

    @property
    def _presets(self) -> dict:
        return HEVC_NVENC_PRESETS

    def _encoder_args(self, preset):
        return ["-preset:v", preset["preset"], "-tune:v", preset["tune"],
                "-rc:v", preset["rc"], "-profile:v", "main10"]


class Av1NvencEncoder(HardwareEncoder):
    @property
    def ffmpeg_name(self) -> str:
        return "av1_nvenc"

    @property
    def display_name(self) -> str:
        return "av1_nvenc (NVIDIA GPU)"

    @property
    def codec_family(self) -> str:
        return "av1"

    @property
    def _presets(self) -> dict:
        return AV1_NVENC_PRESETS

    def _encoder_args(self, preset):
        return ["-preset:v", preset["preset"], "-tune:v", preset["tune"],
                "-rc:v", preset["rc"]]


# ---------------------------------------------------------------------------
# CPU encoders (all platforms)
# ---------------------------------------------------------------------------

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
        ] + _HDR10_COLOR_ARGS


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

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
