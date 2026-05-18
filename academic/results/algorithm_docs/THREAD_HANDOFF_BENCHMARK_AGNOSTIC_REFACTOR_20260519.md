# Thread Handoff: Benchmark-Agnostic Skill Evolution Refactor

Date: 2026-05-19

Working directory: `/home/lixujun/skill_evolving`

Current branch: `spreadsheet-multiturn-notebook`

Latest committed revision at handoff:

```text
0358555 Add cross-benchmark refactor contract tests
```

## 1. 当前状态摘要

这个 thread 的主线是在已有 BFCL / Spreadsheet skill-evolving 实现基础上，把算法中可复用的维护逻辑逐步抽到 `academic/benchmarks/core/`，同时保持 BFCL 原链路不被破坏，并把 Spreadsheet 从单文件 adapter 拆成更接近 BFCL 的粒度。

本 thread 已完成到 “Chapter 3 / Chapter 3.1”：

- 已建立 benchmark-agnostic maintenance core skeleton。
- 已扩展公共层单元测试。
- 已将 Spreadsheet maintenance facade 接入公共层。
- 已将 BFCL 的部分 low-risk maintenance primitive 接入公共层。
- 已新增跨 benchmark refactor contract tests，验证两个 bench 都真的走公共层，且兼容旧入口。
- 已更新主重构计划文档，包含精确代码片段、行号、测试输出。
- 已为每个阶段做 git commit。

当前工作区仍有两个本 thread 没有处理的无关脏状态：

```text
D  academic/results/algorithm_docs/SPREADSHEET_NOTEBOOK_IMPROVEMENT_PLAN_20260518.md
?? tmp_run_local_claude_bfcl_debug.py
```

这两个状态在开始本次 handoff 前已经存在。不要在不了解来源的情况下提交或删除。

## 2. 最近提交链

本 refactor 相关的关键提交：

```text
0358555 Add cross-benchmark refactor contract tests
37df7a6 Share BFCL maintenance primitives with common core
dc3262e Refactor spreadsheet maintenance facade onto common core
314fd17 Expand common maintenance core tests
9532215 Add benchmark-agnostic maintenance core skeleton
6ef4aa7 Split spreadsheet adapter by responsibility
9b83ab8 Refactor spreadsheet maintenance structure
6f8e9be Share credit exposure scope across benchmarks
```

提交含义：

- `9532215`: 新增公共维护核心模块，包括 credit events、bundle cases、micro/macro maintenance、relation graph，以及第一版公共测试。
- `314fd17`: 扩展公共维护核心测试，覆盖更多别名、边界和 orchestration 行为。
- `dc3262e`: Spreadsheet adapter 改为 compatibility facade，真实 maintenance 逻辑搬到 `spreadsheet/maintenance/adapter.py`，并接入公共 helper。
- `37df7a6`: BFCL 保守接入公共 credit/micro/bundle budget primitive，保留原 BFCL 行为。
- `0358555`: 新增跨 benchmark 契约测试，防止后续重构破坏 facade、公共 micro 顺序、BFCL normalization、bundle trim version bump 等关键行为。

## 3. 当前已验证测试

最近一次新增测试：

```text
$ pytest -q academic/benchmarks/tests/test_benchmark_refactor_contracts.py
.......                                                                  [100%]
7 passed, 10 warnings in 1.82s
```

最近一次相关回归：

```text
$ pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py academic/benchmarks/tests/bfcl_related/test_experiment.py academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py academic/benchmarks/tests/test_common_maintenance_core.py academic/benchmarks/tests/test_benchmark_refactor_contracts.py
........................................................................ [ 80%]
..................                                                       [100%]
90 passed, 10 warnings in 27.34s
```

最近一次 py_compile：

```text
$ python -m py_compile academic/benchmarks/tests/test_benchmark_refactor_contracts.py academic/benchmarks/spreadsheet/adapter.py academic/benchmarks/spreadsheet/maintenance/adapter.py academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/maintenance/adapter.py academic/benchmarks/core/credit_events.py academic/benchmarks/core/bundle_cases.py academic/benchmarks/core/micro_maintenance.py
```

已知注意点：

- 如果跑更广的 `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`，历史上曾遇到一个非本次重构引入的 fixture 缺失：

