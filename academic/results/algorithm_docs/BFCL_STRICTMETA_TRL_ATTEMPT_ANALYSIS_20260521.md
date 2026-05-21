# BFCL strictmeta TRL 修改尝试与观察

日期：2026-05-21

相关产物：

- 20/50 结果：`academic/results/bfcl_trl_strictmeta_train20_50_20260520.json`
- 50/50 结果：`academic/results/bfcl_trl_strictmeta_train50_50_20260520.json`
- 50/50 skills：`academic/results/bfcl_trl_strictmeta_train50_50_20260520_skills.json`
- 50/50 log：`academic/results/logs/bfcl_trl_strictmeta_train50_50_20260520.log`
- 主实验记录：`academic/results/algorithm_docs/EXPERIMENT_CHANGELOG_AFTER_FULL_ALGO_20260518.md`

## 1. 上一轮让我做了什么

上一轮的目标不是重新跑一个普通 BFCL skill-evolving 实验，而是在当前已经对齐到 deterministic injector/no-TRL baseline 的框架里，继续修 TRL，让 TRL 更接近论文里“从候选组客观 credit 中学习 role-level meta skill”的设想。

具体改动是：

1. 把 TRL meta-feedback 从 extractor-only 扩展到三个 role：
   - extractor：学习以后应该抽什么、不应该抽什么。
   - refiner：学习如何根据负 credit/正 credit 修已有 skill。
   - refactorer：学习何时合并、保留、归档 candidate variants。

2. 强化 candidate-group evidence：
   - TRL 更新只看 candidate group，而不是孤立单个 skill。
   - 每个 candidate row 带 objective evidence：retrieved/injected/used exposure、helpful/harmful credit、official outcome、bundle/test record。
   - LLM 看到的是尽量原始的比较信息，而不是先被代码过度总结后的单点 judgment。
```
comment:
这部分信息应该有很多可以挖掘的，比如：好坏对比说明skill质量，exposure次数说明skill sell itself能力和相关性，等等。
这里提取meta-skill的时候，有要求他输出cot嘛，即要求其仔细分析信号背后的信息，再形成反馈meta skill。
```

3. 修 loser archive gate：
   - 旧逻辑里 neutral loser 可能因为 `harmful >= helpful` 被 archive，这是 bug。
   - 新逻辑要求有明确 harmful evidence 才 archive loser。
   - neutral loser 改成 backup，避免把还没充分暴露的候选提前删掉。
```
comment:
结合代码，给我解释清楚现在的archive gate的全貌，能写到论文里的那种，中文
```

4. 把 role meta-rules 真正接到各 role prompt：
   - extractor 规则通过 `_extractor_rule_suffix()` 拼进 extractor system prompt。
   - refiner 规则通过 `_refiner_rule_suffix()` 拼进 refiner system prompt。
   - refactorer 规则通过 `_refactorer_rule_suffix()` 拼进 refactorer system prompt。

```
comment:
这里也是另一个问题：你的拼接只是给了信息，但是prompt的表述调过嘛？有没有告知它这个rule的含义？
```

5. 做了验证和实验：
   - 代码级验证：`py_compile`、相关 pytest、smoke run。
   - 小实验：strictmeta smoke/20/50。
   - 完整实验：strictmeta 50/50。
   - 一个错误启动的 50/50 misrun 已保存到 `academic/results/backup_misrun_20260520/`，不作为主结果。

## 2. 最终结果为什么“不理想”

主要比较对象如下：

| run | split | strict | official_valid | avg_score | recall | precision | avg_total_tokens | avg_elapsed_s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| aligned no-TRL competition deterministic | test 50 | 0.12 | 0.74 | 0.7901 | 0.8938 | 0.7235 | 81663.5 | 35.683 |
| maturity-gated TRL deterministic | test 50 | 0.08 | 0.74 | 0.7984 | 0.9024 | 0.7266 | 80774.6 | 34.210 |
| strictmeta TRL | test 50 | 0.10 | 0.66 | 0.7965 | 0.8824 | 0.7423 | 74365.6 | 48.147 |

结论：

- strictmeta TRL 相比 maturity-gated TRL，strict 从 `0.08` 到 `0.10`，有一点恢复。
- 但它仍低于 no-TRL deterministic 的 strict `0.12`。
- 更重要的是 official_valid 从 maturity-gated/no-TRL 的 `0.74` 掉到 `0.66`。
- avg_score `0.7965` 仍接近 maturity-gated TRL 的 `0.7984`，说明不是完全坏掉，而是 exact/official behavior 不稳定。
- 测试平均耗时从 no-TRL `35.683s`、maturity TRL `34.210s` 上升到 strictmeta TRL `48.147s`。

