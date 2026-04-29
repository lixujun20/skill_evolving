"""
cli.py — User-facing CLI for the skill refactoring lab.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

from academic.refactoring_lab.example_skills import get_corpus, list_corpora
from academic.refactoring_lab.test_runner import (
    compare_skill_sets_for_corpus,
    llm_as_judge,
    run_engine,
    run_standalone,
    save_report,
    _rebuild_specs,
)


def _load_engine_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


def _refactored_specs_from_engine(corpus_name: str, eng: Dict[str, Any]):
    corpus = get_corpus(corpus_name)
    subfn_by_skill: Dict[str, List[str]] = {}
    for sf in eng.get("shared_sub_functions", []):
        for sk_name in sf["source_skills"]:
            subfn_by_skill.setdefault(sk_name, []).append(sf["code"])
    return _rebuild_specs(corpus.skills, eng.get("refactored_skills", []), subfn_by_skill)


def main() -> None:
    parser = argparse.ArgumentParser(description="Skill Refactoring Lab CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-corpora", help="List available corpora")

    inspect_p = sub.add_parser("inspect-corpus", help="Print corpus summary")
    inspect_p.add_argument("--corpus", default="builtin_math")

    standalone_p = sub.add_parser("standalone", help="Run standalone skill tests")
    standalone_p.add_argument("--corpus", default="builtin_math")
    standalone_p.add_argument("--name", default=None)

    refactor_p = sub.add_parser("refactor", help="Run a refactoring engine")
    refactor_p.add_argument("--corpus", default="builtin_math")
    refactor_p.add_argument("--engine", choices=["naive", "desc_first"], default="naive")
    refactor_p.add_argument("--llm", default=None)
    refactor_p.add_argument("--name", default=None)

    compare_p = sub.add_parser("compare-sets", help="Run e2e collection-level comparison")
    compare_p.add_argument("--corpus", default="builtin_math")
    compare_p.add_argument("--engine-report", required=True)
    compare_p.add_argument("--llm", required=True)
    compare_p.add_argument("--runs", type=int, default=1)
    compare_p.add_argument("--name", default=None)

    judge_p = sub.add_parser("judge", help="Run LLM-as-judge on an engine report")
    judge_p.add_argument("--corpus", default="builtin_math")
    judge_p.add_argument("--engine-report", required=True)
    judge_p.add_argument("--llm", required=True)
    judge_p.add_argument("--name", default=None)

    run_p = sub.add_parser("run", help="One-shot pipeline: refactor + compare + optional judge")
    run_p.add_argument("--corpus", default="builtin_math")
    run_p.add_argument("--engine", choices=["naive", "desc_first"], default="desc_first")
    run_p.add_argument("--llm", default=None)
    run_p.add_argument("--compare", action="store_true")
    run_p.add_argument("--compare-runs", type=int, default=1)
    run_p.add_argument("--judge", action="store_true")
    run_p.add_argument("--name", default=None)

    args = parser.parse_args()

    if args.cmd == "list-corpora":
        for corpus in list_corpora():
            print(f"{corpus.name}: {corpus.description}")
        return

    corpus = get_corpus(getattr(args, "corpus", "builtin_math"))

    if args.cmd == "inspect-corpus":
        print(json.dumps({
            "name": corpus.name,
            "description": corpus.description,
            "source": corpus.source,
            "groups": [
                {
                    "name": g.name,
                    "domain": g.domain,
                    "shared_sub_task": g.shared_sub_task,
                    "skills": [s.name for s in g.skills],
                }
                for g in corpus.groups
            ],
            "notes": corpus.notes,
        }, ensure_ascii=False, indent=2))
        return

    if args.cmd == "standalone":
        data = {"standalone": run_standalone(corpus.skills)}
        save_report(args.name or f"standalone_{corpus.name}", data)
        return

    if args.cmd == "refactor":
        data = {"engine": run_engine(args.engine, args.llm, corpus)}
        save_report(args.name or f"engine_{args.engine}_{corpus.name}", data)
        return

    if args.cmd == "compare-sets":
        eng = _load_engine_json(args.engine_report)["engine"]
        refactored_specs = _refactored_specs_from_engine(corpus.name, eng)
        comparison = asyncio.run(compare_skill_sets_for_corpus(
            corpus,
            refactored_specs,
            llm_config=args.llm,
            n_runs=args.runs,
            original_total_tokens=eng.get("token_report", {}).get("total_before_tokens"),
            refactored_total_tokens=eng.get("token_report", {}).get("total_after_tokens"),
        ))
        save_report(args.name or f"compare_{corpus.name}", {"engine": {
            "engine": eng["engine"],
            "corpus": corpus.name,
            "llm_config": args.llm,
            "elapsed_s": eng.get("elapsed_s", 0),
            "shared_sub_functions": eng.get("shared_sub_functions", []),
            "rejected_merges": eng.get("rejected_merges", []),
            "correctness_pass_rate": eng.get("correctness_pass_rate", 0.0),
            "token_report": eng.get("token_report", {}),
            "cluster_eval": eng.get("cluster_eval", {}),
            "skill_set_comparison": comparison,
        }})
        return

    if args.cmd == "judge":
        full = _load_engine_json(args.engine_report)
        judge_out = asyncio.run(llm_as_judge(full["engine"], corpus, args.llm))
        full["judge"] = judge_out
        save_report(args.name or f"judge_{corpus.name}", full)
        return

    if args.cmd == "run":
        data = {"engine": run_engine(args.engine, args.llm, corpus)}
        if args.compare:
            refactored_specs = _refactored_specs_from_engine(corpus.name, data["engine"])
            data["engine"]["skill_set_comparison"] = asyncio.run(compare_skill_sets_for_corpus(
                corpus,
                refactored_specs,
                llm_config=args.llm or "bigmodel",
                n_runs=args.compare_runs,
                original_total_tokens=data["engine"].get("token_report", {}).get("total_before_tokens"),
                refactored_total_tokens=data["engine"].get("token_report", {}).get("total_after_tokens"),
            ))
        if args.judge:
            if not args.llm:
                raise ValueError("--judge requires --llm")
            data["judge"] = asyncio.run(llm_as_judge(data["engine"], corpus, args.llm))
        save_report(args.name or f"run_{args.engine}_{corpus.name}", data)
        return


if __name__ == "__main__":
    main()
