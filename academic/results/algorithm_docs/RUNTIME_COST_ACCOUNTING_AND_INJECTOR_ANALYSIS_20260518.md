# Runtime Cost Accounting, Injector, and Benchmark Choice Analysis

本文档回答 2026-05-18 关于 token/cost 统计、测试阶段 injector、baseline 试错、相关工作口径、BFCL adapter prompt、以及后续 benchmark 选择的几个问题。

## 1. 结论

当前实验的主要问题不是只差一个表格字段，而是统计对象还没有和算法角色完全对齐：

- executor 的 token 目前只记录 `total_tokens` 和 `completion_tokens`，可以反推出 input tokens，但没有 per-step input/output/cache-input 明细，也没有把 skill context、base task prompt、tool schema、runtime retrieval update 分开。
- maintenance roles 已经有 `token_breakdown`，但只按 maintenance role 汇总 prompt/completion/total，没有 cache-input、价格、以及与 executor/injector 的统一成本表。
- BFCL 测试阶段有 deterministic retrieval + injection policy，但没有独立 LLM injector 过滤；Spreadsheet 测试阶段更直接，是 top-k retrieval 后把所有 retrieved skills 放进 system prompt。
- BFCL baseline 本身有大量 runtime 试错，skill 有空间降低错误和提升正确率；Spreadsheet 当前是 single-shot code generation，没有 repair loop，所以 skill 更容易表现为 correctness gain，而不是 token saving。
- 需要改统计口径，并且建议增加轻量 injector 后重跑 held-out test。否则现有 token 表会把“skill context 成本”“执行器自身上下文成本”“维护角色成本”混在一起，不利于论证。

## 2. 建议实现的算法统计口径

建议把每一次 LLM 调用都记录成统一 `cost_event`，无论它来自 executor、injector、extractor、credit assigner、refiner、bundle tester、overlap refactor 还是 TRL。

### 2.1 单次调用字段

每个 `cost_event` 至少包含：

```json
{
  "event_id": "",
  "benchmark": "bfcl_v3 | spreadsheet | ...",
  "task_id": "",
  "phase": "train | test | micro | macro | bundle_replay | analysis",
  "role": "executor | injector | extractor | credit_assigner | refiner | bundle_tester | overlap_refactor | trl",
  "call_index": 0,
  "turn_index": null,
  "step_index": null,
  "model_name": "",
  "llm_config": "",
  "api_style": "anthropic_direct | openai_direct | legacy",
  "input_tokens": 0,
  "cache_input_tokens": 0,
  "output_tokens": 0,
  "total_billed_tokens": 0,
  "input_cost": 0.0,
  "cache_input_cost": 0.0,
  "output_cost": 0.0,
  "total_cost": 0.0,
  "prompt_chars": {
    "system": 0,
    "messages": 0,
    "skill_context": 0,
    "tool_schema": 0,
    "runtime_update": 0
  },
  "retrieval": {
    "candidate_count": 0,
    "selected_count": 0,
    "injected_count": 0,
    "filtered_count": 0
  },
  "latency_ms": 0,
  "success": null,
  "metadata": {}
}
```

这里 `cache_input_tokens` 必须单列。Claude/OpenAI/代理在 cache 计价上可能不同，所以不能继续用 `prompt_tokens + completion_tokens` 当唯一成本。统一公式应是：

```text
cost =
  input_tokens / 1e6 * input_price_per_million
+ cache_input_tokens / 1e6 * cache_input_price_per_million
+ output_tokens / 1e6 * output_price_per_million
```

如果某个 provider 没返回 cache token，就记录为 `0` 并设置 `metadata.cache_usage_available=false`，不要用估算值伪装成 provider usage。

### 2.2 汇总表字段

每个实验输出建议新增：

```json
{
  "cost_accounting": {
    "pricing": {
      "input_per_million": 3.0,
      "cache_input_per_million": 0.3,
      "output_per_million": 15.0,
      "currency": "USD",
      "source": "experiment_config"
    },
    "summary": {
      "n_calls": 0,
      "input_tokens": 0,
      "cache_input_tokens": 0,
      "output_tokens": 0,
      "total_cost": 0.0
    },
    "by_role": {},
    "by_phase": {},
    "by_task": {},
    "events_path": ""
  }
}
```

论文主表建议用以下列：

| Setting | Success / official valid | Avg score | Executor cost | Injector cost | Maintenance cost | Total cost | Executor input | Cache input | Output | Final conversation length |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|

其中：

- **Executor cost**：真正解 held-out task 的 agent 调用。
- **Injector cost**：检索后过滤/压缩 skill 的调用。如果 injector 是 deterministic，则 cost 为 0，但要报告 filtered count。
- **Maintenance cost**：train/evolve 阶段 extractor、credit assigner、refiner、bundle test、refactor、TRL 等成本。
- **Total cost**：如果论文主张 deployment efficiency，held-out 表可以只报 executor+injector；如果主张 end-to-end learning cost，应另报 train amortized cost。

## 3. 现有代码中的统计缺口

### 3.1 BFCL executor

代码位置：

- `academic/benchmarks/bfcl/adapter.py`
- `academic/benchmarks/bfcl/tool_clients.py`

现状：

