"""Trace-segment overlap discovery and refactor planning.

This module implements the cheap, benchmark-agnostic half of reusable-skill
refactoring: discover likely pairs of execution segments before any shared
skill exists.  It intentionally does not depend on existing skill names.  The
main signal is token-level n-gram overlap over segment text and error text,
scored with a BM25/TF-IDF-style sparse retrieval model.
"""
from __future__ import annotations

import copy
import math
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from academic.config import EXTRACT_MODEL
from academic.skill_repository.llm_maintenance import _ask_json, _json_block, _refactorer_rule_suffix, _role_json_block, _trim_text
from academic.skill_repository.types import (
    DependencyPin,
    SkillArtifact,
    SkillInterface,
    SkillLineage,
)


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?", re.UNICODE)
STOP_TOKENS = {
    "task_id",
    "user_messages",
    "tool_calls",
    "tool_results",
    "expected_calls",
    "role",
    "content",
    "arguments",
    "actual_arguments",
    "turn_index",
    "actual_name",
    "expected_name",
    "metadata",
    "metrics",
    "trace",
    "user",
    "assistant",
    "tool",
    "tool_call_id",
    "result",
    "error",
    "null",
    "name",
    "type",
    "would",
    "you",
    "please",
    "kind",
    "as",
    "so",
    "could",
    "can",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
}

_EMBED_RETRIEVER = None
_EMBED_RETRIEVER_FAILED = False
_TEXT_EMBED_CACHE: Dict[str, List[float]] = {}


@dataclass
class TraceSegment:
    """A local execution instance candidate mined from a trace."""

    segment_id: str
    task_id: str
    turn_index: int | None
    text: str
    error_text: str = ""
    kind: str = "turn"
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def searchable_text(self) -> str:
        return f"{self.text}\n{self.error_text}".strip()

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def skill_to_overlap_segment(skill: SkillArtifact) -> TraceSegment:
    """Represent an existing skill as a first-class overlap graph node."""

    status = str(skill.status or "active")
    kind = "pending_skill" if status == "pending" or skill.metadata.get("is_pending_skill") else "skill"
    text = "\n".join(
        part
        for part in [
            f"skill_name: {skill.name}",
            f"kind: {skill.kind}",
            f"description: {skill.description}",
            f"body: {skill.body}",
            f"interface: {json_safe(skill.interface.as_dict())}",
            f"allowed_tools: {json_safe(skill.metadata.get('allowed_tools') or [])}",
            f"domains: {json_safe(skill.metadata.get('domains') or [])}",
            f"intent_keywords: {json_safe(skill.metadata.get('intent_keywords') or [])}",
        ]
        if str(part).strip()
    )
    return TraceSegment(
        segment_id=f"skill:{skill.name}:v{skill.version}",
        task_id=f"skill:{skill.name}",
        turn_index=None,
        text=text,
        error_text=json_safe(skill.evidence.harmful_cases[-3:]) if getattr(skill, "evidence", None) else "",
        kind=kind,
        metadata={
            "node_type": kind,
            "skill_name": skill.name,
            "skill_version": skill.version,
            "status": skill.status,
            "is_pending_skill": bool(skill.metadata.get("is_pending_skill") or skill.status == "pending"),
            "source_task_ids": list(skill.metadata.get("source_task_ids") or []),
            "allowed_tools": list(skill.metadata.get("allowed_tools") or []),
            "domains": list(skill.metadata.get("domains") or []),
        },
        raw={"skill": skill.as_dict()},
    )


def json_safe(value: Any) -> str:
    return _trim_text(_json_block(value), limit=2000)


@dataclass
class OverlapEdge:
    """Sparse evidence that two segments may instantiate the same latent skill."""

    source: str
    target: str
    weight: float
    text_score: float
    error_score: float
    embedding_score: float = 0.0
    error_overlap_score: float = 0.0
    combined_weight: float = 0.0
    shared_ngrams: List[str] = field(default_factory=list)
    shared_error_ngrams: List[str] = field(default_factory=list)
    source_task_id: str = ""
    target_task_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OverlapGraph:
    """Candidate graph over trace segments."""

    segments: List[TraceSegment]
    edges: List[OverlapEdge]
    params: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "segments": [item.as_dict() for item in self.segments],
            "edges": [item.as_dict() for item in self.edges],
            "params": dict(self.params),
        }


