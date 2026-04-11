from typing import List, Optional, Dict, Any
import enum
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship, Column, JSON
from pgvector.sqlalchemy import Vector

class TestFailureCategory(str, enum.Enum):
    """测试阶段可能出现的失败类型枚举，涵盖语法、逻辑、兼容性、性能与质量等维度"""
    # 1. 语法与环境错误 (Syntax & Environment)
    SYNTAX_ERROR = "syntax_error"                   # 代码无法解析，存在基本语法错误
    IMPORT_ERROR = "import_error"                   # 依赖不存在，或者大模型虚构了库
    SANDBOX_ERROR = "sandbox_error"                 # 沙盒环境配置/启动/执行引擎失败、沙箱级超时

    # 2. 功能与逻辑错误 (Functional Correctness)
    ASSERTION_FAILED = "assertion_failed"           # 测试用例断言未通过，计算结果不符预期
    RUNTIME_EXCEPTION = "runtime_exception"         # 运行时抛出未捕获的异常(TypeError, ValueError等)

    # 3. 版本与兼容性错误 (Versioning & Compatibility)
    MINOR_COMPATIBILITY_BROKEN = "minor_compatibility_broken" # Minor更新但破坏了被锁定的旧版本测试
    SIGNATURE_MISMATCH = "signature_mismatch"       # 函数入参/出参结构发生未授权变更(伪装成小版本的大变化)
    UPSTREAM_DEPENDENCY_BROKEN = "upstream_dependency_broken" # 上游依赖调用失败(未做被动适配、硬编码老版参数等)

    # 4. 性能与资源错误 (Performance & Resource)
    TIMEOUT_INFINITE_LOOP = "timeout_infinite_loop" # 函数执行超时，疑似死循环或算法性能极差
    RESOURCE_EXHAUSTION = "resource_exhaustion"     # OOM内存溢出、磁盘写爆等沙箱资源滥用

    # 5. 代码质量与规范约束 (Quality & Compliance)
    HARDCODED_RESTRICTION = "hardcoded_restriction" # 内部存在硬编码的特定环境凭证或死逻辑，不具复用性
    EXTENSIBILITY_ISSUE = "extensibility_issue"     # 代码结构过于臃肿、缺乏清晰接口，难以维护和扩展

# 先声明类，解决相互引用的闭环警告（如需要）
# 在 SQLModel 中如果使用字符串引用如 "Skill" 也能避免部分问题，
# 但按照从概念到实体的自上而下顺序是最清晰的。

# ==========================================
# 1. 技能组 (SkillGroup) - 逻辑概念的聚合顶点
# ==========================================
class SkillGroup(SQLModel, table=True):
    """逻辑技能组，例如 'DataCleaner'，用来将 v1, v2 等不同演化版本聚合在一起"""
    __tablename__ = "skill_groups"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)  # 组名, 例如 'fetch_webpage'
    description: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # 一个 Group 下挂载多条实际代码 (各种 version)
    skills: List["Skill"] = Relationship(back_populates="group")

# ==========================================
# 2. 技能实体表 (Skill) - 核心不可变版本代码
# ==========================================
class Skill(SQLModel, table=True):
    """具体代码版本表。通过 Vector 实现 pgvector 高效检索"""
    __tablename__ = "skills"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="skill_groups.id")
    
    # 采用 语义化版本控制 (Semantic Versioning)
    major_version: int = Field(default=1)  # 主版本号：主动重构，接口/语义断崖式改变
    minor_version: int = Field(default=0)  # 次版本号：被动更新或兼容修复
    
    update_log: str = Field(default="")    # 重构时的 update log
    
    # ======== 代码与环境 ========
    code: str
    interface_schema: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON)) # 入参出参描述
    docstring: str = Field(default="")
    
    # 格式化的环境依赖 (PEP 508 格式，如 requirements.txt)
    python_dependencies: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    
    # ======== 评测与向量指标 ========
    retrieval_count: int = Field(default=0)      # 被检索系统召回的次数
    full_hit_count: int = Field(default=0)       # 完整/成功被 Agent 执行的次数
    partial_hit_count: int = Field(default=0)    # 成功执行但触发过外部 debug 的次数
    
    embedding: Optional[List[float]] = Field(default=None, sa_column=Column(Vector(1024)))
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # ======== 实体关联 ========
    group: Optional[SkillGroup] = Relationship(back_populates="skills")
    
    # 反向挂载衍生的多项实体 (1 对 多)
    maintenance_records: List["MaintenanceHistory"] = Relationship(back_populates="skill")
    test_reports: List["TestReport"] = Relationship(back_populates="skill")
    test_cases: List["TestCase"] = Relationship(back_populates="skill")
    refactor_plans: List["RefactorPlan"] = Relationship(back_populates="target_skill")

