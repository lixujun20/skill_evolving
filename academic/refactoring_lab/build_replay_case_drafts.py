from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


def _load_json(path: Path):
    return json.loads(path.read_text())


def _normalize_problem_rows(data: dict) -> List[dict]:
    if "test_details" in data:
        return list(data["test_details"])
    if "problems" in data:
        return list(data["problems"])
    raise ValueError(f"Unrecognized detail format: keys={list(data.keys())}")


def _index_rows(rows: List[dict]) -> Dict[str, dict]:
    return {row["problem_id"]: row for row in rows}


def _index_skills(skills: List[dict]) -> Dict[str, dict]:
    return {skill["name"]: skill for skill in skills}


def build_replay_case_drafts(
    *,
    candidates_path: Path,
    evolve_detail_path: Path,
    baseline_detail_path: Path,
    skills_path: Path,
) -> Dict[str, object]:
    """Build lightly annotated replay cases from mined regressions.

    The output intentionally uses soft `references` instead of rigid
    `expectations`. These drafts are only seeds for later annotation:
    they preserve the query, retrieved skills, and compact history/workflow
    evidence, but they do not enforce a single exact planning target.
    """
    candidates_obj = _load_json(candidates_path)
    evolve_obj = _load_json(evolve_detail_path)
    baseline_obj = _load_json(baseline_detail_path)
    skills_obj = _load_json(skills_path)

    evolve_rows = _index_rows(_normalize_problem_rows(evolve_obj))
    baseline_rows = _index_rows(_normalize_problem_rows(baseline_obj))
    skill_index = _index_skills(skills_obj)

    drafts = []
    for candidate in candidates_obj.get("candidates", []):
        problem_id = candidate["problem_id"]
        evolve_row = evolve_rows[problem_id]
        baseline_row = baseline_rows[problem_id]

        evolve_runs = evolve_row.get("runs", [])
        representative_run = None
        for run in evolve_runs:
            if not run.get("correct", False):
                representative_run = run
                break
        if representative_run is None and evolve_runs:
            representative_run = evolve_runs[0]

        retrieved_skill_names = candidate.get("evolve_skills_seen", [])
        retrieved_skills = []
        for name in retrieved_skill_names:
            skill = skill_index.get(name)
            if not skill:
                continue
            retrieved_skills.append(
                {
                    "name": skill["name"],
                    "description": skill.get("description", ""),
                    "code": skill["code"],
                }
            )

        history_trace = []
        if representative_run:
            # Keep only a few short snippets. In later annotation, these can be
            # converted into workflow summaries or optional reusable fragments.
            for step in representative_run.get("steps", [])[:3]:
                content = step.get("content", "").strip()
                if content:
                    history_trace.append(content[:1200])

        workflow_summary = None
        if representative_run:
            summary_parts = []
            for key in ("final_answer", "answer", "summary"):
                value = representative_run.get(key)
                if isinstance(value, str) and value.strip():
                    summary_parts.append(value.strip())
            if not summary_parts:
                for step in representative_run.get("steps", [])[-2:]:
                    content = step.get("content", "").strip()
                    if content:
                        summary_parts.append(content[:300])
            if summary_parts:
                workflow_summary = "\n".join(summary_parts)[:800]

        drafts.append(
            {
                "case_id": f"draft::{candidate['problem_idx']}::{problem_id}",
                "source_experiment": "ds100_aime_v1_exp1",
                "problem_id": problem_id,
                "query": evolve_row["question"],
                "retrieved_skills": retrieved_skills,
                "history_context": {
                    "previous_query": baseline_row["question"],
                    "previous_workflow_plan": None,
                    "workflow_summary": workflow_summary,
                    "historical_agent_summary": workflow_summary,
                    "workflow_fragments": [],
                    "trace_snippets": history_trace,
                    "proposed_skills": [],
                },
                # These are soft hints for later human annotation or LLM judging.
                # They should not over-constrain the benchmark because multiple
                # valid reuse/adaptation strategies may exist.
                "references": {
                    "preferred_actions": [],
                    "useful_plan_calls": [],
                    "relevant_fragment_ids": [],
                    "possible_shared_skill_names": [],
                    "discouraged_shared_skill_names": [],
                    "desirable_keywords": [],
                    "discouraged_keywords": [],
                    "rubric_notes": "",
                },
                "status": "draft",
                "failure_type": ",".join(candidate.get("candidate_reason_tags", [])),
                "annotation_notes": (
                    "Auto-generated from ds100_aime candidate mining. "
                    "Workflow-summary/history-reuse labels still require annotation."
                ),
                "candidate_metadata": candidate,
                "mock_joint_alignments": [],
                "mock_joint_response": "",
                "mock_legacy_response": "",
            }
        )

    return {
        "source_candidates_path": str(candidates_path),
        "source_evolve_detail_path": str(evolve_detail_path),
        "source_baseline_detail_path": str(baseline_detail_path),
        "source_skills_path": str(skills_path),
        "n_drafts": len(drafts),
        "draft_cases": drafts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build replay case drafts from mined candidates")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--evolve-detail", required=True)
    parser.add_argument("--baseline-detail", required=True)
    parser.add_argument("--skills", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = build_replay_case_drafts(
        candidates_path=Path(args.candidates),
        evolve_detail_path=Path(args.evolve_detail),
        baseline_detail_path=Path(args.baseline_detail),
        skills_path=Path(args.skills),
    )
    text_out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text_out)
    print(text_out)


if __name__ == "__main__":
    main()
