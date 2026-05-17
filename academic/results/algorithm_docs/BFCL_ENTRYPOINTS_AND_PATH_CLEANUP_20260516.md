# BFCL Entrypoints And Path Cleanup

Date: 2026-05-16

## Canonical Entrypoints

- `academic/benchmarks/bfcl/related/experiment.py`
  - Canonical BFCL related-task 50/50 experiment driver.
  - Use this for `validate-config`, `baseline`, `evolve`, and `analyze` modes.
  - This is the entrypoint aligned with `paper_new.md`.
  - If `--output`/`--checkpoint` are omitted, the CLI derives stable default
    paths before invoking the driver, so evolve runs still write mid-run
    checkpoints.

- `academic/benchmarks/bfcl/related/proxy_runner.py`
  - Thin compatibility wrapper around `bfcl_related/experiment.py`.
  - Only use when binding a run to a local Claude proxy port through `BFCL_PROXY_PORT`.
  - It should not contain experiment logic.

- `academic/benchmarks/bfcl/related/suites.py`
  - Preset launcher for named suites.
  - Acceptable as a convenience layer, but it should delegate to canonical runners.

## General Benchmark Entrypoint

- `academic/benchmarks/run.py`
  - Legacy/general benchmark CLI for BFCL and non-BFCL adapters.
  - Keep for backwards compatibility and non-related-task baselines.
  - Do not add new paper_new BFCL 50/50 logic here.

## Diagnostic / Reporting Scripts

These are not canonical experiment launchers:

- `academic/benchmarks/bfcl_parallel_progress_monitor.py`
- `academic/benchmarks/bfcl_evolve_diagnose.py`
- `academic/benchmarks/compare_bfcl_results.py`
- `academic/benchmarks/summarize_bfcl_token_breakdown.py`
- `academic/benchmarks/bfcl_debug_case_selection.py`
- `academic/benchmarks/generate_runtime_optimization_scenario_report.py`

They should remain read-only analysis/monitoring utilities.

## Deprecated / Historical Entrypoints

- `academic/benchmarks/bfcl/related/experiment.py`
  - Compatibility shim for old commands.
  - New code should import or execute `academic.benchmarks.bfcl.related.experiment`.

- `academic/benchmarks/bfcl_maintenance_lab.py`
- `academic/benchmarks/bfcl_real_maintenance_probe.py`

These are historical probe/lab scripts. Do not use for main results. They are
kept for result provenance because earlier experiment records reference them.
Delete only after archiving command lines and result lineage.

## Online Refactor Status

Per-task online refactor is deprecated for the paper_new mainline.

- Train-task execution still performs online extraction and segment-index updates.
- Refactor is now round-end/posterior maintenance only.
- Existing checkpoint/result fields such as `online_refactor_attempts` and
  `online_refactor_budget_remaining` are kept as empty/zero compact fields for
  backward compatibility.
- `BFCL_ONLINE_REFACTOR_MAX_PER_ROUND` is intentionally ignored by the current
  50/50 related-task driver.

## Cleanup Policy

Do not delete old executable scripts inside the same patch as algorithmic fixes.
The safe sequence is:

1. Mark canonical and deprecated entrypoints in documentation.
2. Move historical scripts to an archive path only after confirming no current
   experiment matrix, README, or result reproduction command imports them.
3. Keep thin wrappers if they only inject config and delegate to canonical code.
