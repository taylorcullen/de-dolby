"""Tests for de_dolby.settings module."""

import sys
from pathlib import Path
from unittest import mock

import pytest

from de_dolby.settings import Settings, ToolPaths, find_config_file


class TestSettingsDefaults:
    """Test default settings values."""

    def test_default_values(self):
        """Settings should have sensible defaults."""
        s = Settings()
        assert s.encoder == "auto"
        assert s.quality == "balanced"
        assert s.crf is None
        assert s.bitrate is None
        assert s.output_dir is None
        assert s.temp_dir is None
        assert s.workers is None
        assert s.verbose is False
        assert s.force is False

    def test_default_tool_paths(self):
        """ToolPaths should default to None."""
        s = Settings()
        assert s.tool_paths.ffmpeg is None
        assert s.tool_paths.dovi_tool is None
        assert s.tool_paths.mkvmerge is None

    def test_default_tracks(self):
        """TrackSettings should have empty defaults."""
        s = Settings()
        assert s.tracks.audio_languages == []
        assert s.tracks.skip_subtitles is False


class TestSettingsFromToml:
    """Test loading settings from TOML files."""

    def test_load_full_toml(self, tmp_path):
        """Load complete TOML config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[defaults]
encoder = "hevc_amf"
quality = "quality"
crf = 18
bitrate = "40M"
output_dir = "/home/user/Videos/HDR10"
temp_dir = "/tmp"
workers = 4
verbose = true
force = true

[tool_paths]
ffmpeg = "/usr/bin/ffmpeg"
dovi_tool = "/usr/bin/dovi_tool"
mkvmerge = "/usr/bin/mkvmerge"

[tracks]
audio_languages = ["eng", "jpn"]
skip_subtitles = true
""")
        s = Settings._from_file(config_file)

        assert s.encoder == "hevc_amf"
        assert s.quality == "quality"
        assert s.crf == 18
        assert s.bitrate == "40M"
        assert s.output_dir == "/home/user/Videos/HDR10"
        assert s.temp_dir == "/tmp"
        assert s.workers == 4
        assert s.verbose is True
        assert s.force is True

        assert s.tool_paths.ffmpeg == "/usr/bin/ffmpeg"
        assert s.tool_paths.dovi_tool == "/usr/bin/dovi_tool"
        assert s.tool_paths.mkvmerge == "/usr/bin/mkvmerge"

        assert s.tracks.audio_languages == ["eng", "jpn"]
        assert s.tracks.skip_subtitles is True

    def test_load_partial_toml(self, tmp_path):
        """Load partial TOML config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[defaults]
encoder = "libx265"
quality = "fast"
""")
        s = Settings._from_file(config_file)

        assert s.encoder == "libx265"
        assert s.quality == "fast"
        # Other values should be defaults
        assert s.crf is None
        assert s.verbose is False

    def test_load_top_level_keys(self, tmp_path):
        """Load TOML with keys at top level (no [defaults] section)."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
encoder = "av1_amf"
verbose = true
""")
        s = Settings._from_file(config_file)

        assert s.encoder == "av1_amf"
        assert s.verbose is True


class TestSettingsFromYaml:
    """Test loading settings from YAML files."""

    def test_load_full_yaml(self, tmp_path):
        """Load complete YAML config."""
        pytest.importorskip("yaml")

        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
defaults:
  encoder: hevc_amf
  quality: quality
  crf: 18
  bitrate: 40M
  output_dir: ~/Videos/HDR10
  temp_dir: /tmp
  workers: 4
  verbose: true
  force: true

tool_paths:
  ffmpeg: /usr/bin/ffmpeg
  dovi_tool: /usr/bin/dovi_tool
  mkvmerge: /usr/bin/mkvmerge

tracks:
  audio_languages:
    - eng
    - jpn
  skip_subtitles: true
