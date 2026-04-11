"""
AMU Long-Term Integration Tests — Full Real Pipeline

Tests skill evolution across interleaved query sequences.
Each test:
1. Seeds multiple skill groups into isolated DB
2. Runs queries in interleaved order using PipelineSession (reuses Docker container)
3. Asserts skills in each group evolve across rounds
"""
from __future__ import annotations

import asyncio
import json
import pytest
import time
from pathlib import Path
from sqlmodel import Session, select
from typing import Dict

from app.meta_agent.skills.pipeline import SkillEvolvingPipeline, PipelineSession
from app.meta_agent.skills.database.models import Skill
from app.meta_agent.skills.tests.conftest import create_mock_skill
from app.meta_agent.skills.tests.integration.conftest import (
    record_round, RoundEntry, PhaseEntry,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixtures() -> dict:
    path = FIXTURES_DIR / "long_term.json"
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}. Run generators/build_fixtures.py first.")
    with open(path) as f:
        return json.load(f)


def _make_pipeline(mock_db) -> SkillEvolvingPipeline:
    """Create a pipeline that reuses the test DB manager (avoids second DB connection)."""
    pipeline = SkillEvolvingPipeline()
    pipeline.db_manager = mock_db
    return pipeline


class TestAMULongTermEvolution:

    @pytest.mark.llm
    @pytest.mark.asyncio
    @pytest.mark.timeout(1800)  # multiple rounds with real pipeline
    async def test_lt_group_A_skill_evolves_2rounds(self, mock_db, request):
        """Group A skill evolves across 2 non-consecutive queries (full pipeline)."""
        data = load_fixtures()
        groups = data["groups"]

        group_A = groups["A"]
        skill = create_mock_skill(mock_db, group_A["skill_name"], "1.0", code=group_A["seed_code"])
        for ns in group_A.get("noise_skills", []):
            create_mock_skill(mock_db, ns["name"], "1.0", code=ns["seed_code"])

        pipeline = _make_pipeline(mock_db)
        session = PipelineSession(session_id="lt-group-A", db_manager=mock_db)

        queries = group_A["queries"]
        assert len(queries) >= 2, "Need at least 2 queries for group A"

        current_skill_id = skill.id
        results = []
        for i, query in enumerate(queries[:2]):
            t0 = time.monotonic()
            try:
                r = await asyncio.wait_for(
                    pipeline.run_with_session(
                        session=session,
                        query=query,
                        skill_name=group_A["skill_name"],
                        skill_id=current_skill_id,
                    ),
                    timeout=800.0,
                )
                results.append(r)
                if r.new_skill_id:
                    current_skill_id = r.new_skill_id
                with Session(mock_db.engine) as s:
                    vcount = len(s.exec(select(Skill).where(Skill.group_id == skill.group_id)).all())
                record_round(request.node.nodeid, RoundEntry(
                    round_num=i + 1, query=query, group="A",
                    phases=[
                        PhaseEntry("retrieval", r.retrieval.ok, r.retrieval.elapsed_s, error=r.retrieval.error),
                        PhaseEntry("executor",  r.executor.ok,  r.executor.elapsed_s,  error=r.executor.error),
                        PhaseEntry("extractor", r.extractor.ok, r.extractor.elapsed_s, error=r.extractor.error),
                        PhaseEntry("tester",    r.tester.ok,    r.tester.elapsed_s,    error=r.tester.error),
                    ],
                    new_skill_id=r.new_skill_id,
                    versions_total=vcount,
                    elapsed_s=time.monotonic() - t0,
                ))
                print(f"[A{i+1}] ok={r.extractor.ok} new_id={r.new_skill_id}")
            except asyncio.TimeoutError:
                print(f"[A{i+1}] timed out")
                results.append(None)

        # At least round 1 must succeed
        assert results[0] is not None and results[0].extractor.ok, "Round A1 must succeed"

        with Session(mock_db.engine) as s:
            versions = s.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) >= 2, f"After 2 rounds, expected >=2 versions, got {len(versions)}"

    @pytest.mark.llm
    @pytest.mark.asyncio
    @pytest.mark.timeout(2700)  # interleaved multi-group sequence
    async def test_lt_interleaved_sequence(self, mock_db, request):
        """
        Run the full interleaved sequence from long_term.json.
        Each entry has group + query_idx. Related queries appear non-consecutively.
        Uses shared PipelineSession (one Docker container for all rounds).
        """
        data = load_fixtures()
        groups = data["groups"]
        sequence = data["sequence"]

        # Seed all group skills
        skill_map: Dict[str, Skill] = {}
        for gk, gdata in groups.items():
            sk = create_mock_skill(mock_db, gdata["skill_name"], "1.0", code=gdata["seed_code"])
            skill_map[gk] = sk
            for ns in gdata.get("noise_skills", []):
                create_mock_skill(mock_db, ns["name"], "1.0", code=ns["seed_code"])

        pipeline = _make_pipeline(mock_db)
        session = PipelineSession(session_id="lt-interleaved", db_manager=mock_db)

        for entry in sequence:
            gk = entry["group"]
            query = groups[gk]["queries"][entry["query_idx"]]
            current_skill = skill_map[gk]
            t0 = time.monotonic()

            try:
                r = await asyncio.wait_for(
                    pipeline.run_with_session(
                        session=session,
                        query=query,
                        skill_name=groups[gk]["skill_name"],
                        skill_id=current_skill.id,
                    ),
                    timeout=800.0,
                )
                if r and r.new_skill_id:
                    with Session(mock_db.engine) as s:
                        new_sk = s.get(Skill, r.new_skill_id)
                        if new_sk:
                            skill_map[gk] = new_sk
                with Session(mock_db.engine) as s:
                    vcount = len(s.exec(select(Skill).where(Skill.group_id == current_skill.group_id)).all())
                record_round(request.node.nodeid, RoundEntry(
                    round_num=entry["seq_id"], query=query, group=gk,
                    phases=[
                        PhaseEntry("retrieval", r.retrieval.ok, r.retrieval.elapsed_s, error=r.retrieval.error),
                        PhaseEntry("executor",  r.executor.ok,  r.executor.elapsed_s,  error=r.executor.error),
                        PhaseEntry("extractor", r.extractor.ok, r.extractor.elapsed_s, error=r.extractor.error),
                        PhaseEntry("tester",    r.tester.ok,    r.tester.elapsed_s,    error=r.tester.error),
                    ],
                    new_skill_id=r.new_skill_id if r else None,
                    versions_total=vcount,
                    elapsed_s=time.monotonic() - t0,
                ))
                print(f"[seq {entry['seq_id']} {gk}] ok={r.extractor.ok if r else 'timeout'}")
            except asyncio.TimeoutError:
                print(f"[seq {entry['seq_id']} {gk}] timed out")

        # At least 1 group must have evolved (>=2 versions)
        evolved = 0
        for gk, gdata in groups.items():
            current = skill_map[gk]
            with Session(mock_db.engine) as s:
                versions = s.exec(select(Skill).where(Skill.group_id == current.group_id)).all()
            if len(versions) >= 2:
                evolved += 1

        assert evolved >= 1, (
            f"At least 1 group should have evolved. Evolved: {evolved}/{len(groups)}"
        )
        assert session.round_count == len(sequence), (
            f"Expected {len(sequence)} rounds recorded, got {session.round_count}"
        )
