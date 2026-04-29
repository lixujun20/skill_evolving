# aime_v2_resume_smoke

**Era**: `B-v2`  |  **Dataset**: AIME subset

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct + resume

## Result

timeout-resume sanity check

## Diff vs prior experiment

added per-problem checkpoint/resume

## Files

- `aime_v2_resume_smoke_baseline_detail.json`
- `aime_v2_resume_smoke_baseline_summary.json`

## Reproduce

See `run.sh`.
