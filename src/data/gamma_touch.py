"""Polymarket Gamma discovery + resolution for crypto TOUCH ladders.

This is NOT the 5-minute up/down client. Touch ladders are published as Gamma
*events* ("What price will Bitcoin hit in June?") whose child *markets* are the
individual rungs ("Will Bitcoin reach $80,000 in June?", Yes/No). We discover the
event, parse each rung's strike + Yes/No CLOB token ids, and — once the event
expires — read the resolved outcome back from Gamma.

  discovery : GET /events?closed=false&order=volume24hr&ascending=false  (high-vol
              recurring ladders surface here; the default /markets pagination
              buries them).
  resolution: GET /markets?slug=<rung>&closed=true -> outcomePrices == ["1","0"]
              (Yes won == TOUCHED) or ["0","1"] (No == never touched).

LIVE-SHAPE CAVEAT: Polymarket wording and event/market nesting drift over time.
Field access is defensive (a schema tweak degrades to "no rungs found" rather than
a crash), but VERIFY against a live probe before trusting the parse — print one raw
/events response and confirm the strike regex and Yes/No labelling. Array fields
(`outcomes`, `clobTokenIds`, `outcomePrices`) come back as JSON-encoded STRINGS and
must be json.loads()'d, never indexed raw.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger("gamma_touch")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# "$80,000" / "$80k" / "80,000" / "100k" -> 80000.0 / 100000.0
_STRIKE_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM]?)")
# Crypto asset hints in a question/title.
_ASSET_HINTS = {
    "BTC": ("btc", "bitcoin"),
    "ETH": ("eth", "ether", "ethereum"),
    "SOL": ("sol", "solana"),
    "XRP": ("xrp", "ripple"),
    "DOGE": ("doge", "dogecoin"),
}


def _iso_to_unix(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return []
    return []


def _parse_strike(text: str) -> float | None:
    m = _STRIKE_RE.search(text or "")
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    suffix = m.group(2).lower()
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    return num


def _detect_asset(text: str) -> str | None:
    t = (text or "").lower()
    for asset, hints in _ASSET_HINTS.items():
        if any(h in t for h in hints):
            return asset
    return None


@dataclass
class Rung:
    rung_slug: str
    event_slug: str
    asset: str
    strike: float
    question: str
    yes_token: str            # "touched" leg
    no_token: str             # "never touched" leg
    window_end: int | None    # expiry unix
    closed: bool
    raw: dict = field(repr=False, default_factory=dict)


class GammaTouch:
    def __init__(self, gamma_host: str, title_match: list[str],
                 timeout: int = 20, scan_limit: int = 200):
        self.host = gamma_host.rstrip("/")
        self.title_match = [t.lower() for t in title_match]
        self.timeout = timeout
        self.scan_limit = scan_limit
        self._session = requests.Session()
        self._resolved: dict[str, str] = {}      # rung_slug -> "TOUCHED"/"MISS"
        self._res_last: dict[str, float] = {}
        self.resolution_interval = 60

    def _get(self, path: str, params: dict):
        try:
            r = self._session.get(f"{self.host}{path}", params=params,
                                  timeout=self.timeout,
                                  headers={"User-Agent": _UA, "Accept": "application/json"})
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.warning("Gamma request failed %s: %s", path, e)
            return None

    def _title_matches(self, text: str) -> bool:
        t = (text or "").lower()
        return any(s in t for s in self.title_match)

    def _parse_rung(self, m: dict, event_slug: str, asset_hint: str | None) -> Rung | None:
        outcomes = _as_list(m.get("outcomes"))            # ["Yes","No"]
        token_ids = _as_list(m.get("clobTokenIds"))
        if len(outcomes) < 2 or len(token_ids) < 2:
            return None

        # Map Yes/No -> the "touched" leg. Touch ladders are Yes=touched.
        labels = [str(o).strip().lower() for o in outcomes[:2]]
        try:
            yes_idx = labels.index("yes")
        except ValueError:
            yes_idx = 0  # fall back to first leg; flagged by question text anyway
        no_idx = 1 - yes_idx

        question = str(m.get("question", "") or m.get("groupItemTitle", ""))
        strike = _parse_strike(question) or _parse_strike(str(m.get("groupItemTitle", "")))
        if strike is None:
            return None
        asset = _detect_asset(question) or asset_hint
        if asset is None:
            return None

        slug = str(m.get("slug") or m.get("id"))
        return Rung(
            rung_slug=slug,
            event_slug=event_slug,
            asset=asset,
            strike=strike,
            question=question,
            yes_token=str(token_ids[yes_idx]),
            no_token=str(token_ids[no_idx]),
            window_end=_iso_to_unix(m.get("endDate")),
            closed=bool(m.get("closed", False)),
            raw=m,
        )

    def discover(self, assets: list[str] | None = None) -> list[Rung]:
        """Return all live touch-ladder rungs across matching events.

        `assets` (optional) filters to those tickers; None keeps every detected
        crypto asset.
        """
        data = self._get("/events", {
            "closed": "false", "active": "true", "limit": self.scan_limit,
            "order": "volume24hr", "ascending": "false",
        })
        if data is None:
            return []
        events = data if isinstance(data, list) else data.get("data", [])
        rungs: list[Rung] = []
        wanted = {a.upper() for a in assets} if assets else None

        for ev in events:
            title = str(ev.get("title", "") or ev.get("slug", ""))
            if not self._title_matches(title):
                continue
            event_slug = str(ev.get("slug") or ev.get("id"))
            asset_hint = _detect_asset(title)
            for m in ev.get("markets", []) or []:
                rung = self._parse_rung(m, event_slug, asset_hint)
                if rung is None or rung.closed:
                    continue
                if wanted and rung.asset not in wanted:
                    continue
                rungs.append(rung)
        return rungs

    def get_resolution(self, rung_slug: str) -> str | None:
        """'TOUCHED' / 'MISS' once the rung resolves, else None. Cached permanently.

        Same closed=true trick as the 5min client: only the archived record carries
        the real outcomePrices ["1","0"]/["0","1"].
        """
        if rung_slug in self._resolved:
            return self._resolved[rung_slug]
        now = time.time()
        if now - self._res_last.get(rung_slug, 0.0) < self.resolution_interval:
            return None
        self._res_last[rung_slug] = now

        data = self._get("/markets", {"slug": rung_slug, "closed": "true"})
        if data is None:
            return None
        rows = data if isinstance(data, list) else data.get("data", [])
        if not rows:
            return None
        m = rows[0]
        outcomes = _as_list(m.get("outcomes"))
        prices = _as_list(m.get("outcomePrices"))
        if len(outcomes) < 2 or len(prices) < 2:
            return None
        for o, p in zip(outcomes, prices):
            try:
                if float(p) >= 0.99:
                    res = "TOUCHED" if str(o).strip().lower() == "yes" else "MISS"
                    self._resolved[rung_slug] = res
                    return res
            except (TypeError, ValueError):
                continue
        return None
