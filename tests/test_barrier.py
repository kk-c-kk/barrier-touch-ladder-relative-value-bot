"""Sanity / invariant checks for the one-touch barrier pricing."""
import math

import pytest

from src.pricing.barrier import implied_vol, prob_touch, years_remaining


def test_driftless_reduces_to_2phi():
    """With nu=0 the formula must equal the textbook 2*Phi(-d)."""
    S, B, sigma, T = 65000.0, 80000.0, 0.45, 30 / 365.25
    d = math.log(B / S) / (sigma * math.sqrt(T))
    expected = 2.0 * (0.5 * (1.0 + math.erf(-d / math.sqrt(2.0))))
    got = prob_touch(S, B, sigma, T, drift=0.0)
    assert got == pytest.approx(expected, rel=1e-9)


def test_symmetry_up_down_driftless():
    """A barrier the same log-distance above vs below spot has equal touch prob
    when driftless."""
    S, sigma, T = 65000.0, 0.5, 0.1
    up = S * math.exp(0.2)
    down = S * math.exp(-0.2)
    assert prob_touch(S, up, sigma, T, drift=0.0) == pytest.approx(
        prob_touch(S, down, sigma, T, drift=0.0), rel=1e-9
    )


def test_monotonic_in_vol():
    S, B, T = 65000.0, 85000.0, 0.08
    ps = [prob_touch(S, B, sig, T) for sig in (0.2, 0.4, 0.6, 0.9)]
    assert all(a < b for a, b in zip(ps, ps[1:]))


def test_monotonic_in_barrier_distance():
    """Farther upper barriers are less likely to be touched."""
    S, sigma, T = 65000.0, 0.5, 0.1
    ps = [prob_touch(S, B, sigma, T) for B in (70000, 80000, 90000, 110000)]
    assert all(a > b for a, b in zip(ps, ps[1:]))


def test_already_touched_and_at_barrier():
    assert prob_touch(65000, 80000, 0.5, 0.1, already_touched=True) == 1.0
    assert prob_touch(65000, 65000, 0.5, 0.1) == 1.0


def test_expired_or_zero_vol_untouched():
    assert prob_touch(65000, 80000, 0.5, 0.0) == 0.0
    assert prob_touch(65000, 80000, 0.0, 0.1) == 0.0


def test_bounded_unit_interval():
    for B in (40000, 65001, 200000):
        p = prob_touch(65000, B, 1.2, 0.5)
        assert 0.0 <= p <= 1.0


def test_implied_vol_roundtrip():
    S, B, T, sig = 65000.0, 82000.0, 0.06, 0.55
    price = prob_touch(S, B, sig, T)
    iv = implied_vol(price, S, B, T)
    assert iv == pytest.approx(sig, abs=1e-3)


def test_implied_vol_none_outside_bracket():
    # A settled price carries no vol info.
    assert implied_vol(0.0, 65000, 80000, 0.1) is None
    assert implied_vol(1.0, 65000, 80000, 0.1) is None


def test_years_remaining():
    assert years_remaining(0) == 0.0
    assert years_remaining(-5) == 0.0
    assert years_remaining(365.25 * 24 * 3600) == pytest.approx(1.0)
