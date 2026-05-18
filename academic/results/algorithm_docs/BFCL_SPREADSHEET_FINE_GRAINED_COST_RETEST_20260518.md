# BFCL / Spreadsheet Fine-Grained Cost Retest Report

Date: 2026-05-18

本报告汇总本轮 50-task heldout retest 的细粒度开销口径。实验目的不是重新训练 skill，而是在已有 skill store 上比较三种 test-time setting：

1. baseline: 不注入 skill。
2. full skill: 使用已有 skill，注入完整 skill prompt。
3. compact skill: 使用已有 skill，但通过 compact injector 压缩注入内容。

结果文件：

- `academic/results/cost_retest_bfcl_baseline_20260518.json`
- `academic/results/cost_retest_bfcl_fullskill_20260518.json`
- `academic/results/cost_retest_bfcl_compact_20260518.json`
- `academic/results/cost_retest_sheet_baseline_20260518.json`
- `academic/results/cost_retest_sheet_fullskill_20260518.json`
- `academic/results/cost_retest_sheet_compact_20260518.json`

## 统计口径

本轮新增并使用了两层统计：

- run-level metrics: 每个 task run 的 `metrics.total_tokens / input_tokens / cache_input_tokens / completion_tokens`，用于主表中的 `avg_total_tokens`。
- event-level cost breakdown: 每次 LLM call 记录一个 `cost_event`，再按 `role` 汇总。字段包括 `input_tokens`、`cache_input_tokens`、`output_tokens`、`prompt_chars`、`skill_prompt_chars`、`system_prompt_chars`、`tool_schema_chars`、`final_conversation_chars`。

本轮运行时没有设置价格环境变量：

- `SKILL_EVOLVE_INPUT_PRICE_PER_MTOK`
- `SKILL_EVOLVE_CACHE_INPUT_PRICE_PER_MTOK`
- `SKILL_EVOLVE_OUTPUT_PRICE_PER_MTOK`

因此 JSON 中 `estimated_cost` 为 0。本报告先以 token-denominated cost 为主。后续只要指定价格，可以直接按如下公式后处理：

```text
cost =
  input_tokens / 1e6 * input_price_per_mtok
+ cache_input_tokens / 1e6 * cache_input_price_per_mtok
+ output_tokens / 1e6 * output_price_per_mtok
```

## Main Results

### BFCL 50 Test

| Setting | Success | Official Valid | Avg Score | Avg Total Tok | Avg Input Tok | Avg Output Tok | Score / M Tok |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0.18 | 0.4898 | 0.7339 | 63,632.5 | 62,672.9 | 959.6 | 11.5327 |
| full skill | 0.22 | 0.6600 | 0.7968 | 74,711.6 | 73,700.7 | 1,010.9 | 10.6650 |
| compact skill | 0.22 | 0.6400 | 0.7937 | 71,810.7 | 70,812.6 | 998.1 | 11.0532 |

BFCL 结论：

- full skill 相比 baseline: official valid 从 0.4898 提升到 0.6600，avg score 从 0.7339 提升到 0.7968，但 avg token 增加 17.41%。
- compact skill 相比 full skill: avg score 基本持平，仅从 0.7968 到 0.7937；official valid 小降 0.02；avg token 降低 3.88%。
- compact skill 相比 full skill: skill prompt chars 降低 40.05%，说明压缩确实有效，但 BFCL 的总 token 大头仍然来自工具 schema 和多轮 executor context。

### Spreadsheet 50 Test

| Setting | Success | Avg Score | Avg Total Tok | Avg Input Tok | Avg Output Tok | Score / M Tok |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 0.26 | 0.3189 | 1,621.3 | 747.9 | 873.5 | 196.6818 |
| full skill | 0.36 | 0.4501 | 3,816.3 | 3,006.0 | 810.4 | 117.9428 |
| compact skill | 0.24 | 0.3186 | 1,873.2 | 1,038.7 | 834.5 | 170.0783 |

Spreadsheet 结论：

