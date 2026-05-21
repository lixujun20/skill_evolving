# BFCL strictmeta TRL 第二轮批注回复

日期：2026-05-21

对应上一轮回复文档：

- `academic/results/algorithm_docs/BFCL_STRICTMETA_TRL_COMMENT_RESPONSES_20260521.md`

本文逐条回复用户第二轮 14 条修改建议。这里先记录设计结论和后续应改内容，不在本文中假装已经完成代码实现。

## 1. Meta-skill generator 的 interpretation 应该靠前，不要先让模型填 signal

用户建议：

> interpretation生成顺序应该靠前，否则还是先输出signal，计算capacity是一样的，interp变成自圆其说了；不需要给signal指定范围，可以任意写cot，最后给一个text的rule。确保给他完整信号，提醒可以从有没有检索到、有没有调用过、好坏对比等方面解读。三个role都这样做。

回复：

同意。上一版我建议的 `signal -> interpretation -> rule` 结构仍然太像分类表单。模型可能先选一个 signal label，再围绕 label 自圆其说，不能真正利用完整 evidence。

更好的顺序应该是：

1. 给完整 candidate-group evidence。
2. 明确提示模型先自由分析 evidence。
3. 提醒它可以关注但不限于：
   - 有没有被 retrieved；
   - retrieved 后有没有 injected；
   - injected 后有没有 explicit use/called；
   - helpful/harmful 对比；
   - positive credit / exposure；
   - harmful credit / exposure；
   - strict vs official 的差异；
   - bundle pass/fail；
   - winner/loser 的组内差异；
   - zero exposure 到底是 skill 不相关、名字不好、还是机会不足。
4. 最后只产出简洁的 `text` rule。

新的输出 schema 可以非常简单：

```json
{
  "analysis": "自由文本。先解释 evidence 说明了什么，可以写中间推理，但不要编造没有给出的事实。",
  "rules": [
    {
      "rule_id": "extractor_rule_1",
      "focus": "optional short phrase",
      "text": "最终可执行 rule"
    }
  ]
}
```

也可以进一步简化，甚至不保留 `focus`。关键是：**不要让模型先填受限 signal label；先 interpretation，再 rule。**

三个 role 都应该这样做：

- extractor feedback：从候选质量和可复用性出发，产出 extraction rule。
- refactorer feedback：从组内差异、merge 风险、shared skill 是否过宽出发，产出 refactor rule。
- group-refiner feedback：从同组候选如何修、谁应保留、谁应过滤出发，产出 group-level refine decision。

这里我会修改上一轮建议：不再设计固定 `evidence_analysis.signal` 枚举，而是改成自由 `analysis`。

## 2. Archive gate 过于 heuristic，但现在可继续沿用；它造成过问题吗？

用户建议：

> 我觉得过于heuristic了，但是现在也没时间继续调了，你这种设计造成过问题吗？继续沿用问题也不大

回复：

是的，当前 archive/promotion gate 很 heuristic。它用的是手写分数和阈值：

```text
score = 3*helpful + 2*used + injected + 2*bundle_passes - 3*harmful - 2*bundle_failures
```

然后 winner promote 和 loser archive 又有阈值条件。这不是最优学习算法，只是一个保守的工程 gate。

它确实造成过一个明确问题：旧逻辑把 neutral loser 误 archive。原因是当 `helpful=0, harmful=0` 时，旧条件里的 `harmful >= helpful` 会成立，导致没有负证据的 loser 被删。这次 strictmeta 前已经修掉，当前 archive 必须满足：

```text
member_harmful > 0
and member_harmful >= member_helpful
and (member_strict_harmful > 0 or member_net < 0)
```

修复后，目前没看到同等级的致命 archive bug。当前 gate 的主要风险是：

- promote 可能偏向短期局部 winner；
- helpful/harmful 权重固定；
- injected_count 被当作正分，可能奖励“容易被注入但不一定真有用”的 skill；
- 对 prompt-only skill，`used_count` 基本缺失。

