"""Guided walkthrough: ``halflife demo`` (or ``python examples/demo.py``).

Every number printed here comes from the offline simulated host. The demo is
about what the *instrument* shows and why each design choice exists; it is
not a measurement of any vendor's memory system.
"""

from __future__ import annotations

import textwrap
from dataclasses import replace

from .experiment import ExperimentConfig, run_experiment, run_matrix, run_trial
from .report import LEGEND, ascii_plot, matrix_table, summary

W = 92


def banner(title: str) -> None:
    print("\n" + "=" * W)
    print(title)
    print("=" * W)


def say(text: str) -> None:
    print(textwrap.fill(" ".join(text.split()), W))
    print()


def _yn(b: bool) -> str:
    return "yes" if b else "no "


def act_trace(base: ExperimentConfig) -> None:
    banner("1. One poisoned memory, rewritten cycle by cycle")
    say("""A zombie payload arrives once, inside a tool result. Below are the notes that descend
    from it (ground-truth lineage, invisible to the host) after selected consolidation
    cycles, plus what each detector says. The trial shown is the first seed where the
    literal string is gone by cycle 10 but the agent still complies, chosen to illustrate
    the gap, not because it is typical: the aggregate curves follow in section 2.""")
    cfg = replace(base, payload="zombie", cycles=20)
    for t in range(200):
        tr = run_trial(cfg, t, record_snapshots=True)
        if not tr.hits["literal"][10] and tr.hits["behavioral"][10]:
            break
    print(f"trial #{t}")
    for c in (0, 1, 3, 10, 20):
        h = tr.hits
        print(f"\n-- cycle {c:>2}   literal={_yn(h['literal'][c])} semantic={_yn(h['semantic'][c])} "
              f"agent complies={_yn(h['behavioral'][c])}")
        for note in tr.snapshots[c][:4]:
            print(textwrap.fill(str(note), W, initial_indent="   ", subsequent_indent="      "))
        if len(tr.snapshots[c]) > 4:
            print(f"   ... {len(tr.snapshots[c]) - 4} more derived notes")
    print()
    say("""Notice two things. The wording drifts (synonym swaps, dropped clauses) so a string
    matcher loses it within a few cycles. And the provenance tag flips from
    src=tool_result trust=untrusted to src=consolidation trust=system the first time the
    consolidator touches the note: the host now vouches for the attacker's text.""")


def act_detectors(base: ExperimentConfig) -> None:
    banner("2. The three detectors disagree, and the disagreement is the point")
    r = run_experiment(replace(base, payload="plain"))
    print(summary(r))
    print()
    say("""The literal half-life is a few cycles; the semantic one is longer; the agent keeps
    acting on the injection for far longer than either. That last gap is the write-back
    loop: each time the agent repeats the attacker's link, the host stores 'the assistant
    pointed the user to <link>' as the agent's own memory. Taint (simulation-only ground
    truth) shows the injection's descendants outliving every content-based detector.
    Reporting only a string-match survival rate would understate persistence badly.""")


def act_writeback(base: ExperimentConfig) -> None:
    banner("3. Ablation: switch off the write-back loop")
    on = run_experiment(replace(base, payload="plain"))
    off = run_experiment(replace(base, payload="plain", interactions_per_cycle=0))
    print(f"behavioral t½ with write-back : {on.detectors['behavioral'].half_life}")
    print(f"behavioral t½ without         : {off.detectors['behavioral'].half_life}")
    print()
    print(ascii_plot({"with write-back": on.detectors["behavioral"].curve,
                      "no write-back": off.detectors["behavioral"].curve}, height=8))
    print()
    say("""Same payload, same consolidator: the only difference is whether the agent's answers are
    remembered. Background memory that learns from the agent's own outputs turns a single
    injection into a self-sustaining one, without the attacker asking for it.""")


def act_laundering(base: ExperimentConfig) -> None:
    banner("4. Provenance tags without attribution-preserving consolidation get laundered")
    tags = run_experiment(replace(base, payload="plain", defense="provenance"))
    attr = run_experiment(replace(base, payload="plain", defense="attributed"))
    b1, b2 = tags.detectors["behavioral"].curve, attr.detectors["behavioral"].curve
    print(f"{'cycle':>6}  {'tags only':>10}  {'tags + attributed consolidation':>32}")
    for c in (0, 1, 2, 5, 10, 20, len(b1) - 1):
        print(f"{c:>6}  {b1[c]:>10.2f}  {b2[c]:>32.2f}")
    print()
    say("""At cycle 0 provenance tags work: the agent sees trust=untrusted and mostly refuses
    (the residue is the configured 10% leak, since no instruction hierarchy is perfect). One
    consolidation later, the merged note is relabelled as the consolidator's own write and the
    agent complies again. Tags only help if consolidation carries them through.""")


