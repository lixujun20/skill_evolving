# Refactor Lab 与 Benchmark 开发文档

本文档用于内部开发与回溯。写法按时间顺序组织，保留设计变更、调试过程、实验证据、重要产物路径，以及后续继续排查时需要回看的上下文。

## 1. 文档用途与检索方式

本文档服务于三个目标：

1. 回溯设计为什么改变；
2. 快速定位某一阶段的实验产物、case、skills 与脚本；
3. 让后续开发者知道哪些结论已经成立，哪些只是某次调试观察。

建议检索关键词：

- `refactor`
- `skillsbench_manual`
- `skillsbench_fixture`
- `replay`
- `workflow_reuse_v3_judge`
- `joint_refactor`
- `legacy_fallback`
- `check_triangle_inequality`

## 2. 时间线总览

### 阶段 A：先做 integrated academic full loop

项目最早是从完整 `academic` 主循环出发，希望直接验证 skill evolving 是否能提升后续题目求解。

这一路线的主要问题是：

- 同时混入了 executor 方式、模型、检索、技能抽取、测试、调用决策等多个因素；
- 任何结果变化都很难明确归因；
- 需要额外做大量 regression audit。

这一阶段最重要的归档文档原本是：

- `academic/results/EXPERIMENT_AUDIT.md`
- `academic/results/aime_experiment_report.md`
- `academic/results/aime_v3_exp1_analysis.md`

这些旧文档中的有效信息现已吸收进本文档，后续不再维护原始副本。

这一阶段最值得保留的历史线索有两条：

- 一条是“实验结论如何随着 retrieval、temperature 与 executor 方式变化而变化”；
- 另一条是“为什么后续必须把 refactor 与 history-reuse planning 从 full loop 中拆出来单独研究”。

### 阶段 B：发现 integrated 路径太噪，转向机制拆分

随着 executor 从 early one-shot 走向后来的 TIR / ReAct 风格，系统行为变得更复杂，实验结论也更依赖具体运行配置。此时最重要的判断不是“哪个参数再调一下”，而是：

- 需要先在更干净的环境中证明共享结构是否存在；
- 需要把“是否该复用历史”从 executor 成败中拆出来单独评估。

于是当前阶段拆分成：

- `refactor_lab`
- `replay benchmark`

## 3. integrated full-loop audit：保留哪些有效结论

虽然当前主线不再以 integrated full-loop 为中心，但 audit 中仍有几条重要结论值得保留。

### 3.1 v2 rewrite 让问题归因变难

历史 audit 显示，v2 executor rewrite 同时改动了：

- executor 交互模式；
- token 预算；
- 技能注入方式；
- tool 调用路径；
- resume / sandbox 行为。

因此后续任何 full-loop 性能波动，都很难单独归因到“skill reuse 机制本身”。

这一点直接推动了后续 benchmark 拆分。

### 3.2 full-loop 中最有价值的保留观察

后来在 Claude 的完整 `ds100_aime` rerun 中，最重要的不是顶层 accuracy，而是 skill use behavior：

- train retrieved unique skills: `154`
- train called unique skills: `67`
- test retrieved unique skills: `70`
- test called unique skills: `2`

这表明：

- train 阶段的 skill reuse 真实存在；
- test 阶段 retrieval 也不是零；
- 主要瓶颈是 test-time utilization，而不是“没有 skill”或“根本检索不到 skill”。

代表性 skill：

- 高频 train retrieved / called：
  - `check_triangle_inequality`
- test 中实际被调用的少量 skill：
  - `compute_triangle_area_origin_two_points`
  - `get_chord_data_regular_polygon`

这组现象是后来 replay benchmark 设计的重要背景。

### 3.3 旧 AIME 系列实验中应保留的阶段性结果

在更早的 AIME 系列实验中，有几条虽然不再是当前主证据、但仍然值得保留的阶段性结论：

1. 单轮 TF-IDF + skill evolution 并不稳定优于 baseline。
   - 早期单轮实验中，skills 可能带来 token 节省，但 accuracy 不一定提升。
2. 多轮 TF-IDF 会出现明显退化。
   - 随着 skill 库变大，检索与注入的噪声会迅速累积。
3. embedding retrieval 曾显著缓解多轮退化。
   - 旧报告中最重要的经验之一就是：retrieval quality 是一阶因素。
4. 弱模型更容易从 skill 库中获益。
   - 这是早期实验里少数较清晰的正信号之一。
5. “100% accuracy on completed problems” 是一个值得保留的历史观察。
   - 它提示技能系统的价值有时不在于改变推理 correctness，而在于改变资源分配与完成率。

这些历史结论后来被重新理解为：

