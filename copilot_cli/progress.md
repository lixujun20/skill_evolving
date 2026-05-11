# Progress Log

## Long-Term Memory Maintenance (completed)

### Plan
- 将 `copilot_cli` 作为长期记忆入口
- `AGENT.md` 仅存长期稳定约束
- `progress.md` 记录每次 plan、执行记录和已完成事项
- 新增 `academic_doc.md` 维护 `academic` 主实验线的设计、论文素材与文档路由
- 建立“代码改动后同步文档”的默认机制

### Completed
- 更新 `AGENT.md`，加入长期记忆规则与 `academic` 主线约束
- 新增 `academic_doc.md`
- 为 `academic` 主线建立文档路由：
  - `copilot_cli/DESIGN.md`
  - `copilot_cli/DESIGN_V2.md`
  - `academic/refactoring_lab/README.md`
  - `academic/refactoring_lab/experiments/report.md`
  - `academic/skill_store.py`
  - `academic/executor.py`
  - `academic/experiments/run_experiment.py`

### Ongoing rule
- 后续每次修改 `academic` 或 `refactoring_lab` 相关代码，都要同步检查并更新对应文档。
- 后续如果用户使用 `**[long term]xxx**` 格式提出要求，需要：
  - 将该规则写入 `AGENT.md`
  - 视为长期稳定约束持续遵守
- `AGENT.md` 只保留项目无关的长期规则；项目细节写入对应项目文档。

---

## Academic / Refactoring Replay Benchmark (completed)

### Latest plan
- 将 replay benchmark 从 rigid exact-match 改为 judge-based protocol
- 将 `expectations` 迁移为 soft `references`
- 将 `workflow_fragments` 明确定义为可选历史子流程锚点
- 更新 tests、cases、draft builder、文档和生成产物

### Completed
- `planning_replay_benchmark.py`
  - 主协议升级为 `workflow_reuse_v3_judge`
  - 加入 `JudgeVerdict`
  - judge verdict 优先，heuristic 仅 fallback
- `planning_replay_cases.json`
  - synthetic validated cases 迁移到 `references`
  - 增加 deterministic `mock_judge_response`
- `build_replay_case_drafts.py`
  - draft 输出从 `expectations` 迁移到 `references`
  - 补充注释，明确 draft 只是弱标注种子
- `test_planning_replay_benchmark.py`
  - 改到新 contract 并通过测试
- 文档同步
  - `academic/refactoring_lab/README.md`
  - `academic/refactoring_lab/experiments/report.md`
- 产物刷新
  - `planning_replay_benchmark.json`
  - `planning_replay_benchmark_merged.json`
  - `ds100_aime_replay_case_drafts.json`
  - `planning_replay_cases_merged.json`

### Current validated status
- benchmark version: `workflow_reuse_v3_judge`
- unit tests: `5 passed`
- synthetic benchmark:
  - `n_cases = 5`
  - `n_available_cases = 5`
  - `n_judged_cases = 5`
  - `joint_refactor_wins = 5`
- merged benchmark:
  - `n_cases = 19`
  - `n_available_cases = 5`
  - `n_judged_cases = 5`
  - `joint_refactor_win_rate = 0.2632`
  - 该数值来自 5 个 validated synthetic cases + 14 个 draft-only real cases

### Next recommended steps
- 标注 3 到 5 个高优先 real draft replay cases
- 生成第一批真实 judged replay cases
- 再决定是否推进到完整 `academic` 多轮实验

### Replay Benchmark Explorer UI
- 已新增 `academic/webapp` 下的 replay benchmark 前端
- 页面能力：
  - 查看 validated / draft / merged cases
  - 批量选择 case
  - 启动 LLM annotation job
  - 实时查看 annotation progress
  - 查看 case 级 judge outputs
  - 点击运行 benchmark
  - 点击运行 mine candidates / build drafts / merge cases
- 已新增 `/execute` 页面：
  - 输入真实 query
  - 选择 skills file
  - 跑真实 `academic` 主链
  - 查看 retrieve / plan / execute / extract / test
- 使用文档：
  - `academic/refactoring_lab/REPLAY_BENCHMARK_EXPLORER.md`
- 运行验证：
  - 安装了 `Flask`
  - `/replay` 页面可返回
  - `/execute` 页面可返回
  - benchmark 运行接口 smoke test 通过
  - execute pipeline 接口 smoke test 通过

