# Benchmark-Agnostic Skill Evolution Refactor Plan

Date: 2026-05-18
Branch: `spreadsheet-multiturn-notebook`

## Goal

抽取 BFCL 和 Spreadsheet 共享的 skill evolving 算法层，让 benchmark adapter 只负责环境相关的执行、验证、trace 投影、bundle case 构造和 function skill runtime。重构必须保留现有 BFCL/Spreadsheet 入口兼容，支持后续更多 env 接入。

## Execution Discipline

- 每次只执行一个 chapter。
- 每个 chapter 完成后必须：
  - 写测试。
  - 跑相关测试。
  - 把所有修改按文件、行号、代码块记录在本文档对应 chapter。
  - 单独 git commit。
- 不混入算法效果调参，不跑大规模真实实验作为结构重构验收条件。
- 不触碰无关 dirty state：
  - `academic/results/algorithm_docs/SPREADSHEET_NOTEBOOK_IMPROVEMENT_PLAN_20260518.md`
  - `tmp_run_local_claude_bfcl_debug.py`

## Chapter 1: 公共接口与算法骨架

### Plan

新增 benchmark-agnostic core 层，但暂不迁移 BFCL 主链路。

- 新增公共 credit event 工具：
  - normalize LLM/heuristic credit rows。
  - helpful/harmful/uncertain 强度判断。
  - credit evidence 写入 skill artifact。
- 新增公共 bundle case 工具：
  - 将 credit suggestion 规范化。
  - 统一按 polarity 写入 bundle。
  - 复用 bundle budget trim。
- 新增公共 micro maintenance skeleton：
  - 根据 credit 和新 bundle case 选择 target。
  - credit 强信号先 refine。
  - 再跑 bundle test。
  - 失败后最多 N 轮 repair。
- 新增公共 macro maintenance skeleton：
  - window report 结构。
  - promotion/filter/TRL hook 汇总。
  - store snapshot 汇总。
- 新增公共 relation graph skeleton：
  - 支持 skill/trace/pending node。
  - 支持 edge upsert。
  - 支持短锁并发安全更新。
- 新增 fake adapter/core 单测。

### Implementation Log

Completed in this chapter.

#### Added `academic/benchmarks/core/credit_events.py`

Lines 1-217 define the benchmark-neutral credit event schema helpers. The important behavior is:

- normalize benchmark-specific credit rows into common fields;
- classify helpful/harmful/uncertain signals;
- identify actionable micro targets;
- write compact credit evidence back to `SkillArtifact.evidence`.

```python
     1	"""Benchmark-neutral credit event helpers for skill maintenance."""
     2	from __future__ import annotations
     3	
     4	import copy
     5	from typing import Any, Dict, Iterable, List, Sequence
     6	
     7	from academic.benchmarks.core.artifacts import ArtifactStore
     8	from academic.benchmarks.core.maintenance_utils import now_iso
     9	
    10	HELPFUL_REASONS = {
    11	    "token_saving",
    12	    "schema_help",
    13	    "workflow_alignment",
    14	    "correctness_gain",
    15	}
    16	HARMFUL_JUDGMENTS = {"harmful", "negative", "regression"}
    17	HELPFUL_JUDGMENTS = {"helpful", "positive"}
    18	UNCERTAIN_JUDGMENTS = {"neutral", "uncertain", "unknown", ""}
    19	EVIDENCE_LIMIT_PER_SKILL = 24
    20	
    21	
    22	def normalize_credit_events(
    23	    rows: Iterable[Dict[str, Any]],
    24	    *,
    25	    task_id: str = "",
    26	    benchmark: str = "",
    27	    default_source: str = "credit_assigner",
    28	) -> List[Dict[str, Any]]:
    29	    """Return stable credit rows shared by benchmark adapters.
    30	
    31	    Adapters may keep benchmark-specific fields; this helper only normalizes the
    32	    common columns used by bundle/micro/macro orchestration.
    33	    """
    34	
    35	    normalized: List[Dict[str, Any]] = []
    36	    for index, raw in enumerate(rows or []):
    37	        if not isinstance(raw, dict):
    38	            continue
    39	        skill_name = str(raw.get("skill_name") or raw.get("name") or "").strip()
    40	        if not skill_name:
    41	            continue
    42	        judgment = normalize_judgment(raw.get("judgment") or raw.get("polarity") or raw.get("label"))
    43	        confidence = _float_or_default(raw.get("confidence"), 0.0)
    44	        event_task_id = str(raw.get("task_id") or raw.get("source_task_id") or task_id or "").strip()
    45	        event = copy.deepcopy(raw)
    46	        event.update(
    47	            {
    48	                "skill_name": skill_name,
    49	                "task_id": event_task_id,
    50	                "source_task_id": str(raw.get("source_task_id") or event_task_id),
    51	                "benchmark": str(raw.get("benchmark") or benchmark or ""),
    52	                "judgment": judgment,
    53	                "confidence": confidence,
    54	                "evidence_strength": normalize_evidence_strength(
    55	                    raw.get("evidence_strength"),
    56	                    confidence=confidence,
    57	                    judgment=judgment,
    58	                ),
    59	                "used": bool(raw.get("used") or raw.get("called") or raw.get("prompt_injected")),
    60	                "source": str(raw.get("source") or default_source),
    61	                "event_index": int(raw.get("event_index") if raw.get("event_index") is not None else index),
    62	            }
    63	        )
    64	        event.setdefault("maintenance_actions", [])
    65	        event.setdefault("bundle_case_suggestions", [])
    66	        event.setdefault("attribution_scope", "task_local")
    67	        normalized.append(event)
    68	    return normalized
```

