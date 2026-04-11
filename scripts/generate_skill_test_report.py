#!/usr/bin/env python3
"""
Generate an HTML test report from pytest + LLM trace log files.

Usage:
    python generate_skill_test_report.py --trace ~/llm_test_logs/trace.log [--pytest-log pytest.log] [--output report.html]
"""

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Pricing (Claude Sonnet 4-6)
# ---------------------------------------------------------------------------
INPUT_PRICE_PER_M = 3.00   # $ per 1M prompt tokens
OUTPUT_PRICE_PER_M = 15.00  # $ per 1M completion tokens


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LLMCall:
    call_type: str          # "ASK_TOOL" or "ASK"
    model: str
    timestamp: Optional[datetime]  # timestamp at input line (may be None for inner calls)
    input_snippet: str      # truncated display of input messages
    output_snippet: str     # truncated display of output
    tool_calls: list[str]   # list of tool names called
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class TestGroup:
    name: str
    status: str             # "PASSED" | "FAILED" | "UNKNOWN"
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    llm_calls: list[LLMCall] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.llm_calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.llm_calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens for c in self.llm_calls)

    @property
    def cost_usd(self) -> float:
        return (self.prompt_tokens / 1_000_000 * INPUT_PRICE_PER_M
                + self.completion_tokens / 1_000_000 * OUTPUT_PRICE_PER_M)

    @property
    def tool_flow(self) -> list[str]:
        """Ordered, deduplicated-within-call list of tool names across all LLM calls."""
        flow = []
        for call in self.llm_calls:
            for t in call.tool_calls:
                if not flow or flow[-1] != t:
                    flow.append(t)
        return flow


# ---------------------------------------------------------------------------
# Trace file parser
# ---------------------------------------------------------------------------

