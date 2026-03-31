"""Tests for state management module."""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from de_dolby.metadata import HDR10Metadata
from de_dolby.options import ConvertOptions
from de_dolby.state import (
    PIPELINE_STEPS,
    STATE_VERSION,
    ConversionState,
    _compute_file_hash,
    _deserialize_options,
    _serialize_options,
    clean_all_state_files,
    clean_old_state_files,
    create_initial_state,
    delete_state,
    find_all_state_files,
    get_next_step,
    get_resume_summary,
    get_state_file_path,
    is_step_completed,
    load_state,
    save_state,
    update_state_metadata,
    update_state_progress,
    validate_state_for_resume,
)


class TestConversionState:
    """Test ConversionState dataclass."""

    def test_to_dict(self):
        state = ConversionState(
            version=1,
            input_path="/path/to/input.mkv",
            output_path="/path/to/output.mkv",
            input_hash="abc123",
            current_step="encode",
            completed_steps=["probe", "extract_hevc", "extract_rpu"],
            temp_paths={"raw_path": "/tmp/raw.hevc"},
            metadata={"max_cll": 1000},
            options={"encoder": "hevc_amf"},
        )
        data = state.to_dict()

        assert data["version"] == 1
        assert data["input_path"] == "/path/to/input.mkv"
        assert data["completed_steps"] == ["probe", "extract_hevc", "extract_rpu"]
        assert data["temp_paths"] == {"raw_path": "/tmp/raw.hevc"}

    def test_from_dict(self):
        data = {
            "version": 1,
            "input_path": "/path/to/input.mkv",
            "output_path": "/path/to/output.mkv",
            "input_hash": "abc123",
            "created_at": "2024-01-15T10:30:00",
            "last_updated": "2024-01-15T11:45:00",
            "current_step": "encode",
            "completed_steps": ["probe", "extract_hevc"],
            "temp_paths": {},
            "metadata": {},
            "options": {},
        }
        state = ConversionState.from_dict(data)

        assert state.version == 1
        assert state.input_path == "/path/to/input.mkv"
        assert state.completed_steps == ["probe", "extract_hevc"]

    def test_from_dict_defaults(self):
        state = ConversionState.from_dict({})

        assert state.version == STATE_VERSION
        assert state.input_path == ""
        assert state.completed_steps == []


class TestFileHash:
    """Test file hash computation."""

    def test_compute_file_hash_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mkv", delete=False) as f:
            f.write("test content")
            temp_path = f.name

        try:
            hash1 = _compute_file_hash(temp_path)
            hash2 = _compute_file_hash(temp_path)

            # Same file should produce same hash
            assert hash1 == hash2
            assert len(hash1) == 32
        finally:
            os.unlink(temp_path)

    def test_compute_file_hash_nonexistent_file(self):
        result = _compute_file_hash("/nonexistent/file.mkv")
        assert result == ""

    def test_compute_file_hash_different_files(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mkv", delete=False) as f1:
            f1.write("content A")
            path1 = f1.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mkv", delete=False) as f2:
            f2.write("content B")
            path2 = f2.name

        try:
            hash1 = _compute_file_hash(path1)
            hash2 = _compute_file_hash(path2)

            # Different files should produce different hashes
            assert hash1 != hash2
        finally:
            os.unlink(path1)
            os.unlink(path2)


class TestStateFilePath:
    """Test state file path generation."""

    def test_get_state_file_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mkv", delete=False) as f:
            f.write("test content")
            temp_path = f.name

        try:
            state_path = get_state_file_path(temp_path)

            assert state_path.name.startswith("de_dolby_state_")
            assert state_path.suffix == ".json"
        finally:
            os.unlink(temp_path)

    def test_get_state_file_path_with_custom_temp_dir(self):
        with tempfile.TemporaryDirectory() as custom_temp:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".mkv", delete=False) as f:
                f.write("test content")
                input_path = f.name

            try:
                state_path = get_state_file_path(input_path, custom_temp)

                assert str(state_path.parent) == custom_temp
            finally:
                os.unlink(input_path)


