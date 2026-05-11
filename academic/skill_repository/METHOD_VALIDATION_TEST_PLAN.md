# Method Validation Test Plan

本文档定义 skill repository maintenance 方法的标准验证测例库。目标是把论文和设计文档中的每个技术点，按系统 role 拆成可长期维护的 feature，并为每个 feature 绑定至少一个测例。

本文档只描述测例安排和验收标准；具体 runner / fixture 可以后续按这里的 ID 实现。

## 1. 总体原则

### 1.1 验证目标

这些测例不是 BFCL rubric 的补丁，而是验证方法论是否真实成立：

- skill 是否是可检索、可版本化、可维护的外部资产。
- executor 是否在正确时机检索并真实使用 skill。
- extractor / bundle builder / refiner / stale resolver 是否由 LLM agent 驱动，而不是硬编码规则。
- bundle 是否是 skill-scoped 的长期测试资产。
- test result 是否是每轮独立产物，且能回溯 skill version、bundle version 和 dependency versions。
- with/without 单 skill utility 是否能同时衡量正确性和成本；默认 “skill works” 表示 acc/validity 不降且 tokens/steps 下降。
- integration failure 是否能沉淀为长期 bundle case。
- refine / rollback / stale / refactor 是否遵守版本与正确性约束。
- 对 audit / UI / reporter，只做基础设施健康检查，不作为论文方法效果的核心测例。

### 1.2 标准测例字段

每个测例都应使用统一字段描述：

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定测例 ID，例如 `RET-C01` |
| `feature_id` | 本文档中的 feature ID，例如 `RET-F01` |
| `paper_point_id` | 论文技术点 ID，例如 `P09_pre_execution_retrieval` |
| `primary_role` | 主验证 role |
| `secondary_roles` | 被联动验证的其他 role |
| `query` | 用户任务或 benchmark query |
| `context` | 初始工具状态、已有 skill store、历史 trace、注入错误等 |
| `expected_behavior` | 期待的系统行为 |
| `assertions` | 可机器检查的断言 |
| `mode` | `offline` / `llm_smoke` / `llm_full` |
| `requires_llm` | 是否必须真实调用模型 |
| `suite` | `core_algorithm` / `contract_regression` / `infrastructure_check` |
| `cadence` | `default_smoke` / `pre_experiment` / `nightly` / `manual` |
| `log_requirements` | 必须落盘的 debug / audit 信息 |

### 1.3 运行层级

| 模式 | 用途 | 约束 |
|---|---|---|
| `offline` | 快速验证对象协议、状态机、版本传播、日志 schema | 可以使用 mock LLM / mock executor |
| `llm_smoke` | 少量真实 GLM 调用，验证 role 输入输出和闭环真实性 | 默认开发验收模式 |
| `llm_full` | 全量真实 LLM suite，验证论文技术点覆盖 | 手动或夜间实验 |

### 1.4 测例类型与运行节奏

不是所有 feature 都应在每次实验中运行。本文档把测例分为三类：

| Suite | 目的 | 运行节奏 |
|---|---|---|
| `core_algorithm` | 直接验证论文最终性能相关的核心算法效果，例如复用、unit utility、semantic refine、failure-to-bundle、micro-refactor | 每次正式 smoke 和主实验前优先跑 |
| `contract_regression` | 验证对象协议、版本语义、role API、状态机不被代码改坏 | 代码变更后或 nightly 跑，不计入方法性能 |
| `infrastructure_check` | 验证 audit、UI、reporter、实验发现等可观测性基础设施 | UI/log 相关改动后跑，不作为论文技术点效果证据 |

默认结论报告应优先展示 `core_algorithm`。`contract_regression` 和 `infrastructure_check` 只用于说明系统可信度和工程完整性，不应被当作“方法有效”的证据。

### 1.5 Utility 判定标准

所有 with/without skill 测试都必须同时报告 correctness 和 cost。本文档默认采用以下判定：

| 判定 | 条件 | 含义 |
|---|---|---|
| `work` | `accuracy/validity` 不降，且 `tokens` 或 `steps` 下降 | skill 真正有效：保持正确性的同时降低推理开销 |
| `correctness_gain` | `accuracy/validity` 上升，但 `tokens/steps` 不降或上升 | skill 提升正确性，但不是完整的 efficiency win；需要单独报告 |
| `neutral` | `accuracy/validity` 不变，`tokens/steps` 不变或变化很小 | skill 没有实质价值，不能作为方法有效证据 |
| `harmful` | `accuracy/validity` 下降，或成本显著上升且无正确性收益 | skill 应触发 refine / disable / archive |

例外：如果某个 benchmark 没有可靠 token 统计，必须至少用 `steps`、tool-call count 或 prompt-length estimate 作为成本代理。报告中不能只展示 accuracy/validity 提升而忽略 token/steps。

## 2. Role Feature 设计

### 2.1 Executor

Executor 是核心任务执行者。它不负责提取或维护 skill，但必须把 skill 作为外部上下文真实暴露给模型，并记录模型是否使用 skill。

#### `EXE-F01` 每个 user turn 前检索并附加 skill

原因：论文中 Skill Retrieval 的前置时机要求 executor 在调用前检索已有 skill / workflow history。多 turn 题目不能只在题目开始检索一次。

输入：

- 当前 user turn。
- 当前 skill store。
- 当前任务上下文和上一轮工具结果。

输出：

- 注入 prompt 的 selected skills。
- 模型响应和 tool calls。
- retrieval audit event。

必须记录：

- turn index。
- retrieval query。
- selected skill names / versions。
- injected prompt fragment。
- model response。
- tool calls。

#### `EXE-F02` 报错后 error-aware re-retrieval

原因：设计要求遇到工具报错时重新检索，让历史错误修复经验进入同一题目的 retry。

输入：

- 原始 turn。
- 工具 error。
- 已注入 skills。

输出：

- error-aware retrieval query。
- 新增或重排后的 skills。
- retry guidance。
- retry 后模型响应和 tool calls。

必须记录：

- error message。
- error-aware query。
- reretrieved candidates。
- retry prompt。
- retry result。

#### `EXE-F03` executor 内不做 extraction / bundle / refine

原因：用户明确要求 executor 可能多轮交互，每一轮开始前检索和附加 skill；extraction、test、refinement 只在整道题完成后进行。

输入：

- 任意 multi-turn task。

输出：

- executor trace。

必须断言：

- 每个 turn 有 retrieval。
- extractor / bundle_builder / unit_tester / refiner 事件发生在 task completed 之后。

#### `EXE-F04` 高贴合 skill 的 retrieved-to-used conversion

原因：论文强调模型/scaffold 必须真的使用 external skills，否则 extraction 算法评价无意义。

这个 feature 不要求所有被检索到的 skill 都必须被使用。真实系统中 top-k skill 可能只是背景候选，其中一部分与当前 turn 不完全相关。测例必须人工设计一个“高度贴合、低歧义、可观察行为变化”的 skill，用来判断 scaffold 和模型是否有能力把 retrieved skill 转化为执行行为。

输入：

