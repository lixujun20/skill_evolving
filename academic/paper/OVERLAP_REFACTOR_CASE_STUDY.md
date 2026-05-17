# Case Study: Cross-Task Overlap Refactoring from Real Maintenance Traces

## 1. Setup

We analyze the maintenance experiment:

```text
bfcl_real_glm_maintenance_2026-05-12__overlap_refactor_debug_5case_online2
```

This experiment is a compact refactor-overlap run over five real multi-turn BFCL maintenance tasks. The refactor module builds segment-level similarity edges from execution traces, proposes shared skills from overlap clusters, and then validates whether the proposed abstraction should be committed.

For this case study, we focus on one successful shared-skill commit and contrast it against several high-similarity but lower-value segment pairs. The goal is to answer a narrower question than generic retrieval quality:

When does segment overlap correspond to a genuinely reusable latent skill, rather than merely local lexical similarity or within-task topical continuity?

## 2. Main Successful Refactor Case

The most meaningful result in this run is the committed shared skill:

```text
skip_lookup_when_identifier_explicit_in_context
```

The compact projection records the following rationale in both the `llm_done` and `committed` stages:

> All four segments exhibit the same error pattern: making an extra lookup/query call before the main action call, and passing a wrong identifier value obtained from that lookup instead of using the correct identifier from context.

The proposed skill description is:

```text
When the required identifier is explicitly provided in the task context, use it directly instead of making intermediate lookup/resolution calls.
```

This abstraction is supported by four segments spanning three distinct tasks:

### Case A: `multi_turn_base_111:turn:0`

- User intent: add a stock to a watchlist.
- Relevant context: the task already provides a directly usable entity reference.
- Failure pattern: the agent is tempted to call a lookup step first and then route the result into the main action.
- Why it belongs: the essential issue is not stock-specific behavior, but unnecessary identifier resolution before the main tool call.

### Case B: `multi_turn_base_111:turn:2`

- User intent: send a message with user identity already specified in context.
- Relevant context: explicit sender identity (`USR001`) and target communication context are already present.
- Failure pattern: an unnecessary lookup or resolution step can precede the actual send action, creating an avoidable identifier mismatch.
- Why it belongs: this is the same control-policy error in a different tool family.

### Case C: `multi_turn_base_193:turn:1`

- User intent: continue a travel booking flow after authentication.
- Relevant context: the required authenticated state and downstream booking context are already established.
- Failure pattern: the agent may still perform an intermediate resolution step before the actual booking action.
- Why it belongs: this shows the pattern transfers beyond finance/messaging into authenticated travel operations.

### Case D: `multi_turn_base_66:turn:1`

- User intent: refuel a car with a directly specified amount.
- Relevant context: the action target and needed argument are already explicit.
- Failure pattern: the system may perform a redundant intermediate check or derivation instead of calling the action directly.
- Why it belongs: although the surface domain is very different, the behavioral mistake matches the same latent template.

## 3. Why This Case Is Meaningful

This case is useful because the final abstraction is not driven by superficial wording overlap. The four supporting segments come from different domains:

- stock/watchlist
- messaging
- travel booking
- vehicle control

What generalizes is not topic, but decision structure:

1. the task context already contains a sufficient identifier or action argument,
2. the agent inserts an unnecessary lookup/resolution step,
3. that step increases the chance of using the wrong value in the final tool call.

This is exactly the kind of cross-task latent policy that a reusable skill repository should preserve. It is small, operational, and independent of a single API family.

## 4. High-Similarity but Lower-Value Segment Cases

The same experiment also contains several strong overlap edges that are less compelling as shared-skill evidence.

### Case E: `multi_turn_base_66:turn:0` vs `multi_turn_base_66:turn:3`

- Recorded overlap weight: `0.393377`
- Observation: both segments come from the same driving/navigation task thread.
- Interpretation: the similarity mostly reflects within-task topical continuity around car usage and trip setup.
- Why this is weaker: it is related, but does not clearly imply a reusable cross-task skill.

### Case F: `multi_turn_base_101:turn:0` vs `multi_turn_base_111:turn:0`

- Recorded overlap weight: `0.414005`
- Observation: both are stock-related tasks.
- Interpretation: this is a cleaner cross-task edge than the within-task car example, but it still risks collapsing to domain-topic overlap rather than a failure-mode abstraction.
- Why this is ambiguous: without the downstream reasoning step, similarity alone cannot tell whether the shared skill is “stock symbol handling,” “lookup avoidance,” or simply “financial noun overlap.”

### Case G: `multi_turn_base_187:turn:2` vs `multi_turn_base_187:turn:3`

- Recorded overlap weight: `2.7`
- Observation: both segments are part of the same support-escalation workflow.
- Interpretation: this edge reflects strong sequential continuity inside one task, from contacting support to escalating to a ticket.
- Why this is weaker as refactor evidence: it is useful for local workflow modeling, but much less useful for extracting a broadly reusable shared skill.

## 5. Assessment

Overall, this experiment is a mixed but encouraging result.

### What worked

- The system did recover at least one credible cross-task latent skill.
- The committed abstraction is behavior-level and domain-agnostic.
- The rationale recorded in the commit is coherent and matches the supporting traces.

### What did not work well enough

- Many top overlap edges are still dominated by same-task continuity or surface lexical similarity.
- Raw similarity ranking alone is not selective enough for reusable-skill discovery.
- The high-value result depends on the second-stage refactor decision, not on the overlap score by itself.

### Bottom-line judgment

The overlap graph is currently good enough to surface plausible candidates, but not good enough to be trusted as the primary evidence layer. The real value comes from the maintenance/refactor stage that reinterprets overlapping segments in terms of shared failure mode and downstream action policy.

In other words, this experiment supports the claim that:

```text
segment overlap is a useful proposal mechanism, but not yet a sufficient selection mechanism.
```

## 6. Implications for the Method

This case suggests three concrete lessons for the full methodology.

First, overlap edges should be treated as recall-oriented candidate generation. They are helpful for surfacing possible shared structure, but should not be read as direct proof of reusable abstraction.

Second, the decisive signal is semantic error-pattern agreement. The successful committed case is unified by a common action-policy failure, not by a common topic.

Third, evaluation should emphasize post-refactor behavioral validity. For practical skill maintenance, the most important question is not whether two segments are textually close, but whether the resulting shared skill improves future executions while reducing redundant intermediate calls.

## 7. Takeaway

This experiment provides a compact example of both the promise and the limitation of overlap-based maintenance.

The promise is that a real shared policy can indeed be recovered from sparse, heterogeneous traces:

- do not resolve what is already explicit in context,
- do not insert lookup calls before the main action unless they are necessary.

The limitation is that many visually strong overlap edges still correspond to local narrative continuity rather than reusable skill structure.

For a paper-level claim, the correct interpretation is therefore conservative:

```text
Overlap-based segment retrieval is useful as a high-recall front end for cross-task refactoring, but high-precision reusable skill discovery still depends on a downstream semantic maintenance step.
```
