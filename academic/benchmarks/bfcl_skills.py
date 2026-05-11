"""Handwritten and trace-derived BFCL skill artifacts.

The BFCL evolve path is intentionally conservative. Prompt-injected notes can
easily hurt function-calling behavior, so auto-extracted artifacts should be
schema- or parameter-level hints tied to specific tools rather than generic
workflow advice.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

from academic.benchmarks.bfcl import BFCL_CLASS_FILE_BY_DOC
from academic.benchmarks.types import BenchmarkResult, SkillArtifact


HANDWRITTEN_BFCL_SKILLS: List[SkillArtifact] = [
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
        metadata={"domains": ["TravelAPI", "TradingBot", "TicketAPI", "TwitterAPI"], "injection_type": "workflow"},
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
        metadata={"domains": ["TravelAPI", "TicketAPI"], "injection_type": "informational"},
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
        metadata={"domains": ["all"], "injection_type": "workflow"},
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
        metadata={
            "domains": ["TicketAPI", "TravelAPI"],
            "allowed_tools": ["create_ticket", "edit_ticket", "book_flight", "contact_customer_support", "retrieve_invoice"],
            "injection_type": "informational",
        },
    ),
    SkillArtifact(
        name="bfcl_task_checklist",
        kind="executable_tool",
        description="Return a concise checklist for avoiding common BFCL tool-call errors.",
        body=(
            "Checklist: identify all actions requested in the current user turn; prefer "
            "direct domain tools over unnecessary lookup calls; reuse exact ids and "
            "tokens from user text, prior turns, or tool results; keep string arguments "
            "literal and concise; use exact schema parameter names; stop once the turn's "
            "requested state changes or lookup results are complete."
        ),
        metadata={"domains": ["all"], "injection_type": "functional"},
    ),
]


def default_bfcl_skill_store():
    from academic.benchmarks.artifacts import ArtifactStore

    return ArtifactStore(HANDWRITTEN_BFCL_SKILLS)


def extract_bfcl_skills_from_results(
    results: List[BenchmarkResult],
    *,
    source_label: str = "evolve_rollouts",
    tool_schemas: Iterable[Dict[str, Any]] | None = None,
) -> List[SkillArtifact]:
    """Create compact, high-support BFCL skill cards from rollout statistics."""
    tool_context = _tool_context_by_name(tool_schemas or [])
    tool_counts: Counter[str] = Counter()
    param_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    tool_domains: Dict[str, Counter[str]] = defaultdict(Counter)
    tool_task_ids: Dict[str, Set[str]] = defaultdict(set)
    tool_examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    error_type_counts_by_tool: Dict[str, Counter[str]] = defaultdict(Counter)
    text_mismatch_examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    text_wrong_counts_by_tool: Dict[str, Counter[str]] = defaultdict(Counter)
    wrong_value_counts_by_tool: Dict[str, Counter[str]] = defaultdict(Counter)
    literal_value_examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    unexpected_param_counts_by_tool: Dict[str, Counter[str]] = defaultdict(Counter)
    missing_param_counts_by_tool: Dict[str, Counter[str]] = defaultdict(Counter)
    extra_call_counts: Counter[str] = Counter()
    extra_call_task_ids: Dict[str, Set[str]] = defaultdict(set)
    missing_call_counts: Counter[str] = Counter()
    missing_call_task_ids: Dict[str, Set[str]] = defaultdict(set)
    missing_call_examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    missing_call_arg_counts_by_tool: Dict[str, Counter[str]] = defaultdict(Counter)
    positive_results: List[BenchmarkResult] = []
    for result in results:
        metrics = result.metrics or {}
        if metrics.get("official_valid") is True:
            positive_results.append(result)
    for result in positive_results:
        bad_param_tools = _tools_with_argument_errors(result)
        classes = [
            str(item).strip()
            for item in ((result.trace or {}).get("official_check", {}) or {}).get("involved_classes", [])
            if str(item).strip()
        ]
        if not classes:
            classes = [
                str(item).strip()
                for item in (((result.metrics or {}).get("official_check", {}) or {}).get("involved_classes", []))
                if str(item).strip()
            ]
        for call in result.trace.get("tool_calls", []) or []:
            name = call.get("name")
            if not name or name == "use_skill":
                continue
            if name in bad_param_tools:
                continue
            tool_counts[name] += 1
            tool_task_ids[name].add(result.task_id)
            for cls in classes:
                tool_domains[name][cls] += 1
            for key in (call.get("arguments") or {}):
                param_counts[name][key] += 1
            if len(tool_examples[name]) < 3:
                tool_examples[name].append(
                    {
                        "task_id": result.task_id,
                        "turn_index": call.get("turn_index"),
                        "arguments": call.get("arguments") or {},
                    }
                )
    error_counts: Counter[str] = Counter()
    wrong_param_counts: Counter[str] = Counter()
    error_task_ids: Dict[str, Set[str]] = defaultdict(set)
    wrong_param_task_ids: Dict[str, Set[str]] = defaultdict(set)
    for result in results:
        for error in (result.metrics or {}).get("call_errors", []) or []:
            etype = error.get("type")
            if etype:
                error_counts[str(etype)] += 1
                error_task_ids[str(etype)].add(result.task_id)
            if etype == "extra_call":
                name = str(error.get("actual_name", "")).strip()
                if name:
                    extra_call_counts[name] += 1
                    extra_call_task_ids[name].add(result.task_id)
            if etype == "missing_call":
                name = str(error.get("expected_name", "")).strip()
                if name:
                    missing_call_counts[name] += 1
                    missing_call_task_ids[name].add(result.task_id)
                    for key in (error.get("expected_arguments") or {}):
                        missing_call_arg_counts_by_tool[name][str(key)] += 1
                    examples = missing_call_examples[name]
                    if len(examples) < 3:
                        examples.append(
                            {
                                "task_id": result.task_id,
                                "turn_index": error.get("turn_index"),
                                "expected_arguments": error.get("expected_arguments") or {},
                            }
                        )
            if etype == "argument_mismatch":
                tool_name = str(error.get("name", "")).strip()
                if tool_name:
                    error_type_counts_by_tool[tool_name]["argument_mismatch"] += 1
                missing_map = dict(error.get("missing") or {})
                unexpected_map = dict(error.get("unexpected") or {})
                wrong_map = dict(error.get("wrong") or {})
                if tool_name:
                    missing_map, unexpected_map = _drop_positional_name_artifacts(
                        tool_name,
                        missing_map,
                        unexpected_map,
                        tool_context,
                    )
                for param in wrong_map:
                    key = str(param)
                    wrong_param_counts[key] += 1
                    wrong_param_task_ids[key].add(result.task_id)
                    if tool_name:
                        error_type_counts_by_tool[tool_name][f"wrong:{key}"] += 1
                        wrong_value_counts_by_tool[tool_name][key] += 1
                    if key in {"description", "message", "content", "title"} and tool_name:
                        text_wrong_counts_by_tool[tool_name][key] += 1
                        examples = text_mismatch_examples[tool_name]
                        if len(examples) < 3:
                            wrong = wrong_map.get(param) or {}
                            examples.append(
                                {
                                    "task_id": result.task_id,
                                    "field": key,
                                    "expected": wrong.get("expected"),
                                    "actual": wrong.get("actual"),
                                }
                            )
                    elif tool_name:
                        examples = literal_value_examples[tool_name]
                        if len(examples) < 3:
                            wrong = wrong_map.get(param) or {}
                            examples.append(
                                {
                                    "task_id": result.task_id,
                                    "field": key,
                                    "expected": wrong.get("expected"),
                                    "actual": wrong.get("actual"),
                                }
                            )
                for param in missing_map:
                    key = f"missing:{param}"
                    wrong_param_counts[key] += 1
                    wrong_param_task_ids[key].add(result.task_id)
                    if tool_name:
                        missing_param_counts_by_tool[tool_name][str(param)] += 1
                        error_type_counts_by_tool[tool_name][key] += 1
                for param in unexpected_map:
                    key = f"unexpected:{param}"
                    wrong_param_counts[key] += 1
                    wrong_param_task_ids[key].add(result.task_id)
                    if tool_name:
                        unexpected_param_counts_by_tool[tool_name][str(param)] += 1
                        error_type_counts_by_tool[tool_name][key] += 1

    artifacts: List[SkillArtifact] = []
    candidate_tools: Set[str] = (
        set(param_counts)
        | set(error_type_counts_by_tool)
        | set(extra_call_counts)
        | set(missing_call_counts)
        | set(text_wrong_counts_by_tool)
        | set(wrong_value_counts_by_tool)
    )
    ranked_tools = sorted(
        candidate_tools,
        key=lambda tool_name: (
            4 * int(sum(error_type_counts_by_tool.get(tool_name, Counter()).values()))
            + 3 * int(missing_call_counts.get(tool_name, 0))
            + 2 * int(extra_call_counts.get(tool_name, 0))
            + 2 * int(sum(wrong_value_counts_by_tool.get(tool_name, Counter()).values()))
            + int(tool_counts.get(tool_name, 0)),
            int(sum(error_type_counts_by_tool.get(tool_name, Counter()).values())),
            int(tool_counts.get(tool_name, 0)),
            tool_name,
        ),
        reverse=True,
    )
    for tool_name in ranked_tools[:24]:
        counter = param_counts.get(tool_name, Counter())
        top_domains = [name for name, _ in tool_domains.get(tool_name, Counter()).most_common(2)]
        context = tool_context.get(tool_name, {})
        intent_keywords = _tool_intent_keywords(tool_name, context)
        params = ", ".join(name for name, _ in counter.most_common(8))
        error_weight = int(sum(error_type_counts_by_tool.get(tool_name, Counter()).values()))
        if counter and (int(tool_counts.get(tool_name, 0)) >= 2 or error_weight >= 2):
            body = f"For `{tool_name}`, prefer these observed schema parameter names when applicable: {params}."
            if "arg0" in counter:
                body += " If the schema uses placeholders such as `arg0`, pass the value under that exact field name."
            artifacts.append(
                SkillArtifact(
                    name=f"bfcl_params_{tool_name}",
                    kind="atomic_tool_rule_card",
                    description=f"Observed high-confidence parameter names for {tool_name}.",
                    body=body,
                    metadata={
                        "source": source_label,
                        "tool": tool_name,
                        "allowed_tools": [tool_name],
                        "domains": top_domains or _tool_domains_from_context(context) or ["all"],
                        "injection_type": "informational",
                        "intent_keywords": intent_keywords,
                        "tool_description": context.get("description", ""),
                        "source_task_ids": sorted(tool_task_ids.get(tool_name, set())),
                        "source_tool_count": int(tool_counts.get(tool_name, 0)),
                        "source_param_counts": dict(counter),
                        "source_examples": tool_examples.get(tool_name, []),
                        "source_train_total_runs": len(results),
                        "source_train_positive_runs": len(positive_results),
                    },
                )
            )
        # Exact-parameter rule for tools with repeated alias / missing-arg mistakes.
        repeated_missing = [name for name, count in missing_param_counts_by_tool.get(tool_name, Counter()).items() if count >= 2]
        repeated_unexpected = [name for name, count in unexpected_param_counts_by_tool.get(tool_name, Counter()).items() if count >= 2]
        if repeated_missing or repeated_unexpected:
            body_parts = []
            if repeated_missing:
                body_parts.append(
                    f"For `{tool_name}`, make sure these parameters are present when required by schema: {', '.join(sorted(repeated_missing))}."
                )
            if repeated_unexpected:
                body_parts.append(
                    f"For `{tool_name}`, do not invent alias or extra parameters such as: {', '.join(sorted(repeated_unexpected))}."
                )
            source_task_ids = set(tool_task_ids.get(tool_name, set()))
            for name in repeated_missing:
                source_task_ids.update(wrong_param_task_ids.get(f"missing:{name}", set()))
            for name in repeated_unexpected:
                source_task_ids.update(wrong_param_task_ids.get(f"unexpected:{name}", set()))
            artifacts.append(
                SkillArtifact(
                    name=f"bfcl_exact_args_{tool_name}",
                    kind="negative_rule_card",
                    description=f"Exact schema-argument rule for {tool_name}.",
                    body=" ".join(body_parts),
                    metadata={
                        "source": source_label,
                        "tool": tool_name,
                        "allowed_tools": [tool_name],
                        "domains": top_domains or _tool_domains_from_context(context) or ["all"],
                        "injection_type": "informational",
                        "intent_keywords": intent_keywords,
                        "tool_description": context.get("description", ""),
                        "source_task_ids": sorted(source_task_ids),
                        "source_error_counts": dict(error_type_counts_by_tool.get(tool_name, Counter())),
                    },
                )
            )
        # Text fields should stay literal and concise when train traces show expansions.
        repeated_text_fields = [
            field
            for field, count in text_wrong_counts_by_tool.get(tool_name, Counter()).items()
            if count >= 2
        ]
        if repeated_text_fields:
            text_fields = sorted({item["field"] for item in text_mismatch_examples[tool_name]})
            artifacts.append(
                SkillArtifact(
                    name=f"bfcl_literal_text_{tool_name}",
                    kind="literal_text_rule_card",
                    description=f"Keep `{tool_name}` text arguments literal and concise.",
                    body=(
                        f"For `{tool_name}`, keep text fields `{', '.join(text_fields)}` close to the user-provided wording. "
                        "Do not add explanations, politeness, extra facts, or paraphrases unless the user explicitly asks for them."
                    ),
                    metadata={
                        "source": source_label,
                        "tool": tool_name,
                        "allowed_tools": [tool_name],
                        "domains": top_domains or _tool_domains_from_context(context) or ["all"],
                        "injection_type": "informational",
                        "intent_keywords": intent_keywords,
                        "tool_description": context.get("description", ""),
                        "source_examples": text_mismatch_examples[tool_name],
                        "source_task_ids": sorted({item["task_id"] for item in text_mismatch_examples[tool_name]}),
                    },
                )
            )
        repeated_literal_fields = [
            field
            for field, count in wrong_value_counts_by_tool.get(tool_name, Counter()).items()
            if count >= 2 and field not in {"description", "message", "content", "title"}
        ]
        if repeated_literal_fields:
            artifacts.append(
                SkillArtifact(
                    name=f"bfcl_literal_value_{tool_name}",
                    kind="literal_value_rule_card",
                    description=f"Preserve explicit user-provided values for `{tool_name}`.",
                    body=(
                        f"For `{tool_name}`, keep fields `{', '.join(sorted(repeated_literal_fields))}` aligned with the "
                        "explicit value provided by the user or prior tool result. Avoid silent normalization, substitution, "
                        "or paraphrase unless the schema or environment requires it."
                    ),
                    metadata={
                        "source": source_label,
                        "tool": tool_name,
                        "allowed_tools": [tool_name],
                        "domains": top_domains or _tool_domains_from_context(context) or ["all"],
                        "injection_type": "informational",
                        "intent_keywords": intent_keywords,
                        "tool_description": context.get("description", ""),
                        "source_examples": literal_value_examples.get(tool_name, []),
                        "source_task_ids": sorted(
                            {
                                task_id
                                for field in repeated_literal_fields
                                for task_id in wrong_param_task_ids.get(field, set())
                            }
                        ),
                    },
                )
            )
        if int(missing_call_counts.get(tool_name, 0)) >= 2:
            common_arg_names = [
                name for name, _ in missing_call_arg_counts_by_tool.get(tool_name, Counter()).most_common(5)
            ]
            body_parts = [
                f"Train failures often ended the turn before issuing `{tool_name}`.",
                "If the current user turn implies this operation, continue calling tools until this action is actually completed before ending the turn.",
            ]
            if common_arg_names:
                body_parts.append(
                    f"Common required arguments for `{tool_name}` include: {', '.join(common_arg_names)}."
                )
            artifacts.append(
                SkillArtifact(
                    name=f"bfcl_complete_call_{tool_name}",
                    kind="planning_card",
                    description=f"Do not stop before completing the `{tool_name}` call when it is needed.",
                    body=" ".join(body_parts),
                    metadata={
                        "source": source_label,
                        "tool": tool_name,
                        "allowed_tools": [tool_name],
                        "domains": top_domains or _tool_domains_from_context(context) or ["all"],
                        "injection_type": "workflow",
                        "intent_keywords": intent_keywords,
                        "tool_description": context.get("description", ""),
                        "source_task_ids": sorted(missing_call_task_ids.get(tool_name, set())),
                        "source_missing_call_count": int(missing_call_counts.get(tool_name, 0)),
                        "source_examples": missing_call_examples.get(tool_name, []),
                    },
                )
            )
    # Repeated extra calls are converted into negative guidance cards.
    for tool_name, count in extra_call_counts.most_common(12):
        if count < 3:
            continue
        context = tool_context.get(tool_name, {})
        domains = _tool_domains_from_context(context) or _guess_domains_from_tool_name(tool_name)
        artifacts.append(
            SkillArtifact(
                name=f"bfcl_avoid_extra_{tool_name}",
                kind="negative_rule_card",
                description=f"Avoid unnecessary `{tool_name}` calls unless the turn explicitly requires it.",
                body=(
                    f"`{tool_name}` appeared as an unnecessary extra call in multiple train traces. "
                    "Do not use it as a speculative lookup or status check when the user already provided enough information for the required action."
                ),
                metadata={
                    "source": source_label,
                    "tool": tool_name,
                    "allowed_tools": [tool_name],
                    "domains": domains or ["all"],
                    "injection_type": "informational",
                    "intent_keywords": _tool_intent_keywords(tool_name, context),
                    "tool_description": context.get("description", ""),
                    "source_task_ids": sorted(extra_call_task_ids.get(tool_name, set())),
                    "source_extra_call_count": count,
                },
            )
        )
    total_extra_calls = int(sum(extra_call_counts.values()))
    total_missing_calls = int(sum(missing_call_counts.values()))
    if total_extra_calls >= 10:
        artifacts.append(
            SkillArtifact(
                name="bfcl_direct_action_bias",
                kind="workflow_guardrail_card",
                description="Prefer direct required actions over speculative lookups or status checks.",
                body=(
                    "Across recent train rollouts, many BFCL failures came from optional exploratory tool calls. "
                    "When the current turn already gives enough information for the required action, prefer the direct action tool and skip speculative lookups or status checks."
                ),
                metadata={
                    "source": source_label,
                    "domains": ["all"],
                    "injection_type": "workflow",
                    "intent_keywords": ["action", "direct", "lookup", "status", "request"],
                    "source_extra_call_count": total_extra_calls,
                },
            )
        )
    if total_missing_calls >= 6:
        artifacts.append(
            SkillArtifact(
                name="bfcl_turn_followthrough",
                kind="workflow_guardrail_card",
                description="Re-check whether the current turn still needs another tool call before stopping.",
                body=(
                    "Recent train rollouts frequently ended a turn after a partial solution. "
                    "After each tool result, re-check whether the user asked for an additional state change or lookup in the same turn, and continue until every requested action is done."
                ),
                metadata={
                    "source": source_label,
                    "domains": ["all"],
                    "injection_type": "workflow",
                    "intent_keywords": ["turn", "action", "request", "continue", "complete"],
                    "source_missing_call_count": total_missing_calls,
                },
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
                metadata={
                    "source": source_label,
                    "error_counts": dict(error_counts),
                    "source_error_task_ids": {
                        key: sorted(task_ids) for key, task_ids in sorted(error_task_ids.items())
                    },
                    "source_param_task_ids": {
                        key: sorted(task_ids) for key, task_ids in sorted(wrong_param_task_ids.items())
                    },
                    "source_task_ids": sorted(
                        {task_id for task_ids in error_task_ids.values() for task_id in task_ids}
                    ),
                    "source_train_total_runs": len(results),
                    "source_train_positive_runs": len(positive_results),
                    "domains": ["all"],
                    "injection_type": "workflow",
                    "intent_keywords": ["schema", "argument", "literal", "tool", "call"],
                },
            )
        )
    # Deduplicate by name while preserving the most recent metadata/body.
    dedup: Dict[str, SkillArtifact] = {}
    for artifact in artifacts:
        dedup[artifact.name] = artifact
    return list(dedup.values())


def write_bfcl_handwritten_skills(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([skill.as_dict() for skill in HANDWRITTEN_BFCL_SKILLS], ensure_ascii=False, indent=2)
    )
    return path


def _tool_context_by_name(tool_schemas: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    context: Dict[str, Dict[str, Any]] = {}
    for item in tool_schemas:
        function = item.get("function", {}) if isinstance(item, dict) else {}
        name = str(function.get("name", "")).strip()
        if not name:
            continue
        context[name] = {
            "description": str(function.get("description", "")).strip(),
            "source_file": str(function.get("x_bfcl_source_file", "")).strip(),
            "parameter_names": sorted(
                {
                    str(key).strip()
                    for key in ((function.get("parameters") or {}).get("properties") or {})
                    if str(key).strip()
                }
            ),
        }
    return context


def _tool_domains_from_context(context: Dict[str, Any]) -> List[str]:
    source_file = str(context.get("source_file", "")).strip()
    if not source_file:
        return []
    domain = BFCL_CLASS_FILE_BY_DOC.get(source_file, "")
    return [domain] if domain else []


def _tool_intent_keywords(tool_name: str, context: Dict[str, Any]) -> List[str]:
    mapping = {
        "create_ticket": ["ticket", "support", "issue", "help", "priority"],
        "edit_ticket": ["ticket", "update", "edit", "change"],
        "contact_customer_support": ["support", "assist", "help", "issue"],
        "retrieve_invoice": ["invoice", "receipt", "booking"],
        "purchase_insurance": ["insurance", "coverage"],
        "book_flight": ["book", "flight", "travel"],
        "post_tweet": ["tweet", "post", "twitter"],
        "send_message": ["message", "send", "contact"],
    }
    keywords: List[str] = list(mapping.get(tool_name, []))
    description = str(context.get("description", "")).strip()
    for piece in _tool_description_keywords(description):
        if piece not in keywords:
            keywords.append(piece)
    for piece in _identifier_keywords(tool_name):
        if piece not in keywords:
            keywords.append(piece)
    for piece in (context.get("parameter_names") or []):
        lowered = str(piece).strip().lower()
        if lowered and lowered not in {"arg0", "arg1", "arg2"} and lowered not in keywords:
            keywords.append(lowered)
    return keywords[:12]


def _tool_description_keywords(text: str) -> List[str]:
    if "Tool description:" in text:
        text = text.split("Tool description:", 1)[1]
    if "Response schema:" in text:
        text = text.split("Response schema:", 1)[0]
    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "your", "into", "will", "then", "than",
        "when", "where", "have", "has", "had", "are", "was", "were", "been", "being", "use", "using",
        "tool", "function", "call", "api", "object", "value", "values", "given", "current", "based",
        "return", "returns", "get", "set", "create", "update", "make", "perform",
        "belongs", "allows", "users", "various", "aspects", "which", "system", "provides", "core",
        "functionality", "simple", "more", "such", "their", "through",
    }
    words = re.findall(r"[a-zA-Z]+|[\u4e00-\u9fff]+", text.lower())
    out: List[str] = []
    for word in words:
        if len(word) < 2 or word in stopwords:
            continue
        if word not in out:
            out.append(word)
    return out[:10]


def _identifier_keywords(name: str) -> List[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
    pieces = re.findall(r"[a-zA-Z]+|\d+", expanded.lower())
    return [piece for piece in pieces if piece not in {"get", "set", "api"}]


def _drop_positional_name_artifacts(
    tool_name: str,
    missing_map: Dict[str, Any],
    unexpected_map: Dict[str, Any],
    tool_context: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    context = tool_context.get(tool_name, {})
    schema_params = {
        str(item).strip()
        for item in (context.get("parameter_names") or [])
        if str(item).strip()
    }
    if not schema_params:
        return missing_map, unexpected_map
    placeholder_missing = {
        key: value
        for key, value in missing_map.items()
        if re.fullmatch(r"arg\d+", str(key)) and str(key) not in schema_params
    }
    schema_named_unexpected = {
        key: value
        for key, value in unexpected_map.items()
        if str(key) in schema_params
    }
    if not placeholder_missing or not schema_named_unexpected:
        return missing_map, unexpected_map
    if len(placeholder_missing) != len(schema_named_unexpected):
        return missing_map, unexpected_map
    placeholder_vals = sorted(_stable_json(value) for value in placeholder_missing.values())
    named_vals = sorted(_stable_json(value) for value in schema_named_unexpected.values())
    if placeholder_vals != named_vals:
        return missing_map, unexpected_map
    cleaned_missing = {key: value for key, value in missing_map.items() if key not in placeholder_missing}
    cleaned_unexpected = {key: value for key, value in unexpected_map.items() if key not in schema_named_unexpected}
    return cleaned_missing, cleaned_unexpected


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(value)


def _guess_domains_from_tool_name(tool_name: str) -> List[str]:
    pairs: List[Tuple[str, List[str]]] = [
        ("ticket", ["TicketAPI"]),
        ("flight", ["TravelAPI"]),
        ("insurance", ["TravelAPI"]),
        ("invoice", ["TravelAPI"]),
        ("tweet", ["TwitterAPI"]),
        ("message", ["MessageAPI"]),
        ("watchlist", ["TradingBot"]),
        ("stock", ["TradingBot"]),
        ("order", ["TradingBot"]),
        ("engine", ["VehicleControlAPI"]),
        ("brake", ["VehicleControlAPI"]),
        ("fuel", ["VehicleControlAPI"]),
        ("door", ["VehicleControlAPI"]),
        ("cd", ["GorillaFileSystem"]),
        ("ls", ["GorillaFileSystem"]),
        ("pwd", ["GorillaFileSystem"]),
        ("tail", ["GorillaFileSystem"]),
        ("cat", ["GorillaFileSystem"]),
        ("mv", ["GorillaFileSystem"]),
        ("echo", ["GorillaFileSystem"]),
    ]
    lowered = tool_name.lower()
    domains: List[str] = []
    for token, group in pairs:
        if token in lowered:
            for item in group:
                if item not in domains:
                    domains.append(item)
    return domains


def _tools_with_argument_errors(result: BenchmarkResult) -> Set[str]:
    bad: Set[str] = set()
    for error in (result.metrics or {}).get("call_errors", []) or []:
        if str(error.get("type", "")).strip() != "argument_mismatch":
            continue
        name = str(error.get("name", "")).strip()
        if name:
            bad.add(name)
    return bad
