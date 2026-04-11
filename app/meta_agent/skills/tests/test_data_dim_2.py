from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat

# ==========================================
# Dimension 2: 拓扑依赖体系 (Topological Dependencies)
# 领域：教育教学 - 个性化学习推荐平台
# 技能树：
#   assess_student_knowledge(student_id, subject) -> int  (base, v1.0)
#   assess_student_knowledge -> Dict[str, Any]             (base, v2.0, Major)
#   recommend_next_course(student_id, subject)             (mid, depends on assess)
#   generate_weekly_learning_plan(student_id)              (top, depends on recommend)
# ==========================================

# ---- 基础 Skill 代码 ----

SKILL_ASSESS_V1_0 = """
from typing import Optional

def assess_student_knowledge(student_id: str, subject: str, verbose: Optional[bool] = None) -> int:
    \"\"\"
    Assess a student's current knowledge level for a subject.

    Args:
        student_id (str): Unique student identifier, e.g. 'S1001'.
        subject (str): Subject name, e.g. 'Algebra'.
        verbose (Optional[bool]): Unused in v1.0, reserved for future use.

    Returns:
        int: A knowledge score from 0 (none) to 100 (expert).

    Examples:
        >>> assess_student_knowledge("S1001", "Algebra")
        72
    \"\"\"
    db = {
        "S1001": {"Algebra": 72, "Geometry": 85},
        "S1002": {"Algebra": 45, "Geometry": 60},
    }
    return db.get(student_id, {}).get(subject, 0)
"""

SKILL_ASSESS_V1_2 = """
from typing import Optional

def assess_student_knowledge(student_id: str, subject: str, verbose: Optional[bool] = False) -> int:
    \"\"\"
    Assess a student's current knowledge level.

    Args:
        student_id (str): Unique student identifier.
        subject (str): Subject name.
        verbose (Optional[bool]): If True, prints a detailed report to stdout. Default: False.

    Returns:
        int: A knowledge score from 0 to 100.

    Examples:
        >>> assess_student_knowledge("S1001", "Algebra")
        72
        >>> assess_student_knowledge("S1001", "Algebra", verbose=True)
        [INFO] S1001 scored 72 in Algebra.
        72
    \"\"\"
    db = {
        "S1001": {"Algebra": 72, "Geometry": 85},
        "S1002": {"Algebra": 45, "Geometry": 60},
    }
    score = db.get(student_id, {}).get(subject, 0)
    if verbose:
        print(f"[INFO] {student_id} scored {score} in {subject}.")
    return score
"""

SKILL_ASSESS_V2_0 = """
from typing import Dict, Any, List

def assess_student_knowledge(student_id: str, subject: str) -> Dict[str, Any]:
    \"\"\"
    Assess a student's knowledge (v2.0 - structured output).

    Args:
        student_id (str): Unique student identifier.
        subject (str): Subject name.

    Returns:
        Dict[str, Any]: Contains 'score' (int), 'weak_topics' (List[str]),
                        'recommendation' (str).

    Examples:
        >>> result = assess_student_knowledge("S1001", "Algebra")
        >>> result['score']
        72
        >>> result['weak_topics']
        ['Quadratic Equations']
    \"\"\"
    db = {
        "S1001": {
            "Algebra": {"score": 72, "weak_topics": ["Quadratic Equations"], "recommendation": "Focus on factoring"},
        },
        "S1002": {
            "Algebra": {"score": 45, "weak_topics": ["Linear Equations", "Inequalities"], "recommendation": "Restart from basics"},
        },
    }
    return db.get(student_id, {}).get(subject, {"score": 0, "weak_topics": [], "recommendation": "Start learning"})
"""

SKILL_ASSESS_V3_0 = """
import asyncio
from typing import Dict, Any

async def assess_student_knowledge(student_id: str, subject: str, session_token: str) -> Dict[str, Any]:
    \"\"\"
    Assess student knowledge asynchronously with session auth (v3.0 - async+auth).

    Args:
        student_id (str): Student ID.
        subject (str): Subject.
        session_token (str): Auth token for the new assessment service.

    Returns:
        Dict[str, Any]: Structured knowledge assessment.
    \"\"\"
    await asyncio.sleep(0.01)  # simulated async call
    return {"score": 72, "weak_topics": ["Quadratic Equations"], "recommendation": "Focus on factoring"}
"""

