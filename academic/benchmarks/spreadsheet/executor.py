"""SpreadsheetBench task execution."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import openpyxl

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.cost_accounting import make_cost_event
from academic.benchmarks.core.llm_text import ask_text_llm
from academic.benchmarks.core.skill_injector import BudgetSkillInjector
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask
from academic.benchmarks.spreadsheet.models import SpreadsheetTrace
from academic.benchmarks.spreadsheet.prompts import (
    SPREADSHEET_DONE_PATTERN,
    SPREADSHEET_NOTEBOOK_SYSTEM,
    SPREADSHEET_SYSTEM,
)
from academic.benchmarks.spreadsheet.skill_runtime import (
    NotebookPythonSession,
    called_spreadsheet_skill_functions,
    extract_code,
    run_code,
    write_spreadsheet_skill_library,
)
from academic.benchmarks.spreadsheet.verifier import jsonable_cell_value, verify_spreadsheet_output
from academic.config import CODE_EXEC_TIMEOUT


async def run_spreadsheet_task(
    task: BenchmarkTask,
    *,
    llm_config: str,
    model_name: Optional[str] = None,
    artifact_store: Optional[ArtifactStore] = None,
    top_k_skills: int = 5,
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
    work_dir: Optional[Path] = None,
) -> BenchmarkResult:
    t0 = time.monotonic()
    trace = SpreadsheetTrace(task_id=task.task_id)
    query = str(task.question)
    retrieved = artifact_store.retrieve(query, top_k=top_k_skills) if artifact_store else []
    trace.retrieved_skills = [skill.name for skill in retrieved]
    injector_mode = (
        skill_injector_mode
        or os.environ.get("SPREADSHEET_SKILL_INJECTOR_MODE")
        or os.environ.get("SKILL_INJECTOR_MODE")
        or "full"
    ).strip().lower()
    injected = list(retrieved)
    skill_prompt = artifact_store.build_prompt(injected) if artifact_store else "(none)"
    if artifact_store and injector_mode not in {"", "full"}:
        injector = BudgetSkillInjector(
            mode=injector_mode,
            max_full_skills=int(os.environ.get("SPREADSHEET_SKILL_INJECTOR_MAX_FULL_SKILLS", os.environ.get("SKILL_INJECTOR_MAX_FULL_SKILLS", "0")) or "0"),
            max_summary_skills=int(os.environ.get("SPREADSHEET_SKILL_INJECTOR_MAX_SUMMARY_SKILLS", os.environ.get("SKILL_INJECTOR_MAX_SUMMARY_SKILLS", "2")) or "2"),
            budget_chars=int(skill_context_budget_chars or os.environ.get("SPREADSHEET_SKILL_CONTEXT_BUDGET_CHARS", os.environ.get("SKILL_INJECTOR_BUDGET_CHARS", "2200")) or "2200"),
            compact_chars_per_skill=int(os.environ.get("SPREADSHEET_SKILL_COMPACT_CHARS_PER_SKILL", os.environ.get("SKILL_INJECTOR_COMPACT_CHARS_PER_SKILL", "900")) or "900"),
        )
        injection = injector.select(
            retrieved,
            query=query,
            allowed_injection_types={"informational", "workflow", "functional"},
        )
        injected = injection.artifacts
        skill_prompt = injection.prompt()
        trace.filtered_skills = list(injection.filtered)
        trace.injector_events = [injection.as_event()]
        trace.cost_events.append(
            make_cost_event(
                role="injector",
                phase="executor",
                benchmark="spreadsheet",
                task_id=task.task_id,
                model=model_name or "",
                llm_config=llm_config,
                skill_prompt_chars=int(trace.injector_events[0].get("prompt_chars") or 0),
                metadata={
                    "mode": trace.injector_events[0].get("mode"),
                    "injected_count": trace.injector_events[0].get("injected_count"),
                    "filtered_count": trace.injector_events[0].get("filtered_count"),
                    "deterministic": True,
                    "execution_mode": "single",
                },
            )
        )

    base_work_dir = work_dir or Path(tempfile.mkdtemp(prefix="spreadsheetbench_"))
    base_work_dir.mkdir(parents=True, exist_ok=True)
    callable_prompt = write_spreadsheet_skill_library(injected, base_work_dir)
    trace.callable_skills = callable_prompt["skills"]
    trace.prompt_injected_skills = [skill.name for skill in injected]
    system = SPREADSHEET_SYSTEM.format(
        skills=skill_prompt,
        callable_skills=callable_prompt["prompt"] or "(no callable function skills available)",
    )
    input_copy = base_work_dir / f"{task.task_id}_input.xlsx"
    output_path = base_work_dir / f"{task.task_id}_output.xlsx"
    shutil.copyfile(task.input_artifacts["input_xlsx"], input_copy)
    shutil.copyfile(input_copy, output_path)
    prompt = build_spreadsheet_prompt(task, input_copy, output_path)
    trace.prompt = prompt
    try:
        response = await ask_text_llm(
            llm_config=llm_config,
            model_name=model_name,
            system=system,
            prompt=prompt,
        )
        trace.input_tokens += response.prompt_tokens
        trace.cache_input_tokens += response.cache_input_tokens
        trace.completion_tokens += response.completion_tokens
        trace.total_tokens += response.prompt_tokens + response.cache_input_tokens + response.completion_tokens
        trace.cost_events.append(
            make_cost_event(
                role="executor",
                phase="task_rollout",
                benchmark="spreadsheet",
                task_id=task.task_id,
                model=response.model_name or model_name or "",
                llm_config=llm_config,
                input_tokens=response.prompt_tokens,
                cache_input_tokens=response.cache_input_tokens,
                output_tokens=response.completion_tokens,
                prompt_chars=len(prompt),
                skill_prompt_chars=len(skill_prompt) + len(callable_prompt["prompt"] or ""),
                system_prompt_chars=len(system),
                final_conversation_chars=len(system) + len(prompt) + len(response.content or ""),
                metadata={
                    "api_style": response.api_style,
                    "skill_injector_mode": injector_mode,
                    "prompt_injected_skills": list(trace.prompt_injected_skills),
                    "callable_skills": list(trace.callable_skills),
                    "execution_mode": "single",
                },
            )
        )
        trace.code = extract_code(response.content)
        if not trace.code:
            trace.elapsed_s = round(time.monotonic() - t0, 3)
            return BenchmarkResult(
                benchmark="spreadsheet",
                task_id=task.task_id,
                success=False,
                score=0.0,
                metrics={"reason": "no_python_code"},
                trace=trace.as_dict(),
            )
        trace.called_skill_functions = called_spreadsheet_skill_functions(
            trace.code,
            callable_prompt["skills"],
        )
        stdout, stderr, returncode = await asyncio.to_thread(
            run_code, trace.code, input_copy, output_path, base_work_dir
        )
        trace.stdout = stdout
        trace.stderr = stderr
        verify = verify_spreadsheet_output(
            predicted_xlsx=output_path,
            golden_xlsx=Path(task.expected["golden_xlsx"]),
            sheet_name=task.expected.get("answer_sheet"),
            answer_range=task.expected.get("answer_position"),
        )
        verify["returncode"] = returncode
        verify["execution_ok"] = returncode == 0
        success = bool(returncode == 0 and verify["pass"])
    except Exception as exc:
        trace.elapsed_s = round(time.monotonic() - t0, 3)
        return BenchmarkResult(
            benchmark="spreadsheet",
            task_id=task.task_id,
            success=False,
            score=0.0,
            metrics={"exception": type(exc).__name__},
            trace=trace.as_dict(),
            error=str(exc),
        )

    trace.elapsed_s = round(time.monotonic() - t0, 3)
    verify["total_tokens"] = trace.total_tokens
    verify["input_tokens"] = trace.input_tokens
    verify["cache_input_tokens"] = trace.cache_input_tokens
    verify["completion_tokens"] = trace.completion_tokens
    verify["cost_events"] = trace.cost_events
    verify["elapsed_s"] = trace.elapsed_s
    verify["retrieved_skills"] = trace.retrieved_skills
    verify["prompt_injected_skills"] = trace.prompt_injected_skills
    verify["called_skill_functions"] = trace.called_skill_functions
    verify["filtered_skills"] = trace.filtered_skills
    verify["injector_events"] = trace.injector_events
    verify["skill_injector_mode"] = injector_mode
    verify["model_name"] = response.model_name
    verify["llm_api_style"] = response.api_style
    return BenchmarkResult(
        benchmark="spreadsheet",
        task_id=task.task_id,
        success=success,
        score=float(verify["cell_accuracy"]),
        metrics=verify,
        trace=trace.as_dict(),
    )


async def run_spreadsheet_task_notebook(
    task: BenchmarkTask,
    *,
    llm_config: str,
    model_name: Optional[str] = None,
    artifact_store: Optional[ArtifactStore] = None,
    top_k_skills: int = 5,
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
    max_turns: int = 5,
    work_dir: Optional[Path] = None,
) -> BenchmarkResult:
    t0 = time.monotonic()
    trace = SpreadsheetTrace(task_id=task.task_id, execution_mode="notebook")
    query = str(task.question)
    retrieved = artifact_store.retrieve(query, top_k=top_k_skills) if artifact_store else []
    trace.retrieved_skills = [skill.name for skill in retrieved]
    injector_mode = (
        skill_injector_mode
        or os.environ.get("SPREADSHEET_SKILL_INJECTOR_MODE")
        or os.environ.get("SKILL_INJECTOR_MODE")
        or "full"
    ).strip().lower()
    injected = list(retrieved)
    skill_prompt = artifact_store.build_prompt(injected) if artifact_store else "(none)"
    if artifact_store and injector_mode not in {"", "full"}:
        injector = BudgetSkillInjector(
            mode=injector_mode,
            max_full_skills=int(os.environ.get("SPREADSHEET_SKILL_INJECTOR_MAX_FULL_SKILLS", os.environ.get("SKILL_INJECTOR_MAX_FULL_SKILLS", "0")) or "0"),
            max_summary_skills=int(os.environ.get("SPREADSHEET_SKILL_INJECTOR_MAX_SUMMARY_SKILLS", os.environ.get("SKILL_INJECTOR_MAX_SUMMARY_SKILLS", "2")) or "2"),
            budget_chars=int(skill_context_budget_chars or os.environ.get("SPREADSHEET_SKILL_CONTEXT_BUDGET_CHARS", os.environ.get("SKILL_INJECTOR_BUDGET_CHARS", "2200")) or "2200"),
            compact_chars_per_skill=int(os.environ.get("SPREADSHEET_SKILL_COMPACT_CHARS_PER_SKILL", os.environ.get("SKILL_INJECTOR_COMPACT_CHARS_PER_SKILL", "900")) or "900"),
        )
        injection = injector.select(
            retrieved,
            query=query,
            allowed_injection_types={"informational", "workflow", "functional"},
        )
        injected = injection.artifacts
        skill_prompt = injection.prompt()
        trace.filtered_skills = list(injection.filtered)
        trace.injector_events = [injection.as_event()]
        trace.cost_events.append(
            make_cost_event(
                role="injector",
                phase="executor",
                benchmark="spreadsheet",
                task_id=task.task_id,
                model=model_name or "",
                llm_config=llm_config,
                skill_prompt_chars=int(trace.injector_events[0].get("prompt_chars") or 0),
                metadata={
                    "mode": trace.injector_events[0].get("mode"),
                    "injected_count": trace.injector_events[0].get("injected_count"),
                    "filtered_count": trace.injector_events[0].get("filtered_count"),
                    "deterministic": True,
                    "execution_mode": "notebook",
                },
            )
        )

    base_work_dir = work_dir or Path(tempfile.mkdtemp(prefix="spreadsheetbench_nb_"))
    base_work_dir.mkdir(parents=True, exist_ok=True)
    callable_prompt = write_spreadsheet_skill_library(injected, base_work_dir)
    trace.callable_skills = callable_prompt["skills"]
    skill_prompt_for_system = (
        skill_prompt
        + ("\n\nCallable skill import map:\n" + callable_prompt["prompt"] if callable_prompt["prompt"] else "")
    )
    trace.prompt_injected_skills = [skill.name for skill in injected]
    system = SPREADSHEET_NOTEBOOK_SYSTEM.format(
        skills=skill_prompt,
        callable_skills=callable_prompt["prompt"] or "(no callable function skills available)",
        done_pattern=SPREADSHEET_DONE_PATTERN,
    )
    input_copy = base_work_dir / f"{task.task_id}_input.xlsx"
    output_path = base_work_dir / f"{task.task_id}_output.xlsx"
    shutil.copyfile(task.input_artifacts["input_xlsx"], input_copy)
    shutil.copyfile(input_copy, output_path)

    base_prompt = build_spreadsheet_notebook_prompt(task, input_copy, output_path, max_turns=max_turns)
    trace.prompt = base_prompt
    session: NotebookPythonSession | None = None
    stopped_by_done = False
    try:
        session = NotebookPythonSession(base_work_dir)
        session.run_cell(
            f"INPUT_XLSX = Path({str(input_copy)!r})\nOUTPUT_XLSX = Path({str(output_path)!r})",
            timeout=CODE_EXEC_TIMEOUT,
        )
        history: List[Dict[str, Any]] = []
        response_model = model_name
        response_api_style = ""
        for turn_index in range(max(1, int(max_turns or 5))):
            prompt = build_spreadsheet_notebook_turn_prompt(
                base_prompt=base_prompt,
                history=history,
                turn_index=turn_index,
                max_turns=max_turns,
            )
            response = await ask_text_llm(
                llm_config=llm_config,
                model_name=model_name,
                system=system,
                prompt=prompt,
            )
            response_model = response.model_name or model_name
            response_api_style = response.api_style
            trace.input_tokens += response.prompt_tokens
            trace.cache_input_tokens += response.cache_input_tokens
            trace.completion_tokens += response.completion_tokens
            trace.total_tokens += response.prompt_tokens + response.cache_input_tokens + response.completion_tokens
            code = extract_code(response.content)
            done_requested = SPREADSHEET_DONE_PATTERN in (response.content or "")
            turn: Dict[str, Any] = {
                "turn_index": turn_index,
                "response": (response.content or "")[-4000:],
                "code": code,
                "done_requested": done_requested,
                "prompt_tokens": response.prompt_tokens,
                "cache_input_tokens": response.cache_input_tokens,
                "completion_tokens": response.completion_tokens,
            }
            if code:
                called_this_turn = called_spreadsheet_skill_functions(
                    code,
                    callable_prompt["skills"],
                )
                exec_result = session.run_cell(code, timeout=CODE_EXEC_TIMEOUT)
                turn.update(exec_result)
                trace.code = (trace.code + "\n\n# %% notebook turn " + str(turn_index) + "\n" + code).strip()
                history.append(
                    {
                        "turn_index": turn_index,
                        "code": code,
                        "stdout": exec_result.get("stdout", ""),
                        "stderr": exec_result.get("stderr", ""),
                        "returncode": exec_result.get("returncode"),
                        "called_skill_functions": called_this_turn,
                    }
                )
                trace.called_skill_functions = list(
                    dict.fromkeys([*trace.called_skill_functions, *called_this_turn])
                )
            else:
                turn.update({"stdout": "", "stderr": "", "returncode": None})
                history.append(
                    {
                        "turn_index": turn_index,
                        "code": "",
                        "stdout": "",
                        "stderr": "No fenced python code was returned.",
                        "returncode": None,
                    }
                )
            trace.notebook_turns.append(turn)
            trace.cost_events.append(
                make_cost_event(
                    role="executor",
                    phase="task_rollout",
                    benchmark="spreadsheet",
                    task_id=task.task_id,
                    model=response.model_name or model_name or "",
                    llm_config=llm_config,
                    input_tokens=response.prompt_tokens,
                    cache_input_tokens=response.cache_input_tokens,
                    output_tokens=response.completion_tokens,
                    prompt_chars=len(prompt),
                    skill_prompt_chars=len(skill_prompt_for_system),
                    system_prompt_chars=len(system),
                    final_conversation_chars=len(system) + len(prompt) + len(response.content or ""),
                    metadata={
                        "api_style": response.api_style,
                        "skill_injector_mode": injector_mode,
                        "prompt_injected_skills": list(trace.prompt_injected_skills),
                        "callable_skills": list(trace.callable_skills),
                        "execution_mode": "notebook",
                        "turn_index": turn_index,
                    },
                )
            )
            if done_requested:
                stopped_by_done = True
                break
        trace.stdout = "\n".join(str(turn.get("stdout") or "") for turn in trace.notebook_turns)[-4000:]
        trace.stderr = "\n".join(str(turn.get("stderr") or "") for turn in trace.notebook_turns)[-4000:]
        verify = verify_spreadsheet_output(
            predicted_xlsx=output_path,
            golden_xlsx=Path(task.expected["golden_xlsx"]),
            sheet_name=task.expected.get("answer_sheet"),
            answer_range=task.expected.get("answer_position"),
        )
        last_returncode = trace.notebook_turns[-1].get("returncode") if trace.notebook_turns else None
        verify["returncode"] = last_returncode
        verify["execution_ok"] = not any((turn.get("returncode") not in {0, None}) for turn in trace.notebook_turns)
        success = bool(verify["pass"])
    except Exception as exc:
        trace.elapsed_s = round(time.monotonic() - t0, 3)
        return BenchmarkResult(
            benchmark="spreadsheet",
            task_id=task.task_id,
            success=False,
            score=0.0,
            metrics={"exception": type(exc).__name__, "execution_mode": "notebook"},
            trace=trace.as_dict(),
            error=str(exc),
        )
    finally:
        if session is not None:
            session.close()

    trace.elapsed_s = round(time.monotonic() - t0, 3)
    verify["total_tokens"] = trace.total_tokens
    verify["input_tokens"] = trace.input_tokens
    verify["cache_input_tokens"] = trace.cache_input_tokens
    verify["completion_tokens"] = trace.completion_tokens
    verify["cost_events"] = trace.cost_events
    verify["elapsed_s"] = trace.elapsed_s
    verify["retrieved_skills"] = trace.retrieved_skills
    verify["prompt_injected_skills"] = trace.prompt_injected_skills
    verify["called_skill_functions"] = trace.called_skill_functions
    verify["filtered_skills"] = trace.filtered_skills
    verify["injector_events"] = trace.injector_events
    verify["skill_injector_mode"] = injector_mode
    verify["execution_mode"] = "notebook"
    verify["notebook_turn_count"] = len(trace.notebook_turns)
    verify["notebook_stopped_by_done"] = stopped_by_done
    verify["model_name"] = response_model
    verify["llm_api_style"] = response_api_style
    return BenchmarkResult(
        benchmark="spreadsheet",
        task_id=task.task_id,
        success=success,
        score=float(verify["cell_accuracy"]),
        metrics=verify,
        trace=trace.as_dict(),
    )


def build_spreadsheet_prompt(task: BenchmarkTask, input_copy: Path, output_path: Path) -> str:
    preview = workbook_preview(input_copy)
    return (
        f"Instruction:\n{task.question}\n\n"
        f"Input workbook path: {input_copy}\n"
        f"Output workbook path: {output_path}\n"
        f"Answer sheet: {task.expected.get('answer_sheet')}\n"
        f"Answer range: {task.expected.get('answer_position')}\n"
        f"Data position: {task.metadata.get('data_position')}\n\n"
        f"Workbook preview:\n{preview}\n\n"
        "Write robust openpyxl code. It may inspect workbook sheets/cells, then save to OUTPUT_XLSX."
    )


def build_spreadsheet_notebook_prompt(
    task: BenchmarkTask,
    input_copy: Path,
    output_path: Path,
    *,
    max_turns: int,
) -> str:
    preview = workbook_preview(input_copy)
    return (
        f"Instruction:\n{task.question}\n\n"
        f"Input workbook path: {input_copy}\n"
        f"Output workbook path: {output_path}\n"
        f"Answer sheet: {task.expected.get('answer_sheet')}\n"
        f"Answer range: {task.expected.get('answer_position')}\n"
        f"Data position: {task.metadata.get('data_position')}\n\n"
        f"Workbook preview:\n{preview}\n\n"
        "Notebook protocol:\n"
        f"- You have at most {max_turns} turns.\n"
        "- Each fenced python block executes in the same process and can reuse previous variables.\n"
        "- Use print(...) to inspect workbook state when needed.\n"
        "- If execution fails, use the stderr in the next turn to repair the code.\n"
        f"- Save the final answer workbook to OUTPUT_XLSX, then output {SPREADSHEET_DONE_PATTERN}.\n"
    )


def build_spreadsheet_notebook_turn_prompt(
    *,
    base_prompt: str,
    history: Sequence[Dict[str, Any]],
    turn_index: int,
    max_turns: int,
) -> str:
    if not history:
        return (
            base_prompt
            + "\nThis is turn 1. Return a fenced python code block to inspect or solve the workbook. "
            + f"When the workbook is saved and final, include {SPREADSHEET_DONE_PATTERN}."
        )
    chunks = [base_prompt, "\nPrevious notebook executions:"]
    for row in history[-4:]:
        chunks.append(
            "\n".join(
                [
                    f"Turn {int(row.get('turn_index', 0)) + 1}:",
                    "Code:",
                    "```python",
                    clip_notebook_text(row.get("code") or "", 1800),
                    "```",
                    f"Return code: {row.get('returncode')}",
                    f"stdout:\n{clip_notebook_text(row.get('stdout') or '', 1200)}",
                    f"stderr:\n{clip_notebook_text(row.get('stderr') or '', 1600)}",
                ]
            )
        )
    chunks.append(
        f"\nThis is turn {turn_index + 1} of {max_turns}. "
        "Return the next fenced python code block. "
        f"If the workbook is already correct and saved, include {SPREADSHEET_DONE_PATTERN}."
    )
    return "\n\n".join(chunks)


def clip_notebook_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


def workbook_preview(path: Path, max_rows: int = 8, max_cols: int = 8) -> str:
    wb = openpyxl.load_workbook(path, data_only=False)
    parts = []
    for sheet in wb.sheetnames[:5]:
        ws = wb[sheet]
        rows = []
        for row in ws.iter_rows(
            min_row=1,
            max_row=min(ws.max_row, max_rows),
            min_col=1,
            max_col=min(ws.max_column, max_cols),
            values_only=True,
        ):
            rows.append([jsonable_cell_value(value) for value in row])
        parts.append(f"## {sheet} ({ws.max_row}x{ws.max_column})\n{json.dumps(rows, ensure_ascii=False)}")
    return "\n\n".join(parts)
