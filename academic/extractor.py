"""
extractor.py — LLM-based skill extraction from TIR execution traces.

Analyses a solution trace (code blocks, execution outputs, AND the model's
thinking/reasoning content) and extracts reusable helper functions and
reasoning patterns that could speed up similar future problems.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from academic.config import EXTRACT_MODEL, LLM_CALL_TIMEOUT
from academic.skill_store import Skill


EXTRACT_SYSTEM = """\
You are a code analyst maintaining a reusable skill repository.

Given a problem statement, the solution trace (code + execution outputs), and
optionally the model's internal reasoning, identify computational patterns that
should become standalone helper functions for similar future problems.

## Software-Engineering Norms
1. Correctness first: extract only logic that is directly supported by the trace
   and whose behavior can be checked by concrete tests.
2. Reusability second: make helpers parameterized and domain-general enough to
   help future problems, but do not broaden beyond the evidence.
3. Maintainability third: prefer one focused helper with a clear contract over a
   broad mixed utility. Keep interfaces small, typed by convention, and easy to
   version/refine.

## Rules
1. Extract reusable computational logic such as solving equations, computing
   sequences, number theory operations, combinatorics, geometry calculations,
   symbolic transformations, or a compact reasoning-derived algorithm.
2. Each helper must be a pure Python function (no side effects, no I/O).
3. Give each function a clear name, a one-line docstring, and a narrow contract.
4. Make the function GENERAL (parameterized), not hardcoded to the specific
   problem's values.
5. If you identify a skill that ALREADY EXISTS (listed below), explicitly treat
   it as a refinement only when the trace shows a real contract improvement;
   keep the same function name.
6. Output NO_SKILL if the trace does not support a correct, reusable, testable
   helper.
7. SKILL COMPOSITION: You may build new skills that CALL existing skills, but
   only when the dependency is directly relevant and keeps the new helper simpler.
8. For EACH skill, provide a brief TEST snippet that checks the contract with
   one or two assert statements.
9. REASONING SKILLS: If the model's reasoning shows a clever non-trivial insight
   or algorithm that cannot easily be captured as a Python function, encode it
   as a function whose docstring explains the key mathematical insight. The body
   may contain the algorithmic implementation or a step-by-step comment.

## One-Shot Examples
Good function/interface extraction:
Trace evidence:
- Code repeatedly computes `pow(a, -1, m)` before solving congruences.
- The output confirms `(a * inv) % m == 1`.
Good output:
SKILL_NAME: mod_inverse
SKILL_DESC: Compute a modular inverse with an explicit coprimality contract.
```python
def mod_inverse(a: int, mod: int) -> int:
    'Return x such that (a * x) % mod == 1; requires gcd(a, mod) == 1.'
    return pow(a, -1, mod)
```
SKILL_TEST:
```python
assert (17 * mod_inverse(17, 3120)) % 3120 == 1
assert mod_inverse(3, 11) == 4
```
Bad output: a helper named `solve_problem_17()` hardcoded to the original
numbers. It is correct only for the source task and not reusable.

Good workflow-style computational extraction:
Trace evidence:
- Code advances a Fibonacci-like recurrence by repeated squaring of a 2x2
  transition matrix.
- Parsing and final-answer formatting are unrelated to the reusable part.
Good output:
SKILL_NAME: matmul2_mod
SKILL_DESC: Multiply two 2x2 matrices modulo an integer.
```python
def matmul2_mod(a: list[list[int]], b: list[list[int]], mod: int) -> list[list[int]]:
    'Multiply two 2x2 integer matrices modulo mod.'
    return [
        [(a[0][0] * b[0][0] + a[0][1] * b[1][0]) % mod,
         (a[0][0] * b[0][1] + a[0][1] * b[1][1]) % mod],
        [(a[1][0] * b[0][0] + a[1][1] * b[1][0]) % mod,
         (a[1][0] * b[0][1] + a[1][1] * b[1][1]) % mod],
    ]
```
SKILL_TEST:
```python
assert matmul2_mod([[1, 1], [1, 0]], [[1, 1], [1, 0]], 1000) == [[2, 1], [1, 1]]
```
Bad output: a single helper that parses the original problem text, computes the
recurrence, and formats the final response. That mixes three maintenance scopes.

Good knowledge/rule extraction:
Trace evidence:
- Reasoning converts a polygon with listed vertices into area using the
  shoelace formula.
- The trace never handles arcs or curved boundaries.
Good output:
SKILL_NAME: polygon_area_shoelace
SKILL_DESC: Compute the area of a polygon from ordered vertices using the shoelace formula.
```python
def polygon_area_shoelace(points: list[tuple[float, float]]) -> float:
    'Return the area of a simple polygon whose vertices are given in order.'
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0
```
SKILL_TEST:
```python
assert polygon_area_shoelace([(0, 0), (4, 0), (4, 3), (0, 3)]) == 12.0
```
Bad output: "Use shoelace for every geometry area problem." The evidence only
supports ordered straight-edge polygons.

## Existing Skills
{existing_skills}

## Output Format
For EACH extracted skill, output exactly:

SKILL_NAME: <function_name>
SKILL_DESC: <one-line description>
```python
def <function_name>(...):
    \"\"\"<docstring — include key mathematical insight if reasoning-derived>\"\"\"
    ...
```
SKILL_TEST:
```python
assert <function_name>(<args>) == <expected>
```

Separate multiple skills with a blank line.
If nothing to extract, output: NO_SKILL
"""

REFINE_SYSTEM = """\
You are incrementally refining a previously extracted Python skill after it failed validation or tests.

You will receive:
- the original problem/query
- the current candidate skill code
- a FIXED public test code block
- the tester failure
- optional previous refinement history

