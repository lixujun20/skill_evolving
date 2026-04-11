import pytest
from sqlmodel import Session, select
from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.database.models import Skill, SkillDependency
from app.meta_agent.skills.tests.conftest import create_mock_skill

from app.meta_agent.skills.tests.test_data_dim_2 import (
    SKILL_ASSESS_V1_0, SKILL_ASSESS_V1_2, SKILL_ASSESS_V2_0, SKILL_ASSESS_V3_0,
    SKILL_RECOMMEND_V1_0, SKILL_ASSESS_GAP_V1_0, SKILL_CATALOG_V1_0,
    TRACE_DIM_2_1, TRACE_DIM_2_2, TRACE_DIM_2_3, TRACE_DIM_2_4,
    TRACE_DIM_2_5, TRACE_DIM_2_6, TRACE_DIM_2_7,
)


class TestExtractorDimension2_TopologyDependency:

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_2_1_upstream_minor_passive_plan(self, mock_db):
        """
        Case 2.1: 上游小版本静默更新。
        LLM 预期行为：检测到 assess_student_knowledge v1.2（接口未变），
        Passive Plan 决定不修改 Skill_Down 代码，仅刷新依赖指针。
        """
        base_skill = create_mock_skill(mock_db, "assess_student_knowledge", "1.0", code=SKILL_ASSESS_V1_0)
        down_skill = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_RECOMMEND_V1_0)

        with Session(mock_db.engine) as session:
            dep = SkillDependency(caller_id=down_skill.id, callee_id=base_skill.id, is_hard_pinned=False)
            session.add(dep)
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_2_1, mock_db, target_skill_id=down_skill.id)

        assert result is not None, "Gardener 应返回生成的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == down_skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交被动更新的新版本到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_2_2_upstream_major_forced_adaptation(self, mock_db):
        """
        Case 2.2: 上游大版本阻断性爆发。
        LLM 预期行为：检测到 assess_student_knowledge v2.0（返回 Dict），
        生成适配 result['score'] 的新版 recommend_next_course，并提交到 DB。
        """
        base_skill = create_mock_skill(mock_db, "assess_student_knowledge", "2.0", code=SKILL_ASSESS_V2_0)
        down_skill = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_RECOMMEND_V1_0)

        with Session(mock_db.engine) as session:
            dep = SkillDependency(caller_id=down_skill.id, callee_id=base_skill.id, is_hard_pinned=False)
            session.add(dep)
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_2_2, mock_db, target_skill_id=down_skill.id)

        assert result is not None, "Gardener 应返回适配后的新版代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == down_skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交适配 v2.0 的新版本到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_2_3_diamond_dependency_resolution(self, mock_db):
        """
        Case 2.3: 菱形依赖分支冲突。
        LLM 预期行为：感知 Mid_B 仍使用 Base v1.0 而 Mid_A 已用 v2.0 的不一致情况，
        生成策略性 Passive Plan 并提交（建议级联更新 Mid_B 或直接绕过）。
        """
        base_v2 = create_mock_skill(mock_db, "assess_student_knowledge", "2.0", code=SKILL_ASSESS_V2_0, group_name="BaseGroup")
        mid_a = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_RECOMMEND_V1_0, group_name="MidGroup")
        mid_b = create_mock_skill(mock_db, "assess_gap_area", "1.0", code=SKILL_ASSESS_GAP_V1_0, group_name="MidGroup")
        top_skill = create_mock_skill(mock_db, "generate_weekly_plan", "1.0", code="def generate_weekly_plan(): pass", group_name="TopGroup")

        with Session(mock_db.engine) as session:
            session.add(SkillDependency(caller_id=top_skill.id, callee_id=mid_a.id))
            session.add(SkillDependency(caller_id=top_skill.id, callee_id=mid_b.id))
            session.add(SkillDependency(caller_id=mid_a.id, callee_id=base_v2.id))
            session.add(SkillDependency(caller_id=mid_b.id, callee_id=base_v2.id))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_2_3, mock_db, target_skill_id=top_skill.id)

        assert result is not None, "Gardener 应返回处理菱形依赖的计划/代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == top_skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交新版本到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_2_4_hard_pinned_upstream_ignored(self, mock_db):
        """
        Case 2.4: 硬锁定防御。
        DB 中 recommend_next_course 对 assess_student_knowledge v1.0 设定 is_hard_pinned=True。
        LLM 预期行为：检测到 hard_pinned 标志，在 Passive Plan 中明确跳过版本升级，
        生成不改变上游调用方式的代码。
        """
        base_v1 = create_mock_skill(mock_db, "assess_student_knowledge", "1.0", code=SKILL_ASSESS_V1_0)
        down_skill = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_RECOMMEND_V1_0)

        with Session(mock_db.engine) as session:
            dep = SkillDependency(caller_id=down_skill.id, callee_id=base_v1.id, is_hard_pinned=True)
            session.add(dep)
            session.commit()

        # 纯 DB 验证：确认硬锁定确实被写入 DB（此断言与 LLM 无关）
        with Session(mock_db.engine) as session:
            stored = session.exec(
                select(SkillDependency).where(SkillDependency.caller_id == down_skill.id)
            ).first()
        assert stored.is_hard_pinned is True

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_2_4, mock_db, target_skill_id=down_skill.id)

        assert result is not None, "Gardener 应返回生成的代码（维持 hard-pin 的版本）"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == down_skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交新版本到 DB（即便 hard-pinned 也应更新逻辑）"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_2_5_mixed_upstream_composite_plan(self, mock_db):
        """
        Case 2.5: 混合级联更新（一个上游 Minor + 一个上游 Major）。
        LLM 预期行为：对 assess_student_knowledge v1.2（Minor）不作代码变更；
        对 load_course_catalog v2.0（Major）更新调用参数；生成新版并提交 DB。
        """
        up1 = create_mock_skill(mock_db, "assess_student_knowledge", "1.2", code=SKILL_ASSESS_V1_2, group_name="Up1Group")
        up2 = create_mock_skill(mock_db, "load_course_catalog", "2.0", code="def load_course_catalog(format='json'): pass", group_name="Up2Group")
        down_skill = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_RECOMMEND_V1_0)

        with Session(mock_db.engine) as session:
            session.add(SkillDependency(caller_id=down_skill.id, callee_id=up1.id, is_hard_pinned=False))
            session.add(SkillDependency(caller_id=down_skill.id, callee_id=up2.id, is_hard_pinned=False))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_2_5, mock_db, target_skill_id=down_skill.id)

        assert result is not None, "Gardener 应返回生成的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == down_skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交混合更新后的新版本到 DB"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_2_6_cyclic_dependency_db_prevention(self, mock_db):
        """
        Case 2.6: 环形依赖拦截。
        LLM 预期行为：识别建立 B→A 会形成环（A→B 已存在），拒绝写入反向依赖，
        建议提取共享基础技能来解耦。
        """
        # skill_a (assess) already calls skill_b (load_course_catalog): A→B exists.
        # Trace asks to improve load_course_catalog by calling assess (would be B→A, cyclic).
        skill_a = create_mock_skill(mock_db, "assess_student_knowledge", "1.0", code=SKILL_ASSESS_V1_0)
        skill_b = create_mock_skill(mock_db, "load_course_catalog", "1.0", code=SKILL_CATALOG_V1_0)

        with Session(mock_db.engine) as session:
            session.add(SkillDependency(caller_id=skill_a.id, callee_id=skill_b.id))
            session.commit()

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_2_6, mock_db, target_skill_id=skill_b.id)

        assert result is not None, "Gardener 应返回拒绝/解决环形依赖的方案"

        # 纯 DB 验证：Gardener 不应写入反向依赖（此断言考察 LLM 是否未盲目写 DB）
        with Session(mock_db.engine) as session:
            reverse_dep = session.exec(
                select(SkillDependency).where(
                    SkillDependency.caller_id == skill_b.id,
                    SkillDependency.callee_id == skill_a.id
                )
            ).first()
        assert reverse_dep is None, "Gardener 不应建立形成循环的反向依赖"

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_case_2_7_orphaned_dependency_forced_upgrade(self, mock_db):
        """
        Case 2.7: 上游废弃导致的强制阻断。
        Skill_Down 依赖 Skill_Up (v1.0)，但 DB 中 v1.0 已被删除，只剩 v3.0。
        被动重构从"机会主义"变为"强制响应"。
        Extractor 察觉到底层依赖缺失，必须在本次任务中强制适配 v3.0。
        """
        # Only v3.0 is in DB (v1.0 deprecated/deleted)
        base_v3 = create_mock_skill(mock_db, "assess_student_knowledge", "3.0", code=SKILL_ASSESS_V3_0)
        down_skill = create_mock_skill(mock_db, "recommend_next_course", "1.0", code=SKILL_RECOMMEND_V1_0)

        # Downstream still "references" a v1.0 that no longer exists
        # We model this by NOT creating the dependency in DB (orphaned reference)

        gardener = SkillGardenerAgent(db=mock_db)

        result = await gardener.run_extraction(TRACE_DIM_2_7, mock_db, target_skill_id=down_skill.id)

        assert result is not None, "Gardener 应返回强制迁移到 v3.0 的代码"
        with Session(mock_db.engine) as session:
            versions = session.exec(select(Skill).where(Skill.group_id == down_skill.group_id)).all()
        assert len(versions) > 1, "Gardener 应已提交适配 v3.0 async 接口的新版本到 DB"
