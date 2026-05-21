import asyncio
import copy
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from academic.benchmarks.bfcl.related.experiment import (
    SkillMaintenanceLockManager,
    _aggregate_skill_credit,
    _apply_skill_credit_filter,
    _build_extractor_feedback_rows,
    _checkpoint_payload,
    _compact_task_detail,
    _apply_credit_case_evidence,
    _credit_event_records,
    _default_output_path,
    _load_saved_details,
    _mentioned_skill_names,
    _retrieved_only_skill_names,
    _micro_write_target_names,
    _mark_candidate_competition_artifacts,
    _build_candidate_group_feedback_rows,
    _select_macro_candidate_group_feedback_rows,
    _apply_candidate_group_competition_decisions,
    _append_candidate_group_replay_rows,
    _apply_group_refiner_actions,
    _group_refiner_actions_from_feedback_rows,
    _run_group_maintenance,
    _mark_prior_artifacts_pending,
    _normalize_role_feedback_memory,
    _pending_skill_summary,
    _phase_partial_path,
    _promote_pending_from_refactor_report,
    _role_feedback_projection,
    _restore_current_round_state,
    _run_locked_micro_maintenance,
    _update_skill_relation_graph_locked,
    _write_current_round_sidecars,
    _run_related_baseline,
    _run_related_evolve_experiment,
    _sample_replay_rows_for_role,
    rebuild_checkpoint_from_sidecars,
    build_analysis_artifacts,
    validate_experiment_config,
)
from academic.benchmarks.bfcl.maintenance.adapter import extract_bfcl_skill_artifacts_llm, refine_bfcl_skill_store_llm
from academic.benchmarks.bfcl.related.manifest import (
    build_curated_related_task_manifest,
    validate_curated_manifest,
)
from academic.benchmarks.bfcl.related.segment_index import SegmentVectorIndex
from academic.skill_repository.types import SkillArtifact, SkillBundle, SkillBundleCase, SkillEvidence, SkillTestResult
from academic.skill_repository.refactor_overlap import OverlapGraphState, TraceSegment
from academic.skill_repository.store import ArtifactStore


def test_curated_manifest_has_50_50_split() -> None:
    manifest = build_curated_related_task_manifest(
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        split_seed=42,
        data_source="bfcl_eval_bundle",
    )
    validation = validate_curated_manifest(manifest)
    assert validation["ok"] is True
    assert len(manifest["train_task_ids"]) == 50
    assert len(manifest["test_task_ids"]) == 50
    assert not (set(manifest["train_task_ids"]) & set(manifest["test_task_ids"]))
    assert manifest["train_tasks"][0]["why_related"]


def test_semantic_skill_update_resets_version_local_credit_evidence() -> None:
    original = SkillArtifact(
        name="symbol_rule",
        kind="atomic_tool_rule_card",
        description="Use company names directly.",
        body="Skip symbol lookup for known public companies.",
        bundle=SkillBundle(
            positive_cases=[
                SkillBundleCase(
                    case_id="symbol_rule:positive:0",
                    source="manual",
                    prompt="Buy known public company stock.",
                )
            ]
        ),
        evidence=SkillEvidence(
            helpful_cases=[{"task_id": "task_helped", "judgment": "helpful"}],
            harmful_cases=[{"task_id": "task_harmed", "judgment": "harmful"}],
            repeated_evidence=[{"task_id": "task_repeated", "judgment": "neutral"}],
        ),
    )
    store = ArtifactStore([original])

    updated = SkillArtifact(
        name="symbol_rule",
        kind="atomic_tool_rule_card",
        description="Use exact symbols only when already supplied.",
        body="Use a symbol directly only when the user or previous tool result supplies the exact ticker.",
    )
    store.add(updated)

    current = store.get("symbol_rule")
    assert current is not None
    assert current.version == 2
    assert current.bundle.positive_cases[0].case_id == "symbol_rule:positive:0"
    assert current.metadata["bundle_inherited_from_version"] == 1
    assert current.evidence.helpful_cases == []
    assert current.evidence.harmful_cases == []
    assert current.evidence.repeated_evidence == []
    assert current.history[-1]["evidence"]["helpful_cases"][0]["task_id"] == "task_helped"
    assert current.history[-1]["evidence"]["harmful_cases"][0]["task_id"] == "task_harmed"

    assert store.rollback("symbol_rule") is True
    rolled_back = store.get("symbol_rule")
    assert rolled_back is not None
    assert rolled_back.version == 1
    assert rolled_back.evidence.helpful_cases[0]["task_id"] == "task_helped"


@pytest.mark.asyncio
async def test_bfcl_extraction_metadata_preserves_governed_scope(monkeypatch) -> None:
    observed = []

    async def fake_extract(*args, **kwargs):
        observed.append(kwargs)
        return [
            SkillArtifact(
                name="cancel_booking_rule",
                kind="workflow_guardrail_card",
                description="Cancel booking with id.",
                body="Use the booking id when canceling.",
                metadata={
                    "domains": ["TravelAPI"],
                    "allowed_tools": ["cancel_booking"],
                    "intent_keywords": ["cancel"],
                },
            )
        ]

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.extract_skill_artifacts_from_results_llm",
        fake_extract,
    )

    artifacts = await extract_bfcl_skill_artifacts_llm(
        [
            {
                "task_id": "travel_1",
                "task": {"metadata": {"involved_classes": ["TravelAPI"]}},
                "trace": {
                    "tool_calls": [
                        {"name": "lookup_booking"},
                        {"name": "cancel_booking"},
                        {"name": "contact_customer_support"},
                    ]
                },
                "metrics": {},
            }
        ],
        tool_schemas=[
            {"type": "function", "function": {"name": "lookup_booking"}},
            {"type": "function", "function": {"name": "cancel_booking"}},
            {"type": "function", "function": {"name": "contact_customer_support"}},
        ],
        existing_artifacts=[],
        extractor_rules=[],
        llm_config="mock",
    )

    assert observed[0]["tool_schemas"]
    assert artifacts[0].metadata["allowed_tools"] == ["cancel_booking"]
    assert artifacts[0].metadata["domains"] == ["TravelAPI"]
    assert "metadata_quality" not in artifacts[0].metadata


@pytest.mark.asyncio
async def test_bfcl_extraction_metadata_marks_missing_scope_without_all_or_batch_tools(monkeypatch) -> None:
    async def fake_extract(*args, **kwargs):
        return [
            SkillArtifact(
                name="underspecified_rule",
                kind="workflow_guardrail_card",
                description="Too broad.",
                body="Do something useful.",
                metadata={"domains": ["all"], "allowed_tools": []},
            )
        ]

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.extract_skill_artifacts_from_results_llm",
        fake_extract,
    )

    artifacts = await extract_bfcl_skill_artifacts_llm(
        [
            {
                "task_id": "ticket_1",
                "task": {"metadata": {"involved_classes": ["TicketAPI"]}},
                "trace": {"tool_calls": [{"name": "get_ticket"}, {"name": "edit_ticket"}]},
                "metrics": {},
            }
        ],
        tool_schemas=[],
        existing_artifacts=[],
        extractor_rules=[],
        llm_config="mock",
    )

    metadata = artifacts[0].metadata
    assert metadata["allowed_tools"] == []
    assert metadata["domains"] == []
    assert metadata["metadata_quality"]["missing_allowed_tools"] is True
    assert metadata["metadata_quality"]["missing_domains"] is True
    assert metadata["metadata_quality"]["observed_task_tools"] == ["edit_ticket", "get_ticket"]
    assert metadata["metadata_quality"]["observed_task_domains"] == ["TicketAPI"]


def test_segment_vector_index_records_rows_without_embeddings_when_not_strict() -> None:
    index = SegmentVectorIndex(strict_embeddings=False)
    rows = index.add_segments(
        [
            TraceSegment(
                segment_id="task_a:turn:0",
                task_id="task_a",
                turn_index=0,
                text="lookup booking then cancel booking",
                error_text="wrong booking id after lookup",
            )
        ],
        round_index=0,
        task_id="task_a",
    )
    assert len(rows) == 1
    stats = index.stats()
    assert stats["n_segments"] == 1
    assert stats["strict_embeddings"] is False


def test_candidate_competition_marks_group_and_trial_metadata() -> None:
    artifacts = [
        SkillArtifact(name="airport_lookup_rule", kind="rule_card", description="airport booking lookup", body="lookup airport"),
        SkillArtifact(name="airport_lookup_rule", kind="rule_card", description="airport booking lookup variant", body="lookup airport variant"),
    ]

    prepared = _mark_candidate_competition_artifacts(
        artifacts,
        round_index=0,
        task_index=7,
        task_id="multi_turn_base_7",
        sample_count=2,
        existing_names={"airport_lookup_rule"},
        trial_retrieval=True,
    )

    assert len(prepared) == 2
    assert {artifact.metadata["candidate_group_id"] for artifact in prepared} == {"extract:r0:t7:multi_turn_base_7"}
    assert all(artifact.status == "trial" for artifact in prepared)
    assert all(artifact.retrieval_enabled() for artifact in prepared)
    assert all(artifact.metadata["candidate_group_role"] == "alternative" for artifact in prepared)
    assert prepared[0].name != prepared[1].name
    assert all("airport_lookup_rule" in artifact.metadata.get("candidate_original_name", "") for artifact in prepared)
    assert all(artifact.metadata.get("competes_with") for artifact in prepared)


def test_retrieval_selects_at_most_one_trial_candidate_per_group() -> None:
    group_id = "extract:r0:t1:task"
    store = ArtifactStore(
        [
            SkillArtifact(
                name="candidate_a",
                kind="rule_card",
                description="airport booking lookup candidate",
                body="airport booking lookup",
                status="trial",
                metadata={"candidate_group_id": group_id, "candidate_group_role": "alternative"},
            ),
            SkillArtifact(
                name="candidate_b",
                kind="rule_card",
                description="airport booking lookup candidate alternative",
                body="airport booking lookup",
                status="trial",
                metadata={"candidate_group_id": group_id, "candidate_group_role": "alternative"},
            ),
            SkillArtifact(
                name="independent_skill",
                kind="rule_card",
                description="airport booking lookup independent",
                body="airport booking lookup",
            ),
        ]
    )

    audit = store.retrieve_audit("airport booking lookup", top_k=3)
    selected_names = [row["name"] for row in audit["selected"]]

    assert len([name for name in selected_names if name in {"candidate_a", "candidate_b"}]) == 1
    assert "independent_skill" in selected_names
    suppressed = [
        row for row in audit["candidates"]
        if row["name"] in {"candidate_a", "candidate_b"} and row.get("filter_reason") == "candidate_group_alternative_suppressed"
    ]
    assert suppressed


def test_candidate_group_feedback_selects_winner_and_marks_loser() -> None:
    group_id = "extract:r0:t2:task"
    extraction_events = [
        {
            "artifact_name": "skill_win",
            "skill_name": "skill_win",
            "candidate_group_id": group_id,
            "candidate_sample_index": 0,
            "source_task_id": "task_source",
            "description": "precise winner",
            "kind": "rule_card",
            "status": "trial",
        },
        {
            "artifact_name": "skill_lose",
            "skill_name": "skill_lose",
            "candidate_group_id": group_id,
            "candidate_sample_index": 1,
            "source_task_id": "task_source",
            "description": "broad loser",
            "kind": "rule_card",
            "status": "trial",
        },
    ]
    train_details = [
        {
            "task_id": "task_eval",
            "runs": [
                {
                    "metrics": {
                        "retrieved_skills": ["skill_win", "skill_lose"],
                        "prompt_injected_skills": ["skill_win"],
                        "used_skills": ["skill_win"],
                        "official_valid": True,
                    }
                }
            ],
        }
    ]
    credit_events = [
        {"skill_name": "skill_win", "judgment": "helpful", "reason": "matched exact schema"},
        {"skill_name": "skill_lose", "judgment": "harmful", "reason": "scope too broad"},
    ]

    rows = _build_candidate_group_feedback_rows(
        extraction_events=extraction_events,
        train_details=train_details,
        credit_events=credit_events,
        maintenance_test_results=[],
    )
    selected_rows = _select_macro_candidate_group_feedback_rows(
        raw_rows=rows,
        state={},
        macro_index=0,
        current_task_index=3,
        min_usage=1,
        min_age_tasks=1,
        low_usage_patience=3,
    )

    assert rows[0]["winner"] == "skill_win"
    assert rows[0]["losers"] == ["skill_lose"]
    assert rows[0]["members"][0]["exposure_records"]
    assert rows[0]["members"][0]["credit_records"]
    assert rows[0]["members"][0]["exposure_count"] == 1
    assert rows[0]["members"][0]["helpful_per_exposure"] == 1.0
    assert rows[0]["members"][1]["harmful_per_exposure"] == 1.0
    store = ArtifactStore(
        [
            SkillArtifact(name="skill_win", kind="rule_card", description="d", body="b", status="trial", metadata={"candidate_group_id": group_id, "candidate_group_role": "alternative"}),
            SkillArtifact(name="skill_lose", kind="rule_card", description="d", body="b", status="trial", metadata={"candidate_group_id": group_id, "candidate_group_role": "alternative"}),
        ]
    )
    decisions = _apply_candidate_group_competition_decisions(store=store, group_feedback_rows=selected_rows)

    assert store.get("skill_win").status == "active"
    assert store.get("skill_win").metadata["competition_status"] == "winner"
    assert store.get("skill_lose").metadata["competition_status"] == "loser"
    assert any(row["action"] == "winner_promoted" for row in decisions)


def test_candidate_group_soft_promotion_keeps_neutral_loser_as_backup() -> None:
    group_id = "extract:r0:t2:task"
    rows = [
        {
            "candidate_group_id": group_id,
            "winner": "skill_win",
            "members": [
                {"skill_name": "skill_win", "winner_score": 5, "helpful_count": 2, "harmful_count": 0, "strict_harmful_count": 0},
                {"skill_name": "skill_neutral", "winner_score": 1, "helpful_count": 0, "harmful_count": 0, "strict_harmful_count": 0, "net_credit": 0},
            ],
        }
    ]
    store = ArtifactStore(
        [
            SkillArtifact(name="skill_win", kind="rule_card", description="d", body="b", status="trial", metadata={"candidate_group_id": group_id}),
            SkillArtifact(name="skill_neutral", kind="rule_card", description="d", body="b", status="trial", metadata={"candidate_group_id": group_id}),
        ]
    )

    decisions = _apply_candidate_group_competition_decisions(store=store, group_feedback_rows=rows)

    assert store.get("skill_win").status == "active"
    assert store.get("skill_neutral").status == "trial"
    assert store.get("skill_neutral").metadata["competition_backup_state"] == "runner_up"
    assert any(row["action"] == "marked_loser_backup" for row in decisions)