- 一个高度贴合当前 query 的 skill，例如“当用户明确给出两个文件名并要求比较内容差异时，直接调用 `diff(file_name1=..., file_name2=...)`，不要先 `ls`”。
- 一个与该 skill 完全匹配的 query 和初始文件系统。
- 可选若干干扰 skill，用于验证不是“所有 retrieved skills 都要使用”。

输出：

- 高贴合 skill 是否改变 tool-call 行为。
- 干扰 skill 未使用不计为失败。

必须断言：

- 高贴合 skill 被检索并注入。
- with-skill run 比 without-skill run 更接近期望 tool calls，或减少明确多余调用。
- 若模型没有使用该高贴合 skill，失败原因归到 model/scaffold precondition，而不是 extraction/refine。

### 2.2 Retriever

Retriever 负责从 skill store 中找出候选 skill。它必须可解释、可审计，且不能退化为只看 `intent_keywords` 的硬过滤器。

#### `RET-F01` 非 keyword-only 语义检索

原因：设计要求检索可以结合 embedding、lexical、utility、历史使用效果等信号；没有 intent keyword 不应绝对搜不到。

输入：

- query 不包含 skill 的 `intent_keywords`，但语义上匹配。
- skill store 中有相关 skill。

输出：

- 命中的候选 skill。
- score breakdown。

必须断言：

- 相关 skill 在 top-k 内。
- 如果被过滤，必须有明确过滤原因。

#### `RET-F02` 检索排序与过滤决策正确性

原因：这个 feature 不是为了“多记录 debug 信息”，而是为了检测 retriever 的实际决策是否合理。它验证在多个候选 skill 同时存在时，retriever 是否能把最相关 skill 排在前面，过滤掉明确不应使用的 skill，并避免 disabled / harmful / forbid-matched skill 污染 executor prompt。

输入：

- 一个 query。
- 多个候选 skill：
- 一个语义和工具都强相关的 skill。
- 一个表面关键词相似但 tool/domain 不匹配的 skill。
- 一个匹配但被 disabled 或 harmful 标记的 skill。
- 一个触发 forbid_keywords 的 skill。

输出：

- selected skill list。
- rejected / filtered skill list。
- 每个候选的主要决策因素。

必须断言：

- 强相关 skill 排在 selected top-k。
- disabled / harmful / forbid-matched skill 不进入 prompt。
- 表面关键词相似但实际不匹配的 skill 低于强相关 skill。
- 如果这些行为失败，说明 retriever 影响论文最终性能，需要修复，而不只是日志缺字段。

#### `RET-F03` stale skill 检索后的交接边界

原因：stale lazy handling 本质上属于 Stale Resolver / Executor 使用前检查，不属于 retriever 的排序算法。这里保留一个边界 feature：retriever 只负责返回命中的 stale candidate 并暴露 stale 状态；真正的 refresh / pin / rollback 决策必须交给 stale resolver。

输入：

- skill A 依赖 B。
- B 已更新。
- A 被标记 stale。
- query 检索命中 A。

输出：

- retriever 返回 A 时保留 `stale=true` 和 dependency summary。
- executor 在注入 A 前触发 stale resolver。

必须断言：

- retriever 不擅自清除 stale 或选择 pin。
- executor 不应把 stale A 当作普通 active skill 直接注入。
- stale_resolver 的实际决策由 `STA-F02` 验证。

#### `RET-F04` compact skill set budget effect

原因：论文最终声称不仅提升性能，还降低推理开销和冗余。retriever 需要从维护后的紧凑 skill set 中检索，合并/归档重复 skill 后，top-k candidate 应更少噪声，prompt token 成本应下降或至少不升高。

输入：

- refactor 前含多个重复 skill 的 store。
- refactor / consolidation 后的 store。
- 同一组 retrieval queries。

输出：

- before/after top-k candidates。
- prompt token estimate。
- selected skill diversity / duplicate count。

必须断言：

- consolidation 后 redundant archived skills 不进入正常 top-k。
- candidate duplicate count 下降。
- prompt token estimate 下降或保持不变。
- answer validity 不下降。

### 2.3 Extractor

Extractor 在题目完成后，从 execution trace 中提取可复用 skill。它必须是 LLM-driven，并且输出 benchmark-agnostic 的 `SkillArtifact`。

#### `EXT-F01` trace-to-skill LLM extraction

原因：论文的 New Skill Extraction 要求模型基于执行轨迹反思，提取有复用属性的部分。

性质：这是 extractor role 的 API / 合同正确性检查。它确保 extraction 确实走 LLM role、输出 schema 正确、证据链存在。它本身不直接证明最终性能提升，因此不需要每次实验都跑；只有 extractor prompt、parser、artifact schema 或 adapter 改动后才必须跑。

输入：

- 完整 BenchmarkResult / trace。
- tool schemas。
- existing artifacts summary。

输出：

- `List[SkillArtifact]`。

必须断言：

- audit log 有 extractor 的真实 system/user/raw_response/parsed_response。
- skill evidence 指向 source trace。
- 不是从 hard-coded rubric 直接生成。

#### `EXT-F02` skill 是外部资产而非 prompt patch

原因：论文将 skill 定义为 versioned, retrievable, editable, governable external policy layer。

输入：

- 一个成功或失败 trace。

输出：

- 含 `name/kind/description/body/interface/metadata/version/status/lineage` 的 artifact。

必须断言：

- artifact 能进入 store。
- artifact 能被 retriever 检索。
- artifact 的 prompt block 可注入 executor。

#### `EXT-F03` 多格式 skill 支持

原因：论文明确 code function 只是 skill format 的一个 ablation；skill 可以是 workflow card、rule card、interface convention、shared sub-doc。

输入：

- 一个不需要新代码、只需要行为规则的 trace，例如“已知两个文件名时直接 diff，不要先 ls”。

输出：

- `atomic_tool_rule_card` 或 `workflow_guardrail_card`。

必须断言：

- 非 executable skill 也能被检索和注入。
- executor 行为发生变化。

#### `EXT-F04` 单一责任与窄因果证据

原因：skill 应功能单一、接口清晰，避免 broad benchmark summary。

输入：

- 一个含多个独立错误点的 trace。

输出：

- 多个 narrow artifacts，或拒绝生成过宽 artifact。

必须断言：

- 每个 artifact 的 body 只覆盖一个局部规则。
- metadata / evidence 能定位对应 error site。

#### `EXT-F05` dependency 显式记录

原因：后续 stale propagation 和 lazy resolution 依赖显式 dependency graph。

输入：

- 新 skill A 复用已有 shared skill B。

输出：

- A.dependencies 包含 B。
- A.dependency_pins 或 floating dependency policy 可追踪。

必须断言：

- store 中 A/B 依赖关系可查询。

#### `EXT-F06` interface contract completeness

原因：前向规范要求每个 skill 的接口清晰、功能单一、易于理解和使用。`interface` 不是 UI 文案，而是后续 bundle builder、refiner、stale resolver 和 dependency handling 的共同 contract。

输入：

- 一个可复用 trace。
- extractor 输出的 SkillArtifact。

输出：

- `SkillInterface.summary`。
- `SkillInterface.usage`。
- `input_contract`。
- `output_contract`。
- `invocation_contract`。
- `compatibility_notes`。

