# skill_evolving_v2 设计文档

本文件为 v2 版本的完整设计说明，覆盖问题背景、架构变化、新增组件设计、改动清单、测试策略与成本分析。
文档以中文撰写，面向项目开发者与维护者。以 v1 DESIGN.md 为基线，仅描述 delta。

---

## 1. v2 问题背景与动机

v1 已建立「检索 → 执行 → 提炼 → 验证」的四段闭环，但存在两个系统性缺陷：

### 缺陷 A：冷启动（Cold-Start）问题

- **现象**：当 skill DB 记录稀少时，query-skill embedding 相似度普遍偏低，检索返回空或噪音技能。
- **根本原因**：v1 的检索仅依赖 skill-side 向量空间（docstring/code embedding），完全没有利用历史查询的轨迹信息。
- **后果**：Executor 在缺乏参考技能的情况下退化为纯「从零解题」，丧失 skill reuse 价值；同时提炼出的技能也缺乏历史参考导致质量偏低。

### 缺陷 B：Executor 心智（Mindset）问题

- **现象**：Executor 直接执行 query，产出的 trace 是**为了解决当前 query** 而写的；Gardener 试图从中提炼「通用技能」，但 trace 本身的泛化性差。
- **根本原因**：v1 从未要求 Executor「先设计可复用解法再执行」，思维始终是「解决这道题」而非「设计可复用流程」。
- **后果**：Gardener 提炼的技能往往参数硬编码、边界条件处理不足，复用价值低。

---

## 2. v2 新增特性总览

| 特性 | 解决问题 | 核心机制 |
|------|---------|---------|
| **协同过滤检索（Collaborative Filtering）** | 缺陷 A 冷启动 | QueryRecord 表记录历史查询；双路检索融合 query-skill 直接相似 + query-query 协同信号 |
| **Executor Workflow Planning 模式** | 缺陷 B 心智问题 | Executor 执行前先生成 Python 工作流骨架；骨架随 trace 一起传递给 Gardener |

---

## 3. 架构变化总览

```
v1 pipeline（4 步）:
  query → [embed] → [pgvector: query ↔ skill] → executor → gardener → reviewer → commit

v2 pipeline（5+1 步）:
  query → [embed] ──┬── Path A: [pgvector: query ↔ skill]          ┐
                    └── Path B: [pgvector: query ↔ QueryRecord]     ┤ 融合 top-k
                                  → 历史 query 的 produced_skill     ┘
                  ↓
           [WorkflowPlannerStep]   ← 新增：单次 LLM 调用生成 Python 骨架
                  ↓
           executor (with plan as context hint)
                  ↓
           gardener (AgentTrace.workflow_plan 注入)
                  ↓
           reviewer → commit → [写入 QueryRecord]   ← 新增：为下次协同过滤积累数据
```

**新增组件**：
- `QueryRecord` SQLModel 表：记录每次执行的 query embedding、产出 skill_id 与执行摘要
- `CollaborativeRetriever`：继承 `SkillRetriever`，增加协同过滤路径与融合逻辑
- `_run_planning_phase()`：pipeline 中的轻量 LLM 规划步骤
- `AgentTrace.workflow_plan`：Optional str 字段，承载规划骨架

---

## 4. 新增 DB 模型：QueryRecord

**文件**：`app/meta_agent/skills/database/models.py`

```python
class QueryRecord(SQLModel, table=True):
    """历史查询记录表：为协同过滤检索积累语义快照与执行摘要。

    设计决策：
    - 不存储完整 AgentTrace JSON（避免表膨胀，完整 trace 已在 MaintenanceHistory 中）
    - agent_summary 由 Executor 执行结束后从 memory 最后一条 assistant message 自动提取
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
    produced_skill_name: Optional[str] = None   # 冗余存储，方便 CLI 展示

    # ── 执行摘要 ──────────────────────────────────────────────────────────────
    # 从 executor memory 最后一条 assistant message 前 500 字自动提取
    agent_summary: str = Field(default="")
    # 额外备注（测试阶段通过/失败摘要，或失败原因）
    remarks: str = Field(default="")

    created_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## 5. 协同过滤检索（Collaborative Filtering Retrieval）

### 5.1 双路检索架构

```
新查询 q
  ├─ Path A（直接相似）
  │    embed(q) <=> skill.embedding  [pgvector cosine]
  │    → top-k_a Skills，各带 direct_sim 分数
  │
  └─ Path B（协同过滤）
       embed(q) <=> query_record.query_embedding  [pgvector cosine]
       → top-m 历史 QueryRecord（Q1…Qm）
       → 取 Q1…Qm 的 produced_skill_id 集合（去重）
       → 对这些 skills 计算 collab_score = max(相关历史查询相似度)
       → top-k_b Skills，各带 collab_sim 分数

