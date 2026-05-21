# 完整算法实现后的实验与代码改进追踪

本文档从 `83b6aaf full algo` 之后开始记录。目的不是替代论文结果表，而是给每次工程/算法改动留下可追溯链路：为什么改、改了什么、验证了什么、实验结果如何、对应 git commit 是哪一个。

## 固定实验协议

从 2026-05-18 起，所有主实验必须遵守以下固定设置。除非某次实验明确标为 ablation/diagnostic，否则这些参数不应变化；如果变化，必须在对应结果行和时序记录中显式说明。

### BFCL 主实验固定项

| 项 | 固定值 |
|---|---|
| benchmark | BFCL v3 related-task setting |
| data_source | `bfcl_eval_bundle` |
| manifest | `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`，或显式标注的 `curated_related_manifest_150_50.json` |
| 50/50 train ids hash | `5d8d5179e3536f32` |
| 50/50 test ids hash | `0ab3f3f8d6572175` |
| train/test overlap | 0 |
| split method | deterministic relatedness ranking，manifest 文件落盘后冻结 |
| train order | manifest 中 `train_task_ids` 顺序；不 shuffle |
| test order | manifest 中 `test_task_ids` 顺序；并发执行后结果必须按 manifest order 汇总 |
| model | `claude-sonnet-4-5` |
| llm_config | `local_claude_proxy` |
| epochs | 1 |
| micro_maintenance_step | 1 |
| macro_maintenance_step | 10 |
| candidate_competition_enabled | false，除非做 TRL competition ablation |
| candidate_trial_retrieval | true |
| skill_injection_mode | `prompt_only`，除非做 callable/tool ablation |
| top_k_skills | 2 |
| min_skill_score | 0.0 |
| max_steps_per_turn | 主实验固定为 20；旧 cost retest 中 12 只作为 diagnostic |
| max_task_seconds | 180 |
| train_window_concurrency | 4 |
| micro_concurrency | 4 |
| test_concurrency | 4 |
| cache_input pricing | 统计 input/cache-input/output，但当前 cache input 为 0 |

重要澄清：BFCL 已经有固定 train/test set。`curated_related_manifest_50_50.json` 的 `train_task_ids` 和 `test_task_ids` 是固定列表，主 evolve 路径通过 `_tasks_from_manifest()` 按 manifest 顺序加载，没有随机 shuffle。此前总表中 `cost_retest_bfcl_*` 的 0.22 exact success 是 test-only cost diagnostic，未绑定 curated heldout 50；该 50-task set 混入了 17 个 curated train task、10 个 curated test task、23 个 manifest 外 task，因此不能作为主 heldout exact success 与最新 0.08 横比。

### Spreadsheet 主实验固定项

| 项 | 固定值 |
|---|---|
| benchmark | SpreadsheetBench verified/local fixture setting |
| split | 使用落盘 task id list；不得临时随机抽样后写成主结果 |
| train ids file | `academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json` |
| test ids file | `academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json` |
| train ids hash | `04c5121288e0e687` |
| test ids hash | `a309d0fe247a8d63` |
| train/test overlap | 0 |
| model | `claude-sonnet-4-5` |
| llm_config | `local_claude_proxy` |
| skill modes | baseline / full skill / compact callable 必须分开标注 |
| cost fields | success、avg_score、avg_total_tokens、avg_input_tokens、avg_output_tokens、executor/maintenance split |

### 结果分类规则

- **Main result**：固定 manifest、固定参数、完整 train/test protocol。
- **Frozen-store retest**：同一 manifest test set，使用已有 skill store 重测；可与 main result 比较 test，但不能替代 train 指标。
- **Cost ablation**：用于比较 prompt/injector/cost；只有 task set 与主 manifest 完全一致时，才能进入主结果对比。
- **Diagnostic/mixed-set**：任何混合 train/test/manifest 外任务的结果只能用于 debug，不得用于论文主表的 exact success 横比。

## 2026-05-17 16:33 `f440594 paper method update`

**动机。** 完整算法实现后，论文方法部分仍混合了旧 Python 伪代码、中文/英文叙述和实现细节，难以对应到算法主线。

**具体改进。**
- 在 `academic/paper/paper_new.md` 中保留原 Python 伪代码，新增标准伪代码。
- 将中文方法论和英文方法论拆成两个独立部分，不再交织。
- 按流程划分、skill 属性划分、反馈信号链路划分来解释方法。

**验证/结果。**
- 文档改动，无 benchmark 重跑。
- 后续展示文档 `PRESENTATION_20260518.md` 继续基于该结构展开。

## 2026-05-17 21:20 `c036a3c Add concurrent skill maintenance locks`

**动机。** 50/50 和 150/50 BFCL evolve 运行显示 bundle replay 与 micro maintenance 成为主要耗时。完全串行训练可以保证算法忠实，但端到端时间过长；用户提出 macro window 内可以并发，window 结束再做 macro。

**具体改进。**
- 引入 benchmark-agnostic evolution/maintenance adapter 路径，开始把 BFCL 下的算法抽到 core/generic 层。
- 增加 concurrent maintenance locks：micro/refine 可在任务之间并发，但写 skill store 时受锁保护。
- 将 `micro_concurrency` / test concurrency 等运行参数接入 runner。
- `.gitignore` 补充实验 log/output 排除规则，避免结果文件污染 git。
- 增加并发相关 mock 测试，覆盖有 sleep 的 LLM mock、依赖修改、15 并发压力场景。

**验证/结果。**
- 高并发 mock 测试显示细粒度锁相比粗锁/串行能缩短 wall-clock。
- 当时线上 BFCL 实验发现“粗锁并发”端到端加速有限，瓶颈仍在 LLM 调用与 bundle replay。

## 2026-05-17 21:43 `4c5aae8 Narrow micro maintenance write locks`

**动机。** 进一步分析发现 micro refine 只需要对目标 skill 加写锁；依赖 skill 只需要读锁，依赖变化可以标 stale，不必全局阻塞。

**具体改进。**
- 收窄 micro maintenance 的写锁范围。
- 目标 skill 使用写锁，依赖/邻域读取使用读锁。
- 若 refine 前后依赖变化，标记 stale，而不是在同一临界区强行级联更新。
- 增加测试验证并发 micro 不互相覆盖，并能在依赖变化时稳定落库。

**验证/结果。**
- mock 并发测试通过。
- 设计结论：relation graph 本质可以短锁更新；长耗时 LLM/refine 不应持有图写锁。

## 2026-05-17 21:57 `7baf000 Add gated candidate competition feedback`

**动机。** 用户指出算法里“同一 group 多个 skill 竞争、根据一段时间后的使用情况给 TRL 反馈”的链路尚未完整实现。需要为同组 skill 建立可比较关系，但不能影响正在跑的主实验。

**具体改进。**
- 增加 candidate/group feedback 数据结构与开关。
- 将同一候选组 skill 的使用、成功、失败与 credit 聚合为比较信号。
- 默认 gated，不影响已有 resume 路径。
- 添加测试覆盖候选组生成、低使用量不误判、高使用量才产生对比反馈。

**验证/结果。**
- 单元测试通过。
- 后续用户决定 competition 属于 TRL，短期先不作为主实验变量展开。

## 2026-05-17 22:06 `ee2497d Move candidate group feedback to macro windows`

**动机。** 对比反馈不应每个 task 都生成，否则噪声太大。用户要求在 macro 时生成，只对达到一定使用量的 group 做比较；连续多个 macro 低复用的 group 也能产生“低复用性反馈”。

**具体改进。**
- 将 candidate group feedback 从 task/micro 级移动到 macro window。
- 增加 window-level usage threshold。
- 对连续 N 个 macro window 低使用的 group 生成 low-reuse feedback，而不是误判为完全无用。

**验证/结果。**
- macro window feedback 测试通过。
- 该链路目前仍作为 TRL 预备信号，主实验没有打开为核心变量。

## 2026-05-17 22:09 `81c6b77 Clarify low-usage candidate feedback`

**动机。** 用户澄清“不是完全没有使用量，而是没有达到足够使用量才生成低复用性反馈”。需要避免把低样本 skill 错误归因成 harmful。

**具体改进。**
- 明确 low-usage feedback 的语义：低复用/低证据，而不是负向质量判断。
- 调整测试断言，区分 comparative feedback 与 low-reuse feedback。

**验证/结果。**
- 相关 BFCL experiment 测试通过。

## 2026-05-18 19:46 `bde0b30 speedup`

**动机。** 两个问题同时出现：
1. token/cost 口径不够细，无法回答 skill overhead 来自 executor、injector、input、cache input 还是 output。
2. Spreadsheet compact 模式因为没有给 function skill 内容，性能接近 baseline；用户要求 function skill 对所有 bench 可调用，knowledge/workflow 仍 prompt 注入。

**具体改进。**
- 新增 `academic/benchmarks/core/cost_accounting.py`，记录 input/cache-input/output、role、phase、skill prompt chars、tool schema chars、final conversation chars。
- 新增 `academic/benchmarks/core/skill_injector.py`，实现 deterministic compact/budget injector。
- BFCL：
  - function skill 可作为 `skill__<name>` 暴露。
  - composite function skill 展开为 raw BFCL tool calls，最终评分仍按 raw tool calls。
  - 保持 knowledge/workflow prompt 注入。
- Spreadsheet：
  - 生成 `skill_library.py`，允许 executor `from skill_library import skill_name` 后直接调用 function skill。
  - 兼容旧 evolve store：`kind=executable_tool` 即使 metadata 错写为 `informational`，也可作为 callable。
  - workflow/knowledge/interface card 不导出 callable。
  - 修复官方 answer range 解析，例如 `'Sheet1!'A1:A50,'Sheet2!'A1:E20,'Sheet3!'A1:A50'`。
  - callable snippet wrapper 注入 `wb/ws`、`**kwargs`，支持列字母转列号和常见别名。
- SkillsBench：
  - 增加初步 adapter/test scaffold，用于后续验证“官方 skill vs evolved skill”。
- 文档：
  - 生成 BFCL/Spreadsheet fine-grained cost retest、token overhead case study、runtime cost accounting/injector 分析等文档。

**验证。**
- `pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py`：11 passed。
- `pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_bfcl_function_skill_expands_to_raw_tool_calls`：1 passed。
- Spreadsheet callable normalized store：`academic/results/spreadsheet_evolve_50_50_true_20260518_022120_callable_normalized_skills.json`。

**实验结果。**

Spreadsheet 50-test：

| setting | success | success_rate | avg_score | avg_total_tokens | avg_input | avg_output | elapsed_s |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 13/50 | 0.26 | 0.3189 | 1621.3 | 747.9 | 873.5 | 187.456 |
| full skill | 18/50 | 0.36 | 0.4501 | 3816.3 | 3006.0 | 810.4 | 189.409 |
| compact skill | 12/50 | 0.24 | 0.3186 | 1873.2 | 1038.7 | 834.5 | 194.580 |
| compact callable v2 | 14/50 | 0.28 | 0.3385 | 2010.4 | 1126.9 | 883.5 | 214.061 |

Spreadsheet 结论：
- full skill 当前效果最好，但 token 显著更高。
- compact callable v2 修复了旧 compact 的一部分问题，超过 baseline 与旧 compact，但仍远低于 full skill。
- v2 中 18 条 trace 有 callable skill 可用，但模型几乎没有真正 import `skill_library`，更多是把 callable map 当成更详细提示后重写代码。
- 这说明现有 Spreadsheet skill 还不是“函数优先”的训练产物；后续需要在 extractor/refiner 阶段直接生成稳定函数签名、参数说明和调用示例，而不是事后包装自由文本 snippet。

BFCL 50-test：

| setting | success/pass_at_k | official_valid_rate | avg_score | avg_call_recall | avg_call_precision | avg_total_tokens | avg_input | avg_output | elapsed_s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0.18 | 0.4898 | 0.7339 | 0.7958 | 0.6983 | 63632.5 | 62672.9 | 959.6 | 1841.873 |
| full skill | 0.22 | 0.6600 | 0.7968 | 0.8766 | 0.7493 | 74711.6 | 73700.7 | 1010.9 | 1691.405 |
| compact skill | 0.22 | 0.6400 | 0.7937 | 0.8666 | 0.7511 | 71810.7 | 70812.6 | 998.1 | 1727.631 |

BFCL 结论：
- full/compact skill 都提升了 avg_score 与 success_rate，但 BFCL baseline 的 tool schema/input 已经非常大。
- token overhead 主要来自 input/tool context 和 skill context，不是 completion。
- BFCL callable function skill 中间层已通过单元测试；当前 learned BFCL store 中可展开的 composite function skill 仍少，因此更多影响未来训练产物。

## 未单独提交但需要保留的中间实验结果

这些结果发生在多次代码修改之间，有些当时用于诊断，没有各自对应的独立 git commit。它们仍然应保留在实验链路中，因为它们解释了后续为什么继续改 injector、callable、parser 和 cost 口径。

### 2026-05-17 23:32 BFCL 50/50 guardfix baseline

文件：`academic/results/claude_proxy_related50_50_guardfix_20260517_232531_baseline.json`

| metric | value |
|---|---:|
| success/pass_at_k | 0.06 |
| official_valid_rate | 0.44 |
| avg_score | 0.7312 |
| avg_call_recall | 0.8099 |
| avg_call_precision | 0.6946 |
| avg_total_tokens | 70323.8 |

解释：这是用户记得的“baseline valid rate 0.4 左右”的来源。这里的 0.44 是 official valid/pass 口径，不是 avg_score。

### 2026-05-18 00:30 BFCL evolve train/test 诊断结果

