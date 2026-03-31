"""Watch mode for automatic Dolby Vision file conversion.

Monitors a directory for new MKV files and automatically converts
Dolby Vision files to HDR10 format.
"""

from __future__ import annotations

import fnmatch
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from de_dolby.options import ConvertOptions
from de_dolby.pipeline import convert
from de_dolby.probe import probe
from de_dolby.settings import Settings
from de_dolby.tracks import TrackSelection
from de_dolby.utils import Colors, derive_output_name, format_bytes

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class ProcessedFile:
    """Record of a processed file."""

    input: str
    output: str
    size: int
    mtime: float
    converted_at: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "input": self.input,
            "output": self.output,
            "size": self.size,
            "mtime": self.mtime,
            "converted_at": self.converted_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessedFile:
        """Create from dictionary."""
        return cls(
            input=data["input"],
            output=data["output"],
            size=data["size"],
            mtime=data["mtime"],
            converted_at=data["converted_at"],
        )


@dataclass
class WatchState:
    """Persistent state for watch mode."""

    watch_path: str
    processed_files: list[ProcessedFile] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "watch_path": self.watch_path,
            "processed_files": [f.to_dict() for f in self.processed_files],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchState:
        """Create from dictionary."""
        return cls(
            watch_path=data["watch_path"],
            processed_files=[ProcessedFile.from_dict(f) for f in data.get("processed_files", [])],
        )

    def is_processed(self, path: str, size: int, mtime: float) -> bool:
        """Check if a file has already been processed.

        Matches by path, and verifies size and mtime haven't changed.
        """
        for f in self.processed_files:
            if f.input == path and f.size == size and f.mtime == mtime:
                return True
        return False

    def add_processed(self, input_path: str, output_path: str, size: int, mtime: float) -> None:
        """Add a processed file to the state."""
        # Remove any existing entry for this input path
        self.processed_files = [f for f in self.processed_files if f.input != input_path]

        processed = ProcessedFile(
            input=input_path,
            output=output_path,
            size=size,
            mtime=mtime,
            converted_at=datetime.now(timezone.utc).isoformat(),
        )
        self.processed_files.append(processed)


@dataclass
class WatchOptions:
    """Configuration options for watch mode."""

    watch_path: str
    output_dir: str | None = None
    recursive: bool = False
    interval: int = 5
    delay: int = 10
    pattern: str = "*.mkv"
    move_original: bool = False
    reprocess: bool = False
    # Conversion options
    convert_options: ConvertOptions = field(default_factory=ConvertOptions)
    # Tool paths
    tool_paths: dict[str, str | None] = field(default_factory=dict)


