#!/usr/bin/env python3
"""
Reorganize academic/results/ into one folder per experiment.
Every folder gets:
  - the relevant JSON / log files moved in
  - README.md with settings and diff-vs-prior
  - run.sh reconstructing the command
Keeps these at the root:
  - aime_experiment_report.md
  - EXPERIMENT_AUDIT.md
"""
import json, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
assert ROOT.name == "results", ROOT

# Experiment metadata: tag -> (folder, file_prefixes, era, meta)
# era: "A-legacy" | "A-emb" | "B-v2"
EXPERIMENTS = [
    # ───── Era A — single-shot exec (pre 1c91edb rewrite) ─────
    ("math_experiment", dict(
        folder="01_math_experiment__legacy_singleshot",
        prefixes=["math_experiment_"],
        era="A-legacy",
        dataset="MATH-500 (15-problem subset)",
        model="Pro/zai-org/GLM-4.7 via SiliconFlow",
        temperature="default (~0.0)",
        max_tokens="API default",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full code body in prompt",
        pattern="single-shot exec(code, namespace)",
        summary="baseline 100%, with-skills 100%, evolve→76.7% (regressed at test)",
        diff_prev="first commit; reference implementation",
    )),
    ("baseline_complete", dict(
        folder="02_baseline_complete__legacy_aime_best",
        prefixes=["baseline_complete.json"],
        era="A-legacy",
        dataset="AIME-24 train 30 / AIME-25 test 30",
        model="silicon_flow (problems 1-18) + bigmodel (19-30)",
        temperature="default (~0.0)",
        max_tokens="API default",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full code body in prompt",
        pattern="single-shot exec",
        summary="baseline 70.0% (21/30), with-skills 66.7%, 7/30 zero-token (timeouts). HIGH-WATER MARK AIME RESULT.",
        diff_prev="larger AIME dataset vs math_experiment; mixed API backends",
    )),
    ("evolve_demo", dict(
        folder="03_evolve_demo__legacy",
        prefixes=["evolve_demo.json"],
        era="A-legacy",
        dataset="AIME (small)",
        model="silicon_flow",
        temperature="default",
        max_tokens="API default",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec",
        summary="small demo of evolve pipeline",
        diff_prev="scaffolding",
    )),
    ("aime_experiment_full_run1", dict(
        folder="04_aime_full_run1__legacy",
        prefixes=["aime_experiment_full_run1_"],
        era="A-legacy",
        dataset="AIME full",
        model="bigmodel glm-4.7",
        temperature="default (temperature-bug era)",
        max_tokens="16000",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec",
        summary="skill-set snapshot only (24 skills)",
        diff_prev="first full AIME run with BigModel backend",
    )),
    ("aime_exp7", dict(
        folder="05_aime_exp7__legacy_bigmodel_first",
        prefixes=["aime_exp7_"],
        era="A-legacy",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="default (temperature-bug era, ~1.0)",
        max_tokens="16000",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec",
        summary="baseline subset",
        diff_prev="first BigModel-only baseline",
    )),
    ("aime_exp7b", dict(
        folder="06_aime_exp7b__legacy_bigmodel_baseline",
        prefixes=["aime_exp7b_", "aime_experiment_exp7b_"],
        era="A-legacy",
        dataset="AIME-24 train / AIME-25 test (30+30)",
        model="bigmodel glm-4.7",
        temperature="default (temperature-bug era, effective ~1.0)",
        max_tokens="16000",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec",
        summary="baseline 60.0%, with-skills 60.0%, evolve 66.7%",
        diff_prev="full AIME BigModel reproduction; showed model-backend drop vs SiliconFlow",
    )),
    ("aime_multiepoch_3ep", dict(
        folder="07_aime_multiepoch_3ep__legacy_preemb",
        prefixes=["aime_multiepoch_3ep_"],
        era="A-legacy",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="default (~1.0, buggy)",
        max_tokens="16000",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec + multi-epoch (3 passes over train)",
        summary="baseline 70%† (reuses Exp1), with-skills 43.3%, evolve 61.1%",
        diff_prev="added multi-epoch; shows skill-set regression without temp fix",
    )),
    ("aime_experiment_emb", dict(
        folder="08_aime_emb__exp4_embedding_retrieval",
        prefixes=["aime_experiment_emb_"],
        era="A-emb",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="default (~1.0, buggy)",
        max_tokens="16000",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec + embedding retrieval (3eb6618)",
        summary="baseline 43.3%, with-skills 46.7%, evolve 43.3%",
        diff_prev="adds embedding retriever vs exp7b; baseline dropped 17pp → exposed temperature bug",
    )),
    ("aime_experiment_emb_fix", dict(
        folder="09_aime_emb_fix__exp5_temperature_fix",
        prefixes=["aime_experiment_emb_fix_"],
        era="A-emb",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="0.0 ✓ (fixed via e80bcf7)",
        max_tokens="16000",
        max_steps=15,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec + emb retrieval",
        summary="baseline 53.3% (+10pp vs exp4), with-skills 50.0%, evolve 50.0%",
        diff_prev="=exp4 but temperature properly forwarded to BigModel",
    )),
    ("aime_multiepoch_3ep_emb_fix", dict(
        folder="10_aime_multiepoch_3ep_emb_fix__exp6_best_multiepoch",
        prefixes=["aime_multiepoch_3ep_emb_fix_"],
        era="A-emb",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="0.0",
        max_tokens="16000",
        max_steps=15,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec + emb + 3 epochs",
        summary="baseline 53.3%, with-skills 53.3%, evolve 57.8% (+4.5pp lift)",
        diff_prev="=exp5 but 3 epochs; best multi-epoch legacy result",
    )),
    ("aime_weak_glm45air", dict(
        folder="11_aime_weak_glm45air__exp3_weak_model",
        prefixes=["aime_weak_glm45air_"],
        era="A-legacy",
        dataset="AIME-24 train / AIME-25 test",
        model="glm-4.5-air (weak)",
        temperature="default",
        max_tokens="16000",
        max_steps=8,
        runs_per_problem=1,
        skills_render="full body",
        pattern="single-shot exec (weak model)",
        summary="baseline 6.7%, with-skills 10.0%, evolve 33.3% (+26pp; largest relative lift)",
        diff_prev="weak-model variant of exp7b; skill lift most visible",
    )),

    # ───── Era B — TIR/ReAct rewrite (1c91edb, Apr 22) ─────
    ("aime_v2_debug", dict(
        folder="12_aime_v2_debug__v2_smoke_test",
        prefixes=["aime_v2_debug_"],
        era="B-v2",
        dataset="AIME small subset",
        model="bigmodel glm-4.7",
        temperature="1.0 (config.toml default)",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct (PersistentSandbox + tool-calling)",
        summary="smoke test after rewrite",
        diff_prev="complete executor rewrite; signatures-only skills",
    )),
    ("aime_v2_v2", dict(
        folder="13_aime_v2_v2__v2_initial",
        prefixes=["aime_v2_v2_"],
        era="B-v2",
        dataset="AIME-24 train",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="initial v2 detail file",
        diff_prev="—",
    )),
    ("aime_v2_exp7c", dict(
        folder="14_aime_v2_exp7c__v2_early",
        prefixes=["aime_v2_exp7c_"],
        era="B-v2",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="early v2 AIME",
        diff_prev="first full-AIME v2 run",
    )),
    ("aime_v2_exp7d", dict(
        folder="15_aime_v2_exp7d__v2_evolve_exploration",
        prefixes=["aime_v2_exp7d_"],
        era="B-v2",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct, with 1-epoch and 3-epoch evolve",
        summary="v2 evolve exploration",
        diff_prev="exp7c + evolve runs",
    )),
    ("aime_v2_fixtest4", dict(
        folder="16_aime_v2_fixtest4__v2_pipeline_fix",
        prefixes=["aime_v2_fixtest4_"],
        era="B-v2",
        dataset="AIME subset",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="pipeline fix verification",
        diff_prev="bug fix over exp7d",
    )),
    ("aime_v2_resume_smoke", dict(
        folder="17_aime_v2_resume_smoke__resume_feature",
        prefixes=["aime_v2_resume_smoke_"],
        era="B-v2",
        dataset="AIME subset",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct + resume",
        summary="timeout-resume sanity check",
        diff_prev="added per-problem checkpoint/resume",
    )),
    ("aime_v2_exp8", dict(
        folder="18_aime_v2_exp8__v2_first_full",
        prefixes=["aime_v2_exp8_", "exp8_"],
        era="B-v2",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=8,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="baseline 35.0%, with-skills 30.8%, evolve 40.0% (3ep). Avg 1.23 code blocks/run.",
        diff_prev="first full TIR baseline; accuracy collapsed vs Era A",
    )),
    ("aime_v2_exp9", dict(
        folder="19_aime_v2_exp9__v2_tuning",
        prefixes=["aime_v2_exp9_"],
        era="B-v2",
        dataset="AIME",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="baseline 32.5%",
        diff_prev="=exp8 with step tuning",
    )),
    ("aime_v2_exp10", dict(
        folder="20_aime_v2_exp10__v2_aborted",
        prefixes=["aime_v2_exp10_"],
        era="B-v2",
        dataset="AIME",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="aborted (0% — pipeline bug)",
        diff_prev="—",
    )),
    ("aime_v2_exp11", dict(
        folder="21_aime_v2_exp11__v2_stable",
        prefixes=["aime_v2_exp11_", "exp11_"],
        era="B-v2",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct, stable pipeline",
        summary="baseline 39.2%, with-skills 37.5%, evolve 1ep 56.7%. Avg 4.62 code blocks/run.",
        diff_prev="stable v2 baseline",
    )),
    ("aime_v2_exp11e3", dict(
        folder="22_aime_v2_exp11e3__v2_three_epochs",
        prefixes=["aime_v2_exp11e3_", "exp11_evolve3"],
        era="B-v2",
        dataset="AIME-24 train / AIME-25 test",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct, 3 epochs",
        summary="with-skills 46.7%, evolve 3ep 51.1%. Skills set 66 entries.",
        diff_prev="=exp11 + 3 epochs",
    )),
    ("aime_v2_exp12", dict(
        folder="23_aime_v2_exp12__v2_best",
        prefixes=["aime_v2_exp12_", "exp12_"],
        era="B-v2",
        dataset="AIME-24 train / AIME-25 test (30+30), 4 runs/problem",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct, tuned prompts",
        summary="baseline 64.2%, with-skills 60.8%, evolve 1ep 66.7%. BEST V2 AIME. Avg 4.56 code blocks/run.",
        diff_prev="best v2 run but still -5.8pp vs Era A SiliconFlow 70%",
    )),
    ("math_v1_exp1", dict(
        folder="24_math_v1_exp1__v2_math500_clean",
        prefixes=["math_v1_exp1_"],
        era="B-v2",
        dataset="MATH-500 clean set (~/deepscaler/math.parquet) 200 train / 200 test",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="baseline 91.9%, with-skills 89.4%, evolve 86.5%. But 62% of runs use 0 code blocks — TIR barely exercised; masks regression.",
        diff_prev="easier dataset than AIME; high numbers don't reflect pipeline quality",
    )),
    ("math_train_v1_exp1", dict(
        folder="25_math_train_v1_exp1__v2_deepscaler_train",
        prefixes=["math_train_v1_exp1_"],
        era="B-v2",
        dataset="DeepScaler train.shuffle.parquet 200 train / 200 test",
        model="bigmodel glm-4.7",
        temperature="1.0",
        max_tokens="4096",
        max_steps=16,
        runs_per_problem=4,
        skills_render="signatures only",
        pattern="TIR / ReAct",
        summary="PARTIAL (killed at 77/200): 64.7%/run, 84.4% any-of-4, 0 timeouts. Avg 1.38 code blocks/run.",
        diff_prev="harder MATH pool than math_v1; baseline drops ~27pp vs math_v1",
    )),
]

