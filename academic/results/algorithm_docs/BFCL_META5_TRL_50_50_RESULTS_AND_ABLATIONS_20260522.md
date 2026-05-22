# BFCL Meta5 TRL 50/50 Results and Ablations, 2026-05-22

## Setup

Common setting for the main successful run and clean ablations:

- Manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`
- Model: `claude-sonnet-4-5`
- LLM config: `local_claude_proxy`
- Backend: `official`
- Prompt/tool style: `native`, `auto`
- Skill injection: `prompt_only`
- `top_k_skills=2`
- `candidate_competition=true`
- `candidate_sample_count=3`
- `macro_maintenance_step=5`
- `micro_maintenance_step=1`
- `BFCL_SKILL_INJECTOR_GATE=deterministic`
- `BFCL_EXTRACTOR_EXISTING_ARTIFACTS=full_store`
- `BFCL_TRL_MIN_EXPOSURE_TASK_RATIO=1.0`
- `BFCL_REFINER_CANDIDATE_COUNT=2`
- `BFCL_TRL_EXTRACTOR_REFINER_REPLAY_PROB=0.5`
- `BFCL_TRL_ROLE_REPLAY_MAX_ROWS=8`
- `MAINTENANCE_ROLE_RULE_USE_JSON=0`

## Code Changes Made During This Run

1. Role feedback prompt hardening in `academic/skill_repository/llm_maintenance.py`:
   - If feedback rows are provided, the role feedback LLM is now explicitly asked to output at least one concrete rule unless the evidence is unusable.
   - Refiner feedback now treats mature low/zero-reuse `refiner_revision` groups as evidence about over-specific or non-actionable refinements.
   - Refactorer feedback now treats mature low-reuse groups as retrieval/scope mismatch evidence, not task-specific shortcuts.

2. Role feedback observability:
   - Empty rule updates now return `empty_update_reason`.
   - Parser warnings and `raw_response_preview` are preserved.
   - `_merge_role_feedback_update(...)` now stores `n_new_rules`, `empty_update_reason`, `parse_warning`, and `raw_response_preview` in role feedback history.

3. Role-scoped low-usage replay:
   - Mature low-usage feedback is enabled by default only for `refactorer` and `refiner_revision` source roles through `BFCL_TRL_LOW_USAGE_FEEDBACK_SOURCE_ROLES`.
   - Extractor low-usage groups remain gated unless explicitly enabled globally.

4. Ablation switches:
   - `BFCL_ENABLE_REFINE=0` disables credit pre-refine, group-maintenance refine, and bundle-test refine.
   - `BFCL_ENABLE_REFACTOR=0` disables macro overlap refactor.

5. Parser cleanup after the main run:
   - The delimiter parser now ignores code fences and JSON wrapper residue such as `}`, ``` and `"delimiter": ...` in the rules section.
   - This was fixed after the main successful run; the main metrics are valid, but its stored `role_feedback.rules` include parser residue.

Tests:

- `pytest -q academic/skill_repository/test_llm_maintenance_feedback.py academic/benchmarks/tests/bfcl_related/test_experiment.py`
- Final result: `77 passed`
- `python -m py_compile academic/skill_repository/llm_maintenance.py academic/benchmarks/bfcl/related/experiment.py`

## Main Results

Primary comparison uses heldout test `official_valid` and `avg_score`.

| run | file | train official | train avg | test official | test avg | test strict | test recall | test precision | test tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| latest baseline | `academic/results/bfcl_baseline_detinj_after_baseline_fixes_20260521_run2.json` | 0.62 | 0.8264 | 0.70 | 0.7993 | 0.12 | 0.8985 | 0.7330 | 81423.4 |
| Meta5 TRL rulesfix | `academic/results/bfcl_trl_refiner_candidates_meta5_rulesfix_50_50_20260522.json` | 0.68 | 0.8266 | 0.76 | 0.8144 | 0.12 | 0.9183 | 0.7425 | 82875.1 |

Conclusion:

- Main Meta5 TRL improves heldout official valid by `+0.06` absolute over latest baseline (`0.76 - 0.70`).
- It also improves heldout avg score by `+0.0151` (`0.8144 - 0.7993`).
- No timeouts or task errors occurred.
- Maintenance LLM calls were balanced: `338 start / 338 done`.

## Ablations

| run | file | test official | test avg | test strict | test recall | test precision | interpretation |
|---|---|---:|---:|---:|---:|---:|---|
| Meta5 TRL full | `academic/results/bfcl_trl_refiner_candidates_meta5_rulesfix_50_50_20260522.json` | 0.76 | 0.8144 | 0.12 | 0.9183 | 0.7425 | best current complete run |
| -refine clean | `academic/results/bfcl_trl_meta5_ablation_no_refine_clean_50_50_20260522.json` | 0.72 | 0.7949 | 0.14 | 0.8793 | 0.7413 | refine contributes about `+0.04` official and `+0.0195` avg score |
| -refactor clean | `academic/results/bfcl_trl_meta5_ablation_no_refactor_50_50_20260522.json` | 0.48 | 0.7461 | 0.06 | 0.8295 | 0.6973 | refactor is essential; removing it collapses performance |

Additional diagnostic:

- `academic/results/bfcl_trl_meta5_ablation_no_refine_50_50_20260522.json` finished with test official `0.74`, avg `0.7988`, but it is not a clean `-refine` ablation because one micro refine path was still active when it was launched. It should not be used in paper tables.

## Log Observations

1. The first Meta5 run without role-scoped low-usage replay finished cleanly but did not beat baseline:
   - File: `academic/results/bfcl_trl_refiner_candidates_meta5_50_50_20260522.json`
   - Test official `0.70`, avg `0.8083`
   - Role feedback LLM was called, but all role rules stayed empty.
   - Replay buffer had `refiner_revision=0`, so refiner had no mature feedback.

2. After role-scoped low-usage replay:
   - Main successful run replay buffer: `extractor=15`, `refiner_revision=21`, `refactorer=0`.
   - Refiner feedback was called, but final refiner rules remained empty. The benefit appears to come from enabling better candidate lifecycle/refinement evidence and extractor-side rule changes rather than stable refiner meta-rules.

3. Group refiner remains expensive:
   - During the second run, `group_refiner` sometimes hit 4096-token completions and retried.
   - This is a cost and stability target, not a correctness blocker.

4. Parser issue:
   - The main successful run's stored extractor rules include code fence / JSON residue due to delimiter parser weakness.
   - The parser is now fixed and tested, but the successful metrics were produced before this cleanup. Treat stored role-rule text from that run as diagnostic, not final clean meta-rule content.

## Interpretation

The current evidence supports the story that the framework works end-to-end:

- TRL with frequent meta feedback and role-scoped refiner_revision replay reaches `0.76` heldout official valid, above the latest deterministic baseline.
- Refactor is the strongest component in the ablation: removing it drops test official valid to `0.48`.
- Refine has a smaller but positive contribution: clean `-refine` drops from `0.76` to `0.72`.
- Meta-rule generation is still not fully clean. It can improve end-to-end metrics, but the role-rule parser/prompt needed cleanup, and refiner/refactorer rulebooks are still weak or empty.

