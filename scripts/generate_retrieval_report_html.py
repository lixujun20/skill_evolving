#!/usr/bin/env python3
"""Run retrieval tests and generate a detailed HTML report with per-query tables."""
import os
import subprocess
import sys
import re
import datetime
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_TARGET = "app/meta_agent/skills/tests/test_retrieval_skillsbench.py::TestSkillsBenchRecall::test_recall_at_5_basic"
OUT_HTML = ROOT / "app/meta_agent/skills/tests/retrieval_detailed_report.html"
PYTHON_BIN = Path(
    os.environ.get(
        "PYTHON_BIN",
        "/data/lixujun/miniconda3/envs/meta-agent/bin/python3",
    )
)

def run_pytest():
    python_bin = PYTHON_BIN if PYTHON_BIN.exists() else Path(sys.executable)
    cmd = [str(python_bin), "-m", "pytest", TEST_TARGET, "-q", "-s"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    out = proc.stdout + "\n" + proc.stderr
    return out, proc.returncode


def parse_output(text: str):
    # extract metric blocks (same as before)
    metrics_blocks = []
    for m in re.finditer(r"(?m)^(.*SkillsBench.*)\n((?:.*\n){1,20}?)(?=\n|$)", text):
        title = m.group(1).strip()
        body = m.group(2)
        metrics = {}
        for line in body.splitlines():
            line = line.strip()
            mm = re.match(r"^Total queries\s*:\s*(\d+)", line)
            if mm:
                metrics['total'] = int(mm.group(1)); continue
            mm = re.match(r"^Recall@1\s*:\s*([0-9.]+).*\((\d+)/(\d+)\)", line)
            if mm:
                metrics['recall_at_1'] = float(mm.group(1)); metrics['recall_at_1_count'] = int(mm.group(2)); continue
            mm = re.match(r"^Recall@5\s*:\s*([0-9.]+).*\((\d+)/(\d+)\)", line)
            if mm:
                metrics['recall_at_5'] = float(mm.group(1)); metrics['recall_at_5_count'] = int(mm.group(2)); continue
            mm = re.match(r"^Precision@5\s*:\s*([0-9.]+)", line)
            if mm:
                metrics['precision_at_5'] = float(mm.group(1)); continue
            mm = re.match(r"^F1@5\s*:\s*([0-9.]+)", line)
            if mm:
                metrics['f1_at_5'] = float(mm.group(1)); continue
            mm = re.match(r"^Avg latency\s*:\s*([0-9.]+) ms", line)
            if mm:
                metrics['avg_latency_ms'] = float(mm.group(1)); continue
            mm = re.match(r"^Avg cost/query\s*:\s*\$(\d+\.\d+)", line)
            if mm:
                metrics['avg_cost_usd'] = float(mm.group(1)); continue
        metrics_blocks.append({'title': title, 'metrics': metrics})

    # parse per-query blocks
    queries = []
    q_re = re.compile(r"^\[query\]\s+task=(?P<task>\S+)\s+expected_skill_id=(?P<expected>\d+)\s+result_count=(?P<count>\d+)", re.MULTILINE)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = q_re.match(line)
        if m:
            task = m.group('task')
            expected = int(m.group('expected'))
            count = int(m.group('count'))
            i += 1
            candidates = []
            while i < len(lines) and lines[i].strip().startswith('rank='):
                # sometimes indentation has two spaces
                l = lines[i].strip()
                # parse rank, id, group, sim, doc
                # format: rank=1 id=1 group=name sim=n/a doc='...'
                cand = {'raw': l}
                try:
                    rank_m = re.search(r'rank=(\d+)', l)
                    id_m = re.search(r'id=(\d+)', l)
                    group_m = re.search(r"group=([^\s]+)", l)
                    sim_m = re.search(r"sim=([^\s]+)", l)
                    doc_m = re.search(r"doc='(.*)'", l)
                    cand['rank'] = int(rank_m.group(1)) if rank_m else None
                    cand['id'] = int(id_m.group(1)) if id_m else None
                    cand['group'] = group_m.group(1) if group_m else None
                    cand['sim'] = sim_m.group(1) if sim_m else None
                    cand['doc'] = doc_m.group(1) if doc_m else ''
                except Exception:
                    pass
                candidates.append(cand)
                i += 1
            queries.append({'task': task, 'expected': expected, 'count': count, 'candidates': candidates})
            continue
        i += 1

    return metrics_blocks, queries


def build_html(metrics_blocks, queries, raw_log):
    esc = html.escape
    html_parts = ["<html><head><meta charset='utf-8'><title>Retrieval Detailed Report</title>",
                  "<style>body{font-family:Arial,Helvetica,sans-serif} table{border-collapse:collapse;width:100%;} th,td{border:1px solid #ddd;padding:6px} tr:nth-child(even){background:#f9f9f9} .collapsible{cursor:pointer;padding:8px;background:#eee;border:1px solid #ccc;margin-bottom:6px} .content{display:none;padding:6px;border:1px solid #ddd;margin-bottom:10px}</style>",
                  "<script>function toggle(id){var e=document.getElementById(id); e.style.display=(e.style.display==='none')?'block':'none';}</script></head><body>"]
    html_parts.append(f"<h1>Retrieval Detailed Report</h1><p>Generated: {datetime.datetime.utcnow().isoformat()}Z</p>")
    for mb in metrics_blocks:
        html_parts.append(f"<h2>{esc(mb['title'])}</h2>")
        if mb['metrics']:
            html_parts.append('<ul>')
            for k,v in mb['metrics'].items():
                html_parts.append(f"<li>{esc(k)}: {esc(str(v))}</li>")
            html_parts.append('</ul>')

    html_parts.append(f"<h2>Per-query details ({len(queries)} queries)</h2>")
    for idx,q in enumerate(queries):
        cid = f"q{idx}"
        html_parts.append(f"<div class='collapsible' onclick=\"toggle('{cid}')\">Query {idx+1}: {esc(q['task'])} (expected skill id={q['expected']}) - {len(q['candidates'])} candidates</div>")
        html_parts.append(f"<div id='{cid}' class='content' style='display:none;'>")
        html_parts.append('<table><thead><tr><th>rank</th><th>id</th><th>group</th><th>sim</th><th>doc</th></tr></thead><tbody>')
        for c in q['candidates']:
            html_parts.append('<tr>' +
                              f"<td>{esc(str(c.get('rank','')))}</td>" +
                              f"<td>{esc(str(c.get('id','')))}</td>" +
                              f"<td>{esc(str(c.get('group','')))}</td>" +
                              f"<td>{esc(str(c.get('sim','')))}</td>" +
                              f"<td>{esc(str(c.get('doc','')))}</td></tr>")
        html_parts.append('</tbody></table>')
        html_parts.append('</div>')

    html_parts.append('<h2>Full pytest log</h2>')
    html_parts.append("<pre style='white-space:pre-wrap; max-height:600px; overflow:auto; background:#f8f8f8; padding:10px;'>")
    html_parts.append(esc(raw_log))
    html_parts.append('</pre>')
    html_parts.append('</body></html>')
    return '\n'.join(html_parts)


def main():
    raw, rc = run_pytest()
    metrics_blocks, queries = parse_output(raw)
    html_text = build_html(metrics_blocks, queries, raw)
    OUT_HTML.write_text(html_text, encoding='utf-8')
    print('Wrote detailed report to', OUT_HTML)
    sys.exit(rc)

if __name__ == '__main__':
    main()
