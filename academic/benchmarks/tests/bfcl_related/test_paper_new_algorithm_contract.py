from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

from academic.benchmarks.bfcl.related.experiment import (
    _apply_credit_bundle_case_suggestions,
    _append_credit_negative_bundle_cases,
    _dedupe_promotion_rows,
    _mark_prior_artifacts_pending,
    _promote_pending_from_refactor_report,
    _run_micro_maintenance,
    _run_related_evolve_experiment,
)
from academic.benchmarks.bfcl.maintenance.adapter import (
    refine_bfcl_skill_store_llm,
    run_bfcl_overlap_refactor_llm,
    update_skill_relation_graph,
    validate_skill_static_dependencies,
)
from academic.skill_repository.llm_maintenance import (
    BUNDLE_MAINTAIN_SYSTEM,
    BUNDLE_SYSTEM,
    CREDIT_ASSIGNMENT_SYSTEM,
    REFINE_SYSTEM,
)
from academic.skill_repository.refactor_overlap import (
    OverlapEdge,
    OverlapGraph,
    RefactorClique,
    TraceSegment,
    REFACTOR_SYSTEM,
    _coarse_skill_candidates_for_clique,
    llm_refactor_clique,
    skill_to_overlap_segment,
)
from academic.skill_repository.store import ArtifactStore
from academic.skill_repository.types import SkillArtifact, SkillBundleCase, SkillTestCaseRun, SkillTestResult


def _fake_task(task_id: str):
    return type("Task", (), {"task_id": task_id, "metadata": {}})()


def _detail(
    task_id: str,
    *,
    phase: str,
    retrieved: List[str],
    injected: List[str],
    used: List[str],
    valid: bool,
) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "task": {"task_id": task_id, "metadata": {}},
        "n_runs": 1,
        "n_success": 1 if valid else 0,
        "avg_score": 1.0 if valid else 0.0,
        "runs": [
            {
                "task_id": task_id,
                "success": valid,
                "score": 1.0 if valid else 0.0,
                "metrics": {
                    "phase": phase,
                    "official_valid": valid,
                    "retrieved_skills": retrieved,
                    "prompt_injected_skills": injected,
                    "tool_injected_skills": [],
                    "used_skills": used,
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
                    "retrieved_skills": retrieved,
                    "prompt_injected_skills": injected,
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


def test_paper_new_prior_candidates_are_pending_not_executor_retrievable() -> None:
    store = ArtifactStore()
    [candidate] = _mark_prior_artifacts_pending(
        [
            SkillArtifact(
                name="direct_symbol_prior",
                kind="atomic_tool_rule_card",
                description="Use explicit symbols directly.",
                body="When a ticker is explicit, call the target tool directly.",
            )
        ],
        round_index=0,
        task_index=0,
        task_id="train_1",
    )

    store.add_pending(candidate)
    stored = store.get("direct_symbol_prior")

    assert stored is not None
    assert stored.status == "pending"
    assert stored.metadata["is_pending_skill"] is True
    assert stored.metadata["is_promoted"] is False
    assert stored.retrieval_enabled() is False
    assert store.retrieve("explicit ticker direct symbol", top_k=3) == []


def test_paper_new_pending_candidates_participate_in_posterior_refactor_recall() -> None:
    pending = SkillArtifact(
        name="direct_symbol_prior",
        kind="atomic_tool_rule_card",
        description="Use explicit symbols directly.",
        body="When a ticker is explicit, call remove_stock_from_watchlist directly.",
        status="pending",
        metadata={"is_pending_skill": True},
    )
    disabled = SkillArtifact(
        name="disabled_duplicate",
        kind="atomic_tool_rule_card",
        description="Disabled duplicate.",
        body="When a ticker is explicit, call remove_stock_from_watchlist directly.",
        status="disabled",
        metadata={"disabled": True},
    )
    segments = [
        TraceSegment(
            segment_id="train_1:turn:0",
            task_id="train_1",
            turn_index=0,
            text="explicit stock symbol present call remove_stock_from_watchlist directly",
            error_text="avoid redundant ticker lookup",
        )
    ]

    candidates = _coarse_skill_candidates_for_clique(
        selected_segments=segments,
        existing_skills=[pending, disabled],
        top_k=4,
    )
    names = [row["name"] for row in candidates]

    assert "direct_symbol_prior" in names
    assert "disabled_duplicate" not in names


def test_paper_new_pending_skill_is_real_overlap_graph_node() -> None:
    pending = SkillArtifact(
        name="direct_symbol_prior",
        kind="atomic_tool_rule_card",
        description="Use explicit symbols directly.",
        body="When a ticker is explicit, call remove_stock_from_watchlist directly.",
        status="pending",
        metadata={"is_pending_skill": True, "allowed_tools": ["remove_stock_from_watchlist"]},
    )

    node = skill_to_overlap_segment(pending)

    assert node.kind == "pending_skill"
    assert node.segment_id == "skill:direct_symbol_prior:v1"
    assert node.metadata["is_pending_skill"] is True
    assert "remove_stock_from_watchlist" in node.text


def test_credit_assignment_harmful_event_appends_negative_bundle_case() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(
                name="bad_watchlist_rule",
                kind="interface_contract_card",
                description="bad",
                body="bad",
            )
        ]
    )
    detail = {
        "task_id": "multi_turn_base_1",
        "task": {
            "task_id": "multi_turn_base_1",
            "question": [[{"role": "user", "content": "Remove NVDA from watchlist."}]],
            "expected": [["remove_stock_from_watchlist(symbol='NVDA')"]],
            "input_artifacts": {"initial_config": {"watchlist": ["NVDA"]}},
            "metadata": {"involved_classes": ["TradingBot"]},
        },
    }
    events = [
        {
            "task_id": "multi_turn_base_1",
            "skill_name": "bad_watchlist_rule",
            "judgment": "harmful",
            "effect_type": "schema_harm",
            "confidence": 0.9,
            "reason": "Skill encouraged wrong argument alias.",
            "used": False,
            "bundle_case_suggestions": [
                {
                    "skill_name": "bad_watchlist_rule",
                    "polarity": "negative",
                    "reason": "Guard the exact symbol argument contract.",
                    "source_task_id": "multi_turn_base_1",
                    "focus_turn_indices": [0],
                    "expected_contract": "remove_stock_from_watchlist must use symbol",
                    "task_fragment_policy": "reuse_official_fragment",
                }
            ],
        }
    ]

    added = _append_credit_negative_bundle_cases(store=store, detail=detail, credit_events=events)

    skill = store.get("bad_watchlist_rule")
    assert added and added[0]["skill_name"] == "bad_watchlist_rule"
    assert skill is not None
    assert len(skill.bundle.negative_cases) == 1
    case = skill.bundle.negative_cases[0]
    assert case.source == "credit_assigner_negative"
    assert case.context["task_fragment"]["expected"] == [["remove_stock_from_watchlist(symbol='NVDA')"]]
    assert case.expected["match_task_expected"] is True


def test_credit_assignment_without_focus_does_not_create_whole_task_bundle_case() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(
                name="bad_watchlist_rule",
                kind="interface_contract_card",
                description="bad",
                body="bad",
            )
        ]
    )
    detail = {
        "task_id": "multi_turn_base_1",
        "task": {
            "task_id": "multi_turn_base_1",
            "question": [
                [{"role": "user", "content": "Show my watchlist."}],
                [{"role": "user", "content": "Remove NVDA from watchlist."}],
            ],
            "expected": [["get_watchlist()"], ["remove_stock_from_watchlist(symbol='NVDA')"]],
            "input_artifacts": {"initial_config": {"watchlist": ["NVDA"]}},
            "metadata": {"involved_classes": ["TradingBot"]},
        },
    }
    events = [
        {
            "task_id": "multi_turn_base_1",
            "skill_name": "bad_watchlist_rule",
            "judgment": "harmful",
            "effect_type": "schema_harm",
            "confidence": 0.9,
            "reason": "No replayable fragment was specified.",
            "used": False,
            "bundle_case_suggestions": [
                {
                    "skill_name": "bad_watchlist_rule",
                    "polarity": "negative",
                    "reason": "Missing focus must not become full-task bundle.",
                    "source_task_id": "multi_turn_base_1",
                    "focus_turn_indices": [],
                    "expected_contract": "remove_stock_from_watchlist must use symbol",
                    "task_fragment_policy": "reuse_official_fragment",
                }
            ],
        }
    ]

    added = _apply_credit_bundle_case_suggestions(store=store, detail=detail, credit_events=events)

    skill = store.get("bad_watchlist_rule")
    assert added == []
    assert skill is not None
    assert skill.bundle.negative_cases == []


