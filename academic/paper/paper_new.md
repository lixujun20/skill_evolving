# You Live More than Once: A Software Engineering's Perspective for Test-time Skill System Evolution

Test-time Skill Evolution被视为agentic system部署后不断积累新经验的一种全新范式。随着agentic system project不断复杂化，对大规模skill系统的构建和维护成为一个非常重要的需求。已有工作基本聚焦孤立skill的evolution方法论，而鲜有讨论skill作为软件工程单元的可复用性、可维护性问题。本篇工作将Skill Evolution建模为一个闭环的软件工程问题。将skill建模为可复用逻辑单元，从skill提取到迭代维护流程，都通过整合软件工程原则进行精细设计；通过基于Shapley Value的独立测试、集成测试对提取的Skill进行后验检验和筛选。在多样化的agentic任务场景下的测试结果表明，我们的方法在提升端到端性能的同时，显著降低了推理开销，提升了生成稳定性，且每个工具都得到了充分高效复用。

```
Test-time Skill Evolution is a novel paradigm for agentic systems to continuously accumulate new experiences after deployment. Despite many preliminary attempts, the focus has been primarily on isolated skill evolving effects for single tasks. The feasibility and specific approaches for cross-task skill reuse have been underexplored. In this work, we model Skill Evolution as a closed-loop software engineering problem. By modeling skills as reusable logical units, we design the entire process from skill extraction to iterative maintenance with fine-grained integration of software engineering principles. By using Shapley-Value-based unit tests and integrated tests as post-hoc validation criterion, we perform in-depth analysis and selection of the extracted skills. Testing results across diverse agentic task scenarios show that our method significantly reduces inference overhead and improves generation stability while enhancing end-to-end performance, with each tool being fully and efficiently reused.
```

## 1. Introduction


测试时技能演化（Test-time Skill Evolution, TTSE）正成为一种新范式，使智能体系统在部署后无需更新模型参数即可持续积累能力。在大规模部署中，智能体必须高效处理多样化且不断变化的需求；参数更新代价高昂、验证缓慢且存在性能回退风险。现有替代方案各自只能解决部分问题。检索增强生成可注入陈述性知识，但无法编码可复用的程序性逻辑。测试时训练和 LoRA 会调整模型权重，带来显著延迟和计算开销，并将能力获取紧密耦合到特定的模型快照上。记忆增强架构可积累交互事实，但缺乏结构化、可调用的技能抽象。测试时技能演化则占据了一个独特的设计位置：它增加了一个轻量、外部、可更新的可复用技能策略层，兼具专用工具的性能优势和动态任务驱动组合的灵活性——这是与权重更新相对偶的一种方式，在不修改核心参数的前提下扩展模型行为。

已有技能演化工作，包括 Trace2Skill、SkillX、EvoSkill、CoEvoSkills、PSN、SkillMOO 和 AgentOptimizer，已经证明可以在不修改模型权重的情况下提取、编辑、优化和复用外部能力。然而，新近的文献也清楚地表明，剩余的差距已不再是泛泛的技能生成。我们所针对的未解问题是仓库层次的：当一个系统从有限轨迹中反复采样众多候选技能时，哪些子集应当被保留下来，形成一个紧凑、低噪声、可复用且以测试为锚定的仓库？

在一个长期运行的智能体系统中，需求多样、在线到达且随时间变化。为此类系统构建和维护技能库不是一个一次性提取问题，而是一个在线学习问题：技能库必须以有保障的正确性支撑已有请求，不断提升对未来相似请求的覆盖，并通过新建技能或重构已有技能来快速适应新需求。这种时间维度的多需求视角自然定义了仓库中每个技能必须具备的三项核心属性：**正确性**（忠实地实现特定能力）、**复用性**（能够服务多个不同的未来任务）和**可维护性**（能够通过原则性的流程进行修改、合并或拆分，而不引入冗余、冲突或过时）。这三项属性构成了我们工作的核心设计目标。

核心困难在于，这些属性必须从有限且关联度较低的真实调用中获取：我们必须从一系列任务特定的执行轨迹中提取出真正可复用的技能单元，然后维护这个不断增长的仓库，使得每次增添或修改都能改进整体，而非引入噪声。这本质上是一个软件工程问题。我们面临和软件工程师

我们提出了一个闭环技能演化框架，在单个技能和整个仓库层面上实现这种双规范纪律。在前向方面，每一次技能提取和重构操作都强制要求清晰的接口设计、功能内聚和单一职责；提取出的技能随即打包配有专门的单元测试，这些测试既基于真实的轨迹片段，也包含合成的边界输入。在后向方面，我们引入了建立在真实执行轨迹之上的集成测试与估值循环：对每次执行，我们采用基于 Shapley 值的分析，在有/无技能对照条件下衡量答案质量、令牌节省和实际调用次数，从而估计每个技能的边际贡献。除测试外，后向循环还驱动跨轨迹重构，发现历史上相似轨迹中的公共子计算，将其提取为共享辅助技能，并仅当它们在原始轨迹上保持端到端正确性时才予以接纳。整个仓库在版本控制下管理，追踪依赖关系图，支持回滚，并记录每个技能的演化历史。通过前向单元测试与后向集成测试、重构和版本控制的这种交互，技能库在持续的、以证据为基础的治理下演化，确保每个保留的技能既有效又必要。

具体而言，本文做出以下贡献：

- 我们将技能仓库维护建模为一个以测试为锚定的种群选择问题：在众多 LLM 生成的候选技能中，保留的库应当在最大化验证效用的同时，最小化令牌成本、检索噪声、冗余和维护负担。
- 我们将测试时技能演化形式化为一个闭环软件工程问题，并设计了从技能检索、提取、重构、测试到版本化维护的完整流水线。
- 我们设计了一种技能验证协议，将单技能单元测试与基于 Shapley 值的集成影响评估相结合，确保每个技能在整个仓库语境下的正确性和边际效用。
- 在多样的智能体任务场景（BFCL v3、Spreadsheet、MineDojo、AIME）上的实验表明，我们的方法在提升端到端准确率的同时，显著降低了推理令牌开销，提升了生成稳定性，并实现了高效的跨任务技能复用。



* 介绍Test-time Skill Evolution的定义，在大规模真实场景部署的agent系统为什么需要Test-time Skill Evolution，而不是参数的更新。指出与已有的几个alternatives（RAG，TTT，LoRA，Memory）的区别，强调性能与灵活性的兼顾。指出和参数更新的对偶关系。

* 已有工作简单介绍，存在的局限主要是缺乏对“复用性”、“维护性”的强调。指明这是Evolved skill的核心价值所在，也是我们工作的重点。

* 随着项目需求逐渐复杂，面向个性化场景的大规模skill系统的构建和维护将成为一个非常重要的研究方向。对于一个从历史构建的skill而言，其意义不是再完成相同的历史任务，而是支持未来可能出现的多样化使用场景。「复用性」「维护性」是skill的核心属性，正如模型部署后的测试时表现才是其效能的体现，而不是训练集性能。这意味着我们需要从关注单一任务的skill evolving效果，转向跨任务的skill复用可行性和具体方式的探究。

与孤立问题的skill演化不同，在一个面向多种需求的大规模skill系统中，需求是按照时间在线不断出现的序列，本质是online learning的setting。在这个演化的环境中，从时间尺度出发考虑，skill系统需要：

* 确保能够支持已有需求，保证正确执行。

* 尽可能完善skill设计，用同一批skill支持未来可能出现的相似需求。确保在线性能。

* 随时准备添加新skill和重构已有skill，支持未来可能出现的不同需求。在在线性能不佳的情况下，尽可能快速地修正。

因此，每个skill需要对应具备以下几个核心属性：

* 正确性：skill可以忠实实现对于系统的一定功能
    - 正确性是skill系统的安全可靠基础，主要关注历史和当下需求

* 复用性：skill所实现的功能可以被未来的多种场景使用
    - 复用性是skill系统的性能提升核心，主要关注与历史相似的未来需求

* 可维护性：skill会随着使用需求的丰富而发生变化，可以通过合理的流程进行迭代和维护，避免冗余、冲突和过时
    - 可维护性是skill系统长期演化的可持续基础，主要关注未来需求的多样性和变化

其中，正确性和可维护性是基础，真正让skill发挥作用的是「复用性」，所创造的skill不仅在历史中表现良好，还能够服务诸多未来的相似需求，而且后者是长期且本质的效能，正如模型部署后的测试时表现才是其效能的真正体现，而不是训练集上的效能。

