"""CLI entry point for de-dolby."""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

from de_dolby import __version__
from de_dolby.batch import ConversionTask, run_batch_conversion
from de_dolby.codecs import ENCODERS
from de_dolby.display import display_info
from de_dolby.estimate import display_estimate, estimate_conversion
from de_dolby.options import ConvertOptions
from de_dolby.pipeline import convert, preview_frame
from de_dolby.probe import probe
from de_dolby.settings import Settings, find_config_file
from de_dolby.state import (
    clean_all_state_files,
    clean_old_state_files,
    load_state,
)
from de_dolby.tools import (
    check_encoder_available,
    configure,
    configure_log_file,
    configure_timeout,
    require_tools,
)
from de_dolby.tracks import TrackSelection, parse_lang_string
from de_dolby.utils import derive_output_name
from de_dolby.watch import create_watch_options_from_args, watch


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


def main() -> None:
    # Load config file early to use as argparse defaults
    settings = Settings.load()

    parser = argparse.ArgumentParser(
        prog="de-dolby",
        description="Convert Dolby Vision MKV files to HDR10",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config", metavar="PATH", help="Path to config file (default: auto-detect)"
    )
    sub = parser.add_subparsers(dest="command")

    # convert subcommand
    p_convert = sub.add_parser("convert", help="Convert a Dolby Vision MKV to HDR10")
    p_convert.add_argument(
        "input", nargs="+", metavar="FILE", help="Input MKV file(s) (Dolby Vision)"
    )
    p_convert.add_argument("-o", "--output", help="Output MKV file (single input only)")
    encoder_choices = ["auto"] + sorted(ENCODERS.keys())
    p_convert.add_argument(
        "--encoder",
        choices=encoder_choices,
        default=settings.encoder,
        help="Video encoder (default: auto)",
    )
    p_convert.add_argument(
        "--quality",
        choices=["fast", "balanced", "quality"],
        default=settings.quality,
        help="Encoder quality preset (default: balanced)",
    )
    p_convert.add_argument(
        "--crf", type=int, default=settings.crf, help="CRF value for libx265 (default: from preset)"
    )
    p_convert.add_argument(
        "--bitrate", default=settings.bitrate, help="Target bitrate for hevc_amf, e.g. 40M"
    )
    p_convert.add_argument(
        "--sample",
        type=int,
        nargs="?",
        const=30,
        metavar="SECONDS",
        help="Convert only the first N seconds for testing (default: 30)",
    )
    p_convert.add_argument(
        "--temp-dir",
        default=settings.temp_dir,
        help="Directory for intermediate files (default: system temp)",
    )
    p_convert.add_argument(
        "--timeout",
        type=int,
        metavar="MINUTES",
        help="Timeout per subprocess call in minutes (default: none)",
    )
    p_convert.add_argument(
        "--log-file", metavar="PATH", help="Write all commands and output to a log file"
    )
    p_convert.add_argument("--dry-run", action="store_true", help="Print steps without executing")
    p_convert.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=settings.verbose,
        help="Show detailed output",
    )
    p_convert.add_argument(
        "--force", action="store_true", default=settings.force, help="Overwrite output if exists"
    )
    p_convert.add_argument(
        "--no-validate",
        action="store_true",
        dest="no_validate",
        help="Skip output validation (faster for batch processing)",
    )
    p_convert.add_argument(
        "--workers",
        default="1",
        metavar="N",
        help="Parallel workers for batch conversion: N or 'auto' (default: 1)",
    )
    p_convert.add_argument(
        "--skip-errors", action="store_true", help="Continue processing other files if one fails"
    )
    p_convert.add_argument(
        "--ffmpeg", default=settings.tool_paths.ffmpeg, help="Path to ffmpeg binary"
    )
    p_convert.add_argument(
        "--dovi-tool", default=settings.tool_paths.dovi_tool, help="Path to dovi_tool binary"
    )
    p_convert.add_argument(
        "--mkvmerge", default=settings.tool_paths.mkvmerge, help="Path to mkvmerge binary"
    )

    # Resume functionality
    p_convert.add_argument(
        "--resume",
        action="store_true",
        help="Resume from interrupted conversion if state file exists",
    )

    # Track selection arguments
    p_convert.add_argument(
        "--audio-lang",
        default="all",
        metavar="LANGS",
        help="Audio languages to keep, comma-separated (e.g., 'eng,jpn') or 'all' (default: all)",
    )
    p_convert.add_argument(
        "--subtitle-lang",
        default="all",
        metavar="LANGS",
        help="Subtitle languages to keep, comma-separated (e.g., 'eng') or 'all' (default: all)",
    )
    p_convert.add_argument("--no-audio", action="store_true", help="Strip all audio tracks")
    p_convert.add_argument("--no-subtitles", action="store_true", help="Strip all subtitle tracks")
    p_convert.add_argument(
        "--keep-first-audio",
        action="store_true",
        default=True,
        help="Always keep first audio track regardless of language filter (default: True)",
    )
    p_convert.add_argument(
        "--no-keep-first-audio",
        action="store_false",
        dest="keep_first_audio",
        help="Disable keeping first audio track",
    )
    p_convert.add_argument(
        "--keep-first-subtitle",
        action="store_true",
        default=True,
        help="Always keep first subtitle track regardless of language filter (default: True)",
    )
    p_convert.add_argument(
        "--no-keep-first-subtitle",
        action="store_false",
        dest="keep_first_subtitle",
        help="Disable keeping first subtitle track",
    )

    # Config management subcommand
    p_config = sub.add_parser("config", help="Manage configuration file")
    p_config.add_argument("--init", action="store_true", help="Create an example config file")
    p_config.add_argument(
        "--show", action="store_true", help="Show the current config file path and contents"
    )

    # State management subcommand
    p_state = sub.add_parser("clean-state", help="Clean up orphaned state files")
    p_state.add_argument(
        "--all", action="store_true", help="Remove all state files (not just old ones)"
    )
    p_state.add_argument(
        "--temp-dir",
        default=settings.temp_dir,
        help="Directory where state files are stored (default: system temp)",
    )

    # preview subcommand
    p_preview = sub.add_parser("preview", help="Extract a single frame as PNG to check colors")
    p_preview.add_argument("input", help="Input MKV file (Dolby Vision)")
    p_preview.add_argument(
        "--time",
        default="00:01:00",
        help="Timestamp to extract, e.g. 08:05 or 00:08:05 (default: 00:01:00)",
    )
    p_preview.add_argument("-o", "--output", help="Output PNG file (default: preview.png)")
    p_preview.add_argument("--ffmpeg", help="Path to ffmpeg binary")

    # info subcommand
    p_info = sub.add_parser("info", help="Show file info (DV profile, streams, metadata)")
    p_info.add_argument("input", nargs="+", metavar="FILE", help="Input MKV file(s)")
    p_info.add_argument("--ffmpeg", help="Path to ffmpeg binary")
    p_info.add_argument("--json", action="store_true", help="Output in JSON format for scripting")
    p_info.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output (requires --json)"
    )

    # estimate subcommand
    p_estimate = sub.add_parser("estimate", help="Estimate conversion without executing")
    p_estimate.add_argument("input", help="Input MKV file (Dolby Vision)")
    encoder_choices_est = ["auto"] + sorted(ENCODERS.keys())
    p_estimate.add_argument(
        "--encoder",
        choices=encoder_choices_est,
        default="auto",
        help="Video encoder to estimate with (default: auto)",
    )
    p_estimate.add_argument(
        "--quality",
        choices=["fast", "balanced", "quality"],
        default="balanced",
        help="Encoder quality preset (default: balanced)",
    )
    p_estimate.add_argument("--ffmpeg", help="Path to ffmpeg binary")
    p_estimate.add_argument("--dovi-tool", help="Path to dovi_tool binary")

    # watch subcommand
    p_watch = sub.add_parser("watch", help="Watch directory and auto-convert Dolby Vision files")
    p_watch.add_argument("watch_path", metavar="PATH", help="Directory to watch for new MKV files")
    p_watch.add_argument(
        "--output-dir", metavar="DIR", help="Directory for converted files (default: same as input)"
    )
    p_watch.add_argument(
        "--recursive", action="store_true", help="Watch subdirectories too"
    )
    p_watch.add_argument(
        "--interval",
        type=int,
        default=5,
        metavar="N",
        help="Check interval in seconds (default: 5)",
    )
    p_watch.add_argument(
        "--delay",
        type=int,
        default=10,
        metavar="N",
        help="Wait N seconds after file appears before processing (default: 10)",
    )
    p_watch.add_argument(
        "--pattern",
        default="*.mkv",
        help="File pattern to watch (default: *.mkv)",
    )
    p_watch.add_argument(
        "--move-original",
        action="store_true",
        help="Move original to subdirectory after conversion",
    )
    p_watch.add_argument(
        "--reprocess",
        action="store_true",
        help="Reprocess files already in state",
    )
    # Conversion options for watch mode
    encoder_choices_watch = ["auto"] + sorted(ENCODERS.keys())
    p_watch.add_argument(
        "--encoder",
        choices=encoder_choices_watch,
        default=settings.encoder,
        help="Video encoder (default: auto)",
    )
    p_watch.add_argument(
        "--quality",
        choices=["fast", "balanced", "quality"],
        default=settings.quality,
        help="Encoder quality preset (default: balanced)",
    )
    p_watch.add_argument(
        "--crf", type=int, default=settings.crf, help="CRF value for libx265 (default: from preset)"
    )
    p_watch.add_argument(
        "--bitrate", default=settings.bitrate, help="Target bitrate for hevc_amf, e.g. 40M"
    )
    p_watch.add_argument(
        "--sample",
        type=int,
        nargs="?",
        const=30,
        metavar="SECONDS",
        help="Convert only the first N seconds for testing (default: 30)",
    )
    p_watch.add_argument(
        "--temp-dir",
        default=settings.temp_dir,
        help="Directory for intermediate files (default: system temp)",
    )
    p_watch.add_argument("--dry-run", action="store_true", help="Print steps without executing")
    p_watch.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=settings.verbose,
        help="Show detailed output",
    )
    p_watch.add_argument(
        "--force", action="store_true", default=settings.force, help="Overwrite output if exists"
    )
    p_watch.add_argument(
        "--no-validate",
        action="store_true",
        dest="no_validate",
        help="Skip output validation (faster for batch processing)",
    )
    p_watch.add_argument(
        "--resume",
        action="store_true",
        help="Resume from interrupted conversion if state file exists",
    )
    p_watch.add_argument(
        "--audio-lang",
        default="all",
        metavar="LANGS",
        help="Audio languages to keep, comma-separated (e.g., 'eng,jpn') or 'all' (default: all)",
    )
    p_watch.add_argument(
        "--subtitle-lang",
        default="all",
        metavar="LANGS",
        help="Subtitle languages to keep, comma-separated (e.g., 'eng') or 'all' (default: all)",
    )
    p_watch.add_argument("--no-audio", action="store_true", help="Strip all audio tracks")
    p_watch.add_argument("--no-subtitles", action="store_true", help="Strip all subtitle tracks")
    p_watch.add_argument(
        "--keep-first-audio",
        action="store_true",
        default=True,
        help="Always keep first audio track regardless of language filter (default: True)",
    )
    p_watch.add_argument(
        "--no-keep-first-audio",
        action="store_false",
        dest="keep_first_audio",
        help="Disable keeping first audio track",
    )
    p_watch.add_argument(
        "--keep-first-subtitle",
        action="store_true",
        default=True,
        help="Always keep first subtitle track regardless of language filter (default: True)",
    )
    p_watch.add_argument(
        "--no-keep-first-subtitle",
        action="store_false",
        dest="keep_first_subtitle",
        help="Disable keeping first subtitle track",
    )
    p_watch.add_argument(
        "--ffmpeg", default=settings.tool_paths.ffmpeg, help="Path to ffmpeg binary"
    )
    p_watch.add_argument(
        "--dovi-tool", default=settings.tool_paths.dovi_tool, help="Path to dovi_tool binary"
    )
    p_watch.add_argument(
        "--mkvmerge", default=settings.tool_paths.mkvmerge, help="Path to mkvmerge binary"
    )

    args = parser.parse_args()

    # Reload settings if --config was specified
    if getattr(args, "config", None):
        settings = Settings.load(Path(args.config))

    if not args.command:
        parser.print_help()
        sys.exit(2)

    # Handle config subcommand
    if args.command == "config":
        _cmd_config(args)
        return

    # Handle clean-state subcommand
    if args.command == "clean-state":
        _cmd_clean_state(args)
        return

    # Configure tool paths
    configure(
        ffmpeg=getattr(args, "ffmpeg", None),
        dovi_tool=getattr(args, "dovi_tool", None),
        mkvmerge=getattr(args, "mkvmerge", None),
    )

    if args.command == "info":
        require_tools(need_mkvmerge=False)
        _cmd_info(args)
    elif args.command == "preview":
        require_tools(need_mkvmerge=False)
        _cmd_preview(args)
    elif args.command == "estimate":
        require_tools(need_mkvmerge=False)
        _cmd_estimate(args)
    elif args.command == "convert":
        require_tools(need_mkvmerge=True)
        _cmd_convert(args)
    elif args.command == "watch":
        require_tools(need_mkvmerge=True)
        _cmd_watch(args, settings)


