#!/usr/bin/env bash
# Helper script to run submodule tests for skill_evolving_v1
# Usage: ./scripts/run_tests.sh <target> [pytest_args]
# Targets:
#   all            - run all tests (including llm tests)
#   non-llm        - run only non-llm tests
#   llm            - run only llm-marked tests (requires network)
#   retrieval      - run retrieval unit tests
#   integration    - run integration tests (llm-marked)
#   report         - generate HTML test report from existing trace logs

set -euo pipefail
TARGET="${1:-all}"
shift || true
PYTHON="/data/lixujun/miniconda3/envs/meta-agent/bin/python3"
PYTEST="$PYTHON -m pytest"
export PYTHONPATH="/home/lixujun/AICosmos"

case "$TARGET" in
  all)
    echo "Running all tests (may include LLM tests)"
    $PYTEST -vv "$@"
    ;;
  non-llm)
    echo "Running non-LLM tests"
    $PYTEST -q -m "not llm" "$@"
    ;;
  llm)
    echo "Running LLM tests (ensure network/proxy and credentials)"
    # Optionally enable LLM cache to reduce costs
    # export LLM_CACHE_ENABLED=1
    $PYTEST -vv -m llm "$@"
    ;;
  retrieval)
    echo "Running retrieval unit tests"
    $PYTEST app/meta_agent/skills/tests/test_retrieval.py "$@"
    ;;
  integration)
    echo "Running integration tests (LLM marked)"
    $PYTEST app/meta_agent/skills/tests/integration/ -m llm -vv "$@"
    ;;
  report)
    TRACE_FILE="${1:-~/llm_test_logs/trace.log}"
    OUTPUT_FILE="${2:-~/llm_test_logs/test_report.html}"
    echo "Generating report from $TRACE_FILE -> $OUTPUT_FILE"
    python3 scripts/generate_skill_test_report.py --trace "$TRACE_FILE" --output "$OUTPUT_FILE"
    ;;
  *)
    echo "Unknown target: $TARGET"
    echo "Usage: $0 <all|non-llm|llm|retrieval|integration|report> [pytest_args]"
    exit 2
    ;;
esac