```
comment:
给我解释一下official valid和avg_score都是什么意思？
```

所以这次修改不是“完全没效果”，而是：

1. role-level TRL 确实学到了合理规则；
2. loser archive bug 确实被修掉；
3. 但最终 executor 侧还是 prompt-only skill 影响，不能稳定把 partial improvement 转成 BFCL strict exact success；
4. LLM injector 和更大的 trial skill pool 又引入了筛选成本与检索/注入噪声。

## 3. 这次 TRL 提取了什么 meta skill/rules

这里的 meta skill 不是可被 executor 直接调用的 task skill，而是给 maintenance roles 用的 runtime-informed rules。

50/50 最终每个 role 都有 5 条规则。

### 3.1 Extractor 最终规则

1. 不要抽取会规定固定 message format/content template 的 interface contract，尤其当用户可能已经给了明确 message content；这种 skill 会覆盖用户意图并造成 argument_mismatch。
2. 抽取声称能减少冗余调用的 atomic tool rule 时，要验证逻辑是否和 observed behavior 一致；如果 “skip lookup when code available” 反而导致额外 lookup，说明 precondition 反了或不完整。
3. 如果 candidate 被反复 retrieved 但从未 injected，通常说明它和 baseline reasoning 重叠；应优先抽非显然的 coordination pattern 或 domain-specific sequencing rule。
4. skill name/description 要用 domain-general terminology，让 retrieval query 能命中语义相似任务；不要用过窄 API 名或上下文。
5. 同源任务多个候选中，优先保留有 positive net_credit 且 non-zero injection 的 candidate，而不是 zero-exposure alternative。

这些规则总体是合理的，尤其能识别两类问题：刚性 interface_contract 和反向 skip-lookup rule。
```
comment:
提取完的时候，再要求CoT过一下是否满足了这些meta skill？
```

### 3.2 Refiner 最终规则

1. workflow guardrail 如果既有 helpful workflow_alignment，又有 harmful workflow_pollution，应补充 current-state applicability check，例如 doors 已锁时不要重复 lockDoors。
2. 若一个 candidate zero exposure，但 sibling 有多次 exposure 和正 credit，zero-exposure variant 可能 trigger 太窄，应 broaden trigger。
3. 拒绝会在用户已经给出 exact wording 时仍规定固定 message template/content 的 interface_contract。
4. 对 harmful atomic_tool_rule，要检查 precondition 是否反了或不完整。
5. interface contract 应约束 style/structure，而不是固定 exact template。

这些规则说明 refiner 已经看到“有用 skill 也会因过宽 scope 导致多余调用”的问题，尤其是 Vehicle precondition 类。
```
comment:
refiner有权限修改一个skill的sibling嘛？以及，refiner能看到这里的信息嘛，比如每个skill的exposure，credit等内容？我觉得加上是合理的，如果没有应该加上。

```

### 3.3 Refactorer 最终规则

1. exposure count 差异大于等于 7 时，不要因为 description 相似就 merge candidates。
2. template-style interface_contract 如果产生 argument_mismatch，且 harmful judgments 达到 2+，应 archive。
3. retrieved 10 次、used 0 次、harmful 1 次的 atomic_tool_rule，通常 trigger 太宽。
4. identical-description variants 在 zero-exposure variants 还没累计 5+ exposures 前先保留。
5. 会规定具体 argument values/formats 的 skill，前 5 次 exposure 需要 `official_helpful_per_exposure > 0.3`，否则有 harmful 就说明 binding 太刚性。
```
comment:
这里的3太具体，不确定有没有用。
我想问一下，你给三个role生成meta skill的llm的input是一样的嘛？那生成出来有什么针对role的分别嘛
```

refactorer 的规则方向也是合理的：少 merge、少刚性、保留未成熟 variant。

## 4. 每个 role 如何消化这些 meta-rules

### 4.1 Extractor

实现路径：

- `academic/benchmarks/bfcl/maintenance/adapter.py` 的 `extract_bfcl_skill_artifacts_llm(...)` 接收 `extractor_rules`。
- 它调用 `extract_skill_artifacts_from_results_llm(...)`。
- `academic/skill_repository/llm_maintenance.py` 中 extractor LLM 调用使用：

