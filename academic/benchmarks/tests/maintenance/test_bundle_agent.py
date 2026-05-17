from __future__ import annotations

import pytest

from academic.skill_repository.llm_maintenance import (
    apply_bundle_patch_payload,
    apply_refine_payload_via_editor,
    apply_stale_payload_via_editor,
)
from academic.benchmarks.bfcl.maintenance.adapter import build_bfcl_skill_bundles_llm
from academic.skill_repository.store import ArtifactStore
from academic.skill_repository.types import SkillArtifact, SkillBundleCase


def _artifact_with_bundle() -> SkillArtifact:
    artifact = SkillArtifact(
        name="remove_watchlist_when_symbol_explicit",
        kind="atomic_tool_rule_card",
        description="Call remove_stock_from_watchlist directly when the stock symbol is explicit.",
        body="If the request already contains the exact stock symbol, skip discovery and call remove_stock_from_watchlist directly.",
        metadata={"source_task_ids": ["train_a"], "source": "llm_trace_extraction"},
    )
    artifact.bundle.positive_cases = [
        SkillBundleCase(
            case_id="skill:positive:0",
            source="manual",
            prompt="Show my watchlist.",
            expected={"official_valid": True},
            context={
                "source_task_id": "multi_turn_base_116",
                "task_fragment": {
                    "task_id": "multi_turn_base_116",
                    "question": [[{"role": "user", "content": "Could you peruse my stock watchlist and share what's on my radar right now, please?"}]],
                    "expected": [["get_watchlist()"]],
                    "input_artifacts": {"initial_config": {"TradingBot": {"authenticated": True}}},
                    "metadata": {"involved_classes": ["TradingBot"]},
                },
            },
            tags=["explicit-symbol"],
            polarity="positive",
        )
    ]
    artifact.bundle.negative_cases = [
        SkillBundleCase(
            case_id="skill:negative:0",
            source="manual",
            prompt="Remove the chip stock from my watchlist.",
            expected={"official_valid": False},
            context={"task_fragment": {"question": [[{"role": "user", "content": "Remove the chip stock from my watchlist."}]], "expected": [[]]}},
            tags=["ambiguous"],
            polarity="negative",
        )
    ]
    return artifact


def test_apply_bundle_patch_payload_updates_only_targeted_cases() -> None:
    artifact = _artifact_with_bundle()
    patched = apply_bundle_patch_payload(
        artifact,
        patch_payload={
            "drop_case_ids": ["skill:negative:0"],
            "replace_cases": [
                {
                    "bucket": "positive_cases",
                    "case_id": "skill:positive:0",
                    "source": "manual",
                    "prompt": "Remove TSLA from my watchlist.",
                    "expected": {"official_valid": True},
                    "context": {"task_fragment": {"question": [[{"role": "user", "content": "Remove TSLA from my watchlist."}]], "expected": [["remove_stock_from_watchlist(stock='TSLA')"]]}},
                    "tags": ["explicit-symbol", "patched"],
                    "polarity": "positive",
                    "contrast_protocol": {"with_skill": True, "without_skill": True},
                }
            ],
            "add_cases": {
                "integration_cases": [
                    {
                        "case_id": "skill:integration:0",
                        "source": "integration_failure",
                        "prompt": "Remove NVDA after portfolio context is already loaded.",
                        "expected": {"official_valid": True},
                        "context": {"task_fragment": {"question": [[{"role": "user", "content": "Portfolio loaded. Remove NVDA from my watchlist."}]], "expected": [["remove_stock_from_watchlist(stock='NVDA')"]]}},
                        "tags": ["integration"],
                        "polarity": "integration",
                        "contrast_protocol": {"with_skill": True, "without_skill": True},
                    }
                ]
            },
        },
        maintenance_notes="Patched one explicit-symbol case and added one integration regression case.",
    )
    assert [case.case_id for case in patched.positive_cases] == ["skill:positive:0"]
    assert patched.positive_cases[0].prompt == "Remove TSLA from my watchlist."
    assert patched.negative_cases == []
    assert [case.case_id for case in patched.integration_cases] == ["skill:integration:0"]
    assert "Patched one explicit-symbol case" in patched.maintenance_notes


@pytest.mark.asyncio
async def test_text_editor_roundtrip_preserves_skill_updates() -> None:
    artifact = _artifact_with_bundle()
    refined = await apply_refine_payload_via_editor(
        artifact,
        {
            "decision": {"action": "refine_minor", "version_kind": "minor", "reason": "tighten contract"},
            "artifact": {
                "description": "Refined description",
                "body": "Refined body",
                "metadata": {"x": 1},
            },
            "bundle": {
                "maintenance_notes": "updated",
                "positive_cases": artifact.bundle.positive_cases,
                "negative_cases": artifact.bundle.negative_cases,
                "integration_cases": artifact.bundle.integration_cases,
            },
        },
    )
    assert refined.description == "Refined description"
    assert refined.body == "Refined body"
    assert refined.metadata["x"] == 1
    assert refined.bundle.maintenance_notes == "updated"

    stale = await apply_stale_payload_via_editor(
        artifact,
        {
            "action": "refresh_minor",
            "artifact_updates": {
                "description": "Stale refreshed",
                "body": "Refreshed body",
                "metadata": {"y": 2},
            },
        },
    )
    assert stale.description == "Stale refreshed"
    assert stale.body == "Refreshed body"
    assert stale.metadata["y"] == 2


