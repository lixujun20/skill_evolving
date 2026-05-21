# BFCL TRL Maturity-Gated 20/50 Case Analysis 2026-05-20

## Run Setup

- Tag: `bfcl_align_trl_maturity_train20_50_20260520`
- Train-only manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_0_trl_maturity_20260520.json`
- Test manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_50.json`
- Train-only output: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_trainonly.json`
- Final 20/50 output: `academic/results/bfcl_align_trl_maturity_train20_50_20260520.json`
- Checkpoint for later 50/50 continuation: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_checkpoint.json`
- Skills: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_skills.json`
- Train log: `academic/results/logs/bfcl_align_trl_maturity_train20_50_20260520_train.log`
- Test log: `academic/results/logs/bfcl_align_trl_maturity_train20_50_20260520_test.log`

Core settings:

- `BFCL_SKILL_INJECTOR_GATE=deterministic`
- `BFCL_DYNAMIC_RETRIEVAL_POLICY=every_step`
- `BFCL_EXTRACTOR_EXISTING_ARTIFACTS=full_store`
- `BFCL_ALLOW_PREFIX_CHECKPOINT_EXTEND=1`
- `BFCL_TRL_MIN_EXPOSURE_TASK_RATIO` defaulted to `1.0`
- `BFCL_TRL_ALLOW_LOW_USAGE_FEEDBACK` defaulted to `false`
- `--enable-candidate-competition --candidate-sample-count 3`
- `--macro-maintenance-step 10`
- `--test-concurrency 4 --train-window-concurrency 4`
- `--max-steps-per-turn 20 --max-task-seconds 180`

## Monitoring Result

Training completed all 20 train tasks before heldout testing. There were no `Traceback`, timeout, request error, or parse error events in the train or test logs.

The staged protocol worked:

1. Ran train-only `20/0`.
2. Inspected candidate group feedback, role feedback, and skills.
3. Fixed one competition decision bug discovered by case inspection.
4. Ran heldout `50` from the preserved 20-task checkpoint without redoing training.

## Train Metrics

| phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | avg_elapsed_s | avg_steps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 20 | 0.45 | 0.75 | 0.9069 | 0.9410 | 0.8960 | 49712.9 | 32.014 | 9.05 |

Previous aligned TRL 20/50 train was strict `0.45`, official_valid `0.80`, avg_score `0.9114`.

## TRL Case Inspection

Two mature candidate groups entered TRL at macro index 1.

### Group `extract:r0:t4:multi_turn_base_121`

Winner: `verify_balance_before_withdrawal__candidate_r0_t4_s0`

Members were near-duplicates:

- `s0`: retrieved/injected `5/5`, used `0`, helpful `0`, harmful `0`, neutral `5`, score `5`
- `s1`: retrieved/injected `4/4`, used `0`, helpful `0`, harmful `0`, neutral `4`, score `4`
- `s2`: retrieved/injected `2/2`, used `0`, helpful `0`, harmful `0`, neutral `2`, score `2`

Credit records consistently said the skill was about withdrawal, while the second-window tasks were TradingBot/watchlist/order tasks. This is objective evidence of over-retrieval and duplicate extraction, not evidence that the withdrawal guardrail itself is bad.

### Group `extract:r0:t7:multi_turn_base_135`

Winner: `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s2`

Members again had only neutral/no-use evidence:

- winner: retrieved/injected `7/7`, used `0`, helpful `0`, harmful `0`, neutral `7`, score `7`
- other members: lower exposure but also neutral/no-use

This gave useful meta-signal: candidate sampling produced redundant or overly similar alternatives, and some skills retrieved on tasks where they had no opportunity.

## TRL Rule Output

The extractor feedback LLM output was qualitatively reasonable. It produced rules such as:

- Extract at most one candidate per distinct behavioral pattern from each source task.
- Scope workflow guardrails tightly to specific operation types.
- Include clear triggering conditions in the contract.
- Specify both action sequence and activating context.
- Prefer reusable patterns over task-specific sequences.

Important caveat: the evidence was mostly neutral/no-opportunity. It is useful for anti-duplication and retrieval-scope feedback, but should not be treated as positive/negative skill quality evidence.

## Bug Found And Fixed

During manual inspection, six candidate losers had been archived even though they had `harmful_count=0` and only neutral evidence. The archive condition was:

```python
winner_score - score >= 3 and harmful_count >= helpful_count
```

For neutral-only losers this becomes `0 >= 0`, so the archive path fired incorrectly.

Fix:

```python
winner_score - score >= 3 and member_harmful > 0 and member_harmful >= member_helpful
```

The already-written training artifacts were repaired by restoring six neutral-only archived losers to `trial` and marking them with `neutral_archive_restored_after_maturity_gate_fix=true`. Backups were written with suffix `.pre_neutral_archive_fix`.

## Test Metrics

| phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | avg_elapsed_s | avg_steps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| test | 50 | 0.08 | 0.54 | 0.7655 | 0.8498 | 0.7102 | 73192.1 | 36.348 | 9.60 |

Comparison:

| run | strict | official_valid | avg_score | notes |
|---|---:|---:|---:|---|
| new maturity-gated TRL 20/50 | 0.08 | 0.54 | 0.7655 | candidate-group-only TRL; neutral archive fixed |
| previous aligned TRL 20/50 | 0.10 | 0.52 | 0.7565 | had immature/single-skill feedback issues |
| aligned no-TRL competition deterministic 50/50 | 0.12 | 0.74 | 0.7901 | trained on 50 tasks, much broader skill coverage |

## Heldout Case Notes

- `multi_turn_base_108`: injected `tradingbot_direct_symbol_binding_for_explicit_company_names` and `resolve_market_price_for_stock_order`; strict and official success.
- `multi_turn_base_131`: injected the same two TradingBot skills; official valid but one extra `get_user_id`, so strict failed.
- `multi_turn_base_113`: injected the two TradingBot skills, but failed on watchlist symbol mismatch and missing later calls. Current active skills do not cover this case enough.
- `multi_turn_base_54` and `multi_turn_base_76`: no injected skills; both are vehicle/social tasks and still fail. The 20-task training prefix did not produce promoted vehicle skills, unlike the 50-task no-TRL run.

Heldout injected skills appeared in 13/50 test runs, always the two active TradingBot skills:

- `tradingbot_direct_symbol_binding_for_explicit_company_names`
- `resolve_market_price_for_stock_order`

## Interpretation

The maturity gate fixed the most dangerous TRL failure mode: newly generated or single-skill feedback no longer directly updates extractor rules. The candidate-group TRL update now uses objective exposure and credit records, and the LLM's meta-rules are reasonable.

The main remaining limitation is coverage and promotion. With only 20 train tasks, the repository ended with 2 active skills and 9 trial candidate skills. Heldout trial skills are not exposed by default, so test coverage is narrow. This explains why official_valid is still far below the 50-task no-TRL run.

For the next 50/50 continuation, the checkpoint is suitable: it preserves the 20-task prefix state and completed sidecars. The key thing to watch is whether later training produces broader active skills, especially Vehicle and non-TradingBot workflows, without promoting neutral-only candidates.