并行融合（Parallel Fusion）:
  final_score(skill) = alpha × direct_sim + (1 - alpha) × collab_sim
       其中 alpha = 0.6（默认），仅出现在一路时另一路分数补 0
  → 按 final_score 降序排列，返回 top_k
```

**冷启动保护**：`QueryRecord` 数量 < `collab_min_queries`（默认 3）时，Path B 自动跳过，退化为纯直接检索。

### 5.2 新类 `CollaborativeRetriever`

**文件**：`app/meta_agent/skills/retrieval.py`

```python
@dataclass
class CollabRetrievalResult(RetrievalResult):
    """协同过滤检索结果，在 RetrievalResult 基础上追加路径分析信息"""
    collab_signals: List[Dict] = field(default_factory=list)
    # e.g. [{"skill_id": 3, "direct_sim": 0.82, "collab_sim": 0.0, "final_score": 0.49, "source": "direct"}, ...]

class CollaborativeRetriever(SkillRetriever):
    """
    在 SkillRetriever 基础上增加协同过滤路径。

    Parameters
    ----------
    alpha : float
        Path A（直接相似）在融合分数中的权重，默认 0.6。
    collab_top_m : int
        协同过滤时检索的历史相似查询数，默认 10。
    collab_min_queries : int
        QueryRecord 数量低于此值时退化为纯直接检索（冷启动保护），默认 3。
    """

    async def retrieve_with_collab_filter(
        self,
        query: str,
        db_manager: SkillDatabaseManager,
        top_k: int = 5,
        alpha: float = 0.6,
        collab_top_m: int = 10,
        similarity_threshold: float = 0.5,
        collab_min_queries: int = 3,
        tags_filter: Optional[List[str]] = None,
    ) -> CollabRetrievalResult:
        ...
```

### 5.3 `SkillDatabaseManager` 新增方法

**文件**：`app/meta_agent/skills/database/manager.py`

```python
def search_similar_queries(
    self,
    query_embedding: List[float],
    top_m: int = 10,
    similarity_threshold: float = 0.4,
) -> List[QueryRecord]:
    """pgvector 检索 QueryRecord 表，找最相似的历史查询。"""

def save_query_record(
    self,
    query_text: str,
    query_embedding: List[float],
    produced_skill_id: Optional[int] = None,
    produced_skill_name: Optional[str] = None,
    agent_summary: str = "",
    remarks: str = "",
) -> QueryRecord:
    """执行结束后写入新的历史查询记录。"""
```

---

## 6. Executor Workflow Planning 模式

### 6.1 Workflow Plan 格式规范

参考 `workflow_codeact_builder.py` 的 workflow planner 设计，Plan 是一段**可执行的 Python 工具调用骨架**：

```python
# ── 格式示例 ──────────────────────────────────────────────────────────────────
# Query: 分析学生成绩单并生成改进建议

# Step 1: 获取学生成绩数据
from core_skills import fetch_student_transcript
transcript_data = fetch_student_transcript(student_id=STUDENT_ID)

# Step 2: 解析并结构化成绩
grades = [entry for entry in transcript_data if entry.get("score") is not None]
weak_subjects = [s for s in grades if s["score"] < PASS_THRESHOLD]

# Step 3: 为每个薄弱科目生成改进建议（变参）
recommendations = {}
for subject in weak_subjects:
    # 调用 LLM skill 生成建议
    suggestions = generate_study_recommendations(
        subject_name=subject["name"],
        current_score=subject["score"],
    )
    recommendations[subject["name"]] = suggestions

return_({"grades": grades, "weak_subjects": weak_subjects, "recommendations": recommendations})
```

**格式规则**（源自 `workflow_codeact_builder.py`）：
1. 用 Python 代码表示，每步可加注释说明意图
2. 工具/skill 调用使用函数调用语法（已 `from core_skills import` 导入的技能直接调用）
3. 用 `return_()` 输出最终结果
4. 鼓励控制流（if/for）、函数定义、错误处理
5. 具体占位参数用 `CAPS_VARIABLE` 表示（提示这是可变输入）
6. Plan 是**骨架**，不要求完全正确；Executor 执行时可以偏离，Gardener 以 plan 为参考理解意图

### 6.2 AgentTrace 新增字段

**文件**：`app/meta_agent/skills/schemas.py`

```python
class AgentTrace(BaseModel):
    query: str
    trace_format: TraceFormat = TraceFormat.REACT
    steps: List[TraceStep]
    final_answer: str
    involved_skills: List[str] = Field(default_factory=list)

    # ── v2 新增 ──────────────────────────────────────────────────────────────
    # Executor 执行前生成的 Python 工作流骨架（参考 workflow_codeact_builder.py 格式）
    # None 表示 planning 阶段失败或被跳过；不影响主流程
    workflow_plan: Optional[str] = None
