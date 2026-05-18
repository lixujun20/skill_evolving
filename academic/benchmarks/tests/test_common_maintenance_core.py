import asyncio
from typing import Any, Dict, List

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.bundle_cases import (
    apply_credit_bundle_suggestions,
    bundle_case_rows_by_skill,
    normalize_bundle_case_suggestions,
)
from academic.benchmarks.core.credit_events import (
    apply_credit_evidence,
    credit_target_names,
    is_actionable_helpful_credit,
    is_strong_harmful_credit,
    normalize_credit_events,
    normalize_evidence_strength,
    normalize_judgment,
    summarize_credit_events,
)
from academic.benchmarks.core.macro_maintenance import MacroMaintenanceHooks, run_generic_macro_maintenance, store_summary
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig
from academic.benchmarks.core.micro_maintenance import MicroMaintenanceHooks, micro_target_names, run_generic_micro_maintenance
from academic.benchmarks.core.relation_graph import (
    RelationEdge,
    RelationGraphState,
    RelationNode,
    pending_skill_relation_node,
    skill_relation_node,
    trace_relation_node,
)
from academic.benchmarks.core.types import SkillArtifact, SkillBundleCase


def _artifact(name: str) -> SkillArtifact:
    return SkillArtifact(
        name=name,
        kind="workflow_card",
        description=f"{name} description",
        body=f"{name} body",
    )


def test_normalize_credit_events_and_apply_evidence() -> None:
    store = ArtifactStore([_artifact("skill_a"), _artifact("skill_b")])
    events = normalize_credit_events(
        [
            {
                "skill_name": "skill_a",
                "judgment": "positive",
                "confidence": "0.82",
                "helpful_reasons": ["token_saving"],
                "prompt_injected": True,
            },
            {
                "name": "skill_b",
                "polarity": "negative",
                "confidence": 0.4,
                "used": True,
            },
        ],
        task_id="task_1",
        benchmark="fake",
    )

    assert events[0]["judgment"] == "helpful"
    assert events[0]["evidence_strength"] == "strong"
    assert events[1]["judgment"] == "harmful"
    assert credit_target_names(events) == ["skill_a", "skill_b"]

    applied = apply_credit_evidence(store=store, credit_events=events)

    assert [row["applied"] for row in applied] == [True, True]
    assert store.get("skill_a").evidence.helpful_cases[0]["task_id"] == "task_1"
    assert store.get("skill_b").evidence.harmful_cases[0]["judgment"] == "harmful"


def test_credit_event_helpers_cover_aliases_strengths_targets_and_limits() -> None:
    rows = normalize_credit_events(
        [
            {"name": "missing_name_is_kept", "label": "unknown", "confidence": "bad"},
            {"skill_name": "", "judgment": "helpful"},
            {"skill_name": "skill_h", "judgment": "regression", "confidence": 0.1, "used": True},
            {"skill_name": "skill_p", "judgment": "positive", "confidence": 0.51, "reasons": "schema_help"},
            {"skill_name": "skill_n", "judgment": "neutral", "confidence": 0.99},
        ],
        task_id="task_alias",
        benchmark="bench",
        default_source="unit_source",
    )

    assert [row["skill_name"] for row in rows] == ["missing_name_is_kept", "skill_h", "skill_p", "skill_n"]
    assert rows[0]["judgment"] == "uncertain"
    assert rows[0]["confidence"] == 0.0
    assert rows[0]["source"] == "unit_source"
    assert normalize_judgment("negative") == "harmful"
    assert normalize_judgment("positive") == "helpful"
    assert normalize_judgment("") == "uncertain"
    assert normalize_evidence_strength(None, confidence=0.8, judgment="harmful") == "strong"
    assert normalize_evidence_strength(None, confidence=0.55, judgment="helpful") == "medium"
    assert normalize_evidence_strength(None, confidence=0.2, judgment="helpful") == "weak"
    assert is_strong_harmful_credit(rows[1]) is True
    assert is_actionable_helpful_credit(rows[2]) is True
    assert credit_target_names(rows) == ["skill_h", "skill_p"]

    summary = summarize_credit_events(rows)

    assert summary["total"] == 4
    assert summary["harmful"] == 1
    assert summary["helpful"] == 1
    assert summary["neutral"] == 1
    assert summary["uncertain"] == 1
    assert summary["skills"]["skill_h"]["harmful"] == 1