```python
    71	def normalize_judgment(value: Any) -> str:
    72	    raw = str(value or "").strip().lower()
    73	    if raw in HARMFUL_JUDGMENTS:
    74	        return "harmful"
    75	    if raw in HELPFUL_JUDGMENTS:
    76	        return "helpful"
    77	    if raw in UNCERTAIN_JUDGMENTS:
    78	        return "uncertain" if raw in {"uncertain", "unknown", ""} else "neutral"
    79	    return raw
    80	
    82	def normalize_evidence_strength(value: Any, *, confidence: float = 0.0, judgment: str = "") -> str:
    83	    raw = str(value or "").strip().lower()
    84	    if raw in {"strong", "medium", "weak", "uncertain"}:
    85	        return raw
    86	    if judgment in {"harmful", "helpful"} and confidence >= 0.75:
    87	        return "strong"
    88	    if judgment in {"harmful", "helpful"} and confidence >= 0.5:
    89	        return "medium"
    90	    if judgment in {"harmful", "helpful"}:
    91	        return "weak"
    92	    return "uncertain"
    93	
    95	def is_strong_harmful_credit(event: Dict[str, Any], *, confidence_threshold: float = 0.65) -> bool:
    96	    judgment = normalize_judgment(event.get("judgment"))
    97	    confidence = _float_or_default(event.get("confidence"), 0.0)
    98	    return judgment == "harmful" and (confidence >= confidence_threshold or bool(event.get("used")))
   101	def is_actionable_helpful_credit(event: Dict[str, Any]) -> bool:
   102	    if normalize_judgment(event.get("judgment")) != "helpful":
   103	        return False
   104	    reasons = _string_set(event.get("helpful_reasons") or event.get("reason_codes") or event.get("reasons"))
   105	    if reasons & HELPFUL_REASONS:
   106	        return True
   107	    action = str(event.get("maintenance_action") or "").strip().lower()
   108	    return action in HELPFUL_REASONS
```

```python
   111	def credit_target_names(
   112	    credit_events: Sequence[Dict[str, Any]],
   113	    *,
   114	    include_helpful: bool = True,
   115	    include_harmful: bool = True,
   116	) -> List[str]:
   117	    names: List[str] = []
   118	    seen = set()
   119	    for event in credit_events or []:
   120	        if include_harmful and is_strong_harmful_credit(event):
   121	            pass
   122	        elif include_helpful and is_actionable_helpful_credit(event):
   123	            pass
   124	        else:
   125	            continue
   126	        name = str(event.get("skill_name") or "").strip()
   127	        if name and name not in seen:
   128	            names.append(name)
   129	            seen.add(name)
   130	    return names
   133	def apply_credit_evidence(
   134	    *,
   135	    store: ArtifactStore,
   136	    credit_events: Sequence[Dict[str, Any]],
   137	    limit_per_skill: int = EVIDENCE_LIMIT_PER_SKILL,
   138	) -> List[Dict[str, Any]]:
   139	    """Append compact credit evidence to skill artifacts in place."""
   141	    applied: List[Dict[str, Any]] = []
   142	    for event in credit_events or []:
   143	        name = str(event.get("skill_name") or "").strip()
   144	        if not name:
   145	            continue
   146	        artifact = store.get(name)
   147	        if artifact is None:
   148	            applied.append({"skill_name": name, "applied": False, "reason": "missing_skill"})
   149	            continue
   150	        evidence = _compact_evidence_event(event)
   151	        judgment = normalize_judgment(event.get("judgment"))
   152	        if judgment == "harmful":
   153	            bucket = artifact.evidence.harmful_cases
   154	        elif judgment == "helpful":
   155	            bucket = artifact.evidence.helpful_cases
   156	        else:
   157	            bucket = artifact.evidence.repeated_evidence
   158	        bucket.append(evidence)
   159	        if len(bucket) > limit_per_skill:
   160	            del bucket[:-limit_per_skill]
   161	        applied.append({"skill_name": name, "applied": True, "judgment": judgment})
   162	    return applied
```

#### Added `academic/benchmarks/core/bundle_cases.py`

Lines 1-134 define common bundle suggestion application. The env-specific part is only `build_case(detail, event, suggestion)`.

```python
    16	CaseBuilder = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], SkillBundleCase | None]
    19	def normalize_bundle_case_suggestions(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    20	    """Normalize credit-assigner bundle suggestions without inventing cases."""
    22	    raw_items = event.get("bundle_case_suggestions") or []
    23	    if isinstance(raw_items, dict):
    24	        raw_items = [raw_items]
    25	    suggestions: List[Dict[str, Any]] = []
    26	    for index, raw in enumerate(raw_items):
    27	        if not isinstance(raw, dict):
    28	            continue
    29	        skill_name = str(raw.get("skill_name") or event.get("skill_name") or "").strip()
    30	        if not skill_name:
    31	            continue
    32	        polarity = str(raw.get("polarity") or _polarity_from_event(event)).strip().lower()
    33	        if polarity not in {"positive", "negative", "integration"}:
    34	            polarity = _polarity_from_event(event)
    35	        suggestion = copy.deepcopy(raw)
    36	        suggestion.update(
    37	            {
    38	                "skill_name": skill_name,
    39	                "polarity": polarity,
    40	                "source_task_id": str(raw.get("source_task_id") or event.get("source_task_id") or event.get("task_id") or ""),
    41	                "suggestion_index": int(raw.get("suggestion_index") if raw.get("suggestion_index") is not None else index),
    42	            }
    43	        )
    44	        suggestion.setdefault("task_fragment_policy", "focused_official_fragment")
    45	        suggestions.append(suggestion)
    46	    return suggestions
```

```python
    49	def apply_credit_bundle_suggestions(
    50	    *,
    51	    store: ArtifactStore,
    52	    detail: Dict[str, Any],
    53	    credit_events: Sequence[Dict[str, Any]],
    54	    build_case: CaseBuilder,
    55	    trim_cases: bool = True,
    56	) -> List[Dict[str, Any]]:
    57	    """Apply credit-created bundle cases using an env-specific case builder."""
    59	    rows: List[Dict[str, Any]] = []
    60	    for event in credit_events or []:
    61	        if not _event_allows_case(event):
    62	            rows.append(
    63	                {
    64	                    "skill_name": event.get("skill_name"),
    65	                    "created": False,
    66	                    "reason": "credit_not_actionable_for_bundle",
    67	                    "judgment": normalize_judgment(event.get("judgment")),
    68	                }
    69	            )
    70	            continue
    71	        suggestions = normalize_bundle_case_suggestions(event)
    72	        if not suggestions:
    73	            rows.append({"skill_name": event.get("skill_name"), "created": False, "reason": "no_suggestions"})
    74	            continue
    75	        for suggestion in suggestions:
    76	            skill_name = str(suggestion.get("skill_name") or "").strip()
    77	            artifact = store.get(skill_name)
    78	            if artifact is None:
    79	                rows.append({"skill_name": skill_name, "created": False, "reason": "missing_skill"})
    80	                continue
    81	            case = build_case(detail, event, suggestion)
    82	            if case is None:
    83	                rows.append({"skill_name": skill_name, "created": False, "reason": "case_builder_returned_none"})
    84	                continue
    85	            case.polarity = str(case.polarity or suggestion.get("polarity") or "integration")
    86	            bucket_name = bundle_bucket(case.polarity)
    87	            bucket = list(getattr(artifact.bundle, bucket_name) or [])
    88	            if any(existing.case_id == case.case_id for existing in bucket):
    89	                rows.append({"skill_name": skill_name, "case_id": case.case_id, "created": False, "reason": "duplicate_case"})
    90	                continue
    91	            bucket.append(case)
    92	            setattr(artifact.bundle, bucket_name, bucket)
    93	            if trim_cases:
    94	                trim_bundle_cases_to_budget(artifact)
    95	            rows.append(
    96	                {
    97	                    "skill_name": skill_name,
    98	                    "case_id": case.case_id,
    99	                    "polarity": case.polarity,
   100	                    "created": True,
   101	                    "bucket": bucket_name,
   102	                }
   103	            )
   104	    return rows
```

