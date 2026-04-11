"""
skill_store.py — In-memory skill library with version control, dependency
tracking, and lazy-update support.

Key mechanisms (v2):
  - Version control: every update pushes the old version to `history`
  - Skill composition: skills can call each other; `dependencies` is auto-detected via AST
  - Lazy update: when a skill is updated, all dependents are marked `stale`
  - Persistence: JSON-based, backward compatible with v1 format
"""
from __future__ import annotations

import ast
import json
import math
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SkillVersion:
    """Snapshot of a skill at a specific version."""
    version: int
    code: str
    description: str
    timestamp: float = 0.0


@dataclass
class Skill:
    """One reusable Python helper function with version history."""
    name: str
    description: str
    code: str                       # full function source
    source_problems: List[str] = field(default_factory=list)
    version: int = 1
    usage_count: int = 0
    success_count: int = 0
    # ── v2 additions ──────────────────────────────────────────────────────
    history: List[Dict] = field(default_factory=list)      # serialized SkillVersion dicts
    dependencies: List[str] = field(default_factory=list)   # names of skills this one calls
    stale: bool = False                                     # True when a dependency was updated
    test_code: str = ""                                     # assertion code for testing

    @property
    def success_rate(self) -> float:
        return self.success_count / max(self.usage_count, 1)

    def as_import_block(self) -> str:
        """Return code ready to exec() so the function is available."""
        return self.code

    def rollback(self) -> bool:
        """Revert to the previous version.  Returns True if successful."""
        if not self.history:
            return False
        prev = self.history.pop()
        # Save current as a "rolled-forward" snapshot (don't push to history)
        self.version = prev["version"]
        self.code = prev["code"]
        self.description = prev["description"]
        self.stale = False
        return True


# ── Skill Store ───────────────────────────────────────────────────────────────

