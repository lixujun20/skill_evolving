"""JSON-backed store for benchmark-agnostic skill artifacts and test results."""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from academic.skill_repository.types import (
    DependencyPin,
    SkillArtifact,
    SkillBundle,
    SkillBundleCase,
    SkillEvidence,
    SkillInterface,
    SkillLineage,
    SkillTestCaseRun,
    SkillTestResult,
)


class RetrievalBackend:
    """Scoring backend for benchmark-agnostic skill retrieval.

    The store owns filtering, audit shape, and top-k selection. Backends only
    score a query/artifact pair so lexical, tag, and embedding retrieval can be
    swapped without benchmark-specific branches in executor code.
    """

    name = "base"

    def score(
        self,
        *,
        query: str,
        artifact: SkillArtifact,
        artifact_tags: List[str],
        query_tags: Set[str],
    ) -> Dict[str, Any]:
        raise NotImplementedError


class LexicalTagRetrievalBackend(RetrievalBackend):
    """Default retrieval: sparse token cosine plus controlled tag boost."""

    name = "lexical_tag"

    def score(
        self,
        *,
        query: str,
        artifact: SkillArtifact,
        artifact_tags: List[str],
        query_tags: Set[str],
    ) -> Dict[str, Any]:
        base_score = _cosine(_tokenize(query), _tokenize(artifact.retrieval_text()))
        tag_info = _tag_match_score(artifact_tags, query_tags)
        return {
            "score": base_score + tag_info["score"],
            "base_score": base_score,
            "tag_score": tag_info["score"],
            "embedding_score": None,
            "tag_matches": tag_info["matches"],
            "backend": self.name,
        }


class HybridEmbeddingRetrievalBackend(RetrievalBackend):
    """Optional hybrid retriever using the existing embedding provider.

    This is intentionally an in-memory, small-store implementation. It shares
    the same embedding model as overlap/refactor but uses a separate skill text
    projection and cache. For the current 50/50 setting the full scan is cheaper
    and easier to audit than introducing ANN infrastructure.
    """

    name = "hybrid_embedding"

    def __init__(
        self,
        *,
        embedding_fn: Callable[[str], Optional[List[float]]] | None = None,
        lexical_weight: float = 0.65,
        embedding_weight: float = 0.35,
    ) -> None:
        self.embedding_fn = embedding_fn
        self.lexical_weight = float(lexical_weight)
        self.embedding_weight = float(embedding_weight)
        self._cache: Dict[str, Optional[List[float]]] = {}
        self._retriever: Any = None

    def score(
        self,
        *,
        query: str,
        artifact: SkillArtifact,
        artifact_tags: List[str],
        query_tags: Set[str],
    ) -> Dict[str, Any]:
        lexical = LexicalTagRetrievalBackend().score(
            query=query,
            artifact=artifact,
            artifact_tags=artifact_tags,
            query_tags=query_tags,
        )
        query_embedding = self._embed(query)
        artifact_embedding = self._embed(_skill_embedding_text(artifact))
        embedding_score = _dense_cosine(query_embedding, artifact_embedding)
        if embedding_score is None:
            score = float(lexical["score"])
        else:
            score = (
                self.lexical_weight * float(lexical["base_score"])
                + self.embedding_weight * float(embedding_score)
                + float(lexical["tag_score"])
            )
        return {
            **lexical,
            "score": score,
            "embedding_score": embedding_score,
            "backend": self.name if embedding_score is not None else "hybrid_embedding_fallback_lexical",
        }

    def _embed(self, text: str) -> Optional[List[float]]:
        compact = (text or "").strip()[:8000]
        if not compact:
            return None
        if compact in self._cache:
            cached = self._cache[compact]
            return None if cached is None else list(cached)
        embedding: Optional[List[float]] = None
        try:
            if self.embedding_fn is not None:
                embedding = self.embedding_fn(compact)
            else:
                if self._retriever is None:
                    from app.meta_agent.skills.retrieval import SkillRetriever

                    self._retriever = SkillRetriever()
                embedding = self._retriever.generate_embedding(compact)
        except Exception:
            embedding = None
        self._cache[compact] = list(embedding) if embedding else None
        return None if embedding is None else list(embedding)


