"""Targeted BFCL maintenance experiments for evolve/refine debugging.

Experiments supported:
1. Repeat a single hard task from scratch and inspect whether skills evolve.
2. Inject a broken version of a previously useful skill and check whether
   refine rolls it back or disables it.
3. Run a related sequence of tasks, optionally with bad-skill injection, to
   inspect whether evolve/refine remains coherent across rounds.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
from typing import Any, Dict, List

from academic.benchmarks.artifacts import ArtifactStore
from academic.benchmarks.bfcl import load_bfcl_tasks, load_bfcl_tools
from academic.benchmarks.bfcl_llm_maintenance import (
    build_bfcl_skill_bundles_llm,
    execute_bfcl_bundle_tests,
    extract_bfcl_skill_artifacts_llm,
    refine_bfcl_skill_store_llm,
    select_bfcl_maintenance_targets,
)
from academic.benchmarks.bfcl_skills import default_bfcl_skill_store
from academic.benchmarks.run import (
    _aggregate,
    _result_from_dict,
    _run_bfcl_baseline,
)
from academic.benchmarks.types import SkillArtifact
from academic.config import RESULTS_DIR


def _load_all_tasks() -> Dict[str, Any]:
    train, test = load_bfcl_tasks(
        cache_dir=Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        split_seed=42,
        n_train=50,
        n_test=150,
        data_source="bfcl_eval_bundle",
    )
    return {task.task_id: task for task in train + test}


def _select_tasks(task_ids: List[str]) -> List[Any]:
    all_tasks = _load_all_tasks()
    missing = [task_id for task_id in task_ids if task_id not in all_tasks]
    if missing:
        raise ValueError(f"Unknown task ids: {missing}")
    return [all_tasks[task_id] for task_id in task_ids]


def _seed_store(use_handwritten: bool = False) -> ArtifactStore:
    store = ArtifactStore()
    if use_handwritten:
        for artifact in default_bfcl_skill_store().all():
            store.add(copy.deepcopy(artifact))
    return store


def _inject_broken_skill_version(store: ArtifactStore, skill_name: str, broken_body: str) -> Dict[str, Any]:
    target = next((skill for skill in store.all() if skill.name == skill_name), None)
    if target is None:
        raise ValueError(f"Skill not found for injection: {skill_name}")
    broken = SkillArtifact(
        name=target.name,
        kind=target.kind,
        description=target.description + " [BROKEN-INJECTED]",
        body=broken_body,
        metadata=copy.deepcopy(target.metadata),
        interface=copy.deepcopy(target.interface),
        bundle=copy.deepcopy(target.bundle),
        evidence=copy.deepcopy(target.evidence),
        status=target.status,
        lineage=copy.deepcopy(target.lineage),
        dependency_pins=copy.deepcopy(target.dependency_pins),
        dependencies=list(target.dependencies),
    )
    broken.metadata["manual_fault_injected"] = True
    store.add(broken)
    return {
        "skill_name": skill_name,
        "version_after_injection": broken.version,
        "broken_body": broken_body,
    }


async def _single_round_evolve(
    *,
    task_ids: List[str],
    llm_config: str,
    model_name: str | None,
    store: ArtifactStore,
    use_skills_in_replay: bool,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_task_seconds: float | None,
) -> Dict[str, Any]:
    tools = load_bfcl_tools(
        Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"),
        data_source="bfcl_eval_bundle",
    )
    tasks = _select_tasks(task_ids)
    print(json.dumps({"progress": "lab_train_start", "task_ids": task_ids}, ensure_ascii=False), flush=True)
    train_details = await _run_bfcl_baseline(
        tasks,
        1,
        llm_config,
        tools,
        _seed_store(False),
        adapter_mode="official",
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        top_k_skills=2,
        skill_injection_mode="none",
        max_steps_per_turn=20,
        max_task_seconds=max_task_seconds,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
    )
    print(json.dumps({"progress": "lab_train_done", "task_ids": task_ids}, ensure_ascii=False), flush=True)
    train_results = [
        _result_from_dict(run)
        for item in train_details
        for run in item.get("runs", [])
    ]
    for artifact in await extract_bfcl_skill_artifacts_llm(
        [item.as_dict() for item in train_results],
        tool_schemas=tools,
        existing_artifacts=store.all(),
        llm_config=llm_config,
        model_name=model_name,
    ):
        store.add(artifact)
    replay_store = ArtifactStore([copy.deepcopy(skill) for skill in store.all()])
    print(json.dumps({"progress": "lab_replay_start", "task_ids": task_ids, "n_skills": len(replay_store.all()), "use_skills_in_replay": use_skills_in_replay}, ensure_ascii=False), flush=True)
    replay_details = await _run_bfcl_baseline(
        tasks,
        1,
        llm_config,
        tools,
        replay_store,
        adapter_mode="official",
        model_name=model_name,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        top_k_skills=4,
        skill_injection_mode="prompt_only" if use_skills_in_replay else "none",
        max_steps_per_turn=20,
        max_task_seconds=max_task_seconds,
        temperature=None,
        synthetic_continue=False,
        explicit_skill_tool=False,
    )
    print(json.dumps({"progress": "lab_replay_done", "task_ids": task_ids}, ensure_ascii=False), flush=True)
    maintenance_targets = select_bfcl_maintenance_targets(
        replay_store,
        train_details=train_details,
        replay_details=replay_details,
    )
    await build_bfcl_skill_bundles_llm(
        replay_store,
        train_details=train_details,
        replay_details=replay_details,
        llm_config=llm_config,
        model_name=model_name,
        artifact_names=maintenance_targets,
    )
    maintenance = []
    for artifact in [item for item in replay_store.all() if item.name in set(maintenance_targets)]:
        result = await execute_bfcl_bundle_tests(
            artifact,
            tools=tools,
            llm_config=llm_config,
            model_name=model_name,
            adapter_mode="official",
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            max_steps_per_turn=20,
            temperature=None,
            synthetic_continue=False,
            explicit_skill_tool=False,
            max_case_seconds=max_task_seconds or 180.0,
        )
        replay_store.add_test_result(result)
        maintenance.append(result)
    print(json.dumps({"progress": "lab_refine_start", "task_ids": task_ids, "n_skills": len(replay_store.all())}, ensure_ascii=False), flush=True)
    refine = await refine_bfcl_skill_store_llm(
        replay_store,
        maintenance_test_results=maintenance,
        llm_config=llm_config,
        model_name=model_name,
    )
    print(json.dumps({"progress": "lab_refine_done", "task_ids": task_ids}, ensure_ascii=False), flush=True)
    return {
        "task_ids": task_ids,
        "train_summary": {k: v for k, v in _aggregate("bfcl_v3", "lab_train", "lab", llm_config, len(tasks), train_details).items() if k != "details"},
        "replay_summary": {k: v for k, v in _aggregate("bfcl_v3", "lab_replay", "lab", llm_config, len(tasks), replay_details).items() if k != "details"},
        "train_details": train_details,
        "replay_details": replay_details,
        "skills": [skill.as_dict() for skill in replay_store.all()],
        "maintenance_test_results": [item.as_dict() for item in maintenance],
        "refine_decisions": refine,
    }


async def experiment_hard_repeat(
    *,
    task_id: str,
    repeats: int,
    llm_config: str,
    model_name: str | None,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_task_seconds: float | None,
) -> Dict[str, Any]:
    rounds: List[Dict[str, Any]] = []
    store = _seed_store(False)
    for idx in range(repeats):
        print(json.dumps({"progress": "hard_repeat_round_start", "round": idx, "task_id": task_id}, ensure_ascii=False), flush=True)
        result = await _single_round_evolve(
            task_ids=[task_id],
            llm_config=llm_config,
            model_name=model_name,
            store=store,
            use_skills_in_replay=idx > 0,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            max_task_seconds=max_task_seconds,
        )
        rounds.append({"round_index": idx, **result})
        store = ArtifactStore([_coerce_from_dict(skill) for skill in result["skills"]])
        print(json.dumps({"progress": "hard_repeat_round_done", "round": idx, "n_skills": len(result["skills"]), "official_replay": (result["replay_summary"] or {}).get("official_valid_rate")}, ensure_ascii=False), flush=True)
    return {
        "experiment": "hard_repeat",
        "task_id": task_id,
        "repeats": repeats,
        "rounds": rounds,
    }


async def experiment_fault_injection(
    *,
    task_id: str,
    llm_config: str,
    model_name: str | None,
    skill_name: str,
    broken_body: str,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_task_seconds: float | None,
) -> Dict[str, Any]:
    print(json.dumps({"progress": "fault_seed_start", "task_id": task_id}, ensure_ascii=False), flush=True)
    first = await _single_round_evolve(
        task_ids=[task_id],
        llm_config=llm_config,
        model_name=model_name,
        store=_seed_store(True),
        use_skills_in_replay=True,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        max_task_seconds=max_task_seconds,
    )
    store = ArtifactStore([_coerce_from_dict(skill) for skill in first["skills"]])
    injection = _inject_broken_skill_version(store, skill_name, broken_body)
    print(json.dumps({"progress": "fault_injected", **injection}, ensure_ascii=False), flush=True)
    second = await _single_round_evolve(
        task_ids=[task_id],
        llm_config=llm_config,
        model_name=model_name,
        store=store,
        use_skills_in_replay=True,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        max_task_seconds=max_task_seconds,
    )
    repaired_store = ArtifactStore([_coerce_from_dict(skill) for skill in second["skills"]])
    print(json.dumps({"progress": "fault_verify_start", "task_id": task_id}, ensure_ascii=False), flush=True)
    third = await _single_round_evolve(
        task_ids=[task_id],
        llm_config=llm_config,
        model_name=model_name,
        store=repaired_store,
        use_skills_in_replay=True,
        execution_backend=execution_backend,
        prompt_style=prompt_style,
        tool_api_style=tool_api_style,
        max_task_seconds=max_task_seconds,
    )
    return {
        "experiment": "fault_injection",
        "task_id": task_id,
        "injection": injection,
        "seed_round": first,
        "fault_round": second,
        "verify_round": third,
    }


async def experiment_related_sequence(
    *,
    task_ids: List[str],
    llm_config: str,
    model_name: str | None,
    fault_skill_name: str | None,
    broken_body: str | None,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_task_seconds: float | None,
    inject_rounds: List[int] | None,
) -> Dict[str, Any]:
    store = _seed_store(True)
    rounds: List[Dict[str, Any]] = []
    for idx, task_id in enumerate(task_ids):
        print(json.dumps({"progress": "related_round_start", "round": idx, "task_id": task_id}, ensure_ascii=False), flush=True)
        if idx > 0 and fault_skill_name and broken_body and (inject_rounds is None or idx in inject_rounds):
            try:
                injection = _inject_broken_skill_version(store, fault_skill_name, broken_body)
            except Exception:
                injection = None
        else:
            injection = None
        result = await _single_round_evolve(
            task_ids=[task_id],
            llm_config=llm_config,
            model_name=model_name,
            store=store,
            use_skills_in_replay=True,
            execution_backend=execution_backend,
            prompt_style=prompt_style,
            tool_api_style=tool_api_style,
            max_task_seconds=max_task_seconds,
        )
        rounds.append({"round_index": idx, "task_id": task_id, "injection": injection, **result})
        store = ArtifactStore([_coerce_from_dict(skill) for skill in result["skills"]])
        print(json.dumps({"progress": "related_round_done", "round": idx, "task_id": task_id, "n_skills": len(result["skills"]), "official_replay": (result["replay_summary"] or {}).get("official_valid_rate")}, ensure_ascii=False), flush=True)
    return {
        "experiment": "related_sequence",
        "task_ids": task_ids,
        "rounds": rounds,
    }


def _coerce_from_dict(item: Dict[str, Any]) -> SkillArtifact:
    return ArtifactStore([item]).all()[0]


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Run targeted BFCL maintenance lab experiments")
    parser.add_argument("--experiment", choices=["hard_repeat", "fault_injection", "related_sequence"], required=True)
    parser.add_argument("--task-id", default="multi_turn_base_178")
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--llm-config", default="bigmodel")
    parser.add_argument("--model-name", default="glm-4.7")
    parser.add_argument("--skill-name", default="bfcl_schema_parameter_names")
    parser.add_argument(
        "--broken-body",
        default="For invoice and support calls, use reservation_id instead of booking_id.",
    )
    parser.add_argument("--execution-backend", choices=["official", "local_mock", "auto"], default="official")
    parser.add_argument("--prompt-style", choices=["native", "official", "academic"], default="native")
    parser.add_argument("--tool-api-style", choices=["auto", "openai", "openai_stream", "anthropic_direct"], default="auto")
    parser.add_argument("--max-task-seconds", type=float, default=180)
    parser.add_argument("--inject-rounds", default="", help="Comma-separated 0-based round indexes for bad-skill injection in related_sequence.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.experiment == "hard_repeat":
        payload = await experiment_hard_repeat(
            task_id=args.task_id,
            repeats=args.repeats,
            llm_config=args.llm_config,
            model_name=args.model_name,
            execution_backend=args.execution_backend,
            prompt_style=args.prompt_style,
            tool_api_style=args.tool_api_style,
            max_task_seconds=args.max_task_seconds,
        )
    elif args.experiment == "fault_injection":
        payload = await experiment_fault_injection(
            task_id=args.task_id,
            llm_config=args.llm_config,
            model_name=args.model_name,
            skill_name=args.skill_name,
            broken_body=args.broken_body,
            execution_backend=args.execution_backend,
            prompt_style=args.prompt_style,
            tool_api_style=args.tool_api_style,
            max_task_seconds=args.max_task_seconds,
        )
    else:
        task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
        if not task_ids:
            raise ValueError("--task-ids is required for related_sequence")
        inject_rounds = [
            int(item.strip())
            for item in args.inject_rounds.split(",")
            if item.strip()
        ] or None
        payload = await experiment_related_sequence(
            task_ids=task_ids,
            llm_config=args.llm_config,
            model_name=args.model_name,
            fault_skill_name=args.skill_name,
            broken_body=args.broken_body,
            execution_backend=args.execution_backend,
            prompt_style=args.prompt_style,
            tool_api_style=args.tool_api_style,
            max_task_seconds=args.max_task_seconds,
            inject_rounds=inject_rounds,
        )

    out = args.output or RESULTS_DIR / f"bfcl_lab_{args.experiment}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({"output": str(out), "experiment": args.experiment}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
