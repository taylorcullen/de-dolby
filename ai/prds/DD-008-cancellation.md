# DD-008 — Cancellation and cleanup hardening

Status: Done  
Priority: 8  
Dependencies: DD-003

## Outcome

Ctrl+C and timeouts terminate the complete media-process tree promptly, clean
temporary state, and return predictable exit codes.

## Problem and evidence

Some commands use `subprocess.run`, encoding progress uses `Popen`, cleanup
catches broad exceptions, and the CLI only handles `KeyboardInterrupt` around
`convert`. Child-process termination semantics are not unified or tested.

## Scope

- Centralize process lifecycle, timeout, cancellation, and error translation.
- Terminate process groups/trees on Windows and POSIX, then escalate after a
  bounded grace period.
- Preserve useful stderr and command context in logs.
- Make cleanup failures visible without hiding the original failure.

Non-goals: pausing encoders or resuming within a single video.

## Acceptance criteria

- [x] Ctrl+C returns 130 and leaves no running child media process.
- [x] Timeout has a distinct exit code/message and kills descendants.
- [x] Cleanup occurs once and cannot mask the primary error.
- [x] Process-tree tests use harmless helper processes, never media binaries.
- [x] Batch manifests record interrupted rather than failed where applicable.

## Ralph slices

- [x] Introduce a common process runner interface and error taxonomy.
- [x] Implement/test POSIX process-group termination.
- [x] Implement/test Windows process-tree termination.
- [x] Integrate progress execution, cleanup reporting, and CLI exit codes.

## Verification

Full harness checks plus bounded helper-process integration tests on Windows
and Linux CI.
