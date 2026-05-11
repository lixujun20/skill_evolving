"""BFCL-v3 multi-turn adapter.

This module intentionally separates the BFCL scaffold from the math-oriented
academic executor.  It uses native tool calls, can execute them with the BFCL
official backend when available, and reports both lightweight call-F1 metrics
and an official-style state/response validity check.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import math
import os
import operator
import re
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from academic.benchmarks.artifacts import ArtifactStore
from academic.benchmarks.types import BenchmarkResult, BenchmarkTask, SkillArtifact
from academic.config import LLM_CALL_TIMEOUT, LLM_TIMEOUT_RETRIES, RATE_LIMIT_BASE_WAIT
from academic.skill_repository.debug_events import DebugEventSink, skill_store_snapshot

BFCL_OFFICIAL_UNPACK = Path("/tmp/bfcl_pkg/unpack")
DATASET_URL = (
    "https://huggingface.co/datasets/gorilla-llm/"
    "Berkeley-Function-Calling-Leaderboard/raw/main/BFCL_v3_multi_turn_base.json"
)
ANSWER_URL = (
    "https://huggingface.co/datasets/gorilla-llm/"
    "Berkeley-Function-Calling-Leaderboard/raw/main/possible_answer/"
    "BFCL_v3_multi_turn_base.json"
)
BFCL_BUNDLE_DATASET = BFCL_OFFICIAL_UNPACK / "bfcl_eval" / "data" / "BFCL_v4_multi_turn_base.json"
BFCL_BUNDLE_ANSWER = (
    BFCL_OFFICIAL_UNPACK / "bfcl_eval" / "data" / "possible_answer" / "BFCL_v4_multi_turn_base.json"
)
BFCL_BUNDLE_FUNC_DOC_DIR = BFCL_OFFICIAL_UNPACK / "bfcl_eval" / "data" / "multi_turn_func_doc"
FUNC_DOC_BASE = (
    "https://huggingface.co/datasets/gorilla-llm/"
    "Berkeley-Function-Calling-Leaderboard/raw/main/multi_turn_func_doc"
)
FUNC_DOC_FILES = [
    "gorilla_file_system.json",
    "math_api.json",
    "message_api.json",
    "posting_api.json",
    "ticket_api.json",
    "trading_bot.json",
    "travel_booking.json",
    "vehicle_control.json",
]

CLASS_DOC_FILES = {
    "GorillaFileSystem": ["gorilla_file_system.json"],
    "MathAPI": ["math_api.json"],
    "MessageAPI": ["message_api.json"],
    "TwitterAPI": ["posting_api.json"],
    "TicketAPI": ["ticket_api.json"],
    "TradingBot": ["trading_bot.json"],
    "TravelAPI": ["travel_booking.json"],
    "VehicleControlAPI": ["vehicle_control.json"],
}

BFCL_CLASS_FILE_BY_DOC = {file: cls for cls, files in CLASS_DOC_FILES.items() for file in files}


BFCL_SYSTEM = """You are running a BFCL-v3 multi-turn function-calling task.

Use the provided tools directly. Do not write Python code. For each user turn,
call the minimal sequence of tools needed to update the environment or retrieve
the requested information. Reuse returned ids, paths, login tokens, and state
from previous turns. If a historical skill is relevant, use it as guidance, but
do not force irrelevant reuse. Tool arguments must use the exact parameter names
from the provided schema; do not invent aliases such as insurance_id when the
schema requires booking_id.

Retrieved skill artifacts:
{skills}

Initial environment state summary:
{state_summary}
"""

BFCL_OFFICIAL_SYSTEM = """You are an expert in composing functions. You are given a question and a set of possible functions. Based on the question, you will need to make one or more function/tool calls to achieve the purpose. If none of the functions can be used, point it out. If the given question lacks the parameters required by the function, also point it out.

Use the provided native tools directly. At each turn, try your best to complete the tasks requested by the user within the current turn. Continue to call functions until you have fulfilled the user's request to the best of your ability. Once you have no more functions to call, the system will consider the current turn complete and proceed to the next turn or task.

If a retrieved skill is relevant, use it as guidance, but do not force irrelevant reuse.

Retrieved skill artifacts:
{skills}
"""

TURN_INSTRUCTION = (
    "\n\nFor this BFCL turn, perform the requested operation with tool calls now. "
    "Do not just describe what you would do. If the turn asks for multiple actions, "
    "call every required tool in order before ending the turn. Infer missing ids "
    "from the initial state or previous tool results rather than asking the user."
)

OFFICIAL_TURN_INSTRUCTION = (
    "\n\nAt this turn, try your best to complete the user's requested task with "
    "the available tools. Continue calling tools until the request is fulfilled. "
    "If no more tools are needed, respond briefly with no tool call."
)

USE_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "Mark a retrieved skill artifact as intentionally used. Call this "
            "before domain tools when a skill guides the current turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the retrieved skill artifact being used.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason this skill applies to the current turn.",
                },
            },
            "required": ["skill_name"],
        },
    },
}


@dataclass
class BFCLToolCall:
    name: str  # Canonical tool name actually executed against the BFCL environment.
    arguments: Dict[str, Any]  # Parsed JSON arguments sent to the tool.
    turn_index: int  # Which user turn this tool call belongs to.
    tool_call_id: str = ""  # Provider-native tool call id used to stitch tool results back into the conversation.
    result: Any = None  # Raw tool return payload, possibly wrapped when alias canonicalization happened.
    error: Optional[str] = None  # Tool-level failure message, if any.

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "turn_index": self.turn_index,
            "tool_call_id": self.tool_call_id,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class BFCLTrace:
    task_id: str  # Executed BFCL task id.
    messages: List[Dict[str, Any]] = field(default_factory=list)  # Full conversational transcript, including assistant messages and tool results.
    turns: List[Dict[str, Any]] = field(default_factory=list)  # Turn-level grouped view used by replay, extraction, and UI rendering.
    tool_calls: List[BFCLToolCall] = field(default_factory=list)  # Flattened list of all executed domain tool calls.
    skill_events: List[Dict[str, Any]] = field(default_factory=list)  # Explicit skill-use events, including use_skill calls and skill tool consultations.
    retrieved_skills: List[str] = field(default_factory=list)  # Unique skill names retrieved for this task, across all turns and retries.
    prompt_injected_skills: List[str] = field(default_factory=list)  # Skill names actually inserted into prompt context.
    tool_injected_skills: List[str] = field(default_factory=list)  # Skill names exposed as callable tools rather than prompt notes.
    called_skill_tools: List[str] = field(default_factory=list)  # Skill names explicitly invoked through skill tool calls.
    turn_step_counts: List[int] = field(default_factory=list)  # Number of model-response steps spent per user turn.
    n_model_steps: int = 0  # Total assistant steps over the full task.
    total_tokens: int = 0  # Total input + completion tokens consumed by the task.
    completion_tokens: int = 0  # Completion-side tokens only.
    elapsed_s: float = 0.0  # End-to-end wall clock time for the task.
    timed_out: bool = False  # Whether task execution hit a timeout guard.
    debug_events: List[Dict[str, Any]] = field(default_factory=list)  # Structured debug timeline for retrieval, prompting, and tool execution.

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "messages": self.messages,
            "turns": self.turns,
            "tool_calls": [call.as_dict() for call in self.tool_calls],
            "skill_events": self.skill_events,
            "retrieved_skills": self.retrieved_skills,
            "prompt_injected_skills": self.prompt_injected_skills,
            "tool_injected_skills": self.tool_injected_skills,
            "called_skill_tools": self.called_skill_tools,
            "turn_step_counts": self.turn_step_counts,
            "n_model_steps": self.n_model_steps,
            "total_tokens": self.total_tokens,
            "completion_tokens": self.completion_tokens,
            "elapsed_s": self.elapsed_s,
            "timed_out": self.timed_out,
            "debug_events": self.debug_events,
        }


def load_bfcl_tasks(
    *,
    cache_dir: Path,
    split_seed: int = 42,
    n_train: int = 50,
    n_test: int = 150,
    refresh: bool = False,
    data_source: str = "hf_v3",
) -> Tuple[List[BenchmarkTask], List[BenchmarkTask]]:
    """Load BFCL-v3 multi-turn base and return deterministic train/test split."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    if data_source == "bfcl_eval_bundle":
        if not BFCL_BUNDLE_DATASET.exists() or not BFCL_BUNDLE_ANSWER.exists():
            raise FileNotFoundError(
                "bfcl_eval_bundle data source requires an unpacked bfcl-eval wheel at "
                f"{BFCL_OFFICIAL_UNPACK}"
            )
        dataset_path = BFCL_BUNDLE_DATASET
        answer_path = BFCL_BUNDLE_ANSWER
        data_version = "BFCL_v4_bundle"
    elif data_source == "hf_v3":
        dataset_path = cache_dir / "BFCL_v3_multi_turn_base.jsonl"
        answer_path = cache_dir / "BFCL_v3_multi_turn_base_answers.jsonl"
        if refresh or not dataset_path.exists():
            _download(DATASET_URL, dataset_path)
        if refresh or not answer_path.exists():
            _download(ANSWER_URL, answer_path)
        data_version = "BFCL_v3_hf"
    else:
        raise ValueError(f"Unknown BFCL data_source: {data_source}")

    answers = {
        item["id"]: item.get("ground_truth", [])
        for item in _read_jsonl(answer_path)
    }
    tasks: List[BenchmarkTask] = []
    for item in _read_jsonl(dataset_path):
        task_id = item["id"]
        tasks.append(
            BenchmarkTask(
                benchmark="bfcl_v3",
                task_id=task_id,
                question=item["question"],
                expected=answers.get(task_id, []),
                input_artifacts={"initial_config": item.get("initial_config", {})},
                metadata={
                    "path": item.get("path", []),
                    "involved_classes": item.get("involved_classes", []),
                    "bfcl_data_source": data_source,
                    "bfcl_data_version": data_version,
                },
            )
        )

    import random

    shuffled = list(tasks)
    random.Random(split_seed).shuffle(shuffled)
    train = shuffled[:n_train]
    test = shuffled[n_train : n_train + n_test]
    return train, test


