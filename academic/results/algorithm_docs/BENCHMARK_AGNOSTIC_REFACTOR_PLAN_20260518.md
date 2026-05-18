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

Completed.

#### Added `academic/benchmarks/spreadsheet/maintenance/__init__.py`

Lines 1-5 define the new Spreadsheet maintenance package entrypoint.

```python
     1	"""SpreadsheetBench maintenance implementation modules."""
     2	
     3	from academic.benchmarks.spreadsheet.maintenance.adapter import SpreadsheetMaintenanceAdapter
     4	
     5	__all__ = ["SpreadsheetMaintenanceAdapter"]
```

#### Replaced `academic/benchmarks/spreadsheet/adapter.py` with a compatibility facade

Lines 1-110 now keep the old public/test-facing import surface while moving maintenance implementation into `spreadsheet/maintenance/adapter.py`.

```python
     1	"""SpreadsheetBench compatibility facade.
     2	
     3	Execution, loading, verification, skill runtime, trace projection, and
     4	maintenance helpers live in focused modules.  This facade preserves the public
     5	and test-facing imports that previously came from this file.
     6	"""
     7	from __future__ import annotations
     9	from typing import Any
    11	import academic.benchmarks.spreadsheet.executor as _spreadsheet_executor
    12	from academic.benchmarks.core.llm_text import ask_text_llm
    13	from academic.benchmarks.core.types import BenchmarkResult
    14	from academic.benchmarks.spreadsheet.executor import (
    15	    build_spreadsheet_notebook_prompt as _build_spreadsheet_notebook_prompt,
    16	    build_spreadsheet_notebook_turn_prompt as _build_spreadsheet_notebook_turn_prompt,
    17	    build_spreadsheet_prompt as _build_spreadsheet_prompt,
    18	    clip_notebook_text as _clip_notebook_text,
    19	    run_spreadsheet_task as _run_spreadsheet_task_impl,
    20	    run_spreadsheet_task_notebook as _run_spreadsheet_task_notebook_impl,
    21	    workbook_preview as _workbook_preview,
    22	)
    23	from academic.benchmarks.spreadsheet.loader import ensure_spreadsheetbench, load_spreadsheet_tasks
    24	from academic.benchmarks.spreadsheet.maintenance.adapter import *  # noqa: F401,F403
    25	from academic.benchmarks.spreadsheet.maintenance.adapter import (
    26	    SpreadsheetMaintenanceAdapter,
    27	    _artifact_semantic_signature,
    28	    _coerce_spreadsheet_artifact,
    29	    _dedupe_spreadsheet_skills,
    30	    _execute_spreadsheet_bundle_tests,
    31	    _extract_spreadsheet_skills_from_detail,
    32	    _filter_spreadsheet_harmful_skills,
    33	    _heuristic_spreadsheet_artifact_payload,
    34	    _heuristic_spreadsheet_credit_events,
    35	    _heuristic_spreadsheet_repair_artifact_payload,
    36	    _normalize_spreadsheet_bundle_suggestion,
    37	    _normalize_spreadsheet_credit_events,
    38	    _promote_spreadsheet_pending_from_window,
    39	    _refine_spreadsheet_skill_from_bundle,
    40	    _refine_spreadsheet_skill_from_credit,
    41	    _run_spreadsheet_refiner,
    42	    _spreadsheet_case_from_credit_suggestion,
    43	    _spreadsheet_case_from_task,
    44	    _spreadsheet_dedupe_key,
    45	    _spreadsheet_formula_tokens,
    46	    _spreadsheet_has_repair_evidence,
    47	    _spreadsheet_keywords,
    48	    _spreadsheet_micro_targets,
    49	    _spreadsheet_scope_overlap,
    50	    _spreadsheet_test_result_from_dict,
    51	    _trim_spreadsheet_bundle_cases,
    52	)
```

```python
   103	async def run_spreadsheet_task(*args: Any, **kwargs: Any) -> BenchmarkResult:
   104	    _spreadsheet_executor.ask_text_llm = ask_text_llm
   105	    return await _run_spreadsheet_task_impl(*args, **kwargs)
   108	async def run_spreadsheet_task_notebook(*args: Any, **kwargs: Any) -> BenchmarkResult:
   109	    _spreadsheet_executor.ask_text_llm = ask_text_llm
   110	    return await _run_spreadsheet_task_notebook_impl(*args, **kwargs)
```

#### Added `academic/benchmarks/spreadsheet/maintenance/adapter.py`

This module now owns Spreadsheet maintenance. It currently keeps the legacy helper functions in one maintenance module while the top-level adapter becomes a facade. The module also starts using Chapter 1 common helpers.

Lines 9-20 import the common maintenance primitives.

```python
     9	from academic.benchmarks.core.artifacts import ArtifactStore
    10	from academic.benchmarks.core.bundle_cases import apply_credit_bundle_suggestions
    11	from academic.benchmarks.core.bundle_policy import default_bundle_case_priority, trim_bundle_cases_to_budget
    12	from academic.benchmarks.core.credit_events import apply_credit_evidence, normalize_credit_events
    13	from academic.benchmarks.core.credit_scope import (
    14	    credit_candidate_skill_names,
    15	    skill_exposure_flags,
    16	)
    17	from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig, NoOpMaintenanceAdapter
    18	from academic.benchmarks.core.maintenance_utils import json_block, now_iso, stable_id
    19	from academic.benchmarks.core.micro_maintenance import MicroMaintenanceHooks, micro_target_names, run_generic_micro_maintenance
```

Lines 93-104 route LLM/refiner calls through the facade so old tests and scripts that monkeypatch `academic.benchmarks.spreadsheet.adapter._ask_json` or `refine_skill_artifact_llm` still affect maintenance execution.

```python
    93	def _compat_module() -> Any:
    94	    import academic.benchmarks.spreadsheet.adapter as facade
    96	    return facade
    99	async def _compat_ask_json(**kwargs: Any) -> Dict[str, Any]:
   100	    return await _compat_module()._ask_json(**kwargs)
   103	async def _compat_refine_skill_artifact_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
   104	    return await _compat_module().refine_skill_artifact_llm(*args, **kwargs)
```

Lines 129-159 preserve the generic runner contract while routing task execution through the facade, so monkeypatching `spreadsheet.adapter.run_spreadsheet_task` continues to work.

