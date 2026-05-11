# Benchmark 子系统使用说明

本子系统把新的 agentic benchmark 从原来的数学 `Problem(question, answer)` 链路中拆出来，避免破坏已有 AIME/MATH 实验。当前 registry 覆盖五个目标榜单：

| key | 状态 | 主要用途 | skill format |
|---|---|---|---|
| `bfcl_v3` | 已实现 baseline adapter | 多轮函数调用、API/tool 规则复用 | tool rule card + workflow card |
| `spreadsheet` | 已实现 smoke adapter | xlsx 操作、文档式 skill package | `SKILL.md` + scripts/references |
| `appworld` | registry only | app/API 长程 workflow | planning/function/atomic skill |
| `officeqa` | registry only | 企业文档 grounded QA | document/table workflow |
| `tir_bench` | registry only | tool-integrated reasoning | task skill + action experience |

## Skill Repository Maintenance 设计入口

如果你现在关心的是：

- skill repository 的对象模型
- bundle / test result 分离
- extractor / bundle builder / refiner / stale resolver 的职责
- BFCL executor 到 maintenance 的完整时间顺序
- audit log 和前端看板如何接起来

直接看：

- [../skill_repository/MAINTENANCE_ARCHITECTURE.md](../skill_repository/MAINTENANCE_ARCHITECTURE.md)
- [../skill_repository/README.md](../skill_repository/README.md)
- [../skill_repository/MAINTENANCE_API_REFERENCE.md](../skill_repository/MAINTENANCE_API_REFERENCE.md)

## 运行命令

列出榜单元数据：

```bash
cd /home/lixujun/skill_evolving
python -m academic.benchmarks.run --list
```

BFCL-v3 smoke baseline：

```bash
python -m academic.benchmarks.run \
  --benchmark bfcl_v3 \
  --mode baseline \
  --llm-config bigmodel \
  --bfcl-data-source bfcl_eval_bundle \
  --bfcl-adapter-mode official \
  --bfcl-prompt-style native \
  --bfcl-execution-backend auto \
  --n-train 50 \
  --n-test 5 \
  --tag bfcl_smoke_glm47
```

列出 BFCL 命名实验套件：

```bash
python -m academic.benchmarks.bfcl_experiment_suite --list
```

打印某个套件的实际命令：

```bash
python -m academic.benchmarks.bfcl_experiment_suite \
  --suite glm47_baseline_50_150 \
  --dry-run
```

运行 BFCL 官方对齐 50/150 baseline：

```bash
python -m academic.benchmarks.bfcl_experiment_suite \
  --suite glm47_baseline_50_150
```

比较两份 BFCL 结果：

```bash
python -m academic.benchmarks.compare_bfcl_results \
  academic/results/bfcl_v3_glm47_official_realign_none_10_baseline.json \
  academic/results/bfcl_v3_glm47_official_realign_prompt_infoonly_10_baseline.json
```

SpreadsheetBench-Verified smoke baseline：

```bash
python -m academic.benchmarks.run \
  --benchmark spreadsheet \
  --mode baseline \
  --llm-config default \
  --n-train 200 \
  --n-test 3 \
  --tag spreadsheet_smoke_claude
```

输出默认写到：

```text
academic/results/{benchmark}_{tag}_baseline.json
```

数据缓存默认写到：

```text
data/benchmarks/
```

## 输入输出

BFCL-v3 输入：

- 默认使用本地解包的 `bfcl-eval` bundle：`/tmp/bfcl_pkg/unpack/bfcl_eval/data/BFCL_v4_multi_turn_base.json`
- 可通过 `--bfcl-data-source hf_v3` 切回 Hugging Face raw `BFCL_v3_multi_turn_base.json`
- 注意：HF v3 与当前 `bfcl-eval` bundle 在若干 case 和 function docs 上不一致，例如 `multi_turn_base_187` 在 HF v3 题面未显式给出 booking id，而 bundle v4 已给出，且 bundle docs 新增 `get_booking_history`
- `possible_answer/*.json`
- `multi_turn_func_doc/*.json`
- 每个 case 包含多轮 user messages、initial config、工具路径和 expected function call sequence。

