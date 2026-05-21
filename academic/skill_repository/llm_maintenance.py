"""LLM-driven, benchmark-agnostic skill maintenance helpers.

The goal of this module is to mirror the main system's extractor/refiner
philosophy, but for generic skill artifacts rather than math-only Python code.
It provides:

- trace -> skill artifact extraction
- trace/failure -> skill-scoped bundle case distillation
- trace -> per-skill credit assignment
- test failure -> semantic refine / rollback / disable decisions
- stale downstream -> lazy compatibility decision
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from academic.config import EXTRACT_MODEL, LLM_CALL_TIMEOUT
from academic.skill_repository.types import (
    DependencyPin,
    SkillArtifact,
    SkillBundle,
    SkillBundleCase,
    SkillEvidence,
    SkillInterface,
    SkillLineage,
)
from app.meta_agent_tool.str_replace_editor import StrReplaceEditor


def _bundle_case_limit_per_polarity() -> int:
    try:
        return max(1, int(os.environ.get("BFCL_BUNDLE_CASE_LIMIT_PER_POLARITY", "2") or "2"))
    except Exception:
        return 2


def _bundle_max_total_cases() -> int:
    try:
        return max(1, int(os.environ.get("BFCL_BUNDLE_MAX_TOTAL_CASES", "6") or "6"))
    except Exception:
        return 6


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


EXTRACT_SYSTEM = """\
You are the extractor role in a skill-repository maintenance system.

Input contract:
- You receive only the current trace bundle, compact task/tool snapshots, and optional extractor rules of thumb.
- Do not infer a skill from unrelated repository contents.
- A trace may contain successful behavior, failed calls, retries, unused retrieved skills, and final checker feedback.

Software-engineering norms:
1. Correctness first: extract only behavior causally supported by the trace evidence. A skill must preserve the tool schema, argument names, state dependencies, and task intent shown in evidence.
2. Reusability second: prefer a small reusable invariant over a task-specific transcript. The skill should say when it applies and when it does not apply.
3. Maintainability third: keep the contract focused, testable, and versionable. Split independent rules instead of mixing unrelated tool families or failure causes.
4. Scope control: never transfer a rule across domains/tools unless the trace shows a shared invariant. If evidence is only local, encode the local domain/tool scope explicitly.
5. Failure evidence is valuable: failed calls can reveal missing contracts, but the extracted rule must be the corrected behavior, not merely a ban or symptom label.
6. If a retrieved skill appears unused, irrelevant, or harmful, do not copy its content into a new skill unless the trace independently supports it.
7. If no reusable and testable behavior is supported, return {"artifacts": []}.
8. Do not extract speculative shortcut skills from a single trace. For example, do not claim a lookup/search tool can be skipped unless the official expected calls, tool schema, or observed successful correction proves the skipped tool is unnecessary in that exact scope.
9. Do not encode common-world knowledge, ticker mappings, hidden defaults, or benchmark-specific guesses unless the trace/tool result explicitly provides that value and the tool schema supports reusing it.
10. A single-task extra-call observation is runtime feedback, not by itself a
    reusable skill. If the only evidence is "the model made an extra lookup/time
    check/search call and the official checker preferred fewer calls", return
    no extraction or mark an existing skill for refinement through credit. Do
    not create a new "skip X" skill unless at least two independent task
    segments or an explicit tool/schema contract prove the shortcut.
11. Never use broad domains such as "all", "generic", or "BFCL" to stretch a
    skill. `metadata.domains` must name exact domains observed in evidence, and
    `metadata.allowed_tools` must list only tools directly governed by the
    extracted contract, not every tool that appeared in the task.

Extraction checklist for each proposed skill:
- evidence_span: which trace turn/call/error/result supports it.
- scope: domains, allowed_tools, user intents, and non_applicability.
- contract: exact preconditions, action rule, and expected effect.
- maintainability: whether it should be a new skill, a refinement candidate for an existing named skill, or no extraction.

Field semantics and empty-value rules:
- `artifacts`: [] when evidence is local, speculative, already covered by an
  existing skill, or only shows a one-off extra-call overhead.
- `name`: narrow snake_case contract name. Do not name a broad shortcut such as
  `skip_lookup_for_orders` from one task.
- `kind`: use `interface_contract_card` for exact schema/argument repair,
  `workflow_guardrail_card` for multi-turn/order/state workflows,
  `atomic_tool_rule_card` for a local tool rule, and avoid `executable_tool`
  unless the artifact is actually callable code.
- `body`: must include applicability and non_applicability. If those cannot be
  stated precisely, do not extract.
- `interface.input_contract`: exact domain, required context, and state
  preconditions observed in evidence.
- `interface.output_contract`: exact tool call, argument contract, or workflow
  effect. Do not use placeholders or hidden benchmark guesses.
- `metadata.allowed_tools`: only tools whose usage/arguments/order are governed
  by this skill. Do not dump the task's full tool list.
- `metadata.domains`: exact observed domains only; never use "all" or
  "generic".
- `metadata.source_task_ids`: current official task ids supporting the skill.
- `metadata.maintenance_action`: `new_skill` only for reusable/testable
  contracts; `refine_existing` for evidence that should patch an existing
  skill; `no_extraction` when the evidence should only be recorded.

精品 one-shot examples. Match this level of specificity:

Example A, function/interface contract:
Trace evidence:
- Task: "Remove NVDA from my watchlist."
- Failed call: remove_stock_from_watchlist(stock="NVDA")
- Tool schema excerpt: remove_stock_from_watchlist(symbol: string)
- Corrected call observed or implied by checker: remove_stock_from_watchlist(symbol="NVDA")
Good artifact:
{
  "name": "remove_watchlist_requires_symbol_argument",
  "kind": "interface_contract_card",
  "description": "Use the exact `symbol` parameter when removing an explicit ticker from a trading watchlist.",
  "body": "When the user asks to remove an explicitly named ticker from a TradingBot watchlist, call remove_stock_from_watchlist(symbol=<ticker>). Do not use aliases such as stock, company, ticker_name, or company_name. This rule only applies when the target ticker/symbol is already explicit in the user request or prior state.",
  "interface": {
    "summary": "Trading watchlist removal argument contract.",
    "usage": "Apply before calling remove_stock_from_watchlist for explicit ticker removal.",
    "input_contract": {"domain": "TradingBot", "required_context": ["explicit ticker symbol"]},
    "output_contract": {"tool_call": "remove_stock_from_watchlist(symbol=<ticker>)"},
    "invocation_contract": {"injection_type": "informational"},
    "compatibility_notes": "Do not apply to add_to_watchlist, company-name lookup, or order placement tasks."
  },
  "metadata": {
    "domains": ["TradingBot"],
    "allowed_tools": ["remove_stock_from_watchlist"],
    "intent_keywords": ["watchlist", "remove", "explicit ticker", "schema"],
    "forbid_keywords": ["company name unknown", "lookup symbol first"],
    "evidence_span": "turn 0 failed call remove_stock_from_watchlist(stock='NVDA') against schema requiring symbol",
    "scope": "TradingBot watchlist removal with explicit ticker.",
    "non_applicability": "Do not use for stock orders, watchlist additions, or cases where only a company name is provided.",
    "maintenance_action": "new_skill"
  }
}
Bad artifact: "Always skip stock lookup before trading." It overgeneralizes one local argument contract into an unrelated workflow rule.

Example B, workflow:
Trace evidence:
- Turn 0 books a flight and tool output returns {"booking_id": "B-742"}.
- Turn 1 asks to cancel that booking.
- Failed call: cancel_booking(flight="the Seattle booking")
- Correct behavior: reuse booking_id from prior tool result and call cancel_booking(booking_id="B-742").
Good artifact:
{
  "name": "cancel_travel_booking_reuses_prior_booking_id",
  "kind": "workflow_guardrail_card",
  "description": "Cancel travel bookings by reusing the canonical booking_id from prior booking or lookup results.",
  "body": "For multi-turn TravelAPI cancellation, first identify the canonical booking_id from the previous booking result, invoice, or explicit user-provided id. Then call cancel_booking(booking_id=<id>). Do not pass natural-language flight descriptions, route names, traveler names, or invoice ids as substitutes. If no booking_id is available, retrieve or ask through the available TravelAPI workflow before canceling.",
  "interface": {
    "summary": "Travel cancellation id-reuse workflow.",
    "usage": "Apply when a later turn refers back to a previously booked trip.",
    "input_contract": {"domain": "TravelAPI", "required_context": ["prior booking_id or a way to retrieve it"]},
    "output_contract": {"tool_call_order": ["resolve booking_id if needed", "cancel_booking(booking_id=<id>)"]},
    "invocation_contract": {"injection_type": "workflow"},
    "compatibility_notes": "Do not apply when the user gives a different booking id or asks about insurance/invoices only."
  },
  "metadata": {
    "domains": ["TravelAPI"],
    "allowed_tools": ["cancel_booking"],
    "intent_keywords": ["cancel booking", "reuse id", "multi-turn reference"],
    "forbid_keywords": ["insurance only", "invoice only"],
    "evidence_span": "turn 1 cancellation failed with natural-language flight reference after turn 0 produced booking_id B-742",
    "scope": "Multi-turn TravelAPI cancellation that refers to an earlier booking.",
    "non_applicability": "Do not use for booking creation, support tickets, or cancellations with no resolvable booking_id.",
    "maintenance_action": "new_skill"
  }
}
Bad artifact: a broad travel checklist mentioning insurance, invoices, support, and budgets. Those behaviors were not evidenced by the cancellation failure.

Example C, knowledge/rule:
Trace evidence:
- User provides two exact filenames and asks for their diff.
- Extra exploratory calls find(".") and ls(".") occur before diff.
- The final correct call is diff(file_name1="draft.txt", file_name2="final.txt").
Good artifact:
{
  "name": "diff_explicit_filenames_directly",
  "kind": "atomic_tool_rule_card",
  "description": "When both filenames are explicit, call diff directly instead of exploring the filesystem.",
  "body": "For GorillaFileSystem diff tasks, if the user already provides two exact filenames or paths and asks to compare them, call diff(file_name1=<first>, file_name2=<second>) directly. Avoid find, ls, grep, or cat unless a filename/path is missing, ambiguous, or the task asks for file contents rather than a diff.",
  "interface": {
    "summary": "Direct diff rule for explicit filenames.",
    "usage": "Apply before using filesystem exploration tools in diff requests.",
    "input_contract": {"domain": "GorillaFileSystem", "required_context": ["two explicit filenames", "diff/compare intent"]},
    "output_contract": {"tool_call": "diff(file_name1=<first>, file_name2=<second>)"},
    "invocation_contract": {"injection_type": "informational"},
    "compatibility_notes": "Exploration tools remain valid when filenames are absent or ambiguous."
  },
  "metadata": {
    "domains": ["GorillaFileSystem"],
    "allowed_tools": ["diff"],
    "intent_keywords": ["diff", "compare files", "explicit filenames"],
    "forbid_keywords": ["find a file", "unknown filename", "search contents"],
    "evidence_span": "extra find/ls calls preceded a successful diff on two filenames already present in the prompt",
    "scope": "Filesystem diff tasks with two explicit file names.",
    "non_applicability": "Do not ban find/ls globally; they are needed for missing or ambiguous filenames.",
    "maintenance_action": "new_skill"
  }
}
Bad artifact: "Never call find." It is an unsafe negative rule that would fail when filenames must actually be discovered.

Return strict JSON:
{
  "artifacts": [
    {
      "name": "snake_case_name",
      "kind": "workflow_guardrail_card | atomic_tool_rule_card | interface_contract_card | planning_card | executable_tool | shared_subdoc",
      "description": "short summary",
      "body": "actionable content with applicability and non-applicability",
      "interface": {
        "summary": "short interface summary",
        "usage": "when/how to use",
        "input_contract": {},
        "output_contract": {},
        "invocation_contract": {},
        "compatibility_notes": ""
      },
      "metadata": {
        "domains": [],
        "allowed_tools": [],
        "intent_keywords": [],
        "forbid_keywords": [],
        "source_task_ids": [],
        "source": "llm_trace_extraction",
        "evidence_span": "",
        "scope": "",
        "non_applicability": "",
        "maintenance_action": "new_skill | refine_existing | no_extraction"
      },
      "dependencies": [],
      "dependency_pins": [],
      "version_kind": "seed"
    }
  ]
}
"""

EXTRACTOR_RULE_UPDATE_SYSTEM = """\
You maintain a compact runtime-informed rulebook for the extractor role.

You receive:
- the current extractor rules of thumb
- objective runtime evidence about mature candidate groups produced by the extractor:
  - competing candidate skill alternatives from the same source task
  - raw exposure records showing whether each candidate was retrieved / injected / used
  - raw credit records showing official-validity outcomes and credit judgments
  - raw bundle-test records when available

Your job:
1. First write a free-form `analysis` field that interprets retrieved,
   injected, used, helpful, harmful, per-exposure, strict/official, bundle, and
   winner/loser differences.
2. Then infer a small set of reusable extractor rules that improve future
   extraction quality.
3. Prefer rules about scope control, local contract precision, anti-pollution,
   and reusable/generalizable skill boundaries.
4. Compare candidates within the same candidate group; do not judge a single
   skill in isolation.
5. Keep at most {max_rules} active rules.
6. Merge duplicates or near-duplicates instead of rephrasing the same point many times.
7. Remove stale or weak rules if stronger replacements exist.
8. Rules must be concise, imperative, and actionable by the extractor prompt.
9. Do not emit role-management advice like "look at the evidence carefully"; emit concrete extraction behavior rules.
10. Do not infer quality from zero or low use unless the record explicitly passed the maturity/opportunity gates.
11. Treat winner_score or summaries as bookkeeping only; ground conclusions in objective exposure_records, credit_records, and bundle_test_records.

Return exactly this delimiter format:
=== ANALYSIS ===
free-form evidence interpretation
=== SUMMARY ===
brief update rationale
=== RULES ===
[scope] one concise actionable rule
[contract] another concise actionable rule
=== END ===
"""

ROLE_RULE_UPDATE_SYSTEMS = {
    "extractor": EXTRACTOR_RULE_UPDATE_SYSTEM,
    "refactorer": """\
You maintain a compact runtime-informed rulebook for the refactorer role.

