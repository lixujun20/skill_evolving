# BFCL 最新 run 与上次有记录 run 的系统比较

比较对象：

- 最新 run：`academic/results/bfcl_train50_20260518_202840.json`
- 上次有记录 evolve run：`academic/results/claude_proxy_related50_50_guardfix_20260517_232531_evolve.json`
- 中间 frozen-store rerun：`academic/results/bfcl_guardfix_trainedstore_test50_rerun2_20260518_012134.json`

## 0. 固定 split 与 shuffle 澄清

BFCL 主实验有固定 train/test set，不是每次随机选。

- 固定 50/50 manifest：`academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`
- train_task_ids：50，hash `5d8d5179e3536f32`
- test_task_ids：50，hash `0ab3f3f8d6572175`
- train/test overlap：0
- manifest 构建方法：deterministic relatedness ranking；文件落盘后冻结。
- 训练顺序：`tasks_from_manifest()` 按 `manifest["train_task_ids"]` 顺序加载；evolve 主循环 `for task_index in range(...)` 顺序提交训练记录，不 shuffle。
- 并发说明：`train_window_concurrency=4` 会在 macro window 内并发 rollout/precompute，但结果按 `task_index` 顺序提交，macro barrier 后再维护窗口。
- test 顺序：按 `manifest["test_task_ids"]` 加载；即使 `test_concurrency=4`，汇总应保持 manifest order。

此前 `cost_retest_bfcl_fullskill_20260518.json` 和 `cost_retest_bfcl_compact_20260518.json` 的 exact success 0.22 不是固定 heldout 50 的主结果。它们的 50 task 中只有 10 个属于 curated heldout test，另有 17 个 curated train task 和 23 个 manifest 外 task。因此它们只能作为 cost/injector diagnostic，不能解释为“同一个 heldout test 曾经 0.22，后来掉到 0.08”。

## 1. 共同点

两次完整 evolve 都使用同一个 50/50 manifest：

- `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`
- `epochs=1`
- `micro_maintenance_step=1`
- `macro_maintenance_step=10`
- `train_window_concurrency=4`
- `micro_concurrency=4`
- `candidate_trial_retrieval=true`
- `candidate_competition_enabled=false`
- `skill_injection_mode=prompt_only`
- `llm_config=local_claude_proxy`
- `model=claude-sonnet-4-5`

因此，这不是不同 train/test split 的差异。主要差异来自代码版本、注入参数、cost/accounting 路径、以及上次 heldout test 的 timeout 异常。

## 2. 参数差异

| 项 | 上次 guardfix evolve | 最新 bfcl_train50 |
|---|---:|---:|
| top_k_skills | 旧记录中为 3 或由当时默认控制 | 2 |
| test_concurrency | 已并行化，但结果中 test timeout 异常 | 4 |
| cost accounting | 只有 avg_total_tokens | input/cache-input/output、role、phase、skill_prompt_chars、tool_schema_chars |
| macro snapshot | 无完整 resumable macro snapshot | 有 checkpoint 与 final skill store |
| function callable layer | 尚未完整加入 | 已有 BFCL callable function skill 展开层，但本 run skill 主要仍是 prompt-only cards |
| injector 记录 | 较粗 | executor cost event 记录 `skill_injector_mode=full` |

注意：最新 run 顶层 `skill_injector_mode` 没有单独字段，但 cost event metadata 显示 executor 侧实际为 `full`。这说明后续需要把 injector mode 显式写入顶层 config，避免复查时需要下钻 cost events。

## 3. 算法设计差异

### 3.1 维护链路

最新 run 已包含之前新增的结构化路径：

- credit assigner 生成 helpful/harmful/neutral 信号。
- micro 每 task 执行，但只对相关 targets 做维护。
- macro 每 10 个 train tasks 执行 overlap/refactor/filter/TRL feedback 相关步骤。
- skill credit filter 在 macro 后禁用强负向 skill。
- macro/resume checkpoint 记录训练进度和 store。

上次 run 已有部分 guardfix 与 micro/macro 结构，但缺少后续 cost accounting、callable function skill 支持和 macro snapshot 可追溯性。

### 3.2 Pending 与 active

最新 run final skill store：

- total skills：46
- active：4
- disabled：1
- archived：41

