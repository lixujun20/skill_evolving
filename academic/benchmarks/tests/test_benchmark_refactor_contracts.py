import copy
from typing import Any, Dict, List

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig
from academic.benchmarks.core.types import (
    BenchmarkResult,
    BenchmarkTask,
    SkillArtifact,
    SkillBundleCase,
    SkillInterface,
    SkillTestResult,
)
from academic.benchmarks.spreadsheet.adapter import SpreadsheetMaintenanceAdapter


def _skill(name: str, *, benchmark: str = "generic", status: str = "active") -> SkillArtifact:
    return SkillArtifact(
        name=name,
        kind="workflow_guardrail_card",
        description=f"{name} description",
        body=f"{name} body",
        interface=SkillInterface(
            summary=f"{name} summary",
            invocation_contract={"injection_type": "workflow"},
        ),
        metadata={"benchmark": benchmark, "domains": [benchmark]},
        status=status,
    )


def _spreadsheet_task(task_id: str = "sheet_contract_1") -> BenchmarkTask:
    return BenchmarkTask(
        benchmark="spreadsheet",
        task_id=task_id,
        question="Double the value in Sheet1!A1 into Sheet1!B1.",
        expected={
            "answer_sheet": "Sheet1",
            "answer_position": "B1",
            "golden_xlsx": "/tmp/golden.xlsx",
        },
        input_artifacts={
            "input_xlsx": "/tmp/input.xlsx",
            "prompt_txt": "Double A1 into B1.",
        },
        metadata={
            "instruction_type": "formula_generation",
            "data_position": "Sheet1!A1:B1",
            "spreadsheet_path": "/tmp/input.xlsx",
        },
    )


def _spreadsheet_detail(
    *,
    task: BenchmarkTask | None = None,
    retrieved: List[str] | None = None,
    injected: List[str] | None = None,
    called: List[str] | None = None,
    success: bool = False,
) -> Dict[str, Any]:
    task = task or _spreadsheet_task()
    retrieved = retrieved or []
    injected = injected if injected is not None else retrieved
    called = called or []
    result = BenchmarkResult(
        benchmark="spreadsheet",
        task_id=task.task_id,
        success=success,
        score=1.0 if success else 0.0,
        metrics={
            "answer_sheet": "Sheet1",
            "answer_position": "B1",
            "checked_cells": 1,
            "mismatched_cells": [] if success else [{"cell": "B1", "predicted": 0, "expected": 14}],
            "execution_ok": True,
            "retrieved_skills": retrieved,
            "prompt_injected_skills": injected,
            "called_skill_functions": called,
            "total_tokens": 100,
        },
        trace={
            "retrieved_skills": retrieved,
            "prompt_injected_skills": injected,
            "called_skill_functions": called,
            "code": "import openpyxl\n# mocked trace",
            "stderr": "",
            "stdout": "",
        },
    )
    return {
        "task_id": task.task_id,
        "task": task.as_dict(),
        "n_runs": 1,
        "n_success": 1 if success else 0,
        "avg_score": 1.0 if success else 0.0,
        "runs": [{**result.as_dict(), "run_idx": 0}],
    }


