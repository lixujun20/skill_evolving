# BFCL Group-Refiner TRL 批注逐条回复 - 2026-05-21

本文逐条回复 `BFCL_GROUP_REFINER_TRL_LOG_FAILURE_ANALYSIS_20260521.md` 中的 `comment:`。末尾附带回复 `SKILL_EVOLUTION_STORY_EM_TRL_20260521.md` 中关于 EM/Jensen 的问题。

## Comment 1: credit assigner 是否判断 skill 真正生效

原批注：

```text
credit assigner应该判断每个skill是否被调用过才行，包括规则类的，要判断有没有真正在executor输出中生效。现在没有这个实现嘛？
```

回复：

你的判断是对的。现在实现只做了一半。

代码里 credit assigner 的 prompt 已经明确要求判断 prompt-only 规则类 skill 是否可能生效。例如 `CREDIT_ASSIGNMENT_SYSTEM` 里写了：prompt-only informational/workflow skills 即使 `used_skills` 为空，也要根据 actual trace、call errors、skill scope 和 task context 推断其 helpful/harmful/neutral。这说明 LLM credit assigner 理论上可以判断“规则是否影响了 executor 输出”。

但是结构化统计层面没有真正实现“规则类 skill 是否被采用”的字段。当前 candidate group 的 `used_count` 只看：

```python
metrics["used_skills"] + metrics["called_skill_tools"]
```

BFCL 现在主要是 `prompt_only` injection，不是 callable skill 直接调用，所以 `used_skills/called_skill_tools` 基本为空。因此 `used_count=0` 不代表规则没生效，只代表没有显式调用记录。

所以准确结论是：

- credit assigner prompt 有要求 LLM 做因果判断。
- credit event 里会记录 helpful/harmful/neutral。
- 但系统没有把“规则类 skill 在输出中被实际采纳”单独结构化为 `adopted/effect_matched` 之类字段。
- candidate-group 汇总里的 `used_count` 对 prompt-only 规则类 skill 基本没有意义。

这确实是一个机制缺口。应该补一个结构化字段，例如：

```text
adoption_type = explicit_call | trace_matched_rule | contradicted_rule | no_visible_effect | uncertain
adoption_evidence = matched tool/order/argument fragment
```

对 BFCL 规则类 skill，应该用 expected/actual tool calls 做 pattern match 辅助 LLM 判断，而不是只依赖 `used_count`。

## Comment 2: 不要专门约束 consolidate/shared_subdoc，输出个数设为 2

原批注：

```text
没必要专门约束这类pattern，这只是一次偶然因素。
extractor的输出个数约束是合理的，设置成2吧。既要做硬约束， 也要加入到prompt当中。
```

回复：

同意。之前我把 `shared_subdoc` / `consolidate_*` 作为建议约束写得太具体了，容易把一次现象过拟合成规则。更稳妥的做法是只约束 extractor 的产出预算：

- prompt 中明确：每个 task 最多输出 2 个 skills。
- 代码中做硬约束：解析后最多保留 2 个 candidate artifacts。
- 如果 LLM 输出超过 2 个，优先保留更具体、直接对应当前 trace error、可复用边界清楚的 skill。
- 如果超过长度/格式不合格，可以二次要求压缩；仍失败就截断/丢弃超额项。

这样既能防止 TRL 后的过度生成，又不把某个偶然 pattern 写死。

我后续实现应当改两处：

1. extractor prompt：加入“最多 2 个 skill”的明确约束。
2. extractor parse/ingest：加入 hard cap，不能只靠 prompt。

## Comment 3: TRL feedback 改 prompt 即可

原批注：

```text
好的，试一下吧，改prompt就行
```

回复：

同意。这里我上一版写错了：要改的不是 extractor 的原始 skill 提取 prompt，而是 TRL 的 meta skill / role-rule generator prompt，也就是根据 mature candidate-group evidence 生成“以后 extractor/refactorer 应该怎么做”的那一层 prompt。

代码对应位置是 `academic/skill_repository/llm_maintenance.py` 里的 `EXTRACTOR_RULE_UPDATE_SYSTEM` 和 `ROLE_RULE_UPDATE_SYSTEMS["refactorer"]`，调用链是 `update_role_rules_from_feedback_llm(...)`。它现在会要求模型先写 `analysis`，再输出 strict JSON 里的 `rules`。我们要改的是这里的 role-rule/meta-rule 生成逻辑，而不是 `extract_skills_llm(...)` 的基础 extractor prompt。

