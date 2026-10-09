"""Command line: ``halflife run | compare | list | demo``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields

from .defenses import DEFENSES
from .experiment import ExperimentConfig, run_experiment, run_matrix
from .payloads import CHANNELS, PAYLOADS
from .report import LEGEND, matrix_table, summary


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--channel", default="tool_result", choices=sorted(CHANNELS))
    p.add_argument("--cycles", type=int, default=30)
    p.add_argument("--trials", type=int, default=100)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--capacity", type=int, default=30)
    p.add_argument("--interactions", type=int, default=2, help="user interactions (with write-back) per cycle")
    p.add_argument("--judge-fpr", type=float, default=0.0)
    p.add_argument("--judge-fnr", type=float, default=0.0)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override a consolidator parameter, e.g. --set obey_prob=0.9")
    p.add_argument("--json", metavar="PATH", help="write full results as JSON")


def _config(a: argparse.Namespace, **kw) -> ExperimentConfig:
    cons = {}
    for item in a.set:
        key, _, val = item.partition("=")
        if not _:
            raise SystemExit(f"--set expects KEY=VALUE, got {item!r}")
        cons[key] = float(val)
    return ExperimentConfig(channel=a.channel, cycles=a.cycles, trials=a.trials, seed=a.seed,
                            capacity=a.capacity, interactions_per_cycle=a.interactions,
                            judge_fpr=a.judge_fpr, judge_fnr=a.judge_fnr, consolidator=cons, **kw)


def _progress(done: int, total: int) -> None:
    if sys.stderr.isatty():
        print(f"\r  trial {done}/{total}", end="" if done < total else "\n", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="halflife", description="Measure how long a poisoned memory survives consolidation.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="one payload x one defense")
    run.add_argument("--payload", default="plain", choices=sorted(PAYLOADS))
    run.add_argument("--defense", default="none", choices=sorted(DEFENSES))
    run.add_argument("--no-plot", action="store_true")
    _add_common(run)

    cmp_ = sub.add_parser("compare", help="payloads x defenses matrix")
    cmp_.add_argument("--payloads", default=",".join(PAYLOADS))
    cmp_.add_argument("--defenses", default=",".join(DEFENSES))
    cmp_.add_argument("--channels", default=None, help="comma list; default is --channel")
    cmp_.add_argument("--markdown", action="store_true")
    _add_common(cmp_)

    sub.add_parser("list", help="list payloads, channels and defenses")
    demo = sub.add_parser("demo", help="guided walkthrough")
    demo.add_argument("--quick", action="store_true", help="fewer trials")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            print("payloads:")
            for p in PAYLOADS.values():
                print(f"  {p.name:<14} {p.description}")
            print("channels:")
            for c in CHANNELS.values():
                print(f"  {c.name:<14} trust={c.trust.name.lower()}")
            print("defenses:")
            for d in DEFENSES.values():
                flags = [f.name for f in fields(d) if f.name not in ("name", "quarantine_below") and getattr(d, f.name)]
                print(f"  {d.name:<14} {', '.join(f'{f}={getattr(d, f)}' if f == 'ttl' else f for f in flags) or '-'}")
            return 0
        if a.cmd == "demo":
            from .demo import main as demo_main
            demo_main(quick=a.quick)
            return 0
        if a.cmd == "run":
            r = run_experiment(_config(a, payload=a.payload, defense=a.defense), progress=_progress)
            print(summary(r, plot=not a.no_plot))
            results = [r]
        else:
            split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
            channels = split(a.channels) if a.channels else None
            results = run_matrix(_config(a), split(a.payloads), split(a.defenses), channels)
            print(matrix_table(results, markdown=a.markdown))
            print()
            print(LEGEND)
        if a.json:
            with open(a.json, "w") as f:
                json.dump([r.to_dict() for r in results] if len(results) > 1 else results[0].to_dict(), f, indent=2)
            print(f"\nwrote {a.json}")
        return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