```python
   129	    async def run_task(
   130	        self,
   131	        task: BenchmarkTask,
   132	        *,
   133	        store: ArtifactStore,
   134	        config: MaintenanceRunConfig,
   135	        phase: str,
   136	        task_index: int,
   137	        run_idx: int,
   138	    ) -> BenchmarkResult:
   139	        del phase, task_index, run_idx
   140	        if str(config.extra.get("spreadsheet_execution_mode") or "single").strip().lower() == "notebook":
   141	            return await _compat_module().run_spreadsheet_task_notebook(
   142	                task,
   143	                llm_config=config.llm_config,
   144	                model_name=config.model_name,
   145	                artifact_store=store,
   146	                top_k_skills=config.top_k_skills,
   147	                skill_injector_mode=config.extra.get("skill_injector_mode"),
   148	                skill_context_budget_chars=config.extra.get("skill_context_budget_chars"),
   149	                max_turns=int(config.extra.get("spreadsheet_max_turns") or 5),
   150	            )
   151	        return await _compat_module().run_spreadsheet_task(
   152	            task,
   153	            llm_config=config.llm_config,
   154	            model_name=config.model_name,
   155	            artifact_store=store,
   156	            top_k_skills=config.top_k_skills,
   157	            skill_injector_mode=config.extra.get("skill_injector_mode"),
   158	            skill_context_budget_chars=config.extra.get("skill_context_budget_chars"),
   159	        )
```

Lines 161-221 use `apply_credit_evidence` from the common core after Spreadsheet-specific credit assignment.

```python
   161	    async def assign_credit(
   162	        self,
   163	        *,
   164	        detail: Dict[str, Any],
   165	        store: ArtifactStore,
   166	        config: MaintenanceRunConfig,
   167	        round_index: int,
   168	        task_index: int,
   169	    ) -> List[Dict[str, Any]]:
   170	        del round_index
   171	        projection = _spreadsheet_trace_projection(detail)
   172	        candidate_names = credit_candidate_skill_names(projection)
   173	        candidate_artifacts = [
   174	            artifact for name in candidate_names for artifact in [store.get(str(name))] if artifact
   175	        ]
   176	        if not candidate_artifacts:
   177	            return []
   178	        try:
   179	            payload = await _compat_ask_json(
   180	                system=SPREADSHEET_CREDIT_SYSTEM,
   181	                user=json_block(
   182	                    {
   183	                        "task": _spreadsheet_task_fragment(detail),
   184	                        "trace_projection": projection,
   185	                        "retrieval_audit": {
   186	                            "retrieved_only_skills": projection.get("retrieved_only_skills") or [],
   187	                            "candidate_policy": "prompt_injected_or_called_only",
   188	                        },
   189	                        "candidate_skills": [
   190	                            _spreadsheet_skill_projection(artifact, projection=projection)
   191	                            for artifact in candidate_artifacts
   192	                        ],
   193	                    }
   194	                ),
   195	                llm_config=config.llm_config,
   196	                model_name=config.model_name,
   197	                role="spreadsheet_credit_assigner",
   198	                metadata={"task_id": detail.get("task_id"), "task_index": task_index},
   199	            )
   200	            events = _normalize_spreadsheet_credit_events(
   201	                payload,
   202	                detail=detail,
   203	                candidate_artifacts=candidate_artifacts,
   204	                projection=projection,
   205	            )
   206	        except Exception as exc:
   207	            events = _heuristic_spreadsheet_credit_events(
   208	                detail=detail,
   209	                candidate_artifacts=candidate_artifacts,
   210	                projection=projection,
   211	                reason=f"credit_llm_failed:{type(exc).__name__}",
   212	            )
   213	        apply_credit_evidence(store=store, credit_events=events)
   214	        for event in events:
   215	            artifact = store.get(event["skill_name"])
   216	            if artifact is None:
   217	                continue
   218	            if event.get("judgment") == "helpful":
   219	                artifact.success_count += 1
   220	            artifact.usage_count += 1
   221	        return events
```

Lines 223-269 replace Spreadsheet's local bundle append loop with `apply_credit_bundle_suggestions`, while preserving bundle version bump behavior for created cases.

```python
   223	    async def apply_credit_bundle_cases(
   224	        self,
   225	        *,
   226	        detail: Dict[str, Any],
   227	        credit_events: Sequence[Dict[str, Any]],
   228	        store: ArtifactStore,
   229	        config: MaintenanceRunConfig,
   230	        round_index: int,
   231	        task_index: int,
   232	    ) -> List[Dict[str, Any]]:
   233	        del config, round_index, task_index
   235	        def build_case(case_detail: Dict[str, Any], event: Dict[str, Any], suggestion: Dict[str, Any]) -> SkillBundleCase | None:
   236	            artifact = store.get(str(suggestion.get("skill_name") or event.get("skill_name") or ""))
   237	            if artifact is None:
   238	                return None
   239	            return _spreadsheet_case_from_credit_suggestion(
   240	                detail=case_detail,
   241	                artifact=artifact,
   242	                event=event,
   243	                suggestion=dict(suggestion or {}),
   244	            )
   246	        rows = apply_credit_bundle_suggestions(
   247	            store=store,
   248	            detail=detail,
   249	            credit_events=credit_events,
   250	            build_case=build_case,
   251	            trim_cases=True,
   252	        )
   253	        created = []
   254	        for row in rows:
   255	            if not row.get("created"):
   256	                continue
   257	            artifact = store.get(str(row.get("skill_name") or ""))
   258	            if artifact is not None:
   259	                artifact.bundle.bundle_version += 1
   260	            created.append(
   261	                {
   262	                    "skill_name": row.get("skill_name"),
   263	                    "case_id": row.get("case_id"),
   264	                    "polarity": row.get("polarity"),
   265	                    "source_task_id": detail.get("task_id"),
   266	                    "reason": row.get("reason"),
   267	                }
   268	            )
   269	        return created
```

Lines 271-352 replace Spreadsheet's local micro loop with `run_generic_micro_maintenance`, while keeping extraction reports and Spreadsheet-specific refine/test hooks.

