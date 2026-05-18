from __future__ import annotations

from academic.benchmarks.core.runner import _aggregate
from academic.benchmarks.core.skill_injector import BudgetSkillInjector, compact_skill_prompt_block
from academic.benchmarks.core.types import SkillArtifact, SkillInterface


def _skill(name: str, body: str, *, tool: str = "create_ticket") -> SkillArtifact:
    return SkillArtifact(
        name=name,
        kind="interface_contract_card",
        description=f"{name} exact schema rule",
        body=body,
        interface=SkillInterface(
            summary="Use exact schema.",
            input_contract={"domain": "TicketAPI", "required_context": ["support ticket"]},
            output_contract={"tool_call": f"{tool}(title, description, priority)"},
            compatibility_notes="Do not apply to non-ticket tasks.",
        ),
        metadata={
            "domains": ["TicketAPI"],
            "allowed_tools": [tool],
            "scope": "TicketAPI support ticket creation.",
            "non_applicability": "Not for travel or spreadsheet tasks.",
        },
    )


def test_compact_skill_prompt_omits_long_body_evidence() -> None:
    artifact = _skill("ticket_priority_schema", "IMPORTANT. " + "long evidence " * 200)

    compact = compact_skill_prompt_block(artifact, max_chars=500)

    assert "ticket_priority_schema" in compact
    assert "applies_when:" in compact
    assert "do:" in compact
    assert "long evidence " not in compact
    assert len(compact) <= 500


def test_budget_skill_injector_filters_redundant_and_limits_prompt_chars() -> None:
    first = _skill("ticket_priority_schema_a", "Use title, description, priority exactly.")
    redundant = _skill("ticket_priority_schema_b", "Same ticket schema rule with extra evidence.")
    other = _skill("ticket_status_schema", "Use status exactly.", tool="update_ticket")
    injector = BudgetSkillInjector(
        mode="compact",
        max_full_skills=0,
        max_summary_skills=2,
        budget_chars=900,
        compact_chars_per_skill=420,
    )

    result = injector.select([first, redundant, other], query="create high priority support ticket")

    assert [item.artifact.name for item in result.injected] == [
        "ticket_priority_schema_a",
        "ticket_status_schema",
    ]
    assert any(item["skill_name"] == "ticket_priority_schema_b" and item["reason"] == "redundant_candidate" for item in result.filtered)
    assert result.as_event()["prompt_chars"] <= 900


def test_aggregate_reports_per_token_utility() -> None:
    details = [
        {
            "task_id": "t1",
            "runs": [
                {
                    "success": True,
                    "score": 1.0,
                    "metrics": {"total_tokens": 100, "official_valid": True},
                    "trace": {},
                }
            ],
        },
        {
            "task_id": "t2",
            "runs": [
                {
                    "success": False,
                    "score": 0.5,
                    "metrics": {"total_tokens": 300, "official_valid": False},
                    "trace": {},
                }
            ],
        },
    ]

    summary = _aggregate("toy", "test", "tag", "llm", 0, details)

    utility = summary["utility_per_million_tokens"]
    assert utility["total_tokens"] == 400
    assert utility["successes_per_million_tokens"] == 2500.0
    assert utility["score_points_per_million_tokens"] == 3750.0
    assert utility["official_valid_per_million_tokens"] == 2500.0


def test_aggregate_reports_cost_splits(monkeypatch) -> None:
    monkeypatch.setenv("SKILL_EVOLVE_INPUT_PRICE_PER_MTOK", "2")
    monkeypatch.setenv("SKILL_EVOLVE_CACHE_INPUT_PRICE_PER_MTOK", "0.5")
    monkeypatch.setenv("SKILL_EVOLVE_OUTPUT_PRICE_PER_MTOK", "10")
    from academic.benchmarks.core.cost_accounting import make_cost_event

    details = [
        {
            "task_id": "t1",
            "runs": [
                {
                    "success": True,
                    "score": 1.0,
                    "metrics": {
                        "total_tokens": 170,
                        "input_tokens": 100,
                        "cache_input_tokens": 20,
                        "completion_tokens": 50,
                        "official_valid": True,
                        "cost_events": [
                            make_cost_event(
                                role="executor",
                                phase="task_rollout",
                                benchmark="toy",
                                input_tokens=100,
                                cache_input_tokens=20,
                                output_tokens=50,
                                skill_prompt_chars=300,
                            ),
                            make_cost_event(
                                role="injector",
                                phase="executor",
                                benchmark="toy",
                                skill_prompt_chars=300,
                            ),
                        ],
                    },
                    "trace": {},
                }
            ],
        }
    ]

    summary = _aggregate("toy", "test", "tag", "llm", 0, details)

    assert summary["avg_input_tokens"] == 100
    assert summary["avg_cache_input_tokens"] == 20
    assert summary["avg_output_tokens"] == 50
    assert summary["utility_per_million_tokens"]["input_tokens"] == 100
    assert summary["cost_breakdown"]["by_role"]["executor"]["input_tokens"] == 100
    assert summary["cost_breakdown"]["by_role"]["injector"]["skill_prompt_chars"] == 300
    assert summary["cost_metrics"]["estimated_total_cost"] == 0.00071
    assert summary["correct_only_cost_breakdown"]["summary"]["estimated_cost"] == 0.00071
