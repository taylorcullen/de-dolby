"""CLI entry point for de-dolby."""

import argparse
import glob
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

from de_dolby import __version__
from de_dolby.display import display_info
from de_dolby.diagnostics import (
    configured_tool_paths,
    diagnostic_document,
    probe_ffmpeg_capabilities,
    probe_temp_directory,
    probe_tool_versions,
)
from de_dolby.pipeline import ConvertOptions, convert, plan_conversion, preview_frame
from de_dolby.manifest import (
    BatchManifest,
    ManifestStatus,
    ManifestStore,
    ResumeAction,
    fingerprint_plan,
    identify_input,
    load_manifest,
    select_resume_action,
)
from de_dolby.plan import plan_document
from de_dolby.probe import probe
from de_dolby.codecs import ENCODERS
from de_dolby.tools import check_encoder_available, configure, configure_log_file, configure_timeout, require_tools
from de_dolby.utils import format_bytes, format_duration
from de_dolby.validation import validate_staged_output, validation_document
from de_dolby.settings import (
    ConversionSettings,
    SettingsError,
    load_settings_config,
    merge_settings,
)
from de_dolby.process import ProcessCancelled, ProcessTimedOut


def _expand_globs(paths: list[str]) -> list[str]:
    """Expand glob patterns in file arguments.

    Windows shells (PowerShell, cmd) don't expand wildcards like *.mkv,
    so we handle it here. Already-expanded paths (no wildcards) pass through.
    """
    expanded: list[str] = []
    for p in paths:
        if any(c in p for c in ("*", "?", "[")):
            matches = sorted(glob.glob(p))
            if not matches:
                # Keep the literal so downstream code reports "file not found"
                expanded.append(p)
            else:
                expanded.extend(matches)
        else:
            expanded.append(p)
    return expanded


