# 同期 Skill Evolution 工作文献笔记索引

本目录维护每篇同期工作的独立中文笔记。每篇笔记统一包含：基本信息、问题设定与动机、核心方法、实验设置、主要实验结果、与我们工作的不同、对我们的借鉴。

## 1. 轨迹到 Skill 蒸馏

| 论文 | 笔记 | 核心定位 | 对我们的直接启发 |
| --- | --- | --- | --- |
| Trace2Skill | [trace2skill.md](trace2skill.md) | 从多条成功/失败轨迹中并行提出 patch，再层级合并成 transferable skill directory | post-execute extraction 应跨多条 trace 做归纳，避免逐条在线编辑导致局部过拟合 |
| SkillX | [skillx.md](skillx.md) | 构建 plug-and-play skill knowledge base，包含 planning / functional / atomic 三层 skill | skill format 不能只做代码函数，应系统比较 planning、functional、atomic 等多粒度表示 |

## 2. 验证与诊断驱动的 Skill 演化

| 论文 | 笔记 | 核心定位 | 对我们的直接启发 |
| --- | --- | --- | --- |
| CoEvoSkills | [coevo_skills.md](coevo_skills.md) | Skill Generator 与 Surrogate Verifier 协同演化多文件 skill package | 需要独立 verifier / judge 提供密集诊断，不能只依赖 extractor 自检 |
| EvoSkill | [evoskill.md](evoskill.md) | 基于失败分析 create/edit structured skill folder，并用 validation frontier 准入 | 应区分训练失败集、验证集和测试集，候选 skill 需要门控而不是直接落库 |
| Memento-Skills | [memento_skills.md](memento_skills.md) | 把 structured markdown skills 作为非参数外部记忆，并训练/评估 skill router | 只看语义检索不够，应评估 route hit rate、called rate 和最终 success |
| SkillForge | [skillforge.md](skillforge.md) | 云技术支持场景中的 domain-grounded skill creation-evaluation-refinement loop | 高反馈密度、专家答案和领域文档能显著降低 skill evolution 的评价噪声 |

## 3. 群体演化与 Skill Repository 治理

| 论文 | 笔记 | 核心定位 | 对我们的直接启发 |
| --- | --- | --- | --- |
| PSN | [psn.md](psn.md) | 把 executable skills 组织成可组合 programmatic network，支持 fault localization、maturity gating 和 rollback refactoring | 强覆盖长期 skill network 维护；我们需要把差异收缩到通用资产协议、unit utility、integration-derived tests 和 repository-level selection |
| SkillMOO | [skillmoo.md](skillmoo.md) | 用 NSGA-II 对 agent skill bundles 做 pass rate / cost 多目标优化 | token/cost 应进入所有 with/without tests；repository selection 需要显式考虑冗余、噪声和预算 |
| SkillClaw | [skillclaw.md](skillclaw.md) | 多用户 agent 生态中聚合跨用户轨迹，夜间演化并同步共享 skill pool | skill repository 应记录完整 action-feedback chain，并采用保守验证后发布 |
| EvoSkill | [evoskill.md](evoskill.md) | 用 Pareto/frontier 与 held-out validation 控制 skill program 增长 | refactor_lab 可定位为 repository maintenance，而不是泛化 skill generation |
| SkillX | [skillx.md](skillx.md) | 通过 merge/filter/update 和主动探索扩展 skill bank 覆盖 | skill 库需要去重、合并、剪枝和覆盖扩展，不能只累积新条目 |

## 4. RL / Policy-Skill 协同

