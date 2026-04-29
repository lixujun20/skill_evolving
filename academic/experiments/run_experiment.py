"""
run_experiment.py — Unified experiment runner for skill evolving.

Supports multiple datasets via --dataset:
  aime      — AIME 2024 (30 train / 30 test, integer answers)
  math500   — MATH-500 from DeepScaler parquet (200 train / 200 test, LaTeX answers)

Usage:
    cd ~/skill_evolving

    # MATH-500 baseline (4 runs × 200 test problems):
    python -u -m academic.experiments.run_experiment --dataset math500 --mode baseline --tag exp1

    # MATH-500 evolve 1-epoch:
    python -u -m academic.experiments.run_experiment --dataset math500 --mode evolve --epochs 1 --tag exp1

    # AIME baseline (backward-compatible):
    python -u -m academic.experiments.run_experiment --dataset aime --mode baseline --tag v2

    # Resume a partial run (checkpoint is loaded automatically):
    python -u -m academic.experiments.run_experiment --dataset math500 --mode baseline --tag exp1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path.home()))

from academic.config import (
    RESULTS_DIR, INTER_PROBLEM_DELAY, AGENT_MODEL, EXTRACT_MODEL,
    MAX_AGENT_STEPS, LLM_CALL_TIMEOUT,
)
from academic.executor import ExecTrace, solve, SOLVE_SYSTEM, MATH_SOLVE_SYSTEM
from academic.extractor import extract_skills, refine_skill_after_test_failure
from academic.maintenance_refactor import (
    MaintenanceRefactorConfig,
    run_maintenance_refactor,
)
from academic.skill_store import SkillStore
from academic.tester import test_skill, test_stale_skills
from academic.pipeline import Problem, check_answer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tool_evolving")

CONCURRENCY = 4
MAX_RUN_ATTEMPTS = 3
MAX_REFINE_ATTEMPTS = 3


# ── Dataset configuration ─────────────────────────────────────────────────────

@dataclass
class DatasetConfig:
    name: str
    file_prefix: str
    system_prompt_template: str
    default_n_train: int
    default_n_test: int
    default_concurrency: int
    # Optional: different system prompt for test phase (e.g. mixed-dataset experiments)
    test_system_prompt_template: Optional[str] = None

    def get_test_prompt(self) -> str:
        return self.test_system_prompt_template or self.system_prompt_template


DATASET_CONFIGS = {
    "aime": DatasetConfig(
        name="aime",
        file_prefix="aime_v3",
        system_prompt_template=SOLVE_SYSTEM,
        default_n_train=30,
        default_n_test=30,
        default_concurrency=8,
    ),
    "math500": DatasetConfig(
        name="math500",
        file_prefix="math_v1",
        system_prompt_template=MATH_SOLVE_SYSTEM,
        default_n_train=200,
        default_n_test=200,
        default_concurrency=8,
    ),
    "math_train": DatasetConfig(
        name="math_train",
        file_prefix="math_train_v1",
        system_prompt_template=MATH_SOLVE_SYSTEM,
        default_n_train=200,
        default_n_test=200,
        default_concurrency=8,
    ),
    # Evolve on DeepScaler train.shuffle (100 problems), test on AIME-25 (30 problems)
    "ds100_aime": DatasetConfig(
        name="ds100_aime",
        file_prefix="ds100_aime_v1",
        system_prompt_template=MATH_SOLVE_SYSTEM,   # used during evolve phase
        test_system_prompt_template=SOLVE_SYSTEM,   # used during test phase (AIME integers)
        default_n_train=100,
        default_n_test=30,
        default_concurrency=4,
    ),
}


def load_dataset(dataset: str, n_train: int, n_test: int, seed: int) -> Tuple[List[Problem], List[Problem]]:
    if dataset == "aime":
        from academic.datasets.aime_dataset import load_train_test
        return load_train_test(n_train=n_train, n_test=n_test, seed=seed)
    elif dataset == "math500":
        from academic.datasets.math_dataset import load_train_test
        return load_train_test(n_train=n_train, n_test=n_test, seed=seed)
    elif dataset == "math_train":
        from academic.datasets.math_dataset import load_train_test, _TRAIN_PARQUET_PATH
        return load_train_test(n_train=n_train, n_test=n_test, seed=seed,
                               parquet_path=_TRAIN_PARQUET_PATH)
    elif dataset == "ds100_aime":
        # Train from DeepScaler train.shuffle.parquet, test from AIME-25
        from academic.datasets.math_dataset import load_from_parquet, _TRAIN_PARQUET_PATH
        from academic.datasets.aime_dataset import load_aime_2025
        train = load_from_parquet(n=n_train, seed=seed, parquet_path=_TRAIN_PARQUET_PATH)
        test = load_aime_2025(n=n_test, seed=seed)
        return train, test
    else:
        raise ValueError(f"Unknown dataset: {dataset}. Choose from: {list(DATASET_CONFIGS)}")


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def build_exp_name(mode: str, tag: str, epochs: int, file_prefix: str) -> str:
    exp_name = f"{file_prefix}_{tag}_{mode}"
    if mode == "evolve":
        exp_name += f"_{epochs}ep"
    return exp_name


def save_checkpoint(exp_name: str, summary: dict) -> None:
    detail_path = RESULTS_DIR / f"{exp_name}_detail.json"
    detail_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    compact = {k: v for k, v in summary.items()
               if k not in ("evolve_details", "test_details", "problems")}
    (RESULTS_DIR / f"{exp_name}_summary.json").write_text(
        json.dumps(compact, ensure_ascii=False, indent=2))


def load_checkpoint(exp_name: str) -> Optional[dict]:
    detail_path = RESULTS_DIR / f"{exp_name}_detail.json"
    if not detail_path.exists():
        return None
    return json.loads(detail_path.read_text())


# ── Logging helper ────────────────────────────────────────────────────────────

def log_trace(prob_idx: int, run_idx: int, prob: Problem, trace: ExecTrace,
              correct: bool, elapsed: float, label: str,
              skills_retrieved: list | None = None) -> dict:
    assistant_text = "\n".join(
        s["content"] for s in trace.steps if s.get("type") == "assistant_raw" and s.get("content")
    )[:1500]
    workflow_decision = "fresh"
    lowered = assistant_text.lower()
    if "reuse" in lowered and "fragment" in lowered:
        workflow_decision = "reuse_workflow_fragment"
    elif "reuse" in lowered:
        workflow_decision = "reuse_plan"
    elif "adapt" in lowered or "modify" in lowered:
        workflow_decision = "adapt_plan"
    entry = {
        "label": label,
        "solver_mode": trace.solver_mode,
        "problem_idx": prob_idx,
        "run_idx": run_idx,
        "problem_id": prob.id,
        "question": prob.question,
        "expected": prob.answer,
        "predicted": trace.final_answer,
        "correct": correct,
        "total_tokens": trace.total_tokens,
        "completion_tokens": trace.completion_tokens,
        "n_steps": len(trace.steps),
        "n_code_blocks": len(trace.code_blocks),
        "elapsed_s": round(elapsed, 1),
        "skills_retrieved": [s.name for s in skills_retrieved] if skills_retrieved else [],
        "skill_tool_counts": dict(trace.skill_tool_counts),
        "skill_runtime_call_counts": dict(trace.skill_runtime_call_counts),
        "skills_called": sorted(
            [name for name, count in trace.skill_runtime_call_counts.items() if count > 0]
        ),
        "workflow_summary": assistant_text[:900],
        "workflow_decision": workflow_decision,
        "workflow_plan": "\n".join(trace.code_blocks[:2])[:1200],
        "steps": [{"step": si, "type": s["type"], "content": s["content"]}
                  for si, s in enumerate(trace.steps)],
        "conversation": trace.messages,
    }
    status = "✓" if correct else "✗"
    logger.info(
        f"  [{label}] P{prob_idx} R{run_idx} {status} "
        f"tokens={trace.total_tokens} comp={trace.completion_tokens} "
        f"steps={len(trace.steps)} elapsed={elapsed:.0f}s "
        f"pred={str(trace.final_answer)[:60]}"
    )
    return entry


# ── Core: run one problem N times ─────────────────────────────────────────────

async def run_problem_n_times(
    prob: Problem,
    prob_idx: int,
    n_runs: int,
    skills: list,
    store: Optional[SkillStore],
    label: str,
    agent_model: str,
    system_prompt_template: str,
    solver_mode: str = "tir",
    existing_runs: Optional[list] = None,
    on_run_complete: Optional[Callable[[list], None]] = None,
) -> dict:
    runs = list(existing_runs or [])
    while len(runs) < n_runs:
        runs.append(None)

    for run_idx in range(n_runs):
        existing = runs[run_idx]
        if existing and not existing.get("timed_out"):
            continue

        entry = None
        resume_state = None
        if existing and existing.get("timed_out") and existing.get("partial_state"):
            resume_state = existing["partial_state"]

        for attempt in range(1, MAX_RUN_ATTEMPTS + 1):
            try:
                t0 = time.monotonic()
                trace = await solve(
                    prob.question, skills or [],
                    llm_config=agent_model,
                    store=store if (store and skills) else None,
                    resume_state=resume_state,
                    system_prompt_template=system_prompt_template,
                    solver_mode=solver_mode,
                )
                elapsed = time.monotonic() - t0

                if trace.timed_out:
                    logger.info(
                        f"  [{label}] P{prob_idx} R{run_idx} ⏱ TIMEOUT "
                        f"(attempt {attempt}/{MAX_RUN_ATTEMPTS})"
                    )
                    resume_state = trace.partial_state
                    if attempt < MAX_RUN_ATTEMPTS:
                        await asyncio.sleep(60)
                        continue
                    logger.info(f"  [{label}] P{prob_idx} R{run_idx} ⏱ SHELVED")
                    break

                correct = check_answer(trace.final_answer, prob.answer)
                entry = log_trace(prob_idx, run_idx, prob, trace, correct, elapsed, label,
                                  skills_retrieved=skills or [])
                break
            except Exception as exc:
                if attempt >= MAX_RUN_ATTEMPTS:
                    logger.warning(
                        f"  [{label}] P{prob_idx} R{run_idx} SHELVED "
                        f"(crash: {type(exc).__name__}: {exc})"
                    )
                    entry = {
                        "timed_out": True, "label": label,
                        "problem_idx": prob_idx, "run_idx": run_idx,
                        "problem_id": prob.id,
                        "question": prob.question[:120],
                        "expected": prob.answer, "predicted": None,
                        "correct": False, "total_tokens": 0,
                        "completion_tokens": 0, "n_steps": 0,
                        "n_code_blocks": 0, "elapsed_s": 0, "steps": [],
                    }
                    break
                wait_s = min(60 * attempt, 300)
                logger.warning(
                    f"  [{label}] P{prob_idx} R{run_idx} crashed "
                    f"({type(exc).__name__}), retry {attempt}/{MAX_RUN_ATTEMPTS} in {wait_s}s"
                )
                await asyncio.sleep(wait_s)

        runs[run_idx] = entry
        if on_run_complete:
            on_run_complete(runs)
        if run_idx < n_runs - 1 and INTER_PROBLEM_DELAY > 0:
            await asyncio.sleep(max(INTER_PROBLEM_DELAY // 2, 5))

    completed_runs = [r for r in runs if r and not r.get("timed_out")]
    n_completed = len(completed_runs)
    n_correct = sum(1 for r in completed_runs if r.get("correct"))
    has_timeout = any(r and r.get("timed_out") for r in runs)
    avg_tokens = sum(r.get("total_tokens", 0) for r in completed_runs) / n_completed if n_completed else 0.0
    avg_comp = sum(r.get("completion_tokens", 0) for r in completed_runs) / n_completed if n_completed else 0.0

    return {
        "problem_idx": prob_idx,
        "problem_id": prob.id,
        "question": prob.question[:120],
        "expected": prob.answer,
        "n_runs": n_runs,
        "n_correct": n_correct,
        "accuracy": n_correct / n_runs,
        "avg_total_tokens": round(avg_tokens, 1),
        "avg_completion_tokens": round(avg_comp, 1),
        "has_timeout": has_timeout,
        "runs": runs,
    }


def _agg(results: list):
    """Aggregate per-problem results."""
    valid = [r for r in results if r]
    total_correct = sum(r["n_correct"] for r in valid)
    total_runs = sum(r["n_runs"] for r in valid)
    per_acc = [r["accuracy"] for r in valid]
    mean_acc = sum(per_acc) / len(per_acc) if per_acc else 0.0
    avg_tok = sum(r["avg_total_tokens"] for r in valid) / len(valid) if valid else 0.0
    avg_comp = sum(r["avg_completion_tokens"] for r in valid) / len(valid) if valid else 0.0
    return total_correct, total_runs, mean_acc, avg_tok, avg_comp


def _sum_counts(entries: List[dict]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for entry in entries:
        if not entry:
            continue
        for name, count in entry.items():
            merged[name] = merged.get(name, 0) + int(count)
    return dict(sorted(merged.items()))


def _collect_phase_skill_stats(problem_results: List[dict]) -> dict:
    retrieved_counts: Dict[str, int] = {}
    called_counts: Dict[str, int] = {}
    tool_counts: Dict[str, int] = {}

    for problem_entry in problem_results or []:
        if not problem_entry:
            continue
        for run in problem_entry.get("runs", []) or []:
            if not run or run.get("timed_out"):
                continue
            for name in run.get("skills_retrieved", []) or []:
                retrieved_counts[name] = retrieved_counts.get(name, 0) + 1
            for name, count in (run.get("skill_runtime_call_counts") or {}).items():
                called_counts[name] = called_counts.get(name, 0) + int(count)
            for name, count in (run.get("skill_tool_counts") or {}).items():
                tool_counts[name] = tool_counts.get(name, 0) + int(count)

    return {
        "retrieved_counts": dict(sorted(retrieved_counts.items())),
        "called_counts": dict(sorted(called_counts.items())),
        "tool_call_counts": dict(sorted(tool_counts.items())),
        "n_unique_retrieved": len(retrieved_counts),
        "n_unique_called": len(called_counts),
    }


def _make_persist(results: list, i: int, prob: Problem, n_runs: int):
    """Create a callback to persist partial results after each run."""
    def persist(runs_list: list) -> None:
        valid = [r for r in runs_list if r and not r.get("timed_out")]
        n_v = len(valid)
        results[i] = {
            "problem_idx": i, "problem_id": prob.id,
            "question": prob.question[:120], "expected": prob.answer,
            "n_runs": sum(1 for r in runs_list if r is not None),
            "n_correct": sum(1 for r in valid if r.get("correct")),
            "accuracy": sum(1 for r in valid if r.get("correct")) / n_runs if n_runs else 0.0,
            "avg_total_tokens": round(sum(r.get("total_tokens", 0) for r in valid) / n_v, 1) if n_v else 0.0,
            "avg_completion_tokens": round(sum(r.get("completion_tokens", 0) for r in valid) / n_v, 1) if n_v else 0.0,
            "has_timeout": any(r and r.get("timed_out") for r in runs_list),
            "runs": runs_list,
        }
    return persist


# ── Baseline Mode ─────────────────────────────────────────────────────────────

async def run_baseline(
    test: List[Problem], n_runs: int, tag: str,
    agent_model: str, cfg: DatasetConfig,
    checkpoint: Optional[dict] = None,
    solver_mode: str = "tir",
) -> dict:
    exp_name = build_exp_name("baseline", tag, epochs=1, file_prefix=cfg.file_prefix)
    logger.info(f"=== BASELINE [{cfg.name}]: {len(test)} × {n_runs} runs (concurrency={CONCURRENCY}) ===")

    summary = checkpoint or {
        "dataset": cfg.name, "mode": "baseline", "tag": tag,
        "solver_mode": solver_mode,
        "n_problems": len(test), "n_runs_per_problem": n_runs,
        "total_correct": 0, "total_runs": 0,
        "accuracy_micro": 0.0, "accuracy_macro": 0.0,
        "avg_total_tokens": 0.0, "avg_completion_tokens": 0.0,
        "problems": [],
    }
    results = summary["problems"]
    while len(results) < len(test):
        results.append(None)

    sem = asyncio.Semaphore(CONCURRENCY)
    ck_lock = asyncio.Lock()

    async def _run_one(i: int, prob: Problem):
        existing = results[i]
        if existing and existing.get("n_runs", 0) >= n_runs:
            logger.info(f"\n[Baseline {i+1}/{len(test)}] {prob.id}: resume-skip "
                        f"({existing['n_correct']}/{existing['n_runs']} correct)")
            return
        logger.info(f"\n[Baseline {i+1}/{len(test)}] {prob.id}: {prob.question[:80]}...")
        persist = _make_persist(results, i, prob, n_runs)

        async with sem:
            r = await run_problem_n_times(
                prob, i, n_runs, skills=[], store=None, label="baseline",
                agent_model=agent_model,
                system_prompt_template=cfg.get_test_prompt(),
                solver_mode=solver_mode,
                existing_runs=(existing or {}).get("runs", []),
                on_run_complete=persist,
            )
            results[i] = r
            logger.info(f"  → {r['n_correct']}/{r['n_runs']} correct, avg_tokens={r['avg_total_tokens']}")
            async with ck_lock:
                save_checkpoint(exp_name, summary)

    await asyncio.gather(*[_run_one(i, p) for i, p in enumerate(test)])

    # Retry shelved runs
    for pass_idx in range(10):
        shelved = [idx for idx, r in enumerate(results)
                   if r and any(run and run.get("timed_out") for run in r.get("runs", []))]
        if not shelved:
            logger.info("✓ No timed-out baseline runs remaining!")
            break
        logger.info(f"\n=== SHELVE RETRY {pass_idx+1}: {len(shelved)} problems ===")
        for idx in shelved:
            prob = test[idx]
            logger.info(f"\n[Retry {idx+1}/{len(test)}] {prob.id}")
            persist_retry = _make_persist(results, idx, prob, n_runs)

            r = await run_problem_n_times(
                prob, idx, n_runs, skills=[], store=None, label="baseline",
                agent_model=agent_model,
                system_prompt_template=cfg.get_test_prompt(),
                solver_mode=solver_mode,
                existing_runs=results[idx].get("runs", []),
                on_run_complete=persist_retry,
            )
            results[idx] = r
            save_checkpoint(exp_name, summary)
            if INTER_PROBLEM_DELAY > 0:
                await asyncio.sleep(INTER_PROBLEM_DELAY)

    total_correct, total_runs, mean_acc, avg_tok, avg_comp = _agg(results)
    summary.update({
        "solver_mode": solver_mode,
        "n_problems": len(test), "n_runs_per_problem": n_runs,
        "total_correct": total_correct, "total_runs": total_runs,
        "accuracy_micro": round(total_correct / total_runs, 4) if total_runs else 0,
        "accuracy_macro": round(mean_acc, 4),
        "avg_total_tokens": round(avg_tok, 1),
        "avg_completion_tokens": round(avg_comp, 1),
        "problems": results,
    })
    save_checkpoint(exp_name, summary)
    return summary


# ── Evolve + Test Mode ────────────────────────────────────────────────────────

async def run_evolve_and_test(
    train: List[Problem], test: List[Problem],
    n_epochs: int, n_runs: int, tag: str,
    agent_model: str, extract_model: str,
    cfg: DatasetConfig,
    checkpoint: Optional[dict] = None,
    epoch_mode: str = "passes",
    maintenance_refactor_cfg: Optional[MaintenanceRefactorConfig] = None,
    solver_mode: str = "tir",
) -> dict:
    import random as _random
    exp_name = build_exp_name("evolve", tag, epochs=n_epochs, file_prefix=cfg.file_prefix)
    if epoch_mode == "consecutive" and n_epochs > 1:
        exp_name += "_cons"
    summary = checkpoint or {
        "dataset": cfg.name,
        "mode": f"evolve_{n_epochs}ep" + ("_cons" if epoch_mode == "consecutive" else ""),
        "tag": tag,
        "solver_mode": solver_mode,
        "n_epochs": n_epochs, "epoch_mode": epoch_mode,
        "n_train": len(train), "n_test": len(test),
        "n_runs_per_problem": n_runs,
        "evolve": {"accuracy": 0.0, "total_tokens": 0, "avg_tokens": 0.0, "skills_evolved": 0},
        "test_with_skills": {
            "total_correct": 0, "total_runs": 0,
            "accuracy_micro": 0.0, "accuracy_macro": 0.0,
            "avg_total_tokens": 0.0, "avg_completion_tokens": 0.0,
        },
        "evolve_details": [],
        "test_details": [],
    }

    store = SkillStore()
    store_path = RESULTS_DIR / f"{cfg.file_prefix}_{tag}_skills.json"
    if store_path.exists():
        store = SkillStore.load(store_path)
        logger.info(f"Resumed skill store: {store_path}  ({len(store)} skills)")

    # Phase 1: Evolve
    # Build the round schedule depending on epoch_mode:
    #   - 'passes':       N shuffled passes over the full train set (original behaviour)
    #   - 'consecutive':  one pass, but each problem is attempted N_epochs times in a row
    if epoch_mode == "consecutive":
        shuffled = list(train)
        _random.Random(42).shuffle(shuffled)
        schedule = [(0, i, p) for i, p in enumerate(shuffled) for _ in range(n_epochs)]
        logger.info(f"=== EVOLVE [{cfg.name}] (consecutive): {len(train)} problems × {n_epochs} attempts = {len(schedule)} rounds ===")
    else:
        schedule = []
        for epoch in range(n_epochs):
            epoch_train = list(train)
            _random.Random(42 + epoch).shuffle(epoch_train)
            for i, p in enumerate(epoch_train):
                schedule.append((epoch, i, p))
        logger.info(f"=== EVOLVE [{cfg.name}] (passes): {len(train)} × {n_epochs} = {len(schedule)} rounds ===")

    total_rounds = len(schedule)
    evolve_results = summary["evolve_details"]
    completed_evolve = len(evolve_results)
    round_counter = completed_evolve
    maintenance_cfg = maintenance_refactor_cfg or MaintenanceRefactorConfig(enabled=False)
    maintenance_by_epoch: Dict[int, dict] = summary.setdefault("maintenance_refactor", {})
    new_skills_this_epoch = 0

    for global_idx, (epoch, i, prob) in enumerate(schedule):
        if global_idx < completed_evolve:
            continue
        if round_counter >= total_rounds:
            break
        t0 = time.monotonic()
        logger.info(
            f"\n[evolve e{epoch+1} {i+1}/{len(train)} "
            f"(total {round_counter+1}/{total_rounds})] "
            f"skills={len(store)} | {prob.id}"
        )

        relevant = await store.retrieve(prob.question, top_k=5)
        for sk in relevant:
            sk.usage_count += 1

        trace = await solve(
            prob.question, relevant, store=store, llm_config=agent_model,
            system_prompt_template=cfg.system_prompt_template,
            solver_mode=solver_mode,
        )
        elapsed = time.monotonic() - t0
        correct = check_answer(trace.final_answer, prob.answer)

        if correct:
            for sk in relevant:
                sk.success_count += 1

        entry = log_trace(round_counter, 0, prob, trace, correct, elapsed, "evolve",
                          skills_retrieved=relevant)

        new_skills = []
        if trace.code_blocks and not trace.timed_out:
            candidates = await extract_skills(
                query=prob.question,
                code_blocks=trace.code_blocks,
                outputs=trace.outputs,
                existing_skills_prompt=store.build_skills_prompt(relevant),
                llm_config=extract_model,
                reasoning_traces=trace.reasoning_traces or [],
            )
            for sk in candidates:
                candidate = sk
                refinement_history = []
                fixed_test_code = sk.test_code
                for attempt_idx in range(1, MAX_REFINE_ATTEMPTS + 2):
                    tr = test_skill(candidate, store)
                    if tr.passed:
                        old = store.get(candidate.name)
                        old_ver = old.version if old else 0
                        store.add(candidate)
                        cur = store.get(candidate.name)
                        logger.info(
                            f"    skill '{candidate.name}' "
                            + (f"updated v{old_ver}→v{cur.version}" if old_ver > 0 else f"added v1  deps={cur.dependencies}")
                        )
                        new_skills.append(candidate.name)
                        new_skills_this_epoch += 1
                        break
                    logger.info(
                        f"    skill '{candidate.name}' failed test on attempt {attempt_idx}: {tr.error}"
                    )
                    refinement_history.append({
                        "attempt": attempt_idx,
                        "test_error": tr.error,
                        "skill_code": candidate.code,
                    })
                    if attempt_idx > MAX_REFINE_ATTEMPTS:
                        logger.info(f"    skill '{candidate.name}' REJECTED after refine loop")
                        break
                    refined = await refine_skill_after_test_failure(
                        query=prob.question,
                        skill=candidate,
                        test_error=tr.error,
                        fixed_test_code=fixed_test_code,
                        existing_skills_prompt=store.build_skills_prompt(relevant),
                        llm_config=extract_model,
                        refinement_history=refinement_history,
                    )
                    if not refined:
                        logger.info(f"    skill '{candidate.name}' refine returned no candidate")
                        break
                    candidate = refined[0]

        stale_results = test_stale_skills(store)
        for sr in stale_results:
            if not sr.passed:
                logger.info(f"    stale skill '{sr.skill_name}' rolled back: {sr.error}")

        entry["new_skills"] = new_skills
        entry["skills_total"] = len(store)
        if round_counter < len(evolve_results):
            evolve_results[round_counter] = entry
        else:
            evolve_results.append(entry)

        summary["evolve"]["skills_evolved"] = len(store)
        store.save(store_path)
        save_checkpoint(exp_name, summary)

        round_counter += 1
        if INTER_PROBLEM_DELAY > 0 and round_counter < total_rounds:
            await asyncio.sleep(INTER_PROBLEM_DELAY)

        is_last_round_of_epoch = (
            global_idx == len(schedule) - 1 or schedule[global_idx + 1][0] != epoch
        )
        if is_last_round_of_epoch and maintenance_cfg.enabled:
            if new_skills_this_epoch < maintenance_cfg.min_new_skills_since_last_refactor:
                stats_payload = {
                    "runtime_s": 0.0,
                    "attempted": False,
                    "stopped_early": False,
                    "stop_reason": "skip_too_few_new_skills",
                    "n_input_skills": len(store),
                    "n_pairs_considered": 0,
                    "n_pairs_skipped": 0,
                    "n_candidate_groups": 0,
                    "n_shared_helpers": 0,
                    "n_skills_rewritten": 0,
                    "metadata": {"new_skills_this_epoch": new_skills_this_epoch},
                }
                maintenance_by_epoch[str(epoch + 1)] = stats_payload
                logger.info(
                    f"[maintenance_refactor] epoch={epoch+1} skipped "
                    f"(new_skills_this_epoch={new_skills_this_epoch})"
                )
                new_skills_this_epoch = 0
                continue
            stats = run_maintenance_refactor(store, maintenance_cfg)
            maintenance_by_epoch[str(epoch + 1)] = {
                "runtime_s": stats.runtime_s,
                "attempted": stats.attempted,
                "stopped_early": stats.stopped_early,
                "stop_reason": stats.stop_reason,
                "n_input_skills": stats.n_input_skills,
                "n_pairs_considered": stats.n_pairs_considered,
                "n_pairs_skipped": stats.n_pairs_skipped,
                "n_candidate_groups": stats.n_candidate_groups,
                "n_shared_helpers": stats.n_shared_helpers,
                "n_skills_rewritten": stats.n_skills_rewritten,
                "metadata": stats.metadata,
            }
            store.save(store_path)
            save_checkpoint(exp_name, summary)
            logger.info(
                f"[maintenance_refactor] epoch={epoch+1} runtime={stats.runtime_s}s "
                f"shared={stats.n_shared_helpers} rewritten={stats.n_skills_rewritten} "
                f"stopped_early={stats.stopped_early} reason={stats.stop_reason}"
            )
            if stats.stopped_early:
                summary["maintenance_refactor_aborted"] = True
                summary["maintenance_refactor_abort_reason"] = stats.stop_reason
                save_checkpoint(exp_name, summary)
                raise RuntimeError(
                    f"maintenance refactor exceeded runtime budget at epoch {epoch+1}: {stats.stop_reason}"
                )
            new_skills_this_epoch = 0

    store.save(store_path)
    logger.info(f"Skill store saved: {store_path}  ({len(store)} skills)")

    # Phase 2: Test with skills
    stale_results = test_stale_skills(store)
    for sr in stale_results:
        if not sr.passed:
            logger.info(f"  Pre-test stale rollback: '{sr.skill_name}' — {sr.error}")

    logger.info(f"\n=== TEST WITH SKILLS [{cfg.name}]: {len(test)} × {n_runs} runs (concurrency={CONCURRENCY}) ===")
    test_results = summary["test_details"]
    while len(test_results) < len(test):
        test_results.append(None)

    sem = asyncio.Semaphore(CONCURRENCY)
    ck_lock = asyncio.Lock()

    async def _run_test(i: int, prob: Problem):
        existing = test_results[i]
        if existing and existing.get("n_runs", 0) >= n_runs:
            logger.info(f"\n[Test {i+1}/{len(test)}] {prob.id}: resume-skip "
                        f"({existing['n_correct']}/{existing['n_runs']} correct)")
            return
        logger.info(f"\n[Test {i+1}/{len(test)}] {prob.id}: {prob.question[:80]}...")
        relevant = await store.retrieve(prob.question, top_k=5)
        persist = _make_persist(test_results, i, prob, n_runs)

        async with sem:
            r = await run_problem_n_times(
                prob, i, n_runs, skills=relevant, store=store,
                label="test_skills", agent_model=agent_model,
                system_prompt_template=cfg.get_test_prompt(),
                solver_mode=solver_mode,
                existing_runs=(existing or {}).get("runs", []),
                on_run_complete=persist,
            )
            test_results[i] = r
            logger.info(f"  → {r['n_correct']}/{r['n_runs']} correct, avg_tokens={r['avg_total_tokens']}")
            async with ck_lock:
                save_checkpoint(exp_name, summary)

    await asyncio.gather(*[_run_test(i, p) for i, p in enumerate(test)])

    # Retry shelved test runs
    for pass_idx in range(10):
        shelved = [idx for idx, r in enumerate(test_results)
                   if r and any(run and run.get("timed_out") for run in r.get("runs", []))]
        if not shelved:
            logger.info("✓ No timed-out test runs remaining!")
            break
        logger.info(f"\n=== TEST SHELVE RETRY {pass_idx+1}: {len(shelved)} problems ===")
        for idx in shelved:
            prob = test[idx]
            relevant_retry = await store.retrieve(prob.question, top_k=5)
            logger.info(f"\n[Test Retry {idx+1}/{len(test)}] {prob.id}")
            persist_retry = _make_persist(test_results, idx, prob, n_runs)

            r = await run_problem_n_times(
                prob, idx, n_runs, skills=relevant_retry, store=store,
                label="test_skills", agent_model=agent_model,
                system_prompt_template=cfg.get_test_prompt(),
                solver_mode=solver_mode,
                existing_runs=test_results[idx].get("runs", []),
                on_run_complete=persist_retry,
            )
            test_results[idx] = r
            save_checkpoint(exp_name, summary)
            if INTER_PROBLEM_DELAY > 0:
                await asyncio.sleep(INTER_PROBLEM_DELAY)

    evolve_correct = sum(1 for r in evolve_results if r.get("correct"))
    evolve_tokens = sum(r.get("total_tokens", 0) for r in evolve_results)
    test_correct, test_total_runs, test_mean_acc, test_avg_tok, test_avg_comp = _agg(test_results)

    summary.update({
        "dataset": cfg.name,
        "mode": f"evolve_{n_epochs}ep", "tag": tag,
        "solver_mode": solver_mode,
        "n_epochs": n_epochs, "n_train": len(train), "n_test": len(test),
        "n_runs_per_problem": n_runs,
        "evolve": {
            "accuracy": round(evolve_correct / len(evolve_results), 4) if evolve_results else 0,
            "total_tokens": evolve_tokens,
            "avg_tokens": round(evolve_tokens / len(evolve_results), 1) if evolve_results else 0,
            "skills_evolved": len(store),
        },
        "skill_stats": {
            "train": _collect_phase_skill_stats([
                {"runs": [entry]} for entry in evolve_results if entry
            ]),
            "test": _collect_phase_skill_stats(test_results),
        },
        "test_with_skills": {
            "total_correct": test_correct,
            "total_runs": test_total_runs,
            "accuracy_micro": round(test_correct / test_total_runs, 4) if test_total_runs else 0,
            "accuracy_macro": round(test_mean_acc, 4),
            "avg_total_tokens": round(test_avg_tok, 1),
            "avg_completion_tokens": round(test_avg_comp, 1),
        },
        "evolve_details": evolve_results,
        "test_details": test_results,
    })
    save_checkpoint(exp_name, summary)
    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Skill Evolving Experiment Runner")
    parser.add_argument("--dataset", choices=list(DATASET_CONFIGS), required=True,
                        help="Dataset: aime | math500 | math_train | ds100_aime")
    parser.add_argument("--mode", choices=["baseline", "evolve"], required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--epoch_mode", choices=["passes", "consecutive"], default="passes",
                        help="'passes': N shuffled passes over train set (default). "
                             "'consecutive': one pass where each problem is attempted N times in a row.")
    parser.add_argument("--n_runs", type=int, default=4)
    parser.add_argument("--n_train", type=int, default=None,
                        help="Override default train size for dataset")
    parser.add_argument("--n_test", type=int, default=None,
                        help="Override default test size for dataset")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Override default concurrency for dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tag", type=str, default="exp1")
    parser.add_argument("--agent_model", type=str, default=AGENT_MODEL)
    parser.add_argument("--extract_model", type=str, default=EXTRACT_MODEL)
    parser.add_argument("--solver_mode", choices=["tir", "oneshot"], default="tir")
    parser.add_argument("--enable_refactor", action="store_true")
    parser.add_argument("--refactor_mode", choices=["filtered_only"], default="filtered_only")
    parser.add_argument("--refactor_budget_s", type=float, default=90.0)
    parser.add_argument("--refactor_max_skills", type=int, default=24)
    parser.add_argument("--refactor_max_pairs", type=int, default=32)
    args = parser.parse_args()

    cfg = DATASET_CONFIGS[args.dataset]
    n_train = args.n_train if args.n_train is not None else cfg.default_n_train
    n_test = args.n_test if args.n_test is not None else cfg.default_n_test
    concurrency = args.concurrency if args.concurrency is not None else cfg.default_concurrency

    exp_name = build_exp_name(args.mode, args.tag, args.epochs, cfg.file_prefix)

    print(f"\n{'='*60}")
    print(f"Skill Evolving Experiment")
    print(f"  Dataset         : {args.dataset}")
    print(f"  Mode            : {args.mode}")
    print(f"  Epochs          : {args.epochs}")
    print(f"  Runs/problem    : {args.n_runs}")
    print(f"  N train         : {n_train}")
    print(f"  N test          : {n_test}")
    print(f"  Agent model     : {args.agent_model}")
    print(f"  Extract model   : {args.extract_model}")
    print(f"  Solver mode     : {args.solver_mode}")
    print(f"  Refactor enabled: {args.enable_refactor}")
    print(f"  Refactor mode   : {args.refactor_mode}")
    print(f"  Refactor budget : {args.refactor_budget_s}s")
    print(f"  Concurrency     : {concurrency}")
    print(f"  MAX_AGENT_STEPS : {MAX_AGENT_STEPS}")
    print(f"  LLM_CALL_TIMEOUT: {LLM_CALL_TIMEOUT}s")
    print(f"  PROBLEM_DELAY   : {INTER_PROBLEM_DELAY}s")
    print(f"  Tag             : {args.tag}")
    print(f"  Exp name        : {exp_name}")
    print(f"{'='*60}\n", flush=True)

    global CONCURRENCY
    CONCURRENCY = concurrency

    train, test = load_dataset(args.dataset, n_train, n_test, args.seed)
    print(f"Loaded {len(train)} train + {len(test)} test problems\n", flush=True)

    t0 = time.monotonic()
    checkpoint = load_checkpoint(exp_name)
    if checkpoint:
        print(f"Resuming from checkpoint: {exp_name}\n", flush=True)

    if args.mode == "baseline":
        summary = await run_baseline(
            test, args.n_runs, args.tag, args.agent_model, cfg,
            checkpoint=checkpoint, solver_mode=args.solver_mode)
    else:
        maintenance_cfg = MaintenanceRefactorConfig(
            enabled=args.enable_refactor,
            mode=args.refactor_mode,
            max_skills=args.refactor_max_skills,
            max_pairs=args.refactor_max_pairs,
            per_epoch_budget_s=args.refactor_budget_s,
        )
        try:
            summary = await run_evolve_and_test(
                train, test, args.epochs, args.n_runs, args.tag,
                args.agent_model, args.extract_model, cfg, checkpoint=checkpoint,
                epoch_mode=args.epoch_mode,
                maintenance_refactor_cfg=maintenance_cfg,
                solver_mode=args.solver_mode,
            )
        except RuntimeError as exc:
            if checkpoint:
                summary = checkpoint
            else:
                summary = {
                    "dataset": cfg.name,
                    "mode": f"evolve_{args.epochs}ep",
                    "tag": args.tag,
                    "aborted": True,
                }
            summary["aborted"] = True
            summary["abort_reason"] = str(exc)
            save_checkpoint(exp_name, summary)
            print(f"\nABORTED: {exc}\n", flush=True)
            return

    elapsed = time.monotonic() - t0
    summary["total_elapsed_s"] = round(elapsed, 1)
    save_checkpoint(exp_name, summary)

    print(f"\n{'='*60}")
    print(f"RESULTS — {exp_name} ({elapsed:.0f}s)")
    print(f"{'='*60}")
    if args.mode == "baseline":
        print(f"Accuracy (micro)  : {summary['accuracy_micro']:.1%}")
        print(f"Accuracy (macro)  : {summary['accuracy_macro']:.1%}")
        print(f"Avg tokens        : {summary['avg_total_tokens']:.0f}")
    else:
        ev = summary["evolve"]
        ts = summary["test_with_skills"]
        print(f"Evolve accuracy   : {ev['accuracy']:.1%}")
        print(f"Skills evolved    : {ev['skills_evolved']}")
        print(f"Test accuracy (μ) : {ts['accuracy_micro']:.1%}")
        print(f"Test accuracy (M) : {ts['accuracy_macro']:.1%}")
        print(f"Test avg tokens   : {ts['avg_total_tokens']:.0f}")
    print(f"{'='*60}\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
