"""BFCL call-level and official checker scoring."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from academic.benchmarks.bfcl.call_utils import (
    call_to_source,
    ensure_bfcl_eval_importable,
    jsonable,
    parse_call,
    parse_expected_turn,
    safe_model_stem,
    task_to_official_entry,
)
from academic.benchmarks.bfcl.models import BFCLToolCall
from academic.benchmarks.core.types import BenchmarkTask


def score_bfcl_calls(calls: List[BFCLToolCall], expected_turns: Any) -> Dict[str, Any]:
    try:
        expected_by_turn = [parse_expected_turn(turn) for turn in expected_turns or []]
        expected_parse_error = None
    except Exception as exc:
        expected_by_turn = []
        expected_parse_error = {
            "type": "invalid_expected",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    actual_by_turn: List[List[Tuple[str, Dict[str, Any]]]] = []
    n_turns = max(len(expected_by_turn), 1 + max((c.turn_index for c in calls), default=-1))
    for turn_index in range(n_turns):
        actual_by_turn.append(
            [(c.name, c.arguments) for c in calls if c.turn_index == turn_index]
        )

    turn_scores = []
    total_expected = 0
    total_actual = 0
    total_matched = 0
    for idx in range(n_turns):
        exp = expected_by_turn[idx] if idx < len(expected_by_turn) else []
        act = actual_by_turn[idx] if idx < len(actual_by_turn) else []
        matched = _greedy_match_calls(act, exp)
        total_expected += len(exp)
        total_actual += len(act)
        total_matched += matched
        precision = matched / len(act) if act else float(not exp)
        recall = matched / len(exp) if exp else float(not act)
        f1 = _f1(precision, recall)
        turn_scores.append(
            {
                "turn_index": idx,
                "expected_calls": len(exp),
                "actual_calls": len(act),
                "matched_calls": matched,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "turn_success": bool(exp == [] and act == [] or (matched == len(exp) == len(act))),
            }
        )

    precision = total_matched / total_actual if total_actual else float(total_expected == 0)
    recall = total_matched / total_expected if total_expected else float(total_actual == 0)
    call_f1 = _f1(precision, recall)
    call_errors = _call_error_analysis(actual_by_turn, expected_by_turn)
    if expected_parse_error:
        call_errors = [expected_parse_error, *call_errors]
    return {
        "task_success": all(t["turn_success"] for t in turn_scores),
        "relaxed_task_success": all(t["matched_calls"] == t["expected_calls"] for t in turn_scores),
        "turn_success_rate": round(
            sum(1 for t in turn_scores if t["turn_success"]) / max(len(turn_scores), 1), 4
        ),
        "relaxed_turn_success_rate": round(
            sum(1 for t in turn_scores if t["matched_calls"] == t["expected_calls"])
            / max(len(turn_scores), 1),
            4,
        ),
        "call_precision": round(precision, 4),
        "call_recall": round(recall, 4),
        "call_f1": round(call_f1, 4),
        "n_expected_calls": total_expected,
        "n_actual_calls": total_actual,
        "n_matched_calls": total_matched,
        "turn_scores": turn_scores,
        "call_errors": call_errors,
    }


def score_bfcl_official(calls: List[BFCLToolCall], task: BenchmarkTask) -> Dict[str, Any]:
    """Run BFCL's official multi-turn state/response checker when importable."""
    try:
        ensure_bfcl_eval_importable()
        from bfcl_eval.eval_checker.multi_turn_eval.multi_turn_checker import (
            multi_turn_checker,
        )
    except Exception as exc:
        return {
            "valid": None,
            "error_type": "official_checker_unavailable",
            "error_message": str(exc),
        }

    decoded: List[List[List[str]]] = []
    n_turns = max(
        len(task.expected or []),
        1 + max((call.turn_index for call in calls), default=-1),
    )
    for turn_index in range(n_turns):
        decoded.append(
            [[call_to_source(call.name, call.arguments)] for call in calls if call.turn_index == turn_index]
        )
    try:
        result = multi_turn_checker(
            decoded,
            task.expected or [],
            task_to_official_entry(task),
            "multi_turn_base",
            safe_model_stem(f"academic_checker_{task.task_id}_{time.time_ns()}"),
        )
        return jsonable(result)
    except Exception as exc:
        return {
            "valid": None,
            "error_type": "official_checker_exception",
            "error_message": f"{type(exc).__name__}: {exc}",
        }


