"""Aligned SkillX-on-BFCL runner using our official BFCL executor/scorer.

SkillX provides extraction, retrieval, and prompt formatting, but its BFCL
agent has no environment integration. This module fills only that integration
layer so the comparison keeps SkillX's skill pipeline while sharing the same
BFCL executor/scorer, model, split, and request parameters as our runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

from app.config import LLMSettings, config
from academic.benchmarks.bfcl import load_bfcl_tools, run_bfcl_task
from academic.benchmarks.bfcl.related.experiment import _tasks_from_manifest
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.runner import _aggregate, _run_bfcl_baseline, _task_runs
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask
from academic.config import PROJECT_ROOT, RESULTS_DIR


SKILLX_ROOT = Path(os.environ.get("SKILLX_ROOT", "/home/lixujun/external_repos/SkillX"))
if str(SKILLX_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(SKILLX_ROOT.parent))

from SkillX.pipeline import IterativeSkillPipeline  # noqa: E402
from SkillX.inference.embedding_service import EmbeddingService  # noqa: E402
from SkillX.inference.skill_usage import SkillUsageService  # noqa: E402
from SkillX.core.skill import SkillLibrary  # noqa: E402


def _bfcl_tool_aliases(name: str) -> set[str]:
    text = str(name or "").strip()
    if not text:
        return set()
    aliases = {text}
    parts = [part for part in text.split(".") if part]
    if parts:
        aliases.add(parts[-1])
    if len(parts) >= 2 and parts[0] == "apis":
        aliases.add(".".join(parts[1:]))
    return aliases


class SkillXCompatibleLLM:
    """Small adapter from SkillX's ainvoke interface to the local proxy."""

    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str,
        max_tokens: int,
        temperature: float | None,
        timeout_s: int,
        max_retries: int,
    ) -> None:
        import httpx
        from anthropic import AsyncAnthropic

        endpoint = base_url.rstrip("/")
        if endpoint.endswith("/v1"):
            endpoint = endpoint[:-3]
        self.client = AsyncAnthropic(
            api_key=api_key,
            base_url=endpoint,
            timeout=httpx.Timeout(float(timeout_s), connect=10.0),
            max_retries=1,
        )
        self.model = model_name
        self.max_tokens = max_tokens
        self.temperature = 0.0 if temperature is None else temperature
        self.max_retries = max(1, int(max_retries or 1))

    async def ainvoke(
        self,
        messages: List[Any],
        regex_pattern: str | None = None,
        regex_extractor: Any | None = None,
        **_: Any,
    ) -> str:
        normalized = self._normalize_messages(messages)
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                system, anthropic_messages = self._anthropic_messages(normalized)
                kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    "messages": anthropic_messages,
                }
                if system:
                    kwargs["system"] = [{"type": "text", "text": system}]
                response = await self.client.messages.create(**kwargs)
                text = "\n".join(
                    str(getattr(block, "text", "") or "")
                    for block in (response.content or [])
                    if getattr(block, "type", "") == "text" or getattr(block, "text", None)
                ).strip()
                if regex_extractor is not None and regex_extractor(text) is None:
                    raise ValueError("SkillX regex_extractor failed")
                if regex_pattern is not None and not re.search(regex_pattern, text):
                    raise ValueError("SkillX regex_pattern failed")
                return text
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= self.max_retries:
                    break
                await asyncio.sleep(min(30.0, 2.0 * (attempt + 1)))
        raise last_error or RuntimeError("SkillX LLM call failed")

    def invoke(self, messages: List[Any], **kwargs: Any) -> str:
        return asyncio.run(self.ainvoke(messages, **kwargs))

    @staticmethod
    def _normalize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, tuple) and len(msg) == 2:
                role, content = msg
                normalized.append({"role": "user" if role == "human" else str(role), "content": str(content)})
            elif isinstance(msg, dict):
                role = str(msg.get("role") or "user")
                normalized.append({"role": "user" if role == "human" else role, "content": str(msg.get("content") or "")})
            elif hasattr(msg, "content"):
                role = getattr(msg, "type", None) or getattr(msg, "role", None) or msg.__class__.__name__.lower()
                if role in {"humanmessage", "human"}:
                    role = "user"
                elif role in {"systemmessage", "system"}:
                    role = "system"
                elif role in {"aimessage", "ai", "assistant"}:
                    role = "assistant"
                normalized.append({"role": str(role), "content": str(getattr(msg, "content") or "")})
            else:
                normalized.append({"role": "user", "content": str(msg)})
        return normalized

    @staticmethod
    def _anthropic_messages(messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, str]]]:
        system_parts: List[str] = []
        converted: List[Dict[str, str]] = []
        for msg in messages:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            if role == "system":
                system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"] += "\n\n" + content
            else:
                converted.append({"role": role, "content": content})
        if not converted:
            converted.append({"role": "user", "content": ""})
        return "\n\n".join(part for part in system_parts if part), converted


