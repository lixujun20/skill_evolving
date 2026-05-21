# BFCL 150/50 Mid-run Case Study: Pending, Refactor Promotion, Refiner Keep

本文档回答 2026-05-17 暂停的 BFCL related 150/50 evolve 实验中三个问题：

1. 为什么 store 里有大量 `pending` skill？
2. 为什么 posterior/refactor 生成的 skill 没有持续贡献到 active store？pending 为什么没有被 promotion？
3. 为什么 refiner 大量返回 `keep` / `strict_failure_kept`？

分析对象为暂停时的 checkpoint sidecar：

- `academic/results/claude_proxy_related150_50_parallel_20260517_170640_evolve_checkpoint.json`
- `academic/results/claude_proxy_related150_50_parallel_20260517_170640_evolve_checkpoint_current_round_details.json`
- `academic/results/claude_proxy_related150_50_parallel_20260517_170640_evolve_checkpoint_current_round_store.json`
- `academic/results/claude_proxy_related150_50_parallel_20260517_170640_evolve_checkpoint_current_round_overlap_state.json`

## 0. 暂停状态

实验已经暂停。当前没有匹配到仍在运行的 BFCL evolve 进程。

Checkpoint 进度：

| 字段 | 数值 |
|---|---:|
| `next_task_index` | 109 / 150 |
| train details | 109 |
| partial current window details | 9 |
| prefetched train details | 1 |
| macro windows | 10 |
| micro maintenance reports | 109 |
| extraction events | 96 |
| credit events | 148 |
| store artifacts | 99 |
| store test results | 102 |
| segment rows | 405 |

Store artifact 状态：

| status | 数量 |
|---|---:|
| `pending` | 91 |
| `disabled` | 8 |
| `active` | 0 |

Artifact source：

| source | 数量 |
|---|---:|
| `llm_trace_extraction` | 91 |
| `llm_refactor_overlap` | 8 |

Pending skill 类型：

| kind | 数量 |
|---|---:|
| `workflow_guardrail_card` | 36 |
| `interface_contract_card` | 32 |
| `atomic_tool_rule_card` | 23 |

Overlap state 中的异构节点：

| node_type | 数量 |
|---|---:|
| `trace_segment` | 405 |
| `pending_skill` | 96 |
| `skill` | 7 |

注意：overlap sidecar 中没有单独的 top-level `edges` 列表；边/相似度主要保存在 postings、pair scores 和 refactor window 的 clique payload 中。因此“异构图节点进入 state”已经发生，但“pending skill 被 LLM 明确作为 affected update 提升”没有发生。

## 1. 为什么这么多 pending？

结论：这不是 posterior 没有跑，而是 prior extractor 的默认语义就是“先生成 pending 候选，不进 retrieval，不进 prompt”。当前实现会把每个在线提取出的 artifact 标为：

```text
status = "pending"
metadata.is_pending_skill = True
metadata.is_promoted = False
metadata.promotion_state = "pending"
metadata.retrieval_disabled_reason = "pending_prior_candidate"
metadata.source = "llm_trace_extraction"
```

对应代码路径：

- `academic/benchmarks/bfcl/related/experiment.py::_mark_prior_artifacts_pending`
- `academic/skill_repository/store.py::ArtifactStore.add_pending`

这说明 91 个 `llm_trace_extraction` artifact 并不是已经上线使用的 active skill，而是 prior stage 的候选池。

当前 pending 没有 usage：

| status | artifact 数 | usage_count 总和 |
|---|---:|---:|
| pending | 91 | 0 |
| disabled | 8 | 0 |
| active | 0 | 0 |

这符合 pending 被 retrieval-disabled 的设计，但也暴露了一个算法实现问题：如果 posterior refactor 没有把 pending 明确提升，prior extractor 会持续堆积候选，而不会对执行产生直接贡献。

```
comment
pending经过一段时间没有被promote不会被disable嘛？
```

## 2. Posterior/refactor 是否生成 active skill？为什么现在没有 active？

结论：posterior/refactor 确实生成了 8 个 skill，并且 commit 时进入过 active store；但到暂停时全部被 disable。不是 refactor 被关了，而是 refactor 产生的 active skill 在后续 retrieval/credit 中被判定为跨域污染或不稳，最终被 credit filter 禁用。

8 个 refactor-created skill：

