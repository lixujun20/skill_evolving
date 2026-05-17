"""Compare two BFCL result JSON files.

This is meant for fast baseline-vs-skill or baseline-vs-evolve diagnosis.
It summarizes:
- aggregate metric deltas
- case-level official_valid changes
- case-level call-F1 deltas
- skill usage deltas when available
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


SUMMARY_KEYS = [
    "official_valid_rate",
    "official_avg_at_k",
    "official_pass_at_k",
    "avg_score",
    "avg_call_precision",
    "avg_call_recall",
    "avg_turn_success_rate",
    "avg_total_tokens",
    "avg_elapsed_s",
    "avg_model_steps",
    "timeout_rate",
]


def _load(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text())
    _backfill_official_metrics(obj)
    return obj


def _task_best_run(item: Dict[str, Any]) -> Dict[str, Any]:
    runs = item.get("runs", []) or []
    if not runs:
        return {}
    return max(
        runs,
        key=lambda run: (
            1 if (run.get("metrics") or {}).get("official_valid") is True else 0,
            float(run.get("score", 0.0)),
        ),
    )


def _detail_map(obj: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for item in obj.get("details", []) or []:
        mapping[str(item.get("task_id", ""))] = _task_best_run(item)
    return mapping


def _backfill_official_metrics(obj: Dict[str, Any]) -> None:
    if obj.get("official_avg_at_k") is None:
        vals = []
        for item in obj.get("details", []) or []:
            for run in item.get("runs", []) or []:
                metric = (run.get("metrics") or {}).get("official_valid")
                if metric is not None:
                    vals.append(metric is True)
        if vals:
            obj["official_avg_at_k"] = round(sum(1 for value in vals if value) / len(vals), 4)
    if obj.get("official_pass_at_k") is None:
        counted = 0
        passed = 0
        for item in obj.get("details", []) or []:
            vals = [
                (run.get("metrics") or {}).get("official_valid")
                for run in item.get("runs", []) or []
                if (run.get("metrics") or {}).get("official_valid") is not None
            ]
            if not vals:
                continue
            counted += 1
            if any(value is True for value in vals):
                passed += 1
        if counted:
            obj["official_pass_at_k"] = round(passed / counted, 4)


def _metric_delta(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in SUMMARY_KEYS:
        lv = left.get(key)
        rv = right.get(key)
        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
            out[key] = {
                "left": lv,
                "right": rv,
                "delta": round(rv - lv, 4),
            }
        else:
            out[key] = {
                "left": lv,
                "right": rv,
                "delta": None,
            }
    return out


def _case_deltas(left: Dict[str, Any], right: Dict[str, Any]) -> List[Dict[str, Any]]:
    left_map = _detail_map(left)
    right_map = _detail_map(right)
    rows: List[Dict[str, Any]] = []
    for task_id in sorted(set(left_map) | set(right_map)):
        lrun = left_map.get(task_id, {})
        rrun = right_map.get(task_id, {})
        lm = lrun.get("metrics") or {}
        rm = rrun.get("metrics") or {}
        rows.append(
            {
                "task_id": task_id,
                "left_official_valid": lm.get("official_valid"),
                "right_official_valid": rm.get("official_valid"),
                "left_score": lrun.get("score"),
                "right_score": rrun.get("score"),
                "score_delta": round(float(rrun.get("score", 0.0)) - float(lrun.get("score", 0.0)), 4),
                "left_total_tokens": lm.get("total_tokens"),
                "right_total_tokens": rm.get("total_tokens"),
                "total_tokens_delta": _safe_delta(lm.get("total_tokens"), rm.get("total_tokens")),
                "left_model_steps": lm.get("n_model_steps"),
                "right_model_steps": rm.get("n_model_steps"),
                "model_steps_delta": _safe_delta(lm.get("n_model_steps"), rm.get("n_model_steps")),
                "left_error_types": _error_type_counts(lm),
                "right_error_types": _error_type_counts(rm),
                "left_prompt_injected_skills": lm.get("prompt_injected_skills", []),
                "right_prompt_injected_skills": rm.get("prompt_injected_skills", []),
                "left_retrieved_skills": lm.get("retrieved_skills", []),
                "right_retrieved_skills": rm.get("retrieved_skills", []),
            }
        )
    rows.sort(
        key=lambda row: (
            (1 if row["right_official_valid"] is True else 0) - (1 if row["left_official_valid"] is True else 0),
            row["score_delta"],
        ),
        reverse=True,
    )
    return rows


def _safe_delta(left: Any, right: Any) -> Any:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return round(float(right) - float(left), 4)
    return None


def _error_type_counts(metrics: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for error in metrics.get("call_errors", []) or []:
        etype = str(error.get("type", "unknown"))
        counts[etype] = counts.get(etype, 0) + 1
    return counts


def _skill_stat_delta(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    left_skill = (left.get("skill_stats") or {})
    right_skill = (right.get("skill_stats") or {})
    for group in [
        "retrieved_counts",
        "prompt_injected_counts",
        "tool_injected_counts",
        "used_counts",
        "called_skill_tool_counts",
    ]:
        lgroup = left_skill.get(group) or {}
        rgroup = right_skill.get(group) or {}
        merged: Dict[str, Dict[str, Any]] = {}
        for key in sorted(set(lgroup) | set(rgroup)):
            lv = int(lgroup.get(key, 0))
            rv = int(rgroup.get(key, 0))
            merged[key] = {"left": lv, "right": rv, "delta": rv - lv}
        out[group] = merged
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two BFCL result JSON files")
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()

    left = _load(args.left)
    right = _load(args.right)
    payload = {
        "left": str(args.left),
        "right": str(args.right),
        "metric_delta": _metric_delta(left, right),
        "skill_stat_delta": _skill_stat_delta(left, right),
        "case_deltas": _case_deltas(left, right),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
