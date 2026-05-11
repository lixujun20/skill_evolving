"""Small reporting runner for the method validation catalog.

This runner does not execute real-LLM experiments. It reports which cases have
offline coverage and which still require GLM/Claude runs for a full pass.
Use pytest for executable offline assertions:

    python -m pytest academic/method_validation/tests -q
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List

from academic.method_validation.catalog import CASES, MethodValidationCase


def build_report(cases: Iterable[MethodValidationCase] = CASES) -> Dict[str, Any]:
    items = list(cases)
    by_status = Counter(case.status for case in items)
    by_suite = Counter(case.suite for case in items)
    by_role = Counter(case.primary_role for case in items)
    by_paper_point: Dict[str, List[str]] = defaultdict(list)
    for case in items:
        by_paper_point[case.paper_point_id].append(case.case_id)
    return {
        "n_cases": len(items),
        "by_status": dict(sorted(by_status.items())),
        "by_suite": dict(sorted(by_suite.items())),
        "by_role": dict(sorted(by_role.items())),
        "requires_llm_full_pass": sorted(case.case_id for case in items if case.requires_llm),
        "offline_implemented": sorted(case.case_id for case in items if case.status == "implemented_offline"),
        "planned_infrastructure": sorted(case.case_id for case in items if case.status == "planned"),
        "paper_point_coverage": {key: sorted(value) for key, value in sorted(by_paper_point.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Report method validation catalog coverage.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a compact text report.")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(f"cases: {report['n_cases']}")
    print(f"by_status: {report['by_status']}")
    print(f"by_suite: {report['by_suite']}")
    print(f"requires_llm_full_pass: {len(report['requires_llm_full_pass'])}")
    print(f"offline_implemented: {len(report['offline_implemented'])}")
    print(f"planned_infrastructure: {len(report['planned_infrastructure'])}")


if __name__ == "__main__":
    main()

