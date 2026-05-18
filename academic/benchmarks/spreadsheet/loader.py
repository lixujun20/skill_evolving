"""SpreadsheetBench dataset loading."""
from __future__ import annotations

import json
import random
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import List, Tuple

from academic.benchmarks.core.types import BenchmarkTask
from academic.benchmarks.spreadsheet.prompts import DATASET_URL


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

    shuffled = list(tasks)
    random.Random(split_seed).shuffle(shuffled)
    return shuffled[:n_train], shuffled[n_train : n_train + n_test]
