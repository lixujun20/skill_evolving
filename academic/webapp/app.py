"""
Skill Explorer + Replay Benchmark Explorer.

Usage:
    cd ~/skill_evolving
    python -m academic.webapp.app [--port 5050] [--skills-dir PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import os
import json
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# library_id -> {name, path, skills: [...], by_name: {...}}
LIBRARIES: Dict[str, Dict[str, Any]] = {}
CURRENT_LIB: str = ""

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_URL = (
    "postgresql+psycopg2://edumanus_user:edumanus_password@localhost:15432/aicosmos_test"
)
DEFAULT_REPLAY_PATHS = {
    "validated_cases": "academic/refactoring_lab/experiments/planning_replay_cases.json",
    "draft_cases": "academic/refactoring_lab/experiments/ds100_aime_replay_case_drafts.json",
    "merged_cases": "academic/refactoring_lab/experiments/planning_replay_cases_merged.json",
    "benchmark_output": "academic/refactoring_lab/experiments/planning_replay_benchmark.json",
    "merged_benchmark_output": (
        "academic/refactoring_lab/experiments/planning_replay_benchmark_merged.json"
    ),
    "candidates_output": (
        "academic/refactoring_lab/experiments/ds100_aime_replay_candidates.json"
    ),
    "baseline_detail": "academic/results/ds100_aime_v1_exp1_baseline_detail.json",
    "evolve_detail": "academic/results/ds100_aime_v1_exp1_evolve_1ep_detail.json",
    "skills_json": "academic/results/ds100_aime_v1_exp1_skills.json",
}
REPLAY_ACTIONS = [
    "reuse_plan",
    "adapt_plan",
    "reuse_workflow_fragment",
    "fresh",
    "propose_shared_skill",
]
WEBAPP_RUNTIME_DIR = REPO_ROOT / "academic" / "webapp" / "runtime"
WEBAPP_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR = WEBAPP_RUNTIME_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
ANNOTATION_RUNS_DIR = WEBAPP_RUNTIME_DIR / "annotation_runs"
ANNOTATION_RUNS_DIR.mkdir(parents=True, exist_ok=True)
EXECUTE_RUNS_DIR = WEBAPP_RUNTIME_DIR / "execute_runs"
EXECUTE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_RUNS_DIR = WEBAPP_RUNTIME_DIR / "memory_sessions"
MEMORY_RUNS_DIR.mkdir(parents=True, exist_ok=True)
JOB_REGISTRY: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()
DEFAULT_EXECUTE_SKILLS_PATH = REPO_ROOT / "academic" / "results" / "ds100_aime_v1_exp1_skills.json"
DEFAULT_EXECUTE_LOG_DIR = REPO_ROOT / "academic" / "results"
EXECUTE_RETRIEVE_TIMEOUT_S = 45
EXECUTE_SOLVE_TIMEOUT_S = 900
EXECUTE_EXTRACT_TIMEOUT_S = 180
EXECUTE_TOTAL_TIMEOUT_S = 1200
MAX_REFINE_ATTEMPTS = 3
MEMORY_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _make_lib_id(path: Path) -> str:
    """Derive a human-readable library id from filename."""
    stem = path.stem
    for suffix in ("_skills", "_skill"):
        stem = stem.replace(suffix, "")
    return stem


def load_libraries(skills_dir: str) -> None:
    global LIBRARIES, CURRENT_LIB
    LIBRARIES.clear()
    results_dir = Path(skills_dir)
    for p in sorted(results_dir.glob("*skills*.json")):
        try:
            with open(p) as f:
                skills = json.load(f)
            if not isinstance(skills, list) or not skills:
                continue
            lib_id = _make_lib_id(p)
            by_name = {s["name"]: s for s in skills}
            LIBRARIES[lib_id] = {
                "name": lib_id.replace("_", " ").title(),
                "path": str(p),
                "skills": skills,
                "by_name": by_name,
            }
        except Exception as e:
            print(f"Warning: skipping {p}: {e}")

    if LIBRARIES:
        CURRENT_LIB = next(iter(LIBRARIES))


def _get_lib(lib_id: str | None = None) -> Dict[str, Any] | None:
    """Get library by id, or current default."""
    lid = lib_id or request.args.get("lib") or CURRENT_LIB
    return LIBRARIES.get(lid)


def _resolve_path(raw_path: str | None, *, default_key: str | None = None) -> Path:
    """Resolve a UI-provided path relative to the repo root when needed."""
    if raw_path:
        path = Path(raw_path).expanduser()
    elif default_key:
        path = REPO_ROOT / DEFAULT_REPLAY_PATHS[default_key]
    else:
        raise ValueError("A path must be provided")
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _coerce_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [str(raw).strip()]


def _load_json(path: Path) -> Any:
    with path.open() as f:
        return json.load(f)


def _dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _load_case_container(path: Path) -> Tuple[Any, List[Dict[str, Any]]]:
    payload = _load_json(path)
    if isinstance(payload, list):
        return payload, payload
    if isinstance(payload, dict) and "draft_cases" in payload:
        return payload, list(payload["draft_cases"])
    raise ValueError(f"Unsupported replay case input format: {path}")


def _save_case_container(path: Path, original_payload: Any, cases: List[Dict[str, Any]]) -> None:
    if isinstance(original_payload, list):
        _dump_json(path, cases)
        return
    if isinstance(original_payload, dict) and "draft_cases" in original_payload:
        original_payload["draft_cases"] = cases
        _dump_json(path, original_payload)
        return
    raise ValueError(f"Unsupported replay case input format: {path}")


def _summarize_cases(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    status_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    for case in cases:
        status = str(case.get("status", "unknown"))
        source = str(case.get("source_experiment", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "n_cases": len(cases),
        "status_counts": status_counts,
        "source_counts": source_counts,
        "validated_cases": sum(1 for case in cases if case.get("status") == "validated"),
        "draft_cases": sum(1 for case in cases if case.get("status") == "draft"),
    }


def _now_ts() -> float:
    return time.time()


def _iso_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _persist_job(job: Dict[str, Any]) -> None:
    payload = copy.deepcopy(job)
    _dump_json(_job_path(job["job_id"]), payload)


def _load_job(job_id: str) -> Optional[Dict[str, Any]]:
    path = _job_path(job_id)
    if not path.exists():
        return None
    return _load_json(path)


def _register_job(job_type: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    job_id = f"{job_type}_{uuid.uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "job_type": job_type,
        "status": "queued",
        "created_at": _iso_now(),
        "updated_at": _iso_now(),
        "progress": {
            "total": 0,
            "completed": 0,
            "running_case_id": "",
            "message": "queued",
            "events": [],
        },
        "meta": meta or {},
        "result": None,
        "error": "",
    }
    with JOB_LOCK:
        JOB_REGISTRY[job_id] = job
        _persist_job(job)
    return job


def _append_job_event(job_id: str, message: str, *, data: Optional[Dict[str, Any]] = None) -> None:
    with JOB_LOCK:
        job = JOB_REGISTRY.get(job_id) or _load_job(job_id)
        if not job:
            return
        events = job.setdefault("progress", {}).setdefault("events", [])
        events.append(
            {
                "time": _iso_now(),
                "message": message,
                "data": data or {},
            }
        )
        job["updated_at"] = _iso_now()
        JOB_REGISTRY[job_id] = job
        _persist_job(job)


def _update_job(job_id: str, **updates: Any) -> None:
    with JOB_LOCK:
        job = JOB_REGISTRY.get(job_id) or _load_job(job_id)
        if not job:
            return
        for key, value in updates.items():
            if key == "progress" and isinstance(value, dict):
                job.setdefault("progress", {}).update(value)
            else:
                job[key] = value
        job["updated_at"] = _iso_now()
        JOB_REGISTRY[job_id] = job
        _persist_job(job)


def _set_job_partial_result(job_id: str, key: str, value: Any) -> None:
    with JOB_LOCK:
        job = JOB_REGISTRY.get(job_id) or _load_job(job_id)
        if not job:
            return
        partial = job.setdefault("partial_result", {})
        partial[key] = value
        job["updated_at"] = _iso_now()
        JOB_REGISTRY[job_id] = job
        _persist_job(job)


def _run_background(job_id: str, target, *args, **kwargs) -> None:
    def _runner() -> None:
        try:
            target(job_id, *args, **kwargs)
        except Exception:
            _update_job(
                job_id,
                status="failed",
                error=traceback.format_exc(),
                progress={"message": "job failed"},
            )
            _append_job_event(job_id, "Job failed", data={"traceback": traceback.format_exc()})

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()


def _safe_preview(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _json_block(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_json_loose(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    if "```" in raw:
        blocks = raw.split("```")
        for block in blocks:
            candidate = block.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                return json.loads(candidate)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start : end + 1])
    raise json.JSONDecodeError("Could not parse JSON object from LLM output", raw, 0)


def _normalize_case_history(case: Dict[str, Any]) -> None:
    history = case.setdefault("history_context", {})
    if not history.get("previous_query"):
        history["previous_query"] = case.get("candidate_metadata", {}).get("previous_query") or ""
    if not history.get("workflow_summary"):
        history["workflow_summary"] = history.get("historical_agent_summary") or ""
    if not history.get("previous_workflow_plan"):
        fragments = history.get("workflow_fragments") or []
        if fragments:
            history["previous_workflow_plan"] = "\n\n".join(
                fragment.get("content", "") for fragment in fragments if fragment.get("content")
            )
    history.setdefault("workflow_fragments", [])
    history.setdefault("trace_snippets", [])
    history.setdefault("proposed_skills", [])


def _normalize_replay_cases(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for case in cases:
        _normalize_case_history(case)
        case.setdefault("references", {})
        case.setdefault("annotation_notes", "")
        case.setdefault("status", "draft")
    return cases


def _case_brief(case: Dict[str, Any]) -> Dict[str, Any]:
    history = case.get("history_context", {}) or {}
    return {
        "case_id": case.get("case_id", ""),
        "problem_id": case.get("problem_id", ""),
        "query": case.get("query", ""),
        "status": case.get("status", "draft"),
        "failure_type": case.get("failure_type", ""),
        "source_experiment": case.get("source_experiment", ""),
        "historical_query": history.get("previous_query", ""),
        "workflow_summary": history.get("workflow_summary", ""),
        "previous_workflow_plan": history.get("previous_workflow_plan", ""),
        "retrieved_skills": case.get("retrieved_skills", []),
    }


ANNOTATION_SYSTEM_PROMPT = """You are annotating replay benchmark cases for workflow-aware planning research.

