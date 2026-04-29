# Skill Evolution 同期工作 Benchmark 调研报告

日期：2026-04-28

## 1. 目标

本报告不是做一个独立的 benchmark 研究，而是为我们后续主实验选择环境。评价标准是：

- 是否被同期 skill evolution / agent evolution 工作使用；
- 是否存在足够显式的 skill 复用空间；
- 是否有稳定、低成本的反馈信号；
- 是否能在我们的 frozen-model + skill repository 系统中轻量跑通；
- skill format 是否自然，不需要强行把经验写成 Python 函数。

核心结论：第一阶段最适合轻量跑通的是 `ALFWorld` 或 `BFCL-v3 multi-turn base`。`ALFWorld` 更适合 workflow / step skill，`BFCL-v3` 更适合 tool/API rule skill。`WebShop`、`AppWorld`、`τ²-Bench` 适合作为第二阶段；`SkillsBench` 适合作为 skill package/verifier 专项；`GAIA/HLE`、`WildClawBench`、多模态 benchmarks 更适合在主流程跑通后作为外推讨论或后续实验。

## 2. 全量 Benchmark Map

| Benchmark / 环境 | 出现的同期工作 | 任务类型 | 反馈信号 | Skill 复用潜力 | 工程成本 | 适合我们当前轻量实验 |
| --- | --- | --- | --- | --- | --- | --- |
| ALFWorld | SkillRL, D2Skill | 文本环境 embodied tasks | success/fail，步数 | 高，任务类型重复 | 中低 | 强推荐 |
| WebShop | SkillRL, D2Skill, SkillX 引用 | 网页购物交互 | score/success | 高，搜索/筛选/比较流程重复 | 中 | 推荐第二阶段 |
| BFCL-v3 | SkillX | 函数调用/多轮工具任务 | function call correctness | 高，tool schema / 参数规则复用 | 低到中 | 强推荐 |
| AppWorld | SkillX | app/API 世界中的长程多步任务 | task success | 高，domain workflow 复用 | 中高 | 第二阶段 |
| τ²-Bench | SkillX | telecom/retail/airline 等用户交互任务 | pass rate | 高，客服 workflow 复用 | 中高 | 第二阶段或后续 |
| SkillsBench | CoEvoSkills, Trace2Skill 引用 | agent skill package 任务 | deterministic verifier | 高，但偏 skill creation package | 中 | 专项实验 |
| WildClawBench | SkillClaw | 真实长程 agent 任务，多模态/工具 | end-to-end score | 高 | 高 | 暂不推荐首轮 |
| OfficeQA | EvoSkill | 财政部文档 grounded QA | exact/fuzzy match | 中高，文档推理流程复用 | 中 | 可做轻量 QA/skill transfer |
| SealQA | EvoSkill | noisy web search QA | accuracy | 中，搜索策略复用 | 中 | 可做搜索 skill 小实验 |
| BrowseComp | EvoSkill transfer | hard web browsing/search | accuracy | 中 | 高 | 适合 transfer 评估，不适合首轮 |
| GAIA | Memento-Skills | 通用助手复杂任务 | answer correctness/judge | 中，但任务异质性强 | 高 | 不推荐首轮 |
| HLE | Memento-Skills | 高难知识问答 | accuracy/judge | 中，按学科 taxonomy 复用 | 高 | 不推荐首轮 |
| SpreadsheetBench-Verified | Trace2Skill | spreadsheet manipulation | subtask/all-pass | 高，xlsx workflow 复用 | 中高 | 有价值但依赖 spreadsheet agent |
| WikiTableQuestions | Trace2Skill OOD | 表格问答 | exact match | 中 | 中 | 适合 OOD transfer |
| DAPO-Math / AIME | Trace2Skill, OpenClaw-RL tool-call | 数学推理 | answer correctness | 低到中 | 低 | 可做辅助，不宜主打 reuse |
| DocVQA | Trace2Skill | 文档视觉问答 | ANLS/accuracy | 中 | 高，多模态 | 后续 |
| VisualToolBench | XSkill | 视觉工具使用 | Avg@4/Pass@4 | 高 | 高，多模态工具 | 后续 |
| TIR-Bench | XSkill | multimodal/tool-integrated reasoning | Avg@4/Pass@4 | 中高 | 高 | 后续 |
| MMSearch-Plus | XSkill | 多模态搜索 | Avg@4/Pass@4 | 中 | 高 | 后续 |
| MMBrowseComp | XSkill | 多模态 browsing/search | Avg@4/Pass@4 | 中 | 高 | 后续 transfer |
| AgentVista | XSkill | 综合多模态 agent | Avg@4/Pass@4 | 中高 | 高 | 后续 |
| OSWorld-Verified | OpenClaw-RL | GUI agent | task success | 高 | 高 | 暂不推荐 |
| SWE-Bench-Verified | OpenClaw-RL | 软件工程修 bug | tests pass | 高 | 中高 | 可能适合 code skill，但开销高 |
| Terminal / SETA RL data | OpenClaw-RL | 终端任务 | command outcome | 高 | 中 | 可作为后续工具环境 |
| GSM8K personalization | OpenClaw-RL | 数学问答 + 用户偏好 | judge score | 低 | 低 | 不适合 skill reuse 主实验 |
| NQ / TriviaQA / PopQA / HotpotQA / 2Wiki / MuSiQue / Bamboogle | SkillRL | search-augmented QA | accuracy | 中，搜索/多跳策略复用 | 低到中 | 可作为轻量搜索策略实验 |

