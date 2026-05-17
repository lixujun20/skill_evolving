# paper_new.md Algorithm Implementation Map

Source pseudocode: `academic/paper/paper_new.md:170-310`.

Last checked: 2026-05-17.

Correction update: 2026-05-17 afternoon. The maintenance path now uses explicit
credit-assignment bundle suggestions, `_run_micro_maintenance(...)`, and
`_run_macro_maintenance(...)`. The old `_run_round_refine_and_refactor(...)`
remains only as a legacy internal compatibility hook for older tests.

## Conclusion

Every pseudocode block in `paper_new.md:170-310` now has an explicit implementation anchor in the BFCL related-task experiment mainline. The mapping is one-to-one at the algorithm-stage level:

- execution with skill retrieval;
- prior trace extraction into pending skills;
- posterior overlap/refactor promotion into active skills;
- per-trace credit assignment -> bundle case suggestion -> micro refine;
- distinct micro/macro maintenance functions;
- text-based RL rule updates;
- conservative skill filtering;
- pending-skill revocation;
- structured output for audit.

Some pseudocode lines are implemented as bounded engineering equivalents rather than literal unbounded loops. These are called out in the fidelity notes below.

## Line-to-Implementation Map

| Pseudocode line(s) | Semantic step | Current implementation anchor | Fidelity |
|---|---|---|---|
| `170` | Inputs: queries, initial skill library `S`, reusage/overlap graph `G`, traces `T`, meta-skills `MS`, evolve turns/epochs. | `academic/benchmarks/bfcl/related/experiment.py:_run_related_evolve_experiment` `1774-1823` initializes task split, `ArtifactStore`, `SegmentVectorIndex`, role feedback memory, checkpoint state. | Direct implementation. |
| `171` | Roles: executor, extractor, bundle builder, tester, retriever, refiner, reward model. | Executor/retriever via `_run_bfcl_baseline` call `1906-1927` and heldout call `2337-2358`; extractor via `extract_bfcl_skill_artifacts_llm` `1963-1977`; bundle/test/refine/refactor via `_run_round_refine_and_refactor` `1656-1771`; reward/meta-rule update via `update_extractor_rules_from_feedback_llm` `2211-2244`. | Direct implementation with BFCL official runner reused for Exe/Ret. |
| `174-190` | `execute_with_skills`: execute task turn by turn with retrieved skills. | `experiment.py` calls official BFCL runner in `_run_bfcl_baseline` `1906-1927`. Skill mentions are projected by `_mentioned_skill_names` `196-210` and `_used_skill_names` `213-219`. Retrieval-disabled pending candidates are blocked by `academic/skill_repository/types.py:218-222`. | Direct at driver level; internal turn loop remains inside official BFCL runner, not duplicated. |
| `184` | `Ret(q, trace[-1], S, MS)` turn-level skill retrieval. | Retrieval is owned by the BFCL runner and `ArtifactStore` retrieval path; debug scoring/projection lives in `academic/skill_repository/store.py:440-525`. Pending/rejected/archived skills are excluded by `SkillArtifact.retrieval_enabled` `types.py:218-222`. | Direct role reuse; not reimplemented in experiment driver. |
| `185` | `Exe(q, trace, turn_relevant_skills)`. | `_run_bfcl_baseline(... store, top_k_skills, min_skill_score, skill_injection_mode, ...)` `experiment.py:1906-1927`, final heldout `2337-2358`. | Direct reuse of official BFCL backend. |
| `193-210` | Prior extraction: sample candidate skills from the current trace, mark them pending, test before accepting candidates. | Extract current trace with `existing_artifacts=[]` in `experiment.py:1962-1977`; mark candidates pending in `_mark_prior_artifacts_pending` `515-534`; insert with `ArtifactStore.add_pending` `store.py:221-249`; pending candidates are not retrievable via `types.py:218-222`. Bundle/test gate is performed when the pending skill becomes a maintenance target in `_run_round_refine_and_refactor` `1682-1725`. | Mostly direct. Testing is scheduled through bounded maintenance rather than a literal local `while True`. |
| `199-200` | `skills = Ext(trace, MS)`: extractor uses current trace plus meta-skills/rules. | `extract_bfcl_skill_artifacts_llm` call `experiment.py:1963-1977`; prompt implementation `academic/skill_repository/llm_maintenance.py:45-202`; extractor rules are appended in `llm_maintenance.py:1692-1704`. | Direct implementation. |
| `202-203` | `skill.is_pending_skill=True`, `is_promoted=False`. | `_mark_prior_artifacts_pending` `experiment.py:515-534`; `ArtifactStore.add_pending` `store.py:221-249`. | Direct implementation. |
| `206-208` | Build bundle and test prior skill. | `_run_round_refine_and_refactor` builds bundles `experiment.py:1682-1691`, executes/caches bundle tests `1692-1725`. | Bounded maintenance equivalent. |
| `213-232` | Posterior extraction from real overlap; add solid skills; promote relevant pending skills. | Per-task segment extraction/index/update in `experiment.py:2002-2013`; macro window refactor with `new_segments=window_segments` `2057-2080`; final leftover macro flush `2141-2185`; pending promotion `_promote_pending_from_refactor_report` `553-588`, invoked `2091-2095` and `2175-2179`. | Direct stage implementation. Posterior extraction is clique/refactor based and macro-windowed, not every trace unbounded. |
| `217` | `G.find_potential_overlap_segment(trace)`. | Segment index: `academic/benchmarks/bfcl/related/segment_index.py:94-203`; incremental graph state: `academic/skill_repository/refactor_overlap.py:135-158`, `556-613`; materialized graph with sparse/embedding/error weights `616-625`; clique discovery `872-930`. | Direct implementation using incremental overlap graph. |
| `219-225` | Extract/test posterior skills from overlap candidates. | `run_bfcl_overlap_refactor_llm` `adapter.py:2172-2508`; LLM clique refactor `2271-2328`; candidate store bundle build `2346-2354`; bundle execution gate `2355-2405`; repair loop `2271-2433`; commit only after passing tests `2439-2478`. | Direct implementation with bounded repair rounds. |
| `226` | `G.add(skills)`: add posterior skills to graph/library. | Shared skill/update commit in `adapter.py:2442-2469`; relation graph update `2447-2466`; static dependency validation `2467`; result rows emitted to `refactor_groups` in output `experiment.py:2433-2447`. | Direct implementation. |
| `229-231` | Promote pending skills used by posterior extraction. | `_pending_skill_names_from_refactor_attempt` `experiment.py:537-550`; `_promote_pending_from_refactor_report` `553-588`; `ArtifactStore.promote_pending` `store.py:258-278`. | Direct implementation. |
| `235-257` | Refine existing relevant skills according to execution credit. | Candidate relevant skills from `_mentioned_skill_names` `196-210`; credit LLM call `experiment.py:1931-1957`; credit prompt `llm_maintenance.py:237-306`; evidence storage `_apply_credit_case_evidence` `experiment.py:327-365`; targeted maintenance/refine `_run_round_refine_and_refactor` `2030-2055`. | Direct stage implementation. Positive/negative cases are accumulated as evidence and consumed by maintenance, not appended as literal bundle rows immediately. |
| `239` | `assign_credit(trace, relevant_skills)`. | `assign_skill_credit_llm` call `experiment.py:1935-1947`; implementation `llm_maintenance.py:1587-1636`; event normalization `_credit_event_records` `experiment.py:234-324`. | Direct implementation. |
| `242-252` | Negative credit creates negative evidence and triggers refinement/test. | Harmful events stored in `artifact.evidence.harmful_cases` by `_apply_credit_case_evidence` `327-365`; micro target selection includes recent strong credit targets `2014-2030`; refine called with `credit_context_by_skill` `2030-2055`; refiner consumes credit context in `adapter.py:2003-2169`. | Direct bounded implementation. |
| `253-256` | Non-negative credit creates positive evidence. | Helpful events stored in `artifact.evidence.helpful_cases` by `_apply_credit_case_evidence` `327-365`; aggregate feedback rows `_build_extractor_feedback_rows` `611-721`. | Direct evidence implementation; bundle-case materialization remains through maintenance builder. |
| `260-268` | `maintain_skills`: prior pending extraction, add to graph, posterior extraction, refine existing relevant skills. | Main per-task loop `experiment.py:1904-2056` performs execution, credit, relation graph update, prior extraction, segment index/overlap update, and micro refine. Macro posterior refactor runs `2057-2103`; final macro flush `2141-2185`; combined reports `2188-2194`. | Direct implementation. |
| `264-266` | Add pending skills to `S` and `G`. | `store.add_pending` `experiment.py:1984-1985`; relation graph `derived_from` update `1986-1993`; segment graph update `2002-2013`; `ArtifactStore.add_pending` `store.py:221-249`. | Direct implementation. |
| `267` | Add posterior solid skills. | `run_bfcl_overlap_refactor_llm` commits shared skills/updates `adapter.py:2442-2478`; pending promotion is applied after macro windows `experiment.py:2091-2095`, `2175-2179`. | Direct implementation. |
| `268` | Update refined skills. | `refine_bfcl_skill_store_llm` call `experiment.py:1726-1734`; implementation `adapter.py:2003-2169`; relation graph `refines` update `2155-2159`. | Direct implementation. |
| `271-279` | Text-based policy gradient: summarize runtime feedback into meta-skills/rules for extractor. | Feedback rows `_build_extractor_feedback_rows` `experiment.py:611-721`; rule update call `2211-2244`; rule updater `llm_maintenance.py:1734-1775`; max rule count enforced by `_ROLE_FEEDBACK_RULE_LIMIT` through `max_rules`. | Direct implementation when `extractor_trl_enabled=True`; disabled variants preserve history with no update `2245-2264`. |
| `275-279` | Use skill groups, valid call counts, reward model to produce semantic gradient. | Runtime feedback rows aggregate extraction count, retrieved/injected/used counts, valid/hurt counts, call errors, and bundle failures in `experiment.py:611-721`; `update_extractor_rules_from_feedback_llm` acts as the text reward/meta-policy updater `llm_maintenance.py:1734-1775`. | Engineering equivalent. Not literal numeric policy gradient; text reward summarization matches paper intent. |
| `282-286` | Filter worst skills by correctness/usage. | Credit aggregation `_aggregate_skill_credit` `experiment.py:368-467`; conservative negative-margin filter `_apply_skill_credit_filter` `470-512`; invoked at macro/round end `2204-2210`. Disabled skills are excluded by `types.py:215-222`. | Conservative equivalent, not literal `bottom_p(0.1)`. |
| `289-295` | Revoke pending skills never promoted in practice. | `ArtifactStore.revoke_unpromoted_pending` `store.py:280-286`; invoked at round end `experiment.py:2195-2198`; controlled by `BFCL_REVOKE_UNPROMOTED_PENDING`. | Direct implementation. |
| `298-310` | Main loop over epochs/tasks with micro and macro maintenance. | Epoch config and compatibility `experiment.py:1802-1811`; task loop `1904-2140`; micro maintenance gate `2030-2056`; macro window gate `2057-2103`; final leftover macro flush `2141-2185`; report assembly/output `2270-2460`. | Direct implementation. Note spelling `marco_maintenance_step` in pseudocode maps to CLI/config `macro_maintenance_step`. |
| output contract implied by plan | Store snapshots, segment stats, refactor groups, skill versions, help links, token breakdowns. | Returned result fields in `experiment.py:2368-2460`: `rounds`, `segment_index_stats`, `segment_index`, `token_breakdown`, `role_feedback`, `pending_skill_summary`, `skill_credit_events`, `skill_credit_summary`, `skill_credit_filter_decisions`, `refactor_groups`, `refactor_segment_coverage`, `skill_versions`, `skills`, `test_help_links`, summaries/details. | Direct implementation. |

