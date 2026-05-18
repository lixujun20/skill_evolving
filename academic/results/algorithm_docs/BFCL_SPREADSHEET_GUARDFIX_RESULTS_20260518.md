# BFCL/Spreadsheet Guard-Fix Pilot Results, 2026-05-18

## 摘要

本轮实验使用 `claude-sonnet-4-5` 的本地 proxy 接口。BFCL 使用 curated related 50/50 manifest；训练阶段在 50 个 train task 上做 1 个 epoch 的在线 skill evolution，随后冻结最终 skill store，在 50 个 held-out task 上重测。SpreadsheetBench 当前只跑 baseline test 50。

原始 BFCL evolve 输出的 held-out test 后 30/50 task 连续 `Task exceeded 180.0 seconds`，判断为网络/proxy 故障，不作为算法结果。随后使用同一个 frozen evolved skill snapshot 重跑 held-out test，重测无 timeout，作为本轮有效 BFCL evolve test 结果。

## 结果表

| Benchmark | Setting | Success | Official valid | Avg score | Avg tokens/task | Timeout |
|---|---:|---:|---:|---:|---:|---:|
| BFCL related 50/50 | baseline, no skills | 0.06 | 0.44 | 0.7312 | 70,323.8 | 0.00 |
| BFCL related 50/50 | evolve, 1 epoch, frozen-store test rerun | 0.08 | 0.74 | 0.7991 | 86,813.3 | 0.00 |
| SpreadsheetBench-Verified | baseline, test 50 | 0.22 | N/A | 0.2564 | 1,552.1 | 0.00 |

BFCL paired comparison against baseline:

| Metric | Value |
|---|---:|
| Avg score delta | +0.0679 |
| Score improved tasks | 23 |
| Score worse tasks | 5 |
| Score unchanged tasks | 22 |
| Official-valid gains | 18 |
| Official-valid losses | 3 |
| Exact-success gains | 1 |
| Exact-success losses | 0 |

## 相比上一版问题的改进

上一版主要问题是：大量 skill 留在 pending、refiner 经常 keep、injector/retrieval 会把跨域或弱相关 skill 注入到错误任务中，导致 skill 层看起来没有真正工作。本轮 guard-fix 后，有几个明确改善：

1. Retrieval guard 开始发挥作用。检索现在结合 domain、allowed tools 和 intent；没有 concrete domain 的 skill 需要同时满足 tool overlap 和 intent overlap，跨域误注入被明显压低。

2. Credit filter 不再简单 disable 有正面证据的 skill。带有足够 helpful evidence 的 skill 会被保护；强 harmful 且无正面保护的 skill 会被禁用或要求 scope refine。

3. Refiner 不再允许强 harmful evidence 下无条件 keep。若 LLM 返回 keep 但 credit 明确要求 narrow scope/fix contract，代码会落到 deterministic fallback，给 skill 增加 retrieval guard 或做 minor refine。

4. Active skill 真的进入 held-out test。重测中四个 active skill 都被注入到 held-out tasks，并且相关任务的 official-valid 率明显高于 baseline。

5. 原始 evolve held-out 的后半段失败被定位为环境故障。训练阶段 50/50 无 timeout，maintenance LLM 无超过 60s 的卡死调用；后半段 30 个 timeout 只出现在 held-out test，因此重测是合理的。

## Skill 仓库状态

最终 skill snapshot:

| Status | Count |
|---|---:|
| active | 4 |
| disabled | 1 |
| archived | 38 |
| total | 43 |

版本来源:

| Version kind | Count |
|---|---:|
| seed | 38 |
| refactor | 3 |
| minor | 2 |

Active skills:

| Skill | Version | Domain | Allowed tools | Source tasks | Held-out injections | Official-valid on injected tasks |
|---|---:|---|---|---:|---:|---:|
| `vehicle_engine_start_brake_pedal_prerequisite` | 1 | VehicleControlAPI | `pressBrakePedal`, `startEngine` | 5 | 14 | 12/14 |
| `vehicle_engine_start_brake_pedal_requirement` | 1 | VehicleControlAPI | `pressBrakePedal`, `startEngine` | 5 | 14 | 12/14 |
| `multi_turn_order_verification_workflow` | 9 | TradingBot | `get_order_details` | 5 | 13 | 10/13 |
| `contact_customer_support_message_brevity` | 2 | TicketAPI, TravelAPI | `contact_customer_support` | 4 | 12 | 9/12 |

