"""Paired comparisons between configurations.

Trial i of every configuration starts from the same seed (same warm memory,
same traffic until the configurations diverge), so two configurations can be
compared trial by trial. The per-trial statistic is the area under that trial's
survival indicator, i.e. the share of cycles in which the detector fired. The
paired difference is tested with a sign-flip permutation test and given a
bootstrap interval; ``holm`` controls the family-wise error across many tests.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

from .experiment import ExperimentResult


def trial_auc(matrix: Sequence[Sequence[bool]]) -> list[float]:
    return [sum(row) / len(row) if row else 0.0 for row in matrix]


@dataclass(frozen=True)
class Paired:
    mean_diff: float          # mean of (a - b) over trials
    ci: tuple[float, float]   # 95% bootstrap interval of the mean difference
    p_value: float            # two-sided sign-flip permutation test
    n: int

    def significant(self, alpha: float = 0.05) -> bool:
        return self.p_value < alpha

    def direction(self, alpha: float = 0.05) -> int:
        """+1 if a > b significantly, -1 if a < b significantly, else 0."""
        if not self.significant(alpha):
            return 0
        return 1 if self.mean_diff > 0 else -1

    def __str__(self) -> str:
        return f"{self.mean_diff:+.3f} [{self.ci[0]:+.3f}, {self.ci[1]:+.3f}] p={self.p_value:.3g}"


def paired(a: Sequence[float], b: Sequence[float], *, resamples: int = 2000, seed: int = 0) -> Paired:
    if len(a) != len(b):
        raise ValueError(f"paired samples need equal length, got {len(a)} and {len(b)}")
    if not a:
        raise ValueError("paired comparison needs at least one trial")
    d = [x - y for x, y in zip(a, b, strict=True)]
    n = len(d)
    # math.fsum is exactly rounded on every Python version; the built-in sum() only
    # compensates for rounding from 3.12, so results would otherwise differ across versions.
    mean = math.fsum(d) / n
    rng = random.Random(seed)
    boots = sorted(math.fsum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    ci = (boots[int(0.025 * (resamples - 1))], boots[int(0.975 * (resamples - 1))])
    if all(x == 0 for x in d):
        return Paired(0.0, (0.0, 0.0), 1.0, n)
    observed = abs(mean)
    extreme = sum(abs(math.fsum(x if rng.random() < 0.5 else -x for x in d) / n) >= observed - 1e-12
                  for _ in range(resamples))
    return Paired(mean, ci, (extreme + 1) / (resamples + 1), n)


def compare(a: ExperimentResult, b: ExperimentResult, detector: str = "behavioral",
            detector_b: str | None = None, **kw) -> Paired:
    """Paired difference a - b on one detector (or two detectors of the same run)."""
    if a.per_trial is None or b.per_trial is None:
        raise ValueError("results carry no per-trial data; rerun with the current version")
    if (a.config.seed, a.config.trials) != (b.config.seed, b.config.trials):
        raise ValueError("paired comparison needs the same seed and trial count in both runs")
    return paired(trial_auc(a.per_trial[detector]), trial_auc(b.per_trial[detector_b or detector]), **kw)


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values, in the input order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted
