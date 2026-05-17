"""BFCL adapter for the benchmark-agnostic online evolution runner.

This adapter intentionally preserves BFCL's native executor and verifier.  The
full BFCL-related algorithm still lives in ``related.experiment`` while the
generic runner is introduced; this class is the compatibility boundary for
moving that loop into core without changing BFCL task semantics.
"""
from __future__ import annotations

from typing import Any, Dict, List

from academic.benchmarks.bfcl import run_bfcl_task
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig, NoOpMaintenanceAdapter
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask


class BFCLMaintenanceAdapter(NoOpMaintenanceAdapter):
    benchmark = "bfcl_v3"

    def __init__(
        self,
        *,
        tools: List[Dict[str, Any]],
        adapter_mode: str = "official",
        execution_backend: str = "auto",
        prompt_style: str = "native",
        tool_api_style: str = "auto",
        min_skill_score: float = 0.0,
        skill_injection_mode: str = "prompt_only",
        max_steps_per_turn: int = 20,
        temperature: float | None = None,
        synthetic_continue: bool = False,
        explicit_skill_tool: bool = False,
    ) -> None:
        self.tools = tools
        self.adapter_mode = adapter_mode
        self.execution_backend = execution_backend
        self.prompt_style = prompt_style
        self.tool_api_style = tool_api_style
        self.min_skill_score = min_skill_score
        self.skill_injection_mode = skill_injection_mode
        self.max_steps_per_turn = max_steps_per_turn
        self.temperature = temperature
        self.synthetic_continue = synthetic_continue
        self.explicit_skill_tool = explicit_skill_tool

    async def run_task(
        self,
        task: BenchmarkTask,
        *,
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        phase: str,
        task_index: int,
        run_idx: int,
    ) -> BenchmarkResult:
        del phase, task_index, run_idx
        return await run_bfcl_task(
            task,
            llm_config=config.llm_config,
            tools=self.tools,
            artifact_store=store,
            adapter_mode=self.adapter_mode,
            model_name=config.model_name,
            enable_skill_tool=self.explicit_skill_tool and bool(store.all()),
            execution_backend=self.execution_backend,
            prompt_style=self.prompt_style,
            tool_api_style=self.tool_api_style,
            top_k_skills=config.top_k_skills,
            min_skill_score=self.min_skill_score,
            max_steps_per_turn=self.max_steps_per_turn,
            skill_injection_mode=self.skill_injection_mode,
            temperature=self.temperature,
            synthetic_continue=self.synthetic_continue,
            max_task_seconds=config.max_task_seconds,
        )
