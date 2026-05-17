# Round 2 Regression Case Study

This note summarizes the currently observed `round 1 vs round 0` train-task regressions in the `BFCL Related50/50 Claude45Proxy` experiment, with a focus on separating concrete implementation bugs from algorithmic weaknesses.

## Scope

- Experiment family: `bfcl_related50_50_claude45proxy_official_related50_50`
- Comparison:
  - `round 0`: first completed train round recorded in checkpoint `round_reports[0]`
  - `round 1 current`: currently completed 50 train tasks in `checkpoint_current_round_details`
- Compared tasks: `50`
- Regressed tasks under direct same-task comparison: `8`

## Headline

The second-round regressions are not one single phenomenon.

There are at least:

1. hard implementation bugs that directly invalidate results
2. weaker skill extraction / retrieval / prompt-scope problems that create negative transfer

The most important conclusion is that earlier round-end maintenance artifacts cannot be trusted as-is. At least one bundle/test path was using invalid synthetic BFCL task fragments, and one scorer path can report `call_f1 = 1.0` while still recording `argument_mismatch`.

## Regressed Tasks

The following tasks regressed when comparing current `round 1` runs against their `round 0` counterparts:

- `multi_turn_base_121`
- `multi_turn_base_116`
- `multi_turn_base_118`
- `multi_turn_base_162`
- `multi_turn_base_117`
- `multi_turn_base_156`
- `multi_turn_base_180`
- `multi_turn_base_196`

High-frequency injected skills across these 8 regressions:

- `remove_stock_from_watchlist_stock_parameter_exact_name`: `8/8`
- `skip_symbol_lookup_for_common_stocks`: `8/8`
- `avoid_get_current_time_for_market_status`: `8/8`
- `cancel_order_requires_keyword_argument`: `7/8`
- `place_order_symbol_parameter_exact_name`: `5/8`
- `skip_watchlist_check_before_removal`: `4/8`

## Bug-Induced Regressions

### 1. Wrong watchlist contract skill causes direct extra calls

Affected tasks:

- `multi_turn_base_116`
- `multi_turn_base_118`
- `multi_turn_base_117`

Observed pattern:

- `round 0` correctly used:
  - `remove_stock_from_watchlist(symbol='ZETA')`
  - `remove_stock_from_watchlist(symbol='NVDA')`
- `round 1` injected the bad skill and produced:
  - extra `remove_stock_from_watchlist(stock='ZETA')`
  - extra `remove_stock_from_watchlist(stock='NVDA')`
  - sometimes followed by the correct `symbol=...` call anyway

This is a concrete skill-content bug, not harmless variance.

Representative evidence:

- `multi_turn_base_116`
  - `round 0`: `get_watchlist() -> remove_stock_from_watchlist(symbol='ZETA')`
  - `round 1`: `get_watchlist() -> remove_stock_from_watchlist(stock='ZETA') -> remove_stock_from_watchlist(symbol='ZETA')`
  - resulting error: extra call on turn 1

- `multi_turn_base_118`
  - same structure with `NVDA`

- `multi_turn_base_117`
  - `round 1` first adds `get_symbol_by_name(name='Zeta Corp')`
  - then adds wrong `remove_stock_from_watchlist(stock='ZETA')`
  - then still calls correct `remove_stock_from_watchlist(symbol='ZETA')`

### 2. BFCL scorer inconsistency: full `call_f1` can coexist with argument mismatch

Affected task:

- `multi_turn_base_196`

Observed pattern:

- `round 1` has:
  - `call_f1 = 1.0`
  - but `call_errors` contains `argument_mismatch` for `create_ticket`
  - actual call has extra `priority=3`
  - expected call does not

This is an implementation bug in scoring semantics.

Code reason:

- `score_bfcl_calls(...)` matches calls greedily using argument similarity threshold `>= 0.75`
  - [bfcl.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl.py:1205)
  - [bfcl.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl.py:2563)
- but `call_error_analysis(...)` separately records missing/unexpected/wrong args for the same matched call
  - [bfcl.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl.py:2599)

So the system can simultaneously say:

- score layer: this call matched perfectly enough to count
- diagnostic layer: this call had wrong arguments

This contaminates round-to-round comparisons and maintenance attribution.

### 3. Round-end maintenance / refactor path can crash on malformed bundle `expected`

Observed in log tail:

- failure at round-end maintenance after bundle building / refactor testing
- exception:
  - `ValueError: malformed node or string on line 1: <ast.Name object ...>`

Call path:

- `execute_bfcl_bundle_tests(...)`
- `run_bfcl_task(...)`
- `score_bfcl_calls(...)`
- `_parse_call(...)`

This means some generated bundle case contained a BFCL `task_fragment.expected` string that was not parseable as a BFCL function-call expression.

This is not model drift. It is a concrete bundle/refactor generation bug.

Important nuance:

- scanning the final `checkpoint_current_round_store.json` does not show any currently persisted invalid call strings
- therefore the crash likely happened on an in-memory candidate bundle/refactor artifact before commit

## Algorithm-Induced Regressions

### 4. Retrieval pollution and over-broad skill scope

Several regressions are better explained by weak scope control than by one single broken contract skill.

#### `skip_symbol_lookup_for_common_stocks`

Current content is based on `multi_turn_base_120` and claims:

- for major public stocks like `Apple -> AAPL`, skip `get_symbol_by_name`

But it appears in many unrelated regression tasks, including watchlist-removal flows.

This suggests two issues:

- extraction scope is too broad
- retrieval metadata is too permissive

Implementation amplifier:

- extractor post-processing unions the entire result batch's `allowed_tools`, `domains`, and `intent_keywords` into every artifact
  - [bfcl_llm_maintenance.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl_llm_maintenance.py:675)

