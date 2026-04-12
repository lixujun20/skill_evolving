"""
run_aime_multiepoch.py — Multi-epoch skill evolution on AIME 2024→2025.

Runs multiple shuffled passes over the AIME 2024 training set to evolve skills,
then tests on AIME 2025. Baseline is skipped (reuse existing results).

Usage:
    cd ~/skill_evolving
    python -m academic.experiments.run_aime_multiepoch --epochs 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

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
    parser = argparse.ArgumentParser(description="Multi-epoch AIME skill evolution")
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs")
    parser.add_argument("--n_train", type=int, default=None)
    parser.add_argument("--n_test", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--agent_model", type=str, default="bigmodel")
    parser.add_argument("--extract_model", type=str, default="bigmodel")
    parser.add_argument("--tag", type=str, default="")
    args = parser.parse_args()

    exp_name = f"aime_multiepoch_{args.epochs}ep"
    if args.tag:
        exp_name += f"_{args.tag}"

    print(f"\n{'='*60}")
    print(f"Multi-Epoch AIME Experiment")
    print(f"  Epochs: {args.epochs}")
    print(f"  Agent model: {args.agent_model}")
    print(f"  Extract model: {args.extract_model}")
    print(f"  Experiment: {exp_name}")
    print(f"{'='*60}\n")

    train, test = load_train_test(n_train=args.n_train, n_test=args.n_test, seed=args.seed)
    print(f"Loaded {len(train)} train + {len(test)} test problems")

    t0 = time.monotonic()
    summary = await evolve_and_test(
        train=train,
        test=test,
        experiment_name=exp_name,
        n_epochs=args.epochs,
        skip_baseline=True,
        agent_model=args.agent_model,
        extract_model=args.extract_model,
    )
    elapsed = time.monotonic() - t0

    print(f"\n{'='*60}")
    print(f"RESULTS — {exp_name} ({elapsed:.0f}s)")
    print(f"{'='*60}")
    print(f"Evolve accuracy (over {args.epochs} epochs) : {summary['evolve']['accuracy']:.1%}")
    print(f"Test w/ skills accuracy                     : {summary['test_with_skills']['accuracy']:.1%}")
    print(f"Skills evolved                              : {summary['skills_evolved']}")
    print(f"Avg tokens (evolve)                         : {summary['evolve']['avg_tokens']:.0f}")
    print(f"Avg tokens (test w/ skills)                 : {summary['test_with_skills']['avg_tokens']:.0f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
