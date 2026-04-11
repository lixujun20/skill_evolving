import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import TestCase, Skill
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_4 import (
    SKILL_CODE_4_1_V1_0, SKILL_CODE_4_1_V1_1, SKILL_CODE_4_1_V1_2,
    SKILL_CODE_4_1_V1_3, SKILL_CODE_4_1_V1_4,
    SKILL_TEST_CODE_4_1_V1_0, SKILL_TEST_CODE_4_1_V1_1,
    SKILL_TEST_CODE_4_1_V1_2, SKILL_TEST_CODE_4_1_V1_3,
    SKILL_TEST_CODE_4_1_V1_4,
    SKILL_CODE_4_2_LEGACY, SKILL_CODE_4_2_HEALED,
    TRACE_DIM_4_1_STEP1, TRACE_DIM_4_1_STEP2, TRACE_DIM_4_1_STEP3,
    TRACE_DIM_4_1_STEP4, TRACE_DIM_4_1_STEP5,
    TRACE_DIM_4_2,
)


class TestExtractorDimension4_HistoryTimeline:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_4_1_minor_incremental_v1_1(self, mock_db):
        """
        Case 4.1 Step 1→2: Extractor 对 track_learning_progress 的第一次 Minor 更新。
        LLM 预期行为：识别添加 include_unit_breakdown 参数，生成 v1.1 代码并提交到 DB。
        """
        skill_v1 = create_mock_skill(mock_db, "track_learning_progress", "1.0", code=SKILL_CODE_4_1_V1_0)
        with Session(mock_db.engine) as session:
            session.add(TestCase(
                case_name="test_v1_0_basic",
                skill_version_id=skill_v1.id,
                executable_code=SKILL_TEST_CODE_4_1_V1_0,
                is_legacy_locked=True
            ))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_4_1_STEP2, mock_db, target_skill_id=skill_v1.id)

        assert result is not None, "Gardener 应返回生成的技能代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill_v1.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交 v1.1 到 DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.minor_version >= 1, "Minor 更新应使 minor_version 递增"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_4_1_minor_incremental_v1_2(self, mock_db):
        """
        Case 4.1 Step 2→3: 第二次 Minor 更新，添加 as_of_date 参数。
        LLM 预期行为：在保留已有 locked 测例的前提下，生成 v1.2 代码。
        """
        skill_v1 = create_mock_skill(mock_db, "track_learning_progress", "1.1", code=SKILL_CODE_4_1_V1_1)
        with Session(mock_db.engine) as session:
            for i, code in enumerate([SKILL_TEST_CODE_4_1_V1_0, SKILL_TEST_CODE_4_1_V1_1]):
                session.add(TestCase(
                    case_name=f"test_accumulated_{i}",
                    skill_version_id=skill_v1.id,
                    executable_code=code,
                    is_legacy_locked=True
                ))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_4_1_STEP3, mock_db, target_skill_id=skill_v1.id)

        assert result is not None, "Gardener 应返回生成的技能代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill_v1.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交 v1.2 到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_4_1_minor_cumulative_tests_at_v1_5(self, mock_db):
        """
        Case 4.1 完整验证：v1.5 时 DB 中应积累全部前5个版本的 locked 测例。
        LLM 预期行为：生成 v1.5 代码，DB 中已有的 locked 测例不被删除。
        """
        skill = create_mock_skill(mock_db, "track_learning_progress", "1.4", code=SKILL_CODE_4_1_V1_4)

        all_test_codes = [
            SKILL_TEST_CODE_4_1_V1_0,
            SKILL_TEST_CODE_4_1_V1_1,
            SKILL_TEST_CODE_4_1_V1_2,
            SKILL_TEST_CODE_4_1_V1_3,
            SKILL_TEST_CODE_4_1_V1_4,
        ]
        with Session(mock_db.engine) as session:
            for i, code in enumerate(all_test_codes):
                session.add(TestCase(
                    case_name=f"test_v1_{i}_a",
                    skill_version_id=skill.id,
                    executable_code=code,
                    is_legacy_locked=True
                ))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_4_1_STEP5, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应返回生成的技能代码"

        # 纯 DB 验证：5 条 locked 测例必须仍在 DB 中（考察 LLM 是否未误删）
        with Session(mock_db.engine) as session:
            locked = session.exec(
                select(TestCase).where(TestCase.skill_version_id == skill.id, TestCase.is_legacy_locked == True)
            ).all()
        assert len(locked) == 5, "所有 5 条 locked 测例应保持在 DB 中不被删除"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_4_2_legacy_schema_healing_no_crash(self, mock_db):
        """
        Case 4.2: 跨版本数据结构缺失的自愈。
        LLM 预期行为：遇到 interface_schema 缺失的旧 Skill 时不崩溃，
        通过 AST/Docstring 分析推断 schema，生成现代化的 v2.0 代码并提交。
        """
        legacy_skill = create_mock_skill(
            mock_db, "assign_homework", "0.1",
            code=SKILL_CODE_4_2_LEGACY,
            group_name="LegacyGroup"
        )

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_4_2, mock_db, target_skill_id=legacy_skill.id)

        assert result is not None, "Gardener 处理遗留 Skill 时不应崩溃，应返回生成的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == legacy_skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交自愈后的新版本到 DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        # Starting from v0.1: a minor update → v0.2, a major update → v1.0.
        # The test checks that the version INCREASED, not that it reached v2.0 specifically.
        assert (latest.major_version, latest.minor_version) > (legacy_skill.major_version, legacy_skill.minor_version), \
            "自愈应触发版本更新（Major 或 Minor 均可）"


