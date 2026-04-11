from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat

# ==========================================
# Dimension 5: 根据测试反馈的迭代优化（Test-Driven Refinement）
# 领域：教育教学 - 课程推荐与学习路径规划
# ==========================================

# ==========================================
# 5.1: 小版本迭代出现接口变更（Minor → Interface Breach → Fix）
# 场景：
#   - Skill recommend_next_course v1.0 存在，返回 str (course_id)
#   - Extractor 生成的 v1.1 错误地把返回类型改成了 List[str]（以为是无害增强）
#   - Reviewer 的 locked test 断言 isinstance(result, str) 失败
#   - Extractor 读取失败报告，回滚接口，改为向下兼容的 str 返回，新功能放在独立参数里
# ==========================================

SKILL_CODE_5_1_V1_0 = """
from typing import Optional

def recommend_next_course(student_id: str, subject_area: str,
                           current_level: int = 1) -> str:
    \"\"\"
    Recommend the next course for a student based on subject area and current level.

    Args:
        student_id (str): Student identifier.
        subject_area (str): Subject area (e.g., 'mathematics', 'computer_science').
        current_level (int): Current proficiency level 1-5. Default 1.

    Returns:
        str: Course ID of the recommended next course.

    Examples:
        >>> result = recommend_next_course("S1001", "mathematics", current_level=1)
        >>> isinstance(result, str)
        True
        >>> result.startswith("MATH")
        True
    \"\"\"
    course_map = {
        ("mathematics", 1): "MATH101",
        ("mathematics", 2): "MATH201",
        ("mathematics", 3): "MATH301",
        ("computer_science", 1): "CS101",
        ("computer_science", 2): "CS201",
        ("computer_science", 3): "CS301",
    }
    return course_map.get((subject_area, current_level), "ELECTIVE_OPEN")
"""

SKILL_TEST_CODE_5_1_V1_0 = """
def test_recommend_returns_string():
    \"\"\"LOCKED: recommend_next_course must return a str (course ID).\"\"\"
    result = recommend_next_course("S1001", "mathematics", current_level=1)
    assert isinstance(result, str), f"Expected str, got {type(result)}"

def test_recommend_known_path():
    result = recommend_next_course("S1001", "mathematics", current_level=2)
    assert result == "MATH201"

def test_recommend_unknown_defaults_to_elective():
    result = recommend_next_course("S1001", "philosophy", current_level=5)
    assert result == "ELECTIVE_OPEN"
"""

# v1.1 broken: Extractor incorrectly changed return type to List[str]
SKILL_CODE_5_1_V1_1_BROKEN = """
from typing import Optional, List

def recommend_next_course(student_id: str, subject_area: str,
                           current_level: int = 1) -> List[str]:
    \"\"\"
    Recommend next course(s) - BROKEN: Changed return type List[str] breaks v1.0 contract.
    \"\"\"
    course_map = {
        ("mathematics", 1): ["MATH101", "MATH101_ENRICHED"],
        ("mathematics", 2): ["MATH201"],
        ("computer_science", 1): ["CS101"],
        ("computer_science", 2): ["CS201", "CS201_LAB"],
    }
    return course_map.get((subject_area, current_level), ["ELECTIVE_OPEN"])
"""

# v1.1 fixed: keeps str return type, adds separate parameter for multiple options
SKILL_CODE_5_1_V1_1_FIXED = """
from typing import Optional, List, Union

def recommend_next_course(student_id: str, subject_area: str,
                           current_level: int = 1,
                           include_alternatives: bool = False) -> Union[str, List[str]]:
    \"\"\"
    Recommend the next course for a student.

    Args:
        student_id (str): Student identifier.
        subject_area (str): Subject area.
        current_level (int): Current level 1-5.
        include_alternatives (bool): If True, returns List[str]; else str (default).

    Returns:
        str if include_alternatives=False (default), List[str] if True.

    Examples:
        >>> result = recommend_next_course("S1001", "mathematics", current_level=1)
        >>> isinstance(result, str)
        True
    \"\"\"
    course_map = {
        ("mathematics", 1): ("MATH101", ["MATH101", "MATH101_ENRICHED"]),
        ("mathematics", 2): ("MATH201", ["MATH201"]),
        ("computer_science", 1): ("CS101", ["CS101"]),
        ("computer_science", 2): ("CS201", ["CS201", "CS201_LAB"]),
    }
    entry = course_map.get((subject_area, current_level), ("ELECTIVE_OPEN", ["ELECTIVE_OPEN"]))
    if include_alternatives:
        return entry[1]
    return entry[0]
"""

