"""
refactor_engine.py — Pluggable Skill Refactoring algorithms.

Defines:
  - RefactorEngine (abstract base)
  - NaiveRefactorEngine  (literal-code-dedup baseline)
  - DescriptionFirstEngine (LLM-driven: describe, extract, execute-validate)
  - build_llm_caller(config_name) → synchronous (prompt)->str helper
"""

from __future__ import annotations

import asyncio
import dataclasses
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

from academic.refactoring_lab.example_skills import SkillSpec


SkillDict = Dict[str, Any]


# ── Data types ────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class SubFunction:
    name: str
    description: str
    code: str
    source_skills: List[str]


@dataclasses.dataclass
class RejectedMerge:
    candidate_sub_fn: str
    affected_skills: List[str]
    failure_reason: str
    failed_query: Optional[str] = None


@dataclasses.dataclass
class RefactorResult:
    shared_sub_functions: List[SubFunction]
    refactored_skills: List[SkillDict]
    rejected_merges: List[RejectedMerge]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_harness_by_name(
    skills: List[SkillSpec],
) -> Dict[str, Tuple[SkillSpec, List[Callable], List[Tuple[Any, Any]]]]:
    return {s.name: (s, s.harnesses, s.test_queries) for s in skills}


def _approx_equal(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < tol
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_approx_equal(x, y, tol) for x, y in zip(a, b))
    return a == b


def _exec_and_get(code: str, fn_name: str) -> Optional[Callable]:
    ns: Dict[str, Any] = {}
    try:
        exec(compile(code, "<skill>", "exec"), ns)
        return ns.get(fn_name)
    except Exception:
        return None


def _validate(
    skill_dict: SkillDict,
    harness_by_name: Dict[str, Tuple[SkillSpec, List[Callable], List[Tuple[Any, Any]]]],
) -> Tuple[bool, Optional[str]]:
    entry = harness_by_name.get(skill_dict["name"])
    if not entry:
        return False, "unknown skill (no harness)"
    _, harnesses, queries = entry
    fn = _exec_and_get(skill_dict["code"], skill_dict["name"])
    if fn is None:
        return False, "could not exec/find function"
    for (q, expected), h in zip(queries, harnesses):
        try:
            got = h(fn)
        except Exception as e:
            return False, f"{q!r} raised {e!r}"
        if not _approx_equal(got, expected):
            return False, f"{q!r} expected={expected!r} got={got!r}"
    return True, None


# ── Abstract base ────────────────────────────────────────────────────────────

class RefactorEngine(ABC):
    @abstractmethod
    def refactor(self, skills: List[SkillSpec]) -> RefactorResult: ...


# ── Naive baseline: dedup literally identical code ────────────────────────────

class NaiveRefactorEngine(RefactorEngine):
    def refactor(self, skills: List[SkillSpec]) -> RefactorResult:
        bucket: Dict[str, List[str]] = {}
        for s in skills:
            bucket.setdefault(s.code.strip(), []).append(s.name)
        shared = [
            SubFunction(name=f"shared_{i}", description="literal duplicate",
                        code=code, source_skills=names)
            for i, (code, names) in enumerate(bucket.items()) if len(names) > 1
        ]
        return RefactorResult(
            shared_sub_functions=shared,
            refactored_skills=[s.as_dict() for s in skills],
            rejected_merges=[],
        )


# ── Description-First + Execution-Verified ───────────────────────────────────

class DescriptionFirstEngine(RefactorEngine):
    """
    Phases:
      0. Pairwise LLM alignment → cluster skills by common sub-task description.
      1. For each cluster: LLM extracts a standalone sub-function and rewrites
         every member to call it.
      2. Validate every rewritten skill against its test_queries; reject the
         merge (keep originals) if any member fails.
    """

    def __init__(self, llm_call: Optional[Callable[[str], str]] = None):
        self.llm_call = llm_call or (lambda _p: "NONE")

    # ── Phase 0: pairwise yes/no graph, then clique-style clustering ──────────
    def _align(self, skills: List[SkillSpec]) -> List[List[str]]:
        """
        Build a graph of `shares-sub-task` edges, then grow maximal cliques.
        A skill joins a cluster only if it has edges to EVERY current member —
        so one spurious pairwise edge cannot glue two distinct clusters together.
        Each skill belongs to at most one clique in the returned list.
        """
        n = len(skills)
        adj: Dict[int, set] = {i: set() for i in range(n)}
        edge_label: Dict[Tuple[int, int], str] = {}
        for i in range(n):
            for j in range(i + 1, n):
                si, sj = skills[i], skills[j]
                prompt = _ALIGN_PROMPT.format(
                    name_a=si.name, desc_a=si.description, code_a=si.code,
                    name_b=sj.name, desc_b=sj.description, code_b=sj.code,
                )
                resp = self.llm_call(prompt)
                common = _parse_align(resp)
                if common:
                    adj[i].add(j)
                    adj[j].add(i)
                    edge_label[(i, j)] = common

        assigned = [False] * n
        clusters: List[List[str]] = []
        edges_ordered = sorted(
            edge_label.keys(),
            key=lambda p: -(len(adj[p[0]]) + len(adj[p[1]])),
        )
        for (i, j) in edges_ordered:
            if assigned[i] or assigned[j]:
                continue
            clique = {i, j}
            candidates = (adj[i] & adj[j]) - {i, j}
            while candidates:
                k = max(candidates, key=lambda x: len(adj[x] & clique))
                candidates.discard(k)
                if assigned[k]:
                    continue
                # strict clique: k must be adjacent to every current member
                if all((k in adj[m]) for m in clique):
                    clique.add(k)
                    candidates &= adj[k]
            for m in clique:
                assigned[m] = True
            clusters.append([skills[m].name for m in sorted(clique)])

        print(f"[align] positive edges: {len(edge_label)}; "
              f"cliques: {len(clusters)}")
        for c in clusters:
            print(f"[align]   clique = {c}")
        return clusters

    # ── Phase 1: per-cluster, ask LLM to extract sub-function + rewrites ──────
    def _extract(
        self, cluster_skills: List[SkillSpec],
    ) -> Tuple[Optional[SubFunction], List[SkillDict]]:
        skills_text = "\n\n".join(
            f"### {s.name}\n```python\n{s.code}```" for s in cluster_skills
        )
        names = ", ".join(s.name for s in cluster_skills)
        prompt = _EXTRACT_PROMPT.format(skills_text=skills_text, names=names)
        response = self.llm_call(prompt)
        return _parse_extract(response, cluster_skills)

    # ── Main ───────────────────────────────────────────────────────────────────
    def refactor(self, skills: List[SkillSpec]) -> RefactorResult:
        skill_by_name = {s.name: s for s in skills}
        harness_by_name = _build_harness_by_name(skills)
        clusters = self._align(skills)

        shared: List[SubFunction] = []
        refactored: Dict[str, SkillDict] = {s.name: s.as_dict() for s in skills}
        rejected: List[RejectedMerge] = []

        for names in clusters:
            cluster_skills = [skill_by_name[n] for n in names if n in skill_by_name]
            if len(cluster_skills) < 2:
                continue
            sub_fn, rewritten = self._extract(cluster_skills)
            if sub_fn is None:
                rejected.append(RejectedMerge(
                    candidate_sub_fn=f"<extract failed for {names}>",
                    affected_skills=names,
                    failure_reason="LLM extraction returned no parseable sub-function",
                ))
                continue

            # Validate each rewritten skill — execute with sub-function prepended
            all_ok = True
            fail_msg: Optional[str] = None
            failed_skill: Optional[str] = None
            for rsk in rewritten:
                # In the refactored form, the sub-function is NOT duplicated.
                # For validation we prepend it exactly once.
                combined_code = sub_fn.code.rstrip() + "\n\n" + rsk["code"]
                ok, msg = _validate(
                    {"name": rsk["name"], "code": combined_code},
                    harness_by_name,
                )
                if not ok:
                    all_ok = False
                    fail_msg = msg
                    failed_skill = rsk["name"]
                    break

            if all_ok:
                shared.append(sub_fn)
                for rsk in rewritten:
                    refactored[rsk["name"]] = rsk
            else:
                rejected.append(RejectedMerge(
                    candidate_sub_fn=sub_fn.name,
                    affected_skills=[failed_skill] if failed_skill else names,
                    failure_reason="validation failed",
                    failed_query=fail_msg,
                ))
                # originals retained for every member of this cluster

        return RefactorResult(
            shared_sub_functions=shared,
            refactored_skills=list(refactored.values()),
            rejected_merges=rejected,
        )


