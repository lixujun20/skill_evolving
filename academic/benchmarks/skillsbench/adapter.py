"""Lightweight SkillsBench adapter for skill-pool diagnostics.

This is not the official Harbor pass-rate runner. It maps SkillsBench skill
pools into the benchmark-agnostic ``SkillArtifact`` format and runs local
retrieval/selection diagnostics against either the bundled fixture or a local
SkillsBench checkout.
"""
from __future__ import annotations

import json
import re
import time
import tomllib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.cost_accounting import make_cost_event
from academic.benchmarks.core.llm_text import TextLLMResponse, ask_text_llm
from academic.benchmarks.core.skill_injector import BudgetSkillInjector
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact, SkillInterface
from academic.refactoring_lab.skillsbench_fixture import SkillsBenchFixtureTask, load_skillsbench_fixture


DEFAULT_SKILLSBENCH_ROOT = Path("/home/lixujun/skillsbench")

SKILLSBENCH_SYSTEM = """You are a SkillsBench skill-use diagnostic agent.

You receive a task instruction and a small set of retrieved skill cards. Select
the skill cards that are directly useful for solving the task, then provide a
brief execution plan. Do not claim that an irrelevant skill is useful.

Return strict JSON only:
{
  "selected_skill_names": ["skill_name"],
  "plan": "brief plan",
  "confidence": 0.0,
  "reason": "why these skills match"
}
"""


def load_skillsbench_tasks(
    *,
    skillsbench_root: Path = DEFAULT_SKILLSBENCH_ROOT,
    source: str = "fixture",
    limit: int | None = None,
    offset: int = 0,
) -> List[BenchmarkTask]:
    """Load SkillsBench tasks into the generic benchmark task shape.

    ``source="fixture"`` uses the compact local fixture. ``source="tasks"`` and
    ``source="tasks-no-skills"`` read task metadata from a local SkillsBench
    checkout, without invoking Harbor.
    """

    source = (source or "fixture").strip().lower()
    if source == "fixture":
        tasks = [_task_from_fixture(row) for row in load_skillsbench_fixture(max_tasks=None)]
    elif source in {"tasks", "tasks-no-skills", "tasks_no_skills"}:
        folder = "tasks-no-skills" if source in {"tasks-no-skills", "tasks_no_skills"} else "tasks"
        task_dir = skillsbench_root / folder
        tasks = [_task_from_checkout_dir(path) for path in sorted(task_dir.iterdir()) if path.is_dir()]
    else:
        raise ValueError(f"Unknown SkillsBench task source: {source}")
    start = max(0, int(offset or 0))
    end = None if limit is None else start + max(0, int(limit))
    return tasks[start:end]


def load_skillsbench_skill_artifacts(
    *,
    skillsbench_root: Path = DEFAULT_SKILLSBENCH_ROOT,
    pool: str = "curated",
    limit: int | None = None,
) -> List[SkillArtifact]:
    """Load official/curated SkillsBench skill metadata as SkillArtifacts."""

    pool = (pool or "curated").strip().lower()
    if pool == "official":
        path = skillsbench_root / "docs" / "skills-research" / "official_skills.json"
    elif pool == "curated":
        path = skillsbench_root / "docs" / "skills-research" / "curated_skills.json"
    else:
        raise ValueError(f"Unknown SkillsBench skill pool: {pool}")
    raw = json.loads(path.read_text())
    rows = _flatten_skill_rows(raw)
    if limit is not None:
        rows = rows[: max(0, int(limit))]
    return [_skill_artifact_from_row(row, pool=pool) for row in rows]


def load_skillsbench_fixture_skill_artifacts(
    *,
    max_tasks: int | None = None,
) -> List[SkillArtifact]:
    """Build oracle diagnostic skill cards from the bundled fixture.

    These are not official SkillsBench skills. They are used for mock tests and
    plumbing checks where every fixture task has one known matching skill.
    """

    return [_skill_artifact_from_fixture(row) for row in load_skillsbench_fixture(max_tasks=max_tasks)]


