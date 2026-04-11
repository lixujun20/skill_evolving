#!/usr/bin/env python3
"""CLI script to regenerate AMU test fixtures using LLM generation."""
from __future__ import annotations

import asyncio
import argparse
import json
import sys
from pathlib import Path

# Allow running this script directly from its directory
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

from app.meta_agent.skills.tests.generators.amu_generator import AMUGenerator, FIXTURES_DIR


async def main() -> None:
    parser = argparse.ArgumentParser(description="Build AMU test fixtures via LLM generation")
    parser.add_argument(
        "--type",
        choices=["single_point", "long_term", "all"],
        default="all",
        help="Which fixture(s) to regenerate (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing fixture files",
    )
    parser.add_argument(
        "--n-skills",
        type=int,
        default=3,
        help="Number of single-point entries to generate (default: 3)",
    )
    parser.add_argument(
        "--n-groups",
        type=int,
        default=2,
        help="Number of skill groups for long-term fixture (default: 2)",
    )
    parser.add_argument(
        "--queries-per-group",
        type=int,
        default=3,
        help="Number of queries per group in long-term fixture (default: 3)",
    )
    args = parser.parse_args()

    gen = AMUGenerator()
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    if args.type in ("single_point", "all"):
        path = FIXTURES_DIR / "single_point.json"
        if path.exists() and not args.force:
            print(f"[skip] {path} already exists (use --force to overwrite)")
        else:
            print(f"[generate] single_point.json  n_skills={args.n_skills} ...")
            data = await gen.generate_single_point(n_skills=args.n_skills)
            with open(path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"[ok] wrote {len(data)} entries → {path}")

    if args.type in ("long_term", "all"):
        path = FIXTURES_DIR / "long_term.json"
        if path.exists() and not args.force:
            print(f"[skip] {path} already exists (use --force to overwrite)")
        else:
            print(
                f"[generate] long_term.json  n_groups={args.n_groups} "
                f"queries_per_group={args.queries_per_group} ..."
            )
            data = await gen.generate_long_term(
                n_groups=args.n_groups,
                queries_per_group=args.queries_per_group,
            )
            with open(path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            seq_len = len(data.get("sequence", []))
            print(f"[ok] wrote {len(data['groups'])} groups, {seq_len} sequence steps → {path}")


if __name__ == "__main__":
    asyncio.run(main())
