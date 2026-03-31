"""Tests for de_dolby.watch module."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from de_dolby.watch import (
    ProcessedFile,
    WatchOptions,
    WatchSession,
    WatchState,
    _find_files,
    _get_output_path,
    _get_state_path,
    _move_original,
    _wait_for_file_stable,
    create_watch_options_from_args,
    load_watch_state,
    save_watch_state,
    watch,
)


class TestProcessedFile:
    """Tests for ProcessedFile dataclass."""

    def test_to_dict(self):
        """Test serialization to dict."""
        pf = ProcessedFile(
            input="/path/to/input.mkv",
            output="/path/to/output.HDR10.mkv",
            size=15000000000,
            mtime=1705312800.0,
            converted_at="2024-01-15T10:30:00+00:00",
        )
        d = pf.to_dict()
        assert d["input"] == "/path/to/input.mkv"
        assert d["output"] == "/path/to/output.HDR10.mkv"
        assert d["size"] == 15000000000
        assert d["mtime"] == 1705312800.0
        assert d["converted_at"] == "2024-01-15T10:30:00+00:00"

    def test_from_dict(self):
        """Test deserialization from dict."""
        d = {
            "input": "/path/to/input.mkv",
            "output": "/path/to/output.HDR10.mkv",
            "size": 15000000000,
            "mtime": 1705312800.0,
            "converted_at": "2024-01-15T10:30:00+00:00",
        }
        pf = ProcessedFile.from_dict(d)
        assert pf.input == "/path/to/input.mkv"
        assert pf.output == "/path/to/output.HDR10.mkv"
        assert pf.size == 15000000000
        assert pf.mtime == 1705312800.0
        assert pf.converted_at == "2024-01-15T10:30:00+00:00"


class TestWatchState:
    """Tests for WatchState dataclass."""

    def test_to_dict(self):
        """Test serialization to dict."""
        pf = ProcessedFile(
            input="/path/to/input.mkv",
            output="/path/to/output.HDR10.mkv",
            size=15000000000,
            mtime=1705312800.0,
            converted_at="2024-01-15T10:30:00+00:00",
        )
        state = WatchState(watch_path="/media/dv_movies", processed_files=[pf])
        d = state.to_dict()
        assert d["watch_path"] == "/media/dv_movies"
        assert len(d["processed_files"]) == 1
        assert d["processed_files"][0]["input"] == "/path/to/input.mkv"

    def test_from_dict(self):
        """Test deserialization from dict."""
        d = {
            "watch_path": "/media/dv_movies",
            "processed_files": [
                {
                    "input": "/path/to/input.mkv",
                    "output": "/path/to/output.HDR10.mkv",
                    "size": 15000000000,
                    "mtime": 1705312800.0,
                    "converted_at": "2024-01-15T10:30:00+00:00",
                }
            ],
        }
        state = WatchState.from_dict(d)
        assert state.watch_path == "/media/dv_movies"
        assert len(state.processed_files) == 1
        assert state.processed_files[0].input == "/path/to/input.mkv"

    def test_is_processed_true(self):
        """Test is_processed returns True for matching file."""
        pf = ProcessedFile(
            input="/path/to/input.mkv",
            output="/path/to/output.HDR10.mkv",
            size=15000000000,
            mtime=1705312800.0,
            converted_at="2024-01-15T10:30:00+00:00",
        )
        state = WatchState(watch_path="/media/dv_movies", processed_files=[pf])
        assert state.is_processed("/path/to/input.mkv", 15000000000, 1705312800.0) is True

    def test_is_processed_false_different_size(self):
        """Test is_processed returns False if size differs."""
        pf = ProcessedFile(
            input="/path/to/input.mkv",
            output="/path/to/output.HDR10.mkv",
            size=15000000000,
            mtime=1705312800.0,
            converted_at="2024-01-15T10:30:00+00:00",
        )
        state = WatchState(watch_path="/media/dv_movies", processed_files=[pf])
        assert state.is_processed("/path/to/input.mkv", 16000000000, 1705312800.0) is False

    def test_is_processed_false_different_path(self):
        """Test is_processed returns False if path differs."""
        pf = ProcessedFile(
            input="/path/to/input.mkv",
            output="/path/to/output.HDR10.mkv",
            size=15000000000,
            mtime=1705312800.0,
            converted_at="2024-01-15T10:30:00+00:00",
        )
        state = WatchState(watch_path="/media/dv_movies", processed_files=[pf])
        assert state.is_processed("/path/to/other.mkv", 15000000000, 1705312800.0) is False

    def test_add_processed(self):
        """Test adding a processed file."""
        state = WatchState(watch_path="/media/dv_movies")
        state.add_processed(
            "/path/to/input.mkv",
            "/path/to/output.HDR10.mkv",
            15000000000,
            1705312800.0,
        )
        assert len(state.processed_files) == 1
        assert state.processed_files[0].input == "/path/to/input.mkv"

    def test_add_processed_replaces_existing(self):
        """Test adding a processed file replaces existing entry."""
        state = WatchState(watch_path="/media/dv_movies")
        state.add_processed(
            "/path/to/input.mkv",
            "/path/to/output.HDR10.mkv",
            15000000000,
            1705312800.0,
        )
        state.add_processed(
            "/path/to/input.mkv",
            "/path/to/output2.HDR10.mkv",
            16000000000,
            1705312900.0,
        )
        assert len(state.processed_files) == 1
        assert state.processed_files[0].output == "/path/to/output2.HDR10.mkv"
        assert state.processed_files[0].size == 16000000000


class TestWatchStatePersistence:
    """Tests for loading and saving watch state."""

    def test_get_state_path(self, monkeypatch, tmp_path):
        """Test state path generation."""
        if os.name == "nt":
            # Use temp path to avoid permission issues
            test_appdata = str(tmp_path / "AppData" / "Roaming")
            monkeypatch.setenv("APPDATA", test_appdata)
            path = _get_state_path()
            assert "de-dolby" in str(path)
            assert path.name == "watch_state.json"
        else:
            monkeypatch.setenv("HOME", str(tmp_path))
            path = _get_state_path()
            assert ".config/de-dolby" in str(path)
            assert path.name == "watch_state.json"

    def test_load_watch_state_new(self, tmp_path, monkeypatch):
        """Test loading state when file doesn't exist."""
        monkeypatch.setattr(
            "de_dolby.watch._get_state_path", lambda: tmp_path / "watch_state.json"
        )
        state = load_watch_state("/media/movies")
        assert state.watch_path == "/media/movies"
        assert state.processed_files == []

    def test_load_watch_state_existing(self, tmp_path, monkeypatch):
        """Test loading existing state file."""
        state_file = tmp_path / "watch_state.json"
        data = {
            "watch_path": "/media/movies",
            "processed_files": [
                {
                    "input": "/media/movies/test.mkv",
                    "output": "/media/movies/test.HDR10.mkv",
                    "size": 1000000,
                    "mtime": 1705312800.0,
                    "converted_at": "2024-01-15T10:30:00+00:00",
                }
            ],
        }
        state_file.write_text(json.dumps(data))
        monkeypatch.setattr("de_dolby.watch._get_state_path", lambda: state_file)

        state = load_watch_state("/media/movies")
        assert state.watch_path == "/media/movies"
        assert len(state.processed_files) == 1

    def test_load_watch_state_different_path(self, tmp_path, monkeypatch):
        """Test loading state with different watch path creates new state."""
        state_file = tmp_path / "watch_state.json"
        data = {
            "watch_path": "/media/old_movies",
            "processed_files": [
                {
                    "input": "/media/old_movies/test.mkv",
                    "output": "/media/old_movies/test.HDR10.mkv",
                    "size": 1000000,
                    "mtime": 1705312800.0,
                    "converted_at": "2024-01-15T10:30:00+00:00",
                }
            ],
        }
        state_file.write_text(json.dumps(data))
        monkeypatch.setattr("de_dolby.watch._get_state_path", lambda: state_file)

        state = load_watch_state("/media/new_movies")
        # Should create fresh state since paths don't match
        assert state.watch_path == "/media/new_movies"
        assert state.processed_files == []

    def test_save_watch_state(self, tmp_path, monkeypatch):
        """Test saving state to file."""
        state_file = tmp_path / "watch_state.json"
        monkeypatch.setattr("de_dolby.watch._get_state_path", lambda: state_file)

        state = WatchState(watch_path="/media/movies")
        state.add_processed(
            "/media/movies/test.mkv",
            "/media/movies/test.HDR10.mkv",
            1000000,
            1705312800.0,
        )
        save_watch_state(state)

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["watch_path"] == "/media/movies"
        assert len(data["processed_files"]) == 1


