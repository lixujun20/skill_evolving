import copy
from pathlib import Path
from typing import Any, Dict, List

import openpyxl

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.evolution import OnlineSkillEvolutionRunner
from academic.benchmarks.core.llm_text import TextLLMResponse
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact, SkillInterface, SkillTestResult
from academic.benchmarks.spreadsheet.adapter import (
    SpreadsheetMaintenanceAdapter,
    _execute_spreadsheet_bundle_tests,
    _answer_range_refs,
    _is_spreadsheet_callable_skill,
    _write_spreadsheet_skill_library,
    run_spreadsheet_task,
)


def _xlsx(path: Path, *, source: int = 7, answer: int | None = None) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = source
    ws["B1"] = answer
    wb.save(path)


def _task(tmp_path: Path, task_id: str, question: str, *, source: int = 7, answer: int = 14) -> BenchmarkTask:
    input_xlsx = tmp_path / f"{task_id}_init.xlsx"
    golden_xlsx = tmp_path / f"{task_id}_golden.xlsx"
    _xlsx(input_xlsx, source=source, answer=None)
    _xlsx(golden_xlsx, source=source, answer=answer)
    return BenchmarkTask(
        benchmark="spreadsheet",
        task_id=task_id,
        question=question,
        expected={"golden_xlsx": str(golden_xlsx), "answer_sheet": "Sheet1", "answer_position": "B1"},
        input_artifacts={"input_xlsx": str(input_xlsx), "prompt_txt": question},
        metadata={"instruction_type": "formula_generation", "data_position": "Sheet1!A1:B1"},
    )


def test_spreadsheet_answer_range_parser_handles_quoted_sheet_prefix_and_trailing_quote() -> None:
    refs = _answer_range_refs(
        "'Sheet1!'A1:A50,'Sheet2!'A1:E20,'Sheet3!'A1:A50'",
        default_sheet="Sheet1",
    )

    assert refs == [
        ("Sheet1", "A1:A50"),
        ("Sheet2", "A1:E20"),
        ("Sheet3", "A1:A50"),
    ]


def _detail(task: BenchmarkTask, *, success: bool, score: float, retrieved: List[str], code: str) -> Dict[str, Any]:
    result = BenchmarkResult(
        benchmark="spreadsheet",
        task_id=task.task_id,
        success=success,
        score=score,
        metrics={
            "answer_sheet": "Sheet1",
            "answer_position": "B1",
            "checked_cells": 1,
            "mismatched_cells": [] if success else [{"cell": "B1", "predicted": 0, "expected": 14}],
            "execution_ok": True,
            "returncode": 0,
            "total_tokens": 120,
            "retrieved_skills": retrieved,
        },
        trace={"retrieved_skills": retrieved, "code": code, "stderr": "", "stdout": ""},
    )
    return {
        "task_id": task.task_id,
        "task": task.as_dict(),
        "n_runs": 1,
        "n_success": 1 if success else 0,
        "avg_score": score,
        "runs": [{**result.as_dict(), "run_idx": 0}],
    }


