from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TraceStep:
    step_id: str
    thought: str
    tool_call: Optional[str] = None
    tool_output: Optional[str] = None
    code_block: Optional[str] = None
    status: str = "success"

@dataclass
class AgentTrace:
    query: str
    trace_format: str
    steps: List[TraceStep]

# Simple demo trace used by the minimal pipeline
TRACE_DIM_1_1 = AgentTrace(
    query="Convert student transcript lines into JSON objects",
    trace_format="CODEACT",
    steps=[
        TraceStep(
            step_id="step_1",
            thought="Call existing skill generate_transcript() to fetch raw text",
            tool_call="core_skill_call",
            tool_output='"Name: Alice; Score: 90;"',
            code_block=None,
        ),
        TraceStep(
            step_id="step_2",
            thought="Parse the raw text into a dict and normalize types",
            tool_call="manual_parse",
            tool_output="{'name': 'Alice', 'score': 90}",
            code_block="def parse_line(s):\n    parts = s.replace(';', '').split()\n    # naive parse for demo\n    return {'name': parts[1], 'score': int(parts[3])}",
        ),
    ],
)
