"""
tester.py — Skill verification module.

Tests skills in a sandboxed environment to verify:
  1. Syntax correctness (valid Python AST)
  2. Load correctness (exec without errors, with dependencies)
  3. Test-case assertions (if test_code is available)

Returns a TestResult with pass/fail and diagnostic info.
"""
from __future__ import annotations

import ast
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from academic.skill_store import Skill, SkillStore


@dataclass
class TestResult:
    skill_name: str
    passed: bool
    syntax_ok: bool = True
    load_ok: bool = True
    test_ok: bool = True
    error: str = ""


def test_skill(skill: Skill, store: SkillStore) -> TestResult:
    """Run all checks on *skill*.  Dependencies are loaded from *store*.
    
    Detects dependencies by parsing the skill code against the current store,
    even though the skill hasn't been added to the store yet.
    """
    result = TestResult(skill_name=skill.name, passed=False)

    # 1. Syntax check
    try:
        ast.parse(skill.code)
    except SyntaxError as e:
        result.syntax_ok = False
        result.error = f"SyntaxError: {e}"
        return result

    # Detect dependencies before loading (since skill isn't in store yet)
    called = _detect_calls(skill.code)
    dep_names = [n for n in called if store.get(n) is not None and n != skill.name]

    # 2. Load check — exec skill + dependencies in a fresh namespace
    namespace: Dict = {"__builtins__": __builtins__}
    try:
        # Load dependencies first (topological order)
        deps = []
        for dn in dep_names:
            deps.extend(_collect_deps_by_name(dn, store))
        if deps:
            ordered = store.topological_sort(deps)
            for dep in ordered:
                exec(dep.code, namespace)
        # Load the skill itself
        exec(skill.code, namespace)
    except Exception as e:
        result.load_ok = False
        result.error = f"LoadError: {e}"
        return result

    # 3. Test-case assertions
    if skill.test_code:
        try:
            exec(skill.test_code, namespace)
        except AssertionError as e:
            result.test_ok = False
            result.error = f"AssertionError in test_code: {e}"
            return result
        except Exception as e:
            result.test_ok = False
            result.error = f"TestError: {e}"
            return result

    result.passed = True
    return result


def test_stale_skills(store: SkillStore) -> List[TestResult]:
    """Test all stale skills.  Clear stale flag on pass, rollback on fail."""
    results = []
    for sk in store.get_stale_skills():
        tr = test_skill(sk, store)
        if tr.passed:
            store.clear_stale(sk.name)
        else:
            # Rollback to pre-stale version
            if sk.rollback():
                store.clear_stale(sk.name)
                tr.error += " (rolled back to previous version)"
        results.append(tr)
    return results


def _detect_calls(code: str) -> set:
    """Parse code AST to find all function call names."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def _collect_deps_by_name(name: str, store: SkillStore) -> List[Skill]:
    """Recursively collect dependency Skill objects starting from *name*."""
    visited: set = set()
    deps: List[Skill] = []

    def _walk(n: str):
        if n in visited:
            return
        visited.add(n)
        sk = store.get(n)
        if sk is None:
            return
        for d in sk.dependencies:
            _walk(d)
        deps.append(sk)

    _walk(name)
    return deps
