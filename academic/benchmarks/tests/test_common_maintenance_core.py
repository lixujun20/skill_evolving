import asyncio
from typing import Any, Dict, List

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.bundle_cases import apply_credit_bundle_suggestions
from academic.benchmarks.core.credit_events import (
    apply_credit_evidence,
    credit_target_names,
    normalize_credit_events,
)
from academic.benchmarks.core.macro_maintenance import MacroMaintenanceHooks, run_generic_macro_maintenance
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig
from academic.benchmarks.core.micro_maintenance import MicroMaintenanceHooks, run_generic_micro_maintenance
from academic.benchmarks.core.relation_graph import (
    RelationEdge,
    RelationGraphState,
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
