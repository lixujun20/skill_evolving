# Paper New Algorithm Implementation Update, 2026-05-17

## Scope

This update implements the current `paper_new.md` mainline as a single-epoch online skill-evolution loop with task-level micro maintenance and macro-window posterior refactor.

The old `rounds[]` result field remains for analysis compatibility, but its mainline meaning is now `epochs[]`; default CLI execution uses `--epochs 1`.

## Pseudocode Mapping

| `paper_new.md` pseudocode line | Real implementation |
| --- | --- |
| `execute_with_skills(q, S, MS)` | `academic/benchmarks/bfcl/related/experiment.py::_run_related_evolve_experiment`, `_run_bfcl_baseline(... store=store, top_k_skills=..., phase="related_train_epoch_0")` |
| `Ret(q, trace[-1], S, MS)` | BFCL executor/store retrieval through `ArtifactStore.retrieve*` and benchmark runner; retrieval backend lives in `academic/skill_repository/store.py` |
| `extract_new_skills_prior_as_pending(trace)` | `_run_related_evolve_experiment` calls `extract_bfcl_skill_artifacts_llm`, then `_mark_prior_artifacts_pending`, then `store.add_pending` |
| pending skills not used for execution | `SkillArtifact.retrieval_enabled()` disables `pending/rejected/archived`; `ArtifactStore.add_pending` marks metadata |
| pending skills participate in posterior extraction | `run_bfcl_overlap_refactor_llm` passes `store.all()` to refactor recall; pending skills are not filtered from maintenance recall |
| `extract_new_skills_posterior(trace, G)` | macro maintenance call to `run_bfcl_overlap_refactor_llm(... overlap_state=..., new_segments=window_segments)` |
| `G.find_potential_overlap_segment(trace)` | `update_overlap_graph_state` + `materialize_overlap_graph` + `find_refactor_cliques` |
| posterior shared skill bundle/test gate | `run_bfcl_overlap_refactor_llm` builds bundles and executes bundle tests before commit |
| promote relevant pending skills | `_promote_pending_from_refactor_report` after each macro window and final window |
| `refine_skills(trace, relevant_skills)` | task-level micro maintenance in `_run_related_evolve_experiment` calls `_run_round_refine_and_refactor(... new_segments=None, artifact_names=micro_targets)` |
| `assign_credit(trace, relevant_skills)` | `assign_skill_credit_llm` and `_credit_event_records` |
| positive/negative case evidence | `_apply_credit_case_evidence`, `build_bfcl_skill_bundles_llm`, `maintain_skill_bundle_llm` |
| targeted refiner | `refine_bfcl_skill_store_llm(... artifact_names=..., credit_context_by_skill=...)` |
| `text_based_policy_gradient(MS)` | `update_extractor_rules_from_feedback_llm` over extractor feedback rows, capped to 5 rules |
| `filter_skills(S)` | `_aggregate_skill_credit` + `_apply_skill_credit_filter`, conservative negative-margin filter |
| `check_pending_skills(S)` | `store.revoke_unpromoted_pending` after epoch macro maintenance |
| `micro_maintenance_step` | `_run_related_evolve_experiment(... micro_maintenance_step=...)`, default 1 |
| `macro_maintenance_step` | `_run_related_evolve_experiment(... macro_maintenance_step=...)`, CLI `--macro-maintenance-step`, default 5 |

## Implemented Changes

1. Single-epoch mainline:
   - Added `epochs`, `micro_maintenance_step`, and `macro_maintenance_step` parameters.
   - CLI defaults now use `--epochs 1`; `--rounds` remains a legacy alias for compatibility.
   - Training phase label is now `related_train_epoch_0`.

2. Task-level micro maintenance:
   - After each train task, the driver performs credit assignment, relation graph update, extraction, segment indexing, and relevant-only refinement.
   - Micro refine targets are restricted to retrieved/injected/used skills plus strong credit targets from the current trace.

3. Macro-window posterior refactor:
   - The driver accumulates `window_train_details` and `window_segments`.
   - Every `macro_maintenance_step`, it runs refactor on only the current window while the overlap graph still contains historical segments.
   - A final leftover window is flushed before held-out evaluation.

4. Refactor coverage:
   - `run_bfcl_overlap_refactor_llm` now returns `refactor_segment_coverage`.
   - Every new segment gets a coverage row: committed extraction, rejected/no-op, deferred, or no candidate group.
   - Top-k clique budget no longer makes new segments disappear silently.

5. Target-only refine:
   - `refine_bfcl_skill_store_llm` accepts `artifact_names`, `credit_context_by_skill`, and `dependency_context_by_skill`.
   - It only iterates target artifacts and uses dependency neighborhoods rather than full-store summaries.

6. Credit prompt guard:
   - The credit prompt now says irrelevant retrieved/injected skills are neutral or uncertain by default.
   - Weak prompt pollution must be recorded as `evidence.suspected_prompt_pollution`, not direct harmful credit.

