"""Compact SpreadsheetBench projections used by maintenance agents."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from academic.benchmarks.core.credit_scope import skill_exposure_flags, skill_exposure_from_mappings
from academic.benchmarks.core.types import SkillArtifact


def spreadsheet_trace_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs") or []
    first = runs[0] if runs else {}
    metrics = first.get("metrics") or {}
    trace = first.get("trace") or {}
    exposure = skill_exposure_from_mappings(metrics, trace)
    notebook_steps = spreadsheet_notebook_steps_projection(trace)
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
        "skill_code_reads": metrics.get("skill_code_reads") or trace.get("skill_code_reads") or [],
        "retrieved_only_skills": exposure["retrieved_only_skills"],
        "stderr_tail": compact_spreadsheet_stderr(trace.get("stderr") or "", limit=800),
        "notebook_step_count": len(notebook_steps),
        "notebook_steps": notebook_steps,
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


def spreadsheet_notebook_steps_projection(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for index, turn in enumerate(trace.get("notebook_turns") or []):
        if not isinstance(turn, dict):
            steps.append(
                {
                    "code_snippet": "",
                    "stdout_tail": "",
                    "stderr_tail": _tail_text(turn, limit=1000),
                    "exception": "",
                }
            )
            continue
        stderr = str(turn.get("stderr") or "")
        step = {
            "code_snippet": spreadsheet_code_snippet(turn.get("code") or "", limit=1200),
            "stdout_tail": _tail_text(turn.get("stdout") or "", limit=600),
            "stderr_tail": compact_spreadsheet_stderr(stderr, limit=1000),
            "exception": _python_exception_line(stderr),
        }
        steps.append(step)
    return steps


def compact_spreadsheet_stderr(stderr: Any, *, limit: int = 1200) -> str:
    text = str(stderr or "").strip()
    if len(text) <= limit:
        return text
    traceback_start = text.rfind("Traceback (most recent call last):")
    traceback_text = text[traceback_start:] if traceback_start >= 0 else text
    lines = traceback_text.splitlines()
    frame_indices = [i for i, line in enumerate(lines) if line.lstrip().startswith("File ")]
    if frame_indices:
        final_frame_start = frame_indices[-1]
        snippet = "\n".join(lines[final_frame_start:]).strip()
        if len(snippet) <= limit:
            return "[stderr truncated to final traceback frame]\n" + snippet
        return "[stderr truncated to final traceback frame]\n" + _tail_text(snippet, limit=limit)
    return _tail_text(text, limit=limit)


def _python_exception_line(stderr: Any) -> str:
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning|Exit|Interrupt)\b", line):
            return line
    return ""


def _tail_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def spreadsheet_result_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    run = dict((detail.get("runs") or [{}])[0] or {})
    trace = dict(run.get("trace") or {})
    metrics = dict(run.get("metrics") or {})
    notebook_steps = spreadsheet_notebook_steps_projection(trace)
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
            "stderr_tail": compact_spreadsheet_stderr(trace.get("stderr") or "", limit=1200),
            "stdout_tail": _tail_text(trace.get("stdout") or "", limit=800),
            "skill_code_reads": trace.get("skill_code_reads") or metrics.get("skill_code_reads") or [],
            "notebook_step_count": len(notebook_steps),
            "notebook_steps": notebook_steps,
        },
    }


def spreadsheet_skill_projection(
    artifact: SkillArtifact,
    *,
    projection: Dict[str, Any],
) -> Dict[str, Any]:
    exposure = skill_exposure_flags(artifact.name, projection)
    compact = compact_spreadsheet_skill_card(artifact)
    return {
        "skill_name": artifact.name,
        "version": artifact.version,
        "kind": artifact.kind,
        "status": artifact.status,
        "description": artifact.description,
        "body_projection": {
            "projection_kind": compact.get("projection_kind"),
            "body_chars": compact.get("body_chars"),
            "body_truncated": False,
            "body": artifact.body,
            "applicability": compact.get("applicability"),
            "non_applicability": compact.get("non_applicability"),
            "code_preview": compact.get("executable_code") or "",
        },
        "interface": {
            "summary": artifact.interface.summary,
            "usage": artifact.interface.usage,
            "input_contract": artifact.interface.input_contract,
            "output_contract": artifact.interface.output_contract,
            "invocation_contract": artifact.interface.invocation_contract,
            "compatibility_notes": artifact.interface.compatibility_notes,
        },
        "metadata": {
            "domains": artifact.metadata.get("domains") or [],
            "allowed_tools": artifact.metadata.get("allowed_tools") or [],
            "intent_keywords": artifact.metadata.get("intent_keywords") or [],
            "source_task_ids": artifact.metadata.get("source_task_ids") or [],
            "non_applicability": artifact.metadata.get("non_applicability"),
        },
        "exposure": {
            "retrieved": exposure["retrieved"],
            "injected": exposure["injected"],
            "used": exposure["used"],
        },
        "usage_count": artifact.usage_count,
        "success_count": artifact.success_count,
        "recent_helpful": [_compact_evidence_case(item) for item in artifact.evidence.helpful_cases[-2:]],
        "recent_harmful": [_compact_evidence_case(item) for item in artifact.evidence.harmful_cases[-2:]],
    }


def compact_spreadsheet_skill_card(artifact: SkillArtifact) -> Dict[str, Any]:
    body = str(artifact.body or "")
    executable_code = _spreadsheet_executable_code_block(body)
    include_full_body = bool(executable_code) and len(body) <= 6000
    projected_body = body if include_full_body else _compact_spreadsheet_body_text(body)
    return {
        "projection_kind": "full_executable_body" if include_full_body else "compact_body",
        "body": projected_body,
        "body_chars": len(body),
        "body_truncated": projected_body != body,
        "executable_code": executable_code,
        "executable_code_chars": len(executable_code),
        "executable_code_preserved": bool(executable_code) and executable_code in projected_body,
        "applicability": _extract_spreadsheet_body_section(body, "Applicability"),
        "non_applicability": artifact.metadata.get("non_applicability")
        or _extract_spreadsheet_body_section(body, "Non-applicability"),
    }


def _compact_evidence_case(item: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(item or {})
    return {
        key: row.get(key)
        for key in ("task_id", "judgment", "effect_type", "confidence", "failure_mode", "reason")
        if row.get(key) not in (None, "", [], {})
    }


def _spreadsheet_executable_code_block(body: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", str(body or ""), re.S | re.I)
    if match:
        return match.group(1).strip()
    text = str(body or "").strip()
    if any(marker in text for marker in ("INPUT_XLSX", "OUTPUT_XLSX", "openpyxl", "load_workbook(")):
        return text
    return ""


def _compact_spreadsheet_body_text(body: str, *, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(body or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 24].rstrip() + " ... [body truncated]"


def _extract_spreadsheet_body_section(body: str, label: str, *, limit: int = 360) -> str:
    pattern = rf"(?is){re.escape(label)}\s*:\s*(.*?)(?:\n\s*\n|```|[A-Z][A-Za-z -]+:\s*)"
    match = re.search(pattern, str(body or ""))
    if not match:
        return ""
    return _compact_spreadsheet_body_text(match.group(1), limit=limit)
