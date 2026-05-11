"""BFCL adapter for the benchmark-agnostic LLM skill-maintenance loop."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple
from uuid import uuid4

from academic.benchmarks.artifacts import ArtifactStore
from academic.benchmarks.bfcl import run_bfcl_task
from academic.benchmarks.types import (
    BenchmarkTask,
    SkillArtifact,
    SkillBundleCase,
    SkillLineage,
    SkillTestCaseRun,
    SkillTestResult,
)
from academic.skill_repository.llm_maintenance import (
    apply_refine_payload,
    apply_stale_payload,
    distill_skill_bundle_llm,
    extract_skill_artifacts_from_results_llm,
    refine_skill_artifact_llm,
    resolve_stale_skill_llm,
    summarize_dependency_context,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or str(default))
    except Exception:
        return default


def _maintenance_concurrency() -> int:
    return max(1, _env_int("BFCL_MAINTENANCE_CONCURRENCY", 2))


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
    if not input_artifacts or not metadata.get("involved_classes"):
        source_task = _source_task_snapshot(task_id)
        if source_task:
            input_artifacts = input_artifacts or copy.deepcopy(source_task.get("input_artifacts") or {})
            metadata = {**copy.deepcopy(source_task.get("metadata") or {}), **metadata}
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


async def extract_bfcl_skill_artifacts_llm(
    results: List[Dict[str, Any]],
    *,
    tool_schemas: Iterable[Dict[str, Any]] | None = None,
    existing_artifacts: Iterable[SkillArtifact] | None = None,
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
        llm_config=llm_config,
        model_name=model_name,
        audit_context=audit_context,
    )
    for artifact in artifacts:
        artifact.metadata.setdefault("benchmark", "bfcl_v3")
        artifact.metadata.setdefault("source", "llm_trace_extraction")
        artifact.metadata.setdefault("injection_type", artifact.injection_type())
        artifact.metadata["allowed_tools"] = list(sorted(set(
            [str(item).strip() for item in (artifact.metadata.get("allowed_tools") or []) if str(item).strip()]
            + list(relevant_tools)
        )))
        artifact.metadata["domains"] = list(sorted(set(
            [str(item).strip() for item in (artifact.metadata.get("domains") or []) if str(item).strip()]
            + list(inferred_domains or ["all"])
        )))
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
        integration_failures = _integration_failures_for_skill(artifact, replay_details or [])
        source_task_ids = [
            str(item).strip()
            for item in (artifact.metadata.get("source_task_ids") or [])
            if str(item).strip()
        ]
        artifact_source = str(artifact.metadata.get("source") or "").strip().lower()
        should_use_llm_bundle = bool(
            source_task_ids
            or integration_failures
            or artifact_source in {"llm_trace_extraction", "evolve_rollouts", "manual_fault_probe"}
            or artifact.metadata.get("manual_fault_injected")
        )
        if not should_use_llm_bundle:
            bundle = copy.deepcopy(artifact.bundle)
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
            artifact.bundle.fixtures = {
                **dict(artifact.bundle.fixtures or {}),
                "bundle_generated_at": _now_iso(),
            }
            return artifact.name, bundle
        try:
            async with sem:
                bundle = await asyncio.wait_for(
                    distill_skill_bundle_llm(
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
        bundle.fixtures = {
            **dict(artifact.bundle.fixtures or {}),
            "bundle_generated_at": _now_iso(),
        }
        return artifact.name, bundle

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
) -> SkillTestResult:
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
    # First implementation bias: keep maintenance execution lightweight and
    # bounded even when the distilled bundle is still too broad.
    cases = cases[:1] if len(cases) > 1 else cases
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
        without_result = await _run_case_with_timeout(
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
        )
        with_result = await _run_case_with_timeout(
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
        )
        before_valid = (without_result.metrics or {}).get("official_valid")
        after_valid = (with_result.metrics or {}).get("official_valid")
        before_f1 = _metrics_float(without_result.as_dict(), "call_f1")
        after_f1 = _metrics_float(with_result.as_dict(), "call_f1")
        before_errors = list((without_result.metrics or {}).get("call_errors") or [])
        after_errors = list((with_result.metrics or {}).get("call_errors") or [])
        before_tokens = _metrics_int(without_result.as_dict(), "total_tokens")
        after_tokens = _metrics_int(with_result.as_dict(), "total_tokens")
        before_steps = _metrics_int(without_result.as_dict(), "n_model_steps")
        after_steps = _metrics_int(with_result.as_dict(), "n_model_steps")
        comparable_case_count += 1
        improved_case = (
            (after_valid is True and before_valid is not True)
            or after_f1 > before_f1
            or len(after_errors) < len(before_errors)
        )
        regressed_case = (
            (before_valid is True and after_valid is not True)
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
        unit_case_runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="without_skill",
                passed=before_valid is True,
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
                },
            )
        )
        unit_case_runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="with_skill",
                passed=after_valid is True,
                accuracy=1.0 if after_valid is True else 0.0,
                validity=after_valid,
                tokens=after_tokens,
                steps=after_steps,
                failure_summary="" if after_valid is True else str(with_result.error or ""),
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
                },
            )
        )
        if after_valid is not True:
            integration_failures.append(
                {
                    "task_id": task.task_id,
                    "case_id": case.case_id,
                    "metrics": copy.deepcopy(with_result.metrics or {}),
                    "trace": copy.deepcopy(with_result.trace or {}),
                    "error": with_result.error,
                }
            )

    total_cases = max(comparable_case_count, 1)
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
            "pass_all_tests": regressed == 0 and len(integration_failures) == 0,
            "official_valid_driven": True,
            "call_errors_are_diagnostic": True,
            "unit_utility_report": {
                "delta_accuracy": round((improved - regressed) / total_cases, 4),
                "delta_tokens": tokens_delta,
                "delta_steps": steps_delta,
            },
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
            ),
            timeout=max_case_seconds,
        )
    except asyncio.TimeoutError:
        from academic.benchmarks.types import BenchmarkResult

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
    audit_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    results_by_name = {item.skill_name: item for item in maintenance_test_results}
    dependency_context = summarize_dependency_context(store.all())
    decisions: List[Dict[str, Any]] = []

    for artifact in list(store.all()):
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
            updated = apply_stale_payload(artifact, stale_payload)
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
        try:
            payload = await refine_skill_artifact_llm(
                artifact,
                test_result=test_result.as_dict(),
                integration_failures=list(test_result.integration_failures or []),
                refinement_history=refinement_history,
                dependency_summaries=dependency_context,
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
        updated = apply_refine_payload(artifact, payload)
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