@dataclass
class OverlapGraphState:
    """Incremental overlap graph cache for online refactor.

    This keeps tokenized sparse docs and pairwise scores so online maintenance
    does not need to rebuild the whole graph from scratch after every task.
    """

    segments: List[TraceSegment] = field(default_factory=list)
    text_docs: List[Counter] = field(default_factory=list)
    error_docs: List[Counter] = field(default_factory=list)
    segment_ids: List[str] = field(default_factory=list)
    segment_task_ids: List[str] = field(default_factory=list)
    embeddings: Dict[str, List[float]] = field(default_factory=dict)
    text_scores: Dict[Tuple[int, int], float] = field(default_factory=dict)
    text_shared: Dict[Tuple[int, int], List[str]] = field(default_factory=dict)
    error_scores: Dict[Tuple[int, int], float] = field(default_factory=dict)
    error_shared: Dict[Tuple[int, int], List[str]] = field(default_factory=dict)
    text_norms: List[float] = field(default_factory=list)
    error_norms: List[float] = field(default_factory=list)
    text_postings: Dict[str, List[int]] = field(default_factory=dict)
    error_postings: Dict[str, List[int]] = field(default_factory=dict)
    text_pair_term_scores: Dict[Tuple[int, int], Dict[str, float]] = field(default_factory=dict)
    error_pair_term_scores: Dict[Tuple[int, int], Dict[str, float]] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "segments": [item.as_dict() for item in self.segments],
            "text_docs": [dict(doc) for doc in self.text_docs],
            "error_docs": [dict(doc) for doc in self.error_docs],
            "segment_ids": list(self.segment_ids),
            "segment_task_ids": list(self.segment_task_ids),
            "embeddings": copy.deepcopy(self.embeddings),
            "text_norms": list(self.text_norms),
            "error_norms": list(self.error_norms),
            "text_postings": {key: list(value) for key, value in self.text_postings.items()},
            "error_postings": {key: list(value) for key, value in self.error_postings.items()},
            "text_pair_term_scores": [
                {
                    "pair": [i, j],
                    "terms": {term: score for term, score in sorted(terms.items())},
                }
                for (i, j), terms in self.text_pair_term_scores.items()
            ],
            "error_pair_term_scores": [
                {
                    "pair": [i, j],
                    "terms": {term: score for term, score in sorted(terms.items())},
                }
                for (i, j), terms in self.error_pair_term_scores.items()
            ],
            "text_scores": [
                {"pair": [i, j], "score": score, "shared": list(self.text_shared.get((i, j), []))}
                for (i, j), score in self.text_scores.items()
            ],
            "error_scores": [
                {"pair": [i, j], "score": score, "shared": list(self.error_shared.get((i, j), []))}
                for (i, j), score in self.error_scores.items()
            ],
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "OverlapGraphState":
        state = cls(
            segments=[
                TraceSegment(**dict(item))
                for item in (payload.get("segments") or [])
                if isinstance(item, dict)
            ],
            text_docs=[Counter(dict(item or {})) for item in (payload.get("text_docs") or [])],
            error_docs=[Counter(dict(item or {})) for item in (payload.get("error_docs") or [])],
            segment_ids=[str(item) for item in (payload.get("segment_ids") or [])],
            segment_task_ids=[str(item) for item in (payload.get("segment_task_ids") or [])],
            embeddings={
                str(key): list(value or [])
                for key, value in dict(payload.get("embeddings") or {}).items()
            },
            text_norms=[float(item or 0.0) for item in (payload.get("text_norms") or [])],
            error_norms=[float(item or 0.0) for item in (payload.get("error_norms") or [])],
            text_postings={
                str(key): [int(idx) for idx in (value or [])]
                for key, value in dict(payload.get("text_postings") or {}).items()
            },
            error_postings={
                str(key): [int(idx) for idx in (value or [])]
                for key, value in dict(payload.get("error_postings") or {}).items()
            },
            params=dict(payload.get("params") or {}),
        )
        for row in (payload.get("text_pair_term_scores") or []):
            if not isinstance(row, dict):
                continue
            pair = row.get("pair") or []
            if len(pair) != 2:
                continue
            key = (int(pair[0]), int(pair[1]))
            state.text_pair_term_scores[key] = {
                str(term): float(score or 0.0)
                for term, score in dict(row.get("terms") or {}).items()
            }
        for row in (payload.get("error_pair_term_scores") or []):
            if not isinstance(row, dict):
                continue
            pair = row.get("pair") or []
            if len(pair) != 2:
                continue
            key = (int(pair[0]), int(pair[1]))
            state.error_pair_term_scores[key] = {
                str(term): float(score or 0.0)
                for term, score in dict(row.get("terms") or {}).items()
            }
        for row in (payload.get("text_scores") or []):
            if not isinstance(row, dict):
                continue
            pair = row.get("pair") or []
            if len(pair) != 2:
                continue
            key = (int(pair[0]), int(pair[1]))
            state.text_scores[key] = float(row.get("score") or 0.0)
            state.text_shared[key] = list(row.get("shared") or [])
        for row in (payload.get("error_scores") or []):
            if not isinstance(row, dict):
                continue
            pair = row.get("pair") or []
            if len(pair) != 2:
                continue
            key = (int(pair[0]), int(pair[1]))
            state.error_scores[key] = float(row.get("score") or 0.0)
            state.error_shared[key] = list(row.get("shared") or [])
        _rehydrate_overlap_graph_state(state)
        return state


@dataclass
class RefactorClique:
    """A small group of mutually related segments considered for LLM refactor."""

    clique_id: str
    segment_ids: List[str]
    edge_weight_sum: float
    edges: List[OverlapEdge] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "clique_id": self.clique_id,
            "segment_ids": list(self.segment_ids),
            "edge_weight_sum": self.edge_weight_sum,
            "edges": [item.as_dict() for item in self.edges],
        }


def tokenize(text: str) -> List[str]:
    return [
        tok.lower()
        for tok in TOKEN_RE.findall(text or "")
        if tok.strip() and tok.lower() not in STOP_TOKENS and len(tok.strip()) > 1
    ]


def _counter_cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for token, value in a.items():
        norm_a += value * value
        dot += value * float(b.get(token, 0.0))
    for value in b.values():
        norm_b += value * value
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def _shared_tokens(a: Counter, b: Counter, *, limit: int = 12) -> List[str]:
    shared = [
        (token, min(float(a[token]), float(b[token])))
        for token in a
        if token in b
    ]
    shared.sort(key=lambda item: (-item[1], item[0]))
    return [token for token, _score in shared[:limit]]


def _embed_text_best_effort(text: str) -> List[float] | None:
    global _EMBED_RETRIEVER, _EMBED_RETRIEVER_FAILED
    compact = _trim_text(text.strip(), limit=8000)
    if not compact:
        return None
    cached = _TEXT_EMBED_CACHE.get(compact)
    if cached:
        return list(cached)
    if _EMBED_RETRIEVER_FAILED:
        return None
    if _EMBED_RETRIEVER is None:
        try:
            from app.meta_agent.skills.retrieval import SkillRetriever

            _EMBED_RETRIEVER = SkillRetriever()
        except Exception:
            _EMBED_RETRIEVER_FAILED = True
            return None
    try:
        embedding = _EMBED_RETRIEVER.generate_embedding(compact)
    except Exception:
        return None
    if not embedding:
        return None
    out = list(embedding)
    _TEXT_EMBED_CACHE[compact] = out
    return list(out)


def _coarse_recall_warning() -> str:
    return (
        "Candidate-skill recall is intentionally coarse. It is based on token overlap and "
        "approximate embeddings, so false positives and mixed-scope matches are expected. "
        "Treat recalled skills only as weak hypotheses. Do not force extraction, reuse, or "
        "merging unless the trace segments themselves support a preserved invariant."
    )


def _clique_query_text(selected_segments: Sequence[TraceSegment]) -> str:
    parts: List[str] = []
    for segment in selected_segments:
        parts.append(segment.searchable_text())
        decision_pattern = str((segment.metadata or {}).get("decision_pattern") or "").strip()
        if decision_pattern:
            parts.append(decision_pattern)
    return "\n".join(part for part in parts if part.strip())