def test_credit_assignment_focus_turn_slices_official_task_fragment() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(
                name="watchlist_symbol_rule",
                kind="interface_contract_card",
                description="Use symbol.",
                body="Use symbol.",
            )
        ]
    )
    detail = {
        "task_id": "multi_turn_base_2",
        "task": {
            "task_id": "multi_turn_base_2",
            "question": [
                [{"role": "user", "content": "Show my watchlist."}],
                [{"role": "user", "content": "Remove NVDA from watchlist."}],
            ],
            "expected": [["get_watchlist()"], ["remove_stock_from_watchlist(symbol='NVDA')"]],
            "input_artifacts": {"initial_config": {"watchlist": ["NVDA"]}},
            "metadata": {"involved_classes": ["TradingBot"]},
        },
    }
    events = [
        {
            "task_id": "multi_turn_base_2",
            "skill_name": "watchlist_symbol_rule",
            "judgment": "harmful",
            "effect_type": "schema_harm",
            "confidence": 0.9,
            "reason": "Wrong argument alias on removal turn.",
            "used": True,
            "bundle_case_suggestions": [
                {
                    "skill_name": "watchlist_symbol_rule",
                    "polarity": "negative",
                    "reason": "Only the removal turn is relevant.",
                    "source_task_id": "multi_turn_base_2",
                    "focus_turn_indices": [1],
                    "required_context_turn_indices": [],
                    "expected_contract": "remove_stock_from_watchlist must use symbol",
                    "task_fragment_policy": "reuse_official_fragment",
                }
            ],
        }
    ]

    added = _apply_credit_bundle_case_suggestions(store=store, detail=detail, credit_events=events)

    skill = store.get("watchlist_symbol_rule")
    assert added and added[0]["polarity"] == "negative"
    case = skill.bundle.negative_cases[0]
    fragment = case.context["task_fragment"]
    assert fragment["question"] == [[{"role": "user", "content": "Remove NVDA from watchlist."}]]
    assert fragment["expected"] == [["remove_stock_from_watchlist(symbol='NVDA')"]]
    assert fragment["focus_turn_indices"] == [1]
    assert fragment["source_turn_indices"] == [1]


