import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, Dict, List

from academic.benchmarks.bfcl.maintenance.generic_adapter import BFCLMaintenanceAdapter
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.evolution import OnlineSkillEvolutionRunner
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig, NoOpMaintenanceAdapter
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact
from academic.benchmarks.spreadsheet.adapter import SpreadsheetMaintenanceAdapter


class FakeAdapter(NoOpMaintenanceAdapter):
    benchmark = "fake_bench"

    def __init__(self) -> None:
        self.events: List[str] = []
        self.active_train = 0
        self.max_active_train = 0
        self.active_test = 0
        self.max_active_test = 0

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
        del store, config, run_idx
        is_test = "heldout_test" in phase
        if is_test:
            self.active_test += 1
            self.max_active_test = max(self.max_active_test, self.active_test)
        else:
            self.active_train += 1
            self.max_active_train = max(self.max_active_train, self.active_train)
        await asyncio.sleep(0.01)
        if is_test:
            self.active_test -= 1
        else:
            self.active_train -= 1
        self.events.append(f"{phase}:{task_index}:{task.task_id}")
        return BenchmarkResult(
            benchmark=self.benchmark,
            task_id=task.task_id,
            success=True,
            score=float(task_index),
            metrics={"total_tokens": 1},
            trace={"phase": phase},
        )

    async def assign_credit(self, **kwargs: Any) -> List[Dict[str, Any]]:
        detail = kwargs["detail"]
        return [{"task_id": detail["task_id"], "skill_name": "s", "judgment": "neutral"}]

    async def run_micro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
        detail = kwargs["detail"]
        return {"phase": "micro", "task_id": detail["task_id"], "maintenance_targets": []}

    async def run_macro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
        return {
            "phase": "macro_final" if kwargs.get("final_window") else "macro",
            "window_index": kwargs["window_index"],
            "task_ids": [item["task_id"] for item in kwargs["window_details"]],
            "maintenance_targets": [],
        }


class ConcurrentFakeAdapter(FakeAdapter):
    def __init__(self, skill_by_task: Dict[str, str]) -> None:
        super().__init__()
        self.skill_by_task = skill_by_task
        self.active_micro_by_skill: Dict[str, int] = {}
        self.max_micro_by_skill: Dict[str, int] = {}
        self.active_micro_total = 0
        self.max_active_micro_total = 0
        self.macro_saw_after_window = True
        self.overlapping_pairs: List[tuple[str, List[str]]] = []

    async def assign_credit(self, **kwargs: Any) -> List[Dict[str, Any]]:
        detail = kwargs["detail"]
        skill_name = self.skill_by_task[detail["task_id"]]
        return [{"task_id": detail["task_id"], "skill_name": skill_name, "judgment": "harmful"}]

    def maintenance_lock_names(self, **kwargs: Any) -> List[str]:
        detail = kwargs["detail"]
        return [self.skill_by_task[detail["task_id"]]]

    async def run_micro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
        detail = kwargs["detail"]
        skill_name = self.skill_by_task[detail["task_id"]]
        active = [name for name, count in self.active_micro_by_skill.items() if count > 0]
        if active:
            self.overlapping_pairs.append((skill_name, active))
        self.active_micro_by_skill[skill_name] = self.active_micro_by_skill.get(skill_name, 0) + 1
        self.max_micro_by_skill[skill_name] = max(
            self.max_micro_by_skill.get(skill_name, 0),
            self.active_micro_by_skill[skill_name],
        )
        self.active_micro_total += 1
        self.max_active_micro_total = max(self.max_active_micro_total, self.active_micro_total)
        await asyncio.sleep(0.01)
        self.active_micro_total -= 1
        self.active_micro_by_skill[skill_name] -= 1
        return {"phase": "micro", "task_id": detail["task_id"], "maintenance_targets": [skill_name]}

    async def run_macro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
        window_ids = [item["task_id"] for item in kwargs["window_details"]]
        seen_train_ids = [item["task_id"] for item in kwargs["all_train_details"]]
        if any(task_id not in seen_train_ids for task_id in window_ids):
            self.macro_saw_after_window = False
        return await super().run_macro_maintenance(**kwargs)