class TestFindFiles:
    """Tests for file finding functions."""

    def test_find_files_basic(self, tmp_path):
        """Test finding files with basic pattern."""
        (tmp_path / "test1.mkv").write_text("dummy")
        (tmp_path / "test2.mkv").write_text("dummy")
        (tmp_path / "test.txt").write_text("dummy")

        files = _find_files(str(tmp_path), "*.mkv", recursive=False)
        assert len(files) == 2
        assert all(f.suffix == ".mkv" for f in files)

    def test_find_files_recursive(self, tmp_path):
        """Test finding files recursively."""
        (tmp_path / "test1.mkv").write_text("dummy")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "test2.mkv").write_text("dummy")

        files_non_recursive = _find_files(str(tmp_path), "*.mkv", recursive=False)
        assert len(files_non_recursive) == 1

        files_recursive = _find_files(str(tmp_path), "*.mkv", recursive=True)
        assert len(files_recursive) == 2

    def test_find_files_nonexistent_dir(self, tmp_path):
        """Test finding files in non-existent directory."""
        files = _find_files(str(tmp_path / "nonexistent"), "*.mkv", recursive=False)
        assert files == []


class TestWaitForFileStable:
    """Tests for file stability checking."""

    def test_wait_for_file_stable_zero_delay(self, tmp_path):
        """Test with zero delay returns immediately."""
        test_file = tmp_path / "test.mkv"
        test_file.write_text("dummy")
        result = _wait_for_file_stable(test_file, 0, verbose=False)
        assert result is True

    def test_wait_for_file_stable_file_disappears(self, tmp_path):
        """Test returns False if file disappears."""
        test_file = tmp_path / "test.mkv"
        test_file.write_text("dummy")

        # Delete file during wait
        def delete_file():
            import time

            time.sleep(0.1)
            test_file.unlink()

        import threading

        t = threading.Thread(target=delete_file)
        t.start()
        result = _wait_for_file_stable(test_file, 2, verbose=False)
        t.join()

        assert result is False


