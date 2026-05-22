import json

import pytest

from academic.extractor import EXTRACT_SYSTEM as TIR_EXTRACT_SYSTEM
from academic.benchmarks.bfcl.related.experiment import _build_extractor_feedback_rows
from academic.skill_repository.llm_maintenance import (
    EXTRACT_SYSTEM,
    JsonRoleClient,
    _ask_json,
    _parse_role_rule_update_text,
    _role_json_block,
    propose_group_refiner_actions_llm,
    update_extractor_rules_from_feedback_llm,
    update_role_rules_from_feedback_llm,
)


def test_role_json_block_preserves_code_and_skill_body_verbatim() -> None:
    long_code = "def skill_impl():\n" + "\n".join(f"    step_{idx} = {idx}" for idx in range(500))
    long_body = "Applicability: full implementation.\n```python\n" + long_code + "\n```"

    payload = json.loads(
        _role_json_block(
            {
                "body": long_body,
                "code": long_code,
                "executable_code": long_code,
                "raw": "drop this noisy raw field",
                "reason": "r" * 1000,
            }
        )
    )

    assert payload["body"] == long_body
    assert payload["code"] == long_code
    assert payload["executable_code"] == long_code
    assert "raw" not in payload
    assert len(payload["reason"]) < 500


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

    async def fake_ask_text(*, system, user, llm_config, model_name, role, metadata):
        captured["system"] = system
        captured["user"] = user
        captured["llm_config"] = llm_config
        captured["model_name"] = model_name
        captured["role"] = role
        captured["metadata"] = metadata
        return """=== ANALYSIS ===
The lookup-to-action candidate had repeated helpful use, while the shortcut was harmful when hidden identifier-resolution preconditions varied.
=== SUMMARY ===
Broader shortcut skills reused less reliably and caused regressions when hidden identifier-resolution preconditions varied.
=== RULES ===
[reuse] When success depends on resolving a canonical identifier before an action, extract that lookup-to-action contract explicitly instead of a shortcut that skips lookup based on surface familiarity.
=== END ==="""

    monkeypatch.setattr("academic.skill_repository.llm_maintenance._ask_text", fake_ask_text)

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
    assert "Compare candidates within the same candidate group" in captured["system"]
    assert "objective exposure_records, credit_records, and bundle_test_records" in captured["system"]
    assert "Return exactly this delimiter format" in captured["system"]
    assert result["rules"][0]["focus"] == "reuse"
    assert "canonical identifier" in result["rules"][0]["text"]

    print("\\n=== SYSTEM ===\\n")
    print(captured["system"])
    print("\\n=== USER ===\\n")
    print(captured["user"])
    print("\\n=== OUTPUT ===\\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


@pytest.mark.asyncio
async def test_group_refiner_action_prompt_uses_analysis_then_actions(monkeypatch) -> None:
    captured: dict = {}

    async def fake_ask_json(*, system, user, llm_config, model_name, role, metadata):
        captured["system"] = system
        captured["user"] = user
        captured["role"] = role
        captured["metadata"] = metadata
        return {
            "analysis": "mixed has helpful and harmful evidence, bad only has harmful evidence",
            "actions": [
                {
                    "candidate_group_id": "extract:r0:t1:a",
                    "skill_name": "mixed",
                    "action": "refine",
                    "reason": "helpful idea but harmful trigger breadth",
                    "patch_intent": "tighten trigger to explicit account-match requests",
                },
                {
                    "candidate_group_id": "extract:r0:t1:a",
                    "skill_name": "bad",
                    "action": "archive",
                    "reason": "harmful dominates and no helpful record",
                },
            ],
        }

    monkeypatch.setattr("academic.skill_repository.llm_maintenance._ask_json", fake_ask_json)

    result = await propose_group_refiner_actions_llm(
        group_feedback_rows=[
            {
                "candidate_group_id": "extract:r0:t1:a",
                "members": [
                    {
                        "skill_name": "mixed",
                        "exposure_count": 2,
                        "helpful_count": 1,
                        "harmful_count": 1,
                        "credit_records": [{"judgment": "harmful", "reason": "over-broad trigger"}],
                    },
                    {
                        "skill_name": "bad",
                        "exposure_count": 1,
                        "helpful_count": 0,
                        "harmful_count": 1,
                    },
                ],
            }
        ],
        current_actions=[{"skill_name": "mixed", "action": "refine"}],
        llm_config="mock",
        model_name="mock-model",
        audit_context={"phase": "unit_group_refiner"},
    )

    assert captured["role"] == "group_refiner"
    assert captured["metadata"]["n_group_feedback_rows"] == 1
    assert "First write a free-form `analysis`" in captured["system"]
    assert '"analysis"' in captured["system"]
    assert '"actions"' in captured["system"]
    assert "## Candidate Group Evidence" in captured["user"]
    assert "over-broad trigger" in captured["user"]
    assert result["actions"][0]["action"] == "refine"
    assert result["actions"][0]["patch_intent"] == "tighten trigger to explicit account-match requests"


@pytest.mark.asyncio
async def test_ask_json_retries_after_malformed_json(monkeypatch) -> None:
    calls = []

    class FlakyJsonClient(JsonRoleClient):
        async def ask_json(self, *, system, user, llm_config, model_name):
            calls.append(user)
            if len(calls) == 1:
                return {"text": '{"decision": ', "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
            return {"text": '{"ok": true}', "usage": {"prompt_tokens": 4, "completion_tokens": 2}}

    monkeypatch.setattr("academic.skill_repository.llm_maintenance._json_role_client", lambda **kwargs: FlakyJsonClient())
    monkeypatch.delenv("SKILL_MAINTENANCE_AUDIT_LOG", raising=False)

    result = await _ask_json(
        system="Return JSON.",
        user='{"input": 1}',
        llm_config="mock",
        model_name="mock-model",
        role="unit_json_retry",
        metadata={"phase": "unit"},
    )

    assert result == {"ok": True}
    assert len(calls) == 2
    assert "JSON repair retry" in calls[1]


def test_parse_role_rule_update_text_delimiter_output() -> None:
    parsed = _parse_role_rule_update_text(
        """=== ANALYSIS ===
Candidate A had more positive-credit-per-exposure than Candidate B.
=== SUMMARY ===
Prefer narrow lookup contracts.
=== RULES ===
[contract] Preserve the exact lookup result id when extracting action skills.
[scope] Split skills when candidate siblings differ in hidden state preconditions.
        === END ===""",
        max_rules=5,
        prefix="extractor_rule",
    )

    assert parsed["summary"] == "Prefer narrow lookup contracts."
    assert parsed["rules"][0]["rule_id"] == "extractor_rule_1"
    assert parsed["rules"][0]["focus"] == "contract"
    assert "lookup result id" in parsed["rules"][0]["text"]


def test_parse_role_rule_update_text_ignores_fence_and_json_residue() -> None:
    parsed = _parse_role_rule_update_text(
        """=== ANALYSIS ===
Model wrapped the rule section in stray JSON text.
=== SUMMARY ===
Keep only actionable rules.
=== RULES ===
```json
[scope] Extract one rule per distinct workflow pattern.",
"delimiter": "=== END ==="
}
```
=== END ===""",
        max_rules=5,
        prefix="extractor_rule",
    )

    assert len(parsed["rules"]) == 1
    assert parsed["rules"][0]["focus"] == "scope"
    assert parsed["rules"][0]["text"] == "Extract one rule per distinct workflow pattern."


@pytest.mark.asyncio
async def test_update_role_rules_keeps_raw_preview_for_empty_rule_update(monkeypatch) -> None:
    async def fake_ask_text(*, system, user, llm_config, model_name, role, metadata):
        return """=== ANALYSIS ===
The evidence is mature but the model failed to write a concrete rule.
=== SUMMARY ===
No rule emitted.
=== RULES ===
=== END ==="""

    monkeypatch.setattr("academic.skill_repository.llm_maintenance._ask_text", fake_ask_text)

    result = await update_role_rules_from_feedback_llm(
        role_name="extractor",
        current_rules=[],
        feedback_rows=[
            {
                "candidate_group_id": "extract:r0:t1:task",
                "source_role": "extractor",
                "members": [
                    {"skill_name": "skill_a", "helpful_count": 1},
                    {"skill_name": "skill_b", "harmful_count": 1},
                ],
            }
        ],
        llm_config="mock",
        model_name="mock-model",
        max_rules=3,
    )

    assert result["rules"] == []
    assert result["n_new_rules"] == 0
    assert result["empty_update_reason"] == "empty_rule_update"
    assert "=== RULES ===" in result["raw_response_preview"]


@pytest.mark.asyncio
async def test_update_refiner_rules_prompt_uses_refiner_label_and_empty_list(monkeypatch) -> None:
    captured: dict = {}

    async def fake_ask_text(*, system, user, llm_config, model_name, role, metadata):
        captured["system"] = system
        captured["user"] = user
        captured["role"] = role
        captured["metadata"] = metadata
        return """=== ANALYSIS ===
Refiner revision candidates show one repair preserved the useful workflow and one ignored the failing precondition.
=== SUMMARY ===
Repair the exact failing precondition.
=== RULES ===
[scope] Preserve helpful workflow steps while narrowing the harmful precondition shown by credit records.
=== END ==="""

    monkeypatch.setattr("academic.skill_repository.llm_maintenance._ask_text", fake_ask_text)

    result = await update_role_rules_from_feedback_llm(
        role_name="refiner",
        current_rules=[],
        feedback_rows=[
            {
                "candidate_group_id": "refiner:r0:mock:skill",
                "source_role": "refiner_revision",
                "members": [
                    {"skill_name": "skill__refined_r0_c0", "helpful_count": 1},
                    {"skill_name": "skill__refined_r0_c1", "harmful_count": 1},
                ],
            }
        ],
        llm_config="mock",
        model_name="mock-model",
        max_rules=3,
    )

    assert captured["role"] == "refiner_feedback"
    assert "## Current Refiner Rules\n[]" in captured["user"]
    assert "## Current Extractor Rules" not in captured["user"]
    assert captured["metadata"]["feedback_role"] == "refiner"
    assert result["rules"][0]["rule_id"] == "refiner_rule_1"
    assert result["rules"][0]["focus"] == "scope"


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