## 2.1 提出时间与当前剩余空间

本节回答“哪些 benchmark 已经被刷得很高，是否还有足够实验空间”。这里的“当前空间”不是严格 leaderboard gap，而是综合：

- 官方或公开 leaderboard 的 top score；
- 同期论文中的强结果；
- 是否存在明确人类/满分上界；
- 是否还有可解释的 failure mode；
- 是否容易被 benchmark-specific scaffolding 刷高。

| Benchmark / 环境 | 提出时间 | 当前公开水平 / 近期强结果 | 剩余空间判断 | 对我们实验的含义 |
| --- | --- | --- | --- | --- |
| ALFWorld | 2020 / ICLR 2021 附近 | 近期 agent/RL 工作常报告 90% 左右甚至更高；D2Skill 在部分设定到 90.6 或 95.3 | 中低 | 作为“主效果提升”空间有限，但适合验证 skill 是否能减少步数、token、错误类型和提高稳定性 |
| WebShop | 2022 | 原论文 best model 29%、human 59%；SkillRL/D2Skill 近期报告 WebShop success 70%+ | 中 | 仍有空间，但已经不是低基线；适合看购物流程 skill 是否提升稳定性和成本 |
| BFCL / BFCL-v3 | BFCL 初版 2024；v3 2025；v4 2026 | 官方 BFCL V4 overall top 约 77.47%；multi-turn top 约 77.38%，multi-turn base top 约 82.50% | 中 | 未完全饱和，且 multi-turn/tool-rule failure 可解释；很适合轻量闭环 |
| AppWorld | ACL 2024 | 原论文 GPT-4o 约 49% Test-Normal、30% Test-Challenge；SkillX 报告多模型仍有明显提升空间 | 高 | 很适合作为第二阶段主实验，但工程成本高 |
| τ²-Bench | 2025 | 公开资料显示强模型在部分 domain 可到 80%+，但 dual-control/一致性仍难 | 中 | domain workflow 复用强，但需要确认 harness 和数据可用性 |
| SkillsBench | 2026 | 官方 SkillsBench 站点 with-skills top 约 48.7%；CoEvoSkills 在论文 setting 报告 71.1% | 中高 | 不是饱和；但同赛道竞争很近，适合作 verifier-driven skill package 专项 |
| WildClawBench | 2026 | SkillClaw 报告多类任务从较低基线提升，但公开生态仍早期 | 高 | 空间大但工程重，不适合第一阶段 |
| OfficeQA / OfficeQA Pro | OfficeQA 2025；OfficeQA Pro 2026 | OfficeQA Pro 公开报告 frontier agent 仍低于 12%（web access setting） | 高 | 企业文档 grounded reasoning 仍有很大空间；适合 document/table skill，但需要文档解析链路 |
| SealQA | 2025 | 论文报告 o3/o4-mini 等 agentic models 在 Seal-0 仍很低；EvoSkill 从 26.6 到 38.7 | 高 | search-persistence skill 空间大，适合 strategy skill；但 web search 不稳定 |
| BrowseComp | 2025 | 2026 聚合 leaderboard 已出现 80%+ 高分；但不同版本/视觉扩展仍难 | 中低 | 原始 BrowseComp 可能已被强模型刷高，适合作 transfer 对照，不适合作第一主线 |
| GAIA | 2023 | 公开榜单和聚合榜显示强 agent 已有明显提升，但 Level 3 仍有 gap；验证集污染风险上升 | 中 | 任务异质性强，skill 触发率不稳定；不适合我们近期主线 |
| HLE | 2025 | 2026 强模型仍远低于专家人类，但 leaderboard 波动和数据质量争议较多 | 高但不适配 | 难度空间大，但不适合 skill reuse；更像知识/推理上限评测 |
| SpreadsheetBench-Verified | 2024 | 原 benchmark 报告 GPT-4o / spreadsheet products 仍明显低于 human；Trace2Skill 有较大提升 | 高 | 很适合 workflow skill，但需要 spreadsheet agent 工程 |
| WikiTableQuestions | 2015 | 老 benchmark，本身被刷高；Trace2Skill 作为 spreadsheet OOD transfer 使用 | 低到中 | 不适合作主环境，可作为 OOD transfer |
| DAPO-Math / AIME | DAPO 2025/2026；AIME 长期竞赛 | 强模型数学能力快速提升，AIME 类 benchmark 容易被 frontier model 刷高 | 低 | 继续做主环境不合适；只保留辅助对照 |
| DocVQA | 2020 | 文档 VQA 仍有工具/解析空间，但多模态模型进展快 | 中 | 可做后续多模态/document skill，不适合近期 |
| VisualToolBench | 2026 | Scale Labs 有公开 VTB leaderboard，仍区分 16 个 MLLM | 中高 | 空间尚可，但多模态工具链重 |
| TIR-Bench | 2025/2026 | XSkill 报告 Agent-KB 与 XSkill 有 10+ 点差距 | 中高 | 适合多模态 tool-integrated reasoning，后置 |
| MMSearch-Plus / MMBrowseComp / AgentVista | 2025/2026 | 新 benchmark，强模型仍未饱和；BrowseComp-V3 类视觉扩展仍很难 | 高 | 很适合后续外推，但近期工程不划算 |
| OSWorld-Verified | 2024 | 2026 AI Index 报告 OSWorld 从约 12% 提升到约 66.3%，接近但未达人类 | 中 | 已被大幅推进，但仍有 1/3 failure；工程成本高 |
| SWE-Bench-Verified | 2023/2024 | 2026 top 已接近 80%+，且存在 benchmaxxing / harness 敏感争议 | 低到中 | 不适合作 skill evolution 主证明，除非使用新鲜任务或 SWE-rebench |
| Terminal / Terminal-Bench | 2025/2026 | 近期榜单已到 70%+，但 scaffold 和资源影响很大 | 中 | 适合工具环境，但不应第一阶段引入 |
| GSM8K personalization | 2021 数据集；OpenClaw-RL 作为个性化场景使用 | 数学正确性本身已饱和，个性化 judge 另算 | 低 | 不适合 skill reuse 主实验 |
| NQ / TriviaQA / HotpotQA / 2Wiki / MuSiQue / PopQA / Bamboogle | 2017-2023 | 原 QA benchmark 多数已被强 RAG/agent 显著推进；SealQA 这类新 benchmark 更难 | 低到中 | 若做 search skill，优先选 SealQA 或新鲜搜索任务，而不是老 QA |

