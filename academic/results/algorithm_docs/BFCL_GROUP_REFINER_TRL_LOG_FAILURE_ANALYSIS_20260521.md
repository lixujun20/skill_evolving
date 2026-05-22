# BFCL Group-Refiner TRL Log Failure Analysis - 2026-05-21

## 结论

可以把这次结果理解为：TRL 仍然没有在主指标上 work。

更精确地说，这次 group-refiner TRL 不是完全无效。它把 test `avg_score` 从 baseline 的 `0.7993` 轻微提高到 `0.8021`，并且某些局部技能确实改善了部分 case 的 official_valid。但它没有把收益转化成我们真正关心的 strict success，反而让 strict success 从 `6/50 = 0.12` 掉到 `4/50 = 0.08`，official_valid 也从 `35/50 = 0.70` 掉到 `33/50 = 0.66`。

核心失败原因有四个：

1. TRL 反馈后 extractor 明显过度生成，技能库从 baseline 的 `19` 个膨胀到 `283` 个，其中 `267` 个仍是 `trial`，并且大量是重复的 `consolidate_*` / `shared_subdoc`。
2. candidate-group feedback 的信用信号主要是 official/soft correctness，strict helpful/harmful 在本轮几乎没有信号，因此 TRL 学到的是“提高局部 call F1 / official_valid”，不是“消除 extra/missing call 以获得 strict success”。
3. group-refiner 确实执行了，但 window 3 的 LLM group-refiner 三次打满 `4096` completion tokens 后失败，落到 heuristic fallback；这说明这条链路还不稳定，而且输入/输出规模已经过大。
4. 最终暴露到 heldout 的 active skills 有局部帮助，但仍会诱导时序错误或额外调用。它们改善了一些 official_valid case，同时制造了 strict 退化。

## 对比设置

两个主要对比文件：

- baseline: `academic/results/bfcl_baseline_detinj_after_baseline_fixes_20260521_run2.json`
- TRL: `academic/results/bfcl_trl_group_refiner_50_50_20260521.json`
- TRL log: `academic/results/bfcl_trl_group_refiner_50_50_20260521.log`
- TRL skills: `academic/results/bfcl_trl_group_refiner_50_50_20260521_skills.json`

关键设置一致：

| item | baseline | TRL |
|---|---:|---:|
| train/test | 50/50 | 50/50 |
| model | `claude-sonnet-4-5` | `claude-sonnet-4-5` |
| skill injection | `prompt_only` | `prompt_only` |
| top_k_skills | `2` | `2` |
| heldout_allow_trial_skills | `False` | `False` |
| heldout_allow_candidate_skills | `True` | `True` |
| extractor_trl_enabled | `False` | `True` |

因此这次差异主要来自 TRL/group-refiner 维护链路，而不是 test set 或模型设置变化。

## 总指标

| split | run | strict success | official_valid | avg_score |
|---|---|---:|---:|---:|
| train | baseline | `13/50 = 0.26` | `0.62` | `0.8264` |
| train | TRL | `11/50 = 0.22` | `0.70` | `0.8338` |
| test | baseline | `6/50 = 0.12` | `0.70` | `0.7993` |
| test | TRL | `4/50 = 0.08` | `0.66` | `0.8021` |

这个 pattern 很重要：TRL 提高了 train/test 的软分或 official_valid 的一部分，但 strict success 没提升。说明它学到的是“局部调用更接近期望”，不是“完整工具序列完全匹配”。

## 逐 Task 变化

同一批 50 个 test task 顺序和 task_id 完全一致。

score delta 统计：

- `29` 个 task score 不变。
- `10` 个 task score 上升。
- `11` 个 task score 下降。
- 平均 delta 为 `+0.00283`，所以 avg_score 的提升非常小。

strict success 变化：

| task_id | baseline | TRL | delta | TRL injected skills | 主要变化 |
|---|---:|---:|---:|---|---|
| `multi_turn_base_86` | strict pass | strict fail | `-0.0588` | `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2`, `tire_shop_navigation_requires_shop_lookup_first__candidate_r0_t17_s1` | TRL 多了 `posting_get_login_status` extra call |
| `multi_turn_base_160` | strict pass | strict fail | `-0.1111` | `contact_customer_support_message_brevity_rule` | TRL 多了 `get_flight_cost` extra call |
| `multi_turn_base_101` | strict pass | strict fail | `-0.1429` | `skip_symbol_lookup_for_known_company_names` | TRL 多了 `message_get_login_status` extra call |
| `multi_turn_base_76` | strict fail | strict pass | `+0.1111` | `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2`, `tire_shop_navigation_requires_shop_lookup_first__candidate_r0_t17_s1` | TRL 去掉了 baseline 的 `posting_get_login_status` extra call |

