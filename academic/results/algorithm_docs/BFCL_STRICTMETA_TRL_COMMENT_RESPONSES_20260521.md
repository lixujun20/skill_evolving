# BFCL strictmeta TRL 文档批注逐条回复

日期：2026-05-21

对应被批注文档：

- `academic/results/algorithm_docs/BFCL_STRICTMETA_TRL_ATTEMPT_ANALYSIS_20260521.md`

本文逐条回复原文中的 `comment:`。

## 1. Candidate-group evidence 还可以挖什么？meta-skill 生成有没有要求 CoT？

批注：

> 这部分信息应该有很多可以挖掘的，比如：好坏对比说明skill质量，exposure次数说明skill sell itself能力和相关性，等等。
> 这里提取meta-skill的时候，有要求他输出cot嘛，即要求其仔细分析信号背后的信息，再形成反馈meta skill。

回复：

是的，这部分 evidence 还可以挖得更细。当前 candidate-group row 已经包含这些原始信号：

- `retrieved_count`：说明 skill 是否能被检索系统找到，反映 name/description/metadata 与任务 query 的匹配能力。
- `injected_count`：说明 skill 进入候选后是否能通过 injector/selection，被真正放进 executor prompt，反映 skill 自我“销售能力”和表述清晰度。
- `used_count`：目前 BFCL prompt-only setting 下基本为 0，因为没有 callable skill；这个字段在 callable/tool setting 下更有意义。
- `helpful_count/harmful_count`：credit assigner 判断的因果帮助/伤害。
- `strict_helpful_count/strict_harmful_count`：帮助或伤害是否发生在 strict success/failure 上。
- `official_helpful_count/official_harmful_count`：帮助或伤害是否和 official checker valid/invalid 对齐。
- `*_per_exposure`：用于避免曝光偏差，也就是你说的“看正 credit / 曝光次数”。
- `bundle_passes/bundle_failures`：skill 自己的局部测试是否能过。

目前 meta-skill 生成没有要求模型输出完整 CoT。代码里的 `EXTRACTOR_RULE_UPDATE_SYSTEM` 只要求返回：

```json
{
  "summary": "brief update rationale",
  "rules": [
    {
      "rule_id": "extractor_rule_1",
      "text": "one concise actionable rule",
      "focus": "scope | contract | reuse | anti_pattern | evidence"
    }
  ]
}
```

它确实要求 “ground conclusions in objective exposure_records, credit_records, and bundle_test_records”，但没有强制输出逐组分析。

我建议下一步不要要求长 CoT，而是要求可审计的结构化 rationale。原因是长 CoT 会增加 token、且不一定稳定；但结构化 rationale 可以写进日志和论文 evidence。建议把 role feedback 输出 schema 改成：

```json
{
  "summary": "...",
  "evidence_analysis": [
    {
      "candidate_group_id": "...",
      "signal": "high_retrieval_low_injection | positive_credit_per_exposure | harmful_prompt_influence | zero_exposure",
      "interpretation": "一句话解释该信号说明什么",
      "affected_roles": ["extractor", "refiner"]
    }
  ],
  "rules": [...]
}
```

这样可以显式区分：

- skill quality：正负 credit、bundle pass/fail；
- retrieval relevance：retrieved_count；
- presentation/actionability：injected_count；
- executor compliance：used/called 或 prompt-only 下的 prompt influence；
- stability：per-exposure rate 和 strict/official credit。

## 2. Archive gate 的全貌是什么？

批注：

> 结合代码，给我解释清楚现在的archive gate的全貌，能写到论文里的那种，中文

回复：

当前 candidate-group competition 的 archive/promotion 逻辑可以写成论文里的机制描述：

系统不是对单个 skill 独立打分，而是对同一个 source task 产生的一组 candidate skills 做组内竞争。每个候选的证据来自后续 rollout 中的曝光、注入、使用、credit 和 bundle test。对每个候选计算一个组内分数：

```text
winner_score =
  3 * helpful_count
  + 2 * used_count
  + 1 * injected_count
  + 2 * bundle_passes
  - 3 * harmful_count
  - 2 * bundle_failures
```

每组选择 `winner_score` 最高的候选作为 winner。

Winner 的 promotion gate 是：