#### Added `academic/benchmarks/core/micro_maintenance.py`

Lines 17-175 define common micro orchestration. The ordering is intentionally credit refine first, then bundle test, then repair loop.

```python
    17	@dataclass
    18	class MicroMaintenanceHooks:
    19	    """Benchmark-specific operations used by the generic micro loop."""
    21	    refine_skill: AsyncRefineHook
    22	    run_bundle_test: AsyncBundleTestHook
```

```python
    25	def micro_target_names(
    26	    *,
    27	    credit_events: Sequence[Dict[str, Any]],
    28	    credit_bundle_cases: Sequence[Dict[str, Any]],
    29	    relevant_skill_names: Sequence[str] | None = None,
    30	) -> List[str]:
    31	    """Choose task-local skill targets without scanning the full store."""
    33	    ordered: List[str] = []
    34	    seen = set()
    35	    for name in relevant_skill_names or []:
    36	        value = str(name or "").strip()
    37	        if value and value not in seen:
    38	            ordered.append(value)
    39	            seen.add(value)
    40	    for name in credit_target_names(credit_events):
    41	        if name and name not in seen:
    42	            ordered.append(name)
    43	            seen.add(name)
    44	    for name in bundle_case_rows_by_skill(credit_bundle_cases):
    45	        if name and name not in seen:
    46	            ordered.append(name)
    47	            seen.add(name)
    48	    return ordered
```

```python
    51	async def run_generic_micro_maintenance(
    52	    *,
    53	    detail: Dict[str, Any],
    54	    credit_events: Sequence[Dict[str, Any]],
    55	    credit_bundle_cases: Sequence[Dict[str, Any]],
    56	    store: ArtifactStore,
    57	    config: MaintenanceRunConfig,
    58	    hooks: MicroMaintenanceHooks,
    59	    round_index: int,
    60	    task_index: int,
    61	    relevant_skill_names: Sequence[str] | None = None,
    62	    max_repair_rounds: int | None = None,
    63	) -> Dict[str, Any]:
    64	    """Run the shared credit -> refine -> bundle-test micro flow."""
    66	    targets = micro_target_names(
    67	        credit_events=credit_events,
    68	        credit_bundle_cases=credit_bundle_cases,
    69	        relevant_skill_names=relevant_skill_names,
    70	    )
    71	    if not targets:
    72	        return {
    73	            "phase": "micro",
    74	            "task_id": detail.get("task_id"),
    75	            "task_index": task_index,
    76	            "maintenance_targets": [],
    77	            "maintenance_test_results": [],
    78	            "refine_decisions": [],
    79	            "credit_bundle_cases": copy.deepcopy(list(credit_bundle_cases or [])),
    80	            "reason": "no_micro_targets",
    81	        }
```

```python
    88	    for skill_name in targets:
    89	        artifact = store.get(skill_name)
    90	        if artifact is None:
    91	            refine_decisions.append({"skill_name": skill_name, "action": "skip", "reason": "missing_skill"})
    92	            continue
    93	        skill_credit = [copy.deepcopy(event) for event in credit_events if event.get("skill_name") == skill_name]
    94	        skill_case_rows = grouped_cases.get(skill_name, [])
    95	        if skill_credit or skill_case_rows:
    96	            refine_decisions.append(
    97	                await hooks.refine_skill(
    98	                    skill_name=skill_name,
    99	                    artifact=artifact,
   100	                    detail=detail,
   101	                    credit_events=skill_credit,
   102	                    credit_bundle_cases=skill_case_rows,
   103	                    store=store,
   104	                    config=config,
   105	                    round_index=round_index,
   106	                    task_index=task_index,
   107	                    repair_round=0,
   108	                    stage="credit_pre_refine",
   109	                )
   110	            )
   111	        result = await hooks.run_bundle_test(
   112	            skill_name=skill_name,
   113	            artifact=store.get(skill_name) or artifact,
   114	            detail=detail,
   115	            credit_events=skill_credit,
   116	            credit_bundle_cases=skill_case_rows,
   117	            store=store,
   118	            config=config,
   119	            round_index=round_index,
   120	            task_index=task_index,
   121	            repair_round=0,
   122	        )
   123	        test_results.append(result)
   124	        for repair_round in range(1, repair_limit + 1):
   125	            if _bundle_result_passed(result):
   126	                break
   127	            refine_decisions.append(
   128	                await hooks.refine_skill(
   129	                    skill_name=skill_name,
   130	                    artifact=store.get(skill_name) or artifact,
   131	                    detail=detail,
   132	                    credit_events=skill_credit,
   133	                    credit_bundle_cases=skill_case_rows,
   134	                    store=store,
   135	                    config=config,
   136	                    round_index=round_index,
   137	                    task_index=task_index,
   138	                    repair_round=repair_round,
   139	                    stage="post_bundle_failure",
   140	                    failed_bundle_result=result,
   141	                )
   142	            )
```

#### Added `academic/benchmarks/core/macro_maintenance.py`

Lines 15-118 define window-level macro hook orchestration.

```python
    15	@dataclass
    16	class MacroMaintenanceHooks:
    17	    """Optional benchmark-specific operations in the macro window."""
    19	    promote_pending: AsyncMacroHook | None = None
    20	    refactor_overlap: AsyncMacroHook | None = None
    21	    filter_skills: AsyncMacroHook | None = None
    22	    update_trl: AsyncMacroHook | None = None
```