```python
   271	    async def run_micro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
   272	        detail = kwargs.get("detail") or {}
   273	        credit_events = list(kwargs.get("credit_events") or [])
   274	        credit_bundle_cases = list(kwargs.get("credit_bundle_cases") or [])
   275	        store: ArtifactStore = kwargs["store"]
   276	        config: MaintenanceRunConfig = kwargs["config"]
   277	        task_index = int(kwargs.get("task_index") or 0)
   278	        extracted = await _extract_spreadsheet_skills_from_detail(
   279	            detail,
   280	            store=store,
   281	            config=config,
   282	            task_index=task_index,
   283	        )
   284	        extraction_reports: List[Dict[str, Any]] = []
   285	        for artifact in extracted:
   286	            store.add_pending(artifact)
   287	            extraction_reports.append(
   288	                {
   289	                    "skill_name": artifact.name,
   290	                    "status": artifact.status,
   291	                    "description": artifact.description,
   292	                    "source_task_ids": artifact.metadata.get("source_task_ids") or [],
   293	                }
   294	            )
   295	        async def refine_skill(**hook_kwargs: Any) -> Dict[str, Any]:
   296	            artifact = hook_kwargs["artifact"]
   297	            stage = str(hook_kwargs.get("stage") or "")
   298	            if stage == "post_bundle_failure" and hook_kwargs.get("failed_bundle_result") is not None:
   299	                raw_result = hook_kwargs["failed_bundle_result"]
   300	                test_result = raw_result if isinstance(raw_result, SkillTestResult) else _spreadsheet_test_result_from_dict(raw_result)
   301	                decision = await _refine_spreadsheet_skill_from_bundle(
   302	                    artifact=artifact,
   303	                    test_result=test_result,
   304	                    credit_context=hook_kwargs.get("credit_events") or [],
   305	                    store=store,
   306	                    config=config,
   307	                )
   308	            else:
   309	                decision = await _refine_spreadsheet_skill_from_credit(
   310	                    artifact=artifact,
   311	                    credit_context=hook_kwargs.get("credit_events") or [],
   312	                    detail=detail,
   313	                    store=store,
   314	                    config=config,
   315	                )
   316	            if decision.get("updated_artifact"):
   317	                store.add(decision["updated_artifact"])
   318	            return decision
   320	        async def run_bundle_test(**hook_kwargs: Any) -> Dict[str, Any]:
   321	            artifact = hook_kwargs["artifact"]
   322	            if not artifact.bundle.all_cases():
   323	                return {
   324	                    "skill_name": artifact.name,
   325	                    "passed": True,
   326	                    "aggregate": {"passed": True, "n_cases": 0, "reason": "no_bundle_cases"},
   327	                }
   328	            result = await _compat_module()._execute_spreadsheet_bundle_tests(
   329	                artifact=artifact,
   330	                config=config,
   331	            )
   332	            store.add_test_result(result)
   333	            return result.as_dict()
   335	        report = await run_generic_micro_maintenance(
   336	            detail=detail,
   337	            credit_events=credit_events,
   338	            credit_bundle_cases=credit_bundle_cases,
   339	            store=store,
   340	            config=config,
   341	            hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
   342	            round_index=int(kwargs.get("round_index") or 0),
   343	            task_index=task_index,
   344	        )
   345	        report["refine_decisions"] = [
   346	            {k: v for k, v in item.items() if k != "updated_artifact"}
   347	            for item in report.get("refine_decisions", [])
   348	        ]
   349	        report["extraction_reports"] = extraction_reports
   350	        report["reason"] = "spreadsheet_micro_maintenance"
   351	        report["trace_projection"] = _spreadsheet_trace_projection(detail)
   352	        return report
```

Lines 693-736 normalize Spreadsheet credit payload through the common `normalize_credit_events`.

```python
   693	def _normalize_spreadsheet_credit_events(
   694	    payload: Dict[str, Any],
   695	    *,
   696	    detail: Dict[str, Any],
   697	    candidate_artifacts: Sequence[SkillArtifact],
   698	    projection: Dict[str, Any],
   699	) -> List[Dict[str, Any]]:
   700	    known = {artifact.name for artifact in candidate_artifacts}
   701	    raw_events: List[Dict[str, Any]] = []
   702	    for raw in payload.get("skill_judgments") or []:
   703	        row = dict(raw or {})
   704	        skill_name = str(row.get("skill_name") or "").strip()
   705	        if skill_name not in known:
   706	            continue
   707	        judgment = str(row.get("judgment") or "uncertain").strip().lower()
   708	        if judgment not in {"helpful", "harmful", "neutral", "uncertain"}:
   709	            judgment = "uncertain"
   710	        suggestions = []
   711	        for item in row.get("bundle_case_suggestions") or []:
   712	            suggestion = dict(item or {})
   713	            suggestion.setdefault("skill_name", skill_name)
   714	            suggestions.append(_normalize_spreadsheet_bundle_suggestion(suggestion, detail=detail))
   715	        raw_events.append(
   716	            {
   717	                "benchmark": "spreadsheet",
   718	                "task_id": detail.get("task_id"),
   719	                "skill_name": skill_name,
   720	                "judgment": judgment,
   721	                "effect_type": str(row.get("effect_type") or "unknown").strip().lower() or "unknown",
   722	                "confidence": max(0.0, min(1.0, float(row.get("confidence") or 0.0))),
   723	                "reason": str(row.get("reason") or ""),
   724	                "maintenance_actions": [
   725	                    dict(item or {}) for item in (row.get("maintenance_actions") or []) if isinstance(item, dict)
   726	                ],
   727	                "refine_required": bool(row.get("refine_required")),
   728	                "filter_candidate": bool(row.get("filter_candidate")),
   729	                "evidence_strength": str(row.get("evidence_strength") or "weak").strip().lower() or "weak",
   730	                "attribution_scope": str(row.get("attribution_scope") or "none").strip().lower() or "none",
   731	                "bundle_case_suggestions": suggestions,
   732	                "evidence": copy.deepcopy(dict(row.get("evidence") or {})),
   733	                "projection": copy.deepcopy(projection),
   734	            }
   735	        )
   736	    return normalize_credit_events(raw_events, task_id=str(detail.get("task_id") or ""), benchmark="spreadsheet")
```

Lines 924-939 preserve the old `_spreadsheet_micro_targets` helper as a wrapper around common `micro_target_names`.

```python
   924	def _spreadsheet_micro_targets(
   925	    *,
   926	    credit_events: Sequence[Dict[str, Any]],
   927	    credit_bundle_cases: Sequence[Dict[str, Any]],
   928	    extracted: Sequence[SkillArtifact],
   929	) -> List[str]:
   930	    explicit = [
   931	        str(event.get("skill_name") or "")
   932	        for event in credit_events
   933	        if event.get("refine_required") or event.get("filter_candidate")
   934	    ]
   935	    return micro_target_names(
   936	        relevant_skill_names=[artifact.name for artifact in extracted] + explicit,
   937	        credit_events=credit_events,
   938	        credit_bundle_cases=credit_bundle_cases,
   939	    )
```

