"""
math_dataset.py — Loader for the MATH-500 dataset.

Uses HuggingFace `datasets` library to load `HuggingFaceH4/MATH-500`.
MATH-500 has pre-extracted `answer` fields in LaTeX.
"""
from __future__ import annotations

import random
import re
from typing import List, Optional, Tuple

from academic.pipeline import Problem


def _clean_answer(raw: str) -> str:
    """Simplify LaTeX answer to a comparable string."""
    ans = raw.strip()
    # Remove \boxed{...}
    import re as _re
    m = _re.search(r"\\boxed\{(.+)\}", ans, _re.DOTALL)
    if m:
        ans = m.group(1).strip()
    # Remove \text{}, \mathrm{}, etc.
    ans = re.sub(r"\\(?:text|mathrm|mathbf|textbf)\{([^}]*)\}", r"\1", ans)
    # Remove \left, \right
    ans = re.sub(r"\\(?:left|right)", "", ans)
    # All fraction commands: \frac, \dfrac, \tfrac
    ans = re.sub(r"\\[dt]?frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", ans)
    # \sqrt{x} → sqrt(x)
    ans = re.sub(r"\\sqrt\{([^}]*)\}", r"sqrt(\1)", ans)
    # \cdot → *
    ans = ans.replace("\\cdot", "*")
    # Remove LaTeX thin space \! and \,
    ans = re.sub(r"\\[!,;: ]", "", ans)
    # Remove remaining backslash commands like \pi, etc.
    ans = re.sub(r"\\[a-zA-Z]+", "", ans)
    # Remove braces
    ans = ans.replace("{", "").replace("}", "")
    # Collapse whitespace around commas
    ans = re.sub(r"\s*,\s*", ",", ans)
    # Remove thousand-separator commas: 11,111,100 → 11111100
    if re.match(r"^\d{1,3}(,\d{3})+$", ans):
        ans = ans.replace(",", "")
    # Whitespace
    ans = " ".join(ans.split())
    return ans.strip()


def load_math_problems(
    subjects: Optional[List[str]] = None,
    levels: Optional[List[int]] = None,
    n: Optional[int] = None,
    seed: int = 42,
    offset: int = 0,
) -> List[Problem]:
    """
    Load problems from MATH-500 (single 'test' split of 500 problems).

    Args:
        subjects: Filter by subject (e.g. ['Algebra', 'Number Theory'])
        levels: Filter by difficulty level (1-5)
        n: Number of problems to return (random sample)
        seed: Random seed for reproducibility
        offset: Skip first N matching problems (for train/test split)
    """
    from datasets import load_dataset

    ds = load_dataset("HuggingFaceH4/MATH-500", split="test")

    problems: List[Problem] = []
    for row in ds:
        subj = row.get("subject", "unknown")
        level_num = row.get("level", 1)

        if subjects and not any(
            s.lower().replace("_", " ") in subj.lower()
            or subj.lower() in s.lower().replace("_", " ")
            for s in subjects
        ):
            continue
        if levels and level_num not in levels:
            continue

        answer = _clean_answer(row["answer"])
        problems.append(Problem(
            question=row["problem"],
            answer=answer,
            id=row.get("unique_id", f"math_{len(problems)}"),
        ))

    # Deterministic shuffle then slice
    rng = random.Random(seed)
    rng.shuffle(problems)

    if offset:
        problems = problems[offset:]
    if n and n < len(problems):
        problems = problems[:n]

    return problems


def load_train_test(
    n_train: int = 100,
    n_test: int = 10,
    subjects: Optional[List[str]] = None,
    levels: Optional[List[int]] = None,
    seed: int = 42,
) -> Tuple[List[Problem], List[Problem]]:
    """
    Split MATH-500 into train and test sets.
    First n_train problems for training, next n_test for testing.
    """
    total_needed = n_train + n_test
    all_problems = load_math_problems(
        subjects=subjects, levels=levels, n=total_needed, seed=seed,
    )
    if len(all_problems) < total_needed:
        # Not enough after filtering — take what we have
        split = max(1, len(all_problems) - n_test)
        return all_problems[:split], all_problems[split:]
    return all_problems[:n_train], all_problems[n_train:n_train + n_test]