class ArtifactStore:
    def __init__(
        self,
        artifacts: Iterable[SkillArtifact] | None = None,
        *,
        test_results: Iterable[SkillTestResult] | None = None,
        retrieval_backend: RetrievalBackend | None = None,
    ) -> None:
        coerced_artifacts = [_coerce_artifact(artifact) for artifact in (artifacts or [])]
        self._artifacts: Dict[str, SkillArtifact] = {
            artifact.name: artifact for artifact in coerced_artifacts
        }
        self._test_results: List[SkillTestResult] = [
            _coerce_test_result(item) for item in (test_results or [])
        ]
        self._retrieval_backend = retrieval_backend or LexicalTagRetrievalBackend()
        self.refresh_all_dependencies()

    def set_retrieval_backend(self, backend: RetrievalBackend) -> None:
        self._retrieval_backend = backend

    @property
    def retrieval_backend_name(self) -> str:
        return getattr(self._retrieval_backend, "name", type(self._retrieval_backend).__name__)

    def add(self, artifact: SkillArtifact) -> None:
        artifact = _coerce_artifact(artifact)
        artifact.dependencies = self._detect_dependencies(artifact)
        existing = self._artifacts.get(artifact.name)
        if existing:
            version_kind = artifact.version_kind()
            self._inherit_long_lived_assets(existing, artifact)
            if not _artifact_semantically_changed(existing, artifact):
                existing.dependencies = self._detect_dependencies(existing)
                if _bundle_changed(existing.bundle, artifact.bundle):
                    if artifact.bundle.bundle_version <= existing.bundle.bundle_version:
                        artifact.bundle.bundle_version = existing.bundle.bundle_version + 1
                    existing.bundle = deepcopy(artifact.bundle)
                existing.metadata.setdefault("version_kind", existing.version_kind())
                return
            if artifact.version <= existing.version:
                artifact.version = existing.version + 1
            artifact.history = list(existing.history)
            artifact.history.append(_artifact_snapshot(existing))
            artifact.lineage.parent_version = existing.version
            artifact.lineage.parent_version_id = existing.version_id()
            if not artifact.lineage.version_kind or artifact.lineage.version_kind == "seed":
                artifact.lineage.version_kind = version_kind
            artifact.metadata.setdefault("version_kind", version_kind)
            if artifact.bundle.bundle_version <= existing.bundle.bundle_version:
                if _bundle_changed(existing.bundle, artifact.bundle):
                    artifact.bundle.bundle_version = existing.bundle.bundle_version + 1
                else:
                    artifact.bundle.bundle_version = existing.bundle.bundle_version
            artifact.status = artifact.status or "active"
            artifact.stale = False
            if not artifact.dependency_pins and existing.dependency_pins:
                artifact.dependency_pins = deepcopy(existing.dependency_pins)
            self._artifacts[artifact.name] = artifact
            self._mark_dependents_stale(
                artifact.name,
                upstream_version=artifact.version,
                upstream_version_kind=version_kind,
            )
            return
        artifact.metadata.setdefault("version_kind", artifact.version_kind())
        if not artifact.bundle.bundle_id:
            artifact.bundle.bundle_id = f"{artifact.name}.bundle"
        artifact.bundle.bundle_version = max(int(artifact.bundle.bundle_version or 1), 1)
        artifact.status = artifact.status or "active"
        self._artifacts[artifact.name] = artifact

    def add_pending(self, artifact: SkillArtifact) -> None:
        """Add a prior-extracted candidate without enabling retrieval.

        Pending artifacts are the implementation of the paper algorithm's
        forward-prior extraction stage: they remain visible to repository
        maintenance and overlap/refactor, but are not injected into executor
        prompts until posterior evidence promotes them.
        """

        artifact = _coerce_artifact(artifact)
        existing = self._artifacts.get(artifact.name)
        if existing is not None and existing.status != "pending":
            original_name = artifact.name
            base_name = f"{original_name}__pending"
            idx = 1
            candidate_name = f"{base_name}_{idx}"
            while candidate_name in self._artifacts:
                idx += 1
                candidate_name = f"{base_name}_{idx}"
            artifact.name = candidate_name
            artifact.metadata["candidate_for_existing_skill"] = original_name
            if not artifact.bundle.bundle_id or artifact.bundle.bundle_id == f"{original_name}.bundle":
                artifact.bundle.bundle_id = f"{artifact.name}.bundle"
        artifact.status = "pending"
        artifact.metadata["is_pending_skill"] = True
        artifact.metadata["is_promoted"] = False
        artifact.metadata["retrieval_disabled_reason"] = "pending_prior_candidate"
        artifact.metadata.setdefault("promotion_state", "pending")
        self.add(artifact)

    def pending_artifacts(self) -> List[SkillArtifact]:
        return [
            artifact
            for artifact in self._artifacts.values()
            if artifact.status == "pending" or artifact.metadata.get("is_pending_skill")
        ]

    def promote_pending(
        self,
        name: str,
        *,
        reason: str = "posterior_overlap_evidence",
        refactor_group_id: str = "",
    ) -> bool:
        artifact = self._artifacts.get(name)
        if artifact is None:
            return False
        if artifact.status != "pending" and not artifact.metadata.get("is_pending_skill"):
            return False
        artifact.status = "active"
        artifact.metadata["is_pending_skill"] = False
        artifact.metadata["is_promoted"] = True
        artifact.metadata["promotion_state"] = "promoted"
        artifact.metadata["promotion_reason"] = reason
        if refactor_group_id:
            artifact.metadata["promoted_by_refactor_group_id"] = refactor_group_id
        artifact.metadata.pop("retrieval_disabled_reason", None)
        return True

    def revoke_unpromoted_pending(self, *, reason: str = "pending_not_reused") -> List[str]:
        revoked: List[str] = []
        for artifact in list(self._artifacts.values()):
            if artifact.status != "pending" and not artifact.metadata.get("is_pending_skill"):
                continue
            if artifact.metadata.get("is_promoted"):
                continue
            artifact.status = "archived"
            artifact.metadata["archived_reason"] = reason
            artifact.metadata["promotion_state"] = "revoked"
            revoked.append(artifact.name)
        return sorted(revoked)

    def _inherit_long_lived_assets(self, existing: SkillArtifact, incoming: SkillArtifact) -> None:
        """Preserve durable assets when an extractor emits a same-name update.

        LLM extraction often returns only the semantic skill card. The bound
        bundle/dependency decisions are repository-maintained assets and must
        not be silently erased by a same-name content refresh. Credit evidence
        is version-local runtime performance, so semantic updates must build
        fresh helpful/harmful evidence instead of inheriting the parent
        version's outcomes.
        """

        if not incoming.bundle.all_cases() and existing.bundle.all_cases():
            incoming.bundle = deepcopy(existing.bundle)
            incoming.metadata["bundle_inherited_from_version"] = existing.version
        elif incoming.bundle.all_cases() and existing.bundle.all_cases():
            incoming.bundle.positive_cases = _merge_bundle_cases(
                incoming.bundle.positive_cases,
                existing.bundle.positive_cases,
            )
            incoming.bundle.negative_cases = _merge_bundle_cases(
                incoming.bundle.negative_cases,
                existing.bundle.negative_cases,
            )
            incoming.bundle.integration_cases = _merge_bundle_cases(
                incoming.bundle.integration_cases,
                existing.bundle.integration_cases,
            )
        if not incoming.dependency_pins and existing.dependency_pins:
            incoming.dependency_pins = deepcopy(existing.dependency_pins)

    def get(self, name: str) -> SkillArtifact | None:
        artifact = self._artifacts.get(name)
        return _coerce_artifact(artifact) if artifact else None

    def all(self) -> List[SkillArtifact]:
        return list(self._artifacts.values())

    def history(self, name: str) -> List[Dict[str, Any]]:
        artifact = self._artifacts.get(name)
        return list(artifact.history) if artifact else []

    def stale_artifacts(self) -> List[SkillArtifact]:
        return [artifact for artifact in self._artifacts.values() if artifact.stale]

    def clear_stale(self, name: str) -> None:
        artifact = self._artifacts.get(name)
        if artifact:
            artifact.stale = False
            if artifact.status == "stale":
                artifact.status = "active"

    def pin_dependency(
        self,
        skill_name: str,
        dependency_name: str,
        *,
        pinned_version: int | None,
        compatibility_mode: str = "pinned",
    ) -> None:
        artifact = self._artifacts.get(skill_name)
        if artifact is None:
            raise KeyError(skill_name)
        updated = False
        for pin in artifact.dependency_pins:
            if pin.skill_name == dependency_name:
                pin.pinned_version = pinned_version
                pin.compatibility_mode = compatibility_mode
                updated = True
                break
        if not updated:
            artifact.dependency_pins.append(
                DependencyPin(
                    skill_name=dependency_name,
                    pinned_version=pinned_version,
                    compatibility_mode=compatibility_mode,
                )
            )

    def rollback(self, name: str, *, target_version: int | None = None) -> bool:
        artifact = self._artifacts.get(name)
        if artifact is None or not artifact.history:
            return False
        snapshot: Dict[str, Any] | None = None
        if target_version is None:
            snapshot = artifact.history.pop()
        else:
            for idx in range(len(artifact.history) - 1, -1, -1):
                if int(artifact.history[idx].get("version", -1)) == int(target_version):
                    snapshot = artifact.history.pop(idx)
                    break
        if snapshot is None:
            return False
        current_snapshot = _artifact_snapshot(artifact)
        restored = _coerce_artifact(snapshot)
        restored.history = list(artifact.history)
        restored.history.append(current_snapshot)
        restored.status = "active"
        restored.stale = False
        restored.metadata["version_kind"] = "rollback"
        restored.lineage.version_kind = "rollback"
        self._artifacts[name] = restored
        return True

    def add_test_result(self, result: SkillTestResult) -> None:
        self._test_results.append(_coerce_test_result(result))

    def test_results(
        self,
        *,
        skill_name: str | None = None,
        run_label: str | None = None,
    ) -> List[SkillTestResult]:
        items = self._test_results
        if skill_name is not None:
            items = [item for item in items if item.skill_name == skill_name]
        if run_label is not None:
            items = [item for item in items if item.run_label == run_label]
        return list(items)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        *,
        min_score: float = 0.0,
        predicate: Callable[[SkillArtifact], bool] | None = None,
        rerank_key: Callable[[SkillArtifact], tuple] | None = None,
        debug_context: Dict[str, Any] | None = None,
        include_pending: bool = False,
    ) -> List[SkillArtifact]:
        audit = self.retrieve_audit(
            query,
            top_k=top_k,
            min_score=min_score,
            predicate=predicate,
            rerank_key=rerank_key,
            debug_context=debug_context,
            include_pending=include_pending,
        )
        return [
            self._artifacts[item["name"]]
            for item in audit.get("selected", [])
            if item.get("name") in self._artifacts
        ]

    def retrieve_audit(
        self,
        query: str,
        top_k: int = 5,
        *,
        min_score: float = 0.0,
        predicate: Callable[[SkillArtifact], bool] | None = None,
        rerank_key: Callable[[SkillArtifact], tuple] | None = None,
        debug_context: Dict[str, Any] | None = None,
        include_pending: bool = False,
    ) -> Dict[str, Any]:
        context = dict(debug_context or {})
        if not self._artifacts:
            return {
                "query": query,
                "top_k": top_k,
                "min_score": float(min_score),
                "context": context,
                "store_summary": {"n_total": 0, "n_active": 0, "n_stale": 0, "n_disabled": 0},
                "candidates": [],
                "selected": [],
            }
        q = _tokenize(query)
        query_tags = _query_tags(query, context)
        candidates: List[Dict[str, Any]] = []
        scored: List[tuple[float, tuple, SkillArtifact, Dict[str, Any]]] = []
        for artifact in self._artifacts.values():
            artifact_tags = _artifact_tags(artifact)
            row: Dict[str, Any] = {
                "name": artifact.name,
                "version": artifact.version,
                "version_kind": artifact.version_kind(),
                "kind": artifact.kind,
                "status": artifact.status,
                "stale": bool(artifact.stale),
                "dependencies": list(artifact.dependencies or []),
                "retrieval_enabled": artifact.retrieval_enabled(),
                "description": artifact.description,
                "tags": artifact_tags,
                "metadata": {
                    "intent_keywords": list(artifact.metadata.get("intent_keywords") or []),
                    "allowed_tools": list(artifact.metadata.get("allowed_tools") or []),
                    "domains": list(artifact.metadata.get("domains") or []),
                    "source_task_ids": list(artifact.metadata.get("source_task_ids") or []),
                    "source": artifact.metadata.get("source"),
                },
            }
            pending_allowed = bool(include_pending) and (
                artifact.status == "pending" or bool(artifact.metadata.get("is_pending_skill"))
            )
            if not artifact.retrieval_enabled() and not pending_allowed:
                row.update({"predicate_passed": False, "filter_reason": "retrieval_disabled", "score": 0.0, "base_score": 0.0, "tag_score": 0.0, "tag_matches": [], "rerank": []})
                candidates.append(row)
                continue
            predicate_passed = True
            filter_reason = ""
            if predicate is not None:
                try:
                    predicate_passed = bool(predicate(artifact))
                except Exception as exc:
                    predicate_passed = False
                    filter_reason = f"predicate_error:{type(exc).__name__}"
            if not predicate_passed:
                row.update({"predicate_passed": False, "filter_reason": filter_reason or "predicate_false", "score": 0.0, "base_score": 0.0, "tag_score": 0.0, "tag_matches": [], "rerank": []})
                candidates.append(row)
                continue
            score_info = self._retrieval_backend.score(
                query=query,
                artifact=artifact,
                artifact_tags=artifact_tags,
                query_tags=query_tags,
            )
            base_score = float(score_info.get("base_score") or 0.0)
            tag_score = float(score_info.get("tag_score") or 0.0)
            embedding_score = score_info.get("embedding_score")
            tag_matches = list(score_info.get("tag_matches") or [])
            score = float(score_info.get("score") or 0.0)
            try:
                rerank = rerank_key(artifact) if rerank_key is not None else ()
            except Exception as exc:
                rerank = ()
                row["rerank_error"] = type(exc).__name__
            row.update(
                {
                    "predicate_passed": True,
                    "filter_reason": "",
                    "score": round(float(score), 6),
                    "base_score": round(float(base_score), 6),
                    "tag_score": round(float(tag_score), 6),
                    "embedding_score": None if embedding_score is None else round(float(embedding_score), 6),
                    "tag_matches": tag_matches,
                    "query_tags": sorted(query_tags),
                    "rerank": list(rerank),
                    "retrieval_backend": str(score_info.get("backend") or self.retrieval_backend_name),
                    "retrieval_text_chars": len(artifact.retrieval_text()),
                }
            )
            candidates.append(row)
            scored.append((score, rerank, artifact, row))
        scored.sort(key=lambda item: (item[0], *item[1]), reverse=True)
        selected_rows: List[Dict[str, Any]] = []
        selected_names = set()
        selected_candidate_groups: set[str] = set()
        for rank, (score, rerank, artifact, row) in enumerate(scored, start=1):
            row["rank"] = rank
            candidate_group_id = str(artifact.metadata.get("candidate_group_id") or "").strip()
            is_alternative_candidate = (
                bool(candidate_group_id)
                and str(artifact.metadata.get("candidate_group_role") or "").strip() == "alternative"
            )
            group_suppressed = is_alternative_candidate and candidate_group_id in selected_candidate_groups
            selected = len(selected_rows) < top_k and score >= float(min_score) and not group_suppressed
            row["selected"] = selected
            if group_suppressed:
                row["filter_reason"] = "candidate_group_alternative_suppressed"
            if selected:
                selected_names.add(artifact.name)
                if is_alternative_candidate:
                    selected_candidate_groups.add(candidate_group_id)
                selected_rows.append(
                    {
                        "name": artifact.name,
                        "rank": rank,
                        "score": round(float(score), 6),
                        "base_score": round(float(row.get("base_score", 0.0)), 6),
                        "tag_score": round(float(row.get("tag_score", 0.0)), 6),
                        "embedding_score": row.get("embedding_score"),
                        "tag_matches": list(row.get("tag_matches") or []),
                        "rerank": list(rerank),
                        "retrieval_backend": row.get("retrieval_backend") or self.retrieval_backend_name,
                        "candidate_group_id": candidate_group_id,
                    }
                )
        for row in candidates:
            row.setdefault("rank", None)
            row.setdefault("selected", row["name"] in selected_names)
        return {
            "query": query,
            "top_k": top_k,
            "min_score": float(min_score),
            "context": context,
            "retrieval_backend": self.retrieval_backend_name,
            "store_summary": {
                "n_total": len(self._artifacts),
                "n_active": sum(1 for artifact in self._artifacts.values() if artifact.status == "active"),
                "n_stale": sum(1 for artifact in self._artifacts.values() if artifact.stale),
                "n_disabled": sum(1 for artifact in self._artifacts.values() if artifact.is_disabled()),
            },
            "candidates": candidates,
            "selected": selected_rows,
        }

    def _detect_dependencies(self, artifact: SkillArtifact) -> List[str]:
        explicit = [
            str(item).strip()
            for item in (artifact.metadata.get("dependencies") or artifact.dependencies or [])
            if str(item).strip() and str(item).strip() != artifact.name
        ]
        if explicit:
            return sorted(set(explicit))
        if not self._artifacts:
            return []
        deps: Set[str] = set()
        text = f"{artifact.description}\n{artifact.body}"
        for candidate in self._artifacts:
            if candidate == artifact.name:
                continue
            if re.search(rf"\b{re.escape(candidate)}\b", text):
                deps.add(candidate)
        return sorted(deps)

    def refresh_all_dependencies(self) -> None:
        for artifact in self._artifacts.values():
            artifact.dependencies = self._detect_dependencies(artifact)

    def _mark_dependents_stale(
        self,
        updated_name: str,
        *,
        upstream_version: int,
        upstream_version_kind: str,
    ) -> List[str]:
        marked: List[str] = []
        queue: List[str] = [updated_name]
        visited: Set[str] = {updated_name}
        while queue:
            current = queue.pop(0)
            for artifact in self._artifacts.values():
                if artifact.name in visited:
                    continue
                if current not in artifact.dependencies:
                    continue
                compatible_pin = False
                for pin in artifact.dependency_pins:
                    if (
                        pin.skill_name == current
                        and pin.pinned_version is not None
                        and int(pin.pinned_version) < int(upstream_version)
                    ):
                        compatible_pin = True
                        break
                artifact.stale = True
                artifact.status = "stale"
                artifact.metadata["stale_due_to"] = {
                    "dependency": current,
                    "dependency_version": upstream_version,
                    "version_kind": upstream_version_kind,
                    "pinned_legacy_allowed": compatible_pin,
                }
                marked.append(artifact.name)
                visited.add(artifact.name)
                queue.append(artifact.name)
        return marked

    def build_prompt(
        self,
        artifacts: List[SkillArtifact] | None = None,
        *,
        include_types: Iterable[str] | None = None,
        include_retrieval_disabled: bool = False,
    ) -> str:
        target = artifacts if artifacts is not None else self.all()
        if include_types is not None:
            allowed: Set[str] = {str(item) for item in include_types}
            target = [artifact for artifact in target if artifact.injection_type() in allowed]
        if not include_retrieval_disabled:
            target = [artifact for artifact in target if artifact.retrieval_enabled()]
        if not target:
            return "(no reusable skill artifacts retrieved)"
        return "\n\n".join(artifact.prompt_block() for artifact in target)

    def as_tool_schemas(self, artifacts: List[SkillArtifact] | None = None) -> List[Dict[str, object]]:
        target = artifacts if artifacts is not None else self.all()
        return [
            _artifact_tool_schema(artifact)
            for artifact in target
            if artifact.injection_type() == "functional" and artifact.retrieval_enabled()
        ]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([a.as_dict() for a in self.all()], ensure_ascii=False, indent=2)
        )

    @classmethod
    def load(cls, path: Path) -> "ArtifactStore":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        test_results: List[SkillTestResult] = []
        if isinstance(raw, dict):
            test_results = [_coerce_test_result(item) for item in (raw.get("test_results", []) or [])]
            raw = raw.get("artifacts", [])
        artifacts = [_coerce_artifact(item) for item in raw]
        return cls(artifacts, test_results=test_results)

    def save_test_results(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([item.as_dict() for item in self._test_results], ensure_ascii=False, indent=2)
        )

    @classmethod
    def load_test_results(cls, path: Path) -> List[SkillTestResult]:
        if not path.exists():
            return []
        raw = json.loads(path.read_text())
        if not isinstance(raw, list):
            raise ValueError(f"Malformed test result payload in {path}")
        return [_coerce_test_result(item) for item in raw]


