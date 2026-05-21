# Spreadsheet Notebook Case Study: 为什么 0519 evolve 没有 work

日期：2026-05-19

本文分析 Spreadsheet notebook baseline 与 0519 notebook evolve 主实验的输出和 trace，目标是回答：

1. Spreadsheet notebook evolve 为什么没有相对 baseline 体现收益。
2. 我们的方法中哪些环节导致了退化或无效开销。
3. 下一步应如何修改算法和实验协议。

## 数据来源

主要文件：

- Baseline 结果：`academic/results/spreadsheet_notebook_baseline_test50_0519.json`
- Baseline log：`academic/results/spreadsheet_notebook_baseline_test50_0519.log`
- Evolve 结果：`academic/results/spreadsheet_0519-speedup.json`
- Evolve log：`academic/results/spreadsheet_0519-speedup_rerun.log`
- Evolve 早期失败 log：`academic/results/spreadsheet_0519-speedup.log`

说明：raw log 主要包含进度和 summary，不包含完整 notebook code / stdout / verifier mismatch。真正可分析的输出在 JSON 的 `details` / `test_details` / `train_details` 中，包括 `trace.notebook_turns`、`metrics.mismatched_cells`、`retrieved_skills`、`prompt_injected_skills`、`called_skill_functions`。

## 重要限制：这不是 paired A/B

`spreadsheet_notebook_baseline_test50_0519.json` 与 `spreadsheet_0519-speedup.json` 的 heldout test task id 完全不重叠：

| overlap | count |
|---|---:|
| baseline test vs evolve test | 0 |
| baseline test vs evolve train | 0 |
| evolve train vs evolve test | 0 |

因此不能说“同一道题 baseline 成功而 evolve 失败”。这不是说 baseline 占了便宜；可观测难度代理反而显示 baseline slice 的 question 更长、range task 更多、checked cells 均值更高。更准确的结论是：当前结果不是 paired A/B，不能做逐题因果归因，但整体指标仍然说明 0519 evolve 没有带来可见收益。

本文的结论分两类：

- 可证实结论：来自整体指标、evolve heldout 内部失败 trace、skill/credit/maintenance 记录。
- 需要 paired rerun 验证的结论：某个 skill 是否对某一道 heldout task 造成直接 harm。

下一步若要做严格因果分析，必须使用固定 split 文件，在同一批 test task 上重跑 notebook baseline `top_k=0` 和 evolve/final-skill 条件，再做逐题对照。

### 2026-05-19 fixed split 更新

已新增固定 split 文件：

| file | n | hash |
|---|---:|---|
| `academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json` | 200 | `04c5121288e0e687` |
| `academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json` | 195 | `a309d0fe247a8d63` |

已在 fixed split 上重跑 notebook baseline：

| run | file | n | success_rate | avg_score | avg_total_tokens | timeout |
|---|---|---:|---:|---:|---:|---:|
| fixedsplit notebook baseline test50 | `academic/results/spreadsheet_0520-fixedsplit-notebook-baseline-test50.json` | 50 | 0.26 | 0.3148 | 7645.1 | 0.0 |

并完成 20/0 train debug：

| run | file | n | success_rate | avg_score | key finding |
|---|---|---:|---:|---:|---|
| fixedsplit train20 debug | `academic/results/spreadsheet_0520-fixedsplit-train20-debug.json` | 20 | 0.30 | 0.3397 | active failed-source skills = 0；executable metadata functional = 6/6；called skill functions = 0 |

因此本文 0519 部分仍用于解释旧结果为什么不 work，但严格 paired 结论应从 `0520-fixedsplit-*` 之后的结果开始建立。

## 总体指标

| metric | notebook baseline test50 | notebook evolve 0519 heldout | single-turn cost baseline |
|---|---:|---:|---:|
| n_tasks / n_runs | 50 / 50 | 50 / 50 | 50 / 50 |
| success_rate | 0.28 | 0.28 | 0.26 |
| n_success | 14 | 14 | 13 |
| avg_score | 0.3600 | 0.3350 | 0.3189 |
| avg_total_tokens | 7151.4 | 13557.4 | 1621.3 |
| avg_input_tokens | 5596.2 | 11594.8 | 747.9 |
| avg_output_tokens | 1555.2 | 1962.5 | 873.5 |
| avg notebook turns | 2.96 | 3.28 | n/a |
| avg task elapsed | 37.377s | 35.408s | 14.495s |
| timeout_rate | 0.0 | 0.0 | 0.0 |

