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

from flask import Flask, Response, jsonify, render_template, request

from academic.skill_repository.maintenance_runner import build_runner_trace_from_debug_events
from academic.skill_repository.maintenance_state_machine import (
    build_player_trace,
    build_player_trace_from_pages,
    compact_debug_event_for_player,
)

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


def _load_jsonl(path: Path) -> List[Any]:
    rows: List[Any] = []
    if not path.exists():
        return rows
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:
            rows.append({"error": f"Failed to parse JSONL row: {exc}", "raw": raw_line})
    return rows


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


MAINTENANCE_RESULTS_PREFIX = "bfcl_real_glm_maintenance_"
MAINTENANCE_AUDIT_PREFIX = "real_glm_maintenance_"
METHOD_VALIDATION_DIR = REPO_ROOT / "academic" / "results" / "method_validation"
MAINTENANCE_DETAIL_TRACE_LIMIT = 12
MAINTENANCE_DETAIL_RAW_EVENT_LIMIT = 1200


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _artifact_raw_debug_snapshot(skill: Dict[str, Any]) -> Dict[str, Any]:
    """Small debug snapshot; full fields are sent separately on the card."""

    return {
        "name": skill.get("name", ""),
        "kind": skill.get("kind", ""),
        "description": skill.get("description", ""),
        "status": skill.get("status", ""),
        "version": skill.get("version", 1),
        "version_kind": skill.get("version_kind", ""),
        "stale": bool(skill.get("stale", False)),
        "dependencies": copy.deepcopy(skill.get("dependencies") or []),
        "dependency_pins": copy.deepcopy(skill.get("dependency_pins") or []),
        "metadata": copy.deepcopy(skill.get("metadata") or {}),
        "lineage": copy.deepcopy(skill.get("lineage") or {}),
        "bundle_counts": {
            "positive": len(((skill.get("bundle") or {}).get("positive_cases") or [])),
            "negative": len(((skill.get("bundle") or {}).get("negative_cases") or [])),
            "integration": len(((skill.get("bundle") or {}).get("integration_cases") or [])),
        },
    }


def _read_text_if_exists(raw_path: str | None) -> str:
    if not raw_path:
        return ""
    path = Path(raw_path)
    if not path.exists():
        return ""
    return path.read_text()


def _first_json_file(path: Path) -> Optional[Path]:
    items = sorted(path.glob("*.json"))
    if not items:
        return None
    for exact_name in ("result.json", "evolve.json"):
        for item in items:
            if item.name == exact_name:
                return item
    for marker in ("_evolve", "loopboard_v2", "retry2"):
        for item in items:
            if marker in item.name:
                return item
    for item in items:
        if not item.name.startswith("partial_") and item.name != "skills.json":
            return item
    for item in items:
        return item
    return None


def _maintenance_kind_from_names(*names: str) -> str:
    combined = " ".join(name.lower() for name in names if name)
    if "method_validation" in combined or "sta_" in combined:
        return "method_validation"
    for key in ("exp1", "exp2", "exp3", "medium"):
        if key in combined:
            return key
    return Path(names[0]).stem if names else "unknown"


def _maintenance_suite_dirs() -> List[Path]:
    results_root = REPO_ROOT / "academic" / "results"
    return sorted(
        [
            path
            for path in results_root.glob(f"{MAINTENANCE_RESULTS_PREFIX}*")
            if path.is_dir()
        ],
        reverse=True,
    )


def _method_validation_experiment_meta() -> List[Dict[str, Any]]:
    if not METHOD_VALIDATION_DIR.exists():
        return []
    experiments: List[Dict[str, Any]] = []
    for raw_json in sorted(METHOD_VALIDATION_DIR.glob("*.json"), reverse=True):
        if raw_json.name.endswith(".audit.json"):
            continue
        audit_log = raw_json.with_suffix(".audit.jsonl")
        experiment_id = f"method_validation__{raw_json.stem}"
        experiments.append(
            {
                "id": experiment_id,
                "suite_id": "method_validation",
                "suite_label": "Method Validation",
                "title": raw_json.stem.replace("_", " ").title(),
                "folder_name": raw_json.name,
                "kind": "method_validation",
                "folder_path": str(METHOD_VALIDATION_DIR),
                "result_path": str(raw_json),
                "readme_path": "",
                "suite_readme_path": "",
                "role_log_path": str(audit_log) if audit_log.exists() else "",
                "role_log_exists": audit_log.exists(),
                "role_log_count": len(_load_jsonl(audit_log)) if audit_log.exists() else 0,
            }
        )
    return experiments


def _maintenance_experiment_meta() -> List[Dict[str, Any]]:
    experiments: List[Dict[str, Any]] = []
    for suite_dir in _maintenance_suite_dirs():
        date_token = suite_dir.name.replace(MAINTENANCE_RESULTS_PREFIX, "", 1)
        audit_root = REPO_ROOT / "academic" / "results" / f"{MAINTENANCE_AUDIT_PREFIX}{date_token}"
        full_logs_dir = audit_root / "full_logs"
        suite_readme = suite_dir / "README.md"
        for child in sorted([item for item in suite_dir.iterdir() if item.is_dir()]):
            raw_json = _first_json_file(child)
            if not raw_json:
                continue
            kind = _maintenance_kind_from_names(child.name, raw_json.name)
            role_log = full_logs_dir / f"{kind}_roles.jsonl" if kind.startswith("exp") else None
            role_log_exists = bool(role_log and role_log.exists())
            experiment_id = f"{suite_dir.name}__{child.name}"
            experiments.append(
                {
                    "id": experiment_id,
                    "suite_id": suite_dir.name,
                    "suite_label": suite_dir.name.replace("_", " "),
                    "title": child.name.replace("_", " ").title(),
                    "folder_name": child.name,
                    "kind": kind,
                    "folder_path": str(child),
                    "result_path": str(raw_json),
                    "readme_path": str(child / "README.md") if (child / "README.md").exists() else "",
                    "suite_readme_path": str(suite_readme) if suite_readme.exists() else "",
                    "role_log_path": str(role_log) if role_log else "",
                    "role_log_exists": role_log_exists,
                    "role_log_count": len(_load_jsonl(role_log)) if role_log_exists else 0,
                }
            )
    experiments.extend(_method_validation_experiment_meta())
    return experiments


def _maintenance_lookup(experiment_id: str) -> Dict[str, Any]:
    for item in _maintenance_experiment_meta():
        if item["id"] == experiment_id:
            return item
    raise FileNotFoundError(f"Unknown maintenance experiment: {experiment_id}")


