"""Compact SpreadsheetBench projections used by maintenance agents."""
from __future__ import annotations

from typing import Any, Dict, List

from academic.benchmarks.core.credit_scope import skill_exposure_flags, skill_exposure_from_mappings
from academic.benchmarks.core.types import SkillArtifact


def spreadsheet_trace_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs") or []
    first = runs[0] if runs else {}
    metrics = first.get("metrics") or {}
    trace = first.get("trace") or {}
    exposure = skill_exposure_from_mappings(metrics, trace)
    return {
        "task_id": detail.get("task_id"),
        "success": first.get("success"),
        "score": first.get("score"),
        "answer_sheet": metrics.get("answer_sheet"),
        "answer_position": metrics.get("answer_position"),
        "checked_cells": metrics.get("checked_cells"),
        "mismatched_cells": metrics.get("mismatched_cells", [])[:5],
        "execution_ok": metrics.get("execution_ok"),
        "llm_api_style": metrics.get("llm_api_style"),
        "retrieved_skills": exposure["retrieved_skills"],
        "prompt_injected_skills": exposure["prompt_injected_skills"],
        "callable_skills": metrics.get("callable_skills") or trace.get("callable_skills") or [],
        "called_skill_functions": exposure["called_skill_functions"],
        "retrieved_only_skills": exposure["retrieved_only_skills"],
        "stderr_tail": str(trace.get("stderr") or "")[-800:],
    }


def spreadsheet_task_fragment(detail: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(detail.get("task") or {})
    expected = dict(task.get("expected") or {})
    metadata = dict(task.get("metadata") or {})
    return {
        "benchmark": task.get("benchmark") or "spreadsheet",
        "task_id": task.get("task_id") or detail.get("task_id"),
        "question": task.get("question"),
        "expected": {
            "answer_sheet": expected.get("answer_sheet"),
            "answer_position": expected.get("answer_position"),
            "golden_xlsx": expected.get("golden_xlsx"),
        },
        "input_artifacts": {
            "input_xlsx": (task.get("input_artifacts") or {}).get("input_xlsx"),
            "prompt_txt_preview": str((task.get("input_artifacts") or {}).get("prompt_txt") or "")[:1200],
        },
        "metadata": {
            "instruction_type": metadata.get("instruction_type"),
            "data_position": metadata.get("data_position"),
            "spreadsheet_path": metadata.get("spreadsheet_path"),
        },
    }


def spreadsheet_code_snippet(code: str, limit: int = 1600) -> str:
    text = str(code or "").strip()
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    kept: List[str] = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > limit:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(kept).rstrip() + "\n# ... truncated ..."


def spreadsheet_result_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    run = dict((detail.get("runs") or [{}])[0] or {})
    trace = dict(run.get("trace") or {})
    metrics = dict(run.get("metrics") or {})
    return {
        "task": spreadsheet_task_fragment(detail),
        "success": run.get("success"),
        "score": run.get("score"),
        "metrics": {
            "answer_sheet": metrics.get("answer_sheet"),
            "answer_position": metrics.get("answer_position"),
            "cell_accuracy": metrics.get("cell_accuracy"),
            "checked_cells": metrics.get("checked_cells"),
            "mismatched_cells": (metrics.get("mismatched_cells") or [])[:8],
            "execution_ok": metrics.get("execution_ok"),
            "returncode": metrics.get("returncode"),
            "total_tokens": metrics.get("total_tokens"),
        },
        "trace": {
            "retrieved_skills": trace.get("retrieved_skills") or metrics.get("retrieved_skills") or [],
            "code_snippet": spreadsheet_code_snippet(trace.get("code") or ""),
            "stderr_tail": str(trace.get("stderr") or "")[-1200:],
            "stdout_tail": str(trace.get("stdout") or "")[-800:],
        },
    }


def spreadsheet_skill_projection(
    artifact: SkillArtifact,
    *,
    projection: Dict[str, Any],
) -> Dict[str, Any]:
    exposure = skill_exposure_flags(artifact.name, projection)
    return {
        "skill_name": artifact.name,
        "version": artifact.version,
        "kind": artifact.kind,
        "status": artifact.status,
        "description": artifact.description,
        "body": artifact.body[:1800],
        "interface": artifact.interface.as_dict(),
        "metadata": {
            "domains": artifact.metadata.get("domains") or [],
            "allowed_tools": artifact.metadata.get("allowed_tools") or [],
            "intent_keywords": artifact.metadata.get("intent_keywords") or [],
            "source_task_ids": artifact.metadata.get("source_task_ids") or [],
            "non_applicability": artifact.metadata.get("non_applicability"),
        },
        "retrieved": exposure["retrieved"],
        "injected": exposure["injected"],
        "used": exposure["used"],
        "usage_count": artifact.usage_count,
        "success_count": artifact.success_count,
        "recent_helpful": artifact.evidence.helpful_cases[-3:],
        "recent_harmful": artifact.evidence.harmful_cases[-3:],
    }
