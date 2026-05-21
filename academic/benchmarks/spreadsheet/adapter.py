"""SpreadsheetBench compatibility facade.

Execution, loading, verification, skill runtime, trace projection, and
maintenance helpers live in focused modules.  This facade preserves the public
and test-facing imports that previously came from this file.
"""
from __future__ import annotations

from typing import Any

import academic.benchmarks.spreadsheet.executor as _spreadsheet_executor
from academic.benchmarks.core.llm_text import ask_text_llm
from academic.benchmarks.core.types import BenchmarkResult
from academic.benchmarks.spreadsheet.executor import (
    build_spreadsheet_notebook_prompt as _build_spreadsheet_notebook_prompt,
    build_spreadsheet_notebook_turn_prompt as _build_spreadsheet_notebook_turn_prompt,
    build_spreadsheet_prompt as _build_spreadsheet_prompt,
    clip_notebook_text as _clip_notebook_text,
    run_spreadsheet_task_bash_react as _run_spreadsheet_task_bash_react_impl,
    run_spreadsheet_task as _run_spreadsheet_task_impl,
    run_spreadsheet_task_notebook as _run_spreadsheet_task_notebook_impl,
    workbook_preview as _workbook_preview,
)
from academic.benchmarks.spreadsheet.loader import ensure_spreadsheetbench, load_spreadsheet_task_pool, load_spreadsheet_tasks
from academic.benchmarks.spreadsheet.maintenance.adapter import *  # noqa: F401,F403
from academic.benchmarks.spreadsheet.maintenance.adapter import (
    SpreadsheetMaintenanceAdapter,
    _artifact_semantic_signature,
    _coerce_spreadsheet_artifact,
    _dedupe_spreadsheet_skills,
    _execute_spreadsheet_bundle_tests,
    _extract_spreadsheet_skills_from_detail,
    _filter_spreadsheet_harmful_skills,
    _heuristic_spreadsheet_artifact_payload,
    _heuristic_spreadsheet_credit_events,
    _heuristic_spreadsheet_repair_artifact_payload,
    _normalize_spreadsheet_bundle_suggestion,
    _normalize_spreadsheet_credit_events,
    _promote_spreadsheet_pending_from_window,
    _refine_spreadsheet_skill_from_bundle,
    _refine_spreadsheet_skill_from_credit,
    _run_spreadsheet_refiner,
    _spreadsheet_case_from_credit_suggestion,
    _spreadsheet_case_from_task,
    _spreadsheet_dedupe_key,
    _spreadsheet_formula_tokens,
    _spreadsheet_has_repair_evidence,
    _spreadsheet_keywords,
    _spreadsheet_micro_targets,
    _spreadsheet_scope_overlap,
    _spreadsheet_test_result_from_dict,
    _trim_spreadsheet_bundle_cases,
)
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
    is_spreadsheet_package_skill as _is_spreadsheet_package_skill,
    write_spreadsheet_skill_packages as _write_spreadsheet_skill_packages,
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


async def run_spreadsheet_task_bash_react(*args: Any, **kwargs: Any) -> BenchmarkResult:
    _spreadsheet_executor.ask_text_llm = ask_text_llm
    return await _run_spreadsheet_task_bash_react_impl(*args, **kwargs)
