# Academic Doc

本文件维护 `academic` 主实验线的长期文档记忆，目标是把系统设计、实验协议、benchmark 契约、论文写作要点和文档路由集中到一个稳定入口。

## 1. 文档路由

### 主入口

- `copilot_cli/HANDOFF_FULL_CONTEXT.md`
  - 面向下一任 agent 的完整上下文压缩文件
  - 汇总项目目标、进度、计划、未完成事项、关键结果与路由
- `copilot_cli/academic_doc.md`
  - `academic` 主线总览
  - 维护跨子系统引用关系
  - 记录论文写作中需要长期稳定复用的结论和术语

### 设计文档

- `copilot_cli/DESIGN.md`
  - `skill_evolving_v1` 总体设计
- `copilot_cli/DESIGN_V2.md`
  - `skill_evolving_v2` 设计增量
  - 历史 query / workflow reuse / planning 方向的重要背景

### Refactoring / Replay Benchmark

- `academic/refactoring_lab/PAPER_STAGE_REPORT.md`
  - 对外阶段性汇报文档
  - 永远只写当前已成立的阶段性结果，不写调试过程
- `academic/refactoring_lab/DEV_HISTORY.md`
  - 内部开发文档
  - 按时间顺序记录设计、实验、调试与产物回溯
- `academic/paper/RELATED_WORK_SURVEY.md`
  - Skill 自进化近期文献调研
  - 维护逐篇对比、related work 素材、当前项目定位和后续差异化方向
  - 2026-05-12 已补入 PSN、SkillMOO、AgentOptimizer；当前论文 novelty 应收缩到 test-grounded repository population selection，而不是泛称 skill evolution / refactoring / cost-aware optimization
- `academic/paper/BENCHMARK_SURVEY.md`
  - 同期工作相关 benchmark 调研
  - 维护各 benchmark 的任务形态、反馈信号、skill format、工程成本和实验优先级
- `academic/paper/related_work_notes/`
  - 每篇同期工作的独立中文笔记
  - `README.md` 提供按技术路线组织的索引
  - 新增强相关笔记：
    - `psn.md`：programmatic skill network、fault localization、maturity gating、rollback refactoring
    - `skillmoo.md`：skill bundle pass rate / cost 多目标优化
    - `agentoptimizer.md`：functions as learnable agent weights
- `academic/paper/paper.md`
  - 当前论文草稿
  - `Related Work` 已同步近期 skill evolution 调研
  - 当前主张：多次 LLM sampling 产生候选 skill 后，用 valid-set utility、token/cost、retrieval noise、redundancy、dependency risk 和 maintenance cost 做 repository-level selection
- `academic/refactoring_lab/planning_replay_benchmark.py`
  - replay benchmark 实现
- `academic/refactoring_lab/experiments/planning_replay_cases.json`
  - validated synthetic replay cases
- `academic/refactoring_lab/experiments/planning_replay_cases_merged.json`
  - synthetic + real draft merged cases

### 真实实验主路径

- `academic/skill_store.py`
  - workflow history / workflow record 存储
- `academic/planner.py`
  - execute 前的 lightweight planning artifact
- `academic/executor.py`
  - 历史 workflow 注入与 solver prompt 集成点
  - `tir` / `oneshot` 双 solver 模式
  - runtime skill call counting
- `academic/experiments/run_experiment.py`
  - trace / workflow summary / workflow decision logging
  - experiment-level retrieved vs called aggregation
- `academic/experiments/quick_skill_reuse_check.py`
  - 小样本 skill reuse 诊断脚本
- `academic/benchmarks/`
  - BFCL / Spreadsheet 等 agentic benchmark adapter
  - `academic/benchmarks/README.md` 是该子系统使用文档和当前 smoke 结果入口

## BFCL 近期结论

### 1. 当前已确认的根因

针对 “GLM 在 BFCL 上无法知悉并调用额外工具/skill” 这个问题，当前已经得到更精确的结论：

- 问题不在 `glm-4.7` 完全不会使用额外信息
- 问题也不在我们没有直连官方 `BigModel` 接口
- 主要问题分成两层：
  - 我们早期 BFCL adapter 的 tool-call 请求路径与官方 `bfcl_eval` 的 GLM FC handler 不够对齐
  - 我们早期给 BFCL 注入的 skill 过于泛化，尤其是 checklist / workflow 型提示，会诱导 GLM 产生多余工具调用或错误参数组织