SKILL_RECOMMEND_V1_0 = """
from typing import Optional

def recommend_next_course(student_id: str, target_subject: str) -> str:
    \"\"\"
    Recommend the best next course for a student in a given subject.

    Args:
        student_id (str): Student identifier.
        target_subject (str): The subject area for the recommendation.

    Returns:
        str: Course ID string, e.g. 'ALG-201'.

    Examples:
        >>> recommend_next_course("S1001", "Algebra")
        'ALG-201'
    \"\"\"
    # Depends on assess_student_knowledge v1.0 which returns int
    from upstream_skills import assess_student_knowledge
    knowledge_level = assess_student_knowledge(student_id, target_subject)

    if knowledge_level < 50:
        return f"{target_subject[:3].upper()}-101"   # Beginner
    elif knowledge_level < 80:
        return f"{target_subject[:3].upper()}-201"   # Intermediate
    else:
        return f"{target_subject[:3].upper()}-301"   # Advanced
"""

SKILL_ASSESS_GAP_V1_0 = """
from typing import List

def assess_gap_area(student_id: str, subject: str) -> List[str]:
    \"\"\"
    Identify specific gap areas a student has in a subject.

    Args:
        student_id (str): Student identifier.
        subject (str): Subject to assess.

    Returns:
        List[str]: A list of topic names the student is weak in.

    Examples:
        >>> assess_gap_area("S1001", "Algebra")
        ['Quadratic Equations']
    \"\"\"
    from upstream_skills import assess_student_knowledge
    level = assess_student_knowledge(student_id, subject)  # Uses v1.0, returns int
    if level < 50:
        return ["Linear Equations", "Inequalities"]
    elif level < 80:
        return ["Quadratic Equations"]
    return []
"""


SKILL_TEST_ASSESS_V1_0 = """
import pytest

def test_assess_student_knowledge_known():
    score = assess_student_knowledge("S1001", "Algebra")
    assert isinstance(score, int)
    assert 0 <= score <= 100

def test_assess_student_knowledge_unknown_student():
    score = assess_student_knowledge("UNKNOWN", "Algebra")
    assert score == 0
"""

SKILL_TEST_RECOMMEND_V1_0 = """
import pytest
from unittest.mock import patch

def test_recommend_next_course_beginner():
    with patch('upstream_skills.assess_student_knowledge', return_value=40):
        result = recommend_next_course("S1002", "Algebra")
    assert result == "ALG-101"

def test_recommend_next_course_intermediate():
    with patch('upstream_skills.assess_student_knowledge', return_value=72):
        result = recommend_next_course("S1001", "Algebra")
    assert result == "ALG-201"
"""

# ==========================================
# 2.1: 上游小版本静默更新
# Trace: 用户需要推荐课程，Extractor 检测到 assess_student_knowledge v1.2 存在（新增 verbose 参数）
# recommend_next_course 和 assess_gap_area 均不需要修改调用逻辑
# ==========================================
TRACE_DIM_2_1 = AgentTrace(
    query="Recommend the next Algebra course for student S1001.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll use the existing `recommend_next_course` skill to find a course for S1001 in Algebra.",
            code_block="""
from upstream_skills import recommend_next_course

try:
    course_id = recommend_next_course("S1001", "Algebra")
    print(f"Recommended course: {course_id}")
except Exception as e:
    print(f"Error: {e}")
""",
            tool_output="Recommended course: ALG-201",
            status="success"
        )
    ],
    final_answer="The recommended course for S1001 in Algebra is ALG-201.",
    involved_skills=["recommend_next_course", "assess_student_knowledge"]
)