TRACE_DIM_5_1 = AgentTrace(
    query="Recommend what courses are available for S1001 at level 1 in mathematics. Show multiple choices if possible.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I need to show multiple course options. The current recommend_next_course only returns one course. I could change it to return a list.",
            code_block="""
from edu_skills import recommend_next_course

# Try current behavior
result = recommend_next_course("S1001", "mathematics", current_level=1)
print(f"Type: {type(result)}, Value: {result}")
""",
            tool_output="Type: <class 'str'>, Value: MATH101",
            status="success"
        ),
        TraceStep(
            step_id="step_2",
            thought="I want to return multiple options. I should add an include_alternatives parameter instead of changing the default return type, to preserve backward compatibility.",
            code_block="""
# After adding include_alternatives parameter
result_default = recommend_next_course("S1001", "mathematics", current_level=1)
result_with_alts = recommend_next_course("S1001", "mathematics", current_level=1, include_alternatives=True)
print(f"Default: {result_default} (str)")
print(f"With alternatives: {result_with_alts} (list)")
""",
            tool_output="Default: MATH101 (str)\nWith alternatives: ['MATH101', 'MATH101_ENRICHED'] (list)",
            status="success"
        )
    ],
    final_answer="recommend_next_course updated to v1.1: added include_alternatives parameter for multiple options while preserving str return type by default.",
    involved_skills=["recommend_next_course"]
)


# ==========================================
# 5.2: 大版本迭代中功能缺失（Major → Missing Feature → Fix）
# 场景：
#   - generate_study_plan v1.0 存在，功能简单（仅返回课程列表）
#   - Extractor 生成 v2.0，宣称完整重构为智能学习路径规划
#   - 但 v2.0 代码遗漏了 deadline_constraints 的处理，Reviewer 的新测试失败
#   - Extractor 读取失败报告，补完 deadline_constraints 逻辑
# ==========================================

SKILL_CODE_5_2_V1_0 = """
from typing import List

def generate_study_plan(student_id: str, target_courses: List[str]) -> List[str]:
    \"\"\"
    Generate a basic study plan listing courses in order.

    Args:
        student_id (str): Student identifier.
        target_courses (List[str]): Courses to include in the plan.

    Returns:
        List[str]: Ordered list of course IDs to study.

    Examples:
        >>> plan = generate_study_plan("S1001", ["CS101", "CS201", "CS301"])
        >>> len(plan) == 3
        True
    \"\"\"
    return list(target_courses)
"""

# v2.0 broken: deadline_constraints processing is omitted
SKILL_CODE_5_2_V2_0_BROKEN = """
from typing import List, Dict, Any, Optional

def generate_study_plan(student_id: str, target_courses: List[str],
                         current_progress: Optional[Dict[str, float]] = None,
                         deadline_constraints: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    \"\"\"
    Generate an intelligent study plan with progress and deadline awareness.
    (BROKEN: deadline_constraints is accepted but completely ignored)

    Args:
        student_id (str): Student identifier.
        target_courses (List[str]): Courses to include.
        current_progress (Optional[Dict[str, float]]): course_id -> completion_ratio.
        deadline_constraints (Optional[Dict[str, str]]): course_id -> deadline_date.

    Returns:
        Dict[str, Any]: {'plan': List[str], 'estimated_weeks': int, 'urgent': List[str]}
    \"\"\"
    progress = current_progress or {}
    remaining = [c for c in target_courses if progress.get(c, 0.0) < 1.0]
    # BUG: deadline_constraints is completely ignored!
    return {
        "plan": remaining,
        "estimated_weeks": len(remaining) * 4,
        "urgent": []  # BUG: always empty, deadlines not checked
    }
"""

