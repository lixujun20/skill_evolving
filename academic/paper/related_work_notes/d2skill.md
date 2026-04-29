# D2Skill: Dynamic Dual-Granularity Skill Bank for Agentic RL

## 1. 基本信息

- 论文：Dynamic Dual-Granularity Skill Bank for Agentic RL
- arXiv：2603.28716v1
- 作者：Songjun Tu, Chengdong Xu, Qichao Zhang, Yaocheng Zhang, Xiangyuan Lan, Linjing Li, Dongbin Zhao
- 发布时间：2026-03-30
- 来源核对：arXiv API 摘要与 PDF（https://arxiv.org/abs/2603.28716，https://arxiv.org/pdf/2603.28716v1）
- 项目页：论文首页给出 https://github.com/TU2021/D2Skill-AgenticRL

## 2. 问题设定与动机

D2Skill 研究 agentic RL 中如何利用可复用经验，并指出已有 skill-based 方法多关注 trajectory-level guidance，缺少对 evolving skill memory 的维护机制，也难以在单步错误修正层面提供细粒度支持。

论文的核心动机是：agentic RL 需要同时拥有高层任务指导和局部决策纠错能力，并且 skill bank 本身应在训练过程中动态扩展、估值、检索和剪枝，而不是作为固定外部记忆。

## 3. 核心方法

D2Skill 的 skill bank 是双粒度结构：

- Task skills：面向任务级别的高层 guidance。
- Step skills：面向交互步骤的细粒度决策支持和错误纠正。

训练时，同一个 policy 对每个 task group 采样两组 rollout：

- baseline group：不注入 skill。
- skill group：检索并注入 skill。

两组在同一 policy 下的 performance gap 被用作 hindsight utility signal。task-level signal 使用 skill group 与 baseline group 的成功率差；step-level credit 使用 skill-injected trajectory 成功与 baseline 平均成功率的差。skill utility 通过 EMA 更新，并进一步用于检索排序和剪枝。

Skill generation 由 reflection 触发：当某个 task group 表现低于阈值时，从失败轨迹和可用成功轨迹中反思，最多生成一个 task skill 和一个 step skill。检索采用两阶段机制：先按 query key 和 retrieval key 的 embedding 相似度筛选候选，再结合相似度、utility 和 UCB-style exploration bonus 排序。超过容量时按 utility-aware eviction score 剪枝，并保护新创建 skill 一段时间避免过早删除。

## 4. 实验设置

- 环境：ALFWorld 和 WebShop。
- 模型：Qwen2.5-7B-Instruct、Qwen3-4B-Instruct-2507，以及 Qwen3-4B-Instruct-2507 + SFT。
- 训练：表 1 注明默认每个环境训练 160 steps，每 5 steps 在 128 validation tasks 上评估一次，报告整个训练过程中的最佳表现。Qwen2.5-7B-Instruct 沿用 SFT-initialized model 以保证 skill usage；Qwen3-4B-Instruct-2507 直接使用原 instruct model。
- 比较对象：Gemini-3-Flash、O3、Origin、GRPO、Mem0+GRPO、SimpleMem+GRPO、SkillRL，以及 D2Skill 的 Gemini-3-Flash/O3 reflector variants。
- 消融：在 ALFWorld + Qwen3-4B-Instruct-2507 上评估 w/o task skills、w/o step skills、w/o skill management、w/o baseline group、w/o utility retrieval、w/o utility module、w/o skills (GRPO)。

## 5. 主要实验结果

- Qwen2.5-7B-Instruct：GRPO 在 ALFWorld/WebShop 为 75.0 overall success、86.0 score / 72.6 success；D2Skill(Gemini-3-Flash) 为 90.6、91.1/80.5；D2Skill(O3) 为 87.8、90.1/84.4。论文正文指出 D2Skill(Gemini-3-Flash) 在 ALFWorld 比 GRPO 高 15.6 点，D2Skill(O3) 在 WebShop success 达 84.4。
- Qwen3-4B-Instruct-2507：GRPO ALFWorld overall 为 53.9；D2Skill(Gemini-3-Flash) 为 69.6；D2Skill(O3) 为 72.7。
- Qwen3-4B-Instruct-2507 + SFT：GRPO(120-Steps) 在 ALFWorld/WebShop 为 92.9、88.2/79.9；D2Skill(120-Steps) 为 95.3、89.2/81.3。D2Skill(40-Steps) ALFWorld 为 92.2，接近 GRPO 120 steps 的 92.9。
- 消融：完整 D2Skill validation success 为 72.7；w/o task skills 为 62.7；w/o step skills 为 60.2；w/o skill management 为 57.8；w/o baseline group 为 68.8；w/o utility retrieval 为 64.8；w/o utility module 为 62.5；w/o skills (GRPO) 为 53.9。
- 训练成本：ALFWorld + Qwen3-4B-Instruct-2507 + 8xH100 上，GRPO 20.8h (1.0x)，SkillRL 49.2h (2.4x)，D2Skill 25.6h (1.2x)。

## 6. 与我们工作的不同

- D2Skill 明确是 agentic RL 方法，用 paired rollout 的 success gap 直接提供 policy optimization 和 skill utility 信号；我们当前更偏不训练或少训练 policy 的 test-time skill repository 机制。
- D2Skill 的 task/step skill 依赖交互环境中的 task id、observation、failure step；数学推理任务中 failure step 和 observation 不天然结构化，需要从 reasoning trace、代码执行、答案验证中构造类似信号。
- D2Skill 用外部 reflector LLM 生成 skill，同时用 utility-aware pruning 管理 bank；我们更强调 skill 的可执行正确性、refactoring 后语义保持、以及 skill set 层面的 coverage/redundancy/utility trade-off。
- D2Skill 将 skill 使用放入训练闭环解决“会不会用”的问题；我们需要在 frozen agent 中单独度量“检索到了是否调用、调用后是否提升或降本”。

## 7. 对我们的借鉴

- 双粒度 skill bank 很适合迁移：数学/推理中可区分题型级 strategy skill 与步骤级 correction/check skill，例如代数变形陷阱、取模边界、代码枚举验证模板。
- Paired baseline/skill rollout 是估计 skill 边际价值的强信号。即使不做 RL，也可以用于我们的小规模 Shapley-style 或 A/B replay，估计 answer improvement、token reduction 和 usage contribution。
- Utility-aware retrieval/pruning 可直接启发 repository maintenance：skill 不应只按 embedding 相似度检索，也应考虑历史收益、调用次数、失败覆盖和冗余度。
- 新 skill 保护期值得借鉴，避免刚生成但尚未充分评估的 skill 被短期噪声删除。
