"""Spot price + realized-vol feed via Hyperliquid REST.

Binance is geo-blocked from this location (HTTP 451), so Hyperliquid is the
reference clock; Coinbase is the documented fallback. Both are polled, not
websocket-subscribed — these are multi-day barriers, so a few-second-stale spot is
irrelevant and REST is drift-free (the 5min-bot WS-orderbook drift trap doesn't
apply here).

  POST /info  {"type":"allMids"}            -> {"BTC":"64999.5", ...}
  POST /info  {"type":"candleSnapshot",
               "req":{"coin","interval","startTime","endTime"}}  -> [{t,o,h,l,c,v}]
"""
from __future__ import annotations

import logging
import math
import time

import requests

log = logging.getLogger("spot")

_HOURS_PER_YEAR = 365.25 * 24


class HyperliquidSpot:
    def __init__(self, host: str = "https://api.hyperliquid.xyz", timeout: int = 15):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._mids_cache: dict[str, float] = {}
        self._mids_ts = 0.0

    def _post(self, payload: dict):
        try:
            r = self._session.post(f"{self.host}/info", json=payload,
                                   timeout=self.timeout,
                                   headers={"Content-Type": "application/json"})
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Hyperliquid request failed: %s", e)
            return None

    def all_mids(self, max_age_s: float = 2.0) -> dict[str, float]:
        """All perp mids, lightly cached so a burst of rung snapshots shares one call."""
        if self._mids_cache and time.time() - self._mids_ts < max_age_s:
            return self._mids_cache
        d = self._post({"type": "allMids"})
        if not isinstance(d, dict):
            return self._mids_cache
        out = {}
        for k, v in d.items():
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                continue
        if out:
            self._mids_cache, self._mids_ts = out, time.time()
        return self._mids_cache

    def spot(self, asset: str) -> float | None:
        return self.all_mids().get(asset.upper())

    def _candles(self, asset: str, interval: str, start_ms: int, end_ms: int):
        return self._post({
            "type": "candleSnapshot",
            "req": {"coin": asset.upper(), "interval": interval,
                    "startTime": start_ms, "endTime": end_ms},
        })

    def realized_vol(self, asset: str, lookback_hours: int = 168) -> float | None:
        """Annualized close-to-close vol from hourly candles over the lookback."""
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_hours * 3600 * 1000
        rows = self._candles(asset, "1h", start_ms, end_ms)
        if not isinstance(rows, list) or len(rows) < 3:
            return None
        closes = []
        for c in rows:
            try:
                closes.append(float(c["c"]))
            except (KeyError, TypeError, ValueError):
                continue
        if len(closes) < 3:
            return None
        rets = [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        hourly_sd = math.sqrt(var)
        return hourly_sd * math.sqrt(_HOURS_PER_YEAR)  # hourly -> annualized
