# Paper New Code Review 3 Response

Date: 2026-05-17

Scope: user review covering `run_bfcl_task`, `_run_related_evolve_experiment`, bundle maintenance, micro/macro maintenance, and overlap refactor graph fidelity.

## Summary

This document now separates the current implemented state from follow-up work.
The latest correction pass fixed one real executor bug and several algorithm
fidelity gaps:

- Step-level retrieval now actually injects newly retrieved prompt skills into the current conversation as a lightweight context message.
- Heldout test rollout can run concurrently through a new `test_concurrency` parameter while train/evolve remains serial.
- Credit assignment now emits maintenance actions and positive/negative/integration bundle-case suggestions. Replayable cases are created from official task fragments instead of sending full traces to the bundle maintainer.
- Micro and macro maintenance now have separate functions: `_run_micro_maintenance(...)` and `_run_macro_maintenance(...)`. The old flag-based helper remains only as a legacy internal compatibility hook for older tests.
- Existing active and pending skills are now represented as real overlap-graph nodes, not just prompt-side candidate summaries.
- Macro default is now `10`; manifest builder can generate 150/50 non-overlap splits via `--expected-train-size 150 --expected-test-size 50`.

Visibility note:

- Most touched files live under newly reorganized directories such as `academic/benchmarks/bfcl/` and `academic/benchmarks/core/`.
- In the current worktree these directories show as `??` untracked in `git status`, while old flat files show as deleted. Therefore a plain `git diff` may not show these implementation changes unless untracked files are included.

## Responses

### `run_bfcl_task`: step retrieval was not really injected

Your concern was correct. The previous code refreshed the `system` variable after step-start retrieval, but the active `messages` list did not receive a new skill-context message. Depending on provider behavior, this could mean the newly retrieved skill was only visible through a changed system argument and not reliably available in the conversation context.

Change:

- Added `_step_skill_context_message()` in `academic/benchmarks/bfcl/adapter.py`.
- When step-start retrieval adds prompt skills, the executor now appends a user-context message containing only the newly added skill prompt.
- The debug event `prompt_context_update` now records the injected `step_context_message`.

Test:

- Updated `test_run_bfcl_task_error_feedback_uses_step_start_context_update` to assert the second model request contains the runtime skill retrieval update.

### `_run_related_evolve_experiment`: whether `update_overlap_graph_state` includes pending state

Previously pending skills participated in coarse skill recall for refactor prompts, but they were not first-class graph nodes. That meant the implementation was not a true heterogeneous graph over trace segments plus existing/pending skills.

Change:

- Added `skill_to_overlap_segment()` in `academic/skill_repository/refactor_overlap.py`.
- Added `_skill_overlap_segments()` in `academic/benchmarks/bfcl/maintenance/adapter.py`.
- Newly extracted pending skills are immediately inserted into `OverlapGraphState` after `store.add_pending(...)`.
- `run_bfcl_overlap_refactor_llm()` now appends active and pending skill nodes into the same overlap graph.
- Disabled/rejected/archived skills are excluded.
- Clique attempts are filtered so a refactor must include at least one current-window real trace segment. This prevents pure skill-skill similarity from triggering duplicate LLM refactors.

Test:

- Added `test_paper_new_pending_skill_is_real_overlap_graph_node`.
- Added `test_overlap_refactor_report_contains_pending_skill_graph_node`.
- Existing pending recall test still passes.

### `_run_related_evolve_experiment`: heldout test parallelism

Train/evolve remains serial because every train task changes skill state and segment state. Heldout test does not update the skill store and is safe to parallelize.

Change:

- Added optional `concurrency` to `_run_bfcl_baseline()` in `academic/benchmarks/core/runner.py`.
- Added `--test-concurrency` / `BFCL_RELATED_TEST_CONCURRENCY` to related experiment CLI.
- Evolve heldout test calls `_run_bfcl_baseline(... concurrency=test_concurrency)`.
- Default remains `1` to preserve previous behavior unless explicitly enabled.

Test:

- Added `test_bfcl_baseline_concurrency_runs_tasks_in_parallel`, which proves `concurrency=2` executes two BFCL tasks concurrently while preserving output order.

### `maintain_skill_bundle_llm`: full trace prompt too long and wrong responsibility split

Agreed with the direction. For existing skills, the bundle maintainer should not re-read all source/replay traces. Credit assignment is the right role to identify harmful cases; bundle maintenance should receive compact attributed cases.

Change:

- Added `_append_credit_negative_bundle_cases()` in `academic/benchmarks/bfcl/related/experiment.py`.
- High-confidence harmful credit creates a `negative_cases` row on the relevant skill bundle.
- The case stores a compact official `task_fragment` from the original task snapshot and the credit event rationale.
- Existing-bundle maintenance now passes `source_results=[]`, `replay_results=[]`, plus `credit_cases=[...]` and integration failures only.
- `maintain_skill_bundle_llm()` now accepts `credit_cases` and its prompt states that full traces are intentionally not part of the normal existing-skill path.

Test:

- Added `test_credit_assignment_harmful_event_appends_negative_bundle_case`.

