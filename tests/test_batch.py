"""Tests for de_dolby.batch — parallel batch processing."""

from unittest.mock import MagicMock, patch

from de_dolby.batch import (
    BatchProgress,
    ConversionResult,
    ConversionTask,
    _convert_worker,
    resolve_workers,
    run_batch_conversion,
)
from de_dolby.pipeline import ConvertOptions


class TestResolveWorkers:
    """Tests for worker count resolution."""

    def test_resolve_integer(self):
        """Integer values are returned as-is (clamped to >= 1)."""
        assert resolve_workers(1) == 1
        assert resolve_workers(4) == 4
        assert resolve_workers(8) == 8
        assert resolve_workers(0) == 1  # clamped
        assert resolve_workers(-5) == 1  # clamped

    def test_resolve_auto(self):
        """Auto uses CPU count minus 1."""
        with patch("os.cpu_count", return_value=8):
            assert resolve_workers("auto") == 7

    def test_resolve_auto_single_cpu(self):
        """Auto with single CPU returns 1."""
        with patch("os.cpu_count", return_value=1):
            assert resolve_workers("auto") == 1

    def test_resolve_auto_none_cpu(self):
        """Auto with None CPU count returns 1."""
        with patch("os.cpu_count", return_value=None):
            assert resolve_workers("auto") == 1

    def test_resolve_string_number(self):
        """Numeric strings are parsed."""
        assert resolve_workers("4") == 4
        assert resolve_workers("8") == 8

    def test_resolve_invalid_string(self):
        """Invalid strings return 1."""
        assert resolve_workers("invalid") == 1
        assert resolve_workers("") == 1


class TestConversionTask:
    """Tests for ConversionTask dataclass."""

    def test_task_creation(self):
        """ConversionTask can be created with required fields."""
        options = ConvertOptions(encoder="auto", quality="balanced")
        task = ConversionTask(
            input_path="/path/to/input.mkv",
            output_path="/path/to/output.mkv",
            options=options,
            task_id=1,
        )
        assert task.input_path == "/path/to/input.mkv"
        assert task.output_path == "/path/to/output.mkv"
        assert task.options == options
        assert task.task_id == 1


class TestConversionResult:
    """Tests for ConversionResult dataclass."""

    def test_success_result(self):
        """Successful result stores metadata."""
        result = ConversionResult(
            task_id=1,
            input_path="/path/to/input.mkv",
            output_path="/path/to/output.mkv",
            success=True,
            duration_seconds=120.5,
            output_size_bytes=1024 * 1024 * 1024,  # 1 GB
        )
        assert result.success is True
        assert result.error_message == ""
        assert result.duration_seconds == 120.5
        assert result.output_size_bytes == 1024 * 1024 * 1024

    def test_failure_result(self):
        """Failed result stores error message."""
        result = ConversionResult(
            task_id=1,
            input_path="/path/to/input.mkv",
            output_path="/path/to/output.mkv",
            success=False,
            error_message="Conversion failed",
            duration_seconds=30.0,
        )
        assert result.success is False
        assert result.error_message == "Conversion failed"
        assert result.output_size_bytes == 0


