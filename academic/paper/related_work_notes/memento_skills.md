# Memento-Skills: Let Agents Design Agents

## 1. 基本信息

- 论文：Memento-Skills: Let Agents Design Agents
- arXiv：2603.18743v1，2026-03-19
- 作者：Memento-Team（arXiv API 列出 Huichi Zhou, Siyuan Guo, Anjie Liu, Zhongwei Yu, Ziqin Gong 等多位作者）
- 代码：<https://github.com/Memento-Teams/Memento-Skills>
- 任务定位：把技能作为持久化、可演化的外部记忆，使通用 agent 能通过经验自动构造、适配和改进任务特定 agent。

## 2. 问题设定与动机

Memento-Skills 试图解决“无需更新 LLM 参数的持续学习”问题。系统把可复用技能存为结构化 Markdown 文件，技能既编码行为也编码上下文，作为 stateful prompt 的外部记忆。

与依赖人工设计 agent 的方法不同，Memento-Skills 将 agent 设计本身交给 agent：从少量基础技能（如 Web search、terminal operations）出发，通过交互、反馈、反思和技能写入，不断扩展技能库，让系统为新任务设计更合适的任务型 agent。

## 3. 核心方法

- Read-Write Reflective Learning：每次交互执行 Observe -> Read -> Act -> Feedback -> Write。
- Read 阶段：skill router 根据当前任务和状态化 prompt 选择最相关技能；若启用 CreateOnMiss，路由 miss 时可以创建新技能。
- Act 阶段：冻结的 LLM 使用当前输入和选中技能执行多步 workflow。
- Feedback 阶段：judge 根据任务、agent 输出和参考答案给出反馈。
- Write 阶段：根据反馈更新技能效用、写入 tip memory、选择目标技能、发现新技能或原地优化旧技能；可用 unit-test gate 验证并在失败时回滚。
- 行为对齐路由器：作者认为 BM25 或普通 embedding 更偏语义相似，不一定匹配“执行后能成功”的行为相似；因此基于 Qwen3-Embedding-0.6B 训练 Memento-Qwen，使用合成 query-skill pair 和 hard negatives 做多正样本 InfoNCE/单步离线 RL 式训练。

## 4. 实验设置

- 底层 LLM：论文实验使用 Gemini-3.1-Flash。
- GAIA：使用 validation set 中 165 个问题，划分为 100 个训练样例和 65 个测试样例；最多 3 轮 reflective retries。
- HLE：从 Humanity's Last Exam 采样 788 个训练问题和 342 个测试问题，覆盖 8 个学科类别。
- Baseline：Read-Write ablation，保留技能检索、LLM 执行和反馈收集，但关闭技能级优化、失败归因、技能重写和技能发现。
- Router 实验：使用约 8k 本地技能库，随机约 3k 技能作为种子合成 routing goals；在 140 个 synthetic routing queries 上报告 Recall@K，并用真实执行轨迹报告 route hit rate 和 judge success rate。

## 5. 主要实验结果

Router：

- Synthetic Recall@1：BM25 0.32，Qwen3 embedding 0.54，Memento-Qwen 0.60。
- Synthetic Recall@5：BM25 0.47，Qwen3 embedding 0.79，Memento-Qwen 0.82。
- Synthetic Recall@10：BM25 0.53，Qwen3 embedding 0.86，Memento-Qwen 0.90。
- Real trajectory route hit rate：BM25 0.29，Qwen3 embedding 0.53，Memento-Qwen 0.58。
- Real trajectory judge success rate：BM25 0.50，Qwen3 embedding 0.79，Memento-Qwen 0.80。

GAIA：

- 训练成功率从 first try 的 65.1% 提升到 Round 3 的 91.6%。
- 测试集 overall accuracy：Read-Write baseline 52.3%，Memento-Skills 66.0%，提升 +13.7 个百分点。
- 摘要中将 GAIA overall accuracy 表述为 26.2% relative improvement。
- 作者指出 GAIA 训练峰值与测试表现差距较大，原因是 GAIA 问题多样，训练中优化的许多技能在测试中没有被触发。

HLE：

- 训练 overall success rate 从 R0 的 30.8% 提升到 R3 的 54.5%。
- 测试集 overall accuracy：Read-Write baseline 17.9%，Memento-Skills 38.7%，提升 +20.8 个百分点。
- 摘要中将 HLE overall accuracy 表述为 116.2% relative improvement。
- 技能迁移在 HLE 更明显，因为学科 taxonomy 让同一领域内的问题更容易复用技能。

技能库规模：

- 从相同的 5 个 atomic seed skills 出发，GAIA 学习后技能库增长到 41 个技能。
- HLE 学习后技能库增长到 235 个技能，覆盖更宽的学科能力空间。

## 6. 与我们工作的不同

- Memento-Skills 将技能等同于外部记忆，并带有较强的理论框架（Stateful Reflective Decision Process、Read-Write Reflective Learning）；我们的工作更可能强调具体任务上的技能演化 pipeline 和经验性效果。
- Memento-Skills 的核心组件之一是行为对齐 router；如果我们的系统使用 embedding 检索但未训练 router，需要承认检索质量可能限制技能收益。
- Memento-Skills 允许 agent 自主“设计 agent”，范围更广，包括任务型 agent 构造、技能库扩张和路由器训练；我们的工作若聚焦解题技能，应避免声称过宽。
- Memento-Skills 的实验使用 GAIA/HLE，强调通用助手和广域学科推理；我们的结果若来自数学/代码/问答单域，需要在相关工作中说明更窄但更可控。

## 7. 对我们的借鉴

- 技能可以被建模为可检索、可更新的外部记忆，而非静态 prompt 片段；这有助于组织“经验 -> 技能 -> 后续调用”的闭环叙事。
- 路由质量需要单独评估：不仅看 Recall@K，还要看 route hit rate 和最终 trajectory success。
- 技能写入应有效用统计和触发阈值，避免每次失败都创建新技能导致库膨胀。
- 可以区分 tip memory 与 durable skill：一次性经验先进入轻量记忆，只有稳定可复用的模式才沉淀为技能。
- 跨任务迁移依赖领域结构；在设计实验时应选择有共享题型/共享领域结构的数据集，或明确说明技能无法触发时不应期待迁移收益。
