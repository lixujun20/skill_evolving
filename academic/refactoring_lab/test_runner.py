"""
test_runner.py — Execution and LLM-as-judge harness for the refactoring lab.

Capabilities
────────────
1. Standalone execution tests
   For every SkillSpec (original or refactored) run all test_queries through
   its harness and check the returned value against the expected answer.

2. Token-budget measurement
   Uses tiktoken (if available) or a whitespace approximation to count tokens
   in each skill's code block.  Lets us compare total-corpus tokens before
   vs. after refactoring.

3. LLM-as-judge (optional)
   Given the original skills, refactored skills, and extracted sub-functions,
   asks an LLM to score the refactoring on:
     - correctness_preserved  (0/1 per skill)
     - sub_function_quality   (1–5)
     - naming_quality         (1–5)
     - overall_recommendation (accept / accept_with_changes / reject)
   plus a free-text rationale.

4. Results are written to `experiments/<name>.json` and a human-readable
   `experiments/<name>.md` summary is produced.

CLI
───
    python -m academic.refactoring_lab.test_runner --mode standalone
    python -m academic.refactoring_lab.test_runner --mode engine --engine naive
    python -m academic.refactoring_lab.test_runner --mode engine --engine desc_first --llm bigmodel
    python -m academic.refactoring_lab.test_runner --mode engine --engine desc_first --llm glm5 --judge
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from academic.refactoring_lab.example_skills import (
    SkillCorpus,
    SkillGroup,
    SkillSpec,
    get_corpus,
    ground_truth_clusters,
    list_corpora,
    negative_controls,
)
from academic.skill_store import Skill, SkillStore

# ── Optional tokenizer ────────────────────────────────────────────────────────
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text or ""))
except Exception:  # pragma: no cover
    def count_tokens(text: str) -> int:
        return max(1, int(len(text or "") / 4))  # rough fallback


# ── Execution test ────────────────────────────────────────────────────────────

def _exec_source(code: str) -> Dict[str, Any]:
    ns: Dict[str, Any] = {}
    exec(compile(code, "<skill>", "exec"), ns)
    return ns


def _approx_equal(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < tol
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(_approx_equal(x, y, tol) for x, y in zip(a, b))
    return a == b


def run_standalone(skills: List[SkillSpec]) -> List[Dict]:
    results = []
    for sk in skills:
        ns = _exec_source(sk.code)
        fn = ns.get(sk.name)
        if fn is None:
            results.append({"skill": sk.name, "ok": False, "error": "function not found"})
            continue
        per_query = []
        all_ok = True
        for (q, expected), harness in zip(sk.test_queries, sk.harnesses):
            try:
                got = harness(fn)
                ok = _approx_equal(got, expected)
            except Exception as e:
                got, ok = None, False
                per_query.append({"q": q, "expected": expected, "got": None, "ok": False,
                                  "error": repr(e)})
                all_ok = False
                continue
            per_query.append({"q": q, "expected": expected, "got": got, "ok": ok})
            all_ok = all_ok and ok
        results.append({"skill": sk.name, "ok": all_ok, "tests": per_query})
    return results


# ── Token measurement ─────────────────────────────────────────────────────────

def token_report(
    original: List[SkillSpec],
    refactored_code_by_name: Dict[str, str],
    sub_functions_code: List[str],
) -> Dict[str, Any]:
    before = {s.name: count_tokens(s.code) for s in original}
    after_skills = {
        name: count_tokens(refactored_code_by_name.get(name, "")) for name in before
    }
    sub_total = sum(count_tokens(c) for c in sub_functions_code)
    total_before = sum(before.values())
    total_after = sum(after_skills.values()) + sub_total
    return {
        "per_skill_before_tokens": before,
        "per_skill_after_tokens": after_skills,
        "sub_functions_total_tokens": sub_total,
        "total_before_tokens": total_before,
        "total_after_tokens": total_after,
        "delta_tokens": total_after - total_before,
        "delta_pct": (total_after - total_before) / total_before if total_before else 0.0,
    }


# ── Engine execution ──────────────────────────────────────────────────────────

def run_engine(
    engine_name: str,
    llm_config: Optional[str],
    corpus: SkillCorpus,
) -> Dict[str, Any]:
    from academic.refactoring_lab.refactor_engine import (
        NaiveRefactorEngine,
        DescriptionFirstEngine,
        build_llm_caller,
    )

    if engine_name == "naive":
        engine = NaiveRefactorEngine()
    elif engine_name == "desc_first":
        llm_call = build_llm_caller(llm_config) if llm_config else None
        engine = DescriptionFirstEngine(llm_call=llm_call)
    else:
        raise ValueError(f"Unknown engine: {engine_name}")

    t0 = time.monotonic()
    result = engine.refactor(corpus.skills)
    elapsed = time.monotonic() - t0

    # Each refactored skill may need one or more shared sub-functions prepended
    # (since we removed duplication from the skill body for token accounting).
    subfn_by_skill: Dict[str, List[str]] = {}
    for sf in result.shared_sub_functions:
        for sk_name in sf.source_skills:
            subfn_by_skill.setdefault(sk_name, []).append(sf.code)

    # Standalone correctness for refactored skills — with sub-fns prepended
    refactored_specs = _rebuild_specs(corpus.skills, result.refactored_skills, subfn_by_skill)
    standalone_after = run_standalone(refactored_specs)

    # Token report — sub-functions counted ONCE, skill bodies as stored
    ref_code_by_name = {d["name"]: d["code"] for d in result.refactored_skills}
    sub_codes = [sf.code for sf in result.shared_sub_functions]
    tok = token_report(corpus.skills, ref_code_by_name, sub_codes)

    # Ground-truth cluster recovery
    gt = ground_truth_clusters(corpus.groups)
    predicted_clusters = {
        sf.name: sorted(sf.source_skills) for sf in result.shared_sub_functions
    }
    cluster_eval = _evaluate_clusters(gt, predicted_clusters, set(negative_controls(corpus.groups)))

    return {
        "engine": engine_name,
        "llm_config": llm_config,
        "corpus": corpus.name,
        "elapsed_s": round(elapsed, 2),
        "shared_sub_functions": [
            {"name": sf.name, "description": sf.description,
             "source_skills": sf.source_skills, "code": sf.code}
            for sf in result.shared_sub_functions
        ],
        "refactored_skills": result.refactored_skills,
        "rejected_merges": [asdict(r) for r in result.rejected_merges],
        "standalone_after": standalone_after,
        "correctness_pass_rate": _pass_rate(standalone_after),
        "token_report": tok,
        "cluster_eval": cluster_eval,
    }


def _pass_rate(standalone: List[Dict]) -> float:
    if not standalone:
        return 0.0
    n_ok = sum(1 for r in standalone if r.get("ok"))
    return n_ok / len(standalone)


def _rebuild_specs(
    original: List[SkillSpec],
    refactored_dicts: List[Dict],
    subfn_by_skill: Optional[Dict[str, List[str]]] = None,
) -> List[SkillSpec]:
    by_name = {d["name"]: d for d in refactored_dicts}
    subfn_by_skill = subfn_by_skill or {}
    out: List[SkillSpec] = []
    for orig in original:
        d = by_name.get(orig.name)
        if d is None:
            out.append(orig)
        else:
            prepended = "\n\n".join(subfn_by_skill.get(orig.name, []))
            full_code = (prepended + "\n\n" + d["code"]) if prepended else d["code"]
            out.append(SkillSpec(
                name=orig.name,
                description=d.get("description", orig.description),
                code=full_code,
                test_queries=orig.test_queries,
                harnesses=orig.harnesses,
                negative_control=orig.negative_control,
            ))
    return out


def _evaluate_clusters(
    ground_truth: Dict[str, List[str]],
    predicted: Dict[str, List[str]],
    negative_controls_set: set,
) -> Dict[str, Any]:
    """For each predicted cluster, find best-matching GT cluster by Jaccard."""
    report = []
    matched_gt = set()
    for pred_name, pred_members in predicted.items():
        best = (None, 0.0)
        pred_set = set(pred_members)
        for gt_name, gt_members in ground_truth.items():
            gt_set = set(gt_members)
            if not gt_set:
                continue
            jacc = len(pred_set & gt_set) / len(pred_set | gt_set)
            if jacc > best[1]:
                best = (gt_name, jacc)
        negatives_in_pred = sorted(pred_set & negative_controls_set)
        report.append({
            "predicted_sub_function": pred_name,
            "predicted_members": sorted(pred_members),
            "best_gt_cluster": best[0],
            "jaccard": round(best[1], 3),
            "negative_controls_incorrectly_merged": negatives_in_pred,
        })
        if best[0]:
            matched_gt.add(best[0])
    missed_gt = [g for g in ground_truth if g not in matched_gt]
    return {"predictions": report, "missed_gt_clusters": missed_gt}


def _skill_specs_to_store(skills: List[SkillSpec]) -> SkillStore:
    store = SkillStore()
    for sk in skills:
        store.add(Skill(
            name=sk.name,
            description=sk.description,
            code=sk.code,
            source_problems=[q for q, _ in sk.test_queries],
            test_queries=list(sk.test_queries),
        ))
    return store


def _collect_unique_shared_subfn_codes(
    original: List[SkillSpec],
    refactored: List[SkillSpec],
) -> List[str]:
    original_map = {s.name: s.code.strip() for s in original}
    subfns: List[str] = []
    seen = set()
    for spec in refactored:
        text = spec.code.strip()
        original_text = original_map.get(spec.name, "")
        if text == original_text:
            continue
        if spec.name in text:
            lines = text.splitlines()
            prefix: List[str] = []
            body_started = False
            for line in lines:
                if line.startswith(f"def {spec.name}("):
                    body_started = True
                    break
                prefix.append(line)
            if body_started:
                code = "\n".join(prefix).strip()
                if code and code not in seen:
                    seen.add(code)
                    subfns.append(code)
    return subfns


def _corpus_to_problems(corpus: SkillCorpus) -> List["Problem"]:
    from academic.pipeline import Problem

    problems: List[Problem] = []
    seen = set()
    for skill in corpus.skills:
        for idx, (query, expected) in enumerate(skill.test_queries):
            key = (skill.name, idx, query)
            if key in seen:
                continue
            seen.add(key)
            problems.append(Problem(
                question=query,
                answer=str(expected),
                id=f"{skill.name}_q{idx}",
            ))
    return problems


async def compare_skill_sets_for_corpus(
    corpus: SkillCorpus,
    refactored_specs: List[SkillSpec],
    *,
    llm_config: str,
    n_runs: int = 1,
    original_total_tokens: Optional[float] = None,
    refactored_total_tokens: Optional[float] = None,
) -> Dict[str, Any]:
    from academic.evaluation import (
        DirectEvalCase,
        SkillSetSpec,
        evaluate_skill_sets,
        evaluate_skill_sets_direct,
    )
    from academic.executor import SOLVE_SYSTEM

    original_store = _skill_specs_to_store(corpus.skills)
    refactored_store = _skill_specs_to_store(refactored_specs)
    original_tok = token_report(corpus.skills, {s.name: s.code for s in corpus.skills}, [])
    refactored_tok = token_report(
        corpus.skills,
        {s.name: s.code for s in refactored_specs},
        _collect_unique_shared_subfn_codes(corpus.skills, refactored_specs),
    )
    skill_sets = [
        SkillSetSpec(name="no_skills", skills=[], store=None, total_code_tokens=0.0),
        SkillSetSpec(
            name="original_skills",
            skills=original_store.skills,
            store=original_store,
            metadata={"corpus": corpus.name},
            total_code_tokens=float(
                original_total_tokens
                if original_total_tokens is not None
                else original_tok["total_after_tokens"]
            ),
        ),
        SkillSetSpec(
            name="refactored_skills",
            skills=refactored_store.skills,
            store=refactored_store,
            metadata={"corpus": corpus.name},
            total_code_tokens=float(
                refactored_total_tokens
                if refactored_total_tokens is not None
                else refactored_tok["total_after_tokens"]
            ),
        ),
    ]

    if corpus.source == "skillsbench_manual":
        cases: List[DirectEvalCase] = []
        orig_by_name = {s.name: s for s in corpus.skills}
        for skill in corpus.skills:
            for idx, ((query, expected), harness) in enumerate(zip(skill.test_queries, skill.harnesses)):
                def _make_eval(
                    skill_name: str,
                    h: Callable[[Callable], Any],
                ) -> Callable[[List[Skill]], Any]:
                    def _eval(skills: List[Skill]) -> Any:
                        skill_map = {s.name: s for s in skills}
                        target = skill_map.get(skill_name)
                        if target is None:
                            raise KeyError(f"required skill missing: {skill_name}")
                        ns: Dict[str, Any] = {}
                        exec(compile(target.code, "<skill>", "exec"), ns)
                        fn = ns[target.name]
                        return h(fn)
                    return _eval

                cases.append(DirectEvalCase(
                    case_id=f"{skill.name}_q{idx}",
                    query=query,
                    expected=expected,
                    evaluator=_make_eval(skill.name, harness),
                ))
        result = evaluate_skill_sets_direct(cases, skill_sets)
        result["evaluation_mode"] = "direct_harness"
        return result

    problems = _corpus_to_problems(corpus)
    result = await evaluate_skill_sets(
        problems,
        skill_sets,
        llm_config=llm_config,
        n_runs=n_runs,
        system_prompt_template=SOLVE_SYSTEM,
        inter_problem_delay=0,
    )
    result["evaluation_mode"] = "solver"
    return result


# ── LLM-as-judge ──────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
You are an expert code reviewer evaluating a *skill refactoring* result.

Given:
  - A list of ORIGINAL Python skills (each solving a problem).
  - A list of SHARED SUB-FUNCTIONS extracted by a refactoring algorithm.
  - A list of REFACTORED skills rewritten to call those sub-functions.
  - Execution results (which refactored skills still pass their unit tests).

Score the refactoring. Respond in STRICT JSON with these keys:
{
  "sub_function_quality":     <1-5 integer>,
  "naming_quality":           <1-5 integer>,
  "merge_correctness":        <1-5 integer>,   // did it cluster the right skills?
  "over_merging_detected":    <true|false>,    // were unrelated skills merged?
  "under_merging_detected":   <true|false>,    // were obvious duplicates missed?
  "overall_recommendation":   "accept" | "accept_with_changes" | "reject",
  "rationale":                "<2-5 sentences>"
}
Do not include any text outside the JSON.
"""