ROOT_KEEP = {"aime_experiment_report.md", "EXPERIMENT_AUDIT.md", "_reorganize.py"}


def _files_for(prefixes):
    hits = []
    for p in prefixes:
        for f in ROOT.iterdir():
            if f.is_dir() or f.name in ROOT_KEEP:
                continue
            if f.name == p or f.name.startswith(p):
                hits.append(f)
    return sorted(set(hits), key=lambda x: x.name)


def _render_readme(tag, meta, files):
    lines = [
        f"# {tag}",
        "",
        f"**Era**: `{meta['era']}`  |  **Dataset**: {meta['dataset']}",
        "",
        "## Settings",
        "",
        f"- Model: `{meta['model']}`",
        f"- Temperature: {meta['temperature']}",
        f"- Max tokens (per turn): {meta['max_tokens']}",
        f"- Max agent steps: {meta['max_steps']}",
        f"- Runs per problem (test): {meta['runs_per_problem']}",
        f"- Skill rendering: {meta['skills_render']}",
        f"- Execution pattern: {meta['pattern']}",
        "",
        "## Result",
        "",
        f"{meta['summary']}",
        "",
        "## Diff vs prior experiment",
        "",
        f"{meta['diff_prev']}",
        "",
        "## Files",
        "",
    ]
    for f in files:
        lines.append(f"- `{f.name}`")
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("See `run.sh`.")
    lines.append("")
    return "\n".join(lines)


