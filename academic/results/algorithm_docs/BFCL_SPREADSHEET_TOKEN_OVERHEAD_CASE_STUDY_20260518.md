# BFCL / Spreadsheet Token Overhead Case Study

本文档分析当前 BFCL 与 SpreadsheetBench 两个榜单中 evolve 相比 baseline 多花 token 的原因。结论先行：

- **BFCL 的 token 增量几乎全部来自 prompt tokens，而不是 completion tokens。**50 个 held-out 任务平均多花 16,489.5 tokens，其中 99.6% 来自 prompt token 增量。主要原因是 prompt-only skill injection 在多轮任务中被反复带入上下文，并且部分 skill 文本较长；少数任务还因为 skill 纠正工作流后跑了更多步骤。
- **Spreadsheet 的 token 增量主要来自每个 held-out 任务固定检索 top-5 skills 后形成的大 skill package。**evolve test 的每个任务都检索到 5 个 skills；实际注入 prompt 的 skill block 平均约 7.4k 字符，完整 skill artifact 文本平均约 16.8k 字符，使 prompt tokens 从 baseline 的表格口径 1,552.1 total tokens / task 增至 3,748.2 total tokens / task。completion tokens 反而没有明显增加。
- **两者共同问题不是模型“想得更久”，而是 skill context 变重、检索过宽、skill 没有进一步压缩。**这说明下一步优化重点应是 skill selection、skill compression、retrieval guard、分层加载和 TRL 反馈，而不是简单减少生成长度。

## 1. 数据口径

使用的结果文件：

- BFCL baseline：`academic/results/claude_proxy_related50_50_guardfix_20260517_232531_baseline.json`
- BFCL evolve heldout rerun：`academic/results/bfcl_guardfix_trainedstore_test50_rerun2_20260518_012134.json`
- Spreadsheet baseline：`academic/results/spreadsheet_baseline_test50_20260517_233447.json`
- Spreadsheet true evolve：`academic/results/spreadsheet_evolve_50_50_true_20260518_022120.json`

注意：Spreadsheet baseline 的 50 test 与 evolve 的 50 held-out test 没有 task-id overlap。因此 Spreadsheet 不能做逐 task delta，只能做总体分布和 evolve case study。BFCL baseline/evolve 是同一批 50 held-out task，可以做逐 task 对齐。

## 2. 总体 Token 对比

### 2.0 建议改用的新统计口径

后续表格建议从单一 `total tokens` 改成四列：

1. **Cost**：按模型价格把 input/output tokens 分开计价。本文档下面使用 Claude Sonnet 4.5 标准 API 价格作示例：input `$3 / 1M tokens`，output `$15 / 1M tokens`。如果本地代理有折扣或不同结算，应替换价格参数。
2. **Final conversation length**：最终发送给模型的 conversation / prompt artifact 的长度。BFCL 用最终 `messages` 的字符数近似；Spreadsheet 用 task prompt + generated code 字符数近似。这个指标不是 billing token，而是上下文膨胀的可解释 proxy。
3. **Input tokens**：即 prompt tokens。
4. **Output tokens**：即 completion tokens。

采用这个口径后，可以把“模型输出变长”和“输入上下文变长”分开，也能把 dollar cost 和 token overhead 对齐。

示例 cost 使用公式：

```text
cost = input_tokens / 1e6 * input_price + output_tokens / 1e6 * output_price
```

### 2.0.1 Cost / Conversation / Input-Output Reaccounting

| Benchmark | Setting | Est. cost | Final conversation length | Input tokens / task | Output tokens / task | Total tokens / task |
|---|---:|---:|---:|---:|---:|---:|
| BFCL | baseline | `$11.16 / 50 tasks` | 4,500.8 chars | 69,299.5 | 1,024.3 | 70,323.8 |
| BFCL | evolve | `$13.68 / 50 tasks` | 9,631.7 chars | 85,724.4 | 1,088.9 | 86,813.3 |
| Spreadsheet | baseline | `$0.81 / 50 tasks` | 4,011.3 chars | 772.5 | 928.0 | 1,700.5 trace-token avg |
| Spreadsheet | evolve | `$1.05 / 50 tasks` | 3,434.7 chars | 2,929.2 | 819.1 | 3,748.2 |

说明：