- `run_bfcl_task()` 最后写入 `trace.total_tokens = tool_client.total_tokens()`，`trace.completion_tokens = tool_client.completion_tokens`。
- `score` 中保存 `total_tokens`、`completion_tokens`、`n_model_steps`、`retrieved_skills`、`prompt_injected_skills`。
- `executor_step` debug event 只有累计 `prompt_tokens_used` / `completion_tokens_used`，不是本 step delta，也没有 cache token。

需要改：

- 在 `ToolApiClient.ask()` 返回中保留本次调用 usage delta：`input_tokens`、`cache_input_tokens`、`output_tokens`。
- 每个 BFCL model step 追加一条 `cost_event(role="executor", turn_index, step_index)`。
- `prompt_chars` 至少拆出 system、messages、skill_context、tool_schema、runtime_update。
- `trace` 和 `metrics` 保留 `input_tokens`、`cache_input_tokens`、`output_tokens`，不要只保留 total/completion。

### 3.2 Maintenance roles

代码位置：

- `academic/skill_repository/llm_maintenance.py`
- `academic/benchmarks/bfcl/related/experiment.py`

现状：

- `_record_maintenance_token_event()` 已经按 role 记录 `prompt_tokens`、`completion_tokens`、`total_tokens`、duration、system/user chars。
- `snapshot_maintenance_token_stats()` 能按 role/phase 汇总。

需要改：

- 字段名改为统一口径：`input_tokens`、`cache_input_tokens`、`output_tokens`。
- 加 pricing config 和 `cost`。
- role 名称要稳定：`extractor`、`credit_assigner`、`bundle_builder`、`bundle_maintainer`、`refiner`、`overlap_refactor`、`trl`。
- 和 executor/injector 使用同一 `CostAccountant`，最后输出一个统一 `cost_accounting`。

### 3.3 Spreadsheet executor

代码位置：

- `academic/benchmarks/spreadsheet/adapter.py`

现状：

- `run_spreadsheet_task()` 直接 `artifact_store.retrieve(query, top_k=top_k_skills)`。
- system prompt 由 `SPREADSHEET_SYSTEM.format(skills=artifact_store.build_prompt(retrieved))` 构造。
- trace 保存 `retrieved_skills`、`total_tokens`、`completion_tokens`。

需要改：

- 加 `input_tokens`、`output_tokens`、`cache_input_tokens`。
- 记录实际注入 skill prompt block 字符数。
- 加 injector 后记录 `candidate_skills`、`injected_skills`、`filtered_skills`、`injector_cost`。

## 4. 测试阶段现在有没有 injector

严格说，目前没有“LLM injector role”。

BFCL 有 deterministic injection policy：

- turn start 先 retrieval；
- `_SkillInjectionPolicy.initial_selection()` 根据 `injection_type` 把 skill 分成 prompt/tool；
- prompt skill 会进入 system prompt；
- step start 如果上一轮 tool error 可触发 error-aware retrieval；
- 新增 prompt skill 会通过 `_step_skill_context_message()` 追加到 messages。

这更像 retrieval + deterministic injector，不是会读 task、候选 skill、budget 后做过滤的 injector。

Spreadsheet 更简单：

- retrieve top-k；
- 全部 top-k skills 直接进入 `SPREADSHEET_SYSTEM`；
- 现有结果里 evolve 每题都 retrieved 5 个 skills；
- trace 中只有 `retrieved_skills`，没有 `prompt_injected_skills`，说明没有单独的注入选择审计。

建议新增 `injector`：

```text
retriever: 从 store 找 top-k candidates
injector: 判断每个 candidate 是否应该注入、注入哪种压缩视图
executor: 只看到 injector 选中的 skill context
```

injector 输入应该很小：

- compact task summary；
- candidate skill compact cards；
- budget；
- allowed tool/domain；
- recent runtime observation，如果有。

injector 输出：

```json
{
  "selected": [
    {
      "skill_name": "",
      "decision": "inject_full | inject_summary | tool_only | skip",
      "reason": "",
      "expected_benefit": "correctness_gain | token_saving | schema_help | workflow_alignment",
      "risk": "low | medium | high",
      "max_chars": 1200
    }
  ],
  "filtered": [
    {"skill_name": "", "reason": "domain_mismatch | scope_mismatch | redundant | low_confidence"}
  ]
}
```

默认策略：

- BFCL top-k 可以保留 2，但 injector 默认最多注入 1 个 full skill，第二个只能 summary，除非属于不同 tool family 且高置信。
- Spreadsheet top-k retrieval 可以保留 5，但 injector 默认最多注入 2 个 skills，并优先 summary/snippet，不直接塞完整 artifact。
- 如果 injector 成本高于节省，使用 deterministic budget injector：按 domain/tool match、score、dedupe 后过滤，不调用 LLM。

## 5. Baseline 有没有很多试错

BFCL 有。当前 50/50 held-out 结果中：

| Metric | BFCL baseline | BFCL evolve |
|---|---:|---:|
| Avg model steps | 9.62 | 10.16 |
| Avg actual tool calls | 6.88 | 7.20 |
| Avg call errors | 3.14 | 2.42 |
| Tasks with any call error | 47 / 50 | 46 / 50 |
| Official valid rate | 0.44 | 0.74 |
| Avg score | 0.731 | 0.799 |

这说明 BFCL baseline 确实存在大量运行时错误、schema 错误、extra/missing calls 和多轮状态错误。skill 在这里的价值主要是减少错误并提升 official validity。但本次结果里 token 没省下来，原因是 prompt-only skill context 太重，并且部分 skill 让 agent 多跑了 step。