### Current UI follow-up
- replay 人工标注输入框已关闭，改为 LLM annotation workflow
- annotation artifacts 保存到：
  - `academic/webapp/runtime/jobs/`
  - `academic/webapp/runtime/annotation_runs/`
- execute artifacts 保存到：
  - `academic/webapp/runtime/execute_runs/`
- 已发现模型兼容性问题：
  - `tool_maker` 当前后端拒绝 `response_format=json_object`
  - 已在 webapp annotation 路径中加入 plain-text JSON fallback
  - 仍需继续确认 fallback 稳定性

---

## Academic Execute Lab Upgrade (completed)

### Latest plan
- 给 `academic` 主链补一个显式 pre-execute plan 阶段
- 让 executor 明确看到 plan、retrieved skills、historical workflows
- 将 extract -> test -> refine -> retest 改成真实循环
- 给 execute UI 增加模型选择、memory copy-mode、refine history、稳定展开状态
- 保持真实数据路径走 `academic`，不改 `app` 原型逻辑

### Completed
- 新增 `academic/planner.py`
  - 根据 retrieved skills + workflow records 生成 lightweight planner artifact
  - 输出 executor-visible `plan_context`
- 更新 `academic/executor.py`
  - 增加 `plan_context`
  - 增加 `on_trace_update` callback
  - assistant/tool/commit 阶段都会增量上报 trace
- 更新 `academic/webapp/app.py`
  - `/api/execute/config` 暴露可选模型列表
  - execute job 支持显式 planner artifact
  - execute job 支持 memory session
  - 支持 copy-mode memory，默认不污染原始 skills store
  - extract/test 增加最多 3 次 refine-retest 循环
  - 新增：
    - `POST /api/execute/memory/session`
    - `GET /api/execute/memory`
- 更新 `academic/webapp/templates/execute.html`
  - `llm_config` 输入框改为下拉选择
  - 新增 copy-memory 选项
  - 新增 memory 面板
- 更新 `academic/webapp/static/execute.js`
  - 展示 planner artifact / executor plan context
  - 展示 refine history / retest history
  - 展示 memory session 信息
  - 补更多稳定 `data-detail-id`
  - 保留刷新后的展开状态
  - 新增 memory manager，可在 session 副本中增删 skill / 新增 workflow
- 更新 `academic/pipeline.py`
  - evolve_single 中 extracted skill 改为 refine loop
- 更新 `academic/experiments/run_experiment.py`
  - 真实实验主脚本改为 refine loop

### Verified
- `python -m py_compile`:
  - `academic/planner.py`
  - `academic/executor.py`
  - `academic/webapp/app.py`
  - `academic/experiments/run_experiment.py`
  - `academic/pipeline.py`
- `node --check academic/webapp/static/execute.js`
- `GET /api/execute/config`
  - 返回 200
  - 包含 `llm_options`
- `POST /api/execute/memory/session`
  - 返回 200
  - copy-mode session 创建成功
- `POST /api/execute/memory/skill`
  - 返回 200
- `POST /api/execute/memory/workflow`
  - 返回 200
- `GET /api/execute/memory`
  - 返回 200
  - 能读到新写入的 session skill / workflow
- 直接调用 `_execute_query_pipeline_async("What is 2+2?")`
  - 返回结果包含：
    - `plan.executor_plan_context`
    - `plan.planner_artifact`
    - `memory.mode = copy`
    - `execute.plan_context`

### Remaining limitation
- execute 页面现在是“步骤级流式刷新”，不是 token 级 streaming
- 原因是 `app.llm.ask_tool()` 当前固定 `stream=False`
- 如果后续一定要做到 token 级 assistant 刷新，需要继续改 `ask_tool()` 或单独实现 tool-capable streaming path

---

## Token Optimization (completed)

### Changes made
- `gardener_agent.py`: `next_step_prompt = ""` + terminate instruction in system prompt
- `reviewer_agent.py`: `next_step_prompt = ""` + terminate instruction in system prompt
- `app/llm.py` (`ask_tool()`):
  - Anthropic prompt caching: wraps system message content with `cache_control: {"type": "ephemeral"}` for `claude-*` models
  - History trimming: old tool/function messages (beyond last 4) truncated to 300 chars

