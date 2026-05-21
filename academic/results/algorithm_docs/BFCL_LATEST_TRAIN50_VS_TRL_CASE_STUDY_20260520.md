# BFCL latest train50 vs TRL case study

Date: 2026-05-20

This document compares the earlier strong BFCL run `bfcl_train50_20260518_202840` against the two later TRL runs:

- latest train50: `academic/results/bfcl_train50_20260518_202840.json`
- original TRL 50/50: `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`
- injector-gate TRL 50/50: `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json`
- SkillX aligned reference: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_result_with_elapsed.json`

All three of our runs use the same fixed BFCL 50/50 manifest:
`academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`.
The train and test task ids are identical, so the latest-train50 advantage is not a data split artifact.

## 1. High-level metrics

| run | phase | strict | official_valid | avg_score | recall | precision | avg_tokens | avg_elapsed_s |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| latest train50 | train | 0.20 | 0.58 | 0.8090 | 0.8789 | 0.7697 | 62339.2 | 34.471 |
| latest train50 | test | 0.08 | 0.70 | 0.7892 | 0.8955 | 0.7193 | 83043.9 | 36.932 |
| original TRL | train | 0.24 | 0.70 | 0.8324 | 0.8979 | 0.7939 | 61572.7 | 65.506 |
| original TRL | test | 0.08 | 0.52 | 0.7474 | 0.8491 | 0.6850 | 76689.5 | 79.253 |
| injector-gate TRL | train | 0.22 | 0.6531 | 0.8183 | 0.8744 | 0.7874 | 59604.8 | 46.364 |
| injector-gate TRL | test | 0.08 | 0.60 | 0.7530 | 0.8371 | 0.7096 | 74175.7 | 49.427 |
| SkillX aligned | test | 0.08 | 0.54 | 0.7679 | 0.8508 | 0.7140 | 78787.6 | 34.927 |

Main observation:

- The earlier latest train50 run is the best among these on heldout official_valid (`0.70`), avg_score (`0.7892`), and recall (`0.8955`).
- It beats SkillX aligned on official_valid (`0.70` vs `0.54`) and avg_score (`0.7892` vs `0.7679`).
- The two later TRL runs do improve over the no-skill baseline rerun, but they are regressions relative to latest train50.

Therefore the method did work in the latest train50 setting. The current problem is not "skills never help"; it is that the later TRL/candidate competition path changed the skill population and maintenance/injection dynamics in a way that reduced heldout behavior.

## 2. Setting differences

Settings held constant:

| item | latest train50 | original TRL | injector-gate TRL |
|---|---|---|---|
| manifest | `curated_related_manifest_50_50.json` | same | same |
| model | `claude-sonnet-4-5` | same | same |
| rounds / epochs | 1 / 1 | same | same |
| micro / macro step | 1 / 10 | same | same |
| train / micro / test concurrency | 4 / 4 / 4 | same | same |
| top_k_skills | 2 | same | same |
| skill_injection_mode | `prompt_only` | same | same |
| max_steps_per_turn | 20 | same | same |
| max_task_seconds | 180 | same | same |

Material differences:

| item | latest train50 | original TRL | injector-gate TRL |
|---|---:|---:|---:|
| `candidate_competition_enabled` | false | true | true |
| `candidate_sample_count` | 1 | 3 | 3 |
| final skill artifacts | 46 | 165 | 180 |
| final active skills | 4 | 14 | 16 |
| final trial skills | 0 | 120 | 136 |
| final archived skills | 41 | 26 | 23 |
| final disabled skills | 1 | 4 | 4 |
| train retrieved skill events | 22 | 124 | 139 |
| train injected skill events | 22 | 55 | 65 |
| test retrieved skill events | 52 | 227 | 229 |
| test injected skill events | 52 | 74 | 73 |
| test injected skill names | 4 | 31 | 36 |

Interpretation:

- Latest train50 used a much smaller and more selective skill library. Heldout test saw only four skill names repeatedly:
  `vehicle_brake_pedal_full_press_for_engine_start`, `buy_stock_at_current_market_price`, `tradingbot_direct_ticker_binding`, and `contact_customer_support_message_brevity_rule`.
- TRL changed the regime from "few promoted/active skills" to "many candidate variants and trial skills".
- The heldout executor now sees a more diverse and noisier set of cards. This is not just more coverage; it also increases prompt branching and false-positive skill guidance.
- Candidate competition also triples extraction attempts (`candidate_sample_count=3`), which expands the store and the downstream injector/refiner workload.

```
comment:
我其实关心代码有无区别， 中间经历过代码重构嘛？ 算法， prompt都完全保持一致嘛？
```

回复：

不是完全一致。上面的表只说明了 manifest、模型、top-k、max step、concurrency 等外层运行参数一致；它不能证明代码、算法路径、prompt 完全一致。更准确的说法应该是：`latest train50` 和两次 TRL run 使用相同 fixed split 和大部分 runner 参数，但中间确实经历了代码重构和算法/prompt 变化，因此不能把结果差异解释成纯粹的 `candidate_competition_enabled` 单变量 ablation。

具体差异如下：

1. 代码层面经历过重构。`latest train50` 是 2026-05-18 晚上的 run；之后 git 里有一串维护/credit/core 重构，包括：
   - `6e8f9be Share credit exposure scope across benchmarks`
   - `9532215 Add benchmark-agnostic maintenance core skeleton`
   - `dc3262e Refactor spreadsheet maintenance facade onto common core`
   - `37df7a6 Share BFCL maintenance primitives with common core`
   - `0358555 Add cross-benchmark refactor contract tests`
   这些不是纯文档改动，说明后续 BFCL/通用维护路径的代码形态已经变过。

2. 算法路径不完全一致。`latest train50` 的配置是 `candidate_competition_enabled=false`、`candidate_sample_count=1`，后两次 TRL 是 `candidate_competition_enabled=true`、`candidate_sample_count=3`。这会改变 extractor 产物数量、candidate group feedback、role feedback 生成方式、后续 bundle/refine/injector 工作量。

3. injector 路径不一致。`latest train50` 的 top-level `token_breakdown` 里没有 `skill_injector` 角色；两次 TRL 分别有 `1197` 和 `459` 个 `skill_injector` LLM calls。也就是说 later TRL 不只是“多了候选”，还多了一个实际执行的 LLM-based injection/selection 子系统。

4. prompt/role feedback 也不一致。三次 run 的 extractor role feedback rules 不同。`latest train50` 的规则强调“只抽 generalize across multiple task types”“避免 message brevity/priority calibration”，而 later TRL 的规则由 candidate-group feedback 逐个 macro 更新，出现了“只抽一个候选”“error-recovery triggers”“observable runtime triggers”等不同约束。这些规则会直接拼到 extractor prompt，影响后续抽取。

所以这里需要改写结论：`latest train50` 可以证明方法在当时那套代码+prompt+selective skill setting 下 work；但现在两次 TRL underperform 是“代码重构 + candidate competition + LLM injector + role feedback prompt 变化”共同后的结果。要严格排除原因，需要做 ablation：在当前代码上复刻 `candidate_competition=false/sample_count=1/no LLM injector`，再逐项打开 competition 和 LLM injector。

## 3. Maintenance and injector cost

Top-level `token_breakdown` measures maintenance LLM calls, not executor rollout calls. This is where the training-time blow-up appears.

| run | maintenance calls | maintenance tokens | maintenance duration | skill_injector calls | injector tokens | injector token share | injector duration | injector duration share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| latest train50 | 103 | 724266 | 1538.3s | 0 | 0 | 0.0% | 0.0s | 0.0% |
| original TRL | 1470 | 3307835 | 10014.8s | 1197 | 1338717 | 40.5% | 5688.0s | 56.8% |
| injector-gate TRL | 747 | 2563078 | 6639.1s | 459 | 508855 | 19.9% | 2046.2s | 30.8% |

Role breakdown:

| run | extractor calls/tokens/duration | skill_injector calls/tokens/duration | refiner calls/tokens/duration | credit calls/tokens/duration |
|---|---|---|---|---|
| latest train50 | 50 / 391291 / 668.4s | 0 / 0 / 0.0s | 23 / 141841 / 200.3s | 19 / 105090 / 419.3s |
| original TRL | 150 / 1197657 / 2306.6s | 1197 / 1338717 / 5688.0s | 48 / 277214 / 511.1s | 28 / 181429 / 622.6s |
| injector-gate TRL | 150 / 1199751 / 2431.5s | 459 / 508855 / 2046.2s | 76 / 419450 / 749.4s | 31 / 204073 / 734.9s |

Answer to the injector-cost question:

- Yes, the injector is a major source of the training-time increase.
- In original TRL it is the largest time component: `56.8%` of maintenance duration and `40.5%` of maintenance tokens.
- Injector-gate reduces injector calls from `1197` to `459`, but injector still accounts for `30.8%` of maintenance duration.
- It is not the only cause. Candidate competition also triples extractor calls from `50` to `150`, and extractor itself is `36.2%` of original TRL tokens and `46.8%` of injector-gate TRL tokens.
- The training-time explosion is therefore: `candidate_sample_count=3` creates many more candidates; the injector then evaluates many candidate/turn combinations; refiner/credit load also rises because more skills survive long enough to need maintenance.

Important accounting caveat:

- In `train_summary` / `test_summary`, role `injector` often has `input_tokens=0` because those summaries record skill prompt char bookkeeping for executor injection, not the maintenance-time injector LLM prompt tokens.
- The correct place to measure training-time injector cost is the top-level `token_breakdown.by_role.skill_injector`.

## 4. Why latest train50 worked better

The earlier run had a smaller skill set and injected a few high-impact, repeatedly useful rules.

Latest train50 heldout test injection counts:

| skill | injected count |
|---|---:|
| `vehicle_brake_pedal_full_press_for_engine_start` | 14 |
| `buy_stock_at_current_market_price` | 13 |
| `tradingbot_direct_ticker_binding` | 13 |
| `contact_customer_support_message_brevity_rule` | 12 |

This has two properties that later TRL lost:

1. The prompt was stable. The same few skills appeared repeatedly, so the model saw a compact and predictable rulebook.
2. The useful skills mapped to broad high-frequency failure modes: vehicle engine preconditions and TradingBot direct ticker/current-price behavior.

The later TRL runs generated many more candidate variants. Some were useful, but many were local, overlapping, or trial-quality. The injector had to decide among dozens of variants, and it sometimes selected brittle rules that were locally plausible but harmful in the current heldout context.

```
comment:
你的意思是说更多的candidates质量参差不齐， 这说不通， 数量多不会导致质量下降。 这些你说质量差的skill， credit怎么样？有真正的negative嘛？还是说你的意思是他们根本没被inject和使用？
这里应该有两个主要的变化：injector变成llm based, candidate competition. 我觉得需要各自排除一下？
```

回复：

你说得对，“数量多导致质量下降”这个表述不严谨。数量本身不会让单个 skill 质量下降；更准确的问题是：更多候选增加了未充分验证候选被检索/注入的机会，也增加了 LLM injector 在相近候选之间做错误选择或过度过滤的机会。这里应该拆成两个因素：`candidate competition` 和 `LLM-based injector`，不能混在一起说。

关于这些 skill 的 credit，有真实 negative，不只是“没被使用”：

| run | credited skills | total helpful | total harmful | total neutral | disabled credited skills |
|---|---:|---:|---:|---:|---:|
| original TRL | 19 | 24 | 23 | 8 | 4 |
| injector-gate TRL | 23 | 30 | 27 | 8 | 4 |

典型 negative skill：

- original TRL: `contact_customer_support_concise_message_contract__candidate_r0_t39_s1`，retrieved/injected `5/5`，helpful `0`，harmful `3`，neutral `2`，最终 disabled。
- original TRL: `vehicle_fuel_check_unnecessary_before_fill_with_explicit_amount__candidate_r0_t19_s0`，retrieved/injected `2/2`，helpful `0`，harmful `2`，最终 disabled。
- original TRL: `navigate_to_tire_shop_when_pressure_low__candidate_r0_t18_s0`，retrieved/injected `4/4`，helpful `1`，harmful `2`，neutral `1`，最终 archived。
- injector-gate TRL: `contact_customer_support_concise_message_contract__candidate_r0_t39_s1`，retrieved/injected `4/4`，helpful `0`，harmful `4`，最终 disabled。
- injector-gate TRL: `skip_airport_lookup_when_destination_explicit_iata__candidate_r0_t39_s2`，retrieved/injected `5/5`，helpful `1`，harmful `4`，最终 disabled。
- injector-gate TRL: `vehicle_tire_service_navigation_workflow__candidate_r0_t17_s2`，retrieved/injected `5/5`，helpful `2`，harmful `3`，最终 archived。

也有另一类问题：很多候选确实只是 retrieved 但没有 injected/use。例如 original TRL role feedback 多次指出 `order_id reuse`、`verify_balance_before_withdrawal` 这类 state-reuse/generic guardrail “retrieved but never injected”。所以两种问题都存在：

1. 一部分 skill 被 inject 后有真实 harmful credit。
2. 一部分 skill 根本没有进入有效使用链路，只增加检索/候选竞争/维护开销。

你指出的两个主要变化也正确：

- `candidate competition` 的影响：把候选规模从 46 个扩大到 165/180 个，test retrieved event 从 52 变成 227/229，test injected skill names 从 4 变成 31/36。
- `LLM-based injector` 的影响：original TRL 里 `skill_injector` 有 1197 calls，占维护时长 56.8%；而且它会过滤掉一些有用 skill，例如 `multi_turn_base_54` 原始 TRL retrieved 了 vehicle skills 但 injected 为空。

需要补做的 ablation：

1. 当前代码 + `candidate_competition=false/sample_count=1` + 关闭 LLM injector，复刻 latest train50 风格。
2. 当前代码 + `candidate_competition=true/sample_count=3` + 关闭 LLM injector，看候选竞争本身是否退化。
3. 当前代码 + `candidate_competition=false/sample_count=1` + 打开 LLM injector，看 injector 本身是否退化。
4. 当前代码 + 两者都打开，即现在 TRL。

只有这样才能把 candidate competition 和 LLM injector 的贡献分开。

## 5. Case-level regressions from latest train50 to original TRL

Largest latest-over-original-TRL regressions:

| task | latest score / valid | original TRL score / valid | delta | main symptom |
|---|---:|---:|---:|---|
| `multi_turn_base_54` | 0.9333 / true | 0.3077 / false | -0.6256 | injector rejected/failed to inject relevant engine/brake skills; model shifted required turn-0 actions into later turn and missed Twitter actions |
| `multi_turn_base_76` | 1.0000 / true | 0.5455 / false | -0.4545 | later TRL loses multi-intent sequencing |
| `multi_turn_base_131` | 0.8750 / true | 0.6250 / false | -0.2500 | order/TradingBot state chain regression |
| `multi_turn_base_88` | 0.8750 / true | 0.6316 / false | -0.2434 | vehicle fuel/engine sequence degraded |
| `multi_turn_base_103` | 0.8571 / true | 0.6154 / false | -0.2417 | file/state sequence degraded |
| `multi_turn_base_52` | 0.7692 / true | 0.5333 / false | -0.2359 | vehicle service workflow degraded |
| `multi_turn_base_68` | 0.7619 / true | 0.5714 / false | -0.1905 | vehicle/tire/navigation chain degraded |

Representative case: `multi_turn_base_54`

Expected:

- Turn 0: convert liters, lock all doors, press brake, start engine.
- Turn 1: check tire pressure and post tweet.
- Turn 2: retweet.

Latest train50:

- Injected `vehicle_brake_pedal_full_press_for_engine_start`.
- Actual calls included `liter_to_gallon`, `pressBrakePedal(1.0)`, `lockDoors`, `startEngine`, `check_tire_pressure`, `post_tweet`, `retweet`.
- Score `0.9333`, official valid true.

Original TRL:

- Retrieved several vehicle skills but injected none.
- Actual calls only did `liter_to_gallon`, then delayed lock/start/brake into turn 1 with wrong `pressBrakePedal(0.5)`, and missed `check_tire_pressure`, `post_tweet`, and `retweet`.
- Score `0.3077`, official invalid.

Interpretation:

- Here the original TRL path did not fail because it injected a bad skill; it failed because the injector rejected the useful skill in a multi-intent case.
- This is one concrete way TRL becomes negative: more candidate/injector machinery can filter out the simple useful rule that latest train50 exposed directly.

```
comment:
为什么？是injector的prompt有问题，导致过滤掉了真正有用的skill？过滤机制太严格？
整体来看， 你觉得injector发挥积极作用的时候多， 还是消极的时候多？
```

回复：

从已有证据看，`multi_turn_base_54` 这类 case 更像是 injector 过滤机制太严格，尤其是不擅长“一个 skill 只覆盖当前 user turn 的一个子任务”的 BFCL 多意图场景。它 retrieved 了相关 vehicle skills，但最后 injected 为空，导致 executor 没看到最关键的 lock/brake/start 规则。这个不是 skill 本身质量差，而是 injector 没把它放进 prompt。

可能的 prompt/机制问题：

1. injector 可能要求 skill 和整个 user turn 高度相关，而不是允许 skill 只匹配一个明确 subtask。BFCL 里一个 turn 经常同时包含 vehicle、twitter、file 等多个动作，这会误杀局部但必要的 skill。
2. injector 对“precondition skill”过于保守。它可能把 brake/lock/start 看成额外操作风险，但在 `54` 里这些正是 expected calls。
3. candidate competition 后相似 vehicle skills 很多，injector 面对多个近似候选时可能全部拒绝，避免冲突，但这会退化成 no-skill baseline 或更差。

整体看，injector 不是单向消极，它在 injector-gate run 相比 original TRL 是净正的：

- original TRL -> injector-gate，official valid 从 `0.52` 到 `0.60`。
- avg_score 从 `0.7474` 到 `0.7530`。
- avg_elapsed 从 `79.253s` 降到 `49.427s`。
- case-level official valid flips：injector-gate 相比 original TRL 有 `9` 个 case 从非 valid 变 valid，`5` 个 case 从 valid 变非 valid。

但相对 `latest train50`，injector-gate 仍然是负的：

- latest test official valid `0.70`，injector-gate `0.60`。
- latest avg_score `0.7892`，injector-gate `0.7530`。
- latest 更少 skill、更少 injector 机制，反而更稳。

所以我的判断是：

- 如果比较 original TRL，injector-gate 有净积极作用，说明“过滤一部分候选”是有价值的。
- 如果比较 latest train50，当前 LLM injector 仍然不够好，尤其在多意图 turn 上会错杀有用局部 skill，同时在一些 case 上仍会放入有害局部 rule。
- 下一步应该不是完全去掉 injector，而是把 injector 改成“允许 explicit subtask match + 强约束 trial skill + 缓存/批量化”，并用 ablation 确认它相对 deterministic/simple injector 是否真的增益。

## 6. Case-level regressions from latest train50 to injector-gate TRL

Largest latest-over-injector-gate regressions:

| task | latest score / valid | injector-gate score / valid | delta | main symptom |
|---|---:|---:|---:|---|
| `multi_turn_base_86` | 1.0000 / true | 0.6667 / false | -0.3333 | vehicle workflow regression |
| `multi_turn_base_194` | 0.7692 / false | 0.4444 / false | -0.3248 | Travel chain stops after cost check |
| `multi_turn_base_153` | 0.7273 / false | 0.4444 / false | -0.2829 | wrong airport-code state and missing booking/invoice |
| `multi_turn_base_75` | 0.5333 / false | 0.2857 / false | -0.2476 | over-applied engine/brake skill plus missed social actions |
| `multi_turn_base_131` | 0.8750 / true | 0.6667 / false | -0.2083 | TradingBot order chain regression |
| `multi_turn_base_78` | 0.8571 / true | 0.6667 / false | -0.1904 | harmful tire-pressure navigation rule |
| `multi_turn_base_152` | 0.7273 / false | 0.5455 / false | -0.1818 | missing cancellation and wrong ticket priority |

Representative case: `multi_turn_base_153`

Expected:

- Verify traveler details.
- Resolve Rivermist airport to `RMS` and call `get_flight_cost(travel_from='RMS', travel_to='GFD', ...)`.
- Set budget.
- Book flight.
- Retrieve invoice with booking id `3426812`.

Latest train50:

- Injected unrelated `contact_customer_support_message_brevity_rule`.
- Despite a prompt contamination bug where retrieved rule text entered `last_name`, the trajectory still made `get_flight_cost(RMS, GFD)`, `set_budget_limit`, `book_flight`, and `retrieve_invoice`.
- Score `0.7273`.

Original TRL:

- Injected Travel rules such as `skip_airport_lookup_when_city_to_code_mapping_known`, `travel_booking_skip_balance_check_when_card_id_provided`, and invoice rules.
- Correctly executed the whole chain and reached score `0.9091`, official valid true.

Injector-gate TRL:

- Injected `travel_booking_requires_all_parameters_from_context` and invoice omission rules.
- Actual calls stopped after `verify_traveler_information`, `get_nearest_airport_by_city`, incorrect `get_flight_cost(travel_from='Rivermist')`, and `set_budget_limit`.
- Missing `book_flight` and `retrieve_invoice`.
- Score `0.4444`.

Interpretation:

- The gating/run variation did not simply remove bad skills; it changed which Travel skill family entered the prompt.
- The injected gate skill was a local precondition checklist, not a transaction-chain plan. It did not preserve resolved airport code state or encourage continuing to book and invoice.
- This explains why SkillX's broad `flight book with fallback airports` and the original TRL's broader Travel skills do better on this case than injector-gate.

Representative case: `multi_turn_base_194`

Expected:

- `list_all_airports`, cost from first to last airport, `book_flight`, `purchase_insurance`, `retrieve_invoice`, `contact_customer_support`, `cancel_booking`.

Latest train50:

- Executed through booking, insurance, invoice, and cancellation.
- Missed support and included optional `insurance_id`.
- Score `0.7692`.

Injector-gate TRL:

- Injected `travel_booking_requires_all_parameters_after_cost_check`, `contact_customer_support_message_fidelity`, and `travel_booking_reuses_prior_booking_id_for_cancellation`.
- Actual calls stopped after `list_all_airports` and `get_flight_cost`.
- Missing booking, insurance, invoice, support, and cancellation.
- Score `0.4444`.

Interpretation:

- The gate-selected Travel cards are local and do not form a durable transaction plan.
- A "requires all parameters after cost check" card can make the model hesitate if the current user turn does not restate all parameters, even though they are available from prior state.
- Latest train50 and SkillX both behave more like a plan-following executor here.

Representative case: `multi_turn_base_78`

Expected:

- `check_tire_pressure`, then `find_nearest_tire_shop`, then `post_tweet`.

Latest train50:

- Score `0.8571`, official valid true.

Injector-gate TRL:

- Injected `vehicle_tire_pressure_navigation_workflow` and `vehicle_check_tire_pressure_healthy_flag_stops_navigation`.
- Actual calls omitted `find_nearest_tire_shop` and added an extra Twitter login-status call.
- Score `0.6667`, official invalid.

Interpretation:

- The injected healthy-flag rule is over-specific to a different task family. This task's expected behavior uses the explicit user threshold, not the `healthy_tire_pressure` boolean.
- This is a direct harmful-skill example: a plausible guardrail suppresses an expected tool call.

Representative case: `multi_turn_base_75`

Expected:

- Refuel, check tire pressure, post tire-pressure tweet, retweet, comment preserving the user's typo `pressue`.

Latest train50:

- Injected `vehicle_brake_pedal_full_press_for_engine_start`.
- Added extra engine/brake/lock/start calls, but still performed `retweet` and preserved comment typo.
- Score `0.5333`.

Injector-gate TRL:

- Injected `press_brake_before_starting_engine` and fuel rule.
- Added the same extra engine/brake/lock/start behavior, missed `retweet`, and corrected `pressue` to `pressure`.
- Score `0.2857`.

Interpretation:

- Engine-start guardrails can be helpful in tasks where engine start is explicitly part of the expected call set, but harmful when official expected only cares about fuel/social actions or when it changes attention away from social subtasks.
- This is a prompt-load/over-application failure.

## 7. Cases where later TRL improved

The later TRL is not uniformly worse. Examples:

| task | latest | original TRL | injector-gate TRL | reason |
|---|---:|---:|---:|---|
| `multi_turn_base_29` | 0.7500 / invalid | 1.0000 / valid | 1.0000 / valid | no skill involved; likely executor variation or prompt formatting improvement |
| `multi_turn_base_153` | 0.7273 / invalid | 0.9091 / valid | 0.4444 / invalid | original TRL Travel skills helped, gate-selected Travel skills regressed |
| `multi_turn_base_65` | 0.7500 / invalid | 0.6667 / invalid | 0.9000 / valid | gate injected brake-before-start and preserved Twitter actions |
| `multi_turn_base_160` | 0.8889 / valid | 1.0000 / valid | 1.0000 / valid | later runs improved one Travel case |

This matters because it shows the TRL idea can work, but the current gating/competition logic is unstable. It can find helpful candidates in some cases and harmful/over-narrow candidates in others.

## 8. Why TRL is currently acting like a side effect

The current TRL path has three separate side-effect channels:

1. Candidate explosion:
   - `candidate_sample_count=3` turns each extraction opportunity into three variants.
   - Final artifacts grow from `46` to `165/180`.
   - Heldout retrieved skill events grow from `52` to `227/229`.
   - Heldout injected skill names grow from `4` to `31/36`.
   - More candidates increase both prompt noise and the probability that a near-match rule is selected.

2. Injector over-filtering:
   - In `multi_turn_base_54`, original TRL retrieved relevant vehicle skills but injected none, losing the simple engine/brake guidance that latest train50 used.
   - This is especially dangerous for BFCL multi-intent turns, where a skill may apply to one subtask even if it does not explain the whole user turn.

3. Local-rule over-application:
   - In `multi_turn_base_78`, healthy-tire-pressure guidance suppresses `find_nearest_tire_shop`.
   - In `multi_turn_base_75`, engine-start/brake rules add extra official-invalid calls and distract from social subtasks.
   - In `multi_turn_base_153` / `194`, local Travel precondition cards fail to preserve the broader transaction chain.

A fourth issue is quality-control lag:

- Trial skills dominate the final libraries (`120` and `136` trial skills).
- Many trial skills are unproven but retrievable/injectable enough to affect heldout behavior.
- Credit/refiner eventually identifies some harmful patterns, but after the candidate set has already made the executor prompt noisier and the training loop slower.

```
comment:
我才发现你完全没有分析TRL算法本身啊，找到了什么meta-skill？那些meta-skill效果好吗？
```

回复：

这个批评是对的。前面的文档主要分析了普通 skill/executor 行为，确实没有单独分析 TRL 算法本身学到的 meta-skill，也就是 `role_feedback.extractor.rules` 这条链路。补充如下。

这里的 TRL 不是直接学一个可执行 skill，而是把运行反馈转成 extractor 的“规则书”。这些规则会进入后续 extractor prompt，影响它下一批抽什么 skill、怎么限定 scope、是否避免重复候选。它的产物是 meta-skill / role skill，主要在 `role_feedback.extractor.rules`。

### latest train50 的 meta-skill

`latest train50` 只形成了一轮 extractor rulebook，核心规则是：

1. 只在 pattern 能跨任务/工具组合泛化时抽 skill，不抽单任务参数 quirks。
2. 避免抽 message brevity、priority calibration、parameter omission，除非系统性影响 validity。
3. 优先抽工具交互序列或条件逻辑，而不是单参数修正。
4. unit/参数名冲突只有在多任务一致出现且不是 schema 已说明时才抽。
5. 不抽零 retrieval/zero injection 的 skill，除非它解决清晰重复失败。

效果判断：这组 meta-skill 是有帮助的。它虽然不完美，因为 final store 里仍然有 `contact_customer_support_message_brevity_rule` 这种后来看起来污染 prompt 的 skill，但整体上它把 final active set 压得很小，heldout 只注入 4 个 skill names，结果 official_valid `0.70`。所以 latest 的 meta-skill 方向是“保守、少抽、强调泛化和证据”，和好结果一致。

### original TRL 的 meta-skill

original TRL 的 extractor feedback 有 5 次更新。它学到的规则包括：

1. 不要对同一 workflow pattern 抽多个重复候选。
2. workflow guardrail 要写成 error-recovery trigger，不要 proactive check。
3. state-reuse skill 要说明错误条件、source call 和 dependent call。
4. 不抽 base agent 已经能处理的 efficiency shortcut。
5. message formatting 类问题要合并成一个 generalized contract，不要每个 task 抽一个。

效果判断：方向上合理，但落地效果一般，甚至有副作用。

证据：

- 它确实识别到了重复候选、retrieved-but-not-injected、proactive check harmful 等问题。
- 但这些 meta rules 没有阻止最终 store 膨胀到 165 个 artifacts，其中 120 个 trial。
- credit summary 里 harmful 和 helpful 几乎打平：helpful `24`，harmful `23`，neutral `8`。
- 一些明确 harmful 的 skill 被 disabled，但它们已经在训练/评估链路里造成了注入或维护成本。
- 它倾向把 workflow guardrail 改成 error-recovery，这在某些 BFCL expected-call 场景反而错，因为官方 expected 有时就是要求 proactive precondition call，例如 lock/brake/start。

所以 original TRL 的 meta-skill 是“认识到问题，但规则太抽象/太晚生效/有时方向和 BFCL expected calls 冲突”。

### injector-gate TRL 的 meta-skill

injector-gate 的 extractor rules 更激进地偏向：

1. 抽 explicit precondition sequences，例如 `lockDoors -> startEngine`、`pressBrakePedal -> startEngine`。
2. 避免 result-reuse patterns，例如 `reuse order_id`，因为它们 retrieve 但很少 inject。
3. trigger condition 必须来自 exact tool output fields，例如 `healthy_tire_pressure`。
4. 不抽 message formatting / wording preservation / communication style。

效果判断：这组 meta-skill 局部有效，但有明显偏置。

正面证据：

- injector-gate 相比 original TRL valid 从 `0.52` 提升到 `0.60`。
- `multi_turn_base_54`、`65`、`76`、`88` 等 vehicle/precondition 类 case 明显改善。
- original TRL 的 injector calls 从 1197 降到 459，训练耗时也明显下降。

负面证据：

- 它过度偏好 vehicle precondition sequence，导致 Travel transaction-chain skill 变弱。
- 它把 trigger condition 绑定到 exact tool output field，这对某些 tire/navigation case 不适用，因为 official expected 使用用户给的阈值，不一定用 `healthy_tire_pressure`。
- 它排斥 result-reuse patterns，但 BFCL Travel/Trading 很多成功链路恰恰依赖 `booking_id` / `order_id` 的跨 turn 复用。
- 它仍然有 harmful credit：helpful `30`，harmful `27`，neutral `8`，并非明显净正。

### 总结

TRL 算法本身确实学到了 meta-skill，而且不是空的：

- latest 学到“少抽、泛化、证据优先”。
- original TRL 学到“去重、error-recovery、避免已会的 shortcut”。
- injector-gate 学到“偏好可观测 precondition sequence、禁 message formatting、少信 result-reuse”。

但当前 meta-skill 的效果不够好，原因是它们优化的是 extractor 规则，而不是直接优化 heldout executor utility。它们会根据中间 credit 形成一些看似合理的抽取偏好，但这些偏好和 BFCL official expected calls 不总是一致：

- “error-recovery only” 会错过官方需要 proactive precondition call 的任务。
- “exact tool output field trigger” 会错过用户阈值驱动的任务。
- “avoid result-reuse” 会伤害 Travel/Trading 的 booking_id/order_id 链条。

所以 TRL 现在不是完全没学到东西，而是学到的 meta-skill 过度受局部 credit 和 injector 可观测性影响，缺少 domain/task-family 层面的约束。下一步应该把 meta-skill 分 domain：Vehicle 可以偏 precondition sequence，Travel/Trading 必须保留 transaction/state-reuse chain，不能用同一套 extractor meta-rule 统治所有 BFCL domains。

## 9. What to tune next

The goal should be to recover latest train50's selectivity while keeping TRL's ability to improve candidate quality.

Priority changes:

1. Separate candidate competition from heldout exposure.
   - Competition can generate multiple candidates, but heldout retrieval should only see a compact winner set or skills that passed stronger evidence thresholds.
   - Avoid letting all trial variants compete for prompt slots in heldout.

2. Reduce injector calls and cache decisions.
   - The original TRL `skill_injector` made `1197` calls.
   - Batch candidate decisions per task/turn or cache `(query, skill_name, trust_state)` decisions.
   - Use deterministic prefilters before LLM injection.

3. Restore a small active-skill profile for high-frequency patterns.
   - Latest train50 succeeded with only four heldout skill names.
   - Current TRL should prefer a compact promoted set plus a very small exact-match trial supplement.

4. Add task-family-specific broad skills for Travel.
   - SkillX and latest train50 behave better on Travel chains when the prompt carries a broad transaction plan.
   - The current local Travel cards are not enough. A BFCL TravelAPI transaction-chain skill should cover:
     `airport/code resolution -> cost -> book_flight -> use booking_id for insurance/invoice/support/cancel`.

5. Tighten harmful guardrails.
   - Vehicle tire navigation rules must respect explicit user thresholds and should not blindly use `healthy_tire_pressure`.
   - Engine/brake rules need an official-action guard: do not add precondition calls unless the current turn explicitly requires `startEngine` or the expected operation cannot proceed without it.

## 10. Bottom line

The early latest train50 result is valid evidence that the method can work under the fixed 50/50 BFCL setting:

- Same manifest and same test ids.
- Heldout official_valid `0.70`, avg_score `0.7892`.
- Better than SkillX aligned on both official_valid and avg_score.

The later TRL runs underperform because they changed the operating regime:

- `candidate_sample_count=3` and candidate competition created a much larger candidate library.
- The injector became both a major runtime bottleneck and a behavioral bottleneck.
- Trial/local skills were exposed too broadly, causing either missed useful skill injection or harmful over-application.

The next TRL fix should not be another blind full 50/50. It should first restore the compact/selective behavior of latest train50 while preserving candidate competition internally, then validate on the regression cases listed above.

## 11. 2026-05-20 alignment implementation update

User conclusion: before tuning TRL, first align the current framework to the old latest-train50 algorithm/prompt path, then run no-TRL ablations with injector and candidate competition. Do not fall back to running the old git version except as read-only evidence.

Read-only historical comparison:

- latest train50 result timestamp: `2026-05-18 20:28-21:07`.
- closest historical commit used for code comparison: `c4cd7d2 Add resumable macro snapshots for training runs`.
- historical worktree used only for inspection: `/home/lixujun/skill_evolving_c4cd7d2`.
- implementation remains in current workspace.

Main differences found:

1. Executor skill injector gate changed.
   - Old path: `skill_injector_mode=full` meant deterministic full prompt injection. No LLM injector role was called for skill selection; prompt rendering used `artifact_store.build_prompt(...)`.
   - Current path before this update: retrieved prompt skills were passed through `select_skill_context_with_llm(...)` even when presentation mode was `full`. This means `prompt_only/full` behavior was no longer the old algorithm.

2. Dynamic retrieval changed.
   - Old path: after step 0, the executor ran step-start retrieval each step and could append newly retrieved skill context.
   - Current path before this update: step-start retrieval only ran when the previous tool observation was an actionable error. This is cleaner but not behaviorally identical to latest train50.

3. Maintenance/extraction changed independently of TRL.
   - Old latest train50 still had `extractor_trl_enabled=true`, but `candidate_competition=false/sample_count=1`.
   - Later current runs also changed credit exposure, pending/trial skill logic, candidate group feedback, skill injector, and prompt compaction. Therefore later TRL underperformance cannot be attributed to TRL alone.

Code changes made in current framework:

1. `academic/benchmarks/bfcl/adapter.py`
   - Added `BFCL_SKILL_INJECTOR_GATE`.
   - Supported values:
     - `llm`: current LLM relevance gate.
     - `deterministic` / `legacy` / `legacy_full`: old deterministic injection path.
   - In deterministic `full` mode, prompt rendering uses `artifact_store.build_prompt(turn_prompt_skills)` to match old full prompt injection.
   - Injector cost/debug metadata now records `gate`.

2. `academic/benchmarks/bfcl/adapter.py`
   - Added `BFCL_DYNAMIC_RETRIEVAL_POLICY`.
   - Supported values:
     - `error_only`: current behavior.
     - `every_step`: old behavior; run step-start retrieval even without actionable error.
     - `off`: no dynamic step-start retrieval.

3. `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`
   - Added `test_bfcl_legacy_deterministic_injector_gate_skips_llm`.
   - Added `test_run_bfcl_task_legacy_every_step_retrieval_without_error`.

Verification:

```text
python -m py_compile academic/benchmarks/bfcl/adapter.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py
pytest -q \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_legacy_deterministic_injector_gate_skips_llm \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_run_bfcl_task_legacy_every_step_retrieval_without_error \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_run_bfcl_task_error_feedback_uses_step_start_context_update \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_injector_rescues_explicit_engine_start_subtask
```

Result:

```text
4 passed
```

Smoke run:

```text
BFCL_SKILL_INJECTOR_GATE=deterministic
BFCL_DYNAMIC_RETRIEVAL_POLICY=every_step
python -m academic.benchmarks.bfcl.related.experiment \
  --mode evolve \
  --manifest academic/experiments/bfcl_case_lists/debug_current_legacy_notrl_2_2_20260520.json \
  --expected-train-size 2 \
  --expected-test-size 2 \
  --output academic/results/bfcl_current_legacy_notrl_smoke2_20260520.json \
  --checkpoint academic/results/bfcl_current_legacy_notrl_smoke2_20260520_checkpoint.json \
  --save-skills academic/results/bfcl_current_legacy_notrl_smoke2_20260520_skills.json \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --epochs 1 \
  --micro-maintenance-step 1 \
  --macro-maintenance-step 2 \
  --test-concurrency 1 \
  --train-window-concurrency 1 \
  --top-k-skills 2 \
  --skill-injection-mode prompt_only \
  --max-steps-per-turn 20 \
  --max-task-seconds 180 \
  --disable-extractor-trl \
  --tag bfcl_current_legacy_notrl_smoke2_20260520
