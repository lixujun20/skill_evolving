"""Preset BFCL experiment launcher for official-aligned baselines and evolve runs.

This keeps the command surface small for the current BFCL mainline:
- official-aligned `none` baseline
- conservative handwritten `prompt_only`
- evolve on top of the same split

Examples:
    python -m academic.benchmarks.bfcl_experiment_suite --suite glm47_baseline_50_150
    python -m academic.benchmarks.bfcl_experiment_suite --suite claude_baseline_50_150
    python -m academic.benchmarks.bfcl_experiment_suite --suite glm47_evolve_50_50
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from academic.config import PROJECT_ROOT, RESULTS_DIR


SUITES: Dict[str, Dict[str, Any]] = {
    "glm47_baseline_50_150": {
        "description": "GLM-4.7 official-aligned 50 train / 150 test no-skill baseline.",
        "args": [
            "--benchmark", "bfcl_v3",
            "--mode", "baseline",
            "--llm-config", "bigmodel",
            "--model-name", "glm-4.7",
            "--bfcl-data-source", "bfcl_eval_bundle",
            "--bfcl-adapter-mode", "official",
            "--bfcl-execution-backend", "official",
            "--bfcl-prompt-style", "native",
            "--bfcl-tool-api-style", "auto",
            "--skill-injection-mode", "none",
            "--n-train", "50",
            "--n-test", "150",
            "--max-steps-per-turn", "20",
            "--tag", "glm47_official_main_none_50_150",
            "--partial-output", str(RESULTS_DIR / "bfcl_v3_glm47_official_main_none_50_150_partial.json"),
        ],
    },
    "glm47_prompt_50_150": {
        "description": "GLM-4.7 official-aligned 50 train / 150 test conservative handwritten prompt-only.",
        "args": [
            "--benchmark", "bfcl_v3",
            "--mode", "baseline",
            "--llm-config", "bigmodel",
            "--model-name", "glm-4.7",
            "--bfcl-data-source", "bfcl_eval_bundle",
            "--bfcl-adapter-mode", "official",
            "--bfcl-execution-backend", "official",
            "--bfcl-prompt-style", "native",
            "--bfcl-tool-api-style", "auto",
            "--skill-injection-mode", "prompt_only",
            "--use-handwritten-skills",
            "--top-k-skills", "2",
            "--n-train", "50",
            "--n-test", "150",
            "--max-steps-per-turn", "20",
            "--tag", "glm47_official_main_prompt_50_150",
            "--partial-output", str(RESULTS_DIR / "bfcl_v3_glm47_official_main_prompt_50_150_partial.json"),
        ],
    },
    "claude_baseline_50_150": {
        "description": "Claude official-aligned 50 train / 150 test no-skill baseline.",
        "args": [
            "--benchmark", "bfcl_v3",
            "--mode", "baseline",
            "--llm-config", "default",
            "--model-name", "claude-sonnet-4-6",
            "--bfcl-data-source", "bfcl_eval_bundle",
            "--bfcl-adapter-mode", "official",
            "--bfcl-execution-backend", "official",
            "--bfcl-prompt-style", "native",
            "--bfcl-tool-api-style", "auto",
            "--skill-injection-mode", "none",
            "--n-train", "50",
            "--n-test", "150",
            "--max-steps-per-turn", "20",
            "--tag", "claude46_official_main_none_50_150",
            "--partial-output", str(RESULTS_DIR / "bfcl_v3_claude46_official_main_none_50_150_partial.json"),
        ],
    },
    "claude_prompt_50_150": {
        "description": "Claude official-aligned 50 train / 150 test conservative handwritten prompt-only.",
        "args": [
            "--benchmark", "bfcl_v3",
            "--mode", "baseline",
            "--llm-config", "default",
            "--model-name", "claude-sonnet-4-6",
            "--bfcl-data-source", "bfcl_eval_bundle",
            "--bfcl-adapter-mode", "official",
            "--bfcl-execution-backend", "official",
            "--bfcl-prompt-style", "native",
            "--bfcl-tool-api-style", "auto",
            "--skill-injection-mode", "prompt_only",
            "--use-handwritten-skills",
            "--top-k-skills", "2",
            "--n-train", "50",
            "--n-test", "150",
            "--max-steps-per-turn", "20",
            "--tag", "claude46_official_main_prompt_50_150",
            "--partial-output", str(RESULTS_DIR / "bfcl_v3_claude46_official_main_prompt_50_150_partial.json"),
        ],
    },
    "glm47_evolve_50_50": {
        "description": "GLM-4.7 official-aligned evolve monitoring run on 50 train / 50 test.",
        "args": [
            "--benchmark", "bfcl_v3",
            "--mode", "evolve",
            "--llm-config", "bigmodel",
            "--model-name", "glm-4.7",
            "--bfcl-data-source", "bfcl_eval_bundle",
            "--bfcl-adapter-mode", "official",
            "--bfcl-execution-backend", "official",
            "--bfcl-prompt-style", "native",
            "--bfcl-tool-api-style", "auto",
            "--skill-injection-mode", "prompt_only",
            "--use-handwritten-skills",
            "--top-k-skills", "2",
            "--n-train", "50",
            "--n-test", "50",
            "--max-steps-per-turn", "20",
            "--tag", "glm47_official_main_evolve_50_50",
            "--save-skills", str(RESULTS_DIR / "bfcl_glm47_official_main_evolve_50_50_skills.json"),
            "--partial-output", str(RESULTS_DIR / "bfcl_v3_glm47_official_main_evolve_50_50_partial.json"),
        ],
    },
}


def _result_path_for_args(args: List[str]) -> str | None:
    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 < len(args):
            return args[idx + 1]
    if "--tag" not in args:
        return None
    tag = args[args.index("--tag") + 1]
    if "--benchmark" not in args or "--mode" not in args:
        return None
    benchmark = args[args.index("--benchmark") + 1]
    mode = args[args.index("--mode") + 1]
    return str(RESULTS_DIR / f"{benchmark}_{tag}_{mode}.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run named BFCL experiment suites")
    parser.add_argument("--list", action="store_true", help="List available suites")
    parser.add_argument("--suite", choices=sorted(SUITES), help="Named experiment suite to run")
    parser.add_argument("--dry-run", action="store_true", help="Print the command without executing")
    args = parser.parse_args()

    if args.list or not args.suite:
        payload = {
            name: {
                "description": spec["description"],
                "result_path": _result_path_for_args(spec["args"]),
            }
            for name, spec in sorted(SUITES.items())
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    spec = SUITES[args.suite]
    cmd = [sys.executable, "-m", "academic.benchmarks.run", *spec["args"]]
    print(json.dumps({"suite": args.suite, "cmd": cmd}, ensure_ascii=False, indent=2))
    if args.dry_run:
        return
    subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))


if __name__ == "__main__":
    main()