BFCL-v3 输出：

- `task_success`
- `official_valid` / `official_check`
- `official_avg_at_k` / `official_pass_at_k`
- `turn_success_rate`
- `call_precision / call_recall / call_f1`
- `call_error_summary`
- `n_expected_calls / n_actual_calls / n_matched_calls`
- 完整 tool-call trace、tool result、token、latency、retrieved/used skill 统计。

Spreadsheet 输入：

- Hugging Face `spreadsheetbench_verified_400.tar.gz`
- `dataset.json`
- 每个 case 的 `*_init.xlsx`、`*_golden.xlsx`、`prompt.txt`
- `answer_sheet` 与 `answer_position` 用于 verifier。

Spreadsheet 输出：

- `pass`
- `cell_accuracy`
- `checked_cells`
- `mismatched_cells`
- LLM 生成的 openpyxl code、stdout/stderr、token、latency、retrieved skill。

## 当前实现边界

BFCL 当前实现已经拆成三层指标和执行路径：

- `official` adapter mode：按 BFCL multi-turn native FC 官方语义，向模型暴露 `involved_classes` 对应的整类工具。
- `path_filtered` adapter mode：只暴露 case `path` 中的工具，用于 token-saving / debug ablation，不作为正式 BFCL baseline。
- `debug_hints` adapter mode：额外提示 expected tool names，只用于诊断，不作为正式结果。
- `auto/official` execution backend：优先调用 `bfcl-eval` 官方 executable backend，返回真实 tool results；无法导入时才回退 local mock。
- scorer 同时报 `call_f1` 诊断指标和 `official_valid` state/response checker。
- 汇总层同时保留两套口径：
  - `avg_score / pass_at_k` 基于当前 runner 的 strict `task_success` / `call_f1`
  - `official_valid_rate / official_avg_at_k / official_pass_at_k` 基于 BFCL 官方 checker，更适合和官方/同期工作对齐
- BFCL 官方约束更接近 step budget；本 runner 默认不设置 wall-clock task timeout，`--max-task-seconds` 只作为长跑防挂 guard。汇总结果会报告 `timeout_rate / avg_elapsed_s / max_elapsed_s`。

BFCL prompt style：

- `native`：默认，不额外加系统 prompt，最接近 OpenAI-compatible native function calling 官方 handler。
- `official`：加入 BFCL prompting-style 系统说明，但仍使用 native tool calls。
- `academic`：加入我们自己的 skill/reuse 风格说明，适合 ablation。

Skill 注入现在按类型拆分：

- `functional`：作为 callable skill tool 暴露给模型，tool 返回 skill 内容或 checklist。
- `informational`：作为短 system notes 注入，适合参数约定、注意事项、关键事实。
- `workflow`：作为短 system notes 注入，适合多步流程模板。

可通过 `--skill-injection-mode none|prompt_only|tool_only|hybrid` 控制注入方式。`none` 用于 official baseline；`hybrid` 会把 functional skills 暴露为 tool，把 informational/workflow skills 放进 prompt。旧的 `use_skill` 统计工具默认关闭；如需显式统计模型是否声明使用 skill，可加：

```bash
--bfcl-explicit-skill-tool
```

BFCL 当前的默认 prompt-skill 策略已经进一步收紧：

- retrieval 改为按 turn 生效，不再整题级拼接 skill notes
- `prompt_only` 只注 informational skills
- 默认 handwritten prompt skills 只保留更保守的 Ticket/Travel 参数与文本规则
- 会稳定伤 baseline 的文件系统 / Vehicle 经验卡已从默认集移除

当前 official handler 复核仍需隔离环境。直接导入 `/tmp/bfcl_pkg/unpack` 的 `GLMAPIHandler` 时，`tree_sitter` 相关依赖在当前 Python 3.13 环境存在 API 版本不兼容，因此暂不把官方 handler result 作为主实验产物。