class TestBatchProgress:
    """Tests for BatchProgress tracking."""

    def test_initial_state(self):
        """BatchProgress starts with correct initial state."""
        batch = BatchProgress(total=10)
        summary = batch.get_summary()
        assert summary["total"] == 10
        assert summary["completed"] == 0
        assert summary["active"] == 0
        assert summary["pending"] == 10
        assert summary["succeeded"] == 0
        assert summary["failed"] == 0

    def test_start_task(self):
        """Starting a task moves it to active state."""
        batch = BatchProgress(total=10)
        batch.start_task(1, "/path/to/file.mkv")

        summary = batch.get_summary()
        assert summary["active"] == 1
        assert summary["pending"] == 9

        # Check active task details
        assert 1 in batch.active
        assert batch.active[1]["input_path"] == "/path/to/file.mkv"
        assert batch.active[1]["step"] == "starting"

    def test_update_task(self):
        """Task progress can be updated."""
        batch = BatchProgress(total=10)
        batch.start_task(1, "/path/to/file.mkv")
        batch.update_task(1, "encoding", 45.5)

        assert batch.active[1]["step"] == "encoding"
        assert batch.active[1]["progress"] == 45.5

    def test_complete_task_success(self):
        """Completing a task updates counters correctly."""
        batch = BatchProgress(total=10)
        batch.start_task(1, "/path/to/file.mkv")

        result = ConversionResult(
            task_id=1,
            input_path="/path/to/file.mkv",
            output_path="/path/to/output.mkv",
            success=True,
            duration_seconds=100.0,
            output_size_bytes=1024,
        )
        batch.complete_task(1, result)

        summary = batch.get_summary()
        assert summary["completed"] == 1
        assert summary["active"] == 0
        assert summary["succeeded"] == 1
        assert summary["failed"] == 0

    def test_complete_task_failure(self):
        """Failed task updates counters correctly."""
        batch = BatchProgress(total=10)
        batch.start_task(1, "/path/to/file.mkv")

        result = ConversionResult(
            task_id=1,
            input_path="/path/to/file.mkv",
            output_path="/path/to/output.mkv",
            success=False,
            error_message="Error",
            duration_seconds=10.0,
        )
        batch.complete_task(1, result)

        summary = batch.get_summary()
        assert summary["completed"] == 1
        assert summary["succeeded"] == 0
        assert summary["failed"] == 1

    def test_thread_safety(self):
        """BatchProgress is thread-safe."""
        import threading
        import time

        batch = BatchProgress(total=100)
        errors = []

        def worker(task_id):
            try:
                batch.start_task(task_id, f"/path/to/file{task_id}.mkv")
                time.sleep(0.001)  # Small delay
                batch.update_task(task_id, "encoding", 50.0)
                result = ConversionResult(
                    task_id=task_id,
                    input_path=f"/path/to/file{task_id}.mkv",
                    output_path=f"/path/to/output{task_id}.mkv",
                    success=True,
                    duration_seconds=1.0,
                )
                batch.complete_task(task_id, result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        summary = batch.get_summary()
        assert summary["completed"] == 10
        assert summary["succeeded"] == 10


class TestConvertWorker:
    """Tests for the conversion worker function."""

    @patch("de_dolby.batch.convert")
    @patch("tempfile.mkdtemp")
    @patch("shutil.rmtree")
    @patch("pathlib.Path.exists")
    def test_worker_success(self, mock_exists, mock_rmtree, mock_mkdtemp, mock_convert):
        """Worker returns success result on successful conversion."""
        mock_mkdtemp.return_value = "/tmp/worker_1"
        mock_exists.return_value = True

        # Mock file stat for output size
        mock_stat = MagicMock()
        mock_stat.st_size = 1024 * 1024

        with patch("pathlib.Path.stat", return_value=mock_stat):
            task = ConversionTask(
                input_path="/input/file.mkv",
                output_path="/output/file.mkv",
                options=ConvertOptions(),
                task_id=1,
            )
            result = _convert_worker(task)

        assert result.success is True
        assert result.task_id == 1
        assert result.input_path == "/input/file.mkv"
        assert result.output_path == "/output/file.mkv"
        assert result.output_size_bytes == 1024 * 1024
        assert result.error_message == ""

        # Verify temp dir cleanup
        mock_rmtree.assert_called_once_with("/tmp/worker_1", ignore_errors=True)

    @patch("de_dolby.batch.convert")
    def test_worker_failure(self, mock_convert):
        """Worker returns failure result on conversion error."""
        mock_convert.side_effect = RuntimeError("Conversion failed")

        with patch("tempfile.mkdtemp", return_value="/tmp/worker_1"), patch("shutil.rmtree"):
            task = ConversionTask(
                input_path="/input/file.mkv",
                output_path="/output/file.mkv",
                options=ConvertOptions(),
                task_id=1,
            )
            result = _convert_worker(task)

        assert result.success is False
        assert result.error_message == "Conversion failed"


class TestRunBatchConversion:
    """Tests for batch conversion orchestration."""

    @patch("de_dolby.batch._convert_worker")
    def test_sequential_single_worker(self, mock_worker):
        """Single worker uses sequential processing."""
        mock_worker.return_value = ConversionResult(
            task_id=1,
            input_path="/input/file.mkv",
            output_path="/output/file.mkv",
            success=True,
            duration_seconds=10.0,
        )

        tasks = [
            ConversionTask(
                input_path="/input/file1.mkv",
                output_path="/output/file1.mkv",
                options=ConvertOptions(),
            ),
        ]

        results = run_batch_conversion(tasks, workers=1, skip_errors=False, verbose=False)

        assert len(results) == 1
        assert results[0].success is True
        mock_worker.assert_called_once()

    def test_empty_task_list(self):
        """Empty task list returns empty results."""
        results = run_batch_conversion([], workers=1, skip_errors=False, verbose=False)
        assert results == []

    @patch("de_dolby.batch._parallel_convert")
    def test_parallel_multiple_workers(self, mock_parallel):
        """Multiple workers use parallel processing."""
        mock_parallel.return_value = [
            ConversionResult(
                task_id=1,
                input_path="/input/file.mkv",
                output_path="/output/file.mkv",
                success=True,
            )
        ]

        tasks = [
            ConversionTask(
                input_path="/input/file.mkv",
                output_path="/output/file.mkv",
                options=ConvertOptions(),
            ),
        ]

        results = run_batch_conversion(tasks, workers=4, skip_errors=False, verbose=False)

        mock_parallel.assert_called_once()
        assert len(results) == 1