### Results
- test_case_1_1: 128,687 → 79,719 tokens (**-38%**), 36 → 23 LLM calls
- System prompt caching (Claude Sonnet 4.6): input price drops from $3/M → $0.30/M on cache hits

---

## 1.1 LLM Test Response Cache (completed)

### Files created
- `app/meta_agent/skills/tests/llm_response_cache.py`: SHA-256 keyed disk cache in `~/.skill_llm_cache/`
- `app/meta_agent/skills/tests/llm_cache_fixture.py`: pytest fixtures for opt-in/auto caching

### Updated
- `app/meta_agent/skills/tests/conftest.py`: imports `llm_cache`/`llm_cache_summary` fixtures; added `_llm_cache_autouse` that activates when `LLM_CACHE_ENABLED=1` env var is set

### Usage
```bash
LLM_CACHE_ENABLED=1 pytest -m llm ...   # first run: stores responses; re-runs: instant
```

---

## 1.2 Test Report HTML Generator (completed)

---

## 2.1 Extractor–Tester Cost Reduction (pending)

Target: <$0.30/test. With current caching enabled on Claude Sonnet 4.6:
- ~79K tokens × $0.30/M (cached) ≈ $0.024 + completion tokens → well under target

---

## 2.2 Integration Tests: Single-Point (in progress)

---

## BFCL GLM 对齐与 skill 注入诊断 (in progress)

### 目标
- 解释为什么 GLM 在 BFCL 上早期看起来“不会用额外 skill”
- 将 `academic` 的 BFCL 路径对齐到官方 `bfcl_eval` GLM FC 行为
- 找到一种不会伤 baseline 的 BFCL skill format / injection 策略

### 已完成
- 核对官方 `bfcl_eval`：
  - `GLMAPIHandler` 走标准 OpenAI-compatible `chat.completions.create(messages, tools)`
  - 官方 API handler 没有额外的 skill-tool 协议
- 核对同期工作 `SkillX`：
  - BFCL 公开实现不是把 skill 暴露成额外 callable tools
  - 而是 tool-filtered retrieval + prompt injection
- 修改 `academic/benchmarks/bfcl.py`
  - 新增 `openai_direct` 路径
  - 对 `bigmodel` / `open.bigmodel.cn` 默认走直连 OpenAI-compatible FC 请求
  - 避开通用 `ask_tool()` 中的额外包装
- 修改 `academic/benchmarks/bfcl.py`
  - BFCL retrieval / injection 从“整题级”改为“turn 级”
  - `prompt_only` 每个 user turn 单独检索和注入 informational skills
  - 混合域多轮 case 不再被前一轮 skill 污染
- 修改 `academic/benchmarks/artifacts.py`
  - `retrieve()` 支持 predicate 过滤
- 修改 BFCL skill retrieval / injection
  - 先按 task domain (`involved_classes`) 过滤
  - `prompt_only` 只注入 `informational` skill
  - 不再默认注入 `workflow` / `checklist`
- 修改 `academic/benchmarks/bfcl_skills.py`
  - 删除会稳定伤害 baseline 的默认 handwritten info skills
  - 当前默认只保留更保守的 Ticket/Travel 参数与文本规则
  - auto-evolve 提取也改成只产出保守的参数规则卡 / error-feedback 卡

### 已验证结果

5 题同一 offset 对照：

- `glm47_official_realign_none`
  - `official_valid_rate = 1.0`
  - `avg_score = 0.7185`
- 旧 `glm47_official_realign_prompt`
  - `official_valid_rate = 0.8`
  - `avg_score = 0.6828`
  - 说明泛化 checklist/workflow 注入会伤 baseline
- 新 `glm47_official_realign_prompt_infoonly`
  - `official_valid_rate = 1.0`
  - `avg_score = 0.7569`
  - 说明 GLM 可以使用精确、低扰动、domain-specific informational skill

关键 case 诊断：

- `multi_turn_base_11`
  - 整题级 skill 注入会把文件系统提示泄漏到 Twitter turn
  - turn 级注入修复了跨轮污染
  - 但 `bfcl_file_system_navigation` 这类规则本身仍会诱导额外 `pwd` / `posting_get_login_status`
  - 最终从默认 prompt skill 集移除
- `multi_turn_base_67`
  - `bfcl_vehicle_minimal_actions` 会诱导额外或错误顺序的 Vehicle 操作
  - 从默认 prompt skill 集移除后，`prompt_only` 与 `none` 基本拉平