结论：

- `ALFWorld` 的主 success rate 已经偏高，不适合作为唯一“提点”证明；但它仍适合验证 workflow skill 是否减少错误和成本。
- `BFCL-v3 multi-turn base` 仍有中等空间，且最轻、最结构化，是当前更好的第一实验。
- `AppWorld`、`OfficeQA/Pro`、`SealQA`、`SpreadsheetBench` 的剩余空间更大，但工程成本或外部依赖更高。
- `AIME/MATH`、老 QA、SWE-Bench Verified、BrowseComp 原版都存在被强模型刷高或 contamination / harness 特化的问题，不适合作为主证明环境。

## 2.2 饱和榜单上的模型与论文卖点

很多 benchmark 的 raw leaderboard 已经由旗舰模型或专门 scaffold 刷高。同期 skill/self-evolution 工作通常不直接主张“超过所有旗舰模型”，而是通过更细的实验切口证明方法价值。

### 2.2.1 榜单上常见的强模型

| Benchmark / 环境 | 当前常见强模型或 scaffold | 典型现象 |
| --- | --- | --- |
| BFCL V4 / multi-turn | Claude Opus/Sonnet 4.5, Gemini 3 Pro Preview, GLM-4.6, Grok, xLAM function-calling models, Qwen function-calling models | 官方榜单 overall top 已到 70%+，multi-turn top 也在 70%+；专门 function-calling 模型在 multi-turn 子项上很强 |
| AppWorld | GPT-4o, Claude / GPT 类 ReAct 或 code-writing agents, 后续 SkillX 使用 GLM-4.6、Kimi、Qwen、DeepSeek、GPT-4.1 | GPT-4o 在原论文中 Test-Normal 仍低于一半、Test-Challenge 约三成，因此不是饱和榜 |
| ALFWorld / WebShop | Qwen2.5/Qwen3 + GRPO/SFT/RL scaffold, GPT-4o/Gemini 作为强 baselines，SkillRL/D2Skill 用 open Qwen policy | 经过 RL 后 success 很高；剩余空间更多体现在低成本、少训练步数、泛化和 skill utility |
| SkillsBench | Claude Code/Opus/Sonnet/Haiku, Codex/GPT, Gemini CLI, curated skills / self-generated skills | 单纯 no-skill 不高，curated skills 有显著增益；self-generated skills 平均不稳定 |
| SWE-Bench / Terminal-Bench | Claude Opus/Sonnet, GPT/o-series, Gemini, 专门 coding agent scaffold | 榜单已被强模型和 heavy scaffold 大幅推进，容易出现 harness 特化 |
| OfficeQA/Pro / SealQA | Claude Code/Opus, frontier web agents, search-augmented agents | 仍不饱和，尤其 OfficeQA Pro 与 SealQA 这种企业文档/噪声搜索环境 |

### 2.2.2 他们如何在强模型已高分时 sell 提升

1. 同模型 ablation，而不是直接打总榜。
   - 典型写法是固定 backbone、固定 budget，比较 `no skill`、`raw memory`、`retrieved skill`、`evolved skill`。
   - 例如 `D2Skill` 明确用同一 policy 的 baseline rollout 与 skill-injected rollout 差值估计 utility。

2. 改善弱模型或开源模型，而不是只追旗舰模型。
   - `SkillX` 用强 backbone 构建 skill library，再 plug into Qwen/Kimi/GLM 等模型，看弱一些的 agent 是否受益。
   - 这种叙事是：skill 是可转移资产，能把小模型/低成本模型往上拉。

