"""
executor.py — TIR (Tool-Integrated Reasoning) problem solver with skill injection.

The solver enables GLM thinking mode and exposes a `python_interpreter` tool
so the model can call code at any point during its reasoning, just like a
code interpreter.  All tool results feed back into the same conversation so
the model can build on prior computation (Jupyter-like persistence).

Skills are pre-loaded into the sandbox namespace so the model can call them
by name; only their signatures + docstrings appear in the system prompt to
avoid polluting the context with irrelevant code.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import sys
import traceback
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("tool_evolving")

from academic.config import (
    AGENT_MODEL, CODE_EXEC_TIMEOUT, MAX_AGENT_STEPS,
    LLM_CALL_TIMEOUT, LLM_TIMEOUT_RETRIES,
    RATE_LIMIT_RETRIES, RATE_LIMIT_BASE_WAIT,
)


@dataclass
class ExecTrace:
    """Record of one problem-solving attempt."""
    query: str
    steps: List[Dict[str, str]] = field(default_factory=list)
    final_answer: Optional[str] = None
    total_tokens: int = 0
    completion_tokens: int = 0
    code_blocks: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    reasoning_traces: List[str] = field(default_factory=list)  # thinking tokens per step
    success: bool = False
    timed_out: bool = False
    messages: List[Dict[str, str]] = field(default_factory=list)  # full conversation log
    partial_state: Optional[Dict] = None  # saved state for mid-solve resume
    skill_tool_counts: Dict[str, int] = field(default_factory=dict)
    skill_runtime_call_counts: Dict[str, int] = field(default_factory=dict)
    plan_context: str = ""
    solver_mode: str = "tir"


# ── Persistent sandbox ────────────────────────────────────────────────────────

class PersistentSandbox:
    """
    Subprocess-based Python sandbox that keeps state across exec() calls.

    Behaves like a Jupyter notebook: variables, imports, and function
    definitions defined in one call are available in the next.

    Each code execution spawns a subprocess that replays all prior successful
    code blocks (silently) before running the new code.  The subprocess is
    killed with SIGKILL on timeout, which reliably interrupts even C-level
    operations like math.factorial(huge_number) that would otherwise hold
    the GIL indefinitely.
    """

    def __init__(self):
        self._history: List[str] = []  # successful code blocks in order

    def preload(self, code: str) -> None:
        """Execute *code* unconditionally to seed the namespace (e.g. skill defs)."""
        code = _sanitize_code(code)
        self._history.append(code)

    async def run(
        self,
        code: str,
        timeout: int = CODE_EXEC_TIMEOUT,
    ) -> tuple[str, Dict[str, int]]:
        """Execute *code* asynchronously; returns stdout/stderr plus skill call counts."""
        code = _sanitize_code(code)
        script = _build_subprocess_script(self._history, _append_skill_counter_dump(code))
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                return "[TIMEOUT] Code execution exceeded time limit", {}

            output = stdout.decode("utf-8", errors="replace")
            output, skill_counts = _split_skill_counter_dump(output)
            if proc.returncode != 0:
                return f"[ERROR]\n{output[:4000]}", skill_counts
            # Only add to history on success so future replays don't fail
            self._history.append(code)
            return output, skill_counts
        except Exception as exc:
            return f"[ERROR] Sandbox launch failed: {exc}", {}


def _build_subprocess_script(history: List[str], current_code: str) -> str:
    """Build a Python script that silently replays *history* then runs *current_code*."""
    lines = [
        "import io as _io, sys as _sys",
        "_null = _io.StringIO()",
        "_real_stdout = _sys.stdout",
        "_real_stderr = _sys.stderr",
        "_sys.stdout = _null",
        "_sys.stderr = _null",
    ]
    for block in history:
        # Each history block runs silently; ignore errors (state may be partial)
        lines.append("try:")
        for bl in ("    " + ln for ln in block.splitlines()):
            lines.append(bl)
        lines.append("except Exception: pass")
    lines.extend([
        "_sys.stdout = _real_stdout",
        "_sys.stderr = _real_stderr",
    ])
    lines.append(current_code)
    return "\n".join(lines)


# ── Tool definition ───────────────────────────────────────────────────────────

def _sanitize_code(code: str) -> str:
    """Strip signal-module manipulation that could interfere with process management."""
    code = re.sub(r'(?m)^\s*import\s+signal\b.*$', '# (signal import removed)', code)
    code = re.sub(r'(?m)^\s*from\s+signal\b.*$', '# (signal import removed)', code)
    code = re.sub(r'\bsignal\.(alarm|signal|SIGALRM|SIG_IGN|SIG_DFL)\b', '0', code)
    return code


SKILL_COUNTER_SENTINEL = "__SKILL_CALL_COUNTS__="


def _append_skill_counter_dump(code: str) -> str:
    return (
        f"{code.rstrip()}\n"
        "print("
        f"\"{SKILL_COUNTER_SENTINEL}\" + "
        "__import__('json').dumps(globals().get('__skill_call_counts', {}), sort_keys=True)"
        ")\n"
    )


def _split_skill_counter_dump(output: str) -> tuple[str, Dict[str, int]]:
    lines = output.splitlines()
    counts: Dict[str, int] = {}
    cleaned: List[str] = []
    for line in lines:
        if line.startswith(SKILL_COUNTER_SENTINEL):
            payload = line[len(SKILL_COUNTER_SENTINEL):].strip()
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    counts = {str(k): int(v) for k, v in parsed.items()}
            except Exception:
                pass
            continue
        cleaned.append(line)
    cleaned_output = "\n".join(cleaned)
    if output.endswith("\n"):
        cleaned_output += "\n"
    return cleaned_output, counts


def _instrument_skill_code(skill: "Skill") -> str:
    wrapped_name = f"__orig_skill_{skill.name}"
    return (
        f"{skill.code.rstrip()}\n"
        "__skill_call_counts = globals().get('__skill_call_counts', {})\n"
        f"{wrapped_name} = {skill.name}\n"
        f"def {skill.name}(*args, __skill_name={skill.name!r}, __orig={wrapped_name}, **kwargs):\n"
        "    __skill_call_counts[__skill_name] = __skill_call_counts.get(__skill_name, 0) + 1\n"
        "    return __orig(*args, **kwargs)\n"
    )


def _prepare_skill_prompt_blocks(skills: List["Skill"]) -> tuple[str, str]:
    if not skills:
        return "(none — solve entirely from scratch)", "(no skills available — solve from scratch)"

    desc_parts: List[str] = []
    full_parts: List[str] = []
    for sk in skills:
        dep_str = f" (uses: {', '.join(sk.dependencies)})" if sk.dependencies else ""
        desc_parts.append(f"### `{sk.name}`{dep_str}\n{sk.description}")
        full_parts.append(
            f"# {sk.name}: {sk.description}{dep_str}\n{sk.code}"
        )
    full_parts.append(
        "# IMPORTANT: The above functions are pre-loaded. "
        "Call them directly by name. Do NOT reimplement them. "
        "Skills may call each other — dependencies are already resolved."
    )
    return "\n\n".join(desc_parts), "\n\n".join(full_parts)


def _merge_runtime_skill_counts(trace: ExecTrace, current_counts: Dict[str, int]) -> None:
    if not current_counts:
        return
    trace.skill_runtime_call_counts = {
        name: int(count)
        for name, count in current_counts.items()
        if int(count) > 0
    }


def _build_skill_namespace(skills: List["Skill"]) -> tuple[Dict[str, Any], Dict[str, int]]:
    namespace: Dict[str, Any] = {"__builtins__": __builtins__}
    skill_call_counts: Dict[str, int] = {}
    namespace["__skill_call_counts"] = skill_call_counts

    for sk in skills:
        try:
            exec(sk.code, namespace)  # noqa: S102
            fn = namespace.get(sk.name)
            if callable(fn):
                namespace[sk.name] = _wrap_runtime_skill_function(sk.name, fn, skill_call_counts)
        except Exception:
            logger.debug("Failed to preload skill %s", sk.name, exc_info=True)
    return namespace, skill_call_counts


def _wrap_runtime_skill_function(
    name: str,
    fn: Callable[..., Any],
    skill_call_counts: Dict[str, int],
) -> Callable[..., Any]:
    def wrapped(*args, __orig=fn, __skill_name=name, **kwargs):
        skill_call_counts[__skill_name] = skill_call_counts.get(__skill_name, 0) + 1
        return __orig(*args, **kwargs)

    wrapped.__name__ = getattr(fn, "__name__", name)
    wrapped.__doc__ = getattr(fn, "__doc__", None)
    return wrapped


PYTHON_INTERPRETER_TOOL = {
    "type": "function",
    "function": {
        "name": "python_interpreter",
        "description": (
            "Execute Python code and get the printed output. "
            "The environment is PERSISTENT across calls (like a Jupyter notebook): "
            "variables, imports, and function definitions from earlier calls remain available. "
            "Use print() to see results. The final answer must be printed as: ANSWER: <integer>"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Valid Python code to execute.",
                }
            },
            "required": ["code"],
        },
    },
}


def _make_skill_tool(skill: "Skill") -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": skill.name,
            "description": (
                f"{skill.description} "
                "Call this tool when you want to use the existing helper directly instead of rewriting it in Python. "
                "Pass a single string field `arguments` that contains the raw Python argument expression exactly as it should appear inside the function call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "arguments": {
                        "type": "string",
                        "description": (
                            "Raw Python arguments to place inside skill_name(...). "
                            "Examples: 'n', 'xs, 1000', 'a, b + 1, \"text\"'."
                        ),
                    }
                },
                "required": ["arguments"],
            },
        },
    }


# ── Prompts ───────────────────────────────────────────────────────────────────

SOLVE_SYSTEM = """\
You are a precise math competition problem solver using an interactive Python environment.

