"""Randomized invariant tests: properties that must hold for any input, checked over
many seeded random cases (stdlib only, deterministic, so failures reproduce)."""

import math
import random

import pytest

from halflife.compare import holm, paired
from halflife.consolidator import SimulatedConsolidator
from halflife.defenses import DEFENSES, get_defense
from halflife.experiment import ExperimentConfig, run_trial
from halflife.memory import MemoryStore, Provenance, Trust
from halflife.payloads import CHANNELS, PAYLOADS, benign_items
from halflife.stats import fit_decay, km_curve, prevalence_half_life
from halflife.text import (SYNONYM_GROUPS, abstract_specifics, canonical_tokens, drop_last_clause, normalize,
                           paraphrase, urls)

CASES = 60
URLS = ["https://pay-verify-7731.example/checkout", "https://billing.acme-corp.example/account?x=1",
        "http://a-b.example/p/q"]
WORDS = [w for g in SYNONYM_GROUPS for w in g] + ["the", "user", "notes", "Lisbon", "14", "gate", "B12"]


def random_text(r: random.Random) -> str:
    parts = [r.choice(WORDS) for _ in range(r.randint(1, 14))]
    if r.random() < 0.6:
        parts.insert(r.randrange(len(parts) + 1), r.choice(URLS))
    sep = r.choice([" ", ", ", " — ", "; "])
    return sep.join(parts) + r.choice([".", "", "!"])


# ----------------------------------------------------------------------- text

@pytest.mark.parametrize("seed", range(CASES))
def test_text_operations_preserve_or_remove_urls_never_corrupt_them(seed):
    r = random.Random(seed)
    t = random_text(r)
    original = set(urls(t))
    assert set(urls(paraphrase(t, r, rate=1.0))) == original
    assert set(urls(drop_last_clause(t))) <= original
    assert urls(abstract_specifics(t)) == []
    assert canonical_tokens(paraphrase(t, r, rate=1.0)) == canonical_tokens(t)
    assert normalize(normalize(t)) == normalize(t)


# ---------------------------------------------------------------------- stats

def random_curve(r: random.Random) -> list[float]:
    n = r.randint(1, 40)
    v, out = r.random(), []
    for _ in range(n):
        v = min(1.0, max(0.0, v + r.uniform(-0.3, 0.2)))
        out.append(round(v, 3) if r.random() < 0.9 else 0.5)
    return out


@pytest.mark.parametrize("seed", range(CASES))
def test_half_life_is_consistent_with_its_curve(seed):
    c = random_curve(random.Random(seed))
    hl = prevalence_half_life(c)
    if hl.status == "observed":
        i = math.ceil(hl.value)
        start = next(k for k, v in enumerate(c) if v > 0.5)
        assert start < i < len(c)
        assert c[i] <= 0.5 < c[i - 1]                      # interpolated between the crossing points
        assert all(v > 0.5 for v in c[start:i])           # and it is the *first* crossing
        assert i - 1 <= hl.value <= i
    elif hl.status == "censored":
        start = next(i for i, v in enumerate(c) if v > 0.5)
        assert all(v > 0.5 for v in c[start:])
    else:
        assert all(v <= 0.5 for v in c)


@pytest.mark.parametrize("seed", range(CASES))
def test_km_is_monotone_and_fit_floor_is_bounded(seed):
    r = random.Random(seed)
    m = [[r.random() < 0.7 for _ in range(12)] for _ in range(r.randint(1, 20))]
    km = km_curve(m)
    assert all(a >= b for a, b in zip(km, km[1:], strict=False))
    assert all(0 <= v <= 1 for v in km)
    fit = fit_decay(random_curve(r))
    assert 0 <= fit.floor <= fit.s0 + 1e-12 and fit.rate >= 0


@pytest.mark.parametrize("seed", range(20))
def test_holm_dominates_raw_and_paired_is_antisymmetric(seed):
    r = random.Random(seed)
    ps = [r.random() for _ in range(r.randint(1, 15))]
    adj = holm(ps)
    assert all(q >= p and q <= 1 for p, q in zip(ps, adj, strict=True))
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    assert all(adj[order[k]] <= adj[order[k + 1]] for k in range(len(ps) - 1))
    a = [r.random() for _ in range(25)]
    b = [r.random() for _ in range(25)]
    ab, ba = paired(a, b, seed=1), paired(b, a, seed=1)
    assert ab.mean_diff == pytest.approx(-ba.mean_diff)
    assert ab.ci[0] <= ab.mean_diff <= ab.ci[1]


