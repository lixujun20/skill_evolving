# paper_new.md Algorithm Contract Test Plan

Test file: `academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py`.

These tests are written against the pseudocode in `academic/paper/paper_new.md:170-310`. They avoid real LLM/API calls and use controlled mocks so failures point to algorithm wiring rather than model randomness.

## Test Cases

### 1. `test_paper_new_prior_candidates_are_pending_not_executor_retrievable`

- Pseudocode covered: `extract_new_skills_prior_as_pending`, lines 193-210.
- Input:
  - One extracted candidate skill: `direct_symbol_prior`.
  - Round/task provenance: `round_index=0`, `task_index=0`, `task_id=train_1`.
- Expected output:
  - Skill is stored with `status=pending`.
  - Metadata has `is_pending_skill=True`, `is_promoted=False`.
  - `retrieval_enabled()` is false.
  - `store.retrieve(...)` returns `[]`.
- Meaning:
  - Verifies prior extraction uses LLM/software-engineering prior but does not immediately pollute executor retrieval.

### 2. `test_paper_new_pending_candidates_participate_in_posterior_refactor_recall`

- Pseudocode covered: `G.add(new_pending_skills)` and `extract_new_skills_posterior`, lines 266-231.
- Input:
  - One pending candidate skill related to explicit ticker handling.
  - One disabled duplicate skill.
  - One trace segment with matching explicit-symbol behavior.
- Expected output:
  - Pending skill appears in refactor coarse candidates.
  - Disabled skill does not appear.
- Meaning:
  - Verifies pending candidates are hidden from executor retrieval but visible to posterior overlap/refactor.

### 3. `test_paper_new_committed_posterior_refactor_promotes_pending_skill`

- Pseudocode covered: pending promotion after posterior extraction, lines 228-231.
- Input:
  - Store with one pending skill `direct_symbol_prior`.
  - Mock committed refactor report referencing that skill in `affected_skill_updates`.
- Expected output:
  - Promotion event is emitted.
  - Skill status becomes `active`.
  - `retrieval_enabled()` becomes true.
  - Metadata records `promotion_state=promoted`.
- Meaning:
  - Verifies only posterior evidence admits a prior candidate into the active skill library.

### 4. `test_paper_new_round_end_revokes_unpromoted_pending_without_deleting_audit`

- Pseudocode covered: `check_pending_skills`, lines 289-295.
- Input:
  - Store with one pending skill `weak_prior`.
  - Round-end revoke operation with reason `round_end_not_promoted_by_posterior_overlap`.
- Expected output:
  - Skill is archived, not physically deleted.
  - `promotion_state=revoked`.
  - `retrieval_enabled()` remains false.
- Meaning:
  - Verifies unvalidated prior candidates cannot survive as active prompt noise, while still preserving audit lineage.

### 5. `test_paper_new_evolve_flow_promotes_posterior_skill_before_heldout`

- Pseudocode covered:
  - `main`, lines 298-310.
  - `execute_with_skills`, lines 174-190.
  - `maintain_skills`, lines 260-268.
  - `text_based_policy_gradient/filter/check_pending` round-end behavior, lines 271-310.
- Input:
  - One mocked train task and one mocked heldout task.
  - Initial active seed skill `seed_active`.
  - Extractor returns one prior candidate `prior_symbol_rule`.
  - Round posterior refactor returns a committed attempt referencing `prior_symbol_rule`.
  - BFCL executor is mocked to assert active skill visibility at train and heldout phases.
- Expected output:
  - During train execution, `prior_symbol_rule` is not active/retrievable.
  - After posterior refactor, `prior_symbol_rule` is promoted.
  - During heldout execution, `prior_symbol_rule` is active/retrievable.
  - Final payload records `pending_skill_promotions` and no revocation for the promoted skill.
- Meaning:
  - End-to-end contract test for the new algorithm: prior candidates are safe during current execution and only become test-time skills after posterior evidence.

### 6. `test_task_from_case_rejects_official_fragment_with_wrong_argument_name`

- Pseudocode covered: bundle/test gate after posterior maintenance, especially the backward correctness norm `Unittest, Integrated Test`.
- Input:
  - `source_task_id=multi_turn_base_116`.
  - Literal task fragment question:
    - `I'm inclined to shake things up a bit. Let's take Zeta Corp out of the equation from my watchlist, shall we?`
  - Bad expected call:
    - `remove_stock_from_watchlist(stock='ZETA')`
  - Official BFCL schema for `remove_stock_from_watchlist` requires `symbol`, not `stock`.
- Expected output:
  - `_task_from_case(...)` returns a task marked invalid.
  - `metadata._bundle_case_invalid.reason == "unknown_expected_tool_argument"`.
  - `unknown_arguments == ["stock"]`.
- Meaning:
  - Verifies bundle cases cannot silently define their own wrong BFCL interface contract.

### 7. `test_expected_call_schema_rejects_non_literal_placeholder`

