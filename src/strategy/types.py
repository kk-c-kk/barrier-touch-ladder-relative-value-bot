"""Shared dataclasses for signal generation, sizing and hedging."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RungQuote:
    """A live, priced view of one ladder rung — the input to signal generation."""
    rung_slug: str
    asset: str
    strike: float
    expiry: int | None          # unix
    t_remaining_s: int | None
    spot: float
    yes_ask: float | None       # price to BUY the touched leg (real executable)
    yes_bid: float | None       # price you RECEIVE to sell the touched leg
    realized_vol: float | None
    implied_vol: float | None   # vol implied by yes_ask
    model_fair: float | None    # prob_touch at realized vol
    yes_token: str
    no_token: str


@dataclass
class TradeIntent:
    """A proposed trade. `side` is the directional view on the TOUCH:
      BUY  = long touch  -> buy YES at yes_ask  (price = the ask we pay)
      SELL = short touch -> sell YES at yes_bid (price = the bid we receive;
             on PM this is executed by holding NO / splitting collateral)
    `size_shares` is always >= 0; direction lives in `side`.
    """
    rung_slug: str
    asset: str
    strike: float
    expiry: int | None
    side: str                   # 'BUY' | 'SELL'
    token_id: str               # yes_token (the leg we mark against)
    price: float                # executable price for this side
    size_shares: float
    edge_prob: float            # signed model_fair - price, in probability units
    reason: str
    pair_id: str | None = None  # links the two legs of a smile-RV pair


@dataclass
class Position:
    """An open paper position. `size_shares` is SIGNED: + long touch, - short."""
    rung_slug: str
    asset: str
    strike: float
    expiry: int | None
    size_shares: float
    entry_price: float
    yes_token: str