def _tokenize(text: str) -> Dict[str, float]:
    words: List[str] = []
    for raw in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|[\u4e00-\u9fff]+", text or ""):
        lowered = raw.lower()
        words.append(lowered)
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw).replace("_", " ")
        for piece in re.findall(r"[a-zA-Z]+|\d+|[\u4e00-\u9fff]+", expanded.lower()):
            if piece and piece != lowered:
                words.append(piece)
    total = len(words) or 1
    out: Dict[str, float] = {}
    for word in words:
        out[word] = out.get(word, 0.0) + 1.0 / total
    return out


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    dot = sum(a.get(k, 0.0) * v for k, v in b.items())
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


def _dense_cosine(a: Optional[List[float]], b: Optional[List[float]]) -> Optional[float]:
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
        norm_a += float(x) * float(x)
        norm_b += float(y) * float(y)
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    return dot / math.sqrt(norm_a * norm_b)


def _skill_embedding_text(artifact: SkillArtifact) -> str:
    metadata = artifact.metadata or {}
    parts = [
        artifact.name,
        artifact.kind,
        artifact.description,
        artifact.interface.summary,
        artifact.interface.usage,
        artifact.body[:3000],
        " ".join(str(item) for item in artifact.tags or []),
        " ".join(str(item) for item in metadata.get("domains") or []),
        " ".join(str(item) for item in metadata.get("allowed_tools") or []),
        " ".join(str(item) for item in metadata.get("intent_keywords") or []),
    ]
    return "\n".join(part for part in parts if str(part).strip())