Your task is to read one replay case and produce a soft judgment-oriented annotation.

Rules:
- Historical query and historical workflow are always primary context.
- Do not require exact wording reuse.
- Do not require a shared skill to be proposed unless it is clearly beneficial.
- Prefer soft references that would help an LLM judge evaluate planning quality.
- Keep references non-binding: multiple valid plans may exist.
- Output valid JSON only.
"""


def _build_annotation_prompt(case: Dict[str, Any]) -> str:
    history = case.get("history_context", {}) or {}
    payload = {
        "case_id": case.get("case_id"),
        "problem_id": case.get("problem_id"),
        "query": case.get("query"),
        "failure_type": case.get("failure_type"),
        "historical_query": history.get("previous_query", ""),
        "workflow_summary": history.get("workflow_summary", ""),
        "previous_workflow_plan": history.get("previous_workflow_plan", ""),
        "workflow_fragments": history.get("workflow_fragments", []),
        "trace_snippets": history.get("trace_snippets", []),
        "retrieved_skills": [
            {
                "name": skill.get("name", ""),
                "description": skill.get("description", ""),
            }
            for skill in case.get("retrieved_skills", [])
        ],
        "existing_references": case.get("references", {}),
        "existing_notes": case.get("annotation_notes", ""),
    }
    schema = {
        "status": "validated|draft|rejected",
        "annotation_notes": "brief free-text justification",
        "references": {
            "preferred_actions": ["reuse_plan|adapt_plan|reuse_workflow_fragment|fresh|propose_shared_skill"],
            "useful_plan_calls": ["soft hints of calls or helper names that may appear"],
            "relevant_fragment_ids": ["historical fragment ids worth reusing, if any"],
            "possible_shared_skill_names": ["optional"],
            "discouraged_shared_skill_names": ["optional"],
            "desirable_keywords": ["optional"],
            "discouraged_keywords": ["optional"],
            "rubric_notes": "judge-facing notes"
        },
        "judge_summary": {
            "recommended_primary_mode": "short text",
            "why_history_is_or_is_not_relevant": "short text",
            "must_not_overconstrain": ["list of caveats"]
        }
    }
    return (
        "Annotate the following replay case.\n\n"
        "Case payload:\n"
        f"{_json_block(payload)}\n\n"
        "Return JSON with this schema:\n"
        f"{_json_block(schema)}"
    )


def _coerce_annotation_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    refs = raw.get("references", {}) or {}
    return {
        "status": str(raw.get("status", "draft")).strip() or "draft",
        "annotation_notes": str(raw.get("annotation_notes", "")).strip(),
        "references": {
            "preferred_actions": _coerce_str_list(refs.get("preferred_actions")),
            "useful_plan_calls": _coerce_str_list(refs.get("useful_plan_calls")),
            "relevant_fragment_ids": _coerce_str_list(refs.get("relevant_fragment_ids")),
            "possible_shared_skill_names": _coerce_str_list(
                refs.get("possible_shared_skill_names")
            ),
            "discouraged_shared_skill_names": _coerce_str_list(
                refs.get("discouraged_shared_skill_names")
            ),
            "desirable_keywords": _coerce_str_list(refs.get("desirable_keywords")),
            "discouraged_keywords": _coerce_str_list(refs.get("discouraged_keywords")),
            "rubric_notes": str(refs.get("rubric_notes", "")).strip(),
        },
        "judge_summary": raw.get("judge_summary", {}) or {},
    }


async def _annotate_case_with_llm(case: Dict[str, Any], llm_config: str) -> Dict[str, Any]:
    from app.llm import LLM

    prompt = _build_annotation_prompt(case)
    llm = LLM(config_name=llm_config)
    try:
        output = await llm.ask(
            messages=[{"role": "user", "content": prompt}],
            system_msgs=[{"role": "system", "content": ANNOTATION_SYSTEM_PROMPT}],
            force_json=True,
        )
    except Exception:
        # Some compatible backends reject `json_object` and expect `json_schema`.
        # Fall back to plain-text JSON instructions rather than mutating global LLM code.
        fallback_prompt = (
            prompt
            + "\n\nReturn raw JSON only. Do not wrap it in markdown. The first character must be '{' and the last must be '}'."
        )
        output = await llm.ask(
            messages=[{"role": "user", "content": fallback_prompt}],
            system_msgs=[{"role": "system", "content": ANNOTATION_SYSTEM_PROMPT}],
        )
    parsed = _parse_json_loose(output)
    structured = _coerce_annotation_payload(parsed)
    return {
        "prompt": prompt,
        "output": output,
        "parsed": parsed,
        "structured": structured,
    }


def _run_replay_annotation_job(
    job_id: str,
    *,
    path: Path,
    case_ids: List[str],
    llm_config: str,
    save_results: bool,
) -> None:
    _update_job(job_id, status="running", progress={"message": "loading cases"})
    original_payload, all_cases = _load_case_container(path)
    cases = _normalize_replay_cases(all_cases)
    selected = [case for case in cases if case.get("case_id") in set(case_ids)]
    _update_job(
        job_id,
        progress={
            "total": len(selected),
            "completed": 0,
            "message": "annotating cases",
        },
    )
    run_artifact = {
        "job_id": job_id,
        "cases_path": str(path),
        "llm_config": llm_config,
        "save_results": save_results,
        "cases": [],
        "started_at": _iso_now(),
    }
    _append_job_event(job_id, "Loaded replay cases", data={"n_selected": len(selected)})

    for idx, case in enumerate(selected, start=1):
        case_id = case.get("case_id", f"case_{idx}")
        _update_job(
            job_id,
            progress={
                "running_case_id": case_id,
                "message": f"annotating {case_id} ({idx}/{len(selected)})",
            },
        )
        _append_job_event(job_id, f"Start annotation for {case_id}")
        try:
            annotation = asyncio.run(_annotate_case_with_llm(case, llm_config))
            case.setdefault("llm_annotation", {})
            case["llm_annotation"] = {
                "annotated_at": _iso_now(),
                "llm_config": llm_config,
                "full_prompt": annotation["prompt"],
                "full_output": annotation["output"],
                "parsed_output": annotation["parsed"],
                "judge_summary": annotation["structured"].get("judge_summary", {}),
            }
            case["status"] = annotation["structured"]["status"]
            case["annotation_notes"] = annotation["structured"]["annotation_notes"]
            case["references"] = annotation["structured"]["references"]
            run_artifact["cases"].append(
                {
                    "case_id": case_id,
                    "annotation": annotation,
                    "saved_to_case_file": save_results,
                }
            )
            _append_job_event(
                job_id,
                f"Finished annotation for {case_id}",
                data={"status": case["status"]},
            )
        except Exception:
            tb = traceback.format_exc()
            run_artifact["cases"].append(
                {
                    "case_id": case_id,
                    "error": tb,
                }
            )
            case.setdefault("llm_annotation", {})
            case["llm_annotation"] = {
                "annotated_at": _iso_now(),
                "llm_config": llm_config,
                "error": tb,
            }
            _append_job_event(job_id, f"Annotation failed for {case_id}", data={"traceback": tb})
        finally:
            _update_job(
                job_id,
                progress={"completed": idx},
            )

    if save_results:
        _save_case_container(path, original_payload, cases)

    run_artifact["finished_at"] = _iso_now()
    artifact_path = ANNOTATION_RUNS_DIR / f"{job_id}.json"
    _dump_json(artifact_path, run_artifact)
    _update_job(
        job_id,
        status="completed",
        result={
            "artifact_path": str(artifact_path),
            "cases_path": str(path),
            "n_selected": len(selected),
            "summary": _summarize_cases(cases),
        },
        progress={
            "running_case_id": "",
            "message": "annotation complete",
        },
    )


def _resolve_execute_skills_path(raw_path: str | None) -> Path:
    if raw_path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path
    return DEFAULT_EXECUTE_SKILLS_PATH


def _load_skill_store_for_execute(skills_path: Path):
    from academic.skill_store import SkillStore

    store = SkillStore.load(skills_path)
    return store


def _serialize_workflow_record(record: Any) -> Dict[str, Any]:
    return {
        "query": getattr(record, "query", ""),
        "workflow_summary": getattr(record, "workflow_summary", ""),
        "workflow_plan": getattr(record, "workflow_plan", ""),
        "workflow_decision": getattr(record, "workflow_decision", ""),
        "final_answer": getattr(record, "final_answer", ""),
        "source_problem": getattr(record, "source_problem", ""),
        "retrieved_skills": list(getattr(record, "retrieved_skills", []) or []),
        "timestamp": getattr(record, "timestamp", 0.0),
    }


def _render_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rendered = []
    for msg in messages:
        rendered.append(
            {
                "role": msg.get("role", ""),
                "content": msg.get("content", ""),
                "thinking": msg.get("thinking", ""),
                "tool_call_id": msg.get("tool_call_id", ""),
                "tool_calls": msg.get("tool_calls", []),
            }
        )
    return rendered


def _serialize_skill(skill: Any) -> Dict[str, Any]:
    return {
        "name": getattr(skill, "name", ""),
        "description": getattr(skill, "description", ""),
        "code": getattr(skill, "code", ""),
        "version": getattr(skill, "version", 1),
        "usage_count": getattr(skill, "usage_count", 0),
        "success_count": getattr(skill, "success_count", 0),
        "dependencies": list(getattr(skill, "dependencies", []) or []),
        "test_code": getattr(skill, "test_code", ""),
        "ui_usage_count": getattr(skill, "usage_count", 0),
        "ui_success_count": getattr(skill, "success_count", 0),
    }


def _serialize_trace(trace: Any) -> Dict[str, Any]:
    return {
        "final_answer": trace.final_answer,
        "success": trace.success,
        "timed_out": trace.timed_out,
        "steps": trace.steps,
        "messages": _render_messages(trace.messages),
        "code_blocks": trace.code_blocks,
        "outputs": trace.outputs,
        "reasoning_traces": trace.reasoning_traces,
        "total_tokens": trace.total_tokens,
        "completion_tokens": trace.completion_tokens,
        "skill_tool_counts": trace.skill_tool_counts,
        "plan_context": getattr(trace, "plan_context", ""),
    }


def _memory_session_path(session_id: str) -> Path:
    return MEMORY_RUNS_DIR / f"{session_id}.json"


def _persist_memory_session(session: Dict[str, Any]) -> None:
    _dump_json(_memory_session_path(session["session_id"]), session)


def _register_memory_session(
    *,
    source_path: Path,
    mode: str,
) -> Dict[str, Any]:
    session_id = f"memory_{uuid.uuid4().hex[:12]}"
    if mode == "copy":
        cloned_path = MEMORY_RUNS_DIR / f"{session_id}_skills.json"
        cloned_path.write_text(source_path.read_text() if source_path.exists() else "[]")
        active_path = cloned_path
    else:
        active_path = source_path
    session = {
        "session_id": session_id,
        "mode": mode,
        "source_path": str(source_path),
        "active_path": str(active_path),
        "created_at": _iso_now(),
        "updated_at": _iso_now(),
    }
    MEMORY_SESSIONS[session_id] = session
    _persist_memory_session(session)
    return session


def _get_or_create_memory_session(
    *,
    skills_path: Path,
    session_id: str | None,
    copy_mode: bool,
) -> Dict[str, Any]:
    if session_id:
        session = MEMORY_SESSIONS.get(session_id) or (
            _load_json(_memory_session_path(session_id)) if _memory_session_path(session_id).exists() else None
        )
        if session:
            MEMORY_SESSIONS[session_id] = session
            return session
    return _register_memory_session(
        source_path=skills_path,
        mode="copy" if copy_mode else "direct",
    )


def _memory_session_summary(session: Dict[str, Any], store: Any) -> Dict[str, Any]:
    return {
        "session_id": session["session_id"],
        "mode": session["mode"],
        "source_path": session["source_path"],
        "active_path": session["active_path"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "skill_count": len(store.skills),
        "workflow_count": len(store.workflow_records),
    }


def _save_memory_store(session: Dict[str, Any], store: Any) -> None:
    active_path = Path(session["active_path"])
    store.save(active_path)
    session["updated_at"] = _iso_now()
    MEMORY_SESSIONS[session["session_id"]] = session
    _persist_memory_session(session)


def _load_memory_session_or_404(session_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Any], Optional[Tuple[Any, int]]]:
    if not session_id:
        return None, None, (jsonify({"error": "session_id is required"}), 400)
    path = _memory_session_path(session_id)
    if session_id in MEMORY_SESSIONS:
        session = MEMORY_SESSIONS[session_id]
    elif path.exists():
        session = _load_json(path)
        MEMORY_SESSIONS[session_id] = session
    else:
        return None, None, (jsonify({"error": f"memory session not found: {session_id}"}), 404)
    store = _load_skill_store_for_execute(Path(session["active_path"]))
    return session, store, None


async def _execute_query_pipeline_async(
    query: str,
    *,
    skills_path: Path,
    top_k: int,
    llm_config: str,
    system_prompt_template: str,
    run_extract: bool,
    run_test: bool,
    job_id: Optional[str] = None,
    memory_session_id: Optional[str] = None,
    copy_memory: bool = True,
) -> Dict[str, Any]:
    from academic.executor import solve
    from academic.extractor import extract_skills, refine_skill_after_test_failure
    from academic.pipeline import check_answer
    from academic.planner import build_execution_plan
    from academic.tester import test_skill

    def _stage(message: str, **data: Any) -> None:
        if not job_id:
            return
        _append_job_event(job_id, message, data=data or {})
        _update_job(job_id, progress={"message": message})

    memory_session = _get_or_create_memory_session(
        skills_path=skills_path,
        session_id=memory_session_id,
        copy_mode=copy_memory,
    )
    store = _load_skill_store_for_execute(Path(memory_session["active_path"]))
    _stage(
        "Loading skill store",
        skills_path=str(skills_path),
        memory_session_id=memory_session["session_id"],
        memory_mode=memory_session["mode"],
    )

    try:
        _stage("Retrieving skills", top_k=top_k)
        retrieved_skills = await asyncio.wait_for(
            store.retrieve(query, top_k=top_k),
            timeout=EXECUTE_RETRIEVE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _stage("Skill retrieval timed out, falling back to sync TF-IDF")
        retrieved_skills = store.retrieve_sync(query, top_k=top_k)

    try:
        _stage("Retrieving historical workflows", top_k=3)
        retrieved_workflows = await asyncio.wait_for(
            store.retrieve_workflows(query, top_k=3),
            timeout=EXECUTE_RETRIEVE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _stage("Workflow retrieval timed out, falling back to sync retrieval")
        retrieved_workflows = store.retrieve_sync_workflows(query, top_k=3)

    workflow_prompt = store.build_workflow_prompt(retrieved_workflows)
    skills_prompt = store.build_skills_prompt(retrieved_skills)
    plan_artifact = build_execution_plan(query, retrieved_skills, retrieved_workflows)
    if job_id:
        _set_job_partial_result(
            job_id,
            "retrieve",
            {
                "retrieved_skills": [_serialize_skill(skill) for skill in retrieved_skills],
                "retrieved_workflows": [_serialize_workflow_record(record) for record in retrieved_workflows],
                "workflow_prompt": workflow_prompt,
                "workflow_store_count": len(store.workflow_records),
                "memory": _memory_session_summary(memory_session, store),
            },
        )
        _set_job_partial_result(
            job_id,
            "plan",
            {
                "system_prompt_template": system_prompt_template,
                "historical_workflow_prompt": workflow_prompt,
                "skills_prompt": skills_prompt,
                "planner_artifact": plan_artifact.as_dict(),
                "executor_plan_context": plan_artifact.executor_context,
            },
        )

    _stage(
        "Solving query",
        retrieved_skills=[skill.name for skill in retrieved_skills],
        retrieved_workflows=len(retrieved_workflows),
    )
    trace = await asyncio.wait_for(
        solve(
            query,
            retrieved_skills,
            llm_config=llm_config,
            store=store,
            system_prompt_template=system_prompt_template,
            plan_context=plan_artifact.executor_context,
            on_trace_update=(
                lambda trace: _set_job_partial_result(job_id, "execute", _serialize_trace(trace))
            ) if job_id else None,
        ),
        timeout=EXECUTE_SOLVE_TIMEOUT_S,
    )
    if job_id:
        _set_job_partial_result(job_id, "execute", _serialize_trace(trace))

    extracted_skills = []
    test_results = []
    refine_history = []
    if run_extract:
        _stage("Extracting skills")
        extracted_skills = await asyncio.wait_for(
            extract_skills(
                query=query,
                code_blocks=trace.code_blocks,
                outputs=trace.outputs,
                existing_skills_prompt=skills_prompt,
                reasoning_traces=trace.reasoning_traces,
            ),
            timeout=EXECUTE_EXTRACT_TIMEOUT_S,
        )
        if job_id:
            _set_job_partial_result(
                job_id,
                "extract",
                {
                    "run_extract": run_extract,
                    "skills": [asdict(skill) for skill in extracted_skills],
                    "refine_history": refine_history,
                },
            )
    if run_test and extracted_skills:
        _stage("Testing extracted skills", n_skills=len(extracted_skills))
        for skill in extracted_skills:
            current_skill = skill
            attempts = []
            final_record = None
            fixed_test_code = skill.test_code
            for attempt_idx in range(1, MAX_REFINE_ATTEMPTS + 2):
                tr = test_skill(current_skill, store)
                record = asdict(tr)
                record["attempt"] = attempt_idx
                record["candidate_skill"] = asdict(current_skill)
                attempts.append(record)
                if tr.passed:
                    store.add(current_skill)
                    final_record = {
                        "skill_name": current_skill.name,
                        "passed": True,
                        "attempts": attempts,
                        "final_skill": asdict(current_skill),
                        "final_error": tr.error,
                    }
                    break
                if attempt_idx > MAX_REFINE_ATTEMPTS:
                    final_record = {
                        "skill_name": current_skill.name,
                        "passed": False,
                        "attempts": attempts,
                        "final_skill": asdict(current_skill),
                        "final_error": tr.error,
                    }
                    break
                _stage(
                    "Refining failed extracted skill",
                    skill_name=current_skill.name,
                    attempt=attempt_idx,
                )
                refined = await refine_skill_after_test_failure(
                    query=query,
                    skill=current_skill,
                    test_error=tr.error,
                    fixed_test_code=fixed_test_code,
                    existing_skills_prompt=skills_prompt,
                    refinement_history=[
                        {
                            "attempt": item["attempt"],
                            "test_error": item["error"],
                            "skill_code": item["candidate_skill"]["code"],
                        }
                        for item in attempts
                    ],
                )
                if not refined:
                    final_record = {
                        "skill_name": current_skill.name,
                        "passed": False,
                        "attempts": attempts,
                        "final_skill": asdict(current_skill),
                        "final_error": tr.error,
                    }
                    break
                current_skill = refined[0]
                refine_history.append(
                    {
                        "skill_name": skill.name,
                        "attempt": attempt_idx,
                        "test_error": tr.error,
                        "refined_skill": asdict(current_skill),
                    }
                )
                if job_id:
                    _set_job_partial_result(
                        job_id,
                        "extract",
                        {
                            "run_extract": run_extract,
                            "skills": [asdict(item) for item in extracted_skills],
                            "refine_history": refine_history,
                        },
                    )
            test_results.append(final_record or {
                "skill_name": skill.name,
                "passed": False,
                "attempts": attempts,
                "final_skill": asdict(current_skill),
                "final_error": attempts[-1]["error"] if attempts else "",
            })
        if job_id:
            _set_job_partial_result(
                job_id,
                "test",
                {
                    "run_test": run_test,
                    "results": test_results,
                    "refine_history": refine_history,
                },
            )
    _save_memory_store(memory_session, store)

    expected_answer = ""
    answer_correct = False
    if expected_answer:
        answer_correct = check_answer(trace.final_answer, expected_answer)

    return {
        "query": query,
        "skills_path": str(skills_path),
        "top_k": top_k,
        "llm_config": llm_config,
        "retrieve": {
            "retrieved_skills": [_serialize_skill(skill) for skill in retrieved_skills],
            "retrieved_workflows": [_serialize_workflow_record(record) for record in retrieved_workflows],
            "workflow_prompt": workflow_prompt,
            "workflow_store_count": len(store.workflow_records),
            "memory": _memory_session_summary(memory_session, store),
        },
        "plan": {
            "system_prompt_template": system_prompt_template,
            "historical_workflow_prompt": workflow_prompt,
            "skills_prompt": skills_prompt,
            "planner_artifact": plan_artifact.as_dict(),
            "executor_plan_context": plan_artifact.executor_context,
        },
        "execute": _serialize_trace(trace),
        "extract": {
            "run_extract": run_extract,
            "skills": [asdict(skill) for skill in extracted_skills],
            "refine_history": refine_history,
        },
        "test": {
            "run_test": run_test,
            "results": test_results,
            "refine_history": refine_history,
        },
        "evaluation": {
            "expected_answer": expected_answer,
            "answer_correct": answer_correct,
        },
        "memory": _memory_session_summary(memory_session, store),
    }


def _run_execute_job(
    job_id: str,
    *,
    query: str,
    skills_path: Path,
    top_k: int,
    llm_config: str,
    system_prompt_template: str,
    run_extract: bool,
    run_test: bool,
    memory_session_id: Optional[str] = None,
    copy_memory: bool = True,
) -> None:
    _update_job(job_id, status="running", progress={"message": "retrieving and executing"})
    _append_job_event(job_id, "Starting execute pipeline", data={"query": query})
    try:
        result = asyncio.run(
            asyncio.wait_for(
                _execute_query_pipeline_async(
                    query=query,
                    skills_path=skills_path,
                    top_k=top_k,
                    llm_config=llm_config,
                    system_prompt_template=system_prompt_template,
                    run_extract=run_extract,
                    run_test=run_test,
                    job_id=job_id,
                    memory_session_id=memory_session_id,
                    copy_memory=copy_memory,
                ),
                timeout=EXECUTE_TOTAL_TIMEOUT_S,
            )
        )
    except asyncio.TimeoutError:
        _update_job(
            job_id,
            status="failed",
            error=(
                f"Execute pipeline exceeded timeout of {EXECUTE_TOTAL_TIMEOUT_S} seconds. "
                "The job was terminated instead of remaining stuck in running state."
            ),
            progress={"message": "execute timeout", "completed": 0, "total": 1},
        )
        _append_job_event(
            job_id,
            "Execute pipeline timed out",
            data={"timeout_s": EXECUTE_TOTAL_TIMEOUT_S},
        )
        return
    except Exception:
        tb = traceback.format_exc()
        _update_job(
            job_id,
            status="failed",
            error=tb,
            progress={"message": "execute failed", "completed": 0, "total": 1},
        )
        _append_job_event(job_id, "Execute pipeline failed", data={"traceback": tb})
        return

    artifact_path = EXECUTE_RUNS_DIR / f"{job_id}.json"
    _dump_json(artifact_path, result)
    _update_job(
        job_id,
        status="completed",
        result={"artifact_path": str(artifact_path), "run": result},
        progress={"completed": 1, "total": 1, "message": "execute complete"},
    )
    _append_job_event(job_id, "Execute pipeline finished", data={"artifact_path": str(artifact_path)})


# ── HTML Routes ──────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/replay")
def replay():
    return render_template("replay.html")


@app.route("/execute")
def execute():
    return render_template("execute.html")


# ── Skill Explorer API ──────────────────────────────────────────────────────


@app.route("/api/libraries")
def api_libraries():
    """Return list of available skill libraries."""
    result = []
    for lid, lib in LIBRARIES.items():
        result.append(
            {
                "id": lid,
                "name": lib["name"],
                "skill_count": len(lib["skills"]),
                "path": lib["path"],
            }
        )
    return jsonify(result)


@app.route("/api/skills")
def api_skills():
    """Return all skills (summary view) for a library."""
    lib = _get_lib()
    if not lib:
        return jsonify([])
    q = request.args.get("q", "").lower()
    result = []
    for s in lib["skills"]:
        if q and q not in s["name"].lower() and q not in s.get("description", "").lower():
            continue
        result.append(
            {
                "name": s["name"],
                "description": s.get("description", ""),
                "version": s.get("version", 1),
                "usage_count": s.get("usage_count", 0),
                "success_count": s.get("success_count", 0),
                "dependencies": s.get("dependencies", []),
                "has_test": bool(s.get("test_code")),
            }
        )
    return jsonify(result)


@app.route("/api/skills/<name>")
def api_skill_detail(name: str):
    """Return full skill details."""
    lib = _get_lib()
    if not lib:
        return jsonify({"error": "No library selected"}), 404
    s = lib["by_name"].get(name)
    if not s:
        return jsonify({"error": f"Skill '{name}' not found"}), 404
    return jsonify(s)


@app.route("/api/skills/<name>/run", methods=["POST"])
def api_run_skill(name: str):
    """Run a skill's test code or custom code."""
    lib = _get_lib()
    if not lib:
        return jsonify({"error": "No library selected"}), 404
    s = lib["by_name"].get(name)
    if not s:
        return jsonify({"error": f"Skill '{name}' not found"}), 404

    body = request.get_json(silent=True) or {}
    custom_code = body.get("code", "")

    namespace: Dict[str, Any] = {"__builtins__": __builtins__}
    dep_errors = []
    loaded = set()

    def _load_deps(skill_name: str):
        if skill_name in loaded:
            return
        loaded.add(skill_name)
        dep_skill = lib["by_name"].get(skill_name)
        if not dep_skill:
            dep_errors.append(f"Dependency '{skill_name}' not found")
            return
        for dep in dep_skill.get("dependencies", []):
            _load_deps(dep)
        try:
            exec(dep_skill["code"], namespace)
        except Exception as e:
            dep_errors.append(f"Error loading '{skill_name}': {e}")

    for dep in s.get("dependencies", []):
        _load_deps(dep)

    try:
        exec(s["code"], namespace)
    except Exception as e:
        return jsonify({"success": False, "output": f"Error loading skill: {e}"})

    code_to_run = custom_code or s.get("test_code", "")
    if not code_to_run:
        return jsonify({"success": True, "output": "No test code available."})

    import contextlib
    import io

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(code_to_run, namespace)
        output = buf.getvalue()
        if dep_errors:
            output = "Dependency warnings:\n" + "\n".join(dep_errors) + "\n\n" + output
        return jsonify({"success": True, "output": output or "✓ All assertions passed."})
    except Exception:
        tb = traceback.format_exc()
        return jsonify({"success": False, "output": tb})


