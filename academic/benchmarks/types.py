"""Shared benchmark and skill-artifact data structures.

The existing academic pipeline is math-specific and stores reusable skills as
Python helper functions.  The adapters in this package need a broader contract:
BFCL skills are mostly tool-use rules, while spreadsheet skills are closer to a
small skill directory with markdown instructions and optional scripts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BenchmarkMetadata:
    name: str
    display_name: str
    source_url: str
    introduced: str
    task_type: str
    primary_metric: str
    skill_format: str
    recommended_models: List[str]
    saturation_note: str
    engineering_cost: str
    runnable_stage: str
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillArtifact:
    """A reusable skill in a benchmark-native format."""

    name: str
    kind: str
    description: str
    body: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    usage_count: int = 0
    success_count: int = 0

    def retrieval_text(self) -> str:
        return (
            f"{self.name}\nkind: {self.kind}\n{self.description}\n"
            f"{self.body}\nmetadata: {self.metadata}"
        )

    def prompt_block(self) -> str:
        return (
            f"### {self.name} ({self.kind}, v{self.version})\n"
            f"{self.description}\n\n{self.body}"
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkTask:
    benchmark: str
    task_id: str
    question: Any
    expected: Any = None
    input_artifacts: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkResult:
    benchmark: str
    task_id: str
    success: bool
    score: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    trace: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)