| skill | 当前状态 | disable 原因 | negative margin |
|---|---|---|---:|
| `tradingbot_direct_ticker_binding` | disabled | `credit_assignment_negative_margin` | 6 |
| `vehicle_engine_start_brake_pedal_prerequisite` | disabled | spurious overlap clustering | n/a |
| `vehicle_engine_start_brake_requirement` | disabled | `credit_assignment_negative_margin` | 7 |
| `navigate_to_tire_shop_after_lookup` | disabled | `credit_assignment_negative_margin` | 8 |
| `contact_customer_support_message_literal_binding` | disabled | `credit_assignment_negative_margin` | 2 |
| `avoid_redundant_airport_lookup_for_major_cities` | disabled | `credit_assignment_negative_margin` | 6 |
| `book_flight_parameter_binding_contract` | disabled | `credit_assignment_negative_margin` | 5 |
| `retrieve_invoice_parameter_binding` | disabled | `credit_assignment_negative_margin` | 8 |

Macro window summary：

| window | task range | LLM attempts | committed | rejected | pending promotions |
|---:|---|---:|---:|---:|---:|
| 0 | 0-9 | 10 | 1 | 0 | 0 |
| 1 | 10-19 | 7 | 1 | 0 | 0 |
| 2 | 20-29 | 10 | 1 | 0 | 0 |
| 3 | 30-39 | 1 | 1 | 0 | 0 |
| 4 | 40-49 | 1 | 1 | 0 | 0 |
| 5 | 50-59 | 1 | 1 | 0 | 0 |
| 6 | 60-69 | 0 | 0 | 0 | 0 |
| 7 | 70-79 | 2 | 1 | 1 | 0 |
| 8 | 80-89 | 1 | 1 | 0 | 0 |
| 9 | 90-99 | 0 | 0 | 0 | 0 |

关键点：每个 window 的 `pending_skill_promotions` 都是 0。

Promotion 的代码路径是：

```text
_promote_pending_from_refactor_report()
  for committed attempt:
    for name in _pending_skill_names_from_refactor_attempt(attempt):
      store.promote_pending(name, reason="posterior_refactor_overlap_evidence")
```

也就是说，只有 refactor LLM 的 committed payload 中把某个 pending skill 名字放入 `affected_skill_updates` / 相关 pending-name 字段，才会 promotion。当前 8 次有效 commit 的 `affected_skill_updates` 基本为空；LLM 选择的是创建新 `shared_skill`，不是把 pending candidate 提升成 active skill。因此 pending 没有贡献到 active。

```
comment:
看来pending不怎么会贡献到refactor？这是为什么？按照概率不合理，trace只是pending的几倍，不应该一个pending都没有贡献到active（即便是shared，对应的pending candidate也应该被promote）。算法实现有没有问题？
```

```
回复：
是的，这里更像算法实现/接口设计有问题，不应该解释成正常概率现象。pending candidate 如果已经来自相同 trace evidence，那么 refactor 即便最终创建 shared skill，也应该能把对应 pending candidate 标记为 contributed / superseded / promoted，而不是完全没有关系。

当前问题在于 pending 和 refactor shared skill 之间缺少显式 lineage matching：

- refactor LLM 只被要求输出 `shared_skill`，没有被强制对输入 clique 里已有 pending candidate 做逐个归因。
- promotion 代码只看 committed payload 里的 pending name 字段；如果 LLM 没填 `affected_skill_updates`，代码就认为没有 pending 贡献。
- 系统没有用 `source_task_ids`、trace segment ids、evidence span、tool/domain overlap 去自动匹配 pending candidate 和新 shared skill。

所以这不是“pending 没贡献”，而是“pending 的贡献没有被记录，也没有驱动 promotion”。正确修复应该是：

1. macro refactor 输入中显式列出与 clique segments 相关的 pending candidates。
2. refactor 输出必须为每个相关 pending candidate 选择 `promote / supersede_by_shared / refine / discard / unrelated`。
3. 代码侧做 deterministic fallback：如果 shared skill 的 source segments 与 pending skill 的 source segments 高度重合，即使 LLM 没填 affected update，也要记录 lineage，并至少把 pending 标成 superseded，而不是静默丢失。
4. promotion 不应只依赖名字字段；还应依赖 source segment overlap 和 evidence equivalence。

因此这个点需要作为 bug 修：pending-to-refactor lineage 现在不完整，导致 pending skill 在统计上看起来没有进入 active。
```