_TAG_PREFIXES = {"domain", "tool", "intent"}
_TAG_WEIGHTS = {"domain": 0.08, "tool": 0.12, "intent": 0.04}
_MAX_TAG_BOOST = 0.28


def _normalize_tag(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or ":" not in text:
        return ""
    prefix, value = text.split(":", 1)
    prefix = prefix.strip().lower()
    value = value.strip()
    if prefix not in _TAG_PREFIXES or not value:
        return ""
    value = re.sub(r"\s+", "_", value)
    if prefix == "intent":
        value = re.sub(r"[^A-Za-z0-9_./-]+", "_", value).strip("_").lower()
    else:
        value = re.sub(r"[^A-Za-z0-9_./-]+", "_", value).strip("_")
    return f"{prefix}:{value}" if value else ""


def _normalize_tags(raw_tags: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in raw_tags or []:
        tag = _normalize_tag(raw)
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _intent_tag_value(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^a-z0-9_./-]+", "_", value).strip("_")
    return value


def _artifact_tags(artifact: SkillArtifact) -> List[str]:
    tags: List[str] = list(artifact.tags or [])
    metadata = artifact.metadata or {}
    tags.extend(f"domain:{item}" for item in metadata.get("domains") or [])
    tags.extend(f"tool:{item}" for item in metadata.get("allowed_tools") or [])
    tool = str(metadata.get("tool") or "").strip()
    if tool:
        tags.append(f"tool:{tool}")
    tags.extend(f"intent:{_intent_tag_value(item)}" for item in metadata.get("intent_keywords") or [])
    return _normalize_tags(tags)


def _query_tags(query: str, context: Dict[str, Any]) -> Set[str]:
    raw_tags: List[Any] = []
    raw_tags.extend(context.get("query_tags") or [])
    raw_tags.extend(context.get("tags") or [])
    for domain in context.get("domains") or context.get("task_domains") or []:
        raw_tags.append(f"domain:{domain}")
    for tool in context.get("runtime_tools") or context.get("allowed_tools") or context.get("tools") or []:
        raw_tags.append(f"tool:{tool}")
    for intent in context.get("intent_keywords") or context.get("intents") or []:
        raw_tags.append(f"intent:{intent}")
    lowered = (query or "").lower()
    for raw in re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", query or ""):
        if "_" in raw:
            raw_tags.append(f"tool:{raw}")
        value = _intent_tag_value(raw)
        if value:
            raw_tags.append(f"intent:{value}")
    for marker in ("reuse id", "reuse ids", "exact id", "identifier"):
        if marker in lowered:
            raw_tags.append("intent:reuse_id")
    for marker in ("schema", "argument", "parameter"):
        if marker in lowered:
            raw_tags.append("intent:schema")
    return set(_normalize_tags(raw_tags))


def _tag_match_score(artifact_tags: List[str], query_tags: Set[str]) -> Dict[str, Any]:
    matches = sorted(set(artifact_tags) & set(query_tags))
    score = 0.0
    for tag in matches:
        prefix = tag.split(":", 1)[0]
        score += _TAG_WEIGHTS.get(prefix, 0.0)
    return {"score": min(score, _MAX_TAG_BOOST), "matches": matches}


def _artifact_tool_schema(artifact: SkillArtifact) -> Dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": f"skill__{artifact.name}",
            "description": artifact.description[:900],
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Optional subtask or reason for consulting this skill.",
                    }
                },
                "required": [],
            },
        },
    }


