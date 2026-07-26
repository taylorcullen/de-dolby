# DD-009 — Hermetic media integration suite

Status: Done  
Priority: 9  
Dependencies: DD-004, DD-005

## Outcome

CI verifies real probing, stream mapping, remuxing, and validation using tiny
deterministic fixtures without committing copyrighted or large binary media.

## Problem and evidence

Current tests mock tool boundaries. They are fast but cannot catch incompatible
ffmpeg/mkvmerge argument combinations or stream-loss regressions.

## Scope

- Generate sub-second synthetic SDR/HDR-like containers at test time.
- Cover multiple audio/subtitle tracks, languages, flags, chapters, and
  attachments where tools support them.
- Separate required open-tool tests from optional `dovi_tool` fixtures.
- Cache tools where appropriate, never generated media.

Non-goals: visual quality benchmarking or redistribution of commercial samples.

## Acceptance criteria

- [x] Fixtures are deterministic, under a strict runtime/size budget, and
      generated from repository text/scripts.
- [x] Tests skip with explicit reasons when optional tooling is unavailable.
- [x] CI pins or records external tool versions.
- [x] At least one test catches a deliberately broken stream map.
- [x] Unit tests remain runnable without external binaries.

## Ralph slices

- [x] Add fixture generator and licensing/provenance documentation.
- [x] Add ffprobe and remux smoke tests behind an integration marker.
- [x] Add stream-fidelity and output-validator scenarios.
- [x] Add a bounded CI job and document local invocation.

## Verification

`python -m pytest -m integration` in the provisioned CI job and the normal
harness checks without integration dependencies.
