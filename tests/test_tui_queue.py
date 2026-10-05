import json
import threading
import time
from types import SimpleNamespace

import pytest

from de_dolby.tui_models import QueueItem, QueueModel, QueueStatus
from de_dolby.tui_queue import (
    QueueBackend,
    QueueProgress,
    QueueStore,
    queue_document,
    queue_from_document,
)


def test_queue_store_round_trips_versioned_state(tmp_path):
    store = QueueStore(tmp_path / "queue.json")
    model = QueueModel()
    item = model.add(r"\\server\share\Movie.DV.mkv")
    item.settings = {"quality": "quality", "sample_seconds": 30}
    store.save(model)

    restored = store.load()
    assert restored.selected_index == 0
    assert restored.items[0].input_path == item.input_path
    assert restored.items[0].settings == item.settings
    assert json.loads(store.path.read_text())["schema_version"] == 1


def test_running_item_recovers_as_interrupted_and_is_persisted(tmp_path):
    store = QueueStore(tmp_path / "queue.json")
    model = QueueModel(items=[
        QueueItem("movie.mkv", status=QueueStatus.RUNNING)
    ], selected_index=0)
    store.path.parent.mkdir(exist_ok=True)
    store.path.write_text(json.dumps(queue_document(model)), encoding="utf-8")

    restored = store.load()
    assert restored.items[0].status is QueueStatus.INTERRUPTED
    assert "previous application exit" in restored.items[0].error
    assert json.loads(store.path.read_text())["items"][0]["status"] == "interrupted"


def test_queue_store_rejects_unknown_schema(tmp_path):
    store = QueueStore(tmp_path / "queue.json")
    store.path.write_text('{"schema_version": 99, "items": []}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="schema"):
        store.load()


def test_queue_backend_plans_and_persists_summary(tmp_path):
    plan = SimpleNamespace(
        pipeline=SimpleNamespace(value="reencode"),
        encoder="libx265",
        steps=("probe", "encode"),
    )
    backend = QueueBackend(
        QueueStore(tmp_path / "queue.json"),
        planner=lambda *args: plan,
    )
    item = backend.add("movie.DV.mkv")
    assert backend.plan(item) is plan
    assert item.status is QueueStatus.READY
    assert item.plan_summary == "reencode · libx265 · 2 steps"
    assert QueueStore(tmp_path / "queue.json").load().items[0].plan_summary


def test_queue_backend_records_plan_failure_and_retry(tmp_path):
    def fail(*args):
        raise RuntimeError("probe unavailable")

    backend = QueueBackend(
        QueueStore(tmp_path / "queue.json"), planner=fail
    )
    item = backend.add("movie.DV.mkv")
    assert backend.plan(item) is None
    assert item.status is QueueStatus.FAILED
    assert item.error == "probe unavailable"
    backend.retry(item)
    assert item.status is QueueStatus.PENDING
    assert item.error is None


def test_queue_backend_executes_sequentially_and_reports_progress(tmp_path):
    plan = SimpleNamespace(
        pipeline=SimpleNamespace(value="reencode"),
        encoder="libx265",
        steps=("encode",),
    )
    calls = []

    def convert(item, options, progress, cancel_event):
        calls.append(item.input_path)
        progress(QueueProgress(item.id, percent=50, eta_seconds=10))

    backend = QueueBackend(
        QueueStore(tmp_path / "queue.json"),
        planner=lambda *args: plan,
        converter=convert,
    )
    first = backend.add("first.DV.mkv")
    second = backend.add("second.DV.mkv")
    events = []
    backend.execute_pending(events.append)
    assert calls == ["first.DV.mkv", "second.DV.mkv"]
    assert first.status is QueueStatus.COMPLETED
    assert second.status is QueueStatus.COMPLETED
    assert events[0].percent == 50


def test_queue_backend_cancel_records_interrupted_and_stops_queue(tmp_path):
    plan = SimpleNamespace(
        pipeline=SimpleNamespace(value="reencode"),
        encoder="libx265",
        steps=("encode",),
    )
    started = threading.Event()

    def convert(item, options, progress, cancel_event):
        started.set()
        while not cancel_event.wait(0.01):
            pass
        from de_dolby.process import ProcessCancelled
        raise ProcessCancelled("cancelled", ("convert",))

    backend = QueueBackend(
        QueueStore(tmp_path / "queue.json"),
        planner=lambda *args: plan,
        converter=convert,
    )
    first = backend.add("first.DV.mkv")
    second = backend.add("second.DV.mkv")
    worker = threading.Thread(target=backend.execute_pending)
    worker.start()
    assert started.wait(1)
    backend.cancel()
    worker.join(1)
    assert first.status is QueueStatus.INTERRUPTED
    assert second.status is QueueStatus.PENDING
