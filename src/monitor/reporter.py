"""Client library for the quant bot web reporter.

VENDORED verbatim from the web-interface repo
(github.com/kk-c-kk/Quant-bots-Web-interface-, reporter.py). Kept stdlib-only and
non-blocking on purpose: a down reporter service must never stall or crash the
logger/trader loop. If you update it upstream, re-copy it here (every module the
runtime imports must live in this repo).

All calls are fire-and-forget: events go on an in-memory queue and a daemon thread
POSTs them. Failures are LOGGED, never silently swallowed, and the event is dropped.
"""

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger("reporter")


class Reporter:
    def __init__(
        self,
        bot_id: str,
        base_url: str = "http://127.0.0.1:8000",
        name: Optional[str] = None,
        asset: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 3.0,
        max_queue: int = 2000,
    ):
        self.bot_id = bot_id
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.asset = asset
        self.api_key = api_key
        self.timeout = timeout
        self._q: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=max_queue)
        self._worker = threading.Thread(target=self._run, daemon=True, name="reporter")
        self._worker.start()

    # ------------------------------------------------------------ public API

    def price(self, price: float, ts: Optional[float] = None) -> None:
        self._enqueue("/api/ingest/price", {"price": float(price), "ts": ts})

    def trade(
        self,
        side: str = "",
        price: Optional[float] = None,
        size: Optional[float] = None,
        pnl: float = 0.0,
        note: str = "",
        ts: Optional[float] = None,
    ) -> None:
        self._enqueue(
            "/api/ingest/trade",
            {"side": side, "price": price, "size": size, "pnl": float(pnl),
             "note": note, "ts": ts},
        )

    def skip(self, reason: str = "", ts: Optional[float] = None) -> None:
        self._enqueue("/api/ingest/skip", {"reason": reason, "ts": ts})

    def heartbeat(self, ts: Optional[float] = None) -> None:
        self._enqueue("/api/ingest/heartbeat", {"ts": ts})

    # ------------------------------------------------------------ internals

    def _enqueue(self, path: str, payload: dict) -> None:
        payload = {k: v for k, v in payload.items() if v is not None}
        payload["bot_id"] = self.bot_id
        if self.name:
            payload["name"] = self.name
        if self.asset:
            payload["asset"] = self.asset
        payload.setdefault("ts", time.time())
        try:
            self._q.put_nowait((path, payload))
        except queue.Full:
            log.warning("reporter queue full (%d items); dropping event %s",
                        self._q.qsize(), path)

    def _run(self) -> None:
        while True:
            path, payload = self._q.get()
            try:
                self._post(path, payload)
            except Exception as exc:
                # Never crash the bot, but never hide the failure either.
                log.warning("reporter POST %s failed: %s", path, exc)

    def _post(self, path: str, payload: dict) -> None:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.api_key:
            req.add_header("X-API-Key", self.api_key)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"HTTP {resp.status}")
