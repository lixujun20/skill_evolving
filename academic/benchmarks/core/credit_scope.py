"""Benchmark-neutral skill exposure and credit-candidate policy.

Adapters own benchmark-native evidence extraction.  This module only consumes
normalized trace/metric fields and decides which skills are eligible for
runtime credit assignment.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence


def unique_skill_names(items: Sequence[Any] | None) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items or []:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def skill_exposure_from_mappings(*mappings: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    """Normalize skill exposure fields from benchmark metrics/trace mappings.

    For each canonical field, the first non-empty list wins.  This mirrors the
    existing adapter convention of preferring score metrics and falling back to
    raw trace fields.
    """

    def first_list(key: str) -> List[str]:
        for mapping in mappings:
            if not mapping:
                continue
            value = mapping.get(key)
            names = unique_skill_names(value if isinstance(value, list) else [])
            if names:
                return names
        return []

    exposure = {
        "retrieved_skills": first_list("retrieved_skills"),
        "prompt_injected_skills": first_list("prompt_injected_skills"),
        "tool_injected_skills": first_list("tool_injected_skills"),
        "used_skills": first_list("used_skills"),
        "called_skill_tools": first_list("called_skill_tools"),
        "called_skill_functions": first_list("called_skill_functions"),
    }
    exposed = unique_skill_names(
        [
            *exposure["prompt_injected_skills"],
            *exposure["tool_injected_skills"],
            *exposure["used_skills"],
            *exposure["called_skill_tools"],
            *exposure["called_skill_functions"],
        ]
    )
    direct_used = unique_skill_names(
        [
            *exposure["used_skills"],
            *exposure["called_skill_tools"],
            *exposure["called_skill_functions"],
        ]
    )
    retrieved_only = [
        name for name in exposure["retrieved_skills"] if name not in set(exposed)
    ]
    exposure.update(
        {
            "exposed_skill_names": exposed,
            "direct_used_skill_names": direct_used,
            "credit_candidate_names": exposed,
            "retrieved_only_skills": retrieved_only,
        }
    )
    return exposure


def credit_candidate_skill_names(*mappings: Mapping[str, Any] | None) -> List[str]:
    return skill_exposure_from_mappings(*mappings)["credit_candidate_names"]


def retrieved_only_skill_names(*mappings: Mapping[str, Any] | None) -> List[str]:
    return skill_exposure_from_mappings(*mappings)["retrieved_only_skills"]


def skill_exposure_flags(skill_name: str, *mappings: Mapping[str, Any] | None) -> Dict[str, bool]:
    exposure = skill_exposure_from_mappings(*mappings)
    name = str(skill_name or "").strip()
    return {
        "retrieved": name in set(exposure["retrieved_skills"]),
        "prompt_injected": name in set(exposure["prompt_injected_skills"]),
        "tool_injected": name in set(exposure["tool_injected_skills"]),
        "injected": name
        in set([*exposure["prompt_injected_skills"], *exposure["tool_injected_skills"]]),
        "used": name in set(exposure["direct_used_skill_names"]),
        "retrieved_only": name in set(exposure["retrieved_only_skills"]),
    }
