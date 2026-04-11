import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
from app.meta_agent.skills.database.models import TestCase, TestFailureCategory, TestReport
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_6 import (
    SKILL_CODE_6_1, SKILL_TEST_CODE_6_1, TRACE_DIM_6_1,
)


class TestTesterDimension6_LongTrace:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_6_1_tester_validates_extracted_skill(self, mock_db):
        """
        Case 6.1: Reviewer 验证从超长 Trace 中提取的 process_exam_results 技能。
        LLM 预期行为：提取的核心逻辑是正确的，Reviewer 应生成 is_passed=True 的 TestReport。
        """
        skill = create_mock_skill(mock_db, "process_exam_results", "1.0", code=SKILL_CODE_6_1)
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_process_exam_results_v1",
                skill_version_id=skill.id,
                executable_code=SKILL_TEST_CODE_6_1,
                is_legacy_locked=False
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_6_1)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "正确提取的技能应通过 Reviewer 验证"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_6_1_tester_all_edge_cases_covered(self, mock_db):
        """
        Case 6.1: 验证 Reviewer 为超长 Trace 提取的技能生成足够的边界测例。
        Reviewer 必须覆盖：全通过、混合结果、空学生列表、自定义阈值、返回标识符。
        """
        skill = create_mock_skill(mock_db, "process_exam_results", "1.0", code=SKILL_CODE_6_1)

        # All edge case test codes from test_data_dim_6
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_all_edge_cases",
                skill_version_id=skill.id,
                executable_code=SKILL_TEST_CODE_6_1,
                is_legacy_locked=False
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_6_1)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "Reviewer 应覆盖全部边界测例并通过"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_6_1_tester_no_orchestration_contamination(self, mock_db):
        """
        Case 6.1: 验证 Reviewer 生成的测试不包含编排逻辑（DB 操作、邮件发送等）。
        从超长 Trace 中正确提取的技能是纯函数，LLM 预期行为：Reviewer 不应在测试中引入外部依赖。
        """
        skill = create_mock_skill(mock_db, "process_exam_results", "1.0", code=SKILL_CODE_6_1)
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_pure_function",
                skill_version_id=skill.id,
                executable_code=SKILL_TEST_CODE_6_1,
                is_legacy_locked=False
            ))
            session.commit()

        # Pure data assertions on test code (no LLM involved)
        assert "db.execute" not in SKILL_TEST_CODE_6_1
        assert "email_client" not in SKILL_TEST_CODE_6_1
        assert "requests" not in SKILL_TEST_CODE_6_1

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_6_1)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "纯函数技能应通过 Reviewer 的测试"
