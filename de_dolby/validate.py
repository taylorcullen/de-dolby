"""Output validation for converted HDR10 files."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from de_dolby.probe import FileInfo, StreamInfo, probe
from de_dolby.utils import Colors, format_bytes


@dataclass
class ValidationResult:
    """Result of validating a converted output file."""

    passed: bool
    checks: dict[str, bool]
    warnings: list[str]
    errors: list[str]
    input_info: FileInfo
    output_info: FileInfo
    size_ratio: float


def validate_output(
    input_path: str,
    output_path: str,
    probe_fn: Callable[[str], FileInfo] = probe,
) -> ValidationResult:
    """Validate a converted output file against the input.

    Args:
        input_path: Path to the original input file
        output_path: Path to the converted output file
        probe_fn: Function to use for probing (default: de_dolby.probe.probe)

    Returns:
        ValidationResult with all check results, warnings, and errors
    """
    checks: dict[str, bool] = {}
    warnings: list[str] = []
    errors: list[str] = []

    # Check file exists and is readable
    output_file = Path(output_path)
    if not output_file.exists():
        checks["file_exists"] = False
        errors.append(f"Output file does not exist: {output_path}")
        return ValidationResult(
            passed=False,
            checks=checks,
            warnings=warnings,
            errors=errors,
            input_info=probe_fn(input_path),
            output_info=FileInfo(path=output_path),
            size_ratio=0.0,
        )

    checks["file_exists"] = True

    if not output_file.is_file():
        checks["file_is_file"] = False
        errors.append(f"Output path is not a file: {output_path}")
        return ValidationResult(
            passed=False,
            checks=checks,
            warnings=warnings,
            errors=errors,
            input_info=probe_fn(input_path),
            output_info=FileInfo(path=output_path),
            size_ratio=0.0,
        )

    try:
        with open(output_file, "rb") as f:
            f.read(1)
        checks["file_readable"] = True
    except OSError as e:
        checks["file_readable"] = False
        errors.append(f"Output file is not readable: {e}")
        return ValidationResult(
            passed=False,
            checks=checks,
            warnings=warnings,
            errors=errors,
            input_info=probe_fn(input_path),
            output_info=FileInfo(path=output_path),
            size_ratio=0.0,
        )

    # Probe both files
    try:
        input_info = probe_fn(input_path)
        output_info = probe_fn(output_path)
        checks["probe_success"] = True
    except Exception as e:
        checks["probe_success"] = False
        errors.append(f"Failed to probe files: {e}")
        return ValidationResult(
            passed=False,
            checks=checks,
            warnings=warnings,
            errors=errors,
            input_info=probe_fn(input_path) if "input_info" in dir() else FileInfo(path=input_path),
            output_info=FileInfo(path=output_path),
            size_ratio=0.0,
        )

    # Check video stream exists
    if output_info.video_streams:
        checks["video_stream"] = True
    else:
        checks["video_stream"] = False
        errors.append("No video stream found in output file")

    # Check HDR10 metadata
    if output_info.video_streams:
        vs = output_info.video_streams[0]
        has_smpte2084 = vs.color_transfer == "smpte2084"
        has_bt2020 = vs.color_primaries == "bt2020"

        if has_smpte2084 and has_bt2020:
            checks["hdr10_metadata"] = True
        else:
            checks["hdr10_metadata"] = False
            missing = []
            if not has_smpte2084:
                missing.append(
                    f"color_transfer is {vs.color_transfer or 'not set'} (expected smpte2084)"
                )
            if not has_bt2020:
                missing.append(
                    f"color_primaries is {vs.color_primaries or 'not set'} (expected bt2020)"
                )
            errors.append(f"Missing HDR10 metadata ({'; '.join(missing)})")

    # Check mastering display metadata
    if output_info.master_display:
        checks["mastering_display"] = True
    else:
        checks["mastering_display"] = False
        warnings.append("Mastering display metadata not present in output")

    # Check content light level (MaxCLL/MaxFALL)
    if output_info.content_light_level:
        checks["content_light_level"] = True
        parts = output_info.content_light_level.split(",")
        if len(parts) == 2:
            try:
                output_cll = int(parts[0])
                output_fall = int(parts[1])
                # Compare with input
                if input_info.content_light_level:
                    input_parts = input_info.content_light_level.split(",")
                    if len(input_parts) == 2:
                        input_cll = int(input_parts[0])
                        input_fall = int(input_parts[1])
                        # Warn if significantly different (>20% difference or >100 nits)
                        if input_cll > 0:
                            cll_diff = abs(output_cll - input_cll) / input_cll
                            if cll_diff > 0.2 or abs(output_cll - input_cll) > 100:
                                warnings.append(
                                    f"MaxCLL differs significantly from input: "
                                    f"output={output_cll} nits, input={input_cll} nits"
                                )
                        if input_fall > 0:
                            fall_diff = abs(output_fall - input_fall) / input_fall
                            if fall_diff > 0.2 or abs(output_fall - input_fall) > 50:
                                warnings.append(
                                    f"MaxFALL differs significantly from input: "
                                    f"output={output_fall} nits, input={input_fall} nits"
                                )
            except ValueError:
                pass
    else:
        checks["content_light_level"] = False
        warnings.append("Content light level (MaxCLL/MaxFALL) not present in output")

    # Calculate size ratio
    input_size = Path(input_path).stat().st_size
    output_size = output_file.stat().st_size
    size_ratio = output_size / input_size if input_size > 0 else 0.0

    # Warn about size differences
    if size_ratio > 1.2:
        warnings.append(
            f"Output is significantly larger than input ({size_ratio * 100:.0f}% - possible error)"
        )
    elif size_ratio < 0.8:
        warnings.append(
            f"Output is significantly smaller than input ({size_ratio * 100:.0f}% - possible quality loss)"
        )

    # Determine overall pass/fail
    passed = all(
        checks.get(key, False)
        for key in [
            "file_exists",
            "file_readable",
            "probe_success",
            "video_stream",
            "hdr10_metadata",
        ]
    )

    return ValidationResult(
        passed=passed,
        checks=checks,
        warnings=warnings,
        errors=errors,
        input_info=input_info,
        output_info=output_info,
        size_ratio=size_ratio,
    )


def format_validation_result(result: ValidationResult, verbose: bool = False) -> str:
    """Format a ValidationResult as a human-readable string.

    Args:
        result: The ValidationResult to format
        verbose: If True, include all checks even if passed

    Returns:
        Formatted string with validation summary
    """
    lines: list[str] = []

    # Header
    if result.passed:
        lines.append(f"{Colors.GREEN}✓ Validation passed{Colors.RESET}")
    else:
        lines.append(f"{Colors.RED}✗ Validation failed{Colors.RESET}")

    # Video stream info
    if result.output_info.video_streams:
        vs = result.output_info.video_streams[0]
        parts = [vs.codec_name.upper()]
        if vs.width and vs.height:
            parts.append(f"{vs.width}x{vs.height}")
        if vs.bit_depth:
            parts.append(f"{vs.bit_depth}-bit")
        video_str = " ".join(parts)
        status = "OK" if result.checks.get("video_stream") else "FAIL"
        color = Colors.GREEN if result.checks.get("video_stream") else Colors.RED
        lines.append(f"  {color}- Video stream: {status} ({video_str}){Colors.RESET}")
    else:
        lines.append(f"  {Colors.RED}- Video stream: FAIL (none found){Colors.RESET}")

    # HDR10 metadata
    if result.checks.get("hdr10_metadata"):
        vs2: StreamInfo | None = (
            result.output_info.video_streams[0] if result.output_info.video_streams else None
        )
        transfer = vs2.color_transfer if vs2 else "unknown"
        primaries = vs2.color_primaries if vs2 else "unknown"
        lines.append(f"  {Colors.GREEN}- HDR10 metadata: OK ({transfer}/{primaries}){Colors.RESET}")
    else:
        lines.append(f"  {Colors.RED}- HDR10 metadata: FAIL{Colors.RESET}")

    # Mastering display
    if result.checks.get("mastering_display"):
        lines.append(f"  {Colors.GREEN}- Mastering display: OK{Colors.RESET}")
    else:
        lines.append(f"  {Colors.YELLOW}- Mastering display: missing{Colors.RESET}")

    # MaxCLL
    if result.output_info.content_light_level:
        parts = result.output_info.content_light_level.split(",")
        if len(parts) == 2:
            max_cll = int(parts[0])
            # Compare with input
            match_str = ""
            if result.input_info.content_light_level:
                input_parts = result.input_info.content_light_level.split(",")
                if len(input_parts) == 2:
                    input_cll = int(input_parts[0])
                    if max_cll == input_cll:
                        match_str = " (matches input)"
                    else:
                        match_str = f" (input: {input_cll} nits)"
            lines.append(f"  {Colors.GREEN}- MaxCLL: {max_cll} nits{match_str}{Colors.RESET}")

    # MaxFALL
    if result.output_info.content_light_level:
        parts = result.output_info.content_light_level.split(",")
        if len(parts) == 2:
            max_fall = int(parts[1])
            # Compare with input
            match_str = ""
            if result.input_info.content_light_level:
                input_parts = result.input_info.content_light_level.split(",")
                if len(input_parts) == 2:
                    input_fall = int(input_parts[1])
                    if max_fall == input_fall:
                        match_str = " (matches input)"
                    else:
                        match_str = f" (input: {input_fall} nits)"
            lines.append(f"  {Colors.GREEN}- MaxFALL: {max_fall} nits{match_str}{Colors.RESET}")

    # Output size
    try:
        output_size = Path(result.output_info.path).stat().st_size
        size_str = format_bytes(output_size)
    except OSError:
        size_str = "unknown"
    ratio_str = f" ({result.size_ratio * 100:.0f}% of input)"
    lines.append(f"  {Colors.CYAN}- Output size: {size_str}{ratio_str}{Colors.RESET}")

    # Warnings
    if result.warnings:
        lines.append("")
        lines.append(f"{Colors.YELLOW}Warnings:{Colors.RESET}")
        for warning in result.warnings:
            lines.append(f"  {Colors.YELLOW}- {warning}{Colors.RESET}")

    # Errors
    if result.errors:
        lines.append("")
        lines.append(f"{Colors.RED}Errors:{Colors.RESET}")
        for error in result.errors:
            lines.append(f"  {Colors.RED}- {error}{Colors.RESET}")

    return "\n".join(lines)


def print_validation_result(result: ValidationResult, verbose: bool = False) -> None:
    """Print a validation result to stderr.

    Args:
        result: The ValidationResult to print
        verbose: If True, include all checks even if passed
    """
    import sys

    print("\n" + format_validation_result(result, verbose), file=sys.stderr)
