from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat

# ==========================================
# Dimension 4: 多次溯源与历史记忆（History Timeline）
# 领域：教育教学 - 学生学习进度追踪系统
# ==========================================

# ==========================================
# 4.1: 连续的小版本滚雪球测试（Minor Incremental Avalanche）
# 场景：对 track_learning_progress 做5次连续 Minor 修改，v1.0→v1.5
# 每次 Trace 增加一个可选参数/特性，检验全量累积测试的兼容性
# ==========================================

SKILL_CODE_4_1_V1_0 = """
from typing import Dict, Any

def track_learning_progress(student_id: str, course_id: str) -> Dict[str, Any]:
    \"\"\"
    Track a student's learning progress for a given course.

    Args:
        student_id (str): Unique student identifier.
        course_id (str): Course identifier.

    Returns:
        Dict[str, Any]: {'student_id': str, 'course_id': str, 'progress': float}
                        where progress is 0.0-1.0.

    Examples:
        >>> result = track_learning_progress("S1001", "CS101")
        >>> 'progress' in result
        True
        >>> 0.0 <= result['progress'] <= 1.0
        True
    \"\"\"
    progress_db = {
        ("S1001", "CS101"): 0.75,
        ("S1002", "CS101"): 0.50,
    }
    progress = progress_db.get((student_id, course_id), 0.0)
    return {"student_id": student_id, "course_id": course_id, "progress": progress}
"""

SKILL_CODE_4_1_V1_1 = """
from typing import Dict, Any

def track_learning_progress(student_id: str, course_id: str,
                             include_unit_breakdown: bool = False) -> Dict[str, Any]:
    \"\"\"
    Track a student's learning progress for a given course.

    Args:
        student_id (str): Unique student identifier.
        course_id (str): Course identifier.
        include_unit_breakdown (bool): If True, include per-unit scores. Default False.

    Returns:
        Dict[str, Any]: Base keys: student_id, course_id, progress.
                        If include_unit_breakdown=True, adds 'units' key.
    \"\"\"
    progress_db = {
        ("S1001", "CS101"): 0.75,
        ("S1002", "CS101"): 0.50,
    }
    unit_db = {
        ("S1001", "CS101"): {"unit1": 1.0, "unit2": 0.5},
        ("S1002", "CS101"): {"unit1": 0.75, "unit2": 0.25},
    }
    progress = progress_db.get((student_id, course_id), 0.0)
    result = {"student_id": student_id, "course_id": course_id, "progress": progress}
    if include_unit_breakdown:
        result["units"] = unit_db.get((student_id, course_id), {})
    return result
"""

SKILL_CODE_4_1_V1_2 = """
from typing import Dict, Any, Optional

def track_learning_progress(student_id: str, course_id: str,
                             include_unit_breakdown: bool = False,
                             as_of_date: Optional[str] = None) -> Dict[str, Any]:
    \"\"\"
    Track a student's learning progress, optionally as of a historical date.

    Args:
        student_id (str): Unique student identifier.
        course_id (str): Course identifier.
        include_unit_breakdown (bool): If True, include per-unit scores.
        as_of_date (Optional[str]): ISO date string (YYYY-MM-DD). Returns progress as of that date.

    Returns:
        Dict[str, Any]: progress data, optionally with units and snapshot_date.
    \"\"\"
    progress_db = {
        ("S1001", "CS101"): 0.75,
        ("S1002", "CS101"): 0.50,
    }
    unit_db = {("S1001", "CS101"): {"unit1": 1.0, "unit2": 0.5}}
    progress = progress_db.get((student_id, course_id), 0.0)
    if as_of_date:
        progress = max(0.0, progress - 0.1)  # Simulate historical lookback
    result = {"student_id": student_id, "course_id": course_id, "progress": progress}
    if include_unit_breakdown:
        result["units"] = unit_db.get((student_id, course_id), {})
    if as_of_date:
        result["snapshot_date"] = as_of_date
    return result
"""

