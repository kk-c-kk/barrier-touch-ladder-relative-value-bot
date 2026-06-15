"""Smile-RV signal direction + pairing checks."""
from src.strategy import RungQuote, SmileRV


def _q(slug, strike, ask, bid, fair, expiry=2_000_000):
    return RungQuote(
        rung_slug=slug, asset="BTC", strike=strike, expiry=expiry,
        t_remaining_s=30 * 86400, spot=65000.0, yes_ask=ask, yes_bid=bid,
        realized_vol=0.45, implied_vol=None, model_fair=fair,
        yes_token=f"{slug}-y", no_token=f"{slug}-n",
    )


def test_buys_cheap_belly():
    # fair 0.40 >> ask 0.30 -> underpriced -> BUY
    out = SmileRV(min_edge=0.03).generate([_q("belly", 70000, 0.30, 0.29, 0.40)])
    assert len(out) == 1 and out[0].side == "BUY"
    assert out[0].edge_prob > 0


def test_sells_rich_wing():
    # bid 0.10 >> fair 0.02 -> overpriced -> SELL
    out = SmileRV(min_edge=0.03).generate([_q("wing", 90000, 0.11, 0.10, 0.02)])
    assert len(out) == 1 and out[0].side == "SELL"
    assert out[0].edge_prob < 0


def test_no_trade_inside_threshold():
    out = SmileRV(min_edge=0.10).generate([_q("flat", 75000, 0.30, 0.29, 0.32)])
    assert out == []


def test_requires_realized_vol():
    q = _q("belly", 70000, 0.30, 0.29, 0.40)
    q.realized_vol = None
    assert SmileRV(min_edge=0.03, require_rv=True).generate([q]) == []


def test_pairs_belly_and_wing():
    out = SmileRV(min_edge=0.03).generate([
        _q("belly", 70000, 0.30, 0.29, 0.40),
        _q("wing", 90000, 0.11, 0.10, 0.02),
    ])
    pids = {o.pair_id for o in out}
    assert len(out) == 2 and len(pids) == 1 and None not in pids