def _maintenance_docs(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    skill_repo_dir = REPO_ROOT / "academic" / "skill_repository"
    candidates = [
        {
            "id": "experiment_readme",
            "title": "Experiment README",
            "kind": "experiment",
            "path": meta.get("readme_path", ""),
        },
        {
            "id": "suite_readme",
            "title": "Suite README",
            "kind": "suite",
            "path": meta.get("suite_readme_path", ""),
        },
        {
            "id": "maintenance_repo_readme",
            "title": "Maintenance Repo README",
            "kind": "reference",
            "path": str(skill_repo_dir / "README.md"),
        },
        {
            "id": "maintenance_architecture",
            "title": "Maintenance Architecture",
            "kind": "reference",
            "path": str(skill_repo_dir / "MAINTENANCE_ARCHITECTURE.md"),
        },
        {
            "id": "maintenance_api_reference",
            "title": "Maintenance API Reference",
            "kind": "reference",
            "path": str(skill_repo_dir / "MAINTENANCE_API_REFERENCE.md"),
        },
    ]
    docs: List[Dict[str, Any]] = []
    for item in candidates:
        text = _read_text_if_exists(item["path"])
        if not text.strip():
            continue
        docs.append({**item, "text": text})
    return docs


def _maintenance_reference_docs() -> List[Dict[str, Any]]:
    skill_repo_dir = REPO_ROOT / "academic" / "skill_repository"
    candidates = [
        {
            "id": "overview",
            "title": "Overview",
            "kind": "reference",
            "path": str(skill_repo_dir / "README.md"),
        },
        {
            "id": "architecture",
            "title": "Architecture",
            "kind": "reference",
            "path": str(skill_repo_dir / "MAINTENANCE_ARCHITECTURE.md"),
        },
        {
            "id": "api_reference",
            "title": "API Reference",
            "kind": "reference",
            "path": str(skill_repo_dir / "MAINTENANCE_API_REFERENCE.md"),
        },
        {
            "id": "method_validation_plan",
            "title": "Method Validation Plan",
            "kind": "test_plan",
            "path": str(skill_repo_dir / "METHOD_VALIDATION_TEST_PLAN.md"),
        },
    ]
    docs: List[Dict[str, Any]] = []
    for item in candidates:
        text = _read_text_if_exists(item["path"])
        if text.strip():
            docs.append({**item, "text": text})
    return docs


MAINTENANCE_DOC_FILES: Dict[str, Path] = {
    "README.md": REPO_ROOT / "academic" / "skill_repository" / "README.md",
    "MAINTENANCE_ARCHITECTURE.md": REPO_ROOT / "academic" / "skill_repository" / "MAINTENANCE_ARCHITECTURE.md",
    "MAINTENANCE_API_REFERENCE.md": REPO_ROOT / "academic" / "skill_repository" / "MAINTENANCE_API_REFERENCE.md",
    "METHOD_VALIDATION_TEST_PLAN.md": REPO_ROOT / "academic" / "skill_repository" / "METHOD_VALIDATION_TEST_PLAN.md",
}


def _maintenance_docs_sidebar() -> str:
    return "\n".join(
        [
            "* [Overview](README.md)",
            "* [Architecture](MAINTENANCE_ARCHITECTURE.md)",
            "* [API Reference](MAINTENANCE_API_REFERENCE.md)",
            "* [Method Validation Plan](METHOD_VALIDATION_TEST_PLAN.md)",
            "",
        ]
    )


def _run_tone(run: Optional[Dict[str, Any]]) -> str:
    run = _run_summary(run)
    if not isinstance(run, dict):
        return "neutral"
    if run.get("official_valid") is False:
        return "danger"
    if run.get("official_valid") is True:
        return "success"
    if run.get("success") is True:
        return "success"
    if run.get("success") is False:
        return "warning"
    return "accent"


def _run_pills(run: Optional[Dict[str, Any]]) -> List[str]:
    run = _run_summary(run)
    if not isinstance(run, dict):
        return []
    pills: List[str] = []
    task_id = run.get("task_id")
    if task_id:
        pills.append(str(task_id))
    if "official_valid" in run:
        pills.append(f"official_valid={run.get('official_valid')}")
    if "call_f1" in run and run.get("call_f1") is not None:
        pills.append(f"call_f1={run.get('call_f1')}")
    if "total_tokens" in run and run.get("total_tokens") is not None:
        pills.append(f"tokens={run.get('total_tokens')}")
    if "n_model_steps" in run and run.get("n_model_steps") is not None:
        pills.append(f"steps={run.get('n_model_steps')}")
    return pills


def _run_metrics(run: Dict[str, Any]) -> Dict[str, Any]:
    run = _run_summary(run)
    keys = [
        "task_id",
        "success",
        "score",
        "official_valid",
        "call_f1",
        "total_tokens",
        "elapsed_s",
        "n_model_steps",
    ]
    return {key: run.get(key) for key in keys if key in run}


def _task_label(task_id: str | None) -> str:
    return str(task_id or "").strip() or "unknown_task"


def _run_summary(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    summary = run.get("summary")
    if isinstance(summary, dict):
        return summary
    return run


def _coerce_run_wrapper(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    summary = _run_summary(run)
    detail = _run_detail(run)
    if summary is run and not detail:
        return {"summary": summary, "detail": {}}
    return {"summary": summary, "detail": detail}


def _run_detail(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    detail = run.get("detail")
    if isinstance(detail, dict):
        return detail
    if "runs" in run and isinstance(run.get("runs"), list):
        return run
    return {}


def _run_result_payload(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    detail = _run_detail(run)
    runs = detail.get("runs") or []
    if runs and isinstance(runs[0], dict):
        return runs[0]
    return {}


def _call_error_summary_items(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    run = _run_summary(run)
    items: List[Dict[str, Any]] = []
    for err in run.get("call_errors") or []:
        error_type = err.get("type", "unknown")
        label = error_type
        detail = ""
        if error_type == "extra_call":
            label = f"Extra {err.get('actual_name', '')}".strip()
            detail = f"turn {err.get('turn_index', '?')}"
        elif error_type == "missing_call":
            label = f"Missing {err.get('expected_name', '')}".strip()
            detail = f"turn {err.get('turn_index', '?')}"
        elif error_type == "argument_mismatch":
            label = f"Arg mismatch {err.get('name', '')}".strip()
            detail = f"turn {err.get('turn_index', '?')}"
        else:
            detail = f"turn {err.get('turn_index', '?')}"
        items.append({"label": label, "detail": detail, "raw": err})
    return items


def _metric_help_text(label: str) -> str:
    docs = {
        "Official": "BFCL official checker 的真假值。True 表示最终工具调用序列和状态更新满足官方验证器。",
        "Call F1": "基于 expected calls 与 actual calls 的调用级 F1，用来分析工具选择和参数匹配质量。",
        "Tokens": "整轮执行消耗的总 token 数，通常是 input + completion。",
        "Elapsed": "该轮执行的端到端 wall-clock 时间，单位秒。",
        "Steps": "模型在该轮执行中一共响应了多少步，通常对应 tool-calling loop 的迭代次数。",
        "Errors": "当前运行中被 scorer 标记出的调用错误数量。",
        "Baseline Valid": "注入故障前，baseline 是否通过 BFCL official checker。",
        "Broken Valid": "注入故障后，该轮是否仍通过 BFCL official checker。",
        "Verify Valid": "维护或修复后重新执行时，是否通过 BFCL official checker。",
        "Maint Tests": "该页关联的 maintenance test result 数量。",
        "Refine Actions": "该页发生的 refiner/store-level 决策数量。",
        "New Skills": "这一轮 extract/store update 后新增的 skill 数量。",
        "Final Skills": "实验结束时 skill store 中可见的 skill 数量。",
        "Warmups": "相关任务预热轮数，用于观察技能积累。",
        "Fault Skill": "手工注入的故障 skill 名称。",
        "Success Rate": "聚合实验中 success=True 的比例。",
        "Official Valid": "聚合实验中 official_valid=True 的比例。",
        "Avg Call F1": "聚合实验中调用级 F1 的平均值。",
        "Avg Precision": "聚合实验中调用级 precision 平均值。",
        "Avg Recall": "聚合实验中调用级 recall 平均值。",
        "Micro Refactors": "当前输出中记录到的 micro-refactor 候选数量。",
        "Integration Cases": "由 integration failure 沉淀回 bundle 的样例数量。",
        "Model": "运行该实验的模型标识。",
        "Skills": "当前实验结果中可见的技能条目数量。",
        "Disabled": "当前被标记为 disabled 的技能数量。",
        "Test Valid": "测试集上的 official_valid_rate。",
        "Experiment": "实验类型标识。",
        "Audit Rows": "该实验加载到的 role-level audit log 行数。",
        "Passed": "探针实验自身定义的通过条件是否满足。",
        "Rounds": "实验包含多少轮/页。",
        "cases": "本次 maintenance test 实际执行的 bundle case 数量。",
        "comparable": "可以做 with-skill / without-skill 对照的 case 数量。",
        "improved": "加入 skill 后指标改善的 case 数量。",
        "regressed": "加入 skill 后指标退化的 case 数量。",
        "pass_all": "当前 bundle 回归是否全部通过。",
        "delta_acc": "with-skill 相对 without-skill 的局部 utility 精度差值。",
        "delta_tokens": "with-skill 相对 without-skill 的 token 开销差值。",
        "delta_steps": "with-skill 相对 without-skill 的 step 开销差值。",
        "Before": "refine/store update 前的 skill version。",
        "After": "refine/store update 后的 skill version。",
        "Regressions": "该决策摘要中记录的回归计数。",
        "Helped": "该决策摘要中记录的帮助计数。",
        "Counterfactual": "是否或如何使用了 with/without 对照证据。",
        "Positive": "bundle 中正例 case 数量。",
        "Negative": "bundle 中反例 case 数量。",
        "Integration": "bundle 中 integration-derived case 数量。",
        "Source Runs": "bundle builder 看到的 source result 数量。",
        "Replay Runs": "bundle builder 看到的 replay result 数量。",
        "Failures": "bundle builder 输入里的 integration failure 数量。",
        "Total": "总数量。",
        "Integration Failures": "带 skill 运行后仍失败、被记录为 integration failure 的样例数。",
    }
    return docs.get(label, f"{label} 指标的具体语义需要结合当前卡片上下文理解。")


def _metric_item(label: str, value: Any, tone: str = "accent") -> Dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "tone": tone,
        "help": _metric_help_text(label),
    }


def _preview_text(value: Any, limit: int = 280) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _compact_list(items: List[Any], limit: int = MAINTENANCE_DETAIL_TRACE_LIMIT) -> List[Any]:
    if len(items) <= limit:
        return items
    return [*items[:limit], {"_truncated_items": len(items) - limit}]


def _compact_debug_event(event: Dict[str, Any]) -> Dict[str, Any]:
    compact = compact_debug_event_for_player(event)
    output = compact.get("output") if isinstance(compact.get("output"), dict) else {}
    input_payload = compact.get("input") if isinstance(compact.get("input"), dict) else {}
    return {
        "event_id": compact.get("event_id", ""),
        "event_type": compact.get("event_type", ""),
        "experiment": compact.get("experiment", ""),
        "loop_index": compact.get("loop_index"),
        "turn_index": compact.get("turn_index"),
        "step_index": compact.get("step_index"),
        "task_id": compact.get("task_id", ""),
        "phase": compact.get("phase", ""),
        "input_summary": _summarize_mapping(input_payload),
        "output_summary": _debug_output_summary(str(compact.get("event_type") or ""), output),
        "metrics": compact.get("metrics") or {},
    }


def _debug_output_summary(event_type: str, output: Dict[str, Any]) -> Dict[str, Any]:
    if not output:
        return {}
    if event_type in {"unit_test_done", "post_refine_test_done"}:
        return {
            "result_id": output.get("result_id"),
            "skill_name": output.get("skill_name"),
            "skill_version": output.get("skill_version"),
            "bundle_version": output.get("bundle_version"),
            "aggregate": output.get("aggregate") or {},
            "n_unit_case_runs": len(output.get("unit_case_runs") or []),
            "n_integration_failures": len(output.get("integration_failures") or []),
        }
    if event_type == "refiner_done":
        return {
            "decisions": [
                {
                    "skill_name": item.get("skill_name"),
                    "action": item.get("action"),
                    "version_kind": item.get("version_kind"),
                    "reason": _preview_text(item.get("reason", ""), 260),
                }
                for item in (output.get("decisions") or [])[:8]
            ],
            "store_after": _store_summary_only(output.get("store_after") or {}),
        }
    if event_type in {"refine_cycle_done", "refine_cycle_round_done"}:
        return {
            "maintenance_targets": output.get("maintenance_targets") or [],
            "n_maintenance_rounds": len(output.get("maintenance_rounds") or []),
            "n_maintenance_test_results": len(output.get("maintenance_test_results") or []),
            "n_post_refine_test_results": len(output.get("post_refine_test_results") or []),
            "n_refine_decisions": len(output.get("refine_decisions") or []),
            "integration_cases_appended": output.get("integration_cases_appended"),
            "n_runner_frames": len(output.get("runner_frames") or []),
            "store_after": _store_summary_only(output.get("store_after") or output.get("skills_after_refine") or {}),
        }
    if "store_after" in output:
        return {**_summarize_mapping({k: v for k, v in output.items() if k != "store_after"}), "store_after": _store_summary_only(output.get("store_after") or {})}
    return _summarize_mapping(output)


def _store_summary_only(store_payload: Dict[str, Any] | List[Any]) -> Dict[str, Any]:
    if isinstance(store_payload, list):
        return {"n_total": len(store_payload), "skill_names": [item.get("name", "") for item in store_payload[:20] if isinstance(item, dict)]}
    if not isinstance(store_payload, dict):
        return {}
    skills = store_payload.get("skills") or []
    return {
        "n_total": store_payload.get("n_total", len(skills)),
        "n_active": store_payload.get("n_active"),
        "n_stale": store_payload.get("n_stale"),
        "n_disabled": store_payload.get("n_disabled"),
        "skill_names": [item.get("name", "") for item in skills[:20] if isinstance(item, dict)],
    }


def _summarize_mapping(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in list(payload.items())[:12]:
        out[str(key)] = _summary_value(value)
    return out


def _summary_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _preview_text(value, 300 if depth == 0 else 180)
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        preview = [_summary_value(item, depth=depth + 1) for item in value[:3]]
        return {"type": "list", "length": len(value), "preview": preview}
    if isinstance(value, dict):
        if depth >= 1:
            return {
                "type": "object",
                "field_count": len(value),
                "keys": [str(key) for key in list(value.keys())[:12]],
            }
        return {
            "type": "object",
            "field_count": len(value),
            "keys": [str(key) for key in list(value.keys())[:12]],
            "preview": {
                str(k): _summary_value(v, depth=depth + 1)
                for k, v in list(value.items())[:4]
            },
        }
    return str(type(value).__name__)


def _executor_detail_block(run: Dict[str, Any]) -> Dict[str, Any]:
    detail = _run_detail(run)
    result_payload = _run_result_payload(run)
    trace = result_payload.get("trace") or {}
    turns = trace.get("turns") or []
    tool_calls = trace.get("tool_calls") or []
    messages = trace.get("messages") or []
    return {
        "available": bool(trace),
        "summary": {
            "n_turns": len(turns),
            "n_tool_calls": len(tool_calls),
            "n_messages": len(messages),
            "n_skill_events": len(trace.get("skill_events") or []),
            "truncated": len(turns) > MAINTENANCE_DETAIL_TRACE_LIMIT
            or len(tool_calls) > MAINTENANCE_DETAIL_TRACE_LIMIT
            or len(messages) > MAINTENANCE_DETAIL_TRACE_LIMIT,
        },
        "turns": _compact_list(turns),
        "tool_calls": _compact_list(tool_calls),
        "messages": _compact_list(messages),
        "skill_events": _compact_list(trace.get("skill_events") or []),
        "task": detail.get("task") or {},
        "raw_trace": "",
        "raw_trace_note": "Full raw trace is available in the source result JSON and state player debug events.",
    }


def _skill_context_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    run = _run_summary(run)
    return {
        "retrieved_skills": run.get("retrieved_skills", []),
        "prompt_injected_skills": run.get("prompt_injected_skills", []),
        "used_skills": run.get("used_skills", []),
    }


def _compact_run_card(run: Dict[str, Any]) -> Dict[str, Any]:
    summary = _run_summary(run)
    return {
        "task_id": summary.get("task_id", ""),
        "official_valid": summary.get("official_valid"),
        "call_f1": summary.get("call_f1"),
        "total_tokens": summary.get("total_tokens"),
        "elapsed_s": summary.get("elapsed_s"),
        "n_model_steps": summary.get("n_model_steps"),
        "retrieved_skills": summary.get("retrieved_skills", []),
        "prompt_injected_skills": summary.get("prompt_injected_skills", []),
        "used_skills": summary.get("used_skills", []),
        "call_errors": _call_error_summary_items(summary),
        "detail": _executor_detail_block(run),
    }


def _aggregate_breakdown(aggregate: Dict[str, Any]) -> List[Dict[str, Any]]:
    report = aggregate.get("unit_utility_report") or {}
    return [
        _metric_item("cases", aggregate.get("n_cases", 0)),
        _metric_item("comparable", aggregate.get("n_comparable_cases", 0)),
        _metric_item("improved", aggregate.get("n_improved", 0), "success"),
        _metric_item("regressed", aggregate.get("n_regressed", 0), "danger"),
        _metric_item("pass_all", aggregate.get("pass_all_tests"), "success" if aggregate.get("pass_all_tests") else "warning"),
        _metric_item("delta_acc", report.get("delta_accuracy")),
        _metric_item("delta_tokens", report.get("delta_tokens")),
        _metric_item("delta_steps", report.get("delta_steps")),
    ]


def _artifact_card(skill: Dict[str, Any]) -> Dict[str, Any]:
    bundle = skill.get("bundle") or {}
    interface = skill.get("interface") or {}
    return {
        "name": skill.get("name", ""),
        "kind": skill.get("kind", ""),
        "description": skill.get("description", ""),
        "status": skill.get("status", "unknown"),
        "version": skill.get("version", 1),
        "version_kind": skill.get("version_kind", ""),
        "stale": bool(skill.get("stale", False)),
        "dependencies": skill.get("dependencies", []),
        "bundle_id": bundle.get("bundle_id", ""),
        "bundle_version": bundle.get("bundle_version"),
        "body": skill.get("body", ""),
        "metadata": copy.deepcopy(skill.get("metadata") or {}),
        "interface": copy.deepcopy(interface),
        "bundle": copy.deepcopy(bundle),
        "evidence": copy.deepcopy(skill.get("evidence") or {}),
        "lineage": copy.deepcopy(skill.get("lineage") or {}),
        "dependency_pins": copy.deepcopy(skill.get("dependency_pins") or []),
        "history": copy.deepcopy(skill.get("history") or []),
        "usage_count": skill.get("usage_count", 0),
        "success_count": skill.get("success_count", 0),
        "bundle_counts": {
            "positive": len(bundle.get("positive_cases") or []),
            "negative": len(bundle.get("negative_cases") or []),
            "integration": len(bundle.get("integration_cases") or []),
        },
        "interface_summary": interface.get("summary", ""),
        "raw": _json_text(_artifact_raw_debug_snapshot(skill)),
    }


def _flow_run_card(title: str, run: Dict[str, Any], *, subtitle: str = "") -> Dict[str, Any]:
    summary = _run_summary(run)
    return {
        "type": "run",
        "title": title,
        "subtitle": subtitle,
        "tone": _run_tone(run),
        "pills": _run_pills(run),
        "run": _compact_run_card(run),
        "detail": {
            "input": {
                "retrieved_skills": summary.get("retrieved_skills", []),
                "prompt_injected_skills": summary.get("prompt_injected_skills", []),
                "used_skills": summary.get("used_skills", []),
            },
            "output": {
                "call_errors": _call_error_summary_items(summary),
                "trace": _executor_detail_block(run),
            },
        },
    }


def _flow_bundle_card(role_audit: Dict[str, Any], *, artifact_name: str = "") -> Dict[str, Any]:
    parsed = role_audit.get("parsed_response_data") or {}
    positive_cases = parsed.get("positive_cases") or []
    negative_cases = parsed.get("negative_cases") or []
    integration_cases = parsed.get("integration_cases") or []
    return {
        "type": "role_bundle_builder",
        "title": "Bundle Builder",
        "subtitle": artifact_name or role_audit.get("metadata", {}).get("artifact_name", ""),
        "tone": "accent",
        "role": role_audit.get("role", "bundle_builder"),
        "metadata": role_audit.get("metadata", {}),
        "maintenance_notes": parsed.get("maintenance_notes", ""),
        "counts": {
            "positive": len(positive_cases),
            "negative": len(negative_cases),
            "integration": len(integration_cases),
        },
        "cases": {
            "positive": positive_cases,
            "negative": negative_cases,
            "integration": integration_cases,
        },
        "user_preview": role_audit.get("user_preview", ""),
        "system": role_audit.get("system", ""),
        "user": role_audit.get("user", ""),
        "raw_response": role_audit.get("raw_response", ""),
        "detail": {
            "input": {
                "metadata": role_audit.get("metadata", {}),
                "system": role_audit.get("system", ""),
                "user": role_audit.get("user", ""),
            },
            "output": {
                "maintenance_notes": parsed.get("maintenance_notes", ""),
                "positive_cases": positive_cases,
                "negative_cases": negative_cases,
                "integration_cases": integration_cases,
                "raw_response": role_audit.get("raw_response", ""),
                "parsed_response": parsed,
            },
        },
    }


def _flow_extractor_card(role_audit: Dict[str, Any]) -> Dict[str, Any]:
    parsed = role_audit.get("parsed_response_data") or {}
    artifacts = parsed.get("artifacts") or []
    first = artifacts[0] if artifacts else {}
    return {
        "type": "role_extractor",
        "title": "Extractor",
        "subtitle": first.get("name", ""),
        "tone": "accent",
        "role": role_audit.get("role", "extractor"),
        "metadata": role_audit.get("metadata", {}),
        "artifact_count": len(artifacts),
        "artifact_preview": {
            "name": first.get("name", ""),
            "kind": first.get("kind", ""),
            "description": first.get("description", ""),
            "version_kind": first.get("version_kind", ""),
            "dependencies": first.get("dependencies", []),
        },
        "user_preview": role_audit.get("user_preview", ""),
        "system": role_audit.get("system", ""),
        "user": role_audit.get("user", ""),
        "raw_response": role_audit.get("raw_response", ""),
        "detail": {
            "input": {
                "metadata": role_audit.get("metadata", {}),
                "system": role_audit.get("system", ""),
                "user": role_audit.get("user", ""),
            },
            "output": {
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
                "raw_response": role_audit.get("raw_response", ""),
                "parsed_response": parsed,
            },
        },
    }


def _flow_refiner_card(role_audit: Dict[str, Any], decision: Dict[str, Any] | None = None) -> Dict[str, Any]:
    parsed = role_audit.get("parsed_response_data") or {}
    decision_payload = parsed.get("decision") or decision or {}
    artifact = parsed.get("artifact") or {}
    bundle = parsed.get("bundle") or {}
    return {
        "type": "role_refiner",
        "title": "Refiner",
        "subtitle": artifact.get("name", ""),
        "tone": "warning" if decision_payload.get("action") == "disable" else "accent",
        "role": role_audit.get("role", "refiner"),
        "metadata": role_audit.get("metadata", {}),
        "decision": {
            "action": decision_payload.get("action", ""),
            "reason": decision_payload.get("reason", ""),
            "version_kind": decision_payload.get("version_kind", ""),
            "migration_reason": decision_payload.get("migration_reason", ""),
            "pinned_dependencies": decision_payload.get("pinned_dependencies", []),
        },
        "artifact_preview": {
            "name": artifact.get("name", ""),
            "description": artifact.get("description", ""),
            "dependencies": artifact.get("dependencies", []),
        },
        "bundle_preview": {
            "positive": len(bundle.get("positive_cases") or []),
            "negative": len(bundle.get("negative_cases") or []),
            "integration": len(bundle.get("integration_cases") or []),
        },
        "user_preview": role_audit.get("user_preview", ""),
        "system": role_audit.get("system", ""),
        "user": role_audit.get("user", ""),
        "raw_response": role_audit.get("raw_response", ""),
        "detail": {
            "input": {
                "metadata": role_audit.get("metadata", {}),
                "system": role_audit.get("system", ""),
                "user": role_audit.get("user", ""),
            },
            "output": {
                "decision": decision_payload,
                "artifact": artifact,
                "bundle": bundle,
                "raw_response": role_audit.get("raw_response", ""),
                "parsed_response": parsed,
            },
        },
    }


def _maintenance_test_card(result: Dict[str, Any]) -> Dict[str, Any]:
    aggregate = result.get("aggregate") or {}
    unit_case_runs = [_normalize_unit_case_run(item) for item in _compact_list(result.get("unit_case_runs") or [])]
    integration_failures = _compact_list(result.get("integration_failures") or [])
    return {
        "type": "maintenance_test",
        "title": "Unit Test",
        "subtitle": result.get("skill_name", ""),
        "tone": "success" if aggregate.get("pass_all_tests") else "warning",
        "aggregate": aggregate,
        "breakdown": _aggregate_breakdown(aggregate),
        "counterfactual": result.get("counterfactual") or {},
        "unit_case_runs": unit_case_runs,
        "integration_failures": integration_failures,
        "skill_name": result.get("skill_name", ""),
        "skill_version": result.get("skill_version"),
        "bundle_version": result.get("bundle_version"),
        "detail": {
            "aggregate": aggregate,
            "counterfactual": result.get("counterfactual") or {},
            "unit_case_runs": unit_case_runs,
            "integration_failures": integration_failures,
            "raw_result": {
                "result_id": result.get("result_id"),
                "skill_name": result.get("skill_name"),
                "skill_version": result.get("skill_version"),
                "bundle_id": result.get("bundle_id"),
                "bundle_version": result.get("bundle_version"),
                "aggregate": aggregate,
                "counterfactual": result.get("counterfactual") or {},
                "raw_note": "Large per-case traces are compacted in /api/maintenance/experiment; use player/debug source JSON for full trace.",
            },
        },
    }


def _summary_from_benchmark_runs(details: List[Dict[str, Any]], *, limit: int = 8) -> Dict[str, Any]:
    """Compact the persisted benchmark run details into monitor-friendly I/O."""
    tasks: List[Dict[str, Any]] = []
    for item in (details or [])[:limit]:
        task = item.get("task") or {}
        run = (item.get("runs") or [{}])[0] or {}
        summary = _run_summary(run)
        questions = task.get("question") or []
        first_question = ""
        for turn in questions:
            if isinstance(turn, list) and turn:
                first_question = str((turn[0] or {}).get("content", ""))
                break
        tasks.append(
            {
                "task_id": item.get("task_id") or task.get("task_id"),
                "turns": len(questions),
                "first_user_message": _preview_text(first_question, 220),
                "expected_calls": task.get("expected") or [],
                "official_valid": summary.get("official_valid"),
                "call_f1": summary.get("call_f1"),
                "total_tokens": summary.get("total_tokens"),
                "retrieved_skills": summary.get("retrieved_skills", []),
                "prompt_injected_skills": summary.get("prompt_injected_skills", []),
                "call_errors": _call_error_summary_items(summary),
            }
        )
    return {
        "n_details": len(details or []),
        "shown": len(tasks),
        "tasks": tasks,
    }


def _skill_algorithm_preview(skill: Dict[str, Any]) -> Dict[str, Any]:
    bundle = skill.get("bundle") or {}
    return {
        "name": skill.get("name", ""),
        "description": skill.get("description", ""),
        "kind": skill.get("kind", ""),
        "status": skill.get("status", "unknown"),
        "version": skill.get("version", 1),
        "body": skill.get("body", ""),
        "interface_summary": (skill.get("interface") or {}).get("summary", ""),
        "intent_keywords": (skill.get("metadata") or {}).get("intent_keywords", []),
        "source_task_ids": (skill.get("metadata") or {}).get("source_task_ids", []),
        "dependencies": skill.get("dependencies", []),
        "bundle_id": bundle.get("bundle_id", ""),
        "bundle_version": bundle.get("bundle_version"),
        "bundle_counts": {
            "positive": len(bundle.get("positive_cases") or []),
            "negative": len(bundle.get("negative_cases") or []),
            "integration": len(bundle.get("integration_cases") or []),
        },
    }


def _bundle_case_preview(case: Dict[str, Any]) -> Dict[str, Any]:
    expected = case.get("expected") or {}
    context = case.get("context") or {}
    fragment = context.get("task_fragment") or {}
    return {
        "case_id": case.get("case_id", ""),
        "source": case.get("source", ""),
        "prompt": _preview_text(case.get("prompt") or _extract_prompt_from_fragment(fragment), 240),
        "polarity": case.get("polarity") or case.get("metadata", {}).get("polarity", ""),
        "expected_tool_calls": expected.get("tool_calls") or expected.get("expected_calls") or fragment.get("expected") or [],
        "contrast_protocol": case.get("contrast_protocol") or {},
        "source_task_id": context.get("source_task_id") or context.get("task_id") or "",
    }


def _extract_prompt_from_fragment(fragment: Dict[str, Any]) -> str:
    question = fragment.get("question") or []
    for turn in question:
        if isinstance(turn, list) and turn:
            return str((turn[0] or {}).get("content", ""))
    return ""


def _bundle_algorithm_preview(name: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    positive = bundle.get("positive_cases") or []
    negative = bundle.get("negative_cases") or []
    integration = bundle.get("integration_cases") or []
    return {
        "skill_name": name,
        "bundle_id": bundle.get("bundle_id", ""),
        "bundle_version": bundle.get("bundle_version"),
        "counts": {
            "positive": len(positive),
            "negative": len(negative),
            "integration": len(integration),
        },
        "positive_cases": [_bundle_case_preview(item) for item in positive[:4]],
        "negative_cases": [_bundle_case_preview(item) for item in negative[:4]],
        "integration_cases": [_bundle_case_preview(item) for item in integration[:4]],
        "contrast_protocol": bundle.get("contrast_protocol") or {
            "with_skill": True,
            "without_skill": True,
        },
    }


def _algorithm_card(
    *,
    card_type: str,
    title: str,
    role: str,
    subtitle: str,
    tone: str,
    input_summary: str,
    output_summary: str,
    metrics: List[Dict[str, Any]],
    input_payload: Dict[str, Any],
    output_payload: Dict[str, Any],
    debug_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "type": card_type,
        "title": title,
        "role": role,
        "subtitle": subtitle,
        "tone": tone,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "metrics": metrics,
        "detail": {
            "input": input_payload,
            "output": output_payload,
            "debug_raw": debug_payload or {},
        },
    }


def _algorithm_monitor_cards(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    skills = payload.get("skills") or []
    bundles = payload.get("skill_bundles") or {}
    train_summary = payload.get("train_summary") or {}
    replay_summary = payload.get("refine_summary_before") or {}
    test_results = payload.get("maintenance_test_results") or []
    refine_decisions = payload.get("refine_decisions") or []
    skill_previews = [_skill_algorithm_preview(skill) for skill in skills]
    bundle_previews = [
        _bundle_algorithm_preview(name, bundle)
        for name, bundle in bundles.items()
        if isinstance(bundle, dict)
    ]
    cards: List[Dict[str, Any]] = [
        _algorithm_card(
            card_type="algorithm_executor",
            title="Train Executor",
            role="Executor",
            subtitle="run train tasks from the current skill store",
            tone="accent",
            input_summary=f"{payload.get('n_train', train_summary.get('n_tasks', 0))} train tasks, top_k={payload.get('top_k_skills', '—')}",
            output_summary=(
                f"official_valid={train_summary.get('official_valid_rate')}, "
                f"avg_f1={_avg_call_quality(train_summary)}"
            ),
            metrics=[
                _metric_item("Tasks", train_summary.get("n_tasks", payload.get("n_train")), "accent"),
                _metric_item("Official", train_summary.get("official_valid_rate"), "success"),
                _metric_item("Avg F1", _avg_call_quality(train_summary), "accent"),
                _metric_item("Avg Tokens", train_summary.get("avg_total_tokens"), "warning"),
            ],
            input_payload={
                "benchmark": payload.get("benchmark"),
                "model_name": payload.get("model_name"),
                "execution_backend": payload.get("execution_backend"),
                "skill_store_before": {
                    "n_seed_skills": payload.get("n_skills_seed"),
                    "top_k_skills": payload.get("top_k_skills"),
                    "skill_injection_mode": payload.get("skill_injection_mode"),
                },
                "tasks": _summary_from_benchmark_runs(payload.get("train_details") or []),
            },
            output_payload={
                "train_summary": train_summary,
                "train_run_details": _summary_from_benchmark_runs(payload.get("train_details") or []),
            },
            debug_payload={"raw_train_summary": train_summary},
        ),
        _algorithm_card(
            card_type="algorithm_extractor",
            title="Skill Extractor",
            role="Extractor",
            subtitle="reconstructed from persisted skill artifacts",
            tone="success" if skills else "warning",
            input_summary="Consumes train traces and failure/success evidence.",
            output_summary=f"produced {len(skills)} skill artifacts",
            metrics=[
                _metric_item("Skills", len(skills), "success"),
                _metric_item("Audit Rows", 0, "warning"),
            ],
            input_payload={
                "trace_scope": _summary_from_benchmark_runs(payload.get("train_details") or [], limit=10),
                "audit_note": (
                    "This run did not persist extractor prompt/response audit rows; "
                    "the card reconstructs extractor output from result.json skills."
                ),
            },
            output_payload={"skills": skill_previews},
            debug_payload={"raw_skills": skills},
        ),
        _algorithm_card(
            card_type="algorithm_bundle_builder",
            title="Bundle Builder",
            role="Bundle Builder",
            subtitle="build unittest-like with/without skill cases",
            tone="accent" if bundle_previews else "warning",
            input_summary=f"Consumes {len(skills)} skill artifacts and their evidence.",
            output_summary=f"built {len(bundle_previews)} bundles",
            metrics=[
                _metric_item("Bundles", len(bundle_previews), "accent"),
                _metric_item("Positive", sum(item["counts"]["positive"] for item in bundle_previews), "success"),
                _metric_item("Negative", sum(item["counts"]["negative"] for item in bundle_previews), "warning"),
                _metric_item("Integration", sum(item["counts"]["integration"] for item in bundle_previews), "accent"),
            ],
            input_payload={
                "skills": skill_previews,
                "integration_cases_appended": payload.get("integration_cases_appended", 0),
                "audit_note": (
                    "This run did not persist bundle-builder prompt/response audit rows; "
                    "the card shows persisted bundle artifacts."
                ),
            },
            output_payload={"bundles": bundle_previews},
            debug_payload={"raw_skill_bundles": bundles},
        ),
        _algorithm_card(
            card_type="algorithm_replay",
            title="Integration Replay",
            role="Executor",
            subtitle="rerun train tasks with evolved skills",
            tone="success" if replay_summary.get("official_valid_rate", 0) >= train_summary.get("official_valid_rate", 0) else "warning",
            input_summary=f"{len(skills)} evolved skills injected/retrieved on train replay.",
            output_summary=(
                f"official_valid={replay_summary.get('official_valid_rate')}, "
                f"avg_f1={_avg_call_quality(replay_summary)}"
            ),
            metrics=[
                _metric_item("Replay Official", replay_summary.get("official_valid_rate"), "success"),
                _metric_item("Replay F1", _avg_call_quality(replay_summary), "accent"),
                _metric_item("Replay Tokens", replay_summary.get("avg_total_tokens"), "warning"),
                _metric_item("Delta Official", _numeric_delta(replay_summary.get("official_valid_rate"), train_summary.get("official_valid_rate")), "accent"),
            ],
            input_payload={
                "skill_store": skill_previews,
                "replay_tasks": _summary_from_benchmark_runs(payload.get("refine_details") or [], limit=10),
            },
            output_payload={
                "replay_summary": replay_summary,
                "replay_run_details": _summary_from_benchmark_runs(payload.get("refine_details") or [], limit=10),
            },
            debug_payload={"raw_refine_summary_before": replay_summary},
        ),
    ]
    cards.extend(_maintenance_test_card(item) for item in test_results)
    if refine_decisions:
        cards.append(
            _algorithm_card(
                card_type="algorithm_refiner",
                title="Refiner",
                role="Refiner",
                subtitle="decide whether each skill needs modification",
                tone="warning",
                input_summary=f"Consumes {len(test_results)} unit utility reports.",
                output_summary=", ".join(
                    f"{item.get('skill_name')}:{item.get('action')}" for item in refine_decisions[:4]
                ),
                metrics=[
                    _metric_item("Decisions", len(refine_decisions), "warning"),
                    _metric_item("Keep", sum(1 for item in refine_decisions if item.get("action") == "keep"), "success"),
                    _metric_item("Modify", sum(1 for item in refine_decisions if item.get("action") not in ("keep", None)), "warning"),
                ],
                input_payload={
                    "maintenance_test_results": [
                        {
                            "skill_name": item.get("skill_name"),
                            "skill_version": item.get("skill_version"),
                            "bundle_version": item.get("bundle_version"),
                            "aggregate": item.get("aggregate"),
                        }
                        for item in test_results
                    ],
                    "audit_note": (
                        "This run did not persist refiner prompt/response audit rows; "
                        "the card shows persisted refine decisions."
                    ),
                },
                output_payload={"refine_decisions": refine_decisions},
                debug_payload={"raw_refine_decisions": refine_decisions},
            )
        )
    cards.append(
        _algorithm_card(
            card_type="algorithm_store",
            title="Skill Store",
            role="Skill Store",
            subtitle="final repository state after maintenance",
            tone="success",
            input_summary="Consumes accepted skill and refine outputs.",
            output_summary=f"{len(skills)} active artifacts in the final store",
            metrics=[
                _metric_item("Final Skills", len(skills), "success"),
                _metric_item("Disabled", sum(1 for item in skills if item.get("status") == "disabled"), "warning"),
                _metric_item("Bundles", len(bundle_previews), "accent"),
            ],
            input_payload={
                "accepted_skills": [item.get("name") for item in skills],
                "refine_decisions": refine_decisions,
            },
            output_payload={"final_skills": skill_previews, "bundles": bundle_previews},
            debug_payload={"raw_skills": skills, "raw_bundles": bundles},
        )
    )
    return cards


def _numeric_delta(new_value: Any, old_value: Any) -> Any:
    try:
        return round(float(new_value) - float(old_value), 4)
    except Exception:
        return None


def _avg_call_quality(summary: Dict[str, Any]) -> Any:
    return summary.get("avg_call_f1", summary.get("avg_score"))


def _normalize_unit_case_run(run: Any) -> Any:
    if not isinstance(run, dict):
        return run
    row = copy.deepcopy(run)
    has_io_payload = any(row.get(key) for key in ("input_payload", "expected_behavior", "actual_output", "tool_calls", "trace_summary"))
    row["io_available"] = bool(has_io_payload)
    if not has_io_payload:
        row["io_unavailable_reason"] = (
            "This historical test result only persisted pass/fail metrics. "
            "Rerun the experiment with the current logger to capture per-case role input/output."
        )
    return row


def _method_case_card(payload: Dict[str, Any], role_audit: List[Dict[str, Any]]) -> Dict[str, Any]:
    role_calls = payload.get("role_calls") or {}
    stale_call = (role_calls.get("stale_resolver") or {})
    post = payload.get("post_resolution") or {}
    assertions = payload.get("assertions") or {}
    retrieval = payload.get("retrieval_audit") or {}
    setup = payload.get("setup") or {}
    resolved_skill = post.get("resolved_skill") or {}
    test_result = post.get("test_result") or {}
    stale_output = stale_call.get("output_payload") or {}
    audit_rows = role_audit or []
    given = {
        "query": payload.get("query", ""),
        "setup": setup,
        "expected": payload.get("expected") or {},
        "skills_before": setup.get("skills_before_resolution") or [],
    }
    algorithm_output = {
        "retrieval_audit": retrieval,
        "role_calls": role_calls,
        "post_resolution": post,
        "selected_skills": retrieval.get("selected") or [],
        "candidate_scores": retrieval.get("candidates") or [],
        "store_summary": retrieval.get("store_summary") or {},
        "resolved_skill": resolved_skill,
        "test_result": test_result,
    }
    model_output = {
        "stale_resolver": stale_output,
        "audit_rows": audit_rows,
        "role_io": {
            "stale_resolver": {
                "system": audit_rows[0].get("system", "") if audit_rows else "",
                "user": audit_rows[0].get("user", "") if audit_rows else "",
                "raw_response": audit_rows[0].get("raw_response", "") if audit_rows else "",
                "parsed_output": stale_output,
            }
        },
    }
    return {
        "type": "method_case",
        "title": payload.get("case_id", "Method Case"),
        "subtitle": payload.get("query", ""),
        "tone": "success" if payload.get("passed") else "danger",
        "case_id": payload.get("case_id", ""),
        "passed": bool(payload.get("passed")),
        "given": given,
        "algorithm_output": algorithm_output,
        "model_output": model_output,
        "assertions": assertions,
        "view_model": {
            "given_summary": {
                "query": payload.get("query", ""),
                "n_setup_skills": len(setup.get("skills_before_resolution") or []),
                "expected_behavior": payload.get("expected") or {},
            },
            "retrieval_summary": {
                "top_k": retrieval.get("top_k"),
                "store_summary": retrieval.get("store_summary") or {},
                "selected": retrieval.get("selected") or [],
                "candidates": retrieval.get("candidates") or [],
            },
            "role_summary": {
                "stale_resolver_action": stale_output.get("action", ""),
                "stale_resolver_reason": stale_output.get("reason", ""),
                "pinned_dependencies": stale_output.get("pinned_dependencies") or [],
                "artifact_updates": stale_output.get("artifact_updates") or {},
            },
            "artifact_summary": {
                "resolved_name": resolved_skill.get("name", ""),
                "resolved_version": resolved_skill.get("version"),
                "resolved_status": resolved_skill.get("status", ""),
                "resolved_version_kind": resolved_skill.get("version_kind", ""),
                "resolved_body": resolved_skill.get("body", ""),
                "resolved_interface": resolved_skill.get("interface") or {},
            },
            "test_summary": test_result,
        },
        "detail": {
            "given": given,
            "model_output": model_output,
            "algorithm_output": algorithm_output,
            "assertions": assertions,
            "raw_result": payload,
        },
    }


def _method_validation_pages(payload: Dict[str, Any], role_audit: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    card = _method_case_card(payload, role_audit)
    assertions = payload.get("assertions") or {}
    page = _round_page(
        page_id=str(payload.get("case_id") or "method_case"),
        label=str(payload.get("case_id") or "Method Case"),
        title=f"{payload.get('case_id', 'Method Case')} | {payload.get('query', '')}",
        status_tone="success" if payload.get("passed") else "danger",
        summary_metrics=[
            _metric_item("Passed", bool(payload.get("passed")), "success" if payload.get("passed") else "danger"),
            _metric_item("Assertions", f"{sum(1 for value in assertions.values() if value)}/{len(assertions)}", "accent"),
            _metric_item("Role Calls", len((payload.get("role_calls") or {})), "accent"),
            _metric_item("Audit Rows", len(role_audit), "accent"),
        ],
        flow_cards=[card],
    )
    page["subtitle"] = payload.get("query", "")
    return [page]


def _refine_summary_card(decision: Dict[str, Any]) -> Dict[str, Any]:
    action = decision.get("action", "unknown")
    return {
        "type": "refine_decision",
        "title": "Refine Decision",
        "subtitle": decision.get("skill_name", ""),
        "tone": "danger" if "disable" in action else "accent",
        "action": action,
        "skill_name": decision.get("skill_name", ""),
        "version_before": decision.get("version_before"),
        "version_after": decision.get("version_after"),
        "failed_count": decision.get("failed_count", 0),
        "helped_count": decision.get("helped_count", 0),
        "regression_task_ids": decision.get("regression_task_ids", []),
        "counterfactual_task_ids": decision.get("counterfactual_task_ids", []),
        "used_counterfactual_evidence": decision.get("used_counterfactual_evidence"),
        "detail": {
            "raw_decision": decision,
        },
    }


def _role_audit_entries(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    role_entries = _load_jsonl(Path(meta["role_log_path"])) if meta.get("role_log_exists") else []
    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(role_entries):
        parsed = item.get("parsed_response")
        rows.append(
            {
                "index": idx + 1,
                "role": item.get("role", "unknown"),
                "ts": item.get("ts", ""),
                "llm_config": item.get("llm_config", ""),
                "model_name": item.get("model_name", ""),
                "metadata": item.get("metadata", {}),
                "user_preview": str(item.get("user", "")).strip()[:240],
                "system": item.get("system", ""),
                "user": item.get("user", ""),
                "raw_response": item.get("raw_response", ""),
                "parsed_response_data": parsed if isinstance(parsed, dict) else {},
            }
        )
    return rows


def _group_role_audit_by_loop(role_audit: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for entry in role_audit:
        metadata = dict(entry.get("metadata") or {})
        loop_index = metadata.get("loop_index")
        if loop_index is None:
            continue
        try:
            loop_key = int(loop_index)
        except Exception:
            continue
        grouped.setdefault(loop_key, []).append(entry)
    return grouped


def _fallback_role_group(role_audit: List[Dict[str, Any]], idx: int) -> List[Dict[str, Any]]:
    if not role_audit:
        return []
    group_size = 3
    start = idx * group_size
    end = start + group_size
    return role_audit[start:end]


def _overview_metrics(summary_cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return summary_cards


def _round_page(
    *,
    page_id: str,
    label: str,
    title: str,
    status_tone: str,
    summary_metrics: List[Dict[str, Any]],
    flow_cards: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "page_id": page_id,
        "label": label,
        "title": title,
        "status_tone": status_tone,
        "summary_metrics": summary_metrics,
        "flow_cards": flow_cards,
    }


def _debug_event_card(event: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(event.get("event_type") or "debug_event")
    tone = "accent"
    if "exception" in event_type or "error" in event_type:
        tone = "danger"
    elif "retrieval" in event_type:
        tone = "warning"
    return {
        "type": "debug_event",
        "title": event_type.replace("_", " ").title(),
        "subtitle": str(event.get("event_id") or ""),
        "tone": tone,
        "event": _compact_debug_event(event),
        "detail": {
            "input": _summarize_mapping(event.get("input", {}) if isinstance(event.get("input"), dict) else {}),
            "output": _summarize_mapping(event.get("output", {}) if isinstance(event.get("output"), dict) else {}),
            "metrics": event.get("metrics", {}),
            "raw_event": {
                **_compact_debug_event(event),
                "raw_note": "Full event payload is available through /api/maintenance/player and source result JSON.",
            },
        },
    }


def _debug_cards_for_loop(payload: Dict[str, Any], loop: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = payload.get("debug_events") or []
    refs = set(loop.get("debug_event_refs") or [])
    selected = [event for event in events if event.get("event_id") in refs]
    if not selected and events:
        loop_index = loop.get("loop_index")
        selected = [event for event in events if event.get("loop_index") == loop_index]
    return [_debug_event_card(event) for event in selected]


def _exp1_pages(payload: Dict[str, Any], role_audit: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rounds = payload.get("loops", []) or payload.get("rounds", [])
    pages: List[Dict[str, Any]] = []
    role_groups = _group_role_audit_by_loop(role_audit)
    for idx, round_item in enumerate(rounds):
        result = _coerce_run_wrapper(
            round_item.get("run")
            or round_item.get("pre_run")
            or round_item.get("result")
        )
        evolve = round_item.get("evolve") or {}
        refine = round_item.get("refine") or {}
        flow_cards: List[Dict[str, Any]] = [
            _flow_run_card(
                "Executor",
                result,
                subtitle=f"skills_before={round_item.get('n_skills_before', 0)}",
            ),
        ]
        group = role_groups.get(int(round_item.get("loop_index", idx)), _fallback_role_group(role_audit, idx))
        for entry in group:
            if entry["role"] == "extractor":
                flow_cards.append(_flow_extractor_card(entry))
            elif entry["role"] == "bundle_builder":
                flow_cards.append(_flow_bundle_card(entry))
            elif entry["role"] == "refiner":
                flow_cards.append(_flow_refiner_card(entry))
        for maintenance_round in refine.get("maintenance_rounds") or []:
            for item in maintenance_round.get("maintenance_test_results") or []:
                flow_cards.append(_maintenance_test_card(item))
            for item in maintenance_round.get("refine_decisions") or []:
                flow_cards.append(_refine_summary_card(item))
            for item in maintenance_round.get("post_refine_test_results") or []:
                flow_cards.append(_maintenance_test_card(item))
        flow_cards.append(
            {
                "type": "skill_delta",
                "title": "Skill Store Update",
                "subtitle": f"after round {round_item.get('loop_index', round_item.get('round_index', idx))}",
                "tone": "accent",
                "new_skill_names": evolve.get("new_skill_names", []),
                "n_skills_after": evolve.get("n_skills_after", 0),
                "skill_names_after": evolve.get("skill_names_after", []),
            }
        )
        pages.append(
            _round_page(
                page_id=f"round_{idx}",
                label=f"Round {idx}",
                title=f"Round {idx} | {_task_label(_run_summary(result).get('task_id'))}",
                status_tone=_run_tone(result),
                summary_metrics=[
                    _metric_item("Official", _run_summary(result).get("official_valid"), _run_tone(result)),
                    _metric_item("Call F1", _run_summary(result).get("call_f1"), "accent"),
                    _metric_item("Tokens", _run_summary(result).get("total_tokens"), "accent"),
                    _metric_item("Steps", _run_summary(result).get("n_model_steps"), "accent"),
                    _metric_item("New Skills", len(evolve.get("new_skill_names", [])), "success"),
                ],
                flow_cards=flow_cards,
            )
        )
    return pages


def _exp2_pages(payload: Dict[str, Any], role_audit: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    loops = payload.get("loops") or []
    pages: List[Dict[str, Any]] = []
    role_groups = _group_role_audit_by_loop(role_audit)
    for idx, loop in enumerate(loops):
        run = _coerce_run_wrapper(loop.get("run"))
        refine = loop.get("refine") or {}
        flow_cards: List[Dict[str, Any]] = [
            _flow_run_card(loop.get("label") or "Executor", run, subtitle=loop.get("kind", "")),
        ]
        if loop.get("fault_injection"):
            flow_cards.append(
                {
                    "type": "skill_delta",
                    "title": "Fault Injection",
                    "subtitle": loop.get("fault_injection", {}).get("skill_name", ""),
                    "tone": "warning",
                    "new_skill_names": [loop.get("fault_injection", {}).get("skill_name", "")],
                    "n_skills_after": (loop.get("store_state") or {}).get("n_skills_after", 0),
                    "skill_names_after": (loop.get("store_state") or {}).get("skills_after", []),
                }
            )
        for entry in role_groups.get(int(loop.get("loop_index", idx)), _fallback_role_group(role_audit, idx)):
            if entry["role"] == "bundle_builder":
                flow_cards.append(_flow_bundle_card(entry, artifact_name=(loop.get("fault_injection") or {}).get("skill_name", "")))
            elif entry["role"] == "refiner":
                flow_cards.append(_flow_refiner_card(entry))
        for maintenance_round in refine.get("maintenance_rounds") or []:
            for item in maintenance_round.get("maintenance_test_results") or []:
                flow_cards.append(_maintenance_test_card(item))
            for item in maintenance_round.get("refine_decisions") or []:
                flow_cards.append(_refine_summary_card(item))
            for item in maintenance_round.get("post_refine_test_results") or []:
                flow_cards.append(_maintenance_test_card(item))
        metrics = [
            _metric_item("Official", _run_summary(run).get("official_valid"), _run_tone(run)),
            _metric_item("Call F1", _run_summary(run).get("call_f1"), "accent"),
            _metric_item("Tokens", _run_summary(run).get("total_tokens"), "accent"),
        ]
        if refine:
            metrics.append(_metric_item("Maint Tests", len(refine.get("maintenance_test_results") or []), "accent"))
            metrics.append(_metric_item("Refine Actions", len(refine.get("refine_decisions") or []), "accent"))
        pages.append(
            _round_page(
                page_id=f"round_{idx}",
                label=loop.get("label") or f"Round {idx}",
                title=f"{loop.get('label') or f'Round {idx}'} | {_task_label(_run_summary(run).get('task_id'))}",
                status_tone=_run_tone(run),
                summary_metrics=metrics,
                flow_cards=flow_cards,
            )
        )
    return pages


def _exp3_pages(payload: Dict[str, Any], role_audit: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    loops = payload.get("loops") or []
    pages: List[Dict[str, Any]] = []
    role_groups = _group_role_audit_by_loop(role_audit)
    for idx, loop in enumerate(loops):
        run = _coerce_run_wrapper(loop.get("run") or loop.get("result"))
        evolve = loop.get("evolve") or {}
        refine = loop.get("refine") or {}
        flow_cards: List[Dict[str, Any]] = [
            _flow_run_card(loop.get("label") or "Executor", run, subtitle=f"skills_before={loop.get('n_skills_before', 0)}"),
        ]
        if loop.get("fault_baseline_run"):
            flow_cards.insert(0, _flow_run_card("Fault Baseline", _coerce_run_wrapper(loop.get("fault_baseline_run")), subtitle="before fault injection"))
        for entry in role_groups.get(int(loop.get("loop_index", idx)), _fallback_role_group(role_audit, idx)):
            if entry["role"] == "extractor":
                flow_cards.append(_flow_extractor_card(entry))
            elif entry["role"] == "bundle_builder":
                flow_cards.append(_flow_bundle_card(entry, artifact_name=(loop.get("fault_injection") or {}).get("skill_name", "")))
            elif entry["role"] == "refiner":
                flow_cards.append(_flow_refiner_card(entry))
        for maintenance_round in refine.get("maintenance_rounds") or []:
            for item in maintenance_round.get("maintenance_test_results") or []:
                flow_cards.append(_maintenance_test_card(item))
            for item in maintenance_round.get("refine_decisions") or []:
                flow_cards.append(_refine_summary_card(item))
            for item in maintenance_round.get("post_refine_test_results") or []:
                flow_cards.append(_maintenance_test_card(item))
        if evolve:
            flow_cards.append(
                {
                    "type": "skill_delta",
                    "title": "Skill Store Update",
                    "subtitle": loop.get("label", ""),
                    "tone": "accent",
                    "new_skill_names": evolve.get("new_skill_names", []),
                    "n_skills_after": evolve.get("n_skills_after", 0),
                    "skill_names_after": evolve.get("skill_names_after", []),
                }
            )
        flow_cards.extend(_debug_cards_for_loop(payload, loop))
        metrics = [
            _metric_item("Official", _run_summary(run).get("official_valid"), _run_tone(run)),
            _metric_item("Call F1", _run_summary(run).get("call_f1"), "accent"),
            _metric_item("Tokens", _run_summary(run).get("total_tokens"), "accent"),
        ]
        if evolve:
            metrics.append(_metric_item("New Skills", len(evolve.get("new_skill_names", [])), "success"))
        if refine:
            metrics.append(_metric_item("Maint Tests", len(refine.get("maintenance_test_results") or []), "accent"))
            metrics.append(_metric_item("Refine Actions", len(refine.get("refine_decisions") or []), "accent"))
        if loop.get("debug_event_refs"):
            metrics.append(_metric_item("Debug Events", len(loop.get("debug_event_refs") or []), "accent"))
        pages.append(
            _round_page(
                page_id=f"round_{idx}",
                label=loop.get("label") or f"Round {idx}",
                title=f"{loop.get('label') or f'Round {idx}'} | {_task_label(_run_summary(run).get('task_id'))}",
                status_tone=_run_tone(run),
                summary_metrics=metrics,
                flow_cards=flow_cards,
            )
        )
    return pages


def _medium_pages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        _round_page(
            page_id="algorithm",
            label="Algorithm",
            title="Algorithm Monitor",
            status_tone="accent",
            summary_metrics=[
                {"label": "Train Official", "value": (payload.get("train_summary") or {}).get("official_valid_rate"), "tone": "success"},
                {"label": "Replay Official", "value": (payload.get("refine_summary_before") or {}).get("official_valid_rate"), "tone": "success"},
                {"label": "Skills", "value": len(payload.get("skills") or []), "tone": "success"},
                {"label": "Unit Tests", "value": len(payload.get("maintenance_test_results") or []), "tone": "accent"},
            ],
            flow_cards=_algorithm_monitor_cards(payload),
        ),
        _round_page(
            page_id="train",
            label="Train",
            title="Train Summary",
            status_tone="accent",
            summary_metrics=[
                {"label": "Success Rate", "value": (payload.get("train_summary") or {}).get("success_rate"), "tone": "accent"},
                {"label": "Official Valid", "value": (payload.get("train_summary") or {}).get("official_valid_rate"), "tone": "success"},
                {"label": "Avg Call F1", "value": _avg_call_quality(payload.get("train_summary") or {}) or "—", "tone": "accent"},
                {"label": "Skills", "value": len(payload.get("skills") or []), "tone": "success"},
            ],
            flow_cards=[
                {
                    "type": "summary_board",
                    "title": "Train Metrics",
                    "subtitle": "aggregate over training tasks",
                    "tone": "accent",
                    "metrics": payload.get("train_summary") or {},
                }
            ],
        ),
        _round_page(
            page_id="refine",
            label="Refine",
            title="Refine Board",
            status_tone="warning",
            summary_metrics=[
                {"label": "Maintenance Tests", "value": len(payload.get("maintenance_test_results") or []), "tone": "accent"},
                {"label": "Refine Actions", "value": len(payload.get("refine_decisions") or []), "tone": "warning"},
                {"label": "Integration Cases", "value": payload.get("integration_cases_appended", 0), "tone": "accent"},
                {"label": "Micro Refactors", "value": len(payload.get("micro_refactor_candidates") or []), "tone": "accent"},
            ],
            flow_cards=
            [_maintenance_test_card(item) for item in (payload.get("maintenance_test_results") or [])]
            + [_refine_summary_card(item) for item in (payload.get("refine_decisions") or [])],
        ),
        _round_page(
            page_id="test",
            label="Test",
            title="Test Summary",
            status_tone="success",
            summary_metrics=[
                {"label": "Success Rate", "value": (payload.get("test_summary") or {}).get("success_rate"), "tone": "accent"},
                {"label": "Official Valid", "value": (payload.get("test_summary") or {}).get("official_valid_rate"), "tone": "success"},
                {"label": "Avg Precision", "value": (payload.get("test_summary") or {}).get("avg_call_precision"), "tone": "accent"},
                {"label": "Avg Recall", "value": (payload.get("test_summary") or {}).get("avg_call_recall"), "tone": "accent"},
            ],
            flow_cards=[
                {
                    "type": "summary_board",
                    "title": "Test Metrics",
                    "subtitle": "aggregate over held-out tasks",
                    "tone": "success",
                    "metrics": payload.get("test_summary") or {},
                }
            ],
        ),
    ]


def _maintenance_detail_from_payload(meta: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    kind = meta["kind"]
    docs = _maintenance_docs(meta)
    readme_text = docs[0]["text"] if docs else ""
    role_audit = _role_audit_entries(meta)
    files = {
        "result_json": meta["result_path"],
        "readme": meta["readme_path"],
        "suite_readme": meta["suite_readme_path"],
        "role_log": meta["role_log_path"],
    }
    artifacts: List[Dict[str, Any]] = []
    summary_cards: List[Dict[str, Any]] = [
        {"label": "Experiment", "value": kind.upper(), "tone": "accent"},
        {"label": "Audit Rows", "value": str(len(role_audit)), "tone": "accent"},
    ]
    subtitle = ""
    pages: List[Dict[str, Any]] = []

    if kind == "method_validation":
        artifacts = []
        assertions = payload.get("assertions") or {}
        summary_cards.extend(
            [
                {
                    "label": "Passed",
                    "value": str(bool(payload.get("passed"))),
                    "tone": "success" if payload.get("passed") else "danger",
                },
                {
                    "label": "Case",
                    "value": str(payload.get("case_id", "")),
                    "tone": "accent",
                },
                {
                    "label": "Assertions",
                    "value": f"{sum(1 for value in assertions.values() if value)}/{len(assertions)}",
                    "tone": "accent",
                },
                {
                    "label": "Role Calls",
                    "value": str(len(payload.get("role_calls") or {})),
                    "tone": "accent",
                },
            ]
        )
        subtitle = str(payload.get("query", "method validation case"))
        pages = _method_validation_pages(payload, role_audit)
    elif kind == "exp1":
        rounds = payload.get("loops", []) or payload.get("rounds", [])
        artifacts = [_artifact_card(skill) for skill in payload.get("final_skills", [])]
        summary_cards.extend(
            [
                {
                    "label": "Passed",
                    "value": str(bool(payload.get("passed"))),
                    "tone": "success" if payload.get("passed") else "danger",
                },
                {
                    "label": "Rounds",
                    "value": str(len(rounds)),
                    "tone": "accent",
                },
                {
                    "label": "Final Skills",
                    "value": str(len(artifacts)),
                    "tone": "success",
                },
            ]
        )
        subtitle = f"Task {payload.get('task_id', '')} | repeated from empty store"
        pages = _exp1_pages(payload, role_audit)
    elif kind == "exp2":
        artifacts = [
            _artifact_card(skill)
            for skill in (payload.get("refine") or {}).get("skills_after_refine", [])
        ]
        broken_run = _coerce_run_wrapper(payload.get("broken_run") or {})
        verify_run = _coerce_run_wrapper(payload.get("verify_run") or {})
        summary_cards.extend(
            [
                {
                    "label": "Passed",
                    "value": str(bool(payload.get("passed"))),
                    "tone": "success" if payload.get("passed") else "danger",
                },
                {
                    "label": "Broken Valid",
                    "value": str(_run_summary(broken_run).get("official_valid")),
                    "tone": "danger",
                },
                {
                    "label": "Verify Valid",
                    "value": str(_run_summary(verify_run).get("official_valid")),
                    "tone": "success"
                    if _run_summary(verify_run).get("official_valid")
                    else "warning",
                },
                {
                    "label": "Maint Tests",
                    "value": str(len((payload.get("refine") or {}).get("maintenance_test_results", []))),
                    "tone": "accent",
                },
            ]
        )
        subtitle = f"Task {payload.get('task_id', '')} | fault injection and repair"
        pages = _exp2_pages(payload, role_audit)
    elif kind == "exp3":
        artifacts = [_artifact_card(skill) for skill in payload.get("final_skills", [])]
        loops = payload.get("loops") or []
        summary_cards.extend(
            [
                {
                    "label": "Passed",
                    "value": str(bool(payload.get("passed"))),
                    "tone": "success" if payload.get("passed") else "danger",
                },
                {
                    "label": "Rounds",
                    "value": str(len(loops) if loops else len(payload.get("warmup_rounds", []))),
                    "tone": "accent",
                },
                {
                    "label": "Final Skills",
                    "value": str(len(artifacts)),
                    "tone": "success",
                },
                {
                    "label": "Fault Skill",
                    "value": str(payload.get("chosen_fault_skill", "")),
                    "tone": "warning",
                },
                {
                    "label": "Debug Events",
                    "value": str(len(payload.get("debug_events") or [])),
                    "tone": "accent",
                },
            ]
        )
        subtitle = (
            f"Fault task {payload.get('fault_task_id', '')} | "
            f"verify task {payload.get('verify_task_id', '')}"
        )
        pages = _exp3_pages(payload, role_audit)
    else:
        artifacts = [_artifact_card(skill) for skill in payload.get("skills", [])]
        disabled_count = sum(1 for item in artifacts if item.get("status") == "disabled")
        summary_cards.extend(
            [
                {"label": "Model", "value": str(payload.get("model_name", "")), "tone": "accent"},
                {"label": "Skills", "value": str(len(artifacts)), "tone": "success"},
                {"label": "Disabled", "value": str(disabled_count), "tone": "warning"},
                {
                    "label": "Test Valid",
                    "value": str((payload.get("test_summary") or {}).get("official_valid_rate")),
                    "tone": "success",
                },
            ]
        )
        subtitle = f"{payload.get('benchmark', '')} | medium-scale evolve"
        pages = _medium_pages(payload)

    return {
        "kind": kind,
        "experiment": {
            **meta,
            "passed": bool(payload.get("passed", False))
            if kind.startswith("exp") or kind == "method_validation"
            else None,
            "subtitle": subtitle,
        },
        "overview_metrics": _overview_metrics(summary_cards),
        "files": files,
        "artifacts": artifacts,
        "readme_text": readme_text,
        "docs": docs,
        "pages": pages,
    }


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


@app.route("/maintenance")
def maintenance():
    return render_template("maintenance.html")


@app.route("/maintenance/<path:_subpath>")
def maintenance_subpath(_subpath: str):
    return render_template("maintenance.html")


@app.route("/method-tests")
def method_tests():
    return render_template("maintenance.html")


@app.route("/method-tests/<path:_subpath>")
def method_tests_subpath(_subpath: str):
    return render_template("maintenance.html")


@app.route("/maintenance-docs")
def maintenance_docs_page():
    return render_template("maintenance_docs.html")


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


@app.route("/api/maintenance/experiments")
def api_maintenance_experiments():
    experiments = _maintenance_experiment_meta()
    return jsonify({"experiments": experiments})


@app.route("/api/maintenance/experiment")
def api_maintenance_experiment():
    experiment_id = str(request.args.get("id", "")).strip()
    if not experiment_id:
        return jsonify({"error": "id is required"}), 400
    try:
        meta = _maintenance_lookup(experiment_id)
        payload = _load_json(Path(meta["result_path"]))
        detail = _maintenance_detail_from_payload(meta, payload)
        return jsonify(detail)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/maintenance/player")
def api_maintenance_player():
    experiment_id = str(request.args.get("id", "")).strip()
    if not experiment_id:
        return jsonify({"error": "id is required"}), 400
    try:
        meta = _maintenance_lookup(experiment_id)
        payload = _load_json(Path(meta["result_path"]))
        detail = _maintenance_detail_from_payload(meta, payload)
        title = str((detail.get("experiment") or {}).get("title") or experiment_id)
        kind = str(detail.get("kind") or meta.get("kind") or "maintenance")
        if payload.get("debug_events"):
            trace = build_runner_trace_from_debug_events(
                run_id=experiment_id,
                title=title,
                kind=kind,
                debug_events=payload.get("debug_events") or [],
                pages=detail.get("pages") or [],
                artifacts=detail.get("artifacts") or [],
            )
        else:
            trace = build_player_trace_from_pages(
                run_id=experiment_id,
                title=title,
                kind=kind,
                pages=detail.get("pages") or [],
                artifacts=detail.get("artifacts") or [],
            )
        return jsonify(trace)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/maintenance/docs")
def api_maintenance_docs():
    try:
        return jsonify({"docs": _maintenance_reference_docs()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/maintenance-docs/_sidebar.md")
def maintenance_docs_sidebar():
    return Response(_maintenance_docs_sidebar(), mimetype="text/markdown; charset=utf-8")


@app.route("/maintenance-docs/<path:doc_path>")
def maintenance_docs_markdown(doc_path: str):
    safe_name = Path(doc_path).name
    path = MAINTENANCE_DOC_FILES.get(safe_name)
    if path is None or not path.exists():
        return Response(f"# Not Found\n\nUnknown maintenance doc: `{doc_path}`\n", status=404, mimetype="text/markdown; charset=utf-8")
    return Response(path.read_text(encoding="utf-8"), mimetype="text/markdown; charset=utf-8")


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
