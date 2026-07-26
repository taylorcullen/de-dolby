# Decision log

## 2026-07-26 — Bind Ralph completion to recoverable state and evidence

Ralph lifecycle changes use atomic sibling writes plus a transaction journal
that is committed or rolled back on the next harness load. Completion requires
a passing configured-check receipt containing the SHA-256 of the exact
in-progress PRD, preventing stale verification from completing changed work.

## 2026-07-26 — Validate backlog and PRD state as one contract

Harness loading rejects mismatched PRD identity, status, priority,
dependencies, required sections, or checklists. Default Ralph selection only
considers ready items whose dependencies are done, so completed or blocked
work cannot be selected again.

## 2026-07-26 — Isolate and bound real-media integration checks

Real `ffmpeg`, `ffprobe`, and `mkvmerge` coverage runs in one dedicated Ubuntu
CI job with a five-minute timeout. The job records installed tool versions and
generates its media at test time; the default unit suite continues to exclude
the integration marker and requires no external binaries.

## 2026-07-26 — Fingerprint effective settings in conversion plans

Conversion plan schema version 3 includes all effective conversion settings.
Preset resolution is therefore inspectable and participates in resumable-batch
plan fingerprints.

## 2026-07-24 — Use a provider-neutral, repository-local harness

The project previously contained ignored Claude-specific state but no shared
agent contract. The harness is plain Markdown, JSON, and Python standard
library code so it works with different coding agents and on Python 3.10+.

Product and harness checks are separate scopes. This lets contributors develop
the harness even when product-only dependencies or external media tools are
unavailable, while the default `check` command still verifies both.

## 2026-07-24 — Make improvement work executable in bounded Ralph iterations

Potential improvements are stored as individual PRDs indexed by a validated
JSON backlog. The harness emits a prompt for exactly one small slice at a time.
This keeps repeated fresh-agent loops convergent and makes progress reviewable.

## 2026-07-24 — Keep environment probes separate from doctor presentation

Diagnostics return immutable typed results and accept injectable executable
resolution and process runners. This keeps CLI and JSON rendering independent
from subprocess details and lets unit tests cover partial installations without
requiring media tools.

## 2026-07-25 — Derive doctor readiness from conversion requirements

Doctor reports readiness independently for profiles 5, 7, 8, and 10. Profile 5
requires libplacebo and an HEVC encoder, profiles 7/8 use the lossless tool
route, and profile 10 requires an AV1 encoder without requiring dovi_tool.
Every route requires core mux/probe tools and a writable temp directory.

## 2026-07-26 — Represent conversion intent before execution

Conversion route selection produces an immutable plan from probed media facts
and advertised encoder availability. Planning owns profile/codec compatibility,
lossless-versus-re-encode policy, metadata source, stream policy, estimates,
and ordered logical steps without creating temporary or output files.

Execution consumes the selected route, encoder, output path, profile policy,
and space estimate from that plan. Dry-run stops after presenting the plan so
it cannot allocate an intermediate directory.

Plan JSON uses an explicit versioned serializer rather than generic dataclass
conversion so field names and string enum values change only through an
intentional schema revision.

Automatic encoder selection fails during planning when FFmpeg advertises no
compatible encoder. It does not claim an unavailable software encoder as a
fallback. Explicit encoders must be both codec-compatible and advertised by
the configured FFmpeg.

## 2026-07-26 — Make stream preservation a planned contract

Plans map every inventoried stream to replace, copy, preserve, or omit and
carry language, name, default/forced flags, and an omission reason. Full plans
preserve attachments, chapters, and tags; sample plans explicitly omit
container-level content while copying audio and subtitle tracks. Unsupported
or additional video streams produce plan warnings.

## 2026-07-26 — Make validation results stable data

Post-conversion checks produce immutable issues with enum-backed machine codes,
severity, expected/actual values, and explanatory messages. A report is valid
when it contains no error-severity issue; warnings remain visible without
blocking publication.

## 2026-07-26 — Persist resumable batch state atomically

Batch manifests contain normalized path/size/mtime input identities, SHA-256
fingerprints of stable plan documents, lifecycle fields, and validation
summaries. They exclude command environments and are flushed to a unique
sibling file before atomic replacement. Unsupported schema versions require
explicit migration or a new manifest.

## 2026-07-26 — Publish outputs transactionally from sibling staging files

Conversions stage media beside the requested destination so publication stays
on one filesystem. Non-forced publication uses an atomic hard-link
no-clobber operation; forced publication uses atomic replacement. Staged data
is flushed before publication, and unsupported atomic filesystem operations
fail clearly instead of falling back to a partial copy.
