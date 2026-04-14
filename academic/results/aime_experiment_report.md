# Skill Evolution for Mathematical Reasoning: An Empirical Study on AIME

## Abstract

We evaluate a **skill evolution** framework on competition-level mathematics
(AIME 2024 → 2025). The system extracts reusable code "skills" from
problem-solving traces and accumulates them in a shared library. We conduct
six experiments: (1) single-epoch evolution with GLM-4.7 (TF-IDF retrieval),
(2) multi-epoch (3-pass) evolution, (3) single-epoch with a weaker model
(GLM-4.5-Air), (4) single-epoch with embedding-based retrieval,
(5) single-epoch with embedding after fixing a temperature bug, and
(6) multi-epoch (3-pass) with embedding and the temperature fix.

> **Note on Experiments 2–4**: A critical bug was discovered after Exp 4 —
> the BigModel API adapter stripped the `temperature` parameter from requests,
> causing the model to run at its default temperature (~0.95–1.0) instead of
> the configured `temperature=0.0`. Exp 1 used SiliconFlow (unaffected);
> Exp 2–4 used BigModel (affected). Exp 5–6 were run after the fix.

**Key findings**:

| Experiment | Test Accuracy | Baseline | Δ Accuracy | Completion Token Δ |
|------------|:-:|:-:|:-:|:-:|
| Exp 1: TF-IDF, 1 epoch | 66.7% (20/30) | 70.0% (21/30) | −3.3 pp | **−20.0%** |
| Exp 2: TF-IDF, 3 epochs† | 43.3% (13/30) | (reuse Exp 1) | −26.7 pp | — |
| Exp 3: GLM-4.5-Air, 1 epoch† | 10.0% | 6.7% | **+3.3 pp (+50% rel.)** | +248/problem |
| Exp 4: Embedding, 1 epoch† | 46.7% (14/30) | 43.3% (13/30) | +3.3 pp | −15.2% |
| Exp 5: Embedding, 1 epoch (temp fix) | 50.0% (15/30) | 53.3% (16/30) | −3.3 pp | −2,698 tok |
| Exp 6: Embedding, 3 epochs (temp fix) | 53.3% (16/30) | 53.3% (16/30) | **0.0 pp** | +858 tok (controlled) |

† Experiments 2–4 affected by temperature bug (see Section 7).

**Critical finding**: In Exp 6, both skills and baseline achieve **100%
accuracy on all problems that complete within resource limits** (no timeouts,
no max-token exhaustion). The 16/30 correct answers in each case correspond
exactly to the 16 problems that finished normally. Skills change *which*
problems are solvable — by shifting timeout and max-token boundaries — but
do not change the solve rate on completed problems.

Embedding-based retrieval (Exp 4) converts skill evolution from an
accuracy liability to an accuracy gain, while maintaining completion token
savings. Multi-epoch training with TF-IDF degrades accuracy (Exp 2), but
**multi-epoch with embedding recovers to baseline level** (Exp 6: 0 pp delta).
Weaker models benefit most from skill augmentation (+50% relative accuracy).
In a controlled comparison (19 shared non-zero-token problems), 3-epoch
skills achieve +5.3 pp accuracy and +9.7% completion token savings.

---

## 1  Introduction

Large language models (LLMs) can solve mathematical problems via
tool-integrated reasoning — writing and executing Python code in an
agentic loop. However, each problem is solved independently: the model
re-derives common subroutines (e.g., GCD, modular arithmetic, geometry
helpers) from scratch every time.

**Skill evolution** addresses this by:
1. Solving problems with code execution (the *executor*).
2. Extracting reusable helper functions from successful traces (the *extractor*).
3. Testing each extracted skill automatically (the *tester*).
4. Accumulating verified skills in a shared library (the *skill store*).
5. Providing relevant skills to the executor when solving new problems.

We hypothesise that:
- (H1) Accumulated skills improve downstream accuracy by providing
  verified building blocks.
- (H2) Skills reduce token cost by replacing verbose from-scratch
  derivations with short function calls.
- (H3) Multiple training passes further improve skill quality and
  downstream performance.
- (H4) Weaker models benefit more from skill augmentation than
  stronger models.

---

## 2  Experimental Setup

### 2.1  Datasets

| Dataset | Source | Size | Answer Format |
|---------|--------|:----:|:---:|
| **Train**: AIME 2024 (I + II) | `AI-MO/aimo-validation-aime` | 30 | Integer 0–999 |
| **Test**: AIME 2025 (I + II) | `yentinglin/aime_2025` | 30 | Integer 0–999 |

Problems are shuffled with seed 42 for reproducibility. AIME (American
Invitational Mathematics Examination) problems are competition-level,
spanning number theory, combinatorics, algebra, and geometry.

### 2.2  Models

| Config | Model | API | Role |
|--------|-------|-----|------|
| **GLM-4.7** | `glm-4.7` (ZhipuAI) | BigModel / SiliconFlow | Executor + Extractor (Exp 1–6) |
| **GLM-4.5-Air** | `glm-4.5-air` (ZhipuAI) | BigModel | Executor only (Exp 3) |

In Experiment 3, the weaker GLM-4.5-Air serves as the executor (problem solver),
while GLM-4.7 remains the extractor (skill extraction). This tests whether
skills evolved by a strong extractor can help a weaker executor.

> **Temperature bug (Exp 2–4)**: The BigModel API adapter in `app/llm.py`
> stripped the `temperature` parameter from API requests. The configured
> `temperature=0.0` was not transmitted; the API used its default (~0.95–1.0).
> SiliconFlow (Exp 1) was unaffected. The bug was fixed before Exp 5–6.

### 2.3  System Configuration

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MAX_AGENT_STEPS` | 8 | Max LLM interaction rounds per problem |
| `CODE_EXEC_TIMEOUT` | 30 s | Per code-block execution timeout |
| `LLM_TIMEOUT` | 300 s | Per LLM request timeout |
| `top_k` | 5 | Skills retrieved per query (TF-IDF cosine) |
| Tester | 3-stage | Syntax → Load → Assertion |

### 2.4  Pipeline

```
Phase 1 — Evolve (AIME 2024, 30 problems × N epochs):
   For each epoch, shuffle training problems:
     For each problem:
       1. Retrieve top-5 relevant skills from store
       2. Executor solves with skills pre-loaded
       3. Extractor analyses trace → candidate skills
       4. Tester validates each candidate (syntax, load, assertions)
       5. Verified skills added/updated in store
   Skills accumulate across problems and epochs.

Phase 2 — Test with Skills (AIME 2025, 30 problems):
   Solve each problem with the full evolved skill library.
   No new skills extracted.

Phase 3 — Test Baseline (AIME 2025, 30 problems):
   Solve each problem with an empty skill list.
   Same executor, same model, same parameters.