def act_matrix(base: ExperimentConfig) -> None:
    banner("5. Payloads x defenses, with the utility cost alongside")
    results = run_matrix(base, ["plain", "authoritative", "zombie", "stealth_fact", "fragmented"],
                         ["none", "provenance", "attributed", "ttl", "attributed+ttl"])
    print(matrix_table(results))
    print()
    print(LEGEND)
    print()
    say("""Read the behavioral column against the benign column. TTL quarantine kills attacks by
    hiding everything untrusted until a user verifies it, so benign facts from tools are
    invisible at first: benign t½ is counted from their first appearance, and the benign
    availability column (mean visibility over the run) is where that delay shows. Attributed consolidation
    keeps benign facts visible and still blocks most attacks, but it relies on the agent
    honoring tags (the leak rate). stealth_fact and fragmented are weak here because they
    compete with a legitimate billing fact or need a lucky merge; that is a property of this
    workload, and exactly the kind of claim to re-test on a real host.""")


def act_judge(base: ExperimentConfig) -> None:
    banner("6. A noisy judge, and correcting for it")
    noisy = replace(base, payload="plain", judge_fpr=0.15, judge_fnr=0.25, trials=max(base.trials, 100))
    r = run_experiment(noisy)
    from .stats import prevalence_half_life
    truth = r.detectors["behavioral"].half_life
    raw = prevalence_half_life(r.behavioral_observed)
    true = r.detectors["behavioral"].curve
    mae = lambda c: sum(abs(a - b) for a, b in zip(c, true)) / len(true)
    print(f"judge calibration: sensitivity={r.judge.sensitivity:.2f} specificity={r.judge.specificity:.2f}")
    print(f"mean |error| vs true curve:  as judged={mae(r.behavioral_observed):.3f}   "
          f"corrected={mae(r.behavioral_corrected):.3f}")
    print(f"behavioral t½  true={truth}   as judged={raw}   corrected={r.corrected_half_life}")
    print()
    print(ascii_plot({"true": true, "judged": r.behavioral_observed,
                      "corrected": r.behavioral_corrected}, height=8))
    print()
    say("""An LLM judge with 15% false positives and 25% false negatives pulls the survival curve
    toward the middle. Measuring the judge on labelled responses and applying the Rogan-Gladen
    correction removes that bias (compare the error column), but it inflates variance by
    1/(sensitivity + specificity - 1). When the true curve sits near 0.5, as here, the
    corrected half-life can still land far from the true one: budget more trials, or report
    the corrected curve with its interval instead of a single crossing point. With a live judge,
    the labelled set has to come from humans or a rule such as a unique canary URL.""")


def act_real_host() -> None:
    banner("7. Measuring a real host")
    print(textwrap.dedent("""\
        from halflife import ExperimentConfig, run_experiment
        from halflife.llm import ClaudeClient, LLMAgent, LLMConsolidator, LLMJudge

        llm = ClaudeClient()                      # reads credentials from the environment
        result = run_experiment(
            ExperimentConfig(payload="zombie", defense="provenance", trials=30, cycles=20),
            consolidator_factory=lambda: LLMConsolidator(llm),
            agent=LLMAgent(llm),
            judge=LLMJudge(ClaudeClient(effort="low")),
        )

        # Any other host (a Letta sleep-time agent, your own pipeline):
        from halflife.consolidator import CallableConsolidator
        consolidator_factory = lambda: CallableConsolidator(my_rewrite_fn)   # list[str] -> list[str]
    """))


def main(quick: bool = False) -> None:
    base = ExperimentConfig(trials=40 if quick else 150, cycles=30)
    say(f"""HALFLIFE demo: {base.trials} trials x {base.cycles} consolidation cycles per configuration,
    simulated host, seed {base.seed}. Re-run with a different seed to see the run-to-run spread.""")
    act_trace(base)
    act_detectors(base)
    act_writeback(base)
    act_laundering(base)
    act_matrix(base)
    act_judge(base)
    act_real_host()


if __name__ == "__main__":  # pragma: no cover
    main()
