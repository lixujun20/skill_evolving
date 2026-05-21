# LLM Role I/O Samples for BFCL and Spreadsheet

Date: 2026-05-19

This document shows literal representative input/output payloads for every LLM-powered role used by the current BFCL and Spreadsheet evolution paths. Synthetic case values are used so the formats are stable and readable. Deterministic retriever/injector/verifier code is not listed as an LLM role.

## BFCL Case

Case id: `bfcl_live_simple_001`

User task: find the current weather for Boston, then send it to Alice.

Available BFCL tools:

```json
[
  {
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "Get current weather for a city.",
      "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "send_message",
      "description": "Send a message to a contact.",
      "parameters": {
        "type": "object",
        "properties": {
          "contact": {"type": "string"},
          "message": {"type": "string"}
        },
        "required": ["contact", "message"]
      }
    }
  }
]
```

### BFCL executor, prompt-only skill format

LLM input:

```json
{
  "role": "executor",
  "api": "tool_chat",
  "model": "claude-sonnet-4-5",
  "messages": [
    {
      "role": "system",
      "content": "You are a BFCL function-calling agent.\n\nRetrieved skill package guidance:\n### bfcl_weather_then_message (workflow_guardrail_card, workflow, seed, v1)\nWhen a user asks to forward weather to a contact, first call get_weather with the city, then call send_message with the observed weather summary. Do not invent weather.\n\nUse tools to satisfy the user request."
    },
    {
      "role": "user",
      "content": "What is the weather in Boston? Send the result to Alice."
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city.",
        "parameters": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "send_message",
        "description": "Send a message to a contact.",
        "parameters": {
          "type": "object",
          "properties": {
            "contact": {"type": "string"},
            "message": {"type": "string"}
          },
          "required": ["contact", "message"]
        }
      }
    }
  ]
}
```

LLM output:

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_001",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\":\"Boston\"}"
      }
    }
  ]
}
```

Next executor input after the tool observation:

```json
{
  "role": "executor",
  "api": "tool_chat",
  "messages": [
    {"role": "user", "content": "What is the weather in Boston? Send the result to Alice."},
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {
          "id": "call_001",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\":\"Boston\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_001",
      "name": "get_weather",
      "content": "{\"city\":\"Boston\",\"weather\":\"62F and raining\"}"
    }
  ],
  "tools": ["get_weather schema", "send_message schema"]
}
```

LLM output:

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_002",
      "type": "function",
      "function": {
        "name": "send_message",
        "arguments": "{\"contact\":\"Alice\",\"message\":\"Boston weather: 62F and raining.\"}"
      }
    }
  ]
}
```

### BFCL executor, functional skill-as-tool format

LLM input:

```json
{
  "role": "executor",
  "api": "tool_chat",
  "messages": [
    {
      "role": "system",
      "content": "You are a BFCL function-calling agent.\n\nRetrieved skill package guidance:\n(no prompt skill artifacts retrieved)\n\nA retrieved functional skill is exposed as a callable tool. Use it only if the tool family, schema, and user intent match."
    },
    {
      "role": "user",
      "content": "What is the weather in Boston? Send the result to Alice."
    }
  ],
  "tools": [
    "get_weather schema",
    "send_message schema",
    {
      "type": "function",
      "function": {
        "name": "skill_bfcl_weather_then_message",
        "description": "Composite skill: call get_weather, then send_message.",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string"},
            "contact": {"type": "string"}
          },
          "required": ["city", "contact"]
        }
      }
    }
  ]
}
```

