"""
evaluation.py — Shared end-to-end evaluation utilities for comparing skill sets.

The primary use case in this repo is "collection-level" comparison:
run the same problems under different skill sets (e.g. no skills, original
skills, refactored skills) and compare accuracy / token / latency deltas.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from academic.config import INTER_PROBLEM_DELAY
from academic.executor import solve
from academic.pipeline import Problem, check_answer
from academic.skill_store import Skill, SkillStore


@dataclass
class SkillSetSpec:
    name: str
    skills: List[Skill] = field(default_factory=list)
    store: Optional[SkillStore] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    total_code_tokens: Optional[float] = None


@dataclass
class DirectEvalCase:
    case_id: str
    query: str
    expected: Any
    evaluator: Callable[[List[Skill]], Any]


async def evaluate_skill_sets(
    problems: List[Problem],
    skill_sets: List[SkillSetSpec],
    *,
    llm_config: str,
    n_runs: int = 1,
    system_prompt_template: Optional[str] = None,
    inter_problem_delay: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluate multiple named skill sets on the same problem list.

    Returns a stable JSON-serializable dict with per-set summaries and per-run
    details. The first entry is treated as the baseline for delta reporting.
    """
    if not skill_sets:
        return {"baseline": None, "skill_sets": []}

    delay = INTER_PROBLEM_DELAY if inter_problem_delay is None else inter_problem_delay
    reports: List[Dict[str, Any]] = []
    for idx, spec in enumerate(skill_sets):
        report = await _evaluate_one_set(
            problems,
            spec,
            llm_config=llm_config,
            n_runs=n_runs,
            system_prompt_template=system_prompt_template,
            inter_problem_delay=delay,
        )
        reports.append(report)
        if idx < len(skill_sets) - 1 and delay > 0:
            await asyncio.sleep(delay)

    baseline = reports[0]
    baseline_summary = baseline["summary"]
    for report in reports[1:]:
        summary = report["summary"]
        summary["delta_vs_baseline"] = {
            "accuracy_micro": round(
                summary["accuracy_micro"] - baseline_summary["accuracy_micro"], 4
            ),
            "avg_total_tokens": round(
                summary["avg_total_tokens"] - baseline_summary["avg_total_tokens"], 1
            ),
            "avg_completion_tokens": round(
                summary["avg_completion_tokens"] - baseline_summary["avg_completion_tokens"], 1
            ),
            "total_elapsed_s": round(
                summary["total_elapsed_s"] - baseline_summary["total_elapsed_s"], 1
            ),
        }

    return {
        "baseline": baseline["skill_set"],
        "skill_sets": reports,
    }


def evaluate_skill_sets_direct(
    cases: List[DirectEvalCase],
    skill_sets: List[SkillSetSpec],
) -> Dict[str, Any]:
    if not skill_sets:
        return {"baseline": None, "skill_sets": []}

    reports: List[Dict[str, Any]] = []
    for spec in skill_sets:
        details: List[Dict[str, Any]] = []
        total_correct = 0
        total_elapsed = 0.0
        total_tokens = 0
        total_completion_tokens = 0

        for case in cases:
            t0 = time.monotonic()
            try:
                predicted = case.evaluator(spec.skills)
                correct = predicted == case.expected
                error = None
            except Exception as exc:
                predicted = None
                correct = False
                error = repr(exc)
            elapsed = time.monotonic() - t0
            total_correct += int(correct)
            total_elapsed += elapsed
            total_tokens += (
                spec.total_code_tokens
                if spec.total_code_tokens is not None
                else sum(len((sk.code or "").split()) for sk in spec.skills)
            )
            details.append({
                "case_id": case.case_id,
                "query": case.query,
                "expected": case.expected,
                "predicted": predicted,
                "correct": correct,
                "error": error,
                "elapsed_s": round(elapsed, 4),
            })

        summary = {
            "skill_set": spec.name,
            "n_problems": len(cases),
            "n_runs_per_problem": 1,
            "total_correct": total_correct,
            "total_runs": len(cases),
            "accuracy_micro": round(total_correct / max(len(cases), 1), 4),
            "avg_total_tokens": round(total_tokens / max(len(cases), 1), 1),
            "avg_completion_tokens": round(total_completion_tokens / max(len(cases), 1), 1),
            "total_elapsed_s": round(total_elapsed, 4),
            "n_skills": len(spec.skills),
            "metadata": dict(spec.metadata),
        }
        reports.append({
            "skill_set": spec.name,
            "summary": summary,
            "details": details,
        })

    baseline = reports[0]
    baseline_summary = baseline["summary"]
    for report in reports[1:]:
        summary = report["summary"]
        summary["delta_vs_baseline"] = {
            "accuracy_micro": round(
                summary["accuracy_micro"] - baseline_summary["accuracy_micro"], 4
            ),
            "avg_total_tokens": round(
                summary["avg_total_tokens"] - baseline_summary["avg_total_tokens"], 1
            ),
            "avg_completion_tokens": round(
                summary["avg_completion_tokens"] - baseline_summary["avg_completion_tokens"], 1
            ),
            "total_elapsed_s": round(
                summary["total_elapsed_s"] - baseline_summary["total_elapsed_s"], 4
            ),
        }

    return {
        "baseline": baseline["skill_set"],
        "skill_sets": reports,
        "evaluation_mode": "direct_harness",
    }