Lines 991-1107 keep refiner and bundle replay behavior compatible with facade monkeypatching.

```python
   991	async def _run_spreadsheet_refiner(
   992	    *,
   993	    artifact: SkillArtifact,
   994	    test_result: Dict[str, Any],
   995	    credit_context: Sequence[Dict[str, Any]],
   996	    store: ArtifactStore,
   997	    config: MaintenanceRunConfig,
   998	    phase: str,
   999	) -> Dict[str, Any]:
  1000	    try:
  1001	        payload = await _compat_refine_skill_artifact_llm(
  1002	            artifact,
  1003	            test_result=test_result,
  1004	            credit_context=list(credit_context),
  1005	            refinement_history=artifact.history[-3:],
  1006	            dependency_summaries=summarize_dependency_context(store.all()),
  1007	            llm_config=config.llm_config,
  1008	            model_name=config.model_name,
  1009	            audit_context={"phase": phase, "benchmark": "spreadsheet"},
  1010	        )
```

```python
  1071	async def _execute_spreadsheet_bundle_tests(
  1072	    *,
  1073	    artifact: SkillArtifact,
  1074	    config: MaintenanceRunConfig,
  1075	) -> SkillTestResult:
  1076	    case_runs: List[SkillTestCaseRun] = []
  1077	    for case in artifact.bundle.all_cases():
  1078	        task_fragment = dict((case.context or {}).get("task_fragment") or {})
  1079	        task = BenchmarkTask(
  1080	            benchmark="spreadsheet",
  1081	            task_id=str(task_fragment.get("task_id") or case.case_id),
  1082	            question=task_fragment.get("question") or case.prompt,
  1083	            expected=copy.deepcopy(dict(task_fragment.get("expected") or {})),
  1084	            input_artifacts=copy.deepcopy(dict(task_fragment.get("input_artifacts") or {})),
  1085	            metadata=copy.deepcopy(dict(task_fragment.get("metadata") or {})),
  1086	        )
  1101	        result = await _compat_module().run_spreadsheet_task(
  1102	            task,
  1103	            llm_config=config.llm_config,
  1104	            model_name=config.model_name,
  1105	            artifact_store=ArtifactStore([copy.deepcopy(artifact)]),
  1106	            top_k_skills=1,
  1107	        )
```

#### Updated `academic/benchmarks/tests/test_spreadsheet_evolution.py`

Lines 25-30 add a facade compatibility test.

```python
    25	def test_spreadsheet_adapter_facade_points_to_maintenance_adapter() -> None:
    26	    from academic.benchmarks.spreadsheet.maintenance.adapter import SpreadsheetMaintenanceAdapter as MaintenanceAdapter
    28	    assert SpreadsheetMaintenanceAdapter is MaintenanceAdapter
    29	    assert callable(_execute_spreadsheet_bundle_tests)
    30	    assert callable(run_spreadsheet_task)
```

### Tests

Passed target tests.

```text
$ pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py
................                                                         [100%]
16 passed, 10 warnings in 1.80s
```

```text
$ pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py academic/benchmarks/tests/test_generic_evolution.py academic/benchmarks/tests/test_skill_injector_budget.py academic/benchmarks/tests/test_common_maintenance_core.py
......................................                                   [100%]
38 passed, 10 warnings in 2.01s
```

```text
$ python -m py_compile academic/benchmarks/spreadsheet/adapter.py academic/benchmarks/spreadsheet/maintenance/adapter.py academic/benchmarks/spreadsheet/maintenance/__init__.py academic/benchmarks/tests/test_spreadsheet_evolution.py
```

One broader compatibility command was also run:

```text
$ pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py academic/benchmarks/tests/test_generic_evolution.py academic/benchmarks/tests/test_skill_injector_budget.py academic/benchmarks/tests/bfcl/test_benchmark_adapters.py
76 passed, 1 failed
```

The single failure was unrelated to this refactor:

```text
FAILED academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_historical_bfcl_train_details_produce_nonempty_maintenance_assets
AssertionError: Missing historical BFCL fixture:
/home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_official_tracecheck_evolve_3x3_partial_train.json
```

## Chapter 3: BFCL 低风险接入公共层

### Plan

- BFCL bundle trim 接入公共 bundle policy/wrapper。
- BFCL credit event evidence 写入接入公共 credit 工具。
- BFCL micro target 选择接入公共 helper。
- 保留 BFCL official evaluator、tool replay、overlap refactor prompt。
- 保持 resume/checkpoint/output schema 兼容。

### Implementation Log

Completed.

#### Updated `academic/benchmarks/bfcl/related/experiment.py`

Lines 26-37 import common credit and micro helpers.

```python
    26	from academic.benchmarks.core.artifacts import ArtifactStore
    27	from academic.benchmarks.core.credit_events import (
    28	    apply_credit_evidence,
    29	    is_actionable_helpful_credit,
    30	    is_strong_harmful_credit,
    31	    normalize_credit_events,
    32	)
    33	from academic.benchmarks.core.credit_scope import (
    34	    skill_exposure_from_mappings,
    35	    unique_skill_names,
    36	)
    37	from academic.benchmarks.core.micro_maintenance import micro_target_names
```

Lines 381-409 keep BFCL's existing public helper names but delegate strong target ordering to common `micro_target_names`.

```python
   381	def _strong_credit_targets(events: Sequence[Dict[str, Any]]) -> List[str]:
   382	    return micro_target_names(
   383	        credit_events=[
   384	            event for event in events
   385	            if is_strong_harmful_credit(event, confidence_threshold=0.75)
   386	            or (
   387	                is_actionable_helpful_credit(event)
   388	                and (float(event.get("confidence") or 0.0) >= 0.75 or bool(event.get("used")) or bool((event.get("evidence") or {}).get("used")))
   389	            )
   390	        ],
   391	        credit_bundle_cases=[],
   392	    )
   395	def _micro_write_target_names(
   396	    *,
   397	    task_credit_events: Sequence[Dict[str, Any]],
   398	    credit_bundle_cases: Sequence[Dict[str, Any]],
   399	    relevant_skill_names: Sequence[str],
   400	) -> List[str]:
   401	    relevant = {str(name or "").strip() for name in relevant_skill_names if str(name or "").strip()}
   402	    strong_targets = _strong_credit_targets(task_credit_events)
   403	    if relevant:
   404	        strong_targets = [name for name in strong_targets if name in relevant]
   405	    return micro_target_names(
   406	        credit_events=[],
   407	        credit_bundle_cases=credit_bundle_cases,
   408	        relevant_skill_names=strong_targets,
   409	    )
```