建议把 meta skill generator prompt 改成更强调“先解释完整客观信号，再形成一个文本 rule”，而不是让它过早套用固定分数或固定 schema。核心约束应当是：

```text
Interpret the raw objective evidence before writing the rule.
Use all available signals: retrieved, injected, explicitly called, copied/adopted
behavior, helpful/harmful credit, official-valid outcome, strict call errors,
bundle records, and winner/loser differences inside the candidate group.
The final rule should be one concise text instruction for the target role.
Do not emit bookkeeping advice; only emit behavior-changing rules.
```

这里的目标不是直接限制 extractor 产出个数，而是让 meta skill generator 产生更有用的 role feedback。例如它应该学到“不要把 find/point-to tire shop 和 set GPS navigation 合并成同一个触发规则”，而不是泛泛写“be careful with scope”。

## Comment 4: replay buffer 采样、并发 group refiner、为什么 max token

原批注：

```text
每次做replay buffer采样吧，随机采样N个，N=5
可以每个group并发调用refiner，不要用同一个做，各自开一个对话
我其实好奇，他在说什么？你是让他在一个对话里面干了太多任务吧？如果是多轮生成应该不会触发token限制？另一个方面，如果他说的在理，应该是我们的max token设小了？
```

回复：

同意这个修改方向，并且我同意你说的“保险起见不要用 JSON”。当前 group-refiner 的实现确实是在一个对话里处理多个 group，而且输入里包含 deterministic actions、candidate group evidence、exposure records、credit records 等完整信息。window 3 里一次传了 4 个 group，user prompt 接近 18k-19.5k chars。它要求先写 free-form `analysis`，再输出所有 actions 的 strict JSON。

这导致两个问题：

1. 单个对话任务太重：它要比较多个 group、解释每个成员、再生成 actions。
2. 输出 schema 鼓励长 analysis：模型倾向于把每个 group 都解释一遍，最后 JSON 被截断或无法解析。

从 log 看 window 3 三次调用都打满了 `completion_tokens=4096`：

```text
attempt 1: completion_tokens=4096
attempt 2: completion_tokens=4096
attempt 3: completion_tokens=4096
```

这说明至少有两个可能：

- 任务确实太大，模型输出太长。
- `max_tokens=4096` 对当前 schema 不够。

但我不建议只把 max token 调大。因为这会增加成本，而且仍然不能保证 JSON 稳定。更好的做法是按你的建议拆，并把输出格式从 strict JSON 改成类似 extractor 当前“明确分隔块 + 文件式正文”的格式：

- 每次从 replay buffer 随机采样 `N=5` 个 candidate groups。
- 每个 group 单独开一个 group-refiner 对话。
- 多个 group 并发调用。
- 每个 group 的输出用 sentinel 分隔，不用 JSON。
- analysis / action / reason / patch_intent 都用独立 section，解析失败只影响当前 group。

建议格式：

```text
<<GROUP_REFINER_ANALYSIS>>
先用自然语言解释这个 group 的客观证据：retrieved/injected/called、helpful/harmful credit、official/strict、bundle、winner/loser 差异。
<<END_GROUP_REFINER_ANALYSIS>>

<<GROUP_REFINER_ACTIONS>>
=== ACTION START ===
candidate_group_id: ...
skill_name: ...
action: keep | refine | archive | backup
reason:
...
patch_intent:
...
=== ACTION END ===
<<END_GROUP_REFINER_ACTIONS>>
```

如果一个 group 内多个 skill 需要动作，就重复 `=== ACTION START ===` block。parser 只接受四种 action，未知 action 丢弃；`skill_name` 必须匹配现有 skill，否则丢弃。这样可以避免 JSON 转义、代码片段引号、换行和截断导致整个结果不可解析。

这会把 “一个大 JSON 包含多个 group” 改成 “多个小文本 block，每个只负责一个 group”。这样解析失败的影响也局部化，不会让整个 macro window fallback。

## Comment 5: strict-oriented credit 没必要

原批注：

```text
没必要，我觉得效果还可以，只是涨得不多。
```

回复：

同意先不做 strict-oriented credit。你现在把主口径改成 `official_valid + avg_score` 后，我之前强调 strict 的建议优先级应该下降。

按当前主口径看，这次 TRL 的问题不是完全反向，而是：

- `avg_score` 从 `0.7993` 到 `0.8021`，略涨。
- `official_valid` 从 `35/50` 到 `33/50`，略跌。

这更像是“有一点效果但不稳定”，不是“机制完全错误”。所以短期不应把 credit 改成 strict-heavy，否则可能过度惩罚一些能提升 official_valid/avg_score 的 skill。

