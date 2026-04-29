"""Handwritten and trace-derived BFCL skill artifacts."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

from academic.benchmarks.bfcl import _parse_call
from academic.benchmarks.types import BenchmarkResult, BenchmarkTask, SkillArtifact


HANDWRITTEN_BFCL_SKILLS: List[SkillArtifact] = [
    SkillArtifact(
        name="bfcl_file_system_navigation",
        kind="atomic_tool_rule_card",
        description="Rules for GorillaFileSystem path-sensitive operations.",
        body=(
            "Before calling file tools, reason about current working directory. "
            "Use cd one folder at a time. mkdir before moving into a new folder. "
            "For mv/cp/diff/sort/grep/cat, pass only local file or directory names "
            "unless the schema explicitly accepts a path."
        ),
        metadata={"domains": ["GorillaFileSystem"]},
    ),
    SkillArtifact(
        name="bfcl_state_id_reuse",
        kind="functional_workflow_card",
        description="Reuse ids/tokens from earlier turns and tool results.",
        body=(
            "Multi-turn BFCL tasks often hide the needed id in a previous user turn, "
            "tool result, or environment record. Reuse exact ids such as booking_id, "
            "order_id, ticket_id, tweet_id, card_id, and access_token. Do not write "
            "placeholder strings like 'booking_id' when a concrete id is available."
        ),
        metadata={"domains": ["TravelAPI", "TradingBot", "TicketAPI", "TwitterAPI"]},
    ),
    SkillArtifact(
        name="bfcl_schema_parameter_names",
        kind="atomic_tool_rule_card",
        description="Use exact BFCL function schema parameter names.",
        body=(
            "Arguments are scored by exact schema names. Never invent aliases. "
            "For TravelAPI invoice and support calls use booking_id if the schema "
            "requires booking_id, not insurance_id or reservation_id. For ticket "
            "creation use priority/title/description exactly as provided by schema."
        ),
        metadata={"domains": ["TravelAPI", "TicketAPI"]},
    ),
    SkillArtifact(
        name="bfcl_multi_action_turn_completion",
        kind="planning_card",
        description="Complete all required tool calls before ending a turn.",
        body=(
            "A single user turn may require multiple API calls. Keep calling tools "
            "until all requested state changes or lookups for the current turn are "
            "done, then stop. Do not ask the user for values that can be inferred "
            "from available context or previous tool results."
        ),
        metadata={"domains": ["all"]},
    ),
    SkillArtifact(
        name="bfcl_literal_user_text_arguments",
        kind="atomic_tool_rule_card",
        description="Prefer concise literal text arguments over creative rewrites.",
        body=(
            "BFCL state checks compare many string arguments exactly. When the user "
            "gives a title, message, or description, preserve the requested wording "
            "and avoid adding invoice details, explanations, greetings, or quotes "
            "unless explicitly requested. Treat 'high-priority' conservatively as "
            "priority 4 when the schema uses a 1-5 scale and does not define a "
            "separate urgent level."
        ),
        metadata={"domains": ["TicketAPI", "TravelAPI"]},
    ),
]


def default_bfcl_skill_store():
    from academic.benchmarks.artifacts import ArtifactStore

    return ArtifactStore(HANDWRITTEN_BFCL_SKILLS)


def extract_bfcl_skills_from_results(results: List[BenchmarkResult]) -> List[SkillArtifact]:
    """Create compact skill cards from successful or partially successful traces.

    This is intentionally conservative for v1: it distills observed expected
    calls and failure patterns into tool-order and parameter-name cards instead
    of trying to synthesize executable tools.
    """
    domain_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    param_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    successful_task_ids: List[str] = []

    for result in results:
        metrics = result.metrics or {}
        if metrics.get("task_success") or metrics.get("relaxed_task_success") or metrics.get("call_recall", 0) >= 0.5:
            successful_task_ids.append(result.task_id)
        for call in result.trace.get("tool_calls", []) or []:
            name = call.get("name")
            if not name or name == "use_skill":
                continue
            tool_counts[name] += 1
            for key in (call.get("arguments") or {}):
                param_counts[name][key] += 1
    error_counts: Counter[str] = Counter()
    wrong_param_counts: Counter[str] = Counter()
    for result in results:
        for error in (result.metrics or {}).get("call_errors", []) or []:
            etype = error.get("type")
            if etype:
                error_counts[str(etype)] += 1
            if etype == "argument_mismatch":
                for param in (error.get("wrong") or {}):
                    wrong_param_counts[str(param)] += 1
                for param in (error.get("missing") or {}):
                    wrong_param_counts[f"missing:{param}"] += 1
                for param in (error.get("unexpected") or {}):
                    wrong_param_counts[f"unexpected:{param}"] += 1

    artifacts: List[SkillArtifact] = []
    if tool_counts:
        top_tools = ", ".join(name for name, _ in tool_counts.most_common(12))
        artifacts.append(
            SkillArtifact(
                name="bfcl_observed_tool_usage_patterns",
                kind="functional_workflow_card",
                description="Observed BFCL tool usage patterns from train rollouts.",
                body=(
                    f"Frequently useful tools in recent BFCL rollouts: {top_tools}. "
                    "When a user asks for a state change, prefer a direct tool call "
                    "over asking for confirmation."
                ),
                metadata={"source": "evolve_rollouts", "task_ids": successful_task_ids[:20]},
            )
        )
    for tool_name, counter in list(param_counts.items())[:20]:
        params = ", ".join(name for name, _ in counter.most_common(8))
        artifacts.append(
            SkillArtifact(
                name=f"bfcl_params_{tool_name}",
                kind="atomic_tool_rule_card",
                description=f"Observed parameter names for {tool_name}.",
                body=f"For `{tool_name}`, use these observed parameter names when applicable: {params}.",
                metadata={"source": "evolve_rollouts", "tool": tool_name},
            )
        )
    if error_counts or wrong_param_counts:
        error_text = ", ".join(f"{name}={count}" for name, count in error_counts.most_common(8))
        param_text = ", ".join(f"{name}={count}" for name, count in wrong_param_counts.most_common(12))
        artifacts.append(
            SkillArtifact(
                name="bfcl_observed_error_feedback",
                kind="debug_feedback_card",
                description="Observed BFCL failure modes from train rollouts.",
                body=(
                    f"Recent train rollouts showed error types: {error_text or 'none'}. "
                    f"Parameter-level issues: {param_text or 'none'}. In future calls, "
                    "prioritize exact schema names, concise literal user wording for "
                    "text fields, and avoid extra lookup/tool calls unless needed."
                ),
                metadata={"source": "evolve_rollouts", "error_counts": dict(error_counts)},
            )
        )
    return artifacts


def write_bfcl_handwritten_skills(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([skill.as_dict() for skill in HANDWRITTEN_BFCL_SKILLS], ensure_ascii=False, indent=2)
    )
    return path
