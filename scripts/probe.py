"""Live discovery probe — confirm the logger actually reads the ladders correctly.

Hits the real Gamma /events endpoint, shows which events match, what rungs parse
out of them, and then for a sample of rungs pulls the live CLOB ask/bid, the
Hyperliquid spot, and computes model fair value + implied vol. Read-only; trades
nothing. Run: `python scripts/probe.py`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from src.data import GammaTouch, HyperliquidSpot, PolymarketClob  # noqa: E402
from src.data.gamma_touch import _UA  # noqa: E402
from src.pricing import implied_vol, prob_touch, years_remaining  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HL = "https://api.hyperliquid.xyz"
ASSETS = ["BTC", "ETH", "SOL", "XRP"]
TITLE_MATCH = ["what price will", "hit", "reach"]


def main():
    import time

    print("=" * 70)
    print("STEP 1 — raw /events reachability")
    r = requests.get(f"{GAMMA}/events",
                     params={"closed": "false", "active": "true", "limit": 200,
                             "order": "volume24hr", "ascending": "false"},
                     headers={"User-Agent": _UA, "Accept": "application/json"},
                     timeout=20)
    print(f"  HTTP {r.status_code}")
    events = r.json()
    events = events if isinstance(events, list) else events.get("data", [])
    print(f"  events returned: {len(events)}")
    matched = [e for e in events
               if any(s in str(e.get("title", "")).lower() for s in TITLE_MATCH)]
    print(f"  title-matched events: {len(matched)}")
    for e in matched[:15]:
        print(f"    - {e.get('title')!r}  ({len(e.get('markets') or [])} markets)")

    if matched:
        print("\n  RAW shape of first matched event's first market (key check):")
        m0 = (matched[0].get("markets") or [{}])[0]
        for k in ("slug", "question", "groupItemTitle", "outcomes",
                  "clobTokenIds", "endDate", "closed"):
            print(f"    {k}: {json.dumps(m0.get(k))[:90]}")

    print("\n" + "=" * 70)
    print("STEP 2 — GammaTouch.discover() parsed rungs")
    gamma = GammaTouch(GAMMA, TITLE_MATCH)
    rungs = gamma.discover(ASSETS)
    print(f"  parsed rungs (BTC/ETH/SOL/XRP): {len(rungs)}")
    by_asset = {}
    for rg in rungs:
        by_asset.setdefault(rg.asset, []).append(rg)
    for asset, rs in sorted(by_asset.items()):
        print(f"    {asset}: {len(rs)} rungs, strikes "
              f"{sorted(set(r.strike for r in rs))[:8]}")

    if not rungs:
        print("  !! ZERO rungs parsed — discovery/parse needs fixing. Stopping.")
        return

    print("\n" + "=" * 70)
    print("STEP 3 — live pricing on a sample (CLOB ask/bid + spot + model)")
    clob = PolymarketClob(CLOB)
    spot = HyperliquidSpot(HL)
    now = time.time()
    rv_cache = {}
    sample = rungs[:10]
    print(f"  {'asset':<5} {'strike':>9} {'dir':>4} {'ask':>6} {'bid':>6} "
          f"{'spot':>9} {'days':>5} {'fair':>6} {'iv':>5}")
    for rg in sample:
        ask = clob.buy_price(rg.yes_token)
        bid = clob.sell_price(rg.yes_token)
        sp = spot.spot(rg.asset)
        if rg.asset not in rv_cache:
            rv_cache[rg.asset] = spot.realized_vol(rg.asset, 168)
        rv = rv_cache[rg.asset]
        fair = iv = None
        days = direction = None
        if sp and rg.window_end:
            t_left = rg.window_end - now
            days = t_left / 86400
            up = rg.strike >= sp
            direction = "UP" if up else "DN"
            if t_left > 0 and rv:
                T = years_remaining(t_left)
                already = (sp >= rg.strike) if up else (sp <= rg.strike)
                fair = prob_touch(sp, rg.strike, rv, T, already_touched=already)
                if ask is not None:
                    iv = implied_vol(ask, sp, rg.strike, T)
        print(f"  {rg.asset:<5} {rg.strike:>9.2f} {str(direction):>4} "
              f"{_f(ask):>6} {_f(bid):>6} {_f(sp,2):>9} {_f(days,1):>5} "
              f"{_f(fair):>6} {_f(iv):>5}")

    rva = {a: rv_cache[a] for a in rv_cache}
    print(f"\n  realized vol (annualized): "
          f"{ {a: round(v,3) if v else None for a,v in rva.items()} }")
    print("\nDONE — check: rungs>0 per asset, strikes sane, ask/bid populated, "
          "fair vs ask gap is the smile we trade.")


def _f(x, nd=3):
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "-"


if __name__ == "__main__":
    main()
