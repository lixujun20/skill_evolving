"""
example_skills.py — Five skill groups across different math domains.

Each SkillGroup is a cluster of 2–5 skills that SHOULD share a reusable
sub-function (ground-truth "mergeable" cluster) plus 0–1 negative-control
skills that superficially resemble the group but must NOT be merged.

Groups span five distinct domains to stress-test a refactoring algorithm:
  G1. 2-D Geometry          → signed cross product
  G2. Number Theory         → Euclidean GCD (extended / plain / lcm / nCr reduction)
  G3. Combinatorics         → modular exponentiation by squaring
  G4. Linear Algebra (2×2)  → 2×2 determinant
  G5. Probability / Stats   → sum-and-mean / variance reduction  (with a negative-control
                              skill that uses a median, NOT the reduction pattern)

For every skill we store natural-language test_queries plus a Python "harness"
that turns each query into an actual function call (used for execution-based
ground-truth validation).

The corpus is exposed as a flat list `ALL_SKILLS` for consumers that don't care
about grouping, and as `ALL_GROUPS` for algorithms that want to leverage the
structure.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class SkillSpec:
    name: str
    description: str
    code: str
    # (question_text, expected_answer) pairs
    test_queries: List[Tuple[str, Any]]
    # one harness callable per test query; each takes the compiled function and
    # returns the value to compare against expected_answer.
    harnesses: List[Callable[[Callable], Any]]
    # If True, this skill should NOT be merged with the group's shared pattern.
    # Used as ground-truth for negative-control validation.
    negative_control: bool = False

    def __post_init__(self) -> None:
        if len(self.test_queries) != len(self.harnesses):
            raise ValueError(
                f"skill {self.name}: test_queries and harnesses must have equal length "
                f"({len(self.test_queries)} vs {len(self.harnesses)})"
            )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "code": self.code,
            "test_queries": self.test_queries,
        }


@dataclasses.dataclass
class SkillGroup:
    name: str
    domain: str
    shared_sub_task: str          # one-sentence description of the expected shared operation
    skills: List[SkillSpec]

    @property
    def mergeable_skill_names(self) -> List[str]:
        return [s.name for s in self.skills if not s.negative_control]

    @property
    def negative_control_names(self) -> List[str]:
        return [s.name for s in self.skills if s.negative_control]


@dataclasses.dataclass
class SkillCorpus:
    name: str
    description: str
    source: str
    groups: List[SkillGroup]
    notes: List[str] = dataclasses.field(default_factory=list)

    @property
    def skills(self) -> List[SkillSpec]:
        return [s for g in self.groups for s in g.skills]


# ─────────────────────────────────────────────────────────────────────────────
# G1. 2-D Geometry — shared: signed 2-D cross product
# ─────────────────────────────────────────────────────────────────────────────

_G1_POLYGON_AREA = SkillSpec(
    name="polygon_area",
    description="Area of a simple polygon given ordered vertices (shoelace).",
    code="""\
def polygon_area(vertices):
    n = len(vertices)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x_i, y_i = vertices[i]
        x_j, y_j = vertices[(i + 1) % n]
        total += x_i * y_j
        total -= x_j * y_i          # cross product split over two lines
    return abs(total) / 2.0
""",
    test_queries=[
        ("Area of quadrilateral (0,0),(4,0),(4,3),(0,3)?", 12.0),
        ("Area of triangle (0,0),(6,0),(3,4)?", 12.0),
        ("Area of pentagon (0,0),(2,0),(3,2),(1,4),(-1,2)?", 10.0),
    ],
    harnesses=[
        lambda fn: fn([(0, 0), (4, 0), (4, 3), (0, 3)]),
        lambda fn: fn([(0, 0), (6, 0), (3, 4)]),
        lambda fn: fn([(0, 0), (2, 0), (3, 2), (1, 4), (-1, 2)]),
    ],
)

_G1_POINT_IN_CONVEX = SkillSpec(
    name="point_in_convex_polygon",
    description="Test whether a point lies in a CCW convex polygon.",
    code="""\
def point_in_convex_polygon(px, py, vertices):
    n = len(vertices)
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        edge_dx = bx - ax
        edge_dy = by - ay
        to_px = px - ax
        to_py = py - ay
        cross = edge_dx * to_py - edge_dy * to_px   # cross product, non-contiguous with setup
        if cross < -1e-9:
            return False
    return True
