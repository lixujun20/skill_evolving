# Spreadsheet Refactor Parity Audit

日期：2026-05-19

本文记录对 Spreadsheet 重构前后关键路径的逐段核查。对照版本主要为 `bde0b30`（`speedup`，重构前单文件 Spreadsheet adapter）和当前 `spreadsheet-multiturn-notebook`。

## 结论

重构后的模块拆分总体保留了旧 Spreadsheet 行为：loader、executor、skill runtime、extract/credit/micro/macro/promotion/filter 的主体逻辑与 `bde0b30` 一致。0519 结果暴露出的两个核心问题不是拆文件时新引入的唯一偏差，而是旧 Spreadsheet 实现中已有的危险行为被保留下来：

1. `executable_tool` 可被写成 `metadata.injection_type="informational"`，覆盖 `SkillArtifact.injection_type()` 对 executable 的 functional 默认判断。
2. failure-derived repair artifact 会被 `_coerce_spreadsheet_artifact` 加入 positive case，从而满足 macro promote 的最小门槛。

本次修复将这两点明确标为旧 bug，不作为 parity 目标继续保留。

## Split / Loader

旧版和当前版均使用同一逻辑：

```python
shuffled = list(tasks)
random.Random(split_seed).shuffle(shuffled)
return shuffled[:n_train], shuffled[n_train : n_train + n_test]
```

问题是 test slice 取决于 `n_train`。baseline 默认 `n_train=200`，evolve 50/50 使用 `n_train=50`，因此两者 test slice 不对齐。这个行为是旧逻辑，不是重构新增。

修复：

- 新增固定 split 文件：
  - `academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json`
  - `academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json`
- 当前有效 dataset task 总数为 395，因此 train=200，test=195。
- Runner 支持 Spreadsheet 使用 `--train-task-ids` / `--test-task-ids`，优先按固定 id 文件选任务，再应用 offset/limit。

## Executor / Skill Runtime

旧版 `bde0b30` 已有 callable skill library：

- `_write_spreadsheet_skill_library(...)`
- `_is_spreadsheet_callable_skill(...)`
- `_spreadsheet_skill_code(...)`
- `_called_spreadsheet_skill_functions(...)`

当前重构后对应模块为：

- `academic/benchmarks/spreadsheet/skill_runtime.py`
- `academic/benchmarks/spreadsheet/executor.py`

核心行为一致：callable exposure 根据 kind + code availability，而不是仅根据 `metadata.injection_type`。这也是为什么 legacy informational executable 的 callable 测试能通过。

本次修复点：

- 继续保留 legacy fallback：旧 store 中 informational executable 仍可 callable。
- 但新抽取的 executable artifact 必须被规范化为 `functional`，避免 prompt injector、report、cost/credit 语义错误。

## Extract / Coerce

旧版与当前版 `_coerce_spreadsheet_artifact` 都有如下问题：

```python
"injection_type": metadata.get("injection_type") or "informational"
```

这会把 LLM schema 中默认的 informational 写入 metadata，覆盖 executable 的 functional 默认。

旧版与当前版还都会无条件添加 positive case：

```python
artifact.bundle.positive_cases.append(...)
```

即使 trace 是 failed repair evidence，也会得到 positive case。

修复：

- executable/function/script 且可提取 spreadsheet code 时，强制 `injection_type="functional"`。
- workflow card 强制 `workflow`，interface contract card 保持 `informational`。
- source success/score>=0.9 才添加 positive case。
- failure-derived repair artifact 添加 negative case，并保持 pending，必须经过 validation 才能 promote。

## Credit / Filter

旧版和当前版 credit prompt 都要求：

- retrieved 不等于 helpful。
- harmful 需要因果证据。
- `filter_candidate` 只在需要禁用/移除时为 true。

旧版和当前版 macro filter 都只在重复 harmful 且无 helpful 时禁用：

```python
if len(rows) < 2 or helpful_by_skill.get(skill_name, 0) > 0:
    continue
```

这导致单次强 `filter_candidate=True` 也可能无法立即禁用。

修复：

- `filter_candidate=True` 可作为单次强过滤信号。
- 重复 high-confidence harmful 仍保留为过滤条件。
- disabled artifact 记录 `disabled_credit_events` 便于追溯。

## Promote

旧版和当前版 promotion 只检查：

- pending source id 在 window 中。
- artifact 有 positive cases。

由于 failure-derived repair artifact 被错误加 positive case，失败来源 skill 可以被 promote 成 active。

修复：

- promote 需要 successful source task 或 passing bundle/counterfactual test。
- 不满足条件时保留 pending，并写 `promotion_blocked_reason="requires_successful_source_or_passing_bundle_test"`。

## Paper Pseudocode 对齐

当前修复后的 Spreadsheet 路径对齐论文流程：

1. Rollout：executor 运行 task，记录 trace、retrieved/injected/called、tokens、verifier result。
2. Extract：只从 success trace 直接产生可 promote skill；failure trace 只能产生 repair evidence，不能直接 active。
3. Credit：对 exposed/called candidates 做 attribution，harm/filter 信号写入 evidence。
4. Micro：对 strong credit / bundle case 做 refine/test。
5. Macro：只有 validated posterior evidence 可以 promote pending；harmful/filter 信号可以 disable。
6. Heldout：从固定 test split 选择 prefix/offset，保证 baseline/evolve paired。

## 必须保持的测试契约

- legacy informational executable 仍能 callable。
- new executable artifact 必须 functional。
- failure-derived artifact 不得凭 failed source 自动 promote。
- `filter_candidate=True` 必须产生实际 filter/demote。
- fixed split 文件必须稳定，baseline/evolve 使用同一 test file 时 task ids 对齐。

## 20/0 Fixed-Split 复查结果

复查文件：`academic/results/spreadsheet_0520-fixedsplit-train20-debug.json`

| check | result |
|---|---:|
| train prefix hash | `afbbdccb557d3dbb` |
| n_train_runs | 20 |
| success_rate / avg_score | 0.30 / 0.3397 |
| final skills | 14 |
| active / pending / disabled | 5 / 8 / 1 |
| executable_tool count | 6 |
| executable metadata functional | 6 / 6 |
| active failed-source skills | 0 |
| pending blocked by validation gate | 8 |
| disabled by harmful credit | 1 |
| callable exposed runs | 10 / 20 |
| called skill functions | 0 / 20 |

结论：

- `callable skill 都标成 informational` 的新产物问题已修复；新 executable tool metadata 均为 `functional`。
- `失败来源 skill 被 promote 成 active` 的问题在该 run 中消失；失败/未验证来源保留为 pending，并写入 `promotion_blocked_reason="requires_successful_source_or_passing_bundle_test"`。
- 仍未解决的是模型实际不调用 `skill_library`：即使 callable skills 被暴露，`called_skill_functions` 仍为 0。这不是重构 parity bug，而是下一步 skill 形态/prompt/executor 协议问题。
