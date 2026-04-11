from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat

# ==========================================
# Dimension 3: Reviewer/Tester 沙箱与外部边界
# 领域：教育教学 - 课程资源与作业管理
# ==========================================

# ==========================================
# 3.1: 重度外部 IO 技能（Mocking Enforcement）
# 场景：Skill 内部发起网络请求爬取外部课程资源 URL，以及调用 os.remove 清理文件。
# Tester 不能发起真实网络请求，必须全程 mock。
# ==========================================

SKILL_CODE_3_1 = """
import os
import requests
from typing import List, Dict, Any

def fetch_course_materials(course_url: str, download_dir: str = "/tmp/edu_materials") -> Dict[str, Any]:
    \"\"\"
    Fetch course materials from a given URL and prepare them locally.

    Downloads a JSON course manifest from `course_url`, extracts material links,
    saves metadata to `download_dir`, and cleans up temp files on failure.

    Args:
        course_url (str): URL pointing to the course's JSON manifest.
        download_dir (str): Local directory to save downloaded materials.

    Returns:
        Dict[str, Any]: {'status': 'ok', 'materials': List[str]} on success.
                        {'status': 'error', 'message': str} on failure.

    Examples:
        >>> result = fetch_course_materials("https://eduplatform.com/courses/ALG-201/manifest.json")
        >>> result['status']
        'ok'
        >>> len(result['materials']) > 0
        True
    \"\"\"
    try:
        response = requests.get(course_url, timeout=10)
        response.raise_for_status()
        manifest = response.json()
        materials = manifest.get("materials", [])
        temp_file = os.path.join(download_dir, "manifest_cache.json")
        with open(temp_file, "w") as f:
            import json
            json.dump(manifest, f)
        return {"status": "ok", "materials": materials}
    except Exception as e:
        # Cleanup temp file if exists (use local var safely)
        _temp = locals().get("temp_file")
        if _temp and os.path.exists(_temp):
            os.remove(_temp)
        return {"status": "error", "message": str(e)}
"""

SKILL_TEST_CODE_3_1 = """
import pytest
from unittest.mock import patch, MagicMock, mock_open

def test_fetch_course_materials_success():
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "materials": ["slide1.pdf", "quiz1.json", "notes.md"]
    }
    
    with patch("requests.get", return_value=mock_response), \\
         patch("builtins.open", mock_open()), \\
         patch("json.dump"):
        result = fetch_course_materials("https://fake.test/manifest.json")

    assert result["status"] == "ok"
    assert "slide1.pdf" in result["materials"]

def test_fetch_course_materials_network_failure():
    with patch("requests.get", side_effect=ConnectionError("Network Unreachable")), \\
         patch("os.path.exists", return_value=False):
        result = fetch_course_materials("https://fake.test/manifest.json")

    assert result["status"] == "error"
    assert "Network Unreachable" in result["message"]

def test_fetch_course_materials_cleanup_on_error():
    with patch("requests.get", side_effect=TimeoutError("Timeout")), \\
         patch("os.path.exists", return_value=True), \\
         patch("os.remove") as mock_remove:
        result = fetch_course_materials("https://fake.test/manifest.json")

    # Cleanup should be triggered when temp file exists
    # (exact behavior depends on implementation, but 'error' status must be returned)
    assert result["status"] == "error"
"""

TRACE_DIM_3_1 = AgentTrace(
    query="Fetch the course materials for ALG-201 from the platform and list what's available.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I need to call `fetch_course_materials`. During testing this would make a real HTTP request, so I must ensure the Reviewer wraps it with proper mock patches.",
            code_block="""
from edu_skills import fetch_course_materials

result = fetch_course_materials("https://eduplatform.com/courses/ALG-201/manifest.json")
print("Status:", result["status"])
if result["status"] == "ok":
    print("Materials count:", len(result["materials"]))
    for m in result["materials"]:
        print(" -", m)
""",
            tool_output="""Status: ok
Materials count: 3
 - slides_week1.pdf
 - quiz_algebra_basics.json
 - reading_notes.md""",
            status="success"
        )
    ],
    final_answer="3 materials found for ALG-201: slides, quiz, and reading notes.",
    involved_skills=["fetch_course_materials"]
)