LLM output:

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_skill_001",
      "type": "function",
      "function": {
        "name": "skill_bfcl_weather_then_message",
        "arguments": "{\"city\":\"Boston\",\"contact\":\"Alice\"}"
      }
    }
  ]
}
```

### BFCL executor, step prompt-context update after error

If a previous tool call fails, BFCL performs step retrieval again. If a new prompt skill is found, this literal user message is appended to the same message list.

```json
{
  "role": "user",
  "content": "Runtime skill retrieval update for this same user turn.\nNewly retrieved local rules: bfcl_send_message_contact_disambiguation.\nUse these only if they directly match the current tool family, schema, and user intent; ignore them if they are irrelevant.\n\n### bfcl_send_message_contact_disambiguation (workflow_guardrail_card, workflow, seed, v1)\nWhen send_message returns unknown_contact, call search_contacts with the given display name before retrying send_message with the canonical contact id."
}
```

The next LLM input includes the original messages, the failed tool observation, and the update message:

```json
{
  "role": "executor",
  "api": "tool_chat",
  "messages": [
    {"role": "user", "content": "Send the Boston weather to Alice."},
    {
      "role": "assistant",
      "tool_calls": [
        {
          "id": "call_002",
          "type": "function",
          "function": {
            "name": "send_message",
            "arguments": "{\"contact\":\"Alice\",\"message\":\"Boston weather: 62F and raining.\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_002",
      "name": "send_message",
      "content": "{\"error\":\"unknown_contact\",\"contact\":\"Alice\"}"
    },
    {
      "role": "user",
      "content": "Runtime skill retrieval update for this same user turn.\nNewly retrieved local rules: bfcl_send_message_contact_disambiguation.\nUse these only if they directly match the current tool family, schema, and user intent; ignore them if they are irrelevant.\n\n### bfcl_send_message_contact_disambiguation (workflow_guardrail_card, workflow, seed, v1)\nWhen send_message returns unknown_contact, call search_contacts with the given display name before retrying send_message with the canonical contact id."
    }
  ],
  "tools": ["get_weather schema", "send_message schema", "search_contacts schema"]
}
```

LLM output:

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_003",
      "type": "function",
      "function": {
        "name": "search_contacts",
        "arguments": "{\"query\":\"Alice\"}"
      }
    }
  ]
}
```

## Spreadsheet Case

Case id: `sheet_double_a1_b1`

Task: input workbook has `Sheet1!A1 = 8`, answer range is `Sheet1!B1`, expected value is `16`.

### Spreadsheet executor, single-shot text format

LLM input:

```json
{
  "role": "executor",
  "api": "text_chat",
  "system": "You are a spreadsheet manipulation agent.\n\nWrite Python code using openpyxl to modify the workbook at INPUT_XLSX and save\nthe final answer workbook to OUTPUT_XLSX. Do not explain instead of editing the\nfile. Preserve sheets, formats, and unrelated cells when possible.\n\nRetrieved skill package guidance:\n### spreadsheet_double_source_cell (executable_tool, functional, seed, v1)\nDouble the numeric source cell and write it to the requested answer cell.\n\nCallable function skills:\nExecutable Spreadsheet skills are already importable from `skill_library`. You may directly reuse them in returned code, e.g. `from skill_library import skill_name` then `skill_name(INPUT_XLSX, OUTPUT_XLSX)`. Prefer importing a callable over rewriting its implementation when its scope matches the workbook and requested answer range. Pass explicit keyword arguments for sheet names, ranges, columns, and row bounds when the description requires them.\n- `spreadsheet_double_source_cell(INPUT_XLSX, OUTPUT_XLSX, **kwargs)` from skill `spreadsheet_double_source_cell`: Double the numeric source cell and write it to the requested answer cell.\n\nReturn exactly one fenced python code block.\n",
  "prompt": "Instruction:\nDouble A1 into B1.\n\nInput workbook path: /tmp/sheet_double_a1_b1_input.xlsx\nOutput workbook path: /tmp/sheet_double_a1_b1_output.xlsx\nAnswer sheet: Sheet1\nAnswer range: B1\nData position: Sheet1!A1:B1\n\nWorkbook preview:\n## Sheet1 (1x2)\n[[8, null]]\n\nWrite robust openpyxl code. It may inspect workbook sheets/cells, then save to OUTPUT_XLSX."
}
```

LLM output, direct call variant:

````text
```python
from skill_library import spreadsheet_double_source_cell
spreadsheet_double_source_cell(INPUT_XLSX, OUTPUT_XLSX, sheet_name="Sheet1", source_cell="A1", answer_cell="B1")
```
````

LLM output, copy/adapt-code variant:

````text
```python
import openpyxl
wb = openpyxl.load_workbook(INPUT_XLSX)
ws = wb["Sheet1"]
ws["B1"] = ws["A1"].value * 2
wb.save(OUTPUT_XLSX)
```
````

### Spreadsheet executor, notebook text format with per-turn retrieval

Turn 1 LLM input:

```json
{
  "role": "executor",
  "api": "text_chat",
  "system": "You are a spreadsheet manipulation agent working in a persistent Python notebook.\n\nYou can iteratively write Python/openpyxl code against INPUT_XLSX and OUTPUT_XLSX.\nEach code block is executed in the same Python process, so later turns may reuse\nvariables, functions, imports, and workbook objects created earlier.\n\nRetrieved skill package guidance:\n(no reusable skill artifacts retrieved)\n\nCallable function skills:\n(no callable function skills available)\n\nReturn exactly one fenced python code block when you need to inspect, edit, or\nrepair the workbook. After each execution you will receive stdout, stderr, and\nthe return code. Save the final workbook to OUTPUT_XLSX before finishing.\n\nWhen you are finished, include the exact token <SPREADSHEET_DONE>. If you include a\ncode block and <SPREADSHEET_DONE> in the same response, the code will run first and\nthen the task will be verified.\n",
  "prompt": "Instruction:\nDouble A1 into B1.\n\nInput workbook path: /tmp/sheet_double_a1_b1_input.xlsx\nOutput workbook path: /tmp/sheet_double_a1_b1_output.xlsx\nAnswer sheet: Sheet1\nAnswer range: B1\nData position: Sheet1!A1:B1\n\nWorkbook preview:\n## Sheet1 (1x2)\n[[8, null]]\n\nNotebook protocol:\n- You have at most 5 turns.\n- Each fenced python block executes in the same process and can reuse previous variables.\n- Use print(...) to inspect workbook state when needed.\n- If execution fails, use the stderr in the next turn to repair the code.\n- Save the final answer workbook to OUTPUT_XLSX, then output <SPREADSHEET_DONE>.\n\nThis is turn 1. Return a fenced python code block to inspect or solve the workbook. When the workbook is saved and final, include <SPREADSHEET_DONE>."
}
```

Turn 1 LLM output:

````text
```python
import openpyxl
wb = openpyxl.load_workbook(INPUT_XLSX)
ws = wb["Sheet1"]
print("A1", ws["A1"].value)
missing_name
```
````

Turn 1 execution observation:

```json
{
  "stdout": "A1 8\n",
  "stderr": "Traceback (most recent call last):\n  File \"spreadsheet_notebook_driver.py\", line 32, in <module>\n    exec(str(payload.get(\"code\") or \"\"), ns, ns)\n  File \"<string>\", line 5, in <module>\nNameError: name 'missing_name' is not defined\n",
  "returncode": 1
}
```

Turn 2 retrieval query format:

```text
SpreadsheetBench notebook step retrieval query.
Task id: sheet_double_a1_b1
Turn index: 1
Instruction: Double A1 into B1.
Answer sheet: Sheet1
Answer range: B1
Data position: Sheet1!A1:B1
Previous step had an execution error; retrieve repair, API-usage, or workbook-shape skills that directly address the traceback and the current task.
Latest executed code:
import openpyxl
wb = openpyxl.load_workbook(INPUT_XLSX)
ws = wb["Sheet1"]
print("A1", ws["A1"].value)
missing_name
Latest return code: 1
Latest stdout:
A1 8
Latest stderr:
Traceback (most recent call last):
  File "spreadsheet_notebook_driver.py", line 32, in <module>
    exec(str(payload.get("code") or ""), ns, ns)
  File "<string>", line 5, in <module>
NameError: name 'missing_name' is not defined
```

Turn 2 LLM input after new skill user-message injection:

```json
{
  "role": "executor",
  "api": "text_chat",
  "system": "You are a spreadsheet manipulation agent working in a persistent Python notebook.\n\nYou can iteratively write Python/openpyxl code against INPUT_XLSX and OUTPUT_XLSX.\nEach code block is executed in the same Python process, so later turns may reuse\nvariables, functions, imports, and workbook objects created earlier.\n\nRetrieved skill package guidance:\n(runtime skill retrieval updates, if any, are appended in user messages during the notebook conversation)\n\nCallable function skills:\n(callable skill updates, if any, are appended in user messages during the notebook conversation)\n\nReturn exactly one fenced python code block when you need to inspect, edit, or\nrepair the workbook. After each execution you will receive stdout, stderr, and\nthe return code. Save the final workbook to OUTPUT_XLSX before finishing.\n\nWhen you are finished, include the exact token <SPREADSHEET_DONE>. If you include a\ncode block and <SPREADSHEET_DONE> in the same response, the code will run first and\nthen the task will be verified.\n",
  "prompt": "Instruction:\nDouble A1 into B1.\n\nInput workbook path: /tmp/sheet_double_a1_b1_input.xlsx\nOutput workbook path: /tmp/sheet_double_a1_b1_output.xlsx\nAnswer sheet: Sheet1\nAnswer range: B1\nData position: Sheet1!A1:B1\n\nWorkbook preview:\n## Sheet1 (1x2)\n[[8, null]]\n\nNotebook protocol:\n- You have at most 5 turns.\n- Each fenced python block executes in the same process and can reuse previous variables.\n- Use print(...) to inspect workbook state when needed.\n- If execution fails, use the stderr in the next turn to repair the code.\n- Save the final answer workbook to OUTPUT_XLSX, then output <SPREADSHEET_DONE>.\n\nRuntime skill retrieval updates already appended to this notebook conversation:\n\nUser message 1:\nRuntime skill retrieval update for this same Spreadsheet notebook task.\nNewly retrieved local rules/functions: spreadsheet_repair_after_name_error.\nUse these only if they directly match the workbook, answer range, and user intent; ignore them if they are irrelevant. If a callable function matches, prefer importing and calling it from `skill_library`; if unsure, inspect its code object before adapting or rewriting.\n\n### spreadsheet_repair_after_name_error (executable_tool, functional, seed, v1)\nRepair notebook after a NameError and write double of A1 to B1.\n\n```python\ndef apply_repair(INPUT_XLSX, OUTPUT_XLSX, **kwargs):\n    import openpyxl\n    wb = openpyxl.load_workbook(INPUT_XLSX)\n    ws = wb[\"Sheet1\"]\n    ws[\"B1\"] = ws[\"A1\"].value * 2\n    wb.save(OUTPUT_XLSX)\n```\n\nNew callable Spreadsheet functions:\nExecutable Spreadsheet skills are already available through `skill_library`. You may call them directly, or inspect the implementation before deciding by running `print(<function_name>_skill.code)` in a notebook cell.\nAvailable callable signatures:\n- `spreadsheet_repair_after_name_error(INPUT_XLSX, OUTPUT_XLSX, **kwargs)` from skill `spreadsheet_repair_after_name_error`: Repair notebook after a NameError and write double of A1 to B1. Implementation gist: def apply_repair(INPUT_XLSX, OUTPUT_XLSX, **kwargs): import openpyxl wb = openpyxl.load_workbook(INPUT_XLSX) ws = wb[\"Sheet1\"] ws[\"B1\"] = ws[\"A1\"].value * 2 wb.save(OUTPUT_XLSX)\nInspectable skill code objects:\n- `spreadsheet_repair_after_name_error_skill.code` prints the full implementation for `spreadsheet_repair_after_name_error`.\n\nPrevious notebook executions:\n\nTurn 1:\nCode:\n```python\nimport openpyxl\nwb = openpyxl.load_workbook(INPUT_XLSX)\nws = wb[\"Sheet1\"]\nprint(\"A1\", ws[\"A1\"].value)\nmissing_name\n```\nReturn code: 1\nstdout:\nA1 8\nstderr:\nNameError: name 'missing_name' is not defined\n\nThis is turn 2 of 5. Return the next fenced python code block. If the workbook is already correct and saved, include <SPREADSHEET_DONE>."
}
```

Turn 2 LLM output, direct call variant:

````text
```python
from skill_library import spreadsheet_repair_after_name_error
spreadsheet_repair_after_name_error(INPUT_XLSX, OUTPUT_XLSX)
```
<SPREADSHEET_DONE>
````

Turn 2 LLM output, inspect code then adapt variant:

````text
```python
from skill_library import spreadsheet_repair_after_name_error_skill
print(spreadsheet_repair_after_name_error_skill.code)
```
````

A later turn may then output:

````text
```python
import openpyxl
wb = openpyxl.load_workbook(INPUT_XLSX)
ws = wb["Sheet1"]
ws["B1"] = ws["A1"].value * 2
wb.save(OUTPUT_XLSX)
```
<SPREADSHEET_DONE>
````

## JSON Maintenance Roles

All roles in this section use the same transport shape:

```json
{
  "system": "...role-specific schema and policy...",
  "user": "...JSON-serializable evidence/context...",
  "llm_config": "local_claude_proxy",
  "model_name": "claude-sonnet-4-5",
  "role": "role_name",
  "metadata": {"benchmark": "bfcl_v3 or spreadsheet"}
}
```

The output must be a JSON object, not prose.

### Generic/BFCL role: `credit_assigner`

Input:

```json
{
  "role": "credit_assigner",
  "system": "You are assigning credit to retrieved skill artifacts. Return JSON with task_summary and skill_judgments.",
  "user": "{\"task_id\":\"bfcl_live_simple_001\",\"success\":false,\"trace\":{\"tool_calls\":[{\"name\":\"send_message\",\"arguments\":{\"contact\":\"Alice\"},\"error\":\"unknown_contact\"}]},\"retrieved_skills\":[{\"name\":\"bfcl_weather_then_message\",\"body\":\"first weather, then send_message\"}]}"
}
```

Output:

```json
{
  "task_summary": {
    "task_id": "bfcl_live_simple_001",
    "success": false,
    "score": 0.5
  },
  "skill_judgments": [
    {
      "skill_name": "bfcl_weather_then_message",
      "judgment": "neutral",
      "effect_type": "partial_relevance",
      "confidence": 0.74,
      "reason": "The skill correctly ordered weather before messaging but did not cover contact disambiguation.",
      "maintenance_actions": [
        {
          "action": "extract_new_skill",
          "reason": "Need a rule for unknown_contact recovery.",
          "target_scope": "send_message contact lookup"
        }
      ],
      "refine_required": false,
      "filter_candidate": false,
      "evidence_strength": "medium",
      "attribution_scope": "trace_behavior"
    }
  ]
}
```

### Spreadsheet role: `spreadsheet_credit_assigner`

Input:

```json
{
  "role": "spreadsheet_credit_assigner",
  "system": "You are assigning credit for SpreadsheetBench skill artifacts. Return task_summary and skill_judgments JSON.",
  "user": "{\"task_id\":\"sheet_double_a1_b1\",\"success\":false,\"score\":0.0,\"trace\":{\"code\":\"ws['B1']=0\\nwb.save(OUTPUT_XLSX)\",\"stderr\":\"\"},\"retrieved_skills\":[{\"name\":\"spreadsheet_bad_double\",\"body\":\"For doubling tasks, write 0.\"}]}"
}
```

Output:

```json
{
  "task_summary": {
    "task_id": "sheet_double_a1_b1",
    "success": false,
    "score": 0.0
  },
  "skill_judgments": [
    {
      "skill_name": "spreadsheet_bad_double",
      "judgment": "harmful",
      "effect_type": "correctness_harm",
      "confidence": 0.96,
      "reason": "The retrieved skill encouraged writing a constant instead of A1*2.",
      "maintenance_actions": [
        {
          "action": "refine_workflow",
          "reason": "Replace constant-writing rule with source-cell multiplication.",
          "target_scope": "doubling tasks"
        }
      ],
      "refine_required": true,
      "filter_candidate": true,
      "evidence_strength": "strong",
      "attribution_scope": "prompt_influence",
      "bundle_case_suggestions": [
        {
          "polarity": "negative",
          "reason": "Guard against writing zero for doubling tasks.",
          "source_task_id": "sheet_double_a1_b1",
          "focus_turn_indices": [0],
          "required_context_turn_indices": [],
          "state_requirements": {},
          "expected_contract": "B1 must equal A1*2.",
          "task_fragment_policy": "reuse_official_fragment"
        }
      ]
    }
  ]
}
```

### Generic/BFCL role: `extractor`

Input:

```json
{
  "role": "extractor",
  "system": "Extract reusable skill artifacts from successful or diagnostic traces. Return JSON {\"artifacts\": [...]} only.",
  "user": "{\"benchmark\":\"bfcl_v3\",\"task_id\":\"bfcl_live_simple_001\",\"success\":true,\"trace\":{\"tool_calls\":[{\"name\":\"get_weather\",\"arguments\":{\"city\":\"Boston\"}},{\"name\":\"send_message\",\"arguments\":{\"contact\":\"Alice\",\"message\":\"Boston weather: 62F and raining.\"}}]}}"
}
```

Output:

```json
{
  "artifacts": [
    {
      "name": "bfcl_weather_then_message",
      "kind": "workflow_guardrail_card",
      "description": "Forward weather only after observing get_weather output.",
      "body": "Applicability: user asks to send weather to a contact. Steps: call get_weather(city), then call send_message(contact, message) using the observed weather. Non-applicability: do not use for non-weather messages.",
      "interface": {
        "summary": "Weather forwarding workflow.",
        "usage": "Use for BFCL tasks combining weather lookup and messaging.",
        "input_contract": {"required_tools": ["get_weather", "send_message"]},
        "output_contract": {"final_tool": "send_message"},
        "invocation_contract": {"injection_type": "workflow"},
        "compatibility_notes": "Do not invent weather."
      },
      "metadata": {
        "domains": ["BFCL"],
        "allowed_tools": ["get_weather", "send_message"],
        "intent_keywords": ["weather", "send", "message"]
      }
    }
  ]
}
```

### Spreadsheet role: `spreadsheet_extractor`

Input:

```json
{
  "role": "spreadsheet_extractor",
  "system": "Extract SpreadsheetBench skill artifacts. Prefer executable_tool for reusable openpyxl implementations. Return JSON {\"artifacts\": [...]} only.",
  "user": "{\"task_id\":\"sheet_double_a1_b1\",\"success\":true,\"trace\":{\"code\":\"import openpyxl\\nwb=openpyxl.load_workbook(INPUT_XLSX)\\nws=wb['Sheet1']\\nws['B1']=ws['A1'].value*2\\nwb.save(OUTPUT_XLSX)\"},\"answer_range\":\"B1\"}"
}
```

Output:

```json
{
  "artifacts": [
    {
      "name": "spreadsheet_double_source_cell",
      "kind": "executable_tool",
      "description": "Double a numeric source cell and write it to an answer cell.",
      "body": "```python\ndef apply_double(INPUT_XLSX, OUTPUT_XLSX, sheet_name=\"Sheet1\", source_cell=\"A1\", answer_cell=\"B1\", **kwargs):\n    import openpyxl\n    wb = openpyxl.load_workbook(INPUT_XLSX)\n    ws = wb[sheet_name]\n    ws[answer_cell] = ws[source_cell].value * 2\n    wb.save(OUTPUT_XLSX)\n```",
      "interface": {
        "summary": "Double source cell into answer cell.",
        "usage": "Use for SpreadsheetBench doubling tasks.",
        "input_contract": {"benchmark": "SpreadsheetBench", "source_cell": "numeric"},
        "output_contract": {"answer_cell": "source*2"},
        "invocation_contract": {"injection_type": "functional"},
        "compatibility_notes": "Pass explicit sheet/cell kwargs when they differ."
      },
      "metadata": {
        "domains": ["SpreadsheetBench", "formula_generation"],
        "allowed_tools": ["openpyxl"],
        "intent_keywords": ["double", "multiply", "numeric"]
      }
    }
  ]
}
```

### Generic role: `extractor_feedback`

Input:

```json
{
  "role": "extractor_feedback",
  "system": "Update extractor rules from accepted/rejected extraction feedback. Return JSON with summary and rules.",
  "user": "{\"current_rules\":[\"Do not extract constants as skills.\"],\"feedback_rows\":[{\"artifact\":\"spreadsheet_bad_constant\",\"decision\":\"rejected\",\"reason\":\"hard-coded B1=16\"}]}"
}
```

Output:

```json
{
  "summary": "Tightened rules against hard-coded answer constants.",
  "rules": [
    "Do not extract constants as skills.",
    "For SpreadsheetBench executable skills, require parameterized sheet/range/cell handling unless the case is explicitly fixed-format."
  ],
  "updated_at": "2026-05-19"
}
```

### Generic role: `bundle_builder`

Input:

```json
{
  "role": "bundle_builder",
  "system": "Build focused positive, negative, and integration bundle cases for a skill. Return JSON with bundle fields.",
  "user": "{\"skill\":{\"name\":\"spreadsheet_double_source_cell\",\"body\":\"double source cell\"},\"source_cases\":[{\"task_id\":\"sheet_double_a1_b1\",\"expected_contract\":\"B1=A1*2\"}]}"
}
```

Output:

```json
{
  "bundle": {
    "positive_cases": [
      {
        "case_id": "sheet_double_positive_a1_b1",
        "task_fragment": "Double A1 into B1.",
        "expected_contract": "B1 equals A1*2.",
        "assertions": [{"type": "cell_equals_formula", "cell": "B1", "formula": "A1*2"}]
      }
    ],
    "negative_cases": [
      {
        "case_id": "sheet_double_negative_no_constant",
        "task_fragment": "Double A1 into B1 when A1 changes.",
        "expected_contract": "Do not write a hard-coded constant.",
        "assertions": [{"type": "not_hard_coded", "cell": "B1"}]
      }
    ],
    "integration_cases": []
  }
}
```

### Generic role: `bundle_maintainer`

Input:

```json
{
  "role": "bundle_maintainer",
  "system": "Maintain an existing skill bundle after test evidence. Return JSON with updated bundle and notes.",
  "user": "{\"skill_name\":\"spreadsheet_double_source_cell\",\"current_bundle\":{\"positive_cases\":[\"sheet_double_positive_a1_b1\"]},\"test_results\":[{\"case_id\":\"sheet_double_negative_no_constant\",\"passed\":false,\"reason\":\"hard-coded 16\"}]}"
}
```

Output:

```json
{
  "decision": {
    "action": "add_negative_case",
    "reason": "A hard-coded constant failure needs a focused regression case."
  },
  "bundle": {
    "positive_cases": ["sheet_double_positive_a1_b1"],
    "negative_cases": ["sheet_double_negative_no_constant"],
    "integration_cases": []
  },
  "maintenance_notes": "Added a negative case for source-value changes."
}
```

### Generic role: `refiner`

Input:

```json
{
  "role": "refiner",
  "system": "Refine one skill artifact using credit and bundle evidence. Return JSON with decision, artifact patch, and bundle patch.",
  "user": "{\"artifact\":{\"name\":\"spreadsheet_bad_double\",\"body\":\"write B1=0\"},\"credit_context\":[{\"judgment\":\"harmful\",\"reason\":\"writes constant zero\"}],\"bundle_results\":[{\"case_id\":\"sheet_double_negative_no_constant\",\"passed\":false}]}"
}
```

Output:

```json
{
  "decision": {
    "action": "refine_minor",
    "reason": "Replace constant output with parameterized source-cell multiplication.",
    "version_kind": "minor",
    "pinned_dependencies": []
  },
  "artifact": {
    "body": "```python\ndef apply_double(INPUT_XLSX, OUTPUT_XLSX, sheet_name=\"Sheet1\", source_cell=\"A1\", answer_cell=\"B1\", **kwargs):\n    import openpyxl\n    wb = openpyxl.load_workbook(INPUT_XLSX)\n    ws = wb[sheet_name]\n    ws[answer_cell] = ws[source_cell].value * 2\n    wb.save(OUTPUT_XLSX)\n```",
    "interface": {
      "summary": "Double source cell.",
      "invocation_contract": {"injection_type": "functional"}
    },
    "metadata": {
      "intent_keywords": ["double", "multiply"],
      "last_refine_reason": "Removed constant output."
    }
  },
  "bundle": {
    "maintenance_notes": "Retain negative case against constants.",
    "positive_cases": [],
    "negative_cases": ["sheet_double_negative_no_constant"],
    "integration_cases": []
  }
}
```

### Generic role: `stale_resolver`

Input:

```json
{
  "role": "stale_resolver",
  "system": "Resolve stale dependent skills after an upstream skill changed. Return JSON decisions.",
  "user": "{\"changed_skill\":\"spreadsheet_double_source_cell@v2\",\"dependents\":[{\"name\":\"spreadsheet_double_then_format\",\"dependency\":\"spreadsheet_double_source_cell@v1\"}]}"
}
```

Output:

```json
{
  "decisions": [
    {
      "skill_name": "spreadsheet_double_then_format",
      "action": "update_dependency_pin",
      "reason": "The upstream signature stayed compatible.",
      "new_dependency_pins": [{"name": "spreadsheet_double_source_cell", "version": 2}]
    }
  ]
}
```

### Generic role: `refactorer`

Input:

```json
{
  "role": "refactorer",
  "system": "Refactor overlapping skill artifacts into cleaner shared or specialized artifacts. Return JSON only.",
  "user": "{\"overlap_group\":[{\"name\":\"spreadsheet_double_a1\",\"body\":\"double A1 into B1\"},{\"name\":\"spreadsheet_double_c1\",\"body\":\"double C1 into D1\"}],\"segments\":[{\"task_id\":\"sheet_double_a1_b1\",\"span\":\"ws['B1']=ws['A1'].value*2\"}]}"
}
```

Output:

```json
{
  "decision": {
    "action": "merge",
    "reason": "Both skills implement the same parameterized doubling operation."
  },
  "artifacts": [
    {
      "name": "spreadsheet_double_source_cell",
      "kind": "executable_tool",
      "description": "Parameterized source-cell doubling.",
      "body": "```python\ndef apply_double(INPUT_XLSX, OUTPUT_XLSX, sheet_name, source_cell, answer_cell, **kwargs):\n    import openpyxl\n    wb = openpyxl.load_workbook(INPUT_XLSX)\n    ws = wb[sheet_name]\n    ws[answer_cell] = ws[source_cell].value * 2\n    wb.save(OUTPUT_XLSX)\n```",
      "metadata": {
        "domains": ["SpreadsheetBench"],
        "replaces": ["spreadsheet_double_a1", "spreadsheet_double_c1"]
      },
      "interface": {
        "invocation_contract": {"injection_type": "functional"}
      }
    }
  ],
  "disable": ["spreadsheet_double_a1", "spreadsheet_double_c1"]
}
```