必须断言：

- interface 字段不是空模板。
- usage 能明确说明何时使用和何时不用。
- input/output/invocation contract 与 body 一致。
- 若 interface 缺失或过宽，该 artifact 不能进入 core experiment 的 active skill store。

### 2.4 Bundle Builder

Bundle Builder 为单个 target skill 构建长期 maintenance bundle。它必须只关注该 skill 的局部作用域，不能把整道 benchmark 题目粗暴塞进 bundle。

#### `BUN-F01` LLM skill-scoped case distillation

原因：设计要求根据 trace 归因构建 bundle 时，无论成功失败都只考虑该 skill 的作用；需要 agent 提取轻量 scope。

输入：

- target SkillArtifact。
- source results。
- replay results。
- integration failures。

输出：

- `SkillBundle`。

必须断言：

- audit log 有 bundle_builder 的真实输入输出。
- cases 的 `context.task_fragment` 只包含与 target skill 相关的 turns/tools。

#### `BUN-F02` positive / negative / integration cases 分离

原因：bundle 是长期测试资产，需要覆盖帮助样例、反例/回归防护和真实 integration failure。

输入：

- 成功 trace。
- 手工 broken trace。
- integration failure trace。

输出：

- positive_cases。
- negative_cases。
- integration_cases。

必须断言：

- 三类 case 的 polarity/source/contrast_protocol 正确。

#### `BUN-F03` bundle 与 result 分离

原因：bundle 是长期资产，test result 是每轮运行产物，二者不能混写。

输入：

- 一个已有 bundle。
- 多次 unit test run。

输出：

- 多个独立 SkillTestResult。

必须断言：

- bundle 不含本轮 acc/tokens/steps。
- result 可回溯 skill_version、bundle_version、dependency_versions。

#### `BUN-F04` bundle version 可独立演化

原因：同一个 skill version family 可以经历多个 bundle version；追加测试不必改变 skill 内容。

输入：

- skill version 不变。
- 从 integration failure 追加一个 case。

输出：

- bundle_version 增加。
- skill version 不变。

必须断言：

- store history 不丢失旧 bundle。

#### `BUN-F05` interface 变更时同步更新 bundle

原因：修改 interface / description 会改变 skill contract，必须同步更新 tests。

输入：

- refiner 输出改变 interface。
- bundle 未更新或不一致。

输出：

- validator 拒绝该 refine。

必须断言：

- 流程失败原因指向 interface-test inconsistency。

### 2.5 Unit Tester

Unit Tester 对单个 skill 的 bundle 跑 with/without this skill 局部对照。它不是全 repo leave-one-out，而是 skill-scoped shapley-like utility。

所有 unit utility test 的默认目标都是：`with_skill` 相比 `without_skill`，accuracy/validity 不下降，同时 tokens 或 steps 下降。只要成本不下降，就不能把该 skill 直接标为 `work`；最多标为 `correctness_gain` 或 `neutral`。

#### `TST-F01` with/without counterfactual replay

原因：论文要求通过 Shapley-style unit tests 估计单个 skill 的边际价值。

输入：

- target skill。
- skill bundle case。

输出：

- without_skill run。
- with_skill run。
- delta accuracy / validity / tokens / steps。
- utility_label: `work` / `correctness_gain` / `neutral` / `harmful`。

必须断言：

- 每个 case 至少有对应 variant run。
- aggregate 和 counterfactual 字段完整。
- `work` 必须满足 acc/validity 不降且 tokens 或 steps 下降。
- 若 acc/validity 提升但 tokens/steps 上升，必须标为 `correctness_gain`，不能标为 `work`。

#### `TST-F02` 全量 bundle 回归

原因：refine 成功标准是通过当前绑定的全部 tests，不是只通过首个样例。

输入：

- bundle 中多个 positive / negative / integration cases。

输出：

- 每个 case 的 run。

必须断言：

- `len(unit_case_runs)` 覆盖全部 case 和 protocol variants。
- 不允许默认 `cases[:1]` 作为正式 pass。

#### `TST-F03` 有害 skill 负贡献检测

原因：skill 是否值得保留需要看单独价值；有害 skill 应显示负 contribution。

输入：

- 手工注入会导致错误 tool call 的 skill。

输出：

- with_skill validity 下降。
- 或 cost 显著上升且没有 correctness gain。
- delta_accuracy / delta_validity / delta_tokens / delta_steps 支持该结论。

必须断言：

- report 明确标记 harmful/regression。

#### `TST-F04` replay trace 可视化

原因：用户需要看到每个 bundle case 的运行细节，而不只是过没过。

输入：

- 任意 bundle test run。

输出：

- SkillTestCaseRun.trace。

必须断言：

- trace 中包含 turns、tool calls、errors、model outputs、token/step metrics。
- 前端可从 test result 跳转到 case 和 replay trace。

### 2.6 Refiner

Refiner 根据 test results、integration failures、历史 refine 记录和依赖摘要对 skill 做语义修复。它必须是 LLM-driven，disable 只能作为 fallback safety guard。

#### `REF-F01` LLM semantic refinement

原因：设计要求 refine 允许做必要修复，而不是简单 rubric filter。

性质：这是 refiner role 的 API / 合同正确性检查。它验证 refiner 是否真实调用 LLM 并产生符合 schema 的决策。真正影响论文性能的测试是 `REF-F02`、`REF-F03` 和 `INT-F03-*`，因为它们验证错误恢复是否能改善后续任务。

输入：

- broken skill。
- full bundle。
- failed test result。
- integration failure。
- refinement history。

输出：

- `keep/refine_minor/refine_major/rollback/disable/pin_dependency/mark_stale` 决策。
- 如修复，返回更新后的 artifact。

必须断言：

- audit log 有 refiner 真实输入输出。
- action 不是硬编码规则直接决定。

#### `REF-F02` targeted broken recovery

原因：broken 是为了检验恢复能力，应由针对性手工设计，不消耗大量资源随机摇错。

性质：这是核心算法效果测例。它不应随机生成 broken，而应由测试作者针对某个已知 skill contract 手工设计最小破坏，使失败归因明确、refiner 输入轻量、LLM 调用成本可控。

输入：

- 一个上轮有效 skill。
- 手工注入一个明确错误，例如错误参数名、错误工具顺序、错误 stop condition。

输出：

- refiner 修复成可通过 bundle 的 skill，或明确 rollback。

必须断言：

- semantic repair 和 rollback recovery 分开计数。
- disable 不计为 semantic repair。

#### `REF-F03` refine loop until pass or limit

原因：refine 的成功标准是通过当前绑定全部 tests；失败应继续修改直到上限，再 reject/disabled/stale-lock。

输入：

- 第一次修复不足以通过全部 bundle 的 skill。

输出：

- 多轮 refiner attempts。

必须断言：

- 未达上限且仍失败时不能直接最终通过。
- 每轮 attempt 有 result_id 和 refinement history。

#### `REF-F04` minor update 不删除旧 tests

原因：minor update 表示 interface 不变或前向兼容，旧 tests 必须保留。

输入：

- refiner 返回 `refine_minor`，但 bundle 少了旧 case。

输出：

