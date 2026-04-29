from __future__ import annotations

import argparse
import asyncio
import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlmodel import SQLModel

from app.meta_agent.skills.database.manager import SkillDatabaseManager
from app.meta_agent.skills.pipeline import (
    PipelineResult,
    PlanningResult,
    ProposedSkill,
    SkillEvolvingPipeline,
)
from app.meta_agent.skills.tests.conftest import create_mock_skill


DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parent / "experiments" / "planning_replay_cases.json"
)

VALID_PLANNER_ACTIONS = {
    "fresh",
    "reuse_plan",
    "adapt_plan",
    "reuse_workflow_fragment",
    "propose_shared_skill",
}


@dataclass
class ReplaySkill:
    """Minimal skill payload needed to reconstruct the replay context."""

    name: str
    description: str
    code: str


@dataclass
class ReplayWorkflowFragment:
    """Optional named workflow snippet from history.

    A "fragment" is only a human-readable, optional sub-plan anchor. It is not a
    hard supervision target by default. The judge may decide a plan is good even
    if it does not reuse the exact fragment id listed here.
    """

    fragment_id: str
    content: str
    description: str = ""


@dataclass
class ReplayHistoryContext:
    previous_query: Optional[str] = None
    previous_workflow_plan: Optional[str] = None
    workflow_summary: Optional[str] = None
    historical_agent_summary: Optional[str] = None
    workflow_fragments: List[ReplayWorkflowFragment] = field(default_factory=list)
    trace_snippets: List[str] = field(default_factory=list)
    proposed_skills: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class ReplayReferences:
    """Soft reference labels for analysis and prompting.

    These fields are intentionally non-binding. They are hints for:
    - reference diagnostics, and
    - the comparative LLM judge prompt

    They are not used as strict pass/fail criteria because there may be multiple
    valid planning formulations for the same case.
    """

    preferred_actions: List[str] = field(default_factory=list)
    useful_plan_calls: List[str] = field(default_factory=list)
    relevant_fragment_ids: List[str] = field(default_factory=list)
    possible_shared_skill_names: List[str] = field(default_factory=list)
    discouraged_shared_skill_names: List[str] = field(default_factory=list)
    desirable_keywords: List[str] = field(default_factory=list)
    discouraged_keywords: List[str] = field(default_factory=list)
    rubric_notes: str = ""


@dataclass
class ReplayCase:
    case_id: str
    source_experiment: str
    problem_id: str
    query: str
    retrieved_skills: List[ReplaySkill]
    references: ReplayReferences = field(default_factory=ReplayReferences)
    status: str = "validated"
    failure_type: str = "unlabeled"
    annotation_notes: str = ""
    history_context: ReplayHistoryContext = field(default_factory=ReplayHistoryContext)
    candidate_metadata: Dict[str, Any] = field(default_factory=dict)
    mock_joint_alignments: List[str] = field(default_factory=list)
    mock_joint_response: str = ""
    mock_legacy_response: str = ""
    mock_judge_response: str = ""


@dataclass
class ReferenceDiagnostics:
    """Soft structural diagnostics used for debugging and fallback ranking."""

    planner_strategy: str
    planner_mode: Optional[str]
    history_reuse_action: Optional[str]
    action_reference_match: bool
    useful_plan_calls_used: int
    relevant_fragment_mentions: int
    possible_shared_skills_used: int
    discouraged_shared_skill_violations: List[str]
    missing_useful_plan_calls: List[str]
    missing_relevant_fragment_ids: List[str]
    missing_possible_shared_skills: List[str]
    missing_desirable_keywords: List[str]
    discouraged_keyword_violations: List[str]
    proposed_skill_names: List[str]
    reused_fragments: List[str]
    plan_char_count: int
    plan_is_valid_python: bool
    runtime_s: Optional[float]
    heuristic_score: int


@dataclass
class JudgeVerdict:
    available: bool
    winner: str
    confidence: str
    judge_type: str
    reasoning: str
    joint_refactor_score: Optional[int] = None
    legacy_planner_score: Optional[int] = None


class _ReplaySession:
    def __init__(self, prior_result: PipelineResult) -> None:
        self.round_results = [prior_result]
        self.query_history = [prior_result.query]

    async def close(self) -> None:
        return None


def _extract_called_names(plan: str) -> List[str]:
    try:
        tree = ast.parse(plan)
    except SyntaxError:
        return []
    return sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})