def test_credit_bundle_append_enforces_total_case_budget(monkeypatch) -> None:
    monkeypatch.setenv("BFCL_BUNDLE_CASE_LIMIT_PER_POLARITY", "6")
    monkeypatch.setenv("BFCL_BUNDLE_MAX_TOTAL_CASES", "4")
    artifact = SkillArtifact(
        name="watchlist_symbol_rule",
        kind="interface_contract_card",
        description="Use symbol.",
        body="Use symbol.",
    )
    artifact.bundle.positive_cases = [
        SkillBundleCase(case_id=f"watchlist_symbol_rule:positive:{idx}", source="manual", prompt=f"p{idx}")
        for idx in range(2)
    ]
    artifact.bundle.negative_cases = [
        SkillBundleCase(case_id=f"watchlist_symbol_rule:negative:{idx}", source="manual", prompt=f"n{idx}")
        for idx in range(2)
    ]
    store = ArtifactStore([artifact])
    detail = {
        "task_id": "multi_turn_base_3",
        "task": {
            "task_id": "multi_turn_base_3",
            "question": [
                [{"role": "user", "content": "Remove NVDA from watchlist."}],
            ],
            "expected": [["remove_stock_from_watchlist(symbol='NVDA')"]],
            "input_artifacts": {"initial_config": {"watchlist": ["NVDA"]}},
            "metadata": {"involved_classes": ["TradingBot"]},
        },
    }
    events = [
        {
            "task_id": "multi_turn_base_3",
            "skill_name": "watchlist_symbol_rule",
            "judgment": "harmful",
            "effect_type": "schema_harm",
            "confidence": 0.95,
            "reason": "Wrong argument alias on removal turn.",
            "used": True,
            "bundle_case_suggestions": [
                {
                    "skill_name": "watchlist_symbol_rule",
                    "polarity": "negative",
                    "reason": "Only the removal turn is relevant.",
                    "source_task_id": "multi_turn_base_3",
                    "focus_turn_indices": [0],
                    "expected_contract": "remove_stock_from_watchlist must use symbol",
                    "task_fragment_policy": "reuse_official_fragment",
                }
            ],
        }
    ]

    added = _apply_credit_bundle_case_suggestions(store=store, detail=detail, credit_events=events)

    skill = store.get("watchlist_symbol_rule")
    assert added and skill is not None
    assert len(skill.bundle.all_cases()) == 4
    assert any(case.source == "credit_assigner_negative" for case in skill.bundle.negative_cases)
    assert skill.bundle.fixtures["bundle_trimmed"] is True
    assert skill.bundle.fixtures["bundle_case_budget"]["total_limit"] == 4


def test_paper_new_committed_posterior_refactor_promotes_pending_skill() -> None:
    store = ArtifactStore()
    store.add_pending(
        SkillArtifact(
            name="direct_symbol_prior",
            kind="atomic_tool_rule_card",
            description="Use explicit symbols directly.",
            body="When a ticker is explicit, call the target tool directly.",
        )
    )

    promotions = _promote_pending_from_refactor_report(
        store=store,
        refactor_report={
            "attempts": [
                {
                    "status": "committed",
                    "group_id": "g_posterior",
                    "llm_payload": {
                        "affected_skill_updates": [
                            {"name": "direct_symbol_prior", "action": "rewrite"}
                        ]
                    },
                }
            ]
        },
    )
    promoted = store.get("direct_symbol_prior")

    assert promotions == [
        {
            "skill_name": "direct_symbol_prior",
            "action": "promoted",
            "reason": "posterior_refactor_overlap_evidence",
            "refactor_group_id": "g_posterior",
        }
    ]
    assert promoted is not None
    assert promoted.status == "active"
    assert promoted.retrieval_enabled() is True
    assert promoted.metadata["promotion_state"] == "promoted"


def test_paper_new_promotion_rows_are_idempotent_for_analysis() -> None:
    rows = _dedupe_promotion_rows(
        [
            {"skill_name": "skill_a", "action": "promoted", "refactor_group_id": "g1"},
            {"skill_name": "skill_a", "action": "promoted", "refactor_group_id": "g2"},
            {"skill_name": "skill_b", "action": "promoted", "refactor_group_id": "g3"},
        ]
    )

    assert rows == [
        {"skill_name": "skill_a", "action": "promoted", "refactor_group_id": "g1"},
        {"skill_name": "skill_b", "action": "promoted", "refactor_group_id": "g3"},
    ]


def test_paper_new_round_end_revokes_unpromoted_pending_without_deleting_audit() -> None:
    store = ArtifactStore()
    store.add_pending(
        SkillArtifact(
            name="weak_prior",
            kind="workflow_guardrail_card",
            description="Weak prior",
            body="Maybe reusable.",
        )
    )

    revoked = store.revoke_unpromoted_pending(reason="round_end_not_promoted_by_posterior_overlap")
    archived = store.get("weak_prior")

    assert revoked == ["weak_prior"]
    assert archived is not None
    assert archived.status == "archived"
    assert archived.metadata["promotion_state"] == "revoked"
    assert archived.retrieval_enabled() is False