Input evidence is mature candidate-group feedback from later rollouts. It
contains objective exposure counts, credit records, strict/official outcomes,
bundle-test records, and normalized credit rates.

Your job is to improve future overlap refactoring decisions. Learn when to
extract a shared skill, when to keep residual skills separate, and when a merge
would broaden triggers enough to harm strict exactness.

Priorities:
1. First write a free-form `analysis` field that interprets retrieved,
   injected, used, helpful, harmful, per-exposure, strict/official, bundle, and
   winner/loser differences.
2. Prefer "do not merge" rules when candidates differ in trigger, argument
   binding, state preconditions, or tool schemas.
3. Shared skills must preserve strict-exact behavior; official-valid partial
   gains are not enough if extra/missing calls increase.
4. Use group comparisons and positive-credit-per-exposure rates. Do not archive
   or suppress alternatives without positive harmful evidence.
5. Keep at most {max_rules} active rules.

Return exactly this delimiter format:
=== ANALYSIS ===
free-form evidence interpretation
=== SUMMARY ===
one sentence
=== RULES ===
[merge_guard] one concise actionable refactorer rule
[trigger] another concise actionable refactorer rule
=== END ===
""",
    "refiner": """\
You maintain a compact runtime-informed rulebook for the refiner role.

Input evidence is mature candidate-group feedback from refiner_revision groups:
multiple refined versions of the same parent skill that later competed under
objective exposure, credit, official/strict, and bundle-test records.

Your job is to improve future refinements. Learn when to narrow scope, repair
exact argument/schema contracts, patch workflow ordering, roll back, or keep
the parent unchanged. Do not use extractor-only candidate pairs as evidence.

Priorities:
1. First write free-form analysis interpreting retrieved, injected, used,
   helpful, harmful, per-exposure, strict/official, bundle, and winner/loser
   differences.
2. Prefer small concrete repairs over broad rewrites.
3. Do not refine from weak attribution; require concrete schema/scope/workflow
   evidence.
4. Treat positive-credit-per-exposure and harmful-credit-per-exposure as the
   main normalized signals.
5. Keep at most {max_rules} active rules.

Return exactly this delimiter format:
=== ANALYSIS ===
free-form evidence interpretation
=== SUMMARY ===
one sentence
=== RULES ===
[focus] one concise actionable refiner rule
[focus] another concise actionable refiner rule
=== END ===
""",
}

GROUP_REFINER_ACTION_SYSTEM = """\
You are a macro-stage candidate-group refiner.

You receive mature candidate-group evidence after the group has had enough
opportunity to be retrieved/injected/used. Each group contains competing skill
alternatives from the same source, raw exposure records, raw credit records,
bundle-test records when available, and per-exposure helpful/harmful rates.

First write a free-form `analysis` that interprets the objective evidence.
Consider:
- whether each skill was retrieved, injected, and explicitly used/called
- helpful vs harmful credit per exposure
- strict vs official-valid outcomes
- bundle pass/fail records
- winner/loser differences inside the same group
- whether zero exposure means no opportunity rather than bad content

Then output actions for existing skills only:
- keep: enough helpful evidence and no meaningful harmful evidence
- refine: useful/helpful idea exists but scope, trigger, argument binding, or
  non-applicability must be tightened because harmful/mixed evidence exists
- archive: explicit harmful evidence dominates and there is no helpful signal
- backup: zero/low/neutral evidence; preserve as a non-primary alternative

Prefer refine over archive when a skill has any clear helpful evidence. Do not
archive neutral or zero-exposure skills. Do not rely on winner_score alone.

Return strict JSON:
{
  "analysis": "free-form evidence interpretation grounded in the supplied records",
  "actions": [
    {
      "candidate_group_id": "group id",
      "skill_name": "existing skill name",
      "action": "keep | refine | archive | backup",
      "reason": "objective evidence supporting the action",
      "patch_intent": "for refine only, what scope/trigger/body/interface should change"
    }
  ]
}
"""

CREDIT_ASSIGNMENT_SYSTEM = """\
You are the credit-assignment and maintenance-attribution judge for a
skill-evolving agent system.

You receive one completed task trace together with the set of retrieved /
injected / explicitly used skills. Your job is to judge each candidate skill's
contribution to this one trace and emit executable maintenance evidence.

Input contract:
- You receive a compact task summary, retrieved/injected/used skill lists,
  candidate skill compact projections, focused trace turns, tool calls/tool
  errors, expected calls, official result, and token/step metrics.
- You do not receive full raw traces, full debug events, the whole skill store,
  or unrelated source/replay results. Do not ask for them or invent them.

Important principles:
1. Judge causality, not co-occurrence. A skill being retrieved or injected does
   not mean it helped or harmed.
2. Distinguish three cases carefully:
   - helpful: the skill likely prevented an error, reduced unnecessary steps,
     or improved correctness for this trace.
   - harmful: the skill likely caused, amplified, or biased the model toward an
     incorrect path, wrong schema, wrong literal, wrong workflow, or irrelevant
     domain transfer.
   - neutral: the skill was present but likely had no material effect.
   - uncertain: there is not enough evidence to attribute clear effect.
3. Prompt-only informational/workflow skills can still be harmful even when
   `used_skills` is empty. You must reason from the actual trace, call errors,
   and mismatch between skill scope and task context.
4. Be conservative. Do not mark a skill as helpful or harmful without concrete
   evidence from the task trace.
5. Consider both knowledge-like skills (rules, workflow notes, references) and
   function-like skills (tool-backed helpers, executable helpers). Their
   attribution styles differ:
   - knowledge/workflow skills often influence ordering, stop conditions,
     parameter names, or literal reuse indirectly;
   - function/executable skills often have more direct usage evidence.
6. Prefer local evidence:
   - exact tool calls produced
   - missing / extra / argument-mismatch errors
   - token / step overhead
   - obvious domain mismatch between task and skill scope
7. Irrelevant retrieved/injected skills are neutral or uncertain by default.
   Mark harmful only with direct evidence: explicit use, explicit reference,
   a tool/argument/order error matching the skill content, or a strong trace
   signal that the skill caused the wrong path. Put weak prompt-pollution
   suspicions in evidence.suspected_prompt_pollution instead of treating them
   as negative credit.
8. For each skill, recommend maintenance actions only when the attribution is
   concrete. If harmful credit points to scope pollution, prefer narrowing
   scope. If it points to schema/argument error, identify the exact contract.
9. Suggest bundle cases only when the official task snapshot can be replayed.
   Never invent expected tool calls. Every suggestion must identify the exact
   task-local fragment by focus_turn_indices. If no compact replayable fragment
   exists, set task_fragment_policy to no_replayable_fragment.
10. Positive bundle suggestions require an explicit helpful effect type:
   token_saving, schema_help, workflow_alignment, or correctness_gain.
11. Negative bundle suggestions require harmful attribution. Integration
   suggestions require a concrete cross-skill or contextual failure.

Field semantics and empty-value rules:
- `maintenance_actions`: skill-local actions only. Do not duplicate a top-level
  action list. Use [] when the judgment is neutral/uncertain and no concrete
  maintenance should run.
- `refine_required`: true only when the current skill artifact should be edited
  before bundle testing because the trace identifies a concrete schema, scope,
  workflow, or integration fix.
- `filter_candidate`: true only when the skill should be disabled/revoked or
  removed from retrieval consideration, not merely because evidence is weak.
- `evidence_strength`: strong means direct call/error/success evidence; medium
  means trace behavior strongly matches skill content; weak means circumstantial
  prompt or retrieval noise.
- `attribution_scope`: `direct_use` for explicit tool/helper use,
  `prompt_influence` for injected text likely steering behavior,
  `retrieval_noise` for irrelevant context overhead, `integration_context` for
  interaction between skills/context, and `none` when no causal scope is found.
- `bundle_case_suggestions`: include only cases that can become replayable
  focused official fragments. Use [] for neutral/uncertain judgments unless
  there is a concrete integration diagnostic.
- `focus_turn_indices`: turns whose official user prompt and expected calls are
  sufficient to exercise the skill. Do not use the whole task by default.
- `required_context_turn_indices`: earlier official turns required to make the
  focused turn meaningful, such as a booking id, user preference, or object
  created by a prior tool result. Leave [] when no prior state is needed.
- `state_requirements`: minimal state facts required by the fragment, e.g.
  {"booking_id": "from turn 0 tool result"}. Use {} if the official fragment is
  self-contained.
- `expected_contract`: prose describing what official expected calls/assertions
  should verify. It is not a place to invent new tool calls.
- `task_fragment_policy`: `reuse_official_fragment` only when
  focus_turn_indices identify a compact official fragment. Use
  `no_replayable_fragment` and empty focus/context arrays when the evidence
  cannot be converted into a replayable task fragment.

Return strict JSON:
{
  "task_summary": {
    "task_id": "...",
    "official_valid": true,
    "score": 0.0,
    "n_model_steps": 0,
    "total_tokens": 0
  },
  "skill_judgments": [
    {
      "skill_name": "snake_case_name",
      "judgment": "helpful | harmful | neutral | uncertain",
      "effect_type": "correctness_gain | correctness_harm | token_saving | token_overhead | workflow_alignment | workflow_pollution | schema_help | schema_harm | domain_match | domain_mismatch | no_material_effect | unknown",
      "confidence": 0.0,
      "reason": "short evidence-grounded explanation",
      "maintenance_actions": [
        {
          "action": "keep | narrow_scope | fix_schema_contract | refine_workflow | disable_candidate | add_bundle_case | record_evidence",
          "reason": "why this action follows from the attribution",
          "target_scope": "optional concise scope"
        }
      ],
      "refine_required": false,
      "filter_candidate": false,
      "evidence_strength": "strong | medium | weak",
      "attribution_scope": "direct_use | prompt_influence | retrieval_noise | integration_context | none",
      "bundle_case_suggestions": [
        {
          "polarity": "positive | negative | integration",
          "reason": "why this official task fragment should become a bundle case",
          "source_task_id": "task id from the current official task",
          "focus_turn_indices": [0],
          "required_context_turn_indices": [],
          "state_requirements": {},
          "expected_contract": "short statement of what the case should assert, without inventing calls",
          "task_fragment_policy": "reuse_official_fragment | no_replayable_fragment"
        }
      ],
      "evidence": {
        "retrieved": true,
        "injected": true,
        "used": false,
        "relevant_turn_indices": [],
        "related_tool_names": [],
        "error_refs": [],
        "trace_signals": []
      }
    }
  ]
}
"""

BUNDLE_SYSTEM = """\
You distill long benchmark traces into lightweight, skill-scoped maintenance tests.

Your task is to build bundle cases for ONE skill artifact only. The bundle must
focus on the minimal scope where that skill matters. Do not replay irrelevant
parts of the original task. Cases may cover a single turn, a small subset of
turns, or a reduced interface interaction, as long as they preserve the skill's
causal role.

Rules:
1. Each case must isolate the target skill's role as much as possible.
2. Use both successful and failed evidence when available.
3. Include positive cases where the skill should help.
4. Include negative / regression cases where bad variants of the skill would fail.
5. Include integration-derived cases when the failure only appears in context.
6. Do not include unrelated task segments.
7. If the interface/description implies exact names or literals, encode them in expected assertions.
8. Cases should be lightweight enough to rerun repeatedly.
9. Preserve the benchmark's interaction grain exactly. For BFCL-like function-calling tasks, one user turn may require multiple tool calls; keep those calls in the same `task_fragment.expected` turn as a nested list. Do not invent `next_turn_expected` or split same-turn calls across turns unless the original task has separate user turns.
10. Bundle cases should test the target skill's local causal effect. If the evidence only says "avoid extra lookup X while directly calling Y and Z in this user turn", encode the expected calls for that same user turn as `[["Y(...)", "Z(...)"]]`.
11. Keep the bundle compact. Return at most {bundle_limit} positive case(s), at most {bundle_limit} negative case(s), and at most {bundle_limit} integration case(s), and at most {bundle_total_limit} case(s) total.
12. Each case must contain only the minimal single-turn or two-turn fragment needed to test this skill. Do not copy full traces, full metrics, debug events, or unrelated input artifacts.
13. Keep each `prompt` under 240 characters. Keep each user message under 500 characters. If fixture state is needed, include only the exact fields required by the skill.
14. For BFCL-like function-calling cases, `context.task_fragment.question` must be an array of turns, and each turn must be an array of message objects with `role` and `content`.
15. For BFCL-like expected calls, use only exact official tool names and exact schema parameter names from the source result. Do not invent aliases such as `company_name` when the schema uses `name`, and do not invent `stock` when the schema uses `symbol`.
16. Do not use placeholders such as `<from_lookup>`, `<market_price>`, or non-literal expressions in `task_fragment.expected`; expected calls must be executable literal call strings.
17. The task fragment must come from the same source domain and official task evidence as the target skill. Never borrow a cross-domain fragment or unrelated source task because it looks structurally similar.
18. Return valid JSON that fits comfortably under 3500 output tokens. Do not include explanatory prose outside the schema.

Field semantics and empty-value rules:
- `case_id`: stable id scoped to this skill and polarity, e.g. `skill:positive:0`.
- `source`: why the case exists. Use `train_positive`/`distilled_success` for
  positive evidence, `integration_rewrite` for contextual failures, or `manual`
  only for explicitly supplied human cases.
- `prompt`: short human-readable case label, not a full trace dump.
- `expected`: optional extra assertions outside BFCL official calls. Use {} when
  the official `context.task_fragment.expected` is sufficient.
- `context.task_fragment.question`: official user-turn messages for only the
  focused fragment; preserve nested BFCL turn/message shape.
- `context.task_fragment.expected`: official executable call strings for those
  turns only; no aliases, placeholders, or guessed calls.
- `context.task_fragment.input_artifacts` and `metadata`: copy only official
  snapshot fields needed to replay the fragment. Do not synthesize a new domain.
- `focus_turns`: turn indices included because the skill is exercised there.
- `focus_tools`: exact official tool names exercised by the skill.
- `source_task_id`: official source task id for the fragment.
- `polarity`: `positive` means the skill should help; `negative` catches a bad
  variant; `integration` catches a context/cross-skill failure.
- `contrast_protocol`: booleans describing whether the case is meaningful with
  and/or without the skill. Use both true for most regression-style cases.
- If more than {bundle_total_limit} good cases are available, choose the most
  diagnostic set: recent credit-assigned cases first, then high-confidence
  regression/integration cases, while preserving polarity diversity when useful.
  Do not output overflow cases for a later component to trim.