def default_skillsbench_skill_store(
    *,
    skillsbench_root: Path = DEFAULT_SKILLSBENCH_ROOT,
    pool: str = "curated",
    limit: int | None = None,
) -> ArtifactStore:
    pool = (pool or "curated").strip().lower()
    if pool in {"none", "empty"}:
        return ArtifactStore()
    if pool in {"fixture", "fixture_oracle", "oracle"}:
        return ArtifactStore(load_skillsbench_fixture_skill_artifacts(max_tasks=limit))
    return ArtifactStore(
        load_skillsbench_skill_artifacts(
            skillsbench_root=skillsbench_root,
            pool=pool,
            limit=limit,
        )
    )


def run_skillsbench_fixture_retrieval_diagnostic(
    *,
    skillsbench_root: Path = DEFAULT_SKILLSBENCH_ROOT,
    pool: str = "curated",
    max_tasks: int | None = None,
    top_k: int = 5,
    skill_limit: int | None = None,
) -> Dict[str, Any]:
    """Run a local lexical retrieval diagnostic against fixture tasks."""

    tasks = load_skillsbench_fixture(max_tasks=max_tasks)
    artifacts = load_skillsbench_skill_artifacts(
        skillsbench_root=skillsbench_root,
        pool=pool,
        limit=skill_limit,
    )
    store = ArtifactStore(artifacts)
    per_task: List[Dict[str, Any]] = []
    hit_at_1 = 0
    hit_at_k = 0
    for task in tasks:
        query = f"{task.instruction}\ntags: {', '.join(task.tags)}\ncategory: {task.category}"
        audit = store.retrieve_audit(query, top_k=top_k, min_score=0.0)
        selected = list(audit.get("selected") or [])
        selected_names = [str(row.get("name") or "") for row in selected]
        tag_hits = [
            name
            for name in selected_names
            if _task_skill_tag_hit(task.tags, store.get(name))
        ]
        if tag_hits and selected_names and selected_names[0] == tag_hits[0]:
            hit_at_1 += 1
        if tag_hits:
            hit_at_k += 1
        per_task.append(
            {
                "task_id": task.task_id,
                "category": task.category,
                "tags": list(task.tags),
                "retrieved_skill_names": selected_names,
                "tag_hit_at_1": bool(tag_hits and selected_names and selected_names[0] == tag_hits[0]),
                "tag_hit_at_k": bool(tag_hits),
            }
        )
    return {
        "benchmark": "skillsbench_fixture_retrieval_diagnostic",
        "official_pass_rate": None,
        "diagnostic_only": True,
        "pool": pool,
        "n_tasks": len(tasks),
        "n_skills": len(artifacts),
        "top_k": top_k,
        "summary": {
            "tag_hit_at_1": round(hit_at_1 / max(len(tasks), 1), 4),
            "tag_hit_at_k": round(hit_at_k / max(len(tasks), 1), 4),
        },
        "per_task": per_task,
    }


