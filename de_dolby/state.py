"""State management for resumable conversions."""

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from de_dolby.metadata import HDR10Metadata
from de_dolby.options import ConvertOptions

STATE_VERSION = 1
STATE_FILENAME_PREFIX = "de_dolby_state_"
STATE_MAX_AGE_DAYS = 7

# Pipeline steps in order
PIPELINE_STEPS = [
    "probe",
    "extract_hevc",
    "extract_rpu",
    "parse_meta",
    "strip_rpu",
    "encode",
    "remux",
    "cleanup",
]


@dataclass
class ConversionState:
    """Serializable state for a conversion in progress."""

    version: int = STATE_VERSION
    input_path: str = ""
    output_path: str = ""
    input_hash: str = ""
    created_at: str = ""
    last_updated: str = ""
    current_step: str = ""
    completed_steps: list[str] = field(default_factory=list)
    temp_paths: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert state to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "input_hash": self.input_hash,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "current_step": self.current_step,
            "completed_steps": self.completed_steps,
            "temp_paths": self.temp_paths,
            "metadata": self.metadata,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversionState":
        """Create state from dictionary."""
        return cls(
            version=data.get("version", STATE_VERSION),
            input_path=data.get("input_path", ""),
            output_path=data.get("output_path", ""),
            input_hash=data.get("input_hash", ""),
            created_at=data.get("created_at", ""),
            last_updated=data.get("last_updated", ""),
            current_step=data.get("current_step", ""),
            completed_steps=data.get("completed_steps", []),
            temp_paths=data.get("temp_paths", {}),
            metadata=data.get("metadata", {}),
            options=data.get("options", {}),
        )


def _compute_file_hash(path: str) -> str:
    """Compute a hash of the input file for state identification.

    Uses SHA-256 of the first 1MB + file size + modification time
    for reasonable uniqueness without reading entire large files.
    """
    p = Path(path)
    if not p.exists():
        return ""

    stat = p.stat()
    hasher = hashlib.sha256()
    hasher.update(str(stat.st_size).encode())
    hasher.update(str(stat.st_mtime).encode())

    # Read first 1MB for additional uniqueness
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024 * 1024)
            hasher.update(chunk)
    except OSError:
        pass

    return hasher.hexdigest()[:32]


def get_state_file_path(input_path: str, temp_dir: str | None = None) -> Path:
    """Get the path to the state file for an input file."""
    input_hash = _compute_file_hash(input_path)
    state_dir = temp_dir or tempfile.gettempdir()
    filename = f"{STATE_FILENAME_PREFIX}{input_hash}.json"
    return Path(state_dir) / filename


def load_state(input_path: str, temp_dir: str | None = None) -> ConversionState | None:
    """Load existing state for an input file if it exists."""
    state_path = get_state_file_path(input_path, temp_dir)

    if not state_path.exists():
        return None

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        state = ConversionState.from_dict(data)

        # Verify the state matches the current file
        current_hash = _compute_file_hash(input_path)
        if state.input_hash != current_hash:
            return None

        return state
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def save_state(state: ConversionState, temp_dir: str | None = None) -> None:
    """Save state to disk."""
    state_path = get_state_file_path(state.input_path, temp_dir)
    state.last_updated = datetime.now().isoformat()

    # Ensure directory exists
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state_path.write_text(
        json.dumps(state.to_dict(), indent=2),
        encoding="utf-8",
    )


def delete_state(input_path: str, temp_dir: str | None = None) -> bool:
    """Delete state file for an input file. Returns True if file was deleted."""
    state_path = get_state_file_path(input_path, temp_dir)

    if state_path.exists():
        try:
            state_path.unlink()
            return True
        except OSError:
            return False
    return False


def create_initial_state(
    input_path: str,
    output_path: str,
    options: ConvertOptions,
    temp_paths: dict[str, str],
) -> ConversionState:
    """Create a new initial state for a conversion."""
    now = datetime.now().isoformat()
    return ConversionState(
        version=STATE_VERSION,
        input_path=input_path,
        output_path=output_path,
        input_hash=_compute_file_hash(input_path),
        created_at=now,
        last_updated=now,
        current_step="probe",
        completed_steps=[],
        temp_paths=temp_paths,
        options=_serialize_options(options),
    )


def update_state_progress(
    state: ConversionState,
    step_name: str,
    completed: bool = False,
) -> None:
    """Update state with current step progress."""
    state.current_step = step_name

    if completed and step_name not in state.completed_steps:
        state.completed_steps.append(step_name)

    state.last_updated = datetime.now().isoformat()