Spreadsheet 不一样。当前 Spreadsheet adapter 是 single-shot code generation：

- 一次 LLM 生成 openpyxl code；
- 执行 code；
- verifier 给分；
- 没有运行时 repair loop。

所以 Spreadsheet baseline 没有“多轮试错”。skill 很难通过减少 trial/error 来省 token，更多体现为：给出正确 openpyxl pattern、减少错误代码、提升 score。若要体现 token saving，需要加同等预算下的 repair baseline，例如 baseline 允许 verifier failure 后修复 1-2 轮，然后比较 skill 是否能减少 repair rounds。

## 6. 相关工作的统计口径和 token 问题

相关工作也会遇到同类问题，但很多论文没有把 memory/skill prompt 的 token 成本单独拆出来。

### Reflexion

Reflexion 通过 task feedback 生成 verbal reflection，并把 reflection 放入 episodic memory，在后续 trials 中影响决策。它强调无需参数更新，通过文字反馈提升 sequential decision-making、coding、reasoning 等任务表现。这里 reflection/memory 本质上会占 prompt context，但论文核心主张通常是成功率随 trials 提升，而不是严格的 token/cost accounting。

### ExpeL

ExpeL 自动收集经验，从训练任务中抽取自然语言 insights，在 inference 时 recall extracted insights and past experiences。它和我们的 skill store 很接近：都是非参数、经验驱动、in-context reuse。它的经验同样需要进入 prompt 或检索上下文，因此也有 token 成本；节约主要来自减少用户手写 prompt engineering、减少重复探索、提高成功率。

### Voyager

Voyager 在 Minecraft 中维护 skill library，并通过环境反馈、execution errors 和 self-verification 迭代生成可执行 skill。它的重要差异是 skill 主要是 executable code，复用时可以通过函数调用/代码库降低重复推理，而不是每次把长自然语言经验完整塞进 prompt。这里开销节约更可能来自可执行程序复用和减少探索步数。

### 对我们的启示

我们的论文应该比这些工作更明确地报告 cost：

- skill/memory 是否占 token：占，必须算。
- injector 是否占 token：如果是 LLM injector，也必须算。
- 真正节约来自哪里：减少 failed calls、减少 repair rounds、减少重复探索、用 executable/compact skill 替代长上下文、提高一次成功率。
- 不能只报 final success，不报 skill context 成本。否则 BFCL 当前这种 correctness gain + token overhead 的情况会被掩盖。

## 7. 是否需要改变口径并加 injector 重跑

需要。建议两步走。

### 7.1 先改统计，不改变行为

先实现统一 cost logger，但保持当前 retrieval/injection 行为不变。这样可以回答：

- executor 到底花了多少；
- skill context 占多少；
- maintenance 花了多少；
- 是否有 cache-input；
- BFCL 每个 step 的 input/output 如何增长。

这一步风险低，不改变实验效果。

### 7.2 再加 injector 并重跑 held-out test

第二步增加 injector，重跑 BFCL 50 held-out 和 Spreadsheet 50 held-out。

建议配置：

```text
BFCL:
- retriever top_k = 2
- injector max_full_skills = 1
- max_summary_skills = 1
- skill_context_budget_chars = 1800

Spreadsheet:
- retriever top_k = 5
- injector max_full_skills = 0
- max_summary_or_snippet_skills = 2
- skill_context_budget_chars = 2200
```

对照表：

| Setting | Purpose |
|---|---|
| baseline | no skill |
| evolve-no-injector-filter | 当前行为 |
| evolve-budget-injector | 看是否能保留 accuracy gain 同时降低 token |
| evolve-llm-injector | 看 LLM filter 是否值得它自己的成本 |

如果 LLM injector 每题都调用一次，Spreadsheet 这种单步任务可能不划算；BFCL 如果 injector 能避免后续多步重复 skill context，可能划算。

## 8. 为什么 BFCL 多了这么多 token

不是 BFCL 官方本身突然变长，而是当前 adapter 的 prompt/injection 写法造成的。

### 8.1 Baseline prompt 注入什么

BFCL baseline 没有 artifact_store，因此：

- `retrieved_skills = []`
- `prompt_injected_skills = []`
- `tool_injected_skills = []`
- native prompt_style 下，如果没有 turn skills，`system=""`

也就是说 baseline 主要由 user messages、assistant messages、tool results、tool schemas 组成。它没有 skill prompt。

### 8.2 Evolve prompt 注入什么

当有 retrieved prompt skill 时：

- `_native_skill_system(skill_prompt)` 会构造 system prompt；
- system 里包含 “You may use the following retrieved skill notes...” 和完整 `skill_prompt`；
- `_turn_skill_constraints()` 还会追加一条 user message，把每个 skill 的 body 再列一遍；
- 如果 step-start error-aware retrieval 找到新 skill，`_step_skill_context_message()` 会再追加 runtime skill retrieval update；
- BFCL 是多步工具调用，每一步都会把之前 messages 重新发给模型。

因此成本近似是：

```text
BFCL token overhead
≈ skill system prompt repeatedly billed per model step
+ turn constraint message repeated in history
+ runtime update message repeated in history
+ extra steps caused by changed workflow
```

