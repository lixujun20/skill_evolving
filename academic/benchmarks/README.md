# Benchmark 子系统使用说明

本子系统把新的 agentic benchmark 从原来的数学 `Problem(question, answer)` 链路中拆出来，避免破坏已有 AIME/MATH 实验。当前 registry 覆盖五个目标榜单：

| key | 状态 | 主要用途 | skill format |
|---|---|---|---|
| `bfcl_v3` | 已实现 baseline adapter | 多轮函数调用、API/tool 规则复用 | tool rule card + workflow card |
| `spreadsheet` | 已实现 smoke adapter | xlsx 操作、文档式 skill package | `SKILL.md` + scripts/references |
| `appworld` | registry only | app/API 长程 workflow | planning/function/atomic skill |
| `officeqa` | registry only | 企业文档 grounded QA | document/table workflow |
| `tir_bench` | registry only | tool-integrated reasoning | task skill + action experience |

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

BFCL prompt style：

- `native`：默认，不额外加系统 prompt，最接近 OpenAI-compatible native function calling 官方 handler。
- `official`：加入 BFCL prompting-style 系统说明，但仍使用 native tool calls。
- `academic`：加入我们自己的 skill/reuse 风格说明，适合 ablation。

Handwritten/evolved skills 默认作为 system notes 注入，不再默认增加额外 `use_skill` tool；如需显式统计模型是否声明使用 skill，可加：

```bash
--bfcl-explicit-skill-tool
```

当前 official handler 复核仍需隔离环境。直接导入 `/tmp/bfcl_pkg/unpack` 的 `GLMAPIHandler` 时，`tree_sitter` 相关依赖在当前 Python 3.13 环境存在 API 版本不兼容，因此暂不把官方 handler result 作为主实验产物。

Spreadsheet 第一版是 openpyxl scaffold：模型生成 Python 代码编辑 workbook，runner 对 answer range 与 golden workbook 做 cell-level 比较。它适合先跑 smoke baseline 和 skill package 设计，不等价于 Trace2Skill 的完整 spreadsheet agent。

## 当前 smoke sanity

这些数值只用于确认 scaffold 有效，不作为论文正式结果：

| benchmark | model config | setting | result |
|---|---|---|---|
| `bfcl_v3` | `bigmodel` / GLM-4.7 | bundle data, official class tools, native prompt, 1 held-out case | official valid 0/1, call F1 0.36, about 68k tokens |
| `bfcl_v3` | `default` / Claude | bundle data, official class tools, native prompt, 1 held-out case | official valid 0/1, call F1 0.55, about 79k tokens |
| `bfcl_v3` | `bigmodel` / GLM-4.7 | path-filtered + academic prompt, 5 held-out cases | official valid 0/5, avg call F1 0.50, about 15k tokens/run |
| `bfcl_v3` | `bigmodel` / GLM-4.7 | handwritten skills + native prompt, 1 held-out case | official valid 0/1, call F1 0.50; skills retrieved and injected but no strict success |
| `bfcl_v3` | `bigmodel` / GLM-4.7 | evolve smoke, 1 train + 1 test, handwritten seed skills | train call F1 0.59, test call F1 0.55, official valid 0/1 test |
| `spreadsheet` | `default` / Claude | 1 held-out case | pass 1/1, cell accuracy 1.00, 182 checked cells, about 1.8k tokens |

BFCL 当前主要瓶颈不是 runner 崩溃，而是模型在 strict BFCL state check 下的参数精确性不足。典型错误包括：

- 多轮工具链能跑通，但 `contact_customer_support.message` 和 `create_ticket.description` 会被模型扩写，和 golden text 不一致。
- `high-priority` 经常被映射成 `priority=5`，而 golden answer 使用 `priority=4`。
- 可选参数如 `insurance_id` 会被额外传入，导致 call-F1 和 official checker 不一致。
- SiliconFlow 上的 Qwen/Kimi/GLM 长 tool schema probe 在当前接口下耗时过长，不适合作为快速调试路径。

因此当前还不能声称 BFCL baseline 达到 SkillX 论文中 GLM-4.6 No Memory Avg@4 约 76.67 的水平，也不能声称 evolve 已在 BFCL 上见到有效增益。

下一步优先级：

1. 在隔离 Python 环境中安装 `bfcl-eval` 及其 pinned deps，用官方 `GLMAPIHandler` 直接跑同一 case，验证当前差距是否来自 wrapper。
2. 找到一个在 BFCL native function calling 上稳定的模型后，再跑 50/150 split 的 baseline 与 evolve。
3. 如果 strict text parameter 仍主导失败，先构造 text-literal skill/feedback 的小型子集，不直接扩大全量实验。

## 验证

```bash
python -m pytest -q academic/benchmarks/test_benchmark_adapters.py
```

当前 contract tests 覆盖：

- registry 中包含五个目标榜单；
- BFCL loader/tool docs/possible-answer scorer；
- Spreadsheet verified loader 和 golden-vs-golden verifier。
