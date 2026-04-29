# You Live More than Once: A Software Engineering's Perspective for Test-time Skill System Evolution

Test-time Skill Evolution被视为agentic system部署后不断积累新经验的一种全新范式。尽管已经出现很多类似的初步尝试，目前人们仍然主要聚焦单一任务、孤立skill evolving效果。对于跨任务的skill复用可行性和具体方式少有探究。本篇工作将Skill Evolution建模为一个闭环的软件工程问题。将skill建模为可复用逻辑单元，从skill提取到迭代维护流程，都通过整合软件工程原则进行精细设计；通过Shapley Value对提取的Skill进行后验检验和筛选。在多样化的agentic任务场景下的测试结果表明，我们的方法在提升端到端性能的同时，显著降低了推理开销，提升了生成稳定性，且每个工具都得到了充分高效复用。

```
Test-time Skill Evolution is a novel paradigm for agentic systems to continuously accumulate new experiences after deployment. Despite many preliminary attempts, the focus has been primarily on isolated skill evolving effects for single tasks. The feasibility and specific approaches for cross-task skill reuse have been underexplored. In this work, we model Skill Evolution as a closed-loop software engineering problem. By modeling skills as reusable logical units, we design the entire process from skill extraction to iterative maintenance with fine-grained integration of software engineering principles. By using Shapley Value as post-hoc validation criterion, we perform in-depth analysis and selection of the extracted skills. Testing results across diverse agentic task scenarios show that our method significantly reduces inference overhead and improves generation stability while enhancing end-to-end performance, with each tool being fully and efficiently reused.
```

## 1. Introduction

* 介绍Test-time Skill Evolution的定义，在大规模真实场景部署的agent系统为什么需要Test-time Skill Evolution，而不是参数的更新。指出与已有的几个alternatives（RAG，TTT，LoRA，Memory）的区别，强调性能与灵活性的兼顾。指出和参数更新的对偶关系。

* 已有工作简单介绍，存在的局限主要是缺乏对“复用”的强调。指明这是Evolved skill的核心价值所在，也是我们工作的重点。

* 指出最核心的难点： 如何从「有限的、相关度较低」真实调用中提取出具备“复用属性”的skill。引申为软工问题。讨论人类工程师的经验。指出「前向规范」和「后向规范」（TODO：调研软工领域的专业术语和分类学）

* 我们的解决方案：「闭环」的重要性，包括尽可能严谨的先验设计，尽可能及时准确的后验验证。前者对应「开发规范」「持续维护」「版本控制」，后者对应「测例驱动」（真实测例运行+Shapley Value检验）。

* 行文与贡献。
    - 首次关注跨任务的skill提取和建模，强调「复用属性」的重要性
    - 将skill evolving建模为一个闭环的软件工程问题，设计了从skill提取到迭代维护的完整流程
    - 通过Shapley Value对提取的Skill进行后验检验和筛选，确保每个skill的有效性和必要性
    - 在多样化的agentic任务场景下的测试结果表明，我们的方法在提升端到端性能的同时，显著降低了推理开销，提升了生成稳定性，且每个工具都得到了充分高效复用

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

* New Skill Extraction
    - 发生在executor调用trace生成之后
    - 模型对自己的执行过程进行反思，提取出具备「复用属性」的部分作为skill
    - 尽量追求编写完善的前向规范，确保每个skill的输入输出清晰，功能单一，易于理解和使用

* Skill Testing
    - 针对刚生成的skill尽可能进行测试，确保其正确性和有效性
    - 针对测出的初步问题，进行迭代优化，直到满足预设的质量标准

* Skill Retrieval
    - 时机：在 executor 调用前检索已有 skill / workflow history，供本次执行参考；在 executor 调用后检索相似历史 trace / skill，供 post-execute extraction 与 refactoring 使用。
    - 按照特征嵌入相似度、TF-IDF、usage utility 等方法对 skill 进行检索，确保在后续调用中能够高效找到合适的 skill。
    - 按照相似的办法对 query / workflow history 做检索，找到类似 query 的历史调用记录和已提取 skill。

* Skill Refactoring
    - 时机：主线改为发生在 executor 调用 trace 生成之后。
    - 根据本次 query、完整执行 trace、执行结果、token 成本、golden answer，以及检索到的历史相似 query trace / skill，对已有 skill 进行重新提取、合并、拆分或重构。
    - 目标不是让当前 query 在 execute 前立刻用上新 skill，而是在 train 阶段多花成本沉淀更高质量 skill，使后续 test query 能稳定复用。
    - 发生版本更迭，对上下游的 skill 进行版本控制维护（详见 `/home/lixujun/skill_evolving/copilot_cli/{DESIGN,DESIGN_V2}.md`）。


## 4. Experiments

### 4.1 Skill Refactoring

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