* 指出最核心的难点：如何从「有限的、相关度较低」真实调用中提取出具备“复用属性”的skill，且在真实使用中对不断增长的skill库做迭代和维护。
在未知未来的情况下，发掘并构造真正可复用的skill极具挑战。本质是trace的compression、最优表征问题（MPL）

* 核心挑战：
    - A. 如何发现潜在的可以提取为同一个skill的不同执行结果的trace segment？
    - B. 如何提取出能够最适应未来trace的skill?（不只是贪心适应当下的所有trace）

* 解决方案：
    - A. 两阶段发掘：embedding+token overlap做粗筛，LLM做精筛
    - B. 
        信号：两种方案
        - 测例增广：前向方案，利用模型的世界知识和过往开发经验，尽可能考虑更多的可能出现的未来需求
        - LOO反馈信号：后向方案，只提供部分使用trace采样多个skill，比较谁能满足隐藏的trace
        落地：
        - skill筛选
        - 改写skill extractor的prompt
        - 生成information meta skill / notice反馈给skill extractor

* 行文与贡献。 
    - 首次显式关注跨任务的skill提取和建模，强调「复用性」是skill的本质
    - 将skill evolving建模为复用性片段提取的问题，
    - 在多样化的agentic任务场景下的测试结果表明，我们的在线算法在提升端到端性能的同时，能够给持续显著降低推理开销，提升生成稳定性，且使工具得到充分高效复用


## 2. Related Work

### 2.1 Skill as an External, Evolving Policy Layer

Recent work increasingly treats skills not as prompt patches, but as explicit external capability assets. Anthropic-style agent skills are file-system based modular capabilities centered on a `SKILL.md` file and optional scripts, references, templates, and other resources. This format is important because it separates skill discovery metadata from the heavier artifacts that should only be loaded or executed when needed. In the same spirit, Memento-Skills stores reusable skills as structured markdown memory and uses a read-write-reflect loop to adapt them across interactions. EvoSkill materializes skills as structured reusable folders that can contain workflows and code, while CoEvoSkills targets complex multi-file skill packages rather than single tools. Together, these works indicate that the right abstraction is not a single generated function, but a versioned, retrievable, editable, and governable external policy layer.

This trend directly affects our formulation. A code function is only one possible skill format. Many reusable capabilities are better represented as strategies, workflow cards, tool-use rules, domain references, or document-plus-script modules. Therefore, in our later experimental design, code-function skills should be treated as one ablation rather than the only valid representation.

### 2.2 Trajectory-to-Skill Distillation

Trace2Skill and SkillX study how to distill broad execution experience into transferable skills. Trace2Skill argues against purely sequential per-trajectory updates: it analyzes a diverse pool of executions in parallel and hierarchically merges trajectory-local lessons into a conflict-free skill directory. SkillX similarly constructs a plug-and-play skill knowledge base through multi-level skill design, iterative refinement, and exploratory expansion, organizing experience into strategic plans, functional skills, and atomic skills.

These works strongly overlap with generic claims about "extracting skills from trajectories." Our remaining gap is narrower: rather than claiming first-mover novelty in trajectory-to-skill distillation, we focus on when such distilled skills are actually reusable across tasks, how to refactor an accumulated skill repository without breaking correctness, and how to evaluate skill value by downstream answer, token, and usage effects.

### 2.3 Verification-Driven Skill Evolution

A second line emphasizes verification, diagnosis, and conservative updates. CoEvoSkills couples a skill generator with a surrogate verifier that evolves to provide actionable feedback for complex skill packages. SkillForge studies cloud technical support, where historical tickets, domain knowledge, and expert reference answers enable a creation-evaluation-refinement loop. EvoSkill uses failure analysis to propose new skills or edits to existing skills, then retains candidates only when they improve held-out validation performance. Memento-Skills evaluates and routes skills by task success rather than purely semantic similarity.

The shared lesson is that the bottleneck is not generation but credit assignment: did the skill help, was it called correctly, should we edit it or create a new one, and does the local improvement introduce global repository noise? This motivates our planned Shapley-style skill value evaluation, where a skill's utility is estimated from marginal answer improvement, token reduction, and realized usage under controlled with-skill versus without-skill comparisons.

### 2.4 Skill Governance, Refactoring, and Population Optimization

SkillClaw, EvoSkill, SkillX, PSN, and SkillMOO move from single-agent skill creation to repository-level governance. SkillClaw aggregates multi-user trajectories and synchronizes validated skill updates through a shared repository. EvoSkill distinguishes create and edit operations and uses capacity-limited selection to control repository growth. SkillX adds refinement and expansion to improve coverage while avoiding redundant rediscovery. PSN organizes executable skills into a programmatic skill network and performs fault localization, maturity-aware update gating, and rollback-validated structural refactoring. SkillMOO optimizes agent skill bundles with LLM-proposed edits and NSGA-II selection over pass rate and cost.

This repository-level perspective is close to software engineering, but it also narrows our novelty claim. Long-term refactoring and cost-aware bundle optimization are already present in concurrent work. Our distinct emphasis is test-grounded population selection for a general skill repository: every candidate skill is evaluated by with/without unit utility, integration-derived tests, token cost, retrieval noise, redundancy, and dependency risk before it is retained. Under this framing, refactor_lab is one maintenance backend for discovering shared sub-computations and validating rewrites, while repository selection decides which candidates, merged skills, or legacy versions should survive under a fixed budget.

### 2.5 Policy-Skill Co-Evolution and Model Preconditions

AgentOptimizer, SkillRL, D2Skill, XSkill, and OpenClaw-RL highlight that skill use depends on the policy or execution scaffold. AgentOptimizer treats callable functions as learnable agent weights, showing early evidence that external function sets can be optimized without modifying the base LLM. SkillRL distills skills from experience and lets the skill library co-evolve with the agent policy during reinforcement learning. D2Skill introduces task-level and step-level skills, and computes hindsight utility from paired skill-injected and baseline rollouts. XSkill separates high-level skills from local action-level experiences in multimodal agents. OpenClaw-RL is not an external-skill method in the narrow sense, but it shows that next-state signals can be recovered as online learning feedback.

These works warn against assuming that a frozen model will automatically use code pasted into a prompt. Our own preliminary observations align with this warning: GLM often retrieves but does not call prompt-injected code skills, while Claude-style settings show more reliable skill use. Thus, before evaluating any extraction algorithm, we must first diagnose model and interface preconditions: whether the selected model, skill exposure format, and runtime scaffold actually convert retrieved skills into used skills.

### 2.6 Positioning of This Work

The recent literature already covers broad skill generation, trajectory distillation, verification loops, programmatic skill networks, multi-objective bundle optimization, and RL-based policy-skill co-evolution. Our work should therefore not claim novelty at the level of "self-evolving skills" in general. Instead, we position the project around a narrower software-engineering thesis: test-time skill evolution only becomes meaningful when the environment has reusable structure, the model can use external skills, and the skill format matches the task. Under those preconditions, the central problem becomes maintaining a growing skill repository: extracting reusable structure after execution, validating correctness, estimating marginal skill value, refactoring cross-trace commonalities, and selecting a compact skill population under utility, token, retrieval-noise, redundancy, and maintenance budgets.

This positioning leaves four concrete differentiators: first, peripheral-condition diagnostics for environment, model, and skill format; second, test-grounded repository population selection rather than append-all accumulation or independent single-skill filtering; third, correctness-preserving skill rewrite as a repository maintenance operation; and fourth, frozen-model skill value estimation based on answer, token, retrieval, and usage signals rather than policy training alone.

## 3. Methodology

### 3.1 Methodology (English)

#### 3.1.1 Problem Formulation

We formulate test-time skill evolution as online maintenance of an external,
versioned skill repository. A sequence of training tasks
\(q_1,\ldots,q_N\) arrives during deployment. The base model, executor, and
official task runner are frozen; the algorithm may update only the external
repository \(S\), a heterogeneous overlap graph \(G\), and compact textual
feedback rules \(M\) used by the skill extractor. After training, held-out
evaluation uses a frozen, read-only snapshot of \(S\).

A skill is treated as a maintainable software artifact rather than as a one-off
prompt patch. Each skill has versioned content, scope, interface metadata,
dependency metadata, evidence, status, and a bounded bundle of regression tests.
The repository accepts a skill only when the evidence suggests that it captures
a reusable local invariant and when its bundle tests preserve the relevant tool
and task contracts. This framing separates three goals: correctness under
replayable contracts, reusability across related tasks, and maintainability
under future refinement or refactoring.

#### 3.1.2 Repository State and Roles