```text
/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_official_tracecheck_evolve_3x3_partial_train.json
```

这个缺失不应与本次公共层重构混为一谈。

## 4. 核心代码文件含义

### 4.1 公共层：`academic/benchmarks/core/`

这些文件是 benchmark-agnostic 的算法骨架。后续接入新 benchmark 应尽量复用这里的能力，benchmark adapter 只做实例化。

| 文件 | 含义 |
| --- | --- |
| `types.py` | benchmark-neutral task/result 类型，以及 skill repository 类型 re-export。`BenchmarkTask`、`BenchmarkResult`、`SkillArtifact`、`SkillBundleCase` 等都从这里统一使用。 |
| `artifacts.py` | `ArtifactStore` compatibility re-export。真实 store 在 `academic/skill_repository/store.py`。 |
| `maintenance_adapter.py` | 定义 `MaintenanceRunConfig`、`BenchmarkMaintenanceAdapter` protocol、task snapshot、generic aggregate/cost summary。各 benchmark adapter 应实现这里的 hooks。 |
| `evolution.py` | benchmark-agnostic online skill evolution runner。它调 adapter 的 `run_task`、`assign_credit`、`apply_credit_bundle_cases`、`run_micro_maintenance`、`run_macro_maintenance`。 |
| `runner.py` | 旧 core runner 和 BFCL baseline 等兼容运行逻辑。仍有历史 BFCL 路径，不能随意删。 |
| `llm_text.py` | 文本 LLM 调用抽象，Spreadsheet executor 等通过它接入不同 API style。 |
| `cost_accounting.py` | 细粒度 token/cost 统计：input、cache input、output、role/provider 等口径。实验结果汇总会用。 |
| `skill_injector.py` | runtime skill injector/filter 逻辑。用于测试阶段和训练阶段根据 context 选 skill 进入 executor prompt。 |
| `credit_scope.py` | 把不同 benchmark 的 retrieved/injected/used/called 字段归一成 skill exposure，用于 credit candidate policy。 |
| `credit_events.py` | 公共 credit event normalize、helpful/harmful 判定、evidence 写入、summary。 |
| `bundle_policy.py` | 公共 bundle case budget/trim 策略。按 polarity 和总数限制保留高价值 case，并写入 trim metadata。 |
| `bundle_cases.py` | 公共 credit-to-bundle orchestration：normalize suggestion、检查 actionable credit、调用 benchmark-specific case builder、写入对应 bundle bucket。 |
| `micro_maintenance.py` | 公共 micro maintenance loop：选择 target，先按 credit 触发 refine，再 bundle test，失败后按 repair limit 继续 refine/test。 |
| `macro_maintenance.py` | 公共 macro maintenance skeleton：用于 window-level promotion/refactor/filter/summary 的抽象 hook。 |
| `relation_graph.py` | benchmark-neutral relation graph node/edge 表达，用于未来从 BFCL hetero overlap graph 进一步抽象。 |
| `registry.py` | benchmark registry 辅助。 |
| `maintenance_utils.py` | 时间、stable id、json block、env helper 等小工具。 |

### 4.2 SpreadsheetBench：`academic/benchmarks/spreadsheet/`

当前 Spreadsheet 已拆成 facade + focused modules。重要目标是保留旧 import，同时把算法逻辑向公共层靠拢。

| 文件 | 含义 |
| --- | --- |
| `adapter.py` | Spreadsheet compatibility facade。继续 re-export 旧测试/旧外部调用依赖的 symbol。`run_spreadsheet_task` 和 `run_spreadsheet_task_notebook` 在这里保留，maintenance adapter 内部通过 facade 调用，以便 monkeypatch 仍有效。 |
| `maintenance/adapter.py` | Spreadsheet 的 benchmark-specific maintenance adapter。实现 `SpreadsheetMaintenanceAdapter`，负责 trace projection、credit prompt、credit bundle case 构造、micro/macro hook、bundle test/refine 接线。这里已接入公共 `apply_credit_evidence`、`apply_credit_bundle_suggestions`、`run_generic_micro_maintenance` 等。 |
| `maintenance/__init__.py` | Spreadsheet maintenance package export。 |
| `executor.py` | Spreadsheet executor。支持 single-turn 和 notebook/multi-turn 模式，负责 LLM 写代码、执行 openpyxl、收集 trace/metrics/token。 |
| `loader.py` | SpreadsheetBench 数据加载。 |
| `models.py` | Spreadsheet trace/model 结构。 |
| `prompts.py` | Spreadsheet extractor/credit/system prompts，以及 notebook done pattern。 |
| `skill_runtime.py` | Spreadsheet function skill runtime。包括把 skill 渲染成可 import 的 Python function、写 `skill_library.py`、解析 called skill functions。 |
| `trace_projection.py` | Spreadsheet task/result/skill compact projection。maintenance prompt 和 credit bundle case 都依赖这里的 compact fragment。 |
| `verifier.py` | Spreadsheet 输出验证。比较 output workbook 和 golden workbook 的 answer range。 |

