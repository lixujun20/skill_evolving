235-257
1. 我看错了，refactor不能在round级别，感觉有点太迟了。我希望只有一个epoch，但是每隔marco_maintenance_step就重构一下。train_detail只给这期间的trace。
2. 你这个refine_bfcl_skill_store_llm过了整个store，实际上只需要对这个trace的relevant skill做就可以了？
3. refine_skill_artifact_llm的时候，也给了整个store的dependency_context，这是否没必要？refinement_history是这个skill之前的修改记录嘛？我理解确实给了更全面，但复杂度是否太高？
4. btw，我觉得还需要维护skill之间的相互调用关系。现在做完refine之后、extract之后，会更新调用和被调用关系吗？
5. 好像没有给credit信息来refine？我没有在extract_bfcl_skill_artifacts_llm里面找到用judgement

```response to comment
1. 认同。当前实现不是你现在描述的最终算法：`academic/benchmarks/bfcl/related/experiment.py::_run_round_refine_and_refactor` 只在 round 末尾跑 bundle/test/refine/refactor；`_online_refactor_budget_from_env()` 还明确返回 0。虽然 `OverlapGraphState` 支持增量图更新，但真正 refactor 触发太晚。下一版应改成 single epoch + every `macro_maintenance_step` 触发一次 macro maintenance，maintenance 输入只包含最近窗口 `window_train_details` 和窗口新增 segment，同时 overlap graph 保留历史节点和历史边。
2. 认同。当前 `refine_bfcl_skill_store_llm` 内部先构造 `results_by_name`，然后遍历 `store.all()`，但只有有 test result 的 artifact 才会继续；也就是说逻辑上不是 refine 全 store，但代码形态仍是全 store 扫描。应改成显式 `artifact_names` / `target_artifacts` 参数，只遍历当前 trace/window relevant skills，避免误解和无谓 O(|store|)。
3. `refinement_history` 是该 skill 之前 refinement 决策记录，来自 `artifact.metadata["refinement_history"]`，理解正确。`dependency_context = summarize_dependency_context(store.all())` 当前确实给了全 store 的 dependency summary，复杂度过高，而且可能引入无关干扰。应改成只给 dependency neighborhood：该 skill 直接 dependencies、直接 dependents、同 trace/window 中共同 retrieved/used 的 skill、以及 bundle/refactor 明确 affected 的 skill。
4. 当前没有可靠维护 skill 之间的调用/被调用关系。`SkillArtifact.dependencies` 主要来自 LLM 生成或后续 stale/dependency 逻辑，不会在 extract/refine 后自动解析和更新调用图；executor 也只记录 retrieved/injected/used/called_skill_tools，不会把它沉淀成 skill-call graph。需要新增 `SkillRelationGraph` 或 artifact metadata 字段，记录 `calls`, `called_by`, `co_retrieved_with`, `co_used_with`, `derived_from`, `refines`, `conflicts_with`，并在 extract/refine/refactor/credit 后更新。
5. 是的，credit 当前没有进入 extractor/refiner 的核心输入。`assign_skill_credit_llm` 在每个 task 后生成 judgment，随后 `_credit_event_records` 和 `_apply_credit_case_evidence` 写入 evidence，round-end 通过 `_aggregate_skill_credit` / `_apply_skill_credit_filter` 做保守 disable；但 `extract_bfcl_skill_artifacts_llm` 没有吃 judgment，`refine_skill_artifact_llm` 也只吃 bundle test result、integration failures、refinement_history、dependency_summaries。需要把 credit 作为 `credit_context` 输入 refine，并作为 extractor 的 negative/positive feedback summary 输入，而不是只做后置 filter。
```

```plan
P0 algorithm change:
1. Add config `--epochs 1` and `--macro-maintenance-step N`; deprecate multi-round as ablation/legacy.
2. During training keep a rolling `maintenance_window` containing only details/segments/credit events since last macro maintenance.
3. After each task run per-task light maintenance: credit assignment, extraction, immediate relevant-skill bundle case proposal, and relevant-skill refine if strong negative/positive evidence exists.
4. Every `macro_maintenance_step`, run macro maintenance on window details: relevant bundle/test/refine and overlap refactor using new segments against the global overlap graph.
5. Change `refine_bfcl_skill_store_llm(store, ..., artifact_names=targets, credit_context=...)` so it never scans/refines unrelated skills.
6. Replace full-store dependency context with dependency-neighborhood summaries.
7. Add skill relation graph update after extract/refine/refactor and after executor credit assignment.
```