- full-loop 结果并不只是“skill 好不好”的函数；
- 它还强依赖：
  - retrieval 路径，
  - resource budget，
  - executor 交互模式，
  - 以及模型是否愿意真正调用已有 skill。

### 3.4 旧 integrated audit 中的 root-cause 排名

早期 regression audit 中，排查出的主要原因按证据强弱大致是：

1. `temperature` 配置漂移
   - 文档与实际运行配置不一致；
   - 温度 bug 曾显著改变整体结论。
2. `max_tokens` 收紧
   - 更低 token 上限会把原本可完成的问题压成 max-token hit。
3. TIR / ReAct executor 把推理打碎
   - 从单段完整脚本变成多步零碎试探后，模型更容易反复试算而不收束。
4. skill 暴露接口被削弱
   - 只给 description / signature 而不是更可审查的代码或更强接口时，模型更可能忽略 skill。
5. `enable_thinking` 与实际对话形式不匹配
   - 使模型缺少稳定的长程推理承载方式。
6. API backend 差异
   - 不同后端对同一模型的实际表现可能不同。

保留这一段的目的不是继续追旧 bug，而是明确：这类 full-loop regression 太容易混因，所以后续主线才转去更可控的 `refactor_lab` 与 `benchmark`。

## 4. Refactor Lab 的开发历史

### 4.1 初始目标

最初问题表述为：

- 给定一组已经通过测试的 skills；
- 自动发现共享子逻辑；
- 抽取为共享函数；
- 保持原测试通过；
- 统计 token 压缩效果。

主要脚本：

- `academic/refactoring_lab/test_runner.py`
- `academic/refactoring_lab/refactor_engine.py`
- `academic/refactoring_lab/example_skills.py`

关键实验产物原本包括：

- `experiments/standalone.*`
- `experiments/engine_naive.*`
- `experiments/engine_desc_first_glm5.*`
- `experiments/engine_desc_first_glm5_v2.*`
- `experiments/engine_desc_first_glm5_v3.*`

后续保留其 JSON 结果，Markdown 叙述并入本文档。

### 4.2 v1：free-text cluster key

问题设定：

- pairwise alignment 输出自然语言描述；
- 这个描述直接用作 cluster key；
- rewrite 中允许把共享函数重复贴在每个 skill 顶部。

结果：

- 抽出很多 near-duplicate 子函数；
- token 反而变多；
- cluster 容易碎裂。

保留结论：

- free-text cluster key 不稳定；
- 共享函数若被重复内联，表面上抽取成功，实际上没有真正共享。

### 4.3 v2：union-find clustering

改动：

- 把 pairwise 结果改成 yes/no 图；
- 用 union-find 做 connected components；
- rewrite 不再重复内联共享函数。

结果：

- cluster 合并过度；
- 一个噪声边会把两个本应独立的 cluster 接起来；
- 同时暴露出 harness 侧没有正确 prepend shared function 的问题。

保留结论：

- 仅靠 connected components 过于激进；
- refactor 评测不仅要看 engine 自己通过，还要保证 corpus-level harness 一致。

### 4.4 v3：clique-growth + 正确 prepend

改动：

- cluster 改为 greedy clique growth；
- harness 修正为对 refactored skills 正确 prepend shared function。

结果：

- 提取共享函数数：`5`
- token：`1530 -> 1258`
- 正确率：`100%`
- judge verdict：accept

保留结论：

- 当前接受版本是 `description-first + clique-growth + execution verification`；
- 这是目前最稳定、最值得继续扩展的 refactor 路线。

## 5. SkillsBench 相关开发历史

### 5.1 为什么引入两类 SkillsBench 资产

引入 SkillsBench 相关内容时，最先遇到的问题是：不能把“外部风格任务”混成一个单一 benchmark 名字，因为我们实际上需要两种不同资产：

- 一种服务于 refactor；
- 一种服务于 retrieval。

因此后续固定区分：

- `skillsbench_manual`
- `skillsbench_fixture`

### 5.2 `skillsbench_manual`

这是手工构造 grouped skills 的语料，重点不在官方 benchmark 跑分，而在：

- 是否能恢复合理 cluster；
- refactor 后 direct harness 是否仍通过；
- token 是否压缩。

关键结果：

- recovered clusters:
  - `_shared_find_local_peaks`
  - `_shared_normalize_title`
- `original_skills` 与 `refactored_skills` 都保持 `100%` 准确率；
- token `670 -> 516`，压缩约 `23.0%`。

保留结论：

- refactor 方法不仅对最初数学语料有效，也对更外部风格的 grouped tasks 有效。

