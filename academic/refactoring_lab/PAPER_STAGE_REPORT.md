# Refactor Lab 与 Skill Reuse：阶段性研究汇报

## 摘要

Test-time Skill Evolution 的核心价值不在于“多存一些 skill”，而在于让系统能够在后续任务中真实复用历史结构，并在 test 阶段获得准确率或成本收益。已有探索说明，受控 refactoring 可以在不破坏功能的前提下压缩技能表示，replay benchmark 也已经建立了 judge-based 的规划评测协议。但当前更重要的判断是：继续做算法实验之前，必须先解决三个外围因素，即任务环境是否有复用空间、模型是否会使用外部 skill、skill format 是否过于局限。只有这些条件成立，后续关于 extraction、refactoring、Shapley feedback 与复用性优化的算法实验才有可解释性。本文档因此将当前阶段重新组织为：先验证外围可行性，再回到 post-execute extraction 与 skill value 优化。

## 1. 引言

面向长期运行的 agent 系统，经验积累不应只体现在参数更新上，也可以体现在显式、可管理、可维护的技能库中。Skill evolution 希望系统在 train 阶段多付出一些成本，沉淀可复用经验，并在 test 阶段直接调用这些经验实现提点和降本。

近期 `Skill + 自进化` 方向已经快速拥挤。`Trace2Skill`、`SkillX`、`CoEvoSkills`、`SkillClaw`、`EvoSkill`、`SkillRL`、`D2Skill`、`Memento-Skills`、`SkillForge`、`XSkill` 等工作已经覆盖了轨迹蒸馏、验证闭环、skill repository 治理和 RL policy-skill 协同等核心主题。因此，当前项目不应再把“自动生成自进化 skill”作为主要新颖性，而应把贡献收缩到更可验证的问题：什么环境适合 skill reuse，什么模型/接口真的会使用 skill，什么 skill format 合理，以及如何在 frozen-model setting 下做 post-execute cross-trace refactoring、correctness-preserving rewrite、skill value 评估和 skill set budget 选择。

目前的探索暴露出一个问题：端到端结果不 work 时，很难判断是算法本身失败，还是外围条件不支持 skill reuse。我们已经观察到三类强干扰因素：

- 环境因素：竞赛题 setting 中显式模板和可复用函数较稀疏，skill 发挥空间可能天然较小。
- 模型因素：GLM 在同题重放中经常不会使用 prompt 中拼接的代码 skill，而 Claude 更容易发生真实调用。
- skill format 因素：代码函数可能过于局限，很多有价值的 skill 更像策略文档、workflow card 或 document + scripts 形式的 agentic module。

因此，当前阶段的首要任务不是继续扩大端到端实验，而是先设计实验分别确认这些外围条件。随后再讨论算法本身，包括 extraction 顺序、反馈闭环和复用性 formalization。

## 2. 当前阶段的问题定义

### 2.1 总体目标

长期目标是构建一个闭环 skill system：

1. train 阶段从成功或部分成功的 trace 中提取高质量 skill；
2. test 阶段检索并直接使用这些 skill；
3. 系统用 answer、token、usage 等反馈持续筛选和改进 skill set。

### 2.2 当前优先级

当前阶段按优先级分成两层。

第一层是外围可行性：

- 任务环境中是否存在足够复用密度；
- 模型是否会使用外部提供的 skill；
- skill 应采用什么表示格式。

第二层才是算法设计：

- extraction 是否应发生在 execute 之后；
- 如何联合本次 trace 与历史相似 trace / skill；
- 如何用 golden answer、token 开销和调用次数形成反馈；
- 如何定义 skill 的复用性与 skill set 的整体优化目标。

### 2.3 对之前规划路线的修正

之前引入 pre-execute planning，是希望当前 query 在执行前就能利用历史 skill 或 shared abstraction。但现在看来，这一路线复杂度高、token 成本高，并且在没有本次 trace 的情况下很难精准判断应复用什么。

新的简化路线是：

- execute 当前 query；
- 得到完整 trace、执行结果与 token 成本；
- 在 extract 阶段比较本次 trace 与历史相似 query 的 trace / skill；
- 输出重构后的 skill；
- 接受 train 阶段成本较高，重点优化 test 阶段 skill 质量。

### 2.4 与近期工作的关系

当前调研给出的定位是：

- `Trace2Skill` / `SkillX` 已经覆盖大范围 trajectory-to-skill distillation，因此我们不应主张“首次从 trace 中生成 skill”。
- `CoEvoSkills` / `SkillForge` / `EvoSkill` / `Memento-Skills` 已经覆盖 verification-driven skill evolution，因此我们需要把验证信号落到 answer、token、usage 和 repository-level regression 上。
- `SkillClaw` / `EvoSkill` / `SkillX` 已经强调 repository governance，因此 refactor_lab 最适合被定位成 skill repository maintenance。
- `SkillRL` / `D2Skill` / `XSkill` 表明 skill use 常常依赖 policy 或 router 训练，因此 frozen LLM + prompt-injected code skill 不能被默认视为有效 setting。
- Anthropic-style skills 和多篇论文都使用 markdown / workflow / scripts / references / multi-file package 等格式，这进一步说明 code-function-only skill 只能作为 ablation。