直接结论：

- Notebook evolve heldout 没有超过 notebook baseline：success 同为 `0.28`，avg_score 低 `0.0250`。
- Evolve heldout 的 token 是 notebook baseline 的 `1.90x`，input token 是 `2.07x`。
- Evolve heldout 的 success 只比 single-turn cost baseline 高 `+0.02`，但 token 是 single-turn 的 `8.36x`。
- 当前方法的主要问题不是 timeout，也不是 Python runtime crash，而是 skill 注入和维护链路没有带来可见的 heldout utility。

## 输出分布

Notebook baseline：

| score bucket | count |
|---|---:|
| 1.0 | 14 |
| 0.9-<1 | 2 |
| 0.5-<0.9 | 2 |
| 0-<0.5 | 3 |
| 0 | 29 |

Notebook evolve heldout：

| score bucket | count |
|---|---:|
| 1.0 | 14 |
| 0.9-<1 | 2 |
| 0.5-<0.9 | 1 |
| 0-<0.5 | 0 |
| 0 | 33 |

Evolve 的完全失败更多：score=0 的 task 从 baseline 的 29 个变成 33 个。虽然 success 数一样，但 partial-credit 面更差。

按 notebook turn 数看 evolve heldout：

| turns | n | success | avg_score | avg_tokens |
|---:|---:|---:|---:|---:|
| 2 | 10 | 1 | 0.1979 | 6903.0 |
| 3 | 25 | 8 | 0.3909 | 11558.2 |
| 4 | 6 | 3 | 0.5000 | 17917.7 |
| 5 | 9 | 2 | 0.2222 | 23597.6 |

5-turn cases token 最高，但 avg_score 反而低。这说明多轮通常是在困住后继续消耗 token，不是稳定改进。

## 退化机制 1：skill 全部作为 prompt 注入，但没有被实际调用

Evolve heldout 的 50 个 test task：

| signal | value |
|---|---:|
| tasks with retrieved skills | 50 / 50 |
| tasks with prompt-injected skills | 50 / 50 |
| avg retrieved skills | 3.0 |
| tasks with called skill functions | 0 / 50 |
| avg called skill functions | 0.0 |

最终 store 中虽然有 `executable_tool`：

| skill kind | count |
|---|---:|
| interface_contract_card | 22 |
| workflow_guardrail_card | 14 |
| executable_tool | 14 |

但所有 skill 的 `metadata.injection_type` 都是 `informational`，heldout 里 `called_skill_functions=[]`。也就是说，当前 skill system 对 Spreadsheet 的实际作用几乎完全是“往 prompt 里塞文字/代码示例”，没有可验证的可调用工具收益。

这会产生两个后果：

- 有用 skill 无法被稳定执行，只能靠模型读 prompt 后自行复现。
- 坏 skill 或弱相关 skill 会增加 input token 和认知干扰，却不会被 runtime contract 限制。

## 退化机制 2：大量 active skill 来自失败训练样本

最终 50 个 skill 的 source task 质量：

| source train outcome | skill count |
|---|---:|
| source score = 1.0 | 13 |
| source score = 0.0 | 32 |
| source partial score | 5 |

这非常关键：大多数最终 skill 不是从成功解法中抽取，而是从失败 trace/mismatch 中抽取 repair hint 或 verifier-specific pattern。它们被保留为 active/prompt skills 后，在 heldout 上很容易变成噪声或错误引导。

更严重的是，所有 skill 的 metadata 都显示：

| metadata | value |
|---|---|
| `promotion_state` | `promoted` for 50 / 50 |
| `promotion_reason` | `spreadsheet_successful_source_trace_in_macro_window` for 50 / 50 |

但其中 32 个 source task 的 score 实际是 0。这说明 promotion metadata 与真实 source outcome 不一致，或者 promotion gate 没有把 source success 当硬条件。

### 失败来源 skill 的 heldout 相关性

按 heldout task 检索到的 skill source score 分组：

| retrieved skill source type | n_tasks | success | success_rate | avg_score | avg_tokens |
|---|---:|---:|---:|---:|---:|
| at least one source score=0 skill | 43 | 9 | 0.2093 | 0.2502 | 13226.6 |
| no source score=0 skill | 7 | 5 | 0.7143 | 0.8564 | 15589.3 |

