"""State-machine and player-frame primitives for skill maintenance.

The v1 state machine keeps existing benchmark backends as action executors,
but makes the maintenance loop observable as a sequence of deterministic
frames.  The UI consumes the same frame model for experiments and method tests.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional


MAX_PLAYER_STRING = 1600
MAX_PLAYER_LIST = 30
MAX_PLAYER_DICT = 80
MAX_PLAYER_DEPTH = 5
DELTA_TRACE_FRAME_THRESHOLD = 50
PLAYER_EVENT_TEXT_LIMIT = 700
PLAYER_EVENT_LIST_LIMIT = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlayerElement:
    element_id: str
    kind: str
    label: str
    icon: str
    state: Dict[str, Any] = field(default_factory=dict)
    position: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["state"] = _compact_payload(payload.get("state") or {})
        return payload


@dataclass
class PlayerFrame:
    frame_id: str
    index: int
    name: str
    action_kind: str
    summary: str = ""
    role_group: str = ""
    consumed_slots: List[str] = field(default_factory=list)
    produced_slots: List[str] = field(default_factory=list)
    condition_result: str = ""
    source_mode: str = ""
    is_marker_candidate: bool = False
    changed_elements: List[str] = field(default_factory=list)
    highlighted_elements: List[str] = field(default_factory=list)
    delta: Dict[str, Any] = field(default_factory=dict)
    elements: Dict[str, PlayerElement] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["delta"] = _compact_payload(payload.get("delta") or {})
        payload["elements"] = {
            key: value.as_dict() if isinstance(value, PlayerElement) else value
            for key, value in self.elements.items()
        }
        return payload


@dataclass
class MaintenanceState:
    run_id: str
    phase: str = "init"
    step_index: int = 0
    terminal: bool = False
    context: Dict[str, Any] = field(default_factory=dict)
    elements: Dict[str, PlayerElement] = field(default_factory=dict)
    frames: List[PlayerFrame] = field(default_factory=list)
    pending_actions: List[str] = field(default_factory=list)

    def snapshot_frame(
        self,
        *,
        name: str,
        action_kind: str,
        summary: str = "",
        changed_elements: Iterable[str] = (),
        delta: Optional[Dict[str, Any]] = None,
        role_group: str = "",
        consumed_slots: Iterable[str] = (),
        produced_slots: Iterable[str] = (),
        condition_result: str = "",
        source_mode: str = "",
        is_marker_candidate: bool = False,
    ) -> PlayerFrame:
        changed = list(changed_elements)
        frame = PlayerFrame(
            frame_id=f"{self.run_id}:frame:{len(self.frames):04d}",
            index=len(self.frames),
            name=name,
            action_kind=action_kind,
            summary=summary,
            role_group=role_group,
            consumed_slots=list(consumed_slots),
            produced_slots=list(produced_slots),
            condition_result=condition_result,
            source_mode=source_mode,
            is_marker_candidate=is_marker_candidate,
            changed_elements=changed,
            highlighted_elements=changed,
            delta=copy.deepcopy(delta or {}),
            elements=copy.deepcopy(self.elements),
        )
        self.frames.append(frame)
        self.step_index += 1
        return frame

    def as_trace(self, *, title: str = "", kind: str = "maintenance") -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": kind,
            "title": title or self.run_id,
            "terminal": self.terminal,
            "current_phase": self.phase,
            "elements": {
                key: value.as_dict() for key, value in self.elements.items()
            },
            "frames": [frame.as_dict() for frame in self.frames],
        }


ActionBackend = Callable[[MaintenanceState], Dict[str, Any]]


class MaintenanceStateMachine:
    """Small deterministic state machine shell.

    ``pending_actions`` is the explicit transition table for v1.  Existing
    BFCL procedures can be wrapped as action backends while the broader
    maintenance loop is migrated away from hard-coded orchestration.
    """

    def __init__(self, backends: Dict[str, ActionBackend] | None = None) -> None:
        self.backends = dict(backends or {})

    def choose_next_action(self, state: MaintenanceState) -> str:
        if state.terminal:
            return "terminal"
        if state.pending_actions:
            return state.pending_actions[0]
        return "terminal"

    def step(self, state: MaintenanceState) -> MaintenanceState:
        action = self.choose_next_action(state)
        if action == "terminal":
            state.terminal = True
            state.phase = "terminal"
            state.snapshot_frame(
                name="terminal",
                action_kind="terminal",
                summary="Maintenance state machine reached a terminal state.",
                changed_elements=[],
            )
            return state
        state.pending_actions = state.pending_actions[1:]
        state.phase = action
        backend = self.backends.get(action)
        result = backend(state) if backend else {}
        for element in result.get("elements", []) or []:
            if isinstance(element, PlayerElement):
                state.elements[element.element_id] = element
            elif isinstance(element, dict):
                item = PlayerElement(**element)
                state.elements[item.element_id] = item
        state.snapshot_frame(
            name=result.get("frame_name") or action,
            action_kind=action,
            summary=result.get("summary") or "",
            changed_elements=result.get("changed_elements") or [],
            delta=result.get("delta") or result,
            role_group=result.get("role_group") or _role_group_for_action(action),
            consumed_slots=result.get("consumed_slots") or [],
            produced_slots=result.get("produced_slots") or [],
            condition_result=result.get("condition_result") or "",
            source_mode=result.get("source_mode") or "state_machine",
            is_marker_candidate=bool(result.get("is_marker_candidate", True)),
        )
        return state


def element_from_payload(
    element_id: str,
    *,
    kind: str,
    label: str,
    icon: str,
    state: Dict[str, Any],
    x: int,
    y: int,
) -> PlayerElement:
    return PlayerElement(
        element_id=element_id,
        kind=kind,
        label=label,
        icon=icon,
        state=_compact_payload(state),
        position={"x": x, "y": y},
    )


def compact_debug_event_for_player(event: Dict[str, Any]) -> Dict[str, Any]:
    """Compact a persisted debug event for timeline/player transport.

    The experiment result JSON is the source of truth for full audit payloads.
    Player frames should carry enough structured input/output for visual
    inspection, but must not duplicate full prompts, traces, or stores on every
    frame.
    """

    event_type = str(event.get("event_type") or "debug_event")
    compact: Dict[str, Any] = {
        "event_id": event.get("event_id", ""),
        "event_type": event_type,
        "experiment": event.get("experiment", ""),
        "loop_index": event.get("loop_index"),
        "turn_index": event.get("turn_index"),
        "step_index": event.get("step_index"),
        "task_id": event.get("task_id", ""),
        "phase": event.get("phase", ""),
    }
    for key in ("trigger", "cycle_kind", "attempt", "ts"):
        if key in event:
            compact[key] = _compact_event_value(event.get(key), depth=0)
    if isinstance(event.get("input"), dict):
        compact["input"] = _compact_event_mapping(event.get("input") or {}, event_type=event_type)
    if isinstance(event.get("output"), dict):
        compact["output"] = _compact_event_mapping(event.get("output") or {}, event_type=event_type)
    if isinstance(event.get("metrics"), dict):
        compact["metrics"] = _compact_event_mapping(event.get("metrics") or {}, event_type=event_type)
    compact["raw_event_ref"] = {
        "event_id": compact["event_id"],
        "event_type": event_type,
        "note": "Full event is stored in the source experiment result JSON.",
    }
    return compact


def _compact_event_mapping(payload: Dict[str, Any], *, event_type: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        key_str = str(key)
        if key_str in {"messages", "user_messages"} and isinstance(value, list):
            out[key_str] = [_compact_message(item) for item in value[:PLAYER_EVENT_LIST_LIMIT]]
            if len(value) > PLAYER_EVENT_LIST_LIMIT:
                out[key_str].append({"_truncated_items": len(value) - PLAYER_EVENT_LIST_LIMIT})
        elif key_str in {"candidates", "selected", "retrieved_unique", "turn_prompt_skills"} and isinstance(value, list):
            out[key_str] = _compact_skill_like_list(value)
        elif key_str == "prompt_skills_by_turn" and isinstance(value, list):
            out[key_str] = [
                {
                    "turn_index": idx,
                    "skills": _compact_skill_like_list(skills if isinstance(skills, list) else []),
                }
                for idx, skills in enumerate(value[:PLAYER_EVENT_LIST_LIMIT])
            ]
            if len(value) > PLAYER_EVENT_LIST_LIMIT:
                out[key_str].append({"_truncated_turns": len(value) - PLAYER_EVENT_LIST_LIMIT})
        elif key_str in {"store_after", "store_after_injection", "skills_after_refine", "store_summary"}:
            out[key_str] = _compact_store_like(value)
        elif key_str in {"maintenance_test_results", "post_refine_test_results", "unit_case_runs"} and isinstance(value, list):
            out[key_str] = [_compact_test_like(item) for item in value[:PLAYER_EVENT_LIST_LIMIT] if isinstance(item, dict)]
            if len(value) > PLAYER_EVENT_LIST_LIMIT:
                out[key_str].append({"_truncated_items": len(value) - PLAYER_EVENT_LIST_LIMIT})
        elif key_str == "maintenance_rounds" and isinstance(value, list):
            out[key_str] = [_compact_maintenance_round(item) for item in value[:PLAYER_EVENT_LIST_LIMIT] if isinstance(item, dict)]
            if len(value) > PLAYER_EVENT_LIST_LIMIT:
                out[key_str].append({"_truncated_items": len(value) - PLAYER_EVENT_LIST_LIMIT})
        elif key_str in {"system", "raw_response", "reason", "broken_body"} and isinstance(value, str):
            out[key_str] = _clip_text(value, PLAYER_EVENT_TEXT_LIMIT)
        else:
            out[key_str] = _compact_event_value(value, depth=0)
    return out


def _compact_event_value(value: Any, *, depth: int) -> Any:
    if depth >= 3:
        return _compact_leaf(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _clip_text(value, PLAYER_EVENT_TEXT_LIMIT)
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        items = [_compact_event_value(item, depth=depth + 1) for item in value[:PLAYER_EVENT_LIST_LIMIT]]
        if len(value) > PLAYER_EVENT_LIST_LIMIT:
            items.append({"_truncated_items": len(value) - PLAYER_EVENT_LIST_LIMIT})
        return items
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 24:
                out["_truncated_fields"] = len(value) - 24
                break
            out[str(key)] = _compact_event_value(item, depth=depth + 1)
        return out
    return _compact_leaf(value)


def _clip_text(text: Any, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + f"... [truncated {len(value) - limit} chars]"


def _compact_message(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {"content": _clip_text(item, PLAYER_EVENT_TEXT_LIMIT)}
    return {
        "role": item.get("role", ""),
        "content": _clip_text(item.get("content", ""), PLAYER_EVENT_TEXT_LIMIT),
    }


def _compact_skill_like_list(items: List[Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in items[:PLAYER_EVENT_LIST_LIMIT]:
        if not isinstance(item, dict):
            rows.append({"value": _clip_text(item, 180)})
            continue
        rows.append(
            {
                "name": item.get("name") or item.get("skill_name", ""),
                "version": item.get("version"),
                "version_kind": item.get("version_kind", ""),
                "kind": item.get("kind", ""),
                "status": item.get("status", ""),
                "stale": item.get("stale"),
                "description": _clip_text(item.get("description", ""), 220),
                "score": item.get("score"),
                "rank": item.get("rank"),
                "selected": item.get("selected"),
                "predicate_passed": item.get("predicate_passed"),
                "filter_reason": _clip_text(item.get("filter_reason", ""), 180),
                "allowed_tools": item.get("allowed_tools") or (item.get("metadata") or {}).get("allowed_tools") or [],
                "intent_keywords": item.get("intent_keywords") or (item.get("metadata") or {}).get("intent_keywords") or [],
            }
        )
    if len(items) > PLAYER_EVENT_LIST_LIMIT:
        rows.append({"_truncated_items": len(items) - PLAYER_EVENT_LIST_LIMIT})
    return rows


def _compact_store_like(value: Any) -> Any:
    if not isinstance(value, dict):
        return _compact_event_value(value, depth=0)
    skills = value.get("skills") or []
    return {
        "n_total": value.get("n_total", len(skills)),
        "n_active": value.get("n_active"),
        "n_stale": value.get("n_stale"),
        "n_disabled": value.get("n_disabled"),
        "skills": _compact_skill_like_list(skills if isinstance(skills, list) else []),
    }


def _compact_skill_artifacts_for_store(skills: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    """Store shelf summary used in player state.

    Full artifact cards remain available from /api/maintenance/experiment.
    The player store only needs navigation fields and compact body/interface
    snippets to avoid duplicating every bundle and raw snapshot in frame zero.
    """

    rows: List[Dict[str, Any]] = []
    for item in skills or []:
        if not isinstance(item, dict):
            continue
        bundle = item.get("bundle") if isinstance(item.get("bundle"), dict) else {}
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        rows.append(
            {
                "name": item.get("name") or item.get("skill_name", ""),
                "kind": item.get("kind", ""),
                "description": _clip_text(item.get("description", ""), 260),
                "body": _clip_text(item.get("body") or item.get("implementation") or "", 900),
                "status": item.get("status", ""),
                "version": item.get("version"),
                "version_kind": item.get("version_kind", ""),
                "stale": item.get("stale"),
                "dependencies": item.get("dependencies") or [],
                "dependency_pins": item.get("dependency_pins") or [],
                "bundle_id": item.get("bundle_id") or bundle.get("bundle_id", ""),
                "bundle_version": item.get("bundle_version") or bundle.get("bundle_version"),
                "bundle_counts": item.get("bundle_counts")
                or {
                    "positive": len(bundle.get("positive_cases") or []),
                    "negative": len(bundle.get("negative_cases") or []),
                    "integration": len(bundle.get("integration_cases") or []),
                },
                "interface": _compact_event_value(item.get("interface") or {}, depth=0),
                "lineage": _compact_event_value(item.get("lineage") or {}, depth=0),
                "intent_keywords": item.get("intent_keywords") or metadata.get("intent_keywords") or [],
                "allowed_tools": item.get("allowed_tools") or metadata.get("allowed_tools") or [],
                "source_task_ids": item.get("source_task_ids") or metadata.get("source_task_ids") or [],
                "retrieved_count": item.get("retrieved_count") or item.get("retrieval_count") or 0,
            }
        )
    return rows


def _compact_test_like(item: Dict[str, Any]) -> Dict[str, Any]:
    unit_runs = item.get("unit_case_runs") or []
    integration_failures = item.get("integration_failures") or []
    if isinstance(integration_failures, (int, float)):
        n_integration_failures = int(integration_failures)
    elif isinstance(integration_failures, list):
        n_integration_failures = len(integration_failures)
    else:
        n_integration_failures = 1 if integration_failures else 0
    return {
        "result_id": item.get("result_id"),
        "skill_name": item.get("skill_name"),
        "skill_version": item.get("skill_version"),
        "bundle_id": item.get("bundle_id"),
        "bundle_version": item.get("bundle_version"),
        "aggregate": _compact_event_value(item.get("aggregate") or {}, depth=0),
        "counterfactual": _compact_event_value(item.get("counterfactual") or {}, depth=0),
        "n_unit_case_runs": len(unit_runs) if isinstance(unit_runs, list) else 0,
        "unit_case_runs": [_compact_case_run(run) for run in unit_runs[:PLAYER_EVENT_LIST_LIMIT] if isinstance(run, dict)] if isinstance(unit_runs, list) else [],
        "n_integration_failures": n_integration_failures,
    }


def _compact_case_run(run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": run.get("case_id"),
        "variant": run.get("variant"),
        "passed": run.get("passed"),
        "validity": run.get("validity"),
        "accuracy": run.get("accuracy"),
        "tokens": run.get("tokens"),
        "steps": run.get("steps"),
        "failure_summary": _clip_text(run.get("failure_summary", ""), 240),
        "trace_ref": run.get("trace_ref"),
        "trace_summary": _compact_event_value(run.get("trace_summary") or {}, depth=0),
        "tool_calls": _compact_event_value(run.get("tool_calls") or [], depth=0),
        "has_io_payload": bool(run.get("input_payload") or run.get("actual_output") or run.get("expected_behavior")),
    }


def _compact_maintenance_round(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "maintenance_round": item.get("maintenance_round"),
        "targets": item.get("targets") or [],
        "integration_cases_appended": item.get("integration_cases_appended"),
        "maintenance_test_results": [_compact_test_like(row) for row in (item.get("maintenance_test_results") or [])[:PLAYER_EVENT_LIST_LIMIT] if isinstance(row, dict)],
        "post_refine_test_results": [_compact_test_like(row) for row in (item.get("post_refine_test_results") or [])[:PLAYER_EVENT_LIST_LIMIT] if isinstance(row, dict)],
        "refine_decisions": _compact_event_value(item.get("refine_decisions") or [], depth=0),
    }


def _compact_payload(value: Any, *, depth: int = 0) -> Any:
    """Keep player payloads interactive without losing structural meaning."""

    if depth >= MAX_PLAYER_DEPTH:
        return _compact_leaf(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_PLAYER_STRING else value[:MAX_PLAYER_STRING] + f"... [truncated {len(value) - MAX_PLAYER_STRING} chars]"
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        items = [_compact_payload(item, depth=depth + 1) for item in value[:MAX_PLAYER_LIST]]
        if len(value) > MAX_PLAYER_LIST:
            items.append({"_truncated_items": len(value) - MAX_PLAYER_LIST})
        return items
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= MAX_PLAYER_DICT:
                out["_truncated_fields"] = len(value) - MAX_PLAYER_DICT
                break
            out[str(key)] = _compact_payload(item, depth=depth + 1)
        return out
    if hasattr(value, "as_dict"):
        return _compact_payload(value.as_dict(), depth=depth)
    return _compact_leaf(value)


def _compact_leaf(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_PLAYER_STRING else value[:MAX_PLAYER_STRING] + f"... [truncated {len(value) - MAX_PLAYER_STRING} chars]"
    if isinstance(value, (list, tuple)):
        return {"_type": "list", "_length": len(value), "_preview": [_compact_leaf(item) for item in list(value)[:5]]}
    if isinstance(value, dict):
        keys = list(value.keys())
        return {"_type": "object", "_field_count": len(keys), "_keys": [str(key) for key in keys[:20]]}
    return str(value)


def build_player_trace_from_pages(
    *,
    run_id: str,
    title: str,
    kind: str,
    pages: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Best-effort player trace for legacy result files.

    New state-machine runs should persist frames directly.  Legacy maintenance
    results only contain page/card summaries, so this function maps each card
    into a deterministic frame and keeps element snapshots current enough for
    UI playback.
    """

    state = MaintenanceState(run_id=run_id)
    state.elements["role:executor"] = element_from_payload(
        "role:executor",
        kind="role",
        label="Executor",
        icon="robot",
        state={"status": "idle"},
        x=120,
        y=120,
    )
    state.elements["skill_store"] = element_from_payload(
        "skill_store",
        kind="skill_store",
        label="Skill Store",
        icon="shelf",
        state={"skills": _compact_skill_artifacts_for_store(artifacts or [])},
        x=420,
        y=110,
    )
    state.snapshot_frame(
        name="init",
        action_kind="init",
        summary="Initial environment state.",
        changed_elements=["role:executor", "skill_store"],
        role_group="init",
        produced_slots=["trace", "skill_store"],
        source_mode="legacy_pages",
        is_marker_candidate=True,
    )

    for page_index, page in enumerate(pages or []):
        page_id = str(page.get("page_id") or page_index)
        page_element = f"trace:{page_id}"
        state.elements[page_element] = element_from_payload(
            page_element,
            kind="trace",
            label=page.get("label") or f"Turn {page_index + 1}",
            icon="scroll",
            state={
                "page_id": page_id,
                "label": page.get("label") or f"Turn {page_index + 1}",
                "title": page.get("title", ""),
                "status_tone": page.get("status_tone", ""),
                "summary_metrics": copy.deepcopy(page.get("summary_metrics") or []),
                "n_flow_cards": len(page.get("flow_cards") or []),
            },
            x=120,
            y=300 + page_index * 38,
        )
        state.snapshot_frame(
            name=f"turn_{page_index + 1}_selected",
            action_kind="turn_selected",
            summary=page.get("title") or page_id,
            changed_elements=[page_element],
            delta={"page_id": page_id, "summary_metrics": page.get("summary_metrics") or []},
            role_group="executor",
            consumed_slots=["skill_store"],
            produced_slots=["trace"],
            source_mode="legacy_pages",
            is_marker_candidate=True,
        )
        for card_index, card in enumerate(page.get("flow_cards") or []):
            action_kind = _card_action_kind(card)
            changed = _apply_card_to_elements(state, card, page_index, card_index)
            semantics = _action_semantics(action_kind, {"card": card})
            state.snapshot_frame(
                name=f"{action_kind}:{card.get('title') or card.get('type') or card_index}",
                action_kind=action_kind,
                summary=card.get("subtitle") or card.get("title") or "",
                changed_elements=changed,
                delta={
                    "page_id": page_id,
                    "card": _compact_event_value(copy.deepcopy(card), depth=0),
                },
                role_group=semantics["role_group"],
                consumed_slots=semantics["consumed_slots"],
                produced_slots=semantics["produced_slots"],
                condition_result=semantics["condition_result"],
                source_mode="legacy_pages",
                is_marker_candidate=semantics["is_marker_candidate"],
            )
    state.terminal = True
    state.phase = "terminal"
    trace = state.as_trace(title=title, kind=kind)
    trace["source_mode"] = "legacy_pages"
    return trace


