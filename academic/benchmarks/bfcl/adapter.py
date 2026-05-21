"""BFCL-v3 multi-turn adapter.

This module intentionally separates the BFCL scaffold from the math-oriented
academic executor.  It uses native tool calls, can execute them with the BFCL
official backend when available, and reports both lightweight call-F1 metrics
and an official-style state/response validity check.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from academic.benchmarks.bfcl.call_utils import (
    MATH_FUNCS as _MATH_FUNCS,
    canonical_tool_name as _canonical_tool_name,
    call_to_source as _call_to_source,
    ensure_bfcl_eval_importable as _ensure_bfcl_eval_importable,
    expected_tool_names_for_turn as _expected_tool_names_for_turn,
    first_number as _first_number,
    json_args as _json_args,
    jsonable as _jsonable,
    maybe_json as _maybe_json,
    parse_call as _parse_call,
    safe_model_stem as _safe_model_stem,
    task_to_official_entry as _task_to_official_entry,
)
from academic.benchmarks.bfcl.constants import (
    BFCL_CLASS_FILE_BY_DOC,
    BFCL_OFFICIAL_SYSTEM,
    BFCL_SYSTEM,
    CLASS_DOC_FILES,
    OFFICIAL_TURN_INSTRUCTION,
    TURN_INSTRUCTION,
    USE_SKILL_TOOL,
)
from academic.benchmarks.bfcl.environments import BFCLLocalEnvironment, BFCLOfficialEnvironment
from academic.benchmarks.bfcl.loader import (
    filter_bfcl_tools_by_class,
    filter_bfcl_tools_for_task,
    load_bfcl_tasks,
    load_bfcl_tools,
    make_bfcl_tools_for_task,
    strip_tool_metadata as _strip_tool_metadata,
)
from academic.benchmarks.bfcl.models import BFCLToolCall, BFCLTrace
from academic.benchmarks.bfcl.retrieval import (
    bfcl_retrieval_context as _bfcl_retrieval_context,
    bfcl_low_trust_counts_toward_task_limit as _bfcl_low_trust_counts_toward_task_limit,
    bfcl_low_trust_skill_matches_turn as _bfcl_low_trust_skill_matches_turn,
    bfcl_skill_matches_task as _bfcl_skill_matches_task,
    bfcl_skill_task_filter_reason as _bfcl_skill_task_filter_reason,
    bfcl_skill_matches_turn as _bfcl_skill_matches_turn,
    bfcl_skill_rerank_key as _bfcl_skill_rerank_key,
    error_aware_skill_query as _error_aware_skill_query,
    is_engine_start_brake_skill as _is_engine_start_brake_skill,
    low_trust_turn_match_reason as _low_trust_turn_match_reason,
    query_requests_engine_start as _query_requests_engine_start,
    task_query_text as _task_query_text,
    turn_query_text as _turn_query_text,
)
from academic.benchmarks.bfcl.scoring import score_bfcl_calls, score_bfcl_official
from academic.benchmarks.bfcl.tool_clients import (
    ToolApiClient as _ToolApiClient,
    ToolModelResponse as _ToolModelResponse,
    _effective_llm_timeout,
    make_tool_api_client as _make_tool_api_client,
    resolve_tool_api_style as _resolve_tool_api_style,
)
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.cost_accounting import make_cost_event
from academic.benchmarks.core.skill_injector import BudgetSkillInjector, compact_skill_prompt_block, render_skill_prompt_blocks, select_skill_context_with_llm
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact
from academic.skill_repository.debug_events import DebugEventSink, skill_store_snapshot


ExternalSkillPromptProvider = Any


def _bfcl_trace_detail_level() -> str:
    level = os.environ.get("BFCL_TRACE_DETAIL_LEVEL", "full").strip().lower()
    if level in {"full", "compact", "memory_compact"}:
        return level
    return "full"


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _compact_debug_event_payload(value: Any, *, text_limit: int = 800) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _clip_text(value, text_limit)
    if isinstance(value, list):
        capped = value[:8]
        compact = [_compact_debug_event_payload(item, text_limit=text_limit) for item in capped]
        if len(value) > len(capped):
            compact.append({"_truncated_items": len(value) - len(capped)})
        return compact
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str in {"messages", "user_messages"} and isinstance(item, list):
                clipped = []
                for msg in item[-4:]:
                    if isinstance(msg, dict):
                        clipped.append(
                            {
                                "role": msg.get("role"),
                                "content": _clip_text(msg.get("content"), text_limit),
                                **({"tool_call_id": msg.get("tool_call_id")} if msg.get("tool_call_id") else {}),
                            }
                        )
                    else:
                        clipped.append(_clip_text(msg, text_limit))
                if len(item) > len(clipped):
                    clipped.insert(0, {"_truncated_messages": len(item) - len(clipped)})
                out[key_str] = clipped
                continue
            if key_str == "system":
                out[key_str] = _clip_text(item, text_limit)
                continue
            out[key_str] = _compact_debug_event_payload(item, text_limit=text_limit)
        return out
    return _clip_text(value, text_limit)


def _compact_debug_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compacted: List[Dict[str, Any]] = []
    for event in events:
        compacted.append(_compact_debug_event_payload(event))
    return compacted


def _compact_trace_for_level(trace: BFCLTrace, level: str) -> None:
    if level == "full":
        return
    if level in {"compact", "memory_compact"}:
        trace.messages = []
        compact_turns: List[Dict[str, Any]] = []
        for turn in trace.turns:
            compact_turn: Dict[str, Any] = {
                "turn_index": turn.get("turn_index"),
                "user_messages": [
                    {
                        "role": msg.get("role"),
                        "content": _clip_text(msg.get("content"), 600),
                    }
                    for msg in (turn.get("user_messages") or [])
                    if isinstance(msg, dict)
                ],
                "tool_calls": list(turn.get("tool_calls") or []),
            }
            if turn.get("early_stop_reason"):
                compact_turn["early_stop_reason"] = turn.get("early_stop_reason")
            compact_turns.append(compact_turn)
        trace.turns = compact_turns
        trace.debug_events = _compact_debug_events(trace.debug_events)


def _skill_tool_name(skill: SkillArtifact) -> str:
    return f"skill__{skill.name}"


def _skill_tool_alias(index: int) -> str:
    return f"skill_{index + 1:03d}"


def _build_skill_tool_aliases(skills: List[SkillArtifact]) -> Dict[str, str]:
    return {skill.name: _skill_tool_alias(idx) for idx, skill in enumerate(skills)}


def _build_skill_tools_by_name(
    skills: List[SkillArtifact],
    aliases_by_skill_name: Dict[str, str],
) -> Dict[str, SkillArtifact]:
    out: Dict[str, SkillArtifact] = {}
    for skill in skills:
        alias = aliases_by_skill_name.get(skill.name)
        if alias:
            out[alias] = skill
        out[_skill_tool_name(skill)] = skill
    return out


def _skill_tool_schemas(
    skills: List[SkillArtifact],
    aliases_by_skill_name: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    schemas: List[Dict[str, Any]] = []
    aliases_by_skill_name = aliases_by_skill_name or {}
    for idx, skill in enumerate(skills):
        params = _skill_invocation_parameters(skill)
        tool_name = aliases_by_skill_name.get(skill.name) or _skill_tool_alias(idx)
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": _skill_tool_description(skill),
                    "parameters": params,
                },
            }
        )
    return schemas


def _skill_tool_description(skill: SkillArtifact) -> str:
    steps = _bfcl_function_steps(skill)
    if steps:
        return (
            f"{skill.description[:650]} This is an executable composite skill: "
            "calling it expands into raw BFCL domain tool calls. Use only when "
            "the current user turn exactly matches the skill contract."
        )[:900]
    return skill.description[:900]


def _skill_invocation_parameters(skill: SkillArtifact) -> Dict[str, Any]:
    contract = dict(skill.interface.invocation_contract or {})
    params = contract.get("parameters") or contract.get("input_schema")
    if isinstance(params, dict) and params.get("type") == "object":
        return params
    input_contract = skill.interface.input_contract
    if isinstance(input_contract, dict) and input_contract.get("type") == "object":
        return input_contract
    return {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Optional reason for consulting this skill.",
            }
        },
        "required": [],
    }


def _split_skills_for_injection(
    skills: List[SkillArtifact],
    mode: str,
) -> Tuple[List[SkillArtifact], List[SkillArtifact]]:
    if mode == "none":
        return [], []
    if mode == "prompt_only":
        prompt = [skill for skill in skills if skill.injection_type() in {"informational", "workflow"}]
        return prompt, []
    if mode == "tool_only":
        return [], [skill for skill in skills if skill.injection_type() == "functional"]
    if mode == "hybrid":
        prompt = [skill for skill in skills if skill.injection_type() in {"informational", "workflow"}]
        tool = [skill for skill in skills if skill.injection_type() == "functional"]
        return prompt, tool
    raise ValueError(f"Unknown skill_injection_mode: {mode}")


def _turn_query_text(user_messages: List[Dict[str, Any]]) -> str:
    return "\n".join(str(msg.get("content", "")) for msg in user_messages)


def _skill_brief(skill: SkillArtifact) -> Dict[str, Any]:
    return {
        "name": skill.name,
        "version": skill.version,
        "version_kind": skill.version_kind(),
        "kind": skill.kind,
        "status": skill.status,
        "stale": bool(skill.stale),
        "description": skill.description,
        "dependencies": list(skill.dependencies or []),
        "injection_type": skill.injection_type(),
    }


def _retrieved_from_audit(store: ArtifactStore, audit: Dict[str, Any]) -> List[SkillArtifact]:
    out: List[SkillArtifact] = []
    for item in audit.get("selected") or []:
        skill = store.get(str(item.get("name") or ""))
        if skill is not None:
            out.append(skill)
    return out


@dataclass
class _PromptFrame:
    system: str
    turn_instruction: str


@dataclass
class _RetrievalObservation:
    """Runtime feedback available before the next model step retrieval."""

    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    result: Any = None

    def is_actionable_error(self) -> bool:
        if not self.error:
            return False
        lowered = str(self.error).lower()
        markers = [
            "schema",
            "argument",
            "parameter",
            "missing",
            "required",
            "type",
            "invalid",
            "not found",
            "state",
            "permission",
            "unexpected",
            "bad arguments",
        ]
        return any(marker in lowered for marker in markers)


@dataclass
class _InjectedSkillSet:
    retrieved: List[SkillArtifact] = field(default_factory=list)
    prompt: List[SkillArtifact] = field(default_factory=list)
    tools: List[SkillArtifact] = field(default_factory=list)
    audit_events: List[Dict[str, Any]] = field(default_factory=list)

    def prompt_names(self) -> set[str]:
        return {skill.name for skill in self.prompt}

    def retrieved_names(self) -> set[str]:
        return {skill.name for skill in self.retrieved}


class _PromptPolicy:
    def build(self, *, skill_prompt: str, state_summary: str, has_turn_skills: bool) -> _PromptFrame:
        raise NotImplementedError


class _NativePromptPolicy(_PromptPolicy):
    def build(self, *, skill_prompt: str, state_summary: str, has_turn_skills: bool) -> _PromptFrame:
        return _PromptFrame(
            system=_native_skill_system(skill_prompt) if has_turn_skills else "",
            turn_instruction="",
        )


class _OfficialPromptPolicy(_PromptPolicy):
    def build(self, *, skill_prompt: str, state_summary: str, has_turn_skills: bool) -> _PromptFrame:
        return _PromptFrame(
            system=BFCL_OFFICIAL_SYSTEM.format(skills=skill_prompt),
            turn_instruction=OFFICIAL_TURN_INSTRUCTION,
        )


class _AcademicPromptPolicy(_PromptPolicy):
    def build(self, *, skill_prompt: str, state_summary: str, has_turn_skills: bool) -> _PromptFrame:
        return _PromptFrame(
            system=BFCL_SYSTEM.format(skills=skill_prompt, state_summary=state_summary),
            turn_instruction=TURN_INSTRUCTION,
        )


def _prompt_policy(prompt_style: str) -> _PromptPolicy:
    policies: Dict[str, _PromptPolicy] = {
        "native": _NativePromptPolicy(),
        "official": _OfficialPromptPolicy(),
        "academic": _AcademicPromptPolicy(),
    }
    try:
        return policies[prompt_style]
    except KeyError as exc:
        raise ValueError(f"Unknown BFCL prompt_style: {prompt_style}") from exc


@dataclass
class _RetrievalPolicy:
    store: ArtifactStore | None
    task: BenchmarkTask
    top_k_skills: int
    min_skill_score: float
    allow_trial_skills: bool = True
    allow_candidate_skills: bool = True
    trial_supplement_k: int = 0
    include_pending_supplement: bool = False
    low_trust_total_limit: int = 1
    low_trust_limited_selected_names: set[str] = field(default_factory=set)

    def retrieve(
        self,
        *,
        turn_index: int,
        user_messages: List[Dict[str, Any]],
        observation: _RetrievalObservation | None = None,
    ) -> Tuple[List[SkillArtifact], Dict[str, Any], str]:
        if self.store is None:
            return [], {}, ""
        phase = "turn_start"
        query = _turn_query_text(user_messages)
        tool_name: str | None = None
        predicate_reasons: Dict[str, str] = {}
        if observation is not None and observation.is_actionable_error():
            phase = "previous_observation"
            tool_name = observation.tool_name or None
            query = _error_aware_skill_query(
                task=self.task,
                turn_index=turn_index,
                user_messages=user_messages,
                tool_name=observation.tool_name,
                args=observation.arguments,
                error=observation.error,
            )

        def _is_trial_artifact(artifact: SkillArtifact) -> bool:
            metadata = artifact.metadata or {}
            return str(artifact.status or "").strip().lower() == "trial" or (
                bool(metadata.get("candidate_group_id"))
                and str(metadata.get("promotion_state") or "").strip().lower() == "trial"
            )

        def _is_candidate_artifact(artifact: SkillArtifact) -> bool:
            metadata = artifact.metadata or {}
            return (
                "__candidate_" in str(artifact.name or "")
                or bool(metadata.get("candidate_group_id"))
                or bool(metadata.get("candidate_for_existing_skill"))
                or bool(metadata.get("candidate_original_name"))
                or bool(metadata.get("candidate_group_role"))
            )

        def _is_pending_artifact(artifact: SkillArtifact) -> bool:
            metadata = artifact.metadata or {}
            return str(artifact.status or "").strip().lower() == "pending" or bool(metadata.get("is_pending_skill"))

        def _candidate_failed_promotion_gate(artifact: SkillArtifact) -> bool:
            state = str((artifact.metadata or {}).get("promotion_state") or "").strip().lower()
            return state in {"winner_below_promotion_threshold", "competition_loser", "revoked"}

        def _with_low_trust_hint(artifact: SkillArtifact, *, reason: str) -> SkillArtifact:
            cloned = copy.deepcopy(artifact)
            cloned.metadata = dict(cloned.metadata or {})
            cloned.metadata["executor_low_trust_hint"] = True
            cloned.metadata["executor_low_trust_reason"] = reason
            return cloned

        def _predicate(artifact: SkillArtifact) -> bool:
            if (
                not self.allow_trial_skills
                and _is_trial_artifact(artifact)
            ):
                predicate_reasons[artifact.name] = "trial_skill_disabled_for_phase"
                return False
            if not self.allow_candidate_skills and _is_candidate_artifact(artifact):
                predicate_reasons[artifact.name] = "candidate_skill_disabled_for_phase"
                return False
            reason = _bfcl_skill_task_filter_reason(artifact, self.task)
            if reason:
                predicate_reasons[artifact.name] = reason
                return False
            passed = _bfcl_skill_matches_turn(artifact, self.task, turn_index, user_messages)
            if not passed:
                predicate_reasons[artifact.name] = "turn_scope_mismatch"
            return passed

        def _trusted_predicate(artifact: SkillArtifact) -> bool:
            if _is_trial_artifact(artifact) or _is_pending_artifact(artifact):
                predicate_reasons[artifact.name] = "low_trust_skill_reserved_for_supplement"
                return False
            return _predicate(artifact)

        audit = self.store.retrieve_audit(
            query,
            top_k=self.top_k_skills,
            min_score=self.min_skill_score,
            predicate=_trusted_predicate if self.trial_supplement_k > 0 else _predicate,
            rerank_key=lambda artifact: _bfcl_skill_rerank_key(
                artifact,
                self.task,
                turn_index,
                user_messages,
            ),
            debug_context=_bfcl_retrieval_context(
                self.task,
                phase=phase,
                turn_index=turn_index,
                tool_name=tool_name,
            ),
        )
        if predicate_reasons:
            for row in audit.get("candidates") or []:
                reason = predicate_reasons.get(str(row.get("name") or ""))
                if reason and row.get("filter_reason") == "predicate_false":
                    row["filter_reason"] = reason
        retrieved = _retrieved_from_audit(self.store, audit)
        if self.trial_supplement_k <= 0:
            return retrieved, audit, query

        supplement_reasons: Dict[str, str] = {}

        def _supplement_predicate(artifact: SkillArtifact) -> bool:
            is_trial = _is_trial_artifact(artifact)
            is_pending = _is_pending_artifact(artifact)
            if not is_trial and not (self.include_pending_supplement and is_pending):
                supplement_reasons[artifact.name] = "not_trial_or_pending_supplement"
                return False
            if not self.allow_candidate_skills and _is_candidate_artifact(artifact):
                supplement_reasons[artifact.name] = "candidate_skill_disabled_for_phase"
                return False
            if _candidate_failed_promotion_gate(artifact):
                supplement_reasons[artifact.name] = "candidate_failed_promotion_gate"
                return False
            reason = _bfcl_skill_task_filter_reason(artifact, self.task)
            if reason:
                supplement_reasons[artifact.name] = reason
                return False
            low_trust_reason = _low_trust_turn_match_reason(artifact, self.task, turn_index, user_messages)
            if low_trust_reason:
                supplement_reasons[artifact.name] = low_trust_reason
                return False
            consumes_task_limit = _bfcl_low_trust_counts_toward_task_limit(artifact)
            if (
                consumes_task_limit
                and self.low_trust_total_limit > 0
                and len(self.low_trust_limited_selected_names) >= self.low_trust_total_limit
            ):
                supplement_reasons[artifact.name] = "low_trust_task_total_limit"
                return False
            return True

        supplement_audit = self.store.retrieve_audit(
            query,
            top_k=self.trial_supplement_k,
            min_score=self.min_skill_score,
            predicate=_supplement_predicate,
            rerank_key=lambda artifact: _bfcl_skill_rerank_key(
                artifact,
                self.task,
                turn_index,
                user_messages,
            ),
            debug_context={
                **_bfcl_retrieval_context(
                    self.task,
                    phase=f"{phase}_low_trust_supplement",
                    turn_index=turn_index,
                    tool_name=tool_name,
                ),
                "low_trust_supplement": True,
                "include_pending_supplement": bool(self.include_pending_supplement),
            },
            include_pending=bool(self.include_pending_supplement),
        )
        if supplement_reasons:
            for row in supplement_audit.get("candidates") or []:
                reason = supplement_reasons.get(str(row.get("name") or ""))
                if reason and row.get("filter_reason") == "predicate_false":
                    row["filter_reason"] = reason
        existing_names = {skill.name for skill in retrieved}
        supplement_skills = [
            _with_low_trust_hint(skill, reason="trial_or_pending_supplement")
            for skill in _retrieved_from_audit(self.store, supplement_audit)
            if skill.name not in existing_names
        ]
        engine_start_rescue_enabled = os.environ.get("BFCL_ENABLE_ENGINE_START_BRAKE_RESCUE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if (
            engine_start_rescue_enabled
            and _query_requests_engine_start(query)
            and not any(_is_engine_start_brake_skill(skill) for skill in supplement_skills)
        ):
            for candidate in self.store.all():
                if candidate.name in existing_names or any(skill.name == candidate.name for skill in supplement_skills):
                    continue
                is_trial = _is_trial_artifact(candidate)
                is_pending = _is_pending_artifact(candidate)
                if not is_trial and not (self.include_pending_supplement and is_pending):
                    continue
                if not self.allow_candidate_skills and _is_candidate_artifact(candidate):
                    continue
                if _candidate_failed_promotion_gate(candidate):
                    continue
                if _bfcl_skill_task_filter_reason(candidate, self.task):
                    continue
                if _low_trust_turn_match_reason(candidate, self.task, turn_index, user_messages):
                    continue
                if not _is_engine_start_brake_skill(candidate):
                    continue
                supplement_skills.append(_with_low_trust_hint(candidate, reason="explicit_engine_start_brake_rescue"))
                supplement_audit.setdefault("deterministic_rescue", []).append(
                    {
                        "name": candidate.name,
                        "reason": "explicit_engine_start_brake_rescue",
                    }
                )
                break
        for skill in supplement_skills:
            if _bfcl_low_trust_counts_toward_task_limit(skill):
                self.low_trust_limited_selected_names.add(skill.name)
        retrieved.extend(supplement_skills)
        audit["low_trust_supplement"] = supplement_audit
        audit["selected_with_supplement"] = [
            *list(audit.get("selected") or []),
            *[
                {
                    "name": skill.name,
                    "status": skill.status,
                    "low_trust_hint": True,
                    "reason": "trial_or_pending_supplement",
                }
                for skill in supplement_skills
            ],
        ]
        return retrieved, audit, query

    def retrieve_for_turn(
        self,
        *,
        turn_index: int,
        user_messages: List[Dict[str, Any]],
    ) -> Tuple[List[SkillArtifact], Dict[str, Any]]:
        skills, audit, _query = self.retrieve(turn_index=turn_index, user_messages=user_messages)
        return skills, audit


class _SkillInjectionPolicy:
    def __init__(
        self,
        *,
        mode: str,
        llm_config: str,
        model_name: str | None,
        presentation_mode: str,
        gate_mode: str,
        budget_chars: int,
        compact_chars_per_skill: int,
        task_id: str,
    ) -> None:
        self.mode = mode
        self.llm_config = llm_config
        self.model_name = model_name
        self.presentation_mode = presentation_mode
        self.gate_mode = gate_mode
        self.budget_chars = budget_chars
        self.compact_chars_per_skill = compact_chars_per_skill
        self.task_id = task_id
        self.deterministic_injector = BudgetSkillInjector(
            mode=presentation_mode,
            max_full_skills=int(os.environ.get("BFCL_SKILL_INJECTOR_MAX_FULL_SKILLS", os.environ.get("SKILL_INJECTOR_MAX_FULL_SKILLS", "0")) or "0"),
            max_summary_skills=int(os.environ.get("BFCL_SKILL_INJECTOR_MAX_SUMMARY_SKILLS", os.environ.get("SKILL_INJECTOR_MAX_SUMMARY_SKILLS", "1")) or "1"),
            budget_chars=budget_chars,
            compact_chars_per_skill=compact_chars_per_skill,
        )

    def _deterministic_selection(
        self,
        prompt_candidates: List[SkillArtifact],
        *,
        query: str,
    ) -> Tuple[List[SkillArtifact], Dict[str, Any]]:
        if self.presentation_mode in {"", "full"}:
            event = {
                "mode": "full",
                "budget_chars": 0,
                "llm_config": "",
                "model_name": "",
                "injected": [
                    {
                        "skill_name": skill.name,
                        "decision": "inject_full",
                        "reason": "legacy_full_prompt_injection",
                        "prompt_chars": len(skill.prompt_block()),
                        "injection_type": skill.injection_type(),
                        "kind": skill.kind,
                    }
                    for skill in prompt_candidates
                    if skill.retrieval_enabled()
                ],
                "filtered": [
                    {"skill_name": skill.name, "reason": "retrieval_disabled"}
                    for skill in prompt_candidates
                    if not skill.retrieval_enabled()
                ],
                "injected_count": sum(1 for skill in prompt_candidates if skill.retrieval_enabled()),
                "filtered_count": sum(1 for skill in prompt_candidates if not skill.retrieval_enabled()),
                "prompt_chars": sum(len(skill.prompt_block()) for skill in prompt_candidates if skill.retrieval_enabled()),
                "gate": "deterministic",
            }
            return [skill for skill in prompt_candidates if skill.retrieval_enabled()], event
        injection = self.deterministic_injector.select(
            prompt_candidates,
            query=query,
            allowed_injection_types={"informational", "workflow"},
        )
        event = injection.as_event()
        event["gate"] = "deterministic"
        return injection.artifacts, event

    async def initial_selection(
        self,
        retrieved_by_turn: List[List[SkillArtifact]],
        *,
        turn_queries: List[str] | None = None,
    ) -> Tuple[List[List[SkillArtifact]], List[SkillArtifact], List[Dict[str, Any]]]:
        prompt_skills_by_turn: List[List[SkillArtifact]] = []
        tool_skills: List[SkillArtifact] = []
        seen_tool_skills: set[str] = set()
        injector_events: List[Dict[str, Any]] = []
        for idx, turn_skills in enumerate(retrieved_by_turn):
            prompt_turn, tool_turn = _split_skills_for_injection(turn_skills, self.mode)
            prompt_candidates = list(prompt_turn)
            query = (turn_queries or [""])[idx] if idx < len(turn_queries or []) else ""
            if self.gate_mode == "llm":
                injection = await select_skill_context_with_llm(
                    prompt_candidates,
                    query=query,
                    llm_config=self.llm_config,
                    model_name=self.model_name,
                    presentation_mode=self.presentation_mode,
                    allowed_injection_types={"informational", "workflow"},
                    max_selected=len(prompt_candidates),
                    budget_chars=self.budget_chars,
                    compact_chars_per_skill=self.compact_chars_per_skill,
                    benchmark="bfcl_v3",
                    task_id=self.task_id,
                    phase="executor",
                    metadata={"turn_index": idx},
                )
                prompt_turn = injection.artifacts
                event = injection.as_event()
                event["gate"] = "llm"
            else:
                prompt_turn, event = self._deterministic_selection(
                    prompt_candidates,
                    query=query,
                )
            prompt_turn = self._rescue_explicit_bfcl_subtask_skills(
                selected=prompt_turn,
                candidates=prompt_candidates,
                query=query,
                event=event,
            )
            event["turn_index"] = idx
            injector_events.append(event)
            prompt_skills_by_turn.append(prompt_turn)
            for skill in tool_turn:
                if skill.name not in seen_tool_skills:
                    seen_tool_skills.add(skill.name)
                    tool_skills.append(skill)
        return prompt_skills_by_turn, tool_skills, injector_events

    def _rescue_explicit_bfcl_subtask_skills(
        self,
        *,
        selected: List[SkillArtifact],
        candidates: List[SkillArtifact],
        query: str,
        event: Dict[str, Any],
    ) -> List[SkillArtifact]:
        if not candidates:
            return selected
        if os.environ.get("BFCL_ENABLE_ENGINE_START_BRAKE_RESCUE", "").strip().lower() not in {"1", "true", "yes"}:
            return selected
        if not _query_requests_engine_start(query):
            return selected
        selected_names = {skill.name for skill in selected}
        rescued = list(selected)
        filtered = list(event.get("filtered") or [])
        rescued_events: List[Dict[str, Any]] = []
        for skill in candidates:
            if skill.name in selected_names:
                continue
            if not _is_engine_start_brake_skill(skill):
                continue
            rescued.append(skill)
            selected_names.add(skill.name)
            rescued_events.append(
                {
                    "skill_name": skill.name,
                    "decision": "deterministic_rescue",
                    "reason": "explicit_engine_start_subtask_requires_pressBrakePedal_before_startEngine",
                    "prompt_chars": len(
                        compact_skill_prompt_block(skill, max_chars=self.compact_chars_per_skill)
                        if self.presentation_mode in {"compact", "budget", "summary"}
                        else skill.prompt_block()
                    ),
                    "injection_type": skill.injection_type(),
                    "kind": skill.kind,
                }
            )
            filtered = [
                item
                for item in filtered
                if str(item.get("skill_name") or "") != skill.name
            ]
        if rescued_events:
            event["injected"] = list(event.get("injected") or []) + rescued_events
            event["filtered"] = filtered
            event["injected_count"] = len(event["injected"])
            event["filtered_count"] = len(filtered)
            event["bfcl_deterministic_rescue"] = rescued_events
        return rescued

    async def merge_prompt_skills(
        self,
        *,
        current: List[SkillArtifact],
        retrieved: List[SkillArtifact],
        query: str = "",
        turn_index: int = 0,
    ) -> Tuple[List[SkillArtifact], List[SkillArtifact], Dict[str, Any]]:
        prompt_skills, _tool_skills = _split_skills_for_injection(retrieved, self.mode)
        existing = {skill.name for skill in current}
        new_prompt_skills = [skill for skill in prompt_skills if skill.name not in existing]
        if not new_prompt_skills:
            return list(current), [], {
                "mode": f"skip:{self.presentation_mode}",
                "budget_chars": self.budget_chars,
                "llm_config": self.llm_config,
                "model_name": self.model_name or "",
                "injected": [],
                "filtered": [
                    {
                        "skill_name": skill.name,
                        "reason": "already_in_prompt_context",
                    }
                    for skill in prompt_skills
                ],
                "injected_count": 0,
                "filtered_count": len(prompt_skills),
                "prompt_chars": 0,
                "turn_index": turn_index,
                "phase": "executor_step_update",
                "gate": "no_new_prompt_skills",
            }
        if self.gate_mode == "llm":
            injection = await select_skill_context_with_llm(
                new_prompt_skills,
                query=query,
                llm_config=self.llm_config,
                model_name=self.model_name,
                presentation_mode=self.presentation_mode,
                allowed_injection_types={"informational", "workflow"},
                max_selected=len(new_prompt_skills),
                budget_chars=self.budget_chars,
                compact_chars_per_skill=self.compact_chars_per_skill,
                benchmark="bfcl_v3",
                task_id=self.task_id,
                phase="executor_step_update",
                metadata={"turn_index": turn_index},
            )
            event = injection.as_event()
            event["gate"] = "llm"
            selected = injection.artifacts
        else:
            selected, event = self._deterministic_selection(new_prompt_skills, query=query)
        selected = self._rescue_explicit_bfcl_subtask_skills(
            selected=selected,
            candidates=new_prompt_skills,
            query=query,
            event=event,
        )
        added: List[SkillArtifact] = []
        merged = list(current)
        for skill in selected:
            if skill.name in existing:
                continue
            existing.add(skill.name)
            merged.append(skill)
            added.append(skill)
        event["turn_index"] = turn_index
        return merged, added, event


def _step_skill_context_message(*, skill_prompt: str, added_skills: List[SkillArtifact]) -> Dict[str, str]:
    names = ", ".join(skill.name for skill in added_skills)
    return {
        "role": "user",
        "content": (
            "Runtime skill retrieval update for this same user turn.\n"
            f"Newly retrieved local rules: {names}.\n"
            "Use these only if they directly match the current tool family, schema, and user intent; "
            "ignore them if they are irrelevant.\n\n"
            f"{skill_prompt}"
        ),
    }


class _TerminationPolicy:
    def __init__(self) -> None:
        self._watchdog = _TurnWatchdog()

    def observe_domain_calls(self, calls: List["BFCLToolCall"]) -> str | None:
        return self._watchdog.observe(calls)


class _ToolCallOutcome:
    def __init__(self, *, observation: _RetrievalObservation | None = None) -> None:
        self.observation = observation


def _bfcl_function_steps(skill: SkillArtifact) -> List[Dict[str, Any]]:
    metadata = skill.metadata or {}
    raw = (
        metadata.get("bfcl_function_steps")
        or metadata.get("function_steps")
        or (skill.interface.invocation_contract or {}).get("bfcl_function_steps")
        or (skill.interface.invocation_contract or {}).get("function_steps")
    )
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _execute_bfcl_function_skill(
    *,
    skill: SkillArtifact,
    invocation_args: Dict[str, Any],
    turn_index: int,
    step_index: int,
    trace: BFCLTrace,
    local_sink: DebugEventSink,
    skill_tools_by_name: Dict[str, SkillArtifact],
    available_tools: List[Dict[str, Any]],
    env: Any,
    depth: int = 0,
    call_stack: List[str] | None = None,
) -> Dict[str, Any]:
    steps = _bfcl_function_steps(skill)
    if not steps:
        return {"executed": False, "reason": "no_bfcl_function_steps"}
    call_stack = list(call_stack or [])
    if depth >= 8 or skill.name in call_stack:
        return {
            "executed": False,
            "reason": "recursive_function_skill_limit",
            "skill_name": skill.name,
            "call_stack": call_stack,
        }
    executed_steps: List[Dict[str, Any]] = []
    for idx, step in enumerate(steps):
        target = str(step.get("tool") or step.get("raw_tool") or step.get("skill") or step.get("name") or "").strip()
        if not target:
            executed_steps.append({"step_index": idx, "executed": False, "reason": "missing_target"})
            continue
        step_args = _resolve_bfcl_step_args(step.get("arguments", step.get("args", {})), invocation_args)
        nested_skill = _resolve_bfcl_step_skill(target, skill_tools_by_name)
        if nested_skill is not None:
            nested = _execute_bfcl_function_skill(
                skill=nested_skill,
                invocation_args=step_args,
                turn_index=turn_index,
                step_index=step_index,
                trace=trace,
                local_sink=local_sink,
                skill_tools_by_name=skill_tools_by_name,
                available_tools=available_tools,
                env=env,
                depth=depth + 1,
                call_stack=[*call_stack, skill.name],
            )
            trace.called_skill_tools.append(nested_skill.name)
            executed_steps.append(
                {
                    "step_index": idx,
                    "target_type": "function_skill",
                    "target": nested_skill.name,
                    "arguments": step_args,
                    "result": nested,
                }
            )
            continue
        canonical = _canonical_tool_name(target, available_tools)
        result, error = env.call(canonical, step_args)
        call = BFCLToolCall(
            name=canonical,
            arguments=step_args,
            turn_index=turn_index,
            tool_call_id=f"skill::{skill.name}::{idx}",
            result=result,
            error=error,
        )
        trace.tool_calls.append(call)
        local_sink.emit(
            "function_skill_raw_tool_call",
            turn_index=turn_index,
            step_index=step_index,
            input={
                "skill_name": skill.name,
                "step_index": idx,
                "raw_target": target,
                "canonical_tool_name": canonical,
                "arguments": step_args,
            },
            output=call.as_dict(),
        )
        executed_steps.append(
            {
                "step_index": idx,
                "target_type": "raw_bfcl_tool",
                "target": canonical,
                "arguments": step_args,
                "error": error,
                "result": result,
            }
        )
    return {
        "executed": True,
        "skill_name": skill.name,
        "raw_tool_calls": [
            step for step in executed_steps if step.get("target_type") == "raw_bfcl_tool"
        ],
        "steps": executed_steps,
    }


def _resolve_bfcl_step_skill(
    target: str,
    skill_tools_by_name: Dict[str, SkillArtifact],
) -> SkillArtifact | None:
    if target in skill_tools_by_name:
        return skill_tools_by_name[target]
    key = f"skill__{target}"
    if key in skill_tools_by_name:
        return skill_tools_by_name[key]
    for skill in skill_tools_by_name.values():
        if skill.name == target:
            return skill
    return None


def _resolve_bfcl_step_args(value: Any, invocation_args: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {str(k): _resolve_bfcl_step_args(v, invocation_args) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_bfcl_step_args(item, invocation_args) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("$") and len(text) > 1:
            return copy.deepcopy(invocation_args.get(text[1:], value))
        if re.fullmatch(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", text):
            return copy.deepcopy(invocation_args.get(text[2:-2], value))
        if re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", text):
            return copy.deepcopy(invocation_args.get(text[1:-1], value))
        def repl(match: re.Match[str]) -> str:
            key = match.group(1) or match.group(2)
            return str(invocation_args.get(key, match.group(0)))
        return re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, value)
    return copy.deepcopy(value)


class _ToolCallHandler:
    def can_handle(self, *, tool_name: str, skill_tools_by_name: Dict[str, SkillArtifact]) -> bool:
        raise NotImplementedError

    def handle(
        self,
        *,
        raw_name: str,
        tool_name: str,
        args: Dict[str, Any],
        tc: Dict[str, Any],
        turn_index: int,
        step_index: int,
        trace: BFCLTrace,
        messages: List[Dict[str, Any]],
        tool_client: _ToolApiClient,
        local_sink: DebugEventSink,
        skill_tools_by_name: Dict[str, SkillArtifact],
        available_tools: List[Dict[str, Any]],
        env: Any,
    ) -> _ToolCallOutcome:
        raise NotImplementedError


class _SkillToolCallHandler(_ToolCallHandler):
    def can_handle(self, *, tool_name: str, skill_tools_by_name: Dict[str, SkillArtifact]) -> bool:
        return tool_name in skill_tools_by_name

    def handle(
        self,
        *,
        raw_name: str,
        tool_name: str,
        args: Dict[str, Any],
        tc: Dict[str, Any],
        turn_index: int,
        step_index: int,
        trace: BFCLTrace,
        messages: List[Dict[str, Any]],
        tool_client: _ToolApiClient,
        local_sink: DebugEventSink,
        skill_tools_by_name: Dict[str, SkillArtifact],
        available_tools: List[Dict[str, Any]],
        env: Any,
    ) -> _ToolCallOutcome:
        skill = skill_tools_by_name[tool_name]
        event = {
            "turn_index": turn_index,
            "tool_call_id": tc["id"],
            "skill_name": skill.name,
            "tool_name": tool_name,
            "reason": str(args.get("question", "")),
            "raw_arguments": args,
        }
        trace.skill_events.append(event)
        trace.called_skill_tools.append(skill.name)
        executed = _execute_bfcl_function_skill(
            skill=skill,
            invocation_args=args,
            turn_index=turn_index,
            step_index=step_index,
            trace=trace,
            local_sink=local_sink,
            skill_tools_by_name=skill_tools_by_name,
            available_tools=available_tools,
            env=env,
        )
        local_sink.emit(
            "skill_tool_call",
            turn_index=turn_index,
            step_index=step_index,
            input=event,
            output={"skill": _skill_brief(skill), "body": skill.body, "executed": executed},
        )
        if executed.get("executed"):
            skill_content = json.dumps(executed, ensure_ascii=False)
        else:
            skill_content = json.dumps(
                {
                    "skill_name": skill.name,
                    "kind": skill.kind,
                    "injection_type": skill.injection_type(),
                    "description": skill.description,
                    "body": skill.body,
                },
                ensure_ascii=False,
            )
        messages.append(tool_client.tool_result_message(tc["id"], skill_content))
        trace.messages.append(messages[-1])
        return _ToolCallOutcome()


class _UseSkillCallHandler(_ToolCallHandler):
    def can_handle(self, *, tool_name: str, skill_tools_by_name: Dict[str, SkillArtifact]) -> bool:
        return tool_name == "use_skill"

    def handle(
        self,
        *,
        raw_name: str,
        tool_name: str,
        args: Dict[str, Any],
        tc: Dict[str, Any],
        turn_index: int,
        step_index: int,
        trace: BFCLTrace,
        messages: List[Dict[str, Any]],
        tool_client: _ToolApiClient,
        local_sink: DebugEventSink,
        skill_tools_by_name: Dict[str, SkillArtifact],
        available_tools: List[Dict[str, Any]],
        env: Any,
    ) -> _ToolCallOutcome:
        event = {
            "turn_index": turn_index,
            "tool_call_id": tc["id"],
            "skill_name": str(args.get("skill_name", "")),
            "reason": str(args.get("reason", "")),
            "raw_arguments": args,
        }
        trace.skill_events.append(event)
        local_sink.emit(
            "skill_use_event",
            turn_index=turn_index,
            step_index=step_index,
            input=event,
        )
        skill_content = json.dumps({"status": "recorded", **event}, ensure_ascii=False)
        messages.append(tool_client.tool_result_message(tc["id"], skill_content))
        trace.messages.append(messages[-1])
        return _ToolCallOutcome()


class _DomainToolCallHandler(_ToolCallHandler):
    def can_handle(self, *, tool_name: str, skill_tools_by_name: Dict[str, SkillArtifact]) -> bool:
        return True

    def handle(
        self,
        *,
        raw_name: str,
        tool_name: str,
        args: Dict[str, Any],
        tc: Dict[str, Any],
        turn_index: int,
        step_index: int,
        trace: BFCLTrace,
        messages: List[Dict[str, Any]],
        tool_client: _ToolApiClient,
        local_sink: DebugEventSink,
        skill_tools_by_name: Dict[str, SkillArtifact],
        available_tools: List[Dict[str, Any]],
        env: Any,
    ) -> _ToolCallOutcome:
        result, error = env.call(tool_name, args)
        observation = _RetrievalObservation(
            tool_name=tool_name,
            arguments=args,
            error=error,
            result=result,
        )
        call = BFCLToolCall(
            name=tool_name,
            arguments=args,
            turn_index=turn_index,
            tool_call_id=tc["id"],
            result=result,
            error=error,
        )
        if raw_name != tool_name:
            call.result = {
                "canonical_tool_name": tool_name,
                "raw_tool_name": raw_name,
                "result": result,
            }
        trace.tool_calls.append(call)
        local_sink.emit(
            "tool_result",
            turn_index=turn_index,
            step_index=step_index,
            output=call.as_dict(),
        )
        tool_content = json.dumps(
            result if error is None else {"error": error},
            ensure_ascii=False,
        )[:3000]
        messages.append(tool_client.tool_result_message(tc["id"], tool_content))
        trace.messages.append(messages[-1])
        return _ToolCallOutcome(observation=observation)


def _tool_call_handlers() -> List[_ToolCallHandler]:
    return [_SkillToolCallHandler(), _UseSkillCallHandler(), _DomainToolCallHandler()]


async def run_bfcl_task(
    task: BenchmarkTask,
    *,
    llm_config: str,
    tools: List[Dict[str, Any]],
    artifact_store: Optional[ArtifactStore] = None,
    top_k_skills: int = 5,
    min_skill_score: float = 0.0,
    max_steps_per_turn: int = 20,
    adapter_mode: str = "official",
    model_name: Optional[str] = None,
    enable_skill_tool: bool = False,
    execution_backend: str = "auto",
    prompt_style: str = "native",
    temperature: Optional[float] = None,
    synthetic_continue: bool = False,
    tool_api_style: str = "auto",
    skill_injection_mode: str = "prompt_only",
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
    debug_sink: DebugEventSink | None = None,
    max_task_seconds: float | None = None,
    external_skill_prompt_provider: ExternalSkillPromptProvider | None = None,
    allow_trial_skills: bool = True,
    allow_candidate_skills: bool = True,
    trial_supplement_k: int = 0,
    include_pending_supplement: bool = False,
) -> BenchmarkResult:
    t0 = time.monotonic()
    resolved_api_style = _resolve_tool_api_style(tool_api_style, llm_config, model_name)
    prompt_policy = _prompt_policy(prompt_style)
    official_env = BFCLOfficialEnvironment(
        task.input_artifacts.get("initial_config", {}),
        task.metadata.get("involved_classes", []),
        task.task_id,
    )
    env = (
        official_env
        if execution_backend in {"auto", "official"} and official_env.available
        else BFCLLocalEnvironment(task.input_artifacts.get("initial_config", {}))
    )
    tools = make_bfcl_tools_for_task(
        tools, task, adapter_mode=adapter_mode, enable_skill_tool=enable_skill_tool
    )
    trace = BFCLTrace(task_id=task.task_id)
    cost_events: List[Dict[str, Any]] = []
    local_sink = debug_sink or DebugEventSink.from_env(
        base_context={"task_id": task.task_id, "component": "bfcl_executor"}
    )
    debug_start = len(local_sink.events)
    local_sink.emit(
        "executor_start",
        input={
            "task_id": task.task_id,
            "n_turns": len(task.question),
            "top_k_skills": top_k_skills,
            "min_skill_score": min_skill_score,
            "skill_injection_mode": skill_injection_mode,
            "allow_trial_skills": bool(allow_trial_skills),
            "allow_candidate_skills": bool(allow_candidate_skills),
            "trial_supplement_k": int(trial_supplement_k or 0),
            "include_pending_supplement": bool(include_pending_supplement),
            "prompt_style": prompt_style,
            "adapter_mode": adapter_mode,
            "execution_backend": execution_backend,
            "tool_api_style": resolved_api_style,
        },
        store_snapshot=skill_store_snapshot(artifact_store) if artifact_store else {"n_total": 0, "skills": []},
    )
    retrieval_policy = _RetrievalPolicy(
        store=artifact_store,
        task=task,
        top_k_skills=top_k_skills,
        min_skill_score=min_skill_score,
        allow_trial_skills=allow_trial_skills,
        allow_candidate_skills=allow_candidate_skills,
        trial_supplement_k=trial_supplement_k,
        include_pending_supplement=include_pending_supplement,
    )
    presentation_mode = (
        skill_injector_mode
        or os.environ.get("BFCL_SKILL_INJECTOR_MODE")
        or os.environ.get("SKILL_INJECTOR_MODE")
        or "full"
    ).strip().lower()
    gate_mode = (
        os.environ.get("BFCL_SKILL_INJECTOR_GATE")
        or os.environ.get("SKILL_INJECTOR_GATE")
        or "deterministic"
    ).strip().lower()
    if gate_mode in {"legacy", "legacy_full", "deterministic", "budget", "none"}:
        gate_mode = "deterministic"
    elif gate_mode not in {"llm"}:
        gate_mode = "llm"
    dynamic_retrieval_policy = (
        os.environ.get("BFCL_DYNAMIC_RETRIEVAL_POLICY")
        or "error_only"
    ).strip().lower()
    if dynamic_retrieval_policy not in {"error_only", "every_step", "off"}:
        dynamic_retrieval_policy = "error_only"
    injector_budget_chars = int(skill_context_budget_chars or os.environ.get("BFCL_SKILL_CONTEXT_BUDGET_CHARS", os.environ.get("SKILL_INJECTOR_BUDGET_CHARS", "1800")) or "1800")
    injector_compact_chars = int(os.environ.get("BFCL_SKILL_COMPACT_CHARS_PER_SKILL", os.environ.get("SKILL_INJECTOR_COMPACT_CHARS_PER_SKILL", "900")) or "900")
    injection_policy = _SkillInjectionPolicy(
        mode=skill_injection_mode,
        llm_config=llm_config,
        model_name=model_name,
        presentation_mode=presentation_mode,
        gate_mode=gate_mode,
        budget_chars=injector_budget_chars,
        compact_chars_per_skill=injector_compact_chars,
        task_id=task.task_id,
    )
    retrieved_by_turn: List[List[SkillArtifact]] = []
    turn_queries: List[str] = []
    for turn_index, user_messages in enumerate(task.question):
        query_text = _turn_query_text(user_messages)
        turn_skills, audit = retrieval_policy.retrieve_for_turn(
            turn_index=turn_index,
            user_messages=user_messages,
        )
        retrieved_by_turn.append(turn_skills)
        turn_queries.append(query_text)
        if audit:
            local_sink.emit(
                "retrieval",
                turn_index=turn_index,
                trigger="turn_start",
                input={"query": query_text, "user_messages": user_messages},
                output=audit,
            )
    retrieved_unique: List[SkillArtifact] = []
    seen_retrieved: set[str] = set()
    for turn_skills in retrieved_by_turn:
        for skill in turn_skills:
            if skill.name not in seen_retrieved:
                seen_retrieved.add(skill.name)
                retrieved_unique.append(skill)
    prompt_skills_by_turn, tool_skills, injector_events = await injection_policy.initial_selection(
        retrieved_by_turn,
        turn_queries=turn_queries,
    )
    dynamic_prompt_skills_by_turn: List[List[SkillArtifact]] = [list(items) for items in prompt_skills_by_turn]
    trace.retrieved_skills = [skill.name for skill in retrieved_unique]
    trace.tool_injected_skills = [skill.name for skill in tool_skills]
    local_sink.emit(
        "initial_skill_selection",
        output={
            "retrieved_unique": [_skill_brief(skill) for skill in retrieved_unique],
            "prompt_skills_by_turn": [[_skill_brief(skill) for skill in items] for items in prompt_skills_by_turn],
            "tool_skills": [_skill_brief(skill) for skill in tool_skills],
            "injector_mode": presentation_mode,
            "injector_gate": gate_mode,
            "injector_events": injector_events,
        },
    )
    for event in injector_events:
        local_sink.emit("skill_injector", output=event)
        cost_events.append(
            make_cost_event(
                role="injector",
                phase="executor",
                benchmark="bfcl_v3",
                task_id=task.task_id,
                model=model_name or "",
                llm_config=llm_config,
                skill_prompt_chars=int(event.get("prompt_chars") or 0),
                metadata={
                    "mode": presentation_mode,
                    "gate": gate_mode,
                    "injected_count": event.get("injected_count"),
                    "filtered_count": event.get("filtered_count"),
                },
            )
        )
    skill_tool_aliases = _build_skill_tool_aliases(tool_skills)
    skill_tools_by_name = _build_skill_tools_by_name(tool_skills, skill_tool_aliases)
    if tool_skills:
        tools = tools + _skill_tool_schemas(tool_skills, skill_tool_aliases)
        local_sink.emit(
            "skill_tool_aliases",
            output={
                "aliases": [
                    {
                        "skill_name": skill.name,
                        "tool_name": skill_tool_aliases.get(skill.name),
                        "legacy_tool_name": _skill_tool_name(skill),
                    }
                    for skill in tool_skills
                ]
            },
        )
    tool_call_handlers = _tool_call_handlers()
    state_summary = (
        _summarize_initial_state(task.input_artifacts.get("initial_config", {}))
        if adapter_mode == "debug_hints"
        else "(hidden; use tool results and user-provided values)"
    )
    turn_instruction = ""
    messages: List[Dict[str, Any]] = []
    tool_client = _make_tool_api_client(
        resolved_api_style=resolved_api_style,
        llm_config=llm_config,
        model_name=model_name,
    )

    def _remaining_task_budget() -> float | None:
        if max_task_seconds is None:
            return None
        elapsed = time.monotonic() - t0
        return max(0.0, float(max_task_seconds) - elapsed)

    try:
        for turn_index, user_messages in enumerate(task.question):
            query_text = turn_queries[turn_index] if turn_index < len(turn_queries) else _turn_query_text(user_messages)
            turn_prompt_skills = dynamic_prompt_skills_by_turn[turn_index] if turn_index < len(dynamic_prompt_skills_by_turn) else []
            for skill in turn_prompt_skills:
                if skill.name not in trace.prompt_injected_skills:
                    trace.prompt_injected_skills.append(skill.name)
            skill_prompt = (
                artifact_store.build_prompt(turn_prompt_skills)
                if gate_mode == "deterministic" and presentation_mode in {"", "full"}
                else render_skill_prompt_blocks(
                    turn_prompt_skills,
                    mode=presentation_mode,
                    budget_chars=injector_budget_chars,
                    compact_chars_per_skill=injector_compact_chars,
                )
                if artifact_store
                else "(none)"
            )
            external_skill_metadata: Dict[str, Any] = {}
            has_external_skill_prompt = False
            if external_skill_prompt_provider is not None:
                external_payload = await external_skill_prompt_provider(
                    task=task,
                    turn_index=turn_index,
                    user_messages=user_messages,
                    query=query_text,
                    tools=tools,
                )
                if external_payload:
                    external_skill_prompt = str(external_payload.get("skill_prompt") or "").strip()
                    external_skill_metadata = dict(external_payload.get("metadata") or {})
                    if external_skill_prompt:
                        has_external_skill_prompt = True
                        skill_prompt = (
                            external_skill_prompt
                            if skill_prompt == "(none)"
                            else f"{skill_prompt}\n\n{external_skill_prompt}"
                        )
                        for name in external_payload.get("retrieved_skill_names") or []:
                            name = str(name or "").strip()
                            if name and name not in trace.retrieved_skills:
                                trace.retrieved_skills.append(name)
                        for name in external_payload.get("prompt_injected_skill_names") or []:
                            name = str(name or "").strip()
                            if name and name not in trace.prompt_injected_skills:
                                trace.prompt_injected_skills.append(name)
            prompt_frame = prompt_policy.build(
                skill_prompt=skill_prompt,
                state_summary=state_summary,
                has_turn_skills=bool(turn_prompt_skills) or has_external_skill_prompt,
            )
            system = prompt_frame.system
            turn_instruction = prompt_frame.turn_instruction
            local_sink.emit(
                "prompt_injection",
                turn_index=turn_index,
                input={
                    "user_messages": user_messages,
                    "prompt_style": prompt_style,
                    "turn_prompt_skills": [_skill_brief(skill) for skill in turn_prompt_skills],
                },
                output={
                    "system": system,
                    "skill_prompt": skill_prompt,
                    "turn_instruction": turn_instruction,
                    "external_skill_metadata": external_skill_metadata,
                },
            )
            steps_this_turn = 0
            for msg in user_messages:
                content = str(msg.get("content", ""))
                if msg.get("role", "user") == "user":
                    content += turn_instruction
                    if adapter_mode == "debug_hints":
                        hints = _expected_tool_names_for_turn(task, turn_index)
                        if hints:
                            content += (
                                "\nLikely required tool names for this turn "
                                f"(tool names only, infer parameters yourself): {', '.join(hints)}."
                            )
                messages.append({"role": msg.get("role", "user"), "content": content})
            turn_constraints = _turn_skill_constraints(turn_prompt_skills, task, turn_index)
            if turn_constraints:
                messages.append({"role": "user", "content": turn_constraints})
                trace.messages.append(messages[-1])
            turn_calls_before = len(trace.tool_calls)
            termination_policy = _TerminationPolicy()
            calls_seen_before_step = turn_calls_before
            watchdog_break_reason: str | None = None
            last_observation: _RetrievalObservation | None = None
            for step in range(max_steps_per_turn):
                steps_this_turn += 1
                trace.n_model_steps += 1
                if step > 0 and artifact_store is not None and turn_index < len(dynamic_prompt_skills_by_turn):
                    should_dynamic_retrieve = (
                        dynamic_retrieval_policy == "every_step"
                        or (
                            dynamic_retrieval_policy == "error_only"
                            and last_observation is not None
                            and last_observation.is_actionable_error()
                        )
                    )
                    if not should_dynamic_retrieve:
                        local_sink.emit(
                            "dynamic_retrieval_skipped",
                            turn_index=turn_index,
                            step_index=step,
                            input={
                                "previous_observation": None
                                if last_observation is None
                                else {
                                    "tool_name": last_observation.tool_name,
                                    "arguments": last_observation.arguments,
                                    "error": last_observation.error,
                                },
                            },
                            output={
                                "reason": "dynamic_retrieval_policy"
                                if dynamic_retrieval_policy == "off"
                                else "no_actionable_previous_tool_error",
                                "policy": dynamic_retrieval_policy,
                            },
                        )
                    else:
                        observed_skills, observed_audit, observed_query = retrieval_policy.retrieve(
                            turn_index=turn_index,
                            user_messages=user_messages,
                            observation=last_observation,
                        )
                        local_sink.emit(
                            "retrieval",
                            turn_index=turn_index,
                            trigger="step_start",
                            input={
                                "query": observed_query,
                                "previous_observation": {
                                    "tool_name": last_observation.tool_name,
                                    "arguments": last_observation.arguments,
                                    "error": last_observation.error,
                                },
                            },
                            output=observed_audit,
                        )
                        merged_prompt, added_prompt, step_injector_event = await injection_policy.merge_prompt_skills(
                            current=dynamic_prompt_skills_by_turn[turn_index],
                            retrieved=observed_skills,
                            query=observed_query,
                            turn_index=turn_index,
                        )
                        local_sink.emit("skill_injector", output=step_injector_event)
                        dynamic_prompt_skills_by_turn[turn_index] = merged_prompt
                        for skill in observed_skills:
                            if skill.name not in trace.retrieved_skills:
                                trace.retrieved_skills.append(skill.name)
                        for skill in added_prompt:
                            if skill.name not in trace.prompt_injected_skills:
                                trace.prompt_injected_skills.append(skill.name)
                        if added_prompt:
                            if gate_mode == "deterministic" and presentation_mode in {"", "full"}:
                                refreshed_prompt = artifact_store.build_prompt(dynamic_prompt_skills_by_turn[turn_index])
                                added_skill_prompt = artifact_store.build_prompt(added_prompt)
                            else:
                                refreshed_prompt = render_skill_prompt_blocks(
                                    dynamic_prompt_skills_by_turn[turn_index],
                                    mode=presentation_mode,
                                    budget_chars=injector_budget_chars,
                                    compact_chars_per_skill=injector_compact_chars,
                                )
                                added_skill_prompt = render_skill_prompt_blocks(
                                    added_prompt,
                                    mode=presentation_mode,
                                    budget_chars=injector_budget_chars,
                                    compact_chars_per_skill=injector_compact_chars,
                                )
                            prompt_frame = prompt_policy.build(
                                skill_prompt=refreshed_prompt,
                                state_summary=state_summary,
                                has_turn_skills=bool(dynamic_prompt_skills_by_turn[turn_index]),
                            )
                            system = prompt_frame.system
                            step_context_msg = _step_skill_context_message(
                                skill_prompt=added_skill_prompt,
                                added_skills=added_prompt,
                            )
                            messages.append(step_context_msg)
                            trace.messages.append(step_context_msg)
                            local_sink.emit(
                                "prompt_context_update",
                                turn_index=turn_index,
                                trigger="step_start_observation",
                                output={
                                    "added_skills": [_skill_brief(skill) for skill in added_prompt],
                                    "refreshed_prompt": refreshed_prompt,
                                    "step_context_message": step_context_msg,
                                    "system": system,
                                },
                            )
                            cost_events.append(
                                make_cost_event(
                                    role="injector",
                                    phase="executor_step_update",
                                    benchmark="bfcl_v3",
                                    task_id=task.task_id,
                                    turn_index=turn_index,
                                    step_index=step,
                                    model=model_name or "",
                                    llm_config=llm_config,
                                    skill_prompt_chars=len(added_skill_prompt),
                                    prompt_chars=len(str(step_context_msg.get("content", ""))),
                                    metadata={
                                        "mode": presentation_mode,
                                        "gate": gate_mode,
                                        "dynamic_retrieval_policy": dynamic_retrieval_policy,
                                        "added_skills": [skill.name for skill in added_prompt],
                                    },
                                )
                            )
                model_response = await tool_client.ask(
                    messages=messages,
                    system=system,
                    tools=tools,
                    temperature=temperature,
                    max_request_wall_s=_remaining_task_budget(),
                )
                if model_response is None:
                    trace.timed_out = True
                    break
                content = model_response.content
                tool_calls = model_response.tool_calls
                assistant_msg = model_response.assistant_msg
                messages.append(assistant_msg)
                trace.messages.append(assistant_msg)
                local_sink.emit(
                    "executor_step",
                    turn_index=turn_index,
                    step_index=step,
                    input={
                        "system": system,
                        "messages": messages[:-1],
                        "available_tool_count": len(tools),
                    },
                    output={
                        "assistant_message": assistant_msg,
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                    metrics={
                        "prompt_tokens_used": tool_client.prompt_tokens,
                        "completion_tokens_used": tool_client.completion_tokens,
                    },
                )
                cost_events.append(
                    make_cost_event(
                        role="executor",
                        phase="task_rollout",
                        benchmark="bfcl_v3",
                        task_id=task.task_id,
                        turn_index=turn_index,
                        step_index=step,
                        model=model_name or "",
                        llm_config=llm_config,
                        input_tokens=int(getattr(model_response, "prompt_tokens", 0) or 0),
                        cache_input_tokens=int(getattr(model_response, "cache_input_tokens", 0) or 0),
                        output_tokens=int(getattr(model_response, "completion_tokens", 0) or 0),
                        prompt_chars=sum(len(str(msg.get("content", ""))) for msg in messages[:-1]),
                        skill_prompt_chars=len(skill_prompt),
                        system_prompt_chars=len(system),
                        tool_schema_chars=len(json.dumps(tools, ensure_ascii=False)),
                        final_conversation_chars=sum(len(str(msg.get("content", ""))) for msg in messages),
                        metadata={
                            "prompt_style": prompt_style,
                            "tool_api_style": resolved_api_style,
                            "available_tool_count": len(tools),
                            "skill_injector_mode": presentation_mode,
                        },
                    )
                )
                if not tool_calls:
                    break
                for tc in tool_calls:
                    raw_name = tc["name"]
                    tool_name = _canonical_tool_name(raw_name, tools)
                    args = _json_args(tc["arguments"])
                    local_sink.emit(
                        "tool_call",
                        turn_index=turn_index,
                        step_index=step,
                        input={"raw_tool_name": raw_name, "canonical_tool_name": tool_name, "arguments": args},
                    )
                    handler = next(
                        item
                        for item in tool_call_handlers
                        if item.can_handle(tool_name=tool_name, skill_tools_by_name=skill_tools_by_name)
                    )
                    outcome = handler.handle(
                        raw_name=raw_name,
                        tool_name=tool_name,
                        args=args,
                        tc=tc,
                        turn_index=turn_index,
                        step_index=step,
                        trace=trace,
                        messages=messages,
                        tool_client=tool_client,
                        local_sink=local_sink,
                        skill_tools_by_name=skill_tools_by_name,
                        available_tools=tools,
                        env=env,
                    )
                    if outcome.observation is not None:
                        last_observation = outcome.observation
                domain_calls_this_step = trace.tool_calls[calls_seen_before_step:]
                calls_seen_before_step = len(trace.tool_calls)
                watchdog_break_reason = termination_policy.observe_domain_calls(domain_calls_this_step)
                if watchdog_break_reason:
                    local_sink.emit(
                        "executor_watchdog_break",
                        turn_index=turn_index,
                        step_index=step,
                        output={
                            "reason": watchdog_break_reason,
                            "n_calls_this_step": len(domain_calls_this_step),
                        },
                    )
                    break
                if synthetic_continue and len(trace.tool_calls) > turn_calls_before and step + 1 < max_steps_per_turn:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Continue only if this user turn still requires more tool calls. "
                                "Otherwise respond briefly with no tool call."
                            ),
                        }
                    )
            turn_record: Dict[str, Any] = {
                "turn_index": turn_index,
                "user_messages": user_messages,
                "tool_calls": [
                    call.as_dict()
                    for call in trace.tool_calls
                    if call.turn_index == turn_index
                ],
            }
            if watchdog_break_reason:
                turn_record["early_stop_reason"] = watchdog_break_reason
            trace.turns.append(turn_record)
            trace.turn_step_counts.append(steps_this_turn)
            local_sink.emit(
                "turn_end",
                turn_index=turn_index,
                metrics={
                    "steps_this_turn": steps_this_turn,
                    "n_tool_calls_this_turn": len([call for call in trace.tool_calls if call.turn_index == turn_index]),
                    "timed_out": trace.timed_out,
                },
            )
            if trace.timed_out:
                break
    except Exception as exc:
        trace.elapsed_s = round(time.monotonic() - t0, 3)
        local_sink.emit(
            "executor_exception",
            error={"type": type(exc).__name__, "message": str(exc)},
            metrics={"elapsed_s": trace.elapsed_s},
        )
        trace.debug_events = local_sink.events[debug_start:]
        _compact_trace_for_level(trace, _bfcl_trace_detail_level())
        trace_dict = trace.as_dict()
        trace_dict["cost_events"] = list(cost_events)
        return BenchmarkResult(
            benchmark="bfcl_v3",
            task_id=task.task_id,
            success=False,
            score=0.0,
            metrics={"exception": type(exc).__name__, "cost_events": list(cost_events)},
            trace=trace_dict,
            error=str(exc),
        )

    trace.elapsed_s = round(time.monotonic() - t0, 3)
    trace.total_tokens = tool_client.total_tokens()
    trace.completion_tokens = tool_client.completion_tokens
    trace_dict_cost_events = list(cost_events)
    score = score_bfcl_calls(trace.tool_calls, task.expected)
    score["total_tokens"] = trace.total_tokens
    score["input_tokens"] = int(getattr(tool_client, "prompt_tokens", 0) or 0)
    score["cache_input_tokens"] = int(getattr(tool_client, "cache_input_tokens", 0) or 0)
    score["completion_tokens"] = trace.completion_tokens
    score["cost_events"] = trace_dict_cost_events
    score["elapsed_s"] = trace.elapsed_s
    score["retrieved_skills"] = trace.retrieved_skills
    score["prompt_injected_skills"] = trace.prompt_injected_skills
    score["tool_injected_skills"] = trace.tool_injected_skills
    score["called_skill_tools"] = trace.called_skill_tools
    score["turn_step_counts"] = trace.turn_step_counts
    score["n_model_steps"] = trace.n_model_steps
    explicit_used = [
        event.get("skill_name", "")
        for event in trace.skill_events
        if event.get("skill_name")
    ]
    score["used_skills"] = sorted(set(explicit_used + trace.called_skill_tools))
    score["skill_events"] = trace.skill_events
    score["adapter_mode"] = adapter_mode
    score["execution_backend"] = env.backend_name
    score["prompt_style"] = prompt_style
    score["temperature"] = temperature
    score["synthetic_continue"] = synthetic_continue
    score["tool_api_style"] = resolved_api_style
    score["skill_injection_mode"] = skill_injection_mode
    score["skill_injector_mode"] = presentation_mode
    score["skill_context_budget_chars"] = (
        int(skill_context_budget_chars)
        if skill_context_budget_chars is not None
        else (
            int(os.environ.get("BFCL_SKILL_CONTEXT_BUDGET_CHARS"))
            if os.environ.get("BFCL_SKILL_CONTEXT_BUDGET_CHARS")
            else None
        )
    )
    if model_name:
        score["model_name"] = model_name
    official_check = score_bfcl_official(trace.tool_calls, task)
    score["official_valid"] = official_check.get("valid")
    score["official_error_type"] = official_check.get("error_type")
    score["official_check"] = official_check
    score["available_tool_count"] = len([
        t
        for t in tools
        if t.get("function", {}).get("name") != "use_skill"
        and not str(t.get("function", {}).get("name", "")).startswith("skill__")
        and not str(t.get("function", {}).get("name", "")).startswith("skill_")
    ])
    score["available_skill_tool_count"] = len(tool_skills)
    local_sink.emit(
        "executor_end",
        output={"score": score, "official_check": official_check},
        metrics={
            "elapsed_s": trace.elapsed_s,
            "total_tokens": trace.total_tokens,
            "completion_tokens": trace.completion_tokens,
            "n_model_steps": trace.n_model_steps,
        },
    )
    trace.debug_events = local_sink.events[debug_start:]
    _compact_trace_for_level(trace, _bfcl_trace_detail_level())
    trace_dict = trace.as_dict()
    trace_dict["cost_events"] = trace_dict_cost_events
    return BenchmarkResult(
        benchmark="bfcl_v3",
        task_id=task.task_id,
        success=bool(score["task_success"]),
        score=float(score["call_f1"]),
        metrics=score,
        trace=trace_dict,
    )


def _task_query_text(task: BenchmarkTask) -> str:
    chunks = []
    for turn in task.question:
        for msg in turn:
            chunks.append(str(msg.get("content", "")))
    return "\n".join(chunks)


def _summarize_initial_state(initial_config: Dict[str, Any], max_chars: int = 6000) -> str:
    """Expose compact environment state that a BFCL agent is expected to use."""
    def shrink(obj: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "..."
        if isinstance(obj, dict):
            out = {}
            for idx, (key, value) in enumerate(obj.items()):
                if idx >= 40:
                    out["..."] = f"{len(obj) - idx} more keys"
                    break
                out[key] = shrink(value, depth + 1)
            return out
        if isinstance(obj, list):
            return [shrink(v, depth + 1) for v in obj[:20]]
        if isinstance(obj, str) and len(obj) > 240:
            return obj[:240] + "...[truncated]"
        return obj

    text = json.dumps(shrink(initial_config), ensure_ascii=False, indent=2)
    return text[:max_chars] + ("...[truncated]" if len(text) > max_chars else "")


def _native_skill_system(skill_prompt: str) -> str:
    return (
        "You may use the following retrieved skill notes for tool selection and "
        "argument construction. When a retrieved skill gives an exact local rule "
        "about parameter names, positional-vs-keyword usage, literal values, call "
        "ordering, stop conditions, or avoiding an extra tool call, follow that "
        "rule strictly unless it directly conflicts with the current tool schema or "
        "user request. If a retrieved skill is unrelated to the current tool family, "
        "schema, or user intent, ignore it completely. Do not call irrelevant tools just to reuse a skill; "
        "prioritize the user's current request and the provided tool schemas.\n\n"
        f"{skill_prompt}"
    )


def _turn_skill_constraints(
    skills: List[SkillArtifact],
    task: BenchmarkTask,
    turn_index: int,
) -> str:
    if not skills:
        return ""
    lines = [
        "Retrieved constraints for this turn. Treat them as local execution rules when applicable:"
    ]
    for skill in skills[:4]:
        if os.environ.get("BFCL_TURN_CONSTRAINT_FULL_BODY", "").strip().lower() in {"1", "true", "yes"}:
            lines.append(f"- {skill.name}: {skill.body}")
        else:
            compact = compact_skill_prompt_block(skill, max_chars=360)
            compact_lines = [
                line for line in (compact.splitlines()[2:] if len(compact.splitlines()) > 2 else [skill.description])
                if not line.strip().lower().startswith("tools:")
            ]
            lines.append(f"- {skill.name}: {'; '.join(line.strip() for line in compact_lines if line.strip())}")
    lines.append(
        "If one of these rules clearly applies, satisfy it during the next tool calls instead of exploring with extra calls."
    )
    return "\n".join(lines)


class _TurnWatchdog:
    """Detect per-turn runaway from repeated identical calls.

    Operates on domain tool calls only (skill_tools and use_skill ignored).
    Returns a non-empty reason string from observe() when the executor
    should break the per-turn step loop.
    """

    def __init__(self, expected_names: List[str] | None = None):
        self._signatures: set[Tuple[str, str]] = set()
        self.early_stop_reason: str | None = None

    def observe(self, calls: List["BFCLToolCall"]) -> str | None:
        if not calls:
            return None
        sigs = [
            (
                call.name,
                json.dumps(
                    call.arguments,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )[:500],
            )
            for call in calls
        ]
        repeated = any(sig in self._signatures for sig in sigs)
        self._signatures.update(sigs)
        if repeated:
            self.early_stop_reason = "repeated_call"
            return self.early_stop_reason
        return None