文件：`academic/results/claude_proxy_related50_50_guardfix_20260517_232531_evolve.json`

Train round：

| metric | value |
|---|---:|
| success/pass_at_k | 0.24 |
| official_valid_rate | 0.62 |
| avg_score | 0.8262 |
| avg_call_recall | 0.8794 |
| avg_call_precision | 0.7962 |
| avg_total_tokens | 62468.9 |

Heldout test：

| metric | value |
|---|---:|
| success/pass_at_k | 0.02 |
| official_valid_rate | 0.80 |
| avg_score | 0.3314 |
| avg_call_recall | 0.3718 |
| avg_call_precision | 0.3018 |
| avg_total_tokens | 41788.7 |

解释：这个结果暴露了早期 evolve test 的异常：official_valid_rate 高，但 avg_score/call recall 很低，说明“官方 valid”不能单独作为质量指标，必须同时报 strict success、avg_score、call recall/precision。后续我们才强化了 case study、guardfix、injector 和细粒度 cost 统计。

### 2026-05-18 01:29 使用训练后 store 重测 BFCL test50

文件：`academic/results/bfcl_guardfix_trainedstore_test50_rerun2_20260518_012134.json`

| metric | value |
|---|---:|
| success/pass_at_k | 0.08 |
| official_valid_rate | 0.74 |
| avg_score | 0.7991 |
| avg_call_recall | 0.8987 |
| avg_call_precision | 0.7314 |
| avg_turn_success_rate | 0.5555 |
| avg_relaxed_turn_success_rate | 0.8620 |
| avg_total_tokens | 86813.3 |
| elapsed_s | 446.895 |

解释：这个中间结果说明 learned store 对 call recall/avg_score 有帮助，但 strict success 仍低，并且 token 开销明显偏高。它直接推动了后续 compact injector、cost accounting 和 function skill callable 方向。

### 2026-05-18 16:50 Spreadsheet cost retest

文件：
- `academic/results/cost_retest_sheet_baseline_20260518.json`
- `academic/results/cost_retest_sheet_fullskill_20260518.json`
- `academic/results/cost_retest_sheet_compact_20260518.json`

| setting | success | success_rate | avg_score | avg_total_tokens | avg_input | avg_output |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 13/50 | 0.26 | 0.3189 | 1621.3 | 747.9 | 873.5 |
| full skill | 18/50 | 0.36 | 0.4501 | 3816.3 | 3006.0 | 810.4 |
| compact skill | 12/50 | 0.24 | 0.3186 | 1873.2 | 1038.7 | 834.5 |

解释：full skill 最好，但主要靠输入侧塞完整代码。compact 省 token 后效果掉回 baseline，说明 function/code skill 不能只做摘要，需要可调用机制或更高 utility 的 contract。

### 2026-05-18 19:38 Spreadsheet compact callable v1

文件：`academic/results/cost_retest_sheet_compact_callable_20260518.json`

| metric | value |
|---|---:|
| success_rate | 0.22 |
| avg_score | 0.2895 |
| avg_total_tokens | 1925.8 |
| avg_input_tokens | 1072.3 |
| avg_output_tokens | 853.5 |
| callable available traces | 7 |
| actual `skill_library` imports | 1 |

解释：v1 证明旧 evolved store 里确实有 executable skill 被错误标成 informational，但事后 callable 化不够；一个真实 import case 还因为 kwargs 没注入到 snippet 局部环境而失败。这推动了 v2 的 parser/wrapper/callable-map 修复。

### 2026-05-18 19:45 Spreadsheet compact callable v2

文件：`academic/results/cost_retest_sheet_compact_callable_v2_20260518.json`

| metric | value |
|---|---:|
| success_rate | 0.28 |
| avg_score | 0.3385 |
| avg_total_tokens | 2010.4 |
| avg_input_tokens | 1126.9 |
| avg_output_tokens | 883.5 |
| callable available traces | 18 |
| actual `skill_library` imports | 0 |

解释：v2 修复了官方 answer range parser 和 snippet wrapper，并恢复到略高于 baseline/old compact；但模型仍主要参考 callable map 重写代码，而不是 import。这说明后续必须在 extractor/refiner 阶段生成真正“函数优先”的 skill，而不是事后包装自由文本代码片段。

## 指标口径说明

- `success_rate` / `pass_at_k`：最严格的 task-level pass。
- `official_valid_rate` / `official_pass_at_k`：BFCL official runner 的 valid/pass 口径，可能与 strict success 不一致。
- `avg_score`：连续质量分数，通常更接近 call-level F1/partial credit。
- `avg_call_recall` / `avg_call_precision`：工具调用覆盖和精度，对 BFCL 诊断尤其重要。
- `avg_total_tokens = input + cache_input + output`。当前这些实验 cache input 为 0。

因此，baseline 并没有从 0.4 valid 变成 0.7 valid；`0.7339` 是 cost retest baseline 的 `avg_score`，同一结果里的 `official_valid_rate` 是 `0.4898`。

## 当前可追溯结论

1. **full skill 的收益是真实的，但代价主要是输入侧。** Spreadsheet full skill 比 baseline 平均多约 2195 tokens/task，其中 input 多约 2258 tokens/task，output 反而少约 63 tokens/task。也就是说主要不是模型“多生成”，而是完整 skill context 进了 prompt。
2. **compact 需要保留高 utility 信息。** 旧 compact 去掉了太多 function/code 内容，Spreadsheet 性能掉回 baseline；callable v2 恢复一部分，但还不够。
3. **function skill 应作为可调用资产训练出来。** 事后把自由文本 snippet 包装成函数只能解决兼容问题，不能保证模型会 import 或传对参数。
4. **BFCL 的主要 token 背景不同。** BFCL 的 raw tool schema/tool context 很大，skill overhead 相对 Spreadsheet 更容易被 adapter/tool schema 淹没；因此 BFCL 更需要 tool schema pruning、retrieval filtering 和 executable composite skill。

## 中期目标

- 在每次 macro maintenance 后保存 skill store snapshot。该开销主要是磁盘，不是 LLM：当前 BFCL 43 个 skills 的 final store 约 1.9MB；150 train、macro step 10 时约 15 个 snapshot，预计几十 MB 量级，可接受。真正昂贵的是“每个 snapshot 都跑完整 heldout test”。
- 增加 `macro_skill_snapshots[]` 元数据：window index、task range、n_active/n_pending/n_disabled、changed skill names、snapshot path、store hash。
- 增加 periodic heldout probe：每个 macro 或每 2-3 个 macro 对固定 5-10 个 sentinel test task 测一次；完整 50 test 只在关键 macro、最终 store、或 ablation 需要时跑。
- 将 extractor/refiner 的 function skill schema 标准化：稳定函数名、参数表、适用条件、调用示例、非适用条件。
- 让 test-time injector 按 utility 压缩：优先 callable import map + contract，少给完整历史代码。
- 对训练阶段也接入同样的 injector，验证是否降低 token 且不损害 skill extraction/credit。
- 对 BFCL 继续增强 composite function skill：将常见 raw tool call 序列学习成可展开 function skill，并在 scoring 前展开为 raw calls。
- 将结果表按 cost 口径固定：accuracy/score、input/cache-input/output、executor/injector、correct-only cost、utility per million tokens。
- 重新跑 BFCL/Spreadsheet 的小规模和 50/50 对照，分清 prompt-only skill、compact skill、callable skill 的贡献。
- 设计 Spreadsheet multi-turn setting：把单个 SpreadsheetBench 指令拆成 inspect/plan/execute/verify 修复多轮，保留同一 workbook state，使 workflow skill、credit assigner 和运行时反馈更有发挥空间。

## 远期目标

- 扩展到更多能体现运行时反馈和复用价值的 benchmark：SkillsBench、Spreadsheet 多轮/函数调用版本、以及更短时长的 tool-use/runtime-feedback benchmark。
- 将 competition/TRL feedback 正式纳入主链路：同组 skill 竞争、macro window 聚合、低复用反馈、候选生成器更新。
- 形成 benchmark-agnostic 方法包：同一套 skill artifact、credit、bundle、micro/macro、injector、cost accounting 可落到 BFCL/Spreadsheet/SkillsBench。
- 建立论文级稳定评测协议：固定 manifest、固定 cost 价格表、固定 cache 计费规则、固定 token 统计口径、固定 skill prompt 是否计入的报告方式。
- 从“prompt 中复用文字”推进到“软件工程资产复用”：function skill 可直接调用，workflow/knowledge skill 用压缩 contract 注入，维护测试保护 skill API。

## 2026-05-18 20:13 `3e162c1 Add spreadsheet notebook execution mode`

**动机。** SpreadsheetBench 原始 setting 基本是 single-turn code generation。用户提出需要一个更像 notebook/Jupyter 的多轮执行环境：模型可以写代码、看到执行结果或报错、保留前序变量，最多多轮修复，直到输出结束 pattern。这能更好暴露运行时反馈、workflow skill 和 error-repair skill 的价值。

**具体改进。**
- 增加 Spreadsheet notebook execution mode。
- 每个 task 保留一个 Python execution state，后续代码可以复用前序变量。
- 支持最多 5 轮代码执行，模型通过特殊结束 pattern 主动结束。
- 结果 trace 记录每轮代码、stdout/stderr、异常和最终 answer。

**验证/结果。**
- `academic/results/spreadsheet_notebook_smoke_1_20260518.json`：1 task，success 0/1，avg_score 0.0，avg_total_tokens 3879.0。
- `academic/results/spreadsheet_notebook_hard_55427_20260518.json`：hard case，success 0/1，avg_score 0.0，avg_total_tokens 6950.0。
- smoke 证明多轮状态和执行回传链路可以跑通；效果还不稳定，主要问题是模型没有稳定复用前序变量与 intermediate dataframe。

## 2026-05-18 20:17 `b3b44fc Fix spreadsheet notebook relative workdir`

**动机。** notebook executor 初版在相对路径和 workbook 文件定位上容易失败，导致不是算法能力问题而是运行环境问题。

**具体改进。**
- 修复 Spreadsheet notebook 相对工作目录。
- 确保 workbook、临时脚本和执行 state 位于同一可解析上下文。
- hard direct case 增加回归结果。

**验证/结果。**
- `academic/results/spreadsheet_notebook_hard_55427_direct_20260518.json`：1 task，仍未严格通过，但相对路径问题消失。
- `academic/results/spreadsheet_notebook_hard_55427_direct_v2_20260518.json`：继续验证 direct execution wrapper。

## 2026-05-18 20:31 `a923943 Add macro skill snapshots for evolution runs`

**动机。** 用户要求每次 macro maintenance 后把 skill store 记下来，并在之后可以拿某个 macro checkpoint 去做 heldout probe。这样能追踪 skill 是何时出现、何时被 refine/revoke、何时开始影响 test。

**具体改进。**
- evolution run 增加 macro skill snapshot 元数据。
- snapshot 记录 window index、task range、active/pending/disabled/archived skill 数量、changed skills 和 snapshot path。
- 设计上只保存 store JSON，不在每个 macro 后自动跑完整 50-test，避免把开销乘以 macro 数。

**验证/结果。**
- 该改动是追踪能力增强，不改变 BFCL 训练决策本身。
- 预期磁盘开销可接受：当前 50-train BFCL final skill store 约 2.5MB；150-train、macro step 10 保存 15 个 snapshot 也只是几十 MB 量级。

## 2026-05-18 20:40 `c4cd7d2 Add resumable macro snapshots for training runs`

**动机。** 150/50 和 50/50 长实验容易被网络或手动停止打断；仅 final result 不够，需要能从最近 macro 后恢复，并保留当时 skill 状态。

**具体改进。**
- training run 增加 resumable macro checkpoint/snapshot。
- checkpoint 包含训练进度、store snapshot、maintenance window 信息和 resume 所需配置。
- 与前面的 macro skill snapshot 区分：一个面向分析，一个面向恢复。

**验证/结果。**
- 当前最新 BFCL 50/50 run 生成了：
  - `academic/results/bfcl_train50_20260518_202840_checkpoint.json`
  - `academic/results/bfcl_train50_20260518_202840_skills.json`
  - `academic/results/bfcl_train50_20260518_202840.json`

## 2026-05-18 21:05 `46367fe Add SkillsBench diagnostic baseline adapter`

**动机。** 用户希望引入更贴近 skill-use 的 benchmark。SkillsBench 本质上给定 skill 后测试模型能否应用 skill，适合做“官方 skill vs evolved skill”的诊断，但它不是在线抽取-维护 benchmark，因此先接入 baseline/fixture 路径。

**具体改进。**
- 增加 SkillsBench diagnostic adapter。
- 支持 mock smoke、fixture baseline、curated mock diagnostic。
- 文档中明确 SkillsBench 的定位：更适合测 skill package 使用能力和 prompt overhead，不直接等同于在线 skill evolving。

**验证/结果。**
- `academic/results/skillsbench_mock_smoke_20260518.json`：4 tasks，success_rate 1.0，avg_score 1.0，avg_total_tokens 338.8。
- `academic/results/skillsbench_baseline_fixture3_20260518.json`：3 tasks，success_rate 1.0，avg_score 1.0，avg_total_tokens 702.3。
- `academic/results/skillsbench_curated_mock_diag_20260518.json`：5 tasks，success_rate 0.2，avg_score 0.3，avg_total_tokens 346.4。

## 2026-05-18 21:07 BFCL 50/50 最新完整 run：`bfcl_train50_20260518_202840`

**动机。** 在 cost accounting、injector、function callable、macro snapshot、resume checkpoint 等改动之后，用户要求重跑 50/50，观察是否还存在之前 pending/refiner/injector 不工作的现象，并比较这次与上次有记录实验的差异。