def build_player_trace_from_debug_events(
    *,
    run_id: str,
    title: str,
    kind: str,
    debug_events: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]] | None = None,
    pages: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build a player trace from structured state-machine/debug events.

    This is the preferred adapter for new maintenance runs. Each emitted
    event becomes one frame, and the element snapshots are updated by applying
    event-shaped deltas to long-lived roles/artifacts. Older runs without
    debug events should use ``build_player_trace_from_pages``.
    """

    # Structured runs should show store evolution over time.  Do not seed the
    # init frame with final artifacts; store_update/extractor/bundle/refine
    # events below reconstruct the store state frame by frame.
    state = _initial_player_state(run_id, artifacts=[], pages=pages)
    init_frame = state.snapshot_frame(
        name="init",
        action_kind="init",
        summary="Initial player state reconstructed before applying debug events.",
        changed_elements=list(state.elements.keys()),
        role_group="init",
        produced_slots=["trace", "skill_store"],
        source_mode="debug_events",
        is_marker_candidate=True,
    )
    frames: List[Dict[str, Any]] = []
    initial_elements = {
        key: value.as_dict() if isinstance(value, PlayerElement) else value
        for key, value in state.elements.items()
    }
    init_payload = init_frame.as_dict()
    init_payload["elements"] = copy.deepcopy(initial_elements)
    init_payload["element_deltas"] = copy.deepcopy(initial_elements)
    frames.append(init_payload)
    seen_ids: Dict[str, int] = {}
    for idx, event in enumerate(debug_events or []):
        raw_id = str(event.get("event_id") or f"debug_event_{idx + 1:06d}")
        seen_ids[raw_id] = seen_ids.get(raw_id, 0) + 1
        event_id = raw_id if seen_ids[raw_id] == 1 else f"{raw_id}#{seen_ids[raw_id]}"
        event = {**copy.deepcopy(event), "event_id": event_id}
        compact_event = compact_debug_event_for_player(event)
        action_kind = _event_action_kind(event)
        semantics = _action_semantics(action_kind, {"event": event})
        role_id = _event_role_id(event)
        changed = [role_id]
        frame = PlayerFrame(
            frame_id=f"{run_id}:frame:{len(frames):04d}",
            index=len(frames),
            name=f"{action_kind}:{event_id}",
            action_kind=action_kind,
            summary=_event_summary(event),
            role_group=semantics["role_group"],
            consumed_slots=semantics["consumed_slots"],
            produced_slots=semantics["produced_slots"],
            condition_result=semantics["condition_result"],
            source_mode="debug_events",
            is_marker_candidate=semantics["is_marker_candidate"],
            changed_elements=changed,
            highlighted_elements=changed,
            delta={"event": compact_event},
            elements={},
        ).as_dict()
        frame["element_deltas"] = {
            role_id: _player_element_event_stub(state.elements.get(role_id), role_id, compact_event)
        }
        frames.append(frame)
        state.step_index += 1
    state.terminal = True
    state.phase = "terminal"
    return {
        "run_id": run_id,
        "kind": kind,
        "title": title or run_id,
        "terminal": True,
        "current_phase": state.phase,
        "source_mode": "debug_events",
        "snapshot_mode": "delta",
        "initial_elements": initial_elements,
        "elements": initial_elements,
        "frames": frames,
    }


def _player_element_timeline_stub(element: PlayerElement) -> Dict[str, Any]:
    state = element.state or {}
    return {
        "element_id": element.element_id,
        "kind": element.kind,
        "label": element.label,
        "icon": element.icon,
        "position": dict(element.position or {}),
        "state": {
            "status": state.get("status"),
            "last_event_id": state.get("last_event_id"),
            "last_event_type": state.get("last_event_type"),
        },
    }


def _player_element_event_stub(element: PlayerElement | None, element_id: str, compact_event: Dict[str, Any]) -> Dict[str, Any]:
    label = element.label if element else element_id.replace("role:", "").replace("_", " ").title()
    icon = element.icon if element else "robot"
    kind = element.kind if element else ("role" if element_id.startswith("role:") else "skill_store")
    position = dict(element.position or {}) if element else {}
    return {
        "element_id": element_id,
        "kind": kind,
        "label": label,
        "icon": icon,
        "position": position,
        "state": {
            "status": "active",
            "last_event_id": compact_event.get("event_id"),
            "last_event_type": compact_event.get("event_type"),
        },
    }


def build_player_trace(
    *,
    run_id: str,
    title: str,
    kind: str,
    payload: Dict[str, Any],
    pages: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    debug_events = payload.get("debug_events") or []
    if debug_events:
        return build_player_trace_from_debug_events(
            run_id=run_id,
            title=title,
            kind=kind,
            debug_events=debug_events,
            artifacts=artifacts,
            pages=pages,
        )
    return build_player_trace_from_pages(
        run_id=run_id,
        title=title,
        kind=kind,
        pages=pages,
        artifacts=artifacts,
    )


def _initial_player_state(
    run_id: str,
    *,
    artifacts: List[Dict[str, Any]] | None,
    pages: List[Dict[str, Any]] | None,
) -> MaintenanceState:
    state = MaintenanceState(run_id=run_id)
    for element in [
        element_from_payload(
            "role:executor",
            kind="role",
            label="Executor",
            icon="robot",
            state={"status": "idle", "role": "executor"},
            x=120,
            y=120,
        ),
        element_from_payload(
            "role:retriever",
            kind="role",
            label="Retriever",
            icon="radar",
            state={"status": "idle", "role": "retriever"},
            x=315,
            y=120,
        ),
        element_from_payload(
            "role:extractor",
            kind="role",
            label="Extractor",
            icon="robot",
            state={"status": "idle", "role": "extractor"},
            x=510,
            y=120,
        ),
        element_from_payload(
            "role:bundle_builder",
            kind="role",
            label="Bundle Builder",
            icon="robot",
            state={"status": "idle", "role": "bundle_builder"},
            x=705,
            y=120,
        ),
        element_from_payload(
            "role:unit_tester",
            kind="role",
            label="Unit Tester",
            icon="tester",
            state={"status": "idle", "role": "unit_tester"},
            x=900,
            y=120,
        ),
        element_from_payload(
            "role:refiner",
            kind="role",
            label="Refiner",
            icon="robot",
            state={"status": "idle", "role": "refiner"},
            x=1095,
            y=120,
        ),
        element_from_payload(
            "skill_store",
            kind="skill_store",
            label="Skill Store",
            icon="shelf",
            state={"skills": _compact_skill_artifacts_for_store(artifacts or [])},
            x=510,
            y=330,
        ),
    ]:
        state.elements[element.element_id] = element
    for idx, page in enumerate(pages or []):
        element_id = f"trace:{page.get('page_id') or idx}"
        state.elements[element_id] = element_from_payload(
            element_id,
            kind="trace",
            label=page.get("label") or f"Turn {idx + 1}",
            icon="scroll",
            state={
                "page_id": page.get("page_id") or idx,
                "label": page.get("label") or f"Turn {idx + 1}",
                "title": page.get("title", ""),
                "status_tone": page.get("status_tone", ""),
                "summary_metrics": copy.deepcopy(page.get("summary_metrics") or []),
                "n_flow_cards": len(page.get("flow_cards") or []),
            },
            x=120 + (idx % 3) * 210,
            y=530 + (idx // 3) * 110,
        )
    return state


def _event_action_kind(event: Dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "debug_event")
    if event_type in {"retrieval", "initial_skill_selection", "prompt_injection"}:
        return "retrieval_step"
    if event_type.startswith("executor") or event_type in {"turn_end", "tool_call", "tool_result", "skill_use_event", "skill_tool_call"}:
        return "executor_step"
    if event_type.startswith("extractor"):
        return "extractor_step"
    if event_type.startswith("bundle_builder"):
        return "bundle_builder_step"
    if event_type.startswith("unit_test") or event_type.startswith("post_refine_test"):
        return "unit_test_step"
    if event_type.startswith("refiner") or event_type.startswith("refine"):
        return "refiner_step"
    if event_type in {"store_update", "skill_delta", "fault_injection", "integration_cases_appended", "store_snapshot"}:
        return "skill_store_step"
    if event_type in {"prompt_reinjection", "executor_watchdog_break"}:
        return "executor_step"
    if event_type.startswith("experiment"):
        return "experiment_step"
    return "debug_event"


def _event_role_id(event: Dict[str, Any]) -> str:
    action_kind = _event_action_kind(event)
    if action_kind == "retrieval_step":
        return "role:retriever"
    if action_kind == "extractor_step":
        return "role:extractor"
    if action_kind == "bundle_builder_step":
        return "role:bundle_builder"
    if action_kind == "unit_test_step":
        return "role:unit_tester"
    if action_kind == "refiner_step":
        return "role:refiner"
    if action_kind == "skill_store_step":
        return "skill_store"
    if action_kind == "experiment_step":
        return "trace"
    return "role:executor"


def _apply_debug_event_to_elements(
    state: MaintenanceState,
    event: Dict[str, Any],
    idx: int,
) -> List[str]:
    changed: List[str] = []
    event_type = str(event.get("event_type") or "debug_event")
    compact_event = compact_debug_event_for_player(event)
    role_id = _event_role_id(event)
    existing = state.elements.get(role_id)
    role_state = copy.deepcopy(existing.state if existing else {})
    role_state = _update_role_visual_state(role_state, compact_event, role_id=role_id)
    role_state.update(
        {
            "status": "active",
            "last_event_id": event.get("event_id", ""),
            "last_event_type": event_type,
            "last_input": copy.deepcopy(compact_event.get("input") or {}),
            "last_output": copy.deepcopy(compact_event.get("output") or {}),
            "last_metrics": copy.deepcopy(compact_event.get("metrics") or {}),
            "last_raw_event": copy.deepcopy(compact_event),
            "loop_index": event.get("loop_index"),
            "turn_index": event.get("turn_index"),
            "step_index": event.get("step_index"),
        }
    )
    state.elements[role_id] = element_from_payload(
        role_id,
        kind="role" if role_id.startswith("role:") else "skill_store",
        label=(existing.label if existing else role_id.replace("role:", "").replace("_", " ").title()),
        icon=(existing.icon if existing else "robot"),
        state=role_state,
        x=(existing.position.get("x", 120) if existing else 120),
        y=(existing.position.get("y", 120) if existing else 120),
    )
    changed.append(role_id)

    if event_type == "retrieval":
        element_id = f"retrieval:{event.get('event_id')}"
        state.elements[element_id] = element_from_payload(
            element_id,
            kind="retrieval",
            label=f"Retrieval {event.get('turn_index', '')}",
            icon="radar",
            state={
                "input": copy.deepcopy(compact_event.get("input") or {}),
                "audit": copy.deepcopy(compact_event.get("output") or {}),
                "trigger": event.get("trigger", ""),
            },
            x=315,
            y=260 + (idx % 6) * 18,
        )
        changed.append(element_id)
    elif event_type in {"executor_step", "tool_call", "tool_result", "turn_end", "executor_start", "executor_end", "executor_exception"}:
        element_id = f"trace_event:{event.get('event_id')}"
        state.elements[element_id] = element_from_payload(
            element_id,
            kind="trace_event",
            label=event_type.replace("_", " ").title(),
            icon="scroll",
            state=copy.deepcopy(compact_event),
            x=120,
            y=260 + (idx % 8) * 16,
        )
        changed.append(element_id)
    elif event_type in {"extractor_done", "store_update"}:
        _update_store_from_event(state, event, changed)
    elif event_type in {"bundle_builder_done"}:
        for bundle in ((event.get("output") or {}).get("bundles") or []):
            name = str(bundle.get("skill_name") or f"bundle_{idx}")
            element_id = f"bundle:{name}"
            state.elements[element_id] = element_from_payload(
                element_id,
                kind="bundle",
                label=f"Bundle {name}",
                icon="box",
                state=copy.deepcopy(bundle),
                x=705,
                y=285 + len([key for key in state.elements if key.startswith("bundle:")]) * 50,
            )
            changed.append(element_id)
        _update_store_from_event(state, event, changed)
    elif event_type in {"unit_test_done", "post_refine_test_done"}:
        result = compact_event.get("output") or {}
        name = str(result.get("skill_name") or event.get("event_id") or idx)
        element_id = f"test_result:{name}:{event.get('event_id')}"
        state.elements[element_id] = element_from_payload(
            element_id,
            kind="test_result",
            label=f"Test {name}",
            icon="clipboard",
            state=copy.deepcopy(result),
            x=900,
            y=285 + len([key for key in state.elements if key.startswith("test_result:")]) * 42,
        )
        changed.append(element_id)
    elif event_type in {"refiner_done", "integration_cases_appended"}:
        _update_store_from_event(state, event, changed)
    return changed


def _update_role_visual_state(role_state: Dict[str, Any], compact_event: Dict[str, Any], *, role_id: str) -> Dict[str, Any]:
    """Maintain a stable role-local state board for the player UI.

    Raw debug events are deltas. The UI needs a persistent role state so that
    executor messages, retrieval candidates, unit-test results, and refiner
    decisions remain readable while the timeline moves across event types.
    """

    next_state = copy.deepcopy(role_state or {})
    input_payload = compact_event.get("input") or {}
    output_payload = compact_event.get("output") or {}
    event_type = str(compact_event.get("event_type") or "")
    role_name = role_id.replace("role:", "")

    visible_messages = list(next_state.get("visible_messages") or [])
    new_messages: List[Dict[str, Any]] = []
    for message in _messages_from_payload(input_payload, default_role="user"):
        new_messages.append(message)
    for message in _messages_from_output(output_payload):
        new_messages.append(message)
    for message in new_messages:
        if not _message_seen(visible_messages, message):
            visible_messages.append(message)
    visible_messages = visible_messages[-16:]

    summary_items = _role_summary_items(role_name, input_payload, output_payload, compact_event)
    next_state["role_state"] = {
        "role": role_name,
        "event_type": event_type,
        "event_id": compact_event.get("event_id", ""),
        "turn_index": compact_event.get("turn_index"),
        "step_index": compact_event.get("step_index"),
        "loop_index": compact_event.get("loop_index"),
        "phase": compact_event.get("phase", ""),
        "visible_messages": visible_messages,
        "new_messages": new_messages,
        "tool_calls": _tool_calls_from_output(output_payload),
        "tool_results": _tool_results_from_event(event_type, input_payload, output_payload),
        "summary_items": summary_items,
        "metrics": copy.deepcopy(compact_event.get("metrics") or {}),
        "raw_ref": copy.deepcopy(compact_event.get("raw_event_ref") or {}),
    }
    next_state["visible_messages"] = visible_messages
    return next_state


def _messages_from_payload(payload: Dict[str, Any], *, default_role: str) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("messages", "user_messages", "prompt_messages", "conversation"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return [_normalize_message(item, default_role=default_role) for item in value if isinstance(item, dict)]
    if payload.get("query"):
        return [{"role": "query", "content": _clip_text(payload.get("query"), PLAYER_EVENT_TEXT_LIMIT)}]
    if payload.get("prompt"):
        return [{"role": "prompt", "content": _clip_text(payload.get("prompt"), PLAYER_EVENT_TEXT_LIMIT)}]
    return []


def _messages_from_output(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    messages: List[Dict[str, Any]] = []
    assistant = payload.get("assistant_message")
    if isinstance(assistant, dict):
        messages.append(_normalize_message(assistant, default_role="assistant"))
    elif payload.get("content"):
        messages.append({"role": "assistant", "content": _clip_text(payload.get("content"), PLAYER_EVENT_TEXT_LIMIT)})
    for key, role in (
        ("system", "system"),
        ("skill_prompt", "skill_prompt"),
        ("turn_instruction", "turn_instruction"),
        ("reason", "reason"),
    ):
        if payload.get(key):
            messages.append({"role": role, "content": _clip_text(payload.get(key), PLAYER_EVENT_TEXT_LIMIT)})
    return messages


def _normalize_message(item: Dict[str, Any], *, default_role: str) -> Dict[str, Any]:
    content = item.get("content", "")
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_result":
                    parts.append(f"tool_result({block.get('tool_use_id', '')}): {block.get('content', '')}")
                elif block.get("text"):
                    parts.append(str(block.get("text")))
                else:
                    parts.append(jsonish_compact(block))
            else:
                parts.append(str(block))
        content = "\n".join(parts)
    return {
        "role": item.get("role") or default_role,
        "content": _clip_text(content, PLAYER_EVENT_TEXT_LIMIT),
        "tool_call_id": item.get("tool_call_id", ""),
    }


def jsonish_compact(value: Any) -> str:
    text = str(_compact_event_value(value, depth=0))
    return _clip_text(text, PLAYER_EVENT_TEXT_LIMIT)


def _message_seen(messages: List[Dict[str, Any]], message: Dict[str, Any]) -> bool:
    key = (message.get("role"), message.get("content"), message.get("tool_call_id"))
    return any((item.get("role"), item.get("content"), item.get("tool_call_id")) == key for item in messages)


def _tool_calls_from_output(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    calls = payload.get("tool_calls") or (payload.get("assistant_message") or {}).get("tool_calls") or []
    return copy.deepcopy(calls if isinstance(calls, list) else [])


def _tool_results_from_event(event_type: str, input_payload: Dict[str, Any], output_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if event_type not in {"tool_result", "skill_tool_call", "skill_use_event", "tool_call"}:
        return []
    result = copy.deepcopy(output_payload or input_payload or {})
    return [result] if result else []


def _role_summary_items(
    role_name: str,
    input_payload: Dict[str, Any],
    output_payload: Dict[str, Any],
    compact_event: Dict[str, Any],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    event_type = str(compact_event.get("event_type") or "")
    rows.append({"label": "Event", "value": event_type})
    if compact_event.get("trigger"):
        rows.append({"label": "Trigger", "value": compact_event.get("trigger")})
    if compact_event.get("turn_index") is not None:
        rows.append({"label": "Turn", "value": compact_event.get("turn_index")})
    if compact_event.get("step_index") is not None:
        rows.append({"label": "Step", "value": compact_event.get("step_index")})

    if role_name == "retriever":
        store = output_payload.get("store_summary") or {}
        rows.extend(
            [
                {"label": "Query", "value": input_payload.get("query") or output_payload.get("query") or ""},
                {"label": "Store", "value": f"active={store.get('n_active', '?')} disabled={store.get('n_disabled', '?')} total={store.get('n_total', '?')}"},
                {"label": "Selected", "value": _join_names(output_payload.get("selected") or []) or "none"},
                {"label": "Candidates", "value": _join_candidate_scores(output_payload.get("candidates") or []) or "none"},
            ]
        )
    elif role_name == "extractor":
        rows.extend(
            [
                {"label": "New Skills", "value": ", ".join(output_payload.get("new_skill_names") or []) or "none"},
                {"label": "Updated Skills", "value": ", ".join(output_payload.get("updated_skill_names") or []) or "none"},
            ]
        )
    elif role_name == "bundle_builder":
        rows.extend(
            [
                {"label": "Targets", "value": _join_compact_values(output_payload.get("targets") or input_payload.get("targets") or []) or "none"},
                {"label": "Bundles", "value": _bundle_summary(output_payload.get("bundles") or []) or "none"},
            ]
        )
    elif role_name == "unit_tester":
        aggregate = output_payload.get("aggregate") or {}
        rows.extend(
            [
                {"label": "Pass", "value": aggregate.get("pass_all_tests")},
                {"label": "Cases", "value": aggregate.get("n_cases")},
                {"label": "Utility", "value": _compact_event_value(aggregate.get("unit_utility_report") or {}, depth=0)},
            ]
        )
    elif role_name == "refiner":
        rows.extend(
            [
                {"label": "Decisions", "value": _decision_summary(output_payload.get("decisions") or []) or "none"},
                {"label": "Targets", "value": _join_compact_values(input_payload.get("selected_maintenance_targets") or input_payload.get("targets") or []) or "none"},
            ]
        )
    return [row for row in rows if row.get("value") not in (None, "")]


def _join_compact_values(items: Any) -> str:
    if not isinstance(items, list):
        return _clip_text(items, 260) if items else ""
    values: List[str] = []
    for item in items[:PLAYER_EVENT_LIST_LIMIT]:
        if isinstance(item, dict):
            values.append(str(item.get("name") or item.get("skill_name") or item.get("id") or jsonish_compact(item)))
        else:
            values.append(str(item))
    if len(items) > PLAYER_EVENT_LIST_LIMIT:
        values.append(f"... +{len(items) - PLAYER_EVENT_LIST_LIMIT} more")
    return ", ".join(values)


def _join_names(items: List[Any]) -> str:
    names = []
    for item in items:
        if isinstance(item, dict):
            names.append(str(item.get("name") or item.get("skill_name") or "skill"))
        else:
            names.append(str(item))
    return ", ".join(names)


def _join_candidate_scores(items: List[Any]) -> str:
    rows = []
    for item in items[:PLAYER_EVENT_LIST_LIMIT]:
        if not isinstance(item, dict):
            rows.append(str(item))
            continue
        rows.append(f"{item.get('name') or 'skill'} score={item.get('score', '?')} rank={item.get('rank', '?')} {item.get('filter_reason') or ''}".strip())
    return "\n".join(rows)


def _bundle_summary(items: List[Any]) -> str:
    rows = []
    for item in items:
        if isinstance(item, dict):
            rows.append(f"{item.get('skill_name') or item.get('bundle_id') or 'bundle'} +{item.get('positive', 0)} -{item.get('negative', 0)} int={item.get('integration', 0)}")
    return "\n".join(rows)


def _decision_summary(items: List[Any]) -> str:
    rows = []
    for item in items:
        if isinstance(item, dict):
            rows.append(f"{item.get('skill_name') or 'skill'} -> {item.get('action') or 'decision'}: {_clip_text(item.get('reason', ''), 260)}")
    return "\n".join(rows)


def _update_store_from_event(
    state: MaintenanceState,
    event: Dict[str, Any],
    changed: List[str],
) -> None:
    store = state.elements.get("skill_store")
    next_state = copy.deepcopy(store.state if store else {})
    output = event.get("output") or {}
    compact_event = compact_debug_event_for_player(event)
    compact_output = compact_event.get("output") or {}
    store_after = output.get("store_after")
    if not store_after:
        store_after = output.get("store_after_injection") or output if output.get("skills") else None
    if store_after:
        compact_store = compact_output.get("store_after") or compact_output.get("store_after_injection") or _compact_store_like(store_after)
        next_state["store_summary"] = copy.deepcopy(compact_store)
        next_state["skills"] = copy.deepcopy(compact_store.get("skills") or next_state.get("skills") or [])
    _merge_store_skill_details(next_state, output)
    if output.get("new_skill_names") is not None:
        next_state["new_skill_names"] = copy.deepcopy(output.get("new_skill_names") or [])
    next_state["last_event"] = copy.deepcopy(compact_event)
    state.elements["skill_store"] = element_from_payload(
        "skill_store",
        kind="skill_store",
        label="Skill Store",
        icon="shelf",
        state=next_state,
        x=(store.position.get("x", 510) if store else 510),
        y=(store.position.get("y", 330) if store else 330),
    )
    if "skill_store" not in changed:
        changed.append("skill_store")
    for skill in next_state.get("skills") or []:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        element_id = f"skill:{name}"
        state.elements[element_id] = element_from_payload(
            element_id,
            kind="skill",
            label=name,
            icon="card",
            state=copy.deepcopy(skill),
            x=510,
            y=430 + len([key for key in state.elements if key.startswith("skill:")]) * 42,
        )
        changed.append(element_id)


def _merge_store_skill_details(store_state: Dict[str, Any], output: Dict[str, Any]) -> None:
    """Attach rich artifact/bundle payloads to compact store rows when logged."""

    skills = store_state.get("skills")
    if not isinstance(skills, list):
        return
    by_name: Dict[str, Dict[str, Any]] = {
        str(item.get("name") or item.get("skill_name") or ""): item
        for item in skills
        if isinstance(item, dict)
    }
    for artifact in output.get("artifacts") or output.get("skills_after_refine") or []:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or artifact.get("skill_name") or "")
        if not name:
            continue
        row = by_name.setdefault(name, {"name": name})
        bundle = artifact.get("bundle") if isinstance(artifact.get("bundle"), dict) else {}
        row.update(
            {
                "name": name,
                "kind": artifact.get("kind", row.get("kind", "")),
                "description": artifact.get("description", row.get("description", "")),
                "body": artifact.get("body") or artifact.get("implementation") or row.get("body", ""),
                "interface": copy.deepcopy(artifact.get("interface") or row.get("interface") or {}),
                "version": artifact.get("version", row.get("version")),
                "version_kind": artifact.get("version_kind", row.get("version_kind", "")),
                "status": artifact.get("status", row.get("status", "")),
                "stale": artifact.get("stale", row.get("stale")),
                "dependencies": copy.deepcopy(artifact.get("dependencies") or row.get("dependencies") or []),
                "dependency_pins": copy.deepcopy(artifact.get("dependency_pins") or row.get("dependency_pins") or []),
                "lineage": copy.deepcopy(artifact.get("lineage") or row.get("lineage") or {}),
                "bundle_id": artifact.get("bundle_id") or bundle.get("bundle_id") or row.get("bundle_id", ""),
                "bundle_version": artifact.get("bundle_version") or bundle.get("bundle_version") or row.get("bundle_version"),
                "bundle": copy.deepcopy(bundle or row.get("bundle") or {}),
            }
        )
        if bundle:
            row["bundle_counts"] = {
                "positive": len(bundle.get("positive_cases") or []),
                "negative": len(bundle.get("negative_cases") or []),
                "integration": len(bundle.get("integration_cases") or []),
            }
    for bundle in output.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        name = str(bundle.get("skill_name") or bundle.get("name") or "")
        if not name:
            continue
        row = by_name.setdefault(name, {"name": name})
        cases = bundle.get("cases") if isinstance(bundle.get("cases"), dict) else {}
        row["bundle_id"] = bundle.get("bundle_id", row.get("bundle_id", ""))
        row["bundle_version"] = bundle.get("bundle_version", row.get("bundle_version"))
        row["bundle_counts"] = {
            "positive": bundle.get("positive", len(cases.get("positive") or [])),
            "negative": bundle.get("negative", len(cases.get("negative") or [])),
            "integration": bundle.get("integration", len(cases.get("integration") or [])),
        }
        row["bundle"] = {
            "bundle_id": bundle.get("bundle_id", ""),
            "bundle_version": bundle.get("bundle_version"),
            "positive_cases": copy.deepcopy(cases.get("positive") or []),
            "negative_cases": copy.deepcopy(cases.get("negative") or []),
            "integration_cases": copy.deepcopy(cases.get("integration") or []),
            "maintenance_notes": bundle.get("maintenance_notes", ""),
            "fixtures": copy.deepcopy(bundle.get("fixtures") or {}),
        }
    store_state["skills"] = [item for item in by_name.values() if item.get("name")]


def _event_summary(event: Dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "debug_event")
    parts = [event_type]
    for key in ("loop_index", "turn_index", "step_index", "task_id", "phase"):
        if event.get(key) is not None:
            parts.append(f"{key}={event.get(key)}")
    return " | ".join(parts)


def _role_group_for_action(action_kind: str) -> str:
    if action_kind in {"init", "terminal"}:
        return action_kind
    if "retrieval" in action_kind:
        return "retriever"
    if "executor" in action_kind or action_kind in {"turn_selected", "turn_end", "tool_call", "tool_result"}:
        return "executor"
    if "extractor" in action_kind:
        return "extractor"
    if "bundle" in action_kind:
        return "bundle_builder"
    if "unit_test" in action_kind or "maintenance_test" in action_kind or "method_test" in action_kind or "post_refine_test" in action_kind:
        return "unit_tester"
    if "refiner" in action_kind or "refine" in action_kind:
        return "refiner"
    if "skill_store" in action_kind or "store" in action_kind:
        return "skill_store"
    return "debug"


def _action_semantics(action_kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    role_group = _role_group_for_action(action_kind)
    consumed: List[str] = []
    produced: List[str] = []
    condition = ""

    if role_group == "retriever":
        consumed = ["skill_store", "trace"]
        produced = ["retrieval"]
    elif role_group == "executor":
        consumed = ["retrieval", "skill_store", "trace"]
        produced = ["trace"]
    elif role_group == "extractor":
        consumed = ["trace", "skill_store"]
        produced = ["skill"]
    elif role_group == "bundle_builder":
        consumed = ["trace", "skill"]
        produced = ["bundle"]
    elif role_group == "unit_tester":
        consumed = ["skill", "bundle"]
        produced = ["test_result"]
        event = payload.get("event") or {}
        output = event.get("output") or {}
        card = payload.get("card") or {}
        if output.get("aggregate", {}).get("pass_all_tests") is not None:
            condition = "pass" if output.get("aggregate", {}).get("pass_all_tests") else "fail"
        elif output.get("pass_all_tests") is not None:
            condition = "pass" if output.get("pass_all_tests") else "fail"
        elif card.get("passed") is not None:
            condition = "pass" if card.get("passed") else "fail"
    elif role_group == "refiner":
        consumed = ["skill", "bundle", "test_result", "skill_store"]
        produced = ["skill", "skill_store"]
    elif role_group == "skill_store":
        consumed = ["skill", "bundle", "test_result"]
        produced = ["skill_store"]
    elif role_group == "init":
        produced = ["trace", "skill_store"]

    event = payload.get("event") or {}
    event_type = str(event.get("event_type") or "")
    if event_type in {"fault_injection", "integration_cases_appended"}:
        condition = event_type
    if event_type == "refiner_done":
        output = event.get("output") or {}
        action = output.get("decision", {}).get("action") or output.get("action")
        if action:
            condition = str(action)

    return {
        "role_group": role_group,
        "consumed_slots": consumed,
        "produced_slots": produced,
        "condition_result": condition,
        "is_marker_candidate": role_group not in {"debug"} and not (
            role_group == "executor" and action_kind in {"tool_call", "tool_result"}
        ),
    }


def _delta_encode_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    frames = list(trace.get("frames") or [])
    if not frames:
        return trace
    initial_elements = copy.deepcopy(frames[0].get("elements") or {})
    compact_frames: List[Dict[str, Any]] = []
    for idx, frame in enumerate(frames):
        row = copy.deepcopy(frame)
        elements = row.pop("elements", {}) or {}
        if idx == 0:
            row["elements"] = copy.deepcopy(initial_elements)
            row["element_deltas"] = copy.deepcopy(initial_elements)
        else:
            changed = set(row.get("changed_elements") or [])
            row["elements"] = {}
            row["element_deltas"] = {
                key: copy.deepcopy(value)
                for key, value in elements.items()
                if key in changed
            }
        compact_frames.append(row)
    return {
        **trace,
        "snapshot_mode": "delta",
        "initial_elements": initial_elements,
        "frames": compact_frames,
    }


def _card_action_kind(card: Dict[str, Any]) -> str:
    mapping = {
        "run": "executor_step",
        "role_extractor": "extractor_completed",
        "role_bundle_builder": "bundle_builder_completed",
        "maintenance_test": "unit_test_completed",
        "role_refiner": "refiner_completed",
        "refine_decision": "refine_decision",
        "skill_delta": "skill_store_updated",
        "method_case": "method_test_completed",
        "debug_event": "debug_event",
    }
    return mapping.get(str(card.get("type") or ""), str(card.get("type") or "step"))


def _apply_card_to_elements(
    state: MaintenanceState,
    card: Dict[str, Any],
    page_index: int,
    card_index: int,
) -> List[str]:
    card_type = str(card.get("type") or "card")
    changed: List[str] = []
    role_id = f"role:{_role_name_for_card(card_type)}"
    state.elements[role_id] = element_from_payload(
        role_id,
        kind="role",
        label=_role_label_for_card(card_type),
        icon="robot",
        state={
            "last_card": copy.deepcopy(card),
            "last_input": copy.deepcopy((card.get("detail") or {}).get("input") or {}),
            "last_output": copy.deepcopy((card.get("detail") or {}).get("output") or {}),
        },
        x=120 + (card_index % 4) * 150,
        y=120 + (card_index % 3) * 120,
    )
    changed.append(role_id)

    if card_type == "role_extractor":
        for artifact in (((card.get("detail") or {}).get("output") or {}).get("artifacts") or []):
            name = artifact.get("name") or f"artifact_{card_index}"
            element_id = f"skill:{name}"
            state.elements[element_id] = element_from_payload(
                element_id,
                kind="skill",
                label=name,
                icon="card",
                state=copy.deepcopy(artifact),
                x=470,
                y=220 + len([key for key in state.elements if key.startswith("skill:")]) * 42,
            )
            changed.append(element_id)
    elif card_type == "role_bundle_builder":
        name = card.get("subtitle") or f"bundle_{page_index}_{card_index}"
        element_id = f"bundle:{name}"
        state.elements[element_id] = element_from_payload(
            element_id,
            kind="bundle",
            label=f"Bundle {name}",
            icon="box",
            state=copy.deepcopy((card.get("detail") or {}).get("output") or card),
            x=700,
            y=220,
        )
        changed.append(element_id)
    elif card_type in {"maintenance_test", "method_case"}:
        name = card.get("skill_name") or card.get("case_id") or f"test_{page_index}_{card_index}"
        element_id = f"test_result:{name}"
        state.elements[element_id] = element_from_payload(
            element_id,
            kind="test_result",
            label=str(name),
            icon="clipboard",
            state=copy.deepcopy(card),
            x=910,
            y=240,
        )
        changed.append(element_id)
    elif card_type == "skill_delta":
        store = state.elements.get("skill_store")
        next_state = copy.deepcopy(store.state if store else {})
        next_state["last_delta"] = copy.deepcopy(card)
        next_state["skill_names_after"] = card.get("skill_names_after") or next_state.get("skill_names_after") or []
        state.elements["skill_store"] = element_from_payload(
            "skill_store",
            kind="skill_store",
            label="Skill Store",
            icon="shelf",
            state=next_state,
            x=420,
            y=110,
        )
        changed.append("skill_store")
    return changed


def _role_name_for_card(card_type: str) -> str:
    return {
        "run": "executor",
        "role_extractor": "extractor",
        "role_bundle_builder": "bundle_builder",
        "maintenance_test": "unit_tester",
        "role_refiner": "refiner",
        "refine_decision": "refiner",
        "skill_delta": "skill_store",
        "method_case": "method_validator",
        "debug_event": "debug",
    }.get(card_type, card_type.replace("role_", "") or "role")


def _role_label_for_card(card_type: str) -> str:
    return _role_name_for_card(card_type).replace("_", " ").title()
