"""Detailed STA scenarios for method validation.

The STA block validates lazy downstream dependency handling:

- STA-C01: upstream update marks downstream stale.
- STA-C02: stale downstream is resolved before executor injection.
- STA-C03: incompatible upstream update pins the downstream to a legacy version.

These scenarios are intentionally explicit about setup, action, expected
outputs, and assertions. The default path uses deterministic resolver payloads
so the state-machine can be tested offline. A real-LLM runner can reuse the same
scenario objects and replace ``resolver_payload`` with a call to
``resolve_stale_skill_llm``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from academic.method_validation.assertions import make_result_for_bundle, make_skill
from academic.skill_repository.llm_maintenance import apply_stale_payload
from academic.skill_repository.store import ArtifactStore
from academic.skill_repository.types import SkillArtifact, SkillTestResult


@dataclass
class StaScenarioReport:
    case_id: str
    query: str
    setup: Dict[str, Any]
    action: Dict[str, Any]
    expected: Dict[str, Any]
    observed: Dict[str, Any]
    passed: bool
    assertions: List[str] = field(default_factory=list)


def run_sta_c01_upstream_marks_downstream_stale() -> StaScenarioReport:
    query = "Use the downstream booking update rule."
    store = ArtifactStore()
    store.add(make_skill("id_contract", body="Return booking_id from lookup."))
    store.add(
        make_skill(
            "booking_update_rule",
            body="Use id_contract before calling update_booking.",
            dependencies=["id_contract"],
        )
    )

    store.add(make_skill("id_contract", body="Return reservation_id instead of booking_id.", version_kind="major"))
    downstream = _require_skill(store, "booking_update_rule")

    observed = {
        "downstream_stale": downstream.stale,
        "downstream_status": downstream.status,
        "stale_due_to": downstream.metadata.get("stale_due_to"),
        "upstream_history_len": len(_require_skill(store, "id_contract").history),
    }
    assertions = [
        "downstream skill is marked stale",
        "downstream status is stale",
        "stale metadata records upstream dependency and version kind",
        "upstream old version is retained in history",
    ]
    passed = (
        observed["downstream_stale"] is True
        and observed["downstream_status"] == "stale"
        and observed["stale_due_to"]["dependency"] == "id_contract"
        and observed["stale_due_to"]["version_kind"] == "major"
        and observed["upstream_history_len"] >= 1
    )
    return StaScenarioReport(
        case_id="STA-C01",
        query=query,
        setup={
            "skills": ["id_contract@v1", "booking_update_rule@v1 depends_on id_contract"],
            "upstream_update": "id_contract major update changes output id contract",
        },
        action={"operation": "ArtifactStore.add(id_contract@v2 major)"},
        expected={
            "booking_update_rule.stale": True,
            "booking_update_rule.status": "stale",
            "legacy_upstream_retained": True,
        },
        observed=observed,
        passed=passed,
        assertions=assertions,
    )


def run_sta_c02_lazy_resolver_pin_legacy() -> StaScenarioReport:
    query = "Update the booking using the old booking_id-based workflow."
    store = _stale_store()
    stale_skill = _require_skill(store, "booking_update_rule")
    retrieval_audit = store.retrieve_audit(query, top_k=3)

    resolver_payload = {
        "action": "pin_legacy",
        "reason": "The upstream id_contract major update changed booking_id to reservation_id, but this downstream skill still expects booking_id.",
        "pinned_dependencies": [
            {"skill_name": "id_contract", "pinned_version": 1, "compatibility_mode": "pinned"}
        ],
        "artifact_updates": {},
    }
    resolved = apply_stale_payload(stale_skill, resolver_payload)
    store.add(resolved)
    current = _require_skill(store, "booking_update_rule")

    observed = {
        "retrieved_stale_candidate": any(
            row["name"] == "booking_update_rule" and row["stale"] for row in retrieval_audit["candidates"]
        ),
        "resolver_action": resolver_payload["action"],
        "resolved_stale": current.stale,
        "resolved_status": current.status,
        "dependency_pins": [pin.as_dict() for pin in current.dependency_pins],
    }
    passed = (
        observed["retrieved_stale_candidate"] is True
        and observed["resolver_action"] == "pin_legacy"
        and observed["resolved_stale"] is False
        and observed["resolved_status"] == "active"
        and observed["dependency_pins"] == [
            {"skill_name": "id_contract", "min_version": None, "pinned_version": 1, "compatibility_mode": "pinned"}
        ]
    )
    return StaScenarioReport(
        case_id="STA-C02",
        query=query,
        setup={
            "store": "booking_update_rule is stale because id_contract had a major update",
            "resolver": "offline deterministic payload chooses pin_legacy",
        },
        action={
            "retrieval": "retrieve stale booking_update_rule",
            "resolver_payload": resolver_payload,
            "apply": "apply_stale_payload then store.add",
        },
        expected={
            "stale_candidate_seen_before_injection": True,
            "resolver_action": "pin_legacy",
            "dependency_pin": "id_contract@v1",
            "resolved_status": "active",
        },
        observed=observed,
        passed=passed,
        assertions=[
            "retriever exposes stale candidate",
            "resolver decision happens before using the stale skill",
            "pin_legacy clears stale and records dependency pin",
        ],
    )


def run_sta_c03_pinned_dependency_recorded_in_test_result() -> tuple[StaScenarioReport, SkillTestResult]:
    query = "Replay bundle for booking_update_rule with id_contract pinned to v1."
    store = _stale_store()
    stale_skill = _require_skill(store, "booking_update_rule")
    resolved = apply_stale_payload(
        stale_skill,
        {
            "action": "pin_legacy",
            "reason": "Legacy downstream contract still requires id_contract@v1.",
            "pinned_dependencies": [
                {"skill_name": "id_contract", "pinned_version": 1, "compatibility_mode": "pinned"}
            ],
            "artifact_updates": {},
        },
    )
    store.add(resolved)
    current = _require_skill(store, "booking_update_rule")
    result = make_result_for_bundle(current, result_id="STA-C03.result")

    observed = {
        "dependency_versions": result.dependency_versions,
        "skill_version": result.skill_version,
        "bundle_version": result.bundle_version,
        "utility_label": result.counterfactual["utility_label"],
    }
    passed = observed["dependency_versions"] == {"id_contract": 1} and observed["utility_label"] == "work"
    report = StaScenarioReport(
        case_id="STA-C03",
        query=query,
        setup={
            "resolved_skill": "booking_update_rule active with id_contract pinned to v1",
            "bundle": current.bundle.as_dict(),
        },
        action={"operation": "make_result_for_bundle(current)"},
        expected={
            "dependency_versions": {"id_contract": 1},
            "utility_label": "work",
        },
        observed=observed,
        passed=passed,
        assertions=[
            "test result records dependency_versions",
            "with/without replay still reports utility_label under correctness+cost rule",
        ],
    )
    return report, result


def _stale_store() -> ArtifactStore:
    store = ArtifactStore()
    store.add(make_skill("id_contract", body="Return booking_id from lookup."))
    store.add(
        make_skill(
            "booking_update_rule",
            body="Use id_contract booking_id before calling update_booking.",
            dependencies=["id_contract"],
        )
    )
    store.add(make_skill("id_contract", body="Return reservation_id from lookup.", version_kind="major"))
    return store


def _require_skill(store: ArtifactStore, name: str) -> SkillArtifact:
    artifact = store.get(name)
    if artifact is None:
        raise AssertionError(f"missing skill {name}")
    return artifact