def _artifact_snapshot(artifact: SkillArtifact) -> Dict[str, Any]:
    snapshot = artifact.as_dict()
    snapshot.pop("version_id", None)
    snapshot.pop("version_kind", None)
    snapshot.pop("dependency_versions", None)
    # History snapshots must not recursively embed older history chains.
    # Otherwise every same-name update nests the full prior ancestry again,
    # causing superlinear memory and JSON growth.
    snapshot["history"] = []
    return snapshot


def _artifact_semantic_projection(artifact: SkillArtifact) -> Dict[str, Any]:
    snapshot = _artifact_snapshot(artifact)
    snapshot.pop("version", None)
    snapshot.pop("usage_count", None)
    snapshot.pop("success_count", None)
    snapshot.pop("history", None)
    snapshot.pop("stale", None)
    metadata = dict(snapshot.get("metadata") or {})
    for key in (
        "version_kind",
        "bundle_generated_at",
        "bundle_inherited_from_version",
        "bundle_input_signature",
        "bundle_split_count",
        "bundle_trimmed",
        "last_bundle_test_signature",
        "last_bundle_test_result_id",
        "last_bundle_test_cached",
        "semantic_unchanged_from_version",
    ):
        metadata.pop(key, None)
    snapshot["metadata"] = metadata
    bundle = dict(snapshot.get("bundle") or {})
    bundle.pop("bundle_version", None)
    fixtures = dict(bundle.get("fixtures") or {})
    for key in (
        "bundle_generated_at",
        "bundle_input_signature",
        "bundle_split_count",
        "bundle_trimmed",
        "last_bundle_test_signature",
        "last_bundle_test_result_id",
    ):
        fixtures.pop(key, None)
    bundle["fixtures"] = fixtures
    snapshot["bundle"] = bundle
    return snapshot


