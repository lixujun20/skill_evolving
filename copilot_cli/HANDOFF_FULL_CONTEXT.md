# Full Handoff Context

本文件用于把当前项目的历史会话内容压缩成一个可读、可直接接手的单一入口。目标是让下一个 agent 在不重新翻聊天记录的情况下，立即理解：

- 项目的长期目标与当前研究定位
- 系统与代码的主要结构
- 已完成的实现与实验
- 当前最可信的结论
- 仍未完成的事项
- 接下来应按什么顺序继续
- 相关文档、代码、结果文件应去哪里看

本文件不是替代所有原始文档，而是它们的总索引与高密度摘要。需要更细节时，按文末路由跳转。

---

## 1. 项目目标

项目目标是研究一个 **test-time skill evolution / workflow reuse** 系统：在 train 阶段从求解轨迹中提炼可复用经验，维护成显式 skill / workflow 库；在 test 阶段检索并使用这些经验，以提升后续任务的正确率、稳定性或成本效率。

当前主线不是训练模型参数，而是在 **frozen model** 设定下研究：

1. 什么环境真的适合 skill reuse；
2. 什么模型会真实使用外部 skill；
3. 什么 skill format 合理；
4. 如何做 post-execute extraction / refactoring / repository maintenance；
5. 如何对 skill 进行 feedback-driven 评估与筛选。

---

## 2. 当前研究定位

项目早期曾直接在 `academic` 主循环中做端到端实验，但后来发现 full-loop 结果太容易被多种因素混淆：

- executor 形式
- 模型本身
- 检索方式
- skill 暴露接口
- token / timeout 预算
- extraction / refine / test 细节

因此当前阶段把问题拆成两条更可控的主线：

1. `refactoring_lab`
   - 研究 skill repository maintenance
   - 核心问题：能否从已有 skills 中发现共享结构、正确重构并压缩表示

2. `benchmark`
   - 研究在外部 agentic benchmark 上，模型是否会使用 skill，以及什么 skill format / 注入方式有效
   - 当前重点是 `BFCL-v3`

同时，`academic` 主系统本身仍在维护，用于：

- workflow history / workflow summary 注入
- planner-aware execution
- execute 后 extract / test / refine / retest 闭环
- replay benchmark

---

## 3. 高层方法主张

当前高层主张已经从“预先强制提取 shared skill 并要求下一题复用”转向更保守的路线。

### 3.1 对 planning / extraction 的最新理解

之前引入 pre-execute planning，是希望在 execute 之前就决定要不要复用某个历史 skill 或 workflow 片段。但后续重新思考后，认为这条路线存在问题：

- 没有本次完整 trace 时，很难精准判断该复用什么
- 可能额外消耗大量 token
- 对交互式任务尤其不稳定

当前更认可的路线是：

1. 先执行当前 query；
2. 拿到完整 trace、执行结果、token 成本；
3. 在 extract 阶段综合：
   - 本次 trace
   - 本次结果
   - 历史相似 query 的 trace / skill / workflow
4. 再决定：
   - 是否提取新 skill
   - 是否重构已有 skill
   - 是否只是保留为 workflow summary

也就是说，**skill extraction 现在更像 post-execute repository maintenance，而不是 pre-execute 强制计划产物**。

### 3.2 对 skill 形式的最新理解

项目不再认为 skill 必须是“可调用的 Python 函数”。

当前认为 skill 可能至少包括：

- code function
- natural language strategy / hint
- workflow card
- document + scripts
- 仅是某个场景下的关键事实或注意事项

因此后续所有 benchmark / system 设计，都应允许 **多样 skill format**，而不是只支持代码函数。

---

## 4. 代码与系统结构

### 4.1 长期记忆与文档入口

目录：`/home/lixujun/skill_evolving/copilot_cli`

主要文件：

- `AGENT.md`
  - 长期稳定约束
  - 不放项目细节
- `progress.md`
  - 每次计划、执行记录、阶段结果、阻塞项
- `academic_doc.md`
  - `academic` 主实验线设计、结论、协议、文档路由
- `DESIGN.md`
  - `skill_evolving_v1`
- `DESIGN_V2.md`
  - `skill_evolving_v2`

### 4.2 `academic` 主系统

重要代码：

- `academic/skill_store.py`
  - 存 workflow / skill / history 相关数据
- `academic/planner.py`
  - execute 前的 lightweight planning artifact