Lines 412-496 keep BFCL-specific credit projection fields but normalize final rows and evidence writes through common credit helpers.

```python
   412	def _credit_event_records(
   413	    *,
   414	    detail: Dict[str, Any],
   415	    credit_payload: Dict[str, Any],
   416	    round_index: int,
   417	    task_index: int,
   418	) -> List[Dict[str, Any]]:
   419	    run = _first_run(detail)
   420	    metrics = dict(run.get("metrics") or {})
   421	    mentioned = set(_mentioned_skill_names(detail))
   422	    task_id = str(detail.get("task_id") or "")
   423	    task_summary = dict(credit_payload.get("task_summary") or {})
   424	    rows: List[Dict[str, Any]] = []
   425	    for item in list(credit_payload.get("skill_judgments") or []):
   426	        row = dict(item or {})
   427	        skill_name = str(row.get("skill_name") or "").strip()
   428	        if not skill_name:
   429	            continue
   430	        evidence = dict(row.get("evidence") or {})
   431	        maintenance_actions = [
   432	            copy.deepcopy(action)
   433	            for action in list(row.get("maintenance_actions") or [])
   434	            if str((action or {}).get("skill_name") or skill_name).strip() == skill_name
   435	        ]
   436	        bundle_case_suggestions = [
   437	            copy.deepcopy(suggestion)
   438	            for suggestion in list(row.get("bundle_case_suggestions") or [])
   439	            if str((suggestion or {}).get("skill_name") or skill_name).strip() == skill_name
   440	        ]
   441	        for action in maintenance_actions:
   442	            action.setdefault("skill_name", skill_name)
   443	        for suggestion in bundle_case_suggestions:
   444	            suggestion.setdefault("skill_name", skill_name)
   445	        base_event = {
   446	            "task_id": task_id,
   447	            "round_index": round_index,
   448	            "task_index": task_index,
   449	            "skill_name": skill_name,
   450	            "judgment": str(row.get("judgment") or "uncertain").strip().lower() or "uncertain",
   451	            "effect_type": str(row.get("effect_type") or "unknown").strip().lower() or "unknown",
   452	            "confidence": float(row.get("confidence") or 0.0),
   453	            "reason": str(row.get("reason") or ""),
   454	            "maintenance_actions": maintenance_actions,
   455	            "bundle_case_suggestions": bundle_case_suggestions,
   456	            "refine_required": bool(row.get("refine_required")),
   457	            "filter_candidate": bool(row.get("filter_candidate")),
   458	            "evidence_strength": str(row.get("evidence_strength") or "weak").strip().lower() or "weak",
   459	            "attribution_scope": str(row.get("attribution_scope") or "none").strip().lower() or "none",
   460	            "evidence": copy.deepcopy(evidence),
   461	            "mentioned_in_trace": skill_name in mentioned,
   462	            "retrieved": bool(evidence.get("retrieved", skill_name in set(metrics.get("retrieved_skills") or []))),
   463	            "injected": bool(
   464	                evidence.get(
   465	                    "injected",
   466	                    skill_name in set(metrics.get("prompt_injected_skills") or [])
   467	                    or skill_name in set(metrics.get("tool_injected_skills") or []),
   468	                )
   469	            ),
   470	            "used": bool(
   471	                evidence.get(
   472	                    "used",
   473	                    skill_name in set(metrics.get("used_skills") or [])
   474	                    or skill_name in set(metrics.get("called_skill_tools") or []),
   475	                )
   476	            ),
   477	            "official_valid": metrics.get("official_valid", task_summary.get("official_valid")),
   478	            "score": run.get("score", task_summary.get("score")),
   479	            "n_model_steps": metrics.get("n_model_steps", task_summary.get("n_model_steps")),
   480	            "total_tokens": metrics.get("total_tokens", task_summary.get("total_tokens")),
   481	        }
   482	        rows.append(normalize_credit_events([base_event], task_id=task_id, benchmark="bfcl_v3")[0])
   483	    return rows
   486	def _apply_credit_case_evidence(
   487	    *,
   488	    store: ArtifactStore,
   489	    credit_events: Sequence[Dict[str, Any]],
   490	) -> None:
   491	    touched_names = {
   492	        str(event.get("skill_name") or "").strip()
   493	        for event in credit_events
   494	        if str(event.get("skill_name") or "").strip()
   495	    }
   496	    for name in touched_names:
   497	        artifact = store.get(name)
   498	        if artifact is None:
   499	            continue
   500	        artifact.evidence.helpful_cases = []
   501	        artifact.evidence.harmful_cases = []
   502	        artifact.evidence.repeated_evidence = []
   503	    apply_credit_evidence(
   504	        store=store,
   505	        credit_events=normalize_credit_events(credit_events, benchmark="bfcl_v3"),
   506	        limit_per_skill=_CREDIT_EVIDENCE_CASE_LIMIT,
   507	    )
```

#### Updated `academic/benchmarks/bfcl/maintenance/adapter.py`

Lines 17-18 import the common bundle budget helper.

```python
    17	from academic.benchmarks.core.artifacts import ArtifactStore
    18	from academic.benchmarks.core.bundle_policy import trim_bundle_cases_to_budget
```

Lines 3055-3067 replace BFCL's local trimming implementation with common bundle budgeting while preserving BFCL's version bump behavior.

```python
  3055	def trim_bundle_cases(
  3056	    artifact: SkillArtifact,
  3057	    *,
  3058	    per_polarity_limit: int | None = None,
  3059	) -> bool:
  3060	    changed = trim_bundle_cases_to_budget(
  3061	        artifact,
  3062	        per_polarity_limit=per_polarity_limit or _bundle_case_limit_per_polarity(),
  3063	        total_limit=_bundle_max_total_cases(),
  3064	    )
  3065	    if changed:
  3066	        artifact.bundle.bundle_version = max(int(artifact.bundle.bundle_version or 1), 1) + 1
  3067	    return changed
```

