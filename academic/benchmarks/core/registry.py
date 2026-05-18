"""Benchmark registry for the current skill-evolving environment plan."""
from __future__ import annotations

from typing import Dict

from academic.benchmarks.core.types import BenchmarkMetadata


BENCHMARK_REGISTRY: Dict[str, BenchmarkMetadata] = {
    "bfcl_v3": BenchmarkMetadata(
        name="bfcl_v3",
        display_name="BFCL-v3 multi-turn base",
        source_url=(
            "https://huggingface.co/datasets/gorilla-llm/"
            "Berkeley-Function-Calling-Leaderboard"
        ),
        introduced="BFCL initial release 2024; v3 multi-turn setting 2025",
        task_type="multi-turn function/tool calling",
        primary_metric="task success plus turn-level function-call correctness",
        skill_format="tool_rule_card + workflow_card",
        recommended_models=["glm-4.7", "claude-sonnet-4-6", "qwen3-32b"],
        saturation_note=(
            "Official multi-turn base top is high but not saturated; failures are "
            "interpretable as missing function, parameter, state, or long-context errors."
        ),
        engineering_cost="low",
        runnable_stage="implemented_adapter",
        notes="Aligned with SkillX-style 50 train / 150 test experiments.",
    ),
    "appworld": BenchmarkMetadata(
        name="appworld",
        display_name="AppWorld",
        source_url="https://github.com/StonyBrookNLP/appworld",
        introduced="ACL 2024",
        task_type="long-horizon app/API workflow",
        primary_metric="task success",
        skill_format="planning skill + functional skill + atomic API skill",
        recommended_models=["glm-4.6/4.7", "qwen3-32b", "claude"],
        saturation_note="Still has substantial headroom, especially challenge splits.",
        engineering_cost="medium-high",
        runnable_stage="registry_only",
        notes="Good second-stage benchmark after BFCL validates tool-skill reuse.",
    ),
    "officeqa": BenchmarkMetadata(
        name="officeqa",
        display_name="OfficeQA / OfficeQA Pro",
        source_url="https://github.com/snap-stanford/OfficeQA",
        introduced="OfficeQA 2025; OfficeQA Pro 2026",
        task_type="grounded enterprise-document QA",
        primary_metric="exact/fuzzy answer match",
        skill_format="document/table reasoning workflow skill",
        recommended_models=["claude", "gpt-4.1", "glm-4.7"],
        saturation_note="OfficeQA Pro remains far from saturated for frontier agents.",
        engineering_cost="medium",
        runnable_stage="registry_only",
        notes="Useful later for document/table skill transfer and retrieval protocols.",
    ),
    "spreadsheet": BenchmarkMetadata(
        name="spreadsheet",
        display_name="SpreadsheetBench-Verified",
        source_url="https://huggingface.co/datasets/KAKA22/SpreadsheetBench",
        introduced="2024; verified 400 subset used by Trace2Skill in 2026",
        task_type="spreadsheet manipulation over xlsx files",
        primary_metric="verifier pass/all-pass over workbook test cases",
        skill_format="SKILL.md package + scripts/references",
        recommended_models=["claude", "qwen3.5-122b", "qwen3.5-35b"],
        saturation_note="Strong models and spreadsheet products are still below human level.",
        engineering_cost="medium-high",
        runnable_stage="implemented_smoke_adapter",
        notes="Aligned with Trace2Skill's 200 evolve / 200 held-out split.",
    ),
    "skillsbench": BenchmarkMetadata(
        name="skillsbench",
        display_name="SkillsBench",
        source_url="https://github.com/benchflow-ai/skillsbench",
        introduced="2026",
        task_type="agent skill-use and skill-composition tasks",
        primary_metric="official Harbor reward; local adapter reports diagnostic skill-selection hit rate",
        skill_format="SKILL.md package / instruction + scripts + resources",
        recommended_models=["claude", "gpt-5.2", "glm-4.7"],
        saturation_note="Designed for broad specialized workflows and low SOTA pass rates.",
        engineering_cost="medium-high",
        runnable_stage="implemented_diagnostic_adapter",
        notes="Current generic runner path validates retrieval/injection/selection, not Harbor official pass rate.",
    ),
    "tir_bench": BenchmarkMetadata(
        name="tir_bench",
        display_name="TIR-Bench",
        source_url="https://huggingface.co/datasets/osunlp/TIR-Bench",
        introduced="2025/2026",
        task_type="tool-integrated reasoning, often multimodal",
        primary_metric="Avg@4 / Pass@4",
        skill_format="task-level skill + action-level experience",
        recommended_models=["gemini", "claude", "qwen-vl"],
        saturation_note="XSkill reports clear remaining gaps versus stronger methods.",
        engineering_cost="high",
        runnable_stage="registry_only",
        notes="Deferred until BFCL/Spreadsheet establish the core skill pipeline.",
    ),
}


def get_benchmark(name: str) -> BenchmarkMetadata:
    try:
        return BENCHMARK_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown benchmark {name!r}. Available: {sorted(BENCHMARK_REGISTRY)}"
        ) from exc