def _render_run_sh(tag, meta):
    # Era A used academic/run.py era scripts; Era B uses academic.experiments.run_experiment
    if meta["era"].startswith("B-"):
        dataset = "aime"
        if "math_v1" in tag:
            dataset = "math"
        elif "math_train" in tag:
            dataset = "math_train"
        return f"""#!/usr/bin/env bash
# Reconstructed command for {tag}
# (Era B — TIR/ReAct pipeline)
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo $HOME/skill_evolving)"
conda run -n meta-agent python -u -m academic.experiments.run_experiment \\
    --dataset {dataset} \\
    --tag {tag.replace('aime_v2_', '').replace('math_v1_', '').replace('math_train_v1_', '')} \\
    --mode baseline \\
    --concurrency 4
"""
    else:
        return f"""#!/usr/bin/env bash
# Reconstructed command for {tag}
# (Era A — legacy single-shot exec; launched from academic/run_experiment.py
# before the 1c91edb rewrite)
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo $HOME/skill_evolving)"
# NOTE: Era A scripts were replaced by 1c91edb. Original command inferred from result filenames:
#   python -m academic.run_experiment --name {tag} --mode baseline
# For reference only — running this verbatim on current codebase will use Era B pipeline.
echo "Era A legacy experiment — see git show 0bfb9c2:academic/ for original scripts"
"""


def main():
    for tag, meta in EXPERIMENTS:
        folder = ROOT / meta["folder"]
        folder.mkdir(exist_ok=True)
        files = _files_for(meta["prefixes"])
        for f in files:
            dst = folder / f.name
            if dst.exists():
                continue
            shutil.move(str(f), str(dst))
        moved = sorted(folder.iterdir())
        (folder / "README.md").write_text(_render_readme(tag, meta, moved))
        (folder / "run.sh").write_text(_render_run_sh(tag, meta))
        (folder / "run.sh").chmod(0o755)
        print(f"✓ {meta['folder']}: {len(moved)} files")

    # Leftovers
    leftover = [f for f in ROOT.iterdir() if f.is_file() and f.name not in ROOT_KEEP]
    if leftover:
        print("\nLeftover files (not matched to any experiment):")
        for f in leftover:
            print("   ", f.name)


if __name__ == "__main__":
    main()
