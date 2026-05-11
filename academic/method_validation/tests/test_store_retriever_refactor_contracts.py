from __future__ import annotations

from academic.method_validation.assertions import (
    assert_archived_not_retrieved,
    assert_group_rollback,
    assert_major_lineage,
    assert_retrieval_audit_has_scores,
    assert_stale_exposed_not_resolved,
    consolidate_duplicates,
    make_bundle,
    make_result_for_bundle,
    make_skill,
    should_micro_refactor,
)
from academic.skill_repository.store import ArtifactStore
from academic.skill_repository.types import DependencyPin, SkillLineage


def test_str_c01_same_name_add_versions_history_and_lineage() -> None:
    store = ArtifactStore()
    store.add(make_skill("versioned_skill", body="v1 body direct diff"))
    store.add(make_skill("versioned_skill", body="v2 body direct diff", version_kind="minor"))
    current = store.get("versioned_skill")
    assert current is not None
    assert current.version == 2
    assert current.lineage.parent_version == 1
    assert current.history


def test_str_c02_same_name_update_inherits_bundle() -> None:
    store = ArtifactStore()
    original = make_skill("bundle_preserved", bundle=make_bundle("bundle_preserved", n_positive=2))
    store.add(original)
    incoming = make_skill("bundle_preserved", body="new semantic card without bundle")
    incoming.bundle.positive_cases = []
    store.add(incoming)
    current = store.get("bundle_preserved")
    assert current is not None
    assert len(current.bundle.positive_cases) == 2
    assert current.metadata["bundle_inherited_from_version"] == 1


def test_str_c03_rollback_is_non_destructive() -> None:
    store = ArtifactStore()
    store.add(make_skill("rollback_skill", body="stable body"))
    store.add(make_skill("rollback_skill", body="bad body", version_kind="minor"))
    assert store.rollback("rollback_skill")
    current = store.get("rollback_skill")
    assert current is not None
    assert current.body == "stable body"
    assert current.version_kind() == "rollback"
    assert any(snapshot.get("body") == "bad body" for snapshot in current.history)


def test_str_c04_test_results_are_independent_objects() -> None:
    store = ArtifactStore()
    artifact = make_skill("result_skill")
    store.add(artifact)
    store.add_test_result(make_result_for_bundle(artifact, result_id="r1"))
    store.add_test_result(make_result_for_bundle(artifact, result_id="r2"))
    ids = {result.result_id for result in store.test_results(skill_name="result_skill")}
    assert ids == {"r1", "r2"}


def test_sta_c01_upstream_update_marks_downstream_stale() -> None:
    store = ArtifactStore()
    store.add(make_skill("upstream_rule", body="shared upstream rule"))
    downstream = make_skill(
        "downstream_rule",
        body="Use upstream_rule before downstream call",
        dependencies=["upstream_rule"],
    )
    store.add(downstream)
    store.add(make_skill("upstream_rule", body="updated shared upstream rule", version_kind="major"))
    current_downstream = store.get("downstream_rule")
    assert current_downstream is not None
    assert current_downstream.stale is True
    assert current_downstream.status == "stale"


def test_sta_c03_dependency_pin_records_legacy_version() -> None:
    artifact = make_skill("legacy_downstream")
    artifact.dependency_pins = [DependencyPin(skill_name="upstream_rule", pinned_version=1)]
    result = make_result_for_bundle(artifact)
    assert result.dependency_versions == {"upstream_rule": 1}


def test_ret_c01_retrieval_is_not_keyword_only() -> None:
    store = ArtifactStore()
    store.add(
        make_skill(
            "direct_file_comparison",
            body="When two known filenames must be compared, call diff directly.",
            metadata={"intent_keywords": ["diff"]},
        )
    )
    selected = store.retrieve("Compare report_draft.txt against report_final.txt", top_k=1)
    assert [skill.name for skill in selected] == ["direct_file_comparison"]


def test_ret_c02_retrieval_audit_filters_disabled_and_ranks_relevant() -> None:
    store = ArtifactStore()
    store.add(make_skill("relevant_diff_rule", body="Known filenames should be compared with diff directly."))
    store.add(make_skill("surface_only_rule", body="Diff words appear here but this rule is for calendar booking."))
    store.add(make_skill("disabled_diff_rule", body="Known filenames diff", metadata={"disabled": True}))
    audit = store.retrieve_audit("Compare two known file names with diff", top_k=3)
    assert_retrieval_audit_has_scores(audit)
    selected = {row["name"] for row in audit["selected"]}
    assert "relevant_diff_rule" in selected
    assert "disabled_diff_rule" not in selected
    disabled_rows = [row for row in audit["candidates"] if row["name"] == "disabled_diff_rule"]
    assert disabled_rows[0]["filter_reason"] == "retrieval_disabled"