## Pre-loaded Helper Functions
The following functions are already available in your Python environment.
Use them by description when planning. They are also exposed as callable tools.

{skills_descriptions}

## Historical Workflow Hints
You may also receive compact historical workflow summaries from previous solved
queries. These are planning hints, not hard constraints.

{historical_workflows}

## Current Execution Plan
Before solving, you may also receive an explicit plan brief that summarizes:
- whether to reuse a historical plan,
- whether to reuse only a workflow fragment,
- which retrieved skills are likely relevant,
- and whether any new shared skill idea was proposed upstream.

{plan_context}

## How to work (ReAct pattern)
At each turn you may:
1. **Write your reasoning** in plain text — analyse the problem, plan your approach,
   interpret previous results. This text is part of the conversation and you will
   see it again in future turns, so use it as a persistent scratchpad.
2. **Call a skill tool** when one of the retrieved helpers already matches the subtask.
   For skill tools, pass raw Python argument expressions in the `arguments` field.
3. **Call `python_interpreter`** to execute Python code and get the output.
   The environment is PERSISTENT across calls (like a Jupyter notebook):
   variables, imports, and function definitions from earlier calls are still available.

You can do both in the same turn: write reasoning text, then call the tool.

## Rules
- Use the helper functions above when relevant. Prefer calling the corresponding skill tool instead of rewriting the helper.
- Do NOT redefine existing helpers.
- First decide whether history suggests:
  - reusing a previous plan directly,
  - adapting a previous plan,
  - reusing only a workflow fragment,
  - or planning fresh.
