"""
run_math.py — MATH dataset train/test experiment.

Evolve skills on 100 training problems, then evaluate on 10 test problems.
Compares "with evolved skills" vs "baseline (no skills)" accuracy and token cost.

Usage:
    cd /home/lixujun/skill_evolving
    python -m academic.experiments.run_math
    python -m academic.experiments.run_math --n_train=50 --n_test=5 --subjects=algebra
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from academic.datasets.math_dataset import load_train_test
from academic.pipeline import evolve_and_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tool_evolving")


async def main() -> None:
    parser = argparse.ArgumentParser(description="MATH dataset skill evolution experiment")
    parser.add_argument("--n_train", type=int, default=100, help="Number of training problems")
    parser.add_argument("--n_test", type=int, default=10, help="Number of test problems")
    parser.add_argument("--subjects", type=str, default=None,
                        help="Comma-separated subject filter (e.g. algebra,number_theory)")
    parser.add_argument("--levels", type=str, default=None,
                        help="Comma-separated difficulty levels (1-5)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    subjects = args.subjects.split(",") if args.subjects else None
    levels = [int(x) for x in args.levels.split(",")] if args.levels else None

    print(f"\n{'='*60}")
    print(f"MATH Experiment: {args.n_train} train → {args.n_test} test")
    if subjects:
        print(f"Subjects: {subjects}")
    if levels:
        print(f"Levels: {levels}")
    print(f"{'='*60}\n")

    # Load data
    print("Loading MATH dataset...")
    train, test = load_train_test(
        n_train=args.n_train,
        n_test=args.n_test,
        subjects=subjects,
        levels=levels,
        seed=args.seed,
    )
    print(f"Loaded {len(train)} train + {len(test)} test problems")
    print(f"Sample train: {train[0].question[:80]}..." if train else "No train data")
    print(f"Sample test:  {test[0].question[:80]}..." if test else "No test data")

    # Run experiment
    summary = await evolve_and_test(
        train=train,
        test=test,
        experiment_name="math_experiment",
    )

    # Final report
    print(f"\n{'='*60}")
    print(f"FINAL RESULTS")
    print(f"{'='*60}")
    print(f"Evolve (train) accuracy : {summary['evolve']['accuracy']:.1%}")
    print(f"Test w/ skills accuracy : {summary['test_with_skills']['accuracy']:.1%}")
    print(f"Test baseline accuracy  : {summary['test_baseline']['accuracy']:.1%}")
    print(f"Improvement             : {summary['improvement']:+.1%}")
    print(f"Token saving (total)    : {summary['token_saving']:.0f}")
    print(f"Token saving (compl.)   : {summary['completion_token_saving']:.0f}")
    print(f"Skills evolved          : {summary['skills_evolved']}")
    print(f"{'='*60}")

    if summary["improvement"] > 0:
        print("\nPASS: Evolved skills improved test accuracy over baseline.")
    elif summary["improvement"] == 0:
        print("\nNEUTRAL: Same accuracy. Check token savings.")
    else:
        print("\nFAIL: Baseline was better. Check skill quality.")


if __name__ == "__main__":
    asyncio.run(main())