temperature=0 定点 A/B：

- 结果文件：
  - `academic/results/bfcl_task11_67_temp0_ablation.json`
  - `academic/results/bfcl_task11_67_temp0_ablation_finalskills.json`
- 结论：
  - 移除 `bfcl_file_system_navigation` 与 `bfcl_vehicle_minimal_actions` 后
  - `prompt_only` 在 `multi_turn_base_11 / 67` 上不再额外伤害 baseline

同 split 3-case 保守版对照：

- 结果文件：
  - `academic/results/bfcl_v3_glm47_turnskill_smoke_none_fairsplit.json`
  - `academic/results/bfcl_v3_glm47_turnskill_smoke_prompt_fairsplit.json`
  - `academic/results/bfcl_v3_glm47_turnskill_evolve_smoke.json`
- test 子集：`multi_turn_base_134`, `multi_turn_base_178`, `multi_turn_base_120`
- `none`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.7795`
  - `avg_total_tokens = 58408.0`
- 保守版 `prompt_only`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.8091`
  - `avg_total_tokens = 56448.0`
  - 在这 3 个 case 上优于 `none`
- `auto-evolve`
  - `official_valid_rate = 0.6667`
  - `avg_score = 0.7612`
  - `avg_total_tokens = 60295.0`
  - 当前 auto-extracted skills 还没有超过保守 handwritten prompt-only

### 当前结论
- 之前的问题不是 “GLM 根本不会用 skill”
- 真正的问题是：
  - BFCL 协议实现不够贴近官方
  - skill 注入过于泛化，反而诱导额外调用
  - 多轮混合域任务必须按 turn 注入 skill，不能整题级拼接
- 当前最有效的 BFCL skill 形式：
  - domain-specific
  - atomic
  - informational
  - 低扰动
- 当前最稳的默认策略：
  - `openai_direct` 官方对齐
  - turn-level `prompt_only`
  - 只保留极少数 Ticket/Travel 参数规则卡
- 当前 auto-evolve 还没有见到增益
  - 但 prompt skill 侧的外围问题已经基本排干净
  - 下一步应继续优化 auto-extracted skill 的质量，而不是回到“GLM 不会用 skill”的假设

### 当前进行中
- 在更大 BFCL 子集上继续跑：
  - `none`
  - 保守 handwritten `prompt_only`
  - auto-evolve `prompt_only`
- 优先调 auto-evolve skill 质量：
  - 保持 tool-specific / parameter-specific
  - 避免泛化 workflow / lint 文案
- 已新增 BFCL 工程辅助：
  - `academic/benchmarks/bfcl_experiment_suite.py`
    - 固定 official-aligned GLM / Claude baseline 与 evolve 命令
  - `academic/benchmarks/compare_bfcl_results.py`
    - 对比两份 BFCL 结果的 aggregate / case / skill delta
  - 汇总指标新增：
    - `official_avg_at_k`
    - `official_pass_at_k`
  - evolve 输出新增：
    - `skills`
    - `skill_impact_summary`
    - 用于追踪每个 skill 的来源 task、test 检索/注入次数与命中 case

---

## Academic / One-shot Reintroduction & Skill Reuse Stats (in progress)

### Latest plan
- 将 legacy `one-shot` solver 重新接入 `academic` 主实验路径
- 保留现有 `tir`，通过参数切换 `solver_mode`
- 将 skill 统计拆成：
  - `retrieved`: 被检索进上下文
  - `called`: 运行时真实函数调用
- 用小样本快速验证 `ds100_aime` 下是否真的发生了 skill reuse

### Completed
- 更新 `academic/executor.py`
  - `solve(..., solver_mode=...)` 支持 `tir | oneshot`
  - 新增 `ExecTrace.solver_mode`
  - 新增 `ExecTrace.skill_runtime_call_counts`
  - `tir` 路径中对预加载 skill 做 runtime wrapper 计数
  - `oneshot` 路径恢复 legacy 风格的单段代码求解
- 更新 `academic/experiments/run_experiment.py`
  - 新增 `--solver_mode`
  - trace 日志中落盘：
    - `skills_retrieved`
    - `skill_tool_counts`
    - `skill_runtime_call_counts`
    - `skills_called`
  - summary 新增 `skill_stats.train/test`
