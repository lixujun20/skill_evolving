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

### Spreadsheet 固定项

| 项 | 固定值 |
|---|---|
| train ids file | `academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json` |
| test ids file | `academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json` |
| train ids hash | `04c5121288e0e687` |
| test ids hash | `a309d0fe247a8d63` |
| train/test overlap | 0 |
| train prefix used in 20/0 debug | first 20 ids, hash `afbbdccb557d3dbb` |
| test prefix used in 50-test baseline | first 50 ids, hash `d355c557a14c329e` |
| model / llm_config | `claude-sonnet-4-5` / `local_claude_proxy` |
| notebook max turns | 5 |
| max_task_seconds | 180 |
| baseline test_concurrency | 4 |
| evolve train/micro/test concurrency | 4 / 8 / 4 |

Spreadsheet 主结果必须使用上述落盘 task id 文件。`shuffled_seed42_test_200.json` 只有 195 个 task，因为当前 verified/local fixture loader 能加载 395 个有效 task；不要补造 200 个 test id。

## BFCL

当前 BFCL 主比较口径改为 fixed heldout test 的 `official_valid` 与 `avg_score`，`success/strict` 只作为记录列保留，不用于当前结论。按该口径，最新 deterministic baseline (`bfcl_baseline_detinj_after_baseline_fixes_20260521_run2.json`) 的 heldout test 为 `official_valid=35/50=0.7000`、`avg_score=0.7993`。对比最新 group-refiner TRL test (`33/50=0.6600`, `avg_score=0.8021`)：baseline 的 official_valid 更高，TRL 的 avg_score 仅高 `+0.0028`。

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
| related50 baseline rerun | test | `academic/results/bfcl_related50_50_claude45proxy_official_related50_50_baseline_rerun_20260519.json` | 50 | 0.08 | 0.48 | 0.7388 | 0.8181 | 0.7023 | 70551.3 | 69514.6 | 1036.8 | 0.0 | fixed 50/50 baseline rerun；no skills |
| TRL competition warm-up 20/50 | test | `academic/results/bfcl_related20_50_sonnet_trl_20260520_evolve.json` | 50 | 0.06 | 0.62 | 0.7710 | 0.8595 | 0.7234 | 73770.5 | 72720.6 | 1049.9 | 0.0 | diagnostic：train prefix 20 + heldout 50；candidate_competition_enabled=true；prompt-injected 24/50；summary `academic/results/bfcl_related20_50_sonnet_trl_20260520_summary.json` |
| TRL main 50/50 | train | `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json` | 50 | 0.24 | 0.70 | 0.8324 | 0.8979 | 0.7939 | 61572.7 | 60485.4 | 1087.2 | 0.0 | completed；candidate_competition_enabled=true；candidate_sample_count=3 |
| TRL main 50/50 | test | `academic/results/bfcl_related50_50_sonnet_trl_20260520_evolve.json` | 50 | 0.08 | 0.52 | 0.7474 | 0.8491 | 0.6850 | 76689.5 | 75624.8 | 1064.7 | 0.0 | completed；fixed heldout 50；prompt_only |
| TRL injector-gate 50/50 | train | `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json` | 50 | 0.22 | 0.6531 | 0.8183 | 0.8744 | 0.7874 | 59604.8 | 58559.2 | 1045.6 | 0.0 | completed；LLM injector gate；candidate_competition_enabled=true |
| TRL injector-gate 50/50 | test | `academic/results/bfcl_related50_50_sonnet_trl_injector_gate_20260520_evolve.json` | 50 | 0.08 | 0.60 | 0.7530 | 0.8371 | 0.7096 | 74175.7 | 73152.7 | 1023.0 | 0.0 | completed；当前 best official valid；fixed heldout 50 |
| aligned no-TRL competition deterministic | train | `academic/results/bfcl_align_notrl_compete_detinj_20260520.json` | 50 | 0.26 | 0.62 | 0.8325 | 0.8917 | 0.7982 | 61739.7 | 60635.9 | 1103.8 | 0.0 | ablation；current code role-aligned；extractor TRL off；candidate_competition=true；deterministic injector；every-step retrieval；extractor_existing_artifacts=full_store |
| aligned no-TRL competition deterministic | test | `academic/results/bfcl_align_notrl_compete_detinj_20260520.json` | 50 | 0.12 | 0.74 | 0.7901 | 0.8938 | 0.7235 | 81663.5 | 80598.2 | 1065.3 | 0.0 | ablation；best listed heldout official_valid；no skill_injector LLM calls；avg_score below latest baseline by 0.0092 |
| aligned no-TRL competition LLM injector | train | `academic/results/bfcl_align_notrl_compete_llminj_20260520.json` | 50 | 0.26 | 0.66 | 0.8441 | 0.9035 | 0.8059 | 61484.3 | 60377.7 | 1106.6 | 0.0 | ablation；same as previous but BFCL_SKILL_INJECTOR_GATE=llm；809 total injector calls over train+test |
| aligned no-TRL competition LLM injector | test | `academic/results/bfcl_align_notrl_compete_llminj_20260520.json` | 50 | 0.10 | 0.60 | 0.7763 | 0.8693 | 0.7139 | 74948.4 | 73896.2 | 1052.3 | 0.0 | ablation；LLM injector improves train valid but hurts heldout vs deterministic |
| aligned TRL competition deterministic 20/50 | train | `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520.json` | 20 | 0.45 | 0.80 | 0.9114 | 0.9493 | 0.8972 | 49767.4 | 48747.8 | 1019.5 | 0.0 | debug warm-up；current code role-aligned；TRL on；candidate_competition=true；deterministic injector；checkpoint has completed prefix sidecars for continuation to 50 train |
| aligned TRL competition deterministic 20/50 | test | `academic/results/bfcl_align_trl_compete_detinj_train20_50_20260520.json` | 50 | 0.10 | 0.52 | 0.7565 | 0.8255 | 0.7189 | 73129.4 | 72095.9 | 1033.5 | 0.0 | debug warm-up；fixed heldout 50；no LLM skill_injector role；TRL feedback ran 2 updates |
| aligned TRL maturity-gated deterministic 20/50 | train | `academic/results/bfcl_align_trl_maturity_train20_50_20260520.json` | 20 | 0.45 | 0.75 | 0.9069 | 0.9410 | 0.8960 | 49712.9 | 48689.8 | 1023.0 | 0.0 | candidate-group-only TRL；maturity gate ratio=1.0；neutral-only loser archive bug fixed；checkpoint preserved for 50/50 continuation |
| aligned TRL maturity-gated deterministic 20/50 | test | `academic/results/bfcl_align_trl_maturity_train20_50_20260520.json` | 50 | 0.08 | 0.54 | 0.7655 | 0.8498 | 0.7102 | 73192.1 | 72147.4 | 1044.7 | 0.0 | 13/50 heldout prompt-injected；active skills only TradingBot direct symbol + market price；case analysis `academic/results/algorithm_docs/BFCL_TRL_MATURITY_20_50_CASE_ANALYSIS_20260520.md` |
| aligned TRL maturity-gated deterministic 50/50 | train | `academic/results/bfcl_align_trl_maturity_train50_50_20260520.json` | 50 | 0.26 | 0.64 | 0.8319 | 0.8849 | 0.8006 | 61436.7 | 60329.6 | 1107.1 | 0.0 | resumed from 20-task checkpoint；candidate-group-only TRL；maturity gate ratio=1.0；deterministic injector；no LLM skill_injector |
| aligned TRL maturity-gated deterministic 50/50 | test | `academic/results/bfcl_align_trl_maturity_train50_50_20260520.json` | 50 | 0.08 | 0.74 | 0.7984 | 0.9024 | 0.7266 | 80774.6 | 79684.5 | 1090.1 | 0.0 | heldout prompt-injected Vehicle/TradingBot/invoice skills；official_valid matches no-TRL deterministic；avg_score slightly higher |
| strictmeta TRL 20/50 | train | `academic/results/bfcl_trl_strictmeta_train20_50_20260520.json` | 20 | 0.45 | 0.70 | 0.8974 | 0.9243 | 0.8918 | 47634.7 | 46637.2 | 997.4 | 0.0 | strict harmful gate；三角色 meta-feedback：extractor/refiner/refactorer；soft promotion/backup；candidate rows 2 |
| strictmeta TRL 20/50 | test | `academic/results/bfcl_trl_strictmeta_train20_50_20260520.json` | 50 | 0.10 | 0.54 | 0.7602 | 0.8418 | 0.7117 | 71052.2 | 70013.8 | 1038.4 | 0.0 | fixed heldout 50；37 skills；prompt_only；called_skill_tools empty |
| strictmeta TRL 50/50 | train | `academic/results/bfcl_trl_strictmeta_train50_50_20260520.json` | 50 | 0.22 | 0.64 | 0.8428 | 0.8937 | 0.8128 | 60965.8 | 59874.9 | 1090.9 | 0.0 | full 50 train；candidate rows 15；decisions 71；role feedback rules extractor/refiner/refactorer = 5/5/5 |
| strictmeta TRL 50/50 | test | `academic/results/bfcl_trl_strictmeta_train50_50_20260520.json` | 50 | 0.10 | 0.66 | 0.7965 | 0.8824 | 0.7423 | 74365.6 | 73309.4 | 1056.2 | 0.0 | fixed heldout 50；161 skills；top injected: symbol binding, brake/start, invoice, order verification |
| baseline after fixes deterministic | train | `academic/results/bfcl_baseline_detinj_after_baseline_fixes_20260521_run2.json` | 50 | 0.26 | 0.62 | 0.8264 | 0.8882 | 0.7899 | 62233.1 | 61115.8 | 1117.3 | 0.0 | baseline sanity；same aligned no-TRL deterministic setting；extractor TRL off；candidate rows 7；19 skills |
| baseline after fixes deterministic | test | `academic/results/bfcl_baseline_detinj_after_baseline_fixes_20260521_run2.json` | 50 | 0.12 | 0.70 | 0.7993 | 0.8985 | 0.7330 | 81423.4 | 80334.9 | 1088.6 | 0.0 | latest baseline；主口径 official_valid 35/50、avg_score 0.7993；official_valid above group-refiner TRL 33/50，avg_score below TRL by 0.0028 |
| group-refiner TRL 50/50 | train | `academic/results/bfcl_trl_group_refiner_50_50_20260521.json` | 50 | 0.22 | 0.70 | 0.8338 | 0.8999 | 0.7978 | 63668.7 | 62561.4 | 1107.3 | 0.0 | extractor-only TRL with LLM group_refiner；candidate rows 9；283 skills；group_refiner fallback once due JSON/max-token ValueError |
| group-refiner TRL 50/50 | test | `academic/results/bfcl_trl_group_refiner_50_50_20260521.json` | 50 | 0.08 | 0.66 | 0.8021 | 0.8982 | 0.7353 | 78353.5 | 77282.1 | 1071.4 | 0.0 | fixed heldout 50；avg_score best among listed TRL rows and +0.0028 vs latest baseline；official_valid 33/50 below latest baseline 35/50；no skill_injector LLM |
| SkillX fresh no-memory baseline | test | `academic/results/skillx_bfcl_aligned/skillx_bfcl_no_memory_baseline_50_50_20260521.json` | 50 | 0.08 | 0.50 | 0.7446 | 0.8169 | 0.7066 | 69286.1 | 68271.2 | 1014.9 | 0.0 | same SkillX-aligned manifest；fresh no-skill baseline；all logs show `skill_injection_mode=none` |
| SkillX aligned 50/50 | test | `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/skillx_bfcl_result_with_elapsed.json` | 50 | 0.08 | 0.54 | 0.7679 | 0.8508 | 0.7140 | 78787.6 | 77726.6 | 1061.0 | 0.0 | completed diagnostic；plan_with_skill；max_skills=10；uses hash embedding fallback；caveat `academic/results/skillx_bfcl_aligned/skillx_bfcl_50_50_sonnet_hash_embed_20260520/embedding_caveat.json` |

