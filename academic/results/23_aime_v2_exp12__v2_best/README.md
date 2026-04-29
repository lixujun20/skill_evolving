# aime_v2_exp12

**Era**: `B-v2`  |  **Dataset**: AIME-24 train / AIME-25 test (30+30), 4 runs/problem

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct, tuned prompts

## Result

baseline 64.2%, with-skills 60.8%, evolve 1ep 66.7%. BEST V2 AIME. Avg 4.56 code blocks/run.

## Diff vs prior experiment

best v2 run but still -5.8pp vs Era A SiliconFlow 70%

## Files

- `aime_v2_exp12_baseline_detail.json`
- `aime_v2_exp12_baseline_summary.json`
- `aime_v2_exp12_evolve_1ep_detail.json`
- `aime_v2_exp12_evolve_1ep_summary.json`
- `aime_v2_exp12_evolve_2ep_detail.json`
- `aime_v2_exp12_skills.json`
- `exp12_baseline.log`
- `exp12_evolve1ep.log`

## Reproduce

See `run.sh`.