| 论文 | 笔记 | 核心定位 | 对我们的直接启发 |
| --- | --- | --- | --- |
| AgentOptimizer | [agentoptimizer.md](agentoptimizer.md) | 把 agent functions 当作 learnable weights，在不改 LLM 权重的情况下离线优化函数集合 | 说明外部能力层可被训练；我们的差异应是 versioned skill asset、测试驱动维护和群体选择，而不是“首次优化外部函数” |
| SkillRL | [skillrl.md](skillrl.md) | 用 SFT + GRPO 让 policy 学会使用递归扩展的 SkillBank | 如果不训练 policy，就必须单独诊断 frozen model 是否真的会调用 skill |
| D2Skill | [d2skill.md](d2skill.md) | task skill + step skill 双粒度 skill bank，用 paired rollout gap 估计 hindsight utility | 与我们的 Shapley-style skill value 最接近，可借鉴 with-skill / without-skill 对照 |
| XSkill | [xskill.md](xskill.md) | 多模态 agent 中分离 action-level experience 与 task-level skill | 历史 trace 不必都沉淀为 skill，局部经验可作为 experience bank 分层管理 |
| OpenClaw-RL | [openclaw_rl.md](openclaw_rl.md) | 从 next-state signal 中恢复 reward 和 directive supervision 做在线 RL | 即使不更新参数，也应把执行报错、用户反馈、judge 评语作为上一轮 skill 调用反馈 |

## 5. 当前对我们项目的归纳

近期工作已经覆盖泛化的 trajectory-to-skill、verification loop、skill repository、multi-granularity skill bank 和 policy-skill co-evolution。后续论文定位应避免泛泛声称“提出 skill evolution”，而应聚焦更窄的可验证问题：

- environment suitability：哪些任务分布有足够复用密度和非饱和 baseline；
- model skill-use：retrieved skill 是否转化为真实 called skill；
- skill format：code function、strategy doc、workflow card、document + scripts 的差异；
- post-execute cross-trace refactoring：在完整 trace 后做跨历史经验的结构归纳；
- correctness-preserving repository maintenance：抽取、合并、拆分、重写 skill 时保证功能不退化；
- frozen-model skill value：用 answer、token、usage 和 paired ablation 估计 skill 边际贡献。
- budgeted population selection：用有限 skill 预算最大化 valid-set utility，同时惩罚 token/cost、retrieval noise、redundancy 和 maintenance cost。

## 6. 论文专属 Subagent 映射

以下 subagent 已为对应论文完成初始化，可用于后续定向提问。工具层面的 UI nickname 由系统自动分配，实际使用时以本表的论文名和 agent id 为准。

| 论文身份 | Agent ID | 笔记 |
| --- | --- | --- |
| Trace2Skill | `019dd1de-4ea9-7250-a488-98237412bad9` | [trace2skill.md](trace2skill.md) |
| SkillX | `019dd1de-4ff4-7492-ac5e-64569e258282` | [skillx.md](skillx.md) |
| CoEvoSkills | `019dd1df-159d-7ec2-bf0f-a4d9ef61d665` | [coevo_skills.md](coevo_skills.md) |
| SkillClaw | `019dd1df-1716-74b0-8b99-749456353686` | [skillclaw.md](skillclaw.md) |
| EvoSkill | `019dd1df-1731-7351-ae5a-64632bbf2f58` | [evoskill.md](evoskill.md) |
| Memento-Skills | `019dd1df-1754-7743-b615-e1ee8a8231f9` | [memento_skills.md](memento_skills.md) |
| SkillRL | `019dd1e0-2694-78c1-a99b-bd3610c5174f` | [skillrl.md](skillrl.md) |
| D2Skill | `019dd1e0-26b5-73b3-a4d1-094e2d4a0d04` | [d2skill.md](d2skill.md) |
| XSkill | `019dd1e0-26dd-72f2-9e88-c8ed197535d1` | [xskill.md](xskill.md) |
| SkillForge | `019dd1e0-2719-7240-808f-0cc5d4d7d5eb` | [skillforge.md](skillforge.md) |
| OpenClaw-RL | `019dd1e0-2743-7e81-91f1-3d27d0b0241d` | [openclaw_rl.md](openclaw_rl.md) |

注意：如果后续某个 agent 被关闭，可用对应 Agent ID resume，再向它发送该论文相关问题。