def test_replay_buffer_samples_by_source_role() -> None:
    replay_buffer: Dict[str, List[Dict[str, Any]]] = {"extractor": [], "refactorer": [], "refiner_revision": []}
    _append_candidate_group_replay_rows(
        replay_buffer,
        [
            {"candidate_group_id": "extract:r0:t1:a", "members": []},
            {"candidate_group_id": "refactor:r0:g1", "members": []},
            {"candidate_group_id": "refiner:r0:s1", "members": []},
        ],
    )

    assert [row["source_role"] for row in _sample_replay_rows_for_role(replay_buffer, "extractor", extractor_refiner_probability=0.0)] == ["extractor"]
    assert [row["source_role"] for row in _sample_replay_rows_for_role(replay_buffer, "refactorer")] == ["refactorer"]
    assert [row["source_role"] for row in _sample_replay_rows_for_role(replay_buffer, "refiner_revision")] == ["refiner_revision"]
    assert [row["source_role"] for row in _sample_replay_rows_for_role(replay_buffer, "refiner")] == ["refiner_revision"]
    mixed = _sample_replay_rows_for_role(
        replay_buffer,
        "extractor",
        round_index=0,
        macro_index=0,
        extractor_refiner_probability=1.0,
    )
    assert [row["source_role"] for row in mixed] == ["extractor", "refiner_revision"]


def test_candidate_group_feedback_rows_include_refiner_revision_store_candidates() -> None:
    group_id = "refiner:r0:group:skill_a"
    store = ArtifactStore(
        [
            SkillArtifact(
                name="skill_a__refined_r0_c0",
                kind="rule_card",
                description="candidate 0",
                body="body",
                status="trial",
                metadata={
                    "candidate_group_id": group_id,
                    "candidate_group_source_role": "refiner_revision",
                    "candidate_sample_index": 0,
                },
            ),
            SkillArtifact(
                name="skill_a__refined_r0_c1",
                kind="rule_card",
                description="candidate 1",
                body="body",
                status="trial",
                metadata={
                    "candidate_group_id": group_id,
                    "candidate_group_source_role": "refiner_revision",
                    "candidate_sample_index": 1,
                },
            ),
        ]
    )
    rows = _build_candidate_group_feedback_rows(
        extraction_events=[],
        train_details=[],
        credit_events=[],
        maintenance_test_results=[],
        store=store,
    )
    assert rows[0]["candidate_group_id"] == group_id
    assert rows[0]["source_role"] == "refiner_revision"
    assert {member["skill_name"] for member in rows[0]["members"]} == {
        "skill_a__refined_r0_c0",
        "skill_a__refined_r0_c1",
    }


@pytest.mark.asyncio
async def test_refiner_multi_candidate_creates_zero_credit_trial_revisions(monkeypatch) -> None:
    parent = SkillArtifact(
        name="lookup_then_update",
        kind="rule_card",
        description="Use lookup before update.",
        body="Original body",
        status="active",
        evidence=SkillEvidence(
            helpful_cases=[{"task_id": "old_help"}],
            harmful_cases=[{"task_id": "old_harm"}],
            repeated_evidence=[{"pattern": "old"}],
            integration_failures=[{"case": "old"}],
        ),
        bundle=SkillBundle(
            bundle_id="lookup_then_update.bundle",
            negative_cases=[
                SkillBundleCase(
                    case_id="wrong_account",
                    source="unit",
                    prompt="Update only after lookup.",
                    expected={"official_valid": True},
                    polarity="negative",
                )
            ],
        ),
    )
    store = ArtifactStore([parent])
    test_result = SkillTestResult(
        result_id="unit_refine_result",
        skill_name="lookup_then_update",
        skill_version=parent.version,
        bundle_id=parent.bundle.bundle_id,
        bundle_version=parent.bundle.bundle_version,
        aggregate={"pass_all_tests": False, "n_regressed": 1, "n_improved": 0},
        integration_failures=[{"task_id": "wrong_account", "reason": "updated wrong account"}],
    )
    payloads = [
        {
            "decision": {
                "action": "refine_minor",
                "reason": "tighten account match",
                "version_kind": "minor",
                "migration_reason": "candidate 0",
            },
            "artifact": {"body": "Refined body 0", "description": "candidate 0"},
        },
        {
            "decision": {
                "action": "refine_minor",
                "reason": "require canonical id",
                "version_kind": "minor",
                "migration_reason": "candidate 1",
            },
            "artifact": {"body": "Refined body 1", "description": "candidate 1"},
        },
    ]

    async def fake_refine_skill_artifact_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        index = int((kwargs.get("audit_context") or {}).get("candidate_index") or 0)
        return copy.deepcopy(payloads[index])

    async def fake_apply_refine_payload_via_editor(artifact: SkillArtifact, payload: Dict[str, Any]) -> SkillArtifact:
        updated = copy.deepcopy(artifact)
        updated.body = str((payload.get("artifact") or {}).get("body") or updated.body)
        updated.description = str((payload.get("artifact") or {}).get("description") or updated.description)
        return updated

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.refine_skill_artifact_llm",
        fake_refine_skill_artifact_llm,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.apply_refine_payload_via_editor",
        fake_apply_refine_payload_via_editor,
    )

    decisions = await refine_bfcl_skill_store_llm(
        store,
        maintenance_test_results=[test_result],
        llm_config="mock",
        model_name="mock-model",
        artifact_names=["lookup_then_update"],
        candidate_count=2,
        candidate_group_id="refiner:r0:unit:lookup_then_update",
        audit_context={"phase": "unit_refine", "round_index": 0},
    )

    candidate_names = [row["skill_name"] for row in decisions if row.get("source_role") == "refiner_revision"]
    assert candidate_names == ["lookup_then_update__refined_r0_c0", "lookup_then_update__refined_r0_c1"]
    assert store.get("lookup_then_update").body == "Original body"
    assert store.get("lookup_then_update").metadata["refiner_revision_candidates"] == candidate_names
    for index, name in enumerate(candidate_names):
        candidate = store.get(name)
        assert candidate is not None
        assert candidate.status == "trial"
        assert candidate.body == f"Refined body {index}"
        assert candidate.metadata["candidate_group_source_role"] == "refiner_revision"
        assert candidate.metadata["candidate_for_existing_skill"] == "lookup_then_update"
        assert candidate.evidence == SkillEvidence()
        assert candidate.lineage.refactor_group_id == "refiner:r0:unit:lookup_then_update"


def test_group_refiner_actions_refine_archive_and_backup() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="mixed", kind="rule_card", description="d", body="b", status="trial"),
            SkillArtifact(name="bad", kind="rule_card", description="d", body="b", status="trial"),
            SkillArtifact(name="zero", kind="rule_card", description="d", body="b", status="trial"),
        ]
    )
    rows = [
        {
            "candidate_group_id": "extract:r0:t1:a",
            "members": [
                {"skill_name": "mixed", "exposure_count": 2, "helpful_count": 1, "harmful_count": 1, "strict_harmful_count": 0, "net_credit": 0},
                {"skill_name": "bad", "exposure_count": 1, "helpful_count": 0, "harmful_count": 1, "strict_harmful_count": 1, "net_credit": -1},
                {"skill_name": "zero", "exposure_count": 0, "helpful_count": 0, "harmful_count": 0, "strict_harmful_count": 0, "net_credit": 0},
            ],
        }
    ]

    actions = _group_refiner_actions_from_feedback_rows(store=store, group_feedback_rows=rows)
    by_name = {row["skill_name"]: row["action"] for row in actions}

    assert by_name == {"mixed": "refine", "bad": "archive", "zero": "backup"}
    assert store.get("mixed").metadata["group_refiner_refine_required"] is True
    assert store.get("bad").status == "archived"
    assert store.get("zero").metadata["competition_backup_state"] == "group_refiner_backup"


def test_apply_group_refiner_actions_guards_against_archiving_helpful_or_zero_exposure() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="helpful", kind="rule_card", description="d", body="b", status="trial"),
            SkillArtifact(name="zero", kind="rule_card", description="d", body="b", status="trial"),
        ]
    )
    rows = [
        {
            "candidate_group_id": "extract:r0:t1:a",
            "members": [
                {"skill_name": "helpful", "exposure_count": 3, "helpful_count": 1, "harmful_count": 1},
                {"skill_name": "zero", "exposure_count": 0, "helpful_count": 0, "harmful_count": 0},
            ],
        }
    ]

    actions = _apply_group_refiner_actions(
        store=store,
        group_feedback_rows=rows,
        actions=[
            {"candidate_group_id": "extract:r0:t1:a", "skill_name": "helpful", "action": "archive", "reason": "too broad"},
            {"candidate_group_id": "extract:r0:t1:a", "skill_name": "zero", "action": "refine", "reason": "maybe useful"},
        ],
        source="llm",
    )
    by_name = {row["skill_name"]: row["action"] for row in actions}

    assert by_name == {"helpful": "backup", "zero": "backup"}
    assert store.get("helpful").status == "trial"
    assert store.get("helpful").metadata["group_refiner_action_source"] == "llm"
    assert store.get("zero").metadata["competition_backup_state"] == "group_refiner_backup"


@pytest.mark.asyncio
async def test_run_group_maintenance_triggers_bundle_test_and_repair_rounds(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="group_refine_skill", kind="rule_card", description="d", body="b", status="trial"),
        ]
    )
    calls = {"test": 0, "refine": 0}

    async def fake_bundle_test_and_refine_targets(**kwargs):
        calls["test"] += 1
        calls["refine"] += 1
        return {
            "maintenance_targets": list(kwargs["targets"]),
            "maintenance_test_results": [{"skill_name": "group_refine_skill", "aggregate": {"pass_all_tests": False}}],
            "refine_decisions": [{"skill_name": "group_refine_skill", "action": "rewrite"}],
        }

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bundle_test_and_refine_targets",
        fake_bundle_test_and_refine_targets,
    )

    report = await _run_group_maintenance(
        store=store,
        group_feedback_rows=[
            {
                "candidate_group_id": "extract:r0:t1:task",
                "source_task_ids": ["task_1"],
                "members": [
                    {
                        "skill_name": "group_refine_skill",
                        "exposure_count": 2,
                        "helpful_count": 1,
                        "harmful_count": 1,
                        "strict_harmful_count": 0,
                        "net_credit": 0,
                        "credit_records": [{"skill_name": "group_refine_skill", "judgment": "harmful"}],
                    }
                ],
            }
        ],
        window_details=[{"task_id": "task_1"}],
        tools=[],
        llm_config="mock",
        model_name=None,
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=1,
        max_task_seconds=1.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        round_index=0,
        tag="group_test",
        credit_context_by_skill={"group_refine_skill": [{"task_id": "task_1", "judgment": "harmful"}]},
        dependency_context_by_skill={"group_refine_skill": []},
        refiner_rules=[],
        lock_manager=SkillMaintenanceLockManager(micro_concurrency=1),
    )

    assert calls == {"test": 1, "refine": 1}
    assert report["maintenance_targets"] == ["group_refine_skill"]
    assert report["group_refiner_actions"][0]["action"] == "refine"
    assert report["maintenance_test_results"][0]["skill_name"] == "group_refine_skill"
    assert report["refine_decisions"][0]["action"] == "rewrite"


@pytest.mark.asyncio
async def test_run_group_maintenance_can_consume_precomputed_llm_group_actions(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="llm_refine_skill", kind="rule_card", description="d", body="b", status="trial"),
        ]
    )
    calls = {"refine": 0}

    async def fake_bundle_test_and_refine_targets(**kwargs):
        calls["refine"] += 1
        return {
            "maintenance_targets": list(kwargs["targets"]),
            "maintenance_test_results": [{"skill_name": "llm_refine_skill", "aggregate": {"pass_all_tests": False}}],
            "refine_decisions": [{"skill_name": "llm_refine_skill", "action": "rewrite"}],
        }

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bundle_test_and_refine_targets",
        fake_bundle_test_and_refine_targets,
    )

    report = await _run_group_maintenance(
        store=store,
        group_feedback_rows=[
            {
                "candidate_group_id": "extract:r0:t1:task",
                "source_task_ids": ["task_1"],
                "members": [
                    {"skill_name": "llm_refine_skill", "exposure_count": 4, "helpful_count": 2, "harmful_count": 1}
                ],
            }
        ],
        window_details=[{"task_id": "task_1"}],
        tools=[],
        llm_config="mock",
        model_name=None,
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=1,
        max_task_seconds=1.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        round_index=0,
        tag="group_test",
        refiner_rules=[],
        lock_manager=SkillMaintenanceLockManager(micro_concurrency=1),
        group_refiner_actions=[
            {
                "candidate_group_id": "extract:r0:t1:task",
                "skill_name": "llm_refine_skill",
                "action": "refine",
                "reason": "LLM saw useful but mixed evidence",
                "source": "llm",
                "patch_intent": "tighten trigger",
            }
        ],
        group_refiner_action_payload={
            "source": "llm",
            "analysis": "Useful candidate with one harmful overgeneralization.",
        },
    )

    assert calls["refine"] == 1
    assert report["maintenance_targets"] == ["llm_refine_skill"]
    assert report["group_refiner_actions"][0]["source"] == "llm"
    assert report["group_refiner_action_payload"]["source"] == "llm"
    assert report["refine_decisions"][0]["action"] == "rewrite"