async def test_generic_online_runner_keeps_train_serial_and_heldout_parallel_order() -> None:
    adapter = FakeAdapter()
    runner = OnlineSkillEvolutionRunner(
        adapter=adapter,
        config=MaintenanceRunConfig(
            llm_config="fake",
            tag="unit",
            n_train_runs=1,
            n_test_runs=1,
            micro_maintenance_step=1,
            macro_maintenance_step=2,
            test_concurrency=2,
        ),
    )
    train = [SimpleNamespace(task_id=f"train_{idx}", benchmark="fake_bench") for idx in range(3)]
    test = [SimpleNamespace(task_id=f"test_{idx}", benchmark="fake_bench") for idx in range(4)]

    summary = await runner.run(train_tasks=train, test_tasks=test, seed_store=ArtifactStore())

    assert adapter.max_active_train == 1
    assert adapter.max_active_test == 2
    assert [item["task_id"] for item in summary["test_details"]] == [f"test_{idx}" for idx in range(4)]
    assert len(summary["skill_credit_events"]) == 3
    assert len(summary["micro_maintenance_reports"]) == 3
    assert [window["task_ids"] for window in summary["maintenance_windows"]] == [
        ["train_0", "train_1"],
        ["train_2"],
    ]


async def test_generic_online_runner_parallelizes_train_window_and_locks_micro_by_skill() -> None:
    skill_by_task = {
        "train_0": "skill_a",
        "train_1": "skill_a",
        "train_2": "skill_b",
        "train_3": "skill_c",
    }
    adapter = ConcurrentFakeAdapter(skill_by_task)
    runner = OnlineSkillEvolutionRunner(
        adapter=adapter,
        config=MaintenanceRunConfig(
            llm_config="fake",
            tag="parallel",
            n_train_runs=1,
            n_test_runs=1,
            micro_maintenance_step=1,
            macro_maintenance_step=4,
            train_concurrency=4,
            micro_maintenance_concurrency=4,
            test_concurrency=1,
        ),
    )
    train = [SimpleNamespace(task_id=f"train_{idx}", benchmark="fake_bench") for idx in range(4)]

    summary = await runner.run(train_tasks=train, test_tasks=[], seed_store=ArtifactStore())

    assert adapter.max_active_train > 1
    assert adapter.max_active_micro_total > 1
    assert adapter.max_micro_by_skill["skill_a"] == 1
    assert adapter.macro_saw_after_window is True
    assert [item["task_id"] for item in summary["train_details"]] == [f"train_{idx}" for idx in range(4)]
    assert [window["task_ids"] for window in summary["maintenance_windows"]] == [
        ["train_0", "train_1", "train_2", "train_3"],
    ]


async def test_generic_online_runner_locks_dependency_neighborhood() -> None:
    skill_by_task = {
        "train_0": "skill_base",
        "train_1": "skill_child",
        "train_2": "skill_other",
    }
    adapter = ConcurrentFakeAdapter(skill_by_task)
    runner = OnlineSkillEvolutionRunner(
        adapter=adapter,
        config=MaintenanceRunConfig(
            llm_config="fake",
            micro_maintenance_step=1,
            macro_maintenance_step=3,
            train_concurrency=3,
            micro_maintenance_concurrency=3,
        ),
    )
    train = [SimpleNamespace(task_id=f"train_{idx}", benchmark="fake_bench") for idx in range(3)]
    store = ArtifactStore(
        [
            SkillArtifact(name="skill_base", kind="workflow", description="base", body="base"),
            SkillArtifact(name="skill_child", kind="workflow", description="child", body="uses skill_base"),
            SkillArtifact(name="skill_other", kind="workflow", description="other", body="independent"),
        ]
    )

    await runner.run(train_tasks=train, test_tasks=[], seed_store=store)

    assert any(
        skill == "skill_other" or "skill_other" in active
        for skill, active in adapter.overlapping_pairs
    )
    assert not any(
        (skill == "skill_base" and "skill_child" in active)
        or (skill == "skill_child" and "skill_base" in active)
        for skill, active in adapter.overlapping_pairs
    )