def _cmd_config(args: argparse.Namespace) -> None:
    """Handle config subcommand."""
    if args.init:
        settings = Settings()
        path = settings.write_example()
        print(f"Created example config file: {path}")
        print("Edit this file to customize your defaults.")
        return

    if args.show:
        config_path = find_config_file()
        if config_path:
            print(f"Config file: {config_path}")
            print("-" * 40)
            print(config_path.read_text(encoding="utf-8"))
        else:
            print("No config file found.")
            print("\nStandard locations:")
            for path in Settings._config_paths():
                print(f"  - {path}")
            print("\nRun 'de-dolby config --init' to create an example config.")
        return

    # Default: show help
    print("Usage: de-dolby config [--init | --show]")
    print("\nOptions:")
    print("  --init    Create an example config file")
    print("  --show    Show current config file path and contents")


def _cmd_clean_state(args: argparse.Namespace) -> None:
    """Handle clean-state subcommand."""
    temp_dir = getattr(args, "temp_dir", None)

    if args.all:
        # Remove all state files
        count = clean_all_state_files(temp_dir)
        if count == 0:
            print("No state files found.")
        else:
            print(f"Removed {count} state file(s).")
    else:
        # Remove only old state files
        deleted, kept = clean_old_state_files(temp_dir)
        if deleted == 0 and kept == 0:
            print("No state files found.")
        else:
            print(f"Removed {deleted} old state file(s), kept {kept} recent file(s).")


