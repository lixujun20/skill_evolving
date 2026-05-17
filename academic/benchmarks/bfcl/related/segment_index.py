"""Segment vector index for BFCL related-task overlap/refactor experiments."""
from __future__ import annotations

import copy
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from academic.benchmarks.core.types import SkillArtifact
from academic.skill_repository.refactor_overlap import TraceSegment
from app.meta_agent.skills.retrieval import SkillRetriever

try:
    from sqlmodel import SQLModel, Field, Session, create_engine, select
    from sqlalchemy import Column, text
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover - optional dependency path
    SQLModel = None  # type: ignore[assignment]
    Field = None  # type: ignore[assignment]
    Session = None  # type: ignore[assignment]
    create_engine = None  # type: ignore[assignment]
    select = None  # type: ignore[assignment]
    Column = None  # type: ignore[assignment]
    text = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]


TEXT_PREVIEW_LIMIT = 160


@dataclass
class SegmentVectorRow:
    segment_id: str
    base_segment_id: str
    task_id: str
    round_index: int
    turn_index: Optional[int]
    text: str
    error_text: str
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "base_segment_id": self.base_segment_id,
            "task_id": self.task_id,
            "round": self.round_index,
            "turn_index": self.turn_index,
            "text": self.text,
            "error_text": self.error_text,
            "embedding": self.embedding,
            "metadata": self.metadata,
        }


def segment_row_from_dict(payload: Dict[str, Any]) -> SegmentVectorRow:
    return SegmentVectorRow(
        segment_id=str(payload.get("segment_id") or ""),
        base_segment_id=str(payload.get("base_segment_id") or payload.get("segment_id") or ""),
        task_id=str(payload.get("task_id") or ""),
        round_index=int(payload.get("round") if payload.get("round") is not None else payload.get("round_index") or 0),
        turn_index=payload.get("turn_index"),
        text=str(payload.get("text") or ""),
        error_text=str(payload.get("error_text") or ""),
        embedding=list(payload.get("embedding") or []) or None,
        metadata=dict(payload.get("metadata") or {}),
    )


if SQLModel is not None and Field is not None and Column is not None and Vector is not None:
    class SegmentVectorRecord(SQLModel, table=True):  # pragma: no cover - exercised only when pgvector backend is configured
        __tablename__ = "bfcl_segment_vectors"

        segment_id: str = Field(primary_key=True)
        base_segment_id: str = Field(index=True)
        task_id: str = Field(index=True)
        round_index: int = Field(index=True)
        turn_index: Optional[int] = Field(default=None, index=True)
        text: str = Field(default="")
        error_text: str = Field(default="")
        metadata_json: str = Field(default="{}")
        embedding: Optional[List[float]] = Field(default=None, sa_column=Column(Vector(1024)))

    SEGMENT_VECTOR_TABLE = SegmentVectorRecord.__table__
else:
    SegmentVectorRecord = None  # type: ignore[assignment]
    SEGMENT_VECTOR_TABLE = None