def test_apply_credit_evidence_handles_missing_skills_and_bucket_limits() -> None:
    store = ArtifactStore([_artifact("skill_a")])
    events = normalize_credit_events(
        [
            {"skill_name": "skill_a", "judgment": "neutral", "confidence": 0.1, "task_id": f"task_{idx}"}
            for idx in range(4)
        ]
        + [{"skill_name": "missing", "judgment": "harmful", "confidence": 0.9}],
        benchmark="fake",
    )

    applied = apply_credit_evidence(store=store, credit_events=events, limit_per_skill=2)

    assert applied[-1] == {"skill_name": "missing", "applied": False, "reason": "missing_skill"}
    repeated = store.get("skill_a").evidence.repeated_evidence
    assert [row["task_id"] for row in repeated] == ["task_2", "task_3"]


def test_apply_credit_bundle_suggestions_uses_env_case_builder() -> None:
    store = ArtifactStore([_artifact("skill_a")])
    events = normalize_credit_events(
        [
            {
                "skill_name": "skill_a",
                "judgment": "harmful",
                "confidence": 0.7,
                "bundle_case_suggestions": [
                    {
                        "polarity": "negative",
                        "reason": "bad arguments",
                        "focus_turn_indices": [1],
                        "expected_contract": {"official_valid": True},
                    }
                ],
            }
        ],
        task_id="task_1",
    )

    def build_case(detail: Dict[str, Any], event: Dict[str, Any], suggestion: Dict[str, Any]) -> SkillBundleCase:
        return SkillBundleCase(
            case_id=f"{suggestion['skill_name']}::{suggestion['source_task_id']}::{suggestion['suggestion_index']}",
            source="credit_assigner_negative",
            prompt=detail["task"]["question"],
            expected={"contract": suggestion["expected_contract"]},
            context={"credit_event": event, "task_fragment": detail["task"]},
            polarity=suggestion["polarity"],
        )

    rows = apply_credit_bundle_suggestions(
        store=store,
        detail={"task_id": "task_1", "task": {"question": "focused prompt"}},
        credit_events=events,
        build_case=build_case,
    )

    assert rows[0]["created"] is True
    assert rows[0]["polarity"] == "negative"
    assert store.get("skill_a").bundle.negative_cases[0].prompt == "focused prompt"


def test_bundle_case_helpers_cover_positive_integration_duplicates_and_failures() -> None:
    store = ArtifactStore([_artifact("skill_a"), _artifact("skill_b")])
    events = normalize_credit_events(
        [
            {
                "skill_name": "skill_a",
                "judgment": "helpful",
                "confidence": 0.8,
                "helpful_reasons": ["workflow_alignment"],
                "bundle_case_suggestions": {"polarity": "positive", "expected_contract": {"score": 1}},
            },
            {
                "skill_name": "skill_b",
                "judgment": "neutral",
                "bundle_case_suggestions": [{"polarity": "integration", "expected_contract": {"valid": True}}],
            },
            {
                "skill_name": "skill_missing",
                "judgment": "harmful",
                "confidence": 1.0,
                "bundle_case_suggestions": [{"polarity": "negative"}],
            },
            {"skill_name": "skill_a", "judgment": "neutral", "bundle_case_suggestions": []},
        ],
        task_id="task_2",
    )
    calls: List[str] = []

    def build_case(detail: Dict[str, Any], event: Dict[str, Any], suggestion: Dict[str, Any]) -> SkillBundleCase | None:
        calls.append(suggestion["skill_name"])
        if detail.get("return_none"):
            return None
        return SkillBundleCase(
            case_id=f"{suggestion['skill_name']}::{suggestion['polarity']}",
            source=f"credit_assigner_{suggestion['polarity']}",
            prompt="fragment",
            expected=suggestion.get("expected_contract") or {},
            context={"credit_event": event},
            polarity=suggestion["polarity"],
        )

    assert normalize_bundle_case_suggestions(events[0])[0]["task_fragment_policy"] == "focused_official_fragment"
    rows = apply_credit_bundle_suggestions(
        store=store,
        detail={"task_id": "task_2"},
        credit_events=events,
        build_case=build_case,
    )
    duplicate_rows = apply_credit_bundle_suggestions(
        store=store,
        detail={"task_id": "task_2"},
        credit_events=[events[0]],
        build_case=build_case,
    )
    none_rows = apply_credit_bundle_suggestions(
        store=store,
        detail={"return_none": True},
        credit_events=[events[1]],
        build_case=build_case,
    )

    assert [row["reason"] for row in rows if not row["created"]] == [
        "missing_skill",
        "credit_not_actionable_for_bundle",
    ]
    assert rows[0]["bucket"] == "positive_cases"
    assert rows[1]["bucket"] == "integration_cases"
    assert duplicate_rows[0]["reason"] == "duplicate_case"
    assert none_rows[0]["reason"] == "case_builder_returned_none"
    assert bundle_case_rows_by_skill(rows)["skill_a"][0]["polarity"] == "positive"
    assert store.get("skill_a").bundle.positive_cases[0].case_id == "skill_a::positive"
    assert store.get("skill_b").bundle.integration_cases[0].case_id == "skill_b::integration"
    assert calls[:2] == ["skill_a", "skill_b"]