- validator 拒绝。

必须断言：

- 错误原因包含 minor test monotonicity violation。

#### `REF-F05` major update 记录 migration reason

原因：major update 允许迁移 tests，但必须保留 lineage 和升级原因。

输入：

- refiner 返回 `refine_major`。

输出：

- 新 artifact lineage。

必须断言：

- parent_version / parent_version_id / migration_reason 存在。
- 旧版本进入 history。

### 2.7 Stale Resolver

Stale Resolver 在下游 skill 被调用时处理上游依赖变化。它是惰性维护机制的关键，不应在上游更新时强制全量迁移。

#### `STA-F01` 上游更新后 stale propagation

原因：如果 A 依赖 B，B 更新后 A 应标 stale，但不立即强制升级。

输入：

- A.dependencies = [B]。
- B minor 或 major 更新。

输出：

- A.stale = true。

必须断言：

- B 旧版本保留。
- A 未被自动重写。

#### `STA-F02` lazy compatibility decision

原因：调用 stale A 时才选择 clear_stale、refresh_minor、refresh_major、pin_legacy、rollback 或 keep_stale。

输入：

- stale A。
- upstream_context。
- 当前 query 命中 A。

输出：

- stale_resolver decision。

必须断言：

- decision 落 audit log。
- executor 使用决策后的依赖版本。

#### `STA-F03` incompatible upstream pin legacy

原因：如果 B major 更新不兼容，下游 A 可以继续锁定 B 的旧版本。

输入：

- B major breaking。
- A 的 tests 在 B latest 下失败，在 B old 下通过。

输出：

- A.dependency_pins 指向 B old。

必须断言：

- result.dependency_versions 包含 B old。

### 2.8 Refactorer

Refactorer 在重复证据积累后抽取公共可复用部分、合并重复 skill、拆分过度合并的 skill，并保证 correctness-preserving。

#### `RFA-F01` K-step micro-refactor trigger

原因：公共部分不应在单个 skill 初次出现时预测，而应在固定 K step 扫描并要求重复证据。

输入：

- 连续 K 个相关 train tasks。
- 多个 skills 有 repeated_evidence。

输出：

- refactor candidates。

必须断言：

- 未到 K 或无重复证据时不触发。
- 到 K 且重复证据足够时触发。

#### `RFA-F02` shared reusable part extraction

原因：论文主打 repository-level maintenance，包括重复 skill 合并、shared helper/sub-doc 抽取。

该 feature 需要分难度评估，因为“可复用公共部分”并不总是表面文本重复：

- `easy`: 多个 skill 明文重复同一段参数约定或 checklist。
- `medium`: 文本不同但结构相同，例如不同工具共享“先解析 id，再用 id 调用后续接口”的 workflow。
- `hard`: 表面工具、措辞和任务都不同，但底层是同一个可复用接口传播规则或错误恢复策略。

`medium` 和 `hard` 必须由 refactor agent 判断复用性，并输出为什么这是 shared part、哪些原 skill 应引用它、哪些不应引用。不能只靠 n-gram / 文本相似度。

输入：

- 两个或多个 skills 重复描述同一参数约定、workflow 片段或 checklist。

输出：

- shared_subdoc / shared helper skill。
- 原 skills 引用 shared part。

必须断言：

- dependencies 更新。
- affected skills 的接口仍清楚。
- agent 输出 reuse rationale。
- hard case 中至少有一个表面相似但不应合并的 negative candidate，并被正确排除。

#### `RFA-F03` correctness-preserving group rollback

原因：refactor 后所有受影响 skill 的绑定 tests 必须通过，任一失败则整组回滚。

输入：

- 一个会破坏其中一个 affected skill 的 refactor。

输出：

- refactor group rollback。

必须断言：

- 所有 affected artifacts 恢复到 refactor 前版本。
- 失败 group 有 refactor_group_id 和 failure reason。

#### `RFA-F04` duplicate skill consolidation and safe pruning

原因：真实在线系统中，由于 retriever 不完美、extractor 视野有限、query 表达不同，系统很可能反复制造多个语义相同或高度重叠的 skill。如果不做合并，skill store 会膨胀，retrieval noise 和 prompt token cost 都会上升。这是 repository-level maintenance 的核心职责之一。

这个 feature 不应简单硬删除 skill。正确行为是由 refactor agent 选择 canonical skill，把重复 skill 的 evidence、bundle cases、usage statistics、dependency references 合并到 canonical skill，然后把冗余 skill 标记为 `merged` / `archived`，保留 lineage 和 redirect 信息。只有确认没有历史依赖和测试资产时，才允许物理删除。

输入：

- 多个名称不同但 interface / body / evidence 高度重叠的 skills。
- 每个 skill 可能有自己的 bundle cases、usage_count、source traces、downstream dependencies。
- 至少一个表面相似但语义不同的 negative candidate。

输出：

- canonical skill。
- redundant skills 的 `status=merged` 或 `status=archived`。
- merged evidence / bundle / usage statistics。
- downstream dependency redirect 或 pin decision。

必须断言：

- agent 输出 duplicate rationale 和 non-duplicate exclusion rationale。
- canonical skill 覆盖所有被合并 skill 的旧 bundle cases。
- 所有受影响 bundle tests 通过。
- redundant skill 不再进入正常 retrieval prompt。
- history、lineage、redirect 信息可追踪，不能破坏 legacy dependency。
- retrieval candidate 数量、prompt token 或重复 skill 数有可观测下降。

#### `RFA-F05` over-merge detection and split

原因：重复合并的反面风险是过度合并。两个 skill 表面相似但语义契约不同，强行合并会造成错误泛化和负迁移。论文中的 refactor correctness 不只包括“能抽公共部分”，也包括发现过宽 skill 并拆分或拒绝合并。

输入：

- 一个过宽 skill，覆盖两个实际上不同的 tool contract / domain rule。
- 或一组候选 skills，其中有一部分只表面相似但不应合并。

输出：

- split 后的多个 narrower skills，或 reject merge decision。
- 对应 bundle cases 重新绑定到正确 skill。

必须断言：

- split 后每个 skill single responsibility 更清楚。
- 原 bundle cases 被正确迁移或复制，不丢测试资产。
- 受影响 tests 全部通过。
- 不应合并的 negative candidate 被明确排除。

### 2.9 Artifact Store

Artifact Store 维护版本、history、bundle、test results、dependencies、stale 和 rollback，是方法的状态一致性核心。

#### `STR-F01` artifact add / version / history

原因：skill 是版本化资产，更新时必须保留旧版本历史。

输入：

- 同名 artifact 多次 add。

输出：

- version 单调增加。
- history 包含旧版本快照。

必须断言：

- 不覆盖稳定版本。
- lineage parent 正确。

#### `STR-F02` 同名 skill 更新不丢 bundle

原因：历史实验中出现过同名 skill 更新后 final skill bundle 丢失的问题。

输入：

- 同名 artifact 新版本未显式带 bundle。
- store 中旧版本已有 bundle。

输出：

- 新版本继承或合并旧 bundle。

必须断言：

- bundle cases 不被清空。

#### `STR-F03` rollback non-destructive

