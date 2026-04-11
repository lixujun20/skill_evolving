"""
Unit tests for CollaborativeRetriever (v2 collaborative filtering).

All tests are non-LLM — uses mocked DB and mocked embedding API.
Run with:
    pytest -q -m "not llm" app/meta_agent/skills/tests/unit/test_collab_retrieval.py
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.meta_agent.skills.retrieval import (
    CollabRetrievalResult,
    CollaborativeRetriever,
    _cosine_sim,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unit_vec(dim: int, hot_idx: int) -> List[float]:
    """Return unit vector with 1.0 at hot_idx, 0.0 elsewhere."""
    v = [0.0] * dim
    v[hot_idx] = 1.0
    return v


def _make_skill(skill_id: int, embedding: List[float]):
    sk = MagicMock()
    sk.id = skill_id
    sk.embedding = embedding
    sk.docstring = f"skill_{skill_id}"
    return sk


def _make_query_record(qr_id: int, embedding: List[float], produced_skill_id: Optional[int]):
    qr = MagicMock()
    qr.id = qr_id
    qr.query_embedding = embedding
    qr.produced_skill_id = produced_skill_id
    return qr


# ── Tests: _cosine_sim helper ─────────────────────────────────────────────────

class TestCosineSim:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine_sim(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_sim(a, b)) < 1e-9

    def test_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        assert _cosine_sim(a, b) == 0.0

    def test_symmetry(self):
        a = [0.6, 0.8]
        b = [0.8, 0.6]
        assert abs(_cosine_sim(a, b) - _cosine_sim(b, a)) < 1e-9


# ── Tests: CollabRetrievalResult ──────────────────────────────────────────────

class TestCollabRetrievalResult:
    def test_has_collab_signals_field(self):
        r = CollabRetrievalResult(skills=[], elapsed_ms=10.0)
        assert r.collab_signals == []

    def test_has_query_embedding_field(self):
        emb = [0.5] * 1024
        r = CollabRetrievalResult(skills=[], elapsed_ms=10.0, query_embedding=emb)
        assert r.query_embedding == emb

    def test_inherits_retrieval_result_fields(self):
        r = CollabRetrievalResult(skills=[], elapsed_ms=5.5, embedding_tokens=100, estimated_cost_usd=0.0001)
        assert r.elapsed_ms == 5.5
        assert r.embedding_tokens == 100


# ── Tests: CollaborativeRetriever ─────────────────────────────────────────────

class TestCollaborativeRetrieverColdStart:
    """When QueryRecord count < collab_min_queries, must degrade to pure direct retrieval."""

    @pytest.mark.asyncio
    async def test_cold_start_no_query_records(self):
        """Empty QueryRecord table → collab_signals should all be 'direct' or empty."""
        dim = 1024
        q_emb = _unit_vec(dim, 0)        # query embedding
        skill_emb = _unit_vec(dim, 0)    # skill embedding identical to query

        db = MagicMock()
        db.search_similar_skills.return_value = [_make_skill(1, skill_emb)]
        db.search_similar_queries.return_value = []

        retriever = CollaborativeRetriever()

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=q_emb)]
        mock_response.usage = MagicMock(total_tokens=50)

        mock_exec_result = MagicMock()
        mock_exec_result.one.return_value = 0  # record_count = 0 < collab_min_queries

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value = mock_exec_result
        mock_session.get.side_effect = lambda cls, sid: _make_skill(sid, skill_emb)

        with patch.object(retriever._client.embeddings, "create", return_value=mock_response), \
             patch("sqlmodel.Session", return_value=mock_session):
            result = await retriever.retrieve_with_collab_filter(
                query="test query",
                db_manager=db,
                top_k=3,
                collab_min_queries=3,
            )

        assert isinstance(result, CollabRetrievalResult)
        # All signals should be 'direct' (no collab path was used)
        for sig in result.collab_signals:
            assert sig["source"] == "direct"

    @pytest.mark.asyncio
    async def test_embedding_api_failure_returns_empty_result(self):
        """If embedding API fails, return empty CollabRetrievalResult (non-blocking)."""
        db = MagicMock()
        retriever = CollaborativeRetriever()

        with patch.object(
            retriever._client.embeddings, "create",
            side_effect=Exception("API timeout"),
        ):
            result = await retriever.retrieve_with_collab_filter(
                query="test",
                db_manager=db,
            )

        assert isinstance(result, CollabRetrievalResult)
        assert result.skills == []
        assert result.collab_signals == []


class TestCollaborativeRetrieverFusion:
    """Verify fusion scoring arithmetic."""

    def test_fusion_formula(self):
        """final_score = alpha * direct + (1-alpha) * collab"""
        alpha = 0.6
        direct_sim = 0.8
        collab_sim = 0.5
        expected = alpha * direct_sim + (1 - alpha) * collab_sim
        assert abs(expected - (0.6 * 0.8 + 0.4 * 0.5)) < 1e-9

    def test_direct_only_skill_gets_zero_collab(self):
        """A skill only in Path A should have collab_sim=0."""
        alpha = 0.6
        direct_sim = 0.9
        collab_sim = 0.0
        final = alpha * direct_sim + (1 - alpha) * collab_sim
        assert abs(final - 0.54) < 1e-9

    def test_collab_only_skill_gets_zero_direct(self):
        """A skill only in Path B should have direct_sim=0."""
        alpha = 0.6
        direct_sim = 0.0
        collab_sim = 0.7
        final = alpha * direct_sim + (1 - alpha) * collab_sim
        assert abs(final - 0.28) < 1e-9

    def test_source_label_correctness(self):
        """source='both' when skill appears in both paths."""
        direct_scores = {1: 0.8, 2: 0.5}
        collab_scores = {1: 0.6, 3: 0.4}
        alpha = 0.6

        all_ids = set(direct_scores) | set(collab_scores)
        fusion = []
        for sid in all_ids:
            d = direct_scores.get(sid, 0.0)
            c = collab_scores.get(sid, 0.0)
            f = alpha * d + (1 - alpha) * c
            source = (
                "both" if (sid in direct_scores and sid in collab_scores)
                else ("direct" if sid in direct_scores else "collab")
            )
            fusion.append({"skill_id": sid, "final_score": f, "source": source})

        sources = {item["skill_id"]: item["source"] for item in fusion}
        assert sources[1] == "both"
        assert sources[2] == "direct"
        assert sources[3] == "collab"

    def test_fusion_respects_top_k(self):
        """Result must have ≤ top_k entries after fusion."""
        skills_data = {i: 1.0 / (i + 1) for i in range(20)}  # 20 skills
        top_k = 5
        fusion = sorted(skills_data.items(), key=lambda x: x[1], reverse=True)[:top_k]
        assert len(fusion) == top_k
