# Spreadsheet Bad Skill Lifecycle Analysis 2026-05-19

Case skill:
`spreadsheet_cell_level_manipulation_level_manipulation_extract_number`

Primary run:
`academic/results/spreadsheet_0520-messages-pending-train10.json`

Important logging caveat: this run did not set `SKILL_MAINTENANCE_AUDIT_LOG`, so raw LLM `system/user/raw_response` JSONL rows are unavailable. Literal role I/O below is therefore split into two classes:

- **Stored literal**: exact data persisted in result JSON/log.
- **Reconstructed literal**: exact payload shape rebuilt from the same code path and stored result JSON. This is not claimed to be the historical raw audit row.

## Summary

The bad skill was created from successful source task `31628`, promoted because the old macro rule trusted successful source evidence, retrieved in later unrelated Cell-Level Manipulation tasks `58499` and `59902`, received harmful credit twice, failed refiner twice with `ValueError`, and was finally disabled only at macro filtering after repeated high-confidence harmful credit.

```
comment
你说了这里是因为unrelated才harmful， 为什么最后结论是这个skill写的不好，而不是injector和retriever的问题？credit assigner有没有显式区分这几种情况？（不相关，相关但是skill设计不好导致cover不足，signature相关但是skill实现不对应）
```

Root causes:

1. Extractor copied a notebook transcript into a broad callable skill instead of extracting a short parameterized reusable contract.
2. Coercion/normalization accepted a truncated notebook transcript as `functional`.
```
comment
无论如何，完整代码段不应该截断。
```
3. Old promotion logic did not require posterior positive credit or a passing bundle test before active promotion.
```
comment
我们设计的promotion原则是，存在至少N个（N=2，一个是原始的pass，一个是新的，）真正positive的credit。如果没有实现的话属于实现的bug。
btw，我们只有在executor之后才会算credit吧？refine+test不会算吧？
```
4. Executor prompt exposed only signature/gist in the runtime message, not full body/code, so the model could call or inspect only through a code object but was not shown the implementation inline.
```
comment
现在不是改过了嘛还没有暴露完整code嘛
```
5. Credit assigner correctly identified harmful workflow pollution, but did not set `filter_candidate=true`.
```
comment
这里的action具体都有哪些？
```
6. Refiner received harmful credit and failed bundle evidence, but LLM JSON parsing failed; old fallback returned `keep`.
```
comment
parsing failed是说他写的response json不对吗？我们做一些重试机制吧，至多重试3次。以及我们的json格式是太复杂了嘛，涉及到escape嘛？
```
7. Macro filtering eventually disabled the skill, but too late.

## Responses To Review Comments

### 1. Why conclude the skill was bad rather than only retriever/injector?

Corrected answer: this was not a single-cause failure. It had at least three separable failure modes:

- `irrelevant_retrieval`: retriever/injector exposed a date-extraction skill to branch-filtering and INDEX/MATCH tasks.
- `skill_scope_too_broad`: the skill advertised broad `Cell-Level Manipulation` applicability even though its real contract was date-component extraction from `A1:A11` into `B1`.
- `skill_body_wrong_or_incomplete`: the callable body was a copied/truncated notebook transcript, not a complete parameterized helper.

The credit assigner previously had `effect_type` and `attribution_scope`, but not a first-class failure-mode field. I added `failure_mode` with values:

```text
irrelevant_retrieval
skill_scope_too_broad
skill_body_wrong_or_incomplete
executor_misuse
insufficient_evidence
```

So future credit can distinguish retriever pollution from bad skill design and from executor misuse.

### 2. Complete code must not be truncated

Agreed. The extractor gate now rejects oversized code blocks instead of truncating them. The code limit was changed to non-empty line count only, default `15`, and prompt/rubric explicitly say executable code must be complete and never truncated mid-block.

### 3. Promotion principle should require source pass plus new positive credit

Agreed. The previous implementation was wrong. Promotion now requires:

```text
source_success
+ at least one posterior helpful credit on a different task
+ passing bundle test
```

The threshold is configurable via:

```text
SPREADSHEET_PROMOTION_HELPFUL_CREDIT_THRESHOLD
```

Default is `1`, so together with the source task this implements `N=2` positive evidence: original source pass plus one new positive/helpful reuse.

Also confirmed: credit is assigned after executor rollout. Refine + bundle test did not create normal executor credit; bundle test results are stored as test evidence, not as helpful credit. That distinction is now preserved in promotion: a passing bundle alone is insufficient.

### 4. Does full code exposure exist now?

Clarification: before this latest patch, progressive disclosure still only showed signature/gist and offered `print(<function_name>_skill.code)`. Full mode did not inline the complete implementation either.

Now full callable disclosure includes:

```text
Full implementation:
<complete fenced python implementation>
```

Progressive mode still uses signature/gist plus inspectable code object, by design.

### 5. Credit actions

Current Spreadsheet credit `maintenance_actions[].action` options are:

