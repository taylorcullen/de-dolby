# DD-010 — Ralph backlog lifecycle

Status: Done  
Priority: 10  
Dependencies: None

## Outcome

Repeated agent iterations can claim, execute, verify, and complete PRD slices
without ambiguous state or manual prompt assembly.

## Problem and evidence

The initial harness can list PRDs and emit a bounded prompt, but backlog status
and checkbox progress are edited manually. It does not yet validate that a PRD
is truly complete or prevent selecting work with unmet dependencies.

## Scope

- Add dependency-aware selection of the next executable PRD.
- Add `ralph start`, `ralph status`, and `ralph complete` lifecycle operations.
- Store reviewable state in existing backlog/PRD files using atomic updates.
- Validate required PRD sections, checkbox consistency, and completion gates.
- Provide `--json` output for orchestration.

Non-goals: invoking a particular LLM, infinite unattended execution, Git
commits, branches, or pull-request creation.

## Acceptance criteria

- [x] Default selection excludes blocked items and unmet dependencies.
- [x] Only one item may be `in_progress` unless explicitly overridden.
- [x] Completion requires all acceptance criteria and slices checked plus a
      passing configured verification record.
- [x] Mutating commands support `--dry-run` and use atomic writes.
- [x] Harness tests cover recovery from invalid and interrupted state updates.

## Ralph slices

- [x] Add PRD schema/Markdown validation and dependency-aware selection.
- [x] Implement read-only status and JSON output.
- [x] Implement atomic start/complete transitions with dry-run.
- [x] Add verification receipts, recovery tests, and operator documentation.

## Verification

`python -m harness check --scope harness`, including temporary-repository
lifecycle tests.