def test_bundle_case_budget_is_applied_after_credit_cases() -> None:
    store = ArtifactStore([_artifact("skill_a")])
    events = normalize_credit_events(
        [
            {
                "skill_name": "skill_a",
                "judgment": "harmful",
                "confidence": 0.9,
                "bundle_case_suggestions": [
                    {"polarity": "negative", "expected_contract": {"idx": idx}}
                    for idx in range(4)
                ],
            }
        ],
        task_id="task_budget",
    )

    def build_case(detail: Dict[str, Any], event: Dict[str, Any], suggestion: Dict[str, Any]) -> SkillBundleCase:
        return SkillBundleCase(
            case_id=f"case_{suggestion['suggestion_index']}",
            source="credit_assigner_negative",
            prompt="p",
            expected=suggestion["expected_contract"],
            context={"confidence": suggestion["suggestion_index"] / 10},
            polarity="negative",
        )

    rows = apply_credit_bundle_suggestions(
        store=store,
        detail={},
        credit_events=events,
        build_case=build_case,
    )

    assert len([row for row in rows if row["created"]]) == 4
    assert len(store.get("skill_a").bundle.negative_cases) == 2
    assert store.get("skill_a").bundle.fixtures["bundle_trimmed"] is True


def test_micro_target_names_deduplicates_and_orders_sources() -> None:
    targets = micro_target_names(
        relevant_skill_names=["skill_z", "skill_h", "skill_z"],
        credit_events=normalize_credit_events(
            [
                {"skill_name": "skill_h", "judgment": "harmful", "confidence": 0.8},
                {"skill_name": "skill_p", "judgment": "helpful", "helpful_reasons": ["correctness_gain"]},
            ],
            task_id="task",
        ),
        credit_bundle_cases=[
            {"skill_name": "skill_bundle", "created": True},
            {"skill_name": "skill_h", "created": True},
        ],
    )

    assert targets == ["skill_z", "skill_h", "skill_p", "skill_bundle"]


async def test_generic_micro_refines_before_testing_and_repairs_until_pass() -> None:
    store = ArtifactStore([_artifact("skill_a")])
    order: List[str] = []

    async def refine_skill(**kwargs: Any) -> Dict[str, Any]:
        order.append(f"refine:{kwargs['stage']}:{kwargs['repair_round']}")
        return {"skill_name": kwargs["skill_name"], "stage": kwargs["stage"], "repair_round": kwargs["repair_round"]}

    async def run_bundle_test(**kwargs: Any) -> Dict[str, Any]:
        order.append(f"test:{kwargs['repair_round']}")
        return {"skill_name": kwargs["skill_name"], "passed": kwargs["repair_round"] >= 1}

    report = await run_generic_micro_maintenance(
        detail={"task_id": "task_1"},
        credit_events=normalize_credit_events(
            [{"skill_name": "skill_a", "judgment": "harmful", "confidence": 0.9}],
            task_id="task_1",
        ),
        credit_bundle_cases=[{"skill_name": "skill_a", "created": True, "case_id": "case_1"}],
        store=store,
        config=MaintenanceRunConfig(llm_config="fake", extra={"micro_refine_max_repair_rounds": 2}),
        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
        round_index=0,
        task_index=0,
    )

    assert order == ["refine:credit_pre_refine:0", "test:0", "refine:post_bundle_failure:1", "test:1"]
    assert report["maintenance_targets"] == ["skill_a"]
    assert len(report["maintenance_test_results"]) == 2


