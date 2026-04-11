from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat

# ==========================================
# Dimension 1.1: 严格的向下兼容验证 (Minor Update)
# 领域：教育教学 - 学生成绩查询
# 场景：现有技能只能返回文本格式的成绩单。用户期望以 JSON 格式便于系统 间数据交互。
# Trace：Agent 编写 CodeAct Python 代码调用旧接口，利用正则解析结果并转换为 JSON。
# Extractor：建议增加 `output_format: str = "text"` 参数实现向下兼容的 多态。
# ==========================================

SKILL_CODE_1_1_V1_0 = """
import re

def fetch_student_transcript(student_id: str) -> str:
    \"\"\"
    Fetch the academic transcript for a given student ID as a plain text report.
    
    Args:
        student_id (str): The unique identifier for the student (e.g., 'S1001').
        
    Returns:
        str: A string report of the student's grades.
    \"\"\"
    # Mocking database access
    db = {
        "S1001": {"Math": 95, "Physics": 88, "Literature": 92},
        "S1002": {"Math": 75, "Physics": 80, "Literature": 85}
    }
    
    if student_id not in db:
        raise ValueError(f"Student ID {student_id} not found in records.")
        
    record = db[student_id]
    report_lines = [f"Transcript for {student_id}:"]
    for subject, score in record.items():
        report_lines.append(f"- {subject}: {score}")
        
    return "\\n".join(report_lines)
"""

SKILL_TEST_CODE_1_1_V1_0 = """
import pytest

def test_fetch_student_transcript_valid():
    result = fetch_student_transcript("S1001")
    assert "Transcript for S1001:" in result
    assert "Math: 95" in result

def test_fetch_student_transcript_invalid():
    with pytest.raises(ValueError):
        fetch_student_transcript("S9999")
"""

TRACE_DIM_1_1 = AgentTrace(
    query="Please retrieve the academic transcript for S1001 and format it strictly as a JSON object with subjects as keys and scores as integers.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I need to fetch the transcript using the existing tool which only provides a text report. Then I will write a custom python script to parse the output and convert it into a JSON string.",
            code_block="""
from core_skills import fetch_student_transcript
import json
import re

try:
    text_report = fetch_student_transcript("S1001")
    print("--- RAW OUTPUT ---")
    print(text_report)
    
    # Parsing the text report
    grades = {}
    for line in text_report.split('\\n')[1:]:
        match = re.match(r'-\\s+(\\w+):\\s+(\\d+)', line)
        if match:
            subject = match.group(1)
            score = int(match.group(2))
            grades[subject] = score
            
    print("--- JSON OUTPUT ---")
    print(json.dumps(grades, indent=2))
except Exception as e:
    print(f"Error: {e}")
""",
            tool_output="""--- RAW OUTPUT ---
Transcript for S1001:
- Math: 95
- Physics: 88
- Literature: 92
--- JSON OUTPUT ---
{
  "Math": 95,
  "Physics": 88,
  "Literature": 92
}""",
            status="success"
        )
    ],
    final_answer='{\\n  "Math": 95,\\n  "Physics": 88,\\n  "Literature": 92\\n}',
    involved_skills=["fetch_student_transcript"]
)


# ==========================================
# Dimension 1.2: 伪装成 Minor 的破坏性重构
# 领域：教育教学 - 班级成绩统计
# 场景：原技能计算全班成绩列表返回值为表示平均分的单精度浮点数 (float) 。
# Trace：用户不仅想看平均分，还想看中位数。Agent 用 Python 处理求出两者。
# Extractor (误判)：把返回值改成 `{"mean": float, "median": float}`，标记为 Minor。
# Tester (预期)：因为旧用例中测试代码使用类似 `assert round(calculate_class_stats([...]), 2) == 85.0`，因此必然因为类型变成了 dict 而引发类型 错误，成功捕捉破坏性更新。
# ==========================================

SKILL_CODE_1_2_V1_0 = """
from typing import List, Dict
import statistics

def calculate_class_stats(scores: List[float]) -> Dict[str, float]:
    \"\"\"
    Calculate class statistics (mean and median).

    v1.1: PSEUDO-MINOR update — silently changed return type from float to
    Dict[str, float] while appearing to just add median support.
    Old callers expecting a plain float will break.
    \"\"\"
    if not scores:
        return {"mean": 0.0, "median": 0.0}
    mean = round(sum(scores) / len(scores), 2)
    med = round(statistics.median(scores), 2)
    return {"mean": mean, "median": med}
"""

