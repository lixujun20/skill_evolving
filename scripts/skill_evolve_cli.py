#!/usr/bin/env python3
"""
skill_evolve_cli.py — Interactive CLI for the skill_evolving_v1 pipeline.

Usage:
  python3 scripts/skill_evolve_cli.py --query "student transcript as JSON"
  python3 scripts/skill_evolve_cli.py --query "..." --trace path/to/trace.json
  python3 scripts/skill_evolve_cli.py --query "..." --skill-id 3
  python3 scripts/skill_evolve_cli.py --demo dim_1_1
  python3 scripts/skill_evolve_cli.py --list-demos
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import click
from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

# ── env setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONPATH", str(Path(__file__).resolve().parent.parent))

console = Console()

# ── Rich logging handler ─────────────────────────────────────────────────────

_PHASE_COLORS = {
    "skill_gardener": "green",
    "skill_reviewer": "yellow",
    "retrieval": "cyan",
}

_THOUGHT_ICONS = {
    "✨": ("italic", "white"),
    "🛠️": ("bold", "bright_blue"),
    "🧰": ("bold", "bright_cyan"),
    "🔧": ("dim", "cyan"),
    "🚨": ("bold", "red"),
    "✅": ("bold", "green"),
    "❌": ("bold", "red"),
    "🤔": ("dim", "yellow"),
}


class RichAgentLogHandler(logging.Handler):
    """Routes agent logger messages to the Rich console with color + icons.

    If `full_output=True` is passed, the handler will not truncate long LLM/tool
    output messages. Otherwise it will truncate long dumps to keep terminal
    readable.
    """

    def __init__(self, phase_color: str = "white", full_output: bool = False) -> None:
        super().__init__()
        self.phase_color = phase_color
        self._step = 0
        self.full_output = full_output

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()

        # Detect icon prefix
        style = "dim white"
        for icon, (font_style, color) in _THOUGHT_ICONS.items():
            if icon in msg:
                style = f"{font_style} {color}"
                break

        # Only truncate when full_output is False
        if not getattr(self, "full_output", False):
            # Truncate very long tool argument dumps to keep output readable
            if "🔧 Tool arguments:" in msg and len(msg) > 400:
                msg = msg[:397] + "…"

            # Truncate tool result outputs
            if len(msg) > 800 and "🛠️" not in msg and "✨" not in msg:
                msg = msg[:797] + "…"

        indent = "  " if "🔧" in msg or "🧰" in msg else ""
        console.print(f"  {indent}[{style}]{msg}[/]")


def _attach_rich_handler(logger_name: str, color: str, full_output: bool = False) -> RichAgentLogHandler:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    h = RichAgentLogHandler(phase_color=color, full_output=full_output)
    h.setLevel(logging.DEBUG)
    # Avoid adding duplicates
    for existing in logger.handlers:
        if isinstance(existing, RichAgentLogHandler):
            logger.removeHandler(existing)
    logger.addHandler(h)
    # Don't propagate to root (avoids double-printing)
    logger.propagate = False
    return h


# ── Demo traces registry ─────────────────────────────────────────────────────

DEMO_REGISTRY: dict[str, tuple[str, str]] = {
    "dim_1_1": (
        "app.meta_agent.skills.tests.test_data_dim_1",
        "TRACE_DIM_1_1",
    ),
    "dim_1_2": (
        "app.meta_agent.skills.tests.test_data_dim_1",
        "TRACE_DIM_1_2",
    ),
    "int_A": (
        "app.meta_agent.skills.tests.test_data_integration",
        "TRACE_INT_A_1",
    ),
    "int_B": (
        "app.meta_agent.skills.tests.test_data_integration",
        "TRACE_INT_B_1",
    ),
    "int_C": (
        "app.meta_agent.skills.tests.test_data_integration",
        "TRACE_INT_C_1",
    ),
}

DEMO_SKILL_CODES: dict[str, tuple[str, str]] = {
    "dim_1_1": (
        "app.meta_agent.skills.tests.test_data_dim_1",
        "SKILL_CODE_1_1_V1_0",
    ),
    "int_A": (
        "app.meta_agent.skills.tests.test_data_integration",
        "SKILL_CODE_INT_A_V1_0",
    ),
    "int_B": (
        "app.meta_agent.skills.tests.test_data_integration",
        "SKILL_CODE_INT_B_V1_0",
    ),
    "int_C": (
        "app.meta_agent.skills.tests.test_data_integration",
        "SKILL_CODE_INT_C_V1_0",
    ),
}


def _load_demo(name: str):
    """Return (AgentTrace, optional_skill_code_str) for a registered demo."""
    if name not in DEMO_REGISTRY:
        console.print(f"[red]Unknown demo '{name}'. Use --list-demos to see available demos.[/]")
        sys.exit(1)
    import importlib
    mod_name, attr = DEMO_REGISTRY[name]
    mod = importlib.import_module(mod_name)
    trace = getattr(mod, attr)
    skill_code = None
    if name in DEMO_SKILL_CODES:
        smod_name, sattr = DEMO_SKILL_CODES[name]
        smod = importlib.import_module(smod_name)
        skill_code = getattr(smod, sattr, None)
    return trace, skill_code


# ── Display helpers ──────────────────────────────────────────────────────────

def _phase_header(title: str, color: str, icon: str = "⚙") -> None:
    console.print()
    console.print(Rule(f"[bold {color}]{icon}  {title}[/]", style=color))
    console.print()


def _show_trace_summary(trace) -> None:
    """Print a compact trace overview table."""
    table = Table(title="AgentTrace Summary", border_style="dim", show_header=True, header_style="bold cyan")
    table.add_column("Field", style="cyan", width=16)
    table.add_column("Value", overflow="fold")
    table.add_row("Query", trace.query)
    table.add_row("Format", trace.trace_format.value)
    table.add_row("Steps", str(len(trace.steps)))
    for i, step in enumerate(trace.steps):
        step_text = step.thought[:120] + ("…" if len(step.thought) > 120 else "")
        status_icon = "✅" if step.status == "success" else "❌"
        table.add_row(
            f"  Step {i + 1} {status_icon}",
            step_text,
            style="dim" if step.status != "success" else "",
        )
    console.print(table)
    console.print()


def _show_retrieval_results(result) -> None:
    """Display retrieval results as a table."""
    if not result.skills:
        console.print("  [dim]No matching skills found in DB (embedding search returned empty).[/]")
        return

    table = Table(border_style="cyan", show_header=True, header_style="bold cyan")
    table.add_column("ID", width=5)
    table.add_column("Group", width=24)
    table.add_column("Version", width=9)
    table.add_column("Docstring", overflow="fold")
    for sk in result.skills:
        table.add_row(
            str(sk.id),
            str(sk.group_id),
            f"v{sk.major_version}.{sk.minor_version}",
            (sk.docstring or "")[:80],
        )
    console.print(table)
    console.print(
        f"  [dim]Elapsed: {result.elapsed_ms:.0f} ms  |  "
        f"Tokens: {result.embedding_tokens}  |  "
        f"Cost: ${result.estimated_cost_usd:.5f}[/]"
    )
    console.print()


def _show_skill_code(code: str, title: str = "Skill Code") -> None:
    syntax = Syntax(code.strip(), "python", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title=f"[bold]{title}[/]", border_style="dim green"))


def _show_test_report(report_text: str) -> None:
    lines = report_text.strip().splitlines()
    table = Table(title="Test Report", border_style="yellow", show_header=False)
    table.add_column("", overflow="fold")
    for line in lines[:60]:
        style = "green" if "PASS" in line.upper() else ("red" if "FAIL" in line.upper() else "")
        table.add_row(line, style=style)
    if len(lines) > 60:
        table.add_row(f"… ({len(lines) - 60} more lines)")
    console.print(table)


def _show_summary(
    query: str,
    retrieval_ms: Optional[float],
    extractor_ms: Optional[float],
    tester_ms: Optional[float],
    new_code: Optional[str],
    test_report: Optional[str],
) -> None:
    console.print()
    console.print(Rule("[bold white]Pipeline Summary[/]", style="white"))
    console.print()

    # Timing table
    timing = Table(show_header=True, header_style="bold", border_style="dim")
    timing.add_column("Phase", style="bold")
    timing.add_column("Duration", justify="right")
    timing.add_column("Status", justify="center")
    if retrieval_ms is not None:
        timing.add_row("Retrieval", f"{retrieval_ms:.1f}s", "[cyan]✓[/]")
    if extractor_ms is not None:
        timing.add_row("Extractor", f"{extractor_ms:.1f}s", "[green]✓[/]" if new_code else "[red]✗[/]")
    if tester_ms is not None:
        timing.add_row("Tester", f"{tester_ms:.1f}s", "[yellow]✓[/]" if test_report else "[red]✗[/]")
    console.print(timing)
    console.print()

    if new_code:
        _show_skill_code(new_code, title="🌱 Extracted Skill (new version)")
    else:
        console.print(Panel("[red]Extractor produced no new skill code.[/]", border_style="red"))

    if test_report:
        _show_test_report(test_report)

    console.print()
    console.print(Panel(
        f"[bold]Query:[/] {query}\n"
        f"[bold]Extractor:[/] {'✅ produced new code' if new_code else '❌ no output'}\n"
        f"[bold]Tester:[/] {'✅ ran' if test_report else '⏭  skipped'}",
        title="[bold white]Done[/]",
        border_style="white",
    ))


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db(db_url: str):
    from sqlalchemy import text
    from sqlmodel import SQLModel
    from app.meta_agent.skills.database.manager import SkillDatabaseManager
    manager = SkillDatabaseManager(db_url)
    with manager.engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    SQLModel.metadata.create_all(manager.engine)
    return manager


# ── Pipeline helpers ──────────────────────────────────────────────────────────

async def _run_retrieval(query: str, db_manager, top_k: int, threshold: float):
    from app.meta_agent.skills.retrieval import SkillRetriever
    retriever = SkillRetriever()
    return await retriever.retrieve_for_query(
        query=query,
        db_manager=db_manager,
        top_k=top_k,
        similarity_threshold=threshold,
    )


async def _run_extractor(trace, db_manager, skill_id: Optional[int]):
    from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
    agent = SkillGardenerAgent()
    return await agent.run_extraction(trace=trace, db_manager=db_manager, target_skill_id=skill_id)


async def _run_tester(skill_id: int, trace, db_manager):
    from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
    agent = SkillReviewerAgent(db=db_manager)
    return await agent.run_review_v1(target_skill_id=skill_id, trace=trace)


# ── Interactive REPL loop ────────────────────────────────────────────────────

_REPL_HELP = """
[bold]Interactive commands[/]
  [cyan]<query>[/]           — run one round of the full pipeline
  [cyan]history[/]           — show query history for this session
  [cyan]show-skill[/]        — display latest evolved skill code
  [cyan]session[/]           — print session summary
  [cyan]help[/]              — show this message
  [cyan]quit[/] / [cyan]exit[/]       — close Docker terminal and exit