**运行设置。**
- 文件：`academic/results/bfcl_train50_20260518_202840.json`
- skill store：`academic/results/bfcl_train50_20260518_202840_skills.json`
- checkpoint：`academic/results/bfcl_train50_20260518_202840_checkpoint.json`
- manifest：`academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`
- mode：`related_task_evolve`
- model：`claude-sonnet-4-5`
- llm_config：`local_claude_proxy`
- epochs：1
- micro_maintenance_step：1
- macro_maintenance_step：10
- train_window_concurrency：4
- micro_concurrency：4
- test_concurrency：4
- top_k_skills：2
- skill_injection_mode：`prompt_only`
- candidate_trial_retrieval：true
- candidate_competition_enabled：false
- max_steps_per_turn：20
- max_task_seconds：180

**Train 50 结果。**

| metric | value |
|---|---:|
| success/pass_at_k | 0.20 |
| official_valid_rate | 0.58 |
| avg_score | 0.8090 |
| avg_call_recall | 0.8789 |
| avg_call_precision | 0.7697 |
| avg_turn_success_rate | 0.5687 |
| avg_relaxed_turn_success_rate | 0.7917 |
| avg_total_tokens | 62339.2 |
| avg_input_tokens | 61247.4 |
| avg_output_tokens | 1091.8 |
| avg_model_steps | 9.30 |
| timeout_rate | 0.0 |

**Heldout Test 50 结果。**

| metric | value |
|---|---:|
| success/pass_at_k | 0.08 |
| official_valid_rate | 0.70 |
| avg_score | 0.7892 |
| avg_call_recall | 0.8955 |
| avg_call_precision | 0.7193 |
| avg_turn_success_rate | 0.5421 |
| avg_relaxed_turn_success_rate | 0.8367 |
| avg_total_tokens | 83043.9 |
| avg_input_tokens | 81956.2 |
| avg_output_tokens | 1087.8 |
| avg_model_steps | 10.18 |
| timeout_rate | 0.0 |

**Skill 状态。**
- final skills：46 个。
- active：4 个。
- disabled：1 个。
- archived：41 个。
- kind 分布：workflow_guardrail_card 21，interface_contract_card 20，atomic_tool_rule_card 5。
- 顶层 `pending_skill_summary.n_pending=41` 是 stale summary：它记录 macro revoke/filter 前的 pending 名单；final skill store 中这些已经是 `archived`，不是仍在 active promotion queue。

**Credit 与维护。**
- skill_credit_events：22 条，harmful 11，helpful 6，neutral 5。
- micro_maintenance_reports：50 条，每个 train task 一个 micro 报告。
- maintenance_windows：5 个，macro step=10。
- extraction_events：46。
- credit_events：22。
- maintenance_test_results：17。
- refine_decisions：23。
- pending_skill_revocations：41。
- pending_skill_promotions：0。
- skill_credit_filter_decisions：3：
  - `engine_start_brake_pedal_prerequisite`：harmful_count 8，negative_margin 8，disabled。
  - `buy_stock_at_current_market_price`：negative_margin 1，kept。
  - `tradingbot_direct_ticker_binding`：helpful_count 6，harmful_count 2，negative_margin -4，kept。

**Test skill 注入统计。**
- `vehicle_brake_pedal_full_press_for_engine_start`：14 次。
- `buy_stock_at_current_market_price`：13 次。
- `tradingbot_direct_ticker_binding`：13 次。
- `contact_customer_support_message_brevity_rule`：12 次。
- 当前 test 是 prompt-only skill 注入；`used_counts` 和 `called_skill_tool_counts` 为空，因为这些不是 callable function skill。

**与上次有记录 BFCL evolve 的最重要差异。**
- 上次文件：`academic/results/claude_proxy_related50_50_guardfix_20260517_232531_evolve.json`。
- 上次 heldout test 有 timeout_rate 0.60，导致 avg_score/call recall 异常低；这次 timeout_rate 0.0，所以 test 指标更可信。
- 这次 strict success 从 0.02 提到 0.08，avg_score 从 0.3314 提到 0.7892，avg_call_recall 从 0.3718 提到 0.8955。
- official_valid_rate 从 0.80 降到 0.70，但上次 official_valid_rate 与 avg_score/call recall 严重不一致，因此不能单独解释为更好。
- 训练侧略低：train success 从 0.24 降到 0.20，avg_score 从 0.8262 降到 0.8090，属于小幅波动和 skill set 变化。
- token 变高：test avg_total_tokens 从 41788.7 到 83043.9；主要原因是上次 heldout 60% timeout/异常短路，且当时没有完整 input/output 细分。这次完整执行后 token 更接近真实开销。

**代表 case 对比。**
- `multi_turn_base_191`：上次 test score 0.0，这次 1.0 且 strict success。注入 `contact_customer_support_message_brevity_rule`。
- `multi_turn_base_192`：上次 0.0，这次 0.9231。注入 `contact_customer_support_message_brevity_rule`。
- `multi_turn_base_141`：上次 0.0，这次 0.9231。注入 `tradingbot_direct_ticker_binding` 与 `buy_stock_at_current_market_price`。
- `multi_turn_base_77`：上次 0.0，这次 0.9091。注入 `vehicle_brake_pedal_full_press_for_engine_start`。
- 小幅 regression：`multi_turn_base_65` 从 0.90 降到 0.75，`multi_turn_base_152` 从 0.8333 降到 0.7273，均没有 strict success 变化。

更完整分析见 `academic/results/algorithm_docs/BFCL_LATEST_VS_PREVIOUS_COMPARISON_20260518.md`。

## 2026-05-18 21:09 Spreadsheet notebook train50 中止诊断

**动机。** 用户要求在 BFCL 期间尝试 Spreadsheet notebook 训练，但观察到非常慢，需要判断慢在哪里，并停止后讨论省开销方案。

**运行状态。**
- 训练 run：`sheet_nb_train50_20260518_202840`。
- 已停止；tmux/process 不再存在。
- 日志：`academic/results/spreadsheet_notebook_train50_20260518_202840.log`。

**耗时诊断。**
- `spreadsheet_extractor`：19 calls，累计约 625.9s，平均 32.94s，平均 user chars 10352，最大 15943，累计 tokens 116918。
- `spreadsheet_credit_assigner`：9 calls，累计约 346.2s，平均 38.47s，平均 user chars 24725，最大 31159，累计 tokens 100292。
- `refiner`：41 calls，累计约 340.9s，平均 8.31s，user chars 固定约 14015，累计 tokens 241470。

**结论。**
- notebook executor 本身不是最大瓶颈；维护链路才是主要瓶颈。
- Spreadsheet generic runner 当前仍是串行 train：task -> credit -> bundle -> micro -> macro。
- BFCL 的细粒度锁/并发还没有完整下沉到 generic runner。
- credit prompt 当前包含过多 retrieved skill/candidate projection，不是只看 injected/called skill。
- helpful credit 也可能触发 bundle test/refine，导致维护次数偏高。

详细改进计划见本文档后续“Spreadsheet notebook 近期改进计划”，以及 `academic/results/algorithm_docs/SPREADSHEET_NOTEBOOK_IMPROVEMENT_PLAN_20260518.md`。

## Spreadsheet notebook 近期改进计划

目标不是立刻追求 full 50/50 分数，而是先把 notebook setting 的维护开销降下来，并确保它真正测到“运行时反馈与可复用 skill”。

1. **Credit 输入收窄。** Spreadsheet credit assigner 只看 injected/called skills。workflow/knowledge skill 只要被注入即可视为 exposed；function skill 需要出现 import/call 才视为 direct_use。不要把每步 retrieved skill 全量塞进 credit prompt。
2. **Micro target 收窄。** strong harmful、filter_candidate 或明确 `refine_required` 才触发 pre-refine。helpful credit 默认只记录 evidence 和 positive case，不立即 test/refine。
3. **Bundle replay 分级。** 先做 strict/static gate：fragment 是否 replayable、xlsx/range/golden 是否齐全、callable 代码是否可 parse/import、prompt budget 是否超限。strict gate 过了以后先跑 with-skill；只有 with-skill 通过，才跑 without-skill 做 utility 对比。
4. **减少 without 开销。** with-skill failed 时直接 refine 或 keep evidence，不再跑 without，因为 without 不能解释 skill 是否可用。
5. **训练期 case cap 更紧。** Spreadsheet 训练阶段每个 skill 最多保留 1-2 个 active bundle cases；更多 case 只进入 evidence ledger，由 macro 再决定是否升格。
6. **Skill 投影压缩。** credit/refiner 使用 `compact_skill_prompt_block` 一类结构化 card，不再拼 `body[:1800] + metadata + evidence`。
7. **Function skill 生成约束。** extractor/refiner prompt 要要求 body <= 500 chars、applicability <= 120 chars、code <= 40 lines、明确参数表、返回值、调用示例、非适用条件。
8. **Notebook executor 信号更结构化。** 每轮记录 code cell、stdout、stderr、exception type、changed variables、answer candidate；credit/refiner 只消费这份 projection。
9. **Generic runner 并发。** 将 BFCL 的细粒度锁、micro_concurrency、window 内并发下沉到 benchmark-agnostic runner，使 Spreadsheet 不再 task-by-task 全串行。
10. **Probe 协议。** 每个 macro 后只跑 5-10 个固定 sentinel tasks，不跑完整 heldout；完整 50-test 仅在 final 或关键 ablation 跑。

## Spreadsheet notebook 中期计划

- 把单 turn SpreadsheetBench 改造成 inspect -> code -> observe -> repair -> final 的多轮协议。
- 强制 notebook function skill 可 import/call，workflow/knowledge skill 只以短 contract 注入。
- 从训练阶段开始生成真正可调用的 function skill，而不是事后把自由文本 snippet 包装成函数。
- 设计对照：single-turn baseline、notebook baseline、notebook with full skill、notebook compact callable、notebook evolved callable。
- 与 BFCL 共用同一套 cost accounting：input/cache-input/output、executor/injector/maintenance、correct-only cost、score per million tokens。

## 2026-05-19 Spreadsheet notebook 50/50 speedup 主实验

**动机。** Spreadsheet notebook 训练在 2026-05-18 的中止诊断中显示维护链路过慢。用户要求保持较高并发，跑真实 50/50，并在 timeout 时不要重头跑：task timeout/LLM timeout 应转成失败继续，且每个 task 状态必须可恢复。

**代码改进。**
- Generic `OnlineSkillEvolutionRunner` 增加 partial/resume：记录 `train_details`、`test_details`、`skill_credit_events`、micro/macro reports、skill store、maintenance token events 和每个 task 的 `task_state`。
- Window resume 不是粗略跳过整个 window：若 rollout 已完成但 micro/macro 未完成，会继续补齐缺失阶段。
- Task-level timeout/exception 转成失败 `BenchmarkResult`，不再让 `asyncio.gather` 杀掉整轮。
- Spreadsheet evolve CLI 将 `--max-task-seconds=180` 传给每次 LLM request 的 `max_request_wall_s`，并对 Spreadsheet 关闭外层 task wall timeout。
- 输出 `token_request_curve` 和 `observed_wall_clock_s`；后者用于恢复式运行的完整耗时估计。

**验证。**

```bash
pytest -q \
  academic/benchmarks/tests/test_generic_evolution.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`34 passed`，仅有已有 pytest config / pydantic deprecation warnings。`py_compile` 与 `git diff --check` 通过。

**主实验命令。**

```bash
python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet --mode evolve \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --n-train 50 --n-test 50 \
  --n-train-runs 1 --n-runs 1 \
  --train-concurrency 4 \
  --micro-maintenance-concurrency 8 \
  --test-concurrency 4 \
  --top-k-skills 3 \
  --spreadsheet-execution-mode notebook \
  --spreadsheet-max-turns 5 \
  --max-task-seconds 180 \
  --tag 0519-speedup \
  --macro-snapshot-dir academic/results/macro_snapshots/spreadsheet_0519-speedup \
  --partial-output academic/results/spreadsheet_0519-speedup_partial.json \
  --output academic/results/spreadsheet_0519-speedup.json
