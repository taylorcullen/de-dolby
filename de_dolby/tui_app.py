"""Textual application for interactive de-dolby queue management."""

from __future__ import annotations

import asyncio

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from de_dolby.tui_models import QueueItem, QueueStatus, TuiView
from de_dolby.tui_paths import BrowseResult, BrowseState, BrowserEntry, browse_directory
from de_dolby.tui_queue import QueueBackend, QueueProgress, QueueStore
from de_dolby.progress import format_eta


HELP_TEXT = """\
[b]Queue controls[/b]
  a / Enter path   Add an MKV
  Delete           Remove selected item
  Ctrl+Up/Down     Reorder selected item
  p                Plan selected item
  e                Apply key=value setting
  r                Retry failed/interrupted item
  s                Start sequential queue
  c                Cancel active conversion

[b]Views[/b]
  1 Queue   2 Browser   3 Details   4 Logs   5 Progress   ? Help
  Space            Mark/unmark a browser file
  Shift+A          Add marked files (or all visible files)
  q Quit
"""


class DolbyQueueApp(App):
    TITLE = "de-dolby conversion queue"
    CSS = """
    Screen { layout: vertical; }
    #workspace { height: 1fr; }
    #sidebar { width: 42; min-width: 28; border-right: solid $primary; }
    #path-input, #setting-input { margin: 0 1; }
    #queue-list { height: 1fr; }
    #content { width: 1fr; padding: 1 2; }
    .view-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #details-body, #queue-body, #browser-body { height: 1fr; }
    #help-view { padding: 1 2; }
    Screen.narrow #workspace { layout: vertical; }
    Screen.narrow #sidebar {
        width: 1fr;
        height: 45%;
        border-right: none;
        border-bottom: solid $primary;
    }
    Screen.narrow #content { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("a", "focus_add", "Add"),
        Binding("delete", "remove", "Remove"),
        Binding("ctrl+up", "move_up", "Move up"),
        Binding("ctrl+down", "move_down", "Move down"),
        Binding("p", "plan", "Plan"),
        Binding("e", "apply_setting", "Setting"),
        Binding("r", "retry", "Retry"),
        Binding("s", "start_queue", "Start"),
        Binding("c", "cancel", "Cancel"),
        Binding("1", "show_view('queue')", "Queue"),
        Binding("2", "show_view('browser')", "Browser"),
        Binding("3", "show_view('details')", "Details"),
        Binding("4", "show_view('logs')", "Logs"),
        Binding("5", "show_view('progress')", "Progress"),
        Binding("space", "toggle_browser_mark", "Mark"),
        Binding("shift+a", "add_browser_items", "Add visible"),
        Binding("question_mark", "show_view('help')", "Help"),
    ]

    def __init__(
        self,
        *,
        queue_path: str | None = None,
        backend: QueueBackend | None = None,
        browser=browse_directory,
    ):
        super().__init__()
        self.backend = backend or QueueBackend(QueueStore(queue_path))
        self.browser = browser
        self._browser_entries: tuple[BrowserEntry, ...] = ()
        self._browser_marks: set[int] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Label(" Conversion queue", classes="view-title")
                yield Input(
                    placeholder="Add local or network MKV path",
                    id="path-input",
                )
                yield ListView(id="queue-list")
            with Container(id="content"):
                with ContentSwitcher(initial="queue-view", id="switcher"):
                    with Vertical(id="queue-view"):
                        yield Label("Queue", classes="view-title")
                        yield Static(
                            "Add files, review their plans, then press [b]s[/b] "
                            "to start sequential conversion.",
                            id="queue-body",
                        )
                    with Vertical(id="browser-view"):
                        yield Label("Browser", classes="view-title")
                        yield Input(
                            placeholder=r"Directory or \\server\share path",
                            id="browser-path",
                        )
                        yield Static("Enter a path to browse.", id="browser-state")
                        yield ListView(id="browser-list")
                    with Vertical(id="details-view"):
                        yield Label("Details & settings", classes="view-title")
                        yield Static("No item selected", id="details-body")
                        yield Input(
                            placeholder="Setting, for example quality=quality",
                            id="setting-input",
                        )
                    with Vertical(id="logs-view"):
                        yield Label("Logs", classes="view-title")
                        yield RichLog(id="log")
                    with Vertical(id="progress-view"):
                        yield Label("Active conversion", classes="view-title")
                        yield Static("Queue idle", id="progress-body")
                    yield Static(HELP_TEXT, id="help-view")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_queue()
        self._set_responsive_class(self.size.width)

    def on_resize(self, event: events.Resize) -> None:
        self._set_responsive_class(event.size.width)

    def _set_responsive_class(self, width: int) -> None:
        self.screen.set_class(width < 80, "narrow")

    async def refresh_queue(self) -> None:
        queue = self.query_one("#queue-list", ListView)
        await queue.clear()
        for item in self.backend.model.items:
            await queue.append(ListItem(Label(self._item_label(item))))
        selected = self.backend.model.selected_index
        if selected is not None and self.backend.model.items:
            queue.index = selected
            queue.focus()
        self.refresh_details()

    @staticmethod
    def _item_label(item: QueueItem) -> str:
        progress = (
            f" {item.progress_percent:.1f}%"
            if item.progress_percent is not None else ""
        )
        eta = format_eta(item.eta_seconds)
        eta_label = f" ETA {eta}" if eta else ""
        return f"[{item.status.value:11}] {item.display_name}{progress}{eta_label}"

    def refresh_details(self) -> None:
        item = self.backend.model.selected
        details = self.query_one("#details-body", Static)
        if item is None:
            details.update("No item selected")
            return
        settings = ", ".join(
            f"{key}={value}" for key, value in sorted(item.settings.items())
        ) or "defaults"
        details.update(
            f"[b]Input:[/b] {item.input_path}\n"
            f"[b]Output:[/b] {item.output_path or 'automatic'}\n"
            f"[b]Status:[/b] {item.status.value}\n"
            f"[b]Plan:[/b] {item.plan_summary or 'not planned'}\n"
            f"[b]Settings:[/b] {settings}\n"
            f"[b]Error:[/b] {item.error or '-'}"
        )

    def log_message(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "path-input":
            path = event.value.strip()
            if path:
                item = self.backend.add(path)
                self.log_message(
                    f"Added {path}" if item else f"Skipped duplicate {path}"
                )
                event.input.value = ""
                await self.refresh_queue()
                self.query_one("#queue-list", ListView).focus()
        elif event.input.id == "setting-input":
            await self._apply_setting(event.value)
        elif event.input.id == "browser-path":
            self.run_worker(
                self._browse(event.value.strip()),
                name="directory-browser",
                exclusive=True,
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "browser-list" and event.list_view.index is not None:
            entry = self._browser_entries[event.list_view.index]
            if entry.is_directory:
                self.query_one("#browser-path", Input).value = entry.path
                self.run_worker(
                    self._browse(entry.path),
                    name="directory-browser",
                    exclusive=True,
                )
            else:
                item = self.backend.add(entry.path)
                self.log_message(
                    f"Added {entry.path}" if item else f"Skipped duplicate {entry.path}"
                )
                self.run_worker(self.refresh_queue())
            return
        if event.list_view.id != "queue-list" or event.list_view.index is None:
            return
        self.backend.model.select(event.list_view.index)
        self.backend.persist()
        self.refresh_details()

    def action_focus_add(self) -> None:
        self.query_one("#path-input", Input).focus()

    async def action_remove(self) -> None:
        removed = self.backend.model.remove_selected()
        if removed:
            self.backend.persist()
            self.log_message(f"Removed {removed.input_path}")
            await self.refresh_queue()

    async def action_move_up(self) -> None:
        if self.backend.model.move_selected(-1):
            self.backend.persist()
            await self.refresh_queue()

    async def action_move_down(self) -> None:
        if self.backend.model.move_selected(1):
            self.backend.persist()
            await self.refresh_queue()

    async def action_plan(self) -> None:
        item = self.backend.model.selected
        if item is None:
            return
        self.backend.plan(item)
        self.log_message(
            f"Planned {item.input_path}" if item.status is QueueStatus.READY
            else f"Plan failed: {item.error}"
        )
        await self.refresh_queue()

    async def _apply_setting(self, value: str) -> None:
        item = self.backend.model.selected
        if item is None or "=" not in value:
            return
        key, raw = (part.strip() for part in value.split("=", 1))
        converted: object = raw
        if raw.isdigit():
            converted = int(raw)
        elif raw.lower() in {"true", "false"}:
            converted = raw.lower() == "true"
        item.settings[key] = converted
        item.status = QueueStatus.PENDING
        item.plan_summary = None
        self.backend.persist()
        self.query_one("#setting-input", Input).value = ""
        self.log_message(f"Set {key}={converted}")
        await self.refresh_queue()
        self.query_one("#queue-list", ListView).focus()

    async def action_apply_setting(self) -> None:
        setting = self.query_one("#setting-input", Input)
        if setting.has_focus:
            await self._apply_setting(setting.value)
        else:
            self.action_show_view("details")
            setting.focus()

    async def action_retry(self) -> None:
        item = self.backend.model.selected
        if item:
            self.backend.retry(item)
            await self.refresh_queue()

    def action_show_view(self, view: str) -> None:
        selected = TuiView(view)
        self.backend.model.view = selected
        self.query_one("#switcher", ContentSwitcher).current = f"{view}-view"
        self.refresh_details()

    def action_start_queue(self) -> None:
        if any(
            item.status is QueueStatus.RUNNING
            for item in self.backend.model.items
        ):
            return
        self.log_message("Starting sequential queue")
        self.run_worker(
            self._execute_queue(),
            name="queue-executor",
            exclusive=True,
        )

    def action_cancel(self) -> None:
        self.backend.cancel()
        self.log_message("Cancellation requested")

    async def _execute_queue(self) -> None:
        def receive(event: QueueProgress) -> None:
            self.call_from_thread(self._receive_progress, event)

        await asyncio.to_thread(self.backend.execute_pending, receive)
        await self.refresh_queue()
        self.log_message("Queue idle")

    def _receive_progress(self, event: QueueProgress) -> None:
        if event.message:
            self.log_message(event.message)
        item = next(
            (candidate for candidate in self.backend.model.items
             if candidate.id == event.item_id),
            None,
        )
        if item is not None:
            percent = (
                f"{item.progress_percent:.1f}%"
                if item.progress_percent is not None else "Starting"
            )
            eta = format_eta(item.eta_seconds)
            self.query_one("#progress-body", Static).update(
                f"[b]{item.display_name}[/b]\n"
                f"Status: {item.status.value}\n"
                f"Progress: {percent}\n"
                f"ETA: {eta or 'calculating'}"
            )
        self.run_worker(self.refresh_queue(), name="queue-refresh", exclusive=True)

    async def _browse(self, path: str) -> None:
        if not path:
            return
        state = self.query_one("#browser-state", Static)
        state.update(f"Loading {path}…")
        result: BrowseResult = await self.browser(path)
        self._browser_entries = result.entries
        self._browser_marks.clear()
        if result.state is BrowseState.READY:
            await self._render_browser_entries()
            listing = self.query_one("#browser-list", ListView)
            state.update(f"{len(result.entries)} entries · {result.path}")
            if result.entries:
                listing.index = 0
                listing.focus()
        else:
            state.update(
                f"[b]{result.state.value.replace('_', ' ').title()}:[/b] "
                f"{result.message or result.path}"
            )

    async def _render_browser_entries(self) -> None:
        listing = self.query_one("#browser-list", ListView)
        selected = listing.index
        await listing.clear()
        for index, entry in enumerate(self._browser_entries):
            marker = "📁" if entry.is_directory else "🎞"
            checked = "✓" if index in self._browser_marks else " "
            await listing.append(
                ListItem(Label(f"[{checked}] {marker} {entry.name}"))
            )
        if self._browser_entries:
            listing.index = min(
                selected if selected is not None else 0,
                len(self._browser_entries) - 1,
            )

    async def action_toggle_browser_mark(self) -> None:
        if self.backend.model.view is not TuiView.BROWSER:
            return
        listing = self.query_one("#browser-list", ListView)
        index = listing.index
        if index is None or self._browser_entries[index].is_directory:
            return
        if index in self._browser_marks:
            self._browser_marks.remove(index)
        else:
            self._browser_marks.add(index)
        await self._render_browser_entries()

    async def action_add_browser_items(self) -> None:
        if self.backend.model.view is not TuiView.BROWSER:
            return
        indexes = self._browser_marks or {
            index for index, entry in enumerate(self._browser_entries)
            if not entry.is_directory
        }
        for index in sorted(indexes):
            entry = self._browser_entries[index]
            item = self.backend.add(entry.path)
            self.log_message(
                f"Added {entry.path}" if item else f"Skipped duplicate {entry.path}"
            )
        self._browser_marks.clear()
        await self._render_browser_entries()
        await self.refresh_queue()