SKILL_CODE_4_1_V1_3 = """
from typing import Dict, Any, Optional

def track_learning_progress(student_id: str, course_id: str,
                             include_unit_breakdown: bool = False,
                             as_of_date: Optional[str] = None,
                             return_percentile: bool = False) -> Dict[str, Any]:
    \"\"\"
    Track learning progress with optional class percentile comparison.

    Args:
        student_id (str): Unique student identifier.
        course_id (str): Course identifier.
        include_unit_breakdown (bool): If True, include per-unit scores.
        as_of_date (Optional[str]): ISO date for historical snapshot.
        return_percentile (bool): If True, include progress compared to classmates.

    Returns:
        Dict[str, Any]: progress data; optionally includes units, snapshot_date, percentile.
    \"\"\"
    progress_db = {("S1001", "CS101"): 0.75, ("S1002", "CS101"): 0.50}
    unit_db = {("S1001", "CS101"): {"unit1": 1.0, "unit2": 0.5}}
    class_avg = {"CS101": 0.62}

    progress = progress_db.get((student_id, course_id), 0.0)
    if as_of_date:
        progress = max(0.0, progress - 0.1)
    result = {"student_id": student_id, "course_id": course_id, "progress": progress}
    if include_unit_breakdown:
        result["units"] = unit_db.get((student_id, course_id), {})
    if as_of_date:
        result["snapshot_date"] = as_of_date
    if return_percentile:
        avg = class_avg.get(course_id, 0.5)
        result["class_avg"] = avg
        result["above_avg"] = progress > avg
    return result
"""

SKILL_CODE_4_1_V1_4 = """
from typing import Dict, Any, Optional

def track_learning_progress(student_id: str, course_id: str,
                             include_unit_breakdown: bool = False,
                             as_of_date: Optional[str] = None,
                             return_percentile: bool = False,
                             include_recommendations: bool = False) -> Dict[str, Any]:
    \"\"\"
    Track learning progress and optionally suggest weak areas for review.

    Args:
        include_recommendations (bool): If True, adds remediation suggestions.
    \"\"\"
    progress_db = {("S1001", "CS101"): 0.75, ("S1002", "CS101"): 0.50}
    unit_db = {("S1001", "CS101"): {"unit1": 1.0, "unit2": 0.5}}
    class_avg = {"CS101": 0.62}
    weak_units = {
        ("S1001", "CS101"): ["unit2"],
        ("S1002", "CS101"): ["unit1", "unit2"],
    }

    progress = progress_db.get((student_id, course_id), 0.0)
    if as_of_date:
        progress = max(0.0, progress - 0.1)
    result = {"student_id": student_id, "course_id": course_id, "progress": progress}
    if include_unit_breakdown:
        result["units"] = unit_db.get((student_id, course_id), {})
    if as_of_date:
        result["snapshot_date"] = as_of_date
    if return_percentile:
        avg = class_avg.get(course_id, 0.5)
        result["class_avg"] = avg
        result["above_avg"] = progress > avg
    if include_recommendations:
        result["weak_areas"] = weak_units.get((student_id, course_id), [])
    return result
"""

SKILL_CODE_4_1_V1_5 = """
from typing import Dict, Any, Optional, Literal

def track_learning_progress(student_id: str, course_id: str,
                             include_unit_breakdown: bool = False,
                             as_of_date: Optional[str] = None,
                             return_percentile: bool = False,
                             include_recommendations: bool = False,
                             output_format: Literal["dict", "json"] = "dict") -> Any:
    \"\"\"
    Track learning progress with configurable output format.

    Args:
        output_format (Literal["dict", "json"]): Return as dict or JSON string. Default 'dict'.
    \"\"\"
    import json
    progress_db = {("S1001", "CS101"): 0.75, ("S1002", "CS101"): 0.50}
    unit_db = {("S1001", "CS101"): {"unit1": 1.0, "unit2": 0.5}}
    class_avg = {"CS101": 0.62}
    weak_units = {("S1001", "CS101"): ["unit2"]}

    progress = progress_db.get((student_id, course_id), 0.0)
    if as_of_date:
        progress = max(0.0, progress - 0.1)
    result = {"student_id": student_id, "course_id": course_id, "progress": progress}
    if include_unit_breakdown:
        result["units"] = unit_db.get((student_id, course_id), {})
    if as_of_date:
        result["snapshot_date"] = as_of_date
    if return_percentile:
        avg = class_avg.get(course_id, 0.5)
        result["class_avg"] = avg
        result["above_avg"] = progress > avg
    if include_recommendations:
        result["weak_areas"] = weak_units.get((student_id, course_id), [])

    if output_format == "json":
        return json.dumps(result)
    return result
"""

# Test cases: each version appends tests to the suite
SKILL_TEST_CODE_4_1_V1_0 = """
def test_track_basic_exists():
    result = track_learning_progress("S1001", "CS101")
    assert "progress" in result
    assert 0.0 <= result["progress"] <= 1.0

def test_track_basic_unknown_student():
    result = track_learning_progress("S9999", "CS999")
    assert result["progress"] == 0.0
"""

