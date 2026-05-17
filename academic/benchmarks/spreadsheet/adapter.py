"""SpreadsheetBench-Verified adapter.

The first implementation targets a credible baseline scaffold: the model writes
Python/openpyxl code against a copied workbook, the runner executes it, and the
verifier compares the declared answer range with the golden workbook.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import openpyxl

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask
from academic.config import CODE_EXEC_TIMEOUT

DATASET_URL = (
    "https://huggingface.co/datasets/KAKA22/SpreadsheetBench/resolve/main/"
    "spreadsheetbench_verified_400.tar.gz"
)

SPREADSHEET_SYSTEM = """You are a spreadsheet manipulation agent.

Write Python code using openpyxl to modify the workbook at INPUT_XLSX and save
the final answer workbook to OUTPUT_XLSX. Do not explain instead of editing the
file. Preserve sheets, formats, and unrelated cells when possible.

Retrieved skill package guidance:
{skills}

Return exactly one fenced python code block.
"""


@dataclass
class SpreadsheetTrace:
    task_id: str
    prompt: str = ""
    code: str = ""
    stdout: str = ""
    stderr: str = ""
    elapsed_s: float = 0.0
    retrieved_skills: List[str] = field(default_factory=list)
    total_tokens: int = 0
    completion_tokens: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "code": self.code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "elapsed_s": self.elapsed_s,
            "retrieved_skills": self.retrieved_skills,
            "total_tokens": self.total_tokens,
            "completion_tokens": self.completion_tokens,
        }


def ensure_spreadsheetbench(cache_dir: Path, refresh: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / "spreadsheetbench_verified_400.tar.gz"
    extracted = cache_dir / "spreadsheetbench_verified_400"
    if refresh or not archive.exists():
        with urllib.request.urlopen(DATASET_URL, timeout=180) as response:
            archive.write_bytes(response.read())
    if refresh or not extracted.exists():
        if extracted.exists():
            shutil.rmtree(extracted)
        with tarfile.open(archive) as tf:
            tf.extractall(cache_dir)
    return extracted


def load_spreadsheet_tasks(
    *,
    cache_dir: Path,
    n_train: int = 200,
    n_test: int = 200,
    split_seed: int = 42,
    refresh: bool = False,
) -> Tuple[List[BenchmarkTask], List[BenchmarkTask]]:
    root = ensure_spreadsheetbench(cache_dir, refresh=refresh)
    dataset_path = root / "dataset.json"
    raw = json.loads(dataset_path.read_text())
    tasks: List[BenchmarkTask] = []
    for item in raw:
        folder = root / item["spreadsheet_path"]
        init_files = sorted(folder.glob("*_init.xlsx"))
        golden_files = sorted(folder.glob("*_golden.xlsx"))
        prompt_path = folder / "prompt.txt"
        if not init_files or not golden_files:
            continue
        tasks.append(
            BenchmarkTask(
                benchmark="spreadsheet",
                task_id=str(item["id"]),
                question=item["instruction"],
                expected={
                    "golden_xlsx": str(golden_files[0]),
                    "answer_sheet": item.get("answer_sheet"),
                    "answer_position": item.get("answer_position"),
                },
                input_artifacts={
                    "input_xlsx": str(init_files[0]),
                    "prompt_txt": prompt_path.read_text(errors="replace") if prompt_path.exists() else "",
                },
                metadata={
                    "instruction_type": item.get("instruction_type"),
                    "data_position": item.get("data_position"),
                    "spreadsheet_path": item.get("spreadsheet_path"),
                },
            )
        )

    import random

    shuffled = list(tasks)
    random.Random(split_seed).shuffle(shuffled)
    return shuffled[:n_train], shuffled[n_train : n_train + n_test]


async def run_spreadsheet_task(
    task: BenchmarkTask,
    *,
    llm_config: str,
    artifact_store: Optional[ArtifactStore] = None,
    top_k_skills: int = 5,
    work_dir: Optional[Path] = None,
) -> BenchmarkResult:
    from app.llm import LLM

    t0 = time.monotonic()
    trace = SpreadsheetTrace(task_id=task.task_id)
    query = str(task.question)
    retrieved = artifact_store.retrieve(query, top_k=top_k_skills) if artifact_store else []
    trace.retrieved_skills = [skill.name for skill in retrieved]
    system = SPREADSHEET_SYSTEM.format(
        skills=artifact_store.build_prompt(retrieved) if artifact_store else "(none)"
    )
    base_work_dir = work_dir or Path(tempfile.mkdtemp(prefix="spreadsheetbench_"))
    base_work_dir.mkdir(parents=True, exist_ok=True)
    input_copy = base_work_dir / f"{task.task_id}_input.xlsx"
    output_path = base_work_dir / f"{task.task_id}_output.xlsx"
    shutil.copyfile(task.input_artifacts["input_xlsx"], input_copy)
    shutil.copyfile(input_copy, output_path)

    prompt = _build_spreadsheet_prompt(task, input_copy, output_path)
    trace.prompt = prompt
    llm = LLM(config_name=llm_config)
    tokens_before = llm.total_input_tokens + llm.total_completion_tokens
    completion_before = llm.total_completion_tokens
    try:
        response = await llm.ask(
            messages=[{"role": "user", "content": prompt}],
            system_msgs=[{"role": "system", "content": system}],
            stream=False,
        )
        trace.code = _extract_code(response)
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
        stdout, stderr, returncode = await asyncio.to_thread(
            _run_code, trace.code, input_copy, output_path, base_work_dir
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
        trace.total_tokens = (llm.total_input_tokens + llm.total_completion_tokens) - tokens_before
        trace.completion_tokens = llm.total_completion_tokens - completion_before
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
    trace.total_tokens = (llm.total_input_tokens + llm.total_completion_tokens) - tokens_before
    trace.completion_tokens = llm.total_completion_tokens - completion_before
    verify["total_tokens"] = trace.total_tokens
    verify["completion_tokens"] = trace.completion_tokens
    verify["elapsed_s"] = trace.elapsed_s
    verify["retrieved_skills"] = trace.retrieved_skills
    return BenchmarkResult(
        benchmark="spreadsheet",
        task_id=task.task_id,
        success=success,
        score=float(verify["cell_accuracy"]),
        metrics=verify,
        trace=trace.as_dict(),
    )


def verify_spreadsheet_output(
    *,
    predicted_xlsx: Path,
    golden_xlsx: Path,
    sheet_name: Optional[str],
    answer_range: Optional[str],
) -> Dict[str, Any]:
    pred_wb = openpyxl.load_workbook(predicted_xlsx, data_only=False)
    gold_wb = openpyxl.load_workbook(golden_xlsx, data_only=False)
    range_sheet, cell_range = _split_sheet_range(answer_range)
    requested_sheet = range_sheet or sheet_name
    sheet = requested_sheet if requested_sheet in gold_wb.sheetnames else gold_wb.sheetnames[0]
    if sheet not in pred_wb.sheetnames:
        return {
            "pass": False,
            "cell_accuracy": 0.0,
            "checked_cells": 0,
            "mismatched_cells": [{"cell": "__sheet__", "predicted": None, "expected": sheet}],
        }
    pred_ws = pred_wb[sheet]
    gold_ws = gold_wb[sheet]
    cells = _cells_in_range(cell_range, gold_ws)
    mismatches = []
    for cell in cells:
        pv = _normalize_cell_value(pred_ws[cell].value)
        gv = _normalize_cell_value(gold_ws[cell].value)
        if pv != gv:
            mismatches.append({"cell": cell, "predicted": pv, "expected": gv})
    checked = len(cells)
    correct = checked - len(mismatches)
    return {
        "pass": len(mismatches) == 0 and checked > 0,
        "cell_accuracy": round(correct / max(checked, 1), 4),
        "checked_cells": checked,
        "mismatched_cells": mismatches[:20],
        "answer_sheet": sheet,
        "answer_position": answer_range,
    }


def _build_spreadsheet_prompt(task: BenchmarkTask, input_copy: Path, output_path: Path) -> str:
    preview = _workbook_preview(input_copy)
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


def _workbook_preview(path: Path, max_rows: int = 8, max_cols: int = 8) -> str:
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
            rows.append([_jsonable_cell_value(value) for value in row])
        parts.append(f"## {sheet} ({ws.max_row}x{ws.max_column})\n{json.dumps(rows, ensure_ascii=False)}")
    return "\n\n".join(parts)


def _extract_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", text or "", re.S | re.I)
    return match.group(1).strip() if match else ""


def _run_code(code: str, input_xlsx: Path, output_xlsx: Path, work_dir: Path) -> Tuple[str, str, int]:
    script = work_dir / "run_spreadsheet_solution.py"
    script.write_text(
        "from pathlib import Path\n"
        f"INPUT_XLSX = Path({str(input_xlsx)!r})\n"
        f"OUTPUT_XLSX = Path({str(output_xlsx)!r})\n\n"
        + code
        + "\n"
    )
    proc = subprocess.run(
        ["python", str(script)],
        cwd=str(work_dir),
        text=True,
        capture_output=True,
        timeout=CODE_EXEC_TIMEOUT,
    )
    return proc.stdout[-4000:], proc.stderr[-4000:], proc.returncode


def _cells_in_range(answer_range: Optional[str], ws: Any) -> List[str]:
    if not answer_range:
        return [
            cell.coordinate
            for row in ws.iter_rows()
            for cell in row
            if cell.value is not None
        ]
    cells = []
    for row in ws[answer_range]:
        if isinstance(row, tuple):
            cells.extend(cell.coordinate for cell in row)
        else:
            cells.append(row.coordinate)
    return cells


def _split_sheet_range(answer_range: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not answer_range:
        return None, answer_range
    text = str(answer_range).strip()
    if "!" not in text:
        return None, text
    sheet, cell_range = text.rsplit("!", 1)
    sheet = sheet.strip().strip("'").strip('"')
    return sheet or None, cell_range.strip()


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
