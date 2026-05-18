from __future__ import annotations

import pytest

from academic.benchmarks.core.runner import _run_skillsbench_baseline
from academic.benchmarks.skillsbench.adapter import (
    default_skillsbench_skill_store,
    load_skillsbench_fixture_skill_artifacts,
    load_skillsbench_skill_artifacts,
    load_skillsbench_tasks,
    run_skillsbench_fixture_retrieval_diagnostic,
    run_skillsbench_task,
)


def test_skillsbench_curated_pool_loads_as_skill_artifacts() -> None:
    artifacts = load_skillsbench_skill_artifacts(pool="curated", limit=3)

    assert len(artifacts) == 3
    assert artifacts[0].name.startswith("skillsbench_")
    assert artifacts[0].metadata["skill_pool"] == "curated"
    assert artifacts[0].interface.invocation_contract["injection_type"] == "informational"


def test_skillsbench_fixture_retrieval_diagnostic_is_labeled_non_official() -> None:
    report = run_skillsbench_fixture_retrieval_diagnostic(
        pool="curated",
        max_tasks=3,
        top_k=2,
        skill_limit=25,
    )

    assert report["diagnostic_only"] is True
    assert report["official_pass_rate"] is None
    assert report["n_tasks"] == 3
    assert len(report["per_task"]) == 3


def test_skillsbench_fixture_tasks_and_oracle_skills_load() -> None:
    tasks = load_skillsbench_tasks(source="fixture", limit=2)
    artifacts = load_skillsbench_fixture_skill_artifacts(max_tasks=2)

    assert [task.task_id for task in tasks] == ["citation-check", "data-to-d3"]
    assert artifacts[0].metadata["source_task_ids"] == ["citation-check"]
    assert artifacts[0].metadata["skill_pool"] == "fixture_oracle"


@pytest.mark.asyncio
async def test_skillsbench_mock_baseline_selects_fixture_oracle_skill() -> None:
    tasks = load_skillsbench_tasks(source="fixture", limit=3)
    store = default_skillsbench_skill_store(pool="fixture", limit=3)

    result = await run_skillsbench_task(
        tasks[0],
        llm_config="mock",
        model_name="mock-model",
        artifact_store=store,
        top_k_skills=2,
        skill_injector_mode="compact",
    )

    assert result.success is True
    assert result.metrics["diagnostic_only"] is True
    assert result.metrics["official_pass_rate"] is None
    assert result.metrics["retrieval_hit_at_k"] is True
    assert result.metrics["selection_hit"] is True
    assert "skillsbench_fixture_citation_check" in result.metrics["selected_skill_names"]


@pytest.mark.asyncio
async def test_skillsbench_baseline_concurrency_preserves_order() -> None:
    tasks = load_skillsbench_tasks(source="fixture", limit=4)
    store = default_skillsbench_skill_store(pool="fixture", limit=4)

    details = await _run_skillsbench_baseline(
        tasks,
        n_runs=1,
        llm_config="mock",
        model_name="mock-model",
        store=store,
        concurrency=3,
        top_k_skills=2,
        skill_injector_mode="compact",
    )

    assert [item["task_id"] for item in details] == [task.task_id for task in tasks]
    assert all(item["n_success"] == 1 for item in details)
