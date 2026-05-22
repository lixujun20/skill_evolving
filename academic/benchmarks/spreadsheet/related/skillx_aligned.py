"""Aligned SkillX-on-Spreadsheet runner using our Spreadsheet executor/scorer.

SkillX does not provide a SpreadsheetBench environment integration. This module
keeps SkillX responsible for plan/skill extraction and retrieval, while reusing
the project Spreadsheet executor, verifier, fixed splits, and cost accounting.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import requests

from app.config import LLMSettings, config
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.runner import _aggregate, _run_spreadsheet_baseline, _task_runs
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact, SkillInterface
from academic.benchmarks.spreadsheet.executor import (
    run_spreadsheet_task,
    run_spreadsheet_task_bash_react,
    run_spreadsheet_task_notebook,
)
from academic.benchmarks.spreadsheet.loader import load_spreadsheet_task_pool
from academic.config import PROJECT_ROOT, RESULTS_DIR


SKILLX_ROOT = Path(os.environ.get("SKILLX_ROOT", "/home/lixujun/external_repos/SkillX"))
if str(SKILLX_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(SKILLX_ROOT.parent))

from SkillX.core.skill import SkillLibrary  # noqa: E402
from SkillX.inference.embedding_service import EmbeddingService  # noqa: E402
from SkillX.inference.prompt_formatters import BasePromptFormatter  # noqa: E402
from SkillX.inference.retriever import SkillRetriever  # noqa: E402
from SkillX.pipeline import IterativeSkillPipeline  # noqa: E402
from SkillX.prompts.registry import PromptRegistry  # noqa: E402


SPREADSHEET_PLAN_PROMPT = """You are extracting reusable plans from successful SpreadsheetBench trajectories.

Given a spreadsheet task and a successful interaction history, write a concise
general plan that another spreadsheet agent could adapt to similar workbook
editing tasks. Mention only stable operations such as inspecting workbook
sheets, locating headers/ranges, applying formulas, copying/moving/deleting
rows or columns, preserving styles, saving output.xlsx, and verifying the
answer range. Do not include task-specific workbook filenames or exact answers.
Use at most 3 steps. Prefer broad reusable operations over a long transcript.

Return only:
<plan>
# step 1: ...
# step 2: ...
</plan>
"""

SPREADSHEET_SKILL_PROMPT = """A SpreadsheetBench agent solved a task successfully. Extract generalizable skills.

# Skill Definition
Return JSON updates with `option` in [add, modify, keep]. For add/modify,
`skill` must have:
- `name`: generic snake_case name.
- `document`: when to use it, parameters to adapt, outputs/effects, and caveats.
- `content`: concise openpyxl-oriented implementation or operational recipe.
- `tools`: use ["openpyxl"].

# Requirements
- The skill must be reusable across SpreadsheetBench workbooks, not tied to one
  exact file, sheet, row, column, keyword, or answer range.
- Prefer parameterized spreadsheet operations: header/range lookup, formula
  fill, row filtering, column copy/move, text/number/date normalization, style
  preservation, workbook save discipline.
- Do not import third-party packages. `openpyxl` and Python stdlib are allowed.
- Do not output a thin transcript of the original task. Extract only the
  reusable subroutine for the specific plan step.
- If there is no reusable operation, return [].

# Output
Return strict JSON in a fenced json block:
```json
[
  {
    "option": "add",
    "skill": {
      "name": "header_based_column_copy",
      "document": "Copy a column selected by header to a destination column while preserving styles. Parameters: source header predicate, destination index/header, worksheet scope. Output: modified workbook saved to output path.",
      "content": "Use openpyxl to load the workbook, find the header row, locate the source column by normalized header text, copy cell values and style objects row by row into the destination column, update width when useful, and save the workbook.",
      "tools": ["openpyxl"]
    }
  }
]
```
"""

SPREADSHEET_FILTER_PROMPT = """You are checking a SpreadsheetBench skill for quality.

Return only "good" or "bad".