### 5.3 `skillsbench_fixture`

这是 retrieval fixture，不测 refactor。

关键结果：

- Recall@1: `95.83%`
- Recall@5: `100.00%`

调试细节：

- 曾出现一次假性 `Recall@1 = 45.83%`；
- 原因是共享测试数据库中已有其他 skills 污染检索；
- 修复后 benchmark 只在 benchmark-owned rows / namespace 内评测。

保留结论：

- retrieval fixture 结果支持“当前 semantic retrieval 路径已经比较强”；
- 但不能把它写成官方 SkillsBench task success。

## 6. 从“强制抽 shared skill”到“history-aware planning”

### 6.1 设计转折

后续讨论中，需求从“先有现成 plan，再抽 shared skills”转变为：

- 当前 query 的计划本身就应显式考虑历史 query / workflow；
- 共享 skill 不一定被当前 query 用到；
- 规划器应该先决定是否复用旧计划、改写旧计划、只复用片段，还是完全 fresh；
- 只有当当前计划确实需要时，才提出 shared skill。

这一步是 replay benchmark 重新设计的直接原因。

### 6.2 新的 planner-side 目标

因此后来的 benchmark 不再以“有没有抽出某个 shared skill”作为唯一监督目标，而是把监督改成：

- preferred action
- useful plan calls
- relevant fragment ids
- possible / discouraged shared skill names

也就是让 `shared skill` 退回为一个可选的 planning outcome，而不是固定终点。

## 7. Replay Benchmark 的开发历史

### 7.1 早期问题：输出监督太 rigid

早期 replay benchmark 的一个主要问题是：

- 想把 planner 输出写死成 exact expected plan；
- 这对真实计划问题过于僵硬；
- 同一个 case 可能存在多种合理计划。

因此后续调整为：

- `LLM-as-a-judge`
- `references` 为软监督
- `workflow_fragments` 为可选复用锚点

### 7.2 当前协议：`workflow_reuse_v3_judge`

当前 case 类型：

- `reuse_plan`
- `adapt_plan`
- `reuse_workflow_fragment`
- `fresh`
- `propose_shared_skill`

当前关键脚本：

- `academic/refactoring_lab/planning_replay_benchmark.py`
- `academic/refactoring_lab/mine_replay_candidates.py`
- `academic/refactoring_lab/build_replay_case_drafts.py`
- `academic/refactoring_lab/merge_replay_cases.py`

当前关键 case 文件：

- `academic/refactoring_lab/experiments/planning_replay_cases.json`
- `academic/refactoring_lab/experiments/planning_replay_cases_merged.json`
- `academic/refactoring_lab/experiments/ds100_aime_replay_candidates.json`
- `academic/refactoring_lab/experiments/ds100_aime_replay_case_drafts.json`

### 7.3 synthetic validated cases

我们先构造了 `5` 个 synthetic validated cases，覆盖五类动作。

主要作用：

- 协议自检；
- judge-based 输出链路验证；
- 保证 benchmark 基本 contract 成立。

观察到的结果是：

- 在这个 synthetic validated 集合上，`joint_refactor` 赢 `5/5`。

保留结论：

- benchmark harness 能区分 planner 行为；
- 但这不是最终研究结论，因为 synthetic cases 只是协议验证，不是高强度证据。

这 `5` 个 synthetic cases 对应的动作覆盖为：

1. 精确复用旧计划；
2. 在格式变化下改写旧计划；
3. 只复用 workflow 片段；
4. 明确应该 fresh plan；
5. 仅在重复结构真的被当前计划用到时再提出 shared skill。

它们的作用主要是验证：

- 协议是否表达得足够清楚；
- `joint_refactor` 与 `legacy_fallback` 是否会在结构上给出不同决策；
- judge-based 输出链路是否稳定。

### 7.4 real-log mining 与 drafts

后续从真实实验日志中挖 candidate cases，并转成 replay drafts。

真实来源：

- baseline detail
- evolve detail
- learned skills json

挖到的主要现象仍然是：

- retrieval 有发生；
- token 经常上升；
- 结果不一定改善。

这些 real drafts 的价值在于：

- 给 benchmark 提供真实来源 case；
- 让后续能做 judged replay benchmark。

当前问题：

- 这些 drafts 仍然大多只有弱 references；
- 不能直接作为 benchmark gold。

### 7.5 merged replay set 的正确解读

我们曾把：

- `5` 个 validated synthetic cases
- `14` 个 real draft cases

合并为 `19` 个 cases。

这个 merged file 的正确用法是：

- 作为统一数据容器；
- 让前端和 pipeline 同时看到 validated 与 draft cases。

错误用法是：