def test_paper_new_credit_prompt_keeps_weak_irrelevance_out_of_harmful() -> None:
    assert "Irrelevant retrieved/injected skills are neutral or uncertain by default" in CREDIT_ASSIGNMENT_SYSTEM
    assert "suspected_prompt_pollution" in CREDIT_ASSIGNMENT_SYSTEM
    assert "Mark harmful only with direct evidence" in CREDIT_ASSIGNMENT_SYSTEM


def test_paper_new_credit_prompt_explains_bundle_fragment_fields() -> None:
    assert "Field semantics and empty-value rules" in CREDIT_ASSIGNMENT_SYSTEM
    assert "maintenance_actions`: skill-local actions only" in CREDIT_ASSIGNMENT_SYSTEM
    assert "focus_turn_indices`: turns whose official user prompt and expected calls" in CREDIT_ASSIGNMENT_SYSTEM
    assert "required_context_turn_indices`: earlier official turns required" in CREDIT_ASSIGNMENT_SYSTEM
    assert "state_requirements`: minimal state facts required" in CREDIT_ASSIGNMENT_SYSTEM
    assert "task_fragment_policy`: `reuse_official_fragment` only" in CREDIT_ASSIGNMENT_SYSTEM
    assert "Do not use the whole task by default" in CREDIT_ASSIGNMENT_SYSTEM


def test_paper_new_bundle_prompts_explain_case_fields_and_reject_cross_domain_fragments() -> None:
    for prompt in (BUNDLE_SYSTEM, BUNDLE_MAINTAIN_SYSTEM):
        assert "Field semantics and empty-value rules" in prompt
        assert "source/domain" in prompt or "same source domain" in prompt
        assert "Never" in prompt and "cross-domain" in prompt
        assert "context.task_fragment.expected" in prompt
        assert "official executable call strings" in prompt
        assert "placeholders" in prompt or "no aliases" in prompt


def test_paper_new_refine_prompt_explains_keep_and_dependency_fields() -> None:
    assert "Field semantics and empty-value rules" in REFINE_SYSTEM
    assert "credit pre-refine" in REFINE_SYSTEM
    assert "does not identify a concrete schema, scope, workflow, or dependency fix" in REFINE_SYSTEM
    assert "with-skill `contract_failures`" in REFINE_SYSTEM
    assert "do not claim the case validated" in REFINE_SYSTEM
    assert "current artifact" in REFINE_SYSTEM
    assert "A post-bundle strict failure is not a reason to" in REFINE_SYSTEM
    assert "keep by default" in REFINE_SYSTEM
    assert "`artifact`: when action is `keep`, return {}" in REFINE_SYSTEM
    assert "`artifact.dependencies`: list only named skills" in REFINE_SYSTEM


def test_paper_new_static_dependency_validator_records_code_like_calls() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="helper_skill", kind="atomic_tool_rule_card", description="helper", body="helper"),
            SkillArtifact(
                name="caller_skill",
                kind="executable_tool",
                description="call helper",
                body="def run():\n    return helper_skill()\n",
            ),
        ]
    )

    reports = validate_skill_static_dependencies(store, ["caller_skill"])
    report = reports[0]

    assert report["skill_name"] == "caller_skill"
    assert report["code_like"] is True
    assert "helper_skill" in report["auto_dependencies"]
    assert "helper_skill" in report["called_skill_names"]
    assert "helper_skill" in store.get("caller_skill").dependencies
    caller_relation = store.get("caller_skill").metadata["skill_relation_graph"]
    callee_relation = store.get("helper_skill").metadata["skill_relation_graph"]
    assert caller_relation["calls"] == ["helper_skill"]
    assert callee_relation["called_by"] == ["caller_skill"]
    assert "helper_skill" in store.get("caller_skill").metadata["static_dependency_validation"]["auto_dependencies"]


async def test_refactor_prompt_uses_heterogeneous_nodes_without_coarse_candidates(monkeypatch) -> None:
    trace_segment = TraceSegment(
        segment_id="train_1:turn:0",
        task_id="train_1",
        turn_index=0,
        text="explicit ticker remove watchlist uses symbol",
        error_text="stock argument alias failed",
        metadata={"node_type": "trace_segment"},
    )
    skill = SkillArtifact(
        name="watchlist_symbol_rule",
        kind="interface_contract_card",
        description="Use symbol for watchlist tools.",
        body="For watchlist add/remove calls, bind explicit tickers to symbol.",
        metadata={"allowed_tools": ["remove_stock_from_watchlist"], "domains": ["TradingBot"]},
    )
    skill_segment = skill_to_overlap_segment(skill)
    graph = OverlapGraph(
        segments=[trace_segment, skill_segment],
        edges=[
            OverlapEdge(
                source=trace_segment.segment_id,
                target=skill_segment.segment_id,
                weight=0.9,
                text_score=0.9,
                error_score=0.2,
            )
        ],
    )
    clique = RefactorClique(
        clique_id="c1",
        segment_ids=[trace_segment.segment_id, skill_segment.segment_id],
        edge_weight_sum=0.9,
        edges=graph.edges,
    )
    captured: Dict[str, str] = {}

    async def fake_ask_json(**kwargs):
        captured["system"] = kwargs["system"]
        captured["user"] = kwargs["user"]
        return {"decision": {"action": "reject", "reason": "unit", "confidence": 0.1}}

    monkeypatch.setattr("academic.skill_repository.refactor_overlap._ask_json", fake_ask_json)

    await llm_refactor_clique(
        clique=clique,
        graph=graph,
        existing_skills=[skill],
        llm_config="test",
    )

    assert "Coarsely Recalled Existing Skill Candidates" not in captured["user"]
    assert "Involved Skill Node Summaries" in captured["user"]
    assert "watchlist_symbol_rule" in captured["user"]
    assert "knowledge/rule skill" in captured["system"]
    assert "workflow skill" in captured["system"]
    assert "function/interface skill" in captured["system"]