Spreadsheet 当前关键设计：

- `adapter.py` 不是死代码，是兼容层。很多测试和外部 monkeypatch 都针对这里。
- `maintenance/adapter.py` 里 `_compat_module()` 会 import facade，确保 monkeypatch facade 后仍影响真实 maintenance path。
- `SpreadsheetMaintenanceAdapter.run_micro_maintenance()` 已使用公共 `run_generic_micro_maintenance()`。
- Spreadsheet function skill 可以在 executor 侧被写入 Python space 并 import 调用；knowledge/workflow skill 仍通过 prompt 注入。

### 4.3 BFCL：`academic/benchmarks/bfcl/`

BFCL 仍是主实验链路，当前只做了 low-risk 公共层接入，尚未完全拆成 Spreadsheet 那样的模块粒度。

| 文件 | 含义 |
| --- | --- |
| `adapter.py` | BFCL benchmark adapter / executor-facing glue，包含 step-level skill context injection 等 BFCL runtime 行为。 |
| `executor.py` | BFCL 执行逻辑。负责 multi-turn/tool-call 执行和 trace 采集。 |
| `loader.py` | BFCL 数据加载。 |
| `tool_clients.py` | BFCL tool client / tool execution 适配。 |
| `call_utils.py` | tool call parsing/normalization 等辅助。 |
| `constants.py` | BFCL 常量。 |
| `models.py` | BFCL 结构模型。 |
| `retrieval.py` | BFCL skill retrieval 辅助。 |
| `scoring.py` | BFCL scoring / official validation glue。 |
| `skills.py` | BFCL default skill store / seed skill 等。 |
| `environments.py` | BFCL environment/tool namespace 配置。 |
| `maintenance/adapter.py` | BFCL maintenance 的大文件，包含 extractor、bundle builder/test/refine、overlap refactor、relation graph 更新等。当前已把 `trim_bundle_cases()` 接到公共 `trim_bundle_cases_to_budget()`，但仍有大量 BFCL-specific 逻辑等待后续拆分。 |
| `maintenance/generic_adapter.py` | BFCL 接入 generic evolution adapter 的辅助。 |
| `related/experiment.py` | BFCL related-task paper experiment driver。包含 train/test schedule、micro/macro window、locks、checkpoint/resume、credit events、output aggregation 等。当前 `_credit_event_records`、`_apply_credit_case_evidence`、`_micro_write_target_names` 已接入公共 helper。 |
| `related/manifest.py` | related train/test manifest 构造与校验。 |
| `related/segment_index.py` | trace segment vector index。 |
| `related/analysis.py` | experiment result analysis。 |
| `related/checkpointing.py` | checkpoint/resume 辅助。 |
| `related/credit.py` | BFCL credit 相关辅助拆分。 |
| `related/pending_skills.py` | pending skill 相关辅助。 |
| `related/proxy_runner.py` | proxy runner / local API style runner。 |
| `related/suites.py` | related suite 配置。 |
| `diagnostics/*.py` | 诊断脚本：case selection、result compare、progress monitor、runtime/token report 等。 |
| `legacy/*.py` | 历史 maintenance lab / probe，除非明确迁移，不应作为主链路修改目标。 |

BFCL 当前关键设计：

