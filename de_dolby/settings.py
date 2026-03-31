"""Configuration file loading and settings management for de-dolby.

Supports TOML and YAML config files at platform-appropriate locations:
- ~/.config/de-dolby/config.toml (Unix)
- ~/.config/de-dolby/config.yaml (Unix)
- %APPDATA%/de-dolby/config.toml (Windows)
- %APPDATA%/de-dolby/config.yaml (Windows)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class ToolPaths:
    """Paths to external tools."""

    ffmpeg: str | None = None
    dovi_tool: str | None = None
    mkvmerge: str | None = None


@dataclass
class TrackSettings:
    """Track filtering settings."""

    audio_languages: list[str] = field(default_factory=list)
    skip_subtitles: bool = False


@dataclass
class Settings:
    """de-dolby configuration settings.

    Loaded from config file and merged with CLI arguments.
    CLI arguments take precedence over config file values.
    """

    # Default conversion options
    encoder: Literal["auto", "hevc_amf", "libx265", "av1_amf", "libsvtav1", "copy"] = "auto"
    quality: Literal["fast", "balanced", "quality"] = "balanced"
    crf: int | None = None
    bitrate: str | None = None
    output_dir: str | None = None
    temp_dir: str | None = None
    workers: int | None = None
    verbose: bool = False
    force: bool = False

    # Tool paths
    tool_paths: ToolPaths = field(default_factory=ToolPaths)

    # Track filtering
    tracks: TrackSettings = field(default_factory=TrackSettings)

    @classmethod
    def load(cls, config_path: Path | None = None) -> Settings:
        """Load settings from config file.

        If config_path is provided, load from that specific file.
        Otherwise, search standard config locations.

        Returns Settings with defaults if no config file exists.
        """
        if config_path:
            return cls._from_file(config_path)

        for path in cls._config_paths():
            if path.exists():
                return cls._from_file(path)

        return cls()  # Return defaults

    @classmethod
    def _config_paths(cls) -> list[Path]:
        """Get standard config file paths in order of precedence."""
        paths: list[Path] = []

        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            if appdata:
                base = Path(appdata) / "de-dolby"
                paths.extend([base / "config.toml", base / "config.yaml"])
        else:
            home = Path.home()
            base = home / ".config" / "de-dolby"
            paths.extend([base / "config.toml", base / "config.yaml"])

        return paths

    @classmethod
    def _from_file(cls, path: Path) -> Settings:
        """Load settings from a TOML or YAML file."""
        content = path.read_text(encoding="utf-8")

        if path.suffix in (".toml", ".TOML"):
            data = cls._parse_toml(content)
        elif path.suffix in (".yaml", ".yml", ".YAML", ".YML"):
            data = cls._parse_yaml(content)
        else:
            raise ValueError(f"Unsupported config file format: {path.suffix}")

        return cls._from_dict(data)

    @classmethod
    def _parse_toml(cls, content: str) -> dict[str, Any]:
        """Parse TOML content."""
        try:
            import tomllib

            return tomllib.loads(content)  # type: ignore[no-any-return]
        except ImportError:
            try:
                import tomli

                return tomli.loads(content)  # type: ignore[no-any-return]
            except ImportError as err:
                raise ImportError(
                    "TOML support requires Python 3.11+ or 'tomli' package. "
                    "Install with: pip install de-dolby[toml]"
                ) from err

    @classmethod
    def _parse_yaml(cls, content: str) -> dict[str, Any]:
        """Parse YAML content."""
        try:
            import yaml  # type: ignore[import-untyped]

            return yaml.safe_load(content) or {}
        except ImportError as err:
            raise ImportError(
                "YAML support requires 'pyyaml' package. Install with: pip install de-dolby[yaml]"
            ) from err

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Settings:
        """Create Settings from a dictionary."""
        settings = cls()

        # Handle [defaults] section or top-level keys
        defaults = data.get("defaults", data)

        for key in [
            "encoder",
            "quality",
            "crf",
            "bitrate",
            "output_dir",
            "temp_dir",
            "workers",
            "verbose",
            "force",
        ]:
            if key in defaults:
                value = defaults[key]
                # Expand ~ in paths
                if key in ("output_dir", "temp_dir") and isinstance(value, str):
                    value = os.path.expanduser(value)
                setattr(settings, key, value)

        # Handle [tool_paths] section
        if "tool_paths" in data:
            tool_data = data["tool_paths"]
            settings.tool_paths = ToolPaths(
                ffmpeg=tool_data.get("ffmpeg"),
                dovi_tool=tool_data.get("dovi_tool"),
                mkvmerge=tool_data.get("mkvmerge"),
            )

        # Handle [tracks] section
        if "tracks" in data:
            track_data = data["tracks"]
            settings.tracks = TrackSettings(
                audio_languages=track_data.get("audio_languages", []),
                skip_subtitles=track_data.get("skip_subtitles", False),
            )

        return settings

    def merge_with_args(self, args: Any) -> dict[str, Any]:
        """Merge config file values with CLI arguments.

        CLI arguments take precedence over config file values.
        Only overrides args that weren't explicitly set by user.

        Returns a dict of merged values suitable for ConvertOptions.
        """
        merged: dict[str, Any] = {}

        # Map of config field -> arg name
        field_map = {
            "encoder": "encoder",
            "quality": "quality",
            "crf": "crf",
            "bitrate": "bitrate",
            "temp_dir": "temp_dir",
            "verbose": "verbose",
            "force": "force",
        }

        for config_key, arg_key in field_map.items():
            config_value = getattr(self, config_key)
            arg_value = getattr(args, arg_key, None)

            # Use arg if provided, otherwise use config value
            if arg_value is not None:
                merged[config_key] = arg_value
            elif config_value is not None:
                merged[config_key] = config_value
            else:
                merged[config_key] = arg_value

        # Handle tool_paths separately
        merged["ffmpeg"] = getattr(args, "ffmpeg", None) or self.tool_paths.ffmpeg
        merged["dovi_tool"] = getattr(args, "dovi_tool", None) or self.tool_paths.dovi_tool
        merged["mkvmerge"] = getattr(args, "mkvmerge", None) or self.tool_paths.mkvmerge

        return merged

    def get_output_path(self, input_path: str, cli_output: str | None = None) -> str | None:
        """Determine output path from config or CLI.

        Priority:
        1. CLI -o/--output value
        2. Config output_dir + derived filename
        3. Default derived name in input directory
        """
        if cli_output:
            return cli_output

        if self.output_dir:
            from de_dolby.cli import derive_output_name

            filename = Path(derive_output_name(input_path)).name
            return str(Path(self.output_dir).expanduser() / filename)

        return None

    def ensure_config_dir(self) -> Path:
        """Ensure the config directory exists, creating it if necessary."""
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            if not appdata:
                raise RuntimeError("APPDATA environment variable not set")
            config_dir = Path(appdata) / "de-dolby"
        else:
            config_dir = Path.home() / ".config" / "de-dolby"

        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    def write_example(self, path: Path | None = None) -> Path:
        """Write an example config file.

        Returns the path to the written file.
        """
        if path is None:
            config_dir = self.ensure_config_dir()
            path = config_dir / "config.toml"

        example = """# de-dolby configuration file
# Place at one of these locations:
#   - ~/.config/de-dolby/config.toml (Linux/macOS)
#   - %APPDATA%\\de-dolby\\config.toml (Windows)

[defaults]
encoder = "auto"        # auto, hevc_amf, libx265, av1_amf, libsvtav1, copy
quality = "balanced"    # fast, balanced, quality
crf = 18                # CRF for libx265 (0-51, lower is better)
bitrate = "40M"         # Target bitrate for hardware encoders
output_dir = "~/Videos/HDR10"
temp_dir = "/tmp"
workers = 4
verbose = false
force = false

[tool_paths]
# Optional: override tool paths if not in PATH
# ffmpeg = "C:/Tools/ffmpeg.exe"
# dovi_tool = "C:/Tools/dovi_tool.exe"
# mkvmerge = "C:/Tools/mkvmerge.exe"

[tracks]
# Keep only these audio languages (empty = keep all)
audio_languages = ["eng", "jpn"]
# Skip subtitle tracks entirely
skip_subtitles = false
"""
        path.write_text(example, encoding="utf-8")
        return path


def find_config_file() -> Path | None:
    """Find the first existing config file in standard locations."""
    for path in Settings._config_paths():
        if path.exists():
            return path
    return None
