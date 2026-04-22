"""
math_dataset.py — Loader for MATH-500 problems.

Two backends:
  1. Local parquet  (/home/lixujun/deepscaler/math.parquet)  — DeepScaler format.
  2. HuggingFace dataset (HuggingFaceH4/MATH-500)           — fallback / richer metadata.
"""
from __future__ import annotations

import re
import random
from pathlib import Path
from typing import List, Optional, Tuple

from academic.pipeline import Problem

# ── Local parquet paths ───────────────────────────────────────────────────────
_PARQUET_PATH = Path.home() / "deepscaler" / "math.parquet"
_TRAIN_PARQUET_PATH = Path.home() / "deepscaler" / "train.shuffle.parquet"


def _strip_prompt_suffix(content: str) -> str:
    """Remove DeepScaler's 'Let's think step by step...' training suffix."""
    content = re.sub(
        r"\s*Let'?s think step by step(?: and output the final answer within \\\\?boxed\{\})?\.?\s*$",
        "",
        content,
        flags=re.DOTALL,
    ).strip()
    return content


def _clean_answer_local(raw: str) -> str:
    """Return raw answer as-is (the ~/grading module handles all normalization)."""
    return raw.strip()


def load_from_parquet(
    n: Optional[int] = None,
    seed: int = 42,
    offset: int = 0,
    parquet_path: Optional[Path] = None,
) -> List[Problem]:
    """Load math problems from a local DeepScaler parquet file."""
    import pandas as pd

    path = parquet_path if parquet_path is not None else _PARQUET_PATH
    df = pd.read_parquet(path)
    problems: List[Problem] = []
    for idx, row in df.iterrows():
        content = row["prompt"][0]["content"]
        question = _strip_prompt_suffix(content)
        answer = _clean_answer_local(row["reward_model"]["ground_truth"])
        problems.append(Problem(
            question=question,
            answer=answer,
            id=f"math_{row['extra_info']['index']}",
        ))

    rng = random.Random(seed)
    rng.shuffle(problems)

    if offset:
        problems = problems[offset:]
    if n is not None and n < len(problems):
        problems = problems[:n]

    return problems


def load_train_test(
    n_train: int = 200,
    n_test: int = 200,
    seed: int = 42,
    use_parquet: bool = True,
    parquet_path: Optional[Path] = None,
) -> Tuple[List[Problem], List[Problem]]:
    """
    Split math problems into train and test sets (200/200 by default).

    Uses local parquet by default; falls back to HuggingFace if parquet missing.
    Pass parquet_path to load from an alternative parquet file (e.g. train.shuffle.parquet).
    """
    resolved_path = parquet_path if parquet_path is not None else _PARQUET_PATH
    if use_parquet and resolved_path.exists():
        total = n_train + n_test
        all_problems = load_from_parquet(n=total, seed=seed, parquet_path=resolved_path)
    else:
        all_problems = _load_from_hf(n=n_train + n_test, seed=seed)

    if len(all_problems) < n_train + n_test:
        split = max(1, len(all_problems) - n_test)
        return all_problems[:split], all_problems[split:]
    return all_problems[:n_train], all_problems[n_train:n_train + n_test]


# ── HuggingFace backend (kept for reference / richer metadata) ────────────────

def _clean_answer(raw: str) -> str:
    """Simplify LaTeX answer to a comparable string."""
    ans = raw.strip()
    import re as _re
    m = _re.search(r"\\boxed\{(.+)\}", ans, _re.DOTALL)
    if m:
        ans = m.group(1).strip()
    ans = re.sub(r"\\(?:text|mathrm|mathbf|textbf)\{([^}]*)\}", r"\1", ans)
    ans = re.sub(r"\\(?:left|right)", "", ans)
    ans = re.sub(r"\\[dt]?frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", ans)
    ans = re.sub(r"\\sqrt\{([^}]*)\}", r"sqrt(\1)", ans)
    ans = ans.replace("\\cdot", "*")
    ans = re.sub(r"\\[!,;: ]", "", ans)
    ans = re.sub(r"\\[a-zA-Z]+", "", ans)
    ans = ans.replace("{", "").replace("}", "")
    ans = re.sub(r"\s*,\s*", ",", ans)
    if re.match(r"^\d{1,3}(,\d{3})+$", ans):
        ans = ans.replace(",", "")
    ans = " ".join(ans.split())
    return ans.strip()


def _load_from_hf(
    subjects: Optional[List[str]] = None,
    levels: Optional[List[int]] = None,
    n: Optional[int] = None,
    seed: int = 42,
    offset: int = 0,
) -> List[Problem]:
    """Load from HuggingFace MATH-500 dataset (requires internet / datasets package)."""
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

    rng = random.Random(seed)
    rng.shuffle(problems)
    if offset:
        problems = problems[offset:]
    if n and n < len(problems):
        problems = problems[:n]
    return problems
