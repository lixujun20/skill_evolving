"""Generic state-machine runner for skill repository maintenance.

This module is intentionally benchmark-agnostic.  It models maintenance as
roles consuming and producing named slots under an explicit transition policy.
BFCL, method validation, and future benchmarks should implement role backends
around this API instead of hard-coding a one-off pipeline.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, runtime_checkable

from academic.skill_repository.maintenance_state_machine import (
    MaintenanceState,
    PlayerElement,
    build_player_trace_from_debug_events,
    compact_debug_event_for_player,
)


@dataclass(frozen=True)
class MaintenanceSlotSpec:
    """Logical data slot consumed or produced by maintenance roles."""

    name: str
    description: str = ""
    persistent: bool = False


@dataclass(frozen=True)
class MaintenanceRoleSpec:
    """A role in the maintenance loop and its declared data dependencies."""

    name: str
    consumes: List[str] = field(default_factory=list)
    produces: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class MaintenanceActionResult:
    """Output of one role action.

    ``elements`` are player-observable state deltas.  They are not UI layout
    concepts; they are named state objects that the trace builder can expose.
    """

    frame_name: str
    summary: str = ""
    role_group: str = ""
    consumed_slots: List[str] = field(default_factory=list)
    produced_slots: List[str] = field(default_factory=list)
    condition_result: str = ""
    changed_elements: List[str] = field(default_factory=list)
    elements: List[PlayerElement | Dict[str, Any]] = field(default_factory=list)
    delta: Dict[str, Any] = field(default_factory=dict)
    is_marker_candidate: bool = True

    def as_backend_payload(self) -> Dict[str, Any]:
        return {
            "frame_name": self.frame_name,
            "summary": self.summary,
            "role_group": self.role_group,
            "consumed_slots": list(self.consumed_slots),
            "produced_slots": list(self.produced_slots),
            "condition_result": self.condition_result,
            "changed_elements": list(self.changed_elements),
            "elements": list(self.elements),
            "delta": dict(self.delta),
            "is_marker_candidate": self.is_marker_candidate,
            "source_mode": "maintenance_runner",
        }


@runtime_checkable
class MaintenanceRoleBackend(Protocol):
    """Backend protocol implemented by benchmark-specific role adapters."""

    def run(self, state: MaintenanceState) -> MaintenanceActionResult:
        """Run one action against the current maintenance state."""


@runtime_checkable
class AsyncMaintenanceRoleBackend(Protocol):
    """Async backend protocol for real LLM/executor roles."""

    async def run(self, state: MaintenanceState) -> MaintenanceActionResult:
        """Run one async action against the current maintenance state."""


@runtime_checkable
class MaintenanceTransitionPolicy(Protocol):
    """Policy that chooses the next role/action from state and last result."""

    def next_action(
        self,
        state: MaintenanceState,
        last_result: Optional[MaintenanceActionResult],
    ) -> Optional[str]:
        """Return the next action name, or ``None`` to terminate."""


@runtime_checkable
class AsyncMaintenanceTransitionPolicy(Protocol):
    """Async transition policy for dynamic live maintenance loops."""

    async def next_action(
        self,
        state: MaintenanceState,
        last_result: Optional[MaintenanceActionResult],
    ) -> Optional[str]:
        """Return the next action name, or ``None`` to terminate."""


@dataclass
class StaticTransitionPolicy:
    """Simple policy useful for tests and deterministic probes."""

    actions: List[str]

    def next_action(
        self,
        state: MaintenanceState,
        last_result: Optional[MaintenanceActionResult],
    ) -> Optional[str]:
        del last_result
        index = int(state.context.get("_runner_action_index", 0))
        if index >= len(self.actions):
            return None
        state.context["_runner_action_index"] = index + 1
        return self.actions[index]


@dataclass
class MaintenanceRunnerSpec:
    """Declarative specification for a maintenance runner instance."""

    run_id: str
    slots: List[MaintenanceSlotSpec]
    roles: List[MaintenanceRoleSpec]
    initial_context: Dict[str, Any] = field(default_factory=dict)


class MaintenanceRunner:
    """Execute maintenance roles with an explicit state transition policy."""

    def __init__(
        self,
        *,
        spec: MaintenanceRunnerSpec,
        role_backends: Mapping[str, MaintenanceRoleBackend],
        transition_policy: MaintenanceTransitionPolicy,
    ) -> None:
        self.spec = spec
        self.role_backends = dict(role_backends)
        self.transition_policy = transition_policy

    def new_state(self) -> MaintenanceState:
        state = MaintenanceState(run_id=self.spec.run_id)
        state.context.update(self.spec.initial_context)
        state.context["slot_specs"] = [slot.__dict__ for slot in self.spec.slots]
        state.context["role_specs"] = [role.__dict__ for role in self.spec.roles]
        return state

    def run(self, *, max_steps: int = 200) -> MaintenanceState:
        state = self.new_state()
        last_result: Optional[MaintenanceActionResult] = None
        for _ in range(max_steps):
            action = self.transition_policy.next_action(state, last_result)
            if action is None:
                state.terminal = True
                state.phase = "terminal"
                state.snapshot_frame(
                    name="terminal",
                    action_kind="terminal",
                    summary="Maintenance runner reached a terminal state.",
                    source_mode="maintenance_runner",
                    is_marker_candidate=True,
                )
                return state
            backend = self.role_backends.get(action)
            if backend is None:
                raise KeyError(f"No maintenance role backend registered for action: {action}")
            state.phase = action
            result = backend.run(state)
            last_result = result
            self._apply_result_to_state(state, action, result)
        raise RuntimeError(f"Maintenance runner exceeded max_steps={max_steps}")

    async def run_async(self, *, max_steps: int = 200) -> MaintenanceState:
        """Async variant for real LLM/executor pipelines.

        Backends may implement either sync ``run`` or async ``run``; awaitable
        results are awaited.  This keeps benchmark adapters thin while making
        the state machine usable for live GLM/Claude calls.
        """

        state = self.new_state()
        last_result: Optional[MaintenanceActionResult] = None
        for _ in range(max_steps):
            action = self.transition_policy.next_action(state, last_result)
            if inspect.isawaitable(action):
                action = await action
            if action is None:
                state.terminal = True
                state.phase = "terminal"
                state.snapshot_frame(
                    name="terminal",
                    action_kind="terminal",
                    summary="Maintenance runner reached a terminal state.",
                    source_mode="maintenance_runner",
                    is_marker_candidate=True,
                )
                return state
            backend = self.role_backends.get(action)
            if backend is None:
                raise KeyError(f"No maintenance role backend registered for action: {action}")
            state.phase = action
            result = backend.run(state)
            if inspect.isawaitable(result):
                result = await result
            last_result = result
            self._apply_result_to_state(state, action, result)
        raise RuntimeError(f"Maintenance runner exceeded max_steps={max_steps}")

    def _apply_result_to_state(
        self,
        state: MaintenanceState,
        action: str,
        result: MaintenanceActionResult,
    ) -> None:
        for element in result.elements:
            if isinstance(element, PlayerElement):
                state.elements[element.element_id] = element
            elif isinstance(element, dict):
                item = PlayerElement(**element)
                state.elements[item.element_id] = item
        state.snapshot_frame(
            name=result.frame_name or action,
            action_kind=action,
            summary=result.summary,
            changed_elements=result.changed_elements,
            delta=result.delta,
            role_group=result.role_group or action,
            consumed_slots=result.consumed_slots,
            produced_slots=result.produced_slots,
            condition_result=result.condition_result,
            source_mode="maintenance_runner",
            is_marker_candidate=result.is_marker_candidate,
        )


@dataclass
class DebugEventReplayBackend:
    """Role backend that replays one persisted debug event as a runner action.

    This is the bridge from existing real experiments into the generic runner
    contract.  It does not mock role output: the backend consumes the exact
    recorded event payload and exposes the same slot semantics the UI uses.
    """

    event: Dict[str, Any]

    def run(self, state: MaintenanceState) -> MaintenanceActionResult:
        event_type = str(self.event.get("event_type") or "debug_event")
        compact_event = compact_debug_event_for_player(self.event)
        role_group = role_group_for_event_type(event_type)
        consumed, produced = slots_for_role_group(role_group)
        condition = condition_from_debug_event(self.event)
        element_id = role_element_id(role_group)
        element = PlayerElement(
            element_id=element_id,
            kind="role" if element_id.startswith("role:") else "skill_store",
            label=role_group.replace("_", " ").title(),
            icon="robot",
            state={
                "status": "active",
                "last_event_id": self.event.get("event_id", ""),
                "last_event_type": event_type,
                "last_input": compact_event.get("input") or {},
                "last_output": compact_event.get("output") or {},
                "last_metrics": compact_event.get("metrics") or {},
                "last_raw_event": compact_event,
            },
        )
        return MaintenanceActionResult(
            frame_name=f"{role_group}:{self.event.get('event_id') or event_type}",
            summary=event_type,
            role_group=role_group,
            consumed_slots=consumed,
            produced_slots=produced,
            condition_result=condition,
            changed_elements=[element_id],
            elements=[element],
            delta={"event": compact_event},
            is_marker_candidate=role_group != "debug",
        )


@dataclass
class EventListTransitionPolicy:
    """Transition policy that consumes a fixed list of event action names."""

    actions: List[str]

    def next_action(
        self,
        state: MaintenanceState,
        last_result: Optional[MaintenanceActionResult],
    ) -> Optional[str]:
        del last_result
        index = int(state.context.get("_event_replay_index", 0))
        if index >= len(self.actions):
            return None
        state.context["_event_replay_index"] = index + 1
        return self.actions[index]


def build_runner_from_debug_events(
    *,
    run_id: str,
    debug_events: Iterable[Dict[str, Any]],
    initial_context: Dict[str, Any] | None = None,
) -> MaintenanceRunner:
    """Create a generic runner that replays real debug events.

    The resulting runner is useful for contract tests and for incrementally
    migrating benchmark pipelines.  Production BFCL still executes live roles
    in ``bfcl_real_maintenance_probe.py`` today, but both paths now share the
    same slot/action/result contract.
    """

    events = [dict(event) for event in debug_events]
    actions = [f"event_{idx:04d}" for idx, _event in enumerate(events)]
    backends = {
        action: DebugEventReplayBackend(event)
        for action, event in zip(actions, events)
    }
    return MaintenanceRunner(
        spec=MaintenanceRunnerSpec(
            run_id=run_id,
            slots=default_maintenance_slots(),
            roles=default_maintenance_roles(),
            initial_context=dict(initial_context or {}),
        ),
        role_backends=backends,
        transition_policy=EventListTransitionPolicy(actions),
    )


def build_runner_trace_from_debug_events(
    *,
    run_id: str,
    title: str,
    kind: str,
    debug_events: Iterable[Dict[str, Any]],
    artifacts: List[Dict[str, Any]] | None = None,
    pages: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build the same player trace through the runner-facing contract.

    For compatibility, this delegates snapshot construction to the existing
    debug-event trace builder.  ``build_runner_from_debug_events`` remains
    available for explicit contract tests; the web UI path must stay linear in
    the number of persisted events and avoid replaying large snapshots twice.
    """

    events = [dict(event) for event in debug_events]
    return build_player_trace_from_debug_events(
        run_id=run_id,
        title=title,
        kind=kind,
        debug_events=events,
        artifacts=artifacts,
        pages=pages,
    )