SKILL_TEST_CODE_1_2_V1_0 = """
import pytest

def test_calculate_class_stats_normal():
    # Directly uses float operations based on original float return type.
    avg_score = calculate_class_stats([80.0, 90.0, 100.0])
    assert avg_score == 90.0
    # Operations that assumes a float
    assert avg_score + 10.0 == 100.0

def test_calculate_class_stats_empty():
    assert calculate_class_stats([]) == 0.0
"""

TRACE_DIM_1_2 = AgentTrace(
    query="I have a list of test scores: [80.0, 90.0, 100.0, 75.0, 85.0]. I need both the average score and the median score.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="The system has `calculate_class_stats` which gives the average. But I also need the median. I will write a custom code to compute both.",
            code_block="""
import statistics

scores = [80.0, 90.0, 100.0, 75.0, 85.0]

# Compute mean using native function (or existing skill)
mean_score = sum(scores) / len(scores)

# Compute median
median_score = statistics.median(scores)

result = {
    "mean": round(mean_score, 2),
    "median": round(median_score, 2)
}
print(result)
""",
            tool_output="""{'mean': 86.0, 'median': 85.0}""",
            status="success"
        )
    ],
    final_answer="The average score is 86.0 and the median score is 85.0.",
    involved_skills=["calculate_class_stats"]
)


# ==========================================
# Dimension 1.3: 合理的大版本跃迁 (Major Update)
# 领域：教育教学 - 试卷生成系统
# 场景：v1 版本的技能 `generate_quiz_question` 只能按主题和难度同步生成一整块文本试题，由于 LLM token 耗时和字符串不可拆分带来极大不便。
# Trace：用户在一堂拥有 500 个学生的课程中需要组卷，不仅需要高并发批量 生成，而且要求每道题目都有分离的 "question", "options", "answer", "explanation" 字典结构。Agent 在 CodeAct 中进行了大规模自实现。
# Extractor：识别到结构不可弥补的巨大差异，建议升维至 V2.0，彻底改变函 数签名：抛弃原有的单文本返回，采用 Pydantic models 和异步架构或结构化生成。
# ==========================================

SKILL_CODE_1_3_V1_0 = """
from typing import Dict, Any

def generate_quiz_question(topic: str, difficulty: str) -> Dict[str, Any]:
    \"\"\"
    Generate a quiz question as a structured dict.

    v2.0: INCOMPATIBLE MAJOR change — changed return type from str to
    Dict[str, Any].  Old callers that expected a plain string will break
    (e.g. doing string-in-text membership checks).
    \"\"\"
    return {
        "question_text": f"What is the main concept of {topic}?",
        "options": ["A) Option A", "B) Option B", "C) Option C", "D) Option D"],
        "correct_option_index": 0,
        "explanation": f"Tests understanding of {topic} at {difficulty} level.",
        "difficulty": difficulty,
    }
"""

SKILL_TEST_CODE_1_3_V1_0 = """
def test_generate_quiz_question():
    text = generate_quiz_question("Photosynthesis", "easy")
    assert "Q: What is the main concept" in text
"""

TRACE_DIM_1_3 = AgentTrace(
    query="Generate 3 biology quiz questions for a high school midterm exam. Each question must be strictly structured in an array of dictionaries including 'question_text', 'options', 'correct_option_index', and 'explanation'.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="The `generate_quiz_question` tool returns a monolithic string which is hard to parse for this requirement (needs a structured dictionary). Considering I need 3 questions cleanly separated, I will completely bypass the regular tool's layout and just write a python script to simulate/structure the data directly.",
            code_block="""
import json

# Simulating advanced structured generation
questions = [
    {
        "question_text": "What is the primary function of mitochondria?",
        "options": ["Respiration", "Photosynthesis", "Digestion", "Reproduction"],
        "correct_option_index": 1,
        "explanation": "Mitochondria handles respiration but wait, chloroplasts do photosynthesis. Let's make the question about chloroplasts."
    },
    {
        "question_text": "Which organelle is considered the powerhouse of the cell?",
        "options": ["Nucleus", "Ribosome", "Mitochondria", "Golgi"],
        "correct_option_index": 2,
        "explanation": "Mitochondria generate most of the cell's supply of ATP."
    },
    {
        "question_text": "What is the basic unit of life?",
        "options": ["Tissue", "Cell", "Organ", "Organism"],
        "correct_option_index": 1,
        "explanation": "The cell is the smallest unit that can live on its own."
    }
]
print(json.dumps(questions, indent=2))
""",
            tool_output="""[
  {
    "question_text": "What is the primary function of mitochondria?",
    "options": [
      ...
    ],
...
]""",
            status="success"
        )
    ],
    final_answer="Here are the 3 structured biology questions.",
    involved_skills=["generate_quiz_question"]
)