```

**落盘文件。**
- 主结果：`academic/results/spreadsheet_0519-speedup.json`
- partial/resume：`academic/results/spreadsheet_0519-speedup_partial.json`
- run log：`academic/results/spreadsheet_0519-speedup_rerun.log`
- 早期失败 log：`academic/results/spreadsheet_0519-speedup.log`

**与上一轮 Spreadsheet 50/50 evolve 对比。**

上一轮文件：`academic/results/spreadsheet_evolve_50_50_true_20260518_022120.json`。

| metric | 2026-05-19 notebook speedup | 2026-05-18 evolve 50/50 | delta |
|---|---:|---:|---:|
| observed wall-clock / elapsed | 7740.0s | 8610.942s | -870.942s |
| speedup | 1.1125x | baseline | +11.25% |
| train success | 0.26 | 0.24 | +0.02 |
| train avg_score | 0.3176 | 0.2827 | +0.0349 |
| train avg_total_tokens | 13816.5 | 3460.0 | 3.99x |
| test success | 0.28 | 0.24 | +0.04 |
| test avg_score | 0.3350 | 0.3208 | +0.0142 |
| test avg_total_tokens | 13557.4 | 3748.2 | 3.62x |
| train task avg elapsed | 35.329s | 14.930s | 2.37x slower |
| test task avg elapsed | 35.408s | 14.945s | 2.37x slower |

注意：`spreadsheet_0519-speedup.json` 顶层 `elapsed_s=0.647` 是最后一次 resume 后重新汇总 summary 的进程耗时，不代表完整实验。完整耗时使用 `observed_wall_clock_s=7740.0`。

**0519 详细指标。**

Train：

| metric | value |
|---|---:|
| n_tasks / n_runs | 50 / 50 |
| n_success | 13 |
| success_rate | 0.26 |
| avg_score | 0.3176 |
| avg_total_tokens | 13816.52 |
| avg_input_tokens | 11694.96 |
| avg_output_tokens | 2121.56 |
| executor calls | 161 |
| executor total tokens | 690826 |
| avg notebook turns | 3.22 |
| max notebook turns | 5 |
| avg task elapsed | 35.329s |
| max task elapsed | 84.586s |

Heldout test：

| metric | value |
|---|---:|
| n_tasks / n_runs | 50 / 50 |
| n_success | 14 |
| success_rate | 0.28 |
| avg_score | 0.3350 |
| avg_total_tokens | 13557.36 |
| avg_input_tokens | 11594.82 |
| avg_output_tokens | 1962.54 |
| executor calls | 164 |
| executor total tokens | 677868 |
| avg notebook turns | 3.28 |
| max notebook turns | 5 |
| avg task elapsed | 35.408s |
| max task elapsed | 79.169s |

Maintenance：

| role | calls | total tokens | duration |
|---|---:|---:|---:|
| spreadsheet_extractor | 50 | 508734 | 1519.480s |
| spreadsheet_credit_assigner | 40 | 518795 | 1250.016s |
| refiner | 78 | 484003 | 1170.517s |
| total | 168 | 1511532 | 3940.013s |

Store / maintenance counts：

| metric | value |
|---|---:|
| final skills | 50 |
| skill_credit_events | 120 |
| micro_maintenance_reports | 50 |
| maintenance_windows | 5 |
| macro_skill_snapshots | 5 |
| token_request_curve events | 493 |

**结论。**
- 并发没有失效；真实 50/50 相比上一轮从 `8610.942s` 降到 `7740.0s`，约 `1.11x`。
- 效果略升：train success `+2pp`，test success `+4pp`，test avg_score `+0.0142`。
- 但 notebook 多轮显著增加 executor token 和单 task 耗时：train/test avg_total_tokens 分别是上一轮的 `3.99x` / `3.62x`。
- `micro_maintenance_concurrency=8` 没有显示为主要问题；最大 observed maintenance request duration 约 `70s`，没有持续 timeout 崩溃。
- 主要瓶颈是 maintenance 请求数量和 prompt/token 规模：168 次 maintenance LLM、1.51M maintenance tokens，抵消了 rollout 并发收益。

## 2026-05-19 Spreadsheet notebook baseline test50

**动机。** 用户询问 Spreadsheet notebook baseline 是否已经跑完并要求把结果加进主结果表。检查发现此前只有 1-task smoke、hard case 和一次 `spreadsheet_notebook_baseline_test50_0519.log` 中断尝试，没有完整 50-task notebook baseline JSON；因此先修 runner，再跑完整 baseline。

**代码改进。**
- `academic/benchmarks/core/runner.py` 的 `_run_spreadsheet_baseline` 新增 `partial_output: Path | None = None` 参数，并在 Spreadsheet baseline CLI 调用处传入 `args.partial_output`。
- Spreadsheet baseline 启动时读取 `partial_output` 中已有 `details`，按 `task_id` 跳过已完成任务；每完成一个 task 就写回：

```python
{
    "benchmark": "spreadsheet",
    "completed_tasks": len(ordered_details),
    "total_tasks": len(tasks),
    "details": ordered_details,
}
```

- notebook baseline 调用 `run_spreadsheet_task_notebook(...)` 时显式传入：

```python
llm_request_timeout_s=max_task_seconds,
```

- single-turn baseline 调用 `run_spreadsheet_task(...)` 时也显式传入：

```python
llm_request_timeout_s=max_task_seconds,
```

- `asyncio.TimeoutError` 转成失败 `BenchmarkResult`，保留：

```python
metrics={
    "exception": "TaskTimeout",
    "max_task_seconds": max_task_seconds,
    "execution_mode": execution_mode,
},
trace={
    "task_id": getattr(task, "task_id", ""),
    "timed_out": True,
    "execution_mode": execution_mode,
},
error=f"Task exceeded {max_task_seconds} seconds",
```

- 其他异常也转成失败 `BenchmarkResult`，不再使整批 baseline 中断：

```python
metrics={
    "exception": type(exc).__name__,
    "execution_mode": execution_mode,
},
error=str(exc),
```

- 并发路径从 fail-fast `asyncio.gather(...)` 改为 `asyncio.as_completed(...)`，使已完成任务可以即时写 partial。
- 新增测试 `test_spreadsheet_baseline_records_notebook_timeout_and_partial`，模拟一个 notebook task 成功、一个 timeout，断言结果顺序、timeout metrics、`llm_request_timeout_s` 转发和 partial 文件内容。

**验证。**

```bash
python -m py_compile \
  academic/benchmarks/core/runner.py \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py
```

结果：通过。

```bash
pytest -q \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_baseline_records_notebook_timeout_and_partial \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_runner_forwards_model_name \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`28 passed`，仅有已有 pytest config / pydantic deprecation warnings。

**主实验命令。**

```bash
python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet --mode baseline \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --n-test 50 \
  --n-runs 1 \
  --test-concurrency 4 \
  --top-k-skills 0 \
  --spreadsheet-execution-mode notebook \
  --spreadsheet-max-turns 5 \
  --max-task-seconds 180 \
  --tag 0519-notebook-baseline-test50 \
  --partial-output academic/results/spreadsheet_notebook_baseline_test50_0519_partial.json \
  --output academic/results/spreadsheet_notebook_baseline_test50_0519.json
```

**落盘文件。**
- 主结果：`academic/results/spreadsheet_notebook_baseline_test50_0519.json`
- partial/resume：`academic/results/spreadsheet_notebook_baseline_test50_0519_partial.json`
- run log：`academic/results/spreadsheet_notebook_baseline_test50_0519.log`

注意：该 log 文件前半段包含修复前的中断尝试；主结果只采用完整 JSON。

**结果指标。**

| metric | value |
|---|---:|
| n_tasks / n_runs | 50 / 50 |
| n_success | 14 |
| success_rate | 0.28 |
| avg_score | 0.3600 |
| avg_total_tokens | 7151.4 |
| avg_input_tokens | 5596.2 |
| avg_output_tokens | 1555.2 |
| executor calls | 148 |
| executor total tokens | 357571 |
| avg notebook turns | 2.96 |
| max notebook turns | 5 |
| timeout_rate | 0.0 |
| avg task elapsed | 37.377s |
| p50 task elapsed | 25.330s |
| max task elapsed | 223.443s |
| wall-clock elapsed | 483.784s |

**对比。**

| metric | notebook baseline test50 | notebook evolve 0519 heldout | single-turn cost baseline |
|---|---:|---:|---:|
| success_rate | 0.28 | 0.28 | 0.26 |
| avg_score | 0.3600 | 0.3350 | 0.3189 |
| avg_total_tokens | 7151.4 | 13557.4 | 1621.3 |
| avg_input_tokens | 5596.2 | 11594.8 | 747.9 |
| avg_output_tokens | 1555.2 | 1962.5 | 873.5 |
| avg task elapsed | 37.377s | 35.408s | 14.495s |
| wall-clock | 483.784s | included in 7740.0s full evolve | 187.456s |

**结论。**
- 完整 Spreadsheet notebook baseline 已跑完并写入主实验表。
- 当前 notebook evolve heldout 没有超过 notebook baseline：success 同为 `0.28`，avg_score 低 `0.0250`，avg_total_tokens 高约 `1.90x`。
- notebook baseline 相比 single-turn cost baseline：success 高 `+0.02`，avg_score 高 `+0.0411`，但 avg_total_tokens 高约 `4.41x`、avg task elapsed 高约 `2.58x`。
- 这说明 notebook executor 本身带来一定 partial-credit/成功率收益，但现有 skill evolve/maintenance 还没有在 heldout 上体现净增益。

## 2026-05-19 Spreadsheet fixed split / refactor parity bugfix / baseline rerun

**动机。** 0519 notebook case study 发现 baseline 与 evolve test set 不对齐，且最终 store 中 callable skill 被写成 informational、失败来源 skill 被 promote 成 active。用户要求：
1. train/test 必须严格固化成 shuffle 后的两个文件，每次只取 prefix/offset。
2. 对照论文伪代码和重构前 Spreadsheet 版本逐段检查，修复 callable/informational、credit/filter/promote 等致命 bug。
3. 用 fixed split 重跑 `20/0` train 并分析日志；baseline 也必须重跑。

**代码改进，精确到行。**

`academic/benchmarks/spreadsheet/loader.py`：

```python
39:     shuffled = load_spreadsheet_task_pool(
47: def load_spreadsheet_task_pool(
86:     shuffled = list(tasks)
87:     random.Random(split_seed).shuffle(shuffled)
88:     return shuffled
```

这把 Spreadsheet loader 拆成“完整 seed-shuffled pool”和“按 n_train/n_test 切片”两层，固定 split 文件可以从同一个 pool 选择。

`academic/benchmarks/spreadsheet/adapter.py`：

```python
23: from academic.benchmarks.spreadsheet.loader import ensure_spreadsheetbench, load_spreadsheet_task_pool, load_spreadsheet_tasks
```

facade 显式导出 `load_spreadsheet_task_pool`，runner 能在 Spreadsheet 分支复用已有 task-id 选择逻辑。

`academic/benchmarks/core/runner.py`：

```python
356:         requested_train_ids = _load_task_id_list(args.train_task_ids) if args.train_task_ids else []
358:         if requested_train_ids or requested_test_ids:
359:             pool = load_spreadsheet_task_pool(
364:             if requested_train_ids:
365:                 train = _select_tasks_by_id(pool, args.train_task_ids)
368:             if requested_test_ids:
369:                 test = _select_tasks_by_id(pool, args.test_task_ids)
382:         split_metadata = {
388:             "train_task_ids_hash": _task_ids_hash(str(task.task_id) for task in train) if train else "",
389:             "test_task_ids_hash": _task_ids_hash(str(task.task_id) for task in test) if test else "",
416:                         "split_metadata": split_metadata,
429:             summary["split_metadata"] = split_metadata
514:         summary["split_metadata"] = split_metadata
2173: def _task_ids_hash(ids: Iterable[str]) -> str:
```

Spreadsheet baseline/evolve 现在都支持 `--train-task-ids` / `--test-task-ids`，输出写入 split hash，避免再把不同 test slice 当 paired A/B。

`academic/benchmarks/spreadsheet/prompts.py`：

```python
102:         "invocation_contract": {"injection_type": "functional | workflow | informational"},
```

extract schema 明确要求 executable/function/script 使用 `functional`，workflow guardrail 使用 `workflow`，接口/知识卡才是 `informational`。

`academic/benchmarks/spreadsheet/maintenance/adapter.py`：

```python
475:     source_success = bool(result.get("success")) and float(result.get("score") or 0.0) >= 0.9
492:             "injection_type": injection_type,
493:             "source_success": source_success,
494:             "source_score": float(result.get("score") or 0.0),
524:     if source_success:
535:     else:
536:         artifact.bundle.negative_cases.append(
541:                 reason="failure-derived repair evidence must pass validation before promotion",
546:     return _normalize_spreadsheet_injection_contract(artifact)
549: def _normalize_spreadsheet_injection_contract(artifact: SkillArtifact) -> SkillArtifact:
551:     if kind_lower in {"executable_tool", "function_tool", "script_tool"} and _spreadsheet_skill_code(artifact):
552:         injection_type = "functional"
561:     artifact.metadata["injection_type"] = injection_type
562:     artifact.interface.invocation_contract = {
564:         "injection_type": injection_type,
1162:             updated = _normalize_spreadsheet_injection_contract(copy.deepcopy(artifact))
1181:     updated = _normalize_spreadsheet_injection_contract(apply_refine_payload(artifact, payload))
1544:         source_success = bool(source_ids & successful_window_task_ids) or bool(artifact.metadata.get("source_success"))
1552:             artifact.metadata["promotion_blocked_reason"] = "requires_successful_source_or_passing_bundle_test"
1635:             "spreadsheet_macro_filter_candidate_credit"
```

具体语义：
- new executable/function/script artifact 只要能提取 spreadsheet code，就强制 `functional`，并同步 metadata 与 interface contract。
- refiner 返回部分 interface 时，再次调用 `_normalize_spreadsheet_injection_contract(...)`，防止 executable 被改回空 contract 或 informational。
- source success/score>=0.9 才生成 positive case；失败 trace 只生成 negative case，不能直接成为 promotion 证据。
- macro promote 必须看到 successful source 或 passing bundle/counterfactual validation；否则 pending 并记录 `promotion_blocked_reason`。
- `filter_candidate=True` 可以作为强过滤信号，写入 `spreadsheet_macro_filter_candidate_credit`。

`academic/benchmarks/tests/test_spreadsheet_evolution.py`：

```python
612: async def test_spreadsheet_refiner_preserves_executable_injection_contract(monkeypatch, tmp_path: Path) -> None:
1588:     assert store.get("spreadsheet_failed_pending").metadata["promotion_blocked_reason"] == "requires_successful_source_or_passing_bundle_test"
1625:     assert store.get("spreadsheet_bad").metadata["disabled_reason"] == "spreadsheet_macro_filter_candidate_credit"
```

新增/更新测试覆盖：executable tool 覆盖 LLM 的 informational metadata、refiner 后 contract 保持 functional、失败来源 pending 不 promote、单次强 filter_candidate 会 disable。