SKILL_TEST_CODE_4_1_V1_1 = """
def test_track_unit_breakdown_included():
    result = track_learning_progress("S1001", "CS101", include_unit_breakdown=True)
    assert "units" in result
    assert isinstance(result["units"], dict)

def test_track_no_unit_breakdown_by_default():
    result = track_learning_progress("S1001", "CS101")
    assert "units" not in result
"""

SKILL_TEST_CODE_4_1_V1_2 = """
def test_track_as_of_date():
    result = track_learning_progress("S1001", "CS101", as_of_date="2024-01-01")
    assert "snapshot_date" in result
    assert result["snapshot_date"] == "2024-01-01"

def test_track_no_snapshot_by_default():
    result = track_learning_progress("S1001", "CS101")
    assert "snapshot_date" not in result
"""

SKILL_TEST_CODE_4_1_V1_3 = """
def test_track_percentile_above_avg():
    result = track_learning_progress("S1001", "CS101", return_percentile=True)
    assert "above_avg" in result
    assert "class_avg" in result

def test_track_no_percentile_by_default():
    result = track_learning_progress("S1001", "CS101")
    assert "percentile" not in result and "above_avg" not in result
"""

SKILL_TEST_CODE_4_1_V1_4 = """
def test_track_recommendations_included():
    result = track_learning_progress("S1001", "CS101", include_recommendations=True)
    assert "weak_areas" in result
    assert isinstance(result["weak_areas"], list)

def test_track_no_recommendations_by_default():
    result = track_learning_progress("S1001", "CS101")
    assert "weak_areas" not in result
"""

SKILL_TEST_CODE_4_1_V1_5 = """
import json

def test_track_json_format():
    result = track_learning_progress("S1001", "CS101", output_format="json")
    assert isinstance(result, str)
    data = json.loads(result)
    assert "progress" in data

def test_track_dict_format_default():
    result = track_learning_progress("S1001", "CS101")
    assert isinstance(result, dict)
"""

# 5 Traces, one per minor increment
TRACE_DIM_4_1_STEP1 = AgentTrace(
    query="Track learning progress for student S1001 in CS101.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="Basic progress tracking for S1001.",
            code_block="result = track_learning_progress('S1001', 'CS101')\nprint(result)",
            tool_output="{'student_id': 'S1001', 'course_id': 'CS101', 'progress': 0.75}",
            status="success"
        )
    ],
    final_answer="S1001 is 75% through CS101.",
    involved_skills=["track_learning_progress"]
)

TRACE_DIM_4_1_STEP2 = AgentTrace(
    query="Show S1001's progress in CS101 with breakdown by individual units.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I need unit-level breakdown. The skill doesn't have that parameter yet—I'll propose adding include_unit_breakdown=True as a new optional parameter.",
            code_block="result = track_learning_progress('S1001', 'CS101', include_unit_breakdown=True)\nprint(result)",
            tool_output="{'student_id': 'S1001', 'course_id': 'CS101', 'progress': 0.75, 'units': {'unit1': 1.0, 'unit2': 0.5}}",
            status="success"
        )
    ],
    final_answer="S1001 has completed unit1 fully and unit2 halfway.",
    involved_skills=["track_learning_progress"]
)

TRACE_DIM_4_1_STEP3 = AgentTrace(
    query="Show what S1001's progress looked like in CS101 as of 2024-01-01.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I need historical snapshot. Adding as_of_date parameter.",
            code_block="result = track_learning_progress('S1001', 'CS101', as_of_date='2024-01-01')\nprint(result)",
            tool_output="{'student_id': 'S1001', 'course_id': 'CS101', 'progress': 0.65, 'snapshot_date': '2024-01-01'}",
            status="success"
        )
    ],
    final_answer="As of 2024-01-01, S1001 was at 65% progress.",
    involved_skills=["track_learning_progress"]
)

TRACE_DIM_4_1_STEP4 = AgentTrace(
    query="How does S1001's CS101 progress compare to the class average?",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="Adding return_percentile parameter to include class comparison.",
            code_block="result = track_learning_progress('S1001', 'CS101', return_percentile=True)\nprint(result)",
            tool_output="{'student_id': 'S1001', 'course_id': 'CS101', 'progress': 0.75, 'class_avg': 0.62, 'above_avg': True}",
            status="success"
        )
    ],
    final_answer="S1001 is above the class average (75% vs 62%).",
    involved_skills=["track_learning_progress"]
)

TRACE_DIM_4_1_STEP5 = AgentTrace(
    query="Show S1001's CS101 progress with recommendations for areas needing improvement.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="Adding include_recommendations flag to get weak area suggestions.",
            code_block="result = track_learning_progress('S1001', 'CS101', include_recommendations=True)\nprint(result)",
            tool_output="{'student_id': 'S1001', 'course_id': 'CS101', 'progress': 0.75, 'weak_areas': ['unit2']}",
            status="success"
        )
    ],
    final_answer="S1001 should review unit2 to improve their CS101 score.",
    involved_skills=["track_learning_progress"]
)