7. Skill relation and dependency metadata:
   - Added `update_skill_relation_graph` for `co_retrieved_with`, `co_used_with`, `derived_from`, `refines`, `conflicts_with`, `calls`, and `called_by` buckets.
   - Added `validate_skill_static_dependencies` for code-like skills, including AST call-symbol extraction and known-skill reference detection.

8. Result schema additions:
   - Top-level `epochs`, `micro_maintenance_step`, `macro_maintenance_step`.
   - Top-level and per-epoch `maintenance_windows`, `micro_maintenance_reports`, `refactor_segment_coverage`.
   - Checkpoint sidecars now preserve window state for faithful resume.

9. Promotion idempotence:
   - Posterior pending-skill promotions are de-duplicated in analysis rows.
   - A synthetic instance exposed duplicate promotion rows when multiple macro windows referenced the same pending skill; this is now guarded by `_dedupe_promotion_rows`.

10. Real-trace coverage bug fix:
   - A real BFCL history trace test hit `run_bfcl_overlap_refactor_llm(... overlap_state=None)` and exposed a missing `defaultdict` import in `_refactor_segment_coverage`.
   - The import is fixed and a regression test now covers the non-incremental coverage path.

## Tests Added Or Updated

1. `test_paper_new_macro_windows_flush_new_segments_and_coverage`
   - Uses three synthetic train tasks, `macro_maintenance_step=2`.
   - Verifies macro windows are `[train_1, train_2]` and `[train_3]`.
   - Verifies each new segment appears in result-level `refactor_segment_coverage`.

2. `test_paper_new_refine_targets_only_requested_skills_and_receives_credit`
   - Verifies `refine_bfcl_skill_store_llm` only refines the requested target skill.
   - Verifies recent credit context is passed into the refiner.

3. `test_paper_new_static_dependency_validator_records_code_like_calls`
   - Builds a code-like skill referencing another skill.
   - Verifies static dependency metadata records the auto dependency.

4. `test_paper_new_credit_prompt_keeps_weak_irrelevance_out_of_harmful`
   - Guards the credit prompt wording that prevents weak prompt-pollution over-attribution.

5. `test_paper_new_relation_graph_updates_calls_and_co_use_edges`
   - Verifies relation graph writes `calls`, `called_by`, `co_retrieved_with`, and `co_used_with`.

6. `test_paper_new_promotion_rows_are_idempotent_for_analysis`
   - Verifies duplicate promotion rows for the same skill are collapsed for result analysis.

7. `test_paper_new_real_refactor_coverage_path_without_overlap_state`
   - Exercises the real `run_bfcl_overlap_refactor_llm` coverage path without an incremental `OverlapGraphState`.
   - Guards against the missing-import regression found by the real-trace test.

8. Existing posterior-pending tests were updated to expect `related_train_epoch_0`.

## Verification

Commands run:

```bash
python -m py_compile academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/maintenance/adapter.py academic/skill_repository/llm_maintenance.py
pytest -q academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py academic/benchmarks/tests/bfcl_related/test_experiment.py
pytest -q academic/benchmarks/tests/maintenance/test_bundle_agent.py academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py academic/method_validation/tests/test_store_retriever_refactor_contracts.py academic/skill_repository/test_llm_maintenance_feedback.py academic/skill_repository/test_refactor_overlap.py
```

Results:

- `py_compile`: passed.
- Combined focused suite: `82 passed`.
- After the real-trace coverage fix: `83 passed`.
- Synthetic instance smoke:
  - 3 train tasks, `macro_maintenance_step=2`.
  - Macro windows were `[train_1, train_2]` and `[train_3]`.
  - `refactor_segment_coverage` covered all 3 segments.
  - Pending `prior_symbol_rule` was promoted before held-out execution.
  - Held-out mock run saw `prior_symbol_rule` as active and reached `official_valid_rate=1.0`.
- Real BFCL trace offline smoke:
  - Source: `_tmp_paper_new_real_smoke_4_1_schemafix_20260516_161010_evolve.json`.
  - Real task ids: `multi_turn_base_120`, `multi_turn_base_130`, `multi_turn_base_116`, `multi_turn_base_117`.
  - Extracted 4 real trace segments and built an overlap graph with 4 nodes, 6 edges, and 1 clique.
  - Verified segment index stats, maintenance target selection, skill relation graph, AST dependency validation, and refactor coverage rows on real BFCL traces.
  - Coverage rows were produced for `multi_turn_base_120:task` and `multi_turn_base_130:task` with action `defer` under `BFCL_REFACTOR_MAX_CLIQUES=0`.

Warnings are existing pytest config / Pydantic deprecation warnings, not introduced by this change.

## Known Boundaries

1. The executor still owns per-turn retrieval inside the BFCL runner. The experiment driver records and maintains around those traces but does not replace the executor.
2. Refactor graph is still segment-primary. Pending/existing skills are recalled as candidates and relation metadata is maintained, but full heterogeneous clique discovery over skill nodes is not yet a separate graph algorithm.
3. LOO/Shapley-style with/without execution is represented by current bundle and credit machinery; a full online paired rollout for every retrieved skill is still future work because of API cost.