@pytest.mark.asyncio
async def test_group_maintenance_mock_llm_sees_group_evidence_and_outputs_consumable_refine(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(
                name="group_refine_skill",
                kind="rule_card",
                description="Use lookup before update.",
                body="Original rule misses the group-level account mismatch.",
                status="trial",
                metadata={"domains": ["TicketAPI"], "allowed_tools": ["lookup_ticket", "update_ticket"]},
            ),
        ]
    )
    bundle_calls: List[Dict[str, Any]] = []
    llm_calls: List[Dict[str, Any]] = []
    monkeypatch.setenv("BFCL_GROUP_REPAIR_ROUNDS", "3")

    async def fake_execute_bfcl_bundle_tests(artifact, **kwargs):
        bundle_calls.append({"name": artifact.name, "version": artifact.version, "body": artifact.body})
        passed = len(bundle_calls) >= 3
        return SkillTestResult(
            result_id=f"group_mock:{artifact.name}:call{len(bundle_calls)}",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
            bundle_version=artifact.bundle.bundle_version,
            run_label="mock_group_bundle",
            aggregate={
                "pass_all_tests": passed,
                "passed": passed,
                "n_regressed": 0 if passed else 1,
                "n_improved": 1 if passed else 0,
                "failure_reason": "" if passed else "wrong account update after group exposure",
            },
            integration_failures=[] if passed else [{"task_id": "task_group_1", "reason": "wrong account update"}],
        )

    async def fake_ask_json(**kwargs: Any) -> Dict[str, Any]:
        assert kwargs["role"] == "refiner"
        user = str(kwargs.get("user") or "")
        metadata = dict(kwargs.get("metadata") or {})
        llm_calls.append({"user": user, "metadata": metadata})
        assert metadata["phase"] == "group_refine"
        assert metadata["repair_round"] == len(llm_calls) - 1
        assert "## Recent Credit Assignment Context" in user
        assert "task_group_1" in user
        assert "mixed_helpful_and_harmful_evidence" in user
        assert "wrong account update after group exposure" in user
        return {
            "decision": {
                "action": "refine_minor",
                "reason": "Mock LLM followed group credit and narrowed the account-update scope.",
                "version_kind": "minor",
                "migration_reason": "group_refiner mixed helpful/harmful evidence",
            },
            "artifact": {
                "body": (
                    "Use lookup_ticket to confirm the target account before update_ticket; "
                    f"group repair round {len(llm_calls)}."
                ),
                "metadata": {
                    "mock_group_refine_round": len(llm_calls),
                    "non_applicability": ["Do not update a ticket when lookup result belongs to another account."],
                },
            },
            "bundle": {
                "maintenance_notes": "Added a group-evidence regression case from task_group_1.",
                "negative_cases": [
                    {
                        "case_id": "task_group_1_wrong_account_regression",
                        "source": "group_refiner_mock",
                        "prompt": "Lookup the ticket, then update only if the account matches.",
                        "expected": {"official_valid": True},
                        "context": {"source_task_id": "task_group_1", "credit_judgment": "harmful"},
                        "tags": ["group_refiner", "credit_regression"],
                        "polarity": "negative",
                    }
                ],
            },
        }

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.execute_bfcl_bundle_tests",
        fake_execute_bfcl_bundle_tests,
    )
    monkeypatch.setattr(
        "academic.skill_repository.llm_maintenance._ask_json",
        fake_ask_json,
    )

    report = await _run_group_maintenance(
        store=store,
        group_feedback_rows=[
            {
                "candidate_group_id": "extract:r0:t1:task_group_1",
                "source_task_ids": ["task_group_1"],
                "members": [
                    {
                        "skill_name": "group_refine_skill",
                        "exposure_count": 2,
                        "retrieved_count": 2,
                        "injected_count": 2,
                        "used_count": 1,
                        "helpful_count": 1,
                        "harmful_count": 1,
                        "strict_harmful_count": 0,
                        "net_credit": 0,
                        "credit_records": [
                            {
                                "task_id": "task_group_1",
                                "task_index": 1,
                                "judgment": "harmful",
                                "effect_type": "correctness_loss",
                                "confidence": 0.91,
                                "reason": "wrong account update after group exposure",
                                "retrieved": True,
                                "injected": True,
                                "used": True,
                                "official_valid": False,
                                "strict_success": False,
                            }
                        ],
                    }
                ],
            }
        ],
        window_details=[
            {
                "task_id": "task_group_1",
                "runs": [
                    {
                        "metrics": {
                            "official_valid": False,
                            "retrieved_skills": ["group_refine_skill"],
                            "prompt_injected_skills": ["group_refine_skill"],
                            "used_skills": ["group_refine_skill"],
                        }
                    }
                ],
            }
        ],
        tools=[],
        llm_config="mock",
        model_name="mock-model",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=1,
        max_task_seconds=1.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        round_index=0,
        tag="group_mock_llm",
        credit_context_by_skill={
            "group_refine_skill": [
                {
                    "task_id": "task_group_1",
                    "judgment": "harmful",
                    "reason": "mixed_helpful_and_harmful_evidence: wrong account update after group exposure",
                    "retrieved": True,
                    "injected": True,
                    "used": True,
                }
            ]
        },
        dependency_context_by_skill={"group_refine_skill": []},
        refiner_rules=[],
        lock_manager=SkillMaintenanceLockManager(micro_concurrency=1),
    )

    updated = store.get("group_refine_skill")
    assert updated is not None
    assert len(bundle_calls) == 3
    assert len(llm_calls) == 2
    assert report["group_refiner_actions"][0]["action"] == "refine"
    assert report["group_refiner_credit_rows"][0]["task_id"] == "task_group_1"
    assert [row["action"] for row in report["refine_decisions"]] == ["refine_minor", "refine_minor"]
    assert report["maintenance_test_results"][-1]["aggregate"]["pass_all_tests"] is True
    assert updated.version >= 3
    assert updated.metadata["mock_group_refine_round"] == 2
    assert updated.bundle.negative_cases[0].case_id == "task_group_1_wrong_account_regression"
    assert "group repair round 2" in updated.body


def test_candidate_group_archives_only_positive_harmful_loser() -> None:
    group_id = "extract:r0:t2:task"
    rows = [
        {
            "candidate_group_id": group_id,
            "winner": "skill_win",
            "members": [
                {"skill_name": "skill_win", "winner_score": 6, "helpful_count": 2, "harmful_count": 0, "strict_harmful_count": 0},
                {"skill_name": "skill_bad", "winner_score": 1, "helpful_count": 0, "harmful_count": 1, "strict_harmful_count": 1, "net_credit": -1},
            ],
        }
    ]
    store = ArtifactStore(
        [
            SkillArtifact(name="skill_win", kind="rule_card", description="d", body="b", status="trial", metadata={"candidate_group_id": group_id}),
            SkillArtifact(name="skill_bad", kind="rule_card", description="d", body="b", status="trial", metadata={"candidate_group_id": group_id}),
        ]
    )

    decisions = _apply_candidate_group_competition_decisions(store=store, group_feedback_rows=rows)

    assert store.get("skill_bad").status == "archived"
    assert any(row["action"] == "archived_loser" for row in decisions)


def test_macro_candidate_group_feedback_filters_by_usage_and_low_usage_patience() -> None:
    used_row = {
        "candidate_group_id": "extract:r0:t0:group_used",
        "created_task_index": 0,
        "winner": "skill_used_a",
        "members": [
            {"skill_name": "skill_used_a", "retrieved_count": 1, "injected_count": 0, "used_count": 0},
            {"skill_name": "skill_used_b", "retrieved_count": 0, "injected_count": 0, "used_count": 0},
        ],
    }
    idle_row = {
        "candidate_group_id": "extract:r0:t0:group_idle",
        "created_task_index": 0,
        "winner": "skill_idle_a",
        "members": [
            {"skill_name": "skill_idle_a", "retrieved_count": 0, "injected_count": 0, "used_count": 0},
            {"skill_name": "skill_idle_b", "retrieved_count": 0, "injected_count": 0, "used_count": 0},
        ],
    }
    state: Dict[str, Dict[str, Any]] = {}

    first = _select_macro_candidate_group_feedback_rows(
        raw_rows=[used_row, idle_row],
        state=state,
        macro_index=0,
        current_task_index=0,
        min_usage=1,
        min_age_tasks=2,
        low_usage_patience=3,
    )
    second = _select_macro_candidate_group_feedback_rows(
        raw_rows=[idle_row],
        state=state,
        macro_index=1,
        current_task_index=1,
        min_usage=1,
        min_age_tasks=2,
        low_usage_patience=3,
    )
    third = _select_macro_candidate_group_feedback_rows(
        raw_rows=[used_row, idle_row],
        state=state,
        macro_index=2,
        current_task_index=2,
        min_usage=1,
        min_age_tasks=2,
        low_usage_patience=3,
    )

    assert first == []
    assert second == []
    assert [row["candidate_group_id"] for row in third] == ["extract:r0:t0:group_used"]
    assert third[0]["feedback_reason"] == "sufficient_macro_usage"
    assert third[0]["age_tasks"] == 2
    assert third[0]["min_age_tasks"] == 2
    assert state["extract:r0:t0:group_idle"]["consecutive_low_usage_macros"] == 1
    assert state["extract:r0:t0:group_idle"]["last_skip_reason"] == "below_usage_threshold"


def test_macro_candidate_group_low_usage_feedback_requires_explicit_opt_in() -> None:
    low_usage_row = {
        "candidate_group_id": "extract:r0:t0:group_low",
        "created_task_index": 0,
        "winner": "skill_low_a",
        "members": [
            {"skill_name": "skill_low_a", "retrieved_count": 1, "injected_count": 0, "used_count": 0},
            {"skill_name": "skill_low_b", "retrieved_count": 0, "injected_count": 0, "used_count": 0},
        ],
    }
    state: Dict[str, Dict[str, Any]] = {}

    first = _select_macro_candidate_group_feedback_rows(
        raw_rows=[low_usage_row],
        state=state,
        macro_index=0,
        current_task_index=3,
        min_usage=2,
        min_age_tasks=2,
        low_usage_patience=2,
    )
    second = _select_macro_candidate_group_feedback_rows(
        raw_rows=[low_usage_row],
        state=state,
        macro_index=1,
        current_task_index=4,
        min_usage=2,
        min_age_tasks=2,
        low_usage_patience=2,
        allow_low_usage_feedback=True,
    )

    assert first == []
    assert second[0]["feedback_reason"] == "low_reuse_below_usage_threshold"
    assert second[0]["macro_usage_count"] == 1
    assert second[0]["min_usage_threshold"] == 2
    assert second[0]["age_tasks"] == 4
    assert "low-usage feedback is emitted only after maturity/patience gates" in second[0]["objective_record_schema"]


def test_prior_extraction_adds_pending_skills_that_do_not_retrieve() -> None:
    store = ArtifactStore()
    [pending] = _mark_prior_artifacts_pending(
        [
            SkillArtifact(
                name="explicit_symbol_rule",
                kind="atomic_tool_rule_card",
                description="Use explicit ticker directly.",
                body="When the user provides a stock ticker, call the target tool directly.",
            )
        ],
        round_index=0,
        task_index=1,
        task_id="train_1",
    )
    store.add_pending(pending)
    stored = store.get("explicit_symbol_rule")
    assert stored is not None
    assert stored.status == "pending"
    assert stored.retrieval_enabled() is False
    assert store.retrieve("explicit ticker", top_k=5) == []
    assert _pending_skill_summary(store)["pending_skill_names"] == ["explicit_symbol_rule"]


def test_pending_skill_can_be_promoted_from_committed_refactor_report() -> None:
    store = ArtifactStore()
    pending = SkillArtifact(
        name="explicit_symbol_rule",
        kind="atomic_tool_rule_card",
        description="Use explicit ticker directly.",
        body="When the user provides a stock ticker, call the target tool directly.",
    )
    store.add_pending(pending)
    report = {
        "attempts": [
            {
                "status": "committed",
                "group_id": "g1",
                "llm_payload": {
                    "affected_skill_updates": [
                        {"name": "explicit_symbol_rule", "action": "rewrite"}
                    ]
                },
            }
        ]
    }
    promotions = _promote_pending_from_refactor_report(store=store, refactor_report=report)
    promoted = store.get("explicit_symbol_rule")
    assert promotions[0]["skill_name"] == "explicit_symbol_rule"
    assert promoted is not None
    assert promoted.status == "active"
    assert promoted.metadata["is_promoted"] is True
    assert promoted.retrieval_enabled() is True


async def test_locked_micro_maintenance_parallelizes_disjoint_targets(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name=f"skill_{idx}", kind="rule_card", description="d", body="b")
            for idx in range(4)
        ]
    )
    locks = SkillMaintenanceLockManager(micro_concurrency=4)
    active = 0
    max_active = 0

    async def fake_micro(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return {
            "phase": "micro",
            "task_index": kwargs["task_index"],
            "maintenance_targets": list(kwargs["relevant_skill_names"]),
            "maintenance_test_results": [],
            "refine_decisions": [],
        }

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_micro_maintenance", fake_micro)

    await asyncio.gather(
        *[
            _run_locked_micro_maintenance(
                lock_manager=locks,
                store=store,
                target_names=[f"skill_{idx}"],
                detail={"task_id": f"task_{idx}"},
                task_credit_events=[],
                credit_bundle_cases=[],
                relevant_skill_names=[f"skill_{idx}"],
                tools=[],
                llm_config="test",
                model_name=None,
                execution_backend="official",
                prompt_style="native",
                tool_api_style="auto",
                max_steps_per_turn=1,
                max_task_seconds=1.0,
                temperature=None,
                synthetic_continue=False,
                explicit_skill_tool=False,
                round_index=0,
                task_index=idx,
                tag="lock_test",
                credit_context_by_skill={},
            )
            for idx in range(4)
        ]
    )

    assert max_active == 4


async def test_locked_micro_maintenance_serializes_same_target(monkeypatch) -> None:
    store = ArtifactStore([SkillArtifact(name="skill_a", kind="rule_card", description="d", body="b")])
    locks = SkillMaintenanceLockManager(micro_concurrency=4)
    active_by_skill: Dict[str, int] = {}
    max_active_by_skill: Dict[str, int] = {}

    async def fake_micro(**kwargs):
        name = kwargs["relevant_skill_names"][0]
        active_by_skill[name] = active_by_skill.get(name, 0) + 1
        max_active_by_skill[name] = max(max_active_by_skill.get(name, 0), active_by_skill[name])
        await asyncio.sleep(0.03)
        active_by_skill[name] -= 1
        return {
            "phase": "micro",
            "task_index": kwargs["task_index"],
            "maintenance_targets": [name],
            "maintenance_test_results": [],
            "refine_decisions": [],
        }

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_micro_maintenance", fake_micro)

    await asyncio.gather(
        *[
            _run_locked_micro_maintenance(
                lock_manager=locks,
                store=store,
                target_names=["skill_a"],
                detail={"task_id": f"task_{idx}"},
                task_credit_events=[],
                credit_bundle_cases=[],
                relevant_skill_names=["skill_a"],
                tools=[],
                llm_config="test",
                model_name=None,
                execution_backend="official",
                prompt_style="native",
                tool_api_style="auto",
                max_steps_per_turn=1,
                max_task_seconds=1.0,
                temperature=None,
                synthetic_continue=False,
                explicit_skill_tool=False,
                round_index=0,
                task_index=idx,
                tag="lock_test",
                credit_context_by_skill={},
            )
            for idx in range(5)
        ]
    )

    assert max_active_by_skill["skill_a"] == 1


async def test_locked_micro_uses_credit_write_targets_not_shared_candidates(monkeypatch) -> None:
    store = ArtifactStore(
        [SkillArtifact(name="shared_candidate", kind="rule_card", description="d", body="b")]
        + [
            SkillArtifact(name=f"skill_{idx}", kind="rule_card", description="d", body="b")
            for idx in range(4)
        ]
    )
    locks = SkillMaintenanceLockManager(micro_concurrency=4)
    active = 0
    max_active = 0
    locked_targets: List[List[str]] = []

    async def fake_micro(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.03)
        active -= 1
        return {
            "phase": "micro",
            "task_index": kwargs["task_index"],
            "maintenance_targets": _micro_write_target_names(
                task_credit_events=kwargs["task_credit_events"],
                credit_bundle_cases=kwargs["credit_bundle_cases"],
                relevant_skill_names=kwargs["relevant_skill_names"],
            ),
            "relevant_skill_names": list(kwargs["relevant_skill_names"]),
            "maintenance_test_results": [],
            "refine_decisions": [],
        }

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_micro_maintenance", fake_micro)

    jobs = []
    for idx in range(4):
        relevant = ["shared_candidate", f"skill_{idx}"]
        credit_cases = [{"skill_name": f"skill_{idx}", "case_id": f"case_{idx}", "polarity": "negative"}]
        targets = _micro_write_target_names(
            task_credit_events=[],
            credit_bundle_cases=credit_cases,
            relevant_skill_names=relevant,
        )
        locked_targets.append(targets)
        jobs.append(
            _run_locked_micro_maintenance(
                lock_manager=locks,
                store=store,
                target_names=targets,
                detail={"task_id": f"task_{idx}"},
                task_credit_events=[],
                credit_bundle_cases=credit_cases,
                relevant_skill_names=relevant,
                tools=[],
                llm_config="test",
                model_name=None,
                execution_backend="official",
                prompt_style="native",
                tool_api_style="auto",
                max_steps_per_turn=1,
                max_task_seconds=1.0,
                temperature=None,
                synthetic_continue=False,
                explicit_skill_tool=False,
                round_index=0,
                task_index=idx,
                tag="narrow_lock_test",
                credit_context_by_skill={},
            )
        )

    reports = await asyncio.gather(*jobs)

    assert locked_targets == [[f"skill_{idx}"] for idx in range(4)]
    assert max_active == 4
    assert all("shared_candidate" in report["relevant_skill_names"] for report in reports)
    assert {tuple(report["locked_targets"]) for report in reports} == {
        (f"skill_{idx}",) for idx in range(4)
    }


async def test_locked_micro_marks_target_stale_when_reference_changes(monkeypatch) -> None:
    skill_a = SkillArtifact(name="skill_a", kind="rule_card", description="d", body="b")
    skill_a.dependencies = ["skill_b"]
    skill_b = SkillArtifact(name="skill_b", kind="rule_card", description="d", body="b")
    store = ArtifactStore([skill_a, skill_b])
    locks = SkillMaintenanceLockManager(micro_concurrency=2)
    micro_started = asyncio.Event()
    release_micro = asyncio.Event()

    async def fake_micro(**kwargs):
        micro_started.set()
        await release_micro.wait()
        return {
            "phase": "micro",
            "task_index": kwargs["task_index"],
            "maintenance_targets": ["skill_a"],
            "maintenance_test_results": [],
            "refine_decisions": [],
        }

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_micro_maintenance", fake_micro)
    task = asyncio.create_task(
        _run_locked_micro_maintenance(
            lock_manager=locks,
            store=store,
            target_names=["skill_a"],
            detail={"task_id": "task_0"},
            task_credit_events=[],
            credit_bundle_cases=[],
            relevant_skill_names=["skill_a"],
            tools=[],
            llm_config="test",
            model_name=None,
            execution_backend="official",
            prompt_style="native",
            tool_api_style="auto",
            max_steps_per_turn=1,
            max_task_seconds=1.0,
            temperature=None,
            synthetic_continue=False,
            explicit_skill_tool=False,
            round_index=0,
            task_index=0,
            tag="lock_test",
            credit_context_by_skill={},
        )
    )
    await micro_started.wait()
    store.get("skill_b").version += 1
    release_micro.set()
    report = await task

    updated = store.get("skill_a")
    assert updated is not None
    assert updated.stale is True
    assert updated.metadata["stale_due_to_dependency_change"] is True
    assert report["stale_dependency_changes"][0]["skill_name"] == "skill_b"


async def test_relation_graph_update_uses_short_global_lock(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="skill_a", kind="rule_card", description="a", body="a"),
            SkillArtifact(name="skill_b", kind="rule_card", description="b", body="b"),
        ]
    )
    locks = SkillMaintenanceLockManager(micro_concurrency=2)
    saw_locked = []

    def fake_update(store, **kwargs):
        saw_locked.append(locks.relation_graph_lock.locked())

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.update_skill_relation_graph", fake_update)

    await asyncio.gather(
        *[
            _update_skill_relation_graph_locked(
                lock_manager=locks,
                store=store,
                retrieved=["skill_a", "skill_b"],
            )
            for _ in range(8)
        ]
    )

    assert saw_locked == [True] * 8


