import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.bfcl.maintenance.adapter import (
    _bundle_input_signature,
    _bundle_test_signature,
    _latest_matching_test_result,
    trim_bundle_cases,
)
from academic.benchmarks.bfcl.related.experiment import (
    _checkpoint_payload,
    _restore_current_round_state,
    _write_current_round_sidecars,
    SegmentVectorIndex,
)
from academic.skill_repository.refactor_overlap import (
    OverlapGraphState,
    TraceSegment,
    build_overlap_graph_state,
    discover_overlap_graph,
    materialize_overlap_graph,
    update_overlap_graph_state,
)
from academic.skill_repository.types import SkillArtifact, SkillBundleCase, SkillTestResult


def _sample_segments() -> Dict[str, List[TraceSegment]]:
    return {
        "initial": [
            TraceSegment(
                segment_id="train_a:turn:0",
                task_id="train_a",
                turn_index=0,
                text="explicit stock symbol present call remove_stock_from_watchlist directly",
                error_text="avoid redundant lookup for explicit symbol",
            ),
            TraceSegment(
                segment_id="train_b:turn:0",
                task_id="train_b",
                turn_index=0,
                text="explicit ticker available call remove_stock_from_watchlist directly",
                error_text="avoid extra lookup for explicit ticker",
            ),
        ],
        "new": [
            TraceSegment(
                segment_id="train_c:turn:0",
                task_id="train_c",
                turn_index=0,
                text="ticker already explicit call remove_stock_from_watchlist directly",
                error_text="avoid discovery when identifier is explicit",
            )
        ],
    }


def _sample_artifact() -> SkillArtifact:
    artifact = SkillArtifact(
        name="remove_watchlist_when_symbol_explicit",
        kind="atomic_tool_rule_card",
        description="If the stock symbol is already explicit, call remove_stock_from_watchlist directly.",
        body="When the request already includes the exact stock symbol, do not perform lookup. Call remove_stock_from_watchlist with the symbol as-is.",
        metadata={"source_task_ids": ["train_a", "train_b"]},
    )
    artifact.bundle.positive_cases = [
        SkillBundleCase(case_id="p0", source="manual", prompt="remove NVDA", polarity="positive"),
        SkillBundleCase(case_id="p1", source="manual", prompt="remove TSLA", polarity="positive"),
        SkillBundleCase(case_id="p2", source="manual", prompt="remove AAPL", polarity="positive"),
    ]
    artifact.bundle.negative_cases = [
        SkillBundleCase(case_id="n0", source="manual", prompt="ambiguous company name", polarity="negative"),
        SkillBundleCase(case_id="n1", source="manual", prompt="requires symbol lookup", polarity="negative"),
    ]
    artifact.bundle.integration_cases = [
        SkillBundleCase(case_id="i0", source="manual", prompt="cross-skill failure 1", polarity="negative"),
        SkillBundleCase(case_id="i1", source="manual", prompt="cross-skill failure 2", polarity="negative"),
    ]
    return artifact


