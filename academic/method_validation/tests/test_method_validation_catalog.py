from __future__ import annotations

import re
from pathlib import Path

from academic.method_validation.catalog import CASES, CASE_BY_ID, requires_real_llm_for_full_pass


PLAN_PATH = Path(__file__).resolve().parents[2] / "skill_repository" / "METHOD_VALIDATION_TEST_PLAN.md"


def test_catalog_has_unique_cases() -> None:
    assert len(CASES) == len(CASE_BY_ID)
    assert len(CASES) >= 55


def test_catalog_cases_are_traceable_to_plan() -> None:
    plan = PLAN_PATH.read_text()
    for case in CASES:
        assert case.case_id in plan
        assert case.feature_id in plan
        assert case.paper_point_id in plan
        assert case.suite in {"core_algorithm", "contract_regression", "infrastructure_check"}
        assert case.mode in {"offline", "llm_smoke", "llm_full", "offline+llm_smoke", "offline+llm_full"}
        assert case.status in {"implemented_offline", "requires_llm", "planned"}


def test_all_plan_case_rows_are_in_catalog() -> None:
    plan = PLAN_PATH.read_text()
    case_ids = set(re.findall(r"`([A-Z]{3}-C\d+[A-Z]?)`", plan))
    missing = sorted(case_ids - set(CASE_BY_ID))
    assert not missing


def test_paper_points_have_catalog_coverage() -> None:
    points = {case.paper_point_id for case in CASES}
    expected = {f"P{idx:02d}" for idx in range(1, 44)}
    observed_prefixes = {point.split("_", 1)[0] for point in points}
    assert expected <= observed_prefixes


def test_llm_cases_keep_full_pass_requirement_visible() -> None:
    for case in CASES:
        if case.mode in {"llm_smoke", "llm_full"}:
            assert requires_real_llm_for_full_pass(case)


def test_offline_implemented_cases_can_still_require_llm_for_full_pass() -> None:
    partial_cases = [
        case for case in CASES if case.status == "implemented_offline" and case.requires_llm
    ]
    assert partial_cases, "catalog should distinguish offline coverage from full real-LLM pass"


def test_implemented_offline_cases_have_named_pytest_coverage() -> None:
    tests_dir = Path(__file__).resolve().parent
    test_text = "\n".join(path.read_text() for path in tests_dir.glob("test_*.py"))
    missing = []
    for case in CASES:
        if case.status != "implemented_offline":
            continue
        function_token = case.case_id.lower().replace("-", "_")
        if function_token not in test_text:
            missing.append(case.case_id)
    assert not missing, f"implemented_offline cases lack named pytest coverage: {missing}"