## 3. 为什么 pending 没有贡献到 active？

根因有三层：

1. Pending candidate 已进入 overlap state，但 retrieval 被禁用。

   它们能被 macro/refactor 看见，但不会被 executor prompt 使用。

2. Refactor prompt/决策倾向于生成新的 shared skill。

   当前 committed payload 多为：

   ```json
   {
     "decision": {"action": "extract_shared", "...": "..."},
     "shared_skill": {"name": "..."},
     "affected_skill_updates": []
   }
   ```

   这会创建 active skill，但不会 promotion 任何 pending skill。

3. 新 active skill 的 scope/retrieval 太宽，被 credit filter 逐步禁用。

   例如 `vehicle_engine_start_brake_requirement` 在车辆任务上有 helpful evidence，但也被注入到 travel/booking 任务，产生大量 harmful `domain_mismatch` credit，最终 harmful_count 超过 helpful_count。

```
comment:
现在的skill injector不会管这个嘛？为什么会被注入到其他domain？injector的实现是否还有待改进？
我觉得应该宽松一些，对于产生过帮助的skill，比如Positive credit超过一定值（2？），无论是否有harm都应该保留，如果对其他skill产生harm，可以做refine、反馈给extractor和skill injector（如果开启TRL）
```

```
回复：
injector 理论上应该管，但当前实现显然不够强。`tradingbot_direct_ticker_binding` 被注入到 VehicleControlAPI task，说明至少有一个环节没有把 domain/tool scope 当成硬约束：

- retrieval 可能先把跨 domain skill 召回了。
- injector 对召回结果没有执行足够严格的 domain mismatch rejection。
- skill metadata 的 `allowed_tools/domains` 可能只是提示或 rerank 信号，没有作为正式 hard gate。

这需要改。对于 BFCL 这种工具域很清晰的 benchmark，跨 domain 注入默认应该禁止，除非 skill 是明确 benchmark-general/meta skill。否则 TradingBot skill 出现在 VehicleControl task prompt 里，本身就会污染 executor。

关于保留策略，我同意不能因为有 harm 就立刻丢掉所有曾经有帮助的 skill。更合理的是把“skill 是否有价值”和“当前 scope 是否过宽”分开：

- 如果 positive credit >= 2 且有明确 correctness gain，不应直接 disable。
- 如果 harmful credit 主要来自 domain mismatch / retrieval mismatch，优先 refine scope 或加 injector guard。
- 如果 harmful credit 来自 skill 本体逻辑错误，才考虑 disable 或重写。
- 如果 helpful 和 harmful 都高，应该进入 quarantine / scoped-active 状态：只允许在 proven domain、proven intent 下暴露。

因此改进方向是：credit assigner 输出 harm type 后，maintenance 不应只做全局 disable，而应先尝试 `narrow_scope`、`add_non_applicability`、`restrict_retrieval_domain`，并把这些失败案例反馈给 injector / extractor TRL。
```

当前 retrieval/injection 统计显示只有 refactor-created active skill 曾被注入：

| skill | retrieved/injected 次数 |
|---|---:|
| `vehicle_engine_start_brake_requirement` | 59 |
| `tradingbot_direct_ticker_binding` | 20 |
| `contact_customer_support_message_literal_binding` | 20 |
| `avoid_redundant_airport_lookup_for_major_cities` | 18 |
| `navigate_to_tire_shop_after_lookup` | 10 |
| `book_flight_parameter_binding_contract` | 10 |
| `retrieve_invoice_parameter_binding` | 10 |
| `vehicle_engine_start_brake_pedal_prerequisite` | 1 |

这也解释了为什么 credit events 主要围绕 8 个 refactor-created skill，而不是 91 个 pending skill。

## 4. 为什么 refiner 大量返回 keep？

Micro maintenance 统计：

| 字段 | 数量 |
|---|---:|
| micro reports | 109 |
| 有 targets 的 micro | 73 |
| `pre_refine_decisions.keep` | 68 |
| `refine_decisions.keep` | 68 |
| `refine_decisions.strict_failure_kept` | 78 |
| `refine_decisions.disable` | 1 |

Top maintenance targets：

| skill | target 次数 |
|---|---:|
| `vehicle_engine_start_brake_requirement` | 43 |
| `tradingbot_direct_ticker_binding` | 16 |
| `book_flight_parameter_binding_contract` | 9 |
| `navigate_to_tire_shop_after_lookup` | 8 |
| `retrieve_invoice_parameter_binding` | 8 |
| `avoid_redundant_airport_lookup_for_major_cities` | 7 |

