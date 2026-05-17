import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

from academic.benchmarks.bfcl.maintenance.generic_adapter import BFCLMaintenanceAdapter
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.evolution import OnlineSkillEvolutionRunner
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig, NoOpMaintenanceAdapter
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask
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