```text
keep
narrow_scope
fix_schema_contract
refine_workflow
disable_candidate
add_bundle_case
record_evidence
```

Separately, top-level fields now include:

```text
judgment
effect_type
failure_mode
refine_required
filter_candidate
attribution_scope
```

### 6. What does parsing failed mean, and retry?

`refiner_failed:ValueError` means `_ask_json` did not get a JSON object after extraction/parsing. It can be malformed JSON, Markdown/prose that cannot be repaired, or a top-level JSON type other than object. The schema is complex enough that escaping long code/body strings can make failures more likely.

I added JSON retry in `academic/skill_repository/llm_maintenance.py::_ask_json`:

```text
MAINTENANCE_JSON_MAX_ATTEMPTS=3
```

On parse/type failure, it appends a repair instruction and asks again. Audit rows include `attempt`.

### 7. Strange repeated name

The repeated `level_manipulation_level_manipulation` came from heuristic naming:

```text
spreadsheet_{instruction_type}_{keywords[:4]}
```

and `_spreadsheet_keywords` also included words from `instruction_type`, so `Cell-Level Manipulation` appeared twice. I fixed this by:

- deriving keywords only from the question text;
- excluding broad tokens `level` and `manipulation`;
- deduplicating tokens in `_spreadsheet_make_skill_name`.

### 8. Extractor input seemed to have no trace

The document excerpt was too short. The actual extractor payload includes `result.trace.code_snippet`, `stdout_tail`, `stderr_tail`, and `notebook_steps`. For notebook runs, every notebook step is preserved in projection, although long per-step code/stdout fields are compacted. The old extracted bad skill came from that trace code snippet.

The code-snippet projection can still compact very long traces for prompt budget, but extracted callable code is now required to be complete; if a complete reusable snippet cannot fit the code-line budget, extractor must split or drop it.


## Body Fields

Current body content observed in Spreadsheet skills includes these sections:

```text
Applicability: when this skill should be considered.
Reusable openpyxl idiom: executable or semi-executable code snippet.
Openpyxl idiom: alternative code-snippet section name used by heuristic repair skills.
Corrective rule: failure-derived rule, especially formula/value mismatch repair.
Failure evidence to avoid: predicted/expected examples from verifier mismatches.
Non-applicability: explicit exclusion scope.
```

The new field-aware trimmer preserves these sections independently instead of slicing the entire body string. If the body is too long, it keeps short field content and keeps the first code block only if that code block itself satisfies the code limits.

My recommended cut policy:

- Keep: `Applicability`, `Non-applicability`, concise `Corrective rule`.
- Keep only if small and parseable: first reusable code block.
- Trim aggressively: `Failure evidence to avoid`, because exact predicted/expected examples can explode.
- Drop when over budget: copied notebook inspection transcripts, absolute `/tmp/...` paths, stdout-style commentary.

## Existing Bad Skill Artifact

Stored literal output after extraction/coercion:

