"""Durable queue persistence and product-boundary adapters for the TUI."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from de_dolby.cli import derive_output_name
from de_dolby.manifest import fingerprint_plan, identify_input
from de_dolby.pipeline import ConvertOptions, plan_conversion
from de_dolby.process import ProcessCancelled, process_group_popen_kwargs, terminate_process_tree
from de_dolby.settings import default_config_path
from de_dolby.tui_models import QueueItem, QueueModel, QueueStatus

QUEUE_SCHEMA_VERSION = 1
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class QueueProgress:
    item_id: str
    percent: float | None = None
    eta_seconds: float | None = None
    message: str | None = None


def _eta_text_seconds(text: str) -> float | None:
    match = re.search(
        r"ETA (?:(\d+)h )?(?:(\d+)m )?(\d+)s", text
    )
    if not match:
        return None
    hours, minutes, seconds = (int(value or 0) for value in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


class SubprocessQueueConverter:
    """Run the existing CLI in a child process so TUI cancellation is safe."""

    def __call__(
        self,
        item: QueueItem,
        options: ConvertOptions,
        progress: Callable[[QueueProgress], None],
        cancel_event: threading.Event,
    ) -> None:
        command = [
            sys.executable, "-m", "de_dolby", "convert", item.input_path,
            "-o", item.output_path or derive_output_name(item.input_path),
        ]
        option_flags = {
            "encoder": "--encoder",
            "quality": "--quality",
            "crf": "--crf",
            "bitrate": "--bitrate",
            "sample_seconds": "--sample",
            "temp_dir": "--temp-dir",
        }
        for name, flag in option_flags.items():
            value = getattr(options, name)
            if value is not None and not (
                name in {"encoder", "quality"} and value in {"auto", "balanced"}
            ):
                command.extend([flag, str(value)])
        if options.force:
            command.append("--force")
        if options.unsafe_skip_validation:
            command.append("--unsafe-skip-validation")

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=False,
            **process_group_popen_kwargs(),
        )
        chunks: queue.Queue[bytes] = queue.Queue()

        def read_output() -> None:
            if process.stdout is None:
                return
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    return
                chunks.put(chunk)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        buffered = ""
        while process.poll() is None or not chunks.empty():
            if cancel_event.is_set() and process.poll() is None:
                terminate_process_tree(process)
                raise ProcessCancelled(
                    f"Queue conversion cancelled: {item.input_path}",
                    tuple(command),
                )
            try:
                chunk = chunks.get(timeout=0.05)
            except queue.Empty:
                continue
            buffered += chunk.decode(errors="replace")
            lines = re.split(r"[\r\n]+", buffered)
            buffered = lines.pop()
            for raw in lines:
                text = _ANSI_RE.sub("", raw).strip()
                if not text:
                    continue
                percent_match = re.search(r"(\d+(?:\.\d+)?)%", text)
                progress(QueueProgress(
                    item.id,
                    percent=float(percent_match.group(1)) if percent_match else None,
                    eta_seconds=_eta_text_seconds(text),
                    message=text,
                ))
        reader.join(timeout=1)
        if process.returncode:
            raise RuntimeError(
                f"Conversion exited with code {process.returncode}: {buffered.strip()}"
            )


def default_queue_path() -> Path:
    return default_config_path().with_name("queue.json")


def queue_document(model: QueueModel) -> dict:
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "selected_index": model.selected_index,
        "items": [
            {
                **asdict(item),
                "status": item.status.value,
            }
            for item in model.items
        ],
    }


def queue_from_document(document: dict) -> QueueModel:
    if document.get("schema_version") != QUEUE_SCHEMA_VERSION:
        raise RuntimeError("Unsupported TUI queue schema; migrate or recreate it")
    raw_items = document.get("items")
    if not isinstance(raw_items, list):
        raise RuntimeError("Invalid TUI queue: items must be a list")
    items = []
    try:
        for raw in raw_items:
            values = dict(raw)
            values["status"] = QueueStatus(values["status"])
            if values["status"] is QueueStatus.RUNNING:
                values["status"] = QueueStatus.INTERRUPTED
                values["error"] = "interrupted by previous application exit"
            items.append(QueueItem(**values))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid TUI queue item: {exc}") from exc
    selected = document.get("selected_index")
    if selected is not None and (
        not isinstance(selected, int) or not 0 <= selected < len(items)
    ):
        selected = 0 if items else None
    return QueueModel(items=items, selected_index=selected)


class QueueStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_queue_path()

    def load(self) -> QueueModel:
        if not self.path.exists():
            return QueueModel()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not load TUI queue {self.path}: {exc}") from exc
        model = queue_from_document(document)
        if any(item.status is QueueStatus.INTERRUPTED for item in model.items):
            self.save(model)
        return model

    def save(self, model: QueueModel) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(queue_document(model), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class QueueBackend:
    """Coordinate queue state with existing plan and conversion entry points."""

    def __init__(
        self,
        store: QueueStore,
        *,
        planner: Callable = plan_conversion,
        converter: Callable | None = None,
    ):
        self.store = store
        self.model = store.load()
        self.planner = planner
        self.converter = converter or SubprocessQueueConverter()
        self.cancel_event = threading.Event()

    def persist(self) -> None:
        self.store.save(self.model)

    def add(self, path: str) -> QueueItem | None:
        item = self.model.add(path, output_path=derive_output_name(path))
        if item is not None:
            self.persist()
        return item

    def plan(self, item: QueueItem) -> object | None:
        item.status = QueueStatus.PLANNING
        item.error = None
        self.persist()
        try:
            options = ConvertOptions(**item.settings)
            plan = self.planner(
                item.input_path,
                item.output_path or derive_output_name(item.input_path),
                options,
            )
            item.plan_summary = (
                f"{plan.pipeline.value} · {plan.encoder} · "
                f"{len(plan.steps)} steps"
            )
            try:
                item.input_identity = asdict(identify_input(item.input_path))
                item.plan_fingerprint = fingerprint_plan(plan)
            except (OSError, AttributeError, TypeError):
                # Pure/headless adapters may use virtual paths and plan doubles.
                item.input_identity = None
                item.plan_fingerprint = None
            item.status = QueueStatus.READY
            self.persist()
            return plan
        except Exception as exc:
            item.status = QueueStatus.FAILED
            item.error = str(exc)
            self.persist()
            return None

    def retry(self, item: QueueItem) -> None:
        if item.status not in {QueueStatus.FAILED, QueueStatus.INTERRUPTED}:
            return
        item.status = QueueStatus.PENDING
        item.error = None
        item.progress_percent = None
        item.eta_seconds = None
        self.persist()

    def cancel(self) -> None:
        self.cancel_event.set()

    def execute_pending(
        self, progress: Callable[[QueueProgress], None] | None = None
    ) -> None:
        callback = progress or (lambda event: None)
        self.cancel_event.clear()
        for item in self.model.items:
            if item.status is QueueStatus.COMPLETED:
                if self._completed_is_current(item):
                    continue
                item.status = QueueStatus.PENDING
                item.error = "input, plan, or validated output changed"
                item.progress_percent = None
                item.eta_seconds = None
                self.persist()
            if item.status is QueueStatus.PENDING and self.plan(item) is None:
                continue
            if item.status is not QueueStatus.READY:
                continue
            item.status = QueueStatus.RUNNING
            item.error = None
            self.persist()

            def update(event: QueueProgress) -> None:
                if event.percent is not None:
                    item.progress_percent = event.percent
                if event.eta_seconds is not None:
                    item.eta_seconds = event.eta_seconds
                callback(event)

            try:
                options = ConvertOptions(**item.settings)
                self.converter(item, options, update, self.cancel_event)
                item.status = QueueStatus.COMPLETED
                item.progress_percent = 100.0
                item.eta_seconds = 0.0
                try:
                    item.output_identity = asdict(
                        identify_input(item.output_path or "")
                    )
                except OSError:
                    item.output_identity = None
                self.persist()
            except ProcessCancelled as exc:
                item.status = QueueStatus.INTERRUPTED
                item.error = str(exc)
                self.persist()
                callback(QueueProgress(item.id, message=str(exc)))
                break
            except Exception as exc:
                item.status = QueueStatus.FAILED
                item.error = str(exc)
                self.persist()
                callback(QueueProgress(item.id, message=str(exc)))

    def _completed_is_current(self, item: QueueItem) -> bool:
        if (
            item.input_identity is None
            or item.plan_fingerprint is None
            or item.output_identity is None
        ):
            return False
        try:
            current_input = asdict(identify_input(item.input_path))
            current_output = asdict(identify_input(item.output_path or ""))
            options = ConvertOptions(**item.settings)
            plan = self.planner(
                item.input_path,
                item.output_path or derive_output_name(item.input_path),
                options,
            )
            return (
                current_input == item.input_identity
                and current_output == item.output_identity
                and fingerprint_plan(plan) == item.plan_fingerprint
            )
        except (OSError, RuntimeError, AttributeError, TypeError):
            return False