- Do NOT force reuse. If the historical workflows are irrelevant, ignore them.
- During later reasoning steps, briefly check whether one of the retrieved skills now
  matches the remaining subproblem. Reusing a fitting skill can save tokens and avoid
  small reimplementation mistakes, but do not force it when direct reasoning is simpler.
- Prefer short, focused code calls that build on previous results rather than
  recomputing everything from scratch each turn.
- When you have the final answer, either:
  - Print it in a tool call: `print("ANSWER:", result)`
  - Or state it in your text: `ANSWER: <integer>`
- AIME answers are integers between 0 and 999 inclusive.
"""

MATH_SOLVE_SYSTEM = """\
You are a precise math problem solver using an interactive Python environment.

## Pre-loaded Helper Functions
The following functions are already available in your Python environment.
Call them directly by name — no import or redefinition needed.

{skills_descriptions}

## Historical Workflow Hints
You may also receive compact historical workflow summaries from previous solved
queries. These are planning hints, not hard constraints.

{historical_workflows}

## Current Execution Plan
Before solving, you may also receive an explicit plan brief that summarizes:
- whether to reuse a historical plan,
- whether to reuse only a workflow fragment,
- which retrieved skills are likely relevant,
- and whether any new shared skill idea was proposed upstream.

{plan_context}

## How to work (ReAct pattern)
At each turn you may:
1. **Write your reasoning** in plain text — analyse the problem, plan your approach,
   interpret previous results. This text is part of the conversation and you will
   see it again in future turns, so use it as a persistent scratchpad.
2. **Call a skill tool** when one of the retrieved helpers already matches the subtask.
   For skill tools, pass raw Python argument expressions in the `arguments` field.
3. **Call `python_interpreter`** to execute Python code and get the output.
   The environment is PERSISTENT across calls (like a Jupyter notebook):
   variables, imports, and function definitions from earlier calls are still available.

You can do both in the same turn: write reasoning text, then call the tool.

## Rules
- Use the helper functions above when relevant. Prefer calling the corresponding skill tool instead of rewriting the helper.
- Do NOT redefine existing helpers.
- First decide whether history suggests:
  - reusing a previous plan directly,
  - adapting a previous plan,
  - reusing only a workflow fragment,
  - or planning fresh.
- Do NOT force reuse. If the historical workflows are irrelevant, ignore them.
- During later reasoning steps, briefly check whether one of the retrieved skills now
  matches the remaining subproblem. Reusing a fitting skill can save tokens and avoid
  small reimplementation mistakes, but do not force it when direct reasoning is simpler.
- Prefer short, focused code calls that build on previous results rather than
  recomputing everything from scratch each turn.
- When you have the final answer, state it clearly in your text using LaTeX boxed notation:
  \\boxed{{your_answer_here}}
- The answer may be an integer, fraction, expression, or other mathematical form.
  Write it exactly as it should appear (e.g. \\boxed{{\\frac{{3}}{{4}}}}, \\boxed{{2\\sqrt{{3}}}}).