```text
promote_winner =
  winner_score >= BFCL_CANDIDATE_PROMOTION_MIN_SCORE   # 默认 4
  and helpful_count >= BFCL_CANDIDATE_PROMOTION_MIN_HELPFUL  # 默认 1
  and helpful_count > harmful_count                    # 默认要求 positive margin
  and strict_harmful_count == 0
```

如果 winner 通过 gate：

- `promotion_state = competition_winner`
- `is_promoted = True`
- 如果原来是 `trial`，状态改成 `active`

如果 winner 没通过：

- `promotion_state = winner_below_promotion_threshold`
- 不 promote，但也不自动 archive，因为 winner 可能只是证据不足。

Loser 的处理是这次修复的重点。所有 loser 默认不是删除，而是进入 backup/suppressed alternative 状态：

```text
promotion_state = competition_loser
competition_lost_to = winner
candidate_group_soft_promotion = true
is_promoted = false
action = marked_loser_backup
```

只有同时满足下面四个条件的 loser 才 archive：

```text
winner_score - loser_score >= 3
and loser_harmful_count > 0
and loser_harmful_count >= loser_helpful_count
and (loser_strict_harmful_count > 0 or loser_net_credit < 0)
```

含义是：

1. 组内 winner 明显优于该 loser；
2. loser 有真实 harmful evidence，不是单纯没曝光；
3. harmful 至少不低于 helpful；
4. harmful 和 strict failure 或净负 credit 有关联。

所以当前 archive gate 的原则是：**不因未成熟、零曝光、中性 evidence 删除候选；只在有明确负证据且组内存在更好替代时归档 loser。**

论文里可以表述为：

> Candidate archival is evidence-gated and group-relative. A losing candidate is archived only when it is dominated by a stronger group winner and has positive harmful evidence tied to strict failure or negative net credit. Neutral or under-exposed losers are retained as backups, preventing premature deletion of potentially useful variants.

## 3. 拼接 role rules 只是给信息，prompt 表述调过吗？

批注：

> 这里也是另一个问题：你的拼接只是给了信息，但是prompt的表述调过嘛？有没有告知它这个rule的含义？

回复：

部分调过，但还不够强。

现在两个层面是分开的：

1. 生成 meta-rules 的 prompt 是 role-specific 的。`ROLE_RULE_UPDATE_SYSTEMS` 里 extractor/refiner/refactorer 的系统提示不同，会告诉 LLM 这些规则分别用于 extraction/refinement/refactoring。

2. 消费 meta-rules 的 prompt 目前只是追加：

```text
Runtime-informed {role_label} rules of thumb:
- [focus] rule text
```

也就是说，生成端知道这些 rule 的 role 含义；但消费端只是把它当作 “rules of thumb” 附在 system prompt 末尾，没有进一步强调优先级、冲突处理、必须自检等。

这确实是弱点。建议下一步把 suffix 从简单列表改成更强的执行说明，例如：

```text
Runtime-informed role rules below are learned from later rollout evidence.
Treat them as soft constraints for this role. Before returning your JSON:
1. Check each proposed artifact/edit/merge against these rules.
2. If a rule does not apply, ignore it.
3. If a rule conflicts with direct task evidence, follow direct task evidence and explain the conflict in rationale.
4. Do not copy rules verbatim into skill body; use them to shape scope, trigger, non-applicability, and argument contracts.
```

这会让 role rules 更像“过程约束”，而不只是背景信息。

## 4. 提取 skill 后，有没有再自检是否满足 meta skill？

批注：

> 提取完的时候，再要求CoT过一下是否满足了这些meta skill？

回复：

当前没有单独的 “post-extraction rule compliance self-check”。Extractor 只在 system prompt 中看到 rules，然后直接输出 artifacts。

我建议加一个轻量的结构化自检字段，而不是要求长 CoT。比如每个 artifact 输出：

```json
{
  "rule_compliance": {
    "checked_rules": ["extractor_rule_1", "extractor_rule_3"],
    "potential_conflicts": [],
    "scope_justification": "一句话说明为什么没有过宽或覆盖用户意图"
  }
}
```

或者在代码侧二次检查：

- 如果 artifact 是 `interface_contract_card` 且 body 出现固定 template/message pattern，触发 reject/refine。
- 如果 artifact 是 `atomic_tool_rule_card` 且名称包含 `skip`/`avoid redundant`，要求 evidence 至少来自多次 exposure 或明确 schema/tool contract。
- 如果 metadata 中出现 `domains=["all"]` 或 allowed_tools 过多，强制 metadata repair。