""",
    test_queries=[
        ("Is (2,2) inside square (0,0),(4,0),(4,4),(0,4)?", True),
        ("Is (5,2) inside square (0,0),(4,0),(4,4),(0,4)?", False),
        ("Is (1,1) inside triangle (0,0),(3,0),(0,3)?", True),
    ],
    harnesses=[
        lambda fn: fn(2, 2, [(0, 0), (4, 0), (4, 4), (0, 4)]),
        lambda fn: fn(5, 2, [(0, 0), (4, 0), (4, 4), (0, 4)]),
        lambda fn: fn(1, 1, [(0, 0), (3, 0), (0, 3)]),
    ],
)

_G1_SIGNED_TRI_AREA = SkillSpec(
    name="signed_triangle_area",
    description="Signed area of triangle by three points (positive=CCW).",
    code="""\
def signed_triangle_area(ax, ay, bx, by, cx, cy):
    return 0.5 * ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
""",
    test_queries=[
        ("Signed area of (0,0),(4,0),(0,3) (CCW)?", 6.0),
        ("Signed area of (0,0),(0,3),(4,0) (CW)?", -6.0),
    ],
    harnesses=[
        lambda fn: fn(0, 0, 4, 0, 0, 3),
        lambda fn: fn(0, 0, 0, 3, 4, 0),
    ],
)

_G1_POWER_OF_POINT = SkillSpec(  # negative control
    name="power_of_point",
    description="Power of a point wrt a circle (squared distance − r^2).",
    code="""\
def power_of_point(px, py, cx, cy, r):
    dx = px - cx
    dy = py - cy
    return dx * dx + dy * dy - r * r
""",
    test_queries=[
        ("Power of (5,0) wrt circle centre (0,0) radius 3?", 16.0),
        ("Power of (1,1) wrt circle centre (0,0) radius 2?", -2.0),
    ],
    harnesses=[
        lambda fn: fn(5, 0, 0, 0, 3),
        lambda fn: fn(1, 1, 0, 0, 2),
    ],
    negative_control=True,
)

GROUP_GEOMETRY = SkillGroup(
    name="geometry_cross_product",
    domain="2D geometry",
    shared_sub_task="signed 2-D cross product  (ax·by − ay·bx)",
    skills=[_G1_POLYGON_AREA, _G1_POINT_IN_CONVEX, _G1_SIGNED_TRI_AREA, _G1_POWER_OF_POINT],
)


# ─────────────────────────────────────────────────────────────────────────────
# G2. Number Theory — shared: Euclidean GCD
# ─────────────────────────────────────────────────────────────────────────────

_G2_GCD_PAIR = SkillSpec(
    name="gcd_pair",
    description="Greatest common divisor of two non-negative integers.",
    code="""\
def gcd_pair(a, b):
    while b:
        a, b = b, a % b         # Euclid step
    return a
""",
    test_queries=[
        ("gcd(12, 18)?", 6),
        ("gcd(17, 5)?", 1),
        ("gcd(0, 9)?", 9),
    ],
    harnesses=[
        lambda fn: fn(12, 18),
        lambda fn: fn(17, 5),
        lambda fn: fn(0, 9),
    ],
)

_G2_LCM_LIST = SkillSpec(
    name="lcm_of_list",
    description="Least common multiple of a list of positive integers.",
    code="""\
def lcm_of_list(nums):
    def _gcd_inner(x, y):
        # gcd inlined (Euclid) — different variable names
        while y:
            x, y = y, x % y
        return x
    result = 1
    for n in nums:
        g = _gcd_inner(result, n)
        result = result * n // g
    return result
""",
    test_queries=[
        ("lcm of [2, 3, 4]?", 12),
        ("lcm of [6, 8, 12]?", 24),
        ("lcm of [5, 7]?", 35),
    ],
    harnesses=[
        lambda fn: fn([2, 3, 4]),
        lambda fn: fn([6, 8, 12]),
        lambda fn: fn([5, 7]),
    ],
)

_G2_SIMPLIFY_FRAC = SkillSpec(
    name="simplify_fraction",
    description="Reduce fraction p/q to lowest terms; return (p', q').",
    code="""\