The evolving system maintains four state objects. \(S\) is the versioned skill
repository. A skill can be `pending`, `active`, `disabled`, or `archived`; only
active skills are visible to the executor. \(G\) is a heterogeneous graph whose
nodes are current-window trace segments, active skills, and pending skills.
Edges encode lexical overlap, embedding similarity, tool/error similarity, and
explicit skill-relation metadata. \(M\) stores compact extractor feedback rules
obtained from runtime evidence. \(T\) stores execution traces and official task
snapshots for audit, bundle construction, and maintenance.

The implementation uses six functional roles. The executor solves the task with
retrieved active skills. The retriever selects task-level and step-level skill
context and may inject a new compact skill-context message after tool errors.
The credit assigner attributes the completed trace to each retrieved, injected,
or used skill and emits maintenance signals. The extractor proposes new
trace-local skills as pending candidates. The bundle tester and refiner maintain
existing skills using skill-local evidence. The macro refactorer operates on the
heterogeneous graph to merge, split, promote, or reject reusable structure.

#### 3.1.3 Online Evolution Loop

Training and evolution are serial because each training task can mutate the
repository. For each task, the executor first uses the current active repository
to solve the task. The resulting compact trace projection is then passed to the
credit assigner, which produces skill-level attribution and focused bundle-case
suggestions. The extractor proposes new pending skills from the same trace.
Trace segments, active skill nodes, and pending skill nodes are inserted into the
heterogeneous overlap graph. Micro maintenance runs at the task-local frequency,
defaulting to every task. Macro maintenance runs at the window frequency,
defaulting to every ten training tasks.

```text
Algorithm 1: Online Skill Repository Evolution

Input:
  training tasks Q_train = (q_1, ..., q_N)
  held-out tasks Q_test
  initial active repository S_0
  executor Exe, retriever Ret, extractor Ext
  credit assigner Cdt, bundle tester Tst, refiner Rfn
  graph refactorer Rfg, extractor-rule updater Trl
  micro step k_micro = 1, macro step k_macro = 10

State:
  S <- S_0                         // versioned skill repository
  G <- empty heterogeneous graph    // trace, active-skill, pending-skill nodes
  M <- empty extractor feedback rules
  W <- empty macro window           // traces, segments, and credit events

for i = 1 to N:
    trace_i, injected_i <- ExecuteWithRetrievedSkills(q_i, S, M)
    relevant_i <- RetrievedInjectedOrUsedSkills(trace_i, S)

    credit_i <- AssignCredit(
        compact_task_summary(q_i, trace_i),
        retrieved_injected_used_skills(trace_i),
        compact_skill_projections(relevant_i),
        focused_turns_with_tool_calls_and_errors(trace_i),
        official_result_and_expected_calls(q_i),
        token_and_step_metrics(trace_i)
    )

    credit_cases_i <- BuildFocusedBundleCases(
        credit_i,
        official_task_snapshot(q_i)
    )

    pending_i <- ExtractPendingSkills(trace_i, M)
    for skill in pending_i:
        S.add_pending(skill)
        G.add_node(skill)

    segments_i <- ExtractTraceSegments(trace_i)
    G.incremental_update(segments_i, active_and_pending_skill_nodes(S))
    W.add(trace_i, segments_i, credit_i)

    if i mod k_micro == 0:
        MicroMaintenance(S, q_i, credit_i, credit_cases_i, relevant_i)

    if i mod k_macro == 0:
        MacroMaintenance(S, G, W, M)
        W <- empty

if W is not empty:
    MacroMaintenance(S, G, W, M)

S_frozen <- Freeze(S)
return EvaluateHeldout(Q_test, S_frozen)
```

#### 3.1.4 Execution and Step-Level Retrieval

Executor retrieval is restricted to active skills. Pending skills participate in
maintenance and graph refactoring, but they are not injected into the executor
prompt. At each step, the retriever selects skills from the current task state,
trace prefix, tool/domain predicates, and extractor feedback rules. If tool
errors reveal a new relevant skill that was not already injected, the system
appends a compact prompt-context update to the message list and records
`step_retrieved_skills`, `step_injected_skills`, and a `prompt_context_update`
event in the trace. If step retrieval returns no new skill, no duplicate context
message is appended.

```text
Algorithm 2: Execution with Retrieved Skills

function ExecuteWithRetrievedSkills(q, S, M):
    trace <- empty
    injected <- empty set
    messages <- initial_messages(q)

    while task is not finished:
        step_skills <- Ret(q, trace, active_skills(S), M)
        new_skills <- step_skills \ injected

        if new_skills is not empty:
            messages.append(CompactSkillContext(new_skills))
            injected <- injected union new_skills
            trace.record_prompt_context_update(new_skills)

        step_output <- Exe(messages, trace)
        trace.append(step_output)

    return trace, injected
```

#### 3.1.5 Credit Assignment and Focused Bundle Cases

Credit assignment is the central runtime feedback mechanism. Its prompt receives
only a bounded task projection: compact task summary, retrieved/injected/used
skill lists, compact projections of candidate skills, focused turns with tool
calls and tool errors, official result and expected calls, and token/step
metrics. It intentionally excludes the full raw trace, full debug events, the
entire skill store, and unrelated replay results.

For each candidate skill, the credit assigner predicts whether the skill was
`helpful`, `harmful`, `neutral`, or `uncertain`. It also reports confidence,
evidence strength, attribution scope, whether refinement is required, whether
the skill is a filter candidate, and concrete maintenance actions. Bundle-case
suggestions are fragment-level instructions, not requests to turn the whole task
into a regression test. A suggestion must identify the skill name, polarity
(`positive`, `negative`, or `integration`), reason, source task id,
`focus_turn_indices`, expected contract, and task-fragment policy.

The system constructs a replayable bundle case only by slicing the official task
snapshot. The constructed fragment must reuse the official question, expected
tool calls, input artifacts, and metadata. It cannot invent expected calls. If a
focused official fragment cannot be constructed, the evidence is retained for
audit and future maintenance, but no bundle case is added. Harmful credit
creates a negative case only when the judgment is harmful and either confidence
is at least 0.65 or the skill was actually used. Helpful credit creates a
positive case only when the claimed benefit is explicit, such as token saving,
schema help, workflow alignment, or correctness gain.

```text
Algorithm 3: Credit to Focused Bundle Cases

function BuildFocusedBundleCases(credit, official_snapshot):
    cases <- empty list

    for event in credit.skill_events:
        for suggestion in event.bundle_case_suggestions:
            if suggestion.polarity == negative:
                if event.judgment != harmful:
                    continue
                if event.confidence < 0.65 and not event.used:
                    continue

            if suggestion.polarity == positive:
                if event.judgment != helpful:
                    continue
                if event.effect_type not in {
                    token_saving,
                    schema_help,
                    workflow_alignment,
                    correctness_gain
                }:
                    continue

            fragment <- SliceOfficialTaskSnapshot(
                official_snapshot,
                suggestion.focus_turn_indices,
                suggestion.task_fragment_policy
            )

            if fragment is replayable:
                cases.append(BundleCase(suggestion, fragment))
            else:
                RecordEvidenceWithoutCase(event, suggestion)

    return BoundedByPolarityAndRecency(cases)
```

#### 3.1.6 Micro Maintenance

Micro maintenance is skill-local and task-local. It receives the current task,
credit events for that task, credit-created bundle cases, and the current
relevant skill names. It does not run overlap refactoring, text-rule updates,
pending revocation, full-store bundle rebuilds, or broad per-skill refinement.
If a skill receives strong credit evidence or a new focused bundle case, the
system first allows a credit-guided pre-refinement of that target skill. It then
runs strict bundle tests. If the bundle fails, the refiner receives the failed
bundle cases, credit context for that skill, dependency neighborhood, and
previous refinement history. The repair loop is bounded, with the default micro
repair budget set to one round.

```text
Algorithm 4: Credit-Guided Micro Maintenance

function MicroMaintenance(S, q, credit, credit_cases, relevant_skills):
    targets <- SkillsWithNewCases(credit_cases)
    targets <- targets union StrongHelpfulOrHarmfulCredit(credit, relevant_skills)

    if targets is empty:
        return empty_report

    for s in targets:
        if ShouldPreRefine(credit[s]):
            S[s] <- Rfn(
                artifact=S[s],
                failed_cases=[],
                credit_context=credit[s],
                dependency_neighborhood=Neighbors(S, s),
                refinement_history=History(S, s)
            )

        result <- Tst(S[s].bundle, strict_contract_gate=True)
        rounds <- 0

        while result fails and rounds < max_micro_repair_rounds:
            S[s] <- Rfn(
                artifact=S[s],
                failed_cases=result.failed_cases,
                credit_context=credit[s],
                dependency_neighborhood=Neighbors(S, s),
                refinement_history=History(S, s)
            )
            result <- Tst(S[s].bundle, strict_contract_gate=True)
            rounds <- rounds + 1

    return micro_report(targets)
```

