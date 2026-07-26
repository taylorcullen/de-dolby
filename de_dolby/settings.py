"""Typed conversion settings and deterministic precedence merging."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import os
from pathlib import Path
import sys
from typing import Mapping

from de_dolby.codecs import ENCODERS


class SettingsError(ValueError):
    """A settings layer contains an unknown or invalid value."""


@dataclass(frozen=True)
class ConversionSettings:
    """A partial settings layer; ``None`` means no value was supplied."""

    encoder: str | None = None
    quality: str | None = None
    crf: int | None = None
    bitrate: str | None = None
    sample_seconds: int | None = None
    temp_dir: str | None = None
    timeout_minutes: int | None = None
    unsafe_skip_validation: bool | None = None


@dataclass(frozen=True)
class EffectiveSettings:
    encoder: str = "auto"
    quality: str = "balanced"
    crf: int | None = None
    bitrate: str | None = None
    sample_seconds: int | None = None
    temp_dir: str | None = None
    timeout_minutes: int | None = None
    unsafe_skip_validation: bool = False


@dataclass(frozen=True)
class SettingsConfig:
    defaults: ConversionSettings = ConversionSettings()
    presets: dict[str, ConversionSettings] = field(default_factory=dict)
    path: Path | None = None


_SETTING_NAMES = frozenset(item.name for item in fields(ConversionSettings))


def settings_from_mapping(
    values: Mapping[str, object], *, source: str
) -> ConversionSettings:
    """Parse one layer and report its source in all errors."""
    unknown = sorted(set(values) - _SETTING_NAMES)
    if unknown:
        raise SettingsError(f"{source}.{unknown[0]}: unknown setting")
    try:
        settings = ConversionSettings(**values)
    except TypeError as exc:
        raise SettingsError(f"{source}: {exc}") from exc
    _validate_values(settings, source)
    return settings


def default_config_path(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    platform_name = sys.platform if platform is None else platform
    home_path = Path.home() if home is None else home
    if platform_name == "win32":
        base = Path(environment.get("APPDATA", home_path / "AppData" / "Roaming"))
    elif platform_name == "darwin":
        base = home_path / "Library" / "Application Support"
    else:
        base = Path(environment.get("XDG_CONFIG_HOME", home_path / ".config"))
    return base / "de-dolby" / "config.toml"


def load_settings_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> SettingsConfig:
    """Load versioned TOML, treating an absent platform default as empty."""
    explicit = path is not None
    source = Path(path) if explicit else default_config_path(
        environ=environ, platform=platform, home=home
    )
    if not source.exists():
        if explicit:
            raise SettingsError(f"{source}: configuration file does not exist")
        return SettingsConfig(path=source)
    try:
        try:
            import tomllib
        except ImportError:  # pragma: no cover - exercised on Python 3.10
            import tomli as tomllib
        document = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SettingsError(f"{source}: could not parse TOML: {exc}") from exc
    allowed_sections = {"schema_version", "defaults", "presets"}
    unknown = sorted(set(document) - allowed_sections)
    if unknown:
        raise SettingsError(f"{source}: unknown top-level key {unknown[0]!r}")
    if document.get("schema_version") != 1:
        raise SettingsError(
            f"{source}.schema_version: expected 1; migrate or recreate the config"
        )
    defaults = document.get("defaults", {})
    presets = document.get("presets", {})
    if not isinstance(defaults, dict):
        raise SettingsError(f"{source}.defaults: expected a table")
    if not isinstance(presets, dict):
        raise SettingsError(f"{source}.presets: expected a table")
    parsed_presets: dict[str, ConversionSettings] = {}
    for name, values in presets.items():
        if not isinstance(values, dict):
            raise SettingsError(f"{source}.presets.{name}: expected a table")
        parsed_presets[name] = settings_from_mapping(
            values, source=f"{source}.presets.{name}"
        )
    return SettingsConfig(
        defaults=settings_from_mapping(defaults, source=f"{source}.defaults"),
        presets=parsed_presets,
        path=source,
    )


def merge_settings(
    *layers: tuple[str, ConversionSettings],
) -> EffectiveSettings:
    """Merge low-to-high precedence layers into validated effective settings."""
    merged = {
        item.name: getattr(EffectiveSettings(), item.name)
        for item in fields(EffectiveSettings)
    }
    sources = {name: "built-in defaults" for name in merged}
    for source, layer in layers:
        _validate_values(layer, source)
        for item in fields(layer):
            value = getattr(layer, item.name)
            if value is not None:
                merged[item.name] = value
                sources[item.name] = source
    effective = EffectiveSettings(**merged)
    _validate_effective(effective, sources)
    return effective


def _validate_values(settings: ConversionSettings, source: str) -> None:
    if settings.encoder is not None:
        allowed = {"auto", "copy", *ENCODERS}
        if not isinstance(settings.encoder, str) or settings.encoder not in allowed:
            raise SettingsError(f"{source}.encoder: unsupported encoder")
    if settings.quality is not None and settings.quality not in {
        "fast", "balanced", "quality"
    }:
        raise SettingsError(f"{source}.quality: expected fast, balanced, or quality")
    if settings.crf is not None and (
        not isinstance(settings.crf, int)
        or isinstance(settings.crf, bool)
        or not 0 <= settings.crf <= 51
    ):
        raise SettingsError(f"{source}.crf: expected an integer from 0 to 51")
    for name in ("sample_seconds", "timeout_minutes"):
        value = getattr(settings, name)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
        ):
            raise SettingsError(f"{source}.{name}: expected a positive integer")
    if settings.unsafe_skip_validation is not None and not isinstance(
        settings.unsafe_skip_validation, bool
    ):
        raise SettingsError(
            f"{source}.unsafe_skip_validation: expected true or false"
        )


def _validate_effective(
    settings: EffectiveSettings, sources: Mapping[str, str]
) -> None:
    if settings.crf is not None and settings.encoder not in {"auto", "libx265"}:
        raise SettingsError(
            f"{sources['crf']}.crf: CRF requires encoder 'auto' or 'libx265'"
        )
    if settings.bitrate is not None and settings.encoder == "libx265":
        raise SettingsError(
            f"{sources['bitrate']}.bitrate: bitrate is not supported by libx265"
        )
