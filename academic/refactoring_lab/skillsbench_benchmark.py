from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from sqlmodel import Session, SQLModel, select
from sqlalchemy import delete, text

from academic.refactoring_lab.skillsbench_fixture import (
    SkillsBenchFixtureTask,
    load_skillsbench_fixture,
    summarize_skillsbench_fixture,
)
from app.meta_agent.skills.database.manager import SkillDatabaseManager
from app.meta_agent.skills.database.models import Skill, SkillGroup
from app.meta_agent.skills.retrieval import SkillRetriever

BENCHMARK_GROUP_PREFIX = "skillsbench_fixture::"
EMBEDDING_COST_PER_1K_TOKENS_USD = 0.00007


@dataclass
class RetrievalMetrics:
    total: int = 0
    hit_at_1: int = 0
    hit_at_5: int = 0
    query_costs: List[float] = field(default_factory=list)
    query_latencies_ms: List[float] = field(default_factory=list)

    @property
    def recall_at_1(self) -> float:
        return self.hit_at_1 / self.total if self.total else 0.0

    @property
    def recall_at_5(self) -> float:
        return self.hit_at_5 / self.total if self.total else 0.0

    @property
    def precision_at_5(self) -> float:
        return self.hit_at_5 / (self.total * 5) if self.total else 0.0

    @property
    def f1_at_5(self) -> float:
        p, r = self.precision_at_5, self.recall_at_5
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def avg_cost_usd(self) -> float:
        return sum(self.query_costs) / len(self.query_costs) if self.query_costs else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.query_latencies_ms) / len(self.query_latencies_ms) if self.query_latencies_ms else 0.0


def _retrieve_scoped_skills(
    manager: SkillDatabaseManager,
    query_embedding: List[float],
    *,
    group_prefix: str,
    top_k: int,
    similarity_threshold: float,
) -> List[Skill]:
    with Session(manager.engine) as session:
        distance_expr = Skill.embedding.cosine_distance(query_embedding)
        statement = (
            select(Skill)
            .join(SkillGroup, Skill.group_id == SkillGroup.id)
            .where(SkillGroup.name.like(f"{group_prefix}%"))
            .where(Skill.embedding.isnot(None))
            .where(distance_expr <= (1 - similarity_threshold))
            .order_by(distance_expr)
            .limit(top_k)
        )
        return list(session.exec(statement))


def _insert_skill(
    manager: SkillDatabaseManager,
    task: SkillsBenchFixtureTask,
    embedding: List[float],
    *,
    group_prefix: str,
) -> Skill:
    with Session(manager.engine) as session:
        group_name = f"{group_prefix}{task.task_id}"
        group = session.exec(select(SkillGroup).where(SkillGroup.name == group_name)).first()
        if not group:
            group = SkillGroup(name=group_name)
            session.add(group)
            session.commit()
            session.refresh(group)
        skill = Skill(
            group_id=group.id,
            major_version=1,
            minor_version=0,
            code=task.skill_code,
            docstring=task.skill_docstring,
            interface_schema={},
            tags=task.tags,
            embedding=embedding,
        )
        session.add(skill)
        session.commit()
        session.refresh(skill)
        return skill


def _cleanup_benchmark_rows(manager: SkillDatabaseManager) -> None:
    _cleanup_benchmark_rows_for_prefix(manager, BENCHMARK_GROUP_PREFIX)


def _cleanup_benchmark_rows_for_prefix(
    manager: SkillDatabaseManager,
    group_prefix: str,
) -> None:
    with Session(manager.engine) as session:
        groups = list(
            session.exec(
                select(SkillGroup).where(
                    SkillGroup.name.like(f"{group_prefix}%")
                )
            )
        )
        if not groups:
            return
        group_ids = [group.id for group in groups if group.id is not None]
        if group_ids:
            session.exec(delete(Skill).where(Skill.group_id.in_(group_ids)))
            session.exec(delete(SkillGroup).where(SkillGroup.id.in_(group_ids)))
            session.commit()


