import pytest

from halflife.cli import main
from halflife.compare import compare, holm, paired, trial_auc
from halflife.experiment import ExperimentConfig, run_experiment
from halflife.sweep import CLAIMS, Claim, apply_param, format_sweep, run_sweep


def test_trial_auc():
    assert trial_auc([[True, False, True, False], []]) == [0.5, 0.0]


def test_paired_identical_and_strong_difference():
    same = paired([0.5] * 20, [0.5] * 20)
    assert same.mean_diff == 0 and same.p_value == 1.0 and same.direction() == 0
    strong = paired([0.9] * 30, [0.1] * 30)
    assert strong.mean_diff == pytest.approx(0.8) and strong.p_value < 0.01 and strong.direction() == 1
    assert strong.ci[0] <= 0.8 <= strong.ci[1]
    assert paired([0.1] * 30, [0.9] * 30).direction() == -1


def test_paired_noise_is_not_significant():
    import random
    r = random.Random(0)
    a = [r.random() for _ in range(40)]
    b = [x + r.choice([-0.01, 0.01]) for x in a]
    assert paired(a, b, seed=1).p_value > 0.05


def test_paired_validation():
    with pytest.raises(ValueError):
        paired([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        paired([], [])


def test_holm_known_values_and_order():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5]) == [0.5]
    assert holm([]) == []
    assert all(q <= 1 for q in holm([0.9, 0.8, 0.7]))


def test_compare_results_and_guards():
    base = ExperimentConfig(trials=20, cycles=10)
    on = run_experiment(base)
    off = run_experiment(ExperimentConfig(trials=20, cycles=10, interactions_per_cycle=0))
    assert compare(on, off).mean_diff > 0
    assert compare(on, on, "behavioral", "literal").mean_diff > 0   # two detectors, same run
    with pytest.raises(ValueError, match="same seed"):
        compare(on, run_experiment(ExperimentConfig(trials=20, cycles=10, seed=1)))
    on.per_trial = None
    with pytest.raises(ValueError):
        compare(on, off)


def test_apply_param_routes_and_types():
    cfg = ExperimentConfig()
    assert apply_param(cfg, "obey_prob", 0.3).consolidator == {"obey_prob": 0.3}
    assert apply_param(cfg, "capacity", 12.0).capacity == 12
    assert isinstance(apply_param(cfg, "capacity", 12.0).capacity, int)
    assert apply_param(cfg, "agent_leak", 0.5).agent_leak == 0.5
    with pytest.raises(ValueError, match="unknown sweep parameter"):
        apply_param(cfg, "payload", 1)
    with pytest.raises(ValueError):
        apply_param(cfg, "nonsense", 1)


def test_run_sweep_small_and_format():
    claims = [c for c in CLAIMS if c.name in ("writeback", "zombie-text")]
    s = run_sweep(ExperimentConfig(trials=15, cycles=8), "obey_prob", [0.0, 1.0], claims)
    assert len(s.verdicts) == 4
    assert s.robustness("writeback") in ("robust", "fragile", "not supported", "flips")
    text = format_sweep(s)
    assert "obey_prob=0" in text and "writeback" in text
    d = s.to_dict()
    assert d["claims"]["zombie-text"]["by_value"][1]["value"] == 1.0
    with pytest.raises(ValueError):
        run_sweep(ExperimentConfig(), "obey_prob", [])


def test_inconclusive_claim_reports_honestly():
    # comparing a configuration with itself can never "hold"
    c = Claim("self", "a run beats itself", ("plain", "none", "tool_result", True),
              ("plain", "none", "tool_result", True))
    s = run_sweep(ExperimentConfig(trials=8, cycles=5), "obey_prob", [0.5], [c])
    assert s.verdicts[0].outcome == "inconclusive" and s.robustness("self") == "not supported"


def test_cli_sweep(capsys, tmp_path):
    out = tmp_path / "s.json"
    assert main(["sweep", "--param", "agent_leak", "--values", "0,0.5", "--claims", "laundering",
                 "--trials", "10", "--cycles", "6", "--json", str(out)]) == 0
    assert "laundering" in capsys.readouterr().out and out.exists()
    assert main(["sweep", "--param", "agent_leak", "--values", "x", "--trials", "2", "--cycles", "2"]) == 2
    assert main(["sweep", "--param", "agent_leak", "--values", "0", "--claims", "nope"]) == 2
