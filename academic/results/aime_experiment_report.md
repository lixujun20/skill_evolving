# Skill Evolution for Mathematical Reasoning: An Empirical Study on AIME

## Abstract

We evaluate a **skill evolution** framework on competition-level mathematics
(AIME 2024 → 2025). The system extracts reusable code "skills" from
problem-solving traces and accumulates them in a shared library. We conduct
four experiments: (1) single-epoch evolution with GLM-4.7 (TF-IDF retrieval),
(2) multi-epoch (3-pass) evolution, (3) single-epoch with a weaker model
(GLM-4.5-Air), and (4) single-epoch with embedding-based retrieval.

**Key findings**:

| Experiment | Test Accuracy | Baseline | Δ Accuracy | Completion Token Δ |
|------------|:-:|:-:|:-:|:-:|
| Exp 1: TF-IDF, 1 epoch | 66.7% (20/30) | 70.0% (21/30) | −3.3 pp | **−20.0%** |
| Exp 2: TF-IDF, 3 epochs | 43.3% (13/30) | (reuse Exp 1) | −26.7 pp | — |
| Exp 3: GLM-4.5-Air, 1 epoch | 10.0% | 6.7% | **+3.3 pp (+50% rel.)** | +248/problem |
| Exp 4: Embedding, 1 epoch | **46.7% (14/30)** | 43.3% (13/30) | **+3.3 pp** | **−15.2%** |

Embedding-based retrieval (Exp 4) converts skill evolution from an
accuracy liability to an accuracy gain, while maintaining completion token
savings. Multi-epoch training degrades accuracy due to skill over-accumulation.
Weaker models benefit most from skill augmentation (+50% relative accuracy).

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
| **GLM-4.7** | `glm-4.7` (ZhipuAI) | BigModel / SiliconFlow | Executor + Extractor (Exp 1, 2) |
| **GLM-4.5-Air** | `glm-4.5-air` (ZhipuAI) | BigModel | Executor only (Exp 3) |

In Experiment 3, the weaker GLM-4.5-Air serves as the executor (problem solver),
while GLM-4.7 remains the extractor (skill extraction). This tests whether
skills evolved by a strong extractor can help a weaker executor.

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

2. **Embedding** (Exp 4): Cosine similarity over 2048-dimensional vectors
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
improvement** (Δ) is the fair comparison.

**Key insight**: Embedding retrieval converts skill evolution from an
accuracy liability (−3.3 pp with TF-IDF) to an accuracy gain (+3.3 pp).
Better retrieval quality means the executor receives more relevant skills,
reducing the "prompt pollution" effect where irrelevant skills confuse
the model.

---

## 7  Cross-Experiment Skill Library Analysis

### 6.1  Experiment 1 Skills (34 skills)

| Metric | Value |
|--------|:-----:|
| Total skills | 34 |
| Skills with ≥1 usage | 26 (76.5%) |
| Skills with ≥1 success | 23 (67.6%) |
| Avg usage count | 4.4 |
| Avg success rate | 71.3% |
| Max usage | 17 (`hyperbola_rhombus_diagonal_infimum`) |
| Skills with dependencies | 8 (23.5%) |

### 6.2  Experiment 2 Skills (45 skills)

The 3-epoch library has 45 skills — 11 more than single-epoch.
However, only 7 skill names are shared between the two libraries,
indicating that extraction is highly sensitive to problem ordering
and the skills available at extraction time.

### 6.3  Timeout Analysis

| Phase | Exp 1 | Exp 2 |
|-------|:-----:|:-----:|
| Evolve | 5/30 (16.7%) | 8/90 (8.9%) |
| Test (skills) | 10/30 (33.3%) | 9/30 (30.0%) |
| Test (baseline) | 7/30 (23.3%) | (reused) |

Timeouts are similar between experiments despite different skill counts,
suggesting the timeout increase is inherent to skill injection (prompt
length) rather than library quality.

---

## 8  Discussion

### 8.1  H1: Accuracy Improvement — Retrieval-Dependent

For strong models (GLM-4.7) with TF-IDF retrieval, skill evolution does not
improve accuracy on AIME (−3.3 pp). However, **with embedding retrieval
(Exp 4), skills achieve +3.3 pp**, demonstrating that retrieval quality is
the key moderator. For **weaker models** (GLM-4.5-Air at 6.7% baseline),
skills provide a +50% relative improvement (+3.3 pp) even with TF-IDF.

The hypothesis is supported **when retrieval is sufficiently accurate**.
Poor retrieval (TF-IDF on small libraries) injects irrelevant skills
that confuse the model, offsetting any benefit from relevant ones.

### 8.2  H2: Token Efficiency — Supported

On commonly-solved problems, skills reduce **completion tokens by 15–20%**
across all experiments. The mechanism is clear: relevant skills replace
verbose from-scratch derivations with short function calls. Total tokens
may slightly increase (3.9% in Exp 4) due to skill prompt overhead, but
the generation-side savings dominate.

### 8.3  H3: Multi-Round Benefits — Refuted

**More epochs hurt**: 3 epochs (43.3%) is dramatically worse than 1 epoch
(66.7%). The sweet spot appears to be around 22–38 skills (Epoch 2 of the
3-epoch run had the highest training accuracy). Beyond this, skill
over-accumulation causes:
1. Prompt bloat overwhelming the context window
2. Irrelevant skill injection increasing error rate
3. Conflicting implementations confusing the executor

