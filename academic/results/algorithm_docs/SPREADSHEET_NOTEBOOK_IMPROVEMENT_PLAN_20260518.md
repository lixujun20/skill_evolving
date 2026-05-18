# Spreadsheet Notebook 改进计划

本文档回答当前 Spreadsheet notebook 为什么慢、为什么效果不稳定，以及近期怎么改。目标是让 notebook setting 真正测到运行时反馈和 skill 复用，而不是被维护 prompt 与 bundle replay 开销淹没。

## 当前问题

`sheet_nb_train50_20260518_202840` 已停止。日志显示主要耗时来自维护链路：

| role | calls | total_s | avg_s | 主要问题 |
|---|---:|---:|---:|---|
| spreadsheet_extractor | 19 | 625.9 | 32.94 | trace projection 仍长，prompt 约 10k-16k chars |
| spreadsheet_credit_assigner | 9 | 346.2 | 38.47 | candidate skill projection 太大，平均 prompt 约 24k chars |
| refiner | 41 | 340.9 | 8.31 | helpful/weak credit 也可能触发维护，次数偏多 |

notebook executor 不是最大瓶颈。真正的问题是 generic online runner 仍然串行执行 task -> credit -> bundle -> micro -> macro，且 Spreadsheet 没有吃到 BFCL 细粒度锁并发的收益。

## 近期改进

### 1. Credit assigner 只看真正暴露的 skill

当前 Spreadsheet credit 使用 retrieved skills，容易把每步检索到但没有进入 executor context 的 skill 都塞给 credit assigner。应改成：

- workflow/knowledge skill：只要进入 executor prompt，算 exposed。
- function skill：只有 `from skill_library import ...`、直接函数调用、或 notebook trace 中出现 callable invocation，才算 direct_use。
- retrieved 但未 injected/called 的 skill 不参与 per-skill credit，只能作为 retrieval audit。

预期效果：credit prompt 变短，误归因减少。

### 2. Helpful credit 不默认触发 refine/test

当前 `_spreadsheet_micro_targets` 包含 helpful credit，可能导致正向 evidence 也触发 bundle replay。应改成：

- harmful + high confidence：允许 pre-refine。
- `filter_candidate=true`：允许 pre-refine 或 disable check。
- `refine_required=true`：允许 refine。
- helpful：默认只写 evidence 和 positive bundle candidate，不立即 replay/refine。
- neutral/uncertain：只写 evidence。

预期效果：refiner 调用数下降，bundle replay 数下降。

### 3. Bundle replay 加 strict gate

顺序应为：

1. static/strict gate：检查 case 是否 replayable、workbook/range/golden 是否齐全、function skill 是否可 import、prompt budget 是否合格。
2. with-skill replay：先验证 skill 是否能帮助完成。
3. without-skill replay：只有 with-skill 成功后才跑，用来估计 utility。
4. with-skill failed：直接 refine 或保留失败 evidence，不跑 without。

预期效果：避免大量无意义 without replay。

### 4. Bundle case cap 更紧

训练阶段每个 skill：

- active replay cases 最多 1-2 个。
- 超过部分只进 evidence ledger。
- macro 时再根据 recent/high-confidence/regression 选择要升格的 case。

预期效果：把 bundle replay 从“随 evidence 增长”改成“固定预算”。

### 5. Skill prompt 与 projection 压缩

credit/refiner 不应拼 `body[:1800] + metadata + evidence`。改成统一 compact card：

- skill_name
- kind
- one-line contract
- input/output contract
- applicability
- non-applicability
- last 1-2 evidence ids
- status/version

function skill 的完整代码不进入 credit；只有 strict gate/import test 或 executor callable map 需要代码。

### 6. Extractor/refiner 生成函数优先 skill

对 Spreadsheet function skill 增加硬约束：

- body <= 500 chars。
- applicability <= 120 chars。
- code <= 40 lines。
- 必须有参数表。
- 必须有返回值说明。
- 必须有 one-line import/call example。
- 必须写 non-applicability。

目标是从训练阶段就生成可调用资产，而不是事后包装自由文本 snippet。

### 7. Notebook trace projection 结构化

每个 task 只给维护 agent：

- compact task summary。
- code cell list，最多保留关键 cell。
- stdout/stderr summary。
- exception type/message。
- changed variables summary。
- final answer candidate。
- expected range/golden summary。

不传完整 workbook dump、不传所有 debug events。

### 8. Generic runner 并发下沉

将 BFCL 的并发/锁机制抽到 benchmark-agnostic runner：

- window 内 task rollout 并发。
- micro maintenance 目标 skill 写锁。
- dependency/reference skill 读锁。
- relation graph 短锁更新。
- macro window barrier 后串行或短锁执行。

Spreadsheet 才能和 BFCL 一样享受并发，而不是全串行。

## 中期实验协议

先跑小规模：

| setting | train | test | 目的 |
|---|---:|---:|---|
| notebook baseline | 0 | 10 | 只测多轮 executor 是否比 single-turn 稳 |
| notebook full skill | 0 | 10 | 上界，允许完整 skill prompt |
| notebook compact callable | 0 | 10 | 测 callable map 能否省 token |
| notebook evolve small | 10 | 10 | 测训练链路和维护开销 |
| notebook evolve 50 | 50 | 50 | 仅在 small 通过后跑 |

每个结果必须报告：

- success/pass_at_k。
- avg_score。
- input/cache-input/output。
- executor vs maintenance token。
- skill_prompt_chars。
- notebook turns per task。
- code cells per task。
- exceptions per task。
- actual callable imports/calls。
- correct-only cost。

## 远期方向

- 把 SpreadsheetBench 改造成标准 inspect -> execute -> observe -> repair -> final 多轮任务。
- 让 function skill 通过 Python import 复用，workflow/knowledge skill 只通过短 contract 注入。
- 增加 sentinel heldout probes：每个 macro 只测 5-10 个固定任务，最终再跑完整 50。
- 与 SkillsBench 对齐 skill-use 评测：官方 skill、full evolved skill、compact evolved skill、callable evolved skill 四组对照。
- 把 cost/correctness 做成论文主指标之一：不仅报 accuracy，也报 score per million tokens 和 correct-only cost。
