# 实验结果总表

本文档只记录已经落盘、可以从文件复查的结果。所有路径均相对仓库根目录。相同 benchmark 放在一起，便于比较参数与指标。

## 固定实验协议

主实验只比较固定 split 与固定非关键参数下的结果。诊断实验可以保留在表中，但必须明确标注，不进入主结论横比。

### BFCL 固定项

| 项 | 固定值 |
|---|---|
| 主 manifest | `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json` |
| train ids hash | `5d8d5179e3536f32` |
| test ids hash | `0ab3f3f8d6572175` |
| train/test overlap | 0 |
| train order | manifest order，不 shuffle |
| test order | manifest order；并发只影响 wall-clock，不改变汇总顺序 |
| model / llm_config | `claude-sonnet-4-5` / `local_claude_proxy` |
| epochs | 1 |
| micro / macro step | 1 / 10 |
| top_k_skills | 2 |
| skill_injection_mode | `prompt_only` |
| candidate_trial_retrieval | true |
| candidate_competition_enabled | false |
| max_steps_per_turn | 20 |
| max_task_seconds | 180 |
| train_window_concurrency / micro_concurrency / test_concurrency | 4 / 4 / 4 |

BFCL 的 split 不是每次随机选。`curated_related_manifest_50_50.json` 已冻结；主 evolve 路径按 `train_task_ids` 顺序训练，不 shuffle。若使用其他 task list，必须在表中标为 diagnostic 或 ablation。

## BFCL

| run | phase | file | n | success | official_valid | avg_score | recall | precision | avg_tokens | input | output | timeout | 备注 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| guardfix baseline | test | `academic/results/claude_proxy_related50_50_guardfix_20260517_232531_baseline.json` | 50 | 0.06 | 0.44 | 0.7312 | 0.8099 | 0.6946 | 70323.8 | - | - | 0.0 | 用户记得的 valid 约 0.4 来源 |
| guardfix evolve | train | `academic/results/claude_proxy_related50_50_guardfix_20260517_232531_evolve.json` | 50 | 0.24 | 0.62 | 0.8262 | 0.8794 | 0.7962 | 62468.9 | - | - | 0.0 | 上次有记录 evolve train |
| guardfix evolve | test | `academic/results/claude_proxy_related50_50_guardfix_20260517_232531_evolve.json` | 50 | 0.02 | 0.80 | 0.3314 | 0.3718 | 0.3018 | 41788.7 | - | - | 0.60 | heldout test 异常，timeout 高 |
| trainedstore rerun2 | test | `academic/results/bfcl_guardfix_trainedstore_test50_rerun2_20260518_012134.json` | 50 | 0.08 | 0.74 | 0.7991 | 0.8987 | 0.7314 | 86813.3 | - | - | 0.0 | 用训练后 frozen store 重测 |
| cost baseline | diagnostic mixed-set | `academic/results/cost_retest_bfcl_baseline_20260518.json` | 50 | 0.18 | 0.4898 | 0.7339 | 0.7958 | 0.6983 | 63632.5 | 62672.9 | 959.6 | 0.0 | cost ablation；不是 curated heldout 50 |
| cost fullskill | diagnostic mixed-set | `academic/results/cost_retest_bfcl_fullskill_20260518.json` | 50 | 0.22 | 0.66 | 0.7968 | 0.8766 | 0.7493 | 74711.6 | 73700.7 | 1010.9 | 0.0 | mixed set：17 train / 10 test / 23 manifest 外 |
| cost compact | diagnostic mixed-set | `academic/results/cost_retest_bfcl_compact_20260518.json` | 50 | 0.22 | 0.64 | 0.7937 | 0.8666 | 0.7511 | 71810.7 | 70812.6 | 998.1 | 0.0 | mixed set：17 train / 10 test / 23 manifest 外 |
| latest train50 | train | `academic/results/bfcl_train50_20260518_202840.json` | 50 | 0.20 | 0.58 | 0.8090 | 0.8789 | 0.7697 | 62339.2 | 61247.4 | 1091.8 | 0.0 | 最新完整 run |
| latest train50 | test | `academic/results/bfcl_train50_20260518_202840.json` | 50 | 0.08 | 0.70 | 0.7892 | 0.8955 | 0.7193 | 83043.9 | 81956.2 | 1087.8 | 0.0 | 最新完整 run heldout |

