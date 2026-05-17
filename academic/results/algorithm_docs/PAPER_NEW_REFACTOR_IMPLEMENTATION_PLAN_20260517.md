# Paper New Refactor Implementation Plan

Date: 2026-05-17

## Implemented In This Correction

- Credit assignment is the central attribution role. `CREDIT_ASSIGNMENT_SYSTEM`
  now requires per-skill judgment, per-skill maintenance actions, evidence
  strength, attribution scope, and `bundle_case_suggestions`. The duplicate
  top-level `maintenance_actions` schema has been removed from the prompt.
- Credit-created bundle cases are applied by
  `_apply_credit_bundle_case_suggestions(...)`. Cases reuse official
  `question`, `expected`, `input_artifacts`, and `metadata`, but only for the
  focused replayable turn fragment specified by credit assignment. Empty focus
  no longer falls back to whole-task bundle cases.
- Micro and macro maintenance are split into `_run_micro_maintenance(...)` and
  `_run_macro_maintenance(...)`.
- Micro maintenance now performs credit pre-refine first, then runs the bundle
  gate and repair loop. The synthetic pre-refine result is labeled
  `credit_pre_refine`.
- Existing skill bundle maintenance uses `patch_skill_bundle_from_credit(...)`.
  New/empty skill bundles use `build_initial_skill_bundle_llm(...)`.
- Heterogeneous overlap graph nodes include `trace_segment`, `skill`, and
  `pending_skill`; pure skill-only cliques are filtered before LLM refactor.
- Refactor prompts no longer include coarse recalled skill candidates. They
  include clique graph evidence, selected segment evidence, involved skill-node
  summaries, and repair context only when a previous gate failed.
- Refactor few-shot coverage now explicitly includes knowledge/rule,
  workflow, and function/interface skill examples.
- Heldout test concurrency is supported while train/evolve remains serial.
  Concurrent heldout runs use per-task store copies.

## Verification

```bash
python -m py_compile academic/skill_repository/llm_maintenance.py academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/maintenance/adapter.py academic/skill_repository/refactor_overlap.py academic/benchmarks/core/runner.py academic/benchmarks/bfcl/adapter.py
pytest -q academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py academic/benchmarks/tests/maintenance/test_bundle_agent.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py
pytest -q academic/skill_repository/test_refactor_overlap.py academic/skill_repository/test_llm_maintenance_feedback.py
```

Results after the latest correction: `78 passed` for the combined BFCL
related/maintenance/adapter plus skill repository overlap/maintenance suite.

## Smoke Validation

Real LLM smoke attempted:

```bash
TE_LLM_TIMEOUT=30 python -m academic.benchmarks.bfcl.related.experiment \
  --mode evolve \
  --manifest academic/experiments/bfcl_case_lists/_tmp_related_manifest_smoke_1_1.json \
  --expected-train-size 1 \
  --expected-test-size 1 \
  --epochs 1 \
  --micro-maintenance-step 1 \
  --macro-maintenance-step 1 \
  --max-steps-per-turn 4 \
  --max-task-seconds 30
```

The real run reached the BFCL executor and extractor path but failed because
the configured Claude proxy timed out: executor request timed out, then
extractor maintenance LLM raised `TimeoutError` at `TE_LLM_TIMEOUT=30`.

Mock-role smoke with real BFCL manifest/tasks/tool schemas passed:

- train task: `multi_turn_base_120`
- heldout task: `multi_turn_base_68`
- tools loaded: `128`
- observed order: `credit_pre_refine` before bundle test
- credit-created case polarity: `negative`
- pending skill promoted by mocked posterior refactor
- maintenance windows: `1`
- refactor coverage rows: `1`

## Remaining Limits

- The recommended `curated_related_manifest_150_50.json` is supported by CLI
  generation but was not generated in this correction.
- Round-end pending revocation, credit filtering, and extractor TRL are still
  assembled at round end in the driver output, while macro reports expose the
  refactor/filter path. A later cleanup can move those side effects fully
  inside `_run_macro_maintenance(...)`.
- Current worktree layout has new directories such as
  `academic/benchmarks/bfcl/`, `academic/benchmarks/core/`, and
  `academic/benchmarks/tests/` as untracked (`??`) paths. Confirm whether this
  reorganized layout should be added to version control.