原因：rollback 不应删除坏版本或 legacy 版本，因为仍可能有下游 pin。

输入：

- skill v1 stable。
- skill v2 bad。
- rollback to v1。

输出：

- current artifact 回到 stable 内容。
- v2 进入 history。

必须断言：

- version_kind = rollback。
- history 可追踪 v1/v2。

#### `STR-F04` test result 独立保存

原因：每次运行必须生成独立 result object，不能覆盖 bundle。

输入：

- 同一 bundle 重复测试两次。

输出：

- 两个 result_id。

必须断言：

- created_at 不同。
- result snapshot 可回溯当时版本。

### 2.10 Integration Runner

Integration Runner 用真实 train traces 发现多 skill 共存时的问题。它不做全量 pairwise/subset 枚举，只做真实失败驱动的 targeted diagnosis。

#### `INT-F01` 真实 train trace integration replay

原因：integration test 的职责是检测多 skill 冲突、误导、检索污染、依赖升级问题。

输入：

- 已有 skill store。
- 一组 train tasks。

输出：

- end-to-end details。
- integration failures。

必须断言：

- 失败样例能连接到相关 skills 或 repo-level refactor candidate。

#### `INT-F02` 多次同题从零 evolve

原因：验证 skill 是否会经历 evolving，且上一次积累能帮助下一次。

输入：

- 一个困难题。
- 空 skill store。
- 连续重复运行多轮。

输出：

- loop0 extraction。
- loop1+ retrieval/use。
- maintenance results。

必须断言：

- 后续轮有 skill 命中。
- 指标和 trace 展示 skill 对执行的影响。

#### `INT-F03` 相关 query 连续演化总场景

原因：验证同一类 query 上 evolve 出来的 skill 是否稳定、是否产生冲突，且错误能否被后续修正。

这是当前最核心的论文效果测例，不能只作为一个粗粒度 end-to-end case。它应拆成多个 partial 场景，每个场景只验证一个关键机制，最后再组合成 full-chain 场景。

输入：

- 一系列相关 query。
- 可选 targeted broken injection。

输出：

- 多轮 skill store evolution。
- repeated evidence。
- bundle/refine/refactor events。

必须断言：

- 每道题完成后才 extraction/test/refine。
- 每题 executor 内每 turn 都有 retrieval。

#### `INT-F03A` 连续相关 query 的正向复用

原因：先验证最基本的 online future help。第 1 个 query 产生 skill，第 2/3 个相似 query 能检索并受益。

输入：

- 3 个同族 query，工具和接口模式相同，但实体名、文件名或 id 不同。
- 初始 skill store 为空。

输出：

- query1 完成后 extractor / bundle builder 生成 skill 和 bundle。
- query2/query3 executor 检索该 skill。
- with-skill 相比 without-skill acc/validity 不降，且 tokens 或 steps 下降。

必须断言：

- skill usage / retrieval count 增加。
- utility report 记录 correctness delta 和 cost delta。
- 如果只有 correctness 提升但成本不降，标为 `correctness_gain`，不标为 `work`。
- 如果检索到了但模型没有用，失败归因到 `EXE-F04`。

#### `INT-F03B` targeted broken 后语义恢复

原因：验证上一次 skill 被手工破坏后，后续相关 query 的失败能否触发 refiner 修复，而不是只 disable。

输入：

- `INT-F03A` 产生的有效 skill。
- 手工注入一个最小 broken，例如改错参数名、改错调用顺序、加入错误禁止规则。
- 下一条相关 query。

输出：

- with broken skill 的 unit/integration failure。
- failure 被归因到该 skill。
- refiner 产生 semantic repair 或 rollback。

必须断言：

- repair 后全 bundle pass。
- semantic repair、rollback、disable 三类结果分开记录。
- disable 不能计为恢复成功，只能计为 safety fallback。

#### `INT-F03C` integration failure 回写 bundle

原因：验证真实端到端失败是否会变成长期 skill-specific test，而不是一次性日志。

输入：

- 一个相关 query 在多 skill 共存时失败。
- 失败原因只与其中一个 skill 的局部作用有关。

输出：

- attribution / bundle builder 生成 skill-scoped integration case。
- artifact.bundle.integration_cases 增加。
- bundle_version 增加。

必须断言：

- 新 case 的 task_fragment 只包含目标 skill 作用域。
- 后续 refine/test 会运行这个 integration-derived case。

#### `INT-F03D` 检索污染与负迁移

原因：连续演化可能引入看似相关但会误导 executor 的 skill。这个 partial 场景验证 retriever + unit utility 能否发现负迁移。

输入：

- 两个同域但行为相反或参数契约不同的 skills。
- 一个容易误检索到错误 skill 的 query。

输出：

- retriever 排序或过滤尝试避免错误 skill。
- 如果错误 skill 被使用导致失败，unit utility 显示负贡献并触发 refine/disable/metadata 修正。

必须断言：

- 负迁移不会被当作成功 evolution。
- harmful skill 的后续状态变化可追踪。

#### `INT-F03E` 多 skill 协作与接口传播

原因：论文方法不只测试单 skill，还要验证多 skill 配合时接口是否能传播，例如上游 skill 产生 id，下游 skill 使用 id。

输入：

- 一组相关 query，需要先获取/解析某个实体，再把实体 id 传给后续工具。
- skill A 描述 id 获取规则，skill B 描述后续调用规则，B 依赖 A。

输出：

- executor 同一题中检索并使用 A/B。
- B 记录对 A 的 dependency。
- A 更新后 B stale。

必须断言：

- tool-call sequence 中 id propagation 正确。
- dependency graph 和 stale propagation 正确。

#### `INT-F03F` 重复证据触发 micro-refactor

原因：相关 query 连续运行后应积累 repeated evidence，并在 K-step 扫描时尝试抽 shared part。

输入：

- 至少 K 个相关 query。
- 多个 skills 重复表达同一 workflow / interface convention。

输出：

- repeated_evidence 增加。
- refactor candidate 或 shared_subdoc 生成。

必须断言：

- 未达到 K 或证据不足时不抽取。
- 达到 K 后由 agent 判断 shared reusable part。

### 2.11 Audit Logger

Audit Logger 是可信实验的基础。每个 role 的输入输出、模型、耗时、token、产物 ID 都必须可查。

注意：Audit Logger 不作为论文算法效果测例。它属于 `infrastructure_check`，用于保证真实实验可审计。

#### `LOG-F01` role I/O 完整落盘

原因：用户需要验证结果真实，不能只看 summary。

输入：

- 任意 llm_smoke experiment。

输出：

- debug_events。
- JSONL audit log。

必须断言：

- extractor / bundle_builder / refiner / stale_resolver 若被调用，均有 raw + parsed I/O。
- executor turn 有 prompt / response / tool calls。

#### `LOG-F02` retrieval debug detail

原因：误检索和检索污染需要 repo summary、候选分数、过滤原因。

输入：

- 任意检索事件。

输出：

- retrieval audit event。

必须断言：

- repo summary、candidate scores、selected reason 都存在。

#### `LOG-F03` latency and call accounting

原因：实验很慢时需要定位各 role 耗时和调用次数。

输入：

