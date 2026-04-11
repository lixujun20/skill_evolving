from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat

# ==========================================
# Dimension 6: 其他 - 应对超长 Trace（Long Trace Handling）
# 领域：教育教学 - 完整的期末考试成绩处理流程
# 场景：Trace 包含 30+ 步骤，涵盖数据导入、分析、评分、通知全流程
# Extractor 需从冗余上下文中精准提取核心 Skill
# ==========================================

SKILL_CODE_6_1 = """
from typing import Dict, List, Any, Optional

def process_exam_results(
    exam_id: str,
    student_answers: Dict[str, List[str]],
    answer_key: Dict[int, str],
    passing_threshold: float = 0.6
) -> Dict[str, Any]:
    \"\"\"
    Process exam results for all students and compute pass/fail decisions.

    Args:
        exam_id (str): Unique exam identifier.
        student_answers (Dict[str, List[str]]): student_id -> list of answers (0-indexed).
        answer_key (Dict[int, str]): question_index -> correct_answer.
        passing_threshold (float): Minimum ratio to pass. Default 0.6.

    Returns:
        Dict[str, Any]: {
            'exam_id': str,
            'results': Dict[str, Dict] with per-student 'score', 'passed', 'correct_count',
            'summary': {'pass_rate': float, 'avg_score': float, 'total_students': int}
        }

    Examples:
        >>> answers = {"S1001": ["A", "B", "C"], "S1002": ["A", "A", "C"]}
        >>> key = {0: "A", 1: "B", 2: "C"}
        >>> result = process_exam_results("EXAM_001", answers, key)
        >>> result['results']['S1001']['passed']
        True
        >>> result['summary']['pass_rate'] > 0
        True
    \"\"\"
    results = {}
    total_questions = len(answer_key)

    for student_id, student_ans in student_answers.items():
        correct = sum(
            1 for q_idx, correct_ans in answer_key.items()
            if q_idx < len(student_ans) and student_ans[q_idx] == correct_ans
        )
        score = correct / total_questions if total_questions > 0 else 0.0
        results[student_id] = {
            "score": round(score, 4),
            "passed": score >= passing_threshold,
            "correct_count": correct,
            "total_questions": total_questions
        }

    if results:
        pass_count = sum(1 for r in results.values() if r["passed"])
        avg_score = sum(r["score"] for r in results.values()) / len(results)
        summary = {
            "pass_rate": round(pass_count / len(results), 4),
            "avg_score": round(avg_score, 4),
            "total_students": len(results)
        }
    else:
        summary = {"pass_rate": 0.0, "avg_score": 0.0, "total_students": 0}

    return {
        "exam_id": exam_id,
        "results": results,
        "summary": summary
    }
"""

SKILL_TEST_CODE_6_1 = """
import pytest

def test_process_exam_all_pass():
    answers = {"S1001": ["A", "B", "C"], "S1002": ["A", "B", "C"]}
    key = {0: "A", 1: "B", 2: "C"}
    result = process_exam_results("EXAM001", answers, key)
    assert result["results"]["S1001"]["passed"] is True
    assert result["results"]["S1002"]["passed"] is True
    assert result["summary"]["pass_rate"] == 1.0

def test_process_exam_mixed_results():
    answers = {"S1001": ["A", "B", "C"], "S1002": ["X", "X", "X"]}
    key = {0: "A", 1: "B", 2: "C"}
    result = process_exam_results("EXAM001", answers, key)
    assert result["results"]["S1001"]["passed"] is True
    assert result["results"]["S1002"]["passed"] is False
    assert result["summary"]["pass_rate"] == 0.5

def test_process_exam_empty_students():
    result = process_exam_results("EXAM001", {}, {0: "A"})
    assert result["summary"]["total_students"] == 0
    assert result["results"] == {}

def test_process_exam_custom_threshold():
    answers = {"S1001": ["A", "B", "X"]}  # 2/3 correct = 0.666
    key = {0: "A", 1: "B", 2: "C"}
    result = process_exam_results("EXAM001", answers, key, passing_threshold=0.8)
    assert result["results"]["S1001"]["passed"] is False  # 0.666 < 0.8

def test_process_exam_returns_exam_id():
    result = process_exam_results("FINAL_CS101", {"S1001": ["A"]}, {0: "A"})
    assert result["exam_id"] == "FINAL_CS101"
"""

