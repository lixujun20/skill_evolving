# EvoSkill: Automated Skill Discovery for Multi-Agent Systems

## 1. 基本信息

- 论文：EvoSkill: Automated Skill Discovery for Multi-Agent Systems
- arXiv：2603.02766v1，2026-03-03
- 作者：Salaheddin Alzubi, Noah Provenzano, Jaydon Bingham, Weiyuan Chen, Tu Vu
- 机构：Sentient, Virginia Tech
- 代码：<https://github.com/sentient-agi/EvoSkill>
- 任务定位：通过失败分析自动发现和细化 agent skills，把能力更新落到结构化、可复用的技能文件夹，而不是只优化 prompt 或底层代码。

## 2. 问题设定与动机

通用 coding agents 具备灵活性，但不天然具备特定领域任务所需的专业流程。已有 agent skills 多依赖人工编写；已有演化方法常优化 prompt 或代码等低层 artifact，容易与具体模型和任务耦合。

EvoSkill 关注的问题是：在冻结底层模型的前提下，能否通过迭代分析执行失败，自动提出新技能或修改已有技能，并只保留能在验证集上提升性能的技能，从而获得可迁移的高层能力。

## 3. 核心方法

- 失败驱动演化：从训练集执行失败中采样失败案例，proposer 分析失败原因，提出新技能或编辑已有技能。
- 结构化技能物化：候选能力被写入结构化 skill folder，作为 agent 可复用 artifact。
- 冻结模型：底层模型保持不变，性能提升来自技能层面的外部能力积累。
- Pareto/frontier 选择：维护 top-k agent programs 的 frontier；每轮从 frontier 选择 parent，生成 child program，并在 held-out validation set 上评估。
- 只接受验证提升：候选 program 只有在验证集表现足够好时进入 frontier，避免训练失败集上的过拟合式修改直接污染技能库。
- Git-backed program 管理：附录描述用 git branch/tag 记录 program lineage、配置、系统提示、工具权限和评估元数据。

## 4. 实验设置

- 主模型：Claude Code with Opus 4.5。
- OfficeQA：美国财政部公告文档上的 grounded reasoning benchmark，约 89,000 页文档，246 个问题；划分为训练集、17 个样例验证集和 held-out test；训练比例测试 5%（12 例）、10%（24 例）、15%（36 例），每个演化 1.5 epochs。
- OfficeQA 评分：使用 OfficeQA fuzzy scorer，报告多个数值容差阈值；主评价为 0% tolerance exact match。
- SealQA：搜索增强 QA，web 检索结果可能冲突、嘈杂或无帮助；使用 seal-0 split，共 111 个问题，10% 训练 split，1.5 epochs。
- Zero-shot transfer：把 SealQA 学到的 `search-persistence-protocol` 不加修改迁移到 BrowseComp，在 128 个分层样本上评估。

## 5. 主要实验结果

OfficeQA exact match：

- baseline：60.6%。
- 5% 训练数据：63.4%，提升 +2.8。
- 10% 训练数据：65.8%，提升 +5.2。
- 15% 训练数据：64.5%，低于 10% split，论文认为可能存在收益递减或轻微过拟合。
- merge-unique skills：表格中 0.00% tolerance 为 68.1%，正文/摘要写作 67.9% exact match，均对应约 +7.3 的最佳提升；该处数值存在轻微不一致，需要从 PDF 表格进一步核对最终采用值。

OfficeQA 多容差表格中，baseline 在 0.10%、1.00%、5.00%、10.00% 容差下分别为 66.3、72.8、77.2、79.7；merge-unique 分别为 70.8、77.1、80.5、82.4。

SealQA：

- accuracy 从 26.6% 提升到 38.7%，绝对提升 +12.1。
- 代表性技能 `search-persistence-protocol` 要求扩展检索词解释、多源验证和完整性检查，以避免初始搜索噪声导致过早停止。

BrowseComp zero-shot transfer：

- 将 SealQA 上演化出的技能不加修改迁移到 BrowseComp，accuracy 从 43.5% 提升到 48.8%，绝对提升 +5.3。

## 6. 与我们工作的不同

- EvoSkill 直接以 agent skill folder 作为演化对象，并用 frontier 管理 agent programs；我们的工作若已有独立 skill store/embedding 检索机制，需要区分“技能内容演化”和“检索/调用策略演化”。
- EvoSkill 的 benchmark 主要是 OfficeQA、SealQA、BrowseComp，覆盖文档表格推理和开放网页搜索；我们的工作可能更关注数学推理或特定数据集上的技能复用。
- EvoSkill 依赖 held-out validation frontier 来选择候选技能，偏 AutoML/程序搜索风格；我们的实验如果只比较演化前后，需要补上验证门控以增强可信度。
- EvoSkill 把技能迁移作为重要证据，特别是 SealQA -> BrowseComp；我们的工作若要证明技能不是记忆训练题，应设计跨数据集或跨题型迁移评估。

## 7. 对我们的借鉴

- 失败分析 prompt 应要求 proposer 给出“该失败暴露了什么可复用技能缺口”，而不是只修当前题。
- 可以维护多个候选技能库/frontier，避免单一路径早期错误技能影响后续演化。
- 技能合并值得单独实验：独立 run 发现的技能可能互补，merge-unique skills 在 OfficeQA 上取得最佳结果。
- 需要在论文中清晰区分训练失败集、验证集和测试集，否则技能演化容易被质疑为测试集污染。
- 对搜索类任务，可借鉴 `search-persistence-protocol`：强制多源验证、检索词扩展和停止条件检查。