#### Updated Tests

`academic/benchmarks/tests/bfcl_related/test_experiment.py` lines 1885-1939 add coverage for common credit normalization/evidence limits and common micro target ordering.

```python
  1885	    rows = _credit_event_records(
  1886	        detail=detail,
  1887	        credit_payload={
  1888	            "task_summary": {"official_valid": True, "score": 1.0},
  1889	            "skill_judgments": [
  1890	                {
  1891	                    "skill_name": "skill_a",
  1892	                    "judgment": "positive",
  1893	                    "effect_type": "schema_help",
  1894	                    "confidence": "0.8",
  1895	                    "reason": "helped schema",
  1896	                    "bundle_case_suggestions": [{"polarity": "positive"}],
  1897	                }
  1898	            ],
  1899	        },
  1900	        round_index=1,
  1901	        task_index=2,
  1902	    )
  1903	    store = ArtifactStore([SkillArtifact(name="skill_a", kind="atomic_tool_rule_card", description="a", body="a")])
  1904	    many_rows = [
  1905	        {**rows[0], "task_id": f"task_{idx}", "judgment": "helpful", "confidence": 0.8}
  1906	        for idx in range(30)
  1907	    ]
  1909	    _apply_credit_case_evidence(store=store, credit_events=many_rows)
  1911	    assert rows[0]["judgment"] == "helpful"
  1912	    assert rows[0]["benchmark"] == "bfcl_v3"
  1913	    assert rows[0]["source"] == "credit_assigner"
  1914	    assert rows[0]["event_index"] == 0
  1915	    assert store.get("skill_a").evidence.helpful_cases[0]["task_id"] == "task_18"
  1916	    assert store.get("skill_a").evidence.helpful_cases[-1]["task_id"] == "task_29"
  1917	    assert len(store.get("skill_a").evidence.helpful_cases) == 12
```

```python
  1920	def test_bfcl_micro_write_targets_use_common_target_ordering() -> None:
  1921	    targets = _micro_write_target_names(
  1922	        task_credit_events=[
  1923	            {"skill_name": "skill_weak", "judgment": "harmful", "confidence": 0.2},
  1924	            {"skill_name": "skill_strong", "judgment": "harmful", "confidence": 0.8},
  1925	            {
  1926	                "skill_name": "skill_helpful",
  1927	                "judgment": "helpful",
  1928	                "confidence": 0.9,
  1929	                "helpful_reasons": ["schema_help"],
  1930	            },
  1931	        ],
  1932	        credit_bundle_cases=[
  1933	            {"skill_name": "skill_bundle", "case_id": "case_1"},
  1934	            {"skill_name": "skill_strong", "case_id": "case_2"},
  1935	        ],
  1936	        relevant_skill_names=["skill_strong", "skill_helpful"],
  1937	    )
  1939	    assert targets == ["skill_strong", "skill_helpful", "skill_bundle"]
```

`academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py` lines 85-96 add coverage that BFCL's wrapper still bumps bundle version after using the common budget helper.

```python
    85	def test_bfcl_trim_bundle_cases_uses_common_budget_and_bumps_version() -> None:
    86	    artifact = _sample_artifact()
    87	    artifact.bundle.bundle_version = 3
    89	    changed = trim_bundle_cases(artifact, per_polarity_limit=2)
    91	    assert changed is True
    92	    assert artifact.bundle.bundle_version == 4
    93	    assert len(artifact.bundle.positive_cases) <= 2
    94	    assert len(artifact.bundle.negative_cases) <= 2
    95	    assert artifact.bundle.fixtures["bundle_trimmed"] is True
    96	    assert artifact.bundle.fixtures["bundle_case_budget"]["per_polarity_limit"] == 2
```

### Tests

Passed.

```text
$ python -m py_compile academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/maintenance/adapter.py academic/benchmarks/tests/bfcl_related/test_experiment.py academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py
```

```text
$ pytest -q academic/benchmarks/tests/bfcl_related/test_experiment.py academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py academic/benchmarks/tests/bfcl_related/test_paper_new_algorithm_contract.py academic/benchmarks/tests/maintenance/test_bundle_agent.py
........................................................................ [ 86%]
...........                                                              [100%]
83 passed, 10 warnings in 25.75s
```

```text
$ pytest -q academic/benchmarks/tests/test_common_maintenance_core.py academic/benchmarks/tests/test_spreadsheet_evolution.py academic/benchmarks/tests/test_generic_evolution.py
..................................                                       [100%]
34 passed, 10 warnings in 1.98s
```

## Chapter 3.1: 两个 Bench 的重构契约测例

### Plan

- 新增一个跨 benchmark 的重构契约测试文件，专门测试 BFCL 和 Spreadsheet 是否真的接入公共层，而不是只靠已有端到端测试间接覆盖。
- Spreadsheet 侧重点：
  - facade import 和 monkeypatch 兼容路径仍然可用；
  - `SpreadsheetMaintenanceAdapter` 内部调用 facade 上的 `run_spreadsheet_task` / `_ask_json` / `refine_skill_artifact_llm` / `_execute_spreadsheet_bundle_tests`；
  - credit 生成 benchmark-native bundle case 后，micro maintenance 按公共层顺序先 credit refine，再 bundle test；
  - weak/uncertain credit 不触发 micro refine/test。
- BFCL 侧重点：
  - `_credit_event_records` 使用公共 `normalize_credit_events`，但保留 BFCL task metrics；
  - `_apply_credit_case_evidence` 保持累计 snapshot 语义，不重复追加同一批 evidence；
  - `_micro_write_target_names` 使用公共 target ordering，同时保留 BFCL 的 strong credit 阈值；
  - `trim_bundle_cases` 调用公共预算 helper，同时继续 bump BFCL bundle version。

### Implementation Log

新增文件：`academic/benchmarks/tests/test_benchmark_refactor_contracts.py`。

Lines 17-98 定义跨测试共用的 mock skill、Spreadsheet task 和 detail。这里不调用真实 LLM、不读真实 xlsx，只构造完整的 `BenchmarkTask` / `BenchmarkResult` 形状，保证测试针对重构契约本身。

