from __future__ import annotations

from academic.benchmarks.skillsbench.adapter import (
    load_skillsbench_skill_artifacts,
    run_skillsbench_fixture_retrieval_diagnostic,
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