def load_bfcl_tools(
    cache_dir: Path,
    refresh: bool = False,
    data_source: str = "hf_v3",
) -> List[Dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    tools: List[Dict[str, Any]] = []
    for filename in FUNC_DOC_FILES:
        if data_source == "bfcl_eval_bundle":
            path = BFCL_BUNDLE_FUNC_DOC_DIR / filename
            if not path.exists():
                raise FileNotFoundError(f"Missing bundled BFCL function doc: {path}")
        elif data_source == "hf_v3":
            path = cache_dir / "multi_turn_func_doc" / filename
            if refresh or not path.exists():
                _download(f"{FUNC_DOC_BASE}/{filename}", path)
        else:
            raise ValueError(f"Unknown BFCL data_source: {data_source}")
        for item in _read_jsonl(path):
            tools.append(_to_openai_tool(item, source_file=filename))
    return tools


def filter_bfcl_tools_by_class(
    tools: List[Dict[str, Any]],
    task: BenchmarkTask,
) -> List[Dict[str, Any]]:
    """Match BFCL official multi-turn FC: expose all tools for involved classes."""
    class_files = {
        file
        for cls in task.metadata.get("involved_classes", [])
        for file in CLASS_DOC_FILES.get(cls, [])
    }
    if not class_files:
        return tools
    return [
        tool for tool in tools
        if tool.get("function", {}).get("x_bfcl_source_file") in class_files
    ]


def filter_bfcl_tools_for_task(
    tools: List[Dict[str, Any]],
    task: BenchmarkTask,
    *,
    include_expected_tools: bool = False,
) -> List[Dict[str, Any]]:
    """Reduce tool prompt to the classes/functions actually present in a task.

    BFCL raw docs contain 129 functions.  Providing all of them makes GLM spend
    excessive prompt tokens and increases irrelevant-call errors.  The dataset
    exposes `path` and `involved_classes`, so using this filter still follows the
    benchmark's per-task available-tool contract.
    """
    allowed_funcs = {
        str(path).split(".")[-1]
        for path in task.metadata.get("path", [])
        if path
    }
    if include_expected_tools:
        allowed_funcs.update(_expected_tool_names(task))
    if not allowed_funcs:
        class_files = {
            file
            for cls in task.metadata.get("involved_classes", [])
            for file in CLASS_DOC_FILES.get(cls, [])
        }
        if class_files:
            # Function names are unique enough in BFCL base except get_current_time;
            # when no explicit path is available, class filtering is still better
            # than the full 129-tool prompt.
            return [
                tool for tool in tools
                if _tool_source_file(tool["function"]["name"]) in class_files
            ]
        return tools
    return [
        tool for tool in tools
        if tool.get("function", {}).get("name") in allowed_funcs
    ]


def make_bfcl_tools_for_task(
    tools: List[Dict[str, Any]],
    task: BenchmarkTask,
    *,
    adapter_mode: str = "official",
    enable_skill_tool: bool = True,
) -> List[Dict[str, Any]]:
    if adapter_mode == "official":
        selected = filter_bfcl_tools_by_class(tools, task)
    elif adapter_mode == "path_filtered":
        selected = filter_bfcl_tools_for_task(tools, task, include_expected_tools=False)
    elif adapter_mode == "debug_hints":
        selected = filter_bfcl_tools_for_task(tools, task, include_expected_tools=True)
    elif adapter_mode == "full_tools":
        selected = list(tools)
    else:
        raise ValueError(f"Unknown BFCL adapter_mode: {adapter_mode}")
    selected = [_strip_tool_metadata(tool) for tool in selected]
    if enable_skill_tool:
        return [USE_SKILL_TOOL] + selected
    return selected


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


async def run_bfcl_task(
    task: BenchmarkTask,
    *,
    llm_config: str,
    tools: List[Dict[str, Any]],
    artifact_store: Optional[ArtifactStore] = None,
    top_k_skills: int = 5,
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
) -> BenchmarkResult:
    from app.llm import LLM

    t0 = time.monotonic()
    resolved_api_style = _resolve_tool_api_style(tool_api_style, llm_config, model_name)
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
            "skill_injection_mode": skill_injection_mode,
            "prompt_style": prompt_style,
            "adapter_mode": adapter_mode,
            "execution_backend": execution_backend,
            "tool_api_style": resolved_api_style,
        },
        store_snapshot=skill_store_snapshot(artifact_store) if artifact_store else {"n_total": 0, "skills": []},
    )
    retrieved_by_turn: List[List[SkillArtifact]] = []
    if artifact_store:
        for turn_index, user_messages in enumerate(task.question):
            query = _turn_query_text(user_messages)
            audit = artifact_store.retrieve_audit(
                query,
                top_k=top_k_skills,
                predicate=lambda artifact, ti=turn_index, msgs=user_messages: _bfcl_skill_matches_turn(
                    artifact, task, ti, msgs
                ),
                rerank_key=lambda artifact, ti=turn_index, msgs=user_messages: _bfcl_skill_rerank_key(
                    artifact,
                    task,
                    ti,
                    msgs,
                ),
                debug_context={"phase": "turn_start", "turn_index": turn_index},
            )
            retrieved_by_turn.append(_retrieved_from_audit(artifact_store, audit))
            local_sink.emit(
                "retrieval",
                turn_index=turn_index,
                trigger="turn_start",
                input={"query": query, "user_messages": user_messages},
                output=audit,
            )
    else:
        retrieved_by_turn = [[] for _ in task.question]
    retrieved_unique: List[SkillArtifact] = []
    seen_retrieved: set[str] = set()
    prompt_skills_by_turn: List[List[SkillArtifact]] = []
    tool_skills: List[SkillArtifact] = []
    seen_tool_skills: set[str] = set()
    for turn_skills in retrieved_by_turn:
        for skill in turn_skills:
            if skill.name not in seen_retrieved:
                seen_retrieved.add(skill.name)
                retrieved_unique.append(skill)
        prompt_turn, tool_turn = _split_skills_for_injection(turn_skills, skill_injection_mode)
        prompt_skills_by_turn.append(prompt_turn)
        for skill in tool_turn:
            if skill.name not in seen_tool_skills:
                seen_tool_skills.add(skill.name)
                tool_skills.append(skill)
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
    state_summary = (
        _summarize_initial_state(task.input_artifacts.get("initial_config", {}))
        if adapter_mode == "debug_hints"
        else "(hidden; use tool results and user-provided values)"
    )
    turn_instruction = ""
    messages: List[Dict[str, Any]] = []
    if resolved_api_style == "anthropic_direct":
        llm = None
        anthropic_state = _make_anthropic_state(llm_config, model_name)
        openai_direct_state = None
        openai_stream_state = None
        prompt_tokens_used = 0
        completion_tokens_used = 0
    elif resolved_api_style == "openai_direct":
        llm = None
        anthropic_state = None
        openai_direct_state = _make_openai_direct_state(llm_config, model_name)
        openai_stream_state = None
        prompt_tokens_used = 0
        completion_tokens_used = 0
    elif resolved_api_style == "openai_stream":
        llm = None
        anthropic_state = None
        openai_direct_state = None
        openai_stream_state = _make_openai_stream_state(llm_config, model_name)
        prompt_tokens_used = 0
        completion_tokens_used = 0
    else:
        anthropic_state = None
        openai_direct_state = None
        openai_stream_state = None
        llm = LLM(config_name=llm_config)
        tokens_before = llm.total_input_tokens + llm.total_completion_tokens
        completion_before = llm.total_completion_tokens

    try:
        for turn_index, user_messages in enumerate(task.question):
            turn_prompt_skills = dynamic_prompt_skills_by_turn[turn_index] if turn_index < len(dynamic_prompt_skills_by_turn) else []
            for skill in turn_prompt_skills:
                if skill.name not in trace.prompt_injected_skills:
                    trace.prompt_injected_skills.append(skill.name)
            skill_prompt = artifact_store.build_prompt(turn_prompt_skills) if artifact_store else "(none)"
            if prompt_style == "native":
                system = _native_skill_system(skill_prompt) if turn_prompt_skills else ""
                turn_instruction = ""
            elif prompt_style == "official":
                system = BFCL_OFFICIAL_SYSTEM.format(skills=skill_prompt)
                turn_instruction = OFFICIAL_TURN_INSTRUCTION
            elif prompt_style == "academic":
                system = BFCL_SYSTEM.format(skills=skill_prompt, state_summary=state_summary)
                turn_instruction = TURN_INSTRUCTION
            else:
                raise ValueError(f"Unknown BFCL prompt_style: {prompt_style}")
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
            watchdog = _TurnWatchdog(_expected_tool_names_for_turn(task, turn_index))
            calls_seen_before_step = turn_calls_before
            watchdog_break_reason: str | None = None
            for step in range(max_steps_per_turn):
                steps_this_turn += 1
                trace.n_model_steps += 1
                if resolved_api_style == "anthropic_direct":
                    response = await _ask_anthropic_tool_with_retry(
                        anthropic_state,
                        messages,
                        system,
                        tools,
                        temperature=temperature,
                    )
                elif resolved_api_style == "openai_direct":
                    response = await _ask_openai_direct_tool_with_retry(
                        openai_direct_state,
                        messages,
                        system,
                        tools,
                        temperature=temperature,
                    )
                elif resolved_api_style == "openai_stream":
                    response = await _ask_openai_stream_tool_with_retry(
                        openai_stream_state,
                        messages,
                        system,
                        tools,
                        temperature=temperature,
                    )
                else:
                    response = await _ask_tool_with_retry(
                        llm,
                        messages,
                        system,
                        tools,
                        model_name=model_name,
                        temperature=temperature,
                    )
                if response is None:
                    trace.timed_out = True
                    break
                if resolved_api_style == "anthropic_direct":
                    content, tool_calls, assistant_msg, usage = _normalize_anthropic_response(response)
                    prompt_tokens_used += usage[0]
                    completion_tokens_used += usage[1]
                elif resolved_api_style == "openai_direct":
                    content, tool_calls, assistant_msg, usage = response
                    prompt_tokens_used += usage[0]
                    completion_tokens_used += usage[1]
                elif resolved_api_style == "openai_stream":
                    content, tool_calls, assistant_msg, usage = response
                    prompt_tokens_used += usage[0]
                    completion_tokens_used += usage[1]
                else:
                    content = response.content or ""
                    tool_calls = [
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                        for tc in (response.tool_calls or [])
                    ]
                    assistant_msg = {"role": "assistant", "content": content}
                    if tool_calls:
                        assistant_msg["tool_calls"] = [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            }
                            for tc in tool_calls
                        ]
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
                        "prompt_tokens_used": prompt_tokens_used,
                        "completion_tokens_used": completion_tokens_used,
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
                    if tool_name in skill_tools_by_name:
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
                            step_index=step,
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
                        if resolved_api_style == "anthropic_direct":
                            messages.append(
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "tool_result",
                                            "tool_use_id": tc["id"],
                                            "content": skill_content,
                                        }
                                    ],
                                }
                            )
                        else:
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": skill_content,
                                }
                            )
                        trace.messages.append(messages[-1])
                        continue
                    if tool_name == "use_skill":
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
                            step_index=step,
                            input=event,
                        )
                        skill_content = json.dumps({"status": "recorded", **event}, ensure_ascii=False)
                        if resolved_api_style == "anthropic_direct":
                            messages.append(
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "tool_result",
                                            "tool_use_id": tc["id"],
                                            "content": skill_content,
                                        }
                                    ],
                                }
                            )
                        else:
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": skill_content,
                                }
                            )
                        trace.messages.append(messages[-1])
                        continue
                    result, error = env.call(tool_name, args)
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
                        step_index=step,
                        output=call.as_dict(),
                    )
                    tool_content = json.dumps(
                        result if error is None else {"error": error},
                        ensure_ascii=False,
                    )[:3000]
                    if resolved_api_style == "anthropic_direct":
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": tc["id"],
                                        "content": tool_content,
                                    }
                                ],
                            }
                        )
                    else:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": tool_content,
                            }
                        )
                    trace.messages.append(messages[-1])
                    if (
                        error is not None
                        and artifact_store is not None
                        and turn_index < len(dynamic_prompt_skills_by_turn)
                    ):
                        retry_query = _error_aware_skill_query(
                            task=task,
                            turn_index=turn_index,
                            user_messages=user_messages,
                            tool_name=tool_name,
                            args=args,
                            error=error,
                        )
                        retry_audit = artifact_store.retrieve_audit(
                            retry_query,
                            top_k=max(2, min(top_k_skills, 4)),
                            predicate=lambda artifact, ti=turn_index, msgs=user_messages: _bfcl_skill_matches_turn(
                                artifact,
                                task,
                                ti,
                                msgs,
                            ),
                            rerank_key=lambda artifact, ti=turn_index, msgs=user_messages: _bfcl_skill_rerank_key(
                                artifact,
                                task,
                                ti,
                                msgs,
                            ),
                            debug_context={"phase": "tool_error_retry", "turn_index": turn_index, "tool_name": tool_name},
                        )
                        retry_skills = _retrieved_from_audit(artifact_store, retry_audit)
                        local_sink.emit(
                            "retrieval",
                            turn_index=turn_index,
                            trigger="tool_error_retry",
                            input={"query": retry_query, "tool_name": tool_name, "arguments": args, "error": error},
                            output=retry_audit,
                        )
                        changed = False
                        for retry_skill in retry_skills:
                            if retry_skill.name not in trace.retrieved_skills:
                                trace.retrieved_skills.append(retry_skill.name)
                            if retry_skill.name not in {item.name for item in dynamic_prompt_skills_by_turn[turn_index]}:
                                dynamic_prompt_skills_by_turn[turn_index].append(retry_skill)
                                changed = True
                            if retry_skill.name not in trace.prompt_injected_skills:
                                trace.prompt_injected_skills.append(retry_skill.name)
                        if changed and prompt_style in {"native", "official", "academic"}:
                            refreshed_prompt = artifact_store.build_prompt(dynamic_prompt_skills_by_turn[turn_index])
                            system = (
                                _native_skill_system(refreshed_prompt)
                                if prompt_style == "native"
                                else (
                                    BFCL_OFFICIAL_SYSTEM.format(skills=refreshed_prompt)
                                    if prompt_style == "official"
                                    else BFCL_SYSTEM.format(skills=refreshed_prompt, state_summary=state_summary)
                                )
                            )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "The previous tool call failed. Re-check exact schema names, ids, literal text, "
                                        "and whether additional retrieved skills now apply before the next tool call."
                                    ),
                                }
                            )
                            trace.messages.append(messages[-1])
                            local_sink.emit(
                                "prompt_reinjection",
                                turn_index=turn_index,
                                trigger="tool_error_retry",
                                output={
                                    "added_skills": [_skill_brief(skill) for skill in retry_skills],
                                    "refreshed_prompt": refreshed_prompt,
                                    "system": system,
                                },
                            )
                domain_calls_this_step = trace.tool_calls[calls_seen_before_step:]
                calls_seen_before_step = len(trace.tool_calls)
                watchdog_break_reason = watchdog.observe(domain_calls_this_step)
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
    if resolved_api_style in {"anthropic_direct", "openai_direct", "openai_stream"}:
        trace.total_tokens = prompt_tokens_used + completion_tokens_used
        trace.completion_tokens = completion_tokens_used
    else:
        trace.total_tokens = (llm.total_input_tokens + llm.total_completion_tokens) - tokens_before
        trace.completion_tokens = llm.total_completion_tokens - completion_before
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
    return BenchmarkResult(
        benchmark="bfcl_v3",
        task_id=task.task_id,
        success=bool(score["task_success"]),
        score=float(score["call_f1"]),
        metrics=score,
        trace=trace.as_dict(),
    )


