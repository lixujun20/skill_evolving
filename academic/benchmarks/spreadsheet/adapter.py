"""SpreadsheetBench maintenance adapter and compatibility exports."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.bundle_policy import (
    bundle_bucket,
    default_bundle_case_priority,
    trim_bundle_cases_to_budget,
)
from academic.benchmarks.core.credit_scope import (
    credit_candidate_skill_names,
    skill_exposure_flags,
)
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig, NoOpMaintenanceAdapter
from academic.benchmarks.core.maintenance_utils import json_block, now_iso, stable_id
from academic.benchmarks.core.types import (
    BenchmarkResult,
    BenchmarkTask,
    SkillArtifact,
    SkillBundleCase,
    SkillEvidence,
    SkillInterface,
    SkillLineage,
    SkillTestCaseRun,
    SkillTestResult,
)
import academic.benchmarks.spreadsheet.executor as _spreadsheet_executor
from academic.benchmarks.core.llm_text import ask_text_llm
from academic.benchmarks.spreadsheet.executor import (
    build_spreadsheet_notebook_prompt as _build_spreadsheet_notebook_prompt,
    build_spreadsheet_notebook_turn_prompt as _build_spreadsheet_notebook_turn_prompt,
    build_spreadsheet_prompt as _build_spreadsheet_prompt,
    clip_notebook_text as _clip_notebook_text,
    run_spreadsheet_task as _run_spreadsheet_task_impl,
    run_spreadsheet_task_notebook as _run_spreadsheet_task_notebook_impl,
    workbook_preview as _workbook_preview,
)
from academic.benchmarks.spreadsheet.loader import ensure_spreadsheetbench, load_spreadsheet_tasks
from academic.benchmarks.spreadsheet.models import SpreadsheetTrace
from academic.benchmarks.spreadsheet.prompts import (
    SPREADSHEET_CREDIT_SYSTEM,
    SPREADSHEET_DONE_PATTERN,
    SPREADSHEET_EXTRACT_SYSTEM,
)
from academic.benchmarks.spreadsheet.skill_runtime import (
    NotebookPythonSession as _NotebookPythonSession,
    called_spreadsheet_skill_functions as _called_spreadsheet_skill_functions,
    called_spreadsheet_skill_functions_from_text as _called_spreadsheet_skill_functions_from_text,
    extract_code as _extract_code,
    is_spreadsheet_callable_skill as _is_spreadsheet_callable_skill,
    looks_like_spreadsheet_python as _looks_like_spreadsheet_python,
    render_spreadsheet_skill_function as _render_spreadsheet_skill_function,
    run_code as _run_code,
    spreadsheet_callable_description as _spreadsheet_callable_description,
    spreadsheet_callable_kwargs as _spreadsheet_callable_kwargs,
    spreadsheet_column_value as _spreadsheet_column_value,
    spreadsheet_skill_code as _spreadsheet_skill_code,
    spreadsheet_skill_function_name as _spreadsheet_skill_function_name,
    wrap_spreadsheet_skill_snippet as _wrap_spreadsheet_skill_snippet,
    write_spreadsheet_skill_library as _write_spreadsheet_skill_library,
)
from academic.benchmarks.spreadsheet.trace_projection import (
    spreadsheet_code_snippet as _spreadsheet_code_snippet,
    spreadsheet_result_projection as _spreadsheet_result_projection,
    spreadsheet_skill_projection as _spreadsheet_skill_projection,
    spreadsheet_task_fragment as _spreadsheet_task_fragment,
    spreadsheet_trace_projection as _spreadsheet_trace_projection,
)
from academic.benchmarks.spreadsheet.verifier import (
    answer_range_refs as _answer_range_refs,
    cells_in_range as _cells_in_range,
    first_sheet_name as _first_sheet_name,
    jsonable_cell_value as _jsonable_cell_value,
    normalize_answer_range_text as _normalize_answer_range_text,
    normalize_cell_value as _normalize_cell_value,
    split_answer_range_list as _split_answer_range_list,
    split_sheet_range as _split_sheet_range,
    verify_spreadsheet_output,
)
from academic.skill_repository.llm_maintenance import (
    _ask_json,
    apply_refine_payload,
    normalize_skill_name,
    refine_skill_artifact_llm,
    summarize_dependency_context,
)


async def run_spreadsheet_task(*args: Any, **kwargs: Any) -> BenchmarkResult:
    _spreadsheet_executor.ask_text_llm = ask_text_llm
    return await _run_spreadsheet_task_impl(*args, **kwargs)


async def run_spreadsheet_task_notebook(*args: Any, **kwargs: Any) -> BenchmarkResult:
    _spreadsheet_executor.ask_text_llm = ask_text_llm
    return await _run_spreadsheet_task_notebook_impl(*args, **kwargs)


class SpreadsheetMaintenanceAdapter(NoOpMaintenanceAdapter):
    """SpreadsheetBench adapter for the benchmark-agnostic evolution runner.

    Spreadsheet evolution is benchmark-native: successful traces can extract
    openpyxl/workflow skills, retrieved skills receive per-task credit, credit
    produces focused replayable SpreadsheetBench bundle cases, micro
    maintenance refines or filters implicated skills, and macro maintenance
    promotes/deduplicates/filters repository candidates at the window level.
    """

    benchmark = "spreadsheet"

    async def run_task(
        self,
        task: BenchmarkTask,
        *,
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        phase: str,
        task_index: int,
        run_idx: int,
    ) -> BenchmarkResult:
        del phase, task_index, run_idx
        if str(config.extra.get("spreadsheet_execution_mode") or "single").strip().lower() == "notebook":
            return await run_spreadsheet_task_notebook(
                task,
                llm_config=config.llm_config,
                model_name=config.model_name,
                artifact_store=store,
                top_k_skills=config.top_k_skills,
                skill_injector_mode=config.extra.get("skill_injector_mode"),
                skill_context_budget_chars=config.extra.get("skill_context_budget_chars"),
                max_turns=int(config.extra.get("spreadsheet_max_turns") or 5),
            )
        return await run_spreadsheet_task(
            task,
            llm_config=config.llm_config,
            model_name=config.model_name,
            artifact_store=store,
            top_k_skills=config.top_k_skills,
            skill_injector_mode=config.extra.get("skill_injector_mode"),
            skill_context_budget_chars=config.extra.get("skill_context_budget_chars"),
        )

    async def assign_credit(
        self,
        *,
        detail: Dict[str, Any],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> List[Dict[str, Any]]:
        del round_index
        projection = _spreadsheet_trace_projection(detail)
        candidate_names = credit_candidate_skill_names(projection)
        candidate_artifacts = [
            artifact for name in candidate_names for artifact in [store.get(str(name))] if artifact
        ]
        if not candidate_artifacts:
            return []
        try:
            payload = await _ask_json(
                system=SPREADSHEET_CREDIT_SYSTEM,
                user=json_block(
                    {
                        "task": _spreadsheet_task_fragment(detail),
                        "trace_projection": projection,
                        "retrieval_audit": {
                            "retrieved_only_skills": projection.get("retrieved_only_skills") or [],
                            "candidate_policy": "prompt_injected_or_called_only",
                        },
                        "candidate_skills": [
                            _spreadsheet_skill_projection(artifact, projection=projection)
                            for artifact in candidate_artifacts
                        ],
                    }
                ),
                llm_config=config.llm_config,
                model_name=config.model_name,
                role="spreadsheet_credit_assigner",
                metadata={"task_id": detail.get("task_id"), "task_index": task_index},
            )
            events = _normalize_spreadsheet_credit_events(
                payload,
                detail=detail,
                candidate_artifacts=candidate_artifacts,
                projection=projection,
            )
        except Exception as exc:
            events = _heuristic_spreadsheet_credit_events(
                detail=detail,
                candidate_artifacts=candidate_artifacts,
                projection=projection,
                reason=f"credit_llm_failed:{type(exc).__name__}",
            )
        for event in events:
            artifact = store.get(event["skill_name"])
            if artifact is None:
                continue
            evidence_row = {
                "task_id": detail.get("task_id"),
                "judgment": event.get("judgment"),
                "effect_type": event.get("effect_type"),
                "confidence": event.get("confidence"),
                "reason": event.get("reason"),
                "score": projection.get("score"),
                "success": projection.get("success"),
            }
            if event.get("judgment") == "helpful":
                artifact.evidence.helpful_cases.append(evidence_row)
                artifact.success_count += 1
            elif event.get("judgment") == "harmful":
                artifact.evidence.harmful_cases.append(evidence_row)
            artifact.usage_count += 1
        return events

    async def apply_credit_bundle_cases(
        self,
        *,
        detail: Dict[str, Any],
        credit_events: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        task_index: int,
    ) -> List[Dict[str, Any]]:
        del config, round_index, task_index
        created: List[Dict[str, Any]] = []
        for event in credit_events:
            artifact = store.get(str(event.get("skill_name") or ""))
            if artifact is None:
                continue
            for suggestion in event.get("bundle_case_suggestions") or []:
                case = _spreadsheet_case_from_credit_suggestion(
                    detail=detail,
                    artifact=artifact,
                    event=event,
                    suggestion=dict(suggestion or {}),
                )
                if case is None:
                    continue
                bucket = bundle_bucket(case.polarity)
                existing = {item.case_id for item in getattr(artifact.bundle, bucket)}
                if case.case_id not in existing:
                    getattr(artifact.bundle, bucket).append(case)
                    artifact.bundle.bundle_version += 1
                    created.append(
                        {
                            "skill_name": artifact.name,
                            "case_id": case.case_id,
                            "polarity": case.polarity,
                            "source_task_id": detail.get("task_id"),
                            "reason": suggestion.get("reason") or event.get("reason"),
                        }
                    )
            _trim_spreadsheet_bundle_cases(artifact)
        return created

    async def run_micro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
        detail = kwargs.get("detail") or {}
        credit_events = list(kwargs.get("credit_events") or [])
        credit_bundle_cases = list(kwargs.get("credit_bundle_cases") or [])
        store: ArtifactStore = kwargs["store"]
        config: MaintenanceRunConfig = kwargs["config"]
        task_index = int(kwargs.get("task_index") or 0)
        projection = _spreadsheet_trace_projection(detail)
        extracted = await _extract_spreadsheet_skills_from_detail(
            detail,
            store=store,
            config=config,
            task_index=task_index,
        )
        extraction_reports: List[Dict[str, Any]] = []
        for artifact in extracted:
            store.add_pending(artifact)
            extraction_reports.append(
                {
                    "skill_name": artifact.name,
                    "status": artifact.status,
                    "description": artifact.description,
                    "source_task_ids": artifact.metadata.get("source_task_ids") or [],
                }
            )
        maintenance_targets = _spreadsheet_micro_targets(
            credit_events=credit_events,
            credit_bundle_cases=credit_bundle_cases,
            extracted=extracted,
        )
        refine_decisions: List[Dict[str, Any]] = []
        bundle_results: List[Dict[str, Any]] = []
        for skill_name in maintenance_targets:
            artifact = store.get(skill_name)
            if artifact is None:
                continue
            skill_credit = [event for event in credit_events if event.get("skill_name") == skill_name]
            pre_refine = await _refine_spreadsheet_skill_from_credit(
                artifact=artifact,
                credit_context=skill_credit,
                detail=detail,
                store=store,
                config=config,
            )
            refine_decisions.append(pre_refine)
            if pre_refine.get("updated_artifact"):
                store.add(pre_refine["updated_artifact"])
                artifact = store.get(skill_name) or pre_refine["updated_artifact"]
            if artifact.bundle.all_cases():
                result = await _execute_spreadsheet_bundle_tests(
                    artifact=artifact,
                    config=config,
                )
                store.add_test_result(result)
                bundle_results.append(result.as_dict())
                if not result.aggregate.get("passed"):
                    post_refine = await _refine_spreadsheet_skill_from_bundle(
                        artifact=store.get(skill_name) or artifact,
                        test_result=result,
                        credit_context=skill_credit,
                        store=store,
                        config=config,
                    )
                    refine_decisions.append(post_refine)
                    if post_refine.get("updated_artifact"):
                        store.add(post_refine["updated_artifact"])
        report = {
            "phase": "micro",
            "task_id": detail.get("task_id"),
            "task_index": task_index,
            "maintenance_targets": maintenance_targets,
            "maintenance_test_results": bundle_results,
            "refine_decisions": [
                {k: v for k, v in item.items() if k != "updated_artifact"}
                for item in refine_decisions
            ],
            "credit_bundle_cases": copy.deepcopy(credit_bundle_cases),
            "extraction_reports": extraction_reports,
            "reason": "spreadsheet_micro_maintenance",
        }
        report["trace_projection"] = _spreadsheet_trace_projection(detail)
        return report

    async def run_macro_maintenance(
        self,
        *,
        window_details: Sequence[Dict[str, Any]],
        all_train_details: Sequence[Dict[str, Any]],
        credit_events: Sequence[Dict[str, Any]],
        store: ArtifactStore,
        config: MaintenanceRunConfig,
        round_index: int,
        window_index: int,
        final_window: bool = False,
    ) -> Dict[str, Any]:
        del config, round_index
        promotions = _promote_spreadsheet_pending_from_window(store, window_details)
        dedupe = _dedupe_spreadsheet_skills(store)
        filtered = _filter_spreadsheet_harmful_skills(store, credit_events)
        store.refresh_all_dependencies()
        return {
            "phase": "macro_final" if final_window else "macro",
            "window_index": window_index,
            "task_ids": [str(item.get("task_id") or "") for item in window_details],
            "maintenance_targets": sorted(set(promotions + dedupe + filtered)),
            "maintenance_test_results": [],
            "refine_decisions": [],
            "overlap_refactor": {
                "benchmark": "spreadsheet",
                "attempts": [],
                "promoted_pending_skills": promotions,
                "deduplicated_skills": dedupe,
                "filtered_skills": filtered,
                "window_trace_segments": [
                    _spreadsheet_trace_projection(item) for item in window_details
                ],
                "all_train_count": len(all_train_details),
            },
            "run_overlap_refactor": True,
            "reason": "spreadsheet_macro_promote_dedupe_filter",
        }

    def store_snapshot(self, store: ArtifactStore) -> Dict[str, Any]:
        artifacts = store.all()
        return {
            "n_skills": len(artifacts),
            "n_active": sum(1 for item in artifacts if item.status == "active"),
            "n_pending": sum(1 for item in artifacts if item.status == "pending"),
            "n_disabled": sum(1 for item in artifacts if item.is_disabled()),
            "n_archived": sum(1 for item in artifacts if item.status == "archived"),
            "skill_names": [artifact.name for artifact in artifacts],
            "skill_versions": {artifact.name: artifact.version for artifact in artifacts},
            "skill_status": {artifact.name: artifact.status for artifact in artifacts},
            "bundle_case_counts": {
                artifact.name: len(artifact.bundle.all_cases()) for artifact in artifacts
            },
        }


def _coerce_spreadsheet_artifact(raw: Dict[str, Any], *, detail: Dict[str, Any]) -> SkillArtifact | None:
    name = normalize_skill_name(str(raw.get("name") or ""))
    description = str(raw.get("description") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not name or not description or not body:
        return None
    if str(raw.get("kind") or "executable_tool") == "executable_tool" and "openpyxl" not in body.lower():
        body = "Use openpyxl with INPUT_XLSX and OUTPUT_XLSX.\n\n" + body
    task = _spreadsheet_task_fragment(detail)
    metadata = dict(raw.get("metadata") or {})
    domains = [str(item).strip() for item in (metadata.get("domains") or []) if str(item).strip()]
    if "SpreadsheetBench" not in domains:
        domains.insert(0, "SpreadsheetBench")
    instruction_type = str((task.get("metadata") or {}).get("instruction_type") or "").strip()
    if instruction_type and instruction_type not in domains:
        domains.append(instruction_type)
    metadata.update(
        {
            "domains": domains,
            "allowed_tools": ["openpyxl"],
            "source": metadata.get("source") or "spreadsheet_llm_trace_extraction",
            "source_task_ids": list(dict.fromkeys([*(metadata.get("source_task_ids") or []), str(task.get("task_id") or "")])),
            "benchmark": "spreadsheet",
            "instruction_type": instruction_type,
            "version_kind": "seed",
            "injection_type": metadata.get("injection_type") or "informational",
        }
    )
    tags = [
        "domain:SpreadsheetBench",
        "tool:openpyxl",
        *(f"intent:{item}" for item in metadata.get("intent_keywords") or []),
    ]
    artifact = SkillArtifact(
        name=name,
        kind=str(raw.get("kind") or "executable_tool"),
        description=description[:240],
        body=body[:1400],
        metadata=metadata,
        tags=list(dict.fromkeys(str(tag) for tag in tags if str(tag).strip())),
        interface=SkillInterface(
            summary=str(((raw.get("interface") or {}).get("summary")) or description),
            usage=str(((raw.get("interface") or {}).get("usage")) or "Use when the SpreadsheetBench instruction and workbook shape match this skill scope."),
            input_contract=dict(((raw.get("interface") or {}).get("input_contract")) or {"benchmark": "SpreadsheetBench"}),
            output_contract=dict(((raw.get("interface") or {}).get("output_contract")) or {"tool": "openpyxl"}),
            invocation_contract=dict(((raw.get("interface") or {}).get("invocation_contract")) or {"injection_type": "informational"}),
            compatibility_notes=str(((raw.get("interface") or {}).get("compatibility_notes")) or metadata.get("non_applicability") or ""),
        ),
        evidence=SkillEvidence(
            source_traces=[_spreadsheet_result_projection(detail)],
        ),
        lineage=SkillLineage(version_kind="seed"),
        dependencies=[str(item).strip() for item in (raw.get("dependencies") or []) if str(item).strip()],
        status="pending",
    )
    artifact.bundle.positive_cases.append(
        _spreadsheet_case_from_task(
            detail=detail,
            skill_name=artifact.name,
            polarity="positive",
            reason="successful source trace used to bootstrap the skill contract",
            source="distilled_success",
            confidence=0.7,
        )
    )
    return artifact


async def _extract_spreadsheet_skills_from_detail(
    detail: Dict[str, Any],
    *,
    store: ArtifactStore,
    config: MaintenanceRunConfig,
    task_index: int,
) -> List[SkillArtifact]:
    projection = _spreadsheet_trace_projection(detail)
    has_success_evidence = bool(projection.get("success")) and float(projection.get("score") or 0.0) >= 0.9
    has_repair_evidence = _spreadsheet_has_repair_evidence(projection)
    if not has_success_evidence and not has_repair_evidence:
        return []
    existing = [
        {
            "name": artifact.name,
            "description": artifact.description,
            "status": artifact.status,
            "domains": artifact.metadata.get("domains") or [],
            "intent_keywords": artifact.metadata.get("intent_keywords") or [],
        }
        for artifact in store.all()
        if artifact.status in {"active", "pending", "stale"}
    ]
    try:
        payload = await _ask_json(
            system=SPREADSHEET_EXTRACT_SYSTEM,
            user=json_block(
                {
                    "existing_artifacts": existing[-40:],
                    "result": _spreadsheet_result_projection(detail),
                }
            ),
            llm_config=config.llm_config,
            model_name=config.model_name,
            role="spreadsheet_extractor",
            metadata={"task_id": detail.get("task_id"), "task_index": task_index},
        )
        raw_artifacts = list(payload.get("artifacts") or [])
    except Exception:
        raw_artifacts = [
            _heuristic_spreadsheet_artifact_payload(detail)
            if has_success_evidence
            else _heuristic_spreadsheet_repair_artifact_payload(detail)
        ]
    if not raw_artifacts and has_repair_evidence:
        raw_artifacts = [_heuristic_spreadsheet_repair_artifact_payload(detail)]
    artifacts: List[SkillArtifact] = []
    for raw in raw_artifacts[:3]:
        artifact = _coerce_spreadsheet_artifact(dict(raw or {}), detail=detail)
        if artifact is None:
            continue
        artifacts.append(artifact)
    return artifacts


def _heuristic_spreadsheet_artifact_payload(detail: Dict[str, Any]) -> Dict[str, Any]:
    task = _spreadsheet_task_fragment(detail)
    result = _spreadsheet_result_projection(detail)
    instruction_type = str((task.get("metadata") or {}).get("instruction_type") or "spreadsheet_task").strip()
    answer_position = str((task.get("expected") or {}).get("answer_position") or "").strip()
    question = str(task.get("question") or "")
    keywords = _spreadsheet_keywords(question, instruction_type)
    name = normalize_skill_name(f"spreadsheet_{instruction_type}_{'_'.join(keywords[:4])}")[:80]
    snippet = result["trace"]["code_snippet"]
    body = (
        f"Applicability: SpreadsheetBench {instruction_type} tasks with similar instruction terms "
        f"{keywords[:8]} and answer range `{answer_position}`. Use openpyxl, inspect workbook sheets, "
        "write only the requested answer cells, and preserve unrelated sheets/styles.\n\n"
        "Reusable openpyxl idiom from successful evidence:\n"
        "```python\n"
        f"{snippet}\n"
        "```\n\n"
        "Non-applicability: do not copy hard-coded cell values, sheet names, or row counts unless the "
        "current workbook inspection confirms them."
    )
    return {
        "name": name,
        "kind": "executable_tool",
        "description": f"Openpyxl pattern for {instruction_type} spreadsheet tasks.",
        "body": body,
        "interface": {
            "summary": f"SpreadsheetBench {instruction_type} openpyxl pattern.",
            "usage": "Retrieve for similar SpreadsheetBench instructions before writing Python code.",
            "input_contract": {"benchmark": "SpreadsheetBench", "instruction_type": instruction_type},
            "output_contract": {"tool": "openpyxl", "save_to": "OUTPUT_XLSX"},
            "invocation_contract": {"injection_type": "informational"},
            "compatibility_notes": "Validate workbook layout dynamically; avoid hard-coded values from the source task.",
        },
        "metadata": {
            "domains": ["SpreadsheetBench", instruction_type],
            "allowed_tools": ["openpyxl"],
            "intent_keywords": keywords,
            "source_task_ids": [str(task.get("task_id") or "")],
            "evidence_span": f"successful task score={result.get('score')} answer_position={answer_position}",
            "scope": f"SpreadsheetBench {instruction_type}",
            "non_applicability": "Current workbook layout or instruction type differs materially from the source evidence.",
            "maintenance_action": "new_skill",
        },
        "dependencies": [],
    }


def _spreadsheet_has_repair_evidence(projection: Dict[str, Any]) -> bool:
    if projection.get("execution_ok") is False and projection.get("stderr_tail"):
        return True
    mismatches = list(projection.get("mismatched_cells") or [])
    if not mismatches:
        return False
    expected_text = " ".join(str(item.get("expected") or "") for item in mismatches if isinstance(item, dict))
    predicted_text = " ".join(str(item.get("predicted") or "") for item in mismatches if isinstance(item, dict))
    return bool(expected_text.strip() or predicted_text.strip())


def _heuristic_spreadsheet_repair_artifact_payload(detail: Dict[str, Any]) -> Dict[str, Any]:
    task = _spreadsheet_task_fragment(detail)
    result = _spreadsheet_result_projection(detail)
    projection = _spreadsheet_trace_projection(detail)
    instruction_type = str((task.get("metadata") or {}).get("instruction_type") or "spreadsheet_repair").strip()
    mismatches = list(projection.get("mismatched_cells") or [])
    expected_examples = [
        {"cell": item.get("cell"), "expected": item.get("expected")}
        for item in mismatches
        if isinstance(item, dict)
    ][:4]
    predicted_examples = [
        {"cell": item.get("cell"), "predicted": item.get("predicted")}
        for item in mismatches
        if isinstance(item, dict)
    ][:4]
    question = str(task.get("question") or "")
    keywords = _spreadsheet_keywords(question, instruction_type)
    formula_tokens = _spreadsheet_formula_tokens(expected_examples)
    name = normalize_skill_name(
        f"spreadsheet_repair_{instruction_type}_{'_'.join((formula_tokens or keywords)[:4])}"
    )[:90]
    body = (
        f"Applicability: SpreadsheetBench {instruction_type} tasks with similar instruction terms "
        f"{keywords[:8]} and verifier evidence matching these expected formula/value patterns: "
        f"{json.dumps(expected_examples, ensure_ascii=False)}.\n\n"
        "Corrective rule: when generating formulas, preserve the official spreadsheet semantics "
        "visible in the task and workbook preview, including blank-cell guards, lookup ranges, "
        "absolute references, and formula strings. Prefer writing the formula pattern itself when "
        "the answer range expects formulas, not computed display values.\n\n"
        "Failure evidence to avoid:\n"
        f"predicted={json.dumps(predicted_examples, ensure_ascii=False)}\n"
        f"expected={json.dumps(expected_examples, ensure_ascii=False)}\n\n"
        "Openpyxl idiom:\n"
        "```python\n"
        "import openpyxl\n"
        "wb = openpyxl.load_workbook(INPUT_XLSX)\n"
        "# inspect workbook layout, then write formulas/values to the requested answer range\n"
        "wb.save(OUTPUT_XLSX)\n"
        "```\n\n"
        "Non-applicability: do not hard-code this exact formula unless the current task has the "
        "same workbook layout, answer range, and instruction semantics."
    )
    return {
        "name": name,
        "kind": "workflow_guardrail_card",
        "description": f"Repair pattern for {instruction_type} SpreadsheetBench formula/value mismatches.",
        "body": body,
        "interface": {
            "summary": f"SpreadsheetBench {instruction_type} mismatch repair pattern.",
            "usage": "Retrieve for similar formula/value mismatch-prone spreadsheet tasks.",
            "input_contract": {"benchmark": "SpreadsheetBench", "instruction_type": instruction_type},
            "output_contract": {"tool": "openpyxl", "save_to": "OUTPUT_XLSX", "preserve_formula_semantics": True},
            "invocation_contract": {"injection_type": "workflow"},
            "compatibility_notes": "Validate workbook layout and answer range before applying any source formula pattern.",
        },
        "metadata": {
            "domains": ["SpreadsheetBench", instruction_type],
            "allowed_tools": ["openpyxl"],
            "intent_keywords": list(dict.fromkeys([*keywords, *formula_tokens])),
            "source_task_ids": [str(task.get("task_id") or "")],
            "evidence_span": f"failed trace score={result.get('score')} mismatches={expected_examples}",
            "scope": f"SpreadsheetBench {instruction_type} mismatch repair",
            "non_applicability": "Do not apply when formula/value semantics, sheet layout, or answer range differ.",
            "maintenance_action": "new_skill",
            "extracted_from_failure": True,
        },
        "dependencies": [],
    }


def _spreadsheet_formula_tokens(examples: Sequence[Dict[str, Any]]) -> List[str]:
    text = " ".join(str(item.get("expected") or "") for item in examples)
    tokens = []
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text):
        lowered = word.lower()
        if lowered in {"if", "or", "and"} or len(lowered) >= 4:
            if lowered not in tokens:
                tokens.append(lowered)
        if len(tokens) >= 8:
            break
    return tokens


def _spreadsheet_keywords(question: str, instruction_type: str = "") -> List[str]:
    raw = f"{instruction_type} {question}"
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]+", raw.lower())
    stop = {
        "the", "and", "for", "with", "into", "from", "this", "that", "workbook",
        "spreadsheet", "sheet", "cell", "cells", "please", "using", "make",
    }
    out: List[str] = []
    for word in words:
        if len(word) < 3 or word in stop:
            continue
        if word not in out:
            out.append(word)
        if len(out) >= 12:
            break
    return out or ["spreadsheet", "openpyxl"]


def _normalize_spreadsheet_credit_events(
    payload: Dict[str, Any],
    *,
    detail: Dict[str, Any],
    candidate_artifacts: Sequence[SkillArtifact],
    projection: Dict[str, Any],
) -> List[Dict[str, Any]]:
    known = {artifact.name for artifact in candidate_artifacts}
    events: List[Dict[str, Any]] = []
    for raw in payload.get("skill_judgments") or []:
        row = dict(raw or {})
        skill_name = str(row.get("skill_name") or "").strip()
        if skill_name not in known:
            continue
        judgment = str(row.get("judgment") or "uncertain").strip().lower()
        if judgment not in {"helpful", "harmful", "neutral", "uncertain"}:
            judgment = "uncertain"
        suggestions = []
        for item in row.get("bundle_case_suggestions") or []:
            suggestion = dict(item or {})
            suggestion.setdefault("skill_name", skill_name)
            suggestions.append(_normalize_spreadsheet_bundle_suggestion(suggestion, detail=detail))
        events.append(
            {
                "benchmark": "spreadsheet",
                "task_id": detail.get("task_id"),
                "skill_name": skill_name,
                "judgment": judgment,
                "effect_type": str(row.get("effect_type") or "unknown").strip().lower() or "unknown",
                "confidence": max(0.0, min(1.0, float(row.get("confidence") or 0.0))),
                "reason": str(row.get("reason") or ""),
                "maintenance_actions": [
                    dict(item or {}) for item in (row.get("maintenance_actions") or []) if isinstance(item, dict)
                ],
                "refine_required": bool(row.get("refine_required")),
                "filter_candidate": bool(row.get("filter_candidate")),
                "evidence_strength": str(row.get("evidence_strength") or "weak").strip().lower() or "weak",
                "attribution_scope": str(row.get("attribution_scope") or "none").strip().lower() or "none",
                "bundle_case_suggestions": suggestions,
                "evidence": copy.deepcopy(dict(row.get("evidence") or {})),
                "projection": copy.deepcopy(projection),
            }
        )
    return events


def _normalize_spreadsheet_bundle_suggestion(
    suggestion: Dict[str, Any],
    *,
    detail: Dict[str, Any],
) -> Dict[str, Any]:
    polarity = str(suggestion.get("polarity") or "").strip().lower()
    if polarity not in {"positive", "negative", "integration"}:
        polarity = "integration"
    policy = str(suggestion.get("task_fragment_policy") or "reuse_official_fragment").strip()
    return {
        "skill_name": str(suggestion.get("skill_name") or ""),
        "polarity": polarity,
        "reason": str(suggestion.get("reason") or ""),
        "source_task_id": str(suggestion.get("source_task_id") or detail.get("task_id") or ""),
        "focus_turn_indices": [0] if policy != "no_replayable_fragment" else [],
        "required_context_turn_indices": [],
        "state_requirements": copy.deepcopy(dict(suggestion.get("state_requirements") or {})),
        "expected_contract": str(suggestion.get("expected_contract") or "SpreadsheetBench verifier should match the official answer range in the golden workbook."),
        "task_fragment_policy": policy if policy in {"reuse_official_fragment", "no_replayable_fragment"} else "reuse_official_fragment",
    }


def _heuristic_spreadsheet_credit_events(
    *,
    detail: Dict[str, Any],
    candidate_artifacts: Sequence[SkillArtifact],
    projection: Dict[str, Any],
    reason: str,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    success = bool(projection.get("success"))
    score = float(projection.get("score") or 0.0)
    stderr = str(projection.get("stderr_tail") or "")
    mismatches = projection.get("mismatched_cells") or []
    for artifact in candidate_artifacts:
        overlap = _spreadsheet_scope_overlap(artifact, detail)
        if success and score >= 0.9 and overlap:
            judgment = "helpful"
            effect = "correctness_gain"
            confidence = 0.55
            suggestions = [
                {
                    "skill_name": artifact.name,
                    "polarity": "positive",
                    "reason": "successful task with retrieved in-scope spreadsheet skill",
                    "source_task_id": detail.get("task_id"),
                    "focus_turn_indices": [0],
                    "required_context_turn_indices": [],
                    "state_requirements": {},
                    "expected_contract": "official SpreadsheetBench answer range should match the golden workbook",
                    "task_fragment_policy": "reuse_official_fragment",
                }
            ]
        elif (stderr or mismatches) and overlap:
            judgment = "uncertain"
            effect = "unknown"
            confidence = 0.35
            suggestions = []
        else:
            judgment = "neutral"
            effect = "no_material_effect"
            confidence = 0.5
            suggestions = []
        events.append(
            {
                "benchmark": "spreadsheet",
                "task_id": detail.get("task_id"),
                "skill_name": artifact.name,
                "judgment": judgment,
                "effect_type": effect,
                "confidence": confidence,
                "reason": reason,
                "maintenance_actions": [],
                "refine_required": False,
                "filter_candidate": False,
                "evidence_strength": "weak",
                "attribution_scope": "prompt_influence" if judgment == "helpful" else "none",
                "bundle_case_suggestions": suggestions,
                "evidence": {
                    **skill_exposure_flags(artifact.name, projection),
                    "trace_signals": [reason],
                },
                "projection": copy.deepcopy(projection),
            }
        )
    return events


def _spreadsheet_scope_overlap(artifact: SkillArtifact, detail: Dict[str, Any]) -> bool:
    task = _spreadsheet_task_fragment(detail)
    instruction_type = str((task.get("metadata") or {}).get("instruction_type") or "").lower()
    question = str(task.get("question") or "").lower()
    keywords = [str(item).lower() for item in (artifact.metadata.get("intent_keywords") or [])]
    domains = [str(item).lower() for item in (artifact.metadata.get("domains") or [])]
    if instruction_type and instruction_type in domains:
        return True
    return any(keyword and keyword in question for keyword in keywords)


def _spreadsheet_case_from_credit_suggestion(
    *,
    detail: Dict[str, Any],
    artifact: SkillArtifact,
    event: Dict[str, Any],
    suggestion: Dict[str, Any],
) -> SkillBundleCase | None:
    policy = str(suggestion.get("task_fragment_policy") or "").strip()
    if policy == "no_replayable_fragment":
        artifact.evidence.repeated_evidence.append(
            {
                "task_id": detail.get("task_id"),
                "skill_name": artifact.name,
                "reason": suggestion.get("reason") or event.get("reason"),
                "not_replayable": True,
            }
        )
        return None
    polarity = str(suggestion.get("polarity") or "").strip().lower()
    confidence = float(event.get("confidence") or 0.0)
    judgment = str(event.get("judgment") or "")
    used = bool((event.get("evidence") or {}).get("used"))
    if polarity == "negative" and not (judgment == "harmful" and (confidence >= 0.65 or used)):
        return None
    if polarity == "positive" and str(event.get("effect_type") or "") not in {
        "token_saving",
        "schema_help",
        "workflow_alignment",
        "correctness_gain",
    }:
        return None
    return _spreadsheet_case_from_task(
        detail=detail,
        skill_name=artifact.name,
        polarity=polarity,
        reason=str(suggestion.get("reason") or event.get("reason") or ""),
        source=f"credit_assigner_{polarity}",
        confidence=confidence,
        credit_event=event,
    )


def _spreadsheet_case_from_task(
    *,
    detail: Dict[str, Any],
    skill_name: str,
    polarity: str,
    reason: str,
    source: str,
    confidence: float,
    credit_event: Dict[str, Any] | None = None,
) -> SkillBundleCase:
    task = _spreadsheet_task_fragment(detail)
    case_id = f"{skill_name}:{polarity}:{stable_id(task.get('task_id'), reason, source)}"
    return SkillBundleCase(
        case_id=case_id,
        source=source,
        prompt=str(task.get("question") or "")[:240],
        expected={
            "answer_sheet": (task.get("expected") or {}).get("answer_sheet"),
            "answer_position": (task.get("expected") or {}).get("answer_position"),
            "verifier": "spreadsheet_golden_range",
        },
        context={
            "task_fragment": copy.deepcopy(task),
            "source_task_id": task.get("task_id"),
            "focus_turns": [0],
            "focus_tools": ["openpyxl"],
            "reason": reason,
            "credit_event": copy.deepcopy(credit_event or {}),
            "confidence": confidence,
        },
        tags=["spreadsheet", polarity],
        polarity=polarity,
        contrast_protocol={"with_skill": True, "without_skill": polarity != "negative"},
    )


def _trim_spreadsheet_bundle_cases(artifact: SkillArtifact) -> None:
    trim_bundle_cases_to_budget(artifact, priority_fn=_spreadsheet_case_priority)


def _spreadsheet_case_priority(case: SkillBundleCase, index: int = 0) -> tuple[Any, ...]:
    return default_bundle_case_priority(case, index)


def _spreadsheet_micro_targets(
    *,
    credit_events: Sequence[Dict[str, Any]],
    credit_bundle_cases: Sequence[Dict[str, Any]],
    extracted: Sequence[SkillArtifact],
) -> List[str]:
    targets: List[str] = []
    for event in credit_events:
        judgment = str(event.get("judgment") or "")
        confidence = float(event.get("confidence") or 0.0)
        if event.get("refine_required") or event.get("filter_candidate"):
            targets.append(str(event.get("skill_name") or ""))
        elif judgment == "harmful" and confidence >= 0.65:
            targets.append(str(event.get("skill_name") or ""))
        elif judgment == "helpful" and confidence >= 0.55:
            targets.append(str(event.get("skill_name") or ""))
    targets.extend(str(item.get("skill_name") or "") for item in credit_bundle_cases)
    return sorted({name for name in targets if name})


async def _refine_spreadsheet_skill_from_credit(
    *,
    artifact: SkillArtifact,
    credit_context: Sequence[Dict[str, Any]],
    detail: Dict[str, Any],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
) -> Dict[str, Any]:
    strong = [
        event for event in credit_context
        if event.get("refine_required")
        or event.get("filter_candidate")
        or (event.get("judgment") == "harmful" and float(event.get("confidence") or 0.0) >= 0.65)
    ]
    if not strong:
        return {"skill_name": artifact.name, "action": "keep", "reason": "no_strong_credit_signal"}
    test_result = {
        "result_id": f"{artifact.name}:credit:{stable_id(detail.get('task_id'), now_iso())}",
        "aggregate": {"passed": False, "reason": "pre_bundle_credit_refine"},
        "failed_cases": [_spreadsheet_result_projection(detail)],
    }
    return await _run_spreadsheet_refiner(
        artifact=artifact,
        test_result=test_result,
        credit_context=list(strong),
        store=store,
        config=config,
        phase="spreadsheet_credit_pre_refine",
    )


async def _refine_spreadsheet_skill_from_bundle(
    *,
    artifact: SkillArtifact,
    test_result: SkillTestResult,
    credit_context: Sequence[Dict[str, Any]],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
) -> Dict[str, Any]:
    return await _run_spreadsheet_refiner(
        artifact=artifact,
        test_result=test_result.as_dict(),
        credit_context=list(credit_context),
        store=store,
        config=config,
        phase="spreadsheet_bundle_refine",
    )


async def _run_spreadsheet_refiner(
    *,
    artifact: SkillArtifact,
    test_result: Dict[str, Any],
    credit_context: Sequence[Dict[str, Any]],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
    phase: str,
) -> Dict[str, Any]:
    try:
        payload = await refine_skill_artifact_llm(
            artifact,
            test_result=test_result,
            credit_context=list(credit_context),
            refinement_history=artifact.history[-3:],
            dependency_summaries=summarize_dependency_context(store.all()),
            llm_config=config.llm_config,
            model_name=config.model_name,
            audit_context={"phase": phase, "benchmark": "spreadsheet"},
        )
    except Exception as exc:
        if any(event.get("filter_candidate") for event in credit_context):
            updated = copy.deepcopy(artifact)
            updated.status = "disabled"
            updated.metadata["disabled"] = True
            updated.metadata["disabled_reason"] = f"spreadsheet_refiner_unavailable_after_filter_credit:{type(exc).__name__}"
            return {
                "skill_name": artifact.name,
                "action": "disable",
                "reason": updated.metadata["disabled_reason"],
                "updated_artifact": updated,
            }
        return {
            "skill_name": artifact.name,
            "action": "keep",
            "reason": f"refiner_failed:{type(exc).__name__}",
        }
    decision = dict(payload.get("decision") or {})
    action = str(decision.get("action") or "keep").strip()
    if action == "keep":
        return {"skill_name": artifact.name, "action": "keep", "reason": decision.get("reason") or ""}
    updated = apply_refine_payload(artifact, payload)
    if _artifact_semantic_signature(updated) == _artifact_semantic_signature(artifact):
        return {"skill_name": artifact.name, "action": "keep", "reason": "refiner_returned_no_semantic_change"}
    return {
        "skill_name": artifact.name,
        "action": action,
        "reason": decision.get("reason") or "",
        "updated_artifact": updated,
    }


def _artifact_semantic_signature(artifact: SkillArtifact) -> str:
    payload = {
        "kind": artifact.kind,
        "description": artifact.description,
        "body": artifact.body,
        "metadata": artifact.metadata,
        "interface": artifact.interface.as_dict(),
        "status": artifact.status,
        "dependencies": artifact.dependencies,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


async def _execute_spreadsheet_bundle_tests(
    *,
    artifact: SkillArtifact,
    config: MaintenanceRunConfig,
) -> SkillTestResult:
    case_runs: List[SkillTestCaseRun] = []
    for case in artifact.bundle.all_cases():
        task_fragment = dict((case.context or {}).get("task_fragment") or {})
        task = BenchmarkTask(
            benchmark="spreadsheet",
            task_id=str(task_fragment.get("task_id") or case.case_id),
            question=task_fragment.get("question") or case.prompt,
            expected=copy.deepcopy(dict(task_fragment.get("expected") or {})),
            input_artifacts=copy.deepcopy(dict(task_fragment.get("input_artifacts") or {})),
            metadata=copy.deepcopy(dict(task_fragment.get("metadata") or {})),
        )
        if not task.input_artifacts.get("input_xlsx") or not task.expected.get("golden_xlsx"):
            case_runs.append(
                SkillTestCaseRun(
                    case_id=case.case_id,
                    variant="with_skill",
                    passed=False,
                    accuracy=0.0,
                    failure_summary="missing official workbook paths",
                    trace_ref=task.task_id,
                    bundle_case_snapshot=case.as_dict(),
                    metadata={"polarity": case.polarity, "contract_failure": "missing_workbook_paths"},
                )
            )
            continue
        result = await run_spreadsheet_task(
            task,
            llm_config=config.llm_config,
            model_name=config.model_name,
            artifact_store=ArtifactStore([copy.deepcopy(artifact)]),
            top_k_skills=1,
        )
        metrics = dict(result.metrics or {})
        case_runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="with_skill",
                passed=bool(result.success),
                accuracy=float(result.score or 0.0),
                tokens=int(metrics.get("total_tokens") or 0),
                failure_summary="" if result.success else str(result.error or metrics.get("reason") or metrics.get("mismatched_cells") or "failed"),
                trace_ref=result.task_id,
                trace=copy.deepcopy(result.trace),
                input_payload={"task": task.as_dict(), "skill_name": artifact.name},
                expected_behavior=copy.deepcopy(case.expected),
                actual_output={"metrics": metrics, "score": result.score, "success": result.success},
                trace_summary=_spreadsheet_trace_projection(
                    {"task_id": result.task_id, "task": task.as_dict(), "runs": [result.as_dict()]}
                ),
                skill_snapshot={"name": artifact.name, "version": artifact.version},
                bundle_case_snapshot=case.as_dict(),
                metadata={"polarity": case.polarity},
            )
        )
    passed = bool(case_runs) and all(run.passed for run in case_runs)
    avg_accuracy = round(
        sum(float(run.accuracy or 0.0) for run in case_runs) / max(len(case_runs), 1),
        4,
    )
    result = SkillTestResult(
        result_id=f"{artifact.name}:spreadsheet_bundle:{stable_id(artifact.version, now_iso())}",
        skill_name=artifact.name,
        skill_version=artifact.version,
        bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
        bundle_version=artifact.bundle.bundle_version,
        run_label="spreadsheet_bundle_unit",
        unit_case_runs=case_runs,
        aggregate={
            "passed": passed,
            "n_cases": len(case_runs),
            "n_passed": sum(1 for run in case_runs if run.passed),
            "avg_accuracy": avg_accuracy,
        },
        created_at=now_iso(),
    )
    return result


def _promote_spreadsheet_pending_from_window(
    store: ArtifactStore,
    window_details: Sequence[Dict[str, Any]],
) -> List[str]:
    window_task_ids = {str(item.get("task_id") or "") for item in window_details}
    promoted: List[str] = []
    for artifact in store.pending_artifacts():
        source_ids = {str(item) for item in (artifact.metadata.get("source_task_ids") or [])}
        if not source_ids & window_task_ids:
            continue
        if not artifact.bundle.positive_cases:
            continue
        if store.promote_pending(
            artifact.name,
            reason="spreadsheet_successful_source_trace_in_macro_window",
            refactor_group_id=f"spreadsheet_macro:{stable_id(*sorted(window_task_ids))}",
        ):
            promoted.append(artifact.name)
    return promoted


def _dedupe_spreadsheet_skills(store: ArtifactStore) -> List[str]:
    active = [artifact for artifact in store.all() if artifact.status == "active" and not artifact.is_disabled()]
    groups: Dict[str, List[SkillArtifact]] = {}
    for artifact in active:
        key = _spreadsheet_dedupe_key(artifact)
        groups.setdefault(key, []).append(artifact)
    archived: List[str] = []
    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(
            key=lambda item: (
                item.success_count,
                item.usage_count,
                len(item.evidence.helpful_cases),
                -len(item.evidence.harmful_cases),
            ),
            reverse=True,
        )
        keeper = group[0]
        for duplicate in group[1:]:
            duplicate.status = "archived"
            duplicate.metadata["archived_reason"] = f"spreadsheet_duplicate_of:{keeper.name}"
            duplicate.metadata["duplicate_keeper"] = keeper.name
            keeper.evidence.repeated_evidence.append(
                {
                    "merged_duplicate": duplicate.name,
                    "source_task_ids": duplicate.metadata.get("source_task_ids") or [],
                }
            )
            archived.append(duplicate.name)
    return archived


def _spreadsheet_dedupe_key(artifact: SkillArtifact) -> str:
    instruction_type = str(artifact.metadata.get("instruction_type") or "").lower()
    keywords = sorted(str(item).lower() for item in (artifact.metadata.get("intent_keywords") or [])[:6])
    text = " ".join([instruction_type, artifact.kind, *keywords])
    return stable_id(text, length=12)


def _filter_spreadsheet_harmful_skills(
    store: ArtifactStore,
    credit_events: Sequence[Dict[str, Any]],
) -> List[str]:
    harmful_by_skill: Dict[str, List[Dict[str, Any]]] = {}
    helpful_by_skill: Dict[str, int] = {}
    for event in credit_events:
        skill_name = str(event.get("skill_name") or "")
        if not skill_name:
            continue
        if event.get("judgment") == "harmful" and float(event.get("confidence") or 0.0) >= 0.65:
            harmful_by_skill.setdefault(skill_name, []).append(dict(event))
        if event.get("judgment") == "helpful":
            helpful_by_skill[skill_name] = helpful_by_skill.get(skill_name, 0) + 1
    filtered: List[str] = []
    for skill_name, rows in harmful_by_skill.items():
        if len(rows) < 2 or helpful_by_skill.get(skill_name, 0) > 0:
            continue
        artifact = store.get(skill_name)
        if artifact is None or artifact.is_disabled():
            continue
        artifact.status = "disabled"
        artifact.metadata["disabled"] = True
        artifact.metadata["disabled_reason"] = "spreadsheet_macro_repeated_high_confidence_harmful_credit"
        filtered.append(skill_name)
    return filtered


def _split_sheet_range(answer_range: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not answer_range:
        return None, answer_range
    text = str(answer_range).strip()
    if "!" not in text:
        return None, text.strip().strip("'").strip('"')
    sheet, cell_range = text.rsplit("!", 1)
    sheet = sheet.strip().strip("'").strip('"')
    cell_range = cell_range.strip().strip("'").strip('"')
    return sheet or None, cell_range


def _answer_range_refs(
    answer_range: Optional[str],
    *,
    default_sheet: Optional[str],
) -> List[Tuple[Optional[str], Optional[str]]]:
    parts = _split_answer_range_list(answer_range)
    refs: List[Tuple[Optional[str], Optional[str]]] = []
    for part in parts:
        sheet, cell_range = _split_sheet_range(_normalize_answer_range_text(part))
        refs.append((sheet or default_sheet, cell_range))
    if not refs and default_sheet:
        refs.append((default_sheet, None))
    return refs


def _split_answer_range_list(answer_range: Optional[str]) -> List[str]:
    text = str(answer_range or "").strip()
    if not text:
        return []
    parts: List[str] = []
    current: List[str] = []
    in_quote = False
    for char in text:
        if char == "'":
            in_quote = not in_quote
        if char == "," and not in_quote:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _normalize_answer_range_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    # Some SpreadsheetBench rows encode ranges as "'Sheet1!'A1:B2".
    text = re.sub(r"^'([^']+!)'([A-Z]+[0-9]+(?::[A-Z]+[0-9]+)?)$", r"'\1\2", text)
    text = text.strip().strip('"')
    if text.count("'") == 1 and text.startswith("'") and "!" in text:
        text = text[1:]
    return text


def _first_sheet_name(sheet_name: Optional[str]) -> Optional[str]:
    if not sheet_name:
        return None
    return str(sheet_name).split(",", 1)[0].strip().strip("'").strip('"') or None


def _normalize_cell_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except Exception:
            return text
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return round(float(text), 8)
        except Exception:
            return text
    return text


def _jsonable_cell_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