```python
system=EXTRACT_SYSTEM + _extractor_rule_suffix(extractor_rules)
```

消化方式：

- 每次 extractor 生成新 candidate skill 时，system prompt 末尾会追加 “Runtime-informed extractor rules of thumb”。
- 所以这些 rules 会直接改变后续 skill extraction 的 scope、kind、body、metadata。

观察到的输出变化：

- strictmeta 50/50 产生了 `161` 个 skills。
- candidate-group feedback rows 从 maturity-gated 50/50 的较少 evidence 扩到 strictmeta 的 `15` rows。
- candidate decisions 有 `71` 个：
  - `winner_promoted`: 7
  - `winner_not_promoted`: 8
  - `marked_loser_backup`: 44
  - `archived_loser`: 12
- 说明 extractor 仍然产出大量候选，但新 archive gate 确实避免了 neutral loser 被硬删。

### 4.2 Refiner

实现路径：

- `academic/benchmarks/bfcl/maintenance/adapter.py` 的 `refine_bfcl_skill_store_llm(...)` 接收 `refiner_rules`。
- 它调用 `refine_skill_artifact_llm(...)`。
- `academic/skill_repository/llm_maintenance.py` 中 refiner LLM 调用使用：

```python
system=REFINE_SYSTEM + _refiner_rule_suffix(refiner_rules)
```

消化方式：

- refiner 在处理 failing bundle / integration failures / credit context 时，会看到这些 role rules。
- 它倾向于给 skill 增加 applicability/non-applicability、收紧 current state 条件、拒绝刚性 template。

观察到的输出变化：

- Vehicle skill 被 refine 到包含更明确的 current state 限制。
- 例如 `vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1` 最终 body 里明确写了：
  - 仅当系统状态显示 doors unlocked 或 brake unpressed 时才应用；
  - 不应用于 stop engine；
  - doors/brake 已确认满足时不要重复执行。
- 这是 refiner 消化 meta-rule 后的正向变化。

但问题是：

- 这种 prompt-level applicability check 仍依赖 executor 自觉遵守。
- BFCL strict 对额外/缺失调用很敏感，只要 executor 多做或漏做一步，official_valid/strict 就会掉。

```
comment:
这没问题，我们已经do our best了
```

### 4.3 Refactorer

实现路径：

- `academic/skill_repository/refactor_overlap.py` 的 `llm_refactor_clique(...)` 接收 `refactorer_rules`。
- refactor LLM 调用使用：

```python
system=REFACTOR_SYSTEM + _refactorer_rule_suffix(refactorer_rules)
```

消化方式：

- refactorer 在 overlap clique 上判断是否合并/抽 shared skill 时，会参考这些 rules。
- 这使它更保守：曝光差异大时不 merge，template interface contract 有 harmful 时 archive，zero-exposure variants 先保留。

观察到的输出变化：

- final skills 中 trial/backup 数量很大：
  - status `trial`: 141
  - status `active`: 11
  - status `disabled`: 5
  - status `archived`: 4
- promotion_state：
  - `trial`: 106
  - `competition_loser`: 39
  - `winner_below_promotion_threshold`: 7
  - `competition_winner`: 4
- 这说明 refactorer/promotion path 的保守化生效了，但也带来一个副作用：trial pool 很大，retrieval/injector 需要在很多候选里筛。

### 4.4 Skill injector / Executor

这些角色不直接消费 TRL meta-rules。它们消费的是经过 extractor/refiner/refactorer 改写后的 normal skills。

strictmeta 50/50 的 test 侧 top prompt-injected skills：

- `avoid_redundant_symbol_lookup_for_explicit_names`: injected 10
- `press_brake_before_starting_engine__candidate_r0_t18_s0`: injected 9
- `vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1`: injected 6
- `multi_turn_order_verification_workflow`: injected 6
- `retrieve_invoice_parameter_binding`: injected 6

重要观察：

- `called_skill_tools` 为空。
- 这次是 `prompt_only` skill exposure，不是 callable skill execution。
- 因此 skill 的作用是改变 executor 的上下文和倾向，而不是强制调用某个经过验证的函数。

这解释了为什么 avg_score/partial alignment 可以改善，但 strict exact success 很难稳定提升。
```
comment:
所以我们也不是生成可调用的skill嘛？这和Spreadsheet那边的结果很像。这是为什么？
```

### 4.5 Credit assigner

credit assigner 不消费 role meta-rules；它产出下一轮 TRL 使用的 objective evidence。