顶层 `pending_skill_summary.n_pending=41` 是 stale summary，不代表最终仍 pending。它记录的是 macro revoke/filter 前的 pending 名单；final store 里这些 skills 已经被 `archived`。这是一个记录口径问题，后续应该把字段改名为 `pending_before_filter_summary` 或在 final result 中补 `final_skill_status_summary`。

上次 run final skill store：

- total skills：43
- active：4
- disabled：1
- archived：38

两次都没有 pending promotion：

- 最新：pending_skill_promotions 0，pending_skill_revocations 41。
- 上次：pending_skill_promotions 0，pending_skill_revocations 38。

这说明当前 refactor/promotion gate 仍偏保守：能抽取很多 pending-like segments，但真正升 active 的少。最新 run 不是“pending 都没处理”，而是大部分被 revoke/archive。

### 3.3 Credit/filter 差异

最新 run：

- skill_credit_events：22
- harmful：11
- helpful：6
- neutral：5
- disabled：`engine_start_brake_pedal_prerequisite`
- kept：`buy_stock_at_current_market_price`
- kept：`tradingbot_direct_ticker_binding`

关键 filter decision：

- `engine_start_brake_pedal_prerequisite`：harmful_count 8，helpful_count 0，negative_margin 8，被 disabled。
- `tradingbot_direct_ticker_binding`：helpful_count 6，harmful_count 2，negative_margin -4，被 kept。
- `buy_stock_at_current_market_price`：harmful_count 1，helpful_count 0，negative_margin 1，低于 disable threshold，被 kept。

上次 run 也有 22 条 credit events，但当时 test 侧 timeout 导致 heldout 反馈不可靠。训练侧 filter 同样禁用了负向 skill，但 active skill 组成不同。

## 4. 指标差异

### Train 50

| metric | 上次 | 最新 | 变化 |
|---|---:|---:|---:|
| success/pass_at_k | 0.24 | 0.20 | -0.04 |
| official_valid_rate | 0.62 | 0.58 | -0.04 |
| avg_score | 0.8262 | 0.8090 | -0.0172 |
| avg_call_recall | 0.8794 | 0.8789 | -0.0005 |
| avg_call_precision | 0.7962 | 0.7697 | -0.0265 |
| avg_total_tokens | 62468.9 | 62339.2 | -129.7 |
| timeout_rate | 0.0 | 0.0 | 0 |

训练侧最新 run 略低，但差异不大。call recall 几乎相同，precision 小幅下降。

### Heldout Test 50

| metric | 上次 | 最新 | 变化 |
|---|---:|---:|---:|
| success/pass_at_k | 0.02 | 0.08 | +0.06 |
| official_valid_rate | 0.80 | 0.70 | -0.10 |
| avg_score | 0.3314 | 0.7892 | +0.4578 |
| avg_call_recall | 0.3718 | 0.8955 | +0.5237 |
| avg_call_precision | 0.3018 | 0.7193 | +0.4175 |
| avg_total_tokens | 41788.7 | 83043.9 | +41255.2 |
| timeout_rate | 0.60 | 0.0 | -0.60 |

结论：最新 heldout test 明显更可信。上次 heldout timeout_rate 为 0.60，导致 avg_score、recall、precision 和 token 口径都异常。最新 run 没有 timeout，strict success 与 avg_score 都提升。

official_valid_rate 下降不能单独解释为退步，因为上次 official_valid_rate 与 avg_score/call recall 严重不一致。对 BFCL，必须同时报告 strict success、official_valid、avg_score、call recall/precision。

## 5. Token/cost 差异

最新 run 可以拆分 input/output：

Train:

- avg_total_tokens：62339.2
- avg_input_tokens：61247.4
- avg_output_tokens：1091.8
- skill_prompt_chars：213233
- tool_schema_chars：8773871
- final_conversation_chars：1600211

Test:

- avg_total_tokens：83043.9
- avg_input_tokens：81956.2
- avg_output_tokens：1087.8
- skill_prompt_chars：710845
- tool_schema_chars：11717975
- final_conversation_chars：2182958

主要开销仍在 input 侧。BFCL adapter 每个 step 都带大量 tool schema；skill prompt 是额外开销，但不是唯一主因。最新 test token 比上次高，很大一部分来自上次 60% timeout/短路，而不是这次算法单独膨胀。

## 6. 输出 case 差异

### 明显提升 case

