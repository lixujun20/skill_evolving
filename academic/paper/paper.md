# You Live More than Once: A Software Engineering's Perspective for Test-time Skill System Evolution

Test-time Skill Evolution被视为agentic system部署后不断积累新经验的一种全新范式。随着agentic system project不断复杂化，对大规模skill系统的构建和维护成为一个非常重要的需求。已有工作基本聚焦孤立skill的evolution方法论，而鲜有讨论skill作为软件工程单元的可复用性、可维护性问题。本篇工作将Skill Evolution建模为一个闭环的软件工程问题。将skill建模为可复用逻辑单元，从skill提取到迭代维护流程，都通过整合软件工程原则进行精细设计；通过基于Shapley Value的独立测试、集成测试对提取的Skill进行后验检验和筛选。在多样化的agentic任务场景下的测试结果表明，我们的方法在提升端到端性能的同时，显著降低了推理开销，提升了生成稳定性，且每个工具都得到了充分高效复用。

```
Test-time Skill Evolution is a novel paradigm for agentic systems to continuously accumulate new experiences after deployment. Despite many preliminary attempts, the focus has been primarily on isolated skill evolving effects for single tasks. The feasibility and specific approaches for cross-task skill reuse have been underexplored. In this work, we model Skill Evolution as a closed-loop software engineering problem. By modeling skills as reusable logical units, we design the entire process from skill extraction to iterative maintenance with fine-grained integration of software engineering principles. By using Shapley-Value-based unit tests and integrated tests as post-hoc validation criterion, we perform in-depth analysis and selection of the extracted skills. Testing results across diverse agentic task scenarios show that our method significantly reduces inference overhead and improves generation stability while enhancing end-to-end performance, with each tool being fully and efficiently reused.
```

## 1. Introduction

* 介绍Test-time Skill Evolution的定义，在大规模真实场景部署的agent系统为什么需要Test-time Skill Evolution，而不是参数的更新。指出与已有的几个alternatives（RAG，TTT，LoRA，Memory）的区别，强调性能与灵活性的兼顾。指出和参数更新的对偶关系。

* 随着项目需求逐渐复杂，面向个性化场景的大规模skill系统的构建和维护将成为一个非常重要的研究方向。这意味着我们需要从关注单一任务的skill evolving效果，转向跨任务的skill复用可行性和具体方式的探究。

* 已有工作简单介绍，存在的局限主要是缺乏对“复用性”、“维护性”的强调。指明这是Evolved skill的核心价值所在，也是我们工作的重点。

* 指出最核心的难点： 如何从「有限的、相关度较低」真实调用中提取出具备“复用属性”的skill，且在真实使用中对不断增长的skill库做迭代和维护。引申为软工问题，讨论人类工程师的经验。指出解决这个问题需要建立「前向规范（先验开发规范）」和「后向规范（实践闭环反馈）」（TODO：调研软工领域的专业术语和分类学）

* 我们的解决方案：「闭环」的重要性，包括尽可能严谨的先验设计 + 尽可能及时准确的后验验证。前者对应「开发规范」「持续维护」「版本控制」，后者对应「测例驱动」「孤立测试+集成测试」（真实测例运行+Shapley Value检验）。

* 行文与贡献。 
    - 首次关注跨任务的skill提取和建模，强调「复用性」「可维护性」两个关键点
    - 将skill evolving建模为一个闭环的软件工程问题，设计了从skill提取到迭代维护的完整流程
    - 设计针对skill本身的单元测试+针对真实场景的集成测试，通过Shapley Value对提取的Skill进行后验检验和筛选，确保每个skill的有效性和必要性
    - 在多样化的agentic任务场景下的测试结果表明，我们的方法在提升端到端性能的同时，显著降低了推理开销，提升了生成稳定性，且每个工具都得到了充分高效复用