def derive_output_name(input_path: str) -> str:
    """Derive an HDR10 output filename from the input path.

    If the filename contains '.DV.' (case-insensitive), replace it with '.HDR10.'.
    Otherwise, insert '.HDR10' before the file extension.

    Examples:
        '2x03 - Secrets.DV.mkv'  -> '2x03 - Secrets.HDR10.mkv'
        '2x03 - Secrets.mkv'     -> '2x03 - Secrets.HDR10.mkv'
    """
    # Try replacing .DV. (case-insensitive) with .HDR10.
    replaced = re.sub(
        r"\.DV\.", ".HDR10.", input_path, count=1, flags=re.IGNORECASE
    )
    if replaced != input_path:
        return replaced

    # No .DV. found — insert .HDR10 before the extension
    suffix = Path(input_path).suffix
    return input_path[: -len(suffix)] + ".HDR10" + suffix if suffix else input_path + ".HDR10"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="de-dolby",
        description="Convert Dolby Vision MKV files to HDR10",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    # convert subcommand
    p_convert = sub.add_parser("convert", help="Convert a Dolby Vision MKV to HDR10")
    p_convert.add_argument("input", nargs="+", metavar="FILE",
                           help="Input MKV file(s) (Dolby Vision)")
    p_convert.add_argument("-o", "--output", help="Output MKV file (single input only)")
    encoder_choices = ["auto"] + sorted(ENCODERS.keys())
    p_convert.add_argument("--encoder", choices=encoder_choices,
                           default=None, help="Video encoder (default: auto)")
    p_convert.add_argument("--quality", choices=["fast", "balanced", "quality"],
                           default=None, help="Encoder quality preset (default: balanced)")
    p_convert.add_argument("--crf", type=int, help="CRF value for libx265 (default: from preset)")
    p_convert.add_argument("--bitrate", help="Target bitrate for hevc_amf, e.g. 40M")
    p_convert.add_argument("--sample", type=int, nargs="?", const=30, metavar="SECONDS",
                           help="Convert only the first N seconds for testing (default: 30)")
    p_convert.add_argument("--temp-dir", help="Directory for intermediate files (default: system temp)")
    p_convert.add_argument("--timeout", type=int, metavar="MINUTES",
                           help="Timeout per subprocess call in minutes (default: none)")
    p_convert.add_argument("--log-file", metavar="PATH", help="Write all commands and output to a log file")
    p_convert.add_argument("--dry-run", action="store_true", help="Print steps without executing")
    p_convert.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")
    p_convert.add_argument("--force", action="store_true", help="Overwrite output if exists")
    p_convert.add_argument("--manifest", metavar="PATH",
                           help="Persist resumable batch state as JSON")
    p_convert.add_argument("--resume", action="store_true",
                           help="Resume from an existing --manifest")
    p_convert.add_argument("--retry-failed", action="store_true",
                           help="Retry failed entries when resuming")
    p_convert.add_argument("--config", metavar="PATH",
                           help="Explicit TOML configuration path")
    p_convert.add_argument("--preset", metavar="NAME",
                           help="Named conversion preset from configuration")
    p_convert.add_argument(
        "--unsafe-skip-validation", action="store_true", default=None,
        help="UNSAFE: publish output without post-conversion validation",
    )
    p_convert.add_argument("--ffmpeg", help="Path to ffmpeg binary")
    p_convert.add_argument("--dovi-tool", help="Path to dovi_tool binary")
    p_convert.add_argument("--mkvmerge", help="Path to mkvmerge binary")

    # preview subcommand
    p_preview = sub.add_parser("preview", help="Extract a single frame as PNG to check colors")
    p_preview.add_argument("input", help="Input MKV file (Dolby Vision)")
    p_preview.add_argument("--time", default="00:01:00",
                           help="Timestamp to extract, e.g. 08:05 or 00:08:05 (default: 00:01:00)")
    p_preview.add_argument("-o", "--output", help="Output PNG file (default: preview.png)")
    p_preview.add_argument("--ffmpeg", help="Path to ffmpeg binary")

    # info subcommand
    p_info = sub.add_parser("info", help="Show file info (DV profile, streams, metadata)")
    p_info.add_argument("input", nargs="+", metavar="FILE", help="Input MKV file(s)")
    p_info.add_argument("--ffmpeg", help="Path to ffmpeg binary")

    # doctor subcommand
    p_doctor = sub.add_parser(
        "doctor", help="Check external tools and ffmpeg capabilities"
    )
    p_doctor.add_argument("--json", action="store_true",
                          help="Output the versioned diagnostic JSON schema")
    p_doctor.add_argument("--ffmpeg", help="Path to ffmpeg binary")
    p_doctor.add_argument("--dovi-tool", help="Path to dovi_tool binary")
    p_doctor.add_argument("--mkvmerge", help="Path to mkvmerge binary")
    p_doctor.add_argument("--temp-dir",
                          help="Directory used for conversion intermediates")
    p_doctor.add_argument(
        "--profile", type=int, choices=[5, 7, 8, 10],
        help="Require readiness for a specific Dolby Vision profile",
    )

    # plan subcommand
    p_plan = sub.add_parser(
        "plan", help="Inspect a conversion without creating media files"
    )
    p_plan.add_argument("input", metavar="FILE", help="Input Dolby Vision MKV")
    p_plan.add_argument("-o", "--output", help="Planned output MKV path")
    p_plan.add_argument("--encoder", choices=encoder_choices, default=None,
                        help="Planned video encoder (default: auto)")
    p_plan.add_argument("--quality", choices=["fast", "balanced", "quality"],
                        default=None, help="Planned quality setting")
    p_plan.add_argument("--crf", type=int, help="Planned libx265 CRF")
    p_plan.add_argument("--bitrate", help="Planned hardware encoder bitrate")
    p_plan.add_argument("--temp-dir", help="Planned intermediate directory")
    p_plan.add_argument("--config", metavar="PATH",
                        help="Explicit TOML configuration path")
    p_plan.add_argument("--preset", metavar="NAME",
                        help="Named conversion preset from configuration")
    p_plan.add_argument("--unsafe-skip-validation", action="store_true", default=None,
                        help="Plan without output validation")
    p_plan.add_argument("--sample", type=int, nargs="?", const=30,
                        metavar="SECONDS", help="Plan a sample conversion")
    p_plan.add_argument("--json", action="store_true",
                        help="Output the versioned plan JSON schema")
    p_plan.add_argument("--ffmpeg", help="Path to ffmpeg binary")

    # validate subcommand
    p_validate = sub.add_parser(
        "validate", help="Validate a converted output against its input"
    )
    p_validate.add_argument("input", help="Original Dolby Vision input")
    p_validate.add_argument("output", help="Converted HDR10 output")
    p_validate.add_argument("--sample", type=int, metavar="SECONDS",
                            help="Expected sample duration")
    p_validate.add_argument("--json", action="store_true",
                            help="Output versioned validation JSON")
    p_validate.add_argument("--ffmpeg", help="Path to ffmpeg binary")

    # config subcommand
    p_config = sub.add_parser("config", help="Inspect conversion configuration")
    config_sub = p_config.add_subparsers(dest="config_command")
    p_config_show = config_sub.add_parser("show", help="Show configuration")
    p_config_show.add_argument("--effective", action="store_true", required=True,
                               help="Resolve defaults and an optional preset")
    p_config_show.add_argument("--config", metavar="PATH",
                               help="Explicit TOML configuration path")
    p_config_show.add_argument("--preset", metavar="NAME",
                               help="Named preset to include")
    p_config_show.add_argument("--json", action="store_true",
                               help="Output machine-readable JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(2)

    # Configure tool paths
    configure(
        ffmpeg=getattr(args, "ffmpeg", None),
        dovi_tool=getattr(args, "dovi_tool", None),
        mkvmerge=getattr(args, "mkvmerge", None),
    )

    if args.command == "doctor":
        _cmd_doctor(args)
    elif args.command == "plan":
        _cmd_plan(args)
    elif args.command == "validate":
        _cmd_validate(args)
    elif args.command == "info":
        require_tools(need_mkvmerge=False)
        _cmd_info(args)
    elif args.command == "preview":
        require_tools(need_mkvmerge=False)
        _cmd_preview(args)
    elif args.command == "convert":
        require_tools(need_mkvmerge=True)
        _cmd_convert(args)
    elif args.command == "config":
        if args.config_command != "show":
            p_config.print_help()
            sys.exit(2)
        _cmd_config_show(args)


def _cmd_plan(args: argparse.Namespace) -> None:
    if not Path(args.input).exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    if args.sample is not None and args.sample <= 0:
        print("Error: --sample must be a positive number of seconds", file=sys.stderr)
        sys.exit(2)

    output_path = args.output or derive_output_name(args.input)
    try:
        effective = _effective_settings(args)
        options = ConvertOptions(
            encoder=effective.encoder,
            quality=effective.quality,
            crf=effective.crf,
            bitrate=effective.bitrate,
            sample_seconds=effective.sample_seconds,
            temp_dir=effective.temp_dir,
            unsafe_skip_validation=effective.unsafe_skip_validation,
            dry_run=True,
        )
        plan = plan_conversion(args.input, output_path, options)
    except (OSError, RuntimeError, SettingsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(plan_document(plan), indent=2, sort_keys=True))
        return

    print(f"Input: {plan.input_path}")
    print(f"Output: {plan.output_path}")
    print(f"Profile: {plan.profile} ({plan.input_codec})")
    print(f"Pipeline: {plan.pipeline.value}")
    print(f"Encoder: {plan.encoder}")
    if plan.fallback_reason:
        print(f"Selection: {plan.fallback_reason}")
    print(f"Metadata: {plan.metadata_source.value}")
    estimate = (
        format_bytes(plan.estimated_temp_bytes)
        if plan.estimated_temp_bytes is not None else "unknown"
    )
    print(f"Estimated temp space: {estimate}")
    print("Streams:")
    for name, action in plan_document(plan)["stream_policy"].items():
        print(f"  {name}: {action}")
    print("Stream map:")
    for stream in plan.stream_map:
        label = f"#{stream.source_index} {stream.codec_type}/{stream.codec_name}"
        print(f"  {label}: {stream.action.value}")
    for warning in plan.warnings:
        print(f"Warning: {warning}")
    print("Steps:")
    for index, step in enumerate(plan.steps, 1):
        print(f"  {index}. {step}")


def _cmd_validate(args: argparse.Namespace) -> None:
    for path in (args.input, args.output):
        if not Path(path).exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
    if args.sample is not None and args.sample <= 0:
        print("Error: --sample must be a positive number of seconds", file=sys.stderr)
        sys.exit(2)
    options = ConvertOptions(sample_seconds=args.sample, dry_run=True)
    try:
        plan = plan_conversion(args.input, args.output, options)
        input_info = probe(args.input)
        report = validate_staged_output(
            input_info, args.output, plan, sample_seconds=args.sample
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(validation_document(report), indent=2, sort_keys=True))
    else:
        print(f"Validation: {'valid' if report.valid else 'invalid'}")
        for issue in report.issues:
            print(f"[{issue.severity.value}] {issue.code.value}: {issue.message}")
    if not report.valid:
        sys.exit(1)


def _cmd_doctor(args: argparse.Namespace) -> None:
    paths = configured_tool_paths(
        ffmpeg=args.ffmpeg,
        dovi_tool=args.dovi_tool,
        mkvmerge=args.mkvmerge,
    )
    document = diagnostic_document(
        probe_tool_versions(paths),
        probe_ffmpeg_capabilities(paths.ffmpeg),
        probe_temp_directory(args.temp_dir),
        requested_profile=args.profile,
    )
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        print("de-dolby environment diagnostics")
        print()
        for name, tool in document["tools"].items():
            if not tool["available"]:
                print(
                    f"[missing] {name}: {tool['configured_path']} was not found; "
                    "install it or provide its custom path."
                )
            elif tool["error"]:
                print(
                    f"[error]   {name}: {tool['resolved_path']} — {tool['error']}"
                )
            else:
                print(
                    f"[ok]      {name}: {tool['resolved_path']} "
                    f"({tool['version']})"
                )
        ffmpeg = document["ffmpeg"]
        encoders = ", ".join(ffmpeg["encoders"]) or "none"
        print()
        print(f"ffmpeg encoders: {encoders}")
        print(f"libplacebo filter: {'available' if ffmpeg['libplacebo'] else 'missing'}")
        for error in ffmpeg["errors"]:
            print(f"[error]   {error}")
        temp = document["temp_directory"]
        free = (
            f"{temp['free_bytes']:,} bytes free"
            if temp["free_bytes"] is not None else "free space unknown"
        )
        status = "writable" if temp["writable"] else "not writable"
        print(f"temp directory: {temp['path']} ({status}, {free})")
        if temp["error"]:
            print(f"[error]   {temp['error']}")
        print()
        for profile, readiness in document["profiles"].items():
            label = "ready" if readiness["ready"] else "not ready"
            print(f"Profile {profile}: {label}")
            for reason in readiness["reasons"]:
                remedy = {
                    "ffmpeg libplacebo filter is unavailable":
                        "install an ffmpeg build with libplacebo support",
                    "no supported HEVC encoder is available":
                        "install an ffmpeg build with libx265 or a supported GPU encoder",
                    "no supported AV1 encoder is available":
                        "install an ffmpeg build with libsvtav1 or a supported GPU encoder",
                    "temp directory is not writable":
                        "choose a writable directory with --temp-dir",
                }.get(reason)
                print(f"  - {reason}" + (f"; {remedy}" if remedy else ""))
        print()
        print("Environment is usable." if document["usable"] else
              "Environment is not usable; resolve the items above.")
    if not document["usable"]:
        sys.exit(1)


def _cmd_info(args: argparse.Namespace) -> None:
    input_files = _expand_globs(args.input)
    multiple = len(input_files) > 1

    for idx, input_path in enumerate(input_files, 1):
        if multiple:
            print(f"[{idx}/{len(input_files)}] {input_path}")
            print("-" * 60)

        if not Path(input_path).exists():
            print(f"Error: file not found: {input_path}", file=sys.stderr)
            if not multiple:
                sys.exit(1)
            print()
            continue

        info = probe(input_path)
        display_info(info)


def _cmd_preview(args: argparse.Namespace) -> None:
    input_path = args.input
    if not Path(input_path).exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or "preview.png"
    try:
        preview_frame(input_path, args.time, output_path)
    except RuntimeError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_convert(args: argparse.Namespace) -> None:
    input_files = _expand_globs(args.input)
    multiple = len(input_files) > 1

    # Validate -o usage with multiple files
    if args.output and multiple:
        print("Error: -o/--output cannot be used with multiple input files.",
              file=sys.stderr)
        sys.exit(2)
    if (args.resume or args.retry_failed) and not args.manifest:
        print("Error: --resume and --retry-failed require --manifest PATH.",
              file=sys.stderr)
        sys.exit(2)
    if args.retry_failed and not args.resume:
        print("Error: --retry-failed requires --resume.", file=sys.stderr)
        sys.exit(2)

    # Validate numeric inputs
    if args.crf is not None and not (0 <= args.crf <= 51):
        print("Error: --crf must be between 0 and 51", file=sys.stderr)
        sys.exit(2)
    if args.sample is not None and args.sample <= 0:
        print("Error: --sample must be a positive number of seconds", file=sys.stderr)
        sys.exit(2)

    try:
        effective = _effective_settings(args)
    except SettingsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    # Validate the effective --temp-dir if configured
    if effective.temp_dir:
        td = Path(effective.temp_dir)
        if not td.is_dir():
            print(f"Error: --temp-dir does not exist: {effective.temp_dir}", file=sys.stderr)
            sys.exit(1)
        if not os.access(effective.temp_dir, os.W_OK):
            print(f"Error: --temp-dir is not writable: {effective.temp_dir}", file=sys.stderr)
            sys.exit(1)

    if effective.timeout_minutes:
        configure_timeout(effective.timeout_minutes)

    if hasattr(args, "log_file") and args.log_file:
        configure_log_file(args.log_file)

    # Fail fast: check encoder availability before processing any files
    if effective.encoder not in ("auto", "copy") and not check_encoder_available(effective.encoder):
        print(f"Error: {effective.encoder} encoder not available in your ffmpeg build.",
              file=sys.stderr)
        print("  Use --encoder auto to let de-dolby pick the best available encoder.",
              file=sys.stderr)
        sys.exit(1)

    options = ConvertOptions(
        encoder=effective.encoder,
        quality=effective.quality,
        crf=effective.crf,
        bitrate=effective.bitrate,
        sample_seconds=effective.sample_seconds,
        temp_dir=effective.temp_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
        force=args.force,
        unsafe_skip_validation=effective.unsafe_skip_validation,
    )

    errors: list[str] = []
    batch_start = time.monotonic()
    manifest_store = None
    if args.manifest:
        manifest_path = Path(args.manifest)
        try:
            manifest = (
                load_manifest(manifest_path)
                if args.resume and manifest_path.exists()
                else BatchManifest()
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        manifest_store = ManifestStore(manifest_path, manifest)

    for idx, input_path in enumerate(input_files, 1):
        if multiple:
            elapsed = time.monotonic() - batch_start
            if idx > 1 and elapsed > 0:
                avg = elapsed / (idx - 1)
                remaining = avg * (len(input_files) - idx + 1)
                eta = f"  ETA: {format_duration(remaining)}"
            else:
                eta = ""
            print(f"\n{'=' * 60}")
            print(f"[{idx}/{len(input_files)}] {Path(input_path).name}{eta}")
            print(f"{'=' * 60}")

        if not Path(input_path).exists():
            msg = f"file not found: {input_path}"
            print(f"Error: {msg}", file=sys.stderr)
            errors.append(msg)
            if not multiple:
                sys.exit(1)
            continue

        # Derive output path
        if args.output:
            output_path = args.output
        else:
            output_path = derive_output_name(input_path)

        manifest_key = None
        try:
            if manifest_store:
                identity = identify_input(input_path)
                plan_fingerprint = fingerprint_plan(
                    plan_conversion(input_path, output_path, options)
                )
                existing = manifest_store.manifest.entries.get(identity.path)
                output_identity = (
                    identify_input(output_path) if Path(output_path).exists() else None
                )
                decision = select_resume_action(
                    existing,
                    current_input=identity,
                    current_plan_fingerprint=plan_fingerprint,
                    current_output=output_identity,
                    retry_failed=args.retry_failed,
                )
                if args.resume and decision.action is ResumeAction.SKIP:
                    print(f"Skipping {input_path}: {decision.reason}")
                    continue
                if (
                    args.resume
                    and existing is not None
                    and existing.status is ManifestStatus.FAILED
                    and decision.reason == "failed_not_selected"
                ):
                    print(f"Skipping failed {input_path}; use --retry-failed to retry.")
                    continue
                reusable = (
                    existing is not None
                    and decision.reason in {"retry_failed", "interrupted_attempt", "pending"}
                )
                manifest_key = (
                    identity.path
                    if reusable
                    else manifest_store.register(
                        identity, str(Path(output_path).resolve()), plan_fingerprint
                    )
                )
                if reusable:
                    print(f"Resuming {input_path}: {decision.reason}")
                manifest_store.start(manifest_key)
            convert(input_path, output_path, options)
            if manifest_store and manifest_key:
                output_identity = (
                    identify_input(output_path) if Path(output_path).exists() else None
                )
                manifest_store.complete(
                    manifest_key,
                    validation_valid=(
                        output_identity is not None
                        and not options.unsafe_skip_validation
                        and not options.dry_run
                    ),
                    output_identity=output_identity,
                )
        except ProcessCancelled:
            if manifest_store and manifest_key:
                manifest_store.interrupt(manifest_key)
            print("\n\nInterrupted.", file=sys.stderr)
            sys.exit(130)
        except ProcessTimedOut as e:
            if manifest_store and manifest_key:
                manifest_store.fail(manifest_key, str(e))
            print(f"\nError: {e}", file=sys.stderr)
            sys.exit(124)
        except RuntimeError as e:
            if manifest_store and manifest_key:
                manifest_store.fail(manifest_key, str(e))
            msg = f"{input_path}: {e}"
            print(f"\nError: {e}", file=sys.stderr)
            errors.append(msg)
            if not multiple:
                sys.exit(1)
        except KeyboardInterrupt:
            if manifest_store and manifest_key:
                manifest_store.interrupt(manifest_key)
            print("\n\nInterrupted.", file=sys.stderr)
            sys.exit(130)

    if multiple:
        elapsed = time.monotonic() - batch_start
        time_str = format_duration(elapsed)
        succeeded = len(input_files) - len(errors)
        print(f"\nBatch complete: {succeeded}/{len(input_files)} succeeded in {time_str}",
              file=sys.stderr)

    if multiple and errors:
        print(f"{len(errors)} file(s) failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)


def _cmd_config_show(args: argparse.Namespace) -> None:
    try:
        effective = _effective_settings(args)
    except SettingsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    document = asdict(effective)
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    print("Effective conversion settings:")
    for name, value in document.items():
        rendered = "unset" if value is None else str(value).lower() if isinstance(value, bool) else value
        print(f"  {name}: {rendered}")


def _effective_settings(args: argparse.Namespace):
    config = load_settings_config(getattr(args, "config", None))
    layers = [("config.defaults", config.defaults)]
    preset_name = getattr(args, "preset", None)
    if preset_name:
        preset = config.presets.get(preset_name)
        if preset is None:
            location = config.path or "configuration"
            raise SettingsError(
                f"{location}.presets.{preset_name}: unknown preset"
            )
        layers.append((f"preset.{preset_name}", preset))
    layers.append((
        "CLI",
        ConversionSettings(
            encoder=getattr(args, "encoder", None),
            quality=getattr(args, "quality", None),
            crf=getattr(args, "crf", None),
            bitrate=getattr(args, "bitrate", None),
            sample_seconds=getattr(args, "sample", None),
            temp_dir=getattr(args, "temp_dir", None),
            timeout_minutes=getattr(args, "timeout", None),
            unsafe_skip_validation=getattr(args, "unsafe_skip_validation", None),
        ),
    ))
    return merge_settings(*layers)
