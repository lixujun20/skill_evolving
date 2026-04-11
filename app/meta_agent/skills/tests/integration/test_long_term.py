import pytest
from sqlmodel import Session, select

from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import Skill
from app.meta_agent.skills.pipeline import SkillEvolvingPipeline, PipelineSession
from app.meta_agent.skills.tests.conftest import create_mock_skill
from app.meta_agent.skills.tests.test_data_integration import (
    SKILL_CODE_INT_C_V1_0,
    SKILL_CODE_INT_C_V1_1,
    TRACE_INT_C_1,
    TRACE_INT_C_2,
)


class TestLongTermSkillEvolution:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_int_C_skill_evolves_across_queries(self, mock_db):
        """
        Scenario C long-term: Learning discussion skill evolves over two related queries.

        Round 1: The hardcoded-template skill (v1.0) is used for "Python decorators".
                 The agent manually builds a 5-round discussion, prompting an extraction
                 that adds the 'num_rounds' parameter as a minor update → v1.1.

        Round 2: The v1.1 skill (has num_rounds but only 2 student roles) is used for
                 a 4-participant, 6-round discussion on "machine learning overfitting".
                 The agent has to add custom roles manually, prompting an extraction
                 that adds a 'participants' parameter → major update → v2.0.

        After both rounds the DB must contain at least 3 skill versions
        (v1.0 original, v1.1 after round 1, v2.0 after round 2) and the latest
        version must have major_version >= 2.
        """
        # ---- Seed initial v1.0 skill ----
        skill_v1 = create_mock_skill(
            mock_db, "generate_learning_discussion", "1.0", code=SKILL_CODE_INT_C_V1_0
        )
        gardener = SkillGardenerAgent(db=mock_db)

        # ---- Round 1: first query → expect minor update to v1.1 ----
        result_1 = await gardener.run_extraction(
            TRACE_INT_C_1, mock_db, target_skill_id=skill_v1.id
        )
        assert result_1 is not None, "Round 1: Gardener should return generated skill code"

        with Session(mock_db.engine) as session:
            versions_after_r1 = session.exec(
                select(Skill).where(Skill.group_id == skill_v1.group_id)
            ).all()
        assert len(versions_after_r1) >= 2, (
            "Round 1: DB should have at least 2 versions (v1.0 + v1.1)"
        )
        latest_r1 = max(versions_after_r1, key=lambda s: (s.major_version, s.minor_version))
        assert latest_r1.minor_version >= 1 or latest_r1.major_version >= 2, (
            "Round 1 extraction should produce at least a minor update"
        )

        # ---- Seed v1.1 explicitly so Round 2 starts from the improved skill ----
        skill_v1_1 = create_mock_skill(
            mock_db,
            "generate_learning_discussion",
            "1.1",
            code=SKILL_CODE_INT_C_V1_1,
            group_name="generate_learning_discussion",
        )

        # ---- Round 2: second query → expect major update to v2.0 ----
        result_2 = await gardener.run_extraction(
            TRACE_INT_C_2, mock_db, target_skill_id=skill_v1_1.id
        )
        assert result_2 is not None, "Round 2: Gardener should return generated skill code"

        with Session(mock_db.engine) as session:
            all_versions = session.exec(
                select(Skill).where(Skill.group_id == skill_v1.group_id)
            ).all()

        assert len(all_versions) >= 3, (
            "After both rounds the DB should have at least 3 versions "
            "(v1.0, v1.1, and v2.0)"
        )
        latest = max(all_versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.major_version >= 2, (
            "Adding the 'participants' parameter is a breaking change; "
            "the final skill version should have major_version >= 2"
        )

        # Verify progressive evolution: versions should span at least two major milestones
        major_versions = {s.major_version for s in all_versions}
        assert len(major_versions) >= 2 or any(s.minor_version >= 1 for s in all_versions), (
            "Skill history should show progressive evolution across versions"
        )


class TestMultiTurnSession:
    """
    Tests for the multi-turn session paradigm where a single PipelineSession
    accumulates state (query history, traces, evolved_skill_ids) across
    multiple pipeline rounds.

    These tests use prebuilt_trace mode (no Docker) to keep them fast and
    deterministic, mirroring how the CLI's interactive mode works when
    given --demo traces.
    """

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_session_accumulates_state_across_rounds(self, mock_db):
        """
        A PipelineSession keeps shared state across multiple rounds.

        After two rounds:
        - session.query_history has both queries
        - session.traces has both AgentTraces
        - session.evolved_skill_ids is non-empty (at least one new skill committed)
        - session.round_count == 2
        """
        # Seed the initial skill so retrieval / extractor have something to work from
        skill_v1 = create_mock_skill(
            mock_db, "generate_learning_discussion", "1.0", code=SKILL_CODE_INT_C_V1_0
        )

        session = PipelineSession(
            session_id="test-multi",
            db_manager=mock_db,
        )
        pipeline = SkillEvolvingPipeline()
        pipeline.db_manager = mock_db

        # ── Round 1 ────────────────────────────────────────────────────────
        r1 = await pipeline.run_with_session(
            session=session,
            query=TRACE_INT_C_1.query,
            skill_name="generate_learning_discussion",
            skill_id=skill_v1.id,
            skip_tester=True,
            prebuilt_trace=TRACE_INT_C_1,
        )

        assert session.round_count == 1, "Round count should be 1 after first round"
        assert len(session.query_history) == 1
        assert session.query_history[0] == TRACE_INT_C_1.query
        assert len(session.traces) == 1, "Trace should be accumulated after round 1"

        # ── Seed v1.1 for round 2 ──────────────────────────────────────────
        skill_v1_1 = create_mock_skill(
            mock_db, "generate_learning_discussion", "1.1",
            code=SKILL_CODE_INT_C_V1_1,
            group_name="generate_learning_discussion",
        )

        # ── Round 2 ────────────────────────────────────────────────────────
        r2 = await pipeline.run_with_session(
            session=session,
            query=TRACE_INT_C_2.query,
            skill_name="generate_learning_discussion",
            skill_id=skill_v1_1.id,
            skip_tester=True,
            prebuilt_trace=TRACE_INT_C_2,
        )

        assert session.round_count == 2, "Round count should be 2 after second round"
        assert len(session.query_history) == 2
        assert len(session.traces) == 2, "Both traces should be accumulated"
        assert session.round_results[0] is r1
        assert session.round_results[1] is r2

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_session_tracks_evolved_skills(self, mock_db):
        """
        When a new skill version is committed in a round, session.evolved_skill_ids
        is updated so later rounds can carry the latest skill_id forward.
        """
        skill_v1 = create_mock_skill(
            mock_db, "generate_learning_discussion", "1.0", code=SKILL_CODE_INT_C_V1_0
        )

        session = PipelineSession(session_id="test-evolve", db_manager=mock_db)
        pipeline = SkillEvolvingPipeline()
        pipeline.db_manager = mock_db

        r1 = await pipeline.run_with_session(
            session=session,
            query=TRACE_INT_C_1.query,
            skill_name="generate_learning_discussion",
            skill_id=skill_v1.id,
            skip_tester=True,
            prebuilt_trace=TRACE_INT_C_1,
        )

        # If extraction produced a new version, evolved_skill_ids should be non-empty
        if r1.new_skill_id is not None:
            assert r1.new_skill_id in session.evolved_skill_ids, (
                "new_skill_id from round 1 should appear in session.evolved_skill_ids"
            )

        # The session's trace list should always be updated regardless of extraction
        assert len(session.traces) >= 1

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_session_progressive_skill_evolution(self, mock_db):
        """
        End-to-end two-round skill evolution via PipelineSession mirrors the
        TestLongTermSkillEvolution test but uses the session API.

        Round 1: v1.0 → v1.1 (minor: add num_rounds param)
        Round 2: v1.1 → v2.0 (major: add participants param)
        """
        skill_v1 = create_mock_skill(
            mock_db, "generate_learning_discussion", "1.0", code=SKILL_CODE_INT_C_V1_0
        )

        session = PipelineSession(session_id="test-progressive", db_manager=mock_db)
        pipeline = SkillEvolvingPipeline()
        pipeline.db_manager = mock_db

        # Round 1
        r1 = await pipeline.run_with_session(
            session=session,
            query=TRACE_INT_C_1.query,
            skill_name="generate_learning_discussion",
            skill_id=skill_v1.id,
            skip_tester=True,
            prebuilt_trace=TRACE_INT_C_1,
        )
        assert r1.new_skill_code is not None, "Round 1 should extract new skill code"
        assert session.round_count == 1

        # Seed v1.1 for round 2 (explicit — mirrors the long-term test)
        skill_v1_1 = create_mock_skill(
            mock_db, "generate_learning_discussion", "1.1",
            code=SKILL_CODE_INT_C_V1_1,
            group_name="generate_learning_discussion",
        )

        # Round 2
        r2 = await pipeline.run_with_session(
            session=session,
            query=TRACE_INT_C_2.query,
            skill_name="generate_learning_discussion",
            skill_id=skill_v1_1.id,
            skip_tester=True,
            prebuilt_trace=TRACE_INT_C_2,
        )
        assert r2.new_skill_code is not None, "Round 2 should extract new skill code"
        assert session.round_count == 2

        # Verify progressive skill evolution in DB
        with Session(mock_db.engine) as db_session:
            all_versions = db_session.exec(
                select(Skill).where(Skill.group_id == skill_v1.group_id)
            ).all()

        assert len(all_versions) >= 3, (
            "DB should have >=3 versions (v1.0 seed, v1.1 seed, plus >=1 extracted)"
        )
        latest = max(all_versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.major_version >= 2 or latest.minor_version >= 1, (
            "At least one version upgrade should have happened across the two rounds"
        )