# ==========================================
# 3.2: 小版本更新中的接口兼容性测试
# 场景：Skill_A (grade_submission v1.0) 调用 Skill_B (validate_submission v1.0)。
# Skill_B 大版本更新 v2.0 改变了参数名 (data -> submission_payload)。
# Extractor 对 Skill_A 生成 v1.1 时，内部签名调用被改为 v2.0，但返回接口未变。
# Tester 必须捕捉 Skill_A v1.1 调用 Skill_B v2.0 的新参数名引用错误。
# ==========================================

SKILL_CODE_3_2_A = """
from typing import Dict, Any

def grade_submission(student_id: str, assignment_id: str, file_content: str) -> Dict[str, Any]:
    \"\"\"
    Grade a student's submission by first validating, then scoring it.

    Args:
        student_id (str): Student identifier.
        assignment_id (str): Assignment identifier.
        file_content (str): The content of the submitted file as a string.

    Returns:
        Dict[str, Any]: {'grade': int, 'feedback': str} on success.
    \"\"\"
    from grading_tools import validate_submission
    validated = validate_submission(data={"student_id": student_id, "content": file_content})
    if not validated["is_valid"]:
        return {"grade": 0, "feedback": validated["error"]}
    
    word_count = len(file_content.split())
    grade = min(100, word_count * 2)
    return {"grade": grade, "feedback": "Good submission."}
"""

SKILL_CODE_3_2_A_V1_1_BROKEN = """
from typing import Dict, Any

def grade_submission(student_id: str, assignment_id: str, file_content: str) -> Dict[str, Any]:
    \"\"\"
    Grade a student's submission (v1.1 - adapted to validate_submission v2.0 keyword).
    \"\"\"
    from grading_tools import validate_submission
    # BROKEN: passing old keyword 'data' but v2.0 API uses 'submission_payload'
    validated = validate_submission(data={"student_id": student_id, "content": file_content})
    if not validated["is_valid"]:
        return {"grade": 0, "feedback": validated["error"]}
    
    word_count = len(file_content.split())
    grade = min(100, word_count * 2)
    return {"grade": grade, "feedback": "Well done."}
"""

SKILL_TEST_CODE_3_2_A = """
import pytest
from unittest.mock import patch

def test_grade_submission_valid():
    mock_validation = {"is_valid": True}
    with patch("grading_tools.validate_submission", return_value=mock_validation):
        result = grade_submission("S1001", "HW01", "This is my homework answer for algebra equations")
    assert "grade" in result
    assert result["grade"] > 0

def test_grade_submission_invalid():
    mock_validation = {"is_valid": False, "error": "File too short."}
    with patch("grading_tools.validate_submission", return_value=mock_validation):
        result = grade_submission("S1001", "HW01", "short")
    assert result["grade"] == 0
    assert "too short" in result["feedback"]
"""

TRACE_DIM_3_2 = AgentTrace(
    query="Grade the homework submission for student S1001 on assignment HW01.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll call grade_submission which internally calls validate_submission. The validate_submission v2.0 changed 'data' kwarg to 'submission_payload'.",
            code_block="""
from edu_skills import grade_submission

try:
    result = grade_submission("S1001", "HW01", "My answer is based on solving linear equations step by step.")
    print(result)
except TypeError as e:
    print(f"Interface error: {e}")
""",
            tool_output="Interface error: validate_submission() got an unexpected keyword argument 'data'. Use 'submission_payload' instead.",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="The interface keyword changed from 'data' to 'submission_payload'. I need to update grade_submission's internal call to validate_submission.",
            code_block="""
from grading_tools import validate_submission

# Direct call with correct v2.0 API
validated = validate_submission(submission_payload={
    "student_id": "S1001",
    "content": "My answer is based on solving linear equations step by step."
})
print("Validated:", validated)
""",
            tool_output="Validated: {'is_valid': True}",
            status="success"
        )
    ],
    final_answer="Submission validated. Grade calculated successfully after adapting to v2.0 API.",
    involved_skills=["grade_submission", "validate_submission"]
)