### BFCL 2026-05-20 fixed 50/50 完整指标

这些行都使用同一个 fixed manifest：`academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`。当前 BFCL 主结论优先比较 `official_valid` 与 `avg_score`；`strict` 是 task-level exact success，作为原始记录列保留但不作为当前主口径。`official_valid` 是 BFCL official checker 口径；`score/Mtok` 和 `valid/Mtok` 来自 `utility_per_million_tokens`。

| run | phase | n | strict | official_valid | avg_score | recall | precision | avg_tokens | input | output | elapsed_s | model_steps | score/Mtok | valid/Mtok | total_tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline test | test | 50 | `4/50 = 0.08` | `24/50 = 0.4800` | 0.7388 | 0.8181 | 0.7023 | 70551.3 | 69514.6 | 1036.8 | 35.555 | 9.66 | 10.471129 | 6.803558 | 3527566 |
| TRL train | train | 50 | `12/50 = 0.24` | `35/50 = 0.7000` | 0.8324 | 0.8979 | 0.7939 | 61572.7 | 60485.4 | 1087.2 | 65.506 | 9.28 | 13.519048 | 11.368678 | 3078634 |
| TRL test | test | 50 | `4/50 = 0.08` | `26/50 = 0.5200` | 0.7474 | 0.8491 | 0.6850 | 76689.5 | 75624.8 | 1064.7 | 79.253 | 10.08 | 9.745293 | 6.780586 | 3834477 |
| TRL injector train | train | 50 | `11/50 = 0.22` | `32/50 = 0.6531` | 0.8183 | 0.8744 | 0.7874 | 59604.8 | 58559.2 | 1045.6 | 46.364 | 9.14 | 13.729067 | 10.737394 | 2980239 |
| TRL injector test | test | 50 | `4/50 = 0.08` | `30/50 = 0.6000` | 0.7530 | 0.8371 | 0.7096 | 74175.7 | 73152.7 | 1023.0 | 49.427 | 9.78 | 10.151815 | 8.088902 | 3708785 |
| aligned no-TRL deterministic train | train | 50 | `13/50 = 0.26` | `31/50 = 0.6200` | 0.8325 | 0.8917 | 0.7982 | 61739.7 | 60635.9 | 1103.8 | 32.637 | 9.22 | 13.484590 | 10.042167 | 3086983 |
| aligned no-TRL deterministic test | test | 50 | `6/50 = 0.12` | `37/50 = 0.7400` | 0.7901 | 0.8938 | 0.7235 | 81663.5 | 80598.2 | 1065.3 | 35.683 | 10.16 | 9.675113 | 9.061571 | 4083177 |
| aligned no-TRL LLM-injector train | train | 50 | `13/50 = 0.26` | `33/50 = 0.6600` | 0.8441 | 0.9035 | 0.8059 | 61484.3 | 60377.7 | 1106.6 | 54.196 | 9.26 | 13.727927 | 10.734448 | 3074215 |
| aligned no-TRL LLM-injector test | test | 50 | `5/50 = 0.10` | `30/50 = 0.6000` | 0.7763 | 0.8693 | 0.7139 | 74948.4 | 73896.2 | 1052.3 | 67.044 | 9.94 | 10.358110 | 8.005506 | 3747421 |
| aligned TRL deterministic 20/50 train | train | 20 | `9/20 = 0.45` | `16/20 = 0.8000` | 0.9114 | 0.9493 | 0.8972 | 49767.4 | 48747.8 | 1019.5 | 30.405 | 9.10 | 18.313494 | 16.074780 | 995348 |
| aligned TRL deterministic 20/50 test | test | 50 | `5/50 = 0.10` | `26/50 = 0.5200` | 0.7565 | 0.8255 | 0.7189 | 73129.4 | 72095.9 | 1033.5 | 32.416 | 9.62 | 10.344622 | 7.110683 | 3656470 |
| strictmeta TRL 20/50 train | train | 20 | `9/20 = 0.45` | `14/20 = 0.7000` | 0.8974 | 0.9243 | 0.8918 | 47634.7 | 46637.2 | 997.4 | 37.511 | 9.05 | 18.839332 | 14.695185 | 952693 |
| strictmeta TRL 20/50 test | test | 50 | `5/50 = 0.10` | `27/50 = 0.5400` | 0.7602 | 0.8418 | 0.7117 | 71052.2 | 70013.8 | 1038.4 | 38.094 | 9.68 | 10.698579 | 7.600042 | 3552612 |
| strictmeta TRL 50/50 train | train | 50 | `11/50 = 0.22` | `32/50 = 0.6400` | 0.8428 | 0.8937 | 0.8128 | 60965.8 | 59874.9 | 1090.9 | 46.282 | 9.14 | 13.824828 | 10.497685 | 3048291 |
| strictmeta TRL 50/50 test | test | 50 | `5/50 = 0.10` | `33/50 = 0.6600` | 0.7965 | 0.8824 | 0.7423 | 74365.6 | 73309.4 | 1056.2 | 48.147 | 9.84 | 10.710818 | 8.875076 | 3718278 |
| baseline after fixes train | train | 50 | `13/50 = 0.26` | `31/50 = 0.6200` | 0.8264 | 0.8882 | 0.7899 | 62233.1 | 61115.8 | 1117.3 | 34.035 | 9.26 | 13.279075 | 9.962544 | 3111655 |
| baseline after fixes test | test | 50 | `6/50 = 0.12` | `35/50 = 0.7000` | 0.7993 | 0.8985 | 0.7330 | 81423.4 | 80334.9 | 1088.6 | 35.294 | 10.18 | 9.816019 | 8.597033 | 4071172 |
| group-refiner TRL train | train | 50 | `11/50 = 0.22` | `35/50 = 0.7000` | 0.8338 | 0.8999 | 0.7978 | 63668.7 | 62561.4 | 1107.3 | 34.666 | 9.24 | 13.095728 | 10.994413 | 3183435 |
| group-refiner TRL test | test | 50 | `4/50 = 0.08` | `33/50 = 0.6600` | 0.8021 | 0.8982 | 0.7353 | 78353.5 | 77282.1 | 1071.4 | 33.366 | 9.84 | 10.236766 | 8.423368 | 3917673 |
| SkillX fresh no-memory baseline | test | 50 | `4/50 = 0.08` | `25/50 = 0.5000` | 0.7446 | 0.8169 | 0.7066 | 69286.1 | 68271.2 | 1014.9 | 32.570 | 9.54 | 10.746914 | 7.216453 | 3464306 |
| SkillX test | test | 50 | `4/50 = 0.08` | `27/50 = 0.5400` | 0.7679 | 0.8508 | 0.7140 | 78787.6 | 77726.6 | 1061.0 | 34.927 | 9.78 | 9.746531 | 6.853869 | 3939381 |