```python
    25	async def run_generic_macro_maintenance(
    26	    *,
    27	    window_details: Sequence[Dict[str, Any]],
    28	    all_train_details: Sequence[Dict[str, Any]],
    29	    credit_events: Sequence[Dict[str, Any]],
    30	    store: ArtifactStore,
    31	    config: MaintenanceRunConfig,
    32	    hooks: MacroMaintenanceHooks | None = None,
    33	    round_index: int,
    34	    window_index: int,
    35	    final_window: bool = False,
    36	) -> Dict[str, Any]:
    37	    """Run optional window-level hooks and return a stable macro report."""
    39	    hooks = hooks or MacroMaintenanceHooks()
    40	    task_ids = [str(item.get("task_id") or "") for item in window_details]
    41	    report: Dict[str, Any] = {
    42	        "phase": "macro_final" if final_window else "macro",
    43	        "round_index": round_index,
    44	        "window_index": window_index,
    45	        "task_ids": task_ids,
    46	        "n_window_tasks": len(window_details),
    47	        "n_train_tasks_seen": len(all_train_details),
    48	        "credit_summary": summarize_credit_events(credit_events),
    49	        "store_summary": store_summary(store),
    50	        "promoted_pending_skills": [],
    51	        "filtered_skills": [],
    52	        "overlap_refactor": {"attempts": [], "refactor_segment_coverage": []},
    53	        "trl_feedback": {},
    54	    }
```

#### Added `academic/benchmarks/core/relation_graph.py`

Lines 9-117 define a minimal thread-safe heterogeneous graph for later BFCL/Spreadsheet graph unification.

```python
     9	@dataclass
    10	class RelationNode:
    11	    node_id: str
    12	    node_type: str
    13	    label: str = ""
    14	    metadata: Dict[str, Any] = field(default_factory=dict)
    16	    def as_dict(self) -> Dict[str, Any]:
    17	        return asdict(self)
    20	@dataclass
    21	class RelationEdge:
    22	    source: str
    23	    target: str
    24	    relation: str
    25	    weight: float = 1.0
    26	    metadata: Dict[str, Any] = field(default_factory=dict)
    28	    def key(self) -> Tuple[str, str, str]:
    29	        left, right = sorted([self.source, self.target])
    30	        return (left, right, self.relation)
```

```python
    36	class RelationGraphState:
    37	    """Thread-safe in-memory graph for skill/trace/pending relations."""
    39	    def __init__(self) -> None:
    40	        self._lock = threading.RLock()
    41	        self._nodes: Dict[str, RelationNode] = {}
    42	        self._edges: Dict[Tuple[str, str, str], RelationEdge] = {}
    44	    def upsert_node(self, node: RelationNode) -> None:
    45	        with self._lock:
    46	            existing = self._nodes.get(node.node_id)
    47	            if existing is None:
    48	                self._nodes[node.node_id] = node
    49	                return
    50	            merged = dict(existing.metadata)
    51	            merged.update(node.metadata)
    52	            existing.node_type = node.node_type or existing.node_type
    53	            existing.label = node.label or existing.label
    54	            existing.metadata = merged
    56	    def upsert_edge(self, edge: RelationEdge) -> None:
    57	        with self._lock:
    58	            key = edge.key()
    59	            existing = self._edges.get(key)
    60	            if existing is None:
    61	                self._edges[key] = edge
    62	                return
    63	            merged = dict(existing.metadata)
    64	            merged.update(edge.metadata)
    65	            existing.weight = max(float(existing.weight or 0.0), float(edge.weight or 0.0))
    66	            existing.metadata = merged
```

#### Added `academic/benchmarks/tests/test_common_maintenance_core.py`

Lines 33-195 add five focused tests for the new common layer.

```python
    33	def test_normalize_credit_events_and_apply_evidence() -> None:
    34	    store = ArtifactStore([_artifact("skill_a"), _artifact("skill_b")])
    35	    events = normalize_credit_events(
    36	        [
    37	            {
    38	                "skill_name": "skill_a",
    39	                "judgment": "positive",
    40	                "confidence": "0.82",
    41	                "helpful_reasons": ["token_saving"],
    42	                "prompt_injected": True,
    43	            },
    44	            {
    45	                "name": "skill_b",
    46	                "polarity": "negative",
    47	                "confidence": 0.4,
    48	                "used": True,
    49	            },
    50	        ],
    51	        task_id="task_1",
    52	        benchmark="fake",
    53	    )
    55	    assert events[0]["judgment"] == "helpful"
    56	    assert events[0]["evidence_strength"] == "strong"
    57	    assert events[1]["judgment"] == "harmful"
    58	    assert credit_target_names(events) == ["skill_a", "skill_b"]
    60	    applied = apply_credit_evidence(store=store, credit_events=events)
    62	    assert [row["applied"] for row in applied] == [True, True]
    63	    assert store.get("skill_a").evidence.helpful_cases[0]["task_id"] == "task_1"
    64	    assert store.get("skill_b").evidence.harmful_cases[0]["judgment"] == "harmful"
```

```python
    67	def test_apply_credit_bundle_suggestions_uses_env_case_builder() -> None:
    68	    store = ArtifactStore([_artifact("skill_a")])
    69	    events = normalize_credit_events(
    70	        [
    71	            {
    72	                "skill_name": "skill_a",
    73	                "judgment": "harmful",
    74	                "confidence": 0.7,
    75	                "bundle_case_suggestions": [
    76	                    {
    77	                        "polarity": "negative",
    78	                        "reason": "bad arguments",
    79	                        "focus_turn_indices": [1],
    80	                        "expected_contract": {"official_valid": True},
    81	                    }
    82	                ],
    83	            }
    84	        ],
    85	        task_id="task_1",
    86	    )
    88	    def build_case(detail: Dict[str, Any], event: Dict[str, Any], suggestion: Dict[str, Any]) -> SkillBundleCase:
    89	        return SkillBundleCase(
    90	            case_id=f"{suggestion['skill_name']}::{suggestion['source_task_id']}::{suggestion['suggestion_index']}",
    91	            source="credit_assigner_negative",
    92	            prompt=detail["task"]["question"],
    93	            expected={"contract": suggestion["expected_contract"]},
    94	            context={"credit_event": event, "task_fragment": detail["task"]},
    95	            polarity=suggestion["polarity"],
    96	        )
    98	    rows = apply_credit_bundle_suggestions(
    99	        store=store,
   100	        detail={"task_id": "task_1", "task": {"question": "focused prompt"}},
   101	        credit_events=events,
   102	        build_case=build_case,
   103	    )
   105	    assert rows[0]["created"] is True
   106	    assert rows[0]["polarity"] == "negative"
   107	    assert store.get("skill_a").bundle.negative_cases[0].prompt == "focused prompt"
```