- Pseudocode covered: strict bundle/test contract before any executor call.
- Input:
  - Literal expected call:
    - `place_order(order_type='Buy', symbol='AAPL', price=<market_price>, amount=100)`
  - Tool schema for `place_order(order_type, symbol, price, amount)`.
- Expected output:
  - `_validate_expected_call_schema(...)` rejects the case with `reason == "invalid_expected_call_syntax"`.
- Meaning:
  - Verifies placeholders such as `<market_price>` cannot enter runnable bundle tests.

### 8. `test_build_bfcl_skill_bundles_llm_drops_patch_cases_with_wrong_schema_args`

- Pseudocode covered: bundle maintainer patch action and backward correctness gate.
- Input:
  - Existing bundle with valid official fragment.
  - Mock bundle maintainer patch adds an integration case with:
    - `remove_stock_from_watchlist(stock='ZETA')`
  - No full rebuild is allowed; the test fails if `distill_skill_bundle_llm` is called.
- Expected output:
  - The bad integration case is not written into `artifact.bundle.integration_cases`.
  - `bundle.fixtures.bundle_contract_dropped_cases` records the rejected case id and reason `unknown_expected_tool_argument`.
- Meaning:
  - Verifies both rebuild and lightweight patch paths are protected by the same BFCL schema contract gate.

### 9. `test_bfcl_skill_predicate_rejects_cross_domain_and_cross_tool_noise`

- Pseudocode covered: `execute_with_skills` retrieval gate before prompt injection.
- Input:
  - Vehicle task with `involved_classes=["VehicleControlAPI"]` and expected `setFuelLevel`.
  - Trading skill with `domains=["all", "stock_market", "trading"]` and allowed tools `get_stock_info/place_order/get_current_time`.
  - Vehicle skill with `domains=["VehicleControlAPI"]` and allowed tool `setFuelLevel`.
- Expected output:
  - `_bfcl_skill_matches_task(trading_skill, vehicle_task) is False`.
  - `_bfcl_skill_matches_task(vehicle_skill, vehicle_task) is True`.
- Meaning:
  - Prevents domain/tool retrieval contamination where a trading skill is injected into vehicle or social-media tasks due to text similarity.

### 10. `test_bfcl_retrieval_policy_uses_previous_observation_without_expected_leak`

- Pseudocode covered: `execute_with_skills`, especially `Ret(q, previous_observation, S, MS)`.
- Input:
  - A TicketAPI task.
  - One schema skill scoped to `create_ticket`.
  - A previous observation containing a failed `create_ticket` call with a missing-parameter error.
- Expected output:
  - Retrieval context phase is `previous_observation`.
  - Query includes the runtime failed tool and error.
  - Runtime tool tag `tool:create_ticket` is present.
  - `expected_tools` is absent.
- Meaning:
  - Verifies the old intra-step `tool_error_retry` branch has been replaced by normal next-step retrieval over previous observation.

### 11. `test_retrieval_backend_hybrid_embedding_is_auditable_without_external_api`

- Pseudocode covered: retrieve/recommendation pipeline and embedding-assisted retrieval.
- Input:
  - Two skills, one vehicle and one trading.
  - A fake deterministic embedding function.
  - `HybridEmbeddingRetrievalBackend`.
- Expected output:
  - Vehicle query ranks the vehicle skill first.
  - Audit records `retrieval_backend=hybrid_embedding`.
  - Selected rows include `embedding_score`.
- Meaning:
  - Verifies embedding retrieval is wired as an explicit backend and stays auditable without depending on external API calls in tests.

### 12. `test_extract_system_encodes_se_norms_and_few_shots`

- Pseudocode covered: extractor role prompt for forward prior extraction and text-based role feedback.
- Input:
  - Static extractor system prompt.
- Expected output:
  - Prompt mentions correctness, reusability, maintainability.
  - Prompt requires evidence span and non-applicability.
  - Prompt includes detailed function/interface, workflow, and knowledge/rule examples.
  - The examples include concrete names, scope metadata, non-applicability, and bad-artifact contrasts.
- Meaning:
  - Prevents regression back to a patchy generic prompt that does not express the paper's software-engineering maintenance principles.

### 13. `test_run_bfcl_task_error_feedback_uses_step_start_context_update`

- Pseudocode covered: `execute_with_skills`, especially executor-level sequencing for `Ret(q, previous_observation, S, MS)`.
- Input:
  - A mocked two-step BFCL executor run.
  - First model step calls `create_ticket` with wrong arguments.
  - Mock official environment returns a missing-parameter error.
  - Second model step stops.
- Expected output:
  - The second model call sees the tool result as the latest message.
  - No extra user message is appended for retry/reinjection.
  - Debug events include `retrieval` with `trigger=step_start` and `context.phase=previous_observation`.
  - Debug events do not include `tool_error_retry` or `prompt_reinjection`.
  - Retrieval context does not include `expected_tools`.
- Meaning:
  - Verifies the executor-level behavior, not just helper functions, so future refactors cannot silently reintroduce immediate error-retry prompt pollution.

### 14. `test_tir_extract_system_also_encodes_se_norms_and_few_shots`

