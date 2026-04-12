"""
run_aime_weak.py — AIME experiment with a weaker executor model (GLM-4.5-Air).

Same setup as run_aime but uses GLM-4.5-Air for the executor/agent,
while keeping GLM-4.7 for the extractor. Tests whether skill evolving
helps weaker models more.

Usage:
    cd ~/skill_evolving
    python -m academic.experiments.run_aime_weak
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
    parser = argparse.ArgumentParser(description="AIME with weak executor model")
    parser.add_argument("--n_train", type=int, default=None)
    parser.add_argument("--n_test", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--agent_model", type=str, default="glm45air",
                        help="Weak model for executor (default: glm45air)")
    parser.add_argument("--extract_model", type=str, default="bigmodel",
                        help="Strong model for extractor (default: bigmodel = GLM-4.7)")
    parser.add_argument("--tag", type=str, default="")
    args = parser.parse_args()

    exp_name = "aime_weak_glm45air"
    if args.tag:
        exp_name += f"_{args.tag}"

    print(f"\n{'='*60}")
    print(f"Weak Model AIME Experiment")
    print(f"  Agent (executor): {args.agent_model} (GLM-4.5-Air)")
    print(f"  Extractor:        {args.extract_model} (GLM-4.7)")
    print(f"  Experiment:       {exp_name}")
    print(f"{'='*60}\n")

    train, test = load_train_test(n_train=args.n_train, n_test=args.n_test, seed=args.seed)
    print(f"Loaded {len(train)} train + {len(test)} test problems")

    t0 = time.monotonic()
    summary = await evolve_and_test(
        train=train,
        test=test,
        experiment_name=exp_name,
        n_epochs=1,
        skip_baseline=False,  # Need baseline for weak model
        agent_model=args.agent_model,
        extract_model=args.extract_model,
    )
    elapsed = time.monotonic() - t0

    print(f"\n{'='*60}")
    print(f"RESULTS — {exp_name} ({elapsed:.0f}s)")
    print(f"{'='*60}")
    print(f"Evolve accuracy     : {summary['evolve']['accuracy']:.1%}")
    print(f"Test w/ skills      : {summary['test_with_skills']['accuracy']:.1%}")
    print(f"Test baseline       : {summary['test_baseline']['accuracy']:.1%}")
    print(f"Improvement         : {summary['improvement']:+.1%}")
    print(f"Token saving        : {summary['token_saving']:.0f}")
    print(f"Skills evolved      : {summary['skills_evolved']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