async def test_generic_micro_refine_parallel_wall_clock_speedup_for_independent_skills() -> None:
    n_tasks = 12
    sleep_s = 0.05
    skill_by_task = {f"train_{idx}": f"skill_{idx}" for idx in range(n_tasks)}
    details = [
        {
            "task_id": f"train_{idx}",
            "task_index": idx,
            "task": {"task_id": f"train_{idx}"},
            "runs": [],
        }
        for idx in range(n_tasks)
    ]

    class SlowRefineAdapter(ConcurrentFakeAdapter):
        async def run_micro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
            detail = kwargs["detail"]
            skill_name = self.skill_by_task[detail["task_id"]]
            self.active_micro_total += 1
            self.max_active_micro_total = max(self.max_active_micro_total, self.active_micro_total)
            await asyncio.sleep(sleep_s)
            self.active_micro_total -= 1
            return {"phase": "micro", "task_id": detail["task_id"], "maintenance_targets": [skill_name]}

    async def timed_run(concurrency: int) -> tuple[float, SlowRefineAdapter, List[Dict[str, Any]]]:
        adapter = SlowRefineAdapter(skill_by_task)
        runner = OnlineSkillEvolutionRunner(
            adapter=adapter,
            config=MaintenanceRunConfig(
                llm_config="fake",
                micro_maintenance_step=1,
                micro_maintenance_concurrency=concurrency,
            ),
        )
        start = time.perf_counter()
        reports = await runner._run_window_maintenance(
            window_details=details,
            store=ArtifactStore(),
            round_index=0,
            micro_step=1,
        )
        return time.perf_counter() - start, adapter, reports

    serial_s, serial_adapter, serial_reports = await timed_run(1)
    parallel_s, parallel_adapter, parallel_reports = await timed_run(6)

    assert len(serial_reports) == n_tasks
    assert len(parallel_reports) == n_tasks
    assert serial_adapter.max_active_micro_total == 1
    assert parallel_adapter.max_active_micro_total > 1
    assert parallel_s < serial_s * 0.6


async def test_generic_online_runner_converts_task_timeout_to_failed_detail() -> None:
    class TimeoutAdapter(FakeAdapter):
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
            del task, store, config, phase, run_idx
            if task_index == 0:
                await asyncio.sleep(0.05)
            return BenchmarkResult(
                benchmark=self.benchmark,
                task_id=f"train_{task_index}",
                success=True,
                score=1.0,
                metrics={},
                trace={},
            )

    runner = OnlineSkillEvolutionRunner(
        adapter=TimeoutAdapter(),
        config=MaintenanceRunConfig(
            llm_config="fake",
            max_task_seconds=0.01,
            train_concurrency=2,
            micro_maintenance_concurrency=2,
            macro_maintenance_step=2,
        ),
    )
    train = [SimpleNamespace(task_id=f"train_{idx}", benchmark="fake_bench") for idx in range(2)]

    summary = await runner.run(train_tasks=train, test_tasks=[], seed_store=ArtifactStore())

    assert len(summary["train_details"]) == 2
    first_run = summary["train_details"][0]["runs"][0]
    second_run = summary["train_details"][1]["runs"][0]
    assert first_run["success"] is False
    assert first_run["metrics"]["exception"] == "task_timeout"
    assert second_run["success"] is True