```

### 6.3 WorkflowPlannerStep 实现

**实现位置**：`pipeline.py` 新增 `_run_planning_phase()`

核心逻辑：
- 单次 `LLM.ask()` 调用（非 agent loop），使用 `tool_maker` config
- System prompt 直接取自 `workflow_codeact_builder._generate_workflow` 的 `system_prompt` 字符串（中文 workflow planner 提示词，含 5 条指导原则）
- User message 包含：`query` + `retrieved skills 列表（name + docstring 概要）`
- 解析返回的 ` ```python ... ``` ` 代码块
- 失败（空响应/格式错误/语法错误）时返回 `None`，降级为无 plan 执行（不阻断流程）

### 6.4 Gardener 上下文注入

**文件**：`app/meta_agent/skills/gardener_agent.py`，`run_extraction()` 中的 `user_msg` 构建处。

当 `trace.workflow_plan is not None` 时，在 `user_msg` 末尾追加：

````
Additionally, a Workflow Plan (Python skeleton) was designed BEFORE execution as a guide for the intended generalized solution pattern:

```python
{trace.workflow_plan}
```

When deciding skill boundary, abstraction level, and parameter interface, treat this plan as evidence of the INTENDED reusable design. The actual execution trace may deviate from the plan — that is expected.
````

---

## 7. Pipeline v2 完整流程（5+1 阶段）

```
Phase 0: Retrieval（协同过滤版）
  embed(query) → 复用 embedding（后续阶段共享，不重复调用）
  CollaborativeRetriever.retrieve_with_collab_filter()
  → ColabRetrievalResult（含 collab_signals）

Phase 1: Planning（新增）
  _run_planning_phase(query, retrieved_skills) → workflow_plan: Optional[str]
  单次 LLM.ask()；失败时返回 None，继续

Phase 2: Executor
  _build_executor_workflow() 扩展：在 workflow guideline 末尾追加
    "## Suggested Workflow Plan（仅供参考）\n{workflow_plan}"（如果不为 None）
  执行 CodeAct；_memory_to_agent_trace() 时设置 trace.workflow_plan = workflow_plan

Phase 3: Extractor（Gardener）
  run_extraction(trace, db_manager, target_skill_id)
  trace.workflow_plan 不为 None 时，user_msg 末尾追加 plan 引导段

Phase 4: Tester（Reviewer）
  无变化

Phase 5+1: Commit + QueryRecord 写入（新增）
  _run_commit_phase() 完成后：
    db_manager.save_query_record(
        query_text   = result.query,
        query_embedding = phase0_embedding,   # 复用，不重复调用
        produced_skill_id = result.new_skill_id,
        produced_skill_name = skill_name,
        agent_summary = _extract_executor_summary(result.execution_trace),
        remarks       = result.tester.detail[:300],
    )