async def test_generic_micro_no_targets_and_missing_skill_paths() -> None:
    calls: List[str] = []

    async def refine_skill(**kwargs: Any) -> Dict[str, Any]:
        calls.append("refine")
        return {"skill_name": kwargs["skill_name"]}

    async def run_bundle_test(**kwargs: Any) -> Dict[str, Any]:
        calls.append("test")
        return {"passed": True}

    no_target = await run_generic_micro_maintenance(
        detail={"task_id": "task_none"},
        credit_events=[],
        credit_bundle_cases=[],
        store=ArtifactStore(),
        config=MaintenanceRunConfig(llm_config="fake"),
        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
        round_index=0,
        task_index=0,
    )
    missing = await run_generic_micro_maintenance(
        detail={"task_id": "task_missing"},
        credit_events=[],
        credit_bundle_cases=[],
        store=ArtifactStore(),
        config=MaintenanceRunConfig(llm_config="fake"),
        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
        round_index=0,
        task_index=1,
        relevant_skill_names=["missing_skill"],
    )

    assert no_target["reason"] == "no_micro_targets"
    assert missing["refine_decisions"] == [{"skill_name": "missing_skill", "action": "skip", "reason": "missing_skill"}]
    assert calls == []


async def test_generic_micro_passes_without_repair_and_honors_explicit_repair_limit() -> None:
    store = ArtifactStore([_artifact("skill_a")])
    order: List[str] = []

    async def refine_skill(**kwargs: Any) -> Dict[str, Any]:
        order.append(f"refine:{kwargs['repair_round']}")
        return {"skill_name": kwargs["skill_name"], "repair_round": kwargs["repair_round"]}

    async def run_bundle_test(**kwargs: Any) -> Dict[str, Any]:
        order.append(f"test:{kwargs['repair_round']}")
        return {"skill_name": kwargs["skill_name"], "passed": False}

    report = await run_generic_micro_maintenance(
        detail={"task_id": "task_limit"},
        credit_events=normalize_credit_events(
            [{"skill_name": "skill_a", "judgment": "harmful", "confidence": 1.0}],
            task_id="task_limit",
        ),
        credit_bundle_cases=[],
        store=store,
        config=MaintenanceRunConfig(llm_config="fake", extra={"micro_refine_max_repair_rounds": 9}),
        hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
        round_index=0,
        task_index=0,
        max_repair_rounds=0,
    )

    assert order == ["refine:0", "test:0"]
    assert len(report["maintenance_test_results"]) == 1


async def test_generic_macro_runs_optional_hooks_and_reports_store_summary() -> None:
    store = ArtifactStore([_artifact("skill_a")])

    async def promote_pending(**kwargs: Any) -> Dict[str, Any]:
        return {"promoted_pending_skills": ["skill_a"]}

    async def filter_skills(**kwargs: Any) -> Dict[str, Any]:
        return {"filtered_skills": ["skill_z"]}

    report = await run_generic_macro_maintenance(
        window_details=[{"task_id": "task_1"}],
        all_train_details=[{"task_id": "task_0"}, {"task_id": "task_1"}],
        credit_events=normalize_credit_events([{"skill_name": "skill_a", "judgment": "helpful"}], task_id="task_1"),
        store=store,
        config=MaintenanceRunConfig(llm_config="fake"),
        hooks=MacroMaintenanceHooks(promote_pending=promote_pending, filter_skills=filter_skills),
        round_index=0,
        window_index=1,
    )

    assert report["phase"] == "macro"
    assert report["promoted_pending_skills"] == ["skill_a"]
    assert report["filtered_skills"] == ["skill_z"]
    assert report["store_summary_after"]["n_skills"] == 1


