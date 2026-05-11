"""LLM-driven, benchmark-agnostic skill maintenance helpers.

The goal of this module is to mirror the main system's extractor/refiner
philosophy, but for generic skill artifacts rather than math-only Python code.
It provides:

- trace -> skill artifact extraction
- trace/failure -> skill-scoped bundle case distillation
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
from typing import Any, Dict, Iterable, List, Optional

from academic.config import EXTRACT_MODEL, LLM_CALL_TIMEOUT
from academic.skill_repository.types import (
    DependencyPin,
    SkillArtifact,
    SkillBundle,
    SkillBundleCase,
    SkillInterface,
    SkillLineage,
)


EXTRACT_SYSTEM = """\
You maintain a benchmark-agnostic skill repository.

Given successful and failed benchmark traces, extract reusable skill artifacts
that can help future tasks. You are not limited to code functions. A skill may
be a workflow card, rule card, interface convention, shared checklist, or an
executable helper description.

Rules:
1. Extract only skills with concrete causal evidence from the trace(s).
2. Keep skills benchmark-agnostic in naming and contract as much as the evidence allows.
3. The skill body must describe the actionable behavior precisely, not just the symptom.
4. If a skill uses another known skill, list it in `dependencies`.
5. Output only skills that are specific enough to be tested independently.
6. Prefer one focused skill over one broad benchmark-summary artifact.
7. When the evidence includes argument mismatches, wrong parameter names, unsupported path forms, extra calls, or stop-condition failures, prefer extracting the narrow corrective rule closest to the error site instead of a broad workflow summary.
8. For function-calling traces, prioritize rules such as:
   - exact parameter/argument naming,
   - positional-vs-keyword conventions,
   - unsupported path/value forms,
   - unnecessary extra calls to avoid,
   - precise state/id reuse requirements.
9. When multiple independent error sites appear, prefer returning multiple narrow skills rather than one mixed skill.
10. Use the task snapshot and expected calls when present to identify the exact local contract that failed.
11. If a call error is about a specific tool, parameter name, positional-vs-keyword convention, or avoiding one extra call, the skill should name that exact local rule.
12. When an extra exploratory call should be removed, prefer a positive local rule of the form "call Y directly with these literals/arguments" rather than only a negative ban on X.
13. If nothing reusable is supported by evidence, return {"artifacts": []}.