```python
    17	def _skill(name: str, *, benchmark: str = "generic", status: str = "active") -> SkillArtifact:
    18	    return SkillArtifact(
    19	        name=name,
    20	        kind="workflow_guardrail_card",
    21	        description=f"{name} description",
    22	        body=f"{name} body",
    23	        interface=SkillInterface(
    24	            summary=f"{name} summary",
    25	            invocation_contract={"injection_type": "workflow"},
    26	        ),
    27	        metadata={"benchmark": benchmark, "domains": [benchmark]},
    28	        status=status,
    29	    )
```

```python
    54	def _spreadsheet_detail(
    55	    *,
    56	    task: BenchmarkTask | None = None,
    57	    retrieved: List[str] | None = None,
    58	    injected: List[str] | None = None,
    59	    called: List[str] | None = None,
    60	    success: bool = False,
    61	) -> Dict[str, Any]:
    62	    task = task or _spreadsheet_task()
    66	    result = BenchmarkResult(
    67	        benchmark="spreadsheet",
    68	        task_id=task.task_id,
    69	        success=success,
    70	        score=1.0 if success else 0.0,
    71	        metrics={
    77	            "retrieved_skills": retrieved,
    78	            "prompt_injected_skills": injected,
    79	            "called_skill_functions": called,
    80	            "total_tokens": 100,
    81	        },
    82	        trace={
    83	            "retrieved_skills": retrieved,
    84	            "prompt_injected_skills": injected,
    85	            "called_skill_functions": called,
    86	            "code": "import openpyxl\n# mocked trace",
    87	            "stderr": "",
    88	            "stdout": "",
    89	        },
    90	    )
```

Lines 101-169 测 Spreadsheet facade 兼容性。这个测例把 facade 上的 `run_spreadsheet_task` 和 `_ask_json` monkeypatch 掉，然后从 `SpreadsheetMaintenanceAdapter` 调用，证明 maintenance 子模块没有绕开 facade，旧测试和旧外部入口仍可接管行为。

```python
   101	async def test_spreadsheet_facade_monkeypatches_still_route_into_maintenance_adapter(monkeypatch) -> None:
   107	    async def fake_run_spreadsheet_task(*args: Any, **kwargs: Any) -> BenchmarkResult:
   108	        calls.append({"hook": "run_task", "task_id": args[0].task_id, "top_k": kwargs.get("top_k_skills")})
   109	        return BenchmarkResult(
   110	            benchmark="spreadsheet",
   111	            task_id=args[0].task_id,
   112	            success=True,
   113	            score=1.0,
   114	            metrics={"hooked": True},
   115	        )
```

```python
   143	    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.run_spreadsheet_task", fake_run_spreadsheet_task)
   144	    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._ask_json", fake_ask_json)
   146	    adapter = SpreadsheetMaintenanceAdapter()
   147	    run_result = await adapter.run_task(
   155	    credit = await adapter.assign_credit(
   163	    assert run_result.metrics["hooked"] is True
   164	    assert [call["hook"] for call in calls] == ["run_task", "ask_json"]
   166	    assert calls[1]["role"] == "spreadsheet_credit_assigner"
   167	    assert credit[0]["benchmark"] == "spreadsheet"
   168	    assert credit[0]["judgment"] == "harmful"
   169	    assert store.get("sheet_double").evidence.harmful_cases[0]["task_id"] == task.task_id
```

Lines 172-268 测 Spreadsheet 的公共 bundle/micro 路径。这个测例直接喂 strong harmful credit，断言：

- `apply_credit_bundle_cases` 生成的是 Spreadsheet-native replay case；
- case 复用官方 task snapshot 中的 question/expected/input_artifacts；
- bundle version 被 bump；
- `run_generic_micro_maintenance` 的顺序是先 credit refine，再 bundle test。

```python
   172	async def test_spreadsheet_credit_bundle_and_micro_use_common_flow_with_benchmark_cases(monkeypatch) -> None:
   178	    credit_events = [
   179	        {
   180	            "benchmark": "spreadsheet",
   181	            "task_id": detail["task_id"],
   182	            "skill_name": "sheet_double",
   183	            "judgment": "harmful",
   184	            "effect_type": "correctness_loss",
   185	            "confidence": 0.88,
   189	            "bundle_case_suggestions": [
   190	                {
   191	                    "skill_name": "sheet_double",
   192	                    "polarity": "negative",
   193	                    "reason": "Keep the official SpreadsheetBench answer range as a regression case.",
   194	                    "source_task_id": detail["task_id"],
   195	                    "task_fragment_policy": "reuse_official_fragment",
   196	                }
   197	            ],
   198	        }
   199	    ]
```

```python
   201	    async def fake_refine_skill_artifact_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
   202	        order.append("refine")
   203	        return {
   204	            "decision": {
   205	                "action": "keep",
   206	                "reason": "Mock credit refine inspected the focused SpreadsheetBench case.",
   207	            },
   208	            "artifact": {},
   209	            "bundle": {},
   210	        }
   212	    async def fake_bundle_tests(**kwargs: Any) -> SkillTestResult:
   213	        order.append("bundle_test")
```

```python
   257	    assert case.prompt == "Double the value in Sheet1!A1 into Sheet1!B1."
   258	    assert case.expected == {
   259	        "answer_sheet": "Sheet1",
   260	        "answer_position": "B1",
   261	        "verifier": "spreadsheet_golden_range",
   262	    }
   263	    assert case.context["task_fragment"]["input_artifacts"]["input_xlsx"] == "/tmp/input.xlsx"
   264	    assert case.context["focus_turns"] == [0]
   265	    assert artifact.bundle.bundle_version == 2
   266	    assert order == ["refine", "bundle_test"]
   267	    assert report["maintenance_targets"] == ["sheet_double"]
```

Lines 271-314 测 weak credit 不触发 micro。这个测例避免未来把所有 retrieved skill 都粗暴 replay/refine，保护“只维护强信号或新增 bundle case 目标”的算法约束。

```python
   271	async def test_spreadsheet_micro_skips_when_credit_is_not_actionable(monkeypatch) -> None:
   295	    report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
   297	        credit_events=[
   298	            {
   299	                "skill_name": "sheet_double",
   300	                "judgment": "uncertain",
   301	                "confidence": 0.2,
   302	                "reason": "Weak evidence only.",
   303	            }
   304	        ],
   305	        credit_bundle_cases=[],
   312	    assert report["maintenance_targets"] == []
   313	    assert report["reason"] == "spreadsheet_micro_maintenance"
   314	    assert calls == []
```