但在现在时间约束下，继续沿用是可以接受的。关键是别再让它误删 neutral/under-exposed candidate。这个已经修了。

后续如果有时间，应该用更论文化的形式替代 heuristic score：比如按 `helpful_per_exposure`、`harmful_per_exposure`、credible interval、minimum exposure maturity 做 promotion/filter，而不是固定加权分。

## 3. Role rules 拼接说明可以加

用户建议：

> 没问题，可以加上这个说明

回复：

同意。应该把上一轮建议的 role-rule 消费说明加到 prompt suffix 中。目标不是让模型盲从规则，而是明确：

- 这些 rule 是 runtime evidence 学来的软约束；
- 直接 task evidence 优先于 meta-rule；
- 输出前要检查 proposed artifact/edit/merge 是否违反 rule；
- 不要把 rule 原文复制进 skill body；
- rule 应影响 scope、trigger、non-applicability、argument contract、merge decision。

建议 suffix：

```text
Runtime-informed role rules below are learned from later rollout evidence.
Treat them as soft constraints for this role.
Before returning JSON:
1. Check each proposed artifact/edit/merge against these rules.
2. If a rule does not apply, ignore it.
3. If a rule conflicts with direct task evidence, follow direct task evidence and explain the conflict briefly.
4. Do not copy rules verbatim into skill body; use them to shape scope, trigger, non-applicability, argument contracts, and merge decisions.
```

这条可以直接实现，风险低。

## 4. 如果第 3 条加了，post self-check 可以不加

用户建议：

> 如果3调过了，4就不用了

回复：

同意。单独再加 post self-check 会增加 prompt 和 schema 复杂度，也可能造成模型重复解释。

当前优先级应是：

1. 强化 role-rule suffix；
2. 让 meta-rule generator 先自由 interpretation 再输出 rule；
3. 暂不加每个 artifact 的 rule_compliance 字段。

如果后续仍发现 extractor 输出明显违反 meta-rule，再考虑用代码硬约束检查，而不是先加 LLM 自检。

## 5. 不给每个 refiner 塞 sibling；改成 macro step 单独 group-refiner

用户建议：

> 再给sibling太多了。如果没有就不要给了。我觉得可以在marco_step单独再设一个refiner，这里给group的完整evidence，就像给meta skill generator的一样信息，然后让refiner可以修改这一组的skill。

回复：

同意。把 sibling context 塞进每个 skill-local refiner 会导致 prompt 太大，也会重复处理同一组 evidence。

更好的设计是在 macro step 增加一个 group-level refiner：

```text
macro step:
  build candidate group feedback rows
  for selected candidate groups:
    call group_refiner_llm(group_full_evidence)
    output actions for multiple skills in this group
```

group-refiner 输入应接近 meta-skill generator 的完整 group evidence：

- group id；
- source task；
- 每个 member 的 name/kind/body/interface/metadata projection；
- exposure records；
- credit records；
- bundle records；
- current promotion/archive status；
- winner/runner-up；
- train/test failures if available。

group-refiner 输出可以是：

```json
{
  "group_id": "...",
  "analysis": "自由分析：组内谁好、谁坏、谁可修、谁应过滤",
  "actions": [
    {
      "skill_name": "...",
      "action": "refine | keep | disable | archive | mark_backup",
      "patch_intent": "如果 refine，说明要改 trigger/body/metadata 的哪部分",
      "reason": "证据依据"
    }
  ]
}
```

这样更符合你的目标：refine/filter 是基于 candidate group 比较，而不是 skill-local 孤立判断。

## 6. 多个 group 的 TRL 应按来源分流：extractor group 给 extractor，refactor group 给 refactorer；不要给 refiner meta skill

用户建议：

> 如果skill lib中有很多group的话，我们要顺序过一遍所有的group吧？需要区分extractor提取的skill group，refactor提取的skill group，还是refiner修改过的skill，分别给不同role反馈。改成：不要对refiner给meta skill了，refactor现在也采样多个skill出来，然后每次TRL的时候，维护一个experience replay buffer，每次从buffer采样一些，根据他们是refactor提取的还是extractor提取的，分别反馈给不同的role。