Remaining plan:

- Move more positive-case construction to credit assignment if later experiments show positive examples are needed for stability.
- Keep full trace access only for brand-new skill initial bundle distillation or exceptional rebuilds.

### `_run_round_refine_and_refactor`: small round should not do the same work as big round

The previous single function made micro and macro too similar. I kept the function as a shared implementation utility but added explicit behavior flags so the call sites now express different roles.

Change:

- `_run_round_refine_and_refactor()` now accepts:
  - `run_bundle_builder`
  - `run_bundle_tests`
  - `run_refine`
  - `run_overlap_refactor`
- Micro call:
  - `run_bundle_builder=False`
  - `run_bundle_tests=True`
  - `run_refine=True`
  - `run_overlap_refactor=False`
- Macro call:
  - `run_bundle_builder=True`
  - `run_bundle_tests=True`
  - `run_refine=False`
  - `run_overlap_refactor=True`

Rationale:

- Micro uses the just-finished execution plus credit-created negative cases to refine targeted skills.
- Macro handles window-level refactor, pending promotion/revocation, credit filtering, and TRL.

Remaining plan:

- If micro test cost is still high, add an option to test only skills whose bundle changed in the current task.

### `macro=10`, train set 150 non-repeating

Change:

- Default `--macro-maintenance-step` is now `10`.
- `build_curated_related_task_manifest()` already accepted `n_train`/`n_test`; CLI now passes `--expected-train-size` and `--expected-test-size` into manifest generation.
- To build the longer split:

```bash
python -m academic.benchmarks.bfcl.related.experiment \
  --mode build-manifest \
  --manifest academic/experiments/bfcl_case_lists/curated_related_manifest_150_50.json \
  --expected-train-size 150 \
  --expected-test-size 50
```

Note:

- Existing 50/50 manifests are not overwritten.
- Train/test non-overlap is still enforced by manifest validation.

### `run_bfcl_overlap_refactor_llm`: heterogeneous graph and segment relevance

Previously the prompt had candidate skills, but graph overlap was still trace-segment only. That was a fidelity gap.

Change:

- Active/pending skills are converted into graph nodes with `kind="skill"` or `kind="pending_skill"`.
- The same sparse/embedding/error graph machinery scores trace-skill and skill-skill edges.
- Refactor only attempts cliques that touch current-window trace segments.
- Existing coarse recalled skills remain in the prompt as weak supporting hypotheses, but they are no longer the only way skills participate.

Answer to the specific question:

- It is now not “only prompt candidate skills”; skills are in the same overlap graph.
- It is now not “only segments create a graph”; graph nodes include trace segments plus eligible skill nodes.
- It still only attempts cliques related to current `new_segments`, by design, to avoid stale or pure skill-only refactor churn.

## Verification

Commands run:

```bash
python -m py_compile academic/benchmarks/bfcl/adapter.py academic/benchmarks/core/runner.py academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/maintenance/adapter.py academic/skill_repository/refactor_overlap.py academic/skill_repository/llm_maintenance.py academic/benchmarks/bfcl/related/manifest.py
pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_run_bfcl_task_error_feedback_uses_step_start_context_update academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py::test_paper_new_pending_skill_is_real_overlap_graph_node academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py::test_credit_assignment_harmful_event_appends_negative_bundle_case academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py::test_paper_new_pending_candidates_participate_in_posterior_refactor_recall
pytest -q academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py
```

Results:

- Targeted tests: `4 passed`.
- Added concurrency/heterogeneous-graph behavior tests: `2 passed`.
- Broader BFCL related + adapter + bundle-agent tests: `61 passed`.
- Warnings are existing pytest/Pydantic warnings, not introduced failures.

## Risks

- Concurrent heldout testing now uses per-task store copies when concurrency is greater than one, so future retrieval-side usage counters will not race across heldout tasks.
- Heterogeneous graph adds skill nodes to clique discovery, which can increase edge count. The current implementation filters attempts to cliques touching new trace segments and uses existing refactor budgets.
- Micro skips maintenance when there is no credit-created bundle case and no strong task-local helpful/harmful credit.

## Current Implemented State

- `credit assigner -> bundle case suggestion -> micro refine` is implemented through `assign_skill_credit_llm`, `_apply_credit_bundle_case_suggestions`, and `_run_micro_maintenance`.
- Existing skill bundle maintenance no longer receives full source/replay result lists in the normal path; it receives current bundle, credit cases, integration failures, and contract validation failures.
- New/empty skill bundle construction is isolated in `build_initial_skill_bundle_llm`.
- Heterogeneous overlap graph nodes include trace segments, active skills, and pending skills; pure skill-only cliques are filtered.
- Train/evolve remains serial; heldout test accepts `--test-concurrency`.

## Remaining Follow-Up

- The 150/50 manifest file is supported by CLI but should be generated and committed separately if it becomes the canonical experiment split.
- The reorganized directories under `academic/benchmarks/bfcl/`, `academic/benchmarks/core/`, and related test paths still appear as `??` in `git status`; decide whether to add them as the new layout or migrate back into tracked paths.
