# aime_v2_exp8

**Era**: `B-v2`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 8
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct

## Result

baseline 35.0%, with-skills 30.8%, evolve 40.0% (3ep). Avg 1.23 code blocks/run.

## Diff vs prior experiment

first full TIR baseline; accuracy collapsed vs Era A

## Files

- `aime_v2_exp8_baseline.log`
- `aime_v2_exp8_baseline_detail.json`
- `aime_v2_exp8_baseline_summary.json`
- `aime_v2_exp8_evolve_1ep.log`
- `aime_v2_exp8_evolve_1ep_detail.json`
- `aime_v2_exp8_evolve_1ep_summary.json`
- `aime_v2_exp8_evolve_3ep.log`
- `aime_v2_exp8_evolve_3ep_detail.json`
- `aime_v2_exp8_evolve_3ep_summary.json`
- `aime_v2_exp8_skills.json`

## Reproduce

See `run.sh`.