回复：

同意。这是比“给三个 role 同一批 evidence 生成 meta-rules”更干净的设计。

上一轮 strictmeta 的问题是：三个 role-feedback LLM 基本看同一份 candidate-group evidence，只靠不同 system prompt 区分 role。这样虽然能生成不同规则，但 role credit attribution 不够纯。

新的设计应改成 experience replay buffer：

```text
ExperienceReplayBuffer:
  rows:
    - group_id
    - group_source_role: extractor | refactorer | refiner
    - source_event_type: candidate_samples | refactor_samples | refine_revision
    - members / variants
    - exposure_records
    - credit_records
    - bundle_records
    - lifecycle_actions
```

TRL 更新时：

1. 从 buffer 中采样 extractor-origin groups，反馈给 extractor role，产出 extractor meta-rules。
2. 从 buffer 中采样 refactor-origin groups，反馈给 refactorer role，产出 refactorer meta-rules。
3. 不再给 refiner 生成长期 meta-skill。
4. Refiner 改成 macro group-refiner action：用完整 group evidence 直接修改这一组 skill，而不是维护一个抽象 refiner rulebook。

这样分工更清晰：

- extractor meta skill：改“未来怎么抽 candidate”。
- refactorer meta skill：改“未来怎么合并/拆分/保留 shared skill”。
- group-refiner：直接修“这组已经存在的 skills”。

关于“skill lib 中很多 group 是否顺序过一遍”：

不应该每次全量顺序过所有 group，成本太高。应使用 replay buffer 采样：

- 优先采样最近有新 credit 的 group；
- 优先采样 high harmful / high disagreement / high exposure 的 group；
- 保留少量 random replay，避免遗忘；
- 已稳定 positive 或已 archive 的 group 降低采样概率。

这更像 RL/continual learning 的 replay，而不是每轮全库扫描。

## 7. Prompt-level limitation 已确认

用户建议：

> 嗯

回复：

记录为确认：对于 prompt-only skill，我们在 refiner 里补 scope/current-state check 是合理上限。后续问题应主要从 executor/control/injection 侧解决。

## 8. Callable skill 在 BFCL 先不管，Spreadsheet 继续调

用户建议：

> 在这个榜单上还好，我只是想知道是不是因为我们的提取的skill设计不合理，和任务不相关，或者我们的注入方式有问题，导致了模型不take我们的callable skills。在这里先不管了，在spreadsheet那边我继续调

回复：

同意。BFCL 当前更像 tool-call workflow benchmark，prompt-only workflow/interface skills 是合理形态；这里先不把 callable skill 作为主线。

但这个观察仍然重要：如果某个 benchmark 中确实有 callable skill，而模型不调用，要区分三类原因：

1. skill 设计不合理：函数接口太复杂、名字太长、语义不贴任务。
2. retrieval/injection 不合理：相关 skill 没进入上下文，或只给 signature 不给 body/usage。
3. executor prompt 不合理：没有明确告诉模型该函数已经 import/available，可以直接调用。

这条先不影响 BFCL TRL 主线；Spreadsheet 那边继续单独调 callable/copy-code/alias。

## 9. 注入侧做 alias，并用 mock case 看名字是否影响调用

用户建议：

> 改吧，在注入这一侧做一个alias，允许模型用alias来call skill，看看有没有改进。这里应该做一个mock实验，直接用一个case看这个名字对于是否调用有没有影响

回复：

同意。这个改动适合在 injection/presentation 层做，不应该改 canonical skill name。

设计：

```text
Canonical name:
  vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1

Injected alias:
  skill_017

Prompt display:
  [skill_017] vehicle_engine_start_requires_locked_doors_and_brake
  Purpose: ...
  You may call skill_017(...) if it is exposed as a callable helper.
```

实现原则：