def score_bfcl_calls(calls: List[BFCLToolCall], expected_turns: Any) -> Dict[str, Any]:
    expected_by_turn = [_parse_expected_turn(turn) for turn in expected_turns or []]
    actual_by_turn: List[List[Tuple[str, Dict[str, Any]]]] = []
    n_turns = max(len(expected_by_turn), 1 + max((c.turn_index for c in calls), default=-1))
    for turn_index in range(n_turns):
        actual_by_turn.append(
            [(c.name, c.arguments) for c in calls if c.turn_index == turn_index]
        )

    turn_scores = []
    total_expected = 0
    total_actual = 0
    total_matched = 0
    for idx in range(n_turns):
        exp = expected_by_turn[idx] if idx < len(expected_by_turn) else []
        act = actual_by_turn[idx] if idx < len(actual_by_turn) else []
        matched = _greedy_match_calls(act, exp)
        total_expected += len(exp)
        total_actual += len(act)
        total_matched += matched
        precision = matched / len(act) if act else float(not exp)
        recall = matched / len(exp) if exp else float(not act)
        f1 = _f1(precision, recall)
        turn_scores.append(
            {
                "turn_index": idx,
                "expected_calls": len(exp),
                "actual_calls": len(act),
                "matched_calls": matched,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "turn_success": bool(exp == [] and act == [] or (matched == len(exp) == len(act))),
            }
        )

    precision = total_matched / total_actual if total_actual else float(total_expected == 0)
    recall = total_matched / total_expected if total_expected else float(total_actual == 0)
    call_f1 = _f1(precision, recall)
    return {
        "task_success": all(t["turn_success"] for t in turn_scores),
        "relaxed_task_success": all(t["matched_calls"] == t["expected_calls"] for t in turn_scores),
        "turn_success_rate": round(
            sum(1 for t in turn_scores if t["turn_success"]) / max(len(turn_scores), 1), 4
        ),
        "relaxed_turn_success_rate": round(
            sum(1 for t in turn_scores if t["matched_calls"] == t["expected_calls"])
            / max(len(turn_scores), 1),
            4,
        ),
        "call_precision": round(precision, 4),
        "call_recall": round(recall, 4),
        "call_f1": round(call_f1, 4),
        "n_expected_calls": total_expected,
        "n_actual_calls": total_actual,
        "n_matched_calls": total_matched,
        "turn_scores": turn_scores,
        "call_errors": _call_error_analysis(actual_by_turn, expected_by_turn),
    }


