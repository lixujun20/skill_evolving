import ast
import pytest
from sqlmodel import Session, select
from unittest.mock import AsyncMock, patch

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

    @pytest.mark.asyncio
    async def test_joint_planner_filters_unused_shared_skills(self, mock_db):
        pipeline = SkillEvolvingPipeline()
        pipeline.db_manager = mock_db

        skill_a = create_mock_skill(
            mock_db,
            "mean_skill",
            "1.0",
            code=(
                "def mean_skill(scores):\n"
                "    return sum(scores) / len(scores)\n"
            ),
        )
        skill_b = create_mock_skill(
            mock_db,
            "variance_skill",
            "1.0",
            code=(
                "def variance_skill(scores):\n"
                "    mu = sum(scores) / len(scores)\n"
                "    return sum((x-mu)**2 for x in scores) / len(scores)\n"
            ),
        )

        fake_response = (
            "## 共享技能\n"
            "### _shared_sum_stats\n"
            "描述: 计算求和与均值\n"
            "来源: historical\n"
            "```python\n"
            "def _shared_sum_stats(values):\n"
            "    total = sum(values)\n"
            "    return total, total / len(values)\n"
            "```\n\n"
            "### _shared_unused\n"
            "描述: 不会被调用\n"
            "来源: historical\n"
            "```python\n"
            "def _shared_unused(values):\n"
            "    return values\n"
            "```\n\n"
            "## 工作流计划\n"
            "```python\n"
            "stats = _shared_sum_stats(SCORES)  # TODO: Gardener 待创建\n"
            "return_(stats)\n"
            "```"
        )

        with patch(
            "app.llm.LLM.ask",
            new=AsyncMock(side_effect=[
                "COMMON: shared sum-based statistics",
                fake_response,
            ]),
        ):
            planning = await pipeline._run_joint_refactor_planner(
                query="Calculate summary statistics for a list of scores.",
                retrieved_skills=[skill_a, skill_b],
                session=None,
                query_embedding=None,
                planner_mode="FRESH",
                rubric={"reason": "run_joint_planner"},
            )
        assert planning is not None
        assert planning.metadata["planner_strategy"] == "joint_refactor"
        assert [ps.name for ps in planning.proposed_skills] == ["_shared_sum_stats"]
        ast.parse(planning.workflow_plan)

    @pytest.mark.asyncio
    async def test_planner_shortcut_falls_back_to_legacy(self, mock_db):
        pipeline = SkillEvolvingPipeline()
        pipeline.db_manager = mock_db

        fake_response = (
            "## 候选技能提取\n"
            "### lightweight_helper\n"
            "描述: 简单辅助\n"
            "来源查询: \"legacy\"\n"
            "```python\n"
            "def lightweight_helper(x):\n"
            "    return x\n"
            "```\n\n"
            "## 工作流计划\n"
            "```python\n"
            "result = lightweight_helper(INPUT)  # TODO: Gardener 待创建\n"
            "return_(result)\n"
            "```"
        )
        with patch("app.llm.LLM.ask", new=AsyncMock(return_value=fake_response)):
            planning = await pipeline._run_planning_phase(
                query="Hi",
                retrieved_skills=[],
                session=None,
                query_embedding=None,
            )
        assert planning is not None
        assert planning.metadata["planner_strategy"] == "legacy_fallback"
        assert len(planning.proposed_skills) == 1
        ast.parse(planning.workflow_plan)