3. 打细分子任务，而不是只看 overall。
   - `BFCL` 的 overall 很高，但 multi-turn、miss function、miss parameter、long context、format sensitivity 是可解释 failure mode。
   - `ALFWorld` overall 高时，可以看 task type、step count、失败类型、retries 和 token。

4. 强调 cost / token / training efficiency。
   - `SkillRL` 和 `D2Skill` 都强调 skill 相比 raw trajectory 更短、训练更有效或只引入 modest overhead。
   - 当 accuracy 提升空间变小时，token footprint、sample efficiency、step count 和稳定性会成为主要卖点。

5. 强调 cross-model / OOD transfer。
   - `Trace2Skill`、`CoEvoSkills`、`EvoSkill` 都会证明 evolved skill 不只是当前模型当前任务的 hack，而能迁移到其他模型或其他 benchmark。
   - 这是避免“只是在刷榜”的重要证据。

6. 强调 skill quality / repository governance。
   - `EvoSkill` 卖 create/edit + validation frontier；
   - `SkillClaw` 卖多用户共享 skill pool 和验证后同步；
   - `SkillsBench` 卖 curated skill 的正负效应和 self-generated skill 的不稳定性；
   - 这些都不是单纯 raw score，而是 skill 系统能否长期维护。

7. 选择未饱和或更贴近业务的新 benchmark。
   - `OfficeQA Pro`、`SealQA`、`AppWorld`、`WildClawBench`、多模态 tool-use benchmark 都是为了避开老榜单饱和。
   - 这也是我们不应继续主打 AIME/MATH 的原因。

### 2.2.3 对我们论文的启发

如果我们选 `BFCL-v3` 或 `ALFWorld`，不应只写“accuracy 提升”。更好的主张是：

- 同一模型下，skill evolution 相比 no-skill / raw-memory / static-skill 是否提升；
- retrieved skill 是否真的变成 selected/used skill；
- skill 是否降低 token、步数、tool-call 错误、格式错误；
- skill 是否跨 task type 或跨模型迁移；
- skill library 是否更紧凑，是否能通过 refactoring 降低冗余；
- 失败时是否能用反馈定位到 skill 缺陷并改写，而不是只重跑模型。

因此，当前更稳的实验口径不是“在某榜单刷 SOTA”，而是“在一个有剩余空间且可控的环境中证明 skill artifact 的可复用、可治理、可迁移价值”。

## 3. 重点 Benchmark 细节

### 3.1 ALFWorld

出现位置：`SkillRL`、`D2Skill`。

任务形态：文本版 embodied environment。agent 通过文本命令在房间中找物体、拿取、移动、清洁、加热、冷却、检查等。

典型任务类型：

- `pick_and_place`：找到某物，把它放到目标位置；
- `pick_clean_then_place`：找到物体，清洁后放置；
- `pick_heat_then_place`：找到物体，加热后放置；
- `pick_cool_then_place`：找到物体，冷却后放置；
- `look_at_obj`：找到并检查目标物体；
- `pick_two_obj`：拿两个同类物体并放到目标位置。

典型交互例子：

```text
Goal: put a clean apple in the fridge.
Action sequence:
look
go to countertop
take apple from countertop
go to sinkbasin
clean apple with sinkbasin
go to fridge
open fridge
put apple in fridge
```

为什么适合 skill evolving：

- 同一类任务共享 workflow，例如“找到物体 -> 操作物体 -> 放置物体”；
- step-level failure 很清晰，例如没打开容器、没拿起物体、目标 receptacle 错误；
- 成功/失败反馈明确；
- 不需要复杂外部 API；
- skill format 可以自然写成 `task skill + step skill`。

建议 skill format：

```yaml
type: task_skill | step_skill
trigger: clean_then_place / heat_then_place / cool_then_place
preconditions:
workflow_steps:
action_templates:
failure_checks:
negative_examples:
evidence_traces:
utility:
```

风险：

- 安装/运行环境可能有老依赖；
- 强模型可能无需 skill 也能做得较好，需要控制样本难度或使用较小模型/更低 budget；
- 如果 executor 不强制记录 selected/used skill，usage 统计仍会模糊。

当前判断：最适合第一轮主闭环。

### 3.2 WebShop

出现位置：`SkillRL`、`D2Skill`，也被 `SkillX` 作为相关长程交互 benchmark 引用。

任务形态：网页购物。用户给出需求，agent 搜索商品、打开结果、比较属性、选择选项并购买。

典型任务例子：

```text
Instruction: I am looking for a 3 ounce bottle of brightening facial serum
with vitamin C, price lower than 40 dollars.
Agent actions:
search[brightening facial serum vitamin C 3 ounce]
click[result item]
inspect price, size, ingredients
choose option
click[buy now]
```

为什么适合 skill evolving：

- 搜索 query rewrite、属性匹配、价格过滤、选项选择是高度重复流程；
- failure type 清晰，例如忽略尺寸、价格超限、买错变体；
- WebShop 本身有 score 和 success；
- 与 D2Skill/SkillRL 的 skill setting 很接近。

建议 skill format：