@pytest.mark.asyncio
async def test_build_bfcl_skill_bundles_llm_uses_patch_action_without_full_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact_with_bundle()
    artifact.bundle.fixtures["bundle_input_signature"] = "old-signature"
    store = ArtifactStore([artifact])

    async def fake_maintain_skill_bundle_llm(*args, **kwargs):
        return {
            "action": "patch",
            "reason": "One new integration counterexample requires a local patch.",
            "maintenance_notes": "Patched with one integration regression case.",
            "patch": {
                "drop_case_ids": [],
                "replace_cases": [],
                "add_cases": {
                    "integration_cases": [
                        {
                            "case_id": "skill:integration:0",
                            "source": "integration_failure",
                            "prompt": "Show my watchlist.",
                            "expected": {"official_valid": True},
                            "context": {
                                "source_task_id": "multi_turn_base_116",
                                "task_fragment": {
                                    "task_id": "multi_turn_base_116",
                                    "question": [[{"role": "user", "content": "Could you peruse my stock watchlist and share what's on my radar right now, please?"}]],
                                    "expected": [["get_watchlist()"]],
                                    "input_artifacts": {"initial_config": {"TradingBot": {"authenticated": True}}},
                                    "metadata": {"involved_classes": ["TradingBot"]},
                                },
                            },
                            "tags": ["integration"],
                            "polarity": "integration",
                            "contrast_protocol": {"with_skill": True, "without_skill": True},
                        }
                    ]
                },
            },
        }

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("full bundle rebuild should not be called for patch action")

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.maintain_skill_bundle_llm",
        fake_maintain_skill_bundle_llm,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.distill_skill_bundle_llm",
        fail_if_called,
    )

    train_details = [
        {
            "task_id": "train_a",
            "runs": [
                {
                    "score": 1.0,
                    "metrics": {"official_valid": True, "call_f1": 1.0, "n_model_steps": 2, "total_tokens": 40},
                    "task": {"task_id": "multi_turn_base_116", "question": [[{"role": "user", "content": "Could you peruse my stock watchlist and share what's on my radar right now, please?"}]], "expected": [["get_watchlist()"]]},
                }
            ],
        }
    ]

    await build_bfcl_skill_bundles_llm(
        store,
        train_details=train_details,
        replay_details=[],
        llm_config="test",
        model_name="test-model",
        artifact_names=[artifact.name],
    )

    updated = store.get(artifact.name)
    assert updated is not None
    assert updated.bundle.fixtures["bundle_maintainer_action"] == "patch"
    assert [case.case_id for case in updated.bundle.integration_cases] == ["skill:integration:0"]


@pytest.mark.asyncio
async def test_build_bfcl_skill_bundles_llm_drops_patch_cases_with_wrong_schema_args(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact_with_bundle()
    artifact.bundle.fixtures["bundle_input_signature"] = "old-signature"
    store = ArtifactStore([artifact])

    async def fake_maintain_skill_bundle_llm(*args, **kwargs):
        return {
            "action": "patch",
            "reason": "Bad LLM patch should be contract-filtered.",
            "maintenance_notes": "Attempted bad schema patch.",
            "patch": {
                "add_cases": {
                    "integration_cases": [
                        {
                            "case_id": "skill:integration:bad_arg",
                            "source": "integration_failure",
                            "prompt": "Remove ZETA from watchlist.",
                            "expected": {"official_valid": True},
                            "context": {
                                "source_task_id": "multi_turn_base_116",
                                "task_fragment": {
                                    "task_id": "multi_turn_base_116",
                                    "question": [[{"role": "user", "content": "I'm inclined to shake things up a bit. Let's take Zeta Corp out of the equation from my watchlist, shall we?"}]],
                                    "expected": [["remove_stock_from_watchlist(stock='ZETA')"]],
                                    "input_artifacts": {"initial_config": {"TradingBot": {"authenticated": True}}},
                                    "metadata": {"involved_classes": ["TradingBot"]},
                                },
                            },
                            "tags": ["bad-schema"],
                            "polarity": "integration",
                        }
                    ]
                },
                "drop_case_ids": [],
                "replace_cases": [],
            },
        }

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("full bundle rebuild should not be called for patch action")

    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.maintain_skill_bundle_llm",
        fake_maintain_skill_bundle_llm,
    )
    monkeypatch.setattr(
        "academic.benchmarks.bfcl.maintenance.adapter.distill_skill_bundle_llm",
        fail_if_called,
    )

    await build_bfcl_skill_bundles_llm(
        store,
        train_details=[],
        replay_details=[{"task_id": "multi_turn_base_116", "runs": [{"metrics": {"official_valid": False, "prompt_injected_skills": [artifact.name]}}]}],
        llm_config="test",
        model_name="test-model",
        artifact_names=[artifact.name],
    )

    updated = store.get(artifact.name)
    assert updated is not None
    assert updated.bundle.integration_cases == []
    dropped = updated.bundle.fixtures["bundle_contract_dropped_cases"]
    by_case_id = {item["case_id"]: item for item in dropped}
    assert by_case_id["skill:integration:bad_arg"]["reason"] == "unknown_expected_tool_argument"
