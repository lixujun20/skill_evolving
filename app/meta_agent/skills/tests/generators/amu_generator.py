"""
AMU (Agent Mock User) Query Set Generator.

Generates structured query sets for skill evolution testing.
Each query set contains:
- A skill name and v1.0 seed code stub
- 2-4 queries of increasing complexity in the same skill domain
- Noise skills (other domain skills for realistic retrieval testing)

Constraints enforced:
- Queries within a group share the same skill domain
- Queries build in complexity (basic → intermediate → advanced)
- Long-term sequences interleave groups (A1, B1, A2, B2, ...)
- Each group appears >= 2 times in any long-term sequence
"""
from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any, Optional

from app.llm import LLM
from app.schema import Message


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

_SINGLE_POINT_PROMPT = """You are a test-data generator for an AI skill-evolution system.

Generate a JSON array of {n_skills} educational skill entries.
Each entry represents a DIFFERENT skill domain (e.g., grade analysis, lesson planning, quiz generation,
student attendance tracking, reading comprehension assessment, etc.).

Each entry must have this exact JSON structure:
{{
  "id": "sp_0N",           // sequential id starting at sp_01
  "skill_name": "...",     // snake_case function name
  "seed_code": "...",      // Python function, v1.0 stub — minimal, incomplete implementation
  "query": "...",          // a realistic, moderately complex user request that the stub can't fully handle
  "noise_skills": [        // {n_noise} noise skills from OTHER domains
    {{
      "name": "...",
      "seed_code": "..."   // Python function stub
    }}
  ]
}}

Rules:
- seed_code must be a real Python function with a docstring saying "v1.0 stub"
- query must be specific enough to drive skill evolution (ask for multiple statistics, detailed output, etc.)
- noise_skills must come from domains DIFFERENT from the main skill_name
- All skill domains must be in the EDUCATIONAL technology space
- Return ONLY valid JSON, no markdown, no explanation

Output:
"""

_LONG_TERM_PROMPT = """You are a test-data generator for an AI skill-evolution system.

Generate a JSON object for a long-term skill evolution test with {n_groups} skill groups.
The sequence must INTERLEAVE the groups so queries from the same group appear non-consecutively.
Each group must appear at least 2 times in the sequence.

Output structure:
{{
  "groups": {{
    "A": {{
      "skill_name": "...",
      "seed_code": "...",     // Python function, v1.0 stub
      "queries": [            // exactly {qpg} queries, increasing complexity
        "basic query...",
        "intermediate query...",
        "advanced query..."
      ],
      "noise_skills": [
        {{"name": "...", "seed_code": "..."}}
      ]
    }},
    "B": {{
      "skill_name": "...",
      "seed_code": "...",
      "queries": [
        "basic query...",
        "intermediate query...",
        "advanced query..."
      ],
      "noise_skills": [
        {{"name": "...", "seed_code": "..."}}
      ]
    }}
  }},
  "sequence": [
    {{"seq_id": 1, "group": "A", "query_idx": 0}},
    {{"seq_id": 2, "group": "B", "query_idx": 0}},
    {{"seq_id": 3, "group": "A", "query_idx": 1}},
    {{"seq_id": 4, "group": "B", "query_idx": 1}},
    {{"seq_id": 5, "group": "A", "query_idx": 2}},
    {{"seq_id": 6, "group": "B", "query_idx": 2}}
  ]
}}

Rules:
- Groups A and B must have DIFFERENT skill domains (both in educational technology)
- seed_code must be minimal Python stubs with "v1.0 stub" in docstring
- queries must escalate from basic to advanced within each group
- sequence must interleave groups — no two consecutive entries may be from the same group
- noise_skills for group A come from group B's domain and vice versa
- Return ONLY valid JSON, no markdown, no explanation

Output:
"""


class AMUGenerator:
    """LLM-powered generator for AMU test fixtures."""

    def __init__(self, config_name: str = "default") -> None:
        self.llm = LLM(config_name=config_name)

    async def generate_single_point(
        self,
        n_skills: int = 3,
        n_noise_per_skill: int = 1,
        save: bool = False,
        output_path: Optional[Path] = None,
    ) -> list[dict[str, Any]]:
        """Generate n single-point skill entries."""
        prompt = _SINGLE_POINT_PROMPT.format(n_skills=n_skills, n_noise=n_noise_per_skill)
        response = await self.llm.ask(
            messages=[Message.user_message(prompt)],
            stream=False,
            temperature=0.7,
        )
        data = _parse_json(response, expected_type=list)

        if save:
            path = output_path or (FIXTURES_DIR / "single_point.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return data

    async def generate_long_term(
        self,
        n_groups: int = 2,
        queries_per_group: int = 3,
        save: bool = False,
        output_path: Optional[Path] = None,
    ) -> dict[str, Any]:
        """Generate long-term fixture with groups and interleaved sequence."""
        prompt = _LONG_TERM_PROMPT.format(n_groups=n_groups, qpg=queries_per_group)
        response = await self.llm.ask(
            messages=[Message.user_message(prompt)],
            stream=False,
            temperature=0.7,
        )
        data = _parse_json(response, expected_type=dict)
        _validate_long_term(data)

        if save:
            path = output_path or (FIXTURES_DIR / "long_term.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return data


def _parse_json(text: str, expected_type: type) -> Any:
    """Extract and parse JSON from LLM response."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first and last fence lines
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def _validate_long_term(data: dict) -> None:
    """Validate long-term fixture constraints."""
    groups = data.get("groups", {})
    sequence = data.get("sequence", [])

    if not groups:
        raise ValueError("long_term fixture must have 'groups' key")
    if not sequence:
        raise ValueError("long_term fixture must have 'sequence' key")

    # Check each group appears >= 2 times
    from collections import Counter
    counts = Counter(e["group"] for e in sequence)
    for gk in groups:
        if counts.get(gk, 0) < 2:
            raise ValueError(
                f"Group '{gk}' must appear at least 2 times in sequence, "
                f"got {counts.get(gk, 0)}"
            )

    # Check no two consecutive entries are from the same group
    for i in range(1, len(sequence)):
        if sequence[i]["group"] == sequence[i - 1]["group"]:
            raise ValueError(
                f"Consecutive sequence entries at positions {i-1} and {i} "
                f"are both from group '{sequence[i]['group']}'. "
                "Sequence must interleave groups."
            )