`academic/benchmarks/tests/bfcl/test_benchmark_adapters.py`：

```python
390: def test_runner_select_tasks_by_id_preserves_manifest_order(tmp_path: Path) -> None:
```

验证 runner 的 task-id 文件选择保持 manifest 顺序。

**固定 split 文件。**

| file | n | hash | first ids |
|---|---:|---|---|
| `academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json` | 200 | `04c5121288e0e687` | `58942, 52220, 56427, 31628, 192-22` |
| `academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json` | 195 | `a309d0fe247a8d63` | `567-21, 48643, 54144, 40892, 51431` |

说明：test 文件只有 195 个 id，因为当前 verified/local fixture loader 能加载 395 个有效 task。train/test overlap 为 0。

**验证。**

```bash
python -m py_compile \
  academic/benchmarks/core/runner.py \
  academic/benchmarks/spreadsheet/loader.py \
  academic/benchmarks/spreadsheet/adapter.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/spreadsheet/prompts.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py
```

结果：通过。

```bash
pytest -q \
  academic/benchmarks/tests/test_spreadsheet_evolution.py \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_runner_select_tasks_by_id_preserves_manifest_order \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_baseline_records_notebook_timeout_and_partial
```

结果：`32 passed`，只有已有 pytest config / pydantic deprecation warnings。

**Fixed-split 20/0 train debug 命令。**

```bash
python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet --mode evolve \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --train-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json \
  --test-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json \
  --n-train 20 --n-test 0 \
  --n-train-runs 1 --n-runs 1 \
  --train-concurrency 4 \
  --micro-maintenance-concurrency 8 \
  --test-concurrency 4 \
  --top-k-skills 3 \
  --spreadsheet-execution-mode notebook \
  --spreadsheet-max-turns 5 \
  --max-task-seconds 180 \
  --tag 0520-fixedsplit-train20-debug \
  --partial-output academic/results/spreadsheet_0520-fixedsplit-train20-debug_partial.json \
  --output academic/results/spreadsheet_0520-fixedsplit-train20-debug.json
```

**Fixed-split 20/0 train debug 结果。**

| metric | value |
|---|---:|
| file | `academic/results/spreadsheet_0520-fixedsplit-train20-debug.json` |
| train prefix hash | `afbbdccb557d3dbb` |
| n_train_runs | 20 |
| n_success | 6 |
| success_rate | 0.30 |
| avg_score | 0.3397 |
| avg_total_tokens | 10979.7 |
| elapsed_s | 1860.305 |
| final skills | 14 |
| active / pending / disabled | 5 / 8 / 1 |
| kind counts | workflow_guardrail_card 6, executable_tool 6, interface_contract_card 2 |
| metadata injection counts | workflow 6, functional 6, informational 2 |
| active failed-source skills | 0 |
| pending blocked promotions | 8 |
| disabled skills | 1 |
| credit events | 30 |
| harmful / neutral credit | 5 / 25 |
| callable exposed train runs | 10 / 20 |
| called skill functions | 0 / 20 |

结论：
- `active skill 来自失败 source` 现象在 20/0 debug 中消失：active failed-source skills = 0。
- callable metadata bug 已修复：6 个 executable_tool 的 metadata 均为 functional。
- 仍存在一个方法层问题：即使 10/20 train runs 暴露了 callable skills，模型仍没有实际 import/call skill functions。这不是 metadata bug，而是 Spreadsheet executor prompt / skill 形态还没有让模型形成函数优先行为。
- 8 个 pending skill 被正确阻止 promotion，原因都是 `requires_successful_source_or_passing_bundle_test`。

**Fixed-split notebook baseline rerun 命令。**

```bash
python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet --mode baseline \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --train-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json \
  --test-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json \
  --n-train 20 --n-test 50 \
  --n-runs 1 \
  --test-concurrency 4 \
  --top-k-skills 0 \
  --spreadsheet-execution-mode notebook \
  --spreadsheet-max-turns 5 \
  --max-task-seconds 180 \
  --tag 0520-fixedsplit-notebook-baseline-test50 \
  --partial-output academic/results/spreadsheet_0520-fixedsplit-notebook-baseline-test50_partial.json \
  --output academic/results/spreadsheet_0520-fixedsplit-notebook-baseline-test50.json
```

**Fixed-split notebook baseline rerun 结果。**

| metric | value |
|---|---:|
| file | `academic/results/spreadsheet_0520-fixedsplit-notebook-baseline-test50.json` |
| train prefix hash | `afbbdccb557d3dbb` |
| test prefix hash | `d355c557a14c329e` |
| n_tasks / n_runs | 50 / 50 |
| n_success | 13 |
| success_rate | 0.26 |
| avg_score | 0.3148 |
| avg_total_tokens | 7645.1 |
| avg_input_tokens | 5802.7 |
| avg_output_tokens | 1842.3 |
| timeout_rate | 0.0 |
| errors | 0 |
| elapsed_s | 540.592 |

结论：
- baseline 已按用户要求在 fixed split 上重跑。
- 这条 baseline 是后续 fixed-split evolve 20/50 或 50/50 的 paired test 对照；旧 `spreadsheet_notebook_baseline_test50_0519.json` 与 `spreadsheet_0519-speedup.json` 仍保留为 diagnostic，不再用于严格 paired 结论。

**附属文档。**

- `academic/results/algorithm_docs/SPREADSHEET_REFACTOR_PARITY_AUDIT_20260519.md`
- `academic/results/algorithm_docs/SPREADSHEET_NOTEBOOK_CASE_STUDY_20260519.md`

## 2026-05-19 Spreadsheet callable progressive disclosure and pending exposure switch

**动机。** fixed-split `20/0` debug 显示：10/20 train runs 暴露了 callable skills，但模型没有实际 import/call。用户提出测试阶段尝试“渐进式披露”：prompt 只放 callable signature 和实现梗概，executor 可以直接调用，也可以通过 `print(<skill_object>.code)` 查看完整代码后决定 call 或改写。同时，pending skill 需要可选低权重暴露，避免全部 pending 后没有复用证据。

**trace 观察。**

抽查 `academic/results/spreadsheet_0520-fixedsplit-train20-debug.json` 前 5 个 callable-exposed case：
- 代码没有 `skill_library` import。
- 代码没有 callable function name hit。
- 模型直接 inspect workbook 并重写 openpyxl 逻辑；不是明显照着 callable signature 改写。

skill 名称来自 extractor 返回 artifact name 后的 normalize；它把 instruction type 和若干关键词拼进名字。由于训练 trace/question 里有低质量泛词，出现了 `spreadsheet_sheet_level_manipulation_level_manipulation_need_shift` 这类名字。中间没有人工 rubric 改名。

**代码改进。**

`academic/benchmarks/spreadsheet/skill_runtime.py`：

```python
25: def write_spreadsheet_skill_library(
29:     disclosure_mode: str = "full",
80:         callable_rows.append(
85:                 "signature": f"{func_name}(INPUT_XLSX, OUTPUT_XLSX, **kwargs)",
86:                 "code_preview": spreadsheet_skill_code_preview(code),
89:         skill_objects.append(
90:             f"{func_name}_skill = SimpleNamespace("
103:     prompt = _spreadsheet_callable_prompt(callable_rows, disclosure_mode=disclosure)
107: def _spreadsheet_callable_prompt(callable_rows: Sequence[Dict[str, Any]], *, disclosure_mode: str) -> str:
```

`progressive` 模式中，prompt 明确说明 callable 已在 Python namespace 中可用；可以直接一行调用，也可以 `print(<function_name>_skill.code)` 查看完整实现后改写。

`academic/benchmarks/spreadsheet/executor.py`：

```python
46:     callable_disclosure_mode: str | None = None,
47:     pending_skill_fraction: float = 0.0,
54:     retrieved, retrieval_event = _retrieve_spreadsheet_skills(
109:     callable_prompt = write_spreadsheet_skill_library(
484: def _retrieve_spreadsheet_skills(
496:     pending_k = min(top_k, int(round(top_k * fraction)))
499:     active_k = max(0, top_k - pending_k)
510:     pending = artifact_store.retrieve(
515:         include_pending=True,
```

`academic/skill_repository/store.py`：

```python
420:         include_pending: bool = False,
446:         include_pending: bool = False,
484:             pending_allowed = bool(include_pending) and (
486:             if not artifact.retrieval_enabled() and not pending_allowed:
```

默认 pending 仍不检索；只有显式 `include_pending=True` 或 Spreadsheet executor 的 pending mix 开关打开时才进入。

`academic/benchmarks/core/runner.py` 新增 CLI：

```bash
--spreadsheet-callable-disclosure-mode {full,progressive}
--spreadsheet-test-callable-disclosure-mode {full,progressive}
--spreadsheet-pending-skill-fraction FLOAT
--spreadsheet-test-pending-skill-fraction FLOAT
```

`academic/benchmarks/core/evolution.py` 增加 test-only config override：训练阶段可保持 `full / pending=0`，heldout test 单独启用 `progressive / pending=1/3`。

**pending 生成时机说明。**

pending 在 micro maintenance 中产生：每个 train detail 之后会调用 Spreadsheet extractor，把该 task trace 转成候选 artifact。修复后，成功 trace 可以给 positive case；失败 trace 只能给 negative repair evidence，不能 promote。频繁是因为 micro step 当前是 1，每个 train task 都会尝试提取。

**验证。**

```bash
python -m py_compile \
  academic/benchmarks/spreadsheet/skill_runtime.py \
  academic/benchmarks/spreadsheet/executor.py \
  academic/skill_repository/store.py \
  academic/benchmarks/core/evolution.py \
  academic/benchmarks/core/runner.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：通过。

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`34 passed`，仅有已有 pytest config / pydantic deprecation warnings。

## 2026-05-19 Spreadsheet post-fix train20 progressive pending validation

**命令。**

```bash
SKILL_MAINTENANCE_AUDIT_LOG=academic/results/spreadsheet_0520-postfix-train20-progressive-pending_roles.jsonl \
MAINTENANCE_JSON_MAX_ATTEMPTS=3 \
SPREADSHEET_PROMOTION_HELPFUL_CREDIT_THRESHOLD=1 \
python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet --mode evolve \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --n-train 20 --n-test 0 \
  --n-train-runs 1 --n-runs 1 \
  --train-concurrency 4 \
  --micro-maintenance-concurrency 8 \
  --test-concurrency 4 \
  --top-k-skills 3 \
  --spreadsheet-execution-mode notebook \
  --spreadsheet-max-turns 5 \
  --spreadsheet-callable-disclosure-mode progressive \
  --spreadsheet-pending-skill-fraction 0.3333333333 \
  --max-task-seconds 180 \
  --tag 0520-postfix-train20-progressive-pending \
  --macro-snapshot-dir academic/results/macro_snapshots/spreadsheet_0520-postfix-train20-progressive-pending \
  --partial-output academic/results/spreadsheet_0520-postfix-train20-progressive-pending_partial.json \
  --output academic/results/spreadsheet_0520-postfix-train20-progressive-pending.json