净变化是 `-2` strict successes。

official_valid 变化：

| direction | task_id | delta | TRL injected skills | 主要变化 |
|---|---|---:|---|---|
| lost | `multi_turn_base_68` | `-0.1333` | vehicle engine + tire shop | TRL 把 `set_navigation` 提前到 turn 2，后续 turn 3 缺 `set_navigation`，turn 4 缺 `post_tweet` |
| lost | `multi_turn_base_52` | `-0.1190` | vehicle engine + tire shop | TRL 在 tire check turn 额外调用 `set_navigation` |
| lost | `multi_turn_base_53` | `-0.0419` | tire shop + engine + navigation | TRL official_valid 从 true 掉 false |
| lost | `multi_turn_base_78` | `-0.1071` | tire shop + engine | TRL official_valid 从 true 掉 false |
| gained | `multi_turn_base_103` | `+0.2571` | `skip_symbol_lookup_for_known_company_names` | TRL 补上了 `place_order` / `get_order_details` / `send_message`，official_valid 变 true |
| gained | `multi_turn_base_149` | `+0.1666` | `skip_symbol_lookup_for_known_company_names` | TRL 去掉 `get_symbol_by_name`，补上 `delete_message`，official_valid 变 true |

净变化是 `-2` official_valid。

## Heldout 注入技能关联

TRL test 中真正暴露的 active skill 很少，但影响明显：

| skill | injected tasks | avg delta | TRL strict | TRL official_valid | baseline official_valid on same tasks |
|---|---:|---:|---:|---:|---:|
| `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2` | 14 | `-0.0253` | `1/14` | `8/14` | `12/14` |
| `tire_shop_navigation_requires_shop_lookup_first__candidate_r0_t17_s1` | 14 | `-0.0253` | `1/14` | `8/14` | `12/14` |
| `skip_symbol_lookup_for_known_company_names` | 13 | `+0.0551` | `1/13` | `12/13` | `10/13` |
| `contact_customer_support_message_brevity_rule` | 12 | `-0.0113` | `1/12` | `7/12` | `7/12` |
| no injected skills | 11 | `-0.0077` | `1/11` | `6/11` | `6/11` |

解释：

- `skip_symbol_lookup_for_known_company_names` 是本轮最明显的正例：它提升了 TradingBot 的 official_valid 和 avg_score，但仍然可能引入 strict extra call，导致 `multi_turn_base_101` 从 strict pass 变 strict fail。
- vehicle/tire 两个技能是主要负例：它们关联的 14 个 task 中，baseline official_valid 是 `12/14`，TRL 只有 `8/14`。这说明它们在 heldout 上不是稳定正收益。
- no-injection task 的 strict 和 official_valid 基本不变，说明主要退化来自 skill exposure，而不是完全随机噪声。

## 具体 Case

### Case 1: `multi_turn_base_68`

baseline:

- score `0.8`
- strict fail, official_valid true
- injected: `vehicle_engine_start_brake_precondition`
- actual calls 包含期望的 `find_nearest_tire_shop`，并在下一 turn 调用 `set_navigation`
- errors 主要是 extra `displayCarStatus` / extra `startEngine` / extra `posting_get_login_status`

TRL:

- score `0.6667`
- strict fail, official_valid false
- injected: `vehicle_engine_start_requires_brake_pedal_pressed__candidate_r0_t19_s2`, `tire_shop_navigation_requires_shop_lookup_first__candidate_r0_t17_s1`
- actual calls 在 turn 2 调用了 `check_tire_pressure`, `find_nearest_tire_shop`, `set_navigation`
- turn 3 expected `set_navigation` missing
- turn 4 expected `post_tweet` missing

这里的核心问题是 tire-shop skill 把“找到 tire shop”与“立即导航”绑定得太强。这个 task 的 expected 是 turn 2 找店，turn 3 用户再要求 GPS 时才 `set_navigation`。TRL skill 提前执行，导致时序错位。

### Case 2: `multi_turn_base_52`

baseline:

- score `0.8333`
- official_valid true
- injected: `vehicle_engine_start_brake_precondition`
- tire pressure turn 只调用 `check_tire_pressure` 和 `find_nearest_tire_shop`

TRL:

