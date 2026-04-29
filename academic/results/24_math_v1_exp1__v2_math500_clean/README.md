# math_v1_exp1

**Era**: `B-v2`  |  **Dataset**: MATH-500 clean set (~/deepscaler/math.parquet) 200 train / 200 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct

## Result

baseline 91.9%, with-skills 89.4%, evolve 86.5%. But 62% of runs use 0 code blocks — TIR barely exercised; masks regression.

## Diff vs prior experiment

easier dataset than AIME; high numbers don't reflect pipeline quality

## Files

- `math_v1_exp1_baseline_detail.json`
- `math_v1_exp1_baseline_summary.json`
- `math_v1_exp1_evolve_1ep_detail.json`
- `math_v1_exp1_evolve_1ep_summary.json`
- `math_v1_exp1_skills.json`

## Reproduce

See `run.sh`.
