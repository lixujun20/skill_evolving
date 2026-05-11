"""JSON-backed store for benchmark-agnostic skill artifacts and test results."""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Set

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


class ArtifactStore:
    def __init__(
        self,
        artifacts: Iterable[SkillArtifact] | None = None,
        *,
        test_results: Iterable[SkillTestResult] | None = None,
    ) -> None:
        coerced_artifacts = [_coerce_artifact(artifact) for artifact in (artifacts or [])]
        self._artifacts: Dict[str, SkillArtifact] = {
            artifact.name: artifact for artifact in coerced_artifacts
        }
        self._test_results: List[SkillTestResult] = [
            _coerce_test_result(item) for item in (test_results or [])
        ]
        self.refresh_all_dependencies()

    def add(self, artifact: SkillArtifact) -> None:
        artifact = _coerce_artifact(artifact)
        artifact.dependencies = self._detect_dependencies(artifact)
        existing = self._artifacts.get(artifact.name)
        if existing:
            version_kind = artifact.version_kind()
            self._inherit_long_lived_assets(existing, artifact)
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

    def _inherit_long_lived_assets(self, existing: SkillArtifact, incoming: SkillArtifact) -> None:
        """Preserve durable assets when an extractor emits a same-name update.

        LLM extraction often returns only the semantic skill card. The bound
        bundle/evidence/dependency decisions are repository-maintained assets
        and must not be silently erased by a same-name content refresh.
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
        if _evidence_empty(incoming.evidence) and not _evidence_empty(existing.evidence):
            incoming.evidence = deepcopy(existing.evidence)
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
        predicate: Callable[[SkillArtifact], bool] | None = None,
        rerank_key: Callable[[SkillArtifact], tuple] | None = None,
        debug_context: Dict[str, Any] | None = None,
    ) -> List[SkillArtifact]:
        audit = self.retrieve_audit(
            query,
            top_k=top_k,
            predicate=predicate,
            rerank_key=rerank_key,
            debug_context=debug_context,
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
        predicate: Callable[[SkillArtifact], bool] | None = None,
        rerank_key: Callable[[SkillArtifact], tuple] | None = None,
        debug_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        context = dict(debug_context or {})
        if not self._artifacts:
            return {
                "query": query,
                "top_k": top_k,
                "context": context,
                "store_summary": {"n_total": 0, "n_active": 0, "n_stale": 0, "n_disabled": 0},
                "candidates": [],
                "selected": [],
            }
        q = _tokenize(query)
        candidates: List[Dict[str, Any]] = []
        scored: List[tuple[float, tuple, SkillArtifact, Dict[str, Any]]] = []
        for artifact in self._artifacts.values():
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
                "metadata": {
                    "intent_keywords": list(artifact.metadata.get("intent_keywords") or []),
                    "allowed_tools": list(artifact.metadata.get("allowed_tools") or []),
                    "source_task_ids": list(artifact.metadata.get("source_task_ids") or []),
                    "source": artifact.metadata.get("source"),
                },
            }
            if not artifact.retrieval_enabled():
                row.update({"predicate_passed": False, "filter_reason": "retrieval_disabled", "score": 0.0, "rerank": []})
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
                row.update({"predicate_passed": False, "filter_reason": filter_reason or "predicate_false", "score": 0.0, "rerank": []})
                candidates.append(row)
                continue
            score = _cosine(q, _tokenize(artifact.retrieval_text()))
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
                    "rerank": list(rerank),
                    "retrieval_text_chars": len(artifact.retrieval_text()),
                }
            )
            candidates.append(row)
            scored.append((score, rerank, artifact, row))
        scored.sort(key=lambda item: (item[0], *item[1]), reverse=True)
        selected_rows: List[Dict[str, Any]] = []
        selected_names = set()
        for rank, (score, rerank, artifact, row) in enumerate(scored, start=1):
            row["rank"] = rank
            selected = rank <= top_k and score > 0.0
            row["selected"] = selected
            if selected:
                selected_names.add(artifact.name)
                selected_rows.append(
                    {
                        "name": artifact.name,
                        "rank": rank,
                        "score": round(float(score), 6),
                        "rerank": list(rerank),
                    }
                )
        for row in candidates:
            row.setdefault("rank", None)
            row.setdefault("selected", row["name"] in selected_names)
        return {
            "query": query,
            "top_k": top_k,
            "context": context,
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
    ) -> str:
        target = artifacts if artifacts is not None else self.all()
        if include_types is not None:
            allowed: Set[str] = {str(item) for item in include_types}
            target = [artifact for artifact in target if artifact.injection_type() in allowed]
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
    return snapshot


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
