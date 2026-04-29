from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "meta_agent"
    / "skills"
    / "tests"
    / "skillsbench_fixture.json"
)


@dataclass
class SkillsBenchFixtureTask:
    task_id: str
    category: str
    tags: List[str]
    difficulty: str
    instruction: str
    skill_docstring: str
    skill_code: str


def load_skillsbench_fixture(max_tasks: Optional[int] = None) -> List[SkillsBenchFixtureTask]:
    data = json.loads(FIXTURE_PATH.read_text())
    tasks = [SkillsBenchFixtureTask(**item) for item in data]
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    return tasks


def summarize_skillsbench_fixture(max_tasks: Optional[int] = None) -> Dict[str, object]:
    tasks = load_skillsbench_fixture(max_tasks=max_tasks)
    category_counts: Dict[str, int] = {}
    difficulty_counts: Dict[str, int] = {}
    for task in tasks:
        category_counts[task.category] = category_counts.get(task.category, 0) + 1
        difficulty_counts[task.difficulty] = difficulty_counts.get(task.difficulty, 0) + 1
    multi_category = {k: v for k, v in category_counts.items() if v >= 2}
    return {
        "fixture_path": str(FIXTURE_PATH),
        "n_tasks": len(tasks),
        "category_counts": dict(sorted(category_counts.items())),
        "difficulty_counts": dict(sorted(difficulty_counts.items())),
        "categories_with_multiple_tasks": dict(sorted(multi_category.items())),
    }