Lines 317-386 测 BFCL credit event normalization。关键点是 `positive -> helpful`、`benchmark=bfcl_v3`、`source=credit_assigner`、metric 字段保留、非本 skill 的 maintenance action / bundle suggestion 被过滤。

```python
   317	def test_bfcl_credit_records_normalize_and_preserve_task_metrics() -> None:
   320	    detail = {
   321	        "task_id": "bfcl_contract_1",
   324	                "score": 0.0,
   325	                "metrics": {
   326	                    "official_valid": False,
   327	                    "n_model_steps": 2,
   328	                    "total_tokens": 321,
   329	                    "retrieved_skills": ["travel_schema_guard"],
   330	                    "prompt_injected_skills": ["travel_schema_guard"],
   331	                    "used_skills": [],
```

```python
   371	    assert len(events) == 1
   373	    assert event["benchmark"] == "bfcl_v3"
   374	    assert event["source"] == "credit_assigner"
   375	    assert event["judgment"] == "helpful"
   376	    assert event["confidence"] == 0.83
   379	    assert event["official_valid"] is False
   380	    assert event["n_model_steps"] == 2
   381	    assert event["total_tokens"] == 321
   382	    assert event["retrieved"] is True
   383	    assert event["injected"] is True
   384	    assert event["used"] is False
   385	    assert event["maintenance_actions"] == [{"skill_name": "travel_schema_guard", "action": "keep"}]
```

Lines 389-410 测 BFCL evidence snapshot 语义。`_apply_credit_case_evidence` 会先清空 touched skill 当前 evidence bucket，再用累计 events 重放，因此重复调用同一批累计 events 不会重复追加，且只保留最近 12 条。

```python
   389	def test_bfcl_credit_evidence_is_cumulative_snapshot_without_duplicate_replay() -> None:
   392	    store = ArtifactStore([_skill("bfcl_skill", benchmark="bfcl_v3")])
   393	    cumulative_events = [
   394	        {
   395	            "benchmark": "bfcl_v3",
   396	            "task_id": f"task_{idx}",
   397	            "skill_name": "bfcl_skill",
   398	            "judgment": "harmful",
   399	            "confidence": 0.9,
   400	            "reason": f"bad schema {idx}",
   401	        }
   402	        for idx in range(14)
   403	    ]
   405	    _apply_credit_case_evidence(store=store, credit_events=cumulative_events)
   406	    _apply_credit_case_evidence(store=store, credit_events=cumulative_events)
   408	    harmful = store.get("bfcl_skill").evidence.harmful_cases
   409	    assert len(harmful) == 12
   410	    assert [row["task_id"] for row in harmful] == [f"task_{idx}" for idx in range(2, 14)]
```

Lines 413-437 测 BFCL micro target ordering。强 harmful 阈值仍按 BFCL wrapper 的 `0.75`，但最终 target 合并顺序来自公共 `micro_target_names`：credit targets first, then bundle-case-only targets。

```python
   413	def test_bfcl_micro_targets_use_common_ordering_and_relevance_filter() -> None:
   416	    events = [
   417	        {"skill_name": "weak_harmful", "judgment": "harmful", "confidence": 0.7},
   418	        {"skill_name": "strong_harmful", "judgment": "harmful", "confidence": 0.9},
   419	        {
   420	            "skill_name": "helpful_schema",
   421	            "judgment": "helpful",
   422	            "confidence": 0.8,
   423	            "reason_codes": ["schema_help"],
   424	        },
   425	        {"skill_name": "uncertain", "judgment": "uncertain", "confidence": 1.0},
   426	    ]
   432	    assert _strong_credit_targets(events) == ["strong_harmful", "helpful_schema"]
   433	    assert _micro_write_target_names(
   437	    ) == ["strong_harmful", "helpful_schema", "case_only"]
```

Lines 440-473 测 BFCL bundle trim 仍维持 benchmark-specific version bump。公共 helper 负责预算和 metadata，BFCL wrapper 负责 bundle version 从 4 bump 到 5。

```python
   440	def test_bfcl_trim_bundle_cases_delegates_budget_and_bumps_version(monkeypatch) -> None:
   443	    artifact = _skill("bfcl_budget", benchmark="bfcl_v3")
   444	    artifact.bundle.bundle_version = 4
   445	    for idx in range(4):
   446	        artifact.bundle.positive_cases.append(
   447	            SkillBundleCase(
   448	                case_id=f"pos_{idx}",
   449	                source="credit_assigner_positive" if idx == 3 else "manual",
   450	                prompt=f"positive {idx}",
   451	                context={"confidence": 0.9 if idx == 3 else 0.1},
   452	                polarity="positive",
   453	            )
   454	        )
```

```python
   465	    monkeypatch.setenv("BFCL_BUNDLE_MAX_TOTAL_CASES", "3")
   466	    changed = trim_bundle_cases(artifact, per_polarity_limit=2)
   468	    assert changed is True
   469	    assert artifact.bundle.bundle_version == 5
   470	    assert len(artifact.bundle.all_cases()) == 3
   471	    assert "pos_3" in {case.case_id for case in artifact.bundle.all_cases()}
   472	    assert artifact.bundle.fixtures["bundle_trimmed"] is True
   473	    assert artifact.bundle.fixtures["bundle_case_budget"] == {"per_polarity_limit": 2, "total_limit": 3}
```

### Tests

Passed.

```text
$ pytest -q academic/benchmarks/tests/test_benchmark_refactor_contracts.py
.......                                                                  [100%]
7 passed, 10 warnings in 1.82s
```

```text
$ pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py academic/benchmarks/tests/bfcl_related/test_experiment.py academic/benchmarks/tests/maintenance/test_runtime_optimization_scenarios.py academic/benchmarks/tests/test_common_maintenance_core.py academic/benchmarks/tests/test_benchmark_refactor_contracts.py
........................................................................ [ 80%]
..................                                                       [100%]
90 passed, 10 warnings in 27.34s
```

```text
$ python -m py_compile academic/benchmarks/tests/test_benchmark_refactor_contracts.py academic/benchmarks/spreadsheet/adapter.py academic/benchmarks/spreadsheet/maintenance/adapter.py academic/benchmarks/bfcl/related/experiment.py academic/benchmarks/bfcl/maintenance/adapter.py academic/benchmarks/core/credit_events.py academic/benchmarks/core/bundle_cases.py academic/benchmarks/core/micro_maintenance.py
```

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