async def test_spreadsheet_function_skill_can_be_imported_and_called(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_callable", "Double A1 into B1.", source=9, answer=18)
    skill = SkillArtifact(
        name="spreadsheet_double_a1_to_b1",
        kind="executable_tool",
        description="Callable openpyxl helper that doubles A1 into B1.",
        body="""```python
def apply_double(INPUT_XLSX, OUTPUT_XLSX, **kwargs):
    import openpyxl
    wb = openpyxl.load_workbook(INPUT_XLSX)
    ws = wb["Sheet1"]
    ws["B1"] = ws["A1"].value * 2
    wb.save(OUTPUT_XLSX)
```""",
        interface=SkillInterface(
            invocation_contract={"injection_type": "functional"},
            input_contract={"benchmark": "SpreadsheetBench"},
        ),
        metadata={
            "domains": ["SpreadsheetBench", "formula_generation"],
            "allowed_tools": ["openpyxl"],
            "intent_keywords": ["double"],
        },
    )
    captured = {}

    async def fake_ask_text_llm(**kwargs: Any) -> TextLLMResponse:
        captured["system"] = kwargs["system"]
        assert "from skill_library import" in kwargs["system"]
        assert "spreadsheet_double_a1_to_b1" in kwargs["system"]
        return TextLLMResponse(
            content="""```python
from skill_library import spreadsheet_double_a1_to_b1
spreadsheet_double_a1_to_b1(INPUT_XLSX, OUTPUT_XLSX)
```""",
            prompt_tokens=12,
            completion_tokens=8,
            model_name="mock-model",
            api_style="mock",
        )

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.ask_text_llm", fake_ask_text_llm)

    result = await run_spreadsheet_task(
        task,
        llm_config="mock",
        model_name="mock-model",
        artifact_store=ArtifactStore([skill]),
        top_k_skills=1,
        skill_injector_mode="compact",
        skill_context_budget_chars=800,
        work_dir=tmp_path / "work",
    )

    assert result.success is True
    assert result.metrics["prompt_injected_skills"] == ["spreadsheet_double_a1_to_b1"]
    assert result.trace["callable_skills"][0]["function_name"] == "spreadsheet_double_a1_to_b1"
    assert (tmp_path / "work" / "skill_library.py").exists()


async def test_spreadsheet_executable_tool_marked_informational_still_callable(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_callable_legacy", "Double A1 into B1.", source=6, answer=12)
    skill = SkillArtifact(
        name="spreadsheet_legacy_double_callable",
        kind="executable_tool",
        description="Legacy executable skill whose metadata was incorrectly stored as informational.",
        body="""Applicability: double a source cell.
```python
from openpyxl import load_workbook
wb = load_workbook(INPUT_XLSX)
ws = wb["Sheet1"]
ws["B1"] = ws["A1"].value * 2
wb.save(OUTPUT_XLSX)
```
""",
        metadata={
            "injection_type": "informational",
            "domains": ["SpreadsheetBench", "formula_generation"],
            "allowed_tools": ["openpyxl"],
            "intent_keywords": ["double"],
        },
    )
    assert skill.injection_type() == "informational"
    assert _is_spreadsheet_callable_skill(skill) is True

    async def fake_ask_text_llm(**kwargs: Any) -> TextLLMResponse:
        assert "Callable function skills:" in kwargs["system"]
        assert "spreadsheet_legacy_double_callable" in kwargs["system"]
        return TextLLMResponse(
            content="""```python
from skill_library import spreadsheet_legacy_double_callable
spreadsheet_legacy_double_callable(INPUT_XLSX, OUTPUT_XLSX)
```""",
            prompt_tokens=12,
            completion_tokens=8,
            model_name="mock-model",
            api_style="mock",
        )

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.ask_text_llm", fake_ask_text_llm)
    result = await run_spreadsheet_task(
        task,
        llm_config="mock",
        model_name="mock-model",
        artifact_store=ArtifactStore([skill]),
        top_k_skills=1,
        skill_injector_mode="compact",
        skill_context_budget_chars=800,
        work_dir=tmp_path / "work",
    )

    assert result.success is True
    assert result.trace["callable_skills"][0]["skill_name"] == "spreadsheet_legacy_double_callable"


async def test_spreadsheet_workflow_skill_is_not_exported_as_callable(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_workflow_only", "Double A1 into B1.", source=5, answer=10)
    skill = SkillArtifact(
        name="spreadsheet_workflow_openpyxl_guardrail",
        kind="workflow_guardrail_card",
        description="Prompt-only workflow guidance mentioning openpyxl.",
        body="Use openpyxl only after inspecting workbook shape; preserve unrelated cells.",
        metadata={
            "injection_type": "informational",
            "domains": ["SpreadsheetBench"],
            "allowed_tools": ["openpyxl"],
            "intent_keywords": ["double", "inspect"],
        },
    )
    assert _is_spreadsheet_callable_skill(skill) is False

    async def fake_ask_text_llm(**kwargs: Any) -> TextLLMResponse:
        assert "spreadsheet_workflow_openpyxl_guardrail" not in kwargs["system"].split("Callable function skills:", 1)[1]
        return TextLLMResponse(
            content="""```python
import openpyxl
wb = openpyxl.load_workbook(INPUT_XLSX)
ws = wb["Sheet1"]
ws["B1"] = ws["A1"].value * 2
wb.save(OUTPUT_XLSX)
```""",
            prompt_tokens=12,
            completion_tokens=8,
            model_name="mock-model",
            api_style="mock",
        )

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.ask_text_llm", fake_ask_text_llm)
    result = await run_spreadsheet_task(
        task,
        llm_config="mock",
        model_name="mock-model",
        artifact_store=ArtifactStore([skill]),
        top_k_skills=1,
        skill_injector_mode="compact",
        skill_context_budget_chars=800,
        work_dir=tmp_path / "work",
    )

    assert result.success is True
    assert result.trace["callable_skills"] == []
    assert not (tmp_path / "work" / "skill_library.py").exists()


def test_spreadsheet_callable_snippet_receives_kwargs_and_column_aliases(tmp_path: Path) -> None:
    input_xlsx = tmp_path / "input.xlsx"
    output_xlsx = tmp_path / "output.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = 1
    ws["A2"] = 7
    ws["D1"] = 7
    wb.save(input_xlsx)

    skill = SkillArtifact(
        name="spreadsheet_snippet_with_kwargs",
        kind="executable_tool",
        description="Remove one matching value from a value column.",
        body="""```python
values = []
for row in range(value_start_row, value_end_row + 1):
    values.append(ws.cell(row=row, column=value_col).value)
result_values = []
for row in range(result_start_row, result_end_row + 1):
    result_values.append(ws.cell(row=row, column=result_col).value)
for item in result_values:
    if item in values:
        values.remove(item)
for idx, item in enumerate(values, start=value_start_row):
    ws.cell(row=idx, column=value_col).value = item
```""",
        metadata={"injection_type": "informational", "domains": ["SpreadsheetBench"]},
    )
    info = _write_spreadsheet_skill_library([skill], tmp_path)
    assert info["skills"][0]["function_name"] == "spreadsheet_snippet_with_kwargs"

    import importlib.util

    spec = importlib.util.spec_from_file_location("skill_library", tmp_path / "skill_library.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.spreadsheet_snippet_with_kwargs(
        input_xlsx,
        output_xlsx,
        value_column="A",
        result_column="D",
        value_start_row=1,
        value_end_row=2,
        result_start_row=1,
        result_end_row=1,
    )

    out = openpyxl.load_workbook(output_xlsx)
    assert out["Sheet1"]["A1"].value == 1


async def test_spreadsheet_micro_extracts_actionable_pending_skill_from_success(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_train_1", "Double the value in A1 and write it to B1.")
    detail = _detail(
        task,
        success=True,
        score=1.0,
        retrieved=[],
        code="import openpyxl\nwb=openpyxl.load_workbook(INPUT_XLSX)\nws=wb['Sheet1']\nws['B1']=ws['A1'].value*2\nwb.save(OUTPUT_XLSX)",
    )
    calls: List[str] = []

    async def fake_ask_json(**kwargs: Any) -> Dict[str, Any]:
        calls.append(kwargs["role"])
        return {
            "artifacts": [
                {
                    "name": "spreadsheet_double_a1_to_b1",
                    "kind": "executable_tool",
                    "description": "Double a numeric source cell and write the result with openpyxl.",
                    "body": "Applicability: tasks asking to double a numeric source cell. ```python\nws['B1'] = ws['A1'].value * 2\nwb.save(OUTPUT_XLSX)\n``` Non-applicability: do not hard-code values.",
                    "interface": {
                        "summary": "Double source cell into answer cell.",
                        "usage": "Use for formula_generation doubling tasks.",
                        "input_contract": {"benchmark": "SpreadsheetBench", "source_cell": "numeric"},
                        "output_contract": {"answer_cell": "source*2"},
                        "invocation_contract": {"injection_type": "informational"},
                        "compatibility_notes": "Inspect the workbook before applying.",
                    },
                    "metadata": {
                        "domains": ["SpreadsheetBench", "formula_generation"],
                        "allowed_tools": ["openpyxl"],
                        "intent_keywords": ["double", "numeric", "formula"],
                        "source_task_ids": ["sheet_train_1"],
                        "evidence_span": "successful trace wrote B1=A1*2",
                        "non_applicability": "not for unrelated aggregations",
                    },
                }
            ]
        }

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._ask_json", fake_ask_json)
    store = ArtifactStore()
    report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
        detail=detail,
        credit_events=[],
        credit_bundle_cases=[],
        store=store,
        config=MaintenanceRunConfig(llm_config="mock", model_name="mock-model"),
        round_index=0,
        task_index=0,
    )

    assert "spreadsheet_extractor" in calls
    assert report["extraction_reports"][0]["skill_name"] == "spreadsheet_double_a1_to_b1"
    artifact = store.get("spreadsheet_double_a1_to_b1")
    assert artifact is not None
    assert artifact.status == "pending"
    assert artifact.bundle.positive_cases
    assert "openpyxl" in artifact.body


async def test_spreadsheet_credit_creates_focused_case_and_micro_refines_before_bundle(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_train_2", "Double A1 into B1.", source=7, answer=14)
    skill = SkillArtifact(
        name="spreadsheet_bad_double",
        kind="executable_tool",
        description="Bad doubling pattern.",
        body="Wrongly write ws['B1'] = 0 for doubling tasks.",
        metadata={
            "domains": ["SpreadsheetBench", "formula_generation"],
            "allowed_tools": ["openpyxl"],
            "intent_keywords": ["double"],
            "source_task_ids": ["old"],
        },
        interface=SkillInterface(summary="Bad", usage="Use for doubling."),
    )
    store = ArtifactStore([skill])
    detail = _detail(task, success=False, score=0.0, retrieved=[skill.name], code="ws['B1']=0\nwb.save(OUTPUT_XLSX)")
    call_order: List[str] = []

    async def fake_ask_json(**kwargs: Any) -> Dict[str, Any]:
        call_order.append(kwargs["role"])
        return {
            "task_summary": {"task_id": "sheet_train_2", "score": 0.0, "success": False, "total_tokens": 120},
            "skill_judgments": [
                {
                    "skill_name": skill.name,
                    "judgment": "harmful",
                    "effect_type": "correctness_harm",
                    "confidence": 0.9,
                    "reason": "The retrieved skill writes the wrong answer cell value.",
                    "maintenance_actions": [{"action": "refine_workflow", "reason": "replace hard-coded zero", "target_scope": "doubling"}],
                    "refine_required": True,
                    "filter_candidate": False,
                    "evidence_strength": "strong",
                    "attribution_scope": "prompt_influence",
                    "bundle_case_suggestions": [
                        {
                            "polarity": "negative",
                            "reason": "Guard against writing zero instead of source*2.",
                            "source_task_id": "sheet_train_2",
                            "focus_turn_indices": [0],
                            "required_context_turn_indices": [],
                            "state_requirements": {},
                            "expected_contract": "B1 should match the official golden workbook.",
                            "task_fragment_policy": "reuse_official_fragment",
                        }
                    ],
                    "evidence": {"retrieved": True, "used": False, "trace_signals": ["B1 mismatch"]},
                }
            ],
        }

    async def fake_refine(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        call_order.append("refiner")
        return {
            "decision": {"action": "refine_minor", "reason": "Use source*2, not zero.", "version_kind": "minor", "pinned_dependencies": []},
            "artifact": {
                "body": "Applicability: doubling tasks. ```python\nws['B1'] = ws['A1'].value * 2\nwb.save(OUTPUT_XLSX)\n``` Non-applicability: verify cells dynamically.",
                "metadata": {"last_refine_reason": "Use source*2."},
                "interface": {
                    "summary": "Double source cell.",
                    "usage": "Use for SpreadsheetBench doubling tasks.",
                    "input_contract": {"source_cell": "numeric"},
                    "output_contract": {"answer": "source*2"},
                    "invocation_contract": {"injection_type": "informational"},
                    "compatibility_notes": "Do not hard-code output values.",
                },
            },
            "bundle": {"maintenance_notes": "", "positive_cases": [], "negative_cases": [], "integration_cases": []},
        }

    async def fake_bundle_tests(**kwargs: Any):
        call_order.append("bundle_test")
        artifact = kwargs["artifact"]
        return SkillTestResult(
            result_id="mock_bundle_result",
            skill_name=artifact.name,
            skill_version=artifact.version,
            bundle_id=artifact.bundle.bundle_id,
            bundle_version=artifact.bundle.bundle_version,
            run_label="spreadsheet_bundle_unit",
            aggregate={"passed": True},
        )

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._ask_json", fake_ask_json)
    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.refine_skill_artifact_llm", fake_refine)
    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._execute_spreadsheet_bundle_tests", fake_bundle_tests)

    credit = await SpreadsheetMaintenanceAdapter().assign_credit(
        detail=detail,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )
    cases = await SpreadsheetMaintenanceAdapter().apply_credit_bundle_cases(
        detail=detail,
        credit_events=credit,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )
    report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
        detail=detail,
        credit_events=credit,
        credit_bundle_cases=cases,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )

    assert cases and cases[0]["polarity"] == "negative"
    assert call_order.index("refiner") < call_order.index("bundle_test")
    assert report["refine_decisions"][0]["action"] == "refine_minor"
    assert "ws['A1'].value * 2" in store.get(skill.name).body


async def test_spreadsheet_failed_formula_mismatch_extracts_repair_skill_when_llm_empty(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_formula_fail", "Fill C2:C3 with A+B formulas, leaving blank if either input is blank.")
    detail = _detail(
        task,
        success=False,
        score=0.0,
        retrieved=[],
        code='ws["C2"] = "=IF(AND(A2<>\\"\\", B2<>\\"\\"), A2+B2, \\"\\")"',
    )
    detail["runs"][0]["metrics"]["mismatched_cells"] = [
        {
            "cell": "C2",
            "predicted": '=IF(AND(A2<>"", B2<>""), A2+B2, "")',
            "expected": '=IF(OR(A2="",B2=""),"",A2+B2)',
        }
    ]

    async def fake_ask_json(**kwargs: Any) -> Dict[str, Any]:
        assert kwargs["role"] == "spreadsheet_extractor"
        return {"artifacts": []}

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._ask_json", fake_ask_json)
    store = ArtifactStore()
    report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
        detail=detail,
        credit_events=[],
        credit_bundle_cases=[],
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        task_index=0,
    )

    assert report["extraction_reports"]
    artifact = store.get(report["extraction_reports"][0]["skill_name"])
    assert artifact is not None
    assert artifact.status == "pending"
    assert artifact.metadata["extracted_from_failure"] is True
    assert "expected" in artifact.body


async def test_spreadsheet_bundle_replay_uses_real_workbook_and_injected_skill(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_bundle_1", "Double A1 into B1.", source=9, answer=18)
    skill = SkillArtifact(
        name="spreadsheet_double_openpyxl",
        kind="executable_tool",
        description="Double A1 to B1.",
        body="Use openpyxl to set B1 to A1*2.",
        metadata={"domains": ["SpreadsheetBench"], "allowed_tools": ["openpyxl"], "intent_keywords": ["double"]},
    )
    detail = _detail(task, success=True, score=1.0, retrieved=[], code="")
    from academic.benchmarks.spreadsheet.adapter import _spreadsheet_case_from_task

    skill.bundle.positive_cases.append(
        _spreadsheet_case_from_task(
            detail=detail,
            skill_name=skill.name,
            polarity="positive",
            reason="replay doubling task",
            source="manual",
            confidence=1.0,
        )
    )

    async def fake_ask_text_llm(**kwargs: Any) -> TextLLMResponse:
        assert "spreadsheet_double_openpyxl" in kwargs["system"]
        return TextLLMResponse(
            content="""```python
import openpyxl
wb = openpyxl.load_workbook(INPUT_XLSX)
ws = wb["Sheet1"]
ws["B1"] = ws["A1"].value * 2
wb.save(OUTPUT_XLSX)
```""",
            prompt_tokens=20,
            completion_tokens=10,
            model_name="mock-model",
            api_style="mock",
        )

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.ask_text_llm", fake_ask_text_llm)
    result = await _execute_spreadsheet_bundle_tests(
        artifact=skill,
        config=MaintenanceRunConfig(llm_config="mock", model_name="mock-model"),
    )

    assert result.aggregate["passed"] is True
    assert result.aggregate["avg_accuracy"] == 1.0
    assert result.unit_case_runs[0].tokens == 30


async def test_spreadsheet_macro_promotes_dedupes_and_filters(tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_train_3", "Double A1 into B1.")
    detail = _detail(task, success=True, score=1.0, retrieved=[], code="ws['B1']=ws['A1'].value*2")
    from academic.benchmarks.spreadsheet.adapter import _spreadsheet_case_from_task

    pending = SkillArtifact(
        name="spreadsheet_double_pending",
        kind="executable_tool",
        description="Double source cell.",
        body="Double source cell with openpyxl.",
        metadata={
            "domains": ["SpreadsheetBench", "formula_generation"],
            "allowed_tools": ["openpyxl"],
            "intent_keywords": ["double"],
            "source_task_ids": ["sheet_train_3"],
            "instruction_type": "formula_generation",
        },
        status="pending",
    )
    pending.bundle.positive_cases.append(
        _spreadsheet_case_from_task(
            detail=detail,
            skill_name=pending.name,
            polarity="positive",
            reason="source success",
            source="distilled_success",
            confidence=0.8,
        )
    )
    duplicate = copy.deepcopy(pending)
    duplicate.name = "spreadsheet_double_duplicate"
    duplicate.status = "active"
    duplicate.success_count = 0
    harmful = SkillArtifact(
        name="spreadsheet_harmful",
        kind="workflow_guardrail_card",
        description="Bad generic sheet rule.",
        body="Always write zero.",
        metadata={"domains": ["SpreadsheetBench"], "allowed_tools": ["openpyxl"], "intent_keywords": ["zero"]},
        status="active",
    )
    store = ArtifactStore([pending, duplicate, harmful])
    credit = [
        {"skill_name": "spreadsheet_harmful", "judgment": "harmful", "confidence": 0.9},
        {"skill_name": "spreadsheet_harmful", "judgment": "harmful", "confidence": 0.95},
    ]

    report = await SpreadsheetMaintenanceAdapter().run_macro_maintenance(
        window_details=[detail],
        all_train_details=[detail],
        credit_events=credit,
        store=store,
        config=MaintenanceRunConfig(llm_config="mock"),
        round_index=0,
        window_index=0,
        final_window=True,
    )

    assert store.get("spreadsheet_double_pending").status == "active"
    assert store.get("spreadsheet_harmful").status == "disabled"
    assert report["overlap_refactor"]["promoted_pending_skills"] == ["spreadsheet_double_pending"]
    assert "spreadsheet_harmful" in report["overlap_refactor"]["filtered_skills"]


async def test_spreadsheet_generic_evolve_produces_real_skills_and_retrieves_on_heldout(monkeypatch, tmp_path: Path) -> None:
    train = [_task(tmp_path, "train_double", "Double the value in A1 and write it to B1.", source=5, answer=10)]
    test = [_task(tmp_path, "test_double", "Double the value in A1 and write it to B1.", source=8, answer=16)]
    seen_store_sizes: List[int] = []

    async def fake_run_task(task: BenchmarkTask, **kwargs: Any) -> BenchmarkResult:
        store = kwargs["artifact_store"]
        retrieved = store.retrieve(task.question, top_k=kwargs.get("top_k_skills", 5)) if store else []
        seen_store_sizes.append(len(store.all()) if store else 0)
        return BenchmarkResult(
            benchmark="spreadsheet",
            task_id=task.task_id,
            success=True,
            score=1.0,
            metrics={
                "answer_sheet": "Sheet1",
                "answer_position": "B1",
                "checked_cells": 1,
                "mismatched_cells": [],
                "execution_ok": True,
                "returncode": 0,
                "total_tokens": 100,
                "retrieved_skills": [item.name for item in retrieved],
            },
            trace={
                "retrieved_skills": [item.name for item in retrieved],
                "code": "ws['B1']=ws['A1'].value*2\nwb.save(OUTPUT_XLSX)",
                "stderr": "",
                "stdout": "",
            },
        )

    async def fake_ask_json(**kwargs: Any) -> Dict[str, Any]:
        if kwargs["role"] == "spreadsheet_extractor":
            return {
                "artifacts": [
                    {
                        "name": "spreadsheet_double_a1_to_b1",
                        "kind": "executable_tool",
                        "description": "Double a numeric source cell and write the result with openpyxl.",
                        "body": "Applicability: double A1 into B1. ```python\nws['B1']=ws['A1'].value*2\nwb.save(OUTPUT_XLSX)\n``` Non-applicability: no hard-coded source values.",
                        "metadata": {
                            "domains": ["SpreadsheetBench", "formula_generation"],
                            "allowed_tools": ["openpyxl"],
                            "intent_keywords": ["double", "value"],
                            "source_task_ids": ["train_double"],
                        },
                    }
                ]
            }
        return {"task_summary": {}, "skill_judgments": []}

    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter.run_spreadsheet_task", fake_run_task)
    monkeypatch.setattr("academic.benchmarks.spreadsheet.adapter._ask_json", fake_ask_json)
    runner = OnlineSkillEvolutionRunner(
        adapter=SpreadsheetMaintenanceAdapter(),
        config=MaintenanceRunConfig(
            llm_config="mock",
            model_name="mock-model",
            micro_maintenance_step=1,
            macro_maintenance_step=1,
            test_concurrency=1,
            top_k_skills=3,
        ),
    )

    summary = await runner.run(train_tasks=train, test_tasks=test)

    assert summary["store_snapshot"]["n_active"] >= 1
    assert summary["skills"]
    assert summary["micro_maintenance_reports"][0]["extraction_reports"]
    assert summary["test_details"][0]["runs"][0]["metrics"]["retrieved_skills"] == ["spreadsheet_double_a1_to_b1"]
    assert max(seen_store_sizes) >= 1
