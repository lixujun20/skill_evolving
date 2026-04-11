#!/usr/bin/env python3
"""Generate skillsbench_fixture.json from the SkillsBench repository.

Usage:
    python3 scripts/generate_skillsbench_fixture.py [--skillsbench-dir ~/skillsbench] [--output ...]

The output JSON is loaded by test_retrieval_skillsbench.py.
"""

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

SELECTED_TASK_IDS = [
    "citation-check",
    "data-to-d3",
    "edit-pdf",
    "3d-scan-calc",
    "adaptive-cruise-control",
    "azure-bgp-oscillation-route-leak",
    "civ6-adjacency-optimizer",
    "earthquake-phase-association",
    "energy-ac-optimal-power-flow",
    "enterprise-information-search",
    "exceltable-in-ppt",
    "exoplanet-detection-period",
    "court-form-filling",
    "crystallographic-wyckoff-position-analysis",
    "dapt-intrusion-detection",
    "dialogue-parser",
    "dynamic-object-aware-egomotion",
    "earthquake-plate-calculation",
    "econ-detrending-correlation",
    # Extra tasks to enable same-category clustering tests
    "seismic-phase-picking",         # seismology (same as earthquake-phase-association)
    "gravitational-wave-detection",  # astronomy (same as exoplanet-detection-period)
    "energy-market-pricing",         # energy (same as energy-ac-optimal-power-flow)
    "setup-fuzzing-py",              # security (same as dapt-intrusion-detection)
    "r2r-mpc-control",               # control-systems (same as adaptive-cruise-control)
]


def to_snake(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def generate_fixture(skillsbench_dir: Path, task_ids: list[str]) -> list[dict]:
    tasks_dir = skillsbench_dir / "tasks"
    fixture = []

    for task_id in task_ids:
        toml_path = tasks_dir / task_id / "task.toml"
        instr_path = tasks_dir / task_id / "instruction.md"
        if not toml_path.exists() or not instr_path.exists():
            print(f"[skip] {task_id}: missing task.toml or instruction.md", file=sys.stderr)
            continue

        with open(toml_path, "rb") as f:
            meta = tomllib.load(f)
        with open(instr_path) as f:
            instruction = f.read().strip()

        md = meta.get("metadata", {})
        category = md.get("category", "general")
        tags = md.get("tags", [])
        difficulty = md.get("difficulty", "medium")

        first_line = instruction.split("\n")[0][:120]
        fn_name = to_snake(task_id)
        tag_str = ", ".join(tags[:5])

        skill_docstring = (
            f"{first_line}\n\nCategory: {category}. Tags: {tag_str}."
        )
        skill_code = (
            f"def {fn_name}(input_data: dict) -> dict:\n"
            f'    """Solve the {task_id} task.\n\n'
            f"    {skill_docstring[:200]}\n"
            f'    """\n'
            f"    raise NotImplementedError\n"
        )

        fixture.append(
            {
                "task_id": task_id,
                "category": category,
                "tags": tags,
                "difficulty": difficulty,
                "instruction": instruction[:600],
                "skill_docstring": skill_docstring[:300],
                "skill_code": skill_code,
            }
        )

    return fixture


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SkillsBench retrieval fixture")
    parser.add_argument(
        "--skillsbench-dir",
        default=str(Path.home() / "skillsbench"),
        help="Path to the cloned SkillsBench repository (default: ~/skillsbench)",
    )
    parser.add_argument(
        "--output",
        default=str(
            Path(__file__).parent.parent
            / "app/meta_agent/skills/tests/skillsbench_fixture.json"
        ),
        help="Output JSON file path",
    )
    args = parser.parse_args()

    skillsbench_dir = Path(args.skillsbench_dir)
    if not skillsbench_dir.exists():
        print(f"ERROR: SkillsBench dir not found: {skillsbench_dir}", file=sys.stderr)
        print("Clone it with: git clone https://github.com/benchflow-ai/skillsbench.git ~/skillsbench", file=sys.stderr)
        sys.exit(1)

    fixture = generate_fixture(skillsbench_dir, SELECTED_TASK_IDS)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(fixture, f, indent=2)

    print(f"Generated {len(fixture)} tasks -> {out_path}")
    for t in fixture:
        print(f"  {t['task_id']} [{t['category']}] tags={t['tags'][:3]}")


if __name__ == "__main__":
    main()