- 任意完整实验。

输出：

- per-role call count。
- latency。
- token usage。

必须断言：

- report 能按 role 汇总耗时和调用次数。

### 2.12 Viewer

Viewer 负责把复杂产物变成可审计界面。它不改变实验结果，但必须让用户能按时间顺序检查每个 turn、role 和 artifact。

注意：Viewer 不作为论文算法效果测例。它属于 `infrastructure_check`，用于人工检查和 debug。

#### `UI-F01` 实验列表可发现

原因：历史上出现过搜索不到 experiments 的问题。

输入：

- results directory。

输出：

- `/api/maintenance/experiments`。

必须断言：

- exp1/exp2/exp3 和新 method validation results 可发现。

#### `UI-F02` turn page 数据流展示

原因：用户希望每页展示一轮结果，按时间顺序展示 role 调用输入输出和信息流。

输入：

- experiment detail payload。

输出：

- pages。
- flow cards。

必须断言：

- 每页包含 problem、retrieval、executor、artifacts、tests、refine 等相关节点。

#### `UI-F03` artifact explorer

原因：用户最关心对话历史、产出 skill、bundle、测试结果。

输入：

- final_skills。
- skill_bundles。
- maintenance_test_results。

输出：

- skill explorer。
- bundle explorer。
- test result explorer。

必须断言：

- skill 显示 body/interface/usage/retrieval count。
- bundle case 以结构化字段展示，不是大段 raw JSON。
- test result 能跳转 case 和 replay trace。

#### `UI-F04` raw tree JSON fallback

原因：所有结构化展示都可能漏字段，debug 时仍需要可展开/折叠的 raw tree。

输入：

- 任意 card payload。

输出：

- expandable tree view。

必须断言：

- input/output/raw/debug 字段可展开。
- 不出现单行溢出的 raw JSON。

### 2.13 Reporter

Reporter 把实验输出整理成论文和工程可读报告，必须避免只报告提升指标。

注意：Reporter 不作为论文算法效果测例。它属于 `infrastructure_check` / reporting contract，用于避免实验结果被选择性呈现。

#### `REP-F01` 全指标报告

原因：用户要求所有实验结果包含所有指标，而不是只包含提升指标。

输入：

- experiment result。

输出：

- markdown / json report。

必须断言：

- accuracy/validity/tokens/steps/call_f1/errors/retrieval counts/role calls/latency 全部列出。

#### `REP-F02` method fidelity report

原因：需要判断哪里 work、哪里没有忠实实现论文设想。

输入：

- method validation suite results。

输出：

- paper_point_id coverage table。

必须断言：

- 每个 paper_point_id 状态为 pass/fail/partial/missing。
- fail/partial 有对应 evidence 和 next fix。

## 3. Role 分类测例库

### 3.1 Executor / Retriever 测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `EXE-C01` | `EXE-F01` | `core_algorithm` | `P09_pre_execution_retrieval` | `llm_smoke` | 多 turn BFCL query，store 中已有相关 tool-rule skill | 每个 turn 前均有 retrieval，selected skill 被注入 prompt |
| `EXE-C02` | `EXE-F02` | `core_algorithm` | `P10_error_aware_reretrieval` | `llm_smoke` | 手工注入会导致 domain tool error 的 skill | error 后生成 error-aware query，重新检索并 retry |
| `EXE-C03` | `EXE-F03` | `contract_regression` | `P11_post_execution_maintenance` | `offline` | 多 turn mock task | extraction/test/refine 只在 task completed 后出现 |
| `EXE-C04` | `EXE-F04` | `core_algorithm` | `P05_model_skill_use_precondition` | `llm_smoke` | 高贴合直接 diff skill + 干扰 skill | 高贴合 skill 改善 tool calls，干扰 skill 不用不算失败 |
| `RET-C01` | `RET-F01` | `core_algorithm` | `P34_retrieval_not_keyword_only` | `offline` + `llm_smoke` | query 不含 intent keyword 但语义匹配 | 相关 skill 仍进 top-k |
| `RET-C02` | `RET-F02` | `core_algorithm` | `P35_retrieval_auditability` | `offline` + `llm_smoke` | 强相关、表面相似、disabled/harmful、forbid-matched skills | 强相关 skill selected；污染 skill 不进入 prompt |
| `RET-C03` | `RET-F03` | `contract_regression` | `P28_lazy_stale_resolution` | `offline` | stale downstream skill 被检索命中 | retriever 暴露 stale 状态，决策交给 stale_resolver |
| `RET-C04` | `RET-F04` | `core_algorithm` | `P41_compact_skill_budget` | `offline` + `llm_smoke` | refactor 前后同一组 retrieval queries | duplicate candidates 和 prompt token 下降，accuracy/validity 不降 |

### 3.2 Extractor 测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `EXT-C01` | `EXT-F01` | `contract_regression` | `P08_trace_to_skill_extraction` | `llm_smoke` | 成功完成的文件 diff trace | LLM extractor 产出 schema 正确的 artifact |
| `EXT-C02` | `EXT-F02` | `contract_regression` | `P01_external_policy_asset` | `offline` + `llm_smoke` | 任意可提取 trace | artifact 作为外部资产进入 store 并可检索 |
| `EXT-C03` | `EXT-F03` | `core_algorithm` | `P02_skill_formats_not_only_code` | `llm_smoke` | “已知文件名直接 diff，不要 ls” trace | 产出 rule/workflow card，不是 executable function |
| `EXT-C04` | `EXT-F04` | `core_algorithm` | `P07_single_responsibility` | `llm_smoke` | 一个 trace 同时有参数名错误和多余工具调用 | 拆成多个 narrow skills 或拒绝 broad skill |
| `EXT-C05` | `EXT-F05` | `contract_regression` | `P26_dependency_graph` | `offline` + `llm_smoke` | 新 skill 引用已有 shared convention | dependencies / dependency_pins 显式记录 |
| `EXT-C06` | `EXT-F06` | `contract_regression` | `P06_forward_interface_contract` | `offline` + `llm_smoke` | extractor 输出 artifact | interface 字段完整、非模板、与 body 一致 |

### 3.3 Bundle Builder 测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `BUN-C01` | `BUN-F01` | `core_algorithm` | `P12_skill_scoped_bundle_builder` | `llm_smoke` | 完整 trace + target skill | bundle_builder 只保留 skill-relevant fragment |
| `BUN-C02` | `BUN-F02` | `core_algorithm` | `P13_success_and_failure_attribution` | `llm_smoke` | 成功 trace、broken trace、integration failure | 生成 positive/negative/integration 三类 cases |
| `BUN-C03` | `BUN-F03` | `contract_regression` | `P14_bundle_result_separation` | `offline` | 同 bundle 连续跑两次 | bundle 无运行指标，results 独立 |
| `BUN-C04` | `BUN-F04` | `contract_regression` | `P15_bundle_version_independent` | `offline` | skill 不变但追加 integration case | bundle_version 增加，skill version 不变 |
| `BUN-C05` | `BUN-F05` | `contract_regression` | `P23_interface_change_requires_test_update` | `offline` | interface 改但 tests 未同步 | validator 拒绝 refine |

