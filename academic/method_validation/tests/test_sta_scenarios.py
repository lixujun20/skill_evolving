from __future__ import annotations

from academic.method_validation.sta_scenarios import (
    run_sta_c01_upstream_marks_downstream_stale,
    run_sta_c02_lazy_resolver_pin_legacy,
    run_sta_c03_pinned_dependency_recorded_in_test_result,
)


def test_sta_c01_scenario_report_upstream_update_marks_downstream_stale() -> None:
    report = run_sta_c01_upstream_marks_downstream_stale()
    assert report.case_id == "STA-C01"
    assert report.passed, report.observed
    assert report.setup
    assert report.action
    assert report.expected
    assert report.observed["downstream_stale"] is True
    assert report.observed["stale_due_to"]["dependency"] == "id_contract"


def test_sta_c02_scenario_report_lazy_resolver_pins_legacy_before_use() -> None:
    report = run_sta_c02_lazy_resolver_pin_legacy()
    assert report.case_id == "STA-C02"
    assert report.passed, report.observed
    assert report.observed["retrieved_stale_candidate"] is True
    assert report.observed["resolver_action"] == "pin_legacy"
    assert report.observed["dependency_pins"][0]["pinned_version"] == 1


def test_sta_c03_scenario_report_test_result_records_dependency_snapshot() -> None:
    report, result = run_sta_c03_pinned_dependency_recorded_in_test_result()
    assert report.case_id == "STA-C03"
    assert report.passed, report.observed
    assert result.dependency_versions == {"id_contract": 1}
    assert result.counterfactual["utility_label"] == "work"
    assert result.unit_case_runs

