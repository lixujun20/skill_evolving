"""Benchmark-agnostic contracts for online skill evolution.

The core runner owns the online train/test schedule.  Benchmark adapters own
native execution, trace projection, replayable bundle fragments, and verifier
details.  This keeps the maintenance algorithm portable without forcing BFCL,
SpreadsheetBench, or future benchmarks into the same trace format.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Protocol, Sequence, runtime_checkable

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.cost_accounting import cost_events_from_runs, summarize_cost_events
from academic.benchmarks.core.types import BenchmarkResult


@dataclass
class MaintenanceRunConfig:
    """Shared runtime knobs for a benchmark-agnostic evolution run."""

    llm_config: str
    model_name: str | None = None
    tag: str = "evolve"
    n_train_runs: int = 1
    n_test_runs: int = 1
    micro_maintenance_step: int = 1
    macro_maintenance_step: int = 10
    test_concurrency: int = 1
    max_task_seconds: float | None = None
    top_k_skills: int = 5
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRunBundle:
    """One benchmark task plus its repeated run outputs."""

    task_id: str
    task: Dict[str, Any]
    runs: List[Dict[str, Any]]

    @property
    def n_success(self) -> int:
        return sum(1 for run in self.runs if run.get("success"))

    @property
    def avg_score(self) -> float:
        return round(
            sum(float(run.get("score") or 0.0) for run in self.runs) / max(len(self.runs), 1),
            4,
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": copy.deepcopy(self.task),
            "n_runs": len(self.runs),
            "n_success": self.n_success,
            "avg_score": self.avg_score,
            "runs": copy.deepcopy(self.runs),
        }


def task_snapshot(task: Any, *, benchmark: str = "") -> Dict[str, Any]:
    """Return the stable benchmark-neutral task snapshot used in summaries."""

    return {
        "benchmark": getattr(task, "benchmark", benchmark),
        "task_id": getattr(task, "task_id", ""),
        "question": copy.deepcopy(getattr(task, "question", None)),
        "expected": copy.deepcopy(getattr(task, "expected", None)),
        "input_artifacts": copy.deepcopy(getattr(task, "input_artifacts", {}) or {}),
        "metadata": copy.deepcopy(getattr(task, "metadata", {}) or {}),
    }


def make_task_run_bundle(
    *,
    task: Any,
    runs: Sequence[Dict[str, Any]],
    benchmark: str = "",
) -> Dict[str, Any]:
    return TaskRunBundle(
        task_id=str(getattr(task, "task_id", "")),
        task=task_snapshot(task, benchmark=benchmark),
        runs=[copy.deepcopy(dict(run)) for run in runs],
    ).as_dict()


def aggregate_run_details(
    *,
    benchmark: str,
    mode: str,
    tag: str,
    llm_config: str,
    n_train: int,
    details: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Small benchmark-neutral aggregate used by generic smoke/evolve paths."""

    runs = [run for detail in details for run in (detail.get("runs") or [])]
    total_runs = len(runs)
    total_success = sum(1 for run in runs if run.get("success"))
    scores = [float(run.get("score") or 0.0) for run in runs]
    token_values = [
        int((run.get("metrics") or {}).get("total_tokens") or 0)
        for run in runs
        if (run.get("metrics") or {}).get("total_tokens") is not None
    ]
    input_values = [int((run.get("metrics") or {}).get("input_tokens") or 0) for run in runs]
    cache_values = [int((run.get("metrics") or {}).get("cache_input_tokens") or 0) for run in runs]
    output_values = [int((run.get("metrics") or {}).get("completion_tokens") or 0) for run in runs]
    cost_events = cost_events_from_runs(runs)
    cost_breakdown = summarize_cost_events(cost_events)
    total_tokens = sum(token_values)
    return {
        "benchmark": benchmark,
        "mode": mode,
        "tag": tag,
        "llm_config": llm_config,
        "n_train": n_train,
        "n_tasks": len(details),
        "n_runs": total_runs,
        "n_success": total_success,
        "success_rate": round(total_success / max(total_runs, 1), 4),
        "avg_score": round(sum(scores) / max(len(scores), 1), 4),
        "avg_total_tokens": round(sum(token_values) / max(len(token_values), 1), 2)
        if token_values
        else 0.0,
        "avg_input_tokens": round(sum(input_values) / max(len(input_values), 1), 2) if input_values else 0.0,
        "avg_cache_input_tokens": round(sum(cache_values) / max(len(cache_values), 1), 2) if cache_values else 0.0,
        "avg_output_tokens": round(sum(output_values) / max(len(output_values), 1), 2) if output_values else 0.0,
        "utility_per_million_tokens": {
            "successes_per_million_tokens": round(total_success * 1_000_000 / max(total_tokens, 1), 6),
            "score_points_per_million_tokens": round(sum(scores) * 1_000_000 / max(total_tokens, 1), 6),
            "total_tokens": total_tokens,
            "input_tokens": sum(input_values),
            "cache_input_tokens": sum(cache_values),
            "output_tokens": sum(output_values),
        },
        "cost_breakdown": cost_breakdown,
    }