def _cmd_info(args: argparse.Namespace) -> None:
    input_files = _expand_globs(args.input)
    multiple = len(input_files) > 1
    use_json = getattr(args, "json", False)
    pretty = getattr(args, "pretty", False)

    results: list[dict] = []
    errors: list[str] = []

    for idx, input_path in enumerate(input_files, 1):
        if not Path(input_path).exists():
            error_msg = f"Error: file not found: {input_path}"
            if use_json:
                errors.append(error_msg)
            else:
                if multiple:
                    print(f"[{idx}/{len(input_files)}] {input_path}")
                    print("-" * 60)
                print(error_msg, file=sys.stderr)
                if not multiple:
                    sys.exit(1)
                print()
            continue

        info = probe(input_path)
        info_dict = info.to_dict()

        # Add estimated processing info for JSON output
        if use_json:
            try:
                from de_dolby.estimate import estimate_conversion

                estimate = estimate_conversion(
                    input_path, encoder_preference="auto", quality="balanced"
                )
                info_dict["estimated_processing"] = {
                    "pipeline": estimate.pipeline_type,
                    "encoder": estimate.encoder_name,
                    "estimated_time_minutes": int(
                        estimate.estimated_time_minutes[1]
                    ),  # Use max estimate
                    "estimated_output_size_bytes": estimate.estimated_output_size,
                }
            except Exception:
                # If estimation fails, skip this section
                pass

        if use_json:
            results.append(info_dict)
        else:
            if multiple:
                print(f"[{idx}/{len(input_files)}] {input_path}")
                print("-" * 60)
            display_info(info)

    if use_json:
        indent = 2 if pretty else None
        output = results if multiple else results[0] if results else {}
        print(json.dumps(output, indent=indent))


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


