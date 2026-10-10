"""Interactive showcase: ``halflife showcase --out showcase.html``.

Runs the full grid (every payload x defense x channel, with and without
write-back), a judge-noise sweep, and a few recorded traces, then renders one
self-contained HTML page with no external dependencies except web fonts.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from importlib import resources
from typing import Callable

from .defenses import DEFENSES
from .experiment import DETECTORS, ExperimentConfig, ExperimentResult, run_experiment, run_trial
from .payloads import CHANNELS, PAYLOADS
from .stats import HalfLife

JUDGE_LEVELS = (0.0, 0.1, 0.2, 0.3)  # keys are formatted to one decimal
TRACE_TRIALS = 3

DEFENSE_NOTES = {
    "none": "No defense. Consolidation relabels what it rewrites as its own trusted writes.",
    "provenance": "Notes are tagged with their source and the agent treats untrusted ones as data. "
                  "Consolidation still relabels merged notes.",
    "attributed": "Tags plus consolidation that keeps them, never merges untrusted into trusted notes, "
                  "and ignores 'keep this verbatim' requests from untrusted text.",
    "ttl": "Untrusted notes are quarantined for 3 cycles and dropped unless the user verifies them.",
    "attributed+ttl": "Attributed consolidation and TTL quarantine together.",
    "gated": "Tags, and the agent's answers are not written back when they came from an untrusted note. "
             "The gate only sees the stored tag, which naive consolidation may already have laundered.",
    "sanitize": "Directive sentences from untrusted sources are dropped when stored. Plain facts pass through.",
}


def _hl(h: HalfLife) -> dict:
    return {"s": str(h), "v": None if h.value is None else round(h.value, 2), "st": h.status}


def _num(x: float) -> float | str | None:
    if math.isnan(x):
        return None
    return "inf" if math.isinf(x) else round(x, 2)


def _compact(r: ExperimentResult) -> dict:
    out = {}
    for d in DETECTORS:
        s = r.detectors[d]
        fit = s.fit.half_life()
        out[d] = {
            "c": [round(v, 3) for v in s.curve],
            "hl": _hl(s.half_life),
            "km": _hl(s.km_half_life),
            "ci": [_num(s.bootstrap[0]), _num(s.bootstrap[1])],
            "fit": _num(fit),
        }
    return out


def _grid_job(cfg: ExperimentConfig) -> tuple[str, dict]:
    key = f"{cfg.channel}|{cfg.payload}|{cfg.defense}|{int(cfg.interactions_per_cycle > 0)}"
    return key, _compact(run_experiment(cfg))


def _judge_job(cfg: ExperimentConfig) -> tuple[str, dict]:
    r = run_experiment(cfg)
    return f"{cfg.judge_fpr:.1f}|{cfg.judge_fnr:.1f}", {
        "obs": [round(v, 3) for v in r.behavioral_observed],
        "se": round(r.judge.sensitivity, 4),
        "sp": round(r.judge.specificity, 4),
        "true": [round(v, 3) for v in r.detectors["behavioral"].curve],
    }


def _trace_job(args: tuple[ExperimentConfig, int]) -> tuple[str, int, dict]:
    cfg, trial = args
    tr = run_trial(cfg, trial, record_snapshots=True)
    return f"{cfg.channel}|{cfg.payload}|{cfg.defense}", trial, {
        "hits": {d: [int(b) for b in tr.hits[d]] for d in DETECTORS},
        "notes": [[(n.source, n.trust, n.text, int(n.quarantined)) for n in cyc] for cyc in tr.snapshots],
    }


def _map(fn, jobs, workers: int, progress: Callable[[int, int], None] | None, done: list[int], total: int):
    def tick(x):
        done[0] += 1
        if progress:
            progress(done[0], total)
        return x
    if workers <= 1:
        return [tick(fn(j)) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return [tick(x) for x in ex.map(fn, jobs, chunksize=1)]


def build_data(trials: int = 80, cycles: int = 30, seed: int = 0, workers: int = 1,
               progress: Callable[[int, int], None] | None = None) -> dict:
    base = ExperimentConfig(trials=trials, cycles=cycles, seed=seed)
    base.validate()
    grid = [replace(base, channel=c, payload=p, defense=d, interactions_per_cycle=wb)
            for c in CHANNELS for p in PAYLOADS for d in DEFENSES for wb in (2, 0)]
    judge = [replace(base, payload="plain", judge_fpr=a, judge_fnr=b) for a in JUDGE_LEVELS for b in JUDGE_LEVELS]
    traces = [(replace(base, channel=c, payload=p, defense=d), t)
              for c in CHANNELS for p in PAYLOADS for d in DEFENSES for t in range(TRACE_TRIALS)]
    total, done = len(grid) + len(judge) + len(traces), [0]

    runs = dict(_map(_grid_job, grid, workers, progress, done, total))
    judges = dict(_map(_judge_job, judge, workers, progress, done, total))

    # Notes repeat across cycles, so intern every string once.
    pool: dict[str, int] = {}
    def ref(s: str) -> int:
        return pool.setdefault(s, len(pool))
    trace_out: dict[str, list] = {}
    for key, trial, tr in _map(_trace_job, traces, workers, progress, done, total):
        tr["notes"] = [[[ref(src), ref(trust), ref(text), q] for src, trust, text, q in cyc] for cyc in tr["notes"]]
        trace_out.setdefault(key, [None] * TRACE_TRIALS)[trial] = tr

    return {
        "meta": {"trials": trials, "cycles": cycles, "seed": seed, "judge_levels": list(JUDGE_LEVELS)},
        "payloads": {p.name: {"desc": p.description, "text": list(p.sentences)} for p in PAYLOADS.values()},
        "channels": {c.name: c.trust.name.lower() for c in CHANNELS.values()},
        "defenses": {d: DEFENSE_NOTES.get(d, "") for d in DEFENSES},
        "runs": runs,
        "judge": judges,
        "traces": trace_out,
        "strings": list(pool),
    }


_SKELETON = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
             '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
             '</head>\n<body>\n{}\n</body>\n</html>\n')


def render_html(data: dict, standalone: bool = True) -> str:
    """Fill the page template. ``standalone=False`` returns the body fragment only."""
    template = resources.files("halflife").joinpath("showcase_template.html").read_text(encoding="utf-8")
    # Escape every "<" (as \u003c, valid inside JSON strings): "</script>" would close the element,
    # and "<!--" followed by "<script" switches the HTML tokenizer into an escaped state.
    payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    page = template.replace("__HALFLIFE_DATA__", payload, 1)
    return _SKELETON.format(page) if standalone else page