async def llm_as_judge(
    engine_result: Dict[str, Any],
    corpus: SkillCorpus,
    llm_config: str,
) -> Dict[str, Any]:
    from app.llm import LLM

    payload = {
        "original_skills": [
            {"name": s.name, "description": s.description, "code": s.code}
            for s in corpus.skills
        ],
        "shared_sub_functions": engine_result["shared_sub_functions"],
        "refactored_standalone_results": engine_result["standalone_after"],
        "token_report": engine_result["token_report"],
        "cluster_eval": engine_result["cluster_eval"],
    }
    if engine_result.get("skill_set_comparison"):
        payload["skill_set_comparison"] = engine_result["skill_set_comparison"]
    user_msg = (
        "Evaluate this refactoring result:\n\n"
        "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"
    )
    llm = LLM(config_name=llm_config)
    response = await asyncio.wait_for(
        llm.ask(
            messages=[{"role": "user", "content": user_msg}],
            system_msgs=[{"role": "system", "content": _JUDGE_SYSTEM}],
            force_json=True,
        ),
        timeout=180,
    )
    try:
        return json.loads(response)
    except Exception:
        # strip fences if any
        txt = response.strip().lstrip("`").rstrip("`")
        if txt.startswith("json"):
            txt = txt[4:].strip()
        try:
            return json.loads(txt)
        except Exception as e:
            return {"parse_error": str(e), "raw": response}