# ==========================================
# 4.2: 跨版本数据结构缺失的自愈（Legacy Schema Healing）
# 场景：v0.1 遗留 Skill 没有 interface_schema 字段 (NULL)
# 现代 Gardener v1.0 触碰到这批旧数据需要升级重构
# ==========================================

SKILL_CODE_4_2_LEGACY = """
# Legacy v0.1 skill - predates interface schema documentation
# WARNING: No type hints, no docstring, no interface_schema recorded in DB
def assign_homework(cid, sid, hw):
    assignments = []
    if cid and sid and hw:
        assignments.append({'c': cid, 's': sid, 'h': hw, 'done': False})
    return assignments
"""

SKILL_CODE_4_2_HEALED = """
from typing import Dict, Any, List

def assign_homework(course_id: str, student_id: str, homework_title: str,
                    due_date: str = "TBD") -> List[Dict[str, Any]]:
    \"\"\"
    Assign a homework task to a student in a course.

    (Previously legacy v0.1 'assign_homework' with no type hints or schema.
     This v2.0 adds full interface contract, type annotations, and explicit fields.)

    Args:
        course_id (str): Course identifier.
        student_id (str): Student identifier.
        homework_title (str): Title/description of the homework.
        due_date (str): Due date string, defaults to 'TBD'.

    Returns:
        List[Dict[str, Any]]: List with one assignment record:
            {'course_id': str, 'student_id': str, 'title': str,
             'due_date': str, 'completed': bool}

    Examples:
        >>> result = assign_homework("CS101", "S1001", "Chapter 3 Problems")
        >>> len(result) == 1
        True
        >>> result[0]['completed'] == False
        True
    \"\"\"
    if not (course_id and student_id and homework_title):
        return []
    return [{
        "course_id": course_id,
        "student_id": student_id,
        "title": homework_title,
        "due_date": due_date,
        "completed": False
    }]
"""

TRACE_DIM_4_2 = AgentTrace(
    query="Assign Chapter 3 Problems homework to student S1001 in CS101 due next Friday.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll use assign_homework from the legacy skill. However, the DB record shows interface_schema = NULL, meaning this was created before schema tracking was established.",
            code_block="""
# Attempt to use legacy skill
from edu_skills_legacy import assign_homework

try:
    result = assign_homework("CS101", "S1001", "Chapter 3 Problems", due_date="2024-12-13")
    print(result)
except TypeError as e:
    print(f"Interface error: {e}")
""",
            tool_output="TypeError: assign_homework() got an unexpected keyword argument 'due_date'",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="The legacy skill has old positional args. Let me inspect the source to understand the actual signature and reconstruct a modern interface schema.",
            code_block="""
import inspect
from edu_skills_legacy import assign_homework
print(inspect.getsource(assign_homework))
""",
            tool_output="""def assign_homework(cid, sid, hw):
    assignments = []
    if cid and sid and hw:
        assignments.append({'c': cid, 's': sid, 'h': hw, 'done': False})
    return assignments""",
            status="success"
        ),
        TraceStep(
            step_id="step_3",
            thought="Legacy args are: cid, sid, hw. Return is list of dicts with abbreviated keys 'c', 's', 'h', 'done'. I'll call it with positional args as a workaround, but Gardener must document the new schema on upgrade.",
            code_block="""
result = assign_homework("CS101", "S1001", "Chapter 3 Problems")
print("Legacy result:", result)
""",
            tool_output="Legacy result: [{'c': 'CS101', 's': 'S1001', 'h': 'Chapter 3 Problems', 'done': False}]",
            status="success"
        )
    ],
    final_answer="Homework assigned using legacy interface. Upgrade required to modernize schema.",
    involved_skills=["assign_homework"]
)


# ==========================================
# 4.3: 带伤版本的回归阻截（Strict Regression Prevention）
# 场景：Skill_A (v1.1) 最后一次 TestReport 是 is_passed=False（带伤合入版本）
# 新 Trace 试图在有病基线上叠加新需求升级到 v1.2
# Reviewer 必须先迫使修好旧 bug 才能发版
# ==========================================

