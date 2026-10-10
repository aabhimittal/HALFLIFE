import json
import math
from dataclasses import replace

import pytest

from halflife.consolidator import CallableConsolidator
from halflife.experiment import DETECTORS, ExperimentConfig, run_experiment, run_matrix, run_trial


def test_deterministic_given_seed(small_cfg):
    a = run_experiment(small_cfg).to_dict()
    b = run_experiment(small_cfg).to_dict()
    assert a == b
    c = run_experiment(replace(small_cfg, seed=8)).to_dict()
    assert a["detectors"] != c["detectors"]


def test_curve_shapes_and_cycle0(small_cfg):
    r = run_experiment(small_cfg)
    for d in DETECTORS:
        assert len(r.detectors[d].curve) == small_cfg.cycles + 1
        assert all(0 <= p <= 1 for p in r.detectors[d].curve)
    # straight after injection, with no defense, every content detector fires
    for d in ("literal", "semantic", "behavioral", "taint", "benign"):
        assert r.detectors[d].curve[0] == 1.0


def test_zero_cycles_and_single_trial():
    r = run_experiment(ExperimentConfig(cycles=0, trials=1))
    assert r.detectors["literal"].curve == [1.0]
    assert r.detectors["literal"].half_life.status == "censored"


@pytest.mark.parametrize("bad", [
    dict(payload="nope"), dict(channel="carrier_pigeon"), dict(defense="magic"), dict(trials=0),
    dict(cycles=-1), dict(judge_fpr=1.2), dict(probe_topic="astrology"), dict(probes_per_cycle=0),
    dict(consolidator={"not_a_knob": 1}), dict(capacity=-5),
])
def test_invalid_configs_raise(bad):
    with pytest.raises(ValueError):
        run_experiment(ExperimentConfig(**({"trials": 1, "cycles": 1} | bad)))


def test_ttl_hides_attack_and_benign_at_cycle0(small_cfg):
    r = run_experiment(replace(small_cfg, defense="ttl"))
    assert r.detectors["behavioral"].curve[0] == 0.0
    assert r.detectors["benign"].curve[0] == 0.0          # utility cost is visible
    assert r.detectors["literal"].curve[0] == 1.0         # still stored, just not retrievable
    assert max(r.detectors["benign"].curve[1:4]) > 0.5     # users verify benign facts quickly


def test_ttl_with_rubber_stamping_user_lets_attack_through(small_cfg):
    strict = run_experiment(replace(small_cfg, defense="ttl", verify_malicious=0.0))
    sloppy = run_experiment(replace(small_cfg, defense="ttl", verify_malicious=1.0))
    assert sum(strict.detectors["behavioral"].curve) < sum(sloppy.detectors["behavioral"].curve)


def test_provenance_laundering_shows_up(small_cfg):
    cfg = replace(small_cfg, trials=40, payload="plain")
    tags = run_experiment(replace(cfg, defense="provenance")).detectors["behavioral"].curve
    attr = run_experiment(replace(cfg, defense="attributed")).detectors["behavioral"].curve
    assert tags[0] < 0.3 and attr[0] < 0.3          # tags work at ingest
    assert max(tags[1:4]) > 0.5 > max(attr[1:4])    # ...until a naive consolidation relabels them


def test_zombie_outlives_plain_literal(small_cfg):
    cfg = replace(small_cfg, trials=40, cycles=15)
    z = sum(run_experiment(replace(cfg, payload="zombie")).detectors["literal"].curve)
    p = sum(run_experiment(replace(cfg, payload="plain")).detectors["literal"].curve)
    assert z > p


def test_writeback_drives_behavioral_persistence(small_cfg):
    cfg = replace(small_cfg, trials=40, cycles=20)
    on = sum(run_experiment(cfg).detectors["behavioral"].curve)
    off = sum(run_experiment(replace(cfg, interactions_per_cycle=0)).detectors["behavioral"].curve)
    assert on > off


def test_fragmented_starts_dormant(small_cfg):
    r = run_experiment(replace(small_cfg, payload="fragmented"))
    assert r.detectors["behavioral"].curve[0] == 0.0
    assert r.detectors["semantic"].curve[0] == 0.0
    assert r.detectors["taint"].curve[0] == 1.0


