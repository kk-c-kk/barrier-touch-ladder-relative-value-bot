# barrier-touch-ladder-relative-value-bot

**What it does.** This bot trades **Polymarket crypto "touch ladders"** — one-touch
barrier binaries of the form *"What price will Bitcoin hit in June?"*, where each
"rung" (e.g. *"Will BTC reach $80k?"*) pays \$1 if the coin ever trades through that
level before expiry, else \$0. It treats every rung as a one-touch barrier option,
computes the fair touch probability from first principles, and bets where the market
price disagrees with the model — buying rungs that are too cheap and selling rungs
that are too rich, while staying market-neutral on the underlying.

**How it trades, one loop:**
1. **Discover** the live ladders for BTC/ETH/SOL/XRP from Polymarket's Gamma API.
2. **Price** each rung as a one-touch barrier (`P = 2·Φ(−d)`–style first-passage law)
   using realized vol, and read the **real executable CLOB ask/bid** for that rung.
3. **Signal** the *smile RV* trade: BUY the under-priced belly (e.g. "reach 70–72.5k")
   and SELL the over-priced far-OTM wing ("80k+") — a bet that the implied-vol smile
   is too steep. Only acts when the edge clears a threshold and realized vol agrees.
4. **Risk-gate & size:** cap per-ticket and per-correlated-cluster exposure (one pump
   touches every up-wing at once), and refuse to size at all until the resolved-outcome
   data shows a *statistically significant* edge against real asks.
5. **Hedge** the net book delta with a Hyperliquid perp → delta-neutral, leaving only
   the smile (short gamma/vega) bet we're actually paid for.

The whole thing runs slow and on purpose — these are multi-day barriers, so there's no
latency race; the edge is the **math**, and the moat is pricing path-dependence that
retail anchors on. It ships **paper-only**: live order placement and live hedging are
deliberately not wired until the edge is validated (see *Status* below).

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

## Build status

All four stages are built; the pipeline is paper-only and gated. What remains is
**data and validation**, not code.

1. ✅ **Ladder logger** — discovers ladders, snapshots every rung's live ask + model
   fair value + implied/realized vol to SQLite, backfills resolved outcomes.
   → `src/logger/`, `scripts/run_logger.py`
2. ✅ **Barrier fair-value model** — one-touch first-passage pricing + implied-vol
   inversion, 10 invariant tests. → `src/pricing/barrier.py`
3. ✅ **Validation harness** — one Bernoulli trial **per resolved rung** (deduped),
   marked against the **real ask**, reporting `win% − price%` with a Wald 95% CI;
   bands where `|edge| < CI` are flagged *noise*. → `src/analysis/`, `scripts/validate.py`
4. ✅ **Trader + Hyperliquid delta hedge** — smile-RV signal, cluster risk caps, and a
   `ValidationGate` that refuses to size until stage 3 shows a significant edge.
   **Paper-only**: live CLOB order placement and live hedging are intentionally not
   wired (they raise). → `src/strategy/`, `src/hedge/`, `src/trader/`, `scripts/run_trader.py`

> ⚠️ **Before this trades real money:** let the logger accumulate resolved outcomes,
> run `scripts/validate.py`, and only if the edge is significant against real asks do
> you wire the live executor/hedge — a deliberate, human-reviewed step.

## Layout

```
src/
  pricing/barrier.py         one-touch first-passage pricing + implied-vol inversion
  data/polymarket_clob.py    read-only CLOB asks/bids/mids (reused from the 5min repo)
  data/gamma_touch.py        touch-ladder discovery + resolution (Gamma /events)
  data/spot.py               Hyperliquid spot + realized vol (Binance is geo-blocked here)
  logger/ladder_logger.py    the snapshot+resolution logger
  strategy/smile_rv.py       smile-RV signal (buy cheap belly / sell rich wing) + pairing
  strategy/types.py          RungQuote / TradeIntent / Position
  analysis/validate.py       deduped per-rung trials, win%−price% + Wald CI
  hedge/hyperliquid.py       net-delta hedge (paper) + live stub
  trader/trader.py           discover→price→signal→risk-gate→paper-execute→hedge
scripts/run_logger.py        start logging ladders
scripts/run_trader.py        run the trader (paper by default; --live is stubbed)
scripts/validate.py          print the validation report
tests/                       barrier / hedge / smile / validation invariants (23 tests)
config.example.yaml          copy to config.yaml and edit
```

## Run

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml      # edit hosts / assets if needed

python scripts/run_logger.py            # 1. fill data/ladders.sqlite (run this first, for days)
python scripts/validate.py              # 2. check the edge once outcomes resolve
python scripts/run_trader.py            # 3. paper trader (gate stays BLOCKED until 2 is significant)
pytest                                  # 23 sanity / invariant tests
```

No API keys are needed for logging, validation, or the paper trader (public
read-only CLOB + Gamma + Hyperliquid). Live trading/hedging credentials come only at
the final wiring step, and never get committed (`config.yaml`, `secrets.yaml`, `.env`
are gitignored).