### 2. 当前工程修正

已经在 `academic/benchmarks/bfcl.py` 中完成两类关键修正：

- `bigmodel` / `open.bigmodel.cn` 路径默认走 `openai_direct`
  - 直接使用 OpenAI-compatible `chat.completions.create(messages, tools, store=False)`
  - 不再经过通用 `app.llm.ask_tool()` 中的额外包装逻辑
  - 这样更接近 BFCL 官方 `OpenAICompletionsHandler` / `GLMAPIHandler`
- skill retrieval / injection 改成 BFCL-specific 低扰动策略
  - 先按 `involved_classes` / task domain 过滤 skill
  - `prompt_only` 只注入 `informational` skill
  - 不再把 `workflow` / `checklist` 默认塞进 BFCL inference prompt
  - skill retrieval 从“整题级”改成“turn 级”
  - 每个 user turn 单独做检索和注入，避免混合域多轮 case 被跨轮污染

### 3. 当前 BFCL skill 设计原则

对 BFCL，当前有效的 skill 形式不是“额外可调用工具”，也不是“笼统工作流总结”，而是：

- 域内、原子、低扰动的信息型规则
- 典型内容：
  - 参数名和字段组织约束
  - 文本字段 vs tag/metadata 字段的区分
  - 避免无关状态检查和多余 API 调用

当前的经验更保守：

- 有些看似合理的信息型规则也会稳定伤害 baseline
- 目前确认应从默认 prompt skill 集移除的例子包括：
  - `bfcl_file_system_navigation`
  - `bfcl_vehicle_minimal_actions`
- 当前更稳的默认 handwritten prompt skills 只保留：
  - `bfcl_schema_parameter_names`
  - `bfcl_literal_user_text_arguments`
  - 并且它们只在 Ticket/Travel 域命中时才注入

### 4. 当前小样本对齐结果

在同一批 5 个 BFCL-v3 题目上：

- `official_realign_none`
  - `official_valid_rate = 1.0`
  - `avg_score = 0.7185`
  - `avg_call_precision = 0.5773`
  - `avg_total_tokens = 64205.2`
- 早期 `prompt_only`（泛化 checklist / workflow 注入）
  - `official_valid_rate = 0.8`
  - `avg_score = 0.6828`
  - 会伤 baseline
- 当前 `prompt_only`（info-only, domain-filtered）
  - `official_valid_rate = 1.0`
  - `avg_score = 0.7569`
  - `avg_call_precision = 0.6244`
  - `avg_total_tokens = 63235.6`

因此，当前最合理的解释是：

- GLM 可以在 BFCL 上使用 prompt 注入的 skill
- 但 skill 必须非常精确，并且尽量是 domain-specific informational rule
- “是否能用 skill” 不是 blocker
- “skill 写成什么样、怎么筛进 prompt” 才是关键

### 4.1 最新定点诊断

关键结果文件：

- `academic/results/bfcl_task11_67_temp0_ablation.json`
- `academic/results/bfcl_task11_67_temp0_ablation_finalskills.json`

诊断结论：

- `multi_turn_base_11`
  - 原先的问题既有跨轮污染，也有规则本身的副作用
  - turn-level injection 修复了“文件系统 skill 泄漏到 Twitter turn”
  - 但 `bfcl_file_system_navigation` 仍会诱导额外 `pwd` / `posting_get_login_status`
  - 因此最终从默认 prompt skill 集删除
- `multi_turn_base_67`
  - `bfcl_vehicle_minimal_actions` 会诱导额外或错误顺序的 Vehicle 调用
  - 从默认 prompt skill 集删除后，`prompt_only` 与 `none` 基本拉平

### 4.2 当前最可靠的小规模 BFCL 对照

同 split 3-case 子集：

- `none`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.7795`
  - `avg_total_tokens = 58408.0`
- 保守 handwritten `prompt_only`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.8091`
  - `avg_total_tokens = 56448.0`
