"""Minute-level progress monitor for long-running BFCL experiment processes.

This script is intentionally read-only with respect to experiment artifacts.
It inspects one or more running BFCL jobs and emits:

- a JSONL progress log with timestamped snapshots
- an optional human-readable latest-status markdown file

It is useful when the main experiment runs in tmux/nohup and we need a stable,
user-visible heartbeat that summarizes process health, checkpoint progress,
log growth, and known regression signatures.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    script_path = Path(__file__).resolve()
    script_dir = str(script_path.parent)
    project_root = str(script_path.parents[4])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    try:
        sys.path.remove(script_dir)
    except ValueError:
        pass

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List


_BAD_SIGNATURES = [
    "unexpected keyword argument",
    "remove_stock_from_watchlist(stock=",
    "LLM did not return valid JSON",
    "bundle text roundtrip validation failed",
    "artifact text roundtrip validation failed",
    "Invalid curated manifest",
    "Traceback (most recent call last)",
    "Traceback",
    "ERROR",
    "Exception",
]
_TAIL_BYTES = 262144


@dataclass(frozen=True)
class RunSpec:
    label: str
    pid: int
    run_log: Path
    checkpoint: Path


def _parse_run_spec(raw: str) -> RunSpec:
    parts = raw.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "run specs must have the form label:pid:/abs/run.log:/abs/checkpoint.json"
        )
    label, pid_text, run_log_text, checkpoint_text = parts
    try:
        pid = int(pid_text)
    except ValueError as exc:  # pragma: no cover - argparse path
        raise argparse.ArgumentTypeError(f"invalid pid in run spec: {pid_text}") from exc
    return RunSpec(
        label=label.strip(),
        pid=pid,
        run_log=Path(run_log_text).expanduser().resolve(),
        checkpoint=Path(checkpoint_text).expanduser().resolve(),
    )


def _now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())


def _read_json(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return {"_read_error": f"{type(exc).__name__}: {exc}"}
    if isinstance(payload, dict):
        return payload
    return {"_non_dict_payload_type": type(payload).__name__}


def _file_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_epoch": stat.st_mtime,
        "mtime_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
    }


def _tail_text(path: Path, max_bytes: int = _TAIL_BYTES) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        try:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(size - max_bytes, 0), os.SEEK_SET)
            chunk = handle.read()
        except OSError:
            chunk = handle.read()
    return chunk.decode("utf-8", errors="ignore")


def _log_summary(path: Path) -> Dict[str, Any]:
    stats = _file_stats(path)
    text = _tail_text(path)
    lines = [line for line in text.splitlines() if line.strip()]
    signature_hits = {pattern: text.count(pattern) for pattern in _BAD_SIGNATURES if pattern in text}
    return {
        **stats,
        "tail_lines": lines[-5:],
        "signature_hits": signature_hits,
    }


def _ps_snapshot(pid: int) -> Dict[str, Any]:
    proc = subprocess.run(
        [
            "ps",
            "-p",
            str(pid),
            "-o",
            "pid=,ppid=,etime=,%mem=,%cpu=,rss=,stat=,cmd=",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    line = proc.stdout.strip()
    if proc.returncode != 0 or not line:
        return {"alive": False}
    parts = line.split(None, 6)
    rss_kb = None
    if len(parts) >= 6:
        try:
            rss_kb = int(parts[5])
        except Exception:
            rss_kb = None
    return {"alive": True, "ps": line, "rss_kb": rss_kb}


def _checkpoint_summary(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    if not payload:
        return {"exists": False}
    if "_read_error" in payload or "_non_dict_payload_type" in payload:
        return {"exists": True, **payload}
    current = payload.get("current_round_state") or {}
    extractor_feedback = (current.get("role_feedback") or {}).get("extractor") or {}
    train_preview = list(current.get("train_details_preview") or [])
    online_preview = list(current.get("online_refactor_attempts_preview") or [])
    summary: Dict[str, Any] = {
        "exists": True,
        "next_round_index": payload.get("next_round_index"),
        "output_detail_level": payload.get("output_detail_level"),
        "current_round": {
            "round_index": current.get("round_index"),
            "next_task_index": current.get("next_task_index"),
            "n_train_details": current.get("n_train_details"),
            "n_extraction_events": current.get("n_extraction_events"),
            "n_online_refactor_attempts": current.get("n_online_refactor_attempts"),
            "n_store_artifacts": current.get("n_store_artifacts"),
            "n_store_test_results": current.get("n_store_test_results"),
            "n_segment_index_rows": current.get("n_segment_index_rows"),
            "extractor_n_rules": extractor_feedback.get("n_rules"),
            "extractor_last_update_summary": extractor_feedback.get("last_update_summary"),
            "preview_task_ids": [
                str(item.get("task_id") or "").strip()
                for item in train_preview[-3:]
                if str(item.get("task_id") or "").strip()
            ],
            "preview_task_scores": [
                {
                    "task_id": str(item.get("task_id") or "").strip(),
                    "score": item.get("score"),
                    "success": item.get("success"),
                    "official_valid": item.get("official_valid"),
                }
                for item in train_preview[-2:]
                if str(item.get("task_id") or "").strip()
            ],
            "preview_online_after_task_ids": [
                str(item.get("after_task_id") or "").strip()
                for item in online_preview[-3:]
                if str(item.get("after_task_id") or "").strip()
            ],
        },
    }
    detail_path = current.get("train_details_path")
    if detail_path:
        summary["current_round"]["train_details_path_stats"] = _file_stats(Path(detail_path))
    attempt_path = current.get("online_refactor_attempts_path")
    if attempt_path:
        summary["current_round"]["online_refactor_attempts_path_stats"] = _file_stats(Path(attempt_path))
    store_path = current.get("store_snapshot_path")
    if store_path:
        summary["current_round"]["store_snapshot_path_stats"] = _file_stats(Path(store_path))
    segment_path = current.get("segment_index_rows_path")
    if segment_path:
        summary["current_round"]["segment_index_rows_path_stats"] = _file_stats(Path(segment_path))
    return summary


def _derive_flags(
    *,
    label: str,
    ps_state: Dict[str, Any],
    log_summary: Dict[str, Any],
    checkpoint_stats: Dict[str, Any],
    checkpoint_summary: Dict[str, Any],
    previous: Dict[str, Any] | None,
) -> List[str]:
    flags: List[str] = []
    if not ps_state.get("alive"):
        flags.append("process_dead")
        return flags
    if log_summary.get("signature_hits"):
        flags.append("bad_signature_seen")
    if previous:
        prev_log_size = ((previous.get("log_summary") or {}).get("size_bytes"))
        prev_ckpt_mtime = ((previous.get("checkpoint_stats") or {}).get("mtime_epoch"))
        if prev_log_size == log_summary.get("size_bytes"):
            flags.append("run_log_not_growing")
        if prev_ckpt_mtime == checkpoint_stats.get("mtime_epoch"):
            flags.append("checkpoint_not_growing")
        if (
            prev_log_size != log_summary.get("size_bytes")
            and prev_ckpt_mtime == checkpoint_stats.get("mtime_epoch")
        ):
            flags.append("log_ahead_of_checkpoint")
    if checkpoint_summary.get("exists") and checkpoint_summary.get("current_round"):
        current = checkpoint_summary["current_round"]
        if label.startswith("wo") and current.get("extractor_n_rules") not in (None, 0):
            flags.append("ablation_rule_leak")
        if current.get("n_online_refactor_attempts") and current.get("n_train_details"):
            if int(current.get("n_online_refactor_attempts") or 0) > int(current.get("n_train_details") or 0) * 8:
                flags.append("heavy_online_refactor_density")
    rss_kb = ps_state.get("rss_kb")
    rss_limit_mb = int(os.environ.get("BFCL_MONITOR_RSS_LIMIT_MB", "0") or "0")
    if rss_limit_mb > 0 and isinstance(rss_kb, int) and rss_kb > rss_limit_mb * 1024:
        flags.append("rss_limit_exceeded")
    return flags


def _should_stop_row(row: Dict[str, Any]) -> bool:
    flags = set(row.get("flags") or [])
    hard_flags = {
        "bad_signature_seen",
        "ablation_rule_leak",
        "rss_limit_exceeded",
    }
    if flags & hard_flags:
        return True
    return False


def _terminate_pid(pid: int) -> None:
    try:
        os.kill(int(pid), 15)
    except ProcessLookupError:
        return
    except Exception:
        return


def _markdown_summary(rows: List[Dict[str, Any]]) -> str:
    lines = [
        "# BFCL Parallel Experiment Progress",
        "",
        f"Updated: {_now_ts()}",
        "",
        "## Runs",
        "",
    ]
    for row in rows:
        label = row["label"]
        ps_state = row["ps_state"]
        checkpoint_summary = row["checkpoint_summary"]
        current = checkpoint_summary.get("current_round") or {}
        lines.extend(
            [
                f"### {label}",
                "",
                f"- alive: `{ps_state.get('alive')}`",
                f"- ps: `{ps_state.get('ps', '')}`",
                f"- run_log_size_bytes: `{(row.get('log_summary') or {}).get('size_bytes')}`",
                f"- checkpoint_mtime: `{(row.get('checkpoint_stats') or {}).get('mtime_local')}`",
                f"- next_round_index: `{checkpoint_summary.get('next_round_index')}`",
                f"- next_task_index: `{current.get('next_task_index')}`",
                f"- n_train_details: `{current.get('n_train_details')}`",
                f"- n_extraction_events: `{current.get('n_extraction_events')}`",
                f"- n_online_refactor_attempts: `{current.get('n_online_refactor_attempts')}`",
                f"- extractor_n_rules: `{current.get('extractor_n_rules')}`",
                f"- extractor_last_update_summary: `{current.get('extractor_last_update_summary')}`",
                f"- flags: `{', '.join(row.get('flags') or []) or 'none'}`",
            ]
        )
        tail_lines = (row.get("log_summary") or {}).get("tail_lines") or []
        if tail_lines:
            lines.append("- tail:")
            for tail_line in tail_lines[-3:]:
                lines.append(f"  - `{tail_line[:240]}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def monitor_once(specs: List[RunSpec], previous_by_label: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for spec in specs:
        ps_state = _ps_snapshot(spec.pid)
        log_summary = _log_summary(spec.run_log)
        checkpoint_stats = _file_stats(spec.checkpoint)
        checkpoint_payload = _read_json(spec.checkpoint)
        checkpoint_summary = _checkpoint_summary(checkpoint_payload)
        row = {
            "ts": _now_ts(),
            "label": spec.label,
            "pid": spec.pid,
            "run_log": str(spec.run_log),
            "checkpoint": str(spec.checkpoint),
            "ps_state": ps_state,
            "log_summary": log_summary,
            "checkpoint_stats": checkpoint_stats,
            "checkpoint_summary": checkpoint_summary,
        }
        row["flags"] = _derive_flags(
            label=spec.label,
            ps_state=ps_state,
            log_summary=log_summary,
            checkpoint_stats=checkpoint_stats,
            checkpoint_summary=checkpoint_summary,
            previous=previous_by_label.get(spec.label),
        )
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor one or more BFCL experiment jobs")
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        type=_parse_run_spec,
        help="label:pid:/abs/run.log:/abs/checkpoint.json",
    )
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--latest-md", type=Path, default=None)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--stop-on-anomaly", action="store_true")
    args = parser.parse_args()

    specs: List[RunSpec] = args.run
    output_jsonl = args.output_jsonl.expanduser().resolve()
    latest_md = args.latest_md.expanduser().resolve() if args.latest_md else None
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if latest_md:
        latest_md.parent.mkdir(parents=True, exist_ok=True)

    previous_by_label: Dict[str, Dict[str, Any]] = {}
    while True:
        rows = monitor_once(specs, previous_by_label)
        previous_stop_counters = {
            label: dict((previous_by_label.get(label) or {}).get("_stop_counters") or {})
            for label in previous_by_label
        }
        for row in rows:
            row["_stop_counters"] = previous_stop_counters.get(row["label"], {})
        stop_labels: List[str] = []
        if args.stop_on_anomaly:
            for row in rows:
                if _should_stop_row(row):
                    _terminate_pid(int(row["pid"]))
                    stop_labels.append(str(row["label"]))
            if stop_labels:
                for row in rows:
                    if row["label"] in stop_labels:
                        row.setdefault("flags", []).append("terminated_by_monitor")
        with output_jsonl.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        if latest_md is not None:
            latest_md.write_text(_markdown_summary(rows))
        previous_by_label = {row["label"]: row for row in rows}
        if args.once:
            break
        if not any((row.get("ps_state") or {}).get("alive") for row in rows):
            break
        time.sleep(max(args.interval_seconds, 1))


if __name__ == "__main__":
    main()
