"""CLI runner for benchmark-specific baselines/evolve experiments.

Examples:
    python -m academic.benchmarks.run --list
    python -m academic.benchmarks.run --benchmark bfcl_v3 --mode baseline --n-test 5
    python -m academic.benchmarks.run --benchmark spreadsheet --mode baseline --n-test 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from academic.benchmarks.artifacts import ArtifactStore
from academic.benchmarks.bfcl import load_bfcl_tasks, load_bfcl_tools, run_bfcl_task
from academic.benchmarks.bfcl_skills import (
    default_bfcl_skill_store,
    extract_bfcl_skills_from_results,
    write_bfcl_handwritten_skills,
)
from academic.benchmarks.registry import BENCHMARK_REGISTRY
from academic.benchmarks.spreadsheet import load_spreadsheet_tasks, run_spreadsheet_task
from academic.config import AGENT_MODEL, ACADEMIC_ROOT, RESULTS_DIR


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Run academic benchmark adapters")
    parser.add_argument("--list", action="store_true", help="List registered benchmarks")
    parser.add_argument("--benchmark", choices=sorted(BENCHMARK_REGISTRY), default="bfcl_v3")
    parser.add_argument("--mode", choices=["baseline", "probe", "evolve", "write-skills"], default="baseline")
    parser.add_argument("--llm-config", default=AGENT_MODEL)
    parser.add_argument("--model-name", default=None, help="Optional per-run model override for the selected llm-config")
    parser.add_argument("--model-names", default="", help="Comma-separated model overrides for sweep mode")
    parser.add_argument("--n-train", type=int, default=None)
    parser.add_argument("--n-test", type=int, default=None)
    parser.add_argument("--n-runs", type=int, default=1)
    parser.add_argument("--n-train-runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tag", default="smoke")
    parser.add_argument("--refresh-data", action="store_true")
    parser.add_argument(
        "--bfcl-data-source",
        choices=["hf_v3", "bfcl_eval_bundle"],
        default="bfcl_eval_bundle",
        help="Use bundled bfcl-eval data/docs by default to match the official backend.",
    )
    parser.add_argument("--skills", type=Path, default=None, help="Optional SkillArtifact JSON file")
    parser.add_argument("--use-handwritten-skills", action="store_true")
    parser.add_argument("--bfcl-explicit-skill-tool", action="store_true")
    parser.add_argument("--save-skills", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=ACADEMIC_ROOT.parent / "data" / "benchmarks")
    parser.add_argument(
        "--bfcl-adapter-mode",
        choices=["official", "path_filtered", "debug_hints", "full_tools"],
        default="official",
        help="official exposes all functions for involved BFCL classes; path_filtered is a token-saving ablation.",
    )
    parser.add_argument("--bfcl-execution-backend", choices=["auto", "official", "local_mock"], default="auto")
    parser.add_argument("--bfcl-prompt-style", choices=["native", "official", "academic"], default="native")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--bfcl-synthetic-continue", action="store_true")
    parser.add_argument(
        "--no-bfcl-tool-name-hints",
        action="store_true",
        help="Deprecated alias kept for old scripts; equivalent to --bfcl-adapter-mode official.",
    )
    args = parser.parse_args()

    if args.list:
        for key, meta in BENCHMARK_REGISTRY.items():
            print(json.dumps({"key": key, **meta.as_dict()}, ensure_ascii=False))
        return

    if args.mode == "write-skills":
        out = args.output or RESULTS_DIR / "bfcl_handwritten_skills.json"
        write_bfcl_handwritten_skills(out)
        print(f"Saved handwritten BFCL skills: {out}")
        return

    store = _load_artifact_store(args)
    t0 = time.monotonic()
    if args.benchmark == "bfcl_v3":
        n_train = args.n_train if args.n_train is not None else 50
        n_test = args.n_test if args.n_test is not None else 20
        train, test = load_bfcl_tasks(
            cache_dir=args.cache_dir / "bfcl_v3",
            split_seed=args.seed,
            n_train=n_train,
            n_test=n_test,
            refresh=args.refresh_data,
            data_source=args.bfcl_data_source,
        )
        tools = load_bfcl_tools(
            args.cache_dir / "bfcl_v3",
            refresh=args.refresh_data,
            data_source=args.bfcl_data_source,
        )
        adapter_mode = "official" if args.no_bfcl_tool_name_hints else args.bfcl_adapter_mode
        if args.mode == "evolve":
            summary = await _run_bfcl_evolve(
                train=train,
                test=test,
                n_train_runs=args.n_train_runs,
                n_test_runs=args.n_runs,
                llm_config=args.llm_config,
                model_name=args.model_name,
                tools=tools,
                seed_store=store,
                adapter_mode=adapter_mode,
                execution_backend=args.bfcl_execution_backend,
                data_source=args.bfcl_data_source,
                prompt_style=args.bfcl_prompt_style,
                temperature=args.temperature,
                synthetic_continue=args.bfcl_synthetic_continue,
                explicit_skill_tool=args.bfcl_explicit_skill_tool,
                tag=args.tag,
                save_skills=args.save_skills,
            )
            summary["elapsed_s"] = round(time.monotonic() - t0, 3)
            out = args.output or RESULTS_DIR / f"{args.benchmark}_{args.tag}_{args.mode}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            print(json.dumps({k: v for k, v in summary.items() if k not in ("train_details", "test_details")}, ensure_ascii=False, indent=2))
            print(f"Saved detail: {out}")
            return
        model_names = _model_names(args)
        if len(model_names) > 1:
            summary = await _run_bfcl_model_sweep(
                test=test,
                n_runs=args.n_runs,
                llm_config=args.llm_config,
                model_names=model_names,
                tools=tools,
                store=store,
                adapter_mode=adapter_mode,
                execution_backend=args.bfcl_execution_backend,
                data_source=args.bfcl_data_source,
                prompt_style=args.bfcl_prompt_style,
                temperature=args.temperature,
                synthetic_continue=args.bfcl_synthetic_continue,
                explicit_skill_tool=args.bfcl_explicit_skill_tool,
                tag=args.tag,
            )
            summary["elapsed_s"] = round(time.monotonic() - t0, 3)
            out = args.output or RESULTS_DIR / f"{args.benchmark}_{args.tag}_{args.mode}_sweep.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            print(json.dumps({k: v for k, v in summary.items() if k != "model_reports"}, ensure_ascii=False, indent=2))
            print(f"Saved detail: {out}")
            return
        details = await _run_bfcl_baseline(
            test,
            args.n_runs,
            args.llm_config,
            tools,
            store,
            adapter_mode=adapter_mode,
            model_name=args.model_name,
            execution_backend=args.bfcl_execution_backend,
            prompt_style=args.bfcl_prompt_style,
            temperature=args.temperature,
            synthetic_continue=args.bfcl_synthetic_continue,
            explicit_skill_tool=args.bfcl_explicit_skill_tool,
        )
    elif args.benchmark == "spreadsheet":
        n_train = args.n_train if args.n_train is not None else 200
        n_test = args.n_test if args.n_test is not None else 5
        train, test = load_spreadsheet_tasks(
            cache_dir=args.cache_dir / "spreadsheet",
            split_seed=args.seed,
            n_train=n_train,
            n_test=n_test,
            refresh=args.refresh_data,
        )
        details = await _run_spreadsheet_baseline(test, args.n_runs, args.llm_config, store)
    else:
        raise ValueError(f"Benchmark {args.benchmark} is registry-only for now")

    summary = _aggregate(args.benchmark, args.mode, args.tag, args.llm_config, len(train), details)
    summary["model_name"] = args.model_name
    if args.benchmark == "bfcl_v3":
        summary["adapter_mode"] = "official" if args.no_bfcl_tool_name_hints else args.bfcl_adapter_mode
        summary["execution_backend"] = args.bfcl_execution_backend
        summary["bfcl_data_source"] = args.bfcl_data_source
        summary["bfcl_prompt_style"] = args.bfcl_prompt_style
        summary["temperature"] = args.temperature
        summary["bfcl_synthetic_continue"] = args.bfcl_synthetic_continue
        summary["bfcl_explicit_skill_tool"] = args.bfcl_explicit_skill_tool
    summary["elapsed_s"] = round(time.monotonic() - t0, 3)
    out = args.output or RESULTS_DIR / f"{args.benchmark}_{args.tag}_{args.mode}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "details"}, ensure_ascii=False, indent=2))
    print(f"Saved detail: {out}")


async def _run_bfcl_baseline(
    tasks: List[Any],
    n_runs: int,
    llm_config: str,
    tools: List[Dict[str, Any]],
    store: ArtifactStore,
    adapter_mode: str,
    model_name: str | None = None,
    execution_backend: str = "auto",
    prompt_style: str = "native",
    temperature: float | None = None,
    synthetic_continue: bool = False,
    explicit_skill_tool: bool = False,
) -> List[Dict[str, Any]]:
    details = []
    for task in tasks:
        runs = []
        for run_idx in range(n_runs):
            result = await run_bfcl_task(
                task,
                llm_config=llm_config,
                tools=tools,
                artifact_store=store,
                adapter_mode=adapter_mode,
                model_name=model_name,
                enable_skill_tool=explicit_skill_tool and bool(store.all()),
                execution_backend=execution_backend,
                prompt_style=prompt_style,
                temperature=temperature,
                synthetic_continue=synthetic_continue,
            )
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
        details.append(_task_runs(task.task_id, runs))
    return details


async def _run_spreadsheet_baseline(
    tasks: List[Any],
    n_runs: int,
    llm_config: str,
    store: ArtifactStore,
) -> List[Dict[str, Any]]:
    details = []
    for task in tasks:
        runs = []
        for run_idx in range(n_runs):
            result = await run_spreadsheet_task(
                task,
                llm_config=llm_config,
                artifact_store=store,
            )
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
        details.append(_task_runs(task.task_id, runs))
    return details


def _task_runs(task_id: str, runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "n_runs": len(runs),
        "n_success": sum(1 for run in runs if run.get("success")),
        "avg_score": round(sum(float(run.get("score", 0.0)) for run in runs) / max(len(runs), 1), 4),
        "runs": runs,
    }


def _aggregate(
    benchmark: str,
    mode: str,
    tag: str,
    llm_config: str,
    n_train: int,
    details: List[Dict[str, Any]],
) -> Dict[str, Any]:
    runs = [run for item in details for run in item["runs"]]
    total_runs = len(runs)
    total_success = sum(1 for run in runs if run.get("success"))
    avg_score = sum(float(run.get("score", 0.0)) for run in runs) / max(total_runs, 1)
    total_tokens = [
        int((run.get("metrics") or {}).get("total_tokens", 0))
        for run in runs
    ]
    retrieved_counts: Dict[str, int] = {}
    used_counts: Dict[str, int] = {}
    called_counts: Dict[str, int] = {}
    for run in runs:
        metrics = run.get("metrics") or {}
        for name in metrics.get("retrieved_skills", []) or []:
            retrieved_counts[name] = retrieved_counts.get(name, 0) + 1
        for name in metrics.get("used_skills", []) or []:
            used_counts[name] = used_counts.get(name, 0) + 1
        for call in ((run.get("trace") or {}).get("tool_calls") or []):
            name = call.get("name")
            if name:
                called_counts[name] = called_counts.get(name, 0) + 1
    return {
        "benchmark": benchmark,
        "mode": mode,
        "tag": tag,
        "llm_config": llm_config,
        "n_train_reserved": n_train,
        "n_tasks": len(details),
        "n_runs": total_runs,
        "total_success": total_success,
        "success_rate": round(total_success / max(total_runs, 1), 4),
        "avg_score": round(avg_score, 4),
        "avg_total_tokens": round(sum(total_tokens) / max(len(total_tokens), 1), 1),
        "avg_at_k": round(total_success / max(total_runs, 1), 4),
        "pass_at_k": round(
            sum(1 for item in details if item.get("n_success", 0) > 0) / max(len(details), 1),
            4,
        ),
        "avg_call_recall": _avg_metric(runs, "call_recall"),
        "avg_call_precision": _avg_metric(runs, "call_precision"),
        "avg_turn_success_rate": _avg_metric(runs, "turn_success_rate"),
        "avg_relaxed_turn_success_rate": _avg_metric(runs, "relaxed_turn_success_rate"),
        "official_valid_rate": _avg_bool_metric(runs, "official_valid"),
        "call_error_summary": _call_error_summary(runs),
        "skill_stats": {
            "retrieved_counts": dict(sorted(retrieved_counts.items())),
            "used_counts": dict(sorted(used_counts.items())),
            "domain_tool_called_counts": dict(sorted(called_counts.items())),
        },
        "details": details,
    }


async def _run_bfcl_model_sweep(
    *,
    test: List[Any],
    n_runs: int,
    llm_config: str,
    model_names: List[str],
    tools: List[Dict[str, Any]],
    store: ArtifactStore,
    adapter_mode: str,
    execution_backend: str,
    data_source: str,
    prompt_style: str,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    tag: str,
) -> Dict[str, Any]:
    reports = []
    for model_name in model_names:
        details = await _run_bfcl_baseline(
            test,
            n_runs,
            llm_config,
            tools,
            store,
            adapter_mode=adapter_mode,
            model_name=model_name,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
        )
        report = _aggregate("bfcl_v3", "probe", tag, llm_config, 0, details)
        report["model_name"] = model_name
        report["adapter_mode"] = adapter_mode
        report["execution_backend"] = execution_backend
        report["bfcl_data_source"] = data_source
        report["bfcl_prompt_style"] = prompt_style
        reports.append(report)
    return {
        "benchmark": "bfcl_v3",
        "mode": "probe",
        "tag": tag,
        "llm_config": llm_config,
        "adapter_mode": adapter_mode,
        "execution_backend": execution_backend,
        "bfcl_data_source": data_source,
        "bfcl_prompt_style": prompt_style,
        "temperature": temperature,
        "bfcl_synthetic_continue": synthetic_continue,
        "bfcl_explicit_skill_tool": explicit_skill_tool,
        "n_models": len(model_names),
        "model_reports": reports,
    }


async def _run_bfcl_evolve(
    *,
    train: List[Any],
    test: List[Any],
    n_train_runs: int,
    n_test_runs: int,
    llm_config: str,
    model_name: str | None,
    tools: List[Dict[str, Any]],
    seed_store: ArtifactStore,
    adapter_mode: str,
    execution_backend: str,
    data_source: str,
    prompt_style: str,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    tag: str,
    save_skills: Path | None,
) -> Dict[str, Any]:
    train_details = await _run_bfcl_baseline(
        train,
        n_train_runs,
        llm_config,
        tools,
        seed_store,
        adapter_mode=adapter_mode,
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        temperature=temperature,
        synthetic_continue=synthetic_continue,
        explicit_skill_tool=explicit_skill_tool,
    )
    train_results = [
        _result_from_dict(run)
        for item in train_details
        for run in item.get("runs", [])
    ]
    evolved = ArtifactStore(seed_store.all())
    for artifact in extract_bfcl_skills_from_results(train_results):
        evolved.add(artifact)
    if save_skills:
        evolved.save(save_skills)

    test_details = await _run_bfcl_baseline(
        test,
        n_test_runs,
        llm_config,
        tools,
        evolved,
        adapter_mode=adapter_mode,
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        temperature=temperature,
        synthetic_continue=synthetic_continue,
        explicit_skill_tool=explicit_skill_tool,
    )
    train_summary = _aggregate("bfcl_v3", "evolve_train", tag, llm_config, len(train), train_details)
    test_summary = _aggregate("bfcl_v3", "evolve_test", tag, llm_config, len(train), test_details)
    return {
        "benchmark": "bfcl_v3",
        "mode": "evolve",
        "tag": tag,
        "llm_config": llm_config,
        "model_name": model_name,
        "adapter_mode": adapter_mode,
        "execution_backend": execution_backend,
        "bfcl_data_source": data_source,
        "bfcl_prompt_style": prompt_style,
        "temperature": temperature,
        "bfcl_synthetic_continue": synthetic_continue,
        "bfcl_explicit_skill_tool": explicit_skill_tool,
        "n_train": len(train),
        "n_test": len(test),
        "n_train_runs": n_train_runs,
        "n_test_runs": n_test_runs,
        "n_skills_seed": len(seed_store.all()),
        "n_skills_evolved": len(evolved.all()),
        "skill_names": [skill.name for skill in evolved.all()],
        "train_summary": {k: v for k, v in train_summary.items() if k != "details"},
        "test_summary": {k: v for k, v in test_summary.items() if k != "details"},
        "train_details": train_details,
        "test_details": test_details,
    }


def _load_artifact_store(args: argparse.Namespace) -> ArtifactStore:
    base = ArtifactStore.load(args.skills) if args.skills else ArtifactStore()
    if args.use_handwritten_skills:
        for artifact in default_bfcl_skill_store().all():
            base.add(artifact)
    return base


def _model_names(args: argparse.Namespace) -> List[str]:
    names = [name.strip() for name in (args.model_names or "").split(",") if name.strip()]
    if names:
        return names
    return [args.model_name] if args.model_name else []


def _avg_metric(runs: List[Dict[str, Any]], metric_name: str) -> float:
    vals = [
        float((run.get("metrics") or {}).get(metric_name, 0.0))
        for run in runs
        if run.get("metrics") is not None
    ]
    return round(sum(vals) / max(len(vals), 1), 4)


def _avg_bool_metric(runs: List[Dict[str, Any]], metric_name: str) -> float | None:
    vals = [
        (run.get("metrics") or {}).get(metric_name)
        for run in runs
        if (run.get("metrics") or {}).get(metric_name) is not None
    ]
    if not vals:
        return None
    return round(sum(1 for value in vals if value is True) / len(vals), 4)


def _call_error_summary(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    wrong_params: Dict[str, int] = {}
    for run in runs:
        for error in (run.get("metrics") or {}).get("call_errors", []) or []:
            etype = error.get("type", "unknown")
            counts[etype] = counts.get(etype, 0) + 1
            if etype == "argument_mismatch":
                for param in (error.get("wrong") or {}):
                    wrong_params[param] = wrong_params.get(param, 0) + 1
                for param in (error.get("missing") or {}):
                    wrong_params[f"missing:{param}"] = wrong_params.get(f"missing:{param}", 0) + 1
                for param in (error.get("unexpected") or {}):
                    wrong_params[f"unexpected:{param}"] = wrong_params.get(f"unexpected:{param}", 0) + 1
    return {
        "error_type_counts": dict(sorted(counts.items())),
        "param_error_counts": dict(sorted(wrong_params.items())),
    }


def _result_from_dict(data: Dict[str, Any]):
    from academic.benchmarks.types import BenchmarkResult

    return BenchmarkResult(
        benchmark=data.get("benchmark", "bfcl_v3"),
        task_id=data.get("task_id", ""),
        success=bool(data.get("success")),
        score=float(data.get("score", 0.0)),
        metrics=data.get("metrics") or {},
        trace=data.get("trace") or {},
        error=data.get("error"),
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
