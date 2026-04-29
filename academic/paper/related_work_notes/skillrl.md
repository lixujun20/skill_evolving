# SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning

## 1. 基本信息

- 论文：SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning
- arXiv：2602.08234v1
- 作者：Peng Xia, Jianwen Chen, Hanyang Wang, Jiaqi Liu, Kaide Zeng, Yu Wang, Siwei Han, Yiyang Zhou, Xujiang Zhao, Haifeng Chen, Zeyu Zheng, Cihang Xie, Huaxiu Yao
- 发布时间：2026-02-09
- 来源核对：arXiv API 摘要与 PDF（https://arxiv.org/abs/2602.08234，https://arxiv.org/pdf/2602.08234v1）
- 代码：论文摘要给出 https://github.com/aiming-lab/SkillRL

## 2. 问题设定与动机

论文关注 LLM agent 在交互式任务中如何从历史经验中持续改进。作者认为已有 memory-based agent 多数直接保存 raw trajectories，轨迹冗长、冗余且含噪，难以抽象出可复用的高层行为模式，也会带来上下文 token 压力。

其目标是在 RL 训练中把历史交互经验蒸馏为可检索、可演化的 skill library，使 agent policy 与 skill library 共同进化。实验环境覆盖 ALFWorld、WebShop，以及七个 search-augmented QA benchmark。

## 3. 核心方法

SkillRL 包含三部分：

- Experience-based skill distillation：从 base agent 的成功和失败轨迹中抽取 skill。成功轨迹用于抽取可迁移策略，失败轨迹用于抽取 failure lesson、错误原因和避免类似错误的原则。
- Hierarchical SkillBank：将 skill 分为 general skills 和 task-specific skills。general skills 总是注入，task-specific skills 通过任务描述与 skill embedding 相似度 Top-K 检索。
- Recursive skill evolution：先通过 cold-start SFT 让模型学会使用 skill，再用 GRPO 做 skill-augmented RL。每个 validation epoch 后，针对低成功率 task category 收集失败轨迹，由 teacher model 发现现有 SkillBank 未覆盖的 failure pattern，新增或细化 skill。

关键设计是 skill 不是静态 prompt memory，而是在 RL 过程中随 agent 探索到的新失败模式递归扩展。论文还强调 skill distillation 相比 raw trajectory 有 10-20x token compression，但该数值来自正文表述，若要引用压缩率的精确测量方式，需要从 PDF 相关图表进一步核对。

## 4. 实验设置

- 环境：ALFWorld、WebShop、七个 search-augmented QA 数据集。
- QA 数据集：NQ、TriviaQA、PopQA、HotpotQA、2Wiki、MuSiQue、Bamboogle；其中 SkillRL 在 NQ 和 HotpotQA 上训练，报告 in-domain 与 out-of-domain 表现。
- Base model：Qwen2.5-7B-Instruct。
- 主要 baseline：GPT-4o、Gemini-2.5-Pro、Qwen2.5、ReAct、Reflexion、Mem0、ExpeL、MemP、SimpleMem、RLOO、GRPO、MemRL、EvolveR、Mem0+GRPO、SimpleMem+GRPO；QA 中还比较 R1-Instruct、Search-o1、Search-R1、ZeroSearch、StepSearch、EvolveR。
- 训练细节：PDF 附录表 4 报告 cold-start SFT 学习率 1e-4、batch size 16、epoch 3；RL 学习率 1e-6、batch size 64、KL loss coef 0.01、invalid action penalty coef 0.1、max prompt length 6000、max response length 1024、epoch 150。

## 5. 主要实验结果

- ALFWorld / WebShop：SkillRL 在 ALFWorld overall success rate 为 89.9%，WebShop score/success 为 85.2/72.7。GRPO 对应为 ALFWorld 77.6%、WebShop 79.3/66.1，因此 SkillRL 在 ALFWorld 上比 GRPO 高 12.3 个百分点。
- 相比 memory-augmented RL：Mem0+GRPO 在 ALFWorld/WebShop success 为 54.7%/37.5%，SimpleMem+GRPO 为 62.5%/46.9%，SkillRL 为 89.9%/72.7%。
- Search-augmented QA：SkillRL 平均分 47.1，Search-R1 为 38.5，ZeroSearch 为 39.1，EvolveR 为 43.1。Bamboogle 上 SkillRL 为 73.8，EvolveR 为 54.4。
- 消融：完整 SkillRL 在 ALFWorld/WebShop 为 89.9/72.7；去掉 hierarchical structure 为 76.8/61.4；用 raw trajectories 替代 skill library 为 61.7/50.2；去掉 cold-start SFT 为 65.2/46.5；去掉 dynamic evolution 为 84.4/70.3。

## 6. 与我们工作的不同

- SkillRL 是 policy learning + skill library co-evolution，核心依赖 SFT 和 GRPO；我们当前更偏 frozen-model/test-time skill evolution、skill repository maintenance、skill-use 诊断与后验价值评估。
- SkillRL 的主环境是交互式 agent 任务和搜索 QA，任务中 action/trajectory/failure step 明确；我们当前主要围绕 AIME/MATH 等数学推理或 TIR-style 执行，显式可复用 skill 的密度和调用接口更不稳定。
- SkillRL 通过训练让模型内化 skill 使用能力；我们更需要先确认 retrieved-to-called conversion，以及不同 skill format 是否真的被 frozen model 使用。
- SkillRL 关注生成与演化 SkillBank；我们更强调已有/新增 skill 的 correctness-preserving refactoring、去冗余、utility estimation 和 budgeted repository optimization。

## 7. 对我们的借鉴

- 失败轨迹值得显式进入 skill extraction，但应转化为 failure lesson 或 counterfactual rule，而不是直接存 raw trace。
- general skill + task-specific skill 的分层结构可以用于我们的 skill format ablation，尤其适合区分通用解题策略、题型策略、可执行代码工具。
- cold-start skill-use training 的结果提示：如果不训练 policy，就必须设计更强的 runtime interface 或诊断机制，否则 prompt 中有 skill 不代表模型会使用。
- dynamic evolution 的触发条件可以迁移为 repository maintenance 信号：只对低收益、低调用、高失败覆盖区域做新增、重写或剪枝，而不是每轮盲目扩库。