"""

ONESHOT_SOLVE_SYSTEM = """\
You are a precise problem solver. You solve problems by writing Python code.

## Available Skills (helper functions)
The following functions are already available in your execution environment.
You can call them directly — no import needed.

{skills_block}

## Historical Workflow Hints
These summaries describe how similar past queries were solved. Reuse them only
if they are directly helpful.

{historical_workflows}

## Current Execution Plan
{plan_context}

## Instructions
1. Analyze the problem carefully.
2. Write a single Python solution that computes the answer.
3. Prefer using available skills when they apply.
4. Keep the code short and direct. Do not redefine existing helpers.
5. Your code must print the final answer as the last line of output, prefixed with
   `ANSWER: `. Example: `print(f"ANSWER: {{result}}")`
6. Do not print explanations after `ANSWER:`.

Respond only with a Python code block:
```python
# your code
```
"""

ONESHOT_REFINE_SYSTEM = """\
The previous code produced an error or invalid output.
Here is the execution result:

{output}

Fix the code and try again. Respond only with a corrected Python code block.
"""


NEXT_STEP_PROMPT = (
    "Next step: continue from the result above. If one of the retrieved skills now "
    "directly matches the remaining subproblem, consider reusing it because this can "
    "save tokens and reduce reimplementation mistakes. Do not force skill use if fresh "
    "reasoning is clearly simpler."
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_code(text: str) -> Optional[str]:
    """Pull out the first ```python ... ``` block."""
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None

def _extract_signature(code: str) -> str:
    """Return the first `def func(...)` line from a skill's source."""
    m = re.search(r"(def \w+\([^)]*\))", code)
    return m.group(1) if m else "def unknown()"


def _extract_boxed(text: str) -> Optional[str]:
    """Extract the innermost \\boxed{...} content with proper brace matching.

    Handles nested braces (e.g. \\boxed{\\frac{3}{4}}).
    Returns the last occurrence (model's final answer).
    """
    results = []
    i = 0
    while i < len(text):
        pos = text.find(r"\boxed{", i)
        if pos == -1:
            break
        start = pos + len(r"\boxed{")
        depth = 1
        j = start
        while j < len(text) and depth > 0:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        if depth == 0:
            results.append(text[start:j - 1])
        i = pos + 1
    return results[-1].strip() if results else None


def _extract_answer(text: str) -> Optional[str]:
    """Extract answer from text. Handles multiple formats:
    - ANSWER: 600
    - print("ANSWER:", 600)  → captured in output as 'ANSWER: 600'
    - \\boxed{600} or \\boxed{\\frac{3}{4}} (with nested braces)
    Returns the raw string; caller is responsible for normalization/grading.
    """
    # Priority 1: explicit ANSWER: tag (integer or expression)
    m = re.search(r"ANSWER:\s*(.+)", text)
    if m:
        val = m.group(1).strip().split("\n")[0].strip()
        if val:
            return val
    # Priority 2: LaTeX \boxed{...} with proper brace matching
    boxed = _extract_boxed(text)
    if boxed is not None:
        return boxed
    return None


def _build_history_block(query: str, store: Optional["SkillStore"]) -> str:
    if store is None:
        return "(no historical workflows available)"
    try:
        records = store.retrieve_sync_workflows(query, top_k=3)
    except Exception:
        records = []
    return store.build_workflow_prompt(records)


def _summarize_workflow_from_trace(trace: ExecTrace) -> str:
    snippets: List[str] = []
    for step in trace.steps:
        step_type = step.get("type", "")
        content = (step.get("content") or "").strip()
        if not content:
            continue
        if step_type in {"assistant_raw", "code", "exec_output"}:
            snippets.append(content[:240])
        if len(snippets) >= 4:
            break
    if trace.final_answer:
        snippets.append(f"Final answer: {trace.final_answer}")
    merged = "\n".join(snippets).strip()
    return merged[:900]


def _build_skill_descriptions(skills: List["Skill"]) -> str:
    if not skills:
        return "(none — solve entirely from scratch)"
    parts = []
    for sk in skills:
        dep_str = f" (uses: {', '.join(sk.dependencies)})" if sk.dependencies else ""
        parts.append(
            f"### `{sk.name}`{dep_str}\n"
            f"{sk.description}"
        )
    return "\n\n".join(parts)


def _make_tool_invocation_code(tool_name: str, raw_arguments: str) -> str:
    raw_arguments = (raw_arguments or "").strip()
    if raw_arguments:
        return f"__tool_result = {tool_name}({raw_arguments})\nprint(__tool_result)"
    return f"__tool_result = {tool_name}()\nprint(__tool_result)"


# ── Main TIR solver ───────────────────────────────────────────────────────────

