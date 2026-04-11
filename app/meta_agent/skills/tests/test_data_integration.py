from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat

# ==========================================
# Integration Scenario A: Auto Code Debug
# Skill: auto_debug_code
# Scene: Agent tries naive regex-based debug skill on real buggy code; skill misses
#        actual syntax and name errors. Agent falls back to compile/exec approach.
# Extractor expected: Major update — replace regex with actual compilation/execution.
# ==========================================

SKILL_CODE_INT_A_V1_0 = """
def auto_debug_code(code: str) -> dict:
    \"\"\"Attempt to debug Python code by static analysis.\"\"\"
    issues = []
    if "NameError" in code or "undefined" in code.lower():
        issues.append({"type": "NameError", "suggestion": "Check variable names"})
    return {"issues": issues, "fixed_code": code, "status": "analyzed"}
"""

TRACE_INT_A_1 = AgentTrace(
    query=(
        "Debug the following Python function and return a report of all bugs found:\n"
        "def calculate_avg(scores)\n"
        "    total = sum(scors)\n"
        "    return total / len(scores)"
    ),
    trace_format=TraceFormat.REACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought=(
                "I'll use the auto_debug_code skill to check for issues in the provided code."
            ),
            tool_call="auto_debug_code",
            tool_input={
                "code": (
                    "def calculate_avg(scores)\n"
                    "    total = sum(scors)\n"
                    "    return total / len(scores)"
                )
            },
            tool_output=(
                '{"issues": [], "fixed_code": "def calculate_avg(scores)\\n'
                '    total = sum(scors)\\n'
                '    return total / len(scores)", "status": "analyzed"}'
            ),
            related_skill_name="auto_debug_code",
            status="success",
        ),
        TraceStep(
            step_id="step_2",
            thought=(
                "The skill returned no issues, but I can see at least two problems: "
                "a missing colon after the function definition (SyntaxError) and a typo "
                "'scors' instead of 'scores' (NameError). The skill only does naive string "
                "matching and misses these real errors. I'll compile and execute the code to "
                "catch actual exceptions."
            ),
            code_block="""
import ast

buggy_code = '''def calculate_avg(scores)
    total = sum(scors)
    return total / len(scores)'''

issues = []

# Check for SyntaxError via compilation
try:
    ast.parse(buggy_code)
except SyntaxError as e:
    issues.append({
        "type": "SyntaxError",
        "line": e.lineno,
        "message": str(e.msg),
        "suggestion": "Add missing colon after function definition"
    })

# Check for NameError via execution with dummy args (only if syntax is clean)
if not issues:
    try:
        namespace = {}
        exec(buggy_code, namespace)
        namespace["calculate_avg"]([1, 2, 3])
    except NameError as e:
        issues.append({
            "type": "NameError",
            "message": str(e),
            "suggestion": "Fix typo: 'scors' should be 'scores'"
        })

print(issues)
""",
            tool_output=(
                "[{'type': 'SyntaxError', 'line': 1, 'message': 'expected \":\"', "
                "'suggestion': 'Add missing colon after function definition'}]"
            ),
            status="success",
        ),
        TraceStep(
            step_id="step_3",
            thought=(
                "The compile step caught the SyntaxError on line 1. After fixing the colon "
                "I also detect the NameError from 'scors'. Assembling the final debug report."
            ),
            code_block="""
fixed_code = '''def calculate_avg(scores):
    total = sum(scores)
    return total / len(scores)'''

report = {
    "issues": [
        {
            "type": "SyntaxError",
            "line": 1,
            "message": "expected ':'",
            "suggestion": "Add missing colon after 'def calculate_avg(scores)'"
        },
        {
            "type": "NameError",
            "line": 2,
            "message": "name 'scors' is not defined",
            "suggestion": "Rename 'scors' to 'scores'"
        }
    ],
    "fixed_code": fixed_code,
    "status": "fixed"
}
print(report)
""",
            tool_output=(
                "{'issues': [{'type': 'SyntaxError', 'line': 1, "
                "'message': \"expected ':'\", "
                "'suggestion': \"Add missing colon after 'def calculate_avg(scores)'\"}, "
                "{'type': 'NameError', 'line': 2, 'message': \"name 'scors' is not defined\", "
                "'suggestion': \"Rename 'scors' to 'scores'\"}], "
                "'fixed_code': 'def calculate_avg(scores):\\n    total = sum(scores)\\n    return total / len(scores)', "
                "'status': 'fixed'}"
            ),
            status="success",
        ),
    ],
    final_answer=(
        "Found 2 bugs: SyntaxError (missing colon after def) and NameError (typo 'scors'). "
        "Fixed code provided."
    ),
    involved_skills=["auto_debug_code"],
)


