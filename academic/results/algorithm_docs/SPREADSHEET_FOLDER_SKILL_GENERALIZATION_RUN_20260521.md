# Spreadsheet Folder Skill Generalization Run 20260521

## Code Changes

- `academic/benchmarks/spreadsheet/prompts.py`: strengthened `SPREADSHEET_EXTRACT_SYSTEM` to prefer generalized SpreadsheetBench operations over task-specific answer scripts. Added requirements for parameterizing sheets, ranges, headers, keywords, thresholds, markers, and destination columns. Added few-shot guidance for keyword marking, header-based column moves, cross-sheet matching, and an explicit bad hardcoded-skill example.
- `academic/benchmarks/spreadsheet/maintenance/adapter.py`: strengthened extraction payload and terminal folder extractor instructions. Folder extractor now asks for `SKILL.md` sections: When to use, When not to use, Inputs to configure, Workbook assumptions, and How to copy/adapt or run.
- `academic/benchmarks/spreadsheet/maintenance/adapter.py`: aligned folder extractor size contract with the actual rubric: `SKILL.md <= 180` words, recommended <= 160. Added hard constraints to the system prompt so they are not lost when the JSON payload compactor truncates long `instruction` text.
- `academic/benchmarks/spreadsheet/maintenance/adapter.py`: fixed duplicate `SKILL.md` body counting for `skill_package` rubric checks. Before this, raw folder artifacts could have `body == SKILL.md` and `metadata.package_files["SKILL.md"] == SKILL.md`, causing body word count to be double-counted.
- `academic/benchmarks/spreadsheet/maintenance/adapter.py`: added `_compact_spreadsheet_skill_md` to conservatively shorten generated `SKILL.md` content without changing executable scripts or bundle tests.
- `academic/benchmarks/spreadsheet/executor.py`: made Spreadsheet `skill_injector_mode=off|none` mean no LLM injector but direct injection of retrieved top-k skills.
- `academic/benchmarks/spreadsheet/executor.py`: hardened `extract_bash_command` so outer bash fences are not truncated by Markdown fences embedded inside heredoc-written `SKILL.md`.
- `academic/benchmarks/tests/test_spreadsheet_evolution.py`: added regression tests for direct `off|none`, folder extractor generalized prompt constraints, bash fence parsing, and no double-counting/compaction of `SKILL.md`.

## Verification

- `pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py`
- Result: 68 passed.

## Extractor Probe

Command shape:

```bash
TE_TIMEOUT_RETRIES=1 TE_LLM_TIMEOUT=180 python <probe script over fixed train traces>
```

Source result file:

- `academic/results/spreadsheet_0521-fixedsplit-train20-callable-check.json`

Cases probed:

- `192-22` -> `keyword_column_marker`
- `408-39` -> `move_column_by_header`
- `370-43` -> `insert_row_above_marker`
- `141-20` -> `cross_sheet_match_delete`
- `66-24` -> `date_threshold_filter_copy`

Outcome: 5/5 produced folder-style `skill_package` artifacts that passed bundle/replay before being returned. The generated skills were parameterized and suitable for direct run or copy/adapt.

Important debug finding: before the bash parser fix, the model generated a reasonable `SKILL.md` containing nested Markdown fences, but `extract_bash_command` truncated the outer bash block early. That caused missing `scripts/apply.py` and `bundles/.../run_tests.py`.

## 50/50 Attempt

Started command:

```bash
TE_TIMEOUT_RETRIES=1 TE_LLM_TIMEOUT=180 python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet --mode evolve \
  --llm-config local_claude_proxy --model-name claude-sonnet-4-5 \
  --n-train 50 --n-test 50 --n-train-runs 1 --n-runs 1 \
  --train-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json \
  --test-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json \
  --spreadsheet-execution-mode bash_react \
  --spreadsheet-skill-format folder \
  --spreadsheet-max-turns 20 \
  --skill-injector-mode off \
  --top-k-skills 3 \
  --spreadsheet-pending-skill-fraction 1.0 \
  --spreadsheet-test-pending-skill-fraction 1.0 \
  --train-concurrency 4 \
  --micro-maintenance-concurrency 4 \
  --test-concurrency 4 \
  --max-task-seconds 180 \
  --tag 0521-spreadsheet-folder-generalized-direct-50_50 \
  --partial-output academic/results/spreadsheet_0521-folder-generalized-direct-50_50_partial.json \
  --output academic/results/spreadsheet_0521-folder-generalized-direct-50_50.json
```

Stopped manually after monitoring because maintenance cost was too high and the run had not reached test.

Artifacts retained:

- `academic/results/spreadsheet_0521-folder-generalized-direct-50_50_partial.json`
- `academic/results/spreadsheet_0521-folder-generalized-direct-50_50.log`
- `academic/results/spreadsheet_0521-folder-generalized-direct-50_50_partial.aborted_2153.json`

Partial state at stop:

- stage: `train_window_done`
- completed: `False`
- train details: 20
- test details: 0
- skills: 4
- micro maintenance reports: 10
- train successes: 4/20
- train avg score: 0.3352
- injected train runs: 10
- called skill function runs: 0
- skill code read runs: 0
- `USE_NOW` mentions: 10
- copy/adapt/skills path mentions: 11

Skills created:

- `extract_day_from_last_date`, pending, source `31628`
- `keyword_column_marker`, pending, source `192-22`
- `move_column_by_header`, pending, source `408-39`
- `insert_rows_above_marker`, pending, source `370-43`

## Observations

- Direct injection is working: after the first 10-task maintenance window, subsequent train tasks had `prompt_injected_skills`.
- The executor now explicitly evaluates injected skills each turn. In sampled injected cases, it usually wrote `SKIP` with a relevance reason.
- Strict folder skill call/read still did not happen in the 20-train partial.
- Non-strict adaptation evidence remains weak. The trace contains `USE_NOW`/copy/adapt/path mentions mostly because the prompt asks for decisions; sampled execution code did not import or run folder scripts.
- Folder extraction quality is materially better after the prompt and parser fixes. The main blocker moved from "cannot produce usable folder skills" to "created skills are usually not relevant enough to later tasks, and folder prestore/refine is expensive."
- The folder prestore gate is expensive. The partial log recorded 30 completed `spreadsheet_folder_extractor` LLM calls and 14 completed `spreadsheet_package_refiner` LLM calls before the stopped state. This is too high for a 50/50 run without further gating or amortization.

## Next Fix Candidates

- Do not run full terminal folder extraction for every successful/partial train task. Add a cheap prefilter before terminal extraction, or raise the extraction evidence threshold for folder skills.
- Add a cheaper first-pass extractor that proposes only skill intent and reuse class; run terminal folder extraction only for promising cross-task reusable operations.
- Add a test-mode copy/adapt prompt pattern that explicitly tells the executor to inspect `skills/<name>/SKILL.md` and `scripts/apply.py` when a skill is relevant, rather than only making a decision label.
- Consider promoting the most reusable 20-train pending skills into a fixed store and running a separate 50-test-only evaluation before another full 50/50 train run.