async def solve(
    query: str,
    skills: List["Skill"],
    llm_config: str = AGENT_MODEL,
    store: Optional["SkillStore"] = None,
    resume_state: Optional[Dict] = None,
    system_prompt_template: Optional[str] = None,
    plan_context: str = "",
    on_trace_update: Optional[Callable[[ExecTrace], None]] = None,
    solver_mode: str = "tir",
) -> ExecTrace:
    if solver_mode == "oneshot":
        return await _solve_oneshot(
            query=query,
            skills=skills,
            llm_config=llm_config,
            store=store,
            system_prompt_template=system_prompt_template,
            plan_context=plan_context,
            on_trace_update=on_trace_update,
        )
    return await _solve_tir(
        query=query,
        skills=skills,
        llm_config=llm_config,
        store=store,
        resume_state=resume_state,
        system_prompt_template=system_prompt_template,
        plan_context=plan_context,
        on_trace_update=on_trace_update,
    )


async def _solve_tir(
    query: str,
    skills: List["Skill"],
    llm_config: str = AGENT_MODEL,
    store: Optional["SkillStore"] = None,
    resume_state: Optional[Dict] = None,
    system_prompt_template: Optional[str] = None,
    plan_context: str = "",
    on_trace_update: Optional[Callable[[ExecTrace], None]] = None,
) -> ExecTrace:
    """
    Solve *query* using TIR (Tool-Integrated Reasoning).

    The LLM (with thinking mode enabled) drives the conversation and decides
    when to call the `python_interpreter` tool.  All tool results are fed back
    so the model accumulates computation across calls.

    Skills are pre-loaded into the sandbox; only their signatures appear in
    the system prompt to avoid context pollution.

    If *resume_state* is provided the solver rebuilds the sandbox by replaying
    prior code blocks and continues from the saved message history.

    If *system_prompt_template* is provided it replaces the default SOLVE_SYSTEM.
    The template must contain a `{skills_descriptions}` placeholder.
    """
    from app.llm import LLM
    from academic.skill_store import Skill, SkillStore  # noqa

    trace = ExecTrace(query=query, solver_mode="tir")
    trace.plan_context = plan_context

    def _emit_update() -> None:
        if on_trace_update is not None:
            on_trace_update(trace)

    # Resolve skill dependencies
    if store and skills:
        skills = store.resolve_with_deps(skills)

    # Build sandbox and pre-load skill code
    sandbox = PersistentSandbox()
    for sk in skills:
        sandbox.preload(_instrument_skill_code(sk))

    skills_desc, _ = _prepare_skill_prompt_blocks(skills)
    available_tools = [PYTHON_INTERPRETER_TOOL] + [_make_skill_tool(sk) for sk in skills]

    historical_workflows = _build_history_block(query, store)
    system = (system_prompt_template or SOLVE_SYSTEM).format(
        skills_descriptions=skills_desc,
        historical_workflows=historical_workflows,
        plan_context=plan_context or "(no explicit plan was produced)",
    )

    # ── Resume from partial state or start fresh ──────────────────────────
    start_step = 0
    if resume_state and resume_state.get("messages"):
        for cb in resume_state.get("code_blocks", []):
            await sandbox.run(cb)  # rebuild namespace
        trace.steps = list(resume_state.get("steps", []))
        trace.code_blocks = list(resume_state.get("code_blocks", []))
        trace.outputs = list(resume_state.get("outputs", []))
        trace.reasoning_traces = list(resume_state.get("reasoning_traces", []))
        trace.total_tokens = resume_state.get("total_tokens", 0)
        trace.completion_tokens = resume_state.get("completion_tokens", 0)
        messages = list(resume_state["messages"])
        start_step = resume_state.get("step", 0)
        logger.info(f"  ↻ Resuming from step {start_step}")
    else:
        messages = [{"role": "user", "content": f"Solve: {query}"}]
        trace.messages.append({"role": "system", "content": system})
        trace.messages.append(messages[0].copy())
        _emit_update()

    llm = LLM(config_name=llm_config)
    tokens_before = llm.total_input_tokens + llm.total_completion_tokens
    text_only_count = 0  # consecutive text responses without tool calls
    completion_before = llm.total_completion_tokens

    for step in range(start_step, MAX_AGENT_STEPS):
        logger.info(f"Step {step+1}/{MAX_AGENT_STEPS} — TIR call (msgs={len(messages)})...")

        response_msg = await _ask_tool_with_retry(
            llm, messages, system, trace, tools=available_tools
        )
        if response_msg is None:
            # Shelved — save state for later resume
            trace.partial_state = {
                "messages": list(messages),
                "code_blocks": list(trace.code_blocks),
                "outputs": list(trace.outputs),
                "steps": list(trace.steps),
                "reasoning_traces": list(trace.reasoning_traces),
                "total_tokens": trace.total_tokens,
                "completion_tokens": trace.completion_tokens,
                "step": step,
            }
            break

        # ── Extract visible content and any tool calls ────────────────────
        # Note: hidden reasoning_content (enable_thinking) is intentionally OFF.
        # The model writes its reasoning in the visible content field (ReAct),
        # which persists in conversation history across turns.
        content = response_msg.content or ""
        tool_calls = response_msg.tool_calls or []

        # Capture any hidden reasoning that may still arrive (e.g. if API ignores
        # enable_thinking=False), but we no longer rely on it.
        reasoning = (
            getattr(response_msg, "reasoning_content", None)
            or (response_msg.model_extra or {}).get("reasoning_content", "")
            or ""
        )
        if reasoning:
            trace.reasoning_traces.append(reasoning)
            trace.steps.append({"type": "thinking", "content": reasoning})
            _emit_update()

        # Log visible content preview
        if content:
            logger.info(f"  💬 Content ({len(content)} chars): {content[:120]!r}")

        # ── Append assistant turn to conversation ─────────────────────────
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            asst_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]
        messages.append(asst_msg)
        asst_trace_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": content,        # visible reasoning lives here
            "thinking": reasoning,     # hidden reasoning if any
        }
        # Preserve tool_calls so the saved conversation is fully replayable
        if tool_calls:
            asst_trace_msg["tool_calls"] = asst_msg["tool_calls"]
        trace.messages.append(asst_trace_msg)
        trace.steps.append({"type": "assistant_raw", "content": content})
        _emit_update()

        # ── No tool calls → text response → look for ANSWER ──────────────
        if not tool_calls:
            logger.info(f"  📝 Text response: {content[:120]!r}")
            text_only_count += 1
            ans = _extract_answer(content)
            if ans:
                trace.final_answer = ans
                trace.success = True
                break
            # Model gave text with no answer and no tool call.
            # After 2 such responses give up, or ask once for final answer.
            if text_only_count >= 2:
                break  # model is stuck, accept None answer
            # First text-only response without answer → prompt for explicit answer
            messages.append({
                "role": "user",
                "content": "Please state your final integer answer as: ANSWER: <integer>",
            })
            trace.messages.append({
                "role": "user",
                "content": "Please state your final integer answer as: ANSWER: <integer>",
            })
            _emit_update()
        else:
            text_only_count = 0  # reset on tool call

        # ── Execute each tool call ────────────────────────────────────────
        executed_any_tool = False
        for tc in tool_calls:
            if tc.function.name == "python_interpreter":
                try:
                    args = json.loads(tc.function.arguments)
                    code = args.get("code", "")
                except Exception:
                    code = tc.function.arguments

                logger.info(f"  🔧 Running code ({len(code)} chars)...")
                tool_output, skill_counts = await sandbox.run(code)
                trace.code_blocks.append(code)
                trace.outputs.append(tool_output)
                _merge_runtime_skill_counts(trace, skill_counts)
                trace.steps.append({"type": "code", "content": code})
                trace.steps.append({"type": "exec_output", "content": tool_output})
                _emit_update()
                logger.info(f"  📤 Output: {tool_output[:200]!r}")

                # Answer might appear in execution output
                if "[ERROR]" not in tool_output and "[TIMEOUT]" not in tool_output:
                    ans = _extract_answer(tool_output)
                    if ans:
                        trace.final_answer = ans
                        trace.success = True
            elif any(sk.name == tc.function.name for sk in skills):
                try:
                    args = json.loads(tc.function.arguments)
                    raw_arguments = args.get("arguments", "")
                except Exception:
                    raw_arguments = tc.function.arguments

                code = _make_tool_invocation_code(tc.function.name, raw_arguments)
                logger.info(f"  🧰 Running skill tool {tc.function.name}({raw_arguments})")
                tool_output, skill_counts = await sandbox.run(code)
                trace.skill_tool_counts[tc.function.name] = (
                    trace.skill_tool_counts.get(tc.function.name, 0) + 1
                )
                trace.code_blocks.append(code)
                trace.outputs.append(tool_output)
                _merge_runtime_skill_counts(trace, skill_counts)
                trace.steps.append({
                    "type": "skill_tool_call",
                    "content": json.dumps(
                        {"tool": tc.function.name, "arguments": raw_arguments},
                        ensure_ascii=False,
                    ),
                })
                trace.steps.append({"type": "code", "content": code})
                trace.steps.append({"type": "exec_output", "content": tool_output})
                _emit_update()
                logger.info(f"  📤 Output: {tool_output[:200]!r}")

                if "[ERROR]" not in tool_output and "[TIMEOUT]" not in tool_output:
                    ans = _extract_answer(tool_output)
                    if ans:
                        trace.final_answer = ans
                        trace.success = True
            else:
                logger.warning(f"  Unknown tool called: {tc.function.name}")
                tool_output = f"[ERROR] Unknown tool: {tc.function.name}"

            # Feed tool result back into conversation
            # Truncate only for the LLM context window; store full output in trace
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_output[:3000],  # truncated for LLM context
            })
            trace.messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_output,  # full output saved in trace
            })
            executed_any_tool = True
            _emit_update()

        if executed_any_tool and not trace.success:
            messages.append({"role": "user", "content": NEXT_STEP_PROMPT})
            trace.messages.append({"role": "user", "content": NEXT_STEP_PROMPT})
            _emit_update()

        if trace.success:
            break  # already found ANSWER in tool output

    # ── Commit step: if loop ended without an answer, ask for it ─────────
    if not trace.success and not trace.partial_state:
        messages.append({
            "role": "user",
            "content": (
                "Based on your calculations above, please state your final integer answer "
                "clearly in the format: ANSWER: <integer>"
            ),
        })
        trace.messages.append({
            "role": "user",
            "content": "Please state your final integer answer as: ANSWER: <integer>",
        })
        _emit_update()
        commit_response = await _ask_tool_with_retry(llm, messages, system, trace)
        if commit_response is not None:
            commit_content = commit_response.content or ""
            if commit_content:
                logger.info(f"  📋 Commit response ({len(commit_content)} chars): {commit_content[:120]!r}")
                trace.messages.append({"role": "assistant", "content": commit_content, "thinking": ""})
                trace.steps.append({"type": "assistant_raw", "content": commit_content})
                _emit_update()
                ans = _extract_answer(commit_content)
                if ans:
                    trace.final_answer = ans
                    trace.success = True

    # Accurate cumulative token count
    trace.total_tokens = (
        llm.total_input_tokens + llm.total_completion_tokens
    ) - tokens_before
    trace.completion_tokens = llm.total_completion_tokens - completion_before

    # Persist a compact workflow record for future planning reuse.
    if store is not None:
        try:
            from academic.skill_store import WorkflowRecord

            decision = "fresh"
            if historical_workflows != "(no historical workflows available)":
                final_text = "\n".join(
                    step.get("content", "") for step in trace.steps
                    if step.get("type") == "assistant_raw"
                )[-1200:]
                lowered = final_text.lower()
                if "reuse" in lowered and "fragment" in lowered:
                    decision = "reuse_workflow_fragment"
                elif "reuse" in lowered:
                    decision = "reuse_plan"
                elif "adapt" in lowered or "modify" in lowered:
                    decision = "adapt_plan"

            store.add_workflow_record(
                WorkflowRecord(
                    query=query,
                    workflow_summary=_summarize_workflow_from_trace(trace),
                    workflow_plan="\n".join(trace.code_blocks[:2])[:1200],
                    workflow_decision=decision,
                    final_answer=str(trace.final_answer or ""),
                    source_problem=query[:240],
                    retrieved_skills=[sk.name for sk in skills],
                )
            )
        except Exception:
            logger.debug("Failed to persist workflow record", exc_info=True)

    _emit_update()
    return trace


