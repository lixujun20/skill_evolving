# Benchmark Architecture

`academic/benchmarks` is organized by responsibility:

```text
academic/benchmarks/
  core/                 shared benchmark registry, types, artifact store alias, generic runner
  bfcl/                 BFCL adapter, maintenance, related-task experiments, diagnostics
  spreadsheet/          SpreadsheetBench adapter
  tests/                benchmark tests grouped by subsystem
```

## Public Commands

List benchmark registry entries:

```bash
python -m academic.benchmarks.core.runner --list
```

Run a generic BFCL baseline:

```bash
python -m academic.benchmarks.core.runner \
  --benchmark bfcl_v3 \
  --mode baseline \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --bfcl-data-source bfcl_eval_bundle \
  --bfcl-adapter-mode official \
  --bfcl-prompt-style native \
  --bfcl-execution-backend official \
  --n-test 5
```

Run the BFCL Train50/Heldout50 related-task experiment:

```bash
python -m academic.benchmarks.bfcl.related.experiment --mode validate-config
python -m academic.benchmarks.bfcl.related.suites --suite claude_related_evolve_50_50 --dry-run
```

Run with a local Claude proxy config injected at runtime:

```bash
python -m academic.benchmarks.bfcl.related.proxy_runner --mode validate-config
```

## BFCL Package Map

- `bfcl/adapter.py`: BFCL task execution loop plus prompt/retrieval/tool-call glue; public compatibility exports remain available through `academic.benchmarks.bfcl`.
- `bfcl/constants.py`: dataset paths, BFCL class/tool-family metadata, and executor prompt constants.
- `bfcl/models.py`: `BFCLToolCall` and `BFCLTrace` dataclasses.
- `bfcl/call_utils.py`: pure call parsing, official-call formatting, JSON helpers, model-name sanitization, and local math helpers.
- `bfcl/loader.py`: task/tool loading, tool schema normalization, and tool-selection policies.
- `bfcl/executor.py`: `run_bfcl_task` and BFCL trace model façade.
- `bfcl/scoring.py`: call-F1 diagnostics and official checker integration.
- `bfcl/environments.py`: official BFCL backend wrapper and local mock environment.
- `bfcl/retrieval.py`: BFCL skill matching, tag/domain filtering, error-aware queries, and reranking.
- `bfcl/tool_clients.py`: provider-specific native tool-call clients and timeout policy.
- `bfcl/skills.py`: handwritten and trace-derived BFCL skill helpers.
- `bfcl/maintenance/adapter.py`: BFCL-specific extraction, bundle, test, refine, and overlap-refactor adapter.
- `bfcl/related/experiment.py`: Train50/Heldout50 orchestration and CLI.
- `bfcl/related/manifest.py`: curated Train50/Heldout50 manifest construction and validation.
- `bfcl/related/segment_index.py`: segment embedding/vector index storage and pgvector validation.
- `bfcl/related/{credit,pending_skills,checkpointing,analysis}.py`: compact helper surfaces for credit, pending-skill, checkpoint, and analysis artifacts used by the related-task experiment.
- `bfcl/diagnostics/`: read-only reporting and monitoring utilities.
- `bfcl/legacy/`: historical probes kept for provenance, not current experiment entrypoints.

## Other Benchmarks

- `spreadsheet/adapter.py`: SpreadsheetBench-Verified smoke adapter.
- `core/registry.py`: declares benchmark metadata for BFCL, Spreadsheet, AppWorld, OfficeQA, and TIR-Bench.
- `core/runner.py`: generic CLI for benchmark baselines/evolve scaffolds.

## Current Constraints

- The BFCL 50/50 paper path should use `academic.benchmarks.bfcl.related.experiment` or `academic.benchmarks.bfcl.related.suites`.
- Do not add new BFCL related-task logic to `core/runner.py`; keep paper-specific orchestration under `bfcl/related`.
- Per-task online LLM refactor is disabled in the current mainline; posterior overlap refactor is round-end maintenance.
- Historical result logs may mention old commands. Treat those as provenance only, not current run instructions.

## Validation

Focused benchmark checks:

```bash
pytest -q \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py \
  academic/benchmarks/tests/bfcl_related/test_experiment.py \
  academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py \
  academic/benchmarks/tests/maintenance/test_bundle_agent.py \
  academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py
```