async def test_spreadsheet_facade_monkeypatches_still_route_into_maintenance_adapter(monkeypatch) -> None:
    task = _spreadsheet_task()
    detail = _spreadsheet_detail(task=task, retrieved=["sheet_double"], injected=["sheet_double"])
    store = ArtifactStore([_skill("sheet_double", benchmark="spreadsheet")])
    calls: List[Dict[str, Any]] = []

    async def fake_run_spreadsheet_task(*args: Any, **kwargs: Any) -> BenchmarkResult:
        calls.append({"hook": "run_task", "task_id": args[0].task_id, "top_k": kwargs.get("top_k_skills")})
        return BenchmarkResult(
            benchmark="spreadsheet",
            task_id=args[0].task_id,
            success=True,
            score=1.0,
            metrics={"hooked": True},
        )

    async def fake_ask_json(**kwargs: Any) -> Dict[str, Any]:
        calls.append({"hook": "ask_json", "role": kwargs.get("role"), "user": kwargs.get("user")})
        return {
            "skill_judgments": [
                {
                    "skill_name": "sheet_double",
                    "judgment": "harmful",
                    "effect_type": "correctness_loss",
                    "confidence": 0.91,
                    "reason": "The skill wrote zero instead of doubling A1.",
                    "evidence_strength": "strong",
                    "attribution_scope": "prompt_influence",
                    "evidence": {"retrieved": True, "injected": True, "used": False},
                    "bundle_case_suggestions": [
                        {
                            "skill_name": "sheet_double",
                            "polarity": "negative",
                            "reason": "Regression guard for the doubled-cell contract.",
                            "source_task_id": task.task_id,
                            "task_fragment_policy": "reuse_official_fragment",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.run_spreadsheet_task", fake_run_spreadsheet_task)
    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._ask_json", fake_ask_json)

    adapter = SpreadsheetMaintenanceAdapter()
    run_result = await adapter.run_task(
        task,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock", top_k_skills=3),
        phase="train",
        task_index=0,
        run_idx=0,
    )
    credit = await adapter.assign_credit(
        detail=detail,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )

    assert run_result.metrics["hooked"] is True
    assert [call["hook"] for call in calls] == ["run_task", "ask_json"]
    assert calls[0]["top_k"] == 3
    assert calls[1]["role"] == "spreadsheet_credit_assigner"
    assert credit[0]["benchmark"] == "spreadsheet"
    assert credit[0]["judgment"] == "harmful"
    assert store.get("sheet_double").evidence.harmful_cases[0]["task_id"] == task.task_id


async def test_spreadsheet_credit_bundle_and_micro_use_common_flow_with_benchmark_cases(monkeypatch) -> None:
    detail = _spreadsheet_detail(retrieved=["sheet_double"], injected=["sheet_double"], success=False)
    store = ArtifactStore([_skill("sheet_double", benchmark="spreadsheet")])
    adapter = SpreadsheetMaintenanceAdapter()
    order: List[str] = []

    credit_events = [
        {
            "benchmark": "spreadsheet",
            "task_id": detail["task_id"],
            "skill_name": "sheet_double",
            "judgment": "harmful",
            "effect_type": "correctness_loss",
            "confidence": 0.88,
            "evidence_strength": "strong",
            "reason": "Wrong formula emitted.",
            "evidence": {"retrieved": True, "injected": True},
            "bundle_case_suggestions": [
                {
                    "skill_name": "sheet_double",
                    "polarity": "negative",
                    "reason": "Keep the official SpreadsheetBench answer range as a regression case.",
                    "source_task_id": detail["task_id"],
                    "task_fragment_policy": "reuse_official_fragment",
                }
            ],
        }
    ]

    async def fake_refine_skill_artifact_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        order.append("refine")
        return {
            "decision": {
                "action": "keep",
                "reason": "Mock credit refine inspected the focused SpreadsheetBench case.",
            },
            "artifact": {},
            "bundle": {},
        }

    async def fake_bundle_tests(**kwargs: Any) -> SkillTestResult:
        order.append("bundle_test")
        artifact = kwargs["artifact"]
        return SkillTestResult(
            result_id="sheet_double_bundle_result",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id,
            bundle_version=artifact.bundle.bundle_version,
            run_label="mock_spreadsheet_bundle",
            aggregate={"passed": True, "n_cases": len(artifact.bundle.all_cases())},
        )

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.refine_skill_artifact_llm", fake_refine_skill_artifact_llm)
    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._execute_spreadsheet_bundle_tests", fake_bundle_tests)

    created = await adapter.apply_credit_bundle_cases(
        detail=detail,
        credit_events=credit_events,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )
    artifact = store.get("sheet_double")
    case = artifact.bundle.negative_cases[0]
    report = await adapter.run_micro_maintenance(
        detail=detail,
        credit_events=credit_events,
        credit_bundle_cases=created,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )

    assert created == [
        {
            "skill_name": "sheet_double",
            "case_id": case.case_id,
            "polarity": "negative",
            "source_task_id": detail["task_id"],
            "reason": None,
        }
    ]
    assert case.prompt == "Double the value in Sheet1!A1 into Sheet1!B1."
    assert case.expected == {
        "answer_sheet": "Sheet1",
        "answer_position": "B1",
        "verifier": "spreadsheet_golden_range",
    }
    assert case.context["task_fragment"]["input_artifacts"]["input_xlsx"] == "/tmp/input.xlsx"
    assert case.context["focus_turns"] == [0]
    assert artifact.bundle.bundle_version == 2
    assert order == ["refine", "bundle_test"]
    assert report["maintenance_targets"] == ["sheet_double"]
    assert report["maintenance_test_results"][0]["aggregate"]["passed"] is True


async def test_spreadsheet_micro_skips_when_credit_is_not_actionable(monkeypatch) -> None:
    detail = _spreadsheet_detail(retrieved=["sheet_double"], injected=["sheet_double"], success=False)
    store = ArtifactStore([_skill("sheet_double", benchmark="spreadsheet")])
    calls: List[str] = []

    async def fake_refine_skill_artifact_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        calls.append("refine")
        return {"decision": {"action": "keep"}, "artifact": {}, "bundle": {}}

    async def fake_bundle_tests(**kwargs: Any) -> SkillTestResult:
        calls.append("bundle_test")
        artifact = kwargs["artifact"]
        return SkillTestResult(
            result_id="unexpected",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id,
            bundle_version=artifact.bundle.bundle_version,
            aggregate={"passed": True},
        )

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.refine_skill_artifact_llm", fake_refine_skill_artifact_llm)
    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._execute_spreadsheet_bundle_tests", fake_bundle_tests)

    report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
        detail=detail,
        credit_events=[
            {
                "skill_name": "sheet_double",
                "judgment": "uncertain",
                "confidence": 0.2,
                "reason": "Weak evidence only.",
            }
        ],
        credit_bundle_cases=[],
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )

    assert report["maintenance_targets"] == []
    assert report["reason"] == "spreadsheet_micro_maintenance"
    assert calls == []


def test_bfcl_credit_records_normalize_and_preserve_task_metrics() -> None:
    from academic.benchmarks.bfcl.related.experiment import _credit_event_records

    detail = {
        "task_id": "bfcl_contract_1",
        "runs": [
            {
                "score": 0.0,
                "metrics": {
                    "official_valid": False,
                    "n_model_steps": 2,
                    "total_tokens": 321,
                    "retrieved_skills": ["travel_schema_guard"],
                    "prompt_injected_skills": ["travel_schema_guard"],
                    "used_skills": [],
                },
                "trace": {"debug_events": [{"type": "prompt_context_update", "skill": "travel_schema_guard"}]},
            }
        ],
    }
    payload = {
        "task_summary": {"official_valid": False, "score": 0.0},
        "skill_judgments": [
            {
                "skill_name": "travel_schema_guard",
                "judgment": "positive",
                "effect_type": "schema_help",
                "confidence": "0.83",
                "reason": "The schema guard supplied the correct argument shape.",
                "maintenance_actions": [
                    {"skill_name": "travel_schema_guard", "action": "keep"},
                    {"skill_name": "unrelated", "action": "filter"},
                ],
                "bundle_case_suggestions": [
                    {
                        "skill_name": "travel_schema_guard",
                        "polarity": "positive",
                        "focus_turn_indices": [1],
                        "expected_contract": {"official_valid": True},
                    },
                    {"skill_name": "unrelated", "polarity": "negative"},
                ],
                "evidence": {"retrieved": True, "injected": True},
            }
        ],
    }

    events = _credit_event_records(
        detail=detail,
        credit_payload=payload,
        round_index=1,
        task_index=7,
    )

    assert len(events) == 1
    event = events[0]
    assert event["benchmark"] == "bfcl_v3"
    assert event["source"] == "credit_assigner"
    assert event["judgment"] == "helpful"
    assert event["confidence"] == 0.83
    assert event["round_index"] == 1
    assert event["task_index"] == 7
    assert event["official_valid"] is False
    assert event["n_model_steps"] == 2
    assert event["total_tokens"] == 321
    assert event["retrieved"] is True
    assert event["injected"] is True
    assert event["used"] is False
    assert event["maintenance_actions"] == [{"skill_name": "travel_schema_guard", "action": "keep"}]
    assert event["bundle_case_suggestions"][0]["skill_name"] == "travel_schema_guard"


def test_bfcl_credit_evidence_is_cumulative_snapshot_without_duplicate_replay() -> None:
    from academic.benchmarks.bfcl.related.experiment import _apply_credit_case_evidence

    store = ArtifactStore([_skill("bfcl_skill", benchmark="bfcl_v3")])
    cumulative_events = [
        {
            "benchmark": "bfcl_v3",
            "task_id": f"task_{idx}",
            "skill_name": "bfcl_skill",
            "judgment": "harmful",
            "confidence": 0.9,
            "reason": f"bad schema {idx}",
        }
        for idx in range(14)
    ]

    _apply_credit_case_evidence(store=store, credit_events=cumulative_events)
    _apply_credit_case_evidence(store=store, credit_events=cumulative_events)

    harmful = store.get("bfcl_skill").evidence.harmful_cases
    assert len(harmful) == 12
    assert [row["task_id"] for row in harmful] == [f"task_{idx}" for idx in range(2, 14)]


def test_bfcl_micro_targets_use_common_ordering_and_relevance_filter() -> None:
    from academic.benchmarks.bfcl.related.experiment import _micro_write_target_names, _strong_credit_targets

    events = [
        {"skill_name": "weak_harmful", "judgment": "harmful", "confidence": 0.7},
        {"skill_name": "strong_harmful", "judgment": "harmful", "confidence": 0.9},
        {
            "skill_name": "helpful_schema",
            "judgment": "helpful",
            "confidence": 0.8,
            "reason_codes": ["schema_help"],
        },
        {"skill_name": "uncertain", "judgment": "uncertain", "confidence": 1.0},
    ]
    cases = [
        {"skill_name": "case_only", "case_id": "case_only:credit:negative:t1", "created": True},
        {"skill_name": "strong_harmful", "case_id": "strong_harmful:credit:negative:t1", "created": True},
    ]

    assert _strong_credit_targets(events) == ["strong_harmful", "helpful_schema"]
    assert _micro_write_target_names(
        task_credit_events=events,
        credit_bundle_cases=cases,
        relevant_skill_names=["helpful_schema", "strong_harmful"],
    ) == ["strong_harmful", "helpful_schema", "case_only"]


def test_bfcl_trim_bundle_cases_delegates_budget_and_bumps_version(monkeypatch) -> None:
    from academic.benchmarks.bfcl.maintenance.adapter import trim_bundle_cases

    artifact = _skill("bfcl_budget", benchmark="bfcl_v3")
    artifact.bundle.bundle_version = 4
    for idx in range(4):
        artifact.bundle.positive_cases.append(
            SkillBundleCase(
                case_id=f"pos_{idx}",
                source="credit_assigner_positive" if idx == 3 else "manual",
                prompt=f"positive {idx}",
                context={"confidence": 0.9 if idx == 3 else 0.1},
                polarity="positive",
            )
        )
        artifact.bundle.negative_cases.append(
            SkillBundleCase(
                case_id=f"neg_{idx}",
                source="integration_failure",
                prompt=f"negative {idx}",
                context={"confidence": idx / 10},
                polarity="negative",
            )
        )

    monkeypatch.setenv("BFCL_BUNDLE_MAX_TOTAL_CASES", "3")
    changed = trim_bundle_cases(artifact, per_polarity_limit=2)

    assert changed is True
    assert artifact.bundle.bundle_version == 5
    assert len(artifact.bundle.all_cases()) == 3
    assert "pos_3" in {case.case_id for case in artifact.bundle.all_cases()}
    assert artifact.bundle.fixtures["bundle_trimmed"] is True
    assert artifact.bundle.fixtures["bundle_case_budget"] == {"per_polarity_limit": 2, "total_limit": 3}
