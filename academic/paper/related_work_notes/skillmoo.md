# SkillMOO 文献笔记

## 1. 基本信息

- 论文：SkillMOO: Multi-Objective Optimization of Agent Skills for Software Engineering
- arXiv：2604.09297v1
- 作者：Jingzhi Gong, Ruizhen Gu, Zhiwei Fei, Yazhuo Cao, Lukas Twist, Alina Geiger, Shuo Han, Dominik Sobania, Federica Sarro, Jie M. Zhang
- 发布时间：2026-04-10
- 来源核对：arXiv 页面与 PDF（https://arxiv.org/abs/2604.09297，https://arxiv.org/pdf/2604.09297）
- 复现包：Zenodo replication package（https://zenodo.org/records/19489028）
- 任务定位：用多目标优化自动调整 coding agent 的 skill bundles，在成功率、成本和运行时间之间做 Pareto trade-off。

## 2. 问题设定与动机

SkillMOO 关注 software engineering agents 中的 skill bundle 调优问题。手工维护 skill bundle 需要同时考虑成功率、token / dollar cost 和 runtime，且不同任务之间不易迁移。作者指出，简单添加更多说明并不一定更好，冗余、冲突或噪声内容可能增加成本并降低成功率。

论文的核心动机是把 skill bundle tuning 形式化为 multi-objective optimization，而不是单目标 accuracy improvement。它直接对应我们当前关心的问题：skill library 的价值不只是“能不能提高成功率”，还包括是否降低推理开销、是否减少冗余、是否避免检索噪声。

## 3. 核心方法

SkillMOO 使用 solver-optimizer loop：

- Task solver agent：用一个候选 skill bundle 执行 coding tasks，记录 pass rate、LLM inference cost、runtime 和 error traces。
- Skill optimizer agent：根据 solver 的失败轨迹和当前 bundle，提出 skill edits。
- NSGA-II survivor selection：在候选 bundle population 中根据 pass rate 和 cost 做多目标选择，保留 Pareto 更优的候选。

论文中的 edit patterns 包括 pruning、substitution、reordering 和 rewriting。实验分析显示，pruning 和 substitution 是主要收益来源，这说明 skill optimization 不是“越多越好”，而是要去掉噪声并替换误导内容。

## 4. 实验设置

- Benchmark：SkillsBench 中三个 software engineering tasks。
- 对比：original skill bundle、no-skill baseline，以及 SkillMOO evolution 后的候选 bundle。
- 优化指标：pass rate、LLM inference cost；runtime 作为辅助指标。
- 搜索策略：LLM-proposed edits + NSGA-II survivor selection。
- 任务类型：包括 build repair、language translation、framework migration 等 coding-agent 任务。

## 5. 主要实验结果

论文摘要报告：

- 在三个 SkillsBench 软件工程任务上，SkillMOO 相对每个任务最强 baseline 最高提升 pass rate 131%。
- 同时最高降低 cost 32%。
- 优化开销较低。
- Pattern analysis 显示 pruning 和 substitution 是主要贡献，说明 minimal and focused skill content 往往优于不断累积 instruction。

这些结果对我们的论文非常关键：reviewer 可能会认为“skill 库优化成多目标选择问题”已经被 SkillMOO 覆盖。因此我们需要把差异写清楚。

## 6. 与我们工作的不同

- SkillMOO 优化的是 task-specific skill bundle，主要在 software engineering coding tasks 上做离线 bundle edit search；我们的目标是长期 skill repository maintenance，涉及多个 skill 的版本、依赖、bundle tests、integration-derived tests、stale propagation 和跨 benchmark 的通用资产协议。
- SkillMOO 的单位是一个 bundle candidate；我们的单位包括 individual skill、shared reusable part、bundle case、test result、dependency pin 和 store-level population。
- SkillMOO 已经明确使用 pass rate/cost 多目标优化；我们的创新不能只写“我们也考虑 cost”。更合理的差异是：我们做 repository-level budgeted population selection，把单 skill utility、retrieval noise、redundancy、coverage、maintenance cost 和 dependency risk 放进统一选择目标。
- SkillMOO 的 edit search 主要优化已有 bundle 内容；我们的系统还需要从真实 trace 中抽取新 skill、构建长期 bundle、refine broken skill、处理 integration failures，并在版本化 store 中维护 lineage。

## 7. 对我们的借鉴

- token/cost 不应只是报告指标，而应进入所有 with/without skill unit utility tests：skill work 至少应满足 accuracy 不降且 token/cost 下降，或在 accuracy 提升时显式说明成本代价。
- pruning/substitution 的发现支持我们的 RFA 合并/删除职责：当重复 skill 或噪声 skill 被发现时，系统应允许 merge/delete，而不是只新增 shared part。
- 论文创新可进一步收缩为“test-grounded repository population selection”：通过多次 LLM skill sampling 形成候选池，再用 valid-set utility、成本、冗余和检索噪声做离散群体选择。
- baseline 应包含 append-all、individual utility filter、top-k by usage/help count、random budget、SkillMOO-style bundle optimization proxy，才能说明 repository-level selection 的价值。