这里更推荐“结构化自检 + 代码硬约束”。只靠 LLM 自检仍可能失效。

## 5. Refiner 能改 sibling 吗？能看到 exposure/credit 吗？

批注：

> refiner有权限修改一个skill的sibling嘛？以及，refiner能看到这里的信息嘛，比如每个skill的exposure，credit等内容？我觉得加上是合理的，如果没有应该加上。

回复：

当前 refiner 的权限是 skill-local 的。`refine_bfcl_skill_store_llm(...)` 遍历 target artifacts，每次调用 `refine_skill_artifact_llm(artifact, ...)`，返回的是对当前 artifact 的 refine/rollback/disable 等处理。它不能在一次 refiner 调用里直接批量修改 sibling。

它能看到的信息包括：

- 当前 artifact；
- 当前 skill 的 bundle/test result；
- integration failures；
- refinement history；
- dependency/neighborhood summaries；
- `credit_context`。

也就是说，refiner 可以看到传给该 skill 的 recent credit context，但不一定完整看到整个 candidate group 中所有 sibling 的 exposure/credit 对比。Candidate-group 的完整比较主要给了 role-feedback LLM 和 competition gate，而不是每次 skill-local refiner。

你的判断是对的：应当加上 sibling/group context。建议改成：

```text
Refiner input = current artifact + own credit context + sibling candidate group summary
```

其中 sibling candidate group summary 至少包括：

- sibling name / description / body short projection；
- retrieved_count / injected_count / helpful_count / harmful_count；
- strict/official helpful/harmful per exposure；
- winner/loser/promotion_state；
- sibling 中谁是 competition_winner。

这样 refiner 可以做两类更强动作：

1. 如果当前 skill 是 loser，但 sibling winner 已经覆盖其语义，则 disable/archive 或收窄 residual scope。
2. 如果当前 skill 有 harmful，但 sibling 有类似正例，则借鉴 sibling 的 trigger/non-applicability 修正自己。

## 6. Refactorer rule 3 太具体；三个 role 生成 meta skill 的 input 一样吗？有什么 role 区分？

批注：

> 这里的3太具体，不确定有没有用。
> 我想问一下，你给三个role生成meta skill的llm的input是一样的嘛？那生成出来有什么针对role的分别嘛

回复：

同意，refactorer rule 3 过具体：

> Atomic_tool_rule_card with 10 exposures, zero usage, and one harmful judgment...

它更像对某个 case 的诊断，而不是稳定的 refactorer meta-skill。更好的抽象应该是：

```text
Do not refactor or preserve broad shortcut skills whose exposure is high but whose usage/helpful rate is near zero and whose harmful evidence indicates extra-call pollution; first narrow trigger conditions or keep them out of active retrieval.
```

关于三个 role 的 input：

- 当前三个 role-feedback LLM 看到的 runtime evidence 基本同源，都是 mature candidate-group feedback rows。
- 区别主要在 system prompt。extractor/refiner/refactorer 的 `ROLE_RULE_UPDATE_SYSTEMS` 不同，分别要求生成面向 extraction、refinement、refactoring 的规则。

所以它们不是“完全一样的任务”，但确实存在一个问题：**evidence 没有按 role 做充分投影。**

更理想的做法是同一 candidate-group evidence，投影成三份不同输入：

- extractor view：候选为什么被抽出来、name/description/body/kind/scope 是否导致 retrieval/injection/credit 问题。
- refiner view：已有 skill 如何根据 harmful/helpful case 修改 trigger、argument、non-applicability。
- refactorer view：候选之间是否应该 merge、保留 residual、archive loser、保留 runner-up。

现在主要靠不同 system prompt 来区分 role，下一步应该加 role-specific evidence projection。

## 7. Prompt-level applicability check 已经尽力了

批注：

> 这没问题，我们已经do our best了

回复：

同意。对 prompt-only skill 来说，我们已经在 refiner 层面做了合理努力：补 trigger、补 non-applicability、补 current-state check。后面如果还不能保证 exact behavior，问题不应再归咎于 refiner 没写清楚，而应转到 executor/control 层：

