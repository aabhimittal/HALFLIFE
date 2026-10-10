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
    p.add_argument("--reexpose", type=int, default=0, metavar="N",
                   help="re-ingest the attacker's content every N cycles (0 = a single write)")
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
                            judge_fpr=a.judge_fpr, judge_fnr=a.judge_fnr, consolidator=cons,
                            reexpose_every=a.reexpose, **kw)


def _progress(done: int, total: int) -> None:
    if sys.stderr.isatty():
        print(f"\r  {done}/{total}", end="" if done < total else "\n", file=sys.stderr)


def _make_client(**kw):
    """Indirection so tests can substitute a fake client."""
    from .llm import ClaudeClient
    return ClaudeClient(**kw)


def _add_live_components(p: argparse.ArgumentParser, default: str = "claude") -> None:
    p.add_argument("--consolidator", default=default, choices=["claude", "sim"])
    p.add_argument("--agent", default=default, choices=["claude", "sim"])
    p.add_argument("--judge", default="rule", choices=["rule", "claude"],
                   help="rule is exact for canary goals; claude is needed for goals a rule cannot check")
    p.add_argument("--semantic", default="claude" if default == "claude" else "lexical",
                   choices=["claude", "lexical"],
                   help="lexical only sees the simulator's synonym swaps; use claude on a real host")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--workers", type=int, default=4, help="trials run concurrently")
    p.add_argument("--cache", default=".halflife/cache.sqlite", help="response cache (resume costs nothing)")
    p.add_argument("--yes", action="store_true", help="actually make model calls (otherwise only estimate)")


def _uses_model(a: argparse.Namespace) -> bool:
    return "claude" in (a.consolidator, a.agent, a.judge, a.semantic)


def _estimate_rows(a: argparse.Namespace, cfg: ExperimentConfig):
    from .llm.cost import estimate
    m = a.model
    return estimate(cfg, consolidator=m if a.consolidator == "claude" else None,
                    agent=m if a.agent == "claude" else None,
                    judge=m if a.judge == "claude" else None,
                    semantic=m if a.semantic == "claude" else None)


def _components(a: argparse.Namespace):
    """(run_experiment kwargs, client or None) for the components chosen on the command line."""
    if not _uses_model(a):
        return {}, None, None
    from .llm import LLMAgent, LLMConsolidator, LLMJudge
    from .llm.cache import ResponseCache
    from .semantic import LLMSemantic
    cache = ResponseCache(a.cache) if a.cache else None
    llm = _make_client(model=a.model, effort=a.effort, cache=cache)
    kw = dict(
        consolidator_factory=(lambda: LLMConsolidator(llm)) if a.consolidator == "claude" else None,
        agent=LLMAgent(llm) if a.agent == "claude" else None,
        judge=LLMJudge(llm) if a.judge == "claude" else None,
        semantic=LLMSemantic(llm) if a.semantic == "claude" else None,
    )
    return kw, llm, cache


def _live(a: argparse.Namespace) -> int:
    from .llm.cost import format_estimate

    cfg = _config(a, payload=a.payload, defense=a.defense)
    cfg.validate()
    m = a.model
    rows = _estimate_rows(a, cfg)
    print(f"live run: payload={cfg.payload} defense={cfg.defense} channel={cfg.channel} "
          f"trials={cfg.trials} cycles={cfg.cycles} model={m}\n")
    print(format_estimate(rows))
    if a.semantic == "lexical" and a.consolidator == "claude":
        print("\nwarning: the lexical semantic detector only undoes the simulator's synonym swaps; "
              "on a real consolidator it will report meaning as lost too early.")
    if not a.yes:
        print("\nNothing was spent. Re-run with --yes to make these calls.")
        return 0

    kw, llm, cache = _components(a)
    checkpoint = a.checkpoint or f".halflife/live-{cfg.payload}-{cfg.defense}-{cfg.channel}-s{cfg.seed}.jsonl"
    try:
        r = run_experiment(cfg, checkpoint=checkpoint, workers=a.workers, progress=_progress, **kw)
    except KeyboardInterrupt:
        print(f"\ninterrupted; finished trials are saved in {checkpoint}. Re-run the same command to resume.")
        return 130
    print(summary(r, plot=not getattr(a, "no_plot", False)))
    if llm is not None:
        hits = f", cache hits {cache.hits}" if cache else ""
        print(f"\nAPI calls {llm.calls}{hits}; tokens in {llm.input_tokens:,}, out {llm.output_tokens:,}.")
    print(f"Checkpoint: {checkpoint}")
    if a.json:
        with open(a.json, "w") as f:
            f.write(r.to_json(include_trials=True, indent=2))
        print(f"wrote {a.json}")
    return 0


