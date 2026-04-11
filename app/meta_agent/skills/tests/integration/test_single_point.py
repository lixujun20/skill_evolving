import ast
import pytest
from sqlmodel import Session, select

from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import Skill
from app.meta_agent.skills.pipeline import SkillEvolvingPipeline
from app.meta_agent.skills.tests.conftest import create_mock_skill
from app.meta_agent.skills.tests.test_data_integration import (
    SKILL_CODE_INT_A_V1_0,
    TRACE_INT_A_1,
    SKILL_CODE_INT_B_V1_0,
    TRACE_INT_B_1,
    SKILL_CODE_INT_C_V1_0,
    TRACE_INT_C_1,
)


class TestSinglePointIntegration:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_int_A_code_debug_extraction(self, mock_db):
        """
        Scenario A: Auto-debug skill first extraction.

        The agent trace shows the current regex-based skill misses real compilation
        errors (SyntaxError, NameError). The agent falls back to compile/exec.
        Expected: Extractor upgrades the skill to an actual code compilation/execution
        approach — this is a major behavioral change even if the return schema stays
        the same (major_version >= 2).
        """
        skill = create_mock_skill(mock_db, "auto_debug_code", "1.0", code=SKILL_CODE_INT_A_V1_0)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_INT_A_1, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener should return generated Python code"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener should have committed a new skill version to DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.major_version >= 2, (
            "Replacing naive regex with real compilation is a major behavioral change; "
            "major_version should be incremented to >= 2"
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_int_B_grade_analysis_extraction(self, mock_db):
        """
        Scenario B: Grade analysis skill first extraction.

        The current skill only returns an average as a plain string. The agent had
        to manually compute mean, median, std dev, grade distribution, and at-risk
        list. Expected: Extractor creates a comprehensive statistics skill that
        returns a dict — a major update (return type changes from str to dict).
        """
        skill = create_mock_skill(
            mock_db, "analyze_student_grades", "1.0", code=SKILL_CODE_INT_B_V1_0
        )
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_INT_B_1, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener should return generated skill code"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener should have committed a new skill version to DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.major_version >= 2, (
            "Changing return type from str to dict is a breaking change; "
            "major_version should be incremented to >= 2"
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_int_C_learning_discussion_first_extraction(self, mock_db):
        """
        Scenario C: Learning discussion skill first extraction.

        The hardcoded 3-message template skill is too generic. The agent generates a
        5-round substantive discussion manually. Expected: Extractor adds a
        'num_rounds' parameter with a backward-compatible default — minor update
        (minor_version >= 1, major_version stays at 1).
        """
        skill = create_mock_skill(
            mock_db, "generate_learning_discussion", "1.0", code=SKILL_CODE_INT_C_V1_0
        )
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_INT_C_1, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener should return generated skill code"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener should have committed a new skill version to DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.minor_version >= 1, (
            "Adding an optional num_rounds parameter is a backward-compatible minor update; "
            "minor_version should be >= 1"
        )


class TestV2WorkflowPlan:
    """Verify that v2 planning phase populates workflow_plan on the execution trace."""

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_workflow_plan_populated_after_executor(self, mock_db):
        """
        Run the full pipeline from a fresh query (no prebuilt trace).
        Executor phase must be triggered, which causes planning phase to run first.
        Result: execution_trace.workflow_plan should be a non-empty valid Python string.
        """
        pipeline = SkillEvolvingPipeline()
        pipeline.db_manager = mock_db

        result = await pipeline.run(
            query="Calculate the average of a list of student grades and return only the mean.",
            skill_name="grade_mean",
            seed_code=(
                "def grade_mean(scores: list) -> float:\n"
                "    return sum(scores) / len(scores)\n"
            ),
            skip_tester=True,   # skip reviewer to keep test fast
        )

        assert result.execution_trace is not None, "pipeline must produce an execution trace"

        plan = result.execution_trace.workflow_plan
        assert plan is not None, (
            "workflow_plan must be populated by the planning phase (v2 feature)"
        )
        assert len(plan.strip()) > 10, "workflow_plan must be a non-trivial string"

        # Verify it is syntactically valid Python
        try:
            ast.parse(plan)
        except SyntaxError as e:
            pytest.fail(f"workflow_plan is not valid Python: {e}\n---\n{plan}")