class TestStatePersistence:
    """Test state saving and loading."""

    def test_save_and_load_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".mkv", delete=False, dir=temp_dir
            ) as f:
                f.write("test content for state")
                input_path = f.name

            try:
                state = create_initial_state(
                    input_path,
                    "/output.mkv",
                    ConvertOptions(encoder="hevc_amf"),
                    {"raw_path": "/tmp/raw.hevc"},
                )
                save_state(state, temp_dir)

                loaded = load_state(input_path, temp_dir)

                assert loaded is not None
                assert loaded.input_path == input_path
                assert loaded.options["encoder"] == "hevc_amf"
                assert loaded.temp_paths["raw_path"] == "/tmp/raw.hevc"
            finally:
                os.unlink(input_path)

    def test_load_state_nonexistent(self):
        loaded = load_state("/nonexistent/file.mkv", "/tmp")
        assert loaded is None

    def test_load_state_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".mkv", delete=False, dir=temp_dir
            ) as f:
                f.write("original content")
                input_path = f.name

            try:
                state = create_initial_state(
                    input_path,
                    "/output.mkv",
                    ConvertOptions(),
                    {},
                )
                save_state(state, temp_dir)

                # Modify file
                with open(input_path, "w") as f:
                    f.write("modified content")

                # Should return None due to hash mismatch
                loaded = load_state(input_path, temp_dir)
                assert loaded is None
            finally:
                os.unlink(input_path)

    def test_delete_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".mkv", delete=False, dir=temp_dir
            ) as f:
                f.write("test content")
                input_path = f.name

            try:
                state = create_initial_state(input_path, "/output.mkv", ConvertOptions(), {})
                save_state(state, temp_dir)

                assert delete_state(input_path, temp_dir) is True
                assert delete_state(input_path, temp_dir) is False
            finally:
                os.unlink(input_path)


class TestStateUpdates:
    """Test state update functions."""

    def test_update_state_progress(self):
        state = ConversionState()
        update_state_progress(state, "extract_hevc", completed=True)

        assert state.current_step == "extract_hevc"
        assert "extract_hevc" in state.completed_steps
        assert state.last_updated != ""

    def test_update_state_progress_duplicate(self):
        state = ConversionState()
        update_state_progress(state, "extract_hevc", completed=True)
        update_state_progress(state, "extract_hevc", completed=True)

        # Should not add duplicate
        assert state.completed_steps.count("extract_hevc") == 1

    def test_update_state_metadata(self):
        state = ConversionState()
        meta = HDR10Metadata(
            master_display="G(1,2)B(3,4)",
            max_cll=1000,
            max_fall=400,
        )
        update_state_metadata(state, meta)

        assert state.metadata["master_display"] == "G(1,2)B(3,4)"
        assert state.metadata["max_cll"] == 1000
        assert state.metadata["max_fall"] == 400


class TestStepHelpers:
    """Test step-related helper functions."""

    def test_is_step_completed(self):
        state = ConversionState(completed_steps=["probe", "extract_hevc"])

        assert is_step_completed(state, "probe") is True
        assert is_step_completed(state, "extract_hevc") is True
        assert is_step_completed(state, "extract_rpu") is False
        assert is_step_completed(None, "probe") is False

    def test_get_next_step(self):
        state = ConversionState(completed_steps=["probe", "extract_hevc"])

        assert get_next_step(state) == "extract_rpu"
        assert get_next_step(None) == "probe"

    def test_get_next_step_all_completed(self):
        state = ConversionState(completed_steps=PIPELINE_STEPS[:])

        assert get_next_step(state) is None