- score `0.7143`
- official_valid false
- injected: vehicle engine + tire shop
- tire pressure turn 多了 `set_navigation(destination='456 Oakwood Avenue, Rivermist, 83214')`

这个 case 说明 tire-shop skill 的 scope 仍然过宽。用户说的是 tire pressure 不达标时“point me towards nearest tire shop”，expected 只要 `find_nearest_tire_shop`，不是导航。

### Case 3: `multi_turn_base_86`

baseline strict pass，TRL strict fail。

唯一明显差异是 TRL 在最终发 tweet 前多了：

```text
posting_get_login_status()
```

这不是 vehicle/tire skill 直接要求的调用，但说明 injected prompt 改变了 executor 的决策边界：即使 official_valid 仍为 true，多一个无关工具调用就足以损坏 strict success。

### Case 4: `multi_turn_base_101`

baseline strict pass，TRL strict fail。

TRL injected `skip_symbol_lookup_for_known_company_names` 后，主要交易调用仍正确，但多了：

```text
message_get_login_status()
```

这说明 `skip_symbol_lookup_for_known_company_names` 对 official_valid 有帮助，但不能保证 strict-clean。它减少了一类 TradingBot symbol lookup 错误，却没有抑制 MessageAPI 的登录状态 extra call。

### Case 5: `multi_turn_base_103`

这是 TRL 的正例。

baseline:

- score `0.6`
- official_valid false
- injected: `remove_watchlist_conditional_check__candidate_r0_t3_s1`, `trading_bot_direct_symbol_binding`, `trading_market_price_order_workflow__candidate_r0_t6_s1`
- 后续缺 `place_order`, `get_order_details`, `send_message`

TRL:

- score `0.8571`
- official_valid true
- injected: `skip_symbol_lookup_for_known_company_names`
- 补上了 `place_order`, `get_order_details`, `send_message`
- 但仍多了 `get_symbol_by_name` 和 `get_user_id`，所以 strict 仍 false

这说明 TRL 不是完全没学到东西；问题是它学到的收益停在 official/partial 层面，没有转化成 strict。

## 维护链路证据

### 1. extractor 过度生成

baseline:

- extraction_events: `16`
- 有 extraction 的 task 数: `5`
- final skills: `19`
- skill statuses 主要是少量 active/trial

TRL:

- extraction_events: `279`
- 有 extraction 的 task 数: `29`
- final skills: `283`
- final statuses:
  - `trial`: `267`
  - `active`: `9`
  - `disabled`: `4`
  - `stale`: `2`
  - `archived`: `1`
- final kinds:
  - `workflow_guardrail_card`: `151`
  - `shared_subdoc`: `123`
  - `atomic_tool_rule_card`: `8`
  - `interface_contract_card`: `1`

按 10-task window 统计的 extraction events：

| window | baseline | TRL |
|---:|---:|---:|
| 0-9 | 10 | 4 |
| 10-19 | 6 | 20 |
| 20-29 | 0 | 120 |
| 30-39 | 0 | 21 |
| 40-49 | 0 | 114 |

最异常的是 TRL 在 task 20-29 和 40-49 大量生成 `consolidate_*` 技能。典型名称包括：

```text
consolidate_withdraw_funds_workflow_duplicates__candidate_r0_t20_s0
consolidate_vehicle_parking_brake_lock_duplicates__candidate_r0_t20_s0
consolidate_tire_shop_navigation_lookup_duplicates__candidate_r0_t20_s0
consolidate_parking_brake_requires_brake_pedal_duplicates__candidate_r0_t20_s0
```

这很可能是 extractor feedback 中“avoid duplicates / consolidate”的规则被模型理解成“生成 consolidate 类 meta skill / shared_subdoc”，而不是停止输出重复技能。也就是说，TRL prompt 的文字方向是对的，但没有硬约束产物数量和类型，导致反馈被错误消化。

### 2. extractor 输出长度爆炸

从 log 中统计 extractor LLM done：

baseline:

- extractor calls: `150`
- average response chars: `324.7`
- response chars `>1000`: `11`
- response chars `>7000`: `0`
- first 50 calls avg response chars: `908.0`
- mid 50 calls avg response chars: `33`
- last 50 calls avg response chars: `33`

TRL:

- extractor calls: `150`
- average response chars: `4551.5`
- response chars `>1000`: `79`
- response chars `>7000`: `61`
- first 50 calls avg response chars: `358.6`
- mid 50 calls avg response chars: `7374.1`
- last 50 calls avg response chars: `5921.7`

