# aime_weak_glm45air

**Era**: `A-legacy`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `glm-4.5-air (weak)`
- Temperature: default
- Max tokens (per turn): 16000
- Max agent steps: 8
- Runs per problem (test): 1
- Skill rendering: full body
- Execution pattern: single-shot exec (weak model)

## Result

baseline 6.7%, with-skills 10.0%, evolve 33.3% (+26pp; largest relative lift)

## Diff vs prior experiment

weak-model variant of exp7b; skill lift most visible

## Files

- `aime_weak_glm45air_evolve.json`
- `aime_weak_glm45air_skills.json`
- `aime_weak_glm45air_summary.json`
- `aime_weak_glm45air_test_baseline.json`
- `aime_weak_glm45air_test_with_skills.json`

## Reproduce

See `run.sh`.
