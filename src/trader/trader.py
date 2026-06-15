"""Trader — discover, price, signal, RISK-GATE, (paper) execute, hedge.

Default mode is PAPER. Live execution is gated twice over:
  1. a ValidationGate that refuses to size unless `load_trials(...)` shows a
     statistically significant edge (|edge| > 95% CI) in the resolved data — the
     project's whole reason for existing;
  2. real order placement is simply not wired (PaperExecutor logs intents;
     LiveExecutor raises). Flipping to live is a deliberate, later, human step.

Risk controls: per-ticket cap, and a per-correlated-cluster (asset, expiry, BUY/
SELL) loss cap — one pump touches every up-wing together, so cluster risk, not
single-rung risk, is what blows up.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from src.analysis import load_trials, overall
from src.data import GammaTouch, HyperliquidSpot, PolymarketClob
from src.hedge import PaperHedge
from src.monitor import Monitor
from src.pricing import implied_vol, prob_touch, years_remaining
from src.strategy import Position, RungQuote, SmileRV, TradeIntent

log = logging.getLogger("trader")


class ValidationGate:
    """Allows sizing only if the resolved data shows a significant overall edge."""
    def __init__(self, db_path: str, min_n: int = 100):
        self.db_path = db_path
        self.min_n = min_n

    def status(self) -> tuple[bool, str]:
        try:
            trials = load_trials(self.db_path)
        except Exception as e:                       # no DB / no tables yet
            return False, f"no validation data ({e})"
        o = overall(trials)
        if o is None:
            return False, "no resolved trials yet"
        if o.n < self.min_n:
            return False, f"only {o.n} resolved trials (need >= {self.min_n})"
        if not o.significant:
            return False, (f"edge {o.edge*100:+.1f}pp within CI "
                           f"±{o.ci_half*100:.1f}pp on n={o.n} — not significant")
        return True, f"edge {o.edge*100:+.1f}pp > CI ±{o.ci_half*100:.1f}pp on n={o.n}"


class PaperExecutor:
    def __init__(self):
        self.positions: list[Position] = []

    def execute(self, intent: TradeIntent):
        signed = intent.size_shares if intent.side == "BUY" else -intent.size_shares
        self.positions.append(Position(
            rung_slug=intent.rung_slug, asset=intent.asset, strike=intent.strike,
            expiry=intent.expiry, size_shares=signed, entry_price=intent.price,
            yes_token=intent.token_id,
        ))
        log.info("[paper] %s %s %.0f sh @ %.3f (edge %+.3f) %s",
                 intent.side, intent.rung_slug, intent.size_shares, intent.price,
                 intent.edge_prob, intent.reason)


class LiveExecutor:
    def execute(self, intent: TradeIntent):
        raise NotImplementedError(
            "Live CLOB order placement is intentionally not wired. It needs the PM "
            "L2 API credentials + order signing, and must only run after the "
            "ValidationGate passes with human sign-off."
        )


class Trader:
    def __init__(self, cfg: dict, live: bool = False):
        self.cfg = cfg
        self.live = live
        self.poll = int(cfg.get("trader_poll_s", cfg.get("poll_interval_s", 60)))
        self.assets = cfg.get("assets")
        self.rv_lookback = int(cfg.get("realized_vol_lookback_h", 168))
        self.gamma = GammaTouch(cfg["gamma_host"], cfg.get("event_title_match", []),
                                max_pages=int(cfg.get("discovery_pages", 6)))
        self.clob = PolymarketClob(cfg["clob_host"])
        self.spot_feed = HyperliquidSpot(cfg["hl_host"])
        self.signal = SmileRV(
            min_edge=float(cfg.get("min_edge", 0.03)),
            base_size=float(cfg.get("base_size", 100.0)),
            max_size=float(cfg.get("max_ticket", 500.0)),
        )
        self.gate = ValidationGate(cfg["db_path"], int(cfg.get("min_validation_n", 100)))
        self.executor = LiveExecutor() if live else PaperExecutor()
        self.hedge = PaperHedge()        # live hedge is a separate later step
        self.max_cluster_risk = float(cfg.get("max_cluster_risk", 2000.0))
        self._rv_cache: dict[str, tuple[float, float]] = {}
        self.monitor = Monitor(cfg)      # optional web dashboard; no-op if disabled

    def _realized_vol(self, asset: str) -> float | None:
        rv, ts = self._rv_cache.get(asset, (None, 0.0))
        if rv is not None and time.time() - ts < 1800:
            return rv
        rv = self.spot_feed.realized_vol(asset, self.rv_lookback)
        if rv is not None:
            self._rv_cache[asset] = (rv, time.time())
        return rv

    def _quote(self, r) -> RungQuote | None:
        spot = self.spot_feed.spot(r.asset)
        if spot is None:
            return None
        yes_ask = self.clob.buy_price(r.yes_token)
        yes_bid = self.clob.sell_price(r.yes_token)
        rv = self._realized_vol(r.asset)
        now = time.time()
        t_left = (r.window_end - now) if r.window_end else None
        model_fair = iv = None
        if r.window_end and t_left and t_left > 0:
            T = years_remaining(t_left)
            up = r.strike >= spot
            already = (spot >= r.strike) if up else (spot <= r.strike)
            if rv:
                model_fair = prob_touch(spot, r.strike, rv, T, already_touched=already)
            if yes_ask is not None:
                iv = implied_vol(yes_ask, spot, r.strike, T)
        return RungQuote(
            rung_slug=r.rung_slug, asset=r.asset, strike=r.strike, expiry=r.window_end,
            t_remaining_s=int(t_left) if t_left else None, spot=spot,
            yes_ask=yes_ask, yes_bid=yes_bid, realized_vol=rv, implied_vol=iv,
            model_fair=model_fair, yes_token=r.yes_token, no_token=r.no_token,
        )

    def _apply_cluster_caps(self, intents: list[TradeIntent]) -> list[TradeIntent]:
        """Scale down each (asset, expiry, side) cluster so its worst-case notional
        stays under max_cluster_risk. Worst case for a leg ~= size * price (BUY) or
        size * (1 - price) (SELL, the NO collateral)."""
        groups: dict[tuple, list[TradeIntent]] = defaultdict(list)
        for it in intents:
            groups[(it.asset, it.expiry, it.side)].append(it)
        for group in groups.values():
            risk = sum(it.size_shares * (it.price if it.side == "BUY" else (1 - it.price))
                       for it in group)
            if risk > self.max_cluster_risk and risk > 0:
                scale = self.max_cluster_risk / risk
                for it in group:
                    it.size_shares *= scale
        return intents

    def tick(self):
        self.monitor.set_spot(self.monitor.headline_asset,
                              self.spot_feed.spot(self.monitor.headline_asset))
        allowed, why = self.gate.status()
        rungs = self.gamma.discover(self.assets)
        quotes = [q for q in (self._quote(r) for r in rungs) if q is not None]
        intents = self.signal.generate(quotes)
        log.info("gate=%s (%s) | %d rungs, %d quotes, %d raw intents",
                 "OPEN" if allowed else "BLOCKED", why, len(rungs), len(quotes), len(intents))

        if not allowed:
            for it in intents:                       # show what we WOULD do, size 0
                log.info("[blocked] would %s %s %.0f sh @ %.3f (%s)",
                         it.side, it.rung_slug, it.size_shares, it.price, it.reason)
            if intents:                              # one summary skip/tick (not per rung)
                self.monitor.skip(f"gate blocked: {len(intents)} would-trade ({why})")
            return

        intents = self._apply_cluster_caps(intents)
        for it in intents:
            if it.size_shares >= 1:
                self.executor.execute(it)
                self.monitor.trade(side=it.side, price=it.price,
                                   size=it.size_shares, pnl=0.0,
                                   note=f"{it.rung_slug} {it.reason}")
        now = time.time()
        self.hedge.rebalance(self.executor.positions, self.spot_feed.spot,
                             self._realized_vol, now)

    def run(self):
        log.info("trader started (mode=%s, poll=%ds)", "LIVE" if self.live else "PAPER", self.poll)
        while True:
            start = time.time()
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
            time.sleep(max(self.poll - (time.time() - start), 1.0))
