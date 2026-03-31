"""Extract HDR10 static metadata from dovi_tool RPU export data."""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from de_dolby.config import DEFAULT_MASTER_DISPLAY, DEFAULT_MAX_CLL, DEFAULT_MAX_FALL
from de_dolby.tools import run_dovi_tool


@dataclass
class HDR10Metadata:
    master_display: str  # ffmpeg format: G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)
    max_cll: int
    max_fall: int

    @property
    def content_light_level(self) -> str:
        return f"{self.max_cll},{self.max_fall}"

    @property
    def x265_master_display(self) -> str:
        """Same format works for x265 --master-display param."""
        return self.master_display

    def mkvmerge_args(self, track_id: int = 0) -> list[str]:
        """Build mkvmerge flags for HDR10 metadata on a video track."""
        args = [
            "--colour-matrix-coefficients",
            f"{track_id}:9",  # bt2020nc
            "--colour-transfer-characteristics",
            f"{track_id}:16",  # smpte2084
            "--colour-primaries",
            f"{track_id}:9",  # bt2020
            "--colour-range",
            f"{track_id}:1",  # limited
            "--max-content-light",
            f"{track_id}:{self.max_cll}",
            "--max-frame-light",
            f"{track_id}:{self.max_fall}",
        ]
        # Parse master display to extract chromaticity and luminance
        md = self._parse_master_display()
        if md:
            args += [
                "--chromaticity-coordinates",
                f"{track_id}:{md['rx']},{md['ry']},{md['gx']},{md['gy']},{md['bx']},{md['by']}",
                "--white-colour-coordinates",
                f"{track_id}:{md['wpx']},{md['wpy']}",
                "--max-luminance",
                f"{track_id}:{md['lmax']}",
                "--min-luminance",
                f"{track_id}:{md['lmin']}",
            ]
        return args

    def _parse_master_display(self) -> dict[str, float] | None:
        """Parse master_display string into component values.

        Input format: G(gx,gy)B(bx,by)R(rx,ry)WP(wpx,wpy)L(lmax,lmin)
        Values are in 1/50000 units for chromaticity coordinates.
        Luminance max is in 1/10000 cd/m², min is in 1/10000 cd/m².
        mkvmerge expects chromaticity in 0.0-1.0 float and luminance in cd/m².
        """
        import re

        m = re.match(
            r"G\((\d+),(\d+)\)B\((\d+),(\d+)\)R\((\d+),(\d+)\)"
            r"WP\((\d+),(\d+)\)L\((\d+),(\d+)\)",
            self.master_display,
        )
        if not m:
            return None
        vals = [int(x) for x in m.groups()]
        return {
            "gx": vals[0] / 50000,
            "gy": vals[1] / 50000,
            "bx": vals[2] / 50000,
            "by": vals[3] / 50000,
            "rx": vals[4] / 50000,
            "ry": vals[5] / 50000,
            "wpx": vals[6] / 50000,
            "wpy": vals[7] / 50000,
            "lmax": vals[8] / 10000,
            "lmin": vals[9] / 10000,
        }


def extract_rpu(hevc_path: str, rpu_path: str) -> None:
    """Extract RPU data from a raw HEVC bitstream file."""
    run_dovi_tool(["extract-rpu", hevc_path, "-o", rpu_path])


def parse_rpu_metadata(rpu_path: str) -> HDR10Metadata:
    """Parse RPU file to extract HDR10 static metadata.

    Uses dovi_tool export-data to convert RPU to JSON, then extracts L6 metadata.
    Falls back to defaults if L6 is not present.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        json_path = tmp.name

    try:
        run_dovi_tool(["export-data", rpu_path, "-d", json_path])
        data = json.loads(Path(json_path).read_text())
    except Exception:
        # If export-data fails, return defaults
        return HDR10Metadata(
            master_display=DEFAULT_MASTER_DISPLAY,
            max_cll=DEFAULT_MAX_CLL,
            max_fall=DEFAULT_MAX_FALL,
        )
    finally:
        Path(json_path).unlink(missing_ok=True)

    return _parse_export_data(data)


def _parse_export_data(data: dict) -> HDR10Metadata:
    """Parse dovi_tool export-data JSON output."""
    max_cll = DEFAULT_MAX_CLL
    max_fall = DEFAULT_MAX_FALL
    master_display = DEFAULT_MASTER_DISPLAY

    # Look for L6 metadata (MaxCLL/MaxFALL) in the RPU export
    # The JSON structure has a list of RPU entries; L6 appears in many frames
    # We want the global L6 values (they're typically the same across all frames)
    rpus = data if isinstance(data, list) else data.get("rpus", data.get("data", []))

    if isinstance(rpus, dict):
        # Single RPU or summary format
        l6 = _find_l6(rpus)
        if l6:
            max_cll = l6.get("max_content_light_level", max_cll)
            max_fall = l6.get("max_frame_average_light_level", max_fall)
        md = _find_master_display(rpus)
        if md:
            master_display = md
    elif isinstance(rpus, list) and rpus:
        # Check the first RPU entry for L6
        l6 = _find_l6(rpus[0] if isinstance(rpus[0], dict) else {})
        if l6:
            max_cll = l6.get("max_content_light_level", max_cll)
            max_fall = l6.get("max_frame_average_light_level", max_fall)
        md = _find_master_display(rpus[0] if isinstance(rpus[0], dict) else {})
        if md:
            master_display = md

    return HDR10Metadata(
        master_display=master_display,
        max_cll=max_cll,
        max_fall=max_fall,
    )


def _find_l6(rpu: dict) -> dict | None:
    """Find Level 6 metadata block in an RPU entry."""
    # dovi_tool export-data nests metadata differently depending on version
    # Try common paths
    for key in ("level6", "dm_data", "vdr_dm_data"):
        if key in rpu:
            obj = rpu[key]
            if isinstance(obj, dict):
                if "max_content_light_level" in obj:
                    return obj
                # Nested further
                if "level6" in obj:
                    return obj["level6"]  # type: ignore[no-any-return]
                for sub in obj.values():
                    if isinstance(sub, dict) and "max_content_light_level" in sub:
                        return sub
    # Try cmv40 path (newer dovi_tool versions)
    cmv40 = rpu.get("cmv40", {})
    for key in ("level6", "metadata_blocks"):
        if key in cmv40:
            obj = cmv40[key]
            if isinstance(obj, dict) and "max_content_light_level" in obj:
                return obj
            if isinstance(obj, list):
                for block in obj:
                    if isinstance(block, dict) and "max_content_light_level" in block:
                        return block
    return None


def _find_master_display(rpu: dict) -> str | None:
    """Try to extract mastering display primaries from RPU data.

    Returns ffmpeg-format master_display string or None.
    """
    # dovi_tool doesn't always export mastering display in a standard way.
    # For most DV content the display primaries are standard BT.2020 / DCI-P3,
    # so we rely on the defaults unless explicitly provided.
    return None