- BFCL 的 final conversation length 变长约 5,130.9 chars / task，其中 evolve 最终 conversation 中平均约 4,902.3 chars / task 是 skill context message。由于 BFCL 多轮调用会反复发送历史上下文，最终 conversation length 小于累计 billed input token 的增长。
- Spreadsheet 的 final conversation length 反而略低，说明它的 output code 不一定更长；token 成本主要来自系统 prompt 中额外注入的 skill package，而该 skill package 不在 trace 的 user prompt 字段里。
- Spreadsheet baseline 的论文表格口径是 1,552.1 total tokens / task；这里为了拆 input/output，使用 trace 中可恢复 token 的 1,700.5 avg。少数 baseline failure run 的 metrics token 缺失，但 trace 里保留了 LLM token。

## 2.1 如果不考虑额外 Skill Context，差多少？

这个问题需要估算，因为当前结果只保存了每次调用的总 input/output tokens，没有保存 provider 级别的 per-message token attribution。我们可以用两种方式给出范围：

- **直接上界法**：把所有 prompt token delta 都视为 skill context 或 skill-induced repeated context。这个口径下，扣除 skill context 后 BFCL 几乎只剩 output 差异，约 `+64.6 tokens / task`。
- **字符折算法**：用 skill context 字符数按 `3.5-6 chars/token` 折算成 input tokens，再从 evolve token 中扣除。这个口径更保守，因为它只扣除显式 skill 文本，不扣除 skill 导致的额外 step 对整段上下文的重复计费。

### BFCL 扣除 Skill Context 后的估算

BFCL evolve 中，显式 skill context 在最终 conversation 中平均约 4,902 chars / task；考虑多轮重复发送后，按消息位置加权的 billed skill-context chars 约 30,811 chars / task。

| chars/token assumption | Est. skill input tokens / task | Adjusted input delta / task | Adjusted total delta / task |
|---:|---:|---:|---:|
| 3.5 | 8,803.2 | +7,621.7 | +7,686.3 |
| 4.0 | 7,702.8 | +8,722.1 | +8,786.7 |
| 5.0 | 6,162.3 | +10,262.7 | +10,327.3 |
| 6.0 | 5,135.2 | +11,289.7 | +11,354.3 |
| Direct upper-bound attribution | 16,424.9 | +0.0 | +64.6 |

解释：

- 如果只扣除“显式 skill 文本”，BFCL 仍然平均多 `~7.7k-11.4k total tokens / task`。这部分主要来自 skill 改变执行路径后多跑 step，以及更多历史上下文被重复发送。
- 如果把“skill 诱发的额外 step 造成的上下文重复”也算作 skill context overhead，那么 BFCL 的剩余差异接近 output 增量，即约 `+64.6 tokens / task`。
- 因此 BFCL 的真实答案取决于定义：只扣除显式 skill block，仍有明显差异；扣除 skill block 及其导致的重复上下文，几乎没有差异。

### Spreadsheet 扣除 Skill Context 后的估算

Spreadsheet 是单次代码生成任务，没有 BFCL 那样的多轮重复计费。evolve 每题检索 5 个 skills，实际注入的 skill prompt block 平均约 7,400.8 chars / task。

| chars/token assumption | Est. skill input tokens / task | Adjusted evolve input / task | Adjusted evolve total / task | Delta vs baseline trace-token avg |
|---:|---:|---:|---:|---:|
| 3.5 | 2,114.5 | 814.6 | 1,633.7 | -66.8 |
| 4.0 | 1,850.2 | 1,079.0 | 1,898.0 | +197.5 |
| 5.0 | 1,480.2 | 1,449.0 | 2,268.1 | +567.5 |
| 6.0 | 1,233.5 | 1,695.7 | 2,514.8 | +814.2 |

解释：

- Spreadsheet 的 token 增量几乎可以由额外 skill package 解释。
- 在常见 `3.5-4 chars/token` 估算下，扣除 skill context 后 evolve total 大约是 `1,633.7-1,898.0 tokens / task`，接近 baseline 的 `1,700.5 trace-token avg`。
- 所以 Spreadsheet 如果不考虑 skill context，基本没有明显 token 劣势；它的问题主要是 skill package 太重，而不是模型生成更长。

### 2.2 BFCL

| Metric | Baseline | Evolve | Delta |
|---|---:|---:|---:|
| Avg total tokens / task | 70,323.8 | 86,813.3 | +16,489.5 |
| Avg prompt tokens / task | 69,299.5 | 85,724.4 | +16,424.9 |
| Avg completion tokens / task | 1,024.3 | 1,088.9 | +64.6 |
| Avg model steps / task | 9.62 | 10.16 | +0.54 |

