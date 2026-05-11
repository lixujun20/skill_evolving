# BFCL Evolve Debug Report

本文档维护本轮 `BFCL-v3 + GLM-4.7` evolve 调试过程，目标是达到端到端同时提升：

- `official_valid_rate` 上升
- 测试期总 token 开销下降
- 测试期模型步数下降

## 1. Baseline Anchor

- benchmark: `bfcl_v3`
- model: `GLM-4.7`
- data source: `bfcl_eval_bundle`
- adapter mode: `official`
- execution backend: `official`
- prompt style: `native`
- tool api style: `auto`
- baseline result:
  - file: `academic/results/bfcl_v3_glm47_official_main_none_50_150_baseline.json`
  - `official_valid_rate = 0.76`
  - `avg_score = 0.7836`
  - `avg_total_tokens = 65672.7`
  - `avg_model_steps = 10.83`

## 2. Method Discipline

本轮允许通过 case study 获得启发，但最终方法必须与个别 case 解耦：

- 不允许针对单个 task id、单个 booking id、单个文本值做特判
- 只允许引入跨多个 case 重复出现、可在 validation subset 上验证的通用机制
- 若某项技巧只能改善个别 case，且无法在更大固定子集上复现，则删除

## 3. Fixed Subsets

### debug_hard_cases

- purpose: 用于观察 failure pattern 和形成 case study
- source: 从 baseline 结果自动筛选，优先保留困难、非饱和、非极端超时 case
- file: `academic/experiments/bfcl_case_lists/debug_hard_cases.json`

### validation_subset

- purpose: 用于检验某项方法是否超越个别案例
- source: 从 baseline 结果自动筛选的固定较大子集
- file: `academic/experiments/bfcl_case_lists/validation_subset.json`

## 4. Experiment Loop Template

每轮实验按以下结构补充。

### Round N

- hypothesis:
- implementation change:
- why this is generic:
- expected effect on:
  - accuracy:
  - token cost:
  - model steps:

#### Case Study Notes

- observed recurring pattern:
- representative cases:
- abstracted mechanism:
- rejected case-specific interpretations:

#### Results

- debug_hard_cases:
- validation_subset:
- main_setting:

#### Decision

- keep / refine / revert:
- reason:

## 5. Current Round Status

- round 0:
  - completed:
    - BFCL large-scale baseline finished
    - hard-case failure patterns summarized
    - case-list runner support added
    - richer BFCL compare outputs added
    - fixed `debug_hard_cases` and `validation_subset` generated
    - first conservative evolve extractor upgrade added
    - first generic retrieval reranking / gating added
    - `debug_hard_cases` baseline completed
  - next:
    - finish first `train50 -> debug_hard_cases` evolve run
    - compare baseline vs evolve

### Round 1

- hypothesis:
  - 在 BFCL 上，第一阶段更值得尝试的不是复杂 functional skill，而是保守的参数规则卡、literal text 卡和 repeated extra-call negative 卡。
  - 如果这些卡片只在与当前 turn 的 tool/domain 强相关时注入，可能提升 `official_valid_rate`，同时减少无意义试探调用。
- implementation change:
  - 新增 case-list 运行支持
  - 新增 baseline-vs-evolve token / step / error delta 比较
  - extractor 从单一 observed-params / feedback 卡，扩展到：
    - `atomic_tool_rule_card`
    - `negative_rule_card`
    - `literal_text_rule_card`
  - retrieval 增加按 `allowed_tools/source evidence` 的 rerank
- why this is generic:
  - 所有卡片都来自跨多个 case 的重复错误统计，而非单个 case 的手工修补
  - 注入条件依赖当前 turn 的 domain / expected tools / skill provenance，不依赖具体 task id

#### Case Study Notes

- observed recurring pattern:
  - `extra_call` 是 hard cases 中最主导的问题
  - 第二类是 `argument_mismatch`，主要体现为：
    - 参数名 alias / missing arg
    - 文本字段扩写
  - 第三类是 multi-action turn 下的 `missing_call`
- representative cases:
  - `multi_turn_base_63`, `93`, `154`, `165`, `178`, `188`
- abstracted mechanism:
  - “少调且准调”比“多调求稳”更重要
  - 文本字段在 BFCL strict checker 下应保持 literal
  - 参数规则应当是 tool-specific 的，而不是泛泛流程建议
- rejected case-specific interpretations:
  - 不对具体 booking id / city / message 文本做特判
  - 不引入只服务于单个 case 的 hard-coded pattern

#### Results

- debug_hard_cases baseline:
  - file: `academic/results/bfcl_v3_glm47_bfcl_debughard_none_v1_baseline.json`
  - `official_valid_rate = 0.1818`
  - `avg_score = 0.7398`
  - `avg_total_tokens = 55916.4`
  - `avg_model_steps = 8.91`