因此，当前项目的可 دفاع positioning 是：在不训练模型参数的 setting 下，系统化诊断 skill reuse 的外围条件，并研究如何像维护软件模块一样维护一个 growing skill repository。

## 3. 方法框架

### 3.1 外围因素验证

#### 环境验证

需要比较不同任务环境的 skill reuse potential：

- 竞赛题：技巧分布稀疏，模板复用较少；
- K12 重复练习题：模板重复多，但模型本身能力较强，增益可能有限；
- 工具型 agentic tasks：重复 workflow、文件操作套路和脚本复用更常见。

核心指标：

- query 间可复用结构密度；
- oracle skill reuse rate；
- baseline 强度；
- skill 可能带来的 answer / token 改善空间。

#### 模型验证

需要先判断模型是否会使用给定 skill。最小实验应包含：

- 同题重放；
- 明确可用 skill；
- 多模型对比；
- 统计 retrieved、called、answer、token。

如果模型不会调用 skill，则在该模型上继续研究算法收益没有意义。

#### Skill Format 验证

代码函数只是 skill 的一种形式。后续应比较：

- code function；
- natural language strategy / hint；
- workflow card；
- document + scripts；
- 特定场景灵感，例如“发现递归结构”“尝试数学归纳法”。

评价目标不只是函数调用率，还包括是否提升探索效率、答案正确率和 token 成本。

### 3.2 Post-execute Extraction

新的算法核心放在 execute 之后。输入包括：

- 本次 query；
- 本次完整 trace；
- 本次执行结果；
- golden answer；
- token 成本；
- 历史相似 query 的 trace / skill。

输出是：

- 新 skill；
- 重构 skill；
- 或拒绝生成 skill。

这一路线更接近 refactor_lab 的思路：有了完整 trace 后，再比较当前经验与历史经验，提取真正有复用潜力的结构。

### 3.3 Feedback-driven Skill Evaluation

当前 extraction 仍然偏开环，因为 test code 也是生成的。后续需要引入外部反馈：

- golden answer；
- token 开销；
- skill 使用次数。

一个候选方案是 Shapley-style skill value：比较配备某个 skill 前后，executor 在一批 query 上的效果差异。

优点：

- 直观；
- 端到端；
- 可以比较 skill set 好坏。

问题：

- 方差可能大；
- 开销很高；
- 反馈发生在事后，只能用于筛除 skill 或生成 semantic refine 建议。

因此，Shapley feedback 更适合作为 post-hoc validation 和 skill selection 的核心候选，而不是廉价在线步骤。

### 3.4 复用性的优化目标

目前“每个 skill 被使用多次”只是粗略代理。更完整的 formalization 可以从 query distribution 出发：

- 假设 query 空间有 density；
- 每个 skill 对一部分 query 有正收益；
- skill 的收益可用 Shapley value 估计；
- skill 有一定覆盖半径；
- 在 skill budget 约束下，选择最少 skill 覆盖最多高收益 query 区域。

这将问题从单个 skill 质量评估，扩展成 skill set 的群体优化问题。可能相关的形式包括 coverage optimization、facility location、submodular maximization 和 budgeted selection。

## 4. 实验设计

### 4.1 实验顺序

后续实验应按以下顺序推进：

1. 环境选择实验；
2. 模型 skill-use 诊断；
3. skill format 对比；
4. post-execute extraction 实验；
5. Shapley-style skill value 实验；
6. skill budget / coverage 优化实验；
7. 最后再回到完整 academic loop。

### 4.2 已有实验资产

当前已有资产仍然保留其价值，但优先级调整如下：

- `refactor_lab`
  用于验证 shared structure extraction 与 correctness-preserving rewrite。
- `skillsbench_manual`
  用于验证 refactor 能否迁移到外部风格 grouped skills。
- `skillsbench_fixture`
  用于验证 retrieval substrate。
- `planning_replay_cases`
  用于后续 planner / history reuse 诊断，但不再作为下一步最高优先级。
- `academic/paper/RELATED_WORK_SURVEY.md`
  用于维护近期 skill evolution 文献调研、逐篇对比和项目定位。

### 4.3 待构造实验资产

需要新增：

- 高复用密度任务集；
- 多模型同题 skill-use 诊断集；
- 多格式 skill benchmark；
- post-execute extraction benchmark；
- Shapley value 小规模可控评估集。

## 5. 当前已完成结果

### 5.1 Refactor Lab：内置数学语料

在内置 18-skill 数学语料上，当前接受版本得到：