async def test_generic_macro_final_window_no_hooks_and_all_hooks() -> None:
    store = ArtifactStore([_artifact("active"), _artifact("pending"), _artifact("disabled")])
    store.get("pending").status = "pending"
    store.get("disabled").status = "disabled"
    calls: List[str] = []

    no_hooks = await run_generic_macro_maintenance(
        window_details=[{"task_id": "task_final"}],
        all_train_details=[{"task_id": "task_final"}],
        credit_events=[],
        store=store,
        config=MaintenanceRunConfig(llm_config="fake"),
        round_index=1,
        window_index=2,
        final_window=True,
    )

    async def promote_pending(**kwargs: Any) -> Dict[str, Any]:
        calls.append(f"promote:{kwargs['final_window']}")
        return {"promoted": ["pending"]}

    async def refactor_overlap(**kwargs: Any) -> Dict[str, Any]:
        calls.append("refactor")
        return {"attempts": [{"group": "g"}], "refactor_segment_coverage": ["task_final"]}

    async def filter_skills(**kwargs: Any) -> Dict[str, Any]:
        calls.append("filter")
        return {"disabled_skills": ["disabled"]}

    async def update_trl(**kwargs: Any) -> Dict[str, Any]:
        calls.append("trl")
        return {"feedback_events": 1}

    all_hooks = await run_generic_macro_maintenance(
        window_details=[{"task_id": "task_final"}],
        all_train_details=[{"task_id": "task_final"}],
        credit_events=[],
        store=store,
        config=MaintenanceRunConfig(llm_config="fake"),
        hooks=MacroMaintenanceHooks(
            promote_pending=promote_pending,
            refactor_overlap=refactor_overlap,
            filter_skills=filter_skills,
            update_trl=update_trl,
        ),
        round_index=1,
        window_index=3,
        final_window=True,
    )

    assert no_hooks["phase"] == "macro_final"
    assert no_hooks["overlap_refactor"] == {"attempts": [], "refactor_segment_coverage": []}
    assert store_summary(store)["n_active"] == 1
    assert store_summary(store)["n_pending"] == 1
    assert store_summary(store)["n_disabled"] == 1
    assert calls == ["promote:True", "refactor", "filter", "trl"]
    assert all_hooks["promoted_pending_skills"] == ["pending"]
    assert all_hooks["overlap_refactor"]["attempts"][0]["group"] == "g"
    assert all_hooks["filtered_skills"] == ["disabled"]
    assert all_hooks["trl_feedback"] == {"feedback_events": 1}


def test_relation_graph_state_supports_concurrent_short_updates() -> None:
    graph = RelationGraphState()

    async def update_one(index: int) -> None:
        graph.update(
            nodes=[
                trace_relation_node(f"task_{index}", f"seg_{index}"),
                skill_relation_node("skill_shared", source_task_ids=[f"task_{index}"]),
                pending_skill_relation_node(f"pending_{index}"),
            ],
            edges=[
                RelationEdge(
                    source=f"trace_segment:seg_{index}",
                    target="skill:skill_shared",
                    relation="overlap",
                    weight=0.5 + index,
                )
            ],
        )

    async def run_updates() -> None:
        await asyncio.gather(*(update_one(index) for index in range(15)))

    asyncio.run(run_updates())
    snapshot = graph.snapshot()

    assert len(snapshot["nodes"]) == 31
    assert len(snapshot["edges"]) == 15
    assert len(graph.neighbors("skill:skill_shared")) == 15


def test_relation_graph_merges_nodes_edges_and_preserves_max_weight() -> None:
    graph = RelationGraphState()
    graph.upsert_node(RelationNode(node_id="skill:s", node_type="skill", label="old", metadata={"a": 1}))
    graph.upsert_node(RelationNode(node_id="skill:s", node_type="skill", label="new", metadata={"b": 2}))
    graph.upsert_node(trace_relation_node("task_1", "seg_1"))
    graph.upsert_edge(RelationEdge(source="skill:s", target="trace_segment:seg_1", relation="overlap", weight=0.2, metadata={"first": True}))
    graph.upsert_edge(RelationEdge(source="trace_segment:seg_1", target="skill:s", relation="overlap", weight=0.9, metadata={"second": True}))

    snapshot = graph.snapshot()

    assert snapshot["nodes"][0]["label"] == "new"
    assert snapshot["nodes"][0]["metadata"] == {"a": 1, "b": 2}
    assert len(snapshot["edges"]) == 1
    assert snapshot["edges"][0]["weight"] == 0.9
    assert snapshot["edges"][0]["metadata"] == {"first": True, "second": True}
    assert graph.neighbors("missing") == []
