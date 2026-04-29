# aime_v2_exp11e3

**Era**: `B-v2`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct, 3 epochs

## Result

with-skills 46.7%, evolve 3ep 51.1%. Skills set 66 entries.

## Diff vs prior experiment

=exp11 + 3 epochs

## Files

- `aime_v2_exp11e3_evolve_3ep_detail.json`
- `aime_v2_exp11e3_evolve_3ep_summary.json`
- `aime_v2_exp11e3_skills.json`

## Reproduce

See `run.sh`.