# ==========================================
# Integration Scenario B: Student Grade Statistical Analysis
# Skill: analyze_student_grades
# Scene: Agent asks for comprehensive statistics; current skill returns only average.
#        Agent manually computes mean, median, std dev, grade distribution, at-risk.
# Extractor expected: Major update — change return type from str to comprehensive dict.
# ==========================================

SKILL_CODE_INT_B_V1_0 = """
def analyze_student_grades(scores: list) -> str:
    \"\"\"Analyze student grades.\"\"\"
    mean = sum(scores) / len(scores) if scores else 0
    return f"Average score: {mean:.1f}"
"""

TRACE_INT_B_1 = AgentTrace(
    query=(
        "Produce a comprehensive grade analysis for scores [75, 82, 91, 68, 77, 95, 60, 88]. "
        "Include mean, median, standard deviation, grade distribution (A/B/C/D/F), "
        "and identify at-risk students (score < 70)."
    ),
    trace_format=TraceFormat.REACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought=(
                "The current skill only gives the average. Let me call it first to see what "
                "it returns."
            ),
            tool_call="analyze_student_grades",
            tool_input={"scores": [75, 82, 91, 68, 77, 95, 60, 88]},
            tool_output='"Average score: 79.5"',
            related_skill_name="analyze_student_grades",
            status="success",
        ),
        TraceStep(
            step_id="step_2",
            thought=(
                "The skill only returned the average. I need median, standard deviation, "
                "grade distribution, and at-risk students. I'll compute these manually."
            ),
            code_block="""
import statistics

scores = [75, 82, 91, 68, 77, 95, 60, 88]

mean = sum(scores) / len(scores)
median = statistics.median(scores)
std_dev = statistics.stdev(scores)

def grade_letter(s):
    if s >= 90: return 'A'
    elif s >= 80: return 'B'
    elif s >= 70: return 'C'
    elif s >= 60: return 'D'
    else: return 'F'

distribution = {}
for s in scores:
    g = grade_letter(s)
    distribution[g] = distribution.get(g, 0) + 1

at_risk = [s for s in scores if s < 70]

report = {
    "mean": round(mean, 1),
    "median": median,
    "std_dev": round(std_dev, 1),
    "grade_distribution": distribution,
    "at_risk_scores": at_risk,
    "at_risk_count": len(at_risk),
    "total_students": len(scores)
}
print(report)
""",
            tool_output=(
                "{'mean': 79.5, 'median': 79.5, 'std_dev': 11.4, "
                "'grade_distribution': {'C': 2, 'B': 3, 'A': 2, 'D': 1}, "
                "'at_risk_scores': [68, 60], 'at_risk_count': 2, 'total_students': 8}"
            ),
            status="success",
        ),
        TraceStep(
            step_id="step_3",
            thought=(
                "The manual computation produced the full report. Returning it as the final answer."
            ),
            code_block="""
print("Analysis complete.")
print(f"mean=79.5, median=79.5, std=11.4, at-risk: 2 students (68, 60)")
""",
            tool_output="Analysis complete.\nmean=79.5, median=79.5, std=11.4, at-risk: 2 students (68, 60)",
            status="success",
        ),
    ],
    final_answer=(
        "Analysis complete: mean=79.5, median=79.5, std=10.5, at-risk: 2 students (68, 60)"
    ),
    involved_skills=["analyze_student_grades"],
)


# ==========================================
# Integration Scenario C: Multi-Role Learning Discussion (Long-Term Evolution)
# Skill: generate_learning_discussion
# Scene C v1: Agent uses hardcoded-template skill for "Python decorators";
#             result is too generic — agent writes custom 5-round discussion.
# Extractor expected: Minor update — add num_rounds parameter with backward-compatible default.
# ==========================================

SKILL_CODE_INT_C_V1_0 = """
def generate_learning_discussion(topic: str) -> dict:
    \"\"\"Generate a multi-role discussion on a topic.\"\"\"
    return {
        "topic": topic,
        "discussion": [
            {"role": "Teacher", "message": f"Let's discuss {topic}."},
            {"role": "Student A", "message": "I think it's interesting."},
            {"role": "Student B", "message": "Can you explain more?"}
        ]
    }
"""

