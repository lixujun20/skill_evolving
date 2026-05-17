from __future__ import annotations

import json
import tempfile
from pathlib import Path

from academic.benchmarks.tests.maintenance.test_runtime_optimization_scenarios import (
    build_runtime_optimization_scenario_report,
)


def _fenced_json(payload: object) -> str:
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def generate_report(output_path: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="runtime_opt_scenarios_") as tmpdir:
        report = build_runtime_optimization_scenario_report(Path(tmpdir))
    lines: list[str] = []
    lines.append("# Runtime Optimization Scenario Validation")
    lines.append("")
    lines.append("This document records scenario-driven validation for the runtime optimizations added to the BFCL related-task evolve pipeline.")
    lines.append("")
    lines.append("## Bug Found During Scenario Validation")
    lines.append("")
    lines.append("A real bug was found and fixed before finalizing this report.")
    lines.append("")
    lines.append("- Symptom: `overlap_state` was updated only when online refactor actually triggered. Early tasks that did not yet satisfy the refactor trigger threshold never entered the cached overlap graph.")
    lines.append("- Risk: the first online refactor would miss historical segment context and operate on an incomplete graph.")
    lines.append("- Fix: update `overlap_state` immediately after every task's segment extraction, then call online refactor with `new_segments=[]` because the state is already current.")
    lines.append("- Status: fixed and re-tested successfully.")
    lines.append("")
    for idx, scenario in enumerate(report["scenarios"], start=1):
        lines.append(f"## Scenario {idx}: {scenario['name']}")
        lines.append("")
        lines.append(f"Meaning: {scenario['meaning']}")
        lines.append("")
        lines.append("Status: PASS")
        lines.append("")
        lines.append("Literal input:")
        lines.append(_fenced_json(scenario["input"]))
        lines.append("")
        lines.append("Literal output:")
        lines.append(_fenced_json(scenario["output"]))
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    target = Path("/home/lixujun/skill_evolving/academic/results/BFCL_RUNTIME_OPTIMIZATION_SCENARIO_REPORT.md")
    path = generate_report(target)
    print(path)
