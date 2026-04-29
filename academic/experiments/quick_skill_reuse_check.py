from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Dict, List

from academic.datasets.math_dataset import _TRAIN_PARQUET_PATH, load_from_parquet
from academic.executor import solve
from academic.skill_store import SkillStore


def _merge_counts(dst: Dict[str, int], src: Dict[str, int]) -> None:
    for name, count in src.items():
        dst[name] = dst.get(name, 0) + int(count)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Quick runtime skill reuse check")
    parser.add_argument("--store_path", type=Path, required=True)
    parser.add_argument("--agent_model", type=str, default="glm45air")
    parser.add_argument("--solver_mode", choices=["oneshot", "tir"], default="oneshot")
    parser.add_argument("--n_problems", type=int, default=5)
    parser.add_argument("--n_runs", type=int, default=3)
    parser.add_argument("--top_k", type=int, default=5)
    parser.add_argument("--retrieval_mode", choices=["tfidf", "embedding"], default="tfidf")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    store = SkillStore.load(args.store_path)
    problems = load_from_parquet(n=args.n_problems, seed=args.seed, parquet_path=_TRAIN_PARQUET_PATH)

    summary: Dict[str, object] = {
        "store_path": str(args.store_path),
        "agent_model": args.agent_model,
        "solver_mode": args.solver_mode,
        "n_problems": args.n_problems,
        "n_runs": args.n_runs,
        "top_k": args.top_k,
        "results": [],
        "retrieved_counts": {},
        "called_counts": {},
        "tool_call_counts": {},
    }

    retrieved_counts: Dict[str, int] = {}
    called_counts: Dict[str, int] = {}
    tool_call_counts: Dict[str, int] = {}

    for prob_idx, prob in enumerate(problems):
        # For quick diagnostics we default to TF-IDF retrieval to avoid paying
        # the one-time embedding cost for the entire store.
        if args.retrieval_mode == "embedding":
            relevant = await store.retrieve(prob.question, top_k=args.top_k)
        else:
            relevant = store.retrieve_sync(prob.question, top_k=args.top_k)
        problem_entry = {
            "problem_idx": prob_idx,
            "problem_id": prob.id,
            "question": prob.question,
            "retrieved_skills": [sk.name for sk in relevant],
            "runs": [],
        }
        for skill_name in problem_entry["retrieved_skills"]:
            retrieved_counts[skill_name] = retrieved_counts.get(skill_name, 0) + args.n_runs

        for run_idx in range(args.n_runs):
            trace = await solve(
                prob.question,
                relevant,
                llm_config=args.agent_model,
                store=store,
                solver_mode=args.solver_mode,
            )
            run_entry = {
                "run_idx": run_idx,
                "final_answer": trace.final_answer,
                "success": trace.success,
                "skills_retrieved": [sk.name for sk in relevant],
                "skill_runtime_call_counts": dict(trace.skill_runtime_call_counts),
                "skill_tool_counts": dict(trace.skill_tool_counts),
                "skills_called": sorted(
                    [name for name, count in trace.skill_runtime_call_counts.items() if count > 0]
                ),
            }
            _merge_counts(called_counts, trace.skill_runtime_call_counts)
            _merge_counts(tool_call_counts, trace.skill_tool_counts)
            problem_entry["runs"].append(run_entry)
            print(
                json.dumps(
                    {
                        "problem_id": prob.id,
                        "run_idx": run_idx,
                        "retrieved": run_entry["skills_retrieved"],
                        "called": run_entry["skills_called"],
                        "runtime_call_counts": run_entry["skill_runtime_call_counts"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        summary["results"].append(problem_entry)

    summary["retrieved_counts"] = dict(sorted(retrieved_counts.items()))
    summary["called_counts"] = dict(sorted(called_counts.items()))
    summary["tool_call_counts"] = dict(sorted(tool_call_counts.items()))

    if args.out:
        args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(json.dumps({
        "retrieved_counts": summary["retrieved_counts"],
        "called_counts": summary["called_counts"],
        "tool_call_counts": summary["tool_call_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