```
Test-time Skill Evolution (TTSE) is emerging as a new paradigm for agentic systems to continuously accumulate capabilities after deployment, without updating model parameters. In large-scale deployments, an agent must handle diverse, evolving demands efficiently; parameter updates are expensive, slow to validate, and risk regressions. Existing alternatives each address only part of the problem. Retrieval-Augmented Generation injects declarative knowledge but cannot encode reusable procedural logic. Test-Time Training and LoRA adapt model weights, incurring substantial latency and computational cost while tightly coupling capability acquisition to a specific model snapshot. Memory-augmented architectures accumulate interaction facts but lack structured, callable skill abstractions. Test-time skill evolution occupies a distinctive design point: it adds a lightweight, external, updatable policy layer of reusable skills that combines the performance benefits of specialized tools with the flexibility of dynamic, task-driven composition—a duality to weight updates in which model behavior is extended without modifying core parameters.

Existing work on skill evolution, including Trace2Skill, SkillX, EvoSkill, and CoEvoSkills, has focused predominantly on isolated extraction and verification loops. These methods demonstrate that trajectory-to-skill distillation is feasible, but they leave critical questions unanswered when the system operates continuously across many tasks: When is a distilled skill truly reusable beyond its originating trace? How should an accumulated skill repository be refactored to remove redundancy and conflict without breaking existing functionality? And how can we continuously validate the correctness and necessity of every skill using only finite, real-world execution feedback? In short, the reusability and maintainability of skills—the very qualities that make skill evolution valuable beyond single-task caching—have been largely underexplored.

In a long-running agentic system, demands are diverse, arrive online, and shift over time. Building and maintaining a skill library for such a system is therefore not a one-time extraction problem, but an online learning problem: the library must support past requests with guaranteed correctness, continually improve its coverage for future similar requests, and rapidly accommodate novel demands through new skill creation or existing skill refactoring. This temporal, multi-demand perspective naturally defines the three core attributes every skill in the repository must possess: correctness (faithful implementation of a specific capability), reusability (the ability to serve multiple, distinct future tasks), and maintainability (the capacity to be iteratively modified, merged, or split without introducing redundancy, conflict, or staleness). These three attributes form the central design objectives of our work.

The core difficulty is that these attributes must be achieved from limited, loosely correlated real invocations: we must extract genuinely reusable skill units from a stream of task-specific execution traces, and then maintain the growing library so that each addition or edit improves the whole rather than introducing noise. This is, fundamentally, a software engineering problem. Human engineers manage evolving codebases by combining forward norms—a priori specifications such as modular design, single-responsibility principles, and interface contracts that raise the prior probability of correctness and reusability—with backward norms—a posteriori, closed-loop practices such as testing, integration validation, and version control that catch regressions and guide refactoring. Our work is built on the observation that automated skill evolution, to be sustainable, requires an analogous dual-norm discipline. The challenge is not merely to generate skills, but to institute a software engineering process for skill repositories that systematically applies both forward design rigor and backward empirical validation.

We propose a closed-loop skill evolution framework that instantiates this dual-norm discipline at the level of individual skills and the repository as a whole. On the forward side, every skill extraction and refactoring operation enforces clear interface design, functional cohesion, and single responsibility; extracted skills are immediately packaged with dedicated unit tests that exercise them on both real trace fragments and synthetic boundary inputs. On the backward side, we introduce an integrated testing and valuation loop grounded in actual execution traces: for each execution, we estimate each skill’s marginal contribution using a Shapley-Value-based analysis that measures answer quality, token savings, and actual invocation counts under controlled with/without-skill comparisons. Beyond testing, the backward loop also drives cross-trace refactoring, where common sub-computations across historically similar traces are discovered, extracted into shared helper skills, and admitted only if they preserve end-to-end correctness on the original traces. The entire repository is managed under version control, which tracks the dependency graph, enables rollback, and records the evolutionary history of every skill. Through this interplay of forward unit testing and backward integrated testing, refactoring, and versioning, the skill library evolves under continuous evidence-based governance, ensuring each retained skill is both effective and necessary.

Concretely, this paper makes the following contributions:

* We are the first to systematically address cross-task skill extraction and repository evolution with reusability and maintainability as explicit, first-class objectives.

* We formulate test-time skill evolution as a closed-loop software engineering problem and design a complete pipeline that spans skill retrieval, extraction, refactoring, testing, and versioned maintenance.

* We design a skill validation protocol that combines per-skill unit tests with integrated, Shapley-Value-based impact estimation, ensuring each skill’s correctness and marginal utility in the context of the full repository.

* Experiments across diverse agentic task scenarios (BFCL v3, Spreadsheet, MineDojo, AIME) demonstrate that our method improves end-to-end accuracy, significantly reduces inference token overhead, increases generation stability, and achieves efficient cross-task skill reuse.

```

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

