import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
from app.meta_agent.skills.database.models import TestCase, TestFailureCategory, TestReport
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_4 import (
    SKILL_CODE_4_1_V1_0, SKILL_CODE_4_1_V1_5,
    SKILL_TEST_CODE_4_1_V1_0, SKILL_TEST_CODE_4_1_V1_1,
    SKILL_TEST_CODE_4_1_V1_2, SKILL_TEST_CODE_4_1_V1_3,
    SKILL_TEST_CODE_4_1_V1_4, SKILL_TEST_CODE_4_1_V1_5,
    SKILL_CODE_4_3_V1_1_BUGGY, SKILL_CODE_4_3_V1_2_STILL_BUGGY,
    SKILL_CODE_4_3_V1_2_FIXED, SKILL_TEST_CODE_4_3_LOCKED,
    TRACE_DIM_4_1_STEP5, TRACE_DIM_4_3,
)


class TestTesterDimension4_HistoryTimeline:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_4_1_v1_5_all_locked_tests_pass(self, mock_db):
        """
        Case 4.1: v1.5 时，Reviewer 必须一次性加载全部 5 个版本的锁定测例（共 10 个函数）。
        LLM 预期行为：新代码 v1.5 必须通过所有历史累计的测试，不能有任何回归。
        """
        skill = create_mock_skill(mock_db, "track_learning_progress", "1.5", code=SKILL_CODE_4_1_V1_5)

        # All 5 locked test suites simulate accumulated history
        with Session(mock_db.engine) as session:
            for i, code in enumerate([
                SKILL_TEST_CODE_4_1_V1_0, SKILL_TEST_CODE_4_1_V1_1,
                SKILL_TEST_CODE_4_1_V1_2, SKILL_TEST_CODE_4_1_V1_3,
                SKILL_TEST_CODE_4_1_V1_4,
            ]):
                session.add(TestCase(
                    case_name=f"test_lock_v1_{i}",
                    skill_version_id=skill.id,
                    executable_code=code,
                    is_legacy_locked=True
                ))
            # Also add new test for v1.5
            session.add(TestCase(
                case_name="test_v1_5_output_format",
                skill_version_id=skill.id,
                executable_code=SKILL_TEST_CODE_4_1_V1_5,
                is_legacy_locked=False
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_4_1_STEP5)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "v1.5 升级应通过所有历史 locked 测例"

        # Verify all 6 test cases (5 locked + 1 new) are still in DB
        with Session(mock_db.engine) as session:
            cases = session.exec(
                select(TestCase).where(TestCase.skill_version_id == skill.id)
            ).all()
        assert len(cases) >= 6, f"Should have at least 6 test cases (pre-loaded), got {len(cases)}"
        locked_count = sum(1 for c in cases if c.is_legacy_locked)
        assert locked_count == 5

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_4_3_buggy_base_blocks_new_feature(self, mock_db):
        """
        Case 4.3: 带伤版本的回归阻截。
        Skill_A v1.1 最后一条 TestReport 是 is_passed=False（带伤版本）。
        LLM 预期行为：当 Trace 试图在此基线上升级到 v1.2 时，
        Reviewer 必须先让 bug 测试仍然失败，强制 Gardener 先修好 bug 再发版。
        """
        skill_v1_1 = create_mock_skill(mock_db, "calculate_gpa", "1.1", code=SKILL_CODE_4_3_V1_1_BUGGY)

        # Store the locked test that catches the bug
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_gpa_locked_v1_1",
                skill_version_id=skill_v1_1.id,
                executable_code=SKILL_TEST_CODE_4_3_LOCKED,
                is_legacy_locked=True
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_v1_1.id, TRACE_DIM_4_3)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_v1_1.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "带伤版本应被 Reviewer 锁定测例拦截"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_4_3_fixed_version_clears_debt(self, mock_db):
        """
        Case 4.3 续: 当 Gardener 修好 bug 后生成 v1.2_fixed，
        LLM 预期行为：Reviewer 确认所有历史 locked 测例全部通过，技术债务清零。
        """
        skill_fixed = create_mock_skill(mock_db, "calculate_gpa", "1.2", code=SKILL_CODE_4_3_V1_2_FIXED)

        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_gpa_locked_inherited",
                skill_version_id=skill_fixed.id,
                executable_code=SKILL_TEST_CODE_4_3_LOCKED,
                is_legacy_locked=True
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_fixed.id, TRACE_DIM_4_3)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_fixed.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "Bug 修复后 v1.2 应通过所有 locked 测例"
