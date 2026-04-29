# SkillClaw: Let Skills Evolve Collectively with Agentic Evolver

## 1. 基本信息

- 论文：SkillClaw: Let Skills Evolve Collectively with Agentic Evolver
- arXiv：2604.08377v1，2026-04-09
- 作者：Ziyu Ma, Shidong Yang, Yuxiang Ji, Xucong Wang, Yong Wang, Yiming Hu, Tongwen Huang, Xiangxiang Chu
- 机构/团队：DreamX Team
- 代码：<https://github.com/AMAP-ML/SkillClaw>
- 任务定位：面向 OpenClaw 式多用户 agent 生态的“集体技能演化”（collective skill evolution）。

## 2. 问题设定与动机

OpenClaw 等 LLM agent 依赖可复用技能完成复杂任务，但部署后的技能通常是静态的。不同用户在真实任务中会反复遇到相似的工作流、工具调用模式和失败模式，如果这些轨迹不能汇总成可靠的技能更新，系统就无法从跨用户经验中累积改进。

SkillClaw 的核心问题是：在不要求用户额外标注或手工维护技能的情况下，如何把多用户、跨时间的普通交互轨迹转化为共享技能库的持续更新，并保证更新不会降低线上用户体验。

## 3. 核心方法

- 轨迹聚合：每个会话记录用户 prompt、agent 动作、工具调用、环境/用户反馈和最终响应，形成完整 action-feedback 因果链。
- 按技能分组：把轨迹按引用过的技能分组；未使用技能的轨迹进入无技能组，用于发现缺失的可复用过程。
- Agentic evolver：LLM 驱动的演化器读取分组证据和当前技能定义，基于成功/失败轨迹共同判断采取 `Refine`、`Create` 或 `Skip`。
- 保守更新原则：成功轨迹用于识别应保留的不变量，失败轨迹用于定位需要修改或补充的地方，避免把有效技能整体重写。
- 验证后部署：候选技能必须在真实执行环境中优于当前 best skill 才会被接受并合并进共享技能池；被拒绝的候选只保留记录，不部署。
- 日夜循环：白天用户使用当前 best skill pool 并产生轨迹，夜间进行技能演化与验证，次日同步已验证技能。

## 4. 实验设置

- Benchmark：WildClawBench，包含 60 个复杂真实 agent 任务，覆盖 6 类能力：Productivity Flow、Code Intelligence、Social Interaction、Search & Retrieval、Creative Synthesis、Safety & Alignment。
- 环境：完整 Linux 容器、文本/代码/图像/视频多模态输入，任务长度约 15-50 步，包含 API 和模型下载等外部依赖。
- 主实验：模拟 8 个并发用户，运行 6 天/6 轮日夜演化；Day 1 是初始技能基线，后续天数使用前一夜验证通过的 best skill pool。
- 模型：执行、演化和验证均使用 Qwen3-Max。
- 报告范围：论文主表报告 4 个代表性类别；另外通过 3 个自定义 query 做 Skill Evolve Lite 控制验证。

## 5. 主要实验结果

WildClawBench 用户侧 6 日部署结果：

- Social Interaction：54.01% -> 60.34%，绝对提升 +6.33， 相对提升 +11.72%。
- Search & Retrieval：22.73% -> 34.55%，绝对提升 +11.82，相对提升 +52.00%。
- Creative Synthesis：11.57% -> 21.80%，绝对提升 +10.23，相对提升 +88.41%。
- Safety & Alignment：24.00% -> 32.00%，绝对提升 +8.00，相对提升 +33.33%。

控制验证（Skill Evolve Lite）：

- basic extraction：21.7% -> 69.6%，提升 +47.8%。
- deadline parsing：41.1% -> 48.0%，提升 +6.9%。
- save report：28.3% -> 100.0%，提升 +71.7%。
- 平均：30.4% -> 72.5%，提升 +42.1%。

论文强调该实验仍是小规模测试：用户查询、反馈信号和交互深度有限；更多用户、更长时间和更多验证条件可能进一步改善演化轨迹。

## 6. 与我们工作的不同

- SkillClaw 面向多用户共享 agent 生态，核心信号来自跨用户线上轨迹；我们的工作更偏向在受控实验/数据集上验证技能演化对推理或任务求解的增益。
- SkillClaw 的技能更新需要真实环境验证后进入全局 best pool，强调线上稳定性；我们的实验可以更直接比较 baseline、技能检索、技能演化等离线条件。
- SkillClaw 聚焦 OpenClaw/WildClawBench 式长程工具使用任务，技能常压缩环境/API/流程知识；我们的技能若面向数学或问答推理，更需要压缩解题策略、错误模式和可复用推理模板。
- SkillClaw 的关键贡献是“集体演化”和共享同步机制，而不是单个模型在单个数据集上的自我改进。

## 7. 对我们的借鉴

- 轨迹记录应保留完整 action-feedback 链，而不只是最终答案对错；这有助于区分技能缺陷、agent 执行缺陷和环境缺陷。
- 技能演化应同时读取成功与失败样本：成功样本定义不应破坏的行为，失败样本提供更新依据。
- 可以引入候选技能验证门槛：只有在 held-out/验证任务上超过当前技能池时才部署，避免技能库越演化越臃肿或退化。
- 对技能更新动作做显式分类：改进现有技能、优化描述/触发条件、新建技能、跳过。
- 若后续扩展到多轮实验，可借鉴日夜循环，把“使用产生证据”和“夜间演化验证”分离，便于统计每轮增益。