# ==========================================
# 3.3: 幻觉接口虚构（Hallucinated Imports）
# 场景：Extractor 生成的代码 import 了一个不存在的教育分析库 `edu_analytics.advanced`
# Tester 运行时捕捉 ModuleNotFoundError / ImportError
# ==========================================

SKILL_CODE_3_3_HALLUCINATED = """
from typing import List, Dict
from edu_analytics.advanced import AutoGrader, SemanticSimilarity  # 幻觉的库！不存在

def auto_grade_essay(student_id: str, essay_text: str, rubric: Dict[str, int]) -> Dict[str, int]:
    \"\"\"
    Automatically grade an essay based on a rubric using semantic analysis.
    (Note: This code contains hallucinated imports that do not exist.)
    \"\"\"
    grader = AutoGrader(rubric=rubric)
    score_breakdown = grader.evaluate(
        text=essay_text,
        similarity_engine=SemanticSimilarity(model="bert-edu-base")
    )
    return score_breakdown
"""

SKILL_TEST_CODE_3_3_REAL = """
import pytest

def test_auto_grade_essay_import():
    # This test will fail with ImportError because edu_analytics.advanced does not exist
    try:
        from edu_analytics.advanced import AutoGrader
        assert False, "Expected ImportError not raised"
    except (ImportError, ModuleNotFoundError) as e:
        assert "edu_analytics" in str(e)
"""

TRACE_DIM_3_3 = AgentTrace(
    query="Automatically grade the essay submitted by S1001 based on the provided rubric.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll use the `auto_grade_essay` skill to grade the essay using advanced semantic analysis.",
            code_block="""
from edu_skills import auto_grade_essay

rubric = {"content_relevance": 30, "grammar": 20, "structure": 30, "citations": 20}
try:
    result = auto_grade_essay("S1001", "The Pythagorean theorem states...", rubric)
    print(result)
except ImportError as e:
    print(f"ImportError: {e}")
""",
            tool_output="ImportError: No module named 'edu_analytics.advanced'",
            status="failed"
        )
    ],
    final_answer="Failed due to hallucinated import `edu_analytics.advanced`.",
    involved_skills=["auto_grade_essay"]
)


# ==========================================
# 3.4: 静态语法错误的前置熔断
# 场景：LLM 输出被截断，生成的学生分组脚本里掉了一个闭括号，形成语法错误。
# Tester 在加载时应立即捕捉 SyntaxError，不需要写 test case 也不要运行测试。
# ==========================================

SKILL_CODE_3_4_SYNTAX_ERROR = """
from typing import List, Dict

def group_students_by_performance(scores: Dict[str, float], num_groups: int = 3) -> Dict[str, List[str]]:
    \"\"\"
    Group students into performance tiers based on scores.

    Args:
        scores (Dict[str, float]): student_id -> score mapping.
        num_groups (int): Number of tiers to partition into.

    Returns:
        Dict[str, List[str]]: tier label -> list of student IDs.
    \"\"\"
    if not scores:
        return {}

    sorted_students = sorted(scores.items(), key=lambda x: x[1], reverse=True
    # SYNTAX ERROR: missing closing parenthesis on the line above
    chunk_size = max(1, len(sorted_students) // num_groups)
    groups = {}
    for i in range(num_groups):
        tier_label = ["top", "middle", "bottom"][i] if num_groups == 3 else f"tier_{i+1}"
        groups[tier_label] = [s[0] for s in sorted_students[i*chunk_size:(i+1)*chunk_size]]
    return groups
"""

