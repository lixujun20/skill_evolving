"""Pure BFCL call parsing, formatting, JSON, and local math helpers."""
from __future__ import annotations

import ast
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from academic.benchmarks.bfcl.constants import BFCL_OFFICIAL_UNPACK
from academic.benchmarks.core.types import BenchmarkTask


def parse_call(call: str) -> Tuple[str, Dict[str, Any]]:
    try:
        tree = ast.parse(call.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid call syntax: {call}") from exc
    if not isinstance(tree.body, ast.Call):
        raise ValueError(f"not a call: {call}")
    func = tree.body.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    args: Dict[str, Any] = {}
    for idx, arg in enumerate(tree.body.args):
        try:
            args[f"arg{idx}"] = ast.literal_eval(arg)
        except Exception as exc:
            raise ValueError(
                f"non-literal positional argument in expected call {call}: arg{idx}"
            ) from exc
    for kw in tree.body.keywords:
        if kw.arg:
            try:
                args[kw.arg] = ast.literal_eval(kw.value)
            except Exception as exc:
                raise ValueError(
                    f"non-literal keyword argument in expected call {call}: {kw.arg}"
                ) from exc
    return name, args


def parse_expected_turn(turn: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
    return [parse_call(call) for call in turn]


def expected_tool_names(task: BenchmarkTask) -> set[str]:
    names: set[str] = set()
    for turn in task.expected or []:
        for raw_call in turn:
            try:
                name, _ = parse_call(raw_call)
                names.add(name)
            except Exception:
                pass
    return names


def expected_tool_names_for_turn(task: BenchmarkTask, turn_index: int) -> List[str]:
    if not task.expected or turn_index >= len(task.expected):
        return []
    names: List[str] = []
    for raw_call in task.expected[turn_index]:
        try:
            name, _ = parse_call(raw_call)
            if name not in names:
                names.append(name)
        except Exception:
            pass
    return names


def call_to_source(name: str, args: Dict[str, Any]) -> str:
    return f"{name}({','.join(f'{key}={repr(value)}' for key, value in args.items())})"


def task_to_official_entry(task: BenchmarkTask) -> Dict[str, Any]:
    import copy

    return {
        "id": task.task_id,
        "question": task.question,
        "initial_config": copy.deepcopy(task.input_artifacts.get("initial_config", {})),
        "path": task.metadata.get("path", []),
        "involved_classes": task.metadata.get("involved_classes", []),
    }


def ensure_bfcl_eval_importable(unpack: Path = BFCL_OFFICIAL_UNPACK) -> None:
    if unpack.exists():
        unpack_text = str(unpack)
        if unpack_text not in sys.path:
            sys.path.insert(0, unpack_text)


def safe_model_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def maybe_json(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))


def json_args(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw_arguments": raw}


def canonical_tool_name(raw_name: str, tools: List[Dict[str, Any]]) -> str:
    """Recover valid tool names from providers that leak tags into names."""
    valid = {tool.get("function", {}).get("name", "") for tool in tools}
    if raw_name in valid:
        return raw_name
    if raw_name.startswith("functions.") and raw_name[len("functions."):] in valid:
        return raw_name[len("functions."):]
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", raw_name or "")
    for ident in reversed(identifiers):
        if ident in valid:
            return ident
    for name in valid:
        if name and str(raw_name).endswith(name):
            return name
    return raw_name


def first_number(args: Dict[str, Any]) -> float:
    for value in args.values():
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def math_result(value: Any, key: str = "result") -> Dict[str, Any]:
    return {key: value}


MATH_FUNCS = {
    "absolute_value": lambda number: math_result(abs(number)),
    "add": lambda a, b: math_result(a + b),
    "subtract": lambda a, b: math_result(a - b),
    "multiply": lambda a, b: math_result(a * b),
    "divide": lambda a, b: math_result(a / b),
    "power": lambda a, b: math_result(a**b),
    "square_root": lambda number: math_result(math.sqrt(number)),
    "logarithm": lambda value, base=math.e, precision=4: math_result(round(math.log(value, base), precision)),
    "mean": lambda numbers: math_result(statistics.mean(numbers)),
    "standard_deviation": lambda numbers: math_result(statistics.pstdev(numbers)),
    "sum_values": lambda numbers: math_result(sum(numbers)),
    "max_value": lambda numbers: math_result(max(numbers)),
    "min_value": lambda numbers: math_result(min(numbers)),
    "round_number": lambda value, precision=0: math_result(round(value, precision)),
    "percentage": lambda value, total: math_result(value / total * 100),
    "imperial_si_conversion": lambda value, unit_in, unit_out: math_result(value),
    "si_unit_conversion": lambda value, unit_in, unit_out: math_result(value),
}
