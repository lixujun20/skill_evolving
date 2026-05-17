# BFCL Related50/50 Claude45Proxy Experiment Document

This document records the detailed runtime parameters for the `baseline` and `evolve` experiments under the related-task `Train50 + Heldout50` BFCL setting, together with the meaning of each parameter.

## Scope

- Benchmark: `bfcl_v3`
- Split: curated related-task `Train50 + Heldout50`
- Manifest: [curated_related_manifest_50_50.json](/home/lixujun/skill_evolving/academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json)
- Manifest contract: [RELATED_TASK_SELECTION_CONTRACT.md](/home/lixujun/skill_evolving/academic/experiments/bfcl_case_lists/RELATED_TASK_SELECTION_CONTRACT.md)
- Backend family: BFCL official executor path
- Model family used in this run: Claude Sonnet 4.5 through local proxy

## Split Definition

- `manifest_version`: `1`
- `train_task_ids`: `50`
- `test_task_ids`: `50`
- overlap between train/test ids: `0`
- first train ids:
  - `multi_turn_base_120`
  - `multi_turn_base_130`
  - `multi_turn_base_116`
  - `multi_turn_base_117`
  - `multi_turn_base_121`
- first held-out ids:
  - `multi_turn_base_68`
  - `multi_turn_base_152`
  - `multi_turn_base_173`
  - `multi_turn_base_52`
  - `multi_turn_base_176`

The split is frozen by the manifest file and is shared by both baseline and evolve. The legacy `train50_ids.json` is not the main split for this experiment.

## Baseline Command

The baseline experiment configuration is defined in [suites.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/suites.py:181):

```bash
python -m academic.benchmarks.bfcl.related.experiment \
  --mode baseline \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --data-source bfcl_eval_bundle \
  --execution-backend official \
  --prompt-style native \
  --tool-api-style auto \
  --max-steps-per-turn 12 \
  --max-task-seconds 240 \
  --tag claude45proxy_official_related50_50 \
  --output academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline.json
```

## Evolve Command

The evolve experiment template is defined in [suites.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/suites.py:201):

```bash
python -m academic.benchmarks.bfcl.related.experiment \
  --mode evolve \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --data-source bfcl_eval_bundle \
  --execution-backend official \
  --prompt-style native \
  --tool-api-style auto \
  --skill-injection-mode prompt_only \
  --top-k-skills 2 \
  --max-steps-per-turn 12 \
  --max-task-seconds 240 \
  --rounds 3 \
  --tag claude45proxy_official_related50_50 \
  --output academic/results/bfcl_related50_50_claude45proxy_official_related50_50_evolve.json \
  --save-skills academic/results/bfcl_related50_50_claude45proxy_official_related50_50_skills.json \
  --checkpoint academic/results/bfcl_related50_50_claude45proxy_official_related50_50_checkpoint.json \
  --output-detail-level compact
```

## Actual Runtime Difference In This Run

The current running evolve job uses the same configuration except one deliberate change:

- `--max-task-seconds` was raised from `240` to `600`

Reason:

- earlier failures were traced to proxy unavailability and timeout guard sensitivity, not a benchmark logic bug
- after proxy health recovery, the timeout ceiling was widened to avoid false per-task termination

Current running process shape:

- mode: `evolve`
- rounds target: `3`
- current checkpoint status at document write time:
  - `next_round_index = 0`
  - `current_round_next_task = 20`
  - `online_refactor_budget_remaining = 0` compatibility field only
  - `n_train_details = 20`
  - `n_online_refactor_attempts = 2` from the historical run before online refactor was removed

## Parameter Reference

The authoritative CLI definition lives in [experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/experiment.py:2754). Historical top-level scripts have been removed; use `python -m academic.benchmarks.bfcl.related.experiment` or `python -m academic.benchmarks.bfcl.related.proxy_runner`. The table below records the parameters relevant to baseline/evolve.

