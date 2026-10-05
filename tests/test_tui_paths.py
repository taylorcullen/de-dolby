import asyncio
import time

from de_dolby.tui_paths import (
    BrowseState,
    BrowserEntry,
    browse_directory,
    canonical_path_identity,
    display_basename,
)


def run(coro):
    return asyncio.run(coro)


def test_unc_identity_uses_windows_semantics_on_every_host():
    first = canonical_path_identity(r"\\Server\Share\Folder\..\Movie.DV.mkv")
    second = canonical_path_identity(r"\\server\share\movie.dv.mkv")
    assert first == second
    assert first.startswith(r"\\server\share")
    assert display_basename(r"\\server\share\Movie.DV.mkv") == "Movie.DV.mkv"


def test_mapped_drive_and_mounted_share_identities_keep_path_flavor():
    assert canonical_path_identity(r"Z:\Media\..\Movie.mkv") == r"z:\movie.mkv"
    mounted = canonical_path_identity("/mnt/media/../share/Movie.mkv")
    assert mounted.endswith("/mnt/share/Movie.mkv")


def test_browse_directory_returns_sorted_media_and_directories():
    entries = [
        BrowserEntry("/share/movie.mkv", "movie.mkv", False),
        BrowserEntry("/share/Season", "Season", True),
    ]
    result = run(browse_directory("/share", lister=lambda path: entries))
    assert result.state is BrowseState.READY
    assert result.entries == tuple(entries)


def test_browse_directory_reports_timeout_without_raising():
    def slow(path):
        time.sleep(0.1)
        return ()

    result = run(
        browse_directory("/slow", timeout_seconds=0.01, lister=slow)
    )
    assert result.state is BrowseState.TIMED_OUT


def test_browse_directory_reports_permission_and_unavailable():
    def denied(path):
        raise PermissionError("access denied")

    denied_result = run(browse_directory("/denied", lister=denied))
    assert denied_result.state is BrowseState.PERMISSION_DENIED

    def missing(path):
        raise FileNotFoundError("share offline")

    missing_result = run(browse_directory("/missing", lister=missing))
    assert missing_result.state is BrowseState.UNAVAILABLE