async def test_locked_micro_high_concurrency_with_mock_llm_actions_and_dependency_stale(monkeypatch) -> None:
    skills = [
        SkillArtifact(name=f"skill_{idx}", kind="rule_card", description=f"skill {idx}", body=f"body {idx}")
        for idx in range(15)
    ]
    dependency = SkillArtifact(name="skill_dep", kind="rule_card", description="shared dependency", body="dep body")
    skills[1].dependencies = ["skill_dep"]
    skills[2].dependencies = ["skill_dep"]
    skills[3].dependencies = ["skill_1"]
    store = ArtifactStore([*skills, dependency])
    locks = SkillMaintenanceLockManager(micro_concurrency=15)
    active_replay = 0
    active_refine = 0
    max_active_replay = 0
    max_active_refine = 0
    refine_actions: List[Dict[str, Any]] = []
    dependent_refine_started = asyncio.Event()

    async def mutate_dependency_during_refine() -> None:
        await asyncio.wait_for(dependent_refine_started.wait(), timeout=1.0)
        async with locks.target_write_locks(["skill_dep"]):
            artifact = store.get("skill_dep")
            assert artifact is not None
            updated = copy.deepcopy(artifact)
            updated.body = f"{artifact.body}\nmock dependency rewrite"
            updated.metadata["mock_llm_action"] = "rewrite_dependency"
            store.add(updated)

    async def fake_execute_bfcl_bundle_tests(artifact, **kwargs):
        nonlocal active_replay, max_active_replay
        active_replay += 1
        max_active_replay = max(max_active_replay, active_replay)
        await asyncio.sleep(0.04)
        active_replay -= 1
        return SkillTestResult(
            result_id=f"mock_replay:{artifact.name}:v{artifact.version}",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
            bundle_version=artifact.bundle.bundle_version,
            run_label="mock_bundle_unit",
            aggregate={"pass_all_tests": False, "n_regressed": 1, "n_improved": 0},
            integration_failures=[{"mock_failure": "schema_contract", "skill_name": artifact.name}],
        )

    async def fake_refine_bfcl_skill_store_llm(store, *, maintenance_test_results, artifact_names, **kwargs):
        nonlocal active_refine, max_active_refine
        active_refine += 1
        max_active_refine = max(max_active_refine, active_refine)
        try:
            name = list(artifact_names)[0]
            if name in {"skill_1", "skill_2"}:
                dependent_refine_started.set()
                delay = 0.16
            else:
                delay = 0.05
            await asyncio.sleep(delay)
            artifact = store.get(name)
            assert artifact is not None
            updated = copy.deepcopy(artifact)
            updated.body = f"{artifact.body}\nmock rewrite for {name}"
            updated.metadata["mock_llm_action"] = "rewrite"
            updated.metadata["mock_refine_reason"] = "bundle regression from mock replay"
            store.add(updated)
            current = store.get(name)
            action = {
                "skill_name": name,
                "action": "rewrite",
                "reason": "mock LLM chose rewrite after failing bundle replay",
                "version_before": artifact.version,
                "version_after": current.version if current else artifact.version,
                "mock_llm": True,
            }
            refine_actions.append(action)
            return [action]
        finally:
            active_refine -= 1

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.execute_bfcl_bundle_tests",
        fake_execute_bfcl_bundle_tests,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.refine_bfcl_skill_store_llm",
        fake_refine_bfcl_skill_store_llm,
    )

    started = time.perf_counter()
    dependency_mutation_task = asyncio.create_task(mutate_dependency_during_refine())
    reports = await asyncio.gather(
        *[
            _run_locked_micro_maintenance(
                lock_manager=locks,
                store=store,
                target_names=[f"skill_{idx}"],
                detail={"task_id": f"task_{idx}"},
                task_credit_events=[],
                credit_bundle_cases=[{"skill_name": f"skill_{idx}", "case_id": f"case_{idx}", "polarity": "negative"}],
                relevant_skill_names=[f"skill_{idx}"],
                tools=[],
                llm_config="mock",
                model_name="mock-model",
                execution_backend="official",
                prompt_style="native",
                tool_api_style="auto",
                max_steps_per_turn=1,
                max_task_seconds=1.0,
                temperature=None,
                synthetic_continue=False,
                explicit_skill_tool=False,
                round_index=0,
                task_index=idx,
                tag="high_concurrency_lock_test",
                credit_context_by_skill={},
            )
            for idx in range(15)
        ]
    )
    await dependency_mutation_task
    elapsed = time.perf_counter() - started

    assert len(reports) == 15
    assert {row["refine_decisions"][0]["action"] for row in reports} == {"rewrite"}
    assert len(refine_actions) == 15
    assert all((store.get(f"skill_{idx}") or SkillArtifact(name="", kind="", description="", body="")).version >= 2 for idx in range(15))
    assert (store.get("skill_1") or skills[1]).stale is True
    assert (store.get("skill_1") or skills[1]).metadata["stale_due_to_dependency_change"] is True
    assert max_active_replay >= 12
    assert max_active_refine >= 10
    assert elapsed < 0.75


async def test_locked_micro_mock_llm_sleep_shows_parallel_speedup(monkeypatch) -> None:
    async def fake_execute_bfcl_bundle_tests(artifact, **kwargs):
        await asyncio.sleep(0.03)
        return SkillTestResult(
            result_id=f"mock_replay:{artifact.name}:v{artifact.version}",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
            bundle_version=artifact.bundle.bundle_version,
            run_label="mock_bundle_unit",
            aggregate={"pass_all_tests": False, "n_regressed": 1, "n_improved": 0},
            integration_failures=[{"mock_failure": "needs_rewrite", "skill_name": artifact.name}],
        )

    async def fake_refine_bfcl_skill_store_llm(store, *, artifact_names, **kwargs):
        await asyncio.sleep(0.05)
        name = list(artifact_names)[0]
        artifact = store.get(name)
        assert artifact is not None
        updated = copy.deepcopy(artifact)
        updated.body = f"{artifact.body}\nmock llm rewrite"
        updated.metadata["mock_llm_action"] = "rewrite"
        store.add(updated)
        current = store.get(name)
        return [
            {
                "skill_name": name,
                "action": "rewrite",
                "reason": "mock LLM slept then rewrote",
                "version_before": artifact.version,
                "version_after": current.version if current else artifact.version,
            }
        ]

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.execute_bfcl_bundle_tests",
        fake_execute_bfcl_bundle_tests,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.refine_bfcl_skill_store_llm",
        fake_refine_bfcl_skill_store_llm,
    )

    async def run_batch(concurrency: int) -> float:
        store = ArtifactStore(
            [
                SkillArtifact(name=f"sleep_skill_{idx}", kind="rule_card", description="d", body="b")
                for idx in range(15)
            ]
        )
        locks = SkillMaintenanceLockManager(micro_concurrency=concurrency)
        started = time.perf_counter()
        reports = await asyncio.gather(
            *[
                _run_locked_micro_maintenance(
                    lock_manager=locks,
                    store=store,
                    target_names=[f"sleep_skill_{idx}"],
                    detail={"task_id": f"sleep_task_{idx}"},
                    task_credit_events=[],
                    credit_bundle_cases=[{"skill_name": f"sleep_skill_{idx}", "case_id": f"sleep_case_{idx}", "polarity": "negative"}],
                    relevant_skill_names=[f"sleep_skill_{idx}"],
                    tools=[],
                    llm_config="mock",
                    model_name="mock-model",
                    execution_backend="official",
                    prompt_style="native",
                    tool_api_style="auto",
                    max_steps_per_turn=1,
                    max_task_seconds=1.0,
                    temperature=None,
                    synthetic_continue=False,
                    explicit_skill_tool=False,
                    round_index=0,
                    task_index=idx,
                    tag=f"sleep_speedup_{concurrency}",
                    credit_context_by_skill={},
                )
                for idx in range(15)
            ]
        )
        elapsed = time.perf_counter() - started
        assert len(reports) == 15
        assert {row["refine_decisions"][0]["action"] for row in reports} == {"rewrite"}
        assert all((store.get(f"sleep_skill_{idx}") or SkillArtifact(name="", kind="", description="", body="")).version >= 2 for idx in range(15))
        return elapsed

    parallel_elapsed = await run_batch(concurrency=15)
    serial_elapsed = await run_batch(concurrency=1)

    assert parallel_elapsed < 0.35
    assert serial_elapsed > 1.0
    assert serial_elapsed / parallel_elapsed >= 4.0


async def test_run_related_baseline_forwards_test_concurrency(monkeypatch) -> None:
    manifest = {"train_task_ids": ["train_1"], "test_task_ids": ["test_1", "test_2"]}
    fake_test_tasks = [
        type("Task", (), {"task_id": "test_1", "metadata": {}})(),
        type("Task", (), {"task_id": "test_2", "metadata": {}})(),
    ]
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: ([], fake_test_tasks),
    )
    calls: List[Dict[str, Any]] = []

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        calls.append({"tasks": list(tasks), "kwargs": dict(kwargs)})
        return []

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline",
        fake_run_bfcl_baseline,
    )

    payload = await _run_related_baseline(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        data_source="bfcl_eval_bundle",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=20,
        max_task_seconds=180.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        tag="tag",
        test_concurrency=4,
    )

    assert calls[0]["kwargs"]["concurrency"] == 4
    assert calls[0]["kwargs"]["phase"] == "related_baseline_test"
    assert payload["config_summary"]["test_concurrency"] == 4


def test_unpromoted_pending_skills_are_revoked_without_deleting_lineage() -> None:
    store = ArtifactStore()
    store.add_pending(
        SkillArtifact(
            name="weak_candidate",
            kind="workflow_guardrail_card",
            description="Weak candidate",
            body="Maybe useful.",
        )
    )
    revoked = store.revoke_unpromoted_pending(reason="test_round_end")
    artifact = store.get("weak_candidate")
    assert revoked == ["weak_candidate"]
    assert artifact is not None
    assert artifact.status == "archived"
    assert artifact.retrieval_enabled() is False
    assert artifact.metadata["promotion_state"] == "revoked"


def test_segment_vector_index_supports_query_and_skill_neighbor_lookups() -> None:
    index = SegmentVectorIndex(strict_embeddings=False)
    index.add_segments(
        [
            TraceSegment(
                segment_id="task_a:turn:0",
                task_id="task_a",
                turn_index=0,
                text="lookup booking then cancel booking",
                error_text="wrong booking id after lookup",
            ),
            TraceSegment(
                segment_id="task_b:turn:0",
                task_id="task_b",
                turn_index=0,
                text="lookup order then cancel order",
                error_text="wrong order id after lookup",
            ),
        ],
        round_index=0,
        task_id="task_a",
    )
    skill = SkillArtifact(
        name="cancel_after_lookup",
        kind="workflow_guardrail_card",
        description="Resolve identifier before cancellation.",
        body="Lookup the target id, then call the cancellation tool with that identifier.",
    )
    assert isinstance(index.top_k_neighbors_for_query("lookup cancel identifier", top_k=2), list)
    assert isinstance(index.top_k_neighbors_for_skill(skill, top_k=2), list)
    assert isinstance(index.embedding_map(), dict)


def test_segment_vector_index_keeps_same_base_segment_across_rounds() -> None:
    index = SegmentVectorIndex(strict_embeddings=False)
    segment = TraceSegment(
        segment_id="task_a:turn:0",
        task_id="task_a",
        turn_index=0,
        text="lookup booking then cancel booking",
        error_text="wrong booking id after lookup",
    )
    index.add_segments([segment], round_index=0, task_id="task_a")
    index.add_segments([segment], round_index=1, task_id="task_a")
    stats = index.stats()
    assert stats["n_segments"] == 2
    assert set(index.embedding_map(round_index=0).keys()) == {"task_a:turn:0"} or index.embedding_map(round_index=0) == {}
    assert set(index.embedding_map(round_index=1).keys()) == {"task_a:turn:0"} or index.embedding_map(round_index=1) == {}


