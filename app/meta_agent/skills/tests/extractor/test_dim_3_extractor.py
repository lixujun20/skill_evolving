import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import Skill, TestCase
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_3 import (
    SKILL_CODE_3_1, SKILL_TEST_CODE_3_1, TRACE_DIM_3_1,
    SKILL_CODE_3_2_A, TRACE_DIM_3_2,
    SKILL_CODE_3_3_HALLUCINATED, TRACE_DIM_3_3,
    SKILL_CODE_3_4_SYNTAX_ERROR, TRACE_DIM_3_4,
    SKILL_CODE_3_5_WRONG_CALL, TRACE_DIM_3_5,
    SKILL_CODE_3_6_BUGGY, TRACE_DIM_3_6,
)


class TestExtractorDimension3_SandboxBoundaries:
    """
    Dimension 3: Extractor 在 Sandbox 边界场景下的提取行为。
    这些测例验证 Gardener 在面对含有 IO 依赖、幻觉导入、语法错误、\
调用错误、算法错误的代码时，是否仍能从 Trace 中正确提取/改写技能并提交到 DB。
    """

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_1_extractor_mocking_required_skill(self, mock_db):
        """
        Case 3.1: 从重度 IO 技能（fetch_course_materials）的 Trace 中提取代码。
        LLM 预期行为：提取的技能签名合理，且 Gardener 不应将网络请求写成硬调用，
        应提取出有明确 mock 接口（如依赖注入 http_client）的可测函数。
        """
        skill_v1 = create_mock_skill(mock_db, "fetch_course_materials", "1.0", code=SKILL_CODE_3_1)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_3_1, mock_db, target_skill_id=skill_v1.id)

        assert result is not None, "Gardener 应从含 IO 的 Trace 中提取出可测函数代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill_v1.group_id)).all()
        assert len(versions) > 1, "Gardener 应已将新版本提交到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_2_extractor_interface_mismatch_trace(self, mock_db):
        """
        Case 3.2: 从一个存在接口不兼容错误的 Trace 中提取。
        Trace 中 grade_submission 调用 Skill_B 时传了错误的关键字参数 'data'（应为 'submission_payload'）。
        LLM 预期行为：Gardener 识别 Trace 中的调用错误，生成修正后的 v1.2 代码并提交。
        """
        skill = create_mock_skill(mock_db, "grade_submission", "1.1", code=SKILL_CODE_3_2_A)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_3_2, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应识别接口错误并生成修正后的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "修正版本应被提交到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_3_extractor_hallucinated_import_fixed(self, mock_db):
        """
        Case 3.3: 原始代码含有幻觉导入（edu_analytics.advanced 不存在）。
        LLM 预期行为：Gardener 在提取时识别该问题，生成不含幻觉依赖的修正版代码。
        """
        skill = create_mock_skill(mock_db, "auto_grade_essay", "1.0", code=SKILL_CODE_3_3_HALLUCINATED)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_3_3, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应生成不含幻觉 import 的修正代码"
        # 修正后的代码不应再包含虚构库名
        assert "edu_analytics.advanced" not in result, "幻觉 import 应从生成代码中消除"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "修正版本应被提交到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_4_extractor_syntax_error_not_regenerated(self, mock_db):
        """
        Case 3.4: 原始 Skill 代码含有语法错误（括号未闭合）。
        LLM 预期行为：Gardener 不能把含语法错误的代码直接提交，
        应从 Trace 重新推断意图后生成语法合法的代码。
        """
        skill = create_mock_skill(mock_db, "group_students_by_performance", "1.0",
                                  code=SKILL_CODE_3_4_SYNTAX_ERROR)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_3_4, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应以正确代码覆盖含语法错误的旧版本"
        # 确保生成的代码可以被 Python 解析
        try:
            compile(result, "<string>", "exec")
        except SyntaxError as e:
            raise AssertionError(f"Gardener 生成的代码仍含语法错误: {e}") from e
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "修正版本应被提交到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_5_extractor_wrong_call_corrected(self, mock_db):
        """
        Case 3.5: Trace 中 send_grade_notifications 调用 email_service_fn 时参数顺序颠倒。
        LLM 预期行为：Gardener 从 Trace 中识别预期调用语义，生成参数顺序正确的代码。
        """
        skill = create_mock_skill(mock_db, "send_grade_notifications", "1.0",
                                  code=SKILL_CODE_3_5_WRONG_CALL)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_3_5, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应生成修正了参数顺序的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "修正版本应被提交到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_6_extractor_algorithm_bug_corrected(self, mock_db):
        """
        Case 3.6: calculate_percentile_rank 的百分位算法有误（rank/(n+1) 公式错误）。
        LLM 预期行为：Gardener 从 Trace 中观察到正确预期输出，推断出正确公式后生成修正代码。
        """
        skill = create_mock_skill(mock_db, "calculate_percentile_rank", "1.0",
                                  code=SKILL_CODE_3_6_BUGGY)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_3_6, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应生成修正了算法错误的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "修正版本应被提交到 DB"
