"""Plain-text and Markdown reporting (no plotting dependencies)."""

from __future__ import annotations

import math
from typing import Sequence

from .experiment import ExperimentResult
from .stats import HalfLife

BARS = " ▁▂▃▄▅▆▇█"


def sparkline(curve: Sequence[float]) -> str:
    return "".join(BARS[min(8, max(0, round(v * 8)))] for v in curve)


MARKS = {"literal": "L", "semantic": "S", "behavioral": "A", "taint": "T", "benign": "b"}


def ascii_plot(series: dict[str, Sequence[float]], height: int = 10, width: int | None = None) -> str:
    """Overlay survival curves; known detectors use MARKS, others their first letter."""
    if not series:
        return ""
    marks = {k: MARKS.get(k, k[0].upper()) for k in series}
    n = max(len(s) for s in series.values())
    width = width or n
    grid = [[" "] * width for _ in range(height + 1)]
    for name, curve in series.items():
        mark = marks[name]
        for x in range(width):
            i = round(x * (len(curve) - 1) / max(1, width - 1)) if len(curve) > 1 else 0
            y = height - round(curve[i] * height)
            grid[y][x] = mark if grid[y][x] in (" ", "·") else "*"
    half = height - round(0.5 * height)
    grid[half] = [c if c != " " else "·" for c in grid[half]]
    lines = [f"{'1.0' if r == 0 else ('0.5' if r == half else ('0.0' if r == height else '   '))} |{''.join(row)}"
             for r, row in enumerate(grid)]
    lines.append("    +" + "-" * width)
    lines.append(f"     cycle 0{' ' * max(0, width - len(str(n - 1)) - 7)}{n - 1}")
    lines.append("     " + "  ".join(f"{marks[k]}={k}" for k in series) + "  *=overlap  ·=0.5")
    return "\n".join(lines)


def fit_str(fit) -> str:
    hl = fit.half_life()
    return "n/e" if math.isnan(hl) else _n(hl)


def summary(r: ExperimentResult, plot: bool = True) -> str:
    c = r.config
    out = [f"payload={c.payload}  channel={c.channel}  defense={c.defense}  "
           f"trials={c.trials}  cycles={c.cycles}  seed={c.seed}", ""]
    out.append(f"{'detector':<11}{'t½':>7}{'95% CI':>15}{'KM t½':>8}{'fit t½':>8}  curve")
    for name, d in r.detectors.items():
        lo, hi, _ = d.bootstrap
        ci = "-" if math.isnan(lo) else f"[{_n(lo)}, {_n(hi)}]"
        out.append(f"{name:<11}{str(d.half_life):>7}{ci:>15}{str(d.km_half_life):>8}"
                   f"{fit_str(d.fit):>8}  {sparkline(d.curve)}")
    j = r.judge
    out.append("")
    out.append(f"judge: sensitivity={j.sensitivity:.2f} specificity={j.specificity:.2f} "
               f"(n={j.n_pos}+{j.n_neg})")
    if r.behavioral_corrected is not None and (c.judge_fpr or c.judge_fnr):
        raw = HalfLife_from(r.behavioral_observed)
        out.append(f"behavioral t½ as judged={raw}  corrected (Rogan-Gladen)={r.corrected_half_life}")
    if plot:
        out += ["", ascii_plot({k: r.detectors[k].curve for k in ("literal", "semantic", "behavioral", "benign")},
                               width=min(60, c.cycles + 1))]
    return "\n".join(out)


def HalfLife_from(curve: Sequence[float]) -> HalfLife:
    from .stats import prevalence_half_life
    return prevalence_half_life(curve)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _n(x: float) -> str:
    return "inf" if math.isinf(x) else f"{x:.1f}"


COLUMNS = ("literal", "semantic", "behavioral", "taint", "benign")


def matrix_table(results: list[ExperimentResult], markdown: bool = False) -> str:
    head = ["channel", "payload", "defense"] + [f"t½ {c}" for c in COLUMNS] + ["beh. @end", "benign avail"]
    rows = [[r.config.channel, r.config.payload, r.config.defense]
            + [str(r.detectors[c].half_life) for c in COLUMNS]
            + [f"{r.detectors['behavioral'].curve[-1]:.2f}", f"{_mean(r.detectors['benign'].curve):.2f}"]
            for r in results]
    if markdown:
        lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        lines += ["| " + " | ".join(row) + " |" for row in rows]
        return "\n".join(lines)
    widths = [max(len(x) for x in col) for col in zip(head, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" if i < 3 else f"{{:>{w}}}" for i, w in enumerate(widths))
    return "\n".join([fmt.format(*head), fmt.format(*["-" * w for w in widths])] + [fmt.format(*r) for r in rows])


LEGEND = ("t½ = cycles until survival probability drops to 0.5 (interpolated).  "
          ">N = still above 0.5 at the horizon (censored).  n/e = never rose above 0.5.\n"
          "beh. @end = fraction of trials in which the agent still complies at the last cycle.\n"
          "benign avail = mean fraction of cycles a benign same-channel fact is visible to the agent.\n"
          "benign = a useful fact sent on the same channel: a defense that kills it is paying for safety with utility.")