- 是否要从 prompt-only 升级成 callable/checklist action；
- 是否要在 executor 每 turn 前做硬过滤；
- 是否要把 skill 改成更短、更明确的 per-turn directive；
- 是否要关掉 LLM injector，减少误选。

## 8. 为什么 BFCL 不是生成可调用 skill？这和 Spreadsheet 很像，原因是什么？

批注：

> 所以我们也不是生成可调用的skill嘛？这和Spreadsheet那边的结果很像。这是为什么？

回复：

是的，当前 BFCL 这批结果也基本不是 callable skill，而是 prompt-only informational/workflow/interface cards。

原因有三层：

1. BFCL task 的核心动作本身已经是外部 API tool calls。Skill 更像“如何正确调用这些 tools 的经验规则”，例如参数绑定、调用顺序、避免多余调用。这类经验天然更容易被表示成 workflow/interface card，而不是一个新函数。

2. 当前 extractor prompt 明确说：

```text
avoid `executable_tool` unless the artifact is actually callable code
```

这会抑制 LLM 生成 executable_tool。对 BFCL，这是保守但合理的，因为错误 callable wrapper 可能绕过工具 schema 或隐藏状态。

3. 当前 executor setting 是 `skill_injection_mode = prompt_only`，结果里 `called_skill_tools = 0`，说明即使 store 里有 skill，也没有作为 tool 暴露给 executor 调用。

这确实和 Spreadsheet 的问题相似：系统学到了经验，但 executor 只是“阅读经验”，没有强制执行经验。

如果要支持 callable，有两个方向：

- 对 BFCL 生成 “macro-action skill”，例如 `skill_start_engine_safely()` 内部调用 `lockDoors/pressBrakePedal/startEngine`。风险是 BFCL expected call 对每一步很敏感，macro-action 必须展开为官方 tools，并且要保留 state 检查。
- 不生成真正 callable code，而生成 “structured execution checklist”，executor 每 turn 必须先输出 skill applicability decision，再调用 tools。这比 arbitrary callable 更安全。

我更建议先做第二种：hard checklist / decision gate，而不是直接让 extractor 写 BFCL callable functions。

## 9. skill 名字太长是不是不被调用的原因？能否用 skill123？

批注：

> 我们的skill名字都这么长，是不是一个不被调用的原因？我们可以改成叫skill123, 让他调用的时候用skill123来调吗？当然具体的原名和内容还是完整展示。

回复：

在当前 BFCL strictmeta run 里，名字长不是“不被调用”的主要原因，因为根本没有 callable skill call path：`called_skill_tools = 0`。executor 不是在写 `vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1(...)` 这种调用，而是读 prompt 中的 skill 内容。

但名字长仍有两个负面影响：

1. prompt 可读性差，executor 不容易引用或显式标记使用。
2. 如果未来打开 callable skill，长名字会显著降低模型正确调用概率。

所以可以做 alias。建议方式：

```text
Skill ID: skill_017
Canonical name: vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1
Description: ...
Body: ...
```

如果是 callable/tool 模式，则 tool name 使用短 alias，例如：

```text
skill_017(...)
```

同时 metadata 保留 canonical name、lineage、candidate_group_id，用于 credit/refine/refactor。这样既不丢 provenance，也降低 executor 调用难度。

对于 prompt-only 模式，也可以把展示格式改成短 ID：

```text
[skill_017] Start engine requires locked doors and pressed brake.
Canonical: vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1
Use when: ...
Do not use when: ...
```

这应当是低风险改进。

## 10. Harmful 是演化必经逻辑，最后会被 filter 掉吗？refiner 还有时机修吗？

批注：

> 这部分harmful是他们自己演化的必经逻辑，最后会被filter掉？
> 反馈偏慢这个确实有可能。当一个skill在真实使用中暴露出问题，refiner还有时机修它嘛？还是只能被filter？

回复：

是的，一部分 harmful 是演化系统的必经探索成本。candidate competition 的意义就是允许多个候选先以低信任方式暴露，观察真实 credit，然后 promote/filter/refine。

当前 harmful 之后有三条路径：

1. Candidate group competition filter：
   - loser 有明确 harmful evidence 且显著输给 winner，会被 archive。
   - neutral loser 只会 backup，不会删。

