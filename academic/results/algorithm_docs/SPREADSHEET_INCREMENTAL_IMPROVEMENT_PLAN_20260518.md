# Spreadsheet 分章节改进计划

本计划用于后续逐章实现。执行规则：每次只改一个章节，跑该章节测试，通过后再进入下一章；每章独立提交，不混入后续优化。

## Chapter 1: Credit 输入收窄

目标：Spreadsheet credit assigner 只对 executor 实际暴露或调用过的 skill 做归因，不再把 retrieved-only skill 当作候选。

实现要点：
- trace projection 区分 `retrieved_skills`、`prompt_injected_skills`、`callable_skills`、`called_skill_functions`。
- single-run 与 notebook executor 从生成代码中解析 `skill_library` import/call。
- `assign_credit()` 使用 exposed/called skill names，而不是 retrieved names。
- retrieved-only skill 只进入 audit metadata，不进入 candidate skill prompt。
- function skill 的 evidence 中区分 `retrieved`、`injected`、`used`，实际 import/call 时 `used=true`。

测试：
- retrieved-only 不进入 credit prompt。
- prompt-injected workflow skill 进入 credit prompt。
- callable function skill 被 import/call 后进入 credit prompt，且 evidence 标记 `used=true`。
- heuristic credit fallback 同样只处理 exposed/called skills。

## Chapter 2: Micro target 收窄

目标：helpful credit 默认只记录 evidence，不触发 immediate refine/test。

实现要点：
- `_spreadsheet_micro_targets()` 只对 strong harmful、`filter_candidate`、`refine_required`、negative/integration bundle case 触发维护。
- helpful positive case 进入 evidence 或 bundle candidate，但不立即 test/refine。
- 增加 target reason audit。

测试：
- helpful high confidence 不调用 refiner/bundle test。
- harmful high confidence 调用 pre-refine。
- neutral/uncertain 不触发。

## Chapter 3: Bundle replay 分级

目标：先 strict gate，再 with-skill，只有 with-skill 通过才跑 without-skill。

实现要点：
- strict gate 检查 official task snapshot、workbook/golden/range、callable parse/import、prompt budget。
- with-skill fail 时跳过 without-skill，直接记录 failure。
- result schema 记录 strict gate、with/without 结果和 skipped reason。

测试：
- gate fail 不调用 LLM。
- with fail 不跑 without。
- with pass 才跑 without。

## Chapter 4: Bundle case 固定预算

目标：Spreadsheet 训练期每个 skill 只保留少量 active replay cases。

实现要点：
- `SPREADSHEET_BUNDLE_CASE_LIMIT_PER_POLARITY=1`。
- `SPREADSHEET_BUNDLE_MAX_TOTAL_CASES=2`。
- 超额 case 进入 audit/evidence ledger。
- 优先保留 recent negative/integration、高 confidence、regression。

测试：
- 超额 positive 被 trim。
- high-confidence negative 优先保留。
- dropped cases 可追溯。

## Chapter 5: Compact skill projection 与 prompt schema

目标：credit/refiner 不再读取长 skill body，降低 prompt 长度和乱填字段。

实现要点：
- 新增 Spreadsheet compact skill card。
- credit/refiner 使用同一投影。
- extractor/refiner prompt 增加 body/code/applicability 长度约束、参数表、返回值、调用示例、非适用条件。

测试：
- prompt 不含 full body。
- 超长 function skill 被 trim/拒绝。
- mock LLM 多余字段 normalize 稳定。

## Chapter 6: Notebook trace projection

目标：维护 agent 只读 notebook 关键执行信号，不读完整多轮 trace。

实现要点：
- projection 包含关键 code cell、stdout/stderr、exception、changed variables、final answer candidate、expected range。
- credit/extractor/refiner 使用 projection。

测试：
- 多轮 trace 被压缩。
- exception/stderr 保留。
- projection size 有上限。

## Chapter 7: Generic runner 并发下沉

目标：Spreadsheet 训练使用 benchmark-agnostic window concurrency 和 per-skill locks。

实现要点：
- core runner 增加 window concurrency、micro concurrency、per-skill write lock。
- Spreadsheet adapter 只声明 target skills/dependency neighborhood。
- macro 仍在 window barrier 后执行。

测试：
- mock sleep 下 15-task 并发加速。
- 同 skill refine 串行，不同 skill 并发。
- dependency 变化标 stale。

## Chapter 8: 小规模真实验证

目标：验证改动确实降低维护开销且不破坏效果。

协议：
- 章节 1-3 后跑 3 train / 2 test smoke。
- 全章节后跑 10 train / 10 test。
- 报告 success、avg_score、input/output tokens、maintenance role tokens、credit/refiner/bundle calls、wall-clock、actual callable imports/calls。