TS_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')
INPUT_RE = re.compile(r'^\[?((?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) )?]?\[?(ASK_TOOL INPUT|ASK INPUT)\]? model=(\S+)')
HEADER_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(ASK_TOOL INPUT|ASK INPUT)\] model=(\S+)')
HEADER_NOTS_RE = re.compile(r'^\[(ASK_TOOL INPUT|ASK INPUT)\] model=(\S+)')
OUTPUT_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(ASK_TOOL OUTPUT|ASK OUTPUT)\]')
OUTPUT_NOTS_RE = re.compile(r'^\[(ASK_TOOL OUTPUT|ASK OUTPUT)\]')
TOKENS_RE = re.compile(r'(?:\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] )?\[TOKENS\] model=\S+ prompt=(\d+) completion=(\d+) total=(\d+)')
# Matches Function(arguments='{"action": "some_action"}', name='tool_name')
# Captures action value and tool name separately
TOOL_CALL_ACTION_RE = re.compile(r'"action":\s*"([^"]+)"')
TOOL_CALL_NAME_RE = re.compile(r"ChatCompletionMessageToolCall\(.*?function=Function\(.*?name='([^']+)'", re.DOTALL)
# Simpler per-ChatCompletionMessageToolCall extraction
TOOL_CALL_BLOCK_RE = re.compile(
    r"ChatCompletionMessageToolCall\(id='[^']*',\s*function=Function\(arguments='([^']*)',\s*name='([^']+)'\)"
)
SEP_RE = re.compile(r'^={10,}')


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if ts_str is None:
        return None
    try:
        return datetime.strptime(ts_str.strip(), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def parse_trace(path: str) -> list[LLMCall]:
    """Parse a trace file into a list of LLMCall objects."""
    calls: list[LLMCall] = []
    lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()

    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Try to match input header line (with or without timestamp)
        m = HEADER_RE.match(line)
        nots_m = HEADER_NOTS_RE.match(line) if not m else None

        if m or nots_m:
            if m:
                ts_str, call_type, model = m.group(1), m.group(2), m.group(3)
            else:
                ts_str, call_type, model = None, nots_m.group(1), nots_m.group(2)

            timestamp = _parse_ts(ts_str)
            input_lines = []
            i += 1

            # Collect lines until separator or output marker
            while i < n and not SEP_RE.match(lines[i]):
                input_lines.append(lines[i])
                i += 1

            # Skip separator
            if i < n and SEP_RE.match(lines[i]):
                i += 1

            # Now look for output section (skip blank lines after separator)
            while i < n and lines[i].strip() == '':
                i += 1

            output_lines = []
            tool_calls = []
            out_ts = timestamp

            if i < n:
                out_m = OUTPUT_RE.match(lines[i])
                out_nots_m = OUTPUT_NOTS_RE.match(lines[i]) if not out_m else None
                if out_m or out_nots_m:
                    if out_m:
                        out_ts = _parse_ts(out_m.group(1)) or timestamp
                    i += 1
                    while i < n and not SEP_RE.match(lines[i]) and not TOKENS_RE.match(lines[i]):
                        output_lines.append(lines[i])
                        # Extract tool calls: prefer action name, fallback to function name
                        for args_str, func_name in TOOL_CALL_BLOCK_RE.findall(lines[i]):
                            action_m = TOOL_CALL_ACTION_RE.search(args_str)
                            if action_m:
                                tool_calls.append(action_m.group(1))
                            else:
                                tool_calls.append(func_name)
                        i += 1

            # Build snippets
            input_text = '\n'.join(input_lines)
            output_text = '\n'.join(output_lines)
            input_snippet = _truncate(input_text, 600)
            output_snippet = _truncate(output_text, 600)

            call = LLMCall(
                call_type=call_type,
                model=model,
                timestamp=out_ts or timestamp,
                input_snippet=input_snippet,
                output_snippet=output_snippet,
                tool_calls=tool_calls,
            )
            calls.append(call)
            continue

        # Parse TOKENS line — assign to most recent call that lacks tokens
        tok_m = TOKENS_RE.match(line)
        if tok_m and calls:
            # Find last call with no tokens yet
            for c in reversed(calls):
                if c.prompt_tokens == 0:
                    c.prompt_tokens = int(tok_m.group(2))
                    c.completion_tokens = int(tok_m.group(3))
                    c.total_tokens = int(tok_m.group(4))
                    # Assign timestamp if call has none
                    if c.timestamp is None and tok_m.group(1):
                        c.timestamp = _parse_ts(tok_m.group(1))
                    break

        i += 1

    return calls


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f'\n... [{len(text) - max_chars} more chars]'


# ---------------------------------------------------------------------------
# Pytest log parser
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    full_name: str          # e.g. path::Class::test_name
    short_name: str         # just the test function name
    status: str             # "PASSED" | "FAILED"
    start_time: Optional[datetime]


TEST_LINE_RE = re.compile(
    r'^([\w./]+::[\w:]+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)
RESULT_RE = re.compile(r'^(PASSED|FAILED)\s*$')


def parse_pytest_log(path: str) -> list[TestResult]:
    """Parse a pytest log into a list of test results with start times."""
    results: list[TestResult] = []
    lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()

    pending_name: Optional[str] = None
    pending_ts: Optional[datetime] = None

    for line in lines:
        m = TEST_LINE_RE.match(line.strip())
        if m:
            pending_name = m.group(1)
            pending_ts = _parse_ts(m.group(2))
            continue

        r = RESULT_RE.match(line.strip())
        if r and pending_name:
            short = pending_name.split('::')[-1]
            results.append(TestResult(
                full_name=pending_name,
                short_name=short,
                status=r.group(1),
                start_time=pending_ts,
            ))
            pending_name = None
            pending_ts = None

    return results


# ---------------------------------------------------------------------------
# Grouping: assign LLM calls to test groups
# ---------------------------------------------------------------------------

def group_calls_by_test(
    calls: list[LLMCall],
    test_results: list[TestResult],
) -> list[TestGroup]:
    """
    Assign LLM calls to test groups.

    Strategy:
    - Sort tests by start_time.
    - A call belongs to test[i] if its timestamp >= test[i].start_time
      and < test[i+1].start_time (or end of list).
    - Calls before the first test go into a "pre-test" group.
    - If no pytest results available, use 5-second gap heuristic.
    """
    if not test_results:
        return _group_by_gap(calls)

    # Sort test results by start time
    sorted_tests = sorted(
        [t for t in test_results if t.start_time is not None],
        key=lambda t: t.start_time,
    )

    if not sorted_tests:
        return _group_by_gap(calls)

    groups: list[TestGroup] = []

    # Build end times: test[i] ends when test[i+1] starts
    for idx, tr in enumerate(sorted_tests):
        end_time = sorted_tests[idx + 1].start_time if idx + 1 < len(sorted_tests) else None
        g = TestGroup(
            name=tr.full_name,
            status=tr.status,
            start_time=tr.start_time,
            end_time=end_time,
        )
        groups.append(g)

    # Assign calls to groups
    unassigned = []
    for call in calls:
        if call.timestamp is None:
            # Attach to most recent group
            if groups:
                groups[-1].llm_calls.append(call)
            else:
                unassigned.append(call)
            continue

        assigned = False
        for g in groups:
            after_start = (g.start_time is None or call.timestamp >= g.start_time)
            before_end = (g.end_time is None or call.timestamp < g.end_time)
            if after_start and before_end:
                g.llm_calls.append(call)
                assigned = True
                break

        if not assigned:
            unassigned.append(call)

    # If there are unassigned calls (before first test), create a preamble group
    if unassigned:
        preamble = TestGroup(
            name='(pre-test / unassigned)',
            status='UNKNOWN',
            start_time=None,
            end_time=None,
            llm_calls=unassigned,
        )
        groups.insert(0, preamble)

    # Remove empty groups
    groups = [g for g in groups if g.llm_calls]

    return groups


def _group_by_gap(calls: list[LLMCall], gap_seconds: int = 5) -> list[TestGroup]:
    """Fallback: split calls into groups by time gap."""
    if not calls:
        return []

    groups: list[TestGroup] = []
    current_calls: list[LLMCall] = []
    last_ts: Optional[datetime] = None
    group_idx = 1

    for call in calls:
        if last_ts is not None and call.timestamp is not None:
            delta = (call.timestamp - last_ts).total_seconds()
            if delta > gap_seconds:
                groups.append(TestGroup(
                    name=f'Test Group {group_idx}',
                    status='UNKNOWN',
                    start_time=current_calls[0].timestamp if current_calls else None,
                    end_time=last_ts,
                    llm_calls=current_calls,
                ))
                current_calls = []
                group_idx += 1

        current_calls.append(call)
        if call.timestamp is not None:
            last_ts = call.timestamp

    if current_calls:
        groups.append(TestGroup(
            name=f'Test Group {group_idx}',
            status='UNKNOWN',
            start_time=current_calls[0].timestamp if current_calls else None,
            end_time=last_ts,
            llm_calls=current_calls,
        ))

    return groups


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: #1e1e2e;
    color: #cdd6f4;
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    padding: 24px;
}
h1 { color: #cba6f7; font-size: 1.8em; margin-bottom: 8px; }
h2 { color: #89b4fa; font-size: 1.2em; margin: 24px 0 8px; }
h3 { color: #89dceb; font-size: 1em; margin-bottom: 4px; }
a { color: #89b4fa; }

/* Summary cards */
.summary-grid {
    display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0 24px;
}
.card {
    background: #313244; border-radius: 8px; padding: 16px 20px;
    min-width: 140px;
}
.card .label { font-size: 0.75em; color: #a6adc8; text-transform: uppercase; letter-spacing: .05em; }
.card .value { font-size: 1.6em; font-weight: 700; color: #cba6f7; }

/* Cost table */
table {
    width: 100%; border-collapse: collapse; margin-bottom: 24px;
    background: #313244; border-radius: 8px; overflow: hidden;
}
th {
    background: #45475a; text-align: left; padding: 8px 12px;
    font-size: 0.8em; text-transform: uppercase; letter-spacing: .05em; color: #a6adc8;
}
td { padding: 8px 12px; border-top: 1px solid #45475a; }
tr:hover td { background: #3d3f52; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.pass { color: #a6e3a1; font-weight: 600; }
td.fail { color: #f38ba8; font-weight: 600; }
td.cost { color: #f9e2af; font-weight: 600; }

/* Test sections */
details.test-section {
    background: #313244; border-radius: 8px; margin-bottom: 12px;
    overflow: hidden;
}
details.test-section > summary {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 18px; cursor: pointer; list-style: none;
    border-bottom: 1px solid #45475a;
}
details.test-section > summary::-webkit-details-marker { display: none; }
details.test-section > summary::before {
    content: '▶'; font-size: 0.8em; color: #a6adc8; transition: transform .2s;
    min-width: 12px;
}
details.test-section[open] > summary::before { transform: rotate(90deg); }
.test-name { font-weight: 600; flex: 1; font-size: 0.95em; word-break: break-all; }
.badge {
    padding: 2px 10px; border-radius: 20px; font-size: 0.75em;
    font-weight: 700; letter-spacing: .05em; white-space: nowrap;
}
.badge.pass { background: #1e3a2f; color: #a6e3a1; border: 1px solid #a6e3a1; }
.badge.fail { background: #3a1e2f; color: #f38ba8; border: 1px solid #f38ba8; }
.badge.unknown { background: #2a2a3a; color: #a6adc8; border: 1px solid #a6adc8; }
.test-meta { font-size: 0.75em; color: #a6adc8; white-space: nowrap; }

.test-body { padding: 16px 18px; }

/* Tool flow */
.tool-flow {
    background: #1e1e2e; border-radius: 6px; padding: 12px 16px;
    font-family: monospace; font-size: 0.85em; color: #fab387;
    margin-bottom: 16px; overflow-x: auto; white-space: nowrap;
}
.tool-flow .arrow { color: #6c7086; margin: 0 4px; }
.tool-flow .tool { color: #89b4fa; }

/* LLM call details */
details.llm-call {
    background: #1e1e2e; border-radius: 6px; margin-bottom: 8px;
    border: 1px solid #45475a;
}
details.llm-call > summary {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 14px; cursor: pointer; list-style: none;
}
details.llm-call > summary::-webkit-details-marker { display: none; }
details.llm-call > summary::before {
    content: '▶'; font-size: 0.75em; color: #6c7086; transition: transform .2s;
}
details.llm-call[open] > summary::before { transform: rotate(90deg); }
.call-type { font-size: 0.8em; font-weight: 600; color: #cba6f7; }
.call-tools { font-size: 0.8em; color: #89b4fa; font-family: monospace; }
.call-tokens { font-size: 0.75em; color: #a6adc8; margin-left: auto; }

.call-body { padding: 12px 14px; border-top: 1px solid #45475a; }
.call-section-label { font-size: 0.7em; text-transform: uppercase; color: #6c7086; margin-bottom: 4px; letter-spacing: .08em; }
pre.call-text {
    background: #181825; border-radius: 4px; padding: 10px;
    font-size: 0.8em; white-space: pre-wrap; word-break: break-word;
    color: #cdd6f4; max-height: 300px; overflow-y: auto;
    border: 1px solid #313244; margin-bottom: 10px;
}

/* Per-test token table */
.token-table { margin-top: 14px; }
.token-table table { margin: 0; }
"""

JS = """
// Nothing needed — pure HTML/CSS
"""


def _badge(status: str) -> str:
    cls = {'PASSED': 'pass', 'FAILED': 'fail'}.get(status, 'unknown')
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _fmt_cost(usd: float) -> str:
    if usd < 0.001:
        return f'${usd:.5f}'
    return f'${usd:.4f}'


def _fmt_ts(ts: Optional[datetime]) -> str:
    if ts is None:
        return '—'
    return ts.strftime('%H:%M:%S')


def _render_tool_flow(flow: list[str]) -> str:
    if not flow:
        return '<span style="color:#6c7086">—</span>'
    parts = []
    for i, t in enumerate(flow):
        parts.append(f'<span class="tool">{html.escape(t)}</span>')
        if i < len(flow) - 1:
            parts.append('<span class="arrow">→</span>')
    return ''.join(parts)


def _render_llm_call(idx: int, call: LLMCall) -> str:
    tool_label = ''
    if call.tool_calls:
        tool_label = f'<span class="call-tools">[{html.escape(", ".join(call.tool_calls))}]</span>'

    tokens_label = ''
    if call.total_tokens:
        cost = (call.prompt_tokens / 1_000_000 * INPUT_PRICE_PER_M
                + call.completion_tokens / 1_000_000 * OUTPUT_PRICE_PER_M)
        tokens_label = (f'<span class="call-tokens">'
                        f'↑{call.prompt_tokens:,} ↓{call.completion_tokens:,} '
                        f'= {call.total_tokens:,} tok &nbsp;|&nbsp; {_fmt_cost(cost)}'
                        f'</span>')

    ts_str = _fmt_ts(call.timestamp)

    summary_inner = (
        f'<span class="call-type">{html.escape(call.call_type)}</span>'
        f'&nbsp;<span style="color:#6c7086;font-size:.8em">#{idx + 1}</span>'
        f'&nbsp;<span style="color:#6c7086;font-size:.75em">{ts_str}</span>'
        f'&nbsp;{tool_label}'
        f'{tokens_label}'
    )

    body = (
        f'<div class="call-body">'
        f'<div class="call-section-label">Input</div>'
        f'<pre class="call-text">{html.escape(call.input_snippet or "(empty)")}</pre>'
        f'<div class="call-section-label">Output</div>'
        f'<pre class="call-text">{html.escape(call.output_snippet or "(empty)")}</pre>'
        f'</div>'
    )

    return (
        f'<details class="llm-call">'
        f'<summary>{summary_inner}</summary>'
        f'{body}'
        f'</details>'
    )


def _render_test_group(g: TestGroup) -> str:
    badge = _badge(g.status)
    short_name = g.name.split('::')[-1]
    meta = _fmt_ts(g.start_time)

    # Tool flow
    flow_html = _render_tool_flow(g.tool_flow)
    tool_flow_section = (
        f'<div style="margin-bottom:8px"><span style="font-size:.8em;color:#6c7086;text-transform:uppercase;letter-spacing:.08em">Tool Flow</span></div>'
        f'<div class="tool-flow">{flow_html}</div>'
    )

    # LLM calls
    calls_html = ''.join(_render_llm_call(i, c) for i, c in enumerate(g.llm_calls))

    # Token cost table for this test
    cost = g.cost_usd
    token_table = (
        f'<div class="token-table">'
        f'<table>'
        f'<tr><th>Metric</th><th>Value</th></tr>'
        f'<tr><td>Prompt tokens</td><td class="num">{g.prompt_tokens:,}</td></tr>'
        f'<tr><td>Completion tokens</td><td class="num">{g.completion_tokens:,}</td></tr>'
        f'<tr><td>Total tokens</td><td class="num">{g.total_tokens:,}</td></tr>'
        f'<tr><td>LLM calls</td><td class="num">{len(g.llm_calls)}</td></tr>'
        f'<tr><td>Estimated cost</td><td class="num cost">{_fmt_cost(cost)}</td></tr>'
        f'</table>'
        f'</div>'
    )

    body = (
        f'<div class="test-body">'
        f'{tool_flow_section}'
        f'{calls_html}'
        f'{token_table}'
        f'</div>'
    )

    summary = (
        f'<summary>'
        f'<span class="test-name">{html.escape(short_name)}</span>'
        f'{badge}'
        f'<span class="test-meta">{meta}</span>'
        f'</summary>'
    )

    return f'<details class="test-section">{summary}{body}</details>\n'


def generate_html(groups: list[TestGroup], trace_path: str, run_date: str) -> str:
    total = len(groups)
    passed = sum(1 for g in groups if g.status == 'PASSED')
    failed = sum(1 for g in groups if g.status == 'FAILED')
    total_prompt = sum(g.prompt_tokens for g in groups)
    total_completion = sum(g.completion_tokens for g in groups)
    total_tokens = sum(g.total_tokens for g in groups)
    total_cost = sum(g.cost_usd for g in groups)
    avg_cost = total_cost / total if total else 0.0

    # Summary cards
    cards = (
        f'<div class="summary-grid">'
        f'<div class="card"><div class="label">Date</div><div class="value" style="font-size:1em">{html.escape(run_date)}</div></div>'
        f'<div class="card"><div class="label">Total Tests</div><div class="value">{total}</div></div>'
        f'<div class="card"><div class="label">Passed</div><div class="value" style="color:#a6e3a1">{passed}</div></div>'
        f'<div class="card"><div class="label">Failed</div><div class="value" style="color:#f38ba8">{failed}</div></div>'
        f'<div class="card"><div class="label">Total Cost</div><div class="value" style="color:#f9e2af">{_fmt_cost(total_cost)}</div></div>'
        f'</div>'
    )

    # Cost summary table
    cost_rows = ''
    for g in groups:
        status_cls = {'PASSED': 'pass', 'FAILED': 'fail'}.get(g.status, '')
        short = g.name.split('::')[-1]
        cost_rows += (
            f'<tr>'
            f'<td>{html.escape(short)}</td>'
            f'<td class="{status_cls}">{html.escape(g.status)}</td>'
            f'<td class="num">{g.prompt_tokens:,}</td>'
            f'<td class="num">{g.completion_tokens:,}</td>'
            f'<td class="num">{g.total_tokens:,}</td>'
            f'<td class="num">{len(g.llm_calls)}</td>'
            f'<td class="num cost">{_fmt_cost(g.cost_usd)}</td>'
            f'</tr>\n'
        )

    cost_table = (
        f'<h2>Cost Summary</h2>'
        f'<table>'
        f'<tr><th>Test</th><th>Status</th><th>Prompt Tokens</th>'
        f'<th>Completion Tokens</th><th>Total Tokens</th><th>LLM Calls</th><th>Cost (USD)</th></tr>'
        f'{cost_rows}'
        f'<tr style="background:#45475a;font-weight:600">'
        f'<td><strong>TOTAL</strong></td><td>—</td>'
        f'<td class="num">{total_prompt:,}</td>'
        f'<td class="num">{total_completion:,}</td>'
        f'<td class="num">{total_tokens:,}</td>'
        f'<td class="num">{sum(len(g.llm_calls) for g in groups)}</td>'
        f'<td class="num cost">{_fmt_cost(total_cost)}</td>'
        f'</tr>'
        f'<tr style="background:#3d3f52">'
        f'<td colspan="6" style="text-align:right;color:#a6adc8">Avg per test</td>'
        f'<td class="num cost">{_fmt_cost(avg_cost)}</td>'
        f'</tr>'
        f'</table>'
    )

    # Per-test sections
    test_sections = ''.join(_render_test_group(g) for g in groups)

    source_info = f'<p style="color:#6c7086;font-size:.8em;margin-bottom:16px">Source: {html.escape(trace_path)}</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM Test Report — {html.escape(run_date)}</title>
<style>{CSS}</style>
</head>
<body>
<h1>🧪 LLM Skill Test Report</h1>
{source_info}
{cards}
{cost_table}
<h2>Per-Test Details</h2>
{test_sections}
<script>{JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Generate an HTML report from pytest + LLM trace logs.',
    )
    parser.add_argument('--trace', required=True, help='Path to LLM trace log file')
    parser.add_argument('--pytest-log', default=None, help='Path to pytest output log file')
    parser.add_argument('--output', default=None, help='Output HTML file path')
    args = parser.parse_args()

    trace_path = os.path.expanduser(args.trace)
    if not os.path.exists(trace_path):
        print(f'ERROR: trace file not found: {trace_path}', file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = os.path.expanduser(args.output)
    else:
        base = os.path.splitext(trace_path)[0]
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = f'{base}_report_{ts}.html'

    print(f'Parsing trace: {trace_path}')
    calls = parse_trace(trace_path)
    print(f'  Found {len(calls)} LLM calls')

    test_results: list[TestResult] = []
    if args.pytest_log:
        pytest_log_path = os.path.expanduser(args.pytest_log)
        if os.path.exists(pytest_log_path):
            print(f'Parsing pytest log: {pytest_log_path}')
            test_results = parse_pytest_log(pytest_log_path)
            print(f'  Found {len(test_results)} test results')
        else:
            print(f'WARNING: pytest log not found: {pytest_log_path}', file=sys.stderr)

    print('Grouping calls by test...')
    groups = group_calls_by_test(calls, test_results)
    print(f'  {len(groups)} test groups')

    run_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    html_content = generate_html(groups, trace_path, run_date)

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    size_kb = os.path.getsize(output_path) / 1024
    print(f'\n✅ Report written: {output_path} ({size_kb:.1f} KB)')

    # Print quick summary
    passed = sum(1 for g in groups if g.status == 'PASSED')
    failed = sum(1 for g in groups if g.status == 'FAILED')
    total_cost = sum(g.cost_usd for g in groups)
    print(f'   Tests: {len(groups)} total, {passed} passed, {failed} failed')
    print(f'   Total cost: {_fmt_cost(total_cost)}')


if __name__ == '__main__':
    main()