def default_maintenance_slots() -> List[MaintenanceSlotSpec]:
    return [
        MaintenanceSlotSpec("trace", "Executor conversation/tool trace."),
        MaintenanceSlotSpec("retrieval", "Retrieved skill candidates and scores."),
        MaintenanceSlotSpec("skill", "New or updated skill artifact."),
        MaintenanceSlotSpec("bundle", "Skill-specific maintenance test bundle."),
        MaintenanceSlotSpec("test_result", "Unit utility or regression test result."),
        MaintenanceSlotSpec("skill_store", "Persistent versioned skill repository.", persistent=True),
    ]


def default_maintenance_roles() -> List[MaintenanceRoleSpec]:
    return [
        MaintenanceRoleSpec("retriever", ["trace", "skill_store"], ["retrieval"]),
        MaintenanceRoleSpec("executor", ["retrieval", "skill_store"], ["trace"]),
        MaintenanceRoleSpec("extractor", ["trace", "skill_store"], ["skill"]),
        MaintenanceRoleSpec("bundle_builder", ["trace", "skill"], ["bundle"]),
        MaintenanceRoleSpec("unit_tester", ["skill", "bundle"], ["test_result"]),
        MaintenanceRoleSpec("refiner", ["skill", "bundle", "test_result", "skill_store"], ["skill", "skill_store"]),
        MaintenanceRoleSpec("skill_store", ["skill", "bundle", "test_result"], ["skill_store"]),
    ]