更合理的优先级是：

1. 先限制 extractor 输出数量为 2，降低技能库噪声。
2. group-refiner 改成 replay buffer 采样 + per-group 并发。
3. prompt 改得更可执行。
4. 观察 official_valid 和 avg_score 是否稳定上涨。

strict-oriented credit 可以先作为备选，不进入下一轮主改动。

## Comment 6: tire-shop 提前调用是否发生，是不是 bug

原批注：

```text
发生过提前嘛？这是bug吧？
```

回复：

发生过，但我需要把“发生原因”说得更精确。代码并不是把未来轮次的 skill 提前注入到了当前轮；当前轮 retrieval 本身是按 `turn_index` 做的。真正的问题有两层：

1. 当前轮检索到的 skill 本身 scope 太宽，在同一轮里把 `find_nearest_tire_shop` 和 `set_navigation` 绑定到一起，导致 same-turn premature action。
2. BFCL executor 的 `messages` 是累积 conversation，某一轮追加进去的 skill context message 会留在后续轮次历史中；所以它不会影响“过去轮次”，但可能影响“后续轮次”。

代码层面：

```python
for turn_index, user_messages in enumerate(task.question):
    turn_prompt_skills = dynamic_prompt_skills_by_turn[turn_index]
    skill_prompt = artifact_store.build_prompt(turn_prompt_skills)
```

这说明每轮起始注入用的是 `dynamic_prompt_skills_by_turn[turn_index]`，不是把所有轮的 skill 都塞进去。

同一轮 step 内动态检索也只 merge 回当前 turn：

```python
observed_skills, observed_audit, observed_query = retrieval_policy.retrieve(
    turn_index=turn_index,
    user_messages=user_messages,
    observation=last_observation,
)
merged_prompt, added_prompt, step_injector_event = await injection_policy.merge_prompt_skills(
    current=dynamic_prompt_skills_by_turn[turn_index],
    retrieved=observed_skills,
    query=observed_query,
    turn_index=turn_index,
)
dynamic_prompt_skills_by_turn[turn_index] = merged_prompt
```

所以“每一轮只注入当前轮 skill”的主体逻辑是存在的。

但是注入方式是把 constraints / runtime retrieval update 追加到同一个 `messages` 列表：

```python
messages.append({"role": msg.get("role", "user"), "content": content})
turn_constraints = _turn_skill_constraints(turn_prompt_skills, task, turn_index)
if turn_constraints:
    messages.append({"role": "user", "content": turn_constraints})
...
messages.append(step_context_msg)
...
model_response = await tool_client.ask(messages=messages, system=system, tools=tools, ...)
```

这里 `messages` 没有按 turn 清理历史，因此 skill context 一旦作为 user message 追加，就会留在后续 LLM call 的上下文里。它不等于“未来轮 skill 提前泄漏”，但确实意味着“过去轮 skill 可能继续影响未来轮”。如果后续轮次不该再受旧 skill 影响，需要把旧 skill context 做成可替换的 turn-local context，或者在 message 内容里强约束 “only for this current turn”，并在新 turn 重建可见上下文。

具体到 tire-shop case，最清楚的是 `multi_turn_base_68` 和 `multi_turn_base_52`。

`multi_turn_base_68`：

- baseline 在 turn 2 调 `check_tire_pressure` + `find_nearest_tire_shop`，turn 3 用户要求 GPS 时再调 `set_navigation`。
- TRL 在 turn 2 就调用了 `set_navigation(destination='456 Oakwood Avenue, Rivermist, 83214')`。
- 结果 turn 3 expected 的 `set_navigation` 变成 missing，turn 4 的 `post_tweet` 也 missing，official_valid 从 true 掉到 false。

`multi_turn_base_52`：

- 用户要求检查 tire pressure，若有问题“point me in the direction of closest tire service station”。
- expected 是 `check_tire_pressure` + `find_nearest_tire_shop`，没有 `set_navigation`。
- TRL 因 tire-shop skill 多调用了 `set_navigation`，official_valid 从 true 掉到 false。

这确实应该视为 bug，至少是 skill scope / turn-stage gating 的 bug。不是 executor 工具本身坏了，也不是“未来轮 skill 提前注入当前轮”，而是当前轮可见的 skill 把两个不同意图合并了：

```text
find/point me to nearest tire shop  -> find_nearest_tire_shop
set GPS / navigate to that shop     -> set_navigation
```

当前 `tire_shop_navigation_requires_shop_lookup_first` 的规则表达太强，会把“找到/指路”升级成“导航”。修复方向：

