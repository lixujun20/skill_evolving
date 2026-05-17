"""Benchmark adapters for the academic skill-evolving framework."""

from academic.benchmarks.core.registry import BENCHMARK_REGISTRY, get_benchmark
from academic.benchmarks.core.types import (
    BenchmarkMetadata,
    BenchmarkResult,
    BenchmarkTask,
    SkillArtifact,
)

__all__ = [
    "BENCHMARK_REGISTRY",
    "BenchmarkMetadata",
    "BenchmarkResult",
    "BenchmarkTask",
    "SkillArtifact",
    "get_benchmark",
]
