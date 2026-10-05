"""Pure queue and view models shared by the optional Textual interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from de_dolby.tui_paths import canonical_path_identity, display_basename


class QueueStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TuiView(str, Enum):
    QUEUE = "queue"
    BROWSER = "browser"
    DETAILS = "details"
    LOGS = "logs"
    PROGRESS = "progress"
    HELP = "help"


def canonical_queue_path(path: str) -> str:
    """Return a stable local identity without changing display spelling."""
    return canonical_path_identity(path)


@dataclass
class QueueItem:
    input_path: str
    output_path: str | None = None
    settings: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    status: QueueStatus = QueueStatus.PENDING
    plan_summary: str | None = None
    progress_percent: float | None = None
    eta_seconds: float | None = None
    error: str | None = None
    input_identity: dict[str, object] | None = None
    plan_fingerprint: str | None = None
    output_identity: dict[str, object] | None = None

    @property
    def identity(self) -> str:
        return canonical_queue_path(self.input_path)

    @property
    def display_name(self) -> str:
        return display_basename(self.input_path)


@dataclass
class QueueModel:
    items: list[QueueItem] = field(default_factory=list)
    selected_index: int | None = None
    view: TuiView = TuiView.QUEUE

    @property
    def selected(self) -> QueueItem | None:
        if self.selected_index is None or not self.items:
            return None
        if not 0 <= self.selected_index < len(self.items):
            return None
        return self.items[self.selected_index]

    def add(self, input_path: str, **values: object) -> QueueItem | None:
        identity = canonical_queue_path(input_path)
        if any(item.identity == identity for item in self.items):
            return None
        item = QueueItem(input_path=input_path, **values)
        self.items.append(item)
        if self.selected_index is None:
            self.selected_index = 0
        return item

    def remove_selected(self) -> QueueItem | None:
        if self.selected_index is None or self.selected is None:
            return None
        removed = self.items.pop(self.selected_index)
        if not self.items:
            self.selected_index = None
        else:
            self.selected_index = min(self.selected_index, len(self.items) - 1)
        return removed

    def move_selected(self, offset: int) -> bool:
        if self.selected_index is None or self.selected is None:
            return False
        target = self.selected_index + offset
        if not 0 <= target < len(self.items):
            return False
        self.items[self.selected_index], self.items[target] = (
            self.items[target],
            self.items[self.selected_index],
        )
        self.selected_index = target
        return True

    def select(self, index: int) -> None:
        if not 0 <= index < len(self.items):
            raise IndexError(index)
        self.selected_index = index