219-225
1. 现在检查refactor时候用的segment不是全部的吗？我是说只有和这个round新产生的segment有关的边需要考虑，不是只用这些segment来构图，有可能和历史上的segment也有边？
2. 给我解释一下目前的clique发现算法是什么算法
3. max_attempts疑似也不能要，我们要对所有新segment都看一遍是不是需要refactor，可以是no op，但不能不看。
4. refactor的时候skill还是当做coarse_candidate给的，我们要改成异构图；现有skill还是全都给，这感觉有点太复杂了。我们没必要在refactor的时候知道其他skill
5. 看起来你给的refactor prompt few shot只生成知识类skill。我们也需要另外两类skill的详细的fewshot例子。你应该允许提取各种各样的skill
6. 现在的依赖关系完全依靠造skill的时候llm自己写对吗，不会有审查？我觉得对于代码类的skill，可以通过ast来自动生成dependencies。
7. shared skill有测试链路吗？

```response to comment
1. 当前实现分两层：图状态层支持“新 segment 与历史 segment 建边”，但当前 experiment 调用方式没有充分利用。`run_bfcl_overlap_refactor_llm(... overlap_state=..., new_segments=...)` 会调用 `update_overlap_graph_state(overlap_state, new_segments=resolved_new_segments, ...)`，该函数应把新增 segment 接入已有图；但 `_run_round_refine_and_refactor` 传的是 `new_segments=[]`，segment_embeddings 只传当前 round 的 embedding map，所以 round-end refactor 实际更接近“已累计状态的 materialize + 当前 round embedding 辅助”，不是严格的 per-window new-segment audit。应改成 macro maintenance 时传入窗口新增 segments，并要求对每个新增 segment 生成 coverage record。
2. 当前 clique 发现是启发式贪心严格 clique，不是 Bron-Kerbosch maximal clique。`find_refactor_cliques` 做法是：按边权降序选 seed edge；取两个端点共同邻居；每次加入与当前 clique 总边权最大的候选点；要求新点和 clique 内所有点都有边；过滤 clique size、distinct task 数、purity；去重后按 edge_weight_sum 排序。优点是快，缺点是不能保证枚举所有 maximal clique，也可能漏掉较弱但有意义的 clique。
3. 认同。当前 `BFCL_REFACTOR_MAX_CLIQUES` 默认 3，会截断 LLM refactor attempt；这与“所有新 segment 都要被看过，可 no-op 但不能不看”冲突。应该把 budget 从“最多尝试几个 clique”改成“所有新 segment coverage 必须有结论”，LLM 调用可以分层：cheap deterministic no-op / group skip record / only high-value group 调 LLM，但每个新 segment 都要有 `refactor_audit_status`。
4. 认同。当前 `llm_refactor_clique` 同时传 `Coarsely Recalled Existing Skill Candidates` 和 `Existing Skills In Store` 全量 summaries，这太重且会污染。下一版应改成异构图：节点包括 trace segment 和 skill；边包括 segment-segment、segment-skill、skill-skill；refactor prompt 只给当前 connected component / ego network，不给全 store。已有 skill 不再是“全量背景”，而是图上与 clique 有边的少数节点。
5. 认同。当前 refactor prompt/few-shot 偏向 shared knowledge/workflow skill，覆盖不足。应增加三类精品 few-shot：knowledge rule、workflow/procedure、function/code-like helper，并要求输出 `skill.kind` / `interface` 与测试策略相匹配。refactorer 应允许 no-op、extract knowledge、extract workflow、extract function、merge/refine existing skill 多种 action。
6. 认同。当前 dependencies 主要依赖 LLM 输出和人工约定，没有系统审查。对于 code-like/function skill，应增加 AST/static dependency analyzer：解析 body 或 editable text 中的 import、函数调用、skill reference、tool name，生成 `auto_dependencies`；再和 LLM dependencies 做 diff，不一致时进入 validator/refiner。
7. 有测试链路，但粒度需要改。当前 shared skill 从 refactor payload 生成后，会进入 bundle/test gate：`run_bfcl_overlap_refactor_llm` 对 shared skill/affected updates 构造 bundle、执行 with/without skill 测试，只有 committable 才写入 store。但测试仍偏 bundle 级，不是“每个 shared skill 都绑定其 refactor clique 的真实 trace window + relevant held-out mini regression”的强制链路。需要把 shared skill 的 test cases 固定包含 clique segment 来源任务的 local cases，并限制 bundle 最大大小。
```