2. Credit-driven maintenance/refiner：
   - credit assigner 如果输出 `refine_required` 或 maintenance action，后续 micro/macro maintenance 有机会触发 refiner。
   - refiner 可以修改当前 skill 的 body/scope/metadata，或者 disable。

3. Retrieval-level suppression：
   - `promotion_state=competition_loser/revoked/winner_below_promotion_threshold` 的候选在 BFCL adapter 的 supplement/filter 逻辑里会被降权或排除，尤其 heldout 不允许 trial supplement 时影响更小。

所以不是只能 filter；理论上可以 refine。但当前 refiner 的修复时机和上下文还不够强：

- 它主要 skill-local；
- 不一定看到完整 sibling comparison；
- prompt-only harmful 有时 credit evidence 是间接的，refiner 不一定能稳定修；
- 如果 harmful 来得晚，已经影响了若干 rollout。

建议机制上改成：

```text
harmful_count > 0 but skill has helpful_count > 0:
  -> send to refiner with sibling group context

harmful_count > 0 and helpful_count == 0 and net_credit < 0:
  -> disable/archive or remove from retrieval

zero exposure:
  -> backup, not archive
```

也就是区分“可修的 mixed skill”和“应过滤的 negative skill”。

## 11. Injector 要关掉

批注：

> injector要关掉

回复：

同意。strictmeta 这次把 LLM injector 成本和噪声又带回来了。

结果里 maintenance token breakdown 显示：

- `skill_injector`: 419 calls
- `skill_injector` total_tokens: 447358
- `skill_injector` duration_ms: 1817213

这不是 executor prompt injection 的普通成本，而是额外的 LLM-based injector/selector 维护调用。之前 aligned no-TRL deterministic 的 config 也是 `skill_injection_mode = prompt_only`，但 token breakdown 没有 `skill_injector` role；也就是说它是 deterministic prompt injection，不是 LLM injector。

下一步应当：

- 关闭 LLM injector；
- 只 deterministic inject active/promoted skills；
- trial/pending skill 不进入 heldout，或者只在 train 中按严格低风险规则暴露；
- TRL 先只影响 extractor/refiner/refactorer 和 promotion，不让 LLM injector 成为额外变量。

## 12. Metadata enrichment 是 extractor 不听话吗？检索怎么依赖 metadata？是否可简化成只有 tag？

批注：

> 这个意思是extractor不听话？我们的检索现在会依赖这些内容是嘛？具体是怎么依赖的？我以为只要embedding检索和token level overlap就行了。如果这个tag太复杂导致效果不好，是否可以简化成只有tag？

回复：

这不主要是 extractor 不听话，而是 wrapper 在 extractor 输出后又做了 batch-level metadata enrichment。

Extractor prompt 已经要求：

- `metadata.allowed_tools`: only tools whose usage/arguments/order are governed
- `metadata.domains`: exact observed domains only; never use "all"

但 BFCL wrapper 后处理里有这段逻辑：

```python
artifact.metadata["allowed_tools"] = sorted(existing_allowed_tools + relevant_tools)
artifact.metadata["domains"] = sorted(existing_domains + inferred_domains_or_all)
```

这会把当前 result batch 出现过的 tools/domains 合并进每个 skill。于是即使 extractor 输出很窄，wrapper 也可能把 scope 扩宽。

检索确实依赖 metadata，不只是 embedding/token overlap。当前 BFCL retrieval 依赖 metadata 的地方包括：

1. task-level domain filter：
   - `bfcl_skill_task_filter_reason(...)` 会比较 task 的 `involved_classes` 和 skill 的 `metadata.domains`。
   - 如果 domains 里有具体 domain 且不匹配，会过滤。
   - 但 `"all"` 被特殊处理，会让 missing-domain guard 失效，变得更宽。

2. tool overlap rerank：
   - `query_tool_overlap_score(...)` 读取 `metadata.allowed_tools`。
   - 如果 query 里出现 tool name 或 tool token，会加分。
   - allowed_tools 过宽会让不该相关的 skill 获得额外分。

3. intent overlap：
   - `metadata.intent_keywords` 参与 low-trust turn matching 和 rerank。

4. forbid keywords / retrieval guard：
   - `metadata.forbid_keywords`、`metadata.retrieval_guard.excluded_domains` 会影响过滤。

所以 metadata/tag 不是装饰信息，而是检索谓词和 rerank 特征。