## Spreadsheet

| run | phase | file | n | success | avg_score | avg_tokens | input | output | timeout | 备注 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| baseline old | test | `academic/results/spreadsheet_baseline_test50_20260517_233447.json` | 50 | 0.22 | 0.2564 | 1552.1 | - | - | 0.0 | 早期 baseline |
| evolve 50/50 | train | `academic/results/spreadsheet_evolve_50_50_true_20260518_022120.json` | 50 | 0.24 | 0.2827 | 3460.0 | - | - | - | generic online evolve |
| evolve 50/50 | test | `academic/results/spreadsheet_evolve_50_50_true_20260518_022120.json` | 50 | 0.24 | 0.3208 | 3748.2 | - | - | - | learned skill test |
| notebook baseline test50 | test | `academic/results/spreadsheet_notebook_baseline_test50_0519.json` | 50 | 0.28 | 0.3600 | 7151.4 | 5596.2 | 1555.2 | 0.0 | notebook max_turns=5；test_concurrency=4；top_k=0；elapsed 483.784s |
| notebook evolve 50/50 speedup | train | `academic/results/spreadsheet_0519-speedup.json` | 50 | 0.26 | 0.3176 | 13816.5 | 11695.0 | 2121.6 | 0.0 | notebook max_turns=5；train/micro/test concurrency=4/8/4；top_k=3；observed wall-clock 7740.0s |
| notebook evolve 50/50 speedup | test | `academic/results/spreadsheet_0519-speedup.json` | 50 | 0.28 | 0.3350 | 13557.4 | 11594.8 | 1962.5 | 0.0 | vs 20260518 evolve test：success +0.04，avg_score +0.0142；完整对比见 changelog |
| fixedsplit notebook train20 debug | train | `academic/results/spreadsheet_0520-fixedsplit-train20-debug.json` | 20 | 0.30 | 0.3397 | 10979.7 | - | - | 0.0 | fixed split train prefix hash `afbbdccb557d3dbb`；验证 injection/promotion/filter bugfix |
| fixedsplit train20 post-fix progressive pending | train | `academic/results/spreadsheet_0520-postfix-train20-progressive-pending.json` | 20 | 0.30 | 0.3397 | 11910.8 | 9960.8 | 1950.0 | 0.0 | fixed split hash `afbbdccb557d3dbb`；no callable exposed/called；1 pending skill became disabled after 9 harmful credits；maintenance tokens 657583 |
| fixedsplit notebook baseline test50 | test | `academic/results/spreadsheet_0520-fixedsplit-notebook-baseline-test50.json` | 50 | 0.26 | 0.3148 | 7645.1 | 5802.7 | 1842.3 | 0.0 | fixed split test prefix hash `d355c557a14c329e`；top_k=0；elapsed 540.592s |
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
| notebook baseline test50 | `academic/results/spreadsheet_notebook_baseline_test50_0519.json` | 50 | 0.28 | 0.3600 | 7151.4 | 5596.2 | 1555.2 | 483.784 | baseline；avg turns 2.96，max turns 5，partial `academic/results/spreadsheet_notebook_baseline_test50_0519_partial.json` |
| notebook evolve 50/50 speedup train | `academic/results/spreadsheet_0519-speedup.json` | 50 | 0.26 | 0.3176 | 13816.5 | 11695.0 | 2121.6 | 7740.0 observed | 主实验；train/micro/test concurrency=4/8/4；maintenance tokens 1511532 |
| notebook evolve 50/50 speedup test | `academic/results/spreadsheet_0519-speedup.json` | 50 | 0.28 | 0.3350 | 13557.4 | 11594.8 | 1962.5 | 7740.0 observed | 主实验 heldout；partial/resume 文件 `academic/results/spreadsheet_0519-speedup_partial.json` |
| fixedsplit train20 debug | `academic/results/spreadsheet_0520-fixedsplit-train20-debug.json` | 20 | 0.30 | 0.3397 | 10979.7 | - | - | 1860.305 | fixed train prefix；active failed-source skills = 0；pending blocked = 8；called skill functions = 0 |
| fixedsplit train20 post-fix progressive pending | `academic/results/spreadsheet_0520-postfix-train20-progressive-pending.json` | 20 | 0.30 | 0.3397 | 11910.8 | 9960.8 | 1950.0 | 1967.315 | fixed train prefix；called/inspect/import = 0；bad pending skill disabled；pre-store rejected 6 candidates |
| fixedsplit baseline test50 | `academic/results/spreadsheet_0520-fixedsplit-notebook-baseline-test50.json` | 50 | 0.26 | 0.3148 | 7645.1 | 5802.7 | 1842.3 | 540.592 | fixed test prefix；top_k=0；errors/timeouts = 0 |

