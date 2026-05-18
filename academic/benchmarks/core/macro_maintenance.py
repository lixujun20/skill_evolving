"""Shared macro-maintenance report and hook orchestration."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Sequence

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.credit_events import summarize_credit_events
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig

AsyncMacroHook = Callable[..., Awaitable[Dict[str, Any]]]


@dataclass
class MacroMaintenanceHooks:
    """Optional benchmark-specific operations in the macro window."""

    promote_pending: AsyncMacroHook | None = None
    refactor_overlap: AsyncMacroHook | None = None
    filter_skills: AsyncMacroHook | None = None
    update_trl: AsyncMacroHook | None = None


async def run_generic_macro_maintenance(
    *,
    window_details: Sequence[Dict[str, Any]],
    all_train_details: Sequence[Dict[str, Any]],
    credit_events: Sequence[Dict[str, Any]],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
    hooks: MacroMaintenanceHooks | None = None,
    round_index: int,
    window_index: int,
    final_window: bool = False,
) -> Dict[str, Any]:
    """Run optional window-level hooks and return a stable macro report."""

    hooks = hooks or MacroMaintenanceHooks()
    task_ids = [str(item.get("task_id") or "") for item in window_details]
    report: Dict[str, Any] = {
        "phase": "macro_final" if final_window else "macro",
        "round_index": round_index,
        "window_index": window_index,
        "task_ids": task_ids,
        "n_window_tasks": len(window_details),
        "n_train_tasks_seen": len(all_train_details),
        "credit_summary": summarize_credit_events(credit_events),
        "store_summary": store_summary(store),
        "promoted_pending_skills": [],
        "filtered_skills": [],
        "overlap_refactor": {"attempts": [], "refactor_segment_coverage": []},
        "trl_feedback": {},
    }
    if hooks.promote_pending is not None:
        promotion = await hooks.promote_pending(
            window_details=window_details,
            all_train_details=all_train_details,
            credit_events=credit_events,
            store=store,
            config=config,
            round_index=round_index,
            window_index=window_index,
            final_window=final_window,
        )
        report["promotion"] = copy.deepcopy(promotion)
        report["promoted_pending_skills"] = list(promotion.get("promoted_pending_skills") or promotion.get("promoted") or [])
    if hooks.refactor_overlap is not None:
        refactor = await hooks.refactor_overlap(
            window_details=window_details,
            all_train_details=all_train_details,
            credit_events=credit_events,
            store=store,
            config=config,
            round_index=round_index,
            window_index=window_index,
            final_window=final_window,
        )
        report["overlap_refactor"] = copy.deepcopy(refactor)
    if hooks.filter_skills is not None:
        filtered = await hooks.filter_skills(
            window_details=window_details,
            all_train_details=all_train_details,
            credit_events=credit_events,
            store=store,
            config=config,
            round_index=round_index,
            window_index=window_index,
            final_window=final_window,
        )
        report["filter"] = copy.deepcopy(filtered)
        report["filtered_skills"] = list(filtered.get("filtered_skills") or filtered.get("disabled_skills") or [])
    if hooks.update_trl is not None:
        trl = await hooks.update_trl(
            window_details=window_details,
            all_train_details=all_train_details,
            credit_events=credit_events,
            store=store,
            config=config,
            round_index=round_index,
            window_index=window_index,
            final_window=final_window,
        )
        report["trl_feedback"] = copy.deepcopy(trl)
    report["store_summary_after"] = store_summary(store)
    return report


def store_summary(store: ArtifactStore) -> Dict[str, Any]:
    artifacts = list(store.all())
    return {
        "n_skills": len(artifacts),
        "n_active": len([item for item in artifacts if item.status == "active"]),
        "n_pending": len([item for item in artifacts if item.status == "pending"]),
        "n_disabled": len([item for item in artifacts if item.status == "disabled" or item.is_disabled()]),
        "skill_names": [item.name for item in artifacts],
        "skill_versions": {item.name: item.version for item in artifacts},
    }