这次它发挥了作用：

- Vehicle engine-start candidates 获得正 credit，进入 promotion。
- Travel support/template 和 airport skip lookup candidates 获得 harmful credit，被降权、disable 或 archive。

但 credit 的作用仍有滞后：

- 错 skill 需要先被 retrieved/injected 并产生负例，才能被识别。
- 在 50-task 规模下，某些错误已经影响了若干 train/test 行为。

## 5. 关键窗口 evidence

### Window 1：balance withdrawal 候选无效

Group：`extract:r0:t4:multi_turn_base_121`

三个 `verify_balance_before_withdrawal` variants 总共 retrieved 14 次，但 injected 0、used 0、helpful 0、harmful 0。

TRL 学到：

- 这种 skill 可能太窄、和 baseline reasoning 重叠，或者不够 actionable。
- 后续 extractor/refiner/refactorer 都加强了“zero exposure 不能说明好，只能说明缺少使用机会或触发不对”的规则。

### Window 2/3：Vehicle engine-start 是正例

Group：`extract:r0:t17:multi_turn_base_93`

`vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1` 在后续窗口中表现较强：

- retrieved 6
- injected 4
- helpful 6
- harmful 1
- net +5

TRL 学到：

- Vehicle start 的 lockDoors -> pressBrakePedal -> startEngine 是 non-obvious workflow sequencing。
- 这类 skill 即使 `used_count=0`，也可能通过 prompt influence 改善 call order。
- 但 harmful case 暴露出 scope 问题：如果 doors 已经 locked，就不该重复 lockDoors。

这是本次 TRL 的正面案例。

### Window 4：Travel interface/airport 是反例

Group：`extract:r0:t39:multi_turn_base_165`

失败 candidates：

- `skip_airport_lookup_when_iata_code_available__candidate_r0_t39_s1`
  - retrieved 10
  - injected 1
  - helpful 0
  - harmful 1
  - net -1
- `contact_customer_support_concise_message_contract__candidate_r0_t39_s1`
  - retrieved 3
  - injected 2
  - helpful 0
  - harmful 2
  - net -2
- `contact_customer_support_concise_message_contract__candidate_r0_t39_s0`
  - retrieved 9
  - injected 4
  - helpful 0
  - harmful 3
  - net -3

TRL 学到：

- 不要用固定 template 覆盖用户明确给出的 message content。
- 不要从单个 observed extra lookup 抽出过宽 skip-lookup rule。

这是本次 TRL 的负面纠偏案例。
```
comment:
我们的skill名字都这么长，是不是一个不被调用的原因？我们可以改成叫skill123, 让他调用的时候用skill123来调吗？当然具体的原名和内容还是完整展示。
```

## 6. 为什么流程上最终没有帮助

### 6.1 学到的 meta-rules 多数是负向 guardrail，不是直接制造强正例

最终 rules 很多是：

- 不要抽 template interface contract。
- 不要抽反向 skip lookup。
- 不要过早 merge。
- 不要 archive neutral loser。
- current state 要更严格。

这些能减少灾难性污染，但它们不直接回答 heldout task 的 exact call sequence。BFCL strict 需要的是“下一步该调用哪个 tool、参数是什么、什么时候停止”，而不是只知道哪些 skill 不该抽。

### 6.2 Role feedback 生效太晚

TRL 更新发生在 macro windows 后。

例如 Travel template/airport skip 的问题直到 Window 4 才被明确总结成 role rules。此前相关 candidates 已经进入 store、被 retrieved/injected、产生 harmful influence。

所以 TRL 能纠错，但在 50-task 训练规模上反馈链路仍偏慢。
```
comment:
这部分harmful是他们自己演化的必经逻辑，最后会被filter掉？
反馈偏慢这个确实有可能。当一个skill在真实使用中暴露出问题，refiner还有时机修它嘛？还是只能被filter？
```

### 6.3 Prompt-only skill 无法强制 exact behavior

strictmeta 的 `called_skill_tools` 为空，说明 executor 没有调用可执行 skill；它只是看到了 prompt skill。

这带来两个问题：

1. 有用 skill 可能被 executor 忽略，或者只部分遵守。
2. 有害 skill 也可能通过提示影响 executor 多做一步或换错参数。

测试错误类型也支持这一点：

- `multi_turn:instance_state_mismatch`: 11
- `multi_turn:empty_turn_model_response`: 5
- `multi_turn:execution_response_mismatch`: 1

