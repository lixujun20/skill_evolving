# math_experiment

**Era**: `A-legacy`  |  **Dataset**: MATH-500 (15-problem subset)

## Settings

- Model: `Pro/zai-org/GLM-4.7 via SiliconFlow`
- Temperature: default (~0.0)
- Max tokens (per turn): API default
- Max agent steps: 8
- Runs per problem (test): 1
- Skill rendering: full code body in prompt
- Execution pattern: single-shot exec(code, namespace)

## Result

baseline 100%, with-skills 100%, evolve→76.7% (regressed at test)

## Diff vs prior experiment

first commit; reference implementation

## Files

- `math_experiment_evolve.json`
- `math_experiment_skills.json`
- `math_experiment_summary.json`
- `math_experiment_test_baseline.json`
- `math_experiment_test_with_skills.json`

## Reproduce

See `run.sh`.
