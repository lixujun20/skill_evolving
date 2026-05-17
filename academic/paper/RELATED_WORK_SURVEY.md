# Skill 自进化近期文献调研与本项目定位

日期：2026-05-12

## 1. 调研范围

本报告基于用户给出的微信综述文章本地保存版：

- `/home/lixujun/skill_evolving_survey/10 篇论文拆解 Skill + 自进化的技术路线.html`

 live 微信链接在当前环境中无法直接打开，因此本文以本地 HTML 为综述来源，并用 arXiv 页面/API 与 Anthropic 官方 Agent Skills 文档交叉核对。综述标题写“10 篇”，但 References 实际列出 11 篇论文，其中 `OpenClaw-RL` 更偏参数侧在线 RL，可作为旁支参照；其余 10 篇基本都直接讨论外部化 skill 的生成、演化、验证或治理。

核心判断：这个方向已经很拥挤，不能再把“skill evolution”本身作为主要新颖性。PSN 已经直接覆盖 programmatic skill network 的长期维护、fault localization、maturity gating 和 rollback refactoring；SkillMOO 已经把 skill bundle tuning 写成 pass rate / cost 的多目标优化；AgentOptimizer 更早把外部 functions 作为可学习权重。因此当前更稳的定位是：先证明环境、模型、skill format 这些外围条件成立，再把方法贡献收缩到 test-grounded repository population selection、post-execute cross-trace refactoring、correctness-preserving rewrite、skill value / coverage evaluation 这些更具体的问题上。

## 2. 文献全景

### 2.1 轨迹到 skill 的蒸馏

代表工作是 `Trace2Skill` 和 `SkillX`。这一路线把历史执行轨迹视为原始经验，再通过并行分析、层级合并、过滤和主动探索构造可迁移的 skill bank。它们直接说明一个事实：从单条 trace 顺序更新很容易过拟合局部经验，真正可复用 skill 往往需要跨多条轨迹做归纳。

对我们的影响：post-execute extraction 是合理方向，但不能只从本次 trace 抽一个函数；应该把本次 trace、相似历史 trace、历史 skill 和反馈信号放在一起做归纳。

### 2.2 验证与诊断驱动的 skill 优化

代表工作是 `CoEvoSkills`、`Memento-Skills`、`SkillForge` 和 `EvoSkill`。它们共同强调：skill 生成不难，难的是验证、诊断、准入和回滚。`CoEvoSkills` 使用 generator-verifier 闭环，`SkillForge` 在云技术支持场景中用专家答案和历史工单提供可靠反馈，`EvoSkill` 用 held-out validation 和 Pareto frontier 做准入，`Memento-Skills` 则把结构化 markdown skill 当成非参数外部记忆，并训练 router 选择 skill。

对我们的影响：后续不能只报告 extractor 产出了多少 skill，而必须报告 skill 是否带来 answer gain、token reduction、usage gain，以及是否通过稳定的验证门槛。

### 2.3 群体进化与 skill 治理

代表工作是 `PSN`、`SkillMOO`、`SkillClaw`、`EvoSkill` 和部分 `SkillX`。这些工作把 skill 库视为共享资产，不仅关心新增 skill，还关心 edit / merge / prune / rollback / capacity control。PSN 把 executable skills 组织成 programmatic skill network，并在调用图上做 failure localization、成熟度门控和带 rollback 的结构重构；SkillMOO 则把 skill bundle edit search 形式化为多目标优化，用 pass rate 和 cost 选择 Pareto 更优候选。综述文章把这类趋势概括为 `SkillOps` 是合理的：成熟系统的竞争点不是生成速度，而是长期治理能力。

对我们的影响：refactor_lab 的图结构、去冗余和 correctness-preserving rewrite 可以作为“skill repository maintenance”的具体切口，但不能单独作为核心新颖性。更值得主打的是 repository-level selection：在多次 LLM sampling 产生的候选 skill population 中，用 valid-set utility、token/cost、retrieval noise、redundancy、coverage 和 maintenance cost 共同决定保留哪些 skill。

### 2.4 RL 场景下的 policy-skill 协同

代表工作是 `AgentOptimizer`、`SkillRL`、`D2Skill`、`XSkill`，以及旁支 `OpenClaw-RL`。AgentOptimizer 把 agent functions 作为 learnable weights，在不修改 LLM 权重的情况下离线优化函数集合；`SkillRL` 和 `D2Skill` 通过训练或 RL 让 policy 学会使用 skill，`D2Skill` 还用 with-skill / without-skill rollout gap 构造 hindsight utility。`XSkill` 区分 action-level experience 和 task-level skill，说明不同粒度的外部知识应分流管理。`OpenClaw-RL` 不是外部 skill 演化主线，但它说明 next-state signal 可以成为在线学习反馈。

