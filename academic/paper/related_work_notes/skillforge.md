# SkillForge 文献笔记

## 1. 基本信息

- 论文：SkillForge: Forging Domain-Specific, Self-Evolving Agent Skills in Cloud Technical Support
- arXiv：2604.08618v1
- 作者：Xingyan Liu, Xiyue Luo, Linyu Li, Ganghong Huang, Jianfeng Liu, Honglin Qiao
- 机构：Alibaba Cloud Computing, Alibaba Group
- 时间：2026-04-09
- 发表信息：Accepted at ACM SIGIR 2026 Industry Track
- 主题：企业云技术支持中的领域化 Agent Skill 自动创建、执行评估与自演化优化
- 主要来源：arXiv API 摘要与 arXiv PDF 原文核对

## 2. 问题设定与动机

论文关注云技术支持场景中的 LLM Agent。Agent 面向客户工单生成可交付回复，人类支持工程师再决定是否采纳。该场景需要稳定的领域流程、工具使用规范和组织内部知识，而通用技能生成器缺少领域 grounding，生成的初始 skill 往往与真实任务不匹配。

作者指出两个核心问题：第一，冷启动阶段缺少基于历史工单、知识库和工具调用记录的领域化 skill 生成机制；第二，部署后虽然会积累失败案例，但缺少把执行失败追溯到 skill 缺陷并定向改写 skill 的系统闭环。因此，SkillForge 的目标不是一次性优化 prompt，而是维护一个可版本化、可诊断、可持续改写的 skill artifact。

## 3. 核心方法

SkillForge 是一个 creation-evaluation-refinement 闭环，包含两部分。

第一部分是 Domain-Contextualized Skill Creator，用于生成初始 `Skill_v0`。输入包括任务描述、历史工单数据和技术文档。生成过程包括：从历史工单中挖掘典型解决流程；从操作日志中挖掘高频工具及其 schema；从内部知识库、官方文档或工单引用链接中抽取领域知识；最后填充预定义的云服务 skill 模板。

第二部分是自演化循环。Agent 加载当前 `Skill_v_n` 处理任务，输出与历史工单中的专家参考回复通过 LLM-judge 比较，低一致性的案例被标记为 bad cases。随后进入三阶段优化：Failure Analyzer 从 Knowledge、Tool、Clarification、Style 四个维度并行分析失败；Skill Diagnostician 将聚合后的失败模式映射到 `SKILL.md` 或 reference 文件中的具体缺陷；Skill Optimizer 通过受控 Virtual File System 按优化计划最小化修改 skill，生成 `Skill_v_{n+1}`。

值得注意的是，论文采用受限版 Agent Skill：不允许任意执行 `scripts/`，而是只通过预定义、验证过的系统工具，并用 `references/tools.json` 存储工具 schema。这是出于企业生产环境安全性和稳定性的考虑。

## 4. 实验设置

实验使用某大型云厂商的 5 个真实云技术支持场景：Account、Domain、DNS、OSS、ECS。数据总量为 1,883 个匿名生产工单和 3,737 个任务。任务被定义为工单中的一个单轮对话输入，Agent 根据消息历史和当前用户问题生成客户回复。数据划分为 evolution 使用的 development set 和未见过的 held-out evaluation set。

评价指标是 LLM-judge 的 Consistency Rate，与专家历史回复对比。Strict CR 只统计 Consistent；Lenient CR 统计 Consistent 或 Partially Consistent。论文称在采样子集上与人工标注的一致率超过 90%。

对比对象包括：

- `S_generic`：Claude Code + Claude-Sonnet-4.5 生成的通用 skill，包含从历史工单挖掘出的工具 schema，但不访问领域知识或历史工单内容。
- `S_domain`：Domain-Contextualized Skill Creator 生成的初始 skill。
- `S_manual`：领域专家手写初始 skill。

所有实验使用 Qwen3-Max 作为 backbone LLM，每次离线评估重复 3 次并报告均值。

## 5. 主要实验结果