# v2.0 fixed: deadline_constraints properly handled
SKILL_CODE_5_2_V2_0_FIXED = """
from typing import List, Dict, Any, Optional
from datetime import datetime

def generate_study_plan(student_id: str, target_courses: List[str],
                         current_progress: Optional[Dict[str, float]] = None,
                         deadline_constraints: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    \"\"\"
    Generate an intelligent study plan with progress and deadline awareness.

    Args:
        student_id (str): Student identifier.
        target_courses (List[str]): Courses to include.
        current_progress (Optional[Dict[str, float]]): course_id -> completion 0.0-1.0.
        deadline_constraints (Optional[Dict[str, str]]): course_id -> YYYY-MM-DD deadline.

    Returns:
        Dict[str, Any]: {
            'plan': List[str] (sorted by urgency),
            'estimated_weeks': int,
            'urgent': List[str] (courses with deadline within 30 days)
        }

    Examples:
        >>> plan = generate_study_plan("S1001", ["CS101", "CS201"])
        >>> 'plan' in plan and 'urgent' in plan
        True
    \"\"\"
    progress = current_progress or {}
    deadlines = deadline_constraints or {}
    today = datetime.today()

    remaining = [c for c in target_courses if progress.get(c, 0.0) < 1.0]

    urgent = []
    if deadlines:
        for course in remaining:
            if course in deadlines:
                try:
                    deadline_dt = datetime.strptime(deadlines[course], "%Y-%m-%d")
                    days_left = (deadline_dt - today).days
                    if 0 <= days_left <= 30:
                        urgent.append(course)
                except ValueError:
                    pass

    # Sort: urgent courses first
    plan = urgent + [c for c in remaining if c not in urgent]

    return {
        "plan": plan,
        "estimated_weeks": len(remaining) * 4,
        "urgent": urgent
    }
"""

SKILL_TEST_CODE_5_2_V2_0 = """
import pytest
from datetime import datetime, timedelta

def test_generate_plan_basic():
    result = generate_study_plan("S1001", ["CS101", "CS201", "CS301"])
    assert "plan" in result
    assert "urgent" in result
    assert len(result["plan"]) == 3

def test_generate_plan_skips_completed():
    result = generate_study_plan(
        "S1001",
        ["CS101", "CS201"],
        current_progress={"CS101": 1.0}
    )
    assert "CS101" not in result["plan"]
    assert "CS201" in result["plan"]

def test_generate_plan_urgent_courses_detected():
    tomorrow = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    result = generate_study_plan(
        "S1001",
        ["CS101", "CS201"],
        deadline_constraints={"CS201": tomorrow}
    )
    assert "CS201" in result["urgent"], "Course with deadline tomorrow must be urgent"

def test_generate_plan_urgent_courses_sorted_first():
    tomorrow = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    result = generate_study_plan(
        "S1001",
        ["CS101", "CS201"],
        deadline_constraints={"CS201": tomorrow}
    )
    assert result["plan"][0] == "CS201", "Urgent courses must appear first in plan"

def test_generate_plan_no_urgent_for_far_deadline():
    future = (datetime.today() + timedelta(days=365)).strftime("%Y-%m-%d")
    result = generate_study_plan(
        "S1001",
        ["CS101"],
        deadline_constraints={"CS101": future}
    )
    assert len(result["urgent"]) == 0
"""

TRACE_DIM_5_2 = AgentTrace(
    query="Generate a study plan for S1001 who needs to complete CS201 by next week. Include any urgent prioritization.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll call generate_study_plan with CS201 as an urgent course due in 7 days.",
            code_block="""
from edu_skills import generate_study_plan
from datetime import datetime, timedelta

next_week = (datetime.today() + timedelta(days=7)).strftime("%Y-%m-%d")
result = generate_study_plan(
    "S1001",
    ["CS101", "CS201", "CS301"],
    current_progress={"CS101": 1.0},  # CS101 already done
    deadline_constraints={"CS201": next_week}
)
print(result)
""",
            tool_output="{'plan': ['CS201', 'CS301'], 'estimated_weeks': 8, 'urgent': []}",
            status="success"
        ),
        TraceStep(
            step_id="step_2",
            thought="The urgent list is empty even though CS201 has a deadline next week! The deadline_constraints parameter is being ignored. I need to fix the logic to check deadlines.",
            code_block="""
# Verify deadline logic works independently
from datetime import datetime, timedelta
today = datetime.today()
deadline = today + timedelta(days=7)
days_left = (deadline - today).days
is_urgent = days_left <= 30
print(f"Days left: {days_left}, Is urgent: {is_urgent}")
""",
            tool_output="Days left: 7, Is urgent: True",
            status="success"
        )
    ],
    final_answer="Bug identified in v2.0: deadline_constraints ignored. Fix adds deadline checking and sorts urgent courses first.",
    involved_skills=["generate_study_plan"]
)