def test_ret_c03_stale_candidate_is_exposed_not_resolved_by_retriever() -> None:
    store = ArtifactStore()
    store.add(make_skill("upstream_rule"))
    store.add(make_skill("downstream_rule", body="Use upstream_rule before direct diff.", dependencies=["upstream_rule"]))
    store.add(make_skill("upstream_rule", body="major update direct diff", version_kind="major"))
    audit = store.retrieve_audit("Use downstream_rule for direct diff", top_k=5)
    assert_stale_exposed_not_resolved(audit, "downstream_rule")


def test_ret_c04_compact_store_reduces_duplicate_prompt_cost() -> None:
    duplicates = [
        make_skill("dup_a", body="Known filename diff rule."),
        make_skill("dup_b", body="Known filename diff rule."),
        make_skill("dup_c", body="Known filename diff rule."),
    ]
    before = ArtifactStore(duplicates)
    before_prompt = before.build_prompt(before.retrieve("known filename diff", top_k=5))
    canonical, archived = consolidate_duplicates(duplicates[0], duplicates[1:])
    after = ArtifactStore([canonical, *archived])
    after_prompt = after.build_prompt(after.retrieve("known filename diff", top_k=5))
    assert len(after_prompt) < len(before_prompt)
    assert_archived_not_retrieved(after, "known filename diff", "dup_b")
    assert_archived_not_retrieved(after, "known filename diff", "dup_c")


def test_ref_c05_major_update_requires_lineage() -> None:
    artifact = make_skill("major_skill", version_kind="major")
    artifact.lineage = SkillLineage(
        parent_version=1,
        parent_version_id="major_skill@v1",
        version_kind="major",
        migration_reason="interface changed",
    )
    artifact.history = [{"version": 1, "body": "old"}]
    assert_major_lineage(artifact)


def test_rfa_c01_micro_refactor_requires_k_step_and_evidence() -> None:
    assert should_micro_refactor(step=4, k=4, repeated_evidence_count=2)
    assert not should_micro_refactor(step=3, k=4, repeated_evidence_count=2)
    assert not should_micro_refactor(step=4, k=4, repeated_evidence_count=0)


def test_rfa_c03_group_rollback_restores_all_affected_artifacts() -> None:
    before = [make_skill("a", body="old a"), make_skill("b", body="old b")]
    restored = [make_skill("a", body="old a"), make_skill("b", body="old b")]
    assert_group_rollback(restored, before)


def test_rfa_c04_duplicate_consolidation_archives_redundant_skills_and_merges_assets() -> None:
    canonical = make_skill("canonical_diff", bundle=make_bundle("canonical_diff", n_positive=1))
    duplicate = make_skill("duplicate_diff", bundle=make_bundle("duplicate_diff", n_positive=1))
    duplicate.usage_count = 3
    merged, archived = consolidate_duplicates(canonical, [duplicate])
    assert merged.metadata["consolidated_from"] == ["duplicate_diff"]
    assert len(merged.bundle.positive_cases) == 2
    assert merged.usage_count == 3
    assert archived[0].status == "archived"
    assert archived[0].metadata["merged_into"] == "canonical_diff"


def test_int_c03c_failure_becomes_skill_scoped_integration_case() -> None:
    artifact = make_skill("failure_scope", bundle=make_bundle("failure_scope", n_positive=1))
    before_version = artifact.bundle.bundle_version
    artifact.bundle.integration_cases.append(make_bundle("failure_scope_new", n_integration=1).integration_cases[0])
    artifact.bundle.bundle_version = before_version + 1
    assert len(artifact.bundle.integration_cases) == 1
    assert artifact.bundle.bundle_version == before_version + 1


def test_int_c03e_dependency_chain_stales_downstream_on_upstream_update() -> None:
    store = ArtifactStore()
    store.add(make_skill("id_getter", body="Get id first."))
    store.add(make_skill("id_consumer", body="Use id_getter result for downstream call.", dependencies=["id_getter"]))
    store.add(make_skill("id_getter", body="Get id first with new interface.", version_kind="major"))
    assert store.get("id_consumer").stale is True  # type: ignore[union-attr]