### 3.4 Unit Tester 测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `TST-C01` | `TST-F01` | `core_algorithm` | `P16_unit_with_without_utility` | `llm_smoke` | 单 skill bundle positive case | 输出 correctness + cost deltas 和 utility_label |
| `TST-C02` | `TST-F02` | `core_algorithm` | `P21_refine_until_full_bundle_pass` | `offline` + `llm_smoke` | 多 case bundle | 正式 pass 必须覆盖全部 cases |
| `TST-C03` | `TST-F03` | `core_algorithm` | `P17_negative_skill_detection` | `llm_smoke` | 注入错误 skill | with-skill 负贡献被报告 |
| `TST-C04` | `TST-F04` | `infrastructure_check` | `P36_logging_role_io` | `offline` + `llm_smoke` | 任意 bundle replay | SkillTestCaseRun.trace 可视化 |

### 3.5 Refiner 测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `REF-C01` | `REF-F01` | `contract_regression` | `P20_semantic_refinement_llm` | `llm_smoke` | failed test result + broken skill | LLM refiner 输出合法 schema 决策 |
| `REF-C02` | `REF-F02` | `core_algorithm` | `P22_disable_as_fallback_only` | `llm_smoke` | targeted broken skill | repair/rollback/disable 分类清楚，disable 不算 repair |
| `REF-C03` | `REF-F03` | `core_algorithm` | `P03_correctness_reusability_maintainability` | `llm_full` | 一次修复不足以通过全部 cases | 多轮 refine 直到 pass 或 limit |
| `REF-C04` | `REF-F04` | `contract_regression` | `P24_minor_update_test_monotonicity` | `offline` | minor refine 删除旧 case | validator 拒绝 |
| `REF-C05` | `REF-F05` | `contract_regression` | `P25_major_update_lineage` | `offline` | major interface migration | lineage 和 migration_reason 完整 |

### 3.6 Stale Resolver / Store 测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `STA-C01` | `STA-F01` | `contract_regression` | `P27_stale_propagation` | `offline` | A 依赖 B，B 更新 | A 标 stale，B 旧版本保留 |
| `STA-C02` | `STA-F02` | `core_algorithm` | `P28_lazy_stale_resolution` | `llm_smoke` | stale A 被 query 命中 | stale_resolver 决定 refresh/pin/keep |
| `STA-C03` | `STA-F03` | `core_algorithm` | `P29_legacy_version_pinning` | `offline` + `llm_smoke` | B major breaking | A pin B old，result 记录 dependency_versions |
| `STR-C01` | `STR-F01` | `contract_regression` | `P01_external_policy_asset` | `offline` | 同名 artifact 多次 add | version/history/lineage 正确 |
| `STR-C02` | `STR-F02` | `contract_regression` | `P14_bundle_result_separation` | `offline` | 同名 skill 更新缺 bundle | 继承/合并旧 bundle，不丢 cases |
| `STR-C03` | `STR-F03` | `contract_regression` | `P30_rollback_non_destructive` | `offline` | v2 bad 后 rollback | rollback 非破坏，history 可追踪 |
| `STR-C04` | `STR-F04` | `contract_regression` | `P14_bundle_result_separation` | `offline` | 同 bundle 两次测试 | 两个独立 result_id |

### 3.7 Refactorer 测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `RFA-C01` | `RFA-F01` | `core_algorithm` | `P31_micro_refactor_trigger` | `offline` + `llm_full` | K 个相关 tasks 积累 repeated evidence | 到 K 后触发 refactor scan |
| `RFA-C02E` | `RFA-F02` | `core_algorithm` | `P32_shared_reusable_extraction` | `llm_smoke` | 多个 skill 明文重复同一参数约定 | 抽 shared_subdoc，相关 skill 引用 |
| `RFA-C02M` | `RFA-F02` | `core_algorithm` | `P32_shared_reusable_extraction` | `llm_full` | 文本不同但 workflow 结构相同 | agent 判断 shared workflow 并排除不相关 candidate |
| `RFA-C02H` | `RFA-F02` | `core_algorithm` | `P32_shared_reusable_extraction` | `llm_full` | 表面工具/任务不同但底层接口传播规则相同 | agent 输出 reuse rationale 和引用改写 |
| `RFA-C03` | `RFA-F03` | `contract_regression` | `P33_refactor_correctness_preserving` | `offline` + `llm_full` | refactor 破坏一个 affected skill | 整组 rollback |
| `RFA-C04` | `RFA-F04` | `core_algorithm` | `P42_duplicate_skill_consolidation` | `llm_full` | 多个语义重复 skill + bundle/evidence/dependencies | 合并到 canonical skill，冗余 skill archived，tests 全过 |
| `RFA-C05` | `RFA-F05` | `core_algorithm` | `P43_overmerge_split` | `llm_full` | 过宽 skill 或表面相似但不应合并候选 | split 或 reject merge，测试资产不丢 |

### 3.8 Integration 核心测例

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `INT-C01` | `INT-F01` | `core_algorithm` | `P18_integration_test_real_trace` | `llm_smoke` | 真实 train trace replay | 发现多 skill 冲突/误检索并生成 failure |
| `INT-C02` | `INT-F02` | `core_algorithm` | `P04_online_future_help` | `llm_smoke` | 困难题从空库重复多轮 | loop0 提 skill，loop1+ 检索使用并改善 |
| `INT-C03A` | `INT-F03A` | `core_algorithm` | `P04_online_future_help` | `llm_smoke` | 3 个同族 query，实体不同但工具契约相同 | query1 提 skill，query2/3 acc 不降且 token/steps 下降 |
| `INT-C03B` | `INT-F03B` | `core_algorithm` | `P20_semantic_refinement_llm` | `llm_smoke` | 上轮有效 skill + targeted broken + 下一条相关 query | 失败触发 semantic repair 或 rollback，disable 分开计数 |
| `INT-C03C` | `INT-F03C` | `core_algorithm` | `P19_failure_to_bundle_feedback` | `llm_smoke` | 多 skill 共存导致一个局部 failure | failure 变 skill-scoped integration case |
| `INT-C03D` | `INT-F03D` | `core_algorithm` | `P17_negative_skill_detection` | `llm_smoke` | 同域但契约相反的 skills + 易误检索 query | 负迁移被 utility/refine 识别 |
| `INT-C03E` | `INT-F03E` | `core_algorithm` | `P26_dependency_graph` | `llm_full` | A 负责 id 获取，B 负责下游调用 | 多 skill 协作、dependency、stale propagation 正确 |
| `INT-C03F` | `INT-F03F` | `core_algorithm` | `P31_micro_refactor_trigger` | `llm_full` | 至少 K 个相关 query 积累重复证据 | 到 K 后 agent 判断 shared reusable part |

### 3.9 基础设施检查测例

这些测例用于保证实验可审计和前端可用，但不作为论文算法效果的主要证据。