class TestOutputPath:
    """Tests for output path determination."""

    def test_get_output_path_same_dir(self):
        """Test output path in same directory."""
        input_path = Path("/media/movies/movie.DV.mkv")
        output_path = _get_output_path(input_path, None)
        assert "HDR10" in output_path
        assert output_path.endswith(".mkv")

    def test_get_output_path_custom_dir(self, tmp_path):
        """Test output path in custom directory."""
        input_path = tmp_path / "movie.DV.mkv"
        output_dir = tmp_path / "output"
        output_path = _get_output_path(input_path, str(output_dir))
        assert str(output_dir) in output_path
        assert "HDR10" in output_path
        assert output_dir.exists()  # Directory should be created


class TestMoveOriginal:
    """Tests for moving original files."""

    def test_move_original_success(self, tmp_path):
        """Test successful move of original file."""
        test_file = tmp_path / "test.mkv"
        test_file.write_text("dummy content")

        new_path = _move_original(test_file)

        assert new_path is not None
        assert new_path.parent.name == "original"
        assert not test_file.exists()  # Original should be gone
        assert new_path.exists()  # New location should exist

    def test_move_original_nonexistent_file(self, tmp_path):
        """Test moving non-existent file returns None."""
        test_file = tmp_path / "nonexistent.mkv"
        new_path = _move_original(test_file)
        assert new_path is None


