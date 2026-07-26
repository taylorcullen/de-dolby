import pytest

from de_dolby.settings import (
    ConversionSettings,
    EffectiveSettings,
    SettingsError,
    default_config_path,
    load_settings_config,
    merge_settings,
    settings_from_mapping,
)


def test_empty_layers_preserve_backward_compatible_defaults():
    assert merge_settings() == EffectiveSettings()


def test_layers_merge_from_defaults_through_cli_precedence():
    effective = merge_settings(
        ("config.defaults", ConversionSettings(quality="fast", encoder="libx265")),
        ("preset.cpu", ConversionSettings(quality="quality", crf=20)),
        ("CLI", ConversionSettings(crf=17, sample_seconds=30)),
    )
    assert effective.encoder == "libx265"
    assert effective.quality == "quality"
    assert effective.crf == 17
    assert effective.sample_seconds == 30


def test_none_does_not_clear_a_lower_precedence_value():
    effective = merge_settings(
        ("config.defaults", ConversionSettings(temp_dir="D:/scratch")),
        ("CLI", ConversionSettings(temp_dir=None)),
    )
    assert effective.temp_dir == "D:/scratch"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"typo": True}, "config.toml.typo"),
        ({"quality": "maximum"}, "config.toml.quality"),
        ({"crf": 52}, "config.toml.crf"),
        ({"sample_seconds": 0}, "config.toml.sample_seconds"),
        ({"unsafe_skip_validation": "yes"}, "config.toml.unsafe_skip_validation"),
    ],
)
def test_mapping_validation_is_source_aware(values, message):
    with pytest.raises(SettingsError, match=message.replace(".", r"\.")):
        settings_from_mapping(values, source="config.toml")


def test_invalid_merged_combination_blames_value_source():
    with pytest.raises(SettingsError, match=r"preset\.gpu\.crf"):
        merge_settings(
            ("config.defaults", ConversionSettings(encoder="hevc_nvenc")),
            ("preset.gpu", ConversionSettings(crf=18)),
        )


def test_missing_default_config_returns_empty_without_reading_user_state(tmp_path):
    config = load_settings_config(
        environ={"XDG_CONFIG_HOME": str(tmp_path)},
        platform="linux",
        home=tmp_path / "home",
    )
    assert config.defaults == ConversionSettings()
    assert config.presets == {}
    assert config.path == tmp_path / "de-dolby" / "config.toml"


def test_explicit_missing_config_is_actionable(tmp_path):
    path = tmp_path / "missing.toml"
    with pytest.raises(SettingsError, match="does not exist"):
        load_settings_config(path)


def test_versioned_toml_loads_defaults_and_named_presets(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        """
schema_version = 1
[defaults]
quality = "fast"
[presets.cpu]
encoder = "libx265"
crf = 18
[presets.sample]
sample_seconds = 30
""",
        encoding="utf-8",
    )
    config = load_settings_config(path)
    assert config.defaults.quality == "fast"
    assert config.presets["cpu"].crf == 18
    assert config.presets["sample"].sample_seconds == 30


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("schema_version = 2", "schema_version"),
        ("schema_version = 1\nmystery = true", "unknown top-level"),
        ("schema_version = 1\n[defaults]\nunknown = 1", r"defaults\.unknown"),
        ("schema_version = 1\n[presets.cpu]\ncrf = 99", r"presets\.cpu\.crf"),
        ("not valid toml", "could not parse TOML"),
    ],
)
def test_toml_errors_include_source_path(tmp_path, content, message):
    path = tmp_path / "broken.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError, match=message) as error:
        load_settings_config(path)
    assert str(path) in str(error.value)


def test_default_paths_are_platform_specific(tmp_path):
    assert default_config_path(
        environ={"APPDATA": str(tmp_path / "roaming")},
        platform="win32",
        home=tmp_path,
    ) == tmp_path / "roaming" / "de-dolby" / "config.toml"
    assert default_config_path(
        environ={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        platform="linux",
        home=tmp_path,
    ) == tmp_path / "xdg" / "de-dolby" / "config.toml"
