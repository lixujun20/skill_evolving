"""Canonical BFCL 50-task multi-round related-task experiment driver.

This module adds a paper-oriented experiment layer on top of the existing
BFCL runner and maintenance pipeline. It preserves the underlying BFCL
executor/adapter, but standardizes:

- a deterministic curated related-task 50/50 manifest,
- multi-round train rollout with per-task online extraction,
- segment embedding / vector index bookkeeping,
- round snapshots + held-out baseline/evolve comparison artifacts,
- analysis tables and case-study candidate mining.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import contextlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.bfcl import load_bfcl_tools
from academic.benchmarks.bfcl.maintenance.adapter import (
    _bundle_test_signature,
    _latest_matching_test_result,
    bfcl_trace_segments_from_details,
    build_bfcl_overlap_timeline,
    build_bfcl_skill_bundles_llm,
    execute_bfcl_bundle_tests,
    extract_bfcl_skill_artifacts_llm,
    refine_bfcl_skill_store_llm,
    run_bfcl_overlap_refactor_llm,
    select_bfcl_maintenance_targets,
    trim_bundle_cases,
    update_skill_relation_graph,
    validate_skill_static_dependencies,
)
from academic.benchmarks.bfcl.related.manifest import (
    ACTION_VERBS as _ACTION_VERBS,
    LOOKUP_VERBS as _LOOKUP_VERBS,
    TOOL_TOKEN_RE as _TOOL_TOKEN_RE,
    build_curated_related_task_manifest,
    load_all_bfcl_tasks as _load_all_bfcl_tasks,
    load_or_build_curated_manifest,
    score_failure_family as _score_failure_family,
    task_domain as _task_domain,
    task_relatedness_score as _task_relatedness_score,
    tasks_from_manifest as _tasks_from_manifest,
    tool_family as _tool_family,
    tool_verb as _tool_verb,
    validate_curated_manifest,
)
from academic.benchmarks.bfcl.related.segment_index import (
    SegmentVectorIndex,
    SegmentVectorRow,
    segment_row_from_dict as _segment_row_from_dict,
    validate_segment_backend,
)
from academic.benchmarks.bfcl.skills import default_bfcl_skill_store
from academic.benchmarks.core.runner import (
    _aggregate,
    _result_from_dict,
    _run_bfcl_baseline,
)
from academic.benchmarks.core.types import BenchmarkTask, SkillArtifact, SkillTestResult
from academic.skill_repository.types import SkillBundleCase
from academic.config import PROJECT_ROOT, RESULTS_DIR
from academic.skill_repository.llm_maintenance import (
    assign_skill_credit_llm,
    maintenance_token_event_count,
    reset_maintenance_token_stats,
    snapshot_maintenance_token_stats,
    update_extractor_rules_from_feedback_llm,
)
from academic.skill_repository.refactor_overlap import (
    OverlapGraphState,
    TraceSegment,
    skill_to_overlap_segment,
    update_overlap_graph_state,
)
from academic.skill_repository.debug_events import DebugEventSink
from academic.skill_repository.types import SkillLineage


_TEXT_PREVIEW_LIMIT = 160
_ROLE_FEEDBACK_RULE_LIMIT = 5
_CREDIT_EVIDENCE_CASE_LIMIT = 12


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or str(default))
    except Exception:
        return int(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class _AsyncRWLock:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False

    @contextlib.asynccontextmanager
    async def read(self):
        async with self._condition:
            while self._writer:
                await self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextlib.asynccontextmanager
    async def write(self):
        async with self._condition:
            while self._writer or self._readers:
                await self._condition.wait()
            self._writer = True
        try:
            yield
        finally:
            async with self._condition:
                self._writer = False
                self._condition.notify_all()


class SkillMaintenanceLockManager:
    """Runtime locks for window-parallel micro maintenance."""

    def __init__(self, *, micro_concurrency: int | None = None) -> None:
        self.micro_semaphore = asyncio.Semaphore(max(1, int(micro_concurrency or _env_int("BFCL_MICRO_CONCURRENCY", 4))))
        self.relation_graph_lock = asyncio.Lock()
        self.store_commit_lock = asyncio.Lock()
        self.macro_barrier = asyncio.Lock()
        self._skill_locks: Dict[str, _AsyncRWLock] = {}

    def _skill_lock(self, name: str) -> _AsyncRWLock:
        key = str(name or "").strip()
        lock = self._skill_locks.get(key)
        if lock is None:
            lock = _AsyncRWLock()
            self._skill_locks[key] = lock
        return lock

    @contextlib.asynccontextmanager
    async def target_write_locks(self, names: Sequence[str]):
        ordered = sorted({str(name or "").strip() for name in names if str(name or "").strip()})
        stack = contextlib.AsyncExitStack()
        try:
            for name in ordered:
                await stack.enter_async_context(self._skill_lock(name).write())
            yield
        finally:
            await stack.aclose()

    async def snapshot_skill_versions(self, store: ArtifactStore, names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for name in sorted({str(item or "").strip() for item in names if str(item or "").strip()}):
            async with self._skill_lock(name).read():
                artifact = store.get(name)
                if artifact is None:
                    continue
                out[name] = {
                    "version": int(artifact.version or 0),
                    "bundle_version": int(artifact.bundle.bundle_version or 0),
                    "status": str(artifact.status or ""),
                }
        return out


def _first_run(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs", []) or []
    return runs[0] if runs else {}


def _normalize_role_feedback_memory(role_feedback: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = copy.deepcopy(role_feedback or {})
    extractor = dict(payload.get("extractor") or {})
    rules: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in extractor.get("rules") or []:
        row = dict(item or {})
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        dedupe = re.sub(r"\s+", " ", text).strip().lower()
        if dedupe in seen:
            continue
        seen.add(dedupe)
        rules.append(
            {
                "rule_id": str(row.get("rule_id") or f"extractor_rule_{len(rules) + 1}"),
                "text": text,
                "focus": str(row.get("focus") or "evidence").strip().lower() or "evidence",
            }
        )
        if len(rules) >= _ROLE_FEEDBACK_RULE_LIMIT:
            break
    for idx, row in enumerate(rules, start=1):
        row["rule_id"] = f"extractor_rule_{idx}"
    history = [dict(item or {}) for item in (extractor.get("history") or []) if isinstance(item, dict)][-12:]
    payload["extractor"] = {
        "rules": rules,
        "history": history,
        "last_update_summary": str(extractor.get("last_update_summary") or ""),
        "updated_at": extractor.get("updated_at"),
    }
    return payload


def _role_feedback_projection(role_feedback: Dict[str, Any] | None) -> Dict[str, Any]:
    normalized = _normalize_role_feedback_memory(role_feedback)
    extractor = dict(normalized.get("extractor") or {})
    return {
        "extractor": {
            "rules": copy.deepcopy(extractor.get("rules") or []),
            "n_rules": len(extractor.get("rules") or []),
            "history": copy.deepcopy(extractor.get("history") or []),
            "last_update_summary": extractor.get("last_update_summary") or "",
            "updated_at": extractor.get("updated_at"),
        }
    }


def _token_breakdown_delta(start_index: int) -> Dict[str, Any]:
    return snapshot_maintenance_token_stats(start_index=start_index)


def _extraction_event_records(
    *,
    detail: Dict[str, Any],
    extracted_artifacts: Sequence[SkillArtifact],
    round_index: int,
    task_index: int,
) -> List[Dict[str, Any]]:
    run = _first_run(detail)
    metrics = dict(run.get("metrics") or {})
    task_id = str(detail.get("task_id") or "")
    official_valid = metrics.get("official_valid")
    retrieved = list(metrics.get("retrieved_skills") or [])
    injected = list(metrics.get("prompt_injected_skills") or []) + list(metrics.get("tool_injected_skills") or [])
    used = list(metrics.get("used_skills") or []) + list(metrics.get("called_skill_tools") or [])
    call_errors = list(metrics.get("call_errors") or [])
    rows: List[Dict[str, Any]] = []
    for artifact in extracted_artifacts:
        logical_name = str(artifact.metadata.get("candidate_for_existing_skill") or artifact.name)
        rows.append(
            {
                "skill_name": logical_name,
                "artifact_name": artifact.name,
                "skill_version": artifact.version,
                "source_task_id": task_id,
                "round_index": round_index,
                "task_index": task_index,
                "official_valid_at_extraction": official_valid,
                "retrieved_at_extraction": logical_name in retrieved or artifact.name in retrieved,
                "injected_at_extraction": logical_name in injected or artifact.name in injected,
                "used_at_extraction": logical_name in used or artifact.name in used,
                "call_error_count_at_extraction": len(call_errors),
                "error_types_at_extraction": sorted(
                    {
                        str(item.get("type") or "").strip()
                        for item in call_errors
                        if str(item.get("type") or "").strip()
                    }
                ),
                "description": artifact.description,
                "kind": artifact.kind,
                "status": artifact.status,
                "is_pending_skill": bool(artifact.metadata.get("is_pending_skill") or artifact.status == "pending"),
                "is_promoted": bool(artifact.metadata.get("is_promoted")),
                "candidate_group_id": artifact.metadata.get("candidate_group_id"),
                "candidate_group_role": artifact.metadata.get("candidate_group_role"),
                "candidate_sample_index": artifact.metadata.get("candidate_sample_index"),
                "candidate_sample_count": artifact.metadata.get("candidate_sample_count"),
                "competition_status": artifact.metadata.get("competition_status"),
                "competes_with": list(artifact.metadata.get("competes_with") or []),
                "allowed_tools": list(artifact.metadata.get("allowed_tools") or []),
                "source_task_ids": list(artifact.metadata.get("source_task_ids") or []),
            }
        )
    return rows


def _mentioned_skill_names(detail: Dict[str, Any]) -> List[str]:
    run = _first_run(detail)
    metrics = dict(run.get("metrics") or {})
    ordered: List[str] = []
    for name in (
        list(metrics.get("retrieved_skills") or [])
        + list(metrics.get("prompt_injected_skills") or [])
        + list(metrics.get("tool_injected_skills") or [])
        + list(metrics.get("used_skills") or [])
        + list(metrics.get("called_skill_tools") or [])
    ):
        norm = str(name or "").strip()
        if norm and norm not in ordered:
            ordered.append(norm)
    return ordered


def _used_skill_names(detail: Dict[str, Any]) -> List[str]:
    run = _first_run(detail)
    metrics = dict(run.get("metrics") or {})
    return _unique_ordered(
        list(metrics.get("used_skills") or [])
        + list(metrics.get("called_skill_tools") or [])
    )


def _unique_ordered(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        norm = str(item or "").strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _credit_context_by_skill(events: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        name = str(event.get("skill_name") or "").strip()
        if not name:
            continue
        grouped[name].append(
            {
                "task_id": event.get("task_id"),
                "round_index": event.get("round_index"),
                "task_index": event.get("task_index"),
                "judgment": event.get("judgment"),
                "effect_type": event.get("effect_type"),
                "confidence": event.get("confidence"),
                "reason": event.get("reason"),
                "retrieved": event.get("retrieved"),
                "injected": event.get("injected"),
                "used": event.get("used"),
                "official_valid": event.get("official_valid"),
                "evidence": copy.deepcopy(event.get("evidence") or {}),
            }
        )
    return {name: rows[-_CREDIT_EVIDENCE_CASE_LIMIT:] for name, rows in grouped.items()}


def _strong_credit_targets(events: Sequence[Dict[str, Any]]) -> List[str]:
    targets: List[str] = []
    for event in events:
        judgment = str(event.get("judgment") or "").strip().lower()
        confidence = float(event.get("confidence") or 0.0)
        direct = bool(event.get("used")) or bool((event.get("evidence") or {}).get("used"))
        if judgment in {"harmful", "helpful"} and (confidence >= 0.75 or direct):
            name = str(event.get("skill_name") or "").strip()
            if name:
                targets.append(name)
    return _unique_ordered(targets)


def _micro_write_target_names(
    *,
    task_credit_events: Sequence[Dict[str, Any]],
    credit_bundle_cases: Sequence[Dict[str, Any]],
    relevant_skill_names: Sequence[str],
) -> List[str]:
    relevant = {str(name or "").strip() for name in relevant_skill_names if str(name or "").strip()}
    changed_case_targets = _unique_ordered(
        str(row.get("skill_name") or "")
        for row in credit_bundle_cases
        if isinstance(row, dict)
    )
    strong_targets = [
        name
        for name in _strong_credit_targets(task_credit_events)
        if not relevant or name in relevant
    ]
    return _unique_ordered([*changed_case_targets, *strong_targets])


def _credit_event_records(
    *,
    detail: Dict[str, Any],
    credit_payload: Dict[str, Any],
    round_index: int,
    task_index: int,
) -> List[Dict[str, Any]]:
    run = _first_run(detail)
    metrics = dict(run.get("metrics") or {})
    mentioned = set(_mentioned_skill_names(detail))
    task_id = str(detail.get("task_id") or "")
    task_summary = dict(credit_payload.get("task_summary") or {})
    rows: List[Dict[str, Any]] = []
    for item in list(credit_payload.get("skill_judgments") or []):
        row = dict(item or {})
        skill_name = str(row.get("skill_name") or "").strip()
        if not skill_name:
            continue
        evidence = dict(row.get("evidence") or {})
        maintenance_actions = [
            copy.deepcopy(action)
            for action in list(row.get("maintenance_actions") or [])
            if str((action or {}).get("skill_name") or skill_name).strip() == skill_name
        ]
        bundle_case_suggestions = [
            copy.deepcopy(suggestion)
            for suggestion in list(row.get("bundle_case_suggestions") or [])
            if str((suggestion or {}).get("skill_name") or skill_name).strip() == skill_name
        ]
        for action in maintenance_actions:
            action.setdefault("skill_name", skill_name)
        for suggestion in bundle_case_suggestions:
            suggestion.setdefault("skill_name", skill_name)
        rows.append(
            {
                "task_id": task_id,
                "round_index": round_index,
                "task_index": task_index,
                "skill_name": skill_name,
                "judgment": str(row.get("judgment") or "uncertain").strip().lower() or "uncertain",
                "effect_type": str(row.get("effect_type") or "unknown").strip().lower() or "unknown",
                "confidence": float(row.get("confidence") or 0.0),
                "reason": str(row.get("reason") or ""),
                "maintenance_actions": maintenance_actions,
                "bundle_case_suggestions": bundle_case_suggestions,
                "refine_required": bool(row.get("refine_required")),
                "filter_candidate": bool(row.get("filter_candidate")),
                "evidence_strength": str(row.get("evidence_strength") or "weak").strip().lower() or "weak",
                "attribution_scope": str(row.get("attribution_scope") or "none").strip().lower() or "none",
                "evidence": copy.deepcopy(evidence),
                "mentioned_in_trace": skill_name in mentioned,
                "retrieved": bool(evidence.get("retrieved", skill_name in set(metrics.get("retrieved_skills") or []))),
                "injected": bool(
                    evidence.get(
                        "injected",
                        skill_name in set(metrics.get("prompt_injected_skills") or [])
                        or skill_name in set(metrics.get("tool_injected_skills") or []),
                    )
                ),
                "used": bool(
                    evidence.get(
                        "used",
                        skill_name in set(metrics.get("used_skills") or [])
                        or skill_name in set(metrics.get("called_skill_tools") or []),
                    )
                ),
                "official_valid": metrics.get("official_valid", task_summary.get("official_valid")),
                "score": run.get("score", task_summary.get("score")),
                "n_model_steps": metrics.get("n_model_steps", task_summary.get("n_model_steps")),
                "total_tokens": metrics.get("total_tokens", task_summary.get("total_tokens")),
            }
        )
    return rows


def _apply_credit_case_evidence(
    *,
    store: ArtifactStore,
    credit_events: Sequence[Dict[str, Any]],
) -> None:
    by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in credit_events:
        name = str(event.get("skill_name") or "").strip()
        if name:
            by_name[name].append(dict(event))
    for artifact in store.all():
        events = by_name.get(artifact.name, [])
        if not events:
            continue
        helpful: List[Dict[str, Any]] = []
        harmful: List[Dict[str, Any]] = []
        for event in events:
            compact = {
                "task_id": event.get("task_id"),
                "round_index": event.get("round_index"),
                "task_index": event.get("task_index"),
                "judgment": event.get("judgment"),
                "effect_type": event.get("effect_type"),
                "confidence": event.get("confidence"),
                "reason": event.get("reason"),
                "retrieved": event.get("retrieved"),
                "injected": event.get("injected"),
                "used": event.get("used"),
                "official_valid": event.get("official_valid"),
                "evidence": copy.deepcopy(event.get("evidence") or {}),
            }
            if compact["judgment"] == "helpful":
                helpful.append(compact)
            elif compact["judgment"] == "harmful":
                harmful.append(compact)
        if helpful:
            artifact.evidence.helpful_cases = helpful[-_CREDIT_EVIDENCE_CASE_LIMIT:]
        if harmful:
            artifact.evidence.harmful_cases = harmful[-_CREDIT_EVIDENCE_CASE_LIMIT:]


def _credit_bundle_case_id(*, skill_name: str, task_id: str, judgment: str) -> str:
    safe_task = re.sub(r"[^A-Za-z0-9_]+", "_", task_id).strip("_") or "unknown_task"
    safe_judgment = re.sub(r"[^A-Za-z0-9_]+", "_", judgment).strip("_") or "credit"
    return f"{skill_name}:credit:{safe_judgment}:{safe_task}"


def _credit_bundle_case_id_for_fragment(
    *,
    skill_name: str,
    task_id: str,
    polarity: str,
    turn_indices: Sequence[int],
) -> str:
    base = _credit_bundle_case_id(skill_name=skill_name, task_id=task_id, judgment=polarity)
    if not turn_indices:
        return base
    suffix = "_".join(str(idx) for idx in turn_indices)
    return f"{base}:turns:{suffix}"


def _official_task_fragment_from_snapshot(
    task_snapshot: Dict[str, Any],
    *,
    task_id: str,
    focus_turn_indices: Sequence[int] | None = None,
    required_context_turn_indices: Sequence[int] | None = None,
) -> Dict[str, Any] | None:
    question = copy.deepcopy(task_snapshot.get("question"))
    expected = copy.deepcopy(task_snapshot.get("expected"))
    if not question or expected is None:
        return None
    focus_indices = [
        int(idx)
        for idx in (focus_turn_indices or [])
        if isinstance(idx, int) and 0 <= int(idx) < len(question) and int(idx) < len(expected)
    ]
    if not focus_indices:
        return None
    context_indices = [
        int(idx)
        for idx in (required_context_turn_indices or [])
        if isinstance(idx, int) and 0 <= int(idx) < len(question) and int(idx) < len(expected)
    ]
    valid_indices = _unique_ordered([str(idx) for idx in [*context_indices, *focus_indices]])
    ordered_indices = [int(idx) for idx in valid_indices]
    question = [copy.deepcopy(question[idx]) for idx in ordered_indices]
    expected = [copy.deepcopy(expected[idx]) for idx in ordered_indices]
    return {
        "task_id": task_id,
        "question": question,
        "expected": expected,
        "input_artifacts": copy.deepcopy(task_snapshot.get("input_artifacts") or {}),
        "metadata": copy.deepcopy(task_snapshot.get("metadata") or {}),
        "source_turn_indices": ordered_indices,
        "focus_turn_indices": focus_indices,
        "required_context_turn_indices": context_indices,
    }


def _event_credit_suggestions(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggestions = [dict(item or {}) for item in (event.get("bundle_case_suggestions") or []) if isinstance(item, dict)]
    if suggestions:
        return suggestions
    judgment = str(event.get("judgment") or "").strip().lower()
    if judgment == "harmful":
        polarity = "negative"
    elif judgment == "helpful":
        polarity = "positive"
    else:
        return []
    return [
        {
            "skill_name": event.get("skill_name"),
            "polarity": polarity,
            "reason": event.get("reason") or "",
            "source_task_id": event.get("task_id") or "",
            "focus_turn_indices": [],
            "required_context_turn_indices": [],
            "state_requirements": {},
            "expected_contract": event.get("effect_type") or "",
            "task_fragment_policy": "no_replayable_fragment",
        }
    ]


def _apply_credit_bundle_case_suggestions(
    *,
    store: ArtifactStore,
    detail: Dict[str, Any],
    credit_events: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply credit-assigned positive/negative/integration bundle suggestions.

    The credit assigner has already done attribution. We only preserve the
    minimum official task fragment plus attribution reason, avoiding another
    large trace payload in the bundle-builder prompt.
    """

    task_snapshot = copy.deepcopy(detail.get("task") or {})
    task_id = str(detail.get("task_id") or task_snapshot.get("task_id") or "")
    added: List[Dict[str, Any]] = []
    helpful_effects = {"token_saving", "schema_help", "workflow_alignment", "correctness_gain"}
    for event in credit_events:
        judgment = str(event.get("judgment") or "").strip().lower()
        confidence = float(event.get("confidence") or 0.0)
        effect_type = str(event.get("effect_type") or "").strip().lower()
        skill_name = str(event.get("skill_name") or "").strip()
        artifact = store.get(skill_name)
        if artifact is None:
            continue
        for suggestion in _event_credit_suggestions(event):
            polarity = str(suggestion.get("polarity") or "").strip().lower()
            if polarity not in {"positive", "negative", "integration"}:
                continue
            if str(suggestion.get("task_fragment_policy") or "reuse_official_fragment").strip() == "no_replayable_fragment":
                continue
            if polarity == "negative" and (judgment != "harmful" or (confidence < 0.65 and not event.get("used"))):
                continue
            if polarity == "positive" and (judgment != "helpful" or effect_type not in helpful_effects):
                continue
            if polarity == "integration" and judgment not in {"helpful", "harmful"}:
                continue
            focus_turn_indices = [
                int(idx)
                for idx in (suggestion.get("focus_turn_indices") or [])
                if isinstance(idx, int)
            ]
            required_context_turn_indices = [
                int(idx)
                for idx in (suggestion.get("required_context_turn_indices") or [])
                if isinstance(idx, int)
            ]
            task_fragment = _official_task_fragment_from_snapshot(
                task_snapshot,
                task_id=task_id,
                focus_turn_indices=focus_turn_indices,
                required_context_turn_indices=required_context_turn_indices,
            )
            if task_fragment is None:
                artifact.evidence.harmful_cases.append(copy.deepcopy(dict(event))) if judgment == "harmful" else artifact.evidence.helpful_cases.append(copy.deepcopy(dict(event)))
                continue
            case_id = _credit_bundle_case_id_for_fragment(
                skill_name=artifact.name,
                task_id=task_id,
                polarity=polarity,
                turn_indices=task_fragment.get("source_turn_indices") or [],
            )
            bucket_name = f"{polarity}_cases"
            bucket = getattr(artifact.bundle, bucket_name)
            existing_ids = {case.case_id for case in bucket}
            if case_id in existing_ids:
                continue
            source = f"credit_assigner_{polarity}"
            bucket.append(
                SkillBundleCase(
                    case_id=case_id,
                    source=source,
                    prompt=(
                        f"{polarity.title()} guard from credit assignment for {artifact.name}: "
                        f"{str(suggestion.get('reason') or event.get('reason') or '').strip()}"
                    )[:1200],
                    expected={
                        "match_task_expected": True,
                        "official_valid": True,
                        "expected_contract": str(suggestion.get("expected_contract") or effect_type or ""),
                    },
                    context={
                        "source_task_id": task_id,
                        "task_fragment": task_fragment,
                        "credit_event": copy.deepcopy(dict(event)),
                        "credit_bundle_case_suggestion": copy.deepcopy(dict(suggestion)),
                        "state_requirements": copy.deepcopy(dict(suggestion.get("state_requirements") or {})),
                    },
                    tags=[f"credit-{polarity}", effect_type or "unknown"],
                    polarity=polarity,
                )
            )
            artifact.bundle.bundle_version += 1
            artifact.bundle.fixtures = {
                **dict(artifact.bundle.fixtures or {}),
                f"last_credit_{polarity}_case_task_id": task_id,
            }
            trim_bundle_cases(artifact)
            added.append({"skill_name": artifact.name, "case_id": case_id, "task_id": task_id, "polarity": polarity})
    return added