def test_analysis_artifacts_produce_compare_and_case_candidates() -> None:
    manifest = {
        "train_task_ids": ["train_1"],
        "test_task_ids": ["test_1"],
    }
    baseline = {
        "test_summary": {"official_valid_rate": 0.0, "avg_score": 0.0, "avg_total_tokens": 100.0, "avg_model_steps": 6.0},
        "test_details": [
            {
                "task_id": "test_1",
                "runs": [{"score": 0.0, "metrics": {"official_valid": False, "total_tokens": 100, "n_model_steps": 6}}],
            }
        ],
    }
    evolve = {
        "rounds": [
            {
                "round_index": 0,
                "train_summary": {"official_valid_rate": 0.4},
                "train_details": [
                    {
                        "task_id": "train_1",
                        "runs": [{"metrics": {"official_valid": False, "total_tokens": 50, "n_model_steps": 4, "retrieved_skills": [], "used_skills": [], "call_errors": []}}],
                    }
                ],
                "overlap_refactor": {"attempts": []},
            },
            {
                "round_index": 1,
                "train_summary": {"official_valid_rate": 0.6},
                "train_details": [
                    {
                        "task_id": "train_1",
                        "runs": [{"metrics": {"official_valid": True, "total_tokens": 40, "n_model_steps": 3, "retrieved_skills": ["skill_a"], "used_skills": ["skill_a"], "call_errors": []}}],
                    }
                ],
                "overlap_refactor": {
                    "attempts": [
                        {
                            "group_id": "g1",
                            "repair_round": 0,
                            "status": "committed",
                            "clique": {"clique_id": "c1", "segment_ids": ["s1", "s2"], "edge_weight_sum": 1.2},
                            "llm_payload": {"decision": {"reason": "shared invariant", "confidence": 0.8}},
                            "shared_skill": {"name": "skill_a"},
                            "affected_updates": [],
                        }
                    ]
                },
            },
        ],
        "skills": [
            {
                "name": "skill_a",
                "kind": "atomic_tool_rule_card",
                "description": "desc",
                "body": "body",
                "version": 2,
                "metadata": {"source_task_ids": ["train_1"]},
                "lineage": {"parent_version": 1, "parent_version_id": "skill_a@v1", "version_kind": "minor", "refactor_group_id": "g1"},
                "bundle": {"bundle_id": "skill_a.bundle", "bundle_version": 1, "positive_cases": [], "negative_cases": [], "integration_cases": [], "fixtures": {}, "contrast_protocol": {}, "maintenance_notes": ""},
                "evidence": {"source_traces": [], "helpful_cases": [], "harmful_cases": [], "repeated_evidence": [], "integration_failures": []},
                "interface": {"summary": "", "usage": "", "input_contract": {}, "output_contract": {}, "invocation_contract": {}, "compatibility_notes": ""},
                "status": "active",
                "dependency_pins": [],
                "dependencies": [],
                "history": [
                    {
                        "name": "skill_a",
                        "kind": "atomic_tool_rule_card",
                        "description": "desc v1",
                        "body": "body v1",
                        "version": 1,
                        "metadata": {"source_task_ids": ["train_1"]},
                        "lineage": {"version_kind": "seed"},
                        "bundle": {"bundle_id": "skill_a.bundle", "bundle_version": 1, "positive_cases": [], "negative_cases": [], "integration_cases": [], "fixtures": {}, "contrast_protocol": {}, "maintenance_notes": ""},
                        "evidence": {"source_traces": [], "helpful_cases": [], "harmful_cases": [], "repeated_evidence": [], "integration_failures": []},
                        "interface": {"summary": "", "usage": "", "input_contract": {}, "output_contract": {}, "invocation_contract": {}, "compatibility_notes": ""},
                        "status": "active",
                        "dependency_pins": [],
                        "dependencies": [],
                        "history": [],
                        "stale": False,
                    }
                ],
                "stale": False,
            }
        ],
        "test_summary": {"official_valid_rate": 1.0, "avg_score": 1.0, "avg_total_tokens": 80.0, "avg_model_steps": 5.0},
        "test_details": [
            {
                "task_id": "test_1",
                "runs": [
                    {
                        "score": 1.0,
                        "metrics": {
                            "official_valid": True,
                            "total_tokens": 80,
                            "n_model_steps": 5,
                            "retrieved_skills": ["skill_a"],
                            "prompt_injected_skills": ["skill_a"],
                            "tool_injected_skills": [],
                            "used_skills": ["skill_a"],
                            "called_skill_tools": [],
                        },
                    }
                ],
            }
        ],
        "overlap_refactor": {"attempts": []},
    }
    artifacts = build_analysis_artifacts(manifest=manifest, baseline_summary=baseline, evolve_summary=evolve)
    assert artifacts["end_to_end_metrics_summary"]["baseline"]["official_valid_rate"] == 0.0
    assert artifacts["per_test_task_compare_rows"][0]["evolve_official_valid"] is True
    assert artifacts["case_study_candidates"]["baseline_fail_evolve_pass_candidates"]
    heldout = artifacts["case_study_candidates"]["baseline_fail_evolve_pass_candidates"][0]
    assert heldout["skill_name"] == "skill_a"
    assert heldout["source_train_task_ids"] == ["train_1"]
    assert heldout["refactor_group_id"] == "g1"
    assert any(row["version"] == 1 for row in artifacts["skill_evolution_table"])
    assert any(row["version"] == 2 for row in artifacts["skill_evolution_table"])


def test_validate_curated_manifest_can_skip_task_rows_for_analyze_mode() -> None:
    manifest = {
        "train_task_ids": ["train_1"],
        "test_task_ids": ["test_1"],
    }
    validation = validate_curated_manifest(
        manifest,
        expected_train=None,
        expected_test=None,
        require_task_rows=False,
    )
    assert validation["ok"] is True
    assert validation["missing_row_count"] == 2


def test_validate_experiment_config_respects_custom_expected_sizes() -> None:
    manifest = {
        "train_task_ids": ["train_1", "train_2"],
        "test_task_ids": ["test_1"],
        "train_tasks": [
            {"task_id": "train_1"},
            {"task_id": "train_2"},
        ],
        "test_tasks": [
            {"task_id": "test_1"},
        ],
    }
    payload = validate_experiment_config(
        manifest=manifest,
        output_path=None,
        save_skills=None,
        checkpoint_path=None,
        strict_embeddings=False,
        expected_train=2,
        expected_test=1,
    )
    assert payload["manifest"]["expected_train"] == 2
    assert payload["manifest"]["expected_test"] == 1
    assert payload["manifest"]["ok"] is True
    assert payload["ok"] is True


