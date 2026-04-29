# aime_v2_debug

**Era**: `B-v2`  |  **Dataset**: AIME small subset

## Settings

- Model: `bigmodel glm-4.7`
- Temperature: 1.0 (config.toml default)
- Max tokens (per turn): 4096
- Max agent steps: 16
- Runs per problem (test): 4
- Skill rendering: signatures only
- Execution pattern: TIR / ReAct (PersistentSandbox + tool-calling)

## Result

smoke test after rewrite

## Diff vs prior experiment

complete executor rewrite; signatures-only skills

## Files

- `aime_v2_debug_baseline_detail.json`
- `aime_v2_debug_baseline_summary.json`

## Reproduce

See `run.sh`.