拆分后可以看到，BFCL 的 token 增量几乎完全来自 prompt：

- total token 增量：824,477；
- prompt token 增量：821,246；
- completion token 增量：3,231；
- prompt 增量占 total 增量约 99.6%。

这说明 BFCL 的开销主要不是模型输出变长，而是每次模型调用的输入上下文变长。

### 2.3 SpreadsheetBench

论文表格口径：

| Metric | Baseline | Evolve |
|---|---:|---:|
| Avg total tokens / task | 1,552.1 | 3,748.2 |
| Exact success | 0.22 | 0.24 |
| Avg score | 0.2564 | 0.3208 |

按 run trace 进一步拆分：

| Metric | Baseline approx. | Evolve |
|---|---:|---:|
| Avg prompt tokens / task | 772.5 | 2,929.2 |
| Avg completion tokens / task | 928.0 | 819.1 |
| Retrieved runs | 0 / 50 | 50 / 50 |
| Retrieved skill count | 0 | 5 for every task |
| Avg injected skill prompt block | 0 | 7,400.8 chars |
| Avg retrieved full skill artifact text | 0 | 16,806.7 chars |

Spreadsheet 的增量同样主要来自 prompt。completion tokens 平均没有增加，甚至略低。真正变化是每个 task 都注入了较重的 top-5 skill package。

## 3. BFCL：为什么多花这么多 Token

### 3.1 主因一：Prompt-only skill context 被多轮重复带入

BFCL 是多轮 function-calling benchmark。一次任务通常包含多个 model steps。prompt-only injection 的代价不是一次性的：skill context 一旦进入 message history，后续每一步都会继续被计入 prompt tokens。

因此，一个 10k 到 20k 字符的 skill，在单步中只是几千 token，但在 8 到 12 个 model steps 中会被重复计费。BFCL evolve 中平均每步 prompt token 也上升：

- baseline 平均 prompt tokens / step：约 7,279.9；
- evolve 平均 prompt tokens / step：约 8,408.8。

也就是说，即使 step 数不变，每步上下文也更贵；如果 step 数增加，成本会被进一步放大。

### 3.2 主因二：检索到 2 个 skill 的任务成本明显更高

按 BFCL evolve 中检索 skill 数量分组：

| Retrieved skill count | Task count | Avg token delta | Avg prompt delta | Avg step delta |
|---:|---:|---:|---:|---:|
| 0 | 11 | -328.0 | -336.3 | 0.00 |
| 1 | 25 | +15,530.7 | +15,487.1 | +0.16 |
| 2 | 14 | +31,415.5 | +31,269.1 | +1.64 |

这个分组很清楚：没有检索 skill 的任务几乎没有额外开销；检索 1 个 skill 后平均多 15.5k；检索 2 个 skill 后平均多 31.4k。并且检索 2 个 skill 的任务还平均多跑 1.64 步。

因此 BFCL 的主要开销公式近似是：

```text
token overhead ≈ skill text length × repeated model steps + extra steps caused by changed workflow
```

### 3.3 主因三：部分高频 skill 本身文本较长

BFCL held-out 中最常注入的 skills：

| Count | Skill | Approx chars | 说明 |
|---:|---|---:|---|
| 14 | `vehicle_engine_start_brake_pedal_prerequisite` | 20,855 | 车辆启动前 brake pedal prerequisite |
| 14 | `vehicle_engine_start_brake_pedal_requirement` | 14,461 | 与上一个 skill 语义高度相关 |
| 13 | `multi_turn_order_verification_workflow` | 19,900 | 订单多轮验证 workflow |
| 12 | `contact_customer_support_message_brevity` | 15,391 | support message 简洁性规则 |

这里有两个问题：

1. 单个 skill 文本偏长；
2. 存在语义相近 skill 同时被检索，例如两个 vehicle brake prerequisite skills。

这会造成双重开销：上下文更长，且重复规则降低信息密度。

## 4. BFCL 高开销 Case

### Case 1: `multi_turn_base_194`

| Metric | Baseline | Evolve | Delta |
|---|---:|---:|---:|
| Total tokens | baseline | evolve | +53,088 |
| Prompt delta | - | - | +52,518 |
| Completion delta | - | - | +570 |
| Steps | 8 | 12 | +4 |
| Score | 0.4444 | 0.7692 | +0.3248 |
| Official valid | False | False | unchanged |
| Injected skill | `contact_customer_support_message_brevity` | | |

