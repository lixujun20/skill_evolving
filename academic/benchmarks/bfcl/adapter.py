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
    bfcl_skill_matches_task as _bfcl_skill_matches_task,
    bfcl_skill_matches_turn as _bfcl_skill_matches_turn,
    bfcl_skill_rerank_key as _bfcl_skill_rerank_key,
    error_aware_skill_query as _error_aware_skill_query,
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
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact
from academic.skill_repository.debug_events import DebugEventSink, skill_store_snapshot


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


def _skill_tool_schemas(skills: List[SkillArtifact]) -> List[Dict[str, Any]]:
    schemas: List[Dict[str, Any]] = []
    for skill in skills:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": _skill_tool_name(skill),
                    "description": skill.description[:900],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "Optional reason for consulting this skill.",
                            }
                        },
                        "required": [],
                    },
                },
            }
        )
    return schemas


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
        audit = self.store.retrieve_audit(
            query,
            top_k=self.top_k_skills,
            min_score=self.min_skill_score,
            predicate=lambda artifact: _bfcl_skill_matches_turn(
                artifact, self.task, turn_index, user_messages
            ),
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
        return _retrieved_from_audit(self.store, audit), audit, query

    def retrieve_for_turn(
        self,
        *,
        turn_index: int,
        user_messages: List[Dict[str, Any]],
    ) -> Tuple[List[SkillArtifact], Dict[str, Any]]:
        skills, audit, _query = self.retrieve(turn_index=turn_index, user_messages=user_messages)
        return skills, audit


class _SkillInjectionPolicy:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode

    def initial_selection(self, retrieved_by_turn: List[List[SkillArtifact]]) -> Tuple[List[List[SkillArtifact]], List[SkillArtifact]]:
        prompt_skills_by_turn: List[List[SkillArtifact]] = []
        tool_skills: List[SkillArtifact] = []
        seen_tool_skills: set[str] = set()
        for turn_skills in retrieved_by_turn:
            prompt_turn, tool_turn = _split_skills_for_injection(turn_skills, self.mode)
            prompt_skills_by_turn.append(prompt_turn)
            for skill in tool_turn:
                if skill.name not in seen_tool_skills:
                    seen_tool_skills.add(skill.name)
                    tool_skills.append(skill)
        return prompt_skills_by_turn, tool_skills

    def merge_prompt_skills(
        self,
        *,
        current: List[SkillArtifact],
        retrieved: List[SkillArtifact],
    ) -> Tuple[List[SkillArtifact], List[SkillArtifact]]:
        prompt_skills, _tool_skills = _split_skills_for_injection(retrieved, self.mode)
        existing = {skill.name for skill in current}
        added: List[SkillArtifact] = []
        merged = list(current)
        for skill in prompt_skills:
            if skill.name in existing:
                continue
            existing.add(skill.name)
            merged.append(skill)
            added.append(skill)
        return merged, added


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
        local_sink.emit(
            "skill_tool_call",
            turn_index=turn_index,
            step_index=step_index,
            input=event,
            output={"skill": _skill_brief(skill), "body": skill.body},
        )
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
    debug_sink: DebugEventSink | None = None,
    max_task_seconds: float | None = None,
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
    )
    injection_policy = _SkillInjectionPolicy(mode=skill_injection_mode)
    retrieved_by_turn: List[List[SkillArtifact]] = []
    for turn_index, user_messages in enumerate(task.question):
        turn_skills, audit = retrieval_policy.retrieve_for_turn(
            turn_index=turn_index,
            user_messages=user_messages,
        )
        retrieved_by_turn.append(turn_skills)
        if audit:
            local_sink.emit(
                "retrieval",
                turn_index=turn_index,
                trigger="turn_start",
                input={"query": _turn_query_text(user_messages), "user_messages": user_messages},
                output=audit,
            )
    retrieved_unique: List[SkillArtifact] = []
    seen_retrieved: set[str] = set()
    for turn_skills in retrieved_by_turn:
        for skill in turn_skills:
            if skill.name not in seen_retrieved:
                seen_retrieved.add(skill.name)
                retrieved_unique.append(skill)
    prompt_skills_by_turn, tool_skills = injection_policy.initial_selection(retrieved_by_turn)
    dynamic_prompt_skills_by_turn: List[List[SkillArtifact]] = [list(items) for items in prompt_skills_by_turn]
    trace.retrieved_skills = [skill.name for skill in retrieved_unique]
    trace.tool_injected_skills = [skill.name for skill in tool_skills]
    local_sink.emit(
        "initial_skill_selection",
        output={
            "retrieved_unique": [_skill_brief(skill) for skill in retrieved_unique],
            "prompt_skills_by_turn": [[_skill_brief(skill) for skill in items] for items in prompt_skills_by_turn],
            "tool_skills": [_skill_brief(skill) for skill in tool_skills],
        },
    )
    skill_tools_by_name = {_skill_tool_name(skill): skill for skill in tool_skills}
    if tool_skills:
        tools = tools + _skill_tool_schemas(tool_skills)
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
            turn_prompt_skills = dynamic_prompt_skills_by_turn[turn_index] if turn_index < len(dynamic_prompt_skills_by_turn) else []
            for skill in turn_prompt_skills:
                if skill.name not in trace.prompt_injected_skills:
                    trace.prompt_injected_skills.append(skill.name)
            skill_prompt = artifact_store.build_prompt(turn_prompt_skills) if artifact_store else "(none)"
            prompt_frame = prompt_policy.build(
                skill_prompt=skill_prompt,
                state_summary=state_summary,
                has_turn_skills=bool(turn_prompt_skills),
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
                            "previous_observation": None
                            if last_observation is None
                            else {
                                "tool_name": last_observation.tool_name,
                                "arguments": last_observation.arguments,
                                "error": last_observation.error,
                            },
                        },
                        output=observed_audit,
                    )
                    merged_prompt, added_prompt = injection_policy.merge_prompt_skills(
                        current=dynamic_prompt_skills_by_turn[turn_index],
                        retrieved=observed_skills,
                    )
                    dynamic_prompt_skills_by_turn[turn_index] = merged_prompt
                    for skill in observed_skills:
                        if skill.name not in trace.retrieved_skills:
                            trace.retrieved_skills.append(skill.name)
                    for skill in added_prompt:
                        if skill.name not in trace.prompt_injected_skills:
                            trace.prompt_injected_skills.append(skill.name)
                    if added_prompt:
                        refreshed_prompt = artifact_store.build_prompt(dynamic_prompt_skills_by_turn[turn_index])
                        prompt_frame = prompt_policy.build(
                            skill_prompt=refreshed_prompt,
                            state_summary=state_summary,
                            has_turn_skills=bool(dynamic_prompt_skills_by_turn[turn_index]),
                        )
                        system = prompt_frame.system
                        step_context_msg = _step_skill_context_message(
                            skill_prompt=artifact_store.build_prompt(added_prompt),
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
        return BenchmarkResult(
            benchmark="bfcl_v3",
            task_id=task.task_id,
            success=False,
            score=0.0,
            metrics={"exception": type(exc).__name__},
            trace=trace.as_dict(),
            error=str(exc),
        )

    trace.elapsed_s = round(time.monotonic() - t0, 3)
    trace.total_tokens = tool_client.total_tokens()
    trace.completion_tokens = tool_client.completion_tokens
    score = score_bfcl_calls(trace.tool_calls, task.expected)
    score["total_tokens"] = trace.total_tokens
    score["completion_tokens"] = trace.completion_tokens
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
    return BenchmarkResult(
        benchmark="bfcl_v3",
        task_id=task.task_id,
        success=bool(score["task_success"]),
        score=float(score["call_f1"]),
        metrics=score,
        trace=trace.as_dict(),
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
        lines.append(f"- {skill.name}: {skill.body}")
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
