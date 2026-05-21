"""BFCL adapter for the benchmark-agnostic LLM skill-maintenance loop."""
from __future__ import annotations

import asyncio
import ast
import copy
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence, Tuple
from uuid import uuid4

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.bundle_policy import trim_bundle_cases_to_budget
from academic.benchmarks.bfcl import (
    filter_bfcl_tools_by_class,
    load_bfcl_tools,
    run_bfcl_task,
)
from academic.benchmarks.core.types import (
    BenchmarkTask,
    SkillArtifact,
    SkillBundleCase,
    SkillLineage,
    SkillTestCaseRun,
    SkillTestResult,
)
from academic.skill_repository.llm_maintenance import (
    apply_refine_payload,
    apply_refine_payload_via_editor,
    apply_stale_payload,
    apply_stale_payload_via_editor,
    apply_bundle_patch_payload,
    apply_bundle_patch_payload_via_editor,
    apply_bundle_text_via_editor,
    distill_skill_bundle_llm,
    extract_skill_artifacts_from_results_llm,
    maintain_skill_bundle_llm,
    refine_skill_artifact_llm,
    resolve_stale_skill_llm,
    summarize_dependency_context,
)
from academic.skill_repository.debug_events import DebugEventSink
from academic.skill_repository.refactor_overlap import (
    OverlapGraphState,
    TraceSegment,
    apply_affected_skill_updates,
    artifact_from_refactor_payload,
    build_overlap_graph_state,
    discover_overlap_graph,
    find_refactor_cliques,
    llm_refactor_clique,
    materialize_overlap_graph,
    skill_to_overlap_segment,
    update_overlap_graph_state,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or str(default))
    except Exception:
        return default


def _unique_names(names: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for name in names:
        norm = str(name or "").strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _relation_bucket(artifact: SkillArtifact) -> Dict[str, List[str]]:
    relation = dict(artifact.metadata.get("skill_relation_graph") or {})
    for key in (
        "calls",
        "called_by",
        "co_retrieved_with",
        "co_used_with",
        "derived_from",
        "refines",
        "conflicts_with",
    ):
        relation[key] = _unique_names(relation.get(key) or [])
    artifact.metadata["skill_relation_graph"] = relation
    return relation


def update_skill_relation_graph(
    store: ArtifactStore,
    *,
    retrieved: Sequence[str] | None = None,
    used: Sequence[str] | None = None,
    calls: Dict[str, Sequence[str]] | None = None,
    derived_from: Dict[str, Sequence[str]] | None = None,
    refines: Dict[str, Sequence[str]] | None = None,
    conflicts_with: Dict[str, Sequence[str]] | None = None,
) -> None:
    """Maintain compact skill-skill relation metadata.

    This deliberately stores only name-level graph edges. It is cheap enough to
    update after each trace, extract, refine, or refactor event and gives the
    refiner a dependency neighborhood without scanning the entire repository.
    """

    by_name = {artifact.name: artifact for artifact in store.all()}
    retrieved_names = [name for name in _unique_names(retrieved or []) if name in by_name]
    used_names = [name for name in _unique_names(used or []) if name in by_name]
    for names, field in ((retrieved_names, "co_retrieved_with"), (used_names, "co_used_with")):
        for name in names:
            relation = _relation_bucket(by_name[name])
            relation[field] = _unique_names([*relation[field], *[other for other in names if other != name]])
    for caller_name, callee_values in (calls or {}).items():
        caller = by_name.get(str(caller_name or "").strip())
        if caller is None:
            continue
        callee_names = [name for name in _unique_names(callee_values or []) if name in by_name and name != caller.name]
        caller_relation = _relation_bucket(caller)
        caller_relation["calls"] = _unique_names([*caller_relation["calls"], *callee_names])
        caller.dependencies = _unique_names([*list(caller.dependencies or []), *callee_names])
        for callee_name in callee_names:
            callee_relation = _relation_bucket(by_name[callee_name])
            callee_relation["called_by"] = _unique_names([*callee_relation["called_by"], caller.name])
    for mapping, field in (
        (derived_from or {}, "derived_from"),
        (refines or {}, "refines"),
        (conflicts_with or {}, "conflicts_with"),
    ):
        for name, values in mapping.items():
            artifact = by_name.get(str(name or "").strip())
            if artifact is None:
                continue
            relation = _relation_bucket(artifact)
            relation[field] = _unique_names([*relation[field], *list(values or [])])
    store.refresh_all_dependencies()


def _static_dependency_validation(artifact: SkillArtifact, *, known_names: Iterable[str]) -> Dict[str, Any]:
    text = f"{artifact.description}\n{artifact.body}"
    known = {str(item).strip() for item in known_names if str(item).strip() and str(item).strip() != artifact.name}
    mentioned_skills = sorted(name for name in known if name and re.search(rf"\b{re.escape(name)}\b", text))
    called_symbols: List[str] = []
    called_skill_names: List[str] = []
    parse_error = ""
    code_like = artifact.injection_type() == "functional" or artifact.kind in {"executable_tool", "function_tool", "script_tool"}
    if code_like:
        try:
            tree = ast.parse(artifact.body)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Name):
                        called_symbols.append(fn.id)
                        if fn.id in known:
                            called_skill_names.append(fn.id)
                    elif isinstance(fn, ast.Attribute):
                        called_symbols.append(fn.attr)
                        if fn.attr in known:
                            called_skill_names.append(fn.attr)
        except SyntaxError as exc:
            parse_error = str(exc)
    auto_dependencies = sorted(set([*mentioned_skills, *called_skill_names]))
    explicit = sorted({str(item).strip() for item in (artifact.dependencies or []) if str(item).strip()})
    return {
        "code_like": code_like,
        "parse_error": parse_error,
        "called_symbols": sorted(set(called_symbols)),
        "called_skill_names": sorted(set(called_skill_names)),
        "auto_dependencies": auto_dependencies,
        "explicit_dependencies": explicit,
        "missing_explicit_dependencies": sorted(set(auto_dependencies) - set(explicit)),
        "stale_explicit_dependencies": sorted(set(explicit) - set(auto_dependencies)),
    }


def validate_skill_static_dependencies(store: ArtifactStore, artifact_names: Sequence[str] | None = None) -> List[Dict[str, Any]]:
    target_names = set(_unique_names(artifact_names or []))
    known_names = [artifact.name for artifact in store.all()]
    reports: List[Dict[str, Any]] = []
    for artifact in store.all():
        if target_names and artifact.name not in target_names:
            continue
        report = _static_dependency_validation(artifact, known_names=known_names)
        artifact.metadata["static_dependency_validation"] = copy.deepcopy(report)
        if report["auto_dependencies"]:
            artifact.metadata.setdefault("auto_dependencies", report["auto_dependencies"])
            artifact.dependencies = _unique_names([*list(artifact.dependencies or []), *report["auto_dependencies"]])
            update_skill_relation_graph(store, calls={artifact.name: report["auto_dependencies"]})
        reports.append({"skill_name": artifact.name, **report})
    return reports


def _dependency_neighborhood(store: ArtifactStore, artifact: SkillArtifact, *, extra_names: Sequence[str] | None = None) -> List[Dict[str, Any]]:
    relation = dict(artifact.metadata.get("skill_relation_graph") or {})
    neighbor_names = set(artifact.dependencies or [])
    neighbor_names.update(extra_names or [])
    for key in ("calls", "called_by", "co_retrieved_with", "co_used_with", "derived_from", "refines", "conflicts_with"):
        neighbor_names.update(str(item).strip() for item in (relation.get(key) or []) if str(item).strip())
    summaries = summarize_dependency_context(
        candidate
        for candidate in store.all()
        if candidate.name == artifact.name or candidate.name in neighbor_names
    )
    return summaries[:12]


def _maintenance_concurrency() -> int:
    return max(1, _env_int("BFCL_MAINTENANCE_CONCURRENCY", 2))


def _bundle_case_limit_per_polarity() -> int:
    return max(1, _env_int("BFCL_BUNDLE_CASE_LIMIT_PER_POLARITY", 2))


def _bundle_max_total_cases() -> int:
    return max(1, _env_int("BFCL_BUNDLE_MAX_TOTAL_CASES", 6))


def _result_signature(run: Dict[str, Any]) -> Dict[str, Any]:
    metrics = run.get("metrics") or {}
    return {
        "official_valid": metrics.get("official_valid"),
        "official_error_type": metrics.get("official_error_type"),
        "call_f1": metrics.get("call_f1"),
        "n_model_steps": metrics.get("n_model_steps"),
        "total_tokens": metrics.get("total_tokens"),
        "retrieved_skills": metrics.get("retrieved_skills", []) or [],
        "prompt_injected_skills": metrics.get("prompt_injected_skills", []) or [],
        "tool_injected_skills": metrics.get("tool_injected_skills", []) or [],
        "called_skill_tools": metrics.get("called_skill_tools", []) or [],
        "used_skills": metrics.get("used_skills", []) or [],
        "call_errors": metrics.get("call_errors", []) or [],
    }


