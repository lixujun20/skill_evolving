"""Run STA scenarios with the real LLM stale resolver.

Example:
    python -m academic.method_validation.sta_llm_runner \
      --case STA-C02 \
      --llm-config bigmodel \
      --output academic/results/method_validation/sta_c02_real_llm.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from academic.method_validation.assertions import make_result_for_bundle, make_skill
from academic.skill_repository.llm_maintenance import apply_stale_payload, resolve_stale_skill_llm
from academic.skill_repository.store import ArtifactStore


def _jsonable(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def build_sta_c02_store() -> ArtifactStore:
    store = ArtifactStore()
    store.add(
        make_skill(
            "id_contract",
            body=(
                "Legacy contract: lookup_booking returns booking_id. "
                "Downstream tools expect booking_id."
            ),
        )
    )
    store.add(
        make_skill(
            "booking_update_rule",
            body=(
                "Use id_contract to obtain booking_id, then call update_booking "
                "with booking_id. This skill depends on id_contract's legacy output."
            ),
            dependencies=["id_contract"],
        )
    )
    store.add(
        make_skill(
            "id_contract",
            body=(
                "Breaking contract: lookup_booking now returns reservation_id, "
                "not booking_id. Downstream legacy skills may need to pin v1."
            ),
            version_kind="major",
        )
    )
    return store


async def run_sta_c02_real_llm(*, llm_config: str, output: Path) -> Dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_log = output.with_suffix(".audit.jsonl")
    os.environ["SKILL_MAINTENANCE_AUDIT_LOG"] = str(audit_log)

    store = build_sta_c02_store()
    stale_skill = store.get("booking_update_rule")
    if stale_skill is None:
        raise AssertionError("missing booking_update_rule")
    retrieval_audit = store.retrieve_audit(
        "Update booking using the legacy booking_id workflow.",
        top_k=3,
        debug_context={"case_id": "STA-C02", "runner": "real_llm"},
    )
    if not stale_skill.stale:
        raise AssertionError("scenario setup failed: booking_update_rule should be stale")
    upstream_context = dict(stale_skill.metadata.get("stale_due_to") or {})
    upstream_context.update(
        {
            "old_contract": "id_contract@v1 returns booking_id",
            "new_contract": "id_contract@v2 returns reservation_id",
            "downstream_expected_contract": "booking_update_rule expects booking_id",
            "available_decisions": ["pin_legacy", "refresh_minor", "refresh_major", "keep_stale"],
        }
    )

    payload = await resolve_stale_skill_llm(
        stale_skill,
        upstream_context=upstream_context,
        llm_config=llm_config,
        audit_context={"case_id": "STA-C02", "runner": "real_llm"},
    )
    resolved = apply_stale_payload(stale_skill, payload)
    store.add(resolved)
    current = store.get("booking_update_rule")
    if current is None:
        raise AssertionError("missing resolved booking_update_rule")
    result = make_result_for_bundle(current, result_id="STA-C02.real_llm.post_resolve")

    allowed_actions = {"keep_stale", "clear_stale", "refresh_minor", "refresh_major", "pin_legacy", "rollback"}
    action = str(payload.get("action") or "")
    assertions = {
        "llm_action_allowed": action in allowed_actions,
        "audit_log_exists": audit_log.exists(),
        "resolved_not_stale_if_action_changes_contract": (
            current.stale is False if action in {"clear_stale", "refresh_minor", "refresh_major", "pin_legacy"} else True
        ),
        "dependency_snapshot_recorded": isinstance(result.dependency_versions, dict),
    }
    report = {
        "case_id": "STA-C02",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "llm_config": llm_config,
        "query": "Update booking using the legacy booking_id workflow.",
        "setup": {
            "skills_before_resolution": [_jsonable(skill) for skill in store.all()],
            "upstream_context": upstream_context,
        },
        "retrieval_audit": retrieval_audit,
        "role_calls": {
            "stale_resolver": {
                "input_artifact": _jsonable(stale_skill),
                "input_upstream_context": upstream_context,
                "output_payload": payload,
                "audit_log": str(audit_log),
            }
        },
        "post_resolution": {
            "resolved_skill": _jsonable(current),
            "test_result": _jsonable(result),
        },
        "assertions": assertions,
        "passed": all(assertions.values()),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise AssertionError(json.dumps(assertions, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real-LLM STA method validation cases.")
    parser.add_argument("--case", choices=["STA-C02"], default="STA-C02")
    parser.add_argument("--llm-config", default="bigmodel")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("academic/results/method_validation/sta_c02_real_llm.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(run_sta_c02_real_llm(llm_config=args.llm_config, output=args.output))
    print(json.dumps({"case_id": report["case_id"], "passed": report["passed"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

