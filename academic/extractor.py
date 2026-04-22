"""
extractor.py — LLM-based skill extraction from TIR execution traces.

Analyses a solution trace (code blocks, execution outputs, AND the model's
thinking/reasoning content) and extracts reusable helper functions and
reasoning patterns that could speed up similar future problems.
"""
from __future__ import annotations

import asyncio
import re
from typing import List, Optional

from academic.config import EXTRACT_MODEL, LLM_CALL_TIMEOUT
from academic.skill_store import Skill


EXTRACT_SYSTEM = """\
You are a code analyst specialising in extracting reusable helper functions.

Given a problem statement, the solution trace (code + execution outputs), and
optionally the model's internal reasoning, identify computational patterns that
could be turned into standalone helper functions for similar future problems.

## Rules
1. Be AGGRESSIVE about extraction — extract any computational logic that might
   be reused, such as: solving equations, computing sequences, number theory
   operations, combinatorics, geometry calculations, etc.
2. Each helper must be a pure Python function (no side effects, no I/O).
3. Give each function a clear name and a one-line docstring.
4. Make the function GENERAL (parameterized), not hardcoded to the specific
   problem's values.
5. If you identify a skill that ALREADY EXISTS (listed below), you may output
   an UPDATED version with improvements — keep the same function name.
6. ONLY output NO_SKILL if the solution is trivially simple (e.g., just `print(2 + 3)`).
7. SKILL COMPOSITION: You are ENCOURAGED to build new skills that CALL existing
   skills.  Reference them by name — they will be available at runtime.
8. For EACH skill, also provide a brief TEST snippet (one or two assert statements).
9. REASONING SKILLS: If the model's reasoning shows a clever non-trivial insight
   or algorithm that cannot easily be captured as a Python function, encode it
   as a function whose docstring explains the key mathematical insight. The body
   may contain the algorithmic implementation or a step-by-step comment.

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