解释：这个 case 的开销来自两部分叠加。第一，support-message skill 被注入后在多轮上下文中反复计费；第二，evolve 多跑了 4 个 model steps，使重复计费倍增。虽然 score 明显提升，但 official valid 仍未通过，说明 skill 让局部调用更接近正确，但没有完全解决最终合约。

结论：这是典型“质量提升但 prompt-only 成本过高”的 case。

### Case 2: `multi_turn_base_65`

| Metric | Baseline | Evolve | Delta |
|---|---:|---:|---:|
| Total delta | - | - | +50,399 |
| Prompt delta | - | - | +50,201 |
| Completion delta | - | - | +198 |
| Steps | 6 | 10 | +4 |
| Score | 0.8421 | 0.9000 | +0.0579 |
| Official valid | False | True | improved |
| Injected skills | vehicle brake prerequisite pair | | |

检索到两个语义接近的 vehicle engine start skills：

- `vehicle_engine_start_brake_pedal_prerequisite`
- `vehicle_engine_start_brake_pedal_requirement`

这两个 skill 加起来文本很长，而且语义重叠。evolve 最终 official valid 变 True，说明规则确实有用；但两个重叠 skill 同时注入、多跑 4 步，使 token overhead 达到 50k。

结论：这是“有用但冗余”的 case，适合做 macro dedupe 或 merge。

### Case 3: `multi_turn_base_88`

| Metric | Baseline | Evolve | Delta |
|---|---:|---:|---:|
| Total delta | - | - | +45,691 |
| Prompt delta | - | - | +45,403 |
| Completion delta | - | - | +288 |
| Steps | 7 | 10 | +3 |
| Score | 0.6667 | 0.8750 | +0.2083 |
| Official valid | False | True | improved |
| Injected skills | same vehicle brake prerequisite pair | | |

解释同 Case 2。这个 case 说明 vehicle skill 对 correctness 有帮助，但两个相近 skill 同时检索导致成本明显偏高。

### Case 4: `multi_turn_base_54`

| Metric | Baseline | Evolve | Delta |
|---|---:|---:|---:|
| Total delta | - | - | +43,293 |
| Prompt delta | - | - | +42,995 |
| Completion delta | - | - | +298 |
| Steps | 7 | 10 | +3 |
| Score | 0.3077 | 0.9333 | +0.6256 |
| Official valid | False | True | improved |
| Injected skills | same vehicle brake prerequisite pair | | |

这是一个高收益高成本 case。skill 把 score 从 0.3077 拉到 0.9333，并通过 official valid；但成本依然由重复注入和多步上下文放大。

### Case 5: `multi_turn_base_153`

| Metric | Baseline | Evolve | Delta |
|---|---:|---:|---:|
| Total delta | - | - | +41,495 |
| Prompt delta | - | - | +41,028 |
| Completion delta | - | - | +467 |
| Steps | 8 | 11 | +3 |
| Score | 0.4444 | 0.9091 | +0.4647 |
| Official valid | False | True | improved |
| Injected skill | `contact_customer_support_message_brevity` | | |

该 case 中 skill 有明显 correctness gain，但 prompt-only 注入在 11 步中重复计费。

## 5. Spreadsheet：为什么多花这么多 Token

### 5.1 主因一：每个 held-out task 都注入 top-5 skill package

Spreadsheet evolve 的 50 个 held-out runs 全部检索到 5 个 skills。实际注入 prompt 的 skill block 平均约 7,400.8 字符；如果按完整 skill artifact（description/body/interface/metadata 等）粗略相加，平均约 16,806.7 字符。

这和 baseline 的差异非常直接：

- baseline 没有 skill context；
- evolve 每题都有 5 个 skill blocks；
- Spreadsheet 是单次代码生成任务，没有多轮重复计费，但一次 prompt 里 skill package 已经很重。

因此 Spreadsheet 的 token overhead 更像：

```text
token overhead ≈ top-5 skill package size
```

### 5.2 主因二：skill 内容包含较长 repair evidence 和代码片段

Spreadsheet skills 往往来自公式 mismatch、openpyxl idiom 或 sheet-level repair。为了可复用和可审计，skill body 中包含：

- applicability；
- non-applicability；
- expected formula/value pattern；
- failure evidence；
- openpyxl idiom；
- workbook/sheet/range 注意事项。