按检索到的失败来源 skill 个数：

| failed-source skills retrieved | n_tasks | success | avg_score | avg_tokens |
|---:|---:|---:|---:|---:|
| 0 | 7 | 5 | 0.8564 | 15589.3 |
| 1 | 19 | 6 | 0.3158 | 13789.9 |
| 2 | 15 | 2 | 0.2504 | 13602.1 |
| 3 | 9 | 1 | 0.1111 | 11411.6 |

这不是严格因果，因为 task difficulty 与 retrieval 可能相关；但它是强烈的风险信号：失败来源 skill 越多，heldout score 越低。

## 退化机制 3：credit 识别了 harm，但没有变成过滤动作

`skill_credit_events` 总数为 120：

| field | distribution |
|---|---|
| judgment | neutral 78, harmful 41, helpful 1 |
| effect_type | no_material_effect 77, workflow_pollution 28, correctness_harm 6, retrieval_noise 5, schema_harm 2, token_overhead 1, workflow_alignment 1 |
| used | False 120 |
| refine_required | False 95, True 25 |
| filter_candidate | False 120 |

这说明 credit assigner 已经看到了大量 harmful / pollution / schema_harm 信号，但没有把它转成实际检索过滤或 skill 降权：

- harmful 41 次，但 `filter_candidate=False` 120/120。
- helpful 只有 1 次，但所有 task 仍然固定 top-k 注入。
- `used=False` 120/120，说明 credit 判断也认为这些 skill 没有被实际使用。

当前 credit 更像事后诊断日志，不是闭环控制器。它没有阻止坏 skill 继续进入 heldout prompt。

## 退化机制 4：macro maintenance 没有执行有效 validation

维护统计：

| component | count / sum |
|---|---:|
| micro_maintenance_reports | 50 |
| micro extraction_reports | 53 |
| micro maintenance_targets | 42 |
| micro maintenance_test_results | 79 |
| micro refine_decisions | 79 |
| maintenance_windows | 5 |
| macro maintenance_targets | 63 |
| macro maintenance_test_results | 0 |
| macro refine_decisions | 0 |

Macro window 有大量 promoted skills，但 `maintenance_test_results=0`、`refine_decisions=0`。这意味着最终被 promoted 的 skill 没有经过足够的 benchmark-level validation。

这与最终 store 的症状一致：有些 skill 是失败 trace repair hint，有些是成功 task 的具体代码片段，但都被统一 promoted 为 prompt knowledge。

## 退化机制 5：retrieval 主要靠浅层文本相似，泛化差

Evolve heldout top retrieved skills：

| skill | retrieved | success when retrieved | avg_score |
|---|---:|---:|---:|
| `spreadsheet_sheet_level_manipulation_header_placement_off_by_one` | 19 | 8 | 0.4211 |
| `spreadsheet_cell_level_manipulation_vlookup_wrong_lookup_column` | 14 | 3 | 0.2698 |
| `spreadsheet_cell_level_manipulation_date_block_agent_lookup` | 13 | 4 | 0.3077 |
| `spreadsheet_cell_level_manipulation_nested_lookup_three_dimensional_incentive` | 10 | 2 | 0.2979 |
| `spreadsheet_sheet_level_manipulation_level_manipulation_here_what` | 10 | 6 | 0.7773 |
| `spreadsheet_repair_cell_level_manipulation_vlookup_column_offset_subtraction` | 9 | 1 | 0.3063 |
| `spreadsheet_repair_cell_level_manipulation_openpyxl_worksheet_formula_arrayformula__pending_7` | 8 | 2 | 0.2500 |
| `spreadsheet_repair_cell_level_manipulation_sumifs_argument_order` | 4 | 0 | 0.0000 |

其中很多 skill 名称和 body 暴露了低质量泛化：

- `level_manipulation_need_visual`
- `level_manipulation_here_what`
- `level_manipulation_how_can__pending_1`
- `openpyxl_worksheet_formula_arrayformula__pending_7`

这些名字来自用户问题中的高频泛词，而不是稳定 task semantics。它们容易被任意 Spreadsheet task 检索到，但实际约束很窄。

## Case Study 1：52216，INDEX/MATCH 被改成 SUMIFS，array formula 失败

Evolve heldout case：

