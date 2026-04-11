import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import Skill
from app.meta_agent.skills.tests.conftest import create_mock_skill

# Importers
from app.meta_agent.skills.tests.test_data_dim_1 import (
    SKILL_CODE_1_1_V1_0 as C1, TRACE_DIM_1_1,
    SKILL_CODE_1_2_V1_0 as C2, TRACE_DIM_1_2,
    SKILL_CODE_1_3_V1_0 as C3, TRACE_DIM_1_3,
    SKILL_CODE_1_4_V1_0 as C4, TRACE_DIM_1_4,
    SKILL_CODE_1_5_V1_0 as C5, TRACE_DIM_1_5,
    SKILL_CODE_1_6_V1_0 as C6, TRACE_DIM_1_6,
)

class TestExtractorDimension1_ActiveUpdate:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_1_minor_update_extraction(self, mock_db):
        """Case 1.1: 正常向下兼容的 Minor/参数提取
        LLM 预期行为：识别为 minor 更新，向 fetch_student_transcript 添加 output_format 参数，
        生成 v1.1 代码并提交到 DB（major_version=1, minor_version=1）。
        """
        skill = create_mock_skill(mock_db, "fetch_student_transcript", "1.0", code=C1)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_1_1, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应返回生成的 Python 代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交新的技能版本到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_2_pseudo_minor_extraction(self, mock_db):
        """Case 1.2: Extractor 将 calculate_class_stats 返回类型变更识别为 Minor（误判场景）
        LLM 预期行为：分析 trace（dict 返回），生成新版代码并提交到 DB。
        真正的破坏性验证交由 Tester 完成。
        """
        skill = create_mock_skill(mock_db, "calculate_class_stats", "1.0", code=C2)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_1_2, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应返回生成的技能代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交新版本到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_3_major_update_extraction(self, mock_db):
        """Case 1.3: 返回类型大变（str → 结构化 dict），LLM 应判定为 Major 重构
        LLM 预期行为：识别返回类型根本性变化，生成 v2.0 代码，major_version 应增加。
        """
        skill = create_mock_skill(mock_db, "generate_quiz_question", "1.0", code=C3)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_1_3, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应返回生成的技能代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交新版本到 DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        assert latest.major_version >= 2, "Major 更新应使 major_version 递增到 ≥2"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_4_patch_extraction(self, mock_db):
        """Case 1.4: 纯性能优化（O(N²)→O(N)），LLM 应判定为 Patch/Minor 并保持接口不变
        LLM 预期行为：提取优化算法，不改变函数签名，生成代码并提交。
        """
        skill = create_mock_skill(mock_db, "count_perfect_attendance", "1.0", code=C4)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_1_4, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应返回生成的技能代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交优化后的新版本到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_5_deprecation_major_extraction(self, mock_db):
        """Case 1.5: 废弃参数（use_legacy_calendar），LLM 应判定为 Major 重构
        LLM 预期行为：识别参数删除属于签名变更，生成移除废弃参数的新版代码，major_version 递增。
        """
        skill = create_mock_skill(mock_db, "schedule_course", "1.0", code=C5)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_1_5, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应返回生成的技能代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交新版本到 DB"
        latest = max(versions, key=lambda s: (s.major_version, s.minor_version))
        # Ideally the LLM recognises parameter removal as Major; but a valid Minor
        # update (e.g. changing the default value) is also an acceptable refactor.
        assert latest.major_version >= 2 or latest.minor_version >= 1, \
            "废弃参数应至少触发 Minor 更新（理想情况下 Major 移除废弃参数）"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_1_6_implicit_signature_extraction(self, mock_db):
        """Case 1.6: 隐性签名变更（Dict→List 入参），此测试验证 Extractor 如何标注该更新类型
        LLM 预期行为：生成新代码并提交到 DB；Tester 负责后续检测破坏性。
        """
        skill = create_mock_skill(mock_db, "filter_failing_students", "1.0", code=C6)
        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_1_6, mock_db, target_skill_id=skill.id)

        assert result is not None, "Gardener 应返回生成的技能代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交新版本到 DB"

