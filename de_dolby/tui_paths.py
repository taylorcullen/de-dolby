"""Cross-platform path semantics and asynchronous directory browsing."""

from __future__ import annotations

import asyncio
import ntpath
import os
import posixpath
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

MEDIA_SUFFIXES = {".mkv", ".mp4", ".ts"}


class BrowseState(str, Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    PERMISSION_DENIED = "permission_denied"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class BrowserEntry:
    path: str
    name: str
    is_directory: bool


@dataclass(frozen=True)
class BrowseResult:
    path: str
    state: BrowseState
    entries: tuple[BrowserEntry, ...] = ()
    message: str | None = None


def is_windows_path(path: str) -> bool:
    return path.startswith(("\\\\", "//")) or bool(
        re.match(r"^[A-Za-z]:[\\/]", path)
    )


def canonical_path_identity(path: str) -> str:
    """Normalize identity using the path's own flavor, not the host OS."""
    expanded = os.path.expanduser(path)
    if is_windows_path(expanded):
        return ntpath.normcase(ntpath.abspath(expanded))
    if expanded.startswith("/"):
        return posixpath.normpath(expanded)
    return os.path.normcase(os.path.abspath(expanded))


def display_basename(path: str) -> str:
    if is_windows_path(path):
        return ntpath.basename(ntpath.normpath(path)) or path
    return Path(path).name or path


def _list_directory(path: str) -> tuple[BrowserEntry, ...]:
    entries = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            try:
                is_directory = entry.is_dir()
            except OSError:
                continue
            if is_directory or Path(entry.name).suffix.lower() in MEDIA_SUFFIXES:
                entries.append(
                    BrowserEntry(entry.path, entry.name, is_directory)
                )
    return tuple(sorted(entries, key=lambda item: (not item.is_directory, item.name.lower())))


async def browse_directory(
    path: str,
    *,
    timeout_seconds: float = 5.0,
    lister: Callable[[str], Iterable[BrowserEntry]] = _list_directory,
) -> BrowseResult:
    """List a path off the event loop with bounded, actionable failure states."""
    try:
        entries = await asyncio.wait_for(
            asyncio.to_thread(lambda: tuple(lister(path))),
            timeout=timeout_seconds,
        )
        return BrowseResult(path, BrowseState.READY, entries)
    except asyncio.TimeoutError:
        return BrowseResult(
            path, BrowseState.TIMED_OUT,
            message=f"Timed out after {timeout_seconds:g}s",
        )
    except PermissionError as exc:
        return BrowseResult(path, BrowseState.PERMISSION_DENIED, message=str(exc))
    except FileNotFoundError as exc:
        return BrowseResult(path, BrowseState.UNAVAILABLE, message=str(exc))
    except asyncio.CancelledError:
        return BrowseResult(path, BrowseState.CANCELLED, message="Browsing cancelled")
    except OSError as exc:
        return BrowseResult(path, BrowseState.ERROR, message=str(exc))