## Supporting Components

| Component | Implementation anchor | Notes |
|---|---|---|
| Segment vector index | `academic/benchmarks/bfcl/related/segment_index.py:94-203`, query APIs `254-331`, embedding map/stats `333-363`. | Stores rows even when embeddings are unavailable; strict mode raises instead of silent downgrade. |
| Incremental overlap graph | `academic/skill_repository/refactor_overlap.py:135-158`, `556-613`. | Adds only new segment edges instead of rebuilding all sparse scores from scratch. |
| Combined sparse/embedding/error edge weights | `refactor_overlap.py:616-625`. | Uses default `alpha=0.45`, `beta=0.35`, `gamma=0.20`. |
| Clique-level refactor | `adapter.py:2172-2508`; clique generation `refactor_overlap.py:872-930`. | Refactor receives clique segments, graph, existing skills, bundle gate, repair context, and commit/reject status. |
| Skill relation graph | `adapter.py:84-146`; updated after retrieval/use `experiment.py:1957-1961`, prior extraction `1986-1993`, refine `adapter.py:2155-2159`, refactor `2447-2466`. | Tracks `calls`, `called_by`, `co_retrieved_with`, `co_used_with`, `derived_from`, `refines`, `conflicts_with`. |
| Static dependency validation | `adapter.py:149-201`; invoked in maintenance `experiment.py:1762-1769` and refactor commit `adapter.py:2467`. | Adds detected skill dependencies and relation edges for code-like artifacts. |
| Pending skill lifecycle | `store.py:221-249`, `258-286`; helper functions `experiment.py:515-588`. | Pending skills can join maintenance/refactor but cannot pollute executor retrieval until promoted. |
| Credit assignment | Prompt `llm_maintenance.py:237-306`; call `1587-1636`; driver call `experiment.py:1931-1957`. | Handles knowledge-like and function-like skills, with conservative harmful attribution. |
| Credit -> bundle cases | `experiment.py:_apply_credit_bundle_case_suggestions`; `llm_maintenance.py:CREDIT_ASSIGNMENT_SYSTEM` output schema. | Credit assignment now owns attribution and proposes replayable positive/negative/integration cases. Cases reuse official task snapshots and do not invent expected calls. |
| Micro maintenance | `experiment.py:_run_micro_maintenance`, `_run_bundle_test_and_refine_targets`. | Task-local only; no overlap refactor, no TRL, no pending revocation, no full-store bundle rebuild. |
| Macro maintenance | `experiment.py:_run_macro_maintenance`, `_run_window_overlap_refactor`. | Window-level posterior refactor/filter reporting; no ordinary per-skill refine outside refactor bundle gates. |
| Bundle maintenance split | `adapter.py:build_initial_skill_bundle_llm`, `patch_skill_bundle_from_credit`, `build_bfcl_skill_bundles_llm`. | New skills may use focused source evidence; existing skills receive current bundle, credit cases, integration failures, and contract validation failures only. |
| Heldout parallel test | `core/runner.py:_run_bfcl_baseline(concurrency=...)`; related evolve passes `test_concurrency`. | Train/evolve remains serial; concurrent heldout tasks use per-task store copies when concurrency is greater than one. |
| Extractor prompt and one-shot examples | `llm_maintenance.py:45-202`. | Enforces correctness/reusability/maintainability and explicit scope/non-applicability. |
| Extractor TRL/meta-rules | `llm_maintenance.py:204-235`, updater `1734-1775`; driver `experiment.py:2211-2244`. | Maintains compact rule list with max `n=5` through `_ROLE_FEEDBACK_RULE_LIMIT`. |

