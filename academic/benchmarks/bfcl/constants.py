"""BFCL dataset locations, tool-family metadata, and executor prompt constants."""
from __future__ import annotations

from pathlib import Path

BFCL_OFFICIAL_UNPACK = Path("/tmp/bfcl_pkg/unpack")
DATASET_URL = (
    "https://huggingface.co/datasets/gorilla-llm/"
    "Berkeley-Function-Calling-Leaderboard/raw/main/BFCL_v3_multi_turn_base.json"
)
ANSWER_URL = (
    "https://huggingface.co/datasets/gorilla-llm/"
    "Berkeley-Function-Calling-Leaderboard/raw/main/possible_answer/"
    "BFCL_v3_multi_turn_base.json"
)
BFCL_BUNDLE_DATASET = BFCL_OFFICIAL_UNPACK / "bfcl_eval" / "data" / "BFCL_v4_multi_turn_base.json"
BFCL_BUNDLE_ANSWER = (
    BFCL_OFFICIAL_UNPACK / "bfcl_eval" / "data" / "possible_answer" / "BFCL_v4_multi_turn_base.json"
)
BFCL_BUNDLE_FUNC_DOC_DIR = BFCL_OFFICIAL_UNPACK / "bfcl_eval" / "data" / "multi_turn_func_doc"
FUNC_DOC_BASE = (
    "https://huggingface.co/datasets/gorilla-llm/"
    "Berkeley-Function-Calling-Leaderboard/raw/main/multi_turn_func_doc"
)
FUNC_DOC_FILES = [
    "gorilla_file_system.json",
    "math_api.json",
    "message_api.json",
    "posting_api.json",
    "ticket_api.json",
    "trading_bot.json",
    "travel_booking.json",
    "vehicle_control.json",
]

CLASS_DOC_FILES = {
    "GorillaFileSystem": ["gorilla_file_system.json"],
    "MathAPI": ["math_api.json"],
    "MessageAPI": ["message_api.json"],
    "TwitterAPI": ["posting_api.json"],
    "TicketAPI": ["ticket_api.json"],
    "TradingBot": ["trading_bot.json"],
    "TravelAPI": ["travel_booking.json"],
    "VehicleControlAPI": ["vehicle_control.json"],
}

BFCL_CLASS_FILE_BY_DOC = {file: cls for cls, files in CLASS_DOC_FILES.items() for file in files}

BFCL_SYSTEM = """You are running a BFCL-v3 multi-turn function-calling task.

Use the provided tools directly. Do not write Python code. For each user turn,
call the minimal sequence of tools needed to update the environment or retrieve
the requested information. Reuse returned ids, paths, login tokens, and state
from previous turns. If a historical skill is relevant, use it as guidance, but
do not force irrelevant reuse. If a retrieved skill is unrelated to the current
tool family, schema, or user intent, ignore it completely. Tool arguments must use the exact parameter names
from the provided schema; do not invent aliases such as insurance_id when the
schema requires booking_id.

Retrieved skill artifacts:
{skills}

Initial environment state summary:
{state_summary}
"""

BFCL_OFFICIAL_SYSTEM = """You are an expert in composing functions. You are given a question and a set of possible functions. Based on the question, you will need to make one or more function/tool calls to achieve the purpose. If none of the functions can be used, point it out. If the given question lacks the parameters required by the function, also point it out.

Use the provided native tools directly. At each turn, try your best to complete the tasks requested by the user within the current turn. Continue to call functions until you have fulfilled the user's request to the best of your ability. Once you have no more functions to call, the system will consider the current turn complete and proceed to the next turn or task.

If a retrieved skill is relevant, use it as guidance, but do not force irrelevant reuse. If a retrieved skill is unrelated to the current tool family, schema, or user intent, ignore it completely.

Retrieved skill artifacts:
{skills}
"""

TURN_INSTRUCTION = (
    "\n\nFor this BFCL turn, perform the requested operation with tool calls now. "
    "Do not just describe what you would do. If the turn asks for multiple actions, "
    "call every required tool in order before ending the turn. Infer missing ids "
    "from the initial state or previous tool results rather than asking the user."
)

OFFICIAL_TURN_INSTRUCTION = (
    "\n\nAt this turn, try your best to complete the user's requested task with "
    "the available tools. Continue calling tools until the request is fulfilled. "
    "If no more tools are needed, respond briefly with no tool call."
)

USE_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "Mark a retrieved skill artifact as intentionally used. Call this "
            "before domain tools when a skill guides the current turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the retrieved skill artifact being used.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason this skill applies to the current turn.",
                },
            },
            "required": ["skill_name"],
        },
    },
}
