"""Property-based invariant tests (Hypothesis).

Every run searches new inputs; failures are shrunk to a minimal example and replayed
from the local example database. Run a deeper search with HYPOTHESIS_PROFILE=deep.
"""

import math

from hypothesis import given
from hypothesis import strategies as st

from halflife.compare import holm, paired
from halflife.consolidator import SimulatedConsolidator
from halflife.defenses import DEFENSES, get_defense
from halflife.experiment import ExperimentConfig, run_trial
from halflife.memory import MemoryStore, Provenance, Trust
from halflife.payloads import CHANNELS, PAYLOADS, benign_items
from halflife.stats import fit_decay, km_curve, prevalence_half_life
from halflife.text import (SYNONYM_GROUPS, abstract_specifics, canonical_tokens, drop_last_clause, normalize,
                           paraphrase, urls)

URLS = ["https://pay-verify-7731.example/checkout", "https://billing.acme-corp.example/account?x=1",
        "http://a-b.example/p/q"]
WORDS = [w for g in SYNONYM_GROUPS for w in g] + ["the", "user", "notes", "Lisbon", "14", "gate", "B12"]

prob = st.floats(0, 1, allow_nan=False)
sentence = st.builds(
    lambda parts, sep, end: sep.join(parts) + end,
    st.lists(st.sampled_from(WORDS + URLS), min_size=1, max_size=14),
    st.sampled_from([" ", ", ", " — ", "; "]),
    st.sampled_from([".", "", "!"]),
)
curve = st.lists(st.one_of(prob, st.just(0.5)), min_size=1, max_size=40)
seeded = st.randoms(use_true_random=False)


# ----------------------------------------------------------------------- text

@given(sentence, seeded)
def test_text_operations_preserve_or_remove_urls_never_corrupt_them(t, r):
    original = set(urls(t))
    assert set(urls(paraphrase(t, r, rate=1.0))) == original
    assert set(urls(drop_last_clause(t))) <= original
    assert urls(abstract_specifics(t)) == []
    assert canonical_tokens(paraphrase(t, r, rate=1.0)) == canonical_tokens(t)


@given(st.text())
def test_normalize_is_idempotent_on_any_unicode(t):
    assert normalize(normalize(t)) == normalize(t)


# ---------------------------------------------------------------------- stats

@given(curve)
def test_half_life_is_consistent_with_its_curve(c):
    hl = prevalence_half_life(c)
    if hl.status == "observed":
        i = math.ceil(hl.value)
        start = next(k for k, v in enumerate(c) if v > 0.5)
        assert start < i < len(c)
        assert c[i] <= 0.5 < c[i - 1]                      # interpolated between the crossing points
        assert all(v > 0.5 for v in c[start:i])           # and it is the *first* crossing
        assert i - 1 <= hl.value <= i
    elif hl.status == "censored":
        start = next(k for k, v in enumerate(c) if v > 0.5)
        assert all(v > 0.5 for v in c[start:])
    else:
        assert all(v <= 0.5 for v in c)


@given(st.lists(st.lists(st.booleans(), min_size=12, max_size=12), min_size=1, max_size=20), curve)
def test_km_is_monotone_and_fit_floor_is_bounded(m, c):
    km = km_curve(m)
    assert all(a >= b for a, b in zip(km, km[1:], strict=False))
    assert all(0 <= v <= 1 for v in km)
    fit = fit_decay(c)
    assert 0 <= fit.floor <= fit.s0 + 1e-12 and fit.rate >= 0


@given(st.lists(prob, min_size=1, max_size=15))
def test_holm_dominates_raw_and_preserves_order(ps):
    adj = holm(ps)
    assert all(p <= q <= 1 for p, q in zip(ps, adj, strict=True))
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    assert all(adj[order[k]] <= adj[order[k + 1]] for k in range(len(ps) - 1))


@given(st.lists(st.tuples(prob, prob), min_size=1, max_size=30))
def test_paired_is_antisymmetric_and_ci_contains_mean(pairs):
    a, b = [x for x, _ in pairs], [y for _, y in pairs]
    ab, ba = paired(a, b, seed=1, resamples=300), paired(b, a, seed=1, resamples=300)
    assert math.isclose(ab.mean_diff, -ba.mean_diff, abs_tol=1e-12)
    assert ab.ci[0] - 1e-12 <= ab.mean_diff <= ab.ci[1] + 1e-12
    assert 0 < ab.p_value <= 1


# --------------------------------------------------------------- consolidator

@given(seeded, st.integers(0, 25), st.integers(0, 30), st.sampled_from(sorted(DEFENSES)),
       st.tuples(prob, prob, prob, prob, prob, prob))