def simplify_fraction(p, q):
    # tail-recursive-looking GCD, then divide
    def _g(u, v):
        if v == 0:
            return u
        return _g(v, u % v)
    d = _g(abs(p), abs(q))
    return (p // d, q // d)
""",
    test_queries=[
        ("simplify 8/12?", (2, 3)),
        ("simplify 14/21?", (2, 3)),
        ("simplify 5/7?", (5, 7)),
    ],
    harnesses=[
        lambda fn: fn(8, 12),
        lambda fn: fn(14, 21),
        lambda fn: fn(5, 7),
    ],
)

_G2_COPRIME_COUNT = SkillSpec(
    name="count_coprime_pairs",
    description="Count pairs (a,b) in a list with gcd(a,b)=1.",
    code="""\
def count_coprime_pairs(nums):
    total = 0
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            # Euclid inlined inside a double loop — non-contiguous with body
            a, b = nums[i], nums[j]
            while b:
                a, b = b, a % b
            if a == 1:
                total += 1
    return total
""",
    test_queries=[
        ("Coprime pair count in [2,3,4,5]?", 5),
        ("Coprime pair count in [6,10,15]?", 0),
    ],
    harnesses=[
        lambda fn: fn([2, 3, 4, 5]),
        lambda fn: fn([6, 10, 15]),
    ],
)

GROUP_NUMBER_THEORY = SkillGroup(
    name="number_theory_gcd",
    domain="number theory",
    shared_sub_task="Euclidean algorithm for gcd(a, b)",
    skills=[_G2_GCD_PAIR, _G2_LCM_LIST, _G2_SIMPLIFY_FRAC, _G2_COPRIME_COUNT],
)


# ─────────────────────────────────────────────────────────────────────────────
# G3. Combinatorics / Modular arithmetic — shared: fast modular exponentiation
# ─────────────────────────────────────────────────────────────────────────────

_G3_POWMOD = SkillSpec(
    name="pow_mod",
    description="Compute a^b mod m by binary exponentiation.",
    code="""\
def pow_mod(a, b, m):
    result = 1
    a %= m
    while b > 0:
        if b & 1:
            result = (result * a) % m
        a = (a * a) % m
        b >>= 1
    return result
""",
    test_queries=[
        ("2^10 mod 1000?", 24),
        ("3^200 mod 7?", 2),
        ("5^0 mod 13?", 1),
    ],
    harnesses=[
        lambda fn: fn(2, 10, 1000),
        lambda fn: fn(3, 200, 7),
        lambda fn: fn(5, 0, 13),
    ],
)

_G3_MODINV = SkillSpec(
    name="modular_inverse_prime",
    description="Modular inverse of a mod p (p prime) via Fermat's little theorem.",
    code="""\
def modular_inverse_prime(a, p):
    # inverse = a^(p-2) mod p — uses binary exponentiation inlined
    exp = p - 2
    base = a % p
    out = 1
    while exp > 0:
        if exp % 2 == 1:
            out = (out * base) % p
        base = (base * base) % p
        exp //= 2
    return out
""",
    test_queries=[
        ("Inverse of 3 mod 7?", 5),
        ("Inverse of 10 mod 17?", 12),
    ],
    harnesses=[
        lambda fn: fn(3, 7),
        lambda fn: fn(10, 17),
    ],
)

_G3_BINOM_MOD = SkillSpec(
    name="binomial_mod_prime",
    description="Binomial coefficient C(n,k) mod p (p prime, n<p).",
    code="""\
def binomial_mod_prime(n, k, p):
    if k < 0 or k > n:
        return 0
    # compute factorials mod p directly
    num = 1
    for i in range(n - k + 1, n + 1):
        num = (num * i) % p
    den = 1
    for i in range(1, k + 1):
        den = (den * i) % p
    # modular inverse via fast exponentiation — non-contiguous in this skill
    exp = p - 2
    base = den
    inv = 1
    while exp:
        if exp & 1:
            inv = (inv * base) % p
        base = (base * base) % p
        exp >>= 1
    return (num * inv) % p
""",
    test_queries=[
        ("C(5,2) mod 13?", 10),
        ("C(10,3) mod 97?", 23),
    ],
    harnesses=[
        lambda fn: fn(5, 2, 13),
        lambda fn: fn(10, 3, 97),
    ],
)

GROUP_COMBINATORICS = SkillGroup(
    name="combinatorics_pow_mod",
    domain="combinatorics / modular arithmetic",
    shared_sub_task="binary modular exponentiation: compute a^b mod m",
    skills=[_G3_POWMOD, _G3_MODINV, _G3_BINOM_MOD],
)


# ─────────────────────────────────────────────────────────────────────────────
# G4. Linear Algebra (2x2) — shared: 2×2 determinant
# ─────────────────────────────────────────────────────────────────────────────

_G4_DET2 = SkillSpec(
    name="det_2x2",
    description="Determinant of a 2×2 matrix.",
    code="""\
def det_2x2(M):
    # M is [[a,b],[c,d]]
    a, b = M[0]
    c, d = M[1]
    return a * d - b * c
""",
    test_queries=[
        ("det([[1,2],[3,4]])?", -2),
        ("det([[2,0],[0,5]])?", 10),
    ],
    harnesses=[
        lambda fn: fn([[1, 2], [3, 4]]),
        lambda fn: fn([[2, 0], [0, 5]]),
    ],
)

_G4_INV2 = SkillSpec(
    name="inverse_2x2",
    description="Inverse of a 2×2 matrix as 4-tuple (a',b',c',d'); None if singular.",
    code="""\
def inverse_2x2(M):
    a, b = M[0]
    c, d = M[1]
    det = a * d - b * c             # 2x2 determinant, inlined
    if det == 0:
        return None
    inv_det = 1.0 / det
    return (d * inv_det, -b * inv_det, -c * inv_det, a * inv_det)
""",
    test_queries=[
        ("Inverse of [[1,2],[3,4]]?", (-2.0, 1.0, 1.5, -0.5)),
        ("Inverse of [[1,0],[0,1]]?", (1.0, 0.0, 0.0, 1.0)),
    ],
    harnesses=[
        lambda fn: fn([[1, 2], [3, 4]]),
        lambda fn: fn([[1, 0], [0, 1]]),
    ],
)

_G4_CRAMER = SkillSpec(
    name="solve_linear_2x2",
    description="Solve Ax=b for 2×2 A by Cramer's rule; None if singular.",
    code="""\
def solve_linear_2x2(A, b):
    a11, a12 = A[0]
    a21, a22 = A[1]
    b1, b2 = b
    # main determinant — same 2x2 det pattern
    D = a11 * a22 - a12 * a21
    if D == 0:
        return None
    # x-determinant: replace column 1 with b
    Dx = b1 * a22 - a12 * b2
    # y-determinant: replace column 2 with b
    Dy = a11 * b2 - b1 * a21
    return (Dx / D, Dy / D)
""",
    test_queries=[
        ("Solve [[2,1],[1,3]]x=[5,10]?", (1.0, 3.0)),
        ("Solve [[1,0],[0,1]]x=[7,-2]?", (7.0, -2.0)),
    ],
    harnesses=[
        lambda fn: fn([[2, 1], [1, 3]], [5, 10]),
        lambda fn: fn([[1, 0], [0, 1]], [7, -2]),
    ],
)

GROUP_LINALG = SkillGroup(
    name="linalg_2x2_det",
    domain="linear algebra (2×2 matrices)",
    shared_sub_task="2×2 determinant: a·d − b·c",
    skills=[_G4_DET2, _G4_INV2, _G4_CRAMER],
)


# ─────────────────────────────────────────────────────────────────────────────
# G5. Probability / Statistics — shared: online sum-and-mean reduction
#     (Negative control: median — needs sorting, NOT the sum reduction.)
# ─────────────────────────────────────────────────────────────────────────────

_G5_MEAN = SkillSpec(
    name="sample_mean",
    description="Arithmetic mean of a list of numbers.",
    code="""\
def sample_mean(xs):
    total = 0.0
    count = 0
    for x in xs:
        total += x          # running-sum reduction
        count += 1
    return total / count if count else 0.0
""",
    test_queries=[
        ("mean of [1,2,3,4,5]?", 3.0),
        ("mean of [10,10,10]?", 10.0),
    ],
    harnesses=[
        lambda fn: fn([1, 2, 3, 4, 5]),
        lambda fn: fn([10, 10, 10]),
    ],
)

_G5_VAR = SkillSpec(
    name="sample_variance",
    description="Population variance of a list of numbers.",
    code="""\
def sample_variance(xs):
    # pass 1: mean (sum reduction) — same reduction pattern
    s = 0.0
    for x in xs:
        s += x
    n = len(xs)
    mean = s / n if n else 0.0
    # pass 2: squared-deviation reduction — structurally same, different accumulator
    acc = 0.0
    for x in xs:
        diff = x - mean
        acc += diff * diff
    return acc / n if n else 0.0
""",
    test_queries=[
        ("variance of [1,2,3,4,5]?", 2.0),
        ("variance of [2,2,2]?", 0.0),
    ],
    harnesses=[
        lambda fn: fn([1, 2, 3, 4, 5]),
        lambda fn: fn([2, 2, 2]),
    ],
)

_G5_EXPECTED_VALUE = SkillSpec(
    name="expected_value",
    description="Expected value of a discrete distribution given (value, prob) pairs.",
    code="""\
def expected_value(pairs):
    ev = 0.0
    # reduction over (value * weight); same sum-accumulator skeleton
    for v, p in pairs:
        ev += v * p
    return ev
""",
    test_queries=[
        ("EV of [(1,0.5),(2,0.5)]?", 1.5),
        ("EV of [(-1,0.25),(0,0.5),(1,0.25)]?", 0.0),
    ],
    harnesses=[
        lambda fn: fn([(1, 0.5), (2, 0.5)]),
        lambda fn: fn([(-1, 0.25), (0, 0.5), (1, 0.25)]),
    ],
)

_G5_MEDIAN = SkillSpec(  # negative control
    name="sample_median",
    description="Median of a list of numbers.",
    code="""\
def sample_median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2.0
""",
    test_queries=[
        ("median of [1,2,3,4,5]?", 3.0),
        ("median of [1,2,3,4]?", 2.5),
    ],
    harnesses=[
        lambda fn: fn([1, 2, 3, 4, 5]),
        lambda fn: fn([1, 2, 3, 4]),
    ],
    negative_control=True,
)

GROUP_STATS = SkillGroup(
    name="statistics_sum_reduction",
    domain="probability / statistics",
    shared_sub_task="sum-accumulator reduction over a list (total += x for x in xs)",
    skills=[_G5_MEAN, _G5_VAR, _G5_EXPECTED_VALUE, _G5_MEDIAN],
)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

ALL_GROUPS: List[SkillGroup] = [
    GROUP_GEOMETRY,
    GROUP_NUMBER_THEORY,
    GROUP_COMBINATORICS,
    GROUP_LINALG,
    GROUP_STATS,
]

ALL_SKILLS: List[SkillSpec] = [s for g in ALL_GROUPS for s in g.skills]


BUILTIN_CORPUS = SkillCorpus(
    name="builtin_math",
    description="Original math refactoring corpus (5 groups / 18 skills).",
    source="builtin",
    groups=ALL_GROUPS,
    notes=["Ground-truth corpus used in the original v1/v2/v3 report."],
)


def ground_truth_clusters(groups: Optional[List[SkillGroup]] = None) -> Dict[str, List[str]]:
    """Return mapping {group.name → list of mergeable skill names}."""
    target = groups or ALL_GROUPS
    return {g.name: g.mergeable_skill_names for g in target}


def negative_controls(groups: Optional[List[SkillGroup]] = None) -> List[str]:
    target = groups or ALL_GROUPS
    return [s.name for g in target for s in g.skills if s.negative_control]


_SB_SIGNAL_DETECT_LOCAL_PEAKS = SkillSpec(
    name="detect_local_peaks",
    description="Return indices of local peaks above a threshold.",
    code="""\
def detect_local_peaks(xs, threshold):
    peaks = []
    for i in range(1, len(xs) - 1):
        left = xs[i - 1]
        center = xs[i]
        right = xs[i + 1]
        if center > left and center >= right and center >= threshold:
            peaks.append(i)
    return peaks
""",
    test_queries=[
        ("Peak indices in [0,1,3,1,0,2,2,1] with threshold 2?", [2, 5]),
        ("Peak indices in [0,2,1,2,0] with threshold 2?", [1, 3]),
    ],
    harnesses=[
        lambda fn: fn([0, 1, 3, 1, 0, 2, 2, 1], 2),
        lambda fn: fn([0, 2, 1, 2, 0], 2),
    ],
)

_SB_SIGNAL_FIRST_PEAK_AFTER = SkillSpec(
    name="first_peak_after",
    description="Find the first local peak after a given start index.",
    code="""\
def first_peak_after(xs, threshold, start_idx):
    for i in range(max(1, start_idx + 1), len(xs) - 1):
        prev_v = xs[i - 1]
        cur_v = xs[i]
        next_v = xs[i + 1]
        if cur_v > prev_v and cur_v >= next_v and cur_v >= threshold:
            return i
    return -1
""",
    test_queries=[
        ("First peak after index 0 in [0,1,4,1,3,1] threshold 3?", 2),
        ("First peak after index 2 in [0,1,4,1,3,1] threshold 3?", 4),
    ],
    harnesses=[
        lambda fn: fn([0, 1, 4, 1, 3, 1], 3, 0),
        lambda fn: fn([0, 1, 4, 1, 3, 1], 3, 2),
    ],
)

_SB_SIGNAL_PAIR_NEARBY_PEAKS = SkillSpec(
    name="pair_nearby_peaks",
    description="Pair peaks from two signals if their indices are within a max gap.",
    code="""\
def pair_nearby_peaks(xs, ys, threshold, max_gap):
    x_peaks = []
    for i in range(1, len(xs) - 1):
        if xs[i] > xs[i - 1] and xs[i] >= xs[i + 1] and xs[i] >= threshold:
            x_peaks.append(i)
    y_peaks = []
    for j in range(1, len(ys) - 1):
        if ys[j] > ys[j - 1] and ys[j] >= ys[j + 1] and ys[j] >= threshold:
            y_peaks.append(j)
    pairs = []
    for xp in x_peaks:
        for yp in y_peaks:
            if abs(xp - yp) <= max_gap:
                pairs.append((xp, yp))
                break
    return pairs
""",
    test_queries=[
        (
            "Nearby peaks between [0,3,1,0,4,1] and [0,1,3,1,0,4] with threshold 3 gap 1?",
            [(1, 2)],
        ),
        (
            "Nearby peaks between [0,4,1,0] and [0,1,4,0] with threshold 4 gap 2?",
            [(1, 2)],
        ),
    ],
    harnesses=[
        lambda fn: fn([0, 3, 1, 0, 4, 1], [0, 1, 3, 1, 0, 4], 3, 1),
        lambda fn: fn([0, 4, 1, 0], [0, 1, 4, 0], 4, 2),
    ],
)

_SB_SIGNAL_ARGMAX = SkillSpec(
    name="global_argmax_index",
    description="Return the index of the global maximum sample.",
    code="""\
def global_argmax_index(xs):
    if not xs:
        return -1
    best_i = 0
    best_v = xs[0]
    for i, value in enumerate(xs[1:], start=1):
        if value > best_v:
            best_i = i
            best_v = value
    return best_i
""",
    test_queries=[
        ("Argmax index of [1,5,2,4]?", 1),
        ("Argmax index of [0,1,9,3]?", 2),
    ],
    harnesses=[
        lambda fn: fn([1, 5, 2, 4]),
        lambda fn: fn([0, 1, 9, 3]),
    ],
    negative_control=True,
)

GROUP_SKILLSBENCH_SIGNAL = SkillGroup(
    name="skillsbench_signal_peak_detection",
    domain="signals / seismology",
    shared_sub_task="detect a local peak above threshold from neighboring samples",
    skills=[
        _SB_SIGNAL_DETECT_LOCAL_PEAKS,
        _SB_SIGNAL_FIRST_PEAK_AFTER,
        _SB_SIGNAL_PAIR_NEARBY_PEAKS,
        _SB_SIGNAL_ARGMAX,
    ],
)

_SB_CITE_NORMALIZE = SkillSpec(
    name="normalize_reference_title",
    description="Normalize a reference title to a canonical token string.",
    code="""\
def normalize_reference_title(text):
    chars = []
    for ch in text.lower():
        chars.append(ch if ch.isalnum() else " ")
    return " ".join("".join(chars).split())
""",
    test_queries=[
        ("Normalize 'Graph-based, Search! Systems'?", "graph based search systems"),
        ("Normalize 'Citation_Check 101'?", "citation check 101"),
    ],
    harnesses=[
        lambda fn: fn("Graph-based, Search! Systems"),
        lambda fn: fn("Citation_Check 101"),
    ],
)

_SB_CITE_DEDUPE = SkillSpec(
    name="dedupe_reference_titles",
    description="Deduplicate titles after canonical normalization, preserving first occurrence.",
    code="""\
def dedupe_reference_titles(titles):
    seen = set()
    out = []
    for title in titles:
        cleaned = []
        for ch in title.lower():
            cleaned.append(ch if ch.isalnum() else " ")
        key = " ".join("".join(cleaned).split())
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out
""",
    test_queries=[
        ("Dedupe ['A Study', 'a-study', 'Other Paper']?", ["a study", "other paper"]),
        ("Dedupe ['X.Y', 'x y', 'Z']?", ["x y", "z"]),
    ],
    harnesses=[
        lambda fn: fn(["A Study", "a-study", "Other Paper"]),
        lambda fn: fn(["X.Y", "x y", "Z"]),
    ],
)

_SB_CITE_EQUIV = SkillSpec(
    name="titles_equivalent",
    description="Check whether two titles are equivalent after canonical normalization.",
    code="""\
def titles_equivalent(left, right):
    def _canon(text):
        cleaned = []
        for ch in text.lower():
            cleaned.append(ch if ch.isalnum() else " ")
        return " ".join("".join(cleaned).split())
    return _canon(left) == _canon(right)
""",
    test_queries=[
        ("Are 'Neural Search' and 'neural-search' equivalent?", True),
        ("Are 'Graph Mining' and 'Graph Matching' equivalent?", False),
    ],
    harnesses=[
        lambda fn: fn("Neural Search", "neural-search"),
        lambda fn: fn("Graph Mining", "Graph Matching"),
    ],
)

_SB_CITE_YEAR = SkillSpec(
    name="extract_year_tokens",
    description="Extract 4-digit year-like tokens from text.",
    code="""\
def extract_year_tokens(text):
    current = ""
    out = []
    for ch in text:
        if ch.isdigit():
            current += ch
        else:
            if len(current) == 4:
                out.append(int(current))
            current = ""
    if len(current) == 4:
        out.append(int(current))
    return out
""",
    test_queries=[
        ("Year tokens in 'Published 2024, revised 2025.'?", [2024, 2025]),
        ("Year tokens in 'No year here 123.'?", []),
    ],
    harnesses=[
        lambda fn: fn("Published 2024, revised 2025."),
        lambda fn: fn("No year here 123."),
    ],
    negative_control=True,
)

GROUP_SKILLSBENCH_CITATION = SkillGroup(
    name="skillsbench_citation_normalization",
    domain="citation / information extraction",
    shared_sub_task="normalize free-form text by lowercasing and keeping alphanumeric tokens",
    skills=[
        _SB_CITE_NORMALIZE,
        _SB_CITE_DEDUPE,
        _SB_CITE_EQUIV,
        _SB_CITE_YEAR,
    ],
)

SKILLSBENCH_GROUPS: List[SkillGroup] = [
    GROUP_SKILLSBENCH_SIGNAL,
    GROUP_SKILLSBENCH_CITATION,
]

SKILLSBENCH_CORPUS = SkillCorpus(
    name="skillsbench_manual",
    description="Hand-crafted skill groups inspired by local SkillsBench tasks.",
    source="skillsbench_manual",
    groups=SKILLSBENCH_GROUPS,
    notes=[
        "Derived from SkillsBench themes such as seismic-phase-picking, earthquake-phase-association, and citation-check.",
        "Many SkillsBench tasks are isolated workflows; only themes that can be turned into reusable skill groups are retained.",
    ],
)


def list_corpora() -> List[SkillCorpus]:
    return [BUILTIN_CORPUS, SKILLSBENCH_CORPUS]


def get_corpus(name: str) -> SkillCorpus:
    corpora = {c.name: c for c in list_corpora()}
    if name not in corpora:
        raise KeyError(f"Unknown corpus: {name}. Available: {sorted(corpora)}")
    return corpora[name]
