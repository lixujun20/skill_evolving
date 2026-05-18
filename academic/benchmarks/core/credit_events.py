"""Benchmark-neutral credit event helpers for skill maintenance."""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Sequence

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.maintenance_utils import now_iso

HELPFUL_REASONS = {
    "token_saving",
    "schema_help",
    "workflow_alignment",
    "correctness_gain",
}
HARMFUL_JUDGMENTS = {"harmful", "negative", "regression"}
HELPFUL_JUDGMENTS = {"helpful", "positive"}
UNCERTAIN_JUDGMENTS = {"neutral", "uncertain", "unknown", ""}
EVIDENCE_LIMIT_PER_SKILL = 24


def normalize_credit_events(
    rows: Iterable[Dict[str, Any]],
    *,
    task_id: str = "",
    benchmark: str = "",
    default_source: str = "credit_assigner",
) -> List[Dict[str, Any]]:
    """Return stable credit rows shared by benchmark adapters.

    Adapters may keep benchmark-specific fields; this helper only normalizes the
    common columns used by bundle/micro/macro orchestration.
    """

    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(rows or []):
        if not isinstance(raw, dict):
            continue
        skill_name = str(raw.get("skill_name") or raw.get("name") or "").strip()
        if not skill_name:
            continue
        judgment = normalize_judgment(raw.get("judgment") or raw.get("polarity") or raw.get("label"))
        confidence = _float_or_default(raw.get("confidence"), 0.0)
        event_task_id = str(raw.get("task_id") or raw.get("source_task_id") or task_id or "").strip()
        event = copy.deepcopy(raw)
        event.update(
            {
                "skill_name": skill_name,
                "task_id": event_task_id,
                "source_task_id": str(raw.get("source_task_id") or event_task_id),
                "benchmark": str(raw.get("benchmark") or benchmark or ""),
                "judgment": judgment,
                "confidence": confidence,
                "evidence_strength": normalize_evidence_strength(
                    raw.get("evidence_strength"),
                    confidence=confidence,
                    judgment=judgment,
                ),
                "used": bool(raw.get("used") or raw.get("called") or raw.get("prompt_injected")),
                "source": str(raw.get("source") or default_source),
                "event_index": int(raw.get("event_index") if raw.get("event_index") is not None else index),
            }
        )
        event.setdefault("maintenance_actions", [])
        event.setdefault("bundle_case_suggestions", [])
        event.setdefault("attribution_scope", "task_local")
        normalized.append(event)
    return normalized


def normalize_judgment(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in HARMFUL_JUDGMENTS:
        return "harmful"
    if raw in HELPFUL_JUDGMENTS:
        return "helpful"
    if raw in UNCERTAIN_JUDGMENTS:
        return "uncertain" if raw in {"uncertain", "unknown", ""} else "neutral"
    return raw


def normalize_evidence_strength(value: Any, *, confidence: float = 0.0, judgment: str = "") -> str:
    raw = str(value or "").strip().lower()
    if raw in {"strong", "medium", "weak", "uncertain"}:
        return raw
    if judgment in {"harmful", "helpful"} and confidence >= 0.75:
        return "strong"
    if judgment in {"harmful", "helpful"} and confidence >= 0.5:
        return "medium"
    if judgment in {"harmful", "helpful"}:
        return "weak"
    return "uncertain"


def is_strong_harmful_credit(event: Dict[str, Any], *, confidence_threshold: float = 0.65) -> bool:
    judgment = normalize_judgment(event.get("judgment"))
    confidence = _float_or_default(event.get("confidence"), 0.0)
    return judgment == "harmful" and (confidence >= confidence_threshold or bool(event.get("used")))


def is_actionable_helpful_credit(event: Dict[str, Any]) -> bool:
    if normalize_judgment(event.get("judgment")) != "helpful":
        return False
    reasons = _string_set(event.get("helpful_reasons") or event.get("reason_codes") or event.get("reasons"))
    if reasons & HELPFUL_REASONS:
        return True
    action = str(event.get("maintenance_action") or "").strip().lower()
    return action in HELPFUL_REASONS


def credit_target_names(
    credit_events: Sequence[Dict[str, Any]],
    *,
    include_helpful: bool = True,
    include_harmful: bool = True,
) -> List[str]:
    names: List[str] = []
    seen = set()
    for event in credit_events or []:
        if include_harmful and is_strong_harmful_credit(event):
            pass
        elif include_helpful and is_actionable_helpful_credit(event):
            pass
        else:
            continue
        name = str(event.get("skill_name") or "").strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def apply_credit_evidence(
    *,
    store: ArtifactStore,
    credit_events: Sequence[Dict[str, Any]],
    limit_per_skill: int = EVIDENCE_LIMIT_PER_SKILL,
) -> List[Dict[str, Any]]:
    """Append compact credit evidence to skill artifacts in place."""

    applied: List[Dict[str, Any]] = []
    for event in credit_events or []:
        name = str(event.get("skill_name") or "").strip()
        if not name:
            continue
        artifact = store.get(name)
        if artifact is None:
            applied.append({"skill_name": name, "applied": False, "reason": "missing_skill"})
            continue
        evidence = _compact_evidence_event(event)
        judgment = normalize_judgment(event.get("judgment"))
        if judgment == "harmful":
            bucket = artifact.evidence.harmful_cases
        elif judgment == "helpful":
            bucket = artifact.evidence.helpful_cases
        else:
            bucket = artifact.evidence.repeated_evidence
        bucket.append(evidence)
        if len(bucket) > limit_per_skill:
            del bucket[:-limit_per_skill]
        applied.append({"skill_name": name, "applied": True, "judgment": judgment})
    return applied


def summarize_credit_events(credit_events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total": 0,
        "helpful": 0,
        "harmful": 0,
        "neutral": 0,
        "uncertain": 0,
        "skills": {},
    }
    for event in credit_events or []:
        judgment = normalize_judgment(event.get("judgment"))
        summary["total"] += 1
        summary[judgment if judgment in summary else "uncertain"] += 1
        name = str(event.get("skill_name") or "").strip()
        if name:
            row = summary["skills"].setdefault(name, {"helpful": 0, "harmful": 0, "neutral": 0, "uncertain": 0})
            row[judgment if judgment in row else "uncertain"] += 1
    return summary


def _compact_evidence_event(event: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "task_id",
        "source_task_id",
        "benchmark",
        "judgment",
        "confidence",
        "evidence_strength",
        "reason",
        "reason_codes",
        "helpful_reasons",
        "attribution_scope",
        "used",
        "maintenance_actions",
    ]
    compact = {key: copy.deepcopy(event.get(key)) for key in keys if event.get(key) not in (None, "", [])}
    compact["recorded_at"] = now_iso()
    return compact


def _string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.strip().lower()} if value.strip() else set()
    if isinstance(value, Iterable):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return set()


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default