def _tool_names_from_results(results: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for result in results:
        for call in ((result.get("trace") or {}).get("tool_calls") or []):
            name = str(call.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        for error in ((result.get("metrics") or {}).get("call_errors") or []):
            for key in ("name", "expected_name", "actual_name"):
                name = str(error.get(key) or "").strip()
                if name and name not in names:
                    names.append(name)
    return names


def _domains_from_results(results: List[Dict[str, Any]]) -> List[str]:
    domains: List[str] = []
    for result in results:
        task = dict(result.get("task") or {})
        metadata = dict(task.get("metadata") or {})
        for item in (metadata.get("involved_classes") or []):
            value = str(item).strip()
            if value and value not in domains:
                domains.append(value)
        if not domains:
            metrics = dict(result.get("metrics") or {})
            official_check = dict(metrics.get("official_check") or {})
            for item in (official_check.get("involved_classes") or []):
                value = str(item).strip()
                if value and value not in domains:
                    domains.append(value)
    return domains


def _domains_from_segment_dicts(segments: Sequence[Dict[str, Any]]) -> List[str]:
    domains: List[str] = []
    for segment in segments:
        candidates = [
            dict(((segment.get("raw") or {}).get("task") or {}).get("metadata") or {}),
            dict(segment.get("metadata") or {}),
        ]
        for metadata in candidates:
            for item in (metadata.get("involved_classes") or metadata.get("domains") or []):
                value = str(item).strip()
                if value and value not in domains:
                    domains.append(value)
    return domains


def _intent_keywords_from_results(results: List[Dict[str, Any]]) -> List[str]:
    keywords: List[str] = []
    for result in results:
        task = dict(result.get("task") or {})
        question = task.get("question") or []
        for turn in question:
            if isinstance(turn, list):
                for message in turn:
                    if isinstance(message, dict):
                        text = str(message.get("content") or "")
                    else:
                        text = str(message)
                    for token in text.lower().replace("'", " ").replace('"', " ").split():
                        token = token.strip(".,!?()[]{}:;")
                        if len(token) < 4:
                            continue
                        if token.isdigit():
                            continue
                        if token not in keywords:
                            keywords.append(token)
                        if len(keywords) >= 12:
                            return keywords
    return keywords


def _first_run(item: Dict[str, Any]) -> Dict[str, Any]:
    runs = item.get("runs", []) or []
    return runs[0] if runs else {}


def _official_valid(run: Dict[str, Any]) -> bool | None:
    return (run.get("metrics") or {}).get("official_valid")


def _metrics_int(run: Dict[str, Any], key: str) -> int:
    value = (run.get("metrics") or {}).get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def _metrics_float(run: Dict[str, Any], key: str) -> float:
    value = (run.get("metrics") or {}).get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def _flatten_tool_calls(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls = trace.get("tool_calls") or []
    if isinstance(calls, list) and calls:
        return copy.deepcopy(calls)
    out: List[Dict[str, Any]] = []
    for turn in trace.get("turns") or []:
        turn_index = turn.get("turn_index")
        for call in turn.get("tool_calls") or []:
            row = copy.deepcopy(call)
            row.setdefault("turn_index", turn_index)
            out.append(row)
    return out


def _artifact_semantic_fingerprint(artifact: SkillArtifact) -> str:
    payload = {
        "name": artifact.name,
        "kind": artifact.kind,
        "description": artifact.description,
        "body": artifact.body,
        "interface": artifact.interface.as_dict(),
        "metadata": {
            key: value
            for key, value in dict(artifact.metadata or {}).items()
            if key not in {
                "bundle_generated_at",
                "bundle_input_signature",
                "last_bundle_test_signature",
                "last_bundle_test_result_id",
                "last_bundle_test_cached",
                "version_kind",
            }
        },
        "dependencies": list(artifact.dependencies or []),
        "dependency_pins": [item.as_dict() for item in (artifact.dependency_pins or [])],
        "status": artifact.status,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _bundle_case_signature(case: SkillBundleCase) -> Dict[str, Any]:
    return {
        "case_id": case.case_id,
        "source": case.source,
        "prompt": case.prompt,
        "expected": copy.deepcopy(case.expected or {}),
        "context": copy.deepcopy(case.context or {}),
        "tags": list(case.tags or []),
        "polarity": case.polarity,
        "contrast_protocol": copy.deepcopy(case.contrast_protocol or {}),
    }


def _bundle_input_signature(
    artifact: SkillArtifact,
    *,
    train_details: List[Dict[str, Any]],
    replay_details: List[Dict[str, Any]],
    integration_failures: List[Dict[str, Any]],
) -> str:
    source_task_ids = sorted(
        str(item).strip()
        for item in (artifact.metadata.get("source_task_ids") or [])
        if str(item).strip()
    )
    train_summary = []
    for detail in train_details:
        task_id = str(detail.get("task_id") or "").strip()
        if source_task_ids and task_id not in source_task_ids:
            continue
        run = _first_run(detail)
        train_summary.append(
            {
                "task_id": task_id,
                "score": run.get("score"),
                "result": _result_signature(run),
            }
        )
    replay_summary = []
    for detail in replay_details:
        task_id = str(detail.get("task_id") or "").strip()
        run = _first_run(detail)
        if not _skill_matches_run(artifact, run):
            continue
        replay_summary.append(
            {
                "task_id": task_id,
                "score": run.get("score"),
                "result": _result_signature(run),
            }
        )
    payload = {
        "artifact": json.loads(_artifact_semantic_fingerprint(artifact)),
        "source_task_ids": source_task_ids,
        "train_summary": train_summary,
        "replay_summary": replay_summary,
        "integration_failures": [
            {
                "task_id": item.get("task_id"),
                "metrics": copy.deepcopy((item.get("metrics") or {})),
                "error": item.get("error"),
            }
            for item in integration_failures
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _bundle_test_signature(
    artifact: SkillArtifact,
    *,
    max_steps_per_turn: int,
    adapter_mode: str,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
) -> str:
    bundle_fixtures = {
        key: value
        for key, value in dict(artifact.bundle.fixtures or {}).items()
        if key not in {
            "bundle_generated_at",
            "bundle_reused",
            "bundle_input_signature",
            "bundle_split_count",
            "bundle_trimmed",
            "last_bundle_test_signature",
            "last_bundle_test_result_id",
            "last_bundle_test_cached",
        }
    }
    payload = {
        "artifact": json.loads(_artifact_semantic_fingerprint(artifact)),
        "bundle_id": artifact.bundle.bundle_id,
        "bundle_version": artifact.bundle.bundle_version,
        "bundle_cases": [_bundle_case_signature(case) for case in artifact.bundle.all_cases()],
        "bundle_fixtures": bundle_fixtures,
        "max_steps_per_turn": max_steps_per_turn,
        "adapter_mode": adapter_mode,
        "execution_backend": execution_backend,
        "prompt_style": prompt_style,
        "tool_api_style": tool_api_style,
        "synthetic_continue": synthetic_continue,
        "explicit_skill_tool": explicit_skill_tool,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _latest_matching_test_result(
    store: ArtifactStore,
    *,
    artifact: SkillArtifact,
    test_signature: str,
    run_label: str = "llm_bundle_unit",
) -> SkillTestResult | None:
    target_skill_name = artifact.name
    for result in reversed(store.test_results(skill_name=target_skill_name, run_label=run_label)):
        if result.skill_version != artifact.version:
            continue
        if result.bundle_version != artifact.bundle.bundle_version:
            continue
        if result.dependency_versions != artifact.dependency_version_map():
            continue
        if str((result.aggregate or {}).get("test_signature") or "") != test_signature:
            continue
        return copy.deepcopy(result)
    return None


def _trace_summary(result: Any) -> Dict[str, Any]:
    trace = copy.deepcopy(getattr(result, "trace", {}) or {})
    metrics = copy.deepcopy(getattr(result, "metrics", {}) or {})
    return {
        "task_id": getattr(result, "task_id", ""),
        "official_valid": metrics.get("official_valid"),
        "official_error_type": metrics.get("official_error_type"),
        "call_f1": metrics.get("call_f1"),
        "call_errors": metrics.get("call_errors") or [],
        "retrieved_skills": metrics.get("retrieved_skills") or [],
        "prompt_injected_skills": metrics.get("prompt_injected_skills") or [],
        "tool_injected_skills": metrics.get("tool_injected_skills") or [],
        "used_skills": metrics.get("used_skills") or [],
        "n_turns": len(trace.get("turns") or []),
        "n_messages": len(trace.get("messages") or []),
        "n_tool_calls": len(_flatten_tool_calls(trace)),
        "n_debug_events": len(trace.get("debug_events") or []),
        "total_tokens": metrics.get("total_tokens", 0),
        "completion_tokens": metrics.get("completion_tokens", 0),
        "n_model_steps": metrics.get("n_model_steps", 0),
        "elapsed_s": metrics.get("elapsed_s"),
        "error": getattr(result, "error", None),
    }


def _normalize_expected_tool_rows(expected: Dict[str, Any], key: str = "tool_calls") -> List[Dict[str, Any]]:
    rows = expected.get(key) or []
    if not isinstance(rows, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        arguments = item.get("arguments")
        normalized.append(
            {
                "name": name,
                "arguments": dict(arguments) if isinstance(arguments, dict) else None,
            }
        )
    return normalized


def _tool_call_matches_expected(actual: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    if str(actual.get("name") or "").strip() != str(expected.get("name") or "").strip():
        return False
    expected_args = expected.get("arguments")
    if expected_args is None:
        return True
    actual_args = actual.get("arguments")
    if not isinstance(actual_args, dict):
        return False
    for key, value in expected_args.items():
        if actual_args.get(key) != value:
            return False
    return True


def _expected_rows_from_task_expected(expected_turns: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    if not isinstance(expected_turns, list):
        return rows, [{"type": "invalid_task_expected_shape", "expected": expected_turns}]
    for turn_index, turn in enumerate(expected_turns):
        if not isinstance(turn, list):
            failures.append({"type": "invalid_task_expected_turn_shape", "turn_index": turn_index, "turn": turn})
            continue
        for call_index, raw_call in enumerate(turn):
            try:
                name, args = _parse_expected_call_source(str(raw_call))
            except ValueError as exc:
                failures.append(
                    {
                        "type": "invalid_task_expected_call",
                        "turn_index": turn_index,
                        "call_index": call_index,
                        "call": raw_call,
                        "error": str(exc),
                    }
                )
                continue
            rows.append(
                {
                    "name": name,
                    "arguments": args,
                    "turn_index": turn_index,
                    "call_index": call_index,
                    "source": str(raw_call),
                }
            )
    return rows, failures


def _simplified_observed_rows(observed_calls: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for idx, call in enumerate(observed_calls):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip()
        if not name:
            continue
        args = call.get("arguments")
        rows.append(
            {
                "name": name,
                "arguments": copy.deepcopy(args if isinstance(args, dict) else {}),
                "turn_index": call.get("turn_index"),
                "call_index": idx,
            }
        )
    return rows


def _exact_task_expected_failures(*, observed_calls: Sequence[Dict[str, Any]], task: BenchmarkTask) -> List[Dict[str, Any]]:
    expected_rows, parse_failures = _expected_rows_from_task_expected(task.expected or [])
    failures: List[Dict[str, Any]] = list(parse_failures)
    observed_rows = _simplified_observed_rows(observed_calls)
    if len(observed_rows) != len(expected_rows):
        failures.append(
            {
                "type": "task_expected_call_count_mismatch",
                "expected_count": len(expected_rows),
                "observed_count": len(observed_rows),
                "expected_calls": [
                    {"name": row["name"], "arguments": row["arguments"]}
                    for row in expected_rows
                ],
                "observed_calls": [
                    {"name": row["name"], "arguments": row["arguments"]}
                    for row in observed_rows
                ],
            }
        )
    for idx, expected in enumerate(expected_rows):
        if idx >= len(observed_rows):
            failures.append({"type": "missing_task_expected_call_at_index", "index": idx, "expected": expected})
            continue
        observed = observed_rows[idx]
        if observed.get("name") != expected.get("name") or observed.get("arguments") != expected.get("arguments"):
            failures.append(
                {
                    "type": "task_expected_call_mismatch",
                    "index": idx,
                    "expected": {
                        "name": expected.get("name"),
                        "arguments": expected.get("arguments"),
                    },
                    "observed": {
                        "name": observed.get("name"),
                        "arguments": observed.get("arguments"),
                    },
                }
            )
    return failures


def _run_contract_assertions(
    *,
    case: SkillBundleCase,
    result: Any,
    task: BenchmarkTask,
) -> Dict[str, Any]:
    metrics = copy.deepcopy(getattr(result, "metrics", {}) or {})
    trace = copy.deepcopy(getattr(result, "trace", {}) or {})
    expected = copy.deepcopy(case.expected or {})
    polarity = str(case.polarity or "").strip().lower()
    failures: List[Dict[str, Any]] = []

    expected_valid = expected.get("official_valid")
    actual_valid = metrics.get("official_valid")
    if expected_valid is not None and actual_valid is not expected_valid:
        failures.append(
            {
                "type": "official_valid_mismatch",
                "expected": expected_valid,
                "actual": actual_valid,
            }
        )

    observed_calls = _flatten_tool_calls(trace)
    for row in _normalize_expected_tool_rows(expected, "tool_calls"):
        if not any(_tool_call_matches_expected(call, row) for call in observed_calls):
            failures.append({"type": "missing_expected_tool_call", "expected": row})

    for row in _normalize_expected_tool_rows(expected, "forbidden_tool_calls"):
        if any(_tool_call_matches_expected(call, row) for call in observed_calls):
            failures.append({"type": "forbidden_tool_call_present", "forbidden": row})

    call_error_text = [
        json.dumps(item, ensure_ascii=False)
        for item in (metrics.get("call_errors") or [])
    ]
    for needle in [str(item).strip() for item in (expected.get("required_call_error_substrings") or []) if str(item).strip()]:
        if not any(needle in row for row in call_error_text):
            failures.append({"type": "missing_required_call_error", "expected_substring": needle})
    for needle in [str(item).strip() for item in (expected.get("forbidden_call_error_substrings") or []) if str(item).strip()]:
        if any(needle in row for row in call_error_text):
            failures.append({"type": "forbidden_call_error_present", "forbidden_substring": needle})

    if polarity in {"positive", "integration"}:
        if any(call.get("error") for call in observed_calls if isinstance(call, dict)):
            failures.append(
                {
                    "type": "runtime_tool_error_present",
                    "errors": [
                        {
                            "name": str(call.get("name") or ""),
                            "arguments": copy.deepcopy(call.get("arguments") or {}),
                            "error": str(call.get("error") or ""),
                        }
                        for call in observed_calls
                        if isinstance(call, dict) and call.get("error")
                    ],
                }
            )
        if metrics.get("call_errors"):
            failures.append(
                {
                    "type": "call_error_present",
                    "call_errors": copy.deepcopy(metrics.get("call_errors") or []),
                }
            )

    if expected.get("match_task_expected") is True and actual_valid is not True:
        failures.append(
            {
                "type": "task_expected_not_satisfied",
                "task_expected": copy.deepcopy(task.expected or []),
            }
        )
    if expected.get("match_task_expected") is True:
        failures.extend(_exact_task_expected_failures(observed_calls=observed_calls, task=task))

    return {"passed": not failures, "failures": failures}


def _case_run_payload(
    *,
    case: SkillBundleCase,
    task: BenchmarkTask,
    artifact: SkillArtifact,
    result: Any,
    variant: str,
    top_k_skills: int,
    skill_injection_mode: str,
) -> Dict[str, Any]:
    trace = copy.deepcopy(getattr(result, "trace", {}) or {})
    metrics = copy.deepcopy(getattr(result, "metrics", {}) or {})
    expected_behavior = {
        "bundle_case_expected": copy.deepcopy(case.expected or {}),
        "task_expected": copy.deepcopy(task.expected or []),
        "contrast_protocol": copy.deepcopy(case.contrast_protocol or {}),
        "polarity": case.polarity,
    }
    actual_output = {
        "benchmark": getattr(result, "benchmark", ""),
        "task_id": getattr(result, "task_id", ""),
        "success": getattr(result, "success", False),
        "score": getattr(result, "score", 0.0),
        "metrics": metrics,
        "error": getattr(result, "error", None),
        "trace_summary": _trace_summary(result),
    }
    return {
        "input_payload": {
            "task": task.as_dict(),
            "variant": variant,
            "top_k_skills": top_k_skills,
            "skill_injection_mode": skill_injection_mode,
            "llm_test_scope": "single_skill_with_without_counterfactual",
        },
        "expected_behavior": expected_behavior,
        "actual_output": actual_output,
        "tool_calls": _flatten_tool_calls(trace),
        "trace_summary": actual_output["trace_summary"],
        "skill_snapshot": artifact.as_dict() if variant == "with_skill" else {},
        "bundle_case_snapshot": case.as_dict(),
    }


def _skill_matches_run(skill: SkillArtifact, run: Dict[str, Any]) -> bool:
    metrics = run.get("metrics") or {}
    seen = [
        metrics.get("retrieved_skills", []) or [],
        metrics.get("prompt_injected_skills", []) or [],
        metrics.get("tool_injected_skills", []) or [],
        metrics.get("called_skill_tools", []) or [],
        metrics.get("used_skills", []) or [],
    ]
    return any(skill.name in values for values in seen)


def _result_from_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    run = _first_run(detail)
    return {
        "benchmark": run.get("benchmark", "bfcl_v3"),
        "task_id": detail.get("task_id"),
        "task": copy.deepcopy(detail.get("task") or {}),
        "success": run.get("success"),
        "score": run.get("score"),
        "metrics": copy.deepcopy(run.get("metrics") or {}),
        "trace": copy.deepcopy(run.get("trace") or {}),
        "error": run.get("error"),
    }


def _task_from_case(case: SkillBundleCase) -> BenchmarkTask | None:
    fragment = ((case.context or {}).get("task_fragment") or {})
    question = fragment.get("question")
    expected = fragment.get("expected")
    if not question or expected is None:
        return None
    question = _normalize_fragment_question(question)
    expected = _normalize_fragment_expected(expected)
    task_id = str(_case_task_id(case)).strip() or case.case_id
    input_artifacts = copy.deepcopy(fragment.get("input_artifacts") or {})
    metadata = copy.deepcopy(fragment.get("metadata") or {})
    source_task_id = str((case.context or {}).get("source_task_id") or "").strip()
    if not input_artifacts or not metadata.get("involved_classes"):
        source_task = _source_task_snapshot(task_id)
        if source_task:
            input_artifacts = input_artifacts or copy.deepcopy(source_task.get("input_artifacts") or {})
            metadata = {**copy.deepcopy(source_task.get("metadata") or {}), **metadata}
    if str(task_id).startswith("task_from_trace"):
        metadata["_bundle_case_invalid"] = {
            "reason": "non_replayable_synthetic_task_id",
            "task_id": task_id,
            "source_task_id": source_task_id,
        }
    else:
        invalid_bundle = _validate_bundle_task_fragment(
            task_id=task_id,
            source_task_id=source_task_id,
            question=question,
            expected=expected,
        )
        if invalid_bundle:
            metadata["_bundle_case_invalid"] = invalid_bundle
    if not input_artifacts.get("initial_config") or not metadata.get("involved_classes"):
        metadata.setdefault(
            "_bundle_case_invalid",
            {
                "reason": "missing_official_execution_context",
                "task_id": task_id,
                "source_task_id": source_task_id,
                "has_initial_config": bool(input_artifacts.get("initial_config")),
                "has_involved_classes": bool(metadata.get("involved_classes")),
            },
        )
    return BenchmarkTask(
        benchmark="bfcl_v3",
        task_id=task_id,
        question=copy.deepcopy(question),
        expected=copy.deepcopy(expected),
        input_artifacts=input_artifacts,
        metadata=metadata,
    )


_SOURCE_TASK_CACHE: Dict[str, Dict[str, Any]] | None = None


def _source_task_snapshot(task_id: str) -> Dict[str, Any]:
    global _SOURCE_TASK_CACHE
    if _SOURCE_TASK_CACHE is None:
        try:
            from academic.benchmarks.bfcl import load_bfcl_tasks

            cache_dir = Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3")
            train, test = load_bfcl_tasks(
                cache_dir=cache_dir,
                split_seed=42,
                n_train=50,
                n_test=150,
                data_source="bfcl_eval_bundle",
            )
            _SOURCE_TASK_CACHE = {task.task_id: task.as_dict() for task in train + test}
        except Exception:
            _SOURCE_TASK_CACHE = {}
    return copy.deepcopy((_SOURCE_TASK_CACHE or {}).get(str(task_id), {}))


_SOURCE_TOOL_SCHEMA_CACHE: Dict[str, Dict[str, Dict[str, Any]]] | None = None


def _source_tool_schemas_by_class() -> Dict[str, Dict[str, Dict[str, Any]]]:
    global _SOURCE_TOOL_SCHEMA_CACHE
    if _SOURCE_TOOL_SCHEMA_CACHE is None:
        try:
            cache_dir = Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3")
            tools = load_bfcl_tools(cache_dir=cache_dir, data_source="bfcl_eval_bundle")
            _SOURCE_TOOL_SCHEMA_CACHE = {}
            for tool in tools:
                fn = dict(tool.get("function") or {})
                cls = str(fn.get("x_bfcl_class") or "").strip()
                name = str(fn.get("name") or "").strip()
                if cls and name:
                    _SOURCE_TOOL_SCHEMA_CACHE.setdefault(cls, {})[name] = copy.deepcopy(tool)
        except Exception:
            _SOURCE_TOOL_SCHEMA_CACHE = {}
    return copy.deepcopy(_SOURCE_TOOL_SCHEMA_CACHE or {})


def _tool_schemas_for_source_task(source_task: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    metadata = copy.deepcopy(source_task.get("metadata") or {})
    classes = [str(item).strip() for item in (metadata.get("involved_classes") or []) if str(item).strip()]
    if not classes:
        return {}
    by_class = _source_tool_schemas_by_class()
    schemas: Dict[str, Dict[str, Any]] = {}
    for cls in classes:
        schemas.update(copy.deepcopy(by_class.get(cls) or {}))
    if schemas:
        return schemas
    try:
        cache_dir = Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3")
        tools = load_bfcl_tools(cache_dir=cache_dir, data_source="bfcl_eval_bundle")
        task = BenchmarkTask(
            benchmark="bfcl_v3",
            task_id=str(source_task.get("task_id") or ""),
            question=copy.deepcopy(source_task.get("question") or []),
            expected=copy.deepcopy(source_task.get("expected") or []),
            input_artifacts=copy.deepcopy(source_task.get("input_artifacts") or {}),
            metadata=metadata,
        )
        return {
            str((tool.get("function") or {}).get("name") or ""): copy.deepcopy(tool)
            for tool in filter_bfcl_tools_by_class(tools, task)
            if str((tool.get("function") or {}).get("name") or "")
        }
    except Exception:
        return {}


_BFCL_DOMAIN_NAMES = {
    "TradingBot",
    "VehicleControlAPI",
    "TravelAPI",
    "TicketAPI",
    "TwitterAPI",
    "GorillaFileSystem",
}


def _domains_mentioned_in_text(text: str) -> List[str]:
    lower = str(text or "").lower()
    out: List[str] = []
    for name in sorted(_BFCL_DOMAIN_NAMES):
        if name.lower() in lower:
            out.append(name)
    return out


def _strong_harmful_credit_context(credit_context: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    strong: List[Dict[str, Any]] = []
    for event in credit_context:
        if str(event.get("judgment") or "").strip().lower() != "harmful":
            continue
        confidence = float(event.get("confidence") or 0.0)
        effect_type = str(event.get("effect_type") or "").strip().lower()
        evidence_strength = str(event.get("evidence_strength") or "").strip().lower()
        actions = [
            str(item.get("action") or "").strip().lower()
            for item in (event.get("maintenance_actions") or [])
            if isinstance(item, dict)
        ]
        if (
            confidence >= 0.65
            and (
                effect_type in {"domain_mismatch", "workflow_pollution", "schema_harm", "correctness_harm"}
                or evidence_strength in {"strong", "medium"}
                or any(action in {"narrow_scope", "fix_schema_contract", "refine_workflow", "disable_candidate"} for action in actions)
            )
        ):
            strong.append(dict(event))
    return strong


def _fallback_scope_refine_payload_for_strong_harm(
    artifact: SkillArtifact,
    *,
    credit_context: Sequence[Dict[str, Any]],
    decision_reason: str,
) -> Dict[str, Any] | None:
    strong_harm = _strong_harmful_credit_context(credit_context)
    if not strong_harm:
        return None
    excluded_domains: List[str] = []
    harmful_task_ids: List[str] = []
    reasons: List[str] = []
    for event in strong_harm:
        task_id = str(event.get("task_id") or "").strip()
        if task_id and task_id not in harmful_task_ids:
            harmful_task_ids.append(task_id)
        text = " ".join(
            [
                str(event.get("effect_type") or ""),
                str(event.get("reason") or ""),
                " ".join(str(item) for item in ((event.get("evidence") or {}).get("trace_signals") or [])),
            ]
        )
        for domain in _domains_mentioned_in_text(text):
            if domain not in excluded_domains and domain not in set(artifact.metadata.get("domains") or []):
                excluded_domains.append(domain)
        if event.get("reason"):
            reasons.append(str(event["reason"]))

    current_guard = dict(artifact.metadata.get("retrieval_guard") or {})
    merged_excluded = sorted(set(list(current_guard.get("excluded_domains") or []) + excluded_domains))
    metadata_patch = {
        "retrieval_scope_refine_required": False,
        "retrieval_guard": {
            **current_guard,
            "excluded_domains": merged_excluded,
            "harmful_task_ids": sorted(set(list(current_guard.get("harmful_task_ids") or []) + harmful_task_ids)),
            "source": "strong_harmful_credit_fallback",
        },
        "last_refiner_keep_overridden": True,
        "last_refiner_keep_reason": decision_reason,
    }
    notes = " ".join(reasons[:2])
    return {
        "decision": {
            "action": "refine_minor",
            "reason": (
                "LLM returned keep despite strong harmful credit; applying minimal retrieval scope guard. "
                + notes[:500]
            ).strip(),
            "version_kind": "minor",
            "migration_reason": "strong_harmful_credit_scope_guard",
            "pinned_dependencies": [],
        },
        "artifact": {
            "metadata": metadata_patch,
            "interface": {
                "summary": artifact.interface.summary or artifact.description,
                "usage": artifact.interface.usage,
                "input_contract": artifact.interface.input_contract,
                "output_contract": artifact.interface.output_contract,
                "invocation_contract": artifact.interface.invocation_contract,
                "compatibility_notes": (
                    (artifact.interface.compatibility_notes or "")
                    + " Retrieval is narrowed away from domains that produced strong harmful credit."
                ).strip(),
            },
        },
        "bundle": {"positive_cases": [], "negative_cases": [], "integration_cases": []},
    }


def _parse_expected_call_source(call: str) -> Tuple[str, Dict[str, Any]]:
    try:
        tree = ast.parse(str(call).strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid call syntax: {call}") from exc
    if not isinstance(tree.body, ast.Call):
        raise ValueError(f"not a call: {call}")
    func = tree.body.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if not name:
        raise ValueError(f"missing tool name: {call}")
    args: Dict[str, Any] = {}
    for idx, arg in enumerate(tree.body.args):
        try:
            args[f"arg{idx}"] = ast.literal_eval(arg)
        except Exception as exc:
            raise ValueError(f"non-literal positional argument in expected call {call}: arg{idx}") from exc
    for kw in tree.body.keywords:
        if kw.arg is None:
            raise ValueError(f"unsupported variadic keyword in expected call: {call}")
        try:
            args[kw.arg] = ast.literal_eval(kw.value)
        except Exception as exc:
            raise ValueError(f"non-literal keyword argument in expected call {call}: {kw.arg}") from exc
    return name, args


def _validate_expected_call_schema(
    expected: List[List[str]],
    *,
    tool_schemas: Dict[str, Dict[str, Any]],
) -> Dict[str, Any] | None:
    for turn_index, turn in enumerate(expected or []):
        for call_index, raw_call in enumerate(turn or []):
            try:
                name, args = _parse_expected_call_source(raw_call)
            except ValueError as exc:
                return {
                    "reason": "invalid_expected_call_syntax",
                    "turn_index": turn_index,
                    "call_index": call_index,
                    "call": raw_call,
                    "error": str(exc),
                }
            schema = dict(tool_schemas.get(name) or {})
            if not schema:
                return {
                    "reason": "unknown_expected_tool",
                    "turn_index": turn_index,
                    "call_index": call_index,
                    "call": raw_call,
                    "tool_name": name,
                }
            parameters = dict((schema.get("function") or {}).get("parameters") or {})
            properties = dict(parameters.get("properties") or {})
            allowed = set(properties)
            unknown = sorted(key for key in args if not key.startswith("arg") and key not in allowed)
            if unknown:
                return {
                    "reason": "unknown_expected_tool_argument",
                    "turn_index": turn_index,
                    "call_index": call_index,
                    "call": raw_call,
                    "tool_name": name,
                    "unknown_arguments": unknown,
                    "allowed_arguments": sorted(allowed),
                }
            positional = sorted(key for key in args if key.startswith("arg"))
            if positional and len(properties) != len(positional):
                return {
                    "reason": "ambiguous_positional_expected_arguments",
                    "turn_index": turn_index,
                    "call_index": call_index,
                    "call": raw_call,
                    "tool_name": name,
                    "positional_arguments": positional,
                    "allowed_arguments": sorted(allowed),
                }
    return None


def _validate_bundle_task_fragment(
    *,
    task_id: str,
    source_task_id: str,
    question: List[List[Dict[str, Any]]],
    expected: List[List[str]],
) -> Dict[str, Any] | None:
    candidate_ids = [item for item in [source_task_id, task_id] if item]
    seen: set[str] = set()
    for candidate_id in candidate_ids:
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        source_task = _source_task_snapshot(candidate_id)
        if not source_task:
            continue
        source_question = _normalize_fragment_question(copy.deepcopy(source_task.get("question") or []))
        source_expected = _normalize_fragment_expected(copy.deepcopy(source_task.get("expected") or []))
        schema_invalid = _validate_expected_call_schema(
            expected,
            tool_schemas=_tool_schemas_for_source_task(source_task),
        )
        if schema_invalid:
            return schema_invalid
        if _is_replayable_fragment(
            source_question=source_question,
            source_expected=source_expected,
            fragment_question=question,
            fragment_expected=expected,
        ):
            return None
    return {
        "reason": "fragment_mismatch_with_source_task",
        "source_task_id": source_task_id,
        "task_id": task_id,
    }


def _is_replayable_fragment(
    *,
    source_question: List[List[Dict[str, Any]]],
    source_expected: List[List[str]],
    fragment_question: List[List[Dict[str, Any]]],
    fragment_expected: List[List[str]],
) -> bool:
    if not fragment_question or fragment_expected is None:
        return False
    if len(fragment_question) != len(fragment_expected):
        return False
    if len(fragment_question) > len(source_question) or len(fragment_expected) > len(source_expected):
        return False
    if fragment_question == source_question[: len(fragment_question)] and fragment_expected == source_expected[: len(fragment_expected)]:
        return True
    max_start = min(len(source_question), len(source_expected)) - len(fragment_question)
    for start in range(max_start + 1):
        if (
            fragment_question == source_question[start : start + len(fragment_question)]
            and fragment_expected == source_expected[start : start + len(fragment_expected)]
        ):
            return True
    return False


def _normalize_fragment_question(question: Any) -> List[List[Dict[str, Any]]]:
    normalized: List[List[Dict[str, Any]]] = []
    for turn in list(question or []):
        if isinstance(turn, str):
            normalized.append([{"role": "user", "content": turn}])
            continue
        if isinstance(turn, dict):
            normalized.append([{"role": str(turn.get("role") or "user"), "content": str(turn.get("content") or "")}])
            continue
        if isinstance(turn, list):
            turn_messages: List[Dict[str, Any]] = []
            for message in turn:
                if isinstance(message, dict):
                    turn_messages.append(
                        {
                            "role": str(message.get("role") or "user"),
                            "content": str(message.get("content") or ""),
                        }
                    )
                elif isinstance(message, str):
                    turn_messages.append({"role": "user", "content": message})
            if turn_messages:
                normalized.append(turn_messages)
    return normalized


def _normalize_fragment_expected(expected: Any) -> List[List[str]]:
    normalized: List[List[str]] = []
    raw_expected = list(expected or [])
    if raw_expected and all(isinstance(item, (str, dict)) for item in raw_expected):
        turn_calls: List[str] = []
        for item in raw_expected:
            if isinstance(item, str) and item.strip():
                turn_calls.append(item)
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                args = dict(item.get("arguments") or {})
                if name:
                    turn_calls.append(_call_source(name, args))
        return [turn_calls] if turn_calls else []
    for turn in raw_expected:
        if isinstance(turn, str):
            normalized.append([turn])
            continue
        if isinstance(turn, dict):
            name = str(turn.get("name") or "").strip()
            args = dict(turn.get("arguments") or {})
            if name:
                normalized.append([_call_source(name, args)])
            continue
        if isinstance(turn, list):
            turn_calls: List[str] = []
            for item in turn:
                if isinstance(item, str) and item.strip():
                    turn_calls.append(item)
                elif isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    args = dict(item.get("arguments") or {})
                    if name:
                        turn_calls.append(_call_source(name, args))
            if turn_calls:
                normalized.append(turn_calls)
    return normalized


def _call_source(name: str, args: Dict[str, Any]) -> str:
    return f"{name}({','.join(f'{key}={repr(value)}' for key, value in args.items())})"


def _case_task_id(case: SkillBundleCase) -> str:
    ctx = case.context or {}
    return str(
        ctx.get("source_task_id")
        or ctx.get("task_id")
        or ((ctx.get("task_fragment") or {}).get("task_id"))
        or ""
    )


def _bundle_case_contract_failure(case: SkillBundleCase) -> Dict[str, Any] | None:
    task = _task_from_case(case)
    if task is None:
        return None
    invalid = dict(task.metadata.get("_bundle_case_invalid") or {})
    if not invalid:
        return None
    return {
        **invalid,
        "case_id": case.case_id,
        "source": case.source,
        "polarity": case.polarity,
    }


def _drop_invalid_bfcl_bundle_cases(artifact: SkillArtifact) -> List[Dict[str, Any]]:
    dropped: List[Dict[str, Any]] = []
    for attr in ("positive_cases", "negative_cases", "integration_cases"):
        kept: List[SkillBundleCase] = []
        for case in list(getattr(artifact.bundle, attr) or []):
            failure = _bundle_case_contract_failure(case)
            if failure:
                dropped.append({"bucket": attr, **failure})
                continue
            kept.append(case)
        setattr(artifact.bundle, attr, kept)
    if dropped:
        artifact.bundle.fixtures = {
            **dict(artifact.bundle.fixtures or {}),
            "dropped_invalid_bfcl_bundle_cases": dropped,
        }
    return dropped


def bfcl_trace_segments_from_details(details: List[Dict[str, Any]]) -> List[TraceSegment]:
    """Convert BFCL run details into skill-independent execution segments."""

    segments: List[TraceSegment] = []
    for detail in details or []:
        task_id = str(detail.get("task_id") or "").strip()
        task_snapshot = dict(detail.get("task") or {})
        run = _first_run(detail)
        trace = dict(run.get("trace") or {})
        metrics = dict(run.get("metrics") or {})
        turns = list(trace.get("turns") or [])
        call_errors = list(metrics.get("call_errors") or [])
        errors_by_turn: Dict[int, List[Dict[str, Any]]] = {}
        for err in call_errors:
            try:
                turn_idx = int(err.get("turn_index", 0) or 0)
            except Exception:
                turn_idx = 0
            errors_by_turn.setdefault(turn_idx, []).append(err)
        for idx, turn in enumerate(turns):
            user_messages = turn.get("user_messages") or []
            tool_calls = turn.get("tool_calls") or []
            tool_results = turn.get("tool_results") or []
            task_expected = task_snapshot.get("expected") or []
            expected = task_expected[idx] if isinstance(task_expected, list) and idx < len(task_expected) else []
            text = "\n".join(
                [
                    f"task_id: {task_id}",
                    "user_messages:",
                    json.dumps(user_messages, ensure_ascii=False),
                    "tool_calls:",
                    json.dumps(tool_calls, ensure_ascii=False),
                    "tool_results:",
                    json.dumps(tool_results, ensure_ascii=False),
                    "expected_calls:",
                    json.dumps(expected, ensure_ascii=False),
                ]
            )
            segments.append(
                TraceSegment(
                    segment_id=f"{task_id}:turn:{idx}",
                    task_id=task_id,
                    turn_index=idx,
                    text=text,
                    error_text=json.dumps(errors_by_turn.get(idx, []), ensure_ascii=False),
                    kind="bfcl_turn",
                    metadata={
                        "official_valid": metrics.get("official_valid"),
                        "call_f1": metrics.get("call_f1"),
                        "tool_names": [str(call.get("name") or "") for call in tool_calls if isinstance(call, dict)],
                        "expected": copy.deepcopy(expected),
                    },
                    raw={
                        "task": copy.deepcopy(task_snapshot),
                        "turn": copy.deepcopy(turn),
                        "metrics": copy.deepcopy(metrics),
                    },
                )
            )
        if not turns and (call_errors or task_snapshot):
            segments.append(
                TraceSegment(
                    segment_id=f"{task_id}:task",
                    task_id=task_id,
                    turn_index=None,
                    text=json.dumps({"task": task_snapshot, "run": run}, ensure_ascii=False),
                    error_text=json.dumps(call_errors, ensure_ascii=False),
                    kind="bfcl_task",
                    metadata={"official_valid": metrics.get("official_valid")},
                    raw={"task": copy.deepcopy(task_snapshot), "run": copy.deepcopy(run)},
                )
            )
    return segments


def _segment_embedding_map(segments: List[TraceSegment]) -> Dict[str, List[float]]:
    """Best-effort segment embeddings for overlap refactor.

    This helper intentionally does not fail hard by default because older smoke
    experiments and offline tests should remain runnable. The higher-level
    related-task experiment driver can enforce strictness if desired.
    """

    try:
        from app.meta_agent.skills.retrieval import SkillRetriever

        retriever = SkillRetriever()
    except Exception:
        return {}
    out: Dict[str, List[float]] = {}
    for segment in segments:
        try:
            embedding = retriever.generate_embedding(segment.searchable_text())
        except Exception:
            embedding = None
        if embedding:
            out[segment.segment_id] = embedding
    return out


def _skill_overlap_segments(store: ArtifactStore) -> List[TraceSegment]:
    segments: List[TraceSegment] = []
    for skill in store.all():
        if skill.is_disabled() or skill.status in {"rejected", "archived"}:
            continue
        segments.append(skill_to_overlap_segment(skill))
    return segments


async def extract_bfcl_skill_artifacts_llm(
    results: List[Dict[str, Any]],
    *,
    tool_schemas: Iterable[Dict[str, Any]] | None = None,
    existing_artifacts: Iterable[SkillArtifact] | None = None,
    extractor_rules: Iterable[Dict[str, Any]] | None = None,
    llm_config: str,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> List[SkillArtifact]:
    relevant_tools = set(_tool_names_from_results(results))
    inferred_domains = _domains_from_results(results)
    inferred_intents = _intent_keywords_from_results(results)
    filtered_tools = []
    for tool in tool_schemas or []:
        fn = dict(tool.get("function") or {})
        name = str(fn.get("name") or "").strip()
        if not relevant_tools or name in relevant_tools:
            filtered_tools.append(tool)
    artifacts = await extract_skill_artifacts_from_results_llm(
        results,
        tool_schemas=filtered_tools,
        existing_artifacts=existing_artifacts,
        extractor_rules=extractor_rules,
        llm_config=llm_config,
        model_name=model_name,
        audit_context=audit_context,
    )
    for artifact in artifacts:
        artifact.metadata.setdefault("benchmark", "bfcl_v3")
        artifact.metadata.setdefault("source", "llm_trace_extraction")
        artifact.metadata.setdefault("injection_type", artifact.injection_type())
        metadata_quality: Dict[str, Any] = dict(artifact.metadata.get("metadata_quality") or {})
        governed_tools = sorted(
            {
                str(item).strip()
                for item in (artifact.metadata.get("allowed_tools") or [])
                if str(item).strip()
            }
        )
        governed_domains = sorted(
            {
                str(item).strip()
                for item in (artifact.metadata.get("domains") or [])
                if str(item).strip() and str(item).strip().lower() != "all"
            }
        )
        artifact.metadata["allowed_tools"] = governed_tools
        artifact.metadata["domains"] = governed_domains
        if not governed_tools:
            metadata_quality["missing_allowed_tools"] = True
            metadata_quality["observed_task_tools"] = sorted(relevant_tools)
        if not governed_domains:
            metadata_quality["missing_domains"] = True
            metadata_quality["observed_task_domains"] = list(inferred_domains)
        if metadata_quality:
            artifact.metadata["metadata_quality"] = metadata_quality
        artifact.metadata["intent_keywords"] = list(dict.fromkeys(
            [str(item).strip().lower() for item in (artifact.metadata.get("intent_keywords") or []) if str(item).strip()]
            + list(inferred_intents)
        ))[:16]
        if not (artifact.metadata.get("source_task_ids") or []):
            artifact.metadata["source_task_ids"] = sorted({
                str(item.get("task_id") or "").strip()
                for item in results
                if str(item.get("task_id") or "").strip()
            })
    return artifacts


async def build_initial_skill_bundle_llm(
    artifact: SkillArtifact,
    *,
    source_results: List[Dict[str, Any]],
    replay_results: List[Dict[str, Any]] | None = None,
    integration_failures: List[Dict[str, Any]] | None = None,
    llm_config: str,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Any:
    """Build the first bundle for a new skill from focused source evidence."""

    return await distill_skill_bundle_llm(
        artifact,
        source_results=source_results,
        replay_results=replay_results or [],
        integration_failures=integration_failures or [],
        llm_config=llm_config,
        model_name=model_name,
        audit_context=audit_context,
    )


async def patch_skill_bundle_from_credit(
    artifact: SkillArtifact,
    *,
    integration_failures: List[Dict[str, Any]] | None = None,
    contract_validation_failures: List[Dict[str, Any]] | None = None,
    llm_config: str,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Patch an existing skill bundle using credit-assigned cases only."""

    credit_cases = [
        case.as_dict()
        for case in [
            *list(artifact.bundle.positive_cases or []),
            *list(artifact.bundle.negative_cases or []),
            *list(artifact.bundle.integration_cases or []),
        ]
        if str(case.source or "").startswith("credit_assigner_")
    ][-_bundle_max_total_cases():]
    return await maintain_skill_bundle_llm(
        artifact,
        integration_failures=integration_failures or [],
        credit_cases=credit_cases,
        contract_validation_failures=contract_validation_failures or [],
        llm_config=llm_config,
        model_name=model_name,
        audit_context=audit_context,
    )


async def build_bfcl_skill_bundles_llm(
    store: ArtifactStore,
    *,
    train_details: List[Dict[str, Any]],
    replay_details: List[Dict[str, Any]] | None = None,
    llm_config: str,
    model_name: str | None = None,
    artifact_names: List[str] | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> None:
    # Materialize lightweight train/replay results once so each skill bundle
    # builder can reuse the same normalized evidence instead of re-deriving it.
    train_results = [_result_from_detail(item) for item in train_details]
    replay_results = [_result_from_detail(item) for item in (replay_details or [])]
    target_names = {str(item).strip() for item in (artifact_names or []) if str(item).strip()}
    selected = [
        artifact
        for artifact in store.all()
        if not target_names or artifact.name in target_names
    ]
    sem = asyncio.Semaphore(_maintenance_concurrency())

    async def build_one(artifact: SkillArtifact) -> Tuple[str, Any]:
        # Integration failures are real counterexamples tied to this skill.
        # They are the strongest signal that the bundle should add/refresh
        # regression cases instead of reusing an older snapshot blindly.
        integration_failures = _integration_failures_for_skill(artifact, replay_details or [])
        # The bundle signature is our current cheap cache key. If the skill
        # semantics and supporting evidence are unchanged, we can reuse the
        # existing bundle asset and skip the expensive LLM bundle builder.
        bundle_signature = _bundle_input_signature(
            artifact,
            train_details=train_details,
            replay_details=replay_details or [],
            integration_failures=integration_failures,
        )
        existing_signature = str((artifact.bundle.fixtures or {}).get("bundle_input_signature") or "")
        if existing_signature == bundle_signature and artifact.bundle.all_cases():
            artifact.bundle.fixtures = {
                **dict(artifact.bundle.fixtures or {}),
                "bundle_input_signature": bundle_signature,
                "bundle_reused": True,
            }
            return artifact.name, copy.deepcopy(artifact.bundle)
        bundle_maintainer_used = False
        if artifact.bundle.all_cases():
            try:
                async with sem:
                    maintainer = await asyncio.wait_for(
                        patch_skill_bundle_from_credit(
                            artifact,
                            integration_failures=integration_failures,
                            llm_config=llm_config,
                            model_name=model_name,
                            audit_context={
                                **dict(audit_context or {}),
                                "artifact_name": artifact.name,
                            },
                        ),
                        timeout=45,
                    )
            except Exception as exc:
                maintainer = {
                    "action": "rebuild",
                    "reason": f"bundle maintainer failed: {type(exc).__name__}: {exc}",
                    "maintenance_notes": "",
                    "patch": {},
                }
            action = str(maintainer.get("action") or "rebuild").strip().lower()
            if action == "keep":
                artifact.bundle.fixtures = {
                    **dict(artifact.bundle.fixtures or {}),
                    "bundle_generated_at": _now_iso(),
                    "bundle_input_signature": bundle_signature,
                    "bundle_reused": True,
                    "bundle_maintainer_action": "keep",
                    "bundle_maintainer_reason": str(maintainer.get("reason") or ""),
                }
                return artifact.name, copy.deepcopy(artifact.bundle)
            if action == "patch":
                bundle = await apply_bundle_patch_payload_via_editor(
                    artifact,
                    patch_payload=dict(maintainer.get("patch") or {}),
                    maintenance_notes=str(maintainer.get("maintenance_notes") or ""),
                )
                artifact.bundle = bundle
                trim_bundle_cases(artifact)
                dropped = _drop_invalid_bfcl_bundle_cases(artifact)
                artifact.bundle.fixtures = {
                    **dict(artifact.bundle.fixtures or {}),
                    "bundle_generated_at": _now_iso(),
                    "bundle_input_signature": bundle_signature,
                    "bundle_reused": False,
                    "bundle_maintainer_action": "patch",
                    "bundle_maintainer_reason": str(maintainer.get("reason") or ""),
                    "bundle_contract_dropped_cases": dropped,
                }
                bundle_maintainer_used = True
                return artifact.name, copy.deepcopy(artifact.bundle)
        source_task_ids = [
            str(item).strip()
            for item in (artifact.metadata.get("source_task_ids") or [])
            if str(item).strip()
        ]
        artifact_source = str(artifact.metadata.get("source") or "").strip().lower()
        # Only spend an LLM call when the skill actually comes from evolving
        # evidence or we have integration failures that justify richer cases.
        # Hand-authored / static skills can stay on their existing bundle unless
        # they have no cases at all.
        should_use_llm_bundle = bool(
            source_task_ids
            or integration_failures
            or artifact_source in {"llm_trace_extraction", "evolve_rollouts", "manual_fault_probe"}
            or artifact.metadata.get("manual_fault_injected")
        )
        if not should_use_llm_bundle:
            bundle = copy.deepcopy(artifact.bundle)
            # Bootstrap static skills with a single minimal positive case so
            # downstream test machinery always has at least one executable row.
            if not bundle.all_cases():
                bundle.positive_cases = [
                    SkillBundleCase(
                        case_id=f"{artifact.name}:positive:0",
                        source="artifact_definition",
                        prompt=artifact.description,
                        expected={"injection_type": artifact.injection_type()},
                        context={"artifact_name": artifact.name},
                        tags=["bootstrap"],
                        polarity="positive",
                    )
                ]
            artifact.bundle = bundle
            trim_bundle_cases(artifact)
            artifact.bundle.fixtures = {
                **dict(artifact.bundle.fixtures or {}),
                "bundle_generated_at": _now_iso(),
                "bundle_input_signature": bundle_signature,
                "bundle_reused": False,
            }
            return artifact.name, copy.deepcopy(artifact.bundle)
        try:
            async with sem:
                # Ask the bundle-builder LLM to distill positive / negative /
                # integration-style cases from focused train evidence. This is
                # reserved for new/empty bundles; existing bundles use credit
                # patches above.
                bundle = await asyncio.wait_for(
                    build_initial_skill_bundle_llm(
                        artifact,
                        source_results=train_results,
                        replay_results=replay_results,
                        integration_failures=integration_failures,
                        llm_config=llm_config,
                        model_name=model_name,
                        audit_context={
                            **dict(audit_context or {}),
                            "artifact_name": artifact.name,
                        },
                    ),
                    timeout=90,
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "progress": "bundle_builder_fallback",
                        "artifact_name": artifact.name,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            bundle = copy.deepcopy(artifact.bundle)
        # Fall back to a minimal bootstrap case if the LLM returns nothing usable.
        # This keeps the skill testable even when bundle distillation fails.
        if not bundle.all_cases():
            detail = None
            task_id = ""
            if source_task_ids:
                task_id = source_task_ids[0]
                detail = next((item for item in train_details if str(item.get("task_id")) == task_id), None)
            if detail is None and train_details:
                detail = train_details[0]
                task_id = str(detail.get("task_id") or "")
            if detail:
                run = _first_run(detail)
                task_fragment = _task_fragment_from_run(detail, run)
                if task_fragment.get("question"):
                    task_fragment["question"] = task_fragment["question"][:1]
                if task_fragment.get("expected"):
                    task_fragment["expected"] = task_fragment["expected"][:1]
                bundle.positive_cases = [
                    SkillBundleCase(
                        case_id=f"{artifact.name}:positive:0",
                        source="bootstrap_train_fragment",
                        prompt=artifact.description,
                        expected=_result_signature(run),
                        context={
                            "task_id": task_id,
                            "source_task_id": task_id,
                            "task_fragment": task_fragment,
                        },
                        tags=["bootstrap", "train-fragment"],
                        polarity="positive",
                    )
                ]
            else:
                bundle.positive_cases = [
                    SkillBundleCase(
                        case_id=f"{artifact.name}:positive:0",
                        source="artifact_definition",
                        prompt=artifact.description,
                        expected={"injection_type": artifact.injection_type()},
                        context={"artifact_name": artifact.name},
                        tags=["bootstrap"],
                        polarity="positive",
                    )
                ]
        bundle_target = copy.deepcopy(bundle)
        bundle_target_artifact = copy.deepcopy(artifact)
        bundle_target_artifact.bundle = bundle_target
        trim_bundle_cases(bundle_target_artifact)
        dropped = _drop_invalid_bfcl_bundle_cases(bundle_target_artifact)
        bundle = await apply_bundle_text_via_editor(artifact.bundle, bundle_target_artifact.bundle)
        bundle.fixtures = {
            **dict(artifact.bundle.fixtures or {}),
            "bundle_generated_at": _now_iso(),
            "bundle_input_signature": bundle_signature,
            "bundle_reused": False,
            "bundle_maintainer_action": "rebuild" if artifact.bundle.all_cases() else "bootstrap_or_rebuild",
            "bundle_contract_dropped_cases": dropped,
        }
        artifact.bundle = bundle
        artifact.bundle.fixtures = {
            **dict(artifact.bundle.fixtures or {}),
            "bundle_generated_at": bundle.fixtures.get("bundle_generated_at"),
            "bundle_input_signature": bundle_signature,
            "bundle_reused": False,
            "bundle_maintainer_action": str((artifact.bundle.fixtures or {}).get("bundle_maintainer_action") or ("rebuild" if bundle_maintainer_used else "rebuild")),
            "bundle_contract_dropped_cases": list((artifact.bundle.fixtures or {}).get("bundle_contract_dropped_cases") or []),
        }
        return artifact.name, copy.deepcopy(artifact.bundle)

    built = await asyncio.gather(*(build_one(artifact) for artifact in selected))
    by_name = {name: bundle for name, bundle in built}
    for artifact in selected:
        bundle = by_name.get(artifact.name)
        if bundle is not None:
            artifact.bundle = bundle


def _task_fragment_from_run(detail: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    task_snapshot = dict(detail.get("task") or {})
    if task_snapshot.get("question") and task_snapshot.get("expected") is not None:
        return {
            "task_id": task_snapshot.get("task_id") or str(detail.get("task_id") or ""),
            "question": copy.deepcopy(task_snapshot.get("question")),
            "expected": copy.deepcopy(task_snapshot.get("expected")),
            "input_artifacts": copy.deepcopy(task_snapshot.get("input_artifacts") or {}),
            "metadata": copy.deepcopy(task_snapshot.get("metadata") or {}),
        }
    trace = dict(run.get("trace") or {})
    turns = list(trace.get("turns") or [])
    tool_calls = list(trace.get("tool_calls") or [])
    task_id = str(detail.get("task_id") or "")
    if turns:
        question = [copy.deepcopy(item.get("user_messages") or []) for item in turns]
    else:
        question = []
    expected: List[List[str]] = []
    max_turn = max((int(call.get("turn_index", 0)) for call in tool_calls), default=-1)
    for turn_index in range(max_turn + 1):
        expected.append([])
        for call in tool_calls:
            if int(call.get("turn_index", 0)) != turn_index:
                continue
            name = str(call.get("name") or "")
            args = dict(call.get("arguments") or {})
            src = f"{name}({','.join(f'{key}={repr(value)}' for key, value in args.items())})"
            expected[-1].append(src)
    return {
        "task_id": task_id,
        "question": question,
        "expected": expected,
        "input_artifacts": {},
        "metadata": {
            "involved_classes": (run.get("metrics") or {}).get("official_check", {}).get("involved_classes", []),
            "path": [],
        },
    }


def _integration_failures_for_skill(
    artifact: SkillArtifact,
    replay_details: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for item in replay_details:
        run = _first_run(item)
        if not _skill_matches_run(artifact, run):
            continue
        if _official_valid(run) is not False:
            continue
        failures.append(
            {
                "task_id": item.get("task_id"),
                "metrics": copy.deepcopy(run.get("metrics") or {}),
                "trace": copy.deepcopy(run.get("trace") or {}),
                "error": run.get("error"),
            }
        )
    return failures


async def execute_bfcl_bundle_tests(
    artifact: SkillArtifact,
    *,
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    adapter_mode: str,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    max_case_seconds: float = 180.0,
    debug_sink: DebugEventSink | None = None,
) -> SkillTestResult:
    if hasattr(artifact, "metadata"):
        test_signature = _bundle_test_signature(
            artifact,
            max_steps_per_turn=max_steps_per_turn,
            adapter_mode=adapter_mode,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
        )
    else:
        test_signature = ""
    unit_case_runs: List[SkillTestCaseRun] = []
    improved = 0
    regressed = 0
    comparable_case_count = 0
    tokens_delta = 0
    steps_delta = 0
    integration_failures: List[Dict[str, Any]] = []

    with_store = ArtifactStore([copy.deepcopy(artifact)])
    without_store = ArtifactStore([])
    cases = artifact.bundle.all_cases()
    variant_concurrency = max(1, _env_int("BFCL_BUNDLE_VARIANT_CONCURRENCY", 2))
    for case in cases:
        task = _task_from_case(case)
        if task is None:
            unit_case_runs.append(
                SkillTestCaseRun(
                    case_id=case.case_id,
                    variant="bundle_only",
                    passed=True,
                    expected_behavior={
                        "bundle_case_expected": copy.deepcopy(case.expected or {}),
                        "contrast_protocol": copy.deepcopy(case.contrast_protocol or {}),
                        "polarity": case.polarity,
                    },
                    bundle_case_snapshot=case.as_dict(),
                    metadata={"source": case.source, "polarity": case.polarity},
                )
            )
            continue
        invalid_bundle = dict(task.metadata.get("_bundle_case_invalid") or {})
        if invalid_bundle:
            failure_summary = json.dumps(invalid_bundle, ensure_ascii=False)
            for variant in ("without_skill", "with_skill"):
                unit_case_runs.append(
                    SkillTestCaseRun(
                        case_id=case.case_id,
                        variant=variant,
                        passed=False,
                        accuracy=0.0,
                        validity=False,
                        failure_summary=failure_summary,
                        trace_ref=task.task_id,
                        input_payload={
                            "task": task.as_dict(),
                            "variant": variant,
                            "top_k_skills": 0 if variant == "without_skill" else 1,
                            "skill_injection_mode": "none" if variant == "without_skill" else "prompt_only",
                            "llm_test_scope": "single_skill_with_without_counterfactual",
                        },
                        expected_behavior={
                            "bundle_case_expected": copy.deepcopy(case.expected or {}),
                            "task_expected": copy.deepcopy(task.expected or []),
                            "contrast_protocol": copy.deepcopy(case.contrast_protocol or {}),
                            "polarity": case.polarity,
                        },
                        bundle_case_snapshot=case.as_dict(),
                        metadata={
                            "source": case.source,
                            "polarity": case.polarity,
                            "bundle_case_invalid": invalid_bundle,
                            "contract_failures": [invalid_bundle],
                            "contract_passed": False,
                        },
                    )
                )
            comparable_case_count += 1
            regressed += 1
            integration_failures.append(
                {
                    "task_id": task.task_id,
                    "case_id": case.case_id,
                    "error": failure_summary,
                    "contract_failures": [invalid_bundle],
                }
            )
            continue
        without_coro = _run_case_with_timeout(
            task,
            llm_config=llm_config,
            model_name=model_name,
            tools=tools,
            artifact_store=without_store,
            top_k_skills=0,
            max_steps_per_turn=max_steps_per_turn,
            adapter_mode=adapter_mode,
            explicit_skill_tool=explicit_skill_tool,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            skill_injection_mode="none",
            max_case_seconds=max_case_seconds,
            debug_sink=debug_sink.child(
                phase="unit_test_without_skill",
                skill_name=artifact.name,
                case_id=case.case_id,
                variant="without_skill",
            ) if debug_sink else None,
        )
        with_coro = _run_case_with_timeout(
            task,
            llm_config=llm_config,
            model_name=model_name,
            tools=tools,
            artifact_store=with_store,
            top_k_skills=1,
            max_steps_per_turn=max_steps_per_turn,
            adapter_mode=adapter_mode,
            explicit_skill_tool=explicit_skill_tool,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            skill_injection_mode="prompt_only",
            max_case_seconds=max_case_seconds,
            debug_sink=debug_sink.child(
                phase="unit_test_with_skill",
                skill_name=artifact.name,
                case_id=case.case_id,
                variant="with_skill",
            ) if debug_sink else None,
        )
        if variant_concurrency >= 2:
            without_result, with_result = await asyncio.gather(without_coro, with_coro)
        else:
            without_result = await without_coro
            with_result = await with_coro
        before_valid = (without_result.metrics or {}).get("official_valid")
        after_valid = (with_result.metrics or {}).get("official_valid")
        before_f1 = _metrics_float(without_result.as_dict(), "call_f1")
        after_f1 = _metrics_float(with_result.as_dict(), "call_f1")
        before_errors = list((without_result.metrics or {}).get("call_errors") or [])
        after_errors = list((with_result.metrics or {}).get("call_errors") or [])
        before_contract = _run_contract_assertions(case=case, result=without_result, task=task)
        after_contract = _run_contract_assertions(case=case, result=with_result, task=task)
        before_tokens = _metrics_int(without_result.as_dict(), "total_tokens")
        after_tokens = _metrics_int(with_result.as_dict(), "total_tokens")
        before_steps = _metrics_int(without_result.as_dict(), "n_model_steps")
        after_steps = _metrics_int(with_result.as_dict(), "n_model_steps")
        comparable_case_count += 1
        improved_case = (
            (after_contract["passed"] and not before_contract["passed"])
            or (after_valid is True and before_valid is not True)
            or after_f1 > before_f1
            or len(after_errors) < len(before_errors)
        )
        regressed_case = (
            (before_contract["passed"] and not after_contract["passed"])
            or (before_valid is True and after_valid is not True)
            or after_f1 < before_f1
            or len(after_errors) > len(before_errors)
        )
        if improved_case:
            improved += 1
        if regressed_case:
            regressed += 1
        tokens_delta += after_tokens - before_tokens
        steps_delta += after_steps - before_steps
        without_payload = _case_run_payload(
            case=case,
            task=task,
            artifact=artifact,
            result=without_result,
            variant="without_skill",
            top_k_skills=0,
            skill_injection_mode="none",
        )
        with_payload = _case_run_payload(
            case=case,
            task=task,
            artifact=artifact,
            result=with_result,
            variant="with_skill",
            top_k_skills=1,
            skill_injection_mode="prompt_only",
        )
        if debug_sink:
            debug_sink.emit(
                "unit_case_done",
                phase="unit_test",
                skill_name=artifact.name,
                case_id=case.case_id,
                input={
                    "case": case.as_dict(),
                    "task": task.as_dict(),
                    "without_skill": without_payload["input_payload"],
                    "with_skill": with_payload["input_payload"],
                },
                output={
                    "without_skill": without_payload["actual_output"],
                    "with_skill": with_payload["actual_output"],
                    "expected": with_payload["expected_behavior"],
                    "tool_calls_without": without_payload["tool_calls"],
                    "tool_calls_with": with_payload["tool_calls"],
                    "trace_summary_without": without_payload["trace_summary"],
                    "trace_summary_with": with_payload["trace_summary"],
                },
                metrics={
                    "before_valid": before_valid,
                    "after_valid": after_valid,
                    "before_contract_passed": before_contract["passed"],
                    "after_contract_passed": after_contract["passed"],
                    "before_f1": before_f1,
                    "after_f1": after_f1,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "delta_tokens": after_tokens - before_tokens,
                    "before_steps": before_steps,
                    "after_steps": after_steps,
                    "delta_steps": after_steps - before_steps,
                    "improved": improved_case,
                    "regressed": regressed_case,
                },
            )
        unit_case_runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="without_skill",
                passed=before_contract["passed"],
                accuracy=1.0 if before_valid is True else 0.0,
                validity=before_valid,
                tokens=before_tokens,
                steps=before_steps,
                trace_ref=task.task_id,
                trace=copy.deepcopy(without_result.trace or {}),
                input_payload=without_payload["input_payload"],
                expected_behavior=without_payload["expected_behavior"],
                actual_output=without_payload["actual_output"],
                tool_calls=without_payload["tool_calls"],
                trace_summary=without_payload["trace_summary"],
                skill_snapshot=without_payload["skill_snapshot"],
                bundle_case_snapshot=without_payload["bundle_case_snapshot"],
                metadata={
                    "metrics": copy.deepcopy(without_result.metrics or {}),
                    "polarity": case.polarity,
                    "source": case.source,
                    "call_f1": before_f1,
                    "call_errors": before_errors,
                    "contract_passed": before_contract["passed"],
                    "contract_failures": before_contract["failures"],
                },
            )
        )
        unit_case_runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="with_skill",
                passed=after_contract["passed"],
                accuracy=1.0 if after_valid is True else 0.0,
                validity=after_valid,
                tokens=after_tokens,
                steps=after_steps,
                failure_summary="" if after_contract["passed"] else (
                    json.dumps(after_contract["failures"], ensure_ascii=False)
                    if after_contract["failures"]
                    else str(with_result.error or "")
                ),
                trace_ref=task.task_id,
                trace=copy.deepcopy(with_result.trace or {}),
                input_payload=with_payload["input_payload"],
                expected_behavior=with_payload["expected_behavior"],
                actual_output=with_payload["actual_output"],
                tool_calls=with_payload["tool_calls"],
                trace_summary=with_payload["trace_summary"],
                skill_snapshot=with_payload["skill_snapshot"],
                bundle_case_snapshot=with_payload["bundle_case_snapshot"],
                metadata={
                    "metrics": copy.deepcopy(with_result.metrics or {}),
                    "polarity": case.polarity,
                    "source": case.source,
                    "call_f1": after_f1,
                    "call_errors": after_errors,
                    "contract_passed": after_contract["passed"],
                    "contract_failures": after_contract["failures"],
                },
            )
        )
        if after_contract["passed"] is False or after_valid is not True:
            integration_failures.append(
                {
                    "task_id": task.task_id,
                    "case_id": case.case_id,
                    "metrics": copy.deepcopy(with_result.metrics or {}),
                    "trace": copy.deepcopy(with_result.trace or {}),
                    "error": with_result.error,
                    "contract_failures": after_contract["failures"],
                }
            )

    total_cases = max(comparable_case_count, 1)
    strict_failures = [
        {
            "case_id": run.case_id,
            "variant": run.variant,
            "contract_failures": copy.deepcopy(run.metadata.get("contract_failures") or []),
        }
        for run in unit_case_runs
        if run.variant == "with_skill" and not run.passed
    ]
    return SkillTestResult(
        result_id=f"{artifact.name}:bundle:{uuid4().hex[:8]}",
        skill_name=artifact.name,
        skill_version=artifact.version,
        bundle_id=artifact.bundle.bundle_id,
        bundle_version=artifact.bundle.bundle_version,
        dependency_versions=artifact.dependency_version_map(),
        run_label="llm_bundle_unit",
        unit_case_runs=unit_case_runs,
        aggregate={
            "n_cases": len(cases),
            "n_comparable_cases": comparable_case_count,
            "n_improved": improved,
            "n_regressed": regressed,
            "n_strict_failures": len(strict_failures),
            "strict_failures": strict_failures,
            "pass_all_tests": len(strict_failures) == 0 and regressed == 0 and len(integration_failures) == 0,
            "official_valid_driven": False,
            "strict_contract_gate": True,
            "call_errors_are_diagnostic": True,
            "unit_utility_report": {
                "delta_accuracy": round((improved - regressed) / total_cases, 4),
                "delta_tokens": tokens_delta,
                "delta_steps": steps_delta,
            },
            "test_signature": test_signature,
        },
        counterfactual={
            "with_without_delta": {
                "n_improved": improved,
                "n_regressed": regressed,
            }
        },
        integration_failures=integration_failures,
        created_at=_now_iso(),
    )


async def _run_case_with_timeout(
    task: BenchmarkTask,
    *,
    llm_config: str,
    model_name: str | None,
    tools: List[Dict[str, Any]],
    artifact_store: ArtifactStore,
    top_k_skills: int,
    max_steps_per_turn: int,
    adapter_mode: str,
    explicit_skill_tool: bool,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    temperature: float | None,
    synthetic_continue: bool,
    skill_injection_mode: str,
    max_case_seconds: float,
    debug_sink: DebugEventSink | None = None,
):
    try:
        return await asyncio.wait_for(
            run_bfcl_task(
                task,
                llm_config=llm_config,
                model_name=model_name,
                tools=tools,
                artifact_store=artifact_store,
                top_k_skills=top_k_skills,
                max_steps_per_turn=max_steps_per_turn,
                adapter_mode=adapter_mode,
                enable_skill_tool=explicit_skill_tool,
                execution_backend=execution_backend,
                prompt_style=prompt_style,
                temperature=temperature,
                synthetic_continue=synthetic_continue,
                tool_api_style=tool_api_style,
                skill_injection_mode=skill_injection_mode,
                debug_sink=debug_sink,
            ),
            timeout=max_case_seconds,
        )
    except asyncio.TimeoutError:
        from academic.benchmarks.core.types import BenchmarkResult

        return BenchmarkResult(
            benchmark="bfcl_v3",
            task_id=task.task_id,
            success=False,
            score=0.0,
            metrics={
                "official_valid": False,
                "exception": "BundleCaseTimeout",
                "max_case_seconds": max_case_seconds,
                "total_tokens": 0,
                "n_model_steps": 0,
                "retrieved_skills": [],
                "prompt_injected_skills": [],
                "tool_injected_skills": [],
                "called_skill_tools": [],
                "used_skills": [],
                "call_errors": [],
            },
            trace={"task_id": task.task_id, "timed_out": True},
            error=f"Bundle case exceeded {max_case_seconds} seconds",
        )


async def refine_bfcl_skill_store_llm(
    store: ArtifactStore,
    *,
    maintenance_test_results: List[SkillTestResult],
    llm_config: str,
    model_name: str | None = None,
    artifact_names: Sequence[str] | None = None,
    credit_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
    dependency_context_by_skill: Dict[str, List[Dict[str, Any]]] | None = None,
    refiner_rules: Sequence[Dict[str, Any]] | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    results_by_name = {item.skill_name: item for item in maintenance_test_results}
    target_names = set(_unique_names(artifact_names or results_by_name.keys()))
    credit_context_by_skill = credit_context_by_skill or {}
    dependency_context_by_skill = dependency_context_by_skill or {}
    decisions: List[Dict[str, Any]] = []

    for artifact in [item for item in list(store.all()) if item.name in target_names]:
        test_result = results_by_name.get(artifact.name)
        if test_result is None:
            continue
        if artifact.stale:
            stale_due_to = dict(artifact.metadata.get("stale_due_to") or {})
            stale_payload = await resolve_stale_skill_llm(
                artifact,
                upstream_context=stale_due_to,
                llm_config=llm_config,
                model_name=model_name,
                audit_context={
                    **dict(audit_context or {}),
                    "artifact_name": artifact.name,
                },
            )
            updated = await apply_stale_payload_via_editor(artifact, stale_payload)
            if updated.as_dict() != artifact.as_dict():
                store.add(updated)
            decisions.append(
                {
                    "skill_name": artifact.name,
                    "action": stale_payload.get("action", "keep_stale"),
                    "reason": stale_payload.get("reason", ""),
                    "version_before": artifact.version,
                    "version_after": store.get(artifact.name).version if store.get(artifact.name) else artifact.version,
                }
            )
            continue
        if test_result.aggregate.get("pass_all_tests"):
            decisions.append(
                {
                    "skill_name": artifact.name,
                    "action": "keep",
                    "reason": "all current bundle tests pass",
                    "version_before": artifact.version,
                    "version_after": artifact.version,
                }
            )
            continue
        if (
            int(test_result.aggregate.get("n_regressed") or 0) == 0
            and int(test_result.aggregate.get("n_improved") or 0) == 0
            and not list(test_result.integration_failures or [])
        ):
            decisions.append(
                {
                    "skill_name": artifact.name,
                    "action": "keep",
                    "reason": "bundle tests did not pass, but with-skill did not regress relative to without-skill; treat as neutral/no-attribution bundle issue instead of semantic skill failure",
                    "version_before": artifact.version,
                    "version_after": artifact.version,
                    "neutral_failure": True,
                }
            )
            continue
        refinement_history = list(artifact.metadata.get("refinement_history") or [])
        dependency_context = dependency_context_by_skill.get(artifact.name)
        if dependency_context is None:
            dependency_context = _dependency_neighborhood(store, artifact)
        credit_context = list(credit_context_by_skill.get(artifact.name) or [])
        try:
            payload = await refine_skill_artifact_llm(
                artifact,
                test_result=test_result.as_dict(),
                integration_failures=list(test_result.integration_failures or []),
                refinement_history=refinement_history,
                dependency_summaries=dependency_context,
                credit_context=credit_context,
                refiner_rules=refiner_rules,
                llm_config=llm_config,
                model_name=model_name,
                audit_context={
                    **dict(audit_context or {}),
                    "artifact_name": artifact.name,
                },
            )
        except Exception as exc:
            decisions.append(
                {
                    "skill_name": artifact.name,
                    "action": "keep",
                    "reason": f"refiner failed to return valid JSON; preserving current stable artifact: {exc}",
                    "version_before": artifact.version,
                    "version_after": artifact.version,
                    "refiner_error": str(exc),
                }
            )
            continue
        action = str((payload.get("decision") or {}).get("action") or "keep")
        decision_reason = str((payload.get("decision") or {}).get("reason") or "")
        if action == "keep":
            fallback_payload = _fallback_scope_refine_payload_for_strong_harm(
                artifact,
                credit_context=credit_context,
                decision_reason=decision_reason,
            )
            if fallback_payload is not None:
                updated = await apply_refine_payload_via_editor(artifact, fallback_payload)
                updated.metadata["refinement_history"] = refinement_history + [
                    {
                        "test_result_id": test_result.result_id,
                        "decision": copy.deepcopy(fallback_payload.get("decision") or {}),
                        "llm_original_decision": copy.deepcopy(payload.get("decision") or {}),
                    }
                ]
                updated.lineage = SkillLineage(
                    parent_version=artifact.version,
                    parent_version_id=artifact.version_id(),
                    version_kind="minor",
                    migration_reason=str((fallback_payload.get("decision") or {}).get("migration_reason") or ""),
                    refined_from_result_ids=list(artifact.lineage.refined_from_result_ids or []) + [test_result.result_id],
                    refactor_group_id=artifact.lineage.refactor_group_id,
                )
                store.add(updated)
                update_skill_relation_graph(
                    store,
                    refines={updated.name: [artifact.name]},
                )
                decisions.append(
                    {
                        "skill_name": artifact.name,
                        "action": "refine_minor",
                        "reason": str((fallback_payload.get("decision") or {}).get("reason") or ""),
                        "version_before": artifact.version,
                        "version_after": store.get(artifact.name).version if store.get(artifact.name) else artifact.version,
                        "original_action": "keep",
                        "fallback": "strong_harm_scope_guard",
                    }
                )
                continue
            artifact.metadata["last_refine_reason"] = decision_reason or artifact.metadata.get("last_refine_reason", "")
            artifact.metadata["refinement_history"] = refinement_history + [
                {
                    "test_result_id": test_result.result_id,
                    "decision": copy.deepcopy(payload.get("decision") or {}),
                }
            ]
            decisions.append(
                {
                    "skill_name": artifact.name,
                    "action": "keep",
                    "reason": decision_reason,
                    "version_before": artifact.version,
                    "version_after": artifact.version,
                }
            )
            continue
        if action == "rollback":
            rolled_back = store.rollback(artifact.name)
            decisions.append(
                {
                    "skill_name": artifact.name,
                    "action": "rollback" if rolled_back else "keep",
                    "reason": decision_reason,
                    "version_before": artifact.version,
                    "version_after": store.get(artifact.name).version if store.get(artifact.name) else artifact.version,
                }
            )
            continue
        updated = await apply_refine_payload_via_editor(artifact, payload)
        updated.metadata["refinement_history"] = refinement_history + [
            {
                "test_result_id": test_result.result_id,
                "decision": copy.deepcopy(payload.get("decision") or {}),
            }
        ]
        updated.lineage = SkillLineage(
            parent_version=artifact.version,
            parent_version_id=artifact.version_id(),
            version_kind=str((payload.get("decision") or {}).get("version_kind") or updated.lineage.version_kind or "minor"),
            migration_reason=str((payload.get("decision") or {}).get("migration_reason") or updated.lineage.migration_reason or ""),
            refined_from_result_ids=list(artifact.lineage.refined_from_result_ids or []) + [test_result.result_id],
            refactor_group_id=artifact.lineage.refactor_group_id,
        )
        store.add(updated)
        update_skill_relation_graph(
            store,
            refines={updated.name: [artifact.name]},
        )
        decisions.append(
            {
                "skill_name": artifact.name,
                "action": action,
                "reason": decision_reason,
                "version_before": artifact.version,
                "version_after": store.get(artifact.name).version if store.get(artifact.name) else artifact.version,
            }
        )
    return decisions


async def run_bfcl_overlap_refactor_llm(
    store: ArtifactStore,
    *,
    train_details: List[Dict[str, Any]],
    segment_embeddings: Dict[str, List[float]] | None = None,
    overlap_state: OverlapGraphState | None = None,
    new_segments: List[TraceSegment] | None = None,
    exclude_segment_sets: set[tuple[str, ...]] | None = None,
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    adapter_mode: str,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    max_case_seconds: float = 180.0,
    max_repair_rounds: int = 1,
    refactorer_rules: Sequence[Dict[str, Any]] | None = None,
    debug_sink: DebugEventSink | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run overlap-segment discovery plus test-gated shared-skill refactor."""
    if overlap_state is not None:
        resolved_new_segments = list(new_segments or [])
        skill_segments = [
            segment
            for segment in _skill_overlap_segments(store)
            if segment.segment_id not in set(overlap_state.segment_ids)
        ]
        graph_new_segments = [*resolved_new_segments, *skill_segments]
        resolved_segment_embeddings = segment_embeddings if segment_embeddings is not None else _segment_embedding_map(resolved_new_segments)
        if skill_segments:
            resolved_segment_embeddings = {
                **dict(resolved_segment_embeddings or {}),
                **_segment_embedding_map(skill_segments),
            }
        update_overlap_graph_state(
            overlap_state,
            new_segments=graph_new_segments,
            segment_embeddings=resolved_segment_embeddings,
        )
        graph = materialize_overlap_graph(overlap_state)
        segments = list(graph.segments)
    else:
        trace_segments = bfcl_trace_segments_from_details(train_details)
        skill_segments = _skill_overlap_segments(store)
        segments = [*trace_segments, *skill_segments]
        resolved_new_segments = list(new_segments or segments)
        resolved_segment_embeddings = segment_embeddings if segment_embeddings is not None else _segment_embedding_map(segments)
        if skill_segments and segment_embeddings is not None:
            resolved_segment_embeddings = {
                **dict(resolved_segment_embeddings or {}),
                **_segment_embedding_map(skill_segments),
            }
        graph = discover_overlap_graph(segments, segment_embeddings=resolved_segment_embeddings)
    cliques = find_refactor_cliques(graph)
    new_trace_segment_ids = {
        segment.segment_id
        for segment in resolved_new_segments
        if not str(segment.kind or "").endswith("skill")
    }
    if new_trace_segment_ids:
        cliques = [
            clique
            for clique in cliques
            if set(clique.segment_ids) & new_trace_segment_ids
        ]
    excluded = exclude_segment_sets or set()
    if excluded:
        cliques = [
            clique
            for clique in cliques
            if tuple(sorted(clique.segment_ids)) not in excluded
        ]
    report: Dict[str, Any] = {
        "segments": [item.as_dict() for item in segments],
        "overlap_graph": graph.as_dict(),
        "segment_embedding_stats": {
            "n_segments": len(segments),
            "n_embedded_segments": len(resolved_segment_embeddings),
            "embedding_enabled": bool(resolved_segment_embeddings),
        },
        "timeline": build_bfcl_overlap_timeline(train_details, store=store),
        "cliques": [item.as_dict() for item in cliques],
        "attempts": [],
        "commits": [],
        "rejections": [],
    }
    if debug_sink:
        debug_sink.emit(
            "overlap_graph_built",
            phase="refactor_overlap",
            input={"n_train_details": len(train_details)},
            output={
                "n_segments": len(segments),
                "n_edges": len(graph.edges),
                "n_cliques": len(cliques),
                "overlap_graph": graph.as_dict(),
                "store_state": _store_brief_for_refactor(store),
            },
        )
    if not cliques:
        report["refactor_segment_coverage"] = _refactor_segment_coverage(
            new_segments=resolved_new_segments,
            cliques=[],
            attempts=[],
            excluded=excluded,
        )
        return report

    max_attempts = int(os.environ.get("BFCL_REFACTOR_MAX_CLIQUES", "3") or "3")
    new_segment_ids = {segment.segment_id for segment in resolved_new_segments}
    prioritized_cliques = [
        clique for clique in cliques if not new_segment_ids or (set(clique.segment_ids) & new_segment_ids)
    ]
    skipped_cliques = prioritized_cliques[max_attempts:] if len(prioritized_cliques) > max_attempts else []
    for clique in prioritized_cliques[:max_attempts]:
        group_id = f"bfcl_refactor_{int(time.time())}_{clique.clique_id}"
        selected_segment_ids = set(clique.segment_ids)
        selected_segments = [item.as_dict() for item in graph.segments if item.segment_id in selected_segment_ids]
        repair_context: Dict[str, Any] | None = None
        committable_shared: SkillArtifact | None = None
        committable_updates: List[SkillArtifact] = []
        committable_test_results: List[SkillTestResult] = []
        committable_attempt: Dict[str, Any] | None = None
        for repair_round in range(max(1, max_repair_rounds + 1)):
            try:
                payload = await llm_refactor_clique(
                    clique=clique,
                    graph=graph,
                    existing_skills=store.all(),
                    llm_config=llm_config,
                    model_name=model_name,
                    audit_context={**dict(audit_context or {}), "refactor_group_id": group_id, "repair_round": repair_round},
                    repair_context=repair_context,
                    refactorer_rules=refactorer_rules,
                )
            except Exception as exc:
                attempt = {
                    "group_id": group_id,
                    "repair_round": repair_round,
                    "clique": clique.as_dict(),
                    "segments": selected_segments,
                    "status": "rejected",
                    "reason": f"LLM refactor call failed: {type(exc).__name__}: {exc}",
                    "error_type": type(exc).__name__,
                }
                report["attempts"].append(attempt)
                report["rejections"].append(attempt)
                if debug_sink:
                    debug_sink.emit(
                        "refactor_commit_rejected",
                        phase="refactor_overlap",
                        input={"clique": clique.as_dict(), "segments": selected_segments, "repair_round": repair_round},
                        output=attempt,
                    )
                break
            attempt: Dict[str, Any] = {
                "group_id": group_id,
                "repair_round": repair_round,
                "clique": clique.as_dict(),
                "segments": selected_segments,
                "llm_payload": copy.deepcopy(payload),
            }
            if debug_sink:
                debug_sink.emit(
                    "refactor_llm_done",
                    phase="refactor_overlap",
                    input={
                        "clique": clique.as_dict(),
                        "segments": selected_segments,
                        "store_before": _store_brief_for_refactor(store),
                        "repair_context": copy.deepcopy(repair_context),
                        "repair_round": repair_round,
                    },
                    output=payload,
                )
            shared = artifact_from_refactor_payload(payload, group_id=group_id)
            if shared is None:
                attempt.update({"status": "rejected", "reason": "LLM rejected or returned no shared skill"})
                report["attempts"].append(attempt)
                report["rejections"].append(attempt)
                break

            source_task_ids = sorted({str(item.get("task_id") or "") for item in selected_segments if str(item.get("task_id") or "")})
            shared.metadata["source_task_ids"] = source_task_ids
            shared.metadata["source_segments"] = list(clique.segment_ids)
            shared.metadata["overlap_edges"] = [edge.as_dict() for edge in clique.edges]
            inferred_domains = _domains_from_segment_dicts(selected_segments)
            existing_domains = [
                str(item).strip()
                for item in (shared.metadata.get("domains") or [])
                if str(item).strip() and str(item).strip().lower() != "all"
            ]
            if inferred_domains:
                shared.metadata["domains"] = sorted(set(existing_domains + inferred_domains))
                shared.metadata["domains_inferred_from_refactor_segments"] = True

            existing_by_name = {skill.name: skill for skill in store.all()}
            updates = apply_affected_skill_updates(
                payload,
                existing_by_name=existing_by_name,
                shared_skill=shared,
                group_id=group_id,
            )
            candidate_store = ArtifactStore(store.all(), test_results=store.test_results())
            candidate_store.add(shared)
            for update in updates:
                candidate_store.add(update)

            await build_bfcl_skill_bundles_llm(
                candidate_store,
                train_details=train_details,
                replay_details=[],
                llm_config=llm_config,
                model_name=model_name,
                artifact_names=[shared.name],
                audit_context={**dict(audit_context or {}), "phase": "refactor_bundle", "refactor_group_id": group_id, "repair_round": repair_round},
            )
            test_targets = [shared.name] + [item.name for item in updates if item.status != "archived"]
            test_results: List[SkillTestResult] = []
            failed_results: List[SkillTestResult] = []
            for name in test_targets:
                artifact = candidate_store.get(name)
                if artifact is None:
                    continue
                test_signature = _bundle_test_signature(
                    artifact,
                    max_steps_per_turn=max_steps_per_turn,
                    adapter_mode=adapter_mode,
                    execution_backend=execution_backend,
                    prompt_style=prompt_style,
                    tool_api_style=tool_api_style,
                    synthetic_continue=synthetic_continue,
                    explicit_skill_tool=explicit_skill_tool,
                )
                test_result = _latest_matching_test_result(
                    candidate_store,
                    artifact=artifact,
                    test_signature=test_signature,
                )
                if test_result is None:
                    test_result = await execute_bfcl_bundle_tests(
                        artifact,
                        tools=tools,
                        llm_config=llm_config,
                        model_name=model_name,
                        adapter_mode=adapter_mode,
                        execution_backend=execution_backend,
                        prompt_style=prompt_style,
                        tool_api_style=tool_api_style,
                        max_steps_per_turn=max_steps_per_turn,
                        temperature=temperature,
                        synthetic_continue=synthetic_continue,
                        explicit_skill_tool=explicit_skill_tool,
                        max_case_seconds=max_case_seconds,
                        debug_sink=debug_sink.child(
                            phase="refactor_unit_test",
                            skill_name=name,
                            refactor_group_id=group_id,
                            repair_round=repair_round,
                        ) if debug_sink else None,
                    )
                else:
                    test_result.aggregate = {**dict(test_result.aggregate or {}), "cached_reuse": True}
                if not test_result.aggregate.get("cached_reuse"):
                    candidate_store.add_test_result(test_result)
                test_results.append(test_result)
                if not bool(test_result.aggregate.get("pass_all_tests")):
                    failed_results.append(test_result)

            attempt["shared_skill"] = shared.as_dict()
            attempt["affected_updates"] = [item.as_dict() for item in updates]
            attempt["test_results"] = [item.as_dict() for item in test_results]
            if failed_results:
                attempt.update(
                    {
                        "status": "rejected",
                        "reason": "refactor bundle gate failed",
                        "failed_skills": [item.skill_name for item in failed_results],
                    }
                )
                report["attempts"].append(attempt)
                report["rejections"].append(attempt)
                repair_context = {
                    "failed_attempt": {
                        "decision": copy.deepcopy(payload.get("decision") or {}),
                        "shared_skill": copy.deepcopy(payload.get("shared_skill") or {}),
                        "affected_skill_updates": copy.deepcopy(payload.get("affected_skill_updates") or []),
                    },
                    "failed_test_results": [item.as_dict() for item in failed_results],
                    "all_test_results": [item.as_dict() for item in test_results],
                }
                if debug_sink:
                    debug_sink.emit("refactor_commit_rejected", phase="refactor_overlap", output=attempt)
                if repair_round < max_repair_rounds:
                    continue
                break
            committable_shared = shared
            committable_updates = updates
            committable_test_results = test_results
            committable_attempt = attempt
            break
        if committable_shared is None or committable_attempt is None:
            continue

        store.add(committable_shared)
        committed_names = [committable_shared.name]
        for update in committable_updates:
            store.add(update)
            committed_names.append(update.name)
        update_skill_relation_graph(
            store,
            calls={
                update.name: [committable_shared.name]
                for update in committable_updates
                if update.status != "archived"
            },
            derived_from={
                committable_shared.name: [
                    str(segment.get("segment_id") or "")
                    for segment in selected_segments
                    if str(segment.get("segment_id") or "")
                ]
            },
            refines={
                update.name: [committable_shared.name]
                for update in committable_updates
                if update.status != "archived"
            },
        )
        validate_skill_static_dependencies(store, committed_names)
        for test_result in committable_test_results:
            store.add_test_result(test_result)
        committable_attempt.update(
            {
                "status": "committed",
                "committed_names": committed_names,
                "store_after": _store_brief_for_refactor(store),
            }
        )
        report["attempts"].append(committable_attempt)
        report["commits"].append(committable_attempt)
        if debug_sink:
            debug_sink.emit(
                "refactor_commit_done",
                phase="refactor_overlap",
                input={"group_id": group_id, "committed_names": committed_names},
                output=committable_attempt,
            )
        break
    for clique in skipped_cliques:
        report["attempts"].append(
            {
                "group_id": "",
                "repair_round": None,
                "clique": clique.as_dict(),
                "segments": [
                    item.as_dict()
                    for item in graph.segments
                    if item.segment_id in set(clique.segment_ids)
                ],
                "status": "deferred",
                "reason": "covered_by_refactor_budget_tier_no_llm_call",
            }
        )
    report["refactor_segment_coverage"] = _refactor_segment_coverage(
        new_segments=resolved_new_segments,
        cliques=cliques,
        attempts=report["attempts"],
        excluded=excluded,
    )
    return report


def _refactor_segment_coverage(
    *,
    new_segments: Sequence[TraceSegment],
    cliques: Sequence[Any],
    attempts: Sequence[Dict[str, Any]],
    excluded: set[tuple[str, ...]],
) -> List[Dict[str, Any]]:
    by_segment: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for clique in cliques:
        clique_ids = tuple(sorted(str(item) for item in (clique.segment_ids or []) if str(item)))
        excluded_reason = clique_ids in excluded
        for segment_id in clique_ids:
            by_segment[segment_id].append(
                {
                    "clique_id": clique.clique_id,
                    "segment_ids": list(clique_ids),
                    "edge_weight_sum": clique.edge_weight_sum,
                    "excluded_as_seen": excluded_reason,
                }
            )
    action_by_segment: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts:
        clique = dict(attempt.get("clique") or {})
        status = str(attempt.get("status") or "")
        if status == "committed":
            action = "extract_shared"
        elif status == "deferred":
            action = "defer"
        elif status == "rejected":
            action = "noop"
        else:
            action = "noop"
        for segment_id in clique.get("segment_ids") or []:
            action_by_segment[str(segment_id)] = {
                "action": action,
                "status": status or "not_attempted",
                "group_id": attempt.get("group_id"),
                "reason": attempt.get("reason") or ((attempt.get("llm_payload") or {}).get("decision") or {}).get("reason", ""),
            }
    coverage: List[Dict[str, Any]] = []
    for segment in new_segments:
        action = action_by_segment.get(segment.segment_id) or {
            "action": "noop" if by_segment.get(segment.segment_id) else "defer",
            "status": "covered_no_llm_candidate" if by_segment.get(segment.segment_id) else "no_candidate_group",
            "group_id": "",
            "reason": "no qualifying refactor clique" if not by_segment.get(segment.segment_id) else "candidate group did not need LLM commit",
        }
        coverage.append(
            {
                "segment_id": segment.segment_id,
                "task_id": segment.task_id,
                "turn_index": segment.turn_index,
                "candidate_groups": by_segment.get(segment.segment_id, []),
                **action,
            }
        )
    return coverage


def _store_brief_for_refactor(store: ArtifactStore) -> Dict[str, Any]:
    return {
        "n_total": len(store.all()),
        "skills": [
            {
                "name": skill.name,
                "version": skill.version,
                "status": skill.status,
                "stale": skill.stale,
                "version_kind": skill.version_kind(),
                "dependencies": list(skill.dependencies or []),
                "bundle_cases": len(skill.bundle.all_cases()),
                "description": skill.description,
            }
            for skill in store.all()
        ],
    }


def build_bfcl_overlap_timeline(
    train_details: List[Dict[str, Any]],
    *,
    store: ArtifactStore | None = None,
) -> List[Dict[str, Any]]:
    """Record how the trace-segment overlap graph grows after each task.

    The refactor algorithm uses the final graph for candidate selection, but
    the paper/debug UI needs to show the online evidence accumulation process.
    Each frame is a prefix graph after one more train task has completed.
    """

    frames: List[Dict[str, Any]] = []
    prefix: List[Dict[str, Any]] = []
    for idx, detail in enumerate(train_details):
        prefix.append(detail)
        segments = bfcl_trace_segments_from_details(prefix)
        graph = discover_overlap_graph(segments)
        cliques = find_refactor_cliques(graph)
        frames.append(
            {
                "frame_index": idx,
                "event_type": "task_overlap_graph_updated",
                "task_id": detail.get("task_id"),
                "n_tasks_seen": len(prefix),
                "n_segments": len(segments),
                "n_edges": len(graph.edges),
                "n_cliques": len(cliques),
                "segments": [item.as_dict() for item in segments],
                "overlap_graph": graph.as_dict(),
                "cliques": [item.as_dict() for item in cliques],
                "store_state": _store_brief_for_refactor(store) if store is not None else {},
            }
        )
    return frames


def append_failure_cases_from_result(
    artifact: SkillArtifact,
    test_result: SkillTestResult,
) -> int:
    added = 0
    existing = {case.case_id for case in artifact.bundle.integration_cases}
    for failure in test_result.integration_failures:
        task_id = str(failure.get("task_id") or "").strip()
        case_id = str(failure.get("case_id") or f"{artifact.name}:integration:{task_id or added}")
        if not case_id or case_id in existing:
            continue
        trace = dict(failure.get("trace") or {})
        question = [copy.deepcopy(item.get("user_messages") or []) for item in (trace.get("turns") or [])]
        expected: List[List[str]] = []
        for turn in trace.get("turns") or []:
            turn_calls = []
            for call in (turn.get("tool_calls") or []):
                name = str(call.get("name") or "")
                args = dict(call.get("arguments") or {})
                turn_calls.append(f"{name}({','.join(f'{k}={repr(v)}' for k, v in args.items())})")
            expected.append(turn_calls)
        artifact.bundle.integration_cases.append(
            SkillBundleCase(
                case_id=case_id,
                source="integration_failure",
                prompt=f"Integration failure for {artifact.name}",
                expected={"official_valid": False},
                context={
                    "task_id": task_id,
                    "source_task_id": task_id,
                    "failure": copy.deepcopy(failure),
                    "task_fragment": {
                        "task_id": task_id,
                        "question": question,
                        "expected": expected,
                        "input_artifacts": {},
                        "metadata": {},
                    },
                },
                tags=["integration-derived", "failure"],
                polarity="negative",
            )
        )
        existing.add(case_id)
        added += 1
    if added:
        artifact.bundle.bundle_version += 1
    return added


def trim_bundle_cases(
    artifact: SkillArtifact,
    *,
    per_polarity_limit: int | None = None,
) -> bool:
    changed = trim_bundle_cases_to_budget(
        artifact,
        per_polarity_limit=per_polarity_limit or _bundle_case_limit_per_polarity(),
        total_limit=_bundle_max_total_cases(),
    )
    if changed:
        artifact.bundle.bundle_version = max(int(artifact.bundle.bundle_version or 1), 1) + 1
    return changed


def summarize_case_metrics(result: SkillTestResult) -> Dict[str, Any]:
    return {
        "skill_name": result.skill_name,
        "skill_version": result.skill_version,
        "bundle_version": result.bundle_version,
        "aggregate": copy.deepcopy(result.aggregate),
        "integration_failures": len(result.integration_failures or []),
    }


def select_bfcl_maintenance_targets(
    store: ArtifactStore,
    *,
    train_details: List[Dict[str, Any]] | None = None,
    replay_details: List[Dict[str, Any]] | None = None,
) -> List[str]:
    train_task_ids = {
        str(item.get("task_id") or "").strip()
        for item in (train_details or [])
        if str(item.get("task_id") or "").strip()
    }
    replay_runs = [_first_run(item) for item in (replay_details or [])]
    target_names: List[str] = []
    for artifact in store.all():
        source_task_ids = {
            str(item).strip()
            for item in (artifact.metadata.get("source_task_ids") or [])
            if str(item).strip()
        }
        source = str(artifact.metadata.get("source") or "").strip().lower()
        if artifact.stale or artifact.metadata.get("manual_fault_injected"):
            target_names.append(artifact.name)
            continue
        if artifact.history:
            target_names.append(artifact.name)
            continue
        if source in {"llm_trace_extraction", "evolve_rollouts", "manual_fault_probe"}:
            target_names.append(artifact.name)
            continue
        if source_task_ids & train_task_ids:
            target_names.append(artifact.name)
            continue
        if any(_skill_matches_run(artifact, run) for run in replay_runs):
            target_names.append(artifact.name)
            continue
    return sorted(set(target_names))