@app.route("/api/graph")
def api_graph():
    """Return dependency graph as nodes + edges for visualization."""
    lib = _get_lib()
    if not lib:
        return jsonify({"nodes": [], "edges": []})
    nodes = []
    edges = []
    for s in lib["skills"]:
        nodes.append(
            {
                "id": s["name"],
                "usage": s.get("usage_count", 0),
                "success": s.get("success_count", 0),
            }
        )
        for dep in s.get("dependencies", []):
            edges.append({"source": dep, "target": s["name"]})
    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/api/stats")
def api_stats():
    """Return aggregate statistics."""
    lib = _get_lib()
    if not lib:
        return jsonify(
            {
                "total_skills": 0,
                "used_skills": 0,
                "skills_with_deps": 0,
                "total_usage": 0,
                "total_success": 0,
                "avg_success_rate": 0,
            }
        )
    skills = lib["skills"]
    total = len(skills)
    used = sum(1 for s in skills if s.get("usage_count", 0) > 0)
    with_deps = sum(1 for s in skills if s.get("dependencies"))
    total_usage = sum(s.get("usage_count", 0) for s in skills)
    total_success = sum(s.get("success_count", 0) for s in skills)
    return jsonify(
        {
            "total_skills": total,
            "used_skills": used,
            "skills_with_deps": with_deps,
            "total_usage": total_usage,
            "total_success": total_success,
            "avg_success_rate": total_success / total_usage if total_usage else 0,
        }
    )