| field | value |
|---|---|
| task_id | 52216 |
| success / score | false / 0.0 |
| tokens / turns | 28882 / 5 |
| answer range | `'INPUTS'!C15:G16` |
| retrieved skills | `header_placement_off_by_one`, `nested_lookup_three_dimensional_incentive`, `level_manipulation_have_data` |
| called skills | none |

Verifier mismatch example：

```text
INPUTS!C15 predicted =SUMIFS(Sheet2!C:C, Sheet2!$A:$A, DATA!$E$4, Sheet2!$B:$B, $A15)
INPUTS!C15 expected  <openpyxl.worksheet.formula.ArrayFormula object ...>
```

What happened：

- The task asks for INDEX/MATCH-like formula behavior over dropdown-dependent rows.
- Retrieved skills were not specific enough: one header placement skill, one nested lookup skill, one generic sheet manipulation repair.
- The model generated SUMIFS formulas, not the expected array formula object / exact formula structure.
- It spent 5 turns and 28.9k tokens, but final output was still score 0.

Interpretation：

- Retrieval added context but not the exact constraint needed.
- The array-formula repair skills did not reliably teach how to write/preserve expected array formulas.
- Multi-turn repair did not get real verifier feedback; it inspected its own workbook/output, not the benchmark golden formula object.

## Case Study 2：34033，date-block lookup writes static values instead of formulas

Evolve heldout case：

| field | value |
|---|---|
| task_id | 34033 |
| success / score | false / 0.0 |
| tokens / turns | 27155 / 5 |
| answer range | `K6:K10` |
| retrieved skills | `date_block_agent_lookup`, `level_manipulation_need_visual`, `sumifs_argument_order` |
| called skills | none |

Verifier mismatch example：

```text
ExcelForum help!K6 predicted 26
ExcelForum help!K6 expected  <openpyxl.worksheet.formula.ArrayFormula object ...>
```

What happened：

- The model reasoned about the data correctly enough to produce values, but the benchmark expected formula objects.
- `sumifs_argument_order` is unrelated to this task.
- `level_manipulation_need_visual` is a copied code-pattern skill from a different source task with answer range `B2`, not relevant here.

Interpretation：

- Retrieval can select skills with superficial terms like date / visual / formula even when answer-type semantics differ.
- For SpreadsheetBench, formula-vs-value is a first-class compatibility constraint. Current retrieval does not gate on it.

## Case Study 3：54925，array formula skill is too generic and object-repr based

Evolve heldout case：

| field | value |
|---|---|
| task_id | 54925 |
| success / score | false / 0.0 |
| tokens / turns | 26743 / 5 |
| answer range | `Q2:Q40` |
| retrieved skills | `openpyxl_worksheet_formula_arrayformula__pending_7`, `header_placement_off_by_one`, `nested_lookup_three_dimensional_incentive` |
| called skills | none |

The retrieved `openpyxl_worksheet_formula_arrayformula__pending_7` skill body includes evidence like:

```text
expected: "<openpyxl.worksheet.formula.ArrayFormula object at ...>"
Corrective rule: preserve the official spreadsheet semantics...
```

What happened：

- The skill correctly flags “array formula risk” at a high level.
- But it contains object reprs and generic wording, not a concrete executable way to create the required array formula for this workbook.
- The model writes partial formulas/static outputs; verifier still sees formula-object mismatches.

Interpretation：

- Failure-derived repair cards are not enough for array formula tasks.
- The system needs a typed formula skill or executor support, not a natural-language warning containing object reprs.

## Case Study 4：53117，wrong retrieval family for MAXIFS/INDEX task

Evolve heldout case：

| field | value |
|---|---|
| task_id | 53117 |
| success / score | false / 0.0 |
| tokens / turns | 20258 / 5 |
| answer range | `D2:D27` |
| retrieved skills | `vlookup_wrong_lookup_column`, `index_match_header_lookup_numeric_column`, `vlookup_column_offset_subtraction` |
| called skills | none |

Verifier mismatch example：

```text
Sheet1!D2 predicted =C2
Sheet1!D2 expected  =IF(C2="US",INDEX($C$2:$C$27,MATCH(_xlfn.MAXIFS(...)),C2)
```

What happened：

- The task requires conditional formula generation using `IF`, `INDEX`, `MATCH`, and `_xlfn.MAXIFS`.
- Retrieval pulled VLOOKUP-specific skills.
- The model produced a custom SUMPRODUCT/INDEX formula in some rows and simple `=C2` in others, not the expected formula pattern.

Interpretation：