- 主实验仍以 `related/experiment.py` 和 `maintenance/adapter.py` 为核心。
- 已经有细粒度锁/并发相关代码，但本 handoff 没有重新运行大实验。
- 当前公共层接入是保守的，不应假设 BFCL 已完全 benchmark-agnostic。
- 下一步 Chapter 4 才是 BFCL 结构拆分。

### 4.4 SkillsBench：`academic/benchmarks/skillsbench/`

| 文件 | 含义 |
| --- | --- |
| `adapter.py` | SkillsBench 初步接入。此前已跑过 mock/baseline fixture 结果，但不是本 thread 的主要改动。 |
| `__init__.py` | package export。 |

### 4.5 测试文件含义

| 文件 | 含义 |
| --- | --- |
| `academic/benchmarks/tests/test_common_maintenance_core.py` | 公共层主测试。覆盖 credit normalization/evidence、bundle case application、bundle budget、micro/macro skeleton、relation graph。 |
| `academic/benchmarks/tests/test_benchmark_refactor_contracts.py` | 本 thread 最后新增的跨 benchmark 重构契约测试。验证 Spreadsheet facade/公共 micro、BFCL common primitive 接入和行为不变。 |
| `academic/benchmarks/tests/test_spreadsheet_evolution.py` | Spreadsheet executor/evolution/skill runtime/maintenance 的主要测试。包含 function skill import/call、notebook、多轮、credit/micro 等。 |
| `academic/benchmarks/tests/test_generic_evolution.py` | generic evolution runner 测试。 |
| `academic/benchmarks/tests/test_credit_scope.py` | skill exposure / credit candidate policy 测试。 |
| `academic/benchmarks/tests/test_skill_injector_budget.py` | skill injector budget / prompt 压缩相关测试。 |
| `academic/benchmarks/tests/test_skillsbench_adapter.py` | SkillsBench adapter mock/baseline 测试。 |
| `academic/benchmarks/tests/bfcl_related/test_experiment.py` | BFCL related experiment driver 测试。覆盖 manifest、credit、micro/macro、locks、resume 等。 |
| `academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py` | BFCL paper algorithm contract 测试。覆盖 credit assigner、bundle suggestion、micro/macro、overlap/refactor 等算法约束。 |
| `academic/benchmarks/tests/maintenance/test_bundle_agent.py` | bundle builder/maintainer/refiner 相关测试。 |
| `academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py` | runtime optimization、bundle budget、并发/锁等场景测试。 |
| `academic/benchmarks/tests/bfcl/test_benchmark_adapters.py` | BFCL adapter 更大范围测试。注意可能受历史 fixture 缺失影响。 |

## 5. 结果与文档文件含义

### 5.1 算法/实验文档目录

目录：`academic/results/algorithm_docs/`

