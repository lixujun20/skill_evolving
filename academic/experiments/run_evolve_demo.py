"""
run_evolve_demo.py — 10-round single-query skill evolution demo.

Demonstrates that solving the same problem repeatedly leads to:
  1. Extracted skills that get reused / evolved
  2. Token cost decreasing as skills accumulate
  3. Accuracy stabilizing (or improving)

Usage:
    cd /home/lixujun/skill_evolving
    python -m academic.experiments.run_evolve_demo
"""
from __future__ import annotations

import asyncio
import logging
import sys

from academic.pipeline import evolve_single

# ── Setup logger ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tool_evolving")

# ── Demo problem ──────────────────────────────────────────────────────────────
# A number theory problem requiring multiple helper functions.
# The model needs to implement Euler's totient function, which requires
# prime factorization. As skills evolve, helper functions become available.
DEMO_QUERY = (
    "Calculate the value of Euler's totient function phi(360). "
    "The totient function phi(n) counts the number of integers from 1 to n "
    "that are coprime to n. Output just the integer."
)
DEMO_ANSWER = "96"

# Alternative: harder number theory problem
ALT_QUERY = (
    "Find the sum of all positive integers less than 100 that are coprime to 30. "
    "Two numbers are coprime if their greatest common divisor is 1."
)
ALT_ANSWER = "1280"


async def main() -> None:
    query = DEMO_QUERY
    expected = DEMO_ANSWER

    # Check for --alt flag
    if "--alt" in sys.argv:
        query = ALT_QUERY
        expected = ALT_ANSWER

    n_rounds = 10
    for arg in sys.argv[1:]:
        if arg.startswith("--rounds="):
            n_rounds = int(arg.split("=")[1])

    print(f"\n{'='*60}")
    print(f"Tool-Evolving Demo: {n_rounds} rounds")
    print(f"Query: {query[:80]}...")
    print(f"Expected: {expected}")
    print(f"{'='*60}\n")

    result = await evolve_single(
        query=query,
        expected_answer=expected,
        n_rounds=n_rounds,
        experiment_name="evolve_demo",
    )

    # Print round-by-round metrics
    print(f"\n{'='*60}")
    print(f"{'Round':>6} {'Correct':>8} {'Tokens':>8} {'Skills':>7} {'New':>5} {'Time':>8}")
    print(f"{'-'*6:>6} {'-'*8:>8} {'-'*8:>8} {'-'*7:>7} {'-'*5:>5} {'-'*8:>8}")
    for r in result.rounds:
        print(
            f"{r.round_idx+1:>6} "
            f"{'Yes' if r.answer_correct else 'No':>8} "
            f"{r.tokens:>8} "
            f"{r.skills_total:>7} "
            f"{r.new_skills_extracted:>5} "
            f"{r.elapsed_s:>7.1f}s"
        )

    print(f"\n{result.summary()}")

    # Save
    path = result.save()
    print(f"\nResults saved to: {path}")

    # Quick pass/fail check
    if len(result.rounds) >= 2:
        first_tokens = result.rounds[0].tokens
        last_tokens = result.rounds[-1].tokens
        any_correct = any(r.answer_correct for r in result.rounds)
        token_decreased = last_tokens < first_tokens

        print(f"\n--- Evaluation ---")
        print(f"First-round tokens: {first_tokens}")
        print(f"Last-round tokens:  {last_tokens}")
        print(f"Token decreased:    {token_decreased}")
        print(f"Any round correct:  {any_correct}")
        print(f"Overall accuracy:   {result.accuracy:.1%}")

        if any_correct and token_decreased:
            print("PASS: Skills evolved, token cost decreased.")
        elif any_correct:
            print("PARTIAL: Answers correct but tokens did not decrease.")
        else:
            print("FAIL: No correct answers produced.")


if __name__ == "__main__":
    asyncio.run(main())
