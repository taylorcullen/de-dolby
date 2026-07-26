"""Tests for manifest schema, fingerprints, and atomic persistence."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from de_dolby.manifest import (
    MANIFEST_SCHEMA_VERSION,
    BatchManifest,
    ManifestEntry,
    ManifestStore,
    ManifestStatus,
    ResumeAction,
    fingerprint_plan,
    identify_input,
    load_manifest,
    manifest_document,
    save_manifest,
    select_resume_action,
)
from de_dolby.plan import create_conversion_plan
from de_dolby.probe import FileInfo, StreamInfo


def sample_plan(output="output.mkv"):
    info = FileInfo(
        path="input.mkv", duration=10.0, overall_bitrate=8_000_000,
        dv_profile=7,
        video_streams=[
            StreamInfo(index=0, codec_type="video", codec_name="hevc")
        ],
    )
    return create_conversion_plan(info, output)


def test_input_identity_is_normalized_and_detects_content_metadata(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"media")
    identity = identify_input(source)
    assert identity.path == str(source.resolve())
    assert identity.size == 5
    assert identity.modified_ns == source.stat().st_mtime_ns


def test_plan_fingerprint_is_deterministic_and_option_sensitive():
    assert fingerprint_plan(sample_plan()) == fingerprint_plan(sample_plan())
    assert fingerprint_plan(sample_plan("other.mkv")) != fingerprint_plan(
        sample_plan()
    )
    assert len(fingerprint_plan(sample_plan())) == 64


def test_manifest_round_trip_uses_versioned_schema(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"media")
    entry = ManifestEntry(
        identify_input(source),
        "output.mkv",
        fingerprint_plan(sample_plan()),
        status=ManifestStatus.COMPLETED,
        attempts=1,
        validation_valid=True,
    )
    manifest = BatchManifest({"movie": entry})
    path = tmp_path / "batch.json"

    save_manifest(path, manifest)
    loaded = load_manifest(path)

    assert loaded == manifest
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert "environment" not in json.dumps(document).lower()


def test_manifest_atomic_replace_receives_complete_parseable_json(tmp_path):
    path = tmp_path / "batch.json"
    manifest = BatchManifest()
    real_replace = __import__("os").replace

    def inspect_then_replace(source, destination):
        assert json.loads(source.read_text(encoding="utf-8"))["entries"] == {}
        real_replace(source, destination)

    with patch("de_dolby.manifest.os.replace", side_effect=inspect_then_replace):
        save_manifest(path, manifest)
    assert path.is_file()


def test_failed_atomic_write_removes_temporary_file(tmp_path):
    path = tmp_path / "batch.json"
    with patch("de_dolby.manifest.os.replace", side_effect=OSError("denied")):
        with pytest.raises(RuntimeError, match="atomically save"):
            save_manifest(path, BatchManifest())
    assert list(tmp_path.glob(".*.tmp")) == []


def test_old_schema_has_actionable_migration_error(tmp_path):
    path = tmp_path / "old.json"
    path.write_text('{"schema_version": 0, "entries": {}}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="Start a new manifest or migrate"):
        load_manifest(path)


def test_manifest_document_has_no_process_environment():
    text = json.dumps(manifest_document(BatchManifest()))
    assert "PATH" not in text
    assert "token" not in text.lower()


def deterministic_clock():
    moments = iter([
        datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc),
    ])
    return lambda: next(moments)


def test_sequential_success_transitions_are_durable(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"media")
    path = tmp_path / "batch.json"
    store = ManifestStore(path, clock=deterministic_clock())

    key = store.register(
        identify_input(source), "output.mkv", fingerprint_plan(sample_plan())
    )
    assert load_manifest(path).entries[key].status is ManifestStatus.PENDING

    store.start(key)
    running = load_manifest(path).entries[key]
    assert running.status is ManifestStatus.RUNNING
    assert running.attempts == 1
    assert running.started_at == "2026-01-01T12:00:00+00:00"

    store.complete(key, validation_valid=True)
    completed = load_manifest(path).entries[key]
    assert completed.status is ManifestStatus.COMPLETED
    assert completed.validation_valid is True
    assert completed.finished_at == "2026-01-01T12:05:00+00:00"


def test_failed_attempt_can_transition_to_deterministic_retry(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"media")
    path = tmp_path / "batch.json"
    store = ManifestStore(path, clock=deterministic_clock())
    key = store.register(
        identify_input(source), "output.mkv", fingerprint_plan(sample_plan())
    )
    store.start(key)
    store.fail(key, "conversion failed")
    failed = load_manifest(path).entries[key]
    assert failed.status is ManifestStatus.FAILED
    assert failed.error == "conversion failed"

    store.start(key)
    retried = load_manifest(path).entries[key]
    assert retried.status is ManifestStatus.RUNNING
    assert retried.attempts == 2
    assert retried.error is None


def test_invalid_transition_does_not_rewrite_state(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"media")
    path = tmp_path / "batch.json"
    store = ManifestStore(path)
    key = store.register(
        identify_input(source), "output.mkv", fingerprint_plan(sample_plan())
    )
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="Cannot complete"):
        store.complete(key, validation_valid=True)
    assert path.read_bytes() == before


def test_every_transition_writes_parseable_manifest(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"media")
    store = ManifestStore(tmp_path / "batch.json")
    real_save = __import__("de_dolby.manifest", fromlist=["save_manifest"]).save_manifest
    snapshots = []

    def save_and_capture(path, manifest):
        real_save(path, manifest)
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))

    with patch("de_dolby.manifest.save_manifest", side_effect=save_and_capture):
        key = store.register(
            identify_input(source), "output.mkv", fingerprint_plan(sample_plan())
        )
        store.start(key)
        store.fail(key, "interrupted")
    assert [item["entries"][key]["status"] for item in snapshots] == [
        "pending", "running", "failed"
    ]


def identity(path, size=5, modified=1):
    from de_dolby.manifest import InputIdentity
    return InputIdentity(path, size, modified)


def completed_entry():
    return ManifestEntry(
        identity("/input.mkv"),
        "/output.mkv",
        "plan-a",
        status=ManifestStatus.COMPLETED,
        validation_valid=True,
        output_identity=identity("/output.mkv", size=10, modified=2),
    )


def decide(entry=None, **overrides):
    values = {
        "current_input": identity("/input.mkv"),
        "current_plan_fingerprint": "plan-a",
        "current_output": identity("/output.mkv", size=10, modified=2),
        "retry_failed": False,
    }
    values.update(overrides)
    return select_resume_action(entry, **values)


def test_resume_skips_only_matching_validated_output():
    decision = decide(completed_entry())
    assert decision.action is ResumeAction.SKIP
    assert decision.reason == "validated_output_matches"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"current_input": identity("/input.mkv", size=6)}, "input_changed"),
        ({"current_plan_fingerprint": "plan-b"}, "plan_changed"),
        ({"current_output": None}, "output_changed_or_missing"),
        (
            {"current_output": identity("/output.mkv", size=11, modified=2)},
            "output_changed_or_missing",
        ),
    ],
)
def test_resume_reruns_stale_or_mismatched_completed_item(overrides, reason):
    decision = decide(completed_entry(), **overrides)
    assert decision.action is ResumeAction.RUN
    assert decision.reason == reason


def test_resume_does_not_trust_unvalidated_completed_entry():
    entry = completed_entry()
    entry.validation_valid = False
    assert decide(entry).reason == "validation_not_confirmed"


def test_failed_entries_require_retry_failed_selection():
    entry = completed_entry()
    entry.status = ManifestStatus.FAILED
    assert decide(entry).action is ResumeAction.SKIP
    decision = decide(entry, retry_failed=True)
    assert decision.action is ResumeAction.RUN
    assert decision.reason == "retry_failed_selected"


def test_interrupted_running_entry_is_retryable():
    entry = completed_entry()
    entry.status = ManifestStatus.RUNNING
    assert decide(entry).reason == "interrupted_attempt"
