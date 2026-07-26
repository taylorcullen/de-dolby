# DD-007 — Reusable conversion presets

Status: Done  
Priority: 7  
Dependencies: DD-002

## Outcome

Users can name, inspect, and consistently reuse conversion settings without
copying long command lines.

## Problem and evidence

Encoder presets are hard-coded, while user choices such as encoder, quality,
CRF, bitrate, temp path, timeout, and validation policy are CLI-only. Batch
reproducibility depends on shell history.

## Scope

- Load a versioned TOML configuration from an explicit path and documented
  platform-default location.
- Support named presets and `--preset NAME`.
- Define precedence: built-in defaults, config defaults, named preset, CLI.
- Add `config show --effective` with secret-safe human and JSON output.

Non-goals: cloud synchronization, GUI editing, or storing credentials.

## Acceptance criteria

- [x] Missing config remains backward-compatible.
- [x] Unknown keys and invalid combinations fail with path-aware messages.
- [x] CLI values always win and effective settings appear in conversion plans.
- [x] Tests isolate filesystem and environment; user config is never read.
- [x] Examples cover CPU, AMD, NVIDIA, and sample workflows.

## Ralph slices

- [x] Define typed settings and merge/validation rules.
- [x] Add TOML loading using Python 3.10-compatible dependencies or fallback.
- [x] Integrate named presets with planning and CLI.
- [x] Add effective-config output and documentation.

## Verification

Full harness checks and table-driven precedence tests.
