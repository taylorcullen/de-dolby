"""Read-only environment probes used by the doctor command."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from de_dolby.codecs import ENCODERS
from de_dolby.tools import ToolPaths


@dataclass(frozen=True)
class ToolDiagnostic:
    """The resolved location and reported version of an external tool."""

    name: str
    configured_path: str
    resolved_path: str | None
    version: str | None
    error: str | None

    @property
    def available(self) -> bool:
        return self.resolved_path is not None


@dataclass(frozen=True)
class FfmpegCapabilities:
    """Capabilities advertised by an ffmpeg build."""

    encoders: frozenset[str]
    filters: frozenset[str]
    errors: tuple[str, ...] = ()

    @property
    def has_libplacebo(self) -> bool:
        return "libplacebo" in self.filters


@dataclass(frozen=True)
class TempDirectoryDiagnostic:
    """Read-only accessibility and free-space facts for a temp directory."""

    path: str
    exists: bool
    writable: bool
    free_bytes: int | None
    error: str | None = None


@dataclass(frozen=True)
class ProfileReadiness:
    """Whether one Dolby Vision profile's conversion route can run."""

    profile: int
    ready: bool
    encoders: tuple[str, ...]
    reasons: tuple[str, ...]


RunProbe = Callable[[list[str]], subprocess.CompletedProcess]
ResolveExecutable = Callable[[str], str | None]
DOCTOR_SCHEMA_VERSION = 2


class DiskUsage(Protocol):
    free: int


def _run_probe(command: list[str]) -> subprocess.CompletedProcess:
    """Run a bounded, read-only diagnostic command."""
    return subprocess.run(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=10,
    )


def _text(result: subprocess.CompletedProcess) -> str:
    stdout = result.stdout.decode(errors="replace") if result.stdout else ""
    stderr = result.stderr.decode(errors="replace") if result.stderr else ""
    return "\n".join(part for part in (stdout, stderr) if part)


def probe_tool_version(
    name: str,
    configured_path: str,
    *,
    version_args: Iterable[str] = ("--version",),
    resolve: ResolveExecutable = shutil.which,
    run: RunProbe = _run_probe,
) -> ToolDiagnostic:
    """Resolve a tool and obtain the first non-empty version output line."""
    resolved = resolve(configured_path)
    if resolved is None:
        return ToolDiagnostic(
            name, configured_path, None, None, f"{name} executable was not found"
        )
    try:
        result = run([resolved, *version_args])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolDiagnostic(name, configured_path, resolved, None, str(exc))

    version = next(
        (line.strip() for line in _text(result).splitlines() if line.strip()), None
    )
    error = None
    if result.returncode != 0:
        error = f"{name} version probe exited with status {result.returncode}"
    elif version is None:
        error = f"{name} version probe returned no output"
    return ToolDiagnostic(name, configured_path, resolved, version, error)


def probe_tool_versions(
    paths: ToolPaths,
    *,
    resolve: ResolveExecutable = shutil.which,
    run: RunProbe = _run_probe,
) -> tuple[ToolDiagnostic, ...]:
    """Probe all configured external tools without requiring media input."""
    specs = (
        ("ffmpeg", paths.ffmpeg, ("-version",)),
        ("ffprobe", paths.ffprobe, ("-version",)),
        ("dovi_tool", paths.dovi_tool, ("--version",)),
        ("mkvmerge", paths.mkvmerge, ("--version",)),
    )
    return tuple(
        probe_tool_version(name, path, version_args=args, resolve=resolve, run=run)
        for name, path, args in specs
    )


def _advertised_names(output: str, known_names: Iterable[str]) -> frozenset[str]:
    return frozenset(
        name for name in known_names
        if re.search(rf"(?m)^\s*[A-Z.]+\s+{re.escape(name)}(?:\s|$)", output)
    )


def probe_ffmpeg_capabilities(
    ffmpeg_path: str,
    *,
    resolve: ResolveExecutable = shutil.which,
    run: RunProbe = _run_probe,
) -> FfmpegCapabilities:
    """Probe configured encoders and filters advertised by ffmpeg."""
    resolved = resolve(ffmpeg_path)
    if resolved is None:
        return FfmpegCapabilities(
            frozenset(), frozenset(), ("ffmpeg executable was not found",)
        )

    encoders: frozenset[str] = frozenset()
    filters: frozenset[str] = frozenset()
    errors: list[str] = []
    for capability, args in (
        ("encoders", ["-hide_banner", "-encoders"]),
        ("filters", ["-hide_banner", "-filters"]),
    ):
        try:
            result = run([resolved, *args])
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"ffmpeg {capability} probe failed: {exc}")
            continue
        if result.returncode != 0:
            errors.append(
                f"ffmpeg {capability} probe exited with status {result.returncode}"
            )
            continue
        output = _text(result)
        if capability == "encoders":
            encoders = _advertised_names(output, ENCODERS.keys() - {"copy"})
        else:
            filters = _advertised_names(output, ("libplacebo",))
    return FfmpegCapabilities(encoders, filters, tuple(errors))


