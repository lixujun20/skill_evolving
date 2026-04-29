"""
planner.py — lightweight pre-execution planning for the academic framework.

The current online planner is intentionally cheap and deterministic:
it summarizes retrieved skills and historical workflows into an explicit
executor-facing brief. This keeps the real `academic` execution path aligned
with the replay/refactoring motivation without adding another heavy LLM call
before every solve.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from academic.skill_store import Skill, WorkflowRecord


@dataclass
class PlanningArtifact:
    query: str
    history_reuse_action: str
    rationale: str
    recommended_skill_calls: List[str] = field(default_factory=list)
    referenced_workflows: List[Dict[str, Any]] = field(default_factory=list)
    proposed_shared_skills: List[str] = field(default_factory=list)
    executor_context: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_execution_plan(
    query: str,
    retrieved_skills: List[Skill],
    retrieved_workflows: List[WorkflowRecord],
) -> PlanningArtifact:
    """
    Build a compact plan summary for the executor.

    This planner does not hard-constrain the solve. Its job is to make the
    historical evidence explicit so the executor can decide whether to reuse:
    a full prior workflow, an adapted workflow, a workflow fragment, or nothing.
    """
    if retrieved_workflows:
        top = retrieved_workflows[0]
        action = top.workflow_decision or "adapt_plan"
        if action not in {
            "reuse_plan",
            "adapt_plan",
            "reuse_workflow_fragment",
            "fresh",
            "propose_shared_skill",
        }:
            action = "adapt_plan"
        rationale = (
            "A related historical workflow was retrieved. Start by checking whether "
            "its structure transfers directly, whether only a fragment is reusable, "
            "or whether it should be ignored after a quick relevance check."
        )
    else:
        action = "fresh"
        rationale = (
            "No historical workflow was retrieved, so plan from scratch while still "
            "checking whether any retrieved skill directly matches a sub-problem."
        )

    recommended_skill_calls = [skill.name for skill in retrieved_skills[:5]]
    referenced_workflows = []
    for idx, record in enumerate(retrieved_workflows[:3], start=1):
        referenced_workflows.append(
            {
                "rank": idx,
                "query": record.query,
                "workflow_decision": record.workflow_decision,
                "workflow_summary": record.workflow_summary,
                "workflow_plan": record.workflow_plan,
                "retrieved_skills": list(record.retrieved_skills),
            }
        )

    lines = [
        f"Planned history reuse mode: {action}",
        f"Planner rationale: {rationale}",
        "Execution checklist:",
        "1. First inspect whether a retrieved historical workflow is genuinely relevant.",
        "2. If a retrieved skill directly matches a needed sub-problem, call that skill instead of rewriting it.",
        "3. If history only partially matches, reuse the relevant fragment and adapt the rest.",
        "4. If neither history nor skills help, continue with a fresh plan.",
    ]

    if recommended_skill_calls:
        lines.append(
            "Retrieved skills worth considering first: "
            + ", ".join(recommended_skill_calls)
        )
    else:
        lines.append("No retrieved skills were available.")

    if referenced_workflows:
        lines.append("Historical workflows to inspect:")
        for item in referenced_workflows:
            lines.append(
                f"- Workflow {item['rank']} | decision={item['workflow_decision'] or 'unknown'}"
            )
            lines.append(f"  query: {item['query'][:220]}")
            if item["workflow_summary"]:
                lines.append(f"  summary: {item['workflow_summary'][:420]}")
            if item["workflow_plan"]:
                lines.append(f"  plan fragment: {item['workflow_plan'][:420]}")
    else:
        lines.append("No historical workflow context is available for this query.")

    return PlanningArtifact(
        query=query,
        history_reuse_action=action,
        rationale=rationale,
        recommended_skill_calls=recommended_skill_calls,
        referenced_workflows=referenced_workflows,
        proposed_shared_skills=[],
        executor_context="\n".join(lines),
    )