Spreadsheet 第一版是 openpyxl scaffold：模型生成 Python 代码编辑 workbook，runner 对 answer range 与 golden workbook 做 cell-level 比较。它适合先跑 smoke baseline 和 skill package 设计，不等价于 Trace2Skill 的完整 spreadsheet agent。

## 当前 smoke sanity

这些数值只用于确认 scaffold 有效，不作为论文正式结果：

| benchmark | model config | setting | result |
|---|---|---|---|
| `bfcl_v3` | `bigmodel` / GLM-4.7 | bundle data, official class tools, native prompt, first 10 shuffled cases, per-task timeout 180s | official valid 6/9 non-timeout cases, official_valid_rate 0.667, avg call F1 0.640, avg tokens about 60k; 1 timeout |
| `bfcl_v3` | `bigmodel` / GLM-4.7 / GLM-5 / GLM-4.5-air | simple `multi_turn_base_101`, official class tools, native prompt | all 3 models official valid 1/1, call F1 1.0 |
| `bfcl_v3` | `default` / Claude native Anthropic tools | 5-case subset from `multi_turn_base_101`, official class tools, native prompt | official_valid_rate 0.8, avg call F1 0.836, avg tokens about 56k |
| `bfcl_v3` | `bigmodel` / GLM-4.7 + early generic / domain-misaligned skill notes | first 5 shuffled cases, official class tools, native prompt | official_valid_rate 0.6, avg call F1 0.737; confirms that broad prompt notes can hurt baseline |
| `bfcl_v3` | `bigmodel` / GLM-4.7 + conservative turn-level prompt-only skills | 3-case same-split smoke (`multi_turn_base_134`, `178`, `120`) | official_valid_rate 0.667, avg call F1 0.809, avg tokens about 56k; slightly better than same-split `none` baseline |
| `bfcl_v3` | `bigmodel` / GLM-4.7 + conservative BFCL evolve smoke | 3 train + 3 test same-split smoke | official_valid_rate 0.667, avg call F1 0.761; auto-extracted skills did not yet beat handwritten prompt-only |
| `bfcl_v3` | `silicon_flow` / Qwen3-32B streaming tool path | simple `multi_turn_base_101`, path-filtered tools | official valid 0/1, call F1 0.8, about 7.4k tokens, about 60s |
| `bfcl_v3` | `bigmodel` / GLM-4.7 | path-filtered + academic prompt, 5 held-out cases | official valid 0/5, avg call F1 0.50, about 15k tokens/run |
| `bfcl_v3` | `bigmodel` / GLM-4.7 | path-filtered, train offset 2 / test offset 3, 3 train + 3 test evolve smoke | train official_valid_rate 0.0; extracted 8 skill cards from failed traces; test official_valid_rate 0.5 over non-timeout checks; no clear gain |
| `spreadsheet` | `default` / Claude | 1 held-out case | pass 1/1, cell accuracy 1.00, 182 checked cells, about 1.8k tokens |

BFCL 当前主要瓶颈不是 runner 崩溃，而是模型在 strict BFCL state check 下的参数精确性不足。典型错误包括：