#### 3.1.7 Pending Skills and Bundle Construction

New skills enter the repository as pending candidates. This prevents a
single-trace hypothesis from immediately polluting executor retrieval, while
still allowing the candidate to participate in graph-level posterior analysis.
A pending skill is inserted into \(G\) as a real node with node type, skill name,
version, status, source task ids, allowed tools, and domain metadata. It may be
promoted only if later macro evidence supports reusable structure involving the
pending node; otherwise it is revoked at a macro boundary.

Bundle construction is lifecycle-aware. For a new skill, the initial bundle
builder may use compact source evidence from the originating trace. For an
existing skill, bundle maintenance is a patch operation driven by credit-created
cases, integration failures, and contract validation failures. Existing-skill
bundle maintenance no longer receives full source traces or full replay traces
and is not responsible for independently rediscovering trace-level failures.
Bundle size is bounded by polarity and by total case count, retaining recent
credit cases, high-confidence cases, and recent regression cases first.

#### 3.1.8 Macro Maintenance and Heterogeneous Refactoring

Macro maintenance is repository-level. It runs over the current window rather
than the entire historical trace store. The overlap graph contains trace
segments, active skill nodes, and pending skill nodes in the same graph. Before
calling the refactorer, candidate cliques are filtered: a clique must include at
least one current-window trace segment, must cover sufficient real train-task
evidence, and must not be a pure skill-only clique. Candidate skills can be used
only as weak supplementary recall; true participation comes from graph nodes and
edges.

The refactor prompt is deliberately compact. It provides the clique nodes, the
clique edges, summaries of involved skill nodes, and optional repair context
when a previous proposal failed bundle gates. The prompt does not include a full
repository dump. The refactorer may propose a shared skill, merge/split actions,
or affected updates to existing skills. All proposed commits are applied on a
copy and must pass bundle gates for the affected skills before they are written
to the repository. Macro maintenance also performs conservative skill-credit
filtering, pending promotion or revocation, relation-graph updates, and optional
extractor text-rule updates.

```text
Algorithm 5: Macro Maintenance with Heterogeneous Refactoring

function MacroMaintenance(S, G, W, M):
    G.incremental_update(W.trace_segments, active_and_pending_skill_nodes(S))

    cliques <- FindCandidateCliques(G)
    cliques <- FilterCliques(
        cliques,
        require_current_window_trace_segment=True,
        forbid_pure_skill_only=True,
        require_train_task_coverage=True
    )

    for clique in cliques:
        proposal <- Rfg(
            clique_nodes=clique.nodes,
            clique_edges=clique.edges,
            involved_skill_summaries=Summaries(S, clique.skill_nodes),
            repair_context=None
        )

        candidate <- ApplyProposalOnCopy(S, proposal)
        gate <- BundleGate(candidate, affected_skills(proposal))

        if gate passes:
            S <- candidate
            PromotePendingEvidence(S, proposal)
            UpdateSkillRelations(S, proposal)
        else:
            repair <- Rfg(
                clique_nodes=clique.nodes,
                clique_edges=clique.edges,
                involved_skill_summaries=Summaries(S, clique.skill_nodes),
                repair_context=gate.failures
            )
            CommitOnlyIfBundleGatePasses(S, repair)

    ApplyConservativeCreditFilter(S, W.credit_events)
    RevokeUnpromotedPendingSkills(S)

    if extractor_rule_update_enabled:
        M <- Trl(M, RuntimeFeedbackRows(S, W))

    return macro_report(cliques)
```

#### 3.1.9 Held-Out Evaluation

Held-out evaluation freezes the repository after the training/evolution loop.
The runner uses a read-only skill snapshot, so held-out tasks may run
concurrently while preserving manifest order in the output. Training and
evolution remain serial. If a future retrieval implementation mutates usage
counters during evaluation, each held-out task must receive a deep copy of the
frozen repository. Reported outputs include task correctness, call-level
quality, token cost, step count, retrieval statistics, credit events, micro
maintenance reports, macro windows, refactor groups, and final skill versions.

### 3.2 方法论（中文）

#### 3.2.1 问题形式化

我们将测试时 skill 演化定义为一个外部、带版本 skill 仓库的在线维护问题。训练任务序列
\(q_1,\ldots,q_N\) 在部署过程中依次到达。基础模型、executor 和官方任务 runner 都保持
冻结；算法只能更新外部 skill 仓库 \(S\)、异构 overlap graph \(G\)，以及供 extractor 使用
的紧凑文本反馈规则 \(M\)。训练结束后，held-out evaluation 使用冻结的只读 \(S\) 快照。

在这个框架中，skill 不是一次性的 prompt patch，而是一个可维护的软件工程制品。每个
skill 都有带版本的正文、作用域、接口元数据、依赖元数据、证据、状态和有界 bundle
regression tests。只有当证据表明该 skill 捕捉了可复用的局部不变量，并且 bundle tests
保持相关工具/任务合约时，仓库才接受它。这个定义把三个目标分开：可重放合约下的正确性、
跨相关任务的复用性，以及后续 refine/refactor 时的可维护性。

#### 3.2.2 仓库状态与角色

系统维护四类状态。\(S\) 是带版本的 skill 仓库，skill 状态可以是 `pending`、`active`、
`disabled` 或 `archived`；只有 active skill 会进入 executor 上下文。\(G\) 是异构图，节点
包括当前窗口 trace segment、active skill 和 pending skill；边表示词面重叠、embedding
相似度、工具/错误相似度，以及显式 skill relation 元数据。\(M\) 存储由运行时证据得到的
extractor 文本反馈规则。\(T\) 存储执行 trace 和官方 task snapshot，用于审计、bundle 构造
和维护。

实现中有六类功能角色。executor 使用检索到的 active skills 解题。retriever 选择 task-level
和 step-level skill context，并可在工具错误后追加新的紧凑 skill-context message。credit
assigner 对完成后的 trace 中每个 retrieved、injected 或 used skill 做归因，并输出维护信号。
extractor 从 trace 中提出新的 pending skill。bundle tester 和 refiner 使用 skill-local
证据维护已有 skill。macro refactorer 在异构图上决定 merge、split、promote 或 reject 可复用
结构。

#### 3.2.3 在线演化主循环

训练和演化必须串行，因为每个训练任务都可能修改仓库。对每个任务，executor 先使用当前
active repository 解题。随后，完成后的紧凑 trace projection 进入 credit assigner，生成
skill-level attribution 和聚焦 bundle-case suggestions。extractor 从同一条 trace 中提出新的
pending skills。trace segments、active skill nodes 和 pending skill nodes 被加入异构图。
micro maintenance 按任务级频率运行，默认每个任务一次；macro maintenance 按窗口频率运行，
默认每十个训练任务一次。

```text
算法 1：在线 Skill 仓库演化

输入：
  训练任务 Q_train = (q_1, ..., q_N)
  held-out 测试任务 Q_test
  初始 active 仓库 S_0
  executor Exe，retriever Ret，extractor Ext
  credit assigner Cdt，bundle tester Tst，refiner Rfn
  graph refactorer Rfg，extractor-rule updater Trl
  micro step k_micro = 1，macro step k_macro = 10

状态：
  S <- S_0                         // 带版本 skill 仓库
  G <- empty heterogeneous graph    // trace、active-skill、pending-skill 节点
  M <- empty extractor feedback rules
  W <- empty macro window           // trace、segment 和 credit event

for i = 1 to N:
    trace_i, injected_i <- ExecuteWithRetrievedSkills(q_i, S, M)
    relevant_i <- RetrievedInjectedOrUsedSkills(trace_i, S)

    credit_i <- AssignCredit(
        compact_task_summary(q_i, trace_i),
        retrieved_injected_used_skills(trace_i),
        compact_skill_projections(relevant_i),
        focused_turns_with_tool_calls_and_errors(trace_i),
        official_result_and_expected_calls(q_i),
        token_and_step_metrics(trace_i)
    )

    credit_cases_i <- BuildFocusedBundleCases(
        credit_i,
        official_task_snapshot(q_i)
    )

    pending_i <- ExtractPendingSkills(trace_i, M)
    for skill in pending_i:
        S.add_pending(skill)
        G.add_node(skill)

    segments_i <- ExtractTraceSegments(trace_i)
    G.incremental_update(segments_i, active_and_pending_skill_nodes(S))
    W.add(trace_i, segments_i, credit_i)

    if i mod k_micro == 0:
        MicroMaintenance(S, q_i, credit_i, credit_cases_i, relevant_i)

    if i mod k_macro == 0:
        MacroMaintenance(S, G, W, M)
        W <- empty

if W is not empty:
    MacroMaintenance(S, G, W, M)

S_frozen <- Freeze(S)
return EvaluateHeldout(Q_test, S_frozen)
```