def _artifact_semantically_changed(left: SkillArtifact, right: SkillArtifact) -> bool:
    return _artifact_semantic_projection(left) != _artifact_semantic_projection(right)


def _bundle_changed(left: SkillBundle, right: SkillBundle) -> bool:
    return left.as_dict() != right.as_dict()


def _merge_bundle_cases(
    primary: List[SkillBundleCase],
    secondary: List[SkillBundleCase],
) -> List[SkillBundleCase]:
    merged: List[SkillBundleCase] = []
    seen: Set[str] = set()
    for case in [*primary, *secondary]:
        case_id = str(case.case_id or "").strip()
        key = case_id or json.dumps(case.as_dict(), ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        merged.append(deepcopy(case))
    return merged


def _evidence_empty(evidence: SkillEvidence) -> bool:
    return not any(evidence.as_dict().values())


def _coerce_artifact(value: SkillArtifact | Dict[str, Any] | None) -> SkillArtifact:
    if isinstance(value, SkillArtifact):
        value.tags = _artifact_tags(value)
        if not isinstance(value.interface, SkillInterface):
            value.interface = _coerce_interface(value.interface)
        if not isinstance(value.bundle, SkillBundle):
            value.bundle = _coerce_bundle(value.bundle, default_name=value.name)
        if not isinstance(value.evidence, SkillEvidence):
            value.evidence = _coerce_evidence(value.evidence)
        if not isinstance(value.lineage, SkillLineage):
            value.lineage = _coerce_lineage(value.lineage)
        value.dependency_pins = [_coerce_dependency_pin(item) for item in (value.dependency_pins or [])]
        value.dependencies = [str(item).strip() for item in (value.dependencies or []) if str(item).strip()]
        value.history = list(value.history or [])
        if not value.bundle.bundle_id:
            value.bundle.bundle_id = f"{value.name}.bundle"
        return value
    item = dict(value or {})
    fields = {k: v for k, v in item.items() if k in SkillArtifact.__dataclass_fields__}
    artifact = SkillArtifact(**fields)
    artifact.interface = _coerce_interface(item.get("interface"))
    artifact.bundle = _coerce_bundle(item.get("bundle"), default_name=artifact.name)
    artifact.evidence = _coerce_evidence(item.get("evidence"))
    artifact.lineage = _coerce_lineage(item.get("lineage"))
    artifact.dependency_pins = [
        _coerce_dependency_pin(dep) for dep in (item.get("dependency_pins") or artifact.dependency_pins or [])
    ]
    artifact.dependencies = [
        str(dep).strip() for dep in (item.get("dependencies") or artifact.dependencies or []) if str(dep).strip()
    ]
    artifact.history = list(item.get("history") or artifact.history or [])
    artifact.status = str(item.get("status") or artifact.status or "active")
    artifact.stale = bool(item.get("stale", artifact.stale))
    artifact.metadata = dict(item.get("metadata") or artifact.metadata or {})
    artifact.tags = _artifact_tags(artifact)
    if not artifact.bundle.bundle_id:
        artifact.bundle.bundle_id = f"{artifact.name}.bundle"
    return artifact


def _coerce_interface(value: SkillInterface | Dict[str, Any] | None) -> SkillInterface:
    if isinstance(value, SkillInterface):
        return value
    return SkillInterface(**dict(value or {}))


def _coerce_bundle(
    value: SkillBundle | Dict[str, Any] | None,
    *,
    default_name: str,
) -> SkillBundle:
    if isinstance(value, SkillBundle):
        bundle = value
    else:
        raw = dict(value or {})
        bundle = SkillBundle(
            bundle_id=str(raw.get("bundle_id", "")),
            bundle_version=int(raw.get("bundle_version", 1) or 1),
            fixtures=dict(raw.get("fixtures") or {}),
            contrast_protocol=dict(raw.get("contrast_protocol") or {"with_skill": True, "without_skill": True}),
            maintenance_notes=str(raw.get("maintenance_notes", "")),
        )
        bundle.positive_cases = [_coerce_bundle_case(item) for item in (raw.get("positive_cases") or [])]
        bundle.negative_cases = [_coerce_bundle_case(item) for item in (raw.get("negative_cases") or [])]
        bundle.integration_cases = [_coerce_bundle_case(item) for item in (raw.get("integration_cases") or [])]
    if not bundle.bundle_id:
        bundle.bundle_id = f"{default_name}.bundle"
    return bundle


def _coerce_bundle_case(value: SkillBundleCase | Dict[str, Any]) -> SkillBundleCase:
    if isinstance(value, SkillBundleCase):
        return value
    raw = dict(value or {})
    return SkillBundleCase(
        case_id=str(raw.get("case_id", "")),
        source=str(raw.get("source", "")),
        prompt=str(raw.get("prompt", "")),
        expected=dict(raw.get("expected") or {}),
        context=dict(raw.get("context") or {}),
        tags=[str(item) for item in (raw.get("tags") or [])],
        polarity=str(raw.get("polarity", "positive")),
        contrast_protocol=dict(raw.get("contrast_protocol") or {"with_skill": True, "without_skill": True}),
    )


def _coerce_evidence(value: SkillEvidence | Dict[str, Any] | None) -> SkillEvidence:
    if isinstance(value, SkillEvidence):
        return value
    return SkillEvidence(**dict(value or {}))


def _coerce_lineage(value: SkillLineage | Dict[str, Any] | None) -> SkillLineage:
    if isinstance(value, SkillLineage):
        return value
    return SkillLineage(**dict(value or {}))


def _coerce_dependency_pin(value: DependencyPin | Dict[str, Any]) -> DependencyPin:
    if isinstance(value, DependencyPin):
        return value
    return DependencyPin(**dict(value or {}))


def _coerce_test_case_run(value: SkillTestCaseRun | Dict[str, Any]) -> SkillTestCaseRun:
    if isinstance(value, SkillTestCaseRun):
        value.trace = dict(value.trace or {})
        value.input_payload = dict(value.input_payload or {})
        value.expected_behavior = dict(value.expected_behavior or {})
        value.actual_output = dict(value.actual_output or {})
        value.tool_calls = list(value.tool_calls or [])
        value.trace_summary = dict(value.trace_summary or {})
        value.skill_snapshot = dict(value.skill_snapshot or {})
        value.bundle_case_snapshot = dict(value.bundle_case_snapshot or {})
        value.metadata = dict(value.metadata or {})
        return value
    raw = dict(value or {})
    fields = {k: v for k, v in raw.items() if k in SkillTestCaseRun.__dataclass_fields__}
    run = SkillTestCaseRun(**fields)
    run.trace = dict(raw.get("trace") or run.trace or {})
    run.input_payload = dict(raw.get("input_payload") or run.input_payload or {})
    run.expected_behavior = dict(raw.get("expected_behavior") or run.expected_behavior or {})
    run.actual_output = dict(raw.get("actual_output") or run.actual_output or {})
    run.tool_calls = list(raw.get("tool_calls") or run.tool_calls or [])
    run.trace_summary = dict(raw.get("trace_summary") or run.trace_summary or {})
    run.skill_snapshot = dict(raw.get("skill_snapshot") or run.skill_snapshot or {})
    run.bundle_case_snapshot = dict(raw.get("bundle_case_snapshot") or run.bundle_case_snapshot or {})
    run.metadata = dict(raw.get("metadata") or run.metadata or {})
    return run


def _coerce_test_result(value: SkillTestResult | Dict[str, Any]) -> SkillTestResult:
    if isinstance(value, SkillTestResult):
        value.unit_case_runs = [_coerce_test_case_run(item) for item in (value.unit_case_runs or [])]
        return value
    raw = dict(value or {})
    result = SkillTestResult(
        result_id=str(raw.get("result_id", "")),
        skill_name=str(raw.get("skill_name", "")),
        skill_version=int(raw.get("skill_version", 0) or 0),
        bundle_id=str(raw.get("bundle_id", "")),
        bundle_version=int(raw.get("bundle_version", 0) or 0),
        dependency_versions=dict(raw.get("dependency_versions") or {}),
        run_label=str(raw.get("run_label", "")),
        aggregate=dict(raw.get("aggregate") or {}),
        counterfactual=dict(raw.get("counterfactual") or {}),
        integration_failures=list(raw.get("integration_failures") or []),
        created_at=str(raw.get("created_at", "")),
    )
    result.unit_case_runs = [_coerce_test_case_run(item) for item in (raw.get("unit_case_runs") or [])]
    return result