Return strict JSON:
{
  "maintenance_notes": "brief rationale",
  "positive_cases": [
    {
      "case_id": "skill:positive:0",
      "source": "train_positive | distilled_success | manual | integration_rewrite",
      "prompt": "short case prompt",
      "expected": {},
      "context": {
        "task_fragment": {
          "question": [],
          "expected": [],
          "input_artifacts": {},
          "metadata": {}
        },
        "focus_turns": [],
        "focus_tools": [],
        "source_task_id": ""
      },
      "tags": [],
      "polarity": "positive",
      "contrast_protocol": {
        "with_skill": true,
        "without_skill": true
      }
    }
  ],
  "negative_cases": [],
  "integration_cases": []
}
"""

BUNDLE_SYSTEM = (
    BUNDLE_SYSTEM
    .replace("{bundle_limit}", str(_bundle_case_limit_per_polarity()))
    .replace("{bundle_total_limit}", str(_bundle_max_total_cases()))
)


BUNDLE_MAINTAIN_SYSTEM = """\
You are a lightweight bundle maintenance agent.

You receive one target skill, its current bundle, and incremental new evidence.
The normal existing-skill path is intentionally trace-light: credit assignment
has already selected harmful or useful cases, so do not ask for or depend on
full source traces.
Your job is to choose the cheapest safe maintenance action:
- keep: reuse the current bundle as-is
- patch: minimally edit the current bundle by adding/removing/updating only the few cases needed
- rebuild: rewrite the bundle because the current one is too stale or mis-scoped

Rules:
1. Prefer `keep` when the existing bundle still matches the skill contract and new evidence adds nothing material.
2. Prefer `patch` when a small number of case-level edits can absorb the new evidence.
3. Use `rebuild` only when the existing bundle scope is wrong, contradictory, or too stale for local repair.
4. Keep token use small. Do not copy unchanged cases into the patch payload.
5. For `patch`, modify as little as possible:
   - `add_cases`: new cases to append
   - `drop_case_ids`: stale/harmful cases to remove
   - `replace_cases`: exact case replacements by `case_id`, each with an explicit `bucket`
6. Preserve the benchmark's turn semantics exactly.
7. Treat credit-assigned negative cases and integration failures as high-priority counterexamples, but do not overfit to one noisy trace.
8. Keep the final bundle compact: at most {bundle_limit} positive, {bundle_limit} negative, and {bundle_limit} integration case(s), and at most {bundle_total_limit} cases total.
9. For BFCL-like expected calls, use only exact official tool names and exact schema parameter names from the source result. Never invent aliases such as `company_name` or `stock`, and never use placeholders such as `<market_price>`.
10. New cases must match the target skill's source/domain evidence and the
   official task fragment selected by credit assignment. Never patch in a
   cross-domain task fragment because it is convenient.
11. Return strict JSON only.

Field semantics and empty-value rules:
- `action=keep`: use when the bundle already covers the contract or evidence is
  weak; return empty patch arrays.
- `action=patch`: use for incremental credit-suggested cases or exact repair of
  malformed case fields. Do not copy unchanged cases.
- `action=rebuild`: use only when the current bundle is fundamentally
  mis-scoped or contradictory.
- `patch.add_cases`: only new positive/negative/integration cases selected by
  credit, integration failure, or validation failure.
- `patch.drop_case_ids`: exact stale ids to remove; [] when none.
- `patch.replace_cases`: complete replacement for the named case. Each item
  must include the target `bucket`; [] when no case is replaced.
- Case fields keep the same meanings as in the initial bundle builder:
  `context.task_fragment.expected` is official executable call strings, while
  top-level `expected` is only optional extra assertions.
- If the current bundle plus new evidence exceeds {bundle_total_limit} cases,
  you must decide what to keep/drop in this response. Prefer recent
  credit-assigned cases, high-confidence regressions, and the smallest
  polarity-diverse set that still represents the skill contract.

Return strict JSON:
{
  "action": "keep | patch | rebuild",
  "reason": "brief rationale",
  "maintenance_notes": "brief bundle rationale update",
  "patch": {
    "add_cases": {
      "positive_cases": [],
      "negative_cases": [],
      "integration_cases": []
    },
    "drop_case_ids": [],
    "replace_cases": [
      {
        "bucket": "positive_cases | negative_cases | integration_cases",
        "case_id": "",
        "source": "",
        "prompt": "",
        "expected": {},
        "context": {},
        "tags": [],
        "polarity": "positive | negative | integration",
        "contrast_protocol": {}
      }
    ]
  }
}
"""

BUNDLE_MAINTAIN_SYSTEM = (
    BUNDLE_MAINTAIN_SYSTEM
    .replace("{bundle_limit}", str(_bundle_case_limit_per_polarity()))
    .replace("{bundle_total_limit}", str(_bundle_max_total_cases()))
)


REFINE_SYSTEM = """\
You are refining a versioned skill artifact after maintenance test failures.

You receive:
- the current skill artifact
- failed bundle cases / fresh test results
- integration failures
- recent credit assignment context for this skill
- refinement history
- neighboring dependency summaries

Rules:
1. The bundle is a fixed long-lived test asset unless you explicitly perform a major interface/contract update.
2. If you change interface or description semantics, you MUST also return updated bundle cases consistent with the new contract.
3. Prefer the smallest semantic fix that makes the current tests pass.
4. If the skill is salvageable, return a refined artifact.
5. If the latest version is bad but an older contract should be kept, choose rollback or pin.
6. Disable only if the skill appears harmful and unsalvageable under the current evidence.
7. Minor update: do not remove old tests.
8. Major update: may migrate tests, but provide a migration reason.
9. If the current skill is only a negative prohibition such as "do not call X", prefer refining it into a positive local execution rule when the evidence identifies the correct replacement tool, argument form, literal value, or ordering.
10. For benchmark traces with explicit expected tool calls or literal arguments, prefer adding that positive local anchor instead of returning a purely negative ban.
11. Preserve the benchmark's turn semantics. In BFCL-like traces, multiple tool calls inside one user turn are valid and often required. Do not refine a skill to force those same-turn calls into separate turns unless the expected trace has separate user turns.
12. If tests fail only because the bundle split a flat same-turn expected call list into multiple turns, repair the bundle representation instead of changing the skill behavior.
13. Keep the response compact. Do not copy unchanged artifact fields or unchanged bundle cases.
14. If action is `keep`, return empty `{}` for `artifact` and a bundle with empty case arrays.
15. If action is `refine_minor` or `refine_major`, return only changed fields. Keep `description` under 240 characters and `body` under 900 characters.
16. If returning bundle updates, include at most {bundle_limit} positive, {bundle_limit} negative, and {bundle_limit} integration case(s). Each case must be a minimal fragment, not a copied full trace.
17. The entire response must fit under 3000 output tokens.
18. Do not rewrite a broad skill because of an unrelated task regression. If credit attribution is weak or uncertain, default to `keep`.
19. If harmful credit points to scope pollution, narrow applicability first instead of rewriting the whole function.
20. If harmful credit points to schema or argument mismatch, fix the exact contract and preserve unrelated behavior.
21. If this is a credit pre-refine and the credit context does not identify a concrete schema, scope, workflow, or dependency fix, choose `keep`.
22. If failed bundle cases or integration failures contain `strict_contract_gate`
    or with-skill `contract_failures`, do not claim the case validated the
    current artifact. Choose `refine_minor`, `refine_major`, `disable`, or
    `mark_stale` unless you explicitly identify the failed case as invalid or
    unrelated to this skill. A post-bundle strict failure is not a reason to
    keep by default.
23. Strong harmful credit must not be ignored. If `judgment=harmful` with
    confidence >= 0.65 and `effect_type=domain_mismatch`, return
    `refine_minor` with a retrieval/scope guard such as
    `artifact.metadata.retrieval_guard.excluded_domains`, stricter
    `metadata.domains`, or clearer non-applicability notes. Do not return
    `keep` unless you explicitly explain why the harmful credit is invalid.
24. If credit maintenance actions include `narrow_scope`,
    `fix_schema_contract`, or `refine_workflow`, either apply that local change
    or explain why the action is unsafe. A bare `keep` is invalid for these
    concrete actions.

Field semantics and empty-value rules:
- `decision.action`: `keep` means no semantic change; `refine_minor` means a
  narrow compatible content/scope/interface correction; `refine_major` means a
  contract migration; `rollback`, `disable`, `pin_dependency`, and `mark_stale`
  are repository-control actions.
- `decision.version_kind`: set consistently with the action. Use `minor` for
  compatible edits, `major` for contract changes, `rollback` for rollback, and
  `seed` only for newly created artifacts.
- `decision.pinned_dependencies`: include only concrete upstream pins required
  by a dependency conflict. Use [] otherwise.
- `artifact`: when action is `keep`, return {}. For refine actions, include only
  changed fields; omitted fields are inherited from the current artifact.
- `artifact.interface`: explain exact usage, input contract, output contract,
  invocation contract, and compatibility/non-applicability when those semantics
  change.
- `artifact.dependencies`: list only named skills that this skill actually calls
  or semantically depends on. Use [] if unchanged or none.
- `bundle`: return empty case arrays unless the skill contract changed or the
  failed case itself must be repaired to match official turn semantics.

Return strict JSON:
{
  "decision": {
    "action": "keep | refine_minor | refine_major | rollback | disable | pin_dependency | mark_stale",
    "reason": "brief reason",
    "version_kind": "minor | major | rollback | seed",
    "migration_reason": "",
    "pinned_dependencies": [
      {
        "skill_name": "upstream_skill",
        "pinned_version": 1,
        "compatibility_mode": "pinned"
      }
    ]
  },
  "artifact": {
    "name": "",
    "kind": "",
    "description": "",
    "body": "",
    "interface": {
      "summary": "",
      "usage": "",
      "input_contract": {},
      "output_contract": {},
      "invocation_contract": {},
      "compatibility_notes": ""
    },
    "metadata": {},
    "dependencies": []
  },
  "bundle": {
    "maintenance_notes": "",
    "positive_cases": [],
    "negative_cases": [],
    "integration_cases": []
  }
}
"""

REFINE_SYSTEM = REFINE_SYSTEM.replace("{bundle_limit}", str(_bundle_case_limit_per_polarity()))


STALE_SYSTEM = """\
You are performing lazy downstream compatibility handling for a stale skill.

An upstream dependency changed version. The downstream skill is marked stale.
Decide whether the downstream should:
- stay pinned to an older upstream version,
- be refreshed with a minor compatible update,
- require a major migration,
- or remain stale until enough evidence exists.