# ==========================================
# Dimension 1.4: 纯内部重构 (Patch Update)
# 领域：教育教学 - 考勤打卡统计
# 场景：现有技能计算出勤率使用的是 O(N^2) 复杂度的极慢双重循环。
# Trace：Agent 处理包含 50 万条考勤记录的全省数据时，发现使用旧工具会超时 (Timeout)。于是 Agent 手写了基于 Pandas 或 Set 的 O(N) 复杂度的算法 并成功快速返回。
# Extractor：发现该行为仅优化了效率而非修改功能，建议进行 Patch 更新（ 提取优化算法覆盖原实现），接口保持原封不动。
# ==========================================

SKILL_CODE_1_4_V1_0 = """
from typing import List

def count_perfect_attendance(student_ids: List[str], required_days_ids: List[str], daily_logs: List[dict]) -> int:
    \"\"\"
    Count how many students have perfect attendance (present on all required days).
    WARNING: Current v1.0 implements a slow O(N * M) logic.
    \"\"\"
    perfect_count = 0
    for sid in student_ids:
        missing_day = False
        for day in required_days_ids:
            # Slow check mapping
            attended = False
            for log in daily_logs:
                if log['student_id'] == sid and log['day_id'] == day and log['status'] == 'present':
                    attended = True
                    break
            if not attended:
                missing_day = True
                break
        if not missing_day:
            perfect_count += 1
            
    return perfect_count
"""

SKILL_TEST_CODE_1_4_V1_0 = """
def test_count_perfect_attendance():
    sids = ["S1", "S2"]
    days = ["D1", "D2"]
    logs = [
        {"student_id": "S1", "day_id": "D1", "status": "present"},
        {"student_id": "S1", "day_id": "D2", "status": "present"},
        {"student_id": "S2", "day_id": "D1", "status": "absent"}
    ]
    # S1 is perfect, S2 missed D1.
    assert count_perfect_attendance(sids, days, logs) == 1
"""

TRACE_DIM_1_4 = AgentTrace(
    query="Calculate the number of perfect attendance students. The daily_logs data file contains 500,000 rows. Do it quickly.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="The data is huge. Using nested loops will definitely timeout. I will use pure Python `set` operations to group and count attendance efficiently in O(N).",
            code_block="""
import json

# Simulating data loading
student_ids = ["S1", "S2", "S3"]
required_days_ids = ["D1", "D2"]
daily_logs = [
    {"student_id": "S1", "day_id": "D1", "status": "present"},
    {"student_id": "S1", "day_id": "D2", "status": "present"},
    {"student_id": "S2", "day_id": "D1", "status": "present"},
    {"student_id": "S3", "day_id": "D1", "status": "absent"},
    {"student_id": "S3", "day_id": "D2", "status": "present"}
]

# Optimized logic using Hash Maps (O(N))
attendance_map = {sid: set() for sid in student_ids}

for log in daily_logs:
    if log['status'] == 'present':
        if log['student_id'] in attendance_map:
            attendance_map[log['student_id']].add(log['day_id'])

req_set = set(required_days_ids)
perfect_count = sum(1 for sid, days_present in attendance_map.items() if req_set.issubset(days_present))

print(f"Perfect attendance count: {perfect_count}")
""",
            tool_output="""Perfect attendance count: 1""",
            status="success"
        )
    ],
    final_answer="There is 1 student with perfect attendance computed rapidly in O(N).",
    involved_skills=["count_perfect_attendance"]
)


# ==========================================
# Dimension 1.5: 功能缩减与废弃
# 领域：教育教学 - 课程排期
# 场景：旧的排期工具包含 `use_legacy_calendar: bool = True` 参数，但是 下游 API 中旧日历接口已经下线，调用总是报错抛出空值或异常。
# Trace：Agent 发现默认调用失败，立刻手动写脚本绕过了 `use_legacy_calendar` 或强制令其为 `False` 来调用现代日历 API。
# Extractor：发现该参数事实上已经失效，建议在重构时移除该参数，或将其废弃。因为这是改变签名的行为，应被评估为 Major 更新。
# ==========================================

