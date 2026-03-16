"""Tests for de_dolby.tools — verbose mode, timeouts, logging, and configuration."""

import subprocess
import tempfile
import os
from unittest.mock import patch, MagicMock

import pytest

import de_dolby.tools as tools


class TestVerbose:
    def setup_method(self):
        tools._verbose = False

    def teardown_method(self):
        tools._verbose = False

    def test_set_verbose_enables(self):
        tools.set_verbose(True)
        assert tools._verbose is True

    def test_set_verbose_disables(self):
        tools.set_verbose(True)
        tools.set_verbose(False)
        assert tools._verbose is False

    @patch("de_dolby.tools.subprocess.run")
    def test_verbose_prints_command(self, mock_run, capsys):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        tools.set_verbose(True)
        tools._run(["echo", "hello"])
        captured = capsys.readouterr()
        assert "[cmd] echo hello" in captured.err

    @patch("de_dolby.tools.subprocess.run")
    def test_non_verbose_no_print(self, mock_run, capsys):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        tools.set_verbose(False)
        tools._run(["echo", "hello"])
        captured = capsys.readouterr()
        assert "[cmd]" not in captured.err


class TestTimeout:
    def setup_method(self):
        tools._timeout_seconds = None

    def teardown_method(self):
        tools._timeout_seconds = None

    def test_configure_timeout_sets_seconds(self):
        tools.configure_timeout(5)
        assert tools._timeout_seconds == 300

    def test_configure_timeout_none_clears(self):
        tools.configure_timeout(5)
        tools.configure_timeout(None)
        assert tools._timeout_seconds is None

    @patch("de_dolby.tools.subprocess.run")
    def test_timeout_passed_to_subprocess(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        tools._timeout_seconds = 60
        tools._run(["echo", "test"])
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 60

    @patch("de_dolby.tools.subprocess.run")
    def test_no_timeout_by_default(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        tools._timeout_seconds = None
        tools._run(["echo", "test"])
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] is None

    @patch("de_dolby.tools.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="test", timeout=60))
    def test_timeout_raises_runtime_error(self, mock_run):
        tools._timeout_seconds = 60
        with pytest.raises(RuntimeError, match="timed out"):
            tools._run(["sleep", "999"])


class TestCheckAmfSupport:
    @patch("de_dolby.tools._run")
    def test_amf_available(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"V..... hevc_amf  AMD AMF HEVC encoder")
        assert tools.check_amf_support() is True

    @patch("de_dolby.tools._run")
    def test_amf_not_available(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"V..... libx265  libx265 encoder")
        assert tools.check_amf_support() is False

    @patch("de_dolby.tools._run", side_effect=FileNotFoundError)
    def test_amf_ffmpeg_missing(self, mock_run):
        assert tools.check_amf_support() is False


class TestLogFile:
    def teardown_method(self):
        tools.configure_log_file(None)

    def test_configure_log_file_creates_file(self, tmp_path):
        log_path = str(tmp_path / "test.log")
        tools.configure_log_file(log_path)
        assert tools._log_file is not None
        tools.configure_log_file(None)

    def test_log_writes_to_file(self, tmp_path):
        log_path = str(tmp_path / "test.log")
        tools.configure_log_file(log_path)
        tools._log("test message")
        tools.configure_log_file(None)  # close the file
        content = open(log_path).read()
        assert "test message" in content

    @patch("de_dolby.tools.subprocess.run")
    def test_run_logs_command_and_exit_code(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        log_path = str(tmp_path / "test.log")
        tools.configure_log_file(log_path)
        tools._run(["echo", "hello"])
        tools.configure_log_file(None)
        content = open(log_path).read()
        assert "$ echo hello" in content
        assert "exit=0" in content

    def test_configure_log_file_none_disables(self):
        tools.configure_log_file(None)
        assert tools._log_file is None
