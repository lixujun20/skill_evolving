"""Small JSON-backed store for non-Python benchmark skill artifacts."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List

from academic.benchmarks.types import SkillArtifact


class ArtifactStore:
    def __init__(self, artifacts: Iterable[SkillArtifact] | None = None) -> None:
        self._artifacts: Dict[str, SkillArtifact] = {
            artifact.name: artifact for artifact in artifacts or []
        }

    def add(self, artifact: SkillArtifact) -> None:
        existing = self._artifacts.get(artifact.name)
        if existing:
            artifact.version = existing.version + 1
        self._artifacts[artifact.name] = artifact

    def all(self) -> List[SkillArtifact]:
        return list(self._artifacts.values())

    def retrieve(self, query: str, top_k: int = 5) -> List[SkillArtifact]:
        if not self._artifacts:
            return []
        q = _tokenize(query)
        scored = []
        for artifact in self._artifacts.values():
            scored.append((_cosine(q, _tokenize(artifact.retrieval_text())), artifact))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [artifact for score, artifact in scored[:top_k] if score > 0.0]

    def build_prompt(self, artifacts: List[SkillArtifact] | None = None) -> str:
        target = artifacts if artifacts is not None else self.all()
        if not target:
            return "(no reusable skill artifacts retrieved)"
        return "\n\n".join(artifact.prompt_block() for artifact in target)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([asdict(a) for a in self.all()], ensure_ascii=False, indent=2)
        )

    @classmethod
    def load(cls, path: Path) -> "ArtifactStore":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        if isinstance(raw, dict):
            raw = raw.get("artifacts", [])
        return cls(
            SkillArtifact(
                **{k: v for k, v in item.items() if k in SkillArtifact.__dataclass_fields__}
            )
            for item in raw
        )


def _tokenize(text: str) -> Dict[str, float]:
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|[\u4e00-\u9fff]+", text.lower())
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