```json
{
  "name": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number",
  "kind": "executable_tool",
  "description": "Openpyxl pattern for Cell-Level Manipulation spreadsheet tasks.",
  "status": "disabled",
  "metadata": {
    "domains": ["SpreadsheetBench", "Cell-Level Manipulation"],
    "intent_keywords": ["level", "manipulation", "extract", "number", "last", "day", "range", "dates", "a11", "excel", "ensuring", "date"],
    "source_task_ids": ["31628"],
    "injection_type": "functional",
    "source_success": true,
    "source_score": 1.0,
    "is_promoted": true,
    "promotion_reason": "spreadsheet_successful_source_trace_in_macro_window",
    "disabled": true,
    "disabled_reason": "spreadsheet_macro_repeated_high_confidence_harmful_credit"
  },
  "body": "Applicability: SpreadsheetBench Cell-Level Manipulation tasks with similar instruction terms ['level', 'manipulation', 'extract', 'number', 'last', 'day', 'range', 'dates'] and answer range `B1`. Use openpyxl, inspect workbook sheets, write only the requested answer cells, and preserve unrelated sheets/styles.\n\nReusable openpyxl idiom from successful evidence:\n```python\n# %% notebook turn 0\nimport openpyxl\nfrom datetime import datetime\n\n# Load the input workbook\nINPUT_XLSX = '/tmp/spreadsheetbench_nb_o_2ufrri/31628_input.xlsx'\nOUTPUT_XLSX = '/tmp/spreadsheetbench_nb_o_2ufrri/31628_output.xlsx'\n\nwb = openpyxl.load_workbook(INPUT_XLSX)\nws = wb.active\n\n# Inspect the data in A1:A11\nprint(\"Data in A1:A11:\")\ndates = []\nfor i in range(1, 12):\n    cell_value = ws[f'A{i}'].value\n    print(f\"A{i}: {cell_value} (type: {type(cell_value)})\")\n    if cell_value is not None:\n        dates.append((i, cell_value))\n\nprint(f\"\\nTotal non-empty cells: {len(dates)}\")\n\n# %% notebook turn 1\n# Find the last non-empty date in A1:A11\nlast_date = None\nlast_row = None\n\nfor i in range(1, 12):\n    cell_value = ws[f'A{i}'].value\n    if cell_value is not None:\n        last_date = cell_value\n        last_row = i\n\nprint(f\"Last date found in A{last_row}: {last_date}\")\n\n# Extract the day number from the last date\nif last_date:\n    day_number = last_date.day\n    print(f\"Day number extracted: {day_number}\")\n    \n    "
}
```

Failure: the body is broad, transcript-like, absolute-path-specific, and truncated mid-implementation. It should never have become callable.

```
comment
这个名字看着真的很奇怪，你是说做过一些normalization，我怀疑那块代码写错了吧，为什么要重复两遍level_manipulation？
```

## Role I/O: Extractor

Reconstructed literal extractor user payload for source task `31628`:

```json
{
  "existing_artifacts": [],
  "result": {
    "task": {
      "benchmark": "spreadsheet",
      "task_id": "31628",
      "question": "Extract the number of the last day from a range of dates in cells A1:A11 in my Excel sheet, ensuring that the last date corresponds to the final entry if the dates are not sequential. Display the resulting date in cell B1 as a static number and not a formula while retaining the inputs in column A.",
      "expected": {
        "answer_sheet": null,
        "answer_position": "B1",
        "golden_xlsx": "/home/lixujun/skill_evolving/data/benchmarks/spreadsheet/spreadsheetbench_verified_400/spreadsheet/31628/1_31628_golden.xlsx"
      },
      "metadata": {
        "instruction_type": "Cell-Level Manipulation",
        "spreadsheet_path": "spreadsheet/31628"
      }
    },
    "success": true,
    "score": 1.0,
    "metrics": {
      "answer_position": "B1",
      "cell_accuracy": 1.0,
      "checked_cells": 1,
      "mismatched_cells": [],
      "execution_ok": true,
      "returncode": 0,
      "total_tokens": 4966
    }
  },
  "rubric": {
    "max_body_words": 220,
    "max_body_nonempty_lines": 35,
    "max_code_nonempty_lines_per_block": 15,
    "instruction": "Every artifact must satisfy the limits. If a candidate is too broad or too long, split it into multiple narrower skills or compress it to the reusable contract. Do not output oversized artifacts. Executable code blocks must be complete; never truncate code mid-block."
  }
}
```

Stored literal extractor output is the artifact shown above.

失职原因:

- Extractor produced a copied notebook trace.
- Old prompt did not enforce hard size limits.
- Old post-generation rubric did not reject overlong/unparseable callable code.

```
comment
260有点太少了，只约束nonempty不超过15行吧。
我这里依旧看不出输入是什么， 看起来没有trace只有题目？没有trace怎么提取？
```

## Role I/O: Store/Coercion

Old behavior:

```text
store.add_pending(artifact)
status = "pending"
metadata.retrieval_disabled_reason = "pending_prior_candidate"
```

The artifact was later promoted by macro because old promotion accepted successful source trace without posterior helpful credit or a passing bundle:

```json
{
  "promotion_reason": "spreadsheet_successful_source_trace_in_macro_window",
  "source_task_ids": ["31628"],
  "source_success": true
}
```

失职原因:

- Pending store was used as a quarantine, but promotion did not require validation.
- Callable detection accepted the truncated code body as `functional`.

## Role I/O: Executor On Bad Target 58499

Stored literal task input:

```json
{
  "task_id": "58499",
  "question": "How can I create a formula in Excel to list all the branches in column A that do not have a corresponding tick mark in column I? My attempts with an advanced filter have been unsuccessful, and I can't seem to extract the unticked branches correctly.",
  "expected": {
    "answer_sheet": null,
    "answer_position": "A13:A14"
  },
  "metadata": {
    "instruction_type": "Cell-Level Manipulation",
    "spreadsheet_path": "spreadsheet/58499"
  }
}
```

Stored literal runtime skill update:

```json
{
  "turn_index": 0,
  "retrieved_skills": ["spreadsheet_cell_level_manipulation_level_manipulation_extract_number"],
  "new_skills": ["spreadsheet_cell_level_manipulation_level_manipulation_extract_number"],
  "callable_skills": ["spreadsheet_cell_level_manipulation_level_manipulation_extract_number"],
  "message": {
    "role": "user",
    "content": "Runtime skill retrieval update for this same Spreadsheet notebook task.\nNewly retrieved local rules/functions: spreadsheet_cell_level_manipulation_level_manipulation_extract_number.\nUse these only if they directly match the workbook, answer range, and user intent; ignore them if they are irrelevant. If a callable function matches, prefer importing and calling it from `skill_library`; if unsure, inspect its code object before adapting or rewriting.\n\n(no reusable skill artifacts retrieved)\n\nNew callable Spreadsheet functions:\nExecutable Spreadsheet skills are already available through `skill_library`. You may call them directly, or inspect the implementation before deciding by running `print(<function_name>_skill.code)` in a notebook cell.\nAvailable callable signatures:\n- `spreadsheet_cell_level_manipulation_level_manipulation_extract_number(INPUT_XLSX, OUTPUT_XLSX, **kwargs)` from skill `spreadsheet_cell_level_manipulation_level_manipulation_extract_number`: Openpyxl pattern for Cell-Level Manipulation spreadsheet tasks. Applicability: SpreadsheetBench Cell-Level Manipulation tasks with similar instruction terms ['level', 'manipulation', 'extract', 'number', 'last', 'day', ' Implementation gist: Applicability: SpreadsheetBench Cell-Level Manipulation tasks with similar instruction terms ['level', 'manipulation', 'extract', 'number', 'last', 'day', 'range', 'dates'] and answer range `B1`. Use openpyxl, inspect workbook sheets, write only the requested answer cells, and preserve unrelated sheets/styles. Reusa...\nInspectable skill code objects:\n- `spreadsheet_cell_level_manipulation_level_manipulation_extract_number_skill.code` prints the full implementation for `spreadsheet_cell_level_manipulation_level_manipulation_extract_number`."
  }
}
```

Stored literal executor output:

```json
{
  "success": false,
  "score": 0.0,
  "metrics": {
    "pass": false,
    "cell_accuracy": 0.0,
    "checked_cells": 2,
    "answer_position": "A13:A14",
    "retrieved_skills": ["spreadsheet_cell_level_manipulation_level_manipulation_extract_number"],
    "prompt_injected_skills": ["spreadsheet_cell_level_manipulation_level_manipulation_extract_number"],
    "called_skill_functions": [],
    "mismatched_cells": [
      {
        "cell": "Sheet1!A13",
        "predicted": "=IFERROR(INDEX($A$2:$A$7,SMALL(IF($I$2:$I$7=\"\",ROW($I$2:$I$7)-ROW($I$2)+1),1)),\"\")",
        "expected": "<openpyxl.worksheet.formula.ArrayFormula object at 0x73f0ea6b2450>"
      },
      {
        "cell": "Sheet1!A14",
        "predicted": "=IFERROR(INDEX($A$2:$A$7,SMALL(IF($I$2:$I$7=\"\",ROW($I$2:$I$7)-ROW($I$2)+1),2)),\"\")",
        "expected": "<openpyxl.worksheet.formula.ArrayFormula object at 0x73f0ea6b2e10>"
      }
    ]
  }
}
```

失职原因:

- The skill was irrelevant and not called.
- It still consumed prompt budget and retrieval slots.
- The executor failure itself was an ArrayFormula mismatch, but the skill caused workflow pollution and was correctly attributed as harmful retrieval noise.

## Role I/O: Credit Assigner

Reconstructed literal credit user payload excerpt for `58499`:

```json
{
  "retrieval_audit": {
    "retrieved_only_skills": [],
    "candidate_policy": "prompt_injected_or_called_only"
  },
  "candidate_skills": [
    {
      "name": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number",
      "kind": "executable_tool",
      "status": "disabled",
      "injection_type": "functional",
      "body_projection": {
        "body_truncated": true,
        "executable_code_preserved": false
      }
    }
  ]
}
```

Stored literal credit output for `58499`:

```json
{
  "task_id": "58499",
  "skill_name": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number",
  "judgment": "harmful",
  "effect_type": "workflow_pollution",
  "confidence": 0.85,
  "reason": "The retrieved skill is designed for extracting day numbers from date ranges (source task 31628: 'extract number last day range dates' with answer range B1). Current task 58499 requires filtering branches without tick marks and writing array formulas to A13:A14. The skill's applicability scope ('extract', 'number', 'last', 'day', 'range', 'dates') and code pattern (iterating dates, extracting .day attribute) are completely irrelevant to the filtering/array formula requirement. The skill polluted the prompt with 1400 chars of date-extraction code that provided no guidance for the actual task. The agent correctly ignored it and wrote custom IF/INDEX/SMALL array formulas, but the task still failed because openpyxl wrote regular formulas instead of ArrayFormula objects. The skill's presence added token overhead without contributing to correctness or workflow alignment.",
  "maintenance_actions": [
    {
      "action": "narrow_scope",
      "reason": "The skill's applicability is too broad ('Cell-Level Manipulation') and matches tasks with completely different requirements. Narrow to date-extraction-specific tasks only.",
      "target_scope": "SpreadsheetBench Cell-Level Manipulation tasks requiring date component extraction (day/month/year) from datetime ranges, not general cell manipulation or filtering tasks."
    },
    {
      "action": "add_bundle_case",
      "reason": "Create a negative bundle case to prevent retrieval for filtering/array formula tasks that share 'Cell-Level Manipulation' category but have different workflows.",
      "target_scope": "filtering, array formulas, conditional extraction"
    }
  ],
  "refine_required": true,
  "filter_candidate": false,
  "evidence_strength": "strong",
  "attribution_scope": "retrieval_noise"
}
```

Credit assigner partially succeeded: it gave strong harmful credit and requested narrow-scope plus negative bundle. It failed to set `filter_candidate=true`, so old fallback did not disable immediately when refiner failed.

## Role I/O: Bundle Tester And Refiner

Stored literal micro report for task `58499`:

```json
{
  "maintenance_targets": ["spreadsheet_cell_level_manipulation_level_manipulation_extract_number"],
  "micro_target_reasons": {
    "spreadsheet_cell_level_manipulation_level_manipulation_extract_number": [
      "refine_required",
      "strong_harmful_credit",
      "negative_bundle_case"
    ]
  },
  "refine_decisions": [
    {
      "skill_name": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number",
      "action": "keep",
      "reason": "refiner_failed:ValueError"
    },
    {
      "skill_name": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number",
      "action": "keep",
      "reason": "refiner_failed:ValueError"
    }
  ],
  "credit_bundle_cases": [
    {
      "skill_name": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number",
      "case_id": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number:negative:82c466327f",
      "polarity": "negative",
      "source_task_id": "58499"
    }
  ]
}
```

Reconstructed literal refiner user payload shape:

```json
{
  "Current Artifact": {
    "name": "spreadsheet_cell_level_manipulation_level_manipulation_extract_number",
    "kind": "executable_tool",
    "description": "Openpyxl pattern for Cell-Level Manipulation spreadsheet tasks.",
    "body": "Applicability: SpreadsheetBench Cell-Level Manipulation tasks with similar instruction terms ['level', 'manipulation', 'extract', 'number', 'last', 'day', 'range', 'dates'] and answer range `B1`. Use openpyxl, inspect workbook sheets, write only the requested answer cells, and preserve unrelated sheets/styles.\n\nReusable openpyxl idiom from successful evidence:\n```python\n# %% notebook turn 0\nimport openpyxl\nfrom datetime import datetime\n...",
    "metadata": {
      "spreadsheet_compact_projection": {
        "body_truncated": true,
        "executable_code_preserved": false
      }
    }
  },
  "Test Result": {
    "aggregate": {
      "passed": false,
      "reason": "pre_bundle_credit_refine"
    },
    "failed_cases": [
      {
        "task": {"task_id": "58499"},
        "success": false,
        "score": 0.0
      }
    ]
  },
  "Recent Credit Assignment Context": [
    {
      "judgment": "harmful",
      "effect_type": "workflow_pollution",
      "confidence": 0.85,
      "refine_required": true,
      "filter_candidate": false
    }
  ]
}
```

失职原因:

- Refiner got enough signal to narrow or disable: harmful, confidence 0.85, workflow pollution, concrete negative bundle.
- Raw LLM response is not available because role audit was off.
- Stored decision shows `ValueError`; likely `_ask_json` parse/type validation failed.
- Old exception path returned `keep` unless `filter_candidate=true`, so strong harmful credit was ignored on parser failure.

## Macro Filter

Stored final disable state:

```json
{
  "status": "disabled",
  "disabled_reason": "spreadsheet_macro_repeated_high_confidence_harmful_credit",
  "disabled_credit_events": [
    {
      "task_id": "58499",
      "judgment": "harmful",
      "confidence": 0.85,
      "effect_type": "workflow_pollution"
    },
    {
      "task_id": "59902",
      "judgment": "harmful",
      "confidence": 0.9,
      "effect_type": "workflow_pollution"
    }
  ]
}
```

Macro eventually did the right thing, but only after two bad exposures.

## Code Changes Implemented

### Extractor hard constraints

File: `academic/benchmarks/spreadsheet/prompts.py`

```text
60 SPREADSHEET_EXTRACT_SYSTEM = """\
64 You receive one compact SpreadsheetBench task trace. Extract reusable,
65 testable spreadsheet skills only when the trace evidence supports them.
93 2. Prefer reusable openpyxl idioms over task transcripts.
94 3. Do not copy full code. Keep snippets short and parameterized by active sheet,
95    answer range, or detected headers.
96 4. Hard size limits per artifact:
97    - body <= 220 words and <= 35 non-empty lines.
98    - every executable code block <= 15 non-empty lines. Keep executable code
99      blocks complete; never truncate code mid-block.
100   - description <= 40 words.
101   If a candidate exceeds these limits, split it into multiple narrower skills
102   or compress it to the reusable contract. Never output an oversized skill.
```

File: `academic/benchmarks/spreadsheet/maintenance/adapter.py`

```text
513 def _spreadsheet_extract_limits() -> Dict[str, int]:
515     "max_body_words": _spreadsheet_extract_limit("MAX_BODY_WORDS", 220),
516     "max_body_lines": _spreadsheet_extract_limit("MAX_BODY_LINES", 35),
517     "max_code_lines": _spreadsheet_extract_limit("MAX_CODE_LINES", 15),
518     "max_artifacts": _spreadsheet_extract_limit("MAX_ARTIFACTS", 6),
519     "max_rewrite_rounds": _spreadsheet_extract_limit("MAX_REWRITE_ROUNDS", 2),
535 def _spreadsheet_artifact_rubric_failures(raw: Dict[str, Any], *, limits: Dict[str, int]) -> List[str]:
544     if body_words > limits["max_body_words"]:
546     if body_lines > limits["max_body_lines"]:
550         code_lines = len(_nonempty_lines(code))
551         if code_lines > limits["max_code_lines"]:
552             failures.append(f"code block {idx} has {code_lines} non-empty lines > {limits['max_code_lines']}")
560         if not _is_spreadsheet_callable_skill(probe):
561             failures.append("executable_tool does not contain parseable reusable Python code")
590 def _spreadsheet_extraction_prompt_payload(
595     "rubric": {
596         "max_body_words": limits["max_body_words"],
597         "max_body_nonempty_lines": limits["max_body_lines"],
598         "max_code_nonempty_lines_per_block": limits["max_code_lines"],
599         "instruction": (
600             "Every artifact must satisfy the limits. If a candidate is too broad or too long, "
601             "split it into multiple narrower skills or compress it to the reusable contract. "
602             "Do not output oversized artifacts. Executable code blocks must be complete; "
603             "never truncate code mid-block."
604         ),
605     },
824 role="spreadsheet_extractor" if rewrite_round == 0 else "spreadsheet_extractor_rubric_rewrite",
833 raw_artifacts = [dict(item or {}) for item in list(payload.get("artifacts") or [])]
834 rubric_failures = _spreadsheet_extraction_rubric_failures(raw_artifacts, limits=limits)
847 final_failures = _spreadsheet_extraction_rubric_failures(raw_artifacts, limits=limits)
850 if index in failed_indexes:
851     continue
```

### Field-aware body trimming

File: `academic/benchmarks/spreadsheet/maintenance/adapter.py`

```text
614 def _trim_spreadsheet_body_fields(body: str) -> str:
624 code_blocks = _spreadsheet_code_blocks(text)
633 section_labels = [
634     "Applicability",
635     "Corrective rule",
636     "Reusable openpyxl idiom",
637     "Openpyxl idiom",
638     "Failure evidence to avoid",
639     "Non-applicability",
647 content = _trim_text_words(match.group(1).strip(), max_words=45)
650 if code_block:
652     sections.insert(insert_at, "Reusable code:\n" + code_block)
661 body = _trim_spreadsheet_body_fields(str(raw.get("body") or "").strip())
```

### Pre-store bundle gate

File: `academic/benchmarks/spreadsheet/maintenance/adapter.py`

```text
302 extraction_reports: List[Dict[str, Any]] = []
303 accepted_extracted: List[SkillArtifact] = []
304 for artifact in extracted:
305     prestore_result: SkillTestResult | None = None
307     if artifact.bundle.all_cases():
309         prestore_result = await _compat_module()._execute_spreadsheet_bundle_tests(
310             artifact=artifact,
311             config=config,
312         )
317     prestore_passed = bool(
320         (prestore_result.aggregate or {}).get("pass_all_tests")
321         or (prestore_result.aggregate or {}).get("passed")
324     if not prestore_passed:
328         "status": "rejected",
331         "rejection_reason": (
332             "prestore_bundle_test_failed"
340 store.add_pending(artifact)
341 if prestore_result is not None:
342     store.add_test_result(prestore_result)
343 accepted_extracted.append(artifact)
```

Effect: extracted candidates are no longer inserted into pending store until they pass bundle validation.

### Promotion requires source success, posterior helpful credit, and passing bundle

File: `academic/benchmarks/spreadsheet/maintenance/adapter.py`

```text
1826 window_task_ids = {str(item.get("task_id") or "") for item in window_details}
1827 successful_window_task_ids = {
1828     str(item.get("task_id") or "")
1829     for item in window_details
1830     if item.get("n_success", 0) or float(item.get("avg_score") or 0.0) >= 0.9
1831 }
1834 source_ids = {str(item) for item in (artifact.metadata.get("source_task_ids") or [])}
1835 helpful_task_ids = {
1836     str(item.get("task_id") or item.get("source_task_id") or "")
1837     for item in artifact.evidence.helpful_cases
1838     if str(item.get("task_id") or item.get("source_task_id") or "")
1839 }
1842 source_success = bool(source_ids & successful_window_task_ids) or bool(artifact.metadata.get("source_success"))
1843 has_passing_test = any(
1844     bool((result.aggregate or {}).get("pass_all_tests") or (result.aggregate or {}).get("passed"))
1845     for result in store.test_results()
1846     if result.skill_name == artifact.name
1847 )
1848 helpful_credit_count = len({task_id for task_id in helpful_task_ids if task_id not in source_ids})
1849 has_posterior_helpful_credit = helpful_credit_count >= _spreadsheet_promotion_helpful_credit_threshold()
1850 if not artifact.bundle.positive_cases or not has_passing_test or not has_posterior_helpful_credit:
1851     artifact.metadata.setdefault("promotion_state", "pending")
1852     artifact.metadata["promotion_blocked_reason"] = (
1853         "requires_successful_source_posterior_helpful_credit_and_passing_bundle_test"
1854         if source_success
1855         else "requires_successful_source_or_passing_bundle_test"
1856     )
1857     artifact.metadata["promotion_helpful_credit_count"] = helpful_credit_count
1858     artifact.metadata["promotion_helpful_credit_threshold"] = _spreadsheet_promotion_helpful_credit_threshold()
1859     continue
1860 if store.promote_pending(
1869 def _spreadsheet_promotion_helpful_credit_threshold() -> int:
1871     return max(1, int(os.environ.get("SPREADSHEET_PROMOTION_HELPFUL_CREDIT_THRESHOLD", "1") or "1"))
```

### Refiner failure fallback no longer keeps high-confidence harmful skills

File: `academic/benchmarks/spreadsheet/maintenance/adapter.py`

```text
1400 except Exception as exc:
1401     if any(event.get("filter_candidate") for event in credit_context) or _spreadsheet_has_high_confidence_harmful_credit(credit_context):
1402         updated = _normalize_spreadsheet_injection_contract(copy.deepcopy(artifact))
1403         updated.status = "disabled"
1404         updated.metadata["disabled"] = True
1410         updated.metadata["disabled_reason"] = f"spreadsheet_refiner_unavailable_after_{reason_kind}:{type(exc).__name__}"
1411         return {
1413             "action": "disable",
1415             "updated_artifact": updated,
1437 def _spreadsheet_has_high_confidence_harmful_credit(credit_context: Sequence[Dict[str, Any]]) -> bool:
1439     if str(event.get("judgment") or "").strip().lower() != "harmful":
1441     if float(event.get("confidence") or 0.0) >= 0.85:
1442         return True
```

### Credit failure mode classification

File: `academic/benchmarks/spreadsheet/prompts.py`

```text
143 SPREADSHEET_CREDIT_SYSTEM = """\
167 - `failure_mode`: classify the main failure as one of:
168   `irrelevant_retrieval`, `skill_scope_too_broad`,
169   `skill_body_wrong_or_incomplete`, `executor_misuse`,
170   or `insufficient_evidence`.
223       "failure_mode": "irrelevant_retrieval | skill_scope_too_broad | skill_body_wrong_or_incomplete | executor_misuse | insufficient_evidence",
```

File: `academic/benchmarks/spreadsheet/maintenance/adapter.py`

```text
1045 def _normalize_spreadsheet_credit_events(
1081     "failure_mode": _normalize_spreadsheet_credit_failure_mode(row.get("failure_mode")),
1092 def _normalize_spreadsheet_credit_failure_mode(value: Any) -> str:
1095     allowed = {
1096         "irrelevant_retrieval",
1097         "skill_scope_too_broad",
1098         "skill_body_wrong_or_incomplete",
1099         "executor_misuse",
1100         "insufficient_evidence",
1101     }
```

### Callable code exposure

File: `academic/benchmarks/spreadsheet/skill_runtime.py`

```text
25 def write_spreadsheet_skill_library(
86                 "code": code,
111     if disclosure_mode in {"progressive", "signature", "signature_only"}:
132         return (
133             "Executable Spreadsheet skills are already importable from `skill_library`. "
134             "If a signature matches the task, you can complete the operation with one direct function call. "
135             "You may also inspect the implementation before deciding by running "
136             "`print(<function_name>_skill.code)` in a notebook cell, then either call the function or adapt/rewrite its code.\n"
145     rows = "\n".join(
148         f"- `{row['signature']}` from skill `{row['skill_name']}`: {row['description'][:220]}",
149         "  Full implementation:",
150         "  ```python",
151         "\n".join("  " + line for line in str(row.get("code") or "").splitlines()),
152         "  ```",
```

File: `academic/benchmarks/spreadsheet/executor.py`

```text
867 def _spreadsheet_callable_update_prompt(
928                     "  Full implementation:",
929                     "  ```python",
930                     "\n".join("  " + line for line in str(row.get("code") or "").splitlines()),
931                     "  ```",
```

### JSON retry

File: `academic/skill_repository/llm_maintenance.py`

```text
1026 async def _ask_json(
1040     max_attempts = max(1, int(os.environ.get("MAINTENANCE_JSON_MAX_ATTEMPTS", "3") or "3"))
1044     for attempt in range(1, max_attempts + 1):
1079         metadata={**dict(metadata or {}), "attempt": attempt, "max_attempts": max_attempts},
1112         metadata={**dict(metadata or {}), "parse_error": str(exc), "duration_ms": duration_ms, "usage": usage, "attempt": attempt},
1126         metadata={**dict(metadata or {}), "type_error": type(data).__name__, "duration_ms": duration_ms, "usage": usage, "attempt": attempt},
1141     raise ValueError(f"LLM did not return valid JSON after {max_attempts} attempts: {last_error}\nRaw: {last_response[:1000]}")
1147 def _json_retry_user_prompt(original_user: Any, *, error: str, previous_response: str) -> Any:
1149     "## JSON repair retry\n"
1150     "Your previous response could not be parsed as one strict JSON object.\n"
```

### Tests added/updated

File: `academic/benchmarks/tests/test_spreadsheet_evolution.py`

```text
123 def _passing_skill_test_result(artifact: SkillArtifact) -> SkillTestResult:
250 async def test_spreadsheet_callable_full_disclosure_includes_complete_code(monkeypatch, tmp_path: Path) -> None:
826 async def fake_bundle_tests(**kwargs: Any) -> SkillTestResult:
827     return _passing_skill_test_result(kwargs["artifact"])
893 async def fake_bundle_tests(**kwargs: Any) -> SkillTestResult:
894     return _passing_skill_test_result(kwargs["artifact"])
915 async def test_spreadsheet_extractor_rejects_candidate_that_fails_prestore_bundle(monkeypatch, tmp_path: Path) -> None:
964 assert store.get("spreadsheet_bad_double") is None
965 assert report["maintenance_targets"] == []
966 assert report["extraction_reports"][0]["status"] == "rejected"
967 assert report["extraction_reports"][0]["rejection_reason"] == "prestore_bundle_test_failed"
1479 async def test_spreadsheet_refiner_failure_disables_high_confidence_harmful_skill(monkeypatch, tmp_path: Path) -> None:
1498 async def failing_refiner(*args: Any, **kwargs: Any) -> Dict[str, Any]:
1499     raise ValueError("bad json")
1519 assert decision["action"] == "disable"
1520 assert decision["updated_artifact"].status == "disabled"
1521 assert decision["reason"] == "spreadsheet_refiner_unavailable_after_high_confidence_harmful_credit:ValueError"
1711 async def fake_bundle_tests(**kwargs: Any) -> SkillTestResult:
1712     return _passing_skill_test_result(kwargs["artifact"])
2073 async def test_spreadsheet_macro_requires_posterior_helpful_credit_for_promotion(tmp_path: Path) -> None:
2108 assert store.get("spreadsheet_source_only").status == "pending"
2123 assert store.get("spreadsheet_source_only").status == "active"
```

File: `academic/skill_repository/test_llm_maintenance_feedback.py`

```text
208 async def test_ask_json_retries_after_malformed_json(monkeypatch) -> None:
```

## What Is Fixed Now

Fixed in code:

1. Extractor prompt now tells the model maximum body/description sizes and a hard executable-code limit of 15 non-empty lines.
2. Complete code is required: code blocks are rejected when oversized instead of being truncated mid-block.
3. Extractor post-generation rubric rejects oversized artifacts and retries with `spreadsheet_extractor_rubric_rewrite`.
4. Artifacts still failing the rubric after rewrite are discarded.
5. Body trimming is field-aware instead of blind whole-string truncation, and it preserves a code block only when the full block fits the line limit.
6. New extracted candidates must pass pre-store bundle validation before `store.add_pending`.
7. Macro promotion now requires source success, at least one posterior helpful credit on a different task, and a passing bundle test.
8. Credit assigner now emits `failure_mode` to separate irrelevant retrieval, broad scope, bad/incomplete body, and executor misuse.
9. Callable full-disclosure prompts include complete implementation code; progressive prompts explicitly show direct-call examples and inspectable `.code`.
10. Refiner failure under high-confidence harmful credit disables instead of silently keeping.
11. JSON-valued LLM roles retry malformed/non-object JSON up to `MAINTENANCE_JSON_MAX_ATTEMPTS`, default 3.
12. Callable normalization now refuses non-parseable/truncated code as functional.

Still requires fresh experiment validation:

1. Run a new `20/0` train with role audit enabled to confirm bad transcript-like callable no longer enters the store.
2. Confirm `refiner_failed:ValueError` no longer leaves harmful high-confidence skills active/pending.
3. Confirm pre-store bundle gate cost is acceptable, since every extracted candidate now runs bundle validation.

## Verification

Commands run:

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py academic/skill_repository/test_llm_maintenance_feedback.py academic/benchmarks/tests/test_generic_evolution.py academic/method_validation/tests/test_store_retriever_refactor_contracts.py academic/method_validation/tests/test_offline_case_gap_coverage.py
```

Result:

```text
86 passed, 10 warnings
```
