"""Net delta hedge on a Hyperliquid perp.

A one-touch's value moves with spot, so an open book of touch positions carries a
spot delta. We hold a perp to neutralize it, leaving the bet we actually want:
short gamma/vega on the smile (the thing we're paid for). Multi-day holds are why
this hedge finally earns its keep (in the 5min market it was pointless).

Delta convention (worked through a concrete case in the tests):
  each YES share settles to $1, so its $-value ~= prob_touch and its spot delta is
  dProb/dSpot (units 1/$). For a signed book, the BTC-equivalent delta is

      book_delta_coins = sum_i  size_shares_i * dProb_i/dSpot

  A long-touch book (positive) gains as spot rises, so we SHORT that many coins:
  target perp qty = -book_delta_coins  (negative => short).

LIVE EXECUTION IS NOT WIRED. Placing/adjusting a real Hyperliquid perp needs the
account's signing keys and the exchange (order) endpoint; doing that blind, before
the edge is validated, is exactly the trap this project exists to avoid. `target_qty`
computes the hedge; `PaperHedge` records it; `LiveHedge` is a documented stub.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from src.pricing import prob_touch, years_remaining
from src.strategy.types import Position

log = logging.getLogger("hedge")


def position_delta(pos: Position, spot: float, sigma: float, t_remaining_s: float,
                   h_frac: float = 1e-4) -> float:
    """BTC-equivalent spot delta of one position (signed), by central difference."""
    if spot <= 0 or sigma <= 0 or t_remaining_s <= 0:
        return 0.0
    T = years_remaining(t_remaining_s)
    h = spot * h_frac
    up = prob_touch(spot + h, pos.strike, sigma, T)
    dn = prob_touch(spot - h, pos.strike, sigma, T)
    dprob_dspot = (up - dn) / (2 * h)
    return pos.size_shares * dprob_dspot


def book_delta(positions: list[Position], spot_of: Callable[[str], float | None],
               vol_of: Callable[[str], float | None], now: float) -> dict[str, float]:
    """Per-asset net BTC-equivalent delta of the whole open book."""
    out: dict[str, float] = {}
    for p in positions:
        spot = spot_of(p.asset)
        sigma = vol_of(p.asset)
        if spot is None or sigma is None or p.expiry is None:
            continue
        d = position_delta(p, spot, sigma, p.expiry - now)
        out[p.asset] = out.get(p.asset, 0.0) + d
    return out


def target_qty(positions: list[Position], spot_of, vol_of, now: float) -> dict[str, float]:
    """Per-asset perp qty that neutralizes the book (negative => short)."""
    return {a: -d for a, d in book_delta(positions, spot_of, vol_of, now).items()}


class PaperHedge:
    """Records the target hedge each rebalance; never touches the exchange."""
    def __init__(self):
        self.current: dict[str, float] = {}   # asset -> held perp qty

    def rebalance(self, positions: list[Position], spot_of, vol_of, now: float) -> dict[str, float]:
        target = target_qty(positions, spot_of, vol_of, now)
        for asset, q in target.items():
            prev = self.current.get(asset, 0.0)
            if abs(q - prev) > 1e-6:
                log.info("[paper] hedge %s: %.4f -> %.4f coins", asset, prev, q)
            self.current[asset] = q
        return target


class LiveHedge:
    """Stub. Wiring this needs the HL account key + the /exchange order endpoint and
    must only run after validation (and with explicit human sign-off)."""
    def rebalance(self, *args, **kwargs):
        raise NotImplementedError(
            "Live Hyperliquid hedging is intentionally not wired. Validate the edge "
            "against real CLOB asks first, then implement signed /exchange orders."
        )