def _greedy_match_calls(
    actual: List[Tuple[str, Dict[str, Any]]],
    expected: List[Tuple[str, Dict[str, Any]]],
) -> int:
    unused = set(range(len(actual)))
    matched = 0
    for exp_name, exp_args in expected:
        best_idx = None
        for idx in unused:
            act_name, act_args = actual[idx]
            if act_name != exp_name:
                continue
            normalized_actual, normalized_expected = _align_argument_views(act_args, exp_args)
            if normalized_actual != normalized_expected:
                continue
            if best_idx is None:
                best_idx = idx
                break
        if best_idx is not None:
            unused.remove(best_idx)
            matched += 1
    return matched


def _call_error_analysis(
    actual_by_turn: List[List[Tuple[str, Dict[str, Any]]]],
    expected_by_turn: List[List[Tuple[str, Dict[str, Any]]]],
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    n_turns = max(len(actual_by_turn), len(expected_by_turn))
    for turn_index in range(n_turns):
        actual = actual_by_turn[turn_index] if turn_index < len(actual_by_turn) else []
        expected = expected_by_turn[turn_index] if turn_index < len(expected_by_turn) else []
        used_actual: set[int] = set()
        for exp_name, exp_args in expected:
            candidates = [
                (idx, act_args)
                for idx, (act_name, act_args) in enumerate(actual)
                if idx not in used_actual and act_name == exp_name
            ]
            if not candidates:
                errors.append(
                    {
                        "turn_index": turn_index,
                        "type": "missing_call",
                        "expected_name": exp_name,
                        "expected_arguments": exp_args,
                    }
                )
                continue
            idx, act_args = max(candidates, key=lambda item: _arg_similarity(item[1], exp_args))
            used_actual.add(idx)
            normalized_actual, normalized_expected = _align_argument_views(act_args, exp_args)
            missing = {
                key: value
                for key, value in normalized_expected.items()
                if key not in normalized_actual
            }
            unexpected = {
                key: value
                for key, value in normalized_actual.items()
                if key not in normalized_expected
            }
            wrong = {
                key: {"expected": normalized_expected[key], "actual": normalized_actual[key]}
                for key in normalized_expected
                if key in normalized_actual and not _value_equal(normalized_actual[key], normalized_expected[key])
            }
            if missing or unexpected or wrong:
                errors.append(
                    {
                        "turn_index": turn_index,
                        "type": "argument_mismatch",
                        "name": exp_name,
                        "missing": missing,
                        "unexpected": unexpected,
                        "wrong": wrong,
                    }
                )
        for idx, (act_name, act_args) in enumerate(actual):
            if idx not in used_actual:
                errors.append(
                    {
                        "turn_index": turn_index,
                        "type": "extra_call",
                        "actual_name": act_name,
                        "actual_arguments": act_args,
                    }
                )
    return errors


def _arg_similarity(actual: Dict[str, Any], expected: Dict[str, Any]) -> float:
    if not expected:
        return 1.0
    actual, expected = _align_argument_views(actual, expected)
    hits = 0
    for key, exp_val in expected.items():
        if key in actual and _value_equal(actual[key], exp_val):
            hits += 1
        elif key.startswith("arg"):
            positional_values = list(actual.values())
            try:
                idx = int(key[3:])
                if idx < len(positional_values) and _value_equal(positional_values[idx], exp_val):
                    hits += 1
            except Exception:
                pass
    return hits / len(expected)


def _align_argument_views(
    actual: Dict[str, Any],
    expected: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Treat single positional and single named args as equivalent for BFCL call-F1."""
    actual_norm = dict(actual or {})
    expected_norm = dict(expected or {})

    expected_positional = [key for key in expected_norm if key.startswith("arg")]
    actual_positional = [key for key in actual_norm if key.startswith("arg")]
    actual_named = [key for key in actual_norm if not key.startswith("arg")]
    expected_named = [key for key in expected_norm if not key.startswith("arg")]

    if len(expected_positional) == 1 and not expected_named and len(actual_named) == 1 and not actual_positional:
        expected_norm = {actual_named[0]: expected_norm[expected_positional[0]]}
    elif len(actual_positional) == 1 and not actual_named and len(expected_named) == 1 and not expected_positional:
        actual_norm = {expected_named[0]: actual_norm[actual_positional[0]]}

    return actual_norm, expected_norm


def _value_equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().lower() == right.strip().lower()
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-6
    return left == right


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


__all__ = [
    "score_bfcl_calls",
    "score_bfcl_official",
]
