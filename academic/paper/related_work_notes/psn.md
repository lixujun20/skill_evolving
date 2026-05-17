# PSN 文献笔记

## 1. 基本信息

- 论文：Evolving Programmatic Skill Networks
- arXiv：2601.03509v1
- 作者：Haochen Shi, Xingdi Yuan, Bang Liu
- 发布时间：2026-01-07
- 来源核对：arXiv 页面与 PDF（https://arxiv.org/abs/2601.03509，https://arxiv.org/pdf/2601.03509）
- 任务定位：在开放式 embodied environments 中持续构建、优化、复用和重构 executable programmatic skills。

## 2. 问题设定与动机

PSN 研究 continual skill acquisition：agent 面对一个持续到来的任务流，需要维护一个不断增长的 executable skill library。作者指出 flat skill library 的主要问题不是不能生成 skill，而是缺少组合结构、缺少对嵌套调用失败的 credit assignment，也缺少长期维护 compact skill network 的机制。

论文的核心动机是把 skill library 从一组互相独立的代码片段升级为一个 programmatic skill network。每个 skill 是可执行符号程序，skill 之间可以互相调用，系统显式维护调用图，并在失败、优化和重构时利用这个图结构。

## 3. 核心方法

PSN 包含三个核心机制：

- REFLECT：对组合 skill 的执行失败做结构化 fault localization。系统沿调用链分析失败，把错误归因到具体 skill 或组合关系，而不是让 LLM 直接自由改整段代码。
- Progressive optimization with maturity-aware update gating：每个 skill 有成熟度或质量估计。稳定成熟的 skill 更新频率降低，仍不可靠的 skill 保持可塑性，从而缓解 continual learning 中的稳定性-可塑性冲突。
- Canonical structural refactoring with rollback validation：对 skill network 做参数化、公共子技能抽取、重复删除、结构合并等规范化重构。每次重构需要通过回滚验证，避免 compactness 改进破坏已有能力。

论文还把 PSN 的学习动态类比为神经网络训练：REFLECT 类似 symbolic backpropagation，maturity-aware gating 类似 freezing，structural refactoring 类似 architecture search 或网络结构优化。

## 4. 实验设置

- 环境：MineDojo 和 Crafter。
- 任务形态：开放式 embodied agent 任务，强调长程技能树、组合技能复用、持续任务流和环境交互反馈。
- 主要比较：ReAct、Reflexion、AutoGPT、Voyager 等 LLM agent / skill-learning baselines。
- 评价重点：技能获取效率、技能保留、灾难性遗忘、组合泛化、skill network growth 与 compactness。

## 5. 主要实验结果

论文报告 PSN 在 MineDojo 和 Crafter 上取得更稳定的技能增长和更强的长期保留：

- MineDojo 中，PSN 更快掌握长程 technology tree，能通过 skill composition 复用已有能力。
- Crafter 中，PSN 在复杂 survival / tech-tree setting 下获得更稳定的累计奖励增长。
- 与 flat skill libraries 相比，PSN 更能保留已经掌握的技能，降低新任务引入后的 forgetting。
- Maturity-aware gating 能减少成熟 skill 的不必要改写，提升网络稳定性。
- Structural refactoring 能控制 skill repertoire growth，避免 skill 库膨胀成大量重复或过长的单体程序。

## 6. 与我们工作的不同

- PSN 面向 embodied environments，skill 是 executable symbolic programs，并通过环境交互执行；我们的当前主线更强调 benchmark-agnostic skill asset，可以是 rule card、workflow、doc fragment、tool-use convention 或 shared sub-doc，不限于可执行程序。
- PSN 已经强覆盖了 programmatic skill network、fault localization、maturity-aware update gating、rollback refactoring 这些与长期维护高度相关的点。因此我们的论文不能再把“skill network 会长期重构”作为未被想到的核心新颖性。
- PSN 的 refactoring 更像在线符号程序图重写；我们的区别应落在 test-time repository maintenance 的通用资产模型、skill-bundle/test-result 分离、unit utility with/without protocol、integration-derived test accumulation，以及 frozen-model setting 下的 budgeted skill-set selection。
- PSN 的实验环境天然有可执行环境反馈和组合 skill 调用图；BFCL、spreadsheet、QA 或数学推理任务中，skill 的作用范围、调用证据和失败归因需要额外由 trace/bundle/refiner agent 构造。

## 7. 对我们的借鉴

- PSN 是最重要的强相关工作之一，必须在论文中正面讨论，而不能只作为一般 skill evolution citation。
- REFLECT 说明 trace 归因必须结构化：我们的 bundle builder / refiner 不应直接看全局失败自由生成，而应先定位“哪个 skill 在什么作用域内产生了什么影响”。
- Maturity-aware gating 可以对应我们的 version/status/stale 机制：成熟 skill 不应被频繁重写，新 skill 可以有保护期和更高探索权重。
- Rollback validation 支持我们的 correctness-preserving refactor 叙事，但也提醒我们必须把 rollback、affected-skill tests、dependency stale handling 做成真实可运行协议，而不是只写成设计原则。
- PSN 的强覆盖迫使我们的核心创新收缩到 repository-level selection objective：在候选 skill population 中，用验证集 utility、token cost、retrieval noise、redundancy 和 maintenance cost 做群体优化，而不是只做单 skill evolution。
