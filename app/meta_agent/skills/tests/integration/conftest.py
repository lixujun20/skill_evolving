"""
Integration test conftest — multi-round HTML report plugin.

Hooks into pytest to capture per-phase pipeline results and generate
a rich HTML report at the end of each integration test session.

Report is written to: ~/skill_evolving/logs/test_results/amu_{timestamp}.html
"""
from __future__ import annotations

import html
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPORT_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / "logs" / "test_results"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PhaseEntry:
    name: str
    ok: bool
    elapsed_s: float
    detail: str = ""
    error: str = ""


@dataclass
class RoundEntry:
    round_num: int
    query: str
    group: str = ""
    phases: List[PhaseEntry] = field(default_factory=list)
    new_skill_id: Optional[int] = None
    versions_total: int = 0
    elapsed_s: float = 0.0


@dataclass
class TestEntry:
    test_id: str
    node_id: str
    status: str = "unknown"   # passed / failed / error
    elapsed_s: float = 0.0
    rounds: List[RoundEntry] = field(default_factory=list)
    error_msg: str = ""
    started_at: str = ""


# ── Global registry (per session) ─────────────────────────────────────────────

_session_entries: List[TestEntry] = []
_active: Dict[str, TestEntry] = {}   # node_id → TestEntry


def record_round(test_node_id: str, round_entry: RoundEntry) -> None:
    """Called from test code to record one pipeline round."""
    entry = _active.get(test_node_id)
    if entry:
        entry.rounds.append(round_entry)


# ── Pytest hooks ──────────────────────────────────────────────────────────────

def pytest_runtest_setup(item):
    entry = TestEntry(
        test_id=item.name,
        node_id=item.nodeid,
        started_at=datetime.now().strftime("%H:%M:%S"),
    )
    _active[item.nodeid] = entry
    _session_entries.append(entry)
    item._amu_start = time.monotonic()


def pytest_runtest_logreport(report):
    entry = _active.get(report.nodeid)
    if entry is None:
        return
    if report.when == "call":
        if report.passed:
            entry.status = "passed"
        elif report.failed:
            entry.status = "failed"
            entry.error_msg = str(report.longreprtext) if hasattr(report, "longreprtext") else ""
        elif report.skipped:
            entry.status = "skipped"
    if report.when == "teardown":
        start = getattr(report, "_amu_start", None)
        # elapsed captured via item hook below


def pytest_runtest_makereport(item, call):
    entry = _active.get(item.nodeid)
    if entry and call.when == "call":
        entry.elapsed_s = time.monotonic() - getattr(item, "_amu_start", time.monotonic())


def pytest_sessionfinish(session, exitstatus):
    if not _session_entries:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORT_DIR / f"amu_{ts}.html"
    html_content = _render_html(_session_entries, ts)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"\n\n📊 HTML report: {out_path}\n")


# ── HTML renderer ─────────────────────────────────────────────────────────────

def _status_badge(status: str) -> str:
    colors = {"passed": "#22c55e", "failed": "#ef4444", "error": "#f97316",
               "skipped": "#94a3b8", "unknown": "#94a3b8"}
    color = colors.get(status, "#94a3b8")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:9999px;font-size:12px;font-weight:600">{status.upper()}</span>'


def _phase_badge(ok: bool, name: str, elapsed_s: float) -> str:
    color = "#22c55e" if ok else "#ef4444"
    icon = "✓" if ok else "✗"
    return (f'<span title="{name}: {elapsed_s:.1f}s" '
            f'style="display:inline-block;background:{color};color:#fff;'
            f'padding:2px 7px;border-radius:4px;margin:2px;font-size:11px">'
            f'{icon} {name} ({elapsed_s:.0f}s)</span>')