# ── LLM caller factory ────────────────────────────────────────────────────────

def build_llm_caller(config_name: str) -> Callable[[str], str]:
    """
    Wrap app.llm.LLM.ask into a synchronous (prompt:str)->str callable.
    Uses a dedicated event loop to allow usage from sync context.
    """
    from app.llm import LLM

    async def _ask(prompt: str) -> str:
        llm = LLM(config_name=config_name)
        return await asyncio.wait_for(
            llm.ask(messages=[{"role": "user", "content": prompt}]),
            timeout=180,
        )

    def caller(prompt: str) -> str:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Run in a new loop on a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    return ex.submit(asyncio.run, _ask(prompt)).result()
            return loop.run_until_complete(_ask(prompt))
        except RuntimeError:
            return asyncio.run(_ask(prompt))
        except Exception as e:
            return f"NONE\n# LLM error: {e!r}"

    return caller


# ── Prompt templates ──────────────────────────────────────────────────────────

_ALIGN_PROMPT = """\
You are analysing two Python skills for a refactoring tool.

SKILL A — {name_a}
Description: {desc_a}
```python
{code_a}
```

SKILL B — {name_b}
Description: {desc_b}
```python
{code_b}
```

Decide whether A and B share a MEANINGFUL computational sub-task (a logically
self-contained operation that appears in both, even if implemented with
different variable names, spread over non-contiguous lines, or embedded inside
a loop).

Respond with EXACTLY one line, no preamble:
- If yes: "COMMON: <short phrase ≤12 words naming the shared sub-task>"
- If no:  "NONE"
"""

_EXTRACT_PROMPT = """\
You are refactoring a group of Python skills: {names}.
These skills have been clustered together because they share a common
computational sub-task.

{skills_text}

TASKS:
1. Identify the common sub-task IN ONE SENTENCE.
2. Write ONE standalone Python function implementing that sub-task.
   - Name it starting with `_shared_` using snake_case.
   - Make it pure, with a one-line docstring.
3. Rewrite EACH original skill to CALL the shared function, NOT duplicate it.
   - Keep each skill's original name and signature unchanged.
   - The rewritten skill body MUST NOT contain a `def _shared_` definition;
     assume the shared function is in scope.
   - Leave the rewritten skill as short as reasonably possible.
4. If some skills do NOT actually share the sub-task (mis-clustered), OMIT
   them from the SKILL_REWRITE blocks — they will be kept as originals.

STRICT OUTPUT FORMAT (no prose outside these markers):

SUB_TASK_DESCRIPTION: <one sentence>

SUB_FUNCTION_START
<python source of the shared function; exactly one `def _shared_...`>
SUB_FUNCTION_END

For each skill that should be rewritten:
SKILL_REWRITE_START <skill_name>
<python source of the rewritten skill; must NOT include the shared function>
SKILL_REWRITE_END
"""


# ── Response parsers ──────────────────────────────────────────────────────────

