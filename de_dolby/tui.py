"""Optional entry boundary for the interactive terminal interface."""

from __future__ import annotations


def launch_tui(queue_path: str | None = None) -> None:
    """Launch the Textual application while keeping Textual an optional extra."""
    try:
        from de_dolby.tui_app import DolbyQueueApp
    except ImportError as exc:
        if exc.name and (exc.name == "textual" or exc.name.startswith("textual.")):
            raise RuntimeError(
                "The TUI is not installed. Install it with: pip install 'de-dolby[tui]'"
            ) from exc
        raise
    DolbyQueueApp(queue_path=queue_path).run()
