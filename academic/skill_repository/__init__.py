"""Common skill repository package.

This package is benchmark-agnostic. Benchmark adapters should depend on these
types/stores rather than defining their own repository model inline.
"""

from academic.skill_repository.store import ArtifactStore
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

__all__ = [
    "ArtifactStore",
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