def score_bfcl_official(calls: List[BFCLToolCall], task: BenchmarkTask) -> Dict[str, Any]:
    """Run BFCL's official multi-turn state/response checker when importable.

    The checker accepts a nested list of decoded function-call strings:
    turn -> step -> calls.  Our adapter can receive parallel tool calls in one
    model response, but for stateful BFCL tasks sequential one-call steps are a
    safe representation and preserve turn membership.
    """
    try:
        _ensure_bfcl_eval_importable()
        from bfcl_eval.eval_checker.multi_turn_eval.multi_turn_checker import (
            multi_turn_checker,
        )
    except Exception as exc:
        return {
            "valid": None,
            "error_type": "official_checker_unavailable",
            "error_message": str(exc),
        }

    decoded: List[List[List[str]]] = []
    n_turns = max(
        len(task.expected or []),
        1 + max((call.turn_index for call in calls), default=-1),
    )
    for turn_index in range(n_turns):
        decoded.append(
            [[_call_to_source(call.name, call.arguments)] for call in calls if call.turn_index == turn_index]
        )
    try:
        result = multi_turn_checker(
            decoded,
            task.expected or [],
            _task_to_official_entry(task),
            "multi_turn_base",
            _safe_model_stem(f"academic_checker_{task.task_id}_{time.time_ns()}"),
        )
        return _jsonable(result)
    except Exception as exc:
        return {
            "valid": None,
            "error_type": "official_checker_exception",
            "error_message": f"{type(exc).__name__}: {exc}",
        }