async def run_skillsbench_task(
    task: BenchmarkTask,
    *,
    llm_config: str,
    artifact_store: ArtifactStore,
    model_name: str | None = None,
    top_k_skills: int = 5,
    min_skill_score: float = 0.0,
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
    max_request_wall_s: float | None = None,
) -> BenchmarkResult:
    """Run a SkillsBench skill-selection diagnostic.

    This does not execute the Harbor sandbox. It validates whether the
    retrieval/injection/selection path surfaces a task-relevant skill.
    """

    t0 = time.monotonic()
    instruction = str(task.question or "")
    query = _skillsbench_query(task)
    retrieval_audit = artifact_store.retrieve_audit(
        query,
        top_k=top_k_skills,
        min_score=min_skill_score,
        debug_context={
            "benchmark": "skillsbench",
            "tags": list(task.metadata.get("tags") or []),
            "category": task.metadata.get("category", ""),
        },
    )
    retrieved = [
        artifact_store.get(str(row.get("name") or ""))
        for row in retrieval_audit.get("selected", [])
    ]
    retrieved_artifacts = [artifact for artifact in retrieved if artifact is not None]
    injector = BudgetSkillInjector(
        mode=skill_injector_mode or "compact",
        budget_chars=skill_context_budget_chars or 2200,
        max_full_skills=1,
        max_summary_skills=max(0, top_k_skills),
    )
    injection = injector.select(retrieved_artifacts, query=query)
    response = await _ask_or_mock_skillsbench_llm(
        llm_config=llm_config,
        model_name=model_name,
        instruction=instruction,
        injected_skills=injection.prompt(),
        injected_skill_names=[item.artifact.name for item in injection.injected],
        task=task,
        max_request_wall_s=max_request_wall_s,
    )
    payload = _parse_json_object(response.content)
    selected_names = [
        str(item).strip()
        for item in (payload.get("selected_skill_names") or [])
        if str(item).strip()
    ]
    relevant_selected = [
        name
        for name in selected_names
        if _task_skill_match(task, artifact_store.get(name))
    ]
    relevant_retrieved = [
        artifact.name
        for artifact in retrieved_artifacts
        if _task_skill_match(task, artifact)
    ]
    success = bool(relevant_selected)
    score = 1.0 if success else (0.5 if relevant_retrieved else 0.0)
    total_tokens = response.prompt_tokens + response.cache_input_tokens + response.completion_tokens
    metrics = {
        "diagnostic_only": True,
        "official_pass_rate": None,
        "retrieval_hit_at_k": bool(relevant_retrieved),
        "selection_hit": success,
        "retrieved_skills": [artifact.name for artifact in retrieved_artifacts],
        "prompt_injected_skills": [item.artifact.name for item in injection.injected],
        "selected_skill_names": selected_names,
        "relevant_retrieved_skills": relevant_retrieved,
        "relevant_selected_skills": relevant_selected,
        "input_tokens": response.prompt_tokens,
        "cache_input_tokens": response.cache_input_tokens,
        "completion_tokens": response.completion_tokens,
        "total_tokens": total_tokens,
        "cost_events": [
            make_cost_event(
                role="skillsbench_selector",
                phase="test",
                benchmark="skillsbench",
                task_id=task.task_id,
                model=response.model_name or model_name or llm_config,
                llm_config=llm_config,
                input_tokens=response.prompt_tokens,
                cache_input_tokens=response.cache_input_tokens,
                output_tokens=response.completion_tokens,
                prompt_chars=len(instruction) + len(injection.prompt()),
                skill_prompt_chars=len(injection.prompt()),
                system_prompt_chars=len(SKILLSBENCH_SYSTEM),
                metadata={"api_style": response.api_style},
            )
        ],
        "elapsed_s": round(time.monotonic() - t0, 3),
        "skill_injector": injection.as_event(),
    }
    return BenchmarkResult(
        benchmark="skillsbench",
        task_id=task.task_id,
        success=success,
        score=score,
        metrics=metrics,
        trace={
            "task": task.as_dict(),
            "retrieval_audit": retrieval_audit,
            "skill_injection": injection.as_event(),
            "llm_response": response.content,
            "parsed_response": payload,
        },
    )