对我们的影响：我们前面观察到 GLM 不会调用 prompt 中拼接的代码 skill，并不是偶然小 bug，而是模型/接口假设不成立。若不训练 router、不提供 tool-native skill interface，只靠 prompt 拼接代码，很可能无法研究真正的 skill 效果。

### 2.5 Anthropic-style skill format

Anthropic 官方 Agent Skills 文档将 skill 定义为 filesystem-based modular capability：核心是 `SKILL.md`，并可附带 scripts、reference docs、templates 和其他资源。skill 的 description 用于发现和触发，完整内容按需进入上下文，脚本可由 agent 运行但不需要把脚本全文都放入 prompt。

对我们的影响：当前 code-function-only skill format 过窄，也会夸大“模型不会调用 skill”的问题。更合理的 skill 对象至少要允许 strategy doc、workflow card、document + scripts、tool-call rule、domain reference 等多种形式。

## 3. 逐篇对比表

| 论文 | Skill 表示 | 环境/任务 | 更新机制 | 验证/反馈 | 对我们当前问题的启示 |
| --- | --- | --- | --- | --- | --- |
| AgentOptimizer | callable functions as learnable weights | 多个 LLM agent 下游任务 | LLM optimizer 新增/修改/删除 functions | offline training、rollback、early-stop | 外部能力层作为可学习对象已被提出；我们的差异应是 versioned skill asset、测试驱动维护和群体选择 |
| OpenClaw-RL | 主要不是外部 skill，而是 policy 从 next-state signal 学习 | 对话、终端、GUI、SWE、tool-call | PRM judge + hindsight-guided on-policy distillation | next-state 中的 evaluative/directive signal | 不是主相关工作，但提示我们要充分利用执行后反馈，而不是只做自省式 extraction |
| Trace2Skill | declarative skill directory / guide | spreadsheet、VisionQA、math reasoning | 多 agent 并行分析轨迹，再层级合并 | OOD transfer、跨模型迁移、任务表现 | 强覆盖了 trajectory-to-skill；我们的差异应落在 cross-trace refactoring 和 correctness-preserving maintenance |
| CoEvoSkills | 多文件 skill package | SkillsBench、Claude Code、Codex 等 agent skill setting | skill generator 与 surrogate verifier 协同演化 | verifier 产生可执行反馈，不访问 ground-truth test content | 直接说明复杂 skill 不能用 tool-evolution 方法简单替代；也威胁我们原本的 skill generation 叙事 |
| PSN | executable programmatic skill network | MineDojo、Crafter | REFLECT fault localization、maturity gating、structural refactoring | 环境反馈、调用图归因、rollback validation | 强覆盖长期 skill network maintenance；我们需要强调通用资产模型、bundle/result 分离和 repository-level selection |
| SkillMOO | task-specific skill bundle | SkillsBench 软件工程任务 | LLM edits + NSGA-II survivor selection | pass rate、LLM inference cost、runtime | 直接覆盖 skill bundle 多目标优化；我们的 objective 必须更 repository-level，包含检索噪声、冗余和维护成本 |
| SkillClaw | 共享 skill repository | 多用户 OpenClaw-like agent ecosystem、WildClawBench | 聚合跨用户轨迹，由 agentic evolver refine/create/skip | 多用户真实交互与验证后同步 | 说明环境应选有重复使用和多用户证据的场景；单人竞赛题不一定合适 |
| EvoSkill | structured reusable skill folders，包含 workflow/code | OfficeQA、SealQA、BrowseComp transfer | failure analysis 后 create/edit skill | held-out validation + Pareto frontier | 强调 create/edit/prune/governance；我们的 refactor graph 可对齐为 repository maintenance |
| SkillRL | hierarchical SkillBank | ALFWorld、WebShop、search-augmented tasks | trajectory distillation + adaptive retrieval + recursive RL evolution | RL reward、任务成功率、token footprint | 通过 policy training 缓解“模型不会用 skill”；我们若冻结模型，必须单独做模型 skill-use 诊断 |
| D2Skill | task skill + step skill 双粒度 skill bank | ALFWorld、WebShop | paired rollout performance gap 得到 hindsight utility | with-skill vs without-skill 成功率差、utility-aware pruning | 与我们的 Shapley-style skill value 最接近；需要明确我们是在非 RL / frozen LLM setting 做端到端边际贡献 |
| SkillX | strategic plans / functional skills / atomic skills 三层结构 | AppWorld、BFCL-v3、tau2-Bench | multi-level distillation + iterative refinement + exploratory expansion | transfer 到较弱 agents、任务成功率/效率 | 支持多粒度 skill format；也说明高复用 long-horizon environment 更适合观察收益 |
| Memento-Skills | structured markdown skills + stateful prompts | GAIA、HLE 等 generalist assistant benchmarks | Read-Write Reflective Learning，skill router 可训练 | task success rate 训练/评估 router | 说明 skill selection 本身是可学习模块；只靠 semantic retrieval 可能不够 |
| SkillForge | domain-grounded skill，结合知识库/历史工单 | 云技术支持，1883 tickets / 3737 tasks | Failure Analyzer -> Skill Diagnostician -> Skill Optimizer | expert reference answers、历史 tickets、一轮轮 deployment feedback | 解决了环境和 golden feedback 问题；提示我们应优先找高反馈密度场景 |
| XSkill | task-level skills + action-level experiences | multimodal tool-use benchmarks | multi-path rollout summarization + cross-rollout critique | 多 benchmark、多模型表现与 usage history | 区分 experience 与 skill，对我们当前“workflow history 是否应直接给 executor”很有启发 |

