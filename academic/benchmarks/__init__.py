"""Benchmark adapters for the academic skill-evolving framework."""

from academic.benchmarks.registry import BENCHMARK_REGISTRY, get_benchmark
from academic.benchmarks.types import (
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