def _append_credit_negative_bundle_cases(
    *,
    store: ArtifactStore,
    detail: Dict[str, Any],
    credit_events: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Backward-compatible wrapper for older tests/imports."""

    harmful_events = [
        event for event in credit_events
        if str(event.get("judgment") or "").strip().lower() == "harmful"
    ]
    return [
        row for row in _apply_credit_bundle_case_suggestions(store=store, detail=detail, credit_events=harmful_events)
        if row.get("polarity") == "negative"
    ]


def _aggregate_skill_credit(
    credit_events: Sequence[Dict[str, Any]],
    *,
    store: ArtifactStore,
) -> List[Dict[str, Any]]:
    if not credit_events:
        return []
    artifact_by_name = {artifact.name: artifact for artifact in store.all()}
    grouped: Dict[str, Dict[str, Any]] = {}
    for event in credit_events:
        name = str(event.get("skill_name") or "").strip()
        if not name:
            continue
        artifact = artifact_by_name.get(name)
        row = grouped.setdefault(
            name,
            {
                "skill_name": name,
                "version": artifact.version if artifact else None,
                "version_id": artifact.version_id() if artifact else "",
                "kind": artifact.kind if artifact else "",
                "skill_type": "function_like" if artifact and artifact.injection_type() == "functional" else "knowledge_like",
                "status_before_filter": artifact.status if artifact else "",
                "domains": list((artifact.metadata.get("domains") or []) if artifact else []),
                "source_task_ids": list((artifact.metadata.get("source_task_ids") or []) if artifact else []),
                "allowed_tools": list((artifact.metadata.get("allowed_tools") or []) if artifact else []),
                "retrieved_count": 0,
                "injected_count": 0,
                "used_count": 0,
                "helpful_count": 0,
                "harmful_count": 0,
                "neutral_count": 0,
                "uncertain_count": 0,
                "avg_confidence": 0.0,
                "max_confidence": 0.0,
                "negative_margin": 0,
                "cross_domain_harmful_count": 0,
                "effect_type_counts": {},
                "task_ids": [],
                "helpful_task_ids": [],
                "harmful_task_ids": [],
                "notes": [],
            },
        )
        if event.get("retrieved"):
            row["retrieved_count"] += 1
        if event.get("injected"):
            row["injected_count"] += 1
        if event.get("used"):
            row["used_count"] += 1
        judgment = str(event.get("judgment") or "uncertain").strip().lower() or "uncertain"
        if judgment == "helpful":
            row["helpful_count"] += 1
            if event.get("task_id") not in row["helpful_task_ids"]:
                row["helpful_task_ids"].append(event.get("task_id"))
        elif judgment == "harmful":
            row["harmful_count"] += 1
            if event.get("task_id") not in row["harmful_task_ids"]:
                row["harmful_task_ids"].append(event.get("task_id"))
            evidence = dict(event.get("evidence") or {})
            if evidence.get("trace_signals") and any("domain" in str(item).lower() for item in (evidence.get("trace_signals") or [])):
                row["cross_domain_harmful_count"] += 1
        elif judgment == "neutral":
            row["neutral_count"] += 1
        else:
            row["uncertain_count"] += 1
        confidence = float(event.get("confidence") or 0.0)
        row["avg_confidence"] += confidence
        row["max_confidence"] = max(float(row["max_confidence"] or 0.0), confidence)
        effect_type = str(event.get("effect_type") or "unknown").strip().lower() or "unknown"
        row["effect_type_counts"][effect_type] = int(row["effect_type_counts"].get(effect_type) or 0) + 1
        task_id = event.get("task_id")
        if task_id and task_id not in row["task_ids"]:
            row["task_ids"].append(task_id)
        reason = str(event.get("reason") or "").strip()
        if reason:
            note = f"{judgment}:{reason}"
            if note not in row["notes"]:
                row["notes"].append(note)
    rows = list(grouped.values())
    for row in rows:
        total = row["helpful_count"] + row["harmful_count"] + row["neutral_count"] + row["uncertain_count"]
        row["avg_confidence"] = round((row["avg_confidence"] / total), 4) if total else 0.0
        row["negative_margin"] = int(row["harmful_count"] or 0) - int(row["helpful_count"] or 0)
        row["task_ids"] = sorted(str(item) for item in row["task_ids"] if str(item))
        row["helpful_task_ids"] = sorted(str(item) for item in row["helpful_task_ids"] if str(item))
        row["harmful_task_ids"] = sorted(str(item) for item in row["harmful_task_ids"] if str(item))
        row["source_task_ids"] = sorted(str(item) for item in row["source_task_ids"] if str(item))
        row["allowed_tools"] = sorted(str(item) for item in row["allowed_tools"] if str(item))
        row["domains"] = sorted(str(item) for item in row["domains"] if str(item))
        row["notes"] = list(row["notes"])[:6]
    rows.sort(
        key=lambda item: (
            -int(item.get("negative_margin") or 0),
            -int(item.get("harmful_count") or 0),
            -int(item.get("used_count") or 0),
            str(item.get("skill_name") or ""),
        )
    )
    return rows


def _apply_skill_credit_filter(
    *,
    store: ArtifactStore,
    credit_summary: Sequence[Dict[str, Any]],
    threshold: int = 2,
) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    by_name = {artifact.name: artifact for artifact in store.all()}
    for row in credit_summary:
        name = str(row.get("skill_name") or "").strip()
        artifact = by_name.get(name)
        if artifact is None:
            continue
        negative_margin = int(row.get("negative_margin") or 0)
        harmful_count = int(row.get("harmful_count") or 0)
        should_disable = negative_margin >= threshold and harmful_count >= threshold
        if should_disable:
            artifact.status = "disabled"
            artifact.metadata["disabled"] = True
            artifact.metadata["disabled_reason"] = "credit_assignment_negative_margin"
            artifact.metadata["credit_filter_threshold"] = threshold
            artifact.metadata["credit_harmful_task_ids"] = list(row.get("harmful_task_ids") or [])
            artifact.metadata["credit_negative_margin"] = negative_margin
        decision = {
            "skill_name": name,
            "version": artifact.version,
            "negative_margin": negative_margin,
            "harmful_count": harmful_count,
            "helpful_count": int(row.get("helpful_count") or 0),
            "threshold": threshold,
            "disabled_after": bool(artifact.is_disabled()),
            "action": "disabled" if should_disable else "kept",
            "reason": "credit_assignment_negative_margin" if should_disable else "below_disable_threshold",
        }
        decisions.append(decision)
    decisions.sort(
        key=lambda item: (
            item["action"] != "disabled",
            -int(item.get("negative_margin") or 0),
            str(item.get("skill_name") or ""),
        )
    )
    return decisions


def _mark_prior_artifacts_pending(
    artifacts: Sequence[SkillArtifact],
    *,
    round_index: int,
    task_index: int,
    task_id: str,
) -> List[SkillArtifact]:
    pending: List[SkillArtifact] = []
    for artifact in artifacts:
        artifact.status = "pending"
        artifact.metadata["is_pending_skill"] = True
        artifact.metadata["is_promoted"] = False
        artifact.metadata["promotion_state"] = "pending"
        artifact.metadata["retrieval_disabled_reason"] = "pending_prior_candidate"
        artifact.metadata["prior_extraction_round_index"] = round_index
        artifact.metadata["prior_extraction_task_index"] = task_index
        artifact.metadata["prior_extraction_task_id"] = task_id
        artifact.metadata.setdefault("source", "llm_trace_extraction")
        pending.append(artifact)
    return pending


def _candidate_group_id(*, round_index: int, task_index: int, task_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", str(task_id or "task")).strip("_")[:64] or "task"
    return f"extract:r{round_index}:t{task_index}:{slug}"


def _unique_candidate_name(
    *,
    original_name: str,
    round_index: int,
    task_index: int,
    sample_index: int,
    existing_names: set[str],
    always_suffix: bool = False,
) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", str(original_name or "candidate_skill")).strip("_") or "candidate_skill"
    if not always_suffix and base not in existing_names:
        existing_names.add(base)
        return base
    prefix = f"{base}__candidate_r{round_index}_t{task_index}_s{sample_index}"
    name = prefix
    suffix = 1
    while name in existing_names:
        suffix += 1
        name = f"{prefix}_{suffix}"
    existing_names.add(name)
    return name


def _mark_candidate_competition_artifacts(
    artifacts: Sequence[SkillArtifact],
    *,
    round_index: int,
    task_index: int,
    task_id: str,
    sample_count: int,
    existing_names: Iterable[str],
    trial_retrieval: bool = True,
) -> List[SkillArtifact]:
    group_id = _candidate_group_id(round_index=round_index, task_index=task_index, task_id=task_id)
    seen_names = {str(name or "").strip() for name in existing_names if str(name or "").strip()}
    prepared: List[SkillArtifact] = []
    for artifact in artifacts:
        original_name = str(artifact.name or "").strip()
        sample_index = int(artifact.metadata.get("candidate_sample_index") or 0)
        artifact.name = _unique_candidate_name(
            original_name=original_name,
            round_index=round_index,
            task_index=task_index,
            sample_index=sample_index,
            existing_names=seen_names,
            always_suffix=True,
        )
        if original_name and original_name != artifact.name:
            artifact.metadata["candidate_original_name"] = original_name
            artifact.metadata["candidate_for_existing_skill"] = original_name
        artifact.status = "trial" if trial_retrieval else "pending"
        artifact.metadata["is_pending_skill"] = not trial_retrieval
        artifact.metadata["is_promoted"] = bool(trial_retrieval)
        artifact.metadata["promotion_state"] = "trial" if trial_retrieval else "pending"
        artifact.metadata["competition_status"] = "trial"
        artifact.metadata["candidate_group_id"] = group_id
        artifact.metadata["candidate_group_role"] = "alternative"
        artifact.metadata["candidate_sample_count"] = int(sample_count)
        artifact.metadata["candidate_sample_index"] = sample_index
        artifact.metadata["prior_extraction_round_index"] = round_index
        artifact.metadata["prior_extraction_task_index"] = task_index
        artifact.metadata["prior_extraction_task_id"] = task_id
        artifact.metadata.setdefault("source", "llm_trace_extraction")
        if trial_retrieval:
            artifact.metadata.pop("retrieval_disabled_reason", None)
        else:
            artifact.metadata["retrieval_disabled_reason"] = "pending_prior_candidate"
        prepared.append(artifact)
    names = [artifact.name for artifact in prepared]
    for artifact in prepared:
        competitors = [name for name in names if name != artifact.name]
        artifact.metadata["competes_with"] = competitors
        relation = dict(artifact.metadata.get("skill_relation_graph") or {})
        relation["conflicts_with"] = _unique_ordered([*list(relation.get("conflicts_with") or []), *competitors])
        artifact.metadata["skill_relation_graph"] = relation
    return prepared


async def _extract_candidate_skill_samples(
    *,
    results: List[Dict[str, Any]],
    tool_schemas: Iterable[Dict[str, Any]] | None,
    existing_artifacts: Iterable[SkillArtifact] | None,
    extractor_rules: Iterable[Dict[str, Any]] | None,
    llm_config: str,
    model_name: str | None,
    audit_context: Dict[str, Any],
    competition_enabled: bool,
    sample_count: int,
    trial_retrieval: bool,
    existing_names: Iterable[str],
) -> List[SkillArtifact]:
    if not competition_enabled or int(sample_count or 1) <= 1:
        extracted = await extract_bfcl_skill_artifacts_llm(
            results,
            tool_schemas=tool_schemas,
            existing_artifacts=existing_artifacts,
            extractor_rules=extractor_rules,
            llm_config=llm_config,
            model_name=model_name,
            audit_context=audit_context,
        )
        return _mark_prior_artifacts_pending(
            extracted,
            round_index=int(audit_context.get("round_index") or 0),
            task_index=int(audit_context.get("task_index") or 0),
            task_id=str(audit_context.get("task_id") or ""),
        )
    all_artifacts: List[SkillArtifact] = []
    sample_count = max(1, int(sample_count or 1))
    for sample_index in range(sample_count):
        sample_rules = [
            *list(extractor_rules or []),
            {
                "rule_id": f"candidate_sample_{sample_index + 1}",
                "focus": "candidate_sampling",
                "text": (
                    f"This is independent candidate sample {sample_index + 1}/{sample_count}. "
                    "Prefer a distinct precise abstraction supported by evidence; return no artifacts "
                    "instead of paraphrasing another likely candidate."
                ),
            },
        ]
        extracted = await extract_bfcl_skill_artifacts_llm(
            results,
            tool_schemas=tool_schemas,
            existing_artifacts=existing_artifacts,
            extractor_rules=sample_rules,
            llm_config=llm_config,
            model_name=model_name,
            audit_context={**dict(audit_context or {}), "candidate_sample_index": sample_index, "candidate_sample_count": sample_count},
        )
        for artifact in extracted:
            artifact.metadata["candidate_sample_index"] = sample_index
        all_artifacts.extend(extracted)
    return _mark_candidate_competition_artifacts(
        all_artifacts,
        round_index=int(audit_context.get("round_index") or 0),
        task_index=int(audit_context.get("task_index") or 0),
        task_id=str(audit_context.get("task_id") or ""),
        sample_count=sample_count,
        existing_names=existing_names,
        trial_retrieval=trial_retrieval,
    )


def _pending_skill_names_from_refactor_attempt(attempt: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    payload = dict(attempt.get("llm_payload") or {})
    for row in payload.get("affected_skill_updates") or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").strip()
        name = str(row.get("name") or "").strip()
        if name and action in {"keep", "rewrite", "merge_into_shared", "delete"}:
            names.append(name)
    for row in attempt.get("affected_updates") or []:
        if isinstance(row, dict) and str(row.get("name") or "").strip():
            names.append(str(row.get("name") or "").strip())
    return sorted(set(names))


def _promote_pending_from_refactor_report(
    *,
    store: ArtifactStore,
    refactor_report: Dict[str, Any],
) -> List[Dict[str, Any]]:
    promotions: List[Dict[str, Any]] = []
    seen_names: set[str] = set()
    for attempt in refactor_report.get("attempts") or []:
        if dict(attempt or {}).get("status") != "committed":
            continue
        group_id = str((attempt or {}).get("group_id") or "").strip()
        for name in _pending_skill_names_from_refactor_attempt(dict(attempt or {})):
            if name in seen_names:
                continue
            artifact = store.get(name)
            if artifact is None:
                continue
            if artifact.metadata.get("promotion_state") == "promoted" or artifact.metadata.get("is_promoted"):
                seen_names.add(name)
                continue
            promoted = store.promote_pending(
                name,
                reason="posterior_refactor_overlap_evidence",
                refactor_group_id=group_id,
            )
            if promoted:
                seen_names.add(name)
                promotions.append(
                    {
                        "skill_name": name,
                        "action": "promoted",
                        "reason": "posterior_refactor_overlap_evidence",
                        "refactor_group_id": group_id,
                    }
                )
    return promotions


def _dedupe_promotion_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("skill_name") or ""), str(row.get("action") or "promoted"))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(copy.deepcopy(dict(row)))
    return out


def _pending_skill_summary(store: ArtifactStore) -> Dict[str, Any]:
    pending = store.pending_artifacts()
    return {
        "n_pending": len(pending),
        "pending_skill_names": [artifact.name for artifact in pending],
    }


def _build_extractor_feedback_rows(
    *,
    extraction_events: Sequence[Dict[str, Any]],
    train_details: Sequence[Dict[str, Any]],
    maintenance_test_results: Sequence[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    if not extraction_events:
        return []
    stats: Dict[str, Dict[str, Any]] = {}
    for event in extraction_events:
        name = str(event.get("skill_name") or "").strip()
        if not name:
            continue
        row = stats.setdefault(
            name,
            {
                "skill_name": name,
                "latest_version": event.get("skill_version"),
                "source_task_ids": [],
                "extraction_rounds": [],
                "n_extractions": 0,
                "n_train_tasks_seen": 0,
                "retrieved_count": 0,
                "injected_count": 0,
                "used_count": 0,
                "helped_valid_count": 0,
                "hurt_valid_count": 0,
                "invalid_when_present_count": 0,
                "call_error_count": 0,
                "error_types": [],
                "bundle_failures": 0,
                "observations": [],
                "description": str(event.get("description") or ""),
                "allowed_tools": list(event.get("allowed_tools") or []),
            },
        )
        row["latest_version"] = max(int(row.get("latest_version") or 0), int(event.get("skill_version") or 0))
        row["n_extractions"] += 1
        if event.get("round_index") not in row["extraction_rounds"]:
            row["extraction_rounds"].append(event.get("round_index"))
        for task_id in (event.get("source_task_ids") or []):
            if task_id not in row["source_task_ids"]:
                row["source_task_ids"].append(task_id)
    for detail in train_details:
        run = _first_run(detail)
        metrics = dict(run.get("metrics") or {})
        official_valid = metrics.get("official_valid")
        present_names = []
        for name in list(metrics.get("retrieved_skills") or []) + list(metrics.get("prompt_injected_skills") or []) + list(metrics.get("tool_injected_skills") or []) + list(metrics.get("used_skills") or []) + list(metrics.get("called_skill_tools") or []):
            norm = str(name or "").strip()
            if norm and norm not in present_names:
                present_names.append(norm)
        for name in present_names:
            if name not in stats:
                continue
            row = stats[name]
            row["n_train_tasks_seen"] += 1
            retrieved = name in (metrics.get("retrieved_skills") or [])
            injected = name in (metrics.get("prompt_injected_skills") or []) or name in (metrics.get("tool_injected_skills") or [])
            used = name in (metrics.get("used_skills") or []) or name in (metrics.get("called_skill_tools") or [])
            if retrieved:
                row["retrieved_count"] += 1
            if injected:
                row["injected_count"] += 1
            if used:
                row["used_count"] += 1
            if official_valid is True and used:
                row["helped_valid_count"] += 1
            if official_valid is False and used:
                row["hurt_valid_count"] += 1
            if official_valid is False and (retrieved or injected or used):
                row["invalid_when_present_count"] += 1
            call_errors = list(metrics.get("call_errors") or [])
            row["call_error_count"] += len(call_errors)
            for item in call_errors:
                error_type = str(item.get("type") or "").strip()
                if error_type and error_type not in row["error_types"]:
                    row["error_types"].append(error_type)
            observation = (
                f"task={detail.get('task_id')} valid={official_valid} "
                f"retrieved={retrieved} injected={injected} used={used} call_errors={len(call_errors)}"
            )
            if observation not in row["observations"]:
                row["observations"].append(observation)
    for result in maintenance_test_results or []:
        skill_name = str(result.get("skill_name") or "").strip()
        if skill_name not in stats:
            continue
        aggregate = dict(result.get("aggregate") or {})
        passed = aggregate.get("passed")
        if passed is False:
            stats[skill_name]["bundle_failures"] += 1
            failure_reason = str(aggregate.get("failure_reason") or aggregate.get("summary") or "").strip()
            if failure_reason:
                note = f"bundle_failed:{failure_reason}"
                if note not in stats[skill_name]["observations"]:
                    stats[skill_name]["observations"].append(note)
    rows = list(stats.values())
    for row in rows:
        row["source_task_ids"] = sorted(set(str(item) for item in row.get("source_task_ids") or [] if str(item)))
        row["extraction_rounds"] = sorted({int(item) for item in row.get("extraction_rounds") or [] if item is not None})
        row["error_types"] = sorted(set(str(item) for item in row.get("error_types") or [] if str(item)))
        row["observations"] = list(row.get("observations") or [])[:6]
    rows.sort(
        key=lambda item: (
            -(int(item.get("hurt_valid_count") or 0) + int(item.get("bundle_failures") or 0)),
            -(int(item.get("used_count") or 0)),
            str(item.get("skill_name") or ""),
        )
    )
    return rows[:24]


def _build_candidate_group_feedback_rows(
    *,
    extraction_events: Sequence[Dict[str, Any]],
    train_details: Sequence[Dict[str, Any]],
    credit_events: Sequence[Dict[str, Any]],
    maintenance_test_results: Sequence[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    member_to_group: Dict[str, str] = {}
    for event in extraction_events:
        group_id = str(event.get("candidate_group_id") or "").strip()
        if not group_id:
            continue
        name = str(event.get("artifact_name") or event.get("skill_name") or "").strip()
        if not name:
            continue
        group = groups.setdefault(
            group_id,
            {
                "feedback_type": "candidate_group",
                "candidate_group_id": group_id,
                "source_task_ids": [],
                "members": {},
            },
        )
        source_task_id = str(event.get("source_task_id") or "").strip()
        if source_task_id and source_task_id not in group["source_task_ids"]:
            group["source_task_ids"].append(source_task_id)
        member = group["members"].setdefault(
            name,
            {
                "skill_name": name,
                "description": event.get("description") or "",
                "kind": event.get("kind") or "",
                "sample_index": event.get("candidate_sample_index"),
                "status": event.get("status") or "",
                "retrieved_count": 0,
                "injected_count": 0,
                "used_count": 0,
                "helpful_count": 0,
                "harmful_count": 0,
                "neutral_count": 0,
                "uncertain_count": 0,
                "bundle_failures": 0,
                "bundle_passes": 0,
                "task_ids": [],
                "notes": [],
            },
        )
        member_to_group[name] = group_id
    if not groups:
        return []
    for detail in train_details:
        task_id = str(detail.get("task_id") or "").strip()
        run = _first_run(detail)
        metrics = dict(run.get("metrics") or {})
        retrieved = {str(item).strip() for item in (metrics.get("retrieved_skills") or []) if str(item).strip()}
        injected = {
            str(item).strip()
            for item in [
                *list(metrics.get("prompt_injected_skills") or []),
                *list(metrics.get("tool_injected_skills") or []),
            ]
            if str(item).strip()
        }
        used = {
            str(item).strip()
            for item in [
                *list(metrics.get("used_skills") or []),
                *list(metrics.get("called_skill_tools") or []),
            ]
            if str(item).strip()
        }
        for name, group_id in member_to_group.items():
            member = groups[group_id]["members"].get(name)
            if not member:
                continue
            present = False
            if name in retrieved:
                member["retrieved_count"] += 1
                present = True
            if name in injected:
                member["injected_count"] += 1
                present = True
            if name in used:
                member["used_count"] += 1
                present = True
            if present and task_id and task_id not in member["task_ids"]:
                member["task_ids"].append(task_id)
    for event in credit_events:
        name = str(event.get("skill_name") or "").strip()
        group_id = member_to_group.get(name)
        if not group_id:
            continue
        member = groups[group_id]["members"].get(name)
        if not member:
            continue
        judgment = str(event.get("judgment") or "uncertain").strip().lower() or "uncertain"
        if judgment in {"helpful", "harmful", "neutral"}:
            member[f"{judgment}_count"] += 1
        else:
            member["uncertain_count"] += 1
        reason = str(event.get("reason") or "").strip()
        if reason:
            note = f"{judgment}:{reason}"
            if note not in member["notes"]:
                member["notes"].append(note)
    for result in maintenance_test_results or []:
        name = str(result.get("skill_name") or "").strip()
        group_id = member_to_group.get(name)
        if not group_id:
            continue
        member = groups[group_id]["members"].get(name)
        if not member:
            continue
        aggregate = dict(result.get("aggregate") or {})
        passed = aggregate.get("pass_all_tests", aggregate.get("passed"))
        if passed is True:
            member["bundle_passes"] += 1
        elif passed is False:
            member["bundle_failures"] += 1
    rows: List[Dict[str, Any]] = []
    for group in groups.values():
        members = []
        for member in group["members"].values():
            score = (
                int(member["helpful_count"]) * 3
                + int(member["used_count"]) * 2
                + int(member["injected_count"])
                + int(member["bundle_passes"]) * 2
                - int(member["harmful_count"]) * 3
                - int(member["bundle_failures"]) * 2
            )
            row = {**member, "winner_score": score}
            row["task_ids"] = sorted(str(item) for item in row.get("task_ids") or [] if str(item))
            row["notes"] = list(row.get("notes") or [])[:4]
            members.append(row)
        members.sort(key=lambda item: (-int(item.get("winner_score") or 0), str(item.get("skill_name") or "")))
        winner = members[0]["skill_name"] if members else ""
        rows.append(
            {
                "feedback_type": "candidate_group",
                "candidate_group_id": group["candidate_group_id"],
                "source_task_ids": sorted(str(item) for item in group.get("source_task_ids") or [] if str(item)),
                "winner": winner,
                "losers": [item["skill_name"] for item in members[1:]],
                "members": members,
                "comparison_summary": (
                    "winner selected by higher helpful/use/bundle signal minus harmful/failure signal"
                    if winner
                    else "no winner"
                ),
            }
        )
    rows.sort(key=lambda item: (str(item.get("candidate_group_id") or "")))
    return rows[:24]


def _candidate_group_total_usage(row: Dict[str, Any]) -> int:
    return sum(
        int(member.get("retrieved_count") or 0)
        + int(member.get("injected_count") or 0)
        + int(member.get("used_count") or 0)
        for member in (row.get("members") or [])
        if isinstance(member, dict)
    )


def _candidate_group_member_names(row: Dict[str, Any]) -> List[str]:
    return _unique_ordered(
        str(member.get("skill_name") or "")
        for member in (row.get("members") or [])
        if isinstance(member, dict)
    )


def _select_macro_candidate_group_feedback_rows(
    *,
    raw_rows: Sequence[Dict[str, Any]],
    state: Dict[str, Dict[str, Any]],
    macro_index: int,
    min_usage: int,
    low_usage_patience: int,
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    seen_groups = {str(row.get("candidate_group_id") or "").strip() for row in raw_rows if isinstance(row, dict)}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        group_id = str(row.get("candidate_group_id") or "").strip()
        if not group_id:
            continue
        usage = _candidate_group_total_usage(row)
        group_state = state.setdefault(
            group_id,
            {
                "candidate_group_id": group_id,
                "consecutive_low_usage_macros": 0,
                "last_feedback_macro_index": None,
                "last_low_usage_feedback_macro_index": None,
                "n_usage_feedback": 0,
                "n_low_usage_feedback": 0,
            },
        )
        if "consecutive_low_usage_macros" not in group_state and "consecutive_no_usage_macros" in group_state:
            group_state["consecutive_low_usage_macros"] = int(group_state.get("consecutive_no_usage_macros") or 0)
        if "last_low_usage_feedback_macro_index" not in group_state and "last_no_usage_feedback_macro_index" in group_state:
            group_state["last_low_usage_feedback_macro_index"] = group_state.get("last_no_usage_feedback_macro_index")
        if "n_low_usage_feedback" not in group_state and "n_no_usage_feedback" in group_state:
            group_state["n_low_usage_feedback"] = int(group_state.get("n_no_usage_feedback") or 0)
        group_state["last_seen_macro_index"] = macro_index
        group_state["last_usage_count"] = usage
        group_state["min_usage_threshold"] = max(1, int(min_usage or 1))
        group_state["members"] = _candidate_group_member_names(row)
        if usage >= max(1, int(min_usage or 1)):
            group_state["consecutive_low_usage_macros"] = 0
            group_state["n_usage_feedback"] = int(group_state.get("n_usage_feedback") or 0) + 1
            group_state["last_feedback_macro_index"] = macro_index
            selected.append(
                {
                    **copy.deepcopy(row),
                    "feedback_reason": "sufficient_macro_usage",
                    "macro_usage_count": usage,
                    "macro_index": macro_index,
                }
            )
            continue
        group_state["consecutive_low_usage_macros"] = int(group_state.get("consecutive_low_usage_macros") or 0) + 1
        if int(group_state["consecutive_low_usage_macros"]) >= max(1, int(low_usage_patience or 1)):
            if group_state.get("last_low_usage_feedback_macro_index") != macro_index:
                group_state["n_low_usage_feedback"] = int(group_state.get("n_low_usage_feedback") or 0) + 1
                group_state["last_low_usage_feedback_macro_index"] = macro_index
                group_state["last_feedback_macro_index"] = macro_index
                selected.append(
                    {
                        **copy.deepcopy(row),
                        "winner": "",
                        "losers": _candidate_group_member_names(row),
                        "feedback_reason": "low_reuse_below_usage_threshold",
                        "macro_usage_count": usage,
                        "min_usage_threshold": max(1, int(min_usage or 1)),
                        "consecutive_low_usage_macros": int(group_state["consecutive_low_usage_macros"]),
                        "macro_index": macro_index,
                        "comparison_summary": (
                            "This candidate group stayed below the required macro usage threshold for several "
                            "macro windows; treat this as weak low-reusability evidence for the extractor."
                        ),
                    }
                )
    for group_id, group_state in state.items():
        if group_id not in seen_groups:
            group_state["last_missing_macro_index"] = macro_index
    return selected


def _apply_candidate_group_competition_decisions(
    *,
    store: ArtifactStore,
    group_feedback_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    for group in group_feedback_rows:
        winner = str(group.get("winner") or "").strip()
        members = [dict(item or {}) for item in (group.get("members") or []) if isinstance(item, dict)]
        if not winner or len(members) < 2:
            continue
        winner_score = max(
            int(item.get("winner_score") or 0)
            for item in members
            if str(item.get("skill_name") or "").strip() == winner
        )
        for member in members:
            name = str(member.get("skill_name") or "").strip()
            artifact = store.get(name)
            if artifact is None:
                continue
            score = int(member.get("winner_score") or 0)
            if name == winner:
                artifact.metadata["competition_status"] = "winner"
                artifact.metadata["promotion_state"] = "competition_winner"
                artifact.metadata["is_promoted"] = True
                if artifact.status == "trial":
                    artifact.status = "active"
                decisions.append({"skill_name": name, "candidate_group_id": group.get("candidate_group_id"), "action": "winner", "winner_score": score})
                continue
            artifact.metadata["competition_status"] = "loser"
            artifact.metadata["competition_lost_to"] = winner
            action = "marked_loser"
            if winner_score - score >= 3 and int(member.get("harmful_count") or 0) >= int(member.get("helpful_count") or 0):
                artifact.status = "archived"
                artifact.metadata["archived_reason"] = "candidate_group_loser"
                action = "archived_loser"
            decisions.append({"skill_name": name, "candidate_group_id": group.get("candidate_group_id"), "action": action, "winner": winner, "winner_score": score})
    return decisions


def _extract_task_segments(detail: Dict[str, Any]) -> List[TraceSegment]:
    return bfcl_trace_segments_from_details([detail])


def _phase_partial_path(path: Path | None, phase: str) -> Path | None:
    if path is None:
        return None
    return path.with_name(f"{path.stem}_{phase}{path.suffix or '.json'}")


def _current_round_details_path(checkpoint_path: Path | None) -> Path | None:
    return _phase_partial_path(checkpoint_path, "current_round_details")


def _current_round_online_refactors_path(checkpoint_path: Path | None) -> Path | None:
    return _phase_partial_path(checkpoint_path, "current_round_online_refactors")


def _online_refactor_budget_from_env() -> int:
    """Online refactor is deprecated for the paper_new mainline.

    Refactor remains a round-end/posterior maintenance step. Keeping a stable
    zero budget preserves old checkpoint/result field compatibility without
    allowing per-task online LLM refactor to silently re-enter experiments.
    """

    return 0


def _current_round_store_path(checkpoint_path: Path | None) -> Path | None:
    return _phase_partial_path(checkpoint_path, "current_round_store")


def _current_round_segment_rows_path(checkpoint_path: Path | None) -> Path | None:
    return _phase_partial_path(checkpoint_path, "current_round_segment_rows")


def _current_round_overlap_state_path(checkpoint_path: Path | None) -> Path | None:
    return _phase_partial_path(checkpoint_path, "current_round_overlap_state")


def _load_saved_details(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        details = payload.get("details", [])
    else:
        details = payload
    if not isinstance(details, list):
        raise ValueError(f"Malformed details payload in {path}")
    return [item for item in details if isinstance(item, dict) and item.get("task_id")]


def _load_saved_store_snapshot(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Malformed store snapshot payload in {path}")
    artifacts = payload.get("artifacts", [])
    test_results = payload.get("test_results", [])
    if not isinstance(artifacts, list) or not isinstance(test_results, list):
        raise ValueError(f"Malformed store snapshot payload in {path}")
    return {
        "artifacts": [item for item in artifacts if isinstance(item, dict)],
        "test_results": [item for item in test_results if isinstance(item, dict)],
    }


def _load_saved_overlap_state(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Malformed overlap state payload in {path}")
    return payload


def _load_saved_segment_rows(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        rows = payload.get("rows", [])
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError(f"Malformed segment rows payload in {path}")
    return [item for item in rows if isinstance(item, dict) and item.get("segment_id")]


def _task_trace_projection(trace: Dict[str, Any]) -> Dict[str, Any]:
    tool_calls = list(trace.get("tool_calls") or [])
    return {
        "task_id": trace.get("task_id"),
        "tool_calls": tool_calls,
        "retrieved_skills": list(trace.get("retrieved_skills") or []),
        "prompt_injected_skills": list(trace.get("prompt_injected_skills") or []),
        "tool_injected_skills": list(trace.get("tool_injected_skills") or []),
        "called_skill_tools": list(trace.get("called_skill_tools") or []),
        "turn_step_counts": list(trace.get("turn_step_counts") or []),
        "n_model_steps": trace.get("n_model_steps"),
        "total_tokens": trace.get("total_tokens"),
        "completion_tokens": trace.get("completion_tokens"),
        "elapsed_s": trace.get("elapsed_s"),
        "timed_out": bool(trace.get("timed_out")),
        "n_messages": len(trace.get("messages") or []),
        "n_turns": len(trace.get("turns") or []),
        "n_debug_events": len(trace.get("debug_events") or []),
    }


def _compact_run_projection(run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "benchmark": run.get("benchmark", "bfcl_v3"),
        "task_id": run.get("task_id"),
        "success": bool(run.get("success")),
        "score": run.get("score"),
        "metrics": copy.deepcopy(run.get("metrics") or {}),
        "trace": _task_trace_projection(dict(run.get("trace") or {})),
        "error": run.get("error"),
        "run_idx": run.get("run_idx"),
    }


def _compact_task_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_id": detail.get("task_id"),
        "task": copy.deepcopy(detail.get("task") or {}),
        "n_runs": detail.get("n_runs"),
        "n_success": detail.get("n_success"),
        "avg_score": detail.get("avg_score"),
        "runs": [_compact_run_projection(run) for run in (detail.get("runs") or [])],
    }


def _compact_overlap_timeline(frames: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "frame_index": frame.get("frame_index"),
            "event_type": frame.get("event_type"),
            "task_id": frame.get("task_id"),
            "n_tasks_seen": frame.get("n_tasks_seen"),
            "n_segments": frame.get("n_segments"),
            "n_edges": frame.get("n_edges"),
            "n_cliques": frame.get("n_cliques"),
            "store_state": copy.deepcopy(frame.get("store_state") or {}),
        }
        for frame in frames
    ]


def _maybe_build_overlap_timeline(
    train_details: Sequence[Dict[str, Any]],
    *,
    store: ArtifactStore,
    output_detail_level: str,
) -> List[Dict[str, Any]]:
    if output_detail_level == "compact":
        return []
    return build_bfcl_overlap_timeline(list(train_details), store=store)


def _compact_refactor_report(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "attempts": _refactor_group_rows(report),
        "n_attempts": len(report.get("attempts") or []),
    }


def _project_round_maintenance(round_maintenance: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "maintenance_targets": list(round_maintenance.get("maintenance_targets") or []),
        "maintenance_test_results": [
            {
                "skill_name": item.get("skill_name"),
                "skill_version": item.get("skill_version"),
                "bundle_version": item.get("bundle_version"),
                "aggregate": copy.deepcopy(item.get("aggregate") or {}),
                "integration_failures": len(item.get("integration_failures") or []),
            }
            for item in (round_maintenance.get("maintenance_test_results") or [])
        ],
        "refine_decisions": copy.deepcopy(round_maintenance.get("refine_decisions") or []),
        "overlap_refactor": _compact_refactor_report(round_maintenance.get("overlap_refactor") or {}),
        "refactor_segment_coverage": copy.deepcopy(round_maintenance.get("refactor_segment_coverage") or []),
        "static_dependency_validation": copy.deepcopy(round_maintenance.get("static_dependency_validation") or []),
    }


def _combine_maintenance_reports(reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    targets: List[str] = []
    test_results: List[Dict[str, Any]] = []
    refine_decisions: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    commits: List[Dict[str, Any]] = []
    rejections: List[Dict[str, Any]] = []
    coverage: List[Dict[str, Any]] = []
    static_validations: List[Dict[str, Any]] = []
    token_breakdowns: List[Dict[str, Any]] = []
    for report in reports:
        targets.extend(str(item) for item in (report.get("maintenance_targets") or []) if str(item))
        test_results.extend(copy.deepcopy(report.get("maintenance_test_results") or []))
        refine_decisions.extend(copy.deepcopy(report.get("refine_decisions") or []))
        refactor = dict(report.get("overlap_refactor") or {})
        attempts.extend(copy.deepcopy(refactor.get("attempts") or []))
        commits.extend(copy.deepcopy(refactor.get("commits") or []))
        rejections.extend(copy.deepcopy(refactor.get("rejections") or []))
        coverage.extend(copy.deepcopy(report.get("refactor_segment_coverage") or refactor.get("refactor_segment_coverage") or []))
        static_validations.extend(copy.deepcopy(report.get("static_dependency_validation") or []))
        token_breakdowns.append(copy.deepcopy(report.get("token_breakdown") or {}))
    return {
        "maintenance_targets": _unique_ordered(targets),
        "maintenance_test_results": test_results,
        "refine_decisions": refine_decisions,
        "overlap_refactor": {
            "attempts": attempts,
            "commits": commits,
            "rejections": rejections,
            "refactor_segment_coverage": coverage,
        },
        "refactor_segment_coverage": coverage,
        "static_dependency_validation": static_validations,
        "token_breakdown": {"windows": token_breakdowns},
    }


def _project_online_refactor_attempts(attempts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "after_task_id": attempt.get("after_task_id"),
            "after_task_index": attempt.get("after_task_index"),
            "n_segments_seen": attempt.get("n_segments_seen"),
            "pending_promotions": copy.deepcopy(attempt.get("pending_promotions") or []),
            "report": _compact_refactor_report(attempt.get("report") or {}),
        }
        for attempt in attempts
    ]


def _current_round_state_projection(
    current_round_state: Dict[str, Any] | None,
    *,
    checkpoint_path: Path | None,
    store: ArtifactStore | None = None,
    segment_index: SegmentVectorIndex | None = None,
) -> Dict[str, Any] | None:
    if not current_round_state:
        return None
    train_details = list(current_round_state.get("train_details") or [])
    online_refactor_attempts = list(current_round_state.get("online_refactor_attempts") or [])
    detail_path = _current_round_details_path(checkpoint_path)
    online_refactor_path = _current_round_online_refactors_path(checkpoint_path)
    store_path = _current_round_store_path(checkpoint_path)
    segment_rows_path = _current_round_segment_rows_path(checkpoint_path)
    overlap_state_path = _current_round_overlap_state_path(checkpoint_path)
    projection = {
        "round_index": current_round_state.get("round_index"),
        "next_task_index": current_round_state.get("next_task_index"),
        "seen_refactor_cliques": copy.deepcopy(current_round_state.get("seen_refactor_cliques") or []),
        "online_refactor_budget_remaining": current_round_state.get("online_refactor_budget_remaining"),
        "role_feedback": _role_feedback_projection(current_round_state.get("role_feedback")),
        "n_extraction_events": len(current_round_state.get("extraction_events") or []),
        "n_credit_events": len(current_round_state.get("credit_events") or []),
        "n_train_details": len(train_details),
        "n_window_train_details": len(current_round_state.get("window_train_details") or []),
        "n_window_segments": len(current_round_state.get("window_segments") or []),
        "n_prefetched_train_details": len(current_round_state.get("prefetched_train_details") or []),
        "n_micro_maintenance_reports": len(current_round_state.get("micro_maintenance_reports") or []),
        "n_maintenance_windows": len(current_round_state.get("maintenance_windows") or []),
        "n_candidate_group_feedback_state": len(current_round_state.get("candidate_group_feedback_state") or {}),
        "n_online_refactor_attempts": len(online_refactor_attempts),
        "train_details_preview": [
            _compact_task_detail(detail)
            for detail in train_details[-3:]
        ],
        "online_refactor_attempts_preview": _project_online_refactor_attempts(online_refactor_attempts[-3:]),
    }
    if store is not None:
        projection["n_store_artifacts"] = len(store.all())
        projection["n_store_test_results"] = len(store.test_results())
    if segment_index is not None:
        projection["n_segment_index_rows"] = len(segment_index.rows)
    if detail_path is not None:
        projection["train_details_path"] = str(detail_path)
    if online_refactor_path is not None:
        projection["online_refactor_attempts_path"] = str(online_refactor_path)
    if store_path is not None:
        projection["store_snapshot_path"] = str(store_path)
    if segment_rows_path is not None:
        projection["segment_index_rows_path"] = str(segment_rows_path)
    if overlap_state_path is not None:
        projection["overlap_state_path"] = str(overlap_state_path)
    return projection


def _checkpoint_payload(
    *,
    tag: str,
    rounds: int,
    round_reports: Sequence[Dict[str, Any]],
    store: ArtifactStore,
    segment_index: SegmentVectorIndex,
    next_round_index: int,
    current_round_state: Dict[str, Any] | None,
    role_feedback: Dict[str, Any] | None,
    output_detail_level: str,
    checkpoint_path: Path | None,
) -> Dict[str, Any]:
    return {
        "checkpoint_version": 1,
        "tag": tag,
        "rounds_total": rounds,
        "next_round_index": next_round_index,
        "output_detail_level": output_detail_level,
        "round_reports": list(round_reports),
        "store": {
            "artifacts": [artifact.as_dict() for artifact in store.all()],
            "test_results": [item.as_dict() for item in store.test_results()],
        },
        "segment_index_rows": [row.as_dict() for row in segment_index.rows],
        "role_feedback": _role_feedback_projection(role_feedback),
        "current_round_state": _current_round_state_projection(
            current_round_state,
            checkpoint_path=checkpoint_path,
            store=store,
            segment_index=segment_index,
        ),
    }


def _write_json(path: Path | None, payload: Dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _remove_file(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    path.unlink()


def _default_output_path(mode: str, tag: str) -> Path:
    if mode == "baseline":
        return RESULTS_DIR / f"bfcl_related50_50_{tag}_baseline.json"
    if mode == "evolve":
        return RESULTS_DIR / f"bfcl_related50_50_{tag}_evolve.json"
    if mode == "analyze":
        return RESULTS_DIR / f"bfcl_related50_50_{tag}_analysis.json"
    return RESULTS_DIR / f"bfcl_related50_50_{tag}_{mode}.json"


def _write_current_round_sidecars(
    *,
    checkpoint_path: Path | None,
    current_round_state: Dict[str, Any] | None,
    store: ArtifactStore | None = None,
    segment_index: SegmentVectorIndex | None = None,
) -> None:
    details_path = _current_round_details_path(checkpoint_path)
    online_refactors_path = _current_round_online_refactors_path(checkpoint_path)
    store_path = _current_round_store_path(checkpoint_path)
    segment_rows_path = _current_round_segment_rows_path(checkpoint_path)
    overlap_state_path = _current_round_overlap_state_path(checkpoint_path)
    if not current_round_state:
        _remove_file(details_path)
        _remove_file(online_refactors_path)
        _remove_file(store_path)
        _remove_file(segment_rows_path)
        _remove_file(overlap_state_path)
        return
    if details_path is not None:
        _write_json(
            details_path,
            {
                "details": list(current_round_state.get("train_details") or []),
                "window_train_details": list(current_round_state.get("window_train_details") or []),
                "window_segments": list(current_round_state.get("window_segments") or []),
                "micro_maintenance_reports": list(current_round_state.get("micro_maintenance_reports") or []),
                "maintenance_windows": list(current_round_state.get("maintenance_windows") or []),
                "extraction_events": list(current_round_state.get("extraction_events") or []),
                "credit_events": list(current_round_state.get("credit_events") or []),
                "prefetched_train_details": list(current_round_state.get("prefetched_train_details") or []),
                "candidate_group_feedback_state": copy.deepcopy(current_round_state.get("candidate_group_feedback_state") or {}),
            },
        )
    if online_refactors_path is not None:
        _write_json(
            online_refactors_path,
            {"attempts": list(current_round_state.get("online_refactor_attempts") or [])},
        )
    if store_path is not None and store is not None:
        _write_json(
            store_path,
            {
                "artifacts": [artifact.as_dict() for artifact in store.all()],
                "test_results": [item.as_dict() for item in store.test_results()],
            },
        )
    if segment_rows_path is not None and segment_index is not None:
        _write_json(
            segment_rows_path,
            {"rows": [row.as_dict() for row in segment_index.rows]},
        )
    overlap_state = current_round_state.get("overlap_state")
    if overlap_state_path is not None and isinstance(overlap_state, OverlapGraphState):
        _write_json(overlap_state_path, overlap_state.as_dict())


def _restore_current_round_state(
    checkpoint_state: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    if not checkpoint_state:
        return None
    restored = copy.deepcopy(checkpoint_state)
    if "train_details" not in restored:
        details_path = str(restored.get("train_details_path") or "").strip()
        if details_path:
            payload = json.loads(Path(details_path).read_text())
            restored["train_details"] = _load_saved_details(Path(details_path))
            restored["window_train_details"] = [
                item for item in (payload.get("window_train_details") or []) if isinstance(item, dict) and item.get("task_id")
            ]
            restored["window_segments"] = [
                item for item in (payload.get("window_segments") or []) if isinstance(item, dict) and item.get("segment_id")
            ]
            restored["micro_maintenance_reports"] = [
                dict(item or {}) for item in (payload.get("micro_maintenance_reports") or []) if isinstance(item, dict)
            ]
            restored["maintenance_windows"] = [
                dict(item or {}) for item in (payload.get("maintenance_windows") or []) if isinstance(item, dict)
            ]
            restored["extraction_events"] = [
                dict(item or {}) for item in (payload.get("extraction_events") or []) if isinstance(item, dict)
            ]
            restored["credit_events"] = [
                dict(item or {}) for item in (payload.get("credit_events") or []) if isinstance(item, dict)
            ]
            restored["prefetched_train_details"] = [
                dict(item or {}) for item in (payload.get("prefetched_train_details") or []) if isinstance(item, dict)
            ]
            restored["candidate_group_feedback_state"] = {
                str(key): dict(value or {})
                for key, value in dict(payload.get("candidate_group_feedback_state") or {}).items()
                if str(key)
            }
        else:
            restored["train_details"] = []
            restored["window_train_details"] = []
            restored["window_segments"] = []
            restored["micro_maintenance_reports"] = []
            restored["maintenance_windows"] = []
            restored["extraction_events"] = []
            restored["credit_events"] = []
            restored["prefetched_train_details"] = []
            restored["candidate_group_feedback_state"] = {}
    if "online_refactor_attempts" not in restored:
        attempts_path = str(restored.get("online_refactor_attempts_path") or "").strip()
        if attempts_path:
            payload = json.loads(Path(attempts_path).read_text())
            attempts = payload.get("attempts", payload if isinstance(payload, list) else [])
            restored["online_refactor_attempts"] = list(attempts or [])
        else:
            restored["online_refactor_attempts"] = []
    if "store_snapshot" not in restored:
        store_path = str(restored.get("store_snapshot_path") or "").strip()
        if store_path:
            restored["store_snapshot"] = _load_saved_store_snapshot(Path(store_path))
        else:
            restored["store_snapshot"] = {"artifacts": [], "test_results": []}
    if "segment_index_rows" not in restored:
        rows_path = str(restored.get("segment_index_rows_path") or "").strip()
        if rows_path:
            restored["segment_index_rows"] = _load_saved_segment_rows(Path(rows_path))
        else:
            restored["segment_index_rows"] = []
    if "overlap_state" not in restored:
        overlap_path = str(restored.get("overlap_state_path") or "").strip()
        if overlap_path:
            restored["overlap_state"] = OverlapGraphState.from_dict(_load_saved_overlap_state(Path(overlap_path)))
        else:
            restored["overlap_state"] = OverlapGraphState()
    restored["role_feedback"] = _normalize_role_feedback_memory(restored.get("role_feedback"))
    restored["extraction_events"] = [dict(item or {}) for item in (restored.get("extraction_events") or []) if isinstance(item, dict)]
    restored["credit_events"] = [dict(item or {}) for item in (restored.get("credit_events") or []) if isinstance(item, dict)]
    restored["prefetched_train_details"] = [
        dict(item or {}) for item in (restored.get("prefetched_train_details") or []) if isinstance(item, dict)
    ]
    restored["candidate_group_feedback_state"] = {
        str(key): dict(value or {})
        for key, value in dict(restored.get("candidate_group_feedback_state") or {}).items()
        if str(key)
    }
    restored["window_train_details"] = [
        item for item in (restored.get("window_train_details") or []) if isinstance(item, dict) and item.get("task_id")
    ]
    restored["window_segments"] = [
        item for item in (restored.get("window_segments") or []) if isinstance(item, dict) and item.get("segment_id")
    ]
    restored["micro_maintenance_reports"] = [
        dict(item or {}) for item in (restored.get("micro_maintenance_reports") or []) if isinstance(item, dict)
    ]
    restored["maintenance_windows"] = [
        dict(item or {}) for item in (restored.get("maintenance_windows") or []) if isinstance(item, dict)
    ]
    return restored


def rebuild_checkpoint_from_sidecars(
    *,
    checkpoint_path: Path,
    tag: str,
    rounds: int,
    output_detail_level: str = "compact",
) -> Dict[str, Any]:
    restored = _restore_current_round_state(
        {
            "round_index": 0,
            "next_task_index": 0,
            "seen_refactor_cliques": [],
            "online_refactor_budget_remaining": _online_refactor_budget_from_env(),
            "train_details_path": str(_current_round_details_path(checkpoint_path) or ""),
            "online_refactor_attempts_path": str(_current_round_online_refactors_path(checkpoint_path) or ""),
            "store_snapshot_path": str(_current_round_store_path(checkpoint_path) or ""),
            "segment_index_rows_path": str(_current_round_segment_rows_path(checkpoint_path) or ""),
            "overlap_state_path": str(_current_round_overlap_state_path(checkpoint_path) or ""),
        }
    )
    if not restored:
        raise ValueError(f"Unable to rebuild checkpoint from sidecars for {checkpoint_path}")
    current_round_state = {
        "round_index": int(restored.get("round_index") or 0),
        "next_task_index": len(restored.get("train_details") or []),
        "train_details": list(restored.get("train_details") or []),
        "window_train_details": list(restored.get("window_train_details") or []),
        "window_segments": list(restored.get("window_segments") or []),
        "micro_maintenance_reports": list(restored.get("micro_maintenance_reports") or []),
        "maintenance_windows": list(restored.get("maintenance_windows") or []),
        "online_refactor_attempts": list(restored.get("online_refactor_attempts") or []),
        "seen_refactor_cliques": list(restored.get("seen_refactor_cliques") or []),
        "online_refactor_budget_remaining": _online_refactor_budget_from_env(),
        "role_feedback": _normalize_role_feedback_memory(restored.get("role_feedback")),
        "extraction_events": list(restored.get("extraction_events") or []),
        "credit_events": list(restored.get("credit_events") or []),
        "candidate_group_feedback_state": copy.deepcopy(restored.get("candidate_group_feedback_state") or {}),
        "overlap_state": restored.get("overlap_state") if isinstance(restored.get("overlap_state"), OverlapGraphState) else OverlapGraphState(),
    }
    restored_store = copy.deepcopy(restored.get("store_snapshot") or {"artifacts": [], "test_results": []})
    restored_segment_rows = list(restored.get("segment_index_rows") or [])
    restored_store_wrapper = ArtifactStore(
        restored_store.get("artifacts") or [],
        test_results=restored_store.get("test_results") or [],
    )
    restored_segment_index = SegmentVectorIndex(strict_embeddings=False)
    restored_segment_index.load_rows(restored_segment_rows)
    payload = {
        "checkpoint_version": 1,
        "tag": tag,
        "rounds_total": rounds,
        "next_round_index": 0,
        "output_detail_level": output_detail_level,
        "round_reports": [],
        "store": restored_store,
        "segment_index_rows": restored_segment_rows,
        "role_feedback": _role_feedback_projection(restored.get("role_feedback")),
        "current_round_state": _current_round_state_projection(
            current_round_state,
            checkpoint_path=checkpoint_path,
            store=restored_store_wrapper,
            segment_index=restored_segment_index,
        ),
    }
    _write_json(checkpoint_path, payload)
    return payload


def _skill_versions_table(skills: Iterable[SkillArtifact]) -> List[Dict[str, Any]]:
    rows = []
    for skill in skills:
        rows.append(
            {
                "name": skill.name,
                "version": skill.version,
                "version_id": skill.version_id(),
                "version_kind": skill.version_kind(),
                "status": skill.status,
                "parent_version": skill.lineage.parent_version,
                "parent_version_id": skill.lineage.parent_version_id,
                "refactor_group_id": skill.lineage.refactor_group_id,
                "source_task_ids": list(skill.metadata.get("source_task_ids") or []),
                "dependencies": list(skill.dependencies or []),
                "bundle_case_count": len(skill.bundle.all_cases()),
            }
        )
    rows.sort(key=lambda item: (item["name"], item["version"]))
    return rows


def _skill_history_rows(skills: Iterable[SkillArtifact]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for skill in skills:
        snapshots = list(skill.history or []) + [skill.as_dict()]
        seen_versions: set[tuple[str, int]] = set()
        for snapshot in snapshots:
            name = str(snapshot.get("name") or skill.name)
            try:
                version = int(snapshot.get("version") or 1)
            except Exception:
                version = 1
            key = (name, version)
            if key in seen_versions:
                continue
            seen_versions.add(key)
            metadata = dict(snapshot.get("metadata") or {})
            lineage_raw = dict(snapshot.get("lineage") or {})
            if lineage_raw:
                lineage = SkillLineage(
                    parent_version=lineage_raw.get("parent_version"),
                    parent_version_id=str(lineage_raw.get("parent_version_id") or ""),
                    version_kind=str(lineage_raw.get("version_kind") or "seed"),
                    migration_reason=str(lineage_raw.get("migration_reason") or ""),
                    refined_from_result_ids=list(lineage_raw.get("refined_from_result_ids") or []),
                    refactor_group_id=str(lineage_raw.get("refactor_group_id") or ""),
                )
            else:
                lineage = skill.lineage
            bundle = dict(snapshot.get("bundle") or {})
            row = {
                "name": name,
                "version": version,
                "version_id": f"{name}@v{version}",
                "version_kind": metadata.get("version_kind") or lineage.version_kind or "seed",
                "status": str(snapshot.get("status") or "active"),
                "parent_version": lineage.parent_version,
                "parent_version_id": lineage.parent_version_id,
                "refactor_group_id": lineage.refactor_group_id or metadata.get("refactor_group_id"),
                "source_task_ids": list(metadata.get("source_task_ids") or []),
                "dependencies": list(snapshot.get("dependencies") or []),
                "bundle_case_count": sum(len(bundle.get(key_name) or []) for key_name in ("positive_cases", "negative_cases", "integration_cases")),
                "history_index": len(rows),
            }
            rows.append(row)
    rows.sort(key=lambda item: (item["name"], item["version"]))
    for idx, row in enumerate(rows):
        row["history_index"] = idx
    return rows


def _refactor_group_rows(overlap_refactor: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for attempt in overlap_refactor.get("attempts", []) or []:
        clique = dict(attempt.get("clique") or {})
        llm_payload = dict(attempt.get("llm_payload") or {})
        decision = dict(llm_payload.get("decision") or {})
        rows.append(
            {
                "group_id": attempt.get("group_id"),
                "repair_round": attempt.get("repair_round"),
                "status": attempt.get("status"),
                "reason": attempt.get("reason") or decision.get("reason"),
                "confidence": decision.get("confidence"),
                "clique_id": clique.get("clique_id"),
                "clique_size": len(clique.get("segment_ids") or []),
                "edge_weight_sum": clique.get("edge_weight_sum"),
                "segment_ids": list(clique.get("segment_ids") or []),
                "shared_skill_name": ((attempt.get("shared_skill") or {}).get("name")),
                "affected_skill_names": [
                    str(item.get("name") or "")
                    for item in (attempt.get("affected_updates") or [])
                    if str(item.get("name") or "")
                ],
            }
        )
    return rows


def _collect_refactor_group_rows(evolve_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    if evolve_summary.get("refactor_groups"):
        return list(evolve_summary.get("refactor_groups") or [])
    rows: List[Dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    for round_row in (evolve_summary.get("rounds") or []):
        sources = [round_row.get("overlap_refactor") or {}]
        for online in (round_row.get("online_refactor_attempts") or []):
            sources.append(online.get("report") or {})
        for source in sources:
            for row in _refactor_group_rows(source):
                key = (str(row.get("group_id") or ""), row.get("repair_round"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    return rows


def _build_test_help_links(
    *,
    test_details: List[Dict[str, Any]],
    train_task_ids: Sequence[str],
    skills: Iterable[SkillArtifact],
) -> List[Dict[str, Any]]:
    source_by_skill: Dict[str, Dict[str, Any]] = {}
    for skill in skills:
        source_by_skill[skill.name] = {
            "skill_name": skill.name,
            "version": skill.version,
            "version_id": skill.version_id(),
            "source_task_ids": list(skill.metadata.get("source_task_ids") or []),
            "refactor_group_id": skill.lineage.refactor_group_id or skill.metadata.get("refactor_group_id"),
        }
    links: List[Dict[str, Any]] = []
    train_set = set(str(task_id) for task_id in train_task_ids)
    for detail in test_details:
        run = _first_run(detail)
        metrics = run.get("metrics") or {}
        retrieved = list(metrics.get("retrieved_skills") or [])
        injected = list(metrics.get("prompt_injected_skills") or []) + list(metrics.get("tool_injected_skills") or [])
        used = list(metrics.get("used_skills") or []) + list(metrics.get("called_skill_tools") or [])
        link_rows: List[Dict[str, Any]] = []
        seen = set()
        for name in retrieved + injected + used:
            if name in seen or name not in source_by_skill:
                continue
            seen.add(name)
            src = source_by_skill[name]
            related_train_ids = [task_id for task_id in src["source_task_ids"] if task_id in train_set]
            link_rows.append(
                {
                    "skill_name": name,
                    "version": src["version"],
                    "version_id": src["version_id"],
                    "retrieved": name in retrieved,
                    "injected": name in injected,
                    "used": name in used,
                    "source_train_task_ids": related_train_ids,
                    "refactor_group_id": src["refactor_group_id"],
                }
            )
        links.append(
            {
                "task_id": detail.get("task_id"),
                "official_valid": metrics.get("official_valid"),
                "retrieved_skills": retrieved,
                "injected_skills": injected,
                "used_skills": used,
                "links": link_rows,
            }
        )
    return links


def _heldout_win_case_candidates(
    *,
    compare_rows: Sequence[Dict[str, Any]],
    test_help_links: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    help_by_task = {str(row.get("task_id") or ""): row for row in test_help_links}
    candidates: List[Dict[str, Any]] = []
    for row in compare_rows:
        task_id = str(row.get("task_id") or "")
        if row.get("baseline_official_valid") is not False or row.get("evolve_official_valid") is not True:
            continue
        help_row = help_by_task.get(task_id, {})
        links = list(help_row.get("links") or [])
        used_links = [item for item in links if item.get("used")]
        primary = used_links[0] if used_links else (links[0] if links else {})
        candidates.append(
            {
                "task_id": task_id,
                "baseline_official_valid": row.get("baseline_official_valid"),
                "evolve_official_valid": row.get("evolve_official_valid"),
                "skill_name": primary.get("skill_name"),
                "skill_version": primary.get("version"),
                "skill_version_id": primary.get("version_id"),
                "source_train_task_ids": list(primary.get("source_train_task_ids") or []),
                "refactor_group_id": primary.get("refactor_group_id"),
                "retrieval_path": {
                    "retrieved_skills": list(help_row.get("retrieved_skills") or []),
                    "injected_skills": list(help_row.get("injected_skills") or []),
                    "used_skills": list(help_row.get("used_skills") or []),
                    "links": links,
                },
            }
        )
    return candidates


def build_analysis_artifacts(
    *,
    manifest: Dict[str, Any],
    baseline_summary: Dict[str, Any],
    evolve_summary: Dict[str, Any],
) -> Dict[str, Any]:
    baseline_test = baseline_summary.get("test_summary") or baseline_summary
    evolve_test = evolve_summary.get("test_summary") or {}
    baseline_details = baseline_summary.get("test_details") or baseline_summary.get("details") or []
    evolve_details = evolve_summary.get("test_details") or []
    baseline_by_task = {detail.get("task_id"): detail for detail in baseline_details}
    evolve_by_task = {detail.get("task_id"): detail for detail in evolve_details}
    per_test_compare: List[Dict[str, Any]] = []
    for task_id in manifest.get("test_task_ids", []):
        baseline_run = _first_run(baseline_by_task.get(task_id, {}))
        evolve_run = _first_run(evolve_by_task.get(task_id, {}))
        b_metrics = baseline_run.get("metrics") or {}
        e_metrics = evolve_run.get("metrics") or {}
        per_test_compare.append(
            {
                "task_id": task_id,
                "baseline_official_valid": b_metrics.get("official_valid"),
                "evolve_official_valid": e_metrics.get("official_valid"),
                "baseline_score": baseline_run.get("score"),
                "evolve_score": evolve_run.get("score"),
                "baseline_total_tokens": b_metrics.get("total_tokens"),
                "evolve_total_tokens": e_metrics.get("total_tokens"),
                "baseline_model_steps": b_metrics.get("n_model_steps"),
                "evolve_model_steps": e_metrics.get("n_model_steps"),
                "baseline_retrieved_skills": b_metrics.get("retrieved_skills") or [],
                "evolve_retrieved_skills": e_metrics.get("retrieved_skills") or [],
            }
        )
    test_help_links = _build_test_help_links(
        test_details=evolve_details,
        train_task_ids=manifest.get("train_task_ids") or [],
        skills=[
            ArtifactStore([item]).all()[0] if isinstance(item, dict) else item
            for item in (evolve_summary.get("skills") or [])
        ],
    )
    refactor_groups = _collect_refactor_group_rows(evolve_summary)
    skills = [
        ArtifactStore([item]).all()[0] if isinstance(item, dict) else item
        for item in (evolve_summary.get("skills") or [])
    ]
    skill_versions = _skill_history_rows(skills)
    rounds = list(evolve_summary.get("rounds") or [])
    top_cliques = [row for row in refactor_groups if row.get("status") == "committed"][:3]
    weaker_cliques = [row for row in refactor_groups if row.get("status") != "committed"][:3]
    versioned_candidates = [row for row in skill_versions if row.get("version", 0) > 1][:8]
    heldout_win_candidates = _heldout_win_case_candidates(
        compare_rows=per_test_compare,
        test_help_links=test_help_links,
    )
    train_trace_candidate = None
    for task_id in manifest.get("train_task_ids", []):
        evidence = []
        for round_row in rounds:
            for detail in round_row.get("train_details", []) or []:
                if detail.get("task_id") != task_id:
                    continue
                run = _first_run(detail)
                metrics = run.get("metrics") or {}
                evidence.append(
                    {
                        "round_index": round_row.get("round_index"),
                        "official_valid": metrics.get("official_valid"),
                        "total_tokens": metrics.get("total_tokens"),
                        "n_model_steps": metrics.get("n_model_steps"),
                        "retrieved_skills": metrics.get("retrieved_skills") or [],
                        "used_skills": metrics.get("used_skills") or [],
                        "call_errors": metrics.get("call_errors") or [],
                    }
                )
        if len(evidence) >= 2:
            train_trace_candidate = {"task_id": task_id, "rounds": evidence}
            break
    return {
        "end_to_end_metrics_summary": {
            "baseline": {
                "official_valid_rate": baseline_test.get("official_valid_rate"),
                "avg_score": baseline_test.get("avg_score"),
                "avg_total_tokens": baseline_test.get("avg_total_tokens"),
                "avg_model_steps": baseline_test.get("avg_model_steps"),
            },
            "evolve": {
                "official_valid_rate": evolve_test.get("official_valid_rate"),
                "avg_score": evolve_test.get("avg_score"),
                "avg_total_tokens": evolve_test.get("avg_total_tokens"),
                "avg_model_steps": evolve_test.get("avg_model_steps"),
            },
        },
        "per_round_train_metrics": [
            {
                "round_index": round_row.get("round_index"),
                **dict(round_row.get("train_summary") or {}),
            }
            for round_row in rounds
        ],
        "per_test_task_compare_rows": per_test_compare,
        "skill_evolution_table": skill_versions,
        "clique_refactor_table": refactor_groups,
        "skill_help_evidence_links": test_help_links,
        "skill_credit_summary": copy.deepcopy(evolve_summary.get("skill_credit_summary") or []),
        "skill_credit_filter_decisions": copy.deepcopy(evolve_summary.get("skill_credit_filter_decisions") or []),
        "role_feedback": copy.deepcopy(evolve_summary.get("role_feedback") or {}),
        "extractor_feedback_timeline": [
            {
                "round_index": round_row.get("round_index"),
                "role_feedback": copy.deepcopy(round_row.get("role_feedback") or {}),
                "extractor_feedback_rows": copy.deepcopy(round_row.get("extractor_feedback_rows") or []),
                "skill_credit_summary": copy.deepcopy(round_row.get("skill_credit_summary") or []),
                "skill_credit_filter_decisions": copy.deepcopy(round_row.get("skill_credit_filter_decisions") or []),
            }
            for round_row in rounds
        ],
        "case_study_candidates": {
            "top_cliques": top_cliques,
            "weaker_cliques": weaker_cliques,
            "versioned_skills": versioned_candidates,
            "train_trace_candidate": train_trace_candidate,
            "baseline_fail_evolve_pass_candidates": heldout_win_candidates[:5],
        },
    }


def validate_experiment_config(
    *,
    manifest: Dict[str, Any],
    output_path: Path | None,
    save_skills: Path | None,
    checkpoint_path: Path | None,
    strict_embeddings: bool,
    expected_train: int | None = 50,
    expected_test: int | None = 50,
    require_task_rows: bool = True,
    segment_backend: str | None = None,
    segment_db_url: str | None = None,
) -> Dict[str, Any]:
    validation = validate_curated_manifest(
        manifest,
        expected_train=expected_train,
        expected_test=expected_test,
        require_task_rows=require_task_rows,
    )
    out_checks = []
    for label, path in (("output", output_path), ("save_skills", save_skills), ("checkpoint", checkpoint_path)):
        if path is None:
            continue
        out_checks.append(
            {
                "label": label,
                "path": str(path),
                "parent_exists": path.parent.exists(),
                "parent_writable": os.access(path.parent, os.W_OK) if path.parent.exists() else False,
            }
        )
    backend = (segment_backend or os.environ.get("BFCL_SEGMENT_INDEX_BACKEND", "memory")).strip().lower()
    db_url = segment_db_url or os.environ.get("BFCL_SEGMENT_DB_URL", "").strip() or None
    backend_ok, backend_message = validate_segment_backend(backend=backend, db_url=db_url)
    return {
        "manifest": validation,
        "output_paths": out_checks,
        "segment_backend": backend,
        "segment_db_url_configured": bool(db_url),
        "segment_backend_ok": backend_ok,
        "segment_backend_message": backend_message,
        "strict_embeddings": strict_embeddings,
        "ok": validation["ok"] and backend_ok and all(item["parent_exists"] and item["parent_writable"] for item in out_checks),
    }


# Legacy!
async def _run_round_refine_and_refactor(
    *,
    store: ArtifactStore,
    train_details: List[Dict[str, Any]],
    segment_index: SegmentVectorIndex,
    overlap_state: OverlapGraphState,
    new_segments: List[TraceSegment] | None = None,
    exclude_segment_sets: set[tuple[str, ...]] | None,
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    round_index: int,
    tag: str,
    phase: str = "macro",
    artifact_names: List[str] | None = None,
    credit_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
    run_bundle_builder: bool = True,
    run_bundle_tests: bool = True,
    run_refine: bool = True,
    run_overlap_refactor: bool = True,
) -> Dict[str, Any]:
    token_start = maintenance_token_event_count()
    targets = _unique_ordered(artifact_names or select_bfcl_maintenance_targets(store, train_details=train_details))
    if run_bundle_builder and targets:
        await build_bfcl_skill_bundles_llm(
            store,
            train_details=train_details,
            replay_details=[],
            llm_config=llm_config,
            model_name=model_name,
            artifact_names=targets,
            audit_context={"phase": f"{phase}_bundle_build", "round_index": round_index, "experiment": tag},
        )
    test_results: List[SkillTestResult] = []
    for artifact in [skill for skill in store.all() if run_bundle_tests and skill.name in set(targets)]:
        test_signature = _bundle_test_signature(
            artifact,
            max_steps_per_turn=max_steps_per_turn,
            adapter_mode="official",
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
        )
        result = _latest_matching_test_result(store, artifact=artifact, test_signature=test_signature)
        if result is None:
            result = await execute_bfcl_bundle_tests(
                artifact,
                tools=tools,
                llm_config=llm_config,
                model_name=model_name,
                adapter_mode="official",
                execution_backend=execution_backend,
                prompt_style=prompt_style,
                tool_api_style=tool_api_style,
                max_steps_per_turn=max_steps_per_turn,
                temperature=temperature,
                synthetic_continue=synthetic_continue,
                explicit_skill_tool=explicit_skill_tool,
                max_case_seconds=max_task_seconds or 180.0,
            )
        else:
            result.aggregate = {**dict(result.aggregate or {}), "cached_reuse": True}
        if not result.aggregate.get("cached_reuse"):
            store.add_test_result(result)
        test_results.append(result)
    refine_decisions = []
    if run_refine and test_results:
        refine_decisions = await refine_bfcl_skill_store_llm(
            store,
            maintenance_test_results=test_results,
            llm_config=llm_config,
            model_name=model_name,
            artifact_names=targets,
            credit_context_by_skill=credit_context_by_skill,
            audit_context={"phase": f"{phase}_refine", "round_index": round_index, "experiment": tag},
        )
    overlap_refactor: Dict[str, Any] = {"attempts": [], "refactor_segment_coverage": []}
    if run_overlap_refactor and new_segments is not None:
        overlap_refactor = await run_bfcl_overlap_refactor_llm(
            store,
            train_details=train_details,
            segment_embeddings=segment_index.embedding_map(
                segment_ids=[segment.segment_id for segment in new_segments],
                round_index=round_index,
            ),
            overlap_state=overlap_state,
            new_segments=list(new_segments),
            exclude_segment_sets=exclude_segment_sets,
            tools=tools,
            llm_config=llm_config,
            model_name=model_name,
            adapter_mode="official",
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            max_steps_per_turn=max_steps_per_turn,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
            max_case_seconds=max_task_seconds or 180.0,
            max_repair_rounds=int(os.environ.get("BFCL_REFACTOR_MAX_REPAIR_ROUNDS", "1") or "1"),
            audit_context={"phase": f"{phase}_refactor_overlap", "round_index": round_index, "experiment": tag},
        )
    static_dependency_validation = validate_skill_static_dependencies(store, targets)
    return {
        "maintenance_targets": targets,
        "maintenance_test_results": [item.as_dict() for item in test_results],
        "refine_decisions": refine_decisions,
        "overlap_refactor": overlap_refactor,
        "refactor_segment_coverage": copy.deepcopy(overlap_refactor.get("refactor_segment_coverage") or []),
        "static_dependency_validation": static_dependency_validation,
        "run_bundle_builder": run_bundle_builder,
        "run_bundle_tests": run_bundle_tests,
        "run_refine": run_refine,
        "run_overlap_refactor": run_overlap_refactor,
        "token_breakdown": _token_breakdown_delta(token_start),
    }


_ORIGINAL_RUN_ROUND_REFINE_AND_REFACTOR = _run_round_refine_and_refactor


async def _run_bundle_test_and_refine_targets(
    *,
    store: ArtifactStore,
    targets: Sequence[str],
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    round_index: int,
    tag: str,
    phase: str,
    credit_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
    dependency_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
    lock_manager: SkillMaintenanceLockManager | None = None,
    run_refine: bool = True,
    max_repair_rounds: int = 1,
) -> Dict[str, Any]:
    target_names = _unique_ordered(targets)
    test_results: List[SkillTestResult] = []
    refine_decisions: List[Dict[str, Any]] = []
    for repair_round in range(max(1, int(max_repair_rounds or 1))):
        round_results: List[SkillTestResult] = []
        for artifact in [skill for skill in store.all() if skill.name in set(target_names)]:
            test_signature = _bundle_test_signature(
                artifact,
                max_steps_per_turn=max_steps_per_turn,
                adapter_mode="official",
                execution_backend=execution_backend,
                prompt_style=prompt_style,
                tool_api_style=tool_api_style,
                synthetic_continue=synthetic_continue,
                explicit_skill_tool=explicit_skill_tool,
            )
            result = _latest_matching_test_result(store, artifact=artifact, test_signature=test_signature)
            if result is None:
                result = await execute_bfcl_bundle_tests(
                    artifact,
                    tools=tools,
                    llm_config=llm_config,
                    model_name=model_name,
                    adapter_mode="official",
                    execution_backend=execution_backend,
                    prompt_style=prompt_style,
                    tool_api_style=tool_api_style,
                    max_steps_per_turn=max_steps_per_turn,
                    temperature=temperature,
                    synthetic_continue=synthetic_continue,
                    explicit_skill_tool=explicit_skill_tool,
                    max_case_seconds=max_task_seconds or 180.0,
                )
            else:
                result.aggregate = {**dict(result.aggregate or {}), "cached_reuse": True}
            if not result.aggregate.get("cached_reuse"):
                if lock_manager is not None:
                    async with lock_manager.store_commit_lock:
                        store.add_test_result(result)
                else:
                    store.add_test_result(result)
            round_results.append(result)
        test_results.extend(round_results)
        failed_targets = [
            result.skill_name
            for result in round_results
            if not bool(result.aggregate.get("pass_all_tests"))
        ]
        if not run_refine or not failed_targets:
            break
        decisions = await refine_bfcl_skill_store_llm(
            store,
            maintenance_test_results=round_results,
            llm_config=llm_config,
            model_name=model_name,
            artifact_names=failed_targets,
            credit_context_by_skill=credit_context_by_skill,
            dependency_context_by_skill=dependency_context_by_skill,
            audit_context={
                "phase": f"{phase}_refine",
                "round_index": round_index,
                "experiment": tag,
                "repair_round": repair_round,
            },
        )
        strict_failed_targets = {
            result.skill_name
            for result in round_results
            if any(
                run.variant == "with_skill"
                and not run.passed
                and (run.metadata or {}).get("contract_failures")
                for run in (result.unit_case_runs or [])
            )
        }
        for decision in decisions:
            name = str(decision.get("skill_name") or "").strip()
            action = str(decision.get("action") or "").strip().lower()
            if name in strict_failed_targets and action == "keep":
                decision["action"] = "strict_failure_kept"
                decision["original_action"] = "keep"
                decision["reason"] = (
                    f"{decision.get('reason') or ''} "
                    "Override: with-skill strict contract failures remain after bundle testing; "
                    "do not treat keep as a successful repair."
                ).strip()
        refine_decisions.extend(decisions)
        if not any(str(item.get("action") or "") not in {"keep"} for item in decisions):
            break
    return {
        "maintenance_targets": target_names,
        "maintenance_test_results": [item.as_dict() for item in test_results],
        "refine_decisions": refine_decisions,
    }


def _credit_pre_refine_target_names(events: Sequence[Dict[str, Any]], targets: Sequence[str]) -> List[str]:
    target_set = set(targets)
    names: List[str] = []
    for event in events:
        name = str(event.get("skill_name") or "").strip()
        if not name or name not in target_set:
            continue
        judgment = str(event.get("judgment") or "").strip().lower()
        confidence = float(event.get("confidence") or 0.0)
        direct = bool(event.get("used")) or bool((event.get("evidence") or {}).get("used"))
        if event.get("refine_required") or (judgment == "harmful" and (confidence >= 0.65 or direct)):
            names.append(name)
    return _unique_ordered(names)


def _relation_names_for_artifact(artifact: SkillArtifact) -> List[str]:
    relation = dict(artifact.metadata.get("skill_relation_graph") or {})
    names: List[str] = []
    names.extend(str(item).strip() for item in (artifact.dependencies or []) if str(item).strip())
    for key in ("calls", "called_by", "co_retrieved_with", "co_used_with", "derived_from", "refines", "conflicts_with"):
        names.extend(str(item).strip() for item in (relation.get(key) or []) if str(item).strip())
    return _unique_ordered(name for name in names if name and name != artifact.name)


def _reference_skill_names_for_targets(store: ArtifactStore, target_names: Sequence[str]) -> List[str]:
    target_set = {str(item or "").strip() for item in target_names if str(item or "").strip()}
    refs: List[str] = []
    for name in target_set:
        artifact = store.get(name)
        if artifact is None:
            continue
        refs.extend(name for name in _relation_names_for_artifact(artifact) if name not in target_set)
    return _unique_ordered(refs)


def _dependency_context_projection(store: ArtifactStore, target_names: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name in _unique_ordered(target_names):
        artifact = store.get(name)
        if artifact is None:
            out[name] = []
            continue
        rows: List[Dict[str, Any]] = []
        for ref_name in _relation_names_for_artifact(artifact):
            ref = store.get(ref_name)
            if ref is None:
                continue
            rows.append(
                {
                    "name": ref.name,
                    "kind": ref.kind,
                    "description": ref.description,
                    "version": ref.version,
                    "bundle_version": ref.bundle.bundle_version,
                    "status": ref.status,
                    "dependencies": list(ref.dependencies or []),
                    "allowed_tools": list((ref.metadata or {}).get("allowed_tools") or []),
                    "domains": list((ref.metadata or {}).get("domains") or []),
                }
            )
        out[name] = rows[:12]
    return out


def _mark_targets_stale_for_changed_refs(
    *,
    store: ArtifactStore,
    target_names: Sequence[str],
    before_refs: Dict[str, Dict[str, Any]],
    after_refs: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    changed: List[Dict[str, Any]] = []
    for ref_name, before in before_refs.items():
        after = after_refs.get(ref_name)
        if after is None or before != after:
            changed.append({"skill_name": ref_name, "before": before, "after": after})
    if not changed:
        return []
    for name in _unique_ordered(target_names):
        artifact = store.get(name)
        if artifact is None:
            continue
        artifact.stale = True
        artifact.metadata["stale_due_to_dependency_change"] = True
        artifact.metadata["stale_dependency_changes"] = copy.deepcopy(changed[-12:])
    return changed


async def _update_skill_relation_graph_locked(
    *,
    lock_manager: SkillMaintenanceLockManager | None,
    store: ArtifactStore,
    **kwargs: Any,
) -> None:
    if lock_manager is None:
        update_skill_relation_graph(store, **kwargs)
        return
    async with lock_manager.relation_graph_lock:
        update_skill_relation_graph(store, **kwargs)


async def _run_credit_pre_refine_targets(
    *,
    store: ArtifactStore,
    target_names: Sequence[str],
    task_credit_events: Sequence[Dict[str, Any]],
    credit_context_by_skill: Dict[str, List[Dict[str, Any]]] | None,
    llm_config: str,
    model_name: str | None,
    round_index: int,
    task_index: int,
    tag: str,
    dependency_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
) -> List[Dict[str, Any]]:
    pre_refine_targets = _credit_pre_refine_target_names(task_credit_events, target_names)
    if not pre_refine_targets:
        return []
    synthetic_results: List[SkillTestResult] = []
    by_skill: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in task_credit_events:
        name = str(event.get("skill_name") or "").strip()
        if name in set(pre_refine_targets):
            by_skill[name].append(copy.deepcopy(dict(event)))
    for name in pre_refine_targets:
        artifact = store.get(name)
        if artifact is None:
            continue
        harmful = any(str(event.get("judgment") or "").strip().lower() == "harmful" for event in by_skill.get(name, []))
        synthetic_results.append(
            SkillTestResult(
                result_id=f"credit_pre_refine:{name}:r{round_index}:t{task_index}",
                skill_name=name,
                skill_version=artifact.version,
                bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
                bundle_version=artifact.bundle.bundle_version,
                run_label="credit_pre_refine",
                unit_case_runs=[],
                aggregate={
                    "pass_all_tests": False,
                    "n_regressed": 1 if harmful else 0,
                    "n_improved": 0 if harmful else 1,
                    "credit_pre_refine": True,
                },
                integration_failures=[
                    {
                        "source": "credit_assignment",
                        "credit_event": copy.deepcopy(event),
                    }
                    for event in by_skill.get(name, [])
                ],
                created_at="",
            )
        )
    if not synthetic_results:
        return []
    return await refine_bfcl_skill_store_llm(
        store,
        maintenance_test_results=synthetic_results,
        llm_config=llm_config,
        model_name=model_name,
        artifact_names=pre_refine_targets,
        credit_context_by_skill=credit_context_by_skill,
        dependency_context_by_skill=dependency_context_by_skill,
        audit_context={
            "phase": "micro_credit_pre_refine",
            "round_index": round_index,
            "task_index": task_index,
            "experiment": tag,
        },
    )


async def _run_window_overlap_refactor(
    *,
    store: ArtifactStore,
    train_details: List[Dict[str, Any]],
    segment_index: SegmentVectorIndex,
    overlap_state: OverlapGraphState,
    window_segments: List[TraceSegment],
    exclude_segment_sets: set[tuple[str, ...]] | None,
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    round_index: int,
    tag: str,
    phase: str,
) -> Dict[str, Any]:
    return await run_bfcl_overlap_refactor_llm(
        store,
        train_details=train_details,
        segment_embeddings=segment_index.embedding_map(
            segment_ids=[segment.segment_id for segment in window_segments],
            round_index=round_index,
        ),
        overlap_state=overlap_state,
        new_segments=list(window_segments),
        exclude_segment_sets=exclude_segment_sets,
        tools=tools,
        llm_config=llm_config,
        model_name=model_name,
        adapter_mode="official",
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        max_steps_per_turn=max_steps_per_turn,
        temperature=temperature,
        synthetic_continue=synthetic_continue,
        explicit_skill_tool=explicit_skill_tool,
        max_case_seconds=max_task_seconds or 180.0,
        max_repair_rounds=int(os.environ.get("BFCL_REFACTOR_MAX_REPAIR_ROUNDS", "1") or "1"),
        audit_context={"phase": f"{phase}_refactor_overlap", "round_index": round_index, "experiment": tag},
    )


async def _run_micro_maintenance(
    *,
    store: ArtifactStore,
    detail: Dict[str, Any],
    task_credit_events: Sequence[Dict[str, Any]],
    credit_bundle_cases: Sequence[Dict[str, Any]],
    relevant_skill_names: Sequence[str],
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    round_index: int,
    task_index: int,
    tag: str,
    credit_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
    dependency_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
    lock_manager: SkillMaintenanceLockManager | None = None,
) -> Dict[str, Any]:
    token_start = maintenance_token_event_count()
    targets = _micro_write_target_names(
        task_credit_events=task_credit_events,
        credit_bundle_cases=credit_bundle_cases,
        relevant_skill_names=relevant_skill_names,
    )
    report: Dict[str, Any] = {
        "phase": "micro",
        "task_id": detail.get("task_id"),
        "task_index": task_index,
        "maintenance_targets": targets,
        "relevant_skill_names": _unique_ordered(relevant_skill_names),
        "maintenance_test_results": [],
        "refine_decisions": [],
        "static_dependency_validation": [],
        "credit_bundle_cases": copy.deepcopy(list(credit_bundle_cases)),
        "credit_negative_bundle_cases": [
            copy.deepcopy(row) for row in credit_bundle_cases if row.get("polarity") == "negative"
        ],
        "run_overlap_refactor": False,
        "run_extractor_trl": False,
        "run_pending_revocation": False,
        "run_full_store_bundle_rebuild": False,
    }
    if not targets:
        report["reason"] = "no credit-created bundle case or strong task-local credit"
        report["token_breakdown"] = _token_breakdown_delta(token_start)
        return report
    max_repair_rounds = int(os.environ.get("BFCL_MICRO_REFINE_MAX_REPAIR_ROUNDS", "1") or "1")
    pre_refine_decisions = await _run_credit_pre_refine_targets(
        store=store,
        target_names=targets,
        task_credit_events=task_credit_events,
        credit_context_by_skill=credit_context_by_skill,
        llm_config=llm_config,
        model_name=model_name,
        round_index=round_index,
        task_index=task_index,
        tag=tag,
        dependency_context_by_skill=dependency_context_by_skill,
    )
    tested = await _run_bundle_test_and_refine_targets(
        store=store,
        targets=targets,
        tools=tools,
        llm_config=llm_config,
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        max_steps_per_turn=max_steps_per_turn,
        max_task_seconds=max_task_seconds,
        temperature=temperature,
        synthetic_continue=synthetic_continue,
        explicit_skill_tool=explicit_skill_tool,
        round_index=round_index,
        tag=tag,
        phase="micro",
        credit_context_by_skill=credit_context_by_skill,
        dependency_context_by_skill=dependency_context_by_skill,
        lock_manager=lock_manager,
        run_refine=True,
        max_repair_rounds=max_repair_rounds,
    )
    report.update(tested)
    report["pre_refine_decisions"] = copy.deepcopy(pre_refine_decisions)
    report["refine_decisions"] = [*pre_refine_decisions, *list(report.get("refine_decisions") or [])]
    report["static_dependency_validation"] = validate_skill_static_dependencies(store, targets)
    report["token_breakdown"] = _token_breakdown_delta(token_start)
    return report


async def _run_locked_micro_maintenance(
    *,
    lock_manager: SkillMaintenanceLockManager,
    store: ArtifactStore,
    target_names: Sequence[str],
    **kwargs: Any,
) -> Dict[str, Any]:
    target_names = _unique_ordered(target_names)
    if not target_names:
        return await _run_micro_maintenance(store=store, **kwargs)
    async with lock_manager.micro_semaphore:
        reference_names = _reference_skill_names_for_targets(store, target_names)
        before_refs = await lock_manager.snapshot_skill_versions(store, reference_names)
        dependency_context = _dependency_context_projection(store, target_names)
        async with lock_manager.target_write_locks(target_names):
            report = await _run_micro_maintenance(
                store=store,
                dependency_context_by_skill=dependency_context,
                lock_manager=lock_manager,
                **kwargs,
            )
        after_refs = await lock_manager.snapshot_skill_versions(store, reference_names)
        changed_refs = _mark_targets_stale_for_changed_refs(
            store=store,
            target_names=target_names,
            before_refs=before_refs,
            after_refs=after_refs,
        )
        if changed_refs:
            report["stale_dependency_changes"] = copy.deepcopy(changed_refs)
        report["locked_targets"] = list(target_names)
        return report


async def _run_macro_maintenance(
    *,
    store: ArtifactStore,
    train_details: List[Dict[str, Any]],
    segment_index: SegmentVectorIndex,
    overlap_state: OverlapGraphState,
    window_segments: List[TraceSegment],
    exclude_segment_sets: set[tuple[str, ...]] | None,
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    round_index: int,
    tag: str,
    phase: str,
    credit_events: Sequence[Dict[str, Any]] | None = None,
    extractor_trl_enabled: bool = False,
) -> Dict[str, Any]:
    token_start = maintenance_token_event_count()
    legacy_hook = globals().get("_run_round_refine_and_refactor")
    if legacy_hook is not _ORIGINAL_RUN_ROUND_REFINE_AND_REFACTOR:
        report = await legacy_hook(
            store=store,
            train_details=train_details,
            segment_index=segment_index,
            overlap_state=overlap_state,
            new_segments=window_segments,
            exclude_segment_sets=exclude_segment_sets,
            tools=tools,
            llm_config=llm_config,
            model_name=model_name,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            max_steps_per_turn=max_steps_per_turn,
            max_task_seconds=max_task_seconds,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
            round_index=round_index,
            tag=tag,
            phase=phase,
            credit_context_by_skill=_credit_context_by_skill(credit_events or []),
            run_bundle_builder=False,
            run_bundle_tests=False,
            run_refine=False,
            run_overlap_refactor=True,
        )
        report = dict(report or {})
        report.setdefault("run_bundle_builder", False)
        report.setdefault("run_bundle_tests", False)
        report.setdefault("run_refine", False)
        report.setdefault("run_overlap_refactor", True)
        report.setdefault("token_breakdown", _token_breakdown_delta(token_start))
        return report
    overlap_refactor = await _run_window_overlap_refactor(
        store=store,
        train_details=train_details,
        segment_index=segment_index,
        overlap_state=overlap_state,
        window_segments=window_segments,
        exclude_segment_sets=exclude_segment_sets,
        tools=tools,
        llm_config=llm_config,
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        max_steps_per_turn=max_steps_per_turn,
        max_task_seconds=max_task_seconds,
        temperature=temperature,
        synthetic_continue=synthetic_continue,
        explicit_skill_tool=explicit_skill_tool,
        round_index=round_index,
        tag=tag,
        phase=phase,
    )
    credit_filter_rows = [
        {
            "skill_name": event.get("skill_name"),
            "task_id": event.get("task_id"),
            "reason": event.get("reason"),
            "confidence": event.get("confidence"),
        }
        for event in (credit_events or [])
        if event.get("filter_candidate")
    ]
    credit_summary = _aggregate_skill_credit(credit_events or [], store=store)
    credit_filter_threshold = max(1, int(os.environ.get("BFCL_CREDIT_FILTER_THRESHOLD", "2") or "2"))
    credit_filter_decisions = _apply_skill_credit_filter(
        store=store,
        credit_summary=credit_summary,
        threshold=credit_filter_threshold,
    )
    return {
        "phase": phase,
        "maintenance_targets": [],
        "maintenance_test_results": [],
        "refine_decisions": [],
        "overlap_refactor": overlap_refactor,
        "refactor_segment_coverage": copy.deepcopy(overlap_refactor.get("refactor_segment_coverage") or []),
        "pending_skill_filter_candidates": credit_filter_rows,
        "skill_credit_summary": credit_summary,
        "skill_credit_filter_decisions": credit_filter_decisions,
        "extractor_trl_update": {"enabled": bool(extractor_trl_enabled), "ran": False},
        "static_dependency_validation": validate_skill_static_dependencies(store, []),
        "run_bundle_builder": False,
        "run_bundle_tests": False,
        "run_refine": False,
        "run_overlap_refactor": True,
        "token_breakdown": _token_breakdown_delta(token_start),
    }


async def _run_related_evolve_experiment(
    *,
    manifest: Dict[str, Any],
    cache_dir: Path,
    llm_config: str,
    model_name: str | None,
    tools: List[Dict[str, Any]],
    rounds: int,
    data_source: str,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    top_k_skills: int,
    min_skill_score: float,
    skill_injection_mode: str,
    max_steps_per_turn: int,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    tag: str,
    save_skills: Path | None,
    use_handwritten_skills: bool,
    checkpoint_path: Path | None,
    output_path: Path | None,
    output_detail_level: str,
    extractor_trl_enabled: bool,
    experiment_variant: str,
    epochs: int | None = None,
    micro_maintenance_step: int = 1,
    macro_maintenance_step: int = 10,
    test_concurrency: int = 1,
    train_window_concurrency: int = 1,
    candidate_competition_enabled: bool = False,
    candidate_sample_count: int = 1,
    candidate_trial_retrieval: bool = True,
) -> Dict[str, Any]:
    reset_maintenance_token_stats()
    effective_epochs = max(1, int(epochs if epochs is not None else 1))
    if rounds != effective_epochs:
        rounds = effective_epochs
    micro_maintenance_step = max(1, int(micro_maintenance_step or 1))
    macro_maintenance_step = max(1, int(macro_maintenance_step or 10))
    test_concurrency = max(1, int(test_concurrency or 1))
    train_window_concurrency = max(1, int(train_window_concurrency or 1))
    micro_concurrency = max(1, _env_int("BFCL_MICRO_CONCURRENCY", 4))
    candidate_competition_enabled = bool(
        candidate_competition_enabled or _env_bool("BFCL_CANDIDATE_COMPETITION_ENABLED", False)
    )
    candidate_sample_count = max(
        1,
        int(os.environ.get("BFCL_CANDIDATE_SAMPLE_COUNT", str(candidate_sample_count)) or candidate_sample_count),
    )
    candidate_trial_retrieval = bool(candidate_trial_retrieval and _env_bool("BFCL_CANDIDATE_TRIAL_RETRIEVAL", True))
    candidate_group_min_usage = max(1, _env_int("BFCL_CANDIDATE_GROUP_MIN_USAGE", 1))
    candidate_group_low_usage_patience = max(
        1,
        _env_int(
            "BFCL_CANDIDATE_GROUP_LOW_USAGE_MACROS",
            _env_int("BFCL_CANDIDATE_GROUP_NO_USAGE_MACROS", 3),
        ),
    )
    train_tasks, test_tasks = _tasks_from_manifest(manifest, cache_dir=cache_dir, data_source=data_source)
    strict_embeddings = os.environ.get("BFCL_STRICT_SEGMENT_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes"}
    segment_index = SegmentVectorIndex(strict_embeddings=strict_embeddings)
    round_reports: List[Dict[str, Any]] = []
    checkpoint = _phase_partial_path(output_path, "checkpoint") if checkpoint_path is None else checkpoint_path
    store = ArtifactStore()
    maintenance_locks = SkillMaintenanceLockManager(micro_concurrency=micro_concurrency)
    role_feedback = _normalize_role_feedback_memory(None)
    if use_handwritten_skills:
        for artifact in default_bfcl_skill_store().all():
            store.add(copy.deepcopy(artifact))
    start_round_index = 0
    resumed_round_state: Dict[str, Any] | None = None
    keep_events = os.getenv("SKILL_MAINTENANCE_KEEP_EVENTS", "").strip().lower() in {"1", "true", "yes"}
    if checkpoint and checkpoint.exists():
        payload = json.loads(checkpoint.read_text())
        round_reports = list(payload.get("round_reports") or [])
        store = ArtifactStore(
            payload.get("store", {}).get("artifacts") or [],
            test_results=payload.get("store", {}).get("test_results") or [],
        )
        segment_index.load_rows(payload.get("segment_index_rows") or [])
        role_feedback = _normalize_role_feedback_memory(payload.get("role_feedback"))
        start_round_index = int(payload.get("next_round_index") or 0)
        resumed_round_state = _restore_current_round_state(dict(payload.get("current_round_state") or {}) or None)
        if resumed_round_state:
            resumed_store = resumed_round_state.get("store_snapshot") or {"artifacts": [], "test_results": []}
            resumed_segment_rows = list(resumed_round_state.get("segment_index_rows") or [])
            resumed_next_task_index = int(resumed_round_state.get("next_task_index") or 0)
            if resumed_next_task_index > 0:
                if not store.all() and resumed_store.get("artifacts"):
                    store = ArtifactStore(
                        resumed_store.get("artifacts") or [],
                        test_results=resumed_store.get("test_results") or [],
                    )
                if not segment_index.rows and resumed_segment_rows:
                    segment_index.load_rows(resumed_segment_rows)
                if not store.all() or not segment_index.rows:
                    raise RuntimeError(
                        "Checkpoint resume is missing evolving store or segment-index state for an in-progress round. "
                        "This checkpoint cannot continue faithfully; restart from a clean run or rebuild from full sidecars."
                    )
    debug_sink = DebugEventSink.from_env(
        base_context={
            "experiment": tag,
            "benchmark": "bfcl_v3",
            "component": "bfcl_evolve",
        },
        collect_events=keep_events,
    )
    trace_detail_level_before = os.environ.get("BFCL_TRACE_DETAIL_LEVEL")
    os.environ["BFCL_TRACE_DETAIL_LEVEL"] = os.environ.get("BFCL_TRACE_DETAIL_LEVEL", "memory_compact")
    try:
        for round_index in range(start_round_index, rounds):
            round_token_start = maintenance_token_event_count()
            resumed_round_value = resumed_round_state.get("round_index") if resumed_round_state else None
            if resumed_round_state and resumed_round_value is not None and int(resumed_round_value) == round_index:
                train_details = list(resumed_round_state.get("train_details") or [])
                online_refactor_attempts = list(resumed_round_state.get("online_refactor_attempts") or [])
                extraction_events = list(resumed_round_state.get("extraction_events") or [])
                credit_events = list(resumed_round_state.get("credit_events") or [])
                seen_refactor_cliques = {
                    tuple(str(item) for item in clique)
                    for clique in (resumed_round_state.get("seen_refactor_cliques") or [])
                }
                overlap_state = resumed_round_state.get("overlap_state")
                if not isinstance(overlap_state, OverlapGraphState):
                    overlap_state = OverlapGraphState()
                window_train_details = list(resumed_round_state.get("window_train_details") or [])
                window_segments = [
                    TraceSegment(**dict(item)) if isinstance(item, dict) else item
                    for item in (resumed_round_state.get("window_segments") or [])
                    if isinstance(item, (dict, TraceSegment))
                ]
                micro_maintenance_reports = list(resumed_round_state.get("micro_maintenance_reports") or [])
                maintenance_windows = list(resumed_round_state.get("maintenance_windows") or [])
                candidate_group_feedback_state = {
                    str(key): dict(value or {})
                    for key, value in dict(resumed_round_state.get("candidate_group_feedback_state") or {}).items()
                    if str(key)
                }
                prefetched_train_details = {
                    int(row.get("task_index")): dict(row or {})
                    for row in (resumed_round_state.get("prefetched_train_details") or [])
                    if isinstance(row, dict) and row.get("detail") and str(row.get("task_index", "")).lstrip("-").isdigit()
                }
                online_refactor_budget = _online_refactor_budget_from_env()
                role_feedback = _normalize_role_feedback_memory(resumed_round_state.get("role_feedback") or role_feedback)
                start_task_index = int(resumed_round_state.get("next_task_index") or 0)
            else:
                train_details = []
                online_refactor_attempts = []
                extraction_events = []
                credit_events = []
                seen_refactor_cliques = set()
                overlap_state = OverlapGraphState()
                window_train_details = []
                window_segments = []
                micro_maintenance_reports = []
                maintenance_windows = []
                candidate_group_feedback_state = {}
                prefetched_train_details = {}
                online_refactor_budget = _online_refactor_budget_from_env()
                start_task_index = 0
            resumed_round_state = None
            pending_micro_jobs: List[Tuple[int, List[str], Dict[str, Any]]] = []

            async def flush_pending_micro_jobs() -> None:
                nonlocal pending_micro_jobs, micro_maintenance_reports
                if not pending_micro_jobs:
                    return
                tasks = [
                    asyncio.create_task(
                        _run_locked_micro_maintenance(
                            lock_manager=maintenance_locks,
                            store=store,
                            target_names=target_names,
                            **micro_kwargs,
                        )
                    )
                    for _idx, target_names, micro_kwargs in pending_micro_jobs
                ]
                completed = await asyncio.gather(*tasks)
                for (idx, _target_names, _micro_kwargs), report in sorted(zip(pending_micro_jobs, completed), key=lambda item: item[0][0]):
                    row = dict(report or {})
                    row.setdefault("task_index", idx)
                    micro_maintenance_reports.append(row)
                pending_micro_jobs = []

            async def attach_macro_candidate_group_feedback(
                macro_report: Dict[str, Any],
                *,
                window_details: Sequence[Dict[str, Any]],
            ) -> Dict[str, Any]:
                nonlocal role_feedback
                if not candidate_competition_enabled:
                    macro_report.setdefault("candidate_group_feedback_rows", [])
                    macro_report.setdefault("candidate_group_decisions", [])
                    macro_report.setdefault("extractor_trl_update", {"enabled": bool(extractor_trl_enabled), "ran": False})
                    return macro_report
                macro_index = len(maintenance_windows)
                raw_rows = _build_candidate_group_feedback_rows(
                    extraction_events=extraction_events,
                    train_details=window_details,
                    credit_events=credit_events,
                    maintenance_test_results=macro_report.get("maintenance_test_results") or [],
                )
                feedback_rows = _select_macro_candidate_group_feedback_rows(
                    raw_rows=raw_rows,
                    state=candidate_group_feedback_state,
                    macro_index=macro_index,
                    min_usage=candidate_group_min_usage,
                    low_usage_patience=candidate_group_low_usage_patience,
                )
                decisions = _apply_candidate_group_competition_decisions(
                    store=store,
                    group_feedback_rows=feedback_rows,
                )
                macro_report["candidate_group_feedback_rows"] = copy.deepcopy(feedback_rows)
                macro_report["candidate_group_decisions"] = copy.deepcopy(decisions)
                macro_report["candidate_group_feedback_policy"] = {
                    "min_usage": candidate_group_min_usage,
                    "low_usage_macros": candidate_group_low_usage_patience,
                    "state_size": len(candidate_group_feedback_state),
                }
                if extractor_trl_enabled and feedback_rows:
                    update = await update_extractor_rules_from_feedback_llm(
                        current_rules=(role_feedback.get("extractor") or {}).get("rules"),
                        feedback_rows=feedback_rows,
                        llm_config=llm_config,
                        model_name=model_name,
                        max_rules=_ROLE_FEEDBACK_RULE_LIMIT,
                        audit_context={
                            "phase": "macro_candidate_group_feedback",
                            "experiment": tag,
                            "round_index": round_index,
                            "macro_index": macro_index,
                        },
                    )
                    macro_report["extractor_trl_update"] = {
                        "enabled": True,
                        "ran": True,
                        "summary": update.get("summary") or "",
                        "n_candidate_group_feedback_rows": len(feedback_rows),
                    }
                    role_feedback = _normalize_role_feedback_memory(
                        {
                            **role_feedback,
                            "extractor": {
                                **dict(role_feedback.get("extractor") or {}),
                                "rules": update.get("rules") or [],
                                "last_update_summary": update.get("summary") or "",
                                "updated_at": update.get("updated_at"),
                                "history": [
                                    *list((role_feedback.get("extractor") or {}).get("history") or []),
                                    {
                                        "round_index": round_index,
                                        "macro_index": macro_index,
                                        "summary": update.get("summary") or "",
                                        "rules": copy.deepcopy(update.get("rules") or []),
                                        "n_feedback_rows": len(feedback_rows),
                                        "n_candidate_group_feedback_rows": len(feedback_rows),
                                        "trl_enabled": True,
                                        "feedback_scope": "candidate_group_macro",
                                    },
                                ][-12:],
                            },
                        }
                    )
                else:
                    macro_report["extractor_trl_update"] = {
                        "enabled": bool(extractor_trl_enabled),
                        "ran": False,
                        "n_candidate_group_feedback_rows": len(feedback_rows),
                    }
                return macro_report

            async def precompute_window_records(
                *,
                window_start: int,
                window_end: int,
            ) -> Dict[int, Dict[str, Any]]:
                rollout_tasks = train_tasks[window_start:window_end]
                snapshot_artifacts = [copy.deepcopy(artifact) for artifact in store.all()]
                rollout_details = await _run_bfcl_baseline(
                    rollout_tasks,
                    1,
                    llm_config,
                    tools,
                    store,
                    adapter_mode="official",
                    model_name=model_name,
                    execution_backend=execution_backend,
                    prompt_style=prompt_style,
                    tool_api_style=tool_api_style,
                    top_k_skills=top_k_skills,
                    min_skill_score=min_skill_score,
                    skill_injection_mode=skill_injection_mode,
                    max_steps_per_turn=max_steps_per_turn,
                    partial_output=None,
                    max_task_seconds=max_task_seconds,
                    temperature=temperature,
                    synthetic_continue=synthetic_continue,
                    explicit_skill_tool=explicit_skill_tool,
                    phase=f"related_train_epoch_{round_index}_window_{window_start}_{window_end - 1}",
                    concurrency=train_window_concurrency,
                )

                async def precompute_one(offset: int, detail: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
                    absolute_task_index = window_start + offset
                    current_task = train_tasks[absolute_task_index]
                    candidate_names = _mentioned_skill_names(detail)
                    candidate_artifacts = [
                        copy.deepcopy(artifact)
                        for artifact in snapshot_artifacts
                        if artifact.name in set(candidate_names)
                    ]
                    credit_rows: List[Dict[str, Any]] = []
                    if candidate_names and candidate_artifacts:
                        credit_payload = await assign_skill_credit_llm(
                            detail=detail,
                            candidate_artifacts=candidate_artifacts,
                            llm_config=llm_config,
                            model_name=model_name,
                            audit_context={
                                "phase": "online_credit_assignment",
                                "experiment": tag,
                                "round_index": round_index,
                                "task_id": current_task.task_id,
                                "task_index": absolute_task_index,
                                "window_concurrent": True,
                            },
                        )
                        credit_rows = _credit_event_records(
                            detail=detail,
                            credit_payload=credit_payload,
                            round_index=round_index,
                            task_index=absolute_task_index,
                        )
                    results = [_result_from_dict(run).as_dict() for run in detail.get("runs", [])]
                    extracted = await _extract_candidate_skill_samples(
                        results=results,
                        tool_schemas=tools,
                        existing_artifacts=[],
                        extractor_rules=(role_feedback.get("extractor") or {}).get("rules") if extractor_trl_enabled else [],
                        llm_config=llm_config,
                        model_name=model_name,
                        audit_context={
                            "phase": "online_extract",
                            "experiment": tag,
                            "round_index": round_index,
                            "task_id": current_task.task_id,
                            "task_index": absolute_task_index,
                            "window_concurrent": True,
                        },
                        competition_enabled=candidate_competition_enabled,
                        sample_count=candidate_sample_count,
                        trial_retrieval=candidate_trial_retrieval,
                        existing_names=[artifact.name for artifact in store.all()],
                    )
                    extraction_rows = _extraction_event_records(
                        detail=detail,
                        extracted_artifacts=extracted,
                        round_index=round_index,
                        task_index=absolute_task_index,
                    )
                    segments = _extract_task_segments(detail)
                    return absolute_task_index, {
                        "task_index": absolute_task_index,
                        "detail": detail,
                        "candidate_names": candidate_names,
                        "credit_events": credit_rows,
                        "new_artifacts": [artifact.as_dict() for artifact in extracted],
                        "extraction_events": extraction_rows,
                        "new_segments": [segment.as_dict() for segment in segments],
                    }

                sem = asyncio.Semaphore(train_window_concurrency)

                async def guarded(offset: int, detail: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
                    async with sem:
                        return await precompute_one(offset, detail)

                records = await asyncio.gather(
                    *[guarded(offset, detail) for offset, detail in enumerate(rollout_details)]
                )
                return {idx: record for idx, record in records}

            for task_index in range(start_task_index, len(train_tasks)):
                task = train_tasks[task_index]
                if train_window_concurrency > 1 and task_index not in prefetched_train_details:
                    window_end = min(
                        len(train_tasks),
                        ((task_index // macro_maintenance_step) + 1) * macro_maintenance_step,
                    )
                    prefetched_train_details.update(
                        await precompute_window_records(
                            window_start=task_index,
                            window_end=window_end,
                        )
                    )
                prefetched_record: Dict[str, Any] | None = None
                if task_index in prefetched_train_details:
                    prefetched_record = dict(prefetched_train_details.pop(task_index))
                    detail = dict(prefetched_record.get("detail") or {})
                else:
                    details = await _run_bfcl_baseline(
                        [task],
                        1,
                        llm_config,
                        tools,
                        store,
                        adapter_mode="official",
                        model_name=model_name,
                        execution_backend=execution_backend,
                        prompt_style=prompt_style,
                        tool_api_style=tool_api_style,
                        top_k_skills=top_k_skills,
                        min_skill_score=min_skill_score,
                        skill_injection_mode=skill_injection_mode,
                        max_steps_per_turn=max_steps_per_turn,
                        partial_output=None,
                        max_task_seconds=max_task_seconds,
                        temperature=temperature,
                        synthetic_continue=synthetic_continue,
                        explicit_skill_tool=explicit_skill_tool,
                        phase=f"related_train_epoch_{round_index}",
                        concurrency=1,
                    )
                    detail = details[0]
                train_details.append(detail)
                window_train_details.append(detail)
                candidate_names = list(prefetched_record.get("candidate_names") or []) if prefetched_record else _mentioned_skill_names(detail)
                if prefetched_record is not None:
                    recent_credit_events = [dict(item or {}) for item in (prefetched_record.get("credit_events") or []) if isinstance(item, dict)]
                    credit_events.extend(recent_credit_events)
                    if recent_credit_events:
                        _apply_credit_case_evidence(store=store, credit_events=credit_events)
                    credit_bundle_cases = _apply_credit_bundle_case_suggestions(
                        store=store,
                        detail=detail,
                        credit_events=recent_credit_events,
                    ) if recent_credit_events else []
                elif candidate_names:
                    candidate_artifacts = [artifact for artifact in store.all() if artifact.name in set(candidate_names)]
                    if candidate_artifacts:
                        credit_payload = await assign_skill_credit_llm(
                            detail=detail,
                            candidate_artifacts=candidate_artifacts,
                            llm_config=llm_config,
                            model_name=model_name,
                            audit_context={
                                "phase": "online_credit_assignment",
                                "experiment": tag,
                                "round_index": round_index,
                                "task_id": task.task_id,
                                "task_index": task_index,
                            },
                        )
                        recent_credit_events = _credit_event_records(
                            detail=detail,
                            credit_payload=credit_payload,
                            round_index=round_index,
                            task_index=task_index,
                        )
                        credit_events.extend(recent_credit_events)
                        _apply_credit_case_evidence(store=store, credit_events=credit_events)
                        credit_bundle_cases = _apply_credit_bundle_case_suggestions(
                            store=store,
                            detail=detail,
                            credit_events=recent_credit_events,
                        )
                    else:
                        recent_credit_events = []
                        credit_bundle_cases = []
                else:
                    recent_credit_events = []
                    credit_bundle_cases = []
                await _update_skill_relation_graph_locked(
                    lock_manager=maintenance_locks,
                    store=store,
                    retrieved=candidate_names,
                    used=_used_skill_names(detail),
                )
                if prefetched_record is not None:
                    new_artifacts = ArtifactStore(prefetched_record.get("new_artifacts") or []).all()
                else:
                    results = [_result_from_dict(run).as_dict() for run in detail.get("runs", [])]
                    new_artifacts = await _extract_candidate_skill_samples(
                        results=results,
                        tool_schemas=tools,
                        existing_artifacts=[],
                        extractor_rules=(role_feedback.get("extractor") or {}).get("rules") if extractor_trl_enabled else [],
                        llm_config=llm_config,
                        model_name=model_name,
                        audit_context={
                            "phase": "online_extract",
                            "experiment": tag,
                            "round_index": round_index,
                            "task_id": task.task_id,
                            "task_index": task_index,
                        },
                        competition_enabled=candidate_competition_enabled,
                        sample_count=candidate_sample_count,
                        trial_retrieval=candidate_trial_retrieval,
                        existing_names=[artifact.name for artifact in store.all()],
                    )
                for artifact in new_artifacts:
                    if candidate_competition_enabled and artifact.metadata.get("candidate_group_id") and artifact.status == "trial":
                        store.add(artifact)
                    else:
                        store.add_pending(artifact)
                if new_artifacts:
                    update_overlap_graph_state(
                        overlap_state,
                        new_segments=[skill_to_overlap_segment(artifact) for artifact in new_artifacts],
                        segment_embeddings={},
                    )
                if new_artifacts:
                    await _update_skill_relation_graph_locked(
                        lock_manager=maintenance_locks,
                        store=store,
                        derived_from={
                            artifact.name: candidate_names
                            for artifact in new_artifacts
                        },
                    )
                if prefetched_record is not None:
                    extraction_events.extend(
                        [dict(item or {}) for item in (prefetched_record.get("extraction_events") or []) if isinstance(item, dict)]
                    )
                    new_segments = [
                        TraceSegment(**dict(item))
                        for item in (prefetched_record.get("new_segments") or [])
                        if isinstance(item, dict)
                    ]
                else:
                    extraction_events.extend(
                        _extraction_event_records(
                            detail=detail,
                            extracted_artifacts=new_artifacts,
                            round_index=round_index,
                            task_index=task_index,
                        )
                    )
                    new_segments = _extract_task_segments(detail)
                window_segments.extend(new_segments)
                segment_index.add_segments(new_segments, round_index=round_index, task_id=task.task_id)
                new_segment_embeddings = segment_index.embedding_map(
                    segment_ids=[segment.segment_id for segment in new_segments],
                    round_index=round_index,
                )
                update_overlap_graph_state(
                    overlap_state,
                    new_segments=new_segments,
                    segment_embeddings=new_segment_embeddings,
                )
                current_credit_context = _credit_context_by_skill(credit_events)
                micro_targets = _unique_ordered([*candidate_names, *_strong_credit_targets(recent_credit_events)])
                micro_write_targets = _micro_write_target_names(
                    task_credit_events=recent_credit_events,
                    credit_bundle_cases=credit_bundle_cases,
                    relevant_skill_names=micro_targets,
                )
                micro_report: Dict[str, Any] = {
                    "phase": "micro",
                    "task_id": task.task_id,
                    "task_index": task_index,
                    "maintenance_targets": micro_write_targets,
                    "relevant_skill_names": micro_targets,
                    "maintenance_test_results": [],
                    "refine_decisions": [],
                    "overlap_refactor": {"attempts": [], "refactor_segment_coverage": []},
                    "refactor_segment_coverage": [],
                    "static_dependency_validation": [],
                    "credit_bundle_cases": copy.deepcopy(credit_bundle_cases),
                    "credit_negative_bundle_cases": [
                        copy.deepcopy(row) for row in credit_bundle_cases if row.get("polarity") == "negative"
                    ],
                }
                if micro_write_targets and ((task_index + 1) % micro_maintenance_step == 0):
                    micro_kwargs = {
                        "detail": detail,
                        "task_credit_events": recent_credit_events,
                        "credit_bundle_cases": credit_bundle_cases,
                        "relevant_skill_names": micro_targets,
                        "tools": tools,
                        "llm_config": llm_config,
                        "model_name": model_name,
                        "execution_backend": execution_backend,
                        "prompt_style": prompt_style,
                        "tool_api_style": tool_api_style,
                        "max_steps_per_turn": max_steps_per_turn,
                        "max_task_seconds": max_task_seconds,
                        "temperature": temperature,
                        "synthetic_continue": synthetic_continue,
                        "explicit_skill_tool": explicit_skill_tool,
                        "round_index": round_index,
                        "task_index": task_index,
                        "tag": tag,
                        "credit_context_by_skill": current_credit_context,
                    }
                    if train_window_concurrency > 1 and micro_concurrency > 1:
                        pending_micro_jobs.append((task_index, list(micro_write_targets), micro_kwargs))
                    else:
                        micro_report = await _run_locked_micro_maintenance(
                            lock_manager=maintenance_locks,
                            store=store,
                            target_names=micro_write_targets,
                            **micro_kwargs,
                        )
                        micro_report.update({"phase": "micro", "task_id": task.task_id, "task_index": task_index})
                        micro_report["credit_bundle_cases"] = copy.deepcopy(credit_bundle_cases)
                        micro_report["credit_negative_bundle_cases"] = [
                            copy.deepcopy(row) for row in credit_bundle_cases if row.get("polarity") == "negative"
                        ]
                        micro_maintenance_reports.append(micro_report)
                else:
                    micro_maintenance_reports.append(micro_report)
                if (task_index + 1) % macro_maintenance_step == 0:
                    await flush_pending_micro_jobs()
                    macro_report = await _run_macro_maintenance(
                        store=store,
                        train_details=window_train_details,
                        segment_index=segment_index,
                        overlap_state=overlap_state,
                        window_segments=window_segments,
                        exclude_segment_sets=seen_refactor_cliques,
                        tools=tools,
                        llm_config=llm_config,
                        model_name=model_name,
                        execution_backend=execution_backend,
                        prompt_style=prompt_style,
                        tool_api_style=tool_api_style,
                        max_steps_per_turn=max_steps_per_turn,
                        max_task_seconds=max_task_seconds,
                        temperature=temperature,
                        synthetic_continue=synthetic_continue,
                        explicit_skill_tool=explicit_skill_tool,
                        round_index=round_index,
                        tag=tag,
                        phase="macro",
                        credit_events=credit_events,
                        extractor_trl_enabled=extractor_trl_enabled,
                    )
                    macro_report.update(
                        {
                            "phase": "macro",
                            "window_index": len(maintenance_windows),
                            "start_task_index": task_index + 1 - len(window_train_details),
                            "end_task_index": task_index,
                            "task_ids": [str(item.get("task_id") or "") for item in window_train_details],
                            "new_segment_ids": [segment.segment_id for segment in window_segments],
                        }
                    )
                    macro_report = await attach_macro_candidate_group_feedback(
                        macro_report,
                        window_details=window_train_details,
                    )
                    round_pending_promotions = _promote_pending_from_refactor_report(
                        store=store,
                        refactor_report=macro_report.get("overlap_refactor") or {},
                    )
                    macro_report["pending_skill_promotions"] = copy.deepcopy(round_pending_promotions)
                    for attempt in ((macro_report.get("overlap_refactor") or {}).get("attempts") or []):
                        clique = dict(attempt.get("clique") or {})
                        segment_ids = tuple(sorted(str(item) for item in (clique.get("segment_ids") or []) if str(item)))
                        if segment_ids:
                            seen_refactor_cliques.add(segment_ids)
                    maintenance_windows.append(macro_report)
                    window_train_details = []
                    window_segments = []
                if pending_micro_jobs:
                    continue
                current_round_state = {
                    "round_index": round_index,
                    "next_task_index": task_index + 1,
                    "train_details": train_details,
                    "window_train_details": window_train_details,
                    "window_segments": [segment.as_dict() for segment in window_segments],
                    "micro_maintenance_reports": micro_maintenance_reports,
                    "maintenance_windows": maintenance_windows,
                    "online_refactor_attempts": online_refactor_attempts,
                    "extraction_events": extraction_events,
                    "credit_events": credit_events,
                    "candidate_group_feedback_state": copy.deepcopy(candidate_group_feedback_state),
                    "prefetched_train_details": [
                        dict(record)
                        for _idx, record in sorted(prefetched_train_details.items())
                    ],
                    "role_feedback": role_feedback,
                    "seen_refactor_cliques": [list(item) for item in sorted(seen_refactor_cliques)],
                    "online_refactor_budget_remaining": online_refactor_budget,
                    "overlap_state": overlap_state,
                }
                _write_current_round_sidecars(
                    checkpoint_path=checkpoint,
                    current_round_state=current_round_state,
                    store=store,
                    segment_index=segment_index,
                )
                _write_json(
                    checkpoint,
                    _checkpoint_payload(
                        tag=tag,
                        rounds=rounds,
                        round_reports=round_reports,
                        store=store,
                        segment_index=segment_index,
                        next_round_index=round_index,
                        current_round_state=current_round_state,
                        role_feedback=role_feedback,
                        output_detail_level=output_detail_level,
                        checkpoint_path=checkpoint,
                    ),
                )
            await flush_pending_micro_jobs()
            if window_train_details or window_segments:
                final_macro = await _run_macro_maintenance(
                    store=store,
                    train_details=window_train_details,
                    segment_index=segment_index,
                    overlap_state=overlap_state,
                    window_segments=window_segments,
                    exclude_segment_sets=seen_refactor_cliques,
                    tools=tools,
                    llm_config=llm_config,
                    model_name=model_name,
                    execution_backend=execution_backend,
                    prompt_style=prompt_style,
                    tool_api_style=tool_api_style,
                    max_steps_per_turn=max_steps_per_turn,
                    max_task_seconds=max_task_seconds,
                    temperature=temperature,
                    synthetic_continue=synthetic_continue,
                    explicit_skill_tool=explicit_skill_tool,
                    round_index=round_index,
                    tag=tag,
                    phase="macro_final",
                    credit_events=credit_events,
                    extractor_trl_enabled=extractor_trl_enabled,
                )
                final_macro.update(
                    {
                        "phase": "macro_final",
                        "window_index": len(maintenance_windows),
                        "start_task_index": len(train_details) - len(window_train_details),
                        "end_task_index": len(train_details) - 1,
                        "task_ids": [str(item.get("task_id") or "") for item in window_train_details],
                        "new_segment_ids": [segment.segment_id for segment in window_segments],
                    }
                )
                final_macro = await attach_macro_candidate_group_feedback(
                    final_macro,
                    window_details=window_train_details,
                )
                final_promotions = _promote_pending_from_refactor_report(
                    store=store,
                    refactor_report=final_macro.get("overlap_refactor") or {},
                )
                final_macro["pending_skill_promotions"] = copy.deepcopy(final_promotions)
                for attempt in ((final_macro.get("overlap_refactor") or {}).get("attempts") or []):
                    clique = dict(attempt.get("clique") or {})
                    segment_ids = tuple(sorted(str(item) for item in (clique.get("segment_ids") or []) if str(item)))
                    if segment_ids:
                        seen_refactor_cliques.add(segment_ids)
                maintenance_windows.append(final_macro)
                window_train_details = []
                window_segments = []
            round_maintenance = _combine_maintenance_reports([*micro_maintenance_reports, *maintenance_windows])
            round_pending_promotions = [
                item
                for window in maintenance_windows
                for item in (window.get("pending_skill_promotions") or [])
            ]
            round_pending_promotions = _dedupe_promotion_rows(round_pending_promotions)
            pending_revoke_enabled = os.environ.get("BFCL_REVOKE_UNPROMOTED_PENDING", "1").strip().lower() in {"1", "true", "yes"}
            pending_revoked = store.revoke_unpromoted_pending(
                reason="round_end_not_promoted_by_posterior_overlap"
            ) if pending_revoke_enabled else []
            extractor_feedback_rows = _build_extractor_feedback_rows(
                extraction_events=extraction_events,
                train_details=train_details,
                maintenance_test_results=round_maintenance.get("maintenance_test_results") or [],
            )
            candidate_group_feedback_rows = [
                row
                for window in maintenance_windows
                for row in (window.get("candidate_group_feedback_rows") or [])
            ]
            candidate_group_decisions = [
                row
                for window in maintenance_windows
                for row in (window.get("candidate_group_decisions") or [])
            ]
            credit_summary = _aggregate_skill_credit(credit_events, store=store)
            credit_filter_threshold = max(1, int(os.environ.get("BFCL_CREDIT_FILTER_THRESHOLD", "2") or "2"))
            credit_filter_decisions = _apply_skill_credit_filter(
                store=store,
                credit_summary=credit_summary,
                threshold=credit_filter_threshold,
            )
            if extractor_trl_enabled:
                extractor_feedback_update = await update_extractor_rules_from_feedback_llm(
                    current_rules=(role_feedback.get("extractor") or {}).get("rules"),
                    feedback_rows=extractor_feedback_rows,
                    llm_config=llm_config,
                    model_name=model_name,
                    max_rules=_ROLE_FEEDBACK_RULE_LIMIT,
                    audit_context={
                        "phase": "round_extractor_feedback",
                        "experiment": tag,
                        "round_index": round_index,
                    },
                )
                role_feedback = _normalize_role_feedback_memory(
                    {
                        **role_feedback,
                        "extractor": {
                            **dict(role_feedback.get("extractor") or {}),
                            "rules": extractor_feedback_update.get("rules") or [],
                            "last_update_summary": extractor_feedback_update.get("summary") or "",
                            "updated_at": extractor_feedback_update.get("updated_at"),
                            "history": [
                                *list((role_feedback.get("extractor") or {}).get("history") or []),
                                {
                                    "round_index": round_index,
                                    "summary": extractor_feedback_update.get("summary") or "",
                                    "rules": copy.deepcopy(extractor_feedback_update.get("rules") or []),
                                    "n_feedback_rows": len(extractor_feedback_rows),
                                    "n_skill_feedback_rows": len(extractor_feedback_rows),
                                    "trl_enabled": True,
                                },
                            ][-12:],
                        },
                    }
                )
            else:
                role_feedback = _normalize_role_feedback_memory(
                    {
                        **role_feedback,
                        "extractor": {
                            **dict(role_feedback.get("extractor") or {}),
                            "last_update_summary": "extractor_trl_disabled:no_rule_update",
                            "history": [
                                *list((role_feedback.get("extractor") or {}).get("history") or []),
                                {
                                    "round_index": round_index,
                                    "summary": "extractor_trl_disabled:no_rule_update",
                                    "rules": copy.deepcopy((role_feedback.get("extractor") or {}).get("rules") or []),
                                    "n_feedback_rows": len(extractor_feedback_rows),
                                    "n_skill_feedback_rows": len(extractor_feedback_rows),
                                    "trl_enabled": False,
                                },
                            ][-12:],
                        },
                    }
                )
            for attempt in ((round_maintenance.get("overlap_refactor") or {}).get("attempts") or []):
                clique = dict(attempt.get("clique") or {})
                segment_ids = tuple(sorted(str(item) for item in (clique.get("segment_ids") or []) if str(item)))
                if segment_ids:
                    seen_refactor_cliques.add(segment_ids)
            round_train_summary = _aggregate("bfcl_v3", "related_train_round", tag, llm_config, len(train_tasks), train_details)
            round_report = {
                "round_index": round_index,
                "train_summary": {k: v for k, v in round_train_summary.items() if k != "details"},
                "train_details": [
                    _compact_task_detail(detail) for detail in train_details
                ] if output_detail_level == "compact" else train_details,
                "store_snapshot": {
                    "n_skills": len(store.all()),
                    "skill_names": [skill.name for skill in store.all()],
                    "skill_versions": _skill_versions_table(store.all()),
                },
                "segment_index_stats": segment_index.stats(),
                "token_breakdown": _token_breakdown_delta(round_token_start),
                "role_feedback": _role_feedback_projection(role_feedback),
                "extractor_feedback_rows": copy.deepcopy(extractor_feedback_rows),
                "candidate_group_feedback_rows": copy.deepcopy(candidate_group_feedback_rows),
                "candidate_group_decisions": copy.deepcopy(candidate_group_decisions),
                "extraction_events": copy.deepcopy(extraction_events),
                "credit_events": copy.deepcopy(credit_events),
                "skill_credit_summary": copy.deepcopy(credit_summary),
                "skill_credit_filter_decisions": copy.deepcopy(credit_filter_decisions),
                "pending_skill_summary": _pending_skill_summary(store),
                "pending_skill_promotions": copy.deepcopy(round_pending_promotions),
                "pending_skill_revocations": list(pending_revoked),
                "micro_maintenance_reports": copy.deepcopy(micro_maintenance_reports),
                "maintenance_windows": copy.deepcopy(maintenance_windows),
                "macro_maintenance_step": macro_maintenance_step,
                "micro_maintenance_step": micro_maintenance_step,
                "train_window_concurrency": train_window_concurrency,
                "micro_concurrency": micro_concurrency,
                "candidate_competition": {
                    "enabled": bool(candidate_competition_enabled),
                    "sample_count": int(candidate_sample_count),
                    "trial_retrieval": bool(candidate_trial_retrieval),
                    "group_min_usage": int(candidate_group_min_usage),
                    "group_low_usage_macros": int(candidate_group_low_usage_patience),
                },
                "refactor_segment_coverage": copy.deepcopy(round_maintenance.get("refactor_segment_coverage") or []),
                "online_refactor_attempts": _project_online_refactor_attempts(online_refactor_attempts)
                if output_detail_level == "compact" else online_refactor_attempts,
                "overlap_timeline": _compact_overlap_timeline(
                    _maybe_build_overlap_timeline(
                        train_details,
                        store=store,
                        output_detail_level=output_detail_level,
                    )
                ) if output_detail_level == "compact" else _maybe_build_overlap_timeline(
                    train_details,
                    store=store,
                    output_detail_level=output_detail_level,
                ),
                **(_project_round_maintenance(round_maintenance) if output_detail_level == "compact" else round_maintenance),
            }
            round_reports.append(round_report)
            _write_current_round_sidecars(
                checkpoint_path=checkpoint,
                current_round_state=None,
                store=store,
                segment_index=segment_index,
            )
            _write_json(
                checkpoint,
                _checkpoint_payload(
                    tag=tag,
                    rounds=rounds,
                    round_reports=round_reports,
                    store=store,
                    segment_index=segment_index,
                    next_round_index=round_index + 1,
                    current_round_state=None,
                    role_feedback=role_feedback,
                    output_detail_level=output_detail_level,
                    checkpoint_path=checkpoint,
                ),
            )
            if save_skills:
                store.save(save_skills)
        test_details = await _run_bfcl_baseline(
            test_tasks,
            1,
            llm_config,
            tools,
            store,
            adapter_mode="official",
            model_name=model_name,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            top_k_skills=top_k_skills,
            min_skill_score=min_skill_score,
            skill_injection_mode=skill_injection_mode,
            max_steps_per_turn=max_steps_per_turn,
            partial_output=None,
            max_task_seconds=max_task_seconds,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
            phase="related_heldout_test",
            concurrency=test_concurrency,
        )
    finally:
        if trace_detail_level_before is None:
            os.environ.pop("BFCL_TRACE_DETAIL_LEVEL", None)
        else:
            os.environ["BFCL_TRACE_DETAIL_LEVEL"] = trace_detail_level_before
    test_summary = _aggregate("bfcl_v3", "related_test", tag, llm_config, len(train_tasks), test_details)
    experiment_token_breakdown = snapshot_maintenance_token_stats(start_index=0)
    if save_skills:
        store.save(save_skills)
    return {
        "benchmark": "bfcl_v3",
        "mode": "related_task_evolve",
        "tag": tag,
        "experiment_variant": experiment_variant,
        "llm_config": llm_config,
        "model_name": model_name,
        "config_summary": {
            "experiment_variant": experiment_variant,
            "extractor_trl_enabled": extractor_trl_enabled,
            "baseline_reused": False,
            "manifest_path": str(PROJECT_ROOT / "academic" / "experiments" / "bfcl_case_lists" / "curated_related_manifest_50_50.json"),
            "rounds": rounds,
            "epochs": effective_epochs,
            "micro_maintenance_step": micro_maintenance_step,
            "macro_maintenance_step": macro_maintenance_step,
            "test_concurrency": test_concurrency,
            "train_window_concurrency": train_window_concurrency,
            "micro_concurrency": micro_concurrency,
            "candidate_competition_enabled": bool(candidate_competition_enabled),
            "candidate_sample_count": int(candidate_sample_count),
            "candidate_trial_retrieval": bool(candidate_trial_retrieval),
            "candidate_group_min_usage": int(candidate_group_min_usage),
            "candidate_group_low_usage_macros": int(candidate_group_low_usage_patience),
            "top_k_skills": top_k_skills,
            "min_skill_score": min_skill_score,
            "skill_injection_mode": skill_injection_mode,
            "max_steps_per_turn": max_steps_per_turn,
            "max_task_seconds": max_task_seconds,
        },
        "manifest": manifest,
        "rounds": round_reports,
        "epochs": effective_epochs,
        "micro_maintenance_step": micro_maintenance_step,
        "macro_maintenance_step": macro_maintenance_step,
        "train_window_concurrency": train_window_concurrency,
        "micro_concurrency": micro_concurrency,
        "candidate_competition": {
            "enabled": bool(candidate_competition_enabled),
            "sample_count": int(candidate_sample_count),
            "trial_retrieval": bool(candidate_trial_retrieval),
            "group_min_usage": int(candidate_group_min_usage),
            "group_low_usage_macros": int(candidate_group_low_usage_patience),
        },
        "maintenance_windows": [
            window
            for report in round_reports
            for window in (report.get("maintenance_windows") or [])
        ],
        "micro_maintenance_reports": [
            row
            for report in round_reports
            for row in (report.get("micro_maintenance_reports") or [])
        ],
        "train_task_ids": list(manifest.get("train_task_ids") or []),
        "test_task_ids": list(manifest.get("test_task_ids") or []),
        "n_rounds": rounds,
        "train_split_size": len(train_tasks),
        "test_split_size": len(test_tasks),
        "segment_index_stats": segment_index.stats(),
        "segment_index": segment_index.as_projection(),
        "token_breakdown": experiment_token_breakdown,
        "role_feedback": _role_feedback_projection(role_feedback),
        "pending_skill_summary": _pending_skill_summary(store),
        "candidate_group_feedback_rows": [
            row
            for report in round_reports
            for row in (report.get("candidate_group_feedback_rows") or [])
        ],
        "candidate_group_decisions": [
            row
            for report in round_reports
            for row in (report.get("candidate_group_decisions") or [])
        ],
        "skill_credit_events": [
            item
            for report in round_reports
            for item in (report.get("credit_events") or [])
        ],
        "skill_credit_summary": _aggregate_skill_credit(
            [
                item
                for report in round_reports
                for item in (report.get("credit_events") or [])
            ],
            store=store,
        ),
        "skill_credit_filter_decisions": [
            item
            for report in round_reports
            for item in (report.get("skill_credit_filter_decisions") or [])
        ],
        "refactor_groups": [
            row
            for report in round_reports
            for row in _refactor_group_rows(report.get("overlap_refactor") or {})
        ] + [
            row
            for report in round_reports
            for online_attempt in (report.get("online_refactor_attempts") or [])
            for row in _refactor_group_rows((online_attempt.get("report") or {}))
        ],
        "refactor_segment_coverage": [
            row
            for report in round_reports
            for row in (report.get("refactor_segment_coverage") or [])
        ],
        "skill_versions": _skill_versions_table(store.all()),
        "skills": [skill.as_dict() for skill in store.all()],
        "test_help_links": _build_test_help_links(
            test_details=[
                _compact_task_detail(detail) for detail in test_details
            ] if output_detail_level == "compact" else test_details,
            train_task_ids=manifest.get("train_task_ids") or [],
            skills=store.all(),
        ),
        "train_summary": copy.deepcopy(round_reports[-1]["train_summary"]) if round_reports else {},
        "test_summary": {k: v for k, v in test_summary.items() if k != "details"},
        "test_details": [
            _compact_task_detail(detail) for detail in test_details
        ] if output_detail_level == "compact" else test_details,
    }


async def _run_related_baseline(
    *,
    manifest: Dict[str, Any],
    cache_dir: Path,
    llm_config: str,
    model_name: str | None,
    tools: List[Dict[str, Any]],
    data_source: str,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    tag: str,
    test_concurrency: int = 1,
) -> Dict[str, Any]:
    _, test_tasks = _tasks_from_manifest(manifest, cache_dir=cache_dir, data_source=data_source)
    test_concurrency = max(1, int(test_concurrency or 1))
    details = await _run_bfcl_baseline(
        test_tasks,
        1,
        llm_config,
        tools,
        ArtifactStore(),
        adapter_mode="official",
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        top_k_skills=0,
        skill_injection_mode="none",
        max_steps_per_turn=max_steps_per_turn,
        partial_output=None,
        max_task_seconds=max_task_seconds,
        temperature=temperature,
        synthetic_continue=synthetic_continue,
        explicit_skill_tool=explicit_skill_tool,
        phase="related_baseline_test",
        concurrency=test_concurrency,
    )
    summary = _aggregate("bfcl_v3", "related_task_baseline", tag, llm_config, len(manifest.get("train_task_ids") or []), details)
    return {
        "benchmark": "bfcl_v3",
        "mode": "related_task_baseline",
        "tag": tag,
        "experiment_variant": "baseline",
        "llm_config": llm_config,
        "model_name": model_name,
        "config_summary": {
            "experiment_variant": "baseline",
            "extractor_trl_enabled": False,
            "baseline_reused": True,
            "manifest_path": str(PROJECT_ROOT / "academic" / "experiments" / "bfcl_case_lists" / "curated_related_manifest_50_50.json"),
            "rounds": 0,
            "top_k_skills": 0,
            "skill_injection_mode": "none",
            "max_steps_per_turn": max_steps_per_turn,
            "max_task_seconds": max_task_seconds,
            "test_concurrency": test_concurrency,
        },
        "manifest": manifest,
        "n_test": len(details),
        "train_split_size": len(manifest.get("train_task_ids") or []),
        "test_split_size": len(manifest.get("test_task_ids") or []),
        "test_summary": {k: v for k, v in summary.items() if k != "details"},
        "test_details": details,
    }


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Run the BFCL related-task overlap-refactor experiment")
    parser.add_argument("--mode", choices=["build-manifest", "validate-manifest", "validate-config", "baseline", "evolve", "analyze"], required=True)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "academic" / "experiments" / "bfcl_case_lists" / "curated_related_manifest_50_50.json")
    parser.add_argument("--expected-train-size", type=int, default=50)
    parser.add_argument("--expected-test-size", type=int, default=50)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--baseline-result", type=Path, default=None)
    parser.add_argument("--evolve-result", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "data" / "benchmarks" / "bfcl_v3")
    parser.add_argument("--llm-config", default="local_claude_proxy")
    parser.add_argument("--model-name", default="claude-sonnet-4-5")
    parser.add_argument("--data-source", choices=["bfcl_eval_bundle", "hf_v3"], default="bfcl_eval_bundle")
    parser.add_argument("--rounds", type=int, default=1, help="Legacy alias for epochs; mainline defaults to one epoch.")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--micro-maintenance-step", type=int, default=1)
    parser.add_argument("--macro-maintenance-step", type=int, default=10)
    parser.add_argument("--test-concurrency", type=int, default=int(os.environ.get("BFCL_RELATED_TEST_CONCURRENCY", "1") or "1"))
    parser.add_argument("--train-window-concurrency", type=int, default=int(os.environ.get("BFCL_RELATED_TRAIN_WINDOW_CONCURRENCY", "1") or "1"))
    parser.add_argument("--enable-candidate-competition", action="store_true", default=_env_bool("BFCL_CANDIDATE_COMPETITION_ENABLED", False))
    parser.add_argument("--candidate-sample-count", type=int, default=int(os.environ.get("BFCL_CANDIDATE_SAMPLE_COUNT", "1") or "1"))
    parser.add_argument("--candidate-group-min-usage", type=int, default=int(os.environ.get("BFCL_CANDIDATE_GROUP_MIN_USAGE", "1") or "1"))
    parser.add_argument(
        "--candidate-group-low-usage-macros",
        type=int,
        default=int(os.environ.get("BFCL_CANDIDATE_GROUP_LOW_USAGE_MACROS", os.environ.get("BFCL_CANDIDATE_GROUP_NO_USAGE_MACROS", "3")) or "3"),
    )
    parser.add_argument("--candidate-group-no-usage-macros", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--disable-candidate-trial-retrieval",
        action="store_true",
        help="Keep sampled candidate groups pending instead of exposing trial candidates to retrieval.",
    )
    parser.add_argument("--execution-backend", choices=["official", "local_mock", "auto"], default="official")
    parser.add_argument("--prompt-style", choices=["native", "official", "academic"], default="native")
    parser.add_argument(
        "--tool-api-style",
        choices=["auto", "openai", "openai_direct", "openai_stream", "anthropic_direct"],
        default="auto",
    )
    parser.add_argument("--top-k-skills", type=int, default=2)
    parser.add_argument("--min-skill-score", type=float, default=0.0)
    parser.add_argument("--skill-injection-mode", choices=["none", "prompt_only", "tool_only", "hybrid"], default="prompt_only")
    parser.add_argument("--max-steps-per-turn", type=int, default=20)
    parser.add_argument("--max-task-seconds", type=float, default=180.0)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--synthetic-continue", action="store_true")
    parser.add_argument("--explicit-skill-tool", action="store_true")
    parser.add_argument("--tag", default="claude45proxy_official_related50_50")
    parser.add_argument("--use-handwritten-skills", action="store_true")
    parser.add_argument("--save-skills", type=Path, default=None)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--output-detail-level", choices=["compact", "full"], default="compact")
    parser.add_argument("--disable-extractor-trl", action="store_true")
    parser.add_argument("--experiment-variant", default="w_extractor_reusage_trl")
    args = parser.parse_args()
    os.environ["BFCL_CANDIDATE_GROUP_MIN_USAGE"] = str(max(1, int(args.candidate_group_min_usage or 1)))
    low_usage_macros = args.candidate_group_no_usage_macros if args.candidate_group_no_usage_macros is not None else args.candidate_group_low_usage_macros
    os.environ["BFCL_CANDIDATE_GROUP_LOW_USAGE_MACROS"] = str(max(1, int(low_usage_macros or 1)))
    resolved_output = args.output or _default_output_path(args.mode, args.tag)
    resolved_checkpoint = args.checkpoint or _phase_partial_path(resolved_output, "checkpoint")

    if args.mode == "build-manifest":
        manifest = build_curated_related_task_manifest(
            cache_dir=args.cache_dir,
            split_seed=42,
            data_source=args.data_source,
            n_train=args.expected_train_size,
            n_test=args.expected_test_size,
        )
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps({"manifest": str(args.manifest), "validation": validate_curated_manifest(manifest)}, ensure_ascii=False, indent=2))
        return

    manifest = load_or_build_curated_manifest(
        manifest_path=args.manifest,
        cache_dir=args.cache_dir,
        split_seed=42,
        data_source=args.data_source,
        expected_train=None if args.mode == "analyze" else args.expected_train_size,
        expected_test=None if args.mode == "analyze" else args.expected_test_size,
        require_task_rows=args.mode != "analyze",
    )
    if args.mode == "validate-manifest":
        print(
            json.dumps(
                {
                    "manifest": str(args.manifest),
                    "validation": validate_curated_manifest(
                        manifest,
                        expected_train=args.expected_train_size,
                        expected_test=args.expected_test_size,
                        require_task_rows=True,
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.mode == "validate-config":
        strict_embeddings = os.environ.get("BFCL_STRICT_SEGMENT_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes"}
        payload = validate_experiment_config(
            manifest=manifest,
            output_path=resolved_output,
            save_skills=args.save_skills,
            checkpoint_path=resolved_checkpoint,
            strict_embeddings=strict_embeddings,
            expected_train=None if args.mode == "analyze" else args.expected_train_size,
            expected_test=None if args.mode == "analyze" else args.expected_test_size,
            require_task_rows=args.mode != "analyze",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.mode == "analyze":
        if not args.baseline_result or not args.evolve_result:
            raise ValueError("--baseline-result and --evolve-result are required for analyze mode")
        baseline = json.loads(args.baseline_result.read_text())
        evolve = json.loads(args.evolve_result.read_text())
        analysis = build_analysis_artifacts(manifest=manifest, baseline_summary=baseline, evolve_summary=evolve)
        out = resolved_output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2))
        print(json.dumps({"output": str(out)}, ensure_ascii=False, indent=2))
        return

    tools = load_bfcl_tools(args.cache_dir, data_source=args.data_source)
    if args.mode == "baseline":
        payload = await _run_related_baseline(
            manifest=manifest,
            cache_dir=args.cache_dir,
            llm_config=args.llm_config,
            model_name=args.model_name,
            tools=tools,
            data_source=args.data_source,
            execution_backend=args.execution_backend,
            prompt_style=args.prompt_style,
            tool_api_style=args.tool_api_style,
            max_steps_per_turn=args.max_steps_per_turn,
            max_task_seconds=args.max_task_seconds,
            temperature=args.temperature,
            synthetic_continue=args.synthetic_continue,
            explicit_skill_tool=args.explicit_skill_tool,
            tag=args.tag,
            test_concurrency=args.test_concurrency,
        )
        out = resolved_output
    else:
        payload = await _run_related_evolve_experiment(
            manifest=manifest,
            cache_dir=args.cache_dir,
            llm_config=args.llm_config,
            model_name=args.model_name,
            tools=tools,
            rounds=args.rounds,
            data_source=args.data_source,
            execution_backend=args.execution_backend,
            prompt_style=args.prompt_style,
            tool_api_style=args.tool_api_style,
            top_k_skills=args.top_k_skills,
            min_skill_score=args.min_skill_score,
            skill_injection_mode=args.skill_injection_mode,
            max_steps_per_turn=args.max_steps_per_turn,
            max_task_seconds=args.max_task_seconds,
            temperature=args.temperature,
            synthetic_continue=args.synthetic_continue,
            explicit_skill_tool=args.explicit_skill_tool,
            tag=args.tag,
            save_skills=args.save_skills,
            use_handwritten_skills=args.use_handwritten_skills,
            checkpoint_path=resolved_checkpoint,
            output_path=resolved_output,
            output_detail_level=args.output_detail_level,
            extractor_trl_enabled=not args.disable_extractor_trl,
            experiment_variant=args.experiment_variant,
            epochs=args.epochs,
            micro_maintenance_step=args.micro_maintenance_step,
            macro_maintenance_step=args.macro_maintenance_step,
            test_concurrency=args.test_concurrency,
            train_window_concurrency=args.train_window_concurrency,
            candidate_competition_enabled=args.enable_candidate_competition,
            candidate_sample_count=args.candidate_sample_count,
            candidate_trial_retrieval=not args.disable_candidate_trial_retrieval,
        )
        out = resolved_output

    if isinstance(payload.get("config_summary"), dict):
        payload["config_summary"]["manifest_path"] = str(args.manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({"output": str(out), "mode": args.mode}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