# ── Report writers ────────────────────────────────────────────────────────────

REPORT_DIR = Path(__file__).parent / "experiments"


def _write_markdown_summary(name: str, data: Dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}.md"
    lines = [f"# {name}", ""]
    if "standalone" in data:
        lines.append("## Standalone skill tests\n")
        for r in data["standalone"]:
            sym = "✓" if r.get("ok") else "✗"
            n_ok = sum(1 for t in r.get("tests", []) if t.get("ok"))
            n_total = len(r.get("tests", []))
            lines.append(f"- {sym} `{r['skill']}` — {n_ok}/{n_total}")
    if "engine" in data:
        eng = data["engine"]
        lines += [
            "", "## Engine run", "",
            f"- Engine: `{eng['engine']}`",
            f"- Corpus: `{eng.get('corpus')}`",
            f"- LLM config: `{eng.get('llm_config')}`",
            f"- Elapsed: {eng['elapsed_s']}s",
            f"- Shared sub-functions extracted: {len(eng['shared_sub_functions'])}",
            f"- Rejected merges: {len(eng['rejected_merges'])}",
            f"- Refactored-skill correctness pass rate: {eng['correctness_pass_rate']:.2%}",
            "",
            "### Token report",
            f"- Total tokens before: {eng['token_report']['total_before_tokens']}",
            f"- Total tokens after : {eng['token_report']['total_after_tokens']}",
            f"- Δ tokens           : {eng['token_report']['delta_tokens']} "
            f"({eng['token_report']['delta_pct']:+.1%})",
            "",
            "### Cluster evaluation",
        ]
        for p in eng["cluster_eval"]["predictions"]:
            lines.append(
                f"- `{p['predicted_sub_function']}` members={p['predicted_members']} "
                f"→ GT=`{p['best_gt_cluster']}` jaccard={p['jaccard']}"
            )
            if p["negative_controls_incorrectly_merged"]:
                lines.append(
                    f"  ⚠ negative control(s) merged: {p['negative_controls_incorrectly_merged']}"
                )
        if eng["cluster_eval"]["missed_gt_clusters"]:
            lines.append(
                f"- Missed GT clusters: {eng['cluster_eval']['missed_gt_clusters']}"
            )
        if eng["rejected_merges"]:
            lines.append("\n### Rejected merges")
            for rm in eng["rejected_merges"]:
                lines.append(
                    f"- `{rm['candidate_sub_fn']}` skills={rm['affected_skills']} "
                    f"reason: {rm['failure_reason']}"
                )
        if eng.get("skill_set_comparison"):
            lines += ["", "### Skill-set comparison"]
            if eng["skill_set_comparison"].get("evaluation_mode"):
                lines.append(
                    f"- Mode: `{eng['skill_set_comparison']['evaluation_mode']}`"
                )
            for report in eng["skill_set_comparison"]["skill_sets"]:
                summary = report["summary"]
                delta = summary.get("delta_vs_baseline")
                line = (
                    f"- `{report['skill_set']}` acc={summary['accuracy_micro']:.2%} "
                    f"avg_tok={summary['avg_total_tokens']} elapsed={summary['total_elapsed_s']}s"
                )
                if delta:
                    line += (
                        f" Δacc={delta['accuracy_micro']:+.2%}"
                        f" Δtok={delta['avg_total_tokens']:+.1f}"
                    )
                lines.append(line)
    if "judge" in data:
        lines += ["", "## LLM-as-judge", "```json",
                  json.dumps(data["judge"], ensure_ascii=False, indent=2),
                  "```"]
    path.write_text("\n".join(lines))
    return path