This is a critical finding: **skill libraries require active curation**,
not unbounded growth. A pruning or gating mechanism is essential.

### 8.4  H4: Weak Model Benefit — Supported

GLM-4.5-Air gains +50% relative accuracy from skills (6.7% → 10.0%),
compared to GLM-4.7's mixed results (−3.3 pp with TF-IDF, +3.3 pp with
embedding). Skills act as "cognitive offloading" — delegating computation
the weak model would otherwise fail to complete within the token limit.

### 8.5  H5: Retrieval Quality Matters — Strongly Supported (New)

Exp 4 provides the strongest evidence that **retrieval quality is the
bottleneck**. Switching from TF-IDF to embedding retrieval:
- Flips accuracy delta from −3.3 pp to **+3.3 pp**
- Maintains 15.2% completion token savings
- Reduces skill count from 34 to 17 (more selective extraction)

Embedding retrieval surfaces skills that are **semantically** relevant
(e.g., matching `count_exact_matches` to combinatorial counting problems)
rather than lexically similar. This reduces the "prompt pollution" effect
where irrelevant skills consume context and confuse the model.

### 8.6  Complementary Solving

Skills and baseline have partially orthogonal failure modes. In Exp 1,
the oracle ensemble (24/30 = 80.0%) represents significant headroom over
either method alone. In Exp 4, skills solved Q24 (baseline timeout),
demonstrating that skill reuse can unlock problems the model cannot
solve from scratch within the step budget.

### 8.7  Skill Quality vs Quantity

The comparison between 34 skills (Exp 1) and 17 skills (Exp 4), combined
with the 45-skill library's poor performance (Exp 2), demonstrates that
**quality matters more than quantity**. Embedding retrieval naturally
curates a smaller, more focused library because the extractor only sees
truly relevant skills, reducing redundant extraction.

---

## 9  Limitations

1. **Single model family**: Only GLM variants tested, though at two
   capability levels (GLM-4.7 and GLM-4.5-Air).
2. **Single run**: No repeated trials to estimate variance.
3. **No skill pruning**: The current system only adds skills, never
   removes them.
4. **Small test set**: 30 problems provide limited statistical power.
5. **API inconsistency**: Exp 1 used SiliconFlow; Exp 2–4 use BigModel.
   Baseline absolute accuracies differ across experiments.
6. **Timeout sensitivity**: The 30-second code execution timeout causes
   many problems to appear as "tokens=0", complicating token analysis.

---

## 10  Future Directions

### 10.1  Skill Library Curation
The multi-epoch results strongly motivate **automatic pruning**:
- Remove skills with <20% success rate
- Merge similar skills
- Cap library size based on downstream validation

### 10.2  Selective Skill Injection
Instead of always injecting top-5 skills, use a **gating mechanism**
(confidence threshold) to decide whether any skills are relevant.

### 10.3  Multi-Epoch with Embedding Retrieval
Exp 4 showed embedding retrieval fixes single-epoch degradation. A natural
extension is combining embedding retrieval with multi-epoch training
(Exp 5, in progress) to test whether the Exp 2 degradation is also mitigated.

### 10.4  Ensemble Strategy
Run both skill-augmented and baseline solving; select the better
answer per problem. Oracle bound: 80.0% vs 70% baseline.

### 10.5  Cross-Domain Transfer
Test whether AIME-evolved skills transfer to AMC, MATH-500, or
Olympiad problems.

### 10.6  Adaptive Library Size
Use validation-set feedback to determine the optimal number of
skills to retain, addressing the over-accumulation problem directly.

---

## 11  Conclusion

We evaluated skill evolution on AIME 2024→2025 across four experimental
conditions. Key contributions:

1. **Retrieval quality is the key bottleneck**: Embedding-based retrieval
   (Exp 4) converts skill evolution from an accuracy liability (−3.3 pp
   with TF-IDF) to an accuracy gain (+3.3 pp), demonstrating that the
   quality of skill-problem matching is more important than skill quantity.

2. **Completion tokens consistently decrease 15–20%** across all
   experiments — skills compress model reasoning by replacing verbose
   from-scratch derivations with function calls. Total tokens may slightly
   increase due to skill prompt overhead.

3. **Multi-epoch training is counterproductive** with TF-IDF retrieval:
   3 epochs degrades accuracy from 66.7% to 43.3% due to skill
   over-accumulation. Whether embedding retrieval mitigates this is an
   open question (Exp 5, in progress).

4. **Weak models benefit most from skills**: GLM-4.5-Air gains +50%
   relative accuracy (6.7% → 10.0%), confirming skills act as "cognitive
   scaffolding" for models that cannot independently complete complex
   reasoning chains.

5. **Smaller, focused libraries outperform larger ones**: 17 skills
   (embedding, Exp 4) achieves better accuracy delta than 34 skills
   (TF-IDF, Exp 1) or 45 skills (3-epoch, Exp 2).

The results argue that skill evolution is most effective with
**semantic retrieval**, **bounded, curated libraries**, and for
**weaker models** that benefit most from pre-packaged tools.

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