class TestCleanup:
    """Test cleanup functions."""

    def test_find_all_state_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create some state files
            for i in range(3):
                path = Path(temp_dir) / f"de_dolby_state_{i}.json"
                path.write_text("{}")

            # Create non-state file
            path = Path(temp_dir) / "other_file.txt"
            path.write_text("content")

            files = find_all_state_files(temp_dir)

            assert len(files) == 3
            assert all(f.name.startswith("de_dolby_state_") for f in files)

    def test_clean_old_state_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create old state file
            old_path = Path(temp_dir) / "de_dolby_state_old.json"
            old_path.write_text("{}")
            old_time = datetime.now() - timedelta(days=10)
            os.utime(old_path, (old_time.timestamp(), old_time.timestamp()))

            # Create recent state file
            recent_path = Path(temp_dir) / "de_dolby_state_recent.json"
            recent_path.write_text("{}")

            deleted, kept = clean_old_state_files(temp_dir, max_age_days=7)

            assert deleted == 1
            assert kept == 1
            assert not old_path.exists()
            assert recent_path.exists()

    def test_clean_all_state_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create state files
            for i in range(3):
                path = Path(temp_dir) / f"de_dolby_state_{i}.json"
                path.write_text("{}")

            count = clean_all_state_files(temp_dir)

            assert count == 3
            assert len(find_all_state_files(temp_dir)) == 0


class TestSerialization:
    """Test options serialization."""

    def test_serialize_options(self):
        options = ConvertOptions(
            encoder="hevc_amf",
            quality="quality",
            crf=18,
            bitrate="40M",
            sample_seconds=60,
            verbose=True,
            resume=True,
        )
        data = _serialize_options(options)

        assert data["encoder"] == "hevc_amf"
        assert data["quality"] == "quality"
        assert data["crf"] == 18
        assert data["bitrate"] == "40M"
        assert data["sample_seconds"] == 60
        assert data["verbose"] is True
        assert data["resume"] is True

    def test_deserialize_options(self):
        data = {
            "encoder": "libx265",
            "quality": "fast",
            "crf": 20,
            "verbose": True,
        }
        options = _deserialize_options(data)

        assert options.encoder == "libx265"
        assert options.quality == "fast"
        assert options.crf == 20
        assert options.verbose is True


class TestResumeSummary:
    """Test resume summary and validation."""

    def test_get_resume_summary(self):
        state = ConversionState(
            input_path="/path/to/movie.mkv",
            completed_steps=["probe", "extract_hevc", "extract_rpu", "parse_meta"],
            current_step="strip_rpu",
            temp_paths={
                "raw_path": "/tmp/raw.hevc",
                "rpu_path": "/tmp/rpu.bin",
            },
        )
        summary = get_resume_summary(state)

        assert "movie.mkv" in summary
        assert "4/8" in summary or "50%" in summary
        assert "strip_rpu" in summary

    def test_validate_state_for_resume_valid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create input file
            input_path = Path(temp_dir) / "input.mkv"
            input_path.write_text("test content")

            # Create temp files
            raw_path = Path(temp_dir) / "raw.hevc"
            raw_path.write_text("raw content")

            state = ConversionState(
                input_path=str(input_path),
                temp_paths={"raw_path": str(raw_path)},
            )

            is_valid, error = validate_state_for_resume(state)

            assert is_valid is True
            assert error == ""

    def test_validate_state_for_resume_missing_input(self):
        state = ConversionState(
            input_path="/nonexistent/input.mkv",
            temp_paths={},
        )

        is_valid, error = validate_state_for_resume(state)

        assert is_valid is False
        assert "no longer exists" in error

    def test_validate_state_for_resume_missing_temp_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.mkv"
            input_path.write_text("test content")

            state = ConversionState(
                input_path=str(input_path),
                temp_paths={"raw_path": "/nonexistent/raw.hevc"},
            )

            is_valid, error = validate_state_for_resume(state)

            assert is_valid is False
            assert "missing" in error.lower()


class TestInitialState:
    """Test initial state creation."""

    def test_create_initial_state(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mkv", delete=False) as f:
            f.write("test content")
            input_path = f.name

        try:
            options = ConvertOptions(encoder="hevc_amf", quality="balanced")
            temp_paths = {"raw_path": "/tmp/raw.hevc"}

            state = create_initial_state(input_path, "/output.mkv", options, temp_paths)

            assert state.version == STATE_VERSION
            assert state.input_path == input_path
            assert state.output_path == "/output.mkv"
            assert state.input_hash != ""
            assert state.current_step == "probe"
            assert state.completed_steps == []
            assert state.temp_paths == temp_paths
            assert state.options["encoder"] == "hevc_amf"
            assert state.created_at != ""
            assert state.last_updated != ""
        finally:
            os.unlink(input_path)