- `shopping_strategy_skill`：需求拆解、核心关键词、不可忽略约束；
- `attribute_check_skill`：价格、尺寸、品牌、材料、数量、颜色等检查表；
- `search_rewrite_skill`：如何把用户需求转成搜索关键词；
- `failure_check_skill`：购买前逐项核对。

风险：

- 运行环境比 ALFWorld 稍重；
- 页面状态和 action parser 可能需要适配；
- 强模型可能通过一般网页推理解决，skill 增益主要体现在 token/稳定性。

当前判断：第二阶段强候选。

### 3.3 BFCL-v3

出现位置：`SkillX`。

任务形态：函数调用 benchmark。v3 包含 multi-turn、multi-step、irrelevance detection、live 等类别。SkillX 使用 `base multi-turn`，并随机分 50 train / 150 test。

官方数据中的典型 multi-turn base 例子：

```text
Turn 1: Move 'final_report.pdf' within document directory to 'temp' directory.
        Make sure to create the directory.
Turn 2: Use grep to identify sections in the file pertaining to 'budget analysis'.
Turn 3: Sort the relevant final_report.pdf content by line for clarity.
Turn 4: Move 'previous_report.pdf' to temp and compare it with final_report.pdf
        to detect critical alterations.
```

为什么适合 skill evolving：

- tool/API schema 是显式结构；
- 很多任务共享文件系统操作、搜索、排序、比较、日历/API 组合等 pattern；
- 评测相对自动化；
- skill format 可以设计成 `atomic tool-use skill` 或 `multi-step function workflow card`；
- 工程成本低于 AppWorld/GUI/multimodal 环境。

建议 skill format：

```yaml
type: tool_rule_skill | workflow_skill
tool_scope: file_system / calendar / database / ...
trigger:
canonical_call_pattern:
argument_constraints:
multi_turn_state_update:
common_mistakes:
validation_checks:
```

风险：

- 它更像 function-calling/tool routing，不一定充分体现复杂 skill evolution；
- 如果模型有原生 tool-call 训练，skill 增益可能小；
- 需要把我们现有 executor 和 BFCL tool schema 对接。

当前判断：与 ALFWorld 并列第一轮候选。若目标是尽快跑通 pipeline，BFCL-v3 甚至更轻。

### 3.4 AppWorld

出现位置：`SkillX`。

任务形态：一个由多个 app/API 构成的可控世界，包含虚拟用户、数据库和长程任务。任务通常需要跨 app 调用，例如日历、邮件、购物、账户、支付、联系人等。

典型任务例子：

```text
User asks the assistant to reschedule a meeting, notify attendees,
check calendar conflicts, and update a related reminder.
```

为什么适合 skill evolving：

- 真实 app workflow 复用性强；
- 任务长，skill 有发挥空间；
- API schema 和数据库状态提供可验证反馈；
- 与 SkillX 的 multi-level skill design 很匹配。

建议 skill format：

- `planning_skill`：跨 app 任务拆解；
- `functional_skill`：如“重新安排会议并通知参与者”；
- `atomic_skill`：单个 API 的参数约束和常见错误。

风险：

- 工程成本明显高于 ALFWorld/BFCL；
- 需要适配 API world、状态初始化和评测；
- 适合作为第二阶段，不适合第一轮 pipeline debug。

当前判断：很适合最终主实验，但不适合第一步。

### 3.5 τ²-Bench

出现位置：`SkillX`。

任务形态：面向真实业务流程的用户交互任务，SkillX 中按 telecom、retail、airline 等 domain 报告结果。

典型任务例子：

```text
Telecom: 用户要求修改套餐、查询账单、处理网络问题。
Retail: 用户要求退换货、查询订单、修改配送信息。
Airline: 用户要求改签、查询航班政策、处理行李或座位需求。
```

为什么适合 skill evolving：

- domain workflow 高度重复；
- skill 可以写成 SOP / policy / tool-use rule；
- 与企业客服/SkillForge 方向有共同点。

风险：

- 需要确认数据和评测 harness 可用性；
- 用户交互和政策约束可能较复杂；
- 工程成本预计高于 BFCL，低于 WildClawBench/OSWorld。

当前判断：第二阶段候选，尤其适合 document/workflow skill。

### 3.6 SkillsBench

出现位置：`CoEvoSkills`，也被 `Trace2Skill` 引用。

任务形态：专门评测 agent skills 的 benchmark。CoEvoSkills 记录为 87 个任务、约 11 个 domain，每个任务有 deterministic verifier，评价为 pass/fail。

典型任务形态：

```text
给定一个任务环境和目标，agent 需要加载或生成一个 skill package，
skill package 可能包含 SKILL.md、scripts、references 等，
随后 agent 使用该 skill 完成任务并通过 verifier。
```

为什么适合 skill evolving：

- 与 Anthropic-style skill package 完全对齐；
- verifier 明确；
- 很适合测试 `generate-verify-refine` 和多文件 skill format。

不适合第一主线的原因：

- 它更像“为每个任务生成/改进 skill package”，不一定天然测试跨任务 skill library reuse；
- CoEvoSkills 已经在这个环境上做得很强，直接跟进容易被压在同一叙事下；
- 我们当前系统主线是 post-execute cross-trace refactoring / repository maintenance，需要重新设计任务分组才能体现差异。

