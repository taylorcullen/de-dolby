# DD-012 — Interactive conversion queue TUI

Status: Done  
Priority: 12  
Dependencies: DD-006, DD-008

## Outcome

Users can launch a full-screen terminal interface, browse local or network
storage, build and reorder a conversion queue, inspect plans, and monitor
sequential conversions without composing long shell commands.

## Problem and evidence

The current CLI accepts one or more paths and supports resumable manifests, but
queue construction is shell-driven and the user cannot interactively add,
remove, reorder, or inspect items. This is especially awkward for media stored
on Windows UNC shares or mounted SMB/NFS volumes, where quoting, discovery,
latency, and transient availability require clearer feedback.

## Scope

- Add a cross-platform `de-dolby tui` command using an explicitly selected,
  maintained terminal UI dependency provided through a documented install
  extra.
- Provide keyboard-accessible views for file browsing, queue management,
  per-item settings/plan review, active progress, logs, and completed/failed
  results.
- Add individual files, multiple selected files, or all supported media in a
  directory; prevent accidental duplicate queue entries.
- Allow queued items to be removed, reordered, edited, planned, started,
  cancelled, and retried while preserving the existing output-safety and
  validation policies.
- Execute one conversion at a time and persist queue lifecycle through the
  existing atomic batch-manifest concepts so the TUI can safely resume after a
  crash or restart.
- Treat Windows UNC paths such as `\\server\share\folder\movie.mkv`, mapped
  drives, and mounted SMB/NFS paths on POSIX as first-class filesystem paths.
- Preserve the user's path spelling for display while maintaining a stable
  canonical identity for duplicate detection and resume matching.
- Perform network directory listing, existence checks, probing, and planning
  off the UI thread with bounded waits, cancellation, and actionable
  unavailable/permission/timeout states.
- Adapt layout to practical terminal sizes and provide a discoverable help
  overlay listing every key binding.

Non-goals: mounting network shares, storing network credentials, browsing raw
`smb://` or `nfs://` URLs without an OS mount, concurrent encodes, remote worker
execution, a desktop GUI, or pausing/resuming inside one FFmpeg encode.

## Acceptance criteria

- [x] `de-dolby tui` opens a navigable interface with queue, browser, details,
      progress, logs, and help states and exits cleanly without starting work.
- [x] Keyboard-only users can add, remove, reorder, configure, start, cancel,
      and retry queue entries with visible focus and documented bindings.
- [x] Queue entries show input/output paths, plan summary, lifecycle state,
      progress/ETA when available, and actionable errors.
- [x] Queue state survives process restart and completed items are skipped only
      under the existing identity, plan, validation, and output checks.
- [x] Windows UNC paths, mapped drives, and mounted SMB/NFS paths retain their
      semantics through browsing, planning, manifest identity, conversion, and
      output publication.
- [x] An unreachable, slow, or permission-denied network location does not
      freeze the UI or corrupt/drop existing queue state.
- [x] TUI cancellation uses the existing process-tree termination and staging
      cleanup behavior and records the item as interrupted.
- [x] The normal CLI remains usable without installing the optional TUI
      dependency.
- [x] Automated tests use temporary paths and mocked filesystem/process
      boundaries; they require neither real media tools nor a network share.
- [x] Windows and Linux CI exercise startup, queue persistence, navigation, and
      platform-specific network-path semantics.

## Ralph slices

- [x] Select the TUI framework, define optional packaging/entry behavior, and
      introduce pure queue/view models with tests.
- [x] Add durable queue persistence and adapters to existing plan, manifest,
      conversion, cancellation, and progress boundaries.
- [x] Build keyboard-driven queue, details, settings, help, and log views with
      headless interaction tests.
- [x] Add asynchronous local/network file browsing with UNC and mounted-share
      path semantics, timeouts, cancellation, and failure-state tests.
- [x] Integrate sequential execution, live progress/ETA, cancel/retry/resume,
      responsive-layout behavior, CI coverage, and user documentation.

## Verification

`python -m pytest tests/test_tui_*.py -q`, headless TUI interaction tests on
Windows and Linux, network-path adapter tests using mocked filesystem
boundaries, and `python -m harness check`.