class TestWatchOptions:
    """Tests for WatchOptions dataclass."""

    def test_default_options(self):
        """Test default watch options."""
        opts = WatchOptions(watch_path="/media/movies")
        assert opts.watch_path == "/media/movies"
        assert opts.output_dir is None
        assert opts.recursive is False
        assert opts.interval == 5
        assert opts.delay == 10
        assert opts.pattern == "*.mkv"
        assert opts.move_original is False
        assert opts.reprocess is False


class TestWatchSession:
    """Tests for WatchSession class."""

    def test_session_initialization(self, tmp_path):
        """Test session initialization."""
        opts = WatchOptions(watch_path=str(tmp_path))
        session = WatchSession(opts)
        assert session.options == opts
        assert session.running is False
        assert session._shutdown_requested is False

    def test_should_process_new_file(self, tmp_path):
        """Test should_process returns True for new file."""
        test_file = tmp_path / "test.mkv"
        test_file.write_text("dummy")

        opts = WatchOptions(watch_path=str(tmp_path))
        session = WatchSession(opts)

        assert session._should_process(test_file) is True

    def test_should_process_already_processed(self, tmp_path):
        """Test should_process returns False for already processed file."""
        test_file = tmp_path / "test.mkv"
        test_file.write_text("dummy")
        stat = test_file.stat()

        opts = WatchOptions(watch_path=str(tmp_path))
        session = WatchSession(opts)
        session.state.add_processed(
            str(test_file),
            str(tmp_path / "test.HDR10.mkv"),
            stat.st_size,
            stat.st_mtime,
        )

        assert session._should_process(test_file) is False

    def test_should_process_output_exists(self, tmp_path):
        """Test should_process returns False if output already exists."""
        test_file = tmp_path / "test.mkv"
        test_file.write_text("dummy")
        output_file = tmp_path / "test.HDR10.mkv"
        output_file.write_text("exists")

        opts = WatchOptions(watch_path=str(tmp_path))
        session = WatchSession(opts)

        assert session._should_process(test_file) is False

    def test_should_process_reprocess_enabled(self, tmp_path):
        """Test should_process returns True for processed file when reprocess enabled."""
        test_file = tmp_path / "test.mkv"
        test_file.write_text("dummy")
        stat = test_file.stat()

        opts = WatchOptions(watch_path=str(tmp_path), reprocess=True)
        session = WatchSession(opts)
        session.state.add_processed(
            str(test_file),
            str(tmp_path / "test.HDR10.mkv"),
            stat.st_size,
            stat.st_mtime,
        )

        # Should still check if output exists - it doesn't, so should_process returns True
        assert session._should_process(test_file) is True


class TestCreateWatchOptionsFromArgs:
    """Tests for create_watch_options_from_args function."""

    def test_create_watch_options(self):
        """Test creating watch options from args."""
        from de_dolby.tracks import TrackSelection

        args = MagicMock()
        args.watch_path = "/media/movies"
        args.output_dir = "/media/output"
        args.recursive = True
        args.interval = 10
        args.delay = 20
        args.pattern = "*.mkv"
        args.move_original = True
        args.reprocess = False
        args.encoder = "hevc_amf"
        args.quality = "quality"
        args.crf = None
        args.bitrate = "50M"
        args.sample = None
        args.temp_dir = None
        args.dry_run = False
        args.verbose = True
        args.force = False
        args.no_validate = False
        args.resume = False
        args.ffmpeg = None
        args.dovi_tool = None
        args.mkvmerge = None

        settings = MagicMock()
        settings.tool_paths = MagicMock()
        settings.tool_paths.ffmpeg = None
        settings.tool_paths.dovi_tool = None
        settings.tool_paths.mkvmerge = None

        track_selection = TrackSelection()

        opts = create_watch_options_from_args(args, settings, track_selection)

        assert opts.watch_path == "/media/movies"
        assert opts.output_dir == "/media/output"
        assert opts.recursive is True
        assert opts.interval == 10
        assert opts.delay == 20
        assert opts.pattern == "*.mkv"
        assert opts.move_original is True
        assert opts.reprocess is False
        assert opts.convert_options.encoder == "hevc_amf"
        assert opts.convert_options.quality == "quality"
        assert opts.convert_options.verbose is True