| 文件 | 含义 |
| --- | --- |
| `BENCHMARK_AGNOSTIC_REFACTOR_PLAN_20260518.md` | 当前 benchmark-agnostic refactor 的主计划和执行记录。后续 thread 应继续在这里追加 chapter 进度，要求写代码片段、行号、测试结果。 |
| `THREAD_HANDOFF_BENCHMARK_AGNOSTIC_REFACTOR_20260519.md` | 本 handoff 文档。 |
| `EXPERIMENT_CHANGELOG_AFTER_FULL_ALGO_20260518.md` | 从完整算法实现后开始的实验/代码改动时间线。用户希望它足够详细，用于追溯每次实验 setting 差异。 |
| `EXPERIMENT_RESULTS_MASTER_TABLE_20260518.md` | 实验结果总表。应把同一 bench 放一起，并记录参数、manifest、top-k、injector mode、token/cost 等。 |
| `BFCL_LATEST_VS_PREVIOUS_COMPARISON_20260518.md` | BFCL 最新一次和前一次结果差异分析。 |
| `BFCL_RELATED150_50_MIDRUN_PENDING_REFINER_CASE_STUDY_20260517.md` | BFCL 150/50 中途 pending/refiner 问题 case study。 |
| `BFCL_SPREADSHEET_FINE_GRAINED_COST_RETEST_20260518.md` | BFCL / Spreadsheet 细粒度 token/cost 重测报告。 |
| `BFCL_SPREADSHEET_GUARDFIX_RESULTS_20260518.md` | guardfix 后两个 bench 的结果汇总。 |
| `BFCL_SPREADSHEET_TOKEN_OVERHEAD_CASE_STUDY_20260518.md` | token overhead case study，分析 skill prompt、adapter、executor/injector 开销来源。 |
| `RUNTIME_COST_ACCOUNTING_AND_INJECTOR_ANALYSIS_20260518.md` | runtime cost accounting 和 injector 是否生效的分析文档。 |
| `SPREADSHEET_INCREMENTAL_IMPROVEMENT_PLAN_20260518.md` | Spreadsheet 分章节逐步改进计划。用户要求按章节执行、每章测试。 |
| `PRESENTATION_20260518.md` | 面向展示的中文 story 文档。隐去过多实现细节，强调软工复用性、流程划分、skill 属性、TRL 信号链路和最新结果。 |
| `PAPER_NEW_ALGORITHM_IMPLEMENTATION_MAP.md` | paper algorithm 到代码实现位置的映射。 |
| `PAPER_NEW_ALGORITHM_IMPLEMENTATION_REPORT.md` | paper algorithm 实现报告。 |
| `PAPER_NEW_ALGORITHM_TEST_PLAN.md` | 算法测试计划。 |
| `PAPER_NEW_CODE_REVIEW_RESPONSE_20260516.md` | 早期 code review response。 |
| `PAPER_NEW_CODE_REVIEW_2.md` | 第二轮 code review。 |
| `PAPER_NEW_CODE_REVIEW_3_RESPONSE_20260517.md` | 第三轮 review response。 |
| `PAPER_NEW_IMPLEMENTATION_UPDATE_20260517.md` | 2026-05-17 实现更新。 |
| `PAPER_NEW_REFACTOR_IMPLEMENTATION_PLAN_20260517.md` | 早期 BFCL skill evolution correction/refactor plan。 |
| `BFCL_ENTRYPOINTS_AND_PATH_CLEANUP_20260516.md` | BFCL entrypoint / path cleanup 记录。 |
| `draft.jpg` | presentation 中可能引用的图像资产。 |

注意：`SPREADSHEET_NOTEBOOK_IMPROVEMENT_PLAN_20260518.md` 当前在 git status 里是 deleted，但不是本 thread 删除的。不要在不知道原因时提交这个删除。

### 5.2 实验输出目录

目录：`academic/results/`

这些文件多数是历史实验输出，不是本 thread 新生成。不要随意删除，除非用户明确要求清理且已确认哪些保留。

命名约定：

- `*.json`: 完整实验或测试输出。
- `*_partial.json`: 中断/网络失败/未完成时的部分结果。
- `*_checkpoint.json`: 可 resume 的训练/evolve checkpoint。
- `*_skills.json`: 训练后 skill store snapshot。
- `*_final_skills.json`: BFCL evolve 结束后的 final skill store。
- `*.log`: 运行日志。
- `*_status.env`: 后台实验状态文件，通常记录 PID/status/output path 等。
- `latest_parallel_train50_tag.txt`: 最近 parallel train50 tag 的指针。
- `retest_logs/*.log`: cost retest 的日志集中目录。
- `spreadsheet_notebook_*_work*/`: Spreadsheet notebook/multi-turn run 的临时工作目录，含 input/output xlsx 和 driver。

关键输出分组：

