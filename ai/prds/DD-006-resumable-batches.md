# DD-006 — Resumable batch manifests

Status: Done  
Priority: 6  
Dependencies: DD-003, DD-005

## Outcome

Large multi-file conversions can resume safely after failure or interruption
without repeating completed work.

## Problem and evidence

Batch state currently exists only in one process. Errors are printed at the
end, but there is no durable record of plans, attempts, validation, output
identity, or retry eligibility.

## Scope

- Add `--manifest PATH`, `--resume`, and `--retry-failed`.
- Persist versioned JSON state using atomic writes after every state change.
- Record normalized input identity, plan fingerprint, status, attempts, timing,
  output, and validation summary.
- Skip completed items only when identity, plan, and validated output still
  match.

Non-goals: concurrent workers, remote queues, or database storage.

## Acceptance criteria

- [x] Interrupting between any two items leaves valid parseable state.
- [x] Resume never trusts a stale or mismatched output.
- [x] Changed options invalidate only affected entries.
- [x] Manifest writes contain no machine secrets or raw command environment.
- [x] Old schema versions fail with an actionable migration message.

## Ralph slices

- [x] Define manifest schema, fingerprints, and atomic persistence.
- [x] Record sequential batch transitions with deterministic tests.
- [x] Add resume and retry selection semantics.
- [x] Add CLI reporting and end-to-end mocked batch tests.

## Verification

Run full checks and interruption/resume simulations using mocked conversions.
