"""Benchmark-agnostic online skill evolution scheduler."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.cost_accounting import cost_events_from_runs, summarize_token_request_curve
from academic.benchmarks.core.maintenance_adapter import (
    BenchmarkMaintenanceAdapter,
    MaintenanceRunConfig,
    aggregate_run_details,
    make_task_run_bundle,
    task_snapshot,
)
from academic.benchmarks.core.types import BenchmarkResult
from academic.skill_repository.llm_maintenance import (
    maintenance_token_event_count,
    snapshot_maintenance_token_stats,
)


@dataclass
class _EvolutionPartialState:
    train_details: List[Dict[str, Any]]
    test_details: List[Dict[str, Any]]
    skill_credit_events: List[Dict[str, Any]]
    micro_reports: List[Dict[str, Any]]
    maintenance_windows: List[Dict[str, Any]]
    macro_skill_snapshots: List[Dict[str, Any]]
    store: ArtifactStore


@dataclass
class OnlineSkillEvolutionRunner:
    """Run the common train/credit/micro/macro/test schedule for any benchmark."""

    adapter: BenchmarkMaintenanceAdapter
    config: MaintenanceRunConfig

    async def run(
        self,
        *,
        train_tasks: Sequence[Any],
        test_tasks: Sequence[Any],
        seed_store: ArtifactStore | None = None,
        rounds: int = 1,
    ) -> Dict[str, Any]:
        resume = self._load_partial_state(seed_store=seed_store)
        store = resume["store"]
        train_details: List[Dict[str, Any]] = list(resume["train_details"])
        test_details: List[Dict[str, Any]] = list(resume["test_details"])
        skill_credit_events: List[Dict[str, Any]] = list(resume["skill_credit_events"])
        micro_reports: List[Dict[str, Any]] = list(resume["micro_reports"])
        maintenance_windows: List[Dict[str, Any]] = list(resume["maintenance_windows"])
        macro_skill_snapshots: List[Dict[str, Any]] = list(resume["macro_skill_snapshots"])
        self._resume_maintenance_events = list(resume.get("maintenance_token_events") or [])
        self._partial_state = _EvolutionPartialState(
            train_details=train_details,
            test_details=test_details,
            skill_credit_events=skill_credit_events,
            micro_reports=micro_reports,
            maintenance_windows=maintenance_windows,
            macro_skill_snapshots=macro_skill_snapshots,
            store=store,
        )
        self._maintenance_token_start_index = maintenance_token_event_count()
        self._persist_partial_state(stage="start")

        effective_rounds = max(1, int(rounds or 1))
        micro_step = max(1, int(self.config.micro_maintenance_step or 1))
        macro_step = max(1, int(self.config.macro_maintenance_step or 10))

        for round_index in range(effective_rounds):
            if (
                max(1, int(self.config.train_concurrency or 1)) <= 1
                and max(1, int(self.config.micro_maintenance_concurrency or 1)) <= 1
            ):
                await self._run_serial_train_round(
                    train_tasks=train_tasks,
                    store=store,
                    train_details=train_details,
                    skill_credit_events=skill_credit_events,
                    micro_reports=micro_reports,
                    maintenance_windows=maintenance_windows,
                    macro_skill_snapshots=macro_skill_snapshots,
                    round_index=round_index,
                    micro_step=micro_step,
                    macro_step=macro_step,
                )
                continue
            for window_start in range(0, len(train_tasks), macro_step):
                indexed_window_tasks = list(enumerate(train_tasks))[window_start: window_start + macro_step]
                pending_window_tasks = [
                    (task_index, task)
                    for task_index, task in indexed_window_tasks
                    if not self._detail_done(train_details, round_index=round_index, phase="train", task_index=task_index)
                ]
                final_window = window_start + macro_step >= len(train_tasks)
                if pending_window_tasks:
                    window_store = self._copy_store(store)
                    await self._run_train_window_tasks(
                        pending_window_tasks,
                        store=window_store,
                        round_index=round_index,
                        record_details=train_details,
                    )
                    self._persist_partial_state(stage="train_window_done")

                window_task_indexes = [task_index for task_index, _ in indexed_window_tasks]
                window_details = [
                    detail for detail in train_details
                    if _detail_task_index(detail) in window_task_indexes
                    and int(detail.get("round_index") or 0) == round_index
                    and str(detail.get("evolution_phase") or "train") == "train"
                ]
                window_details.sort(key=lambda item: _detail_task_index(item))
                pending_micro_details = [
                    detail for detail in window_details
                    if not self._micro_done(micro_reports, task_index=_detail_task_index(detail))
                ]

                window_credit_events: List[Dict[str, Any]] = []
                if pending_micro_details:
                    window_micro_reports = await self._run_window_maintenance(
                        window_details=pending_micro_details,
                        store=store,
                        round_index=round_index,
                        micro_step=micro_step,
                    )
                    for item in window_micro_reports:
                        window_credit_events.extend(copy.deepcopy(item.pop("_credit_events", [])))
                    skill_credit_events.extend(copy.deepcopy(window_credit_events))
                    micro_reports.extend(window_micro_reports)
                    self._persist_partial_state(stage="micro_window_done")

                window_index = window_start // macro_step
                if not self._macro_done(maintenance_windows, window_index=window_index):
                    macro_report = await self.adapter.run_macro_maintenance(
                        window_details=window_details,
                        all_train_details=train_details,
                        credit_events=skill_credit_events,
                        store=store,
                        config=self.config,
                        round_index=round_index,
                        window_index=window_index,
                        final_window=final_window,
                    )
                    maintenance_windows.append(macro_report)
                    macro_skill_snapshots.append(
                        self._write_macro_skill_snapshot(
                            store=store,
                            macro_report=macro_report,
                            round_index=round_index,
                            window_index=window_index,
                            train_details=train_details,
                            final_window=final_window,
                        )
                    )
                    self._persist_partial_state(stage="macro_window_done")

        pending_test = [
            (task_index, task)
            for task_index, task in enumerate(test_tasks)
            if not self._detail_done(test_details, round_index=0, phase="test", task_index=task_index)
        ]
        if pending_test:
            await self._run_test_tasks(pending_test, store=store, record_details=test_details)
            test_details.sort(key=lambda item: _detail_task_index(item))
            self._persist_partial_state(stage="test_done")
        train_summary = aggregate_run_details(
            benchmark=self.adapter.benchmark,
            mode="generic_evolve_train",
            tag=self.config.tag,
            llm_config=self.config.llm_config,
            n_train=len(train_tasks),
            details=train_details,
        )
        test_summary = aggregate_run_details(
            benchmark=self.adapter.benchmark,
            mode="generic_evolve_test",
            tag=self.config.tag,
            llm_config=self.config.llm_config,
            n_train=len(train_tasks),
            details=test_details,
        )
        cost_events = [
            *cost_events_from_runs(run for detail in train_details for run in (detail.get("runs") or [])),
            *cost_events_from_runs(run for detail in test_details for run in (detail.get("runs") or [])),
        ]
        maintenance_stats = snapshot_maintenance_token_stats(start_index=self._maintenance_token_start_index)
        maintenance_events = self._all_maintenance_token_events()
        maintenance_stats = _summarize_maintenance_token_events(
            maintenance_events,
            start_index=0,
            end_index=len(maintenance_events),
        )
        maintenance_stats["n_resumed_prior_events"] = len(self._resume_maintenance_events)
        token_curve = summarize_token_request_curve(
            [*cost_events, *maintenance_events],
            bucket_seconds=int(self.config.extra.get("token_curve_bucket_seconds") or 60),
        )
        summary = {
            "benchmark": self.adapter.benchmark,
            "mode": "generic_online_skill_evolve",
            "tag": self.config.tag,
            "llm_config": self.config.llm_config,
            "model_name": self.config.model_name,
            "config_summary": self.config.as_dict(),
            "train_summary": train_summary,
            "test_summary": test_summary,
            "train_details": train_details,
            "test_details": test_details,
            "skill_credit_events": skill_credit_events,
            "micro_maintenance_reports": micro_reports,
            "maintenance_windows": maintenance_windows,
            "macro_skill_snapshots": [row for row in macro_skill_snapshots if row],
            "maintenance_token_stats": maintenance_stats,
            "token_request_curve": token_curve,
            "observed_wall_clock_s": _token_curve_observed_wall_clock_s(token_curve),
            "store_snapshot": self.adapter.store_snapshot(store),
            "skills": [artifact.as_dict() for artifact in store.all()],
        }
        self._persist_partial_state(stage="complete", completed=True)
        return summary

    async def _run_serial_train_round(
        self,
        *,
        train_tasks: Sequence[Any],
        store: ArtifactStore,
        train_details: List[Dict[str, Any]],
        skill_credit_events: List[Dict[str, Any]],
        micro_reports: List[Dict[str, Any]],
        maintenance_windows: List[Dict[str, Any]],
        macro_skill_snapshots: List[Dict[str, Any]],
        round_index: int,
        micro_step: int,
        macro_step: int,
    ) -> None:
        window_details: List[Dict[str, Any]] = []
        for task_index, task in enumerate(train_tasks):
            if self._detail_done(train_details, round_index=round_index, phase="train", task_index=task_index):
                continue
            detail = await self._run_task_repeats(
                task,
                store=store,
                phase=f"{self.adapter.benchmark}_train_round_{round_index}",
                task_index=task_index,
                n_runs=max(1, int(self.config.n_train_runs or 1)),
            )
            train_details.append(detail)
            train_details.sort(key=lambda item: _detail_task_index(item))
            window_details.append(detail)
            self._persist_partial_state(stage="train_task_done")
            report = await self._safe_run_one_task_maintenance(
                detail=detail,
                store=store,
                round_index=round_index,
                micro_step=micro_step,
            )
            skill_credit_events.extend(copy.deepcopy(report.pop("_credit_events", [])))
            micro_reports.append(report)
            self._persist_partial_state(stage="micro_task_done")

            if (task_index + 1) % macro_step == 0:
                await self._run_macro_window(
                    window_details=window_details,
                    train_details=train_details,
                    skill_credit_events=skill_credit_events,
                    store=store,
                    maintenance_windows=maintenance_windows,
                    macro_skill_snapshots=macro_skill_snapshots,
                    round_index=round_index,
                    final_window=False,
                )
                window_details = []
                self._persist_partial_state(stage="macro_window_done")

        if window_details:
            await self._run_macro_window(
                window_details=window_details,
                train_details=train_details,
                skill_credit_events=skill_credit_events,
                store=store,
                maintenance_windows=maintenance_windows,
                macro_skill_snapshots=macro_skill_snapshots,
                round_index=round_index,
                final_window=True,
            )
            self._persist_partial_state(stage="macro_window_done")

    async def _run_macro_window(
        self,
        *,
        window_details: Sequence[Dict[str, Any]],
        train_details: Sequence[Dict[str, Any]],
        skill_credit_events: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        maintenance_windows: List[Dict[str, Any]],
        macro_skill_snapshots: List[Dict[str, Any]],
        round_index: int,
        final_window: bool,
    ) -> None:
        macro_report = await self.adapter.run_macro_maintenance(
            window_details=window_details,
            all_train_details=train_details,
            credit_events=skill_credit_events,
            store=store,
            config=self.config,
            round_index=round_index,
            window_index=len(maintenance_windows),
            final_window=final_window,
        )
        maintenance_windows.append(macro_report)
        macro_skill_snapshots.append(
            self._write_macro_skill_snapshot(
                store=store,
                macro_report=macro_report,
                round_index=round_index,
                window_index=len(maintenance_windows) - 1,
                train_details=train_details,
                final_window=final_window,
            )
        )

    async def _run_train_window_tasks(
        self,
        indexed_tasks: Sequence[tuple[int, Any]],
        *,
        store: ArtifactStore,
        round_index: int,
        record_details: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        concurrency = max(1, int(self.config.train_concurrency or 1))
        if concurrency <= 1 or len(indexed_tasks) <= 1:
            details = []
            for task_index, task in indexed_tasks:
                detail = await self._run_task_repeats(
                    task,
                    store=store,
                    phase=f"{self.adapter.benchmark}_train_round_{round_index}",
                    task_index=task_index,
                    n_runs=max(1, int(self.config.n_train_runs or 1)),
                )
                details.append(detail)
                self._record_detail(record_details, detail, stage="train_task_done")
            return details

        sem = asyncio.Semaphore(concurrency)

        async def guarded(task_index: int, task: Any) -> tuple[int, Dict[str, Any]]:
            task_store = self._copy_store(store)
            async with sem:
                return (
                    task_index,
                    await self._run_task_repeats(
                        task,
                        store=task_store,
                        phase=f"{self.adapter.benchmark}_train_round_{round_index}",
                        task_index=task_index,
                        n_runs=max(1, int(self.config.n_train_runs or 1)),
                    ),
                )

        pending = [asyncio.create_task(guarded(task_index, task)) for task_index, task in indexed_tasks]
        completed = []
        for future in asyncio.as_completed(pending):
            task_index, detail = await future
            completed.append((task_index, detail))
            self._record_detail(record_details, detail, stage="train_task_done")
        return [detail for _, detail in sorted(completed, key=lambda item: item[0])]

    async def _run_window_maintenance(
        self,
        *,
        window_details: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        round_index: int,
        micro_step: int,
    ) -> List[Dict[str, Any]]:
        concurrency = max(1, int(self.config.micro_maintenance_concurrency or 1))
        if concurrency <= 1 or len(window_details) <= 1:
            reports: List[Dict[str, Any]] = []
            for detail in window_details:
                reports.append(
                    await self._safe_run_one_task_maintenance(
                        detail=detail,
                        store=store,
                        round_index=round_index,
                        micro_step=micro_step,
                    )
                )
            return reports

        locks: Dict[str, asyncio.Lock] = {}

        async def guarded(detail: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
            task_index = _detail_task_index(detail)
            lock_names = _unique_names(
                self.adapter.maintenance_lock_names(
                    detail=detail,
                    store=store,
                    config=self.config,
                )
            )
            lock_names = _dependency_lock_neighborhood(store, lock_names)
            acquired = [locks.setdefault(name, asyncio.Lock()) for name in lock_names]
            for lock in sorted(acquired, key=id):
                await lock.acquire()
            try:
                report = await self._safe_run_one_task_maintenance(
                    detail=detail,
                    store=store,
                    round_index=round_index,
                    micro_step=micro_step,
                )
            finally:
                for lock in reversed(sorted(acquired, key=id)):
                    lock.release()
            return task_index, report

        sem = asyncio.Semaphore(concurrency)

        async def bounded(detail: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
            async with sem:
                return await guarded(detail)

        completed = await asyncio.gather(*[bounded(detail) for detail in window_details])
        return [report for _, report in sorted(completed, key=lambda item: item[0])]

    async def _safe_run_one_task_maintenance(
        self,
        *,
        detail: Dict[str, Any],
        store: ArtifactStore,
        round_index: int,
        micro_step: int,
    ) -> Dict[str, Any]:
        try:
            return await self._run_one_task_maintenance(
                detail=detail,
                store=store,
                round_index=round_index,
                micro_step=micro_step,
            )
        except Exception as exc:
            report = {
                "phase": "micro",
                "task_id": detail.get("task_id"),
                "task_index": _detail_task_index(detail),
                "maintenance_targets": [],
                "maintenance_test_results": [],
                "refine_decisions": [],
                "credit_bundle_cases": [],
                "reason": f"maintenance_failed:{type(exc).__name__}",
                "error": str(exc),
                "_credit_events": [],
            }
            return report

    async def _run_one_task_maintenance(
        self,
        *,
        detail: Dict[str, Any],
        store: ArtifactStore,
        round_index: int,
        micro_step: int,
        precomputed_credit: Sequence[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        task_index = _detail_task_index(detail)
        task_credit = list(precomputed_credit) if precomputed_credit is not None else await self.adapter.assign_credit(
            detail=detail,
            store=store,
            config=self.config,
            round_index=round_index,
            task_index=task_index,
        )
        credit_bundle_cases = await self.adapter.apply_credit_bundle_cases(
            detail=detail,
            credit_events=task_credit,
            store=store,
            config=self.config,
            round_index=round_index,
            task_index=task_index,
        )
        if (task_index + 1) % micro_step == 0:
            report = await self.adapter.run_micro_maintenance(
                detail=detail,
                credit_events=task_credit,
                credit_bundle_cases=credit_bundle_cases,
                store=store,
                config=self.config,
                round_index=round_index,
                task_index=task_index,
            )
        else:
            report = {
                "phase": "micro",
                "task_id": detail.get("task_id"),
                "task_index": task_index,
                "maintenance_targets": [],
                "maintenance_test_results": [],
                "refine_decisions": [],
                "credit_bundle_cases": copy.deepcopy(credit_bundle_cases),
                "reason": "micro_step_not_reached",
            }
        report["_credit_events"] = copy.deepcopy(task_credit)
        return report

    def _write_macro_skill_snapshot(
        self,
        *,
        store: ArtifactStore,
        macro_report: Dict[str, Any],
        round_index: int,
        window_index: int,
        train_details: Sequence[Dict[str, Any]],
        final_window: bool,
    ) -> Dict[str, Any]:
        raw_dir = str(self.config.extra.get("macro_snapshot_dir") or "").strip()
        if not raw_dir:
            return {}
        snapshot_dir = Path(raw_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"round_{round_index:02d}_macro_{window_index:03d}"
        skills_path = snapshot_dir / f"{prefix}_skills.json"
        meta_path = snapshot_dir / f"{prefix}_meta.json"
        store.save(skills_path)
        meta = {
            "benchmark": self.adapter.benchmark,
            "tag": self.config.tag,
            "round_index": round_index,
            "window_index": window_index,
            "final_window": bool(final_window),
            "train_tasks_completed": len(train_details),
            "n_active": len([item for item in store.all() if item.status == "active"]),
            "n_pending": len([item for item in store.all() if item.status == "pending"]),
            "n_disabled": len([item for item in store.all() if item.status == "disabled" or item.is_disabled()]),
            "skill_count": len(store.all()),
            "skills_path": str(skills_path),
            "macro_report_summary": {
                "phase": macro_report.get("phase"),
                "window_index": macro_report.get("window_index", window_index),
                "promoted_pending_skills": (macro_report.get("overlap_refactor") or {}).get("promoted_pending_skills")
                or macro_report.get("promoted_pending_skills")
                or [],
                "filtered_skills": (macro_report.get("overlap_refactor") or {}).get("filtered_skills")
                or macro_report.get("filtered_skills")
                or [],
            },
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        return dict(meta, meta_path=str(meta_path))

    async def _run_test_tasks(
        self,
        indexed_tasks: Sequence[tuple[int, Any]],
        *,
        store: ArtifactStore,
        record_details: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        concurrency = max(1, int(self.config.test_concurrency or 1))
        test_config = self._phase_config("test")
        if concurrency <= 1 or len(indexed_tasks) <= 1:
            details = []
            for task_index, task in indexed_tasks:
                detail = await self._run_task_repeats(
                    task,
                    store=store,
                    config=test_config,
                    phase=f"{self.adapter.benchmark}_heldout_test",
                    task_index=task_index,
                    n_runs=max(1, int(self.config.n_test_runs or 1)),
                )
                details.append(detail)
                self._record_detail(record_details, detail, stage="test_task_done")
            return details

        sem = asyncio.Semaphore(concurrency)

        async def guarded(task_index: int, task: Any) -> tuple[int, Dict[str, Any]]:
            task_store = self._copy_store(store)
            async with sem:
                return (
                    task_index,
                    await self._run_task_repeats(
                        task,
                        store=task_store,
                        config=test_config,
                        phase=f"{self.adapter.benchmark}_heldout_test",
                        task_index=task_index,
                        n_runs=max(1, int(self.config.n_test_runs or 1)),
                    ),
                )

        pending = [asyncio.create_task(guarded(task_index, task)) for task_index, task in indexed_tasks]
        completed = []
        for future in asyncio.as_completed(pending):
            task_index, detail = await future
            completed.append((task_index, detail))
            self._record_detail(record_details, detail, stage="test_task_done")
        return [detail for _, detail in sorted(completed, key=lambda item: item[0])]

    async def _run_task_repeats(
        self,
        task: Any,
        *,
        store: ArtifactStore,
        config: MaintenanceRunConfig | None = None,
        phase: str,
        task_index: int,
        n_runs: int,
    ) -> Dict[str, Any]:
        run_config = config or self.config
        runs: List[Dict[str, Any]] = []
        for run_idx in range(n_runs):
            coro = self.adapter.run_task(
                task,
                store=store,
                config=run_config,
                phase=phase,
                task_index=task_index,
                run_idx=run_idx,
            )
            try:
                if run_config.max_task_seconds and not run_config.extra.get("disable_task_wall_timeout"):
                    result = await asyncio.wait_for(coro, timeout=run_config.max_task_seconds)
                else:
                    result = await coro
            except asyncio.TimeoutError:
                result = self._failed_task_result(
                    task=task,
                    phase=phase,
                    task_index=task_index,
                    run_idx=run_idx,
                    reason="task_timeout",
                    error=f"Task exceeded {run_config.max_task_seconds} seconds",
                    config=run_config,
                )
            except Exception as exc:
                result = self._failed_task_result(
                    task=task,
                    phase=phase,
                    task_index=task_index,
                    run_idx=run_idx,
                    reason=f"task_exception:{type(exc).__name__}",
                    error=str(exc),
                    config=run_config,
                )
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
        detail = make_task_run_bundle(
            task=task,
            runs=runs,
            benchmark=self.adapter.benchmark,
        )
        detail["task_index"] = task_index
        detail["phase"] = phase
        detail["evolution_phase"] = "test" if "heldout_test" in phase else "train"
        detail["round_index"] = _phase_round_index(phase)
        return detail

    def _failed_task_result(
        self,
        *,
        task: Any,
        phase: str,
        task_index: int,
        run_idx: int,
        reason: str,
        error: str,
        config: MaintenanceRunConfig | None = None,
    ) -> BenchmarkResult:
        run_config = config or self.config
        task_id = str(getattr(task, "task_id", ""))
        return BenchmarkResult(
            benchmark=self.adapter.benchmark,
            task_id=task_id,
            success=False,
            score=0.0,
            metrics={
                "exception": reason,
                "error": error,
                "phase": phase,
                "task_index": task_index,
                "run_idx": run_idx,
                "max_task_seconds": run_config.max_task_seconds,
            },
            trace={
                "task": task_snapshot(task, benchmark=self.adapter.benchmark),
                "phase": phase,
                "task_index": task_index,
                "run_idx": run_idx,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "exception": reason,
                "error": error,
            },
            error=error,
        )

    def _phase_config(self, phase: str) -> MaintenanceRunConfig:
        if phase != "test":
            return self.config
        extra = dict(self.config.extra or {})
        test_extra = dict(extra.get("test_extra") or {})
        if not test_extra:
            return self.config
        merged = {**extra, **test_extra}
        merged.pop("test_extra", None)
        return MaintenanceRunConfig(
            llm_config=self.config.llm_config,
            model_name=self.config.model_name,
            tag=self.config.tag,
            n_train_runs=self.config.n_train_runs,
            n_test_runs=self.config.n_test_runs,
            micro_maintenance_step=self.config.micro_maintenance_step,
            macro_maintenance_step=self.config.macro_maintenance_step,
            train_concurrency=self.config.train_concurrency,
            micro_maintenance_concurrency=self.config.micro_maintenance_concurrency,
            test_concurrency=self.config.test_concurrency,
            max_task_seconds=self.config.max_task_seconds,
            top_k_skills=self.config.top_k_skills,
            extra=merged,
        )

    def _partial_output_path(self) -> Path | None:
        raw = str(self.config.extra.get("partial_output") or "").strip()
        return Path(raw) if raw else None

    def _load_partial_state(self, *, seed_store: ArtifactStore | None) -> Dict[str, Any]:
        path = self._partial_output_path()
        if not path or not path.exists():
            return {
                "store": self._copy_store(seed_store or ArtifactStore()),
                "train_details": [],
                "test_details": [],
                "skill_credit_events": [],
                "micro_reports": [],
                "maintenance_windows": [],
                "macro_skill_snapshots": [],
                "maintenance_token_events": [],
            }
        try:
            payload = json.loads(path.read_text())
        except Exception:
            return {
                "store": self._copy_store(seed_store or ArtifactStore()),
                "train_details": [],
                "test_details": [],
                "skill_credit_events": [],
                "micro_reports": [],
                "maintenance_windows": [],
                "macro_skill_snapshots": [],
                "maintenance_token_events": [],
            }
        store = ArtifactStore(payload.get("skills") or [])
        return {
            "store": store,
            "train_details": list(payload.get("train_details") or []),
            "test_details": list(payload.get("test_details") or []),
            "skill_credit_events": list(payload.get("skill_credit_events") or []),
            "micro_reports": list(payload.get("micro_maintenance_reports") or []),
            "maintenance_windows": list(payload.get("maintenance_windows") or []),
            "macro_skill_snapshots": list(payload.get("macro_skill_snapshots") or []),
            "maintenance_token_events": list(payload.get("maintenance_token_events") or []),
        }

    def _persist_partial_state(self, *, stage: str, completed: bool = False) -> None:
        path = self._partial_output_path()
        state = getattr(self, "_partial_state", None)
        if not path or state is None:
            return
        state.train_details.sort(key=lambda item: _detail_task_index(item))
        state.test_details.sort(key=lambda item: _detail_task_index(item))
        payload = {
            "version": 1,
            "benchmark": self.adapter.benchmark,
            "mode": "generic_online_skill_evolve_partial",
            "tag": self.config.tag,
            "stage": stage,
            "completed": bool(completed),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "config_summary": self.config.as_dict(),
            "task_state": {
                "train": _task_state_rows(state.train_details),
                "test": _task_state_rows(state.test_details),
            },
            "train_details": state.train_details,
            "test_details": state.test_details,
            "skill_credit_events": state.skill_credit_events,
            "micro_maintenance_reports": state.micro_reports,
            "maintenance_windows": state.maintenance_windows,
            "macro_skill_snapshots": [row for row in state.macro_skill_snapshots if row],
            "maintenance_token_events": self._all_maintenance_token_events(),
            "store_snapshot": self.adapter.store_snapshot(state.store),
            "skills": [artifact.as_dict() for artifact in state.store.all()],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(path)

    def _all_maintenance_token_events(self) -> List[Dict[str, Any]]:
        current = snapshot_maintenance_token_stats(start_index=self._maintenance_token_start_index).get("events") or []
        return [*copy.deepcopy(getattr(self, "_resume_maintenance_events", [])), *copy.deepcopy(list(current))]

    def _record_detail(self, details: List[Dict[str, Any]] | None, detail: Dict[str, Any], *, stage: str) -> None:
        if details is None:
            return
        task_index = _detail_task_index(detail)
        for idx, existing in enumerate(details):
            if _detail_task_index(existing) == task_index and existing.get("evolution_phase") == detail.get("evolution_phase"):
                details[idx] = detail
                break
        else:
            details.append(detail)
        details.sort(key=lambda item: _detail_task_index(item))
        self._persist_partial_state(stage=stage)

    @staticmethod
    def _detail_done(
        details: Sequence[Dict[str, Any]],
        *,
        round_index: int,
        phase: str,
        task_index: int,
    ) -> bool:
        del round_index
        expected_phase = "test" if phase == "test" else "train"
        return any(
            _detail_task_index(detail) == int(task_index)
            and str(detail.get("evolution_phase") or expected_phase) == expected_phase
            for detail in details
        )

    @staticmethod
    def _micro_done(reports: Sequence[Dict[str, Any]], *, task_index: int) -> bool:
        return any(_detail_task_index(report) == int(task_index) for report in reports)

    @staticmethod
    def _macro_done(reports: Sequence[Dict[str, Any]], *, window_index: int) -> bool:
        for report in reports:
            try:
                if int(report.get("window_index")) == int(window_index):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _copy_store(store: ArtifactStore) -> ArtifactStore:
        return ArtifactStore(
            [copy.deepcopy(artifact) for artifact in store.all()],
            test_results=[copy.deepcopy(result) for result in store.test_results()],
        )


def _detail_task_index(detail: Dict[str, Any]) -> int:
    try:
        return int(detail.get("task_index"))
    except Exception:
        return 0


def _phase_round_index(phase: str) -> int:
    marker = "_round_"
    if marker not in phase:
        return 0
    tail = phase.split(marker, 1)[1]
    try:
        return int(tail.split("_", 1)[0])
    except Exception:
        return 0


def _task_state_rows(details: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for detail in details:
        runs = list(detail.get("runs") or [])
        rows.append(
            {
                "task_id": detail.get("task_id"),
                "task_index": _detail_task_index(detail),
                "phase": detail.get("evolution_phase"),
                "round_index": detail.get("round_index"),
                "n_runs": len(runs),
                "n_success": sum(1 for run in runs if run.get("success")),
                "failed": any(not run.get("success") for run in runs),
                "errors": [run.get("error") for run in runs if run.get("error")],
            }
        )
    return sorted(rows, key=lambda row: int(row.get("task_index") or 0))


def _summarize_maintenance_token_events(
    events: Sequence[Dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
) -> Dict[str, Any]:
    rows = [copy.deepcopy(dict(event or {})) for event in events or [] if isinstance(event, dict)]

    def empty() -> Dict[str, Any]:
        return {
            "n_calls": 0,
            "prompt_tokens": 0,
            "input_tokens": 0,
            "cache_input_tokens": 0,
            "completion_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0.0,
            "duration_ms": 0,
        }

    def add(target: Dict[str, Any], event: Dict[str, Any]) -> None:
        target["n_calls"] += 1
        target["prompt_tokens"] += int(event.get("prompt_tokens") or event.get("input_tokens") or 0)
        target["input_tokens"] += int(event.get("input_tokens") or event.get("prompt_tokens") or 0)
        target["cache_input_tokens"] += int(event.get("cache_input_tokens") or 0)
        target["completion_tokens"] += int(event.get("completion_tokens") or event.get("output_tokens") or 0)
        target["output_tokens"] += int(event.get("output_tokens") or event.get("completion_tokens") or 0)
        target["total_tokens"] += int(event.get("total_tokens") or 0)
        target["estimated_cost"] = round(
            float(target.get("estimated_cost") or 0.0) + float(event.get("estimated_cost") or 0.0),
            8,
        )
        target["duration_ms"] += int(event.get("duration_ms") or 0)

    summary = empty()
    by_role: Dict[str, Dict[str, Any]] = {}
    by_phase: Dict[str, Dict[str, Any]] = {}
    for event in rows:
        role = str(event.get("role") or "unknown")
        phase = str(event.get("phase") or "unscoped")
        add(summary, event)
        add(by_role.setdefault(role, empty()), event)
        add(by_phase.setdefault(phase, empty()), event)
    return {
        "start_index": int(start_index),
        "end_index": int(end_index),
        "summary": summary,
        "by_role": by_role,
        "by_phase": by_phase,
        "events": rows,
        "recent_events": rows[-20:],
    }


def _token_curve_observed_wall_clock_s(curve: Dict[str, Any]) -> float:
    buckets = list(curve.get("buckets") or [])
    bucket_seconds = int(curve.get("bucket_seconds") or 60)
    dated = [
        bucket for bucket in buckets
        if bucket.get("bucket_start_s") is not None and int(bucket.get("n_calls") or 0) > 0
    ]
    if not dated:
        return 0.0
    last = max(int(bucket.get("bucket_start_s") or 0) for bucket in dated)
    return float(last + bucket_seconds)


def _credit_skill_names(credit_events: Sequence[Dict[str, Any]]) -> List[str]:
    return _unique_names(event.get("skill_name") for event in credit_events or [])


def _unique_names(values: Sequence[Any]) -> List[str]:
    names: List[str] = []
    seen = set()
    for item in values or []:
        value = str(item or "").strip()
        if value and value not in seen:
            names.append(value)
            seen.add(value)
    return names


def _dependency_lock_neighborhood(store: ArtifactStore, names: Sequence[str]) -> List[str]:
    locked = set(_unique_names(names))
    if not locked:
        return []
    changed = True
    while changed:
        changed = False
        for artifact in store.all():
            deps = {str(item) for item in (artifact.dependencies or [])}
            if artifact.name in locked or not deps.intersection(locked):
                continue
            locked.add(artifact.name)
            changed = True
    return sorted(locked)
