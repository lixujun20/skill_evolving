"""Benchmark-agnostic skill injection budgeting utilities."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence

from academic.benchmarks.core.types import SkillArtifact


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _words(value: Any) -> set[str]:
    return {
        item.lower()
        for item in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|[\u4e00-\u9fff]+", str(value or ""))
        if item
    }


def _structured_text(value: Any, *, limit: int = 260) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        chunks: List[str] = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            chunks.append(f"{key}={item}")
        return _clip("; ".join(chunks), limit)
    if isinstance(value, (list, tuple, set)):
        return _clip(", ".join(str(item) for item in value if item), limit)
    return _clip(value, limit)


def compact_skill_prompt_block(artifact: SkillArtifact, *, max_chars: int = 900) -> str:
    """Return the executor-facing compact view of a skill.

    The full artifact body/evidence is useful for maintenance, but executor
    prompts need only applicability, exact contract, and non-applicability.
    """
    interface = artifact.interface
    metadata = artifact.metadata or {}
    lines = [
        f"### {artifact.name}",
        f"type: {artifact.kind}; injection: {artifact.injection_type()}; v{artifact.version}",
    ]
    if artifact.description:
        lines.append(f"summary: {_clip(artifact.description, 220)}")
    applies = (
        metadata.get("scope")
        or metadata.get("applicability")
        or _structured_text(interface.input_contract, limit=260)
        or _structured_text(metadata.get("domains"), limit=160)
    )
    if applies:
        lines.append(f"applies_when: {applies}")
    allowed_tools = metadata.get("allowed_tools") or interface.output_contract.get("allowed_tools")
    if allowed_tools:
        lines.append(f"tools: {_structured_text(allowed_tools, limit=180)}")
    contract = (
        _structured_text(interface.output_contract, limit=320)
        or _clip(interface.summary or interface.usage, 320)
        or _clip(artifact.body, 320)
    )
    if contract:
        lines.append(f"do: {contract}")
    non_app = metadata.get("non_applicability") or interface.compatibility_notes
    if non_app:
        lines.append(f"do_not: {_clip(non_app, 220)}")
    elif artifact.body and artifact.body != contract:
        body_lower = artifact.body.lower()
        marker = "do not"
        idx = body_lower.find(marker)
        if idx >= 0:
            lines.append(f"do_not: {_clip(artifact.body[idx:], 220)}")
    return _clip("\n".join(lines), max_chars)


def full_skill_prompt_block(artifact: SkillArtifact) -> str:
    return artifact.prompt_block()


@dataclass
class InjectedSkill:
    artifact: SkillArtifact
    decision: str
    reason: str
    prompt_block: str
    prompt_chars: int

    def as_event(self) -> Dict[str, Any]:
        return {
            "skill_name": self.artifact.name,
            "decision": self.decision,
            "reason": self.reason,
            "prompt_chars": self.prompt_chars,
            "injection_type": self.artifact.injection_type(),
            "kind": self.artifact.kind,
        }


@dataclass
class SkillInjectionResult:
    injected: List[InjectedSkill] = field(default_factory=list)
    filtered: List[Dict[str, Any]] = field(default_factory=list)
    mode: str = "full"
    budget_chars: int = 0

    @property
    def artifacts(self) -> List[SkillArtifact]:
        return [item.artifact for item in self.injected]

    def prompt(self) -> str:
        if not self.injected:
            return "(no reusable skill artifacts retrieved)"
        return "\n\n".join(item.prompt_block for item in self.injected)

    def as_event(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "budget_chars": self.budget_chars,
            "injected": [item.as_event() for item in self.injected],
            "filtered": list(self.filtered),
            "injected_count": len(self.injected),
            "filtered_count": len(self.filtered),
            "prompt_chars": len(self.prompt()),
        }


class BudgetSkillInjector:
    """Deterministic context-aware injector.

    This is intentionally cheap: it filters and compresses already-retrieved
    candidates without an extra LLM call. A later LLM injector can share the
    same result schema.
    """

    def __init__(
        self,
        *,
        mode: str = "full",
        max_full_skills: int = 1,
        max_summary_skills: int = 1,
        budget_chars: int = 2200,
        compact_chars_per_skill: int = 900,
    ) -> None:
        self.mode = (mode or "full").strip().lower()
        self.max_full_skills = max(0, int(max_full_skills))
        self.max_summary_skills = max(0, int(max_summary_skills))
        self.budget_chars = max(0, int(budget_chars))
        self.compact_chars_per_skill = max(160, int(compact_chars_per_skill))

    @classmethod
    def from_env(cls, *, prefix: str = "SKILL_INJECTOR") -> "BudgetSkillInjector":
        return cls(
            mode=os.environ.get(f"{prefix}_MODE", os.environ.get("SKILL_INJECTION_BUDGET_MODE", "full")),
            max_full_skills=_env_int(f"{prefix}_MAX_FULL_SKILLS", _env_int("SKILL_INJECTOR_MAX_FULL_SKILLS", 1)),
            max_summary_skills=_env_int(f"{prefix}_MAX_SUMMARY_SKILLS", _env_int("SKILL_INJECTOR_MAX_SUMMARY_SKILLS", 1)),
            budget_chars=_env_int(f"{prefix}_BUDGET_CHARS", _env_int("SKILL_INJECTOR_BUDGET_CHARS", 2200)),
            compact_chars_per_skill=_env_int(
                f"{prefix}_COMPACT_CHARS_PER_SKILL",
                _env_int("SKILL_INJECTOR_COMPACT_CHARS_PER_SKILL", 900),
            ),
        )

    def select(
        self,
        artifacts: Sequence[SkillArtifact],
        *,
        query: str = "",
        allowed_injection_types: Iterable[str] | None = None,
    ) -> SkillInjectionResult:
        allowed = {str(item) for item in allowed_injection_types or []}
        result = SkillInjectionResult(mode=self.mode, budget_chars=self.budget_chars)
        if self.mode in {"off", "none"}:
            result.filtered = [
                {"skill_name": artifact.name, "reason": "injector_disabled"}
                for artifact in artifacts
            ]
            return result
        query_words = _words(query)
        seen_keys: set[tuple[str, str, str]] = set()
        full_remaining = self.max_full_skills
        summary_remaining = self.max_summary_skills
        total_chars = 0
        for artifact in artifacts:
            if allowed and artifact.injection_type() not in allowed:
                result.filtered.append({"skill_name": artifact.name, "reason": "injection_type_not_allowed"})
                continue
            if not artifact.retrieval_enabled():
                result.filtered.append({"skill_name": artifact.name, "reason": "retrieval_disabled"})
                continue
            key = (
                artifact.injection_type(),
                str((artifact.metadata or {}).get("allowed_tools") or ""),
                str((artifact.metadata or {}).get("scope") or (artifact.metadata or {}).get("domains") or "")[:100].lower(),
            )
            if key in seen_keys:
                result.filtered.append({"skill_name": artifact.name, "reason": "redundant_candidate"})
                continue
            seen_keys.add(key)
            if self.mode == "full":
                block = full_skill_prompt_block(artifact)
                decision = "inject_full"
            elif self.mode in {"compact", "budget", "summary"}:
                if full_remaining > 0 and len(full_skill_prompt_block(artifact)) <= self.compact_chars_per_skill:
                    block = full_skill_prompt_block(artifact)
                    decision = "inject_full"
                    full_remaining -= 1
                elif summary_remaining > 0:
                    block = compact_skill_prompt_block(artifact, max_chars=self.compact_chars_per_skill)
                    decision = "inject_summary"
                    summary_remaining -= 1
                else:
                    result.filtered.append({"skill_name": artifact.name, "reason": "summary_slot_limit"})
                    continue
            else:
                block = compact_skill_prompt_block(artifact, max_chars=self.compact_chars_per_skill)
                decision = "inject_summary"
            if self.budget_chars and total_chars + len(block) > self.budget_chars and result.injected:
                result.filtered.append({"skill_name": artifact.name, "reason": "budget_exceeded"})
                continue
            if self.budget_chars and len(block) > self.budget_chars:
                block = _clip(block, self.budget_chars)
            overlap = len(query_words & _words(artifact.description + " " + artifact.body))
            result.injected.append(
                InjectedSkill(
                    artifact=artifact,
                    decision=decision,
                    reason=f"retrieved_candidate; lexical_overlap={overlap}",
                    prompt_block=block,
                    prompt_chars=len(block),
                )
            )
            total_chars += len(block)
        return result
