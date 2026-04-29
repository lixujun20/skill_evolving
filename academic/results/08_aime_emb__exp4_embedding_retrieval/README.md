# aime_experiment_emb

**Era**: `A-emb`  |  **Dataset**: AIME-24 train / AIME-25 test

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: default (~1.0, buggy)
- Max tokens (per turn): 16000
- Max agent steps: 8
- Runs per problem (test): 1
- Skill rendering: full body
- Execution pattern: single-shot exec + embedding retrieval (3eb6618)

## Result

baseline 43.3%, with-skills 46.7%, evolve 43.3%

## Diff vs prior experiment

adds embedding retriever vs exp7b; baseline dropped 17pp → exposed temperature bug

## Files

- `aime_experiment_emb_evolve.json`
- `aime_experiment_emb_fix_evolve.json`
- `aime_experiment_emb_fix_skills.json`
- `aime_experiment_emb_fix_summary.json`
- `aime_experiment_emb_fix_test_baseline.json`
- `aime_experiment_emb_fix_test_with_skills.json`
- `aime_experiment_emb_skills.json`
- `aime_experiment_emb_summary.json`
- `aime_experiment_emb_test_baseline.json`
- `aime_experiment_emb_test_with_skills.json`

## Reproduce

See `run.sh`.
