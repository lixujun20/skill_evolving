"""Shared bundle-case application helpers for benchmark adapters."""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Sequence

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.bundle_policy import bundle_bucket, trim_bundle_cases_to_budget
from academic.benchmarks.core.credit_events import (
    is_actionable_helpful_credit,
    is_strong_harmful_credit,
    normalize_judgment,
)
from academic.benchmarks.core.types import SkillBundleCase

CaseBuilder = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], SkillBundleCase | None]


def normalize_bundle_case_suggestions(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize credit-assigner bundle suggestions without inventing cases."""

    raw_items = event.get("bundle_case_suggestions") or []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    suggestions: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        skill_name = str(raw.get("skill_name") or event.get("skill_name") or "").strip()
        if not skill_name:
            continue
        polarity = str(raw.get("polarity") or _polarity_from_event(event)).strip().lower()
        if polarity not in {"positive", "negative", "integration"}:
            polarity = _polarity_from_event(event)
        suggestion = copy.deepcopy(raw)
        suggestion.update(
            {
                "skill_name": skill_name,
                "polarity": polarity,
                "source_task_id": str(raw.get("source_task_id") or event.get("source_task_id") or event.get("task_id") or ""),
                "suggestion_index": int(raw.get("suggestion_index") if raw.get("suggestion_index") is not None else index),
            }
        )
        suggestion.setdefault("task_fragment_policy", "focused_official_fragment")
        suggestions.append(suggestion)
    return suggestions


def apply_credit_bundle_suggestions(
    *,
    store: ArtifactStore,
    detail: Dict[str, Any],
    credit_events: Sequence[Dict[str, Any]],
    build_case: CaseBuilder,
    trim_cases: bool = True,
) -> List[Dict[str, Any]]:
    """Apply credit-created bundle cases using an env-specific case builder."""

    rows: List[Dict[str, Any]] = []
    for event in credit_events or []:
        if not _event_allows_case(event):
            rows.append(
                {
                    "skill_name": event.get("skill_name"),
                    "created": False,
                    "reason": "credit_not_actionable_for_bundle",
                    "judgment": normalize_judgment(event.get("judgment")),
                }
            )
            continue
        suggestions = normalize_bundle_case_suggestions(event)
        if not suggestions:
            rows.append({"skill_name": event.get("skill_name"), "created": False, "reason": "no_suggestions"})
            continue
        for suggestion in suggestions:
            skill_name = str(suggestion.get("skill_name") or "").strip()
            artifact = store.get(skill_name)
            if artifact is None:
                rows.append({"skill_name": skill_name, "created": False, "reason": "missing_skill"})
                continue
            case = build_case(detail, event, suggestion)
            if case is None:
                rows.append({"skill_name": skill_name, "created": False, "reason": "case_builder_returned_none"})
                continue
            case.polarity = str(case.polarity or suggestion.get("polarity") or "integration")
            bucket_name = bundle_bucket(case.polarity)
            bucket = list(getattr(artifact.bundle, bucket_name) or [])
            if any(existing.case_id == case.case_id for existing in bucket):
                rows.append({"skill_name": skill_name, "case_id": case.case_id, "created": False, "reason": "duplicate_case"})
                continue
            bucket.append(case)
            setattr(artifact.bundle, bucket_name, bucket)
            if trim_cases:
                trim_bundle_cases_to_budget(artifact)
            rows.append(
                {
                    "skill_name": skill_name,
                    "case_id": case.case_id,
                    "polarity": case.polarity,
                    "created": True,
                    "bucket": bucket_name,
                }
            )
    return rows


def bundle_case_rows_by_skill(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        name = str(row.get("skill_name") or "").strip()
        if not name:
            continue
        grouped.setdefault(name, []).append(copy.deepcopy(dict(row)))
    return grouped


def _event_allows_case(event: Dict[str, Any]) -> bool:
    if is_strong_harmful_credit(event):
        return True
    if is_actionable_helpful_credit(event):
        return True
    return any(
        str(item.get("polarity") or "").strip().lower() == "integration"
        for item in normalize_bundle_case_suggestions(event)
    )


def _polarity_from_event(event: Dict[str, Any]) -> str:
    judgment = normalize_judgment(event.get("judgment"))
    if judgment == "harmful":
        return "negative"
    if judgment == "helpful":
        return "positive"
    return "integration"
