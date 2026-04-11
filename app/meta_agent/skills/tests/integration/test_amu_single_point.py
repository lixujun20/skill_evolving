"""
AMU Single-Point Integration Tests — Full Real Pipeline

Each test:
1. Seeds mock skill library into isolated DB
2. Calls pipeline.run(query=..., skill_name=...) with NO prebuilt_trace
3. Full pipeline runs: retriever → executor (Docker) → gardener → reviewer → commit
4. Asserts new skill version committed and pipeline phases succeeded
"""
from __future__ import annotations

import json
import pytest
import time
from pathlib import Path
from sqlmodel import Session, select

from app.meta_agent.skills.pipeline import SkillEvolvingPipeline, PipelineSession
from app.meta_agent.skills.database.models import Skill
from app.meta_agent.skills.tests.conftest import create_mock_skill
from app.meta_agent.skills.tests.integration.conftest import (
    record_round, RoundEntry, PhaseEntry,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixtures() -> list:
    path = FIXTURES_DIR / "single_point.json"
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}. Run generators/build_fixtures.py first.")
    with open(path) as f:
        return json.load(f)


def _make_pipeline(mock_db) -> SkillEvolvingPipeline:
    """Create a pipeline that reuses the test DB manager (avoids second DB connection)."""
    pipeline = SkillEvolvingPipeline()
    pipeline.db_manager = mock_db
    return pipeline


class TestAMUSinglePoint:

    @pytest.mark.llm
    @pytest.mark.asyncio
    @pytest.mark.timeout(900)
    async def test_sp_full_pipeline_scenario_0(self, mock_db, request):
        """Full real pipeline: retriever→executor→extractor→tester for scenario 0."""
        fixtures = load_fixtures()
        entry = fixtures[0]

        # Seed skill library (v1.0 stub + noise skills)
        skill = create_mock_skill(mock_db, entry["skill_name"], "1.0", code=entry["seed_code"])
        for ns in entry.get("noise_skills", []):
            create_mock_skill(mock_db, ns["name"], "1.0", code=ns["seed_code"])

        # Run FULL pipeline — no prebuilt_trace
        pipeline = _make_pipeline(mock_db)
        t0 = time.monotonic()
        result = await pipeline.run(
            query=entry["query"],
            skill_name=entry["skill_name"],
            skill_id=skill.id,
        )
        elapsed = time.monotonic() - t0

        # Record round for HTML report
        with Session(mock_db.engine) as s:
            versions_after = s.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        record_round(request.node.nodeid, RoundEntry(
            round_num=1, query=entry["query"],
            phases=[
                PhaseEntry("retrieval", result.retrieval.ok, result.retrieval.elapsed_s, error=result.retrieval.error),
                PhaseEntry("executor",  result.executor.ok,  result.executor.elapsed_s,  error=result.executor.error),
                PhaseEntry("extractor", result.extractor.ok, result.extractor.elapsed_s, error=result.extractor.error),
                PhaseEntry("tester",    result.tester.ok,    result.tester.elapsed_s,    error=result.tester.error),
            ],
            new_skill_id=result.new_skill_id,
            versions_total=len(versions_after),
            elapsed_s=elapsed,
        ))

        # Assert phases succeeded
        assert result.retrieval.ok, f"Retrieval failed: {result.retrieval.error}"
        assert result.executor.ok, f"Executor failed: {result.executor.error}"
        assert result.extractor.ok, f"Extractor failed: {result.extractor.error}"
        assert result.new_skill_id is not None, "No new skill version committed"

        # Assert new version in DB
        with Session(mock_db.engine) as s:
            versions = s.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, f"Expected >1 versions, got {len(versions)}"

    @pytest.mark.llm
    @pytest.mark.asyncio
    @pytest.mark.timeout(900)
    async def test_sp_full_pipeline_scenario_1(self, mock_db, request):
        """Full real pipeline for scenario 1 — different skill domain."""
        fixtures = load_fixtures()
        if len(fixtures) < 2:
            pytest.skip("Need at least 2 fixture entries")
        entry = fixtures[1]

        skill = create_mock_skill(mock_db, entry["skill_name"], "1.0", code=entry["seed_code"])
        for ns in entry.get("noise_skills", []):
            create_mock_skill(mock_db, ns["name"], "1.0", code=ns["seed_code"])

        pipeline = _make_pipeline(mock_db)
        t0 = time.monotonic()
        result = await pipeline.run(
            query=entry["query"],
            skill_name=entry["skill_name"],
            skill_id=skill.id,
        )
        with Session(mock_db.engine) as s:
            versions_after = s.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        record_round(request.node.nodeid, RoundEntry(
            round_num=1, query=entry["query"],
            phases=[
                PhaseEntry("retrieval", result.retrieval.ok, result.retrieval.elapsed_s, error=result.retrieval.error),
                PhaseEntry("executor",  result.executor.ok,  result.executor.elapsed_s,  error=result.executor.error),
                PhaseEntry("extractor", result.extractor.ok, result.extractor.elapsed_s, error=result.extractor.error),
                PhaseEntry("tester",    result.tester.ok,    result.tester.elapsed_s,    error=result.tester.error),
            ],
            new_skill_id=result.new_skill_id,
            versions_total=len(versions_after),
            elapsed_s=time.monotonic() - t0,
        ))

        assert result.retrieval.ok, f"Retrieval failed: {result.retrieval.error}"
        assert result.executor.ok, f"Executor failed: {result.executor.error}"
        assert result.extractor.ok, f"Extractor failed: {result.extractor.error}"
        assert result.new_skill_id is not None, "No new skill version committed"


