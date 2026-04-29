# XSkill: Continual Learning from Experience and Skills in Multimodal Agents

## 1. 基本信息

- 论文：XSkill: Continual Learning from Experience and Skills in Multimodal Agents
- arXiv：2603.12056v2
- 作者：Guanyu Jiang, Zhaochen Su, Xiaoye Qu, Yi R. Fung
- 发布时间：2026-03-12；v2 更新时间：2026-03-13
- 来源核对：arXiv API 摘要与 PDF（https://arxiv.org/abs/2603.12056，https://arxiv.org/pdf/2603.12056v2）

## 2. 问题设定与动机

XSkill 关注 multimodal agents 在开放式工具使用任务中如何不更新参数地从历史轨迹中持续改进。作者指出多模态 agent 虽然能调用多种工具，但仍存在工具使用低效、workflow 编排不灵活、跨 episode 无法积累经验的问题。

论文将可复用知识分为两类互补结构：

- Experiences：action-level guidance，偏局部决策、工具选择、探索和错误恢复。
- Skills：task-level guidance，偏结构化规划、工作流和工具编排。

其动机是多模态场景中的知识抽取和检索必须 grounding in visual observations，不能只从文本轨迹中总结。

## 3. 核心方法

XSkill 是 dual-stream continual learning framework，包含两个阶段。

Phase I：Accumulation of Task Experience & Skills。

- 对每个训练任务进行多路径 rollout。
- Rollout summary：MLLMkb 对轨迹、图像、query、ground truth 和已适配 skill 做 visually grounded summarization，记录关键决策、工具使用模式、失败原因，并抽取 skill fragments。
- Cross-rollout critique：对成功和失败轨迹做对比，识别导致结果差异的因果因素，产生 experience add/modify 操作。
- Knowledge consolidation：experience bank 以 JSON-like item 存储，skill library 以 Markdown skill document 存储；系统通过相似度过滤、数量控制、MLLM 判断、merge/refine/delete 维护知识库质量。

Phase II：Solving Task with Experience & Skills。

- Task decomposition retrieval：将当前 query 和 images 分解为多个 abstract subtasks，分别检索 top-k experiences。
- Experience rewrite：将通用 experience 改写为适配当前视觉上下文的行动建议。
- Skill adaptation：将全局 skill document 裁剪、合并并适配当前任务，把 rewritten experiences 注入 workflow。
- Prompt injection：把 adapted skill 作为 non-prescriptive reference 注入执行 agent。使用历史会反馈回 accumulation 阶段，形成 continual loop。

论文使用两个模型角色：MLLMexec 负责执行任务，MLLMkb 负责知识抽取、合并、检索适配等知识库操作。

## 4. 实验设置

- Benchmark：VisualToolBench、TIR-Bench、MMSearch-Plus、MMBrowseComp、AgentVista，覆盖 visual agentic tool use、multimodal search 和 comprehensive tasks。
- 数据划分：VisualToolBench 100 train / 214 test；TIR-Bench 从 1215 样本中过滤 5 类得到 430 样本，100 train / 200 test；MMSearch-Plus 100 train / 211 test；MMBrowseComp 130 test-only；AgentVista 100 train / 109 test。
- 工具：code interpreter、web search、image search、visit；不同数据集启用不同组合。
- 模型：Gemini-2.5-Pro、Gemini-3-Flash、GPT-5-mini、o4-mini；附录还评估 Qwen3-VL-235B-Instruct 和 Qwen3-VL-32B-Instruct。
- 知识迁移设置：Gemini-2.5-Pro 和 Gemini-3-Flash 使用自身轨迹积累经验和技能；GPT-5-mini 和 o4-mini 直接使用 Gemini-3-Flash 积累的知识来测试 cross-model transfer。
- 指标：N=4 rollouts，报告 Average@4 和 Pass@4。

## 5. 主要实验结果

- 主表平均表现：Gemini-2.5-Pro 上 XSkill Avg Average@4/Pass@4 为 28.63/45.92，高于 w/ Tools 23.87/41.04 和 Agent-KB 24.99/41.37。Gemini-3-Flash 上 XSkill 为 40.34/58.95，高于 w/ Tools 33.63/53.06 和 Agent-KB 34.88/53.71。
- Cross-model transfer：GPT-5-mini 使用 Gemini-3-Flash 知识时，XSkill 平均为 23.19/38.90，高于 w/ Tools 20.61/36.13；o4-mini 为 23.72/39.07，高于 w/ Tools 19.56/33.69。
- 具体任务例子：TIR-Bench + Gemini-3-Flash 上，XSkill Average@4 为 47.75，Agent-KB 为 36.62，提升 11.13 点。
- 消融：VisualToolBench + Gemini-2.5-Pro 上完整 XSkill 为 30.49/46.73；w/o Experience 为 27.45/42.52；w/o Skill 为 26.64/41.12；w/o Experience Manager 为 26.40/42.06；w/o Skill Manager 为 26.87/42.99；w/o Task Decomposition 为 29.21/44.86；w/o Task Adaptation 为 28.97/44.39；w/ Tools 为 25.35/40.65。
- 错误与工具使用分析：从 Experience Only 到 Skill Only，VisualToolBench + Gemini-2.5-Pro 的 execution error rate 从 29.9% (168 errors) 降到 15.3% (95 errors)。Experience 会改变工具选择模式，例如 VisualToolBench 中 code interpreter 使用从 w/ Tools 的 66.63% 增至 Skill & Exp 的 76.97%。
- OOD / zero-shot transfer：论文图 5 文字说明使用 VisualToolBench 知识迁移到 TIR-Bench、MMSearch-Plus 知识迁移到 MMBrowseComp，XSkill 在两个目标 benchmark 和 backbone 上均优于 baselines，平均比 Agent-KB 高 2-3 点；精确图中数值需要从 PDF 图像进一步核对。

## 6. 与我们工作的不同

- XSkill 主要面向 multimodal tool-use agent，核心困难是视觉 grounding、工具编排和跨模态检索；我们当前主要面向数学/推理与 skill repository maintenance，视觉上下文不是核心输入。
- XSkill 不做参数更新，但有较重的 MLLMkb 知识管理流程；我们更关注 frozen executor 下 skill 是否被真实调用、调用是否带来答案或成本收益，以及 skill 库的正确性保持重构。
- XSkill 的 skill 是 Markdown workflow document，experience 是短 action-level prompt；我们当前已有 Python function skill、workflow/history、strategy doc 等多种候选格式，需要系统比较。
- XSkill 的持续学习发生在 experience/skill bank 层面；我们更强调 repository-level maintenance，包括重复 skill 合并、helper extraction、test-preserving rewrite、utility-based validation 和 budgeted selection。

## 7. 对我们的借鉴

- 明确区分 experience 与 skill 很有价值：历史 trace 不一定都应上升为 skill，局部行动建议可以作为 experience bank，稳定跨题 workflow 才进入 skill library。
- Markdown skill document + structured experience bank 的双结构可作为我们 skill format ablation 的一个强 baseline，尤其可与 Python function skill 对比。
- Cross-rollout critique 适合迁移到数学任务：比较同题多次成功/失败 trace，抽取导致答案差异的关键步骤、错误模式和可复用检查。
- Task decomposition retrieval 可缓解单 query 检索过窄问题；对复杂数学题可以先分解为代数、数论、几何、枚举验证、边界检查等检索子需求。
- XSkill 的 open-source 模型结果显示知识迁移并非总是提升 Average@4，弱模型可能被外部知识干扰；这支持我们先做 model/format/use diagnostics，再声称 skill evolution 有端到端收益。