| 方法 | 提取共享子函数数 | token 总量 | token 变化 | 正确率 |
| --- | ---: | ---: | ---: | ---: |
| 朴素 exact-match 去重 | 0 | 1530 | 0.0% | 100% |
| 当前 refactor 方法 | 5 | 1258 | -17.8% | 100% |

这说明共享结构确实可被发现，并且 correctness-preserving refactoring 可行。

### 5.2 `skillsbench_manual`

在手工 SkillsBench 风格 grouped corpus 上：

| Skill 集 | 准确率 | Avg tokens |
| --- | ---: | ---: |
| `original_skills` | 100.00% | 670 |
| `refactored_skills` | 100.00% | 516 |

token 压缩约为 `23.0%`，功能保持不变。

### 5.3 `skillsbench_fixture`

retrieval fixture 当前结果：

- Recall@1: `95.83%`
- Recall@5: `100.00%`

该结果只说明 retrieval 路径较强，不代表官方 SkillsBench 端到端成功率。

### 5.4 Replay Benchmark

当前已完成：

- `workflow_reuse_v3_judge` 协议；
- `5` 个 synthetic validated cases；
- real-log mining 与 draft generation；
- validated + draft 的 merged case 容器。

这些结果说明 benchmark harness 可用，但真实 judged replay benchmark 尚未完成。

## 6. 尚未完成的关键实验

### 6.1 环境可行性实验

目标：找到 skill reuse 最可能发生、且 baseline 又没有完全饱和的任务环境。

待报告：

- query 复用密度；
- oracle skill reuse rate；
- baseline 已解决比例；
- skill 潜在增益空间。

### 6.2 模型可用性实验

目标：确认不同模型是否会使用给定 skill。

待报告：

- GLM vs Claude；
- prompt skill vs tool skill；
- code skill vs natural language skill；
- retrieved-to-called conversion。

### 6.3 Skill Format 实验

目标：确定后续主算法使用的 skill 表示。

待报告：

- code function；
- strategy doc；
- workflow card；
- document + scripts；
- 对 answer、token、usage 和探索行为的影响。

### 6.4 Post-execute Extraction 实验

目标：验证 execute 后联合本次 trace 和历史 trace / skill 提取 skill 是否更稳。

待报告：

- skill 质量；
- test-time 使用率；
- token 成本；
- 相比 pre-execute planning 的复杂度差异。

### 6.5 Shapley-style Skill Value 实验

目标：用端到端边际贡献评估 skill。

待报告：

- answer improvement；
- token reduction；
- usage；
- 方差；
- 评估成本。

### 6.6 复用性 Formalization

目标：把“复用性”从经验指标变成优化目标。

待回答：

- skill 覆盖半径如何估计；
- 是否用训练集中受益 query 数量近似；
- skill set selection 是否可建模为 budgeted coverage；
- 是否存在可行的贪心或近似算法。

## 7. 讨论

当前阶段的关键判断是：

1. 现有系统不 work 不能直接归因于算法失败，必须先控制环境、模型和 skill format。
2. pre-execute planning 不应继续作为主路线；post-execute extraction 更简单，也更符合 trace-based skill evolution。
3. skill 质量应从 test-time answer、token 和 usage 的边际收益衡量，而不是只看 train 阶段解题成功率。
4. 复用性不是单 skill 属性，也可能是 skill set 在 query distribution 上的 coverage 优化问题。
5. 近期相关工作已经覆盖泛化的 skill generation / self-evolution 叙事，后续论文应强调 controlled diagnostics、repository maintenance 和 frozen-model skill value。

因此，下一步不应继续扩展 planner benchmark 或完整 loop，而应先完成三个外围因素实验。

## 8. 下一阶段计划

### 8.1 先做环境选择

- 比较竞赛题、K12 重复练习题和工具型 agentic tasks；
- 估计 query 间显式复用密度；
- 选择最适合作为主实验环境的 setting。

### 8.2 做模型 skill-use 诊断

- 用同题重放和显式 skill case 比较 GLM、Claude 等模型；
- 检查 prompt skill、tool skill、natural language skill 的调用情况；
- 如果模型不会使用 skill，暂不在该模型上研究算法收益。

### 8.3 做 skill format 对比

- 比较 code function、strategy doc、workflow card、document + scripts；
- 选择后续主算法使用的 skill 表示。

### 8.4 回到 post-execute extraction

- 使用本次 trace + historical similar trace / skill；
- 提取或重构更高质量 skill；
- 接受 train 阶段高成本，优先优化 test-time skill quality。

### 8.5 设计 Shapley-style value 与 budget selection

- 用 answer、token、usage 估计 skill 边际收益；
- 研究方差和成本；
- 进一步形式化 skill reuse 的 coverage / budget 优化问题。

## 9. 当前文档边界

本文档是阶段性研究汇报，不记录调试过程和中途失败尝试。详细开发记录保留在 `DEV_HISTORY.md`。
