"""BFCL skill retrieval predicates and turn-level matching helpers."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from academic.benchmarks.core.types import BenchmarkTask, SkillArtifact


def task_query_text(task: BenchmarkTask) -> str:
    chunks = []
    for turn in task.question:
        for msg in turn:
            chunks.append(str(msg.get("content", "")))
    return "\n".join(chunks)


def turn_query_text(user_messages: List[Dict[str, Any]]) -> str:
    return "\n".join(str(msg.get("content", "")) for msg in user_messages)


def query_tool_overlap_score(skill: SkillArtifact, query: str) -> int:
    allowed_tools = [
        str(item).strip().lower()
        for item in (skill.metadata.get("allowed_tools") or [])
        if str(item).strip()
    ]
    if not allowed_tools:
        return 0
    query_lower = (query or "").lower()
    score = 0
    for tool_name in allowed_tools:
        tool_tokens = re.findall(r"[a-z]+|\d+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tool_name).replace("_", " "))
        if tool_name in query_lower:
            score += 3
            continue
        if any(token and token in query_lower for token in tool_tokens):
            score += 1
    return score


def error_aware_skill_query(
    *,
    task: BenchmarkTask,
    turn_index: int,
    user_messages: List[Dict[str, Any]],
    tool_name: str,
    args: Dict[str, Any],
    error: str | None,
) -> str:
    return "\n".join(
        [
            task_query_text(task),
            turn_query_text(user_messages),
            f"tool_error tool={tool_name}",
            f"arguments={json.dumps(args, ensure_ascii=False, sort_keys=True)}",
            f"error={error or ''}",
            "Need a skill about exact schema names, workflow ordering, literal arguments, id reuse, or dependency-aware retry.",
        ]
    )


def bfcl_retrieval_context(
    task: BenchmarkTask,
    *,
    phase: str,
    turn_index: int,
    tool_name: str | None = None,
) -> Dict[str, Any]:
    domains = [
        str(item).strip()
        for item in (task.metadata.get("involved_classes", []) if task.metadata else [])
        if str(item).strip()
    ]
    runtime_tools: List[str] = []
    if tool_name and tool_name not in runtime_tools:
        runtime_tools.append(tool_name)
    query_tags = [f"domain:{domain}" for domain in domains]
    query_tags.extend(f"tool:{tool}" for tool in runtime_tools)
    return {
        "phase": phase,
        "turn_index": turn_index,
        "tool_name": tool_name,
        "domains": domains,
        "runtime_tools": runtime_tools,
        "query_tags": query_tags,
    }


def bfcl_skill_matches_task(skill: SkillArtifact, task: BenchmarkTask) -> bool:
    task_domains = {
        str(item).strip()
        for item in (task.metadata.get("involved_classes", []) if task.metadata else [])
        if str(item).strip()
    }
    skill_domains = {
        str(item).strip()
        for item in (skill.metadata.get("domains") or [])
        if str(item).strip() and str(item).strip().lower() != "all"
    }
    if task_domains and skill_domains and not (task_domains & skill_domains):
        return False

    query = task_query_text(task).lower()
    forbid_keywords = [
        str(item).strip().lower()
        for item in (skill.metadata.get("forbid_keywords") or [])
        if str(item).strip()
    ]
    if forbid_keywords and any(keyword in query for keyword in forbid_keywords):
        return False

    return True


def bfcl_skill_rerank_key(
    skill: SkillArtifact,
    task: BenchmarkTask,
    turn_index: int,
    user_messages: List[Dict[str, Any]],
) -> tuple:
    query = turn_query_text(user_messages)
    tool_overlap = query_tool_overlap_score(skill, query)
    task_query = task_query_text(task)
    global_overlap = query_tool_overlap_score(skill, task_query)
    intent_keywords = [
        str(item).strip().lower()
        for item in (skill.metadata.get("intent_keywords") or [])
        if str(item).strip()
    ]
    intent_overlap = sum(1 for keyword in intent_keywords if keyword in query.lower())
    source_task_count = len(skill.metadata.get("source_task_ids") or [])
    source_extra_call_count = int(skill.metadata.get("source_extra_call_count") or 0)
    source_error_weight = int(sum((skill.metadata.get("source_error_counts") or {}).values()))
    return (
        tool_overlap,
        global_overlap,
        intent_overlap,
        source_error_weight,
        source_extra_call_count,
        source_task_count,
    )


def bfcl_skill_matches_turn(
    skill: SkillArtifact,
    task: BenchmarkTask,
    turn_index: int,
    user_messages: List[Dict[str, Any]],
) -> bool:
    if not bfcl_skill_matches_task(skill, task):
        return False
    query = turn_query_text(user_messages).lower()
    forbid_keywords = [
        str(item).strip().lower()
        for item in (skill.metadata.get("forbid_keywords") or [])
        if str(item).strip()
    ]
    if forbid_keywords and any(keyword in query for keyword in forbid_keywords):
        return False

    return True


__all__ = [
    "task_query_text",
    "turn_query_text",
    "query_tool_overlap_score",
    "error_aware_skill_query",
    "bfcl_retrieval_context",
    "bfcl_skill_matches_task",
    "bfcl_skill_rerank_key",
    "bfcl_skill_matches_turn",
]