def _coarse_skill_candidates_for_clique(
    *,
    selected_segments: Sequence[TraceSegment],
    existing_skills: Sequence[SkillArtifact],
    top_k: int = 6,
) -> List[Dict[str, Any]]:
    query_text = _clique_query_text(selected_segments)
    if not query_text.strip():
        return []
    query_counter = Counter(tokenize(query_text))
    query_embedding = _embed_text_best_effort(query_text)
    candidates: List[Dict[str, Any]] = []
    for skill in existing_skills:
        # Pending skills are intentionally hidden from executor retrieval, but
        # they are first-class evidence for posterior refactor/extraction.
        if skill.is_disabled() or skill.status in {"rejected", "archived"}:
            continue
        skill_text = skill.retrieval_text()
        skill_counter = Counter(tokenize(skill_text))
        token_similarity = _counter_cosine(query_counter, skill_counter)
        skill_embedding = _embed_text_best_effort(skill_text)
        embedding_similarity = max(
            0.0,
            float(_cosine_similarity(query_embedding, skill_embedding) or 0.0),
        )
        if query_embedding is not None and skill_embedding is not None:
            combined_similarity = 0.55 * token_similarity + 0.45 * embedding_similarity
        else:
            combined_similarity = token_similarity
        candidates.append(
            {
                "name": skill.name,
                "version": skill.version,
                "kind": skill.kind,
                "description": skill.description,
                "source_task_ids": list(skill.metadata.get("source_task_ids") or []),
                "allowed_tools": list(skill.metadata.get("allowed_tools") or []),
                "token_similarity": round(token_similarity, 6),
                "embedding_similarity": round(embedding_similarity, 6),
                "combined_similarity": round(combined_similarity, 6),
                "shared_tokens": _shared_tokens(query_counter, skill_counter),
                "retrieval_warning": _coarse_recall_warning(),
            }
        )
    candidates.sort(
        key=lambda item: (
            -float(item.get("combined_similarity") or 0.0),
            -float(item.get("token_similarity") or 0.0),
            -float(item.get("embedding_similarity") or 0.0),
            str(item.get("name") or ""),
        )
    )
    return candidates[: max(1, top_k)]


def token_ngrams(text: str, *, n_values: Sequence[int] = (2, 3)) -> List[str]:
    toks = tokenize(text)
    grams: List[str] = []
    for n in n_values:
        if len(toks) < n:
            continue
        grams.extend(" ".join(toks[i : i + n]) for i in range(0, len(toks) - n + 1))
    return grams


def discover_overlap_graph(
    segments: Sequence[TraceSegment],
    *,
    top_k_per_segment: int = 8,
    min_weight: float = 0.18,
    max_bucket_size: int = 80,
    n_values: Sequence[int] = (2, 3),
    error_weight: float = 1.7,
    segment_embeddings: Dict[str, List[float]] | None = None,
    alpha: float = 0.45,
    beta: float = 0.35,
    gamma: float = 0.20,
) -> OverlapGraph:
    """Build a candidate graph using sparse + embedding similarity.

    The algorithm is intentionally conservative:
    - repeated low-information n-grams in very large buckets are ignored;
    - segments from the same exact task are allowed but down-selected by top-k;
    - error n-grams get higher weight because they often expose local skill gaps.
    """

    segment_list = list(segments)
    text_docs = [Counter(token_ngrams(seg.text, n_values=n_values)) for seg in segment_list]
    error_docs = [Counter(token_ngrams(seg.error_text, n_values=n_values)) for seg in segment_list]
    text_scores, text_shared = _sparse_pair_scores(text_docs, max_bucket_size=max_bucket_size)
    error_scores, error_shared = _sparse_pair_scores(error_docs, max_bucket_size=max_bucket_size)

    by_pair: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for pair, score in text_scores.items():
        by_pair.setdefault(pair, {})["text_score"] = score
    for pair, score in error_scores.items():
        by_pair.setdefault(pair, {})["error_score"] = score

    raw_edges: List[Tuple[int, int, float, float, float, float, float]] = []
    for (i, j), row in by_pair.items():
        text_score = float(row.get("text_score") or 0.0)
        error_score = float(row.get("error_score") or 0.0)
        sparse_overlap = text_score + error_weight * error_score
        error_overlap_score = min(1.0, error_score * error_weight)
        embedding_score = 0.0
        if segment_embeddings:
            si, sj = segment_list[i], segment_list[j]
            embedding_score = max(
                0.0,
                float(
                    _cosine_similarity(
                        segment_embeddings.get(si.segment_id),
                        segment_embeddings.get(sj.segment_id),
                    ) or 0.0
                ),
            )
        combined_weight = alpha * sparse_overlap + beta * embedding_score + gamma * error_overlap_score
        if combined_weight >= min_weight:
            raw_edges.append((i, j, combined_weight, text_score, error_score, embedding_score, error_overlap_score))

    raw_edges.sort(key=lambda item: item[2], reverse=True)
    selected_counts: Dict[int, int] = defaultdict(int)
    edges: List[OverlapEdge] = []
    for i, j, weight, text_score, error_score, embedding_score, error_overlap_score in raw_edges:
        if selected_counts[i] >= top_k_per_segment or selected_counts[j] >= top_k_per_segment:
            continue
        si, sj = segment_list[i], segment_list[j]
        edges.append(
            OverlapEdge(
                source=si.segment_id,
                target=sj.segment_id,
                weight=round(weight, 6),
                text_score=round(text_score, 6),
                error_score=round(error_score, 6),
                embedding_score=round(embedding_score, 6),
                error_overlap_score=round(error_overlap_score, 6),
                combined_weight=round(weight, 6),
                shared_ngrams=(text_shared.get((i, j)) or [])[:12],
                shared_error_ngrams=(error_shared.get((i, j)) or [])[:12],
                source_task_id=si.task_id,
                target_task_id=sj.task_id,
            )
        )
        selected_counts[i] += 1
        selected_counts[j] += 1

    return OverlapGraph(
        segments=segment_list,
        edges=edges,
        params={
            "top_k_per_segment": top_k_per_segment,
            "min_weight": min_weight,
            "max_bucket_size": max_bucket_size,
            "n_values": list(n_values),
            "error_weight": error_weight,
            "alpha": alpha,
            "beta": beta,
            "gamma": gamma,
            "embedding_enabled": bool(segment_embeddings),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        },
    )