#### 3.2.4 执行与 Step-Level Retrieval

Executor retrieval 只允许使用 active skills。pending skills 参与维护和图重构，但不会注入
executor prompt。每一步中，retriever 根据当前任务状态、trace prefix、工具/领域 predicate
和 extractor feedback rules 选择 skill。如果工具错误暴露出新的相关 skill，且该 skill 尚未
被注入，系统会向 message list 追加一条紧凑 prompt-context update，并在 trace 中记录
`step_retrieved_skills`、`step_injected_skills` 和 `prompt_context_update` event。如果 step
retrieval 没有新增 skill，则不会重复注入上下文。

```text
算法 2：带检索 Skill 的执行

function ExecuteWithRetrievedSkills(q, S, M):
    trace <- empty
    injected <- empty set
    messages <- initial_messages(q)

    while task is not finished:
        step_skills <- Ret(q, trace, active_skills(S), M)
        new_skills <- step_skills \ injected

        if new_skills is not empty:
            messages.append(CompactSkillContext(new_skills))
            injected <- injected union new_skills
            trace.record_prompt_context_update(new_skills)

        step_output <- Exe(messages, trace)
        trace.append(step_output)

    return trace, injected
```

#### 3.2.5 Credit Assignment 与聚焦 Bundle Cases

Credit assignment 是运行时反馈的核心机制。它的 prompt 只接收有界 task projection：紧凑任务
摘要、retrieved/injected/used skill 列表、候选 skill 的紧凑投影、包含 tool calls 和 tool
errors 的聚焦 turns、官方结果和 expected calls，以及 token/step metrics。它刻意不接收完整
raw trace、完整 debug events、整个 skill store 或无关 replay results。

对于每个候选 skill，credit assigner 判断该 skill 是 `helpful`、`harmful`、`neutral` 还是
`uncertain`。同时输出 confidence、evidence strength、attribution scope、是否需要 refinement、
是否是 filter candidate，以及具体 maintenance actions。bundle-case suggestion 是片段级指令，
不是把整个 task 变成 regression test 的请求。每个 suggestion 必须包含 skill name、polarity
（`positive`、`negative` 或 `integration`）、reason、source task id、`focus_turn_indices`、
expected contract 和 task-fragment policy。

系统只能通过切片官方 task snapshot 构造可重放 bundle case。构造出的 fragment 必须复用官方
question、expected tool calls、input artifacts 和 metadata，不能发明 expected calls。如果无法
构造聚焦的官方 fragment，则只记录证据，不加入 bundle case。harmful credit 只有在 judgment 为
harmful 且 confidence 至少 0.65 或该 skill 实际被使用时，才生成 negative case。helpful credit
只有在收益明确属于 token saving、schema help、workflow alignment 或 correctness gain 时，才生成
positive case。

```text
算法 3：从 Credit 构造聚焦 Bundle Cases

function BuildFocusedBundleCases(credit, official_snapshot):
    cases <- empty list

    for event in credit.skill_events:
        for suggestion in event.bundle_case_suggestions:
            if suggestion.polarity == negative:
                if event.judgment != harmful:
                    continue
                if event.confidence < 0.65 and not event.used:
                    continue

            if suggestion.polarity == positive:
                if event.judgment != helpful:
                    continue
                if event.effect_type not in {
                    token_saving,
                    schema_help,
                    workflow_alignment,
                    correctness_gain
                }:
                    continue

            fragment <- SliceOfficialTaskSnapshot(
                official_snapshot,
                suggestion.focus_turn_indices,
                suggestion.task_fragment_policy
            )

            if fragment is replayable:
                cases.append(BundleCase(suggestion, fragment))
            else:
                RecordEvidenceWithoutCase(event, suggestion)

    return BoundedByPolarityAndRecency(cases)
```

#### 3.2.6 Micro Maintenance

Micro maintenance 是 skill-local 和 task-local 的。它接收当前 task、该 task 的 credit events、
credit 生成的 bundle cases，以及当前 relevant skill names。它不运行 overlap refactor、不更新
文本规则、不撤销 pending skill、不做全仓库 bundle rebuild，也不做 broad per-skill refinement。
如果某个 skill 收到强 credit evidence 或新的聚焦 bundle case，系统先允许对该目标 skill 做
credit-guided pre-refinement，然后再运行 strict bundle tests。如果 bundle 失败，refiner 接收
failed bundle cases、该 skill 的 credit context、dependency neighborhood 和 previous refinement
history。repair loop 是有界的，默认 micro repair budget 为一轮。

```text
算法 4：Credit 引导的 Micro Maintenance

function MicroMaintenance(S, q, credit, credit_cases, relevant_skills):
    targets <- SkillsWithNewCases(credit_cases)
    targets <- targets union StrongHelpfulOrHarmfulCredit(credit, relevant_skills)

    if targets is empty:
        return empty_report

    for s in targets:
        if ShouldPreRefine(credit[s]):
            S[s] <- Rfn(
                artifact=S[s],
                failed_cases=[],
                credit_context=credit[s],
                dependency_neighborhood=Neighbors(S, s),
                refinement_history=History(S, s)
            )

        result <- Tst(S[s].bundle, strict_contract_gate=True)
        rounds <- 0

        while result fails and rounds < max_micro_repair_rounds:
            S[s] <- Rfn(
                artifact=S[s],
                failed_cases=result.failed_cases,
                credit_context=credit[s],
                dependency_neighborhood=Neighbors(S, s),
                refinement_history=History(S, s)
            )
            result <- Tst(S[s].bundle, strict_contract_gate=True)
            rounds <- rounds + 1

    return micro_report(targets)
```

#### 3.2.7 Pending Skills 与 Bundle 构造

新 skill 先以 pending candidate 进入仓库。这样可以避免单条 trace 上的假设立刻污染 executor
retrieval，同时仍允许该 candidate 参与 graph-level posterior analysis。pending skill 会作为真实
节点进入 \(G\)，并携带 node type、skill name、version、status、source task ids、allowed tools
和 domain metadata。只有当后续 macro evidence 支持包含该 pending node 的可复用结构时，它才会
被 promote；否则会在 macro 边界被 revoke。

Bundle 构造按 skill 生命周期处理。对于新 skill，initial bundle builder 可以使用来自原始 trace
的 compact source evidence。对于已有 skill，bundle maintenance 是由 credit-created cases、
integration failures 和 contract validation failures 驱动的 patch 操作。已有 skill 的 bundle
maintenance 不再接收完整 source traces 或 replay traces，也不负责从 trace 中重新发现问题。
bundle size 受每类 polarity 和总 case 数限制，优先保留最近 credit cases、高置信 cases 和最近
regression cases。

#### 3.2.8 Macro Maintenance 与异构图重构

Macro maintenance 是仓库级维护。它处理当前 window，而不是全部历史 trace store。overlap graph
在同一张图中包含 trace segments、active skill nodes 和 pending skill nodes。调用 refactorer
前，候选 clique 会被过滤：必须包含至少一个当前窗口 trace segment，必须覆盖足够真实训练任务证据，
并且不能是 pure skill-only clique。candidate skills 只能作为弱 recall 补充；真正的参与关系来自
图节点和边。

Refactor prompt 被刻意压缩。它提供 clique nodes、clique edges、involved skill summaries，以及
当上一次 proposal 未通过 bundle gate 时的可选 repair context。prompt 不包含完整仓库 dump。
refactorer 可以提出 shared skill、merge/split actions 或对已有 skill 的 affected updates。所有
proposal 都先应用在副本上，并且必须让 affected skills 通过 bundle gates 后才能写回仓库。macro
maintenance 还会执行保守 skill-credit filtering、pending promotion/revocation、relation graph
updates，以及可选 extractor text-rule updates。