- 修复数据集边界 bug
  - `n_test=0` 现在会正确加载 0 个 test problem
  - 同类 `n is not None` 边界修复同步到：
    - `academic/datasets/aime_dataset.py`
    - `academic/datasets/math_dataset.py`
- 新增快速验证脚本
  - `academic/experiments/quick_skill_reuse_check.py`
  - 支持：
    - 指定已有 skills store
    - 指定 `oneshot | tir`
    - 输出每次 run 的 `retrieved` / `called`
    - `retrieval_mode=tfidf|embedding`
- 更新 extractor/refine 链路
  - `academic/extractor.py`
    - public `test_code` 改为 fixed input
    - refine 明确禁止修改 test
    - refine 改为增量式修 skill code
    - refine 带最近失败历史
  - 调用点已同步到：
    - `academic/experiments/run_experiment.py`
    - `academic/pipeline.py`
    - `academic/webapp/app.py`

### Verified
- `python -m py_compile` 通过：
  - `academic/executor.py`
  - `academic/experiments/run_experiment.py`
  - `academic/experiments/quick_skill_reuse_check.py`
  - `academic/datasets/aime_dataset.py`
  - `academic/datasets/math_dataset.py`

### Current blocker / observation
- 快速验证时，真正的瓶颈不是本地统计逻辑，而是远程数学求解请求本身较慢
- 另外已定位并修复两个会误导实验判断的问题：
  - `oneshot` prompt 中 `{result}` 未转义，导致 `KeyError`
  - `n_test=0` 被误当成“加载全部 test set”
- 对于“是否发生真实复用”的快速诊断，当前推荐先用：
  - 现成 skills store
  - `quick_skill_reuse_check.py`
  - `retrieval_mode=tfidf`
  - `agent_model=glm45air`
- 新增 same-question 诊断后，当前判断进一步收敛：
  - `same-question retrieval` 是好的
  - 在 `ds100_aime_v1_exp1_skills.json` 上，前 30 个有新 skill 的 evolve case，原题重查 `top_k=5` 命中率是 `100%`
  - 但在 `same_question_call_check.py` 的早期结果里，完全相同题目、且已检索回对应 skill 时，runtime `called` 仍然是 `0`
- 抽样质量检查结果：
  - 抽查 10 个代表性 skill，`tester.test_skill()` 全部通过
  - 所以目前更像是“agent 不使用 skill”，而不是“skill 普遍坏掉”
- 关于 extraction failure 的新调查结论：
  - solve trace 本身经常包含可抽取 pattern
  - extractor 在同一条 trace 上可能输出不稳定候选
  - 当前失败的关键点不是 tester 本身，而是：
    - extractor 候选不稳定
    - refine 以前没有带历史，且可能从零重写
    - public test 和 skill code 耦合太紧，一旦候选略偏就整条拒掉
- Claude 全量 `ds100_aime` rerun 已完成：
  - baseline test acc (micro): `0.9333`
  - evolve 3ep test acc (micro): `0.9000`
  - skills evolved: `174`
  - train called unique skills: `67`
  - test called unique skills: `2`
  - 结论：Claude 确实会在 train 和少量 test case 中调用 skill，但整体 test-time 利用率仍偏低，最终未超过 baseline

### Recommended next step
- 先用 `quick_skill_reuse_check.py` 做最小复用诊断
- 若确认 `oneshot` 或 `tir` 有真实 skill 调用，再回到 `run_experiment.py` 跑完整 `5x3`
- 如果同题重放下仍持续 `retrieved != 0` 且 `called = 0`，优先改：
  - executor prompt 中对 skill 使用的约束
  - skill 的暴露接口形态
  - 或 planner / executor handoff 中对“优先调用已有 skill”的显式要求

### Files created
- `app/meta_agent/skills/tests/test_data_integration.py`: 3 educational scenario traces
  - Scenario A: Auto-debug code (naive regex → real compile/exec, major update)
  - Scenario B: Student grade statistics (mean only → full statistical report, major update)
  - Scenario C: Multi-role learning discussion (hardcoded → num_rounds param, minor update)
  - Scenario C v2: discussion skill evolves to support 4 participants (major update)