### 2.4 Skill Governance and Collective Evolution

SkillClaw, EvoSkill, and SkillX move from single-agent skill creation to repository-level governance. SkillClaw aggregates multi-user trajectories and synchronizes validated skill updates through a shared repository. EvoSkill distinguishes create and edit operations and uses capacity-limited selection to control repository growth. SkillX adds refinement and expansion to improve coverage while avoiding redundant rediscovery.

This repository-level perspective is close to software engineering. Once the skill library grows, the main risks become redundancy, retrieval noise, conflicting skills, regression, and context cost. Our refactor_lab is positioned in this space: it studies correctness-preserving repository maintenance by discovering shared sub-computations, extracting common helpers, validating rewrites, and reducing token footprint.

### 2.5 Policy-Skill Co-Evolution and Model Preconditions

SkillRL, D2Skill, XSkill, and OpenClaw-RL highlight that skill use depends on the policy or execution scaffold. SkillRL distills skills from experience and lets the skill library co-evolve with the agent policy during reinforcement learning. D2Skill introduces task-level and step-level skills, and computes hindsight utility from paired skill-injected and baseline rollouts. XSkill separates high-level skills from local action-level experiences in multimodal agents. OpenClaw-RL is not an external-skill method in the narrow sense, but it shows that next-state signals can be recovered as online learning feedback.

These works warn against assuming that a frozen model will automatically use code pasted into a prompt. Our own preliminary observations align with this warning: GLM often retrieves but does not call prompt-injected code skills, while Claude-style settings show more reliable skill use. Thus, before evaluating any extraction algorithm, we must first diagnose model and interface preconditions: whether the selected model, skill exposure format, and runtime scaffold actually convert retrieved skills into used skills.

### 2.6 Positioning of This Work

The recent literature already covers broad skill generation, trajectory distillation, verification loops, collective skill repositories, and RL-based policy-skill co-evolution. Our work should therefore not claim novelty at the level of "self-evolving skills" in general. Instead, we position the project around a narrower software-engineering thesis: test-time skill evolution only becomes meaningful when the environment has reusable structure, the model can use external skills, and the skill format matches the task. Under those preconditions, the central problem becomes maintaining a growing skill repository: extracting reusable structure after execution, refactoring cross-trace commonalities, validating correctness, estimating marginal skill value, and selecting a compact skill set under token and redundancy budgets.

This positioning leaves four concrete differentiators: first, peripheral-condition diagnostics for environment, model, and skill format; second, post-execute cross-trace refactoring instead of pre-execute plan guessing; third, correctness-preserving skill rewrite as a repository maintenance operation; and fourth, frozen-model skill value estimation based on answer, token, and usage signals rather than policy training alone.

## 3. Methodology

1. 总体原则

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

从而，skill库需要围绕核心的Executor agent，支持如下核心操作：

* 检验和维持skill的正确性 —— Test

* 根据历史调用提取可以复用的公共skill逻辑 —— Refactorization

* 随时根据新的调用轨迹提取新的skill，或者重构已有skill以适应新的需求 —— Extraction, Refinement

从有限的真实调用中提取出具备上述属性的skill，本质上是一个具有挑战性的软件工程问题，也是一个长期的系统演化过程，需要从实践需求出发来指导演化的进行。所以上述操作都需要接受实际执行情况的检验和反馈，形成一个闭环的演化流程。

针对于此，我们借鉴人类工程师的经验，设计了一套「前向规范（先验开发规范）」和「后向规范（实践闭环反馈）」：