def _suite(a: argparse.Namespace) -> int:
    from .suite import SUITES, leaderboard, load_results, run_suite, validate
    if a.suite_cmd == "validate":
        bad = 0
        for f in a.files:
            problems = validate(json.loads(open(f).read()))
            print(f"{f}: " + ("ok" if not problems else "; ".join(problems)))
            bad += bool(problems)
        return 1 if bad else 0
    if a.suite_cmd == "leaderboard":
        text = leaderboard(load_results(a.files))
        if a.out:
            with open(a.out, "w") as f:
                f.write("# HALFLIFE leaderboard\n\n" + text)
            print(f"wrote {a.out}")
        else:
            print(text)
        return 0
    if a.suite not in SUITES:
        raise ValueError(f"unknown suite {a.suite!r}; choose from {sorted(SUITES)}")
    spec = SUITES[a.suite]
    cfgs = spec.configs(a.defense)
    if _uses_model(a):
        from .llm.cost import RoleEstimate, format_estimate
        per = _estimate_rows(a, cfgs[0])
        rows = [RoleEstimate(r.role, r.model, r.calls * len(cfgs), r.input_tokens * len(cfgs),
                             r.output_tokens * len(cfgs)) for r in per]
        print(f"suite {spec.name}: {len(cfgs)} configurations x {spec.base.trials} trials x "
              f"{spec.base.cycles} cycles\n")
        print(format_estimate(rows))
        if not a.yes:
            print("\nNothing was spent. Re-run with --yes to make these calls.")
            return 0
    kw, llm, _ = _components(a)
    if llm is not None:
        kw["workers"] = a.workers
    res = run_suite(spec, a.host, defense=a.defense, notes=a.notes, progress=_progress,
                    checkpoint_dir=f".halflife/suite-{spec.name}-{a.host}" if llm is not None else None, **kw)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    sc = res["scores"]
    print(f"{a.host}: persistence {sc['persistence']:.3f} (lower is safer), utility {sc['utility']:.3f}. "
          f"wrote {a.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="halflife",
                                 description="Measure how long a poisoned memory survives consolidation.")
    from . import __version__
    ap.add_argument("--version", action="version", version=f"halflife {__version__}")
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

    show = sub.add_parser("showcase", help="write the interactive HTML showcase")
    show.add_argument("--out", default="halflife-showcase.html", help="output HTML path")
    show.add_argument("--trials", type=int, default=100)
    show.add_argument("--cycles", type=int, default=30)
    show.add_argument("--seed", type=int, default=0)
    show.add_argument("--workers", type=int, default=1, help="parallel processes")

    live = sub.add_parser("live", help="measure with Claude-backed components (spends API credit)")
    live.add_argument("--payload", default="zombie", choices=sorted(PAYLOADS))
    live.add_argument("--defense", default="none", choices=sorted(DEFENSES))
    _add_live_components(live)
    live.add_argument("--checkpoint", default=None, help="JSONL of finished trials; default under .halflife/")
    _add_common(live)
    live.set_defaults(trials=30, cycles=20)

    sw = sub.add_parser("sweep", help="re-test the headline claims across one parameter's range")
    sw.add_argument("--param", required=True, help="a consolidator or experiment parameter, e.g. obey_prob")
    sw.add_argument("--values", required=True, help="comma-separated values, e.g. 0.1,0.3,0.5,0.7,0.9")
    sw.add_argument("--claims", default=None, help="comma-separated claim names (default: all)")
    _add_common(sw)
    sw.set_defaults(trials=60, cycles=20)

    st = sub.add_parser("suite", help="frozen benchmark suite: run a host, validate results, rank them")
    st_sub = st.add_subparsers(dest="suite_cmd", required=True)
    sr = st_sub.add_parser("run", help="run the suite against one host and write a result file")
    sr.add_argument("--suite", default="v0")
    sr.add_argument("--host", required=True, help="a name for the memory system under test")
    sr.add_argument("--defense", default="none", choices=sorted(DEFENSES),
                    help="the host's own defense configuration (simulated hosts)")
    sr.add_argument("--notes", default="", help="free text stored with the result (model, prompt version...)")
    sr.add_argument("--out", required=True)
    _add_live_components(sr, default="sim")
    sl = st_sub.add_parser("leaderboard", help="rank result files (markdown)")
    sl.add_argument("files", nargs="+")
    sl.add_argument("--out", default=None)
    sv = st_sub.add_parser("validate", help="check result files against the schema")
    sv.add_argument("files", nargs="+")

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
        if a.cmd == "sweep":
            from .sweep import CLAIMS, format_sweep, run_sweep
            names = [x.strip() for x in a.claims.split(",")] if a.claims else None
            unknown = set(names or []) - {c.name for c in CLAIMS}
            if unknown:
                raise ValueError(f"unknown claims {sorted(unknown)}; choose from {[c.name for c in CLAIMS]}")
            claims = [c for c in CLAIMS if names is None or c.name in names]
            try:
                values = [float(v) for v in a.values.split(",") if v.strip()]
            except ValueError:
                raise ValueError(f"--values must be numbers, got {a.values!r}") from None
            res = run_sweep(_config(a), a.param, values, claims, progress=_progress)
            print(format_sweep(res))
            if a.json:
                with open(a.json, "w") as f:
                    json.dump(res.to_dict(), f, indent=2)
                print(f"\nwrote {a.json}")
            return 0
        if a.cmd == "live":
            return _live(a)
        if a.cmd == "suite":
            return _suite(a)
        if a.cmd == "showcase":
            from .showcase import build_data, render_html
            data = build_data(a.trials, a.cycles, a.seed, a.workers, progress=_progress)
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(render_html(data))
            print(f"wrote {a.out} ({len(data['runs'])} configurations x {a.trials} trials); open it in a browser")
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
