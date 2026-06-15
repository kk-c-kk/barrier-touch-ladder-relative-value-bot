"""Hedge delta sign/magnitude checks."""
from src.hedge import position_delta, target_qty
from src.strategy.types import Position


def _pos(size):
    # up-barrier touch, 30d out
    return Position(rung_slug="r", asset="BTC", strike=80000.0,
                    expiry=None, size_shares=size, entry_price=0.1, yes_token="t")


def test_long_touch_has_positive_delta():
    """Long an up-touch -> value rises as spot rises -> positive delta -> short coins."""
    d = position_delta(_pos(100), spot=65000, sigma=0.5, t_remaining_s=30 * 86400)
    assert d > 0


def test_short_touch_has_negative_delta():
    d = position_delta(_pos(-100), spot=65000, sigma=0.5, t_remaining_s=30 * 86400)
    assert d < 0


def test_delta_scales_with_size():
    a = position_delta(_pos(100), 65000, 0.5, 30 * 86400)
    b = position_delta(_pos(200), 65000, 0.5, 30 * 86400)
    assert abs(b - 2 * a) < 1e-9


def test_expired_or_no_vol_zero_delta():
    assert position_delta(_pos(100), 65000, 0.5, 0) == 0.0
    assert position_delta(_pos(100), 65000, 0.0, 30 * 86400) == 0.0


def test_target_qty_opposes_book():
    now = 1_000_000.0
    pos = Position("r", "BTC", 80000.0, int(now + 30 * 86400), 100, 0.1, "t")
    q = target_qty([pos], lambda a: 65000.0, lambda a: 0.5, now)
    # long-touch book -> short perp -> negative target qty
    assert q["BTC"] < 0
