# math_train_v1_exp1

**Era**: `B-v2`  |  **Dataset**: DeepScaler train.shuffle.parquet 200 train / 200 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct

## Result

PARTIAL (killed at 77/200): 64.7%/run, 84.4% any-of-4, 0 timeouts. Avg 1.38 code blocks/run.

## Diff vs prior experiment

harder MATH pool than math_v1; baseline drops ~27pp vs math_v1

## Files

- `math_train_v1_exp1_baseline_detail.json`
- `math_train_v1_exp1_baseline_summary.json`

## Reproduce

See `run.sh`.