当前实现中 micro 有两个阶段：

1. `_run_credit_pre_refine_targets()` 根据 harmful/refine_required credit 先调用 refiner。
2. `_run_bundle_test_and_refine_targets()` 跑 bundle test；如果失败，再调用 refiner。

因此“先根据 credit 直接 refine”已经有一条 pre-refine 路径。但 case study 显示，refiner prompt/上下文传递仍然不够强：即使 harmful credit 已存在，refiner 输出经常说“没有 harmful credit attribution provided”。随后 bundle test 严格失败时，代码把 `keep` 改写成 `strict_failure_kept`，表示“模型选择 keep，但 strict contract gate 没过，不能算成功修复”。

典型输出：

```json
{
  "skill_name": "tradingbot_direct_ticker_binding",
  "action": "strict_failure_kept",
  "reason": "No failed bundle cases, integration failures, or harmful credit attribution provided. Current artifact contract remains valid for direct ticker binding. Override: with-skill strict contract failures remain after bundle testing; do not treat keep as a successful repair.",
  "version_before": 1,
  "version_after": 1,
  "original_action": "keep"
}
```

这不是正确的算法行为。按预期，cross-domain harmful credit 应该让 refiner 优先做 scope narrowing 或 retrieval guard，而不是 keep。

```
comment:
refiner的prompt不对？仔细检查，应该是个bug。有harm的情况refiner不能轻易给keep（当然可能credit给的有问题）
```

```
回复：
同意，这是 bug，不只是 prompt wording 小问题。已有 harmful credit 或 negative bundle suggestion 时，refiner 不能轻易输出 `keep`，更不能说 “No harmful credit attribution provided”。这说明传给 refiner 的 evidence projection、prompt 强约束、或 action gate 至少有一处断了。

应按下面几层修：

1. evidence projection：refiner 输入必须显式包含 harmful credit summary、harm type、失败 task、失败原因、negative bundle contract。不能只给 skill 正面描述和少量 bundle case。
2. prompt contract：如果存在 high-confidence harmful credit，允许的动作应限制为 `narrow_scope / refine / disable / request_credit_review`，默认不允许 `keep`。
3. code gate：即使 LLM 返回 `keep`，如果 unresolved harmful credit 或 strict negative bundle failure 仍存在，代码应把结果转成 `invalid_keep`，并触发 fallback action，而不是只标记 `strict_failure_kept` 后结束。
4. credit sanity：如果 refiner 认为 harmful credit 不成立，必须显式指出是哪条 credit 错、为什么错，并产出 `request_credit_review`，不能无证据忽略。

所以这里应记录为 refiner 链路 bug：harmful evidence 没有被强制进入决策，且 keep 没有被 hard gate 拦住。
```

## 5. Case Study A: `tradingbot_direct_ticker_binding`

当前状态：

```text
status: disabled
source: llm_refactor_overlap
source_tasks: multi_turn_base_107, 120, 135, 142
disabled_reason: credit_assignment_negative_margin
negative_margin: 6
credit: helpful 5, neutral 4, harmful 11
```

Refactor LLM 输出摘要：

```json
{
  "decision": {
    "action": "extract_shared",
    "confidence": 0.85,
    "reason": "All four segments share a common error pattern: calling get_symbol_by_name to resolve a company name to a ticker symbol when the expected behavior is to use the ticker symbol directly..."
  },
  "shared_skill": {
    "name": "tradingbot_direct_ticker_binding",
    "kind": "interface_contract_card",
    "description": "For TradingBot tools that accept a stock symbol parameter, bind well-known company names directly to their canonical ticker symbols without calling get_symbol_by_name"
  },
  "affected_skill_updates": []
}
```

Clique segments：

| segment | class | observed error | expected |
|---|---|---|---|
| `multi_turn_base_120:turn:0` | TradingBot | extra `get_current_time`, extra `get_symbol_by_name(name='Apple')` | `get_stock_info(AAPL)`, `place_order(AAPL, ...)` |
| `multi_turn_base_142:turn:0` | TradingBot | extra `get_symbol_by_name(name='Zeta Corp')` | `add_to_watchlist(stock='ZETA')` |
| `multi_turn_base_135:turn:0` | TradingBot | missing `get_watchlist`, extra `get_symbol_by_name(name='Zeta Corp')` | `add_to_watchlist(ZETA)`, `get_watchlist()` |
| `multi_turn_base_107:turn:0` | TradingBot | extra `get_symbol_by_name(name='Zeta Corp')` | `get_stock_info(ZETA)` |

