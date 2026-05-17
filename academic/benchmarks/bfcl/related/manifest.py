"""BFCL related-task manifest construction and validation."""
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

from academic.benchmarks.bfcl import load_bfcl_tasks
from academic.benchmarks.core.types import BenchmarkTask

LOOKUP_VERBS = {"get", "find", "retrieve", "view", "display", "verify", "check", "authenticate"}
ACTION_VERBS = {
    "book", "cancel", "create", "edit", "send", "post", "place", "set", "start",
    "fund", "purchase", "register", "fill", "touch", "cp", "mv", "sort", "grep",
    "mean", "diff", "wc", "delete", "comment", "retweet", "contact", "update",
}
TOOL_TOKEN_RE = re.compile(r"[A-Z]?[a-z]+|\d+")


def tool_verb(function_path: str) -> str:
    fn = str(function_path or "").split(".")[-1]
    toks = [piece.lower() for piece in TOOL_TOKEN_RE.findall(fn)]
    return toks[0] if toks else fn.lower()


def tool_family(function_path: str) -> str:
    parts = str(function_path or "").split(".")
    return str(parts[0] if parts else "").strip()


def score_failure_family(task: BenchmarkTask) -> str:
    verbs = [tool_verb(path) for path in (task.metadata.get("path") or [])]
    lookup_count = sum(1 for verb in verbs if verb in LOOKUP_VERBS)
    action_count = sum(1 for verb in verbs if verb in ACTION_VERBS)
    if lookup_count >= 3 and action_count >= 2:
        return "identifier_lookup_then_action"
    if verbs.count("cancel") >= 1 and any(verb in {"book", "purchase", "register"} for verb in verbs):
        return "ordering_or_stateful_transaction"
    if any(verb in {"set", "start", "fill"} for verb in verbs) and any(
        verb in {"check", "estimate", "find", "get"} for verb in verbs
    ):
        return "precondition_then_action"
    if any(verb in {"cp", "mv", "diff", "sort", "grep", "wc"} for verb in verbs):
        return "filesystem_argument_binding"
    if any(verb in {"place", "fund", "cancel"} for verb in verbs):
        return "trading_identifier_or_order_binding"
    return "multi_turn_argument_binding"


def task_domain(task: BenchmarkTask) -> str:
    classes = list(task.metadata.get("involved_classes") or [])
    if not classes:
        return "unknown"
    if len(classes) == 1:
        return classes[0]
    return "+".join(sorted(classes))


def task_relatedness_score(task: BenchmarkTask, combo_counts: Counter[Tuple[str, ...]]) -> float:
    paths = list(task.metadata.get("path") or [])
    combo = tuple(sorted(task.metadata.get("involved_classes") or []))
    verbs = [tool_verb(path) for path in paths]
    lookup_count = sum(1 for verb in verbs if verb in LOOKUP_VERBS)
    action_count = sum(1 for verb in verbs if verb in ACTION_VERBS)
    repeated_lookup_action = min(lookup_count, action_count)
    score = (
        combo_counts[combo] * 10.0
        + len(paths) * 2.0
        + lookup_count * 1.8
        + action_count * 1.2
        + repeated_lookup_action * 2.5
    )
    if len(combo) >= 2:
        score += 4.0
    return score


def build_curated_related_task_manifest(
    *,
    cache_dir: Path,
    split_seed: int = 42,
    data_source: str = "bfcl_eval_bundle",
    n_train: int = 50,
    n_test: int = 50,
) -> Dict[str, Any]:
    tasks, _ = load_bfcl_tasks(
        cache_dir=cache_dir,
        split_seed=split_seed,
        n_train=max(200, n_train + n_test),
        n_test=0,
        data_source=data_source,
    )
    combo_counts: Counter[Tuple[str, ...]] = Counter(
        tuple(sorted(task.metadata.get("involved_classes") or []))
        for task in tasks
    )
    rows: List[Dict[str, Any]] = []
    for task in tasks:
        combo = tuple(sorted(task.metadata.get("involved_classes") or []))
        verbs = [tool_verb(path) for path in (task.metadata.get("path") or [])]
        families = sorted({tool_family(path) for path in (task.metadata.get("path") or []) if path})
        rows.append(
            {
                "task_id": task.task_id,
                "domain": task_domain(task),
                "failure_family": score_failure_family(task),
                "tool_families": families,
                "tool_verbs": verbs,
                "why_related": (
                    f"tool_families={families}; repeated lookup/action mix={sum(1 for verb in verbs if verb in LOOKUP_VERBS)}/"
                    f"{sum(1 for verb in verbs if verb in ACTION_VERBS)}; path_len={len(task.metadata.get('path') or [])}"
                ),
                "metadata": copy.deepcopy(task.metadata),
                "score": round(task_relatedness_score(task, combo_counts), 4),
            }
        )
    rows.sort(
        key=lambda item: (
            -float(item["score"]),
            item["domain"],
            item["failure_family"],
            str(item["task_id"]),
        )
    )
    selected = rows[: n_train + n_test]
    selected_ids = {row["task_id"] for row in selected}
    assert len(selected_ids) == len(selected), "curated manifest selected duplicate task ids"
    train_rows = copy.deepcopy(selected[:n_train])
    test_rows = copy.deepcopy(selected[n_train : n_train + n_test])
    return {
        "manifest_version": 1,
        "benchmark": "bfcl_v3",
        "selection_method": {
            "type": "deterministic_relatedness_ranking",
            "description": (
                "Rank BFCL tasks by repeated tool-family / lookup-action / multi-turn workflow signals, "
                "then freeze the requested top related-task pool."
            ),
            "criteria": [
                "shared tool families",
                "repeated identifier/lookup/ordering/argument failure families",
                "multi-turn workflow similarity",
            ],
            "split_seed": split_seed,
            "data_source": data_source,
        },
        "train_task_ids": [row["task_id"] for row in train_rows],
        "test_task_ids": [row["task_id"] for row in test_rows],
        "train_tasks": train_rows,
        "test_tasks": test_rows,
    }