def build_runtime_optimization_scenario_report(tmp_path: Path) -> Dict[str, Any]:
    report: Dict[str, Any] = {"scenarios": []}

    segment_groups = _sample_segments()
    full_graph = discover_overlap_graph([*segment_groups["initial"], *segment_groups["new"]], min_weight=0.01, top_k_per_segment=8)
    state = build_overlap_graph_state(segment_groups["initial"])
    added = update_overlap_graph_state(state, new_segments=segment_groups["new"])
    incremental_graph = materialize_overlap_graph(state, min_weight=0.01, top_k_per_segment=8)
    report["scenarios"].append(
        {
            "name": "incremental_overlap_graph",
            "meaning": "New task segments should only update new edges while preserving full-graph semantics.",
            "input": {
                "initial_segments": [segment.as_dict() for segment in segment_groups["initial"]],
                "new_segments": [segment.as_dict() for segment in segment_groups["new"]],
            },
            "output": {
                "added_segments": added,
                "full_edge_pairs": sorted(tuple(sorted((edge.source, edge.target))) for edge in full_graph.edges),
                "incremental_edge_pairs": sorted(tuple(sorted((edge.source, edge.target))) for edge in incremental_graph.edges),
                "state_segment_count": len(state.segments),
                "state_postings_terms": len(state.text_postings),
            },
        }
    )

    store = ArtifactStore()
    original = _sample_artifact()
    store.add(original)
    noop = _sample_artifact()
    store.add(noop)
    current = store.get(original.name)
    report["scenarios"].append(
        {
            "name": "noop_skill_update",
            "meaning": "Semantically unchanged skill extraction must not create a new version or invalidate bundle/test assets.",
            "input": {
                "original_artifact": original.as_dict(),
                "noop_artifact": noop.as_dict(),
            },
            "output": {
                "current_version": current.version if current else None,
                "history_length": len(current.history) if current else None,
                "bundle_version": current.bundle.bundle_version if current else None,
            },
        }
    )

    artifact_for_bundle = _sample_artifact()
    train_details = [
        {"task_id": "train_a", "runs": [{"score": 1.0, "metrics": {"official_valid": True, "call_f1": 1.0, "n_model_steps": 2, "total_tokens": 40}}]},
        {"task_id": "train_b", "runs": [{"score": 1.0, "metrics": {"official_valid": True, "call_f1": 1.0, "n_model_steps": 2, "total_tokens": 42}}]},
    ]
    bundle_sig_before = _bundle_input_signature(
        artifact_for_bundle,
        train_details=train_details,
        replay_details=[],
        integration_failures=[],
    )
    artifact_for_bundle.bundle.fixtures["bundle_input_signature"] = bundle_sig_before
    bundle_sig_after = _bundle_input_signature(
        artifact_for_bundle,
        train_details=train_details,
        replay_details=[],
        integration_failures=[],
    )
    report["scenarios"].append(
        {
            "name": "bundle_signature_reuse",
            "meaning": "Stable bundle evidence should produce the same signature so bundle generation can be skipped safely.",
            "input": {
                "artifact": artifact_for_bundle.as_dict(),
                "train_details": train_details,
                "integration_failures": [],
            },
            "output": {
                "bundle_signature_before": bundle_sig_before,
                "bundle_signature_after": bundle_sig_after,
                "reuse_expected": bundle_sig_before == bundle_sig_after,
            },
        }
    )

    artifact_for_trim = _sample_artifact()
    trim_changed = trim_bundle_cases(artifact_for_trim, per_polarity_limit=3)
    report["scenarios"].append(
        {
            "name": "bundle_total_cap_and_split",
            "meaning": "When bundle cases exceed the total budget, the bundle should be balanced and overflow should be recorded.",
            "input": {
                "artifact_before_trim": _sample_artifact().as_dict(),
                "per_polarity_limit": 3,
            },
            "output": {
                "trim_changed": trim_changed,
                "positive_case_ids": [case.case_id for case in artifact_for_trim.bundle.positive_cases],
                "negative_case_ids": [case.case_id for case in artifact_for_trim.bundle.negative_cases],
                "integration_case_ids": [case.case_id for case in artifact_for_trim.bundle.integration_cases],
                "bundle_fixtures": dict(artifact_for_trim.bundle.fixtures or {}),
                "bundle_version": artifact_for_trim.bundle.bundle_version,
            },
        }
    )

    artifact_for_test_cache = _sample_artifact()
    test_signature = _bundle_test_signature(
        artifact_for_test_cache,
        max_steps_per_turn=6,
        adapter_mode="official",
        execution_backend="official",
        prompt_style="official",
        tool_api_style="openai",
        synthetic_continue=False,
        explicit_skill_tool=False,
    )
    cached_result = SkillTestResult(
        result_id="remove_watchlist_when_symbol_explicit:bundle:cached",
        skill_name=artifact_for_test_cache.name,
        skill_version=artifact_for_test_cache.version,
        bundle_id=artifact_for_test_cache.bundle.bundle_id,
        bundle_version=artifact_for_test_cache.bundle.bundle_version,
        dependency_versions=artifact_for_test_cache.dependency_version_map(),
        run_label="llm_bundle_unit",
        aggregate={"pass_all_tests": True, "test_signature": test_signature},
    )
    cache_store = ArtifactStore([artifact_for_test_cache], test_results=[cached_result])
    reused = _latest_matching_test_result(cache_store, artifact=artifact_for_test_cache, test_signature=test_signature)
    report["scenarios"].append(
        {
            "name": "bundle_test_cache_reuse",
            "meaning": "Identical skill+bundle+execution config should reuse prior unit-test results instead of rerunning the executor.",
            "input": {
                "artifact": artifact_for_test_cache.as_dict(),
                "test_signature": test_signature,
                "cached_result": cached_result.as_dict(),
            },
            "output": {
                "reused_result_id": reused.result_id if reused else None,
                "reused_pass_all_tests": (reused.aggregate or {}).get("pass_all_tests") if reused else None,
            },
        }
    )

    checkpoint = tmp_path / "scenario_checkpoint.json"
    overlap_state = build_overlap_graph_state(segment_groups["initial"])
    current_round_state = {
        "round_index": 0,
        "next_task_index": 2,
        "train_details": [{"task_id": "train_a", "runs": []}, {"task_id": "train_b", "runs": []}],
        "online_refactor_attempts": [],
        "extraction_events": [],
        "role_feedback": {},
        "seen_refactor_cliques": [],
        "online_refactor_budget_remaining": 1,
        "overlap_state": overlap_state,
    }
    index = SegmentVectorIndex(strict_embeddings=False)
    _write_current_round_sidecars(
        checkpoint_path=checkpoint,
        current_round_state=current_round_state,
        store=ArtifactStore([_sample_artifact()]),
        segment_index=index,
    )
    payload = _checkpoint_payload(
        tag="scenario",
        rounds=1,
        round_reports=[],
        store=ArtifactStore([_sample_artifact()]),
        segment_index=index,
        next_round_index=0,
        current_round_state=current_round_state,
        role_feedback={},
        output_detail_level="compact",
        checkpoint_path=checkpoint,
    )
    restored = _restore_current_round_state(payload["current_round_state"])
    report["scenarios"].append(
        {
            "name": "checkpoint_overlap_state_resume",
            "meaning": "Resume state must persist overlap graph cache so online refactor does not rebuild historical segments after restart.",
            "input": {
                "checkpoint_payload_projection": payload["current_round_state"],
            },
            "output": {
                "restored_next_task_index": restored["next_task_index"],
                "restored_overlap_state_segment_ids": list(restored["overlap_state"].segment_ids),
                "overlap_state_path": payload["current_round_state"]["overlap_state_path"],
            },
        }
    )

    return report


