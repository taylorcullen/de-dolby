"""CLI entry point for de-dolby."""

import argparse
import glob
import os
import re
import sys
import time
from pathlib import Path

from de_dolby import __version__
from de_dolby.display import display_info
from de_dolby.pipeline import ConvertOptions, convert, preview_frame
from de_dolby.probe import probe
from de_dolby.tools import check_amf_support, configure, configure_log_file, configure_timeout, require_tools


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
    p = Path(input_path)
    stem_with_ext = p.name

    # Try replacing .DV. (case-insensitive) with .HDR10.
    replaced = re.sub(r"\.DV\.", ".HDR10.", stem_with_ext, count=1, flags=re.IGNORECASE)
    if replaced != stem_with_ext:
        return str(p.with_name(replaced))

    # No .DV. found — insert .HDR10 before the extension
    return str(p.with_suffix("")) + ".HDR10" + p.suffix


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
    p_convert.add_argument("--encoder", choices=["auto", "hevc_amf", "libx265", "copy"],
                           default="auto", help="Video encoder (default: auto)")
    p_convert.add_argument("--quality", choices=["fast", "balanced", "quality"],
                           default="balanced", help="Encoder quality preset (default: balanced)")
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

    if args.command == "info":
        require_tools(need_mkvmerge=False)
        _cmd_info(args)
    elif args.command == "preview":
        require_tools(need_mkvmerge=False)
        _cmd_preview(args)
    elif args.command == "convert":
        require_tools(need_mkvmerge=True)
        _cmd_convert(args)


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
    if args.encoder == "hevc_amf" and not check_amf_support():
        print("Error: hevc_amf encoder not available. "
              "Use --encoder libx265 for CPU encoding.", file=sys.stderr)
        sys.exit(1)

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
    )

    errors: list[str] = []
    batch_start = time.monotonic()

    for idx, input_path in enumerate(input_files, 1):
        if multiple:
            elapsed = time.monotonic() - batch_start
            if idx > 1 and elapsed > 0:
                avg = elapsed / (idx - 1)
                remaining = avg * (len(input_files) - idx + 1)
                m, s = divmod(int(remaining), 60)
                h, m = divmod(m, 60)
                eta = f"  ETA: {h}:{m:02d}:{s:02d}" if h else f"  ETA: {m}:{s:02d}"
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

        try:
            convert(input_path, output_path, options)
        except RuntimeError as e:
            msg = f"{input_path}: {e}"
            print(f"\nError: {e}", file=sys.stderr)
            errors.append(msg)
            if not multiple:
                sys.exit(1)
        except KeyboardInterrupt:
            print("\n\nInterrupted.", file=sys.stderr)
            sys.exit(130)

    if multiple:
        elapsed = time.monotonic() - batch_start
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        succeeded = len(input_files) - len(errors)
        print(f"\nBatch complete: {succeeded}/{len(input_files)} succeeded in {time_str}",
              file=sys.stderr)

    if multiple and errors:
        print(f"{len(errors)} file(s) failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
