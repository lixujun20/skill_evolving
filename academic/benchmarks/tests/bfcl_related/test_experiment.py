import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from academic.benchmarks.bfcl.related.experiment import (
    _aggregate_skill_credit,
    _apply_skill_credit_filter,
    _build_extractor_feedback_rows,
    _checkpoint_payload,
    _compact_task_detail,
    _credit_event_records,
    _default_output_path,
    _load_saved_details,
    _mentioned_skill_names,
    _mark_prior_artifacts_pending,
    _normalize_role_feedback_memory,
    _pending_skill_summary,
    _phase_partial_path,
    _promote_pending_from_refactor_report,
    _role_feedback_projection,
    _restore_current_round_state,
    _write_current_round_sidecars,
    _run_related_evolve_experiment,
    rebuild_checkpoint_from_sidecars,
    build_analysis_artifacts,
    validate_experiment_config,
)
from academic.benchmarks.bfcl.related.manifest import (
    build_curated_related_task_manifest,
    validate_curated_manifest,
)
from academic.benchmarks.bfcl.related.segment_index import SegmentVectorIndex
from academic.skill_repository.types import SkillArtifact
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
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.update_extractor_rules_from_feedback_llm",
        AsyncMock(
            return_value={
                "summary": "Prefer narrower contract-anchored skills.",
                "rules": [{"rule_id": "extractor_rule_1", "text": "Prefer exact local contract rules.", "focus": "contract"}],
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
        experiment_variant="w_extractor_reusage_trl",
    )
    assert payload["role_feedback"]["extractor"]["n_rules"] == 1
    assert payload["rounds"][0]["role_feedback"]["extractor"]["n_rules"] == 1
    assert payload["rounds"][0]["extractor_feedback_rows"][0]["skill_name"] == "skill_a"
    assert payload["rounds"][0]["credit_events"][0]["skill_name"] == "skill_a"
    assert payload["rounds"][0]["skill_credit_summary"][0]["skill_name"] == "skill_a"
    assert payload["skill_credit_events"][0]["judgment"] == "harmful"
    assert payload["skill_credit_summary"][0]["harmful_count"] == 1
    credit_mock.assert_awaited()


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