```

**结果。**

| metric | value |
|---|---:|
| file | `academic/results/spreadsheet_0520-postfix-train20-progressive-pending.json` |
| train prefix hash | `afbbdccb557d3dbb` |
| n_tasks / n_runs | 20 / 20 |
| n_success | 6 |
| success_rate | 0.30 |
| avg_score | 0.3397 |
| executor calls | 76 |
| executor tokens | 238216 |
| maintenance calls | 86 |
| maintenance tokens | 657583 |
| observed_wall_clock_s | 1980.0 |
| elapsed_s | 1967.315 |

**相对 `academic/results/spreadsheet_0520-fixedsplit-train20-debug.json`。**

- Accuracy unchanged: success `6/20`, avg_score `0.3397`.
- Executor tokens increased from `219593` to `238216` (`+8.5%`).
- Maintenance tokens increased from `368158` to `657583` (`+78.6%`).
- End-to-end elapsed increased from `1860.305s` to `1967.315s` (`+5.8%`).
- Old run exposed callable skills in `10` train runs but had `0` actual calls. New run exposed `0` callable skills, therefore also had `0` call / inspect / `skill_library` import events.

**Skill lifecycle observations.**

- Final store: `n_skills=1`, `n_active=0`, `n_pending=0`, `n_disabled=1`.
- The only retained artifact, `conditional_sum_formula_if_both_cells_nonempty`, stayed non-promoted and was disabled after repeated harmful retrieval credit.
- Credit events: `9 harmful`, `1 neutral`; all had `failure_mode=irrelevant_retrieval`.
- Refiner actions: `3 refine_minor`, `1 disable`, `14 keep` after disabled terminal state.
- Pre-store gate rejected `6` extracted candidates with `prestore_bundle_test_failed`.

**Token/cost diagnosis.**

- Extractor dominated maintenance cost:
  - `spreadsheet_extractor`: `55` calls, `437589` tokens.
  - `spreadsheet_extractor_rubric_rewrite`: `3` calls, `32609` tokens.
  - `spreadsheet_credit_assigner`: `10` calls, `87021` tokens.
  - `refiner`: `18` calls, `100364` tokens.
- `_ask_json` retry was frequent for extractor: attempt counts were `20` at attempt 1, `20` at attempt 2, and `15` at attempt 3. This is not a timeout, but malformed/non-object JSON retry overhead.
- The hard rubric path also triggered once via `spreadsheet_extractor_rubric_rewrite`, with 3 attempts.

**Conclusion.**

The repair fixed the dangerous promotion path: the old broad/bad callable-style skills did not become active, and harmful retrieval was eventually disabled. However, this `20/0` run did not demonstrate positive skill reuse or promotion. It also did not validate callable call/copy behavior because no callable skill survived extraction/gating. The main remaining issue is extractor JSON/rubric retry cost and overly broad pending retrieval before credit can disable a weak skill.

## 2026-05-20 BFCL TRL 20/50 warm-up, SkillX adapter sanity, and live 50/50 runs

**Motivation.** User requested final BFCL evidence: reproduce SkillX baseline under aligned settings where possible, and run the BFCL+TRL main method. SkillX's repository has extraction/retrieval/prompt formatting but its BFCL agent lacks environment integration, so the adapter uses SkillX's skill pipeline with our official BFCL executor/scorer.

**Code changes.**

- Added robust retry logging around maintenance JSON LLM calls so transient Anthropic proxy `APIConnectionError` no longer discards existing checkpoint progress.
- Added a SkillX-aligned BFCL runner under `academic/benchmarks/bfcl/related/skillx_aligned.py`.
- Added an external skill prompt provider hook to the BFCL executor, so SkillX retrieval/prompt formatting can be injected per turn without replacing the official BFCL execution/scoring backend.
- Fixed SkillX/BFCL tool-name alignment in the adapter: SkillX often extracts tools like `apis.trading.get_stock_info`, while the BFCL executor exposes `get_stock_info`; aliases are now used only for filtering compatibility.

**Completed BFCL+TRL warm-up result.**

Command used train prefix 20 and the fixed heldout 50, with candidate competition enabled. Output:

- Result: `academic/results/bfcl_related20_50_sonnet_trl_20260520_evolve.json`
- Skills: `academic/results/bfcl_related20_50_sonnet_trl_20260520_skills.json`
- Checkpoint: `academic/results/bfcl_related20_50_sonnet_trl_20260520_checkpoint.json`
- Summary: `academic/results/bfcl_related20_50_sonnet_trl_20260520_summary.json`

Metrics:

| metric | value |
|---|---:|
| n_tasks / n_runs | 50 / 50 |
| strict success_rate | 0.06 |
| official_valid_rate | 0.62 |
| avg_score | 0.7710 |
| avg_call_recall | 0.8595 |
| avg_call_precision | 0.7234 |
| avg_total_tokens | 73770.5 |
| avg_input_tokens | 72720.6 |
| avg_output_tokens | 1049.9 |
| avg_elapsed_s | 63.573 |
| avg_model_steps | 9.74 |
| timeout_rate | 0.0 |
| prompt-injected test tasks | 24 / 50 |

Comparison to `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json`:

| metric | baseline | TRL 20/50 warm-up | delta |
|---|---:|---:|---:|
| strict success_rate | 0.08 | 0.06 | -0.02 |
| official_valid_rate | 0.48 | 0.62 | +0.14 |
| avg_score | 0.7388 | 0.7710 | +0.0322 |
| avg_call_recall | 0.8181 | 0.8595 | +0.0414 |
| avg_call_precision | 0.7023 | 0.7234 | +0.0211 |
| avg_total_tokens | 70551.3 | 73770.5 | +3219.2 |
| avg_elapsed_s | 35.555 | 63.573 | +28.018 |
| avg_model_steps | 9.66 | 9.74 | +0.08 |

Interpretation: the warm-up run is better on BFCL official-valid and partial-call quality, but worse on strict task success and more expensive in tokens/time. It should not be described as a clean win over baseline. It is evidence that skill injection improved many structured tool-call traces while not yet improving strict end-state success.

**SkillX status at 2026-05-20 01:05.**

- Smoke 5/5 on unrelated heldout tasks injected no skills, because the small 5-task train library mostly learned TradingBot skills and the heldout smoke cases were not in that tool family.
- Related sanity check on two TradingBot heldout cases confirmed the adapter path works: both cases injected SkillX skills and both were official-valid.
- Formal 50/50 SkillX-aligned diagnostic run is still running:
  - tag: `skillx_bfcl_50_50_sonnet_hash_embed_20260520`
  - partial: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/train_rollout_details_partial.json`
  - caveat: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/embedding_caveat.json`

Important caveat: this SkillX run uses `academic.benchmarks.bfcl.related.simple_embedding_server` hash embeddings because the environment has no local Qwen embedding service and no `torch`/`transformers`/`vllm`. Therefore it is an aligned-executor diagnostic, not a strict SkillX Qwen3-Embedding-8B reproduction.

**Live full BFCL+TRL 50/50 run.**

The formal 50/50 run is still running:

- PID at last check: 639018
- Result target: `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`
- Skills target: `academic/results/bfcl_related50_50_sonnet_trl_20260520_skills.json`
- Checkpoint: `academic/results/bfcl_related50_50_sonnet_trl_20260520_checkpoint.json`

Checkpoint has already been written after the first train window (`next_task_index=10`, `n_train_details=10`, `n_micro_maintenance_reports=10`, `n_store_artifacts=4`), with sidecar details/store/segment rows preserved.

## 2026-05-20 BFCL fixed 50/50 completed results: baseline, TRL, injector-gate TRL, and SkillX

**Motivation.** After the live runs completed, the user asked to add all recent BFCL evidence to the main experiment documents with complete metrics. I re-parsed the result JSON files from `test_summary` / `train_summary` and `runs[0].metrics`, because these result files do not store case metrics directly at the row top level.

**Files.**

- Baseline rerun: `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json`
- TRL main 50/50: `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json`
- TRL main skills: `academic/results/bfcl_related50_50_sonnet_trl_20260520_skills.json`
- TRL injector-gate 50/50: `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json`
- TRL injector-gate skills: `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_skills.json`
- SkillX aligned result: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_result_with_elapsed.json`
- SkillX library: `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_extraction/skillx_skill_library.json`

All rows below use the same fixed manifest:
`academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`.

| run | phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | input | output | elapsed_s | model_steps | score/Mtok | valid/Mtok | total_tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline test | test | 50 | `4/50 = 0.08` | `24/50 = 0.4800` | 0.7388 | 0.8181 | 0.7023 | 70551.3 | 69514.6 | 1036.8 | 35.555 | 9.66 | 10.471129 | 6.803558 | 3527566 |
| TRL train | train | 50 | `12/50 = 0.24` | `35/50 = 0.7000` | 0.8324 | 0.8979 | 0.7939 | 61572.7 | 60485.4 | 1087.2 | 65.506 | 9.28 | 13.519048 | 11.368678 | 3078634 |
| TRL test | test | 50 | `4/50 = 0.08` | `26/50 = 0.5200` | 0.7474 | 0.8491 | 0.6850 | 76689.5 | 75624.8 | 1064.7 | 79.253 | 10.08 | 9.745293 | 6.780586 | 3834477 |
| TRL injector train | train | 50 | `11/50 = 0.22` | `32/50 = 0.6531` | 0.8183 | 0.8744 | 0.7874 | 59604.8 | 58559.2 | 1045.6 | 46.364 | 9.14 | 13.729067 | 10.737394 | 2980239 |
| TRL injector test | test | 50 | `4/50 = 0.08` | `30/50 = 0.6000` | 0.7530 | 0.8371 | 0.7096 | 74175.7 | 73152.7 | 1023.0 | 49.427 | 9.78 | 10.151815 | 8.088902 | 3708785 |
| SkillX test | test | 50 | `4/50 = 0.08` | `27/50 = 0.5400` | 0.7679 | 0.8508 | 0.7140 | 78787.6 | 77726.6 | 1061.0 | 34.927 | 9.78 | 9.746531 | 6.853869 | 3939381 |

**Primary comparison.**

- Strict success is flat at `4/50 = 0.08` for baseline, both 50/50 TRL tests, and SkillX.
- Original TRL improves over baseline on official valid (`0.52` vs `0.48`) and avg score (`0.7474` vs `0.7388`), but has lower precision (`0.6850` vs `0.7023`) and higher token/latency cost.
- Injector-gate TRL is the best current method on official valid: `30/50 = 0.60`.
- Injector-gate TRL also improves avg score over baseline (`0.7530` vs `0.7388`) and preserves precision better than original TRL (`0.7096` vs `0.6850`).
- SkillX aligned has the highest avg score (`0.7679`) and slightly higher recall/precision than our injector-gate test, but lower official valid than injector-gate TRL (`0.54` vs `0.60`) and higher average tokens (`78787.6` vs `74175.7`).

**Cost / utility comparison.**

- Baseline has the best score-per-token among test runs: `10.471129 score/Mtok`, largely because it has no skill-maintenance or heavy skill prompt overhead.
- Injector-gate TRL has the best official-valid-per-token among test runs: `8.088902 valid/Mtok`.
- SkillX's large skill/system prompt footprint makes score-per-token only `9.746531`, despite the highest raw avg score.
- Original TRL has the slowest average elapsed time (`79.253s`), while injector-gate TRL reduces that to `49.427s`.

**SkillX caveat.**

The SkillX aligned row is still diagnostic rather than a strict upstream reproduction:
- it uses our BFCL executor/scorer,
- it uses SkillX plan/skill prompt retrieval,
- it uses hash embedding fallback rather than Qwen3-Embedding-8B,
- it uses the same model and test split as our runs.

**Case-level follow-up.**

The detailed SkillX-vs-TRL case study is recorded separately in:
`academic/results/algorithm_docs/BFCL_TRL_SKILLX_MONITORING_CHRONOLOG_20260520.md`.

The latest-train50-vs-TRL regression and cost case study is recorded separately in:
`academic/results/algorithm_docs/BFCL_LATEST_TRAIN50_VS_TRL_CASE_STUDY_20260520.md`.

Main case-level conclusion:
- SkillX's average-score edge mainly comes from Travel long-transaction cases (`152`, `153`, `194`) where its broad `flight book with fallback airports` skill plus task-level planning context keeps the model executing `airport/code -> cost -> book -> invoice/cancel/insurance`.
- Our TRL has better official-valid performance overall and stronger Vehicle/Twitter behavior, but our Travel skills are currently too fragmented.
- The next algorithmic improvement should be a small targeted implementation of BFCL TravelAPI transaction-chain / plan-level retrieval, validated on `152/153/194` with Vehicle regression guards before any full rerun.

## 2026-05-20 BFCL role-aligned no-TRL competition ablation

**Motivation.** The user pointed out that before tuning TRL, the current framework should first reproduce the old latest-train50 role behavior as closely as possible. The specific request was to align role-level algorithm behavior, keep candidate competition, disable TRL and compare two 50/50 runs:

1. no TRL, no LLM injector, candidate competition on;
2. no TRL, LLM injector on, candidate competition on.

**Code alignment used for these runs.**

- `BFCL_SKILL_INJECTOR_GATE=deterministic` restores the old deterministic prompt injection path: no LLM skill-injector role is called, and full prompt rendering uses the artifact store prompt.
- `BFCL_DYNAMIC_RETRIEVAL_POLICY=every_step` restores step-start retrieval on every nonzero step instead of only after actionable errors.
- `BFCL_EXTRACTOR_EXISTING_ARTIFACTS=full_store` restores the old extractor-side visibility of existing artifacts/store context.
- `--disable-extractor-trl` disables extractor role feedback/meta-skill updates for this ablation.
- `--enable-candidate-competition --candidate-sample-count 3` keeps candidate competition on, so the run tests competition without TRL meta-feedback.

**Files.**

- deterministic injector result: `academic/results/bfcl_align_notrl_compete_detinj_20260520.json`
- deterministic injector skills: `academic/results/bfcl_align_notrl_compete_detinj_20260520_skills.json`
- deterministic injector checkpoint: `academic/results/bfcl_align_notrl_compete_detinj_20260520_checkpoint.json`
- LLM injector result: `academic/results/bfcl_align_notrl_compete_llminj_20260520.json`
- LLM injector skills: `academic/results/bfcl_align_notrl_compete_llminj_20260520_skills.json`
- LLM injector checkpoint: `academic/results/bfcl_align_notrl_compete_llminj_20260520_checkpoint.json`

All rows use the fixed BFCL 50/50 manifest:
`academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`.

| run | phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | input | output | elapsed_s | model_steps | score/Mtok | valid/Mtok | total_tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aligned no-TRL deterministic | train | 50 | `13/50 = 0.26` | `31/50 = 0.6200` | 0.8325 | 0.8917 | 0.7982 | 61739.7 | 60635.9 | 1103.8 | 32.637 | 9.22 | 13.484590 | 10.042167 | 3086983 |
| aligned no-TRL deterministic | test | 50 | `6/50 = 0.12` | `37/50 = 0.7400` | 0.7901 | 0.8938 | 0.7235 | 81663.5 | 80598.2 | 1065.3 | 35.683 | 10.16 | 9.675113 | 9.061571 | 4083177 |
| aligned no-TRL LLM injector | train | 50 | `13/50 = 0.26` | `33/50 = 0.6600` | 0.8441 | 0.9035 | 0.8059 | 61484.3 | 60377.7 | 1106.6 | 54.196 | 9.26 | 13.727927 | 10.734448 | 3074215 |
| aligned no-TRL LLM injector | test | 50 | `5/50 = 0.10` | `30/50 = 0.6000` | 0.7763 | 0.8693 | 0.7139 | 74948.4 | 73896.2 | 1052.3 | 67.044 | 9.94 | 10.358110 | 8.005506 | 3747421 |

**Maintenance role cost.**

