"""
run_aime_v2.py — AIME experiment runner v2.

Key changes from v1:
  - 4 runs per problem (configurable), take average accuracy
  - max_tokens=2000, temperature=1.0 (set in config.toml [llm.bigmodel])
  - Infinite retry on timeout (never gives up)
  - Grading via ~/grading (sympy-based robust comparison)
  - Structured logs with full model outputs
  - Separate modes: baseline-only, 1-epoch, multi-epoch

Usage:
    cd ~/skill_evolving
    # Baseline (4 runs × 30 problems = 120 calls):
    python -u -m academic.experiments.run_aime_v2 --mode baseline --tag v2

    # 1-epoch skill evolving + test (4 runs):
    python -u -m academic.experiments.run_aime_v2 --mode evolve --epochs 1 --tag v2

    # 3-epoch skill evolving + test (4 runs):
    python -u -m academic.experiments.run_aime_v2 --mode evolve --epochs 3 --tag v2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# Setup paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path.home()))

from academic.config import (
    RESULTS_DIR, INTER_PROBLEM_DELAY, AGENT_MODEL, EXTRACT_MODEL,
    MAX_AGENT_STEPS, LLM_CALL_TIMEOUT,
)
from academic.executor import ExecTrace, solve
from academic.extractor import extract_skills
from academic.skill_store import SkillStore
from academic.tester import test_skill, test_stale_skills
from academic.datasets.aime_dataset import load_train_test
from academic.pipeline import Problem, check_answer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tool_evolving")


def build_exp_name(mode: str, tag: str, epochs: int) -> str:
    exp_name = f"aime_v2_{tag}_{mode}"
    if mode == "evolve":
        exp_name += f"_{epochs}ep"
    return exp_name


def save_checkpoint(exp_name: str, summary: dict) -> None:
    detail_path = RESULTS_DIR / f"{exp_name}_detail.json"
    detail_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    compact = {k: v for k, v in summary.items()
               if k not in ("evolve_details", "test_details", "problems")}
    compact_path = RESULTS_DIR / f"{exp_name}_summary.json"
    compact_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2))


def load_checkpoint(exp_name: str) -> Optional[dict]:
    detail_path = RESULTS_DIR / f"{exp_name}_detail.json"
    if not detail_path.exists():
        return None
    return json.loads(detail_path.read_text())


# ── Structured Logging ──────────────────────────────────────────────────────

def log_trace(prob_idx: int, run_idx: int, prob: Problem, trace: ExecTrace,
              correct: bool, elapsed: float, label: str) -> dict:
    """Log and return structured trace data."""
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
        "steps": [],
        "conversation": trace.messages,  # full conversation log
    }

    # Record each step in human-readable format
    for si, step in enumerate(trace.steps):
        step_entry = {
            "step": si,
            "type": step["type"],
            "content": step["content"],
        }
        entry["steps"].append(step_entry)

    # Console summary
    status = "✓" if correct else "✗"
    logger.info(
        f"  [{label}] P{prob_idx} R{run_idx} {status} "
        f"tokens={trace.total_tokens} comp={trace.completion_tokens} "
        f"steps={len(trace.steps)} elapsed={elapsed:.0f}s "
        f"pred={trace.final_answer}"
    )

    return entry


# ── Run a single problem N times ────────────────────────────────────────────

MAX_RUN_ATTEMPTS = 3  # max attempts per run before shelving


async def run_problem_n_times(
    prob: Problem,
    prob_idx: int,
    n_runs: int,
    skills: list,
    store: Optional[SkillStore],
    label: str,
    agent_model: str,
    existing_runs: Optional[list] = None,
    on_run_complete: Optional[Callable[[list], None]] = None,
) -> dict:
    """Run a problem n_runs times, return per-run results + aggregate.

    Supports timeout shelving: if a run times out after retries, a timed_out
    placeholder is saved and the function moves on to the next run.
    """
    runs = list(existing_runs or [])
    # Pad to n_runs so we can index all positions
    while len(runs) < n_runs:
        runs.append(None)

    for run_idx in range(n_runs):
        existing = runs[run_idx]
        # Skip completed, non-timeout runs
        if existing and not existing.get("timed_out"):
            continue

        entry = None
        resume_state = None
        # If retrying a timed-out run, use its partial_state for resume
        if existing and existing.get("timed_out") and existing.get("partial_state"):
            resume_state = existing["partial_state"]

        for attempt in range(1, MAX_RUN_ATTEMPTS + 1):
            try:
                t0 = time.monotonic()
                if store and skills:
                    trace = await solve(prob.question, skills, store=store,
                                        llm_config=agent_model, resume_state=resume_state)
                elif skills:
                    trace = await solve(prob.question, skills, llm_config=agent_model,
                                        resume_state=resume_state)
                else:
                    trace = await solve(prob.question, [], llm_config=agent_model,
                                        resume_state=resume_state)
                elapsed = time.monotonic() - t0

                if trace.timed_out:
                    logger.info(
                        f"  [{label}] P{prob_idx} R{run_idx} ⏱ TIMEOUT "
                        f"(attempt {attempt}/{MAX_RUN_ATTEMPTS})"
                    )
                    # Save partial state for future resume
                    resume_state = trace.partial_state
                    if attempt < MAX_RUN_ATTEMPTS:
                        await asyncio.sleep(60)
                        continue

                    logger.info(
                        f"  [{label}] P{prob_idx} R{run_idx} ⏱ SHELVED "
                        f"after {MAX_RUN_ATTEMPTS} attempts"
                    )
                    break

                correct = check_answer(trace.final_answer, prob.answer)
                entry = log_trace(prob_idx, run_idx, prob, trace, correct, elapsed, label)
                break
            except Exception as exc:
                if attempt >= MAX_RUN_ATTEMPTS:
                    logger.warning(
                        f"  [{label}] P{prob_idx} R{run_idx} SHELVED "
                        f"(crash: {type(exc).__name__})"
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

    avg_tokens = (
        sum(r.get("total_tokens", 0) for r in completed_runs) / n_completed
        if n_completed > 0 else 0.0
    )
    avg_comp = (
        sum(r.get("completion_tokens", 0) for r in completed_runs) / n_completed
        if n_completed > 0 else 0.0
    )

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


# ── Baseline Mode ───────────────────────────────────────────────────────────

CONCURRENCY = 4  # run this many problems in parallel


async def run_baseline(test: List[Problem], n_runs: int, tag: str,
                       agent_model: str, checkpoint: Optional[dict] = None) -> dict:
    """Run baseline (no skills) on all test problems, n_runs each."""
    logger.info(f"=== BASELINE: {len(test)} problems × {n_runs} runs (concurrency={CONCURRENCY}) ===")
    exp_name = build_exp_name("baseline", tag, epochs=1)
    summary = checkpoint or {
        "mode": "baseline",
        "tag": tag,
        "n_problems": len(test),
        "n_runs_per_problem": n_runs,
        "total_correct": 0,
        "total_runs": 0,
        "accuracy_micro": 0.0,
        "accuracy_macro": 0.0,
        "avg_total_tokens": 0.0,
        "avg_completion_tokens": 0.0,
        "problems": [],
    }
    results = summary["problems"]
    # Pre-fill results list so we can index by position
    while len(results) < len(test):
        results.append(None)

    sem = asyncio.Semaphore(CONCURRENCY)
    checkpoint_lock = asyncio.Lock()

    async def _run_one(i: int, prob: Problem):
        # Stagger the first CONCURRENCY tasks to avoid initial rate-limit burst
        # if i < CONCURRENCY:
        #     await asyncio.sleep(i * 10)

        existing = results[i]
        if existing and existing.get("n_runs", 0) >= n_runs:
            logger.info(
                f"\n[Baseline {i+1}/{len(test)}] {prob.id}: resume-skip "
                f"({existing['n_correct']}/{existing['n_runs']} correct)"
            )
            return

        logger.info(f"\n[Baseline {i+1}/{len(test)}] {prob.id}: {prob.question[:80]}...")

        def persist_partial(runs_for_problem: list, _i=i, _prob=prob) -> None:
            valid = [r for r in runs_for_problem if r and not r.get("timed_out")]
            n_valid = len(valid)
            results[_i] = {
                "problem_idx": _i,
                "problem_id": _prob.id,
                "question": _prob.question[:120],
                "expected": _prob.answer,
                "n_runs": sum(1 for r in runs_for_problem if r is not None),
                "n_correct": sum(1 for r in valid if r.get("correct")),
                "accuracy": (
                    sum(1 for r in valid if r.get("correct")) / n_runs
                    if n_runs > 0 else 0.0
                ),
                "avg_total_tokens": round(
                    sum(r.get("total_tokens", 0) for r in valid) / n_valid, 1
                ) if n_valid > 0 else 0.0,
                "avg_completion_tokens": round(
                    sum(r.get("completion_tokens", 0) for r in valid) / n_valid, 1
                ) if n_valid > 0 else 0.0,
                "has_timeout": any(r and r.get("timed_out") for r in runs_for_problem),
                "runs": runs_for_problem,
            }

        async with sem:
            r = await run_problem_n_times(
                prob, i, n_runs, skills=[], store=None,
                label="baseline", agent_model=agent_model,
                existing_runs=(existing or {}).get("runs", []),
                on_run_complete=persist_partial,
            )
            results[i] = r
            logger.info(
                f"  → {r['n_correct']}/{r['n_runs']} correct, "
                f"avg_tokens={r['avg_total_tokens']}"
            )
            async with checkpoint_lock:
                save_checkpoint(exp_name, summary)

    tasks = [_run_one(i, prob) for i, prob in enumerate(test)]
    await asyncio.gather(*tasks)

    # ── Retry shelved (timed-out) runs ──────────────────────────────
    MAX_SHELVE_PASSES = 10
    for shelve_pass in range(MAX_SHELVE_PASSES):
        shelved_indices = [
            idx for idx, r in enumerate(results)
            if any(run and run.get("timed_out") for run in r.get("runs", []))
        ]
        if not shelved_indices:
            logger.info("✓ No timed-out baseline runs remaining — all clean!")
            break
        logger.info(
            f"\n=== SHELVE RETRY {shelve_pass+1}/{MAX_SHELVE_PASSES}: "
            f"{len(shelved_indices)} problems with timeouts ==="
        )
        for idx in shelved_indices:
            prob = test[idx]
            logger.info(f"\n[Retry {idx+1}/{len(test)}] {prob.id}")

            def _persist_retry(runs_list: list, _idx=idx, _prob=prob) -> None:
                valid = [r for r in runs_list if r and not r.get("timed_out")]
                n_v = len(valid)
                results[_idx] = {
                    "problem_idx": _idx,
                    "problem_id": _prob.id,
                    "question": _prob.question[:120],
                    "expected": _prob.answer,
                    "n_runs": n_runs,
                    "n_correct": sum(1 for r in valid if r.get("correct")),
                    "accuracy": (
                        sum(1 for r in valid if r.get("correct")) / n_runs
                        if n_runs > 0 else 0.0
                    ),
                    "avg_total_tokens": round(
                        sum(r.get("total_tokens", 0) for r in valid) / n_v, 1
                    ) if n_v else 0.0,
                    "avg_completion_tokens": round(
                        sum(r.get("completion_tokens", 0) for r in valid) / n_v, 1
                    ) if n_v else 0.0,
                    "has_timeout": any(r and r.get("timed_out") for r in runs_list),
                    "runs": runs_list,
                }
                save_checkpoint(exp_name, summary)

            r = await run_problem_n_times(
                prob, idx, n_runs, skills=[], store=None,
                label="baseline", agent_model=agent_model,
                existing_runs=results[idx].get("runs", []),
                on_run_complete=_persist_retry,
            )
            results[idx] = r
            save_checkpoint(exp_name, summary)
            if INTER_PROBLEM_DELAY > 0:
                await asyncio.sleep(INTER_PROBLEM_DELAY)

    # Aggregate
    total_correct = sum(r["n_correct"] for r in results)
    total_runs = sum(r["n_runs"] for r in results)
    avg_accuracy = total_correct / total_runs if total_runs > 0 else 0
    per_problem_acc = [r["accuracy"] for r in results]
    mean_per_problem = sum(per_problem_acc) / len(per_problem_acc)
    avg_tokens = sum(r["avg_total_tokens"] for r in results) / len(results)
    avg_comp = sum(r["avg_completion_tokens"] for r in results) / len(results)

    summary.update({
        "n_problems": len(test),
        "n_runs_per_problem": n_runs,
        "total_correct": total_correct,
        "total_runs": total_runs,
        "accuracy_micro": round(avg_accuracy, 4),
        "accuracy_macro": round(mean_per_problem, 4),
        "avg_total_tokens": round(avg_tokens, 1),
        "avg_completion_tokens": round(avg_comp, 1),
        "problems": results,
    })
    save_checkpoint(exp_name, summary)
    return summary


# ── Evolve + Test Mode ──────────────────────────────────────────────────────

async def run_evolve_and_test(
    train: List[Problem], test: List[Problem],
    n_epochs: int, n_runs: int, tag: str,
    agent_model: str, extract_model: str,
    checkpoint: Optional[dict] = None,
) -> dict:
    """
    Phase 1: Evolve skills on training set (1 run per problem, multi-epoch).
    Phase 2: Test WITH skills (n_runs per problem).
    """
    import random as _random
    exp_name = build_exp_name("evolve", tag, epochs=n_epochs)
    summary = checkpoint or {
        "mode": f"evolve_{n_epochs}ep",
        "tag": tag,
        "n_epochs": n_epochs,
        "n_train": len(train),
        "n_test": len(test),
        "n_runs_per_problem": n_runs,
        "evolve": {
            "accuracy": 0.0,
            "total_tokens": 0,
            "avg_tokens": 0.0,
            "skills_evolved": 0,
        },
        "test_with_skills": {
            "total_correct": 0,
            "total_runs": 0,
            "accuracy_micro": 0.0,
            "accuracy_macro": 0.0,
            "avg_total_tokens": 0.0,
            "avg_completion_tokens": 0.0,
        },
        "evolve_details": [],
        "test_details": [],
    }

    store = SkillStore()
    store_path = RESULTS_DIR / f"aime_v2_{tag}_skills.json"
    if store_path.exists():
        store = SkillStore.load(store_path)
        logger.info(f"Resumed skill store: {store_path}  ({len(store)} skills)")

    # ── Phase 1: Evolve ──────────────────────────────────────────────
    total_rounds = len(train) * n_epochs
    logger.info(f"=== EVOLVE: {len(train)} problems × {n_epochs} epochs = {total_rounds} rounds ===")

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

            trace = await solve(prob.question, relevant, store=store, llm_config=agent_model)
            elapsed = time.monotonic() - t0
            correct = check_answer(trace.final_answer, prob.answer)

            if correct:
                for sk in relevant:
                    sk.success_count += 1

            entry = log_trace(round_counter, 0, prob, trace, correct, elapsed, "evolve")

            # Extract and test skills
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
                        if old_ver > 0:
                            logger.info(f"    skill '{sk.name}' updated v{old_ver}→v{cur.version}")
                        else:
                            logger.info(f"    skill '{sk.name}' added v1  deps={cur.dependencies}")
                        new_skills.append(sk.name)
                    else:
                        logger.info(f"    skill '{sk.name}' REJECTED: {tr.error}")

            # Stale skill check
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

    # Save skill store
    store.save(store_path)
    logger.info(f"Skill store saved: {store_path}  ({len(store)} skills)")

    # ── Phase 2: Test WITH skills ────────────────────────────────────
    stale_results = test_stale_skills(store)
    for sr in stale_results:
        if not sr.passed:
            logger.info(f"  Pre-test stale rollback: '{sr.skill_name}' — {sr.error}")

    logger.info(f"\n=== TEST WITH SKILLS: {len(test)} problems × {n_runs} runs (concurrency={CONCURRENCY}) ===")
    test_results = summary["test_details"]
    while len(test_results) < len(test):
        test_results.append(None)

    sem = asyncio.Semaphore(CONCURRENCY)
    checkpoint_lock = asyncio.Lock()

    async def _run_test(i: int, prob: Problem):
        existing = test_results[i]
        if existing and existing.get("n_runs", 0) >= n_runs:
            logger.info(
                f"\n[Test+Skills {i+1}/{len(test)}] {prob.id}: resume-skip "
                f"({existing['n_correct']}/{existing['n_runs']} correct)"
            )
            return

        logger.info(f"\n[Test+Skills {i+1}/{len(test)}] {prob.id}: {prob.question[:80]}...")
        relevant = await store.retrieve(prob.question, top_k=5)

        def persist_partial(runs_for_problem: list, _i=i, _prob=prob) -> None:
            valid = [r for r in runs_for_problem if r and not r.get("timed_out")]
            n_valid = len(valid)
            test_results[_i] = {
                "problem_idx": _i,
                "problem_id": _prob.id,
                "question": _prob.question[:120],
                "expected": _prob.answer,
                "n_runs": sum(1 for r in runs_for_problem if r is not None),
                "n_correct": sum(1 for r in valid if r.get("correct")),
                "accuracy": (
                    sum(1 for r in valid if r.get("correct")) / n_runs
                    if n_runs > 0 else 0.0
                ),
                "avg_total_tokens": round(
                    sum(r.get("total_tokens", 0) for r in valid) / n_valid, 1
                ) if n_valid > 0 else 0.0,
                "avg_completion_tokens": round(
                    sum(r.get("completion_tokens", 0) for r in valid) / n_valid, 1
                ) if n_valid > 0 else 0.0,
                "has_timeout": any(r and r.get("timed_out") for r in runs_for_problem),
                "runs": runs_for_problem,
            }

        async with sem:
            r = await run_problem_n_times(
                prob, i, n_runs, skills=relevant, store=store,
                label="test_skills", agent_model=agent_model,
                existing_runs=(existing or {}).get("runs", []),
                on_run_complete=persist_partial,
            )
            test_results[i] = r
            logger.info(
                f"  → {r['n_correct']}/{r['n_runs']} correct, "
                f"avg_tokens={r['avg_total_tokens']}"
            )
            async with checkpoint_lock:
                save_checkpoint(exp_name, summary)

    tasks = [_run_test(i, prob) for i, prob in enumerate(test)]
    await asyncio.gather(*tasks)

    # ── Retry shelved (timed-out) test runs ─────────────────────────
    MAX_SHELVE_PASSES = 10
    for shelve_pass in range(MAX_SHELVE_PASSES):
        shelved_indices = [
            idx for idx, r in enumerate(test_results)
            if any(run and run.get("timed_out") for run in r.get("runs", []))
        ]
        if not shelved_indices:
            logger.info("✓ No timed-out test runs remaining — all clean!")
            break
        logger.info(
            f"\n=== TEST SHELVE RETRY {shelve_pass+1}/{MAX_SHELVE_PASSES}: "
            f"{len(shelved_indices)} problems with timeouts ==="
        )
        for idx in shelved_indices:
            prob = test[idx]
            relevant = retrieve_skills(store, prob.question, top_k=5) if store else []
            logger.info(f"\n[Test Retry {idx+1}/{len(test)}] {prob.id}")

            def _persist_test_retry(runs_list: list, _idx=idx, _prob=prob) -> None:
                valid = [r for r in runs_list if r and not r.get("timed_out")]
                n_v = len(valid)
                test_results[_idx] = {
                    "problem_idx": _idx,
                    "problem_id": _prob.id,
                    "question": _prob.question[:120],
                    "expected": _prob.answer,
                    "n_runs": n_runs,
                    "n_correct": sum(1 for r in valid if r.get("correct")),
                    "accuracy": (
                        sum(1 for r in valid if r.get("correct")) / n_runs
                        if n_runs > 0 else 0.0
                    ),
                    "avg_total_tokens": round(
                        sum(r.get("total_tokens", 0) for r in valid) / n_v, 1
                    ) if n_v else 0.0,
                    "avg_completion_tokens": round(
                        sum(r.get("completion_tokens", 0) for r in valid) / n_v, 1
                    ) if n_v else 0.0,
                    "has_timeout": any(r and r.get("timed_out") for r in runs_list),
                    "runs": runs_list,
                }
                save_checkpoint(exp_name, summary)

            r = await run_problem_n_times(
                prob, idx, n_runs, skills=relevant, store=store,
                label="test_skills", agent_model=agent_model,
                existing_runs=test_results[idx].get("runs", []),
                on_run_complete=_persist_test_retry,
            )
            test_results[idx] = r
            save_checkpoint(exp_name, summary)
            if INTER_PROBLEM_DELAY > 0:
                await asyncio.sleep(INTER_PROBLEM_DELAY)

    # Aggregate evolve stats
    evolve_correct = sum(1 for r in evolve_results if r["correct"])
    evolve_tokens = sum(r["total_tokens"] for r in evolve_results)

    # Aggregate test stats
    test_total_correct = sum(r["n_correct"] for r in test_results)
    test_total_runs = sum(r["n_runs"] for r in test_results)
    test_avg_accuracy = test_total_correct / test_total_runs if test_total_runs > 0 else 0
    test_per_problem_acc = [r["accuracy"] for r in test_results]
    test_mean_per_problem = sum(test_per_problem_acc) / len(test_per_problem_acc)
    test_avg_tokens = sum(r["avg_total_tokens"] for r in test_results) / len(test_results)
    test_avg_comp = sum(r["avg_completion_tokens"] for r in test_results) / len(test_results)

    summary.update({
        "mode": f"evolve_{n_epochs}ep",
        "tag": tag,
        "n_epochs": n_epochs,
        "n_train": len(train),
        "n_test": len(test),
        "n_runs_per_problem": n_runs,
        "evolve": {
            "accuracy": round(evolve_correct / len(evolve_results), 4),
            "total_tokens": evolve_tokens,
            "avg_tokens": round(evolve_tokens / len(evolve_results), 1),
            "skills_evolved": len(store),
        },
        "test_with_skills": {
            "total_correct": test_total_correct,
            "total_runs": test_total_runs,
            "accuracy_micro": round(test_avg_accuracy, 4),
            "accuracy_macro": round(test_mean_per_problem, 4),
            "avg_total_tokens": round(test_avg_tokens, 1),
            "avg_completion_tokens": round(test_avg_comp, 1),
        },
        "evolve_details": evolve_results,
        "test_details": test_results,
    })
    save_checkpoint(exp_name, summary)
    return summary


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="AIME Experiment v2")
    parser.add_argument("--mode", choices=["baseline", "evolve"], required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--n_runs", type=int, default=4, help="Runs per test problem")
    parser.add_argument("--n_train", type=int, default=None)
    parser.add_argument("--n_test", type=int, default=None)
    parser.add_argument('--concurrency', type=int, default=4, help="Number of concurrent problems to run")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tag", type=str, default="v2")
    parser.add_argument("--agent_model", type=str, default=AGENT_MODEL)
    parser.add_argument("--extract_model", type=str, default=EXTRACT_MODEL)
    args = parser.parse_args()
    exp_name = build_exp_name(args.mode, args.tag, args.epochs)

    print(f"\n{'='*60}")
    print(f"AIME Experiment v2")
    print(f"  Mode            : {args.mode}")
    print(f"  Epochs          : {args.epochs}")
    print(f"  Runs/problem    : {args.n_runs}")
    print(f"  Agent model     : {args.agent_model}")
    print(f"  Extract model   : {args.extract_model}")
    print(f"  Concurrency     : {args.concurrency}")
    print(f"  MAX_AGENT_STEPS : {MAX_AGENT_STEPS}")
    print(f"  LLM_CALL_TIMEOUT: {LLM_CALL_TIMEOUT}s")
    print(f"  PROBLEM_DELAY   : {INTER_PROBLEM_DELAY}s")
    print(f"  Tag             : {args.tag}")
    print(f"{'='*60}\n", flush=True)

    global CONCURRENCY
    CONCURRENCY = args.concurrency
    train, test = load_train_test(
        n_train=args.n_train, n_test=args.n_test, seed=args.seed
    )
    print(f"Loaded {len(train)} train + {len(test)} test problems\n", flush=True)

    t0 = time.monotonic()

    checkpoint = load_checkpoint(exp_name)
    if checkpoint:
        print(f"Resuming from checkpoint: {exp_name}\n", flush=True)

    if args.mode == "baseline":
        summary = await run_baseline(test, args.n_runs, args.tag, args.agent_model, checkpoint=checkpoint)
    else:
        summary = await run_evolve_and_test(
            train, test, args.epochs, args.n_runs, args.tag,
            args.agent_model, args.extract_model, checkpoint=checkpoint,
        )

    elapsed = time.monotonic() - t0

    # Save results
    summary["total_elapsed_s"] = round(elapsed, 1)
    save_checkpoint(exp_name, summary)
    logger.info(f"Detailed results: {RESULTS_DIR / f'{exp_name}_detail.json'}")

    # Print final summary
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