Good skills are reusable, parameterized spreadsheet/openpyxl operations or
workflow rules. Bad skills hard-code one workbook's exact answer, sheet, row,
column, keyword list, or output range; include unrelated debugging; are too
vague to guide an executor; or require unavailable non-standard packages.
"""


class SpreadsheetSkillXFormatter(BasePromptFormatter):
    def format_skill_library(self, skills: List[Dict[str, Any]]) -> str:
        if not skills:
            return ""
        lines: List[str] = []
        for idx, skill in enumerate(skills, 1):
            lines.append(f"### SkillX Skill {idx}: {skill.get('name', '')}")
            if skill.get("document"):
                lines.append(f"Document:\n{skill.get('document', '')}")
            if skill.get("content"):
                lines.append(f"Content:\n{skill.get('content', '')}")
            lines.append("")
        return "\n".join(lines).strip()

    def format_system_prompt(
        self,
        base_prompt: str,
        skill_library: str,
        plan: str | None = None,
    ) -> str:
        parts = [base_prompt.strip()] if base_prompt else []
        if plan:
            parts.append("# SkillX Retrieved Plan\nUse this only as reference and adapt it to the current workbook.\n" + plan.strip())
        if skill_library:
            parts.append(
                "# SkillX Retrieved Spreadsheet Skills\n"
                "These are reference skills, not pre-imported callable functions. "
                "You may copy/adapt the described openpyxl logic when it matches the current workbook and answer range.\n\n"
                + skill_library
            )
        return "\n\n".join(part for part in parts if part).strip()


class SkillXCompatibleLLM:
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
        self.timeout_s = float(timeout_s)
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
                response = await asyncio.wait_for(
                    self.client.messages.create(**kwargs),
                    timeout=self.timeout_s,
                )
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


def _register_spreadsheet_skillx_prompts() -> None:
    PromptRegistry.register("plan_extraction", "spreadsheet", SPREADSHEET_PLAN_PROMPT)
    PromptRegistry.register("plan_combine", "spreadsheet", SPREADSHEET_PLAN_PROMPT)
    PromptRegistry.register("skill_extraction", "spreadsheet", SPREADSHEET_SKILL_PROMPT)
    PromptRegistry.register("general_filter", "spreadsheet", SPREADSHEET_FILTER_PROMPT)


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


def _load_task_ids(path: Path | None) -> List[str]:
    if path is None:
        return []
    raw = _load_json(path)
    if isinstance(raw, dict):
        raw = raw.get("task_ids") or raw.get("ids") or raw.get("tasks") or []
    return [str(item.get("task_id") if isinstance(item, dict) else item) for item in raw]


def _select_tasks(pool: Sequence[BenchmarkTask], ids: Sequence[str]) -> List[BenchmarkTask]:
    by_id = {str(task.task_id): task for task in pool}
    missing = [task_id for task_id in ids if task_id not in by_id]
    if missing:
        raise ValueError(f"Missing Spreadsheet task ids: {missing[:10]}")
    return [by_id[task_id] for task_id in ids]


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


def _trajectory_from_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    run = (detail.get("runs") or [{}])[0]
    task_payload = detail.get("task") or {}
    trace = run.get("trace") or {}
    task_id = str(detail.get("task_id") or task_payload.get("task_id") or "")
    code = str(trace.get("code") or "")
    turns = trace.get("notebook_turns") or []
    max_turns = int(os.environ.get("SPREADSHEET_SKILLX_EXTRACT_MAX_TURNS", "8") or "8")
    max_code_chars = int(os.environ.get("SPREADSHEET_SKILLX_EXTRACT_MAX_CODE_CHARS", "5000") or "5000")
    max_stream_chars = int(os.environ.get("SPREADSHEET_SKILLX_EXTRACT_MAX_STREAM_CHARS", "1200") or "1200")
    if turns:
        history = [
            {
                "role": "assistant",
                "content": _middle_truncate(str(turn.get("code") or turn.get("assistant") or ""), max_code_chars),
                "stdout": _tail_truncate(str(turn.get("stdout") or ""), max_stream_chars),
                "stderr": _tail_truncate(str(turn.get("stderr") or ""), max_stream_chars),
                "returncode": turn.get("returncode"),
            }
            for turn in turns[:max_turns]
        ]
    else:
        history = [
            {
                "role": "assistant",
                "content": _middle_truncate(code, max_code_chars),
                "stdout": _tail_truncate(str(trace.get("stdout") or ""), max_stream_chars),
                "stderr": _tail_truncate(str(trace.get("stderr") or ""), max_stream_chars),
            }
        ]
    return {
        "trajectory_id": task_id,
        "benchmark": "spreadsheet",
        "task_id": task_id,
        "user_task": str(task_payload.get("question") or ""),
        "task_history": history,
        "trajectory": history,
        "successful_trajectory": history,
        "reward": 1.0 if run.get("success") is True else 0.0,
        "metadata": {
            "score": run.get("score"),
            "success": run.get("success"),
            "instruction_type": (task_payload.get("metadata") or {}).get("instruction_type"),
            "total_tokens": (run.get("metrics") or {}).get("total_tokens"),
            "elapsed_s": (run.get("metrics") or {}).get("elapsed_s"),
        },
    }


def _filter_extraction_trajectories(
    trajectories: List[Dict[str, Any]],
    *,
    max_chars: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for idx, trajectory in enumerate(trajectories):
        if not isinstance(trajectory, dict):
            skipped.append({
                "task_id": None,
                "index": idx,
                "chars": 0,
                "reward": None,
                "reason": "invalid_empty_trajectory",
            })
            continue
        size = len(json.dumps(trajectory, ensure_ascii=False))
        row = {
            "task_id": trajectory.get("task_id"),
            "chars": size,
            "reward": trajectory.get("reward"),
        }
        if size > max_chars:
            skipped.append({**row, "reason": "trajectory_too_long_for_skillx_extraction"})
        else:
            kept.append(trajectory)
    return kept, skipped


def _middle_truncate(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n# ... [trace truncated for SkillX extraction] ...\n" + text[-tail:]


def _tail_truncate(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return "[trace output truncated for SkillX extraction]\n" + text[-max_chars:]


def _write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""))


def _assert_embedding_service(base_url: str) -> None:
    response = requests.post(
        f"{base_url.rstrip('/')}/encode",
        json={"texts": ["spreadsheet skillx embedding preflight"], "model": "Qwen3-Embedding-8B"},
        timeout=15,
    )
    response.raise_for_status()
    embeddings = response.json().get("embeddings") or []
    if not embeddings or not any(float(x) != 0.0 for x in embeddings[0]):
        raise RuntimeError(f"SkillX embedding service at {base_url} returned empty/zero embedding")


def _skillx_library_to_store(library: SkillLibrary) -> ArtifactStore:
    artifacts: List[SkillArtifact] = []
    for plan_task, plan in library.planning.items():
        name = _safe_name("skillx_plan_" + plan_task[:60])
        artifacts.append(
            SkillArtifact(
                name=name,
                kind="workflow_guardrail_card",
                description=f"SkillX retrieved plan for Spreadsheet task pattern: {plan_task[:80]}",
                body=plan.plan,
                tags=["SpreadsheetBench", "SkillX", "plan"],
                interface=SkillInterface(
                    summary="SkillX task-level plan reference.",
                    usage="Use as a reference workflow only when the current SpreadsheetBench task is similar.",
                    invocation_contract={"injection_type": "workflow"},
                ),
                metadata={
                    "source": "skillx",
                    "skillx_type": "planning",
                    "source_task": plan_task,
                    "intent_keywords": ["spreadsheet", "openpyxl"],
                    "domains": ["SpreadsheetBench"],
                    "allowed_tools": ["openpyxl"],
                    "injection_type": "workflow",
                },
            )
        )
    for skill in library.get_all_skills():
        artifacts.append(
            SkillArtifact(
                name=_safe_name("skillx_" + skill.name),
                kind="workflow_guardrail_card",
                description=(skill.document or skill.name)[:180],
                body=f"Document:\n{skill.document}\n\nContent:\n{skill.content}",
                tags=["SpreadsheetBench", "SkillX", *[str(t) for t in skill.tools]],
                interface=SkillInterface(
                    summary=(skill.document or skill.name)[:220],
                    usage="Reference-only SkillX spreadsheet skill. Copy/adapt the described openpyxl logic when applicable.",
                    invocation_contract={"injection_type": "workflow", "reference_only": True},
                ),
                metadata={
                    "source": "skillx",
                    "skillx_type": skill.skill_type,
                    "skillx_name": skill.name,
                    "intent_keywords": ["spreadsheet", "openpyxl", skill.name],
                    "domains": ["SpreadsheetBench"],
                    "allowed_tools": list(skill.tools or ["openpyxl"]),
                    "injection_type": "workflow",
                },
            )
        )
    return ArtifactStore(artifacts)


def _skillx_selection_to_store(
    *,
    task_id: str,
    plan: str | None,
    retrieved_plans: List[Dict[str, Any]],
    selected_skills: List[Dict[str, Any]],
) -> ArtifactStore:
    artifacts: List[SkillArtifact] = []
    if plan:
        source_task = ""
        similarity = None
        if retrieved_plans:
            source_task = str(retrieved_plans[0].get("task") or "")
            similarity = retrieved_plans[0].get("similarity")
        artifacts.append(
            SkillArtifact(
                name=_safe_name(f"skillx_plan_for_{task_id}"),
                kind="workflow_guardrail_card",
                description=f"SkillX retrieved plan for Spreadsheet task {task_id}",
                body=plan,
                tags=["SpreadsheetBench", "SkillX", "plan"],
                interface=SkillInterface(
                    summary="SkillX task-level plan reference.",
                    usage="Use as a reference workflow only when it matches the current SpreadsheetBench task.",
                    invocation_contract={"injection_type": "workflow", "reference_only": True},
                ),
                metadata={
                    "source": "skillx",
                    "skillx_type": "planning",
                    "source_task": source_task,
                    "similarity": similarity,
                    "intent_keywords": ["spreadsheet", "openpyxl"],
                    "domains": ["SpreadsheetBench"],
                    "allowed_tools": ["openpyxl"],
                    "injection_type": "workflow",
                },
            )
        )
    for idx, skill in enumerate(selected_skills, 1):
        name = str(skill.get("name") or f"skillx_spreadsheet_skill_{idx}")
        artifacts.append(
            SkillArtifact(
                name=_safe_name("skillx_" + name),
                kind="workflow_guardrail_card",
                description=str(skill.get("document") or name)[:180],
                body=f"Document:\n{skill.get('document', '')}\n\nContent:\n{skill.get('content', '')}",
                tags=["SpreadsheetBench", "SkillX", *[str(t) for t in (skill.get("tools") or [])]],
                interface=SkillInterface(
                    summary=str(skill.get("document") or name)[:220],
                    usage=(
                        "Reference-only SkillX spreadsheet skill. Copy/adapt the described "
                        "openpyxl logic when applicable."
                    ),
                    invocation_contract={"injection_type": "workflow", "reference_only": True},
                ),
                metadata={
                    "source": "skillx",
                    "skillx_type": skill.get("skill_type"),
                    "skillx_name": name,
                    "similarity": skill.get("similarity"),
                    "intent_keywords": ["spreadsheet", "openpyxl", name],
                    "domains": ["SpreadsheetBench"],
                    "allowed_tools": list(skill.get("tools") or ["openpyxl"]),
                    "injection_type": "workflow",
                },
            )
        )
    return ArtifactStore(artifacts)


async def _retrieve_skillx_for_task(
    *,
    retriever: SkillRetriever,
    task: BenchmarkTask,
    max_skills: int,
    skills_per_step: int,
) -> Tuple[ArtifactStore, Dict[str, Any]]:
    task_text = str(task.question or "")
    retrieved_plans = await retriever.retrieve_plan(task_text, top_k=3)
    plan = str(retrieved_plans[0].get("plan") or "") if retrieved_plans else ""
    if plan:
        raw_skills = await retriever.retrieve_skills_for_plan(
            plan=plan,
            skills_per_step=skills_per_step,
            tool_filter={"openpyxl"},
        )
    else:
        raw_skills = await retriever.retrieve_skills(
            query=task_text,
            skill_type="all",
            top_k=max_skills * 2,
            tool_filter={"openpyxl"},
        )
    selected_skills = raw_skills[:max_skills]
    store = _skillx_selection_to_store(
        task_id=str(task.task_id),
        plan=plan,
        retrieved_plans=retrieved_plans,
        selected_skills=selected_skills,
    )
    metadata = {
        "retrieved_plans": retrieved_plans,
        "selected_skills": selected_skills,
        "raw_retrieved_skill_names": [skill.get("name") for skill in raw_skills],
        "selected_skill_names": [skill.get("name") for skill in selected_skills],
        "artifact_count": len(store.all()),
    }
    return store, metadata


def _safe_name(text: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower()).strip("_")
    if not name:
        name = "skillx_spreadsheet_skill"
    return name[:96]


async def _export_train_trajectories(
    *,
    train_tasks: List[BenchmarkTask],
    args: argparse.Namespace,
    train_details_path: Path,
    trajectories_path: Path,
) -> List[Dict[str, Any]]:
    if train_details_path.exists() and trajectories_path.exists() and not args.force_train_export:
        return [_trajectory_from_detail(detail) for detail in _load_json(train_details_path).get("details", [])]

    details = await _run_spreadsheet_baseline(
        train_tasks,
        1,
        args.llm_config,
        ArtifactStore(),
        model_name=args.model_name,
        concurrency=args.train_concurrency,
        max_task_seconds=args.max_task_seconds,
        llm_request_timeout_s=args.llm_timeout_s,
        top_k_skills=0,
        min_skill_score=0.0,
        skill_injector_mode="off",
        execution_mode=args.spreadsheet_execution_mode,
        max_turns=args.spreadsheet_max_turns,
        partial_output=train_details_path.with_name(f"{train_details_path.stem}_partial.json"),
    )
    _write_json(train_details_path, {"benchmark": "spreadsheet", "mode": "skillx_train_export", "details": details})
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

    trajectories, skipped = _filter_extraction_trajectories(
        trajectories,
        max_chars=args.skillx_max_extraction_trajectory_chars,
    )
    _write_json(extraction_dir / "skillx_extraction_trajectory_filter.json", {
        "kept_count": len(trajectories),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "max_chars": args.skillx_max_extraction_trajectory_chars,
    })
    if not trajectories:
        library = SkillLibrary(benchmark="spreadsheet")
        library.save(str(library_path))
        return library

    _register_spreadsheet_skillx_prompts()
    pipeline = IterativeSkillPipeline(
        llm=_make_skillx_llm(args),
        benchmark="spreadsheet",
        skill_type=args.skillx_skill_type,
        plan_strategy=args.skillx_plan_strategy,
        output_dir=str(extraction_dir),
        verbose=True,
    )
    for component_name in ("plan_extractor", "skill_extractor", "filter_pipeline", "merger", "plan_combiner"):
        component = getattr(pipeline, component_name, None)
        if component is not None and hasattr(component, "max_retries"):
            setattr(component, "max_retries", max(1, int(args.skillx_component_retries or 1)))
    if hasattr(pipeline.skill_extractor, "functional_extractor"):
        pipeline.skill_extractor.functional_extractor.max_retries = max(1, int(args.skillx_component_retries or 1))
    if hasattr(pipeline.skill_extractor, "atomic_extractor"):
        pipeline.skill_extractor.atomic_extractor.max_retries = max(1, int(args.skillx_component_retries or 1))
    pipeline.filter_pipeline.skip_stage2 = True
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


async def _run_skillx_test(
    *,
    test_tasks: List[BenchmarkTask],
    library: SkillLibrary,
    args: argparse.Namespace,
    test_result_path: Path,
) -> Dict[str, Any]:
    if test_result_path.exists() and not args.force_test:
        return _load_json(test_result_path)

    embedding_service = EmbeddingService(base_url=args.embedding_url)
    retriever = SkillRetriever(
        skill_library=library,
        embedding_service=embedding_service,
        similarity_threshold=args.skillx_similarity_threshold,
    )
    details = await _run_spreadsheet_skillx_selected_test(
        test_tasks=test_tasks,
        retriever=retriever,
        args=args,
    )
    summary = _aggregate("spreadsheet", "skillx_aligned_test", args.tag, args.llm_config, args.train_size, details)
    payload = {
        "benchmark": "spreadsheet",
        "mode": "skillx_aligned_test",
        "tag": args.tag,
        "llm_config": args.llm_config,
        "model_name": args.model_name,
        "config_summary": {
            "skillx_root": str(SKILLX_ROOT),
            "train_size": args.train_size,
            "test_size": len(test_tasks),
            "spreadsheet_execution_mode": args.spreadsheet_execution_mode,
            "spreadsheet_max_turns": args.spreadsheet_max_turns,
            "skillx_skill_type": args.skillx_skill_type,
            "skillx_usage": "skillx_embedding_retrieval_then_spreadsheet_reference_prompt",
            "skillx_max_skills": args.skillx_max_skills,
            "skillx_similarity_threshold": args.skillx_similarity_threshold,
        },
        "test_summary": {k: v for k, v in summary.items() if k != "details"},
        "details": details,
        "skillx_library_summary": {
            "planning": len(library.planning),
            "functional": len(library.functional),
            "atomic": len(library.atomic),
        },
    }
    _write_json(test_result_path, payload)
    return payload


async def _run_spreadsheet_skillx_selected_test(
    *,
    test_tasks: List[BenchmarkTask],
    retriever: SkillRetriever,
    args: argparse.Namespace,
) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    if args.partial_test_output and args.partial_test_output.exists():
        try:
            payload = _load_json(args.partial_test_output)
            existing = payload.get("details", [])
            if isinstance(existing, list):
                details = [
                    item
                    for item in existing
                    if isinstance(item, dict) and item.get("task_id")
                ]
        except Exception:
            details = []
    detail_by_task_id = {str(item.get("task_id")): item for item in details}
    remaining = [
        (task_index, task)
        for task_index, task in enumerate(test_tasks)
        if str(task.task_id) not in detail_by_task_id
    ]

    def write_partial() -> None:
        if not args.partial_test_output:
            return
        ordered_details = [
            detail_by_task_id[task_id]
            for task_id in [str(task.task_id) for task in test_tasks]
            if task_id in detail_by_task_id
        ]
        _write_json(
            args.partial_test_output,
            {
                "benchmark": "spreadsheet",
                "mode": "skillx_aligned_test_partial",
                "completed_tasks": len(ordered_details),
                "total_tasks": len(test_tasks),
                "details": ordered_details,
            },
        )

    async def run_one(task: BenchmarkTask, task_index: int) -> Tuple[int, Dict[str, Any]]:
        runs: List[Dict[str, Any]] = []
        retrieval_store, retrieval_metadata = await _retrieve_skillx_for_task(
            retriever=retriever,
            task=task,
            max_skills=args.skillx_max_skills,
            skills_per_step=args.skillx_skills_per_plan_step,
        )
        task_store = copy.deepcopy(retrieval_store)
        for run_idx in range(1):
            try:
                if args.spreadsheet_execution_mode == "notebook":
                    coro = run_spreadsheet_task_notebook(
                        task,
                        llm_config=args.llm_config,
                        model_name=args.model_name,
                        artifact_store=task_store,
                        top_k_skills=args.skillx_max_skills + 1,
                        min_skill_score=0.0,
                        skill_injector_mode="off",
                        skill_context_budget_chars=args.skill_context_budget_chars,
                        max_turns=args.spreadsheet_max_turns,
                        llm_request_timeout_s=args.llm_timeout_s,
                    )
                elif args.spreadsheet_execution_mode == "bash_react":
                    coro = run_spreadsheet_task_bash_react(
                        task,
                        llm_config=args.llm_config,
                        model_name=args.model_name,
                        artifact_store=task_store,
                        top_k_skills=args.skillx_max_skills + 1,
                        min_skill_score=0.0,
                        skill_injector_mode="off",
                        skill_context_budget_chars=args.skill_context_budget_chars,
                        max_turns=args.spreadsheet_max_turns,
                        llm_request_timeout_s=args.llm_timeout_s,
                    )
                else:
                    coro = run_spreadsheet_task(
                        task,
                        llm_config=args.llm_config,
                        model_name=args.model_name,
                        artifact_store=task_store,
                        top_k_skills=args.skillx_max_skills + 1,
                        min_skill_score=0.0,
                        skill_injector_mode="off",
                        skill_context_budget_chars=args.skill_context_budget_chars,
                        llm_request_timeout_s=args.llm_timeout_s,
                    )
                result = await asyncio.wait_for(coro, timeout=args.max_task_seconds) if args.max_task_seconds else await coro
            except asyncio.TimeoutError:
                result = BenchmarkResult(
                    benchmark="spreadsheet",
                    task_id=task.task_id,
                    success=False,
                    score=0.0,
                    metrics={
                        "exception": "TaskTimeout",
                        "max_task_seconds": args.max_task_seconds,
                        "execution_mode": args.spreadsheet_execution_mode,
                    },
                    trace={
                        "task_id": task.task_id,
                        "timed_out": True,
                        "execution_mode": args.spreadsheet_execution_mode,
                    },
                    error=f"Task exceeded {args.max_task_seconds} seconds",
                )
            except Exception as exc:
                result = BenchmarkResult(
                    benchmark="spreadsheet",
                    task_id=task.task_id,
                    success=False,
                    score=0.0,
                    metrics={
                        "exception": type(exc).__name__,
                        "execution_mode": args.spreadsheet_execution_mode,
                    },
                    trace={
                        "task_id": task.task_id,
                        "execution_mode": args.spreadsheet_execution_mode,
                    },
                    error=str(exc),
                )
            item = result.as_dict()
            item["run_idx"] = run_idx
            item.setdefault("metrics", {})["skillx_retrieval"] = retrieval_metadata
            item.setdefault("trace", {})["skillx_retrieval"] = retrieval_metadata
            runs.append(item)
            metrics = item.get("metrics") or {}
            print(
                json.dumps(
                    {
                        "progress": "spreadsheet_skillx_task_run",
                        "task_index": task_index,
                        "n_tasks": len(test_tasks),
                        "task_id": task.task_id,
                        "run_idx": run_idx,
                        "score": item.get("score"),
                        "success": item.get("success"),
                        "elapsed_s": metrics.get("elapsed_s"),
                        "skillx_selected": retrieval_metadata.get("selected_skill_names"),
                        "error": item.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return task_index, _task_runs(task, runs)

    if not remaining:
        return [
            detail_by_task_id[task_id]
            for task_id in [str(task.task_id) for task in test_tasks]
            if task_id in detail_by_task_id
        ]
    sem = asyncio.Semaphore(max(1, int(args.test_concurrency or 1)))

    async def guarded(task: BenchmarkTask, task_index: int) -> Tuple[int, Dict[str, Any]]:
        async with sem:
            return await run_one(task, task_index)

    pending = [
        asyncio.create_task(guarded(task, task_index))
        for task_index, task in remaining
    ]
    for completed_task in asyncio.as_completed(pending):
        _idx, detail = await completed_task
        detail_by_task_id[str(detail.get("task_id"))] = detail
        write_partial()
    return [
        detail_by_task_id[task_id]
        for task_id in [str(task.task_id) for task in test_tasks]
        if task_id in detail_by_task_id
    ]


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Run aligned SkillX baseline on SpreadsheetBench")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR / "skillx_spreadsheet_aligned")
    parser.add_argument("--tag", default="skillx_spreadsheet_50_50_20260522")
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "data/benchmarks/spreadsheet")
    parser.add_argument("--train-task-ids", type=Path, default=PROJECT_ROOT / "academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json")
    parser.add_argument("--test-task-ids", type=Path, default=PROJECT_ROOT / "academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json")
    parser.add_argument("--train-size", type=int, default=50)
    parser.add_argument("--test-size", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--llm-config", default="local_claude_proxy")
    parser.add_argument("--model-name", default="claude-sonnet-4-5")
    parser.add_argument("--openai-base-url", default="http://127.0.0.1:4000/v1")
    parser.add_argument("--api-key", default="1234abcd")
    parser.add_argument("--embedding-url", default="http://127.0.0.1:7000")
    parser.add_argument("--spreadsheet-execution-mode", choices=["single", "notebook", "bash_react"], default="bash_react")
    parser.add_argument("--spreadsheet-max-turns", type=int, default=20)
    parser.add_argument("--max-task-seconds", type=float, default=180.0)
    parser.add_argument("--llm-timeout-s", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--train-concurrency", type=int, default=4)
    parser.add_argument("--test-concurrency", type=int, default=4)
    parser.add_argument("--skill-context-budget-chars", type=int, default=6000)
    parser.add_argument("--min-skill-score", type=float, default=0.0)
    parser.add_argument("--skillx-max-tokens", type=int, default=32768)
    parser.add_argument("--skillx-retries", type=int, default=3)
    parser.add_argument("--skillx-component-retries", type=int, default=1)
    parser.add_argument("--skillx-skill-type", choices=["functional", "atomic", "hybrid"], default="functional")
    parser.add_argument("--skillx-plan-strategy", choices=["shortest", "merge"], default="shortest")
    parser.add_argument("--skillx-epochs", type=int, default=1)
    parser.add_argument("--skillx-filter-threshold", type=float, default=0.999)
    parser.add_argument("--skillx-batch-size", type=int, default=10)
    parser.add_argument("--skillx-max-concurrent", type=int, default=5)
    parser.add_argument("--skillx-filter-timing", choices=["pre_merge", "post_merge", "both", "none"], default="pre_merge")
    parser.add_argument("--skillx-max-skills", type=int, default=10)
    parser.add_argument("--skillx-skills-per-plan-step", type=int, default=4)
    parser.add_argument("--skillx-similarity-threshold", type=float, default=0.45)
    parser.add_argument("--skillx-max-extraction-trajectory-chars", type=int, default=30000)
    parser.add_argument("--force-train-export", action="store_true")
    parser.add_argument("--force-skillx-extraction", action="store_true")
    parser.add_argument("--force-test", action="store_true")
    parser.add_argument("--skip-embedding-preflight", action="store_true")
    args = parser.parse_args()

    os.environ["OPENAI_API_KEY"] = args.api_key
    os.environ["OPENAI_BASE_URL"] = args.openai_base_url
    _ensure_local_claude_proxy(args.openai_base_url)
    if not args.skip_embedding_preflight:
        _assert_embedding_service(args.embedding_url)

    pool = load_spreadsheet_task_pool(cache_dir=args.cache_dir, split_seed=args.seed)
    train_ids = _load_task_ids(args.train_task_ids)[: args.train_size]
    test_ids = _load_task_ids(args.test_task_ids)[: args.test_size]
    train_tasks = _select_tasks(pool, train_ids)
    test_tasks = _select_tasks(pool, test_ids)

    out_dir = args.output_dir / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    args.partial_test_output = out_dir / "skillx_test_partial.json"
    _write_json(
        out_dir / "run_config.json",
        {
            "args": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key != "partial_test_output"},
            "skillx_root": str(SKILLX_ROOT),
            "train_task_ids": [task.task_id for task in train_tasks],
            "test_task_ids": [task.task_id for task in test_tasks],
        },
    )

    t0 = time.monotonic()
    trajectories = await _export_train_trajectories(
        train_tasks=train_tasks,
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
        library=library,
        args=args,
        test_result_path=out_dir / "skillx_spreadsheet_test_result.json",
    )
    result["elapsed_s_total"] = round(time.monotonic() - t0, 3)
    _write_json(out_dir / "skillx_spreadsheet_result_with_elapsed.json", result)
    print(json.dumps({"output_dir": str(out_dir), "result": str(out_dir / "skillx_spreadsheet_test_result.json")}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
