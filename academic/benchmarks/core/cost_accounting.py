"""Unified token and cost accounting for benchmark runs."""
from __future__ import annotations

import copy
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


@dataclass
class PricingConfig:
    input_price_per_mtok: float = field(
        default_factory=lambda: _env_float("SKILL_EVOLVE_INPUT_PRICE_PER_MTOK", 0.0)
    )
    cache_input_price_per_mtok: float = field(
        default_factory=lambda: _env_float("SKILL_EVOLVE_CACHE_INPUT_PRICE_PER_MTOK", 0.0)
    )
    output_price_per_mtok: float = field(
        default_factory=lambda: _env_float("SKILL_EVOLVE_OUTPUT_PRICE_PER_MTOK", 0.0)
    )

    def estimate_cost(
        self,
        *,
        input_tokens: int = 0,
        cache_input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> float:
        return round(
            (
                max(0, int(input_tokens or 0)) * self.input_price_per_mtok
                + max(0, int(cache_input_tokens or 0)) * self.cache_input_price_per_mtok
                + max(0, int(output_tokens or 0)) * self.output_price_per_mtok
            )
            / 1_000_000,
            8,
        )


@dataclass
class CostEvent:
    role: str
    phase: str
    benchmark: str
    task_id: str = ""
    turn_index: int | None = None
    step_index: int | None = None
    model: str = ""
    llm_config: str = ""
    input_tokens: int = 0
    cache_input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    prompt_chars: int = 0
    skill_prompt_chars: int = 0
    system_prompt_chars: int = 0
    tool_schema_chars: int = 0
    final_conversation_chars: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens or 0) + int(self.cache_input_tokens or 0) + int(self.output_tokens or 0)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        return data


def make_cost_event(
    *,
    role: str,
    phase: str,
    benchmark: str,
    task_id: str = "",
    turn_index: int | None = None,
    step_index: int | None = None,
    model: str = "",
    llm_config: str = "",
    input_tokens: int = 0,
    cache_input_tokens: int = 0,
    output_tokens: int = 0,
    prompt_chars: int = 0,
    skill_prompt_chars: int = 0,
    system_prompt_chars: int = 0,
    tool_schema_chars: int = 0,
    final_conversation_chars: int = 0,
    metadata: Dict[str, Any] | None = None,
    pricing: PricingConfig | None = None,
) -> Dict[str, Any]:
    cfg = pricing or PricingConfig()
    event = CostEvent(
        role=role,
        phase=phase,
        benchmark=benchmark,
        task_id=task_id,
        turn_index=turn_index,
        step_index=step_index,
        model=model,
        llm_config=llm_config,
        input_tokens=max(0, int(input_tokens or 0)),
        cache_input_tokens=max(0, int(cache_input_tokens or 0)),
        output_tokens=max(0, int(output_tokens or 0)),
        estimated_cost=cfg.estimate_cost(
            input_tokens=input_tokens,
            cache_input_tokens=cache_input_tokens,
            output_tokens=output_tokens,
        ),
        prompt_chars=max(0, int(prompt_chars or 0)),
        skill_prompt_chars=max(0, int(skill_prompt_chars or 0)),
        system_prompt_chars=max(0, int(system_prompt_chars or 0)),
        tool_schema_chars=max(0, int(tool_schema_chars or 0)),
        final_conversation_chars=max(0, int(final_conversation_chars or 0)),
        metadata=copy.deepcopy(dict(metadata or {})),
    )
    return event.as_dict()


def summarize_cost_events(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [dict(event or {}) for event in events or [] if isinstance(event, dict)]

    def empty() -> Dict[str, Any]:
        return {
            "n_calls": 0,
            "input_tokens": 0,
            "cache_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0.0,
            "prompt_chars": 0,
            "skill_prompt_chars": 0,
            "system_prompt_chars": 0,
            "tool_schema_chars": 0,
            "final_conversation_chars": 0,
        }

    def add(target: Dict[str, Any], event: Dict[str, Any]) -> None:
        target["n_calls"] += 1
        for key in (
            "input_tokens",
            "cache_input_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_chars",
            "skill_prompt_chars",
            "system_prompt_chars",
            "tool_schema_chars",
            "final_conversation_chars",
        ):
            target[key] += int(event.get(key) or 0)
        target["estimated_cost"] = round(
            float(target.get("estimated_cost") or 0.0) + float(event.get("estimated_cost") or 0.0),
            8,
        )

    summary = empty()
    by_role: Dict[str, Dict[str, Any]] = {}
    by_phase: Dict[str, Dict[str, Any]] = {}
    by_benchmark: Dict[str, Dict[str, Any]] = {}
    for event in rows:
        role = str(event.get("role") or "unknown")
        phase = str(event.get("phase") or "unscoped")
        benchmark = str(event.get("benchmark") or "unknown")
        add(summary, event)
        add(by_role.setdefault(role, empty()), event)
        add(by_phase.setdefault(phase, empty()), event)
        add(by_benchmark.setdefault(benchmark, empty()), event)
    return {
        "summary": summary,
        "by_role": by_role,
        "by_phase": by_phase,
        "by_benchmark": by_benchmark,
        "recent_events": rows[-20:],
    }


def cost_events_from_runs(runs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for run in runs or []:
        metrics = run.get("metrics") or {}
        trace = run.get("trace") or {}
        for row in metrics.get("cost_events") or trace.get("cost_events") or []:
            if isinstance(row, dict):
                events.append(copy.deepcopy(row))
    return events


__all__ = [
    "CostEvent",
    "PricingConfig",
    "cost_events_from_runs",
    "make_cost_event",
    "summarize_cost_events",
]