| 文件/模式 | 含义 |
| --- | --- |
| `claude_proxy_related50_50_guardfix_20260517_232531_*` | BFCL related 50/50 guardfix 主结果组。包含 baseline、evolve、checkpoint、final skills、analysis、test task ids、run log、status。 |
| `bfcl_train50_20260518_202840*` | BFCL train50 训练输出组。含 checkpoint、json、log、skills。 |
| `bfcl_guardfix_trainedstore_test50_rerun*_20260518_*` | 使用训练后 store 对 BFCL test50 做 rerun 的输出，含 partial/log/status。 |
| `cost_retest_bfcl_baseline_20260518*` | BFCL baseline cost retest。 |
| `cost_retest_bfcl_compact_20260518*` | BFCL compact skill context cost retest。 |
| `cost_retest_bfcl_fullskill_20260518*` | BFCL full skill prompt cost retest。 |
| `cost_retest_sheet_baseline_20260518.json` | Spreadsheet baseline cost retest。 |
| `cost_retest_sheet_compact_20260518.json` | Spreadsheet compact skill context cost retest。 |
| `cost_retest_sheet_compact_callable*_20260518*` | Spreadsheet compact + callable function skill retest。 |
| `cost_retest_sheet_fullskill_20260518.json` | Spreadsheet full skill prompt cost retest。 |
| `cost_token_reaccounting_20260518.json` | token/cost 重新归账结果。 |
| `token_overhead_case_analysis_data.json` | token overhead case study 用的数据。 |
| `spreadsheet_baseline_test50_20260517_233447*` | Spreadsheet baseline test50 结果、日志和状态。 |
| `spreadsheet_evolve_50_50_20260518_020201*` | Spreadsheet evolve 50/50 的早期日志/status。 |
| `spreadsheet_evolve_50_50_true_20260518_022120*` | Spreadsheet evolve 50/50 true run 输出，含 normalized callable skills、skills snapshot、log/status。 |
| `spreadsheet_real_evolve_smoke_2_1*_20260518_*` | Spreadsheet real small smoke evolve 结果。 |
| `spreadsheet_notebook_smoke_1_20260518.json` | Spreadsheet notebook/multi-turn smoke。 |
| `spreadsheet_notebook_hard_55427*_20260518.json` | Spreadsheet notebook 困难样例 55427 的直接/多轮结果。 |
| `spreadsheet_notebook_55427_work*` | 55427 样例的执行工作目录，含 workbook 和 generated driver。 |
| `spreadsheet_notebook_train50_20260518_202840.log` | Spreadsheet notebook train50 日志。 |
| `skillsbench_*_20260518.json` | SkillsBench mock/baseline/official skill retrieval 初步结果。 |
| `debug_retest.json`、`retest_logs/debug_bg.log` | 临时 debug retest 输出。先不要删除，除非确认不再需要。 |
| `_reorganize.py` | results 目录整理脚本。 |

## 6. 当前算法/架构进度

### 已完成

1. 公共 credit event 层：
   - Normalize skill name / judgment / confidence / evidence strength。
   - 统一 helpful/harmful 判定。
   - 统一 evidence 写入 helpful/harmful/repeated bucket。

2. 公共 bundle case 层：
   - 从 credit event 读取 `bundle_case_suggestions`。
   - 判断 harmful/helpful/integration 是否 actionable。
   - 调 benchmark-specific `build_case` 构造可 replay 的 native bundle case。
   - 使用公共 budget helper 限制每类和总数。

3. 公共 micro maintenance：
   - 选择 task-local targets。
   - 先根据 credit signal 触发 refine。
   - 再跑 bundle test。
   - bundle test fail 后最多 repair N 轮。

4. 公共 macro maintenance skeleton：
   - 已有 hook skeleton 和 summary。
   - Spreadsheet macro 已接入 promotion/dedupe/filter。
   - BFCL macro 仍在 BFCL 大文件里。

5. Spreadsheet adapter 拆分：
   - `spreadsheet/adapter.py` 为 facade。
   - `spreadsheet/maintenance/adapter.py` 为真实 maintenance 实现。
   - executor/loader/verifier/runtime/projection 分文件。

6. BFCL low-risk 公共层接入：
   - credit event normalization。
   - credit evidence snapshot apply。
   - micro target selection helper。
   - bundle case trimming helper。

7. 跨 bench 契约测试：
   - 已验证 Spreadsheet 和 BFCL 两边关键路径没有断。

### 尚未完成

1. Chapter 4: BFCL 结构拆分。
   - 从 `bfcl/related/experiment.py` 抽出 credit、micro、macro、checkpoint/report glue。
   - 从 `bfcl/maintenance/adapter.py` 抽出 bundles、replay、refine、overlap、prompt builders。
   - 保留原入口 facade，不能破坏 CLI/import/resume。

2. Chapter 5: 更多 env 接入准备。
   - 写 fake benchmark adapter 文档和测试。
   - 明确新 benchmark 最小接入面：loader、executor、verifier、trace projector、bundle fragment builder、optional function runtime。

