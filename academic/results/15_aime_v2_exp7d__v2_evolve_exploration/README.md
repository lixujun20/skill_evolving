# aime_v2_exp7d

**Era**: `B-v2`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct, with 1-epoch and 3-epoch evolve

## Result

v2 evolve exploration

## Diff vs prior experiment

exp7c + evolve runs

## Files

- `aime_v2_exp7d_baseline_detail.json`
- `aime_v2_exp7d_baseline_summary.json`
- `aime_v2_exp7d_evolve_1ep_detail.json`
- `aime_v2_exp7d_evolve_1ep_summary.json`
- `aime_v2_exp7d_evolve_3ep_detail.json`
- `aime_v2_exp7d_evolve_3ep_summary.json`
- `aime_v2_exp7d_skills.json`

## Reproduce

See `run.sh`.
