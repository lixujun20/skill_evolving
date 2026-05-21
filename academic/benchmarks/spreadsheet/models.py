"""SpreadsheetBench trace models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class SpreadsheetTrace:
    task_id: str
    prompt: str = ""
    code: str = ""
    stdout: str = ""
    stderr: str = ""
    elapsed_s: float = 0.0
    retrieved_skills: List[str] = field(default_factory=list)
    prompt_injected_skills: List[str] = field(default_factory=list)
    called_skill_functions: List[str] = field(default_factory=list)
    skill_code_reads: List[Dict[str, Any]] = field(default_factory=list)
    callable_skills: List[Dict[str, Any]] = field(default_factory=list)
    package_skills: List[Dict[str, Any]] = field(default_factory=list)
    prompt_context_updates: List[Dict[str, Any]] = field(default_factory=list)
    filtered_skills: List[Dict[str, Any]] = field(default_factory=list)
    injector_events: List[Dict[str, Any]] = field(default_factory=list)
    cost_events: List[Dict[str, Any]] = field(default_factory=list)
    notebook_turns: List[Dict[str, Any]] = field(default_factory=list)
    execution_mode: str = "single"
    total_tokens: int = 0
    input_tokens: int = 0
    cache_input_tokens: int = 0
    completion_tokens: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "code": self.code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "elapsed_s": self.elapsed_s,
            "retrieved_skills": self.retrieved_skills,
            "prompt_injected_skills": self.prompt_injected_skills,
            "called_skill_functions": self.called_skill_functions,
            "skill_code_reads": self.skill_code_reads,
            "callable_skills": self.callable_skills,
            "package_skills": self.package_skills,
            "prompt_context_updates": self.prompt_context_updates,
            "filtered_skills": self.filtered_skills,
            "injector_events": self.injector_events,
            "cost_events": self.cost_events,
            "notebook_turns": self.notebook_turns,
            "execution_mode": self.execution_mode,
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "cache_input_tokens": self.cache_input_tokens,
            "completion_tokens": self.completion_tokens,
        }
