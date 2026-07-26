# DD-002 — Inspectable conversion plans

Status: Done  
Priority: 2  
Dependencies: DD-001

## Outcome

Users and automation can see exactly what a conversion will do before any
large temporary files or outputs are created.

## Problem and evidence

Pipeline choice, encoder fallback, metadata sources, stream mapping, and disk
estimates are decided inside `convert()`. Current `--dry-run` still requires
tools and probing, and its result is terminal presentation rather than a stable
plan that can be tested or consumed.

## Scope

- Introduce an immutable `ConversionPlan` separated from plan execution.
- Include selected pipeline, encoder and fallback reason, input/output paths,
  metadata source, stream policy, estimated space, and ordered steps.
- Add `de-dolby plan FILE` with human-readable and versioned `--json` output.
- Make `convert` execute the same plan rather than recomputing decisions.

Non-goals: editing plans by hand or guaranteeing exact output size.

## Acceptance criteria

- [x] Planning creates no output or intermediate media files.
- [x] All supported profile/codec combinations have plan tests.
- [x] Explicit encoder incompatibilities fail during planning.
- [x] JSON paths and enum values are stable and documented.
- [x] `--dry-run` delegates to planning or is deprecated with a clear migration.

## Ralph slices

- [x] Model the plan and extract pure pipeline-selection logic.
- [x] Make pipeline execution consume a plan without behavior drift.
- [x] Add the `plan` command and JSON serialization.
- [x] Cover fallback reasons, failure cases, and documentation.

## Verification

`python -m harness check` plus snapshot-style assertions for representative
Profile 5, 7/8, and 10 plans.
