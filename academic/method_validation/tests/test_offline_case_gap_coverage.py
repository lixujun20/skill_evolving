from __future__ import annotations

from academic.method_validation.assertions import (
    assert_failure_case_is_skill_scoped,
    assert_interface_change_updates_bundle,
    assert_overmerge_split,
    assert_post_execute_maintenance_order,
    assert_refine_outcome_classification,
    assert_retrieval_audit_has_scores,
    make_bundle,
    make_case,
    make_result_for_bundle,
    make_skill,
    should_micro_refactor,
    utility_label,
)
from academic.skill_repository.store import ArtifactStore
from academic.skill_repository.types import SkillInterface


def test_exe_c03_maintenance_roles_run_after_task_completion() -> None:
    events = [
        {"role": "executor", "phase": "turn_start"},
        {"role": "retriever", "phase": "pre_turn_retrieval"},
        {"role": "executor", "phase": "task_completed"},
        {"role": "extractor", "phase": "post_task"},
        {"role": "bundle_builder", "phase": "post_task"},
        {"role": "unit_tester", "phase": "post_task"},
        {"role": "refiner", "phase": "post_task"},
    ]
    assert_post_execute_maintenance_order(events)


def test_ext_c02_artifact_enters_store_and_prompt() -> None:
    store = ArtifactStore()
    artifact = make_skill("external_policy_asset")
    store.add(artifact)
    retrieved = store.retrieve("known filenames diff", top_k=1)
    prompt = store.build_prompt(retrieved)
    assert retrieved[0].name == "external_policy_asset"
    assert "external_policy_asset" in prompt
    assert "Directly compare known files" in prompt


def test_ext_c05_dependency_is_explicitly_recorded() -> None:
    store = ArtifactStore()
    store.add(make_skill("shared_id_convention", body="Shared id propagation convention."))
    dependent = make_skill(
        "booking_update_rule",
        body="Use shared_id_convention before updating a booking.",
        dependencies=["shared_id_convention"],
    )
    store.add(dependent)
    current = store.get("booking_update_rule")
    assert current is not None
    assert current.dependencies == ["shared_id_convention"]


def test_bun_c04_bundle_version_can_change_without_skill_version_change() -> None:
    artifact = make_skill("bundle_only_change", bundle=make_bundle("bundle_only_change", n_positive=1))
    old_skill_version = artifact.version
    old_bundle_version = artifact.bundle.bundle_version
    artifact.bundle.integration_cases.append(make_case("bundle_only_change:integration:0", polarity="integration"))
    artifact.bundle.bundle_version += 1
    assert artifact.version == old_skill_version
    assert artifact.bundle.bundle_version == old_bundle_version + 1


def test_bun_c05_interface_change_without_bundle_update_is_rejected() -> None:
    parent = make_skill("interface_change_guard", bundle=make_bundle("interface_change_guard", n_positive=1))
    child = make_skill("interface_change_guard", bundle=parent.bundle)
    child.interface = SkillInterface(
        summary="Different contract",
        usage="Use only for a different tool.",
        input_contract={"requires": ["booking_id"]},
        output_contract={"ensures": ["booking update"]},
        invocation_contract={"type": "prompt_rule", "tool": "update_booking"},
    )
    try:
        assert_interface_change_updates_bundle(parent, child)
    except AssertionError as exc:
        assert "interface changed" in str(exc)
    else:
        raise AssertionError("interface change without bundle update should fail")


def test_tst_c03_negative_skill_detection_includes_cost_regression() -> None:
    label = utility_label(
        without_validity=True,
        with_validity=True,
        without_tokens=80,
        with_tokens=200,
        without_steps=1,
        with_steps=4,
    )
    assert label == "harmful"


def test_ref_c02_disable_is_fallback_not_semantic_repair() -> None:
    assert_refine_outcome_classification({"action": "disable", "recovery_category": "safety_fallback"})
    try:
        assert_refine_outcome_classification({"action": "disable", "recovery_category": "semantic_repair"})
    except AssertionError as exc:
        assert "disable" in str(exc)
    else:
        raise AssertionError("disable must not be counted as semantic repair")


def test_rfa_c05_overmerge_split_preserves_cases() -> None:
    original_bundle = make_bundle("overwide", n_positive=2, n_negative=1)
    split_a = make_skill("overwide_file_rule", bundle=make_bundle("overwide_file_rule", n_positive=0))
    split_b = make_skill("overwide_booking_rule", bundle=make_bundle("overwide_booking_rule", n_positive=0))
    split_a.bundle.positive_cases = [original_bundle.positive_cases[0]]
    split_b.bundle.positive_cases = [original_bundle.positive_cases[1]]
    split_b.bundle.negative_cases = list(original_bundle.negative_cases)
    assert_overmerge_split([split_a, split_b], original_bundle)


def test_int_c03d_negative_transfer_is_classified_harmful() -> None:
    harmful = make_skill("wrong_contract")
    result = make_result_for_bundle(
        harmful,
        without_validity=True,
        with_validity=False,
        without_tokens=100,
        with_tokens=120,
        result_id="negative-transfer",
    )
    assert result.counterfactual["utility_label"] == "harmful"


def test_int_c03f_k_step_scan_requires_repeated_evidence() -> None:
    assert should_micro_refactor(step=6, k=3, repeated_evidence_count=2)
    assert not should_micro_refactor(step=6, k=3, repeated_evidence_count=0)


def test_log_c02_retrieval_audit_has_repo_summary_scores_and_selection() -> None:
    store = ArtifactStore([make_skill("audited_retrieval")])
    audit = store.retrieve_audit("known filenames diff", top_k=1, debug_context={"case_id": "LOG-C02"})
    assert_retrieval_audit_has_scores(audit)
    assert audit["context"]["case_id"] == "LOG-C02"


def test_int_c03c_failure_case_scope_excludes_unrelated_tools() -> None:
    case = make_case("failure_scope:integration:0", polarity="integration")
    case.context["focus_tools"] = ["diff"]
    assert_failure_case_is_skill_scoped(case, expected_tool="diff", forbidden_tool="ls")

