import math

import pytest

from halflife.stats import (bootstrap_half_life, fit_decay, km_curve, km_half_life, prevalence,
                            prevalence_half_life, rogan_gladen, wilson)


def test_prevalence_basic_and_ragged():
    assert prevalence([[True, False], [True, True]]) == [1.0, 0.5]
    assert prevalence([]) == []
    with pytest.raises(ValueError):
        prevalence([[True], [True, False]])


def test_half_life_interpolates():
    hl = prevalence_half_life([1.0, 0.8, 0.6, 0.4, 0.2])
    assert hl.status == "observed" and hl.value == pytest.approx(2.5)


def test_half_life_counts_reaching_exactly_one_half():
    # t½ is defined as the cycle survival *drops to* 0.5
    assert prevalence_half_life([1.0, 0.5, 0.5, 0.49]).value == pytest.approx(1.0)
    # ...but a curve that only ever touches 0.5 was never established
    assert prevalence_half_life([0.5, 0.5, 0.2]).status == "never_established"


def test_half_life_censored_and_never_established_and_empty():
    assert prevalence_half_life([1.0, 0.9, 0.8]).status == "censored"
    assert str(prevalence_half_life([1.0, 0.9, 0.8])) == ">2"
    assert prevalence_half_life([0.1, 0.2, 0.3]).status == "never_established"
    assert prevalence_half_life([]).status == "empty"
    assert prevalence_half_life([1.0]).status == "censored"  # zero cycles run


def test_half_life_late_establishment():
    # Fragmented payloads start dormant and are assembled later.
    hl = prevalence_half_life([0.0, 0.2, 0.8, 0.9, 0.3])
    assert hl.status == "observed" and 3 < hl.value < 4


def test_half_life_immediate_drop():
    hl = prevalence_half_life([1.0, 0.0])
    assert hl.value == pytest.approx(0.5)


def test_sort_key_orders_statuses():
    vals = [prevalence_half_life(c) for c in ([1, 0.9], [1, 0], [0, 0])]
    assert sorted(vals, key=lambda h: h.sort_key())[-1].status == "censored"


def test_wilson_bounds():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0 < hi < 0.35
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo > 0.65


def test_km_absorbing_deaths():
    m = [[True, True, False, False], [True, False, False, False],
         [True, True, True, True], [False, False, False, False]]
    # only the 3 trials alive at 0 are at risk
    assert km_curve(m) == pytest.approx([1.0, 2 / 3, 1 / 3, 1 / 3])
    assert km_half_life(m).status == "observed"


def test_km_flicker_counts_first_death():
    m = [[True, False, True, True]] * 4
    assert km_curve(m)[1] == 0.0


def test_km_nobody_alive():
    assert km_half_life([[False, True]]).status == "never_established"
    assert km_half_life([]).status == "never_established"


def test_fit_recovers_exponential():
    lam = 0.2
    fit = fit_decay([math.exp(-lam * n) for n in range(40)])
    assert fit.rate == pytest.approx(lam, rel=0.06)
    assert fit.floor == pytest.approx(0.0, abs=0.02)
    assert fit.half_life() == pytest.approx(math.log(2) / lam, rel=0.06)


def test_fit_detects_persistent_floor():
    curve = [0.7 + 0.3 * math.exp(-0.5 * n) for n in range(30)]
    fit = fit_decay(curve)
    assert fit.floor == pytest.approx(0.7, abs=0.03)
    assert math.isinf(fit.half_life())
    assert fit.to_dict()["half_life"] is None and fit.to_dict()["half_life_status"] == "infinite"


def test_fit_starts_at_peak_and_handles_degenerate():
    fit = fit_decay([0.0, 0.0, 1.0, 0.5, 0.25, 0.125])
    assert fit.start == 2 and fit.half_life() == pytest.approx(3.0, rel=0.05)
    assert math.isnan(fit_decay([0.2, 0.1]).half_life())
    assert fit_decay([]).s0 == 0.0
    assert fit_decay([0.0, 0.0]).rate == 0.0


def test_bootstrap_ci_contains_point_and_censoring():
    m = [[True] * 5 + [False] * 5 for _ in range(10)] + [[True] * 3 + [False] * 7 for _ in range(10)]
    lo, hi, cens = bootstrap_half_life(m, b=100)
    pt = prevalence_half_life(prevalence(m)).value
    assert lo <= pt <= hi and cens == 0
    lo, hi, cens = bootstrap_half_life([[True] * 4] * 5, b=50)
    assert math.isinf(lo) and cens == 1.0
    assert all(math.isnan(x) for x in bootstrap_half_life([]))


def test_bootstrap_deterministic():
    m = [[True, i % 2 == 0, False] for i in range(9)]
    assert bootstrap_half_life(m, seed=3) == bootstrap_half_life(m, seed=3)


@pytest.mark.parametrize("p,se,sp", [(0.3, 0.9, 0.95), (0.0, 0.8, 0.8), (1.0, 0.7, 0.99)])
def test_rogan_gladen_inverts_misclassification(p, se, sp):
    observed = se * p + (1 - sp) * (1 - p)
    assert rogan_gladen(observed, se, sp) == pytest.approx(p)


def test_rogan_gladen_clips_and_rejects_useless_judge():
    assert rogan_gladen(0.0, 0.9, 0.8) == 0.0  # would be negative
    assert rogan_gladen(1.0, 0.7, 0.9) == 1.0  # would exceed 1
    with pytest.raises(ValueError, match="uninformative"):
        rogan_gladen(0.5, 0.5, 0.5)
    with pytest.raises(ValueError):
        rogan_gladen(0.5, 0.3, 0.4)  # worse than random
