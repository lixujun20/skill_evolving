from __future__ import annotations

from academic.method_validation.assertions import (
    assert_bundle_has_no_run_metrics,
    assert_full_bundle_coverage,
    assert_interface_complete,
    assert_minor_keeps_old_cases,
    assert_result_snapshot,
    assert_work_label,
    make_bundle,
    make_result_for_bundle,
    make_skill,
    utility_label,
)


def test_tst_c01_utility_work_requires_correctness_not_down_and_cost_down() -> None:
    label = utility_label(
        without_validity=True,
        with_validity=True,
        without_tokens=120,
        with_tokens=80,
        without_steps=2,
        with_steps=1,
    )
    assert_work_label(label)


def test_tst_c01_correctness_gain_is_not_work_when_cost_increases() -> None:
    label = utility_label(
        without_validity=False,
        with_validity=True,
        without_tokens=80,
        with_tokens=120,
        without_steps=1,
        with_steps=2,
    )
    assert label == "correctness_gain"


def test_tst_c01_neutral_and_harmful_labels() -> None:
    assert (
        utility_label(
            without_validity=True,
            with_validity=True,
            without_tokens=100,
            with_tokens=100,
        )
        == "neutral"
    )
    assert (
        utility_label(
            without_validity=True,
            with_validity=False,
            without_tokens=100,
            with_tokens=80,
        )
        == "harmful"
    )
    assert (
        utility_label(
            without_validity=True,
            with_validity=True,
            without_tokens=80,
            with_tokens=120,
        )
        == "harmful"
    )


def test_tst_c02_full_bundle_coverage_requires_all_cases_and_variants() -> None:
    artifact = make_skill("direct_diff", bundle=make_bundle("direct_diff", n_positive=2, n_negative=1, n_integration=1))
    result = make_result_for_bundle(artifact)
    assert_full_bundle_coverage(result, artifact.bundle)


def test_tst_c04_replay_trace_is_present_for_each_run() -> None:
    artifact = make_skill("traceful_skill", bundle=make_bundle("traceful_skill", n_positive=2))
    result = make_result_for_bundle(artifact)
    for run in result.unit_case_runs:
        assert run.trace
        assert "turns" in run.trace


def test_bun_c03_bundle_and_result_are_separate() -> None:
    artifact = make_skill("separated_assets")
    result = make_result_for_bundle(artifact)
    assert_bundle_has_no_run_metrics(artifact.bundle)
    assert_result_snapshot(result, artifact)


def test_ext_c06_interface_contract_is_complete() -> None:
    artifact = make_skill("interface_complete")
    assert_interface_complete(artifact)


def test_ref_c04_minor_update_must_keep_old_cases() -> None:
    parent = make_skill("minor_contract", bundle=make_bundle("minor_contract", n_positive=2))
    child = make_skill("minor_contract", bundle=make_bundle("minor_contract", n_positive=1), version_kind="minor")
    try:
        assert_minor_keeps_old_cases(parent, child)
    except AssertionError as exc:
        assert "removed cases" in str(exc)
    else:
        raise AssertionError("minor update that removed tests should fail")