| run | role | calls | total_tokens | input | output |
|---|---|---:|---:|---:|---:|
| aligned no-TRL deterministic | extractor | 150 | 1077714 | 1054071 | 23643 |
| aligned no-TRL deterministic | credit_assigner | 19 | 123672 | 97845 | 25827 |
| aligned no-TRL deterministic | refiner | 25 | 140732 | 131627 | 9105 |
| aligned no-TRL deterministic | refactorer | 16 | 141572 | 116891 | 24681 |
| aligned no-TRL deterministic | bundle_builder | 11 | 81190 | 62518 | 18672 |
| aligned no-TRL deterministic | bundle_maintainer | 1 | 4365 | 4118 | 247 |
| aligned no-TRL LLM injector | skill_injector | 809 | 934805 | 820272 | 114533 |
| aligned no-TRL LLM injector | extractor | 150 | 1079693 | 1053639 | 26054 |
| aligned no-TRL LLM injector | credit_assigner | 25 | 147158 | 118395 | 28763 |
| aligned no-TRL LLM injector | refiner | 41 | 230029 | 213485 | 16544 |
| aligned no-TRL LLM injector | refactorer | 11 | 99504 | 81470 | 18034 |
| aligned no-TRL LLM injector | bundle_builder | 9 | 62725 | 50971 | 11754 |

**Conclusion.**

This alignment did find a concrete cause of the recent confusion: later current-code runs had silently changed role behavior relative to `latest train50`. The old latest run did not use an LLM skill-injector selection role, did retrieve every step, and gave the extractor existing store context. After restoring those role-level behaviors in the current framework, the no-TRL deterministic competition run reaches heldout strict `0.12`, official_valid `0.74`, and avg_score `0.7901`, all at or above the old latest train50 heldout `0.08 / 0.70 / 0.7892`.

Therefore the current framework is not inherently worse after refactor. Candidate competition by itself is not the reason for the TRL drop: with TRL disabled and deterministic injection, competition is compatible with the best BFCL heldout result so far.

The TRL drop is now best explained by two interacting mechanisms:

1. **TRL meta-feedback changed extractor behavior.** The previous TRL runs had `extractor_trl_enabled=true` and learned extractor rules from candidate-group/credit feedback. Case analysis showed those rules sometimes over-emphasized error-recovery, exact observable triggers, or avoiding result-reuse. These are plausible locally but conflict with BFCL expected-call behavior in Vehicle/Travel/Trading tasks.
2. **LLM injector is a behavioral bottleneck and a large cost center.** In this no-TRL ablation, simply turning on LLM injector lowers heldout official_valid from `0.74` to `0.60` and strict from `0.12` to `0.10`, while adding `809` injector calls and `934805` injector tokens. The previous TRL runs also had large injector cost: original TRL `1197` calls / `1338717` tokens, injector-gate TRL `459` calls / `508855` tokens.

So the previous TRL did not drop mainly because "competition exists"; it dropped because TRL role feedback plus broad candidate/injector machinery changed which skills are generated, selected, and exposed. The immediate mainline should use deterministic injection for BFCL and tune TRL meta-feedback separately, with domain-specific constraints instead of one global extractor rule set.

## 2026-05-20 BFCL aligned TRL deterministic-injector 20/50 debug run

**Motivation.** After the role-aligned no-TRL ablation showed that current code can recover the strongest heldout result when TRL is disabled, the user asked to turn off the injector and debug TRL first. The run uses train prefix 20 and fixed heldout 50, with checkpoint state preserved so the same run can later continue from 20 train tasks to 50 train tasks.

**Code change before the run.**

- Added completed-prefix checkpoint extension support in `academic/benchmarks/bfcl/related/experiment.py`.
- Completed rounds now write full sidecars:
  - `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520_checkpoint_completed_round_00_details.json`
  - `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520_checkpoint_completed_round_00_state.json`
- The checkpoint records `completed_round_states` with `n_train_details=20`.
- With `BFCL_ALLOW_PREFIX_CHECKPOINT_EXTEND=1`, a later 50/50 manifest run can verify that the 20 task ids are a strict prefix of the 50 train ids and resume round 0 from `next_task_index=20` instead of restarting.
- Verification run before the experiment:
  - `python -m py_compile academic/benchmarks/bfcl/related/experiment.py`
  - local smoke test of `_restore_completed_prefix_round_state`.

**Run configuration.**

