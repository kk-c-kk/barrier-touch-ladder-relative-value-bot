"""One-touch barrier pricing for crypto touch ladders.

A touch-ladder rung ("Will BTC *hit* $80k in June?") pays 1 if the underlying ever
trades through the barrier B before expiry, else 0. Under GBM with log-drift `nu`
and vol `sigma`, the probability the barrier is touched before horizon T is the
first-passage law (Reiner-Rubinstein form):

    upper barrier  m = ln(B/S) > 0:
        P = Phi((nu*T - m)/(sigma*sqrt(T)))
            + exp(2*nu*m/sigma^2) * Phi((-nu*T - m)/(sigma*sqrt(T)))

    lower barrier  m = ln(B/S) < 0:
        P = Phi((m - nu*T)/(sigma*sqrt(T)))
            + exp(2*nu*m/sigma^2) * Phi((m + nu*T)/(sigma*sqrt(T)))

For a martingale spot (no funding/drift) `nu = -sigma^2/2`. With `nu = 0` both
collapse to the textbook `2*Phi(-d)`, `d = |m|/(sigma*sqrt(T))` — useful as a
documented sanity reference but slightly mispriced for real spot.

The risk-neutral price equals this probability (a one-touch pays 1 on touch), so
`prob_touch` doubles as fair value. `implied_vol` inverts a market price back to
the vol it implies — that is what we compare across the ladder to see the smile.

All `sigma` are annualized; `T` is in YEARS. Use `years_remaining()` to convert
from seconds-to-expiry.
"""
from __future__ import annotations

import math

_SECONDS_PER_YEAR = 365.25 * 24 * 3600


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def years_remaining(seconds_left: float) -> float:
    """Seconds-to-expiry -> year fraction (clamped at 0)."""
    return max(seconds_left, 0.0) / _SECONDS_PER_YEAR


def prob_touch(
    spot: float,
    barrier: float,
    sigma: float,
    T: float,
    *,
    drift: float | None = None,
    already_touched: bool = False,
) -> float:
    """Probability the barrier is touched before T (== one-touch fair value), 0..1.

    Parameters
    ----------
    spot, barrier : current price and the touch level (same units).
    sigma         : annualized vol of log returns (>0).
    T             : time to expiry in YEARS.
    drift         : log-price drift `nu`. Defaults to -sigma^2/2 (martingale spot).
                    Pass 0.0 for the textbook driftless `2*Phi(-d)` convention.
    already_touched : if True the rung is settled in-the-money -> returns 1.0.
    """
    if already_touched:
        return 1.0
    if spot <= 0 or barrier <= 0:
        raise ValueError("spot and barrier must be positive")
    if barrier == spot:
        return 1.0  # already at the barrier
    # Expired or no vol: touched iff the barrier is already on the wrong side,
    # which `already_touched` would have caught -> otherwise it cannot touch.
    if T <= 0 or sigma <= 0:
        return 0.0

    nu = -0.5 * sigma * sigma if drift is None else drift
    m = math.log(barrier / spot)
    s = sigma * math.sqrt(T)
    # exp(2*nu*m/sigma^2) can overflow for deep barriers; guard it.
    try:
        refl = math.exp(2.0 * nu * m / (sigma * sigma))
    except OverflowError:
        refl = math.inf

    if m > 0:  # upper barrier
        p = _phi((nu * T - m) / s) + refl * _phi((-nu * T - m) / s)
    else:      # lower barrier
        p = _phi((m - nu * T) / s) + refl * _phi((m + nu * T) / s)

    return min(max(p, 0.0), 1.0)


def implied_vol(
    price: float,
    spot: float,
    barrier: float,
    T: float,
    *,
    drift_uses_iv: bool = True,
    lo: float = 1e-4,
    hi: float = 5.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Vol that reprices a one-touch to `price`, by bisection. None if no solution.

    `prob_touch` is monotincreasing in sigma for a fixed barrier, so bisection is
    safe. With `drift_uses_iv=True` the martingale drift `nu=-sigma^2/2` moves with
    the trial sigma (self-consistent); set False to hold `nu=0` (pure 2*Phi(-d)).

    Returns None when the price is outside the model's attainable range at the
    bracket ends (e.g. a tail priced above what even sigma=hi can justify), which is
    itself a signal — the market is pricing in something the diffusion can't.
    """
    if not (0.0 < price < 1.0):
        # 0 or 1 carry no vol information (settled / certain).
        return None

    def model(sig: float) -> float:
        drift = None if drift_uses_iv else 0.0
        return prob_touch(spot, barrier, sig, T, drift=drift)

    f_lo = model(lo) - price
    f_hi = model(hi) - price
    if f_lo > 0 or f_hi < 0:
        return None  # price not bracketed by [lo, hi]

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = model(mid) - price
        if abs(f_mid) < tol:
            return mid
        if f_mid < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
