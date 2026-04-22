"""
run_baseline_only.py — Baseline-only test on AIME 2025.

Runs all test problems WITHOUT any skills to establish the baseline
with current settings (max_tokens=8000, MAX_AGENT_STEPS=15).

Usage:
    cd ~/skill_evolving
    conda activate meta-agent
    python -u -m academic.experiments.run_baseline_only --tag exp7
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from academic.config import RESULTS_DIR, MAX_AGENT_STEPS, LLM_CALL_TIMEOUT, LLM_TIMEOUT_RETRIES, INTER_PROBLEM_DELAY
from academic.datasets.aime_dataset import load_train_test
from academic.executor import solve
from academic.pipeline import ExperimentResult, RoundMetrics, check_answer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tool_evolving")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline-only AIME 2025 test")
    parser.add_argument("--n_test", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tag", type=str, default="baseline")
    args = parser.parse_args()

    exp_name = f"aime_{args.tag}_baseline"

    # Print settings for reproducibility
    print(f"\n{'='*60}")
    print(f"BASELINE-ONLY Test — AIME 2025")
    print(f"  max_tokens      : 8000 (from config.toml)")
    print(f"  MAX_AGENT_STEPS : {MAX_AGENT_STEPS}")
    print(f"  LLM_CALL_TIMEOUT: {LLM_CALL_TIMEOUT}s")
    print(f"  TIMEOUT_RETRIES : {LLM_TIMEOUT_RETRIES}")
    print(f"  PROBLEM_DELAY   : {INTER_PROBLEM_DELAY}s")
    print(f"  Experiment name : {exp_name}")
    print(f"{'='*60}\n")

    _, test = load_train_test(n_test=args.n_test, seed=args.seed)
    print(f"Loaded {len(test)} test problems (AIME 2025)")

    t0 = time.monotonic()
    result = ExperimentResult(name=exp_name)
    consecutive_timeouts = 0

    for i, prob in enumerate(test):
        pt0 = time.monotonic()
        trace = await solve(prob.question, [])  # no skills
        elapsed = time.monotonic() - pt0
        correct = check_answer(trace.final_answer, prob.answer)
        code_lines = sum(c.count("\n") + 1 for c in trace.code_blocks)

        if trace.timed_out:
            consecutive_timeouts += 1
            logger.warning(f"  ⚠ TIMEOUT Q{i+1} (consecutive: {consecutive_timeouts}/3)")
            if consecutive_timeouts >= 3:
                logger.error("  ✖ 3 consecutive timeouts — HALTING baseline")
                result.save()
                raise RuntimeError(f"Baseline halted: 3 consecutive timeouts at Q{i+1}")
        else:
            consecutive_timeouts = 0

        result.rounds.append(RoundMetrics(
            round_idx=i, query=prob.question[:100],
            answer_correct=correct, predicted=trace.final_answer,
            expected=prob.answer, tokens=trace.total_tokens,
            completion_tokens=trace.completion_tokens,
            code_lines=code_lines, skills_used=0,
            skills_total=0, elapsed_s=elapsed,
            new_skills_extracted=0,
        ))
        status = "✓" if correct else "✗"
        logger.info(
            f"  [{i+1}/{len(test)}] {status} tokens={trace.total_tokens:5d} "
            f"comp={trace.completion_tokens:5d} elapsed={elapsed:.0f}s "
            f"timed_out={trace.timed_out}"
        )

        if INTER_PROBLEM_DELAY > 0 and i < len(test) - 1:
            await asyncio.sleep(INTER_PROBLEM_DELAY)

    result.save()
    elapsed_total = time.monotonic() - t0

    n_correct = sum(1 for r in result.rounds if r.answer_correct)
    n_timeout = sum(1 for r in result.rounds if r.tokens == 0)
    avg_tokens = result.avg_tokens

    print(f"\n{'='*60}")
    print(f"BASELINE RESULTS — {exp_name}")
    print(f"{'='*60}")
    print(f"Accuracy   : {n_correct}/{len(test)} = {n_correct/len(test)*100:.1f}%")
    print(f"Timeouts   : {n_timeout}/{len(test)}")
    print(f"Avg tokens : {avg_tokens:.0f}")
    print(f"Total time : {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