- Pseudocode covered: extractor role consistency outside the BFCL-specific path.
- Input:
  - Static TIR extractor prompt in `academic/extractor.py`.
- Expected output:
  - Prompt mentions correctness, reusability, maintainability.
  - Prompt includes one-shot examples for function/interface, workflow-style computation, and knowledge/rule extraction.
- Meaning:
  - Ensures the older extractor route does not retain the previous aggressive extraction policy that conflicts with the paper algorithm.

### 15. `test_run_related_evolve_experiment_defaults_online_refactor_budget_to_zero`

- Pseudocode covered: macro/posterior maintenance boundary in lines 260-310.
- Input:
  - Empty train/test manifest.
  - No `BFCL_ONLINE_REFACTOR_MAX_PER_ROUND` environment setting.
- Expected output:
  - The produced round payload has `online_refactor_attempts=[]`.
- Meaning:
  - Documents the current mainline decision that online/per-task refactor is disabled and refactor belongs to round-end posterior maintenance.

### 16. `test_run_related_evolve_experiment_never_runs_online_refactor_even_if_env_enabled`

- Pseudocode covered: per-task execution/extraction loop versus round-end maintenance boundary.
- Input:
  - Three mocked train tasks.
  - `BFCL_ONLINE_REFACTOR_MAX_PER_ROUND=99`.
  - `run_bfcl_overlap_refactor_llm` mocked so any per-task invocation would be visible.
  - Round-end maintenance mocked separately.
- Expected output:
  - `run_bfcl_overlap_refactor_llm` is not awaited during the train-task loop.
  - `online_refactor_attempts=[]`.
  - Final checkpoint has no in-progress `current_round_state`.
- Meaning:
  - Prevents the deprecated online refactor path from silently re-entering experiments through environment variables.

### 17. `test_default_evolve_output_derives_checkpoint_path`

- Pseudocode covered: experiment-driver durability for the main multi-epoch loop.
- Input:
  - Mode `evolve`.
  - Tag `smoke_tag`.
  - No explicit `--output` or `--checkpoint`.
- Expected output:
  - Default output path is `bfcl_related50_50_smoke_tag_evolve.json`.
  - Derived checkpoint path is `bfcl_related50_50_smoke_tag_evolve_checkpoint.json`.
- Meaning:
  - Ensures ad-hoc 50/50 evolve launches still get mid-run checkpointing instead of only writing a final result.

## Real Smoke Finding: 2026-05-16

During `paper_new_real_smoke_4_1_20260516_155250`, the bundle builder produced a negative case with an invalid BFCL expected call:

```text
get_symbol_by_name(company_name='Apple Inc')
```

The official `get_symbol_by_name` schema uses parameter `name`, so this was a real algorithmic bug in the bundle/test layer rather than an API issue. The run was stopped and preserved. The fix adds schema validation for generated `task_fragment.expected` calls and drops invalid bundle cases before testing or skill promotion can use them.

Follow-up smoke `paper_new_real_smoke_4_1_schemafix_20260516_161010` completed end-to-end. It confirmed:

- Pending extractor skills were not injected during training before promotion.
- Invalid/generated bundle fragments were filtered into `bundle_contract_dropped_cases` instead of entering bundle tests.
- A separate retrieval bug remained: a TradingBot skill was injected into heldout `multi_turn_base_68` with `VehicleControlAPI/TwitterAPI`, causing cross-domain prompt pollution.

The retrieval bug was fixed by adding BFCL task-level domain/tool predicate checks. A replay over the smoke skill store now selects no TradingBot skills for `multi_turn_base_68`; rejected rows show `predicate_false`.

Follow-up real single-task validation `paper_new_retrievalfix_single_68_v2_20260516_163020` used the same smoke skill store on heldout `multi_turn_base_68` after the predicate fix:

- Retrieval selected no skills on all turns.
- Final `prompt_injected_skills=[]`.
- `skill_stats.retrieved_counts={}` and `prompt_injected_counts={}`.
- The task still failed (`official_valid_rate=0.0`, `avg_score=0.7368`), but the remaining errors are native VehicleControl/Twitter execution errors rather than skill-retrieval pollution.

## Verification Commands

- `pytest -q academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py`
- `pytest -q academic/benchmarks/tests/bfcl_related/test_experiment.py academic/skill_repository/test_refactor_overlap.py`
- `pytest -q academic/benchmarks/tests/maintenance/test_bundle_agent.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`
- `pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py academic/benchmarks/tests/bfcl_related/test_experiment.py academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py academic/method_validation/tests/test_store_retriever_refactor_contracts.py academic/skill_repository/test_llm_maintenance_feedback.py -q`

Current result:

- Combined focused suite on 2026-05-16: `100 passed`

## Remaining Untested Future TODOs

- Online with/without validation for pending skills is not implemented yet, so no contract test exists.
- Small-model retrieval gate is not implemented yet, so no contract test exists.
- Full heterogeneous graph with skill nodes is not implemented yet; current tests cover segment-primary graph plus skill side recall.
