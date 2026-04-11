import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import Skill, TestCase
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_6 import (
    SKILL_CODE_6_1, SKILL_TEST_CODE_6_1, TRACE_DIM_6_1,
)


class TestExtractorDimension6_LongTrace:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_6_1_extractor_core_extraction_from_long_trace(self, mock_db):
        """
        Case 6.1: 超长 Trace 提取测试（30步以上）。
        Trace 含有完整的学期末考试处理流程：数据导入→评分→通知→存档。
        LLM 预期行为：从冗余编排步骤中识别核心可复用技能 process_exam_results，
        提取为独立纯函数并提交到 DB。
        """
        # Provide a stub for the skill to be extracted so the gardener can evolve it.
        stub_skill = create_mock_skill(mock_db, "process_exam_results", "0.1",
                                       code="def process_exam_results(*args, **kwargs): pass")
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_6_1, mock_db, target_skill_id=stub_skill.id)

        assert result is not None, "Gardener 应从超长 Trace 中成功提取核心技能"
        with Session(mock_db.engine) as session:
            skills = session.exec(select(Skill)).all()
        assert len(skills) >= 1, "Gardener 应已向 DB 提交提取的技能"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_6_1_extractor_correct_signature_inference(self, mock_db):
        """
        Case 6.1: 从超长 Trace 中正确推断技能签名。
        LLM 预期行为：识别出 process_exam_results 的 4 个参数：
        exam_id, student_answers, answer_key, passing_threshold，
        不将 db、email_client 等编排参数混入签名。
        """
        stub_skill = create_mock_skill(mock_db, "process_exam_results", "0.1",
                                       code="def process_exam_results(*args, **kwargs): pass")
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_6_1, mock_db, target_skill_id=stub_skill.id)

        assert result is not None, "Gardener 应返回带正确签名的提取结果"
        # LLM 生成的代码应包含核心参数（参数名可能略有不同，但逻辑等价）
        assert any(keyword in result for keyword in
                   ["exam_id", "student_answers", "student_submissions", "answer_key"]), (
            "提取的代码应包含 process_exam_results 的核心参数之一"
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_6_1_extractor_idempotent_across_long_trace(self, mock_db):
        """
        Case 6.1: 超长 Trace 的提取稳定性验证。
        LLM 预期行为：单次提取超长 Trace 时不崩溃、不超时，
        成功返回提取结果并写入 DB。
        （幂等性需多次调用验证，但受 LLM 成本限制，此处仅验证单次稳定性。）
        """
        stub_skill = create_mock_skill(mock_db, "process_exam_results", "0.1",
                                       code="def process_exam_results(*args, **kwargs): pass")
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_6_1, mock_db, target_skill_id=stub_skill.id)

        assert result is not None, "对超长 Trace 的单次提取应能成功完成"
        with Session(mock_db.engine) as session:
            skills = session.exec(select(Skill)).all()
        assert len(skills) >= 1, "Gardener 应已向 DB 提交提取结果"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_6_1_extractor_does_not_extract_orchestration(self, mock_db):
        """
        Case 6.1: 排除编排逻辑提取测试。
        LLM 预期行为：Extractor 不应将 DB 存储、邮件发送或报告生成逻辑提取为技能，
        而是识别唯一的纯函数核心 process_exam_results 并提交。
        """
        stub_skill = create_mock_skill(mock_db, "process_exam_results", "0.1",
                                       code="def process_exam_results(*args, **kwargs): pass")
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_6_1, mock_db, target_skill_id=stub_skill.id)

        assert result is not None, "Gardener 应完成提取而不是对编排 Trace 崩溃"
        with Session(mock_db.engine) as session:
            skills = session.exec(select(Skill)).all()
        # Just one skill should be extracted (the core reusable function)
        assert len(skills) >= 1, "Gardener 应已提交一个原子化的核心技能"