```text
算法 5：基于异构图重构的 Macro Maintenance

function MacroMaintenance(S, G, W, M):
    G.incremental_update(W.trace_segments, active_and_pending_skill_nodes(S))

    cliques <- FindCandidateCliques(G)
    cliques <- FilterCliques(
        cliques,
        require_current_window_trace_segment=True,
        forbid_pure_skill_only=True,
        require_train_task_coverage=True
    )

    for clique in cliques:
        proposal <- Rfg(
            clique_nodes=clique.nodes,
            clique_edges=clique.edges,
            involved_skill_summaries=Summaries(S, clique.skill_nodes),
            repair_context=None
        )

        candidate <- ApplyProposalOnCopy(S, proposal)
        gate <- BundleGate(candidate, affected_skills(proposal))

        if gate passes:
            S <- candidate
            PromotePendingEvidence(S, proposal)
            UpdateSkillRelations(S, proposal)
        else:
            repair <- Rfg(
                clique_nodes=clique.nodes,
                clique_edges=clique.edges,
                involved_skill_summaries=Summaries(S, clique.skill_nodes),
                repair_context=gate.failures
            )
            CommitOnlyIfBundleGatePasses(S, repair)

    ApplyConservativeCreditFilter(S, W.credit_events)
    RevokeUnpromotedPendingSkills(S)

    if extractor_rule_update_enabled:
        M <- Trl(M, RuntimeFeedbackRows(S, W))

    return macro_report(cliques)
```

#### 3.2.9 Held-Out Evaluation

Held-out evaluation 在训练/演化循环结束后冻结仓库。runner 使用只读 skill snapshot，因此可以并发
运行 held-out tasks，同时在输出中保持 manifest 顺序。训练和演化仍保持串行。如果未来 retrieval
实现会在 evaluation 中修改 usage counters，则每个 held-out task 必须拿到 frozen repository 的
deep copy。报告结果包括 task correctness、call-level quality、token cost、step count、retrieval
statistics、credit events、micro maintenance reports、macro windows、refactor groups 和 final skill
versions。

### 3.3 Implementation-Oriented Python-Style Pseudocode (Preserved)

The following block preserves the original Python-style pseudocode as an
implementation-oriented companion to the standard pseudocode above. It is kept
unchanged so the paper retains the previous code-reading view while adding the
more formal algorithms in Sections 3.1 and 3.2.

下面保留原来的 Python-style 伪代码，作为上面标准伪代码的实现导向补充。该代码块保持不变，
用于保留原来的代码阅读视角；正式算法以 3.1 和 3.2 中的标准伪代码为准。

```
Input: Query $\{q_n: 1\le n\le N\}$, Initial skill library $S=\Phi$, Reusage Graph $G=\Phi$, Execution Traces $T=\Phi$, Meta-skills $MS=\Phi$, Evolve turn T.
Parameters: Executor `Exe`, Extractor `Ext`, Bundle Builder `Bbd`, Tester `Tst`, Retriever `Ret`, Refiner `Rfn`, Reward model `Rwd`
Algorithm:

def execute_with_skills(q, S, MS):
    """
    Executor with skill retriever augmented
    """
    trace = []
    turn = 0
    relevant_skills = []

    # Execute turn by turn
    while True:
        turn_relevant_skills = Ret(q, trace[-1] if trace else None, S, MS) # Retrieve relevant skills by turn
        turn_output = Exe(q, trace, turn_relevant_skills)
        trace.append(turn_output)
        relevant_skills.append(turn_relevant_skills)
        if turn_output is EOS:
            break
    return trace, relevant_skills


def extract_new_skills_prior_as_pending(trace):
    """
    Extract "pending skills" according to LLM's experience from human software engineers.
    These skill are addede as pending to library. While they will not be used for augmenting execution, they participate in posterior skill extraction.
    If any of these pending skills takes part in extraction of a new posterior skill, they will be promoted as posterior skills.
    """
    while True:
        skills = Ext(trace, MS) # Sample multiple skills for competing
        for skill in skills:
            skill.is_pending_skill = True
            skill.is_promoted = False

            # Go under test
            bundle = Bbd(skill)
            test_output = Tst(skill, bundle)
            if test_output.success:
                break
    return skills


def extract_new_skills_posterior(trace, G):
    """
    Extract from real execution traces' overlap. They will be directly added as solid skills.
    """
    candidate_traces = G.find_potential_overlap_segment(trace)
    while True:
        skills = Ext(candidate_traces, MS) # Sample multiple skills for competing
        for skill in skills:
            # Go under test
            bundle = Bbd(skill)
            test_output = Tst(skill, bundle)
            if test_output.success:
                break
    G.add(skills)

    # The relevant pending skills are promoted
    for candidate_trace in candidate_traces:
        if candidate_trace.is_pending_skill:
            candidate_trace.is_promoted = True
    return skills


def refine_skills(trace, relevant_skills):
    """
    According execution results of real use cases, assign credit to each skills and perform corresponding refinement
    """
    credits = assign_credit(trace, relevant_skills) # With an LLM, judge the credit of each skill in this execution
    refined_skills = []
    for relevant_skill, credit in zip(relevant_skills, credits):
        if credit is NEGATIVE:
            # Negative test case found by credit assigner. Add to bundle (maybe after merging and refactoring existing bundle cases)
            negative_case = credit.negative_case
            relevant_skill.bundle.negative_cases.maybe_add(negative_case)
            while True:
                # Refinement according to negative cases and re-test.
                refined_skill = Rfn(relevant_skill)
                test_output = Tst(refined_skill)
                if test_output.success:
                    break
            refined_skills.append(refined_skill)
        else:
            # Add positive cases maybe (maybe also merging and refactoring)
            positive_case = credit.positive_case
            relevant_skill.bundle.positive_cases.maybe_add(positive_case)
    return refined_skills


def maintain_skills(trace, relevant_skills, S, G):
    """
    Extract new skills. Refine exisitng relevant skills.
    """
    new_pending_skills = extract_new_skills_prior_as_pending(trace)
    S.add_pending(new_pending_skills)
    G.add(new_pending_skills)
    S.add(new_skills := extract_new_skills_posterior(trace, G))
    S.update(refined_skills := refine_skills(trace, relevant_skills))


def text_based_policy_gradient(MS):
    """
    Summarize experience and methodology as meta-skills. Feedback will be applied to extractor.
    """
    skill_groups = S.get_skill_groups()
    for skill_group in skill_groups:
        group_result = skill_group.valid_call_cnt
        semantic_gradient = Rwd(skill_group, group_result)
        MS.maybe_add(semantic_gradient)

    
def filter_skills(S):
    """
    Filter the worst skills according to credit(correctness), usage count(reusability).
    """
    S.filter_bottom_p(0.1)


def check_pending_skills():
    """
    Revoke pending skills that are never reused in practice.
    """
    for pending_skill in S.pending_skills:
        if not pending_skill.is_promoted:
            S.remove(pending_skill)


def main(S, MS, G, T, N, micro_maintenance_step, marco_maintenance_step):
    """
    Go over multiple epochs of evolution. In each epoch, go through each train set
    """
    for _ in range(T):
        for n in range(N):
            trace, relevant_skills = execute_with_skills(queries[n], S, MS)
            if (n + 1) % micro_maintenance_step == 0:
                maintain_skills(trace, relevant_skills, S, G)
            if (n + 1) % marco_maintenance_step == 0:
                text_based_policy_gradient(MS)
                filter_skills(S)
                check_pending_skills(S)
                
```


## 4. Experiments

### 4.0 Skill Refactoring

We study skill refactoring as a maintenance problem over an accumulated skill
library. The goal is not to extract a brand-new skill from a single execution
trace, but to identify shared sub-computations across historically evolved
skills, factor them into reusable helpers, and preserve downstream correctness.

#### Experimental setup

We use two refactoring benchmarks. The first is a synthetic math corpus built
for controlled analysis of shared computational motifs, including geometry,
number theory, modular arithmetic, linear algebra, and statistics. The second
is a small manually curated `skillsbench_manual` corpus derived from the
SkillsBench task style, which stresses non-math procedural skills. In both
cases, the original skill collections are correct before refactoring, so the
main evaluation questions are: whether correctness is preserved, whether token
footprint decreases, and whether the recovered graph structure matches the
expected reusable decomposition.

Separately, we also report a `skillsbench_fixture` retrieval benchmark built
from 24 selected tasks in the external SkillsBench repository. This fixture is
used only to evaluate semantic retrieval quality of the embedding + pgvector
stack; it is not a refactoring benchmark and not the official end-to-end
SkillsBench runner.

Between the lab-only refactoring benchmarks and the full integrated online
experiment, we also define an offline `planning_replay_benchmark`. This
benchmark is intended to evaluate the planner-aware formulation more directly:
given the same current query, retrieved historical skills, and optional prior
query/plan/trace context, does the planner recover the expected shared
abstraction and emit a workflow plan that explicitly calls it? This benchmark
is meant to evaluate planner structure rather than executor end-to-end task
success.