这个 refactor 本身是有合理信号的：多个 TradingBot task 都显示 expected 里直接用 ticker，而模型多调用了 symbol lookup。

Helpful credit 示例：

```json
{
  "task_id": "multi_turn_base_118",
  "judgment": "helpful",
  "effect_type": "correctness_gain",
  "confidence": 0.85,
  "reason": "The skill was prompt-injected and the trace shows correct direct ticker binding for 'AAPL' in turn 2 without calling get_symbol_by_name..."
}
```

Harmful credit 示例：

```json
{
  "task_id": "multi_turn_base_93",
  "judgment": "harmful",
  "effect_type": "domain_mismatch",
  "confidence": 0.85,
  "reason": "This skill is scoped to TradingBot domain ... but was injected into a VehicleControlAPI task ... The skill's allowed_tools ... are completely irrelevant..."
}
```

Negative bundle suggestion：

```json
{
  "skill_name": "tradingbot_direct_ticker_binding",
  "polarity": "negative",
  "source_task_id": "multi_turn_base_93",
  "focus_turn_indices": [1],
  "required_context_turn_indices": [0],
  "expected_contract": "The skill should NOT be retrieved or injected. The task should complete the expected workflow: fillFuelTank, lockDoors, pressBrakePedal, startEngine, check_tire_pressure without TradingBot prompt pollution"
}
```

Refiner 输出问题：

```json
{
  "skill_name": "tradingbot_direct_ticker_binding",
  "action": "keep",
  "reason": "Current artifact correctly handles direct ticker binding for well-known companies. No failed bundle cases, integration failures, or harmful credit attribution provided..."
}
```

诊断：credit assigner 已经给出明确 `narrow_scope` 方向，但 refiner 没有把它转成修改。后续 strict bundle failure 只把输出标记成 `strict_failure_kept`，没有自动做 scope guard。

## 6. Case Study B: `vehicle_engine_start_brake_pedal_prerequisite`

当前状态：

```text
status: disabled
source: llm_refactor_overlap
source_tasks: multi_turn_base_130, 70, 82, 93
disabled_reason: spurious overlap clustering
credit: helpful 1
```

Refactor LLM 输出摘要：

```json
{
  "decision": {
    "action": "extract_shared",
    "confidence": 0.92,
    "reason": "Three segments ... share identical error patterns: missing pressBrakePedal(pedalPosition=1.0) and extra startEngine(ignitionMode='START')..."
  },
  "shared_skill": {
    "name": "vehicle_engine_start_brake_pedal_prerequisite",
    "description": "Before starting a vehicle engine, the brake pedal must be pressed to pedalPosition=1.0 as a safety prerequisite."
  },
  "affected_skill_updates": []
}
```

但 clique 内混入了一个明显错误 segment：

| segment | class | 问题 |
|---|---|---|
| `multi_turn_base_130:turn:4` | TradingBot | 用户要 `fund_account(amount=5000.0)`，与 VehicleControl 无关 |
| `multi_turn_base_93:turn:1` | VehicleControlAPI | missing `pressBrakePedal`, extra `startEngine` |
| `multi_turn_base_70:turn:0` | VehicleControlAPI | missing `pressBrakePedal`, extra `startEngine` |
| `multi_turn_base_82:turn:2` | VehicleControlAPI | missing `pressBrakePedal`, extra `startEngine` |

Store 中记录的 disable reason：

```text
Artifact was created from spurious overlap clustering. Instance mappings show multi_turn_base_130:turn:4 is unrelated (trading domain), and overlap edges show only weak text similarity (shared_ngrams=['to be']). The three true instances ... share identical error patterns but were incorrectly grouped with an unrelated segment.
```

诊断：这个 case 说明 heterogeneous overlap 图还不够严格。clique 至少应在 tool/domain 层做硬过滤，或要求 LLM 在 `instance_mappings` 中显式把非实例标为 false；否则一个错误 segment 会污染 shared_skill provenance。

## 7. Case Study C: `avoid_redundant_airport_lookup_for_major_cities`