```

Smoke result:

- output: `academic/results/bfcl_current_legacy_notrl_smoke2_20260520.json`
- train: success_rate `0.50`, avg_score `0.9000`.
- test: success_rate `0.00`, avg_score `0.7320`.
- maintenance token roles: `extractor`, `refactorer`, `bundle_builder`.
- no maintenance `skill_injector` LLM role.
- `role_feedback.extractor.rules=[]`, `extractor_trl_enabled=false`.
- final skills: `reuse_order_id_from_place_order_context`.

Note: executor cost accounting still records zero-token deterministic `injector` audit events. These are not LLM calls; they mark deterministic skill selection/rendering.

A broader unrelated test command had two pre-existing/unrelated failures:

- `test_spreadsheet_task_passes_model_override_to_llm`: spreadsheet cost event order expected executor first but current spreadsheet emits injector first.
- `test_historical_bfcl_train_details_produce_nonempty_maintenance_assets`: missing local fixture `bfcl_v3_glm47_official_tracecheck_evolve_3x3_partial_train.json`.

No-TRL ablation plan in current framework:

All runs should use the same manifest and current code:

```text
academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json
```

Common env for latest-compatible base:

```text
BFCL_SKILL_INJECTOR_GATE=deterministic
BFCL_DYNAMIC_RETRIEVAL_POLICY=every_step
```

Four diagnostic runs:

1. `current_legacy_notrl_base`
   - `--disable-extractor-trl`
   - no candidate competition
   - deterministic full injection
   - every-step dynamic retrieval

2. `current_legacy_notrl_llm_injector`
   - `--disable-extractor-trl`
   - no candidate competition
   - `BFCL_SKILL_INJECTOR_GATE=llm`
   - every-step dynamic retrieval

3. `current_legacy_notrl_compete`
   - `--disable-extractor-trl`
   - `--enable-candidate-competition --candidate-sample-count 3`
   - deterministic full injection
   - every-step dynamic retrieval

4. `current_legacy_notrl_both`
   - `--disable-extractor-trl`
   - `--enable-candidate-competition --candidate-sample-count 3`
   - LLM injector gate
   - every-step dynamic retrieval

Interpretation rule:

- If `current_legacy_notrl_base` is close to latest train50, then current framework can reproduce the old behavior and later regression is mainly from LLM injector / competition / TRL interactions.
- If `current_legacy_notrl_base` is already much worse, then the current code/prompt/maintenance path still differs materially from latest train50 and must be further aligned before TRL tuning.
- If `+llm_injector` drops while base holds, injector gate is the main behavioral bottleneck.
- If `+compete` drops while base holds, candidate competition/store exposure is the main bottleneck.
- If only `+both` drops, the interaction between noisy candidates and LLM injector is the bottleneck.

## 12. 2026-05-20 completed role-aligned 50/50 ablation

The requested current-framework ablation has now completed for the two most important settings:

1. no TRL, candidate competition on, deterministic injector;
2. no TRL, candidate competition on, LLM injector.

Both use:

```text
manifest = academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json
BFCL_EXTRACTOR_EXISTING_ARTIFACTS=full_store
BFCL_DYNAMIC_RETRIEVAL_POLICY=every_step
--disable-extractor-trl
--enable-candidate-competition
--candidate-sample-count 3
--top-k-skills 2
--skill-injection-mode prompt_only
--max-steps-per-turn 20
```

Result files:

- deterministic: `academic/results/bfcl_align_notrl_compete_detinj_20260520.json`
- LLM injector: `academic/results/bfcl_align_notrl_compete_llminj_20260520.json`

### Metrics

| run | phase | strict | official_valid | avg_score | recall | precision | avg_tokens | elapsed_s | injector calls |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| latest train50 | train | 0.20 | 0.58 | 0.8090 | 0.8789 | 0.7697 | 62339.2 | 34.471 | 0 |
| latest train50 | test | 0.08 | 0.70 | 0.7892 | 0.8955 | 0.7193 | 83043.9 | 36.932 | 0 |
| aligned no-TRL deterministic | train | 0.26 | 0.62 | 0.8325 | 0.8917 | 0.7982 | 61739.7 | 32.637 | 0 |
| aligned no-TRL deterministic | test | 0.12 | 0.74 | 0.7901 | 0.8938 | 0.7235 | 81663.5 | 35.683 | 0 |
| aligned no-TRL LLM injector | train | 0.26 | 0.66 | 0.8441 | 0.9035 | 0.8059 | 61484.3 | 54.196 | 809 total |
| aligned no-TRL LLM injector | test | 0.10 | 0.60 | 0.7763 | 0.8693 | 0.7139 | 74948.4 | 67.044 | 809 total |
| original TRL | test | 0.08 | 0.52 | 0.7474 | 0.8491 | 0.6850 | 76689.5 | 79.253 | 1197 total |
| injector-gate TRL | test | 0.08 | 0.60 | 0.7530 | 0.8371 | 0.7096 | 74175.7 | 49.427 | 459 total |

### What this answers

The code/role alignment did identify a real cause of the earlier ambiguity. The current refactored framework was not behaviorally identical to `latest train50`:

- deterministic full injection had been replaced by an LLM skill-injector gate;
- dynamic retrieval had changed from every-step retrieval to error-only retrieval;
- extractor context had changed from full-store visibility to empty existing artifacts in the related experiment path.

After aligning these role behaviors while staying in the current framework, the no-TRL deterministic competition run does not regress. It is slightly better than latest train50 on heldout strict, official_valid, and avg_score:

```text
latest train50 test:                 strict 0.08, official 0.70, avg 0.7892
aligned no-TRL deterministic test:   strict 0.12, official 0.74, avg 0.7901
```

This means:

- The refactored current framework can still produce a working BFCL result.
- Candidate competition alone is not the main cause of the TRL drop, because competition with TRL disabled and deterministic injection is the best current heldout result.
- The previous negative TRL result is not explained by train/test split mismatch or a broken current executor.

### Why the previous TRL dropped

The best-supported explanation is now:

1. **TRL changed the extractor's meta-policy in the wrong direction for some BFCL families.**
   - The TRL runs learned extractor role rules from candidate credit/candidate competition feedback.
   - Earlier case analysis showed these rules often emphasized error-recovery, exact observable trigger fields, and avoiding result-reuse.
   - Those biases conflict with BFCL expected calls in several domains: Vehicle often needs proactive precondition sequences; Travel and Trading often need result reuse across turns.

2. **The LLM injector is harmful in this setting.**
   - In the aligned no-TRL ablation, turning on LLM injector changes heldout from `0.12 / 0.74 / 0.7901` to `0.10 / 0.60 / 0.7763`.
   - It adds `809` skill-injector calls and `934805` skill-injector tokens.
   - This reproduces the earlier symptom: injector filtering can improve some train/partial metrics, but it filters or distorts heldout skill exposure.

3. **TRL plus injector is worse than either idea in isolation.**
   - No-TRL deterministic competition works.
   - No-TRL LLM injector is worse on heldout.
   - TRL with LLM injector is also worse than the aligned deterministic ablation.
   - Therefore the failure path is the interaction of TRL-generated meta-rules/candidate population with an LLM gate that decides what the executor actually sees.

### Current conclusion

Yes, this alignment found the main engineering reason why later runs were not comparable to latest train50: the role behavior had changed. It also narrowed the TRL regression cause.

The immediate paper evidence should use `aligned no-TRL deterministic` as the strongest current BFCL result under fixed 50/50:

```text
test strict       6/50 = 0.12
test official     37/50 = 0.74
test avg_score    0.7901
```

The next TRL work should not keep the current LLM injector on by default. TRL should first be tested with deterministic injection, and its meta-rules should be constrained by domain family:

- Vehicle: allow proactive precondition sequences when the requested action requires them.
- Travel/Trading: preserve transaction/result-reuse chains.
- Messaging/social: avoid formatting-only skills unless they change official expected calls.