数据规模：5 个场景共 1,883 tickets、3,737 tasks。其中 Account 为 389 tickets / 706 tasks，Domain 为 527 / 1061，DNS 为 256 / 572，OSS 为 385 / 730，ECS 为 326 / 668。

RQ1 中，`S_domain` 在 5 个场景上均优于 `S_generic`。Strict CR / Lenient CR 分别为：

- S1 Account：`S_generic` 58.0 / 61.4，`S_domain` 64.0 / 65.7
- S2 Domain：`S_generic` 57.9 / 64.4，`S_domain` 60.5 / 68.8
- S3 DNS：`S_generic` 63.0 / 69.6，`S_domain` 65.4 / 70.6
- S4 OSS：`S_generic` 59.1 / 66.3，`S_domain` 62.5 / 72.7
- S5 ECS：`S_generic` 43.4 / 55.3，`S_domain` 50.6 / 57.2

论文报告平均提升为 +4.3pp Strict CR 和 +3.6pp Lenient CR，最大 Strict CR 提升出现在 S5，为 +7.20pp。

RQ2 中，从不同初始 skill 出发经过 3 轮演化后，held-out evaluation set 上 Strict CR / Lenient CR 的相对提升为：

- `S_manual`：v1 +4.09 / +5.39，v2 +9.64 / +9.94，v3 +10.99 / +12.21
- `S_domain`：v1 +2.36 / +0.24，v2 +7.31 / +4.13，v3 +9.23 / +8.00
- `S_generic`：v1 +7.70 / +1.60，v2 +8.40 / +4.60，v3 +11.60 / +4.90

失败类别分析显示 Tool、Style、Clarification 类失败持续下降；Knowledge 类失败在 v1 后趋于平台期，作者认为这可能受底层知识库和检索工具覆盖范围限制。

论文还报告，v3 相比生产 legacy system 的 Strict CR 高 +13.76pp。该 legacy system 由预定义决策树流程和长期人工调优专家 prompt 组成。

## 6. 与我们工作的不同

SkillForge 面向企业云技术支持，核心评价是客户回复与历史专家回复的一致性；我们的工作更关注可泛化的问题求解 skill 生成、复用和演化，任务形态不一定有工单式专家参考答案。

SkillForge 的演化对象是文件化的 enterprise skill artifact，包含 `SKILL.md`、reference 文档和受控工具 schema；如果我们的 skill 更偏数学/推理策略或跨任务经验抽象，就不一定依赖领域文档、工具 schema 和工单流程挖掘。

SkillForge 主要使用 LLM-judge 发现 bad cases，并通过多维故障归因驱动文本级 skill 重写；它没有对模型参数做 RL 或 SFT，也没有重点研究 retrieval 后的 skill 选择、相似题复用和跨任务迁移。

SkillForge 的生产约束更强：禁止任意脚本执行，通过 VFS 修改 skill，强调安全、稳定和可追溯；我们的系统如果允许执行代码或工具生成，安全边界和可验证性设计需要另行处理。

## 7. 对我们的借鉴

可以借鉴其“失败案例 -> 结构化失败记录 -> 聚合诊断 -> 定向改写 skill”的闭环，而不是直接让 LLM 看单个失败样例自由改 skill。尤其是 Knowledge、Tool、Clarification、Style 这种多维归因，可以改造成适合我们任务的维度，例如解题策略缺失、检索误匹配、执行错误、反思不足、答案格式错误等。

可以借鉴其 held-out evaluation 与 development evolution 分离的设置，避免 skill 在演化集上过拟合。每轮 skill 版本应保留，并记录 bad cases、诊断报告、优化计划和最终 diff，便于后续分析哪些修改真正有效。

可以借鉴“最小化修改”原则。skill 演化不应每轮重写整份 skill，而应将失败模式定位到具体章节或规则，再局部增补、删除或改写，以降低破坏已有正确行为的风险。

可以借鉴其冷启动思路：初始 skill 不只由模型总结生成，而应从成功轨迹、失败轨迹、工具调用或参考解中挖掘流程模式、常见错误和可复用知识。
