# Spreadsheet Folder Bash Haiku 20/0 Chronology, 2026-05-21

本文记录这次 Spreadsheet folder skill 实验的完整过程，按时间顺序保留启动、监控、结果、对比与结论。目标是持续观察三件事：

1. 是否出现 `call` 或 `copy code` 式的 skill 使用。
2. 新提取的 skill 是否能进入后续流程并正常发挥作用。
3. 有问题的 skill 是否能被及时发现、修正或丢弃。

## 实验配置

- Tag: `0521-folder-bash-haiku-20_0`
- Benchmark: `spreadsheet`
- Mode: `generic_evolve_train`
- Train/Test: `20/0`
- Executor model: `claude-sonnet-4-5`
- Maintenance model: `claude-haiku-4-5`
- Execution mode: `bash_react`
- Callable disclosure: `progressive`
- Pending exposure fraction: `0.34`

对照 baseline:

- File: `academic/results/spreadsheet_0520-fixedsplit-bash-react-baseline-test50.json`
- Mode: `baseline`
- Runs: `50`
- Success rate: `0.28`
- Avg score: `0.3003`
- Avg total tokens: `21114.2`

## 时间线

### 2026-05-20 17:33

实验启动。该轮使用 folder skill 形态，executor 仍是 `claude-sonnet-4-5`，maintenance 侧切到 `claude-haiku-4-5`。

### 2026-05-20 17:33 - 18:05

训练过程持续运行，共 20 个 train task。

关键现象：

- `callable_skills` 始终为空。
- `called_skill_functions` 始终为空。
- `skill_code_reads` 始终为空。
- 有少量 skill 被注入到 prompt，但模型没有把它们转化为真正的 callable 使用。

代表性 case:

- `56786`:
  - 注入了 `align_data_to_timeline`
  - prompt 中出现了该 skill 的 folder 说明和脚本路径
  - 但 executor 最终仍然自己写了 openpyxl 逻辑
  - trace 中 `called_skill_functions: []`
  - `skill_code_reads: []`
  - 说明是“看到 skill 但没有调用”，不是“调用了 skill 再失败”

- `18935`:
  - 没有注入任何 skill
  - 直接走了基础 bash React 方案
  - 结果失败，说明并不是所有 task 都会自然触发 skill 使用

- `55427`:
  - 同样没有出现 callable skill 调用
  - 结果弱于 baseline 式直接解法

### 2026-05-20 17:59 - 18:05

maintenance 侧进入 credit / refine / macro 汇总。

观察到：

- 训练阶段一共跑了 `20` 个任务。
- micro maintenance reports 为 `20`。
- macro windows 为 `2`。
- 最终 store 中只有 `6` 个 skill。
- `n_active = 0`
- `n_pending = 4`
- `n_disabled = 2`

macro snapshot:

- 第一个 macro window（训练完成 10 个任务时）没有 promote 任何 pending skill。
- 最终 macro window 只 disabled 了两个 harmful skill，没有新增 active skill。

被 disable 的两个 skill：

- `spreadsheet_cell_level_manipulation_extract_number_last_day`
- `spreadsheet_sheet_level_manipulation_need_vba_code_sheet1`

仍为 pending 的四个 skill：

- `find_and_move_column_by_header`
- `insert_rows_above_marker`
- `spreadsheet_sheet_level_manipulation_extract_all_rows_imported`
- `cross_sheet_row_matching_deletion`

## 结果

### Train summary

- `n_runs = 20`
- `n_success = 6`
- `success_rate = 0.30`
- `avg_score = 0.3397`
- `avg_total_tokens = 19995.25`

### Cost accounting

Train summary cost breakdown:

- Total calls: `192`
- Total tokens: `399905`
- Injector calls: `96`
- Executor calls: `96`

Maintenance token stats:

- Total maintenance calls: `128`
- Total maintenance tokens: `335120`
- Folder extractor: `59` calls, `208008` tokens
- Package refiner: `20` calls, `35664` tokens
- Skill injector: `45` calls, `63536` tokens
- Credit assigner: `4` calls, `27912` tokens

结论很直接：成本主要压在 folder extractor 和 executor 侧，maintenance 中 extractor 占比最高。

### No-call verdict

本轮没有观察到真正的 callable skill 执行。

证据：

- 全局聚合里 `called_skill_functions` 为 `0`
- 代表性任务的 `callable_skills` 为空
- 代表性任务的 `skill_code_reads` 为空
- prompt 注入并没有自动转化成 skill 调用

因此，这轮证明的是“skill 可注入、可展示、可过滤”，不是“skill 已经可被稳定调用”。

## 与 baseline 的对比

baseline 50-run:

- `success_rate = 0.28`
- `avg_score = 0.3003`
- `avg_total_tokens = 21114.2`

本轮 20/0:

- `success_rate = 0.30`
- `avg_score = 0.3397`
- `avg_total_tokens = 19995.25`

注意：这不是严格同分布的同规模对照，只能看趋势，不能过度解读。就当前结果看，本轮没有明显改善到“skill 开始被调用”的层面。

## Case study

### 1. `56786`

这是最接近“应该能用 skill”的例子。

现象：

- skill `align_data_to_timeline` 被检索并注入
- prompt 中显示了 folder skill 的说明、脚本路径、`SKILL.md`
- 但 executor 仍然直接写 openpyxl 代码，不去调用 skill

判断：

- 问题不在于完全没检索到 skill
- 问题在于 prompt 协议和 executor 决策没有把 skill 变成“必须优先尝试的操作单元”

### 2. `18935`

该任务没有 skill 注入，直接走通用解法，结果失败。

判断：

- 说明 skill 检索不是每个任务都触发
- 也说明即使不开 skill，executor 仍会默认写自己的脚本逻辑

### 3. `55427`

同样没有出现 callable skill 使用。

判断：

- 这类 lookup / merge 任务没有自然触发我们想要的 skill 调用链
- 需要后续从 prompt 协议、skill 形态、检索约束、injector 决策四个环节继续排查

## 当前结论

1. 本轮没有出现真正的 skill function call。
2. 也没有出现可确认的“复制 skill 代码后再执行”的行为。
3. skill 可以被检索、注入、保留在 prompt 中，但还没有形成稳定的使用习惯。
4. folder 形态本身没有自动解决 callable 问题。
5. harmful skill 已能被 macro 最终过滤掉，但 pending skill 仍然很多，active 仍为 0。
6. 下一步应该继续做 case study，再决定是否进入更大规模的 `50/50` 和 TRL 设计。

## 后续动作

- 先基于这轮结果做 case study，解释为什么仍然没有 call。
- 再跑 `50/50`，继续盯日志。
- 如果还是不 work，再引入 TRL，并把 “not called” 作为重要负信号设计进去。
