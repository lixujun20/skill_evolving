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


def _text_tokens(value: Any) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "when",
        "then",
        "that",
        "this",
        "from",
        "into",
        "user",
        "call",
        "tool",
        "turn",
        "only",
        "after",
        "before",
        "current",
        "vehicle",
        "car",
    }
    return {
        item
        for item in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|\d+", str(value or "").lower())
        if len(item) > 2 and item not in stop
    }


def _skill_scope_text(skill: SkillArtifact) -> str:
    metadata = skill.metadata or {}
    interface = skill.interface
    chunks = [
        skill.description,
        metadata.get("scope"),
        metadata.get("applicability"),
        metadata.get("non_applicability"),
        interface.summary,
        interface.usage,
        interface.compatibility_notes,
        json.dumps(interface.input_contract or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(interface.output_contract or {}, ensure_ascii=False, sort_keys=True),
    ]
    chunks.extend(str(item) for item in (metadata.get("intent_keywords") or []))
    return "\n".join(str(item or "") for item in chunks)


def _output_contract_tool_names(skill: SkillArtifact) -> set[str]:
    text = json.dumps(skill.interface.output_contract or {}, ensure_ascii=False)
    tools = set()
    for match in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        tools.add(match)
    for item in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', text):
        if "_" in item or item[:1].islower():
            tools.add(item)
    return {item for item in tools if item and item not in {"tool_call", "tool_call_order"}}


def skill_action_tool_names(skill: SkillArtifact) -> set[str]:
    """Return tool names the skill actually instructs the executor to call."""

    tools = set(_output_contract_tool_names(skill))
    action_text = "\n".join(
        str(item or "")
        for item in [
            skill.body,
            skill.description,
            skill.interface.summary,
            skill.interface.usage,
            skill.interface.compatibility_notes,
        ]
    )
    for match in re.findall(r"\b(call|invoke|use)\s+([A-Za-z_][A-Za-z0-9_]*)\b", action_text, flags=re.I):
        tools.add(match[1])
    for match in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", action_text):
        tools.add(match)
    return {item for item in tools if item and item not in {"tool_call", "tool_call_order"}}


def bfcl_low_trust_counts_toward_task_limit(skill: SkillArtifact) -> bool:
    """Whether a low-trust candidate should consume the per-task risk cap."""

    high_risk_actions = {
        "set_navigation",
    }
    return bool(skill_action_tool_names(skill) & high_risk_actions)


def query_requests_engine_start(query: str) -> bool:
    query_lower = (query or "").lower()
    return any(
        phrase in query_lower
        for phrase in (
            "start the engine",
            "start engine",
            "start up the engine",
            "start up my engine",
            "start up our engine",
            "turn on the engine",
            "get the engine running",
            "engine running",
            "engine is on",
            "engine's on",
            "prime the engine",
            "fire up the engine",
            "ignite the engine",
            "start the vehicle's engine",
            "start the vehicle engine",
            "start the car engine",
            "start the vehicle",
            "ignitionmode='start'",
            "ignition mode 'start'",
            "ignition mode start",
        )
    )


def is_engine_start_brake_skill(skill: SkillArtifact) -> bool:
    skill_text = "\n".join(
        str(item or "")
        for item in [
            skill.name,
            skill.description,
            skill.body,
            skill.interface.summary,
            skill.interface.usage,
            json.dumps(skill.interface.output_contract or {}, ensure_ascii=False, sort_keys=True),
        ]
    )
    if not bool(re.search(r"\bpressBrakePedal\b", skill_text)) or not bool(re.search(r"\bstartEngine\b", skill_text)):
        return False
    lower = skill_text.lower()
    return bool(re.search(r"\bbrake\b", lower)) and any(
        phrase in lower
        for phrase in (
            "before startengine",
            "before calling startengine",
            "before accepting startengine",
            "before starting",
            "requires the brake",
            "safety precondition",
            "safety interlock",
        )
    )


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


def low_trust_turn_match_reason(skill: SkillArtifact, task: BenchmarkTask, turn_index: int, user_messages: List[Dict[str, Any]]) -> str:
    """Return an empty string when an unvalidated candidate is safe to expose.

    Low-trust trial/pending candidates are useful for recall, but their metadata
    can be broad because it is extracted from failed trajectories.  Keep the
    normal active-skill path permissive and make only this path require current
    turn evidence.
    """

    if not bfcl_skill_matches_turn(skill, task, turn_index, user_messages):
        return "turn_scope_mismatch"

    query = turn_query_text(user_messages).lower()
    query_tokens = _text_tokens(query)
    scope_tokens = _text_tokens(_skill_scope_text(skill))
    intent_tokens = _text_tokens(" ".join(str(item) for item in ((skill.metadata or {}).get("intent_keywords") or [])))
    scope_overlap = len(query_tokens & scope_tokens)
    intent_overlap = len(query_tokens & intent_tokens)
    tool_overlap = query_tool_overlap_score(skill, query)
    contract_tools = skill_action_tool_names(skill)

    # Executing navigation is an especially common source of BFCL extra calls:
    # many turns ask to find or identify a location, while a later turn asks to
    # set GPS/navigation.  Do not expose low-trust navigation workflows unless
    # the current turn explicitly asks for route/GPS/navigation execution.
    if "set_navigation" in contract_tools:
        navigation_terms = {
            "navigation",
            "navigate",
            "gps",
            "route",
            "directions",
        }
        phrase_intent = any(
            phrase in query
            for phrase in (
                "set navigation",
                "set our navigation",
                "set my navigation",
                "set the navigation",
                "set gps",
                "set the gps",
            )
        )
        if not ((query_tokens & navigation_terms) or phrase_intent):
            return "low_trust_navigation_without_explicit_navigation_intent"

    if tool_overlap > 0:
        return ""
    if intent_overlap >= 1 and scope_overlap >= 2:
        return ""
    if scope_overlap >= 4:
        return ""
    return "low_trust_insufficient_turn_evidence"


def bfcl_low_trust_skill_matches_turn(skill: SkillArtifact, task: BenchmarkTask, turn_index: int, user_messages: List[Dict[str, Any]]) -> bool:
    return low_trust_turn_match_reason(skill, task, turn_index, user_messages) == ""


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
    return bfcl_skill_task_filter_reason(skill, task) == ""


def bfcl_skill_task_filter_reason(skill: SkillArtifact, task: BenchmarkTask) -> str:
    task_domains = {
        str(item).strip()
        for item in (task.metadata.get("involved_classes", []) if task.metadata else [])
        if str(item).strip()
    }
    skill_domains_raw = {
        str(item).strip()
        for item in (skill.metadata.get("domains") or [])
        if str(item).strip()
    }
    skill_domains = {
        str(item).strip()
        for item in (skill.metadata.get("domains") or [])
        if str(item).strip() and str(item).strip().lower() != "all"
    }
    retrieval_guard = dict(skill.metadata.get("retrieval_guard") or {})
    excluded_domains = {
        str(item).strip()
        for item in (retrieval_guard.get("excluded_domains") or [])
        if str(item).strip()
    }
    if task_domains and excluded_domains and (task_domains & excluded_domains):
        return "excluded_by_refined_scope"
    if task_domains and skill_domains and not (task_domains & skill_domains):
        return "domain_mismatch"

    query = task_query_text(task).lower()
    forbid_keywords = [
        str(item).strip().lower()
        for item in (skill.metadata.get("forbid_keywords") or [])
        if str(item).strip()
    ]
    if forbid_keywords and any(keyword in query for keyword in forbid_keywords):
        return "forbid_keyword"

    if task_domains and not skill_domains and "all" not in {item.lower() for item in skill_domains_raw}:
        # Refactor-created skills occasionally arrive without domain metadata.
        # Do not let pure vector similarity inject those skills across domains;
        # require concrete tool/intent evidence from the user text.
        intent_keywords = [
            str(item).strip().lower()
            for item in (skill.metadata.get("intent_keywords") or [])
            if str(item).strip()
        ]
        intent_overlap = sum(1 for keyword in intent_keywords if keyword in query)
        tool_overlap = query_tool_overlap_score(skill, query)
        if tool_overlap <= 0 or intent_overlap <= 0:
            return "missing_domain_requires_strong_tool_and_intent_match"

    return ""


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
        1 if query_requests_engine_start(query) and is_engine_start_brake_skill(skill) else 0,
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
    if _is_contextual_order_details_skill(skill) and not _query_requests_contextual_order_details(query):
        return False

    return True


def _is_contextual_order_details_skill(skill: SkillArtifact) -> bool:
    metadata = skill.metadata or {}
    domains = {str(item).strip() for item in (metadata.get("domains") or []) if str(item).strip()}
    if "TradingBot" not in domains:
        return False
    action_tools = skill_action_tool_names(skill)
    allowed_tools = {
        str(item).strip()
        for item in (metadata.get("allowed_tools") or [])
        if str(item).strip()
    }
    tools = allowed_tools or action_tools
    if not tools or not tools <= {"get_order_details"}:
        return False
    scope = " ".join(
        str(item or "")
        for item in (
            skill.name,
            skill.description,
            skill.body,
            skill.interface.summary,
            skill.interface.usage,
            " ".join(str(keyword) for keyword in (metadata.get("intent_keywords") or [])),
        )
    ).lower()
    return "recent order" in scope or "latest order" in scope or "most recent order" in scope


def _query_requests_contextual_order_details(query: str) -> bool:
    query_lower = (query or "").lower()
    if not query_lower:
        return False
    explicit_phrases = (
        "latest order",
        "recent order",
        "most recent order",
        "order details",
        "details of the order",
        "details of my order",
        "verify the order",
        "verify my order",
        "review the order",
        "review my order",
        "status of the order",
        "status of my order",
    )
    if any(phrase in query_lower for phrase in explicit_phrases):
        return True
    tokens = _text_tokens(query_lower)
    detail_terms = {"detail", "details", "status", "verify", "verification", "review", "check", "show"}
    order_terms = {"order", "orders", "transaction", "execution"}
    return bool(tokens & detail_terms) and bool(tokens & order_terms)


__all__ = [
    "task_query_text",
    "turn_query_text",
    "query_tool_overlap_score",
    "skill_action_tool_names",
    "bfcl_low_trust_counts_toward_task_limit",
    "query_requests_engine_start",
    "is_engine_start_brake_skill",
    "low_trust_turn_match_reason",
    "error_aware_skill_query",
    "bfcl_retrieval_context",
    "bfcl_low_trust_skill_matches_turn",
    "bfcl_skill_matches_task",
    "bfcl_skill_task_filter_reason",
    "bfcl_skill_rerank_key",
    "bfcl_skill_matches_turn",
]
