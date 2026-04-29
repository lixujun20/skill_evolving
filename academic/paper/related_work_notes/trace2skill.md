# Trace2Skill 文献笔记

## 1. 基本信息

- 标题：Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills
- arXiv：2603.25158
- arXiv 页面：https://arxiv.org/abs/2603.25158
- PDF：https://arxiv.org/pdf/2603.25158
- arXiv API 核对版本：v3，更新于 2026-03-31；PDF 抽取文本显示 v4，日期为 2026-04-27，说明版本可能已更新，引用具体数值时以 PDF 表格为准。
- 作者：Jingwei Ni, Yihao Liu, Xinpeng Liu, Yutao Sun, Mengyu Zhou, Pengyu Cheng, Dexin Wang, Erchao Zhao, Xiaoxi Jiang, Guanjun Jiang
- 机构：Qwen Large Model Application Team, Alibaba; ETH Zurich; University of Zurich; Peking University; Zhejiang University
- 代码：论文首页注明 https://github.com/Qwen-Applications/Trace2Skill

## 2. 问题设定与动机

论文关注 LLM agent 的技能自动生成与适配。作者认为人工写 skill 可扩展性差，而仅依赖模型参数知识生成 skill 往往浅层、脆弱；在线逐条轨迹更新 skill 又容易把局部经验写成碎片化、过拟合的经验库。

Trace2Skill 的核心问题设定是：给定固定参数的 agent、初始 skill `S0`、演化任务集 `D_evolve` 和测试集 `D_test`，不更新模型参数，只利用 `D_evolve` 上的执行轨迹构造改进后的 skill `S*`，使其在 `D_test` 上成功率高于 `S0`。论文同时研究两种初始化：从人写 skill 继续深化，以及从 LLM 参数知识生成的弱草稿从零创建。

## 3. 核心方法

Trace2Skill 将经验蒸馏分为三阶段：

- 轨迹生成：固定 agent 使用初始 skill 在演化任务上 rollout，产生成功轨迹和失败轨迹；论文主实验中每个问题生成 1 条轨迹。
- 并行多 agent patch proposal：成功分析器从成功轨迹提炼可泛化行为模式，失败分析器以 ReAct 式多轮 agent 循环检查文件、查询 ground truth、定位根因，再提出 skill patch。所有分析器基于同一个冻结的 `S0` 并行运行，避免顺序更新导致的漂移。
- 冲突消解与层次合并：将所有轨迹级 patch 层次化合并为单个 coherent skill update。合并过程包含程序化校验，例如拒绝引用不存在文件、检测同一行编辑冲突、验证 JSON patch 格式。作者将这一过程解释为对大量局部经验的归纳推理。

方法强调产物是可携带的 declarative skill directory，不需要参数更新，也不需要推理时额外检索模块。

## 4. 实验设置

- 主实验领域：spreadsheet agent，使用 SpreadsheetBench-Verified。400 个样本被分为 200 个演化样本和 200 个 held-out 测试样本。
- OOD 泛化：WikiTableQuestions，转换为 spreadsheet 格式后评测 xlsx skill。
- 其他领域：数学推理使用 DAPO-Math-Train-400 演化，DAPO-Math-Test-100 和 AIME 2026 测试；VQA 使用 DocVQA，官方 validation split 中前 2700 个样本作为演化集，后 2649 个作为测试集。
- 模型：Qwen3.5-122B-A10B 与 Qwen3.5-35B-A3B；同一 LLM 同时作为轨迹生成器、patch proposer 和 skill editor。
- 主要对比：No Skill、人写 Anthropic xlsx skill、参数知识生成的 xlsx-basic、Trace2Skill 的 +Error、+Success、+Combined；还比较并行合并 vs 顺序编辑、Trace2Skill vs ReasoningBank、agentic error analysis vs 单次 LLM error analysis。

## 5. 主要实验结果

- Spreadsheet 主表中，人写 xlsx skill 对 122B agent 很强，但对 35B agent 不稳定：122B 上 SprBench-Vrf 从 No Skill 27.67 提升到 Human-Written 48.33；35B 上 Human-Written 的 SprBench-Vrf 为 9.67，低于 No Skill 的 19.00。
- Deepening 模式能强化人写 skill。122B-authored Deepening +Combined 在 122B 使用时 SprBench-Vrf 相对 Human-Written +21.50 pp；122B-authored Deepening +Error 在 35B 使用时 SprBench-Vrf +27.00 pp。
- Creation 模式能从弱 parametric skill 中恢复能力。论文报告 35B-authored Creation +Error 在 122B 使用时 WikiTQ 相对 Parametric +57.65 pp，达到 81.38，并超过 Human-Written。
- 数学推理中，122B-authored +Error 相对 No Skill 在 DAPO-Math-Test-100 +3.0 pp、AIME 2026 +2.9 pp；迁移到 35B 时 DAPO-Math-Test-100 +5.0 pp、AIME 2026 +5.0 pp。
- DocVQA 中，122B-authored +Error 对 122B 提升 +0.1639 ANLS 和 +15.3 pp accuracy，对 35B 提升 +0.1554 ANLS 和 +13.6 pp accuracy；35B-authored skill 在同模型 35B 上反而下降 -0.0620 ANLS 和 -6.2 pp accuracy。
- 并行合并比顺序编辑更高效：Spreadsheet same-model Deepening +Error 中，Parallel 在 122B 上 Vrf 65.83，高于 Seq-B=1 的 61.83 和 Seq-B=4 的 59.00；耗时约 3 分钟，低于 Seq-B=1 的约 60 分钟和 Seq-B=4 的约 15 分钟。
- 相比 ReasoningBank，Human-Written+Combined 在 same-model Deepening 的 Spreadsheet 上更强：122B Vrf 69.83 vs 56.00，35B Vrf 29.67 vs 20.50。

## 6. 与我们工作的不同

- Trace2Skill 是 batch/offline 轨迹池蒸馏：先收集大量轨迹，再一次性并行分析和层次合并；如果我们的工作关注持续在线演化或技能生命周期管理，则问题时序不同。
- 它主要把经验写入单个综合 skill directory，刻意避免推理时检索；如果我们的工作使用 skill bank、retrieval 或多技能路由，则 skill 组织方式不同。
- 失败分析器可访问执行文件和 ground truth 以定位根因；如果我们的设定中没有标注答案或 verifier，则反馈条件更弱。
- 论文关注从轨迹局部 lessons 到 declarative skill 的归纳合并，不涉及模型参数训练或显式技能效用追踪。

## 7. 对我们的借鉴

- 可以借鉴“同一冻结初始 skill + 并行分析 + 层次合并”的设计，减少顺序编辑带来的上下文漂移和早期错误固化。
- 对失败轨迹不应只做单次总结，agentic error analysis 通过查看文件、复现和定位根因，能产出更可靠的 patch。
- skill 合并需要程序化约束：文件存在性、编辑冲突、格式校验、冗余去重都应成为流水线的一部分，而不是完全交给 LLM。
- 评估时应区分 in-distribution、OOD 和 cross-model transfer，否则容易把局部过拟合误判为 skill 质量提升。
- 成功轨迹有价值但波动较大，可考虑先用失败轨迹作为安全默认信号，再设计更严格的成功经验筛选机制。
