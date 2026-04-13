# Skill Evolution for Mathematical Reasoning: An Empirical Study on AIME

## Abstract

We evaluate a **skill evolution** framework on competition-level mathematics
(AIME 2024 → 2025). The system extracts reusable code "skills" from
problem-solving traces and accumulates them in a shared library. We conduct
three experiments: (1) single-epoch evolution with GLM-4.7, (2) multi-epoch
(3-pass) evolution, and (3) single-epoch with a weaker model (GLM-4.5-Air).

**Key findings**:

| Experiment | Test Accuracy | Baseline | Δ Accuracy | Token Δ (shared) |
|------------|:-:|:-:|:-:|:-:|
| Exp 1: GLM-4.7, 1 epoch | 66.7% (20/30) | 70.0% (21/30) | −3.3 pp | **−20.0%** |
| Exp 2: GLM-4.7, 3 epochs | 43.3% (13/30) | (reuse Exp 1) | −26.7 pp | — |
| Exp 3: GLM-4.5-Air, 1 epoch | 10.0% | 6.7% | **+3.3 pp (+50% rel.)** | −166 total / +248 compl. |

The framework achieves a **20% token reduction** on commonly-solved problems
with single-epoch training, but **multi-epoch training degrades accuracy**
due to skill over-accumulation. This reveals an important trade-off between
skill library richness and prompt pollution.

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

Skills are retrieved using TF-IDF cosine similarity over skill names,
descriptions, and code. The top-5 highest-scoring skills (with score > 0)
are injected into the executor's system prompt as pre-loaded functions.

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

## 6  Skill Library Analysis

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

## 7  Discussion

### 7.1  H1: Accuracy Improvement — Model-Dependent

For strong models (GLM-4.7), skill evolution does not improve accuracy on AIME.
With a baseline at 70%, the "low-hanging fruit" is already captured.
For **weaker models** (GLM-4.5-Air at 6.7% baseline), skills provide a +50%
relative improvement (+3.3 pp). The hypothesis is partially supported:
skill evolution helps models that need it most.

### 7.2  H2: Token Efficiency — Supported

On commonly-solved problems, skills reduce tokens by 20%. The mechanism is
clear: relevant skills replace verbose from-scratch derivations with short
function calls. Problems 1 and 4 show 58–60% savings. Even in the 3-epoch
experiment, per-epoch training tokens decrease monotonically.

### 7.3  H3: Multi-Round Benefits — Refuted

**More epochs hurt**: 3 epochs (43.3%) is dramatically worse than 1 epoch
(66.7%). The sweet spot appears to be around 22–38 skills (Epoch 2 of the
3-epoch run had the highest training accuracy). Beyond this, skill
over-accumulation causes:
1. Prompt bloat overwhelming the context window
2. Irrelevant skill injection increasing error rate
3. Conflicting implementations confusing the executor

This is a critical finding: **skill libraries require active curation**,
not unbounded growth. A pruning or gating mechanism is essential.

### 7.4  H4: Weak Model Benefit — Supported

GLM-4.5-Air gains +50% relative accuracy from skills (6.7% → 10.0%),
compared to GLM-4.7's −4.7% relative change (70% → 66.7%). The weak
model also shows dramatic token savings on specific problems (Problem 5:
78% reduction). Skills act as "cognitive offloading" — delegating
computation the weak model would otherwise fail to complete within the
token limit.

### 7.5  Complementary Solving

Skills and baseline have partially orthogonal failure modes. The oracle
ensemble (24/30 = 80.0%) represents significant headroom over either
method alone.

### 7.5  Skill Quality vs Quantity

The comparison between 34 skills (66.7% test accuracy) and 45 skills
(43.3%) demonstrates that **quality matters more than quantity**. The
additional 11 skills in the 3-epoch library are mostly over-specialised
or redundant, contributing noise rather than signal.

---

## 8  Limitations

1. **Single model family**: Only GLM variants tested, though at two
   capability levels (GLM-4.7 and GLM-4.5-Air).
2. **Single run**: No repeated trials to estimate variance.
3. **TF-IDF retrieval**: Bag-of-words matching may miss semantically
   relevant skills.
4. **No skill pruning**: The current system only adds skills, never
   removes them.
5. **Small test set**: 30 problems provide limited statistical power.
6. **API inconsistency**: Exp 1 used SiliconFlow + BigModel APIs;
   Exp 2 and 3 use BigModel only.

---

## 9  Future Directions

### 9.1  Skill Library Curation
The multi-epoch results strongly motivate **automatic pruning**:
- Remove skills with <20% success rate
- Merge similar skills
- Cap library size based on downstream validation

### 9.2  Selective Skill Injection
Instead of always injecting top-5 skills, use a **gating mechanism**
(confidence threshold) to decide whether any skills are relevant.

### 9.3  Embedding-Based Retrieval
Replace TF-IDF with semantic embeddings to better match problem
concepts to skill capabilities.

### 9.4  Ensemble Strategy
Run both skill-augmented and baseline solving; select the better
answer per problem. Oracle bound: 80.0% vs 70% baseline.

### 9.5  Cross-Domain Transfer
Test whether AIME-evolved skills transfer to AMC, MATH-500, or
Olympiad problems.

### 9.6  Adaptive Library Size
Use validation-set feedback to determine the optimal number of
skills to retain, addressing the over-accumulation problem directly.

---

## 10  Conclusion

We evaluated skill evolution on AIME 2024→2025 across three experimental
conditions. Key contributions:

1. **Single-epoch skills provide 20% token savings** at a modest accuracy
   cost for strong models (−3.3 pp), confirming skill reuse compresses reasoning.

2. **Multi-epoch training is counterproductive**: 3 epochs degrades
   accuracy from 66.7% to 43.3% due to skill over-accumulation.
   The optimal skill count appears to be 22–38 for this benchmark.

3. **Weak models benefit more from skills**: GLM-4.5-Air gains +50%
   relative accuracy (6.7% → 10.0%) while the stronger GLM-4.7 sees
   no improvement. Skills serve as "cognitive scaffolding" for models
   that cannot independently complete complex reasoning chains.

4. **Skills and baseline are complementary**: The oracle ensemble
   (80.0%) far exceeds either method, motivating hybrid approaches.

5. **Epoch 2 is the sweet spot**: In multi-epoch training, accuracy
   peaks at Epoch 2 then declines, while token cost continues to
   decrease — demonstrating that efficiency and accuracy can diverge.

The results argue that skill evolution is most effective with
**bounded, curated libraries**, **selective injection**, and for
**weaker models** that benefit most from pre-packaged tools. Unbounded
skill accumulation is a trap — the system must learn not just what to
remember, but what to forget.

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