# ==========================================
# 2.2: 上游大版本阻断性爆发
# Trace: 调用 recommend_next_course，但 assess_student_knowledge 已升至 v2.0 返回 dict。
# Agent 发现旧调用方式（对 int 比较）阻断了执行，手动写了兼容 v2.0 的代码。
# Extractor 应在 passive_plan 里标注：修改所有使用 level 的比较逻辑为 level["score"]。
# ==========================================
TRACE_DIM_2_2 = AgentTrace(
    query="Recommend the next Algebra course for student S1001.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll call `recommend_next_course` which internally calls `assess_student_knowledge`.",
            code_block="""
from upstream_skills import recommend_next_course

try:
    course_id = recommend_next_course("S1001", "Algebra")
    print(f"Recommended course: {course_id}")
except TypeError as e:
    print(f"TypeError encountered: {e}")
""",
            tool_output="TypeError encountered: '<' not supported between instances of 'dict' and 'int'",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="The `assess_student_knowledge` now returns a dict with 'score' key. The downstream `recommend_next_course` compares `knowledge_level < 50` which fails on a dict. I need to manually call with the new interface and adapt the logic.",
            code_block="""
from upstream_skills import assess_student_knowledge

# Call the upgraded v2.0 API
result = assess_student_knowledge("S1001", "Algebra")
print("v2.0 result:", result)

# Adapt logic to use result['score']
knowledge_level = result["score"]

if knowledge_level < 50:
    course_id = "ALG-101"
elif knowledge_level < 80:
    course_id = "ALG-201"
else:
    course_id = "ALG-301"

print(f"Recommended course: {course_id}")
print(f"Weak topics: {result['weak_topics']}")
""",
            tool_output="""v2.0 result: {'score': 72, 'weak_topics': ['Quadratic Equations'], 'recommendation': 'Focus on factoring'}
Recommended course: ALG-201
Weak topics: ['Quadratic Equations']""",
            status="success"
        )
    ],
    final_answer="ALG-201 recommended; student S1001 needs to focus on Quadratic Equations.",
    involved_skills=["recommend_next_course", "assess_student_knowledge"]
)

# ==========================================
# 2.3: 菱形依赖分支冲突
# Skill_Top: generate_weekly_plan -> recommend_next_course (uses assess v2.0)
#                                 -> assess_gap_area      (still uses assess v1.0)
# assess_student_knowledge has been bumped to v2.0, causing inconsistent behaviour.
# Trace: User triggers generate_weekly_plan, and assess_gap_area fails because it treats dict as int.
# ==========================================
TRACE_DIM_2_3 = AgentTrace(
    query="Generate a weekly learning plan for student S1001.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll call `assess_gap_area` to find weaknesses and `recommend_next_course` for course, then combine into a plan.",
            code_block="""
from upstream_skills import assess_gap_area, recommend_next_course

gaps = assess_gap_area("S1001", "Algebra")
print("Gaps:", gaps)
""",
            tool_output="TypeError: '<' not supported between instances of 'dict' and 'int'",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="assess_gap_area is broken because it depends on assess_student_knowledge v1.0 (returns int) but the system now has v2.0 (returns dict). I need to bypass assess_gap_area and get weak_topics directly from the new v2.0 API.",
            code_block="""
from upstream_skills import assess_student_knowledge, recommend_next_course

# Use v2.0 directly for both needs
assess_result = assess_student_knowledge("S1001", "Algebra")
gaps = assess_result["weak_topics"]
course_id = recommend_next_course("S1001", "Algebra")  # already uses v2.0

plan = {
    "student_id": "S1001",
    "subject": "Algebra",
    "recommended_course": course_id,
    "gap_topics_to_address": gaps
}
import json
print(json.dumps(plan, indent=2))
""",
            tool_output="""{
  "student_id": "S1001",
  "subject": "Algebra",
  "recommended_course": "ALG-201",
  "gap_topics_to_address": ["Quadratic Equations"]
}""",
            status="success"
        )
    ],
    final_answer="Weekly plan for S1001 generated by bypassing the broken assess_gap_area.",
    involved_skills=["assess_gap_area", "recommend_next_course", "assess_student_knowledge"]
)