当前判断：适合作为专项实验或 verifier-driven skill package ablation，不适合作为第一主实验。

### 3.7 WildClawBench

出现位置：`SkillClaw`。

任务形态：真实世界复杂 agent benchmark，60 个任务，覆盖六类能力：Productivity Flow、Code Intelligence、Social Interaction、Search & Retrieval、Creative Synthesis、Safety & Alignment。

典型任务例子：

```text
Productivity Flow: 整理多文件材料、生成报告、调用工具保存输出。
Search & Retrieval: 搜索、比较、归纳多源信息。
Creative Synthesis: 多模态素材处理与内容生成。
Code Intelligence: 理解和修改代码。
```

为什么有价值：

- 复用密度高；
- 真实长程、多工具、多模态；
- 非常接近生产级 agent skill evolution。

为什么暂不适合：

- 环境重；
- 评测成本高；
- 多模态/外部依赖会引入很多非算法变量；
- 不适合用来调试我们的第一版闭环。

当前判断：可作为 future work / high-fidelity setting，不作为近期目标。

### 3.8 OfficeQA

出现位置：`EvoSkill`。

任务形态：基于美国财政部公告文档的 grounded reasoning benchmark。EvoSkill 记录约 89,000 页文档、246 个问题，使用 fuzzy scorer 和多容差 exact match。

典型任务例子：

```text
Question: 根据某几年 Treasury Bulletin 表格，计算或比较某类财政指标。
Agent must locate relevant bulletin tables, extract rows/columns, perform calculation,
and return a normalized answer.
```

为什么适合 skill evolving：

- 文档检索 + 表格定位 + 数值计算流程可复用；
- scoring 相对稳定；
- skill 可写成 financial methodology / table extraction / multi-period comparison protocol。

风险：

- 数据规模小；
- 文档处理环境需要准备；
- domain-specific skill 可能迁移窄。

当前判断：适合做轻量 document-reasoning skill experiment，尤其可复现 EvoSkill 式 validation frontier。

### 3.9 SealQA / BrowseComp

出现位置：`EvoSkill`。

SealQA 任务形态：search-augmented QA，web search 结果可能冲突、噪声大或无帮助。EvoSkill 使用 seal-0 split，共 111 个问题，10% train，1.5 epochs。

BrowseComp 任务形态：hard browsing/search benchmark。EvoSkill 用 SealQA 学到的 search-persistence-protocol zero-shot 迁移到 BrowseComp。

典型任务例子：

```text
Question requires web search.
Initial search results conflict or are incomplete.
Agent must reformulate queries, check multiple sources,
avoid premature stopping, and provide final answer with evidence.
```

为什么适合 skill evolving：

- 搜索策略、验证策略、停止条件高度可复用；
- skill format 可以是 `search protocol`；
- 很适合测试 cross-benchmark transfer。

风险：

- web search 会引入时间不稳定性；
- 需要控制搜索 API 和成本；
- 评价可能依赖外部答案/benchmark harness。

当前判断：适合做第二阶段 search-strategy skill 实验，不适合第一轮工程闭环。

### 3.10 GAIA

出现位置：`Memento-Skills`。

任务形态：通用 AI assistant benchmark，包含真实世界复杂问题，常需要网页、文件、计算、推理和多步工具使用。

典型任务例子：

```text
Find an entity from clues, inspect sources, perform a calculation,
and output a specific answer format.
```

优点：

- 任务真实、多样；
- 可测试 generalist skill memory；
- 与 Memento-Skills 的 read-write-reflect 和 router 训练强相关。

问题：

- 任务异质性太强，skill 在 train 学到后 test 不一定触发；
- Memento-Skills 自己也指出 GAIA cross-task transfer 有限；
- 运行和评测成本高。

当前判断：不适合第一轮主实验。

### 3.11 Humanity's Last Exam (HLE)

出现位置：`Memento-Skills`。

任务形态：高难知识与推理问答，覆盖多学科类别。Memento-Skills 使用 788 train / 342 test，按学科 taxonomy 组织。

优点：

- 学科结构让 skill 更容易在同领域复用；
- 可测试 broad knowledge skill 和 router。

问题：

- 难度高，依赖强模型；
- 反馈和评测不一定适合轻量本地闭环；
- 很难区分 skill gain 和模型知识本身。

当前判断：后续可作为 high-level strategy skill 的外部验证，不适合近期跑通。

### 3.12 SpreadsheetBench-Verified / WikiTableQuestions

出现位置：`Trace2Skill`。

任务形态：

- SpreadsheetBench-Verified：真实 spreadsheet manipulation / spreadsheet QA；
- WikiTableQuestions：表格问答，被 Trace2Skill 转成 spreadsheet 格式做 OOD generalization。

典型任务例子：

```text
Open spreadsheet, inspect sheet names and headers,
filter relevant rows, compute aggregate, write answer or modified sheet.
```

为什么适合 skill evolving：

- spreadsheet 操作具有高度 SOP；
- workflow skill 明确，例如 inspect schema、avoid hidden rows、validate formulas；
- skill 可迁移到 OOD table QA。