# ==========================================
# 3. 多对多依赖关系表 (SkillDependency)
# ==========================================
class SkillDependency(SQLModel, table=True):
    """记录上游调用者(Caller)和下游被调用者(Callee)之间的拓扑关系"""
    __tablename__ = "skill_dependencies"
    
    caller_id: int = Field(foreign_key="skills.id", primary_key=True)
    callee_id: int = Field(foreign_key="skills.id", primary_key=True)
    
    # True: 在被动重构时上游变化过大，系统放弃向下兼容，形成永久强版本绑定锁定。
    is_hard_pinned: bool = Field(default=False)

# ==========================================
# 4. 测试用例模型 (TestCase)
# ==========================================
class TestCase(SQLModel, table=True):
    """独立的测试用例实体，支持从旧版本直接复用（只读锁定）而不会因为正则出错"""
    __tablename__ = "skill_test_cases"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    skill_version_id: int = Field(foreign_key="skills.id") # 所属的Skill版本
    
    case_name: str
    
    # 一个完整可执行的 pytest 代码块。
    # 只要 Skill 的类名不变（或者我们约定只用固定名称的 wrapper function），代码就能无感重用
    executable_code: str  
    
    # 小版本更新时，Tester 将直接拷贝上一版本的 Case，并将其标记为 True，禁止以后对其进行修改或删除。
    is_legacy_locked: bool = Field(default=False)  
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    skill: Optional[Skill] = Relationship(back_populates="test_cases")

# ==========================================
# 5. 测试报告模型 (TestReport)
# ==========================================
class TestReport(SQLModel, table=True):
    """重构后，Tester给出的一份针对某特定版本的“质检证书”"""
    __tablename__ = "skill_test_reports"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    skill_version_id: int = Field(foreign_key="skills.id")
    
    is_passed: bool = Field(default=False)
    functional_score: int = Field(default=0)
    compatibility_status: str = Field(default="Not Evaluated") # 兼容性评估："Good", "Broken", ...
    failure_categories: List[TestFailureCategory] = Field(default_factory=list, sa_column=Column(JSON)) # 如果未通过，具体的错误成因归类
    
    report_text: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    skill: Optional[Skill] = Relationship(back_populates="test_reports")

# ==========================================
# 6. 重构规划工单 (RefactorPlan)
# ==========================================
class RefactorPlan(SQLModel, table=True):
    """蕴含了触发本次重构的动机、具体的主动/被动规划。不限于 traceback"""
    __tablename__ = "skill_refactor_plans"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    target_skill_id: int = Field(foreign_key="skills.id") # 明确指向触发重构的【当前特定版本】
    
    trigger_reason: str = Field(default="opportunity") # 比如 'traceback_error', 'opportunity_upgrade', 'manual_request'
    trigger_context: str = Field(default="") # 可以是报错，也可以是一段描述新需求的自然语言
    
    active_refactor_plan: str = Field(default="") # 主动重构规划：解决当前 skill 内部缺陷或添加新特性
    passive_refactor_plan: str = Field(default="") # 被动重构规划：针对底层 API 变化作出的兼容性调整计划
    update_type: str = Field(default="minor")
    hard_pinned_group_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    
    status: str = Field(default="OPEN") # 'OPEN', 'TESTING', 'RESOLVED'
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # 关系映射
    target_skill: Optional[Skill] = Relationship(back_populates="refactor_plans")

# ==========================================
# 7. 维护与交互历史 (JSON 瘦身机制)
# ==========================================
class MaintenanceHistory(SQLModel, table=True):
    """单独解耦对话记录，不污染主表，支持高速搜索"""
    __tablename__ = "skill_maintenance_history"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key="skills.id")
    
    # Extractor 和 Reviewer 之间可能极其冗长的历史记录
    extractor_trace: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    reviewer_trace: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    query_context: str = Field(default="")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    skill: Optional[Skill] = Relationship(back_populates="maintenance_records")


# ==========================================
# 8. 历史查询记录 (QueryRecord) — v2 协同过滤
# ==========================================
class QueryRecord(SQLModel, table=True):
    """历史查询记录表：为协同过滤检索积累语义快照与执行摘要。

    - 不存储完整 AgentTrace（避免表膨胀，完整 trace 已在 MaintenanceHistory 中）
    - agent_summary 由 Executor 执行结束后从 memory 自动提取前 500 字
    - produced_skill_id 允许 NULL（执行失败或未产出技能时）
    """
    __tablename__ = "skill_query_records"

    id: Optional[int] = Field(default=None, primary_key=True)

    # ── 查询语义 ──────────────────────────────────────────────────────────────
    query_text: str
    query_embedding: Optional[List[float]] = Field(
        default=None, sa_column=Column(Vector(1024))
    )

    # ── 产出关联 ──────────────────────────────────────────────────────────────
    produced_skill_id: Optional[int] = Field(default=None, foreign_key="skills.id")
    produced_skill_name: Optional[str] = None  # 冗余存储，方便 CLI 展示

    # ── 执行摘要 ──────────────────────────────────────────────────────────────
    agent_summary: str = Field(default="")
    remarks: str = Field(default="")

    created_at: datetime = Field(default_factory=datetime.utcnow)