## SkillsBench

| run | file | n | success | avg_score | avg_tokens | input | output | elapsed_s | 备注 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| mock smoke | `academic/results/skillsbench_mock_smoke_20260518.json` | 4 | 1.0 | 1.0 | 338.8 | 288.2 | 50.5 | 0.088 | mock harness |
| baseline fixture3 | `academic/results/skillsbench_baseline_fixture3_20260518.json` | 3 | 1.0 | 1.0 | 702.3 | 448.0 | 254.3 | 19.94 | fixture baseline |
| curated mock diag | `academic/results/skillsbench_curated_mock_diag_20260518.json` | 5 | 0.2 | 0.3 | 346.4 | 297.4 | 49.0 | 0.21 | curated mock diagnostic |

## 2026-05-22 Overnight Runs

### BFCL Meta5 TRL / Ablations

这些行使用 fixed manifest `academic/experiments/bfcl_case_lists/curated_related_manifest_50_50.json`。主比较仍优先看 `official_valid` 和 `avg_score`；`strict` 是 exact task success。

| run | phase | file | n | strict | official_valid | avg_score | avg_tokens | model_steps | timeout | 备注 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Meta5 TRL initial | train | `academic/results/bfcl_trl_refiner_candidates_meta5_50_50_20260522.json` | 50 | `13/50 = 0.26` | 0.66 | 0.8251 | 64259.6 | 9.18 | 0.0 | role feedback called but role rules stayed empty; diagnostic predecessor |
| Meta5 TRL initial | test | `academic/results/bfcl_trl_refiner_candidates_meta5_50_50_20260522.json` | 50 | `7/50 = 0.14` | 0.70 | 0.8083 | 81098.8 | 9.82 | 0.0 | official_valid ties latest deterministic baseline; avg_score higher |
| Meta5 TRL rulesfix | train | `academic/results/bfcl_trl_refiner_candidates_meta5_rulesfix_50_50_20260522.json` | 50 | `12/50 = 0.24` | 0.68 | 0.8266 | 63693.5 | 9.24 | 0.0 | main successful Meta5 run |
| Meta5 TRL rulesfix | test | `academic/results/bfcl_trl_refiner_candidates_meta5_rulesfix_50_50_20260522.json` | 50 | `6/50 = 0.12` | 0.76 | 0.8144 | 82875.1 | 10.20 | 0.0 | best current BFCL heldout official_valid and avg_score in this group |
| Meta5 -refactor | train | `academic/results/bfcl_trl_meta5_ablation_no_refactor_50_50_20260522.json` | 50 | `10/50 = 0.20` | 0.48 | 0.7828 | 62361.3 | 9.32 | 0.0 | clean ablation; refactor disabled |
| Meta5 -refactor | test | `academic/results/bfcl_trl_meta5_ablation_no_refactor_50_50_20260522.json` | 50 | `3/50 = 0.06` | 0.48 | 0.7461 | 70454.4 | 9.68 | 0.0 | refactor removal collapses heldout official_valid |
| Meta5 -refine diagnostic | train | `academic/results/bfcl_trl_meta5_ablation_no_refine_50_50_20260522.json` | 50 | `12/50 = 0.24` | 0.70 | 0.8369 | 65198.9 | 9.32 | 0.0 | not clean: one refine path remained active |
| Meta5 -refine diagnostic | test | `academic/results/bfcl_trl_meta5_ablation_no_refine_50_50_20260522.json` | 50 | `2/50 = 0.04` | 0.74 | 0.7988 | 83481.1 | 10.18 | 0.0 | diagnostic only; do not use as clean ablation |
| Meta5 -refine clean | train | `academic/results/bfcl_trl_meta5_ablation_no_refine_clean_50_50_20260522.json` | 50 | `12/50 = 0.24` | 0.70 | 0.8293 | 63632.9 | 9.12 | 0.0 | clean refine-disabled ablation |
| Meta5 -refine clean | test | `academic/results/bfcl_trl_meta5_ablation_no_refine_clean_50_50_20260522.json` | 50 | `7/50 = 0.14` | 0.72 | 0.7949 | 80666.2 | 9.82 | 0.0 | refine contributes about +0.04 official_valid and +0.0195 avg_score vs full |

