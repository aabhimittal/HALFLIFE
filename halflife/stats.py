"""Survival statistics for consolidation-cycle data.

Three complementary half-life estimates, because each one fails differently:

* ``prevalence_half_life`` - model-free: the first cycle at which the fraction
  of trials still showing the injection drops to 0.5 (linear interpolation
  between cycles). Right-censored if it never drops within the horizon.
* ``km_half_life`` - Kaplan-Meier on first-passage time ("first cycle the
  detector stops firing"). Correct for absorbing deaths (a literal string
  that is gone stays gone); pessimistic for flickering signals like behavior.
* ``fit_decay`` - S(n) = c + (S0 - c) * exp(-lambda * n). The floor ``c`` is the
  asymptotic surviving fraction; c >= 0.5 means the half-life is infinite,
  which is the signature of a self-reinforcing ("zombie") injection.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, asdict
from typing import Sequence

Matrix = Sequence[Sequence[bool]]  # trials x cycles


@dataclass(frozen=True)
class HalfLife:
    value: float | None  # cycles; None when censored or never established
    status: str          # "observed" | "censored" | "never_established" | "empty"
    horizon: int

    def __str__(self) -> str:
        if self.status == "observed":
            return f"{self.value:.1f}"
        if self.status == "censored":
            return f">{self.horizon}"
        if self.status == "never_established":
            return "n/e"
        return "-"

    def sort_key(self) -> float:
        if self.status == "observed":
            return float(self.value)
        if self.status == "censored":
            return math.inf
        return -1.0


def prevalence(m: Matrix) -> list[float]:
    if not m:
        return []
    n = len(m[0])
    if any(len(row) != n for row in m):
        raise ValueError("all trials must have the same number of cycles")
    return [sum(row[c] for row in m) / len(m) for c in range(n)]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, mid - half), min(1.0, mid + half))


def prevalence_half_life(curve: Sequence[float], threshold: float = 0.5) -> HalfLife:
    """Cycles until survival drops to ``threshold``, linearly interpolated.

    The injection must first be *established* (survival strictly above the
    threshold); the half-life is then the first point at or below it, counted
    in absolute cycles from injection.
    """
    horizon = len(curve) - 1
    if not curve:
        return HalfLife(None, "empty", 0)
    start = next((i for i, v in enumerate(curve) if v > threshold), None)
    if start is None:
        return HalfLife(None, "never_established", horizon)
    for i in range(start + 1, len(curve)):
        if curve[i] <= threshold:
            hi, lo = curve[i - 1], curve[i]
            frac = (hi - threshold) / (hi - lo) if hi != lo else 0.0
            return HalfLife(i - 1 + frac, "observed", horizon)
    return HalfLife(None, "censored", horizon)


def km_curve(m: Matrix) -> list[float]:
    """Kaplan-Meier survival of first-passage death, among trials alive at cycle 0."""
    alive_at_0 = [row for row in m if row and row[0]]
    if not alive_at_0:
        return [0.0] * (len(m[0]) if m else 0)
    n = len(alive_at_0[0])
    death = [next((c for c in range(1, n) if not row[c]), None) for row in alive_at_0]
    s, at_risk, out = 1.0, len(alive_at_0), [1.0]
    for c in range(1, n):
        d = sum(1 for t in death if t == c)
        if at_risk:
            s *= 1 - d / at_risk
        at_risk -= d
        out.append(s)
    return out


def km_half_life(m: Matrix) -> HalfLife:
    if not m or not any(row and row[0] for row in m):
        return HalfLife(None, "never_established", len(m[0]) - 1 if m else 0)
    return prevalence_half_life(km_curve(m))


@dataclass(frozen=True)
class DecayFit:
    s0: float
    rate: float     # lambda per cycle
    floor: float    # asymptotic survival
    sse: float
    start: int = 0  # cycle of the curve's peak, where the fit begins

    def half_life(self) -> float:
        """Cycles until the fitted curve hits 0.5 (inf if the floor holds it up)."""
        if self.s0 < 0.5:
            return math.nan  # never established
        if self.floor >= 0.5 or self.rate <= 0:
            return math.inf
        return self.start + math.log((self.s0 - self.floor) / (0.5 - self.floor)) / self.rate

    def to_dict(self) -> dict:
        d = asdict(self)
        hl = self.half_life()
        d["half_life"] = None if not math.isfinite(hl) else hl
        d["half_life_status"] = "never_established" if math.isnan(hl) else ("infinite" if math.isinf(hl) else "fitted")
        return d


def fit_decay(curve: Sequence[float]) -> DecayFit:
    """Least-squares fit of c + (S0 - c) exp(-lambda n), fitted from the curve's peak."""
    if not curve:
        return DecayFit(0.0, 0.0, 0.0, 0.0, 0)
    peak = max(range(len(curve)), key=lambda i: curve[i])
    ys = list(curve[peak:])
    s0 = ys[0]
    best = DecayFit(s0, 0.0, s0, sum((y - s0) ** 2 for y in ys), peak)
    if len(ys) < 2 or s0 == 0:
        return best
    for k in range(-160, 41):  # lambda from 1e-4 to 10, log-spaced
        lam = 10 ** (k / 40)
        e = [math.exp(-lam * n) for n in range(len(ys))]
        # For fixed lambda the best floor has a closed form (1-D least squares).
        den = sum((1 - en) ** 2 for en in e)
        c = sum((1 - en) * (y - s0 * en) for en, y in zip(e, ys, strict=True)) / den if den else 0.0
        c = min(s0, max(0.0, c))
        sse = sum((c + (s0 - c) * en - y) ** 2 for en, y in zip(e, ys, strict=True))
        if sse < best.sse - 1e-12:
            best = DecayFit(s0, lam, c, sse, peak)
    return best


def bootstrap_half_life(m: Matrix, b: int = 200, seed: int = 0, alpha: float = 0.05) -> tuple[float, float, float]:
    """Percentile CI for the prevalence half-life; censored draws count as +inf.

    Returns (low, high, fraction_censored). Bounds can be inf.
    """
    if not m:
        return (math.nan, math.nan, math.nan)
    rng = random.Random(seed)
    vals = []
    for _ in range(b):
        sample = [m[rng.randrange(len(m))] for _ in m]
        hl = prevalence_half_life(prevalence(sample))
        vals.append(hl.value if hl.status == "observed" else (math.inf if hl.status == "censored" else 0.0))
    vals.sort()
    lo = vals[int(alpha / 2 * (b - 1))]
    hi = vals[int((1 - alpha / 2) * (b - 1))]
    return lo, hi, sum(math.isinf(v) for v in vals) / b


def rogan_gladen(p_obs: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed prevalence for a judge's known error rates."""
    j = sensitivity + specificity - 1
    if j <= 0:
        raise ValueError(
            f"judge is uninformative (sensitivity + specificity = {sensitivity + specificity:.2f} <= 1); "
            "its verdicts cannot be corrected"
        )
    return min(1.0, max(0.0, (p_obs + specificity - 1) / j))
