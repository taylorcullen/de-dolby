"""Versioned resumable-batch manifests with atomic persistence."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from de_dolby.plan import ConversionPlan, plan_document

MANIFEST_SCHEMA_VERSION = 1


class ManifestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ResumeAction(str, Enum):
    RUN = "run"
    SKIP = "skip"


@dataclass(frozen=True)
class InputIdentity:
    path: str
    size: int
    modified_ns: int


@dataclass
class ManifestEntry:
    input: InputIdentity
    output_path: str
    plan_fingerprint: str
    status: ManifestStatus = ManifestStatus.PENDING
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    validation_valid: bool | None = None
    validation_codes: list[str] = field(default_factory=list)
    error: str | None = None
    output_identity: InputIdentity | None = None


@dataclass(frozen=True)
class ResumeDecision:
    action: ResumeAction
    reason: str


@dataclass
class BatchManifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)


def identify_input(path: str | Path) -> InputIdentity:
    resolved = Path(path).resolve()
    stat = resolved.stat()
    return InputIdentity(str(resolved), stat.st_size, stat.st_mtime_ns)


def fingerprint_plan(plan: ConversionPlan) -> str:
    payload = json.dumps(
        plan_document(plan),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def manifest_document(manifest: BatchManifest) -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "entries": {
            key: {
                "input": {
                    "path": entry.input.path,
                    "size": entry.input.size,
                    "modified_ns": entry.input.modified_ns,
                },
                "output_path": entry.output_path,
                "plan_fingerprint": entry.plan_fingerprint,
                "status": entry.status.value,
                "attempts": entry.attempts,
                "started_at": entry.started_at,
                "finished_at": entry.finished_at,
                "validation": {
                    "valid": entry.validation_valid,
                    "codes": list(entry.validation_codes),
                },
                "error": entry.error,
                "output_identity": (
                    {
                        "path": entry.output_identity.path,
                        "size": entry.output_identity.size,
                        "modified_ns": entry.output_identity.modified_ns,
                    }
                    if entry.output_identity else None
                ),
            }
            for key, entry in sorted(manifest.entries.items())
        },
    }


def save_manifest(path: str | Path, manifest: BatchManifest) -> None:
    """Atomically replace a manifest after flushing complete JSON."""
    destination = Path(path)
    temporary = destination.with_name(
        f".{destination.name}.de-dolby-{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                manifest_document(manifest),
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            f"Could not atomically save batch manifest {destination}: {exc}"
        ) from exc


def load_manifest(path: str | Path) -> BatchManifest:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read batch manifest {source}: {exc}") from exc
    version = document.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported manifest schema version {version!r}; "
            f"expected {MANIFEST_SCHEMA_VERSION}. Start a new manifest or migrate it."
        )
    entries = {}
    for key, item in document.get("entries", {}).items():
        identity = item["input"]
        validation = item.get("validation", {})
        output_identity = item.get("output_identity")
        entries[key] = ManifestEntry(
            input=InputIdentity(
                identity["path"], identity["size"], identity["modified_ns"]
            ),
            output_path=item["output_path"],
            plan_fingerprint=item["plan_fingerprint"],
            status=ManifestStatus(item["status"]),
            attempts=item.get("attempts", 0),
            started_at=item.get("started_at"),
            finished_at=item.get("finished_at"),
            validation_valid=validation.get("valid"),
            validation_codes=list(validation.get("codes", [])),
            error=item.get("error"),
            output_identity=(
                InputIdentity(
                    output_identity["path"],
                    output_identity["size"],
                    output_identity["modified_ns"],
                )
                if output_identity else None
            ),
        )
    return BatchManifest(entries)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ManifestStore:
    """Apply sequential lifecycle transitions with persistence after each."""

    path: Path
    manifest: BatchManifest = field(default_factory=BatchManifest)
    clock: Callable[[], datetime] = _utc_now

    def _save(self) -> None:
        save_manifest(self.path, self.manifest)

    def register(
        self,
        identity: InputIdentity,
        output_path: str,
        plan_fingerprint: str,
    ) -> str:
        key = identity.path
        self.manifest.entries[key] = ManifestEntry(
            identity, output_path, plan_fingerprint
        )
        self._save()
        return key

    def start(self, key: str) -> ManifestEntry:
        entry = self.manifest.entries[key]
        if entry.status not in (
            ManifestStatus.PENDING,
            ManifestStatus.FAILED,
            ManifestStatus.RUNNING,
            ManifestStatus.INTERRUPTED,
        ):
            raise RuntimeError(
                f"Cannot start manifest entry in {entry.status.value} state"
            )
        entry.status = ManifestStatus.RUNNING
        entry.attempts += 1
        entry.started_at = self.clock().isoformat()
        entry.finished_at = None
        entry.error = None
        entry.validation_valid = None
        entry.validation_codes.clear()
        self._save()
        return entry

    def complete(
        self,
        key: str,
        *,
        validation_valid: bool,
        validation_codes: list[str] | None = None,
        output_identity: InputIdentity | None = None,
    ) -> ManifestEntry:
        entry = self.manifest.entries[key]
        if entry.status is not ManifestStatus.RUNNING:
            raise RuntimeError(
                f"Cannot complete manifest entry in {entry.status.value} state"
            )
        entry.status = ManifestStatus.COMPLETED
        entry.finished_at = self.clock().isoformat()
        entry.validation_valid = validation_valid
        entry.validation_codes = list(validation_codes or [])
        entry.output_identity = output_identity
        self._save()
        return entry

    def fail(self, key: str, error: str) -> ManifestEntry:
        entry = self.manifest.entries[key]
        if entry.status is not ManifestStatus.RUNNING:
            raise RuntimeError(
                f"Cannot fail manifest entry in {entry.status.value} state"
            )
        entry.status = ManifestStatus.FAILED
        entry.finished_at = self.clock().isoformat()
        entry.error = error
        self._save()
        return entry

    def interrupt(self, key: str) -> ManifestEntry:
        entry = self.manifest.entries[key]
        if entry.status is not ManifestStatus.RUNNING:
            raise RuntimeError(
                f"Cannot interrupt manifest entry in {entry.status.value} state"
            )
        entry.status = ManifestStatus.INTERRUPTED
        entry.finished_at = self.clock().isoformat()
        entry.error = "interrupted"
        self._save()
        return entry


def select_resume_action(
    entry: ManifestEntry | None,
    *,
    current_input: InputIdentity,
    current_plan_fingerprint: str,
    current_output: InputIdentity | None,
    retry_failed: bool,
) -> ResumeDecision:
    """Decide whether a manifest item can be safely skipped."""
    if entry is None:
        return ResumeDecision(ResumeAction.RUN, "new_input")
    if entry.input != current_input:
        return ResumeDecision(ResumeAction.RUN, "input_changed")
    if entry.plan_fingerprint != current_plan_fingerprint:
        return ResumeDecision(ResumeAction.RUN, "plan_changed")
    if entry.status is ManifestStatus.COMPLETED:
        if entry.validation_valid is not True:
            return ResumeDecision(ResumeAction.RUN, "validation_not_confirmed")
        if entry.output_identity is None or current_output != entry.output_identity:
            return ResumeDecision(ResumeAction.RUN, "output_changed_or_missing")
        return ResumeDecision(ResumeAction.SKIP, "validated_output_matches")
    if entry.status is ManifestStatus.FAILED and not retry_failed:
        return ResumeDecision(ResumeAction.SKIP, "failed_item_not_selected")
    if entry.status is ManifestStatus.FAILED:
        return ResumeDecision(ResumeAction.RUN, "retry_failed_selected")
    if entry.status is ManifestStatus.RUNNING:
        return ResumeDecision(ResumeAction.RUN, "interrupted_attempt")
    if entry.status is ManifestStatus.INTERRUPTED:
        return ResumeDecision(ResumeAction.RUN, "interrupted_attempt")
    return ResumeDecision(ResumeAction.RUN, "pending")