- skill body/prompt 要区分 “find/point” 和 “navigate/set GPS”。
- retrieval/injection 可以加 turn intent guard。
- 对 `messages` 累积带来的历史 skill 影响做审计：旧 turn 的 constraints 是否还会误导后续 turn。
- credit assigner 应把这种 premature action 标记为 harmful scope evidence。

不过在当前主口径下，这个修复属于具体 bad skill 修复，不一定要上升为全局 strict-oriented credit。

## 附：关于 EM / 两层 Jensen 的问题

原问题概括：

```text
J(S)=E_{q~D,x~p_S(.|q)} U(x)
    =E_q ∫ U(x) ∫ p_S(x,z|q) dz dx
    =E_q ∫ U(x) ∫ p_S(x|z,q)p_S(z) dz dx

p_S(x|q) 难求，但是 p_S(x|z,q) 和 p_S(z) 较好得到。
这里 x 还没采出来，是否可以用两层 Jensen？会不会太松？机器学习中一般怎么处理？
```

回复：

我认为这里不应该强行做两层 Jensen。原因是我们的目标不是最大化 log-likelihood，而是最大化期望 utility：

```text
J(S) = E_{q,z,x}[U(x)]
```

如果直接对 `E[U]` 做 Jensen，通常没有自然的 log-concavity 结构。EM 的经典 Jensen lower bound 依赖的是：

```text
log p(x|S) = log ∫ p(x,z|S) dz
```

即 log 在积分外面，才能引入 posterior `q(z)` 构造 ELBO。我们这里的 `U(x)` 不一定是 log probability，也不一定可微或可归一化，所以“照搬 EM + 两层 Jensen”可能会很松，甚至目标不对。

更合适的机器学习视角是 latent-variable policy optimization / policy gradient / variational policy search，而不是标准 EM。

可以把 skill library `S` 看成控制 latent variable `z` 的分布：

```text
z ~ p_S(z | q)
x ~ p_\theta(x | q, z)
reward = U(x)
J(S)=E[U(x)]
```

一般有几类解法：

1. Monte Carlo policy gradient / REINFORCE：
   - 采样 `z` 和 `x`。
   - 用 `U(x)` 或 advantage 更新 `p_S(z|q)`。
   - 优点是目标直接；缺点是方差大。

2. Cross-Entropy Method / elite selection：
   - 对每个 q 采样多个 z/x。
   - 保留高 reward 的 latent choices。
   - 更新 skill selection / retrieval / promotion，使高 reward z 概率升高。
   - 这和我们现在的 candidate group competition / TRL 更接近。

3. Variational EM for reward-weighted likelihood：
   - 构造一个 reward-weighted posterior：
     ```text
     q(z | q) ∝ p_S(z | q) E_{x~p(x|q,z)}[exp(U(x)/τ)]
     ```
   - E-step 估计哪些 z 在 reward 下更好。
   - M-step 更新 `S` 或 retriever/promoter，让这些 z 更容易被选中。
   - 这不是经典 likelihood EM，而是 reward-weighted regression / control-as-inference。

4. Off-policy evaluation / contextual bandit：
   - 每次 retrieval/injection 的 skill set 是 action。
   - task result 是 reward。
   - 用 logged exposure + reward 做 credit assignment。
   - 这也是我们现在最接近的形式。

所以我建议论文里不要说“我们严格使用 EM lower bound”。更稳妥的表述是：

```text
We use an EM-inspired latent-skill improvement loop.
The latent variable is the selected/retrieved skill evidence z.
Because the downstream utility is non-differentiable and x is sampled from an LLM agent,
we approximate the E-step with sampled trajectories and credit assignment,
and approximate the M-step with skill extraction/refinement/promotion.
```

如果要进一步形式化，可以用 reward-weighted latent variable optimization，而不是两层 Jensen：

```text
maximize_S E_{q,z~p_S(z|q),x~p(x|q,z)}[U(x)]
```

然后把 TRL/candidate group comparison 解释为一种 low-variance approximation：不是估计完整 `p_S(x|q)`，而是在 replay buffer 中比较同源 candidate group 的相对 utility，更新 skill library 和 retrieval exposure。

结论：

- 两层 Jensen 理论上可以写，但大概率会很松，而且不一定对应我们的算法。
- 更合适的理论框架是 reward-weighted EM / control-as-inference / contextual bandit style latent policy optimization。
- 我们当前算法可以叫 EM-inspired，但最好不要声称是严格 EM。
