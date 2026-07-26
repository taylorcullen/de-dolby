# Architecture

## Product

- `de_dolby/cli.py` parses commands and translates CLI input into operations.
- `de_dolby/probe.py` inspects media and produces typed file information.
- `de_dolby/codecs.py` models codec and encoder capabilities.
- `de_dolby/diagnostics.py` contains typed, read-only environment probes used
  by preflight reporting.
- `de_dolby/fidelity.py` translates planned preservation policy into explicit
  remux input arguments.
- `de_dolby/config.py` owns encoder presets.
- `de_dolby/settings.py` loads versioned user configuration and resolves typed
  conversion settings using defaults, named presets, and CLI precedence.
- `de_dolby/metadata.py` derives HDR10 metadata.
- `de_dolby/manifest.py` owns versioned resumable-batch state, identities,
  fingerprints, and atomic persistence.
- `de_dolby/output.py` owns sibling staging files and atomic output
  publication.
- `de_dolby/plan.py` models immutable conversion plans and owns pure route and
  encoder-selection policy.
- `de_dolby/pipeline.py` plans and executes conversion pipelines.
- `de_dolby/tools.py` is the subprocess boundary for external media tools.
- `de_dolby/process.py` owns the common process-runner interface and typed
  failure, timeout, and cancellation taxonomy.
- `de_dolby/progress.py` and `de_dolby/display.py` own terminal presentation.
- `de_dolby/utils.py` contains small shared utilities.
- `de_dolby/validation.py` defines post-conversion invariants, stable issue
  codes, and versioned validation reports.

Keep policy and orchestration in the pipeline layer and operating-system
process details in the tools layer. Tests should mock at that boundary.
Stream inventory lives in `probe.py`, preservation policy and the planned map
live in `plan.py`, and only `fidelity.py` translates that contract into
container-tool arguments.

## Harness

- `AGENTS.md` is the concise, tool-neutral instruction entry point.
- `ai/*.md` holds durable product and engineering context.
- `ai/harness.json` declares context files and executable quality gates.
- `ai/backlog.json` indexes prioritized, dependency-aware improvement PRDs.
- `ai/prds/` contains Ralph-ready product requirement documents.
- `harness/` implements the dependency-free harness CLI.
- `harness_tests/` verifies the harness independently from product tests.

The harness intentionally does not call an LLM. The coding agent is replaceable;
the checked-in context, workflow, and validation contract are the harness.
