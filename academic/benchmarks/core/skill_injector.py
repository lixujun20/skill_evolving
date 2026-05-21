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


def injector_model_name(default: str | None = None, *, benchmark: str = "") -> str | None:
    prefix = str(benchmark or "").upper().replace("-", "_")
    keys = []
    if prefix:
        keys.append(f"{prefix}_SKILL_INJECTOR_MODEL_NAME")
    keys.append("SKILL_INJECTOR_MODEL_NAME")
    for key in keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return default


def injector_llm_config(default: str, *, benchmark: str = "") -> str:
    prefix = str(benchmark or "").upper().replace("-", "_")
    keys = []
    if prefix:
        keys.append(f"{prefix}_SKILL_INJECTOR_LLM_CONFIG")
    keys.append("SKILL_INJECTOR_LLM_CONFIG")
    for key in keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
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
    This compact view must not include a truncated implementation/body: if a
    skill needs its body to be usable, render it with ``full_skill_prompt_block``.
    """
    interface = artifact.interface
    metadata = artifact.metadata or {}
    lines = [
        f"### {artifact.name}",
        f"type: {artifact.kind}; injection: {artifact.injection_type()}; v{artifact.version}",
    ]
    if metadata.get("executor_low_trust_hint"):
        lines.append(
            "trust: unvalidated hint; use only if it exactly matches this turn, otherwise ignore"
        )
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
    )
    if contract:
        lines.append(f"do: {contract}")
    non_app = metadata.get("non_applicability") or interface.compatibility_notes
    if non_app:
        lines.append(f"do_not: {_clip(non_app, 220)}")
    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        kept: List[str] = []
        total = 0
        for line in lines:
            if kept and total + len(line) + 1 > max_chars:
                break
            kept.append(line)
            total += len(line) + 1
        text = "\n".join(kept)
    return text


def full_skill_prompt_block(artifact: SkillArtifact) -> str:
    block = artifact.prompt_block()
    if (artifact.metadata or {}).get("executor_low_trust_hint"):
        warning = (
            "Trust: unvalidated hint. Use only if it exactly matches this turn, "
            "the current tool schema, and the observed state; otherwise ignore it."
        )
        return f"{block}\n\n{warning}"
    return block


def render_skill_prompt_blocks(
    artifacts: Sequence[SkillArtifact],
    *,
    mode: str = "full",
    budget_chars: int = 0,
    compact_chars_per_skill: int = 900,
) -> str:
    """Render already-selected skills; this does not decide relevance."""

    blocks: List[str] = []
    total_chars = 0
    presentation = (mode or "full").strip().lower()
    for artifact in artifacts:
        if presentation in {"compact", "budget", "summary"}:
            block = compact_skill_prompt_block(artifact, max_chars=compact_chars_per_skill)
        else:
            block = full_skill_prompt_block(artifact)
        is_compact = presentation in {"compact", "budget", "summary"}
        if budget_chars and total_chars + len(block) > budget_chars and blocks:
            break
        if budget_chars and len(block) > budget_chars and is_compact:
            block = _clip(block, budget_chars)
        blocks.append(block)
        total_chars += len(block)
    return "\n\n".join(blocks) if blocks else "(no reusable skill artifacts retrieved)"


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
    llm_config: str = ""
    model_name: str | None = None

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
            "llm_config": self.llm_config,
            "model_name": self.model_name or "",
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
        min_query_overlap: int = 0,
        pending_min_query_overlap: int = 0,
    ) -> None:
        self.mode = (mode or "full").strip().lower()
        self.max_full_skills = max(0, int(max_full_skills))
        self.max_summary_skills = max(0, int(max_summary_skills))
        self.budget_chars = max(0, int(budget_chars))
        self.compact_chars_per_skill = max(160, int(compact_chars_per_skill))
        self.min_query_overlap = max(0, int(min_query_overlap))
        self.pending_min_query_overlap = max(0, int(pending_min_query_overlap))

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
            overlap = len(query_words & _words(artifact.description + " " + artifact.body))
            is_pending = str(getattr(artifact, "status", "")) == "pending" or bool(
                (getattr(artifact, "metadata", {}) or {}).get("is_pending_skill")
            )
            overlap_floor = self.pending_min_query_overlap if is_pending else self.min_query_overlap
            if overlap < overlap_floor:
                result.filtered.append(
                    {
                        "skill_name": artifact.name,
                        "reason": "query_overlap_below_threshold",
                        "lexical_overlap": overlap,
                        "min_query_overlap": overlap_floor,
                    }
                )
                continue
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


LLM_SKILL_INJECTOR_SYSTEM = """You are the skill injector for a benchmark executor.
You run outside the executor context. Your only job is to decide which retrieved reusable skills are relevant enough to show to the executor for the current task/turn.