Rules:
1. Treat the public test code as FIXED. Do NOT modify, replace, or reinterpret the test.
2. Preserve as much of the previous skill code as possible. Make the smallest incremental edit that fixes the failure.
3. Keep the same skill name unless the original name is clearly invalid Python.
4. Prefer local edits over full rewrites. If only one expression or one branch is wrong, change only that part.
5. Output exactly one skill in the same extractor format.
6. The SKILL_TEST block in your output must repeat the FIXED public test unchanged.
7. Output NO_SKILL only if the candidate is fundamentally unsalvageable.
"""


async def extract_skills(
    query: str,
    code_blocks: List[str],
    outputs: List[str],
    existing_skills_prompt: str = "",
    llm_config: str = EXTRACT_MODEL,
    reasoning_traces: Optional[List[str]] = None,
) -> List[Skill]:
    """
    Analyse a solution trace and return 0+ new/updated Skills.

    *reasoning_traces* are the model's thinking tokens (one per TIR step).
    When provided, the extractor can also surface reasoning-derived insights
    as skills (encoded in the function's docstring and body).
    """
    from app.llm import LLM

    code_text = "\n\n".join(f"# Block {i+1}\n{c}" for i, c in enumerate(code_blocks))
    output_text = "\n---\n".join(o[:500] for o in outputs)

    # Include a summarised view of reasoning (cap at 3000 chars total to avoid
    # blowing the extractor's context with very long thinking traces)
    reasoning_section = ""
    if reasoning_traces:
        combined = "\n\n---\n\n".join(reasoning_traces)
        if len(combined) > 3000:
            combined = combined[:3000] + "\n...[truncated]"
        reasoning_section = f"\n\n## Model Reasoning (thinking traces)\n```\n{combined}\n```"

    system = EXTRACT_SYSTEM.format(existing_skills=existing_skills_prompt or "(none)")
    user_msg = (
        f"## Problem\n{query}\n\n"
        f"## Solution Code\n```python\n{code_text}\n```\n\n"
        f"## Execution Outputs\n```\n{output_text}\n```"
        f"{reasoning_section}\n\n"
        "Extract reusable helper functions (or NO_SKILL):"
    )

    llm = LLM(config_name=llm_config)
    try:
        response = await asyncio.wait_for(
            llm.ask(
                messages=[{"role": "user", "content": user_msg}],
                system_msgs=[{"role": "system", "content": system}],
            ),
            timeout=LLM_CALL_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception):
        return []

    if not response or "NO_SKILL" in response:
        return []

    return _parse_skills(response, source_problem=query)


async def refine_skill_after_test_failure(
    query: str,
    skill: Skill,
    test_error: str,
    fixed_test_code: str,
    existing_skills_prompt: str = "",
    llm_config: str = EXTRACT_MODEL,
    refinement_history: Optional[List[Dict[str, Any]]] = None,
) -> List[Skill]:
    from app.llm import LLM

    system = REFINE_SYSTEM
    history_block = ""
    if refinement_history:
        lines = []
        for idx, item in enumerate(refinement_history[-4:], start=1):
            lines.append(
                f"Attempt {idx}:\n"
                f"Failure: {item.get('test_error', '')}\n"
                f"Candidate code:\n```python\n{item.get('skill_code', '')}\n```"
            )
        history_block = "\n\n## Previous refinement history\n" + "\n\n".join(lines)
    user_msg = (
        f"## Problem\n{query}\n\n"
        f"## Existing Skills\n{existing_skills_prompt or '(none)'}\n\n"
        f"## Candidate Skill\n```python\n{skill.code}\n```\n\n"
        f"## Fixed Public Test\n```python\n{fixed_test_code or '# no test provided'}\n```"
        f"{history_block}\n\n"
        f"## Tester Failure\n{test_error}\n\n"
        "Refine this skill incrementally and return exactly one corrected skill in extractor format."
    )

    llm = LLM(config_name=llm_config)
    try:
        response = await asyncio.wait_for(
            llm.ask(
                messages=[{"role": "user", "content": user_msg}],
                system_msgs=[{"role": "system", "content": system}],
            ),
            timeout=LLM_CALL_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception):
        return []

    if not response or "NO_SKILL" in response:
        return []

    refined = _parse_skills(response, source_problem=query)
    for item in refined:
        item.test_code = fixed_test_code
    return refined


def _parse_skills(text: str, source_problem: str) -> List[Skill]:
    """Parse the LLM's structured output into Skill objects."""
    skills: List[Skill] = []

    # Split on SKILL_NAME markers
    blocks = re.split(r"(?=SKILL_NAME:)", text)
    for block in blocks:
        block = block.strip()
        if not block.startswith("SKILL_NAME:"):
            continue

        name_m = re.search(r"SKILL_NAME:\s*(\S+)", block)
        desc_m = re.search(r"SKILL_DESC:\s*(.+)", block)
        # First code block = skill code; second (after SKILL_TEST) = test code
        code_matches = re.findall(r"```python\s*\n(.*?)```", block, re.DOTALL)
        code = code_matches[0].strip() if code_matches else ""
        test_code = ""
        # Parse test code: code block after SKILL_TEST marker
        test_m = re.search(r"SKILL_TEST:\s*\n```python\s*\n(.*?)```", block, re.DOTALL)
        if test_m:
            test_code = test_m.group(1).strip()
        elif len(code_matches) >= 2:
            test_code = code_matches[1].strip()

        if name_m and code:
            name = name_m.group(1).strip()
            desc = desc_m.group(1).strip() if desc_m else name

            # Basic validation — must contain 'def '
            if "def " not in code:
                continue

            skills.append(Skill(
                name=name,
                description=desc,
                code=code,
                source_problems=[source_problem],
                test_code=test_code,
            ))

    return skills
