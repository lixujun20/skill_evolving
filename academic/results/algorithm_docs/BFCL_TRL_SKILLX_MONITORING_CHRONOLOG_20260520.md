# BFCL TRL and SkillX Monitoring Chronolog, 2026-05-20

This document records the BFCL+TRL and SkillX-aligned experiment monitoring process in chronological order. It is intentionally operational: every checkpoint, anomaly, decision, and follow-up action should be preserved here.

## Decision Rules

1. If the BFCL+TRL 50/50 result is worse than the aligned baseline, first tune TRL. The required workflow is:
   - inspect logs and artifacts to identify concrete failure modes;
   - make one thorough implementation/prompt/algorithmic modification;
   - run a small-scale validation to verify the modification is sane;
   - run a full 50/50 experiment after the small-scale validation passes.
2. If SkillX-aligned outperforms our method, do a detailed skill-level comparison:
   - identify which SkillX skills are retrieved/injected on successful cases;
   - compare against our corresponding retrieved/promoted skills;
   - determine whether their extraction, filtering, retrieval, or prompt formatting can be directly borrowed.
3. While either experiment is running, continue monitoring logs and checkpoints instead of stopping.

## 2026-05-20 01:22:05 +0800

Initial status after user instruction to continue until results and comparisons are complete.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Command tag: `bfcl_related50_50_sonnet_trl_20260520`.
- Final result file not yet present:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_summary.json`
- Checkpoint file present:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_checkpoint.json`
- Current checkpoint state:
  - `next_task_index = 13`
  - `n_train_details = 13`
  - `n_window_train_details = 3`
  - `n_window_segments = 11`
  - `n_prefetched_train_details = 7`
  - `n_micro_maintenance_reports = 13`
  - `n_maintenance_windows = 1`
  - `n_store_artifacts = 8`
  - `n_store_test_results = 1`
  - `n_segment_index_rows = 57`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
  - maintenance LLM starts/dones: `174 / 173`; the missing done corresponds to an active `bundle_builder` request at the time of inspection.
- Interpretation: running normally; no final metrics yet.

SkillX-aligned 50/50:
- Process: `PID 547369`, alive.
- Command tag: `skillx_bfcl_50_50_sonnet_hash_embed_20260520`.
- Important caveat: this run uses deterministic hash embeddings because the real Qwen embedding backend was unavailable. This is a diagnostic SkillX-aligned run, not a strict SkillX reproduction.
- Current phase: training rollout export; extraction and test have not started.
- At latest log parse:
  - completed rollout rows: `42 / 50`
  - success: `10 / 42 = 0.2381`
  - official_valid: `19 / 42 = 0.4524`
  - avg_score: `0.7776`
  - avg_call_f1: `0.7966`
  - avg_elapsed_s: `32.099`
  - one task-level API timeout: `multi_turn_base_56`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `NotImplementedError = 0`
  - tool-schema errors = `0`
- Interpretation: training rollout export is healthy except for one recovered API timeout. Skill effectiveness cannot be judged until extraction and test start.

## 2026-05-20 01:23:28 +0800

Periodic monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final result files still absent.
- Checkpoint advanced:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Interpretation: still running normally and has reached 20/50 training tasks.

SkillX-aligned 50/50:
- Process: `PID 547369`, alive.
- Training rollout export reached `50 / 50`.
- Training rollout metrics from log JSON:
  - success: `10 / 50`
  - official_valid: `23 / 50`
  - avg_score: `0.7618`
  - one recovered API timeout remained: `multi_turn_base_56`
- Skill library generated:
  - `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_extraction/skillx_skill_library.json`
- Test has started. First visible `skillx_bfcl_task_run` lines show prompt-injected SkillX skills, including:
  - `flight book with fallback airports`
  - `stock get most recent order id`
  - `stock cancel order`
  - `trading analyze pending order cancellation decision`
- One caveat appeared in the log:
  - `No skills have valid tool schemas for Stage 2`
- Interpretation: this does not stop the run. The current mode is still injecting SkillX skills through prompt guidance (`plan_with_skill`). Treat the Stage 2 schema warning as a caveat to inspect after the run, especially if SkillX underperforms or if functional skill use is expected.

## 2026-05-20 01:25:27 +0800

Periodic monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final result files still absent.
- Checkpoint unchanged from the prior pass on task progress:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log health remains clean:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Interpretation: likely in the second maintenance window; no error signal.

SkillX-aligned 50/50:
- Process: `PID 547369`, alive.
- Test progress from log JSON:
  - completed test rows: `25 / 50`
  - success: `1 / 25`
  - official_valid: `9 / 25`
  - avg_score: `0.7499`
  - errors: `0`
  - prompt-injected rows: `15 / 25`
- Recent prompt-injected skills include:
  - `trading analyze pending order cancellation decision`
  - `stock cancel order`
  - `stock get most recent order id`
  - `flight book with fallback airports`
- Interpretation: SkillX test is running and skill prompts are being injected. The early strict success trend is weak (`1/25`), though avg_score remains in the same broad range as baseline-like BFCL partial scoring. Need final result before comparing.

## 2026-05-20 01:26:59 +0800

Periodic monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final result file still absent.
- Checkpoint unchanged on task index:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log health remains clean:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Interpretation: still likely inside the second maintenance window; no error signal.

SkillX-aligned 50/50:
- Test progress from log JSON:
  - completed test rows: `34 / 50`
  - success: `2 / 34`
  - official_valid: `15 / 34`
  - avg_score: `0.7427`
  - errors: `0`
  - prompt-injected rows: `23 / 34`
- Recent examples:
  - `multi_turn_base_78`: score `1.0`, success `true`, official_valid `true`, no SkillX skill injected.
  - `multi_turn_base_179`: score `0.0`, success `false`, official_valid `false`, injected `flight book with fallback airports`.
- Interpretation: strict success remains weak while many rows are receiving SkillX prompt injection. This needs final comparison and case analysis before concluding whether SkillX helps or hurts.

## 2026-05-20 01:28:47 +0800

Periodic monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final result and skill files still absent.
- Checkpoint unchanged on task index:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
  - `online_refactor_budget_remaining = 0`
- Log health remains clean:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Interpretation: long maintenance window, but no error signature.

SkillX-aligned 50/50:
- Test progress from log JSON:
  - completed test rows: `47 / 50`
  - success: `4 / 47`
  - official_valid: `25 / 47`
  - avg_score: `0.7669`
  - errors: `0`
  - prompt-injected rows: `30 / 47`
- Recent examples:
  - `multi_turn_base_29`: score `1.0`, success `true`, official_valid `true`, no SkillX skill injected.
  - `multi_turn_base_5`: score `0.875`, success `false`, official_valid `true`, injected `trading analyze pending order cancellation decision`.
- Interpretation: SkillX test is almost complete. Strict success is near baseline range but final result is needed.

## 2026-05-20 01:30:06 +0800

SkillX-aligned 50/50 completed.

Output files:
- `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_test_partial.json`
- `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_test_result.json`

Final SkillX-aligned test summary:
- `n_train_reserved = 50`
- `n_tasks = 50`
- `n_runs = 50`
- `total_success = 4`
- `success_rate = 0.08`
- `official_valid_rate = 0.54`
- `avg_score = 0.7679`
- `avg_call_recall = 0.8508`
- `avg_call_precision = 0.7140`
- `avg_turn_success_rate = 0.5204`
- `avg_relaxed_turn_success_rate = 0.7763`
- `avg_total_tokens = 78787.6`
- `avg_input_tokens = 77726.6`
- `avg_output_tokens = 1061.0`
- `avg_elapsed_s = 34.927`
- `max_elapsed_s = 65.448`
- `avg_model_steps = 9.78`
- `max_model_steps = 16`
- `timeout_rate = 0.0`
- Test rows with SkillX prompt injection: `31 / 50`
- Test errors: `0`

Observed SkillX caveats:
- The run used deterministic hash embeddings, not Qwen embeddings, so it remains a diagnostic SkillX-aligned run rather than a strict SkillX reproduction.
- The log printed `No skills have valid tool schemas for Stage 2` once. Despite this, prompt-based SkillX injection worked and appeared in `31 / 50` test rows.

Immediate comparison against known BFCL references:
- Aligned baseline rerun (`bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json`):
  - strict success: baseline `0.08`, SkillX-aligned `0.08`, tie.
  - official_valid: baseline `0.48`, SkillX-aligned `0.54`, SkillX-aligned higher by `+0.06`.
  - avg_score: baseline `0.7388`, SkillX-aligned `0.7679`, SkillX-aligned higher by `+0.0291`.
  - avg_call_recall: baseline `0.8181`, SkillX-aligned `0.8508`, SkillX-aligned higher by `+0.0327`.
  - avg_call_precision: baseline `0.7023`, SkillX-aligned `0.7140`, SkillX-aligned higher by `+0.0117`.
- Our BFCL+TRL 20/50 warm-up (`bfcl_related20_50_sonnet_trl_20260520_evolve.json`):
  - strict success: warm-up `0.06`, SkillX-aligned `0.08`, SkillX-aligned higher by `+0.02`.
  - official_valid: warm-up `0.62`, SkillX-aligned `0.54`, warm-up higher by `+0.08`.
  - avg_score: warm-up `0.7710`, SkillX-aligned `0.7679`, warm-up higher by `+0.0031`.
  - avg_call_recall: warm-up `0.8595`, SkillX-aligned `0.8508`, warm-up higher by `+0.0087`.
  - avg_call_precision: warm-up `0.7234`, SkillX-aligned `0.7140`, warm-up higher by `+0.0094`.

Interpretation:
- SkillX-aligned does not beat the aligned baseline on strict success, but improves partial quality metrics.
- SkillX-aligned beats our 20/50 warm-up on strict success but is slightly worse on official_valid, avg_score, recall, and precision.
- The user-requested SkillX-vs-our detailed skill comparison should wait for the BFCL+TRL 50/50 final result, because the relevant primary comparison is against the full 50/50 TRL run now in progress.

BFCL+TRL 50/50 status at the same time:
- Process: `PID 639018`, alive.
- Final result still absent.
- Checkpoint:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Interpretation: still running; continue monitoring.

## 2026-05-20 01:30:45 +0800

Additional BFCL+TRL health inspection because checkpoint had not advanced since `next_task_index = 20`.

Observed:
- Log file updated at `2026-05-20 01:30:45 +0800`, so the run is not stalled.
- Checkpoint file last updated at `2026-05-20 01:21:03 +0800`, which means the current maintenance window has not yet checkpointed its final state.
- Process state:
  - `PID 639018`
  - state `Sl`
  - wait channel `ep_poll`
- Log tail shows active maintenance calls, mostly:
  - `extractor`
  - `credit_assigner`
  - `refiner`
  - `skill_injector`
- The latest observed event is a `maintenance_llm_start` for `skill_injector`.
- No error signatures were present.

Interpretation:
- The run is actively executing LLM-backed maintenance and skill injection requests inside the second maintenance window.
- The unchanged checkpoint is expected until that window finishes and flushes state.
- Continue monitoring.

## 2026-05-20 01:32:45 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Process state: `Sl`, wait channel `ep_poll`.
- Final output files still absent:
  - evolve result absent
  - summary absent
  - final skills absent
- Checkpoint still reports:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log progress counters:
  - `bfcl_task_run = 30`
  - `maintenance_llm_start = 385`
  - `maintenance_llm_done = 384`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Latest event: active `skill_injector` request.

Interpretation:
- The log has advanced to 30 task rollout rows even though the checkpoint still reports 20. This means the next window is active and has not flushed its checkpoint yet.
- No anomaly requiring intervention.

## 2026-05-20 01:35:18 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Process state: `Sl`, wait channel `ep_poll`.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log progress counters:
  - `bfcl_task_run = 30`
  - `maintenance_llm_start = 407`
  - `maintenance_llm_done = 407`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows:
  - task rows: `30`
  - success: `9 / 30`
  - official_valid: `22 / 30`
  - avg_score: `0.8663`
  - task errors: `0`
- Recent prompt-injected skills include vehicle and trading candidates:
  - `vehicle_fuel_check_unnecessary_before_fill_with_explicit_amount__candidate_r0_t19_s2`
  - `vehicle_engine_start_requires_locked_doors__candidate_r0_t17_s0`
  - `trading_bot_direct_symbol_binding_for_known_companies`
  - `vehicle_tire_shop_navigation_workflow__candidate_r0_t17_s0`

Interpretation:
- The current train-window rollout rows look healthy and show skill prompt injection.
- These are not final test metrics.
- Checkpoint is still stale relative to the log; continue monitoring.

## 2026-05-20 01:38:22 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Process state: `Sl`, wait channel `ep_poll`.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log progress counters:
  - `bfcl_task_run = 30`
  - `maintenance_llm_start = 429`
  - `maintenance_llm_done = 429`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `30`
  - success: `9 / 30`
  - official_valid: `22 / 30`
  - avg_score: `0.8663`
  - task errors: `0`
- Recent task rows include many prompt-injected skills:
  - `multi_turn_base_99`: score `0.8421`, official_valid `true`, `4` prompt-injected skills.
  - `multi_turn_base_92`: score `0.8`, official_valid `true`, `5` prompt-injected skills.
  - `multi_turn_base_87`: score `0.9412`, official_valid `false`, `5` prompt-injected skills.
  - `multi_turn_base_71`: score `0.8`, official_valid `true`, `3` prompt-injected skills.

Interpretation:
- The process is still active and LLM maintenance calls are completing.
- The lack of checkpoint advancement is still consistent with an unflushed maintenance/evaluation window.
- Continue monitoring.

## 2026-05-20 01:42:04 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Process state: `Sl`, wait channel `ep_poll`.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log progress counters:
  - `bfcl_task_run = 30`
  - `maintenance_llm_start = 457`
  - `maintenance_llm_done = 456`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `30`
  - success: `9 / 30`
  - official_valid: `22 / 30`
  - avg_score: `0.8663`
  - task errors: `0`
- Latest event: active `skill_injector` request.

Interpretation:
- The run is still making progress at the maintenance-call level.
- The long duration appears dominated by many `skill_injector` calls in the current window. If final TRL underperforms or is too expensive, this is a candidate cause to inspect and optimize.

## 2026-05-20 01:45:38 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Process state: `Sl`, wait channel `ep_poll`.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log progress counters:
  - `bfcl_task_run = 30`
  - `maintenance_llm_start = 485`
  - `maintenance_llm_done = 484`
- Role-level maintenance counts:
  - `extractor`: `90 / 90`
  - `refactorer`: `7 / 7`
  - `bundle_builder`: `6 / 6`
  - `skill_injector`: `354 / 353`
  - `credit_assigner`: `13 / 13`
  - `refiner`: `14 / 14`
  - `extractor_feedback`: `1 / 1`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `30`
  - success: `9 / 30`
  - official_valid: `22 / 30`
  - avg_score: `0.8663`
  - task errors: `0`

Interpretation:
- The current window is dominated by `skill_injector` calls. This is a concrete cost/latency concern.
- Do not interrupt yet because the process is still making clean progress, but if the final TRL result is worse than baseline or too expensive, the first tuning target should be reducing repeated skill-injector calls and/or caching equivalent injector decisions.

## 2026-05-20 01:46:xx +0800

Side investigation while BFCL+TRL continued running.

Question:
- Is the high `skill_injector` call count a stuck loop, or an expensive but intended candidate-evaluation path?

Observed from current checkpoint sidecars:
- Store artifacts: `33`
- Store test results: `3`
- Sidecar details:
  - `details = 20`
  - `window_train_details = 0`
  - `window_segments = 0`
  - `micro_maintenance_reports = 20`
  - `maintenance_windows = 2`
  - `extraction_events = 32`
  - `credit_events = 4`
  - `prefetched_train_details = 0`
  - `candidate_group_feedback_state = 8`
- Many artifacts are candidate-competition samples with names like:
  - `verify_balance_before_withdrawal__candidate_r0_t4_s0`
  - `verify_balance_before_withdrawal__candidate_r0_t4_s1`
  - `verify_balance_before_withdrawal__candidate_r0_t4_s2`
  - `trading_bot_reuse_order_id_for_cancellation__candidate_r0_t10_s0`
  - `vehicle_engine_start_requires_locked_doors__candidate_r0_t17_s0`
  - `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t17_s0`
  - `vehicle_tire_shop_navigation_workflow__candidate_r0_t17_s0`
- Artifact statuses include both `active` and `trial`.
- Maintenance role counts at inspection:
  - `skill_injector` starts: `359`
  - unique `(user_chars, system_chars)` prompt shapes: `231`
- The top repeated prompt-shape count was only `9`, so this does not look like a single identical prompt being retried endlessly.

Interpretation:
- The high cost appears to come from evaluating many distinct skill-injection decisions over many candidate/trial skills, not from a single obvious retry loop.
- This is still likely a TRL efficiency problem: candidate competition plus per-candidate LLM-based injection selection can scale poorly once the store grows.
- Do not patch the active run. If final TRL underperforms or is too expensive, likely tuning directions are:
  - cache injector decisions for identical or near-identical `(task/query, candidate skill)` contexts;
  - replace LLM injector with cheaper deterministic prefilter before LLM reranking;
  - limit trial candidates per group before injector calls;
  - batch multiple candidate decisions into one injector call per task instead of one or many small calls.

## 2026-05-20 01:50:55 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Process state: `Sl`, wait channel `ep_poll`.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log progress counters:
  - `bfcl_task_run = 30`
  - `maintenance_llm_start = 549`
  - `maintenance_llm_done = 547`
- `skill_injector` role:
  - starts: `408`
  - dones: `408`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Latest events show the run has moved from mostly `skill_injector` into `refiner` calls:
  - active `refiner` requests are now visible.

Interpretation:
- The expensive skill-injector phase appears to have completed for this window.
- The run remains healthy but costly.
- Continue monitoring for checkpoint flush or final output.

## 2026-05-20 01:54:29 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Process state: `Sl`, wait channel `ep_poll`.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 33`
  - `n_store_test_results = 3`
  - `n_segment_index_rows = 80`
- Log progress counters:
  - `bfcl_task_run = 30`
  - `maintenance_llm_start = 611`
  - `maintenance_llm_done = 610`
- Role-level maintenance start counts:
  - `extractor = 90`
  - `refactorer = 7`
  - `bundle_builder = 6`
  - `skill_injector = 466`
  - `credit_assigner = 13`
  - `refiner = 27`
  - `extractor_feedback = 1`
  - `stale_resolver = 1`
- Role-level maintenance done counts:
  - same as starts except `skill_injector = 465`, with one active request.
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `30`
  - success: `9 / 30`
  - official_valid: `22 / 30`
  - avg_score: `0.8663`
  - task errors: `0`

Interpretation:
- `skill_injector` count increased again after refiner/stale-resolver activity, so the current maintenance window is repeatedly returning to injection evaluation.
- This is still not a crash, but it is now a clear efficiency issue. It should be treated as a primary TRL tuning target if the final result is not decisively better than baseline.

## 2026-05-20 01:59:06 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final output files still absent.
- Checkpoint finally advanced:
  - `next_task_index = 30`
  - `n_train_details = 30`
  - `n_micro_maintenance_reports = 30`
  - `n_maintenance_windows = 3`
  - `n_store_artifacts = 81`
  - `n_store_test_results = 26`
  - `n_segment_index_rows = 112`
- Log progress counters:
  - `bfcl_task_run = 39`
  - `maintenance_llm_start = 692`
  - `maintenance_llm_done = 692`
- Role-level maintenance counts:
  - `extractor = 90 / 90`
  - `refactorer = 8 / 8`
  - `bundle_builder = 7 / 7`
  - `skill_injector = 540 / 540`
  - `credit_assigner = 13 / 13`
  - `refiner = 31 / 31`
  - `extractor_feedback = 2 / 2`
  - `stale_resolver = 1 / 1`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows:
  - task rows: `39`
  - success: `12 / 39`
  - official_valid: `28 / 39`
  - avg_score: `0.8696`
  - task errors: `0`
- Recent rows:
  - `multi_turn_base_79`: score `0.9091`, official_valid `true`, injected `press_brake_before_starting_engine__candidate_r0_t18_s1`.
  - `multi_turn_base_89`: score `0.875`, official_valid `true`, injected three vehicle skills.
  - `multi_turn_base_56`: score `1.0`, success `true`, official_valid `true`, injected two vehicle engine-start skills.

Interpretation:
- The long maintenance window completed and checkpointed.
- Store size increased sharply from `33` to `81`, which explains the growth in subsequent injector workload.
- No error signal. Continue monitoring toward final 50/50 result.

## 2026-05-20 02:03:46 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 30`
  - `n_train_details = 30`
  - `n_micro_maintenance_reports = 30`
  - `n_maintenance_windows = 3`
  - `n_store_artifacts = 81`
  - `n_store_test_results = 26`
  - `n_segment_index_rows = 112`
- Log progress counters:
  - `bfcl_task_run = 40`
  - `maintenance_llm_start = 739`
  - `maintenance_llm_done = 738`
- `skill_injector` role:
  - starts: `546`
  - dones: `545`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows:
  - task rows: `40`
  - success: `12 / 40`
  - official_valid: `29 / 40`
  - avg_score: `0.8671`
  - task errors: `0`
- Recent rows:
  - `multi_turn_base_50`: score `1.0`, success `true`, no injected skills.
  - `multi_turn_base_56`: score `1.0`, success `true`, injected two vehicle engine-start skills.
  - `multi_turn_base_165`: score `0.7692`, official_valid `true`, no injected skills.

Interpretation:
- The run is in the next window and should checkpoint at or after `40 / 50`.
- It remains healthy, but skill-injector volume continues to increase.

## 2026-05-20 02:09:22 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 30`
  - `n_train_details = 30`
  - `n_micro_maintenance_reports = 30`
  - `n_maintenance_windows = 3`
  - `n_store_artifacts = 81`
  - `n_store_test_results = 26`
  - `n_segment_index_rows = 112`
- Log progress counters:
  - `bfcl_task_run = 40`
  - `maintenance_llm_start = 782`
  - `maintenance_llm_done = 782`
- Role-level starts/dones:
  - `extractor = 120 / 120`
  - `refactorer = 8 / 8`
  - `bundle_builder = 7 / 7`
  - `skill_injector = 585 / 585`
  - `credit_assigner = 21 / 21`
  - `refiner = 36 / 36`
  - `extractor_feedback = 2 / 2`
  - `stale_resolver = 3 / 3`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `40`
  - success: `12 / 40`
  - official_valid: `29 / 40`
  - avg_score: `0.8671`
  - task errors: `0`

Interpretation:
- All observed maintenance requests are completed at this moment, but the checkpoint has not yet flushed to `40 / 50`.
- Continue waiting for flush/finalization.

## 2026-05-20 02:15:02 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 30`
  - `n_train_details = 30`
  - `n_micro_maintenance_reports = 30`
  - `n_maintenance_windows = 3`
  - `n_store_artifacts = 81`
  - `n_store_test_results = 26`
  - `n_segment_index_rows = 112`
- Log progress counters:
  - `bfcl_task_run = 40`
  - `maintenance_llm_start = 827`
  - `maintenance_llm_done = 826`
- Role-level starts/dones:
  - `extractor = 120 / 120`
  - `refactorer = 8 / 8`
  - `bundle_builder = 7 / 7`
  - `skill_injector = 620 / 619`
  - `credit_assigner = 21 / 21`
  - `refiner = 41 / 41`
  - `extractor_feedback = 2 / 2`
  - `stale_resolver = 8 / 8`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `40`
  - success: `12 / 40`
  - official_valid: `29 / 40`
  - avg_score: `0.8671`
  - task errors: `0`

Interpretation:
- Still healthy but expensive.
- `stale_resolver` and `skill_injector` counts continue increasing after the 30/50 checkpoint.
- Continue monitoring.

## 2026-05-20 02:21:44 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 30`
  - `n_train_details = 30`
  - `n_micro_maintenance_reports = 30`
  - `n_maintenance_windows = 3`
  - `n_store_artifacts = 81`
  - `n_store_test_results = 26`
  - `n_segment_index_rows = 112`
- Log progress counters:
  - `bfcl_task_run = 40`
  - `maintenance_llm_start = 882`
  - `maintenance_llm_done = 882`
- Role-level starts/dones:
  - `extractor = 120 / 120`
  - `refactorer = 8 / 8`
  - `bundle_builder = 7 / 7`
  - `skill_injector = 671 / 671`
  - `credit_assigner = 21 / 21`
  - `refiner = 41 / 41`
  - `extractor_feedback = 2 / 2`
  - `stale_resolver = 12 / 12`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `40`
  - success: `12 / 40`
  - official_valid: `29 / 40`
  - avg_score: `0.8671`
  - task errors: `0`

Interpretation:
- The run is still healthy, but the post-30/50 maintenance window remains long.
- All observed requests are complete at this snapshot. Continue waiting for the next checkpoint flush.

## 2026-05-20 02:29:21 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final output files still absent.
- Checkpoint still reports:
  - `next_task_index = 30`
  - `n_train_details = 30`
  - `n_micro_maintenance_reports = 30`
  - `n_maintenance_windows = 3`
  - `n_store_artifacts = 81`
  - `n_store_test_results = 26`
  - `n_segment_index_rows = 112`
- Log progress counters:
  - `bfcl_task_run = 40`
  - `maintenance_llm_start = 921`
  - `maintenance_llm_done = 920`
- Role-level starts/dones:
  - `extractor = 120 / 120`
  - `refactorer = 13 / 13`
  - `bundle_builder = 12 / 11`
  - `skill_injector = 698 / 698`
  - `credit_assigner = 21 / 21`
  - `refiner = 41 / 41`
  - `extractor_feedback = 2 / 2`
  - `stale_resolver = 14 / 14`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows remains:
  - task rows: `40`
  - success: `12 / 40`
  - official_valid: `29 / 40`
  - avg_score: `0.8671`
  - task errors: `0`
- Latest events:
  - several `skill_injector` calls completed;
  - one `refactorer` call completed;
  - one `bundle_builder` call is active.

Interpretation:
- The run is still in macro/refactor/bundle maintenance after the 30/50 checkpoint and before the 40/50 flush.
- No error, but maintenance cost is now very high.

## 2026-05-20 02:37:02 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final output files still absent.
- Checkpoint advanced:
  - `next_task_index = 40`
  - `n_train_details = 40`
  - `n_micro_maintenance_reports = 40`
  - `n_maintenance_windows = 4`
  - `n_store_artifacts = 107`
  - `n_store_test_results = 41`
  - `n_segment_index_rows = 141`
- Log progress counters:
  - `bfcl_task_run = 50`
  - `maintenance_llm_start = 1053`
  - `maintenance_llm_done = 1049`
- Role-level starts/dones:
  - `extractor = 132 / 128`
  - `refactorer = 13 / 13`
  - `bundle_builder = 12 / 12`
  - `skill_injector = 813 / 813`
  - `credit_assigner = 25 / 25`
  - `refiner = 41 / 41`
  - `extractor_feedback = 3 / 3`
  - `stale_resolver = 14 / 14`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current task-run aggregate from log rows:
  - task rows: `50`
  - success: `12 / 50`
  - official_valid: `35 / 50`
  - avg_score: `0.8324`
  - task errors: `0`
- Recent final train rows:
  - `multi_turn_base_181`: score `0.7143`, official_valid `false`, one injected skill.
  - `multi_turn_base_156`: score `0.6667`, official_valid `true`, one injected skill.
  - `multi_turn_base_180`: score `0.8235`, official_valid `true`, one injected skill.
  - `multi_turn_base_193`: score `0.8889`, official_valid `true`, no injected skills.

Interpretation:
- All 50 training task rows appear in the log.
- The run is now in the final maintenance/finalization phase after the 40/50 checkpoint.
- Final result still pending.

## 2026-05-20 02:44:41 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final skills file is now present:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_skills.json`
  - size: `3651174` bytes
- Final evolve and summary files are still absent:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_summary.json`
- Checkpoint file is now much larger:
  - size: `264788793` bytes
- `current_round_state` no longer exposes the previous progress fields, which suggests the round-level checkpoint has been finalized into a different/full structure.
- Log progress counters:
  - `bfcl_task_run = 55`
  - `maintenance_llm_start = 1146`
  - `maintenance_llm_done = 1144`
- Role-level starts/dones:
  - `extractor = 150 / 150`
  - `refactorer = 14 / 14`
  - `bundle_builder = 13 / 13`
  - `skill_injector = 873 / 871`
  - `credit_assigner = 28 / 28`
  - `refiner = 48 / 48`
  - `extractor_feedback = 5 / 5`
  - `stale_resolver = 15 / 15`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`

Interpretation:
- Training/maintenance has likely reached a late finalization stage because final skills are written.
- The final evolve/summary result is not yet written, so the process is still not complete.
- Continue monitoring.

## 2026-05-20 02:50:16 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final skills file remains present.
- Final evolve and summary files still absent.
- Log progress counters:
  - `bfcl_task_run = 69`
  - `maintenance_llm_start = 1291`
  - `maintenance_llm_done = 1289`
- Role-level starts/dones:
  - `extractor = 150 / 150`
  - `refactorer = 14 / 14`
  - `bundle_builder = 13 / 13`
  - `skill_injector = 1018 / 1016`
  - `credit_assigner = 28 / 28`
  - `refiner = 48 / 48`
  - `extractor_feedback = 5 / 5`
  - `stale_resolver = 15 / 15`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current all-task log aggregate:
  - task rows: `69`
  - success: `14 / 69`
  - official_valid: `40 / 69`
  - avg_score: `0.7992`
  - task errors: `0`

Interpretation:
- After final skills were written, the run appears to have moved into final test evaluation. The task log count increased beyond 50, and the latest task id is in the heldout/test range.
- Continue monitoring until final evolve/summary files are written.

## 2026-05-20 02:57:51 +0800

Periodic BFCL+TRL monitoring pass.

BFCL+TRL 50/50:
- Process: `PID 639018`, alive.
- Final skills file remains present.
- Final evolve and summary files still absent.
- Log progress counters:
  - `bfcl_task_run = 90`
  - `maintenance_llm_start = 1466`
  - `maintenance_llm_done = 1466`
- Role-level starts/dones:
  - `extractor = 150 / 150`
  - `refactorer = 14 / 14`
  - `bundle_builder = 13 / 13`
  - `skill_injector = 1193 / 1193`
  - `credit_assigner = 28 / 28`
  - `refiner = 48 / 48`
  - `extractor_feedback = 5 / 5`
  - `stale_resolver = 15 / 15`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Current all-task log aggregate:
  - task rows: `90`
  - success: `15 / 90`
  - official_valid: `54 / 90`
  - avg_score: `0.7910`
  - task errors: `0`