def _normalize_action(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    normalized = raw.strip().lower()
    return normalized if normalized in VALID_PLANNER_ACTIONS else normalized


def _coerce_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return [str(raw)]


def _load_replay_cases(path: Path) -> List[ReplayCase]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"Replay case file must be a list: {path}")

    cases: List[ReplayCase] = []
    for item in raw:
        retrieved_skills = [
            ReplaySkill(
                name=skill["name"],
                description=skill.get("description", ""),
                code=skill["code"],
            )
            for skill in item.get("retrieved_skills", [])
        ]
        history_raw = item.get("history_context", {}) or {}

        # Backward compatibility:
        # - old files use rigid `expectations`
        # - new files use soft `references`
        #
        # The conversion deliberately weakens the old labels into hints. Even if
        # an older case listed a single expected fragment or helper, the judge is
        # still allowed to accept alternative plans that use history well.
        refs_raw = item.get("references")
        if refs_raw is None:
            expectations_raw = item.get("expectations", {}) or {}
            refs_raw = {
                "preferred_actions": _coerce_str_list(expectations_raw.get("planner_action")),
                "useful_plan_calls": list(expectations_raw.get("expected_plan_calls", [])),
                "relevant_fragment_ids": list(
                    expectations_raw.get("expected_reused_fragment_ids", [])
                ),
                "possible_shared_skill_names": list(
                    expectations_raw.get("expected_shared_skill_names", [])
                ),
                "discouraged_shared_skill_names": list(
                    expectations_raw.get("forbidden_shared_skill_names", [])
                ),
                "desirable_keywords": list(expectations_raw.get("required_plan_keywords", [])),
                "discouraged_keywords": list(expectations_raw.get("forbidden_plan_keywords", [])),
                "rubric_notes": item.get("annotation_notes", ""),
            }

        references = ReplayReferences(
            preferred_actions=_coerce_str_list(refs_raw.get("preferred_actions")),
            useful_plan_calls=list(refs_raw.get("useful_plan_calls", [])),
            relevant_fragment_ids=list(refs_raw.get("relevant_fragment_ids", [])),
            possible_shared_skill_names=list(refs_raw.get("possible_shared_skill_names", [])),
            discouraged_shared_skill_names=list(
                refs_raw.get("discouraged_shared_skill_names", [])
            ),
            desirable_keywords=list(refs_raw.get("desirable_keywords", [])),
            discouraged_keywords=list(refs_raw.get("discouraged_keywords", [])),
            rubric_notes=refs_raw.get("rubric_notes", ""),
        )

        case = ReplayCase(
            case_id=item["case_id"],
            source_experiment=item.get("source_experiment", "unknown"),
            problem_id=item.get("problem_id", item["case_id"]),
            query=item["query"],
            retrieved_skills=retrieved_skills,
            references=references,
            status=item.get("status", "validated"),
            failure_type=item.get("failure_type", "unlabeled"),
            annotation_notes=item.get("annotation_notes", ""),
            history_context=ReplayHistoryContext(
                previous_query=history_raw.get("previous_query"),
                previous_workflow_plan=history_raw.get("previous_workflow_plan"),
                workflow_summary=history_raw.get("workflow_summary"),
                historical_agent_summary=history_raw.get("historical_agent_summary"),
                workflow_fragments=[
                    ReplayWorkflowFragment(
                        fragment_id=fragment["fragment_id"],
                        content=fragment["content"],
                        description=fragment.get("description", ""),
                    )
                    for fragment in history_raw.get("workflow_fragments", [])
                ],
                trace_snippets=list(history_raw.get("trace_snippets", [])),
                proposed_skills=list(history_raw.get("proposed_skills", [])),
            ),
            candidate_metadata=dict(item.get("candidate_metadata", {})),
            mock_joint_alignments=list(item.get("mock_joint_alignments", [])),
            mock_joint_response=item.get("mock_joint_response", ""),
            mock_legacy_response=item.get("mock_legacy_response", ""),
            mock_judge_response=item.get("mock_judge_response", ""),
        )
        cases.append(case)
    return cases