- GLM-4.7、GLM-5、GLM-4.5-air 在简单 BFCL case 上都能正常 native tool call，因此“GLM 完全不能调工具”不成立。
- Claude 必须走 Anthropic native `tool_use/tool_result` 协议；OpenAI-compatible `role=tool` wrapper 会低估 Claude baseline。
- Qwen/SiliconFlow 需要 streaming-style tool-call path 才更接近 BFCL 官方 handler，但当前长 tool schema 延迟较高。
- 多轮工具链能跑通，但 `contact_customer_support.message` 和 `create_ticket.description` 会被模型扩写，和 golden text 不一致。
- `high-priority` 经常被映射成 `priority=5`，而 golden answer 使用 `priority=4`。
- 可选参数如 `insurance_id` 会被额外传入，导致 call-F1 和 official checker 不一致。
- first-10 GLM baseline 中 extra calls 是主导错误类型，另有少量 `destination` / `fuelAmount` 参数错误。
- official full tools + skill notes 明显增加延迟；当前 handwritten skill notes top-2 没有带来稳定 official_valid 增益。
- turn-level prompt injection is necessary for mixed-domain multi-turn cases; whole-task skill notes leak across turns and can directly hurt official validity.
- after removing harmful handwritten rules, conservative prompt-only BFCL notes can be non-harmful and mildly helpful on a same-split smoke subset.
- `path_filtered` 明显降低部分 case 的超时风险，但它不是正式 BFCL setting；同一区间 evolve 从失败 train traces 提取出的 skill cards 没有提升 test。
- 当前仍处于外围诊断阶段：环境可跑性、模型 tool-call schema、skill format/extraction 质量需要先排除，再进入算法创新。

因此当前可以说 BFCL baseline 已经跑通并达到“可诊断”的水平，但还不能声称达到 SkillX 论文中 GLM-4.6 No Memory Avg@4 约 76.67 的正式 setting，也不能声称 evolve 已在 BFCL 上见到有效增益。

下一步优先级：

1. 继续以 `official_valid` 为主指标；`call_f1` 只作为诊断，因为 extra calls 可能不影响 official state。
2. 优先压缩 skill notes 和 tool schema，而不是直接扩大 evolve；否则 token/延迟会掩盖算法信号。
3. 在隔离 Python 环境中复核官方 `bfcl-eval` handler，确认当前 runner 与官方 reported setting 的剩余差距。
4. 选择一组无 timeout、baseline 非饱和且可复现的 BFCL 子集，再做 baseline vs skill-note/evolve 对照。

新增 runner 工程能力：

- `--bfcl-tool-api-style auto|openai|openai_stream|anthropic_direct`
- `--top-k-skills`
- `--skill-injection-mode none|prompt_only|tool_only|hybrid`
- `--max-steps-per-turn`
- `--partial-output`
- `--max-task-seconds`
- `--train-offset`
- `--test-offset`

这些参数用于 provider 协议对齐、控制 skill 注入成本、长跑断点保存和 per-task timeout。

新增 BFCL 工程脚本：

- `academic/benchmarks/bfcl_experiment_suite.py`
  - 固定当前主线的 GLM / Claude official-aligned baseline 与 evolve 命令
- `academic/benchmarks/compare_bfcl_results.py`
  - 对比两份 BFCL 结果文件的 aggregate delta、case delta 和 skill usage delta

当前 evolve 结果会额外保存：

- `skills`
  - 最终 skill 列表，包含 source metadata
- `skill_bundles`
  - 每个 skill 绑定的长期 maintenance bundle；只存正例/反例/integration-derived cases 和 fixtures，不混入本轮结果
- `maintenance_test_results`
  - refine 前后的单 skill `with/without this skill` unit utility 结果，包含 `delta_accuracy / delta_tokens / delta_steps`
- `final_maintenance_test_results`
  - refine 完成后重新对 train-side bundle 跑的全量回归结果；用于保证“测试补充 -> 增量修改 -> 全量回归”闭环
- `post_refine_details`
  - 仅当 refine 确实改动 skill store 时生成；这是 train bundle 回归 replay，不是 held-out test
- `micro_refactor_candidates`
  - 当前 K-step 扫描下观察到的可复用共享片段候选；第一版只做证据输出，不自动改写 skill
- `skill_impact_summary`
  - 每个 evolved skill 在 test 期的检索次数、注入次数、命中的 task ids
  - 以及在 `official_valid=True/False` task 上分别出现在哪些 case

## 验证

```bash
python -m pytest -q academic/benchmarks/test_benchmark_adapters.py
```

当前 contract tests 覆盖：

- registry 中包含五个目标榜单；
- BFCL loader/tool docs/possible-answer scorer；
- Spreadsheet verified loader 和 golden-vs-golden verifier。
