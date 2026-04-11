"""Tests for the SkillRetriever class.

Non-LLM tests (no @pytest.mark.llm) run against real embedding API calls
but do NOT require a running LLM reasoner; they are fast and cheap.
"""

import asyncio
import random
from typing import List
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.meta_agent.skills.database.models import Skill, SkillGroup
from app.meta_agent.skills.retrieval import RetrievalResult, SkillRetriever


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_embedding(dim: int = 1024) -> List[float]:
    """Return a unit-normalised random embedding."""
    vec = [random.gauss(0, 1) for _ in range(dim)]
    magnitude = sum(x * x for x in vec) ** 0.5
    return [x / magnitude for x in vec]


def _insert_skill_with_embedding(
    manager,
    code: str,
    docstring: str,
    embedding: List[float],
    tags: List[str] = None,
) -> Skill:
    with Session(manager.engine) as session:
        from sqlmodel import select

        group_name = docstring[:40]
        group = session.exec(
            select(SkillGroup).where(SkillGroup.name == group_name)
        ).first()
        if not group:
            group = SkillGroup(name=group_name)
            session.add(group)
            session.commit()
            session.refresh(group)

        skill = Skill(
            group_id=group.id,
            major_version=1,
            minor_version=0,
            code=code,
            docstring=docstring,
            interface_schema={},
            tags=tags or [],
            embedding=embedding,
        )
        session.add(skill)
        session.commit()
        session.refresh(skill)
        return skill


# ---------------------------------------------------------------------------
# Embedding tests
# ---------------------------------------------------------------------------


def test_embedding_generates_correct_dimension():
    """generate_embedding must return a 1024-dim vector."""
    retriever = SkillRetriever()
    result = retriever.generate_embedding("test")
    assert result is not None, "Embedding API returned None"
    assert len(result) == 1024


def test_embedding_returns_none_on_api_failure():
    """generate_embedding must return None when the API is unreachable."""
    retriever = SkillRetriever()
    retriever._client = MagicMock()
    retriever._client.embeddings.create.side_effect = Exception("network error")
    result = retriever.generate_embedding("test")
    assert result is None


# ---------------------------------------------------------------------------
# Retrieval tests
# ---------------------------------------------------------------------------


def test_empty_db_returns_empty(mock_db):
    """An empty database should return an empty skill list."""
    retriever = SkillRetriever()
    result = asyncio.get_event_loop().run_until_complete(
        retriever.retrieve_for_query("anything", mock_db)
    )
    assert isinstance(result, RetrievalResult)
    assert result.skills == []


def test_retrieval_speed(mock_db):
    """DB vector search + embedding (mocked) must complete within 3 seconds."""
    for i in range(10):
        _insert_skill_with_embedding(
            mock_db,
            code=f"def skill_{i}(): pass",
            docstring=f"Random skill number {i}",
            embedding=_random_embedding(),
        )

    retriever = SkillRetriever()
    # Use a pre-computed random embedding to isolate DB retrieval speed from API latency
    fake_embedding = _random_embedding()
    with patch.object(retriever, "generate_embedding", return_value=fake_embedding):
        # Patch internal client so retrieve_for_query also skips real API
        with patch.object(retriever._client.embeddings, "create") as mock_create:
            mock_resp = MagicMock()
            mock_resp.data = [MagicMock(embedding=fake_embedding)]
            mock_resp.usage = MagicMock(total_tokens=6)
            mock_create.return_value = mock_resp

            result = asyncio.get_event_loop().run_until_complete(
                retriever.retrieve_for_query("find something useful", mock_db)
            )

    assert result.elapsed_ms < 3000, f"Retrieval took {result.elapsed_ms:.0f}ms (limit 3000ms)"


def test_retrieval_cost_estimate(mock_db):
    """Cost estimate for a single retrieval must be well under $0.001."""
    retriever = SkillRetriever()
    result = asyncio.get_event_loop().run_until_complete(
        retriever.retrieve_for_query("calculate student average grade", mock_db)
    )
    assert result.estimated_cost_usd < 0.001, (
        f"Cost estimate ${result.estimated_cost_usd:.6f} exceeds $0.001"
    )


def test_retrieval_returns_relevant_skill(mock_db):
    """A skill about student grades should rank in top-5 for a matching query."""
    retriever = SkillRetriever()

    # Insert a clearly relevant skill with its real semantic embedding
    relevant_embedding = retriever.generate_embedding(
        "calculate student average grade compute mean score"
    )
    assert relevant_embedding is not None

    relevant_skill = _insert_skill_with_embedding(
        mock_db,
        code=(
            "def calculate_average_grade(scores: list) -> float:\n"
            "    return sum(scores) / len(scores)\n"
        ),
        docstring="Calculate the average grade for a list of student scores.",
        embedding=relevant_embedding,
    )

    # Insert noise skills with random embeddings
    for i in range(5):
        _insert_skill_with_embedding(
            mock_db,
            code=f"def noise_{i}(): pass",
            docstring=f"Unrelated skill {i} about networking and protocols",
            embedding=_random_embedding(),
        )

    result = asyncio.get_event_loop().run_until_complete(
        retriever.retrieve_for_query(
            "calculate student average grade",
            mock_db,
            top_k=5,
            similarity_threshold=0.3,
        )
    )

    retrieved_ids = [s.id for s in result.skills]
    assert relevant_skill.id in retrieved_ids, (
        f"Relevant skill (id={relevant_skill.id}) not found in top results: {retrieved_ids}"
    )


# ---------------------------------------------------------------------------
# Cost-per-query measurement
# ---------------------------------------------------------------------------


def test_retrieval_cost_per_query():
    """One real embedding call should cost well under $0.001."""
    retriever = SkillRetriever()

    # Patch the DB to skip pgvector so we only measure embedding cost
    mock_manager = MagicMock()
    mock_manager.search_similar_skills.return_value = []

    result = asyncio.get_event_loop().run_until_complete(
        retriever.retrieve_for_query(
            "calculate student average grade", mock_manager
        )
    )

    assert result.embedding_tokens > 0, "Token count not reported"
    assert result.estimated_cost_usd < 0.001, (
        f"Single query cost ${result.estimated_cost_usd:.6f} exceeds $0.001"
    )
    print(
        f"\n[cost] tokens={result.embedding_tokens}, "
        f"cost_usd=${result.estimated_cost_usd:.6f}, "
        f"elapsed_ms={result.elapsed_ms:.1f}"
    )