风险：

- 需要 spreadsheet agent / xlsx toolchain；
- 与 Anthropic xlsx skill 竞争，baseline 可能强；
- 工程上比 ALFWorld/BFCL 更重。

当前判断：如果我们愿意做 document/spreadsheet skill，是很好的方向；但第一阶段不建议。

### 3.13 DAPO-Math / AIME

出现位置：`Trace2Skill`，OpenClaw-RL tool-call track 也使用 AIME 2024。

任务形态：数学推理/竞赛题。

优点：

- 我们已有 academic pipeline；
- evaluation 简单；
- 成本低。

问题：

- 显式 skill reuse 稀疏；
- 强模型 baseline 容易饱和；
- code-function skill 调用不稳定；
- 很多题依赖一次性 insight。

当前判断：可保留为辅助对照，但不应作为证明 skill evolving work 的主环境。

### 3.14 DocVQA

出现位置：`Trace2Skill`。

任务形态：文档视觉问答，需要读取图像文档、定位信息、结合文本和布局回答。指标包括 ANLS 和 accuracy。

优点：

- 文档处理 workflow 可复用；
- Trace2Skill 证明 skill 能带来较明显提升。

问题：

- 多模态依赖；
- 工程成本高；
- 与我们当前代码路径不匹配。

当前判断：后续多模态扩展，不适合近期。

### 3.15 VisualToolBench / TIR-Bench / MMSearch-Plus / MMBrowseComp / AgentVista

出现位置：`XSkill`。

任务形态：

- VisualToolBench：视觉 agentic tool use；
- TIR-Bench：tool-integrated reasoning；
- MMSearch-Plus：多模态搜索；
- MMBrowseComp：多模态 browsing/search；
- AgentVista：综合多模态 agent tasks。

典型任务例子：

```text
Given an image and question, decide whether to use image crop,
OCR, web search, code interpreter, or browsing; combine tool outputs
and answer in required format.
```

为什么适合 skill evolving：

- XSkill 明确区分 experience 与 skill；
- 多模态工具选择和工作流具有复用性；
- 可测试 task decomposition retrieval。

为什么不适合近期：

- 多模态模型和工具链复杂；
- 评测和运行成本高；
- 与我们当前文本/代码 executor 差距大。

当前判断：强相关但后置。

### 3.16 OpenClaw-RL 相关环境：Terminal / GUI / SWE / Tool-call / GSM8K personalization

出现位置：`OpenClaw-RL`。

任务形态：

- Terminal：shell sandbox task；
- GUI：OSWorld-Verified 类 GUI task；
- SWE：SWE-Bench-Verified 类代码修复；
- Tool-call：AIME 2024 tool-call；
- Personal：GSM8K homework / grading personalization。

为什么有启发：

- next-state signal 可作为上一轮 action 的反馈；
- Terminal/SWE 的 process reward 和 test feedback 可转成 skill utility；
- tool-call 和 terminal task 适合 action-feedback chain。

为什么暂不主打：

- OpenClaw-RL 是参数更新/RL 方向，不是外部 skill repository 主线；
- GUI/SWE 成本高；
- GSM8K personalization 不强调跨任务 skill reuse。

当前判断：借鉴 feedback 设计，不直接作为第一 benchmark。

### 3.17 SkillRL 的 Search-Augmented QA：NQ / TriviaQA / PopQA / HotpotQA / 2Wiki / MuSiQue / Bamboogle

出现位置：`SkillRL`。

任务形态：open-domain / multi-hop / compositional search QA。SkillRL 在 NQ 和 HotpotQA 上训练，并报告 in-domain 与 OOD 搜索 QA 表现。

典型任务例子：

```text
Question requires one or more searches.
Agent must decompose query, retrieve evidence, verify source consistency,
and synthesize a short answer.
```

为什么适合 skill evolving：

- 搜索策略和多跳分解可复用；
- 数据集轻，成本低；
- 可做 natural-language strategy skill。

问题：

- 如果使用外部 search API，结果时间不稳定；
- 如果使用固定 corpus，则要搭建 retriever；
- skill gain 可能体现在 search discipline，而不是最终 answer。

当前判断：适合做轻量第二线，尤其用于 strategy skill format。

## 4. 推荐实验路径

### 4.1 第一阶段：跑通主闭环

建议二选一或都做：

1. `ALFWorld`
   - skill format：task skill + step skill；
   - 模型：Claude 作为 executor/extractor，后续加 GLM/Qwen ablation；
   - 指标：success、step count、token、retrieved/selected/used、skill utility；
   - 目标：证明 workflow skill 能被 frozen agent 使用并带来收益。

2. `BFCL-v3 multi-turn base`
   - skill format：tool rule skill + multi-turn workflow skill；
   - 模型：支持 tool/function calling 的 Claude 或 GPT/GLM function-call setting；
   - 指标：function correctness、turn-level error、retrieved/selected/used；
   - 目标：低成本跑通 tool-skill 的提取、检索和复用。

如果只能选一个，我建议先 `BFCL-v3`，因为它更轻、更结构化、和当前代码 executor 更容易对接；如果希望更贴近 `SkillRL/D2Skill`，则选 `ALFWorld`。