We compare four variants: `naive` literal deduplication, `v1` free-text cluster
keys, `v2` union-find clustering over pairwise positive edges, and `v3`
clique-growth clustering with execution validation. The lab setting is
post-hoc: a set of existing skills is refactored after the fact. In the main
system, this mechanism is now treated as a post-execute repository maintenance
step rather than a pre-execute planner requirement: once the current trace is
available, it can be compared against similar historical traces and skills to
decide whether to create, edit, merge, split, or reject a skill.

#### Main results

On the math corpus, the final `v3` method extracts exactly five shared
sub-functions, preserves correctness at 100%, and reduces total code tokens
from 1530 to 1258, a 17.8% reduction. Earlier variants fail for different
reasons: `v1` fragments clusters and duplicates helper code, increasing tokens
by 113.3%; `v2` over-merges distinct motifs into one connected component and
breaks correctness under corpus-level evaluation. On the `skillsbench_manual`
corpus, the corrected direct-harness protocol shows that refactoring preserves
100% accuracy while reducing average tokens from 670 to 516, a 23.0%
reduction. On the separate `skillsbench_fixture` retrieval benchmark, the
current retrieval stack achieves 95.8% Recall@1 and 100% Recall@5 over 24
external-style tasks, with 161 ms average latency and about $1.13e-5 embedding
cost per query. Together, these results show that the system has both a
correctness-preserving refactoring mechanism and a strong retrieval substrate
for external-style tasks, while keeping the two claims explicitly separated.

#### Graph analysis

The decisive change is the clustering primitive. Free-text keying in `v1`
creates semantic fragmentation because equivalent sub-tasks are named
differently across pairwise alignment calls. Union-find in `v2` is too coarse:
a single false-positive edge can connect two otherwise independent dense
regions, forcing one extraction call to explain multiple unrelated motifs.
Clique-growth in `v3` is more robust because a node is added only when it is
connected to every current cluster member. Empirically, this removes the
catastrophic over-merge mode while preserving all true math clusters.

The main over-merge example is `power_of_point`, which superficially resembles
the geometry cross-product group because it manipulates planar coordinates.
The alignment stage can still attach it to the geometry region, but the
extraction stage omits it from the rewrite set and the execution gate prevents
incorrect propagation. The main under-merge example is `expected_value`, which
uses a weighted reduction and is therefore not unified with plain sum-based
reductions. This suggests a richer reduction taxonomy as future work rather
than a failure of the current correctness-first design.

#### Implications for the online system

The lab setting validates graph discovery and correctness-preserving rewrite,
but it should not be directly promoted into a costly pre-execute planner. The
current mainline moves refactoring back to the post-execute extraction stage:
after a query has produced a full trace, the system compares the new trace with
historically similar traces and skills, then decides whether to create, edit,
merge, split, or reject a skill. This change accepts higher training-time cost
in exchange for better test-time skill quality, and avoids asking the planner
to guess reusable abstractions before seeing the current trace.

The offline replay benchmark remains useful, but its role is diagnostic rather
than central. It can test whether a planner notices possible historical
workflow reuse, yet the main algorithmic evidence should come from
post-execute skill quality, downstream reuse, token reduction, and
correctness-preserving repository maintenance.

#### Appendix material

The appendix should include: the exact extracted shared helper texts for the
accepted runs, representative positive and negative pairwise alignment outputs,
the rejected `v1` and `v2` failure cases, rewritten skill bodies, and the full
debug log for the `skillsbench_manual` protocol correction. For the
SkillsBench-derived retrieval fixture, the appendix should additionally include
the full per-query ranking table, the single rank-1 miss case, and the fixture
category/difficulty summary. Figure-ready artifacts include: a cluster graph
schematic, a token-vs-correctness bar chart, a retrieval metrics table for the
fixture benchmark, and a pipeline diagram covering alignment, clique growth,
extraction, validation, and planner-time invocation.


### 4.1. Main Results

Table 1 reports the current reproducible pilot results. BFCL uses the curated
50/50 related-task split, with one online evolution epoch over 50 training
tasks and a frozen skill-store snapshot evaluated on the 50 held-out tasks.
SpreadsheetBench currently reports a baseline-only 50-task smoke run; its
evolution result is left blank until the benchmark-specific skill maintenance
path is enabled. MineDojo and AIME are not included in this pilot table.

For BFCL we report three complementary metrics because exact task success is
too strict to summarize multi-turn tool behavior alone: exact success,
official-valid rate from the official runner, and call-level average score. We
also report average total tokens per held-out task. The first interrupted BFCL
evolve evaluation suffered a network/proxy failure and timed out on 30/50
held-out tasks; the table uses the subsequent held-out-only rerun with the same
frozen evolved skill snapshot.

| Benchmark | Model | Setting | Exact success | Official valid | Avg score | Avg tokens / task | Timeout |
|---|---|---|---:|---:|---:|---:|---:|
| BFCL v3 related 50/50 | Claude Sonnet 4.5 proxy | baseline, no skills | 0.06 | 0.44 | 0.7312 | 70,323.8 | 0.00 |
| BFCL v3 related 50/50 | Claude Sonnet 4.5 proxy | evolve, 1 epoch, frozen skill-store rerun | 0.08 | 0.74 | 0.7991 | 86,813.3 | 0.00 |
| SpreadsheetBench-Verified | Claude Sonnet 4.5 proxy | baseline, test 50 | 0.22 | N/A | 0.2564 | 1,552.1 | 0.00 |

The BFCL pilot shows a clear gain in official-valid rate and call-level score:
official-valid improves from 0.44 to 0.74, and average score improves from
0.7312 to 0.7991. Exact success improves only slightly, from 0.06 to 0.08,
indicating that the current skill layer primarily fixes workflow and contract
structure, while strict end-state and exact-argument failures remain. The
held-out token cost increases by 23.5% because prompt-only skill injection adds
context and does not yet replace enough model/tool steps. This is a current
limitation rather than a claimed efficiency result for the BFCL pilot.

The online maintenance phase for the BFCL run used 106 maintenance LLM calls
and 744,385 maintenance tokens. The largest cost component is extraction
(391,031 tokens), followed by refinement (139,262), credit assignment
(100,248), refactoring (62,382), bundle construction (46,351), and extractor
feedback (5,111). These costs are not included in the held-out inference-token
columns and should be reported separately as training-time maintenance cost.

### 4.2. Ablation Studies
Ablation 1: skill format (code function vs. workflow card vs. tool-use rule)

Ablation 2: refactoring (with vs. without)

Ablation 3: testing (test case from real trace, synthetic test case, no test)

Ablation 4: refinement (with vs. without)

### 4.3. Version control and maintenance analysis

希望研究版本控制在skill库维护中的作用，特别是当skill库不断增长和演化时，版本控制如何帮助我们管理技能的变更、回滚和依赖关系。可能的分析包括：

* 依赖图：图结构分析，依赖密集的技能，频繁变更的技能

* 版本历史：技能的变更频率，回滚次数，变更类型（新增、修改、删除）

* case study

### 4.4. Trajectory analyses

希望详细调研skill evolving对于trace带来的改变究竟体现在什么方面。



## Appendix A. Benchmark Details

### A.1 BFCL v3: Function Calling and Multi-Turn Tool Use

Berkeley Function Calling Leaderboard (BFCL) was introduced by the UC Berkeley
Gorilla team as a benchmark for evaluating whether language models can select
and invoke external functions correctly. The official BFCL description frames
function calling as the ability to map a natural language request into one or
more valid API/tool invocations, including correct function names, argument
schemas, values, ordering, and cases where no function should be called. The
ICML 2025 paper, *The Berkeley Function Calling Leaderboard (BFCL): From Tool
Use to Agentic Evaluation of Large Language Models*, further positions BFCL as
a response to the lack of standard, scalable evaluation for real-world tool use.
The benchmark covers serial and parallel function calls, multiple programming
languages, AST-based evaluation, executable evaluation, and later versions add
more realistic multi-turn and multi-step agentic settings.

In this paper, we use BFCL v3 as the primary benchmark for online skill
evolution because it stresses exactly the type of behavior a skill repository is
supposed to improve: repeated argument binding, ordering-sensitive workflows,
schema obedience, state tracking across turns, and tool-use recovery after
errors. A BFCL task is not simply a question-answer pair. It provides a
conversation, a tool environment, and an official expected call trace. The
model must produce calls that are both semantically appropriate and compatible
with the tool schema. This makes BFCL a useful setting for measuring whether a
skill helps the executor call tools more reliably, or whether it pollutes the
prompt and causes extra or malformed calls.