- Retrieval should classify formula operator family and reject VLOOKUP cards for MAXIFS/INDEX tasks.
- Current text matching over “lookup” is too broad.

## Case Study 5：250-20，almost correct but strict verifier detail missed

Evolve heldout case：

| field | value |
|---|---|
| task_id | 250-20 |
| success / score | false / 0.995 |
| tokens / turns | 14870 / 3 |
| answer range | `'RNM'!A1:J20` |
| mismatch | `RNM!J19 predicted 0, expected ""` |

What happened：

- The workbook transformation was essentially correct.
- One blank-vs-zero detail caused task-level failure despite high avg_score.

Interpretation：

- Spreadsheet task-level success is extremely strict.
- We should report avg_score and near-miss counts, not only success.
- A verifier-feedback repair loop could fix this kind of issue cheaply if it saw mismatch feedback before finalizing.

## Baseline output observations

Notebook baseline also has many failures; notebook alone is not enough. Examples:

- `48643`: score 0.0. It wrote `=(1+C2)*B2` for the first row, but expected `=IF(ROW(B2)=2,F2,(H1+B2)*(1+C2))`. This is formula exactness, not runtime failure.
- `269-44`: score 0.8667. It deleted almost the right rows but shifted one `Chassis` row.
- `91-34`: score 0.9443. Row deletion logic mostly worked but row order/deletion details mismatched.
- `118-50`: score 0.9978 but task-level failure over a huge answer range because early expected cells were blank/predicted mismatched.

This matters because the right target is not merely “add skills”; the executor needs exact formula/style/blank semantics and verifier-aware repair.

## Root Cause Summary

The method did not work for Spreadsheet because the current skill loop is misaligned with SpreadsheetBench's failure modes.

1. SpreadsheetBench rewards exact workbook artifacts, formula strings, blank-vs-zero behavior, array formulas, row order, and style/placement. Generic natural-language skill cards are too weak for this.
2. The training pipeline promoted many skills extracted from failed traces. These are often “what went wrong” patterns, not validated reusable solutions.
3. Credit assignment identified many harmful/no-effect skills but did not filter or demote them.
4. Retrieved skills were injected as prompt text only; none were called as functions/tools in heldout.
5. Retrieval was too broad and keyword-like, so narrow skills were applied to semantically different tasks.
6. Maintenance generated cost and prompt bulk without proving that each skill improves any validation task.
7. Multi-turn notebook repair lacked golden/verifier feedback; it could inspect its own output but not detect formula-object mismatch or exact expected formula.

## Recommended Fixes

### 1. Fix the experimental protocol first

Required paired evaluations:

1. On the exact 0519 evolve heldout task ids, run notebook baseline with `top_k=0`.
2. On the same ids, run notebook with current final skills, `top_k=3`.
3. On the same ids, run ablations:
   - no failure-source skills
   - no repair cards
   - only source-success skills
   - dynamic top-k with confidence threshold
   - no prompt skills, callable tools only

This requires Spreadsheet support for explicit `--test-task-ids` or an equivalent frozen id-list runner. Without paired ids, we cannot quantify per-task harm.

### 2. Add hard promotion gates

Do not promote a skill to active prompt injection unless at least one condition is true:

- It comes from a successful source task (`score=1.0`), and its source trace contains the exact applied solution.
- It comes from a failed task but has a generated repair test that passes after applying the skill.
- It improves at least one validation task in counterfactual replay.

Failure-derived skills should default to disabled negative evidence, not active prompt skills.

Concrete gates:

- Reject active skills whose source task score is 0 unless a repair test passes.
- Reject skill bodies containing raw object reprs such as `<openpyxl.worksheet.formula.ArrayFormula object at ...>`.
- Reject vague source-keyword names like `level_manipulation_need_visual`, `level_manipulation_here_what`, `how_can__pending`.
- Require non-applicability clauses to be machine-checkable, not only free text.

### 3. Make credit operational

Current credit statistics show the signal exists but is not used:

- harmful: 41
- helpful: 1
- filter_candidate: 0

Change credit behavior:

- If `judgment=harmful` or `effect_type in {workflow_pollution, correctness_harm, schema_harm}`, decrement retrieval weight or disable the skill for that task family.
- If a skill is repeatedly retrieved but `used=False` and no score lift is observed, demote it.
- If `filter_candidate=True` is never emitted, update the credit prompt/schema so harmful judgments must choose a concrete action: `demote`, `disable`, `narrow_scope`, or `keep`.
- Feed credit results back into the retriever before final heldout testing.