- `academic/executor.py`
  - 主求解执行逻辑
  - 支持 `tir | oneshot`
  - 记录 runtime skill call counting
- `academic/pipeline.py`
  - 主流程封装
- `academic/experiments/run_experiment.py`
  - 真实实验入口
- `academic/webapp/`
  - 前端研究环境
  - 包含 replay benchmark lab 和 execute lab

### 4.3 `refactoring_lab`

重要代码与文档：

- `academic/refactoring_lab/PAPER_STAGE_REPORT.md`
  - 对外阶段性汇报
- `academic/refactoring_lab/DEV_HISTORY.md`
  - 内部开发历史
- `academic/refactoring_lab/planning_replay_benchmark.py`
  - replay benchmark 主实现
- `academic/refactoring_lab/experiments/`
  - replay cases、drafts、benchmark results、调试产物

### 4.4 `benchmarks`

目录：`academic/benchmarks/`

当前 registry 中的 benchmark：

- `bfcl_v3`
  - 已实现 adapter，当前主战场
- `spreadsheet`
  - 已实现 smoke adapter
- `appworld`
  - registry only
- `officeqa`
  - registry only
- `tir_bench`
  - registry only

说明文档：

- `academic/benchmarks/README.md`

---

## 5. 已完成的核心实现

### 5.1 长期记忆维护体系

已完成：

- 用 `copilot_cli` 作为长期记忆入口
- 明确 `AGENT.md / progress.md / academic_doc.md` 分工
- 建立“改代码后同步文档”的默认要求

### 5.2 `academic` 主系统增强

已完成：

- execute 前显式 `planner` 阶段
- executor 可见 `plan_context`
- history workflow / retrieved skill 注入
- `solver_mode = tir | oneshot`
- runtime skill call counting
- extract -> test -> refine -> retest 闭环
- web execute lab 支持：
  - 模型选择
  - memory copy-mode
  - planner artifact 展示
  - refine/retest 历史展示
  - memory 管理

### 5.3 replay benchmark

已完成：

- 从 rigid exact-match 改成 `LLM-as-a-judge`
- 协议版本：`workflow_reuse_v3_judge`
- `references` 作为软参考
- `workflow_fragments` 作为可选历史子流程锚点
- synthetic validated cases
- real-log mining + case drafts + merged benchmark
- replay benchmark explorer UI

### 5.4 refactoring lab

已完成：

- shared structure discovery
- correctness-preserving rewrite
- clique-growth clustering
- shared helper prepend 修复
- 数学语料与 grouped skills 语料上的 token 压缩实验

### 5.5 BFCL benchmark

已完成：

- `bfcl_eval` 官方语义对齐的 adapter
- `openai_direct` 路径，贴近 GLM official handler
- BFCL tool-call path、metrics、trace、skill stats 完整打通
- `skill_injection_mode = none|prompt_only|tool_only|hybrid`
- 按 turn 检索 / 按 turn 注入 prompt skills
- BFCL handwritten skills 和 auto-evolve skill extraction 路径

---

## 6. 当前最可信的实验结论

这一节只保留当前最可信、最值得后续直接复用的结论。

### 6.1 Refactor Lab：共享结构存在，且可安全压缩

内置 18-skill 数学语料：

- naive exact-match dedup
  - shared functions: `0`
  - tokens: `1530`
  - accuracy: `100%`
- current refactor method
  - shared functions: `5`
  - tokens: `1258`
  - token reduction: `17.8%`
  - accuracy: `100%`

结论：

- 共享结构是真实存在的
- correctness-preserving refactoring 是可行的

### 6.2 SkillsBench 风格 grouped corpus：refactor 可迁移

`skillsbench_manual` 上：

- `original_skills`: accuracy `100%`, avg tokens `670`
- `refactored_skills`: accuracy `100%`, avg tokens `516`

结论：

- token 压缩约 `23%`
- 说明 refactor 能从内置数学语料迁移到外部风格 grouped skills

### 6.3 retrieval fixture：retrieval 路径本身强

`skillsbench_fixture`：

- Recall@1: `95.83%`
- Recall@5: `100%`

结论：

- retrieval substrate 足够强
- 当前大问题不在“根本检索不到”

### 6.4 replay benchmark：评测协议已可用，但真实 judged cases 仍不足

结果文件：

- `academic/refactoring_lab/experiments/planning_replay_benchmark.json`
- `academic/refactoring_lab/experiments/planning_replay_benchmark_merged.json`

当前结果：