def _materialize_retrieved_skills(
    manager: SkillDatabaseManager,
    replay_case: ReplayCase,
) -> List[Any]:
    results = []
    for skill in replay_case.retrieved_skills:
        created = create_mock_skill(
            manager,
            skill.name,
            "1.0",
            code=skill.code,
            group_name=f"replay::{replay_case.case_id}::{skill.name}",
        )
        created.docstring = skill.description
        results.append(created)
    return results


def _make_history_result(case: ReplayCase) -> PipelineResult:
    class _Trace:
        def __init__(self) -> None:
            self.query = case.history_context.previous_query or ""
            self.workflow_plan = case.history_context.previous_workflow_plan or ""
            self.code_blocks = list(case.history_context.trace_snippets)
            self.final_answer = (
                case.history_context.workflow_summary
                or case.history_context.historical_agent_summary
                or ""
            )
            self.steps = []

    prior = PipelineResult(query=case.history_context.previous_query or "")
    prior.execution_trace = _Trace()
    prior.tester.ok = True
    prior.planning_result = PlanningResult(
        workflow_plan=case.history_context.previous_workflow_plan or "",
        proposed_skills=[
            ProposedSkill(
                name=item["name"],
                description=item.get("description", item["name"]),
                code_fragment=item.get("code_fragment", ""),
                source_query=case.history_context.previous_query or "",
            )
            for item in case.history_context.proposed_skills
        ],
        metadata={
            "planner_strategy": "replay_history",
            "planner_mode": "FRESH",
            "workflow_summary": case.history_context.workflow_summary,
            "historical_agent_summary": case.history_context.historical_agent_summary,
            # Fragment ids are light-weight handles for optional reuse anchors.
            # They help the planner or judge refer to a reusable sub-workflow
            # without forcing the current plan to copy the exact same text.
            "workflow_fragment_ids": [
                frag.fragment_id for frag in case.history_context.workflow_fragments
            ],
        },
    )
    return prior


def _diagnose_plan(case: ReplayCase, planning: Optional[PlanningResult]) -> Dict[str, object]:
    refs = case.references
    if planning is None:
        diagnostics = ReferenceDiagnostics(
            planner_strategy="missing",
            planner_mode=None,
            history_reuse_action=None,
            action_reference_match=False,
            useful_plan_calls_used=0,
            relevant_fragment_mentions=0,
            possible_shared_skills_used=0,
            discouraged_shared_skill_violations=[],
            missing_useful_plan_calls=list(refs.useful_plan_calls),
            missing_relevant_fragment_ids=list(refs.relevant_fragment_ids),
            missing_possible_shared_skills=list(refs.possible_shared_skill_names),
            missing_desirable_keywords=list(refs.desirable_keywords),
            discouraged_keyword_violations=[],
            proposed_skill_names=[],
            reused_fragments=[],
            plan_char_count=0,
            plan_is_valid_python=False,
            runtime_s=None,
            heuristic_score=0,
        )
        return {
            "available": False,
            "diagnostics": asdict(diagnostics),
            "workflow_plan": "",
            "proposed_skill_names": [],
        }

    proposed_names = [skill.name for skill in planning.proposed_skills]
    plan = planning.workflow_plan or ""
    try:
        ast.parse(plan)
        plan_is_valid_python = True
    except SyntaxError:
        plan_is_valid_python = False

    called_names = _extract_called_names(plan)
    action = _normalize_action(planning.metadata.get("history_reuse_action"))
    action_reference_match = (
        not refs.preferred_actions or action in refs.preferred_actions
    )

    missing_useful_calls = [
        name for name in refs.useful_plan_calls if name not in called_names
    ]
    # `reused_fragments` should be read as optional evidence that the planner
    # intentionally pointed at a historical sub-plan. Missing one is not an
    # automatic failure; it is only a soft diagnostic signal.
    reused_fragments = list(planning.metadata.get("reused_fragments", []))
    missing_fragment_ids = [
        frag for frag in refs.relevant_fragment_ids if frag not in reused_fragments
    ]
    missing_possible_shared = [
        name for name in refs.possible_shared_skill_names if name not in proposed_names
    ]
    discouraged_shared = [
        name for name in proposed_names if name in refs.discouraged_shared_skill_names
    ]

    plan_lower = plan.lower()
    missing_desirable_keywords = [
        token for token in refs.desirable_keywords if token.lower() not in plan_lower
    ]
    discouraged_keyword_violations = [
        token for token in refs.discouraged_keywords if token.lower() in plan_lower
    ]

    # This heuristic score is only a debugging aid and fallback tie-breaker.
    # It is not the primary benchmark outcome once the LLM judge is available.
    heuristic_score = 0
    if action_reference_match:
        heuristic_score += 2
    heuristic_score += len(refs.useful_plan_calls) - len(missing_useful_calls)
    heuristic_score += len(refs.relevant_fragment_ids) - len(missing_fragment_ids)
    heuristic_score += len(refs.possible_shared_skill_names) - len(missing_possible_shared)
    heuristic_score += len(refs.desirable_keywords) - len(missing_desirable_keywords)
    heuristic_score -= len(discouraged_shared)
    heuristic_score -= len(discouraged_keyword_violations)
    if plan_is_valid_python:
        heuristic_score += 1

    diagnostics = ReferenceDiagnostics(
        planner_strategy=planning.metadata.get("planner_strategy", "unknown"),
        planner_mode=planning.metadata.get("planner_mode"),
        history_reuse_action=action,
        action_reference_match=action_reference_match,
        useful_plan_calls_used=len(refs.useful_plan_calls) - len(missing_useful_calls),
        relevant_fragment_mentions=len(refs.relevant_fragment_ids) - len(missing_fragment_ids),
        possible_shared_skills_used=len(refs.possible_shared_skill_names) - len(missing_possible_shared),
        discouraged_shared_skill_violations=discouraged_shared,
        missing_useful_plan_calls=missing_useful_calls,
        missing_relevant_fragment_ids=missing_fragment_ids,
        missing_possible_shared_skills=missing_possible_shared,
        missing_desirable_keywords=missing_desirable_keywords,
        discouraged_keyword_violations=discouraged_keyword_violations,
        proposed_skill_names=proposed_names,
        reused_fragments=reused_fragments,
        plan_char_count=len(plan),
        plan_is_valid_python=plan_is_valid_python,
        runtime_s=planning.metadata.get("runtime_s"),
        heuristic_score=heuristic_score,
    )
    return {
        "available": True,
        "diagnostics": asdict(diagnostics),
        "workflow_plan": plan,
        "proposed_skill_names": proposed_names,
    }