Return strict JSON:
{
  "artifacts": [
    {
      "name": "snake_case_name",
      "kind": "workflow_guardrail_card | atomic_tool_rule_card | interface_contract_card | planning_card | executable_tool | shared_subdoc",
      "description": "short summary",
      "body": "actionable content",
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
        "source": "llm_trace_extraction"
      },
      "dependencies": [],
      "dependency_pins": [],
      "version_kind": "seed"
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
11. Keep the bundle compact. Return at most 1 positive case, at most 1 negative case, and at most 1 integration case.
12. Each case must contain only the minimal single-turn or two-turn fragment needed to test this skill. Do not copy full traces, full metrics, debug events, or unrelated input artifacts.
13. Keep each `prompt` under 240 characters. Keep each user message under 500 characters. If fixture state is needed, include only the exact fields required by the skill.
14. For BFCL-like function-calling cases, `context.task_fragment.question` must be an array of turns, and each turn must be an array of message objects with `role` and `content`.
15. Return valid JSON that fits comfortably under 3500 output tokens. Do not include explanatory prose outside the schema.

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


REFINE_SYSTEM = """\
You are refining a versioned skill artifact after maintenance test failures.

You receive:
- the current skill artifact
- its bound bundle
- fresh test results
- integration failures
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
16. If returning bundle updates, include at most 1 positive, 1 negative, and 1 integration case. Each case must be a minimal fragment, not a copied full trace.
17. The entire response must fit under 3000 output tokens.

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


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _trim_text(value: str, limit: int = 14000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


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


async def _ask_json(
    *,
    system: str,
    user: str,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    role: str = "llm_maintenance",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    from app.llm import LLM
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
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if str(model or "").startswith("claude-") or "anthropic" in str(cfg.base_url or "") or "127.0.0.1:4000" in str(cfg.base_url or ""):
        response = await asyncio.wait_for(
            _ask_anthropic_json(
                system=system,
                user=user,
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=model,
                max_tokens=int(cfg.max_tokens or 4096),
            ),
            timeout=LLM_CALL_TIMEOUT,
        )
    else:
        llm = LLM(config_name=llm_config)
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
    duration_ms = int((time.monotonic() - started) * 1000)
    print(
        json.dumps(
            {
                "progress": "maintenance_llm_done",
                "role": role,
                "duration_ms": duration_ms,
                "response_chars": len(str(response or "")),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        data = json.loads(_extract_json_text(response))
    except Exception as exc:
        _append_llm_audit_log(
            role=role,
            llm_config=llm_config,
            model_name=model_name,
            system=system,
            user=user,
            raw_response=response,
            parsed_response=None,
            metadata={**dict(metadata or {}), "parse_error": str(exc), "duration_ms": duration_ms},
        )
        raise ValueError(f"LLM did not return valid JSON: {exc}\nRaw: {response[:1000]}") from exc
    if not isinstance(data, dict):
        _append_llm_audit_log(
            role=role,
            llm_config=llm_config,
            model_name=model_name,
            system=system,
            user=user,
            raw_response=response,
            parsed_response=data,
            metadata={**dict(metadata or {}), "type_error": type(data).__name__, "duration_ms": duration_ms},
        )
        raise ValueError(f"Expected JSON object, got: {type(data).__name__}")
    _append_llm_audit_log(
        role=role,
        llm_config=llm_config,
        model_name=model_name,
        system=system,
        user=user,
        raw_response=response,
        parsed_response=data,
        metadata={**dict(metadata or {}), "duration_ms": duration_ms},
    )
    return data


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
) -> str:
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

    timeout = httpx.Timeout(90.0, connect=10.0)
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
    return "\n".join(parts).strip()


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
        "metadata": artifact.metadata,
        "interface": artifact.interface.as_dict(),
        "bundle": artifact.bundle.as_dict(),
        "lineage": artifact.lineage.as_dict(),
        "dependencies": list(artifact.dependencies or []),
        "dependency_pins": [item.as_dict() for item in artifact.dependency_pins],
        "history_tail": list(artifact.history[-3:]),
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
                "input_artifacts": task.get("input_artifacts"),
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
                "turns": trace.get("turns"),
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


async def extract_skill_artifacts_from_results_llm(
    results: List[Dict[str, Any]],
    *,
    tool_schemas: Iterable[Dict[str, Any]] | None = None,
    existing_artifacts: Iterable[SkillArtifact] | None = None,
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
        f"{_json_block(existing_summary)}\n\n"
        "## Tool Schemas\n"
        f"{_json_block(tool_summary[:120])}\n\n"
        "## Call Error Evidence\n"
        f"{_json_block(error_summary[:80])}\n\n"
        "## Error Focus Hints\n"
        f"{_json_block(_error_focus_hints(results)[:80])}\n\n"
        "## Benchmark Results\n"
        f"{_json_block([_result_prompt_block(item) for item in results])}\n"
    )
    data = await _ask_json(
        system=EXTRACT_SYSTEM,
        user=_trim_text(user),
        llm_config=llm_config,
        model_name=model_name,
        role="extractor",
        metadata={
            "n_results": len(results),
            "n_existing_artifacts": len(list(existing_artifacts or [])),
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
            interface=_coerce_interface_payload(payload.get("interface"), fallback_summary=description),
            lineage=SkillLineage(version_kind=str(payload.get("version_kind") or "seed")),
            dependency_pins=_coerce_dependency_pins(payload.get("dependency_pins")),
            dependencies=[str(item).strip() for item in (payload.get("dependencies") or []) if str(item).strip()],
        )
        if not artifact.bundle.bundle_id:
            artifact.bundle.bundle_id = f"{artifact.name}.bundle"
        artifacts.append(artifact)
    return artifacts


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
        f"{_json_block(_artifact_prompt_block(artifact))}\n\n"
        "## Source Results (focused to turns relevant to this skill)\n"
        f"{_json_block([_result_prompt_block(item, focus_artifact=artifact) for item in source_results])}\n\n"
        "## Replay Results (focused)\n"
        f"{_json_block([_result_prompt_block(item, focus_artifact=artifact) for item in (replay_results or [])])}\n\n"
        "## Integration Failures\n"
        f"{_json_block(list(integration_failures or []))}\n"
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
    bundle.positive_cases = bundle.positive_cases[:1]
    bundle.negative_cases = bundle.negative_cases[:1]
    bundle.integration_cases = bundle.integration_cases[:1]
    return bundle


async def refine_skill_artifact_llm(
    artifact: SkillArtifact,
    *,
    test_result: Dict[str, Any],
    integration_failures: List[Dict[str, Any]] | None = None,
    refinement_history: List[Dict[str, Any]] | None = None,
    dependency_summaries: List[Dict[str, Any]] | None = None,
    llm_config: str = EXTRACT_MODEL,
    model_name: str | None = None,
    audit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    user = (
        "## Current Artifact\n"
        f"{_json_block(_artifact_prompt_block(artifact))}\n\n"
        "## Test Result\n"
        f"{_json_block(test_result)}\n\n"
        "## Integration Failures\n"
        f"{_json_block(list(integration_failures or []))}\n\n"
        "## Refinement History\n"
        f"{_json_block(list(refinement_history or []))}\n\n"
        "## Neighbor Dependency Summaries\n"
        f"{_json_block(list(dependency_summaries or []))}\n"
    )
    return await _ask_json(
        system=REFINE_SYSTEM,
        user=_trim_text(user),
        llm_config=llm_config,
        model_name=model_name,
        role="refiner",
        metadata={
            "artifact_name": artifact.name,
            "skill_version": artifact.version,
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
        f"{_json_block(_artifact_prompt_block(artifact))}\n\n"
        "## Upstream Update Context\n"
        f"{_json_block(upstream_context)}\n"
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
