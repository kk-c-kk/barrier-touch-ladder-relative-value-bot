"""Touch-ladder logger — the long pole of the build.

We have ZERO touch-outcome history, and the edge can't be validated until data
accumulates. This loop:

  1. discovers live touch-ladder rungs (Gamma /events),
  2. snapshots each rung every `poll_interval_s`: live CLOB ask + bid + mid, spot,
     time-to-expiry, model fair value (at realized vol), and the vol IMPLIED by the
     ask — so the smile can be reconstructed offline,
  3. backfills the resolved outcome (TOUCHED / MISS) once each rung settles.

Schema is one row per (rung, poll). VALIDATION happens offline and must DEDUP to
one Bernoulli trial per resolved rung (UP-strikes within a ladder are
cluster-correlated; treating snapshots or rungs as independent fakes significance).
Nothing here trades — it only records. SQLite uses WAL + synchronous=NORMAL so the
24/7 write load never fsyncs per commit (OS crash loses <=last few rows, process
crash loses nothing).
"""
from __future__ import annotations

import logging
import sqlite3
import time

from src.data import GammaTouch, HyperliquidSpot, PolymarketClob, Rung
from src.pricing import implied_vol, prob_touch, years_remaining

log = logging.getLogger("ladder_logger")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rungs (
    rung_slug   TEXT PRIMARY KEY,
    event_slug  TEXT,
    asset       TEXT,
    strike      REAL,
    barrier_dir TEXT,          -- 'UP' (B>spot at discovery) / 'DOWN'
    question    TEXT,
    yes_token   TEXT,
    no_token    TEXT,
    window_end  INTEGER,
    discovered_ts INTEGER
);
CREATE TABLE IF NOT EXISTS snapshots (
    ts          INTEGER,
    rung_slug   TEXT,
    asset       TEXT,
    strike      REAL,
    spot        REAL,
    t_remaining_s INTEGER,
    yes_ask     REAL,          -- REAL executable ask on the touched leg (validate on this)
    yes_bid     REAL,
    yes_mid     REAL,
    realized_vol REAL,
    model_fair  REAL,          -- prob_touch at realized vol
    implied_vol REAL,          -- vol implied by yes_ask
    PRIMARY KEY (ts, rung_slug)
);
CREATE TABLE IF NOT EXISTS resolutions (
    rung_slug   TEXT PRIMARY KEY,
    outcome     TEXT,          -- 'TOUCHED' / 'MISS'
    resolved_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_rung ON snapshots(rung_slug);
"""


class LadderLogger:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.assets = cfg.get("assets")
        self.poll = int(cfg.get("poll_interval_s", 60))
        self.rv_lookback = int(cfg.get("realized_vol_lookback_h", 168))
        self.gamma = GammaTouch(cfg["gamma_host"], cfg.get("event_title_match", []))
        self.clob = PolymarketClob(cfg["clob_host"])
        self.spot_feed = HyperliquidSpot(cfg["hl_host"])
        self.db = sqlite3.connect(cfg["db_path"])
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._rv_cache: dict[str, tuple[float, float]] = {}  # asset -> (rv, ts)

    def _realized_vol(self, asset: str) -> float | None:
        rv, ts = self._rv_cache.get(asset, (None, 0.0))
        if rv is not None and time.time() - ts < 1800:  # refresh every 30 min
            return rv
        rv = self.spot_feed.realized_vol(asset, self.rv_lookback)
        if rv is not None:
            self._rv_cache[asset] = (rv, time.time())
        return rv

    def _record_rung(self, r: Rung, spot: float | None):
        barrier_dir = "UP" if (spot is None or r.strike >= spot) else "DOWN"
        self.db.execute(
            "INSERT OR IGNORE INTO rungs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.rung_slug, r.event_slug, r.asset, r.strike, barrier_dir,
             r.question, r.yes_token, r.no_token, r.window_end, int(time.time())),
        )

    def _snapshot(self, r: Rung):
        now = int(time.time())
        spot = self.spot_feed.spot(r.asset)
        self._record_rung(r, spot)

        yes_ask = self.clob.buy_price(r.yes_token)
        yes_bid = self.clob.sell_price(r.yes_token)
        yes_mid = self.clob.midpoint(r.yes_token)
        rv = self._realized_vol(r.asset)
        t_left = (r.window_end - now) if r.window_end else None

        model_fair = iv = None
        if spot and r.window_end and t_left and t_left > 0:
            T = years_remaining(t_left)
            # Direction-aware "already touched": an UP rung is touched if spot has
            # reached the strike (spot>=strike); a DOWN rung if spot<=strike.
            up = r.strike >= spot
            already = (spot >= r.strike) if up else (spot <= r.strike)
            if rv:
                model_fair = prob_touch(spot, r.strike, rv, T, already_touched=already)
            if yes_ask is not None:
                iv = implied_vol(yes_ask, spot, r.strike, T)

        self.db.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, r.rung_slug, r.asset, r.strike, spot, t_left,
             yes_ask, yes_bid, yes_mid, rv, model_fair, iv),
        )

    def _backfill_resolutions(self):
        rows = self.db.execute(
            "SELECT r.rung_slug FROM rungs r "
            "LEFT JOIN resolutions res ON r.rung_slug = res.rung_slug "
            "WHERE res.rung_slug IS NULL"
        ).fetchall()
        for (slug,) in rows:
            outcome = self.gamma.get_resolution(slug)
            if outcome:
                self.db.execute(
                    "INSERT OR REPLACE INTO resolutions VALUES (?,?,?)",
                    (slug, outcome, int(time.time())),
                )
                log.info("resolved %s -> %s", slug, outcome)

    def tick(self):
        rungs = self.gamma.discover(self.assets)
        log.info("discovered %d live rungs", len(rungs))
        for r in rungs:
            try:
                self._snapshot(r)
            except Exception:               # one bad rung must not stall the loop
                log.exception("snapshot failed for %s", r.rung_slug)
        self._backfill_resolutions()
        self.db.commit()

    def run(self):
        log.info("ladder logger started (poll=%ds, assets=%s)", self.poll, self.assets)
        while True:
            start = time.time()
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
            elapsed = time.time() - start
            time.sleep(max(self.poll - elapsed, 1.0))