- full skill 明显提高效果：success 从 0.26 到 0.36，avg score 从 0.3189 到 0.4501。
- full skill 代价也很大：avg total token 从 1,621.3 到 3,816.3，约 2.35 倍。
- compact skill 相比 full skill token 降低 50.92%，skill prompt chars 降低 69.31%，但 avg score 回落到 0.3186，几乎等于 baseline。
- 这说明 Spreadsheet 的 full skill 中包含对模型有用的可执行 openpyxl / formula pattern 内容；当前 compact prompt 对 Spreadsheet 砍掉了太多 function/code 信息。

## Event-Level Cost Breakdown

### BFCL

| Setting | Event Calls | Input Tok | Cache Tok | Output Tok | Total Tok | Prompt Chars | Skill Prompt Chars | System Chars | Tool Schema Chars | Final Conversation Chars |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 450 | 3,139,483 | 0 | 48,049 | 3,187,532 | 1,104,435 | 17,550 | 0 | 9,871,752 | 1,263,878 |
| full skill | 475 | 3,685,034 | 0 | 50,547 | 3,735,581 | 1,787,613 | 706,903 | 939,928 | 10,491,502 | 1,954,888 |
| compact skill | 653 | 3,540,632 | 0 | 49,905 | 3,590,537 | 1,666,397 | 423,813 | 536,868 | 10,429,677 | 1,830,631 |

BFCL role split:

| Setting | Role | Calls | Input Tok | Output Tok | Skill Prompt Chars |
|---|---|---:|---:|---:|---:|
| baseline | executor | 450 | 3,139,483 | 48,049 | 17,550 |
| full skill | executor | 475 | 3,685,034 | 50,547 | 706,903 |
| compact skill | executor | 473 | 3,540,632 | 49,905 | 305,700 |
| compact skill | injector | 180 | 0 | 0 | 118,113 |

解释：

- compact BFCL 的 event call 数更高，是因为 deterministic injector 也记录了 zero-token cost events；它不调用 LLM，因此 `input/output token = 0`。
- BFCL 的 token 大头不是 output，而是 executor input。tool schema chars 约 9.9M-10.5M，是 BFCL prompt 结构的主要固定开销。
- compact injector 降低了 skill prompt 和 system chars，但只带来 3.88% 的 avg total token 降幅，因为 skill prompt 不是 BFCL 总输入的唯一大头。

### Spreadsheet

| Setting | Event Calls | Input Tok | Cache Tok | Output Tok | Total Tok | Prompt Chars | Skill Prompt Chars | System Chars | Tool Schema Chars | Final Conversation Chars |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 50 | 38,628 | 0 | 44,462 | 83,090 | 91,736 | 1,950 | 19,500 | 0 | 243,741 |
| full skill | 50 | 153,976 | 0 | 41,297 | 195,273 | 91,736 | 377,149 | 394,699 | 0 | 609,171 |
| compact skill | 100 | 53,463 | 0 | 42,527 | 95,990 | 91,736 | 115,748 | 75,424 | 0 | 292,353 |

Spreadsheet role split:

| Setting | Role | Calls | Input Tok | Output Tok | Skill Prompt Chars |
|---|---|---:|---:|---:|---:|
| baseline | executor | 50 | 38,628 | 44,462 | 1,950 |
| full skill | executor | 50 | 153,976 | 41,297 | 377,149 |
| compact skill | executor | 50 | 53,463 | 42,527 | 57,874 |
| compact skill | injector | 50 | 0 | 0 | 57,874 |

解释：

- Spreadsheet 没有 BFCL 那种大工具 schema，skill prompt 对 input token 的影响更直接。
- full skill 的 output token 反而略低于 baseline，说明主要增加的是 input-side skill context。
- compact skill 成功把 full skill 的 input token 从 153,976 降到 53,463，但效果也掉回 baseline，说明当前 compact 版本保留的信息不足。

## Relative Changes

### BFCL Relative to Baseline

| Setting | Official Valid Δ | Avg Score Δ | Avg Token Δ | Event Input Token Δ |
|---|---:|---:|---:|---:|
| full skill | +0.1702 | +0.0629 | +17.41% | +17.38% |
| compact skill | +0.1502 | +0.0598 | +12.85% | +12.78% |