Our local BFCL related-task split uses a curated subset of BFCL v3 multi-turn
tasks. The current recommended setting is a deterministic 150/50 split: 150
training tasks for online evolution and 50 held-out tasks for frozen-store
evaluation. Training is serial because the skill repository, overlap graph, and
feedback memory are mutated after each task. Held-out evaluation is read-only
and can run concurrently. The related-task manifest ranks examples by repeated
tool-family patterns, lookup/action mixtures, path length, and multi-turn
argument-binding failure modes.

One representative BFCL training task is `multi_turn_base_118`, a TradingBot
workflow. The user first asks to inspect a stock watchlist, then remove the
first stock, then buy 100 shares of AAPL at the current market price, then
inspect the latest order, and finally cancel that order. The expected trace is:

```text
turn 1: get_watchlist()
turn 2: remove_stock_from_watchlist(symbol='NVDA')
turn 3: get_stock_info(symbol='AAPL')
        place_order(order_type='Buy', symbol='AAPL', price=227.16, amount=100)
turn 4: get_order_details(order_id=12446)
turn 5: cancel_order(order_id=12446)
```

This example tests several reusable behaviors. The executor must bind the
watchlist item `NVDA` from the first call into the remove operation; it must
lookup the current AAPL price before placing a market-price order; and it must
carry the returned order id into later inspection and cancellation calls. A
useful skill for this region is not "always buy AAPL", but a narrower workflow
rule: when a trading request asks for current-market execution, first retrieve
the stock information, then place the order using the observed price, and
preserve the generated order identifier for subsequent order-management turns.
This is why our credit assigner creates focused bundle fragments around the
relevant tool-call subsequence instead of turning the entire conversation into a
single coarse regression case.

Another held-out example, `multi_turn_base_51`, combines two tool families:
vehicle control and messaging. The expected trace locks all doors, starts the
engine only after pressing the brake pedal, checks tire pressure and finds a
nearby tire shop, then logs into the message API and sends a specific message
from `USR001` to `USR002`. This tests cross-domain tool selection and
precondition ordering. A skill that teaches "press brake before startEngine" can
help the vehicle subtask, but it should not influence the later message API
turn. This motivates our attribution-scope field in credit assignment and our
dependency-aware refiner: a local vehicle workflow should be refined or tested
on the vehicle fragment, not on the unrelated messaging fragment.

### A.2 SpreadsheetBench: Real-World Spreadsheet Manipulation

SpreadsheetBench was introduced in *SpreadsheetBench: Towards Challenging
Real World Spreadsheet Manipulation* by Ma et al. and accepted to the NeurIPS
2024 Datasets and Benchmarks track. The benchmark targets realistic Excel-like
spreadsheet manipulation rather than simplified table QA. Its tasks are derived
from real online spreadsheet forum questions, where users describe messy
workbooks, attempted solutions, formatting constraints, multiple sheets,
non-standard relational tables, and desired output regions. The paper argues
that existing spreadsheet benchmarks often use synthetic or simplified
instructions, while real spreadsheet work requires interpreting layout,
formulas, formatting, and ambiguous user intent.

The original SpreadsheetBench contains 912 real-world instructions and proposes
an online-judge-style evaluation: the model should produce a robust solution,
typically executable code, that can be applied to spreadsheet test cases and
compared against golden workbooks. Our repository uses the
`SpreadsheetBench-Verified 400` subset. Each task contains an instruction, an
input workbook, a golden workbook, and an answer sheet/range. The local adapter
asks the model to write Python `openpyxl` code that modifies a copied workbook
and saves an output file. The verifier then compares the declared answer range
between the produced workbook and the golden workbook. This setup turns
spreadsheet manipulation into a concrete artifact-editing benchmark: the model
must actually update cells and formatting, not merely describe a formula in
natural language.

One representative complex task in our local split is `56427`. The instruction
asks the agent to transpose values from column G into columns H:S whenever
column B marks a new group, using the runner count in column C to decide how
many consecutive values to copy. It also asks the agent to preserve blanks,
shade `H2:S28` with color `#E2EFD`, remove decimals from whole values, and
center-align cells to the right of column G. The answer range is `H2:S28`.
This sample stresses several spreadsheet-specific issues: group detection,
sequential row traversal, blank preservation, numeric formatting, fill colors,
and layout preservation. In our smoke run, the benchmark pipeline successfully
called the model, extracted Python code, executed it, and compared the output,
but the generated code failed because it used the short color string `E2EFD`
instead of an openpyxl-compatible aRGB color string. This is a
typical SpreadsheetBench failure: the high-level data transformation can be
mostly understood while a low-level spreadsheet API contract still breaks the
execution.

A simpler single-cell task is `31628`. The input workbook has dates in
`A1:A11`; the instruction asks for the number of the last day in the final date
entry to be written as a static value in `B1`, while preserving column A. The
expected answer range is the single cell `B1`. This task is useful as a small
case because it isolates basic workbook loading, date parsing, static-value
writing, saving, and answer-range verification. During local smoke testing, it
also exposed a verifier edge case: a single-cell range such as `B1` is returned
by openpyxl as a `Cell` object rather than a two-dimensional range. We fixed the
verifier to treat single-cell ranges as one checked coordinate. This kind of
failure is exactly why SpreadsheetBench is useful for our method: robust skills
need to cover both user-facing spreadsheet workflows and API-level details such
as openpyxl range handling, color encodings, formula objects, and workbook
serialization.

Together, BFCL and SpreadsheetBench cover complementary agentic failure modes.
BFCL evaluates structured tool calling under explicit schemas and multi-turn
state. SpreadsheetBench evaluates artifact manipulation where the model must
write executable code against a rich external file format and pass cell-level
comparison. BFCL skills often look like workflow cards or function-contract
rules; SpreadsheetBench skills often look like executable API idioms, such as
"normalize Excel color fills to aRGB before assigning `PatternFill`" or
"handle single-cell and rectangular ranges separately." This contrast lets us
test whether the skill evolution system can maintain different skill types
without collapsing all evidence into one prompt-patching mechanism.

## 5. Discussion

## 6. Conclusion

## References

- Anthropic. Agent Skills documentation. <https://docs.claude.com/en/docs/agents-and-tools/agent-skills>
- Shishir G. Patil, Huanzhi Mao, Fanjia Yan, Charlie Ji, Vishnu Suresh, Ion Stoica, and Joseph E. Gonzalez. The Berkeley Function Calling Leaderboard (BFCL): From Tool Use to Agentic Evaluation of Large Language Models. ICML 2025. <https://icml.cc/virtual/2025/poster/46593>
- Fanjia Yan, Huanzhi Mao, Charlie Cheng-Jie Ji, Ion Stoica, Joseph E. Gonzalez, Tianjun Zhang, and Shishir G. Patil. Berkeley Function-Calling Leaderboard. Gorilla Blog, 2024. <https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html>
- Zeyao Ma, Bohan Zhang, Jing Zhang, Jifan Yu, Xiaokang Zhang, Xiaohan Zhang, Sijia Luo, Xi Wang, and Jie Tang. SpreadsheetBench: Towards Challenging Real World Spreadsheet Manipulation. NeurIPS 2024 Datasets and Benchmarks Spotlight. <https://openreview.net/forum?id=KYxzmRLF6i>
- Yinjie Wang et al. OpenClaw-RL: Train Any Agent Simply by Talking. arXiv:2603.10165, 2026.
- Jingwei Ni et al. Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills. arXiv:2603.25158, 2026.
- Hanrong Zhang et al. CoEvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification. arXiv:2604.01687, 2026.
- Ziyu Ma et al. SkillClaw: Let Skills Evolve Collectively with Agentic Evolver. arXiv:2604.08377, 2026.
- Salaheddin Alzubi et al. EvoSkill: Automated Skill Discovery for Multi-Agent Systems. arXiv:2603.02766, 2026.
- Peng Xia et al. SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning. arXiv:2602.08234, 2026.
- Songjun Tu et al. D2Skill: Dynamic Dual-Granularity Skill Bank for Agentic RL. arXiv:2603.28716, 2026.
- Chenxi Wang et al. SkillX: Automatically Constructing Skill Knowledge Bases for Agents. arXiv:2604.04804, 2026.
- Huichi Zhou et al. Memento-Skills: Let Agents Design Agents. arXiv:2603.18743, 2026.
- Xingyan Liu et al. SkillForge: Forging Domain-Specific, Self-Evolving Agent Skills in Cloud Technical Support. arXiv:2604.08618, 2026.
- Guanyu Jiang et al. XSkill: Continual Learning from Experience and Skills in Multimodal Agents. arXiv:2603.12056, 2026.