async def _evaluate_one_set(
    problems: List[Problem],
    spec: SkillSetSpec,
    *,
    llm_config: str,
    n_runs: int,
    system_prompt_template: Optional[str],
    inter_problem_delay: int,
) -> Dict[str, Any]:
    details: List[Dict[str, Any]] = []
    total_correct = 0
    total_runs = 0
    total_elapsed = 0.0
    total_tokens = 0.0
    total_completion_tokens = 0.0
    completed_runs = 0

    for problem_idx, problem in enumerate(problems):
        runs: List[Dict[str, Any]] = []
        for run_idx in range(n_runs):
            t0 = time.monotonic()
            trace = await solve(
                problem.question,
                spec.skills,
                llm_config=llm_config,
                store=spec.store if spec.skills else None,
                system_prompt_template=system_prompt_template,
            )
            elapsed = time.monotonic() - t0
            correct = check_answer(trace.final_answer, problem.answer)
            total_correct += int(correct)
            total_runs += 1
            total_elapsed += elapsed
            if not trace.timed_out:
                total_tokens += trace.total_tokens
                total_completion_tokens += trace.completion_tokens
                completed_runs += 1
            runs.append({
                "problem_idx": problem_idx,
                "run_idx": run_idx,
                "problem_id": problem.id,
                "expected": problem.answer,
                "predicted": trace.final_answer,
                "correct": correct,
                "timed_out": trace.timed_out,
                "total_tokens": trace.total_tokens,
                "completion_tokens": trace.completion_tokens,
                "elapsed_s": round(elapsed, 1),
                "n_steps": len(trace.steps),
                "n_code_blocks": len(trace.code_blocks),
            })
        details.append({
            "problem_idx": problem_idx,
            "problem_id": problem.id,
            "question": problem.question[:160],
            "expected": problem.answer,
            "runs": runs,
            "n_correct": sum(1 for r in runs if r["correct"]),
            "n_runs": len(runs),
            "accuracy": round(sum(1 for r in runs if r["correct"]) / max(len(runs), 1), 4),
        })
        if problem_idx < len(problems) - 1 and inter_problem_delay > 0:
            await asyncio.sleep(max(inter_problem_delay // 2, 1))

    summary = {
        "skill_set": spec.name,
        "n_problems": len(problems),
        "n_runs_per_problem": n_runs,
        "total_correct": total_correct,
        "total_runs": total_runs,
        "accuracy_micro": round(total_correct / max(total_runs, 1), 4),
        "avg_total_tokens": round(total_tokens / max(completed_runs, 1), 1),
        "avg_completion_tokens": round(total_completion_tokens / max(completed_runs, 1), 1),
        "total_elapsed_s": round(total_elapsed, 1),
        "n_skills": len(spec.skills),
        "metadata": dict(spec.metadata),
    }
    return {
        "skill_set": spec.name,
        "summary": summary,
        "details": details,
    }
