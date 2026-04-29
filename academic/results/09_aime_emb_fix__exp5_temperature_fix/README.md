# aime_experiment_emb_fix

**Era**: `A-emb`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 0.0 ✓ (fixed via e80bcf7)
- Max tokens (per turn): 16000
- Max agent steps: 15
- Runs per problem (test): 1
- Skill rendering: full body
- Execution pattern: single-shot exec + emb retrieval

## Result

baseline 53.3% (+10pp vs exp4), with-skills 50.0%, evolve 50.0%

## Diff vs prior experiment

=exp4 but temperature properly forwarded to BigModel

## Files


## Reproduce

See `run.sh`.