""".strip()


async def _interactive_loop(
    db_url: str,
    skill_name: str,
    top_k: int,
    threshold: float,
    no_tester: bool,
    full_output: bool,
    log_file: Optional[str],
) -> None:
    """REPL loop: create one PipelineSession and run multiple rounds through it."""
    from app.meta_agent.skills.pipeline import SkillEvolvingPipeline, PipelineSession, _get_db

    session = PipelineSession.create(db_url)
    pipeline = SkillEvolvingPipeline(db_url=db_url)

    console.print(Panel(
        f"[bold white]skill_evolving_v1[/]  [dim]•[/]  Interactive Multi-Turn Session\n"
        f"[dim]session_id=[cyan]{session.session_id}[/]  Docker terminal shared across rounds[/]\n\n"
        + _REPL_HELP,
        border_style="bright_white",
        title="[bold]Interactive Mode[/]",
    ))
    console.print()

    last_skill_id: Optional[int] = None
    last_skill_code: Optional[str] = None

    try:
        while True:
            # ── Prompt ──────────────────────────────────────────────────────
            round_num = session.round_count + 1
            try:
                raw = console.input(
                    f"[bold green]round {round_num}[/] [dim]({session.session_id})[/] [bold]>[/] "
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Interrupted.[/]")
                break

            cmd = raw.strip()
            if not cmd:
                continue

            # ── Built-in commands ────────────────────────────────────────────
            if cmd.lower() in ("quit", "exit", "q"):
                break

            if cmd.lower() == "help":
                console.print(_REPL_HELP)
                continue

            if cmd.lower() == "history":
                if not session.query_history:
                    console.print("[dim]No queries yet.[/]")
                else:
                    for i, q in enumerate(session.query_history):
                        console.print(f"  [cyan]{i+1}.[/] {q}")
                continue

            if cmd.lower() == "session":
                console.print(f"[dim]{session.summary()}[/]")
                continue

            if cmd.lower() == "show-skill":
                if last_skill_code:
                    _show_skill_code(last_skill_code, "Latest Evolved Skill")
                else:
                    console.print("[dim]No skill evolved yet this session.[/]")
                continue

            # ── Run one pipeline round ───────────────────────────────────────
            query = cmd
            console.print()
            console.print(Rule(
                f"[bold white]Round {round_num} · {query[:60]}[/]",
                style="dim white",
            ))
            console.print()

            t0 = time.monotonic()
            result = await pipeline.run_with_session(
                session=session,
                query=query,
                skill_name=skill_name,
                skill_id=last_skill_id,    # carry evolved skill across rounds
                top_k=top_k,
                similarity_threshold=threshold,
                skip_tester=no_tester,
            )
            elapsed = time.monotonic() - t0

            # Update last_skill_id if a new version was created
            if result.new_skill_id:
                last_skill_id = result.new_skill_id
            if result.new_skill_code:
                last_skill_code = result.new_skill_code

            # Quick per-round summary
            console.print()
            console.print(Panel(
                f"[bold]Round {round_num} done[/]  ({elapsed:.1f}s)\n"
                f"  Extractor: {'✅ new code' if result.new_skill_code else '❌ no output'}\n"
                f"  Tester:    {'✅ ran' if result.tester.ok and not result.tester.detail.startswith('skip') else '⏭  skipped'}\n"
                f"  New skill_id: {result.new_skill_id or '(none)'}\n"
                f"  Terminal: {'♻️  reused' if session.terminal_id else '🆕  created'}",
                border_style="dim white",
                title=f"[dim]Round {round_num} Summary[/]",
            ))
            console.print()

    finally:
        console.print("[dim]Closing Docker terminal...[/]")
        await session.close()
        console.print(f"[dim]Session {session.session_id} closed.  "
                      f"Total rounds: {session.round_count}[/]")


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--query", "-q", default=None, help="Free-text query for skill retrieval + agent context.")
@click.option("--trace", "-t", "trace_path", default=None, type=click.Path(exists=True),
              help="Path to AgentTrace JSON file. If omitted, uses demo or prompts.")
@click.option("--skill-id", "-s", default=None, type=int,
              help="Target Skill DB ID to evolve. Skips retrieval selection step.")
@click.option("--demo", "-d", default=None,
              help="Use a built-in demo trace. See --list-demos.")
@click.option("--list-demos", is_flag=True, default=False,
              help="Print available demo trace names and exit.")
@click.option("--interactive", "-i", is_flag=True, default=False,
              help="Enter interactive REPL mode: one Docker session, multiple queries.")
@click.option("--top-k", default=5, show_default=True, help="Max skills to retrieve.")
@click.option("--threshold", default=0.3, show_default=True, help="Similarity threshold (0-1).")
@click.option("--no-tester", is_flag=True, default=False, help="Skip the reviewer/tester phase.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Only run retrieval; do not invoke extractor or tester.")
@click.option("--db-url", default=None, envvar="SKILL_DB_URL",
              help="PostgreSQL DB URL. Defaults to aicosmos_test.")
@click.option("--log-file", default=None, type=click.Path(),
              help="Also write LLM trace to this file (sets LLM_TRACE_FILE).")
@click.option("--full-output", is_flag=True, default=False,
              help="Show full LLM/tool outputs without truncation in the CLI.")
def cli(
    query, trace_path, skill_id, demo, list_demos, interactive, top_k, threshold,
    no_tester, dry_run, db_url, log_file, full_output,
):
    """
    \b
    ╔══════════════════════════════════════╗
    ║   skill_evolving_v1  •  CLI Runner   ║
    ╚══════════════════════════════════════╝

    End-to-end pipeline: Retrieval → Extractor → Tester

    Single-shot:   --query "..." [--demo name | --trace path]
    Interactive:   --interactive  (REPL, Docker terminal shared across rounds)
    """
    asyncio.run(_async_main(
        query=query,
        trace_path=trace_path,
        skill_id=skill_id,
        demo=demo,
        list_demos=list_demos,
        interactive=interactive,
        top_k=top_k,
        threshold=threshold,
        no_tester=no_tester,
        dry_run=dry_run,
        db_url=db_url,
        log_file=log_file,
        full_output=full_output,
    ))


async def _async_main(
    query, trace_path, skill_id, demo, list_demos, interactive, top_k, threshold,
    no_tester, dry_run, db_url, log_file, full_output,
):
    # ── list-demos shortcut ────────────────────────────────────────────────
    if list_demos:
        console.print(Panel(
            "\n".join(f"  [cyan]{k}[/]" for k in DEMO_REGISTRY),
            title="[bold]Available Demo Traces[/]",
            border_style="cyan",
        ))
        return

    # ── LLM trace file ─────────────────────────────────────────────────────
    if log_file:
        os.environ["LLM_TRACE_FILE"] = log_file
        console.print(f"[dim]LLM trace → {log_file}[/]")

    # ── Attach rich log handlers ───────────────────────────────────────────
    _attach_rich_handler("skill_gardener", "green", full_output=full_output)
    _attach_rich_handler("skill_reviewer", "yellow", full_output=full_output)
    _attach_rich_handler("skill_pipeline", "magenta", full_output=full_output)

    # ── Resolve DB URL ─────────────────────────────────────────────────────
    default_db = (
        "postgresql+psycopg2://edumanus_user:edumanus_password"
        "@localhost:15432/aicosmos_test"
    )
    resolved_db_url = db_url or default_db

    # ── Interactive REPL mode ──────────────────────────────────────────────
    if interactive:
        skill_name = "interactive_skill"
        await _interactive_loop(
            db_url=resolved_db_url,
            skill_name=skill_name,
            top_k=top_k,
            threshold=threshold,
            no_tester=no_tester,
            full_output=full_output,
            log_file=log_file,
        )
        return

    # ── Banner ─────────────────────────────────────────────────────────────
    console.print(Panel(
        "[bold white]skill_evolving_v1[/]  [dim]•[/]  "
        "Retrieval → Executor (CodeAct) → Extractor → Tester → Commit",
        border_style="bright_white",
    ))

    prebuilt_trace = None
    seed_skill_code = None
    skill_name = "cli_skill"

    if demo:
        prebuilt_trace, seed_skill_code = _load_demo(demo)
        if not query:
            query = prebuilt_trace.query
        skill_name = demo.replace("_", "-")
        console.print(f"[dim]Demo: [cyan]{demo}[/]  Query: {query}[/]")
        if seed_skill_code:
            console.print(Panel(
                Syntax(seed_skill_code.strip(), "python", theme="monokai", line_numbers=True),
                title="[dim]Seed Skill Code (v1.0)[/]",
                border_style="dim",
            ))
        if prebuilt_trace:
            _phase_header("Trace Overview", "white", "📋")
            _show_trace_summary(prebuilt_trace)

    elif trace_path:
        from app.meta_agent.skills.schemas import AgentTrace as _AgentTrace
        with open(trace_path) as f:
            prebuilt_trace = _AgentTrace.model_validate_json(f.read())
        if not query:
            query = prebuilt_trace.query
        console.print(f"[dim]Trace loaded from: {trace_path}[/]")
        _phase_header("Trace Overview", "white", "📋")
        _show_trace_summary(prebuilt_trace)

    elif not query:
        query = click.prompt("Enter query", default="Retrieve student transcript as JSON")

    if not query:
        console.print("[red]No query provided.[/]")
        sys.exit(1)

    # ── Run pipeline ───────────────────────────────────────────────────────
    from app.meta_agent.skills.pipeline import SkillEvolvingPipeline, _ensure_seed_skill, _get_db

    pipeline = SkillEvolvingPipeline(db_url=resolved_db_url)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 1: Retrieval
    # ═══════════════════════════════════════════════════════════════════════
    _phase_header("Phase 1 · Retrieval", "cyan", "🔍")
    console.print(f"  [cyan]Query:[/] {query}")
    console.print(f"  [dim]top_k={top_k}  threshold={threshold}[/]")
    console.print()

    # Seed if demo
    if seed_skill_code and skill_id is None:
        db_mgr = _get_db(resolved_db_url)
        seeded = _ensure_seed_skill(db_mgr, skill_name, seed_skill_code)
        skill_id = seeded.id
        console.print(f"[dim]Demo skill seeded in DB — skill_id={skill_id}[/]")
    else:
        db_mgr = _get_db(resolved_db_url)

    # Run retrieval standalone for interactive display
    t0 = time.monotonic()
    retrieval_result = await _run_retrieval(query, db_mgr, top_k, threshold)
    retrieval_ms = time.monotonic() - t0
    _show_retrieval_results(retrieval_result)

    # Interactive skill selection (if no --skill-id and no auto-demo)
    if skill_id is None and retrieval_result.skills and not dry_run:
        console.print("  [bold]Select a skill to evolve[/] (or press Enter to create new):")
        for sk in retrieval_result.skills:
            console.print(
                f"    [cyan]{sk.id}[/]  v{sk.major_version}.{sk.minor_version}  "
                f"{(sk.docstring or '')[:60]}"
            )
        raw = click.prompt("  Skill ID (blank = create new)", default="", show_default=False)
        if raw.strip().isdigit():
            skill_id = int(raw.strip())
            console.print(f"  [dim]→ Targeting skill_id={skill_id}[/]")

    if dry_run:
        console.print("[dim]--dry-run: stopping after retrieval.[/]")
        return

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2: Executor (CodeAct via Docker) OR skip with prebuilt trace
    # ═══════════════════════════════════════════════════════════════════════
    if prebuilt_trace is not None:
        console.print()
        console.print(Rule("[bold magenta]⚡  Phase 2 · Executor[/]  [dim](using prebuilt trace)[/]", style="magenta"))
        console.print("  [dim]Skipping live Docker execution — prebuilt trace supplied.[/]")
        executor_trace = prebuilt_trace
        executor_ms = 0.0
        executor_ok = True
    else:
        _phase_header("Phase 2 · Executor (CodeAct)", "magenta", "⚡")
        console.print(f"  [dim]Starting Docker IPython session for query...[/]")
        console.print()
        t1 = time.monotonic()
        exec_phase = await pipeline._phase_executor(query, retrieval_result.skills, skill_id, session=None)
        executor_ms = time.monotonic() - t1
        executor_trace = getattr(exec_phase, "_trace", None)
        executor_ok = exec_phase.ok
        if executor_ok:
            console.print(f"  [magenta]✅ Executor done in {executor_ms:.1f}s — {exec_phase.detail}[/]")
        else:
            console.print(f"  [red]❌ Executor failed: {exec_phase.error}[/]")
        if executor_trace:
            _show_trace_summary(executor_trace)

    if executor_trace is None:
        console.print("[red]No execution trace available — aborting.[/]")
        return

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 3: Extractor (Gardener)
    # ═══════════════════════════════════════════════════════════════════════
    _phase_header("Phase 3 · Extractor (Gardener)", "green", "🌱")
    console.print(
        f"  [dim]target skill_id=[cyan]{skill_id or 'new'}[/]  "
        f"trace steps={len(executor_trace.steps)}[/]"
    )
    console.print()

    t2 = time.monotonic()
    new_code = await _run_extractor(executor_trace, db_mgr, skill_id)
    extractor_ms = time.monotonic() - t2
    console.print()
    if new_code:
        console.print(f"  [green]✅ Extractor completed in {extractor_ms:.1f}s — new skill code generated.[/]")
    else:
        console.print(f"  [red]❌ Extractor finished in {extractor_ms:.1f}s but produced no new code.[/]")

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 4: Tester (Reviewer)
    # ═══════════════════════════════════════════════════════════════════════
    test_report = None
    tester_ms = None

    if no_tester:
        console.print()
        console.print("[dim]--no-tester: skipping reviewer phase.[/]")
    elif skill_id is not None:
        # Get latest skill id after extractor may have created a new version
        _p = SkillEvolvingPipeline(db_url=resolved_db_url)
        _p.db_manager = db_mgr
        _review_id = _p._get_latest_skill_id(skill_id)
        _phase_header("Phase 4 · Tester (Reviewer)", "yellow", "🧪")
        console.print(f"  [dim]reviewing latest skill_id={_review_id}[/]")
        console.print()
        t3 = time.monotonic()
        try:
            test_report = await _run_tester(_review_id, executor_trace, db_mgr)
            tester_ms = time.monotonic() - t3
            console.print()
            console.print(f"  [yellow]✅ Tester completed in {tester_ms:.1f}s.[/]")
        except Exception as exc:
            tester_ms = time.monotonic() - t3
            console.print(f"  [red]❌ Tester raised an exception: {exc}[/]")
    else:
        console.print()
        console.print("[dim]Tester skipped — no skill_id.[/]")

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 5: Commit embedding
    # ═══════════════════════════════════════════════════════════════════════
    if new_code and skill_id is not None:
        _phase_header("Phase 5 · Commit Embedding", "dim cyan", "💾")
        _commit_pipeline = SkillEvolvingPipeline(db_url=resolved_db_url)
        _commit_pipeline.db_manager = db_mgr
        commit_phase = await _commit_pipeline._phase_commit_embedding(skill_id, skill_name)
        if commit_phase.ok:
            console.print(f"  [cyan]✅ {commit_phase.detail}[/]")
        else:
            console.print(f"  [red]❌ Embedding commit failed: {commit_phase.error}[/]")

    # ═══════════════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════════════
    _show_summary(
        query=query,
        retrieval_ms=retrieval_ms,
        extractor_ms=extractor_ms,
        tester_ms=tester_ms,
        new_code=new_code,
        test_report=str(test_report) if test_report else None,
    )


if __name__ == "__main__":
    cli()