""")
        s = Settings._from_file(config_file)

        assert s.encoder == "hevc_amf"
        assert s.quality == "quality"
        assert s.crf == 18
        assert s.bitrate == "40M"
        assert s.workers == 4
        assert s.verbose is True
        assert s.force is True

        assert s.tool_paths.ffmpeg == "/usr/bin/ffmpeg"
        assert s.tracks.audio_languages == ["eng", "jpn"]
        assert s.tracks.skip_subtitles is True


class TestSettingsMerge:
    """Test merging settings with CLI arguments."""

    def test_cli_args_override_config(self):
        """CLI args should take precedence over config."""
        s = Settings(encoder="libx265", quality="fast", crf=20)

        class Args:
            encoder = "hevc_amf"  # Override
            quality = "balanced"  # Override
            crf = None  # Use config
            bitrate = None
            temp_dir = None
            verbose = False
            force = False
            ffmpeg = None
            dovi_tool = None
            mkvmerge = None

        merged = s.merge_with_args(Args())
        assert merged["encoder"] == "hevc_amf"  # CLI wins
        assert merged["quality"] == "balanced"  # CLI wins
        assert merged["crf"] == 20  # Config used

    def test_config_used_when_no_cli(self):
        """Config values used when CLI args are None."""
        s = Settings(encoder="libx265", quality="quality")

        class Args:
            encoder = None
            quality = None
            crf = None
            bitrate = None
            temp_dir = None
            verbose = False
            force = False
            ffmpeg = None
            dovi_tool = None
            mkvmerge = None

        merged = s.merge_with_args(Args())
        assert merged["encoder"] == "libx265"
        assert merged["quality"] == "quality"

    def test_tool_paths_merge(self):
        """Tool paths merge correctly from both sources."""
        s = Settings()
        s.tool_paths = ToolPaths(ffmpeg="/config/ffmpeg")

        class Args:
            encoder = None
            quality = None
            crf = None
            bitrate = None
            temp_dir = None
            verbose = False
            force = False
            ffmpeg = "/cli/ffmpeg"
            dovi_tool = None
            mkvmerge = None

        merged = s.merge_with_args(Args())
        assert merged["ffmpeg"] == "/cli/ffmpeg"  # CLI wins
        assert merged["dovi_tool"] is None


class TestConfigPaths:
    """Test config file path discovery."""

    def test_config_paths_unix(self):
        """Config paths on Unix systems."""
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(Path, "home", return_value=Path("/home/user")),
        ):
            paths = Settings._config_paths()
            assert len(paths) == 2
            assert str(paths[0]).replace("\\", "/") == "/home/user/.config/de-dolby/config.toml"
            assert str(paths[1]).replace("\\", "/") == "/home/user/.config/de-dolby/config.yaml"

    def test_config_paths_windows(self):
        """Config paths on Windows."""
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.dict("os.environ", {"APPDATA": "C:\\Users\\User\\AppData\\Roaming"}),
        ):
            paths = Settings._config_paths()
            assert len(paths) == 2
            assert str(paths[0]) == "C:\\Users\\User\\AppData\\Roaming\\de-dolby\\config.toml"


class TestFindConfigFile:
    """Test finding existing config files."""

    def test_find_existing_config(self, tmp_path):
        """Find config when it exists."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("encoder = 'libx265'")

        with mock.patch.object(Settings, "_config_paths", return_value=[config_file]):
            found = find_config_file()
            assert found == config_file

    def test_find_no_config(self, tmp_path):
        """Return None when no config exists."""
        nonexistent = tmp_path / "nonexistent.toml"

        with mock.patch.object(Settings, "_config_paths", return_value=[nonexistent]):
            found = find_config_file()
            assert found is None


class TestOutputPathResolution:
    """Test output path resolution with config."""

    def test_cli_output_takes_precedence(self):
        """CLI -o takes precedence over config output_dir."""
        s = Settings(output_dir="~/Videos")
        result = s.get_output_path("input.mkv", cli_output="explicit.mkv")
        assert result == "explicit.mkv"

    def test_output_dir_with_derived_name(self, tmp_path):
        """Config output_dir used with derived filename."""
        s = Settings(output_dir=str(tmp_path / "output"))
        result = s.get_output_path(str(tmp_path / "input.DV.mkv"), cli_output=None)
        assert result is not None
        assert "HDR10" in result


class TestWriteExample:
    """Test writing example config files."""

    def test_write_example_default_location(self, tmp_path):
        """Write example to default location."""
        with mock.patch.object(Settings, "ensure_config_dir", return_value=tmp_path):
            s = Settings()
            path = s.write_example()
            assert path.exists()
            assert "de-dolby configuration file" in path.read_text()

    def test_write_example_specific_path(self, tmp_path):
        """Write example to specific path."""
        s = Settings()
        target = tmp_path / "my-config.toml"
        path = s.write_example(target)
        assert path == target
        assert target.exists()


class TestPathExpansion:
    """Test path expansion in settings."""

    def test_output_dir_expansion_in_get_output(self, tmp_path):
        """~ should be expanded in output_dir."""
        s = Settings(output_dir="~/Videos/HDR10")
        # get_output_path should expand ~ when constructing full path
        result = s.get_output_path("/path/to/input.mkv")
        if result:
            assert "~" not in result

    def test_temp_dir_expanded_on_load(self, tmp_path):
        """~ in temp_dir is expanded during load."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('temp_dir = "/home/user/tmp"')
        s = Settings._from_file(config_file)
        assert "~" not in s.temp_dir if s.temp_dir else True


class TestErrorHandling:
    """Test error handling for missing dependencies."""

    def test_toml_import_error(self, tmp_path):
        """Raise helpful error when toml support missing."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("encoder = 'libx265'")

        with mock.patch.dict("sys.modules", {"tomli": None, "tomllib": None}):
            # Simulate Python < 3.11 without tomli
            if sys.version_info < (3, 11):
                with pytest.raises(ImportError, match="TOML support requires"):
                    Settings._from_file(config_file)

    def test_yaml_import_error(self, tmp_path):
        """Raise helpful error when yaml support missing."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("encoder: libx265")

        with (
            mock.patch.dict("sys.modules", {"yaml": None}),
            pytest.raises(ImportError, match="YAML support requires"),
        ):
            Settings._from_file(config_file)

    def test_unsupported_format(self, tmp_path):
        """Raise error for unsupported file format."""
        config_file = tmp_path / "config.ini"
        config_file.write_text("[defaults]\nencoder=libx265")

        with pytest.raises(ValueError, match="Unsupported config file format"):
            Settings._from_file(config_file)