- validated synthetic:
  - `n_cases = 5`
  - `joint_refactor_win_rate = 1.0`
- merged:
  - `n_cases = 19`
  - `n_available_cases = 5`
  - `n_judged_cases = 5`
  - `joint_refactor_win_rate = 0.2632`

结论：

- benchmark harness 可用
- 但 merged benchmark 里大量 real draft 还未标注
- 所以 replay benchmark 还不能作为强论文主结果

### 6.5 `academic` full loop：skill reuse 在 train 存在，在 test 利用率低

最关键的历史 observation 来自 Claude 完整 rerun：

- train retrieved unique skills: `154`
- train called unique skills: `67`
- test retrieved unique skills: `70`
- test called unique skills: `2`

结论：

- skill reuse 在 train 阶段是真实存在的
- test 阶段也能检索到 skill
- 当前主要瓶颈是 **test-time utilization**
- 问题不是“没有 skill”或“检索不到 skill”

### 6.6 BFCL：外围问题基本排干净，handwritten prompt-only 已见到小幅正信号

当前 BFCL 关键结论：

1. 问题不在“GLM 完全不会用 skill”
2. 真正问题在于：
   - adapter 协议是否贴近官方
   - skill 是否过于泛化
   - 注入是否按 turn，而不是整题级

#### 早期 5-case 对照

结果文件：

- `bfcl_v3_glm47_official_realign_none_baseline.json`
- `bfcl_v3_glm47_official_realign_prompt_baseline.json`
- `bfcl_v3_glm47_official_realign_prompt_infoonly_baseline.json`

结论：

- generic checklist / workflow prompt injection 会伤 baseline
- 精确、低扰动、domain-specific informational skill 是可行方向

#### 定点 A/B：`task11/task67`

结果文件：

- `bfcl_task11_67_temp0_ablation.json`
- `bfcl_task11_67_temp0_ablation_finalskills.json`

结论：

- `bfcl_file_system_navigation` 会伤 `multi_turn_base_11`
- `bfcl_vehicle_minimal_actions` 会伤 `multi_turn_base_67`
- 移除这两张默认 prompt rule 之后，`prompt_only` 不再额外伤害这两个敏感 case

#### 同 split 3-case 对照

结果文件：

- `bfcl_v3_glm47_turnskill_smoke_none_fairsplit.json`
- `bfcl_v3_glm47_turnskill_smoke_prompt_fairsplit.json`
- `bfcl_v3_glm47_turnskill_evolve_smoke.json`

子集：`multi_turn_base_134`, `178`, `120`

结果：

- `none`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.7795`
  - `avg_call_precision = 0.7083`
  - `avg_total_tokens = 58408.0`
  - `avg_elapsed_s = 48.905`
- 保守 handwritten `prompt_only`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.8091`
  - `avg_call_precision = 0.7528`
  - `avg_total_tokens = 56448.0`
  - `avg_elapsed_s = 45.191`
