#!/usr/bin/env bash
# Convenience snippets for running AMU tests interactively or in background.
# Copy-paste the commands you need.

# Activate env and set project
conda activate meta-agent
cd ~/skill_evolving
export PYTHONPATH=$(pwd)

# 1) List available single-point scenarios
# (reads fixtures/app/meta_agent/skills/tests/fixtures/single_point.json)
./scripts/run_tests.py --list

# 2) Run scenario 1 in foreground (900s timeout)
./scripts/run_tests.py --target scenario1 --timeout 900

# 3) Run all single-point tests in background (default timeout 900s)
./scripts/run_tests.py --target single-point --background

# 4) Run long-term tests (slow) in background (2700s timeout)
./scripts/run_tests.py --target long-term --timeout 2700 --background

# 5) Run an explicit pytest nodeid (foreground)
./scripts/run_tests.py --target "app/meta_agent/skills/tests/integration/test_amu_single_point.py::TestAMUSinglePoint::test_sp_full_pipeline_scenario_1" --timeout 900 --log logs/test_results/amu_single_point_scenario.log

# 6) Run and write to a specific log file
./scripts/run_tests.py --target single-point --timeout 900 --log logs/test_results/amu_custom.log

# 7) Start in background via nohup (alternative)
nohup ./scripts/run_tests.py --target single-point --background > logs/test_results/amu_nohup_$(date -u +%Y%m%d_%H%M%S).log 2>&1 &

# 8) Tail the latest amu_run_ log (copy-paste)
TAIL_LOG=$(ls logs/test_results/amu_run_* 2>/dev/null | sort | tail -1)
if [ -n "$TAIL_LOG" ]; then
  echo "Tailing $TAIL_LOG"
  tail -f "$TAIL_LOG"
else
  echo "No amu_run_ logs found in logs/test_results"
fi

# 9) Open the latest HTML report (Linux)
HTML=$(ls logs/test_results/amu_*.html 2>/dev/null | sort | tail -1)
if [ -n "$HTML" ]; then
  echo "Opening $HTML"
  xdg-open "$HTML" 2>/dev/null || echo "Open with browser: $HTML"
else
  echo "No HTML reports found in logs/test_results"
fi
