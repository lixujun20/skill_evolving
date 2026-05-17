"""Select reusable BFCL debug/validation case subsets from a baseline result.

The goal is not to hand-pick memorable cases, but to deterministically sample
non-saturated failures across recurring error families so evolve debugging
stays case-agnostic.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _dominant_error(metrics: Dict[str, Any]) -> str:
    counts: Dict[str, int] = {}
    for error in metrics.get("call_errors", []) or []:
        etype = str(error.get("type", "unknown"))
        counts[etype] = counts.get(etype, 0) + 1
    if not counts:
        return "none"
    return max(sorted(counts.items()), key=lambda item: item[1])[0]


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    obj = json.loads(path.read_text())
    rows: List[Dict[str, Any]] = []
    for item in obj.get("details", []) or []:
        runs = item.get("runs", []) or []
        if not runs:
            continue
        run = runs[0]
        metrics = run.get("metrics") or {}
        rows.append(
            {
                "task_id": item.get("task_id"),
                "official_valid": metrics.get("official_valid"),
                "score": float(run.get("score", 0.0)),
                "elapsed_s": float(metrics.get("elapsed_s") or 0.0),
                "n_model_steps": int(metrics.get("n_model_steps") or 0),
                "dominant_error": _dominant_error(metrics),
                "call_errors": metrics.get("call_errors", []) or [],
            }
        )
    return rows


def _select_debug(rows: List[Dict[str, Any]], per_error: int, max_elapsed_s: float) -> List[str]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["official_valid"] is not False:
            continue
        if row["elapsed_s"] > max_elapsed_s:
            continue
        if row["score"] < 0.45:
            continue
        buckets[row["dominant_error"]].append(row)
    selected: List[str] = []
    for error_type in sorted(buckets):
        candidates = sorted(
            buckets[error_type],
            key=lambda row: (
                abs(row["score"] - 0.72),
                row["elapsed_s"],
                row["task_id"],
            ),
        )
        selected.extend(row["task_id"] for row in candidates[:per_error])
    # de-duplicate while preserving order
    out: List[str] = []
    seen = set()
    for task_id in selected:
        if task_id not in seen:
            seen.add(task_id)
            out.append(task_id)
    return out


def _select_validation(rows: List[Dict[str, Any]], n_cases: int, max_elapsed_s: float) -> List[str]:
    candidates = [
        row
        for row in rows
        if row["elapsed_s"] <= max_elapsed_s and row["score"] >= 0.45
    ]
    candidates.sort(
        key=lambda row: (
            0 if row["official_valid"] is False else 1,
            abs(row["score"] - 0.75),
            row["elapsed_s"],
            row["task_id"],
        )
    )
    return [row["task_id"] for row in candidates[:n_cases]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Select deterministic BFCL debug/validation subsets")
    parser.add_argument("baseline", type=Path, help="Baseline result JSON")
    parser.add_argument("--debug-out", type=Path, required=True)
    parser.add_argument("--validation-out", type=Path, required=True)
    parser.add_argument("--per-error", type=int, default=4)
    parser.add_argument("--validation-size", type=int, default=24)
    parser.add_argument("--max-elapsed-s", type=float, default=250.0)
    args = parser.parse_args()

    rows = _load_rows(args.baseline)
    debug_task_ids = _select_debug(rows, per_error=args.per_error, max_elapsed_s=args.max_elapsed_s)
    validation_task_ids = _select_validation(rows, n_cases=args.validation_size, max_elapsed_s=args.max_elapsed_s)

    args.debug_out.parent.mkdir(parents=True, exist_ok=True)
    args.validation_out.parent.mkdir(parents=True, exist_ok=True)
    args.debug_out.write_text(json.dumps({"task_ids": debug_task_ids}, ensure_ascii=False, indent=2))
    args.validation_out.write_text(json.dumps({"task_ids": validation_task_ids}, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "debug_task_ids": debug_task_ids,
                "validation_task_ids": validation_task_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
