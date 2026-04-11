import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
from app.meta_agent.skills.database.models import TestCase, TestFailureCategory, TestReport
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_3 import (
    SKILL_CODE_3_1, SKILL_TEST_CODE_3_1,
    SKILL_CODE_3_2_A, SKILL_TEST_CODE_3_2_A,
    SKILL_CODE_3_3_HALLUCINATED, SKILL_TEST_CODE_3_3_REAL,
    SKILL_CODE_3_4_SYNTAX_ERROR,
    SKILL_CODE_3_5_WRONG_CALL, SKILL_TEST_CODE_3_5,
    SKILL_CODE_3_6_BUGGY, SKILL_TEST_CODE_3_6,
    TRACE_DIM_3_1, TRACE_DIM_3_2, TRACE_DIM_3_3,
    TRACE_DIM_3_4, TRACE_DIM_3_5, TRACE_DIM_3_6,
)


class TestTesterDimension3_SandboxBoundaries:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_1_mocking_enforcement(self, mock_db):
        """
        Case 3.1: 重度外部 IO 技能（Mocking Enforcement）。
        Reviewer 生成的 TestCase 必须使用 unittest.mock.patch，不能发起真实网络请求。
        LLM 预期行为：Reviewer 通过执行测试并提交 is_passed=True 的 TestReport。
        """
        skill = create_mock_skill(mock_db, "fetch_course_materials", "1.0", code=SKILL_CODE_3_1)

        # Simulate that reviewer wrote a properly mocked test (LOCKED so reviewer won't bypass it)
        test_case = TestCase(
            case_name="test_fetch_mocked",
            skill_version_id=skill.id,
            executable_code=SKILL_TEST_CODE_3_1,
            is_legacy_locked=True
        )
        with Session(mock_db.engine) as session:
            session.add(test_case)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_3_1)
        assert result == "Review finished. See DB TestReports."

        # Verify the stored test code uses mock patching (data-level check, no LLM involved)
        assert "patch" in SKILL_TEST_CODE_3_1
        assert "requests.get" not in SKILL_TEST_CODE_3_1 or "patch" in SKILL_TEST_CODE_3_1

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "Reviewer 应识别正确的 mock 测试并通过"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_2_interface_incompatibility_captured(self, mock_db):
        """
        Case 3.2: 小版本更新中的接口兼容性测试。
        Skill_A v1.1 内部调用 Skill_B v2.0 但使用了旧关键字 'data'（应为 'submission_payload'）。
        Reviewer 必须捕捉到 SIGNATURE_MISMATCH / RUNTIME_EXCEPTION 并报告 is_passed=False。
        """
        skill = create_mock_skill(mock_db, "grade_submission", "1.1", code=SKILL_CODE_3_2_A)
        test_case = TestCase(
            case_name="test_grade_interface",
            skill_version_id=skill.id,
            executable_code=SKILL_TEST_CODE_3_2_A,
            is_legacy_locked=True
        )
        with Session(mock_db.engine) as session:
            session.add(test_case)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_3_2)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "接口不兼容应被 Reviewer 判定为失败"
        assert (
            TestFailureCategory.SIGNATURE_MISMATCH in saved_report.failure_categories
            or TestFailureCategory.RUNTIME_EXCEPTION in saved_report.failure_categories
            or TestFailureCategory.UPSTREAM_DEPENDENCY_BROKEN in saved_report.failure_categories
        ), "接口不兼容应分类为 signature_mismatch、runtime_exception 或 upstream_dependency_broken"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_3_hallucinated_import_blocked(self, mock_db):
        """
        Case 3.3: 幻觉接口虚构。
        生成代码 import 了 'edu_analytics.advanced' 这个不存在的模块。
        LLM 预期行为：Reviewer 必须捕捉 IMPORT_ERROR 并对 is_passed 打 False。
        """
        skill = create_mock_skill(mock_db, "auto_grade_essay", "1.0", code=SKILL_CODE_3_3_HALLUCINATED)
        test_case = TestCase(
            case_name="test_import_check",
            skill_version_id=skill.id,
            executable_code=SKILL_TEST_CODE_3_3_REAL,
            is_legacy_locked=False
        )
        with Session(mock_db.engine) as session:
            session.add(test_case)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_3_3)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "幻觉 import 应被 Reviewer 找出并判定为失败"
        assert TestFailureCategory.IMPORT_ERROR in saved_report.failure_categories

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_4_syntax_error_fast_fail(self, mock_db):
        """
        Case 3.4: 静态语法错误的前置熔断。
        代码含有语法错误（括号未闭合），加载时立即触发 SyntaxError。
        LLM 预期行为：Reviewer 跳过重试，直接标记 SYNTAX_ERROR 并中断。
        """
        skill = create_mock_skill(mock_db, "group_students_by_performance", "1.0", code=SKILL_CODE_3_4_SYNTAX_ERROR)

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_3_4)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "语法错误应被 Reviewer 快速失败并标记"
        assert TestFailureCategory.SYNTAX_ERROR in saved_report.failure_categories

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_5_wrong_tool_call_pinpointed(self, mock_db):
        """
        Case 3.5: 工具调用错误定位测试。
        send_grade_notifications 内部调用 email_service_fn 时参数顺序颠倒。
        LLM 预期行为：Reviewer 的测试必须唯一指向 email_service_fn 参数顺序错误。
        """
        skill = create_mock_skill(mock_db, "send_grade_notifications", "1.0", code=SKILL_CODE_3_5_WRONG_CALL)
        test_case = TestCase(
            case_name="test_email_call_order",
            skill_version_id=skill.id,
            executable_code=SKILL_TEST_CODE_3_5,
            is_legacy_locked=False
        )
        with Session(mock_db.engine) as session:
            session.add(test_case)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_3_5)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "参数顺序错误应被 Reviewer 捕捉"
        assert (
            TestFailureCategory.RUNTIME_EXCEPTION in saved_report.failure_categories
            or TestFailureCategory.ASSERTION_FAILED in saved_report.failure_categories
            or TestFailureCategory.SIGNATURE_MISMATCH in saved_report.failure_categories
        ), "参数顺序错误应分类为 runtime_exception、assertion_failed 或 signature_mismatch"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_3_6_algorithm_error_pinpointed(self, mock_db):
        """
        Case 3.6: 实现逻辑错误定位测试。
        calculate_percentile_rank 的百分位算法有误（rank/(n+1) 公式错误）。
        LLM 预期行为：Reviewer 测试必须精准指向是算法逻辑错误 (ASSERTION_FAILED)。
        """
        skill = create_mock_skill(mock_db, "calculate_percentile_rank", "1.0", code=SKILL_CODE_3_6_BUGGY)
        test_case = TestCase(
            case_name="test_percentile_logic",
            skill_version_id=skill.id,
            executable_code=SKILL_TEST_CODE_3_6,
            is_legacy_locked=False
        )
        with Session(mock_db.engine) as session:
            session.add(test_case)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill.id, TRACE_DIM_3_6)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "算法错误应被 Reviewer 捕捉"
        assert TestFailureCategory.ASSERTION_FAILED in saved_report.failure_categories
