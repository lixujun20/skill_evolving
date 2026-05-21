# BFCL Paper Heldout TRL Comparison Table

This document is intended as a paper-ready table source. It includes only complete fixed `50/50` runs or non-training baselines evaluated on the same fixed BFCL heldout set of 50 tasks. It intentionally excludes train accuracy and incomplete-training diagnostics such as `20/50` warm-up runs.

All rows use the fixed heldout manifest:

`academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`

## Recommended Paper Table

| Method | Training | TRL | Skill selection | Strict success | Official valid | Avg. score | Recall | Precision | Avg. tokens | Valid / Mtok |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| No-skill baseline | none | no | none | 0.08 | 0.48 | 0.7388 | 0.8181 | 0.7023 | 70.6k | 6.80 |
| SkillX aligned baseline | 50 | external | SkillX retrieval | 0.08 | 0.54 | 0.7679 | 0.8508 | 0.7140 | 78.8k | 6.85 |
| Ours, earlier train50 | 50 | earlier | deterministic | 0.08 | 0.70 | 0.7892 | 0.8955 | 0.7193 | 83.0k | 8.43 |
| Ours, no TRL | 50 | no | deterministic | **0.12** | **0.74** | 0.7901 | 0.8938 | 0.7235 | 81.7k | 9.06 |
| Ours, no TRL + LLM selector | 50 | no | LLM selector | 0.10 | 0.60 | 0.7763 | 0.8693 | 0.7139 | 74.9k | 8.01 |
| Ours, TRL before maturity gate | 50 | yes | LLM selector | 0.08 | 0.52 | 0.7474 | 0.8491 | 0.6850 | 76.7k | 6.78 |
| Ours, TRL + injector gate | 50 | yes | LLM selector gate | 0.08 | 0.60 | 0.7530 | 0.8371 | 0.7096 | 74.2k | 8.09 |
| Ours, TRL + maturity gate | 50 | yes | deterministic | 0.08 | **0.74** | **0.7984** | **0.9024** | **0.7266** | 80.8k | **9.16** |

## Main Takeaways

- The maturity-gated TRL run recovers heldout official validity from `0.52` to `0.74` compared with the earlier TRL run, while also improving average score from `0.7474` to `0.7984`.
- Maturity-gated TRL matches the best no-TRL official-valid rate (`0.74`) and achieves the best average score, recall, precision, and official-valid utility per million tokens among the complete runs.
- Strict exact success remains weaker for maturity-gated TRL (`0.08`) than the aligned no-TRL deterministic run (`0.12`). This is the main remaining gap.
- The LLM skill selector was not beneficial in these BFCL runs: no-TRL deterministic outperformed no-TRL with LLM selector, and the earlier TRL variants with LLM selector underperformed maturity-gated deterministic TRL.

## LaTeX Version

```latex
\begin{table}[t]
\centering
\small
\begin{tabular}{lccccccc}
\toprule
Method & TRL & Skill selection & Strict & Official & Avg. score & Avg. tokens & Valid/Mtok \\
\midrule
No-skill baseline & -- & none & 0.08 & 0.48 & 0.7388 & 70.6k & 6.80 \\
SkillX aligned baseline & external & SkillX retrieval & 0.08 & 0.54 & 0.7679 & 78.8k & 6.85 \\
Ours, earlier train50 & earlier & deterministic & 0.08 & 0.70 & 0.7892 & 83.0k & 8.43 \\
Ours, no TRL & no & deterministic & \textbf{0.12} & \textbf{0.74} & 0.7901 & 81.7k & 9.06 \\
Ours, no TRL + LLM selector & no & LLM selector & 0.10 & 0.60 & 0.7763 & 74.9k & 8.01 \\
Ours, TRL before maturity gate & yes & LLM selector & 0.08 & 0.52 & 0.7474 & 76.7k & 6.78 \\
Ours, TRL + injector gate & yes & LLM selector gate & 0.08 & 0.60 & 0.7530 & 74.2k & 8.09 \\
Ours, TRL + maturity gate & yes & deterministic & 0.08 & \textbf{0.74} & \textbf{0.7984} & 80.8k & \textbf{9.16} \\
\bottomrule
\end{tabular}
\caption{BFCL heldout results on the fixed 50-task test set. We report only complete 50-task training runs or no-training baselines. Train accuracy and incomplete-training diagnostics are omitted.}
\label{tab:bfcl-heldout-trl}
\end{table}
```

## Source Rows

| Method | Result file | Test n | Strict | Official valid | Avg. score | Recall | Precision | Avg. tokens | Input | Output | Avg. elapsed (s) | Avg. steps | Timeout |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| No-skill baseline | `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json` | 50 | 0.08 | 0.48 | 0.7388 | 0.8181 | 0.7023 | 70551.3 | 69514.6 | 1036.8 | 35.555 | 9.66 | 0.0 |
| SkillX aligned baseline | `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_result_with_elapsed.json` | 50 | 0.08 | 0.54 | 0.7679 | 0.8508 | 0.7140 | 78787.6 | 77726.6 | 1061.0 | 34.927 | 9.78 | 0.0 |
| Ours, earlier train50 | `academic/results/bfcl_train50_20260518_202840.json` | 50 | 0.08 | 0.70 | 0.7892 | 0.8955 | 0.7193 | 83043.9 | 81956.2 | 1087.8 | 36.932 | 10.18 | 0.0 |
| Ours, no TRL | `academic/results/bfcl_align_notrl_compete_detinj_20260520.json` | 50 | 0.12 | 0.74 | 0.7901 | 0.8938 | 0.7235 | 81663.5 | 80598.2 | 1065.3 | 35.683 | 10.16 | 0.0 |
| Ours, no TRL + LLM selector | `academic/results/bfcl_align_notrl_compete_llminj_20260520.json` | 50 | 0.10 | 0.60 | 0.7763 | 0.8693 | 0.7139 | 74948.4 | 73896.2 | 1052.3 | 67.044 | 9.94 | 0.0 |
| Ours, TRL before maturity gate | `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json` | 50 | 0.08 | 0.52 | 0.7474 | 0.8491 | 0.6850 | 76689.5 | 75624.8 | 1064.7 | 79.253 | 10.08 | 0.0 |
| Ours, TRL + injector gate | `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json` | 50 | 0.08 | 0.60 | 0.7530 | 0.8371 | 0.7096 | 74175.7 | 73152.7 | 1023.0 | 49.427 | 9.78 | 0.0 |
| Ours, TRL + maturity gate | `academic/results/bfcl_align_trl_maturity_train50_50_20260520.json` | 50 | 0.08 | 0.74 | 0.7984 | 0.9024 | 0.7266 | 80774.6 | 79684.5 | 1090.1 | 34.210 | 9.98 | 0.0 |