| Parameter | Baseline value | Evolve value | Meaning |
|---|---:|---:|---|
| `--mode` | `baseline` | `evolve` | Selects whether to run held-out-only baseline evaluation or the multi-round evolving experiment. |
| `--manifest` | default | default | Path to the frozen curated related-task split manifest. Default is `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`. |
| `--expected-train-size` | `50` | `50` | Validation guard for manifest train split size. |
| `--expected-test-size` | `50` | `50` | Validation guard for manifest held-out split size. |
| `--output` | baseline JSON path | evolve JSON path | Final result file path. |
| `--cache-dir` | default | default | BFCL cached dataset/tool artifact directory. Default is `data/benchmarks/bfcl_v3`. |
| `--llm-config` | `local_claude_proxy` | `local_claude_proxy` | LLM backend configuration key. Here it routes requests to the local proxy service. |
| `--model-name` | `claude-sonnet-4-5` | `claude-sonnet-4-5` | Concrete model identifier used through the proxy. |
| `--data-source` | `bfcl_eval_bundle` | `bfcl_eval_bundle` | BFCL data source adapter. This experiment uses the official eval bundle path rather than HF adapter mode. |
| `--rounds` | not used | `3` | Number of train rounds in evolve mode. The main experiment target is 3 rounds. |
| `--execution-backend` | `official` | `official` | Uses BFCL official backend/executor path rather than local mock execution. |
| `--prompt-style` | `native` | `native` | Prompt wrapper style for BFCL execution. `native` keeps the lighter native formatting rather than the alternative `official` or `academic` wrappers. |
| `--tool-api-style` | `auto` | `auto` | Auto-selects the tool-call transport style compatible with the configured model backend. |
| `--top-k-skills` | not used | `2` | Maximum number of retrieved skills per turn before any error-triggered retry retrieval. Only relevant in evolve mode because baseline disables skill use. |
| `--skill-injection-mode` | implicit none via baseline path | `prompt_only` | How retrieved skills are injected. `prompt_only` means skills are inserted into prompt context, not exposed as skill-tools. |
| `--max-steps-per-turn` | `12` | `12` | Maximum model-tool interaction loop steps allowed for each turn. This constrains reasoning/tool-call budget. |
| `--max-task-seconds` | `240` | planned `240`, actual run `600` | Per-task wall-clock guard. Increased to `600` in the current evolve run to avoid false timeouts after proxy recovery. |
| `--temperature` | `None` | `None` | Sampling temperature. `None` means backend default behavior is used. |
| `--synthetic-continue` | `false` | `false` | Whether to synthesize continuation behavior in certain tool-loop cases. Disabled here. |
| `--explicit-skill-tool` | `false` | `false` | Whether to expose retrieved skills as explicit callable skill tools. Disabled here. |
| `--tag` | `claude45proxy_official_related50_50` | `claude45proxy_official_related50_50` | Run identifier embedded in outputs and monitoring files. |
| `--use-handwritten-skills` | not used | `false` | Whether evolve should seed from handwritten skills. Disabled for this experiment. |
| `--save-skills` | not used | `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_skills.json` | Final saved skill repository snapshot after evolve. |
| `--checkpoint` | not used | `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_checkpoint.json` | Resume checkpoint for the long-running evolve process. |
| `--output-detail-level` | not used | `compact` | Controls result payload verbosity. `compact` keeps analysis-friendly projections without full raw payload explosion. |

## Baseline Semantics

Baseline is intentionally a no-evolution held-out evaluation:

- uses the exact same held-out 50-task list as evolve
- uses the same model/backend/prompt/tool-call family
- does not retrieve or inject evolving skills
- does not extract skills
- does not write segment index
- does not perform overlap refactor

In implementation, baseline runs with:

- empty `ArtifactStore`
- `top_k_skills = 0`
- `skill_injection_mode = "none"`

See [experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/experiment.py:2686).

## Evolve Semantics

Evolve is the full related-task overlap-refactor experiment:

1. Iterate train tasks sequentially inside each round.
2. After each train task:
   - run BFCL task execution
   - extract skill artifacts
   - write new trace segments into the segment vector index
3. At round end:
   - run bundle/test/refine maintenance
   - run clique-level posterior overlap refactor
   - snapshot round report, store state, version lineage, segment stats
4. After all rounds:
   - evaluate the held-out 50 tasks with retrieval enabled
   - do not let held-out tasks update the train-side store

Relevant implementation anchors:

- train loop: [experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/experiment.py:2262)
- per-task extraction: [experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/experiment.py:2343)
- segment index update: [experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/experiment.py:2374)
- round-end maintenance/refactor: [experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/experiment.py:2418)
- held-out test: [experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl/related/experiment.py:2579)

## Output Files

Baseline output:

- [bfcl_related50_50_claude45proxy_official_related50_50_baseline.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline.json)

Evolve output targets:

- final result:
  - [bfcl_related50_50_claude45proxy_official_related50_50_evolve.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_evolve.json)
- evolving skill store snapshot:
  - [bfcl_related50_50_claude45proxy_official_related50_50_skills.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_skills.json)
- resume checkpoint:
  - [bfcl_related50_50_claude45proxy_official_related50_50_checkpoint.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_checkpoint.json)
- current-round sidecars:
  - [bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_details.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_details.json)
  - [bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_online_refactors.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_online_refactors.json)
  - [bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_store.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_store.json)
  - [bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_segment_rows.json](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50_checkpoint_current_round_segment_rows.json)

Monitoring logs:

- [bfcl_related50_50_claude45proxy_official_related50_50.log](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_official_related50_50.log)
- [bfcl_related50_50_claude45proxy_monitor.log](/home/lixujun/skill_evolving/academic/results/bfcl_related50_50_claude45proxy_monitor.log)

## Notes On Interpretation

- `top_k_skills = 2` means per-turn retrieval budget, not per-task total unique skill count. A task trace may show more than 2 unique injected skills across all turns.
- `official_valid = true` does not imply task success. It only indicates the official checker accepted the tool-call form.
- `output-detail-level = compact` is intentional to control memory/log size and make checkpoint/resume stable for the long run.

