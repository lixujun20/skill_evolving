# CoEvoSkills 文献笔记

## 1. 基本信息

- 标题：CoEvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification
- arXiv：2604.01687
- arXiv 页面：https://arxiv.org/abs/2604.01687
- PDF：https://arxiv.org/pdf/2604.01687
- arXiv API 核对版本：v2，更新于 2026-04-12
- 作者：Hanrong Zhang, Shicheng Fan, Henry Peng Zou, Yankai Chen, Zhenting Wang, Jiayu Zhou, Chengze Li, Wei-Chieh Huang, Yifei Yao, Kening Zheng, Xue Liu, Xiaoxiao Li, Philip S. Yu
- 机构：University of Illinois Chicago; MBZUAI; McGill University; Columbia University; Zhejiang University; University of British Columbia
- 项目页：论文首页注明 https://zhang-henry.github.io/CoEvoSkills/

## 2. 问题设定与动机

论文关注 agent skill 的自主生成。作者指出 tool 是单一自包含函数，而 skill 是由说明、脚本、参考资料等组成的多文件结构化包。人工写 skill 成本高，而且可能存在 human-machine cognitive misalignment：人类专家觉得合理的流程不一定适合 LLM agent 的上下文处理、推理和执行约束。

现有 self-evolving tool 方法多面向单函数或 prompt heuristic，难以直接生成结构化多文件 skill package；依赖 ground-truth failure diagnosis 的方法又不适合真实场景。CoEvoSkills 的目标是在没有 ground-truth test content 的情况下，让 agent 通过 co-evolutionary verification 自主迭代生成高质量多文件技能包。

## 3. 核心方法

CoEvoSkills 由 Skill Generator 和 Surrogate Verifier 两个相互独立的 LLM 会话组成：

- Skill Generator：从任务指令和 meta-skill 出发生成初始多文件 skill bundle，并在持久对话上下文中累积 verifier 的失败诊断，持续修订 `SKILL.md`、脚本和其他 artifacts。
- Surrogate Verifier：不继承 generator 的推理、代码和 skill 内容，只观察任务指令和当前输出文件，独立生成 deterministic assertions/test scripts，对输出进行代理验证，并给出 per-assertion 失败、根因分析和可操作修订建议。
- Co-evolution loop：若 surrogate tests 失败，则固定测试并把诊断反馈给 generator 修 skill；若 surrogate tests 通过但 ground-truth oracle 失败，则 oracle 只返回不透明 pass/fail bit，不暴露测试内容，迫使 verifier 升级测试覆盖。算法设置中 evolution iters `N=5`，surrogate iters `M=15`，上下文使用比例 cap 为 `beta=0.7`。

方法关键点是信息隔离：verifier 不看 generator 的内部过程，降低 self-verification 的 confirmation bias；oracle 只给 opaque signal，降低过拟合 held-out tests 的风险。

## 4. 实验设置

- Benchmark：SkillsBench，包含 87 个任务，约 20 个专业领域；每个任务有 deterministic verifier，评价为二元 pass/fail。
- 主指标：pass rate，即 reward=1.0 的任务比例。
- 主要评估 harness：Claude Opus 4.6 + Claude-Code；另有 GPT-5.2 + Codex。
- Baselines：No-Skill Baseline、SkillsBench Self-Generated Skills、CoT-Guided Self-Generation、Anthropic Skill-Creator、Human Curated Skills，以及 CoEvoSkills。
- baseline comparison 对每个主要方法进行 5 次独立运行并报告均值和标准差；跨模型迁移实验对每个模型进行 3 次独立运行。
- 跨模型迁移：把 Claude Opus 4.6 演化出的 skills 迁移到 GPT-5.2、Claude Sonnet 4.5、Claude Haiku 4.5、Qwen3-Coder-480B、DeepSeek V3-671B、Mistral Large 3-675B。

## 5. 主要实验结果

- Claude Opus 4.6 + Claude-Code 上，CoEvoSkills pass rate 为 71.1%，No-Skill 为 30.6%，提升 +40.5 pp；Human Curated Skills 为 53.5%，CoEvoSkills 高 +17.6 pp。
- 其他 baseline：Anthropic Skill-Creator 34.1%，SkillsBench Self-Generated Skills 32.0%（±3.1），CoT-Guided Self-Generation 30.7%（±5.2）。论文结论是没有 co-evolutionary verification 的技能生成几乎不能显著超过 no-skill。
- GPT-5.2 self-evolved skills 的 pass rate 为 69.8%，no skill 为 29.6%，提升 +40.2 pp。
- Claude Opus 4.6 演化出的 skills 跨模型迁移：GPT-5.2 65.0 vs 29.6（+35.4 pp），Claude Sonnet 4.5 63.1 vs 20.0（+43.1 pp），Claude Haiku 4.5 54.5 vs 10.4（+44.1 pp），Qwen3 Coder 50.8 vs 8.4（+42.4 pp），DeepSeek V3 48.8 vs 13.0（+35.8 pp），Mistral Large 3 43.1 vs 4.9（+38.2 pp）。
- Ablation：去掉 surrogate verifier 后 pass rate 从 71.1% 降到 41.1%（-30.0 pp）；只给 background context、无 skill evolution 为 48.6%（-22.5 pp vs full）；No-Skill 为 30.6%。
- 论文图 2 展示 5 轮演化中 skill quality 上升，并在 5 轮内超过 human-curated skills；每轮具体数值需要从 PDF 图表进一步核对。

## 6. 与我们工作的不同

- CoEvoSkills 面向“为每个任务自主生成多文件 skill package”，而不是从大量历史轨迹中归纳一个通用领域 skill 或维护长期 skill library。
- 它强依赖 surrogate verifier 合成测试和 ground-truth oracle 的 opaque pass/fail；如果我们的环境没有可执行 verifier 或 oracle，方法需要改造。
- 它的演化对象是 skill bundle 的代码/文档/artifacts，强调多文件产物质量；如果我们的工作更偏 declarative skill、prompt skill 或轻量知识条目，则 artifacts 复杂度不同。
- 评估主要在 SkillsBench 上，任务级 deterministic verifier 清晰；开放式任务、非确定性环境或弱评价信号下的适用性仍需验证。

## 7. 对我们的借鉴

- 信息隔离 verifier 是重要设计：让生成器和验证器处于独立会话，能减少自我确认偏差。
- 当真实 oracle 只能给 pass/fail 时，可以让 surrogate verifier 提供密集诊断，再用 opaque oracle 作为防过拟合的最终门控。
- 多文件 skill 生成应采用迭代式 generate-verify-refine，而不是一次性 skill creation。
- 技能质量不只取决于背景知识，结构化 packaging 和可执行验证同样关键；ablation 中 background context alone 明显低于 full framework。
- 可借鉴其跨模型迁移评估：同一组 evolved skills 应测试在不同模型和 harness 上是否仍然有用，以区分 agent-native skill 与模型特定 hack。