| Case ID | Feature | Suite | Paper Point | Mode | Query / Context | Expected Behavior |
|---|---|---|---|---|---|---|
| `LOG-C01` | `LOG-F01` | `infrastructure_check` | `P36_logging_role_io` | `llm_smoke` | 任意完整实验 | role I/O 全部落 debug_events / audit JSONL |
| `LOG-C02` | `LOG-F02` | `infrastructure_check` | `P35_retrieval_auditability` | `offline` + `llm_smoke` | 任意检索事件 | repo summary 和分数明细完整 |
| `LOG-C03` | `LOG-F03` | `infrastructure_check` | `P40_ablation_hooks` | `llm_smoke` | 任意完整实验 | 按 role 汇总调用数、耗时、token |
| `UI-C01` | `UI-F01` | `infrastructure_check` | `P37_visualizable_artifacts` | `offline` | results directory | experiment list 可发现 |
| `UI-C02` | `UI-F02` | `infrastructure_check` | `P37_visualizable_artifacts` | `offline` | experiment payload | 每 turn page 数据流可显示 |
| `UI-C03` | `UI-F03` | `infrastructure_check` | `P37_visualizable_artifacts` | `offline` | skills/bundles/results | explorer 结构化展示核心产物 |
| `UI-C04` | `UI-F04` | `infrastructure_check` | `P37_visualizable_artifacts` | `offline` | 任意 raw payload | tree JSON 可展开，不溢出 |
| `REP-C01` | `REP-F01` | `infrastructure_check` | `P39_trajectory_analysis` | `offline` + `llm_smoke` | with/without experiment result | 全指标和轨迹差异完整报告 |
| `REP-C02` | `REP-F02` | `infrastructure_check` | `P38_benchmark_agnostic_boundary` | `offline` | method validation suite result | paper_point_id coverage table |

## 4. 论文技术点覆盖索引

| Paper Point | 覆盖测例 |
|---|---|
| `P01_external_policy_asset` | `EXT-C02`, `STR-C01` |
| `P02_skill_formats_not_only_code` | `EXT-C03` |
| `P03_correctness_reusability_maintainability` | `REF-C03` |
| `P04_online_future_help` | `INT-C02`, `INT-C03A` |
| `P05_model_skill_use_precondition` | `EXE-C04` |
| `P06_forward_interface_contract` | `EXT-C06` |
| `P07_single_responsibility` | `EXT-C04` |
| `P08_trace_to_skill_extraction` | `EXT-C01` |
| `P09_pre_execution_retrieval` | `EXE-C01` |
| `P10_error_aware_reretrieval` | `EXE-C02` |
| `P11_post_execution_maintenance` | `EXE-C03` |
| `P12_skill_scoped_bundle_builder` | `BUN-C01` |
| `P13_success_and_failure_attribution` | `BUN-C02` |
| `P14_bundle_result_separation` | `BUN-C03`, `STR-C02`, `STR-C04` |
| `P15_bundle_version_independent` | `BUN-C04` |
| `P16_unit_with_without_utility` | `TST-C01` |
| `P17_negative_skill_detection` | `TST-C03`, `INT-C03D` |
| `P18_integration_test_real_trace` | `INT-C01` |
| `P19_failure_to_bundle_feedback` | `INT-C03C` |
| `P20_semantic_refinement_llm` | `REF-C01`, `INT-C03B` |
| `P21_refine_until_full_bundle_pass` | `TST-C02` |
| `P22_disable_as_fallback_only` | `REF-C02` |
| `P23_interface_change_requires_test_update` | `BUN-C05` |
| `P24_minor_update_test_monotonicity` | `REF-C04` |
| `P25_major_update_lineage` | `REF-C05` |
| `P26_dependency_graph` | `EXT-C05`, `INT-C03E` |
| `P27_stale_propagation` | `STA-C01` |
| `P28_lazy_stale_resolution` | `RET-C03`, `STA-C02` |
| `P29_legacy_version_pinning` | `STA-C03` |
| `P30_rollback_non_destructive` | `STR-C03` |
| `P31_micro_refactor_trigger` | `RFA-C01`, `INT-C03F` |
| `P32_shared_reusable_extraction` | `RFA-C02E`, `RFA-C02M`, `RFA-C02H` |
| `P33_refactor_correctness_preserving` | `RFA-C03` |
| `P34_retrieval_not_keyword_only` | `RET-C01` |
| `P35_retrieval_auditability` | `RET-C02`, `LOG-C02` |
| `P36_logging_role_io` | `TST-C04`, `LOG-C01` |
| `P37_visualizable_artifacts` | `UI-C01`, `UI-C02`, `UI-C03`, `UI-C04` |
| `P38_benchmark_agnostic_boundary` | `REP-C02` |
| `P39_trajectory_analysis` | `REP-C01` |
| `P40_ablation_hooks` | `LOG-C03` |
| `P41_compact_skill_budget` | `RET-C04` |
| `P42_duplicate_skill_consolidation` | `RFA-C04` |
| `P43_overmerge_split` | `RFA-C05` |

## 5. 实施优先级

### P0: 必须先补齐

- `TST-C02`: 正式 bundle test 必须覆盖全部 cases，不能只跑第一个 case。
- `BUN-C01`: bundle builder 必须 LLM-driven 且 skill-scoped。
- `REF-C02`: targeted broken 必须验证 semantic repair / rollback / disable 的区别。
- `INT-C03A`: 相关 query 连续运行时必须证明上轮 skill 能帮助后续 query。
- `INT-C03B`: 手工破坏上轮有效 skill 后，后续 query 必须能触发恢复路径。
- `STR-C02`: 同名 skill 更新不得丢 bundle。

### P1: 方法闭环 smoke

- `INT-C02`: 困难题从零多轮 evolve。
- `INT-C03C`: integration failure 回写成 skill-specific bundle case。
- `INT-C03D`: 检索污染和负迁移可被识别。
- `RFA-C04`: 重复 skill 合并和安全归档可降低冗余，不破坏测试。
- `EXE-C01`: 每 turn 检索。
- `EXE-C02`: 报错后重新检索。
- `EXE-C04`: 高贴合 skill retrieved-to-used conversion。

### P2: 论文完整覆盖

- `STA-C02` / `STA-C03`: stale lazy handling 和 legacy pinning。
- `INT-C03E`: 多 skill 协作、接口传播和依赖 stale。
- `INT-C03F` / `RFA-C02E/M/H`: K-step micro-refactor 和不同难度 shared extraction。
- `RET-C04`: compact skill set 对检索噪声和 token budget 的影响。
- `RFA-C05`: over-merge detection / split。
- `infrastructure_check`: audit、UI、reporter，仅在对应代码变化或正式报告前运行。

## 6. 验收规则

一次 method validation run 只有在以下条件满足时才可称为通过：

- 每个被执行测例都能回溯到 `feature_id` 和 `paper_point_id`。
- 所有 with/without tests 都必须报告 accuracy/validity/tokens/steps deltas 和 utility_label。
- `work` 必须满足 acc/validity 不降且 tokens 或 steps 下降；不能只凭正确率不降或正确率提升判定 skill work。
- 所有真实 LLM role 都有 raw input/output audit。
- 所有 bundle test result 都有 per-case replay trace。
- 所有 pass/fail 都有机器断言，不只依赖人工观感。
- disable fallback 不被计入 semantic refine 成功。
- partial / missing 必须在报告中显式列出，不允许静默跳过。