TRACE_INT_C_1 = AgentTrace(
    query=(
        "Generate a structured multi-role learning discussion on 'Python decorators' "
        "with 5 rounds of dialogue covering definition, use cases, and common pitfalls."
    ),
    trace_format=TraceFormat.REACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought=(
                "I'll use generate_learning_discussion to get a discussion on Python decorators."
            ),
            tool_call="generate_learning_discussion",
            tool_input={"topic": "Python decorators"},
            tool_output=(
                '{"topic": "Python decorators", "discussion": ['
                '{"role": "Teacher", "message": "Let\'s discuss Python decorators."}, '
                '{"role": "Student A", "message": "I think it\'s interesting."}, '
                '{"role": "Student B", "message": "Can you explain more?"}]}'
            ),
            related_skill_name="generate_learning_discussion",
            status="success",
        ),
        TraceStep(
            step_id="step_2",
            thought=(
                "The skill returned a generic 3-message template with no real content. "
                "I need 5 rounds of substantive discussion. I'll write custom code to generate it."
            ),
            code_block="""
topic = "Python decorators"

rounds = [
    {
        "round": 1,
        "role": "Teacher",
        "message": (
            f"Today we'll explore {topic}. A decorator is a function that wraps another "
            "function to extend its behavior without modifying its source code."
        )
    },
    {
        "round": 2,
        "role": "Student A",
        "message": (
            "So it's like a wrapper? Can you show a simple example?"
        )
    },
    {
        "round": 3,
        "role": "Teacher",
        "message": (
            "Exactly! Here's a timing decorator:\\n"
            "import time\\n"
            "def timer(func):\\n"
            "    def wrapper(*args, **kwargs):\\n"
            "        start = time.time()\\n"
            "        result = func(*args, **kwargs)\\n"
            "        print(f'Elapsed: {time.time()-start:.4f}s')\\n"
            "        return result\\n"
            "    return wrapper"
        )
    },
    {
        "round": 4,
        "role": "Student B",
        "message": (
            "What are common pitfalls when using decorators?"
        )
    },
    {
        "round": 5,
        "role": "Teacher",
        "message": (
            "Great question! The most common pitfall is forgetting to use functools.wraps "
            "inside the wrapper, which causes the decorated function to lose its __name__ "
            "and __doc__ attributes."
        )
    }
]

result = {"topic": topic, "num_rounds": 5, "discussion": rounds}
print(result)
""",
            tool_output=(
                "{'topic': 'Python decorators', 'num_rounds': 5, 'discussion': ["
                "{'round': 1, 'role': 'Teacher', 'message': 'Today we\\'ll explore Python decorators...'}, "
                "{'round': 2, 'role': 'Student A', 'message': 'So it\\'s like a wrapper? Can you show a simple example?'}, "
                "{'round': 3, 'role': 'Teacher', 'message': 'Exactly! Here\\'s a timing decorator...'}, "
                "{'round': 4, 'role': 'Student B', 'message': 'What are common pitfalls when using decorators?'}, "
                "{'round': 5, 'role': 'Teacher', 'message': 'Great question! The most common pitfall is...'}]}"
            ),
            status="success",
        ),
    ],
    final_answer=(
        "Generated a 5-round discussion on Python decorators with Teacher, Student A, Student B roles."
    ),
    involved_skills=["generate_learning_discussion"],
)


# ==========================================
# Scenario C v1.1 skill (after first extraction — has num_rounds but only 2 student roles)
# ==========================================

SKILL_CODE_INT_C_V1_1 = """
def generate_learning_discussion(topic: str, num_rounds: int = 3) -> dict:
    \"\"\"Generate a multi-role discussion on a topic.

    Args:
        topic: The subject to discuss.
        num_rounds: Number of dialogue rounds (default 3 for backward compatibility).

    Returns:
        dict with keys 'topic', 'num_rounds', and 'discussion' (list of role/message dicts).
    \"\"\"
    roles = ["Teacher", "Student A", "Student B"]
    discussion = []
    for i in range(num_rounds):
        role = roles[i % len(roles)]
        if role == "Teacher":
            msg = f"Round {i + 1}: Let's continue exploring {topic}."
        else:
            msg = f"Round {i + 1}: I have a question about {topic}."
        discussion.append({"round": i + 1, "role": role, "message": msg})
    return {"topic": topic, "num_rounds": num_rounds, "discussion": discussion}
"""

