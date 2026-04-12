"""
pipeline.py — Main tool-evolving pipeline.

Two evaluation modes:
  1. evolve_single(query, n_rounds)  — same query N rounds, skills evolve
  2. evolve_and_test(train, test)     — evolve on train set, evaluate on test set
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from academic.config import RESULTS_DIR
from academic.executor import ExecTrace, solve
from academic.extractor import extract_skills
from academic.skill_store import Skill, SkillStore
from academic.tester import TestResult, test_skill, test_stale_skills

logger = logging.getLogger("tool_evolving")


@dataclass
class RoundMetrics:
    round_idx: int
    query: str
    answer_correct: bool
    predicted: Optional[str]
    expected: Optional[str]
    tokens: int
    completion_tokens: int
    code_lines: int
    skills_used: int
    skills_total: int
    elapsed_s: float
    new_skills_extracted: int


@dataclass
class ExperimentResult:
    name: str
    rounds: List[RoundMetrics] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if not self.rounds:
            return 0.0
        return sum(1 for r in self.rounds if r.answer_correct) / len(self.rounds)

    @property
    def avg_tokens(self) -> float:
        if not self.rounds:
            return 0.0
        return sum(r.tokens for r in self.rounds) / len(self.rounds)

    @property
    def avg_completion_tokens(self) -> float:
        if not self.rounds:
            return 0.0
        return sum(r.completion_tokens for r in self.rounds) / len(self.rounds)

    def save(self, path: Optional[Path] = None) -> Path:
        p = path or (RESULTS_DIR / f"{self.name}.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"name": self.name, "accuracy": self.accuracy,
             "avg_tokens": self.avg_tokens,
             "rounds": [asdict(r) for r in self.rounds]},
            ensure_ascii=False, indent=2,
        ))
        return p

    def summary(self) -> str:
        lines = [
            f"=== {self.name} ===",
            f"Rounds: {len(self.rounds)}",
            f"Accuracy: {self.accuracy:.1%}",
            f"Avg tokens: {self.avg_tokens:.0f}",
            f"Skills in store: {self.rounds[-1].skills_total if self.rounds else 0}",
        ]
        return "\n".join(lines)


# ── Answer comparison ─────────────────────────────────────────────────────────

def _normalize_answer(ans: str) -> str:
    """Strip whitespace, dollar signs, trailing periods, LaTeX wrappers."""
    import re
    ans = ans.strip()
    # Remove \boxed{...} wrapper
    m = re.search(r"\\boxed\{(.+)\}", ans, re.DOTALL)
    if m:
        ans = m.group(1).strip()
    # Remove $ delimiters
    ans = re.sub(r"[\$]", "", ans)
    # LaTeX: \frac{a}{b} → (a)/(b)
    ans = re.sub(r"\\[dt]?frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", ans)
    # \text, \mathrm etc.
    ans = re.sub(r"\\(?:text|mathrm|mathbf|textbf)\{([^}]*)\}", r"\1", ans)
    ans = re.sub(r"\\(?:left|right)", "", ans)
    ans = re.sub(r"\\sqrt\{([^}]*)\}", r"sqrt(\1)", ans)
    ans = ans.replace("\\cdot", "*").replace("\\times", "*")
    # Remove LaTeX thin space commands: \! \, \; \: and \<space>
    ans = re.sub(r"\\[!,;: ]", "", ans)
    # Remove remaining LaTeX commands
    ans = re.sub(r"\\[a-zA-Z]+", "", ans)
    # Remove braces
    ans = ans.replace("{", "").replace("}", "")
    # Trailing period
    ans = re.sub(r"\.$", "", ans)
    # Collapse whitespace around commas
    ans = re.sub(r"\s*,\s*", ",", ans)
    # Remove thousand-separator commas: 11,111,111,100 → 11111111100
    if re.match(r"^\d{1,3}(,\d{3})+$", ans):
        ans = ans.replace(",", "")
    # Remove surrounding parens if entire answer is wrapped
    stripped = ans.strip()
    if stripped.startswith("(") and stripped.endswith(")") and stripped.count("(") == 1:
        stripped = stripped[1:-1]
    ans = stripped.strip()
    # Whitespace normalization
    ans = " ".join(ans.split())
    # Try to parse as number for canonical form
    try:
        val = float(ans)
        if val == int(val):
            return str(int(val))
        return f"{val:.6g}"
    except ValueError:
        return ans.lower()


def _try_eval(expr: str) -> Optional[float]:
    """Try to safely evaluate a numeric expression."""
    import math as _math
    import re as _re
    # Handle mixed numbers: "1 4/5" or "1(4)/(5)" → "1 + 4/5"
    m = _re.match(r"^(-?\d+)\s+(\d+)\s*/\s*(\d+)$", expr.strip())
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den != 0:
            sign = -1 if whole < 0 else 1
            return float(abs(whole) + num / den) * sign
    m = _re.match(r"^(-?\d+)\s*\((\d+)\)\s*/\s*\((\d+)\)$", expr.strip())
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den != 0:
            sign = -1 if whole < 0 else 1
            return float(abs(whole) + num / den) * sign
    try:
        # Only allow safe math builtins
        val = eval(expr, {"__builtins__": {}}, {
            "sqrt": _math.sqrt, "pi": _math.pi, "e": _math.e,
            "abs": abs, "round": round, "int": int, "float": float,
        })
        return float(val)
    except Exception:
        return None


def check_answer(predicted: Optional[str], expected: str) -> bool:
    if predicted is None:
        return False

    # 1. Direct string match after normalization
    norm_p = _normalize_answer(predicted)
    norm_e = _normalize_answer(expected)
    if norm_p == norm_e:
        return True

    # 2. Try numeric evaluation of both sides
    val_p = _try_eval(predicted)
    val_e = _try_eval(expected)
    if val_p is not None and val_e is not None:
        return abs(val_p - val_e) < 1e-6

    # 3. Try evaluating normalized forms
    val_p = _try_eval(norm_p)
    val_e = _try_eval(norm_e)
    if val_p is not None and val_e is not None:
        return abs(val_p - val_e) < 1e-6

    return False


# ── Mode 1: Single-query evolution ────────────────────────────────────────────

async def evolve_single(
    query: str,
    expected_answer: str,
    n_rounds: int = 10,
    store: Optional[SkillStore] = None,
    experiment_name: str = "single_query",
) -> ExperimentResult:
    """
    Run *query* N rounds. After each round, extract skills from the trace.
    Measure accuracy and token cost per round.
    """
    store = store or SkillStore()
    result = ExperimentResult(name=experiment_name)

    for i in range(n_rounds):
        t0 = time.monotonic()
        logger.info(f"[round {i+1}/{n_rounds}] skills={len(store)}")

        # Retrieve relevant skills
        relevant = store.retrieve(query, top_k=5)
        for sk in relevant:
            sk.usage_count += 1

        # Solve (pass store for dependency resolution)
        trace: ExecTrace = await solve(query, relevant, store=store)
        correct = check_answer(trace.final_answer, expected_answer)
        if correct:
            for sk in relevant:
                sk.success_count += 1

        # Extract new skills
        new_skills: List[Skill] = []
        if trace.code_blocks:
            new_skills = await extract_skills(
                query=query,
                code_blocks=trace.code_blocks,
                outputs=trace.outputs,
                existing_skills_prompt=store.build_skills_prompt(),
            )
            # Test each skill before adding to store
            accepted = []
            for sk in new_skills:
                tr = test_skill(sk, store)
                if tr.passed:
                    old = store.get(sk.name)
                    old_ver = old.version if old else 0
                    store.add(sk)
                    cur = store.get(sk.name)
                    if old_ver > 0:
                        logger.info(
                            f"    skill '{sk.name}' updated v{old_ver}→v{cur.version}"
                            f"  deps={cur.dependencies}"
                        )
                    else:
                        logger.info(
                            f"    skill '{sk.name}' added v1  deps={cur.dependencies}"
                        )
                    accepted.append(sk)
                else:
                    logger.info(f"    skill '{sk.name}' REJECTED by tester: {tr.error}")
            new_skills = accepted

        # Handle stale skills (lazy update)
        stale_results = test_stale_skills(store)
        for sr in stale_results:
            if not sr.passed:
                logger.info(f"    stale skill '{sr.skill_name}' rolled back: {sr.error}")

        elapsed = time.monotonic() - t0
        code_lines = sum(c.count("\n") + 1 for c in trace.code_blocks)

        rm = RoundMetrics(
            round_idx=i,
            query=query[:100],
            answer_correct=correct,
            predicted=trace.final_answer,
            expected=expected_answer,
            tokens=trace.total_tokens,
            completion_tokens=trace.completion_tokens,
            code_lines=code_lines,
            skills_used=len(relevant),
            skills_total=len(store),
            elapsed_s=elapsed,
            new_skills_extracted=len(new_skills),
        )
        result.rounds.append(rm)
        logger.info(
            f"  → correct={correct}  tokens={trace.total_tokens}  "
            f"new_skills={len(new_skills)}  total_skills={len(store)}"
        )

    return result


# ── Mode 2: Train/test evolution ──────────────────────────────────────────────

@dataclass
class Problem:
    question: str
    answer: str
    id: str = ""


async def evolve_and_test(
    train: List[Problem],
    test: List[Problem],
    experiment_name: str = "train_test",
    n_epochs: int = 1,
    skip_baseline: bool = False,
    agent_model: Optional[str] = None,
    extract_model: Optional[str] = None,
) -> dict:
    """
    Phase 1: Evolve skills by solving training problems (possibly multiple epochs).
    Phase 2: Evaluate on test problems WITH evolved skills.
    Phase 3: Also evaluate on test problems WITHOUT skills (baseline), unless skipped.
    Returns a dict with all three result sets.
    """
    import random as _random
    from academic.config import AGENT_MODEL, EXTRACT_MODEL
    _agent_model = agent_model or AGENT_MODEL
    _extract_model = extract_model or EXTRACT_MODEL
    store = SkillStore()

    # ── Phase 1: Evolve (multi-epoch) ────────────────────────────────────
    total_train_rounds = len(train) * n_epochs
    logger.info(f"=== EVOLVE PHASE: {len(train)} problems × {n_epochs} epochs = {total_train_rounds} rounds ===")
    evolve_result = ExperimentResult(name=f"{experiment_name}_evolve")

    round_counter = 0
    for epoch in range(n_epochs):
        # Shuffle training data each epoch (different order)
        epoch_train = list(train)
        _random.Random(42 + epoch).shuffle(epoch_train)
        logger.info(f"--- Epoch {epoch+1}/{n_epochs} ({len(epoch_train)} problems) ---")

        for i, prob in enumerate(epoch_train):
            t0 = time.monotonic()
            logger.info(f"[evolve e{epoch+1} {i+1}/{len(epoch_train)} (total {round_counter+1}/{total_train_rounds})] skills={len(store)} | {prob.id}")

            relevant = store.retrieve(prob.question, top_k=5)
            for sk in relevant:
                sk.usage_count += 1

            trace = await solve(prob.question, relevant, store=store, llm_config=_agent_model)
            correct = check_answer(trace.final_answer, prob.answer)
            if correct:
                for sk in relevant:
                    sk.success_count += 1

            # Extract skills even from incorrect solutions (patterns may still be useful)
            if trace.code_blocks:
                new_skills = await extract_skills(
                    query=prob.question,
                    code_blocks=trace.code_blocks,
                    outputs=trace.outputs,
                    existing_skills_prompt=store.build_skills_prompt(),
                    llm_config=_extract_model,
                )
                # Test each skill before adding to store
                accepted = []
                for sk in new_skills:
                    tr = test_skill(sk, store)
                    if tr.passed:
                        old = store.get(sk.name)
                        old_ver = old.version if old else 0
                        store.add(sk)
                        cur = store.get(sk.name)
                        if old_ver > 0:
                            logger.info(
                                f"    skill '{sk.name}' updated v{old_ver}→v{cur.version}"
                                f"  deps={cur.dependencies}"
                            )
                        else:
                            logger.info(
                                f"    skill '{sk.name}' added v1  deps={cur.dependencies}"
                            )
                        accepted.append(sk)
                    else:
                        logger.info(f"    skill '{sk.name}' REJECTED by tester: {tr.error}")
                new_skills = accepted
            else:
                new_skills = []

            # Handle stale skills (lazy update)
            stale_results = test_stale_skills(store)
            for sr in stale_results:
                if not sr.passed:
                    logger.info(f"    stale skill '{sr.skill_name}' rolled back: {sr.error}")

            elapsed = time.monotonic() - t0
            code_lines = sum(c.count("\n") + 1 for c in trace.code_blocks)
            evolve_result.rounds.append(RoundMetrics(
                round_idx=round_counter, query=prob.question[:100],
                answer_correct=correct, predicted=trace.final_answer,
                expected=prob.answer, tokens=trace.total_tokens,
                completion_tokens=trace.completion_tokens,
                code_lines=code_lines, skills_used=len(relevant),
                skills_total=len(store), elapsed_s=elapsed,
                new_skills_extracted=len(new_skills),
            ))
            logger.info(
                f"  → correct={correct}  tokens={trace.total_tokens}  "
                f"skills_now={len(store)}"
            )
            round_counter += 1

    # Save evolved skill store
    store_path = RESULTS_DIR / f"{experiment_name}_skills.json"
    store.save(store_path)
    logger.info(f"Skill store saved: {store_path}  ({len(store)} skills)")

    # ── Phase 2: Test WITH skills ─────────────────────────────────────────
    # Run lazy update tests on stale skills before evaluation
    stale_results = test_stale_skills(store)
    for sr in stale_results:
        if not sr.passed:
            logger.info(f"  Pre-test stale rollback: '{sr.skill_name}' — {sr.error}")

    logger.info(f"=== TEST (with skills): {len(test)} problems ===")
    test_with = ExperimentResult(name=f"{experiment_name}_test_with_skills")
    for i, prob in enumerate(test):
        t0 = time.monotonic()
        relevant = store.retrieve(prob.question, top_k=5)
        trace = await solve(prob.question, relevant, store=store, llm_config=_agent_model)
        correct = check_answer(trace.final_answer, prob.answer)
        elapsed = time.monotonic() - t0
        code_lines = sum(c.count("\n") + 1 for c in trace.code_blocks)
        test_with.rounds.append(RoundMetrics(
            round_idx=i, query=prob.question[:100],
            answer_correct=correct, predicted=trace.final_answer,
            expected=prob.answer, tokens=trace.total_tokens,
            completion_tokens=trace.completion_tokens,
            code_lines=code_lines, skills_used=len(relevant),
            skills_total=len(store), elapsed_s=elapsed,
            new_skills_extracted=0,
        ))
        logger.info(f"  [{i+1}] correct={correct}  tokens={trace.total_tokens}")

    # ── Phase 3: Test WITHOUT skills (baseline) ───────────────────────────
    if skip_baseline:
        logger.info("=== BASELINE SKIPPED (reusing existing results) ===")
        test_without = ExperimentResult(name=f"{experiment_name}_test_baseline")
    else:
        logger.info(f"=== TEST (baseline, no skills): {len(test)} problems ===")
        test_without = ExperimentResult(name=f"{experiment_name}_test_baseline")
        for i, prob in enumerate(test):
            t0 = time.monotonic()
            trace = await solve(prob.question, [], llm_config=_agent_model)  # empty skills
            correct = check_answer(trace.final_answer, prob.answer)
            elapsed = time.monotonic() - t0
            code_lines = sum(c.count("\n") + 1 for c in trace.code_blocks)
            test_without.rounds.append(RoundMetrics(
                round_idx=i, query=prob.question[:100],
                answer_correct=correct, predicted=trace.final_answer,
                expected=prob.answer, tokens=trace.total_tokens,
                completion_tokens=trace.completion_tokens,
                code_lines=code_lines, skills_used=0,
                skills_total=0, elapsed_s=elapsed,
                new_skills_extracted=0,
            ))
            logger.info(f"  [{i+1}] correct={correct}  tokens={trace.total_tokens}")

    # Save all results
    evolve_result.save()
    test_with.save()
    test_without.save()

    summary = {
        "evolve": {"accuracy": evolve_result.accuracy, "avg_tokens": evolve_result.avg_tokens,
                    "avg_completion_tokens": evolve_result.avg_completion_tokens},
        "test_with_skills": {"accuracy": test_with.accuracy, "avg_tokens": test_with.avg_tokens,
                             "avg_completion_tokens": test_with.avg_completion_tokens},
        "test_baseline": {"accuracy": test_without.accuracy, "avg_tokens": test_without.avg_tokens,
                          "avg_completion_tokens": test_without.avg_completion_tokens},
        "skills_evolved": len(store),
        "improvement": test_with.accuracy - test_without.accuracy,
        "token_saving": test_without.avg_tokens - test_with.avg_tokens,
        "completion_token_saving": test_without.avg_completion_tokens - test_with.avg_completion_tokens,
    }
    logger.info(f"\n{'='*60}")
    logger.info(f"RESULTS SUMMARY")
    logger.info(f"  Evolve accuracy       : {evolve_result.accuracy:.1%}")
    logger.info(f"  Test w/ skills        : {test_with.accuracy:.1%}")
    logger.info(f"  Test baseline         : {test_without.accuracy:.1%}")
    logger.info(f"  Improvement           : {summary['improvement']:+.1%}")
    logger.info(f"  Token saving (total)  : {summary['token_saving']:.0f}")
    logger.info(f"  Token saving (compl.) : {summary['completion_token_saving']:.0f}")
    logger.info(f"  Skills evolved        : {len(store)}")
    logger.info(f"{'='*60}")

    (RESULTS_DIR / f"{experiment_name}_summary.json").write_text(
        json.dumps(summary, indent=2)
    )
    return summary
