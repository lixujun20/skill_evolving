"""
complete_baseline.py — Complete the remaining baseline problems (19-30).

The full experiment (run_aime.py) was interrupted when SiliconFlow API
ran out of credits at baseline problem 19. This script resumes from
problem 19 using the BigModel API, then merges results.

Usage:
    cd ~/skill_evolving
    TE_AGENT_MODEL=bigmodel python -m academic.experiments.complete_baseline
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
import time
from pathlib import Path

from academic.config import RESULTS_DIR
from academic.datasets.aime_dataset import load_aime_2025
from academic.executor import solve
from academic.pipeline import RoundMetrics, ExperimentResult, check_answer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tool_evolving")

# Existing valid baseline results from run 1 (problems 1-18)
EXISTING_BASELINE = [
    {"idx": 1, "correct": True, "tokens": 33080},
    {"idx": 2, "correct": True, "tokens": 9045},
    {"idx": 3, "correct": True, "tokens": 20860},
    {"idx": 4, "correct": True, "tokens": 20111},
    {"idx": 5, "correct": True, "tokens": 7367},
    {"idx": 6, "correct": True, "tokens": 17506},
    {"idx": 7, "correct": True, "tokens": 3753},
    {"idx": 8, "correct": False, "tokens": 0},
    {"idx": 9, "correct": True, "tokens": 19352},
    {"idx": 10, "correct": True, "tokens": 2905},
    {"idx": 11, "correct": False, "tokens": 0},
    {"idx": 12, "correct": True, "tokens": 11017},
    {"idx": 13, "correct": False, "tokens": 0},
    {"idx": 14, "correct": True, "tokens": 4901},
    {"idx": 15, "correct": True, "tokens": 1755},
    {"idx": 16, "correct": False, "tokens": 0},
    {"idx": 17, "correct": False, "tokens": 0},
    {"idx": 18, "correct": False, "tokens": 0},
]

# Test with skills results (all 30, complete)
TEST_WITH_SKILLS = [
    {"idx": 1, "correct": True, "tokens": 13948},
    {"idx": 2, "correct": True, "tokens": 8713},
    {"idx": 3, "correct": False, "tokens": 0},
    {"idx": 4, "correct": True, "tokens": 7945},
    {"idx": 5, "correct": True, "tokens": 12909},
    {"idx": 6, "correct": True, "tokens": 14049},
    {"idx": 7, "correct": True, "tokens": 3692},
    {"idx": 8, "correct": False, "tokens": 0},
    {"idx": 9, "correct": False, "tokens": 0},
    {"idx": 10, "correct": True, "tokens": 3339},
    {"idx": 11, "correct": False, "tokens": 0},
    {"idx": 12, "correct": True, "tokens": 10834},
    {"idx": 13, "correct": False, "tokens": 0},
    {"idx": 14, "correct": True, "tokens": 5486},
    {"idx": 15, "correct": True, "tokens": 2450},
    {"idx": 16, "correct": False, "tokens": 0},
    {"idx": 17, "correct": True, "tokens": 20277},
    {"idx": 18, "correct": False, "tokens": 0},
    {"idx": 19, "correct": True, "tokens": 8130},
    {"idx": 20, "correct": True, "tokens": 2693},
    {"idx": 21, "correct": True, "tokens": 7618},
    {"idx": 22, "correct": False, "tokens": 0},
    {"idx": 23, "correct": True, "tokens": 6262},
    {"idx": 24, "correct": True, "tokens": 19001},
    {"idx": 25, "correct": True, "tokens": 4285},
    {"idx": 26, "correct": False, "tokens": 0},
    {"idx": 27, "correct": True, "tokens": 13022},
    {"idx": 28, "correct": True, "tokens": 1836},
    {"idx": 29, "correct": True, "tokens": 3160},
    {"idx": 30, "correct": False, "tokens": 0},
]

START_IDX = 18  # 0-based: resume from problem 19 (0-based idx 18)


async def main() -> None:
    # Load test problems in same order as original experiment (seed=42)
    test = load_aime_2025(seed=42)
    assert len(test) == 30, f"Expected 30 test problems, got {len(test)}"

    remaining = test[START_IDX:]
    logger.info(f"=== COMPLETING BASELINE: problems {START_IDX+1}-{len(test)} ({len(remaining)} problems) ===")

    log_path = RESULTS_DIR / "baseline_completion.log"
    fh = logging.FileHandler(log_path, mode="w")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(fh)

    new_results = []
    for i, prob in enumerate(remaining):
        global_idx = START_IDX + i + 1  # 1-based
        logger.info(f"[baseline {global_idx}/30] {prob.id}")
        t0 = time.monotonic()
        trace = await solve(prob.question, [])  # empty skills = baseline
        correct = check_answer(trace.final_answer, prob.answer)
        elapsed = time.monotonic() - t0
        new_results.append({
            "idx": global_idx,
            "correct": correct,
            "tokens": trace.total_tokens,
            "completion_tokens": trace.completion_tokens,
            "predicted": trace.final_answer,
            "expected": prob.answer,
            "elapsed_s": elapsed,
            "problem_id": prob.id,
        })
        logger.info(f"  [{global_idx}] correct={correct}  tokens={trace.total_tokens}  elapsed={elapsed:.1f}s")

    # Merge with existing results
    all_baseline = EXISTING_BASELINE + new_results
    
    # Save merged results
    out_path = RESULTS_DIR / "baseline_complete.json"
    with open(out_path, "w") as f:
        json.dump({
            "existing_results": EXISTING_BASELINE,
            "new_results": new_results,
            "all_baseline": all_baseline,
            "test_with_skills": TEST_WITH_SKILLS,
            "notes": "Problems 1-18 from SiliconFlow (GLM-4.7), 19-30 from BigModel (GLM-4.7)"
        }, f, indent=2, default=str)

    # Print summary
    total_correct = sum(1 for r in all_baseline if r["correct"])
    new_correct = sum(1 for r in new_results if r["correct"])
    logger.info(f"\n{'='*60}")
    logger.info(f"BASELINE COMPLETION RESULTS")
    logger.info(f"  New problems ({START_IDX+1}-30): {new_correct}/{len(new_results)}")
    logger.info(f"  Total baseline: {total_correct}/{len(all_baseline)}")
    logger.info(f"  Results saved: {out_path}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