这说明 TRL 反馈不是让 extractor 更谨慎，而是在中后段把 extractor 变成长输出、高产出模式。

### 3. token 开销上涨

维护阶段 token breakdown：

| role | baseline total tokens | TRL total tokens | change |
|---|---:|---:|---:|
| extractor | `1,074,292` | `1,244,621` | `+170,329` |
| extractor output tokens | `13,591` | `170,267` | `+156,676` |
| refiner | `163,643` | `296,634` | `+132,991` |
| credit_assigner | `124,181` | `195,881` | `+71,700` |
| group_refiner | `0` | `50,896` | `+50,896` |
| total maintenance | `1,582,418` | `1,955,017` | `+372,599` |

开销上涨不是主要由 executor test 引起，而是维护侧的 extractor/refiner/credit/group-refiner 链路膨胀导致。

### 4. group-refiner 执行但不稳定

TRL maintenance windows：

| window | candidate rows | actions | source | note |
|---:|---:|---:|---|---|
| 0 | 0 | 0 | heuristic | no rows |
| 1 | 1 | 3 | LLM | only backup actions for neutral withdrawal skills |
| 2 | 4 | 23 | LLM | keep/refine/archive mixed actions |
| 3 | 4 | 32 | heuristic_fallback | LLM failed after max-token JSON/value error |
| 4 | 0 | 0 | heuristic | no rows |

window 3 log 中 group_refiner 三次调用都打满 completion token：

```text
attempt 1: user_chars=18015, completion_tokens=4096, duration_ms=44504
attempt 2: user_chars=19518, completion_tokens=4096, duration_ms=86656
attempt 3: user_chars=19518, completion_tokens=4096, duration_ms=126428
```

随后 result 中记录：

```text
source = heuristic_fallback
llm_error_type = ValueError
```

这说明 group-refiner 当前 prompt/schema 太 verbose，无法稳定输出可解析 action。fallback 保住了实验继续跑，但这不等价于 LLM TRL 稳定生效。

### 5. credit 信号没有 strict 分辨率

TRL candidate group rows 汇总：

```text
members = 58
retrieved_count = 96
injected_count = 96
used_count = 0
helpful_count = 38
harmful_count = 27
neutral_count = 88
strict_helpful_count = 0
strict_harmful_count = 0
official_helpful_count = 33
official_harmful_count = 13
```

所有 candidate group member 的 `used_count` 都是 `0`，因为 BFCL 当前是 prompt-only skill injection，不是 callable skill 直接调用。因此 credit 实际上是 retrieval/injection 关联，不是“模型真的使用了某个 skill”的直接因果证据。

更关键的是，`strict_helpful_count = 0` 且 `strict_harmful_count = 0`。本轮 TRL 的 group comparison 几乎完全依赖 official/soft signal。结果自然是：它可能学会减少一些 missing call 或提高 call F1，但不会优先惩罚 strict 中最致命的 extra call。

```
comment:
credit assigner应该判断每个skill是否被调用过才行，包括规则类的，要判断有没有真正在executor输出中生效。现在没有这个实现嘛？
```

## TRL 反馈本身的问题

最终 extractor feedback 规则是：

```text
Extract skills only when the source task demonstrates the pattern through actual tool usage or explicit user requests, not from hypothetical or unused tool availability.
When extracting workflow guardrails, ensure the triggering condition and the guarded action both appear in the source task trace.
Avoid generating multiple candidate variants with identical semantic content; if sampling produces duplicates, consolidate to a single representative before emission.
Scope guardrail skills to specific tool pairs or workflow sequences actually demonstrated, not to general tool categories that may appear in unrelated contexts.
Before extracting a workflow guardrail, estimate its reuse potential by checking whether the triggering condition appears frequently across the task distribution, not just in the source task.
```

这些规则从文字看是合理的，但实际消化失败：

1. 它们没有硬性限制“最多产出几个 skill / 总长度 / 禁止 shared_subdoc / 禁止 consolidate meta skill”。
2. “consolidate duplicates” 被模型转化成大量 `consolidate_*` 产物，而不是少产出。
3. feedback 只给 extractor，没有给 refactorer 产生有效规则；`role_feedback.refactorer.rules` 仍是 `0`。
4. refiner 虽然有执行，但更多是在事后修补已产生的坏 skill，而不是从源头控制 skill creation。

## 为什么会出现“avg_score 有一点涨，strict 反而掉”