- Since the first 50 `bfcl_task_run` rows correspond to training, this suggests final test is around `40 / 50` complete.
- Recent heldout/test rows:
  - `multi_turn_base_10`: score `0.6957`, official_valid `false`, no injected skills.
  - `multi_turn_base_137`: score `0.7273`, official_valid `true`, no injected skills.
  - `multi_turn_base_191`: score `1.0`, success `true`, official_valid `true`, two prompt-injected skills.

Interpretation:
- Final test is underway and near completion.
- Early strict test trend does not look clearly better than baseline, but final metrics are required before acting.

## 2026-05-20 03:02:34 +0800

BFCL+TRL 50/50 completed.

Output files:
- Final evolve result:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`
  - size: `433742536` bytes
- Final skills:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_skills.json`
  - size: `3651174` bytes
- Checkpoint:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_checkpoint.json`
  - size: `264788793` bytes
- Summary sidecar was not written:
  - `academic/results/bfcl_related50_50_sonnet_trl_20260520_summary.json` absent
  - The evolve JSON contains `train_summary` and `test_summary`, so metrics are available.

Final BFCL+TRL 50/50 train summary:
- `n_train_reserved = 50`
- `n_tasks = 50`
- `n_runs = 50`
- `total_success = 12`
- `success_rate = 0.24`
- `official_valid_rate = 0.70`
- `avg_score = 0.8324`
- `avg_call_recall = 0.8979`
- `avg_call_precision = 0.7939`
- `avg_turn_success_rate = 0.6073`
- `avg_relaxed_turn_success_rate = 0.8210`
- `avg_total_tokens = 61572.7`
- `avg_elapsed_s = 65.506`
- `avg_model_steps = 9.28`
- `timeout_rate = 0.0`

Final BFCL+TRL 50/50 test summary:
- `n_train_reserved = 50`
- `n_tasks = 50`
- `n_runs = 50`
- `total_success = 4`
- `success_rate = 0.08`
- `official_valid_rate = 0.52`
- `avg_score = 0.7474`
- `avg_call_recall = 0.8491`
- `avg_call_precision = 0.6850`
- `avg_turn_success_rate = 0.4768`
- `avg_relaxed_turn_success_rate = 0.7710`
- `avg_total_tokens = 76689.5`
- `avg_input_tokens = 75624.8`
- `avg_output_tokens = 1064.7`
- `avg_elapsed_s = 79.253`
- `max_elapsed_s = 161.080`
- `avg_model_steps = 10.08`
- `max_model_steps = 17`
- `timeout_rate = 0.0`
- Prompt-injected test rows from log parse: `32 / 50`
- Test errors from log parse: `0`

Maintenance cost:
- Total maintenance LLM calls: `1470`
- Total maintenance tokens: `3307835`
- `skill_injector` calls: `1197`
- `skill_injector` tokens: `1338717`
- `skill_injector` duration: `5688029 ms`
- The skill-injector was the largest maintenance-call source by count and total duration.

Comparison against aligned baseline rerun:
- Baseline file: `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json`
- Strict success:
  - baseline `0.08`
  - BFCL+TRL `0.08`
  - result: tie, no strict win.
- Official valid:
  - baseline `0.48`
  - BFCL+TRL `0.52`
  - result: BFCL+TRL `+0.04`.
- Avg score:
  - baseline `0.7388`
  - BFCL+TRL `0.7474`
  - result: BFCL+TRL `+0.0086`.
- Avg call recall:
  - baseline `0.8181`
  - BFCL+TRL `0.8491`
  - result: BFCL+TRL `+0.0310`.
- Avg call precision:
  - baseline `0.7023`
  - BFCL+TRL `0.6850`
  - result: BFCL+TRL `-0.0173`.
- Avg total tokens:
  - baseline `70551.3`
  - BFCL+TRL `76689.5`
  - result: BFCL+TRL `+6138.2`.
- Avg elapsed seconds:
  - baseline `35.555`
  - BFCL+TRL `79.253`
  - result: BFCL+TRL much slower.

Interpretation against user rule:
- BFCL+TRL did not beat baseline on strict success. It improved partial metrics but at higher time/token cost and lower precision.
- Treat this as not good enough. Start TRL tuning before running another full 50/50.

Comparison against SkillX-aligned diagnostic:
- SkillX-aligned file: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_test_result.json`
- Strict success:
  - SkillX-aligned `0.08`
  - BFCL+TRL `0.08`
  - result: tie.
- Official valid:
  - SkillX-aligned `0.54`
  - BFCL+TRL `0.52`
  - result: SkillX-aligned `+0.02`.
- Avg score:
  - SkillX-aligned `0.7679`
  - BFCL+TRL `0.7474`
  - result: SkillX-aligned `+0.0205`.
- Avg call recall:
  - SkillX-aligned `0.8508`
  - BFCL+TRL `0.8491`
  - result: nearly tied, SkillX-aligned `+0.0017`.
- Avg call precision:
  - SkillX-aligned `0.7140`
  - BFCL+TRL `0.6850`
  - result: SkillX-aligned `+0.0290`.
- Avg total tokens:
  - SkillX-aligned `78787.6`
  - BFCL+TRL `76689.5`
  - result: BFCL+TRL lower by `2098.1`.
- Avg elapsed seconds:
  - SkillX-aligned `34.927`
  - BFCL+TRL `79.253`
  - result: SkillX-aligned much faster.

Interpretation against user rule:
- SkillX-aligned is better than BFCL+TRL on avg_score, official_valid, precision, and speed, while strict success ties.
- After TRL tuning begins, also perform skill-level comparison to identify what can be borrowed from SkillX.

## 2026-05-20 03:0x +0800

TRL tuning started.

Primary diagnosis:
- BFCL+TRL did not improve strict success over baseline.
- It improved official_valid and avg_score slightly but degraded precision and was much slower.
- The largest concrete cost source was executor skill injection:
  - `skill_injector` maintenance calls in full 50/50: `1197`
  - `skill_injector` maintenance tokens: `1338717`
  - `skill_injector` maintenance duration: `5688029 ms`
- Code inspection found the executor did dynamic step-level retrieval/injection on every step after step 0 whenever an artifact store existed.
- When there was no prior actionable tool error, this reuses essentially the same turn query, causing repeated LLM injector decisions for the same turn and store.
- When retrieved skills were already present in the prompt context, the code still called the LLM injector and only deduplicated afterward.

Code modification:
- File: `academic/benchmarks/bfcl/adapter.py`
- In `_SkillInjectionPolicy.merge_prompt_skills`, added a prefilter:
  - compute `existing = {skill.name for skill in current}`;
  - compute `new_prompt_skills = [skill for skill in prompt_skills if skill.name not in existing]`;
  - if no new prompt skills exist, return immediately with an event whose fields include:
    - `"mode": f"skip:{self.presentation_mode}"`
    - `"phase": "executor_step_update"`
    - `"gate": "no_new_prompt_skills"`
    - filtered reason `"already_in_prompt_context"`
  - call `select_skill_context_with_llm` only on `new_prompt_skills`.
- In the executor step loop, changed step-level dynamic retrieval/injection:
  - before running retrieval on `step > 0`, check `last_observation`;
  - if `last_observation is None or not last_observation.is_actionable_error()`, emit `dynamic_retrieval_skipped` with reason `"no_actionable_previous_tool_error"`;
  - only run retrieval plus LLM injector when the previous tool observation is an actionable error.

Rationale:
- Keep initial per-turn skill retrieval/injection unchanged.
- Keep reactive recovery retrieval for schema/state/not-found/invalid-argument errors.
- Remove repeated normal-path step-level injector calls, which are expensive and likely add noisy skills.

Verification so far:
- `python -m py_compile academic/benchmarks/bfcl/adapter.py` passed.
- `python -m pytest academic/benchmarks/bfcl -q` returned exit code `5` because no tests were collected in that directory; no test failures were reported.

Small validation run started:
- PID: `2020751`
- Tag: `bfcl_trl_injector_gate_smoke4_3_20260520`
- Manifest: `academic/experiments/bfcl_case_lists/_tmp_related_manifest_speed_eval_4_3.json`
- Train/test: `4 / 3`
- Purpose:
  - verify the modified executor path runs end-to-end;
  - check that step-level injector calls drop;
  - confirm no traceback/timeout/API errors.
- First monitoring pass:
  - process alive;
  - no final output yet;
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
  - `bfcl_task_run = 4`
  - extractor calls started/done: `6 / 4`
  - first four train rows completed with no prompt-injected skills, as expected before skills are available.

## 2026-05-20 03:11:15 +0800

Small validation smoke run completed.

Output files:
- `academic/results/bfcl_trl_injector_gate_smoke4_3_20260520_evolve.json`
- `academic/results/bfcl_trl_injector_gate_smoke4_3_20260520_skills.json`
- `academic/results/bfcl_trl_injector_gate_smoke4_3_20260520_checkpoint.json`

Run health:
- Process exited normally.
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 0`
- `maintenance_llm_error = 0`

Smoke metrics:
- Train:
  - `success_rate = 0.5`
  - `official_valid_rate = 1.0`
  - `avg_score = 0.8961`
  - `avg_total_tokens = 56732.0`
  - `avg_elapsed_s = 41.522`
- Test:
  - `success_rate = 0.0`
  - `official_valid_rate = 0.0`
  - `avg_score = 0.7843`
  - `avg_total_tokens = 89804.3`
  - `avg_elapsed_s = 36.622`
  - `avg_call_precision = 0.7677`
  - `avg_call_recall = 0.8250`

Injector cost after patch in smoke:
- `skill_injector` calls: `5`
- `skill_injector` total tokens: `4541`
- `skill_injector` duration: `20750 ms`

Interpretation:
- The patched executor path runs end-to-end.
- The run is too small and produced no prompt-injected test skills, so it validates stability and reduced injector cost, not final effectiveness.
- Proceed to a 20/50 validation run before another 50/50.

## 2026-05-20 03:1x +0800

Started 20/50 validation after injector-gating patch.

Command characteristics:
- PID: `2067993`
- Tag: `bfcl_related20_50_sonnet_trl_injector_gate_20260520`
- Manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_50.json`
- Train/test: `20 / 50`
- Shared settings:
  - model: `claude-sonnet-4-5`
  - execution backend: `official`
  - prompt style: `native`
  - max steps per turn: `20`
  - max task seconds: `180`
  - temperature: `0.0`
  - candidate competition enabled
  - candidate sample count: `3`
  - train window concurrency: `4`
  - test concurrency: `4`
  - micro concurrency: `4`
- Output:
  - `academic/results/bfcl_related20_50_sonnet_trl_injector_gate_20260520_evolve.json`
  - `academic/results/bfcl_related20_50_sonnet_trl_injector_gate_20260520_skills.json`
  - `academic/results/bfcl_related20_50_sonnet_trl_injector_gate_20260520_checkpoint.json`
  - log: `academic/results/logs/bfcl_related20_50_sonnet_trl_injector_gate_20260520.log`

Decision target:
- If this run preserves or improves the prior 20/50 warm-up quality while substantially reducing `skill_injector` calls/time, run a new full 50/50.
- If strict or partial quality drops badly, inspect cases before running 50/50.

## 2026-05-20 03:13:44 +0800

First monitoring pass for 20/50 injector-gate validation.

Status:
- PID `2067993` alive.
- Final output/checkpoint not yet present.
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Progress:
  - `bfcl_task_run = 10`
  - `maintenance_llm_start = 26`
  - `maintenance_llm_done = 22`
  - all maintenance calls so far are `extractor`
- First 10 train rows:
  - success: `4 / 10`
  - official_valid: `9 / 10`
  - avg_score: `0.9308`
  - errors: `0`
  - prompt-injected skills: `0` so far, expected because early train rows run before useful skills exist.

Interpretation:
- Run is healthy.
- Continue monitoring.

## 2026-05-20 03:18:14 +0800

Second monitoring pass for 20/50 injector-gate validation.

Status:
- PID `2067993` alive.
- First checkpoint present.
- Checkpoint:
  - `next_task_index = 10`
  - `n_train_details = 10`
  - `n_micro_maintenance_reports = 10`
  - `n_maintenance_windows = 1`
  - `n_store_artifacts = 5`
  - `n_store_test_results = 1`
  - `n_segment_index_rows = 46`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Progress:
  - `bfcl_task_run = 10`
  - `maintenance_llm_start = 55`
  - `maintenance_llm_done = 55`
- Role-level counts:
  - `extractor = 30 / 30`
  - `refactorer = 3 / 3`
  - `bundle_builder = 2 / 2`
  - `skill_injector = 20 / 20`
- First 10 train rows:
  - success: `4 / 10`
  - official_valid: `9 / 10`
  - avg_score: `0.9308`
  - errors: `0`
  - prompt-injected rows: `0`

Interpretation:
- First window completed cleanly.
- `skill_injector` volume is modest (`20` calls at 10 train rows), and store has only 5 artifacts.
- Continue monitoring.

## 2026-05-20 03:24:53 +0800

Third monitoring pass for 20/50 injector-gate validation.

Status:
- PID `2067993` alive.
- Training reached 20/20 and checkpointed.
- Final evolve file not yet present.
- Checkpoint:
  - `next_task_index = 20`
  - `n_train_details = 20`
  - `n_micro_maintenance_reports = 20`
  - `n_maintenance_windows = 2`
  - `n_store_artifacts = 28`
  - `n_store_test_results = 6`
  - `n_segment_index_rows = 80`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Progress:
  - `bfcl_task_run = 20`
  - `maintenance_llm_start = 116`
  - `maintenance_llm_done = 115`
- Role-level starts/dones:
  - `extractor = 60 / 60`
  - `refactorer = 4 / 4`
  - `bundle_builder = 3 / 3`
  - `skill_injector = 38 / 38`
  - `credit_assigner = 5 / 5`
  - `refiner = 4 / 4`
  - `extractor_feedback = 2 / 1`
- Train rows:
  - success: `9 / 20`
  - official_valid: `16 / 20`
  - avg_score: `0.9019`
  - errors: `0`
  - prompt-injected rows: `5 / 20`

Interpretation:
- Training quality is healthy.
- Injector-call count is dramatically lower than the prior full run pattern.
- Continue into final test.

## 2026-05-20 03:32:31 +0800

Fourth monitoring pass for 20/50 injector-gate validation.

Status:
- PID `2067993` alive.
- Final skills file present:
  - `academic/results/bfcl_related20_50_sonnet_trl_injector_gate_20260520_skills.json`
- Final evolve file not yet present.
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 1`
  - `maintenance_llm_error = 0`
- The one timeout is a task-level API timeout on `multi_turn_base_77`, recorded as a failed task and the run continued.
- Progress:
  - `bfcl_task_run = 52`
  - `maintenance_llm_start = 212`
  - `maintenance_llm_done = 212`
- Role-level starts/dones:
  - `extractor = 60 / 60`
  - `refactorer = 4 / 4`
  - `bundle_builder = 3 / 3`
  - `skill_injector = 134 / 134`
  - `credit_assigner = 5 / 5`
  - `refiner = 4 / 4`
  - `extractor_feedback = 2 / 2`
- All task rows so far:
  - task rows: `52`
  - success: `13 / 52`
  - official_valid: `34 / 52`
  - avg_score: `0.8257`
  - errors: `1`
  - prompt-injected rows: `26`
- Test rows so far, after the 20 train rows:
  - test rows: `32 / 50`
  - success: `4 / 32`
  - official_valid: `18 / 32`
  - avg_score: `0.7781`
  - errors: `1`
  - prompt-injected rows: `21 / 32`

Interpretation:
- Test quality is plausible and strict success is already at `4` successes before finishing, but the API timeout may depress final metrics.
- Injector-call volume remains much lower than the prior 20/50 and 50/50 run patterns.
- Continue to final output.

## 2026-05-20 03:39:11 +0800

20/50 injector-gate validation completed.

Output files:
- `academic/results/bfcl_related20_50_sonnet_trl_injector_gate_20260520_evolve.json`
- `academic/results/bfcl_related20_50_sonnet_trl_injector_gate_20260520_skills.json`
- `academic/results/bfcl_related20_50_sonnet_trl_injector_gate_20260520_checkpoint.json`

Run health:
- Process exited normally.
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 1`
- `maintenance_llm_error = 0`
- One task-level API timeout occurred on `multi_turn_base_77`; it was converted to a failed task and did not crash the run.

Train summary:
- `success_rate = 0.45`
- `official_valid_rate = 0.80`
- `avg_score = 0.9019`
- `avg_call_precision = 0.8847`
- `avg_call_recall = 0.9493`
- `avg_total_tokens = 47665.8`
- `avg_elapsed_s = 37.477`
- `timeout_rate = 0.0`

Test summary:
- `success_rate = 0.10`
- `official_valid_rate = 0.551`
- `avg_score = 0.7591`
- `avg_call_precision = 0.7044`
- `avg_call_recall = 0.8491`
- `avg_total_tokens = 73059.0`
- `avg_elapsed_s = 44.206`
- `timeout_rate = 0.0`
- From log parse:
  - test rows: `50`
  - strict successes: `5`
  - prompt-injected rows: `23`
  - task errors: `1`

Injector cost:
- `skill_injector` calls: `142`
- `skill_injector` total tokens: `186928`
- `skill_injector` duration: `682403 ms`

Comparison to prior 20/50 warm-up:
- Prior warm-up file: `academic/results/bfcl_related20_50_sonnet_trl_20260520_evolve.json`
- Strict success:
  - prior `0.06`
  - injector-gate `0.10`
  - result: injector-gate `+0.04`
- Official valid:
  - prior `0.62`
  - injector-gate `0.551`
  - result: injector-gate `-0.069`
- Avg score:
  - prior `0.7710`
  - injector-gate `0.7591`
  - result: injector-gate `-0.0119`
- Avg recall:
  - prior `0.8595`
  - injector-gate `0.8491`
  - result: injector-gate `-0.0104`
- Avg precision:
  - prior `0.7234`
  - injector-gate `0.7044`
  - result: injector-gate `-0.0190`
- Avg elapsed:
  - prior `63.573`
  - injector-gate `44.206`
  - result: injector-gate faster by `19.367s`
- Avg total tokens:
  - prior `73770.5`
  - injector-gate `73059.0`
  - result: injector-gate slightly lower by `711.5`

Comparison to aligned baseline:
- Baseline strict success: `0.08`; injector-gate 20/50 strict success: `0.10`.
- Baseline official_valid: `0.48`; injector-gate: `0.551`.
- Baseline avg_score: `0.7388`; injector-gate: `0.7591`.
- Baseline avg_precision: `0.7023`; injector-gate: `0.7044`.
- Baseline avg_recall: `0.8181`; injector-gate: `0.8491`.

Decision:
- The patch did not preserve every partial metric from the prior warm-up, but it improved strict success on the same 50-test set and made the system much faster.
- It also beats the aligned baseline on strict success and partial metrics in this 20/50 validation.
- Proceed to a full 50/50 rerun with the injector-gate patch.

## 2026-05-20 03:4x +0800

Started full 50/50 rerun after injector-gate patch.

Run:
- PID: `2363638`
- Tag: `bfcl_related50_50_sonnet_trl_injector_gate_20260520`
- Manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`
- Train/test: `50 / 50`
- Output:
  - `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json`
  - `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json`
  - `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_checkpoint.json`
  - log: `academic/results/logs/bfcl_related50_50_sonnet_trl_injector_gate_20260520.log`
  - macro snapshots: `academic/results/macro_snapshots/bfcl_related50_50_sonnet_trl_injector_gate_20260520`
- Shared settings:
  - model: `claude-sonnet-4-5`
  - execution backend: `official`
  - prompt style: `native`
  - max steps per turn: `20`
  - max task seconds: `180`
  - temperature: `0.0`
  - candidate competition enabled
  - candidate sample count: `3`
  - train window concurrency: `4`
  - test concurrency: `4`
  - micro concurrency: `4`

Monitoring goal:
- Confirm no regressions from the injector-gate patch.
- Track `skill_injector` call count and final strict/partial metrics.
- If final result beats baseline and compares favorably to SkillX, update master docs.

## 2026-05-20 03:42:25 +0800

First monitoring pass for full 50/50 injector-gate rerun.

Status:
- PID `2363638` alive.
- Final output files not yet present.
- Checkpoint present:
  - `next_task_index = 6`
  - `n_train_details = 6`
  - `n_micro_maintenance_reports = 6`
  - `n_maintenance_windows = 0`
  - `n_store_artifacts = 3`
  - `n_store_test_results = 0`
  - `n_segment_index_rows = 29`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Progress:
  - `bfcl_task_run = 10`
  - `maintenance_llm_start = 30`
  - `maintenance_llm_done = 30`
  - all maintenance calls so far are `extractor`
- First 10 train rows:
  - success: `4 / 10`
  - official_valid: `9 / 10`
  - avg_score: `0.9308`
  - errors: `0`
  - prompt-injected rows: `0`

Interpretation:
- Run is healthy.
- Continue monitoring.

## 2026-05-20 03:47:57 +0800

Second monitoring pass for full 50/50 injector-gate rerun.

Status:
- PID `2363638` alive.
- Final output files not yet present.
- Checkpoint:
  - `next_task_index = 10`
  - `n_train_details = 10`
  - `n_micro_maintenance_reports = 10`
  - `n_maintenance_windows = 1`
  - `n_store_artifacts = 6`
  - `n_store_test_results = 1`
  - `n_segment_index_rows = 46`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Progress:
  - `bfcl_task_run = 20`
  - `maintenance_llm_start = 73`
  - `maintenance_llm_done = 69`
- Role-level starts/dones:
  - `extractor = 33 / 31`
  - `refactorer = 4 / 4`
  - `bundle_builder = 2 / 2`
  - `skill_injector = 30 / 30`
  - `credit_assigner = 4 / 2`
- Current train rows from log:
  - task rows: `20`
  - success: `9 / 20`
  - official_valid: `16 / 20`
  - avg_score: `0.9168`
  - errors: `0`
  - prompt-injected rows: `7 / 20`

Interpretation:
- The first full window completed and checkpointed.
- Injector-call count is controlled compared with the previous full 50/50 run.
- Continue monitoring.

## 2026-05-20 03:55:32 +0800

Third monitoring pass for full 50/50 injector-gate rerun.

Status:
- PID `2363638` alive.
- Final output files not yet present.
- Checkpoint still reports:
  - `next_task_index = 10`
  - `n_train_details = 10`
  - `n_micro_maintenance_reports = 10`
  - `n_maintenance_windows = 1`
  - `n_store_artifacts = 6`
  - `n_store_test_results = 1`
  - `n_segment_index_rows = 46`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Progress:
  - `bfcl_task_run = 20`
  - `maintenance_llm_start = 118`
  - `maintenance_llm_done = 117`
- Role-level starts/dones:
  - `extractor = 60 / 60`
  - `refactorer = 5 / 4`
  - `bundle_builder = 2 / 2`
  - `skill_injector = 38 / 38`
  - `credit_assigner = 7 / 7`
  - `refiner = 5 / 5`
  - `stale_resolver = 1 / 1`
- Current train rows from log:
  - task rows: `20`
  - success: `9 / 20`
  - official_valid: `16 / 20`
  - avg_score: `0.9168`
  - errors: `0`
  - prompt-injected rows: `7 / 20`
- Latest active role: `refactorer`.

Interpretation:
- Still in maintenance/refactor after first checkpoint and before second checkpoint flush.
- No anomaly.

## 2026-05-20 04:03:06 +0800

Fourth monitoring pass for full 50/50 injector-gate rerun.

Status:
- PID `2363638` alive.
- Final output files not yet present.
- Checkpoint still reports:
  - `next_task_index = 10`
  - `n_train_details = 10`
  - `n_micro_maintenance_reports = 10`
  - `n_maintenance_windows = 1`
  - `n_store_artifacts = 6`
  - `n_store_test_results = 1`
  - `n_segment_index_rows = 46`
- Log health:
  - `Traceback = 0`
  - `APIConnectionError = 0`
  - `Timeout = 0`
  - `timeout = 0`
  - `maintenance_llm_error = 0`
- Progress:
  - `bfcl_task_run = 20`
  - `maintenance_llm_start = 146`
  - `maintenance_llm_done = 145`
- Role-level starts/dones:
  - `extractor = 60 / 60`
  - `refactorer = 10 / 10`
  - `bundle_builder = 7 / 7`
  - `skill_injector = 55 / 55`
  - `credit_assigner = 7 / 7`
  - `refiner = 5 / 5`
  - `stale_resolver = 1 / 1`
  - `extractor_feedback = 1 / 0`
- Current train rows from log:
  - task rows: `20`
  - success: `9 / 20`
  - official_valid: `16 / 20`
  - avg_score: `0.9168`
  - errors: `0`
  - prompt-injected rows: `7 / 20`
- Latest active role: `extractor_feedback`.

Interpretation:
- Still in maintenance after the first checkpoint.
- The run remains healthy and much cheaper than the previous ungated full run at the same rough stage.

## 2026-05-20 04:12:13 +0800 - Full 50/50 injector-gate rerun still healthy

Command/status checked:
- `ps -p 2363638 -o pid,etime,stat,cmd`
- `tail -n 80 academic/results/logs/bfcl_related50_50_sonnet_trl_injector_gate_20260520.log`
- Parsed `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_checkpoint.json` and related current-round detail files.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `31:48`.
- Status: `Sl`.
- Final result file `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json` has not appeared yet.

Checkpoint state from `current_round_state`:
- `round_index = 0`
- `next_task_index = 20`
- `n_train_details = 20`
- `n_micro_maintenance_reports = 20`
- `n_maintenance_windows = 2`
- `n_store_artifacts = 35`
- `n_store_test_results = 7`
- `n_segment_index_rows = 80`
- `online_refactor_budget_remaining = 0`
- `n_extraction_events = 34`
- `n_credit_events = 9`
- `n_candidate_group_feedback_state = 7`

Log event counts:
- `bfcl_task_run = 30`
- `maintenance_llm_start = 232`
- `maintenance_llm_done = 232`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 0`
- `maintenance_llm_error = 0`