The two vehicle skills are near-duplicates and should be merged in a later macro-refactor pass. Still, they show useful retrieval and workflow alignment: injected VehicleControl tasks reach 12/14 official-valid. The order-verification workflow also generalizes from TradingBot train tasks to held-out tasks. The support-message brevity skill is broader and less clean, but still has 9/12 official-valid on injected tasks.

## Credit 和 filtering 行为

Credit summary:

| Skill | Retrieved/injected | Helpful | Harmful | Neutral | Negative margin | Decision |
|---|---:|---:|---:|---:|---:|---|
| `buy_stock_at_current_market_price` | 3/3 | 0 | 3 | 0 | +3 | disabled |
| `multi_turn_order_verification_workflow` | 10/10 | 5 | 2 | 3 | -3 | kept |
| `vehicle_engine_start_brake_pedal_prerequisite` | 9/9 | 8 | 0 | 1 | -8 | kept |

This is the clearest sign that the new credit/filter path is doing useful repository governance. A harmful trading skill was disabled after repeated negative credit. Two high-evidence skills were retained despite some noisy cases. The filter no longer treats every harmful event as a reason to remove a skill when there is enough positive evidence.

## Cost

Held-out inference cost:

| Setting | Avg tokens/task | Avg model steps/task | Avg elapsed/task |
|---|---:|---:|---:|
| BFCL baseline | 70,323.8 | 9.62 | 32.64s |
| BFCL evolve rerun | 86,813.3 | 10.16 | 34.68s |
| Spreadsheet baseline | 1,552.1 | N/A | 15.24s |

BFCL held-out token cost increased by about 23.5%. This is a limitation of the current prompt-only skill exposure: the skill context improves workflow correctness, but it adds prompt tokens and does not yet reliably reduce model/tool steps.

BFCL training-time maintenance cost:

| Role | Calls | Tokens |
|---|---:|---:|
| extractor | 50 | 391,031 |
| refiner | 23 | 139,262 |
| credit_assigner | 19 | 100,248 |
| refactorer | 7 | 62,382 |
| bundle_builder | 6 | 46,351 |
| extractor_feedback | 1 | 5,111 |
| total | 106 | 744,385 |

The largest training-time cost is extractor. The second-largest is refiner, which means the current micro-maintenance loop is still expensive. Bundle pressure is lower than earlier failed designs but still not negligible.

## 如何解释 baseline 效果低

BFCL baseline exact success is low (`0.06`) because exact task success is harsher than official-valid and call-level score. The baseline average score is `0.7312`, and official-valid is `0.44`, so the model is often close but fails strict multi-turn state or exact argument constraints.

Main baseline failure types:

| Error type | Count |
|---|---:|
| `multi_turn:instance_state_mismatch` | 20 |
| `multi_turn:empty_turn_model_response` | 6 |
| `multi_turn:execution_response_mismatch` | 2 |

This means the main failure mode is not inability to call tools at all, but subtle multi-turn state mismatch, extra/missing calls, and exact argument formatting.

## 剩余问题

1. Exact success remains low. Evolve improves official-valid and call-level score, but exact success only moves from 0.06 to 0.08.

2. Token cost increases. The current result does not support the paper claim of lower BFCL inference overhead. The paper should state this pilot result honestly and mark token reduction as future/ablation target for BFCL.

3. Duplicate vehicle skills remain active. The relation/refactor path should merge `vehicle_engine_start_brake_pedal_prerequisite` and `vehicle_engine_start_brake_pedal_requirement`.

4. Many generated skills are archived. This is not necessarily bad, but the paper should report active/archived ratio and explain that conservative filtering is intentional.

5. Spreadsheet has only baseline. We should not claim Spreadsheet evolution gains until the benchmark-specific LLM skill maintenance path is enabled and tested.

## Files

- BFCL baseline: `academic/results/claude_proxy_related50_50_guardfix_20260517_232531_baseline.json`
- BFCL original evolve with faulty held-out tail: `academic/results/claude_proxy_related50_50_guardfix_20260517_232531_evolve.json`
- BFCL frozen-store held-out rerun: `academic/results/bfcl_guardfix_trainedstore_test50_rerun2_20260518_012134.json`
- BFCL analysis: `academic/results/claude_proxy_related50_50_guardfix_20260517_232531_analysis.json`
- BFCL final skills: `academic/results/claude_proxy_related50_50_guardfix_20260517_232531_final_skills.json`
- Spreadsheet baseline: `academic/results/spreadsheet_baseline_test50_20260517_233447.json`