def test_refactor_prompt_has_executable_function_few_shot_and_field_semantics() -> None:
    assert "function/interface skill with executable sequence" in REFACTOR_SYSTEM
    assert 'remove_stock_from_watchlist(stock="NVDA")' in REFACTOR_SYSTEM
    assert 'remove_stock_from_watchlist(symbol="NVDA")' in REFACTOR_SYSTEM
    assert 'add_stock_to_watchlist(symbol="TSLA")' in REFACTOR_SYSTEM
    assert "Tool schema: `remove_stock_from_watchlist(symbol: string)`" in REFACTOR_SYSTEM
    assert "Field semantics and empty-value rules" in REFACTOR_SYSTEM
    assert "`shared_skill`: when `decision.action` is `reject`, return {}" in REFACTOR_SYSTEM
    assert "include executable tool-call forms or call-order sequences" in REFACTOR_SYSTEM
    assert "Never complete missing fields by guessing" in REFACTOR_SYSTEM


def test_paper_new_relation_graph_updates_calls_and_co_use_edges() -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="caller", kind="workflow_guardrail_card", description="caller", body="caller"),
            SkillArtifact(name="callee", kind="atomic_tool_rule_card", description="callee", body="callee"),
            SkillArtifact(name="peer", kind="atomic_tool_rule_card", description="peer", body="peer"),
        ]
    )

    update_skill_relation_graph(
        store,
        retrieved=["caller", "peer"],
        used=["caller", "callee"],
        calls={"caller": ["callee"]},
    )

    caller_relation = store.get("caller").metadata["skill_relation_graph"]
    callee_relation = store.get("callee").metadata["skill_relation_graph"]
    peer_relation = store.get("peer").metadata["skill_relation_graph"]

    assert caller_relation["calls"] == ["callee"]
    assert callee_relation["called_by"] == ["caller"]
    assert caller_relation["co_retrieved_with"] == ["peer"]
    assert peer_relation["co_retrieved_with"] == ["caller"]
    assert caller_relation["co_used_with"] == ["callee"]
    assert callee_relation["co_used_with"] == ["caller"]
    assert "callee" in store.get("caller").dependencies


async def test_paper_new_refine_targets_only_requested_skills_and_receives_credit(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(name="target_skill", kind="atomic_tool_rule_card", description="target", body="target"),
            SkillArtifact(name="untouched_skill", kind="atomic_tool_rule_card", description="untouched", body="untouched"),
        ]
    )
    result = SkillTestResult(
        result_id="r1",
        skill_name="target_skill",
        skill_version=1,
        bundle_id="target_skill.bundle",
        bundle_version=1,
        run_label="llm_bundle_unit",
        unit_case_runs=[],
        aggregate={"pass_all_tests": False, "n_regressed": 1, "n_improved": 0},
        integration_failures=[{"task_id": "train_1", "error": "wrong arg"}],
    )
    seen = {}

    async def fake_refine(artifact, **kwargs):
        seen["artifact_name"] = artifact.name
        seen["credit_context"] = kwargs.get("credit_context")
        seen["dependency_summaries"] = kwargs.get("dependency_summaries")
        return {
            "decision": {"action": "keep", "reason": "no safe change"},
            "artifact": {},
            "bundle": {},
        }

    monkeypatch.setattr("academic.benchmarks.bfcl.maintenance.adapter.refine_skill_artifact_llm", fake_refine)

    decisions = await refine_bfcl_skill_store_llm(
        store,
        maintenance_test_results=[result],
        llm_config="local",
        artifact_names=["target_skill"],
        credit_context_by_skill={
            "target_skill": [
                {
                    "task_id": "train_1",
                    "judgment": "harmful",
                    "effect_type": "schema_harm",
                    "confidence": 0.9,
                }
            ]
        },
    )

    assert seen["artifact_name"] == "target_skill"
    assert seen["credit_context"][0]["judgment"] == "harmful"
    assert all(row["skill_name"] == "target_skill" for row in decisions)
    assert store.get("untouched_skill").version == 1