Role start/done counts:
- `extractor = 90 / 90`
- `refactorer = 10 / 10`
- `bundle_builder = 7 / 7`
- `skill_injector = 100 / 100`
- `credit_assigner = 16 / 16`
- `refiner = 7 / 7`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 1 / 1`

Current-round train detail summary from checkpoint sidecar:
- Rows loaded from `checkpoint_current_round_details.json`: `20`.
- Non-null ids: `20`.
- First ids: `multi_turn_base_120`, `multi_turn_base_130`.
- Last ids: `multi_turn_base_70`, `multi_turn_base_82`.
- Average score over rows with `score`: `0.91677`.
- Rows with explicit error/exception field set: `0`.

Interpretation:
- The run remains healthy.
- The previous transient `extractor_feedback = 1 / 0` observation has resolved to `1 / 1`; no stuck role is visible now.
- Progress is still around train index `20`, so continue monitoring rather than summarizing final results.

## 2026-05-20 04:14:37 +0800 - Full 50/50 injector-gate rerun in active maintenance

Command/status checked:
- Waited 120 seconds, then checked process, output files, latest log, and checkpoint.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `34:45`.
- Final result file is still absent.
- Checkpoint timestamp remains `2026-05-20 04:03`, so no new checkpoint has been written since the previous checkpoint snapshot.
- Log grew from roughly `102K` to `108K`, indicating continued activity.

Checkpoint state remains:
- `next_task_index = 20`
- `n_train_details = 20`
- `n_micro_maintenance_reports = 20`
- `n_maintenance_windows = 2`
- `n_store_artifacts = 35`
- `n_store_test_results = 7`
- `n_segment_index_rows = 80`
- `n_extraction_events = 34`
- `n_credit_events = 9`

Log event counts:
- `bfcl_task_run = 30`
- `maintenance_llm_start = 246`
- `maintenance_llm_done = 245`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 90 / 90`
- `refactorer = 10 / 10`
- `bundle_builder = 7 / 7`
- `skill_injector = 109 / 108`
- `credit_assigner = 16 / 16`
- `refiner = 12 / 12`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 1 / 1`

Latest active event:
- A `skill_injector` call is active with approximately `user_chars = 1843`, `system_chars = 833`, `attempt = 1 / 5`.

Interpretation:
- The run is not stuck by current evidence: log activity continues and only one short skill-injector call is outstanding.
- No error/timeout/traceback signals are present.
- Continue monitoring until either checkpoint advances or final output appears.

## 2026-05-20 04:18:14 +0800 - Full 50/50 injector-gate rerun still in post-index-20 maintenance

Command/status checked:
- Waited 180 seconds, then checked process, output files, latest log, checkpoint timestamps, and role token/duration totals.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `38:23`.
- Final result file is still absent.
- Checkpoint timestamp remains `2026-05-20 04:03:14 +0800`.
- Current-round details timestamp remains `2026-05-20 04:03:13 +0800`.
- Log timestamp is fresh at `2026-05-20 04:18:23 +0800`, so the process is still writing activity.

Log event counts:
- `bfcl_task_run = 30`
- `maintenance_llm_start = 264`
- `maintenance_llm_done = 263`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 90 / 90`
- `refactorer = 10 / 10`
- `bundle_builder = 7 / 7`
- `skill_injector = 120 / 120`
- `credit_assigner = 16 / 16`
- `refiner = 19 / 18`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 1 / 1`

Role token totals so far from log `maintenance_llm_done` events:
- `extractor = 695231`
- `refactorer = 88579`
- `bundle_builder = 50457`
- `skill_injector = 131398`
- `credit_assigner = 104118`
- `refiner = 96884`
- `stale_resolver = 4838`
- `extractor_feedback = 3121`

Role duration totals so far from log `maintenance_llm_done` events:
- `extractor = 1123900 ms`
- `refactorer = 251621 ms`
- `bundle_builder = 126704 ms`
- `skill_injector = 522321 ms`
- `credit_assigner = 355499 ms`
- `refiner = 135993 ms`
- `stale_resolver = 12575 ms`
- `extractor_feedback = 15724 ms`

Latest active event:
- A `refiner` call is active with approximately `user_chars = 14015`, `system_chars = 6065`, `attempt = 1 / 5`.

Interpretation:
- This is a long maintenance segment after train index `20`, but not obviously stuck: log writes continue and outstanding call count is one.
- The injector-gate change is still visibly reducing `skill_injector` load relative to the original full 50/50 run (`131398` tokens observed so far here versus `1338717` total in the original ungated full run), though final comparison must wait for completion.

## 2026-05-20 04:21:57 +0800 - Long maintenance continues after train index 20

Command/status checked:
- Waited another 180 seconds, then checked process, output files, checkpoint, and log role counters.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `42:05`.
- Final result file is still absent.
- Checkpoint timestamp still remains at `2026-05-20 04:03:14 +0800`.
- Log timestamp is fresh, and log size has grown to about `121K`.

Checkpoint state remains unchanged:
- `next_task_index = 20`
- `n_train_details = 20`
- `n_micro_maintenance_reports = 20`
- `n_maintenance_windows = 2`
- `n_store_artifacts = 35`
- `n_store_test_results = 7`
- `n_segment_index_rows = 80`
- `n_extraction_events = 34`
- `n_credit_events = 9`
- `n_candidate_group_feedback_state = 7`

Log event counts:
- `bfcl_task_run = 30`
- `maintenance_llm_start = 282`
- `maintenance_llm_done = 281`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 90 / 90`
- `refactorer = 10 / 10`
- `bundle_builder = 7 / 7`
- `skill_injector = 134 / 134`
- `credit_assigner = 16 / 16`
- `refiner = 23 / 22`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 1 / 1`

Role token totals so far:
- `extractor = 695231`
- `refactorer = 88579`
- `bundle_builder = 50457`
- `skill_injector = 143449`
- `credit_assigner = 104118`
- `refiner = 118361`
- `stale_resolver = 4838`
- `extractor_feedback = 3121`

Role duration totals so far:
- `extractor = 1123900 ms`
- `refactorer = 251621 ms`
- `bundle_builder = 126704 ms`
- `skill_injector = 576683 ms`
- `credit_assigner = 355499 ms`
- `refiner = 169049 ms`
- `stale_resolver = 12575 ms`
- `extractor_feedback = 15724 ms`

Interpretation:
- The run is still active, with the latest outstanding call being `refiner`.
- The post-index-20 maintenance segment is long and is adding repeated `skill_injector`/`refiner` calls.
- This is not an immediate failure, but it is a cost/speed signal to include in final comparison: the gate reduced the previous repeated normal-path injection problem, but candidate/refiner maintenance can still be expensive.

## 2026-05-20 04:26:38 +0800 - Long maintenance remains active but LLM calls are returning

Command/status checked:
- Waited 240 seconds, then checked process, output files, checkpoint, log role counters, and current-round store snapshot.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `46:47`.
- Final result file is still absent.
- Checkpoint timestamp remains `2026-05-20 04:03:14 +0800`.
- Log size has grown to about `132K`, with fresh writes at `04:26`.

Checkpoint state remains unchanged:
- `next_task_index = 20`
- `n_train_details = 20`
- `n_micro_maintenance_reports = 20`
- `n_maintenance_windows = 2`
- `n_store_artifacts = 35`
- `n_store_test_results = 7`
- `n_segment_index_rows = 80`
- `n_extraction_events = 34`
- `n_credit_events = 9`
- `n_candidate_group_feedback_state = 7`

Log event counts:
- `bfcl_task_run = 30`
- `maintenance_llm_start = 310`
- `maintenance_llm_done = 310`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 90 / 90`
- `refactorer = 10 / 10`
- `bundle_builder = 7 / 7`
- `skill_injector = 152 / 152`
- `credit_assigner = 16 / 16`
- `refiner = 33 / 33`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 1 / 1`

Role token totals so far:
- `extractor = 695231`
- `refactorer = 88579`
- `bundle_builder = 50457`
- `skill_injector = 158772`
- `credit_assigner = 104118`
- `refiner = 178206`
- `stale_resolver = 4838`
- `extractor_feedback = 3121`

Role duration totals so far:
- `extractor = 1123900 ms`
- `refactorer = 251621 ms`
- `bundle_builder = 126704 ms`
- `skill_injector = 642780 ms`
- `credit_assigner = 355499 ms`
- `refiner = 287434 ms`
- `stale_resolver = 12575 ms`
- `extractor_feedback = 15724 ms`

Current store snapshot text counts:
- `pending = 52`
- `active = 16`
- `rejected = 0`
- `candidate = 1659`
- `prompt_only = 34`
- `callable = 0`
- `informational = 6`

Interpretation:
- All observed maintenance LLM starts are matched by completions at this snapshot, so this is not an API hang.
- The long post-index-20 maintenance is still a real overhead issue. It appears dominated by repeated `skill_injector` and `refiner` work after candidate/refinement maintenance, not by executor-step repeated injection alone.
- `rejected = 0` in the store snapshot is worth revisiting after completion: it may simply mean current candidates are pending/active, but if poor skills are not being rejected, that affects user concern about bad skills being found and dropped.

## 2026-05-20 04:32:31 +0800 - Checkpoint advanced from train index 20 to 30

Command/status checked:
- Waited 300 seconds, then checked process, output files, checkpoint, latest log, and current-round train details.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `52:39`.
- Final result file is still absent.
- Checkpoint file refreshed at `2026-05-20 04:29` and grew to about `18M`.
- Log size grew to about `150K`.

Checkpoint state:
- `next_task_index = 30`
- `n_train_details = 30`
- `n_micro_maintenance_reports = 30`
- `n_maintenance_windows = 3`
- `n_store_artifacts = 80`
- `n_store_test_results = 33`
- `n_segment_index_rows = 112`
- `n_extraction_events = 78`
- `n_credit_events = 36`
- `n_candidate_group_feedback_state = 15`

Current-round train detail sidecar:
- Rows: `30`.
- First ids: `multi_turn_base_120`, `multi_turn_base_130`, `multi_turn_base_116`.
- Last ids: `multi_turn_base_94`, `multi_turn_base_71`, `multi_turn_base_104`.
- Average score over rows with `score`: `0.8805466666666667`.
- Explicit error/exception rows: `0`.

Log event counts:
- `bfcl_task_run = 39`
- `maintenance_llm_start = 342`
- `maintenance_llm_done = 342`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 90 / 90`
- `refactorer = 11 / 11`
- `bundle_builder = 8 / 8`
- `skill_injector = 178 / 178`
- `credit_assigner = 16 / 16`
- `refiner = 36 / 36`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 2 / 2`

Role token totals so far:
- `extractor = 695231`
- `refactorer = 98273`
- `bundle_builder = 57871`
- `skill_injector = 193971`
- `credit_assigner = 104118`
- `refiner = 194380`
- `stale_resolver = 4838`
- `extractor_feedback = 7697`

Recent task observations from log:
- `multi_turn_base_96`: score `0.7692`, success `false`, official_valid `false`, prompt skill `lock_all_doors_after_parking__candidate_r0_t23_s1` injected, no called skill tools.
- `multi_turn_base_50`: score `1.0`, success `true`, official_valid `true`, no prompt skills injected.
- `multi_turn_base_89`: score `0.8235`, success `false`, official_valid `true`, prompt skills `vehicle_fill_fuel_check_current_level_before_capacity_fill__candidate_r0_t24_s2`, `press_brake_before_starting_engine__candidate_r0_t18_s1`, `vehicle_engine_start_requires_locked_doors__candidate_r0_t17_s1` injected, no called skill tools.
- `multi_turn_base_56`: score `0.9412`, success `false`, official_valid `true`, prompt skills `vehicle_fuel_check_unnecessary_before_explicit_full_tank_fill__candidate_r0_t28_s1`, `vehicle_engine_start_after_door_lock_no_redundant_start__candidate_r0_t26_s2`, `press_brake_before_starting_engine__candidate_r0_t18_s1` injected, no called skill tools.
- `multi_turn_base_165`: score `0.7692`, success `false`, official_valid `true`, no prompt skills injected.

Interpretation:
- The previous long maintenance segment completed and checkpoint progressed normally from index `20` to `30`.
- No runtime errors were observed.
- Prompt-only skill injection is occurring on some tasks. `called_skill_tools` is empty by design in this formal run because `--skill-injection-mode prompt_only` is used, so call/copy behavior must be judged from prompt influence and final metrics rather than direct tool-call counts.
- The run remains healthy; continue monitoring until completion.

## 2026-05-20 04:38:24 +0800 - One task-level timeout observed; run continues

Command/status checked:
- Waited 300 seconds, then checked process, output files, checkpoint, latest log, role counters, and task summaries.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `58:32`.
- Final result file is still absent.
- Checkpoint still at `next_task_index = 30`; latest checkpoint timestamp remains `2026-05-20 04:29`.
- Log size grew to about `167K`.

Checkpoint state remains:
- `next_task_index = 30`
- `n_train_details = 30`
- `n_micro_maintenance_reports = 30`
- `n_maintenance_windows = 3`
- `n_store_artifacts = 80`
- `n_store_test_results = 33`
- `n_segment_index_rows = 112`
- `n_extraction_events = 78`
- `n_credit_events = 36`
- `n_candidate_group_feedback_state = 15`

Log event counts:
- `bfcl_task_run = 40`
- `maintenance_llm_start = 386`
- `maintenance_llm_done = 386`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 120 / 120`
- `refactorer = 11 / 11`
- `bundle_builder = 8 / 8`
- `skill_injector = 183 / 183`
- `credit_assigner = 23 / 23`
- `refiner = 38 / 38`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 2 / 2`

Role token totals so far:
- `extractor = 931773`
- `refactorer = 98273`
- `bundle_builder = 57871`
- `skill_injector = 198143`
- `credit_assigner = 155469`
- `refiner = 205657`
- `stale_resolver = 4838`
- `extractor_feedback = 7697`

Task summary from log rows so far:
- Task rows: `40`.
- Success rows: `11 / 40`.
- Official-valid rows: `27 / 40`.
- Average score: `0.8580225`.

Timeout detail:
- `multi_turn_base_79` recorded as a failed task row with `score = 0.0`, `success = false`, `official_valid = null`, and error text: `Request timed out or interrupted. This could be due to a network timeout, dropped connection, or request cancellation. See https://docs.anthropic.com/en/api/errors#long-requests for more details.`
- This matches the intended behavior of converting a task-level request timeout into a failed task and continuing the run, rather than restarting.

Recent prompt-injection observations:
- Several tasks still receive prompt skills, for example `multi_turn_base_66` received `vehicle_navigation_set_only_when_destination_requested__candidate_r0_t26_s1`, `vehicle_fuel_check_unnecessary_before_explicit_full_tank_fill__candidate_r0_t28_s1`, `vehicle_engine_start_after_door_lock_no_redundant_start__candidate_r0_t26_s2`, and `press_brake_before_starting_engine__candidate_r0_t18_s1`.
- Current formal run remains `prompt_only`, so no called skill tools are expected.

Interpretation:
- The run remains healthy at the process/maintenance level.
- There is now one task-level timeout that will likely hurt strict success and average score. It should be included in final metrics, not hidden.
- Because the process continued correctly, no intervention is needed now.

## 2026-05-20 04:44:08 +0800 - Still in maintenance after index 30; timeout count unchanged

Command/status checked:
- Waited 300 seconds, then checked process, output files, checkpoint, log role counters, task rows, and log tail.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:04:17`.
- Final result file is still absent.
- Checkpoint still at `next_task_index = 30`; latest checkpoint timestamp remains `2026-05-20 04:29`.
- Log size grew to about `177K`.

Checkpoint state remains:
- `next_task_index = 30`
- `n_train_details = 30`
- `n_micro_maintenance_reports = 30`
- `n_maintenance_windows = 3`
- `n_store_artifacts = 80`
- `n_store_test_results = 33`
- `n_segment_index_rows = 112`
- `n_extraction_events = 78`
- `n_credit_events = 36`
- `n_candidate_group_feedback_state = 15`

Log event counts:
- `bfcl_task_run = 40`
- `maintenance_llm_start = 411`
- `maintenance_llm_done = 411`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 120 / 120`
- `refactorer = 11 / 11`
- `bundle_builder = 8 / 8`
- `skill_injector = 203 / 203`
- `credit_assigner = 23 / 23`
- `refiner = 43 / 43`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 2 / 2`

Role token totals so far:
- `extractor = 931773`
- `refactorer = 98273`
- `bundle_builder = 57871`
- `skill_injector = 214370`
- `credit_assigner = 155469`
- `refiner = 232462`
- `stale_resolver = 4838`
- `extractor_feedback = 7697`

Task summary from log rows remains:
- Task rows: `40`.
- Success rows: `11 / 40`.
- Official-valid rows: `27 / 40`.
- Average score: `0.8580225`.
- Timeout rows: still `1`, no additional timeout since the previous observation.

Interpretation:
- The process is healthy but still paying substantial maintenance overhead after index `30`.
- `skill_injector` and `refiner` are again the roles accumulating cost in this segment.
- No intervention yet because calls are completing and the run is expected to continue.

## 2026-05-20 04:49:48 +0800 - Post-index-30 maintenance still running; calls remain healthy

Command/status checked:
- Waited 300 seconds, then checked process, output files, checkpoint, log role counters, task rows, and log tail.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:09:57`.
- Final result file is still absent.
- Checkpoint still at `next_task_index = 30`; latest checkpoint timestamp remains `2026-05-20 04:29`.
- Log size grew to about `186K`.

Checkpoint state remains:
- `next_task_index = 30`
- `n_train_details = 30`
- `n_micro_maintenance_reports = 30`
- `n_maintenance_windows = 3`
- `n_store_artifacts = 80`
- `n_store_test_results = 33`
- `n_segment_index_rows = 112`
- `n_extraction_events = 78`
- `n_credit_events = 36`
- `n_candidate_group_feedback_state = 15`

Log event counts:
- `bfcl_task_run = 40`
- `maintenance_llm_start = 435`
- `maintenance_llm_done = 435`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 120 / 120`
- `refactorer = 11 / 11`
- `bundle_builder = 8 / 8`
- `skill_injector = 219 / 219`
- `credit_assigner = 23 / 23`
- `refiner = 51 / 51`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 2 / 2`

Role token totals so far:
- `extractor = 931773`
- `refactorer = 98273`
- `bundle_builder = 57871`
- `skill_injector = 227794`
- `credit_assigner = 155469`
- `refiner = 277300`
- `stale_resolver = 4838`
- `extractor_feedback = 7697`

Task summary from log rows remains:
- Task rows: `40`.
- Success rows: `11 / 40`.
- Official-valid rows: `27 / 40`.
- Average score: `0.8580225`.
- Timeout rows: still `1`.

Interpretation:
- The run is still healthy in the narrow sense: all observed LLM starts are matched by completions, and no new timeout/error appeared.
- The post-index-30 maintenance segment has become a notable cost/speed risk. The main incremental roles are `skill_injector` and `refiner`, which suggests candidate/refinement maintenance is still expensive after the executor-step injection gate.
- Continue monitoring; do not interrupt while it is progressing.

## 2026-05-20 04:55:40 +0800 - Long post-index-30 maintenance reaches refactorer phase

Command/status checked:
- Waited 300 seconds, then checked process, output files, checkpoint, log role counters, task rows, and log tail.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:15:49`.
- Final result file is still absent.
- Checkpoint still at `next_task_index = 30`; latest checkpoint timestamp remains `2026-05-20 04:29:25 +0800`.
- Log size grew to about `198K`.

Checkpoint state remains:
- `next_task_index = 30`
- `n_train_details = 30`
- `n_micro_maintenance_reports = 30`
- `n_maintenance_windows = 3`
- `n_store_artifacts = 80`
- `n_store_test_results = 33`
- `n_segment_index_rows = 112`
- `n_extraction_events = 78`
- `n_credit_events = 36`
- `n_candidate_group_feedback_state = 15`

Log event counts:
- `bfcl_task_run = 40`
- `maintenance_llm_start = 465`
- `maintenance_llm_done = 464`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 120 / 120`
- `refactorer = 12 / 11`
- `bundle_builder = 8 / 8`
- `skill_injector = 238 / 238`
- `credit_assigner = 23 / 23`
- `refiner = 61 / 61`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 2 / 2`

Role token totals so far:
- `extractor = 931773`
- `refactorer = 98273`
- `bundle_builder = 57871`
- `skill_injector = 243625`
- `credit_assigner = 155469`
- `refiner = 334387`
- `stale_resolver = 4838`
- `extractor_feedback = 7697`

Task summary from log rows remains:
- Task rows: `40`.
- Success rows: `11 / 40`.
- Official-valid rows: `27 / 40`.
- Average score: `0.8580225`.
- Timeout rows: still `1`.

Latest active event:
- A `refactorer` call is active with approximately `user_chars = 16015`, `system_chars = 7365`, `attempt = 1 / 5`.

Interpretation:
- The segment appears to have progressed from repeated `skill_injector`/`refiner` work into a `refactorer` stage, which may mean the maintenance window is nearing a checkpoint boundary.
- Still no evidence of a hang or API failure. Continue waiting.
- Cost/speed risk remains important: this window accumulated many additional `skill_injector` and `refiner` calls before reaching refactorer.

## 2026-05-20 05:00:24 +0800 - Checkpoint advanced to train index 40; latest ten tasks are weak

Command/status checked:
- Waited 240 seconds, then checked process, output files, checkpoint, log role counters, task rows, and train detail sidecar.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:20:32`.
- Final result file is still absent.
- Checkpoint file refreshed at `2026-05-20 04:57` and grew to about `35M`.
- Log size grew to about `222K`.

Checkpoint state:
- `next_task_index = 40`
- `n_train_details = 40`
- `n_micro_maintenance_reports = 40`
- `n_maintenance_windows = 4`
- `n_store_artifacts = 118`
- `n_store_test_results = 50`
- `n_segment_index_rows = 139`
- `n_extraction_events = 115`
- `n_credit_events = 55`
- `n_candidate_group_feedback_state = 23`

Current-round train detail sidecar:
- Rows: `40`.
- First ids: `multi_turn_base_120`, `multi_turn_base_130`, `multi_turn_base_116`.
- Last ids: `multi_turn_base_56`, `multi_turn_base_50`, `multi_turn_base_165`.
- Average score over rows with `score`: `0.8580225`.
- Explicit error/exception rows: `0`.

Log event counts:
- `bfcl_task_run = 50`
- `maintenance_llm_start = 520`
- `maintenance_llm_done = 516`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 121 / 120`
- `refactorer = 12 / 12`
- `bundle_builder = 9 / 9`
- `skill_injector = 287 / 287`
- `credit_assigner = 26 / 23`
- `refiner = 61 / 61`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 3 / 3`

Role token totals so far:
- `extractor = 931773`
- `refactorer = 107731`
- `bundle_builder = 64894`
- `skill_injector = 285962`
- `credit_assigner = 155469`
- `refiner = 334387`
- `stale_resolver = 4838`
- `extractor_feedback = 12068`

Task summary from log rows so far:
- Task rows: `50`.
- Success rows: `11 / 50`.
- Official-valid rows: `32 / 50`.
- Average score: `0.818318`.
- Timeout rows: still `1`.

Latest ten task observations:
- `multi_turn_base_190`: score `0.5`, success `false`, official_valid `true`, prompt skill `contact_customer_support_concise_message_contract__candidate_r0_t39_s1`.
- `multi_turn_base_187`: score `0.3636`, success `false`, official_valid `false`, prompt skill `contact_customer_support_concise_message_contract__candidate_r0_t39_s1`.
- `multi_turn_base_181`: score `0.7143`, success `false`, official_valid `false`, no prompt skills.
- `multi_turn_base_156`: score `0.625`, success `false`, official_valid `true`, prompt skills `skip_airport_lookup_when_destination_explicit_iata__candidate_r0_t39_s2`, `contact_customer_support_concise_message_contract__candidate_r0_t39_s1`.
- `multi_turn_base_177`: score `0.6667`, success `false`, official_valid `true`, prompt skill `skip_airport_lookup_when_destination_explicit_iata__candidate_r0_t39_s2`.
- `multi_turn_base_162`: score `0.7273`, success `false`, official_valid `false`, no prompt skills.
- `multi_turn_base_178`: score `0.5714`, success `false`, official_valid `false`, prompt skill `contact_customer_support_concise_message_contract__candidate_r0_t39_s1`.
- `multi_turn_base_196`: score `0.7143`, success `false`, official_valid `false`, prompt skill `skip_airport_lookup_when_destination_explicit_iata__candidate_r0_t39_s2`.
- `multi_turn_base_180`: score `0.8235`, success `false`, official_valid `true`, prompt skills `skip_airport_lookup_when_destination_explicit_iata__candidate_r0_t39_s2`, `contact_customer_support_concise_message_contract__candidate_r0_t39_s2`.
- `multi_turn_base_193`: score `0.8889`, success `false`, official_valid `true`, prompt skill `skip_airport_lookup_when_destination_explicit_iata__candidate_r0_t39_s2`.

Interpretation:
- The run progressed normally from train index `30` to `40`.
- The latest ten task rows are weak: strict success is `0 / 10`, although several rows remain official-valid with partial score.
- Prompt injection is active, but the injected travel/contact skills did not lead to strict successes in this window. This is an important case-study target if the final full result does not beat baseline.
- The process has entered another maintenance phase with one extractor and three credit_assigner calls currently active.

## 2026-05-20 05:06:23 +0800 - Final training segment advances to index 41

Command/status checked:
- Waited 300 seconds, then checked process, output files, checkpoint, log role counters, task rows, and log tail.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:26:32`.
- Final result file is still absent.
- Checkpoint timestamp refreshed to `2026-05-20 05:04:52 +0800`.
- Log size grew to about `239K`.

Checkpoint state:
- `next_task_index = 41`
- `n_train_details = 41`
- `n_micro_maintenance_reports = 41`
- `n_maintenance_windows = 4`
- `n_store_artifacts = 127`
- `n_store_test_results = 50`
- `n_segment_index_rows = 144`
- `n_extraction_events = 124`
- `n_credit_events = 55`
- `n_candidate_group_feedback_state = 23`

Log event counts:
- `bfcl_task_run = 50`
- `maintenance_llm_start = 563`
- `maintenance_llm_done = 562`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 150 / 150`
- `refactorer = 12 / 12`
- `bundle_builder = 9 / 9`
- `skill_injector = 292 / 292`
- `credit_assigner = 31 / 31`
- `refiner = 65 / 64`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 3 / 3`

Role token totals so far:
- `extractor = 1199751`
- `refactorer = 107731`
- `bundle_builder = 64894`
- `skill_injector = 290430`
- `credit_assigner = 204073`
- `refiner = 351317`
- `stale_resolver = 4838`
- `extractor_feedback = 12068`

Task summary from log rows remains:
- Task rows: `50`.
- Success rows: `11 / 50`.
- Official-valid rows: `32 / 50`.
- Average score: `0.818318`.
- Timeout rows: still `1`.

Latest active event:
- A `refiner` call is active with approximately `user_chars = 14015`, `system_chars = 6065`, `attempt = 1 / 5`.

Interpretation:
- The final training segment is now progressing one task beyond the 40-task checkpoint.
- The run remains healthy, with only the previously recorded task timeout.
- Maintenance cost is high: extractor tokens have exceeded `1.19M`, and the injector/refiner totals are also substantial. This must be included in final comparison even if strict accuracy improves.

## 2026-05-20 05:12:26 +0800 - Still at index 41; maintenance expansion persists

Command/status checked:
- Waited 300 seconds, then checked process, output files, checkpoint, log role counters, task rows, and log tail.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:32:35`.
- Final result file is still absent.
- Checkpoint still at timestamp `2026-05-20 05:04:52 +0800` and `next_task_index = 41`.
- Log size grew to about `249K`.

Checkpoint state remains:
- `next_task_index = 41`
- `n_train_details = 41`
- `n_micro_maintenance_reports = 41`
- `n_maintenance_windows = 4`
- `n_store_artifacts = 127`
- `n_store_test_results = 50`
- `n_segment_index_rows = 144`
- `n_extraction_events = 124`
- `n_credit_events = 55`
- `n_candidate_group_feedback_state = 23`

Log event counts:
- `bfcl_task_run = 50`
- `maintenance_llm_start = 587`
- `maintenance_llm_done = 586`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 150 / 150`
- `refactorer = 13 / 12`
- `bundle_builder = 9 / 9`
- `skill_injector = 304 / 304`
- `credit_assigner = 31 / 31`
- `refiner = 76 / 76`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 3 / 3`

Role token totals so far:
- `extractor = 1199751`
- `refactorer = 107731`
- `bundle_builder = 64894`
- `skill_injector = 300022`
- `credit_assigner = 204073`
- `refiner = 419450`
- `stale_resolver = 4838`
- `extractor_feedback = 12068`

Task summary from log rows remains:
- Task rows: `50`.
- Success rows: `11 / 50`.
- Official-valid rows: `32 / 50`.
- Average score: `0.818318`.
- Timeout rows: still `1`.

Latest active event:
- A `refactorer` call is active with approximately `user_chars = 16015`, `system_chars = 7365`, `attempt = 1 / 5`.

Interpretation:
- Still no crash or API hang, but the maintenance expansion is substantial. The training checkpoint moved only from `40` to `41` while maintenance calls increased notably.
- This is now a concrete finding for the final TRL tuning writeup: the executor-step gate fixed one repeated-injection source, but micro-maintenance can still explode because every new/refined candidate causes repeated refiner/injector/refactorer work.
- Continue monitoring because the formal full run is still alive and writing logs.

## 2026-05-20 05:17:11 +0800 - Training round completed; formal test/finalization still running

Command/status checked:
- Waited 240 seconds, then checked process, output files, checkpoint, log counters, macro snapshots, and skills output.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:37:19`.
- Final result file `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json` is still absent.
- `skills.json` now exists at `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json`, size about `4.3M`.
- Checkpoint grew sharply to about `248M`, timestamp `2026-05-20 05:15:15 +0800`.
- Log size grew to about `271K`.

Checkpoint structure change:
- My first parser expected `current_round_state` to be a dict and hit a local `AttributeError: 'NoneType' object has no attribute 'get'`.
- This was not an experiment error. Re-parsing showed `current_round_state = None`, `next_round_index = 1`, and `round_reports` length `1`, which means the training round has completed and checkpoint structure moved from in-progress state to completed-round state.

Completed training round summary from `round_reports[0].train_summary`:
- `benchmark = bfcl_v3`
- `mode = related_train_round`
- `tag = bfcl_related50_50_sonnet_trl_injector_gate_20260520`
- `n_train_reserved = 50`
- `n_tasks = 50`
- `n_runs = 50`
- `total_success = 11`
- `success_rate = 0.22`
- `avg_score = 0.8183`
- `avg_total_tokens = 59604.8`
- `avg_input_tokens = 58559.2`
- `avg_output_tokens = 1045.6`

Completed training round token breakdown from checkpoint:
- `n_calls = 594`
- `prompt_tokens = 2080562`
- `completion_tokens = 275560`
- `total_tokens = 2356122`
- `duration_ms = 5892766`
- Key role totals already visible from log: `extractor = 1199751`, `skill_injector = 364079`, `refiner = 419450`, `credit_assigner = 204073`, `refactorer = 125196`, `bundle_builder = 79175`, `extractor_feedback = 21740`, `stale_resolver = 4838`.

Checkpoint formal test state:
- `round_reports[0].test_summary = None`.
- Therefore formal test/finalization is not complete yet, despite training being complete.

Macro snapshots present:
- `round_00_macro_000_skills.json` at `03:45`, size `52969`.
- `round_00_macro_001_skills.json` at `04:03`, size `585928`.
- `round_00_macro_002_skills.json` at `04:29`, size `1877388`.
- `round_00_macro_003_skills.json` at `04:57`, size `3292767`.
- `round_00_macro_004_skills.json` at `05:14`, size `4448702`.

Log event counts:
- `bfcl_task_run = 58`
- `maintenance_llm_start = 640`
- `maintenance_llm_done = 640`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Task summary from log rows so far:
- Task rows: `58`.
- Success rows: `11 / 58`.
- Official-valid rows: `34 / 58`.
- Average score: `0.8071603448275863`.
- Timeout rows: still `1`.

Interpretation:
- The 50-task training phase has completed successfully and produced skills/checkpoint/snapshots.
- Formal test result is still pending; continue monitoring process until final `evolve.json` appears.
- Training strict success is `0.22`, but this is not directly the formal test metric. Final comparison to baseline/SkillX must wait for `test_summary`.
- Checkpoint/state growth is large (`248M` checkpoint, `4.3M` skills), another concrete overhead signal.

## 2026-05-20 05:21:25 +0800 - Formal test is running; about 27/50 test rows observed

Command/status checked:
- Waited 180 seconds, then checked process, output files, checkpoint, log counters, task rows, and log tail.

Observed process state:
- PID `2363638` is alive.
- Elapsed runtime: `01:41:33`.
- Final result file is still absent.
- `skills.json` remains present at about `4.3M`.
- Checkpoint timestamp remains `2026-05-20 05:15:15 +0800`.
- Log size grew to about `306K`.

Checkpoint state:
- `next_round_index = 1`
- `round_reports = 1`
- `round_reports[0].test_summary = None`

Log event counts:
- `bfcl_task_run = 77`
- `maintenance_llm_start = 708`
- `maintenance_llm_done = 705`
- `timeout = 1`
- `Traceback = 0`
- `APIConnectionError = 0`
- `Timeout = 0`
- `maintenance_llm_error = 0`
- `Error = 0`
- `Exception = 0`

Role start/done counts:
- `extractor = 150 / 150`
- `refactorer = 14 / 14`
- `bundle_builder = 11 / 11`
- `skill_injector = 420 / 417`
- `credit_assigner = 31 / 31`
- `refiner = 76 / 76`
- `stale_resolver = 1 / 1`
- `extractor_feedback = 5 / 5`

Task summary from all log rows so far:
- Total task rows: `77`.
- Because training had `50` rows, this implies about `27 / 50` formal test rows are now visible in the log.
- Combined success rows: `13 / 77`.
- Combined official-valid rows: `49 / 77`.
- Combined average score: `0.8066584415584416`.
- Prompt-injected rows across all visible rows: `54 / 77`.
- Timeout rows: still `1`, from the earlier training row.

Recent test observations:
- `multi_turn_base_160` test row succeeded with score `1.0`, official_valid `true`, and prompt skills `travel_budget_requires_currency_conversion_before_set__candidate_r0_t44_s0` plus `travel_booking_requires_all_parameters_from_context__candidate_r0_t44_s0`.
- Most recent visible test rows remain non-strict-success despite partial scores, for example `multi_turn_base_138`, `multi_turn_base_77`, `multi_turn_base_53`, `multi_turn_base_113`, `multi_turn_base_167`, `multi_turn_base_143`, and `multi_turn_base_141`.
- Several rows receive many prompt skills but still miss strict success, e.g. `multi_turn_base_167` received five travel/invoice/cancellation-related prompt skills and scored `0.7692` without strict success.

Interpretation:
- Formal test is progressing and not stuck.
- Interim test trend is weak: only about two additional strict successes are visible after the 50 training rows, though exact test metrics must wait for the final result file.
- Test-time prompt injection is active and costly; `skill_injector` has three active calls at this snapshot.

## 2026-05-20 05:26:11 +0800 - Full 50/50 injector-gate run completed

Command/status checked:
- Waited 240 seconds, then checked process and output files.
- Parsed final result, prior original TRL result, baseline rerun, and SkillX-aligned diagnostic result.

Observed process state:
- PID `2363638` is no longer present in `ps` output.
- Final result file exists: `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json`, size about `403M`.
- Final skills file exists: `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json`, size about `4.3M`.
- Checkpoint file exists: `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_checkpoint.json`, size about `248M`.
- Log file exists: `academic/results/logs/bfcl_related50_50_sonnet_trl_injector_gate_20260520.log`, size about `334K`.
- Log ended with output marker for `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json`.

Final injector-gate 50/50 result:
- Test `n_tasks = 50`
- Test `total_success = 4`
- Test `success_rate = 0.08`
- Test `official_valid_rate = 0.60`
- Test `avg_score = 0.7530`
- Test `avg_total_tokens = 74175.7`
- Test `avg_input_tokens = 73152.7`
- Test `avg_output_tokens = 1023.0`
- Test `avg_elapsed_s = 49.427`
- Test `timeout_rate = 0.0`
- Train `total_success = 11 / 50`
- Train `success_rate = 0.22`
- Train `official_valid_rate = 0.6531`
- Train `avg_score = 0.8183`
- Train `avg_elapsed_s = 46.364`

Comparison to baseline rerun:
- Baseline file: `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json`
- Baseline `success_rate = 0.08`, injector-gate `success_rate = 0.08`: strict success ties baseline, does not beat it.
- Baseline `official_valid_rate = 0.48`, injector-gate `official_valid_rate = 0.60`: injector-gate improves validity by `+0.12` absolute.
- Baseline `avg_score = 0.7388`, injector-gate `avg_score = 0.7530`: injector-gate improves average score by `+0.0142`.
- Baseline `avg_total_tokens = 70551.3`, injector-gate `avg_total_tokens = 74175.7`: injector-gate uses `+3624.4` average tokens per test task.
- Baseline `avg_elapsed_s = 35.555`, injector-gate `avg_elapsed_s = 49.427`: injector-gate is `+13.872s` slower per test task.

Comparison to original ungated TRL 50/50:
- Original TRL file: `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`
- Original TRL `success_rate = 0.08`, injector-gate `success_rate = 0.08`: strict success unchanged.
- Original TRL `official_valid_rate = 0.52`, injector-gate `official_valid_rate = 0.60`: injector-gate improves validity by `+0.08` absolute.
- Original TRL `avg_score = 0.7474`, injector-gate `avg_score = 0.7530`: injector-gate improves average score by `+0.0056`.
- Original TRL `avg_total_tokens = 76689.5`, injector-gate `avg_total_tokens = 74175.7`: injector-gate reduces test task tokens by `-2513.8` average tokens per task.
- Original TRL `avg_elapsed_s = 79.253`, injector-gate `avg_elapsed_s = 49.427`: injector-gate is `-29.826s` faster per task.

Comparison to SkillX-aligned diagnostic:
- SkillX-aligned file: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_test_result.json`
- SkillX-aligned `success_rate = 0.08`, injector-gate `success_rate = 0.08`: strict success ties.
- SkillX-aligned `official_valid_rate = 0.54`, injector-gate `official_valid_rate = 0.60`: injector-gate improves validity by `+0.06` absolute.
- SkillX-aligned `avg_score = 0.7679`, injector-gate `avg_score = 0.7530`: injector-gate is lower by `-0.0149` average score.
- SkillX-aligned `avg_total_tokens = 78787.6`, injector-gate `avg_total_tokens = 74175.7`: injector-gate uses `-4611.9` average tokens per task.
- SkillX-aligned `avg_elapsed_s = 34.927`, injector-gate `avg_elapsed_s = 49.427`: injector-gate is `+14.500s` slower per task.