- store 中仍保留 canonical name，用于 credit、lineage、candidate_group、refine/refactor。
- executor prompt/tool schema 中暴露短 alias。
- trace 中同时记录 alias 和 canonical name：

```json
{
  "called_skill_alias": "skill_017",
  "called_skill_name": "vehicle_engine_start_requires_locked_doors_and_brake__candidate_r0_t17_s1"
}
```

mock 实验建议：

同一个 case 跑三种 prompt：

1. 长 canonical name callable。
2. 短 alias callable，body/description 相同。
3. prompt-only，无 callable。

看指标：

- 是否调用 skill；
- 是否调用正确 alias；
- 是否减少 hesitation/重写；
- 是否影响最终 task success；
- token/step 是否下降。

这条可以先在一个可控 mock case 上做，不需要直接上完整 BFCL 50/50。

## 10. Macro 阶段是否已有 refine？archive 是否也在 macro？refine/filter 应按 helpful 区分

用户建议：

> 你是说现在marco阶段本身也有refine？archive的时机也在marco step? 那我觉得marco阶段的refine应该加上完整的group信息，见5. 而且你说的对，如果都在marco阶段，refine和filter应该区分，其实就是看有没有足够的helpful的skill，有的话尽量refine，没有的话直接filter。

回复：

是的，当前系统中 macro/micro maintenance 都可能涉及 skill maintenance；candidate group competition decisions 和 archive/promotion 是在 macro report 过程中产生并写入结果的。Refine 也在 maintenance 流程里发生，但目前 refiner 不是专门的 group-level refiner。

你的建议是正确的：如果 archive/filter 和 refine 都发生在 macro 阶段，就应该把二者统一放在 group evidence 下做决策。

建议的 group-level policy：

```text
For each mature candidate group:
  if exists candidate with sufficient helpful evidence:
    preserve/promote best candidate
    refine mixed candidates if they have useful evidence but scope problems
    archive only clearly harmful dominated alternatives
  else:
    if candidates have harmful evidence:
      filter/archive harmful candidates
    if candidates are zero/neutral exposure:
      keep as backup or suppress from retrieval, but do not archive
```

更具体：

- 有足够 helpful 的 skill：
  - 优先 refine，而不是直接 filter；
  - 因为它说明核心 idea 有价值，只是 trigger/scope/argument 需要修。

- 没有 helpful，且 harmful 明确：
  - 直接 filter/archive；
  - 不值得让 refiner 花 token 修。

- helpful 和 harmful 混合：
  - group-refiner 读取完整 evidence，要求明确收窄 applicability。

- zero exposure：
  - 不 refine，不 archive；
  - 降低检索或保留 backup。

这和第 5 条一致：macro 阶段加 group-refiner，比给每个 skill-local refiner 塞 sibling 更合理。

## 11. 关于 “1”

用户建议：

> 1

回复：

我理解这里是在确认上一轮“优先级第 1 项”：关掉 LLM injector，先保持 deterministic injection。这个方向我同意，并且和第 14 条一致。

如果这里的 “1” 指的是其他选项，需要后续再校正；但从上下文看，最合理解释是：下一版先执行第一个高优先级改动。

## 12. 删除 metadata 后处理；allowed_tools 没必要；这部分和 TRL 无关但先删

用户建议：

> 这个后处理为什么？不是按照extractor说什么就是什么嘛？allowed tool我觉得也似乎没有必要？我理解这个部分和TRL无关？所以改了之后可能影响效果，但还是先删掉，之后ablation的时候重跑。只给

回复：

同意先删或大幅收窄 metadata 后处理。你最后一句 “只给” 没写完，我按上下文理解为：只保留最简单、必要、可解释的标签/字段。

这个后处理的初衷大概是防止 extractor 漏填 metadata，保证 retrieval 至少有 domain/tool hints。但它现在的问题更大：

