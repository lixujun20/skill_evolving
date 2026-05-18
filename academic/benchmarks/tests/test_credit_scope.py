from academic.benchmarks.core.credit_scope import (
    credit_candidate_skill_names,
    skill_exposure_flags,
    skill_exposure_from_mappings,
)


def test_credit_scope_uses_exposed_and_called_skills_not_retrieved_only() -> None:
    metrics = {
        "retrieved_skills": ["retrieved_only", "prompt_skill", "tool_skill", "called_function", "used_skill"],
        "prompt_injected_skills": ["prompt_skill"],
        "tool_injected_skills": ["tool_skill"],
        "called_skill_functions": ["called_function"],
        "used_skills": ["used_skill"],
    }

    exposure = skill_exposure_from_mappings(metrics)

    assert exposure["credit_candidate_names"] == [
        "prompt_skill",
        "tool_skill",
        "used_skill",
        "called_function",
    ]
    assert exposure["retrieved_only_skills"] == ["retrieved_only"]
    assert credit_candidate_skill_names(metrics) == exposure["credit_candidate_names"]
    assert skill_exposure_flags("called_function", metrics) == {
        "retrieved": True,
        "prompt_injected": False,
        "tool_injected": False,
        "injected": False,
        "used": True,
        "retrieved_only": False,
    }


def test_credit_scope_falls_back_from_metrics_to_trace_fields() -> None:
    trace = {
        "retrieved_skills": ["retrieved_only", "prompt_skill"],
        "prompt_injected_skills": ["prompt_skill"],
    }

    exposure = skill_exposure_from_mappings({}, trace)

    assert exposure["credit_candidate_names"] == ["prompt_skill"]
    assert exposure["retrieved_only_skills"] == ["retrieved_only"]
