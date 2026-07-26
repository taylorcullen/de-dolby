# AI contributor guide

This repository contains two things that may be changed:

1. **The product** — the `de_dolby` Python CLI and its tests.
2. **The AI harness** — `AGENTS.md`, `ai/`, `harness/`, and
   `harness_tests/`.

Treat the harness as maintained source code, not as generated configuration.
When a task exposes a missing instruction, stale architectural fact, or weak
quality gate, improve the harness in the same change.

## Start here

Run this before making a non-trivial change:

```bash
python -m harness context
```

It prints the small set of repository documents that provide durable context.
Read the files relevant to the task and inspect the implementation before
editing it. Keep provider-specific local state out of the repository; the
checked-in harness is the shared project truth.

## Working agreement

- Keep product behavior in `de_dolby/` and product tests in `tests/`.
- Keep harness behavior in `harness/` and its tests in `harness_tests/`.
- Prefer small, reviewable changes and preserve unrelated user changes.
- Add or update tests for observable behavior.
- Never require real media files or installed video tools in unit tests; mock
  subprocess and probe boundaries.
- Do not silently change conversion quality, stream preservation, metadata, or
  overwrite behavior. These are user-visible and potentially destructive.
- Update `ai/ARCHITECTURE.md` when module boundaries or pipelines change.
- Add a short entry to `ai/DECISIONS.md` for decisions future agents would
  otherwise be tempted to reverse.
- Update the harness when its documented commands, paths, or checks become
  stale.

## Definition of done

Run:

```bash
python -m harness check
```

For backlog work, use `python -m harness backlog` and
`python -m harness ralph --prd DD-NNN`. A Ralph iteration completes one
smallest unchecked PRD slice only; it does not opportunistically start another.

The change is done when the relevant checks pass, documentation agrees with
the code, and `git diff` contains only intentional changes. If an external
binary prevents an integration check, report that explicitly; do not weaken
unit tests or claim the integration was verified.