# ==========================================
# 2.4: 硬锁定防御
# recommend_next_course is hard-pinned to assess_student_knowledge v1.0.
# The system now has v3.0 (async + auth token). Trace triggers recommend_next_course.
# Extractor should detect hard_pinned and skip passive upgrade.
# ==========================================
TRACE_DIM_2_4 = AgentTrace(
    query="Recommend the next course for S1001 in Geometry.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="Call recommend_next_course for Geometry. The skill is pinned to assess_student_knowledge v1.0 so it should still work correctly.",
            code_block="""
from upstream_skills import recommend_next_course

result = recommend_next_course("S1001", "Geometry")
print(f"Recommended: {result}")
""",
            tool_output="Recommended: GEO-301",
            status="success"
        )
    ],
    final_answer="GEO-301 recommended. Hard-pinned dependency on v1.0 kept the skill stable.",
    involved_skills=["recommend_next_course", "assess_student_knowledge"]
)

# ==========================================
# 2.5: 混合级联更新
# recommend_next_course depends on two upstreams:
#   - assess_student_knowledge: Minor v1.0 -> v1.2 (compatible, no code change)
#   - load_course_catalog(subject) -> List[str]: Major v1.0 -> v2.0 (now returns Dict)
# Trace triggers both, showing that catalog call breaks.
# ==========================================

SKILL_CATALOG_V1_0 = """
from typing import List

def load_course_catalog(subject: str) -> List[str]:
    \"\"\"
    Load available course IDs for a given subject.

    Args:
        subject (str): Subject name.

    Returns:
        List[str]: List of available course IDs, e.g. ['ALG-101', 'ALG-201', 'ALG-301'].

    Examples:
        >>> load_course_catalog("Algebra")
        ['ALG-101', 'ALG-201', 'ALG-301']
    \"\"\"
    catalog = {
        "Algebra": ["ALG-101", "ALG-201", "ALG-301"],
        "Geometry": ["GEO-101", "GEO-201", "GEO-301"]
    }
    return catalog.get(subject, [])
"""

SKILL_CATALOG_V2_0 = """
from typing import Dict, Any

def load_course_catalog(subject: str) -> Dict[str, Any]:
    \"\"\"
    Load available courses with metadata (v2.0 - structured output).

    Args:
        subject (str): Subject name.

    Returns:
        Dict[str, Any]: Contains 'courses' (List[Dict]) with 'id', 'level', 'prerequisites'.
    \"\"\"
    catalog = {
        "Algebra": {
            "courses": [
                {"id": "ALG-101", "level": "beginner", "prerequisites": []},
                {"id": "ALG-201", "level": "intermediate", "prerequisites": ["ALG-101"]},
                {"id": "ALG-301", "level": "advanced", "prerequisites": ["ALG-201"]},
            ]
        }
    }
    return catalog.get(subject, {"courses": []})
"""

TRACE_DIM_2_5 = AgentTrace(
    query="Recommend a course for S1001 in Algebra with prerequisites check.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll call assess_student_knowledge (v1.2, minor, compatible) and load_course_catalog (now v2.0, major, returns Dict instead of List).",
            code_block="""
from upstream_skills import assess_student_knowledge, load_course_catalog

# assess still works (v1.2, minor update, same signature)
level = assess_student_knowledge("S1001", "Algebra")
print("Level:", level)

# load_course_catalog fails: trying to index by integer a dict
catalog = load_course_catalog("Algebra")
print("Catalog type:", type(catalog))
""",
            tool_output="""Level: 72
Catalog type: <class 'dict'>""",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="The catalog is now a dict with a 'courses' key. I'll adapt to use catalog['courses'] and filter by matching level.",
            code_block="""
from upstream_skills import assess_student_knowledge, load_course_catalog

level = assess_student_knowledge("S1001", "Algebra")
catalog = load_course_catalog("Algebra")

# Adapt: catalog is now a dict with 'courses' list of dicts
courses = catalog["courses"]
if level < 50:
    recommended = [c for c in courses if c["level"] == "beginner"]
elif level < 80:
    recommended = [c for c in courses if c["level"] == "intermediate"]
else:
    recommended = [c for c in courses if c["level"] == "advanced"]

print("Recommended:", recommended[0]["id"] if recommended else "None")
""",
            tool_output="Recommended: ALG-201",
            status="success"
        )
    ],
    final_answer="ALG-201 recommended after adapting to load_course_catalog v2.0 (Dict output).",
    involved_skills=["recommend_next_course", "assess_student_knowledge", "load_course_catalog"]
)