```python
   110	async def test_generic_micro_refines_before_testing_and_repairs_until_pass() -> None:
   111	    store = ArtifactStore([_artifact("skill_a")])
   112	    order: List[str] = []
   114	    async def refine_skill(**kwargs: Any) -> Dict[str, Any]:
   115	        order.append(f"refine:{kwargs['stage']}:{kwargs['repair_round']}")
   116	        return {"skill_name": kwargs["skill_name"], "stage": kwargs["stage"], "repair_round": kwargs["repair_round"]}
   118	    async def run_bundle_test(**kwargs: Any) -> Dict[str, Any]:
   119	        order.append(f"test:{kwargs['repair_round']}")
   120	        return {"skill_name": kwargs["skill_name"], "passed": kwargs["repair_round"] >= 1}
   122	    report = await run_generic_micro_maintenance(
   123	        detail={"task_id": "task_1"},
   124	        credit_events=normalize_credit_events(
   125	            [{"skill_name": "skill_a", "judgment": "harmful", "confidence": 0.9}],
   126	            task_id="task_1",
   127	        ),
   128	        credit_bundle_cases=[{"skill_name": "skill_a", "created": True, "case_id": "case_1"}],
   129	        store=store,
   130	        config=MaintenanceRunConfig(llm_config="fake", extra={"micro_refine_max_repair_rounds": 2}),
   131	        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
   132	        round_index=0,
   133	        task_index=0,
   134	    )
   136	    assert order == ["refine:credit_pre_refine:0", "test:0", "refine:post_bundle_failure:1", "test:1"]
   137	    assert report["maintenance_targets"] == ["skill_a"]
   138	    assert len(report["maintenance_test_results"]) == 2
```

```python
   167	def test_relation_graph_state_supports_concurrent_short_updates() -> None:
   168	    graph = RelationGraphState()
   170	    async def update_one(index: int) -> None:
   171	        graph.update(
   172	            nodes=[
   173	                trace_relation_node(f"task_{index}", f"seg_{index}"),
   174	                skill_relation_node("skill_shared", source_task_ids=[f"task_{index}"]),
   175	                pending_skill_relation_node(f"pending_{index}"),
   176	            ],
   177	            edges=[
   178	                RelationEdge(
   179	                    source=f"trace_segment:seg_{index}",
   180	                    target="skill:skill_shared",
   181	                    relation="overlap",
   182	                    weight=0.5 + index,
   183	                )
   184	            ],
   185	        )
   187	    async def run_updates() -> None:
   188	        await asyncio.gather(*(update_one(index) for index in range(15)))
   190	    asyncio.run(run_updates())
   191	    snapshot = graph.snapshot()
   193	    assert len(snapshot["nodes"]) == 31
   194	    assert len(snapshot["edges"]) == 15
   195	    assert len(graph.neighbors("skill:skill_shared")) == 15
```

### Tests

Passed.

#### Test Coverage Expansion 2026-05-19

The original Chapter 1 test pass covered only five main paths. The test file was expanded from 5 tests to 14 tests so each common component has explicit normal-path, boundary, and failure-path coverage.

##### Updated imports in `academic/benchmarks/tests/test_common_maintenance_core.py`

Lines 4-31 now import every public helper that receives direct component coverage.

```python
     4	from academic.benchmarks.core.artifacts import ArtifactStore
     5	from academic.benchmarks.core.bundle_cases import (
     6	    apply_credit_bundle_suggestions,
     7	    bundle_case_rows_by_skill,
     8	    normalize_bundle_case_suggestions,
     9	)
    10	from academic.benchmarks.core.credit_events import (
    11	    apply_credit_evidence,
    12	    credit_target_names,
    13	    is_actionable_helpful_credit,
    14	    is_strong_harmful_credit,
    15	    normalize_credit_events,
    16	    normalize_evidence_strength,
    17	    normalize_judgment,
    18	    summarize_credit_events,
    19	)
    20	from academic.benchmarks.core.macro_maintenance import MacroMaintenanceHooks, run_generic_macro_maintenance, store_summary
    21	from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig
    22	from academic.benchmarks.core.micro_maintenance import MicroMaintenanceHooks, micro_target_names, run_generic_micro_maintenance
    23	from academic.benchmarks.core.relation_graph import (
    24	    RelationEdge,
    25	    RelationGraphState,
    26	    RelationNode,
    27	    pending_skill_relation_node,
    28	    skill_relation_node,
    29	    trace_relation_node,
    30	)
    31	from academic.benchmarks.core.types import SkillArtifact, SkillBundleCase
```

##### Credit event component tests

Lines 77-130 cover alias fields, invalid confidence fallback, judgment normalization, evidence strength thresholds, target selection, summary counts, missing skill handling, and evidence bucket limits.