Return strict JSON:
{
  "action": "keep_stale | clear_stale | refresh_minor | refresh_major | pin_legacy | rollback",
  "reason": "brief reason",
  "pinned_dependencies": [
    {
      "skill_name": "",
      "pinned_version": 1,
      "compatibility_mode": "pinned"
    }
  ],
  "artifact_updates": {
    "description": "",
    "body": "",
    "interface": {
      "summary": "",
      "usage": "",
      "input_contract": {},
      "output_contract": {},
      "invocation_contract": {},
      "compatibility_notes": ""
    },
    "metadata": {}
  }
}
"""


_MAINTENANCE_TOKEN_EVENTS: List[Dict[str, Any]] = []


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _role_json_block(value: Any, *, preserve_keys: Iterable[str] | None = None) -> str:
    return json.dumps(
        _compact_role_payload(value, preserve_keys=set(preserve_keys or ())),
        ensure_ascii=False,
        indent=2,
    )


def _trim_text(value: str, limit: int = 14000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


_ROLE_DROP_KEYS = {
    "raw",
    "raw_response",
    "input_artifacts",
    "golden_xlsx",
    "prompt_txt_preview",
    "history_tail",
    "lineage",
    "fixtures",
    "version_id",
}

_ROLE_TEXT_LIMITS = {
    "description": 360,
    "reason": 420,
    "failure_summary": 900,
    "stderr": 900,
    "stderr_tail": 900,
    "stdout": 500,
    "stdout_tail": 500,
    "question": 1400,
    "question_preview": 1400,
    "instruction": 1400,
    "user_messages": 1400,
    "raw_error": 700,
}

_ROLE_PRESERVE_TEXT_KEYS = {
    "body",
    "code",
    "code_preview",
    "code_snippet",
    "executable_code",
    "implementation",
    "source_code",
}

_ROLE_LIST_LIMITS = {
    "positive_cases": 4,
    "negative_cases": 4,
    "integration_cases": 4,
    "unit_case_runs": 8,
    "failed_cases": 6,
    "passed_cases": 4,
    "candidate_skills": 8,
    "recent_helpful": 2,
    "recent_harmful": 2,
    "dependency_summaries": 6,
    "refinement_history": 3,
    "integration_failures": 6,
    "contract_validation_failures": 6,
    "credit_cases": 6,
    "credit_context": 6,
    "question": 4,
    "expected": 6,
    "expected_calls": 6,
    "expected_focused": 6,
    "focused_turns": 6,
    "turns": 6,
    "tool_calls": 8,
    "tool_results": 4,
    "notebook_steps": 10,
    "tool_summary": 80,
    "error_summary": 40,
}


def _compact_role_payload(
    value: Any,
    *,
    key: str = "",
    depth: int = 0,
    preserve_keys: set[str] | None = None,
) -> Any:
    preserve_keys = preserve_keys or set()
    if value in (None, ""):
        return None
    if value == []:
        return []
    if value == {}:
        return {}
    if isinstance(value, str):
        if key in _ROLE_PRESERVE_TEXT_KEYS:
            return value
        limit = _ROLE_TEXT_LIMITS.get(key, 1800 if len(value) > 2400 else 0)
        return _trim_text(value, limit=limit) if limit else value
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for child_key, child_value in value.items():
            if child_key in _ROLE_DROP_KEYS and child_key not in preserve_keys:
                continue
            if child_key == "metadata" and isinstance(child_value, dict):
                child_value = {
                    k: child_value.get(k)
                    for k in (
                        "domains",
                        "allowed_tools",
                        "intent_keywords",
                        "source_task_ids",
                        "source_success",
                        "non_applicability",
                        "promotion_state",
                        "disabled_reason",
                    )
                    if child_value.get(k) not in (None, "", [], {})
                }
            compacted = _compact_role_payload(
                child_value,
                key=str(child_key),
                depth=depth + 1,
                preserve_keys=preserve_keys,
            )
            if compacted not in (None, "", [], {}):
                out[str(child_key)] = compacted
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        limit = _ROLE_LIST_LIMITS.get(key)
        truncated = False
        if limit is not None and len(items) > limit:
            items = items[:limit]
            truncated = True
        compacted_items = [
            compacted
            for item in items
            for compacted in [
                _compact_role_payload(
                    item,
                    key=key,
                    depth=depth + 1,
                    preserve_keys=preserve_keys,
                )
            ]
            if compacted not in (None, "", [], {})
        ]
        if truncated:
            compacted_items.append({"truncated_count": len(value) - len(items)})
        return compacted_items
    return str(value)



def _first_nonempty(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _ensure_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, "", [], ()):
        return {}
    return {"value": value}


def _normalize_feedback_rules(
    raw_rules: Iterable[Dict[str, Any]] | None,
    *,
    max_rules: int = 5,
    prefix: str = "extractor_rule",
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_rules or []:
        payload = dict(item or {})
        text = str(payload.get("text") or "").strip()
        if not text:
            continue
        dedupe_key = re.sub(r"\s+", " ", text).strip().lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        focus = str(payload.get("focus") or "").strip().lower() or "evidence"
        out.append(
            {
                "rule_id": str(payload.get("rule_id") or f"{prefix}_{len(out) + 1}"),
                "text": text,
                "focus": focus,
            }
        )
        if len(out) >= max_rules:
            break
    for idx, row in enumerate(out, start=1):
        row["rule_id"] = f"{prefix}_{idx}"
    return out


def _parse_role_rule_update_text(text: str, *, max_rules: int, prefix: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {"analysis": "", "summary": "", "rules": []}
    try:
        parsed = json.loads(_extract_json_text(raw))
        if isinstance(parsed, dict):
            parsed["rules"] = _normalize_feedback_rules(parsed.get("rules"), max_rules=max_rules, prefix=prefix)
            return parsed
    except Exception:
        pass

    def section(name: str) -> str:
        pattern = rf"===\s*{re.escape(name)}\s*===\s*(.*?)(?=\n===\s*[A-Z_ ]+\s*===|\Z)"
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    analysis = section("ANALYSIS")
    summary = section("SUMMARY")
    rules_text = section("RULES")
    rules: List[Dict[str, Any]] = []
    for line in rules_text.splitlines():
        item = line.strip()
        if not item or item == "=== END ===":
            continue
        item = re.sub(r"^\s*[-*]\s*", "", item).strip()
        match = re.match(r"^\[([^\]]+)\]\s*(.+)$", item)
        if match:
            focus = match.group(1).strip().lower() or "evidence"
            text_value = match.group(2).strip()
        else:
            focus = "evidence"
            text_value = item
        if text_value:
            rules.append({"focus": focus, "text": text_value})
    return {
        "analysis": analysis,
        "summary": summary or (analysis[:180] if analysis else ""),
        "rules": _normalize_feedback_rules(rules, max_rules=max_rules, prefix=prefix),
    }


def _role_rule_suffix(
    rules_payload: Iterable[Dict[str, Any]] | None,
    *,
    role_label: str,
    prefix: str,
) -> str:
    rules = _normalize_feedback_rules(rules_payload, prefix=prefix)
    if not rules:
        return ""
    lines = [
        "",
        f"Runtime-informed {role_label} rules of thumb:",
        "- These are learned soft constraints. Direct task evidence, tool schemas, and official trace evidence take priority.",
        "- Do not copy rule text into skill bodies. Use rules only to shape scope, trigger, argument contract, and merge/refactor decisions.",
    ]
    for row in rules:
        lines.append(f"- [{row['focus']}] {row['text']}")
    return "\n".join(lines)


def _extractor_rule_suffix(extractor_rules: Iterable[Dict[str, Any]] | None) -> str:
    return _role_rule_suffix(extractor_rules, role_label="extractor", prefix="extractor_rule")


def _refiner_rule_suffix(refiner_rules: Iterable[Dict[str, Any]] | None) -> str:
    return _role_rule_suffix(refiner_rules, role_label="refiner", prefix="refiner_rule")


def _refactorer_rule_suffix(refactorer_rules: Iterable[Dict[str, Any]] | None) -> str:
    return _role_rule_suffix(refactorer_rules, role_label="refactorer", prefix="refactorer_rule")


def _normalize_usage_payload(usage: Dict[str, Any] | None) -> Dict[str, int]:
    payload = dict(usage or {})
    prompt_tokens = int(payload.get("prompt_tokens") or 0)
    cache_input_tokens = int(
        payload.get("cache_input_tokens")
        or payload.get("cached_tokens")
        or payload.get("cache_read_input_tokens")
        or 0
    )
    completion_tokens = int(payload.get("completion_tokens") or 0)
    total_tokens = int(payload.get("total_tokens") or (prompt_tokens + cache_input_tokens + completion_tokens))
    return {
        "prompt_tokens": prompt_tokens,
        "cache_input_tokens": cache_input_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


class JsonRoleClient:
    """Polymorphic JSON LLM client used by maintenance roles."""

    async def ask_json(
        self,
        *,
        system: str,
        user: str,
        llm_config: str,
        model_name: str | None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def ask_text(
        self,
        *,
        system: str,
        user: str,
        llm_config: str,
        model_name: str | None,
    ) -> Dict[str, Any]:
        return await self.ask_json(system=system, user=user, llm_config=llm_config, model_name=model_name)


class AnthropicJsonRoleClient(JsonRoleClient):
    async def ask_json(
        self,
        *,
        system: str,
        user: str,
        llm_config: str,
        model_name: str | None,
    ) -> Dict[str, Any]:
        from app.config import config

        cfg = config.llm.get(llm_config, config.llm["default"])
        return await _ask_anthropic_json(
            system=system,
            user=user,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=model_name or cfg.model,
            max_tokens=int(cfg.max_tokens or 4096),
        )


class GenericJsonRoleClient(JsonRoleClient):
    async def ask_json(
        self,
        *,
        system: str,
        user: str,
        llm_config: str,
        model_name: str | None,
    ) -> Dict[str, Any]:
        from app.llm import LLM

        llm = LLM(config_name=llm_config)
        before_prompt_tokens = int(getattr(llm, "total_input_tokens", 0) or 0)
        before_completion_tokens = int(getattr(llm, "total_completion_tokens", 0) or 0)
        response = await asyncio.wait_for(
            llm.ask(
                messages=[{"role": "user", "content": user}],
                system_msgs=[{"role": "system", "content": system}],
                force_json=True,
                new_model=model_name,
                temperature=0.0,
            ),
            timeout=LLM_CALL_TIMEOUT,
        )
        return {
            "text": str(response or ""),
            "usage": {
                "prompt_tokens": int(getattr(llm, "total_input_tokens", 0) or 0) - before_prompt_tokens,
                "completion_tokens": int(getattr(llm, "total_completion_tokens", 0) or 0) - before_completion_tokens,
            },
        }

    async def ask_text(
        self,
        *,
        system: str,
        user: str,
        llm_config: str,
        model_name: str | None,
    ) -> Dict[str, Any]:
        from app.llm import LLM

        llm = LLM(config_name=llm_config)
        before_prompt_tokens = int(getattr(llm, "total_input_tokens", 0) or 0)
        before_completion_tokens = int(getattr(llm, "total_completion_tokens", 0) or 0)
        response = await asyncio.wait_for(
            llm.ask(
                messages=[{"role": "user", "content": user}],
                system_msgs=[{"role": "system", "content": system}],
                force_json=False,
                new_model=model_name,
                temperature=0.0,
            ),
            timeout=LLM_CALL_TIMEOUT,
        )
        return {
            "text": str(response or ""),
            "usage": {
                "prompt_tokens": int(getattr(llm, "total_input_tokens", 0) or 0) - before_prompt_tokens,
                "completion_tokens": int(getattr(llm, "total_completion_tokens", 0) or 0) - before_completion_tokens,
            },
        }


def _json_role_client(*, llm_config: str, model_name: str | None) -> JsonRoleClient:
    from app.config import config

    cfg = config.llm.get(llm_config, config.llm["default"])
    model = model_name or cfg.model
    if str(model or "").startswith("claude-") or "anthropic" in str(cfg.base_url or "") or "127.0.0.1:4000" in str(cfg.base_url or ""):
        return AnthropicJsonRoleClient()
    return GenericJsonRoleClient()


def _record_maintenance_token_event(
    *,
    role: str,
    llm_config: str,
    model_name: str | None,
    usage: Dict[str, Any] | None,
    metadata: Dict[str, Any] | None,
    duration_ms: int,
    system_chars: int,
    user_chars: int,
) -> None:
    from academic.benchmarks.core.cost_accounting import PricingConfig

    normalized_usage = _normalize_usage_payload(usage)
    cache_input_tokens = int(normalized_usage.get("cache_input_tokens", 0) or 0)
    estimated_cost = PricingConfig().estimate_cost(
        input_tokens=normalized_usage["prompt_tokens"],
        cache_input_tokens=cache_input_tokens,
        output_tokens=normalized_usage["completion_tokens"],
    )
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "role": str(role or "").strip(),
        "phase": str((metadata or {}).get("phase") or "").strip(),
        "benchmark": str((metadata or {}).get("benchmark") or "maintenance").strip(),
        "llm_config": str(llm_config or "").strip(),
        "model_name": model_name,
        "prompt_tokens": normalized_usage["prompt_tokens"],
        "input_tokens": normalized_usage["prompt_tokens"],
        "cache_input_tokens": cache_input_tokens,
        "completion_tokens": normalized_usage["completion_tokens"],
        "output_tokens": normalized_usage["completion_tokens"],
        "total_tokens": normalized_usage["total_tokens"],
        "estimated_cost": estimated_cost,
        "duration_ms": int(duration_ms or 0),
        "system_chars": int(system_chars or 0),
        "user_chars": int(user_chars or 0),
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    _MAINTENANCE_TOKEN_EVENTS.append(event)


def reset_maintenance_token_stats() -> None:
    _MAINTENANCE_TOKEN_EVENTS.clear()


def maintenance_token_event_count() -> int:
    return len(_MAINTENANCE_TOKEN_EVENTS)


def snapshot_maintenance_token_stats(*, start_index: int = 0) -> Dict[str, Any]:
    events = [copy.deepcopy(item) for item in _MAINTENANCE_TOKEN_EVENTS[start_index:]]

    def _empty_row() -> Dict[str, Any]:
        return {
            "n_calls": 0,
            "prompt_tokens": 0,
            "input_tokens": 0,
            "cache_input_tokens": 0,
            "completion_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0.0,
            "duration_ms": 0,
        }

    summary = _empty_row()
    by_role: Dict[str, Dict[str, Any]] = {}
    by_phase: Dict[str, Dict[str, Any]] = {}
    for event in events:
        for row in (summary, by_role.setdefault(event["role"] or "unknown", _empty_row()), by_phase.setdefault(event["phase"] or "unscoped", _empty_row())):
            row["n_calls"] += 1
            row["prompt_tokens"] += int(event.get("prompt_tokens") or 0)
            row["input_tokens"] += int(event.get("input_tokens") or event.get("prompt_tokens") or 0)
            row["cache_input_tokens"] += int(event.get("cache_input_tokens") or 0)
            row["completion_tokens"] += int(event.get("completion_tokens") or 0)
            row["output_tokens"] += int(event.get("output_tokens") or event.get("completion_tokens") or 0)
            row["total_tokens"] += int(event.get("total_tokens") or 0)
            row["estimated_cost"] = round(
                float(row.get("estimated_cost") or 0.0) + float(event.get("estimated_cost") or 0.0),
                8,
            )
            row["duration_ms"] += int(event.get("duration_ms") or 0)
    return {
        "start_index": int(start_index or 0),
        "end_index": len(_MAINTENANCE_TOKEN_EVENTS),
        "summary": summary,
        "by_role": by_role,
        "by_phase": by_phase,
        "events": events,
        "recent_events": events[-20:],
    }


async def _ask_json(
    *,
    system: str,
    user: str,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    role: str = "llm_maintenance",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    from app.config import config

    started = time.monotonic()
    cfg = config.llm.get(llm_config, config.llm["default"])
    model = model_name or cfg.model
    max_attempts = max(1, int(os.environ.get("MAINTENANCE_JSON_MAX_ATTEMPTS", "3") or "3"))
    retry_user = user
    last_error = ""
    last_response = ""
    for attempt in range(1, max_attempts + 1):
        print(
            json.dumps(
                {
                    "progress": "maintenance_llm_start",
                    "role": role,
                    "llm_config": llm_config,
                    "model": model,
                    "user_chars": len(retry_user),
                    "system_chars": len(system),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            response_payload = await asyncio.wait_for(
                _json_role_client(llm_config=llm_config, model_name=model_name).ask_json(
                    system=system,
                    user=retry_user,
                    llm_config=llm_config,
                    model_name=model_name,
                ),
                timeout=LLM_CALL_TIMEOUT,
            )
        except Exception as exc:
            last_error = f"request_error:{type(exc).__name__}:{exc}"
            duration_ms = int((time.monotonic() - started) * 1000)
            print(
                json.dumps(
                    {
                        "progress": "maintenance_llm_error",
                        "role": role,
                        "llm_config": llm_config,
                        "model": model,
                        "duration_ms": duration_ms,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[-1000:],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            _append_llm_audit_log(
                role=role,
                llm_config=llm_config,
                model_name=model_name,
                system=system,
                user=retry_user,
                raw_response="",
                parsed_response=None,
                metadata={
                    **dict(metadata or {}),
                    "request_error": str(exc),
                    "request_error_type": type(exc).__name__,
                    "duration_ms": duration_ms,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
            )
            if attempt >= max_attempts:
                raise
            await asyncio.sleep(min(30.0, 2.0 * attempt))
            continue
        response = str(response_payload.get("text") or "")
        last_response = response
        usage = _normalize_usage_payload(dict(response_payload.get("usage") or {}))
        duration_ms = int((time.monotonic() - started) * 1000)
        _record_maintenance_token_event(
            role=role,
            llm_config=llm_config,
            model_name=model_name,
            usage=usage,
            metadata={**dict(metadata or {}), "attempt": attempt, "max_attempts": max_attempts},
            duration_ms=duration_ms,
            system_chars=len(system),
            user_chars=len(retry_user),
        )
        print(
            json.dumps(
                {
                    "progress": "maintenance_llm_done",
                    "role": role,
                    "duration_ms": duration_ms,
                    "response_chars": len(str(response or "")),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "attempt": attempt,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            data = json.loads(_extract_json_text(response))
        except Exception as exc:
            last_error = f"parse_error:{exc}"
            _append_llm_audit_log(
                role=role,
                llm_config=llm_config,
                model_name=model_name,
                system=system,
                user=retry_user,
                raw_response=response,
                parsed_response=None,
                metadata={**dict(metadata or {}), "parse_error": str(exc), "duration_ms": duration_ms, "usage": usage, "attempt": attempt},
            )
            retry_user = _json_retry_user(user, response=response, error=str(exc))
            continue
        if not isinstance(data, dict):
            last_error = f"type_error:{type(data).__name__}"
            _append_llm_audit_log(
                role=role,
                llm_config=llm_config,
                model_name=model_name,
                system=system,
                user=retry_user,
                raw_response=response,
                parsed_response=data,
                metadata={**dict(metadata or {}), "type_error": type(data).__name__, "duration_ms": duration_ms, "usage": usage, "attempt": attempt},
            )
            retry_user = _json_retry_user(user, response=response, error=f"Expected JSON object, got {type(data).__name__}")
            continue
        _append_llm_audit_log(
            role=role,
            llm_config=llm_config,
            model_name=model_name,
            system=system,
            user=retry_user,
            raw_response=response,
            parsed_response=data,
            metadata={**dict(metadata or {}), "duration_ms": duration_ms, "usage": usage, "attempt": attempt},
        )
        return data
    raise ValueError(f"LLM did not return valid JSON after {max_attempts} attempts: {last_error}\nRaw: {last_response[:1000]}")


async def _ask_text(
    *,
    system: str,
    user: str,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    role: str = "llm_maintenance",
    metadata: Dict[str, Any] | None = None,
) -> str:
    from app.config import config

    started = time.monotonic()
    cfg = config.llm.get(llm_config, config.llm["default"])
    model = model_name or cfg.model
    print(
        json.dumps(
            {
                "progress": "maintenance_llm_start",
                "role": role,
                "llm_config": llm_config,
                "model": model,
                "user_chars": len(user),
                "system_chars": len(system),
                "attempt": 1,
                "max_attempts": 1,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    response_payload = await asyncio.wait_for(
        _json_role_client(llm_config=llm_config, model_name=model_name).ask_text(
            system=system,
            user=user,
            llm_config=llm_config,
            model_name=model_name,
        ),
        timeout=LLM_CALL_TIMEOUT,
    )
    response = str(response_payload.get("text") or "")
    usage = _normalize_usage_payload(dict(response_payload.get("usage") or {}))
    duration_ms = int((time.monotonic() - started) * 1000)
    _record_maintenance_token_event(
        role=role,
        llm_config=llm_config,
        model_name=model_name,
        usage=usage,
        metadata={**dict(metadata or {}), "text_mode": True},
        duration_ms=duration_ms,
        system_chars=len(system),
        user_chars=len(user),
    )
    print(
        json.dumps(
            {
                "progress": "maintenance_llm_done",
                "role": role,
                "duration_ms": duration_ms,
                "response_chars": len(response),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "attempt": 1,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    _append_llm_audit_log(
        role=role,
        llm_config=llm_config,
        model_name=model_name,
        system=system,
        user=user,
        raw_response=response,
        parsed_response=None,
        metadata={**dict(metadata or {}), "duration_ms": duration_ms, "usage": usage, "text_mode": True},
    )
    return response


def _json_retry_user(original_user: str, *, response: str, error: str) -> str:
    return (
        f"{original_user}\n\n"
        "## JSON repair retry\n"
        "Your previous response could not be parsed as a JSON object. Return only one valid JSON object matching the requested schema. "
        "Do not include Markdown fences or explanatory prose.\n"
        f"Parser/type error: {error}\n"
        f"Previous response prefix: {str(response or '')[:1200]}"
    )


def _extract_json_text(response: str) -> str:
    """Return the JSON object text from a model response.

    Claude-compatible models sometimes obey the schema but wrap the payload in
    a Markdown fenced block. The maintenance loop should reject malformed JSON,
    not fail on harmless fencing around an otherwise valid object.
    """

    text = str(response or "").strip()
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            text = match.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return text


async def _ask_anthropic_json(
    *,
    system: str,
    user: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
) -> Dict[str, Any]:
    """Call Anthropic-compatible Messages API and return textual JSON.

    The BFCL executor already has native Anthropic tool-call support. This
    helper keeps extractor/bundle/refiner on the same local proxy instead of
    falling back to the OpenAI chat-completions client.
    """

    try:
        from anthropic import AsyncAnthropic
    except ModuleNotFoundError as exc:
        raise RuntimeError("anthropic package is required for Claude maintenance roles") from exc

    endpoint = str(base_url or "").rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3]
    import httpx

    timeout_s = float(os.environ.get("MAINTENANCE_ANTHROPIC_TIMEOUT", "300") or "300")
    timeout = httpx.Timeout(timeout_s, connect=10.0)
    client = AsyncAnthropic(api_key=api_key, base_url=endpoint or None, timeout=timeout, max_retries=1)
    response = await client.messages.create(
        model=model,
        max_tokens=min(max_tokens, int(os.environ.get("MAINTENANCE_JSON_MAX_TOKENS", "4096"))),
        temperature=0.0,
        system=system,
        messages=[
            {
                "role": "user",
                "content": user + "\n\nReturn only valid JSON. Do not wrap it in Markdown.",
            }
        ],
    )
    parts: List[str] = []
    for block in response.content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    usage_obj = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage_obj, "input_tokens", 0) or 0)
    cache_input_tokens = int(getattr(usage_obj, "cache_read_input_tokens", 0) or 0)
    completion_tokens = int(getattr(usage_obj, "output_tokens", 0) or 0)
    return {
        "text": "\n".join(parts).strip(),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "cache_input_tokens": cache_input_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + cache_input_tokens + completion_tokens,
        },
    }


def _append_llm_audit_log(
    *,
    role: str,
    llm_config: str,
    model_name: str | None,
    system: str,
    user: str,
    raw_response: Any,
    parsed_response: Any,
    metadata: Dict[str, Any] | None = None,
) -> None:
    path_raw = os.getenv("SKILL_MAINTENANCE_AUDIT_LOG", "").strip()
    if not path_raw:
        return
    path = Path(path_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "llm_config": llm_config,
        "model_name": model_name,
        "system": system,
        "user": user,
        "raw_response": raw_response,
        "parsed_response": parsed_response,
        "metadata": dict(metadata or {}),
        "usage": _normalize_usage_payload(dict((metadata or {}).get("usage") or {})),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _coerce_interface_payload(raw: Dict[str, Any] | None, fallback_summary: str = "") -> SkillInterface:
    payload = dict(raw or {})
    return SkillInterface(
        summary=str(payload.get("summary") or fallback_summary),
        usage=str(payload.get("usage") or ""),
        input_contract=_ensure_dict(payload.get("input_contract")),
        output_contract=_ensure_dict(payload.get("output_contract")),
        invocation_contract=_ensure_dict(payload.get("invocation_contract")),
        compatibility_notes=str(payload.get("compatibility_notes") or ""),
    )


def _coerce_bundle_case_payload(raw: Dict[str, Any], fallback_case_id: str) -> SkillBundleCase:
    if isinstance(raw, SkillBundleCase):
        payload = raw.as_dict()
    elif hasattr(raw, "as_dict") and not isinstance(raw, dict):
        payload = dict(raw.as_dict() or {})
    else:
        payload = dict(raw or {})
    return SkillBundleCase(
        case_id=str(payload.get("case_id") or fallback_case_id),
        source=str(payload.get("source") or "llm_distilled"),
        prompt=str(payload.get("prompt") or ""),
        expected=dict(payload.get("expected") or {}),
        context=_ensure_dict(payload.get("context")),
        tags=[str(item) for item in (payload.get("tags") or []) if str(item).strip()],
        polarity=str(payload.get("polarity") or "positive"),
        contrast_protocol=dict(payload.get("contrast_protocol") or {"with_skill": True, "without_skill": True}),
    )


def _bundle_projection(bundle: SkillBundle) -> Dict[str, Any]:
    return {
        "bundle_id": bundle.bundle_id,
        "bundle_version": bundle.bundle_version,
        "contrast_protocol": copy.deepcopy(bundle.contrast_protocol or {}),
        "maintenance_notes": bundle.maintenance_notes,
        "positive_cases": [case.as_dict() for case in bundle.positive_cases],
        "negative_cases": [case.as_dict() for case in bundle.negative_cases],
        "integration_cases": [case.as_dict() for case in bundle.integration_cases],
        "fixtures": copy.deepcopy(bundle.fixtures or {}),
    }


def _bundle_from_projection(payload: Dict[str, Any]) -> SkillBundle:
    data = dict(payload or {})
    bundle = SkillBundle(
        bundle_id=str(data.get("bundle_id") or ""),
        bundle_version=int(data.get("bundle_version") or 1),
        maintenance_notes=str(data.get("maintenance_notes") or ""),
        fixtures=copy.deepcopy(dict(data.get("fixtures") or {})),
        contrast_protocol=copy.deepcopy(
            dict(data.get("contrast_protocol") or {"with_skill": True, "without_skill": True})
        ),
    )
    bundle.positive_cases = [
        _coerce_bundle_case_payload(item, f"{bundle.bundle_id or 'bundle'}:positive:{idx}")
        for idx, item in enumerate(data.get("positive_cases") or [])
    ]
    bundle.negative_cases = [
        _coerce_bundle_case_payload(item, f"{bundle.bundle_id or 'bundle'}:negative:{idx}")
        for idx, item in enumerate(data.get("negative_cases") or [])
    ]
    bundle.integration_cases = [
        _coerce_bundle_case_payload(item, f"{bundle.bundle_id or 'bundle'}:integration:{idx}")
        for idx, item in enumerate(data.get("integration_cases") or [])
    ]
    return bundle


def _artifact_text(artifact: SkillArtifact) -> str:
    return json.dumps(artifact.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _artifact_from_text(text: str) -> SkillArtifact:
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise ValueError("artifact text must decode to a JSON object")
    fields = {k: v for k, v in payload.items() if k in SkillArtifact.__dataclass_fields__}
    artifact = SkillArtifact(**fields)
    artifact.interface = _coerce_interface_payload(payload.get("interface"), fallback_summary=artifact.description)
    artifact.bundle = _bundle_from_projection(dict(payload.get("bundle") or {}))
    artifact.evidence = SkillEvidence(**dict(payload.get("evidence") or {}))
    artifact.lineage = SkillLineage(**dict(payload.get("lineage") or {}))
    artifact.dependency_pins = _coerce_dependency_pins(payload.get("dependency_pins"))
    artifact.dependencies = [
        str(dep).strip()
        for dep in (payload.get("dependencies") or artifact.dependencies or [])
        if str(dep).strip()
    ]
    artifact.history = list(payload.get("history") or artifact.history or [])
    artifact.status = str(payload.get("status") or artifact.status or "active")
    artifact.stale = bool(payload.get("stale", artifact.stale))
    artifact.metadata = dict(payload.get("metadata") or artifact.metadata or {})
    artifact.tags = [str(item).strip() for item in (payload.get("tags") or artifact.tags or []) if str(item).strip()]
    if not artifact.bundle.bundle_id:
        artifact.bundle.bundle_id = f"{artifact.name}.bundle"
    return artifact


def _artifact_projection(artifact: SkillArtifact) -> Dict[str, Any]:
    payload = artifact.as_dict()
    payload.pop("version_id", None)
    payload.pop("version_kind", None)
    payload.pop("dependency_versions", None)
    payload["interface"] = dict(payload.get("interface") or {})
    payload["interface"].pop("summary", None)
    payload["history"] = []
    return payload


def _bundle_text(bundle: SkillBundle) -> str:
    return json.dumps(_bundle_projection(bundle), ensure_ascii=False, indent=2, sort_keys=True)


def _bundle_from_text(text: str) -> SkillBundle:
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise ValueError("bundle text must decode to a JSON object")
    return _bundle_from_projection(payload)


def _trim_bundle_projection(
    bundle: SkillBundle,
    *,
    per_polarity_limit: int,
    total_limit: int,
) -> bool:
    changed = False
    def case_priority(case: SkillBundleCase, index: int) -> tuple[int, float, int, int]:
        ctx = dict(case.context or {})
        credit_event = dict(ctx.get("credit_event") or {})
        source = str(case.source or "")
        confidence = float(credit_event.get("confidence") or 0.0)
        is_credit = 1 if source.startswith("credit_assigner_") else 0
        is_regression = 1 if any(token in source for token in ("regression", "failure", "integration")) else 0
        return (is_credit + is_regression, confidence, index, 1)

    for attr in ("positive_cases", "negative_cases", "integration_cases"):
        cases = list(getattr(bundle, attr) or [])
        if len(cases) <= per_polarity_limit:
            continue
        indexed = list(enumerate(cases))
        indexed.sort(key=lambda item: case_priority(item[1], item[0]), reverse=True)
        setattr(bundle, attr, [case for _idx, case in indexed[:per_polarity_limit]])
        changed = True
    ordered_groups = [
        (
            "positive_cases",
            [
                case for _idx, case in sorted(
                    enumerate(list(bundle.positive_cases or [])),
                    key=lambda item: case_priority(item[1], item[0]),
                    reverse=True,
                )
            ],
        ),
        (
            "negative_cases",
            [
                case for _idx, case in sorted(
                    enumerate(list(bundle.negative_cases or [])),
                    key=lambda item: case_priority(item[1], item[0]),
                    reverse=True,
                )
            ],
        ),
        (
            "integration_cases",
            [
                case for _idx, case in sorted(
                    enumerate(list(bundle.integration_cases or [])),
                    key=lambda item: case_priority(item[1], item[0]),
                    reverse=True,
                )
            ],
        ),
    ]
    total_cases = sum(len(cases) for _, cases in ordered_groups)
    if total_cases > total_limit:
        kept: Dict[str, List[SkillBundleCase]] = {name: [] for name, _ in ordered_groups}
        group_iters = {name: list(cases) for name, cases in ordered_groups}
        while sum(len(items) for items in kept.values()) < total_limit:
            progress = False
            for name, _cases in ordered_groups:
                remaining = group_iters[name]
                if not remaining:
                    continue
                kept[name].append(remaining.pop(0))
                progress = True
                if sum(len(items) for items in kept.values()) >= total_limit:
                    break
            if not progress:
                break
        bundle.positive_cases = kept["positive_cases"]
        bundle.negative_cases = kept["negative_cases"]
        bundle.integration_cases = kept["integration_cases"]
        bundle.fixtures = {
            **dict(bundle.fixtures or {}),
            "bundle_split_count": total_cases - total_limit,
            "bundle_trimmed": True,
        }
        changed = True
    if changed:
        bundle.bundle_version = max(int(bundle.bundle_version or 1), 1) + 1
    return changed


async def _edit_bundle_text(
    *,
    current_bundle: SkillBundle,
    target_bundle: SkillBundle,
) -> SkillBundle:
    current_text = _bundle_text(current_bundle)
    target_text = _bundle_text(target_bundle)
    editor = StrReplaceEditor()
    result = await editor.execute(action="replace", content=current_text, old=current_text, new=target_text)
    if result.error:
        raise ValueError(f"bundle text edit failed: {result.error}")
    edited_text = str(result.output or "")
    edited_bundle = _bundle_from_text(edited_text)
    if _bundle_projection(edited_bundle) != _bundle_projection(target_bundle):
        raise ValueError("bundle text roundtrip validation failed")
    return edited_bundle


async def _edit_artifact_text(
    *,
    current_artifact: SkillArtifact,
    target_artifact: SkillArtifact,
) -> SkillArtifact:
    current_text = _artifact_text(current_artifact)
    target_text = _artifact_text(target_artifact)
    editor = StrReplaceEditor()
    result = await editor.execute(action="replace", content=current_text, old=current_text, new=target_text)
    if result.error:
        raise ValueError(f"artifact text edit failed: {result.error}")
    edited_text = str(result.output or "")
    edited_artifact = _artifact_from_text(edited_text)
    if _artifact_projection(edited_artifact) != _artifact_projection(target_artifact):
        raise ValueError("artifact text roundtrip validation failed")
    return edited_artifact


async def apply_bundle_patch_payload_via_editor(
    artifact: SkillArtifact,
    *,
    patch_payload: Dict[str, Any],
    maintenance_notes: str = "",
) -> SkillBundle:
    target_bundle = apply_bundle_patch_payload(
        artifact,
        patch_payload=patch_payload,
        maintenance_notes=maintenance_notes,
    )
    temp_artifact = copy.deepcopy(artifact)
    temp_artifact.bundle = copy.deepcopy(target_bundle)
    trim_changed = False
    if temp_artifact.bundle.all_cases():
        trim_changed = _trim_bundle_projection(
            temp_artifact.bundle,
            per_polarity_limit=_bundle_case_limit_per_polarity(),
            total_limit=_bundle_max_total_cases(),
        )
    if trim_changed:
        target_bundle = temp_artifact.bundle
    return await _edit_bundle_text(current_bundle=artifact.bundle, target_bundle=target_bundle)


async def apply_bundle_text_via_editor(
    current_bundle: SkillBundle,
    target_bundle: SkillBundle,
) -> SkillBundle:
    return await _edit_bundle_text(current_bundle=current_bundle, target_bundle=target_bundle)


async def apply_refine_payload_via_editor(
    artifact: SkillArtifact,
    payload: Dict[str, Any],
) -> SkillArtifact:
    target = apply_refine_payload(artifact, payload)
    return await _edit_artifact_text(current_artifact=artifact, target_artifact=target)


async def apply_stale_payload_via_editor(
    artifact: SkillArtifact,
    payload: Dict[str, Any],
) -> SkillArtifact:
    target = apply_stale_payload(artifact, payload)
    return await _edit_artifact_text(current_artifact=artifact, target_artifact=target)


def _coerce_dependency_pins(raw: Iterable[Dict[str, Any]] | None) -> List[DependencyPin]:
    pins: List[DependencyPin] = []
    for item in raw or []:
        payload = dict(item or {})
        name = str(payload.get("skill_name") or "").strip()
        if not name:
            continue
        pins.append(
            DependencyPin(
                skill_name=name,
                min_version=payload.get("min_version"),
                pinned_version=payload.get("pinned_version"),
                compatibility_mode=str(payload.get("compatibility_mode") or "floating"),
            )
        )
    return pins


def _artifact_prompt_block(artifact: SkillArtifact) -> Dict[str, Any]:
    return {
        "name": artifact.name,
        "kind": artifact.kind,
        "description": artifact.description,
        "body": artifact.body,
        "version": artifact.version,
        "status": artifact.status,
        "stale": artifact.stale,
        "metadata": {
            key: artifact.metadata.get(key)
            for key in (
                "domains",
                "allowed_tools",
                "intent_keywords",
                "source_task_ids",
                "non_applicability",
                "disabled_reason",
            )
            if artifact.metadata.get(key) not in (None, "", [], {})
        },
        "interface": {
            "summary": artifact.interface.summary,
            "usage": artifact.interface.usage,
            "input_contract": artifact.interface.input_contract,
            "output_contract": artifact.interface.output_contract,
            "invocation_contract": artifact.interface.invocation_contract,
            "compatibility_notes": artifact.interface.compatibility_notes,
        },
        "bundle": _bundle_projection(artifact.bundle),
        "dependencies": list(artifact.dependencies or []),
        "dependency_pins": [item.as_dict() for item in artifact.dependency_pins],
    }


def _result_prompt_block(
    result: Dict[str, Any],
    *,
    focus_artifact: SkillArtifact | None = None,
) -> Dict[str, Any]:
    metrics = dict(result.get("metrics") or {})
    trace = dict(result.get("trace") or {})
    task = dict(result.get("task") or {})

    if focus_artifact is None:
        return {
            "task_id": result.get("task_id"),
            "task": {
                "benchmark": task.get("benchmark"),
                "task_id": task.get("task_id"),
                "question": task.get("question"),
                "expected": task.get("expected"),
                "metadata": task.get("metadata"),
            },
            "success": result.get("success"),
            "score": result.get("score"),
            "metrics": {
                "official_valid": metrics.get("official_valid"),
                "official_error_type": metrics.get("official_error_type"),
                "call_f1": metrics.get("call_f1"),
                "turn_scores": metrics.get("turn_scores"),
                "call_errors": metrics.get("call_errors"),
                "retrieved_skills": metrics.get("retrieved_skills"),
                "prompt_injected_skills": metrics.get("prompt_injected_skills"),
                "tool_injected_skills": metrics.get("tool_injected_skills"),
                "used_skills": metrics.get("used_skills"),
                "n_model_steps": metrics.get("n_model_steps"),
                "total_tokens": metrics.get("total_tokens"),
            },
            "trace": {
                "turns": _focused_turn_summaries(trace.get("turns") or []),
                "tool_calls": trace.get("tool_calls"),
                "skill_events": trace.get("skill_events"),
            },
        }

    allowed_tools = {
        str(item).strip()
        for item in (focus_artifact.metadata.get("allowed_tools") or [])
        if str(item).strip()
    }
    expected = list(task.get("expected") or [])
    turns = list(trace.get("turns") or [])
    call_errors = list(metrics.get("call_errors") or [])

    error_turn_indices = {
        e.get("turn_index")
        for e in call_errors
        if isinstance(e.get("turn_index"), int)
    }

    def _turn_relevant(turn_index: int) -> bool:
        if turn_index in error_turn_indices:
            return True
        if not allowed_tools:
            return True
        if 0 <= turn_index < len(expected):
            for raw in expected[turn_index] or []:
                name = str(raw).split("(", 1)[0].strip()
                if name in allowed_tools:
                    return True
        if 0 <= turn_index < len(turns):
            for call in (turns[turn_index].get("tool_calls") or []):
                if str(call.get("name") or "") in allowed_tools:
                    return True
        return False

    selected_turns: List[Dict[str, Any]] = []
    selected_expected: List[Any] = []
    for idx, turn in enumerate(turns):
        ti = turn.get("turn_index", idx)
        if not _turn_relevant(ti):
            continue
        compact_turn = {
            "turn_index": ti,
            "user_messages": turn.get("user_messages"),
            "tool_calls": turn.get("tool_calls"),
        }
        if turn.get("early_stop_reason"):
            compact_turn["early_stop_reason"] = turn["early_stop_reason"]
        selected_turns.append(compact_turn)
        if 0 <= ti < len(expected):
            selected_expected.append({"turn_index": ti, "calls": expected[ti]})

    selected_errors = [
        e for e in call_errors if e.get("turn_index") in error_turn_indices
    ] or call_errors[:5]

    task_metadata = task.get("metadata") or {}
    return {
        "task_id": result.get("task_id"),
        "task": {
            "benchmark": task.get("benchmark"),
            "task_id": task.get("task_id"),
            "expected_focused": selected_expected,
            "metadata": {
                k: task_metadata.get(k)
                for k in ("involved_classes", "domains")
                if k in task_metadata
            },
        },
        "success": result.get("success"),
        "score": result.get("score"),
        "metrics": {
            "official_valid": metrics.get("official_valid"),
            "official_error_type": metrics.get("official_error_type"),
            "call_f1": metrics.get("call_f1"),
            "call_errors": selected_errors,
            "n_model_steps": metrics.get("n_model_steps"),
            "total_tokens": metrics.get("total_tokens"),
        },
        "trace": {"focused_turns": selected_turns},
    }


def _focused_turn_summaries(turns: Sequence[Any], *, limit: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, turn in enumerate(list(turns or [])[:limit]):
        if not isinstance(turn, dict):
            continue
        out.append(
            {
                "turn_index": turn.get("turn_index", idx),
                "user_messages": turn.get("user_messages"),
                "tool_calls": turn.get("tool_calls"),
                "early_stop_reason": turn.get("early_stop_reason"),
            }
        )
    if len(turns or []) > limit:
        out.append({"truncated_count": len(turns or []) - limit})
    return out


def _error_focus_hints(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    for item in results:
        task = dict(item.get("task") or {})
        expected = list(task.get("expected") or [])
        metrics = dict(item.get("metrics") or {})
        for error in (metrics.get("call_errors") or []):
            err = dict(error or {})
            error_type = str(err.get("type") or "").strip()
            tool_name = str(
                err.get("name")
                or err.get("actual_name")
                or err.get("expected_name")
                or ""
            ).strip()
            hint: Dict[str, Any] = {
                "task_id": item.get("task_id"),
                "error_type": error_type,
                "tool_name": tool_name,
                "turn_index": err.get("turn_index"),
                "expected_turn_calls": [],
                "focus_rule_hint": "",
                "raw_error": err,
            }
            turn_index = err.get("turn_index")
            if isinstance(turn_index, int) and 0 <= turn_index < len(expected):
                hint["expected_turn_calls"] = expected[turn_index]
            expected_names: List[str] = []
            for raw_call in hint["expected_turn_calls"]:
                raw_text = str(raw_call or "")
                name = raw_text.split("(", 1)[0].strip()
                if name and name not in expected_names:
                    expected_names.append(name)
            if expected_names:
                hint["expected_tool_names"] = expected_names
            if error_type == "argument_mismatch":
                missing = dict(err.get("missing") or {})
                unexpected = dict(err.get("unexpected") or {})
                wrong = dict(err.get("wrong") or {})
                focus_parts = [f"Use exact contract for `{tool_name}`."]
                if missing:
                    focus_parts.append(f"Required fields/positions: {missing}.")
                if unexpected:
                    focus_parts.append(f"Do not invent fields: {unexpected}.")
                if wrong:
                    focus_parts.append(f"Correct wrong argument values: {wrong}.")
                hint["focus_rule_hint"] = " ".join(focus_parts)
            elif error_type == "extra_call":
                hint["focus_rule_hint"] = (
                    f"Avoid the extra `{tool_name}` call at this local step. "
                    "Extract a stop-condition or minimal-call rule, not a broad workflow summary. "
                    f"If expected tool names are known here, anchor the rule to those tools: {hint.get('expected_tool_names', [])}."
                )
            elif error_type:
                hint["focus_rule_hint"] = (
                    f"Extract the narrow local rule that would prevent `{error_type}` for `{tool_name}`."
                )
            hints.append(hint)
    return hints


def _skill_credit_projection(
    artifact: SkillArtifact,
    *,
    task_domains: List[str],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    prompt_injected = set(metrics.get("prompt_injected_skills") or [])
    tool_injected = set(metrics.get("tool_injected_skills") or [])
    used = set(metrics.get("used_skills") or []) | set(metrics.get("called_skill_tools") or [])
    source_domains = [str(item).strip() for item in (artifact.metadata.get("domains") or []) if str(item).strip()]
    allowed_tools = [str(item).strip() for item in (artifact.metadata.get("allowed_tools") or []) if str(item).strip()]
    skill_type = "function_like" if artifact.injection_type() == "functional" else "knowledge_like"
    return {
        "skill_name": artifact.name,
        "version": artifact.version,
        "version_id": artifact.version_id(),
        "kind": artifact.kind,
        "skill_type": skill_type,
        "injection_type": artifact.injection_type(),
        "description": artifact.description,
        "body": artifact.body,
        "interface": {
            "summary": artifact.interface.summary,
            "usage": artifact.interface.usage,
            "input_contract": artifact.interface.input_contract,
            "output_contract": artifact.interface.output_contract,
            "invocation_contract": artifact.interface.invocation_contract,
            "compatibility_notes": artifact.interface.compatibility_notes,
        },
        "source_domains": source_domains,
        "task_domain_overlap": sorted(set(source_domains) & set(task_domains)),
        "allowed_tools": allowed_tools,
        "dependencies": list(artifact.dependencies or []),
        "source_task_ids": list(artifact.metadata.get("source_task_ids") or []),
        "exposure": {
            "retrieved": artifact.name in set(metrics.get("retrieved_skills") or []),
            "prompt_injected": artifact.name in prompt_injected,
            "tool_injected": artifact.name in tool_injected,
            "used": artifact.name in used,
        },
    }


def _credit_assignment_prompt_block(
    *,
    detail: Dict[str, Any],
    candidate_artifacts: List[SkillArtifact],
) -> Dict[str, Any]:
    run = dict((detail.get("runs") or [{}])[0] or {})
    task = dict(detail.get("task") or {})
    metrics = dict(run.get("metrics") or {})
    trace = dict(run.get("trace") or {})
    task_domains = [
        str(item).strip()
        for item in ((task.get("metadata") or {}).get("involved_classes") or [])
        if str(item).strip()
    ]
    call_errors = list(metrics.get("call_errors") or [])
    candidate_names = {artifact.name for artifact in candidate_artifacts}
    selected_turns: List[Dict[str, Any]] = []
    turns = list(trace.get("turns") or [])
    expected = list(task.get("expected") or [])
    error_turn_indices = {
        item.get("turn_index")
        for item in call_errors
        if isinstance(item.get("turn_index"), int)
    }
    allowed_tools = {
        tool
        for artifact in candidate_artifacts
        for tool in (artifact.metadata.get("allowed_tools") or [])
        if str(tool).strip()
    }
    for idx, turn in enumerate(turns):
        ti = turn.get("turn_index", idx)
        include = ti in error_turn_indices
        if not include and 0 <= ti < len(expected):
            for raw_call in expected[ti] or []:
                if str(raw_call).split("(", 1)[0].strip() in allowed_tools:
                    include = True
                    break
        if not include:
            for call in (turn.get("tool_calls") or []):
                if str(call.get("name") or "").strip() in allowed_tools:
                    include = True
                    break
        if not include and idx == 0:
            include = True
        if include:
            selected_turns.append(
                {
                    "turn_index": ti,
                    "user_messages": turn.get("user_messages"),
                    "expected_calls": expected[ti] if 0 <= ti < len(expected) else [],
                    "tool_calls": turn.get("tool_calls"),
                }
            )
    return {
        "task": {
            "benchmark": task.get("benchmark"),
            "task_id": task.get("task_id"),
            "question_preview": _trim_text(_json_block(task.get("question") or []), limit=1400),
            "metadata": {
                "involved_classes": task_domains,
            },
        },
        "result": {
            "success": run.get("success"),
            "score": run.get("score"),
            "metrics": {
                "official_valid": metrics.get("official_valid"),
                "official_error_type": metrics.get("official_error_type"),
                "call_f1": metrics.get("call_f1"),
                "n_model_steps": metrics.get("n_model_steps"),
                "total_tokens": metrics.get("total_tokens"),
                "call_errors": call_errors,
                "prompt_injected_skills": metrics.get("prompt_injected_skills"),
                "tool_injected_skills": metrics.get("tool_injected_skills"),
                "used_skills": metrics.get("used_skills"),
            },
        },
        "trace": {
            "focused_turns": selected_turns,
        },
        "candidate_skills": [
            _skill_credit_projection(artifact, task_domains=task_domains, metrics=metrics)
            for artifact in candidate_artifacts
            if artifact.name in candidate_names
        ],
    }


def _normalize_credit_bundle_suggestions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    for raw in list(data.get("bundle_case_suggestions") or []):
        if isinstance(raw, dict):
            suggestions.append(raw)
    for judgment in list(data.get("skill_judgments") or []):
        if not isinstance(judgment, dict):
            continue
        skill_name = str(judgment.get("skill_name") or "").strip()
        for raw in list(judgment.get("bundle_case_suggestions") or []):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("skill_name", skill_name)
            suggestions.append(row)
    normalized: List[Dict[str, Any]] = []
    for raw in suggestions:
        skill_name = str(raw.get("skill_name") or "").strip()
        if not skill_name:
            continue
        polarity = str(raw.get("polarity") or "").strip().lower()
        if polarity not in {"positive", "negative", "integration"}:
            continue
        normalized.append(
            {
                "skill_name": skill_name,
                "polarity": polarity,
                "reason": str(raw.get("reason") or ""),
                "source_task_id": str(raw.get("source_task_id") or ""),
                "focus_turn_indices": [
                    int(item)
                    for item in (raw.get("focus_turn_indices") or [])
                    if isinstance(item, int) or str(item).strip().isdigit()
                ],
                "required_context_turn_indices": [
                    int(item)
                    for item in (raw.get("required_context_turn_indices") or [])
                    if isinstance(item, int) or str(item).strip().isdigit()
                ],
                "state_requirements": copy.deepcopy(dict(raw.get("state_requirements") or {})),
                "expected_contract": str(raw.get("expected_contract") or ""),
                "task_fragment_policy": str(raw.get("task_fragment_policy") or "reuse_official_fragment").strip() or "reuse_official_fragment",
            }
        )
    return normalized


def _normalize_credit_maintenance_actions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for raw in list(data.get("maintenance_actions") or []):
        if isinstance(raw, dict):
            actions.append(raw)
    for judgment in list(data.get("skill_judgments") or []):
        if not isinstance(judgment, dict):
            continue
        skill_name = str(judgment.get("skill_name") or "").strip()
        for raw in list(judgment.get("maintenance_actions") or []):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("skill_name", skill_name)
            actions.append(row)
    normalized: List[Dict[str, Any]] = []
    for raw in actions:
        skill_name = str(raw.get("skill_name") or "").strip()
        action = str(raw.get("action") or "").strip().lower()
        if not skill_name or not action:
            continue
        normalized.append(
            {
                "skill_name": skill_name,
                "action": action,
                "reason": str(raw.get("reason") or ""),
                "target_scope": str(raw.get("target_scope") or ""),
            }
        )
    return normalized


async def assign_skill_credit_llm(
    *,
    detail: Dict[str, Any],
    candidate_artifacts: List[SkillArtifact],
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    prompt_payload = _credit_assignment_prompt_block(
        detail=detail,
        candidate_artifacts=candidate_artifacts,
    )
    user = (
        "## Completed Task Trace\n"
        f"{_role_json_block(prompt_payload)}\n"
    )
    data = await _ask_json(
        system=CREDIT_ASSIGNMENT_SYSTEM,
        user=_trim_text(user, limit=18000),
        llm_config=llm_config,
        model_name=model_name,
        role="credit_assigner",
        metadata={
            "task_id": str(detail.get("task_id") or ""),
            "n_candidate_skills": len(candidate_artifacts),
            **dict(audit_context or {}),
        },
    )
    judgments = []
    for item in list(data.get("skill_judgments") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("skill_name") or "").strip()
        if not name:
            continue
        judgments.append(
            {
                "skill_name": name,
                "judgment": str(item.get("judgment") or "uncertain").strip().lower() or "uncertain",
                "effect_type": str(item.get("effect_type") or "unknown").strip().lower() or "unknown",
                "confidence": float(item.get("confidence") or 0.0),
                "reason": str(item.get("reason") or ""),
                "maintenance_actions": _normalize_credit_maintenance_actions({"skill_judgments": [item]}),
                "bundle_case_suggestions": _normalize_credit_bundle_suggestions({"skill_judgments": [item]}),
                "refine_required": bool(item.get("refine_required")),
                "filter_candidate": bool(item.get("filter_candidate")),
                "evidence_strength": str(item.get("evidence_strength") or "weak").strip().lower() or "weak",
                "attribution_scope": str(item.get("attribution_scope") or "none").strip().lower() or "none",
                "evidence": copy.deepcopy(dict(item.get("evidence") or {})),
            }
        )
    return {
        "task_summary": copy.deepcopy(dict(data.get("task_summary") or {})),
        "skill_judgments": judgments,
        "input_projection": prompt_payload,
    }


async def extract_skill_artifacts_from_results_llm(
    results: List[Dict[str, Any]],
    *,
    tool_schemas: Iterable[Dict[str, Any]] | None = None,
    existing_artifacts: Iterable[SkillArtifact] | None = None,
    extractor_rules: Iterable[Dict[str, Any]] | None = None,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> List[SkillArtifact]:
    existing_summary = [
        {
            "name": item.name,
            "description": item.description,
            "kind": item.kind,
            "dependencies": list(item.dependencies or []),
            "interface": item.interface.as_dict(),
        }
        for item in (existing_artifacts or [])
    ]
    tool_summary = []
    for tool in tool_schemas or []:
        fn = dict(tool.get("function") or {})
        tool_summary.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters"),
                "class": fn.get("x_bfcl_class"),
            }
        )
    error_summary = []
    for item in results:
        metrics = dict(item.get("metrics") or {})
        for error in (metrics.get("call_errors") or []):
            error_summary.append(
                {
                    "task_id": item.get("task_id"),
                    "error": error,
                }
            )
    user = (
        "## Existing Artifacts\n"
        f"{_role_json_block(existing_summary)}\n\n"
        "## Tool Schemas\n"
        f"{_role_json_block(tool_summary[:80])}\n\n"
        "## Call Error Evidence\n"
        f"{_role_json_block(error_summary[:40])}\n\n"
        "## Error Focus Hints\n"
        f"{_role_json_block(_error_focus_hints(results)[:40])}\n\n"
        "## Benchmark Results\n"
        f"{_role_json_block([_result_prompt_block(item) for item in results])}\n"
    )
    data = await _ask_json(
        system=EXTRACT_SYSTEM + _extractor_rule_suffix(extractor_rules),
        user=_trim_text(user),
        llm_config=llm_config,
        model_name=model_name,
        role="extractor",
        metadata={
            "n_results": len(results),
            "n_existing_artifacts": len(list(existing_artifacts or [])),
            "n_extractor_rules": len(_normalize_feedback_rules(extractor_rules)),
            **dict(audit_context or {}),
        },
    )
    artifacts: List[SkillArtifact] = []
    for idx, raw in enumerate(data.get("artifacts") or []):
        payload = dict(raw or {})
        name = str(payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not name or not description or not body:
            continue
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("source", "llm_trace_extraction")
        metadata.setdefault("version_kind", str(payload.get("version_kind") or "seed"))
        artifact = SkillArtifact(
            name=name,
            kind=str(payload.get("kind") or "workflow_guardrail_card"),
            description=description,
            body=body,
            metadata=metadata,
            tags=[str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()],
            interface=_coerce_interface_payload(payload.get("interface"), fallback_summary=description),
            lineage=SkillLineage(version_kind=str(payload.get("version_kind") or "seed")),
            dependency_pins=_coerce_dependency_pins(payload.get("dependency_pins")),
            dependencies=[str(item).strip() for item in (payload.get("dependencies") or []) if str(item).strip()],
        )
        if not artifact.bundle.bundle_id:
            artifact.bundle.bundle_id = f"{artifact.name}.bundle"
        artifacts.append(artifact)
    return artifacts


async def update_role_rules_from_feedback_llm(
    *,
    role_name: str,
    current_rules: Iterable[Dict[str, Any]] | None,
    feedback_rows: List[Dict[str, Any]],
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    max_rules: int = 5,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_role = str(role_name or "extractor").strip().lower() or "extractor"
    if normalized_role not in ROLE_RULE_UPDATE_SYSTEMS:
        normalized_role = "extractor"
    prefix = f"{normalized_role}_rule"
    normalized_current = _normalize_feedback_rules(current_rules, max_rules=max_rules, prefix=prefix)
    if not feedback_rows:
        return {
            "summary": "no_feedback_rows",
            "rules": normalized_current,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    role_label = normalized_role.replace("_", " ").title()
    user = (
        f"## Current {role_label} Rules\n"
        f"{_role_json_block(normalized_current or [])}\n\n"
        "## Runtime Feedback Evidence\n"
        f"{_role_json_block(feedback_rows)}\n"
    )
    system = ROLE_RULE_UPDATE_SYSTEMS[normalized_role].replace("{max_rules}", str(max_rules))
    metadata = {
        "feedback_role": normalized_role,
        "n_current_rules": len(normalized_current),
        "n_feedback_rows": len(feedback_rows),
        **dict(audit_context or {}),
    }
    if _env_bool("MAINTENANCE_ROLE_RULE_USE_JSON", False):
        data = await _ask_json(
            system=system,
            user=_trim_text(user, limit=12000),
            llm_config=llm_config,
            model_name=model_name,
            role=f"{normalized_role}_feedback",
            metadata=metadata,
        )
    else:
        response_text = await _ask_text(
            system=system,
            user=_trim_text(user, limit=12000),
            llm_config=llm_config,
            model_name=model_name,
            role=f"{normalized_role}_feedback",
            metadata=metadata,
        )
        data = _parse_role_rule_update_text(response_text, max_rules=max_rules, prefix=prefix)
    updated_rules = _normalize_feedback_rules(data.get("rules"), max_rules=max_rules, prefix=prefix)
    if not updated_rules:
        updated_rules = normalized_current
    return {
        "analysis": str(data.get("analysis") or ""),
        "summary": str(data.get("summary") or ""),
        "rules": updated_rules,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def update_extractor_rules_from_feedback_llm(
    *,
    current_rules: Iterable[Dict[str, Any]] | None,
    feedback_rows: List[Dict[str, Any]],
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    max_rules: int = 5,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return await update_role_rules_from_feedback_llm(
        role_name="extractor",
        current_rules=current_rules,
        feedback_rows=feedback_rows,
        llm_config=llm_config,
        model_name=model_name,
        max_rules=max_rules,
        audit_context=audit_context,
    )


async def propose_group_refiner_actions_llm(
    *,
    group_feedback_rows: List[Dict[str, Any]],
    current_actions: List[Dict[str, Any]] | None = None,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not group_feedback_rows:
        return {"analysis": "no_group_feedback_rows", "actions": []}
    user = (
        "## Current Deterministic Action Proposal\n"
        f"{_role_json_block(list(current_actions or []))}\n\n"
        "## Candidate Group Evidence\n"
        f"{_role_json_block(group_feedback_rows)}\n"
    )
    data = await _ask_json(
        system=GROUP_REFINER_ACTION_SYSTEM,
        user=_trim_text(user, limit=18000),
        llm_config=llm_config,
        model_name=model_name,
        role="group_refiner",
        metadata={
            "n_group_feedback_rows": len(group_feedback_rows),
            "n_current_actions": len(current_actions or []),
            **dict(audit_context or {}),
        },
    )
    actions: List[Dict[str, Any]] = []
    for item in data.get("actions") or []:
        if not isinstance(item, dict):
            continue
        skill_name = str(item.get("skill_name") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        if not skill_name or action not in {"keep", "refine", "archive", "backup"}:
            continue
        actions.append(
            {
                "candidate_group_id": str(item.get("candidate_group_id") or "").strip(),
                "skill_name": skill_name,
                "action": action,
                "reason": str(item.get("reason") or "").strip(),
                "patch_intent": str(item.get("patch_intent") or "").strip(),
            }
        )
    return {
        "analysis": str(data.get("analysis") or ""),
        "actions": actions,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def distill_skill_bundle_llm(
    artifact: SkillArtifact,
    *,
    source_results: List[Dict[str, Any]],
    replay_results: List[Dict[str, Any]] | None = None,
    integration_failures: List[Dict[str, Any]] | None = None,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> SkillBundle:
    user = (
        "## Target Skill Artifact\n"
        f"{_role_json_block(_artifact_prompt_block(artifact))}\n\n"
        "## Source Results (focused to turns relevant to this skill)\n"
        f"{_role_json_block([_result_prompt_block(item, focus_artifact=artifact) for item in source_results])}\n\n"
        "## Replay Results (focused)\n"
        f"{_role_json_block([_result_prompt_block(item, focus_artifact=artifact) for item in (replay_results or [])])}\n\n"
        "## Integration Failures\n"
        f"{_role_json_block(list(integration_failures or []))}\n"
    )
    data = await _ask_json(
        system=BUNDLE_SYSTEM,
        user=_trim_text(user),
        llm_config=llm_config,
        model_name=model_name,
        role="bundle_builder",
        metadata={
            "artifact_name": artifact.name,
            "n_source_results": len(source_results),
            "n_replay_results": len(replay_results or []),
            "n_integration_failures": len(integration_failures or []),
            **dict(audit_context or {}),
        },
    )
    bundle = copy.deepcopy(artifact.bundle)
    bundle.maintenance_notes = str(data.get("maintenance_notes") or bundle.maintenance_notes or "")
    bundle.positive_cases = [
        _coerce_bundle_case_payload(item, f"{artifact.name}:positive:{idx}")
        for idx, item in enumerate(data.get("positive_cases") or [])
    ]
    bundle.negative_cases = [
        _coerce_bundle_case_payload(item, f"{artifact.name}:negative:{idx}")
        for idx, item in enumerate(data.get("negative_cases") or [])
    ]
    bundle.integration_cases = [
        _coerce_bundle_case_payload(item, f"{artifact.name}:integration:{idx}")
        for idx, item in enumerate(data.get("integration_cases") or [])
    ]
    if not bundle.bundle_id:
        bundle.bundle_id = f"{artifact.name}.bundle"
    _trim_bundle_projection(
        bundle,
        per_polarity_limit=_bundle_case_limit_per_polarity(),
        total_limit=_bundle_max_total_cases(),
    )
    return bundle


def apply_bundle_patch_payload(
    artifact: SkillArtifact,
    *,
    patch_payload: Dict[str, Any],
    maintenance_notes: str = "",
) -> SkillBundle:
    bundle = copy.deepcopy(artifact.bundle)
    buckets: Dict[str, Dict[str, SkillBundleCase]] = {
        "positive_cases": {case.case_id: copy.deepcopy(case) for case in bundle.positive_cases},
        "negative_cases": {case.case_id: copy.deepcopy(case) for case in bundle.negative_cases},
        "integration_cases": {case.case_id: copy.deepcopy(case) for case in bundle.integration_cases},
    }
    drop_ids = {
        str(item).strip()
        for item in (patch_payload.get("drop_case_ids") or [])
        if str(item).strip()
    }
    for case_id in drop_ids:
        for bucket_cases in buckets.values():
            bucket_cases.pop(case_id, None)
    for raw in (patch_payload.get("replace_cases") or []):
        payload = dict(raw or {})
        case_id = str(payload.get("case_id") or "").strip()
        bucket = str(payload.get("bucket") or "").strip()
        if not case_id:
            continue
        target_bucket = bucket if bucket in buckets else None
        if target_bucket is None:
            for bucket_name, bucket_cases in buckets.items():
                if case_id in bucket_cases:
                    target_bucket = bucket_name
                    break
        if target_bucket is None:
            target_bucket = "integration_cases"
        for bucket_name, bucket_cases in buckets.items():
            if bucket_name != target_bucket:
                bucket_cases.pop(case_id, None)
        buckets[target_bucket][case_id] = _coerce_bundle_case_payload(payload, case_id)
    add_cases = dict(patch_payload.get("add_cases") or {})
    for polarity_key in ("positive_cases", "negative_cases", "integration_cases"):
        for idx, raw in enumerate(add_cases.get(polarity_key) or []):
            payload = dict(raw or {})
            fallback_case_id = str(payload.get("case_id") or f"{artifact.name}:{polarity_key}:{idx}")
            case = _coerce_bundle_case_payload(payload, fallback_case_id)
            buckets[polarity_key][case.case_id] = case

    bundle.positive_cases = list(buckets["positive_cases"].values())
    bundle.negative_cases = list(buckets["negative_cases"].values())
    bundle.integration_cases = list(buckets["integration_cases"].values())
    if maintenance_notes.strip():
        bundle.maintenance_notes = maintenance_notes.strip()
    if not bundle.bundle_id:
        bundle.bundle_id = f"{artifact.name}.bundle"
    _trim_bundle_projection(
        bundle,
        per_polarity_limit=_bundle_case_limit_per_polarity(),
        total_limit=_bundle_max_total_cases(),
    )
    return bundle


async def maintain_skill_bundle_llm(
    artifact: SkillArtifact,
    *,
    source_results: List[Dict[str, Any]] | None = None,
    replay_results: List[Dict[str, Any]] | None = None,
    integration_failures: List[Dict[str, Any]] | None = None,
    credit_cases: List[Dict[str, Any]] | None = None,
    contract_validation_failures: List[Dict[str, Any]] | None = None,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    user = (
        "## Target Skill Artifact\n"
        f"{_role_json_block(_artifact_prompt_block(artifact))}\n\n"
        "## Current Bundle\n"
        f"{_role_json_block(_bundle_projection(artifact.bundle))}\n\n"
        "## Credit-Assigned Bundle Cases\n"
        f"{_role_json_block(list(credit_cases or []))}\n\n"
        "## Integration Failures\n"
        f"{_role_json_block(list(integration_failures or []))}\n\n"
        "## Contract Validation Failures\n"
        f"{_role_json_block(list(contract_validation_failures or []))}\n"
    )
    data = await _ask_json(
        system=BUNDLE_MAINTAIN_SYSTEM,
        user=_trim_text(user, limit=12000),
        llm_config=llm_config,
        model_name=model_name,
        role="bundle_maintainer",
        metadata={
            "artifact_name": artifact.name,
            "skill_version": artifact.version,
            "existing_bundle_cases": len(artifact.bundle.all_cases()),
            "n_integration_failures": len(integration_failures or []),
            "n_credit_cases": len(credit_cases or []),
            "n_contract_validation_failures": len(contract_validation_failures or []),
            **dict(audit_context or {}),
        },
    )
    action = str(data.get("action") or "rebuild").strip().lower() or "rebuild"
    normalized_action = action if action in {"keep", "patch", "rebuild"} else "rebuild"
    return {
        "action": normalized_action,
        "reason": str(data.get("reason") or ""),
        "maintenance_notes": str(data.get("maintenance_notes") or ""),
        "patch": copy.deepcopy(dict(data.get("patch") or {})),
        "raw": copy.deepcopy(data),
    }


async def refine_skill_artifact_llm(
    artifact: SkillArtifact,
    *,
    test_result: Dict[str, Any],
    integration_failures: List[Dict[str, Any]] | None = None,
    refinement_history: List[Dict[str, Any]] | None = None,
    dependency_summaries: List[Dict[str, Any]] | None = None,
    credit_context: List[Dict[str, Any]] | None = None,
    refiner_rules: Iterable[Dict[str, Any]] | None = None,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    user = (
        "## Current Artifact\n"
        f"{_role_json_block(_artifact_prompt_block(artifact))}\n\n"
        "## Test Result\n"
        f"{_role_json_block(test_result)}\n\n"
        "## Integration Failures\n"
        f"{_role_json_block(list(integration_failures or []))}\n\n"
        "## Refinement History\n"
        f"{_role_json_block(list(refinement_history or []))}\n\n"
        "## Neighbor Dependency Summaries\n"
        f"{_role_json_block(list(dependency_summaries or []))}\n\n"
        "## Recent Credit Assignment Context\n"
        f"{_role_json_block(list(credit_context or []))}\n"
    )
    return await _ask_json(
        system=REFINE_SYSTEM + _refiner_rule_suffix(refiner_rules),
        user=_trim_text(user),
        llm_config=llm_config,
        model_name=model_name,
        role="refiner",
        metadata={
            "artifact_name": artifact.name,
            "skill_version": artifact.version,
            "n_refiner_rules": len(_normalize_feedback_rules(refiner_rules, prefix="refiner_rule")),
            "n_integration_failures": len(integration_failures or []),
            "n_refinement_history": len(refinement_history or []),
            **dict(audit_context or {}),
        },
    )


async def resolve_stale_skill_llm(
    artifact: SkillArtifact,
    *,
    upstream_context: Dict[str, Any],
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    user = (
        "## Stale Downstream Artifact\n"
        f"{_role_json_block(_artifact_prompt_block(artifact))}\n\n"
        "## Upstream Update Context\n"
        f"{_role_json_block(upstream_context)}\n"
    )
    return await _ask_json(
        system=STALE_SYSTEM,
        user=_trim_text(user),
        llm_config=llm_config,
        model_name=model_name,
        role="stale_resolver",
        metadata={
            "artifact_name": artifact.name,
            "skill_version": artifact.version,
            **dict(audit_context or {}),
        },
    )


def apply_refine_payload(
    artifact: SkillArtifact,
    payload: Dict[str, Any],
) -> SkillArtifact:
    decision = dict(payload.get("decision") or {})
    artifact_payload = dict(payload.get("artifact") or {})
    bundle_payload = dict(payload.get("bundle") or {})
    updated = copy.deepcopy(artifact)
    if artifact_payload:
        updated.kind = str(_first_nonempty(artifact_payload, "kind") or updated.kind)
        updated.description = str(_first_nonempty(artifact_payload, "description") or updated.description)
        updated.body = str(_first_nonempty(artifact_payload, "body") or updated.body)
        updated.metadata = {**updated.metadata, **dict(artifact_payload.get("metadata") or {})}
        updated.dependencies = [
            str(item).strip()
            for item in (artifact_payload.get("dependencies") or updated.dependencies or [])
            if str(item).strip()
        ]
        updated.interface = _coerce_interface_payload(
            artifact_payload.get("interface"),
            fallback_summary=updated.description,
        )
    if bundle_payload:
        bundle = copy.deepcopy(updated.bundle)
        bundle.maintenance_notes = str(bundle_payload.get("maintenance_notes") or bundle.maintenance_notes or "")
        if "positive_cases" in bundle_payload:
            bundle.positive_cases = [
                _coerce_bundle_case_payload(item, f"{updated.name}:positive:{idx}")
                for idx, item in enumerate(bundle_payload.get("positive_cases") or [])
            ]
        if "negative_cases" in bundle_payload:
            bundle.negative_cases = [
                _coerce_bundle_case_payload(item, f"{updated.name}:negative:{idx}")
                for idx, item in enumerate(bundle_payload.get("negative_cases") or [])
            ]
        if "integration_cases" in bundle_payload:
            bundle.integration_cases = [
                _coerce_bundle_case_payload(item, f"{updated.name}:integration:{idx}")
                for idx, item in enumerate(bundle_payload.get("integration_cases") or [])
            ]
        if not bundle.bundle_id:
            bundle.bundle_id = f"{updated.name}.bundle"
        updated.bundle = bundle
    version_kind = str(decision.get("version_kind") or updated.metadata.get("version_kind") or "minor")
    updated.metadata["version_kind"] = version_kind
    updated.lineage.version_kind = version_kind
    updated.lineage.migration_reason = str(decision.get("migration_reason") or updated.lineage.migration_reason or "")
    if decision.get("reason"):
        updated.metadata["last_refine_reason"] = str(decision["reason"])
    pins = _coerce_dependency_pins(decision.get("pinned_dependencies"))
    if pins:
        updated.dependency_pins = pins
    action = str(decision.get("action") or "keep")
    if action == "disable":
        updated.status = "disabled"
        updated.metadata["disabled"] = True
        if decision.get("reason"):
            updated.metadata["disabled_reason"] = str(decision["reason"])
    return updated


def apply_stale_payload(
    artifact: SkillArtifact,
    payload: Dict[str, Any],
) -> SkillArtifact:
    updated = copy.deepcopy(artifact)
    action = str(payload.get("action") or "keep_stale")
    if action == "clear_stale":
        updated.stale = False
        updated.status = "active"
    elif action == "keep_stale":
        updated.stale = True
        updated.status = "stale"
    elif action in {"refresh_minor", "refresh_major"}:
        updates = dict(payload.get("artifact_updates") or {})
        if updates.get("description"):
            updated.description = str(updates["description"])
        if updates.get("body"):
            updated.body = str(updates["body"])
        if updates.get("interface"):
            updated.interface = _coerce_interface_payload(
                updates.get("interface"),
                fallback_summary=updated.description,
            )
        updated.metadata = {**updated.metadata, **dict(updates.get("metadata") or {})}
        updated.stale = False
        updated.status = "active"
        updated.metadata["version_kind"] = "major" if action == "refresh_major" else "minor"
        updated.lineage.version_kind = updated.metadata["version_kind"]
        if not updated.bundle.bundle_id:
            updated.bundle.bundle_id = f"{updated.name}.bundle"
    elif action == "pin_legacy":
        updated.dependency_pins = _coerce_dependency_pins(payload.get("pinned_dependencies"))
        updated.stale = False
        updated.status = "active"
    return updated


def summarize_dependency_context(store_artifacts: Iterable[SkillArtifact]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for artifact in store_artifacts:
        out.append(
            {
                "name": artifact.name,
                "version": artifact.version,
                "version_kind": artifact.version_kind(),
                "description": artifact.description,
                "dependencies": list(artifact.dependencies or []),
                "stale": artifact.stale,
                "status": artifact.status,
                "dependency_pins": [item.as_dict() for item in artifact.dependency_pins],
            }
        )
    return out


def normalize_skill_name(raw: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw or "").strip().lower())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "skill_artifact"
