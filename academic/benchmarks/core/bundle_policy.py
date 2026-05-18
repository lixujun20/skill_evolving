"""Shared bundle-case budgeting helpers for benchmark adapters."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from academic.benchmarks.core.maintenance_utils import env_int
from academic.benchmarks.core.types import SkillArtifact, SkillBundleCase

BUNDLE_CASE_ATTRS = ("positive_cases", "negative_cases", "integration_cases")


def bundle_bucket(polarity: str) -> str:
    return {
        "positive": "positive_cases",
        "negative": "negative_cases",
        "integration": "integration_cases",
    }.get(str(polarity), "integration_cases")


def bundle_case_limit_per_polarity() -> int:
    return max(1, env_int("BFCL_BUNDLE_CASE_LIMIT_PER_POLARITY", 2))


def bundle_max_total_cases() -> int:
    return max(1, env_int("BFCL_BUNDLE_MAX_TOTAL_CASES", 6))


def default_bundle_case_priority(case: SkillBundleCase, index: int = 0) -> tuple[int, float, int, str]:
    ctx = dict(case.context or {})
    credit_event = dict(ctx.get("credit_event") or {})
    source = str(case.source or "")
    confidence = float(ctx.get("confidence") or credit_event.get("confidence") or 0.0)
    is_credit = 1 if source.startswith("credit_assigner") else 0
    is_regression = 1 if (
        case.polarity in {"negative", "integration"}
        or "regression" in source
        or "failure" in source
        or "integration" in source
    ) else 0
    return (is_credit + is_regression, confidence, index, case.case_id)


def trim_bundle_cases_to_budget(
    artifact: SkillArtifact,
    *,
    per_polarity_limit: int | None = None,
    total_limit: int | None = None,
    priority_fn: Callable[[SkillBundleCase, int], tuple[Any, ...]] | None = None,
) -> bool:
    """Trim bundle cases while preserving high-value recent/credit cases.

    Returns true when the artifact bundle changed.  The artifact bundle version
    is not bumped here; callers can decide whether trimming is versioned in
    their benchmark path.
    """

    limit = max(1, int(per_polarity_limit or bundle_case_limit_per_polarity()))
    total = max(1, int(total_limit or bundle_max_total_cases()))
    priority = priority_fn or default_bundle_case_priority
    changed = False
    trimmed_case_ids: List[str] = []

    for attr in BUNDLE_CASE_ATTRS:
        cases = list(getattr(artifact.bundle, attr) or [])
        if len(cases) <= limit:
            continue
        indexed = list(enumerate(cases))
        indexed.sort(key=lambda item: priority(item[1], item[0]), reverse=True)
        kept = [case for _idx, case in indexed[:limit]]
        kept_ids = {case.case_id for case in kept}
        trimmed_case_ids.extend([case.case_id for case in cases if case.case_id not in kept_ids])
        setattr(artifact.bundle, attr, kept)
        changed = True

    ordered_groups = [
        (
            attr,
            [
                case for idx, case in sorted(
                    enumerate(list(getattr(artifact.bundle, attr) or [])),
                    key=lambda item: priority(item[1], item[0]),
                    reverse=True,
                )
            ],
        )
        for attr in BUNDLE_CASE_ATTRS
    ]
    total_cases = sum(len(cases) for _, cases in ordered_groups)
    if total_cases <= total:
        if changed:
            _record_trim_metadata(
                artifact,
                per_polarity_limit=limit,
                total_limit=total,
                trimmed_case_ids=trimmed_case_ids,
            )
        return changed

    kept: Dict[str, List[SkillBundleCase]] = {name: [] for name, _ in ordered_groups}
    group_iters = {name: list(cases) for name, cases in ordered_groups}
    while sum(len(items) for items in kept.values()) < total:
        progress = False
        for name, _cases in ordered_groups:
            remaining = group_iters[name]
            if not remaining:
                continue
            kept[name].append(remaining.pop(0))
            progress = True
            if sum(len(items) for items in kept.values()) >= total:
                break
        if not progress:
            break

    for attr in BUNDLE_CASE_ATTRS:
        setattr(artifact.bundle, attr, kept[attr])
    kept_ids = {case.case_id for cases in kept.values() for case in cases}
    trimmed_case_ids.extend(
        case.case_id
        for _name, cases in ordered_groups
        for case in cases
        if case.case_id not in kept_ids
    )
    _record_trim_metadata(
        artifact,
        per_polarity_limit=limit,
        total_limit=total,
        trimmed_case_ids=trimmed_case_ids,
        overflow=total_cases - total,
    )
    return True


def _record_trim_metadata(
    artifact: SkillArtifact,
    *,
    per_polarity_limit: int,
    total_limit: int,
    trimmed_case_ids: List[str],
    overflow: int | None = None,
) -> None:
    fixtures = dict(artifact.bundle.fixtures or {})
    fixtures.update(
        {
            "bundle_trimmed": True,
            "bundle_case_budget": {
                "per_polarity_limit": per_polarity_limit,
                "total_limit": total_limit,
            },
            "bundle_trimmed_case_ids": list(dict.fromkeys(trimmed_case_ids))[-24:],
        }
    )
    if overflow is not None:
        fixtures["bundle_split_count"] = overflow
    artifact.bundle.fixtures = fixtures