def _ensure_local_claude_proxy(base_url: str = "http://127.0.0.1:4000/v1") -> None:
    config.llm["local_claude_proxy"] = LLMSettings(
        model="claude-sonnet-4-5",
        base_url=base_url.rstrip("/"),
        api_key="1234abcd",
        max_tokens=32768,
        max_input_tokens=None,
        temperature=0.0,
        api_type="",
        api_version="",
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _task_text(task: BenchmarkTask) -> str:
    chunks: List[str] = []
    for turn in task.question or []:
        for msg in turn or []:
            if msg.get("role", "user") == "user":
                chunks.append(str(msg.get("content") or ""))
    return "\n".join(chunks).strip()


def _messages_from_trace(trace: Dict[str, Any], task: BenchmarkTask) -> List[Dict[str, Any]]:
    messages = trace.get("messages")
    if isinstance(messages, list) and messages:
        out = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            row = {"role": msg.get("role", ""), "content": msg.get("content", "")}
            if msg.get("tool_calls"):
                row["tool_calls"] = msg.get("tool_calls")
            if msg.get("tool_call_id"):
                row["tool_call_id"] = msg.get("tool_call_id")
            out.append(row)
        return out
    return [
        {"role": "user", "content": str(msg.get("content") or "")}
        for turn in task.question or []
        for msg in turn or []
        if msg.get("role", "user") == "user"
    ]


def _trajectory_from_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    run = (detail.get("runs") or [{}])[0]
    metrics = dict(run.get("metrics") or {})
    task_payload = dict(detail.get("task") or {})
    task = BenchmarkTask(
        benchmark=str(task_payload.get("benchmark") or "bfcl_v3"),
        task_id=str(detail.get("task_id") or task_payload.get("task_id") or ""),
        question=task_payload.get("question") or [],
        expected=task_payload.get("expected"),
        input_artifacts=dict(task_payload.get("input_artifacts") or {}),
        metadata=dict(task_payload.get("metadata") or {}),
    )
    valid = metrics.get("official_valid")
    reward = 1.0 if valid is True or run.get("success") is True else 0.0
    return {
        "trajectory_id": str(detail.get("task_id") or ""),
        "benchmark": "bfcl",
        "task_id": str(detail.get("task_id") or ""),
        "user_task": _task_text(task),
        "task_history": _messages_from_trace(dict(run.get("trace") or {}), task),
        "reward": reward,
        "metadata": {
            "official_valid": valid,
            "score": run.get("score"),
            "call_f1": metrics.get("call_f1"),
            "total_tokens": metrics.get("total_tokens"),
            "elapsed_s": metrics.get("elapsed_s"),
        },
    }


def _write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""))