- `app/meta_agent/skills/tests/integration/test_single_point.py` (3 `@pytest.mark.llm` tests)
- `app/meta_agent/skills/tests/integration/test_long_term.py` (1 `@pytest.mark.llm` test)
- All 4 tests collect cleanly; run with `pytest -m llm app/meta_agent/skills/tests/integration/`

---

## 2.3 Integration Tests: Long-Term Skill Evolution (completed)

Scenario C: two consecutive gardener runs assert ≥3 DB skill versions and final `major_version >= 2`

---

## 3.1 Skill Retrieval (completed)

### Files created/modified
- `app/meta_agent/skills/retrieval.py`: `SkillRetriever` class
  - `generate_embedding(text)` → ZhipuAI `embedding-3`, 1024-dim (explicit `dimensions=1024`)
  - `retrieve_for_query(query, db, top_k)` → async pgvector cosine search, returns `RetrievalResult`
  - `enrich_skill_with_embedding(skill, db)` → generate + persist embedding
- `app/meta_agent/skills/tests/test_retrieval.py`: 7 non-LLM tests — all passing
- `config/config.toml`: added `[llm.embedding]` section (ZhipuAI embedding-3)

---

## Academic Benchmark Adapter / BFCL Alignment (in progress)

### Latest plan

- 对齐 BFCL official/concurrent-work setting，优先确认 baseline 是否可信
- 检查官方工具格式、function-call loop、execution backend 和 evaluator 口径
- 手写少量 BFCL skill，比较 baseline / handwritten / evolve
- 输出 retrieved / used / domain tool call counts，以及参数级错误统计

### Completed

- 新增并完善 `academic/benchmarks` 子系统：
  - `bfcl.py`
  - `run.py`
  - `bfcl_skills.py`
  - `artifacts.py`
  - `types.py`
  - `README.md`
- BFCL adapter 现在支持：
  - `--bfcl-data-source bfcl_eval_bundle|hf_v3`
  - `--bfcl-adapter-mode official|path_filtered|debug_hints|full_tools`
  - `--bfcl-execution-backend auto|official|local_mock`
  - `--bfcl-prompt-style native|official|academic`
  - `--bfcl-explicit-skill-tool`
  - `--model-name` / `--model-names`
- `official` adapter mode 已改成按 `involved_classes` 暴露整类工具，贴近 BFCL multi-turn native FC 官方语义。
- execution backend 已接入 `/tmp/bfcl_pkg/unpack` 中的 `bfcl-eval` official executable backend；无法导入时才 fallback local mock。
- scorer 新增：
  - `official_valid`
  - `official_check`
  - `call_error_summary`
  - `domain_tool_called_counts`
- skills 改为默认 system notes 注入，不再默认增加 `use_skill` 工具，避免改变正式工具空间。
- handwritten skills 新增：
  - `bfcl_literal_user_text_arguments`
- evolve extractor 新增参数级错误反馈卡：
  - `bfcl_observed_error_feedback`

### Verified

- `python -m py_compile academic/benchmarks/bfcl.py academic/benchmarks/run.py academic/benchmarks/bfcl_skills.py`
- `python -m pytest -q academic/benchmarks/test_benchmark_adapters.py`
  - `3 passed`

### Current BFCL sanity results updated on 2026-04-29

- GLM-4.7 / GLM-5 / GLM-4.5-air, bundle data, official class tools, native prompt, simple `multi_turn_base_101`:
  - all three reached official valid `1/1`
  - call F1 `1.0`
  - conclusion: GLM native function calling works on BFCL; the blocker is not complete tool-call inability.
- GLM-4.7, bundle data, official class tools, native prompt, first 10 shuffled cases, per-task timeout 180s:
  - official_valid_rate `0.6667` over non-null official checks
  - one timeout: `multi_turn_base_187`
  - avg call F1 `0.6396`
  - avg tokens about `60k`
  - dominant call-error type: extra calls
- Claude default via native Anthropic `tool_use/tool_result`, 5-case subset from `multi_turn_base_101`:
  - official_valid_rate `0.8`
  - avg call F1 `0.8358`
  - avg tokens about `56k`
  - adapter bug fixed: Anthropic message normalization must deep-copy content blocks to avoid duplicated `tool_result`.
- SiliconFlow quick probes:
  - minimal one-tool probe: Qwen3-32B, Qwen3.5-35B, SiliconFlow GLM-4.7 all produced tool calls; Kimi timed out in current endpoint.
  - Qwen3-32B path-filtered BFCL simple case: official valid `0/1`, call F1 `0.8`, about 60s.
