"""Shared benchmark/task/result types plus skill-repository re-exports.

Benchmark adapters should use the benchmark-agnostic skill repository model
defined under ``academic.skill_repository`` instead of maintaining a second
copy in ``academic.benchmarks``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

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
class BenchmarkTask:
    benchmark: str  # Benchmark family identifier, e.g. bfcl_v3 or spreadsheet.
    task_id: str  # Stable benchmark-native task id.
    question: Any  # Raw task input in benchmark-native form; for BFCL this is multi-turn message groups.
    expected: Any = None  # Benchmark-native gold target used by verifiers/scorers.
    input_artifacts: Dict[str, Any] = field(default_factory=dict)  # External initial state required for execution, such as files, configs, or environment state.
    metadata: Dict[str, Any] = field(default_factory=dict)  # Extra benchmark-specific routing info, e.g. involved classes, paths, data source.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkResult:
    benchmark: str  # Benchmark family identifier copied from the executed task.
    task_id: str  # Task id of the executed case.
    success: bool  # Runner-level success boolean; benchmark-specific and not always the same as official validity.
    score: float  # Main scalar score exposed by the current runner.
    metrics: Dict[str, Any] = field(default_factory=dict)  # Structured benchmark metrics such as official_valid, call_f1, tokens, steps, and retrieval stats.
    trace: Dict[str, Any] = field(default_factory=dict)  # Full execution trace needed for extraction, replay, attribution, and UI visualization.
    error: Optional[str] = None  # Top-level execution error summary when the run crashes or times out.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


__all__ = [
    "BenchmarkMetadata",
    "BenchmarkResult",
    "BenchmarkTask",
    "DependencyPin",
    "SkillArtifact",
    "SkillBundle",
    "SkillBundleCase",
    "SkillEvidence",
    "SkillInterface",
    "SkillLineage",
    "SkillTestCaseRun",
    "SkillTestResult",
]
