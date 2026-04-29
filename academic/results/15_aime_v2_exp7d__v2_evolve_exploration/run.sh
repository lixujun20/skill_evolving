#!/usr/bin/env bash
# Reconstructed command for aime_v2_exp7d
# (Era B — TIR/ReAct pipeline)
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo $HOME/skill_evolving)"
conda run -n meta-agent python -u -m academic.experiments.run_experiment \
    --dataset aime \
    --tag exp7d \
    --mode baseline \
    --concurrency 4