### Spreadsheet Bash / Folder Skill

这些行使用 fixed Spreadsheet split files `shuffled_seed42_train_200.json` / `shuffled_seed42_test_200.json`，`bash_react` executor，folder skill format。`called` 统计为命令层直接读取/import skill package 文件，不把 prompt metadata 计为使用。

| run | phase | file | n | success | avg_score | avg_tokens | input | output | elapsed_s | skills | called/copy evidence | 备注 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| folder generalized direct strict | train | `academic/results/spreadsheet_0521-folder-generalized-direct-strict-50_50.json` | 50 | `11/50 = 0.22` | 0.3140 | 37874.3 | 33890.8 | 3983.5 | 19500 observed | 13 total / 12 pending / 1 disabled | not rechecked here | long full run; no TRL |
| folder generalized direct strict | test | `academic/results/spreadsheet_0521-folder-generalized-direct-strict-50_50.json` | 50 | `17/50 = 0.34` | 0.3782 | 31944.9 | 28852.6 | 3092.3 | 19500 observed | 13 total / 12 pending / 1 disabled | not rechecked here | best strict among these Spreadsheet folder rows |
| folder + Spreadsheet TRL | train | `academic/results/spreadsheet_trl_folder_bash_50_50_20260522.json` | 50 | `11/50 = 0.22` | 0.3151 | 35483.4 | 31595.4 | 3888.0 | 13920 observed | 8 pending | 0 command-level use | TRL enabled but no feedback rows |
| folder + Spreadsheet TRL | test | `academic/results/spreadsheet_trl_folder_bash_50_50_20260522.json` | 50 | `15/50 = 0.30` | 0.3755 | 32637.9 | 29390.8 | 3247.1 | 13920 observed | 8 pending | 3 tasks read/import/copy skill package code; 0 callable calls | completed after adding micro-maintenance timeout guard |
| Spreadsheet SkillX aligned | test | `academic/results/skillx_spreadsheet_aligned/skillx_spreadsheet_bash_react_50_50_20260522/skillx_spreadsheet_test_result.json` | 50 | `17/50 = 0.34` | 0.3969 | 31645.2 | 27946.6 | 3698.6 | - | SkillX library | selected skills on 48/50 tasks | strongest Spreadsheet heldout avg_score among this set |
| bash fixedsplit baseline | test | `academic/results/spreadsheet_0520-fixedsplit-bash-react-baseline-test50.json` | 50 | `14/50 = 0.28` | 0.3003 | - | - | - | - | 0 | none | paired no-skill baseline |
| bash promptfix baseline | test | `academic/results/spreadsheet_0520-bash-react-baseline-test50-promptfix.json` | 50 | `15/50 = 0.30` | 0.3582 | - | - | - | - | 0 | none | prompt-fixed baseline |