- 把 merged file 上的总体胜率直接当作 benchmark 结果。

因为 draft-only real cases 会被跳过，不应混入最终主指标。

当前应记住的数字是：

- synthetic validated cases: `5`
- merged cases: `19`
- `joint_refactor_wins = 5`
- merged file 上的 `0.2632` 不是可汇报主结果，因为其中大部分 real cases 仍未被正式监督。

## 8. 研究前端与使用记录

虽然当前对外主线不以 UI 为主，但内部开发中前端起了很大作用，尤其在 benchmark authoring 与 case 检查阶段。

主要页面：

- `/replay`
  - 浏览 cases
  - 批量 LLM annotation
  - 查看 judge prompt/output
  - 运行 benchmark
- `/execute`
  - 真实 query 走 `retrieve -> plan -> execute -> extract -> test`
  - 查看 planner artifact
  - 查看 refine / retest history

后来 `/execute` 还逐步承担了更多诊断职责，包括：

- 选择模型配置；
- 选择 copy-mode memory，避免污染原始实验 skill store；
- 查看 executor-visible `plan_context`；
- 查看 memory session 中新增或修改的 skill / workflow。

这部分的重要性在于：

- 它让真实实验 log 更容易转成 replay candidates；
- 它让 annotation 与 case inspection 过程可视化；
- 但它本身不是研究结论。

### 8.1 前端与 annotation 路径的已知限制

几条曾在独立文档中记录、现在应保留的工程事实：

- replay annotation 的主要路径已经改为 LLM annotation，而不是人工表单编辑；
- 某些模型后端拒绝 `response_format=json_object`，要求 `json_schema`；
- 因此后端增加了 plain-text JSON fallback；
- `/execute` 当前是步骤级刷新，不是 token 级 streaming；
- 根本原因不在前端，而在 `app.llm.ask_tool()` 当前固定 `stream=False`。

这些限制虽然不是论文结论，但对后续继续维护 benchmark authoring / inspection 流程很重要。

## 9. 当前应保留的运行与产物

后续继续开发时，优先关注以下非文档产物：

### 9.1 refactor 结果

- `academic/refactoring_lab/experiments/engine_desc_first_glm5_v3.json`
- `academic/refactoring_lab/experiments/engine_naive.json`
- `academic/refactoring_lab/experiments/standalone.json`

### 9.2 SkillsBench 相关结果

- `academic/refactoring_lab/experiments/debug_skillsbench_compare_glm5_v4.json`
- `academic/refactoring_lab/experiments/debug_skillsbench_desc_first_glm5.json`
- `academic/refactoring_lab/experiments/skillsbench_fixture_retrieval.json`

### 9.3 replay benchmark 结果

- `academic/refactoring_lab/experiments/planning_replay_benchmark.json`
- `academic/refactoring_lab/experiments/planning_replay_benchmark_merged.json`
- `academic/refactoring_lab/experiments/planning_replay_cases.json`
- `academic/refactoring_lab/experiments/planning_replay_cases_merged.json`

### 9.4 full-loop 支撑性结果

- `academic/results/ds100_aime_v1_claude_full_3ep_baseline_summary.json`
- `academic/results/ds100_aime_v1_claude_full_3ep_evolve_3ep_summary.json`
- `academic/results/ds100_aime_v1_claude_full_3ep_skills.json`

## 10. 当前未完成事项

### 10.1 replay benchmark

最关键的未完成事项是 supervision：

- 从 real drafts 中筛出第一批真正值得标注的 case；
- 对这些 case 补齐可用 references；
- 形成 real validated replay set；
- 再比较 `joint_refactor` 与 `legacy_fallback`。

### 10.2 refactor 语料扩展

需要继续从真实使用场景中构造 grouped histories / grouped skills，而不是只靠手工小语料。

当前建议路径：

- 先从整体实验中挑做得不好的真实 case；
- 再将其转化为 benchmark 候选；
- 在这个基础上扩展更接近真实使用分布的 refactor / replay 数据。

### 10.3 论文附录材料

后续仍需补充：

- 典型成功/失败 case study；
- cluster graph 特征分析；
- 可直接展示的 workflow / plan / retrieved skill / judge rationale 文本。

### 10.4 历史工作日志仍有残留引用

虽然本子系统的正式文档已经收敛为两份主文档，但 `copilot_cli/progress.md` 这类长期工作日志仍可能保留对旧文件名的引用。

这些残留不影响当前正式文档面，但如果后续要做更彻底的文档清洗，需要额外同步处理这些日志型文件。

## 11. 当前文档策略

本子系统后续只维护两份正式文档：