class TestAMUIterativeEvolution:
    """Same query repeated 3 times — skill should evolve each round."""

    @pytest.mark.llm
    @pytest.mark.asyncio
    @pytest.mark.timeout(2700)  # 3 rounds × 900s each
    async def test_sp_iterative_3rounds_same_query(self, mock_db, request):
        """3-round iterative evolution: same skill, same query, 3 times."""
        fixtures = load_fixtures()
        entry = fixtures[0]

        skill = create_mock_skill(mock_db, entry["skill_name"], "1.0", code=entry["seed_code"])
        for ns in entry.get("noise_skills", []):
            create_mock_skill(mock_db, ns["name"], "1.0", code=ns["seed_code"])

        pipeline = _make_pipeline(mock_db)
        session = PipelineSession(session_id="iterative-3rounds", db_manager=mock_db)

        current_skill_id = skill.id
        for round_num in range(1, 4):
            t0 = time.monotonic()
            result = await pipeline.run_with_session(
                session=session,
                query=entry["query"],
                skill_name=entry["skill_name"],
                skill_id=current_skill_id,
            )
            with Session(mock_db.engine) as s:
                versions_after = s.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
            record_round(request.node.nodeid, RoundEntry(
                round_num=round_num, query=entry["query"],
                phases=[
                    PhaseEntry("retrieval", result.retrieval.ok, result.retrieval.elapsed_s, error=result.retrieval.error),
                    PhaseEntry("executor",  result.executor.ok,  result.executor.elapsed_s,  error=result.executor.error),
                    PhaseEntry("extractor", result.extractor.ok, result.extractor.elapsed_s, error=result.extractor.error),
                    PhaseEntry("tester",    result.tester.ok,    result.tester.elapsed_s,    error=result.tester.error),
                ],
                new_skill_id=result.new_skill_id,
                versions_total=len(versions_after),
                elapsed_s=time.monotonic() - t0,
            ))
            print(f"\n[round {round_num}] ok={result.extractor.ok} new_id={result.new_skill_id}")
            assert result.extractor.ok, f"Round {round_num} extractor failed"
            if result.new_skill_id:
                current_skill_id = result.new_skill_id

        with Session(mock_db.engine) as s:
            versions = s.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) >= 3, f"Expected >=3 versions after 3 rounds, got {len(versions)}"