def build_overlap_graph_state(
    segments: Sequence[TraceSegment],
    *,
    max_bucket_size: int = 80,
    n_values: Sequence[int] = (2, 3),
    segment_embeddings: Dict[str, List[float]] | None = None,
) -> OverlapGraphState:
    state = OverlapGraphState(
        params={
            "max_bucket_size": max_bucket_size,
            "n_values": list(n_values),
        }
    )
    update_overlap_graph_state(
        state,
        new_segments=segments,
        segment_embeddings=segment_embeddings,
        max_bucket_size=max_bucket_size,
        n_values=n_values,
    )
    return state


def update_overlap_graph_state(
    state: OverlapGraphState,
    *,
    new_segments: Sequence[TraceSegment],
    segment_embeddings: Dict[str, List[float]] | None = None,
    max_bucket_size: int | None = None,
    n_values: Sequence[int] | None = None,
) -> int:
    max_bucket_size = int(max_bucket_size or state.params.get("max_bucket_size") or 80)
    n_values = tuple(n_values or state.params.get("n_values") or (2, 3))
    segment_embeddings = segment_embeddings or {}
    existing_ids = set(state.segment_ids)
    added_indices: List[int] = []
    for segment in new_segments:
        if segment.segment_id in existing_ids:
            continue
        normalized = copy.deepcopy(segment)
        normalized.metadata = dict(normalized.metadata or {})
        if "node_type" not in normalized.metadata:
            normalized.metadata["node_type"] = "trace_segment"
        state.segments.append(normalized)
        state.segment_ids.append(normalized.segment_id)
        state.segment_task_ids.append(normalized.task_id)
        text_doc = Counter(token_ngrams(normalized.text, n_values=n_values))
        error_doc = Counter(token_ngrams(normalized.error_text, n_values=n_values))
        state.text_docs.append(text_doc)
        state.error_docs.append(error_doc)
        state.text_norms.append(math.sqrt(sum(v * v for v in text_doc.values())) or 1.0)
        state.error_norms.append(math.sqrt(sum(v * v for v in error_doc.values())) or 1.0)
        if normalized.segment_id in segment_embeddings:
            state.embeddings[normalized.segment_id] = list(segment_embeddings[normalized.segment_id] or [])
        existing_ids.add(normalized.segment_id)
        added_indices.append(len(state.segments) - 1)
    if not added_indices:
        return 0
    _update_sparse_scores_incremental(
        docs=state.text_docs,
        norms=state.text_norms,
        postings=state.text_postings,
        pair_term_scores=state.text_pair_term_scores,
        score_map=state.text_scores,
        shared_map=state.text_shared,
        added_indices=added_indices,
        max_bucket_size=max_bucket_size,
    )
    _update_sparse_scores_incremental(
        docs=state.error_docs,
        norms=state.error_norms,
        postings=state.error_postings,
        pair_term_scores=state.error_pair_term_scores,
        score_map=state.error_scores,
        shared_map=state.error_shared,
        added_indices=added_indices,
        max_bucket_size=max_bucket_size,
    )
    state.params.update(
        {
            "max_bucket_size": max_bucket_size,
            "n_values": list(n_values),
        }
    )
    return len(added_indices)


def materialize_overlap_graph(
    state: OverlapGraphState,
    *,
    top_k_per_segment: int = 8,
    min_weight: float = 0.18,
    error_weight: float = 1.7,
    alpha: float = 0.45,
    beta: float = 0.35,
    gamma: float = 0.20,
) -> OverlapGraph:
    segment_list = list(state.segments)
    max_text_score = max(state.text_scores.values(), default=1.0) or 1.0
    max_error_score = max(state.error_scores.values(), default=1.0) or 1.0
    by_pair: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for pair, score in state.text_scores.items():
        by_pair.setdefault(pair, {})["text_score"] = score / max_text_score
    for pair, score in state.error_scores.items():
        by_pair.setdefault(pair, {})["error_score"] = score / max_error_score
    raw_edges: List[Tuple[int, int, float, float, float, float, float]] = []
    for (i, j), row in by_pair.items():
        text_score = float(row.get("text_score") or 0.0)
        error_score = float(row.get("error_score") or 0.0)
        sparse_overlap = text_score + error_weight * error_score
        error_overlap_score = min(1.0, error_score * error_weight)
        embedding_score = 0.0
        si, sj = segment_list[i], segment_list[j]
        if state.embeddings:
            embedding_score = max(
                0.0,
                float(
                    _cosine_similarity(
                        state.embeddings.get(si.segment_id),
                        state.embeddings.get(sj.segment_id),
                    ) or 0.0
                ),
            )
        combined_weight = alpha * sparse_overlap + beta * embedding_score + gamma * error_overlap_score
        if combined_weight >= min_weight:
            raw_edges.append((i, j, combined_weight, text_score, error_score, embedding_score, error_overlap_score))
    raw_edges.sort(key=lambda item: item[2], reverse=True)
    selected_counts: Dict[int, int] = defaultdict(int)
    edges: List[OverlapEdge] = []
    for i, j, weight, text_score, error_score, embedding_score, error_overlap_score in raw_edges:
        if selected_counts[i] >= top_k_per_segment or selected_counts[j] >= top_k_per_segment:
            continue
        si, sj = segment_list[i], segment_list[j]
        edges.append(
            OverlapEdge(
                source=si.segment_id,
                target=sj.segment_id,
                weight=round(weight, 6),
                text_score=round(text_score, 6),
                error_score=round(error_score, 6),
                embedding_score=round(embedding_score, 6),
                error_overlap_score=round(error_overlap_score, 6),
                combined_weight=round(weight, 6),
                shared_ngrams=(state.text_shared.get((i, j)) or [])[:12],
                shared_error_ngrams=(state.error_shared.get((i, j)) or [])[:12],
                source_task_id=si.task_id,
                target_task_id=sj.task_id,
            )
        )
        selected_counts[i] += 1
        selected_counts[j] += 1
    return OverlapGraph(
        segments=segment_list,
        edges=edges,
        params={
            "top_k_per_segment": top_k_per_segment,
            "min_weight": min_weight,
            "max_bucket_size": int(state.params.get("max_bucket_size") or 80),
            "n_values": list(state.params.get("n_values") or [2, 3]),
            "error_weight": error_weight,
            "alpha": alpha,
            "beta": beta,
            "gamma": gamma,
            "embedding_enabled": bool(state.embeddings),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "incremental_state": True,
        },
    )


