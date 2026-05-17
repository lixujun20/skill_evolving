"""BFCL task/tool loading and schema helpers."""
from __future__ import annotations

import copy
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from academic.benchmarks.bfcl.call_utils import MATH_FUNCS, expected_tool_names
from academic.benchmarks.bfcl.constants import (
    ANSWER_URL,
    BFCL_BUNDLE_ANSWER,
    BFCL_BUNDLE_DATASET,
    BFCL_BUNDLE_FUNC_DOC_DIR,
    BFCL_CLASS_FILE_BY_DOC,
    BFCL_OFFICIAL_UNPACK,
    CLASS_DOC_FILES,
    DATASET_URL,
    FUNC_DOC_BASE,
    FUNC_DOC_FILES,
    USE_SKILL_TOOL,
)
from academic.benchmarks.core.types import BenchmarkTask


def load_bfcl_tasks(
    *,
    cache_dir: Path,
    split_seed: int = 42,
    n_train: int = 50,
    n_test: int = 150,
    refresh: bool = False,
    data_source: str = "hf_v3",
) -> Tuple[List[BenchmarkTask], List[BenchmarkTask]]:
    """Load BFCL-v3 multi-turn base and return deterministic train/test split."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    if data_source == "bfcl_eval_bundle":
        if not BFCL_BUNDLE_DATASET.exists() or not BFCL_BUNDLE_ANSWER.exists():
            raise FileNotFoundError(
                "bfcl_eval_bundle data source requires an unpacked bfcl-eval wheel at "
                f"{BFCL_OFFICIAL_UNPACK}"
            )
        dataset_path = BFCL_BUNDLE_DATASET
        answer_path = BFCL_BUNDLE_ANSWER
        data_version = "BFCL_v4_bundle"
    elif data_source == "hf_v3":
        dataset_path = cache_dir / "BFCL_v3_multi_turn_base.jsonl"
        answer_path = cache_dir / "BFCL_v3_multi_turn_base_answers.jsonl"
        if refresh or not dataset_path.exists():
            _download(DATASET_URL, dataset_path)
        if refresh or not answer_path.exists():
            _download(ANSWER_URL, answer_path)
        data_version = "BFCL_v3_hf"
    else:
        raise ValueError(f"Unknown BFCL data_source: {data_source}")

    answers = {
        item["id"]: item.get("ground_truth", [])
        for item in _read_jsonl(answer_path)
    }
    tasks: List[BenchmarkTask] = []
    for item in _read_jsonl(dataset_path):
        task_id = item["id"]
        tasks.append(
            BenchmarkTask(
                benchmark="bfcl_v3",
                task_id=task_id,
                question=item["question"],
                expected=answers.get(task_id, []),
                input_artifacts={"initial_config": item.get("initial_config", {})},
                metadata={
                    "path": item.get("path", []),
                    "involved_classes": item.get("involved_classes", []),
                    "bfcl_data_source": data_source,
                    "bfcl_data_version": data_version,
                },
            )
        )

    import random

    shuffled = list(tasks)
    random.Random(split_seed).shuffle(shuffled)
    train = shuffled[:n_train]
    test = shuffled[n_train : n_train + n_test]
    return train, test


def load_bfcl_tools(
    cache_dir: Path,
    refresh: bool = False,
    data_source: str = "hf_v3",
) -> List[Dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    tools: List[Dict[str, Any]] = []
    for filename in FUNC_DOC_FILES:
        if data_source == "bfcl_eval_bundle":
            path = BFCL_BUNDLE_FUNC_DOC_DIR / filename
            if not path.exists():
                raise FileNotFoundError(f"Missing bundled BFCL function doc: {path}")
        elif data_source == "hf_v3":
            path = cache_dir / "multi_turn_func_doc" / filename
            if refresh or not path.exists():
                _download(f"{FUNC_DOC_BASE}/{filename}", path)
        else:
            raise ValueError(f"Unknown BFCL data_source: {data_source}")
        for item in _read_jsonl(path):
            tools.append(_to_openai_tool(item, source_file=filename))
    return tools


def filter_bfcl_tools_by_class(
    tools: List[Dict[str, Any]],
    task: BenchmarkTask,
) -> List[Dict[str, Any]]:
    """Match BFCL official multi-turn FC: expose all tools for involved classes."""
    class_files = {
        file
        for cls in task.metadata.get("involved_classes", [])
        for file in CLASS_DOC_FILES.get(cls, [])
    }
    if not class_files:
        return tools
    return [
        tool for tool in tools
        if tool.get("function", {}).get("x_bfcl_source_file") in class_files
    ]


def filter_bfcl_tools_for_task(
    tools: List[Dict[str, Any]],
    task: BenchmarkTask,
    *,
    include_expected_tools: bool = False,
) -> List[Dict[str, Any]]:
    """Reduce tool prompt to the classes/functions actually present in a task."""
    allowed_funcs = {
        str(path).split(".")[-1]
        for path in task.metadata.get("path", [])
        if path
    }
    if include_expected_tools:
        allowed_funcs.update(expected_tool_names(task))
    if not allowed_funcs:
        class_files = {
            file
            for cls in task.metadata.get("involved_classes", [])
            for file in CLASS_DOC_FILES.get(cls, [])
        }
        if class_files:
            return [
                tool for tool in tools
                if _tool_source_file(tool["function"]["name"]) in class_files
            ]
        return tools
    return [
        tool for tool in tools
        if tool.get("function", {}).get("name") in allowed_funcs
    ]


def make_bfcl_tools_for_task(
    tools: List[Dict[str, Any]],
    task: BenchmarkTask,
    *,
    adapter_mode: str = "official",
    enable_skill_tool: bool = True,
) -> List[Dict[str, Any]]:
    selected = _tool_selection_policy(adapter_mode).select(tools=tools, task=task)
    selected = [strip_tool_metadata(tool) for tool in selected]
    if enable_skill_tool:
        return [USE_SKILL_TOOL] + selected
    return selected


class _ToolSelectionPolicy:
    def select(self, *, tools: List[Dict[str, Any]], task: BenchmarkTask) -> List[Dict[str, Any]]:
        raise NotImplementedError


class _OfficialClassToolSelectionPolicy(_ToolSelectionPolicy):
    def select(self, *, tools: List[Dict[str, Any]], task: BenchmarkTask) -> List[Dict[str, Any]]:
        return filter_bfcl_tools_by_class(tools, task)


class _PathFilteredToolSelectionPolicy(_ToolSelectionPolicy):
    def select(self, *, tools: List[Dict[str, Any]], task: BenchmarkTask) -> List[Dict[str, Any]]:
        return filter_bfcl_tools_for_task(tools, task, include_expected_tools=False)


class _DebugHintsToolSelectionPolicy(_ToolSelectionPolicy):
    def select(self, *, tools: List[Dict[str, Any]], task: BenchmarkTask) -> List[Dict[str, Any]]:
        return filter_bfcl_tools_for_task(tools, task, include_expected_tools=True)


class _FullToolSelectionPolicy(_ToolSelectionPolicy):
    def select(self, *, tools: List[Dict[str, Any]], task: BenchmarkTask) -> List[Dict[str, Any]]:
        return list(tools)


def _tool_selection_policy(adapter_mode: str) -> _ToolSelectionPolicy:
    policies: Dict[str, _ToolSelectionPolicy] = {
        "official": _OfficialClassToolSelectionPolicy(),
        "path_filtered": _PathFilteredToolSelectionPolicy(),
        "debug_hints": _DebugHintsToolSelectionPolicy(),
        "full_tools": _FullToolSelectionPolicy(),
    }
    try:
        return policies[adapter_mode]
    except KeyError as exc:
        raise ValueError(f"Unknown BFCL adapter_mode: {adapter_mode}") from exc


def strip_tool_metadata(tool: Dict[str, Any]) -> Dict[str, Any]:
    clean = copy.deepcopy(tool)
    function = clean.get("function", {})
    for key in list(function):
        if key.startswith("x_bfcl_"):
            del function[key]
    return clean


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        path.write_bytes(response.read())


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    for line in path.read_text().splitlines():
        if line.strip():
            yield json.loads(line)


def _to_openai_tool(doc: Dict[str, Any], *, source_file: str = "") -> Dict[str, Any]:
    params = _normalize_json_schema(copy.deepcopy(doc.get("parameters", {})))
    description = doc.get("description", "")
    if doc.get("response"):
        description += f" Response schema: {json.dumps(doc['response'], ensure_ascii=False)}"
    return {
        "type": "function",
        "function": {
            "name": doc["name"],
            "description": description,
            "parameters": params or {"type": "object", "properties": {}},
            "x_bfcl_source_file": source_file,
            "x_bfcl_class": BFCL_CLASS_FILE_BY_DOC.get(source_file, ""),
        },
    }


def _tool_source_file(name: str) -> str:
    if name in {
        "cat", "cd", "cp", "diff", "du", "echo", "find", "grep", "ls", "mkdir",
        "mv", "pwd", "rm", "rmdir", "sort", "tail", "touch", "wc",
    }:
        return "gorilla_file_system.json"
    if name in MATH_FUNCS:
        return "math_api.json"
    if name in {
        "add_contact", "delete_message", "get_message_stats", "get_user_id",
        "list_users", "message_get_login_status", "message_login",
        "search_messages", "send_message", "view_messages_sent",
    }:
        return "message_api.json"
    if name in {
        "authenticate_twitter", "comment", "follow_user", "get_tweet",
        "get_tweet_comments", "get_user_stats", "get_user_tweets",
        "list_all_following", "mention", "post_tweet",
        "posting_get_login_status", "retweet", "search_tweets", "unfollow_user",
    }:
        return "posting_api.json"
    if name in {
        "close_ticket", "create_ticket", "edit_ticket", "get_ticket",
        "get_user_tickets", "logout", "resolve_ticket", "ticket_get_login_status",
        "ticket_login",
    }:
        return "ticket_api.json"
    if name.startswith("get_") or name in {
        "add_to_watchlist", "cancel_order", "filter_stocks_by_price",
        "fund_account", "make_transaction", "notify_price_change", "place_order",
        "remove_stock_from_watchlist", "trading_get_login_status", "trading_login",
        "trading_logout", "update_market_status", "update_stock_price",
    }:
        return "trading_bot.json"
    if name in {
        "authenticate_travel", "book_flight", "cancel_booking",
        "compute_exchange_rate", "contact_customer_support", "get_all_credit_cards",
        "get_budget_fiscal_year", "get_credit_card_balance", "get_flight_cost",
        "get_nearest_airport_by_city", "list_all_airports", "purchase_insurance",
        "register_credit_card", "retrieve_invoice", "set_budget_limit",
        "travel_get_login_status", "verify_traveler_information",
    }:
        return "travel_booking.json"
    return "vehicle_control.json"


def _normalize_json_schema(schema: Any) -> Any:
    """Keep BFCL tool docs within common OpenAI-compatible schema subset."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out: Dict[str, Any] = {}
    schema_type = schema.get("type", "object")
    if schema_type == "dict":
        schema_type = "object"
    if schema_type == "float":
        schema_type = "number"
    if schema_type not in {"object", "string", "number", "integer", "boolean", "array"}:
        schema_type = "string"
    out["type"] = schema_type
    if "description" in schema:
        out["description"] = str(schema["description"])
    if schema_type == "object":
        props = schema.get("properties", {})
        out["properties"] = {
            str(name): _normalize_json_schema(value)
            for name, value in props.items()
            if isinstance(value, dict)
        }
        required = schema.get("required", [])
        if isinstance(required, list):
            out["required"] = [str(x) for x in required if str(x) in out["properties"]]
    elif schema_type == "array":
        items = schema.get("items", {"type": "string"})
        out["items"] = _normalize_json_schema(items if isinstance(items, dict) else {"type": "string"})
    return out


__all__ = [
    "BFCL_CLASS_FILE_BY_DOC",
    "CLASS_DOC_FILES",
    "load_bfcl_tasks",
    "load_bfcl_tools",
    "filter_bfcl_tools_by_class",
    "filter_bfcl_tools_for_task",
    "make_bfcl_tools_for_task",
]
