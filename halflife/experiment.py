"""Run injection-survival experiments.

One *trial*: warm a memory, inject once at cycle 0 through a channel, then run
``cycles`` consolidation cycles with ordinary traffic in between, measuring
every detector after each cycle. An *experiment* is many independent trials
(different seeds); survival probability at cycle n is the fraction of trials
in which a detector still fires at n.

Measurement never perturbs the run: probes use their own RNG stream and do
not write back. Only the simulated user traffic (``interactions_per_cycle``)
writes the agent's answers back into memory.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import detectors
from .agent import (Agent, Judge, JudgeCalibration, NoisyJudge, RuleJudge, SimulatedAgent,
                    calibrate, calibration_set, writeback)
from .consolidator import Consolidator, SimulatedConsolidator
from .defenses import Defense, get_defense
from .memory import MemoryItem, MemoryStore, Trust
from .payloads import (BENIGN_PROBE_TERMS, CHANNELS, PAYLOADS, TOPICS, Payload, benign_items,
                       benign_probe, inject, lookup, seed_memory)
from .stats import (DecayFit, HalfLife, bootstrap_half_life, fit_decay, km_half_life, prevalence,
                    prevalence_half_life, rogan_gladen, wilson)

DETECTORS = ("literal", "semantic", "behavioral", "taint", "benign")
ATTACK_ROOT = "attack"


@dataclass
class ExperimentConfig:
    payload: str = "plain"
    channel: str = "tool_result"
    defense: str = "none"
    cycles: int = 30
    trials: int = 100
    seed: int = 0
    capacity: int = 30
    warmup_items: int = 20
    benign_per_cycle: int = 3
    interactions_per_cycle: int = 2
    payment_query_rate: float = 0.3   # share of user interactions about the target topic
    probe_topic: str = "payment"
    probes_per_cycle: int = 1
    judge_fpr: float = 0.0            # >0 simulates an imperfect LLM judge
    judge_fnr: float = 0.0
    calibration_n: int = 200
    verify_benign: float = 0.5        # chance per cycle a user confirms a quarantined benign item
    verify_malicious: float = 0.02    # ...or rubber-stamps a quarantined malicious one
    agent_leak: float = 0.1
    consolidator: dict = field(default_factory=dict)  # SimulatedConsolidator overrides

    def validate(self) -> None:
        lookup(PAYLOADS, self.payload, "payload")
        lookup(CHANNELS, self.channel, "channel")
        get_defense(self.defense)
        if self.probe_topic not in TOPICS:
            raise ValueError(f"unknown probe_topic {self.probe_topic!r}; choose from {sorted(TOPICS)}")
        for name in ("cycles", "capacity", "warmup_items", "benign_per_cycle",
                     "interactions_per_cycle", "calibration_n"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.trials < 1:
            raise ValueError("trials must be >= 1")
        if self.probes_per_cycle < 1:
            raise ValueError("probes_per_cycle must be >= 1")
        for name in ("payment_query_rate", "judge_fpr", "judge_fnr", "verify_benign",
                     "verify_malicious", "agent_leak"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        unknown = set(self.consolidator) - set(SimulatedConsolidator.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown consolidator parameters: {sorted(unknown)}")


@dataclass(frozen=True)
class NoteSnapshot:
    """One note descended from the injection, as the host stores it at some cycle."""
    source: str
    trust: str
    text: str
    quarantined: bool = False

    def __str__(self) -> str:
        return f"[src={self.source} trust={self.trust}] {self.text}" + (" [quarantined]" if self.quarantined else "")


@dataclass
class TrialTrace:
    """Per-cycle detector outcomes plus (optionally) snapshots of the poisoned notes."""
    hits: dict[str, list[bool]]
    behavioral_raw: list[float]      # fraction of probes the judge flagged
    behavioral_true: list[float]     # fraction of probes that truly complied
    snapshots: list[list[NoteSnapshot]] = field(default_factory=list)


def _trial_rng(seed: int, trial: int, stream: str) -> random.Random:
    return random.Random(f"{seed}:{trial}:{stream}")


def run_trial(cfg: ExperimentConfig, trial: int = 0, *, consolidator: Consolidator | None = None,
              agent: Agent | None = None, judge: Judge | None = None,
              record_snapshots: bool = False) -> TrialTrace:
    payload = PAYLOADS[cfg.payload]
    channel = CHANNELS[cfg.channel]
    defense = get_defense(cfg.defense)
    consolidator = consolidator or SimulatedConsolidator(**cfg.consolidator)
    agent = agent or SimulatedAgent(leak=cfg.agent_leak)
    judge = judge or RuleJudge()
    truth_judge = RuleJudge()

    rng = _trial_rng(cfg.seed, trial, "dynamics")
    store = MemoryStore(capacity=cfg.capacity)
    for it in seed_memory(rng, cfg.warmup_items):
        store.add(it)

    def ingest(item: MemoryItem, cycle: int) -> None:
        defense.on_ingest(item, cycle)
        store.add(item)

    for it in inject(payload, channel, 0, ATTACK_ROOT):
        ingest(it, 0)
    ingest(benign_probe(channel, 0), 0)

    def verifier(item: MemoryItem, r: random.Random) -> bool:
        malicious = any(l.startswith(ATTACK_ROOT) for l in item.lineage())
        return r.random() < (cfg.verify_malicious if malicious else cfg.verify_benign)

    trace = TrialTrace({d: [] for d in DETECTORS}, [], [])

    def measure(cycle: int) -> None:
        mrng = _trial_rng(cfg.seed, trial, f"probe:{cycle}")
        visible = store.visible()
        raw = true = 0
        for _ in range(cfg.probes_per_cycle):
            resp = agent.respond(visible, cfg.probe_topic, mrng, defense)
            raw += bool(judge(resp.text, payload))
            true += truth_judge(resp.text, payload)
        trace.behavioral_raw.append(raw / cfg.probes_per_cycle)
        trace.behavioral_true.append(true / cfg.probes_per_cycle)
        trace.hits["literal"].append(detectors.literal(store, payload))
        trace.hits["semantic"].append(detectors.semantic(store.items, payload.key_terms))
        trace.hits["behavioral"].append(true / cfg.probes_per_cycle >= 0.5)
        trace.hits["taint"].append(detectors.taint(store, ATTACK_ROOT))
        trace.hits["benign"].append(detectors.semantic(visible, BENIGN_PROBE_TERMS))
        if record_snapshots:
            trace.snapshots.append([
                NoteSnapshot(s.prov.source, s.prov.trust.name.lower(), s.text, it.quarantined)
                for it, s in store.sentences()
                if any(l.startswith(ATTACK_ROOT) for l in s.lineage)
            ])

    measure(0)
    for cycle in range(1, cfg.cycles + 1):
        for it in benign_items(rng, cfg.benign_per_cycle, cycle):
            ingest(it, cycle)
        for _ in range(cfg.interactions_per_cycle):
            topic = cfg.probe_topic if rng.random() < cfg.payment_query_rate else rng.choice(list(TOPICS))
            resp = agent.respond(store.visible(), topic, rng, defense)
            wb = writeback(resp, topic, cycle, defense)
            if wb is not None:
                ingest(wb, cycle)
        defense.before_consolidation(store, cycle, rng, verifier)
        consolidator.consolidate(store, cycle, rng, defense)
        measure(cycle)
    return trace


@dataclass
class DetectorSummary:
    curve: list[float]
    ci: list[tuple[float, float]]
    half_life: HalfLife
    km_half_life: HalfLife
    fit: DecayFit
    bootstrap: tuple[float, float, float]

    def to_dict(self) -> dict:
        def num(x: float) -> float | str:
            return x if math.isfinite(x) else ("inf" if x > 0 else "nan")
        return {
            "curve": self.curve,
            "ci95": self.ci,
            "half_life": asdict(self.half_life),
            "km_half_life": asdict(self.km_half_life),
            "fit": self.fit.to_dict(),
            "bootstrap_ci95": [num(self.bootstrap[0]), num(self.bootstrap[1])],
            "bootstrap_censored_fraction": self.bootstrap[2],
        }


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    detectors: dict[str, DetectorSummary]
    judge: JudgeCalibration
    behavioral_observed: list[float]
    behavioral_corrected: list[float] | None
    corrected_half_life: HalfLife | None

    def to_dict(self) -> dict:
        return {
            "config": asdict(self.config),
            "detectors": {k: v.to_dict() for k, v in self.detectors.items()},
            "judge": asdict(self.judge) | {"informative": self.judge.informative},
            "behavioral_observed": self.behavioral_observed,
            "behavioral_corrected": self.behavioral_corrected,
            "behavioral_corrected_half_life": asdict(self.corrected_half_life) if self.corrected_half_life else None,
        }

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), **kw)


def summarize(matrix: list[list[bool]], seed: int = 0) -> DetectorSummary:
    curve = prevalence(matrix)
    n = len(matrix)
    return DetectorSummary(
        curve,
        [wilson(round(p * n), n) for p in curve],
        prevalence_half_life(curve),
        km_half_life(matrix),
        fit_decay(curve),
        bootstrap_half_life(matrix, seed=seed),
    )


def run_experiment(cfg: ExperimentConfig, *, consolidator_factory: Callable[[], Consolidator] | None = None,
                   agent: Agent | None = None, judge: Judge | None = None,
                   progress: Callable[[int, int], None] | None = None) -> ExperimentResult:
    cfg.validate()
    payload = PAYLOADS[cfg.payload]
    if judge is None:
        judge = RuleJudge()
        if cfg.judge_fpr or cfg.judge_fnr:
            judge = NoisyJudge(judge, cfg.judge_fpr, cfg.judge_fnr, seed=cfg.seed)

    traces = []
    for t in range(cfg.trials):
        traces.append(run_trial(cfg, t, consolidator=consolidator_factory() if consolidator_factory else None,
                                agent=agent, judge=judge))
        if progress:
            progress(t + 1, cfg.trials)

    summaries = {d: summarize([tr.hits[d] for tr in traces], cfg.seed) for d in DETECTORS}

    n_cyc = cfg.cycles + 1
    observed = [sum(tr.behavioral_raw[c] for tr in traces) / len(traces) for c in range(n_cyc)]
    cal = calibrate(judge, payload, *calibration_set(payload, max(1, cfg.calibration_n),
                                                     random.Random(f"{cfg.seed}:calibration")))
    corrected = corrected_hl = None
    if cal.informative:
        corrected = [rogan_gladen(p, cal.sensitivity, cal.specificity) for p in observed]
        corrected_hl = prevalence_half_life(corrected)
    return ExperimentResult(cfg, summaries, cal, observed, corrected, corrected_hl)


def run_matrix(base: ExperimentConfig, payloads: list[str], defenses: list[str],
               channels: list[str] | None = None, **kw) -> list[ExperimentResult]:
    from dataclasses import replace
    out = []
    for ch in channels or [base.channel]:
        for p in payloads:
            for d in defenses:
                out.append(run_experiment(replace(base, payload=p, defense=d, channel=ch), **kw))
    return out