def _render_rounds(rounds: List[RoundEntry]) -> str:
    if not rounds:
        return '<p style="color:#94a3b8;font-style:italic">No rounds recorded</p>'
    rows = []
    for r in rounds:
        phase_badges = "".join(
            _phase_badge(p.ok, p.name, p.elapsed_s) for p in r.phases
        )
        skill_tag = (f'<code style="background:#f1f5f9;padding:1px 5px;border-radius:3px">'
                     f'new_id={r.new_skill_id} total_v={r.versions_total}</code>'
                     if r.new_skill_id else
                     '<span style="color:#94a3b8">no new version</span>')
        group_tag = (f'<span style="background:#e0f2fe;color:#0369a1;padding:1px 6px;'
                     f'border-radius:3px;font-size:11px">group {r.group}</span> '
                     if r.group else "")
        rows.append(f"""
        <tr style="border-bottom:1px solid #f1f5f9">
          <td style="padding:8px 12px;font-weight:600;white-space:nowrap">Round {r.round_num}</td>
          <td style="padding:8px 12px">{group_tag}<span style="font-size:13px">{html.escape(r.query[:120])}</span></td>
          <td style="padding:8px 12px">{phase_badges}</td>
          <td style="padding:8px 12px">{skill_tag}</td>
          <td style="padding:8px 12px;color:#64748b;font-size:12px">{r.elapsed_s:.0f}s</td>
        </tr>""")
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#f8fafc;color:#475569;font-size:12px">
        <th style="padding:6px 12px;text-align:left">Round</th>
        <th style="padding:6px 12px;text-align:left">Query</th>
        <th style="padding:6px 12px;text-align:left">Phases</th>
        <th style="padding:6px 12px;text-align:left">Skill Version</th>
        <th style="padding:6px 12px;text-align:left">Time</th>
      </tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _render_html(entries: List[TestEntry], ts: str) -> str:
    passed = sum(1 for e in entries if e.status == "passed")
    failed = sum(1 for e in entries if e.status == "failed")
    total_rounds = sum(len(e.rounds) for e in entries)

    cards = []
    for e in entries:
        error_section = ""
        if e.error_msg:
            error_section = (f'<details style="margin-top:8px"><summary style="color:#ef4444;cursor:pointer">'
                             f'Error detail</summary><pre style="background:#fef2f2;padding:10px;'
                             f'font-size:11px;overflow:auto;max-height:200px">{html.escape(e.error_msg[:3000])}'
                             f'</pre></details>')
        cards.append(f"""
    <div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:16px;overflow:hidden">
      <div style="background:#f8fafc;padding:12px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #e2e8f0">
        {_status_badge(e.status)}
        <span style="font-weight:600;font-family:monospace;font-size:13px">{html.escape(e.test_id)}</span>
        <span style="color:#94a3b8;font-size:12px">started {e.started_at} · {e.elapsed_s:.0f}s · {len(e.rounds)} round(s)</span>
      </div>
      <div style="padding:12px 16px">
        {_render_rounds(e.rounds)}
        {error_section}
      </div>
    </div>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AMU Integration Test Report — {ts}</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#f8fafc;color:#1e293b;margin:0;padding:24px }}
    h1 {{ margin:0 0 4px 0;font-size:22px }}
    .summary {{ display:flex;gap:20px;margin:16px 0 24px }}
    .stat {{ background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 20px;text-align:center }}
    .stat-n {{ font-size:28px;font-weight:700 }}
    .stat-l {{ font-size:12px;color:#64748b;margin-top:2px }}
  </style>
</head>
<body>
  <h1>🧪 AMU Integration Test Report</h1>
  <p style="color:#64748b;margin:0">Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} · Full real pipeline (retriever→executor→extractor→tester)</p>
  <div class="summary">
    <div class="stat"><div class="stat-n" style="color:#1e293b">{len(entries)}</div><div class="stat-l">Total Tests</div></div>
    <div class="stat"><div class="stat-n" style="color:#22c55e">{passed}</div><div class="stat-l">Passed</div></div>
    <div class="stat"><div class="stat-n" style="color:#ef4444">{failed}</div><div class="stat-l">Failed</div></div>
    <div class="stat"><div class="stat-n" style="color:#3b82f6">{total_rounds}</div><div class="stat-l">Total Rounds</div></div>
  </div>
  {"".join(cards)}
</body>
</html>"""