async def test_generic_online_runner_partial_resume_skips_completed_tasks(tmp_path) -> None:
    partial_path = tmp_path / "partial.json"
    adapter = FakeAdapter()
    runner = OnlineSkillEvolutionRunner(
        adapter=adapter,
        config=MaintenanceRunConfig(
            llm_config="fake",
            tag="resume",
            train_concurrency=2,
            micro_maintenance_concurrency=2,
            macro_maintenance_step=3,
            extra={"partial_output": str(partial_path)},
        ),
    )
    train = [SimpleNamespace(task_id=f"train_{idx}", benchmark="fake_bench") for idx in range(3)]
    first_detail = await runner._run_task_repeats(
        train[0],
        store=ArtifactStore(),
        phase="fake_bench_train_round_0",
        task_index=0,
        n_runs=1,
    )
    adapter.events.clear()
    partial_path.write_text(
        json.dumps(
            {
                "skills": [],
                "train_details": [first_detail],
                "test_details": [],
                "skill_credit_events": [],
                "micro_maintenance_reports": [],
                "maintenance_windows": [],
                "macro_skill_snapshots": [],
            },
            ensure_ascii=False,
        )
    )

    summary = await runner.run(train_tasks=train, test_tasks=[], seed_store=ArtifactStore())
    rerun_train_zero = [event for event in adapter.events if event.endswith(":0:train_0")]
    payload = json.loads(partial_path.read_text())

    assert rerun_train_zero == []
    assert [item["task_id"] for item in summary["train_details"]] == ["train_0", "train_1", "train_2"]
    assert len(payload["task_state"]["train"]) == 3
    assert payload["completed"] is True


async def test_spreadsheet_adapter_uses_generic_run_task_contract(monkeypatch) -> None:
    calls: List[Dict[str, Any]] = []

    async def fake_run_spreadsheet_task(task: BenchmarkTask, **kwargs: Any) -> BenchmarkResult:
        calls.append({"task": task, "kwargs": kwargs})
        return BenchmarkResult(
            benchmark="spreadsheet",
            task_id=task.task_id,
            success=True,
            score=1.0,
            metrics={"llm_api_style": "anthropic_direct"},
            trace={},
        )

    monkeypatch.setattr(
        "academic.benchmarks.spreadsheet.adapter.run_spreadsheet_task",
        fake_run_spreadsheet_task,
    )
    task = BenchmarkTask(benchmark="spreadsheet", task_id="sheet_1", question="q")
    result = await SpreadsheetMaintenanceAdapter().run_task(
        task,
        store=ArtifactStore(),
        config=MaintenanceRunConfig(llm_config="local_claude_proxy", model_name="claude-sonnet-4-5", top_k_skills=3),
        phase="spreadsheet_train",
        task_index=0,
        run_idx=0,
    )

    assert result.success is True
    assert calls[0]["kwargs"]["llm_config"] == "local_claude_proxy"
    assert calls[0]["kwargs"]["model_name"] == "claude-sonnet-4-5"
    assert calls[0]["kwargs"]["top_k_skills"] == 3


async def test_bfcl_generic_adapter_preserves_executor_arguments(monkeypatch) -> None:
    calls: List[Dict[str, Any]] = []

    async def fake_run_bfcl_task(task: BenchmarkTask, **kwargs: Any) -> BenchmarkResult:
        calls.append({"task": task, "kwargs": kwargs})
        return BenchmarkResult(
            benchmark="bfcl_v3",
            task_id=task.task_id,
            success=True,
            score=1.0,
            metrics={},
            trace={},
        )

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.generic_adapter.run_bfcl_task",
        fake_run_bfcl_task,
    )
    adapter = BFCLMaintenanceAdapter(
        tools=[{"function": {"name": "lookup"}}],
        adapter_mode="official",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="anthropic_direct",
        skill_injection_mode="prompt_only",
        max_steps_per_turn=7,
    )
    task = BenchmarkTask(benchmark="bfcl_v3", task_id="bfcl_1", question=[])
    await adapter.run_task(
        task,
        store=ArtifactStore(),
        config=MaintenanceRunConfig(llm_config="local_claude_proxy", model_name="claude-sonnet-4-5", top_k_skills=2),
        phase="bfcl_train",
        task_index=0,
        run_idx=0,
    )

    kwargs = calls[0]["kwargs"]
    assert kwargs["llm_config"] == "local_claude_proxy"
    assert kwargs["model_name"] == "claude-sonnet-4-5"
    assert kwargs["tools"][0]["function"]["name"] == "lookup"
    assert kwargs["top_k_skills"] == 2
    assert kwargs["max_steps_per_turn"] == 7
    assert kwargs["tool_api_style"] == "anthropic_direct"