def _cmd_estimate(args: argparse.Namespace) -> None:
    input_path = args.input
    if not Path(input_path).exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        estimate = estimate_conversion(
            input_path,
            encoder_preference=args.encoder,
            quality=args.quality,
        )
        display_estimate(estimate)
    except RuntimeError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_convert(args: argparse.Namespace) -> None:
    input_files = _expand_globs(args.input)
    multiple = len(input_files) > 1

    # Validate -o usage with multiple files
    if args.output and multiple:
        print("Error: -o/--output cannot be used with multiple input files.", file=sys.stderr)
        sys.exit(2)

    # Validate numeric inputs
    if args.crf is not None and not (0 <= args.crf <= 51):
        print("Error: --crf must be between 0 and 51", file=sys.stderr)
        sys.exit(2)
    if args.sample is not None and args.sample <= 0:
        print("Error: --sample must be a positive number of seconds", file=sys.stderr)
        sys.exit(2)

    # Validate --temp-dir if provided
    if args.temp_dir:
        td = Path(args.temp_dir)
        if not td.is_dir():
            print(f"Error: --temp-dir does not exist: {args.temp_dir}", file=sys.stderr)
            sys.exit(1)
        if not os.access(args.temp_dir, os.W_OK):
            print(f"Error: --temp-dir is not writable: {args.temp_dir}", file=sys.stderr)
            sys.exit(1)

    if hasattr(args, "timeout") and args.timeout:
        configure_timeout(args.timeout)

    if hasattr(args, "log_file") and args.log_file:
        configure_log_file(args.log_file)

    # Fail fast: check encoder availability before processing any files
    if args.encoder not in ("auto", "copy") and not check_encoder_available(args.encoder):
        print(f"Error: {args.encoder} encoder not available in your ffmpeg build.", file=sys.stderr)
        print(
            "  Use --encoder auto to let de-dolby pick the best available encoder.", file=sys.stderr
        )
        sys.exit(1)

    track_selection = TrackSelection(
        audio_langs=parse_lang_string(args.audio_lang),
        subtitle_langs=parse_lang_string(args.subtitle_lang),
        no_audio=args.no_audio,
        no_subtitles=args.no_subtitles,
        keep_first_audio=args.keep_first_audio,
        keep_first_subtitle=args.keep_first_subtitle,
    )

    options = ConvertOptions(
        encoder=args.encoder,
        quality=args.quality,
        crf=args.crf,
        bitrate=args.bitrate,
        sample_seconds=args.sample,
        temp_dir=args.temp_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
        force=args.force,
        skip_validation=getattr(args, "no_validate", False),
        resume=args.resume,
        track_selection=track_selection,
    )

    # Single file mode: use traditional sequential processing
    if not multiple:
        input_path = input_files[0]
        if not Path(input_path).exists():
            print(f"Error: file not found: {input_path}", file=sys.stderr)
            sys.exit(1)

        output_path = args.output or derive_output_name(input_path)

        # Check for existing state file and warn if not using --resume
        if not args.resume and not args.dry_run:
            existing_state = load_state(input_path, args.temp_dir)
            if existing_state:
                print(
                    f"Warning: Found existing incomplete conversion state for {input_path}",
                    file=sys.stderr,
                )
                print(
                    "  Use --resume to continue from where it left off, or --force to start fresh.",
                    file=sys.stderr,
                )
                sys.exit(1)

        try:
            convert(input_path, output_path, options)
        except RuntimeError as e:
            print(f"\nError: {e}", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            print(
                "\n\nInterrupted. Use --resume to continue this conversion later.", file=sys.stderr
            )
            sys.exit(130)
        return

    # Multiple files: use batch processing with optional parallelism
    tasks: list[ConversionTask] = []
    errors: list[str] = []

    for idx, input_path in enumerate(input_files, 1):
        if not Path(input_path).exists():
            msg = f"file not found: {input_path}"
            print(f"Error: {msg}", file=sys.stderr)
            errors.append(msg)
            continue

        output_path = derive_output_name(input_path)
        task = ConversionTask(
            input_path=input_path,
            output_path=output_path,
            options=options,
            task_id=idx,
        )
        tasks.append(task)

    # Exit early if no valid tasks
    if not tasks:
        print("\nNo valid input files to process.", file=sys.stderr)
        if errors:
            print("Errors:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Run batch conversion
    results = run_batch_conversion(
        tasks=tasks,
        workers=args.workers,
        skip_errors=args.skip_errors,
        verbose=args.verbose,
    )

    # Exit with error if any conversions failed
    failed = [r for r in results if not r.success]
    if failed:
        sys.exit(1)


def _cmd_watch(args: argparse.Namespace, settings: Settings) -> None:
    """Handle watch subcommand."""
    from pathlib import Path

    # Validate watch path
    watch_path = Path(args.watch_path)
    if not watch_path.exists():
        print(f"Error: watch directory does not exist: {watch_path}", file=sys.stderr)
        sys.exit(1)
    if not watch_path.is_dir():
        print(f"Error: watch path is not a directory: {watch_path}", file=sys.stderr)
        sys.exit(1)

    # Validate numeric inputs
    if args.interval < 1:
        print("Error: --interval must be at least 1 second", file=sys.stderr)
        sys.exit(2)
    if args.delay < 0:
        print("Error: --delay cannot be negative", file=sys.stderr)
        sys.exit(2)
    if args.sample is not None and args.sample <= 0:
        print("Error: --sample must be a positive number of seconds", file=sys.stderr)
        sys.exit(2)

    # Validate --temp-dir if provided
    if args.temp_dir:
        td = Path(args.temp_dir)
        if not td.is_dir():
            print(f"Error: --temp-dir does not exist: {args.temp_dir}", file=sys.stderr)
            sys.exit(1)
        if not os.access(args.temp_dir, os.W_OK):
            print(f"Error: --temp-dir is not writable: {args.temp_dir}", file=sys.stderr)
            sys.exit(1)

    # Validate --output-dir if provided
    if args.output_dir:
        od = Path(args.output_dir)
        if not od.exists():
            try:
                od.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                print(f"Error: cannot create --output-dir: {e}", file=sys.stderr)
                sys.exit(1)

    # Configure tool paths
    configure(
        ffmpeg=getattr(args, "ffmpeg", None),
        dovi_tool=getattr(args, "dovi_tool", None),
        mkvmerge=getattr(args, "mkvmerge", None),
    )

    if hasattr(args, "timeout") and args.timeout:
        configure_timeout(args.timeout)

    if hasattr(args, "log_file") and args.log_file:
        configure_log_file(args.log_file)

    # Fail fast: check encoder availability
    if args.encoder not in ("auto", "copy") and not check_encoder_available(args.encoder):
        print(f"Error: {args.encoder} encoder not available in your ffmpeg build.", file=sys.stderr)
        print(
            "  Use --encoder auto to let de-dolby pick the best available encoder.", file=sys.stderr
        )
        sys.exit(1)

    # Set up track selection
    track_selection = TrackSelection(
        audio_langs=parse_lang_string(args.audio_lang),
        subtitle_langs=parse_lang_string(args.subtitle_lang),
        no_audio=args.no_audio,
        no_subtitles=args.no_subtitles,
        keep_first_audio=args.keep_first_audio,
        keep_first_subtitle=args.keep_first_subtitle,
    )

    # Create watch options from args
    watch_options = create_watch_options_from_args(args, settings, track_selection)

    # Start watching
    try:
        watch(
            watch_path=str(watch_path),
            output_dir=watch_options.output_dir,
            recursive=watch_options.recursive,
            interval=watch_options.interval,
            delay=watch_options.delay,
            pattern=watch_options.pattern,
            move_original=watch_options.move_original,
            reprocess=watch_options.reprocess,
            convert_options=watch_options.convert_options,
            tool_paths=watch_options.tool_paths,
        )
    except KeyboardInterrupt:
        sys.exit(130)
