# aime_exp7b

**Era**: `A-legacy`  |  **Dataset**: AIME-24 train / AIME-25 test (30+30)

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: default (temperature-bug era, effective ~1.0)
- Max tokens (per turn): 16000
- Max agent steps: 8
- Runs per problem (test): 1
- Skill rendering: full body
- Execution pattern: single-shot exec

## Result

baseline 60.0%, with-skills 60.0%, evolve 66.7%

## Diff vs prior experiment

full AIME BigModel reproduction; showed model-backend drop vs SiliconFlow

## Files

- `aime_exp7b_baseline.json`
- `aime_exp7b_baseline_merged.json`
- `aime_experiment_exp7b_evolve.json`
- `aime_experiment_exp7b_skills.json`
- `aime_experiment_exp7b_summary.json`
- `aime_experiment_exp7b_test_baseline.json`
- `aime_experiment_exp7b_test_with_skills.json`

## Reproduce

See `run.sh`.