这解释了为什么 BFCL evolve 的 completion tokens 只多 `+64.6 / task`，但 input tokens 多 `+16,424.9 / task`。

### 8.3 当前结果中的证据

在 BFCL 50 held-out 中：

- baseline avg input tokens：69,299.5；
- evolve avg input tokens：85,724.4；
- delta input：+16,424.9；
- delta output：+64.6；
- prompt delta 占 total delta 约 99.6%；
- retrieved 0 个 skill 的任务平均 token delta 约 -328；
- retrieved 1 个 skill 平均 +15.5k；
- retrieved 2 个 skills 平均 +31.4k。

所以主要不是模型输出更长，而是 skill context 在多轮 adapter 里重复计费。

## 9. 是否有更适合运行时反馈、又不太慢的 benchmark

当前仓库里已经实现且能快速出结果的：

1. **BFCL-v3 multi-turn base**  
   最适合当前主张：多轮工具调用、明确 tool errors、official checker、有 runtime feedback。缺点是 token 成本高，需要 injector 和 compact skill。

2. **SpreadsheetBench-Verified**  
   适合证明 benchmark-agnostic skill extraction/refinement，但当前 single-shot adapter 不利于展示“减少试错”。如果加 1-2 轮 verifier repair loop，会更适合展示 skill evolving 价值。

仓库 registry 里已有但未实现/未接入的候选：

1. **AppWorld**  
   长程 app/API workflow，有状态、有 runtime feedback，和 skill evolving 很匹配。缺点是工程接入成本中高，运行会比 BFCL 慢。

2. **AgentBoard / ALFWorld / WebShop 风格任务**  
   多轮环境反馈强，适合 Reflexion/experience learning 对比；可选小 split 快速跑。但需要新 adapter 和 scorer。

3. **ToolBench / WildToolBench / Agent-Diff**  
   工具使用更贴近真实 API，适合讨论 tool-use skill，但接入和稳定评测成本更高。

短期建议：

- 主实验继续用 BFCL；
- Spreadsheet 加 repair-loop ablation，用来展示 artifact manipulation 场景；
- 若要新增一个快速 runtime-feedback benchmark，优先做小规模 ALFWorld/WebShop-style adapter 或 AppWorld mini split，而不是直接接大型复杂 benchmark。

## 10. 具体实现计划

### Phase A: Cost accounting

1. 新增 `academic/benchmarks/core/cost_accounting.py`。
2. 定义 `CostEvent`、`PricingConfig`、`CostAccountant`。
3. 支持从 env 读价格：
   - `SKILL_EVOLVE_INPUT_PRICE_PER_MTOK`
   - `SKILL_EVOLVE_CACHE_INPUT_PRICE_PER_MTOK`
   - `SKILL_EVOLVE_OUTPUT_PRICE_PER_MTOK`
4. BFCL executor 每 step 记录 cost event。
5. Spreadsheet executor 每 task 记录 cost event。
6. Maintenance `_record_maintenance_token_event()` 接入同一个 accountant。
7. experiment output 增加 `cost_accounting`。

测试：

- mock provider 返回 input/cache/output usage；
- assert cost 分项正确；
- assert BFCL 每 step 有 executor event；
- assert maintenance role 能按 role 汇总；
- assert cache token 缺失时为 0 且标记 unavailable。

### Phase B: Injector

1. 新增 benchmark-agnostic `SkillInjector` interface。
2. 实现 deterministic budget injector：
   - domain/tool match；
   - remove duplicates；
   - budget by chars；
   - max full / max summary。
3. 可选实现 LLM injector role。
4. BFCL `run_bfcl_task()` 在 retrieval 和 injection policy 中间调用 injector。
5. Spreadsheet `run_spreadsheet_task()` 在 `artifact_store.build_prompt()` 前调用 injector。
6. trace 增加：
   - `candidate_skills`
   - `injected_skills`
   - `filtered_skills`
   - `injector_events`
   - `injector_cost`

测试：

- top-5 candidates 中只注入 budget 内 top relevant skills；
- redundant skills 被过滤；
- filtered skill 不进入 prompt；
- injector event 进入 trace；
- LLM injector mock 返回 skip/inject_summary，prompt 确实变短。

### Phase C: Rerun

最小重跑：

```text
BFCL held-out 50:
- baseline no skill
- evolve current no-filter
- evolve budget-injector

Spreadsheet held-out 50:
- baseline
- evolve current top-5
- evolve budget-injector
```

如果 budget injector 明显降 token 且保持 correctness，再跑 LLM injector 小样本 10-20 tasks 判断是否值得。

## 11. 文献参考

- Reflexion: Language Agents with Verbal Reinforcement Learning, arXiv:2303.11366. https://arxiv.org/abs/2303.11366
- ExpeL: LLM Agents Are Experiential Learners, arXiv:2308.10144. https://arxiv.org/abs/2308.10144
- Voyager: An Open-Ended Embodied Agent with Large Language Models, arXiv:2305.16291. https://arxiv.org/abs/2305.16291
- AgentBench: Evaluating LLMs as Agents, arXiv:2308.03688. https://arxiv.org/abs/2308.03688
- AgentBoard: An Analytical Evaluation Board of Multi-turn LLM Agents, NeurIPS Datasets and Benchmarks 2024. https://proceedings.neurips.cc/paper_files/paper/2024/file/877b40688e330a0e2a3fc24084208dfa-Paper-Datasets_and_Benchmarks_Track.pdf

