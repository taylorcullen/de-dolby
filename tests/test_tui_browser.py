import asyncio

from textual.widgets import Input, ListView, Static

from de_dolby.tui_app import DolbyQueueApp
from de_dolby.tui_paths import BrowseResult, BrowseState, BrowserEntry
from de_dolby.tui_queue import QueueBackend, QueueStore


def test_headless_browser_adds_network_file_without_blocking_ui(tmp_path):
    async def fake_browser(path):
        await asyncio.sleep(0)
        return BrowseResult(
            path,
            BrowseState.READY,
            (BrowserEntry(
                r"\\server\share\Movie.DV.mkv", "Movie.DV.mkv", False
            ),),
        )

    async def scenario():
        app = DolbyQueueApp(
            backend=QueueBackend(QueueStore(tmp_path / "queue.json")),
            browser=fake_browser,
        )
        async with app.run_test() as pilot:
            await pilot.press("2")
            browser_input = app.query_one("#browser-path", Input)
            browser_input.focus()
            browser_input.value = r"\\server\share"
            await pilot.press("enter")
            await pilot.pause()
            assert "1 entries" in str(app.query_one("#browser-state", Static).render())
            listing = app.query_one("#browser-list", ListView)
            listing.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.backend.model.items[0].input_path.startswith("\\\\server")

    asyncio.run(scenario())


def test_headless_browser_renders_network_failure_state(tmp_path):
    async def fake_browser(path):
        return BrowseResult(
            path, BrowseState.UNAVAILABLE, message="share offline"
        )

    async def scenario():
        app = DolbyQueueApp(
            backend=QueueBackend(QueueStore(tmp_path / "queue.json")),
            browser=fake_browser,
        )
        async with app.run_test() as pilot:
            await pilot.press("2")
            browser_input = app.query_one("#browser-path", Input)
            browser_input.focus()
            browser_input.value = r"\\server\offline"
            await pilot.press("enter")
            await pilot.pause()
            rendered = str(app.query_one("#browser-state", Static).render())
            assert "share offline" in rendered

    asyncio.run(scenario())


def test_headless_browser_marks_and_adds_multiple_files(tmp_path):
    async def fake_browser(path):
        return BrowseResult(
            path,
            BrowseState.READY,
            (
                BrowserEntry(r"\\server\share\one.mkv", "one.mkv", False),
                BrowserEntry(r"\\server\share\two.mkv", "two.mkv", False),
            ),
        )

    async def scenario():
        app = DolbyQueueApp(
            backend=QueueBackend(QueueStore(tmp_path / "queue.json")),
            browser=fake_browser,
        )
        async with app.run_test():
            app.action_show_view("browser")
            await app._browse(r"\\server\share")
            listing = app.query_one("#browser-list", ListView)
            listing.index = 0
            await app.action_toggle_browser_mark()
            listing.index = 1
            await app.action_toggle_browser_mark()
            await app.action_add_browser_items()
            assert [item.display_name for item in app.backend.model.items] == [
                "one.mkv", "two.mkv"
            ]

    asyncio.run(scenario())
