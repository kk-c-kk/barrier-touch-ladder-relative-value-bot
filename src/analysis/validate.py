"""Offline validation — the gate everything must pass before sizing real money.

THE recurring failure mode here: a backtest priced on mids/proxies shows +4-12pp
"edge" that collapses to noise against real executable asks. So this harness:

  1. takes ONE Bernoulli trial per RESOLVED rung (dedup) — never per snapshot and
     never per rung-within-a-ladder treated as independent. UP-strikes in a ladder
     are cluster-correlated; counting snapshots/legs as independent fakes
     significance. The trial's price is the REAL `yes_ask` at the chosen entry, and
     the outcome is whether the rung actually TOUCHED.
  2. buckets trials and reports n, win%, mean price%, edge = win% - price%, and a
     Wald 95% CI on the win rate. If |edge| < the CI half-width, it's NOISE.

`entry_when` selects which snapshot of each rung becomes the trial — default is the
EARLIEST snapshot that has a real ask and a positive time-to-expiry (an entry you
could actually have taken), not the most favorable one.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

_Z95 = 1.959963985


@dataclass
class Trial:
    rung_slug: str
    asset: str
    strike: float
    price: float        # real yes_ask paid at entry
    implied_vol: float | None
    won: int            # 1 if TOUCHED, else 0


@dataclass
class BandStat:
    label: str
    n: int
    win_rate: float
    mean_price: float
    edge: float         # win_rate - mean_price
    ci_half: float      # Wald 95% half-width on win_rate
    @property
    def significant(self) -> bool:
        return abs(self.edge) > self.ci_half


def load_trials(db_path: str, entry: str = "earliest") -> list[Trial]:
    """One trial per resolved rung. `entry` = 'earliest' | 'latest' snapshot with a
    valid ask and positive time-to-expiry."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    order = "ASC" if entry == "earliest" else "DESC"
    rows = con.execute(
        f"""
        SELECT s.rung_slug, s.asset, s.strike, s.yes_ask, s.implied_vol, r.outcome
        FROM snapshots s
        JOIN resolutions r ON r.rung_slug = s.rung_slug
        WHERE s.yes_ask IS NOT NULL AND s.t_remaining_s > 0
        ORDER BY s.rung_slug, s.ts {order}
        """
    ).fetchall()
    con.close()

    seen: set[str] = set()
    trials: list[Trial] = []
    for row in rows:
        if row["rung_slug"] in seen:
            continue                      # dedup: first (=chosen entry) wins
        seen.add(row["rung_slug"])
        trials.append(Trial(
            rung_slug=row["rung_slug"], asset=row["asset"], strike=row["strike"],
            price=float(row["yes_ask"]), implied_vol=row["implied_vol"],
            won=1 if row["outcome"] == "TOUCHED" else 0,
        ))
    return trials


def _band(label: str, trials: list[Trial]) -> BandStat | None:
    n = len(trials)
    if n == 0:
        return None
    win = sum(t.won for t in trials) / n
    price = sum(t.price for t in trials) / n
    ci = _Z95 * math.sqrt(max(win * (1 - win), 0.0) / n)
    return BandStat(label, n, win, price, win - price, ci)


def by_price_band(trials: list[Trial], edges=(0.0, 0.05, 0.15, 0.35, 0.65, 1.01)) -> list[BandStat]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        b = _band(f"price [{lo:.2f},{hi:.2f})",
                  [t for t in trials if lo <= t.price < hi])
        if b:
            out.append(b)
    return out


def overall(trials: list[Trial]) -> BandStat | None:
    return _band("ALL", trials)


def format_report(trials: list[Trial]) -> str:
    lines = [f"{'band':<22} {'n':>4} {'win%':>7} {'price%':>7} {'edge':>7} {'+-95%':>7}  sig"]
    rows = []
    o = overall(trials)
    if o:
        rows.append(o)
    rows.extend(by_price_band(trials))
    for b in rows:
        lines.append(
            f"{b.label:<22} {b.n:>4} {b.win_rate*100:>6.1f}% {b.mean_price*100:>6.1f}% "
            f"{b.edge*100:>+6.1f} {b.ci_half*100:>6.1f}  {'YES' if b.significant else 'noise'}"
        )
    if not trials:
        lines.append("(no resolved trials yet - let the logger accumulate outcomes)")
    return "\n".join(lines)