```

**注**：`phase0_embedding` 在 Phase 0 计算后存入 `PipelineResult`（或 `_run_phases` 局部变量），在 Commit 阶段复用，**不额外调用 ZhipuAI API**。

---

## 8. 文件改动清单

| 文件 | 变更类型 | 核心内容 |
|------|---------|---------|
| `database/models.py` | **新增** | `QueryRecord` SQLModel 表（含 Vector(1024) 列） |
| `database/manager.py` | **新增方法** | `search_similar_queries()`, `save_query_record()` |
| `retrieval.py` | **新增类/数据类** | `CollabRetrievalResult`, `CollaborativeRetriever.retrieve_with_collab_filter()` |
| `schemas.py` | **新增字段** | `AgentTrace.workflow_plan: Optional[str] = None` |
| `pipeline.py` | **新增方法+修改** | `_run_planning_phase()`, 修改 `_run_phases()` 插入 Phase 1，Phase 0 改用 `CollaborativeRetriever`，Commit 后调用 `save_query_record()` |
| `gardener_agent.py` | **小修改** | `run_extraction()` 中 `user_msg` 追加 plan 引导段（仅当 `workflow_plan is not None`） |

---

## 9. 成本分析

| 新增开销 | 每次 query 估算 | 控制手段 |
|---------|---------------|---------|
| Planning LLM call（Phase 1） | ~500 tokens × $0.003/1K ≈ **$0.0015** | 失败不重试；`skip_planning=True` 可跳过 |
| `search_similar_queries` pgvector 查询 | < 1 ms，极低 | 无需控制 |
| `save_query_record` DB INSERT | < 1 ms，极低 | 无需控制 |
| query embedding 调用 | 零（Phase 0 已计算，复用） | 复用机制保证不重复调用 |

总结：v2 额外成本约 **$0.0015/query**（仅 planning LLM call），可接受。

---

## 10. 测试策略

### 10.1 非 LLM 单元测试（新增，不需要网络）

**`tests/unit/test_collab_retrieval.py`**：
- 空 `QueryRecord` 表时，`ColabRetriever` 正确退化为纯直接检索（`collab_min_queries` 保护）
- 有 ≥ 3 条 `QueryRecord` 时，协同路径贡献的 `collab_sim` > 0
- `final_score = alpha × direct + (1-alpha) × collab` 计算正确
- 融合去重后结果数 ≤ top_k

**`tests/unit/test_query_record_manager.py`**：
- `save_query_record()` 正确写入所有字段
- `search_similar_queries()` 按余弦距离升序返回

### 10.2 LLM 集成测试扩展（扩展现有 `test_single_point.py`）

```python
# 新增断言
assert result.execution_trace.workflow_plan is not None
import ast
ast.parse(result.execution_trace.workflow_plan)  # 合法 Python 语法
```

### 10.3 长期演化测试扩展（扩展现有 `test_long_term.py`）

Round 2 以后，验证协同过滤信号生效：
```python
assert len(result.retrieval_result.collab_signals) > 0
```

---

## 11. 降级策略（风险矩阵）

| 风险 | 触发条件 | 降级行为 |
|------|---------|---------|
| Planning LLM 超时/失败 | LLM 返回空/格式错误/SyntaxError | `workflow_plan = None`，主流程继续 |
| 协同路径无数据 | `QueryRecord` 数 < `collab_min_queries` | Path B 跳过，纯 Path A 直接检索 |
| pgvector 未安装 | `search_similar_queries` 抛 `ProgrammingError` | 捕获异常，返回 `[]`，不阻断 |
| Plan 语法有误 | Gardener `user_msg` 中 plan 含非法 Python | Gardener prompt 中标注「plan 仅供参考，不保证语法正确」 |
| `QueryRecord.query_embedding` 为 NULL | Phase 0 embedding 生成失败时跳过写入 | `save_query_record` 写入时 embedding 置 None（不进入协同检索池，但记录本身仍写入） |

---

## 12. 实现 PLAN（6 个子任务）

按顺序执行，每个子任务为独立可测试单元：

```
Task 1 — DB 层扩展
  文件: database/models.py, database/manager.py
  内容: QueryRecord SQLModel; search_similar_queries(); save_query_record()
  测试: test_query_record_manager.py（mock engine）

Task 2 — 检索层扩展
  文件: retrieval.py
  内容: CollabRetrievalResult; CollaborativeRetriever.retrieve_with_collab_filter()
  测试: test_collab_retrieval.py（mock QueryRecord）

Task 3 — Schema 扩展
  文件: schemas.py
  内容: AgentTrace.workflow_plan: Optional[str] = None
  验证: 不破坏现有序列化（backward compatible，已有 trace JSON 加载正常）

Task 4 — Pipeline 规划阶段
  文件: pipeline.py
  内容: _run_planning_phase(); 修改 _run_phases(); Phase 0 改用 CollaborativeRetriever;
         Commit 阶段末尾写 QueryRecord
  测试: 集成测试中 result.execution_trace.workflow_plan not None

Task 5 — Gardener 上下文注入
  文件: gardener_agent.py
  内容: run_extraction() 中 user_msg 追加 plan 引导段（3 行修改）

Task 6 — 单元测试补全
  文件: tests/unit/test_collab_retrieval.py, tests/unit/test_query_record_manager.py
  内容: 覆盖降级路径、空表行为、融合计算正确性
```

---

## 13. 变更说明

- **向后兼容**：`AgentTrace.workflow_plan` 为 `Optional` 字段，已有测试代码无需修改
- **DB 迁移**：新增 `skill_query_records` 表，需执行 `SQLModel.metadata.create_all()` 或 alembic migration（开发阶段走 `create_all` 即可）
- **API 兼容**：`SkillEvolvingPipeline.run()` / `run_with_session()` 签名不变，内部透明升级

---

文档最后更新：2026-04-06
由 GitHub Copilot 根据 skill_evolving_v1 设计与 workflow_codeact_builder.py 约束自动生成，写入：
`/home/lixujun/skill_evolving/app/copilot_cli/DESIGN_V2.md`