这让单个完整 skill artifact 通常达到 3k 到 4k 字符。当前实际注入 prompt 时会做一定字段压缩，但 top-5 合并后的 prompt block 仍然平均达到约 7.4k 字符。

### 5.3 主因三：检索 guard 不够强，所有任务固定塞满 top-5

Spreadsheet evolve 中每个 test run 都检索 5 个 skills，没有根据置信度、domain match、instruction type 或 skill compactness 动态减少注入数量。

最常被检索的 Spreadsheet skills：

| Count | Skill | Kind | Approx chars | 说明 |
|---:|---|---|---:|---|
| 36 | `spreadsheet_cell_level_manipulation_vlookup_lookup_column_mismatch` | interface contract | 3,578 | VLOOKUP lookup column 相关 |
| 32 | `spreadsheet_repair_cell_level_manipulation_if_countif_supplier_1_calc` | workflow guardrail | 3,340 | formula/value mismatch repair |
| 21 | `spreadsheet_sheet_level_manipulation_level_manipulation_excel_file` | executable tool | 2,985 | sheet-level openpyxl pattern |
| 20 | `spreadsheet_sheet_level_manipulation_level_manipulation_need_visual` | executable tool | 2,953 | sheet-level pattern |
| 20 | `spreadsheet_repair_sheet_level_manipulation_level_manipulation_have_data` | workflow guardrail | 3,169 | sheet-level repair |

这些 skill 的主题并不总是和每个 held-out task 精确匹配。固定 top-5 策略会让相关性一般的 skill 也进入 prompt。

## 6. Spreadsheet 高开销 Case

由于 Spreadsheet baseline/evolve test split 没有 task-id overlap，下面不是逐任务 delta，而是 evolve held-out 中 token 最高的 case。

### Case 1: `80-42`

| Metric | Value |
|---|---:|
| Total tokens | 5,772 |
| Prompt tokens | 4,331 |
| Completion tokens | 1,441 |
| Score | 0.5835 |
| Retrieved skill chars | 17,138 |
| Retrieved skills | 5 |

Top retrieved skills 包括：

- `spreadsheet_cell_level_manipulation_vlookup_lookup_column_mismatch`
- `spreadsheet_sheet_level_manipulation_level_manipulation_need_vba`
- `spreadsheet_sheet_level_manipulation_level_manipulation_excel_file`

解释：该 case 的 prompt token 很高，主要因为 task 本身 prompt 较长，加上 5 个 skill block。它有一定 score gain 空间，但 17k 字符 skill context 对单次代码生成任务来说偏重。

### Case 2: `47766`

| Metric | Value |
|---|---:|
| Total tokens | 5,116 |
| Prompt tokens | 3,017 |
| Completion tokens | 2,099 |
| Score | 0.0 |
| Retrieved skill chars | 16,822 |
| Retrieved skills | 5 |

Top retrieved skills 包括：

- `spreadsheet_repair_cell_level_manipulation_xlfn_ifna_index_match`
- `spreadsheet_sheet_level_manipulation_level_manipulation_excel_file`
- `spreadsheet_cell_level_manipulation_cumulative_countif_with_conditional_subtraction`

解释：该 case 不仅 prompt 重，completion 也长，但最终 score 为 0。这类 case 显示 skill context 没有有效约束模型，反而可能让模型生成更复杂但不正确的代码。应该作为 harmful / retrieval-noise feedback 进入 TRL 和 retrieval guard。

### Case 3: `52216`

| Metric | Value |
|---|---:|
| Total tokens | 5,058 |
| Prompt tokens | 3,932 |
| Completion tokens | 1,126 |
| Score | 0.0 |
| Retrieved skill chars | 17,975 |
| Retrieved skills | 5 |

Top retrieved skills 包括：

- `spreadsheet_repair_cell_level_manipulation_if_countif_supplier_1_calc`
- `spreadsheet_cell_level_manipulation_arrayformula_vlookup_multi_criteria`
- `spreadsheet_repair_sheet_level_manipulation_level_manipulation_have_data`

解释：skill package 非常重，但结果为 0。该 case 说明当前检索更像“相关技能尽量都放进去”，没有判断 task 是否真正需要这些 repair patterns。

### Case 4: `7902`

| Metric | Value |
|---|---:|
| Total tokens | 4,842 |
| Prompt tokens | 3,611 |
| Completion tokens | 1,231 |
| Score | 0.0 |
| Retrieved skill chars | 16,788 |
| Retrieved skills | 5 |

Top retrieved skills 包括：