```python
    77	def test_credit_event_helpers_cover_aliases_strengths_targets_and_limits() -> None:
    78	    rows = normalize_credit_events(
    79	        [
    80	            {"name": "missing_name_is_kept", "label": "unknown", "confidence": "bad"},
    81	            {"skill_name": "", "judgment": "helpful"},
    82	            {"skill_name": "skill_h", "judgment": "regression", "confidence": 0.1, "used": True},
    83	            {"skill_name": "skill_p", "judgment": "positive", "confidence": 0.51, "reasons": "schema_help"},
    84	            {"skill_name": "skill_n", "judgment": "neutral", "confidence": 0.99},
    85	        ],
    86	        task_id="task_alias",
    87	        benchmark="bench",
    88	        default_source="unit_source",
    89	    )
    91	    assert [row["skill_name"] for row in rows] == ["missing_name_is_kept", "skill_h", "skill_p", "skill_n"]
    92	    assert rows[0]["judgment"] == "uncertain"
    93	    assert rows[0]["confidence"] == 0.0
    94	    assert rows[0]["source"] == "unit_source"
    95	    assert normalize_judgment("negative") == "harmful"
    96	    assert normalize_judgment("positive") == "helpful"
    97	    assert normalize_judgment("") == "uncertain"
    98	    assert normalize_evidence_strength(None, confidence=0.8, judgment="harmful") == "strong"
    99	    assert normalize_evidence_strength(None, confidence=0.55, judgment="helpful") == "medium"
   100	    assert normalize_evidence_strength(None, confidence=0.2, judgment="helpful") == "weak"
   101	    assert is_strong_harmful_credit(rows[1]) is True
   102	    assert is_actionable_helpful_credit(rows[2]) is True
   103	    assert credit_target_names(rows) == ["skill_h", "skill_p"]
   105	    summary = summarize_credit_events(rows)
   107	    assert summary["total"] == 4
   108	    assert summary["harmful"] == 1
   109	    assert summary["helpful"] == 1
   110	    assert summary["neutral"] == 1
   111	    assert summary["uncertain"] == 1
   112	    assert summary["skills"]["skill_h"]["harmful"] == 1
```

```python
   115	def test_apply_credit_evidence_handles_missing_skills_and_bucket_limits() -> None:
   116	    store = ArtifactStore([_artifact("skill_a")])
   117	    events = normalize_credit_events(
   118	        [
   119	            {"skill_name": "skill_a", "judgment": "neutral", "confidence": 0.1, "task_id": f"task_{idx}"}
   120	            for idx in range(4)
   121	        ]
   122	        + [{"skill_name": "missing", "judgment": "harmful", "confidence": 0.9}],
   123	        benchmark="fake",
   124	    )
   126	    applied = apply_credit_evidence(store=store, credit_events=events, limit_per_skill=2)
   128	    assert applied[-1] == {"skill_name": "missing", "applied": False, "reason": "missing_skill"}
   129	    repeated = store.get("skill_a").evidence.repeated_evidence
   130	    assert [row["task_id"] for row in repeated] == ["task_2", "task_3"]
```

##### Bundle case component tests

Lines 176-287 cover helpful positive cases, neutral integration cases, missing skill rows, non-actionable credit rows, duplicate case suppression, `build_case=None`, grouping by skill, and budget trimming.

```python
   176	def test_bundle_case_helpers_cover_positive_integration_duplicates_and_failures() -> None:
   177	    store = ArtifactStore([_artifact("skill_a"), _artifact("skill_b")])
   178	    events = normalize_credit_events(
   179	        [
   180	            {
   181	                "skill_name": "skill_a",
   182	                "judgment": "helpful",
   183	                "confidence": 0.8,
   184	                "helpful_reasons": ["workflow_alignment"],
   185	                "bundle_case_suggestions": {"polarity": "positive", "expected_contract": {"score": 1}},
   186	            },
   187	            {
   188	                "skill_name": "skill_b",
   189	                "judgment": "neutral",
   190	                "bundle_case_suggestions": [{"polarity": "integration", "expected_contract": {"valid": True}}],
   191	            },
   192	            {
   193	                "skill_name": "skill_missing",
   194	                "judgment": "harmful",
   195	                "confidence": 1.0,
   196	                "bundle_case_suggestions": [{"polarity": "negative"}],
   197	            },
   198	            {"skill_name": "skill_a", "judgment": "neutral", "bundle_case_suggestions": []},
   199	        ],
   200	        task_id="task_2",
   201	    )
   202	    calls: List[str] = []
   204	    def build_case(detail: Dict[str, Any], event: Dict[str, Any], suggestion: Dict[str, Any]) -> SkillBundleCase | None:
   205	        calls.append(suggestion["skill_name"])
   206	        if detail.get("return_none"):
   207	            return None
   208	        return SkillBundleCase(
   209	            case_id=f"{suggestion['skill_name']}::{suggestion['polarity']}",
   210	            source=f"credit_assigner_{suggestion['polarity']}",
   211	            prompt="fragment",
   212	            expected=suggestion.get("expected_contract") or {},
   213	            context={"credit_event": event},
   214	            polarity=suggestion["polarity"],
   215	        )
   217	    assert normalize_bundle_case_suggestions(events[0])[0]["task_fragment_policy"] == "focused_official_fragment"
   218	    rows = apply_credit_bundle_suggestions(
   219	        store=store,
   220	        detail={"task_id": "task_2"},
   221	        credit_events=events,
   222	        build_case=build_case,
   223	    )
   224	    duplicate_rows = apply_credit_bundle_suggestions(
   225	        store=store,
   226	        detail={"task_id": "task_2"},
   227	        credit_events=[events[0]],
   228	        build_case=build_case,
   229	    )
   230	    none_rows = apply_credit_bundle_suggestions(
   231	        store=store,
   232	        detail={"return_none": True},
   233	        credit_events=[events[1]],
   234	        build_case=build_case,
   235	    )
   237	    assert [row["reason"] for row in rows if not row["created"]] == [
   238	        "missing_skill",
   239	        "credit_not_actionable_for_bundle",
   240	    ]
   241	    assert rows[0]["bucket"] == "positive_cases"
   242	    assert rows[1]["bucket"] == "integration_cases"
   243	    assert duplicate_rows[0]["reason"] == "duplicate_case"
   244	    assert none_rows[0]["reason"] == "case_builder_returned_none"
   245	    assert bundle_case_rows_by_skill(rows)["skill_a"][0]["polarity"] == "positive"
   246	    assert store.get("skill_a").bundle.positive_cases[0].case_id == "skill_a::positive"
   247	    assert store.get("skill_b").bundle.integration_cases[0].case_id == "skill_b::integration"
   248	    assert calls[:2] == ["skill_a", "skill_b"]
```

