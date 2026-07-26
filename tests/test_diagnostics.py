"""Tests for read-only environment diagnostic probes."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

from de_dolby.diagnostics import (
    DOCTOR_SCHEMA_VERSION,
    FfmpegCapabilities,
    TempDirectoryDiagnostic,
    ToolDiagnostic,
    aggregate_profile_readiness,
    configured_tool_paths,
    diagnostic_document,
    probe_ffmpeg_capabilities,
    probe_temp_directory,
    probe_tool_version,
    probe_tool_versions,
)
from de_dolby.tools import ToolPaths


def completed(stdout=b"", stderr=b"", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_probe_tool_version_reports_resolved_path_and_version():
    run = Mock(return_value=completed(stdout=b"ffmpeg version 7.1\nbuilt with gcc"))
    result = probe_tool_version(
        "ffmpeg", "custom-ffmpeg", version_args=("-version",),
        resolve=lambda path: f"/tools/{path}", run=run,
    )
    assert result.available is True
    assert result.configured_path == "custom-ffmpeg"
    assert result.resolved_path == "/tools/custom-ffmpeg"
    assert result.version == "ffmpeg version 7.1"
    assert result.error is None
    run.assert_called_once_with(["/tools/custom-ffmpeg", "-version"])


def test_probe_tool_version_distinguishes_missing_executable():
    run = Mock()
    result = probe_tool_version(
        "dovi_tool", "/missing/dovi_tool", resolve=lambda path: None, run=run
    )
    assert result.available is False
    assert result.version is None
    assert result.error == "dovi_tool executable was not found"
    run.assert_not_called()


def test_probe_tool_version_preserves_failed_probe_as_error():
    result = probe_tool_version(
        "mkvmerge", "mkvmerge", resolve=lambda path: path,
        run=Mock(return_value=completed(stderr=b"bad option", returncode=2)),
    )
    assert result.available is True
    assert result.version == "bad option"
    assert result.error == "mkvmerge version probe exited with status 2"


def test_probe_tool_versions_uses_each_tools_version_syntax():
    run = Mock(return_value=completed(stdout=b"tool version 1"))
    paths = ToolPaths(
        ffmpeg="custom-ffmpeg", ffprobe="custom-ffprobe",
        dovi_tool="custom-dovi", mkvmerge="custom-mkvmerge",
    )
    results = probe_tool_versions(paths, resolve=lambda path: path, run=run)
    assert [result.name for result in results] == [
        "ffmpeg", "ffprobe", "dovi_tool", "mkvmerge"
    ]
    assert [call.args[0] for call in run.call_args_list] == [
        ["custom-ffmpeg", "-version"],
        ["custom-ffprobe", "-version"],
        ["custom-dovi", "--version"],
        ["custom-mkvmerge", "--version"],
    ]


def test_probe_ffmpeg_capabilities_parses_known_encoders_and_libplacebo():
    run = Mock(side_effect=[
        completed(stdout=(
            b" V..... hevc_nvenc NVIDIA NVENC\n"
            b" V..... libx265 libx265 encoder\n"
            b" V..... unrelated another encoder\n"
        )),
        completed(stdout=b" ..C libplacebo V->V Apply libplacebo\n"),
    ])
    result = probe_ffmpeg_capabilities(
        "custom-ffmpeg", resolve=lambda path: "/tools/ffmpeg", run=run
    )
    assert result == FfmpegCapabilities(
        encoders=frozenset({"hevc_nvenc", "libx265"}),
        filters=frozenset({"libplacebo"}),
    )
    assert result.has_libplacebo is True


def test_probe_ffmpeg_capabilities_covers_partial_installation():
    run = Mock(side_effect=[
        completed(stdout=b" V..... libx265 libx265 encoder\n"),
        completed(stderr=b"filters unavailable", returncode=1),
    ])
    result = probe_ffmpeg_capabilities(
        "ffmpeg", resolve=lambda path: path, run=run
    )
    assert result.encoders == frozenset({"libx265"})
    assert result.filters == frozenset()
    assert result.errors == ("ffmpeg filters probe exited with status 1",)


def test_probe_ffmpeg_capabilities_does_not_run_when_ffmpeg_missing():
    run = Mock()
    result = probe_ffmpeg_capabilities(
        "/missing/ffmpeg", resolve=lambda path: None, run=run
    )
    assert result.errors == ("ffmpeg executable was not found",)
    run.assert_not_called()


def test_configured_tool_paths_reflects_custom_paths_and_derives_ffprobe():
    paths = configured_tool_paths(
        ffmpeg="/opt/media/ffmpeg",
        dovi_tool="/opt/dovi/dovi_tool",
        mkvmerge="/opt/mkv/mkvmerge",
    )
    assert paths.ffmpeg == "/opt/media/ffmpeg"
    assert Path(paths.ffprobe).name == "ffprobe"
    assert Path(paths.ffprobe).parent == Path("/opt/media")
    assert paths.dovi_tool == "/opt/dovi/dovi_tool"
    assert paths.mkvmerge == "/opt/mkv/mkvmerge"


def test_diagnostic_document_has_versioned_stable_schema():
    tools = (
        ToolDiagnostic("ffmpeg", "ffmpeg", "/bin/ffmpeg", "ffmpeg 7", None),
        ToolDiagnostic("ffprobe", "ffprobe", "/bin/ffprobe", "ffprobe 7", None),
        ToolDiagnostic("dovi_tool", "dovi_tool", "/bin/dovi_tool", "2.2", None),
        ToolDiagnostic("mkvmerge", "mkvmerge", "/bin/mkvmerge", "88", None),
    )
    document = diagnostic_document(
        tools,
        FfmpegCapabilities(frozenset({"libx265"}), frozenset({"libplacebo"})),
        TempDirectoryDiagnostic("/tmp", True, True, 1_000_000),
    )
    assert document["schema_version"] == DOCTOR_SCHEMA_VERSION == 2
    assert document["usable"] is True
    assert set(document) == {
        "schema_version", "usable", "requested_profile", "tools", "ffmpeg",
        "temp_directory", "profiles",
    }
    assert set(document["tools"]["ffmpeg"]) == {
        "configured_path", "resolved_path", "available", "version", "error"
    }
    assert document["ffmpeg"] == {
        "encoders": ["libx265"],
        "filters": ["libplacebo"],
        "libplacebo": True,
        "errors": [],
    }


def test_diagnostic_document_is_not_usable_with_partial_installation():
    tools = (
        ToolDiagnostic("ffmpeg", "ffmpeg", "/bin/ffmpeg", "ffmpeg 7", None),
        ToolDiagnostic(
            "dovi_tool", "dovi_tool", None, None,
            "dovi_tool executable was not found",
        ),
    )
    document = diagnostic_document(
        tools, FfmpegCapabilities(frozenset({"libx265"}), frozenset()),
        TempDirectoryDiagnostic("/tmp", True, True, 1_000_000),
    )
    assert document["usable"] is False


def test_probe_temp_directory_reports_custom_path_and_free_space():
    usage = Mock()
    usage.free = 42_000
    result = probe_temp_directory(
        "/scratch", is_dir=lambda path: True,
        is_writable=lambda path: True, disk_usage=lambda path: usage,
    )
    assert result == TempDirectoryDiagnostic("/scratch", True, True, 42_000)


def test_probe_temp_directory_reports_missing_without_disk_probe():
    disk_usage = Mock()
    result = probe_temp_directory(
        "/missing", is_dir=lambda path: False, disk_usage=disk_usage
    )
    assert result.exists is False
    assert result.writable is False
    assert result.error == "temp directory does not exist"
    disk_usage.assert_not_called()


def test_profile_readiness_models_each_pipeline_requirement():
    tools = (
        ToolDiagnostic("ffmpeg", "ffmpeg", "/bin/ffmpeg", "7", None),
        ToolDiagnostic("ffprobe", "ffprobe", "/bin/ffprobe", "7", None),
        ToolDiagnostic("dovi_tool", "dovi_tool", None, None, "missing"),
        ToolDiagnostic("mkvmerge", "mkvmerge", "/bin/mkvmerge", "88", None),
    )
    profiles = {
        result.profile: result
        for result in aggregate_profile_readiness(
            tools,
            FfmpegCapabilities(
                frozenset({"libx265", "libsvtav1"}), frozenset({"libplacebo"})
            ),
            TempDirectoryDiagnostic("/tmp", True, True, 1_000_000),
        )
    }
    assert profiles[5].ready is False
    assert profiles[7].ready is False
    assert profiles[8].ready is False
    assert profiles[10].ready is True
    assert profiles[5].encoders == ("libx265",)
    assert profiles[10].encoders == ("libsvtav1",)


def test_requested_profile_controls_document_usability():
    tools = (
        ToolDiagnostic("ffmpeg", "ffmpeg", "/bin/ffmpeg", "7", None),
        ToolDiagnostic("ffprobe", "ffprobe", "/bin/ffprobe", "7", None),
        ToolDiagnostic("dovi_tool", "dovi_tool", None, None, "missing"),
        ToolDiagnostic("mkvmerge", "mkvmerge", "/bin/mkvmerge", "88", None),
    )
    capabilities = FfmpegCapabilities(
        frozenset({"libsvtav1"}), frozenset()
    )
    temp = TempDirectoryDiagnostic("/tmp", True, True, 1_000_000)
    assert diagnostic_document(
        tools, capabilities, temp, requested_profile=10
    )["usable"] is True
    assert diagnostic_document(
        tools, capabilities, temp, requested_profile=7
    )["usable"] is False
