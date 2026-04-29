# aime_multiepoch_3ep_emb_fix

**Era**: `A-emb`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 0.0
- Max tokens (per turn): 16000
- Max agent steps: 15
- Runs per problem (test): 1
- Skill rendering: full body
- Execution pattern: single-shot exec + emb + 3 epochs

## Result

baseline 53.3%, with-skills 53.3%, evolve 57.8% (+4.5pp lift)

## Diff vs prior experiment

=exp5 but 3 epochs; best multi-epoch legacy result

## Files


## Reproduce

See `run.sh`.
