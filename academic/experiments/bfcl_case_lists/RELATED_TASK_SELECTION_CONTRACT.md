# BFCL Related-Task Selection Contract

This contract freezes how the `Train50 + Heldout50` related-task split is built and reviewed for the BFCL overlap-refactor experiment.

## Scope

- Benchmark: `bfcl_v3`
- Data source: `bfcl_eval_bundle`
- Split seed: `42`
- Output manifest: `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`

## Programmatic Selection Rules

The manifest generator ranks all 200 BFCL tasks using the following fixed signals:

1. Shared tool families
2. Repeated identifier/lookup/ordering/argument failure families
3. Multi-turn workflow similarity

The current implementation records, per task:

- `domain`
- `failure_family`
- `tool_families`
- `tool_verbs`
- `why_related`
- `score`

The top 100 ranked tasks are frozen as the related-task pool.

- First 50 tasks: `train_task_ids`
- Next 50 tasks: `test_task_ids`

## Manual Review Checklist

The manifest is not allowed to drift silently after generation. Before accepting a new manifest revision, review:

1. Train/test task ids do not overlap.
2. Both splits contain 50 tasks.
3. Top-ranked tasks still show concentrated relatedness patterns rather than broad surface similarity.
4. Failure families remain interpretable for case-study writing.
5. Held-out tasks are still plausibly helped by train-side reusable skills, not duplicates of the same exact trace.

If manual review changes are needed, edit the persisted manifest file directly and treat that file as the canonical split for baseline and evolve runs.

## Rebuild Command

```bash
python -m academic.benchmarks.bfcl.related.experiment \
  --mode build-manifest \
  --manifest academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json \
  --data-source bfcl_eval_bundle
```

## Validation Command

```bash
python -m academic.benchmarks.bfcl.related.experiment \
  --mode validate-manifest \
  --manifest academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json \
  --data-source bfcl_eval_bundle
```

## Freeze Rule

Baseline and evolve comparisons must use the exact same persisted manifest file. `train50_ids.json` remains a legacy reference only and is not the main experiment split.