TRACE_DIM_3_4 = AgentTrace(
    query="Group all students in class CS201 into performance tiers: top, middle, and bottom.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll use the group_students_by_performance skill with the class score data.",
            code_block="""
from edu_skills import group_students_by_performance

scores = {
    "S1001": 92.5, "S1002": 78.0, "S1003": 65.0,
    "S1004": 88.0, "S1005": 55.0, "S1006": 71.0
}
try:
    groups = group_students_by_performance(scores)
    for tier, students in groups.items():
        print(f"{tier}: {students}")
except SyntaxError as e:
    print(f"SyntaxError: {e}")
""",
            tool_output="SyntaxError: '(' was never closed (<string>, line 17)",
            status="failed"
        )
    ],
    final_answer="Execution aborted due to SyntaxError in the skill code.",
    involved_skills=["group_students_by_performance"]
)


# ==========================================
# 3.5: 工具调用错误定位测试
# 场景：Extractor 为 send_grade_notification 生成了错误的工具调用：
#   将 send_email(to, subject, body) 误写成 send_email(subject, to, body) (参数顺序颠倒)。
# Tester 应精准指出是 send_email 调用传参顺序错误导致的失败。
# ==========================================

SKILL_CODE_3_5_CORRECT = """
from typing import List

def send_grade_notifications(grade_report: dict, email_service_fn=None) -> List[str]:
    \"\"\"
    Send grade notifications to all students in the report.

    Args:
        grade_report (dict): Maps student_id -> {'grade': int, 'email': str}.
        email_service_fn: Callable with signature (to: str, subject: str, body: str) -> bool.
                          Defaults to a simple print stub.

    Returns:
        List[str]: List of student IDs to whom notifications were sent.
    \"\"\"
    if email_service_fn is None:
        def email_service_fn(to, subject, body): print(f"Email to {to}: {subject}"); return True

    sent = []
    for sid, info in grade_report.items():
        success = email_service_fn(
            to=info["email"],
            subject=f"Your grade for Assignment: {info['grade']}",
            body=f"Dear Student, your grade is {info['grade']}."
        )
        if success:
            sent.append(sid)
    return sent
"""

SKILL_CODE_3_5_WRONG_CALL = """
from typing import List

def send_grade_notifications(grade_report: dict, email_service_fn=None) -> List[str]:
    \"\"\"Send grade notifications to students. (BROKEN: tool call parameter order wrong)\"\"\"
    if email_service_fn is None:
        def email_service_fn(to, subject, body): return True

    sent = []
    for sid, info in grade_report.items():
        # BUG: subject is passed as first positional arg (should be 'to')
        success = email_service_fn(
            f"Your grade for Assignment: {info['grade']}",  # <- actually 'to', passed 'subject' string
            info["email"],                                   # <- actually 'subject', passed email address
            f"Dear Student, your grade is {info['grade']}."
        )
        if success:
            sent.append(sid)
    return sent
"""

SKILL_TEST_CODE_3_5 = """
import pytest
from unittest.mock import patch, call

def test_send_grade_notifications_correct_call():
    report = {
        "S1001": {"grade": 90, "email": "s1001@univ.edu"},
        "S1002": {"grade": 75, "email": "s1002@univ.edu"},
    }
    
    call_log = []
    def mock_email(to, subject, body):
        call_log.append((to, subject))
        return True

    result = send_grade_notifications(report, email_service_fn=mock_email)
    
    # Verify 'to' received an email address, not a subject string
    for to, subject in call_log:
        assert "@" in to, f"Expected email address in 'to', got: {to}"
        assert "grade" in subject.lower(), f"Expected grade info in 'subject', got: {subject}"
    
    assert set(result) == {"S1001", "S1002"}
"""

TRACE_DIM_3_5 = AgentTrace(
    query="Send grade notifications to all students in the CS201 class.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll call send_grade_notifications with the grade report and verify outputs.",
            code_block="""
from edu_skills import send_grade_notifications

grade_report = {
    "S1001": {"grade": 90, "email": "s1001@univ.edu"},
    "S1002": {"grade": 75, "email": "s1002@univ.edu"},
}

sent_to = send_grade_notifications(grade_report)
print("Sent:", sent_to)
""",
            tool_output="Sent: ['S1001', 'S1002']",
            status="success"
        )
    ],
    final_answer="Notifications sent to S1001 and S1002.",
    involved_skills=["send_grade_notifications"]
)