def test_consolidation_invariants(r, capacity, n_items, defense_name, p):
    cons = SimulatedConsolidator(merge_prob=p[0], paraphrase_prob=p[1], clause_drop_prob=p[2] / 2,
                                 abstract_prob=p[3] / 4, obey_prob=p[4], misc_attach_prob=p[5])
    defense = get_defense(defense_name)
    store = MemoryStore(capacity=capacity)
    for i, it in enumerate(benign_items(r, n_items, 0)):
        sent = it.sentences[0]
        sent.prov = Provenance("src", r.choice(list(Trust)))     # mixed trust within one memory
        sent.lineage = frozenset({f"root{i}"})
        if r.random() < 0.2:
            it.quarantined = True
        store.add(it)
    roots = frozenset().union(*(s.lineage for _, s in store.sentences())) if store.items else frozenset()
    root_trust = {next(iter(s.lineage)): s.prov.trust for _, s in store.sentences()}
    held_text = [[s.text for s in it.sentences] for it in store.items if it.quarantined]
    cons.consolidate(store, 1, r, defense)
    assert len(store.sentences(include_quarantined=False)) <= store.capacity   # capacity respected
    assert all(it.sentences for it in store.items)                             # no empty items
    assert [[s.text for s in it.sentences] for it in store.items if it.quarantined] == held_text
    after = frozenset().union(*(s.lineage for _, s in store.sentences())) if store.items else frozenset()
    assert after <= roots                                                       # lineage never invented
    if defense.preserve_attribution:
        # a note is never more trusted than the least trusted note it was built from
        for _, sent in store.sentences():
            assert sent.prov.trust <= min(root_trust[x] for x in sent.lineage)


# ------------------------------------------------------------------ whole run

configs = st.builds(
    ExperimentConfig,
    payload=st.sampled_from(sorted(PAYLOADS)), channel=st.sampled_from(sorted(CHANNELS)),
    defense=st.sampled_from(sorted(DEFENSES)), cycles=st.integers(0, 8), trials=st.just(1),
    seed=st.integers(0, 10**6), capacity=st.integers(0, 40), warmup_items=st.integers(0, 25),
    benign_per_cycle=st.integers(0, 5), interactions_per_cycle=st.integers(0, 4), payment_query_rate=prob,
    probes_per_cycle=st.integers(1, 3), agent_leak=prob, verify_benign=prob, verify_malicious=prob,
    reexpose_every=st.sampled_from([0, 0, 1, 3]),
    consolidator=st.fixed_dictionaries({"merge_prob": prob, "obey_prob": prob, "paraphrase_prob": prob}),
)


@given(configs)
def test_trial_invariants(cfg):
    cfg.validate()
    tr = run_trial(cfg, 0, record_snapshots=True)
    n = cfg.cycles + 1
    for name, hits in tr.hits.items():
        assert len(hits) == n, name
    for c in range(n):
        # Only the injection carries the attacker's exact text or link, so a literal match or
        # an agent that routes to the attacker implies a surviving descendant of the injection.
        if tr.hits["literal"][c] or tr.hits["behavioral"][c]:
            assert tr.hits["taint"][c], c
        assert bool(tr.snapshots[c]) == tr.hits["taint"][c]
        assert 0 <= tr.behavioral_true[c] <= 1


@given(configs, st.integers(0, 5))
def test_trial_is_a_pure_function_of_config_and_index(cfg, trial):
    assert run_trial(cfg, trial).to_dict() == run_trial(cfg, trial).to_dict()


# Targeted strategy: the trust rule only matters when near-duplicate notes with different trust
# levels are merged, which broad random memories rarely produce. Build exactly that situation.
NOTE_POOL = ["The user prefers aisle seats on flights.", "The user is vegetarian.",
             "The user pays invoices at the end of each month.", "The user's manager is called Wen."]


@given(st.lists(st.tuples(st.sampled_from(NOTE_POOL), st.sampled_from(list(Trust))), min_size=2, max_size=12),
       st.sampled_from(["attributed", "attributed+ttl"]), seeded)
def test_attributed_merge_never_raises_trust(notes, defense_name, r):
    from halflife.memory import MemoryItem, Sentence
    from halflife.payloads import topic_of
    store = MemoryStore(capacity=100)
    root_trust = {}
    for i, (text, trust) in enumerate(notes):
        root_trust[f"root{i}"] = trust
        store.add(MemoryItem([Sentence(text, Provenance("src", trust), 0.5, frozenset({f"root{i}"}))],
                             topic_of(text)))
    SimulatedConsolidator(merge_prob=1.0, paraphrase_prob=0, clause_drop_prob=0, abstract_prob=0,
                          forget_below=0, eviction_noise=0).consolidate(store, 1, r, get_defense(defense_name))
    for _, sent in store.sentences():
        assert sent.prov.trust <= min(root_trust[x] for x in sent.lineage)