class SegmentVectorIndex:
    """Compact segment vector store with optional embedding generation.

    The index always records rows. Embeddings are optional; when unavailable and
    `strict_embeddings=True`, callers get a hard failure instead of a silent
    downgrade. This matches the experiment plan's explicit failure strategy.
    """

    def __init__(
        self,
        *,
        strict_embeddings: bool = False,
        embedding_backend: str = "zhipu_embedding_3",
        backend: str | None = None,
        db_url: str | None = None,
    ) -> None:
        self.strict_embeddings = strict_embeddings
        self.embedding_backend = embedding_backend
        self.backend = (backend or os.environ.get("BFCL_SEGMENT_INDEX_BACKEND", "memory")).strip().lower()
        self.db_url = db_url or os.environ.get("BFCL_SEGMENT_DB_URL", "").strip() or None
        self.rows: List[SegmentVectorRow] = []
        self._by_segment: Dict[str, SegmentVectorRow] = {}
        self._retriever: SkillRetriever | None = None
        self._embedding_failures: List[Dict[str, Any]] = []
        self._backend_failures: List[Dict[str, Any]] = []
        self._engine = None
        self._pgvector_ready = False
        self._init_backend()

    def _init_backend(self) -> None:
        if self.backend != "pgvector":
            return
        if not self.db_url or SQLModel is None or create_engine is None or text is None or SegmentVectorRecord is None:
            self._backend_failures.append(
                {
                    "backend": self.backend,
                    "reason": "pgvector backend requested but dependencies or BFCL_SEGMENT_DB_URL are unavailable",
                }
            )
            if self.strict_embeddings:
                raise RuntimeError(self._backend_failures[-1]["reason"])
            self.backend = "memory"
            return
        try:
            self._engine = create_engine(self.db_url)
            with self._engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            if SEGMENT_VECTOR_TABLE is None:
                raise RuntimeError("segment vector SQLModel table definition unavailable")
            SEGMENT_VECTOR_TABLE.create(self._engine, checkfirst=True)
            self._pgvector_ready = True
        except Exception as exc:  # pragma: no cover - requires pgvector backend
            self._backend_failures.append(
                {
                    "backend": self.backend,
                    "reason": f"pgvector init failed: {type(exc).__name__}: {exc}",
                }
            )
            if self.strict_embeddings:
                raise RuntimeError(self._backend_failures[-1]["reason"])
            self.backend = "memory"
            self._pgvector_ready = False

    def _get_retriever(self) -> SkillRetriever:
        if self._retriever is None:
            self._retriever = SkillRetriever()
        return self._retriever

    def _embed_text(self, text: str) -> Optional[List[float]]:
        try:
            return self._get_retriever().generate_embedding(text)
        except Exception as exc:
            self._embedding_failures.append({"error": type(exc).__name__, "message": str(exc)})
            return None

    def add_segments(
        self,
        segments: Sequence[TraceSegment],
        *,
        round_index: int,
        task_id: str,
    ) -> List[SegmentVectorRow]:
        added: List[SegmentVectorRow] = []
        for segment in segments:
            scoped_segment_id = f"r{round_index}:{segment.segment_id}"
            if scoped_segment_id in self._by_segment:
                continue
            combined_text = f"{segment.text}\n{segment.error_text}".strip()
            embedding = self._embed_text(combined_text) if combined_text else None
            if self.strict_embeddings and embedding is None:
                raise RuntimeError(
                    "Segment embedding generation failed while strict embeddings are enabled. "
                    "Set BFCL_STRICT_SEGMENT_EMBEDDINGS=0 only if you intentionally want to skip the experiment."
                )
            row = SegmentVectorRow(
                segment_id=scoped_segment_id,
                base_segment_id=segment.segment_id,
                task_id=task_id,
                round_index=round_index,
                turn_index=segment.turn_index,
                text=segment.text,
                error_text=segment.error_text,
                embedding=embedding,
                metadata=copy.deepcopy(segment.metadata),
            )
            self.rows.append(row)
            self._by_segment[row.segment_id] = row
            self._persist_row(row)
            added.append(row)
        return added

    def load_rows(self, rows: Sequence[Dict[str, Any] | SegmentVectorRow]) -> None:
        for item in rows:
            row = item if isinstance(item, SegmentVectorRow) else segment_row_from_dict(dict(item or {}))
            if row.segment_id in self._by_segment:
                continue
            self.rows.append(row)
            self._by_segment[row.segment_id] = row

    def _persist_row(self, row: SegmentVectorRow) -> None:
        if self.backend != "pgvector" or not self._pgvector_ready or self._engine is None or Session is None:
            return
        try:  # pragma: no cover - requires pgvector backend
            with Session(self._engine) as session:
                existing = session.get(SegmentVectorRecord, row.segment_id)
                payload = SegmentVectorRecord(
                    segment_id=row.segment_id,
                    base_segment_id=row.base_segment_id,
                    task_id=row.task_id,
                    round_index=row.round_index,
                    turn_index=row.turn_index,
                    text=row.text,
                    error_text=row.error_text,
                    metadata_json=json.dumps(row.metadata, ensure_ascii=False),
                    embedding=row.embedding,
                )
                if existing is not None:
                    payload.segment_id = existing.segment_id
                session.merge(payload)
                session.commit()
        except Exception as exc:
            self._backend_failures.append({"backend": self.backend, "reason": f"persist failed: {type(exc).__name__}: {exc}"})
            if self.strict_embeddings:
                raise

    @staticmethod
    def _cosine(a: Optional[List[float]], b: Optional[List[float]]) -> Optional[float]:
        if not a or not b or len(a) != len(b):
            return None
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b):
            dot += x * y
            norm_a += x * x
            norm_b += y * y
        if norm_a <= 0 or norm_b <= 0:
            return None
        return dot / math.sqrt(norm_a * norm_b)

    def top_k_neighbors_for_segment(self, segment_id: str, *, top_k: int = 5) -> List[Dict[str, Any]]:
        row = self._by_segment.get(segment_id)
        if row is None or row.embedding is None:
            return []
        scored: List[Tuple[float, SegmentVectorRow]] = []
        for candidate in self.rows:
            if candidate.segment_id == segment_id or candidate.embedding is None:
                continue
            score = self._cosine(row.embedding, candidate.embedding)
            if score is None:
                continue
            scored.append((score, candidate))
        scored.sort(key=lambda item: (-item[0], item[1].segment_id))
        return [
            {
                "segment_id": candidate.segment_id,
                "task_id": candidate.task_id,
                "round": candidate.round_index,
                "score": round(score, 6),
            }
            for score, candidate in scored[:top_k]
        ]

    def top_k_neighbors_for_task(self, task_id: str, *, top_k: int = 5) -> List[Dict[str, Any]]:
        task_rows = [row for row in self.rows if row.task_id == task_id and row.embedding is not None]
        if not task_rows:
            return []
        candidate_scores: Dict[str, List[float]] = defaultdict(list)
        for row in task_rows:
            for neighbor in self.top_k_neighbors_for_segment(row.segment_id, top_k=top_k * 3):
                if neighbor["task_id"] == task_id:
                    continue
                candidate_scores[neighbor["task_id"]].append(float(neighbor["score"]))
        agg = [
            {
                "task_id": other_task_id,
                "avg_score": round(sum(scores) / len(scores), 6),
                "support": len(scores),
            }
            for other_task_id, scores in candidate_scores.items()
        ]
        agg.sort(key=lambda item: (-item["avg_score"], -item["support"], item["task_id"]))
        return agg[:top_k]

    def top_k_neighbors_for_query(self, query_text: str, *, top_k: int = 5) -> List[Dict[str, Any]]:
        query_embedding = self._embed_text(query_text.strip()) if query_text and query_text.strip() else None
        if query_embedding is None:
            if self.strict_embeddings:
                raise RuntimeError(
                    "Query embedding generation failed while strict embeddings are enabled for the segment index."
                )
            return []
        scored: List[Tuple[float, SegmentVectorRow]] = []
        for candidate in self.rows:
            if candidate.embedding is None:
                continue
            score = self._cosine(query_embedding, candidate.embedding)
            if score is None:
                continue
            scored.append((score, candidate))
        scored.sort(key=lambda item: (-item[0], item[1].segment_id))
        return [
            {
                "segment_id": candidate.segment_id,
                "task_id": candidate.task_id,
                "round": candidate.round_index,
                "score": round(score, 6),
            }
            for score, candidate in scored[:top_k]
        ]

    def top_k_neighbors_for_skill(self, skill: SkillArtifact, *, top_k: int = 5) -> List[Dict[str, Any]]:
        query = "\n".join(
            item.strip()
            for item in [skill.name, skill.description, skill.body]
            if str(item).strip()
        )
        return self.top_k_neighbors_for_query(query, top_k=top_k)

    def embedding_map(
        self,
        segment_ids: Sequence[str] | None = None,
        *,
        round_index: int | None = None,
    ) -> Dict[str, List[float]]:
        allowed = None if segment_ids is None else {str(segment_id) for segment_id in segment_ids}
        out: Dict[str, List[float]] = {}
        for row in self.rows:
            if row.embedding is None:
                continue
            if round_index is not None and row.round_index != round_index:
                continue
            if allowed is not None and row.base_segment_id not in allowed and row.segment_id not in allowed:
                continue
            out[row.base_segment_id] = list(row.embedding)
        return out

    def stats(self) -> Dict[str, Any]:
        by_round = Counter(row.round_index for row in self.rows)
        return {
            "n_segments": len(self.rows),
            "n_embedded_segments": sum(1 for row in self.rows if row.embedding is not None),
            "embedding_backend": self.embedding_backend,
            "strict_embeddings": self.strict_embeddings,
            "index_backend": self.backend,
            "pgvector_ready": self._pgvector_ready,
            "embedding_failures": copy.deepcopy(self._embedding_failures),
            "backend_failures": copy.deepcopy(self._backend_failures),
            "rows_by_round": dict(sorted(by_round.items())),
        }

    def as_projection(self) -> Dict[str, Any]:
        return {
            "stats": self.stats(),
            "rows": [
                {
                    **row.as_dict(),
                    "text": (row.text[:TEXT_PREVIEW_LIMIT] + "...") if len(row.text) > TEXT_PREVIEW_LIMIT else row.text,
                    "error_text": (
                        row.error_text[:TEXT_PREVIEW_LIMIT] + "..."
                    ) if len(row.error_text) > TEXT_PREVIEW_LIMIT else row.error_text,
                    "embedding": None if row.embedding is None else {"dim": len(row.embedding)},
                }
                for row in self.rows
            ],
        }


def validate_segment_backend(
    *,
    backend: str,
    db_url: str | None,
) -> tuple[bool, str]:
    if backend != "pgvector":
        return True, "memory backend"
    if not db_url:
        return False, "BFCL_SEGMENT_DB_URL missing"
    if SQLModel is None or create_engine is None or text is None:
        return False, "sqlmodel/sqlalchemy/pgvector dependency unavailable"
    try:  # pragma: no cover - backend depends on environment
        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        return True, "pgvector backend reachable"
    except Exception as exc:
        return False, f"pgvector validation failed: {type(exc).__name__}: {exc}"


__all__ = [
    "SQLModel",
    "create_engine",
    "text",
    "SegmentVectorRow",
    "SegmentVectorIndex",
    "segment_row_from_dict",
    "validate_segment_backend",
]