```python
   251	def test_bundle_case_budget_is_applied_after_credit_cases() -> None:
   252	    store = ArtifactStore([_artifact("skill_a")])
   253	    events = normalize_credit_events(
   254	        [
   255	            {
   256	                "skill_name": "skill_a",
   257	                "judgment": "harmful",
   258	                "confidence": 0.9,
   259	                "bundle_case_suggestions": [
   260	                    {"polarity": "negative", "expected_contract": {"idx": idx}}
   261	                    for idx in range(4)
   262	                ],
   263	            }
   264	        ],
   265	        task_id="task_budget",
   266	    )
   268	    def build_case(detail: Dict[str, Any], event: Dict[str, Any], suggestion: Dict[str, Any]) -> SkillBundleCase:
   269	        return SkillBundleCase(
   270	            case_id=f"case_{suggestion['suggestion_index']}",
   271	            source="credit_assigner_negative",
   272	            prompt="p",
   273	            expected=suggestion["expected_contract"],
   274	            context={"confidence": suggestion["suggestion_index"] / 10},
   275	            polarity="negative",
   276	        )
   278	    rows = apply_credit_bundle_suggestions(
   279	        store=store,
   280	        detail={},
   281	        credit_events=events,
   282	        build_case=build_case,
   283	    )
   285	    assert len([row for row in rows if row["created"]]) == 4
   286	    assert len(store.get("skill_a").bundle.negative_cases) == 2
   287	    assert store.get("skill_a").bundle.fixtures["bundle_trimmed"] is True
```

##### Micro maintenance component tests

Lines 290-406 cover target ordering/deduplication, no-target returns, missing skill skip, refine-before-test ordering, post-failure repair, and explicit repair limit override.

```python
   290	def test_micro_target_names_deduplicates_and_orders_sources() -> None:
   291	    targets = micro_target_names(
   292	        relevant_skill_names=["skill_z", "skill_h", "skill_z"],
   293	        credit_events=normalize_credit_events(
   294	            [
   295	                {"skill_name": "skill_h", "judgment": "harmful", "confidence": 0.8},
   296	                {"skill_name": "skill_p", "judgment": "helpful", "helpful_reasons": ["correctness_gain"]},
   297	            ],
   298	            task_id="task",
   299	        ),
   300	        credit_bundle_cases=[
   301	            {"skill_name": "skill_bundle", "created": True},
   302	            {"skill_name": "skill_h", "created": True},
   303	        ],
   304	    )
   306	    assert targets == ["skill_z", "skill_h", "skill_p", "skill_bundle"]
```

```python
   340	async def test_generic_micro_no_targets_and_missing_skill_paths() -> None:
   341	    calls: List[str] = []
   343	    async def refine_skill(**kwargs: Any) -> Dict[str, Any]:
   344	        calls.append("refine")
   345	        return {"skill_name": kwargs["skill_name"]}
   347	    async def run_bundle_test(**kwargs: Any) -> Dict[str, Any]:
   348	        calls.append("test")
   349	        return {"passed": True}
   351	    no_target = await run_generic_micro_maintenance(
   352	        detail={"task_id": "task_none"},
   353	        credit_events=[],
   354	        credit_bundle_cases=[],
   355	        store=ArtifactStore(),
   356	        config=MaintenanceRunConfig(llm_config="fake"),
   357	        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
   358	        round_index=0,
   359	        task_index=0,
   360	    )
   361	    missing = await run_generic_micro_maintenance(
   362	        detail={"task_id": "task_missing"},
   363	        credit_events=[],
   364	        credit_bundle_cases=[],
   365	        store=ArtifactStore(),
   366	        config=MaintenanceRunConfig(llm_config="fake"),
   367	        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
   368	        round_index=0,
   369	        task_index=1,
   370	        relevant_skill_names=["missing_skill"],
   371	    )
   373	    assert no_target["reason"] == "no_micro_targets"
   374	    assert missing["refine_decisions"] == [{"skill_name": "missing_skill", "action": "skip", "reason": "missing_skill"}]
   375	    assert calls == []
```

```python
   378	async def test_generic_micro_passes_without_repair_and_honors_explicit_repair_limit() -> None:
   379	    store = ArtifactStore([_artifact("skill_a")])
   380	    order: List[str] = []
   382	    async def refine_skill(**kwargs: Any) -> Dict[str, Any]:
   383	        order.append(f"refine:{kwargs['repair_round']}")
   384	        return {"skill_name": kwargs["skill_name"], "repair_round": kwargs["repair_round"]}
   386	    async def run_bundle_test(**kwargs: Any) -> Dict[str, Any]:
   387	        order.append(f"test:{kwargs['repair_round']}")
   388	        return {"skill_name": kwargs["skill_name"], "passed": False}
   390	    report = await run_generic_micro_maintenance(
   391	        detail={"task_id": "task_limit"},
   392	        credit_events=normalize_credit_events(
   393	            [{"skill_name": "skill_a", "judgment": "harmful", "confidence": 1.0}],
   394	            task_id="task_limit",
   395	        ),
   396	        credit_bundle_cases=[],
   397	        store=store,
   398	        config=MaintenanceRunConfig(llm_config="fake", extra={"micro_refine_max_repair_rounds": 9}),
   399	        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
   400	        round_index=0,
   401	        task_index=0,
   402	        max_repair_rounds=0,
   403	    )
   405	    assert order == ["refine:0", "test:0"]
   406	    assert len(report["maintenance_test_results"]) == 1
```

##### Macro maintenance component tests

Lines 435-494 cover final-window default report behavior, store summary counts, all optional hook branches, and result field mapping.