- `auto-evolve prompt_only`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.7612`
  - `avg_total_tokens = 60295.0`

当前解读：

- prompt skill 这条外围链路已经基本打通
- 保守 handwritten prompt-only 可以在不降低 `official_valid_rate` 的前提下带来小幅收益
- 当前 auto-evolved BFCL skills 还没有超过 handwritten prompt-only

### 5. 当前推荐 BFCL 配置

在 `academic` 里，当前 BFCL 推荐配置为：

- `bfcl_data_source = bfcl_eval_bundle`
- `bfcl_adapter_mode = official`
- `bfcl_execution_backend = official`
- `bfcl_prompt_style = native`
- `bfcl_tool_api_style = auto`
  - 对 `bigmodel` 会解析到 `openai_direct`
- `skill_injection_mode = prompt_only`
  - 但实际只注入 `informational` skill
  - 并且按 turn 检索、按 turn 注入
- skill 库优先使用 BFCL-specific handwritten atomic rules
  - 当前默认只保留更保守的 Ticket/Travel 参数与文本规则
- 当目标是正式 baseline 时，仍应以 `skill_injection_mode = none` 为主
- 当目标是 skill-format / evolve 诊断时，优先使用当前保守版 `prompt_only`
- 当前 BFCL 工程辅助脚本：
  - `academic/benchmarks/bfcl_experiment_suite.py`
    - 固定 official-aligned 50/150 与 evolve 命令，避免手工拼参
  - `academic/benchmarks/compare_bfcl_results.py`
    - 对比两份 BFCL 结果，输出 aggregate delta、case delta 与 skill delta
- 当前 BFCL 汇总建议优先查看：
  - `official_valid_rate`
  - `official_avg_at_k`
  - `official_pass_at_k`
  - `avg_score` / `call_f1` 仅作为辅助诊断
- 当前 BFCL evolve 结果新增：
  - `skills`
    - 保存最终 skill 列表及其 source metadata
  - `skill_impact_summary`
    - 记录每个 skill 的来源 task、test 检索/注入次数、命中的 test case ids

### 6. 当前不再采用的假设

以下假设目前已被否定或弱化：

- “GLM 不会使用 prompt 里的额外 skill”
- “要让 GLM 复用 BFCL skill，必须把 skill 暴露成额外 callable tools”
- “只要把一些通用 workflow/checklist 提示拼到 prompt 里，就能稳定提升 BFCL”

更接近事实的是：

- BFCL 这类函数调用 benchmark 上，额外 skill 需要像 schema-sensitive lint rule，而不是通用计划建议
- 并且需要按 turn 暴露，而不是整题级拼接

## 2. 当前主张

当前 `academic` 主线不是“强制提取 shared skill”，而是：

- 先根据当前 query 与历史 workflow 规划本次解题路径
- 在 plan 中显式考虑：
  - 直接复用旧计划
  - 轻微改写旧计划
  - 只复用一个历史 workflow 片段
  - 完全 fresh plan
  - 仅在当前计划真正需要时，再提议 shared skill

这意味着 shared skill extraction 现在是 planning 的一个可选结果，而不是固定目标。

## 3. 当前主线文档约定

关于 `refactor_lab` / `benchmark`，后续只维护两份正式文档：

- `PAPER_STAGE_REPORT.md`
  - 面向对外汇报
  - 只保留阶段性结果、方法、结论与下一阶段计划
- `DEV_HISTORY.md`
  - 面向内部开发
  - 按时间顺序记录实验 history、设计变更、调试细节与证据路径

其余与本子系统直接相关的 Markdown / txt 文档原则上不再保留。

## 4. Replay Benchmark 当前协议

当前 replay benchmark 版本：

- `workflow_reuse_v3_judge`

核心原则：

- 主评测方式是 `LLM-as-a-judge`
- `references` 是软参考，不是 rigid exact-match 监督
- `workflow_fragments` 是可选历史子流程锚点，不是必须复用的文本块
- 启发式 diagnostic score 只用于 fallback 和 debug，不是主指标

当前 case 类型包括：

- `reuse_plan`
- `adapt_plan`
- `reuse_workflow_fragment`
- `fresh`
- `propose_shared_skill`

## 5. 当前已落地的文档同步规则

后续凡是修改以下任一模块，都默认需要同步至少一个文档：

- `academic/refactoring_lab/*`
  - 默认同步：
    - `academic/refactoring_lab/PAPER_STAGE_REPORT.md`
    - `academic/refactoring_lab/DEV_HISTORY.md`
- `academic/skill_store.py`
  - 同步本文件第 5 节或新增子系统文档
- `academic/executor.py`
  - 同步本文件第 5 节或新增子系统文档
- `academic/experiments/run_experiment.py`
  - 同步本文件与 `progress.md`

## 6. Academic 主线现状

### 5.1 已实现

- `academic` 路径下的 workflow history / workflow summary 注入
- planner-aware 的 solver prompt 改造
- execute 前的 explicit planning artifact
- `solver_mode = tir | oneshot`
- runtime skill call counting
- refactoring lab 的 replay benchmark
- synthetic validated replay cases
- real-log mining + draft generation + merged case pipeline
- execute lab memory session / copy-mode
- extracted skill refine -> retest loop

### 5.2 已验证

- replay benchmark tests 已通过
- synthetic replay benchmark 已切换到 judge-based 协议
- merged replay benchmark 已可运行，并能区分 validated synthetic cases 与 draft-only real cases

### 5.3 尚未完成

- 将一批真实 draft replay cases 标注成 validated cases
- 用真实 judged replay cases 验证 planner 改动是否稳定优于 legacy path
- 在完整 `academic` 多轮实验上证明 end-to-end accuracy gain
- 对 `oneshot` / `tir` 在真实小样本上的 skill reuse 给出稳定、可复现实证

### 5.4 当前 one-shot / reuse 验证口径

- `retrieved`
  - 某个 skill 被检索进 prompt / 执行上下文
- `called`
  - 该 skill 在运行时代码路径中被真实执行
  - 现在通过 wrapper 计数，不再依赖旧版 `usage_count`
- 快速诊断脚本：
  - `python -m academic.experiments.quick_skill_reuse_check ...`
- 当前推荐配置：
  - `--retrieval_mode tfidf`
  - `--agent_model glm45air`
  - 先验证真实调用是否存在，再决定是否跑完整 evolve/test
- 当前 prompt 调整方向：
  - 不强迫模型调用 skill
  - 只强调 skill reuse 在推理后期的潜在收益：
    - 节省 token
    - 减少重复实现的小错误
  - 并在每轮 tool 执行结束后追加一次 `next-step` 弱提醒
- 当前 extractor / refine 约束：
  - public `test_code` 视为固定，不允许 refine 修改
  - refine 只能增量修改 `skill.code`
  - refine 会保留最近几轮失败历史，避免每次从零重写
  - 当前设计目标是把“是否通过 public test”当作稳定门槛，而不是让模型顺手改 test 逃过验证
- 当前已知对照结果：
  - `glm45air`：
    - same-question retrieval 命中很好
    - 但早期 case 中常见 `retrieved > 0, called = 0`
  - `default = claude-sonnet-4-6`：
    - 在相同 same-question case 上，已观察到稳定 `3/3` 调用 `circle_from_equation`
  - 因此当前更像是模型使用行为差异，而不是 retriever 故障

## 7. Research Frontend 现状

`academic/webapp` 现在有三个页面：

- `/`
  - skill explorer
- `/replay`
  - replay benchmark lab
  - 支持：
    - 加载 merged / validated / draft cases
    - 批量选择 case
    - 启动 LLM annotation job
    - 实时查看 annotation progress
    - 查看每个 case 的：
      - historical query
      - workflow summary / plan / fragments
      - LLM full prompt
      - LLM full output
      - parsed structured annotation
      - benchmark result
- `/execute`
  - execute lab
  - 支持：
    - 输入真实 query
    - 选择 skills file
    - 选择模型配置
    - 选择 copy-mode memory
    - 运行 `academic` 主链
    - 查看：
      - retrieve
      - plan
      - execute
      - extract
      - test
      - refine / retest history
      - memory session
      - executor plan context

运行产物目录：

- `academic/webapp/runtime/jobs/`
  - 所有后台 job 状态
- `academic/webapp/runtime/annotation_runs/`
  - replay annotation artifacts
- `academic/webapp/runtime/execute_runs/`
  - execute run artifacts
- `academic/webapp/runtime/memory_sessions/`
  - UI 侧 memory session 副本
  - 支持 session-scoped skill/workflow 编辑

## 8. 当前已知问题

- 某些模型后端拒绝 `response_format=json_object`，要求 `json_schema`

## 9. Benchmark Adapter 现状

当前已落地 BFCL 与 Spreadsheet 两个 adapter：

- BFCL adapter 支持 `official / path_filtered / debug_hints / full_tools` 四种工具暴露模式。
- BFCL 默认使用 `bfcl_eval_bundle` 数据源，与 `/tmp/bfcl_pkg/unpack` 中的官方 backend/docs 保持版本一致；`hf_v3` 保留为 SkillX 论文 setting 的对照入口，但不能和当前 v4 bundle backend 混用。
- BFCL execution backend 默认 `auto`，会优先调用 `bfcl-eval` official executable backend；scorer 同时报 `call_f1` 诊断指标和 `official_valid` state/response checker。
- BFCL skills 现在按注入方式分为 `functional / informational / workflow`：功能类 skill 可暴露为 callable tool；信息类和流程类 skill 默认作为短 prompt notes；`use_skill` 统计工具仍默认关闭，避免改变正式工具空间。
- Spreadsheet adapter 当前是 openpyxl scaffold，用于验证 spreadsheet-style skill package 的输入输出与 verifier，不作为最终完整 spreadsheet agent 结论。

当前 BFCL 关键观察：

- GLM-4.7、GLM-5、GLM-4.5-air 在简单 BFCL case 上都能产生正确 native tool calls，并达到 official valid `1/1`。
- Claude 需要走 Anthropic native `tool_use/tool_result` 协议；OpenAI-style tool role 会低估 Claude。当前 runner 已加入 `anthropic_direct`。
- Qwen/SiliconFlow 类模型可用 streaming OpenAI-compatible tool-call path 测试；最小工具调用 probe 可用，但 BFCL 长 schema 下延迟较高。
- GLM-4.7 first-10 shuffled baseline：official_valid_rate `0.6667`（1 个 timeout），avg call F1 `0.6396`，avg tokens about `60k`。
- GLM-4.7 + handwritten top-2 skill notes first-5：official_valid_rate `0.6`，没有稳定增益；当前 skill notes 更像行为提示，可能增加 token/延迟并改变 action tendency。
- 失败主要来自 strict state/parameter/action-set：文本字段被扩写、`high-priority` 被映射为 5 而 golden 为 4、可选 `insurance_id` 被额外传入、以及 extra calls。
- 当前 runner 支持 `--skill-injection-mode none|prompt_only|tool_only|hybrid` 和 `--max-steps-per-turn`。正式 baseline 优先用 `none`；诊断 skill format 时用 `hybrid` 对照。
- wall-clock `--max-task-seconds` 保留为可选防挂 guard；BFCL 对齐口径优先看 step budget，并在 summary 里报告 timeout/latency。

下一步优先级：

- 主指标使用 `official_valid`；`call_f1` 只作为诊断，因为 extra calls 不总是导致 official failure。
- 先压缩 skill notes / tool schema，并选择无 timeout、baseline 非饱和的 BFCL 子集，再做 evolve。
- 用隔离环境跑官方 `bfcl-eval` handler，确认当前 runner 与官方 reported setting 的剩余差距。
- 不要在 full official tools + 长 skill notes 上直接扩大 50/150 split；当前吞吐和 timeout 还不适合。
- 当前 replay annotation 已加入 plain-text JSON fallback
- 仍需继续观察：
  - fallback 路径在当前 `tool_maker` 配置下是否稳定
  - 模型是否会偶发返回非 JSON，需要进一步强化 parser 或 prompt
- execute 目前是“步骤级流式刷新”，不是 token 级 streaming
- 当前 token 级 limitation 不在 `academic`，而在 `app.llm.ask_tool()` 的 non-streaming tool path
- 当前 memory 管理已支持 session 级编辑，但还没有单独的 `/memory` 独立页面；目前入口仍在 `/execute`

## 9. 推荐维护方式

当前对子系统文档的要求已经收敛为两份正式文档：

- `academic/refactoring_lab/PAPER_STAGE_REPORT.md`
- `academic/refactoring_lab/DEV_HISTORY.md`

除非未来出现全新且长期独立的子系统，否则不再继续扩展新的主文档层级。
