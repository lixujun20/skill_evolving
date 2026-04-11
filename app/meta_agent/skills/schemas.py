from typing import List, Dict, Optional, Any
from enum import Enum
from pydantic import BaseModel, Field

class FunctionType(str, Enum):
    ATOMIC = "atomic"       # 基础工具调用
    WORKFLOW = "workflow"   # 组合式工作流

class TraceFormat(str, Enum):
    REACT = "react"       # Standard Tool Calls (JSON)
    CODEACT = "codeact"   # IPython Code Execution

class RefactoringPattern(str, Enum):
    # 下面一一对应你提出的重构等级
    CREATE_NEW = "create_new"                   # 新建
    PARAMETER_EXTRACTION = "parameter_extraction" # 1级: 参数提取
    BRANCH_AUGMENTATION = "branch_augmentation"   # 1级: 分支增广
    ALGORITHM_MODIFICATION = "algorithm_modification" # 1级: 算法/逻辑修改
    DOCUMENTATION_UPDATE = "documentation_update" # 1级: 文档优化
    POLYMORPHISM_SPLIT = "polymorphism_split"     # 2级: 多态增广/分文件
    LOGIC_DECOUPLING = "logic_decoupling"         # 3级: 逻辑拆分
    WORKFLOW_COMPOSITION = "workflow_composition" # 4级: 组合成新工作流

class Skill(BaseModel):
    """
    Skill 的存储结构
    """
    name: str
    description: str
    code: str  # Python 源代码
    version: str = "0.1.0"
    function_type: FunctionType = FunctionType.ATOMIC
    # 依赖的其他 skill 或 tool
    dependencies: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

class TraceStep(BaseModel):
    """
    Agent 执行流中的单步
    """
    step_id: str
    thought: str
    tool_call: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None
    
    # CodeAct support
    code_block: Optional[str] = None # Python code for CodeAct
    
    # 标记这一步是否是在尝试调用某个旧 Skill
    related_skill_name: Optional[str] = None
    status: str = "success" # success, failed

class AgentTrace(BaseModel):
    """
    一次完整的 Agent 执行记录
    """
    query: str
    trace_format: TraceFormat = TraceFormat.REACT
    steps: List[TraceStep]
    final_answer: str
    # 本次 trace 涉及到的所有 skill（无论成功失败）
    involved_skills: List[str] = Field(default_factory=list)

    # v2 新增：Executor 执行前生成的 Python 工作流骨架（参考 workflow_codeact_builder.py 格式）
    # None 表示 planning 阶段失败或被跳过；不影响主流程；向后兼容
    workflow_plan: Optional[str] = None

class RefactoringPlan(BaseModel):
    """
    Gardener 分析出的重构计划
    """
    target_skill_name: str # 如果是新建，则为建议的新名称
    pattern: RefactoringPattern
    reasoning: str  # 为什么选择这个模式
    # 相关的 trace 片段索引（start_index, end_index）
    trace_segment: tuple[int, int]
    
class RefinedSkillResult(BaseModel):
    """
    重构后的结果
    """
    original_skill_name: Optional[str]
    new_skill: Skill
    pattern_used: RefactoringPattern
    changes_summary: str