Final interpretation:
- The injector-gate fix improved speed and score relative to the original ungated TRL run, especially reducing the severe `79.253s` average test latency to `49.427s`.
- It still does not beat baseline strict success. Since strict success remains `0.08`, the next step is not another blind full run; per user instruction, analyze failure cases, tune TRL again, run a small validation, then rerun full 50/50 only if the validation is clean.
- The main suspicious patterns from monitoring are repeated maintenance expansion, large checkpoint/result size, and prompt injection that raises validity/partial score without converting enough tasks to exact strict success.

## 2026-05-20 05:30:00 +0800 - Implemented heldout trial-skill filtering

Code changes made after full 50/50 injector-gate failed to exceed baseline strict success:
- `academic/benchmarks/bfcl/adapter.py`: added `allow_trial_skills` to `_RetrievalPolicy`; when false, trial artifacts are filtered with audit reason `trial_skill_disabled_for_phase`. `run_bfcl_task` now accepts and logs `allow_trial_skills`, then passes it into retrieval.
- `academic/benchmarks/core/runner.py`: added `_run_bfcl_baseline(..., allow_trial_skills=True)` and forwards it into all `run_bfcl_task` calls. Added CLI flag `--bfcl-disable-trial-skills` for direct BFCL baseline/debug runs.
- `academic/benchmarks/bfcl/related/experiment.py`: added `heldout_allow_trial_skills=False` to the related evolve driver. The train rollouts still allow trial candidates for exploration, but the final heldout test now passes `allow_trial_skills=False` by default. Added CLI flag `--heldout-allow-trial-skills` to recover old behavior if needed. The config summary now records `heldout_allow_trial_skills`.

Reasoning:
- The completed 50/50 run had `136` trial artifacts and `176` candidate-named artifacts in the final skills file. Heldout test retrieved skills on `39/50` tasks and injected skills on `35/50` tasks. Many injected skills were still trial candidates and did not convert strict successes.
- Strict success tied baseline because the successful task set almost completely overlapped baseline. Partial score improved, but trial candidates added noise and latency.
- This change keeps candidate exploration during training while preventing unvalidated trial candidates from polluting the formal heldout test.

Verification started:
- `python -m py_compile academic/benchmarks/bfcl/adapter.py academic/benchmarks/core/runner.py academic/benchmarks/bfcl/related/experiment.py`
- Preparing a 5-case heldout debug run on ids `multi_turn_base_152`, `multi_turn_base_153`, `multi_turn_base_78`, `multi_turn_base_194`, `multi_turn_base_160` using the completed 50/50 skills and `--bfcl-disable-trial-skills`.

## 2026-05-20 05:32:00 +0800 - First debug run launch failed before model calls due to id-file format

Attempted command:
- `python -m academic.benchmarks.core.runner ... --test-task-ids academic/experiments/bfcl_case_lists/debug_trial_filter_test_ids_20260520.txt ... --bfcl-disable-trial-skills`

Observed error:
- `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`
- Stack ended in `_load_task_id_list`, which calls `json.loads(path.read_text())`.

Interpretation:
- This was a local validation command input error: `_load_task_id_list` expects a JSON list, but I wrote a newline-delimited text file.
- No model call happened; this does not invalidate the code change.
- Fix: write `academic/experiments/bfcl_case_lists/debug_trial_filter_test_ids_20260520.json` as a JSON array and rerun the same 5-case validation.

## 2026-05-20 05:33:00 +0800 - Second debug launch failed due to core-runner split mismatch

Attempted command:
- Same 5-case validation, now with JSON id list.

Observed error:
- `ValueError: Unknown task ids in academic/experiments/bfcl_case_lists/debug_trial_filter_test_ids_20260520.json: ['multi_turn_base_153', 'multi_turn_base_194', 'multi_turn_base_160']`

Interpretation:
- This is another validation harness issue: `academic.benchmarks.core.runner` loads its own default BFCL train/test split before selecting ids. Some related-manifest heldout ids are not in that default test pool.
- No model call happened.
- Fix: use ids directly from `curated_related_manifest_50_50.json` that are available in the related split, or run through the related proxy runner. For a quick core-runner smoke, I wrote `academic/experiments/bfcl_case_lists/debug_trial_filter_manifest5_20260520.json` from the first five related heldout test ids.

## 2026-05-20 05:36:00 +0800 - 5-case validation reached runtime but failed due to API authentication

Attempted command:
- `python -m academic.benchmarks.core.runner --benchmark bfcl_v3 --mode baseline --test-task-ids academic/experiments/bfcl_case_lists/debug_trial_filter_core_known5_20260520.json --skills academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json --bfcl-disable-trial-skills --test-concurrency 4 ...`

Result:
- Process exited successfully at the Python process level and wrote `academic/results/bfcl_trial_filter_debug5_20260520.json`.
- However every task failed before meaningful BFCL execution because the LLM provider returned authentication errors.
- Representative error: `AuthenticationError: Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}}`.
- Summary in the output: `success_rate = 0.0`, `avg_score = 0.0`, `official_valid_rate = null`, `total_tokens = 0`.

Interpretation:
- This validation did exercise the code path enough to show the new CLI flag and result writing do not crash, but it did not validate task quality because the LLM API key is currently invalid in this shell/config.
- The log also shows injector events with `injected_count = 0`, `filtered_count = 2` for several tasks before executor failure. This is consistent with trial candidates being filtered out, but I still need a clean run or a direct retrieval audit to confirm the exact `trial_skill_disabled_for_phase` filter reason.
- Next action: inspect local LLM config/environment and restore the known key (`1234abcd`, supplied earlier by user) if that is the intended local proxy key, then rerun a short validation.

## 2026-05-20 05:38:32 +0800 - Resume monitoring and verify proxy path

State on resume:
- No active BFCL/SkillX formal experiment process was found in `ps`; the completed BFCL+TRL injector-gate 50/50 and SkillX-aligned files remain the latest formal results.
- One unrelated spreadsheet observation process is still running; I did not interrupt it.
- The failed 5-case trial-filter validation from 05:36 used direct core-runner config and all tasks failed with `invalid x-api-key`, so it remains invalid for quality.

Proxy/key verification:
- `config/config.toml` contains `local_claude_proxy` with `base_url = "http://127.0.0.1:4000/v1"` and `api_key = "1234abcd"`.
- A direct Anthropic Messages request to `http://127.0.0.1:4000/v1/messages` with `x-api-key: 1234abcd` returned HTTP 200 and assistant text `OK`.
- A direct OpenAI chat-completions request to `http://127.0.0.1:4000/v1/chat/completions` returned HTTP 404, confirming that this local proxy should be used through the Anthropic Messages/native-tool path rather than OpenAI chat-completions.

Immediate action:
- Created tiny related manifest `academic/experiments/bfcl_case_lists/debug_trial_filter_related2_20260520.json` with heldout ids `multi_turn_base_68` and `multi_turn_base_152` for a clean, short validation of the new heldout trial-skill filter.

## 2026-05-20 05:39:45 +0800 - Tiny related validation confirmed proxy auth but not trial filtering

Command run:
- `BFCL_PROXY_PORT=4000 BFCL_PROXY_API_KEY=1234abcd BFCL_MICRO_CONCURRENCY=1 python -m academic.benchmarks.bfcl.related.proxy_runner --mode evolve --manifest academic/experiments/bfcl_case_lists/debug_trial_filter_related2_20260520.json --expected-train-size 0 --expected-test-size 2 ... --tag bfcl_trial_filter_related2_20260520`

Observed result:
- The run completed without API authentication errors.
- `multi_turn_base_152`: score `0.7273`, strict success `false`, official valid `false`, elapsed `25.558s`, no injected skills.
- `multi_turn_base_68`: score `0.7778`, strict success `false`, official valid `false`, elapsed `43.376s`, no injected skills.

Interpretation:
- This proves the local proxy and Anthropic Messages/native-tool path are usable with `1234abcd`.
- It does not prove the heldout trial filter because this tiny evolve run used an empty 0-train store, so there were no existing 50/50 skills to retrieve or filter.
- Next action: rerun a short core-runner validation with the completed `bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json` skill store, `--bfcl-disable-trial-skills`, and explicit `--bfcl-tool-api-style anthropic_direct`.

## 2026-05-20 05:42:51 +0800 - Core validation exposed candidate-winner leakage

Command run:
- `ANTHROPIC_API_KEY=1234abcd OPENAI_API_KEY=1234abcd python -m academic.benchmarks.core.runner --benchmark bfcl_v3 --mode baseline --llm-config local_claude_proxy --model-name claude-sonnet-4-5 --n-test 2 --n-runs 1 --test-task-ids academic/experiments/bfcl_case_lists/debug_trial_filter_core2_20260520.json --skills academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json --top-k-skills 3 --skill-injection-mode prompt_only --bfcl-disable-trial-skills --bfcl-execution-backend official --bfcl-prompt-style native --bfcl-tool-api-style anthropic_direct --max-steps-per-turn 20 --max-task-seconds 180 --temperature 0.0 --tag bfcl_trial_filter_core2_20260520 --output academic/results/bfcl_trial_filter_core2_20260520.json`

Observed result:
- No API auth failures and no runtime exceptions.
- Summary: `success_rate = 0.0`, `official_valid_rate = 0.5`, `avg_score = 0.7636`, `avg_total_tokens = 95235.5`, `avg_elapsed_s = 59.512`, `timeout_rate = 0.0`.
- `multi_turn_base_68`: score `0.8`, strict `false`; retrieved 7 skills and prompt-injected `press_brake_before_starting_engine__candidate_r0_t18_s1`.
- `multi_turn_base_152`: score `0.7273`, strict `false`; retrieved `contact_customer_support_concise_message_contract__candidate_r0_t39_s0` and `contact_customer_support_message_fidelity`; prompt-injected none.
- Injector/cost events show trial filtering did fire (`filtered_count = 2/3` in several events), but it did not block active candidate winners.

Critical finding:
- The first tuning only filtered artifacts whose `status == "trial"`.
- The final 50/50 skills file has 180 artifacts: `trial = 136`, `active = 16`, `archived = 23`, `disabled = 4`, `stale = 1`.
- Among active skills, 13 names still contain `__candidate_`. Example: `press_brake_before_starting_engine__candidate_r0_t18_s1` has `status = active`, `metadata.candidate_group_role = alternative`, `metadata.candidate_for_existing_skill = press_brake_before_starting_engine`, and `metadata.competition_status = winner`.
- Therefore `allow_trial_skills=False` is not enough for formal heldout. It blocks unpromoted trial candidates but still allows promoted sample candidates whose names and metadata indicate they are candidate artifacts. This explains why formal heldout can still be polluted even after the trial filter.

Tuning decision:
- Add a second retrieval policy flag for formal heldout: disable candidate artifacts as a class, including names containing `__candidate_` or candidate metadata such as `candidate_group_id`, `candidate_for_existing_skill`, `candidate_original_name`, or `candidate_group_role`.
- Keep candidate retrieval available during training and debugging unless explicitly disabled.
- Related formal heldout default should be strict mature-only: no trial skills and no candidate artifacts unless `--heldout-allow-candidate-skills` is set.

## 2026-05-20 05:46:37 +0800 - Implemented and validated mature-only heldout retrieval

Code changes made:
- `academic/benchmarks/bfcl/adapter.py`: added `allow_candidate_skills` to `_RetrievalPolicy` and `run_bfcl_task`. When false, retrieval rejects artifacts whose name contains `__candidate_` or whose metadata contains candidate markers: `candidate_group_id`, `candidate_for_existing_skill`, `candidate_original_name`, or `candidate_group_role`. Audit reason is `candidate_skill_disabled_for_phase`.
- `academic/benchmarks/core/runner.py`: added CLI flag `--bfcl-disable-candidate-skills` and passed `allow_candidate_skills` through `_run_bfcl_baseline` into every `run_bfcl_task` call.
- `academic/benchmarks/bfcl/related/experiment.py`: added `heldout_allow_candidate_skills=False` to the related evolve driver and CLI flag `--heldout-allow-candidate-skills`. Formal heldout now defaults to mature-only retrieval: no trial skills and no candidate artifacts, while train-time candidate exploration remains unchanged.

Static verification:
- `python -m py_compile academic/benchmarks/bfcl/adapter.py academic/benchmarks/core/runner.py academic/benchmarks/bfcl/related/experiment.py` passed.

Small real validation command:
- `ANTHROPIC_API_KEY=1234abcd OPENAI_API_KEY=1234abcd python -m academic.benchmarks.core.runner --benchmark bfcl_v3 --mode baseline --llm-config local_claude_proxy --model-name claude-sonnet-4-5 --n-test 2 --n-runs 1 --test-task-ids academic/experiments/bfcl_case_lists/debug_trial_filter_core2_20260520.json --skills academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json --top-k-skills 3 --skill-injection-mode prompt_only --bfcl-disable-trial-skills --bfcl-disable-candidate-skills --bfcl-execution-backend official --bfcl-prompt-style native --bfcl-tool-api-style anthropic_direct --max-steps-per-turn 20 --max-task-seconds 180 --temperature 0.0 --tag bfcl_mature_filter_core2_20260520 --output academic/results/bfcl_mature_filter_core2_20260520.json`

Observed result:
- No API auth failures and no runtime exceptions.
- Summary: `success_rate = 0.0`, `official_valid_rate = 0.5`, `avg_score = 0.7446`, `avg_total_tokens = 112266.0`, `avg_elapsed_s = 64.001`, `timeout_rate = 0.0`.
- `multi_turn_base_68`: retrieved `fill_fuel_tank_capacity_error_recovery` and `vehicle_engine_start_preconditions`; prompt-injected both; no `__candidate_` retrieval or injection remained.
- `multi_turn_base_152`: retrieved `contact_customer_support_message_fidelity`; prompt-injected none; no `__candidate_` retrieval remained.

Comparison against trial-only 2-case validation:
- Trial-only run: `avg_score = 0.7636`, `avg_total_tokens = 95235.5`, `avg_elapsed_s = 59.512`; it still retrieved active candidate artifacts and injected `press_brake_before_starting_engine__candidate_r0_t18_s1`.
- Mature-only run: `avg_score = 0.7446`, `avg_total_tokens = 112266.0`, `avg_elapsed_s = 64.001`; it successfully eliminated candidate artifacts, but did not improve strict success in this tiny sample and slightly reduced partial score.

Interpretation:
- The code-level bug is fixed: formal heldout can now exclude both trial and promoted-candidate artifacts.
- The quality signal from 2 cases is mixed and too small for a full 50/50 decision. It validates safety and behavior, not benefit.
- Next action: run a 5-case mature-only validation on a broader set before launching a full formal 50/50 rerun.

## 2026-05-20 05:51:38 +0800 - 5-case mature-only validation is safe but not beneficial

5-case mature-only command:
- `ANTHROPIC_API_KEY=1234abcd OPENAI_API_KEY=1234abcd python -m academic.benchmarks.core.runner --benchmark bfcl_v3 --mode baseline --llm-config local_claude_proxy --model-name claude-sonnet-4-5 --n-test 5 --n-runs 1 --test-task-ids academic/experiments/bfcl_case_lists/debug_mature_filter_core5_20260520.json --skills academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json --top-k-skills 3 --skill-injection-mode prompt_only --bfcl-disable-trial-skills --bfcl-disable-candidate-skills --bfcl-execution-backend official --bfcl-prompt-style native --bfcl-tool-api-style anthropic_direct --max-steps-per-turn 20 --max-task-seconds 180 --temperature 0.0 --test-concurrency 3 --tag bfcl_mature_filter_core5_20260520 --output academic/results/bfcl_mature_filter_core5_20260520.json`

Observed result:
- No authentication failures, runtime exceptions, or timeouts.
- Summary: `success_rate = 0.0`, `official_valid_rate = 0.4`, `avg_score = 0.6556`, `avg_total_tokens = 81470.6`, `avg_elapsed_s = 45.741`, `avg_model_steps = 8.6`.
- Retrieval/injection contained no `__candidate_` artifacts, confirming the new mature-only filter behaves as intended.

Per-case mature-only details:
- `multi_turn_base_68`: score `0.7619`, official valid `true`; retrieved `fill_fuel_tank_capacity_error_recovery`, `vehicle_engine_start_preconditions`; injected both. Errors were mostly extra `displayCarStatus` and one extra `posting_get_login_status`.
- `multi_turn_base_152`: score `0.7273`, official valid `false`; retrieved `contact_customer_support_message_fidelity`; injected none. Errors: extra airport lookup calls and missing `cancel_booking`.
- `multi_turn_base_173`: score `0.8889`, official valid `false`; retrieved `contact_customer_support_message_fidelity`; injected none. Error: missing `close_ticket`.
- `multi_turn_base_176`: score `0.0`, official valid `false`; retrieved `contact_customer_support_message_fidelity`; injected none. Errors: missing `book_flight`, missing `cancel_booking`, and wrong ticket description year (`2026` vs expected `2023`).
- `multi_turn_base_65`: score `0.9`, official valid `true`; retrieved `fill_fuel_tank_capacity_error_recovery`, `vehicle_engine_start_preconditions`; injected `vehicle_engine_start_preconditions`. Errors: extra `posting_get_login_status` and extra `displayCarStatus(option=doors)`.

Same 5-case comparison:
- Baseline rerun: strict `0/5`, valid `0/5`, avg score `0.7724`.
- Original TRL: strict `0/5`, valid `0/5`, avg score `0.7042`; many candidate skills injected.
- Injector-gate TRL: strict `0/5`, valid `1/5`, avg score `0.7265`; candidates still dominate retrieval/injection.
- SkillX-aligned: strict `0/5`, valid `1/5`, avg score `0.7585`; retrieved/injected a compact `flight book with fallback airports` skill for travel tasks.
- Mature-only validation: strict `0/5`, valid `2/5`, avg score `0.6556`; candidate artifacts removed, but score worsened, especially `multi_turn_base_176`.

Interpretation:
- Mature-only heldout filtering is a behaviorally correct safety option but not a quality fix. It removes noisy candidates, but it also removes potentially useful promoted candidate winners because the training pipeline never canonicalizes or robustly validates them before active exposure.
- The next TRL tuning should not be a blanket candidate ban. The root problem is earlier: promotion/credit permits candidate artifacts with weak or harmful evidence to become active, and some mature non-candidate skills are too broad or irrelevant.
- I will inspect the active candidate promotion evidence and compare against SkillX's compact travel skill to decide a targeted TRL/promotion fix before any full 50/50 rerun.

## 2026-05-20 05:54:21 +0800 - Implemented targeted candidate promotion gate

Root cause found in code:
- `_apply_candidate_group_competition_decisions` promoted a group winner directly to `status = active` whenever that candidate had the highest score inside its candidate group.
- There was no absolute evidence requirement. A candidate could become active with `winner_score = 0`, `helpful_count = 0`, and no bundle/refinement history, simply because all alternatives were equally unsupported.
- This explains active candidate artifacts with empty `refinement_history` and no positive evidence, including `verify_balance_before_withdrawal__candidate_r0_t4_s0`, `order_details_reuses_prior_order_id__candidate_r0_t13_s1`, `reuse_order_id_from_prior_place_order_result__candidate_r0_t16_s0`, and `contact_customer_support_concise_message_contract__candidate_r0_t39_s0`.

Code change:
- In `academic/benchmarks/bfcl/related/experiment.py`, candidate competition now computes promotion with explicit thresholds:
  - `BFCL_CANDIDATE_PROMOTION_MIN_SCORE`, default `4`.
  - `BFCL_CANDIDATE_PROMOTION_MIN_HELPFUL`, default `1`.
  - `BFCL_CANDIDATE_PROMOTION_REQUIRE_POSITIVE_MARGIN`, default `true`, requiring helpful evidence to exceed harmful evidence.
- Winners below threshold are marked `winner_not_promoted` with `promotion_state = winner_below_promotion_threshold` and `is_promoted = false`; they remain trial/pending instead of becoming active.
- Winners above threshold are recorded as `winner_promoted` with helpful/harmful counts and threshold metadata.
- Heldout behavior is adjusted to rely on this better training-time promotion: formal heldout still disables trial skills by default, but allows candidate artifacts that passed the promotion gate by default.

Offline replay against the completed injector-gate 50/50 candidate feedback:
- Would promote 9 candidate-winner rows with evidence, e.g. `cancel_order_reuses_prior_order_id__candidate_r0_t6_s1` (`score=10`, helpful=3, harmful=1), `vehicle_engine_start_requires_locked_doors__candidate_r0_t17_s1` (`score=8`, helpful=2, harmful=0), `press_brake_before_starting_engine__candidate_r0_t18_s1` (`score=20`, helpful=5, harmful=0), and `vehicle_parking_brake_engage_before_engine_start_confirmation__candidate_r0_t24_s1` (`score=4`, helpful=1, harmful=0).
- Would block 9 weak candidate-winner rows, including zero-evidence winners and low-score winners: `verify_balance_before_withdrawal__candidate_r0_t4_s0`, `order_details_reuses_prior_order_id__candidate_r0_t13_s1`, `reuse_order_id_from_prior_place_order_result__candidate_r0_t16_s0`, `estimate_drive_feasibility_before_trip_planning__candidate_r0_t22_s0`, `estimate_drive_feasibility_uses_exact_distance_from_estimate_distance__candidate_r0_t23_s0`, `vehicle_engine_start_after_door_lock_no_redundant_start__candidate_r0_t26_s2`, `estimate_drive_feasibility_uses_full_distance_not_partial__candidate_r0_t28_s0`, and `contact_customer_support_concise_message_contract__candidate_r0_t39_s0`.

Static verification:
- `python -m py_compile academic/benchmarks/bfcl/adapter.py academic/benchmarks/core/runner.py academic/benchmarks/bfcl/related/experiment.py` passed.

Next action:
- Run a small evolve validation with the promotion gate. If it is clean and not obviously worse, proceed to formal 50/50; if it is worse or unstable, inspect logs before full rerun.

## 2026-05-20 05:56:00 +0800 - Launching small evolve validation for promotion gate