# ── Replay Benchmark API ────────────────────────────────────────────────────


@app.route("/api/replay/files")
def api_replay_files():
    files = {}
    for key, rel_path in DEFAULT_REPLAY_PATHS.items():
        path = REPO_ROOT / rel_path
        files[key] = {"path": str(path), "exists": path.exists()}
    return jsonify(
        {
            "repo_root": str(REPO_ROOT),
            "db_url": DEFAULT_DB_URL,
            "files": files,
            "actions": REPLAY_ACTIONS,
            "annotation_runs_dir": str(ANNOTATION_RUNS_DIR),
            "execute_runs_dir": str(EXECUTE_RUNS_DIR),
            "default_skills_path": str(DEFAULT_EXECUTE_SKILLS_PATH),
        }
    )


@app.route("/api/replay/cases")
def api_replay_cases():
    try:
        path = _resolve_path(request.args.get("path"), default_key="merged_cases")
        original_payload, cases = _load_case_container(path)
        cases = _normalize_replay_cases(cases)
        return jsonify(
            {
                "path": str(path),
                "summary": _summarize_cases(cases),
                "cases": cases,
                "container_type": "draft_cases"
                if isinstance(original_payload, dict)
                else "list",
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/replay/annotate", methods=["POST"])
def api_replay_annotate():
    body = request.get_json(silent=True) or {}
    try:
        path = _resolve_path(body.get("path"), default_key="merged_cases")
        case_ids = _coerce_str_list(body.get("case_ids"))
        if not case_ids:
            original_payload, cases = _load_case_container(path)
            cases = _normalize_replay_cases(cases)
            case_ids = [case.get("case_id", "") for case in cases if case.get("case_id")]
        llm_config = str(body.get("llm_config", "tool_maker")).strip() or "tool_maker"
        save_results = bool(body.get("save_results", True))
        job = _register_job(
            "replay_annotate",
            meta={
                "path": str(path),
                "case_ids": case_ids,
                "llm_config": llm_config,
                "save_results": save_results,
            },
        )
        _run_background(
            job["job_id"],
            _run_replay_annotation_job,
            path=path,
            case_ids=case_ids,
            llm_config=llm_config,
            save_results=save_results,
        )
        return jsonify({"ok": True, "job_id": job["job_id"]})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/jobs/<job_id>")
def api_job_status(job_id: str):
    job = JOB_REGISTRY.get(job_id) or _load_job(job_id)
    if not job:
        return jsonify({"error": f"Job not found: {job_id}"}), 404
    return jsonify(job)


@app.route("/api/jobs")
def api_jobs():
    job_type = request.args.get("job_type", "").strip()
    jobs = []
    for path in sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            payload = _load_json(path)
            if job_type and payload.get("job_type") != job_type:
                continue
            jobs.append(payload)
        except Exception:
            continue
    return jsonify({"jobs": jobs[:50]})


@app.route("/api/replay/cases/save", methods=["POST"])
def api_replay_cases_save():
    body = request.get_json(silent=True) or {}
    case_id = body.get("case_id", "")
    if not case_id:
        return jsonify({"error": "case_id is required"}), 400

    try:
        path = _resolve_path(body.get("path"), default_key="merged_cases")
        original_payload, cases = _load_case_container(path)
        for case in cases:
            if case.get("case_id") != case_id:
                continue
            case["status"] = body.get("status", case.get("status", "draft"))
            case["annotation_notes"] = body.get(
                "annotation_notes", case.get("annotation_notes", "")
            )
            refs = case.setdefault("references", {})
            incoming_refs = body.get("references", {}) or {}
            refs["preferred_actions"] = _coerce_str_list(
                incoming_refs.get("preferred_actions")
            )
            refs["useful_plan_calls"] = _coerce_str_list(
                incoming_refs.get("useful_plan_calls")
            )
            refs["relevant_fragment_ids"] = _coerce_str_list(
                incoming_refs.get("relevant_fragment_ids")
            )
            refs["possible_shared_skill_names"] = _coerce_str_list(
                incoming_refs.get("possible_shared_skill_names")
            )
            refs["discouraged_shared_skill_names"] = _coerce_str_list(
                incoming_refs.get("discouraged_shared_skill_names")
            )
            refs["desirable_keywords"] = _coerce_str_list(
                incoming_refs.get("desirable_keywords")
            )
            refs["discouraged_keywords"] = _coerce_str_list(
                incoming_refs.get("discouraged_keywords")
            )
            refs["rubric_notes"] = str(incoming_refs.get("rubric_notes", "")).strip()

            _save_case_container(path, original_payload, cases)
            return jsonify(
                {
                    "ok": True,
                    "path": str(path),
                    "case": case,
                    "summary": _summarize_cases(cases),
                }
            )
        return jsonify({"error": f"Case not found: {case_id}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/replay/results")
def api_replay_results():
    try:
        path = _resolve_path(request.args.get("path"), default_key="benchmark_output")
        payload = _load_json(path)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/replay/annotation-run")
def api_replay_annotation_run():
    try:
        path = _resolve_path(request.args.get("path"))
        payload = _load_json(path)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/replay/run-benchmark", methods=["POST"])
def api_replay_run_benchmark():
    body = request.get_json(silent=True) or {}
    try:
        cases_path = _resolve_path(body.get("cases_path"), default_key="validated_cases")
        output_path = _resolve_path(body.get("output_path"), default_key="benchmark_output")
        db_url = body.get("db_url") or DEFAULT_DB_URL
        allow_live_llm = bool(body.get("allow_live_llm", False))

        from academic.refactoring_lab.planning_replay_benchmark import (
            run_planning_replay_benchmark,
        )

        result = asyncio.run(
            run_planning_replay_benchmark(
                cases_path=cases_path,
                db_url=db_url,
                allow_live_llm=allow_live_llm,
            )
        )
        _dump_json(output_path, result)
        return jsonify({"ok": True, "output_path": str(output_path), "result": result})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/replay/mine-candidates", methods=["POST"])
def api_replay_mine_candidates():
    body = request.get_json(silent=True) or {}
    try:
        baseline_detail_path = _resolve_path(
            body.get("baseline_detail_path"), default_key="baseline_detail"
        )
        evolve_detail_path = _resolve_path(
            body.get("evolve_detail_path"), default_key="evolve_detail"
        )
        output_path = _resolve_path(
            body.get("output_path"), default_key="candidates_output"
        )

        from academic.refactoring_lab.mine_replay_candidates import mine_replay_candidates

        result = mine_replay_candidates(
            baseline_detail_path=baseline_detail_path,
            evolve_detail_path=evolve_detail_path,
        )
        _dump_json(output_path, result)
        return jsonify({"ok": True, "output_path": str(output_path), "result": result})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/replay/build-drafts", methods=["POST"])
def api_replay_build_drafts():
    body = request.get_json(silent=True) or {}
    try:
        candidates_path = _resolve_path(
            body.get("candidates_path"), default_key="candidates_output"
        )
        baseline_detail_path = _resolve_path(
            body.get("baseline_detail_path"), default_key="baseline_detail"
        )
        evolve_detail_path = _resolve_path(
            body.get("evolve_detail_path"), default_key="evolve_detail"
        )
        skills_path = _resolve_path(body.get("skills_path"), default_key="skills_json")
        output_path = _resolve_path(body.get("output_path"), default_key="draft_cases")

        from academic.refactoring_lab.build_replay_case_drafts import (
            build_replay_case_drafts,
        )

        result = build_replay_case_drafts(
            candidates_path=candidates_path,
            evolve_detail_path=evolve_detail_path,
            baseline_detail_path=baseline_detail_path,
            skills_path=skills_path,
        )
        _dump_json(output_path, result)
        return jsonify({"ok": True, "output_path": str(output_path), "result": result})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/replay/merge-cases", methods=["POST"])
def api_replay_merge_cases():
    body = request.get_json(silent=True) or {}
    try:
        base_cases_path = _resolve_path(
            body.get("base_cases_path"), default_key="validated_cases"
        )
        drafts_path = _resolve_path(body.get("drafts_path"), default_key="draft_cases")
        output_path = _resolve_path(body.get("output_path"), default_key="merged_cases")

        from academic.refactoring_lab.merge_replay_cases import merge_replay_cases

        merged = merge_replay_cases(
            base_cases_path=base_cases_path,
            drafts_path=drafts_path,
        )
        _dump_json(output_path, merged)
        return jsonify({"ok": True, "output_path": str(output_path), "n_cases": len(merged)})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/execute/config")
def api_execute_config():
    from app.config import config

    llm_options = []
    for name, cfg in config.llm.items():
        model = getattr(cfg, "model", "")
        if name == "embedding":
            continue
        label = f"{name} | {model}" if model else name
        llm_options.append(
            {
                "id": name,
                "label": label,
                "model": model,
            }
        )
    llm_options.sort(key=lambda item: item["id"])

    return jsonify(
        {
            "default_skills_path": str(DEFAULT_EXECUTE_SKILLS_PATH),
            "default_log_dir": str(DEFAULT_EXECUTE_LOG_DIR),
            "skill_libraries": [
                {
                    "id": lid,
                    "name": lib["name"],
                    "path": lib["path"],
                    "skill_count": len(lib["skills"]),
                }
                for lid, lib in LIBRARIES.items()
            ],
            "llm_config_default": "tool_maker",
            "llm_options": llm_options,
            "system_prompt_options": [
                {"id": "solve", "label": "AIME / Integer", "import_name": "SOLVE_SYSTEM"},
                {"id": "math", "label": "General Math", "import_name": "MATH_SOLVE_SYSTEM"},
            ],
        }
    )


@app.route("/api/execute/run", methods=["POST"])
def api_execute_run():
    body = request.get_json(silent=True) or {}
    query = str(body.get("query", "")).strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        from academic.executor import MATH_SOLVE_SYSTEM, SOLVE_SYSTEM

        skills_path = _resolve_execute_skills_path(body.get("skills_path"))
        prompt_mode = str(body.get("prompt_mode", "solve")).strip()
        system_prompt_template = MATH_SOLVE_SYSTEM if prompt_mode == "math" else SOLVE_SYSTEM
        top_k = int(body.get("top_k", 5))
        llm_config = str(body.get("llm_config", "tool_maker")).strip() or "tool_maker"
        run_extract = bool(body.get("run_extract", True))
        run_test = bool(body.get("run_test", True))
        memory_session_id = str(body.get("memory_session_id", "")).strip() or None
        copy_memory = bool(body.get("copy_memory", True))

        job = _register_job(
            "execute_pipeline",
            meta={
                "query": query,
                "skills_path": str(skills_path),
                "prompt_mode": prompt_mode,
                "top_k": top_k,
                "llm_config": llm_config,
                "run_extract": run_extract,
                "run_test": run_test,
                "memory_session_id": memory_session_id,
                "copy_memory": copy_memory,
            },
        )
        _run_background(
            job["job_id"],
            _run_execute_job,
            query=query,
            skills_path=skills_path,
            top_k=top_k,
            llm_config=llm_config,
            system_prompt_template=system_prompt_template,
            run_extract=run_extract,
            run_test=run_test,
            memory_session_id=memory_session_id,
            copy_memory=copy_memory,
        )
        return jsonify({"ok": True, "job_id": job["job_id"]})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/execute/run-artifact")
def api_execute_run_artifact():
    try:
        path = _resolve_path(request.args.get("path"))
        payload = _load_json(path)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/execute/memory/session", methods=["POST"])
def api_execute_memory_session():
    body = request.get_json(silent=True) or {}
    try:
        skills_path = _resolve_execute_skills_path(body.get("skills_path"))
        copy_mode = bool(body.get("copy_mode", True))
        session = _register_memory_session(
            source_path=skills_path,
            mode="copy" if copy_mode else "direct",
        )
        store = _load_skill_store_for_execute(Path(session["active_path"]))
        return jsonify(
            {
                "ok": True,
                "session": _memory_session_summary(session, store),
                "skills": [_serialize_skill(skill) for skill in store.skills],
                "workflows": [_serialize_workflow_record(item) for item in store.workflow_records],
            }
        )
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/execute/memory", methods=["GET"])
def api_execute_memory():
    session_id = str(request.args.get("session_id", "")).strip()
    try:
        session, store, error = _load_memory_session_or_404(session_id)
        if error:
            return error
        return jsonify(
            {
                "ok": True,
                "session": _memory_session_summary(session, store),
                "skills": [_serialize_skill(skill) for skill in store.skills],
                "workflows": [_serialize_workflow_record(item) for item in store.workflow_records],
            }
        )
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/execute/memory/skill", methods=["POST"])
def api_execute_memory_upsert_skill():
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id", "")).strip()
    try:
        from academic.skill_store import Skill

        session, store, error = _load_memory_session_or_404(session_id)
        if error:
            return error
        skill = Skill(
            name=str(body.get("name", "")).strip(),
            description=str(body.get("description", "")).strip(),
            code=str(body.get("code", "")).rstrip(),
            test_code=str(body.get("test_code", "")).rstrip(),
        )
        if not skill.name or not skill.code:
            return jsonify({"error": "name and code are required"}), 400
        store.add(skill)
        _save_memory_store(session, store)
        return jsonify(
            {
                "ok": True,
                "session": _memory_session_summary(session, store),
                "skills": [_serialize_skill(item) for item in store.skills],
                "workflows": [_serialize_workflow_record(item) for item in store.workflow_records],
            }
        )
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/execute/memory/skill/delete", methods=["POST"])
def api_execute_memory_delete_skill():
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id", "")).strip()
    name = str(body.get("name", "")).strip()
    try:
        session, store, error = _load_memory_session_or_404(session_id)
        if error:
            return error
        if not name:
            return jsonify({"error": "name is required"}), 400
        store.remove(name)
        _save_memory_store(session, store)
        return jsonify(
            {
                "ok": True,
                "session": _memory_session_summary(session, store),
                "skills": [_serialize_skill(item) for item in store.skills],
                "workflows": [_serialize_workflow_record(item) for item in store.workflow_records],
            }
        )
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/execute/memory/workflow", methods=["POST"])
def api_execute_memory_add_workflow():
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id", "")).strip()
    try:
        from academic.skill_store import WorkflowRecord

        session, store, error = _load_memory_session_or_404(session_id)
        if error:
            return error
        record = WorkflowRecord(
            query=str(body.get("query", "")).strip(),
            workflow_summary=str(body.get("workflow_summary", "")).strip(),
            workflow_plan=str(body.get("workflow_plan", "")).strip(),
            workflow_decision=str(body.get("workflow_decision", "")).strip(),
            final_answer=str(body.get("final_answer", "")).strip(),
            source_problem=str(body.get("source_problem", "")).strip(),
            retrieved_skills=_coerce_str_list(body.get("retrieved_skills")),
        )
        if not record.query or not record.workflow_summary:
            return jsonify({"error": "query and workflow_summary are required"}), 400
        store.add_workflow_record(record)
        _save_memory_store(session, store)
        return jsonify(
            {
                "ok": True,
                "session": _memory_session_summary(session, store),
                "skills": [_serialize_skill(item) for item in store.skills],
                "workflows": [_serialize_workflow_record(item) for item in store.workflow_records],
            }
        )
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--skills-dir",
        default=str(Path(__file__).parent.parent / "results"),
        help="Directory containing *skills*.json files",
    )
    args = parser.parse_args()

    load_libraries(args.skills_dir)
    for lid, lib in LIBRARIES.items():
        print(f"  [{lid}] {lib['name']} — {len(lib['skills'])} skills ({lib['path']})")
    print(f"Loaded {len(LIBRARIES)} libraries. Server: http://{args.host}:{args.port}")

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
