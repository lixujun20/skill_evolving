import json

import pytest

from academic.extractor import EXTRACT_SYSTEM as TIR_EXTRACT_SYSTEM
from academic.benchmarks.bfcl.related.experiment import _build_extractor_feedback_rows
from academic.skill_repository.llm_maintenance import (
    EXTRACT_SYSTEM,
    update_extractor_rules_from_feedback_llm,
)


@pytest.mark.asyncio
async def test_update_extractor_rules_from_synthetic_feedback_captures_prompt(monkeypatch) -> None:
    extraction_events = [
        {
            "skill_name": "resolve_order_id_then_cancel_order",
            "skill_version": 1,
            "source_task_ids": ["train_cancel_01", "train_cancel_02"],
            "round_index": 0,
            "description": (
                "Before canceling an order, first resolve the canonical order id "
                "and then pass that exact id to cancel_order."
            ),
            "allowed_tools": ["lookup_order", "cancel_order"],
        },
        {
            "skill_name": "skip_lookup_for_frequent_orders",
            "skill_version": 1,
            "source_task_ids": ["train_cancel_01"],
            "round_index": 0,
            "description": (
                "For common order requests, skip the lookup step and call "
                "cancel_order directly with the visible order token."
            ),
            "allowed_tools": ["cancel_order"],
        },
    ]
    train_details = [
        {
            "task_id": "train_cancel_03",
            "runs": [{"metrics": {
                "official_valid": True,
                "retrieved_skills": ["resolve_order_id_then_cancel_order"],
                "prompt_injected_skills": ["resolve_order_id_then_cancel_order"],
                "tool_injected_skills": [],
                "used_skills": ["resolve_order_id_then_cancel_order"],
                "called_skill_tools": [],
                "call_errors": [],
            }}],
        },
        {
            "task_id": "train_cancel_04",
            "runs": [{"metrics": {
                "official_valid": True,
                "retrieved_skills": [
                    "resolve_order_id_then_cancel_order",
                    "skip_lookup_for_frequent_orders",
                ],
                "prompt_injected_skills": [
                    "resolve_order_id_then_cancel_order",
                    "skip_lookup_for_frequent_orders",
                ],
                "tool_injected_skills": [],
                "used_skills": ["resolve_order_id_then_cancel_order"],
                "called_skill_tools": [],
                "call_errors": [],
            }}],
        },
        {
            "task_id": "train_cancel_05",
            "runs": [{"metrics": {
                "official_valid": True,
                "retrieved_skills": ["resolve_order_id_then_cancel_order"],
                "prompt_injected_skills": ["resolve_order_id_then_cancel_order"],
                "tool_injected_skills": [],
                "used_skills": ["resolve_order_id_then_cancel_order"],
                "called_skill_tools": [],
                "call_errors": [],
            }}],
        },
        {
            "task_id": "train_cancel_06",
            "runs": [{"metrics": {
                "official_valid": False,
                "retrieved_skills": ["skip_lookup_for_frequent_orders"],
                "prompt_injected_skills": ["skip_lookup_for_frequent_orders"],
                "tool_injected_skills": [],
                "used_skills": ["skip_lookup_for_frequent_orders"],
                "called_skill_tools": [],
                "call_errors": [{"type": "argument_mismatch"}],
            }}],
        },
        {
            "task_id": "train_cancel_07",
            "runs": [{"metrics": {
                "official_valid": False,
                "retrieved_skills": [
                    "resolve_order_id_then_cancel_order",
                    "skip_lookup_for_frequent_orders",
                ],
                "prompt_injected_skills": [
                    "resolve_order_id_then_cancel_order",
                    "skip_lookup_for_frequent_orders",
                ],
                "tool_injected_skills": [],
                "used_skills": ["skip_lookup_for_frequent_orders"],
                "called_skill_tools": [],
                "call_errors": [{"type": "missing_call"}, {"type": "argument_mismatch"}],
            }}],
        },
        {
            "task_id": "train_cancel_08",
            "runs": [{"metrics": {
                "official_valid": True,
                "retrieved_skills": ["resolve_order_id_then_cancel_order"],
                "prompt_injected_skills": ["resolve_order_id_then_cancel_order"],
                "tool_injected_skills": [],
                "used_skills": ["resolve_order_id_then_cancel_order"],
                "called_skill_tools": [],
                "call_errors": [],
            }}],
        },
    ]
    maintenance_test_results = [
        {"skill_name": "resolve_order_id_then_cancel_order", "aggregate": {"passed": True}},
        {
            "skill_name": "skip_lookup_for_frequent_orders",
            "aggregate": {
                "passed": False,
                "failure_reason": "bundle contradicts real tasks with hidden id lookup precondition",
            },
        },
    ]
    feedback_rows = _build_extractor_feedback_rows(
        extraction_events=extraction_events,
        train_details=train_details,
        maintenance_test_results=maintenance_test_results,
    )

    captured: dict = {}

    async def fake_ask_json(*, system, user, llm_config, model_name, role, metadata):
        captured["system"] = system
        captured["user"] = user
        captured["llm_config"] = llm_config
        captured["model_name"] = model_name
        captured["role"] = role
        captured["metadata"] = metadata
        return {
            "summary": (
                "Broader shortcut skills reused less reliably and caused regressions "
                "when hidden identifier-resolution preconditions varied."
            ),
            "rules": [
                {
                    "rule_id": "extractor_rule_1",
                    "text": (
                        "When success depends on resolving a canonical identifier "
                        "before an action, extract that lookup-to-action contract "
                        "explicitly instead of a shortcut that skips lookup based on "
                        "surface familiarity."
                    ),
                    "focus": "reuse",
                }
            ],
        }

    monkeypatch.setattr("academic.skill_repository.llm_maintenance._ask_json", fake_ask_json)

    result = await update_extractor_rules_from_feedback_llm(
        current_rules=[
            {
                "rule_id": "extractor_rule_1",
                "text": "Prefer narrow skills over broad benchmark summaries.",
                "focus": "scope",
            }
        ],
        feedback_rows=feedback_rows,
        llm_config="local_claude_proxy",
        model_name="claude-sonnet-4-5",
        max_rules=5,
        audit_context={"phase": "synthetic_feedback_demo"},
    )

    assert captured["role"] == "extractor_feedback"
    assert captured["metadata"]["n_feedback_rows"] == 2
    assert "## Current Extractor Rules" in captured["user"]
    assert "## Runtime Feedback Evidence" in captured["user"]
    assert "skip_lookup_for_frequent_orders" in captured["user"]
    assert "hurt_valid_count" in captured["user"]
    assert "resolve_order_id_then_cancel_order" in captured["user"]
    assert "helped_valid_count" in captured["user"]
    assert result["rules"][0]["focus"] == "reuse"
    assert "canonical identifier" in result["rules"][0]["text"]

    print("\\n=== SYSTEM ===\\n")
    print(captured["system"])
    print("\\n=== USER ===\\n")
    print(captured["user"])
    print("\\n=== OUTPUT ===\\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def test_extract_system_encodes_se_norms_and_few_shots() -> None:
    assert "Correctness first" in EXTRACT_SYSTEM
    assert "Reusability second" in EXTRACT_SYSTEM
    assert "Maintainability third" in EXTRACT_SYSTEM
    assert "evidence_span" in EXTRACT_SYSTEM
    assert "non_applicability" in EXTRACT_SYSTEM
    assert "Example A, function/interface contract" in EXTRACT_SYSTEM
    assert "Example B, workflow" in EXTRACT_SYSTEM
    assert "Example C, knowledge/rule" in EXTRACT_SYSTEM
    assert "remove_watchlist_requires_symbol_argument" in EXTRACT_SYSTEM
    assert '"input_contract": {"domain": "TradingBot"' in EXTRACT_SYSTEM
    assert "cancel_travel_booking_reuses_prior_booking_id" in EXTRACT_SYSTEM
    assert "diff_explicit_filenames_directly" in EXTRACT_SYSTEM
    assert "Bad artifact" in EXTRACT_SYSTEM
    assert "A single-task extra-call observation is runtime feedback" in EXTRACT_SYSTEM
    assert "\"skip X\" skill" in EXTRACT_SYSTEM
    assert "`metadata.allowed_tools`: only tools whose usage/arguments/order are governed" in EXTRACT_SYSTEM
    assert '`metadata.domains`: exact observed domains only; never use "all"' in EXTRACT_SYSTEM


def test_tir_extract_system_also_encodes_se_norms_and_few_shots() -> None:
    assert "Correctness first" in TIR_EXTRACT_SYSTEM
    assert "Reusability second" in TIR_EXTRACT_SYSTEM
    assert "Maintainability third" in TIR_EXTRACT_SYSTEM
    assert "One-Shot Examples" in TIR_EXTRACT_SYSTEM
    assert "function/interface extraction" in TIR_EXTRACT_SYSTEM
    assert "workflow-style computational extraction" in TIR_EXTRACT_SYSTEM
    assert "knowledge/rule extraction" in TIR_EXTRACT_SYSTEM
    assert "SKILL_NAME: mod_inverse" in TIR_EXTRACT_SYSTEM
    assert "SKILL_NAME: matmul2_mod" in TIR_EXTRACT_SYSTEM
    assert "SKILL_NAME: polygon_area_shoelace" in TIR_EXTRACT_SYSTEM
    assert "Bad output" in TIR_EXTRACT_SYSTEM
