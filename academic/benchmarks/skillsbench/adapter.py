"""Lightweight SkillsBench adapter for skill-pool diagnostics.

This is not the official Harbor pass-rate runner. It maps SkillsBench skill
pools into the benchmark-agnostic `SkillArtifact` format and runs local
retrieval diagnostics against the small fixture bundled in this repository.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.types import SkillArtifact, SkillInterface
from academic.refactoring_lab.skillsbench_fixture import load_skillsbench_fixture


DEFAULT_SKILLSBENCH_ROOT = Path("/home/lixujun/skillsbench")


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


def run_skillsbench_fixture_retrieval_diagnostic(
    *,
    skillsbench_root: Path = DEFAULT_SKILLSBENCH_ROOT,
    pool: str = "curated",
    max_tasks: int | None = None,
    top_k: int = 5,
    skill_limit: int | None = None,
) -> Dict[str, Any]:
    """Run a local lexical retrieval diagnostic against fixture tasks.

    The fixture does not contain official SkillsBench verification logic, so
    this reports retrieval/tag coverage only.
    """

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
            "retrieval_diagnostic_only": True,
        },
        tags=[f"intent:{tag}" for tag in tags],
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