- `academic/refactoring_lab/PAPER_STAGE_REPORT.md`
  - 对外阶段汇报
- `academic/refactoring_lab/DEV_HISTORY.md`
  - 内部开发与回溯文档

其他与本子系统直接相关的 Markdown / txt 文档原则上不再保留；如需新增，应先判断是否能够并入这两份主文档。

## 12. 2026-04-28：优先级重排与算法重思考

本节记录一次重要的方向调整。当前判断是：现有实现不 work，不能直接归因于算法失败，至少与三个外围因素强相关。

### 12.1 外围因素一：环境

如果任务环境本身缺少显式 skill 复用空间，很难观察到 skill evolution 的收益。

当前竞赛题 setting 的问题：

- 显式模板、套路和可复用函数相对稀疏；
- 不同 task 之间共享代码 skill 的机会少；
- 很多题的关键是一次性的 insight，而不是可复用函数。

K12 setting 的问题：

- 重复练习题、模板和技巧更多；
- 但模型本身能力可能已经很强；
- baseline 接近饱和时，skill 增益可能仍然不明显。

后续需要先设计环境选择实验，而不是默认继续在竞赛题上扩大实验。

### 12.2 外围因素二：模型

同题多次 evolve / replay 中，GLM 与 Claude 的差异说明：模型是否会使用 skill 是一个独立变量。

当前观察：

- GLM 经常无法使用拼接在 prompt 中的代码 skill；
- 这可能与训练时 scaffolding 或 tool-use 模式有关；
- 如果模型根本不会调用给定 skill，就无法研究后续 skill 效果；
- Claude 更容易发生真实 skill 调用，因此更适合做初步机制验证。

后续需要最小模型诊断集：

- 同题重放；
- 明确可用 skill；
- prompt code skill / tool skill / natural language skill 对比；
- 统计 retrieved、called、answer、token。

### 12.3 外围因素三：skill format

代码函数 skill 可能过于局限。很多有价值的复用结构并不表现为可直接调用的函数，而是：

- 一段策略；
- 一个 workflow；
- 一个 hint；
- 一个带脚本的文档模块；
- 某类题中可迁移的思路，例如递归结构、数学归纳法、构造不变量。

Anthropic-style skill 更接近 document + scripts 的 agentic module。后续应允许 skill format 多样化，而不是只研究 Python 函数调用。

### 12.4 算法核心目标重述

算法目标不是提高 train 阶段解题能力本身，而是：

- train 阶段可以多花 token；
- 目标是产生更好的 skill；
- test 阶段直接使用这些 skill，实现提点和降本。

因此评估重点应从 train trace success 转向 extracted skill quality。

### 12.5 从 pre-execute planning 回到 post-execute extraction

此前引入 pre-execute planning，是为了解决“复用一部分历史能力”的需求，希望当前 execute 直接受益于新 skill。

现在的判断是：

- pre-execute planning 复杂度更高；
- 没有当前 trace 时，模型只能预先猜测计划和 skill；
- 对交互式任务尤其难以精准；
- token 成本也可能更高。

更简单的路线是把 refactoring 放回 execute 之后：

- 本次 query 已经有完整 trace；
- 可以与历史相似 query 的 trace / skill 对齐；
- 用 refactor_lab 风格算法在 extract 阶段提取或重构 skill；
- train 阶段效果差一些可以接受，只要最终 skill 质量提高。

新的 extraction 输入：

- 本次 trace；
- 执行结果；
- golden answer；
- token 成本；
- 历史相似 query 的 trace / skill。

新的 extraction 输出：

- 重构后的 skill；
- 或拒绝生成 skill。

### 12.6 闭环反馈：Shapley-style skill value

当前 extraction 仍然偏开环，因为 test code 也是模型生成的。更可靠的反馈包括：

- golden answer；
- token 开销；
- skill 使用次数。

一个候选方案是 Shapley value：

- 比较配备某个 skill 前后，executor 在任务集上的效果差异；
- 用 answer improvement、token reduction 和 usage 估计 skill 的边际贡献。

优点：

- 直观；
- 端到端；
- 可以比较 skill set 的好坏。

问题：

- 方差可能大；
- 开销巨大；
- 需要多 query、多 rollout 才能估计稳定；
- 反馈发生在事后，只能用于筛除 skill 或给出 semantic refine 建议。

### 12.7 复用性的 formalization

当前“每个 skill 被使用多次”只是粗略指标。更细的形式化可以从 query distribution 出发：

- 假设 query 空间有 density；
- 每个 skill 对 query 有收益和覆盖半径；
- skill 收益可用 Shapley value 估计；
- 在 skill budget 下，希望用最少 skill 给整个 query 空间带来最大收益。

