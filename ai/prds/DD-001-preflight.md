# DD-001 — Environment preflight diagnostics

Status: Done  
Priority: 1  
Dependencies: None

## Outcome

Users can determine whether their machine can run each conversion path before
starting a large job.

## Problem and evidence

The CLI only checks whether binaries exist. Encoder support, required ffmpeg
filters, tool versions, writable temp space, and per-profile capability are
discovered late, sometimes after expensive work has begun.

## Scope

- Add `de-dolby doctor` with human-readable and `--json` output.
- Report tool paths and versions, encoders, `libplacebo`, temp writability/free
  space, and support for profiles 5, 7/8, and 10.
- Exit 0 when at least one supported route is usable and nonzero when the
  requested route cannot run.
- Keep probes read-only and fast; do not require an input video.

Non-goals: downloading tools, changing PATH, or benchmarking encoders.

## Acceptance criteria

- [x] Missing binaries and capabilities have distinct actionable messages.
- [x] JSON output has a documented, versioned schema.
- [x] A custom `--ffmpeg`, `--dovi-tool`, `--mkvmerge`, and `--temp-dir` is
      reflected in diagnostics.
- [x] Unit tests mock every external process and cover partial installations.
- [x] README installation and troubleshooting sections reference `doctor`.

## Ralph slices

- [x] Introduce typed diagnostic results and isolated version/capability probes.
- [x] Add CLI rendering, exit semantics, and JSON schema tests.
- [x] Add temp-directory diagnostics and profile readiness aggregation.
- [x] Document the command and run `python -m harness check`.

## Verification

`python -m pytest tests -q` and manual inspection of `de-dolby doctor --json`
using mocked or locally available tools.