# ==========================================
# 2.6: 环形依赖尝试拦截
# 场景: Skill_A (assess_student_knowledge) 已经调用了 Skill_B (load_course_catalog)
# 现在 Trace 要求 Skill_B 内调用 Skill_A 以获取辅助学生水平数据，形成环形依赖。
# Extractor 应该在 Plan 中拒绝并提出抽取 Skill_C 避免环。
# ==========================================
TRACE_DIM_2_6 = AgentTrace(
    query="Improve load_course_catalog to personalize based on student's current level.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I could make load_course_catalog smarter by calling assess_student_knowledge inside it. But wait - if assess_student_knowledge already imports from load_course_catalog, this would create a circular import.",
            code_block="""
# Attempting a prospective design:
# load_course_catalog could call assess_student_knowledge to filter
# But assess_student_knowledge already depends on load_course_catalog context
# This creates a CIRCULAR dependency:
#   load_course_catalog -> assess_student_knowledge -> load_course_catalog
# 
# Better approach: extract a shared helper 'get_subject_levels()' as a base utility
# that both can safely call without circular imports.

def get_subject_levels(subject: str) -> dict:
    \"\"\"Shared lightweight helper - no circular imports\"\"\"
    levels = {"Algebra": [101, 201, 301], "Geometry": [101, 201, 301]}
    return {"available_levels": levels.get(subject, [])}

print("Extracted base utility to avoid circular dependency.")
print(get_subject_levels("Algebra"))
""",
            tool_output="""Extracted base utility to avoid circular dependency.
{'available_levels': [101, 201, 301]}""",
            status="success"
        )
    ],
    final_answer="Circular dependency avoided by extracting a shared base skill `get_subject_levels`.",
    involved_skills=["load_course_catalog", "assess_student_knowledge"]
)

# ==========================================
# 2.7: 上游废弃导致的强制阻断
# assess_student_knowledge v1.0 is deprecated (deleted). Only v3.0 exists (async + session_token).
# recommend_next_course depends on v1.0. Trace triggers it and everything breaks.
# Extractor must recognize this as a FORCED passive update.
# ==========================================
TRACE_DIM_2_7 = AgentTrace(
    query="Recommend the next course for student S1001 in Algebra.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="Call recommend_next_course. Should work fine.",
            code_block="""
from upstream_skills import recommend_next_course
try:
    result = recommend_next_course("S1001", "Algebra")
    print(result)
except Exception as e:
    print(f"DEPENDENCY ERROR: {e}")
""",
            tool_output="DEPENDENCY ERROR: ImportError: cannot import 'assess_student_knowledge' v1.0 — it has been deprecated and removed. Latest available: v3.0 (async).",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="The upstream assess_student_knowledge v1.0 is gone. I must migrate to v3.0 which requires async and a session_token. I'll write an adapter wrapper for the forced migration.",
            code_block="""
import asyncio
from new_upstream_v3 import assess_student_knowledge as assess_v3

async def recommend_next_course_adapted(student_id: str, subject: str, session_token: str) -> str:
    result = await assess_v3(student_id, subject, session_token)
    level = result["score"]
    prefix = subject[:3].upper()
    if level < 50:
        return f"{prefix}-101"
    elif level < 80:
        return f"{prefix}-201"
    else:
        return f"{prefix}-301"

# Test adapted version
result = asyncio.run(recommend_next_course_adapted("S1001", "Algebra", session_token="tok_abc123"))
print(f"Recommended: {result}")
""",
            tool_output="Recommended: ALG-201",
            status="success"
        )
    ],
    final_answer="Forced migration of recommend_next_course to use assess_student_knowledge v3.0.",
    involved_skills=["recommend_next_course", "assess_student_knowledge"]
)
