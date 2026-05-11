"""Summarize BFCL baseline-vs-evolve diagnostics for a fixed experiment pair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _best_run_map(obj: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in obj.get("details", []) or []:
        runs = item.get("runs", []) or []
        if not runs:
            continue
        out[str(item.get("task_id"))] = runs[0]
    return out


def _case_rows(baseline: Dict[str, Any], evolve_test: Dict[str, Any]) -> List[Dict[str, Any]]:
    left = _best_run_map(baseline)
    right = _best_run_map(evolve_test)
    rows: List[Dict[str, Any]] = []
    for task_id in sorted(set(left) | set(right)):
        lrun = left.get(task_id, {})
        rrun = right.get(task_id, {})
        lm = lrun.get("metrics") or {}
        rm = rrun.get("metrics") or {}
        rows.append(
            {
                "task_id": task_id,
                "baseline_official_valid": lm.get("official_valid"),
                "evolve_official_valid": rm.get("official_valid"),
                "baseline_score": lrun.get("score"),
                "evolve_score": rrun.get("score"),
                "baseline_tokens": lm.get("total_tokens"),
                "evolve_tokens": rm.get("total_tokens"),
                "baseline_steps": lm.get("n_model_steps"),
                "evolve_steps": rm.get("n_model_steps"),
                "baseline_skills": lm.get("prompt_injected_skills", []),
                "evolve_skills": rm.get("prompt_injected_skills", []),
            }
        )
    rows.sort(
        key=lambda row: (
            (1 if row["evolve_official_valid"] is True else 0) - (1 if row["baseline_official_valid"] is True else 0),
            (float(row["evolve_score"] or 0.0) - float(row["baseline_score"] or 0.0)),
        ),
        reverse=True,
    )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose BFCL evolve result against baseline")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--evolve", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline = _load(args.baseline)
    evolve = _load(args.evolve)
    payload = {
        "baseline": str(args.baseline),
        "evolve": str(args.evolve),
        "baseline_summary": {k: baseline.get(k) for k in [
            "official_valid_rate", "avg_score", "avg_total_tokens", "avg_model_steps"
        ]},
        "evolve_train_summary": evolve.get("train_summary"),
        "evolve_test_summary": evolve.get("test_summary"),
        "skill_impact_summary": evolve.get("skill_impact_summary"),
        "case_deltas": _case_rows(baseline, {"details": evolve.get("test_details", [])}),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({
        "output": str(args.output),
        "baseline_summary": payload["baseline_summary"],
        "evolve_test_summary": payload["evolve_test_summary"],
        "n_case_deltas": len(payload["case_deltas"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
