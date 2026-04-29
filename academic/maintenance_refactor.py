from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from academic.skill_store import Skill, SkillStore
from academic.tester import test_skill


@dataclass
class MaintenanceRefactorConfig:
    enabled: bool = False
    mode: str = "filtered_only"
    max_skills: int = 24
    min_skills: int = 4
    min_pair_similarity: float = 0.18
    max_pairs: int = 32
    per_epoch_budget_s: float = 90.0
    min_new_skills_since_last_refactor: int = 2


@dataclass
class MaintenanceRefactorStats:
    enabled: bool
    attempted: bool = False
    stopped_early: bool = False
    stop_reason: str = ""
    runtime_s: float = 0.0
    n_input_skills: int = 0
    n_pairs_considered: int = 0
    n_pairs_skipped: int = 0
    n_candidate_groups: int = 0
    n_shared_helpers: int = 0
    n_skills_rewritten: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)


def _tokens(text: str) -> set[str]:
    return {
        tok for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", (text or "").lower())
        if tok not in {"def", "return", "true", "false", "none"}
    }


def _sim(a: Skill, b: Skill) -> float:
    ta = _tokens(f"{a.name}\n{a.description}\n{a.code}")
    tb = _tokens(f"{b.name}\n{b.description}\n{b.code}")
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def _extract_signature(code: str) -> Optional[Tuple[str, str, str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            args = [arg.arg for arg in node.args.args]
            if not args:
                return None
            body_lines = code.splitlines()
            if len(body_lines) < 2:
                return None
            return node.name, args[0], "\n".join(body_lines[1:])
    return None


def _rewrite_with_shared(skill: Skill, shared_name: str, first_arg: str) -> Optional[str]:
    sig = _extract_signature(skill.code)
    if sig is None:
        return None
    fn_name, arg0, _ = sig
    if arg0 != first_arg:
        return None
    return (
        f"def {fn_name}({', '.join(arg.arg for arg in ast.parse(skill.code).body[0].args.args)}):\n"
        f"    return {shared_name}({first_arg})\n"
    )


def run_maintenance_refactor(store: SkillStore, config: MaintenanceRefactorConfig) -> MaintenanceRefactorStats:
    stats = MaintenanceRefactorStats(enabled=config.enabled, n_input_skills=len(store.skills))
    if not config.enabled:
        return stats
    if len(store.skills) < config.min_skills:
        stats.stop_reason = "skip_too_few_skills"
        stats.metadata = {"skipped": True, "mode": config.mode}
        return stats

    start = time.monotonic()
    stats.attempted = True
    skills = list(store.skills)[:config.max_skills]
    pair_scores: List[Tuple[float, Skill, Skill]] = []
    total_pairs = 0
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            total_pairs += 1
            if time.monotonic() - start > config.per_epoch_budget_s:
                stats.stopped_early = True
                stats.stop_reason = "budget_exceeded_pre_grouping"
                stats.runtime_s = round(time.monotonic() - start, 3)
                stats.n_pairs_skipped = total_pairs - len(pair_scores)
                return stats
            sim = _sim(skills[i], skills[j])
            if sim >= config.min_pair_similarity:
                pair_scores.append((sim, skills[i], skills[j]))
    pair_scores.sort(key=lambda item: item[0], reverse=True)
    pair_scores = pair_scores[:config.max_pairs]
    stats.n_pairs_considered = len(pair_scores)
    stats.n_pairs_skipped = max(total_pairs - len(pair_scores), 0)

    grouped: Dict[str, List[Skill]] = {}
    for _, sa, sb in pair_scores:
        key = f"{sa.name}|{sb.name}"
        grouped[key] = [sa, sb]
    stats.n_candidate_groups = len(grouped)

    for key, group in grouped.items():
        if time.monotonic() - start > config.per_epoch_budget_s:
            stats.stopped_early = True
            stats.stop_reason = "budget_exceeded_rewriting"
            break
        a, b = group
        sig_a = _extract_signature(a.code)
        sig_b = _extract_signature(b.code)
        if sig_a is None or sig_b is None:
            continue
        _, arg_a, body_a = sig_a
        _, arg_b, body_b = sig_b
        if arg_a != arg_b or body_a.strip() != body_b.strip():
            continue
        shared_name = f"_shared_{a.name}_{b.name}"
        shared_code = f"def {shared_name}({arg_a}):\n" + "\n".join(f"    {line}" for line in body_a.splitlines())
        rewritten = []
        for sk in (a, b):
            new_code = _rewrite_with_shared(sk, shared_name, arg_a)
            if not new_code:
                rewritten = []
                break
            candidate = Skill(
                name=sk.name,
                description=sk.description,
                code=shared_code + "\n\n" + new_code,
                source_problems=list(sk.source_problems),
                test_queries=list(sk.test_queries),
                test_code=sk.test_code,
            )
            if not test_skill(candidate, store).passed:
                rewritten = []
                break
            rewritten.append(candidate)
        if not rewritten:
            continue
        shared_skill = Skill(
            name=shared_name,
            description=f"Shared helper extracted from {a.name} and {b.name}",
            code=shared_code,
            source_problems=list(dict.fromkeys(a.source_problems + b.source_problems)),
        )
        store.add(shared_skill)
        stats.n_shared_helpers += 1
        for candidate in rewritten:
            store.add(candidate)
            stats.n_skills_rewritten += 1

    stats.runtime_s = round(time.monotonic() - start, 3)
    stats.metadata = {"mode": config.mode}
    return stats
