"""Shared micro-maintenance orchestration skeleton."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Sequence

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.bundle_cases import bundle_case_rows_by_skill
from academic.benchmarks.core.credit_events import credit_target_names
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig

AsyncRefineHook = Callable[..., Awaitable[Dict[str, Any]]]
AsyncBundleTestHook = Callable[..., Awaitable[Dict[str, Any]]]


@dataclass
class MicroMaintenanceHooks:
    """Benchmark-specific operations used by the generic micro loop."""

    refine_skill: AsyncRefineHook
    run_bundle_test: AsyncBundleTestHook


def micro_target_names(
    *,
    credit_events: Sequence[Dict[str, Any]],
    credit_bundle_cases: Sequence[Dict[str, Any]],
    relevant_skill_names: Sequence[str] | None = None,
) -> List[str]:
    """Choose task-local skill targets without scanning the full store."""

    ordered: List[str] = []
    seen = set()
    for name in relevant_skill_names or []:
        value = str(name or "").strip()
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    for name in credit_target_names(credit_events):
        if name and name not in seen:
            ordered.append(name)
            seen.add(name)
    for name in bundle_case_rows_by_skill(credit_bundle_cases):
        if name and name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


async def run_generic_micro_maintenance(
    *,
    detail: Dict[str, Any],
    credit_events: Sequence[Dict[str, Any]],
    credit_bundle_cases: Sequence[Dict[str, Any]],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
    hooks: MicroMaintenanceHooks,
    round_index: int,
    task_index: int,
    relevant_skill_names: Sequence[str] | None = None,
    max_repair_rounds: int | None = None,
) -> Dict[str, Any]:
    """Run the shared credit -> refine -> bundle-test micro flow."""

    targets = micro_target_names(
        credit_events=credit_events,
        credit_bundle_cases=credit_bundle_cases,
        relevant_skill_names=relevant_skill_names,
    )
    if not targets:
        return {
            "phase": "micro",
            "task_id": detail.get("task_id"),
            "task_index": task_index,
            "maintenance_targets": [],
            "maintenance_test_results": [],
            "refine_decisions": [],
            "credit_bundle_cases": copy.deepcopy(list(credit_bundle_cases or [])),
            "reason": "no_micro_targets",
        }

    repair_limit = max(0, int(max_repair_rounds if max_repair_rounds is not None else config.extra.get("micro_refine_max_repair_rounds", 1)))
    grouped_cases = bundle_case_rows_by_skill(credit_bundle_cases)
    refine_decisions: List[Dict[str, Any]] = []
    test_results: List[Dict[str, Any]] = []

    for skill_name in targets:
        artifact = store.get(skill_name)
        if artifact is None:
            refine_decisions.append({"skill_name": skill_name, "action": "skip", "reason": "missing_skill"})
            continue
        skill_credit = [copy.deepcopy(event) for event in credit_events if event.get("skill_name") == skill_name]
        skill_case_rows = grouped_cases.get(skill_name, [])
        if skill_credit or skill_case_rows:
            refine_decisions.append(
                await hooks.refine_skill(
                    skill_name=skill_name,
                    artifact=artifact,
                    detail=detail,
                    credit_events=skill_credit,
                    credit_bundle_cases=skill_case_rows,
                    store=store,
                    config=config,
                    round_index=round_index,
                    task_index=task_index,
                    repair_round=0,
                    stage="credit_pre_refine",
                )
            )
        result = await hooks.run_bundle_test(
            skill_name=skill_name,
            artifact=store.get(skill_name) or artifact,
            detail=detail,
            credit_events=skill_credit,
            credit_bundle_cases=skill_case_rows,
            store=store,
            config=config,
            round_index=round_index,
            task_index=task_index,
            repair_round=0,
        )
        test_results.append(result)
        for repair_round in range(1, repair_limit + 1):
            if _bundle_result_passed(result):
                break
            refine_decisions.append(
                await hooks.refine_skill(
                    skill_name=skill_name,
                    artifact=store.get(skill_name) or artifact,
                    detail=detail,
                    credit_events=skill_credit,
                    credit_bundle_cases=skill_case_rows,
                    store=store,
                    config=config,
                    round_index=round_index,
                    task_index=task_index,
                    repair_round=repair_round,
                    stage="post_bundle_failure",
                    failed_bundle_result=result,
                )
            )
            result = await hooks.run_bundle_test(
                skill_name=skill_name,
                artifact=store.get(skill_name) or artifact,
                detail=detail,
                credit_events=skill_credit,
                credit_bundle_cases=skill_case_rows,
                store=store,
                config=config,
                round_index=round_index,
                task_index=task_index,
                repair_round=repair_round,
            )
            test_results.append(result)

    return {
        "phase": "micro",
        "task_id": detail.get("task_id"),
        "task_index": task_index,
        "round_index": round_index,
        "maintenance_targets": targets,
        "maintenance_test_results": test_results,
        "refine_decisions": refine_decisions,
        "credit_bundle_cases": copy.deepcopy(list(credit_bundle_cases or [])),
    }


def _bundle_result_passed(result: Dict[str, Any]) -> bool:
    if "passed" in result:
        return bool(result.get("passed"))
    aggregate = result.get("aggregate") or {}
    if "passed" in aggregate:
        return bool(aggregate.get("passed"))
    return bool(result.get("success"))
