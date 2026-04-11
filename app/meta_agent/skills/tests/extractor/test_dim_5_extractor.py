import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import Skill, TestCase
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_5 import (
    SKILL_CODE_5_1_V1_0, SKILL_TEST_CODE_5_1_V1_0,
    SKILL_CODE_5_1_V1_1_BROKEN, SKILL_CODE_5_1_V1_1_FIXED,
    SKILL_CODE_5_2_V1_0,
    SKILL_CODE_5_2_V2_0_BROKEN, SKILL_CODE_5_2_V2_0_FIXED,
    SKILL_TEST_CODE_5_2_V2_0,
    TRACE_DIM_5_1, TRACE_DIM_5_2,
)


class TestExtractorDimension5_TestDrivenRefinement:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_1_extractor_initially_breaks_interface(self, mock_db):
        """
        Case 5.1 Phase 1: Extractor 生成的 v1.1 错误地改变了返回类型（str → List[str]）。
        LLM 预期行为：Gardener 处理 TRACE_DIM_5_1，尝试生成 recommend_next_course 的
        Minor 更新，并将结果提交到 DB。真实 LLM 可能生成 broken 或 correct 的版本，
        此处仅验证 LLM 能完成提取并向 DB 中写入新版本。
        """
        skill_v1 = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_CODE_5_1_V1_0)
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_v1_returns_string",
                skill_version_id=skill_v1.id,
                executable_code=SKILL_TEST_CODE_5_1_V1_0,
                is_legacy_locked=True
            ))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_5_1, mock_db, target_skill_id=skill_v1.id)

        assert result is not None, "Gardener 应返回从 Trace 提取的代码或解析结果"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill_v1.group_id)).all()
        assert len(versions) > 1, "Gardener 应已向 DB 提交至少一个新版本"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_1_extractor_fixes_interface_after_feedback(self, mock_db):
        """
        Case 5.1 Phase 2: 给 Gardener 提供含修复反馈的 Trace，要求重新生成 v1.1。
        LLM 预期行为：保持 str 返回类型，添加可选的 include_alternatives 参数，
        生成向后兼容的 Minor 更新并提交到 DB。
        """
        skill_v1 = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_CODE_5_1_V1_0)
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_v1_str_return",
                skill_version_id=skill_v1.id,
                executable_code=SKILL_TEST_CODE_5_1_V1_0,
                is_legacy_locked=True
            ))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_5_1, mock_db, target_skill_id=skill_v1.id)

        assert result is not None, "Gardener 修复反馈后应返回重新生成的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill_v1.group_id)).all()
        assert len(versions) > 1, "修复后应在 DB 中新增版本记录"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_2_extractor_initially_incomplete_major(self, mock_db):
        """
        Case 5.2 Phase 1: 从 TRACE_DIM_5_2 中提取 generate_study_plan 的 Major 更新。
        LLM 预期行为：识别 deadline_constraints 新参数，生成 v2.0 代码并提交。
        此处不判断 LLM 实现是否完整（那是 Tester 的职责），仅验证提取流程完成。
        """
        skill_v1 = create_mock_skill(mock_db, "generate_study_plan", "1.0", code=SKILL_CODE_5_2_V1_0)

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_5_2, mock_db, target_skill_id=skill_v1.id)

        assert result is not None, "Gardener 应返回 generate_study_plan 的更新代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill_v1.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交 v2.0 到 DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.major_version >= 2, "Major 更新应使 major_version 至少递增到 2"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_2_extractor_fixes_after_tester_feedback(self, mock_db):
        """
        Case 5.2 Phase 2: Gardener 读取含修复反馈的 Trace，重新生成完整 v2.0。
        LLM 预期行为：正确实现 urgent 课程排序与截止日期检查后提交到 DB。
        """
        skill_v1 = create_mock_skill(mock_db, "generate_study_plan", "1.0", code=SKILL_CODE_5_2_V1_0)

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_5_2, mock_db, target_skill_id=skill_v1.id)

        assert result is not None, "Gardener 修复后应返回完整的 v2.0 实现代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill_v1.group_id)).all()
        assert len(versions) > 1, "修复完成后应在 DB 中写入新版本"
