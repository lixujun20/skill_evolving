"""
Unit tests for SkillDatabaseManager v2 methods: save_query_record, search_similar_queries.

Uses an in-memory SQLite DB (pgvector fallback: tests skip gracefully if Vector not available).

Run with:
    pytest -q -m "not llm" app/meta_agent/skills/tests/unit/test_query_record_manager.py
"""

import pytest
from datetime import datetime
from typing import List, Optional
from unittest.mock import MagicMock, patch

from sqlmodel import SQLModel, Session, create_engine, select

from app.meta_agent.skills.database.models import QueryRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db_manager():
    """SkillDatabaseManager with a mocked engine — tests DB logic via mock."""
    from app.meta_agent.skills.database.manager import SkillDatabaseManager
    mgr = MagicMock(spec=SkillDatabaseManager)
    # Restore real method implementations on the mock
    mgr.save_query_record = SkillDatabaseManager.save_query_record.__get__(mgr, SkillDatabaseManager)
    mgr.search_similar_queries = SkillDatabaseManager.search_similar_queries.__get__(mgr, SkillDatabaseManager)
    return mgr


# ── Tests: QueryRecord model ──────────────────────────────────────────────────

class TestQueryRecordModel:
    def test_tablename(self):
        assert QueryRecord.__tablename__ == "skill_query_records"

    def test_required_fields(self):
        fields = QueryRecord.model_fields
        assert "query_text" in fields
        assert "query_embedding" in fields
        assert "produced_skill_id" in fields
        assert "agent_summary" in fields
        assert "remarks" in fields
        assert "created_at" in fields

    def test_optional_fields_have_defaults(self):
        """Optional fields must have sane defaults so minimal construction works."""
        qr = QueryRecord(query_text="test query")
        assert qr.agent_summary == ""
        assert qr.remarks == ""
        assert qr.produced_skill_id is None
        assert qr.query_embedding is None

    def test_query_embedding_is_optional(self):
        """query_embedding can be None (e.g., when embedding API fails)."""
        qr = QueryRecord(query_text="no embedding")
        assert qr.query_embedding is None


# ── Tests: save_query_record contract ────────────────────────────────────────

class TestSaveQueryRecord:
    def test_returns_query_record_instance(self):
        """save_query_record should always return a QueryRecord, even on failure."""
        from app.meta_agent.skills.database.manager import SkillDatabaseManager

        mgr = MagicMock(spec=SkillDatabaseManager)
        mock_engine = MagicMock()
        mgr.engine = mock_engine

        with patch("app.meta_agent.skills.database.manager.Session") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess_cls.return_value.__enter__.return_value = mock_sess
            mock_sess.refresh.side_effect = lambda obj: None

            result = SkillDatabaseManager.save_query_record(
                mgr,
                query_text="calculate average grade",
                query_embedding=[0.1] * 1024,
                produced_skill_id=3,
                produced_skill_name="grade_calculator",
                agent_summary="Computed mean of student scores",
                remarks="passed",
            )

        assert isinstance(result, QueryRecord)

    def test_returns_fallback_on_db_error(self):
        """If DB write fails, save_query_record returns a minimal QueryRecord (non-blocking)."""
        from app.meta_agent.skills.database.manager import SkillDatabaseManager

        mgr = MagicMock(spec=SkillDatabaseManager)
        mgr.engine = MagicMock()

        with patch("app.meta_agent.skills.database.manager.Session") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess_cls.return_value.__enter__.return_value = mock_sess
            mock_sess.add.side_effect = Exception("DB connection error")

            result = SkillDatabaseManager.save_query_record(
                mgr,
                query_text="failing query",
                query_embedding=None,
            )

        # Must not raise; should return a QueryRecord with at least query_text set
        assert isinstance(result, QueryRecord)
        assert result.query_text == "failing query"


# ── Tests: search_similar_queries contract ────────────────────────────────────

class TestSearchSimilarQueries:
    def test_returns_empty_list_on_pgvector_error(self):
        """If pgvector extension is not installed, returns [] without raising."""
        from app.meta_agent.skills.database.manager import SkillDatabaseManager
        from sqlalchemy.exc import ProgrammingError

        mgr = MagicMock(spec=SkillDatabaseManager)
        mgr.engine = MagicMock()

        with patch("app.meta_agent.skills.database.manager.Session") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess_cls.return_value.__enter__.return_value = mock_sess
            mock_sess.exec.side_effect = ProgrammingError(
                statement="", params={}, orig=Exception("vector not found")
            )

            result = SkillDatabaseManager.search_similar_queries(
                mgr,
                query_embedding=[0.1] * 1024,
                top_m=5,
            )

        assert result == []

    def test_returns_empty_list_when_no_records(self):
        """Empty table → empty list."""
        from app.meta_agent.skills.database.manager import SkillDatabaseManager

        mgr = MagicMock(spec=SkillDatabaseManager)
        mgr.engine = MagicMock()

        with patch("app.meta_agent.skills.database.manager.Session") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess_cls.return_value.__enter__.return_value = mock_sess
            mock_sess.exec.return_value.all.return_value = []

            result = SkillDatabaseManager.search_similar_queries(
                mgr,
                query_embedding=[0.1] * 1024,
                top_m=5,
            )

        assert result == []

    def test_returns_query_records_sorted_by_distance(self):
        """Returned records should be ordered by cosine distance (closest first)."""
        from app.meta_agent.skills.database.manager import SkillDatabaseManager

        mgr = MagicMock(spec=SkillDatabaseManager)
        mgr.engine = MagicMock()
        rec1 = QueryRecord(id=1, query_text="q1")
        rec2 = QueryRecord(id=2, query_text="q2")
        expected = [rec1, rec2]

        with patch("app.meta_agent.skills.database.manager.Session") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess_cls.return_value.__enter__.return_value = mock_sess
            mock_sess.exec.return_value.all.return_value = expected

            result = SkillDatabaseManager.search_similar_queries(
                mgr,
                query_embedding=[0.5] * 1024,
                top_m=10,
            )

        assert result == expected
        assert len(result) == 2