## 12. 追问答复：accuracy/cost 口径、渐进披露、SkillsBench 与 prompt 压缩

### 12.1 Workflow skill 是不是“更多操作换更高 accuracy”

是的，当前 BFCL 结果里一部分 workflow skill 的效果就是：让 agent 做了更多必要步骤，因此 official validity 上升，但 token 也上升。

这不是单纯的坏事。对于多轮工具调用，baseline 有时 token 低，是因为它少做了必要工具调用、提前结束、或者做错后没有继续修复。此时低 token 不是高效率，而是低完成度。当前 BFCL 50 held-out 的分组能说明这个问题：

| Group | n | Baseline tokens | Evolve tokens | Delta tokens | Delta steps | Baseline score | Evolve score | Evolve retrieved skills |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline correct / evolve correct | 19 | 63,691 | 72,420 | +8,729 | -0.05 | 0.83 | 0.83 | 0.74 |
| baseline wrong / evolve correct | 18 | 79,281 | 110,727 | +31,446 | +1.61 | 0.68 | 0.86 | 1.61 |
| baseline correct / evolve wrong | 3 | 75,436 | 67,807 | -7,628 | -2.00 | 0.83 | 0.73 | 1.33 |
| baseline wrong / evolve wrong | 10 | 65,269 | 76,817 | +11,548 | +0.50 | 0.62 | 0.65 | 0.60 |

这里最重要的是第二行：18 道题从 baseline wrong 变成 evolve correct，平均多花 31.4k tokens，平均多 1.61 步。这说明 workflow skill 的收益主要是 correctness gain，不是 token saving。

所以统计不能只报 all-task average token。应该同时报：

1. **All tasks cost**：真实部署总体成本。
2. **Correct-only cost**：只在正确题上比较效率，避免“错得快”显得便宜。
3. **Fixed-case cost**：baseline wrong / evolve correct 的增量成本，说明提升 accuracy 的价格。
4. **Regressed-case cost**：baseline correct / evolve wrong，发现 skill harmful 或过度约束。
5. **Cost-normalized score**：例如 score per dollar 或 valid task per dollar。

当前我们应该诚实表述：BFCL 的现有 skill 提高了 official validity，但还没有证明 token saving；token saving 需要靠 injector、压缩、渐进披露、以及减少 repair/exploration step 来实现。

### 12.2 Skill prompt 是否给了太多无用信息，渐进披露是否更好

是，当前 prompt 给得太重，特别是 BFCL：

- system prompt 注入完整 skill prompt；
- `_turn_skill_constraints()` 又把 skill body 作为 user message 重复给一次；
- step-start 新 retrieval 时 `_step_skill_context_message()` 再追加一次；
- 多轮下这些内容会反复计入 input tokens。

更好的做法是渐进披露：

1. **Stage 0: skill header only**  
   只给 name、one-line description、applicability、allowed tools，成本控制在 200-400 chars。

2. **Stage 1: compact rule**  
   当 injector 判断高相关时，给 exact contract，例如 tool order 或参数名，控制在 600-1000 chars。

3. **Stage 2: full skill body / executable snippet**  
   只有当 task 明确需要、或上一轮出现对应 tool/schema error 时才展开。

4. **Stage 3: runtime repair disclosure**  
   只有 error-aware retrieval 命中时，把和当前 error 相关的片段注入，不再注入完整 skill。

对 BFCL，默认应该只给 Stage 1，而不是 full body。对 Spreadsheet，function/code skill 可以给 executable snippet，但 workflow/knowledge skill 应该只给短 contract。

### 12.3 SkillsBench 能不能测，它的问题是否针对单个 skill，有没有复用和泛化

可以考虑，但它不是当前最直接的主实验替代。

我查到的 SkillsBench 是 2026 年的 skill-use benchmark，目标是评估 LLM 能否用自然语言 skills 解决复杂任务。它的形式是给模型 skills，再在 benchmark tasks 上评估 with-skills vs without-skills。它确实关心 skill 是否带来 performance gain，也在附录讨论 token/cost：with skills 会增加 input tokens，但可能减少 output tokens 和总成本。这点对我们 related work 很有用，因为它承认 skill prompt overhead 是 skill-use 评测的共同问题。

但它和我们的核心设置有差别：

- SkillsBench 更像 **skill use / skill following** benchmark：给定 skill 后看模型能否用。
- 我们是 **skill evolving**：从 runtime feedback 中抽取、归因、维护、筛选、压缩、复用 skill。
- 如果 SkillsBench 的每题主要针对一个预定义 skill，它能测“单 skill 使用能力”，但不一定测“在线抽取后跨任务泛化”。
- 如果它提供 train/test task families 和可复用 skill pools，则可以作为 held-out reuse benchmark；否则只能作为 injector/skill-format ablation。

建议定位：

- 可以把 SkillsBench 放进 related work 和 supplementary ablation；
- 不建议马上替代 BFCL，因为 BFCL 有更强 runtime feedback、tool errors、official checker，更贴合 skill evolving；
- 如果要接入，优先测：同一个 skill 在多个 related tasks 上的泛化、skill compression 后是否保留收益、injector 是否能选中正确 skill。

### 12.4 训练阶段 injector 发挥作用了吗