async def _solve_oneshot(
    query: str,
    skills: List["Skill"],
    llm_config: str = AGENT_MODEL,
    store: Optional["SkillStore"] = None,
    system_prompt_template: Optional[str] = None,
    plan_context: str = "",
    on_trace_update: Optional[Callable[[ExecTrace], None]] = None,
) -> ExecTrace:
    from app.llm import LLM

    trace = ExecTrace(query=query, solver_mode="oneshot")
    trace.plan_context = plan_context

    def _emit_update() -> None:
        if on_trace_update is not None:
            on_trace_update(trace)

    if store and skills:
        skills = store.resolve_with_deps(skills)

    _, skills_block = _prepare_skill_prompt_blocks(skills)
    historical_workflows = _build_history_block(query, store)
    system = (system_prompt_template or ONESHOT_SOLVE_SYSTEM).format(
        skills_block=skills_block,
        skills_descriptions=skills_block,
        historical_workflows=historical_workflows,
        plan_context=plan_context or "(no explicit plan was produced)",
    )

    messages = [{"role": "user", "content": f"Solve: {query}"}]
    trace.messages.append({"role": "system", "content": system})
    trace.messages.append(messages[0].copy())
    _emit_update()

    llm = LLM(config_name=llm_config)
    tokens_before = llm.total_input_tokens + llm.total_completion_tokens
    completion_before = llm.total_completion_tokens

    namespace, skill_call_counts = _build_skill_namespace(skills)

    for step in range(MAX_AGENT_STEPS):
        prompt_system = system if step == 0 else ONESHOT_REFINE_SYSTEM.format(output=trace.outputs[-1][:1500])
        try:
            response = await asyncio.wait_for(
                llm.ask(
                    messages=messages,
                    system_msgs=[{"role": "system", "content": prompt_system}],
                ),
                timeout=300,
            )
        except asyncio.TimeoutError:
            trace.steps.append({"type": "llm_error", "content": "LLM request timed out (300s)"})
            break
        except Exception as exc:
            trace.steps.append({"type": "llm_error", "content": str(exc)})
            break

        if not response:
            break

        trace.messages.append({"role": "assistant", "content": response})
        trace.steps.append({"type": "assistant_raw", "content": response})
        _emit_update()

        code = _extract_code(response)
        if not code:
            ans = _extract_answer(response)
            if ans:
                trace.final_answer = ans
                trace.success = True
            break

        trace.code_blocks.append(code)
        trace.steps.append({"type": "code", "content": code})
        output = _safe_exec(code, namespace)
        trace.outputs.append(output)
        trace.steps.append({"type": "exec_output", "content": output})
        _merge_runtime_skill_counts(trace, skill_call_counts)
        _emit_update()

        ans = _extract_answer(output)
        if ans and "[ERROR]" not in output and "[TIMEOUT]" not in output:
            trace.final_answer = ans
            trace.success = True
            break

        messages = [
            {"role": "user", "content": f"Solve: {query}"},
            {"role": "assistant", "content": f"```python\n{code}\n```"},
            {"role": "user", "content": ONESHOT_REFINE_SYSTEM.format(output=output[:1500])},
        ]

    trace.total_tokens = (
        llm.total_input_tokens + llm.total_completion_tokens
    ) - tokens_before
    trace.completion_tokens = llm.total_completion_tokens - completion_before

    if store is not None:
        try:
            from academic.skill_store import WorkflowRecord

            store.add_workflow_record(
                WorkflowRecord(
                    query=query,
                    workflow_summary=_summarize_workflow_from_trace(trace),
                    workflow_plan="\n".join(trace.code_blocks[:2])[:1200],
                    workflow_decision="fresh",
                    final_answer=str(trace.final_answer or ""),
                    source_problem=query[:240],
                    retrieved_skills=[sk.name for sk in skills],
                )
            )
        except Exception:
            logger.debug("Failed to persist workflow record", exc_info=True)

    _emit_update()
    return trace