3. Spreadsheet 后续优化。
   - 用户之前要求“每章只改一个部分，测试通过再继续”。
   - 当前已做结构层重构，后续若继续 Spreadsheet 性能/成本优化，需要回到 `SPREADSHEET_INCREMENTAL_IMPROVEMENT_PLAN_20260518.md`。

4. SkillsBench 深入接入。
   - 已有初步 adapter 和 mock/baseline fixture。
   - 尚未作为主实验 bench 完整跑 evolve。

5. 实验层。
   - 本 thread 没有启动新的真实大实验。
   - 后续实验结果必须同步更新 `EXPERIMENT_CHANGELOG_AFTER_FULL_ALGO_20260518.md` 和 `EXPERIMENT_RESULTS_MASTER_TABLE_20260518.md`。

## 7. 下一个 Thread 建议执行顺序

建议不要直接大改 BFCL 主文件。按下面顺序继续：

1. 先读：
   - `academic/results/algorithm_docs/BENCHMARK_AGNOSTIC_REFACTOR_PLAN_20260518.md`
   - 本 handoff 文档
   - `academic/benchmarks/tests/test_benchmark_refactor_contracts.py`
   - `academic/benchmarks/core/*.py`

2. 确认工作区：

```text
git status --short
```

如果仍看到：

```text
D  academic/results/algorithm_docs/SPREADSHEET_NOTEBOOK_IMPROVEMENT_PLAN_20260518.md
?? tmp_run_local_claude_bfcl_debug.py
```

不要误提交。

3. 若继续 Chapter 4，先写计划到：

```text
academic/results/algorithm_docs/BENCHMARK_AGNOSTIC_REFACTOR_PLAN_20260518.md
```

然后一次只拆一个 BFCL 责任块，并为每块补测试。

4. 每次改动后至少跑：

```text
pytest -q academic/benchmarks/tests/test_benchmark_refactor_contracts.py
pytest -q academic/benchmarks/tests/test_common_maintenance_core.py
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py
pytest -q academic/benchmarks/tests/bfcl_related/test_experiment.py
pytest -q academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py
```

如果改到 BFCL maintenance/refactor/bundle prompt，再加：

```text
pytest -q academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py
pytest -q academic/benchmarks/tests/maintenance/test_bundle_agent.py
```

5. 每个 chapter 完成后：
   - 更新 `BENCHMARK_AGNOSTIC_REFACTOR_PLAN_20260518.md`，必须写具体函数/行号/代码片段/测试输出。
   - `git add` 只 add 本次相关文件。
   - `git commit`。

## 8. 接手时的风险点

1. 不要把 facade 当无用层删掉。
   - Spreadsheet `adapter.py` 的存在是为了兼容旧 import 和 monkeypatch。
   - 新测试已经显式保护这一点。

2. 不要把 BFCL 的 `used` 和 `injected` 混淆。
   - 当前契约：prompt injected 不等于 used。
   - `test_bfcl_credit_records_normalize_and_preserve_task_metrics` 断言 `used` 在只 injected 时仍是 `False`。

3. 不要把所有 weak credit 都拿去 refine/test。
   - 当前契约：weak/uncertain 且没有 credit bundle case 时，micro 不触发。
   - 这是为控制 bundle/replay/refine 压力。

4. 不要让公共 bundle trim 自己 bump version。
   - 公共 helper 只返回 changed 并写 trim metadata。
   - BFCL wrapper 自己 bump bundle version。
   - Spreadsheet apply credit bundle case 当前也在 adapter 层 bump bundle version。

5. 不要在公共层引入 BFCL-only trace/schema。
   - 公共层只认 normalized event、bundle rows、adapter hooks。
   - BFCL/Spreadsheet native trace fragment 由各自 adapter 构造。

6. 大实验输出不要随手删。
   - 结果文件之间有对照和论文表格引用关系。
   - 若清理，需要先更新结果总表和 changelog，或把保留/删除列表写清楚。

## 9. 本 Handoff 创建时的最终状态

最后一次检查前的状态：

```text
D  academic/results/algorithm_docs/SPREADSHEET_NOTEBOOK_IMPROVEMENT_PLAN_20260518.md
?? tmp_run_local_claude_bfcl_debug.py
```

本 handoff 文档创建后应被单独提交。提交时不要包含上面两个无关状态。