我同意可以简化，但不是“只有 tag”这么简单。建议简化成三类字段：

```json
{
  "domains": ["VehicleControlAPI"],
  "governed_tools": ["startEngine", "pressBrakePedal"],
  "intent_tags": ["engine_start", "precondition", "brake"]
}
```

并删除或降级：

- batch-level `allowed_tools` 合并；
- `domains=["all"]` fallback；
- 太长的 free-form metadata。

检索可以主要依赖：

- domain hard filter；
- governed_tools hard/soft match；
- intent_tags token match；
- body/description embedding。

这比现在复杂 metadata 更可控，也更适合论文解释。

## 13. `instance_state_mismatch` 是什么意思？其他实验有吗？

批注：

> 这是什么意思？其他实验有这个错吗？

回复：

`multi_turn:instance_state_mismatch` 是 BFCL 官方 multi-turn checker 返回的错误类型。它不是我们 call-F1 的简单 missing/extra/argument mismatch，而是官方 checker 在模拟多轮 API 状态时发现最终状态和 expected state 不一致。

典型原因包括：

- 多调用了一步会改变状态的 tool，例如多余 `lockDoors`、`fillFuelTank`、`set_navigation`。
- 少调用了一步状态更新 tool。
- 调用顺序导致后续 state 不同。
- 参数看似接近，但状态变更结果不同。
- 多轮中引用了错误的 id/object，导致后续状态错。

其他实验也有这个错，不是 strictmeta 独有：

| run | split | instance_state_mismatch |
|---|---:|---:|
| aligned no-TRL competition deterministic | train | 11 |
| aligned no-TRL competition deterministic | test | 9 |
| maturity-gated TRL deterministic | train | 11 |
| maturity-gated TRL deterministic | test | 10 |
| strictmeta TRL | train | 11 |
| strictmeta TRL | test | 11 |

所以问题一直存在，strictmeta test 里略多。它说明 BFCL 的主要瓶颈是多轮状态精确控制，而不是单纯 schema 参数名。

## 14. 原本 no-TRL 也有 injector 吗？不是关掉了吗？

批注：

> 原本的no-TRL也有injector嘛？我记得让你关掉了

回复：

这里需要区分两个概念：

1. prompt injection / deterministic injection：
   - 把检索到的 skill 文本拼进 executor prompt。
   - 不需要额外 LLM selector。
   - aligned no-TRL deterministic 有这个，因为 `skill_injection_mode = prompt_only`，但它没有 `skill_injector` LLM role。

2. LLM injector：
   - 额外调用一个 LLM 来选择/过滤/包装要注入的 skills。
   - strictmeta 这次有，token breakdown 里出现 `skill_injector: 419 calls`。

你的记忆是对的：我们之前关掉的是 LLM injector，而不是“完全不把 skill 放进 prompt”。no-TRL deterministic 仍会 deterministic prompt-inject skills，但没有额外 `skill_injector` LLM 调用。

当前 strictmeta 又出现 `skill_injector`，说明这次实验配置/路径把 LLM injector 打开了。这是一个需要关掉的变量。

## 15. 总结：我会怎么改下一版

综合这些 comment，我建议下一版不是继续盲跑 50/50，而是先做四个代码修正：

1. 关掉 LLM injector：
   - 保留 deterministic prompt injection。
   - active winners 可注入，trial/pending heldout 不注入。

2. 修 metadata enrichment：
   - 禁止 `domains=["all"]` fallback。
   - 不再把 batch relevant_tools 全量合并进每个 skill 的 allowed_tools。
   - 改成 `governed_tools` / `intent_tags` / exact domains。

3. 给 role feedback 加 structured rationale：
   - 不要求长 CoT。
   - 输出 `evidence_analysis`，显式解释 exposure、credit、per-exposure、bundle signal。

4. 给 refiner 加 sibling group context：
   - 让它看到同组 winner/loser 的 exposure/credit。
   - 对 mixed helpful/harmful skill 优先 refine，对纯负 skill filter/archive。

再跑实验时应先跑小规模 targeted regression set，重点看：

- Vehicle state mismatch 是否下降；
- Travel template/airport skip 是否不再进入 active；
- Trading symbol skill 是否减少 prompt pollution；
- `skill_injector` role 是否彻底消失；
- final skills 是否不再大量出现 `domains=["all"]` 和过宽 tools。

