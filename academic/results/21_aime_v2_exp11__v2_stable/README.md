# aime_v2_exp11

**Era**: `B-v2`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct, stable pipeline

## Result

baseline 39.2%, with-skills 37.5%, evolve 1ep 56.7%. Avg 4.62 code blocks/run.

## Diff vs prior experiment

stable v2 baseline

## Files

- `aime_v2_exp11_baseline_detail.json`
- `aime_v2_exp11_baseline_summary.json`
- `aime_v2_exp11_evolve_1ep_detail.json`
- `aime_v2_exp11_evolve_1ep_summary.json`
- `aime_v2_exp11_skills.json`
- `exp11_baseline.log`
- `exp11_evolve1.log`
- `exp11_evolve3.log`

## Reproduce

See `run.sh`.