That makes later retrieval more likely to over-select the skill outside its narrow causal context.

#### `skip_watchlist_check_before_removal`

Source task:

- `multi_turn_base_117`

Current rule says:

- do not call `get_watchlist` before `remove_stock_from_watchlist`

But it was stored even though its old bundle result only showed:

- `n_improved = 0`
- `n_regressed = 0`
- `pass_all_tests = true`

So this skill was never strongly validated as useful. It simply was not falsified by a weak old bundle.

### 5. Missing required steps after prompt compression

Affected task:

- `multi_turn_base_121`

Regression:

- `round 0` correctly did:
  - `get_account_info()`
  - `withdraw_funds(amount=500)`
- `round 1` skipped `get_account_info()`

This does not point to one single contract skill. It looks more like:

- prompt-injected skills encouraged shortcut behavior
- executor accepted an over-compressed plan
- no mechanism pushed the model to preserve required pre-withdrawal step structure

### 6. Literal message rewriting and unnecessary explanatory actions

Affected tasks:

- `multi_turn_base_156`
- `multi_turn_base_180`
- `multi_turn_base_162`

Representative behaviors:

- `multi_turn_base_156`
  - expected support message is a short literal string
  - `round 1` rewrote it into a longer paraphrase
  - this changed a required argument value

- `multi_turn_base_180`
  - added extra `get_booking_history(...)`
  - also rewrote support message content

- `multi_turn_base_162`
  - added extra `get_nearest_airport_by_city(location='New York')`

These look more like:

- unnecessary model elaboration
- literal-preservation failure
- extra planning steps

rather than a single parser or runtime bug.

## Why Some Bad Skills Did Not Trigger Refine

There are two distinct reasons.

### Reason 1. Old bundle/test gate was too weak

The old stored test results for several suspect skills show patterns like:

- `n_cases = 1`
- `n_improved = 0`
- `n_regressed = 0`
- `pass_all_tests = true`

Examples:

- `skip_watchlist_check_before_removal`
- `place_order_symbol_parameter_exact_name`
- `skip_symbol_lookup_for_common_stocks`
- `avoid_get_current_time_for_market_status`

So the system often interpreted:

- "not proven helpful, not obviously worse on one weak case"

as enough to keep the skill active.

### Reason 2. Maintenance target selection is source-task driven, not usage-statistic driven

Round-end maintenance currently selects targets through:

- `select_bfcl_maintenance_targets(...)`
- then builds bundles
- then runs tests
- then refines

Relevant call site:

- [bfcl_related_task_experiment.py](/home/lixujun/skill_evolving/academic/benchmarks/bfcl_related_task_experiment.py:1464)

But there is no strong post-round arbitration pass that says:

- this skill was frequently retrieved
- not used, or harmful
- therefore down-rank / disable / narrow / quarantine it

So harmful or weak skills can survive until a stronger direct failure reaches their bundle path.

## Important Newly Confirmed Root Cause

The bad watchlist skill also revealed a more serious structural issue:

- a BFCL bundle case could previously use synthetic task ids like `task_from_trace`
- bundle `task_fragment.expected` could define its own gold call sequence
- the fragment could lack real official execution context (`initial_config`, `involved_classes`)
- then official execution would degrade into meaningless runtime errors like `name not defined`
- and old checks could still pass

This means some historical maintenance outputs are invalid as evidence, even when the code path completed.

## Immediate Interpretation For The Experiment

At this point:

- raw `round 1` train rollouts are still useful for diagnosing what retrieval/prompt pollution did to behavior
- but round-end maintenance artifacts from the old pipeline cannot be treated as trustworthy evidence
- any result depending on those maintained skills or their bundles needs revalidation after the new guardrails

## Comments On Proposed Next Directions

The following user concerns are supported by the current evidence.

### A. Prompt pollution / scope control

This is a real issue.

Needed directions:

- tighter prompt wording around when a skill applies
- stronger scope fields for retrieval
- explicit "do not generalize beyond evidence" extraction behavior
- stronger few-shot examples for:
  - exact literal preservation
  - exact arg-name preservation
  - "do not add one more lookup"
  - "do not rewrite support message text"

### B. Round-end skill arbitration

This is currently missing in strong form.

A practical design should consider at least:

- high retrieval count + low use count
- high retrieval count + repeated regression correlation
- repeated bundle neutrality with no demonstrated help
- repeated prompt injection without observable utility gain

That is the right place for:

- disable
- quarantine
- retrieval penalty
- scope narrowing

### C. Reflection on retrieved-but-unused or retrieved-but-harmful skills

Current pipeline is too weak here.

What exists now:

- with/without bundle testing
- integration failure append path

What is still missing:

- a post-round "retrieved but not used" analysis loop
- a "retrieved and correlated with regression" loop
- automatic bundle tightening from negative evidence
- retriever-facing metadata repair based on non-use / harmful-use evidence

## Recommended Next Fixes

1. Repair scorer consistency:
   - a call with argument mismatch must not still receive full match credit
   - `call_f1`, `task_success`, and `call_errors` semantics must agree

2. Add bundle-fragment fidelity checks before any BFCL bundle execution:
   - real `source_task_id`
   - real official execution context
   - fragment must trace back to a real task or be rejected

3. Add skill arbitration after each round:
   - harmful correlation
   - repeated neutral/no-help
   - retrieved-but-unused
   - repeated regression association

4. Tighten extractor and bundle-builder prompts:
   - local contract only
   - no scope broadening
   - no synthetic mandatory contract without direct evidence

5. Add literal-preservation contract tests for support-message and ticket fields:
   - these are common BFCL failure surfaces in the current regressions