Rules:
- Select only skills that directly match the current task, data shape, operation, and tool context.
- It is valid and preferred to select zero skills when retrieved candidates are unrelated or too broad.
- Pending skills are lower-confidence candidates; include them only when clearly relevant.
- Low-trust hints are unvalidated candidates; include them only for an exact match and never because they are merely in the same domain.
- Do not select a skill just to fill a quota.
- Return one strict JSON object and no Markdown.

Schema:
{
  "selected_skills": [
    {"skill_name": "name", "reason": "why this helps the current task"}
  ],
  "rejected_skills": [
    {"skill_name": "name", "reason": "why it should stay out of context"}
  ]
}
"""


async def select_skill_context_with_llm(
    artifacts: Sequence[SkillArtifact],
    *,
    query: str,
    llm_config: str,
    model_name: str | None = None,
    presentation_mode: str = "full",
    allowed_injection_types: Iterable[str] | None = None,
    max_selected: int | None = None,
    budget_chars: int = 0,
    compact_chars_per_skill: int = 900,
    benchmark: str = "",
    task_id: str = "",
    phase: str = "executor",
    metadata: Dict[str, Any] | None = None,
) -> SkillInjectionResult:
    """LLM relevance gate followed by deterministic rendering.

    `presentation_mode` controls only the prompt block shown after a skill is
    accepted; it does not disable the LLM relevance decision.
    """

    allowed = {str(item) for item in allowed_injection_types or []}
    candidates = [
        artifact
        for artifact in artifacts
        if not allowed or artifact.injection_type() in allowed
    ]
    injector_config = injector_llm_config(llm_config, benchmark=benchmark)
    injector_model = injector_model_name(model_name, benchmark=benchmark)
    result = SkillInjectionResult(
        mode=f"llm:{presentation_mode}",
        budget_chars=budget_chars,
        llm_config=injector_config,
        model_name=injector_model,
    )
    if not candidates:
        if artifacts:
            result.filtered = [
                {"skill_name": artifact.name, "reason": "injection_type_not_allowed"}
                for artifact in artifacts
                if artifact not in candidates
            ]
        return result

    limit = max(0, int(max_selected if max_selected is not None else len(candidates)))
    if limit <= 0:
        result.filtered = [{"skill_name": artifact.name, "reason": "max_selected_zero"} for artifact in candidates]
        return result

    if str(injector_config or "").strip().lower() in {"mock", "unused"}:
        selected_names = [artifact.name for artifact in candidates[:limit]]
        parsed = {
            "selected_skills": [
                {"skill_name": name, "reason": "mock_llm_selected"} for name in selected_names
            ],
            "rejected_skills": [
                {"skill_name": artifact.name, "reason": "mock_llm_not_in_top_limit"}
                for artifact in candidates[limit:]
            ],
            "mock_llm": True,
        }
    else:
        payload = {
            "benchmark": benchmark,
            "task_id": task_id,
            "phase": phase,
            "query": _clip(query, 1800),
            "max_selected": limit,
            "candidates": [_llm_injector_candidate_payload(artifact) for artifact in candidates],
        }
        try:
            from academic.skill_repository.llm_maintenance import _ask_json, _role_json_block

            parsed = await _ask_json(
                system=LLM_SKILL_INJECTOR_SYSTEM,
                user=_role_json_block(payload),
                llm_config=injector_config,
                model_name=injector_model,
                role="skill_injector",
                metadata={
                    "benchmark": benchmark,
                    "task_id": task_id,
                    "phase": phase,
                    "llm_config": injector_config,
                    "model_name": injector_model,
                    **dict(metadata or {}),
                },
            )
        except Exception as exc:
            selected_names = [artifact.name for artifact in candidates[:limit]]
            parsed = {
                "selected_skills": [
                    {
                        "skill_name": name,
                        "reason": f"llm_injector_failed_fallback:{type(exc).__name__}",
                    }
                    for name in selected_names
                ],
                "rejected_skills": [
                    {
                        "skill_name": artifact.name,
                        "reason": f"llm_injector_failed_and_over_limit:{type(exc).__name__}",
                    }
                    for artifact in candidates[limit:]
                ],
                "fallback": True,
                "error_type": type(exc).__name__,
            }

    selected_by_name = {
        str(item.get("skill_name") or "").strip(): str(item.get("reason") or "llm_selected")
        for item in (parsed.get("selected_skills") or parsed.get("selected") or [])
        if isinstance(item, dict) and str(item.get("skill_name") or "").strip()
    }
    rejected_by_name = {
        str(item.get("skill_name") or "").strip(): str(item.get("reason") or "llm_rejected")
        for item in (parsed.get("rejected_skills") or parsed.get("rejected") or [])
        if isinstance(item, dict) and str(item.get("skill_name") or "").strip()
    }
    selected_count = 0
    total_chars = 0
    for artifact in candidates:
        if artifact.name not in selected_by_name or selected_count >= limit:
            result.filtered.append(
                {
                    "skill_name": artifact.name,
                    "reason": rejected_by_name.get(artifact.name) or "llm_rejected",
                }
            )
            continue
        block = (
            compact_skill_prompt_block(artifact, max_chars=compact_chars_per_skill)
            if (presentation_mode or "full").strip().lower() in {"compact", "budget", "summary"}
            else full_skill_prompt_block(artifact)
        )
        if budget_chars and total_chars + len(block) > budget_chars and result.injected:
            result.filtered.append({"skill_name": artifact.name, "reason": "budget_exceeded_after_llm_selection"})
            continue
        if budget_chars and len(block) > budget_chars:
            block = _clip(block, budget_chars)
        result.injected.append(
            InjectedSkill(
                artifact=artifact,
                decision="llm_select",
                reason=selected_by_name.get(artifact.name) or "llm_selected",
                prompt_block=block,
                prompt_chars=len(block),
            )
        )
        selected_count += 1
        total_chars += len(block)
    known = {artifact.name for artifact in candidates}
    for artifact in artifacts:
        if artifact.name not in known:
            result.filtered.append({"skill_name": artifact.name, "reason": "injection_type_not_allowed"})
    return result


def _llm_injector_candidate_payload(artifact: SkillArtifact) -> Dict[str, Any]:
    metadata = artifact.metadata or {}
    return {
        "skill_name": artifact.name,
        "status": artifact.status,
        "injection_type": artifact.injection_type(),
        "summary": _clip(artifact.description or artifact.interface.summary or artifact.interface.usage, 360),
        "contract": {
            "input": artifact.interface.input_contract,
            "output": artifact.interface.output_contract,
            "invocation": artifact.interface.invocation_contract,
        },
        "match_hints": {
            "domains": list(metadata.get("domains") or []),
            "intent_keywords": list(metadata.get("intent_keywords") or []),
            "allowed_tools": list(metadata.get("allowed_tools") or []),
        },
        "trust": {
            "low_trust_hint": bool(metadata.get("executor_low_trust_hint")),
            "reason": str(metadata.get("executor_low_trust_reason") or ""),
            "promotion_state": str(metadata.get("promotion_state") or ""),
        },
        "non_applicability": _clip(metadata.get("non_applicability") or artifact.interface.compatibility_notes, 260),
    }