```python
   435	async def test_generic_macro_final_window_no_hooks_and_all_hooks() -> None:
   436	    store = ArtifactStore([_artifact("active"), _artifact("pending"), _artifact("disabled")])
   437	    store.get("pending").status = "pending"
   438	    store.get("disabled").status = "disabled"
   439	    calls: List[str] = []
   441	    no_hooks = await run_generic_macro_maintenance(
   442	        window_details=[{"task_id": "task_final"}],
   443	        all_train_details=[{"task_id": "task_final"}],
   444	        credit_events=[],
   445	        store=store,
   446	        config=MaintenanceRunConfig(llm_config="fake"),
   447	        round_index=1,
   448	        window_index=2,
   449	        final_window=True,
   450	    )
   452	    async def promote_pending(**kwargs: Any) -> Dict[str, Any]:
   453	        calls.append(f"promote:{kwargs['final_window']}")
   454	        return {"promoted": ["pending"]}
   456	    async def refactor_overlap(**kwargs: Any) -> Dict[str, Any]:
   457	        calls.append("refactor")
   458	        return {"attempts": [{"group": "g"}], "refactor_segment_coverage": ["task_final"]}
   460	    async def filter_skills(**kwargs: Any) -> Dict[str, Any]:
   461	        calls.append("filter")
   462	        return {"disabled_skills": ["disabled"]}
   464	    async def update_trl(**kwargs: Any) -> Dict[str, Any]:
   465	        calls.append("trl")
   466	        return {"feedback_events": 1}
   468	    all_hooks = await run_generic_macro_maintenance(
   469	        window_details=[{"task_id": "task_final"}],
   470	        all_train_details=[{"task_id": "task_final"}],
   471	        credit_events=[],
   472	        store=store,
   473	        config=MaintenanceRunConfig(llm_config="fake"),
   474	        hooks=MacroMaintenanceHooks(
   475	            promote_pending=promote_pending,
   476	            refactor_overlap=refactor_overlap,
   477	            filter_skills=filter_skills,
   478	            update_trl=update_trl,
   479	        ),
   480	        round_index=1,
   481	        window_index=3,
   482	        final_window=True,
   483	    )
   485	    assert no_hooks["phase"] == "macro_final"
   486	    assert no_hooks["overlap_refactor"] == {"attempts": [], "refactor_segment_coverage": []}
   487	    assert store_summary(store)["n_active"] == 1
   488	    assert store_summary(store)["n_pending"] == 1
   489	    assert store_summary(store)["n_disabled"] == 1
   490	    assert calls == ["promote:True", "refactor", "filter", "trl"]
   491	    assert all_hooks["promoted_pending_skills"] == ["pending"]
   492	    assert all_hooks["overlap_refactor"]["attempts"][0]["group"] == "g"
   493	    assert all_hooks["filtered_skills"] == ["disabled"]
   494	    assert all_hooks["trl_feedback"] == {"feedback_events": 1}
```

##### Relation graph component tests

Lines 528-543 cover node metadata merging, undirected edge deduplication, max-weight preservation, metadata merging, and missing-node neighbor lookup.

```python
   528	def test_relation_graph_merges_nodes_edges_and_preserves_max_weight() -> None:
   529	    graph = RelationGraphState()
   530	    graph.upsert_node(RelationNode(node_id="skill:s", node_type="skill", label="old", metadata={"a": 1}))
   531	    graph.upsert_node(RelationNode(node_id="skill:s", node_type="skill", label="new", metadata={"b": 2}))
   532	    graph.upsert_node(trace_relation_node("task_1", "seg_1"))
   533	    graph.upsert_edge(RelationEdge(source="skill:s", target="trace_segment:seg_1", relation="overlap", weight=0.2, metadata={"first": True}))
   534	    graph.upsert_edge(RelationEdge(source="trace_segment:seg_1", target="skill:s", relation="overlap", weight=0.9, metadata={"second": True}))
   536	    snapshot = graph.snapshot()
   538	    assert snapshot["nodes"][0]["label"] == "new"
   539	    assert snapshot["nodes"][0]["metadata"] == {"a": 1, "b": 2}
   540	    assert len(snapshot["edges"]) == 1
   541	    assert snapshot["edges"][0]["weight"] == 0.9
   542	    assert snapshot["edges"][0]["metadata"] == {"first": True, "second": True}
   543	    assert graph.neighbors("missing") == []
```

Updated test result:

```text
$ pytest -q academic/benchmarks/tests/test_common_maintenance_core.py
..............                                                           [100%]
14 passed, 2 warnings in 0.06s
```

Regression checks:

```text
$ pytest -q academic/benchmarks/tests/test_generic_evolution.py academic/benchmarks/tests/test_credit_scope.py academic/benchmarks/tests/test_skill_injector_budget.py
.........                                                                [100%]
9 passed, 10 warnings in 0.44s
```

```text
$ python -m py_compile academic/benchmarks/core/credit_events.py academic/benchmarks/core/bundle_cases.py academic/benchmarks/core/micro_maintenance.py academic/benchmarks/core/macro_maintenance.py academic/benchmarks/core/relation_graph.py academic/benchmarks/tests/test_common_maintenance_core.py
```

```text
$ pytest -q academic/benchmarks/tests/test_common_maintenance_core.py
.....                                                                    [100%]
5 passed, 2 warnings in 0.12s
```

```text
$ pytest -q academic/benchmarks/tests/test_generic_evolution.py academic/benchmarks/tests/test_credit_scope.py academic/benchmarks/tests/test_skill_injector_budget.py
.........                                                                [100%]
9 passed, 10 warnings in 0.41s
```

```text
$ python -m py_compile academic/benchmarks/core/credit_events.py academic/benchmarks/core/bundle_cases.py academic/benchmarks/core/micro_maintenance.py academic/benchmarks/core/macro_maintenance.py academic/benchmarks/core/relation_graph.py academic/benchmarks/tests/test_common_maintenance_core.py
```

## Chapter 2: Spreadsheet 接入公共层

### Plan

- 将 `spreadsheet/adapter.py` 继续拆成 `spreadsheet/maintenance/*`。
- `spreadsheet/adapter.py` 保留 facade 和旧符号导出。
- 接入 Chapter 1 的 credit/bundle/micro/macro 公共工具。
- 保持 notebook runtime、function skill callable、旧测试 monkeypatch 行为兼容。

### Implementation Log

Not started.

### Tests

Not started.

## Chapter 3: BFCL 低风险接入公共层

### Plan

- BFCL bundle trim 接入公共 bundle policy/wrapper。
- BFCL credit event evidence 写入接入公共 credit 工具。
- BFCL micro target 选择接入公共 helper。
- 保留 BFCL official evaluator、tool replay、overlap refactor prompt。
- 保持 resume/checkpoint/output schema 兼容。

### Implementation Log

Not started.

### Tests

Not started.

## Chapter 4: BFCL 结构拆分

### Plan

- 从 `bfcl/related/experiment.py` 抽出 credit、micro、macro、checkpoint/report glue。
- 从 `bfcl/maintenance/adapter.py` 抽出 bundles、replay、refine、overlap、prompt builders。
- 原入口保留 facade，旧 CLI/import 不变。

### Implementation Log

Not started.

### Tests

Not started.

## Chapter 5: 更多 Env 接入准备

### Plan

- 写 fake benchmark adapter 文档和测试，证明最小 hooks 可跑。
- 补充新 env 接入说明：
  - loader
  - executor
  - verifier
  - trace projector
  - bundle case fragment builder
  - optional function skill runtime

### Implementation Log

Not started.

### Tests

Not started.