def validate_curated_manifest(
    manifest: Dict[str, Any],
    *,
    expected_train: int | None = 50,
    expected_test: int | None = 50,
    require_task_rows: bool = True,
) -> Dict[str, Any]:
    train_ids = [str(item).strip() for item in manifest.get("train_task_ids", []) if str(item).strip()]
    test_ids = [str(item).strip() for item in manifest.get("test_task_ids", []) if str(item).strip()]
    overlap = sorted(set(train_ids) & set(test_ids))
    rows = list(manifest.get("train_tasks", []) or []) + list(manifest.get("test_tasks", []) or [])
    by_id = {str(row.get("task_id") or ""): row for row in rows}
    missing_rows = [task_id for task_id in train_ids + test_ids if task_id not in by_id]
    size_ok = True
    if expected_train is not None:
        size_ok = size_ok and len(train_ids) == expected_train
    if expected_test is not None:
        size_ok = size_ok and len(test_ids) == expected_test
    return {
        "n_train": len(train_ids),
        "n_test": len(test_ids),
        "train_unique": len(set(train_ids)),
        "test_unique": len(set(test_ids)),
        "overlap_count": len(overlap),
        "overlap_task_ids": overlap,
        "missing_row_count": len(missing_rows),
        "missing_row_task_ids": missing_rows,
        "expected_train": expected_train,
        "expected_test": expected_test,
        "require_task_rows": require_task_rows,
        "ok": size_ok and not overlap and (not require_task_rows or not missing_rows),
    }


def load_or_build_curated_manifest(
    *,
    manifest_path: Path,
    cache_dir: Path,
    split_seed: int = 42,
    data_source: str = "bfcl_eval_bundle",
    expected_train: int | None = 50,
    expected_test: int | None = 50,
    require_task_rows: bool = True,
) -> Dict[str, Any]:
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = build_curated_related_task_manifest(
            cache_dir=cache_dir,
            split_seed=split_seed,
            data_source=data_source,
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    validation = validate_curated_manifest(
        manifest,
        expected_train=expected_train,
        expected_test=expected_test,
        require_task_rows=require_task_rows,
    )
    if not validation["ok"]:
        raise ValueError(f"Invalid curated manifest: {validation}")
    return manifest


def load_all_bfcl_tasks(cache_dir: Path, data_source: str) -> Dict[str, BenchmarkTask]:
    tasks, _ = load_bfcl_tasks(
        cache_dir=cache_dir,
        split_seed=42,
        n_train=200,
        n_test=0,
        data_source=data_source,
    )
    return {task.task_id: task for task in tasks}


def tasks_from_manifest(
    manifest: Dict[str, Any],
    *,
    cache_dir: Path,
    data_source: str,
) -> Tuple[List[BenchmarkTask], List[BenchmarkTask]]:
    task_map = load_all_bfcl_tasks(cache_dir, data_source)
    train = [task_map[task_id] for task_id in manifest["train_task_ids"]]
    test = [task_map[task_id] for task_id in manifest["test_task_ids"]]
    return train, test


__all__ = [
    "ACTION_VERBS",
    "LOOKUP_VERBS",
    "TOOL_TOKEN_RE",
    "tool_verb",
    "tool_family",
    "score_failure_family",
    "task_domain",
    "task_relatedness_score",
    "build_curated_related_task_manifest",
    "validate_curated_manifest",
    "load_or_build_curated_manifest",
    "load_all_bfcl_tasks",
    "tasks_from_manifest",
]
