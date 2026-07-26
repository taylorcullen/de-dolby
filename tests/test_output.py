"""Filesystem tests for transactional output publication."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from de_dolby.output import OutputTransaction, staging_path_for


def test_staging_path_is_unique_sibling_with_media_suffix(tmp_path):
    destination = tmp_path / "movie.HDR10.mkv"
    first = staging_path_for(destination)
    second = staging_path_for(destination)
    assert first.parent == destination.parent
    assert first.suffix == ".mkv"
    assert first != second
    assert ".staging" in first.name


def test_publish_reveals_complete_staged_output(tmp_path):
    destination = tmp_path / "movie.mkv"
    transaction = OutputTransaction(destination)
    transaction.staging_path.write_bytes(b"complete media")

    assert not destination.exists()
    transaction.publish()

    assert destination.read_bytes() == b"complete media"
    assert not transaction.staging_path.exists()


def test_publish_without_force_preserves_existing_output(tmp_path):
    destination = tmp_path / "movie.mkv"
    destination.write_bytes(b"valid original")
    transaction = OutputTransaction(destination)
    transaction.staging_path.write_bytes(b"new media")

    with pytest.raises(RuntimeError, match="already exists"):
        transaction.publish()

    assert destination.read_bytes() == b"valid original"
    assert transaction.staging_path.read_bytes() == b"new media"


def test_force_replaces_only_after_staging_exists(tmp_path):
    destination = tmp_path / "movie.mkv"
    destination.write_bytes(b"valid original")
    transaction = OutputTransaction(destination, force=True)

    with pytest.raises(RuntimeError, match="Staged output does not exist"):
        transaction.publish()
    assert destination.read_bytes() == b"valid original"

    transaction.staging_path.write_bytes(b"complete replacement")
    transaction.publish()
    assert destination.read_bytes() == b"complete replacement"


def test_context_discards_staging_on_failure(tmp_path):
    destination = tmp_path / "movie.mkv"
    transaction = OutputTransaction(destination)

    with pytest.raises(ValueError):
        with transaction:
            transaction.staging_path.write_bytes(b"partial")
            raise ValueError("conversion failed")

    assert not destination.exists()
    assert not transaction.staging_path.exists()


def test_publish_reports_unsupported_atomic_operation(tmp_path):
    destination = tmp_path / "movie.mkv"
    transaction = OutputTransaction(destination, force=True)
    transaction.staging_path.write_bytes(b"complete")

    with patch(
        "de_dolby.output.os.replace",
        side_effect=OSError("operation not supported"),
    ):
        with pytest.raises(RuntimeError, match="atomically publish"):
            transaction.publish()

    assert not destination.exists()
    assert transaction.staging_path.exists()


def test_publish_flushes_staging_file_before_atomic_operation(tmp_path):
    destination = tmp_path / "movie.mkv"
    transaction = OutputTransaction(destination)
    transaction.staging_path.write_bytes(b"complete")

    with patch("de_dolby.output.os.fsync", wraps=os.fsync) as fsync:
        transaction.publish()

    fsync.assert_called_once()
