import json
from pathlib import Path

import pytest

from academic.skill_repository.maintenance_runner import (
    MaintenanceActionResult,
    MaintenanceRunner,
    MaintenanceRunnerSpec,
    StaticTransitionPolicy,
    build_runner_trace_from_debug_events,
    default_maintenance_roles,
    default_maintenance_slots,
)
from academic.skill_repository.maintenance_state_machine import (
    build_player_trace_from_debug_events,
    build_player_trace_from_pages,
)


def test_player_trace_reconstructs_legacy_page_cards() -> None:
    trace = build_player_trace_from_pages(
        run_id="legacy_case",
        title="Legacy Case",
        kind="method_validation",
        artifacts=[],
        pages=[
            {
                "page_id": "round_0",
                "label": "Round 0",
                "title": "Round 0",
                "summary_metrics": [],
                "flow_cards": [
                    {
                        "type": "maintenance_test",
                        "title": "Unit Test",
                        "skill_name": "direct_diff_rule",
                        "aggregate": {"pass_all_tests": True},
                        "detail": {"unit_case_runs": []},
                    }
                ],
            }
        ],
    )

    assert trace["frames"][0]["action_kind"] == "init"
    assert any(frame["action_kind"] == "unit_test_completed" for frame in trace["frames"])
    assert "test_result:direct_diff_rule" in trace["frames"][-1]["elements"]


def test_player_trace_reconstructs_debug_event_state_deltas_with_duplicate_legacy_ids() -> None:
    trace = build_player_trace_from_debug_events(
        run_id="debug_case",
        title="Debug Case",
        kind="exp3",
        artifacts=[],
        pages=[],
        debug_events=[
            {
                "event_id": "debug_event_000001",
                "event_type": "retrieval",
                "turn_index": 0,
                "input": {"query": "compare reports"},
                "output": {"selected": [{"name": "direct_diff_rule", "score": 0.8}]},
            },
            {
                "event_id": "debug_event_000001",
                "event_type": "unit_test_done",
                "output": {
                    "skill_name": "direct_diff_rule",
                    "aggregate": {"pass_all_tests": True},
                },
            },
        ],
    )

    assert len(trace["frames"]) == 3
    assert trace["frames"][1]["delta"]["event"]["event_id"] == "debug_event_000001"
    assert trace["frames"][2]["delta"]["event"]["event_id"] == "debug_event_000001#2"
    assert "role:retriever" in trace["frames"][1]["changed_elements"]
    assert any(key.startswith("test_result:direct_diff_rule") for key in trace["frames"][-1]["elements"])


def test_player_trace_compacts_large_role_io_and_classifies_prompt_injection() -> None:
    trace = build_player_trace_from_debug_events(
        run_id="compact_case",
        title="Compact Case",
        kind="exp3",
        artifacts=[],
        pages=[],
        debug_events=[
            {
                "event_id": "debug_event_000001",
                "event_type": "prompt_injection",
                "input": {
                    "user_messages": [
                        {"role": "user", "content": "x" * 5000},
                    ],
                    "turn_prompt_skills": [
                        {"name": "direct_rule", "description": "d" * 1000, "score": 0.9},
                    ],
                },
                "output": {"system": "s" * 5000},
            }
        ],
    )

    frame = trace["frames"][1]
    assert frame["role_group"] == "retriever"
    assert "role:retriever" in frame["changed_elements"]
    event = frame["delta"]["event"]
    assert event["event_type"] == "prompt_injection"
    assert len(event["input"]["user_messages"][0]["content"]) < 900
    assert len(event["output"]["system"]) < 900