- per-case notes:
  - `multi_turn_base_24` / `93` 已 official valid，可用作“skill 不应伤害 baseline”的监测点
  - 其余大多数 case 仍是非饱和失败，适合用于第一轮 evolve 调试

#### Decision

- keep:
  - `debug_hard_cases` 作为第一阶段调试入口
  - 保守规则卡 + gated retrieval 路线
- pending:
  - 第一轮 evolve 结果尚未完成，需要进一步确认是否真的改善三目标

### Round 2

- hypothesis:
  - 目前第一轮方法存在两个会直接误导实验判断的通用问题：
    - skill retrieval 使用了 `task.expected` 做 turn-level tool gating / rerank，属于 benchmark leakage
    - train `call_errors` 中混入了 local scorer 与官方命名 schema 不一致导致的伪 `arg0`/位置参数噪声，如果直接拿来抽 skill，会把错误反馈学成错误规则
  - 修复这两个问题后，再补足 `missing_call` / `extra_call` 对应的 workflow guardrail cards，才有资格判断 skill evolving 是否真的有效
- implementation change:
  - `bfcl.py`
    - 移除基于 `task.expected` 的 turn-level expected-tool gating / rerank
    - 将 skill rerank 改为仅依赖：
      - 当前 turn/query 与 tool name / tool description 的词法重合
      - train 反馈支持度
    - `prompt_only` / `hybrid` 模式下允许 `workflow` 类 skill 进入 prompt
  - `artifacts.py`
    - 检索分词支持 snake_case / camelCase 展开，减少 tool-name 与自然语言 query 的错配
  - `bfcl_skills.py`
    - 从 BFCL tool schema 中读取真实参数名与描述，过滤 local scorer 产生的伪 `arg0` 纠错信号
    - extractor 从“前若干正例工具参数卡”为主，改为“高频失败族优先”的 skill 生成策略
    - 新增通用 workflow cards：
      - `bfcl_complete_call_*`
      - `bfcl_turn_followthrough`
      - `bfcl_direct_action_bias`
    - literal / exact-arg 规则现在只保留高支持度、非 scorer artifact 的卡
  - `run.py`
    - 新增 `--load-train-details`，允许后续直接复用固定 train rollout 做 extract+test，缩短调参闭环
- why this is generic:
  - 所有新增卡片都来自跨训练集重复出现的 failure family，不依赖特定 task id / 文本值 / pattern
  - 去噪逻辑基于 tool schema 与 scorer representation 的一般不一致，不是针对个别 case 的硬编码
  - 训练缓存只改变实验速度，不改变算法与指标

#### Case Study Notes

- observed recurring pattern:
  - `extra_call` 是全量 baseline 中最主导的失败族（397 次）
  - `missing_call` 虽然频次更低（32 次），但往往直接导致本 turn 没完成
  - `argument_mismatch` 中一部分是有效信号（如 `retrieve_invoice:insurance_id`、`contact_customer_support:message`），另一部分是 scorer artifact（如 gold trace 使用位置参数而 tool schema 使用命名参数）
- representative examples:
  - `get_zipcode_based_on_city`
    - local scorer 报 `missing:arg0` + `unexpected:city`
    - 官方 tool schema 实际要求参数名就是 `city`
    - 说明这类 signal 不能直接进入 extractor
  - `contact_customer_support`
    - 真实失败族表现为 message 字段扩写、或者 turn 提前结束导致没完成 call
- abstracted mechanism:
  - skill learning 的反馈源必须先“去表示噪声”，否则学习到的是 scorer artifact 而非可迁移规则
  - 对 BFCL 这类 strict tool-calling 环境，workflow guardrail 比泛泛参数卡更可能同时影响 accuracy / token / steps
- rejected case-specific interpretations:
  - 不为任何单个 task id 写补丁
  - 不写“括号错误”“某城市名”“某 booking id”等特定 pattern 修复

#### Interim Sanity Check

- full-baseline offline extraction after de-noising:
  - pseudo `arg0` cards for `get_zipcode_based_on_city` / `cd` 已消失
  - workflow cards successfully appear:
    - `bfcl_complete_call_contact_customer_support`
    - `bfcl_turn_followthrough`
    - `bfcl_direct_action_bias`
  - prompt retriever intent keywords no longer include large amounts of BFCL boilerplate

#### Status

- running:
  - `train50 -> debug_hard_cases` evolve rerun with generic fixes
  - tag: `glm47_bfcl_debughard_evolve_v2`
- next check:
  - train phase completion
  - evolved skill file contents
  - test-phase `prompt_injected_skills`
  - baseline vs evolve delta on `official_valid_rate / avg_total_tokens / avg_model_steps`
