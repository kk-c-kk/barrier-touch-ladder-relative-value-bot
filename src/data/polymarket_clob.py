"""Read-only CLOB price client (reused from the 5min repo, unchanged).

Gamma returns null prices for open markets, so live share prices come from the
CLOB order book. No auth needed for public price reads.

  /price?token_id=..&side=buy  -> best ask (what you'd pay to BUY the share)
  /price?token_id=..&side=sell -> best bid (what you'd receive to SELL the share)
  /midpoint?token_id=..        -> book midpoint

VALIDATION RULE (the whole project hinges on this): mark every hypothetical fill
against `buy_price` (the real executable ask), never the midpoint. Mids flattered
every prior strategy here into a fake edge.

Returns None when no order book exists yet (market just opened or already settled).
"""
import requests


class PolymarketClob:
    def __init__(self, clob_host: str, timeout: int = 10):
        self.host = clob_host.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()  # reuse TCP+TLS across requests

    def _get(self, path: str, params: dict):
        try:
            r = self._session.get(f"{self.host}{path}", params=params, timeout=self.timeout,
                                  headers={"User-Agent": "touch-ladder-bot/0.1"})
            if r.status_code == 404:
                return None  # no orderbook
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            return None

    def _price(self, token_id: str, side: str) -> float | None:
        d = self._get("/price", {"token_id": token_id, "side": side})
        if not d or "price" not in d:
            return None
        try:
            return float(d["price"])
        except (TypeError, ValueError):
            return None

    def buy_price(self, token_id: str) -> float | None:
        """Best ask: the price to BUY one share, 0..1. THE number to validate on."""
        return self._price(token_id, "buy")

    def sell_price(self, token_id: str) -> float | None:
        """Best bid: the price you'd RECEIVE to sell one share, 0..1."""
        return self._price(token_id, "sell")

    def midpoint(self, token_id: str) -> float | None:
        d = self._get("/midpoint", {"token_id": token_id})
        if not d or "mid" not in d:
            return None
        try:
            return float(d["mid"])
        except (TypeError, ValueError):
            return None
