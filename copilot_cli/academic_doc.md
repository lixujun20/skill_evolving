# Academic Doc

本文件维护 `academic` 主实验线的长期文档记忆，目标是把系统设计、实验协议、benchmark 契约、论文写作要点和文档路由集中到一个稳定入口。

## 1. 文档路由

### 主入口

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
- `academic/paper/BENCHMARK_SURVEY.md`
  - 同期工作相关 benchmark 调研
  - 维护各 benchmark 的任务形态、反馈信号、skill format、工程成本和实验优先级
- `academic/paper/related_work_notes/`
  - 每篇同期工作的独立中文笔记
  - `README.md` 提供按技术路线组织的索引
- `academic/paper/paper.md`
  - 当前论文草稿
  - `Related Work` 已同步近期 skill evolution 调研
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
- BFCL skills 现在是 tool rule / workflow / debug feedback card，默认作为 system notes 注入，不再默认新增 `use_skill` 工具，避免改变正式工具空间。
- Spreadsheet adapter 当前是 openpyxl scaffold，用于验证 spreadsheet-style skill package 的输入输出与 verifier，不作为最终完整 spreadsheet agent 结论。

当前 BFCL 关键观察：

- GLM-4.7、GLM-5、GLM-4.5-air、Claude 在 1-case BFCL bundle smoke 上都能产生 native tool calls，但 strict `official_valid` 仍为 0。
- 失败主要来自参数精确性，而不是工具调用完全缺失：文本字段被扩写、`high-priority` 被映射为 5 而 golden 为 4、可选 `insurance_id` 被额外传入。
- path-filtered ablation 能降低 token，但不是正式 BFCL baseline；5-case GLM-4.7 path-filtered smoke 的 avg call F1 约 0.50，official valid 仍为 0/5。
- 1 train + 1 test 的 evolve smoke 可以生成并注入技能，但 test official valid 仍为 0/1，尚不能证明 BFCL evolve 有效。

下一步优先级：

- 用隔离环境跑官方 `bfcl-eval` handler，确认当前 wrapper 与官方 handler 的差距。
- 找一个在 BFCL native tool calling 上达到合理 baseline 的模型，再跑 50/150 split。
- 在 strict text parameter 子集上先验证 skill 能否减少参数错误，再扩大到完整 evolve。
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
