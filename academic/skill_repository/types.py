"""Benchmark-agnostic skill repository data structures.

These types describe versioned skill artifacts, their maintenance bundles,
ephemeral test results, and dependency/version metadata. They are intended to
be reusable across benchmarks, and should not depend on any BFCL- or
spreadsheet-specific execution contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SkillInterface:
    """Benchmark-agnostic interface contract for a skill artifact."""

    summary: str = ""  # One-line contract summary used in docs, prompts, and UI cards.
    usage: str = ""  # Human-readable instruction for when/how the skill should be applied.
    input_contract: Dict[str, Any] = field(default_factory=dict)  # Structured assumptions about required context, fields, ids, or tool preconditions.
    output_contract: Dict[str, Any] = field(default_factory=dict)  # Structured expectations about what the skill produces or guarantees after use.
    invocation_contract: Dict[str, Any] = field(default_factory=dict)  # How the skill is invoked: prompt-only, tool-backed, ordering constraints, etc.
    compatibility_notes: str = ""  # Forward/backward compatibility notes used during stale handling and major/minor updates.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillBundleCase:
    """Long-lived test case bound to a skill artifact."""

    case_id: str  # Stable bundle-local identifier; survives across repeated maintenance runs.
    source: str  # Provenance label such as train_positive / distilled_success / integration_failure / manual.
    prompt: str  # Minimal natural-language case prompt given to the executor when replaying this case.
    expected: Dict[str, Any] = field(default_factory=dict)  # Assertions used for verification, e.g. expected tool calls or official_valid.
    context: Dict[str, Any] = field(default_factory=dict)  # Extra execution context, typically task_fragment / focus_turns / focus_tools / source_task_id.
    tags: List[str] = field(default_factory=list)  # Searchable labels for maintenance reports and later filtering.
    polarity: str = "positive"  # Whether this case should demonstrate help, regression protection, or integration failure behavior.
    contrast_protocol: Dict[str, Any] = field(
        default_factory=lambda: {"with_skill": True, "without_skill": True}
    )  # Declares whether this case participates in with/without counterfactual testing.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillBundle:
    """Stable maintenance-test bundle. Does not store run results."""

    bundle_id: str = ""  # Stable identity for the long-lived test asset attached to a skill.
    bundle_version: int = 1  # Version of the bundle asset itself; can evolve independently from the skill version.
    positive_cases: List[SkillBundleCase] = field(default_factory=list)  # Cases where the skill should improve validity/accuracy/efficiency.
    negative_cases: List[SkillBundleCase] = field(default_factory=list)  # Regression guards and "bad variant should fail" cases.
    integration_cases: List[SkillBundleCase] = field(default_factory=list)  # Cases distilled from real multi-skill or replay failures.
    fixtures: Dict[str, Any] = field(default_factory=dict)  # Shared lightweight execution context reused across cases.
    contrast_protocol: Dict[str, Any] = field(
        default_factory=lambda: {"with_skill": True, "without_skill": True}
    )  # Bundle-level default for counterfactual execution policy.
    maintenance_notes: str = ""  # Free-form rationale explaining why these cases define the current contract.

    def all_cases(self) -> List[SkillBundleCase]:
        return [*self.positive_cases, *self.negative_cases, *self.integration_cases]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "positive_cases": [case.as_dict() for case in self.positive_cases],
            "negative_cases": [case.as_dict() for case in self.negative_cases],
            "integration_cases": [case.as_dict() for case in self.integration_cases],
            "fixtures": self.fixtures,
            "contrast_protocol": self.contrast_protocol,
            "maintenance_notes": self.maintenance_notes,
        }


@dataclass
class SkillEvidence:
    """Accumulated evidence used for maintenance and refactor decisions."""

    source_traces: List[Dict[str, Any]] = field(default_factory=list)  # Raw or summarized traces from which the skill was originally extracted.
    helpful_cases: List[Dict[str, Any]] = field(default_factory=list)  # Concrete examples where the skill improved the run.
    harmful_cases: List[Dict[str, Any]] = field(default_factory=list)  # Concrete examples where the skill caused or amplified failure.
    repeated_evidence: List[Dict[str, Any]] = field(default_factory=list)  # Cross-task repeated patterns used for refactor/generalization decisions.
    integration_failures: List[Dict[str, Any]] = field(default_factory=list)  # Multi-skill or end-to-end failures later attributed to this skill's scope.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillLineage:
    """Lineage metadata for versioned skill artifacts."""

    parent_version: Optional[int] = None  # Numeric version directly preceding this one.
    parent_version_id: str = ""  # Human-readable pointer like skill_name@v3 for UI and reports.
    version_kind: str = "seed"  # seed / minor / major / rollback / refactor.
    migration_reason: str = ""  # Why a major migration or contract rewrite happened.
    refined_from_result_ids: List[str] = field(default_factory=list)  # Maintenance test result ids that directly triggered this version.
    refactor_group_id: str = ""  # Optional group id when several skills are rewritten together in one refactor batch.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DependencyPin:
    """Explicit dependency decision for an upstream skill version."""

    skill_name: str  # Upstream dependency name.
    min_version: Optional[int] = None  # Lowest acceptable upstream version when floating within a compatible family.
    pinned_version: Optional[int] = None  # Exact upstream version to lock to when lazy migration chooses legacy compatibility.
    compatibility_mode: str = "floating"  # floating / pinned / custom repository policy.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillTestCaseRun:
    """Per-case runtime metrics for one maintenance test execution."""

    case_id: str  # Which bundle case this row belongs to.
    variant: str  # without_skill / with_skill / bundle_only / other benchmark-specific replay mode.
    passed: bool  # Variant-level pass/fail after benchmark verification.
    accuracy: Optional[float] = None  # Numeric task quality proxy when the benchmark exposes one.
    validity: Optional[bool] = None  # Canonical boolean correctness signal, e.g. BFCL official_valid.
    tokens: Optional[int] = None  # Token cost for this one replay.
    steps: Optional[int] = None  # Number of model steps / tool-call rounds used for this replay.
    failure_summary: str = ""  # Short textual reason for failure in reports and refine prompts.
    trace_ref: str = ""  # Pointer to the underlying task id or external trace identifier.
    trace: Dict[str, Any] = field(default_factory=dict)  # Full replay trace for this variant when captured; enables UI/debug inspection beyond pass/fail summaries.
    input_payload: Dict[str, Any] = field(default_factory=dict)  # Exact executor/test input for this variant, including task fragment and injected skill policy.
    expected_behavior: Dict[str, Any] = field(default_factory=dict)  # Expected calls, validity, forbidden calls, or other case-level oracle data used during this run.
    actual_output: Dict[str, Any] = field(default_factory=dict)  # Structured executor output for this variant, including metrics and final trace summary.
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # Flattened tool calls produced by this variant, copied out for lightweight UI inspection.
    trace_summary: Dict[str, Any] = field(default_factory=dict)  # Compact trace summary used by reports without opening the full raw trace.
    skill_snapshot: Dict[str, Any] = field(default_factory=dict)  # Tested skill version/content snapshot visible to this variant, empty for without_skill.
    bundle_case_snapshot: Dict[str, Any] = field(default_factory=dict)  # Bundle case snapshot used to produce this run.
    metadata: Dict[str, Any] = field(default_factory=dict)  # Extra benchmark-specific fields such as call_f1, polarity, and error lists.

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillTestResult:
    """Ephemeral test result artifact saved per run, separate from bundle."""

    result_id: str  # Unique id for this maintenance execution; immutable once written.
    skill_name: str  # Skill tested in this run.
    skill_version: int  # Exact skill version under test.
    bundle_id: str  # Bundle identity used for the test.
    bundle_version: int  # Exact bundle version used for the test.
    dependency_versions: Dict[str, int] = field(default_factory=dict)  # Snapshot of pinned upstream versions at test time.
    run_label: str = ""  # Human-readable label such as llm_bundle_unit / post_refine_regression.
    unit_case_runs: List[SkillTestCaseRun] = field(default_factory=list)  # Fine-grained case-level replay outcomes.
    aggregate: Dict[str, Any] = field(default_factory=dict)  # Bundle-level summary metrics and pass/fail decision.
    counterfactual: Dict[str, Any] = field(default_factory=dict)  # With-vs-without deltas used by refine and reporting.
    integration_failures: List[Dict[str, Any]] = field(default_factory=list)  # Failure payloads that can be appended back into long-lived bundle assets.
    created_at: str = ""  # Timestamp when this test result object was generated.

    def as_dict(self) -> Dict[str, Any]:
        return {
            "result_id": self.result_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "dependency_versions": self.dependency_versions,
            "run_label": self.run_label,
            "unit_case_runs": [item.as_dict() for item in self.unit_case_runs],
            "aggregate": self.aggregate,
            "counterfactual": self.counterfactual,
            "integration_failures": self.integration_failures,
            "created_at": self.created_at,
        }


@dataclass
class SkillArtifact:
    """A reusable skill in a benchmark-native format."""

    name: str  # Stable repository key; same logical skill across versions keeps the same name.
    kind: str  # High-level category such as workflow_guardrail_card or atomic_tool_rule_card.
    description: str  # Short summary used in retrieval, UI lists, and prompts.
    body: str  # Full actionable skill content shown to the model or returned by skill tools.
    metadata: Dict[str, Any] = field(default_factory=dict)  # Benchmark-specific retrieval hints, provenance, disable flags, and auxiliary annotations.
    tags: List[str] = field(default_factory=list)  # Controlled retrieval labels, e.g. domain:TravelAPI, tool:create_ticket, intent:reuse_id.
    version: int = 1  # Monotonic artifact version managed by ArtifactStore.
    usage_count: int = 0  # Optional runtime usage counter for reporting and pruning decisions.
    success_count: int = 0  # Optional count of successful uses for later utility heuristics.
    interface: SkillInterface = field(default_factory=SkillInterface)  # Explicit contract separate from free-form body text.
    bundle: SkillBundle = field(default_factory=SkillBundle)  # Long-lived maintenance test asset bound to this skill version family.
    evidence: SkillEvidence = field(default_factory=SkillEvidence)  # Accumulated support and failure evidence behind the skill.
    status: str = "active"  # active / pending / stale / disabled / rejected / archived, etc.
    lineage: SkillLineage = field(default_factory=SkillLineage)  # Version ancestry and migration metadata.
    dependency_pins: List[DependencyPin] = field(default_factory=list)  # Explicit version decisions for upstream dependencies.
    dependencies: List[str] = field(default_factory=list)  # Names of other skills this artifact conceptually relies on.
    history: List[Dict[str, Any]] = field(default_factory=list)  # Serialized snapshots of older versions retained for rollback and audit.
    stale: bool = False  # Whether upstream changes require lazy downstream compatibility handling before trusted reuse.

    def injection_type(self) -> str:
        explicit = str(self.metadata.get("injection_type", "")).strip().lower()
        if explicit in {"functional", "informational", "workflow"}:
            return explicit
        if self.kind in {"executable_tool", "function_tool", "script_tool"}:
            return "functional"
        if "workflow" in self.kind or self.kind == "planning_card":
            return "workflow"
        return "informational"

    def is_disabled(self) -> bool:
        return bool(self.metadata.get("disabled")) or self.status == "disabled"

    def retrieval_enabled(self) -> bool:
        # Pending skills are repository candidates, not execution-time aids.
        # They can participate in maintenance/refactor, but must not pollute
        # executor retrieval before posterior evidence promotes them.
        return not self.is_disabled() and self.status not in {"pending", "rejected", "archived"}

    def version_kind(self) -> str:
        explicit = str(self.metadata.get("version_kind", "")).strip().lower()
        if explicit in {"seed", "minor", "major", "rollback", "refactor"}:
            return explicit
        lineage_kind = str(self.lineage.version_kind or "").strip().lower()
        if lineage_kind:
            return lineage_kind
        return "seed"

    def version_id(self) -> str:
        return f"{self.name}@v{self.version}"

    def dependency_version_map(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for item in self.dependency_pins:
            if item.pinned_version is not None:
                out[item.skill_name] = int(item.pinned_version)
        return out

    def retrieval_text(self) -> str:
        interface_bits = [
            self.interface.summary,
            self.interface.usage,
            self.interface.compatibility_notes,
        ]
        return (
            f"{self.name}\nkind: {self.kind}\n{self.description}\n"
            f"{self.body}\ninterface: {' '.join(bit for bit in interface_bits if bit)}\n"
            f"tags: {' '.join(self.tags)}\n"
            f"metadata: {self.metadata}"
        )

    def prompt_block(self) -> str:
        header = (
            f"### {self.name} ({self.kind}, {self.injection_type()}, "
            f"{self.version_kind()}, v{self.version})"
        )
        if self.stale:
            header += " [stale]"
        return f"{header}\n{self.description}\n\n{self.body}"

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["interface"] = self.interface.as_dict()
        payload["bundle"] = self.bundle.as_dict()
        payload["evidence"] = self.evidence.as_dict()
        payload["lineage"] = self.lineage.as_dict()
        payload["dependency_pins"] = [item.as_dict() for item in self.dependency_pins]
        payload["version_id"] = self.version_id()
        payload["version_kind"] = self.version_kind()
        payload["dependency_versions"] = self.dependency_version_map()
        return payload
