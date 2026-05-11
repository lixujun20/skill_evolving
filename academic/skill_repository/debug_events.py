"""Structured debug events for skill maintenance experiments."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "as_dict"):
        return _jsonable(value.as_dict())
    return str(value)


@dataclass
class DebugEventSink:
    """Collects structured events and optionally mirrors them to JSONL."""

    base_context: Dict[str, Any] = field(default_factory=dict)
    jsonl_path: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)
    _counter: int = 0
    _counter_ref: Dict[str, int] = field(default_factory=lambda: {"value": 0})

    @classmethod
    def from_env(cls, *, base_context: Dict[str, Any] | None = None) -> "DebugEventSink":
        return cls(
            base_context=dict(base_context or {}),
            jsonl_path=os.getenv("SKILL_MAINTENANCE_DEBUG_LOG", "").strip(),
        )

    def child(self, **context: Any) -> "DebugEventSink":
        return DebugEventSink(
            base_context={**self.base_context, **context},
            jsonl_path=self.jsonl_path,
            events=self.events,
            _counter=self._counter,
            _counter_ref=self._counter_ref,
        )

    def emit(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        # Child sinks share the same event list, so they must also share a
        # monotonic counter. Otherwise multiple children produce duplicate
        # event ids and the UI cannot replay the trace reliably.
        self._counter_ref["value"] = int(self._counter_ref.get("value", self._counter)) + 1
        self._counter = self._counter_ref["value"]
        event = {
            "event_id": f"debug_event_{self._counter:06d}",
            "ts": _now(),
            "event_type": event_type,
            **_jsonable(self.base_context),
            **_jsonable(payload),
        }
        self.events.append(event)
        if self.jsonl_path:
            path = Path(self.jsonl_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event


def skill_store_snapshot(store: Any) -> Dict[str, Any]:
    skills = []
    for skill in store.all() if store is not None else []:
        skills.append(
            {
                "name": skill.name,
                "version": skill.version,
                "version_kind": skill.version_kind(),
                "kind": skill.kind,
                "status": skill.status,
                "stale": bool(skill.stale),
                "retrieval_enabled": skill.retrieval_enabled(),
                "dependencies": list(skill.dependencies or []),
                "dependency_pins": [pin.as_dict() for pin in skill.dependency_pins],
                "description": skill.description,
                "intent_keywords": list(skill.metadata.get("intent_keywords") or []),
                "allowed_tools": list(skill.metadata.get("allowed_tools") or []),
                "source_task_ids": list(skill.metadata.get("source_task_ids") or []),
            }
        )
    return {
        "n_total": len(skills),
        "n_active": sum(1 for item in skills if item.get("status") == "active"),
        "n_stale": sum(1 for item in skills if item.get("stale")),
        "n_disabled": sum(1 for item in skills if item.get("status") == "disabled"),
        "skills": skills,
    }
