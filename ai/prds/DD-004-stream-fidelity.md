# DD-004 — Stream fidelity contract

Status: Done  
Priority: 4  
Dependencies: DD-002

## Outcome

Conversions preserve every intended non-video element and report any element
that cannot be preserved.

## Problem and evidence

The probe models audio and subtitle streams, while remuxing relies on broad
`mkvmerge` defaults. Attachments, chapters, tags, track flags, names, language
metadata, fonts, and ordering are not represented by an explicit contract.

## Scope

- Inventory video, audio, subtitle, attachment, chapter, tag, and track metadata.
- Define default preservation and explicit exclusion rules.
- Include the stream mapping in conversion plans and verbose output.
- Ensure sample mode has a documented policy for each non-video element.

Non-goals: transcoding unsupported audio/subtitle codecs or metadata editing.

## Acceptance criteria

- [x] The preservation policy is documented and represented in typed models.
- [x] Remux commands are derived from that policy rather than implicit defaults.
- [x] Track language, name, default/forced flags, attachments, and chapters have
      command-level tests.
- [x] Unsupported elements fail or warn explicitly; none disappear silently.
- [x] Sample and full conversions have separately tested policies.

## Ralph slices

- [x] Expand probe models and fixtures for container-level content.
- [x] Define the preservation policy and planned stream map.
- [x] Generate explicit remux arguments with unit tests.
- [x] Document limitations and update architecture context.

## Verification

`python -m harness check`; later, DD-009 supplies synthetic-container
integration coverage.