### 4.2 第二阶段：证明 skill evolution 有真实复用空间

推荐：

- `WebShop`：测试 shopping/search workflow skill；
- `AppWorld`：测试 app/API multi-step workflow skill；
- `SealQA` 或搜索 QA：测试 search-persistence strategy skill。

这三类可以分别对应三种 skill format：

- WebShop：shopping protocol；
- AppWorld：planning / functional / atomic skill；
- Search QA：natural-language strategy skill。

### 4.3 第三阶段：专项与外推

- `SkillsBench`：验证 multi-file skill package + verifier-driven refine；
- `SpreadsheetBench`：验证 document/spreadsheet workflow skill；
- `WildClawBench` / `OSWorld` / `SWE-Bench` / XSkill 多模态 benchmarks：作为高成本外推，不进入近期主线。

## 5. 对我们系统的具体设计建议

### 5.1 不同环境对应不同 skill format

| 环境 | 推荐 Skill Format |
| --- | --- |
| ALFWorld | task skill + step skill + failure check |
| BFCL-v3 | tool rule card + multi-turn workflow card |
| WebShop | shopping strategy + attribute checklist + search rewrite protocol |
| AppWorld | planning skill + functional skill + atomic API skill |
| Search QA | search-persistence protocol + source verification checklist |
| SkillsBench | SKILL.md + scripts/references + verifier feedback |
| Spreadsheet | spreadsheet workflow guide + formula/check scripts |

### 5.2 统一最小接口

无论环境如何，executor 应被要求输出结构化 usage：

```json
{
  "retrieved_skill_ids": ["..."],
  "selected_skill_ids": ["..."],
  "used_skill_ids": ["..."],
  "skill_usage_rationale": "...",
  "failure_or_success_evidence": "..."
}
```

这样可以区分：

- 检索到了但没选；
- 选了但没实际用；
- 用了但没收益；
- 用了且有 answer/token/step benefit。

### 5.3 统一反馈字段

每个环境都应记录：

```yaml
answer_success:
task_score:
token_cost:
step_count:
tool_call_count:
selected_skill_ids:
used_skill_ids:
failure_mode:
judge_feedback:
```

后续 Shapley-style value 或 paired ablation 才有数据基础。

## 6. 最终推荐

如果目标是尽快把我们自己的实验跑通，当前执行选择已经收敛到五个榜单：

1. `BFCL-v3 multi-turn base`：第一优先级，已经开始适配。原因是环境轻、任务结构化、工具调用正确性明确、skill format 清楚，且与 `SkillX` 的 50 train / 150 test setting 对齐。
2. `SpreadsheetBench-Verified`：第一阶段并行适配。原因是它和 `Trace2Skill` 的 spreadsheet agent setting 对齐，最适合验证 `SKILL.md + scripts/references` 这种非纯代码函数的 skill package。
3. `AppWorld`：第二阶段。它更真实，更能体现 app/API workflow skill，但工程成本明显高于 BFCL。
4. `OfficeQA / OfficeQA Pro`：第二阶段或文档 skill 专项。它适合 document/table reasoning workflow，但需要文档解析与 grounded QA 评分链路。
5. `TIR-Bench`：后置外推。它适合 tool-integrated reasoning 与多模态 skill，但工程成本高，暂不作为第一个闭环。

不建议近期继续把 AIME/MATH 作为主证明环境。它可以保留为 low-cost auxiliary math setting，但不适合作为 skill reuse 主结论来源。

当前代码层面已新增 `academic.benchmarks` 子系统：

- `bfcl_v3`：已实现 Hugging Face 数据 loader、tool-doc loader、本地 tool-call loop scaffold、基于 possible-answer 的 sequence scorer。
- `spreadsheet`：已实现 SpreadsheetBench-Verified 下载/解析、openpyxl baseline scaffold、answer range cell-level verifier。
- `appworld`、`officeqa`、`tir_bench`：先进入 registry，保留数据源、metric、skill format 和工程成本信息。

使用文档见 `academic/benchmarks/README.md`。

## 7. Sources

本报告综合以下本地文献笔记与 primary sources：

- `academic/paper/related_work_notes/trace2skill.md`
- `academic/paper/related_work_notes/skillx.md`
- `academic/paper/related_work_notes/coevo_skills.md`
- `academic/paper/related_work_notes/skillclaw.md`
- `academic/paper/related_work_notes/evoskill.md`
- `academic/paper/related_work_notes/memento_skills.md`
- `academic/paper/related_work_notes/skillrl.md`
- `academic/paper/related_work_notes/d2skill.md`
- `academic/paper/related_work_notes/xskill.md`
- `academic/paper/related_work_notes/skillforge.md`
- `academic/paper/related_work_notes/openclaw_rl.md`
- arXiv / PDF text cache under `/tmp/skill_evolving_papers/txt/`
- ALFWorld official repository: <https://github.com/alfworld/alfworld>
- WebShop official repository: <https://github.com/princeton-nlp/WebShop>
- AppWorld official repository: <https://github.com/StonyBrookNLP/appworld>
- BFCL dataset/example source: <https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard>
- SkillsBench: <https://www.skillsbench.ai/tasks>