如果说的是“根据当前 context 对 skill 做筛选，选择相关 skill 进入 executor context”，当前训练阶段发挥的是 deterministic retrieval/injection，而不是独立 injector role。

BFCL train/evolve 中每个 task rollout 会：

- 按 turn 检索相关 skill；
- 根据 injection type 分成 prompt/tool；
- prompt skills 进入 executor context；
- 如果 tool error 触发 step-start retrieval，新增 skill 再进入 context。

但它没有单独做：

- 候选 skill 两两去重；
- 预算内压缩；
- 根据当前 turn 判断 full/summary/skip；
- 估计注入这个 skill 是否值得它的 token cost。

所以目前训练阶段的 injector 没有发挥到算法应有的程度。它只是 retrieval 后的注入规则，不是 context-aware skill selector。

这也是下一步要改的：训练和测试都应该经过同一个 `SkillInjector`，否则 train 时看到的 skill context 分布和 test 时不一致。

### 12.5 为什么 Spreadsheet 和 BFCL 检索设置不一样

原因是任务形态不同，但现在的设置也确实不够统一。

BFCL：

- 多轮工具调用；
- 每步都会重复发送上下文；
- skill prompt 会被重复计费；
- 所以 top-k 默认较小，当前 held-out 平均 retrieved 约 1.06，最多 2。

Spreadsheet：

- single-shot code generation；
- skill prompt 只计费一次；
- openpyxl 任务类型多，检索时倾向 top-5 提供足够 coverage；
- 但实际结果说明 top-5 太宽，50/50 evolve 每题都塞 5 个 skills，平均 skill package 很重。

统一原则应该是：

```text
retriever top-k 可以 benchmark-specific
injector budget 必须 benchmark-agnostic
```

也就是说，Spreadsheet 可以先 retrieve 5 个 candidate，但 injector 只能让 1-2 个短 skill 进入 executor。BFCL 可以 retrieve 2 个 candidate，但 injector 默认只展开最相关的一个。

### 12.6 同期 skill evolving 工作如何处理 skill prompt 成本

这是 related work 里需要重点讲的公平性问题。很多 skill/memory/evolving work 都会遇到同样劣势：with-skill 条件天然多了一段 system prompt / memory prompt / skill prompt。

从公开论文看，常见处理方式有几类：

1. **只报 performance，不细拆 token**  
   Reflexion/ExpeL 这类经验学习工作主要强调 success rate 随经验提升。经验或 reflection 进入 prompt，但 token 成本通常不是主表核心指标。这对我们不够，因为我们的主张包含软件工程复用和效率。

2. **把 skill 当成 external memory/tool，不纳入 executor token 主比较**  
   一些 embodied/code-skill 工作把 skill library 看作外部程序库，强调复用带来的 exploration reduction。这适合 executable skill，但不适合我们当前 prompt-only skill，因为 prompt token 是真实成本。

3. **报告 with-skills 的 token/cost tradeoff**  
   SkillsBench 这类 skill-use benchmark 更接近我们的公平口径：with-skills 会增加 input token，但可能减少 output token 或提高成功率。它提示我们必须把 input/output/cost 分开。

我们的论文应该更进一步：

- 主表报告 all-token cost；
- 附表拆 executor / injector / maintenance；
- 再给 “without skill context adjusted cost” 作为诊断，不作为唯一主指标；
- 对 prompt-only skill 和 executable skill 分开报告，因为两者成本结构不同。

这样可以把 skill prompt overhead 从劣势变成论文贡献点：我们不仅提出 skill evolving，还提出可审计的 skill cost accounting 和 injector budget control。

### 12.7 turn constraint 是什么，system prompt 还能压缩吗

`turn constraint` 是 BFCL adapter 里额外追加的一条 user message，由 `_turn_skill_constraints()` 生成。它大致长这样：

```text
Retrieved constraints for this turn. Treat them as local execution rules when applicable:
- skill_a: <skill body>
- skill_b: <skill body>
If one of these rules clearly applies, satisfy it during the next tool calls instead of exploring with extra calls.
```

它的初衷是让模型在当前 turn 更容易注意到 skill，而不是只把 skill 放在 system prompt 里。但代价是：skill body 被重复注入，且后续每个 model step 都会带着这条 user message。

可以压缩，而且应该压缩：

1. **删除重复注入**  
   如果 system prompt 已经有 skill，就不要再把完整 body 放进 turn constraint。turn constraint 只保留 skill names 和 one-line contract。

2. **system prompt 常量化**  
   把通用 instruction 固定为极短模板，例如：
   ```text
   Use retrieved local skills only when directly applicable. Prefer exact tool schemas and current user intent.
   ```

3. **skill card 压缩**  
   prompt skill 默认只含：
   ```text
   name:
   applies_when:
   do:
   do_not:
   exact_contract:
   ```

4. **tool schema / skill context 分离**  
   如果 provider 支持 prompt caching，tool schema 和 static policy 应该作为 cacheable prefix；dynamic skill context 单独计价和记录。

5. **runtime update 只给 delta**  
   `_step_skill_context_message()` 只给新增 skill 的 short contract，不给完整 prompt。

短期最该改的是：去掉 `_turn_skill_constraints()` 里的 full `skill.body`，改成 compact contract；同时让 injector 控制每个 turn 的 skill context budget。

## 13. 2026-05-18 实现更新：测试阶段 budget injector 与 prompt 压缩