def update_state_metadata(
    state: ConversionState,
    metadata: HDR10Metadata,
) -> None:
    """Update state with extracted metadata."""
    state.metadata = {
        "master_display": metadata.master_display,
        "max_cll": metadata.max_cll,
        "max_fall": metadata.max_fall,
    }
    state.last_updated = datetime.now().isoformat()


def is_step_completed(state: ConversionState | None, step_name: str) -> bool:
    """Check if a step has been completed."""
    if state is None:
        return False
    return step_name in state.completed_steps


def get_next_step(state: ConversionState | None) -> str | None:
    """Get the next step that needs to be executed."""
    if state is None:
        return PIPELINE_STEPS[0] if PIPELINE_STEPS else None

    for step in PIPELINE_STEPS:
        if step not in state.completed_steps:
            return step
    return None


def find_all_state_files(temp_dir: str | None = None) -> list[Path]:
    """Find all state files in the temp directory."""
    search_dir = Path(temp_dir or tempfile.gettempdir())
    if not search_dir.exists():
        return []

    return list(search_dir.glob(f"{STATE_FILENAME_PREFIX}*.json"))


def clean_old_state_files(
    temp_dir: str | None = None,
    max_age_days: int = STATE_MAX_AGE_DAYS,
) -> tuple[int, int]:
    """Remove state files older than max_age_days.

    Returns (files_deleted, files_kept).
    """
    state_files = find_all_state_files(temp_dir)
    cutoff = datetime.now() - timedelta(days=max_age_days)

    deleted = 0
    kept = 0

    for path in state_files:
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            if mtime < cutoff:
                path.unlink()
                deleted += 1
            else:
                kept += 1
        except OSError:
            kept += 1

    return deleted, kept


def clean_all_state_files(temp_dir: str | None = None) -> int:
    """Remove all state files. Returns number of files deleted."""
    state_files = find_all_state_files(temp_dir)

    deleted = 0
    for path in state_files:
        try:
            path.unlink()
            deleted += 1
        except OSError:
            pass

    return deleted


def _serialize_options(options: ConvertOptions) -> dict[str, Any]:
    """Serialize ConvertOptions to dictionary."""
    return {
        "encoder": options.encoder,
        "quality": options.quality,
        "crf": options.crf,
        "bitrate": options.bitrate,
        "sample_seconds": options.sample_seconds,
        "temp_dir": options.temp_dir,
        "dry_run": options.dry_run,
        "verbose": options.verbose,
        "force": options.force,
        "skip_validation": options.skip_validation,
        "resume": options.resume,
    }


def _deserialize_options(data: dict[str, Any]) -> ConvertOptions:
    """Deserialize ConvertOptions from dictionary."""
    return ConvertOptions(
        encoder=data.get("encoder", "auto"),
        quality=data.get("quality", "balanced"),
        crf=data.get("crf"),
        bitrate=data.get("bitrate"),
        sample_seconds=data.get("sample_seconds"),
        temp_dir=data.get("temp_dir"),
        dry_run=data.get("dry_run", False),
        verbose=data.get("verbose", False),
        force=data.get("force", False),
        skip_validation=data.get("skip_validation", False),
        resume=data.get("resume", False),
    )


def get_resume_summary(state: ConversionState) -> str:
    """Get a human-readable summary of the resumed conversion."""
    total_steps = len(PIPELINE_STEPS)
    completed = len(state.completed_steps)
    progress_pct = (completed / total_steps * 100) if total_steps > 0 else 0

    lines = [
        f"Resuming conversion: {Path(state.input_path).name}",
        f"  Progress: {completed}/{total_steps} steps ({progress_pct:.0f}%)",
        f"  Current step: {state.current_step}",
    ]

    if state.temp_paths:
        lines.append("  Temp files available:")
        for key, path in state.temp_paths.items():
            exists = "✓" if Path(path).exists() else "✗"
            lines.append(f"    {exists} {key}: {path}")

    return "\n".join(lines)


def validate_state_for_resume(state: ConversionState) -> tuple[bool, str]:
    """Validate that a state can be resumed.

    Returns (is_valid, error_message).
    """
    # Check input file still exists
    if not Path(state.input_path).exists():
        return False, f"Input file no longer exists: {state.input_path}"

    # Check required temp paths exist
    required_paths = []
    if "raw_path" in state.temp_paths:
        required_paths.append("raw_path")
    if "clean_path" in state.temp_paths:
        required_paths.append("clean_path")
    if "encoded_path" in state.temp_paths:
        required_paths.append("encoded_path")

    missing = []
    for key in required_paths:
        path = state.temp_paths.get(key, "")
        if path and not Path(path).exists():
            missing.append(key)

    if missing:
        return (
            False,
            f"Required temp files missing: {', '.join(missing)}. "
            "Cannot resume - please start fresh conversion.",
        )

    return True, ""