### 4. Add semantic retrieval filters

Before top-k injection, classify both task and skill:

- instruction type: cell formula vs sheet manipulation vs formatting/style vs VBA-like workflow
- answer type: formula string, array formula, static values, row deletion, table generation
- operator family: VLOOKUP, INDEX/MATCH, MAXIFS/SUMIFS, FILTER/array, date lookup, row grouping
- answer range shape: single cell, column formula, rectangular table, whole sheet
- source quality: source score, validation score, harmful credit count

Then enforce compatibility:

- Do not retrieve VLOOKUP skills for MAXIFS/INDEX tasks unless the task classifier explicitly includes VLOOKUP.
- Do not retrieve row-deletion executable examples for formula-only answer ranges.
- Do not retrieve header-placement skills unless instruction includes “from [cell] with headers” or answer range starts at a header-like cell.
- Prefer no skill over a low-confidence skill.

### 5. Convert real executable skills into callable tools

For Spreadsheet, code snippets are risky as prompt examples. If a skill is truly executable:

- Put the function in a callable module with a typed signature.
- Run unit tests on small workbook fixtures.
- Expose it to notebook as an importable helper.
- Track `called_skill_functions` and only credit direct calls when the helper materially affects output.

Prompt-only skill cards should be short contracts, not long copied notebook code.

### 6. Add verifier-aware repair for near misses

For high-score failures like `250-20`, the model needs mismatch feedback:

- After each notebook attempt, run verifier.
- Feed a compact mismatch projection back to the model:
  - cell
  - predicted
  - expected
  - blank-vs-zero / formula-vs-value / style / row-shift category
- Allow one cheap repair turn before final.

This is especially useful for blank-vs-zero and off-by-one row/order errors.

### 7. Treat array formula as a first-class executor capability

ArrayFormula tasks are a repeated failure mode. Natural-language repair cards are not sufficient.

Needed changes:

- Detect expected array formula tasks from task/workbook/verifier signals.
- Provide a tested openpyxl helper for writing array formulas correctly.
- Do not store object reprs in skill evidence.
- Learn and inject formula templates, not `<ArrayFormula object>` mismatch text.
- If array formulas cannot be reliably authored by openpyxl, mark these tasks as requiring formula-string preservation or special handling.

### 8. Reduce prompt bulk

Evolve heldout input tokens are `2.07x` baseline with no success lift. Until skill utility is proven:

- Use dynamic top-k, default 0 if no high-confidence match.
- Cap skill prompt budget aggressively.
- Prefer one compact contract over three long examples.
- Suppress skills from failed source tasks.
- Report per-task “skill prompt chars” and compare score lift.

## Proposed next experiment

Run a controlled paired experiment on the exact 0519 evolve heldout ids:

| condition | purpose |
|---|---|
| A: notebook baseline `top_k=0` | true baseline on same split |
| B: current final store `top_k=3` | reproduce current skill injection effect |
| C: only source-success skills | test whether failed-source skills are harmful |
| D: no repair cards | test whether repair cards pollute prompt |
| E: semantic retrieval + min score | test retrieval gating |
| F: verifier repair turn | test near-miss recovery |

Primary metrics:

- paired delta score per task
- paired success flip counts: win/loss/tie
- token delta
- retrieved skill source score
- harmful credit count per task
- formula-vs-value mismatch count
- blank-vs-zero mismatch count
- array formula mismatch count

Stop condition:

- If B is worse than A on paired avg_score or has no success lift with >1.5x tokens, do not use current prompt-skill injection for Spreadsheet.
- If C beats B, block failure-source skills from active retrieval.
- If E beats B with lower tokens, make semantic retrieval the default.

## Bottom Line

Spreadsheet did not work because the current algorithm learned and injected weak or failure-derived prompt knowledge into a benchmark that requires exact executable spreadsheet artifacts. The system paid substantial token and maintenance cost, but the injected skills were never actually called, were often sourced from failed traces, and were not filtered even when credit assignment judged them harmful or irrelevant.

The next fix should not be “more skills” or “more maintenance”. It should be:

1. paired same-split evaluation,
2. hard promotion/validation gates,
3. operational credit-based filtering,
4. semantic retrieval,
5. callable tested spreadsheet helpers,
6. verifier-aware repair for exact-output mismatches.