def test_cli_entrypoints_can_run_directly_without_stdlib_types_shadowing() -> None:
    project_root = Path("/home/lixujun/skill_evolving")
    proxy_entry = project_root / "academic" / "benchmarks" / "bfcl" / "related" / "proxy_runner.py"
    monitor_entry = project_root / "academic" / "benchmarks" / "bfcl" / "diagnostics" / "progress_monitor.py"
    canonical = subprocess.run(
        [sys.executable, "-m", "academic.benchmarks.bfcl.related.experiment", "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    proxy_module = subprocess.run(
        [sys.executable, "-m", "academic.benchmarks.bfcl.related.proxy_runner", "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    proxy = subprocess.run(
        [sys.executable, str(proxy_entry), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    monitor = subprocess.run(
        [sys.executable, str(monitor_entry), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert canonical.returncode == 0, canonical.stderr
    assert proxy_module.returncode == 0, proxy_module.stderr
    assert proxy.returncode == 0, proxy.stderr
    assert monitor.returncode == 0, monitor.stderr
    assert "Run the BFCL related-task overlap-refactor experiment" in canonical.stdout
    assert "Run the BFCL related-task overlap-refactor experiment" in proxy_module.stdout
    assert "Run the BFCL related-task overlap-refactor experiment" in proxy.stdout
    assert "Monitor one or more BFCL experiment jobs" in monitor.stdout


def test_compact_task_detail_preserves_analysis_fields() -> None:
    detail: Dict[str, Any] = {
        "task_id": "task_1",
        "task": {"task_id": "task_1", "question": ["q"]},
        "n_runs": 1,
        "n_success": 1,
        "avg_score": 1.0,
        "runs": [
            {
                "benchmark": "bfcl_v3",
                "task_id": "task_1",
                "success": True,
                "score": 1.0,
                "metrics": {
                    "official_valid": True,
                    "total_tokens": 42,
                    "n_model_steps": 3,
                    "retrieved_skills": ["skill_a"],
                    "prompt_injected_skills": ["skill_a"],
                    "tool_injected_skills": [],
                    "used_skills": ["skill_a"],
                    "called_skill_tools": [],
                },
                "trace": {
                    "task_id": "task_1",
                    "messages": [{"role": "user", "content": "hello"}],
                    "turns": [{"tool_calls": []}],
                    "tool_calls": [{"name": "tool_a", "arguments": {"x": 1}}],
                    "retrieved_skills": ["skill_a"],
                    "prompt_injected_skills": ["skill_a"],
                    "tool_injected_skills": [],
                    "called_skill_tools": [],
                    "turn_step_counts": [3],
                    "n_model_steps": 3,
                    "total_tokens": 42,
                    "completion_tokens": 7,
                    "elapsed_s": 1.2,
                    "timed_out": False,
                    "debug_events": [{"event_type": "x"}] * 5,
                },
                "error": None,
                "run_idx": 0,
            }
        ],
    }
    compact = _compact_task_detail(detail)
    run = compact["runs"][0]
    assert run["metrics"]["official_valid"] is True
    assert run["trace"]["tool_calls"][0]["name"] == "tool_a"
    assert run["trace"]["n_messages"] == 1
    assert run["trace"]["n_debug_events"] == 5
    assert "messages" not in run["trace"]
    assert "turns" not in run["trace"]


def test_checkpoint_payload_roundtrips_saved_details(tmp_path: Path) -> None:
    checkpoint = tmp_path / "exp.json"
    phase_path = _phase_partial_path(checkpoint, "checkpoint")
    assert phase_path == tmp_path / "exp_checkpoint.json"
    detail = {
        "task_id": "train_1",
        "task": {"task_id": "train_1"},
        "n_runs": 1,
        "n_success": 1,
        "avg_score": 1.0,
        "runs": [],
    }
    payload = {
        "details": [detail],
    }
    phase_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    assert _load_saved_details(phase_path) == [detail]


def test_validate_experiment_config_checks_checkpoint_path(tmp_path: Path) -> None:
    manifest = {
        "train_task_ids": ["train_1"],
        "test_task_ids": ["test_1"],
        "train_tasks": [{"task_id": "train_1"}],
        "test_tasks": [{"task_id": "test_1"}],
    }
    out = tmp_path / "out.json"
    skills = tmp_path / "skills.json"
    checkpoint = tmp_path / "checkpoint.json"
    validation = validate_experiment_config(
        manifest=manifest,
        output_path=out,
        save_skills=skills,
        checkpoint_path=checkpoint,
        strict_embeddings=False,
    )
    labels = {row["label"] for row in validation["output_paths"]}
    assert {"output", "save_skills", "checkpoint"} <= labels


def test_checkpoint_payload_contains_store_and_segment_rows() -> None:
    skill = SkillArtifact(
        name="skill_a",
        kind="atomic_tool_rule_card",
        description="desc",
        body="body",
    )
    manifest_rounds: List[Dict[str, Any]] = [{"round_index": 0, "train_details": []}]
    index = SegmentVectorIndex(strict_embeddings=False)
    index.load_rows(
        [
            {
                "segment_id": "r0:seg0",
                "base_segment_id": "seg0",
                "task_id": "train_1",
                "round": 0,
                "turn_index": 0,
                "text": "x",
                "error_text": "",
                "embedding": None,
                "metadata": {},
            }
        ]
    )
    payload = _checkpoint_payload(
        tag="tag",
        rounds=3,
        round_reports=manifest_rounds,
        store=type("StoreWrap", (), {"all": lambda self: [skill], "test_results": lambda self: []})(),
        segment_index=index,
        next_round_index=1,
        current_round_state={"round_index": 0, "next_task_index": 2},
        role_feedback=None,
        output_detail_level="compact",
        checkpoint_path=Path("/tmp/checkpoint.json"),
    )
    assert payload["next_round_index"] == 1
    assert payload["store"]["artifacts"][0]["name"] == "skill_a"
    assert payload["segment_index_rows"][0]["segment_id"] == "r0:seg0"


def test_analysis_artifacts_accept_compact_trace_projection() -> None:
    manifest = {
        "train_task_ids": ["train_1"],
        "test_task_ids": ["test_1"],
    }
    baseline = {
        "test_summary": {"official_valid_rate": 0.0, "avg_score": 0.0, "avg_total_tokens": 100.0, "avg_model_steps": 6.0},
        "test_details": [
            {
                "task_id": "test_1",
                "runs": [{"score": 0.0, "metrics": {"official_valid": False, "total_tokens": 100, "n_model_steps": 6}}],
            }
        ],
    }
    compact_detail = _compact_task_detail(
        {
            "task_id": "test_1",
            "task": {"task_id": "test_1"},
            "n_runs": 1,
            "n_success": 1,
            "avg_score": 1.0,
            "runs": [
                {
                    "benchmark": "bfcl_v3",
                    "task_id": "test_1",
                    "success": True,
                    "score": 1.0,
                    "metrics": {
                        "official_valid": True,
                        "total_tokens": 80,
                        "n_model_steps": 5,
                        "retrieved_skills": ["skill_a"],
                        "prompt_injected_skills": ["skill_a"],
                        "tool_injected_skills": [],
                        "used_skills": ["skill_a"],
                        "called_skill_tools": [],
                    },
                    "trace": {
                        "task_id": "test_1",
                        "tool_calls": [{"name": "tool_a", "arguments": {}}],
                        "retrieved_skills": ["skill_a"],
                        "prompt_injected_skills": ["skill_a"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "turn_step_counts": [5],
                        "n_model_steps": 5,
                        "total_tokens": 80,
                        "completion_tokens": 10,
                        "elapsed_s": 2.0,
                        "timed_out": False,
                        "messages": [{"role": "user", "content": "q"}],
                        "turns": [{"tool_calls": []}],
                        "debug_events": [],
                    },
                    "error": None,
                    "run_idx": 0,
                }
            ],
        }
    )
    evolve = {
        "rounds": [
            {
                "round_index": 0,
                "train_summary": {"official_valid_rate": 0.5},
                "train_details": [compact_detail],
                "overlap_refactor": {"attempts": []},
            }
        ],
        "skills": [
            {
                "name": "skill_a",
                "kind": "atomic_tool_rule_card",
                "description": "desc",
                "body": "body",
                "version": 1,
                "metadata": {"source_task_ids": ["train_1"]},
                "lineage": {"version_kind": "seed"},
                "bundle": {"bundle_id": "skill_a.bundle", "bundle_version": 1, "positive_cases": [], "negative_cases": [], "integration_cases": [], "fixtures": {}, "contrast_protocol": {}, "maintenance_notes": ""},
                "evidence": {"source_traces": [], "helpful_cases": [], "harmful_cases": [], "repeated_evidence": [], "integration_failures": []},
                "interface": {"summary": "", "usage": "", "input_contract": {}, "output_contract": {}, "invocation_contract": {}, "compatibility_notes": ""},
                "status": "active",
                "dependency_pins": [],
                "dependencies": [],
                "history": [],
                "stale": False,
            }
        ],
        "test_summary": {"official_valid_rate": 1.0, "avg_score": 1.0, "avg_total_tokens": 80.0, "avg_model_steps": 5.0},
        "test_details": [compact_detail],
    }
    artifacts = build_analysis_artifacts(manifest=manifest, baseline_summary=baseline, evolve_summary=evolve)
    assert artifacts["per_test_task_compare_rows"][0]["evolve_official_valid"] is True


def test_artifact_history_snapshots_do_not_recursively_embed_history() -> None:
    store = ArtifactStore()
    v1 = SkillArtifact(name="skill_a", kind="workflow_guardrail_card", description="d1", body="b1")
    store.add(v1)
    v2 = SkillArtifact(name="skill_a", kind="workflow_guardrail_card", description="d2", body="b2")
    store.add(v2)
    v3 = SkillArtifact(name="skill_a", kind="workflow_guardrail_card", description="d3", body="b3")
    store.add(v3)
    current = store.get("skill_a")
    assert current is not None
    assert len(current.history) == 2
    assert current.history[0].get("history") == []
    assert current.history[1].get("history") == []


def test_artifact_store_add_skips_noop_same_name_updates() -> None:
    store = ArtifactStore()
    v1 = SkillArtifact(name="skill_a", kind="workflow_guardrail_card", description="d1", body="b1")
    store.add(v1)
    noop = SkillArtifact(name="skill_a", kind="workflow_guardrail_card", description="d1", body="b1")
    store.add(noop)
    current = store.get("skill_a")
    assert current is not None
    assert current.version == 1
    assert current.history == []


def test_checkpoint_payload_projects_current_round_state_without_inline_large_details(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    current_round_state = {
        "round_index": 0,
        "next_task_index": 3,
        "train_details": [
            {
                "task_id": "train_1",
                "task": {"task_id": "train_1"},
                "n_runs": 1,
                "n_success": 1,
                "avg_score": 1.0,
                "runs": [
                    {
                        "benchmark": "bfcl_v3",
                        "task_id": "train_1",
                        "success": True,
                        "score": 1.0,
                        "metrics": {"official_valid": True},
                        "trace": {
                            "task_id": "train_1",
                            "messages": [{"role": "user", "content": "hello"}],
                            "turns": [{"tool_calls": []}],
                            "tool_calls": [{"name": "tool_a", "arguments": {}}],
                            "debug_events": [{"event_type": "debug"}] * 10,
                        },
                        "error": None,
                        "run_idx": 0,
                    }
                ],
            }
        ],
        "online_refactor_attempts": [
            {
                "after_task_id": "train_1",
                "after_task_index": 0,
                "n_segments_seen": 2,
                "report": {"attempts": [{"group_id": "g1", "clique": {"segment_ids": ["s1", "s2"]}}]},
            }
        ],
        "extraction_events": [{"skill_name": "skill_a", "source_task_id": "train_1"}],
        "role_feedback": {
            "extractor": {
                "rules": [{"rule_id": "x", "text": "Prefer exact local contract rules.", "focus": "contract"}],
                "history": [{"round_index": 0, "summary": "seed"}],
            }
        },
        "seen_refactor_cliques": [["s1", "s2"]],
        "online_refactor_budget_remaining": 1,
        "overlap_state": OverlapGraphState(),
    }
    _write_current_round_sidecars(
        checkpoint_path=checkpoint,
        current_round_state=current_round_state,
    )
    payload = _checkpoint_payload(
        tag="tag",
        rounds=3,
        round_reports=[],
        store=type("StoreWrap", (), {"all": lambda self: [], "test_results": lambda self: []})(),
        segment_index=SegmentVectorIndex(strict_embeddings=False),
        next_round_index=0,
        current_round_state=current_round_state,
        role_feedback=current_round_state["role_feedback"],
        output_detail_level="compact",
        checkpoint_path=checkpoint,
    )
    state = payload["current_round_state"]
    assert "train_details" not in state
    assert "online_refactor_attempts" not in state
    assert state["n_train_details"] == 1
    assert state["n_online_refactor_attempts"] == 1
    assert state["n_extraction_events"] == 1
    assert state["role_feedback"]["extractor"]["n_rules"] == 1
    assert Path(state["train_details_path"]).exists()
    assert Path(state["online_refactor_attempts_path"]).exists()
    assert Path(state["overlap_state_path"]).exists()
    assert state["train_details_preview"][0]["task_id"] == "train_1"


def test_restore_current_round_state_uses_sidecar_paths(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    skill = SkillArtifact(name="skill_a", kind="atomic_tool_rule_card", description="desc", body="body")
    index = SegmentVectorIndex(strict_embeddings=False)
    index.load_rows(
        [
            {
                "segment_id": "r0:seg0",
                "base_segment_id": "seg0",
                "task_id": "train_1",
                "round": 0,
                "turn_index": 0,
                "text": "x",
                "error_text": "",
                "embedding": None,
                "metadata": {},
            }
        ]
    )
    current_round_state = {
        "round_index": 1,
        "next_task_index": 4,
        "train_details": [{"task_id": "train_1", "runs": []}],
        "online_refactor_attempts": [{"after_task_id": "train_1", "report": {"attempts": []}}],
        "extraction_events": [{"skill_name": "skill_a", "source_task_id": "train_1"}],
        "role_feedback": {
            "extractor": {
                "rules": [{"rule_id": "x", "text": "Keep scope narrow.", "focus": "scope"}],
                "history": [{"round_index": 0, "summary": "init"}],
            }
        },
        "seen_refactor_cliques": [],
        "online_refactor_budget_remaining": 0,
        "overlap_state": OverlapGraphState(),
    }
    _write_current_round_sidecars(
        checkpoint_path=checkpoint,
        current_round_state=current_round_state,
        store=ArtifactStore([skill]),
        segment_index=index,
    )
    projected = _checkpoint_payload(
        tag="tag",
        rounds=3,
        round_reports=[],
        store=type("StoreWrap", (), {"all": lambda self: [skill], "test_results": lambda self: []})(),
        segment_index=index,
        next_round_index=1,
        current_round_state=current_round_state,
        role_feedback=current_round_state["role_feedback"],
        output_detail_level="compact",
        checkpoint_path=checkpoint,
    )["current_round_state"]
    restored = _restore_current_round_state(projected)
    assert restored["round_index"] == 1
    assert restored["next_task_index"] == 4
    assert restored["train_details"][0]["task_id"] == "train_1"
    assert restored["online_refactor_attempts"][0]["after_task_id"] == "train_1"
    assert restored["store_snapshot"]["artifacts"][0]["name"] == "skill_a"
    assert restored["segment_index_rows"][0]["segment_id"] == "r0:seg0"
    assert restored["role_feedback"]["extractor"]["rules"][0]["text"] == "Keep scope narrow."
    assert isinstance(restored["overlap_state"], OverlapGraphState)


def test_restore_current_round_state_keeps_round_zero_resume_marker(tmp_path: Path) -> None:
    details_path = tmp_path / "details.json"
    details_path.write_text(
        json.dumps(
            {
                "details": [
                    {
                        "task_id": "train_1",
                        "runs": [],
                    }
                ]
            }
        )
    )
    store_path = tmp_path / "store.json"
    store_path.write_text(json.dumps({"artifacts": [{"name": "skill_a"}], "test_results": []}))
    rows_path = tmp_path / "rows.json"
    rows_path.write_text(json.dumps([{"segment_id": "r0:seg0", "task_id": "train_1"}]))

    restored = _restore_current_round_state(
        {
            "round_index": 0,
            "next_task_index": 7,
            "train_details_path": str(details_path),
            "store_snapshot_path": str(store_path),
            "segment_index_rows_path": str(rows_path),
        }
    )

    assert restored is not None
    assert restored["round_index"] == 0
    assert restored["next_task_index"] == 7
    assert restored["train_details"][0]["task_id"] == "train_1"
    assert restored["store_snapshot"]["artifacts"][0]["name"] == "skill_a"
    assert restored["segment_index_rows"][0]["segment_id"] == "r0:seg0"


def test_rebuild_checkpoint_from_sidecars(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    skill = SkillArtifact(name="skill_a", kind="atomic_tool_rule_card", description="desc", body="body")
    index = SegmentVectorIndex(strict_embeddings=False)
    index.load_rows(
        [
            {
                "segment_id": "r0:seg0",
                "base_segment_id": "seg0",
                "task_id": "train_1",
                "round": 0,
                "turn_index": 0,
                "text": "x",
                "error_text": "",
                "embedding": None,
                "metadata": {},
            }
        ]
    )
    current_round_state = {
        "round_index": 0,
        "next_task_index": 2,
        "train_details": [{"task_id": "train_1", "runs": []}, {"task_id": "train_2", "runs": []}],
        "online_refactor_attempts": [{"after_task_id": "train_2", "report": {"attempts": []}}],
        "extraction_events": [{"skill_name": "skill_a", "source_task_id": "train_1"}],
        "role_feedback": {
            "extractor": {
                "rules": [{"rule_id": "x", "text": "Anchor skills to local evidence.", "focus": "evidence"}],
                "history": [],
            }
        },
        "seen_refactor_cliques": [["s1", "s2"]],
        "online_refactor_budget_remaining": 1,
        "overlap_state": OverlapGraphState(),
    }
    _write_current_round_sidecars(
        checkpoint_path=checkpoint,
        current_round_state=current_round_state,
        store=ArtifactStore([skill]),
        segment_index=index,
    )
    payload = rebuild_checkpoint_from_sidecars(
        checkpoint_path=checkpoint,
        tag="tag",
        rounds=3,
        output_detail_level="compact",
    )
    state = payload["current_round_state"]
    assert payload["next_round_index"] == 0
    assert state["n_train_details"] == 2
    assert state["n_online_refactor_attempts"] == 1
    assert state["train_details_path"].endswith("checkpoint_current_round_details.json")
    assert payload["store"]["artifacts"][0]["name"] == "skill_a"
    assert payload["segment_index_rows"][0]["segment_id"] == "r0:seg0"
    assert state["n_store_artifacts"] == 1
    assert state["n_segment_index_rows"] == 1


def test_role_feedback_normalization_caps_rules_and_reindexes() -> None:
    raw = {
        "extractor": {
            "rules": [
                {"rule_id": "a", "text": "Keep scope narrow.", "focus": "scope"},
                {"rule_id": "b", "text": "Keep scope narrow.", "focus": "scope"},
                {"rule_id": "c", "text": "Prefer exact parameter names.", "focus": "contract"},
                {"rule_id": "d", "text": "Extract multiple narrow skills for independent failures.", "focus": "reuse"},
                {"rule_id": "e", "text": "Avoid benchmark-summary artifacts.", "focus": "anti_pattern"},
                {"rule_id": "f", "text": "Use expected calls to anchor local rules.", "focus": "evidence"},
                {"rule_id": "g", "text": "Another extra rule.", "focus": "evidence"},
            ],
            "history": [{"round_index": i} for i in range(20)],
        }
    }
    normalized = _normalize_role_feedback_memory(raw)
    projected = _role_feedback_projection(raw)
    assert len(normalized["extractor"]["rules"]) == 5
    assert normalized["extractor"]["rules"][0]["rule_id"] == "extractor_rule_1"
    assert len(normalized["extractor"]["history"]) == 12
    assert projected["extractor"]["n_rules"] == 5
    assert projected["refactorer"]["n_rules"] == 0
    assert projected["refiner"]["n_rules"] == 0


def test_role_feedback_normalization_keeps_refiner_and_refactorer() -> None:
    raw = {
        "refiner": {
            "rules": [{"text": "Narrow triggers when extra calls appear.", "focus": "strictness"}],
            "history": [{"round_index": 1}],
        },
        "refactorer": {
            "rules": [{"text": "Do not merge skills with different argument schemas.", "focus": "merge_guard"}],
            "history": [{"round_index": 2}],
        },
    }
    projected = _role_feedback_projection(raw)
    assert projected["refiner"]["rules"][0]["rule_id"] == "refiner_rule_1"
    assert projected["refactorer"]["rules"][0]["rule_id"] == "refactorer_rule_1"


def test_build_extractor_feedback_rows_summarizes_harm_and_bundle_failures() -> None:
    extraction_events = [
        {
            "skill_name": "skill_a",
            "skill_version": 1,
            "source_task_ids": ["train_1"],
            "round_index": 0,
            "description": "desc",
            "allowed_tools": ["tool_a"],
        },
        {
            "skill_name": "skill_b",
            "skill_version": 1,
            "source_task_ids": ["train_2"],
            "round_index": 0,
            "description": "desc b",
            "allowed_tools": ["tool_b"],
        },
    ]
    train_details = [
        {
            "task_id": "train_1",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "retrieved_skills": ["skill_a"],
                        "prompt_injected_skills": ["skill_a"],
                        "tool_injected_skills": [],
                        "used_skills": ["skill_a"],
                        "called_skill_tools": [],
                        "call_errors": [{"type": "argument_mismatch"}],
                    }
                }
            ],
        },
        {
            "task_id": "train_2",
            "runs": [
                {
                    "metrics": {
                        "official_valid": True,
                        "retrieved_skills": ["skill_b"],
                        "prompt_injected_skills": ["skill_b"],
                        "tool_injected_skills": [],
                        "used_skills": ["skill_b"],
                        "called_skill_tools": [],
                        "call_errors": [],
                    }
                }
            ],
        },
    ]
    maintenance_test_results = [
        {"skill_name": "skill_a", "aggregate": {"passed": False, "failure_reason": "runtime error"}},
        {"skill_name": "skill_b", "aggregate": {"passed": True}},
    ]
    rows = _build_extractor_feedback_rows(
        extraction_events=extraction_events,
        train_details=train_details,
        maintenance_test_results=maintenance_test_results,
    )
    by_name = {row["skill_name"]: row for row in rows}
    assert by_name["skill_a"]["hurt_valid_count"] == 1
    assert by_name["skill_a"]["bundle_failures"] == 1
    assert by_name["skill_a"]["error_types"] == ["argument_mismatch"]
    assert by_name["skill_b"]["helped_valid_count"] == 1


def test_credit_helpers_build_summary_and_disable_only_strongly_negative_skill() -> None:
    detail = {
        "task_id": "train_1",
        "runs": [
            {
                "score": 0.0,
                "metrics": {
                    "official_valid": False,
                    "retrieved_skills": ["skill_a", "skill_b"],
                    "prompt_injected_skills": ["skill_a", "skill_b"],
                    "tool_injected_skills": [],
                    "used_skills": ["skill_a"],
                    "called_skill_tools": [],
                    "n_model_steps": 4,
                    "total_tokens": 50,
                },
            }
        ],
    }
    assert _mentioned_skill_names(detail) == ["skill_a", "skill_b"]
    credit_payload = {
        "task_summary": {"task_id": "train_1", "official_valid": False, "score": 0.0, "n_model_steps": 4, "total_tokens": 50},
        "skill_judgments": [
            {
                "skill_name": "skill_a",
                "judgment": "harmful",
                "effect_type": "workflow_pollution",
                "confidence": 0.9,
                "reason": "pushed the trace toward a wrong schema",
                "evidence": {"retrieved": True, "injected": True, "used": True, "trace_signals": ["domain mismatch"], "relevant_turn_indices": [0]},
            },
            {
                "skill_name": "skill_b",
                "judgment": "helpful",
                "effect_type": "schema_help",
                "confidence": 0.7,
                "reason": "supported the correct argument binding",
                "evidence": {"retrieved": True, "injected": True, "used": False, "relevant_turn_indices": [0]},
            },
        ],
    }
    rows = _credit_event_records(
        detail=detail,
        credit_payload=credit_payload,
        round_index=0,
        task_index=0,
    )
    store = ArtifactStore(
        [
            SkillArtifact(name="skill_a", kind="workflow_guardrail_card", description="a", body="a"),
            SkillArtifact(name="skill_b", kind="atomic_tool_rule_card", description="b", body="b"),
        ]
    )
    summary = _aggregate_skill_credit(
        rows
        + [
            {
                **rows[0],
                "task_id": "train_2",
                "round_index": 0,
                "task_index": 1,
                "confidence": 0.8,
            }
        ],
        store=store,
    )
    by_name = {row["skill_name"]: row for row in summary}
    assert by_name["skill_a"]["harmful_count"] == 2
    assert by_name["skill_a"]["negative_margin"] == 2
    assert by_name["skill_b"]["helpful_count"] == 1
    decisions = _apply_skill_credit_filter(store=store, credit_summary=summary, threshold=2)
    skill_a = store.get("skill_a")
    skill_b = store.get("skill_b")
    assert skill_a is not None and skill_a.is_disabled() is True
    assert skill_a.metadata["disabled_reason"] == "credit_assignment_negative_margin"
    assert skill_b is not None and skill_b.is_disabled() is False
    disabled = [row for row in decisions if row["action"] == "disabled"]
    assert [row["skill_name"] for row in disabled] == ["skill_a"]


def test_bfcl_credit_events_use_common_normalization_and_evidence_limits() -> None:
    detail = {
        "task_id": "train_alias",
        "runs": [
            {
                "score": 1.0,
                "metrics": {
                    "official_valid": True,
                    "retrieved_skills": ["skill_a"],
                    "prompt_injected_skills": ["skill_a"],
                    "tool_injected_skills": [],
                    "used_skills": [],
                    "called_skill_tools": [],
                    "n_model_steps": 2,
                    "total_tokens": 20,
                },
            }
        ],
    }
    rows = _credit_event_records(
        detail=detail,
        credit_payload={
            "task_summary": {"official_valid": True, "score": 1.0},
            "skill_judgments": [
                {
                    "skill_name": "skill_a",
                    "judgment": "positive",
                    "effect_type": "schema_help",
                    "confidence": "0.8",
                    "reason": "helped schema",
                    "bundle_case_suggestions": [{"polarity": "positive"}],
                }
            ],
        },
        round_index=1,
        task_index=2,
    )
    store = ArtifactStore([SkillArtifact(name="skill_a", kind="atomic_tool_rule_card", description="a", body="a")])
    many_rows = [
        {**rows[0], "task_id": f"task_{idx}", "judgment": "helpful", "confidence": 0.8}
        for idx in range(30)
    ]

    _apply_credit_case_evidence(store=store, credit_events=many_rows)
    _apply_credit_case_evidence(store=store, credit_events=many_rows)

    assert rows[0]["judgment"] == "helpful"
    assert rows[0]["benchmark"] == "bfcl_v3"
    assert rows[0]["source"] == "credit_assigner"
    assert rows[0]["event_index"] == 0
    assert store.get("skill_a").evidence.helpful_cases[0]["task_id"] == "task_18"
    assert store.get("skill_a").evidence.helpful_cases[-1]["task_id"] == "task_29"
    assert len(store.get("skill_a").evidence.helpful_cases) == 12


def test_bfcl_micro_write_targets_use_common_target_ordering() -> None:
    targets = _micro_write_target_names(
        task_credit_events=[
            {"skill_name": "skill_weak", "judgment": "harmful", "confidence": 0.2},
            {"skill_name": "skill_strong", "judgment": "harmful", "confidence": 0.8},
            {
                "skill_name": "skill_helpful",
                "judgment": "helpful",
                "confidence": 0.9,
                "helpful_reasons": ["schema_help"],
            },
        ],
        credit_bundle_cases=[
            {"skill_name": "skill_bundle", "case_id": "case_1"},
            {"skill_name": "skill_strong", "case_id": "case_2"},
        ],
        relevant_skill_names=["skill_strong", "skill_helpful"],
    )

    assert targets == ["skill_strong", "skill_helpful", "skill_bundle"]


def test_bfcl_credit_candidates_exclude_retrieved_only_skill() -> None:
    detail = {
        "task_id": "train_scope",
        "runs": [
            {
                "score": 1.0,
                "metrics": {
                    "official_valid": True,
                    "retrieved_skills": ["retrieved_only", "prompt_skill", "tool_skill", "used_skill"],
                    "prompt_injected_skills": ["prompt_skill"],
                    "tool_injected_skills": ["tool_skill"],
                    "used_skills": ["used_skill"],
                    "called_skill_tools": ["tool_skill"],
                },
                "trace": {
                    "retrieved_skills": ["retrieved_only", "prompt_skill", "tool_skill", "used_skill"],
                    "prompt_injected_skills": ["prompt_skill"],
                    "tool_injected_skills": ["tool_skill"],
                    "called_skill_tools": ["tool_skill"],
                },
            }
        ],
    }

    assert _mentioned_skill_names(detail) == ["prompt_skill", "tool_skill", "used_skill"]
    assert _retrieved_only_skill_names(detail) == ["retrieved_only"]


def test_credit_filter_protects_positive_skill_and_requests_scope_refine() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(
                name="vehicle_engine_start_brake_requirement",
                kind="workflow_guardrail_card",
                description="Brake before engine start.",
                body="Call pressBrakePedal before startEngine.",
                metadata={"domains": ["VehicleControlAPI"], "allowed_tools": ["pressBrakePedal", "startEngine"]},
            )
        ]
    )
    events: List[Dict[str, Any]] = []
    for idx in range(2):
        events.append(
            {
                "skill_name": "vehicle_engine_start_brake_requirement",
                "task_id": f"vehicle_positive_{idx}",
                "judgment": "helpful",
                "effect_type": "workflow_alignment",
                "confidence": 0.9,
                "retrieved": True,
                "injected": True,
                "used": False,
                "evidence": {"trace_signals": ["VehicleControlAPI engine start followed the brake prerequisite"]},
            }
        )
    for idx in range(8):
        events.append(
            {
                "skill_name": "vehicle_engine_start_brake_requirement",
                "task_id": f"travel_negative_{idx}",
                "judgment": "harmful",
                "effect_type": "domain_mismatch",
                "confidence": 0.85,
                "retrieved": True,
                "injected": True,
                "used": False,
                "evidence": {"trace_signals": ["domain mismatch: TravelAPI task received VehicleControlAPI skill"]},
            }
        )

    summary = _aggregate_skill_credit(events, store=store)
    decisions = _apply_skill_credit_filter(store=store, credit_summary=summary, threshold=2)
    skill = store.get("vehicle_engine_start_brake_requirement")

    assert skill is not None
    assert skill.is_disabled() is False
    assert skill.metadata["retrieval_scope_refine_required"] is True
    assert decisions[0]["action"] == "protected_refine_scope"
    assert decisions[0]["helpful_count"] == 2
    assert decisions[0]["harmful_count"] == 8


async def test_run_related_evolve_experiment_rejects_incomplete_resume_state(monkeypatch, tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    current_round_state = {
        "round_index": 0,
        "next_task_index": 1,
        "train_details": [{"task_id": "train_1", "runs": []}],
        "online_refactor_attempts": [],
        "seen_refactor_cliques": [],
        "online_refactor_budget_remaining": 1,
    }
    _write_current_round_sidecars(
        checkpoint_path=checkpoint,
        current_round_state=current_round_state,
    )
    checkpoint.write_text(
        json.dumps(
            {
                "checkpoint_version": 1,
                "tag": "tag",
                "rounds_total": 3,
                "next_round_index": 0,
                "output_detail_level": "compact",
                "round_reports": [],
                "store": {"artifacts": [], "test_results": []},
                "segment_index_rows": [],
                "current_round_state": {
                    "round_index": 0,
                    "next_task_index": 1,
                    "seen_refactor_cliques": [],
                    "online_refactor_budget_remaining": 1,
                    "train_details_path": str(checkpoint.with_name("checkpoint_current_round_details.json")),
                    "online_refactor_attempts_path": str(checkpoint.with_name("checkpoint_current_round_online_refactors.json")),
                },
            },
            ensure_ascii=False,
        )
    )

    manifest = {"train_task_ids": ["train_1"], "test_task_ids": ["test_1"]}
    fake_task = type("Task", (), {"task_id": "train_1", "metadata": {}})()
    fake_test_task = type("Task", (), {"task_id": "test_1", "metadata": {}})()
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: ([fake_task], [fake_test_task]),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline",
        AsyncMock(return_value=[]),
    )
    with pytest.raises(RuntimeError, match="missing evolving store or segment-index state"):
        await _run_related_evolve_experiment(
            manifest=manifest,
            cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
            llm_config="local_claude_proxy",
            model_name="claude-sonnet-4-5",
            tools=[],
            rounds=3,
            data_source="bfcl_eval_bundle",
            execution_backend="official",
            prompt_style="native",
            tool_api_style="auto",
            top_k_skills=2,
            min_skill_score=0.0,
            skill_injection_mode="prompt_only",
            max_steps_per_turn=12,
            max_task_seconds=240.0,
            temperature=None,
            synthetic_continue=False,
            explicit_skill_tool=False,
            tag="tag",
            save_skills=None,
            use_handwritten_skills=False,
            checkpoint_path=checkpoint,
            output_path=None,
            output_detail_level="compact",
            extractor_trl_enabled=False,
            experiment_variant="test",
        )


async def test_run_related_evolve_experiment_prefetches_train_window_concurrently(monkeypatch, tmp_path: Path) -> None:
    train_tasks = [type("Task", (), {"task_id": f"train_{idx}", "metadata": {}})() for idx in range(4)]
    test_tasks = [type("Task", (), {"task_id": "test_1", "metadata": {}})()]
    manifest = {
        "train_task_ids": [task.task_id for task in train_tasks],
        "test_task_ids": ["test_1"],
    }
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: (train_tasks, test_tasks),
    )
    calls: List[Dict[str, Any]] = []

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        calls.append({"task_ids": [task.task_id for task in tasks], "concurrency": kwargs.get("concurrency"), "phase": kwargs.get("phase")})
        return [
            {
                "task_id": task.task_id,
                "task": {"task_id": task.task_id, "metadata": {}},
                "n_runs": 1,
                "n_success": 1,
                "avg_score": 1.0,
                "runs": [
                    {
                        "task_id": task.task_id,
                        "success": True,
                        "score": 1.0,
                        "metrics": {
                            "official_valid": True,
                            "retrieved_skills": [],
                            "prompt_injected_skills": [],
                            "tool_injected_skills": [],
                            "used_skills": [],
                            "called_skill_tools": [],
                            "call_errors": [],
                            "n_model_steps": 1,
                            "total_tokens": 10,
                        },
                        "trace": {"tool_calls": [], "turns": [], "messages": [], "debug_events": []},
                        "run_idx": 0,
                    }
                ],
            }
            for task in tasks
        ]

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline", fake_run_bfcl_baseline)
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.extract_bfcl_skill_artifacts_llm", AsyncMock(return_value=[]))
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._extract_task_segments", lambda detail: [])
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_macro_maintenance",
        AsyncMock(return_value={
            "maintenance_targets": [],
            "maintenance_test_results": [],
            "refine_decisions": [],
            "overlap_refactor": {"attempts": [], "refactor_segment_coverage": []},
            "refactor_segment_coverage": [],
            "static_dependency_validation": [],
            "token_breakdown": {},
        }),
    )

    payload = await _run_related_evolve_experiment(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        rounds=1,
        data_source="bfcl_eval_bundle",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        top_k_skills=2,
        min_skill_score=0.0,
        skill_injection_mode="prompt_only",
        max_steps_per_turn=12,
        max_task_seconds=30.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        tag="tag",
        save_skills=None,
        use_handwritten_skills=False,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=False,
        experiment_variant="test_window_concurrency",
        macro_maintenance_step=2,
        train_window_concurrency=4,
    )

    train_calls = [call for call in calls if str(call["phase"]).startswith("related_train_epoch_")]
    assert [call["task_ids"] for call in train_calls] == [["train_0", "train_1"], ["train_2", "train_3"]]
    assert [call["concurrency"] for call in train_calls] == [4, 4]
    assert payload["config_summary"]["train_window_concurrency"] == 4
    assert payload["rounds"][0]["train_details"][0]["task_id"] == "train_0"
    assert payload["rounds"][0]["train_details"][-1]["task_id"] == "train_3"


async def test_run_related_evolve_experiment_emits_role_feedback(monkeypatch, tmp_path: Path) -> None:
    manifest = {"train_task_ids": ["train_1"], "test_task_ids": ["test_1"]}
    fake_train_task = type("Task", (), {"task_id": "train_1", "metadata": {}})()
    fake_test_task = type("Task", (), {"task_id": "test_1", "metadata": {}})()
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: ([fake_train_task], [fake_test_task]),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.default_bfcl_skill_store",
        lambda: ArtifactStore([SkillArtifact(name="skill_a", kind="atomic_tool_rule_card", description="seed", body="seed")]),
    )

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        details = []
        phase = kwargs.get("phase")
        for task in tasks:
            valid = phase == "related_heldout_test"
            details.append(
                {
                    "task_id": task.task_id,
                    "task": {"task_id": task.task_id, "metadata": {}},
                    "n_runs": 1,
                    "n_success": 1 if valid else 0,
                    "avg_score": 1.0 if valid else 0.0,
                    "runs": [
                        {
                            "task_id": task.task_id,
                            "success": valid,
                            "score": 1.0 if valid else 0.0,
                            "metrics": {
                                "official_valid": valid,
                                "retrieved_skills": ["skill_a"] if phase != "related_heldout_test" else ["skill_a"],
                                "prompt_injected_skills": ["skill_a"] if phase != "related_heldout_test" else ["skill_a"],
                                "tool_injected_skills": [],
                                "used_skills": ["skill_a"] if phase != "related_heldout_test" else ["skill_a"],
                                "called_skill_tools": [],
                                "call_errors": [] if valid else [{"type": "argument_mismatch"}],
                                "n_model_steps": 3,
                                "total_tokens": 40,
                            },
                            "trace": {
                                "tool_calls": [],
                                "turns": [],
                                "messages": [],
                                "debug_events": [],
                                "retrieved_skills": ["skill_a"],
                                "prompt_injected_skills": ["skill_a"],
                                "tool_injected_skills": [],
                                "called_skill_tools": [],
                                "turn_step_counts": [3],
                                "n_model_steps": 3,
                                "total_tokens": 40,
                            },
                            "error": None,
                            "run_idx": 0,
                        }
                    ],
                }
            )
        return details

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline",
        fake_run_bfcl_baseline,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.extract_bfcl_skill_artifacts_llm",
        AsyncMock(return_value=[SkillArtifact(name="skill_a", kind="atomic_tool_rule_card", description="desc", body="body")]),
    )
    credit_mock = AsyncMock(
        return_value={
            "task_summary": {"task_id": "train_1", "official_valid": False, "score": 0.0, "n_model_steps": 3, "total_tokens": 40},
            "skill_judgments": [
                {
                    "skill_name": "skill_a",
                    "judgment": "harmful",
                    "effect_type": "schema_harm",
                    "confidence": 0.8,
                    "reason": "encouraged a wrong argument pattern",
                    "evidence": {"retrieved": True, "injected": True, "used": True, "relevant_turn_indices": [0]},
                }
            ],
        }
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.assign_skill_credit_llm",
        credit_mock,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._extract_task_segments",
        lambda detail: [],
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_round_refine_and_refactor",
        AsyncMock(
            return_value={
                "maintenance_targets": ["skill_a"],
                "maintenance_test_results": [{"skill_name": "skill_a", "aggregate": {"passed": False, "failure_reason": "runtime error"}}],
                "refine_decisions": [],
                "overlap_refactor": {"attempts": []},
            }
        ),
    )
    update_mock = AsyncMock(
        return_value={
            "summary": "Prefer narrower contract-anchored skills.",
            "rules": [{"rule_id": "extractor_rule_1", "text": "Prefer exact local contract rules.", "focus": "contract"}],
            "updated_at": "2026-05-15T00:00:00+00:00",
        }
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.update_extractor_rules_from_feedback_llm",
        update_mock,
    )

    payload = await _run_related_evolve_experiment(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        rounds=1,
        data_source="bfcl_eval_bundle",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        top_k_skills=2,
        min_skill_score=0.0,
        skill_injection_mode="prompt_only",
        max_steps_per_turn=12,
        max_task_seconds=30.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        tag="tag",
        save_skills=None,
        use_handwritten_skills=True,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=True,
        experiment_variant="w_extractor_reusage_trl",
    )
    assert payload["role_feedback"]["extractor"]["n_rules"] == 0
    assert payload["rounds"][0]["role_feedback"]["extractor"]["n_rules"] == 0
    assert payload["rounds"][0]["role_feedback"]["extractor"]["last_update_summary"] == "extractor_trl_candidate_group_only:round_single_skill_feedback_logged_not_used"
    assert payload["rounds"][0]["extractor_feedback_rows"][0]["skill_name"] == "skill_a"
    assert payload["rounds"][0]["credit_events"][0]["skill_name"] == "skill_a"
    assert payload["rounds"][0]["skill_credit_summary"][0]["skill_name"] == "skill_a"
    assert payload["skill_credit_events"][0]["judgment"] == "harmful"
    assert payload["skill_credit_summary"][0]["harmful_count"] == 1
    credit_mock.assert_awaited()
    update_mock.assert_not_called()


async def test_run_related_evolve_experiment_can_disable_extractor_trl(monkeypatch, tmp_path: Path) -> None:
    manifest = {"train_task_ids": ["train_1"], "test_task_ids": ["test_1"]}
    fake_train_task = type("Task", (), {"task_id": "train_1", "metadata": {}})()
    fake_test_task = type("Task", (), {"task_id": "test_1", "metadata": {}})()
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: ([fake_train_task], [fake_test_task]),
    )

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        details = []
        for task in tasks:
            details.append(
                {
                    "task_id": task.task_id,
                    "task": {"task_id": task.task_id, "metadata": {}},
                    "n_runs": 1,
                    "n_success": 0,
                    "avg_score": 0.0,
                    "runs": [
                        {
                            "task_id": task.task_id,
                            "success": False,
                            "score": 0.0,
                            "metrics": {
                                "official_valid": False,
                                "retrieved_skills": ["skill_a"],
                                "prompt_injected_skills": ["skill_a"],
                                "tool_injected_skills": [],
                                "used_skills": ["skill_a"],
                                "called_skill_tools": [],
                                "call_errors": [{"type": "argument_mismatch"}],
                                "n_model_steps": 3,
                                "total_tokens": 40,
                            },
                            "trace": {
                                "tool_calls": [],
                                "turns": [],
                                "messages": [],
                                "debug_events": [],
                                "retrieved_skills": ["skill_a"],
                                "prompt_injected_skills": ["skill_a"],
                                "tool_injected_skills": [],
                                "called_skill_tools": [],
                                "turn_step_counts": [3],
                                "n_model_steps": 3,
                                "total_tokens": 40,
                            },
                            "error": None,
                            "run_idx": 0,
                        }
                    ],
                }
            )
        return details

    extract_calls = []

    async def fake_extract(*args, **kwargs):
        extract_calls.append(kwargs.get("extractor_rules"))
        return [SkillArtifact(name="skill_a", kind="atomic_tool_rule_card", description="desc", body="body")]

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline",
        fake_run_bfcl_baseline,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.extract_bfcl_skill_artifacts_llm",
        fake_extract,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.assign_skill_credit_llm",
        AsyncMock(
            return_value={
                "task_summary": {"task_id": "train_1", "official_valid": False, "score": 0.0, "n_model_steps": 3, "total_tokens": 40},
                "skill_judgments": [
                    {
                        "skill_name": "skill_a",
                        "judgment": "harmful",
                        "effect_type": "workflow_pollution",
                        "confidence": 0.9,
                        "reason": "prompt-only pollution",
                        "evidence": {"retrieved": True, "injected": True, "used": True, "trace_signals": ["domain mismatch"]},
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._extract_task_segments",
        lambda detail: [],
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_round_refine_and_refactor",
        AsyncMock(
            return_value={
                "maintenance_targets": ["skill_a"],
                "maintenance_test_results": [{"skill_name": "skill_a", "aggregate": {"passed": False, "failure_reason": "runtime error"}}],
                "refine_decisions": [],
                "overlap_refactor": {"attempts": []},
            }
        ),
    )
    update_mock = AsyncMock(
        return_value={
            "summary": "should_not_run",
            "rules": [{"rule_id": "extractor_rule_1", "text": "bad", "focus": "scope"}],
            "updated_at": "2026-05-15T00:00:00+00:00",
        }
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.update_extractor_rules_from_feedback_llm",
        update_mock,
    )

    payload = await _run_related_evolve_experiment(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        rounds=1,
        data_source="bfcl_eval_bundle",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        top_k_skills=2,
        min_skill_score=0.0,
        skill_injection_mode="prompt_only",
        max_steps_per_turn=12,
        max_task_seconds=30.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        tag="tag",
        save_skills=None,
        use_handwritten_skills=False,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=False,
        experiment_variant="wo_extractor_reusage_trl",
    )
    assert extract_calls == [[]]
    update_mock.assert_not_called()
    assert payload["experiment_variant"] == "wo_extractor_reusage_trl"
    assert payload["config_summary"]["extractor_trl_enabled"] is False
    assert payload["rounds"][0]["extractor_feedback_rows"][0]["skill_name"] == "skill_a"
    assert payload["rounds"][0]["role_feedback"]["extractor"]["last_update_summary"] == "extractor_trl_disabled:no_rule_update"


async def test_run_related_evolve_experiment_applies_credit_filter(monkeypatch, tmp_path: Path) -> None:
    manifest = {"train_task_ids": ["train_1", "train_2"], "test_task_ids": ["test_1"]}
    fake_train_tasks = [type("Task", (), {"task_id": "train_1", "metadata": {}})(), type("Task", (), {"task_id": "train_2", "metadata": {}})()]
    fake_test_tasks = [type("Task", (), {"task_id": "test_1", "metadata": {}})()]
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: (fake_train_tasks, fake_test_tasks),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.default_bfcl_skill_store",
        lambda: ArtifactStore([SkillArtifact(name="skill_bad", kind="workflow_guardrail_card", description="seed", body="seed")]),
    )

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        phase = kwargs.get("phase")
        details = []
        for task in tasks:
            valid = phase == "related_heldout_test"
            details.append(
                {
                    "task_id": task.task_id,
                    "task": {"task_id": task.task_id, "metadata": {}},
                    "n_runs": 1,
                    "n_success": 1 if valid else 0,
                    "avg_score": 1.0 if valid else 0.0,
                    "runs": [
                        {
                            "task_id": task.task_id,
                            "success": valid,
                            "score": 1.0 if valid else 0.0,
                            "metrics": {
                                "official_valid": valid,
                                "retrieved_skills": ["skill_bad"],
                                "prompt_injected_skills": ["skill_bad"],
                                "tool_injected_skills": [],
                                "used_skills": ["skill_bad"],
                                "called_skill_tools": [],
                                "call_errors": [] if valid else [{"type": "argument_mismatch"}],
                                "n_model_steps": 3,
                                "total_tokens": 40,
                            },
                            "trace": {
                                "tool_calls": [],
                                "turns": [],
                                "messages": [],
                                "debug_events": [],
                                "retrieved_skills": ["skill_bad"],
                                "prompt_injected_skills": ["skill_bad"],
                                "tool_injected_skills": [],
                                "called_skill_tools": [],
                                "turn_step_counts": [3],
                                "n_model_steps": 3,
                                "total_tokens": 40,
                            },
                            "error": None,
                            "run_idx": 0,
                        }
                    ],
                }
            )
        return details

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline",
        fake_run_bfcl_baseline,
    )
    extract_mock = AsyncMock(
        return_value=[SkillArtifact(name="skill_bad", kind="workflow_guardrail_card", description="desc", body="body")]
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.extract_bfcl_skill_artifacts_llm",
        extract_mock,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.assign_skill_credit_llm",
        AsyncMock(
            return_value={
                "task_summary": {"official_valid": False, "score": 0.0, "n_model_steps": 3, "total_tokens": 40},
                "skill_judgments": [
                    {
                        "skill_name": "skill_bad",
                        "judgment": "harmful",
                        "effect_type": "workflow_pollution",
                        "confidence": 0.95,
                        "reason": "biased the trace off task",
                        "evidence": {"retrieved": True, "injected": True, "used": True},
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._extract_task_segments",
        lambda detail: [],
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_round_refine_and_refactor",
        AsyncMock(
            return_value={
                "maintenance_targets": ["skill_bad"],
                "maintenance_test_results": [],
                "refine_decisions": [],
                "overlap_refactor": {"attempts": []},
            }
        ),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.update_extractor_rules_from_feedback_llm",
        AsyncMock(
            return_value={
                "summary": "keep scope narrow",
                "rules": [{"rule_id": "extractor_rule_1", "text": "Prefer local rules.", "focus": "scope"}],
                "updated_at": "2026-05-15T00:00:00+00:00",
            }
        ),
    )

    payload = await _run_related_evolve_experiment(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        rounds=1,
        data_source="bfcl_eval_bundle",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        top_k_skills=2,
        min_skill_score=0.0,
        skill_injection_mode="prompt_only",
        max_steps_per_turn=12,
        max_task_seconds=30.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        tag="tag",
        save_skills=None,
        use_handwritten_skills=True,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=True,
        experiment_variant="test_credit_filter",
    )
    decisions = payload["skill_credit_filter_decisions"]
    assert any(row["skill_name"] == "skill_bad" and row["action"] == "disabled" for row in decisions)
    skill_rows = {row["name"]: row for row in payload["skills"]}
    assert skill_rows["skill_bad"]["status"] == "disabled"
    assert skill_rows["skill_bad"]["metadata"]["disabled_reason"] == "credit_assignment_negative_margin"
    assert extract_mock.await_args.kwargs["existing_artifacts"] == []
    pending_conflicts = [
        row
        for row in payload["skills"]
        if row["metadata"].get("candidate_for_existing_skill") == "skill_bad"
    ]
    assert pending_conflicts
    assert all(row["name"].startswith("skill_bad__pending_") for row in pending_conflicts)


async def test_run_related_evolve_experiment_defaults_online_refactor_budget_to_zero(monkeypatch, tmp_path: Path) -> None:
    manifest = {"train_task_ids": [], "test_task_ids": []}
    monkeypatch.delenv("BFCL_ONLINE_REFACTOR_MAX_PER_ROUND", raising=False)
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: ([], []),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.default_bfcl_skill_store",
        lambda: ArtifactStore([]),
    )
    payload = await _run_related_evolve_experiment(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        rounds=1,
        data_source="bfcl_eval_bundle",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        top_k_skills=2,
        min_skill_score=0.0,
        skill_injection_mode="prompt_only",
        max_steps_per_turn=12,
        max_task_seconds=30.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        tag="tag",
        save_skills=None,
        use_handwritten_skills=False,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=False,
        experiment_variant="test_online_default",
    )
    assert payload["rounds"][0]["online_refactor_attempts"] == []


async def test_run_related_evolve_experiment_never_runs_online_refactor_even_if_env_enabled(monkeypatch, tmp_path: Path) -> None:
    manifest = {"train_task_ids": ["train_1", "train_2", "train_3"], "test_task_ids": []}
    fake_tasks = [type("Task", (), {"task_id": f"train_{idx}", "metadata": {}})() for idx in range(1, 4)]
    monkeypatch.setenv("BFCL_ONLINE_REFACTOR_MAX_PER_ROUND", "99")
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: (fake_tasks, []),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.default_bfcl_skill_store",
        lambda: ArtifactStore([]),
    )

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        details = []
        for task in tasks:
            details.append(
                {
                    "task_id": task.task_id,
                    "task": {"task_id": task.task_id, "metadata": {}},
                    "n_runs": 1,
                    "n_success": 1,
                    "avg_score": 1.0,
                    "runs": [
                        {
                            "task_id": task.task_id,
                            "success": True,
                            "score": 1.0,
                            "metrics": {
                                "official_valid": True,
                                "retrieved_skills": [],
                                "prompt_injected_skills": [],
                                "tool_injected_skills": [],
                                "used_skills": [],
                                "called_skill_tools": [],
                                "call_errors": [],
                                "n_model_steps": 1,
                                "total_tokens": 10,
                            },
                            "trace": {
                                "tool_calls": [],
                                "turns": [],
                                "messages": [],
                                "debug_events": [],
                                "retrieved_skills": [],
                                "prompt_injected_skills": [],
                                "tool_injected_skills": [],
                                "called_skill_tools": [],
                                "turn_step_counts": [1],
                                "n_model_steps": 1,
                                "total_tokens": 10,
                            },
                            "error": None,
                            "run_idx": 0,
                        }
                    ],
                }
            )
        return details

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline", fake_run_bfcl_baseline)
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.extract_bfcl_skill_artifacts_llm", AsyncMock(return_value=[]))
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.assign_skill_credit_llm", AsyncMock(return_value={"skill_judgments": []}))
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._extract_task_segments", lambda detail: [])
    online_mock = AsyncMock(return_value={"attempts": [{"group_id": "should_not_run"}]})
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.run_bfcl_overlap_refactor_llm", online_mock)
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_round_refine_and_refactor",
        AsyncMock(return_value={"maintenance_targets": [], "maintenance_test_results": [], "refine_decisions": [], "overlap_refactor": {"attempts": []}}),
    )

    payload = await _run_related_evolve_experiment(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        rounds=1,
        data_source="bfcl_eval_bundle",
        execution_backend="official",
        prompt_style="native",
        tool_api_style="auto",
        top_k_skills=2,
        min_skill_score=0.0,
        skill_injection_mode="prompt_only",
        max_steps_per_turn=12,
        max_task_seconds=30.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        tag="tag",
        save_skills=None,
        use_handwritten_skills=False,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=False,
        experiment_variant="test_online_removed",
    )

    online_mock.assert_not_awaited()
    assert payload["rounds"][0]["online_refactor_attempts"] == []
    checkpoint_payload = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint_payload["current_round_state"] is None


def test_default_evolve_output_derives_checkpoint_path() -> None:
    output = _default_output_path("evolve", "smoke_tag")
    checkpoint = _phase_partial_path(output, "checkpoint")
    assert output.name == "bfcl_related50_50_smoke_tag_evolve.json"
    assert checkpoint is not None
    assert checkpoint.name == "bfcl_related50_50_smoke_tag_evolve_checkpoint.json"
