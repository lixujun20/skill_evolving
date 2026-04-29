"""
skill_store.py — In-memory skill library with version control, dependency
tracking, and lazy-update support.

Key mechanisms (v2):
  - Version control: every update pushes the old version to `history`
  - Skill composition: skills can call each other; `dependencies` is auto-detected via AST
  - Lazy update: when a skill is updated, all dependents are marked `stale`
  - Persistence: JSON-based, backward compatible with v1 format
  - Embedding retrieval: uses BigModel embedding-3 for semantic matching (with TF-IDF fallback)
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

logger = logging.getLogger("tool_evolving")


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
    test_queries: List[Tuple[str, Any]] = field(default_factory=list)
    version: int = 1
    usage_count: int = 0
    success_count: int = 0
    # ── v2 additions ──────────────────────────────────────────────────────
    history: List[Dict] = field(default_factory=list)      # serialized SkillVersion dicts
    dependencies: List[str] = field(default_factory=list)   # names of skills this one calls
    stale: bool = False                                     # True when a dependency was updated
    test_code: str = ""                                     # assertion code for testing
    embedding: Optional[List[float]] = field(default=None, repr=False)  # cached embedding vector

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


@dataclass
class WorkflowRecord:
    """Compact historical workflow summary for future planning reuse."""
    query: str
    workflow_summary: str
    workflow_plan: str = ""
    workflow_decision: str = ""
    final_answer: str = ""
    source_problem: str = ""
    retrieved_skills: List[str] = field(default_factory=list)
    timestamp: float = 0.0


# ── Skill Store ───────────────────────────────────────────────────────────────

class SkillStore:
    """Skill library with TF-IDF retrieval, version control, and dependency tracking."""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._workflow_records: List[WorkflowRecord] = []

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
            existing.embedding = None  # clear cached embedding — needs re-embedding
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

    @property
    def workflow_records(self) -> List[WorkflowRecord]:
        return list(self._workflow_records)

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

    # ── Retrieval ─────────────────────────────────────────────────────────

    def _skill_text(self, skill: Skill) -> str:
        """Build the text representation of a skill for embedding/matching."""
        return (
            f"{skill.name}: {skill.description}\n"
            f"source: {' '.join(skill.source_problems[:3])}\n"
            f"code:\n{skill.code}"
        )

    def _workflow_text(self, record: WorkflowRecord) -> str:
        return (
            f"query: {record.query}\n"
            f"decision: {record.workflow_decision}\n"
            f"summary: {record.workflow_summary}\n"
            f"plan:\n{record.workflow_plan}\n"
            f"skills: {' '.join(record.retrieved_skills)}"
        )

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding vector via BigModel API."""
        try:
            from openai import AsyncOpenAI
            from app.config import config
            emb_cfg = config.llm.get("embedding")
            if not emb_cfg:
                return None
            client = AsyncOpenAI(
                api_key=emb_cfg.api_key,
                base_url=emb_cfg.base_url,
            )
            # Truncate to avoid API limits (embedding-3 supports ~8k tokens)
            truncated = text[:8000]
            resp = await client.embeddings.create(
                model=emb_cfg.model,
                input=[truncated],
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding API failed: {e}")
            return None

    async def _get_embeddings_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Batch embedding call for multiple texts."""
        try:
            from openai import AsyncOpenAI
            from app.config import config
            emb_cfg = config.llm.get("embedding")
            if not emb_cfg:
                return [None] * len(texts)
            client = AsyncOpenAI(
                api_key=emb_cfg.api_key,
                base_url=emb_cfg.base_url,
            )
            truncated = [t[:8000] for t in texts]

            # Some embedding backends reject batches larger than 64 items.
            # Chunk requests so retrieval remains usable for medium-sized stores.
            batch_size = 64
            outputs: List[Optional[List[float]]] = []
            for start in range(0, len(truncated), batch_size):
                batch = truncated[start : start + batch_size]
                resp = await client.embeddings.create(
                    model=emb_cfg.model,
                    input=batch,
                )
                outputs.extend(item.embedding for item in resp.data)
            return outputs
        except Exception as e:
            logger.warning(f"Batch embedding failed: {e}")
            return [None] * len(texts)

    async def ensure_embeddings(self) -> None:
        """Compute embeddings for any skills missing them."""
        missing = [s for s in self._skills.values() if s.embedding is None]
        if not missing:
            return
        texts = [self._skill_text(s) for s in missing]
        embeddings = await self._get_embeddings_batch(texts)
        for skill, emb in zip(missing, embeddings):
            if emb is not None:
                skill.embedding = emb
        cached = sum(1 for s in self._skills.values() if s.embedding is not None)
        logger.info(f"Embeddings: {cached}/{len(self._skills)} skills cached")

    async def retrieve(self, query: str, top_k: int = 5) -> List[Skill]:
        """Return most relevant skills using embedding cosine similarity.
        Falls back to TF-IDF if embeddings are unavailable."""
        if not self._skills:
            return []

        if np is None:
            logger.info("NumPy unavailable; falling back to TF-IDF retrieval")
            return self._retrieve_tfidf(query, top_k)

        # Try embedding-based retrieval
        await self.ensure_embeddings()
        query_emb = await self._get_embedding(query)

        if query_emb is not None:
            query_vec = np.array(query_emb)
            scored: List[tuple] = []
            for skill in self._skills.values():
                if skill.embedding is not None:
                    skill_vec = np.array(skill.embedding)
                    sim = float(np.dot(query_vec, skill_vec) / (
                        np.linalg.norm(query_vec) * np.linalg.norm(skill_vec) + 1e-10
                    ))
                    scored.append((sim, skill))
                else:
                    scored.append((0.0, skill))
            scored.sort(key=lambda t: t[0], reverse=True)
            if len(self._skills) <= top_k:
                return [s for _, s in scored]
            return [s for _, s in scored[:top_k] if _ > 0.0]

        # Fallback: TF-IDF bag-of-words
        logger.info("Falling back to TF-IDF retrieval")
        return self._retrieve_tfidf(query, top_k)

    async def retrieve_workflows(self, query: str, top_k: int = 3) -> List[WorkflowRecord]:
        """Return the most relevant historical workflow summaries."""
        if not self._workflow_records:
            return []
        query_tokens = _tokenize(query)
        scored: List[tuple] = []
        for record in self._workflow_records:
            doc_tokens = _tokenize(self._workflow_text(record))
            sim = _cosine(query_tokens, doc_tokens)
            scored.append((sim, record))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [record for score, record in scored[:top_k] if score > 0.0]

    def retrieve_sync_workflows(self, query: str, top_k: int = 3) -> List[WorkflowRecord]:
        if not self._workflow_records:
            return []
        query_tokens = _tokenize(query)
        scored: List[tuple] = []
        for record in self._workflow_records:
            doc_tokens = _tokenize(self._workflow_text(record))
            sim = _cosine(query_tokens, doc_tokens)
            scored.append((sim, record))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [record for score, record in scored[:top_k] if score > 0.0]

    def add_workflow_record(self, record: WorkflowRecord, max_records: int = 400) -> None:
        if not record.query.strip() or not record.workflow_summary.strip():
            return
        self._workflow_records.append(record)
        if len(self._workflow_records) > max_records:
            self._workflow_records = self._workflow_records[-max_records:]

    def build_workflow_prompt(self, records: Optional[List[WorkflowRecord]] = None) -> str:
        target = records if records is not None else self._workflow_records[-3:]
        if not target:
            return "(no historical workflows available)"
        parts = []
        for idx, rec in enumerate(target, start=1):
            skills_str = ", ".join(rec.retrieved_skills[:5]) if rec.retrieved_skills else "(none)"
            parts.append(
                f"### Historical Workflow {idx}\n"
                f"Query: {rec.query[:240]}\n"
                f"Decision: {rec.workflow_decision or 'unknown'}\n"
                f"Skills used: {skills_str}\n"
                f"Summary: {rec.workflow_summary[:500]}\n"
                + (
                    f"Plan:\n```text\n{rec.workflow_plan[:800]}\n```"
                    if rec.workflow_plan.strip() else ""
                )
            )
        return "\n\n".join(parts)

    def retrieve_sync(self, query: str, top_k: int = 5) -> List[Skill]:
        """Synchronous wrapper for retrieve (uses TF-IDF if no event loop)."""
        try:
            loop = asyncio.get_running_loop()
            # If we're in an async context, we can't use run_until_complete
            # Fall back to TF-IDF for sync calls
            return self._retrieve_tfidf(query, top_k)
        except RuntimeError:
            return asyncio.run(self.retrieve(query, top_k))

    def _retrieve_tfidf(self, query: str, top_k: int = 5) -> List[Skill]:
        """TF-IDF bag-of-words retrieval (fallback)."""
        query_tokens = _tokenize(query)
        scored: List[tuple] = []
        for skill in self._skills.values():
            doc_text = self._skill_text(skill)
            doc_tokens = _tokenize(doc_text)
            sim = _cosine(query_tokens, doc_tokens)
            scored.append((sim, skill))

        scored.sort(key=lambda t: t[0], reverse=True)
        if len(self._skills) <= top_k:
            return [s for _, s in scored]
        return [s for _, s in scored[:top_k] if _ > 0.0]

    # ── Persistence ───────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        data = []
        for s in self._skills.values():
            d = asdict(s)
            d.pop("embedding", None)  # don't persist large embedding vectors
            data.append(d)
        payload = {
            "skills": data,
            "workflow_records": [asdict(r) for r in self._workflow_records],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: Path) -> "SkillStore":
        store = cls()
        if path.exists():
            raw = json.loads(path.read_text())
            if isinstance(raw, list):
                skill_items = raw
                workflow_items = []
            else:
                skill_items = raw.get("skills", [])
                workflow_items = raw.get("workflow_records", [])
            for d in skill_items:
                # Older saved skills may not contain newer optional fields
                # such as `test_queries`; dataclass defaults preserve compatibility.
                store._skills[d["name"]] = Skill(**{
                    k: v for k, v in d.items()
                    if k in Skill.__dataclass_fields__
                })
            store._workflow_records = [
                WorkflowRecord(**{
                    k: v for k, v in d.items()
                    if k in WorkflowRecord.__dataclass_fields__
                })
                for d in workflow_items
            ]
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
