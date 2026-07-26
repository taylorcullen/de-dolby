# DD-003 — Transactional output writes

Status: Done  
Priority: 3  
Dependencies: DD-002

## Outcome

A failed or interrupted conversion never leaves a partial file at the requested
final output path and never destroys a valid existing output.

## Problem and evidence

External tools currently receive the final output path directly and `-y` is
used globally. Cleanup focuses on the temporary directory, while output
publication is not an explicit atomic step.

## Scope

- Write final media to a uniquely named sibling staging file.
- Validate success, flush/close subprocesses, then atomically publish it.
- Define `--force` behavior without exposing a partially replaced destination.
- Remove staging files on ordinary failure and interruption.
- Detect cross-filesystem or unsupported atomic replacement cases clearly.

Non-goals: crash-proof distributed transactions or automatic backups.

## Acceptance criteria

- [x] Final output becomes visible only after the conversion succeeds.
- [x] Without `--force`, an existing output remains byte-for-byte unchanged.
- [x] With `--force`, replacement occurs only after staging succeeds.
- [x] Failure and cancellation tests leave neither partial output nor staging
      residue.
- [x] Publishing behavior works on supported Windows and Linux CI.

## Ralph slices

- [x] Introduce and test output staging/publishing helpers.
- [x] Route lossless and re-encode remux steps through staging.
- [x] Harden force, cleanup, and interruption behavior.
- [x] Add cross-platform CI coverage and user documentation.

## Verification

Run the full harness and focused filesystem tests using temporary directories;
no real media tools are required.