* 前向规范：这是指开环的skill提取和维护经验，来自于长期的人类软件工程实践，目的是尽可能确保skill有较高的先验概率遵守上述属性。这体现在skill演化的多个环节：
    Extraction & Refinement: 始终确保每个skill的接口清晰，功能单一，易于理解和使用。
    Testing: 设计全面的测试用例，覆盖不同的输入场景和边界情况，确保每个skill在被提取出来时就具备较高的正确性。
    Refactorization: 保持拆分或合并后的skill代码的清晰和模块化，避免过度耦合和冗余。
* 后向规范：这是指闭环的skill提取和维护经验，来自于实际执行情况的反馈，目的是通过不断的检验和迭代来提升skill的质量和适应性。executor的执行结果会为各个skill演化环节提供反馈：
    Extraction: execution trace的一个片段具备复用潜质时，就可以被提取成一个新的skill
    Testing: trace中对某个skill的执行过程天然构成一个测试用例，可以用来检验该skill的正确性和必要性
    Refactorization: trace中多个skill的执行过程如果存在公共子结构，就可以被重构成一个新的skill，或者被合并成一个更高层次的skill
    Refinement: trace中某个skill的执行如果出现了问题，就可以按照执行反馈来修正该skill的设计

```
1. Overall principles


```


2. 逻辑顺序

* Skill Retrieval
    - 时机：在 executor 调用前检索已有 skill / workflow history，供本次执行参考；在 executor 调用后检索相似历史 trace / skill，供 post-execute extraction 与 refactoring 使用。
    - 按照特征嵌入相似度、TF-IDF、usage utility 等方法对 skill 进行检索，确保在后续调用中能够高效找到合适的 skill。
    - 按照相似的办法对 query / workflow history 做检索，找到类似 query 的历史调用记录和已提取 skill。

* New Skill Extraction
    - 发生在executor调用trace生成之后
    - 模型对自己的执行过程进行反思，提取出具备「复用属性」的部分作为skill
    - 尽量追求编写完善的前向规范，确保每个skill的输入输出清晰，功能单一，易于理解和使用

* Skill Refactoring
    - 时机：主线改为发生在 executor 调用 trace 生成之后。
    - 根据本次 query、完整执行 trace、执行结果、token 成本、golden answer，以及检索到的历史相似 query trace / skill，对已有 skill 进行重新提取、合并、拆分或重构。
    - 目标不是让当前 query 在 execute 前立刻用上新 skill，而是在 train 阶段多花成本沉淀更高质量 skill，使后续 test query 能稳定复用。
    - 发生版本更迭，对上下游的 skill 进行版本控制维护（详见 `/home/lixujun/skill_evolving/copilot_cli/{DESIGN,DESIGN_V2}.md`）。

* Skill Testing
    - 结合真实运行实例和自己构造的测例，针对刚生成的skill尽可能进行测试，确保其正确性和有效性
    - 针对测出的初步问题，进行迭代优化，直到满足预设的质量标准


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

Benchmark: BFCL_v3, Spreadsheet, MineDojo, AIME

Model: GLM4.7, Claude-4.6-sonnet

Setting: 现在训练集上evolve skills，可能过多个epoch。最后在测试集上评估

Metrics: Accuracy, token cost, turn count

Results:

glm4.7
```
        |BFCL_v3 acc    |BFCL_v3 token  |Spreadsheet acc    |Spreadsheet token  |MineDojo acc   |MineDojo token |AIME acc   |AIME token |
baseline|               |               |                   |                   |               |               |           |           |
evolve 1|               |               |                   |                   |               |               |           |           |
evolve 3|               |               |                   |                   |               |               |           |           |
```

claude-4.7-opus
```
        |BFCL_v3 acc    |BFCL_v3 token  |Spreadsheet acc    |Spreadsheet token  |MineDojo acc   |MineDojo token |AIME acc   |AIME token |
baseline|               |               |                   |                   |               |               |           |           |
evolve 1|               |               |                   |                   |               |               |           |           |
evolve 3|               |               |                   |                   |               |               |           |           |
```

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



## 5. Discussion

## 6. Conclusion

## References

- Anthropic. Agent Skills documentation. <https://docs.claude.com/en/docs/agents-and-tools/agent-skills>
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