Validation plan:
- Create a 20 train / 10 heldout manifest from `curated_related_manifest_20_50.json`: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_10_promotion_gate_20260520.json`.
- Keep the same core settings as the previous 20/50 and 50/50 injector-gate runs: candidate competition enabled, `candidate_sample_count = 3`, top-k `2`, prompt-only skill injection, official backend, native prompt, tool API auto/Anthropic, max steps `20`, max task seconds `180`, train window concurrency `4`, test concurrency `4`, micro concurrency `4`.
- The target is not to claim final quality; it is to verify the promotion gate runs end-to-end, blocks zero-evidence winners, preserves evidenced winners, writes checkpoints/skills, and does not introduce runtime errors before deciding on a full 50/50 rerun.

## 2026-05-20 06:09:44 +0800 - Promotion-gate 20/10 validation completed

Run command:
- `BFCL_PROXY_PORT=4000 BFCL_PROXY_API_KEY=1234abcd BFCL_MICRO_CONCURRENCY=4 ANTHROPIC_API_KEY=1234abcd OPENAI_API_KEY=1234abcd python -m academic.benchmarks.bfcl.related.proxy_runner --mode evolve --manifest academic/experiments/bfcl_case_lists/curated_related_manifest_20_10_promotion_gate_20260520.json --expected-train-size 20 --expected-test-size 10 --epochs 1 --rounds 1 --micro-maintenance-step 1 --macro-maintenance-step 10 --test-concurrency 4 --train-window-concurrency 4 --enable-candidate-competition --candidate-sample-count 3 --candidate-group-min-usage 1 --candidate-group-low-usage-macros 3 --top-k-skills 2 --skill-injection-mode prompt_only --execution-backend official --prompt-style native --tool-api-style auto --max-steps-per-turn 20 --max-task-seconds 180 --temperature 0.0 --output-detail-level compact --tag bfcl_trl_promotion_gate_20_10_20260520 --save-skills academic/results/bfcl_trl_promotion_gate_20_10_20260520_skills.json --checkpoint academic/results/bfcl_trl_promotion_gate_20_10_20260520_checkpoint.json --macro-snapshot-dir academic/results/macro_snapshots/bfcl_trl_promotion_gate_20_10_20260520 --output academic/results/bfcl_trl_promotion_gate_20_10_20260520_evolve.json`

Artifacts:
- Result: `academic/results/bfcl_trl_promotion_gate_20_10_20260520_evolve.json`, about `93M`.
- Skills: `academic/results/bfcl_trl_promotion_gate_20_10_20260520_skills.json`, about `331K`.
- Checkpoint: `academic/results/bfcl_trl_promotion_gate_20_10_20260520_checkpoint.json`, about `51M`.
- Log: `academic/results/logs/bfcl_trl_promotion_gate_20_10_20260520.log`, about `57K`.
- Macro snapshots were written under `academic/results/macro_snapshots/bfcl_trl_promotion_gate_20_10_20260520/`.

Health:
- No authentication failures, no task-level timeout, no Python exception.
- Train summary: strict success `9/20 = 0.45`, avg score `0.9016`.
- Test summary: strict success `1/10 = 0.10`, official valid `2/10 = 0.20`, avg score `0.7570`, avg total tokens `76730.9`, avg elapsed `37.864s`.

Promotion-gate behavior:
- Final store statuses: `trial = 28`, `archived = 5`, `active = 2`.
- Candidate statuses: `trial = 28`, `archived = 5`; no candidate artifact became active in this 20-train validation.
- Two candidate group winners were explicitly blocked:
  - `verify_balance_before_withdrawal__candidate_r0_t4_s2`: `winner_not_promoted`, score `0`, helpful `0`, harmful `0`.
  - `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s2`: `winner_not_promoted`, score `4`, helpful `0`, harmful `0`; it had neutral evidence only, so it failed the `min_helpful=1` gate.
- The only active skills were non-candidate refactored/canonical skills: `inline_symbol_resolution_for_explicit_company_names` and `skip_symbol_lookup_for_known_companies`.

Same 10 heldout comparison:
- Baseline rerun: strict `1/10`, valid `1/10`, avg score `0.7433`, avg tokens `78591.3`, avg elapsed `32.981s`.
- Original TRL 20/50 on same ids: strict `1/10`, valid `3/10`, avg score `0.7916`, avg tokens `84942.5`, avg elapsed `73.448s`.
- Injector-gate 20/50 on same ids: strict `2/10`, valid `3/10`, avg score `0.8005`, avg tokens `85281.0`, avg elapsed `49.425s`.
- Injector-gate 50/50 on same ids: strict `1/10`, valid `3/10`, avg score `0.7566`, avg tokens `83663.0`, avg elapsed `53.418s`.
- SkillX aligned on same ids: strict `1/10`, valid `2/10`, avg score `0.7652`, avg tokens `83885.2`, avg elapsed `34.039s`.
- Promotion-gate 20/10: strict `1/10`, valid `2/10`, avg score `0.7570`, avg tokens `76730.9`, avg elapsed `37.864s`.

Interpretation:
- The targeted promotion gate fixed a real mechanism bug and prevents zero/neutral-evidence candidate winners from becoming active.
- However, with only 20 training tasks and heldout trial filtering, it becomes too conservative: most heldout tasks receive no skill context, so the quality becomes close to baseline rather than improving like the previous 20/50 injector-gate run.
- This is safer but not yet enough to justify a full 50/50 as the final method. The next adjustment should preserve this evidence gate but add a low-risk way to expose non-promoted candidates to the injector without treating them as active skills, or lower the promotion threshold only for high-retrieval + neutral candidates. The data here suggests a blanket full 50/50 rerun would likely be inconclusive.

Additional code correction:
- I also fixed candidate loser metadata in `academic/benchmarks/bfcl/related/experiment.py`: losers now get `promotion_state = competition_loser` and `is_promoted = false`, instead of retaining the initial trial-time `is_promoted = true` marker that confused analysis.
- `python -m py_compile academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/adapter.py academic/benchmarks/core/runner.py` passed after this correction.

## 2026-05-20 06:14:38 +0800 - Second TRL tuning plan after promotion-gate validation

Current state check:
- No obvious BFCL/SkillX experiment process is still running; only long-lived services and unrelated interactive Python processes are visible.
- The completed promotion-gate 20/10 validation is clean but does not justify a full 50/50 run: heldout strict `1/10`, valid `2/10`, avg score `0.7570`, close to baseline and below earlier injector-gate 20/50 on the same 10 heldout ids.

Root cause refinement:
- The promotion gate fixed the dangerous bug where weak candidate winners became active.
- The new failure mode is recall: final store has many `trial` candidate skills, but formal heldout defaults to `heldout_allow_trial_skills = false`, so most heldout tasks see no candidate skill context.
- Directly enabling all trial skills would be too blunt because `trial` candidates would compete with active skills as if trusted, and blocked winners such as `verify_balance_before_withdrawal__candidate_r0_t4_s2` and `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s2` could re-enter retrieval despite failing the evidence gate.

Targeted tuning to implement now:
- Add a two-lane BFCL retrieval policy: trusted active retrieval remains unchanged; optional low-trust trial/pending retrieval contributes a small supplement, e.g. one extra candidate when `top_k = 2`.
- Keep below-threshold candidate winners out of the low-trust lane by default.
- Mark low-trust prompt blocks explicitly so the executor sees them as unvalidated hints rather than trusted local rules.
- Expose CLI/env knobs for heldout experiments: low-trust trial budget and pending inclusion. Defaults remain conservative, so existing runs are not silently changed.
- Validate with a small 20/10 run before any 50/50 full run.

## 2026-05-20 06:23:00 +0800 - Implemented low-trust trial supplement for BFCL heldout

Code changes made:
- `academic/benchmarks/core/skill_injector.py`
  - `compact_skill_prompt_block` now prints `trust: unvalidated hint; use only if it exactly matches this turn, otherwise ignore` when `artifact.metadata["executor_low_trust_hint"]` is true.
  - `full_skill_prompt_block` appends a low-trust warning to full prompt blocks for such artifacts.
  - The LLM injector candidate payload now includes `trust.low_trust_hint`, `trust.reason`, and `trust.promotion_state`.
  - The injector system prompt now says low-trust hints are unvalidated candidates and should only be selected for exact matches.
  - If the LLM injector fails, it falls back to retrieved order instead of dropping all candidate context. This was exposed by unit tests using `llm_config="unused"`; in real experiments it prevents API hiccups from silently erasing all retrieved skills.
- `academic/benchmarks/bfcl/adapter.py`
  - `_RetrievalPolicy` now has `trial_supplement_k` and `include_pending_supplement`.
  - Normal trusted retrieval excludes trial/pending when supplement is enabled, so active skills still occupy the trusted top-k lane.
  - A second low-trust supplement retrieval can add up to `trial_supplement_k` trial skills, plus pending skills only if explicitly enabled.
  - Candidates with `promotion_state` in `winner_below_promotion_threshold`, `competition_loser`, or `revoked` are filtered from the low-trust lane by default.
  - Supplemented skills are deep-copied and tagged with `executor_low_trust_hint = true` and `executor_low_trust_reason = trial_or_pending_supplement`.
  - Retrieval audit now includes `low_trust_supplement` and `selected_with_supplement` for debugging.
- `academic/benchmarks/core/runner.py`
  - Added CLI flags `--bfcl-trial-supplement-k` and `--bfcl-include-pending-supplement`.
  - `_run_bfcl_baseline` passes these through to `run_bfcl_task` in both serial and concurrent paths.
- `academic/benchmarks/bfcl/related/experiment.py`
  - Added heldout-only knobs `--heldout-trial-supplement-k` and `--heldout-include-pending-supplement`.
  - Related heldout test passes these values through to `_run_bfcl_baseline`.
  - Config summary records `heldout_trial_supplement_k` and `heldout_include_pending_supplement`.
- Tests added:
  - Low-trust prompt blocks are explicitly marked.
  - LLM injector failure falls back to retrieved order.
  - BFCL trial supplement retrieves a trial skill while `allow_trial_skills=false` and marks it low trust before injection.

Verification:
- `python -m py_compile academic/benchmarks/core/skill_injector.py academic/benchmarks/bfcl/adapter.py academic/benchmarks/core/runner.py academic/benchmarks/bfcl/related/experiment.py` passed.
- Focused tests passed: `pytest -q academic/benchmarks/tests/test_skill_injector_budget.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_run_bfcl_task_error_feedback_uses_step_start_context_update academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_trial_supplement_marks_trial_skill_low_trust` -> 11 passed.
- Full `test_skill_injector_budget.py + test_benchmark_adapters.py` still has unrelated pre-existing failures: one Spreadsheet cost-event order assertion and one missing historical BFCL fixture at `academic/results/bfcl_v3_glm47_official_tracecheck_evolve_3x3_partial_train.json`. These are recorded but not treated as blockers for the BFCL supplement validation.

Next action:
- Run the same 20/10 promotion-gate validation with `--heldout-trial-supplement-k 1` and no pending supplement. If it improves without noise, consider full 50/50; if not, inspect logs and do not launch 50/50.

## 2026-05-20 06:19:00 +0800 - Launched 20/10 validation with heldout trial supplement

Command tag: `bfcl_trl_trial_supplement_20_10_20260520`.

Key settings:
- Same manifest as promotion-gate 20/10: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_10_promotion_gate_20260520.json`.
- Train/test: `20/10`.
- Candidate competition: enabled, sample count `3`.
- Concurrency: train window `4`, test `4`, micro `4`.
- Executor: official backend, native prompt, tool API auto, max steps `20`, max task seconds `180`.
- New setting under validation: `--heldout-trial-supplement-k 1`, with pending supplement disabled.

Artifacts:
- Launcher log: `academic/results/logs/bfcl_trl_trial_supplement_20_10_20260520.launcher.log`.
- Result: `academic/results/bfcl_trl_trial_supplement_20_10_20260520_evolve.json`.
- Skills: `academic/results/bfcl_trl_trial_supplement_20_10_20260520_skills.json`.
- Checkpoint: `academic/results/bfcl_trl_trial_supplement_20_10_20260520_checkpoint.json`.
- Macro snapshots: `academic/results/macro_snapshots/bfcl_trl_trial_supplement_20_10_20260520/`.

Initial health check:
- PID `4005252` is running.
- Launcher log is still empty at the first 10-second check; continue monitoring instead of exiting.

## 2026-05-20 06:21:53 +0800 - Monitoring trial-supplement validation, first train window

Observation:
- PID `4005252` still running after about 2 minutes.
- First 10 rollout tasks completed with no task timeout, no auth failure, and no Python exception.
- First-window rollout scores include several valid/correct cases: e.g. `multi_turn_base_130` strict success, `multi_turn_base_116` strict success, `multi_turn_base_123` strict success, `multi_turn_base_128` strict success.
- Prompt-injected skills are empty in this first window, which is expected because the store starts empty.
- Extractor calls started with prompt token counts around `6.6k-7.5k`, completion tokens `17`, and response chars `33` for many calls. This likely means empty extraction responses for many tasks. This is not immediately a runtime bug because earlier windows can legitimately yield no extracted skill, but it is a recall risk to inspect after checkpoint/skills appear.

Health:
- No `maintenance_llm_error` lines seen in the tailed log.
- No timeout or exception lines seen.
- Continue monitoring; do not stop.

## 2026-05-20 06:23:51 +0800 - First checkpoint inspection for trial-supplement validation

Checkpoint sidecars appeared:
- `academic/results/bfcl_trl_trial_supplement_20_10_20260520_checkpoint.json`, about `1.1M` at this point.
- `academic/results/bfcl_trl_trial_supplement_20_10_20260520_checkpoint_current_round_store.json`, about `55K`.
- Current round detail/segment/overlap sidecars also appeared.

Store state from `checkpoint_current_round_store.json`:
- `n_artifacts = 9`.
- Status distribution: `trial = 9`.
- All 9 are candidate-group artifacts.
- Current candidates are TradingBot skills from early tasks:
  - `verify_balance_before_withdrawal__candidate_r0_t4_s0/s1/s2`.
  - `display_watchlist_after_add_to_watchlist__candidate_r0_t7_s0/s1/s2`.
  - `skip_symbol_lookup_when_adding_to_watchlist_with_display__candidate_r0_t7_s0`.
  - `skip_symbol_lookup_when_adding_known_company_to_watchlist__candidate_r0_t7_s1/s2`.

Correction to my previous live note:
- My first parser incorrectly treated the sidecar as empty because of Python expression precedence. The store is not empty; it has 9 trial candidates.

Interpretation:
- The low-trust supplement has something to expose during heldout if retrieval matches, but these early candidates are all TradingBot. Need to see whether the second window adds Vehicle/Travel candidates before judging recall.
- Continue monitoring. No runtime errors observed so far.

## 2026-05-20 06:26:06 +0800 - First macro snapshot and second-window monitoring

Macro snapshot:
- `academic/results/macro_snapshots/bfcl_trl_trial_supplement_20_10_20260520/round_00_macro_000_meta.json` exists.
- It covers train task indices `0-9`.
- `skill_count = 10`, `n_active = 1`, `n_pending = 0`, `n_disabled = 0`.
- `candidate_group_feedback_rows = 0` in the macro summary, so candidate competition has not yet made evidence-based group decisions for the first-window candidates.

Current store state:
- `n_artifacts = 10`.
- Status distribution: `trial = 9`, `active = 1`.
- Active skill: `tradingbot_explicit_company_name_to_symbol_binding`.
- Trial candidates remain the TradingBot balance/watchlist candidates from the first window.

Second training window:
- It is running.
- `multi_turn_base_146` strict succeeded with prompt-injected skill `tradingbot_explicit_company_name_to_symbol_binding`, showing the active canonical skill is being retrieved and injected in training.
- Several other second-window tasks have completed with no prompt skills.

Health:
- No auth failures, no retry storms, no task timeouts, no Python exceptions observed.
- Skill injector calls are active and token sizes are moderate (`~1.2k-1.6k` total tokens per injector call), not a token explosion.

Risk noted:
- If candidate-group feedback rows remain zero, promotion will still be conservative and heldout quality may depend mostly on the low-trust supplement rather than matured active skills. This is exactly what the validation is meant to measure.

## 2026-05-20 06:28:27 +0800 - Second window completed, maintenance running

Progress:
- PID `4005252` is still running.
- Second train window rollout appears complete: task indices `0-9` for that window finished, including `multi_turn_base_118`, `132`, `122`, `146`, `126`, `115`, `127`, `93`, `70`, `82`.
- `multi_turn_base_146` used prompt-injected skill `tradingbot_explicit_company_name_to_symbol_binding` and strict succeeded.
- Several later tasks were weaker (`multi_turn_base_93`, `70`, `82` official invalid), so maintenance has failure evidence to process.

Maintenance:
- `credit_assigner` ran and completed once with response chars `3983`, total tokens `5291`.
- Extractor calls in the second window include both empty responses (`response_chars=33`) and several substantive responses (`~4.9k-8.5k chars`), so extraction is not fully collapsed.
- No auth failures, no task timeouts, no Python exceptions observed.

Current checkpoint:
- Checkpoint grew to about `1.5M` and current round details to about `25.8M`.
- Store sidecar still shows `10` artifacts at the latest checkpoint checked, likely before final maintenance commit for the second window.

Next:
- Continue waiting for final macro and heldout test. Do not start 50/50 until this 20/10 result is parsed and compared.

## 2026-05-20 06:30:50 +0800 - Final macro snapshot, entering heldout test

Macro snapshot 1:
- `round_00_macro_001_meta.json` exists.
- It covers train task indices `10-19` and completes `20` train tasks total.
- `skill_count = 31`, `n_active = 2`, `n_pending = 0`, `n_disabled = 0`.
- `candidate_group_feedback_rows = 2`, so candidate competition feedback finally appeared after the second window.

Process state:
- PID `4005252` still running.
- The current round sidecar files disappeared between checks, which is expected after checkpoint/current-round cleanup or finalization.
- Log has moved into heldout `skill_injector` calls with smaller prompts (`~0.8k-1.0k` tokens), likely testing the final store.

Health:
- No runtime errors, no timeout, no auth failures.
- Token usage still moderate for heldout injector calls.

Next:
- Wait for heldout `bfcl_task_run` lines and final result file, then compare against prior same-10 results.

## 2026-05-20 06:32:49 +0800 - Heldout test in progress, low-trust supplement is active

Observed heldout progress:
- Heldout task results have started appearing for the same 10 ids.
- `multi_turn_base_173`: score `0.8889`, official invalid, no prompt skills.
- `multi_turn_base_152`: score `0.7273`, official invalid, no prompt skills.
- `multi_turn_base_176`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_52`: score `0.7143`, official invalid, prompt-injected `vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s0` and `vehicle_tire_shop_navigation_for_low_pressure__candidate_r0_t17_s2`.
- `multi_turn_base_65`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_68`: score `0.6667`, official invalid, prompt-injected `vehicle_fuel_check_unnecessary_before_fill_to_capacity__candidate_r0_t19_s1`, `press_brake_before_starting_engine__candidate_r0_t18_s0`, and `navigate_to_tire_shop_when_pressure_low__candidate_r0_t18_s0`.
- `multi_turn_base_86`: score `0.9412`, official valid, prompt-injected `vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s0` and `vehicle_tire_shop_navigation_for_low_pressure__candidate_r0_t17_s1`.
- `multi_turn_base_88`: score `0.5882`, official invalid, no prompt skills.
- `multi_turn_base_108`: strict success and official valid, prompt-injected `tradingbot_direct_symbol_binding`.

Interpretation before final parse:
- The low-trust supplement is definitely being used: vehicle candidate skills are entering heldout prompts.
- Early signal is mixed or bad: multiple vehicle cases with candidate injections are still failing, and some have too many candidate hints per task despite `heldout_trial_supplement_k=1` because retrieval happens per turn and accumulates across turns.
- This means the recall problem was fixed mechanically, but the candidate quality/selection problem remains. I should not launch 50/50 unless the final aggregate unexpectedly improves clearly.

Health:
- No auth failures, no timeout, no exceptions.


## 2026-05-20 06:36:14 +0800 - Parsed trial-supplement 20/10 validation

Final parsed result for `bfcl_trl_trial_supplement_20_10_20260520`:
- Train summary: strict success `9/20 = 0.45`, avg score `0.9016`, avg total tokens `47019.1`.
- Heldout summary: strict success `1/10 = 0.10`, official valid `3/10 = 0.30`, avg score `0.7793`, avg total tokens `83017.5`, avg elapsed about `45.575s` from detail-level average.
- Heldout low-trust supplement was active. It injected trial/candidate vehicle skills into `multi_turn_base_52`, `multi_turn_base_68`, and `multi_turn_base_86`, and the active TradingBot skill into `multi_turn_base_108` and `multi_turn_base_131`.

Comparison on the same 10 heldout ids:
- Baseline rerun: strict `1/10`, valid `1/10`, avg score `0.7433`.
- Original TRL 20/50 same ids: strict `1/10`, valid `3/10`, avg score `0.7916`.
- Injector-gate 20/50 same ids: strict `2/10`, valid `3/10`, avg score `0.8005`.
- Injector-gate 50/50 same ids: strict `1/10`, valid `3/10`, avg score `0.7566`.
- SkillX aligned same ids: strict `1/10`, valid `2/10`, avg score `0.7652`.
- Promotion-gate 20/10: strict `1/10`, valid `2/10`, avg score `0.7570`.
- Trial-supplement 20/10: strict `1/10`, valid `3/10`, avg score `0.7793`.

Interpretation:
- The latest fix improves over the too-conservative promotion-gate validation and beats the baseline and SkillX diagnostic on average score for this exact 10-case slice, but it still does not beat the earlier injector-gate 20/50 validation and does not improve strict success.
- The low-trust supplement fixed the mechanical recall issue: candidate skills can enter heldout prompts before promotion.
- The remaining issue is quality/selection: injected vehicle candidates often raise partial score but do not produce strict correctness, and per-turn retrieval can accumulate multiple low-trust hints inside one multi-turn task even when `heldout_trial_supplement_k = 1`.
- Decision: do not launch full 50/50 from this state. Run a focused case study on `multi_turn_base_52`, `multi_turn_base_68`, and `multi_turn_base_86`, then tune TRL/prompting/retrieval based on concrete failure evidence.


## 2026-05-20 06:41:56 +0800 - Trial-supplement case study and targeted TRL retrieval fix

Case study from `bfcl_trl_trial_supplement_20_10_20260520`:
- `multi_turn_base_52`: retrieved/injected `vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s0` and `vehicle_tire_shop_navigation_for_low_pressure__candidate_r0_t17_s2`; score `0.7143`, official invalid. Vehicle start turn was correct. Tire turn expected `check_tire_pressure()` and `find_nearest_tire_shop()`, but executor also called `set_navigation(...)`, producing an extra-call error. The candidate body included `find_nearest_tire_shop` plus `set_navigation`, so it was too strong for a turn that asked to find/guide to a facility but whose gold expected navigation was not yet a separate GPS action.
- `multi_turn_base_68`: retrieved/injected `vehicle_fuel_check_unnecessary_before_fill_to_capacity__candidate_r0_t19_s1`, `press_brake_before_starting_engine__candidate_r0_t18_s0`, and `navigate_to_tire_shop_when_pressure_low__candidate_r0_t18_s0`; score `0.6667`, official invalid. Errors included an initial bad `fillFuelTank(fuelAmount=50)` extra call, extra `displayCarStatus`, extra `startEngine`, and premature `set_navigation` in the pressure-check turn followed by missing `set_navigation` in the later GPS turn. This is the clearest evidence that low-trust candidates can raise partial recall while harming strict correctness by pulling future-turn actions into the current turn.
- `multi_turn_base_86`: retrieved/injected vehicle start and tire navigation candidates; score `0.9412`, official valid but not strict. Vehicle and navigation turns were correct; only extra `posting_get_login_status` in tweet turn prevented strict success. This shows the candidate content can be useful when exactly aligned, so simply disabling all low-trust supplement would discard useful signal.

Root cause:
- `bfcl_skill_matches_turn` only checked task/domain scope and forbid keywords. For low-trust trial/pending candidates this is too weak because extracted metadata can be broad, especially `allowed_tools` covering nearly the full VehicleControlAPI.
- `heldout_trial_supplement_k=1` applied per turn, so a multi-turn task could accumulate several low-trust candidate hints even when the operator intended one low-trust supplement.

Code changes made:
- `academic/benchmarks/bfcl/retrieval.py`: added low-trust matching helpers `_text_tokens`, `_skill_scope_text`, `_output_contract_tool_names`, `low_trust_turn_match_reason`, and `bfcl_low_trust_skill_matches_turn`. These require low-trust candidates to have current-turn evidence from tool overlap or intent/scope token overlap. Candidates whose output contract includes `set_navigation` are rejected unless the current turn explicitly contains navigation/GPS/route/directions-like intent.
- `academic/benchmarks/bfcl/adapter.py`: imported `low_trust_turn_match_reason` and routed trial/pending supplement candidates through it. Added `low_trust_total_limit` and `low_trust_selected_names` to `_RetrievalPolicy`; default total low-trust supplement limit is one per task, preventing per-turn accumulation.
- `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`: added `test_bfcl_low_trust_navigation_requires_current_navigation_intent` and `test_bfcl_trial_supplement_caps_low_trust_candidates_per_task`.
- `academic/benchmarks/bfcl/retrieval.py`: exported `low_trust_turn_match_reason` and `bfcl_low_trust_skill_matches_turn`.

Verification:
- `python -m py_compile academic/benchmarks/bfcl/retrieval.py academic/benchmarks/bfcl/adapter.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py` passed.
- Focused pytest passed: `pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_low_trust_navigation_requires_current_navigation_intent academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_trial_supplement_marks_trial_skill_low_trust academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_trial_supplement_caps_low_trust_candidates_per_task academic/benchmarks/tests/test_skill_injector_budget.py::test_low_trust_skill_prompt_blocks_are_explicitly_marked` -> `4 passed`.

Decision:
- This is a real TRL-side fix based on observed underperformance relative to baseline/injector-gate. Next step is a small 20/10 validation with the same manifest and `--heldout-trial-supplement-k 1`, then inspect whether `multi_turn_base_52/68` no longer get premature navigation/action hints.


## 2026-05-20 06:42:40 +0800 - Launched low-trust-gate 20/10 validation

Started validation run after the targeted low-trust retrieval fix.
- Tag: `bfcl_trl_lowtrust_gate_20_10_20260520`.
- PID: `60166`.
- Same manifest as prior 20/10 checks: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_10_promotion_gate_20260520.json`.
- Same core settings: 20 train / 10 heldout, candidate competition enabled, candidate sample count `3`, micro/macro maintenance `1/10`, train/test/micro concurrency `4/4/4`, top-k `2`, prompt-only skills, official backend, native prompt, max steps per turn `20`, max task seconds `180`, temperature `0.0`.
- Heldout setting: `--heldout-trial-supplement-k 1`; pending supplement remains disabled.
- Artifacts: `academic/results/bfcl_trl_lowtrust_gate_20_10_20260520_evolve.json`, `academic/results/bfcl_trl_lowtrust_gate_20_10_20260520_skills.json`, `academic/results/bfcl_trl_lowtrust_gate_20_10_20260520_checkpoint.json`, `academic/results/macro_snapshots/bfcl_trl_lowtrust_gate_20_10_20260520/`, and launcher log `academic/results/logs/bfcl_trl_lowtrust_gate_20_10_20260520.launcher.log`.

Monitoring targets:
- Verify no auth errors, timeouts, or Python exceptions.
- Check whether `multi_turn_base_52` and `multi_turn_base_68` stop receiving premature `set_navigation`-style low-trust hints.
- Compare strict/valid/avg_score/tokens against baseline, promotion-gate, trial-supplement, injector-gate, and SkillX same-10 slices before deciding on any 50/50 run.


## 2026-05-20 06:44:10 +0800 - Low-trust-gate validation first train window

PID `60166` is still running.
First 10 train tasks completed:
- Strict successes: `multi_turn_base_130`, `multi_turn_base_116`, `multi_turn_base_123`, `multi_turn_base_128`.
- Official valid on all except `multi_turn_base_121`; `multi_turn_base_135` is now official valid with score `0.9231` in this run.
- Prompt/tool injected skills are empty, expected for the empty-store first window.
- Extractor maintenance has started with four concurrent calls around `11k-14k` user chars and `12.3k` system chars.

Health:
- No auth failure, no timeout, no Python traceback, no maintenance LLM error observed.
- Continue waiting for first macro snapshot and second train window.


## 2026-05-20 06:47:43 +0800 - Low-trust-gate validation first maintenance still running

PID `60166` remains active after about five minutes.
Current state:
- First-window maintenance progressed through extractor, refactorer, bundle_builder, and several skill_injector calls.
- Current store sidecar has `4` trial artifacts: three `verify_balance_before_withdrawal` candidates and one `cancel_order_reuses_prior_order_id` candidate.
- No macro snapshot file was readable yet at the time checked.

Health:
- No `maintenance_llm_error`, traceback, timeout, auth failure, 401/429, or task-level error observed.
- Token sizes are normal for this pipeline: extractor around `6.6k-7.7k` total tokens per call, refactorer around `7.6k-8.4k`, bundle_builder around `7.1k`, skill_injector around `1.0k`.
- Continue waiting for second train window; do not stop while process is running.


## 2026-05-20 06:51:00 +0800 - Low-trust-gate validation entered second train window

PID `60166` remains active and second training window has started.
First macro snapshot:
- `round_00_macro_000_meta.json` exists.
- `train_tasks_completed = 10`.
- `skill_count = 5`, `n_active = 1`, `n_pending = 0`, `n_disabled = 0`.
- Store status from sidecar: `4` trial artifacts and `1` active artifact.
- Active artifact: `explicit_buy_order_direct_binding`.
- Trial artifacts: three `verify_balance_before_withdrawal` candidates and `cancel_order_reuses_prior_order_id__candidate_r0_t6_s0`.
- `candidate_group_feedback_rows = 0`, same as prior first macro behavior.

Second-window observations so far:
- `multi_turn_base_132`: score `0.8571`, official valid, prompt-injected `cancel_order_reuses_prior_order_id__candidate_r0_t6_s0`.
- `multi_turn_base_122`: strict success, official valid, prompt-injected active `explicit_buy_order_direct_binding` and trial `cancel_order_reuses_prior_order_id__candidate_r0_t6_s0`.
- `multi_turn_base_146`: strict success, official valid, no prompt skills.

Health:
- No runtime errors observed.
- The long first maintenance was active work, not a hang: it produced macro snapshot and entered second train window.
- Continue monitoring through heldout, where the low-trust navigation gate will be tested on `multi_turn_base_52/68/86`.


## 2026-05-20 06:52:40 +0800 - Low-trust-gate validation second train window completed

Second train window completed and final maintenance has started.
Second-window task observations:
- `multi_turn_base_118`: strict/official valid, injected `cancel_order_reuses_prior_order_id__candidate_r0_t6_s0`.
- `multi_turn_base_132`: score `0.8571`, official valid, injected `cancel_order_reuses_prior_order_id__candidate_r0_t6_s0`.
- `multi_turn_base_122`: strict/official valid, injected `explicit_buy_order_direct_binding` and `cancel_order_reuses_prior_order_id__candidate_r0_t6_s0`.
- `multi_turn_base_146`: strict/official valid, no injected skills.
- `multi_turn_base_126`: strict/official valid, injected `cancel_order_reuses_prior_order_id__candidate_r0_t6_s0`.
- `multi_turn_base_115`: score `0.75`, official valid, no injected skills.
- `multi_turn_base_127`: score `0.9091`, official valid, injected `explicit_buy_order_direct_binding`.
- `multi_turn_base_93`: score `0.6667`, official invalid, no injected skills.
- `multi_turn_base_70`: score `0.8235`, official invalid, no injected skills.
- `multi_turn_base_82`: score `0.8235`, official invalid, no injected skills.

Interpretation:
- Training remains healthy. Active and trial TradingBot skills are being used; no evidence yet of runaway low-trust accumulation during training.
- The key heldout check still remains: whether vehicle trial candidates are filtered more conservatively on `multi_turn_base_52/68/86`.

Health:
- No auth failure, timeout, Python traceback, or maintenance LLM error observed.
- Continue waiting through final maintenance and heldout.


## 2026-05-20 06:58:01 +0800 - Low-trust-gate validation final maintenance prolonged but active

PID `60166` remains active.
Observations:
- Final maintenance after the second train window is much longer than the prior trial-supplement run.
- It is not frozen: launcher log and process stats continue changing. Recent stages include many substantive `extractor` responses (`response_chars` up to about `8990`, `7620`, `7853`) and several `refiner` calls.
- Checkpoint grew to about `1.7M`; current round detail sidecar is about `27M`, store sidecar about `495K`.
- Only first macro snapshot exists so far; second macro snapshot has not yet appeared.

Health:
- Still no traceback, auth failure, timeout, or maintenance LLM error.
- Continue waiting; do not interrupt because process is making progress.


## 2026-05-20 07:05:17 +0800 - Low-trust-gate validation reached heldout

The run finally entered heldout test after prolonged final maintenance.
Second macro snapshot appeared:
- `round_00_macro_001_meta.json` exists.
- Result checkpoint grew to about `64M`; skills file about `622K`.

Heldout observations so far:
- `multi_turn_base_52`: score `0.7692`, official valid, prompt-injected only `press_brake_before_starting_engine__candidate_r0_t18_s1`. The previous bad trial-supplement run injected tire-navigation candidates and produced premature `set_navigation`; the low-trust navigation gate appears to have blocked that class of hint here.
- `multi_turn_base_68`: score `0.6667`, official invalid, prompt-injected only `vehicle_fuel_check_before_fill_unnecessary_when_amount_explicit__candidate_r0_t19_s1`. The previous bad trial-supplement run injected a tire-navigation candidate and produced premature `set_navigation`; this run no longer injects that navigation candidate.
- `multi_turn_base_173`: score `0.8889`, official invalid, no prompt skills.
- `multi_turn_base_152`: score `0.7273`, official invalid, no prompt skills.
- `multi_turn_base_176`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_65`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_88`: score `0.5556`, official invalid, no prompt skills.

Interpretation:
- The targeted low-trust navigation gate is mechanically working: it prevents the exact premature navigation injection observed in the previous case study.
- Quality is still mixed; `multi_turn_base_52` improved official validity and score relative to the prior trial-supplement run, but `multi_turn_base_68` remains bad due to fuel/other errors.
- Continue waiting for remaining heldout tasks (`86`, `108`, `131`) and final aggregate.


## 2026-05-20 07:08:57 +0800 - Parsed low-trust-gate 20/10 validation

Run completed cleanly:
- Tag: `bfcl_trl_lowtrust_gate_20_10_20260520`.
- Output: `academic/results/bfcl_trl_lowtrust_gate_20_10_20260520_evolve.json`.
- Skills: `academic/results/bfcl_trl_lowtrust_gate_20_10_20260520_skills.json`.
- Checkpoint: `academic/results/bfcl_trl_lowtrust_gate_20_10_20260520_checkpoint.json`.
- Log: `academic/results/logs/bfcl_trl_lowtrust_gate_20_10_20260520.launcher.log`.
- No traceback, auth failure, task timeout, or maintenance LLM error was observed.

Final heldout aggregate:
- `success_rate = 0.1` (`1/10` strict).
- `official_valid_rate = 0.3` (`3/10` official valid).
- `avg_score = 0.7549`.
- `avg_call_recall = 0.7959`.
- `avg_call_precision = 0.7388`.
- `avg_total_tokens = 82190.9`.
- `avg_input_tokens = 81103.0`.
- `avg_output_tokens = 1087.9`.
- `avg_elapsed_s = 43.482`.
- `timeout_rate = 0.0`.
- `avg_model_steps = 9.4`, `max_model_steps = 16`.

Per-task heldout results:
- `multi_turn_base_68`: score `0.6667`, strict `false`, official valid `false`, prompt skills `vehicle_fuel_check_before_fill_unnecessary_when_amount_explicit__candidate_r0_t19_s1`.
- `multi_turn_base_152`: score `0.7273`, strict `false`, official valid `false`, no prompt skills.
- `multi_turn_base_173`: score `0.8889`, strict `false`, official valid `false`, no prompt skills.
- `multi_turn_base_52`: score `0.7692`, strict `false`, official valid `true`, prompt skills `press_brake_before_starting_engine__candidate_r0_t18_s1`.
- `multi_turn_base_176`: score `0.6667`, strict `false`, official valid `false`, no prompt skills.
- `multi_turn_base_65`: score `0.6667`, strict `false`, official valid `false`, no prompt skills.
- `multi_turn_base_86`: score `0.9412`, strict `false`, official valid `true`, prompt skills `press_brake_before_starting_engine__candidate_r0_t18_s1`.
- `multi_turn_base_88`: score `0.5556`, strict `false`, official valid `false`, no prompt skills.
- `multi_turn_base_108`: score `1.0`, strict `true`, official valid `true`, no prompt skills.
- `multi_turn_base_131`: score `0.6667`, strict `false`, official valid `false`, no prompt skills.

Same-10 comparison:
- Baseline 50/50 rerun same ids: strict `1/10`, valid `1/10`, avg score `0.7433`.
- Promotion-gate 20/10: strict `1/10`, valid `2/10`, avg score `0.7570`.
- Trial-supplement 20/10 before this low-trust gate: strict `1/10`, valid `3/10`, avg score `0.7793`.
- Injector-gate 50/50 same ids: strict `1/10`, valid `3/10`, avg score `0.7566`.
- SkillX-aligned 50/50 same ids: strict `1/10`, valid `2/10`, avg score `0.7652`.
- Low-trust-gate 20/10: strict `1/10`, valid `3/10`, avg score `0.7549`.

Interpretation:
- The low-trust current-turn gate fixed the specific failure mode it targeted. The earlier trial-supplement run injected tire/navigation skills into `multi_turn_base_52` and `multi_turn_base_68`; the new run no longer injects those navigation skills, and `multi_turn_base_52` becomes official-valid.
- The small validation does not justify a full 50/50 rerun. It is below the prior trial-supplement 20/10 avg score by `-0.0244`, below SkillX same-10 by `-0.0103`, and only barely above baseline same-10 by `+0.0116`.
- Main regression candidate: `multi_turn_base_131`. Trial-supplement previously injected `tradingbot_direct_symbol_binding` and got score `0.9333`, official-valid `true`; low-trust-gate retrieves TradingBot skills but prompt-injects none and falls to `0.6667`, official-valid `false`.
- Next action: inspect why `multi_turn_base_131` had retrieved TradingBot candidates but zero prompt-injected skills. This likely points to supplement budget/gate interaction rather than an executor-only issue.


## 2026-05-20 07:14:07 +0800 - Selective low-trust cap fix after 131 regression

Case-study findings:
- `multi_turn_base_131` is the clearest regression from the previous low-trust-gate validation.
- Trial-supplement 20/10 behavior on `131`: prompt-injected `tradingbot_direct_symbol_binding`, called `place_order(order_type='Buy', symbol='MSFT', price=310.23, amount=150)`, then `get_order_details(order_id=12446)`, score `0.9333`, official-valid `true`.
- Low-trust-gate 20/10 behavior on `131`: prompt-injected no skills, skipped `place_order` on turn 3, then called `get_order_history()` and `get_order_details(order_id=12345)` on turn 4, score `0.6667`, official-valid `false`.
- Static retrieval replay with the latest store showed that early TradingBot trial candidates can consume the single all-task low-trust supplement slot before the key turn. That made the later `buy_order_fetch_current_price_workflow__candidate_r0_t10_s0` unavailable even though it exactly matches turn 3.

Root cause:
- The previous `low_trust_total_limit = 1` was too coarse. It was introduced to prevent repeated high-risk vehicle navigation candidates, but it counted all low-trust supplement skills equally.
- This overprotected unrelated domains: a low-risk TradingBot hint in an early turn could block a later, highly relevant TradingBot market-price buy workflow.

Code changes:
- `academic/benchmarks/bfcl/retrieval.py`:
  - Added `skill_action_tool_names(skill)`, which extracts tool names the skill actually instructs the executor to call from output contract and action text.
  - Added `bfcl_low_trust_counts_toward_task_limit(skill)`, currently true only for high-risk action `set_navigation`.
  - Changed `low_trust_turn_match_reason` to use `skill_action_tool_names` for the navigation special case.
- `academic/benchmarks/bfcl/adapter.py`:
  - Replaced all-low-trust `low_trust_selected_names` with `low_trust_limited_selected_names`.
  - The per-task cap now applies only when `_bfcl_low_trust_counts_toward_task_limit(artifact)` is true.
  - Non-navigation low-trust supplements can appear in later turns if they match current-turn evidence.
- `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`:
  - Updated the cap test so it covers `set_navigation` high-risk candidates rather than generic low-trust hints.
  - Added `test_bfcl_low_trust_task_limit_only_counts_high_risk_actions`, verifying a TradingBot sector-symbol hint does not block a later market-price buy-order hint.

Verification:
- `python -m py_compile academic/benchmarks/bfcl/retrieval.py academic/benchmarks/bfcl/adapter.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py` passed.
- Focused pytest passed: `pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_low_trust_navigation_requires_current_navigation_intent academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_trial_supplement_caps_low_trust_candidates_per_task academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_low_trust_task_limit_only_counts_high_risk_actions academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_trial_supplement_marks_trial_skill_low_trust academic/benchmarks/tests/test_skill_injector_budget.py::test_low_trust_skill_prompt_blocks_are_explicitly_marked` -> `5 passed`.
- Static replay on `multi_turn_base_131` with `academic/results/bfcl_trl_lowtrust_gate_20_10_20260520_skills.json` showed turn 3 now retrieves `buy_order_fetch_current_price_workflow__candidate_r0_t10_s0` in the supplement lane.

Decision:
- This is a targeted TRL-side fix. Run another 20/10 validation before considering any 50/50 rerun.


## 2026-05-20 07:14:07 +0800 - Launched selective-cap 20/10 validation

Started validation run:
- Tag: `bfcl_trl_lowtrust_selective_cap_20_10_20260520`.
- PID: `385614`.
- Manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_10_promotion_gate_20260520.json`.
- Same core settings as previous 20/10 checks: 20 train / 10 heldout, candidate competition enabled, candidate sample count `3`, micro/macro maintenance `1/10`, train/test/micro concurrency `4/4/4`, top-k `2`, prompt-only skills, official backend, native prompt, tool API auto, max steps per turn `20`, max task seconds `180`, temperature `0.0`.
- Heldout setting: `--heldout-trial-supplement-k 1`; pending supplement disabled.
- Artifacts: `academic/results/bfcl_trl_lowtrust_selective_cap_20_10_20260520_evolve.json`, `academic/results/bfcl_trl_lowtrust_selective_cap_20_10_20260520_skills.json`, `academic/results/bfcl_trl_lowtrust_selective_cap_20_10_20260520_checkpoint.json`, `academic/results/macro_snapshots/bfcl_trl_lowtrust_selective_cap_20_10_20260520/`, and log `academic/results/logs/bfcl_trl_lowtrust_selective_cap_20_10_20260520.launcher.log`.

