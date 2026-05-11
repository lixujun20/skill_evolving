# Local Claude 10-Case Fast Evolve

## Purpose

This run debugs whether the BFCL evolve pipeline is slow because of the local Claude proxy or because of algorithmic call count and BFCL executor cost.

## Command Summary

- Model: `claude-sonnet-4-5`
- API: local Anthropic-compatible proxy at `http://127.0.0.1:4000/v1`
- Benchmark: `bfcl_v3`
- Mode: `evolve`
- Train cases: `10`
- Test cases: `0`
- Skill injection: `prompt_only`
- Limits: `BFCL_MAX_EXTRACTED_SKILLS=3`, `BFCL_MAX_MAINTENANCE_TARGETS=3`, `BFCL_MAINTENANCE_CONCURRENCY=2`, `BFCL_UNIT_TEST_CONCURRENCY=2`

## Files

- `result.json`: full evolve result, including train traces, replay traces, skill artifacts, bundles, and maintenance test results.
- `skills.json`: saved evolved skill store.
- `partial_train.json`: incremental train rollout output.
- `partial_refine.json`: incremental integration replay output.

## Logging Caveat

This run did not set `SKILL_MAINTENANCE_AUDIT_LOG`, so role-level raw system/user/raw_response rows for extractor and bundle builder were not written to a separate `.audit.jsonl` file.

The result JSON still contains executor traces, debug events, retrieved skills, generated skills, bundles, test results, and refine decisions. The frontend can display those artifacts, but the role input/output dropdowns for maintenance LLM calls will show no audit rows for this run.

## Result Summary

- Total elapsed: `724.514s`
- Train rollout: `338.154s`
- Integration replay: `335.155s`
- Extractor: `22.179s`
- Initial bundle build: `8.007s`
- Replay bundle build: `4.846s`
- Unit utility tests: `16.162s`
- Refiner: `0s`

Main finding: the local Claude proxy is not the bottleneck. The dominant cost is real BFCL executor replay over 10 multi-turn tasks.
