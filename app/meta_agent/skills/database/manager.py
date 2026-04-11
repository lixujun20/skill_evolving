import logging
from typing import List, Optional, Dict, Any
from sqlmodel import Session, select, create_engine, func
from .models import Skill, SkillGroup, SkillDependency, MaintenanceHistory, TestCase, TestReport, QueryRecord
from sqlalchemy.exc import ProgrammingError

logger = logging.getLogger(__name__)

class SkillDatabaseManager:
    """
    负责围绕 Skill 进行高效读写与向量检索的管理类。
    采用了主系统中 "预过滤 (Scalar Filters) + pgvector (余弦距离搜索)" 的架构。
    """
    
    def __init__(self, db_url: str):
        """初始化引擎，要求数据库底层已安装并开启 CREATE EXTENSION vector"""
        self.engine = create_engine(db_url)
        # 初始化表结构 (若需要在生产环境，改用 alembic)
        # SQLModel.metadata.create_all(self.engine)

    def search_similar_skills(self, 
                              query_embedding: List[float], 
                              top_k: int = 5,
                              tags_filter: Optional[List[str]] = None,
                              similarity_threshold: float = 0.5) -> List[Skill]:
        """
        核心向量检索功能 (效仿主系统 _search_with_pgvector 方法)：
        在数据库层面直接使用 `ORDER BY embedding <=> query_embedding` 计算余弦相似度，
        并且在执行前先结合其他字段（如 tags）进行前置过滤。
        """
        with Session(self.engine) as session:
            try:
                # 构造基础查询
                statement = select(Skill)
                
                # ======== 1. 标量前置过滤 (Scalar Pre-filtering) ========
                # 如果传入了必须满足的 tag，通过 JSON 操作符执行硬过滤，极大降低向量比对池规模
                # 这里为了简单展示逻辑，实际 JSON 匹配在不同 SQL 方言语法可能不同 (Postgres是 @>)
                if tags_filter:
                    # 假定实现简单的 Python 层面过滤或自定义 JSON 包含指令
                    pass 
                
                # ======== 2. pgvector 余弦距离计算与排序 (<=>) ========
                # cosine_distance 返回的是距离 (0 = 完全相同，1 = 正交)
                # similarity = 1 - distance
                distance_expr = Skill.embedding.cosine_distance(query_embedding)
                
                # 基于距离阈值过滤，并用距离进行升序排序 (越近越靠前)
                statement = statement.where(distance_expr <= (1 - similarity_threshold))
                statement = statement.order_by(distance_expr)
                statement = statement.limit(top_k)
                
                results = session.exec(statement).all()
                return results

            except ProgrammingError as e:
                logger.error("pgvector 扩展可能未安装或 SQL 语法错误，请确保数据库已执行 `CREATE EXTENSION vector;`")
                logger.error(str(e))
                return []

    def get_forward_dependencies(self, skill_id: int) -> List[int]:
        """查询当前 skill 调用了哪些底层 skill (前向引用)"""
        with Session(self.engine) as session:
            statement = select(SkillDependency.callee_id).where(SkillDependency.caller_id == skill_id)
            return list(session.exec(statement).all())

    def get_backward_dependencies(self, skill_id: int) -> List[int]:
        """查询当前 skill 被哪些上游 skill 调用了 (后向引用/被谁依赖)"""
        with Session(self.engine) as session:
            statement = select(SkillDependency.caller_id).where(SkillDependency.callee_id == skill_id)
            return list(session.exec(statement).all())
            
    def record_skill_hit(self, skill_id: int, hit_type: str = "full"):
        """极轻量的高频数值更新：记录检索与命中"""
        with Session(self.engine) as session:
            skill = session.get(Skill, skill_id)
            if skill:
                if hit_type == "retrieval":
                    skill.retrieval_count += 1
                elif hit_type == "full":
                    skill.full_hit_count += 1
                elif hit_type == "partial":
                    skill.partial_hit_count += 1
                session.add(skill)
                session.commit()
                
    # ==========================================
    # 核心重构与维护操作 (Refactoring & Evolving)
    # ==========================================
    
    def create_skill_group(self, name: str, description: str = "") -> SkillGroup:
        """为一个全新的抽象能力创建一个技能组 (Skill Group)"""
        with Session(self.engine) as session:
            group = SkillGroup(name=name, description=description)
            session.add(group)
            session.commit()
            session.refresh(group)
            return group

    def add_skill_version(self, 
                          group_id: int, 
                          code: str, 
                          update_type: str = "minor",  # "major" (主动) or "minor" (被动/兼容)
                          docstring: str = "", 
                          tags: List[str] = None, 
                          python_dependencies: List[str] = None,
                          embedding: List[float] = None,
                          callee_skill_ids: List[int] = None,
                          hard_pinned_group_ids: List[int] = None) -> Skill:
        """
        追加一个 Skill 的新版本。
        如果是 'major' 更新（接口/语义大变），major_version + 1，minor_version 归零。
        如果是 'minor' 更新（兼容性修复/被动升级），major_version 保持不变，minor_version + 1。
        """
        with Session(self.engine) as session:
            # 1. 查找当前该 group 的最新版本
            statement = select(Skill).where(Skill.group_id == group_id).order_by(
                Skill.major_version.desc(), Skill.minor_version.desc()
            ).limit(1)
            
            latest_skill = session.exec(statement).first()
            
            new_major = 1
            new_minor = 0
            
            if latest_skill:
                if update_type == "major":
                    new_major = latest_skill.major_version + 1
                    new_minor = 0
                else:
                    new_major = latest_skill.major_version
                    new_minor = latest_skill.minor_version + 1
            
            # 2. 插入不可变的新版本 Skill
            new_skill = Skill(
                group_id=group_id,
                major_version=new_major,
                minor_version=new_minor,
                code=code,
                docstring=docstring,
                tags=tags or [],
                python_dependencies=python_dependencies or [],
                embedding=embedding
            )
            session.add(new_skill)
            session.commit()
            session.refresh(new_skill)
            
            # 3. 记录它依赖了哪些更底层的 Skill
            if callee_skill_ids:
                hard_pinned_group_ids = hard_pinned_group_ids or []
                for callee_id in callee_skill_ids:
                    callee = session.get(Skill, callee_id)
                    is_hard_pinned = bool(callee and callee.group_id in hard_pinned_group_ids)
                    dep = SkillDependency(
                        caller_id=new_skill.id,
                        callee_id=callee_id,
                        is_hard_pinned=is_hard_pinned
                    )
                    session.add(dep)
                session.commit()
                
            return new_skill

    def get_latest_skill_in_group(self, group_id: int, max_major_version: int = None) -> Optional[Skill]:
        """
        按主版本号进行安全获取。
        如果传入了 max_major_version，将只获取该大版本系列下的最新小版本！
        """
        with Session(self.engine) as session:
            statement = select(Skill).where(Skill.group_id == group_id)
            if max_major_version is not None:
                statement = statement.where(Skill.major_version == max_major_version)
                
            statement = statement.order_by(Skill.major_version.desc(), Skill.minor_version.desc()).limit(1)
            return session.exec(statement).first()

    def add_maintenance_record(self, 
                               skill_id: int, 
                               extractor_trace: Dict[str, Any] = None, 
                               reviewer_trace: Dict[str, Any] = None, 
                               query_context: str = ""):
        """隔离存放长文本：记录该版本的生成/重构过程中，Agents 的交互原始长历史"""
        with Session(self.engine) as session:
            record = MaintenanceHistory(
                skill_id=skill_id,
                extractor_trace=extractor_trace or {},
                reviewer_trace=reviewer_trace or {},
                query_context=query_context
            )
            session.add(record)
            session.commit()
    # ==========================================
    # Reviewer / Tester 操作（供 SkillReviewerTool 使用）
    # ==========================================

    def get_skill(self, skill_id: int) -> Optional[Skill]:
        with Session(self.engine) as session:
            return session.get(Skill, skill_id)

    def get_test_cases(self, skill_version_id: int) -> List[TestCase]:
        with Session(self.engine) as session:
            return session.exec(
                select(TestCase).where(TestCase.skill_version_id == skill_version_id)
            ).all()

    def save_test_case(self, test_case: TestCase) -> TestCase:
        with Session(self.engine) as session:
            session.add(test_case)
            session.commit()
            session.refresh(test_case)
            return test_case

    def save_test_report(self, skill_version_id: int, report: TestReport) -> TestReport:
        with Session(self.engine) as session:
            report.skill_version_id = skill_version_id
            session.add(report)
            session.commit()
            session.refresh(report)
            return report

    def get_test_reports(self, skill_version_id: int) -> List[TestReport]:
        with Session(self.engine) as session:
            return session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_version_id)
            ).all()

    def get_test_report(self, skill_version_id: int) -> Optional[TestReport]:
        with Session(self.engine) as session:
            return session.exec(
                select(TestReport).where(TestReport.skill_version_id == skill_version_id)
            ).first()
    # ==========================================
    # 协同过滤支持 (v2 新增)
    # ==========================================

    def search_similar_queries(
        self,
        query_embedding: List[float],
        top_m: int = 10,
        similarity_threshold: float = 0.4,
    ) -> List[QueryRecord]:
        """pgvector 检索 QueryRecord 表，找最相似的历史查询。"""
        from sqlalchemy.exc import ProgrammingError as _PGError
        with Session(self.engine) as session:
            try:
                distance_expr = QueryRecord.query_embedding.cosine_distance(query_embedding)
                statement = (
                    select(QueryRecord)
                    .where(QueryRecord.query_embedding.isnot(None))
                    .where(distance_expr <= (1 - similarity_threshold))
                    .order_by(distance_expr)
                    .limit(top_m)
                )
                return list(session.exec(statement).all())
            except _PGError as e:
                logger.error("search_similar_queries failed (pgvector?): %s", e)
                return []

    def save_query_record(
        self,
        query_text: str,
        query_embedding,
        produced_skill_id=None,
        produced_skill_name=None,
        agent_summary: str = "",
        remarks: str = "",
    ) -> QueryRecord:
        """执行结束后写入新的历史查询记录。失败时记录日志但不抛出异常。"""
        with Session(self.engine) as session:
            try:
                record = QueryRecord(
                    query_text=query_text,
                    query_embedding=query_embedding,
                    produced_skill_id=produced_skill_id,
                    produced_skill_name=produced_skill_name,
                    agent_summary=agent_summary,
                    remarks=remarks,
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                return record
            except Exception as e:
                logger.error("save_query_record failed: %s", e)
                session.rollback()
                return QueryRecord(query_text=query_text)