instance_state_mismatch 是最主要问题，说明 skill 提示没能稳定控制多轮状态更新和精确调用序列。

### 6.4 LLM injector 成本和噪声回来了

strictmeta 50/50 的 maintenance token breakdown：

- 总 maintenance LLM calls：692
- `skill_injector`: 419 calls，447358 tokens，1817213 ms
- `extractor`: 150 calls，1194655 tokens，2268648 ms
- `refiner`: 60 calls，349910 tokens，557820 ms
- 三个 feedback role 各 4 calls，合计 token 不大，但整体 pipeline 被 injector/refiner 放大。
```
comment:
injector要关掉
```

相比 maturity-gated TRL：

- maturity-gated maintenance calls：123
- strictmeta maintenance calls：692

所以这次质量问题不是 meta-feedback LLM 本身太贵，而是 strictmeta 开启了更大的 candidate/refine/injector 生态，导致成本和选择噪声都增加。

### 6.5 Metadata enrichment 仍有检索污染

final skills 中有明显 metadata scope 问题：

- 161 个 skills 里，154 个含 `domains: ["...", "all"]`。
- 133 个 skills 的 `allowed_tools` 数量大于等于 6。
- active Vehicle skills 也含 `domains: ["VehicleControlAPI", "all"]`，并且 allowed_tools 包含很多同 task 出现但不一定由该 skill 管辖的 tools。

对应实现中，BFCL wrapper 会把当前 batch inferred domains/relevant tools 合并进 artifact metadata：

```python
artifact.metadata["allowed_tools"] = list(sorted(set(
    existing_allowed_tools + list(relevant_tools)
)))
artifact.metadata["domains"] = list(sorted(set(
    existing_domains + list(inferred_domains or ["all"])
)))
```

这和 extractor prompt 里的“metadata.allowed_tools 只列 governed tools、metadata.domains 不要 all”目标相冲突。LLM 学到的 scope 控制会被 wrapper 的 batch-level enrichment 稀释，导致 retrieval 面扩大。

这是本次“meta-rule 合理但最终效果不理想”的一个实现层原因。
```
comment:
这个意思是extractor不听话？我们的检索现在会依赖这些内容是嘛？具体是怎么依赖的？我以为只要embedding检索和token level overlap就行了。如果这个tag太复杂导致效果不好，是否可以简化成只有tag？
```

### 6.6 Strictmeta 改变了 skill population

strictmeta final skill pool：

- total skills: 161
- trial: 141
- active: 11
- disabled: 5
- archived: 4

这说明系统变成了“大量 trial + 少量 active + LLM injector 筛选”的 regime。

no-TRL deterministic 的强点是更直接、更少中间筛选；strictmeta 的强点是更安全地保留/比较候选。当前结果表明：保留候选对研究机制是必要的，但 executor 侧如果仍然靠 prompt-only + injector，则 trial pool 太大时会引入额外噪声。

## 7. 产出如何变化

### 7.1 正向变化

1. Vehicle engine-start skills 变得更可用：
   - learned/promoted 了 `vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1`
   - learned/promoted 了 `press_brake_before_starting_engine__candidate_r0_t18_s0`
   - test 中分别 injected 6 和 9 次。

2. 有害 interface/template skills 被识别：
   - `contact_customer_support_concise_message_contract__candidate_r0_t39_s0` 最终 disabled。
   - feedback 明确指出 template 会覆盖用户 message，造成 argument mismatch。

3. neutral loser 不再被误删：
   - 44 个 loser 被 marked backup。
   - 只有 12 个 loser 因明确负证据被 archive。

4. 三个 role 都获得了各自的 meta-rules，而不是 extractor 单点更新。

### 7.2 负向或未解决变化

1. official_valid 降到 `0.66`，低于 no-TRL/maturity 的 `0.74`。
2. strict 只有 `0.10`，仍没超过 no-TRL 的 `0.12`。
3. test 错误中 `instance_state_mismatch` 仍有 11 个，是主要瓶颈。
```
comment:
这是什么意思？其他实验有这个错吗？
```
4. `empty_turn_model_response` 从 no-TRL/maturity 的 2 个上升到 strictmeta 的 5 个，说明更长/更复杂 prompt 或 injector 输出可能让部分多轮响应不稳定。
5. LLM injector 带来 419 次 maintenance calls；训练/维护时间明显增加。
```
comment:
原本的no-TRL也有injector嘛？我记得让你关掉了
```
6. wrapper metadata enrichment 仍把很多 skill scope 扩到 `all` 和过宽 tool sets，削弱了 extractor meta-rule 的约束力。

