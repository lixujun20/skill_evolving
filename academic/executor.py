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
import ctypes
import io
import json
import logging
import re
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


# ── Persistent sandbox ────────────────────────────────────────────────────────

class PersistentSandbox:
    """
    In-process Python sandbox that keeps state across exec() calls.

    Behaves like a Jupyter notebook: variables, imports, and function
    definitions defined in one call are available in the next.

    Uses thread-based timeout (same as the old _safe_exec) so it is
    asyncio-friendly.  Can be swapped for LocalSandboxClient (Docker) for
    stronger isolation at the cost of higher startup overhead.
    """

    def __init__(self):
        self._namespace: Dict[str, Any] = {"__builtins__": __builtins__}

    def preload(self, code: str) -> None:
        """Execute *code* unconditionally to seed the namespace (e.g. skill defs)."""
        # Sanitize signal manipulation first
        code = _sanitize_code(code)
        try:
            exec(code, self._namespace)  # noqa: S102
        except Exception:
            pass

    async def run(self, code: str, timeout: int = CODE_EXEC_TIMEOUT) -> str:
        """Execute *code* asynchronously; returns stdout/stderr as a string."""
        code = _sanitize_code(code)
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _exec_in_namespace, code, self._namespace, timeout),
                timeout=timeout + 15,
            )
        except asyncio.TimeoutError:
            return "[TIMEOUT] Code execution exceeded time limit (outer guard)"


def _sanitize_code(code: str) -> str:
    """Strip signal-module manipulation that would bypass the timeout."""
    code = re.sub(r'(?m)^\s*import\s+signal\b.*$', '# (signal import removed)', code)
    code = re.sub(r'(?m)^\s*from\s+signal\b.*$', '# (signal import removed)', code)
    code = re.sub(r'\bsignal\.(alarm|signal|SIGALRM|SIG_IGN|SIG_DFL)\b', '0', code)
    return code


def _exec_in_namespace(code: str, namespace: Dict[str, Any], timeout: int) -> str:
    """Thread-safe exec with stdout capture and timeout via ctypes async-exception."""
    buf = io.StringIO()
    done = threading.Event()
    error_holder: list = [None]

    _real_print = print
    def _thread_print(*args, **kwargs):
        kwargs.setdefault("file", buf)
        _real_print(*args, **kwargs)
    namespace["print"] = _thread_print

    def _run():
        try:
            exec(code, namespace)  # noqa: S102
        except Exception as exc:
            error_holder[0] = exc
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    if not done.wait(timeout=timeout):
        try:
            if t.ident:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(t.ident),
                    ctypes.py_object(TimeoutError),
                )
        except Exception:
            pass
        return "[TIMEOUT] Code execution exceeded time limit"

    exc = error_holder[0]
    if exc is not None:
        tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return f"[ERROR]\n{tb_str}"
    return buf.getvalue()


# ── Tool definition ───────────────────────────────────────────────────────────

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


# ── Prompts ───────────────────────────────────────────────────────────────────