## 4. 对三个外围因素的回答

### 4.1 环境

近期工作基本都绕开了“复用稀疏、反馈弱”的环境。`SkillForge` 选择云技术支持，因为历史工单、领域知识和专家答案形成高质量闭环；`D2Skill` 和 `SkillRL` 选择 ALFWorld / WebShop 这类可重复交互环境；`SkillClaw` 使用多用户共享生态，天然有重复 workflow 和 failure mode。

这支持我们的担忧：竞赛数学题不一定是观察 skill reuse 的好环境。它的 reusable code fragment 密度低，很多题依赖一次性 insight；K12 题虽然重复度高，但强模型 baseline 可能接近饱和。下一步必须先做 environment suitability diagnostic，而不是继续在当前 setting 上堆 full-loop。

### 4.2 模型

近期工作很少假设“任意模型看到 prompt 里的代码 skill 就会自然调用”。`SkillRL`、`D2Skill` 通过 RL 或 paired rollout 把 skill 使用写入 policy learning；`Memento-Skills` 训练 skill router；`SkillX` 把强 backbone 构造出的 skill library transfer 给弱 agent。Anthropic-style skills 也不是简单 prompt 拼接，而是由 runtime 暴露 metadata、按需加载文件，并允许运行脚本。

这支持我们的观察：GLM 在 prompt-code skill setting 下不调用 skill，可能是 setting 设计不合适，而不是算法抽取失败。后续模型诊断应先回答 retrieved-to-called conversion，再讨论 skill 是否提升解题。

### 4.3 Skill format

近期工作几乎都不把 skill 限制为单个 Python 函数。常见形式包括 markdown skill、multi-file package、workflow、hierarchical strategy、task-level skill、step-level correction、action-level experience、domain reference、script/template bundle。

因此 code-function-only format 应降级为一个 ablation，而不是默认主形态。更合适的主对象是：

- `metadata`：名称、description、触发条件、版本、来源、置信度；
- `body`：strategy / workflow / tool-use rule / code function 中的一种或多种；
- `resources`：scripts、references、templates、tests；
- `evidence`：支持该 skill 的 trace、成功/失败样本、usage、token 与 answer 反馈。

## 5. 我们还能做什么

### 5.1 不宜再主打的点

- “自动生成 skill”已经被 `Trace2Skill`、`SkillX`、`EvoSkill`、`CoEvoSkills` 覆盖。
- “skill 会自我迭代”已经被 `SkillForge`、`Memento-Skills`、`SkillClaw` 覆盖。
- “programmatic skill network 的 fault localization / maturity gating / rollback refactoring”已经被 `PSN` 强覆盖。
- “skill bundle 的 pass rate / cost 多目标优化”已经被 `SkillMOO` 直接覆盖。
- “不改模型权重而优化外部 functions”已经被 `AgentOptimizer` 覆盖。
- “用历史轨迹提升后续 agent”已经被多篇 RL / memory / skill-bank 工作覆盖。
- “多粒度 skill bank”已经被 `D2Skill`、`SkillX`、`XSkill` 覆盖。