- `auto-evolve`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.7612`
  - `avg_call_precision = 0.6687`
  - `avg_total_tokens = 60295.0`
  - `avg_elapsed_s = 44.608`

当前最重要结论：

- 保守 handwritten prompt-only 已经能做到：
  - 不降低 `official_valid_rate`
  - 小幅提升 `avg_score`
  - 还略降 token
- 当前 **auto-evolved BFCL skills 还没有超过 handwritten prompt-only**

也就是说：

- BFCL 上 skill prompt 这条外围链路已经基本打通
- 真正接下来的问题是 **auto-evolved skill 质量**

---

## 7. 当前最可信的研究判断

综合目前所有探索，当前最可信的判断如下。

### 7.1 三个外围因素必须先解决

用户已经明确提出并当前仍然成立的判断：

1. **环境**
   - 如果环境中 skill 发挥空间太小，算法再好也看不到效果
   - 竞赛题往往显式模板稀疏，复用密度较低

2. **模型**
   - 模型是否会使用 skill 不是理所当然
   - 不同模型差异很大
   - frozen LLM + prompt-injected skill 未必总成立

3. **skill format**
   - 代码 skill 太局限
   - workflow / document / hint / rules 也可能是更合适的 skill 形式

### 7.2 对 BFCL 的当前结论

BFCL 当前可以支持如下说法：

- GLM 不是完全不会用 skill
- BFCL 上 skill 最适合写成 **低扰动、domain-specific、信息型规则**
- skill 注入必须按 turn，不应该整题级拼接
- 保守 handwritten prompt-only 已经出现小幅正信号
- auto-evolve 还没有 work

不能支持如下说法：

- evolve 已经在 BFCL 上稳定提点
- 当前 auto-extracted BFCL skills 已经优于手写保守规则
- 当前 setting 已经和官方 SkillX / BFCL strongest setting 完全对齐

### 7.3 对 `academic` 主系统的当前结论

当前 `academic` 主系统可以支持如下说法：

- history-aware planner / executor / replay benchmark / refine loop 都已经实现
- execute lab / replay lab 前端都已可用
- train-time skill reuse 真实存在
- test-time retrieval 不是零

但不能支持如下强结论：

- 完整 `academic` 多轮实验已经证明 end-to-end accuracy gain
- replay benchmark 已有足够真实 judged cases 支撑论文主结论

---

## 8. 当前未完成事项

### 8.1 replay benchmark

未完成：

- 把 real draft replay cases 标注成 validated judged cases
- 用真实 judged cases 评估 planner / history reuse 改动
- 将 replay benchmark 从 synthetic-driven 转成真实 case 主导

### 8.2 `academic` full loop

未完成：

- 在完整 `academic` 多轮实验上证明 end-to-end accuracy gain
- 在 `oneshot / tir` 下给出稳定、可复现的 skill reuse 利用率证据
- 进一步分析 skill format 对 test-time utilization 的影响

### 8.3 BFCL

未完成：

- 在更大、可复现、同 split 的 BFCL 子集上继续跑：
  - `none`
  - conservative handwritten `prompt_only`
  - `auto-evolve`
- 继续优化 auto-evolved BFCL skills：
  - 更强的 tool-specific / parameter-specific约束
  - 避免泛化 workflow / broad lint text
- 在隔离环境中进一步对齐官方 `bfcl-eval` handler

### 8.4 benchmark 扩展

用户后来选中的后续 benchmark 方向包括：

- `BFCL-v3`
- `AppWorld`
- `OfficeQA`
- `SpreadSheet`
- `TIR-Bench`

其中已经有一定实现的只有：

- `BFCL-v3`
- `SpreadSheet`

其余仍主要停留在调研与 registry 级别。

### 8.5 论文层面

未完成：

- 把 related work、benchmark survey、当前方法定位进一步压缩成论文可直接使用的结构
- 对外汇报版本还需要继续统一口径
- 把“外围诊断 -> post-execute extraction -> skill value / budget optimization”写成更完整研究路线

---

## 9. 当前推荐的下一步顺序

这是当前最推荐的继续顺序，优先级从高到低。

### 9.1 第一优先级：继续 BFCL auto-evolve 质量调试

原因：

- BFCL 是当前最接近“外围问题基本排干净”的环境
- handwritten prompt-only 已经见到小幅收益
- auto-evolve 还未超过 handwritten，因此这里最容易继续出结果

具体建议：

1. 固定当前保守 baseline：
   - `openai_direct`
   - `official`
   - turn-level `prompt_only`
   - 只保留 Ticket/Travel handwritten info rules
2. 扩大到更大 same-split BFCL 子集
3. 只调 auto-evolved skill 的形式与筛选，不再回头怀疑“GLM 完全不会用 skill”

### 9.2 第二优先级：继续 replay benchmark 标注

原因：

- replay benchmark harness 已经打通
- 当前主要缺的是真实 judged cases
- 这是后续证明“history-aware planning / workflow reuse”最直接的资产

### 9.3 第三优先级：环境 / 模型 / skill format 的系统性实验

原因：

- 用户已经明确要求后续实验必须先解决外围三因素
- 这部分是论文最终定位的关键

建议：

- 继续完善 benchmark survey
- 在 BFCL 之外选择一到两个更适合 skill evolving 的环境
- 比较不同 skill format

### 9.4 第四优先级：再回到完整 `academic` 主循环

只有当：

- benchmark 侧已经确认模型/环境/format 三者可行
- auto-evolve skill 质量已有稳定正信号

再回到完整 `academic` 多轮 loop 做 end-to-end 验证，才更有解释性。

---

## 10. 关键结果文件

### 10.1 BFCL

- `/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_official_realign_none_baseline.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_official_realign_prompt_baseline.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_official_realign_prompt_infoonly_baseline.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_task11_67_temp0_ablation.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_task11_67_temp0_ablation_finalskills.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_turnskill_smoke_none_fairsplit.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_turnskill_smoke_prompt_fairsplit.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_turnskill_evolve_smoke.json`
- `/home/lixujun/skill_evolving/academic/results/bfcl_glm47_turnskill_evolve_smoke_skills.json`

### 10.2 replay benchmark

- `/home/lixujun/skill_evolving/academic/refactoring_lab/experiments/planning_replay_benchmark.json`
- `/home/lixujun/skill_evolving/academic/refactoring_lab/experiments/planning_replay_benchmark_merged.json`
- `/home/lixujun/skill_evolving/academic/refactoring_lab/experiments/planning_replay_cases.json`
- `/home/lixujun/skill_evolving/academic/refactoring_lab/experiments/planning_replay_cases_merged.json`
- `/home/lixujun/skill_evolving/academic/refactoring_lab/experiments/ds100_aime_replay_case_drafts.json`

### 10.3 full-loop / historical math line

- `/home/lixujun/skill_evolving/academic/results/ds100_aime_v1_claude_full_3ep_baseline_summary.json`
- `/home/lixujun/skill_evolving/academic/results/ds100_aime_v1_claude_full_3ep_evolve_3ep_summary.json`

### 10.4 文献 / benchmark 调研

- `/home/lixujun/skill_evolving/academic/paper/RELATED_WORK_SURVEY.md`
- `/home/lixujun/skill_evolving/academic/paper/BENCHMARK_SURVEY.md`
- `/home/lixujun/skill_evolving/academic/paper/related_work_notes/`

---

## 11. 关键代码文件

- `/home/lixujun/skill_evolving/academic/benchmarks/bfcl.py`
- `/home/lixujun/skill_evolving/academic/benchmarks/bfcl_skills.py`
- `/home/lixujun/skill_evolving/academic/benchmarks/artifacts.py`
- `/home/lixujun/skill_evolving/academic/benchmarks/run.py`
- `/home/lixujun/skill_evolving/academic/planner.py`
- `/home/lixujun/skill_evolving/academic/executor.py`
- `/home/lixujun/skill_evolving/academic/pipeline.py`
- `/home/lixujun/skill_evolving/academic/experiments/run_experiment.py`
- `/home/lixujun/skill_evolving/academic/refactoring_lab/planning_replay_benchmark.py`

---

## 12. 文档路由

如果只看一个文档，先看本文件。

然后按需求继续：

- 长期约束
  - `/home/lixujun/skill_evolving/copilot_cli/AGENT.md`
- 项目进度 / 阶段结果 / 下一步
  - `/home/lixujun/skill_evolving/copilot_cli/progress.md`
- `academic` 主实验线总览
  - `/home/lixujun/skill_evolving/copilot_cli/academic_doc.md`
- `refactoring_lab` 对外阶段性汇报
  - `/home/lixujun/skill_evolving/academic/refactoring_lab/PAPER_STAGE_REPORT.md`
- `refactoring_lab` 内部开发细节
  - `/home/lixujun/skill_evolving/academic/refactoring_lab/DEV_HISTORY.md`
- benchmark 子系统说明
  - `/home/lixujun/skill_evolving/academic/benchmarks/README.md`
- related work / benchmark survey
  - `/home/lixujun/skill_evolving/academic/paper/RELATED_WORK_SURVEY.md`
  - `/home/lixujun/skill_evolving/academic/paper/BENCHMARK_SURVEY.md`

---

## 13. 给下一个 agent 的工作建议

如果下一个 agent 要继续工作，建议直接按以下方式启动：

1. 先读本文件。
2. 再读：
   - `copilot_cli/progress.md`
   - `copilot_cli/academic_doc.md`
   - `academic/benchmarks/README.md`
3. 如果要继续 BFCL：
   - 先看 `bfcl.py`, `bfcl_skills.py`, `run.py`
   - 再看 `bfcl_v3_glm47_turnskill_smoke_*` 与 `bfcl_task11_67_temp0_ablation*`
4. 如果要继续 replay benchmark：
   - 先看 `planning_replay_benchmark.py`
   - 再看 `planning_replay_benchmark*.json` 和 `planning_replay_cases*.json`
5. 不要重新争论已经成立的结论：
   - “GLM 完全不会用 skill” 不是当前结论
   - BFCL 必须按 turn 注入 skill
   - 当前 handwritten prompt-only 已经比同 split none baseline 略好
   - 当前 auto-evolve 还没超过 handwritten prompt-only

