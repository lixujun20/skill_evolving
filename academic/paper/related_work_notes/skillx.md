# SkillX 文献笔记

## 1. 基本信息

- 标题：SkillX: Automatically Constructing Skill Knowledge Bases for Agents
- arXiv：2604.04804
- arXiv 页面：https://arxiv.org/abs/2604.04804
- PDF：https://arxiv.org/pdf/2604.04804
- arXiv API 核对版本：v2，更新于 2026-04-19
- 作者：Chenxi Wang, Zhuoyun Yu, Xin Xie, Wuguannan Yao, Runnan Fang, Shuofei Qiao, Kexin Cao, Guozhou Zheng, Xiang Qi, Peng Zhang, Shumin Deng
- 机构：Zhejiang University; Ant Digital Technologies, Ant Group
- 代码：论文摘要注明将发布于 https://github.com/zjunlp/SkillX

## 2. 问题设定与动机

论文关注如何从 agent 经验中自动构建可复用的 plug-and-play skill knowledge base。作者认为现有 self-evolving agent 往往每个 agent 孤立学习，重复发现相似行为，经验泛化差，并且受限于自身探索能力。

SkillX 的目标不是为单个任务临时生成一个 skill，而是预先用强 agent 构建一个可被不同 agent 和环境复用的技能库。论文把经验表示作为关键问题：相比存储轨迹、insight 或 workflow，作者主张用层次化 skill 表示，将经验拆成规划、功能和原子三个层级，以提升可组合性、检索效率和迁移能力。

## 3. 核心方法

SkillX 包含三个主要设计：

- Multi-Level Skills Design：将技能库分为 planning skills、functional skills、atomic skills。planning skills 描述任务高层步骤、顺序、依赖和分支；functional skills 对应可复用子任务或宏操作；atomic skills 对单个工具/API 提供增强语义说明、参数模式、约束和常见失败模式。
- Rollout and Skills Extraction：对训练任务进行多次 rollout，从成功轨迹中抽取多层技能。抽取 planning skill 时压缩最终解法并过滤探索、回溯、试错；functional skill 由 planning step 指导抽取；atomic skill 从工具调用模式中提炼。
- Iterative Skills Refinement 与 Exploratory Skills Expansion：反复用当前技能库 rollout、抽取候选技能、merge/filter，并通过 add/modify/keep 更新技能库；此外利用 seed rollout 中的工具使用情况、失败率和未使用工具，引导探索 under-explored 或 failure-prone 工具，再合成新任务并扩展技能库。

推理时，SkillX 先检索 planning skills 并让模型重写 pseudo-plan，再用 pseudo-plan 的步骤检索 functional/atomic skills，去重并让 LLM 自筛选适用技能后注入系统提示。

## 4. 实验设置

- Benchmarks：BFCL-v3、AppWorld、tau^2-Bench。
- BFCL-v3：使用 base multi-turn category，随机分 50 个训练实例和 150 个测试实例。
- AppWorld：使用 90 个训练实例，Test Normal 作为测试集。
- tau^2-Bench：使用各子域定义的 train/test split。
- 指标：AppWorld 与 BFCL-v3 报告 Avg@4 和 Pass@4；tau^2-Bench 按论文设置报告运行四次的 pass rate。
- 模型：Qwen3-32B、Kimi-K2-Instruct-0905、GLM-4.6。SkillX 构建时用 GLM-4.6 每个训练任务独立 rollout 4 次，最大 refinement iterations 为 3；探索时每个训练任务 1 次 rollout；检索和去重使用 Qwen3-Embedding-8B，检索 cosine similarity 阈值为 0.45。
- Baselines：No-memory、A-Mem、AWM、ExpeL。论文同时比较自提取经验和 GLM-4.6 强模型蒸馏经验迁移到弱模型的设定。

## 5. 主要实验结果

- Qwen3-32B 上，SkillX 相比 No Memory 在 BFCL-v3 Avg@4 从 53.67 到 63.67，Pass@4 从 73.33 到 82.00；tau^2-Bench Avg@4 从 27.68 到 35.12，Pass@4 从 47.62 到 58.93；AppWorld Retail/Airline/Telecom 从 53.75/38.75/36.25 到 66.87/47.50/43.75。
- Kimi-K2-Instruct-0905 上，SkillX 在 AppWorld 提升明显：Retail/Airline/Telecom 从 75.62/51.25/78.12 到 78.12/58.75/82.50；BFCL-v3 和 tau^2-Bench 也有提升但幅度较小。
- GLM-4.6 上，SkillX 相比 No Memory 在 BFCL-v3 Avg@4 从 76.67 到 79.50，Pass@4 从 83.33 到 86.00；tau^2-Bench Avg@4 从 60.27 到 64.88，Pass@4 从 83.33 到 88.69；AppWorld Retail/Airline/Telecom 从 76.25/70.00/70.63 到 82.50/76.25/71.88。
- 论文指出 Qwen3-32B 在多个 benchmark 上大约获得 10 个点左右提升；这与主表中 Qwen3-32B 的 BFCL-v3 Avg@4 +10.00、AppWorld Retail +13.12、tau^2-Bench Avg@4 +7.44 等数值一致。
- 在更强模型实验中，DeepSeek-V3.2 使用 SkillX 后 BFCL-v3 Avg@4 从 64.33 提升到 GLM-Extract 67.17 / Self-Extract 67.83，AppWorld Avg@4 从 61.90 提升到 64.28 / 65.48；GPT-4.1 的 BFCL-v3 GLM-Extract 从 49.66 到 60.00，但 Self-Extract 仅到 50.67。
- Ablation 表显示 GLM-4.6 上 Vanilla/Expand 多轮 refinement 和 expansion 通常能继续提升 BFCL-v3 与 AppWorld，但不同轮次不单调；具体细节需要从 PDF 表格进一步核对。

## 6. 与我们工作的不同

- SkillX 构建的是可检索的 skill knowledge base，而不是单个综合 skill 文档；推理时依赖 embedding 检索、pseudo-plan rewrite 和 LLM self-filter。
- 它主要从成功轨迹中抽取可复用技能，并通过探索扩展覆盖；失败轨迹不是像 Trace2Skill 那样作为根因诊断和 patch proposal 的主信号。
- SkillX 强调“强 agent 预构建、弱 agent 复用”的 distillation paradigm；如果我们的工作强调每个 agent 自主演化或用户本地持续更新，则训练/部署关系不同。
- 技能表示更偏条目化、层次化知识库，和 Claude-style 多文件 skill package 或单目录 skill 的组织不同。

## 7. 对我们的借鉴

- 三层 skill taxonomy 很有参考价值：planning 解决任务组织，functional 解决子任务复用，atomic 补足工具 schema 和调用约束。
- pseudo-plan 可以作为中间检索 query，而不是直接注入最终 prompt，这能降低 hallucinated plan 对执行的负面影响。
- 技能库构建需要 merge/filter/update 机制，简单累积技能会带来冗余、冲突和检索噪声。
- 对大工具空间，主动探索 under-used 和 failure-prone 工具比只依赖 seed tasks 更可能补齐覆盖。
- 评估技能表示时应比较 trajectory、workflow、memory、skill 等不同经验载体，而不仅比较是否使用经验。