## Spreadsheet

| run | phase | file | n | success | avg_score | avg_tokens | input | output | timeout | 备注 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| baseline old | test | `academic/results/spreadsheet_baseline_test50_20260517_233447.json` | 50 | 0.22 | 0.2564 | 1552.1 | - | - | 0.0 | 早期 baseline |
| evolve 50/50 | train | `academic/results/spreadsheet_evolve_50_50_true_20260518_022120.json` | 50 | 0.24 | 0.2827 | 3460.0 | - | - | - | generic online evolve |
| evolve 50/50 | test | `academic/results/spreadsheet_evolve_50_50_true_20260518_022120.json` | 50 | 0.24 | 0.3208 | 3748.2 | - | - | - | learned skill test |
| cost baseline | test | `academic/results/cost_retest_sheet_baseline_20260518.json` | 50 | 0.26 | 0.3189 | 1621.3 | 747.9 | 873.5 | 0.0 | 新 cost accounting |
| cost fullskill | test | `academic/results/cost_retest_sheet_fullskill_20260518.json` | 50 | 0.36 | 0.4501 | 3816.3 | 3006.0 | 810.4 | 0.0 | full skill 最好但 input 高 |
| cost compact | test | `academic/results/cost_retest_sheet_compact_20260518.json` | 50 | 0.24 | 0.3186 | 1873.2 | 1038.7 | 834.5 | 0.0 | compact 丢失 function 信息 |
| compact callable v1 | test | `academic/results/cost_retest_sheet_compact_callable_20260518.json` | 50 | 0.22 | 0.2895 | 1925.8 | 1072.3 | 853.5 | 0.0 | callable wrapper v1 |
| compact callable v2 | test | `academic/results/cost_retest_sheet_compact_callable_v2_20260518.json` | 50 | 0.28 | 0.3385 | 2010.4 | 1126.9 | 883.5 | 0.0 | parser/wrapper 修复后略高于 baseline |

## Spreadsheet Notebook

| run | file | n | success | avg_score | avg_tokens | input | output | elapsed_s | 备注 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| notebook smoke | `academic/results/spreadsheet_notebook_smoke_1_20260518.json` | 1 | 0.0 | 0.0 | 3879.0 | 2787.0 | 1092.0 | 18.553 | 多轮 executor smoke |
| notebook hard | `academic/results/spreadsheet_notebook_hard_55427_20260518.json` | 1 | 0.0 | 0.0 | 6950.0 | 5788.0 | 1162.0 | 26.124 | hard case |
| notebook hard direct | `academic/results/spreadsheet_notebook_hard_55427_direct_20260518.json` | 1 | - | - | - | - | - | - | relative workdir 修复验证 |
| notebook hard direct v2 | `academic/results/spreadsheet_notebook_hard_55427_direct_v2_20260518.json` | 1 | - | - | - | - | - | - | direct wrapper 验证 |

## SkillsBench

| run | file | n | success | avg_score | avg_tokens | input | output | elapsed_s | 备注 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| mock smoke | `academic/results/skillsbench_mock_smoke_20260518.json` | 4 | 1.0 | 1.0 | 338.8 | 288.2 | 50.5 | 0.088 | mock harness |
| baseline fixture3 | `academic/results/skillsbench_baseline_fixture3_20260518.json` | 3 | 1.0 | 1.0 | 702.3 | 448.0 | 254.3 | 19.94 | fixture baseline |
| curated mock diag | `academic/results/skillsbench_curated_mock_diag_20260518.json` | 5 | 0.2 | 0.3 | 346.4 | 297.4 | 49.0 | 0.21 | curated mock diagnostic |

## 读表注意

- BFCL 的 `success/pass_at_k` 是最严格 task-level pass；`official_valid` 是 BFCL official runner 口径，二者可能不一致。
- `avg_score` 更接近 partial credit/call-level F1，BFCL 诊断时必须和 recall/precision 一起看。
- 旧结果没有 input/output 细分，表中记为 `-`。
- `guardfix evolve test` 的 timeout_rate 为 0.60，因此它的低 token 和高 official_valid 不能直接解释为更优。
- `cost_retest_bfcl_*` 三行是混合 task set 的 cost diagnostic，不是固定 curated heldout 50；其 exact success 不进入主结论。
