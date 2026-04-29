from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Dict, List

from academic.executor import solve
from academic.skill_store import SkillStore


def _merge_counts(dst: Dict[str, int], src: Dict[str, int]) -> None:
    for name, count in src.items():
        dst[name] = dst.get(name, 0) + int(count)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run repeated same-question skill call diagnostics")
    parser.add_argument("--store_path", type=Path, required=True)
    parser.add_argument("--detail_path", type=Path, required=True)
    parser.add_argument("--agent_model", type=str, default="glm45air")
    parser.add_argument("--solver_mode", choices=["tir", "oneshot"], default="tir")
    parser.add_argument("--top_k", type=int, default=5)
    parser.add_argument("--n_cases", type=int, default=5)
    parser.add_argument("--n_runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    store = SkillStore.load(args.store_path)
    detail = json.loads(args.detail_path.read_text())
    candidates = [e for e in detail.get("evolve_details", []) if e.get("new_skills")]
    selected = candidates[: args.n_cases]

    results: List[dict] = []
    total_retrieved_counts: Dict[str, int] = {}
    total_called_counts: Dict[str, int] = {}

    for case_idx, entry in enumerate(selected):
        question = entry["question"]
        expected_skills = entry.get("new_skills", [])
        retrieved = store._retrieve_tfidf(question, args.top_k)
        retrieved_names = [sk.name for sk in retrieved]
        case_result = {
            "case_idx": case_idx,
            "problem_id": entry.get("problem_id"),
            "question": question,
            "expected_skills": expected_skills,
            "retrieved_names": retrieved_names,
            "runs": [],
        }
        for name in retrieved_names:
            total_retrieved_counts[name] = total_retrieved_counts.get(name, 0) + args.n_runs

        for run_idx in range(args.n_runs):
            trace = await solve(
                question,
                retrieved,
                llm_config=args.agent_model,
                store=store,
                solver_mode=args.solver_mode,
            )
            run_entry = {
                "run_idx": run_idx,
                "final_answer": trace.final_answer,
                "success": trace.success,
                "skill_runtime_call_counts": dict(trace.skill_runtime_call_counts),
                "skills_called": sorted(
                    [name for name, count in trace.skill_runtime_call_counts.items() if count > 0]
                ),
            }
            _merge_counts(total_called_counts, trace.skill_runtime_call_counts)
            case_result["runs"].append(run_entry)
            print(json.dumps({
                "problem_id": entry.get("problem_id"),
                "run_idx": run_idx,
                "expected_skills": expected_skills,
                "retrieved": retrieved_names,
                "called": run_entry["skills_called"],
                "runtime_call_counts": run_entry["skill_runtime_call_counts"],
            }, ensure_ascii=False), flush=True)
        results.append(case_result)

    summary = {
        "agent_model": args.agent_model,
        "solver_mode": args.solver_mode,
        "top_k": args.top_k,
        "n_cases": len(results),
        "n_runs": args.n_runs,
        "retrieved_counts": dict(sorted(total_retrieved_counts.items())),
        "called_counts": dict(sorted(total_called_counts.items())),
        "results": results,
    }

    if args.out:
        args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(json.dumps({
        "retrieved_counts": summary["retrieved_counts"],
        "called_counts": summary["called_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