- `spreadsheet_cell_level_manipulation_vlookup_lookup_column_mismatch`
- `spreadsheet_cell_level_manipulation_vlookup_absolute_vs_entire_column_reference`
- `spreadsheet_repair_cell_level_manipulation_if_countif_supplier_1_calc`

解释：多个 formula / VLOOKUP repair skills 被同时注入，但最终没有提升 correctness。可能问题是 skill 粒度过宽，模型无法从多个相近规则中选择当前需要的 exact pattern。

### Case 5: `433-47`

| Metric | Value |
|---|---:|
| Total tokens | 4,783 |
| Prompt tokens | 2,604 |
| Completion tokens | 2,179 |
| Score | 0.8095 |
| Retrieved skill chars | 17,044 |
| Retrieved skills | 5 |

Top retrieved skills 包括：

- `spreadsheet_cell_level_manipulation_vlookup_lookup_column_mismatch`
- `spreadsheet_repair_sheet_level_manipulation_item`
- `spreadsheet_sheet_level_manipulation_level_manipulation_how_can`

解释：该 case 的 score 较高，但 completion 很长。skill context 可能帮助模型写出较复杂代码，但也没有把输出压缩成更短、更直接的 solution。

## 7. 根因归纳

### 7.1 BFCL 根因

BFCL token overhead 的根因排序：

1. **Prompt-only skill injection 在多轮对话中重复计费。**
2. **skill 文本偏长，且 top skills 中存在语义重复。**
3. **skill 改善 workflow 后有时会让模型执行更多必要步骤。**
4. **当前没有分层加载：检索到 skill 后基本注入完整 skill block，而不是先注入 compact hint。**
5. **缺少强 enough 的 dedupe / relation-aware suppression，导致相近 vehicle skills 同时进入 prompt。**

### 7.2 Spreadsheet 根因

Spreadsheet token overhead 的根因排序：

1. **每个 test task 固定检索并注入 5 个 skills。**
2. **单个 Spreadsheet skill body 很长，通常包含 repair evidence、formula pattern、code idiom 和 non-applicability。**
3. **检索 guard 不够强，相关性一般的 skill 也进入 prompt。**
4. **skill 没有压缩成“短卡片 + 按需展开”的两级结构。**
5. **TRL 反馈还没有充分压制低复用、过宽、常 harmful 的 skill 模式。**

## 8. 优化建议

### 8.1 立即可做的工程优化

1. **动态 top-k**：默认 top-1 或 top-2；只有 retrieval score 明显高、domain/instruction type 强匹配时才扩展到 top-5。
2. **skill compact view**：执行时只注入 summary、applicability、exact contract；把 evidence、history、bundle notes 留给 maintenance，不进入 executor prompt。
3. **相似 skill 抑制**：同一 candidate group 或同一 relation cluster 中只注入一个代表 skill。
4. **token budget gate**：每个 task 的 skill context 设硬上限；超过后按 retrieval score、credit history 和 compactness 排序截断。
5. **step-level lazy injection**：BFCL 里不要一开始注入完整 skill；等工具错误、domain match 或 specific intent 出现后再注入局部技能。

### 8.2 算法层优化

1. **把 token cost 纳入 credit assignment**：helpful 但成本过高的 skill 应进入 compression 或 merge 队列。
2. **TRL 反馈到 extractor**：对反复 harmful / retrieval-noise 的 skill，总结成 extractor rule，例如“必须写清 non-applicability”，“不要把单条 formula mismatch 泛化成全局 spreadsheet rule”。
3. **bundle 中加入 with-skill token budget assertion**：不仅测 correctness，也测是否引入不合理上下文成本。
4. **macro refactor 合并高重叠 skill**：例如 BFCL vehicle brake prerequisite pair 应合并为一个短 contract card。
5. **按 skill 类型采用不同注入策略**：interface contract 注入短规则；workflow skill 注入 decision boundary；executable skill 只在需要代码 idiom 时展开。

## 9. 结论

当前 token 增长不是随机现象，而是 skill repository 已经开始工作后的典型副作用：系统把更多外部知识放进上下文，但还没有足够强的压缩、选择和分层加载机制。

BFCL 的高 token 成本主要是“长 skill 在多轮中重复计费”；Spreadsheet 的高 token 成本主要是“每题 top-5 长 skill package”。这两个问题都支持论文的核心观点：skill evolution 的关键不是生成更多 skill，而是维护一个紧凑、低噪声、可复用、可测试的 skill 仓库。