def _assert_embedding_service(base_url: str) -> None:
    response = requests.post(
        f"{base_url.rstrip('/')}/encode",
        json={"texts": ["bfcl embedding preflight"], "model": "Qwen3-Embedding-8B"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    embeddings = payload.get("embeddings") or []
    if not embeddings or not any(float(x) != 0.0 for x in embeddings[0]):
        raise RuntimeError(f"SkillX embedding service at {base_url} returned empty/zero embedding")


def _make_skillx_llm(args: argparse.Namespace) -> SkillXCompatibleLLM:
    return SkillXCompatibleLLM(
        model_name=args.model_name,
        api_key=args.api_key,
        base_url=args.openai_base_url,
        max_tokens=args.skillx_max_tokens,
        temperature=args.temperature,
        timeout_s=args.llm_timeout_s,
        max_retries=args.skillx_retries,
    )


async def _export_train_trajectories(
    *,
    train_tasks: List[BenchmarkTask],
    tools: List[Dict[str, Any]],
    args: argparse.Namespace,
    train_details_path: Path,
    trajectories_path: Path,
) -> List[Dict[str, Any]]:
    if train_details_path.exists() and trajectories_path.exists() and not args.force_train_export:
        return [_trajectory_from_detail(detail) for detail in _load_json(train_details_path).get("details", [])]

    partial_path = train_details_path.with_name(f"{train_details_path.stem}_partial.json")
    details = await _run_bfcl_baseline(
        train_tasks,
        1,
        args.llm_config,
        tools,
        ArtifactStore(),
        adapter_mode="official",
        model_name=args.model_name,
        execution_backend=args.execution_backend,
        prompt_style=args.prompt_style,
        tool_api_style=args.tool_api_style,
        top_k_skills=0,
        skill_injection_mode="none",
        max_steps_per_turn=args.max_steps_per_turn,
        partial_output=partial_path,
        max_task_seconds=args.max_task_seconds,
        temperature=args.temperature,
        synthetic_continue=args.synthetic_continue,
        explicit_skill_tool=False,
        phase="skillx_train_export",
        concurrency=args.train_concurrency,
    )
    payload = {"benchmark": "bfcl_v3", "mode": "skillx_train_export", "details": details}
    _write_json(train_details_path, payload)
    trajectories = [_trajectory_from_detail(detail) for detail in details]
    _write_jsonl(trajectories_path, trajectories)
    return trajectories


async def _run_skillx_extraction(
    *,
    trajectories: List[Dict[str, Any]],
    args: argparse.Namespace,
    extraction_dir: Path,
) -> SkillLibrary:
    library_path = extraction_dir / "skillx_skill_library.json"
    if library_path.exists() and not args.force_skillx_extraction:
        return SkillLibrary.load(str(library_path))

    skillx_llm = _make_skillx_llm(args)
    pipeline = IterativeSkillPipeline(
        llm=skillx_llm,
        benchmark="bfcl",
        skill_type=args.skillx_skill_type,
        plan_strategy=args.skillx_plan_strategy,
        output_dir=str(extraction_dir),
        verbose=True,
    )
    results = await pipeline.run(
        trajectories,
        num_epochs=args.skillx_epochs,
        filter_threshold=args.skillx_filter_threshold,
        batch_size=args.skillx_batch_size,
        max_concurrent=args.skillx_max_concurrent,
        filter_timing=args.skillx_filter_timing,
    )
    saved = pipeline.save_results(results, prefix="skillx")
    library = results["skill_library"]
    library.save(str(library_path))
    _write_json(extraction_dir / "skillx_saved_paths.json", saved)
    return library


class SkillXPromptProvider:
    def __init__(self, *, library: SkillLibrary, args: argparse.Namespace) -> None:
        skillx_llm = _make_skillx_llm(args)
        self.service = SkillUsageService(
            skill_library=library,
            embedding_service=EmbeddingService(base_url=args.embedding_url, timeout=30),
            llm=skillx_llm if args.skillx_use_selector else None,
            benchmark="bfcl",
            mode=args.skillx_usage_mode,
        )
        self.max_skills = args.skillx_max_skills
        self.rewrite_plan = args.skillx_rewrite_plan
        self.events: List[Dict[str, Any]] = []

    async def __call__(
        self,
        *,
        task: BenchmarkTask,
        turn_index: int,
        user_messages: List[Dict[str, Any]],
        query: str,
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        available = set()
        for tool in tools:
            name = ((tool.get("function") or {}).get("name") or tool.get("name") or "").strip()
            if name:
                available.update(_bfcl_tool_aliases(name))
        for skill in self.service.retriever._skills:
            if all(_bfcl_tool_aliases(tool_name) & available for tool_name in skill.tools):
                for tool_name in skill.tools:
                    available.update(_bfcl_tool_aliases(tool_name))
        self.service.set_available_tools(available)
        result = await self.service.prepare_prompt(
            task=query or _task_text(task),
            base_prompt="",
            max_skills=self.max_skills,
            rewrite_plan=self.rewrite_plan,
        )
        metadata = dict(result.get("metadata") or {})
        selected = metadata.get("selected_skills") or []
        names = [
            str(item.get("name") or "").strip()
            for item in selected
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        event = {
            "task_id": task.task_id,
            "turn_index": turn_index,
            "query": query,
            "available_tool_count": len(available),
            "selected_skill_names": names,
            "metadata": metadata,
        }
        self.events.append(event)
        return {
            "skill_prompt": str(result.get("system_prompt") or ""),
            "retrieved_skill_names": names,
            "prompt_injected_skill_names": names,
            "metadata": {
                "source": "skillx",
                "selected_skill_names": names,
                "raw_retrieved_skills": metadata.get("raw_retrieved_skills") or [],
                "retrieved_plans": metadata.get("retrieved_plans") or [],
            },
        }


async def _run_skillx_test(
    *,
    test_tasks: List[BenchmarkTask],
    tools: List[Dict[str, Any]],
    library: SkillLibrary,
    args: argparse.Namespace,
    test_result_path: Path,
    provider_events_path: Path,
) -> Dict[str, Any]:
    if test_result_path.exists() and not args.force_test:
        return _load_json(test_result_path)

    provider = SkillXPromptProvider(library=library, args=args)
    detail_by_task: Dict[str, Dict[str, Any]] = {}
    if args.partial_test_output.exists():
        try:
            existing = _load_json(args.partial_test_output).get("details") or []
            detail_by_task = {str(item.get("task_id")): item for item in existing if isinstance(item, dict)}
        except Exception:
            detail_by_task = {}

    def write_partial() -> None:
        ordered = [
            detail_by_task[task.task_id]
            for task in test_tasks
            if task.task_id in detail_by_task
        ]
        _write_json(
            args.partial_test_output,
            {
                "benchmark": "bfcl_v3",
                "mode": "skillx_aligned_test",
                "completed_tasks": len(ordered),
                "total_tasks": len(test_tasks),
                "details": ordered,
            },
        )
        _write_json(provider_events_path, {"events": provider.events})

    async def run_one(task: BenchmarkTask, task_index: int) -> tuple[int, Dict[str, Any]]:
        if task.task_id in detail_by_task:
            return task_index, detail_by_task[task.task_id]
        try:
            result = await run_bfcl_task(
                task,
                llm_config=args.llm_config,
                tools=tools,
                artifact_store=ArtifactStore(),
                adapter_mode="official",
                model_name=args.model_name,
                execution_backend=args.execution_backend,
                prompt_style=args.prompt_style,
                tool_api_style=args.tool_api_style,
                top_k_skills=0,
                min_skill_score=0.0,
                max_steps_per_turn=args.max_steps_per_turn,
                skill_injection_mode="none",
                temperature=args.temperature,
                synthetic_continue=args.synthetic_continue,
                max_task_seconds=args.max_task_seconds,
                external_skill_prompt_provider=provider,
            )
        except asyncio.TimeoutError:
            result = BenchmarkResult(
                benchmark="bfcl_v3",
                task_id=task.task_id,
                success=False,
                score=0.0,
                metrics={"exception": "TaskTimeout", "max_task_seconds": args.max_task_seconds},
                trace={"task_id": task.task_id, "timed_out": True},
                error=f"Task exceeded {args.max_task_seconds} seconds",
            )
        item = result.as_dict()
        item["run_idx"] = 0
        metrics = item.get("metrics") or {}
        print(
            json.dumps(
                {
                    "progress": "skillx_bfcl_task_run",
                    "task_index": task_index,
                    "n_tasks": len(test_tasks),
                    "task_id": task.task_id,
                    "score": item.get("score"),
                    "success": item.get("success"),
                    "official_valid": metrics.get("official_valid"),
                    "elapsed_s": metrics.get("elapsed_s"),
                    "skillx_prompt_injected": metrics.get("prompt_injected_skills"),
                    "error": item.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return task_index, _task_runs(task, [item])

    remaining = [(idx, task) for idx, task in enumerate(test_tasks) if task.task_id not in detail_by_task]
    concurrency = max(1, int(args.test_concurrency or 1))
    if concurrency > 1:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(idx: int, task: BenchmarkTask) -> tuple[int, Dict[str, Any]]:
            async with sem:
                return await run_one(task, idx)

        for idx, detail in sorted(await asyncio.gather(*(guarded(idx, task) for idx, task in remaining))):
            detail_by_task[test_tasks[idx].task_id] = detail
            write_partial()
    else:
        for idx, task in remaining:
            _, detail = await run_one(task, idx)
            detail_by_task[task.task_id] = detail
            write_partial()

    details = [detail_by_task[task.task_id] for task in test_tasks if task.task_id in detail_by_task]
    summary = _aggregate("bfcl_v3", "skillx_aligned_test", args.tag, args.llm_config, args.train_size, details)
    payload = {
        "benchmark": "bfcl_v3",
        "mode": "skillx_aligned_test",
        "tag": args.tag,
        "llm_config": args.llm_config,
        "model_name": args.model_name,
        "config_summary": {
            "skillx_root": str(SKILLX_ROOT),
            "manifest": str(args.manifest),
            "train_size": args.train_size,
            "test_size": len(test_tasks),
            "max_steps_per_turn": args.max_steps_per_turn,
            "max_task_seconds": args.max_task_seconds,
            "temperature": args.temperature,
            "embedding_url": args.embedding_url,
            "skillx_usage_mode": args.skillx_usage_mode,
            "skillx_max_skills": args.skillx_max_skills,
        },
        "test_summary": {k: v for k, v in summary.items() if k != "details"},
        "details": details,
        "skillx_provider_events_path": str(provider_events_path),
    }
    _write_json(test_result_path, payload)
    _write_json(provider_events_path, {"events": provider.events})
    return payload


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Run aligned SkillX baseline on BFCL")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json")
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "data/benchmarks/bfcl_v3")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR / "skillx_bfcl_aligned")
    parser.add_argument("--tag", default="skillx_bfcl_50_50_sonnet_20260520")
    parser.add_argument("--train-size", type=int, default=50)
    parser.add_argument("--test-size", type=int, default=50)
    parser.add_argument("--data-source", choices=["bfcl_eval_bundle", "hf_v3"], default="bfcl_eval_bundle")
    parser.add_argument("--llm-config", default="local_claude_proxy")
    parser.add_argument("--model-name", default="claude-sonnet-4-5")
    parser.add_argument("--openai-base-url", default="http://127.0.0.1:4000/v1")
    parser.add_argument("--api-key", default="1234abcd")
    parser.add_argument("--embedding-url", default="http://127.0.0.1:7000")
    parser.add_argument("--execution-backend", choices=["official", "local_mock", "auto"], default="official")
    parser.add_argument("--prompt-style", choices=["native", "official", "academic"], default="native")
    parser.add_argument("--tool-api-style", choices=["auto", "openai", "openai_direct", "openai_stream", "anthropic_direct"], default="auto")
    parser.add_argument("--max-steps-per-turn", type=int, default=20)
    parser.add_argument("--max-task-seconds", type=float, default=180.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--llm-timeout-s", type=int, default=180)
    parser.add_argument("--synthetic-continue", action="store_true")
    parser.add_argument("--train-concurrency", type=int, default=1)
    parser.add_argument("--test-concurrency", type=int, default=1)
    parser.add_argument("--skillx-max-tokens", type=int, default=32768)
    parser.add_argument("--skillx-retries", type=int, default=3)
    parser.add_argument("--skillx-skill-type", choices=["functional", "atomic", "hybrid"], default="hybrid")
    parser.add_argument("--skillx-plan-strategy", choices=["shortest", "merge"], default="shortest")
    parser.add_argument("--skillx-epochs", type=int, default=1)
    parser.add_argument("--skillx-filter-threshold", type=float, default=0.999)
    parser.add_argument("--skillx-batch-size", type=int, default=10)
    parser.add_argument("--skillx-max-concurrent", type=int, default=5)
    parser.add_argument("--skillx-filter-timing", choices=["pre_merge", "post_merge", "both", "none"], default="pre_merge")
    parser.add_argument("--skillx-usage-mode", choices=["vanilla", "plan_only", "skill_only", "plan_with_skill"], default="plan_with_skill")
    parser.add_argument("--skillx-max-skills", type=int, default=10)
    parser.add_argument("--skillx-use-selector", action="store_true")
    parser.add_argument("--skillx-rewrite-plan", action="store_true")
    parser.add_argument("--force-train-export", action="store_true")
    parser.add_argument("--force-skillx-extraction", action="store_true")
    parser.add_argument("--force-test", action="store_true")
    parser.add_argument("--skip-embedding-preflight", action="store_true")
    args = parser.parse_args()

    os.environ["OPENAI_API_KEY"] = args.api_key
    os.environ["OPENAI_BASE_URL"] = args.openai_base_url
    _ensure_local_claude_proxy(args.openai_base_url)

    out_dir = args.output_dir / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    args.partial_test_output = out_dir / "skillx_test_partial.json"
    if not args.skip_embedding_preflight:
        _assert_embedding_service(args.embedding_url)

    manifest = _load_json(args.manifest)
    manifest = {
        **manifest,
        "train_task_ids": list(manifest.get("train_task_ids") or [])[: args.train_size],
        "train_tasks": list(manifest.get("train_tasks") or [])[: args.train_size],
        "test_task_ids": list(manifest.get("test_task_ids") or [])[: args.test_size],
        "test_tasks": list(manifest.get("test_tasks") or [])[: args.test_size],
    }
    train_tasks, test_tasks = _tasks_from_manifest(manifest, cache_dir=args.cache_dir, data_source=args.data_source)
    tools = load_bfcl_tools(args.cache_dir, data_source=args.data_source)

    config_payload = {
        "args": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key != "partial_test_output"},
        "skillx_root": str(SKILLX_ROOT),
        "train_task_ids": [task.task_id for task in train_tasks],
        "test_task_ids": [task.task_id for task in test_tasks],
    }
    _write_json(out_dir / "run_config.json", config_payload)

    t0 = time.monotonic()
    trajectories = await _export_train_trajectories(
        train_tasks=train_tasks,
        tools=tools,
        args=args,
        train_details_path=out_dir / "train_rollout_details.json",
        trajectories_path=out_dir / "skillx_train_trajectories.jsonl",
    )
    library = await _run_skillx_extraction(
        trajectories=trajectories,
        args=args,
        extraction_dir=out_dir / "skillx_extraction",
    )
    result = await _run_skillx_test(
        test_tasks=test_tasks,
        tools=tools,
        library=library,
        args=args,
        test_result_path=out_dir / "skillx_bfcl_test_result.json",
        provider_events_path=out_dir / "skillx_provider_events.json",
    )
    result["elapsed_s_total"] = round(time.monotonic() - t0, 3)
    _write_json(out_dir / "skillx_bfcl_result_with_elapsed.json", result)
    print(json.dumps({"output_dir": str(out_dir), "result": str(out_dir / "skillx_bfcl_test_result.json")}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