def test_runner_trace_builds_from_real_exp3_debug_events() -> None:
    result_path = Path(
        "academic/results/bfcl_real_glm_maintenance_2026-05-10/"
        "03_exp3_related_sequence_fault_repair/"
        "bfcl_real_glm_exp3_rerun_20260510_watchdog.json"
    )
    if not result_path.exists():
        return
    payload = json.loads(result_path.read_text())
    trace = build_runner_trace_from_debug_events(
        run_id="real_exp3_contract",
        title="Real Exp3 Contract",
        kind="exp3",
        debug_events=payload.get("debug_events") or [],
        artifacts=payload.get("final_skills") or [],
        pages=[],
    )

    assert trace["source_mode"] == "debug_events"
    assert trace["snapshot_mode"] == "delta"
    assert len(trace["frames"]) > 100
    groups = {frame.get("role_group") for frame in trace["frames"]}
    assert {"executor", "retriever", "extractor", "bundle_builder", "unit_tester", "refiner"} <= groups
    assert any(frame.get("consumed_slots") == ["skill", "bundle"] for frame in trace["frames"])
    assert any(frame.get("produced_slots") == ["test_result"] for frame in trace["frames"])


def test_maintenance_experiment_marks_legacy_test_runs_without_io_payload() -> None:
    from academic.webapp.app import app

    client = app.test_client()
    exp = "bfcl_real_glm_maintenance_2026-05-10__03_exp3_related_sequence_fault_repair"
    response = client.get(f"/api/maintenance/experiment?id={exp}")
    if response.status_code != 200:
        return
    payload = response.get_json()
    test_cards = [
        card
        for page in payload.get("pages") or []
        for card in page.get("flow_cards") or []
        if card.get("type") == "maintenance_test"
    ]
    if not test_cards:
        return
    run = (test_cards[0].get("unit_case_runs") or [])[0]
    assert "variant" in run
    assert run["io_available"] is False
    assert "historical test result" in run["io_unavailable_reason"]


@pytest.mark.asyncio
async def test_maintenance_runner_async_backend_executes_real_state_steps() -> None:
    class AsyncRole:
        async def run(self, state):
            return MaintenanceActionResult(
                frame_name="async_executor_done",
                summary=f"phase={state.phase}",
                role_group="executor",
                consumed_slots=["retrieval", "skill_store"],
                produced_slots=["trace"],
                changed_elements=["role:executor"],
                delta={"live_role": True},
            )

    runner = MaintenanceRunner(
        spec=MaintenanceRunnerSpec(
            run_id="async_runner_contract",
            slots=default_maintenance_slots(),
            roles=default_maintenance_roles(),
        ),
        role_backends={"executor": AsyncRole()},
        transition_policy=StaticTransitionPolicy(["executor"]),
    )
    state = await runner.run_async(max_steps=3)

    assert state.terminal
    assert state.frames[0].role_group == "executor"
    assert state.frames[0].produced_slots == ["trace"]
    assert state.frames[-1].action_kind == "terminal"


@pytest.mark.asyncio
async def test_bfcl_loop_transition_policy_controls_role_order() -> None:
    from academic.benchmarks.bfcl.legacy.real_maintenance_probe import BFCLLoopTransitionPolicy

    class Role:
        def __init__(self, name, produced):
            self.name = name
            self.produced = produced

        async def run(self, state):
            state.context.setdefault("seen_roles", []).append(self.name)
            return MaintenanceActionResult(
                frame_name=f"{self.name}:done",
                role_group=self.name,
                produced_slots=[self.produced],
                changed_elements=[f"role:{self.name}"],
            )

    runner = MaintenanceRunner(
        spec=MaintenanceRunnerSpec(
            run_id="bfcl_loop_policy_contract",
            slots=default_maintenance_slots(),
            roles=default_maintenance_roles(),
        ),
        role_backends={
            "executor": Role("executor", "trace"),
            "extractor": Role("extractor", "skill"),
            "refine_cycle": Role("refiner", "skill_store"),
        },
        transition_policy=BFCLLoopTransitionPolicy(["executor", "extractor", "refine_cycle"]),
    )
    state = await runner.run_async(max_steps=5)

    assert state.context["seen_roles"] == ["executor", "extractor", "refiner"]
    assert [frame.action_kind for frame in state.frames[:3]] == ["executor", "extractor", "refine_cycle"]