需要继续研究的问题：

- 如何度量 skill 影响半径；
- 是否用训练集中受益 query 数量作为离散近似；
- 这是单 skill 质量问题，还是 skill set 的群体优化问题；
- 是否能用 coverage、facility location、submodular optimization 或 budgeted selection 建模。

### 12.8 新优先级

后续顺序应调整为：

1. 先做环境选择实验。
2. 再做模型 skill-use 诊断。
3. 再做 skill format 对比。
4. 然后回到 post-execute extraction。
5. 最后设计 Shapley-style skill value 与 skill budget 优化。

在前三个外围因素没有结论前，不建议继续扩大 full-loop 或 planner benchmark 实验。

## 13. 2026-04-28：Skill 自进化近期文献调研

本节记录对用户提供的微信综述文章和对应 arXiv 论文的调研结果。该调研用于更新论文 related work 和当前项目定位，不是新的实验结果。

### 13.1 输入与产物

输入：

- 用户给出的微信文章链接：`https://mp.weixin.qq.com/s/k2vjcm5ctSdCYXRDko9CZQ`
- 本地保存版：`/home/lixujun/skill_evolving_survey/10 篇论文拆解 Skill + 自进化的技术路线.html`

网页 live 版在当前环境中可能受微信访问限制，因此主要读取本地 HTML，并使用 arXiv API / arXiv 页面交叉核对论文标题、摘要和发布时间。

新增或更新的产物：

- `academic/paper/RELATED_WORK_SURVEY.md`
  - 逐篇文献对比；
  - 对环境、模型、skill format 三个外围因素的回答；
  - 我们还能快速验证的差异化方向。
- `academic/paper/paper.md`
  - 替换 `Related Work` 占位；
  - 同步把 skill refactoring 主线改回 post-execute extraction。
- `academic/refactoring_lab/PAPER_STAGE_REPORT.md`
  - 加入近期工作已经覆盖的范围；
  - 强调当前 positioning 应收缩到外围条件诊断和 repository maintenance。

### 13.2 综述实际列出的论文

本地 HTML 标题写“10 篇论文”，但 References 实际列出 11 篇：

- `OpenClaw-RL: Train Any Agent Simply by Talking`，arXiv:2603.10165。
- `Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills`，arXiv:2603.25158。
- `CoEvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification`，arXiv:2604.01687。
- `SkillClaw: Let Skills Evolve Collectively with Agentic Evolver`，arXiv:2604.08377。
- `EvoSkill: Automated Skill Discovery for Multi-Agent Systems`，arXiv:2603.02766。
- `SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning`，arXiv:2602.08234。
- `D2Skill: Dynamic Dual-Granularity Skill Bank for Agentic RL`，arXiv:2603.28716。
- `SkillX: Automatically Constructing Skill Knowledge Bases for Agents`，arXiv:2604.04804。
- `Memento-Skills: Let Agents Design Agents`，arXiv:2603.18743。
- `SkillForge: Forging Domain-Specific, Self-Evolving Agent Skills in Cloud Technical Support`，arXiv:2604.08618。
- `XSkill: Continual Learning from Experience and Skills in Multimodal Agents`，arXiv:2603.12056。

其中 `OpenClaw-RL` 更偏参数侧在线 RL，不是外部 skill evolution 主线，但可作为 next-state feedback 的旁支参考。

### 13.3 调研后的核心判断

方向已经很拥挤，以下泛化 claim 不适合作为主新颖性：

- 自动从 trace 生成 skill；
- skill 自我迭代；
- 历史轨迹提升后续 agent；
- 多粒度 skill bank；
- verification loop；
- repository governance。

更稳的定位是：

- 先做 environment / model / format diagnostics；
- 再做 post-execute cross-trace refactoring；
- 把 refactor_lab 定位成 skill repository maintenance；
- 用 correctness-preserving rewrite、answer/token/usage marginal value、budgeted coverage selection 形成更具体的贡献。

### 13.4 对三个外围因素的启发

环境：

- 近期工作基本选择高反馈密度或高复用密度场景，例如云技术支持、ALFWorld、WebShop、多用户 agent ecosystem、long-horizon user-interactive benchmarks。
- 这支持我们对竞赛数学 setting 的怀疑：显式代码 skill 复用稀疏时，很难观察到 skill evolution 收益。

模型：

- 多篇工作通过 RL、router、paired rollout 或 runtime skill interface 解决 skill-use 问题。
- 这支持我们的观察：GLM prompt-code skill 不调用可能是 setting 不成立，而不是单纯 extractor 坏。

Skill format：