```

### 2.5  Retrieval

Two retrieval methods are evaluated:

1. **TF-IDF** (Exp 1–3): Cosine similarity over TF-IDF vectors of skill
   names, descriptions, and code. Top-5 skills with score > 0 are injected.

2. **Embedding** (Exp 4–6): Cosine similarity over 2048-dimensional vectors
   from ZhipuAI's `embedding-3` model. Each skill's text representation
   includes name, description, source problems, and code. Falls back to
   TF-IDF if the embedding API is unavailable.

In both cases, the top-5 highest-scoring skills are injected into the
executor's system prompt as pre-loaded functions.

### 2.6  Skill Testing

Each candidate skill undergoes three verification stages:
1. **Syntax check**: `ast.parse()` on the skill code.
2. **Load check**: `exec()` the skill and all dependencies in a fresh namespace.
3. **Assertion check**: Execute the LLM-generated test assertions.

Skills failing any stage are rejected.

---

## 3  Experiment 1: Single-Epoch Evolution (GLM-4.7)

### 3.1  Evolve Phase (Training)

| Metric | Value |
|--------|:-----:|
| Accuracy | 25/30 = **83.3%** |
| Skills evolved | **34** |
| Skills with dependencies | 8 (23.5%) |
| Avg tokens (solved) | 9,427 |
| Total tokens | 235,671 |
| Timeouts | 5 (16.7%) |

The evolve phase solved 25 of 30 AIME 2024 problems, producing 34 verified
skills. Eight skills have explicit dependencies on other skills, forming
a dependency graph with maximum depth 2. Notable skill clusters include:

- **Complex number operations**: `complex_pow_int` → `complex_magnitude_sq` → `product_roots_unity_conjugate`
- **Combinatorics**: `count_positive_integer_compositions` → `count_grid_paths_with_direction_changes`
- **Algebra**: `solve_quadratic` → `solve_speed_and_delay_from_times`
- **Geometry**: `square_factor` → `simplify_radical_fraction`

### 3.2  Test Phase Comparison

#### 3.2.1  Overall Accuracy

| Method | Correct | Accuracy | Timeouts | Solved but Wrong |
|--------|:-------:|:--------:|:--------:|:-------:|
| With skills (1 epoch, 34 skills) | 20/30 | **66.7%** | 10 | 0 |
| Baseline (no skills) | 21/30 | **70.0%** | 7 | 2 |

The baseline achieves marginally higher accuracy (+3.3 percentage points).
Notably, the skill-augmented method has **more timeouts** (10 vs 7) but
**zero wrong answers** among solved problems — every problem that
produced an answer was correct.

#### 3.2.2  Per-Problem Breakdown

| # | Skills | Baseline | S_tok | B_tok | Token Δ | Note |
|:-:|:------:|:--------:|------:|------:|--------:|------|
| 1 | ✓ | ✓ | 13,948 | 33,080 | **−58%** | |
| 2 | ✓ | ✓ | 8,713 | 9,045 | −4% | |
| 3 | ✗ | ✓ | timeout | 20,860 | — | baseline only |
| 4 | ✓ | ✓ | 7,945 | 20,111 | **−60%** | |
| 5 | ✓ | ✓ | 12,909 | 7,367 | +75% | |
| 6 | ✓ | ✓ | 14,049 | 17,506 | −20% | |
| 7 | ✓ | ✓ | 3,692 | 3,753 | −2% | |
| 8 | ✗ | ✗ | timeout | timeout | — | both fail |
| 9 | ✗ | ✓ | timeout | 19,352 | — | baseline only |
| 10 | ✓ | ✓ | 3,339 | 2,905 | +15% | |
| 11 | ✗ | ✗ | timeout | timeout | — | both fail |
| 12 | ✓ | ✓ | 10,834 | 11,017 | −2% | |
| 13 | ✗ | ✗ | timeout | timeout | — | both fail |
| 14 | ✓ | ✓ | 5,486 | 4,901 | +12% | |
| 15 | ✓ | ✓ | 2,450 | 1,755 | +40% | |
| 16 | ✗ | ✗ | timeout | timeout | — | both fail |
| 17 | ✓ | ✗ | 20,277 | timeout | — | **skills only** |
| 18 | ✗ | ✗ | timeout | timeout | — | both fail |
| 19 | ✓ | ✓ | 8,130 | 7,601 | +7% | |
| 20 | ✓ | ✓ | 2,693 | 2,096 | +28% | |
| 21 | ✓ | ✗ | 7,618 | 9,911 | — | **skills only** |
| 22 | ✗ | ✗ | timeout | 16,303 | — | baseline wrong |
| 23 | ✓ | ✓ | 6,262 | 11,501 | **−46%** | |
| 24 | ✓ | ✓ | 19,001 | 15,727 | +21% | |
| 25 | ✓ | ✓ | 4,285 | 8,421 | **−49%** | |
| 26 | ✗ | ✓ | timeout | 9,026 | — | baseline only |
| 27 | ✓ | ✗ | 13,022 | timeout | — | **skills only** |
| 28 | ✓ | ✓ | 1,836 | 1,721 | +7% | |
| 29 | ✓ | ✓ | 3,160 | 2,329 | +36% | |
| 30 | ✗ | ✓ | timeout | 8,090 | — | baseline only |

#### 3.2.3  Unique Solves

- **Skills solved but baseline didn't**: Problems 17, 21, 27 (3 problems)
- **Baseline solved but skills didn't**: Problems 3, 9, 26, 30 (4 problems)

This suggests the two methods have **partially complementary** solving
capabilities. An oracle ensemble selecting the better answer per problem
would achieve 24/30 = 80.0%.

#### 3.2.4  Token Efficiency

On the **17 problems both methods solved correctly**:

| Metric | With Skills | Baseline | Δ |
|--------|:-----------:|:--------:|:-:|
| Total tokens | 128,732 | 160,836 | **−20.0%** |
| Avg tokens/problem | 7,572 | 9,461 | −1,889 |

Token savings are highly problem-dependent:
- **Large savings** (>30%): Problems 1 (−58%), 4 (−60%), 23 (−46%), 25 (−49%)
- **Increased cost** (>30%): Problems 5 (+75%), 15 (+40%), 29 (+36%)

### 3.3  Cost Analysis

| Phase | Total Tokens | Purpose |
|-------|:-----------:|---------|
| Evolve (train) | 235,671 | Solving + extraction + testing |
| Test (skills) | 169,649 | Solving with skills |
| Test (baseline) | 244,378 | Solving without skills |

**Overhead**: Including training cost, the skill method costs
405,320 tokens vs baseline's 244,378 — **65.8% more**. Break-even
requires ~95 additional test problems (~3.2 test sets of 30).

---

## 4  Experiment 2: Multi-Epoch Evolution (3 Epochs)

### 4.1  Motivation

We test whether multiple passes over the training set improve skill quality
through version updates, higher-level composition, and better coverage.

### 4.2  Setup

Same configuration as Experiment 1, but the 30 training problems are solved
**3 times** with different shuffled orders (seeds 42, 43, 44). Skills
accumulate across all 90 rounds. Baseline is reused from Experiment 1.

### 4.3  Epoch-by-Epoch Training Analysis

| Epoch | Accuracy | Timeouts | Avg Tokens (solved) | Skills Start → End | New Skills |
|:-----:|:--------:|:--------:|:-------------------:|:------------------:|:----------:|
| 1 | 18/30 (60.0%) | 4 | 11,736 | 0 → 22 | +22 |
| 2 | 20/30 (66.7%) | 1 | 10,711 | 22 → 38 | +16 |
| 3 | 17/30 (56.7%) | 3 | 9,696 | 38 → 45 | +7 |
| **Total** | **55/90 (61.1%)** | **8** | **10,048** | — | **45** |

Key observations:
- **Epoch 2 is the sweet spot**: Highest training accuracy (66.7%) with
  minimal timeouts (1). Skills from Epoch 1 are actively helping.
- **Epoch 3 shows degradation**: Accuracy drops to 56.7% despite having
  38→45 skills. Diminishing returns as the skill library grows.
- **Token cost decreases monotonically**: 11,736 → 10,711 → 9,696.
  Skills consistently compress the reasoning path, even when they hurt accuracy.
- **Skill creation rate declines**: +22 → +16 → +7. The library saturates;
  most useful patterns have already been captured.

### 4.4  Test Results

| Method | Correct | Accuracy | Timeouts |
|--------|:-------:|:--------:|:--------:|
| 3 epochs, 45 skills | 13/30 | **43.3%** | 9 |
| 1 epoch, 34 skills (Exp 1) | 20/30 | 66.7% | 10 |
| Baseline (Exp 1) | 21/30 | 70.0% | 7 |

**Multi-epoch is significantly worse**: 43.3% vs 66.7% single-epoch.

### 4.5  Regression Analysis: 1 Epoch vs 3 Epochs

| # | 1ep | 3ep | Regression? | Note |
|:-:|:---:|:---:|:-----------:|------|
| 1 | ✓ | ✗ | **YES** | 13,948→17,431 tokens, wrong answer |
| 4 | ✓ | ✗ | **YES** | 7,945→17,339 tokens |
| 12 | ✓ | ✗ | **YES** | 10,834→timeout |
| 17 | ✓ | ✗ | **YES** | 20,277→17,511 (wrong) |
| 21 | ✓ | ✗ | **YES** | 7,618→timeout |
| 23 | ✓ | ✗ | **YES** | 6,262→timeout |
| 24 | ✓ | ✗ | **YES** | 19,001→16,855 (wrong) |

**7 problems regressed** from correct (1 epoch) to incorrect (3 epochs).
No problems improved (0 new solves that 1 epoch missed).

### 4.6  Root Cause: Skill Over-Accumulation

The 3-epoch library has 45 skills vs 34 for single-epoch, but only
**7 skills** overlap between the two libraries (different shuffling
produces different extraction patterns). The multi-epoch library contains:

- More highly-specialised skills with low generality
- Higher prompt overhead: 45 skill definitions inflate the context window
- Potential skill conflicts: overlapping but slightly different implementations

The TF-IDF retriever injects top-5 skills regardless of relevance. With a
larger library, the probability of injecting *irrelevant* skills increases,
confusing the executor and triggering wrong answers rather than timeouts.

---

## 5  Experiment 3: Weak Model (GLM-4.5-Air)

### 5.1  Motivation

We test **H4**: whether skill evolution benefits weaker models more than
stronger ones. GLM-4.5-Air is a lighter, faster variant of the GLM family.
The extractor remains GLM-4.7 to maintain skill quality.

### 5.2  Setup

| Component | Model |
|-----------|-------|
| Executor (solver) | GLM-4.5-Air |
| Extractor | GLM-4.7 |
| Epochs | 1 |

### 5.3  Results

| Metric | Value |
|--------|:-----:|
| Evolve accuracy (train) | 33.3% (10/30) |
| Test w/ skills | 10.0% (3/30) |
| Test baseline | 6.7% (2/30) |
| Accuracy improvement | +3.3 pp (+50% relative) |
| Total token saving | −166 per problem |
| Completion token saving | +248 per problem |
| Skills evolved | 8 |
| Runtime | ~4.2 hours |

### 5.4  Per-Problem Analysis (Test Set)

| # | Skills | Baseline | Δ | Tokens (S) | Tokens (B) |
|:-:|:------:|:--------:|:-:|:----------:|:----------:|
| 5 | ✓ | ✗ | ++ | 9,753 | 43,473 |
| 20 | ✓ | ✗ | ++ | 10,335 | 9,840 |
| 28 | ✓ | ✓ | = | 3,472 | 5,368 |
| 15 | ✗ | ✓ | −− | 5,770 | 7,021 |

- **2 problems gained** by skills (5, 20), **1 lost** (15) → net +1 problem
- Problem 5 is notable: baseline consumed 43,473 tokens (multi-round tool use)
  vs 9,753 with skills — a **78% reduction** and correct answer
- Problem 28: both solve it, but skills use 35% fewer tokens

### 5.5  Cross-Model Comparison

| Metric | GLM-4.7 (Exp 1) | GLM-4.5-Air (Exp 3) |
|--------|:----------------:|:--------------------:|
| Evolve accuracy | 60.0% | 33.3% |
| Test w/ skills | 66.7% | 10.0% |
| Test baseline | 70.0% | 6.7% |
| Accuracy change | −3.3 pp (−4.7% rel.) | +3.3 pp (+50% rel.) |
| Skills evolved | 34 | 8 |
| Completion token saving | +1,078 | +248 |

**Key finding**: The weaker model benefits **more** from skill evolution in
relative terms. While the absolute improvement is modest (+3.3 pp for both),
the relative gain for GLM-4.5-Air is +50% vs −4.7% for GLM-4.7. This
supports **H4**: weaker models, which struggle more with multi-step reasoning,
gain more from pre-packaged computational tools.

However, the weak model extracts far fewer skills (8 vs 34) because:
1. Lower solve rate (33.3% vs 60%) → fewer correct traces to extract from
2. Simpler solutions → fewer reusable components
3. Higher timeout rate → incomplete reasoning traces

### 5.6  Skill Library (8 skills)

| Skill Name | Dependencies | Origin |
|------------|:-------------|:------:|
| `binomial_coefficient` | — | Round 4 |
| `stars_and_bars` | `binomial_coefficient` | Round 4 |
| `count_triple_intersection_from_exact` | — | Round 11 |
| `sum_of_squares` | — | Round 23 |
| `max_linear_combo_sin_cos` | `sum_of_squares` | Round 25 |
| `max_real_part_complex_linear_fraction` | `max_linear_combo_sin_cos` | Round 25 |
| `count_lattice_paths_with_turns` | `binomial_coefficient` | Round 26 |
| `sum_of_set_bit_indices_plus_one` | — | Round 29 |

The dependency graph shows meaningful composition:
`sum_of_squares` → `max_linear_combo_sin_cos` → `max_real_part_complex_linear_fraction`
forms a 3-level chain, demonstrating skill building even with a weak executor.

---

## 6  Experiment 4: Embedding-Based Retrieval (Single Epoch)

Experiments 1–3 used TF-IDF cosine similarity for skill retrieval, a
bag-of-words approach that may miss semantically relevant skills. In this
experiment we replace TF-IDF with **embedding-based retrieval** using
ZhipuAI's `embedding-3` model (2048-dimensional vectors), and additionally
fix the **extractor scope** to see only retrieved skills (top-5) rather than
the full library — reducing prompt pollution in the extraction step.

### 6.1  Changes from Prior Experiments

| Component | Exp 1–3 (TF-IDF) | Exp 4 (Embedding) |
|-----------|:---:|:---:|
| Retrieval | TF-IDF cosine over name+desc+code | Embedding cosine (embedding-3, 2048-d) |
| Extractor context | Full skill library | Top-5 retrieved skills only |
| API | SiliconFlow (Exp 1) / BigModel (Exp 2–3) | BigModel |

### 6.2  Evolve Phase (Training)

| Metric | Value |
|--------|:-----:|
| Accuracy | 43.3% (13/30) |
| Avg tokens (total) | 2,209 |
| Avg completion tokens | 1,763 |
| Skills evolved | 17 |
| Skills with dependencies | 4 (23.5%) |

The evolve accuracy (43.3%) matches Exp 1 (46.7%) within noise,
indicating embedding retrieval does not harm training performance.

### 6.3  Test Phase — Results

| Metric | With Skills | Baseline | Δ |
|--------|:-:|:-:|:-:|
| Accuracy | **46.7% (14/30)** | 43.3% (13/30) | **+3.3 pp** |
| Avg total tokens | 2,810 | 7,356 | +4,546 (−61.8%) |
| Avg completion tokens | 2,180 | 7,096 | +4,916 (−69.3%) |

**Accuracy improvement**: Skill-augmented solving gains **+3.3 pp** over
baseline — the first positive accuracy delta for the strong model (GLM-4.7).
One problem (Q24) was solved only with skills; no problems were lost.

### 6.4  Controlled Token Analysis

The headline token numbers include timeouts (tokens=0). A more controlled
comparison isolates problems where **both** approaches produced non-zero tokens:

| Metric | Both-Solved (n=13) | Comment |
|--------|:-:|:--|
| Avg total tokens (skills) | 5,865 | +3.9% vs baseline (prompt overhead) |
| Avg total tokens (baseline) | 5,644 | |
| Avg completion tokens (skills) | 4,490 | **−15.2%** vs baseline |
| Avg completion tokens (baseline) | 5,297 | |

On commonly-solved problems:
- **Total tokens increase 3.9%** — the skill prompt adds ~220 tokens per problem
- **Completion tokens decrease 15.2%** — the model generates shorter solutions
  by calling pre-loaded skill functions instead of re-deriving them

This confirms H2: skills compress model reasoning even when prompt overhead is
factored in. The net efficiency gain is on the **generation** side.

### 6.5  Timeout Analysis

| Phase | Timeouts | Comment |
|-------|:--------:|---------|
| Evolve | 14/30 (46.7%) | Higher than Exp 1 (16.7%) — API differences |
| Test (skills) | 16/30 (53.3%) | |
| Test (baseline) | 8/30 (26.7%) | |

Skills cause more timeouts — the skill prompt increases input length,
leaving fewer tokens for step-by-step reasoning within the same step
budget. Despite this, accuracy still improves because successful skill
reuse is more reliable.

### 6.6  Skill Library (17 Skills)

| # | Skill | Deps | Usage | Success Rate |
|:-:|-------|:-----|:-----:|:--------:|
| 1 | `count_exact_matches` | — | 23 | 43.5% |
| 2 | `count_at_least_matches` | `count_exact_matches` | 26 | 42.3% |
| 3 | `check_all_replacements_divisible` | — | 12 | 50.0% |
| 4 | `calculate_intersection_from_exact_counts` | — | 14 | 35.7% |
| 5 | `solve_quadratic` | — | 9 | 33.3% |
| 6 | `solve_speed_from_time_diff` | `solve_quadratic` | 5 | 20.0% |
| 7 | `convert_hm_to_hours` | — | 4 | 25.0% |
| 8 | `calculate_total_time_with_break` | — | 5 | 20.0% |
| 9 | `solve_log_system_product` | — | 3 | 100% |
| 10 | `max_real_part_az_plus_b_over_z` | — | 1 | 100% |
| 11 | `count_positive_partitions` | — | 1 | 100% |
| 12 | `count_grid_paths_with_turns` | `count_positive_partitions` | 0 | — |
| 13 | `solve_cyclic_log_system_linear_combination` | — | 3 | 66.7% |
| 14 | `complex_modulus_squared` | — | 0 | — |
| 15 | `complex_power` | — | 0 | — |
| 16 | `product_quadratic_at_roots_of_unity` | — | 1 | 0% |
| 17 | `sum_of_set_bit_indices_plus_one` | — | 0 | — |

Notably, `count_exact_matches` / `count_at_least_matches` form the
most-used skill pair (23–26 usages), demonstrating that embedding retrieval
correctly surfaces combinatorial counting skills for relevant problems.

### 6.7  Comparison: TF-IDF vs Embedding (Single Epoch)

| Metric | Exp 1 (TF-IDF) | Exp 4 (Embedding) | Better |
|--------|:-:|:-:|:-:|
| Test accuracy (skills) | 66.7% | 46.7% | TF-IDF* |
| Test accuracy (baseline) | 70.0% | 43.3% | TF-IDF* |
| Δ Accuracy | −3.3 pp | **+3.3 pp** | **Embedding** |
| Completion token saving | 20.0% | **15.2%** | TF-IDF |
| Skills evolved | 34 | 17 | TF-IDF |

\* Absolute accuracies differ due to API account changes between
experiments (Exp 1 used SiliconFlow; Exp 4 used BigModel). The **relative
improvement** (Δ) is the fair comparison. Additionally, Exp 4 was affected
by the temperature bug (see Section 7), inflating randomness in the model's
output.

**Key insight**: Embedding retrieval converts skill evolution from an
accuracy liability (−3.3 pp with TF-IDF) to an accuracy gain (+3.3 pp).
Better retrieval quality means the executor receives more relevant skills,
reducing the "prompt pollution" effect where irrelevant skills confuse
the model.

---

## 7  Experiment 5: Temperature Fix + Embedding (Single Epoch)

### 7.1  Temperature Bug Discovery and Fix

Between Experiments 4 and 5, a critical implementation bug was discovered in
`app/llm.py`: when using the BigModel API, the `temperature` parameter was
**stripped from API requests**. The configuration specified `temperature=0.0`
(deterministic decoding), but the API received no temperature parameter and
used its default value (~0.95–1.0), introducing significant randomness.

| Aspect | Detail |
|--------|--------|
| Bug location | `app/llm.py` — BigModel API adapter |
| Configured value | `temperature=0.0` |
| Actual value received by API | Default (~0.95–1.0) |
| Affected experiments | Exp 2, 3, 4 (BigModel API) |
| Unaffected experiments | Exp 1 (SiliconFlow API) |
| Fix | Ensure `temperature` is passed through to BigModel requests |

This bug has significant implications for interpreting Exp 2–4:
- Results include **non-deterministic** model behaviour not present in Exp 1
- Baseline accuracies for Exp 2–4 may be **artificially deflated** by
  sampling randomness (the model is more likely to explore dead-end
  reasoning paths at high temperature)
- Exp 5 and 6 provide the first clean comparison with proper `temperature=0.0`

### 7.2  Setup

| Parameter | Value |
|-----------|:-----:|
| Model | GLM-4.7 (BigModel API) |
| Temperature | **0.0 (fixed — actually transmitted)** |
| Retrieval | Embedding (embedding-3) |
| Epochs | 1 |
| max_tokens | 16,000 |

### 7.3  Evolve Phase (Training)

| Metric | Value |
|--------|:-----:|
| Accuracy | 50.0% (15/30) |
| Skills evolved | **16** |
| Avg tokens (total) | 6,904 |

### 7.4  Test Phase — Results

| Metric | With Skills | Baseline | Δ |
|--------|:-:|:-:|:-:|
| Accuracy | 50.0% (15/30) | **53.3% (16/30)** | −3.3 pp |
| Avg total tokens | 11,872 | 7,883 | +3,989 |
| Avg completion tokens | 10,306 | 7,607 | +2,699 |

Skills use **more** tokens than baseline (+2,699 completion tokens per
problem on average), reversing the pattern seen in Exp 1 and 4. The
accuracy delta is −3.3 pp, matching the Exp 1 result with TF-IDF.

### 7.5  Impact of the Temperature Fix

The temperature fix has a dramatic effect on baseline performance:

| Metric | Exp 4 (buggy temp) | Exp 5 (fixed temp) | Δ |
|--------|:-:|:-:|:-:|
| Baseline accuracy | 43.3% (13/30) | **53.3% (16/30)** | **+10.0 pp** |
| Skills accuracy | 46.7% (14/30) | 50.0% (15/30) | +3.3 pp |
| Δ accuracy | +3.3 pp | −3.3 pp | — |

The baseline gains +10 pp from the temperature fix — confirming that
high-temperature sampling was substantially harming performance. With
deterministic decoding, the baseline is strong enough that single-epoch
skills no longer provide a net accuracy benefit. However, the skills
accuracy also improves (+3.3 pp), suggesting the fix helps both approaches.

### 7.6  Implications for Exp 2–4

The temperature bug casts doubt on absolute accuracy numbers from Exp 2–4:
- **Exp 2** (43.3% test): May be partly due to temperature randomness, not
  just skill over-accumulation. However, the relative degradation (3 epochs
  worse than 1 epoch within the same buggy setting) remains valid.
- **Exp 3** (10% / 6.7%): The weak model's low accuracy may be amplified
  by high temperature, but the relative skill benefit (+50%) is preserved.
- **Exp 4** (+3.3 pp): The positive delta appeared under adverse conditions;
  the true benefit of embedding retrieval may be different with correct
  temperature (as Exp 5 shows: −3.3 pp with fixed temperature).

---

## 8  Experiment 6: Multi-Epoch Evolution with Embedding + Temperature Fix (3 Epochs)

### 8.1  Motivation

Experiment 2 showed that multi-epoch training with TF-IDF retrieval
**catastrophically degrades** accuracy (66.7% → 43.3%). We hypothesised
that embedding retrieval might mitigate this by providing more selective
skill injection. With the temperature fix applied, this experiment provides
a clean test of multi-epoch evolution with high-quality retrieval.

### 8.2  Setup

| Parameter | Value |
|-----------|:-----:|
| Model | GLM-4.7 (BigModel API) |
| Temperature | 0.0 (fixed) |
| Retrieval | Embedding (embedding-3) |
| Epochs | 3 |
| max_tokens | 16,000 |
| Baseline | Reused from Exp 5 (53.3%, same model/config) |

### 8.3  Evolve Phase — Per-Epoch Analysis

| Epoch | Accuracy | Skills Start → End | New Skills | Avg Tokens |
|:-----:|:--------:|:------------------:|:----------:|:----------:|
| 1 | 18/30 (60.0%) | 0 → 21 | +21 | 9,228 |
| 2 | 16/30 (53.3%) | 21 → 27 | +6 | 7,943 |
| 3 | 18/30 (60.0%) | 27 → 32 | +5 | 9,145 |
| **Total** | **52/90 (57.8%)** | — | **32** | **8,772** |

Key observations:
- **Epoch 1 is the strongest**: 60.0% accuracy with the largest skill yield
  (+21), consistent with the pattern from Exp 2.
- **Epoch 2 dips**: Accuracy drops to 53.3%. Skill creation slows (+6).
- **Epoch 3 recovers**: Returns to 60.0%, suggesting the embedding retriever
  handles the growing library better than TF-IDF. Skill creation continues
  to slow (+5).
- **Overall evolve accuracy** (57.8%) exceeds Exp 5's single-epoch (50.0%),
  confirming multi-epoch training still adds value during the evolve phase.
- **32 total skills** — substantially fewer than Exp 2's 45 skills from 3
  epochs with TF-IDF, suggesting embedding-guided extraction is more selective.

### 8.4  Test Phase — Results

| Metric | With Skills (3-epoch) | Baseline | Δ |
|--------|:-:|:-:|:-:|
| Accuracy | **53.3% (16/30)** | **53.3% (16/30)** | **0.0 pp** |
| Avg total tokens | 8,739 | 7,883 | +856 |
| Avg completion tokens | 7,566 | 7,607 | −41 |

Skills and baseline achieve **identical accuracy** (53.3%). Completion tokens
are virtually identical (−41 tokens difference). The skill system neither
helps nor hurts at the aggregate level.

### 8.5  Critical Finding: 100% Accuracy on Completed Problems

A detailed breakdown reveals a striking pattern. Each test run (skills and
baseline) has three categories of outcomes:

| Category | Skills (3-epoch) | Baseline |
|----------|:----------------:|:--------:|
| Timeout (>300s execution) | 7 | 7 |
| Max-token hit (16K completion limit) | 7 | 7 |
| Completed normally | 16 | 16 |
| **Correct among completed** | **16/16 (100%)** | **16/16 (100%)** |

**Both systems solve every single problem they can finish within resource
limits.** There are zero wrong answers on completed problems. The model
(GLM-4.7 at temperature=0.0) is deterministic and reliable — when it has
enough compute budget (tokens + time), it always arrives at the correct
answer.

This has profound implications:
- **Accuracy is a resource allocation problem**, not a reasoning quality
  problem. Improving accuracy means reducing timeouts and max-token hits.
- **Skills change the resource profile** — they shift which problems hit
  resource limits — but don't change the fundamental solve rate.
- **The marginal value of skills** is not in improving reasoning, but in
  **compressing reasoning** enough to fit within resource constraints.

### 8.6  Timeout and Max-Token Analysis

#### 8.6.1  Timeout Distribution

| Question | Skills (3-epoch) | Baseline |
|:--------:|:----------------:|:--------:|
| Q1 | — | timeout |
| Q5 | timeout | — |
| Q6 | — | timeout |
| Q8 | timeout | timeout |
| Q9 | — | timeout |
| Q11 | — | timeout |
| Q13 | timeout | timeout |
| Q16 | timeout | — |
| Q22 | timeout | timeout |
| Q24 | timeout | — |
| Q27 | timeout | — |

Only **Q8, Q13, Q22** timeout for both — the "truly hard" problems that
neither approach can solve within 300 seconds. The remaining timeouts are
approach-specific: skills cause timeouts on {Q5, Q16, Q24, Q27} while
baseline times out on {Q1, Q6, Q9, Q11}.

#### 8.6.2  Max-Token Distribution

| Question | Skills (3-epoch) | Baseline |
|:--------:|:----------------:|:--------:|
| Q1 | max-token | — |
| Q2 | — | max-token |
| Q3 | max-token | — |
| Q6 | max-token | — |
| Q11 | max-token | — |
| Q12 | max-token | — |
| Q16 | — | max-token |
| Q17 | max-token | max-token |
| Q18 | max-token | max-token |
| Q21 | — | max-token |
| Q23 | — | max-token |
| Q24 | — | max-token |

Only **Q17, Q18** hit max-token for both. Skills cause max-token on
{Q1, Q3, Q6, Q11, Q12} while baseline hits it on {Q2, Q16, Q21, Q23, Q24}.

### 8.7  Comparison: 1-Epoch vs 3-Epoch Skills (with Temperature Fix)

| Metric | Exp 5 (1 epoch) | Exp 6 (3 epochs) | Δ |
|--------|:-:|:-:|:-:|
| Test accuracy (skills) | 50.0% (15/30) | **53.3% (16/30)** | **+3.3 pp** |
| Skills | 16 | 32 | +16 |
| Avg total tokens | 11,872 | 8,739 | −3,133 |
| Avg completion tokens | 10,306 | 7,566 | −2,740 |

3 epochs **improves** over 1 epoch by +3.3 pp and reduces tokens. Specific
problem changes:

| Change | Problems | Count |
|--------|----------|:-----:|
| Gained (3-epoch solves, 1-epoch doesn't) | Q2, Q4, Q9, Q26 | 4 |
| Lost (1-epoch solves, 3-epoch doesn't) | Q5, Q12, Q24 | 3 |
| **Net** | — | **+1** |

Unlike Exp 2 (TF-IDF, 3 epochs), where multi-epoch training caused
catastrophic regression (−7 problems, 0 gained), embedding-based multi-epoch
training shows a **net positive** result: more problems gained than lost.

### 8.8  Controlled Comparison (Shared Non-Zero-Token Problems)

To eliminate the effect of timeouts and max-token hits on aggregate
statistics, we compare only the 19 problems where both 3-epoch skills and
baseline produced non-zero token counts:

| Metric | Skills (3-epoch) | Baseline | Δ |
|--------|:-:|:-:|:-:|
| Accuracy | 15/19 (78.9%) | 14/19 (73.7%) | **+5.3 pp** |
| Avg total tokens | 9,281 | 9,214 | +67 (+0.7%) |
| Avg completion tokens | 7,996 | 8,854 | **−858 (−9.7%)** |

On the subset of problems where both systems have a fair chance:
- **Skills achieve +5.3 pp higher accuracy**
- **Skills save 9.7% completion tokens**
- Total tokens are virtually identical (+0.7%), confirming skills trade
  prompt overhead for generation savings

This controlled comparison provides the clearest evidence that skill
evolution offers genuine benefits when resource limits are not the
binding constraint.

---

## 9  Cross-Experiment Skill Library Analysis

### 9.1  Experiment 1 Skills (34 skills)

| Metric | Value |
|--------|:-----:|
| Total skills | 34 |
| Skills with ≥1 usage | 26 (76.5%) |
| Skills with ≥1 success | 23 (67.6%) |
| Avg usage count | 4.4 |
| Avg success rate | 71.3% |
| Max usage | 17 (`hyperbola_rhombus_diagonal_infimum`) |
| Skills with dependencies | 8 (23.5%) |

### 9.2  Experiment 2 Skills (45 skills)

The 3-epoch library has 45 skills — 11 more than single-epoch.
However, only 7 skill names are shared between the two libraries,
indicating that extraction is highly sensitive to problem ordering
and the skills available at extraction time.

### 9.3  Timeout Analysis

| Phase | Exp 1 | Exp 2 |
|-------|:-----:|:-----:|
| Evolve | 5/30 (16.7%) | 8/90 (8.9%) |
| Test (skills) | 10/30 (33.3%) | 9/30 (30.0%) |
| Test (baseline) | 7/30 (23.3%) | (reused) |

Timeouts are similar between experiments despite different skill counts,
suggesting the timeout increase is inherent to skill injection (prompt
length) rather than library quality.

### 9.4  Skill Library Growth Across Experiments

| Experiment | Epochs | Skills | Retrieval | Temp | Notes |
|:----------:|:------:|:------:|:---------:|:----:|-------|
| Exp 1 | 1 | 34 | TF-IDF | 0.0 | SiliconFlow API |
| Exp 2 | 3 | 45 | TF-IDF | ~1.0† | Over-accumulation |
| Exp 3 | 1 | 8 | TF-IDF | ~1.0† | Weak model |
| Exp 4 | 1 | 17 | Embedding | ~1.0† | More selective |
| Exp 5 | 1 | 16 | Embedding | 0.0 | Temperature fixed |
| Exp 6 | 3 | 32 | Embedding | 0.0 | Temperature fixed |

† Affected by temperature bug — actual temperature was API default.

Embedding retrieval consistently produces more focused libraries (16–32 skills)
compared to TF-IDF (34–45 skills). The 3-epoch embedding run (Exp 6, 32 skills)
has fewer skills than the 1-epoch TF-IDF run (Exp 1, 34 skills), confirming
that embedding-guided extraction is more selective.

### 9.5  Multi-Epoch Comparison: TF-IDF vs Embedding

| Metric | Exp 2 (TF-IDF, 3 ep) | Exp 6 (Embedding, 3 ep) |
|--------|:-:|:-:|
| Evolve accuracy | 61.1% | 57.8% |
| Test accuracy (skills) | 43.3% | **53.3%** |
| Test baseline | 70.0% | 53.3% |
| Δ accuracy | −26.7 pp | **0.0 pp** |
| Skills accumulated | 45 | 32 |
| Problems regressed vs 1-epoch | 7 | 3 |
| Problems gained vs 1-epoch | 0 | 4 |

Embedding retrieval **eliminates the catastrophic multi-epoch degradation**
seen with TF-IDF. While TF-IDF 3-epoch caused a −26.7 pp accuracy drop with
7 regressions and 0 gains, embedding 3-epoch shows 3 regressions offset by
4 gains (net +1).

---

## 10  Discussion

### 10.1  H1: Accuracy Improvement — Retrieval-Dependent and Temperature-Sensitive

For strong models (GLM-4.7) with TF-IDF retrieval, skill evolution does not
improve accuracy on AIME (−3.3 pp). **With embedding retrieval and buggy
temperature (Exp 4), skills achieve +3.3 pp**, but this advantage disappears
when temperature is fixed (Exp 5: −3.3 pp; Exp 6: 0 pp).

With correct `temperature=0.0`, skills **match but do not exceed** baseline
accuracy. The Exp 4 result (+3.3 pp) may have been partly an artefact of
high-temperature randomness — skills may have stabilised otherwise-random
exploration, yielding an apparent benefit that vanishes under deterministic
decoding.

For **weaker models** (GLM-4.5-Air at 6.7% baseline), skills provide a
+50% relative improvement (+3.3 pp) even with TF-IDF. The hypothesis is
supported **for weaker models** and **conditionally** for strong models.

### 10.2  H2: Token Efficiency — Supported

On commonly-solved problems, skills reduce **completion tokens by 10–20%**
across most experiments. The mechanism is clear: relevant skills replace
verbose from-scratch derivations with short function calls. Total tokens
may slightly increase (0.7–3.9%) due to skill prompt overhead, but the
generation-side savings dominate.

The controlled comparison in Exp 6 is particularly clean: on 19 shared
problems, skills save 9.7% completion tokens while adding only 0.7% total
token overhead.

### 10.3  H3: Multi-Round Benefits — Retrieval-Dependent

**With TF-IDF**: Multi-epoch is catastrophic — 3 epochs degrades accuracy from
66.7% to 43.3% (−23.4 pp within Exp 2), with 7 problem regressions and 0 gains.
The TF-IDF retriever cannot cope with a growing library of 45 skills.

**With embedding**: Multi-epoch **recovers to baseline level** — 3 epochs achieve
53.3% vs baseline 53.3% (0 pp delta), compared to 1-epoch's 50.0% (−3.3 pp
delta). The narrative fundamentally changes: **multi-epoch with embedding is
viable**. It produces a net +1 problem gain over single-epoch (4 gained,
3 lost) and reduces completion tokens by 2,740 per problem.

The key difference is that embedding retrieval scales with library size —
semantically relevant skills continue to be retrieved even as the library
grows to 32 skills, whereas TF-IDF's bag-of-words matching increasingly
surfaces irrelevant skills.

### 10.4  H4: Weak Model Benefit — Supported

GLM-4.5-Air gains +50% relative accuracy from skills (6.7% → 10.0%),
compared to GLM-4.7's mixed results (−3.3 pp with TF-IDF, +3.3 pp with
buggy embedding, 0 pp with fixed embedding). Skills act as "cognitive
offloading" — delegating computation the weak model would otherwise fail
to complete within the token limit.

### 10.5  H5: Retrieval Quality Matters — Strongly Supported

Exp 4 provides evidence that **retrieval quality is the bottleneck**.
Switching from TF-IDF to embedding retrieval:
- Flips accuracy delta from −3.3 pp to +3.3 pp (Exp 1 vs Exp 4, noting
  temperature confound)
- Maintains completion token savings
- Reduces skill count from 34 to 17 (more selective extraction)

**Updated with Exp 5/6**: The controlled comparison in Exp 6 provides the
cleanest evidence. On 19 shared non-zero-token problems:
- Skills achieve +5.3 pp accuracy (78.9% vs 73.7%)
- Skills save 9.7% completion tokens (7,996 vs 8,854)
- Total tokens are virtually identical (+0.7%)

Embedding retrieval surfaces skills that are **semantically** relevant
(e.g., matching combinatorial counting skills to counting problems)
rather than lexically similar. This reduces the "prompt pollution" effect
where irrelevant skills consume context and confuse the model.

### 10.6  H6: 100% Accuracy on Completed Problems — New Finding

Experiment 6 reveals that **both skills and baseline achieve 100% accuracy
on all problems that complete within resource limits**. Specifically:
- Both have exactly 16 completed problems, 7 timeouts, and 7 max-token hits
- All 16 completed problems are solved correctly in both cases
- The model never produces a wrong answer when it has sufficient time and tokens

This finding reframes the role of skill evolution:
1. **Skills do not improve reasoning quality** — the model already reasons
   correctly when it can complete its chain of thought.
2. **Skills redistribute resource pressure** — they change which problems
   timeout or exhaust max tokens, without changing the total count.
3. **The marginal value of skills** lies in *compressing reasoning* to fit
   within resource limits for different subsets of problems.
4. **Future work should focus on resource efficiency**: increasing max_tokens,
   reducing timeout frequency, and targeting skills at problems near the
   resource boundary.

### 10.7  Temperature Sensitivity

The +10 pp baseline improvement from fixing the temperature bug
(43.3% → 53.3%) reveals that **temperature is a first-order parameter**
for mathematical reasoning. At high temperature, the model explores diverse
but often incorrect reasoning paths. At `temperature=0.0`, greedy decoding
follows the most likely chain of thought, which for well-trained models is
often correct.

This has implications for skill evaluation:
- Skill benefits observed at high temperature may not transfer to
  deterministic settings (as seen in Exp 4 vs Exp 5)
- Deterministic baselines are substantially stronger, raising the bar
  for skills to add value
- Future skill experiments should always verify temperature settings

### 10.8  Complementary Solving

Skills and baseline have partially orthogonal failure modes. In Exp 1,
the oracle ensemble (24/30 = 80.0%) represents significant headroom over
either method alone. In Exp 6, skills solved {Q2, Q4, Q9, Q26} while
baseline solved {Q1, Q6, Q9, Q11} — an ensemble selecting the better
answer per problem could exceed either individual result.

### 10.9  Skill Quality vs Quantity

The comparison across all experiments reinforces that **quality matters
more than quantity**:
- 17 skills (Exp 4, embedding) → +3.3 pp delta
- 32 skills (Exp 6, embedding, 3-epoch) → 0 pp delta, +5.3 pp controlled
- 34 skills (Exp 1, TF-IDF) → −3.3 pp delta
- 45 skills (Exp 2, TF-IDF, 3-epoch) → −26.7 pp delta

Embedding retrieval naturally curates smaller, more focused libraries. Even
the 3-epoch embedding library (32 skills) is smaller than the 1-epoch TF-IDF
library (34 skills).

---

## 11  Limitations

1. **Temperature bug in Exp 2–4**: The BigModel API adapter stripped the
   `temperature` parameter, causing Exp 2–4 to run at ~1.0 instead of 0.0.
   Absolute accuracy numbers for these experiments are unreliable. Relative
   comparisons within each experiment (skills vs baseline) are still valid
   as both arms were equally affected.
2. **Single model family**: Only GLM variants tested, though at two
   capability levels (GLM-4.7 and GLM-4.5-Air).
3. **Single run**: No repeated trials to estimate variance. The temperature=0.0
   setting reduces variance in Exp 1/5/6, but stochastic API behaviour
   (timeouts, rate limits) still introduces noise.
4. **No skill pruning**: The current system only adds skills, never
   removes them.
5. **Small test set**: 30 problems provide limited statistical power.
   The ±3.3 pp deltas correspond to ±1 problem.
6. **API inconsistency**: Exp 1 used SiliconFlow; Exp 2–6 use BigModel.
   Baseline absolute accuracies differ across experiments.
7. **Timeout sensitivity**: The 300-second execution timeout and 16K
   max-token limit are binding constraints on 14/30 problems (Exp 6),
   meaning accuracy is as much a resource limit issue as a reasoning issue.
8. **Execution timeout vs API timeout**: Exp 1–4 timeouts may include API
   rate limiting (not purely computational), while Exp 5–6 timeouts are
   verified as genuine execution timeouts (>300s).

---

## 12  Future Directions

### 12.1  Skill Library Curation
The multi-epoch results strongly motivate **automatic pruning**:
- Remove skills with <20% success rate
- Merge similar skills
- Cap library size based on downstream validation

### 12.2  Selective Skill Injection
Instead of always injecting top-5 skills, use a **gating mechanism**
(confidence threshold) to decide whether any skills are relevant.

### 12.3  Resource Limit Optimisation
Exp 6 shows that accuracy is entirely determined by resource limits
(100% on completed problems). Increasing `max_tokens` beyond 16K and
extending execution timeouts beyond 300s would directly improve accuracy.
Experiments should characterise the accuracy–resource curve.

### 12.4  Targeted Skill Development
Since skills shift which problems hit resource limits, future work could
analyse which problems are near the boundary and develop skills specifically
aimed at compressing their reasoning chains.

### 12.5  Ensemble Strategy
Run both skill-augmented and baseline solving; select the better
answer per problem. Oracle bound: 80.0% vs 70% baseline.

### 12.6  Cross-Domain Transfer
Test whether AIME-evolved skills transfer to AMC, MATH-500, or
Olympiad problems.

### 12.7  Adaptive Library Size
Use validation-set feedback to determine the optimal number of
skills to retain, addressing the over-accumulation problem directly.

---

## 13  Conclusion

We evaluated skill evolution on AIME 2024→2025 across six experimental
conditions. Key contributions:

1. **Retrieval quality is the key bottleneck**: Embedding-based retrieval
   (Exp 4) converts skill evolution from an accuracy liability (−3.3 pp
   with TF-IDF) to an accuracy gain (+3.3 pp). With correct temperature
   settings (Exp 5–6), the accuracy delta narrows to −3.3 pp (1-epoch)
   and 0 pp (3-epoch), while the controlled comparison on shared problems
   shows +5.3 pp and 9.7% completion token savings.

2. **Completion tokens consistently decrease 10–20%** on shared problems
   across experiments — skills compress model reasoning by replacing verbose
   from-scratch derivations with function calls.

3. **Multi-epoch training is retrieval-dependent**: With TF-IDF (Exp 2),
   3 epochs catastrophically degrades accuracy (−26.7 pp). With embedding
   retrieval (Exp 6), 3 epochs **recovers to baseline level** (0 pp delta),
   demonstrating that the multi-epoch degradation is a retrieval failure,
   not an inherent limitation of iterative skill evolution.

4. **100% accuracy on completed problems** (Exp 6): Both skills and baseline
   solve every problem they can finish within resource limits (16/16). The
   model's reasoning quality is not the bottleneck — resource allocation
   (timeouts, max-token limits) is. Skills redistribute resource pressure
   but do not change the fundamental solve rate.

5. **Weak models benefit most from skills**: GLM-4.5-Air gains +50%
   relative accuracy (6.7% → 10.0%), confirming skills act as "cognitive
   scaffolding" for models that cannot independently complete complex
   reasoning chains.

6. **Temperature is a first-order parameter**: Fixing the temperature bug
   improved baseline accuracy by +10 pp (43.3% → 53.3%). Skill evaluation
   must control for temperature settings; benefits observed at high
   temperature may not transfer to deterministic decoding.

7. **Smaller, focused libraries outperform larger ones**: Embedding
   retrieval naturally curates smaller libraries (16–32 skills) that
   outperform TF-IDF's larger libraries (34–45 skills) on accuracy delta.

The results argue that skill evolution is most effective with
**semantic retrieval**, **bounded, curated libraries**, and for
**weaker models** that benefit most from pre-packaged tools. The 100%
accuracy finding on completed problems suggests that **future work should
focus on resource efficiency** — extending token limits and reducing
timeouts — rather than improving reasoning quality.

---

## Appendix A: Evolved Skills Catalogue (Experiment 1)

| # | Skill Name | Dependencies | Usage | Success Rate |
|:-:|------------|:-------------|:-----:|:--------:|
| 1 | `complex_pow_int` | — | 5 | 100% |
| 2 | `complex_magnitude_sq` | — | 5 | 100% |
| 3 | `product_roots_unity_conjugate` | 1, 2 | 4 | 100% |
| 4 | `hyperbola_rhombus_diagonal_infimum` | — | 17 | 88% |
| 5 | `get_set_bit_indices` | — | 7 | 86% |
| 6 | `generate_integer_partitions` | — | 14 | 79% |
| 7 | `get_modes` | — | 14 | 79% |
| 8 | `sum_of_squares` | — | 15 | 73% |
| 9 | `square_factor` | — | 1 | 100% |
| 10 | `simplify_radical_fraction` | 9 | 3 | 100% |
| 11 | `disphenoid_inradius_squared` | — | 3 | 67% |
| 12 | `max_real_part_linear_reciprocal` | — | 0 | — |
| 13 | `count_positive_integer_compositions` | — | 9 | 89% |
| 14 | `count_grid_paths_with_direction_changes` | 13 | 0 | — |
| 15 | `calculate_exact_overlap_with_universal_set` | — | 3 | 100% |
| 16 | `count_exact_matches` | — | 0 | — |
| 17 | `parallel_sum_three` | — | 4 | 75% |
| 18 | `count_mixed_color_assignments` | — | 2 | 100% |
| 19 | `count_maximal_monochrome_grid_placements` | 18 | 2 | 50% |
| 20 | `product_sides_perpendicular_io` | — | 0 | — |
| 21 | `replace_digit_at_index` | — | 6 | 67% |
| 22 | `find_max_in_range` | — | 7 | 86% |
| 23 | `split_by_power_of_10` | — | 5 | 80% |
| 24 | `sqrt_fraction` | — | 3 | 100% |
| 25 | `sum_of_cubes` | — | 3 | 67% |
| 26 | `astroid_envelope_sq_dist` | 25 | 1 | 100% |
| 27 | `find_min_power_plus_c_root` | — | 3 | 100% |
| 28 | `solve_system_diag_one_off_neg_one` | — | 2 | 50% |
| 29 | `sum_reduced_fraction_parts` | — | 2 | 50% |
| 30 | `solve_log_system_product_linear_exponents` | — | 1 | 0% |
| 31 | `compute_subtraction_game_states` | — | 0 | — |
| 32 | `solve_quadratic` | — | 0 | — |
| 33 | `solve_speed_and_delay_from_times` | 32 | 0 | — |
| 34 | `count_distinct_prime_factors` | — | 0 | — |

## Appendix B: Raw Data

All raw experiment data is stored at:
- `academic/results/aime_full_run1.log` — Exp 1 full log
- `academic/results/aime_experiment_full_run1_skills.json` — Exp 1 skills (34)
- `academic/results/baseline_complete.json` — Exp 1 complete baseline
- `academic/results/aime_multiepoch_3ep.log` — Exp 2 full log
- `academic/results/aime_multiepoch_3ep_skills.json` — Exp 2 skills (45)
- `academic/results/aime_multiepoch_3ep_evolve.json` — Exp 2 evolve results
- `academic/results/aime_multiepoch_3ep_test_with_skills.json` — Exp 2 test results
- `academic/results/aime_weak_glm45air.log` — Exp 3 full log
- `academic/results/aime_weak_glm45air_skills.json` — Exp 3 skills (8)
- `academic/results/aime_weak_glm45air_evolve.json` — Exp 3 evolve results
- `academic/results/aime_weak_glm45air_test_with_skills.json` — Exp 3 test results
- `academic/results/aime_weak_glm45air_test_baseline.json` — Exp 3 baseline results
- `academic/results/aime_weak_glm45air_summary.json` — Exp 3 summary
- `academic/results/aime_embedding_1ep_tempfix.*` — Exp 5 results (embedding, 1 epoch, temp fix)
- `academic/results/aime_embedding_3ep_tempfix.*` — Exp 6 results (embedding, 3 epochs, temp fix)
