# baseline_complete

**Era**: `A-legacy`  |  **Dataset**: AIME-24 train 30 / AIME-25 test 30

## Settings

- Model: `silicon_flow (problems 1-18) + bigmodel (19-30)`
- Temperature: default (~0.0)
- Max tokens (per turn): API default
- Max agent steps: 8
- Runs per problem (test): 1
- Skill rendering: full code body in prompt
- Execution pattern: single-shot exec

## Result

baseline 70.0% (21/30), with-skills 66.7%, 7/30 zero-token (timeouts). HIGH-WATER MARK AIME RESULT.

## Diff vs prior experiment

larger AIME dataset vs math_experiment; mixed API backends

## Files

- `baseline_complete.json`

## Reproduce

See `run.sh`.