SKILL_CODE_4_3_V1_1_BUGGY = """
from typing import List, Dict, Any

def calculate_gpa(grade_records: List[Dict[str, Any]]) -> float:
    \"\"\"
    Calculate GPA from a list of grade records.

    Args:
        grade_records (List[Dict[str, Any]]): List of records containing
            'course_id' (str), 'credits' (int), 'letter_grade' (str).

    Returns:
        float: Grade Point Average on a 4.0 scale.

    Examples:
        >>> records = [{'course_id': 'CS101', 'credits': 3, 'letter_grade': 'A'}]
        >>> calculate_gpa(records)
        4.0
    \"\"\"
    grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    total_points = 0.0
    total_credits = 0
    for record in grade_records:
        grade = record.get('letter_grade', 'F')
        credits = record.get('credits', 0)
        # BUG: Missing grade_points lookup, using raw grade string
        total_points += float(grade)  # BUG: 'A' can't be converted to float!
        total_credits += credits
    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)
"""

SKILL_CODE_4_3_V1_2_STILL_BUGGY = """
from typing import List, Dict, Any

def calculate_gpa(grade_records: List[Dict[str, Any]],
                  grading_scale: str = "4.0") -> float:
    \"\"\"
    Calculate GPA (v1.2: added grading_scale parameter), but base bug still present.
    \"\"\"
    grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    total_points = 0.0
    total_credits = 0
    for record in grade_records:
        grade = record.get('letter_grade', 'F')
        credits = record.get('credits', 0)
        total_points += float(grade)  # BUG STILL PRESENT
        total_credits += credits
    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)
"""

SKILL_CODE_4_3_V1_2_FIXED = """
from typing import List, Dict, Any

def calculate_gpa(grade_records: List[Dict[str, Any]],
                  grading_scale: str = "4.0") -> float:
    \"\"\"
    Calculate GPA with proper grade point lookup and optional scale parameter.

    Args:
        grade_records (List[Dict[str, Any]]): List of records with 'credits' and 'letter_grade'.
        grading_scale (str): GPA scale, currently only '4.0' is supported.

    Returns:
        float: Weighted GPA on specified scale.
    \"\"\"
    grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    total_points = 0.0
    total_credits = 0
    for record in grade_records:
        grade = record.get('letter_grade', 'F')
        credits = record.get('credits', 0)
        points = grade_points.get(grade.upper(), 0.0)  # BUG FIXED
        total_points += points * credits
        total_credits += credits
    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)
"""

# Locked test from v1.1 era that catches the bug
SKILL_TEST_CODE_4_3_LOCKED = """
def test_calculate_gpa_all_a():
    records = [
        {'course_id': 'CS101', 'credits': 3, 'letter_grade': 'A'},
        {'course_id': 'MATH101', 'credits': 3, 'letter_grade': 'A'},
    ]
    result = calculate_gpa(records)
    assert result == 4.0, f"Expected 4.0, got {result}"

def test_calculate_gpa_mixed():
    records = [
        {'course_id': 'CS101', 'credits': 3, 'letter_grade': 'A'},
        {'course_id': 'ENG101', 'credits': 3, 'letter_grade': 'B'},
    ]
    result = calculate_gpa(records)
    assert result == 3.5, f"Expected 3.5, got {result}"

def test_calculate_gpa_empty():
    assert calculate_gpa([]) == 0.0
"""

TRACE_DIM_4_3 = AgentTrace(
    query="Calculate GPA for student S1001. Also add support for a grading_scale parameter for future use.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll call calculate_gpa for S1001's grade records.",
            code_block="""
from edu_skills import calculate_gpa

records = [
    {'course_id': 'CS101', 'credits': 3, 'letter_grade': 'A'},
    {'course_id': 'MATH101', 'credits': 3, 'letter_grade': 'B'},
]

try:
    gpa = calculate_gpa(records)
    print(f"GPA: {gpa}")
except ValueError as e:
    print(f"Error: {e}")
""",
            tool_output="Error: could not convert string to float: 'A'",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="There's a bug in v1.1: it tries to convert the letter grade to float directly instead of mapping through grade_points. I need to fix the grade_points lookup AND add the grading_scale parameter.",
            code_block="""
# Verify the fix logic
grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
records = [
    {'credits': 3, 'letter_grade': 'A'},
    {'credits': 3, 'letter_grade': 'B'},
]
total = sum(grade_points[r['letter_grade']] * r['credits'] for r in records)
total_credits = sum(r['credits'] for r in records)
gpa = total / total_credits
print(f"Fixed GPA: {gpa}")
""",
            tool_output="Fixed GPA: 3.5",
            status="success"
        )
    ],
    final_answer="GPA calculation bug fixed. S1001 has a 3.5 GPA. grading_scale parameter added for v1.2.",
    involved_skills=["calculate_gpa"]
)
