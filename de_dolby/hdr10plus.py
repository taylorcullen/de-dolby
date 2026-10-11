"""HDR10+ preservation and experimental sampled-pixel Profile A analysis."""

from array import array
from functools import lru_cache
import json
import math
from pathlib import Path
import sys

from de_dolby.tools import run_ffmpeg, run_hdr10plus_tool

ANALYSIS_WIDTH = 64
ANALYSIS_HEIGHT = 36
FRAME_BYTES = ANALYSIS_WIDTH * ANALYSIS_HEIGHT * 6
DISTRIBUTION_INDEXES = [1, 5, 10, 25, 50, 75, 90, 95, 99]
# Indexes 5 and 10 carry Y99 and the percentage at/below 100 nits;
# index 99 denotes the 99.98th maxRGB percentile (HDR10+ Application 4).
PERCENTILES = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.9998]


def pq_to_nits(value: float) -> float:
    """SMPTE ST 2084 EOTF for a normalized PQ code value."""
    m1, m2 = 2610 / 16384, 2523 / 32
    c1, c2, c3 = 3424 / 4096, 2413 / 128, 2392 / 128
    power = min(1.0, max(0.0, value)) ** (1 / m2)
    return 10000 * (max(power - c1, 0) / (c2 - c3 * power)) ** (1 / m1)


@lru_cache(maxsize=1)
def _pq_table() -> tuple[int, ...]:
    # HDR10+ brightness fields use 0.1 cd/m² units.
    return tuple(round(pq_to_nits(code / 65535) * 10) for code in range(65536))


def frame_metadata(frame: bytes, index: int) -> dict:
    """Measure full-range PQ GBR planar pixels in display order."""
    if len(frame) != FRAME_BYTES:
        raise RuntimeError("Incomplete HDR10+ analysis frame")
    codes = array("H")
    codes.frombytes(frame)
    if sys.byteorder != "little":
        codes.byteswap()
    pixels = ANALYSIS_WIDTH * ANALYSIS_HEIGHT
    table = _pq_table()
    green, blue, red = (
        [table[code] for code in codes[offset:offset + pixels]]
        for offset in (0, pixels, pixels * 2)
    )
    maximum = sorted(max(r, g, b) for r, g, b in zip(red, green, blue))
    distribution = [maximum[max(0, math.ceil(p * pixels) - 1)] for p in PERCENTILES]
    luminance = sorted(0.2627 * r + 0.6780 * g + 0.0593 * b for r, g, b in zip(red, green, blue))
    distribution[1] = round(luminance[math.ceil(0.99 * pixels) - 1])
    distribution[2] = round(sum(value <= 1000 for value in luminance) * 100 / pixels)
    return {
        "NumberOfWindows": 1,
        "TargetedSystemDisplayMaximumLuminance": 0,
        "LuminanceParameters": {
            "AverageRGB": round(sum(maximum) / pixels),
            "MaxScl": [max(red), max(green), max(blue)],
            "LuminanceDistributions": {
                "DistributionIndex": DISTRIBUTION_INDEXES,
                "DistributionValues": distribution,
            },
        },
        "SceneFrameIndex": 0,
        "SceneId": index,
        "SequenceFrameIndex": index,
    }


def generate_metadata(video: str, directory: str) -> str:
    """Decode every output frame; sample its image onto a small analysis grid.

    This measures the HDR10 output pixels, never treats DV L1 as an HDR10+
    histogram, and deliberately emits Profile A without a fabricated curve.
    Raw samples and JSON are streamed to disk to bound Python memory usage.
    """
    samples = Path(directory) / "hdr10plus-samples.gbr"
    output = Path(directory) / "hdr10plus-generated.json"
    run_ffmpeg([
        "-i", video, "-map", "0:v:0", "-an", "-sn",
        "-vf", f"zscale=w={ANALYSIS_WIDTH}:h={ANALYSIS_HEIGHT}:"
        "matrixin=bt2020nc:transferin=smpte2084:primariesin=bt2020:rangein=limited:"
        "matrix=gbr:transfer=smpte2084:primaries=bt2020:range=full:filter=point,format=gbrp16le",
        "-fps_mode", "passthrough", "-f", "rawvideo", str(samples),
    ])
    count = 0
    try:
        with samples.open("rb") as source, output.open("w", encoding="utf-8") as target:
            target.write('{"JSONInfo":{"HDR10plusProfile":"A","Version":"1.0"},"SceneInfo":[')
            while frame := source.read(FRAME_BYTES):
                if count:
                    target.write(",")
                json.dump(frame_metadata(frame, count), target)
                count += 1
            target.write('],"SceneInfoSummary":')
            json.dump({"SceneFirstFrameIndex": list(range(count)), "SceneFrameNumbers": [1] * count}, target)
            target.write(',"ToolInfo":{"Tool":"de-dolby experimental sampled analysis","Version":"1"}}')
        if not count:
            raise RuntimeError("HDR10+ analysis decoded no video frames")
    finally:
        samples.unlink(missing_ok=True)
    return str(output)


def verify_metadata(video: str, directory: str) -> dict:
    output = Path(directory) / "hdr10plus-verified.json"
    output.unlink(missing_ok=True)
    # --verify only checks presence and exits without writing JSON. Normal
    # extraction validates profile conformity and lets us check every frame.
    run_hdr10plus_tool(["extract", video, "-o", str(output)])
    if not output.exists():
        raise RuntimeError("No HDR10+ metadata found; use --hdr10plus generate for experimental generation")
    data = json.loads(output.read_text(encoding="utf-8"))
    if data.get("JSONInfo", {}).get("HDR10plusProfile") not in {"A", "B"} or not data.get("SceneInfo"):
        raise RuntimeError("Output does not contain valid HDR10+ metadata")
    return data


def prepare_video(video: str, directory: str, mode: str) -> str:
    if mode == "preserve":
        verify_metadata(video, directory)
        return video
    metadata = generate_metadata(video, directory)
    clean = str(Path(directory) / "hdr10plus-clean.hevc")
    output = str(Path(directory) / "hdr10plus.hevc")
    run_hdr10plus_tool(["remove", video, "-o", clean])
    run_hdr10plus_tool(["inject", "-i", clean, "-j", metadata, "-o", output])
    verified = verify_metadata(output, directory)
    expected = json.loads(Path(metadata).read_text(encoding="utf-8"))
    if len(verified["SceneInfo"]) != len(expected["SceneInfo"]):
        raise RuntimeError("HDR10+ metadata frame count changed during injection")
    if [frame["LuminanceParameters"] for frame in verified["SceneInfo"]] != [frame["LuminanceParameters"] for frame in expected["SceneInfo"]]:
        raise RuntimeError("HDR10+ frame metadata changed or reordered during injection")
    return output
