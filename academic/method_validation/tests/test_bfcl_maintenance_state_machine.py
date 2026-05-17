from __future__ import annotations

import pytest

from academic.benchmarks.bfcl.legacy.real_maintenance_probe import BFCLRefineTransitionPolicy
from academic.skill_repository.maintenance_runner import (
    MaintenanceActionResult,
    MaintenanceRunner,
    MaintenanceRunnerSpec,
    default_maintenance_roles,
    default_maintenance_slots,
)


class DummyStore:
    def all(self):
        return []


class Role:
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn

    async def run(self, state):
        self.fn(state)
        return MaintenanceActionResult(
            frame_name=f"{self.name}:done",
            role_group=self.name,
            changed_elements=[f"role:{self.name}"],
        )


def _runner(context, roles):
    return MaintenanceRunner(
        spec=MaintenanceRunnerSpec(
            run_id="bfcl_refine_policy_test",
            slots=default_maintenance_slots(),
            roles=default_maintenance_roles(),
            initial_context={
                "store": DummyStore(),
                "maintenance_round": 0,
                "maintenance_rounds": [],
                "all_decisions": [],
                "integration_cases_appended": 0,
                **context,
            },
        ),
        role_backends=roles,
        transition_policy=BFCLRefineTransitionPolicy(),
    )


@pytest.mark.asyncio
async def test_refine_policy_empty_targets_terminates_without_roles() -> None:
    state = await _runner({"maintenance_targets": [], "current_targets": []}, {}).run_async(max_steps=3)

    assert state.terminal
    assert state.frames[0].action_kind == "terminal"
    assert state.context["terminal_payload"]["maintenance_targets"] == []


@pytest.mark.asyncio
async def test_refine_policy_pass_stops_after_unit_test() -> None:
    seen = []

    def bundle_builder(state):
        seen.append("bundle_builder")

    def unit_tester(state):
        seen.append("unit_tester")
        state.context["final_maintenance_results"] = [{"aggregate": {"pass_all_tests": True}}]

    state = await _runner(
        {"maintenance_targets": ["skill_a"], "current_targets": ["skill_a"]},
        {
            "bundle_builder": Role("bundle_builder", bundle_builder),
            "unit_tester": Role("unit_tester", unit_tester),
        },
    ).run_async(max_steps=5)

    assert seen == ["bundle_builder", "unit_tester"]
    assert state.context["terminal_payload"]["post_refine_test_results"] == []
    assert [frame.action_kind for frame in state.frames[:2]] == ["bundle_builder", "unit_tester"]


@pytest.mark.asyncio
async def test_refine_policy_failure_runs_refiner_and_post_retest() -> None:
    seen = []

    def bundle_builder(state):
        seen.append("bundle_builder")

    def unit_tester(state):
        seen.append("unit_tester")
        state.context["maintenance_objects"] = []
        state.context["final_maintenance_results"] = [{"aggregate": {"pass_all_tests": False}}]

    def refiner(state):
        seen.append("refiner")
        state.context["last_decisions"] = []
        state.context["retest_targets"] = ["skill_a"]

    def append_failures(state):
        seen.append("append_failures")

    def post_refine_tester(state):
        seen.append("post_refine_tester")
        state.context["post_refine_objects"] = []
        state.context["final_post_refine_results"] = [{"aggregate": {"pass_all_tests": True}}]

    def append_post_failures(state):
        seen.append("append_post_failures")

    state = await _runner(
        {"maintenance_targets": ["skill_a"], "current_targets": ["skill_a"]},
        {
            "bundle_builder": Role("bundle_builder", bundle_builder),
            "unit_tester": Role("unit_tester", unit_tester),
            "refiner": Role("refiner", refiner),
            "append_failures": Role("append_failures", append_failures),
            "post_refine_tester": Role("post_refine_tester", post_refine_tester),
            "append_post_failures": Role("append_post_failures", append_post_failures),
        },
    ).run_async(max_steps=10)

    assert seen == [
        "bundle_builder",
        "unit_tester",
        "refiner",
        "append_failures",
        "post_refine_tester",
        "append_post_failures",
    ]
    assert state.context["terminal_payload"]["post_refine_test_results"][0]["aggregate"]["pass_all_tests"] is True
