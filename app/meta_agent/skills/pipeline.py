"""
skill_evolving_pipeline.py — End-to-end skill_evolving_v1 pipeline.

Orchestrates the full loop:
  1. Retrieval   — embed query → pgvector search for relevant skills
  2. Executor    — CodeAct agent runs user query inside Docker IPython,
                   with retrieved skill code available as importable module
  3. Extractor   — Gardener agent analyses the execution trace, produces refactored skill
  4. Tester      — Reviewer agent validates the new skill version
  5. Commit      — new skill version + embedding saved to DB

Usage (from CLI):
  See scripts/skill_evolve_cli.py  (--demo / --query flags)

Usage (programmatic):
  from app.meta_agent.skills.pipeline import SkillEvolvingPipeline, PipelineResult
  result = await SkillEvolvingPipeline().run(query="...", skill_name="my_skill")
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app.meta_agent.skills.database.manager import SkillDatabaseManager
from app.meta_agent.skills.database.models import Skill, SkillGroup
from app.meta_agent.skills.retrieval import SkillRetriever, RetrievalResult, CollaborativeRetriever
from app.meta_agent.skills.schemas import AgentTrace, TraceFormat, TraceStep
from app.meta_agent.skills.workflow_view import WorkflowView

logger = logging.getLogger("skill_pipeline")


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class PhaseResult:
    ok: bool
    elapsed_s: float
    detail: str = ""
    error: str = ""


@dataclass
class ProposedSkill:
    """v2.1: 规划阶段从历史经验中提取的候选技能片段。"""
    name: str                # 建议的函数名
    description: str         # 功能描述
    code_fragment: str       # 提取的代码骨架（含 def 签名）
    source_query: str        # 来源的历史查询文本（用于溯源）


@dataclass
class PlanningResult:
    """v2.1: 规划阶段的结构化输出。"""
    workflow_plan: str                                         # Python 工作流骨架
    proposed_skills: List[ProposedSkill] = field(default_factory=list)  # 从历史中提取的候选技能


@dataclass
class PipelineResult:
    query: str
    retrieval: PhaseResult = field(default_factory=lambda: PhaseResult(False, 0))
    executor: PhaseResult = field(default_factory=lambda: PhaseResult(False, 0))
    extractor: PhaseResult = field(default_factory=lambda: PhaseResult(False, 0))
    tester: PhaseResult = field(default_factory=lambda: PhaseResult(False, 0))
    commit: PhaseResult = field(default_factory=lambda: PhaseResult(False, 0))

    # payloads set during the run
    retrieved_skills: List[Skill] = field(default_factory=list)
    execution_trace: Optional[AgentTrace] = None
    new_skill_code: Optional[str] = None
    new_skill_id: Optional[int] = None
    planning_result: Optional["PlanningResult"] = None   # exposed for callers & tests


@dataclass
class PipelineSession:
    """
    Maintains shared state across multiple rounds of the skill-evolving pipeline.

    A session keeps:
      - The DB connection alive (no re-connect per query)
      - The Docker IPython terminal alive (no re-spawn per query)
      - Accumulated query/trace history so later rounds have richer context
      - A record of which skill_ids were evolved during the session

    Usage::

        session = PipelineSession.create(db_url)
        try:
            r1 = await pipeline.run_with_session(session, query="...", skill_name="foo")
            r2 = await pipeline.run_with_session(session, query="...", skill_name="foo")
        finally:
            await session.close()   # shuts down Docker terminal gracefully
    """

    session_id: str
    db_manager: SkillDatabaseManager

    # Docker executor state (kept alive between rounds)
    terminal_id: Optional[str] = None
    sandbox: Optional[Any] = None           # SandboxTool instance

    # Accumulated history
    query_history: List[str] = field(default_factory=list)
    traces: List[AgentTrace] = field(default_factory=list)
    evolved_skill_ids: List[int] = field(default_factory=list)
    round_results: List["PipelineResult"] = field(default_factory=list)

    # Workflow view (ARCH-BUG-4): persists and tracks workflow plans across rounds.
    # Lazily initialised on first use; agent accesses it via WorkflowManagerTool.
    workflow_view: Optional[WorkflowView] = field(default=None)

    @classmethod
    def create(cls, db_url: str, session_id: Optional[str] = None) -> "PipelineSession":
        """Create a new session with a fresh DB connection."""
        sid = session_id or str(uuid.uuid4())[:8]
        return cls(
            session_id=sid,
            db_manager=_get_db(db_url),
            workflow_view=WorkflowView(session_id=sid),
        )

    async def close(self) -> None:
        """Gracefully shut down the Docker terminal if one is active."""
        if self.sandbox is not None:
            try:
                await self.sandbox.close()
            except Exception:
                pass
            self.sandbox = None
            self.terminal_id = None

    @property
    def round_count(self) -> int:
        return len(self.round_results)

    def summary(self) -> str:
        lines = [
            f"Session [{self.session_id}]  rounds={self.round_count}",
            f"  terminal_id={self.terminal_id or '(none)'}",
            f"  evolved skills: {self.evolved_skill_ids}",
        ]
        for i, q in enumerate(self.query_history):
            lines.append(f"  round {i+1}: {q[:80]}")
        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_db(db_url: str) -> SkillDatabaseManager:
    manager = SkillDatabaseManager(db_url)
    with manager.engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    SQLModel.metadata.create_all(manager.engine)
    return manager


def _ensure_skill_group(db_manager: SkillDatabaseManager, skill_name: str) -> SkillGroup:
    """Return existing SkillGroup or create one."""
    with Session(db_manager.engine) as session:
        group = session.exec(select(SkillGroup).where(SkillGroup.name == skill_name)).first()
        if not group:
            group = SkillGroup(name=skill_name, description=f"Auto-created group for {skill_name}")
            session.add(group)
            session.commit()
            session.refresh(group)
        return group


def _ensure_seed_skill(
    db_manager: SkillDatabaseManager,
    skill_name: str,
    seed_code: str,
) -> Skill:
    """Ensure at least a v1.0 seed skill exists; return it."""
    group = _ensure_skill_group(db_manager, skill_name)
    with Session(db_manager.engine) as session:
        existing = session.exec(
            select(Skill)
            .where(Skill.group_id == group.id)
            .where(Skill.major_version == 1)
            .where(Skill.minor_version == 0)
        ).first()
        if existing:
            return existing

        skill = Skill(
            group_id=group.id,
            major_version=1,
            minor_version=0,
            code=seed_code,
            docstring=f"Seed skill: {skill_name} v1.0",
            interface_schema={},
            embedding=[0.0] * 1024,
        )
        session.add(skill)
        session.commit()
        session.refresh(skill)
        return skill


def _build_skill_module_code(skills: List[Skill]) -> str:
    """
    Build a Python code snippet that defines all retrieved skills as importable
    functions in the executor's IPython environment.

    The executor agent will see a module 'core_skills' available that it can
    import directly, e.g.:  from core_skills import fetch_student_transcript
    """
    parts = ["# Auto-generated skill module for executor context"]
    for sk in skills:
        if sk.code:
            parts.append(f"\n# Skill: group_id={sk.group_id}  v{sk.major_version}.{sk.minor_version}")
            parts.append(sk.code)
    return "\n".join(parts)


def _build_executor_workflow(query: str, skills: List[Skill]) -> str:
    """
    Build the 'workflow guideline' string passed to CodeActWorkflowExecutorAgent.

    It describes:
    - What skills are available (via the preloaded core_skills module)
    - How to approach the query using those skills
    - What to do if existing skills are insufficient
    """
    skill_summaries = []
    for sk in skills:
        doc = (sk.docstring or "").strip()[:200]
        skill_summaries.append(
            f"  - group_id={sk.group_id} v{sk.major_version}.{sk.minor_version}: {doc}"
        )
    skill_block = "\n".join(skill_summaries) if skill_summaries else "  (none found — you will need to implement from scratch)"

    return textwrap.dedent(f"""
    # Skill-Evolving CodeAct Workflow

    ## User Query
    {query}

    ## Available Skills (pre-imported into `core_skills` module)
    {skill_block}

    ## Instructions
    1. Import relevant functions from the `core_skills` module:
       ```python
       from core_skills import <function_name>
       ```
    2. Try to satisfy the query using the available skills.
    3. If a skill does not fully satisfy the query (wrong format, missing fields, etc.):
       - Call the skill anyway to observe its output
       - Then write additional Python code to bridge the gap (parse, transform, supplement)
    4. Print intermediate outputs clearly with labels so the execution trace is informative.
    5. When done, output your final answer clearly.

    ## Important
    - Always call existing skills even if they seem insufficient — the trace of the attempt
      is what the downstream Extractor agent will learn from.
    - Do NOT silently skip calling the existing skill.
    """).strip()


# ── Executor trace builder ────────────────────────────────────────────────────

def _memory_to_agent_trace(query: str, memory) -> AgentTrace:
    """
    Convert the executor agent's Memory object into an AgentTrace for the extractor.
    """
    steps: List[TraceStep] = []
    step_idx = 0

    messages = memory.messages if memory else []

    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.role == "assistant":
            thought = ""
            code_block = None
            tool_call_name = None
            tool_input = None

            # Extract thought from text content
            if msg.content:
                thought = str(msg.content)

            # Check for tool calls (SandboxTool = code execution)
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                for tc in tool_calls:
                    fn_name = getattr(getattr(tc, "function", None), "name", "")
                    if fn_name in ("sandbox", "execute_code", "SandboxTool"):
                        import json as _json
                        args_raw = getattr(getattr(tc, "function", None), "arguments", "{}")
                        try:
                            args = _json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                            code_block = args.get("command", args.get("code", str(args_raw)))
                        except Exception:
                            code_block = str(args_raw)
                    tool_call_name = fn_name
                    tool_input = {"function": fn_name}

            # Look ahead for the tool result
            tool_output = None
            if i + 1 < len(messages) and messages[i + 1].role in ("tool", "function"):
                tool_output = str(messages[i + 1].content or "")
                i += 1  # consume the tool message

            step_idx += 1
            steps.append(TraceStep(
                step_id=f"step_{step_idx}",
                thought=thought[:2000] if thought else "(no thought)",
                tool_call=tool_call_name,
                tool_input=tool_input,
                tool_output=(tool_output or "")[:2000],
                code_block=code_block,
                status="success",
            ))

        i += 1

    if not steps:
        # Fallback: single-step trace with just the query
        steps = [TraceStep(
            step_id="step_1",
            thought=f"Executed query: {query}",
            status="success",
        )]

    return AgentTrace(
        query=query,
        trace_format=TraceFormat.CODEACT,
        steps=steps,
        final_answer="(extracted from executor memory)",
        involved_skills=[],
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

class SkillEvolvingPipeline:
    """
    Orchestrates the full skill_evolving_v1 loop:
      Retrieval → Executor (CodeAct) → Extractor → Tester → Commit
    """

    DEFAULT_DB_URL = (
        "postgresql+psycopg2://edumanus_user:edumanus_password"
        "@localhost:15432/aicosmos_test"
    )

    def __init__(self, db_url: Optional[str] = None) -> None:
        self.db_url = db_url or self.DEFAULT_DB_URL
        self.db_manager: Optional[SkillDatabaseManager] = None

    # ── public entry points ───────────────────────────────────────────────────

    async def run(
        self,
        query: str,
        skill_name: str,
        seed_code: Optional[str] = None,
        skill_id: Optional[int] = None,
        top_k: int = 5,
        similarity_threshold: float = 0.3,
        skip_executor: bool = False,
        skip_tester: bool = False,
        prebuilt_trace: Optional[AgentTrace] = None,
    ) -> PipelineResult:
        """
        Run the full pipeline for a single query (stateless — no session reuse).

        For multi-turn interactive use, prefer ``run_with_session()`` which keeps
        the Docker terminal and DB connection alive between rounds.
        """
        # Only initialize db_manager if not already injected (e.g., by tests via mock_db)
        if self.db_manager is None:
            self.db_manager = _get_db(self.db_url)

        # Seed skill if needed
        if seed_code and skill_id is None:
            seed = _ensure_seed_skill(self.db_manager, skill_name, seed_code)
            skill_id = seed.id
            logger.info(f"[pipeline] seed skill_id={skill_id}")

        return await self._run_phases(
            query=query,
            skill_name=skill_name,
            skill_id=skill_id,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            skip_executor=skip_executor,
            skip_tester=skip_tester,
            prebuilt_trace=prebuilt_trace,
            session=None,
        )

    async def run_with_session(
        self,
        session: PipelineSession,
        query: str,
        skill_name: str,
        seed_code: Optional[str] = None,
        skill_id: Optional[int] = None,
        top_k: int = 5,
        similarity_threshold: float = 0.3,
        skip_tester: bool = False,
        prebuilt_trace: Optional[AgentTrace] = None,
    ) -> PipelineResult:
        """
        Run one round of the pipeline within a shared session context.

        The session keeps the Docker IPython terminal alive between rounds so
        subsequent queries reuse the same container and IPython state.
        Query/trace history is accumulated on ``session`` for richer context
        in later rounds.

        Args:
            session:  A ``PipelineSession`` created via ``PipelineSession.create()``.
            query:    Natural-language user request for this round.
            skill_name: Name of the skill group to evolve (or create).
            seed_code:  Optional Python source to seed v1.0 if it doesn't exist.
            skill_id:   Target skill DB ID (skips retrieval-based selection).
            top_k / similarity_threshold: Retrieval parameters.
            skip_tester: If True, skip the reviewer/tester phase this round.
            prebuilt_trace: Supply a ready-made AgentTrace (skips executor).
                            Useful in tests and demos.

        Returns:
            ``PipelineResult`` for this round; session state is updated in-place.
        """
        # Use the session's DB manager
        self.db_manager = session.db_manager

        # Seed skill if needed
        if seed_code and skill_id is None:
            seed = _ensure_seed_skill(self.db_manager, skill_name, seed_code)
            skill_id = seed.id
            logger.info(f"[pipeline] seed skill_id={skill_id}  session={session.session_id}")

        result = await self._run_phases(
            query=query,
            skill_name=skill_name,
            skill_id=skill_id,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            skip_executor=False,
            skip_tester=skip_tester,
            prebuilt_trace=prebuilt_trace,
            session=session,
        )

        # Accumulate history onto session
        session.query_history.append(query)
        if result.execution_trace:
            session.traces.append(result.execution_trace)
        if result.new_skill_id:
            session.evolved_skill_ids.append(result.new_skill_id)
        session.round_results.append(result)

        logger.info(
            f"[pipeline] round {session.round_count} complete  "
            f"session={session.session_id}  new_skill_id={result.new_skill_id}"
        )
        return result

    # ── internal phases orchestrator ──────────────────────────────────────────

    async def _run_phases(
        self,
        query: str,
        skill_name: str,
        skill_id: Optional[int],
        top_k: int,
        similarity_threshold: float,
        skip_executor: bool,
        skip_tester: bool,
        prebuilt_trace: Optional[AgentTrace],
        session: Optional[PipelineSession],
    ) -> PipelineResult:
        """Internal: run all pipeline phases, returning a PipelineResult."""
        result = PipelineResult(query=query)

        # ── Phase 0: Retrieval (collaborative filtering) ────────────────────
        result.retrieval = await self._phase_retrieval(query, top_k, similarity_threshold)
        result.retrieved_skills = result.retrieval.__dict__.get("_skills", [])
        # store skills + cached embedding via side-channel attrs on PhaseResult
        retrieved_skills: List[Skill] = getattr(result.retrieval, "_skills", [])
        _query_embedding = getattr(result.retrieval, "query_embedding", None) or getattr(result.retrieval, "_query_embedding", None)

        # ── Phase 0.5: Workflow Planning (v2.2) ──────────────────────────────
        planning_result: Optional[PlanningResult] = None
        # Keep planning enabled even when prebuilt_trace is supplied so multi-turn
        # sessions can still produce/use proposed_skills without requiring Docker.
        if not skip_executor:
            _MAX_PLANNING_RETRIES = 3
            for _attempt in range(_MAX_PLANNING_RETRIES):
                planning_result = await self._run_planning_phase(
                    query, retrieved_skills, session=session, query_embedding=_query_embedding
                )
                if planning_result is not None:
                    break
                if _attempt < _MAX_PLANNING_RETRIES - 1:
                    _delay = 2.0 ** _attempt
                    logger.warning(
                        f"[pipeline] planning attempt {_attempt + 1} failed — "
                        f"retrying in {_delay:.0f}s ({_attempt + 2}/{_MAX_PLANNING_RETRIES})"
                    )
                    await asyncio.sleep(_delay)
            if planning_result is None:
                logger.error(
                    f"[pipeline] planning failed after {_MAX_PLANNING_RETRIES} attempts — aborting pipeline"
                )
                return result
        workflow_plan: Optional[str] = planning_result.workflow_plan if planning_result else None
        proposed_skills: List[ProposedSkill] = planning_result.proposed_skills if planning_result else []
        result.planning_result = planning_result   # expose to callers

        # Sync active workflow plan into WorkflowView so the agent's
        # WorkflowManagerTool can inspect / edit it during the executor phase.
        if workflow_plan and session and session.workflow_view is not None:
            round_label = f"round_{session.round_count + 1}"
            session.workflow_view.edit(round_label, workflow_plan)

        # Auto-select skill_id from retrieved if not given
        if skill_id is None and retrieved_skills:
            skill_id = retrieved_skills[0].id
            logger.info(f"[pipeline] auto-selected skill_id={skill_id} from retrieval")

        # ── Phase 2: Executor (CodeAct) ───────────────────────────────────────
        if prebuilt_trace is not None:
            result.executor = PhaseResult(ok=True, elapsed_s=0.0, detail="prebuilt trace supplied")
            result.execution_trace = prebuilt_trace
            if prebuilt_trace.workflow_plan is None and workflow_plan:
                prebuilt_trace.workflow_plan = workflow_plan
        elif skip_executor:
            result.executor = PhaseResult(ok=False, elapsed_s=0.0, detail="skipped", error="no trace")
        else:
            result.executor = await self._phase_executor(
                query, retrieved_skills, skill_id, session=session, workflow_plan=workflow_plan
            )
            result.execution_trace = getattr(result.executor, "_trace", None)

        if result.execution_trace is None and not skip_executor:
            logger.warning("[pipeline] executor produced no trace — aborting")
            return result

        # ── Phase 3: Extractor (v2.1: receives proposed_skills from planner) ──
        if result.execution_trace:
            result.extractor = await self._phase_extractor(
                result.execution_trace, skill_id, proposed_skills=proposed_skills
            )
            result.new_skill_code = getattr(result.extractor, "_code", None)

        # ── Phase 4: Tester ───────────────────────────────────────────────────
        if not skip_tester and skill_id is not None and result.new_skill_code:
            # Get the latest skill_id after extractor committed a new version
            latest_id = self._get_latest_skill_id(skill_id)
            result.tester = await self._phase_tester(latest_id, result.execution_trace)
        else:
            result.tester = PhaseResult(ok=True, elapsed_s=0.0, detail="skipped")

        # ── Phase 5: Commit embedding ─────────────────────────────────────────
        if result.new_skill_code and skill_id is not None:
            result.commit = await self._phase_commit_embedding(skill_id, skill_name)
            result.new_skill_id = getattr(result.commit, "_new_id", None)
        else:
            result.commit = PhaseResult(ok=True, elapsed_s=0.0, detail="no new code to commit")

        # ── Phase 5+1: Write QueryRecord (v2 collaborative filtering data) ────
        await self._phase_save_query_record(
            query=query,
            query_embedding=_query_embedding,
            produced_skill_id=result.new_skill_id,
            produced_skill_name=skill_name,
            execution_trace=result.execution_trace,
            tester_detail=result.tester.detail if result.tester else "",
        )

        return result

    # ── Phase implementations ─────────────────────────────────────────────────

    async def _phase_retrieval(
        self, query: str, top_k: int, threshold: float
    ) -> PhaseResult:
        t0 = time.monotonic()
        try:
            retriever = CollaborativeRetriever()
            ret = await retriever.retrieve_with_collab_filter(
                query=query,
                db_manager=self.db_manager,
                top_k=top_k,
                similarity_threshold=threshold,
            )
            elapsed = time.monotonic() - t0
            collab_count = len([s for s in getattr(ret, "collab_signals", []) if s.get("source") == "collab"])
            r = PhaseResult(
                ok=True,
                elapsed_s=elapsed,
                detail=(f"{len(ret.skills)} skills retrieved  tokens={ret.embedding_tokens}"
                        f"  cost=${ret.estimated_cost_usd:.5f}  collab_signals={collab_count}"),
            )
            r._skills = ret.skills  # side-channel
            r.query_embedding = getattr(ret, "query_embedding", None)  # formal field (v2)
            r._query_embedding = r.query_embedding  # backward compat alias
            logger.info(f"[pipeline] retrieval: {r.detail}")
            return r
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error(f"[pipeline] retrieval failed: {exc}")
            r = PhaseResult(ok=False, elapsed_s=elapsed, error=str(exc))
            r._skills = []
            r._query_embedding = None
            r.query_embedding = None
            return r

    async def _phase_executor(
        self,
        query: str,
        retrieved_skills: List[Skill],
        skill_id: Optional[int],
        session: Optional[PipelineSession] = None,
        workflow_plan: Optional[str] = None,
    ) -> PhaseResult:
        t0 = time.monotonic()
        try:
            import tempfile
            import os
            from app.meta_agent.workflows.workflow_codeact_executor_testagent import (
                WorkflowExecutor,
            )
            from app.meta_agent.executors.code_executor import SandboxTool

            # Build skill module source and write to a temp file
            skill_module_code = _build_skill_module_code(retrieved_skills)
            workflow = _build_executor_workflow(query, retrieved_skills)

            # v2: inject workflow_plan hint if available
            if workflow_plan:
                workflow += textwrap.dedent(f"""

                ## Suggested Workflow Plan (Python Skeleton — for reference only)
                The following plan was designed before execution as a guide for a GENERALIZED solution.
                You may deviate from it, but prefer its structure when possible.

                ```python
                {workflow_plan}
                ```
                """).strip()

            # Write core_skills.py to a temp file so we can copy it into the container
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", prefix="core_skills_", delete=False
            ) as tf:
                tf.write(skill_module_code)
                tmp_skills_path = tf.name

            # ── Reuse or create Docker sandbox ───────────────────────────────
            reusing_session = (
                session is not None
                and session.sandbox is not None
                and session.terminal_id is not None
            )

            if reusing_session:
                sandbox = session.sandbox
                terminal_id = session.terminal_id
                logger.info(
                    f"[pipeline] executor: reusing session terminal_id={terminal_id}"
                )
                # Update core_skills.py in the running container
                await sandbox.copy_to(tmp_skills_path, "/app/core_skills.py")
                os.unlink(tmp_skills_path)
                # Reload the module so new skill versions are visible
                reload_code = textwrap.dedent("""
                    import importlib
                    import core_skills as _cs
                    importlib.reload(_cs)
                    import core_skills
                    print("[skill_pipeline] core_skills reloaded:", dir(core_skills))
                """).strip()
                await sandbox.execute(terminal_id=terminal_id, command=reload_code)
            else:
                sandbox = SandboxTool(
                    terminal_start_commands=["bash -c 'stty -echo; ipython'"],
                )
                executor = WorkflowExecutor(code_executor=sandbox)

                # Initialize container session BEFORE execute() so we can copy the file in
                await sandbox.initialize_session(image="my-python-image:latest")
                terminal_id = await sandbox.create_terminal()

                # Copy core_skills.py into the container at /app/core_skills.py
                await sandbox.copy_to(tmp_skills_path, "/app/core_skills.py")
                os.unlink(tmp_skills_path)

                # Preload: add /app to sys.path so `from core_skills import ...` works
                preload = textwrap.dedent("""
                    %autoindent False
                    import sys
                    if '/app' not in sys.path:
                        sys.path.insert(0, '/app')
                    import core_skills
                    print("[skill_pipeline] core_skills loaded:", dir(core_skills))
                """).strip()
                await sandbox.execute(terminal_id=terminal_id, command=preload)

                # Persist sandbox + terminal_id into session for future rounds
                if session is not None:
                    session.sandbox = sandbox
                    session.terminal_id = terminal_id

            # Now run the executor agent (it reuses the same sandbox/terminal)
            from app.meta_agent.workflows.workflow_codeact_executor_testagent import (
                CodeActWorkflowExecutorAgent,
            )
            from app.meta_agent_tool.tool_collection import ToolCollection

            executor_agent = CodeActWorkflowExecutorAgent(
                workflow=workflow,
                terminal_id=terminal_id,
                logger_instance=logging.getLogger("skill_executor"),
            )
            executor_agent.available_tools = ToolCollection(sandbox)

            prompt = textwrap.dedent(f"""
                Query:

                {query}

                Guideline:

                {workflow}

                terminal_id:

                {terminal_id}

                All tools imported information:

                  core_skills module — available via `from core_skills import <func>`
                  (already loaded in the IPython session at /app/core_skills.py)

                Solve the above query using the core_skills module where possible.
                When you call SandboxTool to execute code, you MUST use the given terminal_id.
            """).strip()

            await executor_agent.run(prompt)

            # The executor agent's cleanup() destroys the sandbox Docker container.
            # Reset session state so the next round creates a fresh sandbox instead
            # of trying to reuse a dead container.
            if session is not None:
                session.sandbox = None
                session.terminal_id = None

            memory = executor_agent.memory
            trace = _memory_to_agent_trace(query, memory)
            trace.workflow_plan = workflow_plan  # v2: carry plan through to Gardener

            elapsed = time.monotonic() - t0
            r = PhaseResult(
                ok=True,
                elapsed_s=elapsed,
                detail=f"executor finished  steps={len(trace.steps)}  reused_session={reusing_session}",
            )
            r._trace = trace
            logger.info(f"[pipeline] executor: {r.detail}")
            return r

        except Exception as exc:
            import traceback
            elapsed = time.monotonic() - t0
            tb = traceback.format_exc()
            logger.error(f"[pipeline] executor failed: {exc}\n{tb}")
            r = PhaseResult(ok=False, elapsed_s=elapsed, error=str(exc))
            r._trace = None
            return r

    async def _phase_extractor(
        self,
        trace: AgentTrace,
        skill_id: Optional[int],
        proposed_skills: Optional[List["ProposedSkill"]] = None,
    ) -> PhaseResult:
        t0 = time.monotonic()
        try:
            from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
            agent = SkillGardenerAgent()
            new_code = await agent.run_extraction(
                trace=trace,
                db_manager=self.db_manager,
                target_skill_id=skill_id,
                proposed_skills=proposed_skills or [],
            )
            elapsed = time.monotonic() - t0
            r = PhaseResult(
                ok=bool(new_code),
                elapsed_s=elapsed,
                detail="new code extracted" if new_code else "no code extracted",
            )
            r._code = new_code
            logger.info(f"[pipeline] extractor: {r.detail}")
            return r
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error(f"[pipeline] extractor failed: {exc}")
            r = PhaseResult(ok=False, elapsed_s=elapsed, error=str(exc))
            r._code = None
            return r

    async def _phase_tester(
        self, skill_id: int, trace: Optional[AgentTrace]
    ) -> PhaseResult:
        t0 = time.monotonic()
        try:
            from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
            agent = SkillReviewerAgent(db_manager=self.db_manager)
            report = await agent.run_review_v1(target_skill_id=skill_id, trace=trace)
            elapsed = time.monotonic() - t0
            r = PhaseResult(ok=True, elapsed_s=elapsed, detail=str(report)[:300])
            logger.info(f"[pipeline] tester: done in {elapsed:.1f}s")
            return r
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error(f"[pipeline] tester failed: {exc}")
            return PhaseResult(ok=False, elapsed_s=elapsed, error=str(exc))

    async def _phase_commit_embedding(
        self, skill_id: int, skill_name: str
    ) -> PhaseResult:
        t0 = time.monotonic()
        try:
            latest_id = self._get_latest_skill_id(skill_id)
            with Session(self.db_manager.engine) as session:
                sk = session.get(Skill, latest_id)
                if sk:
                    retriever = SkillRetriever()
                    retriever.enrich_skill_with_embedding(sk, self.db_manager)
            elapsed = time.monotonic() - t0
            r = PhaseResult(ok=True, elapsed_s=elapsed, detail=f"embedding committed for skill_id={latest_id}")
            r._new_id = latest_id
            logger.info(f"[pipeline] commit: {r.detail}")
            return r
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error(f"[pipeline] commit embedding failed: {exc}")
            r = PhaseResult(ok=False, elapsed_s=elapsed, error=str(exc))
            r._new_id = None
            return r

    def _get_latest_skill_id(self, base_skill_id: int) -> int:
        """Return the newest skill_id in the same group as base_skill_id."""
        try:
            with Session(self.db_manager.engine) as session:
                base = session.get(Skill, base_skill_id)
                if not base:
                    return base_skill_id
                latest = session.exec(
                    select(Skill)
                    .where(Skill.group_id == base.group_id)
                    .order_by(Skill.major_version.desc(), Skill.minor_version.desc())
                    .limit(1)
                ).first()
                return latest.id if latest else base_skill_id
        except Exception:
            return base_skill_id

    # ── v2: Workflow Planning Phase ───────────────────────────────────────────

    # ── v2.1: 获取历史相似查询及其技能代码 ────────────────────────────────────

    def _fetch_historical_context(
        self,
        query_embedding: Optional[List[float]],
        top_m: int = 4,
    ) -> str:
        """从 DB QueryRecord 中检索相似历史查询，返回格式化的参考文本。

        包含：历史查询文本、执行摘要、产出技能代码（如有）。
        供 planner 分析可复用片段。失败时静默返回空串。
        """
        if not query_embedding or self.db_manager is None:
            return ""
        try:
            records = self.db_manager.search_similar_queries(
                query_embedding=query_embedding,
                top_m=top_m,
                similarity_threshold=0.35,
            )
            if not records:
                return ""

            from sqlmodel import Session
            from app.meta_agent.skills.database.models import Skill as _Skill

            blocks: List[str] = []
            for rec in records:
                lines = [f'查询: "{rec.query_text[:200]}"']
                if rec.agent_summary:
                    lines.append(f"执行摘要: {rec.agent_summary[:300]}")
                # 附上产出技能的完整代码（最有价值的可复用素材）
                if rec.produced_skill_id:
                    try:
                        with Session(self.db_manager.engine) as _s:
                            sk = _s.get(_Skill, rec.produced_skill_id)
                            if sk and sk.code:
                                lines.append(f"产出技能 ({sk.docstring.splitlines()[0][:60] if sk.docstring else sk.id}):")
                                lines.append("```python")
                                lines.append(sk.code[:800])
                                lines.append("```")
                    except Exception:
                        pass
                blocks.append("\n".join(lines))

            return "\n\n---\n".join(blocks)
        except Exception as exc:
            logger.debug(f"[pipeline] _fetch_historical_context failed: {exc}")
            return ""

    @staticmethod
    def _build_planning_context(session: "PipelineSession") -> Optional[str]:
        """Extract a compact history block from session for planner context injection.

        Returns a formatted string describing the previous round, or None when
        there is no useful history (first round, or no traces recorded).
        Only the *most recent* round is injected to keep context bounded.
        """
        if not session or not session.round_results:
            return None

        last_result = session.round_results[-1]
        last_query = session.query_history[-1] if session.query_history else ""

        # Grab workflow_plan from previous trace (may be None)
        prev_plan: Optional[str] = None
        if last_result.execution_trace and last_result.execution_trace.workflow_plan:
            prev_plan = last_result.execution_trace.workflow_plan

        # Summarise tester outcome (pass/fail hint) without leaking full trace
        tester_ok = last_result.tester.ok if last_result.tester else None
        tester_hint = "✓ 测试通过" if tester_ok else ("✗ 测试未通过" if tester_ok is False else "(未测试)")

        # New skill produced?
        new_skill_note = (
            f"产出了新 skill (id={last_result.new_skill_id})"
            if last_result.new_skill_id
            else "未产出新 skill"
        )

        lines = [
            "=== 上一轮执行摘要 ===",
            f"查询: {last_query[:200]}",
            f"结果: {tester_hint} / {new_skill_note}",
        ]
        if prev_plan:
            lines += [
                "上一轮工作流计划:",
                "```python",
                prev_plan[:1200],  # hard cap: ~300 tokens
                "```",
            ]
        lines.append("====================")
        return "\n".join(lines)

    async def _run_planning_phase(
        self,
        query: str,
        retrieved_skills: List[Skill],
        session: Optional["PipelineSession"] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> Optional["PlanningResult"]:
        """v2.2: Two-phase planning.

        Phase 1 (multi-turn only): single LLM call determines mode:
          FRESH  — query changed or last round failed → re-plan from scratch.
          UPDATE — skeleton still valid, needs local edits.
          REUSE  — identical query, last round passed → return previous plan verbatim.
        Round 1 skips Phase 1 (always FRESH).

        Phase 2 (FRESH / UPDATE): full planning call.
          proposed_skills are ALWAYS extracted when the section is present
          (removed the erroneous patch_mode gate that kept them permanently dead).

        Returns None on failure; caller applies application-level retry (ARCH-BUG-3).
        """
        try:
            from app.llm import LLM
            import ast
            import re

            # ── 已有技能：展示简介 + 完整代码（最多前 600 chars） ────────────
            skill_parts: List[str] = []
            for sk in retrieved_skills:
                first_line = (sk.docstring or "").splitlines()[0][:60] if sk.docstring else f"skill_id={sk.id}"
                code_snippet = (sk.code or "")[:600]
                skill_parts.append(
                    f"  [{first_line}]\n```python\n{code_snippet}\n```"
                )
            skill_block = "\n\n".join(skill_parts) if skill_parts else "  (no skills retrieved — implement from scratch)"

            # ── 跨会话历史参考 ────────────────────────────────────────────────
            hist_context = self._fetch_historical_context(query_embedding)
            hist_section = (
                f"\n## 历史相似查询参考\n"
                f"以下是历史上相似查询的解决方案（含产出技能代码），请仔细阅读，"
                f"思考其中哪些逻辑片段可以抽象为通用技能供当前查询复用：\n\n"
                f"{hist_context}\n"
            ) if hist_context else ""

            # ── 会话内历史（多轮） ────────────────────────────────────────────
            history_block = self._build_planning_context(session)
            is_multi_turn = history_block is not None

            # ══ Phase 1: Mode determination (only for multi-turn) ══════════════
            planner_mode: str = "FRESH"  # round 1 default
            if is_multi_turn:
                mode_system = (
                    "你是工作流规划模式分类器。根据当前查询与上一轮执行历史，判断最合适的规划模式：\n\n"
                    "- **FRESH** — 查询与上轮本质不同，或上轮测试未通过，需从头规划。\n"
                    "- **UPDATE** — 上一轮计划的整体骨架仍适用，但需局部修改（例如新增子任务或参数调整）。\n"
                    "- **REUSE** — 查询完全相同且上一轮测试通过，可直接复用上一轮计划。\n\n"
                    "判断原则：上一轮测试未通过 → 优先 FRESH；查询仅扩展子任务 → UPDATE；完全相同且测试通过 → REUSE。\n\n"
                    "严格按以下格式输出（两行，不输出其他内容）：\n"
                    "## 模式\n"
                    "<FRESH|UPDATE|REUSE>"
                )
                mode_user = (
                    f"{history_block}\n\n"
                    f"## 当前查询\n{query}\n\n"
                    "请判断规划模式："
                )
                llm_mode = LLM(config_name="tool_maker")
                mode_resp = await llm_mode.ask(
                    messages=[{"role": "user", "content": mode_user}],
                    system_msgs=[{"role": "system", "content": mode_system}],
                )
                if mode_resp:
                    m = re.search(r"\b(FRESH|UPDATE|REUSE)\b", mode_resp, re.IGNORECASE)
                    if m:
                        planner_mode = m.group(1).upper()
                        logger.info(f"[pipeline] planning phase 1: mode={planner_mode}")
                    else:
                        logger.warning("[pipeline] planning phase 1: unrecognized mode response — defaulting to FRESH")

            # ══ REUSE path: skip Phase 2, return previous plan verbatim ════════
            if planner_mode == "REUSE":
                if session and session.round_results:
                    prev_trace = session.round_results[-1].execution_trace
                    if prev_trace and prev_trace.workflow_plan:
                        logger.info("[pipeline] planning phase: REUSE — returning previous plan verbatim")
                        return PlanningResult(workflow_plan=prev_trace.workflow_plan)
                # No valid previous plan — fall back to FRESH
                logger.warning("[pipeline] planning phase: REUSE requested but no previous plan — falling back to FRESH")
                planner_mode = "FRESH"

            # ══ Phase 2: Full planning (FRESH or UPDATE) ════════════════════════
            mode_hint = (
                "\n## 当前规划模式\n"
                f"**{planner_mode}**\n"
                + (
                    "上一轮计划骨架仍然适用，请在此基础上做最小改动，输出修改后的完整两段格式。\n"
                    if planner_mode == "UPDATE"
                    else "从头规划，不受上一轮计划限制。\n"
                )
            ) if is_multi_turn else ""

            system_prompt = (
                "你是一个工作流规划师，同时承担部分技能抽取职责。\n\n"
                "## 任务\n"
                "1. 阅读历史相似查询的解决经验（如有），识别可以抽象复用的代码片段（不必是完整技能，片段和思路均可）。\n"
                "2. 将识别到的复用片段提炼为候选技能（函数骨架）。\n"
                "3. 规划当前查询的工作流，优先使用已有技能和候选技能。\n\n"
                "## 输出格式（严格遵守）\n"
                "输出分为两段，用 `## 候选技能提取` 和 `## 工作流计划` 作为分隔标题。\n\n"
                "**段1：候选技能提取**（若无可复用片段可留空，但标题必须保留）\n"
                "每个候选技能格式：\n"
                "### 函数名\n"
                "描述: 一句话说明功能\n"
                "来源查询: \"历史查询文本片段\"\n"
                "```python\n"
                "def 函数名(PARAM1, PARAM2=DEFAULT):\n"
                "    # 核心逻辑骨架\n"
                "    return result\n"
                "```\n\n"
                "**段2：工作流计划**\n"
                "使用已有 core_skills + 候选技能规划工作流（候选技能加注释 `# TODO: Gardener 待创建`）。\n"
                "代码不超过 20 行，给 high-level 骨架，CAPS_VARIABLE 占位可变输入，`return_(result)` 返回结果。\n\n"
                "## 示例输出\n"
                "## 候选技能提取\n"
                "### compute_at_risk_stats\n"
                "描述: 计算不及格学生列表及统计摘要\n"
                "来源查询: \"分析班级成绩，找出不及格学生\"\n"
                "```python\n"
                "def compute_at_risk_stats(scores, threshold=60):\n"
                "    at_risk = [s for s in scores if s < threshold]\n"
                "    return {'at_risk': at_risk, 'mean': sum(scores)/len(scores)}\n"
                "```\n\n"
                "## 工作流计划\n"
                "```python\n"
                "from core_skills import generate_learning_discussion\n"
                "stats = compute_at_risk_stats(SCORES)  # TODO: Gardener 待创建\n"
                "advice = [generate_learning_discussion(score=s) for s in stats['at_risk']]\n"
                "return_({'stats': stats, 'advice': advice})\n"
                "```"
                + mode_hint
            )

            # ── User message ────────────────────────────────────────────────
            history_section = f"{history_block}\n\n" if history_block else ""
            user_msg = (
                f"{history_section}"
                f"{hist_section}"
                f"## 当前查询\n{query}\n\n"
                f"## 已有技能（已通过 `from core_skills import ...` 预导入）\n{skill_block}\n\n"
                "请按格式输出（## 候选技能提取 + ## 工作流计划）："
            )

            llm = LLM(config_name="tool_maker")
            response = await llm.ask(
                messages=[{"role": "user", "content": user_msg}],
                system_msgs=[{"role": "system", "content": system_prompt}],
            )

            if not response or not response.strip():
                return None

            # ── 解析结构化输出 ─────────────────────────────────────────────
            proposed_skills: List[ProposedSkill] = []
            workflow_plan: str = ""

            # 分割两段
            if "## 工作流计划" in response:
                parts = response.split("## 工作流计划", 1)
                skills_section = parts[0]
                plan_section = parts[1]
            else:
                skills_section = ""
                plan_section = response

            # 候选技能提取 — ALWAYS extract when section is present
            # (removed erroneous patch_mode gate: previously all proposed_skills were dead code)
            if "## 候选技能提取" in skills_section:
                skills_text = skills_section.split("## 候选技能提取", 1)[1]
                skill_blocks = re.split(r"\n###\s+", skills_text)
                for blk in skill_blocks:
                    blk = blk.strip()
                    if not blk:
                        continue
                    lines_blk = blk.splitlines()
                    name = lines_blk[0].strip()
                    if not name or " " in name.split("(")[0]:
                        continue  # 跳过非合法函数名
                    desc = ""
                    source_q = ""
                    for line in lines_blk[1:]:
                        if line.startswith("描述:"):
                            desc = line[len("描述:"):].strip()
                        elif line.startswith("来源查询:"):
                            source_q = line[len("来源查询:"):].strip().strip('"\'')
                    m = re.search(r"```python\s*(.*?)\s*```", blk, re.DOTALL)
                    code_frag = m.group(1).strip() if m else ""
                    if name and code_frag:
                        proposed_skills.append(ProposedSkill(
                            name=name.split("(")[0].strip(),
                            description=desc,
                            code_fragment=code_frag,
                            source_query=source_q,
                        ))

            # 解析工作流计划
            if "```python" in plan_section:
                workflow_plan = plan_section.split("```python")[1].split("```")[0].strip()
            elif "```" in plan_section:
                workflow_plan = plan_section.split("```")[1].split("```")[0].strip()
            else:
                workflow_plan = plan_section.strip()

            # 语法检查（非阻塞）
            try:
                ast.parse(workflow_plan)
            except SyntaxError:
                logger.warning("[pipeline] planning phase: syntax error in workflow plan, using as-is")

            logger.info(
                f"[pipeline] planning phase 2 ({planner_mode}): plan={len(workflow_plan)}chars "
                f"proposed_skills={len(proposed_skills)}"
            )
            return PlanningResult(workflow_plan=workflow_plan, proposed_skills=proposed_skills)

        except Exception as exc:
            logger.warning(f"[pipeline] planning phase failed: {exc}")
            return None

    # ── v2: Save QueryRecord for collaborative filtering ──────────────────────

    async def _phase_save_query_record(
        self,
        query: str,
        query_embedding: Optional[List[float]],
        produced_skill_id: Optional[int],
        produced_skill_name: Optional[str],
        execution_trace,
        tester_detail: str,
    ) -> None:
        """Write a QueryRecord after each pipeline run (v2 collaborative filtering data).

        Fully non-blocking: logs errors but never raises.
        """
        try:
            # Extract agent summary from executor trace.
            # User-selected strategy: prefer the last step's thought/tool_output synthesis.
            agent_summary = ""
            if execution_trace and execution_trace.steps:
                last_step = execution_trace.steps[-1]
                merged = "\n".join(
                    x for x in [last_step.thought or "", last_step.tool_output or ""] if x
                ).strip()
                agent_summary = merged[:500]
            elif execution_trace and execution_trace.final_answer:
                agent_summary = execution_trace.final_answer[:500]

            self.db_manager.save_query_record(
                query_text=query,
                query_embedding=query_embedding,
                produced_skill_id=produced_skill_id,
                produced_skill_name=produced_skill_name,
                agent_summary=agent_summary,
                remarks=tester_detail[:300] if tester_detail else "",
            )
            logger.info(f"[pipeline] QueryRecord saved for query={query[:60]!r}")
        except Exception as exc:
            logger.warning(f"[pipeline] save_query_record failed (non-blocking): {exc}")

