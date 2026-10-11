"""Capture README screenshots from the real TUI with synthetic example data.

Run from the repository root: python docs/capture_tui.py
Requires the project's optional [tui] dependencies. No media tools are run.
"""

import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.widgets import Input, ListView

from de_dolby.tui_app import DolbyQueueApp
from de_dolby.tui_models import QueueStatus
from de_dolby.tui_paths import BrowserEntry, BrowseResult, BrowseState
from de_dolby.tui_queue import QueueBackend, QueueProgress, QueueStore


OUTPUT = Path(__file__).resolve().parent / "images"
SHARE = r"\\nas\TV\Example Show\Season 01"
NAMES = ["01 - Pilot.DV.mkv", "02 - New Horizons.DV.mkv", "03 - Homeward.DV.mkv"]


async def browse(path):
    return BrowseResult(path, BrowseState.READY, tuple(
        BrowserEntry(path + "\\" + name, name, False) for name in NAMES
    ))


def plan(*args):
    return SimpleNamespace(
        pipeline=SimpleNamespace(value="lossless_rpu_strip"), encoder="copy",
        steps=("probe", "extract_video", "extract_rpu", "parse_metadata",
               "strip_rpu", "hdr10plus", "remux", "cleanup"), hdr10plus="preserve",
    )


async def capture():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="de-dolby-screenshots-") as temp:
        backend = QueueBackend(QueueStore(Path(temp) / "queue.json"), planner=plan)
        for name in NAMES:
            backend.add(SHARE + "\\" + name)
        app = DolbyQueueApp(backend=backend, browser=browse)
        async with app.run_test(size=(132, 26)) as pilot:
            await pilot.pause()
            app.action_show_view("browser")
            app.query_one("#browser-path", Input).value = SHARE
            await app._browse(SHARE)
            await app.action_toggle_browser_mark()
            app.query_one("#browser-list", ListView).index = 1
            await app.action_toggle_browser_mark()
            await pilot.pause()
            app.save_screenshot("tui-browser.svg", path=str(OUTPUT))

            backend.model.select(0)
            backend.model.selected.settings["hdr10plus"] = "auto"
            await app.action_plan()
            app.action_show_view("details")
            app.query_one("#setting-input", Input).value = "sample_seconds=30"
            app.query_one("#setting-input", Input).focus()
            await pilot.pause()
            app.save_screenshot("tui-details.svg", path=str(OUTPUT))

            item = backend.model.selected
            item.status = QueueStatus.RUNNING
            item.progress_percent = 62.5
            item.eta_seconds = 18
            app._receive_progress(QueueProgress(item.id, message="Remuxing HDR10 output (example progress)"))
            app.action_show_view("progress")
            await pilot.pause()
            app.save_screenshot("tui-progress.svg", path=str(OUTPUT))
    print(f"Saved three TUI screenshots to {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(capture())