## Fidelity Notes and Remaining Engineering Substitutions

1. `while True` loops in the pseudocode are not implemented as unbounded loops. Bundle/test/refine and refactor use bounded repair/caching gates to prevent infinite API spend and checkpoint stalls.
2. Prior extracted skills remain non-retrievable until promotion. New/empty bundles are initialized from focused evidence; existing bundles are patched from credit evidence instead of rediscovering cases from full traces.
3. Posterior extraction is implemented as macro-window clique refactor plus final leftover flush. This is intentionally cheaper and more auditable than per-task online LLM refactor; `_online_refactor_budget_from_env` is hardcoded to `0` in `experiment.py:742-750`.
4. Text-based RL is a text feedback/rule-update mechanism, not a numeric gradient update. Runtime reward evidence is summarized into extractor rules.
5. `S.filter_bottom_p(0.1)` is implemented as conservative negative-margin disabling, not percentile pruning. This avoids deleting rarely observed but potentially useful skills.
6. Online paired with/without-skill rollout and full Shapley-style credit assignment are not implemented. Current credit is LLM attribution plus bundle/integration evidence and replayable official task fragments.
7. Retrieval is still owned by the BFCL runner/store path. The map verifies that pending/disabled skills are excluded, but the experiment driver does not duplicate turn-level retrieval logic.

## Verification Status

Latest implementation checks reported in `PAPER_NEW_IMPLEMENTATION_UPDATE_20260517.md`:

- Core files compile.
- Focused contract tests pass.
- Real BFCL offline trace validation passed on four tasks.
- Real trace validation exercised segment extraction, overlap graph/clique discovery, relation graph update, static dependency validation, target selection, and refactor coverage rows.
