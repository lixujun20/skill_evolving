# paper_new.md Algorithm Implementation Report

## What Changed

- Added `pending` skill semantics to the repository.
  - Pending skills are stored and versioned, but `retrieval_enabled()` returns false until promotion.
  - Pending skills can still participate in maintenance/refactor discovery.

- Wired BFCL online extraction to produce pending prior candidates.
  - Each train task still runs extraction after execution.
  - Extracted artifacts are marked with `is_pending_skill=True`, `is_promoted=False`, `promotion_state=pending`, and prior extraction provenance.
  - Same-name candidates for existing active skills are stored under a `__pending_N` name while preserving `candidate_for_existing_skill` for feedback attribution.

- Wired posterior overlap/refactor to promote pending candidates.
  - Committed refactor attempts are scanned for affected pending skills.
  - Any referenced pending skill is promoted to `active`, making it eligible for future retrieval.
  - Unpromoted pending skills are archived at round end by default.

- Preserved the existing official BFCL executor.
  - No new executor was introduced.
  - The implementation keeps using `_run_bfcl_baseline` and the official BFCL backend.

- Updated refactor candidate recall.
  - Pending skills are excluded from executor retrieval but included in refactor coarse recall.
  - Disabled/rejected/archived skills remain excluded.

- Refactored the BFCL executor control boundaries.
  - Added `PromptPolicy`, `ToolApiClient`, `RetrievalPolicy`, `SkillInjectionPolicy`, and `TerminationPolicy` objects in `academic/benchmarks/bfcl/adapter.py`; provider clients now live in `academic/benchmarks/bfcl/tool_clients.py`.
  - Added `ToolSelectionPolicy` for `adapter_mode` tool exposure.
  - Added `ToolCallHandler` objects for skill-tool, `use_skill`, and domain-tool calls, so the executor loop no longer owns every tool branch directly.
  - Removed the special "tool error immediately re-retrieve and reinject" path.
  - Retrieval now runs at step start with `previous_observation` as part of the retrieval context, matching `Ret(q, previous_observation, S, MS)`.
  - Step-start retrieval may update the system skill context, but it no longer appends an extra user message or emits `prompt_reinjection`.
  - The termination policy only uses model EOS, step budget, and repeated identical-call safety stop; it does not use expected-call coverage.

- Added retrieval backend abstraction for the repository.
  - `ArtifactStore` now accepts a `RetrievalBackend`.
  - Default remains `LexicalTagRetrievalBackend`, preserving current experiment behavior.
  - Added `HybridEmbeddingRetrievalBackend`, which reuses the existing embedding provider for whole-skill text projections and records `embedding_score` in retrieval audit.
  - The implementation remains in-memory/full-scan for 50/50 scale; ANN/pgvector skill index is intentionally deferred.

- Rewrote the extractor base prompt around software-engineering norms.
  - The prompt now explicitly prioritizes correctness, reusability, and maintainability.
  - It requires evidence span, scope, non-applicability, and maintenance action metadata.
  - It includes three detailed one-shot examples: function/interface contract, workflow, and knowledge/rule.
  - Each example includes good output structure plus a bad overgeneralized artifact, so later generations have a concrete quality target.
  - The older TIR extractor prompt in `academic/extractor.py` now follows the same SE norms and includes detailed executable examples instead of the prior aggressive extraction style.

- Refactored maintenance JSON-role API calls.
  - Added `JsonRoleClient`, `AnthropicJsonRoleClient`, and `GenericJsonRoleClient`.
  - `_ask_json(...)` now delegates provider-specific JSON calls through the role client instead of keeping provider branches inline.

- Removed per-task online refactor from the `paper_new` mainline.
  - Training still performs per-task execution, credit assignment, extraction, segment indexing, and overlap graph updates.
  - Clique-level refactor now runs only as round-end posterior maintenance through `_run_round_refine_and_refactor`.
  - `BFCL_ONLINE_REFACTOR_MAX_PER_ROUND` is intentionally ignored; compatibility fields such as `online_refactor_attempts` remain empty/zero in checkpoint/result payloads.

- Documented canonical and deprecated experiment entrypoints.
  - Canonical driver: `academic/benchmarks/bfcl/related/experiment.py`.
  - Local proxy wrapper: `academic/benchmarks/bfcl/related/proxy_runner.py`.
  - Historical top-level scripts have been removed from the current benchmark package; result logs may still mention them as provenance.

- Hardened ad-hoc CLI checkpoint defaults.
  - `baseline`, `evolve`, `analyze`, and `validate-config` now resolve the default output path before invoking the experiment driver.
  - `evolve` now passes a derived checkpoint path even when the user omits `--output` and `--checkpoint`, preserving mid-run checkpointing for ad-hoc 50/50 launches.

## Why

The algorithm in `paper_new.md` separates forward-prior extraction from posterior evidence admission. The previous implementation added extracted skills directly to the active store, which allowed rough single-trace skills to pollute retrieval. The new implementation makes prior extraction safe: candidates can be tested and refactored, but cannot affect execution until posterior overlap evidence promotes them.

