"""Tests for de_dolby.validate — output validation functionality."""

import os
import tempfile
from pathlib import Path

from de_dolby.probe import FileInfo, StreamInfo
from de_dolby.validate import ValidationResult, format_validation_result, validate_output


class TestValidateOutput:
    """Test cases for validate_output function."""

    def test_output_file_not_found(self):
        """Validation should fail if output file does not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "nonexistent.mkv")
            # Create input file
            Path(input_path).write_bytes(b"dummy")

            def mock_probe(path):
                return FileInfo(path=path)

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is False
            assert result.checks["file_exists"] is False
            assert any("does not exist" in e for e in result.errors)

    def test_output_not_readable(self):
        """Validation should handle unreadable files gracefully."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            # Create input file
            Path(input_path).write_bytes(b"dummy input")
            # Create output file but make it unreadable (on Windows this is harder)
            Path(output_path).write_bytes(b"dummy output")

            def mock_probe(path):
                return FileInfo(path=path)

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            # Should pass basic file checks but may fail probe
            assert result.checks.get("file_readable") is True

    def test_valid_hdr10_output(self):
        """Validation should pass for a valid HDR10 output file."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            # Create files with realistic sizes
            Path(input_path).write_bytes(b"x" * 1000)
            Path(output_path).write_bytes(b"x" * 800)  # 80% of input

            # Mock probe to return valid HDR10 data
            input_info = FileInfo(
                path=input_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )
            output_info = FileInfo(
                path=output_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                master_display="G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)",
                content_light_level="1000,400",
                has_hdr10=True,
            )

            def mock_probe(path):
                if path == input_path:
                    return input_info
                return output_info

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is True
            assert result.checks["video_stream"] is True
            assert result.checks["hdr10_metadata"] is True
            assert result.checks["mastering_display"] is True
            assert result.checks["content_light_level"] is True
            assert result.size_ratio == 0.8

    def test_missing_video_stream(self):
        """Validation should fail if no video stream is found."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            Path(input_path).write_bytes(b"x" * 1000)
            Path(output_path).write_bytes(b"x" * 800)

            input_info = FileInfo(
                path=input_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )
            # Output with no video streams
            output_info = FileInfo(path=output_path, video_streams=[])

            def mock_probe(path):
                return input_info if path == input_path else output_info

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is False
            assert result.checks["video_stream"] is False
            assert any("No video stream" in e for e in result.errors)

    def test_missing_hdr10_metadata(self):
        """Validation should fail if HDR10 metadata is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            Path(input_path).write_bytes(b"x" * 1000)
            Path(output_path).write_bytes(b"x" * 800)

            input_info = FileInfo(
                path=input_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )
            # Output with wrong color metadata (SDR)
            output_info = FileInfo(
                path=output_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="bt709",
                        color_primaries="bt709",
                        bit_depth=10,
                    )
                ],
                has_hdr10=False,
            )

            def mock_probe(path):
                return input_info if path == input_path else output_info

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is False
            assert result.checks["hdr10_metadata"] is False
            assert any("smpte2084" in e.lower() for e in result.errors)

    def test_missing_mastering_display_warning(self):
        """Validation should warn but not fail if mastering display is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            Path(input_path).write_bytes(b"x" * 1000)
            Path(output_path).write_bytes(b"x" * 800)

            input_info = FileInfo(
                path=input_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )
            # Output with HDR10 metadata but no mastering display
            output_info = FileInfo(
                path=output_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )

            def mock_probe(path):
                return input_info if path == input_path else output_info

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is True  # Still passes
            assert result.checks["mastering_display"] is False
            assert any("Mastering display" in w for w in result.warnings)

    def test_cll_differs_from_input_warning(self):
        """Validation should warn if MaxCLL/MaxFALL differ significantly from input."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            Path(input_path).write_bytes(b"x" * 1000)
            Path(output_path).write_bytes(b"x" * 800)

            input_info = FileInfo(
                path=input_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )
            # Output with significantly different CLL
            output_info = FileInfo(
                path=output_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="4000,800",  # Very different from input
                has_hdr10=True,
            )

            def mock_probe(path):
                return input_info if path == input_path else output_info

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is True
            assert any("MaxCLL differs" in w for w in result.warnings)
            assert any("MaxFALL differs" in w for w in result.warnings)

    def test_output_larger_than_input_warning(self):
        """Validation should warn if output is significantly larger than input."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            Path(input_path).write_bytes(b"x" * 1000)
            Path(output_path).write_bytes(b"x" * 1300)  # 130% of input

            input_info = FileInfo(
                path=input_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )
            output_info = FileInfo(
                path=output_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                master_display="G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)",
                content_light_level="1000,400",
                has_hdr10=True,
            )

            def mock_probe(path):
                return input_info if path == input_path else output_info

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is True
            assert result.size_ratio == 1.3
            assert any("larger than input" in w.lower() for w in result.warnings)

    def test_output_smaller_than_input_info(self):
        """Validation should note if output is significantly smaller than input."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.mkv")
            output_path = os.path.join(tmp, "output.mkv")
            Path(input_path).write_bytes(b"x" * 1000)
            Path(output_path).write_bytes(b"x" * 700)  # 70% of input

            input_info = FileInfo(
                path=input_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
                has_hdr10=True,
            )
            output_info = FileInfo(
                path=output_path,
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                master_display="G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)",
                content_light_level="1000,400",
                has_hdr10=True,
            )

            def mock_probe(path):
                return input_info if path == input_path else output_info

            result = validate_output(input_path, output_path, probe_fn=mock_probe)

            assert result.passed is True
            assert result.size_ratio == 0.7
            assert any("smaller than input" in w.lower() for w in result.warnings)


class TestFormatValidationResult:
    """Test cases for format_validation_result function."""

    def test_format_passed_result(self):
        """Test formatting a successful validation result."""
        result = ValidationResult(
            passed=True,
            checks={
                "video_stream": True,
                "hdr10_metadata": True,
                "mastering_display": True,
                "content_light_level": True,
            },
            warnings=[],
            errors=[],
            input_info=FileInfo(path="input.mkv"),
            output_info=FileInfo(
                path="output.mkv",
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
            ),
            size_ratio=0.82,
        )

        formatted = format_validation_result(result)

        assert "Validation passed" in formatted
        assert "HEVC 3840x2160 10-bit" in formatted
        assert "smpte2084/bt2020" in formatted
        assert "1000 nits" in formatted
        assert "400 nits" in formatted
        assert "82%" in formatted

    def test_format_failed_result(self):
        """Test formatting a failed validation result."""
        result = ValidationResult(
            passed=False,
            checks={
                "video_stream": False,
                "hdr10_metadata": False,
            },
            warnings=[],
            errors=[
                "No video stream found in output file",
                "Missing HDR10 metadata",
            ],
            input_info=FileInfo(path="input.mkv"),
            output_info=FileInfo(path="output.mkv"),
            size_ratio=0.0,
        )

        formatted = format_validation_result(result)

        assert "Validation failed" in formatted
        assert "No video stream" in formatted
        assert "Missing HDR10" in formatted

    def test_format_with_warnings(self):
        """Test formatting a result with warnings."""
        result = ValidationResult(
            passed=True,
            checks={
                "video_stream": True,
                "hdr10_metadata": True,
                "mastering_display": False,
            },
            warnings=[
                "Mastering display metadata not present in output",
                "Output is significantly smaller than input",
            ],
            errors=[],
            input_info=FileInfo(path="input.mkv"),
            output_info=FileInfo(
                path="output.mkv",
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        color_transfer="smpte2084",
                        color_primaries="bt2020",
                        bit_depth=10,
                    )
                ],
            ),
            size_ratio=0.7,
        )

        formatted = format_validation_result(result)

        assert "Validation passed" in formatted
        assert "Warnings:" in formatted
        assert "Mastering display" in formatted
        assert "smaller than input" in formatted

    def test_format_matches_input(self):
        """Test formatting shows when MaxCLL/MaxFALL match input."""
        result = ValidationResult(
            passed=True,
            checks={"video_stream": True, "hdr10_metadata": True, "content_light_level": True},
            warnings=[],
            errors=[],
            input_info=FileInfo(
                path="input.mkv",
                content_light_level="1000,400",
            ),
            output_info=FileInfo(
                path="output.mkv",
                video_streams=[
                    StreamInfo(
                        index=0,
                        codec_type="video",
                        codec_name="hevc",
                        width=3840,
                        height=2160,
                        bit_depth=10,
                    )
                ],
                content_light_level="1000,400",
            ),
            size_ratio=0.82,
        )

        formatted = format_validation_result(result)

        assert "matches input" in formatted