```plan
P0/P1 refactor graph changes:
1. Replace round-end `new_segments=[]` with macro-window `new_segments=window_segments`.
2. Add `refactor_segment_coverage[]`: every new segment records candidate groups considered, action=`noop|merged|extract_shared|refine_existing|defer`, and reason.
3. Replace `max_attempts` with deterministic full coverage plus LLM budget tiers. No segment can disappear because of top-k clique truncation.
4. Implement heterogeneous graph nodes: `trace_segment`, `skill`; edges: sparse overlap, embedding similarity, credit co-use, dependency/call relation.
5. Refactor prompt input becomes connected component / ego network only, not full store.
6. Expand refactor few-shot to knowledge/workflow/function examples.
7. Add AST dependency validator for code-like skills and a dependency consistency gate before commit.
8. Strengthen shared-skill bundle generation to include clique-derived cases and cap/split bundle size.
```

239
1. 我看你在prompt的最后一条显式说对于无关skill导致退化要标成harmful，这个我觉得一般模型无法判别，这不属于强因果关系。我们可以标成neutral，后面统一反馈给skill gate（待未来实现）

```response to comment
认同。当前 `CREDIT_ASSIGNMENT_SYSTEM` 第 7 条要求“irrelevant + regressed consistent with prompt pollution” 标 harmful，这会诱导模型做弱因果归因。虽然第 4 条写了 conservative，但第 7 条仍太强。应改成：无关 skill 默认标 neutral 或 uncertain；只有存在直接证据（模型显式引用 skill、调用 skill tool、skill 内容与错误参数/错误工具/错误顺序高度一致）才可标 harmful。prompt pollution 这类弱信号应进入 `skill_gate_feedback`，不直接作为 negative credit。
```

```plan
P0 credit prompt change:
1. Change credit rubric: irrelevant retrieved/injected skill without direct evidence => neutral/uncertain.
2. Add `suspected_prompt_pollution` as separate boolean/notes field，不计入 harmful。
3. Skill filter only consumes high-confidence harmful with direct evidence; weak pollution goes to future gate/reranker feedback.
```

242-252
1. 那你每次maintain bundle的时候会给所有的执行结果嘛，这个复杂度也太高不能接受。至少应该每个task结束之后refine skill的时候考虑构造一个对应的新的test case。（btw，我发现现在没有每个task结束之后的refine？这个是需要的）

```response to comment
当前实现确实不符合这个目标。`_run_round_refine_and_refactor` 调 `build_bfcl_skill_bundles_llm(store, train_details=train_details, artifact_names=targets, ...)`，这里的 `train_details` 是整轮累计 details；targets 虽然经过 `select_bfcl_maintenance_targets` 过滤，但 bundle builder 仍可能看到过多执行结果。每个 task 结束后目前做了 execution、credit assignment、extraction、segment index、overlap graph update、checkpoint；没有 per-task refine，也没有 per-task 立刻为 relevant skill 增量构造 test case。
```

```plan
P0 bundle/refine scheduling:
1. Add per-task `post_task_micro_maintenance(detail)`:
   - identify relevant skills from retrieved/injected/used/credit/extraction source;
   - append at most one or few task-derived bundle cases to each relevant skill;
   - run cheap contract validation for new cases;
   - if high-confidence harmful/helpful evidence exists, call lightweight refine for that skill only.
2. Macro maintenance no longer receives all historical train_details. It receives only `window_train_details` and existing compact bundle summaries.
3. Bundle store enforces max cases per skill; overflow uses split/replace policy based on coverage, recency, failure type, and credit value.
4. Bundle builder input becomes delta-oriented: current skill, current bundle summary, one/few new trace cases, recent credit evidence, not all task traces.
```

268
1. 见上一条，这应该每task结束之后发生，而不是每轮。

```response to comment
认同。当前每轮末尾 refine/refactor 是旧设计遗留；新算法应改成 task-level micro maintenance + macro-step refactor。每个 task 结束后至少发生：credit assignment、relevant skill evidence update、delta bundle case proposal、optional relevant-skill refine、segment graph incremental update。每 `macro_maintenance_step` 才做较重的 clique/shared-skill refactor。
```

```plan
P0 schedule rewrite:
1. Replace round loop with single epoch task loop.
2. Add `macro_maintenance_step` counter.
3. Per task run micro maintenance.
4. Every macro step run refactor over new-window segments against global graph.
5. At epoch end run final macro maintenance for leftover window.
```