Diagnostic only:

| run | file | n | result | note |
|---|---|---:|---|---|
| Spreadsheet +TRL smoke | `academic/results/spreadsheet_trl_smoke4_2_20260522.json` | train 4 / test 2 | completed; 1 pending skill; TRL enabled but `n_feedback_rows=0` | validated resume + micro-maintenance timeout guard before full 50/50 |

## 读表注意

- BFCL 的 `success/pass_at_k` 是最严格 task-level pass；`official_valid` 是 BFCL official runner 口径，二者可能不一致。
- `avg_score` 更接近 partial credit/call-level F1，BFCL 诊断时必须和 recall/precision 一起看。
- 旧结果没有 input/output 细分，表中记为 `-`。
- `guardfix evolve test` 的 timeout_rate 为 0.60，因此它的低 token 和高 official_valid 不能直接解释为更优。
- `cost_retest_bfcl_*` 三行是混合 task set 的 cost diagnostic，不是固定 curated heldout 50；其 exact success 不进入主结论。
- `spreadsheet_0519-speedup.json` 的顶层 `elapsed_s=0.647` 是最后一次 resume 后重新汇总 summary 的进程耗时，不是完整运行耗时；完整对比使用 `observed_wall_clock_s=7740.0`。
- `spreadsheet_notebook_baseline_test50_0519.log` 里前半段包含一次修复前中断尝试；主表只采用完整 JSON `spreadsheet_notebook_baseline_test50_0519.json`。
- `spreadsheet_notebook_baseline_test50_0519.json` 和 `spreadsheet_0519-speedup.json` 的 test set 不对齐，只能作为诊断对比；从 `0520-fixedsplit-*` 开始，Spreadsheet notebook baseline/evolve 必须使用固定 task id 文件。