当前状态：

```text
status: disabled
source_tasks: multi_turn_base_152, 156, 165, 177, 190
negative_margin: 6
credit: helpful 1, neutral 10, harmful 7
```

Refactor LLM 输出摘要：

```json
{
  "decision": {
    "action": "extract_shared",
    "confidence": 0.92,
    "reason": "All five segments share a common latent skill: when the user provides city names for flight booking, the agent unnecessarily calls get_nearest_airport_by_city..."
  },
  "shared_skill": {
    "name": "avoid_redundant_airport_lookup_for_major_cities",
    "description": "For flight booking workflows involving major US cities with well-known airport codes, skip get_nearest_airport_by_city calls and use standard airport codes directly"
  }
}
```

这个 skill 的 refactor evidence 混合了两类情况：

- San Francisco -> SFO、Los Angeles -> LAX、Chicago -> ORD：较合理。
- Hong Kong、Tokyo、Oakendale：不是“major US city direct mapping”的同一类，且有多机场/国际城市/nearest airport 语义。

Harmful credit 示例：

```json
{
  "task_id": "multi_turn_base_90",
  "judgment": "harmful",
  "effect_type": "domain_mismatch",
  "confidence": 0.75,
  "reason": "This skill addresses flight booking workflows and airport lookups, which are completely unrelated to the vehicle control and Twitter posting task..."
}
```

诊断：这个 case 同时暴露两个问题：

1. refactor prompt 把“skip lookup”归纳得过宽；
2. retrieval/applicability 没有把 flight/airport skill 限定到 TravelAPI/TicketAPI 语境，导致非 travel task 中被注入，credit negative margin 累积后 disable。

## 8. Case Study D: `retrieve_invoice_parameter_binding`

当前状态：

```text
status: disabled
source_tasks: multi_turn_base_178, 179, 187, 190, 194
negative_margin: 8
credit: harmful 8, neutral 2
```

Refactor LLM 输出摘要：

```json
{
  "decision": {
    "action": "extract_shared",
    "confidence": 0.92,
    "reason": "All five segments show the same systematic error: passing an unexpected 'insurance_id' parameter to retrieve_invoice when the tool schema only accepts 'access_token' and 'booking_id'."
  },
  "shared_skill": {
    "name": "retrieve_invoice_parameter_binding",
    "description": "Correct parameter binding for retrieve_invoice tool: only access_token and booking_id are valid arguments"
  }
}
```

Clique evidence 是强的：

| segment | class | observed error | expected |
|---|---|---|---|
| `multi_turn_base_190:turn:2` | TicketAPI, TravelAPI | unexpected `insurance_id` | `retrieve_invoice(access_token, booking_id='3426812')` |
| `multi_turn_base_187:turn:1` | TicketAPI, TravelAPI | unexpected `insurance_id` | `retrieve_invoice(access_token, booking_id='insurance_12345')` |
| `multi_turn_base_178:turn:1` | TicketAPI, TravelAPI | unexpected `insurance_id` | `retrieve_invoice(access_token, booking_id='flight_001')` |
| `multi_turn_base_194:turn:3` | TravelAPI | unexpected `insurance_id` | `retrieve_invoice(access_token, booking_id='3426812')` |
| `multi_turn_base_179:turn:2` | TravelAPI | unexpected `insurance_id` | `retrieve_invoice(access_token, booking_id='3426812')` |

但后续 harmful credit 主要来自非 booking/file-system task：

```json
{
  "task_id": "multi_turn_base_12",
  "judgment": "harmful",
  "effect_type": "domain_mismatch",
  "confidence": 0.85,
  "reason": "This skill is about retrieve_invoice parameter binding for booking/insurance domain, but the task is about filesystem operations (cd, touch, ls)."
}
```

诊断：这是一个“skill 内容本身大概率正确，但 retrieval/applicability 太宽”的 case。理想行为不是删除 skill，而是把 retrieval guard 收紧到 `retrieve_invoice` / TravelAPI / TicketAPI / invoice intent。当前 credit filter 直接 disable，refiner 没有在 disable 前成功 narrow scope。

## 9. Candidate competition 反馈是否参与了这次 run？

当前 checkpoint 中 10 个 macro window 的：

```text
candidate_group_feedback_rows = 0
candidate_group_decisions = 0
```

