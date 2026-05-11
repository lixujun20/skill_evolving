"""Reusable assertions and offline fixtures for method validation tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from academic.skill_repository.store import ArtifactStore
from academic.skill_repository.types import (
    DependencyPin,
    SkillArtifact,
    SkillBundle,
    SkillBundleCase,
    SkillEvidence,
    SkillInterface,
    SkillLineage,
    SkillTestCaseRun,
    SkillTestResult,
)


def utility_label(
    *,
    without_accuracy: float | None = None,
    with_accuracy: float | None = None,
    without_validity: bool | None = None,
    with_validity: bool | None = None,
    without_tokens: int | None = None,
    with_tokens: int | None = None,
    without_steps: int | None = None,
    with_steps: int | None = None,
) -> str:
    """Classify with/without utility under the agreed correctness+cost rule."""

    correctness_delta = _correctness_delta(
        without_accuracy=without_accuracy,
        with_accuracy=with_accuracy,
        without_validity=without_validity,
        with_validity=with_validity,
    )
    cost_delta = _cost_delta(
        without_tokens=without_tokens,
        with_tokens=with_tokens,
        without_steps=without_steps,
        with_steps=with_steps,
    )
    if correctness_delta < 0:
        return "harmful"
    if cost_delta > 0 and correctness_delta <= 0:
        return "harmful"
    if correctness_delta >= 0 and cost_delta < 0:
        return "work"
    if correctness_delta > 0:
        return "correctness_gain"
    return "neutral"


def assert_work_label(label: str, *, message: str = "") -> None:
    assert label == "work", message or f"expected work utility label, got {label!r}"


def assert_interface_complete(artifact: SkillArtifact) -> None:
    interface = artifact.interface
    assert interface.summary.strip(), "interface.summary is required"
    assert interface.usage.strip(), "interface.usage is required"
    assert interface.input_contract, "interface.input_contract is required"
    assert interface.output_contract, "interface.output_contract is required"
    assert interface.invocation_contract, "interface.invocation_contract is required"
    assert "todo" not in interface.summary.lower()
    body = artifact.body.lower()
    usage = interface.usage.lower()
    assert any(token in body for token in _tokens(interface.summary)), "summary must be body-consistent"
    assert any(token in body for token in _tokens(usage)), "usage must be body-consistent"


def assert_bundle_has_no_run_metrics(bundle: SkillBundle) -> None:
    text = str(bundle.as_dict()).lower()
    for forbidden in ("accuracy", "validity", "tokens", "steps", "delta_accuracy", "delta_tokens"):
        assert forbidden not in text, f"bundle must not contain run metric {forbidden}"


def assert_result_snapshot(result: SkillTestResult, artifact: SkillArtifact) -> None:
    assert result.skill_name == artifact.name
    assert result.skill_version == artifact.version
    assert result.bundle_id == artifact.bundle.bundle_id
    assert result.bundle_version == artifact.bundle.bundle_version
    assert isinstance(result.dependency_versions, dict)


def assert_full_bundle_coverage(result: SkillTestResult, bundle: SkillBundle) -> None:
    expected_cases = {case.case_id for case in bundle.all_cases()}
    observed_cases = {run.case_id for run in result.unit_case_runs}
    assert expected_cases <= observed_cases, f"missing bundle cases: {sorted(expected_cases - observed_cases)}"
    for case in bundle.all_cases():
        protocol = case.contrast_protocol or bundle.contrast_protocol
        variants = {run.variant for run in result.unit_case_runs if run.case_id == case.case_id}
        if protocol.get("with_skill"):
            assert "with_skill" in variants, f"{case.case_id} missing with_skill run"
        if protocol.get("without_skill"):
            assert "without_skill" in variants, f"{case.case_id} missing without_skill run"


def assert_minor_keeps_old_cases(parent: SkillArtifact, child: SkillArtifact) -> None:
    assert child.version_kind() == "minor"
    old_cases = {case.case_id for case in parent.bundle.all_cases()}
    new_cases = {case.case_id for case in child.bundle.all_cases()}
    assert old_cases <= new_cases, f"minor update removed cases: {sorted(old_cases - new_cases)}"


def assert_interface_change_updates_bundle(parent: SkillArtifact, child: SkillArtifact) -> None:
    parent_interface = parent.interface.as_dict()
    child_interface = child.interface.as_dict()
    if parent_interface == child_interface:
        return
    parent_cases = {case.case_id for case in parent.bundle.all_cases()}
    child_cases = {case.case_id for case in child.bundle.all_cases()}
    assert parent.bundle.bundle_version < child.bundle.bundle_version, "interface changed but bundle_version did not increase"
    assert child_cases != parent_cases or child.lineage.migration_reason, (
        "interface changed but bundle cases/migration reason did not reflect the new contract"
    )


def assert_major_lineage(artifact: SkillArtifact) -> None:
    assert artifact.version_kind() == "major"
    assert artifact.lineage.parent_version is not None
    assert artifact.lineage.parent_version_id
    assert artifact.lineage.migration_reason
    assert artifact.history


def assert_retrieval_audit_has_scores(audit: Dict[str, Any]) -> None:
    assert audit["store_summary"]["n_total"] >= 1
    assert audit["candidates"], "retrieval audit should include candidates"
    for row in audit["candidates"]:
        assert "score" in row
        assert "filter_reason" in row
        assert "retrieval_enabled" in row
    assert "selected" in audit


def assert_stale_exposed_not_resolved(audit: Dict[str, Any], skill_name: str) -> None:
    rows = [row for row in audit["candidates"] if row["name"] == skill_name]
    assert rows, f"{skill_name} missing from candidates"
    assert rows[0]["stale"] is True
    assert rows[0]["status"] == "stale"


def assert_archived_not_retrieved(store: ArtifactStore, query: str, archived_name: str) -> None:
    selected = store.retrieve(query, top_k=10)
    assert archived_name not in {skill.name for skill in selected}


def assert_group_rollback(restored: Sequence[SkillArtifact], before: Sequence[SkillArtifact]) -> None:
    before_by_name = {artifact.name: artifact.body for artifact in before}
    for artifact in restored:
        assert artifact.body == before_by_name[artifact.name]


def assert_post_execute_maintenance_order(events: Sequence[Dict[str, Any]]) -> None:
    completed_indices = [idx for idx, event in enumerate(events) if event.get("phase") == "task_completed"]
    assert completed_indices, "task_completed event is required"
    completed_at = completed_indices[0]
    for idx, event in enumerate(events):
        if event.get("role") in {"extractor", "bundle_builder", "unit_tester", "refiner"}:
            assert idx > completed_at, f"{event.get('role')} ran before task completion"


def assert_refine_outcome_classification(decision: Dict[str, Any]) -> None:
    action = str(decision.get("action") or "")
    category = str(decision.get("recovery_category") or "")
    if action == "disable":
        assert category != "semantic_repair", "disable must not be counted as semantic repair"
    if action in {"refine_minor", "refine_major"}:
        assert category == "semantic_repair"
    if action == "rollback":
        assert category == "rollback_recovery"


def assert_overmerge_split(split_skills: Sequence[SkillArtifact], original_bundle: SkillBundle) -> None:
    assert len(split_skills) >= 2, "over-merged skill should split into at least two narrower skills"
    migrated_cases = {case.case_id for skill in split_skills for case in skill.bundle.all_cases()}
    original_cases = {case.case_id for case in original_bundle.all_cases()}
    assert original_cases <= migrated_cases, f"split lost cases: {sorted(original_cases - migrated_cases)}"


def assert_failure_case_is_skill_scoped(case: SkillBundleCase, *, expected_tool: str, forbidden_tool: str) -> None:
    focus_tools = set(case.context.get("focus_tools") or [])
    assert expected_tool in focus_tools
    assert forbidden_tool not in focus_tools
    assert case.polarity in {"integration", "negative"}


def make_case(case_id: str, *, polarity: str = "positive") -> SkillBundleCase:
    return SkillBundleCase(
        case_id=case_id,
        source="manual",
        prompt=f"Prompt for {case_id}",
        expected={"tool_calls": [{"name": "diff"}]},
        context={"task_fragment": {"question": [{"role": "user", "content": f"Question {case_id}"}]}},
        tags=["method_validation"],
        polarity=polarity,
        contrast_protocol={"with_skill": True, "without_skill": True},
    )


def make_bundle(name: str, *, n_positive: int = 1, n_negative: int = 0, n_integration: int = 0) -> SkillBundle:
    return SkillBundle(
        bundle_id=f"{name}.bundle",
        positive_cases=[make_case(f"{name}:positive:{idx}") for idx in range(n_positive)],
        negative_cases=[make_case(f"{name}:negative:{idx}", polarity="negative") for idx in range(n_negative)],
        integration_cases=[make_case(f"{name}:integration:{idx}", polarity="integration") for idx in range(n_integration)],
        fixtures={"fixture": "lightweight"},
        maintenance_notes="method validation fixture",
    )


def make_skill(
    name: str,
    *,
    body: str | None = None,
    description: str | None = None,
    kind: str = "atomic_tool_rule_card",
    bundle: SkillBundle | None = None,
    metadata: Dict[str, Any] | None = None,
    dependencies: List[str] | None = None,
    status: str = "active",
    version_kind: str = "seed",
) -> SkillArtifact:
    body = body or "When comparing known file names, call diff directly and avoid exploratory listing."
    return SkillArtifact(
        name=name,
        kind=kind,
        description=description or "Directly compare known files with diff.",
        body=body,
        metadata=dict(metadata or {}),
        interface=SkillInterface(
            summary="Direct known file diff rule",
            usage="Use this diff rule when two file names are already given.",
            input_contract={"requires": ["file_name1", "file_name2"]},
            output_contract={"ensures": ["diff call"]},
            invocation_contract={"type": "prompt_rule", "tool": "diff"},
            compatibility_notes="Forward compatible with additional file metadata.",
        ),
        bundle=bundle or make_bundle(name),
        evidence=SkillEvidence(source_traces=[{"task_id": "fixture"}]),
        status=status,
        lineage=SkillLineage(version_kind=version_kind),
        dependencies=list(dependencies or []),
    )


def make_result_for_bundle(
    artifact: SkillArtifact,
    *,
    with_tokens: int = 80,
    without_tokens: int = 120,
    with_steps: int = 1,
    without_steps: int = 2,
    with_validity: bool = True,
    without_validity: bool = True,
    result_id: str = "result.fixture",
) -> SkillTestResult:
    runs: List[SkillTestCaseRun] = []
    for case in artifact.bundle.all_cases():
        runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="without_skill",
                passed=without_validity,
                accuracy=1.0 if without_validity else 0.0,
                validity=without_validity,
                tokens=without_tokens,
                steps=without_steps,
                trace={"turns": [{"role": "assistant", "content": "without"}]},
            )
        )
        runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="with_skill",
                passed=with_validity,
                accuracy=1.0 if with_validity else 0.0,
                validity=with_validity,
                tokens=with_tokens,
                steps=with_steps,
                trace={"turns": [{"role": "assistant", "content": "with"}]},
            )
        )
    label = utility_label(
        without_validity=without_validity,
        with_validity=with_validity,
        without_tokens=without_tokens,
        with_tokens=with_tokens,
        without_steps=without_steps,
        with_steps=with_steps,
    )
    return SkillTestResult(
        result_id=result_id,
        skill_name=artifact.name,
        skill_version=artifact.version,
        bundle_id=artifact.bundle.bundle_id,
        bundle_version=artifact.bundle.bundle_version,
        dependency_versions=artifact.dependency_version_map(),
        run_label="method_validation",
        unit_case_runs=runs,
        aggregate={"passed": all(run.passed for run in runs if run.variant == "with_skill"), "utility_label": label},
        counterfactual={
            "delta_tokens": with_tokens - without_tokens,
            "delta_steps": with_steps - without_steps,
            "utility_label": label,
        },
        created_at="2026-05-10T00:00:00Z",
    )


def consolidate_duplicates(
    canonical: SkillArtifact,
    duplicates: Iterable[SkillArtifact],
) -> tuple[SkillArtifact, List[SkillArtifact]]:
    merged = make_skill(
        canonical.name,
        body=canonical.body,
        description=canonical.description,
        kind=canonical.kind,
        bundle=canonical.bundle,
        metadata=dict(canonical.metadata),
        dependencies=list(canonical.dependencies),
        version_kind="refactor",
    )
    merged.evidence = canonical.evidence
    merged.usage_count = canonical.usage_count
    archived: List[SkillArtifact] = []
    for duplicate in duplicates:
        merged.bundle.positive_cases.extend(duplicate.bundle.positive_cases)
        merged.bundle.negative_cases.extend(duplicate.bundle.negative_cases)
        merged.bundle.integration_cases.extend(duplicate.bundle.integration_cases)
        merged.evidence.source_traces.extend(duplicate.evidence.source_traces)
        merged.usage_count += duplicate.usage_count
        archived_skill = make_skill(
            duplicate.name,
            body=duplicate.body,
            description=duplicate.description,
            kind=duplicate.kind,
            bundle=duplicate.bundle,
            metadata={**duplicate.metadata, "merged_into": canonical.name},
            status="archived",
            version_kind="refactor",
        )
        archived.append(archived_skill)
    merged.metadata["consolidated_from"] = [item.name for item in archived]
    return merged, archived


def should_micro_refactor(*, step: int, k: int, repeated_evidence_count: int) -> bool:
    return step > 0 and step % k == 0 and repeated_evidence_count > 0


def _correctness_delta(
    *,
    without_accuracy: float | None,
    with_accuracy: float | None,
    without_validity: bool | None,
    with_validity: bool | None,
) -> float:
    if without_accuracy is not None and with_accuracy is not None:
        return float(with_accuracy) - float(without_accuracy)
    if without_validity is not None and with_validity is not None:
        return float(bool(with_validity)) - float(bool(without_validity))
    return 0.0


def _cost_delta(
    *,
    without_tokens: int | None,
    with_tokens: int | None,
    without_steps: int | None,
    with_steps: int | None,
) -> float:
    if without_tokens is not None and with_tokens is not None:
        return float(with_tokens) - float(without_tokens)
    if without_steps is not None and with_steps is not None:
        return float(with_steps) - float(without_steps)
    return 0.0


def _tokens(text: str) -> List[str]:
    return [token for token in text.lower().replace("_", " ").split() if len(token) > 2]
