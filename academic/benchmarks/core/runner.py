"""CLI runner for benchmark-specific baselines/evolve experiments.

Examples:
    python -m academic.benchmarks.core.runner --list
    python -m academic.benchmarks.core.runner --benchmark bfcl_v3 --mode baseline --n-test 5
    python -m academic.benchmarks.core.runner --benchmark spreadsheet --mode baseline --n-test 3
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from uuid import uuid4

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.bfcl import load_bfcl_tasks, load_bfcl_tools, run_bfcl_task
from academic.benchmarks.bfcl.maintenance.adapter import (
    _normalize_fragment_expected,
    _normalize_fragment_question,
    _source_task_snapshot,
    _validate_expected_call_schema,
    _tool_schemas_for_source_task,
    _validate_bundle_task_fragment,
    append_failure_cases_from_result,
    build_bfcl_skill_bundles_llm,
    execute_bfcl_bundle_tests,
    extract_bfcl_skill_artifacts_llm,
    refine_bfcl_skill_store_llm,
    run_bfcl_overlap_refactor_llm,
    select_bfcl_maintenance_targets,
    summarize_case_metrics,
)
from academic.benchmarks.bfcl.skills import (
    default_bfcl_skill_store,
    write_bfcl_handwritten_skills,
)
from academic.benchmarks.core.registry import BENCHMARK_REGISTRY
from academic.benchmarks.core.evolution import OnlineSkillEvolutionRunner
from academic.benchmarks.core.cost_accounting import cost_events_from_runs, summarize_cost_events
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig
from academic.benchmarks.spreadsheet.adapter import (
    SpreadsheetMaintenanceAdapter,
    load_spreadsheet_tasks,
    run_spreadsheet_task,
    run_spreadsheet_task_notebook,
)
from academic.benchmarks.skillsbench.adapter import (
    DEFAULT_SKILLSBENCH_ROOT,
    default_skillsbench_skill_store,
    load_skillsbench_tasks,
    run_skillsbench_task,
)
from academic.benchmarks.core.types import (
    BenchmarkResult,
    SkillArtifact,
    SkillBundleCase,
    SkillInterface,
    SkillLineage,
    SkillTestCaseRun,
    SkillTestResult,
)
from academic.config import AGENT_MODEL, ACADEMIC_ROOT, RESULTS_DIR
from academic.skill_repository.debug_events import DebugEventSink, skill_store_snapshot


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
    parser.add_argument("--train-offset", type=int, default=0, help="Skip this many shuffled train tasks after loading")
    parser.add_argument("--test-offset", type=int, default=0, help="Skip this many shuffled test tasks after loading")
    parser.add_argument(
        "--train-task-ids",
        type=Path,
        default=None,
        help="Optional JSON file containing an ordered list of BFCL train task ids to run.",
    )
    parser.add_argument(
        "--test-task-ids",
        type=Path,
        default=None,
        help="Optional JSON file containing an ordered list of BFCL test task ids to run.",
    )
    parser.add_argument("--n-runs", type=int, default=1)
    parser.add_argument("--n-train-runs", type=int, default=1)
    parser.add_argument("--test-concurrency", type=int, default=1)
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
    parser.add_argument("--top-k-skills", type=int, default=2, help="Maximum retrieved skill artifacts per BFCL task")
    parser.add_argument(
        "--min-skill-score",
        type=float,
        default=0.0,
        help="Hard minimum retrieval score required for a BFCL skill to be selected and injected.",
    )
    parser.add_argument(
        "--skill-injection-mode",
        choices=["none", "prompt_only", "tool_only", "hybrid"],
        default="prompt_only",
        help="How retrieved benchmark skills are exposed to the BFCL model.",
    )
    parser.add_argument(
        "--skill-injector-mode",
        choices=["full", "compact", "budget", "summary", "none", "off"],
        default=None,
        help="Optional test-time skill injector compression/filter mode. Defaults to env or full.",
    )
    parser.add_argument(
        "--skill-context-budget-chars",
        type=int,
        default=None,
        help="Optional prompt character budget for injected skill context.",
    )
    parser.add_argument(
        "--spreadsheet-execution-mode",
        choices=["single", "notebook"],
        default="single",
        help="Spreadsheet executor mode: single runs one code block; notebook allows persistent multi-turn Python cells.",
    )
    parser.add_argument(
        "--spreadsheet-max-turns",
        type=int,
        default=5,
        help="Maximum notebook turns for --spreadsheet-execution-mode notebook.",
    )
    parser.add_argument("--bfcl-explicit-skill-tool", action="store_true")
    parser.add_argument("--save-skills", type=Path, default=None)
    parser.add_argument(
        "--macro-snapshot-dir",
        type=Path,
        default=None,
        help="Optional directory for per-macro skill/store snapshots in evolve mode.",
    )
    parser.add_argument(
        "--load-train-details",
        type=Path,
        default=None,
        help="Optional JSON file containing previously completed BFCL train-task run details; when set in evolve mode, skip rerunning train and reuse these details for skill extraction.",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--partial-output", type=Path, default=None, help="Write completed BFCL task results incrementally")
    parser.add_argument("--max-task-seconds", type=float, default=None, help="Optional wall-clock timeout per BFCL task run")
    parser.add_argument("--cache-dir", type=Path, default=ACADEMIC_ROOT.parent / "data" / "benchmarks")
    parser.add_argument("--skillsbench-root", type=Path, default=DEFAULT_SKILLSBENCH_ROOT)
    parser.add_argument(
        "--skillsbench-task-source",
        choices=["fixture", "tasks", "tasks-no-skills"],
        default="fixture",
        help="SkillsBench task source for the diagnostic adapter.",
    )
    parser.add_argument(
        "--skillsbench-skill-pool",
        choices=["curated", "official", "fixture", "none"],
        default="curated",
        help="Skill pool loaded when --benchmark skillsbench and --skills is not provided.",
    )
    parser.add_argument("--skillsbench-skill-limit", type=int, default=None)
    parser.add_argument(
        "--bfcl-adapter-mode",
        choices=["official", "path_filtered", "debug_hints", "full_tools"],
        default="official",
        help="official exposes all functions for involved BFCL classes; path_filtered is a token-saving ablation.",
    )
    parser.add_argument("--bfcl-execution-backend", choices=["auto", "official", "local_mock"], default="auto")
    parser.add_argument("--bfcl-prompt-style", choices=["native", "official", "academic"], default="native")
    parser.add_argument(
        "--bfcl-tool-api-style",
        choices=["auto", "openai", "openai_stream", "anthropic_direct"],
        default="auto",
        help="Provider protocol for native tool calls. auto uses Anthropic native tools for Claude and streaming OpenAI-compatible calls for Qwen.",
    )
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-steps-per-turn", type=int, default=20)
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
        requested_train_ids = _load_task_id_list(args.train_task_ids) if args.train_task_ids else []
        requested_test_ids = _load_task_id_list(args.test_task_ids) if args.test_task_ids else []
        need_full_train_pool = bool(requested_train_ids)
        need_full_test_pool = bool(requested_test_ids)
        train, test = load_bfcl_tasks(
            cache_dir=args.cache_dir / "bfcl_v3",
            split_seed=args.seed,
            n_train=(50 if need_full_train_pool else n_train + max(args.train_offset, 0)),
            n_test=(150 if need_full_test_pool else n_test + max(args.test_offset, 0)),
            refresh=args.refresh_data,
            data_source=args.bfcl_data_source,
        )
        if requested_train_ids:
            train = _select_tasks_by_id(train, args.train_task_ids)
        else:
            train = train[max(args.train_offset, 0): max(args.train_offset, 0) + n_train]
        if requested_test_ids:
            test = _select_tasks_by_id(test, args.test_task_ids)
        else:
            test = test[max(args.test_offset, 0): max(args.test_offset, 0) + n_test]
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
                tool_api_style=args.bfcl_tool_api_style,
                top_k_skills=args.top_k_skills,
                min_skill_score=args.min_skill_score,
                skill_injection_mode=args.skill_injection_mode,
                max_steps_per_turn=args.max_steps_per_turn,
                partial_output=args.partial_output,
                max_task_seconds=args.max_task_seconds,
                temperature=args.temperature,
                synthetic_continue=args.bfcl_synthetic_continue,
                explicit_skill_tool=args.bfcl_explicit_skill_tool,
                tag=args.tag,
                save_skills=args.save_skills,
                load_train_details=args.load_train_details,
            )
            summary["elapsed_s"] = round(time.monotonic() - t0, 3)
            out = args.output or RESULTS_DIR / f"{args.benchmark}_{args.tag}_{args.mode}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            compact_summary = {
                k: v
                for k, v in summary.items()
                if k
                not in {
                    "skills",
                    "skill_bundles",
                    "maintenance_test_results",
                    "final_maintenance_test_results",
                    "train_details",
                    "refine_details",
                    "post_refine_details",
                    "test_details",
                    "debug_events",
                }
            }
            print(json.dumps(compact_summary, ensure_ascii=False, indent=2))
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
                tool_api_style=args.bfcl_tool_api_style,
                top_k_skills=args.top_k_skills,
                min_skill_score=args.min_skill_score,
                skill_injection_mode=args.skill_injection_mode,
                max_steps_per_turn=args.max_steps_per_turn,
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
            tool_api_style=args.bfcl_tool_api_style,
            top_k_skills=args.top_k_skills,
            min_skill_score=args.min_skill_score,
            skill_injection_mode=args.skill_injection_mode,
            max_steps_per_turn=args.max_steps_per_turn,
            partial_output=args.partial_output,
            max_task_seconds=args.max_task_seconds,
            temperature=args.temperature,
            synthetic_continue=args.bfcl_synthetic_continue,
            explicit_skill_tool=args.bfcl_explicit_skill_tool,
            skill_injector_mode=args.skill_injector_mode,
            skill_context_budget_chars=args.skill_context_budget_chars,
        )
    elif args.benchmark == "spreadsheet":
        n_train = args.n_train if args.n_train is not None else 200
        n_test = args.n_test if args.n_test is not None else 5
        train, test = load_spreadsheet_tasks(
            cache_dir=args.cache_dir / "spreadsheet",
            split_seed=args.seed,
            n_train=n_train + max(args.train_offset, 0),
            n_test=n_test + max(args.test_offset, 0),
            refresh=args.refresh_data,
        )
        train = train[max(args.train_offset, 0): max(args.train_offset, 0) + n_train]
        test = test[max(args.test_offset, 0): max(args.test_offset, 0) + n_test]
        if args.mode == "evolve":
            runner = OnlineSkillEvolutionRunner(
                adapter=SpreadsheetMaintenanceAdapter(),
                config=MaintenanceRunConfig(
                    llm_config=args.llm_config,
                    model_name=args.model_name,
                    tag=args.tag,
                    n_train_runs=args.n_train_runs,
                    n_test_runs=args.n_runs,
                    micro_maintenance_step=1,
                    macro_maintenance_step=10,
                    test_concurrency=args.test_concurrency,
                    max_task_seconds=args.max_task_seconds,
                    top_k_skills=args.top_k_skills,
                    extra={
                        "skill_injector_mode": args.skill_injector_mode,
                        "skill_context_budget_chars": args.skill_context_budget_chars,
                        "spreadsheet_execution_mode": args.spreadsheet_execution_mode,
                        "spreadsheet_max_turns": args.spreadsheet_max_turns,
                        "macro_snapshot_dir": str(args.macro_snapshot_dir) if args.macro_snapshot_dir else "",
                    },
                ),
            )
            summary = await runner.run(
                train_tasks=train,
                test_tasks=test,
                seed_store=store,
                rounds=1,
            )
            summary["elapsed_s"] = round(time.monotonic() - t0, 3)
            summary["spreadsheet_execution_mode"] = args.spreadsheet_execution_mode
            summary["spreadsheet_max_turns"] = args.spreadsheet_max_turns
            if args.save_skills:
                args.save_skills.parent.mkdir(parents=True, exist_ok=True)
                args.save_skills.write_text(json.dumps(summary.get("skills") or [], ensure_ascii=False, indent=2))
            out = args.output or RESULTS_DIR / f"{args.benchmark}_{args.tag}_{args.mode}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            compact_summary = {
                k: v
                for k, v in summary.items()
                if k not in {"train_details", "test_details", "skills"}
            }
            print(json.dumps(compact_summary, ensure_ascii=False, indent=2))
            print(f"Saved detail: {out}")
            return
        details = await _run_spreadsheet_baseline(
            test,
            args.n_runs,
            args.llm_config,
            store,
            model_name=args.model_name,
            concurrency=args.test_concurrency,
            max_task_seconds=args.max_task_seconds,
            top_k_skills=args.top_k_skills,
            skill_injector_mode=args.skill_injector_mode,
            skill_context_budget_chars=args.skill_context_budget_chars,
            execution_mode=args.spreadsheet_execution_mode,
            max_turns=args.spreadsheet_max_turns,
        )
    elif args.benchmark == "skillsbench":
        if args.mode != "baseline":
            raise ValueError("SkillsBench generic runner currently supports --mode baseline only")
        n_test = args.n_test if args.n_test is not None else 5
        train: List[Any] = []
        test = load_skillsbench_tasks(
            skillsbench_root=args.skillsbench_root,
            source=args.skillsbench_task_source,
            limit=n_test,
            offset=args.test_offset,
        )
        if args.skills is None:
            store = default_skillsbench_skill_store(
                skillsbench_root=args.skillsbench_root,
                pool=args.skillsbench_skill_pool,
                limit=args.skillsbench_skill_limit,
            )
        details = await _run_skillsbench_baseline(
            test,
            args.n_runs,
            args.llm_config,
            store,
            model_name=args.model_name,
            concurrency=args.test_concurrency,
            max_task_seconds=args.max_task_seconds,
            top_k_skills=args.top_k_skills,
            min_skill_score=args.min_skill_score,
            skill_injector_mode=args.skill_injector_mode,
            skill_context_budget_chars=args.skill_context_budget_chars,
        )
    else:
        raise ValueError(f"Benchmark {args.benchmark} is registry-only for now")

    summary = _aggregate(args.benchmark, args.mode, args.tag, args.llm_config, len(train), details)
    summary["model_name"] = args.model_name
    summary["skill_injector_mode"] = args.skill_injector_mode
    summary["skill_context_budget_chars"] = args.skill_context_budget_chars
    if args.benchmark == "bfcl_v3":
        summary["adapter_mode"] = "official" if args.no_bfcl_tool_name_hints else args.bfcl_adapter_mode
        summary["execution_backend"] = args.bfcl_execution_backend
        summary["bfcl_data_source"] = args.bfcl_data_source
        summary["bfcl_prompt_style"] = args.bfcl_prompt_style
        summary["bfcl_tool_api_style"] = args.bfcl_tool_api_style
        summary["top_k_skills"] = args.top_k_skills
        summary["min_skill_score"] = args.min_skill_score
        summary["skill_injection_mode"] = args.skill_injection_mode
        summary["skill_injector_mode"] = args.skill_injector_mode
        summary["skill_context_budget_chars"] = args.skill_context_budget_chars
        summary["max_steps_per_turn"] = args.max_steps_per_turn
        summary["temperature"] = args.temperature
        summary["bfcl_synthetic_continue"] = args.bfcl_synthetic_continue
        summary["bfcl_explicit_skill_tool"] = args.bfcl_explicit_skill_tool
    if args.benchmark == "spreadsheet":
        summary["spreadsheet_execution_mode"] = args.spreadsheet_execution_mode
        summary["spreadsheet_max_turns"] = args.spreadsheet_max_turns
    if args.benchmark == "skillsbench":
        summary["diagnostic_only"] = True
        summary["official_pass_rate"] = None
        summary["skillsbench_task_source"] = args.skillsbench_task_source
        summary["skillsbench_skill_pool"] = args.skillsbench_skill_pool if args.skills is None else "custom"
        summary["skillsbench_skill_limit"] = args.skillsbench_skill_limit
        summary["top_k_skills"] = args.top_k_skills
        summary["min_skill_score"] = args.min_skill_score
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
    tool_api_style: str = "auto",
    top_k_skills: int = 5,
    min_skill_score: float = 0.0,
    skill_injection_mode: str = "prompt_only",
    max_steps_per_turn: int = 8,
    partial_output: Path | None = None,
    max_task_seconds: float | None = None,
    temperature: float | None = None,
    synthetic_continue: bool = False,
    explicit_skill_tool: bool = False,
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
    debug_sink: DebugEventSink | None = None,
    phase: str = "",
    concurrency: int = 1,
) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    completed_task_ids: set[str] = set()
    if partial_output and partial_output.exists():
        try:
            payload = json.loads(partial_output.read_text())
            existing = payload.get("details", [])
            if isinstance(existing, list):
                details = [item for item in existing if isinstance(item, dict) and item.get("task_id")]
                completed_task_ids = {str(item.get("task_id")) for item in details}
        except Exception:
            details = []
            completed_task_ids = set()
    completed_before = len(details)
    remaining_tasks = [task for task in tasks if task.task_id not in completed_task_ids]
    concurrency = max(1, int(concurrency or 1))

    async def run_one(task: Any, task_index: int) -> Dict[str, Any]:
        runs = []
        task_store = copy.deepcopy(store) if concurrency > 1 else store
        for run_idx in range(n_runs):
            run_sink = debug_sink.child(
                phase=phase or "bfcl_rollout",
                task_id=task.task_id,
                task_index=task_index,
                run_idx=run_idx,
            ) if debug_sink else None
            try:
                result = await asyncio.wait_for(
                    run_bfcl_task(
                        task,
                        llm_config=llm_config,
                        tools=tools,
                        artifact_store=task_store,
                        adapter_mode=adapter_mode,
                        model_name=model_name,
                        enable_skill_tool=explicit_skill_tool and bool(task_store.all()),
                        execution_backend=execution_backend,
                        prompt_style=prompt_style,
                        tool_api_style=tool_api_style,
                        top_k_skills=top_k_skills,
                        min_skill_score=min_skill_score,
                        max_steps_per_turn=max_steps_per_turn,
                        skill_injection_mode=skill_injection_mode,
                        skill_injector_mode=skill_injector_mode,
                        skill_context_budget_chars=skill_context_budget_chars,
                        temperature=temperature,
                        synthetic_continue=synthetic_continue,
                        debug_sink=run_sink,
                        max_task_seconds=max_task_seconds,
                    ),
                    timeout=max_task_seconds,
                ) if max_task_seconds else await run_bfcl_task(
                    task,
                    llm_config=llm_config,
                    tools=tools,
                    artifact_store=task_store,
                    adapter_mode=adapter_mode,
                    model_name=model_name,
                    enable_skill_tool=explicit_skill_tool and bool(task_store.all()),
                    execution_backend=execution_backend,
                    prompt_style=prompt_style,
                    tool_api_style=tool_api_style,
                    top_k_skills=top_k_skills,
                    min_skill_score=min_skill_score,
                    max_steps_per_turn=max_steps_per_turn,
                    skill_injection_mode=skill_injection_mode,
                    skill_injector_mode=skill_injector_mode,
                    skill_context_budget_chars=skill_context_budget_chars,
                    temperature=temperature,
                    synthetic_continue=synthetic_continue,
                    debug_sink=run_sink,
                    max_task_seconds=max_task_seconds,
                )
            except asyncio.TimeoutError:
                result = BenchmarkResult(
                    benchmark="bfcl_v3",
                    task_id=task.task_id,
                    success=False,
                    score=0.0,
                    metrics={
                        "exception": "TaskTimeout",
                        "max_task_seconds": max_task_seconds,
                    },
                    trace={"task_id": task.task_id, "timed_out": True},
                    error=f"Task exceeded {max_task_seconds} seconds",
                )
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
            metrics = item.get("metrics") or {}
            print(
                json.dumps(
                    {
                        "progress": "bfcl_task_run",
                        "task_index": task_index,
                        "n_tasks": len(tasks),
                        "task_id": task.task_id,
                        "run_idx": run_idx,
                        "score": item.get("score"),
                        "success": item.get("success"),
                        "official_valid": metrics.get("official_valid"),
                        "call_f1": metrics.get("call_f1"),
                        "elapsed_s": metrics.get("elapsed_s"),
                        "skill_injection_mode": metrics.get("skill_injection_mode"),
                        "prompt_injected_skills": metrics.get("prompt_injected_skills"),
                        "tool_injected_skills": metrics.get("tool_injected_skills"),
                        "called_skill_tools": metrics.get("called_skill_tools"),
                        "error": item.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return _task_runs(task, runs)

    if concurrency > 1 and len(remaining_tasks) > 1:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(task: Any, task_index: int) -> tuple[int, Dict[str, Any]]:
            async with sem:
                return task_index, await run_one(task, task_index)

        completed = await asyncio.gather(
            *[
                guarded(task, completed_before + offset)
                for offset, task in enumerate(remaining_tasks)
            ]
        )
        for _task_index, detail in sorted(completed, key=lambda item: item[0]):
            details.append(detail)
        if partial_output:
            partial_output.parent.mkdir(parents=True, exist_ok=True)
            partial_output.write_text(
                json.dumps(
                    {
                        "benchmark": "bfcl_v3",
                        "completed_tasks": len(details),
                        "total_tasks": len(tasks),
                        "details": details,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return details

    for offset, task in enumerate(remaining_tasks):
        task_index = completed_before + offset
        runs = []
        for run_idx in range(n_runs):
            run_sink = debug_sink.child(
                phase=phase or "bfcl_rollout",
                task_id=task.task_id,
                task_index=task_index,
                run_idx=run_idx,
            ) if debug_sink else None
            try:
                result = await asyncio.wait_for(
                    run_bfcl_task(
                        task,
                        llm_config=llm_config,
                        tools=tools,
                        artifact_store=store,
                        adapter_mode=adapter_mode,
                        model_name=model_name,
                        enable_skill_tool=explicit_skill_tool and bool(store.all()),
                        execution_backend=execution_backend,
                        prompt_style=prompt_style,
                        tool_api_style=tool_api_style,
                        top_k_skills=top_k_skills,
                        min_skill_score=min_skill_score,
                        max_steps_per_turn=max_steps_per_turn,
                        skill_injection_mode=skill_injection_mode,
                        skill_injector_mode=skill_injector_mode,
                        skill_context_budget_chars=skill_context_budget_chars,
                        temperature=temperature,
                        synthetic_continue=synthetic_continue,
                        debug_sink=run_sink,
                        max_task_seconds=max_task_seconds,
                    ),
                    timeout=max_task_seconds,
                ) if max_task_seconds else await run_bfcl_task(
                    task,
                    llm_config=llm_config,
                    tools=tools,
                    artifact_store=store,
                    adapter_mode=adapter_mode,
                    model_name=model_name,
                    enable_skill_tool=explicit_skill_tool and bool(store.all()),
                    execution_backend=execution_backend,
                    prompt_style=prompt_style,
                    tool_api_style=tool_api_style,
                    top_k_skills=top_k_skills,
                    min_skill_score=min_skill_score,
                    max_steps_per_turn=max_steps_per_turn,
                    skill_injection_mode=skill_injection_mode,
                    skill_injector_mode=skill_injector_mode,
                    skill_context_budget_chars=skill_context_budget_chars,
                    temperature=temperature,
                    synthetic_continue=synthetic_continue,
                    debug_sink=run_sink,
                    max_task_seconds=max_task_seconds,
                )
            except asyncio.TimeoutError:
                result = BenchmarkResult(
                    benchmark="bfcl_v3",
                    task_id=task.task_id,
                    success=False,
                    score=0.0,
                    metrics={
                        "exception": "TaskTimeout",
                        "max_task_seconds": max_task_seconds,
                    },
                    trace={"task_id": task.task_id, "timed_out": True},
                    error=f"Task exceeded {max_task_seconds} seconds",
                )
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
            metrics = item.get("metrics") or {}
            print(
                json.dumps(
                    {
                        "progress": "bfcl_task_run",
                        "task_index": task_index,
                        "n_tasks": len(tasks),
                        "task_id": task.task_id,
                        "run_idx": run_idx,
                        "score": item.get("score"),
                        "success": item.get("success"),
                        "official_valid": metrics.get("official_valid"),
                        "call_f1": metrics.get("call_f1"),
                        "elapsed_s": metrics.get("elapsed_s"),
                        "skill_injection_mode": metrics.get("skill_injection_mode"),
                        "prompt_injected_skills": metrics.get("prompt_injected_skills"),
                        "tool_injected_skills": metrics.get("tool_injected_skills"),
                        "called_skill_tools": metrics.get("called_skill_tools"),
                        "error": item.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        details.append(_task_runs(task, runs))
        if partial_output:
            partial_output.parent.mkdir(parents=True, exist_ok=True)
            partial_output.write_text(
                json.dumps(
                    {
                        "benchmark": "bfcl_v3",
                        "completed_tasks": len(details),
                        "total_tasks": len(tasks),
                        "details": details,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    return details


async def _run_spreadsheet_baseline(
    tasks: List[Any],
    n_runs: int,
    llm_config: str,
    store: ArtifactStore,
    model_name: str | None = None,
    concurrency: int = 1,
    max_task_seconds: float | None = None,
    top_k_skills: int = 5,
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
    execution_mode: str = "single",
    max_turns: int = 5,
) -> List[Dict[str, Any]]:
    concurrency = max(1, int(concurrency or 1))

    async def run_one(task: Any, task_index: int) -> Tuple[int, Dict[str, Any]]:
        runs = []
        task_store = copy.deepcopy(store) if concurrency > 1 else store
        for run_idx in range(n_runs):
            if execution_mode == "notebook":
                coro = run_spreadsheet_task_notebook(
                    task,
                    llm_config=llm_config,
                    model_name=model_name,
                    artifact_store=task_store,
                    top_k_skills=top_k_skills,
                    skill_injector_mode=skill_injector_mode,
                    skill_context_budget_chars=skill_context_budget_chars,
                    max_turns=max_turns,
                )
            else:
                coro = run_spreadsheet_task(
                    task,
                    llm_config=llm_config,
                    model_name=model_name,
                    artifact_store=task_store,
                    top_k_skills=top_k_skills,
                    skill_injector_mode=skill_injector_mode,
                    skill_context_budget_chars=skill_context_budget_chars,
                )
            result = await asyncio.wait_for(coro, timeout=max_task_seconds) if max_task_seconds else await coro
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
            metrics = item.get("metrics") or {}
            print(
                json.dumps(
                    {
                        "progress": "spreadsheet_task_run",
                        "task_index": task_index,
                        "n_tasks": len(tasks),
                        "task_id": getattr(task, "task_id", ""),
                        "run_idx": run_idx,
                        "score": item.get("score"),
                        "success": item.get("success"),
                        "elapsed_s": metrics.get("elapsed_s"),
                        "retrieved_skills": metrics.get("retrieved_skills"),
                        "error": item.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return task_index, _task_runs(task, runs)

    if concurrency <= 1 or len(tasks) <= 1:
        return [(await run_one(task, task_index))[1] for task_index, task in enumerate(tasks)]

    sem = asyncio.Semaphore(concurrency)

    async def guarded(task: Any, task_index: int) -> Tuple[int, Dict[str, Any]]:
        async with sem:
            return await run_one(task, task_index)

    completed = await asyncio.gather(
        *[guarded(task, task_index) for task_index, task in enumerate(tasks)]
    )
    return [detail for _idx, detail in sorted(completed, key=lambda item: item[0])]


async def _run_skillsbench_baseline(
    tasks: List[Any],
    n_runs: int,
    llm_config: str,
    store: ArtifactStore,
    model_name: str | None = None,
    concurrency: int = 1,
    max_task_seconds: float | None = None,
    top_k_skills: int = 5,
    min_skill_score: float = 0.0,
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
) -> List[Dict[str, Any]]:
    concurrency = max(1, int(concurrency or 1))

    async def run_one(task: Any, task_index: int) -> Tuple[int, Dict[str, Any]]:
        runs = []
        task_store = copy.deepcopy(store) if concurrency > 1 else store
        for run_idx in range(n_runs):
            coro = run_skillsbench_task(
                task,
                llm_config=llm_config,
                model_name=model_name,
                artifact_store=task_store,
                top_k_skills=top_k_skills,
                min_skill_score=min_skill_score,
                skill_injector_mode=skill_injector_mode,
                skill_context_budget_chars=skill_context_budget_chars,
                max_request_wall_s=max_task_seconds,
            )
            result = await asyncio.wait_for(coro, timeout=max_task_seconds) if max_task_seconds else await coro
            item = result.as_dict()
            item["run_idx"] = run_idx
            runs.append(item)
            metrics = item.get("metrics") or {}
            print(
                json.dumps(
                    {
                        "progress": "skillsbench_task_run",
                        "task_index": task_index,
                        "n_tasks": len(tasks),
                        "task_id": getattr(task, "task_id", ""),
                        "run_idx": run_idx,
                        "score": item.get("score"),
                        "success": item.get("success"),
                        "retrieval_hit_at_k": metrics.get("retrieval_hit_at_k"),
                        "selection_hit": metrics.get("selection_hit"),
                        "retrieved_skills": metrics.get("retrieved_skills"),
                        "selected_skill_names": metrics.get("selected_skill_names"),
                        "elapsed_s": metrics.get("elapsed_s"),
                        "error": item.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return task_index, _task_runs(task, runs)

    if concurrency <= 1 or len(tasks) <= 1:
        return [(await run_one(task, task_index))[1] for task_index, task in enumerate(tasks)]

    sem = asyncio.Semaphore(concurrency)

    async def guarded(task: Any, task_index: int) -> Tuple[int, Dict[str, Any]]:
        async with sem:
            return await run_one(task, task_index)

    completed = await asyncio.gather(
        *[guarded(task, task_index) for task_index, task in enumerate(tasks)]
    )
    return [detail for _idx, detail in sorted(completed, key=lambda item: item[0])]


def _task_snapshot(task: Any) -> Dict[str, Any]:
    return {
        "benchmark": getattr(task, "benchmark", "bfcl_v3"),
        "task_id": getattr(task, "task_id", ""),
        "question": copy.deepcopy(getattr(task, "question", None)),
        "expected": copy.deepcopy(getattr(task, "expected", None)),
        "input_artifacts": copy.deepcopy(getattr(task, "input_artifacts", {}) or {}),
        "metadata": copy.deepcopy(getattr(task, "metadata", {}) or {}),
    }


def _store_snapshot(store: ArtifactStore) -> Dict[str, Any]:
    return skill_store_snapshot(store)


def _artifact_brief(artifact: SkillArtifact) -> Dict[str, Any]:
    data = artifact.as_dict()
    bundle = data.get("bundle") or {}
    return {
        "name": data.get("name"),
        "kind": data.get("kind"),
        "description": data.get("description"),
        "body": data.get("body"),
        "interface": copy.deepcopy(data.get("interface") or {}),
        "version": data.get("version"),
        "version_kind": data.get("version_kind") or artifact.version_kind(),
        "status": data.get("status"),
        "stale": data.get("stale"),
        "dependencies": copy.deepcopy(data.get("dependencies") or []),
        "dependency_pins": copy.deepcopy(data.get("dependency_pins") or []),
        "lineage": copy.deepcopy(data.get("lineage") or {}),
        "metadata": {
            key: copy.deepcopy((data.get("metadata") or {}).get(key))
            for key in ("intent_keywords", "allowed_tools", "source_task_ids", "source", "last_refine_reason")
            if key in (data.get("metadata") or {})
        },
        "bundle_id": bundle.get("bundle_id"),
        "bundle_version": bundle.get("bundle_version"),
        "bundle_counts": {
            "positive": len(bundle.get("positive_cases") or []),
            "negative": len(bundle.get("negative_cases") or []),
            "integration": len(bundle.get("integration_cases") or []),
        },
    }


def _artifact_full_snapshot(artifact: SkillArtifact) -> Dict[str, Any]:
    return artifact.as_dict()


def _bundle_brief(artifact: SkillArtifact) -> Dict[str, Any]:
    bundle = artifact.bundle.as_dict()
    return {
        "skill_name": artifact.name,
        "bundle_id": bundle.get("bundle_id"),
        "bundle_version": bundle.get("bundle_version"),
        "maintenance_notes": bundle.get("maintenance_notes", ""),
        "positive": len(bundle.get("positive_cases") or []),
        "negative": len(bundle.get("negative_cases") or []),
        "integration": len(bundle.get("integration_cases") or []),
        "cases": {
            "positive": copy.deepcopy(bundle.get("positive_cases") or []),
            "negative": copy.deepcopy(bundle.get("negative_cases") or []),
            "integration": copy.deepcopy(bundle.get("integration_cases") or []),
        },
        "fixtures": copy.deepcopy(bundle.get("fixtures") or {}),
    }


def _bundle_case_ids(bundle: Dict[str, Any]) -> Dict[str, List[str]]:
    return {
        key: [
            str(case.get("case_id") or "")
            for case in bundle.get(f"{key}_cases", []) or []
            if isinstance(case, dict)
        ]
        for key in ("positive", "negative", "integration")
    }


def _bundle_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before_ids = _bundle_case_ids(before)
    after_ids = _bundle_case_ids(after)
    return {
        "bundle_version_before": before.get("bundle_version"),
        "bundle_version_after": after.get("bundle_version"),
        "maintenance_notes_changed": before.get("maintenance_notes") != after.get("maintenance_notes"),
        "case_ids_before": before_ids,
        "case_ids_after": after_ids,
        "added_case_ids": {
            key: [item for item in after_ids.get(key, []) if item not in before_ids.get(key, [])]
            for key in before_ids
        },
        "removed_case_ids": {
            key: [item for item in before_ids.get(key, []) if item not in after_ids.get(key, [])]
            for key in before_ids
        },
    }


def _artifact_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    changed_fields = []
    for key in ("description", "body", "interface", "status", "stale", "dependencies", "dependency_pins"):
        if before.get(key) != after.get(key):
            changed_fields.append(key)
    return {
        "name": after.get("name") or before.get("name"),
        "version_before": before.get("version"),
        "version_after": after.get("version"),
        "version_kind_before": before.get("version_kind"),
        "version_kind_after": after.get("version_kind"),
        "changed_fields": changed_fields,
        "description_before": before.get("description", ""),
        "description_after": after.get("description", ""),
        "body_before": before.get("body", ""),
        "body_after": after.get("body", ""),
        "dependencies_before": copy.deepcopy(before.get("dependencies") or []),
        "dependencies_after": copy.deepcopy(after.get("dependencies") or []),
        "bundle_diff": _bundle_diff(before.get("bundle") or {}, after.get("bundle") or {}),
    }


def _safe_debug_events(sink: DebugEventSink | None) -> List[Dict[str, Any]]:
    return list(sink.events) if sink else []


def _detail_debug_events(details: List[Dict[str, Any]], *, phase: str = "") -> List[Dict[str, Any]]:
    """Flatten executor/retriever events stored inside per-task BFCL traces."""

    events: List[Dict[str, Any]] = []
    for detail in details or []:
        task_id = str(detail.get("task_id") or "")
        for run_idx, run in enumerate(detail.get("runs") or []):
            trace = run.get("trace") or {}
            for event in trace.get("debug_events") or []:
                if not isinstance(event, dict):
                    continue
                row = copy.deepcopy(event)
                row.setdefault("phase", phase)
                row.setdefault("task_id", task_id)
                row.setdefault("run_idx", run_idx)
                row.setdefault("component", "bfcl_executor")
                events.append(row)
    return events


def _task_runs(task: Any, runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "task_id": getattr(task, "task_id", ""),
        "task": _task_snapshot(task),
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
    input_tokens = [
        int((run.get("metrics") or {}).get("input_tokens", 0))
        for run in runs
    ]
    cache_input_tokens = [
        int((run.get("metrics") or {}).get("cache_input_tokens", 0))
        for run in runs
    ]
    output_tokens = [
        int((run.get("metrics") or {}).get("completion_tokens", 0))
        for run in runs
    ]
    total_token_sum = sum(total_tokens)
    cost_events = cost_events_from_runs(runs)
    cost_breakdown = summarize_cost_events(cost_events)
    estimated_cost = float((cost_breakdown.get("summary") or {}).get("estimated_cost") or 0.0)
    official_valid_count = sum(
        1 for run in runs if (run.get("metrics") or {}).get("official_valid") is True
    )
    score_sum = sum(float(run.get("score", 0.0)) for run in runs)
    correct_runs = [
        run
        for run in runs
        if run.get("success") or (run.get("metrics") or {}).get("official_valid") is True
    ]
    correct_only_cost_breakdown = summarize_cost_events(cost_events_from_runs(correct_runs))
    retrieved_counts: Dict[str, int] = {}
    used_counts: Dict[str, int] = {}
    prompt_injected_counts: Dict[str, int] = {}
    tool_injected_counts: Dict[str, int] = {}
    called_skill_counts: Dict[str, int] = {}
    called_counts: Dict[str, int] = {}
    elapsed_values: List[float] = []
    timeout_count = 0
    step_values: List[int] = []
    for run in runs:
        metrics = run.get("metrics") or {}
        trace = run.get("trace") or {}
        if metrics.get("exception") == "TaskTimeout" or trace.get("timed_out"):
            timeout_count += 1
        if metrics.get("elapsed_s") is not None:
            elapsed_values.append(float(metrics.get("elapsed_s") or 0.0))
        if metrics.get("n_model_steps") is not None:
            step_values.append(int(metrics.get("n_model_steps") or 0))
        for name in metrics.get("retrieved_skills", []) or []:
            retrieved_counts[name] = retrieved_counts.get(name, 0) + 1
        for name in metrics.get("used_skills", []) or []:
            used_counts[name] = used_counts.get(name, 0) + 1
        for name in metrics.get("prompt_injected_skills", []) or []:
            prompt_injected_counts[name] = prompt_injected_counts.get(name, 0) + 1
        for name in metrics.get("tool_injected_skills", []) or []:
            tool_injected_counts[name] = tool_injected_counts.get(name, 0) + 1
        for name in metrics.get("called_skill_tools", []) or []:
            called_skill_counts[name] = called_skill_counts.get(name, 0) + 1
        for call in (trace.get("tool_calls") or []):
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
        "avg_input_tokens": round(sum(input_tokens) / max(len(input_tokens), 1), 1),
        "avg_cache_input_tokens": round(sum(cache_input_tokens) / max(len(cache_input_tokens), 1), 1),
        "avg_output_tokens": round(sum(output_tokens) / max(len(output_tokens), 1), 1),
        "cost_breakdown": cost_breakdown,
        "correct_only_cost_breakdown": correct_only_cost_breakdown,
        "cost_metrics": {
            "estimated_total_cost": round(estimated_cost, 8),
            "estimated_cost_per_run": round(estimated_cost / max(total_runs, 1), 8),
            "successes_per_dollar": round(total_success / estimated_cost, 6) if estimated_cost > 0 else None,
            "score_points_per_dollar": round(score_sum / estimated_cost, 6) if estimated_cost > 0 else None,
            "official_valid_per_dollar": round(official_valid_count / estimated_cost, 6) if estimated_cost > 0 else None,
            "correct_run_count": len(correct_runs),
        },
        "utility_per_million_tokens": {
            "successes_per_million_tokens": round(total_success * 1_000_000 / max(total_token_sum, 1), 6),
            "score_points_per_million_tokens": round(score_sum * 1_000_000 / max(total_token_sum, 1), 6),
            "official_valid_per_million_tokens": round(official_valid_count * 1_000_000 / max(total_token_sum, 1), 6),
            "total_tokens": total_token_sum,
            "input_tokens": sum(input_tokens),
            "cache_input_tokens": sum(cache_input_tokens),
            "output_tokens": sum(output_tokens),
        },
        "timeout_rate": round(timeout_count / max(total_runs, 1), 4),
        "avg_elapsed_s": round(sum(elapsed_values) / max(len(elapsed_values), 1), 3) if elapsed_values else None,
        "max_elapsed_s": round(max(elapsed_values), 3) if elapsed_values else None,
        "avg_model_steps": round(sum(step_values) / max(len(step_values), 1), 2) if step_values else None,
        "max_model_steps": max(step_values) if step_values else None,
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
        "official_avg_at_k": _avg_bool_metric(runs, "official_valid"),
        "official_pass_at_k": _pass_bool_metric(details, "official_valid"),
        "call_error_summary": _call_error_summary(runs),
        "skill_stats": {
            "retrieved_counts": dict(sorted(retrieved_counts.items())),
            "prompt_injected_counts": dict(sorted(prompt_injected_counts.items())),
            "tool_injected_counts": dict(sorted(tool_injected_counts.items())),
            "used_counts": dict(sorted(used_counts.items())),
            "called_skill_tool_counts": dict(sorted(called_skill_counts.items())),
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
    tool_api_style: str,
    top_k_skills: int,
    min_skill_score: float,
    skill_injection_mode: str,
    max_steps_per_turn: int,
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
            tool_api_style=tool_api_style,
            top_k_skills=top_k_skills,
            min_skill_score=min_skill_score,
            skill_injection_mode=skill_injection_mode,
            max_steps_per_turn=max_steps_per_turn,
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
        report["bfcl_tool_api_style"] = tool_api_style
        report["top_k_skills"] = top_k_skills
        report["min_skill_score"] = min_skill_score
        report["skill_injection_mode"] = skill_injection_mode
        report["max_steps_per_turn"] = max_steps_per_turn
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
        "bfcl_tool_api_style": tool_api_style,
        "top_k_skills": top_k_skills,
        "min_skill_score": min_skill_score,
        "skill_injection_mode": skill_injection_mode,
        "max_steps_per_turn": max_steps_per_turn,
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
    tool_api_style: str,
    top_k_skills: int,
    min_skill_score: float,
    skill_injection_mode: str,
    max_steps_per_turn: int,
    partial_output: Path | None,
    max_task_seconds: float | None,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    tag: str,
    save_skills: Path | None,
    load_train_details: Path | None,
) -> Dict[str, Any]:
    phase_t0 = time.monotonic()
    debug_sink = DebugEventSink.from_env(
        base_context={
            "experiment": tag,
            "benchmark": "bfcl_v3",
            "component": "bfcl_evolve",
        }
    )

    def log_phase(name: str, **extra: Any) -> None:
        nonlocal phase_t0
        now = time.monotonic()
        payload = {
            "progress": "bfcl_evolve_phase_done",
            "phase": name,
            "duration_s": round(now - phase_t0, 3),
            **extra,
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        phase_t0 = now

    evolved = ArtifactStore(seed_store.all(), test_results=seed_store.test_results())
    max_extracted = int(os.environ.get("BFCL_MAX_EXTRACTED_SKILLS", "0") or "0")
    n_extracted_total = 0
    train_details: List[Dict[str, Any]] = []
    train_partial = _phase_partial_path(partial_output, "train")

    async def extract_after_task(detail: Dict[str, Any], task_index: int) -> List[SkillArtifact]:
        nonlocal n_extracted_total
        task_id = str(detail.get("task_id") or "")
        llm_task_results = [
            _result_from_dict(run).as_dict()
            for run in detail.get("runs", [])
        ]
        existing_names = [skill.name for skill in evolved.all()]
        extracted_for_task = await extract_bfcl_skill_artifacts_llm(
            llm_task_results,
            tool_schemas=tools,
            existing_artifacts=evolved.all(),
            llm_config=llm_config,
            model_name=model_name,
            audit_context={
                "phase": "extractor",
                "experiment": tag,
                "task_id": task_id,
                "online_task_index": task_index,
            },
        )
        if max_extracted > 0:
            remaining = max(max_extracted - n_extracted_total, 0)
            extracted_for_task = extracted_for_task[:remaining]
        for artifact in extracted_for_task:
            evolved.add(artifact)
        for artifact in evolved.all():
            _ensure_artifact_maintenance_fields(artifact)
        n_extracted_total += len(extracted_for_task)
        debug_sink.emit(
            "extractor_done",
            phase="extract",
            task_id=task_id,
            online_task_index=task_index,
            input={
                "n_train_results": len(llm_task_results),
                "source_task_ids": [task_id] if task_id else [],
                "existing_skill_names": existing_names,
                "tool_names": [
                    str((tool.get("function") or {}).get("name") or "")
                    for tool in tools
                    if (tool.get("function") or {}).get("name")
                ],
            },
            output={
                "new_skill_names": [artifact.name for artifact in extracted_for_task],
                "artifacts": [_artifact_full_snapshot(artifact) for artifact in extracted_for_task],
                "store_after": _store_snapshot(evolved),
            },
        )
        debug_sink.emit(
            "store_update",
            phase="extract",
            task_id=task_id,
            online_task_index=task_index,
            input={"action": "add_extracted_skills", "n_new_skills": len(extracted_for_task), "source_task_ids": [task_id] if task_id else []},
            output={
                "new_skill_names": [artifact.name for artifact in extracted_for_task],
                "store_after": _store_snapshot(evolved),
            },
        )
        log_phase(
            "extract_candidate_skills",
            task_id=task_id,
            online_task_index=task_index,
            n_extracted=len(extracted_for_task),
            n_store=len(evolved.all()),
        )
        return extracted_for_task

    if load_train_details:
        loaded_details = _load_saved_details(load_train_details)
        loaded_by_task_id = {str(item.get("task_id") or ""): item for item in loaded_details}
        ordered_loaded = [
            loaded_by_task_id[str(task.task_id)]
            for task in train
            if str(task.task_id) in loaded_by_task_id
        ] or loaded_details
        for task_index, detail in enumerate(ordered_loaded):
            for event in _detail_debug_events([detail], phase="train"):
                debug_sink.emit(str(event.get("event_type") or "debug_event"), **{k: v for k, v in event.items() if k not in {"event_id", "ts", "event_type"}})
            train_details.append(detail)
            await extract_after_task(detail, task_index)
    else:
        resumed_details = _load_saved_details(train_partial) if train_partial and train_partial.exists() else []
        resumed_by_task_id = {str(item.get("task_id") or ""): item for item in resumed_details}
        for task_index, task in enumerate(train):
            task_id = str(task.task_id)
            if task_id in resumed_by_task_id:
                detail = resumed_by_task_id[task_id]
                for event in _detail_debug_events([detail], phase="train"):
                    debug_sink.emit(str(event.get("event_type") or "debug_event"), **{k: v for k, v in event.items() if k not in {"event_id", "ts", "event_type"}})
            else:
                task_details = await _run_bfcl_baseline(
                    [task],
                    n_train_runs,
                    llm_config,
                    tools,
                    evolved,
                    adapter_mode=adapter_mode,
                    model_name=model_name,
                    execution_backend=execution_backend,
                    prompt_style=prompt_style,
                    tool_api_style=tool_api_style,
                    top_k_skills=top_k_skills,
                    min_skill_score=min_skill_score,
                    skill_injection_mode=skill_injection_mode,
                    max_steps_per_turn=max_steps_per_turn,
                    partial_output=None,
                    max_task_seconds=max_task_seconds,
                    temperature=temperature,
                    synthetic_continue=synthetic_continue,
                    explicit_skill_tool=explicit_skill_tool,
                    debug_sink=debug_sink.child(phase="train", online_task_index=task_index),
                    phase="train",
                )
                detail = task_details[0]
            train_details.append(detail)
            await extract_after_task(detail, task_index)
            if train_partial:
                train_partial.parent.mkdir(parents=True, exist_ok=True)
                train_partial.write_text(
                    json.dumps(
                        {
                            "benchmark": "bfcl_v3",
                            "completed_tasks": len(train_details),
                            "total_tasks": len(train),
                            "details": train_details,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
    debug_sink.emit(
        "experiment_phase_done",
        phase="train",
        output={
            "n_details": len(train_details),
            "task_ids": [item.get("task_id") for item in train_details],
            "store_after": _store_snapshot(evolved),
            "online_extract_per_task": True,
        },
    )
    log_phase("train_rollout", n_train_details=len(train_details), online_extract_per_task=True)
    maintenance_targets = select_bfcl_maintenance_targets(
        evolved,
        train_details=train_details,
    )
    maintenance_targets = _limit_maintenance_targets(maintenance_targets)
    initial_bundle_before = {
        artifact.name: _artifact_full_snapshot(artifact)
        for artifact in evolved.all()
        if artifact.name in set(maintenance_targets)
    }
    debug_sink.emit(
        "bundle_builder_start",
        phase="build_initial_bundles",
        input={
            "targets": maintenance_targets,
            "n_train_details": len(train_details),
            "n_replay_details": 0,
            "store_before": _store_snapshot(evolved),
        },
    )
    await build_bfcl_skill_bundles_llm(
        evolved,
        train_details=train_details,
        llm_config=llm_config,
        model_name=model_name,
        artifact_names=maintenance_targets,
        audit_context={"phase": "build_initial_bundles", "experiment": tag},
    )
    initial_bundle_after = {
        artifact.name: _artifact_full_snapshot(artifact)
        for artifact in evolved.all()
        if artifact.name in set(maintenance_targets)
    }
    debug_sink.emit(
        "bundle_builder_done",
        phase="build_initial_bundles",
        input={
            "targets": maintenance_targets,
            "source_task_ids": [item.get("task_id") for item in train_details],
        },
        output={
            "targets": maintenance_targets,
            "bundles": [_bundle_brief(artifact) for artifact in evolved.all() if artifact.name in set(maintenance_targets)],
            "bundle_diffs": [
                {
                    "skill_name": name,
                    "artifact_diff": _artifact_diff(initial_bundle_before.get(name, {}), after),
                }
                for name, after in initial_bundle_after.items()
            ],
            "store_after": _store_snapshot(evolved),
        },
    )
    log_phase("build_initial_bundles", n_targets=len(maintenance_targets))
    refine_input_skill_names = [skill.name for skill in evolved.all()]
    skip_integration_replay = os.environ.get("BFCL_EVOLVE_SKIP_REPLAY", "").strip().lower() in {"1", "true", "yes"}
    if skip_integration_replay:
        refine_details = []
        refine_summary_before = _aggregate("bfcl_v3", "evolve_refine_before", tag, llm_config, len(train), refine_details)
        debug_sink.emit(
            "experiment_phase_done",
            phase="integration_replay_before_refine",
            output={"skipped": True, "reason": "BFCL_EVOLVE_SKIP_REPLAY enabled"},
        )
    else:
        refine_details = await _run_bfcl_baseline(
        train,
        1,
        llm_config,
        tools,
        evolved,
        adapter_mode=adapter_mode,
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        top_k_skills=top_k_skills,
        min_skill_score=min_skill_score,
        skill_injection_mode=skill_injection_mode,
        max_steps_per_turn=max_steps_per_turn,
        partial_output=_phase_partial_path(partial_output, "refine"),
        max_task_seconds=max_task_seconds,
        temperature=temperature,
        synthetic_continue=synthetic_continue,
        explicit_skill_tool=explicit_skill_tool,
            debug_sink=debug_sink.child(phase="integration_replay_before_refine"),
            phase="integration_replay_before_refine",
        )
        refine_summary_before = _aggregate("bfcl_v3", "evolve_refine_before", tag, llm_config, len(train), refine_details)
        debug_sink.emit(
            "experiment_phase_done",
            phase="integration_replay_before_refine",
            output={
                "skipped": False,
                "n_details": len(refine_details),
                "task_ids": [item.get("task_id") for item in refine_details],
                "store_after": _store_snapshot(evolved),
            },
        )
    log_phase("integration_replay_before_refine", n_refine_details=len(refine_details))
    maintenance_targets = select_bfcl_maintenance_targets(
        evolved,
        train_details=train_details,
        replay_details=refine_details,
    )
    maintenance_targets = _limit_maintenance_targets(maintenance_targets)
    maintenance_target_set = set(maintenance_targets)
    replay_bundle_targets = []
    for artifact in evolved.all():
        if artifact.name not in maintenance_target_set:
            continue
        needs_bundle = not artifact.bundle.all_cases()
        skill_used_in_failed_replay = False
        for detail in refine_details or []:
            run = (detail.get("runs") or [{}])[0]
            metrics = run.get("metrics") or {}
            if metrics.get("official_valid") is not False:
                continue
            used_names = set(metrics.get("retrieved_skills") or []) | set(metrics.get("prompt_injected_skills") or [])
            if artifact.name in used_names:
                skill_used_in_failed_replay = True
                break
        if needs_bundle or skill_used_in_failed_replay:
            replay_bundle_targets.append(artifact.name)
    if replay_bundle_targets:
        replay_bundle_before = {
            artifact.name: _artifact_full_snapshot(artifact)
            for artifact in evolved.all()
            if artifact.name in set(replay_bundle_targets)
        }
        debug_sink.emit(
            "bundle_builder_start",
            phase="build_replay_bundles",
            input={
                "targets": replay_bundle_targets,
                "selected_maintenance_targets": maintenance_targets,
                "n_train_details": len(train_details),
                "n_replay_details": len(refine_details),
                "store_before": _store_snapshot(evolved),
            },
        )
        await build_bfcl_skill_bundles_llm(
            evolved,
            train_details=train_details,
            replay_details=refine_details,
            llm_config=llm_config,
            model_name=model_name,
            artifact_names=replay_bundle_targets,
            audit_context={"phase": "build_replay_bundles", "experiment": tag},
        )
        replay_bundle_after = {
            artifact.name: _artifact_full_snapshot(artifact)
            for artifact in evolved.all()
            if artifact.name in set(replay_bundle_targets)
        }
        debug_sink.emit(
            "bundle_builder_done",
            phase="build_replay_bundles",
            input={"targets": replay_bundle_targets, "source_task_ids": [item.get("task_id") for item in train_details]},
            output={
                "targets": replay_bundle_targets,
                "bundles": [_bundle_brief(artifact) for artifact in evolved.all() if artifact.name in set(replay_bundle_targets)],
                "bundle_diffs": [
                    {
                        "skill_name": name,
                        "artifact_diff": _artifact_diff(replay_bundle_before.get(name, {}), after),
                    }
                    for name, after in replay_bundle_after.items()
                ],
                "store_after": _store_snapshot(evolved),
            },
        )
    log_phase("build_replay_bundles", n_targets=len(replay_bundle_targets), selected_targets=len(maintenance_targets))

    async def run_bundle_test_for(artifact: SkillArtifact) -> SkillTestResult:
        debug_sink.emit(
            "unit_test_start",
            phase="unit_test",
            input={
                "skill": _artifact_brief(artifact),
                "bundle": _bundle_brief(artifact),
                "case_count": len(artifact.bundle.all_cases()),
            },
        )
        result = await execute_bfcl_bundle_tests(
            artifact,
            tools=tools,
            llm_config=llm_config,
            model_name=model_name,
            adapter_mode=adapter_mode,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            max_steps_per_turn=max_steps_per_turn,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
            max_case_seconds=max_task_seconds or 180.0,
            debug_sink=debug_sink.child(
                phase="unit_test",
                skill_name=artifact.name,
            ),
        )
        debug_sink.emit(
            "unit_test_done",
            phase="unit_test",
            input={
                "skill": _artifact_brief(artifact),
                "bundle": _bundle_brief(artifact),
            },
            output=result.as_dict(),
            metrics=copy.deepcopy(result.aggregate or {}),
        )
        return result

    unit_test_results: List[SkillTestResult] = []
    unit_targets = [item for item in evolved.all() if item.name in set(maintenance_targets)]
    unit_sem = asyncio.Semaphore(max(1, int(os.environ.get("BFCL_UNIT_TEST_CONCURRENCY", os.environ.get("BFCL_MAINTENANCE_CONCURRENCY", "2")) or "2")))

    async def guarded_bundle_test(artifact: SkillArtifact) -> SkillTestResult:
        async with unit_sem:
            return await run_bundle_test_for(artifact)

    for artifact, result in zip(unit_targets, await asyncio.gather(*(guarded_bundle_test(item) for item in unit_targets))):
        evolved.add_test_result(result)
        artifact.evidence.integration_failures = copy.deepcopy(result.integration_failures)
        unit_test_results.append(result)
    log_phase("run_unit_utility_tests", n_results=len(unit_test_results))
    refine_store_before = {
        artifact.name: _artifact_full_snapshot(artifact)
        for artifact in evolved.all()
    }
    debug_sink.emit(
        "refiner_start",
        phase="refine",
        input={
            "selected_maintenance_targets": maintenance_targets,
            "maintenance_test_results": [item.as_dict() for item in unit_test_results],
            "store_before": _store_snapshot(evolved),
        },
    )
    refine_decisions = await refine_bfcl_skill_store_llm(
        evolved,
        maintenance_test_results=unit_test_results,
        llm_config=llm_config,
        model_name=model_name,
        audit_context={"phase": "refine", "experiment": tag},
    )
    refine_store_after = {
        artifact.name: _artifact_full_snapshot(artifact)
        for artifact in evolved.all()
    }
    debug_sink.emit(
        "refiner_done",
        phase="refine",
        input={
            "selected_maintenance_targets": maintenance_targets,
            "maintenance_test_results": [summarize_case_metrics(item) for item in unit_test_results],
        },
        output={
            "decisions": refine_decisions,
            "artifact_diffs": [
                _artifact_diff(refine_store_before.get(name, {}), after)
                for name, after in refine_store_after.items()
                if refine_store_before.get(name, {}) != after
            ],
            "store_after": _store_snapshot(evolved),
        },
    )
    debug_sink.emit(
        "store_update",
        phase="refine",
        input={"action": "apply_refine_decisions", "decisions": refine_decisions},
        output={
            "decisions": refine_decisions,
            "store_after": _store_snapshot(evolved),
        },
    )
    log_phase("llm_refine", n_decisions=len(refine_decisions))
    generic_refine_decisions = []
    appended_failure_cases = 0
    for artifact in evolved.all():
        result = next((item for item in unit_test_results if item.skill_name == artifact.name), None)
        if result is None:
            continue
        generic_refine_decisions.append(
            _refine_skill_artifact(
                artifact,
                test_result=result,
            )
        )
        appended_failure_cases += append_failure_cases_from_result(artifact, result)
    if appended_failure_cases:
        debug_sink.emit(
            "integration_cases_appended",
            phase="refine",
            input={"maintenance_test_results": [summarize_case_metrics(item) for item in unit_test_results]},
            output={
                "appended_failure_cases": appended_failure_cases,
                "bundles": [_bundle_brief(artifact) for artifact in evolved.all() if artifact.name in set(maintenance_targets)],
                "store_after": _store_snapshot(evolved),
            },
        )
    has_refine_change = any(
        row.get("action") != "keep"
        for row in refine_decisions
    ) or any(
        bool(row.get("changed"))
        for row in generic_refine_decisions
    ) or appended_failure_cases > 0
    if has_refine_change:
        post_refine_details = [] if skip_integration_replay else await _run_bfcl_baseline(
            train,
            1,
            llm_config,
            tools,
            evolved,
            adapter_mode=adapter_mode,
            model_name=model_name,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            top_k_skills=top_k_skills,
            skill_injection_mode=skill_injection_mode,
            max_steps_per_turn=max_steps_per_turn,
            partial_output=_phase_partial_path(partial_output, "post_refine"),
            max_task_seconds=max_task_seconds,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
            debug_sink=debug_sink.child(phase="post_refine_replay"),
            phase="post_refine_replay",
        )
    else:
        post_refine_details = refine_details
    log_phase("post_refine_replay", changed=has_refine_change)
    refine_summary_after = summarize_bfcl_skill_impact(
        skills=[skill.as_dict() for skill in evolved.all()],
        test_details=post_refine_details,
    )
    if has_refine_change:
        final_train_test_results: List[SkillTestResult] = []
        final_targets = [item for item in evolved.all() if item.name in set(maintenance_targets)]
        for result in await asyncio.gather(*(guarded_bundle_test(item) for item in final_targets)):
            evolved.add_test_result(result)
            final_train_test_results.append(result)
        log_phase("run_final_unit_utility_tests", n_results=len(final_train_test_results), skipped=False)
    else:
        final_train_test_results = unit_test_results
        log_phase("run_final_unit_utility_tests", n_results=len(final_train_test_results), skipped=True)
    micro_refactor_candidates = _micro_refactor_candidates(
        evolved,
        k_step=max(len(train), 1),
    )
    overlap_refactor_enabled = os.environ.get("BFCL_ENABLE_OVERLAP_REFACTOR", "").strip().lower() in {"1", "true", "yes"}
    if overlap_refactor_enabled:
        debug_sink.emit(
            "refactor_overlap_start",
            phase="refactor_overlap",
            input={
                "n_train_details": len(train_details),
                "store_before": _store_snapshot(evolved),
            },
        )
        overlap_refactor_report = await run_bfcl_overlap_refactor_llm(
            evolved,
            train_details=train_details,
            tools=tools,
            llm_config=llm_config,
            model_name=model_name,
            adapter_mode=adapter_mode,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            max_steps_per_turn=max_steps_per_turn,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
            max_case_seconds=max_task_seconds or 180.0,
            max_repair_rounds=int(os.environ.get("BFCL_REFACTOR_MAX_REPAIR_ROUNDS", "1") or "1"),
            debug_sink=debug_sink.child(phase="refactor_overlap"),
            audit_context={"phase": "refactor_overlap", "experiment": tag},
        )
        debug_sink.emit(
            "refactor_overlap_done",
            phase="refactor_overlap",
            output={
                "n_segments": len(overlap_refactor_report.get("segments") or []),
                "n_edges": len((overlap_refactor_report.get("overlap_graph") or {}).get("edges") or []),
                "n_cliques": len(overlap_refactor_report.get("cliques") or []),
                "n_commits": len(overlap_refactor_report.get("commits") or []),
                "n_rejections": len(overlap_refactor_report.get("rejections") or []),
                "store_after": _store_snapshot(evolved),
            },
        )
    else:
        overlap_refactor_report = {
            "enabled": False,
            "reason": "Set BFCL_ENABLE_OVERLAP_REFACTOR=1 to run overlap refactor.",
        }
    if save_skills:
        evolved.save(save_skills)

    skip_final_test = os.environ.get("BFCL_EVOLVE_SKIP_FINAL_TEST", "").strip().lower() in {"1", "true", "yes"}
    if skip_final_test:
        test_details = []
        debug_sink.emit(
            "experiment_phase_done",
            phase="final_test_rollout",
            output={"skipped": True, "reason": "BFCL_EVOLVE_SKIP_FINAL_TEST enabled"},
        )
    else:
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
            tool_api_style=tool_api_style,
            top_k_skills=top_k_skills,
            skill_injection_mode=skill_injection_mode,
            max_steps_per_turn=max_steps_per_turn,
            partial_output=_phase_partial_path(partial_output, "test"),
            max_task_seconds=max_task_seconds,
            temperature=temperature,
            synthetic_continue=synthetic_continue,
            explicit_skill_tool=explicit_skill_tool,
            debug_sink=debug_sink.child(phase="final_test_rollout"),
            phase="final_test_rollout",
        )
        debug_sink.emit(
            "experiment_phase_done",
            phase="final_test_rollout",
            output={
                "skipped": False,
                "n_details": len(test_details),
                "task_ids": [item.get("task_id") for item in test_details],
                "store_after": _store_snapshot(evolved),
            },
        )
    log_phase("final_test_rollout", n_test_details=len(test_details))
    train_summary = _aggregate("bfcl_v3", "evolve_train", tag, llm_config, len(train), train_details)
    test_summary = _aggregate("bfcl_v3", "evolve_test", tag, llm_config, len(train), test_details)
    evolved_skill_rows = summarize_bfcl_skill_impact(
        skills=[skill.as_dict() for skill in evolved.all()],
        test_details=test_details,
    )
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
        "bfcl_tool_api_style": tool_api_style,
        "top_k_skills": top_k_skills,
        "skill_injection_mode": skill_injection_mode,
        "max_steps_per_turn": max_steps_per_turn,
        "temperature": temperature,
        "bfcl_synthetic_continue": synthetic_continue,
        "bfcl_explicit_skill_tool": explicit_skill_tool,
        "max_task_seconds": max_task_seconds,
        "partial_output": str(partial_output) if partial_output else None,
        "n_train": len(train),
        "n_test": len(test),
        "n_train_runs": n_train_runs,
        "load_train_details": str(load_train_details) if load_train_details else None,
        "n_test_runs": n_test_runs,
        "n_skills_seed": len(seed_store.all()),
        "n_skills_evolved": len(evolved.all()),
        "n_skills_refine_input": len(refine_input_skill_names),
        "skill_names": [skill.name for skill in evolved.all()],
        "skills": [skill.as_dict() for skill in evolved.all()],
        "skill_bundles": {
            skill.name: skill.bundle.as_dict()
            for skill in evolved.all()
        },
        "skill_impact_summary": evolved_skill_rows,
        "refine_summary_before": {k: v for k, v in refine_summary_before.items() if k != "details"},
        "refine_skill_impact": refine_summary_after,
        "refine_decisions": refine_decisions,
        "refine_generic_decisions": generic_refine_decisions,
        "maintenance_test_results": [item.as_dict() for item in unit_test_results],
        "final_maintenance_test_results": [item.as_dict() for item in final_train_test_results],
        "integration_cases_appended": appended_failure_cases,
        "micro_refactor_candidates": micro_refactor_candidates,
        "overlap_refactor": overlap_refactor_report,
        "train_summary": {k: v for k, v in train_summary.items() if k != "details"},
        "test_summary": {k: v for k, v in test_summary.items() if k != "details"},
        "train_details": train_details,
        "refine_details": refine_details,
        "post_refine_details": post_refine_details,
        "test_details": test_details,
        "debug_events": _safe_debug_events(debug_sink),
        "debug_event_count": len(_safe_debug_events(debug_sink)),
    }


def _load_artifact_store(args: argparse.Namespace) -> ArtifactStore:
    base = ArtifactStore.load(args.skills) if args.skills else ArtifactStore()
    if args.use_handwritten_skills:
        for artifact in default_bfcl_skill_store().all():
            base.add(artifact)
    return base


def _load_task_id_list(path: Path | None) -> List[str]:
    if path is None:
        return []
    raw = json.loads(path.read_text())
    if isinstance(raw, dict):
        ids = raw.get("task_ids", [])
    else:
        ids = raw
    return [str(item).strip() for item in ids if str(item).strip()]


def _select_tasks_by_id(tasks: List[Any], path: Path) -> List[Any]:
    ordered_ids = _load_task_id_list(path)
    task_map = {str(task.task_id): task for task in tasks}
    missing = [task_id for task_id in ordered_ids if task_id not in task_map]
    if missing:
        raise ValueError(f"Unknown task ids in {path}: {missing}")
    return [task_map[task_id] for task_id in ordered_ids]


def _phase_partial_path(path: Path | None, phase: str) -> Path | None:
    if path is None:
        return None
    return path.with_name(f"{path.stem}_{phase}{path.suffix or '.json'}")


def _limit_maintenance_targets(targets: List[str]) -> List[str]:
    limit = int(os.environ.get("BFCL_MAX_MAINTENANCE_TARGETS", "0") or "0")
    if limit <= 0:
        return targets
    return list(targets)[:limit]


def _load_saved_details(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        details = payload.get("details", [])
    else:
        details = payload
    if not isinstance(details, list):
        raise ValueError(f"Malformed details payload in {path}")
    return [item for item in details if isinstance(item, dict) and item.get("task_id")]


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


def _pass_bool_metric(details: List[Dict[str, Any]], metric_name: str) -> float | None:
    if not details:
        return None
    passes = 0
    counted = 0
    for item in details:
        runs = item.get("runs", []) or []
        metric_vals = [
            (run.get("metrics") or {}).get(metric_name)
            for run in runs
            if (run.get("metrics") or {}).get(metric_name) is not None
        ]
        if not metric_vals:
            continue
        counted += 1
        if any(value is True for value in metric_vals):
            passes += 1
    if counted == 0:
        return None
    return round(passes / counted, 4)


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
    from academic.benchmarks.core.types import BenchmarkResult

    return BenchmarkResult(
        benchmark=data.get("benchmark", "bfcl_v3"),
        task_id=data.get("task_id", ""),
        success=bool(data.get("success")),
        score=float(data.get("score", 0.0)),
        metrics=data.get("metrics") or {},
        trace=data.get("trace") or {},
        error=data.get("error"),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skill_case_id(skill_name: str, label: str, index: int) -> str:
    return f"{skill_name}:{label}:{index}"


def _first_run(item: Dict[str, Any]) -> Dict[str, Any]:
    runs = item.get("runs", []) or []
    return runs[0] if runs else {}


def _official_valid(run: Dict[str, Any]) -> bool | None:
    return (run.get("metrics") or {}).get("official_valid")


def _metrics_int(run: Dict[str, Any], key: str) -> int:
    value = (run.get("metrics") or {}).get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def _skill_matches_run(skill: SkillArtifact, run: Dict[str, Any]) -> bool:
    metrics = run.get("metrics") or {}
    sets = [
        metrics.get("retrieved_skills", []) or [],
        metrics.get("prompt_injected_skills", []) or [],
        metrics.get("tool_injected_skills", []) or [],
        metrics.get("called_skill_tools", []) or [],
        metrics.get("used_skills", []) or [],
    ]
    return any(skill.name in values for values in sets)


def _result_signature(run: Dict[str, Any]) -> Dict[str, Any]:
    metrics = run.get("metrics") or {}
    return {
        "official_valid": metrics.get("official_valid"),
        "call_f1": metrics.get("call_f1"),
        "n_model_steps": metrics.get("n_model_steps"),
        "total_tokens": metrics.get("total_tokens"),
        "retrieved_skills": metrics.get("retrieved_skills", []) or [],
        "prompt_injected_skills": metrics.get("prompt_injected_skills", []) or [],
        "tool_injected_skills": metrics.get("tool_injected_skills", []) or [],
        "called_skill_tools": metrics.get("called_skill_tools", []) or [],
        "used_skills": metrics.get("used_skills", []) or [],
        "call_errors": metrics.get("call_errors", []) or [],
    }


def _ensure_artifact_maintenance_fields(artifact: SkillArtifact) -> SkillArtifact:
    if not artifact.interface.summary:
        artifact.interface = SkillInterface(
            summary=artifact.description,
            usage=f"Use `{artifact.name}` when its described BFCL pattern applies.",
            invocation_contract={
                "injection_type": artifact.injection_type(),
                "allowed_tools": list(artifact.metadata.get("allowed_tools") or []),
                "domains": list(artifact.metadata.get("domains") or []),
            },
        )
    if not artifact.bundle.bundle_id:
        artifact.bundle.bundle_id = f"{artifact.name}.bundle"
    if not artifact.lineage.version_kind or artifact.lineage.version_kind == "seed":
        artifact.lineage = SkillLineage(
            parent_version=artifact.lineage.parent_version,
            parent_version_id=artifact.lineage.parent_version_id,
            version_kind=str(artifact.metadata.get("version_kind") or artifact.lineage.version_kind or "seed"),
            migration_reason=artifact.lineage.migration_reason,
            refined_from_result_ids=list(artifact.lineage.refined_from_result_ids or []),
            refactor_group_id=artifact.lineage.refactor_group_id,
        )
    artifact.metadata.setdefault("version_kind", artifact.lineage.version_kind or "seed")
    return artifact


def _build_bfcl_skill_bundles(
    store: ArtifactStore,
    *,
    train_details: List[Dict[str, Any]],
    replay_details: List[Dict[str, Any]] | None = None,
) -> None:
    train_by_id = {str(item.get("task_id")): item for item in train_details}
    replay_by_id = {str(item.get("task_id")): item for item in (replay_details or [])}
    for artifact in store.all():
        _ensure_artifact_maintenance_fields(artifact)
        source_task_ids = [
            str(item).strip()
            for item in (artifact.metadata.get("source_task_ids") or [])
            if str(item).strip()
        ]
        positive_cases: List[SkillBundleCase] = []
        negative_cases: List[SkillBundleCase] = []
        integration_cases: List[SkillBundleCase] = list(artifact.bundle.integration_cases or [])
        seen_case_ids = {case.case_id for case in integration_cases}
        for index, task_id in enumerate(source_task_ids):
            detail = train_by_id.get(task_id)
            if not detail:
                continue
            run = _first_run(detail)
            query = f"BFCL task {task_id} for skill {artifact.name}"
            case = SkillBundleCase(
                case_id=_skill_case_id(artifact.name, "positive", index),
                source="train_positive",
                prompt=query,
                expected=_result_signature(run),
                context={
                    "task_id": task_id,
                    "run": copy.deepcopy(run),
                    "artifact_name": artifact.name,
                },
                tags=["positive", "source-train"],
                polarity="positive",
            )
            positive_cases.append(case)
            replay = replay_by_id.get(task_id)
            if replay:
                replay_run = _first_run(replay)
                if _skill_matches_run(artifact, replay_run) and _official_valid(replay_run) is False:
                    case_id = _skill_case_id(artifact.name, "integration", len(integration_cases))
                    if case_id in seen_case_ids:
                        continue
                    integration_cases.append(
                        SkillBundleCase(
                            case_id=case_id,
                            source="integration_replay_failure",
                            prompt=query,
                            expected=_result_signature(replay_run),
                            context={
                                "task_id": task_id,
                                "baseline_run": copy.deepcopy(run),
                                "replay_run": copy.deepcopy(replay_run),
                                "artifact_name": artifact.name,
                            },
                            tags=["integration-derived", "failure"],
                            polarity="negative",
                        )
                    )
                    seen_case_ids.add(case_id)
        for task_id, replay in replay_by_id.items():
            should_seed_failure_case = bool(
                artifact.metadata.get("manual_fault_injected")
                or artifact.history
                or task_id in source_task_ids
            )
            if not should_seed_failure_case:
                continue
            replay_run = _first_run(replay)
            if not _skill_matches_run(artifact, replay_run):
                continue
            if _official_valid(replay_run) is not False:
                continue
            case_id = f"{artifact.name}:integration:{task_id}"
            if case_id in seen_case_ids:
                continue
            baseline_detail = train_by_id.get(task_id) or {}
            tags = ["integration-derived", "failure"]
            source = "integration_replay_failure"
            if artifact.metadata.get("manual_fault_injected"):
                tags.append("manual-fault")
                source = "manual_fault_replay_failure"
            integration_cases.append(
                SkillBundleCase(
                    case_id=case_id,
                    source=source,
                    prompt=f"BFCL task {task_id} for skill {artifact.name}",
                    expected=_result_signature(replay_run),
                    context={
                        "task_id": task_id,
                        "baseline_run": copy.deepcopy(_first_run(baseline_detail)),
                        "replay_run": copy.deepcopy(replay_run),
                        "artifact_name": artifact.name,
                    },
                    tags=tags,
                    polarity="negative",
                )
            )
            seen_case_ids.add(case_id)
        if not positive_cases:
            positive_cases.append(
                SkillBundleCase(
                    case_id=_skill_case_id(artifact.name, "positive", 0),
                    source="artifact_definition",
                    prompt=artifact.description,
                    expected={"injection_type": artifact.injection_type()},
                    context={"artifact_name": artifact.name},
                    tags=["bootstrap"],
                    polarity="positive",
                )
            )
        if artifact.metadata.get("source") == "evolve_rollouts":
            negative_cases.append(
                SkillBundleCase(
                    case_id=_skill_case_id(artifact.name, "negative", 0),
                    source="regression_guard",
                    prompt=f"Guardrail for {artifact.name}",
                    expected={"should_not_regress": True},
                    context={"artifact_name": artifact.name},
                    tags=["negative", "guardrail"],
                    polarity="negative",
                )
            )
        artifact.bundle.positive_cases = positive_cases
        artifact.bundle.negative_cases = negative_cases
        artifact.bundle.integration_cases = integration_cases
        artifact.bundle.fixtures = {
            "source_task_ids": source_task_ids,
            "bundle_generated_at": _now_iso(),
        }


def _build_skill_test_result(
    artifact: SkillArtifact,
    *,
    train_details: List[Dict[str, Any]],
    replay_details: List[Dict[str, Any]],
    run_label: str,
) -> SkillTestResult:
    baseline_valid = _task_official_valid_map(train_details)
    replay_valid = _task_official_valid_map(replay_details)
    cases = artifact.bundle.all_cases()
    case_runs: List[SkillTestCaseRun] = []
    comparable_case_count = 0
    improved = 0
    regressed = 0
    tokens_delta = 0
    steps_delta = 0
    relevant_task_ids: List[str] = []
    for case in cases:
        task_id = str(case.context.get("task_id", "")).strip()
        if not task_id:
            case_runs.append(
                SkillTestCaseRun(
                    case_id=case.case_id,
                    variant="bundle_only",
                    passed=True,
                    trace_ref="",
                    metadata={"polarity": case.polarity, "source": case.source},
                )
            )
            continue
        if task_id not in relevant_task_ids:
            relevant_task_ids.append(task_id)
        before_valid = baseline_valid.get(task_id)
        after_valid = replay_valid.get(task_id)
        baseline_run = case.context.get("baseline_run")
        if baseline_run is None:
            baseline_detail = next((item for item in train_details if str(item.get("task_id")) == task_id), None)
            baseline_run = _first_run(baseline_detail or {})
        replay_run = case.context.get("replay_run")
        if replay_run is None:
            replay_detail = next((item for item in replay_details if str(item.get("task_id")) == task_id), None)
            replay_run = _first_run(replay_detail or {})
        if not baseline_run and not replay_run and before_valid is None and after_valid is None:
            case_runs.append(
                SkillTestCaseRun(
                    case_id=case.case_id,
                    variant="bundle_only",
                    passed=True,
                    trace_ref=task_id,
                    metadata={"polarity": case.polarity, "source": case.source},
                )
            )
            continue
        comparable_case_count += 1
        before_tokens = _metrics_int(baseline_run or {}, "total_tokens")
        after_tokens = _metrics_int(replay_run or {}, "total_tokens")
        before_steps = _metrics_int(baseline_run or {}, "n_model_steps")
        after_steps = _metrics_int(replay_run or {}, "n_model_steps")
        accuracy_before = 1.0 if before_valid is True else 0.0
        accuracy_after = 1.0 if after_valid is True else 0.0
        if accuracy_after > accuracy_before:
            improved += 1
        if accuracy_after < accuracy_before:
            regressed += 1
        tokens_delta += after_tokens - before_tokens
        steps_delta += after_steps - before_steps
        case_runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="without_skill",
                passed=before_valid is True,
                accuracy=accuracy_before,
                validity=before_valid,
                tokens=before_tokens,
                steps=before_steps,
                trace_ref=task_id,
                metadata={"polarity": case.polarity},
            )
        )
        case_runs.append(
            SkillTestCaseRun(
                case_id=case.case_id,
                variant="with_skill",
                passed=after_valid is True,
                accuracy=accuracy_after,
                validity=after_valid,
                tokens=after_tokens,
                steps=after_steps,
                failure_summary="" if after_valid is True else str((replay_run or {}).get("error") or ""),
                trace_ref=task_id,
                metadata={"polarity": case.polarity},
            )
        )
    total_cases = max(comparable_case_count, 1)
    aggregate = {
        "n_cases": len(cases),
        "n_comparable_cases": comparable_case_count,
        "n_improved": improved,
        "n_regressed": regressed,
        "pass_all_tests": regressed == 0,
        "unit_utility_report": {
            "delta_accuracy": round((improved - regressed) / total_cases, 4),
            "delta_tokens": tokens_delta,
            "delta_steps": steps_delta,
        },
    }
    counterfactual = {
        "without_skill_valid_by_task": {
            task_id: baseline_valid.get(task_id)
            for task_id in relevant_task_ids
        },
        "with_skill_valid_by_task": {
            task_id: replay_valid.get(task_id)
            for task_id in relevant_task_ids
        },
    }
    integration_failures = _integration_failures_for_skill(artifact, replay_details)
    return SkillTestResult(
        result_id=f"{artifact.name}:{run_label}:{uuid4().hex[:8]}",
        skill_name=artifact.name,
        skill_version=artifact.version,
        bundle_id=artifact.bundle.bundle_id,
        bundle_version=artifact.bundle.bundle_version,
        dependency_versions=artifact.dependency_version_map(),
        run_label=run_label,
        unit_case_runs=case_runs,
        aggregate=aggregate,
        counterfactual=counterfactual,
        integration_failures=integration_failures,
        created_at=_now_iso(),
    )


def _integration_failures_for_skill(
    artifact: SkillArtifact,
    replay_details: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for item in replay_details:
        run = _first_run(item)
        if not _skill_matches_run(artifact, run):
            continue
        if _official_valid(run) is not False:
            continue
        failures.append(
            {
                "task_id": item.get("task_id"),
                "metrics": copy.deepcopy(run.get("metrics") or {}),
                "error": run.get("error"),
            }
        )
    return failures


def _append_failure_cases_from_test_result(
    artifact: SkillArtifact,
    test_result: SkillTestResult,
) -> int:
    added = 0
    existing = {case.case_id for case in artifact.bundle.integration_cases}
    for failure in test_result.integration_failures:
        task_id = str(failure.get("task_id", "")).strip()
        if not task_id:
            continue
        case_id = f"{artifact.name}:integration:{task_id}"
        if case_id in existing:
            continue
        artifact.bundle.integration_cases.append(
            SkillBundleCase(
                case_id=case_id,
                source="integration_failure",
                prompt=f"Integration failure from task {task_id}",
                expected={"official_valid": False},
                context={"task_id": task_id, "failure": failure},
                tags=["integration-derived", "failure"],
                polarity="negative",
            )
        )
        existing.add(case_id)
        added += 1
    if added:
        artifact.bundle.bundle_version += 1
    return added


def _validate_refine_output_consistency(artifact: SkillArtifact) -> None:
    if not artifact.bundle.bundle_id:
        raise ValueError(f"{artifact.name} missing bundle_id")
    for case in artifact.bundle.all_cases():
        fragment = dict((case.context or {}).get("task_fragment") or {})
        if not fragment:
            continue
        source_task_id = str((case.context or {}).get("source_task_id") or "").strip()
        task_id = str(
            fragment.get("task_id")
            or (case.context or {}).get("task_id")
            or source_task_id
            or ""
        ).strip()
        if task_id.startswith("task_from_trace") or source_task_id.startswith("task_from_trace"):
            raise ValueError(
                f"{artifact.name} bundle case {case.case_id} uses non-replayable synthetic task id"
            )
        has_initial_config = bool(dict(fragment.get("input_artifacts") or {}).get("initial_config"))
        has_involved_classes = bool(dict(fragment.get("metadata") or {}).get("involved_classes"))
        if fragment.get("question") and fragment.get("expected") is not None:
            question = _normalize_fragment_question(fragment.get("question"))
            expected = _normalize_fragment_expected(fragment.get("expected"))
            if not source_task_id:
                raise ValueError(
                    f"{artifact.name} bundle case {case.case_id} missing source_task_id for BFCL replay"
                )
            invalid_bundle = _validate_bundle_task_fragment(
                task_id=task_id,
                source_task_id=source_task_id,
                question=question,
                expected=expected,
            )
            if invalid_bundle:
                raise ValueError(
                    f"{artifact.name} bundle case {case.case_id} has invalid BFCL replay fragment: "
                    f"{invalid_bundle.get('reason')}"
                )
            source_task = _source_task_snapshot(source_task_id or task_id)
            invalid_schema = _validate_expected_call_schema(
                expected,
                tool_schemas=_tool_schemas_for_source_task(source_task),
            )
            if invalid_schema:
                raise ValueError(
                    f"{artifact.name} bundle case {case.case_id} has invalid BFCL expected call schema: "
                    f"{invalid_schema.get('reason')}"
                )
            if not has_initial_config or not has_involved_classes:
                raise ValueError(
                    f"{artifact.name} bundle case {case.case_id} missing official BFCL execution context"
                )
    if artifact.version_kind() == "minor":
        parent_tests = [
            entry.get("bundle")
            for entry in artifact.history[-1:]
            if isinstance(entry, dict) and entry.get("bundle")
        ]
        if parent_tests:
            prev_bundle = parent_tests[-1]
            prev_cases = []
            for key in ("positive_cases", "negative_cases", "integration_cases"):
                prev_cases.extend(case.get("case_id") for case in (prev_bundle.get(key) or []))
            current_cases = {case.case_id for case in artifact.bundle.all_cases()}
            missing = [case_id for case_id in prev_cases if case_id not in current_cases]
            if missing:
                raise ValueError(
                    f"{artifact.name} minor update removed existing tests: {missing[:5]}"
                )
    if artifact.lineage.version_kind in {"major", "minor"}:
        if not artifact.interface.summary:
            raise ValueError(f"{artifact.name} updated interface without interface summary")


def _refine_skill_artifact(
    artifact: SkillArtifact,
    *,
    test_result: SkillTestResult,
    max_rounds: int = 2,
) -> Dict[str, Any]:
    action = "keep"
    notes: List[str] = []
    changed = False
    for _ in range(max_rounds):
        if test_result.aggregate.get("pass_all_tests"):
            break
        if test_result.aggregate.get("n_regressed", 0) > 0 and artifact.metadata.get("source") == "evolve_rollouts":
            artifact.status = "disabled"
            artifact.metadata["disabled"] = True
            artifact.metadata["disabled_reason"] = "Refine failed unit utility regression guard."
            action = "disable"
            changed = True
            notes.append("disabled_on_regression")
            break
        if artifact.stale:
            artifact.status = "active"
            artifact.stale = False
            action = "refresh_stale"
            changed = True
            notes.append("cleared_stale_after_refine")
        if artifact.lineage.version_kind in {"major", "minor"} and not artifact.interface.summary:
            artifact.interface.summary = artifact.description
            changed = True
            notes.append("filled_interface_summary")
        break
    _validate_refine_output_consistency(artifact)
    return {
        "skill_name": artifact.name,
        "action": action,
        "changed": changed,
        "notes": notes,
        "pass_all_tests": bool(test_result.aggregate.get("pass_all_tests")),
    }


def _build_maintenance_test_results(
    store: ArtifactStore,
    *,
    train_details: List[Dict[str, Any]],
    replay_details: List[Dict[str, Any]],
    run_label: str,
) -> List[SkillTestResult]:
    results: List[SkillTestResult] = []
    for artifact in store.all():
        test_result = _build_skill_test_result(
            artifact,
            train_details=train_details,
            replay_details=replay_details,
            run_label=run_label,
        )
        store.add_test_result(test_result)
        artifact.evidence.integration_failures = copy.deepcopy(test_result.integration_failures)
        results.append(test_result)
    return results


def _micro_refactor_candidates(
    store: ArtifactStore,
    *,
    k_step: int,
) -> List[Dict[str, Any]]:
    artifacts = store.all()
    if len(artifacts) < 2 or len(artifacts) % max(k_step, 1) != 0:
        return []
    by_phrase: Dict[str, List[str]] = {}
    for artifact in artifacts:
        normalized = " ".join(artifact.description.lower().split())
        if len(normalized) < 24:
            continue
        by_phrase.setdefault(normalized, []).append(artifact.name)
    out = []
    for phrase, names in by_phrase.items():
        if len(names) < 2:
            continue
        out.append(
            {
                "shared_fragment": phrase,
                "affected_skills": sorted(names),
                "reason": "repeated description fragment",
            }
        )
    return out


def summarize_bfcl_skill_impact(
    *,
    skills: List[Dict[str, Any]],
    test_details: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    skill_rows: Dict[str, Dict[str, Any]] = {}
    for item in skills:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        metadata = item.get("metadata") or {}
        skill_rows[name] = {
            "skill_name": name,
            "kind": item.get("kind"),
            "description": item.get("description"),
            "injection_type": metadata.get("injection_type"),
            "source": metadata.get("source"),
            "source_task_ids": list(metadata.get("source_task_ids") or []),
            "source_tool": metadata.get("tool"),
            "source_allowed_tools": list(metadata.get("allowed_tools") or []),
            "source_domains": list(metadata.get("domains") or []),
            "source_examples": list(metadata.get("source_examples") or []),
            "retrieved_test_count": 0,
            "prompt_injected_test_count": 0,
            "tool_injected_test_count": 0,
            "called_skill_tool_test_count": 0,
            "used_test_count": 0,
            "helped_official_task_ids": [],
            "failed_after_injection_task_ids": [],
            "retrieved_test_task_ids": [],
        }
    for item in test_details:
        task_id = item.get("task_id", "")
        runs = item.get("runs", []) or []
        for run in runs:
            metrics = run.get("metrics") or {}
            official_valid = metrics.get("official_valid")
            retrieved = set(metrics.get("retrieved_skills", []) or [])
            prompt_injected = set(metrics.get("prompt_injected_skills", []) or [])
            tool_injected = set(metrics.get("tool_injected_skills", []) or [])
            called_skill_tools = set(metrics.get("called_skill_tools", []) or [])
            used = set(metrics.get("used_skills", []) or [])
            mentioned = retrieved | prompt_injected | tool_injected | called_skill_tools | used
            for skill_name in mentioned:
                row = skill_rows.setdefault(
                    skill_name,
                    {
                        "skill_name": skill_name,
                        "kind": None,
                        "description": None,
                        "injection_type": None,
                        "source": None,
                        "source_task_ids": [],
                        "source_tool": None,
                        "source_allowed_tools": [],
                        "source_domains": [],
                        "source_examples": [],
                        "retrieved_test_count": 0,
                        "prompt_injected_test_count": 0,
                        "tool_injected_test_count": 0,
                        "called_skill_tool_test_count": 0,
                        "used_test_count": 0,
                        "helped_official_task_ids": [],
                        "failed_after_injection_task_ids": [],
                        "retrieved_test_task_ids": [],
                    },
                )
                if skill_name in retrieved:
                    row["retrieved_test_count"] += 1
                    if task_id and task_id not in row["retrieved_test_task_ids"]:
                        row["retrieved_test_task_ids"].append(task_id)
                if skill_name in prompt_injected:
                    row["prompt_injected_test_count"] += 1
                if skill_name in tool_injected:
                    row["tool_injected_test_count"] += 1
                if skill_name in called_skill_tools:
                    row["called_skill_tool_test_count"] += 1
                if skill_name in used:
                    row["used_test_count"] += 1
                if official_valid is True and skill_name in (prompt_injected | tool_injected | used):
                    if task_id and task_id not in row["helped_official_task_ids"]:
                        row["helped_official_task_ids"].append(task_id)
                if official_valid is False and skill_name in (prompt_injected | tool_injected | used):
                    if task_id and task_id not in row["failed_after_injection_task_ids"]:
                        row["failed_after_injection_task_ids"].append(task_id)
    rows = list(skill_rows.values())
    rows.sort(
        key=lambda row: (
            -int(row.get("retrieved_test_count", 0)),
            -int(row.get("prompt_injected_test_count", 0)),
            row.get("skill_name", ""),
        )
    )
    return rows


def _task_official_valid_map(details: List[Dict[str, Any]]) -> Dict[str, bool | None]:
    out: Dict[str, bool | None] = {}
    for item in details:
        task_id = str(item.get("task_id", "")).strip()
        if not task_id:
            continue
        runs = item.get("runs", []) or []
        if not runs:
            out[task_id] = None
            continue
        metrics = runs[0].get("metrics") or {}
        out[task_id] = metrics.get("official_valid")
    return out


def _refine_bfcl_skill_store(
    store: ArtifactStore,
    *,
    train_details: List[Dict[str, Any]],
    replay_details: List[Dict[str, Any]],
    maintenance_test_results: List[SkillTestResult] | None = None,
    min_fail_count: int = 2,
) -> List[Dict[str, Any]]:
    replay_rows = summarize_bfcl_skill_impact(
        skills=[skill.as_dict() for skill in store.all()],
        test_details=replay_details,
    )
    row_by_skill = {str(row.get("skill_name", "")): row for row in replay_rows}
    result_by_skill = {
        item.skill_name: item
        for item in (maintenance_test_results or [])
    }
    decisions: List[Dict[str, Any]] = []
    for artifact in store.all():
        row = row_by_skill.get(artifact.name, {})
        retrieved = int(row.get("retrieved_test_count", 0) or 0)
        test_result = result_by_skill.get(artifact.name)
        if test_result is not None:
            without_skill = {
                str(task_id).strip(): value
                for task_id, value in (test_result.counterfactual.get("without_skill_valid_by_task") or {}).items()
                if str(task_id).strip()
            }
            with_skill = {
                str(task_id).strip(): value
                for task_id, value in (test_result.counterfactual.get("with_skill_valid_by_task") or {}).items()
                if str(task_id).strip()
            }
            evidence_task_ids = sorted(set(without_skill) | set(with_skill))
            helped = {
                task_id
                for task_id in evidence_task_ids
                if with_skill.get(task_id) is True and without_skill.get(task_id) is not True
            }
            failed = {
                task_id
                for task_id in evidence_task_ids
                if with_skill.get(task_id) is False
            }
            regressions = sorted(
                task_id
                for task_id in evidence_task_ids
                if without_skill.get(task_id) is True and with_skill.get(task_id) is False
            )
        else:
            helped = {
                str(task_id).strip()
                for task_id in row.get("helped_official_task_ids", []) or []
                if str(task_id).strip()
            }
            failed = {
                str(task_id).strip()
                for task_id in row.get("failed_after_injection_task_ids", []) or []
                if str(task_id).strip()
            }
            regressions = sorted(
                task_id
                for task_id in failed
                if task_id in {
                    str(task_id).strip()
                    for task_id in row.get("failed_after_injection_task_ids", []) or []
                    if str(task_id).strip()
                }
            )
            evidence_task_ids = sorted(set(helped) | set(failed))
        has_counterfactual_evidence = bool(test_result is not None and evidence_task_ids)
        decision = {
            "skill_name": artifact.name,
            "version_before": artifact.version,
            "retrieved_test_count": retrieved,
            "helped_count": len(helped),
            "failed_count": len(failed),
            "regression_task_ids": regressions,
            "counterfactual_task_ids": evidence_task_ids,
            "used_counterfactual_evidence": has_counterfactual_evidence,
            "disabled_before": artifact.is_disabled(),
            "disabled_after": artifact.is_disabled(),
            "action": "keep",
        }
        should_rollback = (
            artifact.history
            and len(helped) == 0
            and (
                len(regressions) >= 1
                or (
                    len(failed) >= min_fail_count
                    and (has_counterfactual_evidence or retrieved >= min_fail_count)
                )
            )
        )
        if should_rollback:
            rolled_back = store.rollback(artifact.name)
            repaired = next((skill for skill in store.all() if skill.name == artifact.name), None)
            if rolled_back and repaired is not None:
                repaired.metadata["rollback_reason"] = (
                    "Rolled back after train replay refine detected a regressed or repeatedly harmful version."
                )
                repaired.metadata["rollback_task_ids"] = sorted(set(regressions) | set(failed))
                decision["disabled_after"] = repaired.is_disabled()
                decision["version_after"] = repaired.version
                decision["action"] = "rollback_on_regression" if regressions else "rollback_on_no_help"
                decisions.append(decision)
                continue
        if len(regressions) >= 1 and len(helped) == 0 and (has_counterfactual_evidence or artifact.metadata.get("source") == "evolve_rollouts"):
            artifact.status = "disabled"
            artifact.metadata["disabled"] = True
            artifact.metadata["disabled_reason"] = (
                "Disabled after train replay refine: with/without-skill evidence showed "
                "official-valid regressions with no observed helped cases."
            )
            artifact.metadata["disabled_task_ids"] = regressions
            decision["disabled_after"] = True
            decision["action"] = "disable_on_regression"
        elif len(helped) == 0 and len(failed) >= min_fail_count and (has_counterfactual_evidence or artifact.metadata.get("source") == "evolve_rollouts" or retrieved >= min_fail_count):
            artifact.status = "disabled"
            artifact.metadata["disabled"] = True
            artifact.metadata["disabled_reason"] = (
                "Disabled after train replay refine: repeatedly failed with no observed "
                "helped cases in maintenance replay."
            )
            artifact.metadata["disabled_task_ids"] = sorted(failed)
            decision["disabled_after"] = True
            decision["action"] = "disable_on_no_help"
        decision["version_after"] = next(
            (skill.version for skill in store.all() if skill.name == artifact.name),
            artifact.version,
        )
        decisions.append(decision)
    decisions.sort(key=lambda item: (item["action"] != "keep", -len(item["regression_task_ids"]), item["skill_name"]), reverse=True)
    return decisions


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