- 它把 batch 中出现的 tools 合并进每个 skill 的 `allowed_tools`；
- 它可能把 domain fallback 成 `all`；
- 它会覆盖/稀释 extractor 原本更窄的 scope；
- 它让 TRL 学到的 “不要 broad scope” 在入库后被 wrapper 抵消。

这部分严格说和 TRL 算法本体无关，但会影响 TRL 的结果，因为 retrieval/injection 依赖 metadata。如果 metadata 被后处理污染，TRL 的 credit 会评价一个“被 wrapper 改宽后的 skill”，而不是 extractor 原始产物。

下一步建议：

1. 删除 `domains=["all"]` fallback。
2. 不再把 batch `relevant_tools` 全量合并进 `allowed_tools`。
3. 如果 extractor 没填 allowed/governed tools，不自动补全；最多标记：

```json
{
  "metadata_quality": "missing_governed_tools"
}
```

4. 简化字段为：

```json
{
  "domains": ["VehicleControlAPI"],
  "intent_tags": ["engine_start", "brake_precondition"],
  "governed_tools": ["startEngine", "pressBrakePedal"]
}
```

5. retrieval 逐步改用 `governed_tools`，不要把所有 observed tools 当成 skill 管辖范围。

这个改动会影响效果，所以之后 ablation 需要重跑。但考虑现在已经看到 154/161 skills 带 `all`，我认为先删是合理的。

## 13. `instance_state_mismatch` 先不管

用户建议：

> 明白了，那不管了

回复：

记录为确认。`instance_state_mismatch` 是 BFCL 多轮状态核心难点，短期不把它作为独立调试目标。当前只把它作为结果解释指标，优先改 TRL/injection/metadata。

## 14. LLM injector 还是关掉吗？

用户建议：

> OK，还是关掉吗

回复：

是，建议关掉 LLM injector。

保留：

- deterministic retrieval；
- deterministic prompt injection；
- active/promoted skill 注入；
- candidate group competition；
- extractor/refactorer TRL；
- macro group-refiner。

关闭：

- LLM-based skill injector/selector；
- heldout trial supplement；
- pending/trial 的高风险注入。

原因：

- strictmeta 这次 `skill_injector` 有 419 次 LLM calls，成本和噪声都大；
- no-TRL deterministic 的效果更强，说明不是必须靠 LLM injector；
- 当前我们要 debug TRL，本来就应该减少 injector 这个额外变量；
- 后续要证明 TRL 的贡献，应先用 deterministic exposure 保证可控。

结论：下一版 BFCL TRL 主线应关闭 LLM injector，只保留 deterministic prompt injection。

## 下一版综合设计

根据这 14 条，下一版设计应改成：

1. 关闭 LLM injector。
2. 删除/收窄 metadata 后处理，禁用 `domains=["all"]` 和 batch-level allowed_tools 合并。
3. Role-rule suffix 加强说明，但不加 artifact-level self-check。
4. Meta-skill generator 改成先自由 analysis，再输出 rule text；不限制 signal 枚举。
5. 不再给 refiner 维护长期 meta-skill。
6. Macro step 加 group-refiner，输入完整 group evidence，输出对该组 skills 的 refine/filter/keep/archive actions。
7. TRL 维护 experience replay buffer，按 group source role 分流：
   - extractor-origin groups -> extractor feedback；
   - refactor-origin groups -> refactorer feedback；
   - refiner 修改记录 -> 作为 group-refiner 的经验，不生成 refiner meta-rule。
8. Injection 侧支持 short alias，mock 一个 case 检查 alias 对 callable skill 调用率的影响。

最小验证顺序：

1. 单测：metadata 后处理不再生成 `all`，allowed/governed tools 不被 batch 扩宽。
2. 单测：role feedback prompt 先 analysis 后 rules。
3. 单测：experience replay buffer 能按 source role 采样。
4. 单测：group-refiner 能对同组多个 skills 输出 actions。
5. Mock：skill alias 是否提高 callable 调用。
6. 小实验：固定 regression subset 或 20/50。
7. 完整 50/50 ablation。