Monitoring targets:
- `multi_turn_base_52/68/86`: ensure navigation low-trust candidates remain blocked unless the current turn explicitly asks for navigation.
- `multi_turn_base_131`: check whether market-price buy/order-detail skills now enter prompt and recover the previous `0.9333`-style trajectory.
- Token/time: ensure the extra supplement availability does not produce runaway injector calls.


## 2026-05-20 07:18:25 +0800 - Selective-cap validation first train window and maintenance

PID `385614` remains active.

First train window completed:
- `multi_turn_base_120`: score `0.8`, official valid, no prompt skills.
- `multi_turn_base_130`: strict/official valid, no prompt skills.
- `multi_turn_base_116`: strict/official valid, no prompt skills.
- `multi_turn_base_117`: score `0.8571`, official valid, no prompt skills.
- `multi_turn_base_121`: score `0.9091`, official invalid, no prompt skills.
- `multi_turn_base_142`: score `0.9091`, official valid, no prompt skills.
- `multi_turn_base_123`: strict/official valid, no prompt skills.
- `multi_turn_base_135`: score `0.8333`, official invalid, no prompt skills.
- `multi_turn_base_107`: score `0.9091`, official valid, no prompt skills.
- `multi_turn_base_128`: strict/official valid, no prompt skills.

Maintenance status:
- Entered first macro maintenance after the first 10 train tasks.
- Many extractor calls returned short `response_chars = 33`, likely “no skill” outputs, but several substantive extractor responses appeared (`response_chars` around `2405`, `6186`, `6288`, `5834`).
- Sidecar files are growing normally: current round details about `2.1M`, overlap state about `2.7M`, segment rows about `955K`, store about `55K`, checkpoint about `1.1M`.

Health:
- No traceback, auth failure, timeout, or stalled process observed.
- Continue waiting for the first macro snapshot and second train window.


## 2026-05-20 07:21:00 +0800 - Selective-cap first macro snapshot and second train window

First macro snapshot appeared:
- `round_00_macro_000_meta.json`.
- `train_tasks_completed = 10`.
- `skill_count = 10`, `n_active = 1`, `n_pending = 0`, `n_disabled = 0`.
- Skill snapshot size about `94K`; store sidecar about `525K`, so no store explosion.
- Active skill: `resolve_company_name_to_symbol_for_watchlist`.
- Trial skills include account-balance-before-withdrawal candidates and watchlist display / symbol-resolution candidates.

Second train window observations so far:
- `multi_turn_base_132`: score `0.6667`, official valid, prompt-injected `display_watchlist_after_add_requires_get_watchlist__candidate_r0_t7_s1`. This is a possible harmful/low-quality trial injection to review if final heldout is bad.
- `multi_turn_base_146`: strict/official valid, prompt-injected active `resolve_company_name_to_symbol_for_watchlist`.
- `multi_turn_base_122`: strict/official valid, no prompt skills.
- `multi_turn_base_118`: strict/official valid, no prompt skills.
- `multi_turn_base_93`: score `0.6667`, official invalid, no prompt skills.

Health:
- No traceback, auth failure, timeout, or stalled process.
- Continue monitoring through final maintenance and heldout. The main decision point remains heldout `52/68/86/131`.


## 2026-05-20 07:23:00 +0800 - Selective-cap second train window completed

PID `385614` remains active and the run is moving toward final maintenance / heldout.

Second train window completed:
- `multi_turn_base_118`: strict/official valid, no prompt skills.
- `multi_turn_base_132`: score `0.6667`, official valid, injected `display_watchlist_after_add_requires_get_watchlist__candidate_r0_t7_s1`.
- `multi_turn_base_122`: strict/official valid, no prompt skills.
- `multi_turn_base_146`: strict/official valid, injected active `resolve_company_name_to_symbol_for_watchlist`.
- `multi_turn_base_115`: score `0.75`, official valid, injected active `resolve_company_name_to_symbol_for_watchlist`.
- `multi_turn_base_127`: strict/official valid, no prompt skills.
- `multi_turn_base_93`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_70`: score `0.8235`, official invalid, no prompt skills.
- `multi_turn_base_82`: score `0.8235`, official invalid, no prompt skills.

Observations:
- Training is stable; no runtime errors or timeouts.
- Active symbol-resolution skill is being injected and at least `146` remains strict-correct.
- A trial watchlist display skill was injected into `132` with a low score, but official validity stayed true. If final heldout regresses, inspect whether this family is too broad.

Next:
- Continue waiting through final maintenance and heldout. Main target cases remain `52`, `68`, `86`, and especially `131`.


## 2026-05-20 07:25:00 +0800 - Selective-cap final maintenance with one train timeout

PID `385614` remains active.

New observation:
- `multi_turn_base_126` in the second train window was converted to a failed result due to request timeout:
  - score `0.0`.
  - `official_valid = null`.
  - error: `Request timed out or interrupted... long-requests`.
- This did not crash the run. Maintenance continued with extractor and credit assigner calls.

Maintenance health:
- Credit assigner calls completed with substantive responses, e.g. response chars around `3030`, `4359`, `3541`.
- Extractor calls continued; many were short no-op outputs, but later substantive outputs appeared around `8157`, `7979`, `4997` response chars.
- No Python traceback, auth failure, or process stall observed.

Interpretation:
- The timeout is not from the new selective low-trust cap directly; it occurred during a train rollout and was handled by the failure-conversion path.
- Still record it as residual execution instability. If final metrics are marginal, this timeout may affect training/maintenance quality and should be considered when comparing this run to prior 20/10 validations.


## 2026-05-20 07:34:11 +0800 - Selective-cap still in final maintenance

Process status:
- PID `385614` is still active after about `1151` seconds.
- Result JSON has not been written yet.
- Checkpoint exists and is about `1.84M`.
- Macro snapshot directory still contains only the first snapshot:
  - `round_00_macro_000_meta.json`
  - `round_00_macro_000_skills.json`

Recent log status:
- The run is still in final maintenance after the second train window.
- Recent roles are mostly `refiner`, `refactorer`, `bundle_builder`, and `skill_injector`.
- LLM calls are completing normally; examples include:
  - `refactorer` around `27-36s`, prompt tokens about `6.6K-8.1K`, total tokens about `8.4K-10.0K`.
  - `bundle_builder` around `16-24s`, total tokens about `6.9K-7.5K`.
  - `skill_injector` around `2.4-3.5s`, total tokens about `1.0K-1.2K`.

Health:
- No traceback, auth failure, or process stall.
- The only observed runtime instability remains the earlier `multi_turn_base_126` train rollout timeout, which was converted to a failed result and did not terminate the run.

Next:
- Continue waiting. Do not launch a full 50/50 until this selective-cap 20/10 validation finishes and the heldout behavior is inspected.


## 2026-05-20 07:35:18 +0800 - Selective-cap entered heldout

Process status:
- PID `385614` is still active after about `1227` seconds.
- Result JSON has not been written yet.
- Checkpoint has grown to about `60M`.

Second macro snapshot:
- `round_00_macro_001_meta.json` appeared at `07:34`.
- `train_tasks_completed = 20`.
- `skill_count = 31`.
- `n_active = 2`, `n_pending = 0`, `n_disabled = 0`.
- Active skills:
  - `resolve_company_name_to_symbol_for_watchlist`
  - `retrieve_recent_order_details_from_context`
- Macro report summary:
  - `candidate_group_feedback_rows = 2`
  - no pending promotions listed
  - no filtered skills listed
- Skill snapshot grew from about `94K` after macro 0 to about `489K` after macro 1. This is larger but not explosive.

Heldout started:
- `multi_turn_base_173`: score `0.8889`, official invalid, no prompt skills.
- `multi_turn_base_152`: score `0.7273`, official invalid, no prompt skills.
- `multi_turn_base_176`: score `0.6667`, official invalid, no prompt skills.

Initial heldout interpretation:
- No evidence yet that the selective cap reintroduced broad prompt pollution; the first three heldout tasks injected no skills.
- Also no evidence yet that learned skills are helping heldout, because no heldout prompt skill has appeared in the first three completed test tasks.
- Continue watching the target cases `52`, `68`, `86`, and `131`.


## 2026-05-20 07:36:32 +0800 - Selective-cap heldout shows vehicle skill pollution

PID `385614` is still active after about `1302` seconds.

Heldout results observed so far:
- `multi_turn_base_173`: score `0.8889`, official invalid, no prompt skills.
- `multi_turn_base_152`: score `0.7273`, official invalid, no prompt skills.
- `multi_turn_base_176`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_52`: score `0.5333`, official invalid, injected `vehicle_tire_service_navigation_workflow__candidate_r0_t17_s0`.
- `multi_turn_base_65`: score `0.8421`, official invalid, no prompt skills.
- `multi_turn_base_68`: score `0.8`, official valid, injected:
  - `vehicle_fuel_check_before_fill_unnecessary_when_filling_to_capacity__candidate_r0_t19_s2`
  - `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2`
- `multi_turn_base_88`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_86`: score `0.9412`, official valid, injected:
  - `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2`
  - `vehicle_tire_shop_navigation_workflow__candidate_r0_t17_s1`

Important interpretation:
- The selective low-trust cap did not fully solve vehicle-domain pollution.
- `52` is the clearest failure: the run injected a tire navigation workflow and scored only `0.5333`.
- `86` also received a tire shop navigation workflow, although it remained official valid with high partial score.
- `68` avoided the specific navigation pollution but still received vehicle low-trust skills; official validity stayed true but strict success failed.
- This weakens the case for launching a full 50/50 from this exact selective-cap setting unless the final two cases, especially `131`, show a major positive shift.

Next:
- Continue waiting for the remaining heldout tasks and final result JSON.
- After completion, compare against prior 20/10 variants and inspect whether `131` recovered the TradingBot skill that the selective cap was designed to unblock.


## 2026-05-20 07:37:31 +0800 - Selective-cap 20/10 completed

The process exited and wrote the final result:
- `academic/results/bfcl_trl_lowtrust_selective_cap_20_10_20260520_evolve.json`
- result size about `111M`
- skills size about `489K`
- checkpoint size about `62M`

Final heldout metrics:
- strict success: `1/10 = 0.10`
- official valid: `3/10 = 0.30`
- avg score: `0.7835`
- avg total tokens: `82568.4`
- avg elapsed: `43.81s`
- max elapsed: `77.26s`
- avg model steps: `9.3`
- max model steps: `15`
- timeout rate: `0.0` on heldout
- call recall: `0.8251`
- call precision: `0.7592`
- call errors:
  - `argument_mismatch = 5`
  - `extra_call = 14`
  - `missing_call = 5`

Heldout task details:
- `multi_turn_base_68`: score `0.8`, official valid, injected `vehicle_fuel_check_before_fill_unnecessary_when_filling_to_capacity__candidate_r0_t19_s2` and `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2`.
- `multi_turn_base_152`: score `0.7273`, official invalid, no prompt skills.
- `multi_turn_base_173`: score `0.8889`, official invalid, no prompt skills.
- `multi_turn_base_52`: score `0.5333`, official invalid, injected `vehicle_tire_service_navigation_workflow__candidate_r0_t17_s0`.
- `multi_turn_base_176`: score `0.6667`, official invalid, no prompt skills.
- `multi_turn_base_65`: score `0.8421`, official invalid, no prompt skills, but retrieved vehicle fuel / brake candidates.
- `multi_turn_base_86`: score `0.9412`, official valid, injected `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2` and `vehicle_tire_shop_navigation_workflow__candidate_r0_t17_s1`.
- `multi_turn_base_88`: score `0.6667`, official invalid, no prompt skills, but retrieved vehicle fuel candidates.
- `multi_turn_base_108`: strict and official valid, injected active `retrieve_recent_order_details_from_context`.
- `multi_turn_base_131`: score `0.7692`, official invalid, injected active `retrieve_recent_order_details_from_context`.

Comparison against prior small BFCL validations on the same 10 heldout IDs:
- Baseline same 10 from previous notes: strict `1/10`, official valid `1/10`, avg score `0.7433`.
- Promotion-gate 20/10: strict `1/10`, official valid `2/10`, avg score `0.7570`, avg tokens `76730.9`.
- Trial-supplement 20/10: strict `1/10`, official valid `3/10`, avg score `0.7793`, avg tokens `83017.5`.
- Low-trust-gate 20/10: strict `1/10`, official valid `3/10`, avg score `0.7549`, avg tokens `82190.9`.
- Selective-cap 20/10: strict `1/10`, official valid `3/10`, avg score `0.7835`, avg tokens `82568.4`.

Comparison against SkillX same 10 heldout IDs:
- SkillX same 10: strict `1/10`, official valid `2/10`, avg score `0.76523`.
- Selective-cap same 10: strict `1/10`, official valid `3/10`, avg score `0.7835`.
- On this narrow slice, selective-cap has higher official-valid count and avg score than SkillX.
- However, SkillX did not inject vehicle skills into `52/68/86/88`, while selective-cap did. This means our higher same-10 score is not clean evidence that the learned skill behavior is safer.

Comparison against full 50/50 formal results:
- Baseline rerun 50/50 from previous notes: strict `0.08`, official valid `0.48`, avg score `0.7388`.
- Original TRL 50/50: strict `0.08`, official valid `0.52`, avg score `0.7474`, avg tokens `76689.5`, avg elapsed `79.253s`.
- Injector-gate TRL 50/50: strict `0.08`, official valid `0.60`, avg score `0.7530`, avg tokens `74175.7`, avg elapsed `49.427s`.
- SkillX aligned 50/50: strict `0.08`, official valid `0.54`, avg score `0.7679`, avg tokens `78787.6`, avg elapsed `34.927s`.
- Selective-cap is only a 20/10 validation, so it is not directly comparable to 50/50; it does not justify a full 50/50 launch without addressing vehicle pollution.

Decision:
- Do not launch full 50/50 from this exact setting yet.
- The validation did not collapse, but it exposed a concrete low-trust navigation gate bug on `52`.
- Fix the bug first, verify with unit tests and retrieval replay, then decide whether to run another short validation.


## 2026-05-20 07:48:00 +0800 - Fixed generic-guidance false positive for low-trust navigation

Root cause found:
- In `academic/benchmarks/bfcl/retrieval.py`, `low_trust_turn_match_reason` treated these weak words as explicit navigation intent:
  - `directly`
  - `guide`
  - `guidance`
- This let `multi_turn_base_52` turn 1 pass the low-trust navigation gate because the user said:
  - `kindly guide me to the nearest tire service facility for a fix`
- But the expected calls for that turn are only:
  - `check_tire_pressure()`
  - `find_nearest_tire_shop()`
- The expected call does not include `set_navigation`.

Code change:
- File: `academic/benchmarks/bfcl/retrieval.py`
- Removed weak generic guidance terms from the navigation-intent token set:
  - removed `directly`
  - removed `guide`
  - removed `guidance`
- Added explicit phrase checks for true navigation setup:
  - `set navigation`
  - `set our navigation`
  - `set my navigation`
  - `set the navigation`
  - `set gps`
  - `set the gps`
- The gate now allows low-trust `set_navigation` candidates only when the query contains strict navigation tokens or one of the explicit set-navigation phrases.

Regression test added:
- File: `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`
- Added `test_bfcl_low_trust_navigation_does_not_treat_generic_guidance_as_navigation_intent`.
- The test uses the literal `52`-style phrasing and confirms:
  - generic `guide me to the nearest tire service facility` is rejected with `low_trust_navigation_without_explicit_navigation_intent`
  - later explicit `Set our navigation to that tire service facility` is accepted

Verification:
- `python -m py_compile academic/benchmarks/bfcl/retrieval.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`
- `pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_low_trust_navigation_requires_current_navigation_intent academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_low_trust_navigation_does_not_treat_generic_guidance_as_navigation_intent academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_trial_supplement_caps_low_trust_candidates_per_task academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_low_trust_task_limit_only_counts_high_risk_actions`
- Result: `4 passed`.

Static retrieval replay using the just-finished selective-cap skill store:
- Store: `academic/results/bfcl_trl_lowtrust_selective_cap_20_10_20260520_skills.json`
- `multi_turn_base_52`, turn 1:
  - after the fix, `vehicle_tire_service_navigation_workflow__candidate_r0_t17_s0` is filtered with `low_trust_navigation_without_explicit_navigation_intent`
  - `vehicle_tire_shop_navigation_workflow__candidate_r0_t17_s1` is also filtered with the same reason
  - selected skills become `vehicle_fuel_check_before_fill_unnecessary_when_filling_to_capacity__candidate_r0_t19_s2`
- `multi_turn_base_86`, turn 1:
  - explicit `set our navigation` still passes
  - selected includes `vehicle_tire_shop_navigation_workflow__candidate_r0_t17_s1`
- This confirms the fix targets the false positive without blocking real navigation turns.

Residual issue:
- `multi_turn_base_131` did not recover the desired buy-order/current-price workflow.
- After replay, turns 3 and 4 still select active `retrieve_recent_order_details_from_context` and `resolve_company_name_to_symbol_for_watchlist`.
- The expected useful skill `buy_order_fetch_current_price_workflow__candidate_r0_t10_s0` is not in the final 31-skill store of this run.
- Therefore the `131` failure is not caused by the newly fixed navigation gate. It is an extraction / promotion / retention problem for the buy-order workflow, or the active order-details skill is over-broad and distracts the executor.


## 2026-05-20 07:58:00 +0800 - Fixed active order-details skill over-retrieval

Additional root cause found for `131`:
- Active skills use the normal `_bfcl_skill_matches_turn` predicate, which previously checked mainly task/domain scope and forbid keywords.
- This allowed active `retrieve_recent_order_details_from_context` to be retrieved even on turn 3 of `multi_turn_base_131`, where the user asks:
  - `Please execute an order for MSFT at current market price, targeting 150 shares in today's strategy.`
- That turn is a buy-order turn, not an order-details turn.
- The same skill is appropriate on turn 4:
  - `May I have the details of the most recent order I've placed to verify the transaction?`

Code change:
- File: `academic/benchmarks/bfcl/retrieval.py`
- Added a narrow contextual-order-details guard inside `bfcl_skill_matches_turn`.
- The guard recognizes TradingBot skills whose tool set is only `get_order_details` and whose scope mentions recent/latest/most recent order.
- Such skills now pass only if the current query explicitly asks for contextual order details, status, verification, review, or similar order-detail intent.
- Implementation detail:
  - `_is_contextual_order_details_skill(skill)` now prefers `metadata["allowed_tools"]` over broad parsed action symbols, because `skill_action_tool_names` can pick up schema words such as `amount`, `price`, `status`, and `symbol` from interface JSON.
  - `_query_requests_contextual_order_details(query)` checks explicit phrases such as `latest order`, `recent order`, `most recent order`, `order details`, `verify my order`, `status of my order`, plus a detail-term and order-term fallback.

Regression test added:
- File: `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`
- Added `test_bfcl_contextual_order_details_skill_requires_details_turn_intent`.
- The test confirms:
  - the skill is rejected for `Please execute an order for MSFT at current market price...`
  - the skill is accepted for `details of the most recent order... verify the transaction`

Verification:
- `python -m py_compile academic/benchmarks/bfcl/retrieval.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`
- `pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_contextual_order_details_skill_requires_details_turn_intent academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_low_trust_navigation_does_not_treat_generic_guidance_as_navigation_intent`
- Result: `2 passed`.
- Earlier broader targeted run after the first version of this change:
  - `pytest -q ... test_bfcl_skill_predicate_rejects_cross_domain_and_cross_tool_noise ... test_bfcl_low_trust_task_limit_only_counts_high_risk_actions`
  - Result: `5 passed`.

Static retrieval replay after the fix:
- `multi_turn_base_131`, turn 3:
  - selected skills changed from `retrieve_recent_order_details_from_context` plus `resolve_company_name_to_symbol_for_watchlist`
  - to only `resolve_company_name_to_symbol_for_watchlist`
  - the order-details active row is now filtered as `turn_scope_mismatch`
- `multi_turn_base_131`, turn 4:
  - selected skills remain `retrieve_recent_order_details_from_context` and `resolve_company_name_to_symbol_for_watchlist`
- `multi_turn_base_108`, true order-details scenario:
  - still selects `retrieve_recent_order_details_from_context`
- `multi_turn_base_52`, turn 1:
  - navigation workflows remain filtered by the first fix
- `multi_turn_base_86`, turn 1:
  - explicit navigation workflow remains selected

Current interpretation:
- Two concrete TRL retrieval bugs are fixed:
  - generic guidance was over-interpreted as explicit navigation intent
  - contextual order-details active skill was over-retrieved on non-details turns
- The remaining missing piece for `131` is that this selective-cap run did not keep the buy-order/current-price workflow in the final store. Older `bfcl_trl_lowtrust_gate_20_10_20260520_skills.json` did contain `buy_order_fetch_current_price_workflow__candidate_r0_t10_s0`; the selective-cap final store does not.
- This points to extraction/promotion/retention variance or competition behavior, not just turn gating.


## 2026-05-20 07:48:13 +0800 - Launching post-fix 20/10 validation

Decision:
- Do not launch full 50/50 yet.
- Launch one more short 20/10 validation after the two retrieval fixes.

Why:
- The previous selective-cap run had the best same-10 avg score so far (`0.7835`) but showed concrete contamination.
- The two bugs are now fixed and statically replayed, but the actual executor behavior must be checked with live trajectories.
- Full 50/50 is only justified if the new short run preserves or improves the small-run metrics and removes the observed bad behavior.

Validation focus:
- `multi_turn_base_52`: should not inject `vehicle_tire_service_navigation_workflow` when the user only says `guide me to the nearest tire service facility`.
- `multi_turn_base_86`: should still allow true navigation workflow because the user explicitly asks to `set our navigation`.
- `multi_turn_base_131`: should not inject `retrieve_recent_order_details_from_context` on the buy-order turn; it may still inject it on the subsequent order-details turn.
- `multi_turn_base_108`: should still benefit from order-details skill.
- Check whether any buy-order/current-price workflow appears in the final store or heldout retrieval.

Planned tag:
- `bfcl_trl_turnguard_20_10_20260520`

Launch command:
- `BFCL_PROXY_PORT=4000 BFCL_PROXY_API_KEY=1234abcd BFCL_MICRO_CONCURRENCY=4 ANTHROPIC_API_KEY=1234abcd OPENAI_API_KEY=1234abcd nohup python -m academic.benchmarks.bfcl.related.proxy_runner --mode evolve --manifest academic/experiments/bfcl_case_lists/curated_related_manifest_20_10_promotion_gate_20260520.json --expected-train-size 20 --expected-test-size 10 --epochs 1 --rounds 1 --micro-maintenance-step 1 --macro-maintenance-step 10 --test-concurrency 4 --train-window-concurrency 4 --enable-candidate-competition --candidate-sample-count 3 --candidate-group-min-usage 1 --candidate-group-low-usage-macros 3 --top-k-skills 2 --skill-injection-mode prompt_only --execution-backend official --prompt-style native --tool-api-style auto --max-steps-per-turn 20 --max-task-seconds 180 --temperature 0.0 --output-detail-level compact --heldout-trial-supplement-k 1 --tag bfcl_trl_turnguard_20_10_20260520 --save-skills academic/results/bfcl_trl_turnguard_20_10_20260520_skills.json --checkpoint academic/results/bfcl_trl_turnguard_20_10_20260520_checkpoint.json --macro-snapshot-dir academic/results/macro_snapshots/bfcl_trl_turnguard_20_10_20260520 --output academic/results/bfcl_trl_turnguard_20_10_20260520_evolve.json > academic/results/logs/bfcl_trl_turnguard_20_10_20260520.launcher.log 2>&1 &`