class TestWatchIntegration:
    """Integration tests for watch functionality."""

    @patch("de_dolby.watch.WatchSession")
    def test_watch_function(self, mock_session_class):
        """Test watch function creates and runs session."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        watch("/media/movies", recursive=True, interval=10)

        mock_session_class.assert_called_once()
        mock_session.run.assert_called_once()

    @patch("de_dolby.watch._print")
    @patch("de_dolby.watch.WatchSession.run")
    def test_watch_keyboard_interrupt(self, mock_run, mock_print):
        """Test watch handles keyboard interrupt."""
        mock_run.side_effect = KeyboardInterrupt()

        with pytest.raises(SystemExit) as exc_info:
            watch("/media/movies")

        assert exc_info.value.code == 130


class TestWatchScanOnce:
    """Tests for WatchSession._scan_once method."""

    def test_scan_once_no_files(self, tmp_path):
        """Test scanning empty directory."""
        opts = WatchOptions(watch_path=str(tmp_path))
        session = WatchSession(opts)

        processed = session._scan_once()
        assert processed == 0

    @patch("de_dolby.watch.probe")
    @patch("de_dolby.watch.convert")
    def test_scan_once_finds_and_processes_dv_file(self, mock_convert, mock_probe, tmp_path):
        """Test scanning finds and processes DV file."""
        # Create a mock file
        test_file = tmp_path / "movie.mkv"
        test_file.write_text("dummy")

        # Mock probe to return DV info
        mock_info = MagicMock()
        mock_info.dv_profile = 7
        mock_probe.return_value = mock_info

        opts = WatchOptions(watch_path=str(tmp_path), delay=0)
        session = WatchSession(opts)

        # Suppress output
        with patch("de_dolby.watch._print"):
            processed = session._scan_once()

        assert processed == 1
        mock_probe.assert_called_once()
        mock_convert.assert_called_once()

    @patch("de_dolby.watch.probe")
    def test_scan_once_skips_non_dv_file(self, mock_probe, tmp_path):
        """Test scanning skips non-DV files."""
        # Create a mock file
        test_file = tmp_path / "movie.mkv"
        test_file.write_text("dummy")

        # Mock probe to return non-DV info
        mock_info = MagicMock()
        mock_info.dv_profile = None
        mock_probe.return_value = mock_info

        opts = WatchOptions(watch_path=str(tmp_path), delay=0)
        session = WatchSession(opts)

        # Suppress output
        with patch("de_dolby.watch._print"):
            processed = session._scan_once()

        assert processed == 0  # Non-DV files count as "successfully skipped"
        mock_probe.assert_called_once()

    @patch("de_dolby.watch.probe")
    def test_scan_once_handles_probe_error(self, mock_probe, tmp_path):
        """Test scanning handles probe errors gracefully."""
        # Create a mock file
        test_file = tmp_path / "movie.mkv"
        test_file.write_text("dummy")

        # Mock probe to raise error
        mock_probe.side_effect = RuntimeError("Probe failed")

        opts = WatchOptions(watch_path=str(tmp_path), delay=0)
        session = WatchSession(opts)

        # Suppress output
        with patch("de_dolby.watch._print"):
            processed = session._scan_once()

        assert processed == 0  # Failed conversions don't count
        mock_probe.assert_called_once()
