#!/usr/bin/env python3
"""Generate AMU fixture files for integration tests.

Usage:
    cd ~/skill_evolving
    PYTHONPATH=/home/lixujun/skill_evolving /data/lixujun/miniconda3/envs/meta-agent/bin/python3 scripts/generate_amu_fixtures.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.meta_agent.skills.tests.amu.generator import AICosmosMockUser


async def main():
    amu = AICosmosMockUser()

    print("Generating single-point fixtures (10 scenarios)...")
    sp = await amu.generate_single_point_fixtures(n=10)
    print(f"  Generated {len(sp)} scenarios → saved to tests/fixtures/amu_single_point.json")

    print("Generating long-term fixtures (12-query sequence)...")
    lt = await amu.generate_long_term_fixtures(n=12)
    seq = lt.get("sequence", lt) if isinstance(lt, dict) else lt
    print(f"  Generated sequence of {len(seq)} queries → saved to tests/fixtures/amu_long_term.json")

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