def _parse_align(response: str) -> Optional[str]:
    if not response:
        return None
    for line in response.strip().splitlines():
        s = line.strip()
        if s.upper().startswith("COMMON:"):
            return s[len("COMMON:"):].strip()
        if s.upper() == "NONE":
            return None
    return None


def _canonicalise(desc: str) -> str:
    return " ".join(desc.lower().split())


def _merge_overlapping(clusters: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Merge clusters that share ≥2 members (likely same concept, different wording)."""
    items = list(clusters.items())
    merged: List[Tuple[str, List[str]]] = []
    used = [False] * len(items)
    for i, (di, mi) in enumerate(items):
        if used[i]:
            continue
        group_desc, group_members = di, list(mi)
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, (dj, mj) in enumerate(items):
                if used[j]:
                    continue
                overlap = len(set(group_members) & set(mj))
                if overlap >= 2:
                    for n in mj:
                        if n not in group_members:
                            group_members.append(n)
                    used[j] = True
                    changed = True
        merged.append((group_desc, group_members))
    return dict(merged)


def _parse_extract(
    response: str,
    cluster_skills: List[SkillSpec],
) -> Tuple[Optional[SubFunction], List[SkillDict]]:
    sub_code = _extract_block(response, "SUB_FUNCTION_START", "SUB_FUNCTION_END")
    sub_code = _strip_code_fence(sub_code)
    if not sub_code:
        return None, []
    fn_name = None
    for line in sub_code.splitlines():
        ls = line.strip()
        if ls.startswith("def "):
            fn_name = ls.split("(")[0][4:].strip()
            break
    if fn_name is None:
        return None, []

    # Try to grab SUB_TASK_DESCRIPTION; fall back to blank
    desc = ""
    for line in response.splitlines():
        s = line.strip()
        if s.upper().startswith("SUB_TASK_DESCRIPTION:"):
            desc = s[len("SUB_TASK_DESCRIPTION:"):].strip()
            break

    # Rewrites: only include skills for which a SKILL_REWRITE block exists.
    rewritten: List[SkillDict] = []
    rewritten_names: List[str] = []
    for s in cluster_skills:
        block = _extract_block(
            response, f"SKILL_REWRITE_START {s.name}", "SKILL_REWRITE_END"
        )
        block = _strip_code_fence(block) if block else None
        if block:
            # safety: if LLM accidentally duplicated the sub-function, strip it
            block = _strip_duplicate_sub_def(block, fn_name)
            rewritten.append({"name": s.name, "description": s.description, "code": block})
            rewritten_names.append(s.name)

    if not rewritten:
        return None, []

    sub_fn = SubFunction(
        name=fn_name,
        description=desc or "(no description)",
        code=sub_code,
        source_skills=rewritten_names,
    )
    return sub_fn, rewritten


def _strip_duplicate_sub_def(skill_code: str, sub_fn_name: str) -> str:
    """If the skill body contains `def {sub_fn_name}(...):`, remove that block."""
    lines = skill_code.splitlines()
    out: List[str] = []
    skipping = False
    base_indent = 0
    for ln in lines:
        stripped = ln.lstrip()
        if not skipping and stripped.startswith(f"def {sub_fn_name}("):
            skipping = True
            base_indent = len(ln) - len(stripped)
            continue
        if skipping:
            if ln.strip() == "" or (len(ln) - len(ln.lstrip())) > base_indent:
                continue
            skipping = False
        out.append(ln)
    return "\n".join(out)


def _extract_block(text: str, start_marker: str, end_marker: str) -> Optional[str]:
    if not text:
        return None
    i = text.find(start_marker)
    if i == -1:
        return None
    i += len(start_marker)
    j = text.find(end_marker, i)
    if j == -1:
        return None
    return text[i:j].strip()


def _strip_code_fence(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    s = s.strip()
    if s.startswith("```"):
        # remove first fence line
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    return s