根据上面的诊断，已先在测试/执行路径实现低风险压缩，不默认重跑训练。

### 13.1 已实现代码路径

新增：

- `academic/benchmarks/core/skill_injector.py`
  - `BudgetSkillInjector`
  - `compact_skill_prompt_block()`
  - `SkillInjectionResult`

接入：

- `academic/benchmarks/bfcl/adapter.py`
  - `run_bfcl_task(..., skill_injector_mode, skill_context_budget_chars)`
  - 支持 env/CLI 控制：`BFCL_SKILL_INJECTOR_MODE=compact|budget|summary`
  - 默认 `full`，不改变训练/旧实验行为。
  - `_turn_skill_constraints()` 不再默认注入 full `skill.body`，改成 compact contract；如需旧行为可设 `BFCL_TURN_CONSTRAINT_FULL_BODY=1`。
- `academic/benchmarks/spreadsheet/adapter.py`
  - `run_spreadsheet_task(..., skill_injector_mode, skill_context_budget_chars)`
  - 支持 `SPREADSHEET_SKILL_INJECTOR_MODE=compact|budget|summary`
  - trace/metrics 新增 `prompt_injected_skills`、`filtered_skills`、`injector_events`。
- `academic/benchmarks/core/runner.py`
  - CLI 新增 `--skill-injector-mode`
  - CLI 新增 `--skill-context-budget-chars`
  - aggregate 新增 `utility_per_million_tokens`。

新增测试：

- `academic/benchmarks/tests/test_skill_injector_budget.py`
  - compact prompt 不包含长 evidence/body；
  - redundant skill 被过滤；
  - prompt char budget 生效；
  - aggregate 输出 per-token utility。

已跑测试：

```text
python -m py_compile academic/benchmarks/core/skill_injector.py academic/benchmarks/bfcl/adapter.py academic/benchmarks/spreadsheet/adapter.py academic/benchmarks/core/runner.py
pytest -q academic/benchmarks/tests/test_skill_injector_budget.py \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_turn_skill_constraints_do_not_leak_expected_tools \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_run_bfcl_task_error_feedback_uses_step_start_context_update \
  academic/refactoring_lab/test_skillsbench_fixture.py
```

结果：`7 passed`。

### 13.2 如何重跑测试阶段压缩版

BFCL held-out / trained store test 可用：

```bash
python -m academic.benchmarks.core.runner \
  --benchmark bfcl_v3 \
  --mode baseline \
  --skills <final_skills.json> \
  --skill-injector-mode compact \
  --skill-context-budget-chars 1800 \
  --top-k-skills 2 \
  ...
```

Spreadsheet 可用：

```bash
python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet \
  --mode baseline \
  --skills <final_skills.json> \
  --skill-injector-mode compact \
  --skill-context-budget-chars 2200 \
  --top-k-skills 5 \
  ...
```

推荐先只重跑 held-out test，比较三组：

| Setting | Purpose |
|---|---|
| baseline | no skill |
| evolve full prompt | 旧行为，衡量原始 skill gain/cost |
| evolve compact injector | 新行为，衡量压缩后是否保留 gain 并降 cost |

### 13.3 Per-token utility 口径

`_aggregate()` 现在输出：

```json
"utility_per_million_tokens": {
  "successes_per_million_tokens": 0.0,
  "score_points_per_million_tokens": 0.0,
  "official_valid_per_million_tokens": 0.0,
  "total_tokens": 0
}
```

这个指标不能替代 accuracy，但能回答“每 1M tokens 买到多少成功/分数/official valid”。论文中建议和 all-task token、correct-only token、fixed-case token 一起报告。

### 13.4 Spreadsheet 是否应该做 function skill 或 multi-turn

当前 Spreadsheet adapter 是 single-turn：一次生成 openpyxl code，然后 verifier 评分。因此现在很难体现“skill 减少试错”的优势。

两个可选方向：

1. **Function skill import/call**  
   对 `executable_tool` skill，生成一个本地 Python helper module，让 agent code `import` 并调用。例如把常见 openpyxl 操作封装成函数。这样 skill 不必完整进入 prompt，只需给函数签名和少量说明，成本明显更低。

2. **Verifier repair multi-turn**  
   把 Spreadsheet 改成最多 2-3 轮：generate code -> execute/verify -> 把 stderr/mismatch feedback 给模型修复。这样 baseline 会有真实试错过程，skill 的价值可以体现为减少 repair rounds、降低失败率、或让第一轮更接近正确。

是否“有人把 spreadsheet 当多轮任务做”：Trace2Skill 的 SpreadsheetBench 设置虽然不是交互式用户多轮，但它的轨迹生成、错误分析、patch proposal、skill editor 是多阶段 agentic loop；CoEvoSkills/SkillsBench 的 verifier-driven skill generation 也属于 generate-verify-refine 多轮过程。因此我们可以合理设计 Spreadsheet repair-loop ablation，不违背 benchmark 本身。

### 13.5 SkillsBench 当前探测结果

本机存在完整 SkillsBench 仓库：`/home/lixujun/skillsbench`。仓库有 91 个 `tasks/`、88 个 `tasks-no-skills/`，以及官方/curated skill pool：

- `/home/lixujun/skillsbench/docs/skills-research/official_skills.json`
- `/home/lixujun/skillsbench/docs/skills-research/curated_skills.json`

当前 repo 已有一个 24-task fixture：

