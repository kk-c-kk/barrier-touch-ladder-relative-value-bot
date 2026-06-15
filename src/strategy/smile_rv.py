"""Smile relative-value signal generation.

The core trade (see README / project memory): the one-touch implied-vol smile on
these ladders is too STEEP — far-OTM wings imply 49-69% vol vs ~45% realized, while
the belly is if anything cheap. So:

  BUY  the underpriced belly  (ask-implied vol < realized  ->  model_fair > ask)
  SELL the rich wing          (ask-implied vol > realized  ->  model_fair < bid)

and pair a belly-buy with a wing-sell in the same (asset, expiry) group so the legs
offset collateral and tail risk. We act ONLY where the rung clears `min_edge` in
probability units — and, per memory, only treat a rung as tradeable when realized
vol and the near-money implied smile agree it's mispriced (the `require_rv` gate).

This module is pure: it turns priced RungQuotes into TradeIntents. It does not size
against capital limits (the trader does) and it does not execute.
"""
from __future__ import annotations

from collections import defaultdict

from .types import RungQuote, TradeIntent


class SmileRV:
    def __init__(self, min_edge: float = 0.03, base_size: float = 100.0,
                 max_size: float = 500.0, require_rv: bool = True):
        self.min_edge = min_edge        # min |model_fair - price| to act (prob units)
        self.base_size = base_size      # shares at exactly min_edge
        self.max_size = max_size        # cap per leg
        self.require_rv = require_rv     # require realized vol present to trust a signal

    def _size(self, edge: float) -> float:
        """Linear in edge above the threshold, capped."""
        mult = abs(edge) / self.min_edge
        return min(self.base_size * mult, self.max_size)

    def _candidate(self, q: RungQuote) -> TradeIntent | None:
        if q.model_fair is None or q.spot <= 0:
            return None
        if self.require_rv and q.realized_vol is None:
            return None

        # BUY edge uses the ask we'd pay; SELL edge uses the bid we'd receive.
        if q.yes_ask is not None:
            buy_edge = q.model_fair - q.yes_ask          # >0 => underpriced
            if buy_edge >= self.min_edge:
                return TradeIntent(
                    rung_slug=q.rung_slug, asset=q.asset, strike=q.strike,
                    expiry=q.expiry, side="BUY", token_id=q.yes_token,
                    price=q.yes_ask, size_shares=self._size(buy_edge),
                    edge_prob=buy_edge,
                    reason=f"belly cheap: fair {q.model_fair:.3f} > ask {q.yes_ask:.3f}",
                )
        if q.yes_bid is not None:
            sell_edge = q.yes_bid - q.model_fair          # >0 => overpriced
            if sell_edge >= self.min_edge:
                return TradeIntent(
                    rung_slug=q.rung_slug, asset=q.asset, strike=q.strike,
                    expiry=q.expiry, side="SELL", token_id=q.yes_token,
                    price=q.yes_bid, size_shares=self._size(sell_edge),
                    edge_prob=-sell_edge,
                    reason=f"wing rich: bid {q.yes_bid:.3f} > fair {q.model_fair:.3f}",
                )
        return None

    def generate(self, quotes: list[RungQuote]) -> list[TradeIntent]:
        """All qualifying intents, with belly-buy/wing-sell legs paired by group."""
        cands: list[TradeIntent] = []
        for q in quotes:
            c = self._candidate(q)
            if c is not None:
                cands.append(c)

        # Pair a BUY with a SELL within each (asset, expiry) so the legs offset.
        by_group: dict[tuple, list[TradeIntent]] = defaultdict(list)
        for c in cands:
            by_group[(c.asset, c.expiry)].append(c)

        pair_n = 0
        for (asset, expiry), group in by_group.items():
            buys = sorted((c for c in group if c.side == "BUY"),
                          key=lambda c: c.edge_prob, reverse=True)
            sells = sorted((c for c in group if c.side == "SELL"),
                           key=lambda c: c.edge_prob)  # most negative = richest wing
            for b, s in zip(buys, sells):
                pid = f"{asset}-{expiry}-{pair_n}"
                b.pair_id = s.pair_id = pid
                pair_n += 1
        return cands
