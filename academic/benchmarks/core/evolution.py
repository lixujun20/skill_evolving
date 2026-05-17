"""Benchmark-agnostic online skill evolution scheduler."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.maintenance_adapter import (
    BenchmarkMaintenanceAdapter,
    MaintenanceRunConfig,
    aggregate_run_details,
    make_task_run_bundle,
)


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
        store = self._copy_store(seed_store or ArtifactStore())
        train_details: List[Dict[str, Any]] = []
        test_details: List[Dict[str, Any]] = []
        skill_credit_events: List[Dict[str, Any]] = []
        micro_reports: List[Dict[str, Any]] = []
        maintenance_windows: List[Dict[str, Any]] = []

        effective_rounds = max(1, int(rounds or 1))
        micro_step = max(1, int(self.config.micro_maintenance_step or 1))
        macro_step = max(1, int(self.config.macro_maintenance_step or 10))

        for round_index in range(effective_rounds):
            window_details: List[Dict[str, Any]] = []
            for task_index, task in enumerate(train_tasks):
                detail = await self._run_task_repeats(
                    task,
                    store=store,
                    phase=f"{self.adapter.benchmark}_train_round_{round_index}",
                    task_index=task_index,
                    n_runs=max(1, int(self.config.n_train_runs or 1)),
                )
                train_details.append(detail)
                window_details.append(detail)

                task_credit = await self.adapter.assign_credit(
                    detail=detail,
                    store=store,
                    config=self.config,
                    round_index=round_index,
                    task_index=task_index,
                )
                skill_credit_events.extend(copy.deepcopy(task_credit))
                credit_bundle_cases = await self.adapter.apply_credit_bundle_cases(
                    detail=detail,
                    credit_events=task_credit,
                    store=store,
                    config=self.config,
                    round_index=round_index,
                    task_index=task_index,
                )

                if (task_index + 1) % micro_step == 0:
                    micro_reports.append(
                        await self.adapter.run_micro_maintenance(
                            detail=detail,
                            credit_events=task_credit,
                            credit_bundle_cases=credit_bundle_cases,
                            store=store,
                            config=self.config,
                            round_index=round_index,
                            task_index=task_index,
                        )
                    )
                else:
                    micro_reports.append(
                        {
                            "phase": "micro",
                            "task_id": detail.get("task_id"),
                            "task_index": task_index,
                            "maintenance_targets": [],
                            "maintenance_test_results": [],
                            "refine_decisions": [],
                            "credit_bundle_cases": copy.deepcopy(credit_bundle_cases),
                            "reason": "micro_step_not_reached",
                        }
                    )

                if (task_index + 1) % macro_step == 0:
                    maintenance_windows.append(
                        await self.adapter.run_macro_maintenance(
                            window_details=window_details,
                            all_train_details=train_details,
                            credit_events=skill_credit_events,
                            store=store,
                            config=self.config,
                            round_index=round_index,
                            window_index=len(maintenance_windows),
                            final_window=False,
                        )
                    )
                    window_details = []

            if window_details:
                maintenance_windows.append(
                    await self.adapter.run_macro_maintenance(
                        window_details=window_details,
                        all_train_details=train_details,
                        credit_events=skill_credit_events,
                        store=store,
                        config=self.config,
                        round_index=round_index,
                        window_index=len(maintenance_windows),
                        final_window=True,
                    )
                )

        test_details = await self._run_test_tasks(test_tasks, store=store)
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
        return {
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
            "store_snapshot": self.adapter.store_snapshot(store),
            "skills": [artifact.as_dict() for artifact in store.all()],
        }

    async def _run_test_tasks(
        self,
        tasks: Sequence[Any],
        *,
        store: ArtifactStore,
    ) -> List[Dict[str, Any]]:
        concurrency = max(1, int(self.config.test_concurrency or 1))
        if concurrency <= 1 or len(tasks) <= 1:
            return [
                await self._run_task_repeats(
                    task,
                    store=store,
                    phase=f"{self.adapter.benchmark}_heldout_test",
                    task_index=task_index,
                    n_runs=max(1, int(self.config.n_test_runs or 1)),
                )
                for task_index, task in enumerate(tasks)
            ]

        sem = asyncio.Semaphore(concurrency)

        async def guarded(task: Any, task_index: int) -> tuple[int, Dict[str, Any]]:
            task_store = self._copy_store(store)
            async with sem:
                return (
                    task_index,
                    await self._run_task_repeats(
                        task,
                        store=task_store,
                        phase=f"{self.adapter.benchmark}_heldout_test",
                        task_index=task_index,
                        n_runs=max(1, int(self.config.n_test_runs or 1)),
                    ),
                )

        completed = await asyncio.gather(
            *[guarded(task, task_index) for task_index, task in enumerate(tasks)]
        )
        return [detail for _, detail in sorted(completed, key=lambda item: item[0])]

    async def _run_task_repeats(
        self,
        task: Any,
        *,
        store: ArtifactStore,
        phase: str,
        task_index: int,
        n_runs: int,
    ) -> Dict[str, Any]:
        runs: List[Dict[str, Any]] = []
        for run_idx in range(n_runs):
            coro = self.adapter.run_task(
                task,
                store=store,
                config=self.config,
                phase=phase,
                task_index=task_index,
                run_idx=run_idx,
            )
            if self.config.max_task_seconds:
                result = await asyncio.wait_for(coro, timeout=self.config.max_task_seconds)
            else:
                result = await coro
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
        return make_task_run_bundle(
            task=task,
            runs=runs,
            benchmark=self.adapter.benchmark,
        )

    @staticmethod
    def _copy_store(store: ArtifactStore) -> ArtifactStore:
        return ArtifactStore(
            [copy.deepcopy(artifact) for artifact in store.all()],
            test_results=[copy.deepcopy(result) for result in store.test_results()],
        )