# --------------------------------------------------------------- consolidator

@pytest.mark.parametrize("seed", range(CASES))
def test_consolidation_invariants(seed):
    r = random.Random(seed)
    cons = SimulatedConsolidator(merge_prob=r.random(), paraphrase_prob=r.random(), clause_drop_prob=r.random() / 2,
                                 abstract_prob=r.random() / 4, obey_prob=r.random(), misc_attach_prob=r.random())
    defense = get_defense(r.choice(sorted(DEFENSES)))
    store = MemoryStore(capacity=r.randint(0, 25))
    for i, it in enumerate(benign_items(r, r.randint(0, 30), 0)):
        sent = it.sentences[0]
        sent.prov = Provenance("src", r.choice(list(Trust)))     # mixed trust within one memory
        sent.lineage = frozenset({f"root{i}"})
        if r.random() < 0.2:
            it.quarantined = True
        store.add(it)
    roots = frozenset().union(*(s.lineage for _, s in store.sentences())) if store.items else frozenset()
    root_trust = {next(iter(s.lineage)): s.prov.trust for _, s in store.sentences()}
    held = [it for it in store.items if it.quarantined]
    held_text = [[s.text for s in it.sentences] for it in held]
    cons.consolidate(store, 1, r, defense)
    active = store.sentences(include_quarantined=False)
    assert len(active) <= store.capacity                          # capacity respected
    assert all(it.sentences for it in store.items)                # no empty items left behind
    assert [[s.text for s in it.sentences] for it in store.items if it.quarantined] == held_text  # untouched
    after = frozenset().union(*(s.lineage for _, s in store.sentences())) if store.items else frozenset()
    assert after <= roots                                          # lineage is never invented
    if defense.preserve_attribution:
        # a note is never more trusted than the least trusted note it was built from
        for _, sent in store.sentences():
            assert sent.prov.trust <= min(root_trust[x] for x in sent.lineage)


# ------------------------------------------------------------------ whole run

def random_config(r: random.Random) -> ExperimentConfig:
    return ExperimentConfig(
        payload=r.choice(sorted(PAYLOADS)), channel=r.choice(sorted(CHANNELS)), defense=r.choice(sorted(DEFENSES)),
        cycles=r.randint(0, 8), trials=1, seed=r.randint(0, 10**6), capacity=r.randint(0, 40),
        warmup_items=r.randint(0, 25), benign_per_cycle=r.randint(0, 5), interactions_per_cycle=r.randint(0, 4),
        payment_query_rate=r.random(), probes_per_cycle=r.randint(1, 3), agent_leak=r.random(),
        verify_benign=r.random(), verify_malicious=r.random(), reexpose_every=r.choice([0, 0, 1, 3]),
        consolidator={"merge_prob": r.random(), "obey_prob": r.random(), "paraphrase_prob": r.random()},
    )


@pytest.mark.parametrize("seed", range(CASES))
def test_trial_invariants(seed):
    cfg = random_config(random.Random(seed))
    cfg.validate()
    tr = run_trial(cfg, 0, record_snapshots=True)
    n = cfg.cycles + 1
    for name, hits in tr.hits.items():
        assert len(hits) == n, name
    for c in range(n):
        # Only the injection carries the attacker's exact text or link, so a literal match or
        # an agent that routes to the attacker implies a surviving descendant of the injection.
        if tr.hits["literal"][c] or tr.hits["behavioral"][c]:
            assert tr.hits["taint"][c], (cfg, c)
        assert bool(tr.snapshots[c]) == tr.hits["taint"][c]
        assert 0 <= tr.behavioral_true[c] <= 1


@pytest.mark.parametrize("seed", range(10))
def test_trial_is_a_pure_function_of_config_and_index(seed):
    cfg = random_config(random.Random(seed))
    a = run_trial(cfg, 3)
    b = run_trial(cfg, 3)
    assert a.to_dict() == b.to_dict()
