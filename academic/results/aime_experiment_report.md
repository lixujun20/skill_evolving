# Skill Evolution for Mathematical Reasoning: An Empirical Study on AIME

## Abstract

We evaluate a **skill evolution** framework on competition-level mathematics
(AIME 2024 → 2025). The system extracts reusable code "skills" from
problem-solving traces and accumulates them in a shared library. On a
held-out test set, we compare **skill-augmented solving** against a
**baseline** that uses the same LLM without accumulated skills.

**Key findings** (GLM-4.7, 30 train → 30 test):

| Metric | Skill-Augmented | Baseline | Δ |
|--------|:-:|:-:|:-:|
| Accuracy (30 problems) | 66.7% (20/30) | 70.0% (21/30) | −3.3 pp |
| Token cost (17 shared solved) | 128,732 | 160,836 | **−20.0%** |
| Unique solves | 3 | 4 | — |
| Timeouts | 10 | 7 | +3 |

The framework **does not improve accuracy** on this benchmark but achieves
a **20% token reduction** on commonly-solved problems, demonstrating that
skill reuse compresses the reasoning path.

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

### 2.2  Model

- **LLM**: GLM-4.7 (ZhipuAI)
  - Evolve and test-with-skills phases: via SiliconFlow API (`Pro/zai-org/GLM-4.7`)
  - Baseline completion (problems 19–30): via BigModel API (`glm-4.7`, same underlying model)
  - Temperature: default (API default)

### 2.3  System Configuration

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MAX_AGENT_STEPS` | 8 | Max LLM interaction rounds per problem |
| `CODE_EXEC_TIMEOUT` | 30 s | Per code-block execution timeout |
| `LLM_TIMEOUT` | 300 s | Per LLM request timeout |
| `top_k` | 5 | Skills retrieved per query (TF-IDF cosine) |
| Extraction model | GLM-4.7 | Same model extracts skills |
| Tester | 3-stage | Syntax → Load → Assertion |

### 2.4  Pipeline

```
Phase 1 — Evolve (AIME 2024, 30 problems):
   For each problem:
     1. Retrieve top-5 relevant skills from store
     2. Executor solves with skills pre-loaded
     3. Extractor analyses trace → candidate skills
     4. Tester validates each candidate (syntax, load, assertions)
     5. Verified skills added to store
   Skills accumulate across problems.

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

## 3  Results

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
| With skills | 20/30 | **66.7%** | 10 | 0 |
| Baseline | 21/30 | **70.0%** | 7 | 2 |

The baseline achieves marginally higher accuracy (+3.3 percentage points).
Notably, the skill-augmented method has **more timeouts** (10 vs 7) but
**zero wrong answers** among solved problems — every problem that
produced an answer was correct.

The baseline has 2 problems where it produced an answer but got it wrong
(problems 21, 22), while the skill method timed out on those instead.

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

The large-savings cases correspond to problems where relevant skills were
retrieved and reused effectively. The increased-cost cases suggest that
irrelevant skills in the prompt may occasionally distract the model or
inflate prompt length without benefit.

### 3.3  Skill Usage Analysis

| Metric | Value |
|--------|:-----:|
| Total skills evolved | 34 |
| Skills with ≥1 usage | 26 (76.5%) |
| Skills with ≥1 success | 23 (67.6%) |
| Avg usage count | 4.4 |
| Avg success rate | 71.3% |
| Max usage | 17 (`hyperbola_rhombus_diagonal_infimum`) |
| Skills with dependencies | 8 (23.5%) |

Most skills (76.5%) were used at least once during test-time retrieval.
The most-used skill, `hyperbola_rhombus_diagonal_infimum`, was retrieved
17 times but only succeeded 15 times, suggesting that TF-IDF retrieval
occasionally matches skills to unrelated problems.

### 3.4  Timeout Analysis

| Phase | Timeouts | Rate |
|-------|:--------:|:----:|
| Evolve | 5/30 | 16.7% |
| Test (skills) | 10/30 | **33.3%** |
| Test (baseline) | 7/30 | 23.3% |

The skill-augmented method has a **higher timeout rate** than baseline
(33.3% vs 23.3%). Two possible explanations:

1. **Prompt bloat**: Injecting 5 skill definitions increases prompt
   length, consuming more of the 300s LLM timeout budget.
2. **Distraction**: Irrelevant skills may lead the model down wrong
   solution paths, causing more retries within the 8-step limit.

Five problems (8, 11, 13, 16, 18) timed out under both conditions,
suggesting these are genuinely beyond GLM-4.7's capability for AIME 2025.

---

## 4  Cost Analysis

### 4.1  Total Token Budget

| Phase | Total Tokens | Purpose |
|-------|:-----------:|---------|
| Evolve (train) | 235,671 | Solving + extraction + testing |
| Test (skills) | 169,649 | Solving with skills |
| Test (baseline) | 244,378 | Solving without skills |

**Overhead of skill evolution**: The evolve phase costs 235,671 tokens
for training. At test time, the skill method uses 169,649 tokens vs
baseline's 244,378 — a saving of 74,729 tokens (30.6%).

However, including the training cost:
- **Total with skills**: 235,671 + 169,649 = **405,320 tokens**
- **Total baseline**: 244,378 tokens

The skill evolution system costs **65.8% more** in total when including
training. This overhead is amortised only if the skill library is reused
across many test sets.

### 4.2  Break-Even Analysis

At the observed test-time savings rate of 74,729 tokens per 30 problems
(2,491 tokens/problem), the training cost of 235,671 tokens would be
amortised after solving approximately **95 additional problems** — about
3.2 additional test sets of 30 problems each.