@runtime_checkable
class BenchmarkMaintenanceAdapter(Protocol):
    """Benchmark-specific hooks consumed by the generic evolution runner."""

    benchmark: str

    async def run_task(
        self,
        task: Any,
        *,
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        phase: str,
        task_index: int,
        run_idx: int,
    ) -> BenchmarkResult:
        """Execute one benchmark-native task with the current skill store."""

    async def assign_credit(
        self,
        *,
        detail: Dict[str, Any],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> List[Dict[str, Any]]:
        """Return skill-credit events for one completed task."""

    async def apply_credit_bundle_cases(
        self,
        *,
        detail: Dict[str, Any],
        credit_events: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> List[Dict[str, Any]]:
        """Patch benchmark-native replayable bundle fragments from credit."""

    async def run_micro_maintenance(
        self,
        *,
        detail: Dict[str, Any],
        credit_events: Sequence[Dict[str, Any]],
        credit_bundle_cases: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> Dict[str, Any]:
        """Run task-local refine/test maintenance."""

    async def run_macro_maintenance(
        self,
        *,
        window_details: Sequence[Dict[str, Any]],
        all_train_details: Sequence[Dict[str, Any]],
        credit_events: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        window_index: int,
        final_window: bool = False,
    ) -> Dict[str, Any]:
        """Run window-level refactor/filter/TRL maintenance."""

    def store_snapshot(self, store: ArtifactStore) -> Dict[str, Any]:
        """Return a compact serializable store snapshot."""


class NoOpMaintenanceAdapter:
    """Base class for benchmarks that only support rollout at first."""

    benchmark = "unknown"

    async def assign_credit(
        self,
        *,
        detail: Dict[str, Any],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> List[Dict[str, Any]]:
        return []

    async def apply_credit_bundle_cases(
        self,
        *,
        detail: Dict[str, Any],
        credit_events: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> List[Dict[str, Any]]:
        return []

    async def run_micro_maintenance(
        self,
        *,
        detail: Dict[str, Any],
        credit_events: Sequence[Dict[str, Any]],
        credit_bundle_cases: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> Dict[str, Any]:
        return {
            "phase": "micro",
            "task_id": detail.get("task_id"),
            "task_index": task_index,
            "maintenance_targets": [],
            "maintenance_test_results": [],
            "refine_decisions": [],
            "credit_bundle_cases": list(copy.deepcopy(credit_bundle_cases)),
            "reason": "adapter_noop_micro",
        }

    async def run_macro_maintenance(
        self,
        *,
        window_details: Sequence[Dict[str, Any]],
        all_train_details: Sequence[Dict[str, Any]],
        credit_events: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        window_index: int,
        final_window: bool = False,
    ) -> Dict[str, Any]:
        task_ids = [str(item.get("task_id") or "") for item in window_details]
        return {
            "phase": "macro_final" if final_window else "macro",
            "window_index": window_index,
            "task_ids": task_ids,
            "maintenance_targets": [],
            "maintenance_test_results": [],
            "refine_decisions": [],
            "overlap_refactor": {"attempts": [], "refactor_segment_coverage": []},
            "run_overlap_refactor": False,
            "reason": "adapter_noop_macro",
        }

    def store_snapshot(self, store: ArtifactStore) -> Dict[str, Any]:
        return {
            "n_skills": len(store.all()),
            "skill_names": [artifact.name for artifact in store.all()],
            "skill_versions": {
                artifact.name: artifact.version
                for artifact in store.all()
            },
        }