| task | 上次 score | 最新 score | 最新注入 skill | 说明 |
|---|---:|---:|---|---|
| `multi_turn_base_191` | 0.0 | 1.0 | `contact_customer_support_message_brevity_rule` | 从完全失败到 strict success |
| `multi_turn_base_192` | 0.0 | 0.9231 | `contact_customer_support_message_brevity_rule` | travel/support 类多轮任务明显恢复 |
| `multi_turn_base_141` | 0.0 | 0.9231 | `tradingbot_direct_ticker_binding`, `buy_stock_at_current_market_price` | watchlist/TradingBot 类任务恢复 |
| `multi_turn_base_77` | 0.0 | 0.9091 | `vehicle_brake_pedal_full_press_for_engine_start` | VehicleControl 类任务恢复 |
| `multi_turn_base_35` | 0.0 | 0.8750 | 无 skill 注入 | 说明部分提升来自 runtime/timeout 修复，而非 skill 本身 |

### 小幅 regression case

| task | 上次 score | 最新 score | 最新注入 skill | 说明 |
|---|---:|---:|---|---|
| `multi_turn_base_65` | 0.9000 | 0.7500 | `vehicle_brake_pedal_full_press_for_engine_start` | VehicleControl 任务局部退步 |
| `multi_turn_base_152` | 0.8333 | 0.7273 | `contact_customer_support_message_brevity_rule` | travel/support 任务局部退步 |
| `multi_turn_base_69` | 0.9474 | 0.8889 | `vehicle_brake_pedal_full_press_for_engine_start` | 小幅下降 |
| `multi_turn_base_131` | 0.9333 | 0.8750 | `buy_stock_at_current_market_price`, `tradingbot_direct_ticker_binding` | TradingBot 小幅下降 |

这些 regression 都没有 strict success 变化，多为 partial score 波动。需要后续逐条查看 tool call diff，判断是 skill pollution 还是随机解码/执行路径差异。

### Strict success 改变

最新从失败变成功：

- `multi_turn_base_191`：0.0 -> 1.0。
- `multi_turn_base_76`：0.8889 -> 1.0。
- `multi_turn_base_86`：0.9412 -> 1.0。

没有观察到 strict success 从成功变失败的 case。

## 7. Skill set 差异

上次 final skills：

- total 43
- active 4
- disabled 1
- archived 38
- kind：workflow_guardrail_card 21，interface_contract_card 14，atomic_tool_rule_card 8。

最新 final skills：

- total 46
- active 4
- disabled 1
- archived 41
- kind：workflow_guardrail_card 21，interface_contract_card 20，atomic_tool_rule_card 5。

最新 active/injected skill 更偏 interface contract：

- `tradingbot_direct_ticker_binding`
- `buy_stock_at_current_market_price`
- `vehicle_brake_pedal_full_press_for_engine_start`
- `contact_customer_support_message_brevity_rule`

上次 test 注入里还出现：

- `multi_turn_order_verification_workflow`
- `vehicle_engine_start_brake_pedal_prerequisite`
- `vehicle_engine_start_brake_pedal_requirement`

这解释了部分 case 差异：最新 run 的 active set 更少保留 order verification 类 workflow，而更多集中在 TradingBot、VehicleControl、support message brevity。

## 8. 当前判断

这次不是简单“算法全面更强”。更准确的判断是：

1. 最新 run 修复了上次 heldout test timeout/短路导致的异常，因此 test avg_score、recall、precision 回到可信区间。
2. skill 对部分 heldout case 有正向帮助，特别是 TradingBot direct ticker、support brevity、VehicleControl brake prerequisite 类。
3. active skill 数仍少，大量 pending 被 archive；promotion/refactor gate 仍偏保守。
4. token 开销主要在 input，尤其 BFCL tool schema 和 full skill prompt；后续应重点压缩 executor prompt、显式记录 injector mode、评估 compact/callable skill。
5. strict success 仍低，说明 partial-call quality 改善尚未完全转化为 exact success。后续需要把 schema/argument exactness 作为 refiner 和 bundle gate 的核心目标。

## 9. 后续应改的记录字段

- 顶层写入 `skill_injector_mode`，不要只存在 cost event metadata。
- 结果顶层补 `final_skill_status_summary`，避免 `pending_skill_summary` 被误读。
- 每个 test detail 记录 `prompt_injected_skills`、`retrieved_skills` 的 top-level summary，避免每次都下钻 `runs[0].trace`。
- 每个 macro window 记录 active skill 列表和 revoked/preserved reasons。
- 对每个 strict success 改变的 task 自动生成 tool-call diff summary。
