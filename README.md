# barrier-touch-ladder-relative-value-bot

A relative-value bot for **Polymarket crypto "touch ladders"** — one-touch barrier
binaries of the form *"What price will Bitcoin hit in June?"* (daily / weekly /
monthly). It prices each rung as a one-touch barrier option, finds rungs that are
mispriced **against the live executable CLOB ask** (not mids, not a proxy), and
delta-hedges the net book on a Hyperliquid perp.

## Why this market (and not 5-minute up/down)

Every prior 5-minute up/down bot here died the same way: in that market fair value
is trivial, the price is an efficient unbiased predictor (actual win rate ≈ entry
price in every band), so the only thing left to compete on is **latency** — a
speed/infra war against incumbents. Apparent backtest edges (+4–12pp on mids/proxy)
collapsed to noise against real asks.

Touch ladders are the opposite trade. Pricing a path-dependent barrier is **hard**
— retail anchors and overpays the longshot tail — so the edge is the **math, not
speed**. No latency war; a multi-day hold makes the Hyperliquid hedge actually earn
its keep. The moat is correct first-passage pricing.

> **Status: UNPROVEN.** A single live snapshot (2026-06-14) showed the implied-vol
> smile is too steep — wings imply 49–69% vol vs ~45% realized, while the belly is
> if anything cheap. That is a hypothesis, not a validated edge. It is not real
> until it survives against real CLOB asks with a proper, deduped confidence
> interval. If it dies there too, crypto's transparent reference price means even
> this market is too efficient → pivot to non-crypto ladders.

## The trade (three layers)

- **Smile RV (the core):** BUY the underpriced belly (e.g. "reach 70–72.5k") + SELL
  the rich wing ("80k+") — a bet that the smile is too steep. The legs offset
  collateral and tail risk.
- **Tail fade:** sell rich far-OTM wings. Cleanest signal but capital-inefficient
  (PM has no naked short → ~99¢ locked per ticket) and cluster-correlated.
- **Anchoring:** fade round-number salience ($100k). Soft.

## Pricing

Driftless-spot one-touch via the first-passage law of GBM (`src/pricing/barrier.py`):

```
P_touch(up)  = Φ((νT − m)/σ√T) + e^(2νm/σ²)·Φ((−νT − m)/σ√T),   m = ln(B/S) > 0
```

with `ν = −σ²/2` (martingale spot). At `ν = 0` this reduces to the textbook
`2·Φ(−d)`, `d = ln(B/S)/(σ√T)`. Condition on already-touched (→ 1), on time
remaining, and calibrate σ to **both** the near-money implied smile and realized
vol — trade only rungs where both agree a price is wrong.

## Build order

1. **Ladder logger** *(the long pole — start here).* We have zero touch-outcome
   history; the edge can't be validated until data accumulates. Discover ladders,
   snapshot every rung's live ask + model fair value + implied/realized vol to
   SQLite, and backfill the resolved outcome. → `src/logger/`, `scripts/run_logger.py`
2. **Barrier fair-value model.** → `src/pricing/barrier.py` (scaffolded + tested).
3. **Validation.** Mark every hypothetical fill against the **real** CLOB ask,
   **dedup to one Bernoulli trial per resolved rung**, report `win% − price%` with a
   Wald 95% CI. If a band's edge is smaller than its CI, it's noise — don't size it.
4. **Live trader + Hyperliquid delta hedge.** Only after step 3 shows a real edge.

## Layout

```
src/
  pricing/barrier.py        one-touch first-passage pricing + implied-vol inversion
  data/polymarket_clob.py   read-only CLOB asks/mids (reused from the 5min repo)
  data/gamma_touch.py       touch-ladder discovery + resolution (Gamma /events)
  data/spot.py              Hyperliquid spot + candles (Binance is geo-blocked here)
  logger/ladder_logger.py   the snapshot+resolution logger
scripts/run_logger.py       entrypoint
tests/test_barrier.py       pricing sanity / invariants
config.example.yaml         copy to config.yaml and edit
```

## Run

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml      # edit hosts / assets if needed
python scripts/run_logger.py            # starts filling data/ladders.sqlite
pytest                                  # barrier-math sanity checks
```

No API keys are needed for logging (public read-only CLOB + Gamma + Hyperliquid).
Trading/hedging credentials come later, in the trader stage, and never get committed.
