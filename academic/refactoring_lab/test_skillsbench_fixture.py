from academic.refactoring_lab.skillsbench_fixture import (
    load_skillsbench_fixture,
    summarize_skillsbench_fixture,
)


def test_skillsbench_fixture_summary_matches_expected_counts() -> None:
    summary = summarize_skillsbench_fixture()

    assert summary["n_tasks"] == 24
    assert summary["difficulty_counts"] == {"easy": 2, "hard": 7, "medium": 15}
    assert summary["categories_with_multiple_tasks"] == {
        "astronomy": 2,
        "control-systems": 2,
        "energy": 2,
        "security": 2,
        "seismology": 2,
    }


def test_skillsbench_fixture_loader_preserves_selected_order() -> None:
    tasks = load_skillsbench_fixture(max_tasks=3)

    assert [task.task_id for task in tasks] == [
        "citation-check",
        "data-to-d3",
        "edit-pdf",
    ]