# Long trace: 30+ steps covering the full exam processing workflow
TRACE_DIM_6_1 = AgentTrace(
    query="Process the final exam results for CS101. 50 students, 10 questions. Calculate scores, pass/fail, and send notifications to failing students.",
    trace_format=TraceFormat.CODEACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought="First, let me load the list of enrolled students for CS101.",
            code_block="students = db.query('SELECT student_id FROM enrollments WHERE course_id = \"CS101\"')\nprint(f'Enrolled: {len(students)} students')",
            tool_output="Enrolled: 50 students",
            status="success"
        ),
        TraceStep(
            step_id="step_2",
            thought="Now load the exam answer submissions.",
            code_block="submissions = db.query('SELECT student_id, answers FROM exam_submissions WHERE exam_id = \"FINAL_CS101\"')\nprint(f'Submissions: {len(submissions)}')",
            tool_output="Submissions: 48",
            status="success"
        ),
        TraceStep(
            step_id="step_3",
            thought="2 students didn't submit. I'll flag them as absent and proceed with the 48 who did.",
            code_block="submitted_ids = {s['student_id'] for s in submissions}\nall_ids = {s['student_id'] for s in students}\nabsent = all_ids - submitted_ids\nprint('Absent:', absent)",
            tool_output="Absent: {'S1033', 'S1047'}",
            status="success"
        ),
        TraceStep(
            step_id="step_4",
            thought="Load the answer key for FINAL_CS101.",
            code_block="answer_key = db.query('SELECT question_idx, correct_answer FROM answer_keys WHERE exam_id = \"FINAL_CS101\"')\nprint('Questions in key:', len(answer_key))",
            tool_output="Questions in key: 10",
            status="success"
        ),
        TraceStep(
            step_id="step_5",
            thought="Convert the answer key to a dictionary indexed by question number.",
            code_block="key_dict = {a['question_idx']: a['correct_answer'] for a in answer_key}\nprint('Key:', key_dict)",
            tool_output="Key: {0: 'B', 1: 'A', 2: 'D', 3: 'C', 4: 'A', 5: 'B', 6: 'D', 7: 'C', 8: 'A', 9: 'B'}",
            status="success"
        ),
        TraceStep(
            step_id="step_6",
            thought="Convert student submissions to the required format: student_id -> list of answers.",
            code_block="student_answers = {s['student_id']: s['answers'] for s in submissions}\nprint('Sample:', list(student_answers.items())[:2])",
            tool_output="Sample: [('S1001', ['B', 'A', 'D', 'C', 'A', 'B', 'D', 'C', 'A', 'B']), ('S1002', ['B', 'A', 'D', 'C', 'A', 'B', 'D', 'C', 'B', 'A'])]",
            status="success"
        ),
        TraceStep(
            step_id="step_7",
            thought="Now I'll score student S1001 manually to verify the logic.",
            code_block="s1_answers = student_answers['S1001']\ncorrect = sum(1 for i, a in enumerate(s1_answers) if key_dict.get(i) == a)\nprint(f'S1001 correct: {correct}/10')",
            tool_output="S1001 correct: 10/10",
            status="success"
        ),
        TraceStep(
            step_id="step_8",
            thought="Good, S1001 got a perfect score. Let me check S1002.",
            code_block="s2_answers = student_answers['S1002']\ncorrect = sum(1 for i, a in enumerate(s2_answers) if key_dict.get(i) == a)\nprint(f'S1002 correct: {correct}/10')",
            tool_output="S1002 correct: 8/10",
            status="success"
        ),
        TraceStep(
            step_id="step_9",
            thought="I should batch this. Let me use the process_exam_results skill.",
            code_block="from edu_skills import process_exam_results\nresult = process_exam_results('FINAL_CS101', student_answers, key_dict)\nprint('Summary:', result['summary'])",
            tool_output="Summary: {'pass_rate': 0.7917, 'avg_score': 0.7208, 'total_students': 48}",
            status="success"
        ),
        TraceStep(
            step_id="step_10",
            thought="79% pass rate. Let me identify all failing students.",
            code_block="failing = [sid for sid, r in result['results'].items() if not r['passed']]\nprint(f'Failing students ({len(failing)}):', failing[:5])",
            tool_output="Failing students (10): ['S1005', 'S1012', 'S1015', 'S1023', 'S1028']",
            status="success"
        ),
        TraceStep(
            step_id="step_11",
            thought="10 students failed. Let me also see the score distribution.",
            code_block="scores = sorted([r['score'] for r in result['results'].values()])\nprint('Score distribution:', scores[:10])",
            tool_output="Score distribution: [0.3, 0.4, 0.4, 0.5, 0.5, 0.5, 0.5, 0.6, 0.6, 0.6]",
            status="success"
        ),
        TraceStep(
            step_id="step_12",
            thought="The minimum score is 0.3, maximum should be 1.0. Let me verify the top scorers.",
            code_block="top = sorted(result['results'].items(), key=lambda x: x[1]['score'], reverse=True)[:5]\nfor sid, r in top: print(sid, r['score'])",
            tool_output="S1001 1.0\nS1007 1.0\nS1011 0.9\nS1014 0.9\nS1016 0.9",
            status="success"
        ),
        TraceStep(
            step_id="step_13",
            thought="Good stats. Now I need to load email addresses for the failing students.",
            code_block="fail_emails = db.query(f'SELECT student_id, email FROM students WHERE student_id IN {tuple(failing)}')\nprint('Got emails for:', len(fail_emails), 'students')",
            tool_output="Got emails for: 10 students",
            status="success"
        ),
        TraceStep(
            step_id="step_14",
            thought="Prepare notification message template.",
            code_block="""template = \"\"\"Dear {name},

We regret to inform you that you did not pass the CS101 Final Exam.
Your score: {score_percent}%
Passing threshold: 60%

Please contact your advisor to discuss options for course retake.

Best regards,
Academic Office\"\"\"
print("Template loaded.")""",
            tool_output="Template loaded.",
            status="success"
        ),
        TraceStep(
            step_id="step_15",
            thought="Send emails to all failing students.",
            code_block="""from edu_tools import email_client

sent_count = 0
for fe in fail_emails:
    sid = fe['student_id']
    score = result['results'][sid]['score']
    msg = template.format(name=fe.get('name', 'Student'), score_percent=int(score*100))
    email_client.send(to=fe['email'], subject='CS101 Final Exam Result', body=msg)
    sent_count += 1
print(f'Sent {sent_count} notifications')""",
            tool_output="Sent 10 notifications",
            status="success"
        ),
        TraceStep(
            step_id="step_16",
            thought="Now save all results to the database for official records.",
            code_block="""for sid, r in result['results'].items():
    db.execute(
        'INSERT INTO exam_records (exam_id, student_id, score, passed) VALUES (?,?,?,?)',
        ('FINAL_CS101', sid, r['score'], r['passed'])
    )
db.commit()
print('Saved', len(result['results']), 'records')""",
            tool_output="Saved 48 records",
            status="success"
        ),
        TraceStep(
            step_id="step_17",
            thought="Record the 2 absent students as well.",
            code_block="""for absent_id in absent:
    db.execute(
        'INSERT INTO exam_records (exam_id, student_id, score, passed, absent) VALUES (?,?,?,?,?)',
        ('FINAL_CS101', absent_id, 0.0, False, True)
    )
db.commit()
print('Saved absent records for:', absent)""",
            tool_output="Saved absent records for: {'S1033', 'S1047'}",
            status="success"
        ),
        TraceStep(
            step_id="step_18",
            thought="Generate a summary report for the instructor.",
            code_block="""report = f\"\"\"
=== FINAL EXAM REPORT: CS101 ===
Exam ID: FINAL_CS101
Total Enrolled: 50
Submitted: 48
Absent: 2
Pass Rate: {result['summary']['pass_rate']*100:.1f}%
Average Score: {result['summary']['avg_score']*100:.1f}%
Passing Students: {48 - 10}
Failing Students: {10}
Notifications Sent: 10
================================
\"\"\"
print(report)""",
            tool_output="""=== FINAL EXAM REPORT: CS101 ===
Exam ID: FINAL_CS101
Total Enrolled: 50
Submitted: 48
Absent: 2
Pass Rate: 79.2%
Average Score: 72.1%
Passing Students: 38
Failing Students: 10
Notifications Sent: 10
================================""",
            status="success"
        ),
        TraceStep(
            step_id="step_19",
            thought="Good. Now let me also calculate grade letter distributions for the department.",
            code_block="""from collections import Counter
grade_letters = {}
for sid, r in result['results'].items():
    s = r['score']
    if s >= 0.9: grade_letters[sid] = 'A'
    elif s >= 0.8: grade_letters[sid] = 'B'
    elif s >= 0.7: grade_letters[sid] = 'C'
    elif s >= 0.6: grade_letters[sid] = 'D'
    else: grade_letters[sid] = 'F'
dist = Counter(grade_letters.values())
print('Grade distribution:', dict(dist))""",
            tool_output="Grade distribution: {'A': 12, 'B': 14, 'C': 8, 'D': 4, 'F': 10}",
            status="success"
        ),
        TraceStep(
            step_id="step_20",
            thought="Add grade letter to each DB record and calculate weighted GPA impact.",
            code_block="""for sid, letter in grade_letters.items():
    db.execute(
        'UPDATE exam_records SET grade_letter = ? WHERE exam_id = ? AND student_id = ?',
        (letter, 'FINAL_CS101', sid)
    )
db.commit()
print('Grade letters saved.')""",
            tool_output="Grade letters saved.",
            status="success"
        ),
        TraceStep(
            step_id="step_21",
            thought="Check if any students need to be placed on academic probation (less than 50% in 2 consecutive exams).",
            code_block="""prev_failing = db.query("""
"SELECT student_id FROM exam_records WHERE exam_id = 'MIDTERM_CS101' AND passed = 0"
""")
prev_fail_ids = {r['student_id'] for r in prev_failing}
double_fail = [sid for sid in failing if sid in prev_fail_ids]
print('At-risk students (double fail):', double_fail)""",
            tool_output="At-risk students (double fail): ['S1005', 'S1023']",
            status="success"
        ),
        TraceStep(
            step_id="step_22",
            thought="Flag S1005 and S1023 for academic advisor review.",
            code_block="""for sid in ['S1005', 'S1023']:
    db.execute(
        'INSERT INTO academic_holds (student_id, reason, created_at) VALUES (?, ?, datetime("now"))',
        (sid, 'Failed both midterm and final for CS101')
    )
db.commit()
print('Academic holds placed for:', ['S1005', 'S1023'])""",
            tool_output="Academic holds placed for: ['S1005', 'S1023']",
            status="success"
        ),
        TraceStep(
            step_id="step_23",
            thought="Send special notifications to at-risk students.",
            code_block="""for sid in ['S1005', 'S1023']:
    email = db.query(f'SELECT email FROM students WHERE student_id = \"{sid}\"')[0]['email']
    email_client.send(
        to=email,
        subject='Urgent: Academic Hold Placed - CS101',
        body=f'Dear Student {sid}, you have been placed on academic hold due to failing both exams.'
    )
print('At-risk notifications sent.')""",
            tool_output="At-risk notifications sent.",
            status="success"
        ),
        TraceStep(
            step_id="step_24",
            thought="Generate statistics for the registrar: pass/fail count by section.",
            code_block="""sections = db.query('SELECT student_id, section FROM enrollments WHERE course_id = \"CS101\"')
section_map = {s['student_id']: s['section'] for s in sections}
section_stats = {}
for sid, r in result['results'].items():
    sec = section_map.get(sid, 'unknown')
    if sec not in section_stats:
        section_stats[sec] = {'pass': 0, 'fail': 0}
    if r['passed']:
        section_stats[sec]['pass'] += 1
    else:
        section_stats[sec]['fail'] += 1
print('Section stats:', section_stats)""",
            tool_output="Section stats: {'A': {'pass': 20, 'fail': 5}, 'B': {'pass': 18, 'fail': 5}}",
            status="success"
        ),
        TraceStep(
            step_id="step_25",
            thought="Archive the complete exam processing results for audit trail.",
            code_block="""import json, datetime
archive = {
    'exam_id': 'FINAL_CS101',
    'processed_at': datetime.datetime.utcnow().isoformat(),
    'summary': result['summary'],
    'grade_distribution': dict(dist),
    'section_stats': section_stats,
    'at_risk_students': ['S1005', 'S1023']
}
with open('/tmp/FINAL_CS101_audit.json', 'w') as f:
    json.dump(archive, f, indent=2)
print('Audit archive saved.')""",
            tool_output="Audit archive saved.",
            status="success"
        ),
        TraceStep(
            step_id="step_26",
            thought="Verify the archived file is accessible and correctly structured.",
            code_block="""import json
with open('/tmp/FINAL_CS101_audit.json') as f:
    audit = json.load(f)
print('Summary:', audit['summary'])
print('Keys:', list(audit.keys()))""",
            tool_output="""Summary: {'pass_rate': 0.7917, 'avg_score': 0.7208, 'total_students': 48}
Keys: ['exam_id', 'processed_at', 'summary', 'grade_distribution', 'section_stats', 'at_risk_students']""",
            status="success"
        ),
        TraceStep(
            step_id="step_27",
            thought="Confirm all tasks are complete. Let me do a final check on DB record integrity.",
            code_block="""count = db.query('SELECT COUNT(*) as cnt FROM exam_records WHERE exam_id = \"FINAL_CS101\"')[0]['cnt']
print(f'Total records stored: {count} / 50 expected')""",
            tool_output="Total records stored: 50 / 50 expected",
            status="success"
        ),
        TraceStep(
            step_id="step_28",
            thought="Excellent. All 50 records are saved. Generate a final confirmation log.",
            code_block="""print('=== PROCESSING COMPLETE ===')\nprint('Exam: FINAL_CS101')\nprint('Records: 50 saved')\nprint('Notifications: 12 sent (10 fail + 2 at-risk)')\nprint('Audit: /tmp/FINAL_CS101_audit.json')""",
            tool_output="""=== PROCESSING COMPLETE ===
Exam: FINAL_CS101
Records: 50 saved
Notifications: 12 sent (10 fail + 2 at-risk)
Audit: /tmp/FINAL_CS101_audit.json""",
            status="success"
        ),
        TraceStep(
            step_id="step_29",
            thought="The core reusable logic here is the process_exam_results function. Everything else is orchestration. Let me verify it handles edge cases.",
            code_block="""# Test edge case: empty exam
empty_result = process_exam_results('TEST_EMPTY', {}, {0: 'A'})
print('Empty result:', empty_result)""",
            tool_output="Empty result: {'exam_id': 'TEST_EMPTY', 'results': {}, 'summary': {'pass_rate': 0.0, 'avg_score': 0.0, 'total_students': 0}}",
            status="success"
        ),
        TraceStep(
            step_id="step_30",
            thought="Edge case works correctly. The process_exam_results skill is well-defined and handles all cases including empty input.",
            code_block="""# Final confirmation
print('process_exam_results skill verified:')
print('- Handles 0..N students')
print('- Configurable passing_threshold')
print('- Returns structured results with per-student and summary data')
print('- Ready for production use')""",
            tool_output="""process_exam_results skill verified:
- Handles 0..N students
- Configurable passing_threshold
- Returns structured results with per-student and summary data
- Ready for production use""",
            status="success"
        )
    ],
    final_answer="Final exam processing complete for CS101. 48/50 students scored; 38 passed (79.2% pass rate); 10 notified of failure; 2 placed on academic hold.",
    involved_skills=["process_exam_results"]
)