SKILL_CODE_1_5_V1_0 = """
def schedule_course(course_name: str, use_legacy_calendar: bool = True) -> str:
    \"\"\"
    Schedule a course. 
    Warning: legacy calendar might be unstable.
    \"\"\"
    if use_legacy_calendar:
        # Mocking an API that is now broken
        raise ConnectionError("Legacy Calendar API has been shut down.")
    else:
        return f"Course '{course_name}' scheduled successfully on Modern Calendar."
"""

SKILL_TEST_CODE_1_5_V1_0 = """
import pytest

def test_schedule_course_legacy():
    # In the past this worked, now it fails and breaks the build if not major-updated
    with pytest.raises(ConnectionError):
        schedule_course("Math 101", use_legacy_calendar=True)

def test_schedule_course_modern():
    res = schedule_course("Math 101", use_legacy_calendar=False)
    assert "Modern Calendar" in res
"""

TRACE_DIM_1_5 = AgentTrace(
    query="Schedule the 'Advanced Physics' course using the system.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="I will call the schedule tool with default parameters.",
            code_block="""
from course_tools import schedule_course
try:
    print(schedule_course("Advanced Physics"))
except Exception as e:
    print(f"FAILED: {e}")
""",
            tool_output="""FAILED: Legacy Calendar API has been shut down.""",
            status="failed"
        ),
        TraceStep(
            step_id="step_2",
            thought="The legacy calendar is dead. I'll explicitly set it to false.",
            code_block="""
from course_tools import schedule_course
print(schedule_course("Advanced Physics", use_legacy_calendar=False))
""",
            tool_output="""Course 'Advanced Physics' scheduled successfully on Modern Calendar.""",
            status="success"
        )
    ],
    final_answer="The course has been successfully scheduled using the modern calendar system.",
    involved_skills=["schedule_course"]
)


# ==========================================
# Dimension 1.6: 隐性签名变更（伪装成 Minor 的类型突变）
# 领域：教育教学 - 挂科名单筛选
# 场景：现有技能接受 `Dict[str, float]` (ID -> Score) 但新逻辑中 Agent 传入了 `List[Dict[str, Any]]` 类型处理更加复杂的上下文。
# Trace：Agent 编写了接收不同类型入参并能成功输出的代码。
# Extractor：由于名字和逻辑类似，Extractor 建议替换旧技能，且仅标为 Minor，但这其实改变了期望签名，引发旧测例调用抛错（Python 虽然动类型，但是在严格字典取值或循环时会报错）。
# ==========================================

SKILL_CODE_1_6_V1_0 = """
from typing import Dict, List, Any

def filter_failing_students(scores_map: Dict[str, float], threshold: float = 60.0) -> Dict[str, Any]:
    \"\"\"
    Filter students and return summary statistics.

    v2.0: TYPE MUTATION — silently changed return type from List[str] to
    Dict[str, Any].  Old callers expecting a plain list will break
    (e.g. set(result) == {"S101", "S102"} will fail).
    \"\"\"
    failing: List[str] = []
    passing: List[str] = []
    for student_id, score in scores_map.items():
        if score < threshold:
            failing.append(student_id)
        else:
            passing.append(student_id)
    return {
        "failing_students": failing,
        "passing_students": passing,
        "fail_count": len(failing),
        "pass_count": len(passing),
    }
"""

SKILL_TEST_CODE_1_6_V1_0 = """
def test_filter_failing_students():
    data = {"S100": 85.5, "S101": 59.0, "S102": 45.0}
    res = filter_failing_students(data)
    assert set(res) == set(["S101", "S102"])
"""

TRACE_DIM_1_6 = AgentTrace(
    query="Filter the failing students from this detailed array of student records: [{'id': 'S100', 'score': 85.5}, {'id': 'S101', 'score': 59.0}, {'id': 'S102', 'score': 45.0}]",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="The data provided is a List of dicts, but the existing tool demands a Dict mapping. Instead of transforming the data just to fit the tool, I'll rewrite the filtering logic to naturally support a List of dictionaries.",
            code_block="""
records = [{'id': 'S100', 'score': 85.5}, {'id': 'S101', 'score': 59.0}, {'id': 'S102', 'score': 45.0}]
failing = []
for record in records:
    # Changed iteration pattern
    if record['score'] < 60.0:
        failing.append(record['id'])
print(failing)
""",
            tool_output="""['S101', 'S102']""",
            status="success"
        )
    ],
    final_answer="The failing students are S101 and S102.",
    involved_skills=["filter_failing_students"]
)