def role_group_for_event_type(event_type: str) -> str:
    event_type = str(event_type or "debug_event")
    if event_type in {"retrieval", "initial_skill_selection", "prompt_injection"}:
        return "retriever"
    if event_type.startswith("executor") or event_type in {"turn_end", "tool_call", "tool_result", "skill_use_event", "skill_tool_call"}:
        return "executor"
    if event_type.startswith("extractor"):
        return "extractor"
    if event_type.startswith("bundle_builder"):
        return "bundle_builder"
    if event_type.startswith("unit_test") or event_type.startswith("post_refine_test"):
        return "unit_tester"
    if event_type.startswith("refiner") or event_type.startswith("refine"):
        return "refiner"
    if event_type in {"store_update", "skill_delta", "fault_injection", "integration_cases_appended", "store_snapshot"}:
        return "skill_store"
    if event_type.startswith("experiment") or event_type in {"loop_start", "loop_end", "fault_target_selected"}:
        return "debug"
    return "debug"


def slots_for_role_group(role_group: str) -> tuple[List[str], List[str]]:
    mapping = {
        "retriever": (["skill_store", "trace"], ["retrieval"]),
        "executor": (["retrieval", "skill_store", "trace"], ["trace"]),
        "extractor": (["trace", "skill_store"], ["skill"]),
        "bundle_builder": (["trace", "skill"], ["bundle"]),
        "unit_tester": (["skill", "bundle"], ["test_result"]),
        "refiner": (["skill", "bundle", "test_result", "skill_store"], ["skill", "skill_store"]),
        "skill_store": (["skill", "bundle", "test_result"], ["skill_store"]),
    }
    return mapping.get(role_group, ([], []))


def condition_from_debug_event(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    if event_type in {"fault_injection", "integration_cases_appended"}:
        return event_type
    output = event.get("output") if isinstance(event.get("output"), Mapping) else {}
    aggregate = output.get("aggregate") if isinstance(output.get("aggregate"), Mapping) else {}
    if aggregate.get("pass_all_tests") is not None:
        return "pass" if aggregate.get("pass_all_tests") else "fail"
    if output.get("pass_all_tests") is not None:
        return "pass" if output.get("pass_all_tests") else "fail"
    decision = output.get("decision") if isinstance(output.get("decision"), Mapping) else {}
    action = decision.get("action") or output.get("action")
    return str(action or "")


def role_element_id(role_group: str) -> str:
    return {
        "retriever": "role:retriever",
        "executor": "role:executor",
        "extractor": "role:extractor",
        "bundle_builder": "role:bundle_builder",
        "unit_tester": "role:unit_tester",
        "refiner": "role:refiner",
        "skill_store": "skill_store",
    }.get(role_group, "role:executor")