async def _retrieve_for_query_in_fixture_scope(
    retriever: SkillRetriever,
    db: SkillDatabaseManager,
    query: str,
    *,
    group_prefix: str,
    top_k: int,
    similarity_threshold: float,
) -> Dict[str, object]:
    import time

    t0 = time.monotonic()
    response = retriever._client.embeddings.create(
        model=retriever._model,
        input=query,
        dimensions=1024,
    )
    query_embedding = response.data[0].embedding
    tokens = response.usage.total_tokens if response.usage else len(query.split())
    skills = _retrieve_scoped_skills(
        db,
        query_embedding,
        group_prefix=group_prefix,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000
    estimated_cost_usd = (tokens / 1000) * EMBEDDING_COST_PER_1K_TOKENS_USD
    return {
        "skills": skills,
        "elapsed_ms": elapsed_ms,
        "estimated_cost_usd": estimated_cost_usd,
    }


async def run_skillsbench_fixture_benchmark(
    *,
    db_url: str,
    max_tasks: Optional[int] = None,
    top_k: int = 5,
    similarity_threshold: float = 0.2,
    cleanup_after: bool = True,
    run_namespace: Optional[str] = None,
) -> Dict[str, object]:
    tasks = load_skillsbench_fixture(max_tasks=max_tasks)
    retriever = SkillRetriever()
    db = SkillDatabaseManager(db_url)
    run_namespace = run_namespace or uuid.uuid4().hex[:8]
    group_prefix = f"{BENCHMARK_GROUP_PREFIX}{run_namespace}::"
    with db.engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    SQLModel.metadata.create_all(db.engine)
    _cleanup_benchmark_rows_for_prefix(db, group_prefix)

    task_to_skill_id: Dict[str, int] = {}
    skill_id_to_task_id: Dict[int, str] = {}
    skipped = False
    skip_reason = ""
    metrics = RetrievalMetrics(total=len(tasks))
    per_query: List[Dict[str, object]] = []
    missed: List[str] = []
    rank1_missed: List[str] = []

    try:
        for task in tasks:
            embed_text = f"{task.skill_docstring}\ntags: {', '.join(task.tags)}"
            embedding = retriever.generate_embedding(embed_text)
            if embedding is None:
                skipped = True
                skip_reason = "embedding_api_unavailable"
                break
            skill = _insert_skill(db, task, embedding, group_prefix=group_prefix)
            task_to_skill_id[task.task_id] = skill.id
            skill_id_to_task_id[skill.id] = task.task_id

        if not skipped:
            for task in tasks:
                result = await _retrieve_for_query_in_fixture_scope(
                    retriever,
                    db,
                    task.instruction,
                    group_prefix=group_prefix,
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                )
                retrieved_ids = [s.id for s in result["skills"]]
                retrieved_task_ids = [
                    skill_id_to_task_id.get(skill_id, f"external::{skill_id}")
                    for skill_id in retrieved_ids
                ]
                expected_id = task_to_skill_id[task.task_id]
                hit_at_1 = bool(result["skills"] and result["skills"][0].id == expected_id)
                hit_at_5 = expected_id in retrieved_ids
                if hit_at_1:
                    metrics.hit_at_1 += 1
                else:
                    rank1_missed.append(task.task_id)
                if hit_at_5:
                    metrics.hit_at_5 += 1
                else:
                    missed.append(task.task_id)
                metrics.query_costs.append(result["estimated_cost_usd"])
                metrics.query_latencies_ms.append(result["elapsed_ms"])
                per_query.append({
                    "task_id": task.task_id,
                    "category": task.category,
                    "expected_skill_id": expected_id,
                    "expected_benchmark_task_id": task.task_id,
                    "retrieved_skill_ids": retrieved_ids,
                    "retrieved_benchmark_task_ids": retrieved_task_ids,
                    "hit_at_1": hit_at_1,
                    "hit_at_5": hit_at_5,
                    "elapsed_ms": round(result["elapsed_ms"], 3),
                    "estimated_cost_usd": result["estimated_cost_usd"],
                })
    finally:
        if cleanup_after:
            _cleanup_benchmark_rows_for_prefix(db, group_prefix)

    return {
        "benchmark": "skillsbench_fixture_retrieval",
        "source": "skillsbench_fixture",
        "fixture_summary": summarize_skillsbench_fixture(max_tasks=max_tasks),
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "metadata": {
            "benchmark_group_prefix": BENCHMARK_GROUP_PREFIX,
            "run_namespace": run_namespace,
            "run_group_prefix": group_prefix,
            "cleanup_before_run": True,
            "cleanup_after_run": cleanup_after,
            "strict_isolation_note": (
                "Benchmark-owned rows are isolated by run namespace and cleaned "
                "before and after each run. For strict isolation from unrelated "
                "skills, use a dedicated benchmark database."
            ),
        },
        "summary": {
            "n_tasks": len(tasks),
            "recall_at_1": round(metrics.recall_at_1, 4),
            "recall_at_5": round(metrics.recall_at_5, 4),
            "precision_at_5": round(metrics.precision_at_5, 4),
            "f1_at_5": round(metrics.f1_at_5, 4),
            "avg_latency_ms": round(metrics.avg_latency_ms, 3),
            "avg_cost_usd": metrics.avg_cost_usd,
            "missed_task_ids": missed,
            "rank1_missed_task_ids": rank1_missed,
        },
        "per_query": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SkillsBench fixture retrieval benchmark")
    parser.add_argument("--db-url", default="postgresql+psycopg2://edumanus_user:edumanus_password@localhost:15432/aicosmos_test")
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--similarity-threshold", type=float, default=0.2)
    parser.add_argument("--keep-benchmark-rows", action="store_true")
    parser.add_argument("--run-namespace", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = asyncio.run(
        run_skillsbench_fixture_benchmark(
            db_url=args.db_url,
            max_tasks=args.max_tasks,
            top_k=args.top_k,
            similarity_threshold=args.similarity_threshold,
            cleanup_after=not args.keep_benchmark_rows,
            run_namespace=args.run_namespace,
        )
    )
    text_out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text_out)
    print(text_out)


if __name__ == "__main__":
    main()