- Result: `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520.json`
- Checkpoint: `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520_checkpoint.json`
- Skills: `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520_skills.json`
- Snapshots: `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520_snapshots/`
- Log: `academic/results/logs/bfcl_align_trl_compete_detinj_train20_50_20260520.log`
- Manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_50.json`
- Key env/settings:
  - `BFCL_SKILL_INJECTOR_GATE=deterministic`
  - `BFCL_DYNAMIC_RETRIEVAL_POLICY=every_step`
  - `BFCL_EXTRACTOR_EXISTING_ARTIFACTS=full_store`
  - `BFCL_ALLOW_PREFIX_CHECKPOINT_EXTEND=1`
  - `--enable-candidate-competition --candidate-sample-count 3`
  - TRL enabled by omission of `--disable-extractor-trl`
  - train/test concurrency `4/4`, max steps `20`, max task seconds `180`

**Metrics.**

| run | phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | input | output | elapsed_s | model_steps | score/Mtok | valid/Mtok | total_tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aligned TRL deterministic 20/50 | train | 20 | `9/20 = 0.45` | `16/20 = 0.8000` | 0.9114 | 0.9493 | 0.8972 | 49767.4 | 48747.8 | 1019.5 | 30.405 | 9.10 | 18.313494 | 16.074780 | 995348 |
| aligned TRL deterministic 20/50 | test | 50 | `5/50 = 0.10` | `26/50 = 0.5200` | 0.7565 | 0.8255 | 0.7189 | 73129.4 | 72095.9 | 1033.5 | 32.416 | 9.62 | 10.344622 | 7.110683 | 3656470 |

**Maintenance role cost.**

| role | calls | total_tokens | input | output |
|---|---:|---:|---:|---:|
| extractor | 60 | 453262 | 432300 | 20962 |
| credit_assigner | 7 | 44700 | 35812 | 8888 |
| refiner | 7 | 37509 | 35818 | 1691 |
| refactorer | 4 | 33346 | 26940 | 6406 |
| bundle_builder | 3 | 22065 | 17247 | 4818 |
| extractor_feedback | 2 | 7500 | 6496 | 1004 |
| total maintenance | 83 | 598382 | 554613 | 43769 |

**Live observations.**

- No Python traceback, timeout, or maintenance LLM retry failure was observed in the log.
- Deterministic injector produced zero-token audit events only; no `skill_injector` LLM role appeared.
- TRL did run: `role_feedback.extractor.history` contains 2 updates. The final update says extracted skills had zero retrieval/usage and were too hyper-specific, then strengthens extractor scope/contract rules.
- The first 10-task train window completed quickly, then the first macro wrote checkpoint state with 10 details and 1 maintenance window.
- The second 10-task window was much heavier. The main wall-clock cost came from maintenance, especially extractor/refiner/refactor/bundle operations over many candidates.
- Heldout test injected only two skills frequently: `direct_symbol_binding_for_explicit_company_names` and `multi_turn_order_cancellation_with_context_resolution`; `used_counts` and `called_skill_tool_counts` remained empty because this run is `prompt_only`.

**Conclusion.**

This run confirms that disabling the LLM injector removes the large injector token/time cost, but TRL under the aligned deterministic setting is still not yet better than the aligned no-TRL deterministic 50/50 result. On the fixed heldout 50, aligned TRL 20/50 reaches strict `0.10`, official_valid `0.52`, avg_score `0.7565`, while aligned no-TRL deterministic 50/50 reached strict `0.12`, official_valid `0.74`, avg_score `0.7901`.

The result is not a final TRL verdict because it trained on 20 tasks, not 50. It is enough for debugging: even without LLM injector, the learned extractor feedback is pushing toward stricter/hyper-specific filtering and the resulting heldout skill exposure is narrow. Next step should continue from the preserved 20-task checkpoint to train 50 only after inspecting whether the TRL feedback rules should be softened or made BFCL-domain-specific.

## 2026-05-20 BFCL aligned TRL maturity-gated 20/50 run

**Motivation.** The previous aligned TRL run still let extractor feedback learn from immature or effectively single-skill evidence. The user clarified the intended TRL mechanism: every referenced skill should have enough library residence time, feedback should compare a candidate group rather than judge one isolated skill, and the LLM should receive objective credit records with minimal preprocessing. This run implements that maturity-gated candidate-group-only TRL variant, trains on the fixed first 20 BFCL train tasks, manually inspects the produced feedback/skills, then evaluates on the fixed heldout 50. The checkpoint is preserved for continuing this same training state to 50/50.

**Code changes before the run.**

- `academic/benchmarks/bfcl/related/experiment.py`
  - Added `BFCL_TRL_MIN_EXPOSURE_TASK_RATIO`, default `1.0`, so a candidate group can update extractor TRL only after its members have lived for at least `ceil(macro_maintenance_step * ratio)` later tasks.
  - Changed extractor TRL selection to candidate groups with at least two members.
  - Added objective records to feedback payloads: exposure records, credit records, bundle-test records, and created task index.
  - Kept round-end single-skill feedback rows for logging, but stopped using them to update extractor TRL rules.
  - Fixed summary bookkeeping so round-end logging no longer overwrites the macro candidate-group TRL update summary.
  - Fixed a promotion/archive bug where neutral-only candidate losers could be archived because `harmful_count >= helpful_count` was true for `0 >= 0`. The new archive condition requires `member_harmful > 0 and member_harmful >= member_helpful`.
- `academic/skill_repository/llm_maintenance.py`
  - Updated `EXTRACTOR_RULE_UPDATE_SYSTEM` to say the input is objective mature candidate-group evidence, rules must compare candidates within the same group, and zero/low usage should not be treated as quality evidence unless maturity/opportunity conditions are satisfied.

**Verification before/around the run.**

- `python -m py_compile academic/benchmarks/bfcl/related/experiment.py academic/skill_repository/llm_maintenance.py`
- `pytest -q academic/benchmarks/tests/bfcl_related/test_experiment.py -k 'candidate_group_feedback or role_feedback or extractor_trl'`
- `pytest -q academic/skill_repository/test_llm_maintenance_feedback.py`
- The train/test logs for this run had no `Traceback`, `timeout`, `RequestError`, or `parse error` matches.

**Run artifacts.**

- Train-only manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_20_0_trl_maturity_20260520.json`
- Final result with heldout test: `academic/results/bfcl_align_trl_maturity_train20_50_20260520.json`
- Train-only output: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_trainonly.json`
- Checkpoint for later 20 -> 50 continuation: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_checkpoint.json`
- Skills: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_skills.json`
- Train log: `academic/results/logs/bfcl_align_trl_maturity_train20_50_20260520_train.log`
- Test log: `academic/results/logs/bfcl_align_trl_maturity_train20_50_20260520_test.log`
- Role logs:
  - `academic/results/logs/bfcl_align_trl_maturity_train20_50_20260520_roles.jsonl`
  - `academic/results/logs/bfcl_align_trl_maturity_train20_50_20260520_roles_test.jsonl`
- Case analysis: `academic/results/algorithm_docs/BFCL_TRL_MATURITY_20_50_CASE_ANALYSIS_20260520.md`

**Metrics.**

| run | phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | input | output | elapsed_s | model_steps | timeout | total_tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aligned TRL maturity-gated deterministic 20/50 | train | 20 | `9/20 = 0.45` | `15/20 = 0.75` | 0.9069 | 0.9410 | 0.8960 | 49712.9 | 48689.8 | 1023.0 | 32.014 | 9.05 | 0.0 | 994258 |
| aligned TRL maturity-gated deterministic 20/50 | test | 50 | `4/50 = 0.08` | `27/50 = 0.54` | 0.7655 | 0.8498 | 0.7102 | 73192.1 | 72147.4 | 1044.7 | 36.348 | 9.60 | 0.0 | 3659603 |

**Manual case inspection.**

- TRL candidate-group feedback rows: 2.
- TRL decisions: 9.
- Both mature candidate groups had only neutral/no-use evidence:
  - `extract:r0:t4:multi_turn_base_121`, winner `verify_balance_before_withdrawal__candidate_r0_t4_s0`.
  - `extract:r0:t7:multi_turn_base_135`, winner `watchlist_display_requires_get_watchlist_call__candidate_r0_t7_s2`.
- In both groups, members were retrieved/injected later but had `used=0`, `helpful=0`, `harmful=0`, and neutral-only credit records. This is valid evidence for duplicate extraction and over-broad retrieval scope, but not valid evidence that the actual skill behavior is harmful.
- The LLM-generated extractor rules were qualitatively reasonable for this evidence: avoid near-duplicate candidates from the same source task, make scope guardrails tighter, include triggering conditions, specify action sequence plus activating context, and prefer reusable behavioral patterns over task-specific sequences.

**Bug found during inspection and repaired.**

The manual inspection found six neutral-only candidate losers archived by the old loser condition. This was a real TRL-side promotion/archive bug: a neutral loser with `harmful=0` and `helpful=0` satisfied `harmful >= helpful`. The code now requires positive harmful evidence before archiving a loser. Already-written artifacts were repaired by restoring those six skills from `archived` to `trial` and marking them with `neutral_archive_restored_after_maturity_gate_fix=true`; backups were written with suffix `.pre_neutral_archive_fix`.

**Heldout behavior.**

- Heldout prompt injection happened in `13/50` tasks.
- The only active skills injected in heldout were:
  - `tradingbot_direct_symbol_binding_for_explicit_company_names`
  - `resolve_market_price_for_stock_order`
- Positive example: `multi_turn_base_108` injected both active TradingBot skills and reached strict/official success.
- Partial example: `multi_turn_base_131` injected both skills and was official-valid, but strict failed due an extra `get_user_id`.
- Negative examples: `multi_turn_base_54` and `multi_turn_base_76` injected no skill and failed Vehicle/Social workflows. The 20-task prefix did not produce promoted Vehicle skills, unlike the 50-task no-TRL run.

**Conclusion.**

The maturity-gated TRL mechanism fixed the most dangerous implementation issue: extractor rules are no longer updated from newly created skills, single isolated skills, or immature evidence. The generated meta-rules are reasonable when interpreted as anti-duplication and retrieval-scope guidance. However, this 20/50 run still does not beat the aligned no-TRL deterministic 50/50 result. Heldout official_valid is `0.54` versus `0.74` for aligned no-TRL deterministic 50/50, mainly because this 20-task prefix produced narrow active coverage: only two TradingBot skills were exposed in heldout.

This run is therefore a cleaner TRL debug baseline, not final evidence that TRL helps. The next continuation should start from `academic/results/bfcl_align_trl_maturity_train20_50_20260520_checkpoint.json`, extend train from 20 to 50, and watch whether later candidate groups promote broader Vehicle/Travel/Social skills without archiving neutral-only alternatives.

## 2026-05-20 BFCL aligned TRL maturity-gated 50/50 continuation

**Motivation.** Continue the maturity-gated TRL run from the preserved 20-task checkpoint to the full fixed 50/50 setting, without restarting the first 20 training tasks. The goal was to test whether later training promotes broader active skills, especially Vehicle and non-TradingBot workflows, after the neutral-only loser archive bug fix and candidate-group-only TRL maturity gate.

**Resume setup.**

- Source checkpoint: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_checkpoint.json`
- Pre-continue backup directory: `academic/results/checkpoint_backups/bfcl_align_trl_maturity_train20_before_continue_50_20260520/`
- Manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`
- Verified before launch:
  - The first 20 train task ids in the 50/50 manifest exactly match `academic/experiments/bfcl_case_lists/curated_related_manifest_20_50.json`.
  - The 20-task checkpoint had `next_round_index=1`, one completed round state, and no in-progress current round state.
- Resume env/settings:
  - `BFCL_ALLOW_PREFIX_CHECKPOINT_EXTEND=1`
  - `BFCL_SKILL_INJECTOR_GATE=deterministic`
  - `BFCL_DYNAMIC_RETRIEVAL_POLICY=every_step`
  - `BFCL_EXTRACTOR_EXISTING_ARTIFACTS=full_store`
  - `BFCL_TRL_MIN_EXPOSURE_TASK_RATIO=1.0`
  - `BFCL_MICRO_CONCURRENCY=4`
  - `--enable-candidate-competition --candidate-sample-count 3`
  - `--micro-maintenance-step 1 --macro-maintenance-step 10`
  - `--top-k-skills 2 --skill-injection-mode prompt_only`
  - train/test concurrency `4/4`, max steps `20`, max task seconds `180`.

**Run artifacts.**

- Final result: `academic/results/bfcl_align_trl_maturity_train50_50_20260520.json`
- Skills: `academic/results/bfcl_align_trl_maturity_train50_50_20260520_skills.json`
- Updated checkpoint: `academic/results/bfcl_align_trl_maturity_train20_50_20260520_checkpoint.json`
- Resume log: `academic/results/logs/bfcl_align_trl_maturity_train50_50_20260520_resume.log`
- Snapshots: `academic/results/bfcl_align_trl_maturity_train50_50_20260520_snapshots/`

**Monitoring observations.**

- The run correctly resumed after the first 20 tasks. The first resumed train window was `multi_turn_base_99` through `multi_turn_base_104`, matching train tasks 21-30 in the 50/50 manifest.
- Checkpoint progression:
  - After the first resumed window, checkpoint reached `next_task_index=30`.
  - After the second resumed window, checkpoint reached `next_task_index=40`.
  - After final training, checkpoint finalized with `next_round_index=1`, no current in-progress state, and completed round state available.
- No `Traceback`, `timeout`, `RequestError`, `parse error`, `Exception`, or `Error` matches were found in the resume log.
- The resumed 21-30 train window was weak: only 2/10 official-valid task rows in the live log.
- The 31-40 window showed the desired broader skill exposure. `vehicle_engine_start_brake_pedal_prerequisite` was injected repeatedly and produced many official-valid Vehicle rows, including strict successes.
- Heldout test exposed a much broader active skill set than the 20/50 maturity run:
  - `vehicle_engine_start_brake_pedal_prerequisite`: 14 prompt injections.
  - `vehicle_engine_start_brake_pedal_precondition`: 14 prompt injections.
  - `tradingbot_direct_symbol_binding_for_explicit_company_names`: 13 prompt injections.
  - `resolve_market_price_for_stock_order`: 13 prompt injections.
  - `retrieve_invoice_parameter_binding`: 12 prompt injections.

**Metrics.**

| run | phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | input | output | elapsed_s | model_steps | timeout | total_tokens | score/Mtok | valid/Mtok |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aligned TRL maturity-gated deterministic 50/50 | train | 50 | `13/50 = 0.26` | `32/50 = 0.64` | 0.8319 | 0.8849 | 0.8006 | 61436.7 | 60329.6 | 1107.1 | 34.074 | 9.14 | 0.0 | 3071834 | 13.541357 | 10.417230 |
| aligned TRL maturity-gated deterministic 50/50 | test | 50 | `4/50 = 0.08` | `37/50 = 0.74` | 0.7984 | 0.9024 | 0.7266 | 80774.6 | 79684.5 | 1090.1 | 34.210 | 9.98 | 0.0 | 4038732 | 9.884315 | 9.161291 |

**Comparison to key BFCL rows.**

| run | heldout strict | heldout official_valid | heldout avg_score | note |
|---|---:|---:|---:|---|
| aligned TRL maturity-gated deterministic 50/50 | 0.08 | 0.74 | 0.7984 | resumed from 20 checkpoint; broader Vehicle/TradingBot/invoice exposure |
| aligned no-TRL competition deterministic 50/50 | 0.12 | 0.74 | 0.7901 | strongest strict, same official_valid |
| latest train50 | 0.08 | 0.70 | 0.7892 | older best-current run before role alignment |
| SkillX aligned 50/50 | 0.08 | 0.54 | 0.7679 | same fixed heldout 50 diagnostic |
| maturity-gated TRL 20/50 | 0.08 | 0.54 | 0.7655 | narrow active coverage, only TradingBot skills |

**Conclusion.**

The 50/50 continuation changes the maturity-gated TRL picture substantially. The 20/50 result looked weak because active skill coverage was narrow. After continuing training to 50 tasks, heldout official_valid rises from `0.54` to `0.74`, matching the aligned no-TRL deterministic 50/50 result, and avg_score rises to `0.7984`, slightly above no-TRL deterministic `0.7901`. The remaining weakness is strict exact success: `0.08` versus no-TRL deterministic `0.12`.

This suggests the maturity-gated TRL fixes removed the worst negative behavior and allowed broader useful skills to emerge, especially Vehicle and invoice workflows. It still does not yet improve exact-call behavior enough to beat no-TRL on strict success. The next debugging target should be why official-valid partial improvements are not converting into exact success, especially extra/missing calls in Vehicle and invoice cases.

## 2026-05-20 BFCL strictmeta TRL 20/50 and 50/50 run

**Motivation.** After the maturity-gated TRL run showed partial improvement but still weak exact success, the user asked to implement a stricter TRL plan: extend meta-feedback from extractor-only to extractor/refiner/refactorer, keep objective candidate-group evidence, require explicit harmful evidence before archiving losers, and preserve neutral losers as backup instead of deleting them.

**Code changes.**

- `academic/skill_repository/llm_maintenance.py`
  - Added role-specific rule updates for `extractor`, `refiner`, and `refactorer`.
  - Added refiner rule injection into refine prompts.
- `academic/benchmarks/bfcl/maintenance/adapter.py`
  - Passed refiner/refactorer rule context through BFCL maintenance.
- `academic/skill_repository/refactor_overlap.py`
  - Injected refactorer rules into overlap refactor prompts.
- `academic/benchmarks/bfcl/related/experiment.py`
  - Candidate-group rows now include exposure counts, strict harmful/helpful rates, official valid rates, and raw objective records.
  - Promotion uses a strict harmful gate.
  - Neutral losers are kept as backup; only losers with positive harmful evidence are archived.
  - Candidate-group feedback updates all three roles: extractor/refiner/refactorer.

**Verification.**

- `python -m py_compile academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/maintenance/adapter.py academic/skill_repository/llm_maintenance.py academic/skill_repository/refactor_overlap.py`
- `pytest -q academic/benchmarks/tests/bfcl_related/test_experiment.py`
- `pytest -q academic/skill_repository/test_llm_maintenance_feedback.py`
- Smoke result: `academic/results/bfcl_trl_strictmeta_smoke20_10_20260520.json`
  - Verified role feedback for extractor/refiner/refactorer.
  - Verified candidate-group feedback/decision structure.
  - Verified neutral loser backup behavior.

**Run artifacts.**

- 20/50 result: `academic/results/bfcl_trl_strictmeta_train20_50_20260520.json`
- 20/50 checkpoint: `academic/results/bfcl_trl_strictmeta_train20_50_20260520_checkpoint.json`
- 20/50 skills: `academic/results/bfcl_trl_strictmeta_train20_50_20260520_skills.json`
- 20/50 log: `academic/results/logs/bfcl_trl_strictmeta_train20_50_20260520.log`
- 50/50 result: `academic/results/bfcl_trl_strictmeta_train50_50_20260520.json`
- 50/50 checkpoint: `academic/results/bfcl_trl_strictmeta_train50_50_20260520_checkpoint.json`
- 50/50 skills: `academic/results/bfcl_trl_strictmeta_train50_50_20260520_skills.json`
- 50/50 log: `academic/results/logs/bfcl_trl_strictmeta_train50_50_20260520.log`
- One mistaken 50/50 attempt without prefix-extension env is preserved under `academic/results/backup_misrun_20260520/`; it re-evaluated the 20-task completed round and is not used as a result row.

**Monitoring notes.**

- The 20/50 run completed cleanly: no traceback, timeout, status mismatch, or parse error in the final log scan.
- The 50/50 run required explicit `BFCL_ALLOW_PREFIX_CHECKPOINT_EXTEND=1`; without it, the checkpoint did not extend from the 20-task prefix.
- The correct 50/50 run reached `next_task_index=10`, `20`, `30`, `40`, then completed with `next_round_index=1`, `completed_round_states=1`, and no in-progress current round state before heldout evaluation.
- The 50/50 run produced `161` final skill versions, `15` candidate-group feedback rows, and `71` candidate-group decisions.
- Heldout top prompt-injected/retrieved skills included symbol-binding, Vehicle engine-start/brake, invoice parameter binding, and order verification workflows. `called_skill_tool_counts` remained empty because this run is `prompt_only`.

**Metrics.**

| run | phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | input | output | elapsed_s | model_steps | timeout | total_tokens | score/Mtok | valid/Mtok |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| strictmeta TRL 20/50 | train | 20 | `9/20 = 0.45` | `14/20 = 0.7000` | 0.8974 | 0.9243 | 0.8918 | 47634.7 | 46637.2 | 997.4 | 37.511 | 9.05 | 0.0 | 952693 | 18.839332 | 14.695185 |
| strictmeta TRL 20/50 | test | 50 | `5/50 = 0.10` | `27/50 = 0.5400` | 0.7602 | 0.8418 | 0.7117 | 71052.2 | 70013.8 | 1038.4 | 38.094 | 9.68 | 0.0 | 3552612 | 10.698579 | 7.600042 |
| strictmeta TRL 50/50 | train | 50 | `11/50 = 0.22` | `32/50 = 0.6400` | 0.8428 | 0.8937 | 0.8128 | 60965.8 | 59874.9 | 1090.9 | 46.282 | 9.14 | 0.0 | 3048291 | 13.824828 | 10.497685 |
| strictmeta TRL 50/50 | test | 50 | `5/50 = 0.10` | `33/50 = 0.6600` | 0.7965 | 0.8824 | 0.7423 | 74365.6 | 73309.4 | 1056.2 | 48.147 | 9.84 | 0.0 | 3718278 | 10.710818 | 8.875076 |

**Comparison.**

| run | heldout strict | heldout official_valid | heldout avg_score | note |
|---|---:|---:|---:|---|
| strictmeta TRL 50/50 | 0.10 | 0.66 | 0.7965 | three-role meta-feedback; strict loser archive gate; broad but still prompt-only skill exposure |
| aligned TRL maturity-gated deterministic 50/50 | 0.08 | 0.74 | 0.7984 | stronger official_valid but lower exact success |
| aligned no-TRL competition deterministic 50/50 | 0.12 | 0.74 | 0.7901 | strongest exact success so far |
| latest train50 | 0.08 | 0.70 | 0.7892 | older best-current run before role alignment |
| SkillX aligned 50/50 | 0.08 | 0.54 | 0.7679 | same fixed heldout 50 diagnostic |

**Conclusion.**

The strictmeta changes improve strict heldout success relative to the earlier maturity-gated TRL continuation (`0.10` vs `0.08`) and keep avg_score high (`0.7965`). However, official_valid drops from `0.74` in maturity-gated 50/50 to `0.66`, and strict success still does not beat the no-TRL deterministic competition result (`0.12`).

This is useful evidence that three-role TRL and strict loser handling are not catastrophic, but the current prompt-only skill exposure is still not converting enough partial/official improvements into exact BFCL success. The next TRL improvement should focus on exact-call error conversion, especially whether meta-rules make refiner/refactorer skills too broad or whether retrieved prompt skills add extra calls in Vehicle/Trading/Invoice tasks.
