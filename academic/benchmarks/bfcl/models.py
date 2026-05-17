"""BFCL trace dataclasses shared by executor, scoring, and diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
