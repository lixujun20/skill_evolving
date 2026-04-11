"""
executor.py — LLM-based problem solver with skill injection.

The executor takes a problem + available skills, asks the LLM to write
Python code, executes it in a sandboxed exec(), and returns the answer
plus a trace of what happened.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import re
import signal
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from academic.config import AGENT_MODEL, CODE_EXEC_TIMEOUT, MAX_AGENT_STEPS


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
    success: bool = False


# ── Sandboxed code execution ─────────────────────────────────────────────────

class _Timeout:
    """Context manager using SIGALRM for code timeout (Linux only)."""
    def __init__(self, seconds: int):
        self.seconds = seconds

    def __enter__(self):
        signal.signal(signal.SIGALRM, self._handler)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, *args):
        signal.alarm(0)

    @staticmethod
    def _handler(signum, frame):
        raise TimeoutError("Code execution timed out")


def _safe_exec(code: str, namespace: Dict[str, Any], timeout: int = CODE_EXEC_TIMEOUT) -> str:
    """Execute *code* in *namespace*, capturing stdout. Returns output string."""
    buf = io.StringIO()
    try:
        with _Timeout(timeout), contextlib.redirect_stdout(buf):
            exec(code, namespace)  # noqa: S102 — academic sandbox
    except TimeoutError:
        return "[TIMEOUT] Code execution exceeded time limit"
    except Exception:
        return f"[ERROR]\n{traceback.format_exc()}"
    return buf.getvalue()


# ── Agent executor ────────────────────────────────────────────────────────────

SOLVE_SYSTEM = """\
You are a precise problem solver. You solve problems by writing Python code.

## Available Skills (helper functions)
The following functions are available in your execution environment.
You can call them directly — no import needed.

{skills_block}

## Instructions
1. Analyze the problem carefully.
2. Write Python code that computes the answer.
   - **ALWAYS** prefer using the available skills when they apply.
   - Call skills directly by name (they are pre-loaded in your environment).
   - Only implement from scratch if NO skill is relevant.
3. Keep your code SHORT and DIRECT. If a skill already does the computation,
   just call it — do NOT reimplement the logic.
4. Your code MUST print the final answer as the LAST line of output,
   prefixed with "ANSWER: ". Example: print(f"ANSWER: {{result}}")
5. Do NOT print explanations after ANSWER.

Respond ONLY with a Python code block:
```python
# your code
```
"""

REFINE_SYSTEM = """\
The previous code produced an error or invalid output.
Here is the execution result:

{output}

Fix the code and try again. Respond ONLY with a corrected Python code block.
"""


async def solve(
    query: str,
    skills: List["Skill"],
    llm_config: str = AGENT_MODEL,
    store: Optional["SkillStore"] = None,
) -> ExecTrace:
    """
    Solve *query* using an LLM that writes Python code + available skills.
    Returns an ExecTrace with answer, code, outputs, and token usage.

    If *store* is provided, dependencies are resolved and skills are
    loaded in topological order.
    """
    from app.llm import LLM
    from academic.skill_store import Skill, SkillStore  # noqa: avoid circular at top level

    trace = ExecTrace(query=query)

    # Resolve dependencies + topological sort when store available
    if store and skills:
        skills = store.resolve_with_deps(skills)

    # Build skills prompt
    if skills:
        parts = []
        for sk in skills:
            dep_str = f"  (uses: {', '.join(sk.dependencies)})" if sk.dependencies else ""
            parts.append(f"# {sk.name}: {sk.description}{dep_str}\n{sk.code}")
        skills_block = "\n\n".join(parts)
        # When skills exist, add strong directive to use them
        skills_block += (
            "\n\n# IMPORTANT: The above functions are pre-loaded. "
            "Call them directly by name. Do NOT reimplement them. "
            "Skills may call each other — dependencies are already resolved."
        )
    else:
        skills_block = "(no skills available — solve from scratch)"

    # Prepare execution namespace with skill functions pre-loaded
    # (already in topological order so deps are available)
    namespace: Dict[str, Any] = {"__builtins__": __builtins__}
    for sk in skills:
        try:
            exec(sk.code, namespace)  # noqa: S102
        except Exception:
            pass  # skip broken skills

    system = SOLVE_SYSTEM.format(skills_block=skills_block)
    messages = [{"role": "user", "content": f"Solve: {query}"}]

    llm = LLM(config_name=llm_config)
    tokens_before = llm.total_input_tokens + llm.total_completion_tokens
    completion_before = llm.total_completion_tokens

    for step in range(MAX_AGENT_STEPS):
        try:
            response = await asyncio.wait_for(
                llm.ask(
                    messages=messages,
                    system_msgs=[{"role": "system", "content": system}],
                ),
                timeout=300,
            )
        except asyncio.TimeoutError:
            trace.steps.append({"type": "llm_error", "content": "LLM request timed out (300s)"})
            break
        except Exception as e:
            trace.steps.append({"type": "llm_error", "content": str(e)})
            break

        if not response:
            break

        # Extract code block
        code = _extract_code(response)
        if not code:
            # LLM gave text, not code — treat as final answer
            trace.steps.append({"type": "text", "content": response})
            ans = _extract_answer(response)
            if ans:
                trace.final_answer = ans
                trace.success = True
            break

        trace.code_blocks.append(code)
        trace.steps.append({"type": "code", "content": code})

        # Execute
        output = _safe_exec(code, namespace)
        trace.outputs.append(output)
        trace.steps.append({"type": "output", "content": output})

        # Check for answer in output
        ans = _extract_answer(output)
        if ans and "[ERROR]" not in output and "[TIMEOUT]" not in output:
            trace.final_answer = ans
            trace.success = True
            break

        # Error or no ANSWER → retry with refinement
        if "[ERROR]" in output or "[TIMEOUT]" in output or not ans:
            messages = [
                {"role": "user", "content": f"Solve: {query}"},
                {"role": "assistant", "content": f"```python\n{code}\n```"},
                {"role": "user", "content": REFINE_SYSTEM.format(output=output[:1500])},
            ]
            system = SOLVE_SYSTEM.format(skills_block=skills_block)
            continue

        break

    # Accurate token count from LLM wrapper
    tokens_after = llm.total_input_tokens + llm.total_completion_tokens
    trace.total_tokens = tokens_after - tokens_before
    completion_after = llm.total_completion_tokens
    trace.completion_tokens = completion_after - completion_before

    return trace


def _extract_code(text: str) -> Optional[str]:
    """Pull out the first ```python ... ``` block."""
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def _extract_answer(text: str) -> Optional[str]:
    """Find 'ANSWER: ...' in text."""
    m = re.search(r"ANSWER:\s*(.+)", text)
    if m:
        return m.group(1).strip()
    return None
