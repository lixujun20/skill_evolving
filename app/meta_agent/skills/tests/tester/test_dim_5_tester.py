import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
from app.meta_agent.skills.database.models import TestCase, TestFailureCategory, TestReport
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_5 import (
    SKILL_CODE_5_1_V1_0, SKILL_TEST_CODE_5_1_V1_0,
    SKILL_CODE_5_1_V1_1_BROKEN, SKILL_CODE_5_1_V1_1_FIXED,
    SKILL_CODE_5_2_V2_0_BROKEN, SKILL_CODE_5_2_V2_0_FIXED,
    SKILL_TEST_CODE_5_2_V2_0,
    TRACE_DIM_5_1, TRACE_DIM_5_2,
)


class TestTesterDimension5_TestDrivenRefinement:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_1_tester_rejects_broken_minor(self, mock_db):
        """
        Case 5.1 Phase 1: Reviewer 对『破坏性 Minor』发起测试。
        v1.1_broken 将返回类型变为 List[str]，违反了旧的 isinstance(result, str) 断言。
        Reviewer 必须产出 is_passed=False，failure_categories 包含 MINOR_COMPATIBILITY_BROKEN。
        """
        # Use the BROKEN v1.1 skill (returns List[str]) — the locked v1.0 tests expect str
        skill_v1 = create_mock_skill(mock_db, "recommend_next_course", "1.1", code=SKILL_CODE_5_1_V1_1_BROKEN)
        # Locked test that asserts string return — will FAIL against v1.1_BROKEN
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_returns_string_locked",
                skill_version_id=skill_v1.id,
                executable_code=SKILL_TEST_CODE_5_1_V1_0,
                is_legacy_locked=True
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_v1.id, TRACE_DIM_5_1)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_v1.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "破坏性 Minor 应被 Reviewer 判定为失败"
        assert (
            TestFailureCategory.MINOR_COMPATIBILITY_BROKEN in saved_report.failure_categories
            or TestFailureCategory.SIGNATURE_MISMATCH in saved_report.failure_categories
            or TestFailureCategory.ASSERTION_FAILED in saved_report.failure_categories
        ), "破坏性 Minor 应分类为 minor_compatibility_broken、signature_mismatch 或 assertion_failed"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_1_tester_approves_fixed_minor(self, mock_db):
        """
        Case 5.1 Phase 2: Extractor 修正后的 v1.1_fixed 保留了 str 默认返回类型。
        LLM 预期行为：Reviewer 重新测试，旧的 isinstance(result, str) 断言应全部通过。
        """
        skill_v1_1_fixed = create_mock_skill(
            mock_db, "recommend_next_course", "1.1", code=SKILL_CODE_5_1_V1_1_FIXED
        )
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_v1_0_string_lock",
                skill_version_id=skill_v1_1_fixed.id,
                executable_code=SKILL_TEST_CODE_5_1_V1_0,
                is_legacy_locked=True
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_v1_1_fixed.id, TRACE_DIM_5_1)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_v1_1_fixed.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "修复后的 v1.1 应通过所有 locked 断言"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_2_tester_rejects_incomplete_major(self, mock_db):
        """
        Case 5.2 Phase 1: Reviewer 对 v2.0_broken 发起测试。
        deadline_constraints 完全被忽略，urgent 列表永远为空。
        LLM 预期行为：Reviewer 测试必须捕捉到 ASSERTION_FAILED，is_passed=False。
        """
        skill_v2_broken = create_mock_skill(
            mock_db, "generate_study_plan", "2.0", code=SKILL_CODE_5_2_V2_0_BROKEN
        )
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_v2_deadline_handling",
                skill_version_id=skill_v2_broken.id,
                executable_code=SKILL_TEST_CODE_5_2_V2_0,
                is_legacy_locked=False
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_v2_broken.id, TRACE_DIM_5_2)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_v2_broken.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "deadline_constraints 被忽略应被 Reviewer 捕捉"
        assert TestFailureCategory.ASSERTION_FAILED in saved_report.failure_categories

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_5_2_tester_approves_complete_major(self, mock_db):
        """
        Case 5.2 Phase 2: Extractor 修正后的 v2.0_fixed 正确实现了 deadline 优先级排序。
        LLM 预期行为：Reviewer 重新运行所有测试，全部通过。
        """
        skill_v2_fixed = create_mock_skill(
            mock_db, "generate_study_plan", "2.0", code=SKILL_CODE_5_2_V2_0_FIXED
        )
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_v2_deadline_complete",
                skill_version_id=skill_v2_fixed.id,
                executable_code=SKILL_TEST_CODE_5_2_V2_0,
                is_legacy_locked=False
            ))
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_v2_fixed.id, TRACE_DIM_5_2)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_v2_fixed.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "完整实现的 v2.0 应通过所有测试"