- GLM-4.7 + handwritten BFCL skill notes:
  - first 5 shuffled cases, top-2 retrieved skills: official_valid_rate `0.6`, avg call F1 `0.7366`, avg tokens about `55k`.
  - no clear gain over baseline; skill notes changed behavior in hard cases but did not fix strict official validity.
- GLM-4.7 offset/debug runs for peripheral diagnosis:
  - official baseline with first 3 held-out tasks: official_valid_rate `0.5` over non-timeout checks; `multi_turn_base_66` timed out.
  - official evolve with first 2 train tasks: both train tasks timed out; no skills were generated; test subset still reached official_valid_rate `1.0`.
  - path-filtered baseline on test-offset 3, n-test 3: no timeout, official_valid_rate `0.3333`, avg call F1 `0.7919`.
  - path-filtered evolve with train-offset 2/test-offset 3: train official_valid_rate `0.0`, extracted 8 skill cards from failed traces, test official_valid_rate `0.5` over non-timeout checks; no reliable gain.

### Key findings

- HF `BFCL_v3` data and current `bfcl-eval` bundle differ on some cases/docs. Example: `multi_turn_base_187` in HF v3 hides the concrete booking id, while bundle v4 exposes `insurance_12345` and adds `get_booking_history`.
- Current BFCL failures are mostly strict state/parameter/action-set failures, not total tool-call absence:
  - `contact_customer_support.message` is expanded instead of preserving concise expected text.
  - `create_ticket.description` is expanded.
  - `high-priority` is mapped to `priority=5` while golden answer uses `priority=4`.
  - optional `insurance_id` may be passed where golden uses only `booking_id`.
- First-10 GLM-4.7 baseline shows many extra calls; official checker can still pass some extra-call cases if final state matches, so `official_valid` is the primary metric and `call_f1` is diagnostic.
- Tool space is a real environmental factor: `path_filtered` reduced timeout relative to full official class tools on the same task band, but it did not solve strict validity.
- The current old evolve/extraction loop can extract skill cards from failed traces; those cards are not reliable enough for BFCL yet and may add latency or behavioral bias.
- SiliconFlow Qwen/Kimi/GLM with BFCL long tool schemas remains slow; minimal tool-call probes confirm API-level tool calling works.
- Direct official `bfcl-eval` GLM handler import currently hits `tree_sitter` dependency/API incompatibility in the shared Python 3.13 environment; should be retried in an isolated pinned environment.

### Implementation changes in this round

- Added provider protocol selection:
  - `--bfcl-tool-api-style auto|openai|openai_stream|anthropic_direct`
  - `auto` maps Claude to Anthropic native tools and Qwen-like models to streaming OpenAI-compatible path.
- Added `anthropic>=0.45.0` dependency.
- Added direct Anthropic tool-call implementation with correct `tool_use/tool_result` history.
- Added streaming OpenAI-compatible tool-call implementation for Qwen/SiliconFlow probes.
- Added `--top-k-skills` to control skill-note prompt cost.
- Added `--partial-output` and `--max-task-seconds` to make long BFCL runs observable and interrupt-safe.
- Added `--train-offset` and `--test-offset` to select deterministic sub-bands of the shuffled split without editing data files.
- Evolve now propagates partial-output and per-task timeout into both train and test phases, writing phase-specific partial files.
- Verified:
  - `python -m py_compile academic/benchmarks/bfcl.py academic/benchmarks/run.py academic/benchmarks/bfcl_skills.py`
  - `python -m pytest -q academic/benchmarks/test_benchmark_adapters.py`

### Next recommended steps

- Use first-10 GLM-4.7 baseline as a bounded diagnostic set, not as final paper result.
- Before larger evolve, compress skill notes and make retrieval more selective; full official tools + skills currently increases latency and has no clear gain.
- Treat the current phase as peripheral debugging, not algorithm innovation: environment, model schema, and skill format must be controlled before interpreting evolve results.
- Build an isolated Python environment for `bfcl-eval` official handler to compare official GLM/Qwen/Kimi handler behavior against this runner.
- Select a no-timeout, non-saturated BFCL subset for baseline vs evolve; do not expand to 50/150 until cost and timeout behavior are controlled.