SOLVE_SYSTEM = """\
You are a precise math competition problem solver using an interactive Python environment.

## Pre-loaded Helper Functions
The following functions are already available in your Python environment.
Call them directly by name — no import or redefinition needed.

{skills_descriptions}

## How to work (ReAct pattern)
At each turn you may:
1. **Write your reasoning** in plain text — analyse the problem, plan your approach,
   interpret previous results. This text is part of the conversation and you will
   see it again in future turns, so use it as a persistent scratchpad.
2. **Call `python_interpreter`** to execute code and get the output.
   The environment is PERSISTENT across calls (like a Jupyter notebook):
   variables, imports, and function definitions from earlier calls are still available.

You can do both in the same turn: write reasoning text, then call the tool.

## Rules
- Use the helper functions above when relevant. Do NOT redefine them.
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

## How to work (ReAct pattern)
At each turn you may:
1. **Write your reasoning** in plain text — analyse the problem, plan your approach,
   interpret previous results. This text is part of the conversation and you will
   see it again in future turns, so use it as a persistent scratchpad.
2. **Call `python_interpreter`** to execute code and get the output.
   The environment is PERSISTENT across calls (like a Jupyter notebook):
   variables, imports, and function definitions from earlier calls are still available.

You can do both in the same turn: write reasoning text, then call the tool.

## Rules
- Use the helper functions above when relevant. Do NOT redefine them.
- Prefer short, focused code calls that build on previous results rather than
  recomputing everything from scratch each turn.
- When you have the final answer, state it clearly in your text using LaTeX boxed notation:
  \\boxed{{your_answer_here}}
- The answer may be an integer, fraction, expression, or other mathematical form.
  Write it exactly as it should appear (e.g. \\boxed{{\\frac{{3}}{{4}}}}, \\boxed{{2\\sqrt{{3}}}}).
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Main TIR solver ───────────────────────────────────────────────────────────

async def solve(
    query: str,
    skills: List["Skill"],
    llm_config: str = AGENT_MODEL,
    store: Optional["SkillStore"] = None,
    resume_state: Optional[Dict] = None,
    system_prompt_template: Optional[str] = None,
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

    trace = ExecTrace(query=query)

    # Resolve skill dependencies
    if store and skills:
        skills = store.resolve_with_deps(skills)

    # Build sandbox and pre-load skill code
    sandbox = PersistentSandbox()
    for sk in skills:
        sandbox.preload(sk.code)

    # Build compact skill descriptions for the system prompt (no code bodies)
    if skills:
        lines = []
        for sk in skills:
            sig = _extract_signature(sk.code)
            dep_str = f" (uses: {', '.join(sk.dependencies)})" if sk.dependencies else ""
            lines.append(f"- `{sig}`{dep_str}: {sk.description}")
        skills_desc = "\n".join(lines)
    else:
        skills_desc = "(none — solve entirely from scratch)"

    system = (system_prompt_template or SOLVE_SYSTEM).format(skills_descriptions=skills_desc)

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

    llm = LLM(config_name=llm_config)
    tokens_before = llm.total_input_tokens + llm.total_completion_tokens
    text_only_count = 0  # consecutive text responses without tool calls
    completion_before = llm.total_completion_tokens

    for step in range(start_step, MAX_AGENT_STEPS):
        logger.info(f"Step {step+1}/{MAX_AGENT_STEPS} — TIR call (msgs={len(messages)})...")

        response_msg = await _ask_tool_with_retry(llm, messages, system, trace)
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
        trace.messages.append({
            "role": "assistant",
            "content": content,        # visible reasoning lives here
            "thinking": reasoning,     # hidden reasoning if any
        })
        trace.steps.append({"type": "assistant_raw", "content": content})

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
        else:
            text_only_count = 0  # reset on tool call

        # ── Execute each tool call ────────────────────────────────────────
        for tc in tool_calls:
            if tc.function.name != "python_interpreter":
                logger.warning(f"  Unknown tool called: {tc.function.name}")
                tool_output = f"[ERROR] Unknown tool: {tc.function.name}"
            else:
                try:
                    args = json.loads(tc.function.arguments)
                    code = args.get("code", "")
                except Exception:
                    code = tc.function.arguments

                logger.info(f"  🔧 Running code ({len(code)} chars)...")
                tool_output = await sandbox.run(code)
                trace.code_blocks.append(code)
                trace.outputs.append(tool_output)
                trace.steps.append({"type": "code", "content": code})
                trace.steps.append({"type": "exec_output", "content": tool_output})
                logger.info(f"  📤 Output: {tool_output[:200]!r}")

                # Answer might appear in execution output
                if "[ERROR]" not in tool_output and "[TIMEOUT]" not in tool_output:
                    ans = _extract_answer(tool_output)
                    if ans:
                        trace.final_answer = ans
                        trace.success = True

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
        commit_response = await _ask_tool_with_retry(llm, messages, system, trace)
        if commit_response is not None:
            commit_content = commit_response.content or ""
            if commit_content:
                logger.info(f"  📋 Commit response ({len(commit_content)} chars): {commit_content[:120]!r}")
                ans = _extract_answer(commit_content)
                if ans:
                    trace.final_answer = ans
                    trace.success = True

    # Accurate cumulative token count
    trace.total_tokens = (
        llm.total_input_tokens + llm.total_completion_tokens
    ) - tokens_before
    trace.completion_tokens = llm.total_completion_tokens - completion_before

    return trace


# ── LLM call helper ───────────────────────────────────────────────────────────

async def _ask_tool_with_retry(
    llm,
    messages: List[Dict],
    system: str,
    trace: ExecTrace,
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
                    tools=[PYTHON_INTERPRETER_TOOL],
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