def test_runtime_optimization_scenarios(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BFCL_BUNDLE_MAX_TOTAL_CASES", "4")
    report = build_runtime_optimization_scenario_report(tmp_path)
    scenarios = {item["name"]: item for item in report["scenarios"]}

    assert scenarios["incremental_overlap_graph"]["output"]["full_edge_pairs"] == scenarios["incremental_overlap_graph"]["output"]["incremental_edge_pairs"]
    assert scenarios["noop_skill_update"]["output"]["current_version"] == 1
    assert scenarios["noop_skill_update"]["output"]["history_length"] == 0
    assert scenarios["bundle_signature_reuse"]["output"]["reuse_expected"] is True
    assert len(scenarios["bundle_total_cap_and_split"]["output"]["positive_case_ids"]) >= 1
    assert len(scenarios["bundle_total_cap_and_split"]["output"]["negative_case_ids"]) >= 1
    assert len(scenarios["bundle_total_cap_and_split"]["output"]["integration_case_ids"]) >= 1
    assert scenarios["bundle_total_cap_and_split"]["output"]["bundle_fixtures"]["bundle_trimmed"] is True
    assert scenarios["bundle_test_cache_reuse"]["output"]["reused_result_id"] == "remove_watchlist_when_symbol_explicit:bundle:cached"
    assert scenarios["checkpoint_overlap_state_resume"]["output"]["restored_overlap_state_segment_ids"] == ["train_a:turn:0", "train_b:turn:0"]
