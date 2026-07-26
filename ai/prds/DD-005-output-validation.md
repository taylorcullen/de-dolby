# DD-005 — Post-conversion validation

Status: Done  
Priority: 5  
Dependencies: DD-003, DD-004

## Outcome

A successful command means the resulting file is readable, HDR10-compatible,
free of Dolby Vision signalling, and faithful to the planned stream map.

## Problem and evidence

Success currently means subprocesses returned zero. There is no structured
comparison between input, plan, and output, so malformed metadata or lost
streams can go unnoticed.

## Scope

- Add a typed validation report and `de-dolby validate INPUT OUTPUT`.
- Check readability, codec, dimensions, duration tolerance, HDR colour fields,
  static metadata, absence of DV side data, and planned stream preservation.
- Run validation before transactional publication by default.
- Support human-readable and versioned `--json` output.

Non-goals: perceptual quality scoring or frame-by-frame losslessness proof.

## Acceptance criteria

- [x] Validation failures prevent publication and explain the failed invariant.
- [x] Duration tolerances distinguish sample and full conversions.
- [x] Warnings and errors are stable machine-readable codes.
- [x] The validator is unit-testable with ffprobe fixtures.
- [x] Users may opt out only with an explicitly named unsafe flag.

## Ralph slices

- [x] Model validation invariants, severities, and report schema.
- [x] Implement probe-to-plan comparison with fixture tests.
- [x] Add CLI command and transactional pipeline integration.
- [x] Document codes, tolerances, and unsafe override.

## Verification

Full harness checks plus positive and negative ffprobe fixture matrices.
