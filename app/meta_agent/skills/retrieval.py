"""Skill retrieval system using ZhipuAI embedding-3 + pgvector cosine search."""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openai import OpenAI

from app.meta_agent.skills.database.manager import SkillDatabaseManager
from app.meta_agent.skills.database.models import Skill

logger = logging.getLogger(__name__)

# ZhipuAI embedding-3 pricing: ~$0.00007 per 1K tokens
_EMBEDDING_COST_PER_1K_TOKENS_USD = 0.00007


@dataclass
class RetrievalResult:
    """Result of a skill retrieval query."""
    skills: List[Skill] = field(default_factory=list)
    elapsed_ms: float = 0.0
    embedding_tokens: int = 0
    estimated_cost_usd: float = 0.0


class SkillRetriever:
    """Retrieves skills from the DB using semantic similarity via ZhipuAI embeddings."""

    def __init__(self) -> None:
        from app.config import config
        llm_cfg = config.llm["embedding"]
        self._client = OpenAI(
            api_key=llm_cfg.api_key,
            base_url=llm_cfg.base_url,
        )
        self._model = llm_cfg.model

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Return a 1024-dim embedding for *text* using ZhipuAI embedding-3.

        Returns None if the API is unavailable so callers can skip retrieval
        gracefully.
        """
        try:
            response = self._client.embeddings.create(
                model=self._model, input=text, dimensions=1024
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.warning("Embedding API unavailable: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Query retrieval
    # ------------------------------------------------------------------

    async def retrieve_for_query(
        self,
        query: str,
        db_manager: SkillDatabaseManager,
        top_k: int = 5,
        tags_filter: Optional[List[str]] = None,
        similarity_threshold: float = 0.6,
    ) -> RetrievalResult:
        """Embed *query* and return the top-k most similar skills from *db_manager*."""
        t0 = time.monotonic()

        try:
            response = self._client.embeddings.create(
                model=self._model, input=query, dimensions=1024
            )
            embedding = response.data[0].embedding
            tokens = response.usage.total_tokens if response.usage else len(query.split())
        except Exception as exc:
            logger.warning("Embedding API unavailable during retrieval: %s", exc)
            elapsed_ms = (time.monotonic() - t0) * 1000
            return RetrievalResult(elapsed_ms=elapsed_ms)

        skills = db_manager.search_similar_skills(
            query_embedding=embedding,
            top_k=top_k,
            tags_filter=tags_filter,
            similarity_threshold=similarity_threshold,
        )

        elapsed_ms = (time.monotonic() - t0) * 1000
        cost = (tokens / 1000) * _EMBEDDING_COST_PER_1K_TOKENS_USD

        return RetrievalResult(
            skills=skills,
            elapsed_ms=elapsed_ms,
            embedding_tokens=tokens,
            estimated_cost_usd=cost,
        )

    # ------------------------------------------------------------------
    # Skill enrichment
    # ------------------------------------------------------------------

    def enrich_skill_with_embedding(
        self, skill: Skill, db_manager: SkillDatabaseManager
    ) -> None:
        """Generate and persist an embedding for *skill* based on its content."""
        parts: List[str] = []
        if skill.docstring:
            parts.append(skill.docstring)
        if skill.tags:
            parts.append(" ".join(skill.tags) if isinstance(skill.tags, list) else str(skill.tags))
        if skill.code:
            # Use only the first 500 chars to keep the embedding focused on the signature/summary
            parts.append(skill.code[:500])

        text = "\n".join(parts) if parts else (skill.code or "")
        embedding = self.generate_embedding(text)
        if embedding is None:
            logger.warning("Could not generate embedding for skill id=%s; skipping.", skill.id)
            return

        from sqlmodel import Session
        with Session(db_manager.engine) as session:
            db_skill = session.get(Skill, skill.id)
            if db_skill is None:
                logger.warning("Skill id=%s not found in DB; skipping embedding update.", skill.id)
                return
            db_skill.embedding = embedding
            session.add(db_skill)
            session.commit()
            session.refresh(db_skill)

        skill.embedding = embedding

# ── v2: Collaborative Filtering ──────────────────────────────────────────────

@dataclass
class CollabRetrievalResult(RetrievalResult):
    """协同过滤检索结果，在 RetrievalResult 基础上追加路径分析信息。

    collab_signals: list of dicts, e.g.
        [{"skill_id": 3, "direct_sim": 0.82, "collab_sim": 0.0,
          "final_score": 0.49, "source": "direct"}, ...]
    query_embedding: the query's 1024-dim embedding (cached for pipeline reuse)
    """
    collab_signals: List[Dict] = field(default_factory=list)
    query_embedding: Optional[List[float]] = None


class CollaborativeRetriever(SkillRetriever):
    """在 SkillRetriever 基础上增加协同过滤路径。

    Parameters
    ----------
    alpha : float
        Path A（直接相似）权重，默认 0.6。
    collab_top_m : int
        协同路径检索历史相似查询数，默认 10。
    collab_min_queries : int
        QueryRecord 少于此值时退化为纯直接检索，默认 3。
    """

    async def retrieve_with_collab_filter(
        self,
        query: str,
        db_manager,
        top_k: int = 5,
        alpha: float = 0.6,
        collab_top_m: int = 10,
        similarity_threshold: float = 0.5,
        collab_min_queries: int = 3,
        tags_filter: Optional[List[str]] = None,
    ) -> "CollabRetrievalResult":
        """双路检索：直接相似 + 协同过滤，按加权分数融合返回 top_k。

        Returns (result, query_embedding) via retrieve_with_collab_filter_and_emb().
        """
        import time as _time
        from sqlmodel import Session as _Session, select as _select, func as _func
        from app.meta_agent.skills.database.models import QueryRecord, Skill as _Skill

        t0 = _time.monotonic()

        # ── 生成 query embedding（复用给两路）────────────────────────────────
        try:
            response = self._client.embeddings.create(
                model=self._model, input=query, dimensions=1024
            )
            q_emb = response.data[0].embedding
            tokens = response.usage.total_tokens if response.usage else len(query.split())
        except Exception as exc:
            logger.warning("Embedding API unavailable: %s", exc)
            elapsed_ms = (_time.monotonic() - t0) * 1000
            return CollabRetrievalResult(elapsed_ms=elapsed_ms)

        # ── 冷启动保护：QueryRecord 数量检查 ─────────────────────────────────
        with _Session(db_manager.engine) as _s:
            record_count = _s.exec(_select(_func.count(QueryRecord.id))).one()
        use_collab = record_count >= collab_min_queries

        # ── Path A: 直接相似检索 ──────────────────────────────────────────────
        path_a_skills = db_manager.search_similar_skills(
            query_embedding=q_emb,
            top_k=top_k,
            tags_filter=tags_filter,
            similarity_threshold=similarity_threshold,
        )
        direct_scores: Dict[int, float] = {}
        for sk in path_a_skills:
            if sk.embedding is not None and sk.id is not None:
                direct_scores[sk.id] = _cosine_sim(q_emb, sk.embedding)

        # ── Path B: 协同过滤检索 ──────────────────────────────────────────────
        collab_scores: Dict[int, float] = {}
        if use_collab:
            similar_queries = db_manager.search_similar_queries(
                query_embedding=q_emb,
                top_m=collab_top_m,
                similarity_threshold=0.4,
            )
            for qr in similar_queries:
                if qr.produced_skill_id is None:
                    continue
                q_sim = _cosine_sim(q_emb, qr.query_embedding) if qr.query_embedding is not None else 0.0
                sid = qr.produced_skill_id
                if sid not in collab_scores or collab_scores[sid] < q_sim:
                    collab_scores[sid] = q_sim

        # ── 融合 (Parallel Fusion) ────────────────────────────────────────────
        all_skill_ids = set(direct_scores.keys()) | set(collab_scores.keys())
        fusion: List[Dict] = []
        for sid in all_skill_ids:
            d_sim = direct_scores.get(sid, 0.0)
            c_sim = collab_scores.get(sid, 0.0)
            final = alpha * d_sim + (1 - alpha) * c_sim
            source = (
                "both" if (sid in direct_scores and sid in collab_scores)
                else ("direct" if sid in direct_scores else "collab")
            )
            fusion.append({
                "skill_id": sid, "direct_sim": d_sim,
                "collab_sim": c_sim, "final_score": final, "source": source,
            })
        fusion.sort(key=lambda x: x["final_score"], reverse=True)
        fusion = fusion[:top_k]

        # ── 获取融合后的 Skill 对象 ───────────────────────────────────────────
        top_skill_ids = [f["skill_id"] for f in fusion]
        with _Session(db_manager.engine) as _s:
            skills_map: Dict[int, _Skill] = {}
            for sid in top_skill_ids:
                sk = _s.get(_Skill, sid)
                if sk:
                    skills_map[sid] = sk
        final_skills = [skills_map[sid] for sid in top_skill_ids if sid in skills_map]

        elapsed_ms = (_time.monotonic() - t0) * 1000
        cost = (tokens / 1000) * _EMBEDDING_COST_PER_1K_TOKENS_USD

        result = CollabRetrievalResult(
            skills=final_skills,
            elapsed_ms=elapsed_ms,
            embedding_tokens=tokens,
            estimated_cost_usd=cost,
            collab_signals=fusion,
            query_embedding=q_emb,
        )
        result._query_embedding = q_emb  # side-channel for pipeline reuse
        return result


def _cosine_sim(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