def _cosine_similarity(a: List[float] | None, b: List[float] | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    return dot / math.sqrt(norm_a * norm_b)


def _sparse_pair_scores(
    docs: Sequence[Counter],
    *,
    max_bucket_size: int,
) -> Tuple[Dict[Tuple[int, int], float], Dict[Tuple[int, int], List[str]]]:
    scores, shared, _ = _sparse_pair_scores_with_terms(docs, max_bucket_size=max_bucket_size)
    return scores, shared


def _sparse_pair_scores_with_terms(
    docs: Sequence[Counter],
    *,
    max_bucket_size: int,
) -> Tuple[Dict[Tuple[int, int], float], Dict[Tuple[int, int], List[str]], Dict[Tuple[int, int], Dict[str, float]]]:
    index: Dict[str, List[int]] = defaultdict(list)
    for idx, doc in enumerate(docs):
        for gram in doc:
            index[gram].append(idx)
    n_docs = max(len(docs), 1)
    pair_scores: Dict[Tuple[int, int], float] = defaultdict(float)
    pair_shared: Dict[Tuple[int, int], List[str]] = defaultdict(list)
    pair_term_scores: Dict[Tuple[int, int], Dict[str, float]] = defaultdict(dict)
    norm = [math.sqrt(sum(v * v for v in doc.values())) or 1.0 for doc in docs]
    for gram, ids in index.items():
        if len(ids) < 2 or len(ids) > max_bucket_size:
            continue
        idf = math.log((n_docs + 1) / (len(ids) + 0.5)) + 1.0
        for a_pos in range(len(ids)):
            i = ids[a_pos]
            for j in ids[a_pos + 1 :]:
                pair = (i, j) if i < j else (j, i)
                score = idf * min(docs[i][gram], docs[j][gram]) / math.sqrt(norm[i] * norm[j])
                pair_scores[pair] += score
                pair_term_scores[pair][gram] = score
                if len(pair_shared[pair]) < 24:
                    pair_shared[pair].append(gram)
    max_score = max(pair_scores.values(), default=1.0)
    if max_score > 0:
        pair_scores = {pair: score / max_score for pair, score in pair_scores.items()}
    return dict(pair_scores), dict(pair_shared), {pair: dict(terms) for pair, terms in pair_term_scores.items()}


def _update_sparse_scores_incremental(
    *,
    docs: Sequence[Counter],
    norms: Sequence[float],
    postings: Dict[str, List[int]],
    pair_term_scores: Dict[Tuple[int, int], Dict[str, float]],
    score_map: Dict[Tuple[int, int], float],
    shared_map: Dict[Tuple[int, int], List[str]],
    added_indices: Sequence[int],
    max_bucket_size: int,
) -> None:
    if not added_indices:
        return
    n_docs = max(len(docs), 1)
    for idx in added_indices:
        doc = docs[idx]
        for gram in doc:
            postings.setdefault(gram, []).append(idx)
    touched_pairs: set[Tuple[int, int]] = set()
    touched_grams: set[str] = set()
    for idx in added_indices:
        for gram in docs[idx]:
            touched_grams.add(gram)
            ids = postings.get(gram) or []
            if len(ids) < 2 or len(ids) > max_bucket_size:
                continue
            idf = math.log((n_docs + 1) / (len(ids) + 0.5)) + 1.0
            for other in ids:
                if other == idx:
                    continue
                pair = (idx, other) if idx < other else (other, idx)
                touched_pairs.add(pair)
                term_scores = pair_term_scores.setdefault(pair, {})
                term_scores[gram] = idf * min(docs[idx][gram], docs[other][gram]) / math.sqrt(norms[idx] * norms[other])
    for gram in touched_grams:
        ids = postings.get(gram) or []
        if len(ids) > max_bucket_size:
            for a_pos in range(len(ids)):
                i = ids[a_pos]
                for j in ids[a_pos + 1 :]:
                    pair = (i, j) if i < j else (j, i)
                    term_scores = pair_term_scores.get(pair)
                    if term_scores and gram in term_scores:
                        term_scores.pop(gram, None)
                        touched_pairs.add(pair)
                        if not term_scores:
                            pair_term_scores.pop(pair, None)
            continue
        if len(ids) < 2:
            continue
        idf = math.log((n_docs + 1) / (len(ids) + 0.5)) + 1.0
        for a_pos in range(len(ids)):
            i = ids[a_pos]
            for j in ids[a_pos + 1 :]:
                pair = (i, j) if i < j else (j, i)
                touched_pairs.add(pair)
                term_scores = pair_term_scores.setdefault(pair, {})
                term_scores[gram] = idf * min(docs[i][gram], docs[j][gram]) / math.sqrt(norms[i] * norms[j])
    if touched_pairs:
        for pair in touched_pairs:
            terms = pair_term_scores.get(pair) or {}
            if not terms:
                score_map.pop(pair, None)
                shared_map.pop(pair, None)
                continue
            score_map[pair] = sum(max(0.0, value) for value in terms.values())
            shared_map[pair] = list(sorted(terms, key=lambda key: terms[key], reverse=True))[:24]


def _rehydrate_overlap_graph_state(state: OverlapGraphState) -> None:
    if len(state.text_norms) != len(state.text_docs):
        state.text_norms = [math.sqrt(sum(v * v for v in doc.values())) or 1.0 for doc in state.text_docs]
    if len(state.error_norms) != len(state.error_docs):
        state.error_norms = [math.sqrt(sum(v * v for v in doc.values())) or 1.0 for doc in state.error_docs]
    if not state.text_postings and state.text_docs:
        for idx, doc in enumerate(state.text_docs):
            for gram in doc:
                state.text_postings.setdefault(gram, []).append(idx)
    if not state.error_postings and state.error_docs:
        for idx, doc in enumerate(state.error_docs):
            for gram in doc:
                state.error_postings.setdefault(gram, []).append(idx)
    if not state.text_pair_term_scores and state.text_docs:
        _, _, term_scores = _sparse_pair_scores_with_terms(
            state.text_docs,
            max_bucket_size=int(state.params.get("max_bucket_size") or 80),
        )
        state.text_pair_term_scores = term_scores
        if not state.text_scores:
            state.text_scores = {
                pair: sum(max(0.0, value) for value in terms.values())
                for pair, terms in term_scores.items()
            }
        if not state.text_shared:
            state.text_shared = {
                pair: list(sorted(terms, key=lambda key: terms[key], reverse=True))[:24]
                for pair, terms in term_scores.items()
            }
    if not state.error_pair_term_scores and state.error_docs:
        _, _, term_scores = _sparse_pair_scores_with_terms(
            state.error_docs,
            max_bucket_size=int(state.params.get("max_bucket_size") or 80),
        )
        state.error_pair_term_scores = term_scores
        if not state.error_scores:
            state.error_scores = {
                pair: sum(max(0.0, value) for value in terms.values())
                for pair, terms in term_scores.items()
            }
        if not state.error_shared:
            state.error_shared = {
                pair: list(sorted(terms, key=lambda key: terms[key], reverse=True))[:24]
                for pair, terms in term_scores.items()
            }


def find_refactor_cliques(
    graph: OverlapGraph,
    *,
    min_size: int = 3,
    max_size: int = 5,
    min_distinct_tasks: int = 2,
    min_edge_weight: float = 0.18,
    max_cliques: int = 12,
    require_explainable_purity: bool = True,
) -> List[RefactorClique]:
    """Grow small strict cliques from the weighted overlap graph."""

    edge_by_pair: Dict[frozenset[str], OverlapEdge] = {}
    neighbors: Dict[str, set[str]] = defaultdict(set)
    segment_task = {segment.segment_id: segment.task_id for segment in graph.segments}
    segment_by_id = {segment.segment_id: segment for segment in graph.segments}
    for edge in graph.edges:
        if edge.weight < min_edge_weight:
            continue
        key = frozenset([edge.source, edge.target])
        edge_by_pair[key] = edge
        neighbors[edge.source].add(edge.target)
        neighbors[edge.target].add(edge.source)

    seeds = sorted(graph.edges, key=lambda item: item.weight, reverse=True)
    seen: set[Tuple[str, ...]] = set()
    cliques: List[RefactorClique] = []
    for seed in seeds:
        if seed.weight < min_edge_weight:
            continue
        clique = {seed.source, seed.target}
        candidates = (neighbors[seed.source] & neighbors[seed.target]) - clique
        while candidates and len(clique) < max_size:
            best = max(
                candidates,
                key=lambda node: sum(edge_by_pair[frozenset([node, member])].weight for member in clique),
            )
            candidates.remove(best)
            if all(frozenset([best, member]) in edge_by_pair for member in clique):
                clique.add(best)
                candidates &= neighbors[best]
        if len(clique) < min_size:
            continue
        key = tuple(sorted(clique))
        node_types = {
            str((segment_by_id.get(segment_id).metadata or {}).get("node_type") or "trace_segment")
            for segment_id in key
            if segment_by_id.get(segment_id) is not None
        }
        trace_ids = [
            segment_id for segment_id in key
            if str((segment_by_id.get(segment_id).metadata or {}).get("node_type") or "trace_segment") == "trace_segment"
        ]
        if not trace_ids:
            continue
        distinct_tasks = {
            segment_task.get(segment_id, "")
            for segment_id in trace_ids
            if segment_task.get(segment_id, "")
        }
        has_pending_and_active = "pending_skill" in node_types and "skill" in node_types
        if len(distinct_tasks) < min_distinct_tasks and not (len(distinct_tasks) >= 1 and has_pending_and_active):
            continue
        if key in seen:
            continue
        seen.add(key)
        clique_edges = [
            edge_by_pair[frozenset([a, b])]
            for idx, a in enumerate(key)
            for b in key[idx + 1 :]
            if frozenset([a, b]) in edge_by_pair
        ]
        if require_explainable_purity and not _passes_purity_filter(key, clique_edges):
            continue
        cliques.append(
            RefactorClique(
                clique_id=f"refactor_clique_{len(cliques)}",
                segment_ids=list(key),
                edge_weight_sum=round(sum(edge.weight for edge in clique_edges), 6),
                edges=clique_edges,
            )
        )
        if len(cliques) >= max_cliques:
            break
    cliques.sort(key=lambda item: item.edge_weight_sum, reverse=True)
    return cliques


def _passes_purity_filter(segment_ids: Sequence[str], clique_edges: Sequence[OverlapEdge]) -> bool:
    if len(segment_ids) < 3:
        return False
    if not clique_edges:
        return False
    shared_error = sum(1 for edge in clique_edges if edge.error_overlap_score >= 0.15 or edge.shared_error_ngrams)
    semantic_support = sum(1 for edge in clique_edges if edge.embedding_score >= 0.55)
    sparse_support = sum(1 for edge in clique_edges if edge.text_score >= 0.12)
    return (shared_error + semantic_support + sparse_support) >= max(2, len(clique_edges) // 2)


def _segment_for_llm(segment: TraceSegment) -> Dict[str, Any]:
    """Compact segment evidence for LLM refactor.

    Raw BFCL traces can contain full task fixtures and long tool-result dumps.
    The refactorer only needs the local execution instance evidence, not the
    entire benchmark payload.
    """

    return {
        "segment_id": segment.segment_id,
        "task_id": segment.task_id,
        "turn_index": segment.turn_index,
        "kind": segment.kind,
        "text": _trim_text(segment.text, limit=int(os.environ.get("REFACTOR_SEGMENT_TEXT_LIMIT", "1800") or "1800")),
        "error_text": _trim_text(segment.error_text, limit=int(os.environ.get("REFACTOR_SEGMENT_ERROR_LIMIT", "900") or "900")),
        "metadata": copy.deepcopy(segment.metadata or {}),
    }


REFACTOR_SYSTEM = """\
You are a skill repository refactorer.

You are given several execution trace segments. Existing skills may not yet
name the shared behavior. Your job is to decide whether these segments are
different execution instances of the same latent reusable skill program.

Core concept:
- A skill is not a trace summary.
- A skill is a reusable program/rule/workflow hypothesis.
- A trace segment is an execution instance if the skill would explain why that
  local action/decision/constraint occurred.

Refactor rules:
1. Extract a shared skill only when a common invariant explains at least two
   segments after abstracting task-specific names, ids, literals, and entities.
2. The shared skill must preserve behavior. Refactoring may factor out reusable
   content, but it must not change the original skills' contracts.
3. If old skills are rewritten, they should depend on the shared skill and keep
   their original function/semantic scope. Their old bundles should still pass.
4. You may propose deleting/merging an old skill only if the shared skill fully
   subsumes it; otherwise keep it as a residual skill.
5. Prefer precise rule/workflow cards over broad benchmark summaries.
6. Include positive and negative applicability conditions.
7. If overlap is only superficial token similarity, reject.
8. Skill nodes may appear directly in the clique. Treat those skill nodes as
   graph evidence, not as permission to merge. Rewrite or merge only when trace
   evidence and the skill's own contract both support the shared invariant.
9. When skill-node content and trace evidence disagree, trust the trace
   evidence and preserve repository safety by rejecting or keeping residual
   skills.

Few-shot examples. Match this specificity and do not invent tools or
parameters outside the clique evidence.

Example A, knowledge/rule skill:
- Segment 1: user asks to diff `draft.txt` and `final.txt`; the valid local
  behavior is `diff(file_name1="draft.txt", file_name2="final.txt")` with no
  discovery because both filenames are explicit.
- Segment 2: user asks to compare records `A-10` and `B-22`; the valid local
  behavior is `compare_records(left_id="A-10", right_id="B-22")` with no lookup
  because both ids are explicit.
Shared latent skill: When every required identifier for a comparison tool is
explicit in the current user turn, bind those literals directly and skip
discovery.
Residual skills: keep tool-specific parameter names (`file_name1/file_name2`
vs `left_id/right_id`) in the original skills unless the schemas truly share
the same interface.
Reject if: one segment required lookup, the only commonality is the word
"compare", or the proposed rule would ban discovery globally.

Example B, workflow skill:
- Segment 1 context turn 0 returns `{"booking_id": "B-742"}` after a flight
  booking. Focus turn 1 asks "cancel that booking"; the valid sequence is to
  reuse prior state and call `cancel_booking(booking_id="B-742")`.
- Segment 2 context turn 0 returns `{"order_id": "O-31"}` after an order
  lookup. Focus turn 1 asks "cancel it"; the valid sequence is to reuse prior
  state and call `cancel_order(order_id="O-31")`.
Shared latent skill: For multi-turn cancellation, resolve the canonical object
id from required context turns before calling the domain cancellation tool.
Residual skills: keep domain-specific id names, cancellation tool names, and
lookup fallbacks in the old skills.
Reject if: no prior canonical id exists, the user supplies a new id that
overrides context, or the proposed shared skill erases domain-specific schema.

Example C, function/interface skill with executable sequence:
- Segment 1 bad call: `remove_stock_from_watchlist(stock="NVDA")`
  Tool schema: `remove_stock_from_watchlist(symbol: string)`
  Correct executable call sequence:
  [
    "remove_stock_from_watchlist(symbol=\"NVDA\")"
  ]
- Segment 2 bad call: `add_stock_to_watchlist(ticker="TSLA")`
  Tool schema: `add_stock_to_watchlist(symbol: string)`
  Correct executable call sequence:
  [
    "add_stock_to_watchlist(symbol=\"TSLA\")"
  ]
Shared latent skill: For TradingBot watchlist tools whose schema declares a
`symbol` argument, bind an explicit ticker literal to `symbol` exactly.
Affected old skills: rewrite only the shared argument-binding sentence into a
dependency on the shared skill; keep add/remove-specific intent and tool names
as residual behavior.
Reject if: a clique tool schema uses `stock` or `ticker` as the real parameter,
if the user provided a company name that requires lookup, or if the proposed
skill would rename valid non-watchlist parameters.

Example D, reject:
- Segment 1 searches a product catalog.
- Segment 2 searches flight tickets.
They share the word "search" but not a reusable local contract, unless the trace evidence shows the same stop condition, argument binding, or error repair.

Field semantics and empty-value rules:
- `decision.action`: use `extract_shared` only when trace/skill-node evidence
  supports one reusable invariant. Use `reject` for superficial overlap,
  schema disagreement, insufficient source-task coverage, or unsafe merges.
- `decision.confidence`: calibrated confidence in the refactor, not task
  success probability.
- `shared_skill`: when `decision.action` is `reject`, return {}. When extracting,
  include a complete skill with positive applicability and non-applicability.
- `shared_skill.interface.input_contract`: required domains, user intents,
  context turns, literals, state, and schema facts.
- `shared_skill.interface.output_contract`: for function/interface skills,
  include executable tool-call forms or call-order sequences, not vague prose.
- `shared_skill.metadata.allowed_tools`: exact tools covered by the shared
  skill; [] only for pure textual knowledge that does not bind a tool.
- `affected_skill_updates`: [] on reject. On extract, list only skill nodes that
  were in the clique and truly need keep/rewrite/merge/delete decisions.
- `instance_mappings`: one item per clique segment. Mark `is_instance=false`
  with a residual/reject reason when a segment does not instantiate the shared
  invariant.
- Never complete missing fields by guessing. Empty arrays/objects are safer than
  invented tools, parameters, source_task_ids, or dependency edges.

Return strict JSON:
{
  "decision": {
    "action": "extract_shared | reject",
    "reason": "brief rationale",
    "confidence": 0.0
  },
  "shared_skill": {
    "name": "snake_case_name",
    "kind": "atomic_tool_rule_card | workflow_guardrail_card | interface_contract_card | shared_subdoc",
    "description": "short summary",
    "body": "actionable reusable skill content",
    "interface": {
      "summary": "",
      "usage": "",
      "input_contract": {},
      "output_contract": {},
      "invocation_contract": {},
      "compatibility_notes": ""
    },
    "metadata": {
      "allowed_tools": [],
      "intent_keywords": [],
      "source_task_ids": []
    }
  },
  "affected_skill_updates": [
    {
      "name": "existing_skill_name",
      "action": "keep | rewrite | merge_into_shared | delete",
      "reason": "",
      "description": "",
      "body": "",
      "interface": {},
      "metadata": {}
    }
  ],
  "instance_mappings": [
    {
      "segment_id": "",
      "is_instance": true,
      "invariants": [],
      "parameters": {},
      "residual": ""
    }
  ]
}
"""


async def llm_refactor_clique(
    *,
    clique: RefactorClique,
    graph: OverlapGraph,
    existing_skills: Iterable[SkillArtifact],
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
    repair_context: Dict[str, Any] | None = None,
    refactorer_rules: Sequence[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    seg_by_id = {segment.segment_id: segment for segment in graph.segments}
    selected_segments = [seg_by_id[seg_id] for seg_id in clique.segment_ids if seg_id in seg_by_id]
    existing_skill_list = list(existing_skills)
    selected_skill_names = {
        str((segment.metadata or {}).get("skill_name") or "").strip()
        for segment in selected_segments
        if str((segment.metadata or {}).get("skill_name") or "").strip()
    }
    skill_summaries = [
        {
            "name": skill.name,
            "version": skill.version,
            "kind": skill.kind,
            "description": skill.description,
            "body": skill.body[:1200],
            "interface": skill.interface.as_dict(),
            "bundle_case_count": len(skill.bundle.all_cases()),
            "source_task_ids": list(skill.metadata.get("source_task_ids") or []),
        }
        for skill in existing_skill_list
        if skill.name in selected_skill_names
    ]
    repair_section = ""
    if repair_context:
        repair_section = (
            "## Previous Refactor Attempt Failed Gate\n"
            f"{_role_json_block(repair_context)}\n\n"
            "Revise the refactor proposal so every new or rewritten skill can pass its bundle gate. "
            "Preserve the old skills' semantics; do not hide failures by weakening old bundles.\n\n"
        )
    user = (
        "## Candidate Refactor Clique\n"
        f"{_role_json_block(clique.as_dict())}\n\n"
        "## Candidate Segments\n"
        f"{_role_json_block([_segment_for_llm(segment) for segment in selected_segments])}\n\n"
        "## Involved Skill Node Summaries\n"
        f"{_role_json_block(skill_summaries)}\n\n"
        f"{repair_section}"
        "## Task\n"
        "Decide whether the segments instantiate one latent reusable skill. "
        "If yes, extract the shared skill and list affected old-skill updates.\n"
    )
    return await _ask_json(
        system=REFACTOR_SYSTEM + _refactorer_rule_suffix(refactorer_rules),
        user=_trim_text(user, limit=16000),
        llm_config=llm_config,
        model_name=model_name,
        role="refactorer",
        metadata={
            "clique_id": clique.clique_id,
            "segment_ids": list(clique.segment_ids),
            "n_refactorer_rules": len(list(refactorer_rules or [])),
            **dict(audit_context or {}),
        },
    )


def artifact_from_refactor_payload(payload: Dict[str, Any], *, group_id: str) -> SkillArtifact | None:
    decision = dict(payload.get("decision") or {})
    if str(decision.get("action") or "") != "extract_shared":
        return None
    raw = dict(payload.get("shared_skill") or {})
    name = str(raw.get("name") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not name or not body:
        return None
    interface_raw = dict(raw.get("interface") or {})
    metadata = dict(raw.get("metadata") or {})
    metadata.update(
        {
            "source": "llm_refactor_overlap",
            "refactor_group_id": group_id,
            "version_kind": "refactor",
            "instance_mappings": copy.deepcopy(payload.get("instance_mappings") or []),
        }
    )
    return SkillArtifact(
        name=name,
        kind=str(raw.get("kind") or "shared_subdoc"),
        description=str(raw.get("description") or name).strip(),
        body=body,
        metadata=metadata,
        tags=[str(item).strip() for item in (raw.get("tags") or []) if str(item).strip()],
        interface=SkillInterface(
            summary=str(interface_raw.get("summary") or raw.get("description") or name),
            usage=str(interface_raw.get("usage") or ""),
            input_contract=dict(interface_raw.get("input_contract") or {}),
            output_contract=dict(interface_raw.get("output_contract") or {}),
            invocation_contract=dict(interface_raw.get("invocation_contract") or {}),
            compatibility_notes=str(interface_raw.get("compatibility_notes") or ""),
        ),
        lineage=SkillLineage(version_kind="refactor", refactor_group_id=group_id),
    )


def apply_affected_skill_updates(
    payload: Dict[str, Any],
    *,
    existing_by_name: Dict[str, SkillArtifact],
    shared_skill: SkillArtifact,
    group_id: str,
) -> List[SkillArtifact]:
    updates: List[SkillArtifact] = []
    for raw in payload.get("affected_skill_updates") or []:
        name = str((raw or {}).get("name") or "").strip()
        action = str((raw or {}).get("action") or "keep").strip()
        existing = existing_by_name.get(name)
        if existing is None or action == "keep":
            continue
        updated = copy.deepcopy(existing)
        updated.lineage = SkillLineage(
            parent_version=existing.version,
            parent_version_id=existing.version_id(),
            version_kind="refactor",
            migration_reason=str(raw.get("reason") or "refactor overlap extraction"),
            refined_from_result_ids=list(existing.lineage.refined_from_result_ids or []),
            refactor_group_id=group_id,
        )
        updated.metadata = {
            **dict(updated.metadata or {}),
            **dict(raw.get("metadata") or {}),
            "version_kind": "refactor",
            "refactor_group_id": group_id,
            "refactored_with_shared_skill": shared_skill.name,
            "refactor_action": action,
        }
        if action in {"merge_into_shared", "delete"}:
            updated.status = "archived"
            updated.metadata["disabled"] = True
            updated.metadata["archive_reason"] = str(raw.get("reason") or f"{action} during refactor")
        else:
            if raw.get("description"):
                updated.description = str(raw.get("description"))
            if raw.get("body"):
                updated.body = str(raw.get("body"))
            if raw.get("interface"):
                iface = dict(raw.get("interface") or {})
                updated.interface = SkillInterface(
                    summary=str(iface.get("summary") or updated.interface.summary or updated.description),
                    usage=str(iface.get("usage") or updated.interface.usage),
                    input_contract=dict(iface.get("input_contract") or updated.interface.input_contract or {}),
                    output_contract=dict(iface.get("output_contract") or updated.interface.output_contract or {}),
                    invocation_contract=dict(iface.get("invocation_contract") or updated.interface.invocation_contract or {}),
                    compatibility_notes=str(iface.get("compatibility_notes") or updated.interface.compatibility_notes),
                )
            updated.dependencies = sorted(set(list(updated.dependencies or []) + [shared_skill.name]))
            updated.dependency_pins = list(updated.dependency_pins or []) + [
                DependencyPin(skill_name=shared_skill.name, min_version=1, compatibility_mode="floating")
            ]
        updates.append(updated)
    return updates
