"""Read-only CLOB price client.

Gamma returns null prices for open markets, so live share prices come from the
CLOB order book. No auth needed for public price reads.

  CAREFUL — Polymarket's `side` names the SIDE OF THE BOOK, not your action.
  Verified live against /book on 2026-06-15 (BTC reach-70k token):
    /price?token_id=..&side=buy   -> best BID  (0.47)  == highest buy order
    /price?token_id=..&side=sell  -> best ASK  (0.48)  == lowest sell order
  So to BUY a share you pay the ASK (side=sell); to SELL you receive the BID
  (side=buy). The 5min repo's client had these labelled backwards — it didn't
  matter there (it found no edge anyway) but here it's fatal: marking a buy at the
  bid instead of the ask manufactures ~1c of fake edge per leg.

  /midpoint?token_id=..          -> book midpoint

VALIDATION RULE (the whole project hinges on this): mark every hypothetical BUY
against `buy_price` (the real executable ASK) and every SELL against `sell_price`
(the real BID), never the midpoint. Mids/wrong-side prices flattered every prior
strategy here into a fake edge.

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
        """Best ASK: the price to BUY one share, 0..1. THE number to validate buys on.
        Polymarket returns the ask under side='sell' (sell side of the book)."""
        return self._price(token_id, "sell")

    def sell_price(self, token_id: str) -> float | None:
        """Best BID: the price you'd RECEIVE to sell one share, 0..1.
        Polymarket returns the bid under side='buy' (buy side of the book)."""
        return self._price(token_id, "buy")

    def midpoint(self, token_id: str) -> float | None:
        d = self._get("/midpoint", {"token_id": token_id})
        if not d or "mid" not in d:
            return None
        try:
            return float(d["mid"])
        except (TypeError, ValueError):
            return None