# ==========================================
# Scenario C v2 trace (long-term test — v1.1 skill cannot handle 4 participants)
# ==========================================

TRACE_INT_C_2 = AgentTrace(
    query=(
        "Generate a learning discussion on 'machine learning overfitting' "
        "with 4 participants and 6 rounds."
    ),
    trace_format=TraceFormat.REACT,
    steps=[
        TraceStep(
            step_id="step_1",
            thought=(
                "The skill now supports num_rounds. Let me call it with 6 rounds. "
                "But it only has 3 hardcoded roles (Teacher, Student A, Student B). "
                "I need 4 participants."
            ),
            tool_call="generate_learning_discussion",
            tool_input={"topic": "machine learning overfitting", "num_rounds": 6},
            tool_output=(
                '{"topic": "machine learning overfitting", "num_rounds": 6, "discussion": ['
                '{"round": 1, "role": "Teacher", "message": "Round 1: Let\'s continue exploring machine learning overfitting."}, '
                '{"round": 2, "role": "Student A", "message": "Round 2: I have a question about machine learning overfitting."}, '
                '{"round": 3, "role": "Student B", "message": "Round 3: I have a question about machine learning overfitting."}, '
                '{"round": 4, "role": "Teacher", "message": "Round 4: Let\'s continue exploring machine learning overfitting."}, '
                '{"round": 5, "role": "Student A", "message": "Round 5: I have a question about machine learning overfitting."}, '
                '{"round": 6, "role": "Student B", "message": "Round 6: I have a question about machine learning overfitting."}]}'
            ),
            related_skill_name="generate_learning_discussion",
            status="success",
        ),
        TraceStep(
            step_id="step_2",
            thought=(
                "The skill only uses 3 roles and cycles through them. I need 4 distinct "
                "participants: Teacher, Student A, Student B, and Student C. The skill has no "
                "'participants' parameter to customize this. I'll write custom code to generate "
                "the 4-participant, 6-round discussion with real content about overfitting."
            ),
            code_block="""
topic = "machine learning overfitting"
participants = ["Teacher", "Student A", "Student B", "Student C"]
num_rounds = 6

scripts = [
    ("Teacher",
     "Overfitting occurs when a model learns noise in training data and performs poorly on new data."),
    ("Student A",
     "How can we tell if a model is overfitting?"),
    ("Teacher",
     "One key sign is a large gap between training accuracy and validation accuracy."),
    ("Student B",
     "What techniques help prevent overfitting?"),
    ("Student C",
     "I've heard dropout and regularization are used — can you explain?"),
    ("Teacher",
     "Great observations! Dropout randomly disables neurons during training. "
     "L2 regularization penalizes large weights. Both reduce the model's tendency to memorize training data."),
]

discussion = [
    {"round": i + 1, "role": role, "message": msg}
    for i, (role, msg) in enumerate(scripts)
]

result = {
    "topic": topic,
    "participants": participants,
    "num_rounds": num_rounds,
    "discussion": discussion
}
print(result)
""",
            tool_output=(
                "{'topic': 'machine learning overfitting', "
                "'participants': ['Teacher', 'Student A', 'Student B', 'Student C'], "
                "'num_rounds': 6, 'discussion': ["
                "{'round': 1, 'role': 'Teacher', 'message': 'Overfitting occurs when...'}, "
                "{'round': 2, 'role': 'Student A', 'message': 'How can we tell if a model is overfitting?'}, "
                "{'round': 3, 'role': 'Teacher', 'message': 'One key sign is a large gap...'}, "
                "{'round': 4, 'role': 'Student B', 'message': 'What techniques help prevent overfitting?'}, "
                "{'round': 5, 'role': 'Student C', 'message': 'I\\'ve heard dropout and regularization...'}, "
                "{'round': 6, 'role': 'Teacher', 'message': 'Great observations! Dropout randomly disables...'}]}"
            ),
            status="success",
        ),
    ],
    final_answer=(
        "Generated a 6-round discussion on 'machine learning overfitting' with 4 participants: "
        "Teacher, Student A, Student B, Student C."
    ),
    involved_skills=["generate_learning_discussion"],
)
