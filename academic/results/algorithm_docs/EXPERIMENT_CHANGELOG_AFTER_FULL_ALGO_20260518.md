# 完整算法实现后的实验与代码改进追踪

本文档从 `83b6aaf full algo` 之后开始记录。目的不是替代论文结果表，而是给每次工程/算法改动留下可追溯链路：为什么改、改了什么、验证了什么、实验结果如何、对应 git commit 是哪一个。

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
