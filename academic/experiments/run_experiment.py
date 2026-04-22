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
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path.home()))

from academic.config import (
    RESULTS_DIR, INTER_PROBLEM_DELAY, AGENT_MODEL, EXTRACT_MODEL,
    MAX_AGENT_STEPS, LLM_CALL_TIMEOUT,
)
from academic.executor import ExecTrace, solve, SOLVE_SYSTEM, MATH_SOLVE_SYSTEM
from academic.extractor import extract_skills
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


# ── Dataset configuration ─────────────────────────────────────────────────────

@dataclass
class DatasetConfig:
    name: str
    file_prefix: str
    system_prompt_template: str
    default_n_train: int
    default_n_test: int
    default_concurrency: int


DATASET_CONFIGS = {
    "aime": DatasetConfig(
        name="aime",
        file_prefix="aime_v2",
        system_prompt_template=SOLVE_SYSTEM,
        default_n_train=30,
        default_n_test=30,
        default_concurrency=4,
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
              correct: bool, elapsed: float, label: str) -> dict:
    entry = {
        "label": label,
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
                entry = log_trace(prob_idx, run_idx, prob, trace, correct, elapsed, label)
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
) -> dict:
    exp_name = build_exp_name("baseline", tag, epochs=1, file_prefix=cfg.file_prefix)
    logger.info(f"=== BASELINE [{cfg.name}]: {len(test)} × {n_runs} runs (concurrency={CONCURRENCY}) ===")

    summary = checkpoint or {
        "dataset": cfg.name, "mode": "baseline", "tag": tag,
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
                system_prompt_template=cfg.system_prompt_template,
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
                system_prompt_template=cfg.system_prompt_template,
                existing_runs=results[idx].get("runs", []),
                on_run_complete=persist_retry,
            )
            results[idx] = r
            save_checkpoint(exp_name, summary)
            if INTER_PROBLEM_DELAY > 0:
                await asyncio.sleep(INTER_PROBLEM_DELAY)

    total_correct, total_runs, mean_acc, avg_tok, avg_comp = _agg(results)
    summary.update({
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
) -> dict:
    import random as _random
    exp_name = build_exp_name("evolve", tag, epochs=n_epochs, file_prefix=cfg.file_prefix)
    summary = checkpoint or {
        "dataset": cfg.name,
        "mode": f"evolve_{n_epochs}ep", "tag": tag,
        "n_epochs": n_epochs, "n_train": len(train), "n_test": len(test),
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
    total_rounds = len(train) * n_epochs
    logger.info(f"=== EVOLVE [{cfg.name}]: {len(train)} × {n_epochs} = {total_rounds} rounds ===")
    evolve_results = summary["evolve_details"]
    completed_evolve = len(evolve_results)
    round_counter = completed_evolve

    for epoch in range(n_epochs):
        epoch_train = list(train)
        _random.Random(42 + epoch).shuffle(epoch_train)
        logger.info(f"--- Epoch {epoch+1}/{n_epochs} ({len(epoch_train)} problems) ---")

        for i, prob in enumerate(epoch_train):
            global_idx = epoch * len(train) + i
            if round_counter >= total_rounds:
                break
            if global_idx < completed_evolve:
                continue

            t0 = time.monotonic()
            logger.info(
                f"\n[evolve e{epoch+1} {i+1}/{len(epoch_train)} "
                f"(total {round_counter+1}/{total_rounds})] "
                f"skills={len(store)} | {prob.id}"
            )

            relevant = await store.retrieve(prob.question, top_k=5)
            for sk in relevant:
                sk.usage_count += 1

            trace = await solve(
                prob.question, relevant, store=store, llm_config=agent_model,
                system_prompt_template=cfg.system_prompt_template,
            )
            elapsed = time.monotonic() - t0
            correct = check_answer(trace.final_answer, prob.answer)

            if correct:
                for sk in relevant:
                    sk.success_count += 1

            entry = log_trace(round_counter, 0, prob, trace, correct, elapsed, "evolve")

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
                    tr = test_skill(sk, store)
                    if tr.passed:
                        old = store.get(sk.name)
                        old_ver = old.version if old else 0
                        store.add(sk)
                        cur = store.get(sk.name)
                        logger.info(
                            f"    skill '{sk.name}' "
                            + (f"updated v{old_ver}→v{cur.version}" if old_ver > 0 else f"added v1  deps={cur.dependencies}")
                        )
                        new_skills.append(sk.name)
                    else:
                        logger.info(f"    skill '{sk.name}' REJECTED: {tr.error}")

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
                system_prompt_template=cfg.system_prompt_template,
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
                system_prompt_template=cfg.system_prompt_template,
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
        "n_epochs": n_epochs, "n_train": len(train), "n_test": len(test),
        "n_runs_per_problem": n_runs,
        "evolve": {
            "accuracy": round(evolve_correct / len(evolve_results), 4) if evolve_results else 0,
            "total_tokens": evolve_tokens,
            "avg_tokens": round(evolve_tokens / len(evolve_results), 1) if evolve_results else 0,
            "skills_evolved": len(store),
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
                        help="Dataset to use: aime or math500")
    parser.add_argument("--mode", choices=["baseline", "evolve"], required=True)
    parser.add_argument("--epochs", type=int, default=1)
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
            test, args.n_runs, args.tag, args.agent_model, cfg, checkpoint=checkpoint)
    else:
        summary = await run_evolve_and_test(
            train, test, args.epochs, args.n_runs, args.tag,
            args.agent_model, args.extract_model, cfg, checkpoint=checkpoint,
        )

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