def test_user_channel_bypasses_provenance(small_cfg):
    """A pasted email arrives as USER trust: provenance-based defenses can't see it."""
    r = run_experiment(replace(small_cfg, channel="user_message", defense="attributed+ttl"))
    assert r.detectors["behavioral"].curve[0] == 1.0


def test_tiny_capacity_forgets_everything():
    r = run_experiment(ExperimentConfig(capacity=0, trials=3, cycles=3))
    assert r.detectors["taint"].curve[1:] == [0.0, 0.0, 0.0]


def test_empty_warm_memory_and_no_traffic():
    r = run_experiment(ExperimentConfig(warmup_items=0, benign_per_cycle=0, interactions_per_cycle=0,
                                        trials=5, cycles=5))
    assert r.detectors["behavioral"].curve[0] == 1.0  # nothing to compete with


def test_noisy_judge_correction_reduces_bias():
    base = ExperimentConfig(trials=150, cycles=15, seed=3)
    r = run_experiment(replace(base, judge_fpr=0.2, judge_fnr=0.2))
    true = r.detectors["behavioral"].curve
    err = lambda c: sum(abs(a - b) for a, b in zip(c, true, strict=True)) / len(true)
    assert r.judge.informative
    assert err(r.behavioral_corrected) < err(r.behavioral_observed)


def test_uninformative_judge_skips_correction():
    r = run_experiment(ExperimentConfig(trials=3, cycles=2, judge_fpr=0.6, judge_fnr=0.6))
    assert not r.judge.informative
    assert r.behavioral_corrected is None and r.corrected_half_life is None
    json.loads(r.to_json())


def test_multiple_probes_per_cycle():
    r = run_experiment(ExperimentConfig(trials=4, cycles=3, probes_per_cycle=5, defense="provenance"))
    assert len(r.behavioral_observed) == 4


def test_snapshots_track_only_attack_lineage(small_cfg):
    tr = run_trial(small_cfg, 0, record_snapshots=True)
    assert len(tr.snapshots) == small_cfg.cycles + 1
    notes = [str(n) for n in tr.snapshots[0]]
    assert notes and tr.snapshots[0][0].trust == "untrusted"
    assert all("pay-verify" in n or "preserve" in n or "Tool output" in n or "pointed" in n
               or "portal" in n or "payment" in n.lower() or "billing" in n.lower() or "checkout" in n.lower()
               for n in notes)


def test_json_roundtrip_has_no_nan_or_inf(small_cfg):
    out = run_experiment(replace(small_cfg, payload="zombie")).to_json()
    assert "NaN" not in out and "Infinity" not in out
    d = json.loads(out)
    assert set(d["detectors"]) == set(DETECTORS)


def test_progress_callback(small_cfg):
    seen = []
    run_experiment(replace(small_cfg, trials=3), progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_run_matrix_shape(small_cfg):
    rs = run_matrix(replace(small_cfg, trials=2, cycles=2), ["plain", "zombie"], ["none", "ttl"],
                    channels=["tool_result", "document"])
    assert len(rs) == 8
    assert {(r.config.channel, r.config.payload, r.config.defense) for r in rs} == {
        (c, p, d) for c in ("tool_result", "document") for p in ("plain", "zombie") for d in ("none", "ttl")}


def test_callable_consolidator_black_box_host():
    """A host that summarizes by keeping every other note; lineage reassigned by overlap."""
    calls = []
    def host(notes):
        calls.append(len(notes))
        return notes[::2] + ["", "   "]  # blank lines must be ignored
    r = run_experiment(ExperimentConfig(trials=2, cycles=4), consolidator_factory=lambda: CallableConsolidator(host))
    assert len(calls) == 8
    assert len(r.detectors["taint"].curve) == 5


def test_callable_consolidator_that_forgets_everything():
    r = run_experiment(ExperimentConfig(trials=2, cycles=3),
                       consolidator_factory=lambda: CallableConsolidator(lambda n: []))
    assert r.detectors["literal"].curve[1:] == [0.0, 0.0, 0.0]


def test_custom_agent_and_judge_plug_in():
    from halflife.agent import Response
    class Refuser:
        def respond(self, items, topic, rng, defense):
            return Response("I can't help with that.")
    r = run_experiment(ExperimentConfig(trials=2, cycles=2), agent=Refuser(), judge=lambda resp, p: False)
    assert r.detectors["behavioral"].half_life.status == "never_established"
    assert not math.isnan(r.judge.specificity)