## 8. Case-level 对比观察

相对 no-TRL deterministic，strictmeta 出现了一些 official_valid regression：

- `multi_turn_base_103`：no-TRL valid，strictmeta invalid；strictmeta 注入 `avoid_redundant_symbol_lookup_for_explicit_names`，错误为 `instance_state_mismatch`。
- `multi_turn_base_137`：no-TRL valid，strictmeta invalid；同样注入 Trading symbol skill，错误为 `instance_state_mismatch`。
- `multi_turn_base_143`：no-TRL valid，strictmeta invalid；注入 `avoid_redundant_symbol_lookup_for_explicit_names` 和 `multi_turn_order_verification_workflow`，错误为 `empty_turn_model_response`。
- `multi_turn_base_53`、`multi_turn_base_88`：no-TRL valid，strictmeta invalid；注入 `press_brake_before_starting_engine__candidate_r0_t18_s0`，错误为 `instance_state_mismatch`。

也有 improvement：

- `multi_turn_base_153`：no-TRL invalid，strictmeta valid；注入 `retrieve_invoice_parameter_binding`。
- `multi_turn_base_29`：no-TRL invalid，strictmeta strict success。

这说明 strictmeta 不是单向退化。它能让某些 invoice/parameter binding case 变好，但同时在 Trading/Vehicle/Social 等多轮状态 case 上引入不稳定。

## 9. 这次尝试的结论

这次 strictmeta TRL 的算法方向是部分成立的：

- candidate-group evidence 能提取出合理 meta-rules；
- 三个 role 的 rulebook 都不是空的；
- harmful loser archive gate 修掉了一个真实 bug；
- Vehicle positive pattern 和 Travel/interface negative pattern 都被 TRL 识别到了。

但最终结果仍不理想，原因不是“TRL 没学到东西”，而是学到的东西没有被稳定转化成 executor exact behavior：

1. meta-rules 多数是质量控制规则，不能直接保证 heldout exact call sequence；
2. prompt-only skill 影响太软；
3. LLM injector 在大 trial pool 上带来筛选噪声和成本；
4. metadata enrichment 把 skill scope 又扩宽，抵消了 extractor/refiner 的 scope 控制；
5. feedback 链路滞后，坏 candidate 需要先造成若干负例才会被总结；
6. BFCL strict 对 extra/missing calls 极敏感，partial score/official alignment 的改善不一定变成 strict success。

因此，这次结果应该被解释为：strictmeta TRL 修复了若干机制问题，但仍没有解决“role-level meta learning 如何稳定提升 executor exactness”这个核心瓶颈。

## 10. 下一步建议

优先级从高到低：

1. 修 metadata enrichment：
   - 不要把 batch `relevant_tools` 全量并入每个 skill 的 `allowed_tools`。
   - 不要 fallback 到 `domains=["all"]`。
   - 如果 LLM 给的 metadata 为空，应宁可保守禁用检索或标记 needs_metadata_repair，而不是扩大到 all。

2. 把 TRL meta-rules 分 domain/task-family：
   - Vehicle 可以偏 workflow precondition sequence。
   - Travel/Trading/Invoice 更需要 argument binding、state id reuse、avoid template override。
   - 不能让一个全局 rulebook 同时支配所有 BFCL domains。

3. 降低 prompt-only 不确定性：
   - 对高 credit、低 scope 的 skill，考虑生成 callable/checklist executor wrapper，至少让关键 exact sequence 可验证。
   - 或在 executor prompt 中把 injected skill 转成更硬的 per-turn decision checklist，而不是普通文本描述。

4. 改 injector：
   - 当前 419 次 injector calls 成本高。
   - 对 deterministic high-confidence active skills 直接注入；LLM injector 只处理冲突候选或 pending/trial supplement。

5. TRL objective 应直接看 strict error conversion：
   - 当前 meta-rules 更多优化“skill 质量”，但 strict 失败主要来自 extra/missing call 和 state mismatch。
   - candidate scoring 应把 `instance_state_mismatch`、`execution_response_mismatch` 转成更具体的 sequence-level negative signal。

6. 做 targeted ablation：
   - strictmeta + metadata conservative enrichment。
   - strictmeta + no LLM injector，只 deterministic inject active winners。
   - strictmeta + domain-specific role feedback。
   - 每个 ablation 先跑 20/50 或固定 regression subset，再决定是否跑 50/50。