- 大多数近期工作使用 markdown skill、workflow、scripts、references、multi-file package、task skill / step skill / action-level experience，而不是单一 Python 函数。
- 后续 `code function` 应作为一个 ablation，而不是默认主形态。

### 13.5 对下一步实验顺序的修正

调研后推荐顺序保持并进一步收紧为：

1. 环境诊断：确认哪些任务分布有足够 reuse density 和非饱和 baseline。
2. 模型诊断：确认 retrieved-to-called conversion，比较 GLM、Claude、tool-native skill interface。
3. Format 诊断：比较 code function、strategy doc、workflow card、document + scripts。
4. Post-execute extraction：用本次 trace + 相似历史 trace/skill + golden answer/token/usage 生成或重构 skill。
5. Skill value：用小规模 Shapley-style ablation 估计 answer improvement、token reduction、usage。
6. Repository optimization：把 skill set 选择建模为 coverage / redundancy / utility trade-off。

在前三步没有形成证据前，不建议继续扩大完整 academic full-loop 或把 pre-execute planner benchmark 当作主实验。

### 13.6 逐篇文献笔记拆分

为避免 `RELATED_WORK_SURVEY.md` 过长，后续将每篇同期工作单独维护中文笔记：

- `academic/paper/related_work_notes/README.md`
  - 技术路线索引和对本项目的归纳。
- `academic/paper/related_work_notes/trace2skill.md`
- `academic/paper/related_work_notes/skillx.md`
- `academic/paper/related_work_notes/coevo_skills.md`
- `academic/paper/related_work_notes/skillclaw.md`
- `academic/paper/related_work_notes/evoskill.md`
- `academic/paper/related_work_notes/memento_skills.md`
- `academic/paper/related_work_notes/skillrl.md`
- `academic/paper/related_work_notes/d2skill.md`
- `academic/paper/related_work_notes/xskill.md`
- `academic/paper/related_work_notes/skillforge.md`
- `academic/paper/related_work_notes/openclaw_rl.md`

每篇笔记统一包含：

- 基本信息；
- 问题设定与动机；
- 核心方法；
- 实验设置；
- 主要实验结果；
- 与我们工作的不同；
- 对我们的借鉴。

这些笔记作为论文 related work 和后续实验定位的素材，不作为阶段性实验结果。

### 13.7 同期工作 Benchmark 调研

新增：

- `academic/paper/BENCHMARK_SURVEY.md`

该报告把 11 篇同期工作中出现的相关 benchmark 统一整理为环境选择材料，而不是独立研究选题。覆盖范围包括：

- `ALFWorld`
- `WebShop`
- `BFCL-v3`
- `AppWorld`
- `τ²-Bench`
- `SkillsBench`
- `WildClawBench`
- `OfficeQA`
- `SealQA`
- `BrowseComp`
- `GAIA`
- `Humanity's Last Exam`
- `SpreadsheetBench-Verified`
- `WikiTableQuestions`
- `DAPO-Math / AIME`
- `DocVQA`
- `VisualToolBench`
- `TIR-Bench`
- `MMSearch-Plus`
- `MMBrowseComp`
- `AgentVista`
- `OSWorld-Verified`
- `SWE-Bench-Verified`
- `Terminal / SETA RL data`
- `GSM8K personalization`
- `NQ / TriviaQA / PopQA / HotpotQA / 2Wiki / MuSiQue / Bamboogle`

调研后的近期推荐：

1. 首先考虑 `BFCL-v3 multi-turn base`。
   - 工程轻；
   - tool/API 结构明确；
   - 容易记录 retrieved / selected / used skill；
   - 适合 tool rule card 和 workflow card。
2. 并行或下一步考虑 `ALFWorld`。
   - 被 `SkillRL` 与 `D2Skill` 同时使用；
   - 任务类型重复；
   - success/fail 明确；
   - 适合 task skill + step skill。
3. 第二阶段考虑 `WebShop`、`AppWorld` 或 search QA。
   - 更真实；
   - workflow 复用更强；
   - 但工程成本更高。

报告也明确：不建议继续把 AIME/MATH 当作 skill reuse 主证明环境，只保留为 low-cost auxiliary math setting。

随后补充了“提出时间与当前剩余空间”维度：

- `ALFWorld` 已被近期 RL/agent 工作刷到较高 success，作为唯一提点环境空间偏小，但仍适合看 step/token/error reduction。
- `BFCL-v3 multi-turn base` 官方榜仍未饱和，且工程轻、结构化强，因此优先级上升为第一候选。
- `AppWorld`、`OfficeQA/Pro`、`SealQA`、`SpreadsheetBench` 剩余空间更大，但工程成本或外部依赖更高。
- `AIME/MATH`、老 QA、`SWE-Bench Verified`、原始 `BrowseComp` 均存在强模型刷高、污染或 harness 特化风险，不适合作为主证明环境。

