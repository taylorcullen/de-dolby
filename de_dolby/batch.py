"""Parallel batch processing for de-dolby conversions."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from de_dolby.options import ConvertOptions
from de_dolby.pipeline import convert
from de_dolby.utils import Colors as _C
from de_dolby.utils import format_bytes, format_duration


@dataclass
class ConversionTask:
    """Single conversion task specification."""

    input_path: str
    output_path: str
    options: ConvertOptions
    task_id: int = 0


@dataclass
class ConversionResult:
    """Result of a single conversion task."""

    task_id: int
    input_path: str
    output_path: str
    success: bool
    error_message: str = ""
    duration_seconds: float = 0.0
    output_size_bytes: int = 0


@dataclass
class BatchProgress:
    """Tracks progress of the entire batch."""

    total: int
    completed: int = 0
    active: dict[int, dict] = field(default_factory=dict)
    results: list[ConversionResult] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start_task(self, task_id: int, input_path: str) -> None:
        with self._lock:
            self.active[task_id] = {
                "input_path": input_path,
                "step": "starting",
                "progress": 0.0,
                "start_time": time.monotonic(),
            }

    def update_task(self, task_id: int, step: str, progress: float = 0.0) -> None:
        with self._lock:
            if task_id in self.active:
                self.active[task_id]["step"] = step
                self.active[task_id]["progress"] = progress

    def complete_task(self, task_id: int, result: ConversionResult) -> None:
        with self._lock:
            self.active.pop(task_id, None)
            self.completed += 1
            self.results.append(result)

    def get_summary(self) -> dict:
        with self._lock:
            succeeded = sum(1 for r in self.results if r.success)
            failed = sum(1 for r in self.results if not r.success)
            return {
                "total": self.total,
                "completed": self.completed,
                "active": len(self.active),
                "pending": self.total - self.completed - len(self.active),
                "succeeded": succeeded,
                "failed": failed,
            }


def _convert_worker(task: ConversionTask) -> ConversionResult:
    """Worker function for parallel conversion (must be picklable)."""
    start_time = time.monotonic()
    input_path = task.input_path
    output_path = task.output_path
    options = task.options
    task_id = task.task_id

    try:
        # Each worker needs its own temp directory to avoid conflicts
        worker_temp_dir = tempfile.mkdtemp(prefix=f"de_dolby_worker_{task_id}_")

        # Create a modified options with the worker temp dir
        worker_options = ConvertOptions(
            encoder=options.encoder,
            quality=options.quality,
            crf=options.crf,
            bitrate=options.bitrate,
            sample_seconds=options.sample_seconds,
            temp_dir=worker_temp_dir,
            dry_run=options.dry_run,
            verbose=False,  # Disable verbose in parallel mode
            force=options.force,
        )

        # Run the conversion
        convert(input_path, output_path, worker_options)

        # Get output file size
        output_size = Path(output_path).stat().st_size if Path(output_path).exists() else 0

        # Cleanup worker temp dir
        import shutil

        shutil.rmtree(worker_temp_dir, ignore_errors=True)

        duration = time.monotonic() - start_time

        return ConversionResult(
            task_id=task_id,
            input_path=input_path,
            output_path=output_path,
            success=True,
            duration_seconds=duration,
            output_size_bytes=output_size,
        )

    except Exception as e:
        duration = time.monotonic() - start_time
        return ConversionResult(
            task_id=task_id,
            input_path=input_path,
            output_path=output_path,
            success=False,
            error_message=str(e),
            duration_seconds=duration,
        )


def _render_progress_line(batch: BatchProgress, width: int = 80) -> str:
    """Render a single progress line for terminal display."""
    summary = batch.get_summary()

    lines = []

    # Header line
    total = summary["total"]
    completed = summary["completed"]
    active = summary["active"]
    pending = summary["pending"]

    header = f"Batch: {completed}/{total} complete, {active} active, {pending} pending"
    lines.append(header)

    # Active conversions (show up to 3)
    with batch._lock:
        active_tasks = list(batch.active.items())[:3]

    if active_tasks:
        lines.append("")
        for task_id, info in active_tasks:
            name = Path(info["input_path"]).name
            step = info["step"]
            progress = info["progress"]
            if progress > 0:
                lines.append(f"  [{task_id}] {name[:40]:<40} - {step} ({progress:.1f}%)")
            else:
                lines.append(f"  [{task_id}] {name[:40]:<40} - {step}")

    # Show completed count
    if completed > 0:
        lines.append("")
        lines.append(f"  Completed: {completed}/{total}")

    return "\n".join(lines)


def _progress_reporter(
    batch: BatchProgress, stop_event: threading.Event, update_interval: float = 2.0
) -> None:
    """Background thread that periodically updates the progress display."""
    last_line_count = 0

    while not stop_event.is_set():
        # Clear previous lines
        for _ in range(last_line_count):
            sys.stderr.write("\033[1A\033[K")  # Move up and clear line

        # Render and display new progress
        progress_text = _render_progress_line(batch)
        lines = progress_text.split("\n")
        last_line_count = len(lines)

        for line in lines:
            sys.stderr.write(line + "\n")
        sys.stderr.flush()

        # Wait for next update
        stop_event.wait(update_interval)


def _sequential_convert(
    tasks: list[ConversionTask],
    skip_errors: bool,
    verbose: bool,
) -> list[ConversionResult]:
    """Run conversions sequentially with traditional progress display."""
    results: list[ConversionResult] = []

    for idx, task in enumerate(tasks, 1):
        task.task_id = idx

        if verbose:
            print(f"\n{'=' * 60}")
            print(f"[{idx}/{len(tasks)}] {Path(task.input_path).name}")
            print(f"{'=' * 60}")

        try:
            result = _convert_worker(task)
            results.append(result)

            if result.success:
                if verbose:
                    size_str = format_bytes(result.output_size_bytes)
                    time_str = format_duration(result.duration_seconds)
                    print(f"  Done! ({size_str}, {time_str})")
            else:
                print(f"\n  {_C.RED}Error:{_C.RESET} {result.error_message}", file=sys.stderr)
                if not skip_errors:
                    break

        except KeyboardInterrupt:
            print("\n\nInterrupted.", file=sys.stderr)
            break

    return results


def _parallel_convert(
    tasks: list[ConversionTask],
    workers: int,
    skip_errors: bool,
    verbose: bool,
) -> list[ConversionResult]:
    """Run conversions in parallel with dashboard progress display."""
    batch = BatchProgress(total=len(tasks))

    # Assign task IDs
    for idx, task in enumerate(tasks, 1):
        task.task_id = idx

    # Start progress reporter thread
    stop_event = threading.Event()
    reporter_thread = threading.Thread(
        target=_progress_reporter,
        args=(batch, stop_event, 1.0),
        daemon=True,
    )
    reporter_thread.start()

    completed_results: list[ConversionResult] = []

    try:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            # Submit all tasks
            future_to_task = {executor.submit(_convert_worker, task): task for task in tasks}

            # Process results as they complete
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                batch.start_task(task.task_id, task.input_path)

                try:
                    result = future.result()
                    batch.complete_task(task.task_id, result)
                    completed_results.append(result)

                    if not result.success and not skip_errors:
                        # Cancel remaining futures
                        for f in future_to_task:
                            f.cancel()
                        break

                except Exception as e:
                    result = ConversionResult(
                        task_id=task.task_id,
                        input_path=task.input_path,
                        output_path=task.output_path,
                        success=False,
                        error_message=str(e),
                    )
                    batch.complete_task(task.task_id, result)
                    completed_results.append(result)

                    if not skip_errors:
                        # Cancel remaining futures
                        for f in future_to_task:
                            f.cancel()
                        break

    except KeyboardInterrupt:
        pass

    finally:
        stop_event.set()
        reporter_thread.join(timeout=2.0)

    return completed_results


def resolve_workers(workers: str | int) -> int:
    """Resolve worker count from 'auto' or integer."""
    if isinstance(workers, int):
        return max(1, workers)

    if workers == "auto":
        cpu_count = os.cpu_count() or 1
        # Leave one CPU free for system responsiveness
        return max(1, cpu_count - 1)

    try:
        count = int(workers)
        return max(1, count)
    except (ValueError, TypeError):
        return 1


def run_batch_conversion(
    tasks: list[ConversionTask],
    workers: str | int = 1,
    skip_errors: bool = False,
    verbose: bool = False,
) -> list[ConversionResult]:
    """Run batch conversion with specified parallelism.

    Args:
        tasks: List of conversion tasks to execute
        workers: Number of parallel workers (1 for sequential, 'auto' for CPU count)
        skip_errors: Continue processing other files if one fails
        verbose: Show detailed output

    Returns:
        List of conversion results
    """
    if not tasks:
        return []

    worker_count = resolve_workers(workers)

    if verbose:
        print(f"\nBatch conversion: {len(tasks)} file(s), {worker_count} worker(s)")
        print("=" * 60)

    if worker_count == 1:
        results = _sequential_convert(tasks, skip_errors, verbose)
    else:
        results = _parallel_convert(tasks, worker_count, skip_errors, verbose)

    # Print summary
    _print_summary(results, verbose)

    return results


def _print_summary(results: list[ConversionResult], verbose: bool) -> None:
    """Print final batch summary."""
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    total_time = sum(r.duration_seconds for r in results)
    total_size = sum(r.output_size_bytes for r in succeeded)

    print("\n" + "=" * 60)
    print(f"Batch Summary: {len(succeeded)}/{len(results)} succeeded")

    if succeeded:
        print(f"  Total output size: {format_bytes(total_size)}")
        print(f"  Total time: {format_duration(total_time)}")

    if failed:
        print(f"\n  {_C.RED}Failed conversions ({len(failed)}):{_C.RESET}")
        for r in failed:
            print(f"    - {Path(r.input_path).name}: {r.error_message}")

    print("=" * 60)