def _get_state_path() -> Path:
    """Get the path to the watch state file."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA environment variable not set")
        config_dir = Path(appdata) / "de-dolby"
    else:
        config_dir = Path.home() / ".config" / "de-dolby"

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "watch_state.json"


def load_watch_state(watch_path: str) -> WatchState:
    """Load watch state from disk, or create new if not exists."""
    state_path = _get_state_path()
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            # Only load if watch_path matches
            if data.get("watch_path") == watch_path:
                return WatchState.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            pass
    return WatchState(watch_path=watch_path)


def save_watch_state(state: WatchState) -> None:
    """Save watch state to disk."""
    state_path = _get_state_path()
    state_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def _timestamp() -> str:
    """Get current timestamp for display."""
    return datetime.now().strftime("%H:%M:%S")


def _print(message: str, color: str = "", verbose: bool = False) -> None:
    """Print a message with optional color."""
    if color and sys.stderr.isatty():
        print(f"{color}[{_timestamp()}] {message}{Colors.RESET}", file=sys.stderr)
    else:
        print(f"[{_timestamp()}] {message}", file=sys.stderr)


def _find_files(watch_path: str, pattern: str, recursive: bool) -> list[Path]:
    """Find all files matching pattern in watch path."""
    watch_dir = Path(watch_path)
    if not watch_dir.exists():
        return []

    files: list[Path] = []
    if recursive:
        for root, _dirs, filenames in os.walk(watch_dir):
            for filename in filenames:
                if fnmatch.fnmatch(filename, pattern):
                    files.append(Path(root) / filename)
    else:
        for filename in os.listdir(watch_dir):
            if fnmatch.fnmatch(filename, pattern):
                files.append(watch_dir / filename)

    return sorted(files)


def _wait_for_file_stable(file_path: Path, delay: int, verbose: bool) -> bool:
    """Wait for file to be stable (not being written to).

    Returns True if file is stable, False if interrupted.
    """
    if delay <= 0:
        return True

    if verbose:
        _print(f"Waiting {delay}s for file to stabilize...", Colors.DIM, verbose)

    # Check size stability
    last_size = -1
    stable_count = 0
    check_interval = 1
    elapsed = 0

    while elapsed < delay:
        try:
            current_size = file_path.stat().st_size
        except OSError:
            return False

        if current_size == last_size:
            stable_count += 1
            if stable_count >= 2:  # Size stable for 2 checks
                break
        else:
            stable_count = 0

        last_size = current_size
        time.sleep(check_interval)
        elapsed += check_interval

    return True


def _get_output_path(input_path: Path, output_dir: str | None) -> str:
    """Determine output path for a file."""
    if output_dir:
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)
        derived_name = Path(derive_output_name(str(input_path))).name
        return str(output_dir_path / derived_name)
    return derive_output_name(str(input_path))


def _move_original(input_path: Path) -> Path | None:
    """Move original file to a subdirectory.

    Returns the new path if successful, None otherwise.
    """
    try:
        parent = input_path.parent
        original_dir = parent / "original"
        original_dir.mkdir(exist_ok=True)
        new_path = original_dir / input_path.name
        input_path.rename(new_path)
        return new_path
    except OSError:
        return None


class WatchSession:
    """Manages a watch mode session."""

    def __init__(self, options: WatchOptions):
        self.options = options
        self.state = load_watch_state(options.watch_path)
        self.running = False
        self.current_file: str | None = None
        self._shutdown_requested = False
        self._original_sigint = None

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        self._original_sigint = signal.signal(signal.SIGINT, self._handle_sigint)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._handle_sigint)

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)

    def _handle_sigint(self, signum: int, frame: Any) -> None:
        """Handle interrupt signal."""
        self._shutdown_requested = True
        _print("\nShutdown requested, finishing current task...", Colors.YELLOW)

    def _should_process(self, file_path: Path) -> bool:
        """Check if a file should be processed."""
        try:
            stat = file_path.stat()
        except OSError:
            return False

        # Skip if already processed and not reprocessing
        if not self.options.reprocess:
            if self.state.is_processed(str(file_path), stat.st_size, stat.st_mtime):
                return False

        # Skip if output already exists
        output_path = _get_output_path(file_path, self.options.output_dir)
        if Path(output_path).exists():
            # Add to state to avoid re-checking
            if not self.options.reprocess:
                self.state.add_processed(str(file_path), output_path, stat.st_size, stat.st_mtime)
            return False

        return True

    def _probe_and_convert(self, file_path: Path) -> bool:
        """Probe a file and convert if it's Dolby Vision.

        Returns True if conversion was successful or skipped non-DV file.
        Returns False if conversion failed.
        """
        verbose = self.options.convert_options.verbose

        _print(f"Detected: {file_path} ({format_bytes(file_path.stat().st_size)})", Colors.CYAN)

        # Wait for file to be stable
        if not _wait_for_file_stable(file_path, self.options.delay, verbose):
            _print(f"File disappeared or unstable: {file_path}", Colors.RED)
            return False

        # Probe the file
        _print("Probing for Dolby Vision...", Colors.DIM, verbose)
        try:
            info = probe(str(file_path))
        except RuntimeError as e:
            _print(f"Probe failed: {e}", Colors.RED)
            return False

        # Check if it's Dolby Vision
        if info.dv_profile is None:
            _print(f"  Skipping: not Dolby Vision", Colors.DIM)
            return True  # Not an error, just skip

        _print(f"  Dolby Vision Profile {info.dv_profile} detected", Colors.GREEN)

        # Determine output path
        output_path = _get_output_path(file_path, self.options.output_dir)

        # Check for existing output (race condition)
        if Path(output_path).exists() and not self.options.convert_options.force:
            _print(f"  Output already exists: {output_path}", Colors.YELLOW)
            return True

        # Perform conversion
        _print(f"Starting conversion to HDR10...", Colors.CYAN)
        self.current_file = str(file_path)

        try:
            convert(str(file_path), output_path, self.options.convert_options)

            _print(f"Conversion complete: {output_path}", Colors.GREEN)

            # Record in state
            stat = file_path.stat()
            self.state.add_processed(str(file_path), output_path, stat.st_size, stat.st_mtime)
            save_watch_state(self.state)

            # Move original if requested
            if self.options.move_original:
                moved = _move_original(file_path)
                if moved:
                    _print(f"Moved original to: {moved}", Colors.DIM, verbose)
                else:
                    _print(f"Failed to move original file", Colors.YELLOW)

            return True

        except RuntimeError as e:
            _print(f"Conversion failed: {e}", Colors.RED)
            return False
        except KeyboardInterrupt:
            _print("Conversion interrupted", Colors.YELLOW)
            raise
        finally:
            self.current_file = None

    def _scan_once(self) -> int:
        """Scan for files once and process any new ones.

        Returns number of files processed.
        """
        files = _find_files(
            self.options.watch_path,
            self.options.pattern,
            self.options.recursive,
        )

        processed = 0
        for file_path in files:
            if self._shutdown_requested:
                break

            if self._should_process(file_path):
                success = self._probe_and_convert(file_path)
                if success:
                    processed += 1
                # Small delay between files
                if not self._shutdown_requested:
                    time.sleep(1)

        return processed

    def run(self) -> None:
        """Run the watch loop."""
        self._setup_signal_handlers()
        self.running = True

        try:
            watch_dir = Path(self.options.watch_path)
            if not watch_dir.exists():
                print(f"Error: watch directory does not exist: {watch_dir}", file=sys.stderr)
                sys.exit(1)

            recursive_str = " (recursive)" if self.options.recursive else ""
            _print(
                f"Watching {watch_dir}{recursive_str} for Dolby Vision MKV files...",
                Colors.BRIGHT_CYAN,
            )
            _print("Press Ctrl+C to stop", Colors.DIM)
            print(file=sys.stderr)

            while not self._shutdown_requested:
                try:
                    processed = self._scan_once()

                    if processed == 0 and not self._shutdown_requested:
                        # No files processed, wait before next scan
                        time.sleep(self.options.interval)
                    elif not self._shutdown_requested:
                        _print("Waiting for new files...", Colors.DIM)
                        time.sleep(self.options.interval)

                except KeyboardInterrupt:
                    break

        finally:
            self.running = False
            self._restore_signal_handlers()
            save_watch_state(self.state)
            _print("Watch mode stopped.", Colors.BRIGHT_CYAN)


def watch(
    watch_path: str,
    output_dir: str | None = None,
    recursive: bool = False,
    interval: int = 5,
    delay: int = 10,
    pattern: str = "*.mkv",
    move_original: bool = False,
    reprocess: bool = False,
    convert_options: ConvertOptions | None = None,
    tool_paths: dict[str, str | None] | None = None,
) -> None:
    """Start watching a directory for Dolby Vision files and auto-convert them.

    Args:
        watch_path: Directory to watch
        output_dir: Directory for converted files (default: same as input)
        recursive: Watch subdirectories too
        interval: Check interval in seconds
        delay: Wait time after file appears before processing
        pattern: File pattern to watch
        move_original: Move original to subdirectory after conversion
        reprocess: Reprocess files already in state
        convert_options: Options for conversion
        tool_paths: Paths to external tools
    """
    options = WatchOptions(
        watch_path=watch_path,
        output_dir=output_dir,
        recursive=recursive,
        interval=interval,
        delay=delay,
        pattern=pattern,
        move_original=move_original,
        reprocess=reprocess,
        convert_options=convert_options or ConvertOptions(),
        tool_paths=tool_paths or {},
    )

    session = WatchSession(options)
    session.run()


def create_watch_options_from_args(
    args: Any,
    settings: Settings,
    track_selection: TrackSelection,
) -> WatchOptions:
    """Create WatchOptions from CLI arguments and settings."""
    convert_options = ConvertOptions(
        encoder=args.encoder,
        quality=args.quality,
        crf=args.crf,
        bitrate=args.bitrate,
        sample_seconds=args.sample,
        temp_dir=args.temp_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
        force=args.force,
        skip_validation=getattr(args, "no_validate", False),
        resume=args.resume,
        track_selection=track_selection,
    )

    tool_paths = {
        "ffmpeg": getattr(args, "ffmpeg", None) or settings.tool_paths.ffmpeg,
        "dovi_tool": getattr(args, "dovi_tool", None) or settings.tool_paths.dovi_tool,
        "mkvmerge": getattr(args, "mkvmerge", None) or settings.tool_paths.mkvmerge,
    }

    return WatchOptions(
        watch_path=args.watch_path,
        output_dir=args.output_dir,
        recursive=args.recursive,
        interval=args.interval,
        delay=args.delay,
        pattern=args.pattern,
        move_original=args.move_original,
        reprocess=args.reprocess,
        convert_options=convert_options,
        tool_paths=tool_paths,
    )