class BFCLOfficialEnvironment:
    """Thin wrapper around BFCL's official executable backend."""

    backend_name = "official"

    def __init__(
        self,
        initial_config: Dict[str, Any],
        involved_classes: List[str],
        task_id: str,
    ) -> None:
        self.initial_config = copy.deepcopy(initial_config)
        self.involved_classes = list(involved_classes or [])
        self.task_id = task_id
        self.model_stem = _safe_model_stem(f"academic_runtime_{task_id}_{id(self)}")
        self.available = self._load_backend()

    def _load_backend(self) -> bool:
        try:
            _ensure_bfcl_eval_importable()
            from bfcl_eval.eval_checker.multi_turn_eval.multi_turn_utils import (
                execute_multi_turn_func_call,
            )
            self._execute = execute_multi_turn_func_call
            return True
        except Exception as exc:
            self._import_error = str(exc)
            return False

    def call(self, name: str, args: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        if not self.available:
            return {"error": getattr(self, "_import_error", "official backend unavailable")}, "unavailable"
        source = _call_to_source(name, args)
        try:
            outputs, _ = self._execute(
                [source],
                self.initial_config,
                self.involved_classes,
                self.model_stem,
                self.task_id,
                long_context=False,
                is_evaL_run=False,
            )
            raw = outputs[0] if outputs else ""
            parsed = _maybe_json(raw)
            error = raw if isinstance(raw, str) and raw.startswith("Error during execution:") else None
            return parsed, error
        except Exception as exc:
            return {"error": str(exc)}, str(exc)


class BFCLLocalEnvironment:
    """Lightweight stateful executor for BFCL base tools.

    It is deliberately permissive: unsupported business APIs return structured
    mock data while file-system and common arithmetic/social actions update
    state.  This keeps the baseline scaffold focused on function-call behavior
    rather than exact backend simulation.
    """

    backend_name = "local_mock"

    def __init__(self, initial_config: Dict[str, Any]) -> None:
        self.state = copy.deepcopy(initial_config)
        self.fs = _FileSystem(self.state.get("GorillaFileSystem", {}).get("root"))

    def call(self, name: str, args: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        try:
            if hasattr(self.fs, name):
                return getattr(self.fs, name)(**args), None
            if name in _MATH_FUNCS:
                return _MATH_FUNCS[name](**args), None
            return self._call_stateful_api(name, args), None
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {args}"}, str(exc)
        except Exception as exc:
            return {"error": str(exc)}, str(exc)

    def _call_stateful_api(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name.startswith("authenticate_") or name.endswith("_login"):
            token = f"{name}_token"
            return {"authenticated": True, "access_token": token, "token_type": "Bearer"}
        if name.endswith("_get_login_status"):
            return {"authenticated": True}
        if name == "post_tweet":
            api = self.state.setdefault("TwitterAPI", {})
            counter = int(api.get("tweet_counter", 0))
            tweets = api.setdefault("tweets", {})
            tweets[str(counter)] = {
                "id": counter,
                "username": api.get("username", "user"),
                "content": args.get("content", ""),
                "tags": args.get("tags", []),
                "mentions": args.get("mentions", []),
            }
            api["tweet_counter"] = counter + 1
            return {"tweet_id": counter, "posted": True}
        if name in {"comment", "mention", "retweet", "follow_user", "unfollow_user"}:
            return {"status": True, "action": name, **args}
        if name == "get_user_id":
            user = args.get("user") or args.get("user_name") or "user"
            return {"user_id": str(abs(hash(user)) % 10000)}
        if name == "send_message":
            return {"sent_status": True, "message_id": abs(hash(json.dumps(args, sort_keys=True))) % 100000}
        if name.startswith("view_messages") or name in {"add_contact", "delete_message", "search_messages"}:
            return {"status": True, "items": []}
        if name in {"create_ticket", "get_ticket", "edit_ticket", "close_ticket", "resolve_ticket"}:
            ticket_id = args.get("ticket_id", self.state.setdefault("TicketAPI", {}).get("ticket_counter", 1))
            self.state.setdefault("TicketAPI", {})["ticket_counter"] = int(ticket_id) + 1 if isinstance(ticket_id, int) else 1
            return {"ticket_id": ticket_id, "status": True}
        if name in {"get_stock_info", "get_symbol_by_name"}:
            stock = args.get("stock") or args.get("company_name") or args.get("symbol") or "STOCK"
            return {"symbol": str(stock).upper()[:5], "price": 100.0, "stock": stock}
        if name in {"place_order", "cancel_order", "fund_account", "make_transaction"}:
            return {"status": True, "order_id": args.get("order_id", 1), "transaction_id": 1}
        if name.startswith("get_") or name.startswith("list_") or name.startswith("retrieve_"):
            return {"result": self._lookup_state_value(name, args), "status": True}
        if name.startswith("set_") or name.startswith("update_") or name in {
            "book_flight", "cancel_booking", "purchase_insurance", "register_credit_card",
            "verify_traveler_information", "contact_customer_support", "add_to_watchlist",
            "remove_stock_from_watchlist", "startEngine", "fillFuelTank", "lockDoors",
            "setHeadlights", "activateParkingBrake", "setCruiseControl", "pressBrakePedal",
        }:
            return {"status": True, "action": name, **args}
        if name in {"estimate_distance", "estimate_drive_feasibility_by_mileage"}:
            return {"distance": args.get("distance", 100), "feasible": True}
        if name in {"liter_to_gallon", "gallon_to_liter"}:
            value = _first_number(args)
            return {"value": value * (0.264172 if name == "liter_to_gallon" else 3.78541)}
        return {"status": True, "action": name, "arguments": args}

    def _lookup_state_value(self, name: str, args: Dict[str, Any]) -> Any:
        key = name.replace("get_", "").replace("retrieve_", "")
        for section in self.state.values():
            if isinstance(section, dict):
                for k, v in section.items():
                    if key in k.lower():
                        return v
        return args or []


class _FileSystem:
    def __init__(self, root: Any) -> None:
        self.root = root if isinstance(root, dict) else {}
        self.cwd: List[str] = []

    def _node(self, path: Optional[str] = None) -> Dict[str, Any]:
        parts = self._parts(path)
        node = self.root
        for part in parts:
            node = node[part]["contents"]
        return node

    def _parts(self, path: Optional[str] = None) -> List[str]:
        parts = list(self.cwd)
        if path and path not in {".", ""}:
            for part in str(path).split("/"):
                if part in {"", "."}:
                    continue
                if part == "..":
                    if parts:
                        parts.pop()
                else:
                    parts.append(part)
        return parts

    def _entry(self, name: str) -> Dict[str, Any]:
        node = self._node()
        if name not in node:
            raise FileNotFoundError(name)
        return node[name]

    def _read_file(self, name: str) -> str:
        entry = self._entry(name)
        if entry.get("type") != "file":
            raise IsADirectoryError(name)
        return str(entry.get("content", ""))

    def cat(self, file_name: str) -> Dict[str, Any]:
        return {"file_content": self._read_file(file_name)}

    def cd(self, folder: str) -> Dict[str, Any]:
        if folder == "..":
            if self.cwd:
                self.cwd.pop()
        else:
            node = self._node()
            if folder not in node:
                raise FileNotFoundError(folder)
            if node[folder].get("type") != "directory":
                raise NotADirectoryError(folder)
            self.cwd.append(folder)
        return {"current_working_directory": "/" + "/".join(self.cwd)}

    def cp(self, source: str, destination: str) -> Dict[str, Any]:
        node = self._node()
        if source not in node:
            raise FileNotFoundError(source)
        copied = copy.deepcopy(node[source])
        if destination in node and node[destination].get("type") == "directory":
            node[destination]["contents"][source] = copied
        else:
            node[destination] = copied
        return {"result": "copied"}

    def diff(self, file_name1: str, file_name2: str) -> Dict[str, Any]:
        a = self._read_file(file_name1).splitlines() or self._read_file(file_name1).split()
        b = self._read_file(file_name2).splitlines() or self._read_file(file_name2).split()
        out = []
        for left, right in zip(a, b):
            if left != right:
                out.append(f"- {left}\n+ {right}")
        if len(a) != len(b):
            out.append(f"length differs: {len(a)} vs {len(b)}")
        return {"diff_lines": "\n".join(out)}

    def du(self, human_readable: bool = False) -> Dict[str, Any]:
        size = self._size(self._node())
        return {"disk_usage": f"{size}B" if human_readable else size}

    def echo(self, content: str, file_name: str) -> Dict[str, Any]:
        self._node()[file_name] = {"type": "file", "content": content}
        return {"result": "written"}

    def find(self, path: str = ".", name: str = "") -> Dict[str, Any]:
        start = self._node(path)
        matches: List[str] = []

        def walk(node: Dict[str, Any], prefix: str) -> None:
            for child_name, entry in node.items():
                child_path = f"{prefix}/{child_name}".strip("/")
                if name in child_name:
                    matches.append(child_path)
                if entry.get("type") == "directory":
                    walk(entry.get("contents", {}), child_path)

        walk(start, path if path != "." else "")
        return {"matches": matches}

    def grep(self, file_name: str, pattern: str) -> Dict[str, Any]:
        content = self._read_file(file_name)
        lines = content.splitlines() or [content]
        return {"matches": [line for line in lines if pattern.lower() in line.lower()]}

    def ls(self, a: bool = False) -> Dict[str, Any]:
        names = list(self._node().keys())
        if not a:
            names = [name for name in names if not name.startswith(".")]
        return {"files": names}

    def mkdir(self, dir_name: str) -> Dict[str, Any]:
        self._node()[dir_name] = {"type": "directory", "contents": {}}
        return {"result": "directory created"}

    def mv(self, source: str, destination: str) -> Dict[str, Any]:
        node = self._node()
        if source not in node:
            raise FileNotFoundError(source)
        entry = node.pop(source)
        if destination in node and node[destination].get("type") == "directory":
            node[destination]["contents"][source] = entry
        else:
            node[destination] = entry
        return {"result": "moved"}

    def pwd(self) -> Dict[str, Any]:
        return {"current_working_directory": "/" + "/".join(self.cwd)}

    def rm(self, file_name: str) -> Dict[str, Any]:
        del self._node()[file_name]
        return {"result": "removed"}

    def rmdir(self, dir_name: str) -> Dict[str, Any]:
        entry = self._entry(dir_name)
        if entry.get("type") != "directory":
            raise NotADirectoryError(dir_name)
        if entry.get("contents"):
            raise OSError("directory not empty")
        del self._node()[dir_name]
        return {"result": "directory removed"}

    def sort(self, file_name: str) -> Dict[str, Any]:
        content = self._read_file(file_name)
        lines = content.splitlines()
        if not lines:
            lines = content.split()
        sorted_content = "\n".join(sorted(lines))
        self._node()[file_name]["content"] = sorted_content
        return {"sorted_content": sorted_content}

    def tail(self, file_name: str, lines: int = 10) -> Dict[str, Any]:
        content = self._read_file(file_name)
        parts = content.splitlines() or content.split()
        return {"tail": "\n".join(parts[-int(lines):])}

    def touch(self, file_name: str) -> Dict[str, Any]:
        self._node()[file_name] = {"type": "file", "content": ""}
        return {"result": "created"}

    def wc(self, file_name: str, mode: str = "w") -> Dict[str, Any]:
        content = self._read_file(file_name)
        if mode == "l":
            value = len(content.splitlines())
        elif mode == "c":
            value = len(content)
        else:
            value = len(content.split())
        return {"count": value}

    def _size(self, node: Dict[str, Any]) -> int:
        total = 0
        for entry in node.values():
            if entry.get("type") == "file":
                total += len(str(entry.get("content", "")))
            else:
                total += self._size(entry.get("contents", {}))
        return total


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        path.write_bytes(response.read())


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    for line in path.read_text().splitlines():
        if line.strip():
            yield json.loads(line)


def _to_openai_tool(doc: Dict[str, Any], *, source_file: str = "") -> Dict[str, Any]:
    params = _normalize_json_schema(copy.deepcopy(doc.get("parameters", {})))
    description = doc.get("description", "")
    if doc.get("response"):
        description += f" Response schema: {json.dumps(doc['response'], ensure_ascii=False)}"
    return {
        "type": "function",
        "function": {
            "name": doc["name"],
            "description": description,
            "parameters": params or {"type": "object", "properties": {}},
            "x_bfcl_source_file": source_file,
            "x_bfcl_class": BFCL_CLASS_FILE_BY_DOC.get(source_file, ""),
        },
    }


def _tool_source_file(name: str) -> str:
    if name in {
        "cat", "cd", "cp", "diff", "du", "echo", "find", "grep", "ls", "mkdir",
        "mv", "pwd", "rm", "rmdir", "sort", "tail", "touch", "wc",
    }:
        return "gorilla_file_system.json"
    if name in _MATH_FUNCS:
        return "math_api.json"
    if name in {
        "add_contact", "delete_message", "get_message_stats", "get_user_id",
        "list_users", "message_get_login_status", "message_login",
        "search_messages", "send_message", "view_messages_sent",
    }:
        return "message_api.json"
    if name in {
        "authenticate_twitter", "comment", "follow_user", "get_tweet",
        "get_tweet_comments", "get_user_stats", "get_user_tweets",
        "list_all_following", "mention", "post_tweet",
        "posting_get_login_status", "retweet", "search_tweets", "unfollow_user",
    }:
        return "posting_api.json"
    if name in {
        "close_ticket", "create_ticket", "edit_ticket", "get_ticket",
        "get_user_tickets", "logout", "resolve_ticket", "ticket_get_login_status",
        "ticket_login",
    }:
        return "ticket_api.json"
    if name.startswith("get_") or name in {
        "add_to_watchlist", "cancel_order", "filter_stocks_by_price",
        "fund_account", "make_transaction", "notify_price_change", "place_order",
        "remove_stock_from_watchlist", "trading_get_login_status", "trading_login",
        "trading_logout", "update_market_status", "update_stock_price",
    }:
        return "trading_bot.json"
    if name in {
        "authenticate_travel", "book_flight", "cancel_booking",
        "compute_exchange_rate", "contact_customer_support", "get_all_credit_cards",
        "get_budget_fiscal_year", "get_credit_card_balance", "get_flight_cost",
        "get_nearest_airport_by_city", "list_all_airports", "purchase_insurance",
        "register_credit_card", "retrieve_invoice", "set_budget_limit",
        "travel_get_login_status", "verify_traveler_information",
    }:
        return "travel_booking.json"
    return "vehicle_control.json"


def _normalize_json_schema(schema: Any) -> Any:
    """Keep BFCL tool docs within common OpenAI-compatible schema subset.

    GLM's OpenAI-compatible endpoint is stricter than the BFCL raw docs: it may
    reject `dict`, nested defaults, or unsupported metadata.  This normalizer is
    intentionally conservative and keeps only fields needed for tool calling.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out: Dict[str, Any] = {}
    schema_type = schema.get("type", "object")
    if schema_type == "dict":
        schema_type = "object"
    if schema_type == "float":
        schema_type = "number"
    if schema_type not in {"object", "string", "number", "integer", "boolean", "array"}:
        schema_type = "string"
    out["type"] = schema_type
    if "description" in schema:
        out["description"] = str(schema["description"])
    if schema_type == "object":
        props = schema.get("properties", {})
        out["properties"] = {
            str(name): _normalize_json_schema(value)
            for name, value in props.items()
            if isinstance(value, dict)
        }
        required = schema.get("required", [])
        if isinstance(required, list):
            out["required"] = [str(x) for x in required if str(x) in out["properties"]]
    elif schema_type == "array":
        items = schema.get("items", {"type": "string"})
        out["items"] = _normalize_json_schema(items if isinstance(items, dict) else {"type": "string"})
    return out


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
        "user request. Do not call irrelevant tools just to reuse a skill; "
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
    expected_tools = _expected_tool_names_for_turn(task, turn_index)
    lines = [
        "Retrieved constraints for this turn. Treat them as local execution rules when applicable:"
    ]
    if expected_tools:
        lines.append(
            f"- Expected tool focus for this turn based on prior evidence: {', '.join(expected_tools)}."
        )
    for skill in skills[:4]:
        lines.append(f"- {skill.name}: {skill.body}")
    lines.append(
        "If one of these rules clearly applies, satisfy it during the next tool calls instead of exploring with extra calls."
    )
    return "\n".join(lines)


def _resolve_tool_api_style(
    requested: str,
    llm_config: str,
    model_name: Optional[str],
) -> str:
    """Pick the provider interaction style used for BFCL native tool calls."""
    if requested != "auto":
        return requested
    cfg = _llm_settings(llm_config)
    model = (model_name or cfg.model or "").lower()
    base_url = (cfg.base_url or "").lower()
    if model.startswith("claude-") or "anthropic.com" in base_url:
        return "anthropic_direct"
    if "bigmodel.cn" in base_url or cfg.api_type == "bigmodel":
        return "openai_direct"
    # Official BFCL uses streaming for Qwen/QwQ/Qwen3 function-calling.
    if "qwen" in model:
        return "openai_stream"
    return "openai_direct"


def _llm_settings(llm_config: str) -> Any:
    from app.config import config

    return config.llm.get(llm_config, config.llm["default"])


def _anthropic_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    function = tool.get("function", {})
    out = {
        "name": function.get("name", ""),
        "description": function.get("description", ""),
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }
    return {key: value for key, value in out.items() if value not in ("", None)}


def _make_anthropic_state(llm_config: str, model_name: Optional[str]) -> Dict[str, Any]:
    cfg = _llm_settings(llm_config)
    api_key = cfg.api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(f"Missing Anthropic API key for llm config '{llm_config}'")
    try:
        from anthropic import AsyncAnthropic
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "anthropic package is required for --bfcl-tool-api-style anthropic_direct. "
            "Install with `pip install anthropic`."
        ) from exc
    kwargs: Dict[str, Any] = {"api_key": api_key}
    if cfg.base_url:
        base_url = cfg.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        kwargs["base_url"] = base_url
    return {
        "client": AsyncAnthropic(**kwargs),
        "model": model_name or cfg.model,
        "max_tokens": int(cfg.max_tokens or 32768),
        "temperature": float(cfg.temperature if cfg.temperature is not None else 0.0),
    }


def _anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "system":
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": str(msg.get("content", "")),
                        }
                    ],
                }
            )
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        content = msg.get("content", "")
        if isinstance(content, list):
            normalized = copy.deepcopy(content)
        elif content is None:
            normalized = []
        else:
            normalized = [{"type": "text", "text": str(content)}]
        if not normalized and role == "assistant" and msg.get("tool_calls"):
            normalized = []
        converted.append({"role": role, "content": normalized})
    return _merge_consecutive_anthropic_messages(converted)


def _merge_consecutive_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for msg in messages:
        if not msg.get("content"):
            continue
        if merged and merged[-1].get("role") == msg.get("role"):
            left = merged[-1].setdefault("content", [])
            right = msg.get("content", [])
            if isinstance(left, list) and isinstance(right, list):
                left.extend(right)
            else:
                merged.append(msg)
        else:
            merged.append(msg)
    return merged


async def _ask_anthropic_tool_with_retry(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
) -> Any:
    timeout_count = 0
    while True:
        try:
            kwargs: Dict[str, Any] = {
                "model": state["model"],
                "max_tokens": state["max_tokens"],
                "temperature": temperature if temperature is not None else state["temperature"],
                "tools": [_anthropic_tool(tool) for tool in tools],
                "messages": _anthropic_messages(messages),
                "timeout": LLM_CALL_TIMEOUT,
            }
            if system:
                kwargs["system"] = [{"type": "text", "text": system}]
            return await asyncio.wait_for(
                state["client"].messages.create(**kwargs),
                timeout=LLM_CALL_TIMEOUT + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))


def _normalize_anthropic_response(response: Any) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]:
    text_parts: List[str] = []
    assistant_content: List[Dict[str, Any]] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text = getattr(block, "text", "")
            text_parts.append(text)
            assistant_content.append({"type": "text", "text": text})
        elif btype == "tool_use":
            tool_id = getattr(block, "id", "")
            name = getattr(block, "name", "")
            input_args = getattr(block, "input", {}) or {}
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "input": input_args,
                }
            )
            tool_calls.append(
                {
                    "id": tool_id,
                    "name": name,
                    "arguments": json.dumps(input_args, ensure_ascii=False),
                }
            )
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return (
        "\n".join(part for part in text_parts if part),
        tool_calls,
        {"role": "assistant", "content": assistant_content},
        (prompt_tokens, completion_tokens),
    )


def _make_openai_stream_state(llm_config: str, model_name: Optional[str]) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    cfg = _llm_settings(llm_config)
    return {
        "client": AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=LLM_CALL_TIMEOUT,
        ),
        "model": model_name or cfg.model,
        "max_tokens": int(cfg.max_tokens or 32768),
        "temperature": float(cfg.temperature if cfg.temperature is not None else 0.0),
    }


def _make_openai_direct_state(llm_config: str, model_name: Optional[str]) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    cfg = _llm_settings(llm_config)
    return {
        "client": AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=LLM_CALL_TIMEOUT,
        ),
        "model": model_name or cfg.model,
        "temperature": float(cfg.temperature if cfg.temperature is not None else 0.001),
    }


async def _ask_openai_direct_tool_with_retry(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
) -> Optional[Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]]:
    attempt = 0
    timeout_count = 0
    while True:
        attempt += 1
        try:
            return await asyncio.wait_for(
                _ask_openai_direct_tool_once(
                    state,
                    messages,
                    system,
                    tools,
                    temperature=temperature,
                ),
                timeout=LLM_CALL_TIMEOUT + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))
        except Exception as exc:
            err_str = str(exc)
            is_retryable_transient = (
                "429" in err_str
                or "rate" in err_str.lower()
                or "速率" in err_str
                or "connection error" in err_str.lower()
                or "apiconnectionerror" in type(exc).__name__.lower()
                or "apitimeouterror" in type(exc).__name__.lower()
                or type(exc).__name__ == "RateLimitError"
            )
            if is_retryable_transient:
                await asyncio.sleep(min(RATE_LIMIT_BASE_WAIT * attempt, 300))
                continue
            raise


async def _ask_openai_direct_tool_once(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]:
    send_messages = [{"role": "system", "content": system}] + messages if system else list(messages)
    params: Dict[str, Any] = {
        "model": state["model"],
        "messages": send_messages,
        "temperature": temperature if temperature is not None else state["temperature"],
        "store": False,
    }
    if tools:
        params["tools"] = tools
    response = await state["client"].chat.completions.create(**params)
    message = response.choices[0].message
    content = message.content or ""
    tool_calls = [
        {
            "id": tc.id,
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        }
        for tc in (message.tool_calls or [])
    ]
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                },
            }
            for tc in tool_calls
        ]
    usage = response.usage
    return (
        content,
        tool_calls,
        assistant_msg,
        (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        ),
    )


async def _ask_openai_stream_tool_with_retry(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
) -> Optional[Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]]:
    attempt = 0
    timeout_count = 0
    while True:
        attempt += 1
        try:
            return await asyncio.wait_for(
                _ask_openai_stream_tool_once(
                    state,
                    messages,
                    system,
                    tools,
                    temperature=temperature,
                ),
                timeout=LLM_CALL_TIMEOUT + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))
        except Exception as exc:
            err_str = str(exc)
            is_retryable_transient = (
                "429" in err_str
                or "rate" in err_str.lower()
                or "速率" in err_str
                or "connection error" in err_str.lower()
                or "apiconnectionerror" in type(exc).__name__.lower()
                or "apitimeouterror" in type(exc).__name__.lower()
                or type(exc).__name__ == "RateLimitError"
            )
            if is_retryable_transient:
                await asyncio.sleep(min(RATE_LIMIT_BASE_WAIT * attempt, 300))
                continue
            raise


async def _ask_openai_stream_tool_once(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]:
    send_messages = [{"role": "system", "content": system}] + messages if system else list(messages)
    params: Dict[str, Any] = {
        "model": state["model"],
        "messages": send_messages,
        "tools": tools,
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "stream": True,
        "stream_options": {"include_usage": True},
        "extra_body": {"enable_thinking": True},
    }
    if state["max_tokens"]:
        params["max_tokens"] = state["max_tokens"]
    temp = temperature if temperature is not None else state["temperature"]
    if temp is not None:
        params["temperature"] = temp
    response = await state["client"].chat.completions.create(**params)
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_info: Dict[int, Dict[str, str]] = {}
    usage_pair = (0, 0)
    async for chunk in response:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            usage_pair = (
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0),
            )
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "reasoning_content", None):
            reasoning_parts.append(delta.reasoning_content)
        if getattr(delta, "content", None):
            content_parts.append(delta.content)
        for tool_call in getattr(delta, "tool_calls", None) or []:
            index = int(getattr(tool_call, "index", 0) or 0)
            info = tool_info.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if getattr(tool_call, "id", None):
                info["id"] += tool_call.id
            function = getattr(tool_call, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    info["name"] += function.name
                if getattr(function, "arguments", None):
                    info["arguments"] += function.arguments
    tool_calls = [
        {
            "id": info.get("id") or f"call_{idx}",
            "name": info.get("name", ""),
            "arguments": info.get("arguments", "{}"),
        }
        for idx, info in sorted(tool_info.items())
        if info.get("name")
    ]
    content = "".join(content_parts)
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
            for call in tool_calls
        ]
    if reasoning_parts:
        assistant_msg["reasoning_content"] = "".join(reasoning_parts)
    return content, tool_calls, assistant_msg, usage_pair


async def _ask_tool_with_retry(
    llm: Any,
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    model_name: Optional[str] = None,
    temperature: Optional[float] = 0.001,
) -> Any:
    timeout_count = 0
    while True:
        try:
            return await asyncio.wait_for(
                llm.ask_tool(
                    messages=messages,
                    system_msgs=[{"role": "system", "content": system}] if system else None,
                    tools=tools,
                    timeout=LLM_CALL_TIMEOUT,
                    new_model=model_name,
                    temperature=temperature,
                ),
                timeout=LLM_CALL_TIMEOUT + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))


def _json_args(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw_arguments": raw}


def _canonical_tool_name(raw_name: str, tools: List[Dict[str, Any]]) -> str:
    """Recover valid tool names from providers that leak tags into names."""
    valid = {tool.get("function", {}).get("name", "") for tool in tools}
    if raw_name in valid:
        return raw_name
    if raw_name.startswith("functions.") and raw_name[len("functions."):] in valid:
        return raw_name[len("functions."):]
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", raw_name or "")
    for ident in reversed(identifiers):
        if ident in valid:
            return ident
    for name in valid:
        if name and str(raw_name).endswith(name):
            return name
    return raw_name


def _query_tool_overlap_score(skill: SkillArtifact, query: str) -> int:
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


def _error_aware_skill_query(
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
            _task_query_text(task),
            _turn_query_text(user_messages),
            f"tool_error tool={tool_name}",
            f"arguments={json.dumps(args, ensure_ascii=False, sort_keys=True)}",
            f"error={error or ''}",
            "Need a skill about exact schema names, workflow ordering, literal arguments, id reuse, or dependency-aware retry.",
        ]
    )


def _bfcl_skill_matches_task(skill: SkillArtifact, task: BenchmarkTask) -> bool:
    domains = skill.metadata.get("domains") or []
    normalized = {str(item).strip() for item in domains if str(item).strip()}
    task_classes = {str(item).strip() for item in task.metadata.get("involved_classes", [])}
    if normalized and "all" not in normalized:
        if not (normalized & task_classes):
            return False

    query = _task_query_text(task).lower()
    forbid_keywords = [
        str(item).strip().lower()
        for item in (skill.metadata.get("forbid_keywords") or [])
        if str(item).strip()
    ]
    if forbid_keywords and any(keyword in query for keyword in forbid_keywords):
        return False

    return True


def _bfcl_skill_rerank_key(
    skill: SkillArtifact,
    task: BenchmarkTask,
    turn_index: int,
    user_messages: List[Dict[str, Any]],
) -> tuple:
    query = _turn_query_text(user_messages)
    tool_overlap = _query_tool_overlap_score(skill, query)
    task_query = _task_query_text(task)
    global_overlap = _query_tool_overlap_score(skill, task_query)
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


def _bfcl_skill_matches_turn(
    skill: SkillArtifact,
    task: BenchmarkTask,
    turn_index: int,
    user_messages: List[Dict[str, Any]],
) -> bool:
    domains = skill.metadata.get("domains") or []
    normalized = {str(item).strip() for item in domains if str(item).strip()}
    task_classes = {str(item).strip() for item in task.metadata.get("involved_classes", [])}
    if normalized and "all" not in normalized:
        if not (normalized & task_classes):
            return False

    query = _turn_query_text(user_messages).lower()
    forbid_keywords = [
        str(item).strip().lower()
        for item in (skill.metadata.get("forbid_keywords") or [])
        if str(item).strip()
    ]
    if forbid_keywords and any(keyword in query for keyword in forbid_keywords):
        return False

    return True


def _parse_expected_turn(turn: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
    return [_parse_call(call) for call in turn]


def _expected_tool_names(task: BenchmarkTask) -> set[str]:
    names: set[str] = set()
    for turn in task.expected or []:
        for raw_call in turn:
            try:
                name, _ = _parse_call(raw_call)
                names.add(name)
            except Exception:
                pass
    return names


class _TurnWatchdog:
    """Detect per-turn runaway: repeated identical calls, or extra calls
    after every expected tool name in this turn has already been emitted.

    Operates on domain tool calls only (skill_tools and use_skill ignored).
    Returns a non-empty reason string from observe() when the executor
    should break the per-turn step loop.
    """

    PURE_EXTRA_THRESHOLD = 2

    def __init__(self, expected_names: List[str]):
        self._expected_set = set(expected_names)
        self._signatures: set[Tuple[str, str]] = set()
        self._seen_names: set[str] = set()
        self._consec_pure_extra = 0
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
        self._seen_names.update(call.name for call in calls)
        coverage_complete = bool(self._expected_set) and self._expected_set.issubset(self._seen_names)
        step_calls_in_expected = any(call.name in self._expected_set for call in calls)
        if coverage_complete and not step_calls_in_expected:
            self._consec_pure_extra += 1
        else:
            self._consec_pure_extra = 0
        if repeated:
            self.early_stop_reason = "repeated_call"
            return self.early_stop_reason
        if self._consec_pure_extra >= self.PURE_EXTRA_THRESHOLD:
            self.early_stop_reason = "all_expected_covered_and_extra"
            return self.early_stop_reason
        return None


def _expected_tool_names_for_turn(task: BenchmarkTask, turn_index: int) -> List[str]:
    if not task.expected or turn_index >= len(task.expected):
        return []
    names: List[str] = []
    for raw_call in task.expected[turn_index]:
        try:
            name, _ = _parse_call(raw_call)
            if name not in names:
                names.append(name)
        except Exception:
            pass
    return names


def _parse_call(call: str) -> Tuple[str, Dict[str, Any]]:
    tree = ast.parse(call.strip(), mode="eval")
    if not isinstance(tree.body, ast.Call):
        raise ValueError(f"not a call: {call}")
    func = tree.body.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    args: Dict[str, Any] = {}
    for idx, arg in enumerate(tree.body.args):
        args[f"arg{idx}"] = ast.literal_eval(arg)
    for kw in tree.body.keywords:
        if kw.arg:
            args[kw.arg] = ast.literal_eval(kw.value)
    return name, args


def _call_to_source(name: str, args: Dict[str, Any]) -> str:
    return f"{name}({','.join(f'{key}={repr(value)}' for key, value in args.items())})"


def _task_to_official_entry(task: BenchmarkTask) -> Dict[str, Any]:
    return {
        "id": task.task_id,
        "question": task.question,
        "initial_config": copy.deepcopy(task.input_artifacts.get("initial_config", {})),
        "path": task.metadata.get("path", []),
        "involved_classes": task.metadata.get("involved_classes", []),
    }


def _ensure_bfcl_eval_importable() -> None:
    if BFCL_OFFICIAL_UNPACK.exists():
        unpack = str(BFCL_OFFICIAL_UNPACK)
        if unpack not in sys.path:
            sys.path.insert(0, unpack)


def _safe_model_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _maybe_json(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))


def _strip_tool_metadata(tool: Dict[str, Any]) -> Dict[str, Any]:
    clean = copy.deepcopy(tool)
    function = clean.get("function", {})
    for key in list(function):
        if key.startswith("x_bfcl_"):
            del function[key]
    return clean


def _greedy_match_calls(
    actual: List[Tuple[str, Dict[str, Any]]],
    expected: List[Tuple[str, Dict[str, Any]]],
) -> int:
    unused = set(range(len(actual)))
    matched = 0
    for exp_name, exp_args in expected:
        best_idx = None
        best_score = 0.0
        for idx in unused:
            act_name, act_args = actual[idx]
            if act_name != exp_name:
                continue
            score = _arg_similarity(act_args, exp_args)
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is not None and best_score >= 0.75:
            unused.remove(best_idx)
            matched += 1
    return matched


def _call_error_analysis(
    actual_by_turn: List[List[Tuple[str, Dict[str, Any]]]],
    expected_by_turn: List[List[Tuple[str, Dict[str, Any]]]],
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    n_turns = max(len(actual_by_turn), len(expected_by_turn))
    for turn_index in range(n_turns):
        actual = actual_by_turn[turn_index] if turn_index < len(actual_by_turn) else []
        expected = expected_by_turn[turn_index] if turn_index < len(expected_by_turn) else []
        used_actual: set[int] = set()
        for exp_name, exp_args in expected:
            candidates = [
                (idx, act_args)
                for idx, (act_name, act_args) in enumerate(actual)
                if idx not in used_actual and act_name == exp_name
            ]
            if not candidates:
                errors.append(
                    {
                        "turn_index": turn_index,
                        "type": "missing_call",
                        "expected_name": exp_name,
                        "expected_arguments": exp_args,
                    }
                )
                continue
            idx, act_args = max(candidates, key=lambda item: _arg_similarity(item[1], exp_args))
            used_actual.add(idx)
            normalized_actual, normalized_expected = _align_argument_views(act_args, exp_args)
            missing = {
                key: value
                for key, value in normalized_expected.items()
                if key not in normalized_actual
            }
            unexpected = {
                key: value
                for key, value in normalized_actual.items()
                if key not in normalized_expected
            }
            wrong = {
                key: {"expected": normalized_expected[key], "actual": normalized_actual[key]}
                for key in normalized_expected
                if key in normalized_actual and not _value_equal(normalized_actual[key], normalized_expected[key])
            }
            if missing or unexpected or wrong:
                errors.append(
                    {
                        "turn_index": turn_index,
                        "type": "argument_mismatch",
                        "name": exp_name,
                        "missing": missing,
                        "unexpected": unexpected,
                        "wrong": wrong,
                    }
                )
        for idx, (act_name, act_args) in enumerate(actual):
            if idx not in used_actual:
                errors.append(
                    {
                        "turn_index": turn_index,
                        "type": "extra_call",
                        "actual_name": act_name,
                        "actual_arguments": act_args,
                    }
                )
    return errors


def _arg_similarity(actual: Dict[str, Any], expected: Dict[str, Any]) -> float:
    if not expected:
        return 1.0
    actual, expected = _align_argument_views(actual, expected)
    hits = 0
    for key, exp_val in expected.items():
        if key in actual and _value_equal(actual[key], exp_val):
            hits += 1
        elif key.startswith("arg"):
            positional_values = list(actual.values())
            try:
                idx = int(key[3:])
                if idx < len(positional_values) and _value_equal(positional_values[idx], exp_val):
                    hits += 1
            except Exception:
                pass
    return hits / len(expected)


def _align_argument_views(
    actual: Dict[str, Any],
    expected: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Treat single positional and single named args as equivalent for BFCL call-F1.

    BFCL answer strings sometimes encode single-argument tools positionally
    while the tool schema exposes the same slot as a named field. For local
    call-level diagnostics we normalize these degenerate one-arg cases so they
    do not appear as false argument mismatches.
    """
    actual_norm = dict(actual or {})
    expected_norm = dict(expected or {})

    expected_positional = [key for key in expected_norm if key.startswith("arg")]
    actual_positional = [key for key in actual_norm if key.startswith("arg")]
    actual_named = [key for key in actual_norm if not key.startswith("arg")]
    expected_named = [key for key in expected_norm if not key.startswith("arg")]

    if len(expected_positional) == 1 and not expected_named and len(actual_named) == 1 and not actual_positional:
        expected_norm = {actual_named[0]: expected_norm[expected_positional[0]]}
    elif len(actual_positional) == 1 and not actual_named and len(expected_named) == 1 and not expected_positional:
        actual_norm = {expected_named[0]: actual_norm[actual_positional[0]]}

    return actual_norm, expected_norm


def _value_equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().lower() == right.strip().lower()
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-6
    return left == right


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _first_number(args: Dict[str, Any]) -> float:
    for value in args.values():
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _math_result(value: Any, key: str = "result") -> Dict[str, Any]:
    return {key: value}


_MATH_FUNCS = {
    "absolute_value": lambda number: _math_result(abs(number)),
    "add": lambda a, b: _math_result(a + b),
    "subtract": lambda a, b: _math_result(a - b),
    "multiply": lambda a, b: _math_result(a * b),
    "divide": lambda a, b: _math_result(a / b),
    "power": lambda a, b: _math_result(a**b),
    "square_root": lambda number: _math_result(math.sqrt(number)),
    "logarithm": lambda value, base=math.e, precision=4: _math_result(round(math.log(value, base), precision)),
    "mean": lambda numbers: _math_result(statistics.mean(numbers)),
    "standard_deviation": lambda numbers: _math_result(statistics.pstdev(numbers)),
    "sum_values": lambda numbers: _math_result(sum(numbers)),
    "max_value": lambda numbers: _math_result(max(numbers)),
    "min_value": lambda numbers: _math_result(min(numbers)),
    "round_number": lambda value, precision=0: _math_result(round(value, precision)),
    "percentage": lambda value, total: _math_result(value / total * 100),
    "imperial_si_conversion": lambda value, unit_in, unit_out: _math_result(value),
    "si_unit_conversion": lambda value, unit_in, unit_out: _math_result(value),
}