async def test_micro_maintenance_pre_refines_from_credit_before_bundle_test(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(
                name="bad_watchlist_rule",
                kind="interface_contract_card",
                description="bad",
                body="bad",
            )
        ]
    )
    order: List[str] = []

    async def fake_refine(store_arg, *, maintenance_test_results, artifact_names, **kwargs):
        order.append("refine")
        assert maintenance_test_results[0].run_label == "credit_pre_refine"
        assert maintenance_test_results[0].aggregate["credit_pre_refine"] is True
        assert artifact_names == ["bad_watchlist_rule"]
        return [{"skill_name": "bad_watchlist_rule", "action": "refine_minor"}]

    async def fake_execute(artifact, **kwargs):
        order.append("test")
        return SkillTestResult(
            result_id="bundle-pass",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
            bundle_version=artifact.bundle.bundle_version,
            run_label="llm_bundle_unit",
            unit_case_runs=[],
            aggregate={"pass_all_tests": True, "n_regressed": 0, "n_improved": 0},
        )

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.refine_bfcl_skill_store_llm", fake_refine)
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.execute_bfcl_bundle_tests", fake_execute)

    report = await _run_micro_maintenance(
        store=store,
        detail={"task_id": "train_1"},
        task_credit_events=[
            {
                "task_id": "train_1",
                "skill_name": "bad_watchlist_rule",
                "judgment": "harmful",
                "effect_type": "schema_harm",
                "confidence": 0.9,
                "refine_required": True,
                "used": False,
            }
        ],
        credit_bundle_cases=[{"skill_name": "bad_watchlist_rule", "case_id": "c1", "polarity": "negative"}],
        relevant_skill_names=["bad_watchlist_rule"],
        tools=[],
        llm_config="test",
        model_name=None,
        execution_backend="local_mock",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=2,
        max_task_seconds=10.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        round_index=0,
        task_index=0,
        tag="unit",
        credit_context_by_skill={"bad_watchlist_rule": [{"judgment": "harmful"}]},
    )

    assert order == ["refine", "test"]
    assert report["pre_refine_decisions"][0]["action"] == "refine_minor"
    assert report["maintenance_test_results"][0]["aggregate"]["pass_all_tests"] is True


async def test_micro_maintenance_flags_keep_after_strict_bundle_failure(monkeypatch) -> None:
    store = ArtifactStore(
        [
            SkillArtifact(
                name="bad_checklist",
                kind="workflow_guardrail_card",
                description="bad",
                body="bad",
            )
        ]
    )

    async def fake_execute(artifact, **kwargs):
        return SkillTestResult(
            result_id="bundle-fail",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
            bundle_version=artifact.bundle.bundle_version,
            run_label="llm_bundle_unit",
            unit_case_runs=[
                SkillTestCaseRun(
                    case_id="case:0",
                    variant="with_skill",
                    passed=False,
                    metadata={"contract_failures": [{"type": "task_expected_call_count_mismatch"}]},
                )
            ],
            aggregate={"pass_all_tests": False, "n_strict_failures": 1},
        )

    async def fake_refine(store_arg, *, artifact_names, **kwargs):
        assert artifact_names == ["bad_checklist"]
        return [{"skill_name": "bad_checklist", "action": "keep", "reason": "no change"}]

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.execute_bfcl_bundle_tests", fake_execute)
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.refine_bfcl_skill_store_llm", fake_refine)

    report = await _run_micro_maintenance(
        store=store,
        detail={"task_id": "train_1"},
        task_credit_events=[],
        credit_bundle_cases=[{"skill_name": "bad_checklist", "case_id": "c1", "polarity": "negative"}],
        relevant_skill_names=["bad_checklist"],
        tools=[],
        llm_config="test",
        model_name=None,
        execution_backend="local_mock",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=2,
        max_task_seconds=10.0,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        round_index=0,
        task_index=0,
        tag="unit",
        credit_context_by_skill={},
    )

    assert report["refine_decisions"][0]["action"] == "strict_failure_kept"
    assert report["refine_decisions"][0]["original_action"] == "keep"