因此这次暂停状态下，candidate competition / group-level TRL feedback 没有实际参与 skill 选择或 promotion。即使代码已经支持宏窗口生成 group feedback，这个 run 的输出中没有相关 rows。

## 10. 结论

### 10.1 对用户问题的直接回答

Q1：为什么这么多 pending？

因为 online prior extractor 每个 task 后生成的 skill 都被 `_mark_prior_artifacts_pending()` 和 `store.add_pending()` 设为 pending，默认 retrieval-disabled。它们不是 posterior active skill。当前 91 个 pending 主要是 prior candidates。

Q2：如果 task 有 overlap，refactor 应该发现一批 skill 作为 active，它们是 disabled 了吗？

是的。posterior/refactor 发现并 commit 了 8 个 shared skill，它们都曾作为 active skill 进入 store；但到暂停时 8 个全部 disabled。大部分是 credit filter 因 negative margin 禁用，另有一个因为 spurious overlap clustering 被 refiner/maintenance disable。

Q3：生成的 pending 为什么都没有贡献到 active 中？

因为 promotion 只发生在 committed refactor attempt 明确引用 pending skill 名字时；当前 committed refactor payload 的 `affected_skill_updates` 为空，LLM 选择创建新 shared_skill，而不是 promote pending。所有 macro window 的 `pending_skill_promotions` 都是 0。

Q4：refiner 为什么给 keep？

有两个原因：

1. 大部分 harmful 信号是 retrieval/domain mismatch，正确修复应是 scope narrowing / retrieval guard；但 refiner prompt 当前没有足够强制把这类 credit 转成修改。
2. 具体 case 中 refiner 输出甚至说 “No harmful credit attribution provided”，说明 credit context 虽然进入了 synthetic result/maintenance context，但 prompt 字段解释或显著性不够，模型没有正确读取。之后 bundle strict failure 只把 keep 标成 `strict_failure_kept`，没有自动生成 patch。

### 10.2 当前实现的主要缺口

1. Pending promotion path 太弱：只靠 refactor LLM 自发把 pending name 写入 affected update。
2. Refactor clique 质量不足：domain/tool gating 不够，导致 spurious clique。
3. Active skill retrieval guard 太弱：好 skill 被跨域注入后产生 harmful credit，被整体 disable。
4. Refiner 对 harmful credit 的动作映射不够硬：domain_mismatch 应优先 narrow scope；schema/argument 错误才改 executable contract；spurious provenance 才 disable。
5. Candidate competition 在该 run 中没有产生 feedback rows，因此没有提供 group-level posterior 比较信号。

## 11. 建议修复计划

1. Promotion 改为“refactor commit 对 pending 的软匹配 + LLM 显式确认”：

   - 如果 committed shared_skill 与 pending_skill node 同 clique、同 source evidence、同 allowed_tools/domain，自动生成 promotion candidate。
   - LLM payload 必须输出 `promote_pending_skill_names`，不能只输出 `affected_skill_updates`。
   - promotion report 中记录 matched pending、匹配证据、被拒原因。

2. Refactor clique 前置过滤：

   - clique 必须 tool/domain coherent。
   - 每个 segment 必须有 `is_instance=true/false`，false 不得进入 shared skill source provenance。
   - 如果 function skill，必须输出 executable tool-call sequence，不允许只给文本。

3. Retrieval guard 从 artifact metadata 强制生效：

   - `allowed_tools`、`domains`、`involved_classes`、intent keywords 必须参与 step retrieval hard filter。
   - cross-domain negative credit 不应直接 disable 内容正确的 skill；应先触发 retrieval scope narrowing。

4. Refiner prompt/schema 加硬动作表：

   - `domain_mismatch` -> narrow scope / retrieval guard patch。
   - `workflow_pollution` -> add non-applicability + stricter preconditions。
   - `schema_help` / `argument_mismatch` -> patch exact interface contract。
   - `spurious_overlap` -> disable or split provenance。
   - weak/uncertain -> keep。

5. `strict_failure_kept` 后自动二次处理：

   - 如果 refiner keep 但 strict gate fail，不能只记录；应强制进入 fallback action：
     - scope-only patch；
     - disable until next macro；
     - or mark stale and remove from retrieval.

6. Candidate group feedback 在 macro window 强制审计：

   - 每个 macro 输出 feedback rows count。
   - 如果 0，记录原因：disabled by flag、low usage、no candidate groups、or no qualifying comparisons。