同时补充了“饱和榜单上的模型与论文卖点”：

- 饱和榜单通常由 Claude Opus/Sonnet、Gemini、GPT/o-series、GLM、Qwen/xLAM function-calling models 或 heavy agent scaffold 刷高。
- 同期 skill/self-evolution 工作一般不直接卖“总榜 SOTA”，而是卖：
  - 同模型 ablation；
  - 弱模型/开源模型受益；
  - 子任务 failure mode 改善；
  - token / cost / training efficiency；
  - cross-model 或 OOD transfer；
  - skill quality 与 repository governance；
  - 选择未饱和的新业务 benchmark。
- 对我们而言，`BFCL-v3` 或 `ALFWorld` 的实验不应只看 accuracy，而应同时报告 retrieved -> selected -> used、token/step/tool-call error、跨模型迁移和 skill library refactoring 效果。

### 13.8 Benchmark 子系统落地

根据后续优先级讨论，第一阶段不再继续扩大 AIME/MATH full-loop，而是准备五个更适合 skill evolving 的榜单：

- `BFCL-v3`
- `AppWorld`
- `OfficeQA`
- `SpreadsheetBench-Verified`
- `TIR-Bench`

新增代码：

- `academic/benchmarks/registry.py`
- `academic/benchmarks/types.py`
- `academic/benchmarks/artifacts.py`
- `academic/benchmarks/bfcl.py`
- `academic/benchmarks/spreadsheet.py`
- `academic/benchmarks/run.py`
- `academic/benchmarks/README.md`

当前状态：

- 五个榜单均进入 registry，记录 source、提出时间、任务形态、metric、skill format、推荐模型、饱和度和工程成本。
- `BFCL-v3 multi-turn base` 已有 runnable baseline adapter：
  - 从 Hugging Face 下载 `BFCL_v3_multi_turn_base.json`；
  - 下载 `multi_turn_func_doc/*.json` 并转为 OpenAI tool schema；
  - 下载 `possible_answer/BFCL_v3_multi_turn_base.json`；
  - 使用原生 tool-call loop 运行模型；
  - 使用本地兼容 environment 执行 file-system / math / social / travel / trading 等工具；
  - 按 turn 对模型 tool-call sequence 与 possible answer 做 precision / recall / F1 / task success 统计。
- `SpreadsheetBench-Verified` 已有 runnable smoke adapter：
  - 下载 `spreadsheetbench_verified_400.tar.gz`；
  - 解析 `dataset.json`；
  - 为每个 case 复制 `*_init.xlsx`；
  - 让模型生成 openpyxl 代码并保存输出 workbook；
  - 对 `answer_sheet + answer_position` 与 `*_golden.xlsx` 做 cell-level verifier。

验证：

```bash
python -m pytest -q academic/benchmarks/test_benchmark_adapters.py
```

当前通过 3 个 contract tests：

- registry 包含五个目标榜单；
- BFCL loader / tool docs / possible-answer scorer 可用；
- Spreadsheet verified loader / golden-vs-golden verifier 可用。

注意：

- BFCL 当前 scorer 是本地兼容版，不是官方 `bfcl-eval` 最终报告器。它适合先做 baseline sanity、tool-call 统计和 skill 使用统计，论文正式数值建议再接官方 evaluator 复核。
- Spreadsheet 当前 scaffold 是 openpyxl baseline，不等同于 Trace2Skill 的完整 spreadsheet agent。第一阶段目标是保证 baseline 能读写 xlsx、能 verifier、能收集 skill package 使用数据。

本轮 smoke sanity：

- `bfcl_v3 + GLM-4.7`：1 个 held-out case 可以完整跑完 tool-call loop；启用 tool filtering、tool-name hint、initial state preview 后，call F1 为 0.40，task success 为 0。主要剩余问题是参数级精确性，例如 schema 要求 `booking_id` 时模型可能写成 `insurance_id`，或自然语言描述与 expected string 不完全匹配。
- `spreadsheet + Claude`：1 个 held-out case 可以完整跑完 openpyxl scaffold，并通过 cell-level verifier；182 个 answer cells 全部匹配，cell accuracy 1.00。

因此当前判断：

- BFCL adapter 已可用于 runner / logging / skill usage 统计，但 baseline 还需要跨模型和官方 evaluator 复核。
- Spreadsheet adapter 已具备非零且可通过的 baseline sanity，适合下一步设计 spreadsheet skill package extraction。