PID:
- `713646`

Initial monitor at `2026-05-20 07:49:18 +0800`:
- PID `713646` active after about `65s`.
- First training window is running.
- Completed train tasks so far:
  - `multi_turn_base_130`: strict/official valid, no skills.
  - `multi_turn_base_120`: score `0.8`, official valid, no skills.
  - `multi_turn_base_116`: strict/official valid, no skills.
  - `multi_turn_base_117`: score `0.8571`, official valid, no skills.
  - `multi_turn_base_142`: score `0.9091`, official valid, no skills.
  - `multi_turn_base_123`: strict/official valid, no skills.
  - `multi_turn_base_121`: score `0.9091`, official invalid, no skills.
- No traceback, auth failure, timeout, or suspicious prompt-skill injection observed.


## 2026-05-20 07:50:46 +0800 - Turnguard first train window completed

PID `713646` is active after about `153s`.

First train window completed:
- `multi_turn_base_120`: score `0.8`, official valid, no skills.
- `multi_turn_base_130`: strict/official valid, no skills.
- `multi_turn_base_116`: strict/official valid, no skills.
- `multi_turn_base_117`: score `0.8571`, official valid, no skills.
- `multi_turn_base_121`: score `0.9091`, official invalid, no skills.
- `multi_turn_base_142`: score `0.9091`, official valid, no skills.
- `multi_turn_base_123`: strict/official valid, no skills.
- `multi_turn_base_135`: score `0.8333`, official invalid, no skills.
- `multi_turn_base_107`: score `0.9091`, official valid, no skills.
- `multi_turn_base_128`: strict/official valid, no skills.

Maintenance status:
- First macro maintenance has started.
- Many extractor calls are short no-op outputs (`response_chars = 33`), but several substantive extractor outputs have appeared:
  - `response_chars = 2611`
  - `response_chars = 3172`
  - `response_chars = 2334`
  - `response_chars = 6468`
  - `response_chars = 2576`
- No snapshot yet.

Health:
- No traceback, auth failure, or timeout.
- Continue waiting for first macro snapshot and second train window.


## 2026-05-20 07:52:40 +0800 - Turnguard first maintenance still active

PID `713646` is active after about `267s`.

Status:
- Checkpoint exists at about `1.13M`.
- No result JSON yet.
- No macro snapshot yet.
- Still in first macro maintenance after the first 10 train tasks.

Recent maintenance activity:
- Additional substantive extractor outputs appeared:
  - `response_chars = 5849`
  - `response_chars = 5906`
- `refactorer` completed in about `31.7s`, total tokens `8529`.
- `bundle_builder` completed in about `24.6s`, total tokens `7609`.
- `skill_injector` calls are completing in about `2.5-4.2s`, total tokens around `969-1018`.

Health:
- No traceback, auth failure, timeout, or stalled process.
- Continue waiting for first macro snapshot.


## 2026-05-20 07:54:29 +0800 - Turnguard first maintenance still progressing

PID `713646` is active after about `375s`.

Status:
- Still in first macro maintenance.
- Checkpoint remains present at about `1.13M`.
- No macro snapshot yet.
- No result JSON yet.

Recent maintenance activity:
- More `skill_injector` calls completed normally, total tokens around `990-1023`.
- `refactorer` completed twice:
  - about `16.8s`, total tokens `8722`
  - about `33.2s`, total tokens `8002`
- Another `bundle_builder` call has started.

Health:
- No traceback, auth failure, timeout, or stall.
- This maintenance is somewhat long but active. Continue monitoring.


## 2026-05-20 07:56:47 +0800 - Turnguard first snapshot and second train window started

PID `713646` is active after about `514s`.

First macro snapshot:
- `round_00_macro_000_meta.json` appeared at `07:56`.
- `train_tasks_completed = 10`.
- `skill_count = 10`.
- `n_active = 0`, `n_pending = 0`, `n_disabled = 0`.
- All 10 skills are `trial`; no active skills after the first macro.

Trial skills after first macro:
- `withdraw_funds_requires_prior_balance_check__candidate_r0_t4_s0`
- `verify_balance_before_withdrawal__candidate_r0_t4_s1`
- `verify_balance_before_withdrawal__candidate_r0_t4_s2`
- `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`
- `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s0`
- `avoid_symbol_lookup_when_adding_known_company_to_watchlist__candidate_r0_t7_s0`
- `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s1`
- `add_watchlist_skip_symbol_lookup_when_company_name_given__candidate_r0_t7_s1`
- `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s2`
- `skip_symbol_lookup_when_adding_known_company_to_watchlist__candidate_r0_t7_s2`