def _judge_payload_to_verdict(obj: Dict[str, Any]) -> JudgeVerdict:
    winner = str(obj.get("winner", "tie")).strip()
    if winner not in {"joint_refactor", "legacy_planner", "tie"}:
        winner = "tie"
    return JudgeVerdict(
        available=True,
        winner=winner,
        confidence=str(obj.get("confidence", "medium")),
        judge_type=str(obj.get("judge_type", "llm")),
        reasoning=str(obj.get("reasoning", "")),
        joint_refactor_score=obj.get("joint_refactor_score"),
        legacy_planner_score=obj.get("legacy_planner_score"),
    )


def _build_judge_prompt(
    case: ReplayCase,
    joint_result: Dict[str, object],
    legacy_result: Dict[str, object],
) -> str:
    refs = case.references
    history_lines: List[str] = []
    if case.history_context.previous_query:
        history_lines.append(f"Previous query: {case.history_context.previous_query}")
    if case.history_context.workflow_summary:
        history_lines.append(f"Workflow summary: {case.history_context.workflow_summary}")
    if case.history_context.previous_workflow_plan:
        history_lines.append(
            "Previous workflow plan:\n```python\n"
            + case.history_context.previous_workflow_plan
            + "\n```"
        )
    if case.history_context.workflow_fragments:
        # Fragments are small named workflow pieces for human-readable analysis.
        # They are not mandatory extraction units or mandatory plan outputs.
        fragment_text = "\n".join(
            f"- {frag.fragment_id}: {frag.description or frag.content[:120]}"
            for frag in case.history_context.workflow_fragments
        )
        history_lines.append(f"Named historical workflow fragments:\n{fragment_text}")

    reference_lines: List[str] = []
    if refs.preferred_actions:
        reference_lines.append(f"Preferred actions: {', '.join(refs.preferred_actions)}")
    if refs.useful_plan_calls:
        reference_lines.append(f"Useful calls: {', '.join(refs.useful_plan_calls)}")
    if refs.relevant_fragment_ids:
        reference_lines.append(
            f"Relevant historical fragments: {', '.join(refs.relevant_fragment_ids)}"
        )
    if refs.possible_shared_skill_names:
        reference_lines.append(
            f"Possible valid shared skills: {', '.join(refs.possible_shared_skill_names)}"
        )
    if refs.discouraged_shared_skill_names:
        reference_lines.append(
            f"Discouraged shared skills: {', '.join(refs.discouraged_shared_skill_names)}"
        )
    if refs.rubric_notes:
        reference_lines.append(f"Rubric notes: {refs.rubric_notes}")

    return (
        "You are judging a planning replay benchmark.\n"
        "Important: the references below are SOFT hints, not hard rules.\n"
        "Multiple planning formulations may be valid.\n"
        "Prefer the plan that uses history appropriately, avoids unnecessary abstraction, "
        "and better serves the current query.\n\n"
        f"Current query:\n{case.query}\n\n"
        f"Retrieved skills:\n"
        + "\n".join(f"- {sk.name}: {sk.description}" for sk in case.retrieved_skills)
        + "\n\n"
        + ("Historical context:\n" + "\n".join(history_lines) + "\n\n" if history_lines else "")
        + ("Soft references:\n" + "\n".join(reference_lines) + "\n\n" if reference_lines else "")
        + "Candidate A: joint_refactor\n"
        + json.dumps(
            {
                "diagnostics": joint_result["diagnostics"],
                "workflow_plan": joint_result["workflow_plan"],
                "proposed_skill_names": joint_result["proposed_skill_names"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\nCandidate B: legacy_planner\n"
        + json.dumps(
            {
                "diagnostics": legacy_result["diagnostics"],
                "workflow_plan": legacy_result["workflow_plan"],
                "proposed_skill_names": legacy_result["proposed_skill_names"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\nReturn JSON with keys:"
        + " winner, confidence, reasoning, joint_refactor_score, legacy_planner_score, judge_type."
    )


async def _judge_case(
    case: ReplayCase,
    *,
    joint_result: Dict[str, object],
    legacy_result: Dict[str, object],
    allow_live_llm: bool,
) -> JudgeVerdict:
    if case.mock_judge_response:
        # Synthetic/offline tests use deterministic mock judge outputs so that
        # unit tests exercise the benchmark wiring without depending on a live LLM.
        return _judge_payload_to_verdict(json.loads(case.mock_judge_response))

    if not allow_live_llm:
        return JudgeVerdict(
            available=False,
            winner="tie",
            confidence="none",
            judge_type="unavailable",
            reasoning="LLM judge not available; falling back to heuristic diagnostics.",
        )

    from app.llm import LLM

    prompt = _build_judge_prompt(case, joint_result, legacy_result)
    llm = LLM(config_name="tool_maker")
    response = await llm.ask(
        messages=[{"role": "user", "content": prompt}],
        system_msgs=[{
            "role": "system",
            "content": (
                "You are a strict but flexible planning benchmark judge. "
                "Return JSON only."
            ),
        }],
    )
    try:
        return _judge_payload_to_verdict(json.loads(response))
    except Exception:
        return JudgeVerdict(
            available=False,
            winner="tie",
            confidence="low",
            judge_type="parse_failed",
            reasoning=f"Judge response could not be parsed as JSON: {response[:400]}",
        )


def _heuristic_winner(joint_result: Dict[str, object], legacy_result: Dict[str, object]) -> str:
    # This path exists only so the benchmark still produces a result when the
    # judge is unavailable. Report this as fallback behavior, not as the primary
    # evaluation method.
    joint_score = joint_result["diagnostics"]["heuristic_score"]
    legacy_score = legacy_result["diagnostics"]["heuristic_score"]
    if joint_score > legacy_score:
        return "joint_refactor"
    if legacy_score > joint_score:
        return "legacy_planner"
    return "tie"


async def run_planning_replay_benchmark(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    db_url: Optional[str] = None,
    db_manager: Optional[SkillDatabaseManager] = None,
    allow_live_llm: bool = False,
) -> Dict[str, object]:
    if db_manager is None and db_url is None:
        raise ValueError("Either db_url or db_manager must be provided")

    manager = db_manager or SkillDatabaseManager(db_url)
    with manager.engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    SQLModel.metadata.create_all(manager.engine)

    pipeline = SkillEvolvingPipeline()
    pipeline.db_manager = manager
    cases = _load_replay_cases(cases_path)

    results: List[Dict[str, object]] = []
    for case in cases:
        retrieved_skills = _materialize_retrieved_skills(manager, case)
        has_history = any(
            [
                case.history_context.previous_workflow_plan,
                case.history_context.workflow_summary,
                case.history_context.historical_agent_summary,
                case.history_context.workflow_fragments,
                case.history_context.trace_snippets,
            ]
        )
        session = _ReplaySession(_make_history_result(case)) if has_history else None
        skipped = False
        skip_reason = ""

        if case.mock_joint_response and case.mock_legacy_response:
            alignment_iter = iter(case.mock_joint_alignments)

            async def _mock_joint_ask(*args, **kwargs):
                if kwargs.get("system_msgs"):
                    return case.mock_joint_response
                try:
                    return next(alignment_iter)
                except StopIteration:
                    return "NONE"

            with patch("app.llm.LLM.ask", new=AsyncMock(side_effect=_mock_joint_ask)):
                joint = await pipeline._run_joint_refactor_planner(
                    query=case.query,
                    retrieved_skills=retrieved_skills,
                    session=session,
                    query_embedding=None,
                    planner_mode="FRESH",
                    rubric={"reason": "replay_benchmark"},
                )

            with patch(
                "app.llm.LLM.ask",
                new=AsyncMock(return_value=case.mock_legacy_response),
            ):
                legacy = await pipeline._run_legacy_planner(
                    query=case.query,
                    retrieved_skills=retrieved_skills,
                    session=session,
                    query_embedding=None,
                    planner_mode="FRESH",
                    rubric={"reason": "replay_benchmark_legacy"},
                )
        elif allow_live_llm:
            joint = await pipeline._run_joint_refactor_planner(
                query=case.query,
                retrieved_skills=retrieved_skills,
                session=session,
                query_embedding=None,
                planner_mode="FRESH",
                rubric={"reason": "replay_benchmark_live"},
            )
            legacy = await pipeline._run_legacy_planner(
                query=case.query,
                retrieved_skills=retrieved_skills,
                session=session,
                query_embedding=None,
                planner_mode="FRESH",
                rubric={"reason": "replay_benchmark_live_legacy"},
            )
        else:
            joint = None
            legacy = None
            skipped = True
            skip_reason = "missing_mock_responses_and_live_llm_disabled"

        joint_diagnostics = _diagnose_plan(case, joint)
        legacy_diagnostics = _diagnose_plan(case, legacy)
        judge = await _judge_case(
            case,
            joint_result=joint_diagnostics,
            legacy_result=legacy_diagnostics,
            allow_live_llm=allow_live_llm,
        )
        heuristic_winner = _heuristic_winner(joint_diagnostics, legacy_diagnostics)
        winner = judge.winner if judge.available else heuristic_winner

        results.append(
            {
                "case": asdict(case),
                "skipped": skipped,
                "skip_reason": skip_reason,
                # These sections expose enough structure for debugging a bad case:
                # plan text, proposed skill names, and soft-reference diagnostics.
                "joint_refactor": joint_diagnostics,
                "legacy_planner": legacy_diagnostics,
                "judge": asdict(judge),
                "heuristic_winner": heuristic_winner,
                "winner": winner,
            }
        )

        if session is not None:
            await session.close()

    wins = sum(1 for item in results if item["winner"] == "joint_refactor")
    judged_cases = sum(1 for item in results if item["judge"]["available"])
    available_cases = sum(
        1
        for item in results
        if item["joint_refactor"]["available"] or item["legacy_planner"]["available"]
    )
    return {
        "benchmark": "planning_replay_benchmark",
        "benchmark_version": "workflow_reuse_v3_judge",
        "cases_path": str(cases_path),
        "n_cases": len(results),
        "n_available_cases": available_cases,
        "n_judged_cases": judged_cases,
        "joint_refactor_wins": wins,
        "joint_refactor_win_rate": round(wins / max(len(results), 1), 4),
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline planning replay benchmark")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH))
    parser.add_argument(
        "--db-url",
        default="postgresql+psycopg2://edumanus_user:edumanus_password@localhost:15432/aicosmos_test",
    )
    parser.add_argument("--allow-live-llm", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = asyncio.run(
        run_planning_replay_benchmark(
            cases_path=Path(args.cases),
            db_url=args.db_url,
            allow_live_llm=args.allow_live_llm,
        )
    )
    text_out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text_out)
    print(text_out)


if __name__ == "__main__":
    main()
