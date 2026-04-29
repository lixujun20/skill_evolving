"""
aime_dataset.py — Loader for AIME 2024 (train) and AIME 2025 (test).

Sources:
  - AIME 2024: AI-MO/aimo-validation-aime (filter year=2024)
  - AIME 2025: yentinglin/aime_2025
"""
from __future__ import annotations

import random
from typing import List, Optional, Tuple

from academic.pipeline import Problem


def load_aime_2024(
    n: Optional[int] = None,
    seed: int = 42,
) -> List[Problem]:
    """Load AIME 2024 problems (30 total: I + II, 15 each)."""
    from datasets import load_dataset

    ds = load_dataset("AI-MO/aimo-validation-aime", split="train")
    problems: List[Problem] = []
    for row in ds:
        url = row.get("url", "")
        if "2024" not in url:
            continue
        answer = str(row["answer"]).strip()
        # Extract problem number from url for id
        pid = url.split("/")[-1] if "/" in url else f"aime2024_{len(problems)}"
        problems.append(Problem(
            question=row["problem"],
            answer=answer,
            id=pid,
        ))

    rng = random.Random(seed)
    rng.shuffle(problems)
    if n is not None and n < len(problems):
        problems = problems[:n]
    return problems


def load_aime_2025(
    n: Optional[int] = None,
    seed: int = 42,
) -> List[Problem]:
    """Load AIME 2025 problems (30 total: I + II, 15 each)."""
    from datasets import load_dataset

    ds = load_dataset("yentinglin/aime_2025", split="train")
    problems: List[Problem] = []
    for row in ds:
        answer = str(row["answer"]).strip()
        url = row.get("url", "")
        pid = url.split("/")[-1] if url and "/" in url else f"aime2025_{row.get('id', len(problems))}"
        problems.append(Problem(
            question=row["problem"],
            answer=answer,
            id=pid,
        ))

    rng = random.Random(seed)
    rng.shuffle(problems)
    if n is not None and n < len(problems):
        problems = problems[:n]
    return problems


def load_train_test(
    n_train: Optional[int] = None,
    n_test: Optional[int] = None,
    seed: int = 42,
) -> Tuple[List[Problem], List[Problem]]:
    """
    Return (train=AIME2024, test=AIME2025).

    Args:
        n_train: Limit training set size (default: all 30)
        n_test: Limit test set size (default: all 30)
        seed: Random seed for shuffling
    """
    train = load_aime_2024(n=n_train, seed=seed)
    test = load_aime_2025(n=n_test, seed=seed)
    return train, test