async def test_paper_new_evolve_flow_promotes_posterior_skill_before_heldout(monkeypatch, tmp_path: Path) -> None:
    manifest = {"train_task_ids": ["train_1"], "test_task_ids": ["test_1"]}
    fake_train = [_fake_task("train_1")]
    fake_test = [_fake_task("test_1")]
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: (fake_train, fake_test),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.default_bfcl_skill_store",
        lambda: ArtifactStore(
            [
                SkillArtifact(
                    name="seed_active",
                    kind="atomic_tool_rule_card",
                    description="Seed active rule.",
                    body="Already validated.",
                )
            ]
        ),
    )
    run_calls: List[Dict[str, Any]] = []

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        store = args[3]
        phase = kwargs.get("phase", "")
        active_names = [skill.name for skill in store.all() if skill.retrieval_enabled()]
        run_calls.append({"phase": phase, "active_names": active_names})
        details = []
        for task in tasks:
            if phase == "related_heldout_test":
                assert "prior_symbol_rule" in active_names
                assert all("__pending" not in name for name in active_names)
                details.append(
                    _detail(
                        task.task_id,
                        phase=phase,
                        retrieved=["prior_symbol_rule"],
                        injected=["prior_symbol_rule"],
                        used=["prior_symbol_rule"],
                        valid=True,
                    )
                )
            else:
                assert "prior_symbol_rule" not in active_names
                details.append(
                    _detail(
                        task.task_id,
                        phase=phase,
                        retrieved=["seed_active"],
                        injected=["seed_active"],
                        used=["seed_active"],
                        valid=True,
                    )
                )
        return details

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline",
        fake_run_bfcl_baseline,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.extract_bfcl_skill_artifacts_llm",
        AsyncMock(
            return_value=[
                SkillArtifact(
                    name="prior_symbol_rule",
                    kind="atomic_tool_rule_card",
                    description="Use explicit symbols directly.",
                    body="When a ticker is explicit, call the target tool directly.",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.assign_skill_credit_llm",
        AsyncMock(
            return_value={
                "task_summary": {"task_id": "train_1", "official_valid": True, "score": 1.0},
                "skill_judgments": [
                    {
                        "skill_name": "seed_active",
                        "judgment": "helpful",
                        "effect_type": "execution_help",
                        "confidence": 0.8,
                        "reason": "seed was used successfully",
                        "evidence": {"retrieved": True, "injected": True, "used": True},
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._extract_task_segments",
        lambda detail: [
            TraceSegment(
                segment_id=f"{detail['task_id']}:turn:0",
                task_id=detail["task_id"],
                turn_index=0,
                text="explicit symbol direct call",
                error_text="",
            )
        ],
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._run_round_refine_and_refactor",
        AsyncMock(
            return_value={
                "maintenance_targets": ["prior_symbol_rule"],
                "maintenance_test_results": [
                    {"skill_name": "prior_symbol_rule", "aggregate": {"pass_all_tests": True}}
                ],
                "refine_decisions": [],
                "overlap_refactor": {
                    "attempts": [
                        {
                            "status": "committed",
                            "group_id": "g_posterior",
                            "llm_payload": {
                                "affected_skill_updates": [
                                    {"name": "prior_symbol_rule", "action": "rewrite"}
                                ]
                            },
                        }
                    ]
                },
            }
        ),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.update_extractor_rules_from_feedback_llm",
        AsyncMock(return_value={"summary": "ok", "rules": [], "updated_at": "2026-05-16T00:00:00+00:00"}),
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
        tag="paper_new_contract",
        save_skills=None,
        use_handwritten_skills=True,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=True,
        experiment_variant="paper_new_contract",
    )

    skills = {row["name"]: row for row in payload["skills"]}
    assert skills["prior_symbol_rule"]["status"] == "active"
    assert skills["prior_symbol_rule"]["metadata"]["promotion_state"] == "promoted"
    assert payload["rounds"][0]["pending_skill_promotions"][0]["skill_name"] == "prior_symbol_rule"
    assert payload["rounds"][0]["pending_skill_revocations"] == []
    assert run_calls[0]["phase"] == "related_train_epoch_0"
    assert "prior_symbol_rule" not in run_calls[0]["active_names"]
    assert run_calls[-1]["phase"] == "related_heldout_test"
    assert "prior_symbol_rule" in run_calls[-1]["active_names"]


async def test_paper_new_macro_windows_flush_new_segments_and_coverage(monkeypatch, tmp_path: Path) -> None:
    manifest = {"train_task_ids": ["train_1", "train_2", "train_3"], "test_task_ids": []}
    fake_train = [_fake_task(f"train_{idx}") for idx in range(1, 4)]
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._tasks_from_manifest",
        lambda manifest, cache_dir, data_source: (fake_train, []),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.default_bfcl_skill_store",
        lambda: ArtifactStore([SkillArtifact(name="seed_active", kind="atomic_tool_rule_card", description="seed", body="seed")]),
    )

    async def fake_run_bfcl_baseline(tasks, *args, **kwargs):
        return [
            _detail(
                task.task_id,
                phase=kwargs.get("phase", ""),
                retrieved=["seed_active"],
                injected=["seed_active"],
                used=["seed_active"],
                valid=True,
            )
            for task in tasks
        ]

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_bfcl_baseline", fake_run_bfcl_baseline)
    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment.extract_bfcl_skill_artifacts_llm", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.assign_skill_credit_llm",
        AsyncMock(return_value={"task_summary": {}, "skill_judgments": []}),
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment._extract_task_segments",
        lambda detail: [
            TraceSegment(
                segment_id=f"{detail['task_id']}:turn:0",
                task_id=detail["task_id"],
                turn_index=0,
                text=f"{detail['task_id']} common overlap",
                error_text="",
            )
        ],
    )

    maintenance_calls: List[Dict[str, Any]] = []

    async def fake_maintenance(**kwargs):
        phase = kwargs.get("phase")
        segments = list(kwargs.get("new_segments") or [])
        maintenance_calls.append(
            {
                "phase": phase,
                "task_ids": [item.get("task_id") for item in kwargs.get("train_details") or []],
                "segment_ids": [segment.segment_id for segment in segments],
                "artifact_names": list(kwargs.get("artifact_names") or []),
            }
        )
        coverage = [
            {
                "segment_id": segment.segment_id,
                "task_id": segment.task_id,
                "action": "noop",
                "status": "covered_no_llm_candidate",
                "candidate_groups": [],
            }
            for segment in segments
        ]
        return {
            "maintenance_targets": list(kwargs.get("artifact_names") or []),
            "maintenance_test_results": [],
            "refine_decisions": [],
            "overlap_refactor": {"attempts": [], "commits": [], "rejections": [], "refactor_segment_coverage": coverage},
            "refactor_segment_coverage": coverage,
            "static_dependency_validation": [],
            "token_breakdown": {},
        }

    monkeypatch.setattr("academic.benchmarks.bfcl.related.experiment._run_round_refine_and_refactor", fake_maintenance)
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.related.experiment.update_extractor_rules_from_feedback_llm",
        AsyncMock(return_value={"summary": "ok", "rules": [], "updated_at": "2026-05-16T00:00:00+00:00"}),
    )

    payload = await _run_related_evolve_experiment(
        manifest=manifest,
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        tools=[],
        rounds=3,
        epochs=1,
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
        tag="paper_new_macro",
        save_skills=None,
        use_handwritten_skills=True,
        checkpoint_path=tmp_path / "checkpoint.json",
        output_path=tmp_path / "out.json",
        output_detail_level="compact",
        extractor_trl_enabled=False,
        experiment_variant="paper_new_macro",
        micro_maintenance_step=1,
        macro_maintenance_step=2,
    )

    macro_calls = [row for row in maintenance_calls if str(row["phase"]).startswith("macro")]
    assert [row["task_ids"] for row in macro_calls] == [["train_1", "train_2"], ["train_3"]]
    assert [row["segment_ids"] for row in macro_calls] == [["train_1:turn:0", "train_2:turn:0"], ["train_3:turn:0"]]
    assert payload["config_summary"]["epochs"] == 1
    assert payload["config_summary"]["macro_maintenance_step"] == 2
    assert len(payload["maintenance_windows"]) == 2
    assert {row["segment_id"] for row in payload["refactor_segment_coverage"]} == {
        "train_1:turn:0",
        "train_2:turn:0",
        "train_3:turn:0",
    }


async def test_paper_new_real_refactor_coverage_path_without_overlap_state(monkeypatch) -> None:
    monkeypatch.setenv("BFCL_REFACTOR_MAX_CLIQUES", "0")
    store = ArtifactStore([])
    details = [
        _detail(
            "train_1",
            phase="real_refactor_unit",
            retrieved=[],
            injected=[],
            used=[],
            valid=True,
        ),
        _detail(
            "train_2",
            phase="real_refactor_unit",
            retrieved=[],
            injected=[],
            used=[],
            valid=True,
        ),
    ]
    segments = [
        TraceSegment(
            segment_id="train_1:turn:0",
            task_id="train_1",
            turn_index=0,
            text="explicit ticker direct watchlist removal",
            error_text="wrong argument alias",
        ),
        TraceSegment(
            segment_id="train_2:turn:0",
            task_id="train_2",
            turn_index=0,
            text="explicit ticker direct watchlist deletion",
            error_text="wrong argument alias",
        ),
    ]

    report = await run_bfcl_overlap_refactor_llm(
        store,
        train_details=details,
        segment_embeddings={},
        overlap_state=None,
        new_segments=segments,
        exclude_segment_sets=set(),
        tools=[],
        llm_config="mock_no_call",
        model_name=None,
        adapter_mode="official",
        execution_backend="local_mock",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=2,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        max_case_seconds=1.0,
    )

    assert {row["segment_id"] for row in report["refactor_segment_coverage"]} == {
        "train_1:turn:0",
        "train_2:turn:0",
    }


async def test_overlap_refactor_report_contains_pending_skill_graph_node(monkeypatch) -> None:
    monkeypatch.setenv("BFCL_REFACTOR_MAX_CLIQUES", "0")
    store = ArtifactStore()
    store.add_pending(
        SkillArtifact(
            name="pending_symbol_rule",
            kind="atomic_tool_rule_card",
            description="Use explicit ticker symbols directly.",
            body="When the user gives an explicit ticker symbol, call the target watchlist tool directly.",
            metadata={"allowed_tools": ["remove_stock_from_watchlist"], "domains": ["TradingBot"]},
        )
    )
    segments = [
        TraceSegment(
            segment_id="train_1:turn:0",
            task_id="train_1",
            turn_index=0,
            text="explicit ticker symbol directly remove from watchlist",
            error_text="wrong argument alias",
        ),
        TraceSegment(
            segment_id="train_2:turn:0",
            task_id="train_2",
            turn_index=0,
            text="explicit ticker symbol directly delete from watchlist",
            error_text="wrong argument alias",
        ),
    ]

    report = await run_bfcl_overlap_refactor_llm(
        store,
        train_details=[],
        segment_embeddings={},
        overlap_state=None,
        new_segments=segments,
        exclude_segment_sets=set(),
        tools=[],
        llm_config="mock_no_call",
        model_name=None,
        adapter_mode="official",
        execution_backend="local_mock",
        prompt_style="native",
        tool_api_style="auto",
        max_steps_per_turn=2,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
        max_case_seconds=1.0,
    )

    skill_nodes = [
        row for row in report["overlap_graph"]["segments"]
        if row["segment_id"] == "skill:pending_symbol_rule:v1"
    ]
    assert skill_nodes
    assert skill_nodes[0]["kind"] == "pending_skill"