BFCL strict success 对 extra call 非常敏感。TRL 学到的 prompt-only skills 能帮助模型多补一些 expected call，因此 call F1 / avg_score 可以涨；但它们同时也会诱导额外工具调用，导致 strict 失败。

典型模式：

- TradingBot symbol skill 让模型少做 `get_symbol_by_name`，提高 official_valid，但 message/login 类 extra call 仍会破坏 strict。
- tire-shop skill 让模型更主动调用 `find_nearest_tire_shop` / `set_navigation`，但在多轮时序里会提前导航或把“point me”误解成“set GPS now”。
- support brevity skill 没有解决 flight booking 场景中的额外 `get_flight_cost`，反而有一个 strict pass 被破坏。

所以这次 TRL 的收益主要是 soft/official 层面的局部纠错，不是 strict-clean sequence learning。

## 当前实现中已经做对的部分

这次不是所有东西都失败：

1. group-refiner action path 确实接入了，并在 window 1/2 产出 LLM action。
2. 有问题的技能能被 filter/disable 一部分，例如：
   - `vehicle_refuel_skip_fuel_status_check_when_amount_explicit__candidate_r0_t19_s0` 被 disabled。
   - `vehicle_parking_omits_redundant_engine_start__candidate_r0_t18_s2` 被 disabled。
   - `vehicle_parking_requires_brake_and_lock_sequence__candidate_r0_t17_s2` 后续被 disabled。
3. `skip_symbol_lookup_for_known_company_names` 是一个有效局部改进，在一些 TradingBot case 上提升 official_valid。

问题在于这些机制不足以控制整体技能库质量和 strict metric。

## 建议的修复方向

短期最应该做的不是继续扩大 TRL，而是先把 TRL 的产物约束住：

1. extractor 输出硬约束：
   - 每个 task 最多产出 `K` 个 skill，例如 `K=1` 或 `K=2`。
   - 禁止输出 `shared_subdoc` / `consolidate_*` 作为普通 skill，除非进入单独 refactorer 路径。
   - 超过长度或数量直接二次要求压缩；仍不满足则丢弃。

```
comment:
没必要专门约束这类pattern，这只是一次偶然因素。
extractor的输出个数约束是合理的，设置成2吧。既要做硬约束， 也要加入到prompt当中。
```

2. TRL feedback 改成可执行规则，而不是抽象建议：
   - 不要只写“avoid duplicates”。
   - 应该写成“if candidates are semantically duplicate, emit exactly one skill; do not create consolidation skills”。

```
comment:
好的，试一下吧，改prompt就行
```

3. group-refiner schema 压缩：
   - 限制每个 window 最多处理 top-N candidate groups。
   - 限制每个 group 最多输出一个 winner action + 少量 archive action。
   - 不要求长 analysis；analysis 单独留给日志，不进入必须解析的 JSON。

```
comment:
每次做replay buffer采样吧，随机采样N个，N=5
可以每个group并发调用refiner，不要用同一个做，各自开一个对话
我其实好奇，他在说什么？你是让他在一个对话里面干了太多任务吧？如果是多轮生成应该不会触发token限制？另一个方面，如果他说的在理，应该是我们的max token设小了？
```

4. strict-oriented credit：
   - candidate group comparison 中显式加入 extra_call/missing_call 对 strict 的破坏。
   - 即使 official_valid 为 true，只要引入 extra call，也要作为 strict_harmful 证据。
   - promotion/exposure 不应该只看 official_helpful_count，要要求 strict-clean 或至少 no-extra-call improvement。

```
comment:
没必要，我觉得效果还可以，只是涨得不多。
```

5. retrieval/exposure 保护：
   - 对 active skill 也要有 domain/turn-stage gate。
   - tire-shop 类 skill 必须区分：
     - “find/point me to nearest tire shop” -> `find_nearest_tire_shop`
     - “set GPS / navigate to that shop” -> `set_navigation`
     - 多轮中不能把后一 turn 的动作提前到当前 turn。

```
comment:
发生过提前嘛？这是bug吧？
```

6. 先做小规模 ablation：
   - no-TRL + hard extractor cap
   - TRL + hard extractor cap
   - TRL + strict-oriented credit
   - TRL + compact group-refiner schema

我的判断是：这次 TRL 没 work 的主要原因不是“meta signal 完全没价值”，而是“meta signal 没有被约束成稳定、少量、strict-aligned 的技能更新”。在当前形式下，它会把 extractor 推向过度生成，维护链路再努力 refine/filter 也追不上。
