#!/usr/bin/env bash
# Reconstructed command for math_v1_exp1
# (Era B — TIR/ReAct pipeline)
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo $HOME/skill_evolving)"
conda run -n meta-agent python -u -m academic.experiments.run_experiment \
    --dataset math \
    --tag exp1 \
    --mode baseline \
    --concurrency 4
