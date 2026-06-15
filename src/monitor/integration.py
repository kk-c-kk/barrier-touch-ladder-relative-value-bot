"""Bridge from the bot to the central quant web reporter.

Why a wrapper (not raw Reporter):
  - The reporter must be OPTIONAL and config-gated: if `reporter.enabled` is false
    or the section is absent, every call is a no-op and the bot runs unchanged.
  - The dashboard marks a bot OFFLINE after 20s, but the logger ticks every 60s, so
    a naive integration flaps offline between ticks. A daemon heartbeat thread pings
    every `heartbeat_s` (default 10s) with the last known headline spot — keeping the
    online dot green AND drawing a smoother price line than the 60s loop would.
  - Never let monitoring break the bot: the underlying Reporter is already
    non-blocking and logs (not raises) on failure; this wrapper additionally guards
    construction so a bad config can't stop the logger from starting.

The headline spot is fed by the bot once per tick via `set_spot()`; the heartbeat
thread re-emits the latest value, so a slow loop still produces a live chart.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .reporter import Reporter

log = logging.getLogger("monitor")


class Monitor:
    def __init__(self, cfg: dict | None):
        rc = (cfg or {}).get("reporter") or {}
        self.enabled = bool(rc.get("enabled", False))
        self.headline_asset = str(rc.get("headline_asset", "BTC")).upper()
        self.heartbeat_s = float(rc.get("heartbeat_s", 10))
        self._last_spot: float | None = None
        self._rep: Reporter | None = None

        if not self.enabled:
            return
        try:
            self._rep = Reporter(
                bot_id=str(rc.get("bot_id", "touch-ladder-logger")),
                base_url=str(rc.get("base_url", "http://127.0.0.1:8000")),
                name=str(rc.get("name", "Touch-Ladder Logger")),
                asset=str(rc.get("asset", f"{self.headline_asset} touch ladders")),
                api_key=os.environ.get("REPORTER_API_KEY"),
            )
        except Exception:
            log.exception("monitor: failed to build Reporter; monitoring disabled")
            self._rep = None
            return

        threading.Thread(target=self._heartbeat_loop, daemon=True,
                         name="monitor-hb").start()
        log.info("monitor enabled -> %s (bot_id=%s, headline=%s)",
                 rc.get("base_url"), rc.get("bot_id"), self.headline_asset)

    # -- called by the bot --------------------------------------------------

    def set_spot(self, asset: str, price: float | None) -> None:
        """Record the headline asset's spot; the heartbeat thread emits it."""
        if self._rep and price and asset.upper() == self.headline_asset:
            self._last_spot = float(price)

    def trade(self, **kw) -> None:
        if self._rep:
            self._rep.trade(**kw)

    def skip(self, reason: str = "") -> None:
        if self._rep:
            self._rep.skip(reason)

    # -- internals ----------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        while True:
            try:
                if self._last_spot is not None:
                    self._rep.price(self._last_spot)  # doubles as heartbeat + chart
                else:
                    self._rep.heartbeat()             # alive before first spot
            except Exception:
                log.exception("monitor heartbeat failed")
            time.sleep(self.heartbeat_s)