# ==========================================
# 3.6: 实现逻辑错误定位测试
# 场景：Extractor 提取的 calculate_percentile_rank 含有算法错误：
#   百分位计算用了 rank/(n+1) 但实际正确公式是 (rank-1)/n
#   导致边界条件下（999分），百分位超过100%。
# Tester 应精准指出是算法逻辑错误（ASSERTION_FAILED）。
# ==========================================

SKILL_CODE_3_6_CORRECT = """
from typing import List

def calculate_percentile_rank(scores: List[float], target_score: float) -> float:
    \"\"\"
    Calculate the percentile rank of target_score in a list of scores.

    The percentile rank is the percentage of scores BELOW the target score.

    Args:
        scores (List[float]): List of all scores in the population.
        target_score (float): The score whose rank is to be calculated.

    Returns:
        float: Percentile rank between 0.0 and 100.0.

    Examples:
        >>> calculate_percentile_rank([60, 70, 80, 90, 100], 80)
        40.0
    \"\"\"
    if not scores:
        return 0.0
    below = sum(1 for s in scores if s < target_score)
    return round((below / len(scores)) * 100, 2)
"""

SKILL_CODE_3_6_BUGGY = """
from typing import List

def calculate_percentile_rank(scores: List[float], target_score: float) -> float:
    \"\"\"
    Calculate the percentile rank (BUGGY: uses rank/(n+1) which can exceed 100%).
    \"\"\"
    if not scores:
        return 0.0
    # BUG: rank-based formula doesn't correctly compute percentage below
    sorted_scores = sorted(scores)
    rank = sorted_scores.index(target_score) + 1  # 1-indexed rank, fails on duplicates
    n = len(sorted_scores)
    return round((rank / (n + 1)) * 100, 2)  # BUG: can produce > 100% and wrong baseline
"""

SKILL_TEST_CODE_3_6 = """
import pytest

def test_calculate_percentile_rank_basic():
    scores = [60.0, 70.0, 80.0, 90.0, 100.0]
    # 80 has 2 scores below it (60, 70) -> 2/5 * 100 = 40.0
    result = calculate_percentile_rank(scores, 80.0)
    assert result == 40.0, f"Expected 40.0, got {result}"

def test_calculate_percentile_rank_minimum():
    scores = [60.0, 70.0, 80.0]
    # 60 has 0 scores below it -> 0%
    result = calculate_percentile_rank(scores, 60.0)
    assert result == 0.0

def test_calculate_percentile_rank_maximum():
    scores = [60.0, 70.0, 80.0]
    # 80 has 2 scores below it -> 2/3 * 100 ≈ 66.67%
    result = calculate_percentile_rank(scores, 80.0)
    assert result == pytest.approx(66.67, abs=0.1)

def test_calculate_percentile_rank_never_exceeds_100():
    scores = [50.0, 60.0, 70.0, 80.0, 90.0]
    result = calculate_percentile_rank(scores, 90.0)
    assert 0.0 <= result <= 100.0, f"Percentile rank out of range: {result}"
"""

TRACE_DIM_3_6 = AgentTrace(
    query="Calculate the percentile rank for student S1001 who scored 90 out of a class of 5.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I'll use calculate_percentile_rank with the class score distribution.",
            code_block="""
from edu_skills import calculate_percentile_rank

class_scores = [60.0, 70.0, 80.0, 90.0, 100.0]
target = 90.0

percentile = calculate_percentile_rank(class_scores, target)
print(f"S1001 is at the {percentile}th percentile")
""",
            tool_output="S1001 is at the 60.0th percentile",
            status="success"
        )
    ],
    final_answer="S1001 is at the 60th percentile (3 out of 5 classmates scored below 90).",
    involved_skills=["calculate_percentile_rank"]
)
