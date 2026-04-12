"""
run_aime.py — AIME 2024→2025 skill evolution experiment.

Train on AIME 2024 (30 problems), test on AIME 2025 (30 problems).
Compares skill-augmented solving vs baseline (no skills).

Usage:
    cd ~/skill_evolving
    python -m academic.experiments.run_aime
    python -m academic.experiments.run_aime --n_train=30 --n_test=30
    python -m academic.experiments.run_aime --n_train=15 --n_test=15 --tag=quick
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from academic.config import RESULTS_DIR
from academic.datasets.aime_dataset import load_train_test
from academic.pipeline import evolve_and_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tool_evolving")


async def main() -> None:
    parser = argparse.ArgumentParser(description="AIME 2024→2025 skill evolution experiment")
    parser.add_argument("--n_train", type=int, default=None, help="Number of AIME 2024 problems for training (default: all 30)")
    parser.add_argument("--n_test", type=int, default=None, help="Number of AIME 2025 problems for testing (default: all 30)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--tag", type=str, default="", help="Experiment tag suffix (e.g. 'run1')")
    args = parser.parse_args()

    exp_name = "aime_experiment"
    if args.tag:
        exp_name += f"_{args.tag}"

    n_train_str = args.n_train or "all(30)"
    n_test_str = args.n_test or "all(30)"
    print(f"\n{'='*60}")
    print(f"AIME Experiment: {n_train_str} train (2024) → {n_test_str} test (2025)")
    print(f"Experiment name: {exp_name}")
    print(f"{'='*60}\n")

    # Load data
    print("Loading AIME datasets...")
    train, test = load_train_test(
        n_train=args.n_train,
        n_test=args.n_test,
        seed=args.seed,
    )
    print(f"Loaded {len(train)} train (AIME 2024) + {len(test)} test (AIME 2025)")
    if train:
        print(f"  Train sample: [{train[0].id}] {train[0].question[:80]}...")
        print(f"  Train answer: {train[0].answer}")
    if test:
        print(f"  Test sample:  [{test[0].id}] {test[0].question[:80]}...")
        print(f"  Test answer:  {test[0].answer}")

    t0 = time.monotonic()

    # Run experiment
    summary = await evolve_and_test(
        train=train,
        test=test,
        experiment_name=exp_name,
    )

    elapsed = time.monotonic() - t0

    # Final report
    print(f"\n{'='*60}")
    print(f"FINAL RESULTS — {exp_name}")
    print(f"{'='*60}")
    print(f"Total time              : {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"Train (evolve) accuracy : {summary['evolve']['accuracy']:.1%}")
    print(f"Test w/ skills accuracy : {summary['test_with_skills']['accuracy']:.1%}")
    print(f"Test baseline accuracy  : {summary['test_baseline']['accuracy']:.1%}")
    print(f"Accuracy improvement    : {summary['improvement']:+.1%}")
    print(f"Token saving (total)    : {summary['token_saving']:.0f}")
    print(f"Token saving (compl.)   : {summary['completion_token_saving']:.0f}")
    print(f"Skills evolved          : {summary['skills_evolved']}")
    print(f"{'='*60}")

    # Verdict
    if summary["improvement"] > 0:
        print("\n✅ PASS: Evolved skills IMPROVED test accuracy over baseline.")
    elif summary["improvement"] == 0:
        if summary["token_saving"] > 0:
            print("\n✅ PASS: Same accuracy, but SAVED tokens.")
        else:
            print("\n⚠️  NEUTRAL: Same accuracy, no token saving.")
    else:
        print("\n❌ FAIL: Baseline was better.")


if __name__ == "__main__":
    asyncio.run(main())
