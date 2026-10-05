import asyncio
import time
from types import SimpleNamespace

from textual.widgets import ContentSwitcher, Input

from de_dolby.tui_app import DolbyQueueApp
from de_dolby.tui_models import QueueStatus
from de_dolby.tui_queue import QueueBackend, QueueStore


def run(coro):
    return asyncio.run(coro)


def backend(tmp_path):
    plan = SimpleNamespace(
        pipeline=SimpleNamespace(value="reencode"),
        encoder="libx265",
        steps=("probe", "encode"),
    )
    return QueueBackend(
        QueueStore(tmp_path / "queue.json"),
        planner=lambda *args: plan,
    )


def test_tui_add_plan_reorder_remove_and_exit(tmp_path):
    async def scenario():
        app = DolbyQueueApp(backend=backend(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            path_input = app.query_one("#path-input", Input)
            path_input.value = "first.DV.mkv"
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.backend.model.items) == 1
            assert app.query_one("#queue-list").has_focus
            await pilot.press("a")
            await pilot.pause()
            assert path_input.has_focus
            path_input.value = "second.DV.mkv"
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.backend.model.items) == 2

            await pilot.press("p")
            await pilot.pause()
            assert app.backend.model.selected.status is QueueStatus.READY
            await pilot.press("ctrl+down")
            await pilot.pause()
            assert app.backend.model.selected_index == 1
            await pilot.press("delete")
            await pilot.pause()
            assert len(app.backend.model.items) == 1
            await pilot.press("q")
    run(scenario())


def test_tui_keyboard_views_settings_and_help(tmp_path):
    async def scenario():
        app = DolbyQueueApp(backend=backend(tmp_path))
        app.backend.add("movie.DV.mkv")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("3")
            assert app.query_one("#switcher", ContentSwitcher).current == "details-view"
            setting = app.query_one("#setting-input", Input)
            setting.value = "quality=quality"
            setting.focus()
            await pilot.press("enter")
            assert app.backend.model.selected.settings["quality"] == "quality"
            await pilot.press("question_mark")
            assert app.query_one("#switcher", ContentSwitcher).current == "help-view"
            await pilot.press("4")
            assert app.query_one("#switcher", ContentSwitcher).current == "logs-view"
    run(scenario())


def test_tui_duplicate_path_is_not_added(tmp_path):
    async def scenario():
        app = DolbyQueueApp(backend=backend(tmp_path))
        async with app.run_test() as pilot:
            input_widget = app.query_one("#path-input", Input)
            input_widget.value = "movie.mkv"
            await pilot.press("enter")
            input_widget.value = "movie.mkv"
            await pilot.press("enter")
            assert len(app.backend.model.items) == 1
    run(scenario())


def test_tui_start_updates_live_progress_and_completes(tmp_path):
    def convert(item, options, progress, cancel_event):
        from de_dolby.tui_queue import QueueProgress
        progress(QueueProgress(item.id, percent=40, eta_seconds=12, message="encoding"))

    async def scenario():
        queue_backend = backend(tmp_path)
        queue_backend.converter = convert
        queue_backend.add("movie.DV.mkv")
        app = DolbyQueueApp(backend=queue_backend)
        async with app.run_test() as pilot:
            await pilot.press("s")
            for _ in range(20):
                await pilot.pause()
                if queue_backend.model.items[0].status is QueueStatus.COMPLETED:
                    break
            item = queue_backend.model.items[0]
            assert item.status is QueueStatus.COMPLETED
            assert item.progress_percent == 100
            assert item.eta_seconds == 0
    run(scenario())


def test_tui_uses_narrow_layout_class_on_small_terminal(tmp_path):
    async def scenario():
        app = DolbyQueueApp(backend=backend(tmp_path))
        async with app.run_test(size=(70, 24)):
            assert app.screen.has_class("narrow")
    run(scenario())