## Early Regression Cases During Round 1

The current run showed several same-task regressions when comparing `round 1` against the same tasks from `round 0`. These are not baseline comparisons. They are intra-evolve regression checks used to understand whether repository evolution is helping or hurting repeated train performance.

At the time of inspection:

- `round 1 current` on the currently completed same-task subset:
  - `n = 27`
  - `avg_score = 0.8537`
  - `success_rate = 0.2222`
- `round 0` on the same 27 tasks:
  - `avg_score = 0.8538`
  - `success_rate = 0.2963`

This means the current half-round does not yet show improvement on repeated train tasks. The most important regressions are below.

### Case 1: `multi_turn_base_116`

Observed change:

- `round 0`: `score = 1.0`, `success = true`
- `round 1`: `score = 0.9231`, `success = false`

Key trace difference:

- `round 0` directly called:
  - `remove_stock_from_watchlist(symbol="ZETA")`
- `round 1` first called:
  - `remove_stock_from_watchlist(stock="ZETA")`
  - this produced:
    - `TradingBot.remove_stock_from_watchlist() got an unexpected keyword argument 'stock'`
  - then retried with `symbol="ZETA"`

Likely cause:

- retrieval set in `round 1` included `remove_stock_from_watchlist_stock_parameter_exact_name`
- that skill currently claims the exact parameter name should be `stock`
- the actual executor behavior contradicts the skill and accepts `symbol`, not `stock`

Interpretation:

- this is not just stochastic drift
- this is a concrete repository-side skill-quality bug that introduced a wrong first call and lowered the task score

### Case 2: `multi_turn_base_118`

Observed change:

- `round 0`: `score = 1.0`, `success = true`
- `round 1`: `score = 0.9231`, `success = false`

Key trace difference:

- `round 0` called:
  - `remove_stock_from_watchlist(symbol="NVDA")`
- `round 1` first called:
  - `remove_stock_from_watchlist(stock="NVDA")`
  - same tool error as Case 1
  - then retried with `symbol="NVDA"`

Likely cause:

- same erroneous retrieved skill:
  - `remove_stock_from_watchlist_stock_parameter_exact_name`

Interpretation:

- this is a repeated, systematic regression pattern
- because the same wrong skill is reused across tasks, it can create correlated degradation rather than isolated noise

### Case 3: `multi_turn_base_117`

Observed change:

- `round 0`: `score = 0.8571`
- `round 1`: `score = 0.8`

Key trace differences:

- `round 0` started with:
  - `get_watchlist()`
  - `remove_stock_from_watchlist(symbol="ZETA")`
- `round 1` instead started with:
  - `get_symbol_by_name(name="Zeta Corp")`
  - `remove_stock_from_watchlist(stock="ZETA")` -> error
  - retry with `symbol="ZETA"`

Likely causes:

1. The same incorrect `stock`-parameter skill caused an avoidable error.
2. The retrieved skill set also became noisier:
   - `round 0` injected 2 skills
   - `round 1` injected 6 skills
3. `round 1` added an unnecessary `get_symbol_by_name` call before removal, suggesting prompt crowding or retrieval pollution.

Interpretation:

- this case is stronger evidence that degradation is not only from one wrong skill body
- the enlarged retrieved skill set may also be shifting the model toward extra or lower-value actions

### Case 4: `multi_turn_base_121`

Observed change:

- `round 0`: `score = 1.0`, `success = true`
- `round 1`: `score = 0.9091`, `success = false`

Key trace difference:

- `round 0` turn 3 called:
  - `get_account_info()`
  - `withdraw_funds(amount=500)`
- `round 1` turn 3 only called:
  - `withdraw_funds(amount=500)`

Retrieved skill-set difference:

- `round 0` retrieved 3 skills
- `round 1` retrieved 6 skills, including unrelated watchlist/order-argument rules

Likely cause:

- there is no direct wrong-parameter error here
- the more plausible explanation is prompt interference: the skill context became broader and noisier, and the model omitted a previously successful intermediate step

Interpretation:

- this case suggests a second regression mechanism beyond outright false skills
- even when skills are not directly wrong, extra retrieved cards may dilute task-relevant reasoning

## Current Hypotheses From These Regressions

1. There is at least one repository skill with an incorrect tool-interface claim:
   - `remove_stock_from_watchlist_stock_parameter_exact_name`
2. Retrieval noise appears to increase over rounds on some tasks:
   - more unique injected skills
   - some are not locally relevant to the task turn
3. Regression is therefore currently explained by both:
   - wrong skill content
   - prompt/retrieval over-expansion

## Actionable Follow-Up

These findings should be treated as experiment-health signals, not just analysis notes:

- audit and fix the `remove_stock_from_watchlist_*` parameter-contract skill against real executor behavior
- inspect why this incorrect skill survived prior maintenance
- consider adding a stronger retrieval filter or turn-local relevance gate
- in the final report, separate:
  - regressions from false skill content
  - regressions from prompt crowding / over-retrieval