### BFCL Compact Relative to Full

| Metric | Change |
|---|---:|
| official valid | -0.0200 |
| avg score | -0.0031 |
| avg total token | -3.88% |
| event input token | -3.92% |
| skill prompt chars | -40.05% |
| system chars | -42.88% |

### Spreadsheet Relative to Baseline

| Setting | Success Δ | Avg Score Δ | Avg Token Δ | Event Input Token Δ |
|---|---:|---:|---:|---:|
| full skill | +0.10 | +0.1312 | +135.39% | +298.62% |
| compact skill | -0.02 | -0.0003 | +15.53% | +38.40% |

### Spreadsheet Compact Relative to Full

| Metric | Change |
|---|---:|
| success | -0.12 |
| avg score | -0.1315 |
| avg total token | -50.92% |
| event input token | -65.28% |
| skill prompt chars | -69.31% |
| system chars | -80.89% |

## Interpretation

### BFCL

BFCL 上 skill 的收益是稳定的：full 和 compact 都比 baseline 明显更好。compact 保住了大部分效果，但 token 降幅有限。原因是 BFCL 的输入主要由 multi-turn conversation 和 tool schema 驱动，skill prompt 只是其中一部分。

下一步 BFCL 的压缩重点不应只放在 skill prompt，还需要：

- 缩短 repeated tool schema / adapter prompt。
- 对 step-level retrieval update 做更严格的去重。
- 在 native tool schema 层做 class/path-filtered ablation，确认是否能减少 `tool_schema_chars` 而不损害 official validity。

### Spreadsheet

Spreadsheet 上 full skill 有明显效果，但成本高。compact 能大幅降低成本，但当前压缩策略不适合 Spreadsheet，因为 Spreadsheet skill 很多是 function/code/formula pattern。只保留 summary/trigger 会丢掉真正有用的 openpyxl 代码 idiom。

下一步 Spreadsheet compact injector 应改成 typed compact：

- workflow / knowledge skill: 可以继续压缩成文本 contract。
- function skill: 必须保留最小可执行 openpyxl snippet 或 formula pattern。
- 每个 function skill 保留 `inputs -> code idiom -> output range contract -> non-applicability`。
- 对 Spreadsheet 的 `compact_chars_per_skill` 可以高于 BFCL，例如 1200-1600 chars，而不是统一 900 chars。

## Verifier Fix During Retest

重测中发现 Spreadsheet verifier 对官方 answer range 的解析过窄，曾将如下合法官方格式误判为异常：

- `"'Main!'A2:M70"`
- `"'Sheet1!'A1:A50,'Sheet2!'A1:E20,'Sheet3!'A1:A50"`
- `"G4:G6, G11:G13, G20:G22"`

已修复：

- 支持引号错位的 sheet/range。
- 支持逗号分隔的多个 range。
- 支持无 sheet 的多个 range。

测试：

- `test_spreadsheet_answer_range_parser_handles_official_multi_ranges`
- `test_spreadsheet_task_passes_model_override_to_llm`

## Reporting Recommendation

论文表格建议至少同时报告四类指标：

1. Accuracy side:
   - BFCL: `official_valid_rate`, `avg_score`, `success_rate`
   - Spreadsheet: `success_rate`, `avg_score`
2. Token side:
   - `avg_total_tokens`
   - `avg_input_tokens`
   - `avg_output_tokens`
   - `cache_input_tokens`
3. Prompt composition:
   - `skill_prompt_chars`
   - `system_prompt_chars`
   - `tool_schema_chars`
   - `final_conversation_chars`
4. Efficiency:
   - `score_points_per_million_tokens`
   - `official_valid_per_million_tokens` for BFCL
   - optional dollar cost after setting price env vars

本轮最关键结论：

- BFCL: compact skill 是可接受的折中，几乎保住 full skill 效果，并降低 skill prompt 约 40%。
- Spreadsheet: full skill 证明 skill 有效；compact 证明能降成本，但当前压缩策略损害了 function/code skill 的有效性，需要 typed compact prompt。