# ── LLM call helper ───────────────────────────────────────────────────────────

async def _ask_tool_with_retry(
    llm,
    messages: List[Dict],
    system: str,
    trace: ExecTrace,
    tools: Optional[List[Dict[str, Any]]] = None,
    max_timeout_retries: int = LLM_TIMEOUT_RETRIES,
) -> Optional[Any]:
    """
    Call llm.ask_tool() with retry logic:
      - Finite retries on timeout (then shelve)
      - Infinite retries on rate-limit (with back-off)
    Returns the ChatCompletionMessage or None if shelved.
    """
    attempt = 0
    timeout_count = 0
    while True:
        attempt += 1
        try:
            response_msg = await asyncio.wait_for(
                llm.ask_tool(
                    messages=messages,
                    system_msgs=[{"role": "system", "content": system}],
                    tools=tools or [PYTHON_INTERPRETER_TOOL],
                    timeout=LLM_CALL_TIMEOUT,  # passed to httpx / OpenAI client
                ),
                timeout=LLM_CALL_TIMEOUT + 60,  # outer asyncio guard
            )
            return response_msg
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= max_timeout_retries:
                logger.warning(f"  ⏱ Timeout {timeout_count}/{max_timeout_retries} — shelving")
                trace.timed_out = True
                trace.steps.append({
                    "type": "timeout_shelved",
                    "content": f"Shelved after {max_timeout_retries} timeout retries",
                })
                return None
            wait = min(30 * timeout_count, 120)
            logger.warning(
                f"  ⏱ Timeout {timeout_count}/{max_timeout_retries}, retrying in {wait}s..."
            )
            trace.steps.append({
                "type": "retry",
                "content": f"Timeout {timeout_count}/{max_timeout_retries}, wait {wait}s",
            })
            await asyncio.sleep(wait)
        except Exception as e:
            err_str = str(e)
            is_rate_limit = (
                "429" in err_str
                or "rate" in err_str.lower()
                or "速率" in err_str
                or type(e).__name__ == "RateLimitError"
            )
            if is_rate_limit:
                wait = min(RATE_LIMIT_BASE_WAIT * attempt, 300)
                logger.warning(f"  ⚡ Rate limit attempt {attempt}, waiting {wait}s...")
                trace.steps.append({
                    "type": "rate_limit",
                    "content": f"Rate limit attempt {attempt}: {err_str[:200]}. Wait {wait}s",
                })
                await asyncio.sleep(wait)
            else:
                logger.error(f"  ✖ LLM error: {err_str[:200]}")
                trace.steps.append({"type": "llm_error", "content": err_str[:500]})
                return None
