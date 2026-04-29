from pathlib import Path

from academic.benchmarks.bfcl import BFCLToolCall, load_bfcl_tasks, load_bfcl_tools, score_bfcl_calls
from academic.benchmarks.registry import BENCHMARK_REGISTRY
from academic.benchmarks.spreadsheet import load_spreadsheet_tasks, verify_spreadsheet_output


def test_registry_has_selected_benchmarks() -> None:
    assert {"bfcl_v3", "appworld", "officeqa", "spreadsheet", "tir_bench"} <= set(BENCHMARK_REGISTRY)


def test_bfcl_loader_and_scorer_contract() -> None:
    train, test = load_bfcl_tasks(cache_dir=Path("data/benchmarks/bfcl_v3"), n_train=2, n_test=2)
    tools = load_bfcl_tools(Path("data/benchmarks/bfcl_v3"))
    assert len(train) == 2
    assert len(test) == 2
    assert any(t["function"]["name"] == "cd" for t in tools)

    from academic.benchmarks.bfcl import _parse_call

    task = train[0]
    calls = []
    for turn_index, turn in enumerate(task.expected):
        for raw in turn:
            name, args = _parse_call(raw)
            calls.append(BFCLToolCall(name=name, arguments=args, turn_index=turn_index))
    score = score_bfcl_calls(calls, task.expected)
    assert score["task_success"] is True
    assert score["call_f1"] == 1.0


def test_spreadsheet_loader_and_verifier_contract() -> None:
    train, test = load_spreadsheet_tasks(cache_dir=Path("data/benchmarks/spreadsheet"), n_train=2, n_test=1)
    assert len(train) == 2
    assert len(test) == 1
    task = train[0]
    result = verify_spreadsheet_output(
        predicted_xlsx=Path(task.expected["golden_xlsx"]),
        golden_xlsx=Path(task.expected["golden_xlsx"]),
        sheet_name=task.expected["answer_sheet"],
        answer_range=task.expected["answer_position"],
    )
    assert result["pass"] is True
    assert result["cell_accuracy"] == 1.0
