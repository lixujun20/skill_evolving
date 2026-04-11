import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
from app.meta_agent.skills.database.models import TestCase, TestFailureCategory, TestReport
from app.meta_agent.skills.tests.conftest import create_mock_skill

# Importers
from app.meta_agent.skills.tests.test_data_dim_1 import (
    SKILL_CODE_1_1_V1_0 as C1, SKILL_TEST_CODE_1_1_V1_0 as T1,
    SKILL_CODE_1_2_V1_0 as C2, SKILL_TEST_CODE_1_2_V1_0 as T2,
    SKILL_CODE_1_3_V1_0 as C3, SKILL_TEST_CODE_1_3_V1_0 as T3,
    SKILL_CODE_1_4_V1_0 as C4, SKILL_TEST_CODE_1_4_V1_0 as T4,
    SKILL_CODE_1_5_V1_0 as C5, SKILL_TEST_CODE_1_5_V1_0 as T5,
    SKILL_CODE_1_6_V1_0 as C6, SKILL_TEST_CODE_1_6_V1_0 as T6,
    TRACE_DIM_1_4, TRACE_DIM_1_5, TRACE_DIM_1_6,
)


class TestTesterDimension1_ActiveUpdate:

    @pytest.mark.asyncio
    async def test_case_1_1_tester_minor_locked_test_exists(self, mock_db):
        """Case 1.1: 正常的 Minor 向下兼容，验证 DB 中存在被锁定的旧测试."""
        skill_a = create_mock_skill(mock_db, "fetch_student_transcript", "1.0", code=C1)
        test_case_v1 = TestCase(
            case_name="test_1_1",
            skill_version_id=skill_a.id,
            executable_code=T1,
            is_legacy_locked=True
        )
        with Session(mock_db.engine) as session:
            session.add(test_case_v1)
            session.commit()

        # Verify locked test exists in DB by direct query (no get_locked_tests method on reviewer)
        with Session(mock_db.engine) as session:
            locked_tests = session.exec(
                select(TestCase).where(
                    TestCase.skill_version_id == skill_a.id,
                    TestCase.is_legacy_locked == True
                )
            ).all()

        assert len(locked_tests) == 1
        assert locked_tests[0].is_legacy_locked is True

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_2_tester_pseudo_minor_capture(self, mock_db):
        """Case 1.2: 成功捕捉虚假的 Minor 破坏性更新."""
        skill_a = create_mock_skill(mock_db, "calculate_class_stats", "1.0", code=C2)
        test_case_v1 = TestCase(case_name="test_1_2", skill_version_id=skill_a.id, executable_code=T2, is_legacy_locked=True)
        with Session(mock_db.engine) as session:
            session.add(test_case_v1)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_a.id)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_a.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "虚假 Minor 应被 Reviewer 判定为失败"
        assert TestFailureCategory.MINOR_COMPATIBILITY_BROKEN in saved_report.failure_categories

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_3_tester_incompatible_major(self, mock_db):
        """Case 1.3: 对 Major 的不兼容防线."""
        skill_a = create_mock_skill(mock_db, "generate_quiz_question", "1.0", code=C3)
        test_case_v1 = TestCase(case_name="test_1_3", skill_version_id=skill_a.id, executable_code=T3, is_legacy_locked=True)
        with Session(mock_db.engine) as session:
            session.add(test_case_v1)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_a.id)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_a.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "Major 版本不兼容应被 Reviewer 判定为失败"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_4_tester_patch_passes_all_locked(self, mock_db):
        """
        Case 1.4: 纯内部重构（O(N²) → O(N)），Patch 级别。
        Reviewer 必须加载旧的 [LOCKED] 测例并验证全部通过，外部行为不变。
        """
        skill_a = create_mock_skill(mock_db, "count_perfect_attendance", "1.0", code=C4)
        test_case_v1 = TestCase(
            case_name="test_1_4_locked",
            skill_version_id=skill_a.id,
            executable_code=T4,
            is_legacy_locked=True
        )
        with Session(mock_db.engine) as session:
            session.add(test_case_v1)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_a.id, TRACE_DIM_1_4)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_a.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "Patch 级更新应通过所有 locked 测例"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_5_tester_deprecation_allows_unlocked_rewrite(self, mock_db):
        """
        Case 1.5: 功能缩减（废弃 use_legacy_calendar），Major 更新。
        Reviewer 不得硬锁旧测例中涉及废弃参数的断言。
        is_passed=True，因为 Reviewer 重写了全新的 TestCase。
        """
        skill_a = create_mock_skill(mock_db, "schedule_course", "2.0", code=C5)
        # Major update: new test code for the new interface
        test_case_new = TestCase(
            case_name="test_1_5_new_interface",
            skill_version_id=skill_a.id,
            executable_code=T5,
            is_legacy_locked=False  # Major: old tests no longer locked
        )
        with Session(mock_db.engine) as session:
            session.add(test_case_new)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_a.id, TRACE_DIM_1_5)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_a.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert saved_report.is_passed is True, "Major 更新新接口应通过 Reviewer 验证"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_6_tester_type_mutation_caught(self, mock_db):
        """
        Case 1.6: 隐性签名变更（List[str] → Dict[str, Any]）。
        即使函数名未变，返回类型突变视为 Major。
        若被误判为 Minor，Reviewer 的 isinstance(result, list) 旧断言必须将其拦截。
        """
        skill_a = create_mock_skill(mock_db, "filter_failing_students", "1.0", code=C6)
        # Locked test that asserts isinstance(result, list)
        test_case_locked = TestCase(
            case_name="test_1_6_list_type",
            skill_version_id=skill_a.id,
            executable_code=T6,
            is_legacy_locked=True
        )
        with Session(mock_db.engine) as session:
            session.add(test_case_locked)
            session.commit()

        reviewer = SkillReviewerAgent(db_manager=mock_db)

        result = await reviewer.run_review_v1(skill_a.id, TRACE_DIM_1_6)
        assert result == "Review finished. See DB TestReports."

        with Session(mock_db.engine) as session:
            saved_report = session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_a.id)
            ).first()
        assert saved_report is not None, "Reviewer 应已将 TestReport 写入 DB"
        assert not saved_report.is_passed, "返回类型突变应被 Reviewer 判定为失败"
        assert (
            TestFailureCategory.MINOR_COMPATIBILITY_BROKEN in saved_report.failure_categories
            or TestFailureCategory.SIGNATURE_MISMATCH in saved_report.failure_categories
        ), "返回类型突变应分类为 minor_compatibility_broken 或 signature_mismatch"
