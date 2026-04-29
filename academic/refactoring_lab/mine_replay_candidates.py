from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass
class CandidateCase:
    problem_idx: int
    problem_id: str
    question: str
    baseline_accuracy: float
    evolve_accuracy: float
    baseline_avg_tokens: float
    evolve_avg_tokens: float
    baseline_avg_steps: float
    evolve_avg_steps: float
    baseline_has_timeout: bool
    evolve_has_timeout: bool
    candidate_reason_tags: List[str] = field(default_factory=list)
    priority_score: float = 0.0
    baseline_skills_seen: List[str] = field(default_factory=list)
    evolve_skills_seen: List[str] = field(default_factory=list)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _normalize_problem_rows(data: dict) -> List[dict]:
    if "test_details" in data:
        return list(data["test_details"])
    if "problems" in data:
        return list(data["problems"])
    raise ValueError(f"Unrecognized detail format: keys={list(data.keys())}")


def _avg_steps(row: dict) -> float:
    runs = row.get("runs", [])
    if not runs:
        return 0.0
    return round(sum(run.get("n_steps", 0) for run in runs) / len(runs), 2)


def _collect_skill_names(row: dict) -> List[str]:
    names = []
    for run in row.get("runs", []):
        for skill in run.get("skills_retrieved", []) or []:
            if skill not in names:
                names.append(skill)
    return names


def _reason_tags(baseline: dict, evolve: dict) -> Tuple[List[str], float]:
    tags: List[str] = []
    score = 0.0

    b_acc = baseline.get("accuracy", 0.0)
    e_acc = evolve.get("accuracy", 0.0)
    b_tok = float(baseline.get("avg_total_tokens", 0.0))
    e_tok = float(evolve.get("avg_total_tokens", 0.0))
    b_steps = _avg_steps(baseline)
    e_steps = _avg_steps(evolve)
    b_code = sum(run.get("n_code_blocks", 0) for run in baseline.get("runs", []))
    e_code = sum(run.get("n_code_blocks", 0) for run in evolve.get("runs", []))

    if e_acc < b_acc:
        tags.append("regression")
        score += 5 + (b_acc - e_acc) * 10

    if e_tok > b_tok * 1.2 and e_acc <= b_acc:
        tags.append("token_blow_up")
        score += 3

    if e_steps < b_steps * 0.5 and e_acc < b_acc:
        tags.append("shortcut_suspicion")
        score += 4

    if e_code < max(b_code * 0.5, 1) and e_acc < b_acc:
        tags.append("code_avoidance")
        score += 3

    if evolve.get("has_timeout") and not baseline.get("has_timeout"):
        tags.append("new_timeout")
        score += 2

    evolve_skills = _collect_skill_names(evolve)
    if evolve_skills and e_acc < b_acc:
        tags.append("planner_or_retrieval_mismatch")
        score += 2

    return tags, round(score, 2)


def mine_replay_candidates(
    *,
    baseline_detail_path: Path,
    evolve_detail_path: Path,
) -> Dict[str, object]:
    baseline = _load_json(baseline_detail_path)
    evolve = _load_json(evolve_detail_path)

    baseline_rows = _normalize_problem_rows(baseline)
    evolve_rows = _normalize_problem_rows(evolve)
    if len(baseline_rows) != len(evolve_rows):
        raise ValueError(
            f"Mismatched problem counts: baseline={len(baseline_rows)} evolve={len(evolve_rows)}"
        )

    candidates: List[CandidateCase] = []
    for base_row, evo_row in zip(baseline_rows, evolve_rows):
        if base_row.get("problem_id") != evo_row.get("problem_id"):
            raise ValueError(
                f"Problem mismatch: {base_row.get('problem_id')} vs {evo_row.get('problem_id')}"
            )
        tags, priority = _reason_tags(base_row, evo_row)
        if not tags:
            continue
        candidates.append(
            CandidateCase(
                problem_idx=base_row["problem_idx"],
                problem_id=base_row["problem_id"],
                question=base_row["question"],
                baseline_accuracy=base_row.get("accuracy", 0.0),
                evolve_accuracy=evo_row.get("accuracy", 0.0),
                baseline_avg_tokens=float(base_row.get("avg_total_tokens", 0.0)),
                evolve_avg_tokens=float(evo_row.get("avg_total_tokens", 0.0)),
                baseline_avg_steps=_avg_steps(base_row),
                evolve_avg_steps=_avg_steps(evo_row),
                baseline_has_timeout=bool(base_row.get("has_timeout")),
                evolve_has_timeout=bool(evo_row.get("has_timeout")),
                candidate_reason_tags=tags,
                priority_score=priority,
                baseline_skills_seen=_collect_skill_names(base_row),
                evolve_skills_seen=_collect_skill_names(evo_row),
            )
        )

    candidates.sort(
        key=lambda item: (-item.priority_score, item.problem_idx, item.problem_id)
    )
    return {
        "baseline_detail_path": str(baseline_detail_path),
        "evolve_detail_path": str(evolve_detail_path),
        "n_candidates": len(candidates),
        "candidates": [asdict(item) for item in candidates],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mine replay benchmark candidates from real experiments")
    parser.add_argument("--baseline-detail", required=True)
    parser.add_argument("--evolve-detail", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = mine_replay_candidates(
        baseline_detail_path=Path(args.baseline_detail),
        evolve_detail_path=Path(args.evolve_detail),
    )
    text_out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text_out)
    print(text_out)


if __name__ == "__main__":
    main()