def configured_tool_paths(
    *,
    ffmpeg: str | None = None,
    dovi_tool: str | None = None,
    mkvmerge: str | None = None,
) -> ToolPaths:
    """Build diagnostic paths, deriving ffprobe beside a custom ffmpeg."""
    paths = ToolPaths()
    if ffmpeg:
        paths.ffmpeg = ffmpeg
        executable = Path(ffmpeg)
        paths.ffprobe = str(
            executable.with_name("ffprobe" + executable.suffix)
        )
    if dovi_tool:
        paths.dovi_tool = dovi_tool
    if mkvmerge:
        paths.mkvmerge = mkvmerge
    return paths


def probe_temp_directory(
    configured_path: str | None,
    *,
    is_dir: Callable[[str], bool] = lambda path: Path(path).is_dir(),
    is_writable: Callable[[str], bool] = lambda path: os.access(path, os.W_OK),
    disk_usage: Callable[[str], DiskUsage] = shutil.disk_usage,
) -> TempDirectoryDiagnostic:
    """Inspect a temp directory without creating or modifying files."""
    path = configured_path or tempfile.gettempdir()
    if not is_dir(path):
        return TempDirectoryDiagnostic(
            path, exists=False, writable=False, free_bytes=None,
            error="temp directory does not exist",
        )
    writable = is_writable(path)
    try:
        free_bytes = disk_usage(path).free
        error = None
    except OSError as exc:
        free_bytes = None
        error = f"could not determine free space: {exc}"
    if not writable:
        error = "temp directory is not writable"
    return TempDirectoryDiagnostic(path, True, writable, free_bytes, error)


def aggregate_profile_readiness(
    tools: Iterable[ToolDiagnostic],
    capabilities: FfmpegCapabilities,
    temp: TempDirectoryDiagnostic,
) -> tuple[ProfileReadiness, ...]:
    """Aggregate tool and capability facts for each supported DV profile."""
    available = {
        tool.name: tool.available and tool.error is None for tool in tools
    }
    common_reasons = [
        f"{name} is unavailable"
        for name in ("ffmpeg", "ffprobe", "mkvmerge")
        if not available.get(name, False)
    ]
    if capabilities.errors:
        common_reasons.append("ffmpeg capabilities could not be determined")
    if not temp.writable:
        common_reasons.append("temp directory is not writable")

    hevc_encoders = tuple(sorted(
        name for name in capabilities.encoders
        if ENCODERS[name].codec_family == "hevc"
    ))
    av1_encoders = tuple(sorted(
        name for name in capabilities.encoders
        if ENCODERS[name].codec_family == "av1"
    ))

    results = []
    for profile in (5, 7, 8, 10):
        reasons = list(common_reasons)
        encoders: tuple[str, ...] = ()
        if profile in (5, 7, 8) and not available.get("dovi_tool", False):
            reasons.append("dovi_tool is unavailable")
        if profile == 5:
            encoders = hevc_encoders
            if not capabilities.has_libplacebo:
                reasons.append("ffmpeg libplacebo filter is unavailable")
            if not encoders:
                reasons.append("no supported HEVC encoder is available")
        elif profile == 10:
            encoders = av1_encoders
            if not encoders:
                reasons.append("no supported AV1 encoder is available")
        results.append(ProfileReadiness(profile, not reasons, encoders, tuple(reasons)))
    return tuple(results)


def diagnostic_document(
    tools: Iterable[ToolDiagnostic],
    capabilities: FfmpegCapabilities,
    temp: TempDirectoryDiagnostic,
    requested_profile: int | None = None,
) -> dict:
    """Return the stable, JSON-serializable doctor schema."""
    tool_items = tuple(tools)
    profiles = aggregate_profile_readiness(tool_items, capabilities, temp)
    selected = (
        tuple(profile for profile in profiles if profile.profile == requested_profile)
        if requested_profile is not None else profiles
    )
    usable = any(profile.ready for profile in selected)
    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "usable": usable,
        "requested_profile": requested_profile,
        "tools": {
            tool.name: {
                "configured_path": tool.configured_path,
                "resolved_path": tool.resolved_path,
                "available": tool.available,
                "version": tool.version,
                "error": tool.error,
            }
            for tool in tool_items
        },
        "ffmpeg": {
            "encoders": sorted(capabilities.encoders),
            "filters": sorted(capabilities.filters),
            "libplacebo": capabilities.has_libplacebo,
            "errors": list(capabilities.errors),
        },
        "temp_directory": {
            "path": temp.path,
            "exists": temp.exists,
            "writable": temp.writable,
            "free_bytes": temp.free_bytes,
            "error": temp.error,
        },
        "profiles": {
            str(profile.profile): {
                "ready": profile.ready,
                "encoders": list(profile.encoders),
                "reasons": list(profile.reasons),
            }
            for profile in profiles
        },
    }