Second train window started:
- `multi_turn_base_132`: score `0.8`, official invalid, injected `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_146`: strict/official valid, injected `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s0` and `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_122`: strict/official valid, injected `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.

Interpretation:
- The turnguard fixes are not involved yet; these are TradingBot trial-supplement behaviors.
- `trading_order_id_reuse_across_turns__candidate_r0_t6_s0` may be over-broad: it appears in `132`, where the score becomes `0.8` and official validity is false.
- Because `122` and `146` remain strict-correct with the same skill injected, this is not automatically a fatal skill; we need to see credit assignment after the second window.

Health:
- No traceback, auth failure, timeout, or stalled process.
- Continue monitoring the rest of second train window and final maintenance.


## 2026-05-20 07:58:57 +0800 - Turnguard second train window completed

PID `713646` is active after about `643s`.

Second train window completed:
- `multi_turn_base_118`: strict/official valid, injected `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s0` and `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_132`: score `0.8`, official invalid, injected `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_122`: strict/official valid, injected `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_146`: strict/official valid, injected `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s0` and `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_126`: strict/official valid, injected `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s0` and `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_115`: score `0.75`, official valid, injected `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_127`: strict/official valid, injected `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s0` and `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_93`: score `0.6667`, official invalid, no skills.
- `multi_turn_base_70`: score `0.8235`, official invalid, no skills.
- `multi_turn_base_82`: score `0.8235`, official invalid, no skills.

Important observations:
- Unlike the previous selective-cap run, `multi_turn_base_126` did not timeout; it completed strict/official valid.
- `trading_order_id_reuse_across_turns__candidate_r0_t6_s0` is heavily injected across TradingBot tasks.
- It is not always harmful: `118`, `122`, `126`, `127`, and `146` stayed strict-correct.
- But `132` became official invalid with this skill injected, so credit assignment/refinement should have a chance to narrow or penalize it.

Maintenance status:
- Final maintenance has started.
- `credit_assigner` calls are completing with substantive outputs:
  - response chars around `2870`, `3827`, `3780`, `4017`, `4064`, `3618`
- Extractor calls are also running, with both short no-op outputs and substantive outputs around `2966`, `3589`, `3140`, `3648`, `3059`.

Health:
- No traceback, auth failure, timeout, or stalled process.
- Continue waiting for the second macro snapshot and heldout.


## 2026-05-20 08:01:27 +0800 - Turnguard final maintenance still active

PID `713646` is active after about `794s`.

Status:
- Still in final maintenance after 20 train tasks.
- Second macro snapshot has not appeared yet.
- Result JSON has not appeared yet.
- Checkpoint remains present.

Recent maintenance activity:
- Additional credit assigner completed with substantive output:
  - response chars `4010`, total tokens `5824`.
- Many extractor calls completed with substantive outputs:
  - response chars around `7658`, `2970`, `7841`, `4918`, `7975`, `5627`, `5038`, `6431`, `2798`, `7501`, `6003`, `4903`.
- Recent skill injector calls completed with total tokens around `892-907`.

Health:
- No traceback, auth failure, timeout, or stall.
- Continue waiting for second macro snapshot and heldout.

## 2026-05-20 08:05:27 +0800 - Turnguard final maintenance continues

PID `713646` is still active after about `1023s`.

Status:
- Training has completed and the run remains in final maintenance.
- Second macro snapshot has not appeared yet.
- Final result JSON has not appeared yet.
- Checkpoint exists at `academic/results/bfcl_trl_turnguard_20_10_20260520_checkpoint.json`.

Latest log observations:
- No traceback, auth failure, timeout, or task stall.
- Recent work is mostly maintenance LLM calls, not BFCL task execution.
- Extractor calls are the largest cost center in this phase:
  - substantive extractor outputs include response chars `7658`, `7841`, `7975`, `7501`, `6003`, `4903`.
  - extractor total tokens are frequently around `7k-9.5k`.
- Refiner calls are completing quickly:
  - examples: around `5.4k` tokens and `4.5s-6.0s`.
- Skill injector calls are short:
  - examples: around `0.9k` tokens and `2.7s-5.4s`.

Interpretation:
- Current small-scale validation is healthy but maintenance-heavy.
- If this setting is promoted to 50/50, maintenance token/time cost needs to be reported explicitly; the task execution speed is not the only bottleneck.
- Still waiting to see whether final credit/refine behavior narrows or suppresses the broad TradingBot trial skill `trading_order_id_reuse_across_turns__candidate_r0_t6_s0` after its mixed evidence, especially the invalid `132` case.


## 2026-05-20 08:07:20 +0800 - Maintenance call-count audit

PID `713646` is still active after about `1127s`.

Status:
- Still no second macro snapshot.
- Still no final result JSON.
- Log file mtime is current, so this is not a silent stall.

Maintenance LLM call counts from the launcher log:
- `bfcl_task_run`: `20`
- `maintenance_llm_start`: `145`
- `maintenance_llm_done`: `144`

Role-level call counts:
- `extractor`: `60` starts, `60` done.
- `refactorer`: `5` starts, `5` done.
- `bundle_builder`: `2` starts, `2` done.
- `skill_injector`: `66` starts, `66` done.
- `credit_assigner`: `7` starts, `7` done.
- `refiner`: `5` starts, `4` done at the time of this audit.

Role-level token totals observed so far:
- `extractor`: `450986` total tokens.
- `skill_injector`: `73767` total tokens.
- `refactorer`: `40695` total tokens.
- `credit_assigner`: `38101` total tokens.
- `refiner`: `21720` total tokens.
- `bundle_builder`: `15098` total tokens.

Interpretation:
- The run is maintenance-heavy but not obviously stuck.
- Extractor dominates maintenance cost by a large margin.
- The small-scale validation should be used to decide behavior first; if behavior is good enough for 50/50, the paper/report should still include this maintenance token cost.


## 2026-05-20 08:08:58 +0800 - Maintenance still active, refiner/injector phase

PID `713646` is still active after about `1232s`.

Status:
- Still no second macro snapshot.
- Still no final result JSON.
- The log continues to update with normal maintenance completions.

Updated maintenance LLM call counts:
- `extractor`: `60` starts, `60` done, `450986` total tokens.
- `refactorer`: `5` starts, `5` done, `40695` total tokens.
- `bundle_builder`: `2` starts, `2` done, `15098` total tokens.
- `skill_injector`: `74` starts, `74` done, `81018` total tokens.
- `credit_assigner`: `7` starts, `7` done, `38101` total tokens.
- `refiner`: `6` starts, `5` done, `27162` total tokens so far.

Latest log line:
- A new `refiner` call has started with `user_chars=14015`, `system_chars=6065`.

Interpretation:
- This is still not a dead process: calls keep completing.
- The maintenance phase is unexpectedly long for 20/10 because it expands into many per-skill/per-candidate injector and refiner calls.
- Continue monitoring rather than terminating, because the user explicitly requested waiting while experiments run and because no actual error has appeared.


## 2026-05-20 08:10:38 +0800 - Checkpoint and maintenance completion audit

PID `713646` is still active after about `1324s`.

Status:
- Still no second macro snapshot.
- Still no final result JSON.
- Checkpoint file is still the first macro checkpoint from `07:56`, not updated during final maintenance.

Checkpoint structure:
- Top-level keys: `checkpoint_version`, `current_round_state`, `next_round_index`, `output_detail_level`, `role_feedback`, `round_reports`, `rounds_total`, `segment_index_rows`, `store`, `tag`.
- `store.artifacts` contains `10` artifacts, matching the first macro snapshot only.
- `segment_index_rows` contains `46` rows.

Updated maintenance LLM call counts:
- Total `maintenance_llm_start`: `163`
- Total `maintenance_llm_done`: `163`
- `extractor`: `60/60`, `450986` total tokens.
- `refactorer`: `5/5`, `40695` total tokens.
- `bundle_builder`: `2/2`, `15098` total tokens.
- `skill_injector`: `82/82`, `88250` total tokens.
- `credit_assigner`: `7/7`, `38101` total tokens.
- `refiner`: `7/7`, `38033` total tokens.

Interpretation:
- As of this audit there is no outstanding LLM call in the log.
- The process is still alive, so it is likely either finalizing macro state, running synchronous non-logged work, or about to enter heldout.
- If no log/snapshot changes appear in the next monitoring interval, inspect the runner/maintenance code path around post-maintenance snapshot/write/test transition.


## 2026-05-20 08:12:07 +0800 - Final macro is still inside overlap refactor

PID `713646` remains active.

Updated maintenance LLM call counts:
- Total `maintenance_llm_start`: `174`
- Total `maintenance_llm_done`: `173`
- `extractor`: `60/60`, `450986` total tokens.
- `refactorer`: `6/5`, `40695` total tokens completed so far.
- `bundle_builder`: `2/2`, `15098` total tokens.
- `skill_injector`: `90/90`, `95513` total tokens.
- `credit_assigner`: `7/7`, `38101` total tokens.
- `refiner`: `9/9`, `48843` total tokens.

Latest log transition:
- After another `skill_injector` and `refiner` cycle, a new `refactorer` call started with `user_chars=12773`, `system_chars=7365`.

Code-path inspection:
- Final macro calls `_run_macro_maintenance(...)` in `academic/benchmarks/bfcl/related/experiment.py`.
- `_run_macro_maintenance` calls `_run_window_overlap_refactor(...)`.
- `_run_window_overlap_refactor` calls `run_bfcl_overlap_refactor_llm(...)` with `max_repair_rounds=int(os.environ.get(\"BFCL_REFACTOR_MAX_REPAIR_ROUNDS\", \"1\"))`.
- Therefore the repeated `skill_injector`/`refiner` calls are part of the overlap refactor candidate/build/test/refine process before control returns to the snapshot-writing layer.

Interpretation:
- The process is still making forward progress.
- The long maintenance is plausibly caused by multiple overlap candidate groups/cliques, not by a dead loop observed so far.
- Continue monitoring until either final macro snapshot appears or a concrete error/stall is observed.


## 2026-05-20 08:14:25 +0800 - Refactorer returned and candidate bundle/test path continued

PID `713646` remains active after about `1556s`.

Status:
- Still no second macro snapshot.
- Still no final result JSON.
- No traceback, auth error, timeout, or process stall.

Latest final macro events:
- `refactorer` completed:
  - duration `34792ms`
  - response chars `7467`
  - prompt tokens `7114`
  - completion tokens `2173`
  - total tokens `9287`
- A new `bundle_builder` completed immediately after:
  - duration `23022ms`
  - response chars `6060`
  - total tokens `7579`
- Several new `skill_injector` calls followed for the candidate/bundle-test path:
  - examples around `932-947` total tokens.

Interpretation:
- This confirms the process was not stuck in refiner; it returned to overlap refactor and is now validating a newly proposed candidate/refactor artifact.
- The current issue is cost/latency of final macro maintenance, not an observed correctness failure yet.


## 2026-05-20 08:15:53 +0800 - Second macro snapshot and heldout start

Second macro snapshot appeared:
- `round_00_macro_001_meta.json`
- `round_00_macro_001_skills.json`

Second macro metadata:
- `train_tasks_completed`: `20`
- `phase`: `macro`
- `window_index`: `1`
- `skill_count`: `43`
- `n_active`: `1`
- `n_pending`: `0`
- `n_disabled`: `0`
- `candidate_group_feedback_rows`: `3`
- `pending_skill_promotions`: `[]`
- `filtered_skills`: `[]`

Skill status after second macro:
- `trial`: `37`
- `archived`: `5`
- `active`: `1`

Important skill observations:
- `trading_order_id_reuse_across_turns__candidate_r0_t6_s0` remains `trial`, version `9`.
- It was not disabled despite harmful/mixed evidence on train `132`.
- Several watchlist duplicates were archived, leaving `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s0` as trial.
- Several tire-navigation skills remain trial, but turnguard controls whether they are injected into heldout.

Heldout has started:
- `multi_turn_base_173`: score `0.8889`, official invalid, no injected skills.
- `multi_turn_base_152`: score `0.7273`, official invalid, no injected skills.
- `multi_turn_base_176`: score `0.0`, official invalid, no injected skills.
- `multi_turn_base_52`: score `0.5714`, official invalid, no injected skills.
- `multi_turn_base_65`: score `0.6667`, official invalid, no injected skills.

Turnguard check:
- `52` no longer injects the tire-navigation skill. This confirms the specific low-trust navigation false-positive fix is active.
- `52` still fails, so the remaining error is likely executor/base-model behavior rather than that specific skill pollution.


## 2026-05-20 08:16:13 +0800 - Heldout skill injection resumes on true vehicle cases

Heldout progress:
- `multi_turn_base_68`: score `0.7619`, official valid, injected:
  - `vehicle_fuel_check_before_fill_unnecessary_when_filling_to_capacity__candidate_r0_t19_s0`
  - `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s0`
- `multi_turn_base_86`: score `1.0`, strict/official valid, injected:
  - `vehicle_start_requires_brake_pedal_pressed__candidate_r0_t18_s2`
  - `vehicle_navigation_setup_after_tire_check__candidate_r0_t18_s1`
- `multi_turn_base_88`: score `0.7059`, official invalid, injected:
  - `vehicle_fuel_check_before_fill_unnecessary_when_filling_to_capacity__candidate_r0_t19_s0`

Interpretation:
- Trial/skill injection is not globally disabled; it simply did not match the first heldout cases.
- The `86` result is a key positive signal: explicit navigation intent still retrieves a tire-navigation skill and reaches strict/official valid.
- The `52` vs `86` contrast supports the turnguard intent split: generic guidance no longer gets navigation injection, explicit navigation still does.
- Need to inspect `88` after final results: fuel-fill skill may be neutral or harmful; official invalid means this case requires trace-level comparison.


## 2026-05-20 08:17-08:18 +0800 - Turnguard 20/10 completed

Process `713646` exited and wrote:
- `academic/results/bfcl_trl_turnguard_20_10_20260520_evolve.json`
- `academic/results/bfcl_trl_turnguard_20_10_20260520_skills.json`

Heldout summary:
- strict success: `2/10 = 0.20`
- official valid: `4/10 = 0.40`
- avg score: `0.7197`
- avg total tokens: `83124.2`
- avg elapsed: about `43.02s` from per-case mean

Heldout per-case results:
- `multi_turn_base_68`: score `0.7619`, official valid, injected `vehicle_fuel_check_before_fill_unnecessary_when_filling_to_capacity__candidate_r0_t19_s0`, `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s0`.
- `multi_turn_base_152`: score `0.7273`, official invalid, no injected skills.
- `multi_turn_base_173`: score `0.8889`, official invalid, no injected skills.
- `multi_turn_base_52`: score `0.5714`, official invalid, no injected skills.
- `multi_turn_base_176`: score `0.0`, official invalid, no injected skills.
- `multi_turn_base_65`: score `0.6667`, official invalid, no injected skills.
- `multi_turn_base_86`: score `1.0`, strict/official valid, injected `vehicle_start_requires_brake_pedal_pressed__candidate_r0_t18_s2`, `vehicle_navigation_setup_after_tire_check__candidate_r0_t18_s1`.
- `multi_turn_base_88`: score `0.7059`, official invalid, injected `vehicle_fuel_check_before_fill_unnecessary_when_filling_to_capacity__candidate_r0_t19_s0`.
- `multi_turn_base_108`: score `1.0`, strict/official valid, injected `get_stock_info_before_market_order__candidate_r0_t16_s1`, `trading_order_id_reuse_across_turns__candidate_r0_t6_s0`.
- `multi_turn_base_131`: score `0.875`, official valid, injected `stock_symbol_direct_binding_rule`, `trading_order_review_requires_order_id_resolution__candidate_r0_t11_s1`.

Immediate comparison:
- Same-10 baseline known from earlier analysis: strict `1/10`, official valid `1/10`, avg score `0.7433`.
- SkillX same-10 known from earlier analysis: strict `1/10`, official valid `2/10`, avg score `0.76523`.
- Turnguard improves strict and official validity against both, but avg score is worse.

Decision:
- Do not launch 50/50 from this setting yet.
- Per user instruction, because +TRL does not cleanly beat baseline/SkillX on avg score, prioritize TRL debugging/tuning before any full run.
- Next step is a case-aligned comparison against baseline, previous TRL variants, and SkillX, with focus on regressions `176`, `52`, `65`, `88`, plus skill-positive cases `86`, `108`, `131`.


## 2026-05-20 08:19-08:22 +0800 - Corrected same-10 comparison

Initial parser warning:
- A first recursive parser accidentally treated `cost_events` as task rows, producing `score=None` rows.
- I discarded that output and re-parsed only the formal `test_details` / `details` task containers.

Correct same-10 comparison:
- Baseline 50/50 rerun restricted to this same 10:
  - strict `1/10`, official valid `1/10`, avg score `0.7433`, avg tokens `78591.3`.
- Promotion-gate 20/10:
  - strict `1/10`, official valid `2/10`, avg score `0.7570`, avg tokens `76730.9`.
- Trial-supplement 20/10:
  - strict `1/10`, official valid `3/10`, avg score `0.7793`, avg tokens `83017.5`.
- Lowtrust-gate 20/10:
  - strict `1/10`, official valid `3/10`, avg score `0.7549`, avg tokens `82190.9`.
- Selective-cap 20/10:
  - strict `1/10`, official valid `3/10`, avg score `0.7835`, avg tokens `82568.4`.
- Turnguard 20/10:
  - strict `2/10`, official valid `4/10`, avg score `0.7197`, avg tokens `83124.2`.
- SkillX 50/50 restricted to this same 10:
  - strict `1/10`, official valid `2/10`, avg score `0.7652`, avg tokens `83885.2`.

Case-level observations:
- `68`: turnguard `0.7619/valid`, baseline `0.7368/invalid`, selective `0.8/valid`. Turnguard helps validity but loses some F1 vs selective.
- `86`: turnguard `1.0/valid`, baseline `0.7778/invalid`, selective `0.9412/valid`. Clear positive skill effect.
- `108`: all methods score `1.0/valid`; turnguard preserves this.
- `131`: turnguard `0.875/valid`, baseline `0.6667/invalid`, selective `0.7692/invalid`. Turnguard fixes validity here.
- `52`: turnguard `0.5714/invalid`, baseline `0.5714/invalid`, selective `0.5333/invalid`; removing tire-navigation false-positive returns to baseline-like behavior but does not solve the task.
- `152`: turnguard `0.7273/invalid`, baseline `0.7273/invalid`, SkillX `0.8333/valid`; SkillX is better here.
- `88`: turnguard `0.7059/invalid`, baseline `0.5556/invalid`, SkillX `0.8/invalid`; turnguard helps but SkillX still better.
- `176`: turnguard `0.0/invalid`, baseline/selective/SkillX all `0.6667/invalid`; major regression with no injected skill.
- `65`: turnguard `0.6667/invalid`, baseline/selective `0.8421/invalid`, SkillX `0.6667/invalid`; regression vs baseline/selective with no injected skill.

Interpretation:
- The avg-score regression is concentrated in `176` and `65`.
- Both have no prompt-injected skills in turnguard, so the immediate cause is not an obvious harmful skill injection.
- This points to executor stochasticity, prompt/context differences, or run-order/provider variation rather than a direct TRL retrieval bug.
- However, because turnguard does not beat baseline/SkillX on avg score, this setting should still not be scaled to 50/50 without debugging.

Next action:
- Inspect traces for `176` and `65`, comparing baseline/selective/turnguard/SkillX call errors and actual tool calls.


## 2026-05-20 08:22-08:30 +0800 - Regression trace analysis and probe

Trace analysis for `176`:
- Baseline/selective/SkillX all make the correct `book_flight` call on turn 0 and `cancel_booking` on turn 1.
- They all still fail because the benchmark expected ticket description uses `December 15, 2023`, while the user says `December 15, 2026`; the model follows the user and writes `2026`.
- Turnguard main run scored `0.0` because it missed `book_flight` and `cancel_booking`, and only created the ticket.
- No skill was injected in turnguard for `176`.

Trace analysis for `65`:
- Baseline/selective perform the Twitter actions:
  - `post_tweet(...)` on turn 0
  - `retweet(tweet_id=5)` and `comment(tweet_id=5, ...)` on turn 1
- Turnguard and SkillX miss the Twitter actions and stop after vehicle calls.
- Turnguard retrieved two vehicle skills for `65`, but `prompt_injected_skills` is empty.
- Therefore no skill text should have entered the executor prompt.

Trace analysis for `88`:
- Turnguard's fuel skill helps fix `fillFuelTank(fuelAmount=36.8)`, which baseline/selective often get wrong as `13.2`.
- Turnguard still has extra `displayCarStatus`, extra `posting_get_login_status`, and wrong `pressBrakePedal(pedalPosition=0.5)` instead of `1.0`.
- SkillX is best on this case because it fixes fuel amount without the extra fuel-status call.

Invalid probe:
- I first ran a direct script with `ANTHROPIC_API_KEY=1234abcd` and `OPENAI_API_KEY=1234abcd`.
- This was invalid because it overwrote real credentials and did not correctly register the local proxy config.
- It returned 401 authentication errors and empty traces; stored at `academic/results/bfcl_turnguard_regression_probe_176_65_20260520.json`.
- This file must not be used as behavior evidence.

Valid probe:
- I reran a small direct probe after explicitly registering `LLMSettings` for `local_claude_proxy_4000` and not overriding real provider keys.
- Output: `academic/results/bfcl_turnguard_regression_probe_176_65_20260520_valid.json`.

Valid probe results:
- `noskill / 176`: score `0.6667`, official invalid, same as baseline/selective; makes `book_flight`, `cancel_booking`, `create_ticket`.
- `turnguard_store / 176`: score `0.6667`, official invalid, same as baseline/selective; no retrieved or injected skills.
- `noskill / 65`: score `0.8421`, official invalid, same as baseline/selective; makes `post_tweet`, `retweet`, and `comment`.
- `turnguard_store / 65`: score `0.6667`, official invalid; retrieves two vehicle skills but injects none, and misses `post_tweet`, `retweet`, `comment`.

Interpretation:
- The `176` main-run collapse is a one-off executor/model variation, not a stable TRL regression.
- The `65` degradation reproduces with the turnguard store, but since no skill was actually injected, the observed behavioral difference is not direct skill prompt pollution.
- Code inspection confirms that when `prompt_injected_skills=[]`, native prompt style sends an empty system prompt and no skill constraints; retrieved-but-filtered skills should not be appended to executor messages.
- The remaining plausible explanations are:
  - low-temperature nondeterminism / proxy scheduling variation;
  - the extra injector LLM calls before executor calls perturb provider/cache/timing behavior;
  - or an unobserved dynamic path not visible in compact trace, though code inspection did not find skill text entering the prompt when no skill is injected.

Actionable TRL issue:
- `65` retrieved a useful brake-before-start skill, but the injector rejected it, so the executor never saw a rule that could fix the missing `pressBrakePedal`.
- This suggests the injector is too conservative for multi-intent BFCL turns: it may reject a skill if it only applies to one subtask in a broad user request.
- A likely fix is to adjust the injector rule to allow skills matching any explicit subtask in the current turn, while keeping low-trust exact-match safeguards.


## 2026-05-20 08:32 +0800 - Resumed monitoring and parsed completed 50/50 results

Process check:
- No active project BFCL/SkillX/TRL experiment process was found.
- The only matching project process is the local embedding server: `python -m academic.benchmarks.bfcl.related.simple_embedding_server --host 127.0.0.1 --port 7000 --dim 384`.
- Therefore there is no running experiment to wait on at this moment; analysis continued from completed artifacts.

Completed artifacts found:
- Baseline: `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json`.
- Original TRL 50/50: `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`.
- Injector-gate TRL 50/50: `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json`.
- SkillX aligned 50/50: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_result_with_elapsed.json`.

Parser correction:
- The first quick parser looked for `score` / `metrics` directly on each `details` row.
- These result files store per-run metrics under `details[i].runs[0].metrics`, so that first parse produced meaningless zero values for SkillX.
- I discarded the bad parse and re-parsed from `runs[0].metrics`.

Unified 50/50 metrics on the fixed `curated_related_manifest_50_50.json` test ids:

| method | strict | official valid | avg score | avg tokens | avg elapsed |
|---|---:|---:|---:|---:|---:|
| baseline rerun | `4/50 = 0.08` | `24/50 = 0.48` | `0.7388` | `70551.3` | `35.555s` |
| original TRL | `4/50 = 0.08` | `26/50 = 0.52` | `0.7474` | `76689.5` | `79.253s` |
| injector-gate TRL | `4/50 = 0.08` | `30/50 = 0.60` | `0.7530` | `74175.7` | `49.427s` |
| SkillX aligned | `4/50 = 0.08` | `27/50 = 0.54` | `0.7679` | `78787.6` | `34.927s` |

Immediate interpretation under the user's decision rule:
- `injector-gate TRL` is better than baseline on official valid and avg score, so it is not a baseline-loss failure.
- `SkillX aligned` has higher avg score than our current best TRL (`0.7679` vs `0.7530`), while our current best TRL has higher official valid (`0.60` vs `0.54`).
- This triggers the SkillX comparison path: inspect concrete cases where SkillX gains score, and identify whether its skill style can be borrowed.
- The earlier small-slice `65` issue is still actionable because the turnguard run showed LLM injector over-filtering an explicit engine-start subtask. I will implement a narrow BFCL injector rescue and validate on small cases before any broader rerun.

Key case deltas from baseline / injector-gate TRL / SkillX:
- Our TRL beats both baseline and SkillX on several Vehicle cases by injecting explicit engine/brake/lock rules: `53`, `54`, `58`, `65`, `76`, `88`, `90`.
- SkillX is notably better on `10`, `28`, `75`, `152`, `153`, `194`.
- The strongest SkillX-over-TRL travel cases are `152` (`0.8333/valid` vs TRL `0.5455/invalid`), `153` (`0.9091/valid` vs TRL `0.4444/invalid`), and `194` (`0.7692` vs TRL `0.4444`). SkillX injects the broad skill `flight book with fallback airports` in these.
- Our TRL travel skills on those same cases are fragmented and sometimes harmful: examples include `travel_booking_reuses_prior_booking_id_for_cancellation__candidate_r0_t48_s0`, `create_ticket_priority_and_description_fidelity__candidate_r0_t43_s2`, `travel_booking_requires_all_parameters_from_context__candidate_r0_t44_s0`, and invoice/contact-support skills.

Next planned code change:
- Add a BFCL-specific deterministic injector rescue after the LLM relevance gate.
- Scope: only rescue a candidate that clearly contains `pressBrakePedal` and `startEngine` when the current turn explicitly asks to start/prime/get the engine running.
- Do not loosen navigation rules; generic `guide me` must remain non-navigation intent.
- Add focused tests and run py_compile + targeted pytest.


## 2026-05-20 08:36-08:39 +0800 - First rescue probe on four BFCL cases

Code change before probe:
- Added a BFCL-specific deterministic rescue in `academic/benchmarks/bfcl/adapter.py` after the LLM injector gate.
- Initial scope: rescue skills containing both `pressBrakePedal` and `startEngine` when the current user turn explicitly says phrases like `start the engine` / `get the engine running`.
- Added unit coverage in `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py` for engine-start rescue and no rescue of generic navigation guidance.

Verification before probe:
- `python -m py_compile academic/benchmarks/bfcl/adapter.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py` passed.
- Focused pytest passed: `test_bfcl_injector_rescues_explicit_engine_start_subtask`, `test_bfcl_injector_rescue_does_not_rescue_generic_navigation`, `test_bfcl_low_trust_navigation_does_not_treat_generic_guidance_as_navigation_intent`, `test_bfcl_contextual_order_details_skill_requires_details_turn_intent`, `test_bfcl_trial_supplement_marks_trial_skill_low_trust` -> `5 passed`.

Probe setup:
- Store: `academic/results/bfcl_trl_turnguard_20_10_20260520_skills.json`.
- Cases: `multi_turn_base_65`, `52`, `86`, `88`.
- Output: `academic/results/bfcl_rescue_probe_65_52_86_88_20260520.json`.
- Runtime: no active long experiment was running; this was a bounded four-case probe.

Probe results:
- `65`: score `0.8421`, official invalid, retrieved `vehicle_fuel_check_unnecessary_before_fill_when_amount_explicit__candidate_r0_t19_s1` and `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s0`, injected `[]`, tokens `58724`, elapsed `29.086s`.
- `52`: score `0.5714`, official invalid, injected `[]`, tokens `106562`, elapsed `51.122s`. This preserves the no-navigation false-positive behavior.
- `86`: score `0.9412`, official valid, injected `vehicle_start_requires_brake_pedal_pressed__candidate_r0_t18_s2` and `vehicle_navigation_setup_after_tire_check__candidate_r0_t18_s1`, tokens `113132`, elapsed `44.149s`. No regression in validity.
- `88`: score `0.7500`, official invalid, injected fuel skill only, tokens `68021`, elapsed `32.365s`.

Interpretation:
- The probe did not show runtime failure.
- `65` recovered to baseline-level score but did not use the new deterministic rescue. This means the current rescue intent phrase list is too narrow for the actual BFCL wording.
- Next action: inspect the literal `65` user turns and broaden only the engine-start wording, still without loosening navigation.


## 2026-05-20 08:41-08:51 +0800 - Retrieval-side engine-start rescue debugging

Root cause found after v2 probe:
- The new injector rescue was correct in isolation, but `multi_turn_base_65` did not retrieve the brake-before-start skill on turn 0.
- Static per-turn replay showed `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s0` appeared on turn 1, the Twitter retweet/comment turn, not on the actual engine-start turn.
- With `trial_supplement_k=1`, the turn-0 supplement slot was consumed by a fuel skill: `vehicle_fuel_check_unnecessary_before_fill_when_amount_explicit__candidate_r0_t19_s1`.
- With `trial_supplement_k=10`, engine-start skills passed the low-trust predicate, so the issue was supplement ranking/cap rather than predicate rejection.

Code changes made:
- `academic/benchmarks/bfcl/retrieval.py`:
  - Added `query_requests_engine_start(query)` for explicit engine/ignition wording, including `start up the engine`, `start the vehicle's engine`, and `engine is on`.
  - Added `is_engine_start_brake_skill(skill)`. Initial version over-trusted `allowed_tools`; after debugging, it now requires explicit body/description/interface evidence containing both `pressBrakePedal` and `startEngine`, plus brake/precondition wording.
  - Prepended an engine-start/brake priority bit to `bfcl_skill_rerank_key`.
- `academic/benchmarks/bfcl/adapter.py`:
  - Uses the shared helpers in the injector rescue.
  - Adds a low-trust supplement deterministic rescue: if current turn explicitly requests engine start and the selected supplement lacks an engine-start brake rule, append the first passing brake-before-start candidate as `explicit_engine_start_brake_rescue`.
- `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`:
  - Added focused tests for explicit engine-start rescue and for not rescuing generic navigation guidance.

Verification after code changes:
- `python -m py_compile academic/benchmarks/bfcl/retrieval.py academic/benchmarks/bfcl/adapter.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py` passed.
- Focused pytest passed: `5 passed`.

Static replay after tightening `is_engine_start_brake_skill`:
- Fuel skills no longer classify as engine-start brake skills.
- `multi_turn_base_65` turn 0 retrieves fuel skill plus `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t17_s0`; rescue recorded `explicit_engine_start_brake_rescue`.
- `multi_turn_base_52` turn 0 similarly retrieves an engine-start brake rescue, while turn 1 generic tire-service guidance does not retrieve navigation.
- `multi_turn_base_88` turn 2 retrieves fuel skill plus engine-start brake rescue.

Probe v3 setup:
- Store: `academic/results/bfcl_trl_turnguard_20_10_20260520_skills.json`.
- Cases: `65`, `52`, `86`, `88`.
- Output: `academic/results/bfcl_rescue_probe_v3_65_52_86_88_20260520.json`.

Probe v3 results:
- `65`: score `0.7500`, official invalid, injected `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t17_s0`; this is worse than v2/baseline-like `0.8421`.
- `52`: score `0.7143`, official invalid, injected `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t17_s0`; better than prior `0.5714` without reviving navigation false-positive.
- `86`: score `0.9412`, official valid, unchanged validity.
- `88`: score `1.0000`, strict success and official valid, injected fuel skill plus engine-start brake skill; strong positive effect.

Decision from v3 probe:
- The rescue is not safe enough to scale immediately because it regresses the mixed Twitter+Vehicle case `65`, even though it improves `52` and solves `88`.
- Next action is to inspect `65` tool calls and determine whether the regression is caused by additional prompt load distracting from Twitter actions, or by the specific rescued skill text changing vehicle-call order.


## 2026-05-20 09:01 +0800 - Resumed from handoff and rechecked live experiment state

Process check:
- I ran `ps -eo pid,etime,cmd | rg 'skill_evolving|SkillX|bfcl|trl|evolve|run_bfcl|embedding'`.
- No active BFCL baseline, SkillX, or TRL train/test process was running.
- The only matching process was still the embedding server:
  `python -m academic.benchmarks.bfcl.related.simple_embedding_server --host 127.0.0.1 --port 7000 --dim 384`.
- Therefore there was no live experiment log to wait on or monitor at this moment. I continued from completed artifacts rather than starting a new long run blindly.

Git/worktree note:
- The working tree is dirty with many project edits from this ongoing experiment line.
- Relevant files already modified before this resume include `academic/benchmarks/bfcl/adapter.py`, `academic/benchmarks/bfcl/retrieval.py`, and `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`.
- I did not revert unrelated changes.

Parser correction repeated:
- I initially tried a generic parser that expected `details[i].score` or `details[i].metrics`.
- The actual BFCL result schema stores per-run data at `test_details[i].runs[0]` for our/baseline results and `details[i].runs[0]` for SkillX.
- I discarded the incorrect zero-case parse and re-parsed from `runs[0].metrics`.

Confirmed 50/50 metrics on the same frozen test ids:

| method | strict success | official valid | avg score | avg total tokens | avg elapsed |
|---|---:|---:|---:|---:|---:|
| baseline rerun | `4/50 = 0.08` | `24/50 = 0.48` | `0.7388` | `70551.3` | `35.555s` |
| our injector-gate TRL | `4/50 = 0.08` | `30/50 = 0.60` | `0.7530` | `74175.7` | `49.427s` |
| SkillX aligned | `4/50 = 0.08` | `27/50 = 0.54` | `0.7679` | `78787.6` | `34.927s` |

Decision under the user's rule:
- Our injector-gate TRL is not worse than baseline: official valid improves from `0.48` to `0.60`, and avg score improves from `0.7388` to `0.7530`.
- Therefore I did not enter the "TRL below baseline, tune TRL first" branch.
- SkillX has higher avg score than our best TRL (`0.7679` vs `0.7530`), even though our official valid is higher (`0.60` vs `0.54`).
- Therefore I entered the "SkillX better in some respect, compare concrete skills/behavior and identify borrowable ideas" branch.


## 2026-05-20 09:02 +0800 - Case-level SkillX vs our TRL comparison

I verified that baseline, our injector-gate TRL, and SkillX all cover the same 50 test ids from `curated_related_manifest_50_50.json`.

Cases where SkillX is materially better than our TRL by score or official validity:
- `multi_turn_base_153`: baseline `0.8333/valid`, our `0.4444/invalid`, SkillX `0.9091/valid`, delta `+0.4647`.
- `multi_turn_base_78`: baseline `0.8571/valid`, our `0.6667/invalid`, SkillX `1.0000/valid`, delta `+0.3333`.
- `multi_turn_base_194`: baseline `0.4444/invalid`, our `0.4444/invalid`, SkillX `0.7692/invalid`, delta `+0.3248`.
- `multi_turn_base_75`: baseline `0.4000/invalid`, our `0.2857/invalid`, SkillX `0.6000/invalid`, delta `+0.3143`.
- `multi_turn_base_152`: baseline `0.7273/invalid`, our `0.5455/invalid`, SkillX `0.8333/valid`, delta `+0.2878`.
- `multi_turn_base_10`: baseline `0.5385/invalid`, our `0.5556/invalid`, SkillX `0.7273/invalid`, delta `+0.1717`.
- `multi_turn_base_28`: baseline `0.7692/invalid`, our `0.7692/invalid`, SkillX `0.9231/valid`, delta `+0.1539`.
- `multi_turn_base_9`: baseline `0.6667/invalid`, our `0.5333/invalid`, SkillX `0.6667/invalid`, delta `+0.1334`.
- `multi_turn_base_86`: baseline `0.7778/invalid`, our `0.6667/invalid`, SkillX `0.7778/invalid`, delta `+0.1111`.
- `multi_turn_base_68`: baseline `0.7368/invalid`, our `0.6316/invalid`, SkillX `0.7368/invalid`, delta `+0.1052`.
- `multi_turn_base_143`: baseline `0.8000/valid`, our `0.7273/invalid`, SkillX `0.8000/valid`, delta `+0.0727`.
- `multi_turn_base_137`: baseline `0.8000/valid`, our `0.7273/invalid`, SkillX `0.8000/valid`, delta `+0.0727`.

Cases where our TRL is materially better than SkillX:
- `multi_turn_base_54`: our `0.9333/valid`, SkillX `0.3333/invalid`, delta `+0.6000` for our TRL.
- `multi_turn_base_65`: our `0.9000/valid`, SkillX `0.6667/invalid`, delta `+0.2333` for our TRL.
- `multi_turn_base_179`: our `0.2000/valid`, SkillX `0.0000/invalid`, delta `+0.2000` for our TRL.
- `multi_turn_base_103`: our `0.8000/valid`, SkillX `0.6154/invalid`, delta `+0.1846` for our TRL.
- `multi_turn_base_53`: our `0.7692/valid`, SkillX `0.6154/invalid`, delta `+0.1538` for our TRL.
- `multi_turn_base_76`: our `0.8889/valid`, SkillX `0.7500/invalid`, delta `+0.1389` for our TRL.
- `multi_turn_base_88`: our `0.9333/valid`, SkillX `0.8000/invalid`, delta `+0.1333` for our TRL.
- `multi_turn_base_160`: our `1.0000/valid`, SkillX `0.8889/valid`, delta `+0.1111` for our TRL.

Domain-level approximate grouping:
- FileSystem: `11` cases, our avg `0.7324`, SkillX avg `0.7581`, our valid `6`, SkillX valid `6`.
- Trading: `7` cases, our avg `0.7987`, SkillX avg `0.7917`, our valid `6`, SkillX valid `6`.
- Travel: `17` cases, our avg `0.6868`, SkillX avg `0.7470`, our valid `9`, SkillX valid `8`.
- Vehicle/Twitter: `15` cases, our avg `0.8219`, SkillX avg `0.7877`, our valid `9`, SkillX valid `7`.

Interpretation:
- SkillX's average-score advantage mainly comes from Travel long-transaction cases and a few FileSystem formatting/state cases.
- Our TRL's official-valid advantage mainly comes from Vehicle/Twitter and some Trading/FileSystem cases.
- This argues against replacing our full method with SkillX. The likely borrowable part is SkillX's broader Travel skill representation and task-level plan-guided retrieval.


## 2026-05-20 09:03 +0800 - Literal behavior comparison on strongest SkillX wins

Case `multi_turn_base_153`:
- User flow: verify traveler identity, resolve nearest airport for Rivermist, get business-class cost to GFD, set budget, book flight, retrieve invoice.
- Expected calls: `verify_traveler_information`, `get_flight_cost(travel_from='RMS', ...)`, `set_budget_limit`, `book_flight`, `retrieve_invoice`.
- Our retrieved skills:
  `skip_airport_lookup_when_destination_explicit_iata__candidate_r0_t39_s0`,
  `travel_booking_requires_all_parameters_from_context__candidate_r0_t44_s0`,
  `travel_budget_requires_currency_conversion_before_set__candidate_r0_t44_s2`,
  `travel_booking_reuses_prior_booking_id_for_cancellation__candidate_r0_t48_s0`,
  `travel_booking_reuses_prior_booking_id_for_cancellation__candidate_r0_t48_s1`,
  `retrieve_invoice_omit_insurance_id_when_not_required__candidate_r0_t42_s0`,
  `retrieve_invoice_omit_insurance_id_when_not_required__candidate_r0_t43_s1`.
- Our injected skills:
  `travel_booking_requires_all_parameters_from_context__candidate_r0_t44_s0`,
  `retrieve_invoice_omit_insurance_id_when_not_required__candidate_r0_t42_s0`,
  `retrieve_invoice_omit_insurance_id_when_not_required__candidate_r0_t43_s1`.
- Our actual calls:
  `verify_traveler_information(...)`,
  `get_nearest_airport_by_city(location='Rivermist')`,
  `get_flight_cost(travel_from='Rivermist', travel_to='GFD', ...)`,
  `set_budget_limit(...)`.
- Our errors:
  wrong `travel_from='Rivermist'` instead of resolved `RMS`, then missing `book_flight` and missing `retrieve_invoice`.
- SkillX retrieved/injected only `flight book with fallback airports`.
- SkillX actual calls:
  `verify_traveler_information(...)`,
  `get_nearest_airport_by_city(location='Rivermist')`,
  `get_flight_cost(travel_from='RMS', travel_to='GFD', ...)`,
  `set_budget_limit(...)`,
  `book_flight(... travel_from='RMS', travel_to='GFD' ...)`,
  `retrieve_invoice(access_token='abc123xyz', booking_id='3426812')`.
- Concrete difference: SkillX preserves the resolved airport code across turns and keeps executing the long transaction chain after budget-setting.

Case `multi_turn_base_152`:
- User flow: cost-check and book SFO to ORD, cancel booking, create urgent ticket.
- Expected calls include `get_flight_cost`, `book_flight`, `cancel_booking`, `ticket_login`, `create_ticket(priority=5)`.
- Our injected skills:
  `travel_booking_reuses_prior_booking_id_for_cancellation__candidate_r0_t48_s0`,
  `create_ticket_priority_and_description_fidelity__candidate_r0_t43_s2`,
  `create_ticket_priority_from_context__candidate_r0_t46_s0`.
- Our actual calls:
  `get_nearest_airport_by_city(San Francisco)`,
  `get_nearest_airport_by_city(Chicago)`,
  `get_flight_cost(SFO, ORD, ...)`,
  `book_flight(SFO, ORD, ...)`,
  `ticket_login(...)`,
  `create_ticket(... priority=4)`.
- Our missing/wrong behavior: skipped `cancel_booking`; set priority `4` instead of expected `5`.
- SkillX retrieved/injected `flight book with fallback airports`.
- SkillX actual calls:
  same first four booking calls,
  then `cancel_booking(access_token='secureAccessToken12345', booking_id='3426812')`,
  `ticket_login(...)`,
  `create_ticket(... priority=5)`.
- Concrete difference: SkillX preserved the booking-id chain and did not let ticket-priority local skill drift the priority value.

Case `multi_turn_base_194`:
- User flow: list airports, cost first-to-last airport, book, buy insurance, retrieve invoice, contact support, cancel booking.
- Our injected skills:
  `travel_booking_requires_all_parameters_after_cost_check__candidate_r0_t44_s1`,
  `contact_customer_support_message_fidelity`,
  `travel_booking_reuses_prior_booking_id_for_cancellation__candidate_r0_t48_s0`.
- Our actual calls:
  `list_all_airports()`,
  `get_flight_cost(travel_from='RMS', travel_to='BOS', ...)`.
- Our missing behavior: `book_flight`, `purchase_insurance`, `retrieve_invoice`, `contact_customer_support`, `cancel_booking`.
- SkillX retrieved/injected `flight book with fallback airports`.
- SkillX actual calls:
  `list_all_airports()`,
  `get_flight_cost(RMS, BOS, ...)`,
  `book_flight(...)`,
  `purchase_insurance(...)`,
  `retrieve_invoice(... insurance_id='498276044')`,
  `cancel_booking(...)`.
- SkillX still missed `contact_customer_support` and included optional `insurance_id`, so it is not perfect, but it continues the transaction instead of stopping after cost.

Case `multi_turn_base_78`:
- User flow: check tire pressure; if below threshold, find nearest tire shop; then post tweet.
- Our injected skills:
  `vehicle_tire_pressure_navigation_workflow__candidate_r0_t31_s1`,
  `vehicle_check_tire_pressure_healthy_flag_stops_navigation__candidate_r0_t30_s1`.
- Our actual calls:
  `check_tire_pressure()`,
  `posting_get_login_status()`,
  `post_tweet(...)`.
- Our missing behavior: no `find_nearest_tire_shop()`.
- SkillX injected no skills.
- SkillX actual calls:
  `check_tire_pressure()`,
  `find_nearest_tire_shop()`,
  `post_tweet(...)`.
- Concrete difference: our injected "healthy flag stops navigation" rule is harmful here because the expected behavior uses the explicit threshold from the user, not the `healthy_tire_pressure` boolean.

Case `multi_turn_base_28`:
- Expected final write is exactly `205 bytes`.
- Our actual final write was `205.00 bytes`; SkillX wrote `205 bytes`.
- Neither method retrieved/injected a named skill.
- This is likely executor/prompt stochasticity or base instruction difference rather than a skill-library advantage.

Case `multi_turn_base_75`:
- User asks to top up fuel before turning on ignition using START mode, then tweet tire pressures, retweet, comment with typo-preserved text.
- Our injected skills:
  `press_brake_before_starting_engine__candidate_r0_t18_s1`,
  `fillFuelTank_no_displayCarStatus_when_target_total_given__candidate_r0_t32_s0`.
- Our actual calls:
  `fillFuelTank(30)`,
  extra `pressBrakePedal(1.0)`,
  extra `startEngine(START)`,
  extra `lockDoors(...)`,
  extra second `startEngine(START)`,
  `check_tire_pressure()`,
  extra `posting_get_login_status()`,
  `post_tweet(...)` with decimal PSI formatting,
  no `retweet`,
  `comment(...)` but corrected user's misspelling from `pressue` to `pressure`.
- SkillX injected no skills.
- SkillX actual calls:
  `fillFuelTank(30)`,
  `startEngine(START)`,
  `check_tire_pressure()`,
  `post_tweet(...)` with decimal PSI formatting,
  `comment(...)` preserving `pressue`.
- Concrete difference: our engine/brake skill over-applies and adds safety-precondition operations that official expected does not want for this task; SkillX avoids that harmful injection and preserves typo fidelity in comment.


## 2026-05-20 09:04 +0800 - SkillX skill library and framework comparison

SkillX skill library inspected:
- Path: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_extraction/skillx_skill_library.json`.
- Structure:
  - `planning`: `23` task-level plans.
  - `functional`: `4` skills.
  - `atomic`: `0` skills.

Most important functional skill:
- Name: `flight book with fallback airports`.
- Document: "Attempt to book a flight with primary airport codes, and if the booking fails due to invalid airport codes, automatically retry with alternative airport codes for the same city."
- Content:
  ```python
  booking_result = apis.flight.book_flight(
      access_token=access_token,
      card_id=card_id,
      travel_date=travel_date,
      travel_from=travel_from,
      travel_to=travel_to,
      travel_class=travel_class
  )

  if not booking_result.get('booking_status') and 'Invalid destination airport code' in booking_result.get('error', ''):
      booking_result = apis.flight.book_flight(
          access_token=access_token,
          card_id=card_id,
          travel_date=travel_date,
          travel_from=travel_from,
          travel_to=travel_to_fallback,
          travel_class=travel_class
      )
  ```
- Tools: `apis.flight.book_flight`.

Important observation:
- The literal code only wraps `book_flight`, but the document and the retrieved planning context induce a broader behavior: keep airport codes resolved, proceed to booking, then use resulting booking id for downstream invoice/cancel operations.
- In the SkillX 50/50 test, this single skill was repeatedly retrieved for Travel tasks `152`, `153`, and `194`.

SkillX inference code inspected:
- `/home/lixujun/external_repos/SkillX/inference/skill_usage.py`.
- `/home/lixujun/external_repos/SkillX/inference/retriever.py`.
- `/home/lixujun/external_repos/SkillX/inference/prompt_formatters.py`.
- `/home/lixujun/external_repos/SkillX/inference/benchmarks/bfcl.py`.

SkillX inference mechanism:
- It first retrieves similar task-level plans with `retrieve_plan(task, top_k=3)`.
- It then optionally rewrites the plan.
- If a plan exists, it retrieves skills for each plan step using `retrieve_skills_for_plan(plan, skills_per_step=4, tool_filter=available_tools)`.
- The BFCL prompt formatter inserts skill descriptions and content into the system prompt as reference material.
- The BFCL formatter says the skill library is reference only and the actual calls must follow the provided tool specs.
- It does not expose callable skills in the BFCL setting; the executor still calls native BFCL tools.

Contrast with our current BFCL TRL:
- Our final test trace shows per-case `retrieved_skills` and `prompt_injected_skills`, but no callable skill tools: `available_skill_tool_count=0`, `skill_injection_mode=prompt_only`.
- Our retrieved Travel skills are mostly local cards:
  - `travel_booking_requires_all_parameters_from_context__candidate_r0_t44_s0`
  - `travel_booking_requires_all_parameters_after_cost_check__candidate_r0_t44_s1`
  - `travel_booking_reuses_prior_booking_id_for_cancellation__candidate_r0_t48_s0`
  - `retrieve_invoice_omit_insurance_id_when_not_required__candidate_r0_t42_s0`
  - `contact_customer_support_message_fidelity`
- These local cards are individually reasonable but do not act like a single durable transaction-chain plan.
- The result is that the model can satisfy one local rule and still stop before later turns (`153`, `194`) or fail to carry booking id into cancellation (`152`).

Borrowable ideas from SkillX:
- Preserve task-level planning skills as first-class retrieval material for BFCL, especially for domains like TravelAPI where the useful behavior is an ordered transaction chain.
- Add plan-guided retrieval: retrieve a broad plan by the full multi-turn task, then retrieve per-step skills from that plan, rather than only scoring narrow cards against each current turn.
- For TravelAPI, extractor/refiner should be allowed to produce a bounded "transaction-chain workflow" card that explicitly covers:
  `airport/code resolution -> cost check -> book_flight -> use booking_id for insurance/invoice/support/cancel`.
- The broad card must be concise and guarded by domain/tool names to avoid turning into a verbose prompt blob.
- Keep our existing Vehicle/Twitter safeguards and do not globally switch to SkillX behavior, because our current TRL beats SkillX on several Vehicle cases and has higher official validity overall.

Risks and non-borrowable parts:
- Directly enabling the engine-start rescue by default is risky: the v3 probe improved `52` and `88` but regressed `65`.
- Directly replacing our executor with SkillX is not justified by results: SkillX has lower official valid than our current best TRL and its BFCL formatter is also reference-only system prompt guidance.
- Copying only the `flight book with fallback airports` code is insufficient; the observed gains come from the surrounding plan retrieval and broad task-level prompt context.

Next implementation candidate:
- Add BFCL TravelAPI transaction-chain extraction/refinement guidance and a plan-level retrieval supplement.
- Validate only on a small targeted set first:
  - positive targets: `152`, `153`, `194`.
  - regression guards: `54`, `65`, `78`, `88`, and one or two Trading/FileSystem cases.
- Do not run a full 50/50 until the targeted probe improves Travel without damaging Vehicle/Twitter.

## 2026-05-21 +0800 - Fresh SkillX no-memory baseline on the aligned BFCL 50/50 split

User asked to run the SkillX baseline and inspect its effect.

I first rechecked the existing SkillX-aligned run:
- SkillX-aligned result: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_result_with_elapsed.json`.
- It is a diagnostic aligned run, not a strict official SkillX embedding reproduction, because `embedding_caveat.json` says local `Qwen3-Embedding-8B` was unavailable and a local deterministic/simple embedding endpoint was used.
- It extracted a SkillX library with `23` planning skills, `4` functional skills, and `0` atomic skills.
- Test-time provider events show `191` retrieval events over `50` tasks; event histogram by selected-skill count was `{0: 84, 1: 53, 2: 26, 3: 28}`.
- SkillX injected reference text, not callable skill tools. `called_skill_tools` remained empty by design.

Then I launched a fresh no-memory baseline on the same curated BFCL 50/50 manifest:

```bash
ANTHROPIC_API_KEY=1234abcd OPENAI_API_KEY=1234abcd BFCL_RELATED_TEST_CONCURRENCY=4 \
python -m academic.benchmarks.bfcl.related.experiment \
  --mode baseline \
  --manifest academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json \
  --expected-train-size 50 \
  --expected-test-size 50 \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --data-source bfcl_eval_bundle \
  --execution-backend official \
  --prompt-style native \
  --tool-api-style auto \
  --max-steps-per-turn 20 \
  --max-task-seconds 180 \
  --temperature 0.0 \
  --test-concurrency 4 \
  --tag skillx_bfcl_no_memory_baseline_50_50_20260521 \
  --output academic/results/skillx_bfcl_aligned/skillx_bfcl_no_memory_baseline_50_50_20260521.json
```

Output:
- Result: `academic/results/skillx_bfcl_aligned/skillx_bfcl_no_memory_baseline_50_50_20260521.json`.
- Log: `academic/results/skillx_bfcl_aligned/logs/skillx_bfcl_no_memory_baseline_50_50_20260521.log`.
- The log confirmed every task used `skill_injection_mode=none`, with empty `prompt_injected_skills`, `tool_injected_skills`, and `called_skill_tools`.

Fresh no-memory baseline metrics:
- Strict success: `4/50 = 0.08`.
- Official valid: `25/50 = 0.50`.
- Avg score: `0.7446`.
- Avg total tokens: `69286.1`.
- Avg elapsed: `32.57s`.
- Timeout rate: `0.0`.

SkillX-aligned diagnostic metrics on the same 50 heldout ids:
- Strict success: `4/50 = 0.08`.
- Official valid: `27/50 = 0.54`.
- Avg score: `0.7679`.
- Avg total tokens: `78787.6`.
- Avg elapsed: `34.927s`.
- Timeout rate: `0.0`.

Net effect of SkillX-aligned vs fresh no-memory baseline:
- Strict success: unchanged, `+0/50`.
- Official valid: `+2/50`.
- Avg score: `+0.0233`.
- Avg total tokens: `+9501.5` per task, about `+13.7%`.
- Avg elapsed: `+2.357s` per task, about `+7.2%`.

Largest SkillX-over-baseline score gains:
- `multi_turn_base_194`: `0.4000 -> 0.7692`, `+0.3692`.
- `multi_turn_base_10`: `0.5556 -> 0.7273`, `+0.1717`.
- `multi_turn_base_28`: `0.7692 -> 0.9231`, `+0.1539`, official valid improved.
- `multi_turn_base_9`: `0.5333 -> 0.6667`, `+0.1334`.
- `multi_turn_base_88`: `0.6667 -> 0.8000`, `+0.1333`.
- `multi_turn_base_152`: `0.7273 -> 0.8333`, `+0.1060`, official valid improved.

Largest SkillX regressions:
- `multi_turn_base_179`: `0.1818 -> 0.0000`, `-0.1818`, official valid regressed.
- `multi_turn_base_103`: `0.6667 -> 0.6154`, `-0.0513`.
- `multi_turn_base_86`: `0.8235 -> 0.7778`, `-0.0457`.
- `multi_turn_base_145`: `0.5455 -> 0.5000`, `-0.0455`.

Official-valid transitions:
- Improved to valid: `multi_turn_base_138`, `multi_turn_base_152`, `multi_turn_base_28`.
- Regressed from valid: `multi_turn_base_179`.

SkillX selected-skill task coverage:
- `trading analyze pending order cancellation decision`: `21` tasks.
- `stock get most recent order id`: `13` tasks.
- `stock cancel order`: `13` tasks.
- `flight book with fallback airports`: `12` tasks.

Interpretation:
- SkillX-aligned gives a real but modest gain over a clean no-memory baseline on this split: mainly average score and a small official-valid gain.
- The improvement is not from callable execution; it is from reference prompt guidance and plan/skill retrieval.
- The strongest gains are still Travel/FileSystem-like cases where broad workflow context helps. The main regression is a Travel case where retrieved guidance appears to push the model into a wrong or incomplete transaction path.
- Because this SkillX run uses non-official local/simple embeddings, it should be reported as `SkillX-aligned diagnostic`, not as a strict SkillX reproduction.