def save_report(name: str, data: Dict[str, Any]) -> Tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"{name}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    md_path = _write_markdown_summary(name, data)
    return json_path, md_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_standalone(results: List[Dict]) -> None:
    print("\n" + "=" * 64)
    print("STANDALONE SKILL TESTS")
    print("=" * 64)
    for r in results:
        if "error" in r:
            print(f"  ✗ {r['skill']:32s} ERROR: {r['error']}")
            continue
        tests = r["tests"]
        n_ok = sum(1 for t in tests if t["ok"])
        sym = "✓" if n_ok == len(tests) else "✗"
        print(f"  {sym} {r['skill']:32s} {n_ok}/{len(tests)}")


def _print_engine(data: Dict[str, Any]) -> None:
    eng = data["engine"]
    print("\n" + "=" * 64)
    print(f"ENGINE RUN — {eng['engine']} corpus={eng.get('corpus')} (llm={eng.get('llm_config')})  "
          f"elapsed={eng['elapsed_s']}s")
    print("=" * 64)
    print(f"  sub-functions extracted : {len(eng['shared_sub_functions'])}")
    for sf in eng["shared_sub_functions"]:
        print(f"    • {sf['name']:32s} ← {sf['source_skills']}")
    print(f"  rejected merges         : {len(eng['rejected_merges'])}")
    for rm in eng["rejected_merges"]:
        print(f"    × {rm['candidate_sub_fn']}  ({rm['failure_reason']})")
    print(f"  correctness pass rate   : {eng['correctness_pass_rate']:.1%}")
    tr = eng["token_report"]
    print(f"  tokens before → after   : {tr['total_before_tokens']} → "
          f"{tr['total_after_tokens']}  (Δ {tr['delta_tokens']:+d}, "
          f"{tr['delta_pct']:+.1%})")
    if eng.get("skill_set_comparison"):
        print("  skill-set comparison    :")
        for report in eng["skill_set_comparison"]["skill_sets"]:
            summary = report["summary"]
            print(
                f"    • {report['skill_set']:18s} acc={summary['accuracy_micro']:.2%} "
                f"avg_tok={summary['avg_total_tokens']}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Skill Refactoring Lab — Test Runner")
    parser.add_argument("--mode", choices=["standalone", "engine"], default="standalone")
    parser.add_argument("--engine", choices=["naive", "desc_first"], default="naive")
    parser.add_argument("--corpus", default="builtin_math",
                        help="Corpus name. Use --list-corpora to inspect available corpora.")
    parser.add_argument("--llm", default=None,
                        help="LLM config_name (e.g. bigmodel, glm5). If omitted and "
                             "engine=desc_first, a stub LLM is used.")
    parser.add_argument("--judge", action="store_true",
                        help="Also run LLM-as-judge (requires --llm).")
    parser.add_argument("--compare", action="store_true",
                        help="Also run end-to-end collection-level comparison: no/original/refactored skills.")
    parser.add_argument("--compare-runs", type=int, default=1,
                        help="Number of runs per problem for --compare.")
    parser.add_argument("--list-corpora", action="store_true",
                        help="List available corpora and exit.")
    parser.add_argument("--name", default=None, help="Report base name.")
    args = parser.parse_args()

    if args.list_corpora:
        for corpus in list_corpora():
            print(f"{corpus.name}: {corpus.description}")
        return

    corpus = get_corpus(args.corpus)
    data: Dict[str, Any] = {}

    if args.mode == "standalone":
        results = run_standalone(corpus.skills)
        _print_standalone(results)
        data["standalone"] = results
        name = args.name or "standalone"

    else:  # engine
        eng_result = run_engine(args.engine, args.llm, corpus)
        data["engine"] = eng_result
        if args.compare:
            subfn_by_skill: Dict[str, List[str]] = {}
            for sf in eng_result["shared_sub_functions"]:
                for sk_name in sf["source_skills"]:
                    subfn_by_skill.setdefault(sk_name, []).append(sf["code"])
            refactored_specs = _rebuild_specs(
                corpus.skills,
                eng_result.get("refactored_skills", []),
                subfn_by_skill,
            )
            eng_result["skill_set_comparison"] = asyncio.run(
                compare_skill_sets_for_corpus(
                    corpus,
                    refactored_specs,
                    llm_config=args.llm or "bigmodel",
                    n_runs=args.compare_runs,
                )
            )
        _print_engine(data)
        if args.judge:
            if not args.llm:
                print("⚠ --judge requires --llm; skipping judge.")
            else:
                print("\nRunning LLM-as-judge...")
                judge_out = asyncio.run(llm_as_judge(eng_result, corpus, args.llm))
                data["judge"] = judge_out
                print(json.dumps(judge_out, ensure_ascii=False, indent=2))
        name = args.name or f"engine_{args.engine}_{corpus.name}" + (f"_{args.llm}" if args.llm else "")

    jpath, mpath = save_report(name, data)
    print(f"\nWrote: {jpath}")
    print(f"Wrote: {mpath}")


if __name__ == "__main__":
    main()