---

## 5  Discussion

### 5.1  Why Didn't Accuracy Improve?

**H1 (accuracy improvement) is not supported** on this benchmark.
Several factors contribute:

1. **Strong baseline**: GLM-4.7 with code execution already solves
   70% of AIME 2025 problems. The "low-hanging fruit" that skills would
   help with is already captured.

2. **Domain mismatch**: AIME 2024 and 2025 problems share the same
   competition format but may test different mathematical concepts.
   Skills extracted from 2024 geometry problems may not transfer to
   2025 number theory problems.

3. **TF-IDF retrieval limitations**: Bag-of-words retrieval based on
   surface-level text similarity may fail to match semantically relevant
   skills. A problem about "counting lattice points" might benefit from
   a skill about "integer partitions," but TF-IDF may not connect them.

4. **Timeout increase**: The 10% higher timeout rate with skills
   (33.3% vs 23.3%) directly reduces accuracy. If the 3 extra timeouts
   had been solved, skill accuracy would match baseline.

### 5.2  Token Efficiency Is Real

**H2 (token reduction) is supported**. On commonly-solved problems,
skills reduce tokens by 20%. The mechanism is clear: when a relevant
skill is available (e.g., `complex_pow_int` for a complex number problem),
the model writes a short function call instead of re-deriving the
algorithm. Problems 1 and 4 show 58–60% savings, confirming this.

### 5.3  Complementary Solving

The 3 problems solved only with skills and 4 solved only without suggest
the methods have **partially orthogonal failure modes**. This points
toward a potential **ensemble approach**:

- Run both methods; take the first valid answer.
- Oracle accuracy: 24/30 = 80.0% (vs 70% baseline, 66.7% skills).

### 5.4  Skill Quality and Granularity

The 34 extracted skills vary in quality:
- **High utility**: `complex_pow_int` (5 uses, 100% success),
  `get_set_bit_indices` (7 uses, 86% success)
- **Moderate utility**: `hyperbola_rhombus_diagonal_infimum` (17 uses,
  88% success) — retrieved often but highly specialised
- **Low utility**: 8 skills (23.5%) with 0 usage — never retrieved

The high proportion of unused skills suggests the extractor is
**over-extracting** specialised skills that don't generalise. A more
conservative extraction strategy or a usage-based pruning mechanism
could improve skill library quality.

---

## 6  Limitations

1. **Single model**: Only GLM-4.7 was tested. Results may differ with
   stronger models (e.g., GPT-4, Claude) or weaker ones.

2. **Single run**: No repeated trials to estimate variance. The 3.3pp
   accuracy difference may be within noise.

3. **API inconsistency**: Baseline problems 1–18 used SiliconFlow API;
   19–30 used BigModel API. Both serve GLM-4.7 but may differ in
   latency and timeout behaviour.

4. **No multi-round evolution**: Each training problem was solved once.
   Multiple passes over the training set might improve skill quality
   through version updates.

5. **TF-IDF retrieval**: A more sophisticated retrieval method (e.g.,
   embedding-based) might improve skill matching.

6. **Small test set**: 30 problems provide limited statistical power.

---

## 7  Future Directions

### 7.1  Improved Retrieval
Replace TF-IDF with **embedding-based retrieval** (e.g., using the same
LLM or a dedicated embedding model) to better match problem semantics
to skill capabilities.

### 7.2  Selective Skill Injection
Instead of always injecting top-5 skills, use a **gating mechanism** to
decide whether any skills are relevant. If confidence is low, fall back
to no-skill mode. This could reduce the timeout increase observed.

### 7.3  Multi-Round Evolution
Run the training set through **multiple passes**, allowing the system to:
- Update existing skills based on new usage patterns
- Prune unused skills
- Build higher-level composite skills

### 7.4  Cross-Domain Transfer
Test whether skills evolved on one dataset (e.g., AIME) transfer to
other mathematical benchmarks (e.g., MATH-500, AMC, Olympiad problems).

### 7.5  Adaptive Timeout
Dynamically adjust the LLM timeout based on problem complexity or
prompt length to account for the increased latency from skill injection.

### 7.6  Ensemble Strategy
Implement the oracle ensemble approach: run both skill-augmented and
baseline solving, selecting the better answer per problem. The
theoretical upper bound (80.0%) suggests significant gains.

### 7.7  Stronger Base Models
Test with more capable models (e.g., GPT-4o, Claude Sonnet 4) where
the baseline accuracy ceiling is higher and skill reuse patterns may
differ.

---

## 8  Conclusion

We evaluated a skill evolution framework on AIME 2024→2025, comparing
skill-augmented solving against a no-skill baseline using GLM-4.7.

The framework successfully:
- Extracted **34 verified skills** with meaningful dependency structures
- Achieved **20% token reduction** on commonly-solved problems
- Solved **3 problems** that the baseline could not

However, it did not improve overall accuracy (66.7% vs 70.0% baseline),
primarily due to increased timeouts from prompt bloat and limited
relevance of retrieved skills.

The results suggest skill evolution is more effective as a **cost
optimisation** strategy than an accuracy improvement strategy on
competition-level mathematics, at least with current retrieval
mechanisms. The complementary solving patterns between the two methods
point toward ensemble approaches as a promising direction.

---

## Appendix A: Evolved Skills Catalogue

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
- `academic/results/aime_full_run1.log` — Full experiment log
- `academic/results/aime_experiment_full_run1_skills.json` — Evolved skills
- `academic/results/baseline_complete.json` — Complete baseline results
- `academic/results/baseline_completion.log` — Baseline completion log