## Fidelity Notes

- Faithful:
  - Prior skills are pending and not used for execution.
  - Pending skills participate in posterior refactor.
  - Posterior committed refactor can promote pending skills.
  - Credit assignment, extractor feedback, credit filtering, and round-end maintenance remain integrated.
  - Runtime retrieval can consume previous observation/error feedback without a separate intra-step retry hack.
  - Executor retrieval audit remains separated from final prompt/tool injection decisions.
  - Expected BFCL answers are not used by formal retrieval, injection, or stop policies.
  - Per-task online refactor is disabled; posterior refactor is round-end only.

- Approximate:
  - Posterior extraction is implemented by the existing clique-level refactorer, not by calling the same extractor on `candidate_traces`.
  - Positive/negative credit does not immediately append a concrete bundle case per event; evidence is accumulated and used by round maintenance.
  - Filtering is conservative negative-margin disabling, not percentile bottom-p pruning.

- Still missing:
  - Online with/without validation for pending skills.
  - Retrieval small-model gate.
  - Production-scale pgvector/ANN skill retrieval index; current skill embedding backend is a small-store in-memory hybrid scorer.
  - Explicit heterogeneous graph nodes for skills; current refactor graph is trace-segment primary with skill side recall.
  - Package-level directory rename is complete for the BFCL benchmark stack; `academic/benchmarks/bfcl/adapter.py` is now the compatibility execution surface, with loader/scoring/environment/retrieval/tool-client logic split into sibling modules.

## Real Smoke: 2026-05-16

- Command family:
  - `python -m academic.benchmarks.bfcl.related.proxy_runner --mode evolve --manifest _tmp_related_manifest_smoke_1_1.json --rounds 1 --tool-api-style auto --execution-backend official`
- Output:
  - `academic/results/_tmp_policy_refactor_v2_auto_smoke_1_1_20260516_231344_evolve.json`
- Result:
  - Train task `multi_turn_base_120`: `official_valid=True`, `call_f1=0.8`, no injected skills.
  - Heldout task `multi_turn_base_68`: `official_valid=False`, `call_f1=0.7778`, no injected skills.
  - No occurrences of `tool_error_retry`, `Expected tool focus`, `expected_tools`, `all_expected_covered_and_extra`, or `Likely required tool names` in the result.
  - Segment index wrote 3 rows with 3 embeddings.
- API finding:
  - Forcing `--tool-api-style openai_direct` against the local Claude proxy at port 4000 returned `404 Not Found` for BFCL tool calls.
  - `--tool-api-style auto` correctly selected the Anthropic-style tool path and executed the smoke run.

## Verification

- `pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py academic/benchmarks/tests/bfcl_related/test_experiment.py academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py academic/method_validation/tests/test_store_retriever_refactor_contracts.py academic/skill_repository/test_llm_maintenance_feedback.py -q`

Result: `102 passed` on 2026-05-17 after the default-checkpoint entrypoint fix.

## Real Smoke: 2026-05-17

- Command family:
  - `python -m academic.benchmarks.bfcl.related.proxy_runner --mode evolve --manifest _tmp_related_manifest_smoke_1_1.json --rounds 1 --tool-api-style auto --execution-backend official --prompt-style native`
  - Environment included `BFCL_ONLINE_REFACTOR_MAX_PER_ROUND=99` as a negative control.
- Output:
  - `academic/results/_tmp_no_online_refactor_prompt_v3_smoke_1_1_20260517_000558_evolve.json`
- Result:
  - Train task `multi_turn_base_120`: `official_valid=True`, `call_f1=0.8`, `prompt_injected_skills=[]`.
  - Heldout task `multi_turn_base_68`: `official_valid=False`, `call_f1=0.7368`, `prompt_injected_skills=[]`.
  - `online_refactor_attempts=[]` even though the old online-refactor env var was set to `99`.
  - Checkpoint ended with `current_round_state=None` and `next_round_index=1`.
  - Two prior skills were extracted, kept pending during the round, and revoked at round end because no posterior refactor promoted them:
    - `skip_time_check_for_immediate_stock_orders`
    - `skip_symbol_lookup_for_known_tickers`
  - Segment index wrote 3 rows with 3 embeddings using `zhipu_embedding_3`.
  - No occurrences of `tool_error_retry`, `prompt_reinjection`, `Expected tool focus`, `Likely required tool names`, `all_expected_covered_and_extra`, or `expected_tools` were found in result/checkpoint/skills outputs.
- Maintenance token breakdown:
  - extractor: 1 call, 8,222 total tokens.
  - bundle_builder: 2 calls, 10,944 total tokens.
  - refiner: 1 call, 5,376 total tokens.
  - total maintenance LLM usage: 24,542 tokens.

Interpretation: this smoke validates the latest prompt/entrypoint/online-refactor behavior on the real local Claude proxy path. The heldout failure is not evidence of skill pollution in this run because no skills were retrieved or injected for heldout.
