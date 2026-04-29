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
from academic.config import LLM_CALL_TIMEOUT, LLM_TIMEOUT_RETRIES

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
    name: str
    arguments: Dict[str, Any]
    turn_index: int
    tool_call_id: str = ""
    result: Any = None
    error: Optional[str] = None

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
    task_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    turns: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[BFCLToolCall] = field(default_factory=list)
    skill_events: List[Dict[str, Any]] = field(default_factory=list)
    retrieved_skills: List[str] = field(default_factory=list)
    total_tokens: int = 0
    completion_tokens: int = 0
    elapsed_s: float = 0.0
    timed_out: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "messages": self.messages,
            "turns": self.turns,
            "tool_calls": [call.as_dict() for call in self.tool_calls],
            "skill_events": self.skill_events,
            "retrieved_skills": self.retrieved_skills,
            "total_tokens": self.total_tokens,
            "completion_tokens": self.completion_tokens,
            "elapsed_s": self.elapsed_s,
            "timed_out": self.timed_out,
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


async def run_bfcl_task(
    task: BenchmarkTask,
    *,
    llm_config: str,
    tools: List[Dict[str, Any]],
    artifact_store: Optional[ArtifactStore] = None,
    top_k_skills: int = 5,
    max_steps_per_turn: int = 8,
    adapter_mode: str = "official",
    model_name: Optional[str] = None,
    enable_skill_tool: bool = False,
    execution_backend: str = "auto",
    prompt_style: str = "native",
    temperature: Optional[float] = None,
    synthetic_continue: bool = False,
    tool_api_style: str = "auto",
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
    query_text = _task_query_text(task)
    retrieved = artifact_store.retrieve(query_text, top_k=top_k_skills) if artifact_store else []
    trace.retrieved_skills = [skill.name for skill in retrieved]
    state_summary = (
        _summarize_initial_state(task.input_artifacts.get("initial_config", {}))
        if adapter_mode == "debug_hints"
        else "(hidden; use tool results and user-provided values)"
    )
    skill_prompt = artifact_store.build_prompt(retrieved) if artifact_store else "(none)"
    if prompt_style == "native":
        system = _native_skill_system(skill_prompt) if retrieved else ""
        turn_instruction = ""
    elif prompt_style == "official":
        system = BFCL_OFFICIAL_SYSTEM.format(skills=skill_prompt)
        turn_instruction = OFFICIAL_TURN_INSTRUCTION
    elif prompt_style == "academic":
        system = BFCL_SYSTEM.format(skills=skill_prompt, state_summary=state_summary)
        turn_instruction = TURN_INSTRUCTION
    else:
        raise ValueError(f"Unknown BFCL prompt_style: {prompt_style}")
    messages: List[Dict[str, Any]] = []
    if resolved_api_style == "anthropic_direct":
        llm = None
        anthropic_state = _make_anthropic_state(llm_config, model_name)
        prompt_tokens_used = 0
        completion_tokens_used = 0
    else:
        llm = LLM(config_name=llm_config)
        tokens_before = llm.total_input_tokens + llm.total_completion_tokens
        completion_before = llm.total_completion_tokens

    try:
        for turn_index, user_messages in enumerate(task.question):
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
            turn_calls_before = len(trace.tool_calls)
            for step in range(max_steps_per_turn):
                if resolved_api_style == "anthropic_direct":
                    response = await _ask_anthropic_tool_with_retry(
                        anthropic_state,
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
                if not tool_calls:
                    break
                for tc in tool_calls:
                    raw_name = tc["name"]
                    tool_name = _canonical_tool_name(raw_name, tools)
                    args = _json_args(tc["arguments"])
                    if tool_name == "use_skill":
                        event = {
                            "turn_index": turn_index,
                            "tool_call_id": tc["id"],
                            "skill_name": str(args.get("skill_name", "")),
                            "reason": str(args.get("reason", "")),
                            "raw_arguments": args,
                        }
                        trace.skill_events.append(event)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": json.dumps({"status": "recorded", **event}, ensure_ascii=False),
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
            trace.turns.append(
                {
                    "turn_index": turn_index,
                    "user_messages": user_messages,
                    "tool_calls": [
                        call.as_dict()
                        for call in trace.tool_calls
                        if call.turn_index == turn_index
                    ],
                }
            )
            if trace.timed_out:
                break
    except Exception as exc:
        trace.elapsed_s = round(time.monotonic() - t0, 3)
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
    if resolved_api_style == "anthropic_direct":
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
    explicit_used = [
        event.get("skill_name", "")
        for event in trace.skill_events
        if event.get("skill_name")
    ]
    inferred_used = _infer_used_skill_names(trace, retrieved)
    score["used_skills"] = sorted(set(explicit_used + inferred_used))
    score["skill_events"] = trace.skill_events
    score["adapter_mode"] = adapter_mode
    score["execution_backend"] = env.backend_name
    score["prompt_style"] = prompt_style
    score["temperature"] = temperature
    score["synthetic_continue"] = synthetic_continue
    score["tool_api_style"] = resolved_api_style
    if model_name:
        score["model_name"] = model_name
    official_check = score_bfcl_official(trace.tool_calls, task)
    score["official_valid"] = official_check.get("valid")
    score["official_error_type"] = official_check.get("error_type")
    score["official_check"] = official_check
    score["available_tool_count"] = len([t for t in tools if t.get("function", {}).get("name") != "use_skill"])
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
        "You may use the following retrieved skill notes as lightweight guidance for "
        "tool selection and argument construction. Do not call irrelevant tools just "
        "to reuse a skill; prioritize the user's current request and the provided "
        f"tool schemas.\n\n{skill_prompt}"
    )


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
            missing = {key: value for key, value in exp_args.items() if key not in act_args}
            unexpected = {key: value for key, value in act_args.items() if key not in exp_args}
            wrong = {
                key: {"expected": exp_args[key], "actual": act_args[key]}
                for key in exp_args
                if key in act_args and not _value_equal(act_args[key], exp_args[key])
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


def _infer_used_skill_names(trace: BFCLTrace, retrieved: List[SkillArtifact]) -> List[str]:
    text = json.dumps(trace.as_dict(), ensure_ascii=False).lower()
    return [skill.name for skill in retrieved if skill.name.lower() in text]


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