class SkillStore:
    """Skill library with TF-IDF retrieval, version control, and dependency tracking."""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}

    # ── CRUD ──────────────────────────────────────────────────────────────
    def add(self, skill: Skill) -> None:
        """Add or update a skill.  On update: pushes old version to history,
        bumps version, re-detects dependencies, marks dependents stale."""
        existing = self._skills.get(skill.name)
        if existing:
            # Push old version to history
            existing.history.append({
                "version": existing.version,
                "code": existing.code,
                "description": existing.description,
                "timestamp": time.time(),
            })
            existing.code = skill.code
            existing.description = skill.description
            existing.version += 1
            existing.source_problems.extend(skill.source_problems)
            if skill.test_code:
                existing.test_code = skill.test_code
            existing.stale = False
            # Re-detect dependencies and mark dependents stale
            existing.dependencies = self._detect_dependencies(existing)
            self._mark_dependents_stale(existing.name)
        else:
            skill.dependencies = self._detect_dependencies(skill)
            self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def remove(self, name: str) -> None:
        self._skills.pop(name, None)

    @property
    def skills(self) -> List[Skill]:
        return list(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    # ── Dependency management ─────────────────────────────────────────────

    def _detect_dependencies(self, skill: Skill) -> List[str]:
        """Parse skill code AST to find calls to other skills in the store."""
        try:
            tree = ast.parse(skill.code)
        except SyntaxError:
            return []

        called: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)

        return [n for n in called if n in self._skills and n != skill.name]

    def refresh_all_dependencies(self) -> None:
        """Re-detect dependencies for every skill (useful after bulk load)."""
        for sk in self._skills.values():
            sk.dependencies = self._detect_dependencies(sk)

    def _mark_dependents_stale(self, updated_name: str) -> List[str]:
        """BFS: mark every skill that (transitively) depends on *updated_name*
        as stale.  Returns list of names marked."""
        marked: List[str] = []
        queue: deque[str] = deque([updated_name])
        visited: Set[str] = {updated_name}
        while queue:
            name = queue.popleft()
            for sk in self._skills.values():
                if sk.name in visited:
                    continue
                if name in sk.dependencies:
                    sk.stale = True
                    marked.append(sk.name)
                    visited.add(sk.name)
                    queue.append(sk.name)
        return marked

    def get_stale_skills(self) -> List[Skill]:
        return [sk for sk in self._skills.values() if sk.stale]

    def clear_stale(self, name: str) -> None:
        sk = self._skills.get(name)
        if sk:
            sk.stale = False

    # ── Topological sort ──────────────────────────────────────────────────

    def topological_sort(self, skills: List[Skill]) -> List[Skill]:
        """Sort skills so that dependencies come before dependents.
        Falls back to original order on cycles."""
        name_set = {sk.name for sk in skills}
        in_degree: Dict[str, int] = {sk.name: 0 for sk in skills}
        adj: Dict[str, List[str]] = {sk.name: [] for sk in skills}

        for sk in skills:
            for dep in sk.dependencies:
                if dep in name_set:
                    adj[dep].append(sk.name)
                    in_degree[sk.name] += 1

        queue = deque(n for n, d in in_degree.items() if d == 0)
        order: List[str] = []
        while queue:
            n = queue.popleft()
            order.append(n)
            for nb in adj[n]:
                in_degree[nb] -= 1
                if in_degree[nb] == 0:
                    queue.append(nb)

        if len(order) != len(skills):
            return skills  # cycle — keep original order

        by_name = {sk.name: sk for sk in skills}
        return [by_name[n] for n in order]

    def resolve_with_deps(self, skills: List[Skill]) -> List[Skill]:
        """Given a list of skills, include any missing dependencies from the
        store and return them in topological order."""
        needed: Dict[str, Skill] = {sk.name: sk for sk in skills}
        queue = list(skills)
        while queue:
            sk = queue.pop()
            for dep_name in sk.dependencies:
                if dep_name not in needed:
                    dep = self._skills.get(dep_name)
                    if dep:
                        needed[dep_name] = dep
                        queue.append(dep)
        return self.topological_sort(list(needed.values()))

    # ── Retrieval (TF-IDF cosine) ─────────────────────────────────────────
    def retrieve(self, query: str, top_k: int = 5) -> List[Skill]:
        """Return most relevant skills for *query* using bag-of-words cosine."""
        if not self._skills:
            return []

        query_tokens = _tokenize(query)
        scored: List[tuple] = []
        for skill in self._skills.values():
            doc_text = (
                skill.description + " " + skill.name + " " +
                " ".join(skill.source_problems)
            )
            doc_tokens = _tokenize(doc_text)
            sim = _cosine(query_tokens, doc_tokens)
            scored.append((sim, skill))

        scored.sort(key=lambda t: t[0], reverse=True)
        if len(self._skills) <= top_k:
            return [s for _, s in scored]
        return [s for _, s in scored[:top_k] if _ > 0.0]

    # ── Persistence ───────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        data = [asdict(s) for s in self._skills.values()]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: Path) -> "SkillStore":
        store = cls()
        if path.exists():
            for d in json.loads(path.read_text()):
                store._skills[d["name"]] = Skill(**{
                    k: v for k, v in d.items()
                    if k in Skill.__dataclass_fields__
                })
        store.refresh_all_dependencies()
        return store

    def build_skills_prompt(self, skills: Optional[List[Skill]] = None) -> str:
        """Format skills into a prompt block the agent can reference."""
        target = skills if skills is not None else self.skills
        if not target:
            return "(no skills available — solve from scratch)"
        parts = []
        for sk in target:
            deps_str = f", depends on: {', '.join(sk.dependencies)}" if sk.dependencies else ""
            stale_str = " [STALE]" if sk.stale else ""
            parts.append(
                f"### {sk.name} (v{sk.version}, used {sk.usage_count}x, "
                f"success rate {sk.success_rate:.0%}{deps_str}{stale_str})\n"
                f"{sk.description}\n```python\n{sk.code}\n```"
            )
        return "\n\n".join(parts)


# ── helpers ───────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> Dict[str, float]:
    """Cheap bag-of-words with TF weighting."""
    words = re.findall(r"[a-z_][a-z0-9_]*", text.lower())
    tf: Dict[str, float] = {}
    for w in words:
        tf[w] = tf.get(w, 0) + 1
    total = sum(tf.values()) or 1
    return {w: c / total for w, c in tf.items()}


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    dot = sum(a.get(k, 0) * v for k, v in b.items())
    na = math.sqrt(sum(v * v for v in a.values())) or 1
    nb = math.sqrt(sum(v * v for v in b.values())) or 1
    return dot / (na * nb)