def _flatten_skill_rows(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        rows: List[Dict[str, Any]] = []
        for value in raw.values():
            if isinstance(value, list):
                rows.extend(dict(item) for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                rows.append(dict(value))
        return rows
    return []


def _skill_artifact_from_row(row: Dict[str, Any], *, pool: str) -> SkillArtifact:
    name = str(row.get("name") or "").strip().replace("-", "_")
    description = str(row.get("description") or _frontmatter_description(row.get("content")) or "").strip()
    content = str(row.get("content") or description or "").strip()
    source = str(row.get("source") or "").strip()
    tags = _infer_tags(name=name, description=description, source=source)
    return SkillArtifact(
        name=f"skillsbench_{name}",
        kind="skillsbench_official_skill_card",
        description=description[:1000] or name,
        body=content[:4000] or description,
        interface=SkillInterface(
            summary=description[:240] or name,
            usage="Retrieve when the SkillsBench task intent matches this skill description.",
            input_contract={"benchmark": "SkillsBench"},
            output_contract={"injection": "skill_guidance"},
            invocation_contract={"injection_type": "informational"},
            compatibility_notes="Diagnostic retrieval only; official execution requires Harbor runner.",
        ),
        metadata={
            "domains": ["SkillsBench"],
            "source": source,
            "url": row.get("url"),
            "path": row.get("path"),
            "skill_pool": pool,
            "intent_keywords": tags,
            "source_task_ids": _source_task_ids_from_row(row),
            "retrieval_diagnostic_only": True,
        },
        tags=[f"intent:{tag}" for tag in tags],
    )


def _skill_artifact_from_fixture(row: SkillsBenchFixtureTask) -> SkillArtifact:
    name = f"skillsbench_fixture_{_slug(row.task_id).replace('-', '_')}"
    body = (
        f"Task family: {row.task_id}\n"
        f"Category: {row.category}\n"
        f"Tags: {', '.join(row.tags)}\n\n"
        f"Reusable skill summary:\n{row.skill_docstring}\n\n"
        f"Reference callable shape:\n{row.skill_code}"
    )
    return SkillArtifact(
        name=name,
        kind="skillsbench_fixture_oracle_skill",
        description=row.skill_docstring[:1000] or row.task_id,
        body=body,
        interface=SkillInterface(
            summary=f"Use for SkillsBench task family {row.task_id}.",
            usage="Retrieve when the task id, category, or tags match this fixture task family.",
            input_contract={"benchmark": "SkillsBench", "task_id": row.task_id, "tags": list(row.tags)},
            output_contract={"diagnostic": "select matching skill card"},
            invocation_contract={"injection_type": "informational"},
            compatibility_notes="Fixture diagnostic only; not an official Harbor skill package.",
        ),
        metadata={
            "domains": ["SkillsBench"],
            "source": "skillsbench_fixture",
            "skill_pool": "fixture_oracle",
            "source_task_ids": [row.task_id],
            "category": row.category,
            "difficulty": row.difficulty,
            "intent_keywords": [_slug(row.task_id), row.category, *row.tags],
            "retrieval_diagnostic_only": True,
        },
        tags=[
            f"intent:{_slug(row.task_id)}",
            f"category:{_slug(row.category)}",
            *[f"intent:{_slug(tag)}" for tag in row.tags],
        ],
    )


def _task_from_fixture(row: SkillsBenchFixtureTask) -> BenchmarkTask:
    return BenchmarkTask(
        benchmark="skillsbench",
        task_id=row.task_id,
        question=row.instruction,
        expected={"matching_task_id": row.task_id, "tags": list(row.tags)},
        metadata={
            "source": "fixture",
            "category": row.category,
            "tags": list(row.tags),
            "difficulty": row.difficulty,
            "skill_docstring": row.skill_docstring,
        },
    )


def _task_from_checkout_dir(path: Path) -> BenchmarkTask:
    instruction_path = path / "instruction.md"
    task_toml_path = path / "task.toml"
    metadata: Dict[str, Any] = {"source": path.parent.name, "path": str(path)}
    if task_toml_path.exists():
        raw = tomllib.loads(task_toml_path.read_text())
        meta = raw.get("metadata") or {}
        metadata.update(
            {
                "category": meta.get("category", ""),
                "tags": list(meta.get("tags") or []),
                "difficulty": meta.get("difficulty", ""),
                "environment": raw.get("environment") or {},
                "agent": raw.get("agent") or {},
                "verifier": raw.get("verifier") or {},
            }
        )
    return BenchmarkTask(
        benchmark="skillsbench",
        task_id=path.name,
        question=instruction_path.read_text() if instruction_path.exists() else "",
        expected={"matching_task_id": path.name, "tags": list(metadata.get("tags") or [])},
        input_artifacts={"task_dir": str(path)},
        metadata=metadata,
    )


def _frontmatter_description(content: Any) -> str:
    text = str(content or "")
    if not text.startswith("---"):
        return ""
    head = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    for line in head.splitlines():
        if line.strip().startswith("description:"):
            return line.split(":", 1)[1].strip()
    return ""


def _infer_tags(*, name: str, description: str, source: str) -> List[str]:
    raw = f"{name} {description} {source}".lower().replace("-", " ")
    tags: List[str] = []
    for token in raw.split():
        token = "".join(ch for ch in token if ch.isalnum() or ch in {"_", "/"})
        if len(token) >= 4 and token not in tags:
            tags.append(token)
        if len(tags) >= 24:
            break
    return tags


def _task_skill_tag_hit(task_tags: Iterable[str], artifact: SkillArtifact | None) -> bool:
    if artifact is None:
        return False
    task_terms = {str(tag).lower().replace("-", "_") for tag in task_tags or []}
    text = f"{artifact.name} {artifact.description} {' '.join(artifact.metadata.get('intent_keywords') or [])}".lower()
    text = text.replace("-", "_")
    return any(term and term in text for term in task_terms)


def _skillsbench_query(task: BenchmarkTask) -> str:
    return (
        f"task_id: {task.task_id}\n"
        f"category: {task.metadata.get('category', '')}\n"
        f"tags: {', '.join(str(item) for item in (task.metadata.get('tags') or []))}\n"
        f"instruction:\n{task.question}"
    )


async def _ask_or_mock_skillsbench_llm(
    *,
    llm_config: str,
    model_name: str | None,
    instruction: str,
    injected_skills: str,
    injected_skill_names: Sequence[str],
    task: BenchmarkTask,
    max_request_wall_s: float | None,
) -> TextLLMResponse:
    if str(llm_config).startswith("mock"):
        selected = [name for name in injected_skill_names if _mock_name_matches_task(name, task)]
        if not selected and injected_skill_names:
            selected = [str(injected_skill_names[0])]
        content = json.dumps(
            {
                "selected_skill_names": selected[:2],
                "plan": "Use the selected matching SkillsBench skill card to guide the task.",
                "confidence": 0.9 if selected else 0.0,
                "reason": "mock lexical/source-task match",
            },
            ensure_ascii=False,
        )
        prompt_tokens = max(1, (len(instruction) + len(injected_skills)) // 4)
        completion_tokens = max(1, len(content) // 4)
        return TextLLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_name=model_name or "mock-skillsbench-selector",
            api_style="mock",
        )
    prompt = (
        f"Task instruction:\n{instruction}\n\n"
        f"Retrieved skill cards:\n{injected_skills}\n"
    )
    return await ask_text_llm(
        llm_config=llm_config,
        model_name=model_name,
        system=SKILLSBENCH_SYSTEM,
        prompt=prompt,
        temperature=0.0,
        max_request_wall_s=max_request_wall_s,
    )


def _parse_json_object(text: str) -> Dict[str, Any]:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", value, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


def _task_skill_match(task: BenchmarkTask, artifact: SkillArtifact | None) -> bool:
    if artifact is None:
        return False
    source_task_ids = {str(item) for item in (artifact.metadata.get("source_task_ids") or [])}
    if task.task_id in source_task_ids:
        return True
    return _task_skill_tag_hit(task.metadata.get("tags") or [], artifact)


def _mock_name_matches_task(name: str, task: BenchmarkTask) -> bool:
    slug = _slug(task.task_id).replace("-", "_")
    return bool(slug and slug in _slug(name).replace("-", "_"))


def _source_task_ids_from_row(row: Dict[str, Any]) -> List[str]:
    values = row.get("source_task_ids") or row.get("task_ids") or []
    if isinstance(values, str):
        values = [values]
    out = [str(item).strip() for item in values if str(item).strip()]
    path = str(row.get("path") or "")
    parts = Path(path).parts
    if path.startswith("skills/") and len(parts) > 1:
        out.append(str(parts[1]))
    return sorted({item for item in out if item})


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
