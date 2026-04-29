# aime_multiepoch_3ep

**Era**: `A-legacy`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: default (~1.0, buggy)
- Max tokens (per turn): 16000
- Max agent steps: 8
- Runs per problem (test): 1
- Skill rendering: full body
- Execution pattern: single-shot exec + multi-epoch (3 passes over train)

## Result

baseline 70%† (reuses Exp1), with-skills 43.3%, evolve 61.1%

## Diff vs prior experiment

added multi-epoch; shows skill-set regression without temp fix

## Files

- `aime_multiepoch_3ep_emb_fix_evolve.json`
- `aime_multiepoch_3ep_emb_fix_skills.json`
- `aime_multiepoch_3ep_emb_fix_summary.json`
- `aime_multiepoch_3ep_emb_fix_test_baseline.json`
- `aime_multiepoch_3ep_emb_fix_test_with_skills.json`
- `aime_multiepoch_3ep_evolve.json`
- `aime_multiepoch_3ep_skills.json`
- `aime_multiepoch_3ep_summary.json`
- `aime_multiepoch_3ep_test_baseline.json`
- `aime_multiepoch_3ep_test_with_skills.json`

## Reproduce

See `run.sh`.