- `app/meta_agent/skills/tests/skillsbench_fixture.json`
- `academic/refactoring_lab/skillsbench_benchmark.py`

我尝试运行现有 fixture retrieval benchmark：

```bash
PYTHONPATH=. python -m academic.refactoring_lab.skillsbench_benchmark --max-tasks 5 ...
```

结果：失败在 pgvector/Postgres 连接，说明这条 runner 需要先启动/配置 pgvector DB。

随后跑了一个离线 lexical diagnostic，输出：

- `academic/results/skillsbench_fixture_official_skill_retrieval_20260518.json`

结果：

| Diagnostic | Value |
|---|---:|
| fixture task-derived skill recall@1 | 0.9583 |
| fixture task-derived skill recall@5 | 0.9583 |
| curated skill tag-hit@5 | 0.1250 |
| official skill tag-hit@5 | 0.1667 |
| curated pool size | 201 |
| official pool size | 26 |

解释：

- fixture 里的 task-derived skill 几乎能被对应 task instruction 检索回来，说明这个 fixture 适合测 retrieval/injector。
- official/curated skill pool 对这 24 个任务的 tag-hit 很低，这不代表官方 skill 没用；它只是说明当前离线 lexical diagnostic 不是官方 pass-rate 评测。
- 要做“和官方 skill 对比”的正式实验，需要走 Harbor/SkillsBench 的 with-skills vs without-skills harness，挂载官方 skill folders，并跑 verifier reward。

### 13.6 Related work 文件夹中的密切相关工作

之前回答漏掉了本仓库 `academic/paper/related_work_notes/` 下更密切的同期工作。这里补上与 token/skill prompt 成本最相关的点：

| Work | 与 skill prompt/cost 的关系 | 对我们的启发 |
|---|---|---|
| SkillX | 推理时检索 planning skills，重写 pseudo-plan，再检索 functional/atomic skills，最后还做 LLM self-filter 后注入系统提示。 | 它已经承认不能直接把所有 skill 塞进 prompt；我们的 injector/budget 是必要组件。 |
| D2Skill | 明确用 baseline group vs skill group 的 performance gap 做 utility signal，并用 utility-aware retrieval/pruning。 | 我们应报告 per-token utility，并把 skill utility 放进检索排序与剪枝。 |
| SkillRL | 强调 raw trajectories 冗长有噪，skill distillation 有 10-20x token compression。 | 支持我们把 long trace/body 压成 compact skill card，而不是完整注入。 |
| XSkill | 解题阶段会做 task decomposition retrieval、experience rewrite、skill adaptation，再把 adapted skill 作为 non-prescriptive reference 注入。 | 说明“适配后注入”比“原样注入”更合理；我们的 compact injector 是简化版 adaptation。 |
| Memento-Skills | 使用 skill router 选择相关 Markdown skills，并记录效用、unit-test gate、回滚。 | 说明 router/injector 是核心，不是附属实现细节。 |
| SkillMOO | 把 pass rate、token/dollar cost、runtime 做多目标优化，pruning/substitution 是主要收益来源。 | 直接支持我们的 cost-aware skill selection 和 prompt pruning。 |
| CoEvoSkills | SkillsBench 上生成多文件 skill package，通过 verifier co-evolution 提升 pass rate。 | 更适合用 SkillsBench 做 skill package 质量评测，但其成本需要和 verifier/generator 分开报。 |
| Trace2Skill | 主要把多条 trajectory lessons 合并成 declarative skill directory，避免推理时额外检索模块。 | 它绕开了多 skill retrieval cost，但 skill directory 本身仍会作为 agent context/skill asset 被加载，需要报告 context 成本。 |
| EvoSkill | 失败驱动 create/edit skill folder，只接受 validation 提升的 candidate program。 | 需要验证门控，不能把训练失败 patch 直接放进 test prompt。 |

结论：同期工作普遍面对同一个劣势：skill/memory/context 会增加 prompt。区别在于有些论文不细拆成本，有些把 skill 做成可执行外部资产，有些用 router/filter/adaptation 降低注入噪声。我们的论文应该把“skill prompt overhead 的可审计统计和 budgeted injection”作为方法贡献之一。

### 13.7 当前注入内容中 utility 低的信息

审计结果：

- `SkillArtifact.prompt_block()` 默认注入：`name/kind/injection_type/version_kind/version + description + full body`。
- BFCL 旧 `_turn_skill_constraints()` 又注入 full `skill.body`。
- Spreadsheet 旧路径对 top-k 全部 `artifact_store.build_prompt(retrieved)`，即每个 retrieved skill 都走完整 prompt block。

低 utility 信息主要包括：

- version_kind/version：对 executor 通常没用，保留给 audit 即可；
- provenance/evidence：对 maintenance 有用，对 executor 通常是噪声；
- 长 body 中的解释性段落：应该压成 `applies_when/do/do_not/exact_contract`；
- 重复的 body：system prompt 已有时，turn constraint 不应再重复；
- 多个语义近似 skill 的完整展开：应由 injector 去重或只保留一个 summary。

新 compact block 默认只保留：

```text
name
type / injection
summary
applies_when
tools
do
do_not
```

BFCL 的 turn constraint 进一步去掉 `tools:` 行，以避免把 expected/tool focus 当成额外提示泄漏，同时保留本 turn 的局部 contract。