### 5.2 仍有快速验证价值的差异化

1. Peripheral-condition diagnostics：系统化证明 environment、model、skill format 是 skill evolution 是否 work 的前置条件。这本身可形成很实用的实验贡献。
2. Post-execute cross-trace refactoring：不在 execute 前猜测 plan，而是在完整 trace 之后，与相似历史 trace / skill 做结构对齐、抽取或重构 reusable skill。
3. Correctness-preserving skill rewrite：把 refactor_lab 的 graph clustering、helper extraction、test-preserving rewrite 做成 skill repository maintenance，而不是单次 skill generation。
4. Frozen-model skill value：在不训练 policy 的情况下，用 answer、token、usage 和 Shapley-style marginal contribution 估计 skill 的端到端价值。
5. Budgeted skill-set optimization：把“复用性”形式化为 query distribution 上的 coverage / redundancy / utility trade-off，而不是只看单个 skill 被调用几次。相对 SkillMOO 的 bundle-level pass-rate/cost Pareto search，这里应进一步纳入 retrieval noise、duplicate skill rate、dependency risk、maintenance cost 和 integration-derived unit tests。
6. Format ablation in one controlled setting：同一批任务、同一模型、同一 retrieval 下比较 code function、strategy doc、workflow card、document+scripts，直接回答什么 skill format 最有效。

### 5.3 推荐的近期实验顺序

1. 环境诊断：比较竞赛数学、K12 重复练习、工具型 agentic tasks 的 oracle reuse density、baseline saturation、golden feedback availability。
2. 模型诊断：同题重放，比较 GLM / Claude / tool-native interface 的 retrieved-to-called conversion。
3. Format 诊断：在可控任务集上比较 code function、strategy doc、workflow card、document+scripts。
4. Post-execute extraction：输入本次 trace + historical similar traces/skills + golden answer/token/usage，输出 new/refactored/rejected skill。
5. Skill value：小规模 Shapley-style ablation，估计每个 skill 对 answer 与 token 的边际贡献。
6. Skill set optimization：用 refactor graph + utility 近似 coverage，做 budgeted selection / pruning。

## 6. 对论文叙事的建议

论文不应写成“我们提出第一个 skill evolution 系统”。更稳的写法是：

本文将 test-time skill evolution 重新表述为一个 software-engineering problem：skill 不是一次性 prompt artifact，而是一个长期维护的软件资产。已有工作已经展示了 skill generation、trajectory distillation、verification loop 和 RL co-evolution 的有效性，但仍缺少对以下问题的系统分析：什么环境中 skill reuse 有意义，什么模型/接口真的会使用 skill，什么 skill format 最适合 frozen LLM agent，以及如何在 repository 级别做 correctness-preserving refactoring、utility-based validation 与 budgeted maintenance。

该叙事可以保留我们已有 refactor_lab 结果，但不能把它包装成完整 end-to-end skill evolution 的最终证明。它目前最适合作为“repository maintenance / refactoring mechanism”的阶段性证据。

## 7. Sources

- 微信综述本地 HTML：`/home/lixujun/skill_evolving_survey/10 篇论文拆解 Skill + 自进化的技术路线.html`
- 微信原始链接：<https://mp.weixin.qq.com/s/k2vjcm5ctSdCYXRDko9CZQ>
- Anthropic Agent Skills docs：<https://docs.claude.com/en/docs/agents-and-tools/agent-skills>
- OpenClaw-RL：<https://arxiv.org/abs/2603.10165>
- Trace2Skill：<https://arxiv.org/abs/2603.25158>
- CoEvoSkills：<https://arxiv.org/abs/2604.01687>
- SkillClaw：<https://arxiv.org/abs/2604.08377>
- EvoSkill：<https://arxiv.org/abs/2603.02766>
- SkillRL：<https://arxiv.org/abs/2602.08234>
- D2Skill：<https://arxiv.org/abs/2603.28716>
- SkillX：<https://arxiv.org/abs/2604.04804>
- Memento-Skills：<https://arxiv.org/abs/2603.18743>
- SkillForge：<https://arxiv.org/abs/2604.08618>
- XSkill：<https://arxiv.org/abs/2603.12056>
- PSN / Evolving Programmatic Skill Networks：<https://arxiv.org/abs/2601.03509>
- SkillMOO：<https://arxiv.org/abs/2604.09297>
- AgentOptimizer：<https://arxiv.org/abs/2402.11359>
