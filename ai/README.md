# AI harness

This directory is the durable context layer for coding agents. It is deliberately
small: source code and tests remain the authority for implementation details.

## Commands

```bash
python -m harness context
python -m harness doctor
python -m harness check
python -m harness check --scope product
python -m harness check --scope harness
python -m harness backlog
python -m harness ralph
python -m harness ralph status --json
python -m harness ralph start DD-010
python -m harness ralph verify DD-010 --scope harness
python -m harness ralph complete DD-010
```

- `context` lists and optionally prints the documents an agent should read.
- `doctor` validates the harness configuration without running project tests.
- `check` runs the configured quality gates from `ai/harness.json`.
- `backlog` lists the prioritized PRDs and their dependencies.
- `ralph` emits a bounded prompt for one iteration of the selected PRD. With
  no ID, it selects the highest-priority item marked `ready` whose dependencies
  are all `done`. Explicit selection also enforces dependencies.
- `ralph status` reports dependency and execution state; `--json` emits schema
  version 1 for orchestration.
- `ralph start` atomically moves one executable item to `in_progress`. It
  refuses a second active item unless `--allow-concurrent` is supplied.
- `ralph verify` runs configured checks and atomically records a receipt tied
  to the exact PRD content.
- `ralph complete` requires checked acceptance criteria and slices plus a
  current passing receipt, then atomically marks the PRD and backlog `done`.
  Mutating commands support `--dry-run`.

## Ralph loop

Run bare `ralph` to choose work, then claim it with `ralph start ID`. Give the
prompt to a coding agent and repeat until every slice is checked. After the
final slice, run `ralph verify ID` and `ralph complete ID`. Verification
receipts under `ai/ralph/receipts/` are reviewable evidence, not a substitute
for CI.

Lifecycle updates use atomic sibling replacement and a short-lived transaction
journal. A later harness command automatically finishes or rolls back an
interrupted update. An invalid journal is preserved and reported for manual
inspection rather than discarded.

The configuration and commands are checked into the repository, so changes to
the harness can be reviewed, tested, and evolved alongside product features.

## Extending the harness

Add a gate to `ai/harness.json` when it is deterministic, works from the
repository root, and gives a useful failure message. Add corresponding coverage
in `harness_tests/`. Keep provider-specific prompts and private state outside
this directory.
