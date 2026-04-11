#!/usr/bin/env python3
"""Run AMU tests quickly with options.

Usage examples:
  # list available single-point scenarios
  ./scripts/run_tests.py --list

  # run scenario 1 in foreground
  ./scripts/run_tests.py --target scenario1 --timeout 900

  # run all single-point tests in background
  ./scripts/run_tests.py --target single-point --background

  # run a custom pytest nodeid
  ./scripts/run_tests.py --target "app/meta_agent/skills/tests/integration/test_amu_single_point.py::TestAMUSinglePoint::test_sp_full_pipeline_scenario_1"

Logs are written to logs/test_results by default.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs", "test_results")
os.makedirs(LOG_DIR, exist_ok=True)

SINGLE_POINT_FILE = "app/meta_agent/skills/tests/integration/test_amu_single_point.py"
LONG_TERM_FILE = "app/meta_agent/skills/tests/integration/test_amu_long_term.py"
FIXTURES_FILE = "app/meta_agent/skills/tests/fixtures/single_point.json"


def list_scenarios():
    path = os.path.join(ROOT, FIXTURES_FILE)
    if not os.path.exists(path):
        print(f"Fixtures not found: {path}")
        return 1
    with open(path) as f:
        data = json.load(f)
    print("Available single-point scenarios:")
    for i, e in enumerate(data):
        name = e.get("skill_name") or e.get("name") or f"scenario_{i}"
        q = e.get("query", "<no query>")
        print(f"  scenario{i}: {name} — query: {q[:80].replace('\n',' ')}")
    return 0


def build_nodeid(target, scenario_idx=None):
    # Accept explicit nodeid, keywords: single-point, long-term, all, scenarioN
    if target is None:
        return [SINGLE_POINT_FILE]
    t = target.strip()
    if t.startswith("app/") or "::" in t:
        # treat as explicit pytest nodeid or filepath
        return [t]
    if t == "single-point":
        return [SINGLE_POINT_FILE]
    if t == "long-term":
        return [LONG_TERM_FILE]
    if t == "all":
        return [SINGLE_POINT_FILE, LONG_TERM_FILE]
    if t.startswith("scenario"):
        # scenario or scenarioN
        try:
            n = int(t.replace("scenario", ""))
        except Exception:
            n = scenario_idx
        return [f"{SINGLE_POINT_FILE}::TestAMUSinglePoint::test_sp_full_pipeline_scenario_{n}"]
    # fallback: treat as single-point
    return [SINGLE_POINT_FILE]


def timestamp():
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def main():
    parser = argparse.ArgumentParser(description="Run AMU tests quickly with options")
    parser.add_argument("--target", "-t", help="Target to run: single-point, long-term, all, scenarioN or pytest nodeid", default=None)
    parser.add_argument("--scenario", "-s", type=int, help="Scenario index (if using scenario keyword)")
    parser.add_argument("--timeout", type=int, default=900, help="pytest timeout seconds")
    parser.add_argument("--background", "-b", action="store_true", help="Run detached in background")
    parser.add_argument("--log", "-l", help="Log file path (default in logs/test_results)")
    parser.add_argument("--python", help="Python executable to use", default=sys.executable)
    parser.add_argument("--list", action="store_true", help="List available single-point scenarios and exit")
    parser.add_argument("--pytest-args", help="Extra pytest args (quoted)")
    args = parser.parse_args()

    if args.list:
        return list_scenarios()

    nodeids = build_nodeid(args.target, args.scenario)
    name = "+".join([os.path.basename(n).replace('/','_') for n in nodeids])
    ts = timestamp()
    default_log = os.path.join(LOG_DIR, f"amu_run_{ts}.log")
    logpath = args.log or default_log

    cmd = [args.python, "-m", "pytest"]
    for n in nodeids:
        cmd.append(n)
    cmd += ["-v", "-s", f"--timeout={args.timeout}", "--log-cli-level=INFO"]
    if args.pytest_args:
        cmd += args.pytest_args.split()

    print("Running command:")
    print(" ".join(cmd))
    print(f"Log: {logpath}")

    if args.background:
        # spawn detached process, write logs to file
        with open(logpath, "ab") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, cwd=ROOT, start_new_session=True)
        print(f"Started background pytest, PID: {proc.pid}")
        print(f"Tailing log: tail -f {logpath}")
        return 0

    # foreground: stream output to console and file
    with open(logpath, "wb") as lf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=ROOT)
        try:
            for line in proc.stdout:
                lf.write(line)
                lf.flush()
                try:
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()
                except Exception:
                    # fallback text write
                    sys.stdout.write(line.decode(errors="ignore"))
            ret = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            print("Interrupted; terminated child process")
            return 1
    print(f"pytest finished with exit code {ret}; log at {logpath}")
    return ret


if __name__ == "__main__":
    sys.exit(main())