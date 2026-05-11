"""Traceable method validation case catalog.

The catalog mirrors ``academic/skill_repository/METHOD_VALIDATION_TEST_PLAN.md``.
It is intentionally data-first: every case has a stable ID, paper point, role,
suite, mode, and implementation status. Real-LLM cases remain explicit in the
catalog even when the local offline test runner does not execute them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class MethodValidationCase:
    case_id: str
    feature_id: str
    paper_point_id: str
    primary_role: str
    suite: str
    mode: str
    query: str
    context: str
    expected_behavior: str
    assertions: List[str] = field(default_factory=list)
    secondary_roles: List[str] = field(default_factory=list)
    requires_llm: bool = False
    cadence: str = "pre_experiment"
    status: str = "planned"  # implemented_offline / requires_llm / planned


def _case(
    case_id: str,
    feature_id: str,
    paper_point_id: str,
    primary_role: str,
    suite: str,
    mode: str,
    query: str,
    context: str,
    expected_behavior: str,
    *,
    assertions: List[str] | None = None,
    secondary_roles: List[str] | None = None,
    requires_llm: bool | None = None,
    cadence: str = "pre_experiment",
    status: str | None = None,
) -> MethodValidationCase:
    needs_llm = bool(requires_llm) if requires_llm is not None else "llm" in mode
    resolved_status = status or ("requires_llm" if needs_llm else "planned")
    return MethodValidationCase(
        case_id=case_id,
        feature_id=feature_id,
        paper_point_id=paper_point_id,
        primary_role=primary_role,
        suite=suite,
        mode=mode,
        query=query,
        context=context,
        expected_behavior=expected_behavior,
        assertions=list(assertions or []),
        secondary_roles=list(secondary_roles or []),
        requires_llm=needs_llm,
        cadence=cadence,
        status=resolved_status,
    )


def requires_real_llm_for_full_pass(case: MethodValidationCase) -> bool:
    return bool(case.requires_llm)


CASES: List[MethodValidationCase] = [
    _case("EXE-C01", "EXE-F01", "P09_pre_execution_retrieval", "executor", "core_algorithm", "llm_smoke", "Multi-turn task with a relevant rule-card skill.", "Existing skill store.", "Each turn retrieves and injects skills.", requires_llm=True),
    _case("EXE-C02", "EXE-F02", "P10_error_aware_reretrieval", "executor", "core_algorithm", "llm_smoke", "Tool error after a bad skill suggestion.", "Injected bad skill and retryable tool error.", "Executor performs error-aware retrieval before retry.", requires_llm=True),
    _case("EXE-C03", "EXE-F03", "P11_post_execution_maintenance", "executor", "contract_regression", "offline", "Mock multi-turn task.", "Event timeline with retrieval and maintenance events.", "Extraction/testing/refine only occur after task completion.", status="implemented_offline"),
    _case("EXE-C04", "EXE-F04", "P05_model_skill_use_precondition", "executor", "core_algorithm", "llm_smoke", "Direct diff task with one highly fitting skill and distractors.", "With/without executor traces.", "Highly fitting skill improves tool-call behavior; unused distractors are acceptable.", requires_llm=True),
    _case("RET-C01", "RET-F01", "P34_retrieval_not_keyword_only", "retriever", "core_algorithm", "offline+llm_smoke", "Semantic query missing intent keyword.", "Store with a semantically matching skill.", "Relevant skill is retrieved without hard keyword dependence.", status="implemented_offline"),
    _case("RET-C02", "RET-F02", "P35_retrieval_auditability", "retriever", "core_algorithm", "offline+llm_smoke", "Query with relevant and polluting candidates.", "Strong, surface-only, disabled, harmful, and forbidden skills.", "Strong candidate selected; polluting candidates excluded or ranked lower.", status="implemented_offline"),
    _case("RET-C03", "RET-F03", "P28_lazy_stale_resolution", "retriever", "contract_regression", "offline", "Query retrieves stale downstream skill.", "A depends on B; B updated.", "Retriever exposes stale state and does not resolve it.", status="implemented_offline"),
    _case("RET-C04", "RET-F04", "P41_compact_skill_budget", "retriever", "core_algorithm", "offline+llm_smoke", "Same retrieval queries before/after consolidation.", "Duplicate-heavy and compact stores.", "Duplicate candidates and prompt tokens decrease without validity loss.", status="implemented_offline"),
    _case("EXT-C01", "EXT-F01", "P08_trace_to_skill_extraction", "extractor", "contract_regression", "llm_smoke", "Successful file diff trace.", "Tool schemas and existing artifacts.", "LLM extractor emits schema-valid artifact.", requires_llm=True),
    _case("EXT-C02", "EXT-F02", "P01_external_policy_asset", "extractor", "contract_regression", "offline+llm_smoke", "Reusable trace.", "Artifact emitted by extractor.", "Artifact enters store and can be retrieved/injected.", status="implemented_offline"),
    _case("EXT-C03", "EXT-F03", "P02_skill_formats_not_only_code", "extractor", "core_algorithm", "llm_smoke", "Known filenames should be diffed directly.", "Trace requires a rule, not a function.", "Extractor produces rule/workflow card.", requires_llm=True),
    _case("EXT-C04", "EXT-F04", "P07_single_responsibility", "extractor", "core_algorithm", "llm_smoke", "Trace with multiple independent error sites.", "Call errors and expected calls.", "Extractor splits narrow skills or rejects broad skill.", requires_llm=True),
    _case("EXT-C05", "EXT-F05", "P26_dependency_graph", "extractor", "contract_regression", "offline+llm_smoke", "New skill references shared convention.", "Existing shared skill.", "Dependencies/pins are explicit.", status="implemented_offline"),
    _case("EXT-C06", "EXT-F06", "P06_forward_interface_contract", "extractor", "contract_regression", "offline+llm_smoke", "Extractor output artifact.", "Artifact interface fields.", "Interface is complete, non-template, and body-consistent.", status="implemented_offline"),
    _case("BUN-C01", "BUN-F01", "P12_skill_scoped_bundle_builder", "bundle_builder", "core_algorithm", "llm_smoke", "Full trace plus target skill.", "Source/replay results.", "Bundle builder keeps only skill-relevant fragment.", requires_llm=True),
    _case("BUN-C02", "BUN-F02", "P13_success_and_failure_attribution", "bundle_builder", "core_algorithm", "llm_smoke", "Success, broken, and integration failure traces.", "Target skill.", "Positive/negative/integration cases are separated.", requires_llm=True),
    _case("BUN-C03", "BUN-F03", "P14_bundle_result_separation", "bundle_builder", "contract_regression", "offline", "Same bundle tested twice.", "Store with test results.", "Bundle has no run metrics; results are independent.", status="implemented_offline"),
    _case("BUN-C04", "BUN-F04", "P15_bundle_version_independent", "bundle_builder", "contract_regression", "offline", "Append integration case without skill content change.", "Existing skill and bundle.", "Bundle version changes independently from skill version.", status="implemented_offline"),
    _case("BUN-C05", "BUN-F05", "P23_interface_change_requires_test_update", "bundle_builder", "contract_regression", "offline", "Interface changes but tests are not updated.", "Refine payload validator fixture.", "Flow rejects interface-test inconsistency.", status="implemented_offline"),
    _case("TST-C01", "TST-F01", "P16_unit_with_without_utility", "unit_tester", "core_algorithm", "llm_smoke", "Single-skill bundle positive case.", "With/without runs.", "Outputs correctness and cost deltas plus utility_label.", status="implemented_offline"),
    _case("TST-C02", "TST-F02", "P21_refine_until_full_bundle_pass", "unit_tester", "core_algorithm", "offline+llm_smoke", "Multi-case bundle.", "Bundle execution result.", "Formal pass covers all cases and variants.", status="implemented_offline"),
    _case("TST-C03", "TST-F03", "P17_negative_skill_detection", "unit_tester", "core_algorithm", "llm_smoke", "Injected harmful skill.", "With/without runs.", "Negative contribution is reported.", status="implemented_offline"),
    _case("TST-C04", "TST-F04", "P36_logging_role_io", "unit_tester", "infrastructure_check", "offline+llm_smoke", "Any bundle replay.", "SkillTestCaseRun trace.", "Replay trace is present for UI/debug.", status="implemented_offline"),
    _case("REF-C01", "REF-F01", "P20_semantic_refinement_llm", "refiner", "contract_regression", "llm_smoke", "Failed test result and broken skill.", "Refiner input payload.", "LLM refiner outputs schema-valid decision.", requires_llm=True),
    _case("REF-C02", "REF-F02", "P22_disable_as_fallback_only", "refiner", "core_algorithm", "llm_smoke", "Targeted broken skill.", "Repair/rollback/disable decisions.", "Disable is not counted as semantic repair.", status="implemented_offline"),
    _case("REF-C03", "REF-F03", "P03_correctness_reusability_maintainability", "refiner", "core_algorithm", "llm_full", "First repair cannot pass all cases.", "Multiple refine attempts.", "Refine continues until pass or limit.", requires_llm=True),
    _case("REF-C04", "REF-F04", "P24_minor_update_test_monotonicity", "refiner", "contract_regression", "offline", "Minor update removes old case.", "Parent and child bundles.", "Validator rejects minor test deletion.", status="implemented_offline"),
    _case("REF-C05", "REF-F05", "P25_major_update_lineage", "refiner", "contract_regression", "offline", "Major interface migration.", "Updated artifact lineage.", "Lineage and migration reason are present.", status="implemented_offline"),
    _case("STA-C01", "STA-F01", "P27_stale_propagation", "stale_resolver", "contract_regression", "offline", "A depends on B; B updates.", "ArtifactStore dependency graph.", "A is marked stale while B legacy version remains in history.", status="implemented_offline"),
    _case("STA-C02", "STA-F02", "P28_lazy_stale_resolution", "stale_resolver", "core_algorithm", "llm_smoke", "Stale A is queried.", "Upstream context.", "Resolver decides refresh/pin/keep.", requires_llm=True),
    _case("STA-C03", "STA-F03", "P29_legacy_version_pinning", "stale_resolver", "core_algorithm", "offline+llm_smoke", "B major breaking; A passes only with old B.", "Dependency pins.", "A pins old B and result records dependency_versions.", status="implemented_offline"),
    _case("STR-C01", "STR-F01", "P01_external_policy_asset", "artifact_store", "contract_regression", "offline", "Same-name artifact added repeatedly.", "ArtifactStore.", "Version/history/lineage are correct.", status="implemented_offline"),
    _case("STR-C02", "STR-F02", "P14_bundle_result_separation", "artifact_store", "contract_regression", "offline", "Same-name skill update lacks bundle.", "Existing bundle.", "Long-lived bundle is inherited/merged.", status="implemented_offline"),
    _case("STR-C03", "STR-F03", "P30_rollback_non_destructive", "artifact_store", "contract_regression", "offline", "Bad v2 then rollback.", "Artifact history.", "Rollback is non-destructive and traceable.", status="implemented_offline"),
    _case("STR-C04", "STR-F04", "P14_bundle_result_separation", "artifact_store", "contract_regression", "offline", "Same bundle tested twice.", "Store test results.", "Two independent result IDs exist.", status="implemented_offline"),
    _case("RFA-C01", "RFA-F01", "P31_micro_refactor_trigger", "refactorer", "core_algorithm", "offline+llm_full", "K related tasks accumulate repeated evidence.", "Micro-refactor scheduler.", "Scan triggers only at K with enough evidence.", status="implemented_offline"),
    _case("RFA-C02E", "RFA-F02", "P32_shared_reusable_extraction", "refactorer", "core_algorithm", "llm_smoke", "Explicitly duplicated parameter convention.", "Refactor candidates.", "Shared subdoc is extracted.", requires_llm=True),
    _case("RFA-C02M", "RFA-F02", "P32_shared_reusable_extraction", "refactorer", "core_algorithm", "llm_full", "Different text but same workflow.", "Refactor candidates plus negative candidate.", "Agent identifies shared workflow and excludes unrelated candidate.", requires_llm=True),
    _case("RFA-C02H", "RFA-F02", "P32_shared_reusable_extraction", "refactorer", "core_algorithm", "llm_full", "Different tools/tasks but same propagation rule.", "Refactor candidates.", "Agent provides reuse rationale and rewrites references.", requires_llm=True),
    _case("RFA-C03", "RFA-F03", "P33_refactor_correctness_preserving", "refactorer", "contract_regression", "offline+llm_full", "Refactor breaks one affected skill.", "Affected artifacts and bundles.", "Group rollback restores all affected artifacts.", status="implemented_offline"),
    _case("RFA-C04", "RFA-F04", "P42_duplicate_skill_consolidation", "refactorer", "core_algorithm", "llm_full", "Duplicate skills with bundles/evidence/dependencies.", "Canonicalization fixture.", "Canonical skill absorbs assets; redundant skills archived.", status="implemented_offline"),
    _case("RFA-C05", "RFA-F05", "P43_overmerge_split", "refactorer", "core_algorithm", "llm_full", "Overbroad skill or false merge candidates.", "Split/reject fixture.", "Split/reject preserves tests and excludes false merge.", status="implemented_offline"),
    _case("INT-C01", "INT-F01", "P18_integration_test_real_trace", "integration_runner", "core_algorithm", "llm_smoke", "Real train trace replay.", "Existing skill store.", "Integration failure can be linked to skills or refactor candidate.", requires_llm=True),
    _case("INT-C02", "INT-F02", "P04_online_future_help", "integration_runner", "core_algorithm", "llm_smoke", "Difficult task repeated from empty store.", "Multiple loops.", "Loop0 extracts skill; later loops retrieve/use it.", requires_llm=True),
    _case("INT-C03A", "INT-F03A", "P04_online_future_help", "integration_runner", "core_algorithm", "llm_smoke", "Three sibling queries.", "Empty initial store.", "Later queries keep correctness and reduce cost.", requires_llm=True),
    _case("INT-C03B", "INT-F03B", "P20_semantic_refinement_llm", "integration_runner", "core_algorithm", "llm_smoke", "Effective skill then targeted broken injection.", "Next sibling query.", "Failure triggers repair/rollback; disable counted separately.", requires_llm=True),
    _case("INT-C03C", "INT-F03C", "P19_failure_to_bundle_feedback", "integration_runner", "core_algorithm", "llm_smoke", "Local failure under multi-skill coexistence.", "Failure attribution fixture.", "Failure becomes skill-scoped integration case.", status="implemented_offline"),
    _case("INT-C03D", "INT-F03D", "P17_negative_skill_detection", "integration_runner", "core_algorithm", "llm_smoke", "Opposite-contract skills and ambiguous query.", "Retrieval and unit utility fixture.", "Negative transfer is recognized.", status="implemented_offline"),
    _case("INT-C03E", "INT-F03E", "P26_dependency_graph", "integration_runner", "core_algorithm", "llm_full", "A obtains id; B consumes id.", "Multi-skill dependency fixture.", "Collaboration/dependency/stale propagation are correct.", status="implemented_offline"),
    _case("INT-C03F", "INT-F03F", "P31_micro_refactor_trigger", "integration_runner", "core_algorithm", "llm_full", "At least K related queries.", "Repeated evidence.", "K-step scan asks agent for shared reusable part.", status="implemented_offline"),
    _case("LOG-C01", "LOG-F01", "P36_logging_role_io", "audit_logger", "infrastructure_check", "llm_smoke", "Any full experiment.", "Debug events and JSONL audit.", "Role I/O is fully logged.", requires_llm=True),
    _case("LOG-C02", "LOG-F02", "P35_retrieval_auditability", "audit_logger", "infrastructure_check", "offline+llm_smoke", "Any retrieval event.", "Retrieval audit payload.", "Repo summary and scores are present.", status="implemented_offline"),
    _case("LOG-C03", "LOG-F03", "P40_ablation_hooks", "audit_logger", "infrastructure_check", "llm_smoke", "Any full experiment.", "Role call accounting.", "Latency/token/call counts are grouped by role.", requires_llm=True),
    _case("UI-C01", "UI-F01", "P37_visualizable_artifacts", "viewer", "infrastructure_check", "offline", "Results directory.", "Maintenance API listing.", "Experiments are discoverable.", status="planned"),
    _case("UI-C02", "UI-F02", "P37_visualizable_artifacts", "viewer", "infrastructure_check", "offline", "Experiment payload.", "Pages and flow cards.", "Each turn page can show data flow.", status="planned"),
    _case("UI-C03", "UI-F03", "P37_visualizable_artifacts", "viewer", "infrastructure_check", "offline", "Skills, bundles, and results.", "Artifact explorer payload.", "Core artifacts are structured, not raw blobs.", status="planned"),
    _case("UI-C04", "UI-F04", "P37_visualizable_artifacts", "viewer", "infrastructure_check", "offline", "Any raw card payload.", "Tree JSON view.", "Raw fallback is expandable and non-overflowing.", status="planned"),
    _case("REP-C01", "REP-F01", "P39_trajectory_analysis", "reporter", "infrastructure_check", "offline+llm_smoke", "With/without experiment result.", "Report payload.", "All metrics and trajectory deltas are reported.", status="planned"),
    _case("REP-C02", "REP-F02", "P38_benchmark_agnostic_boundary", "reporter", "infrastructure_check", "offline", "Method validation suite result.", "Coverage table.", "Every paper point is pass/fail/partial/missing.", status="planned"),
]


CASE_BY_ID: Dict[str, MethodValidationCase] = {case.case_id: case for case in CASES}
