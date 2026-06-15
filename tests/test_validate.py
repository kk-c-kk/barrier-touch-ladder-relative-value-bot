"""Validation harness: dedup-to-one-trial-per-rung + CI math."""
import sqlite3

from src.analysis import load_trials, overall


def _make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE snapshots (ts INTEGER, rung_slug TEXT, asset TEXT, strike REAL,
            spot REAL, t_remaining_s INTEGER, yes_ask REAL, yes_bid REAL, yes_mid REAL,
            realized_vol REAL, model_fair REAL, implied_vol REAL);
        CREATE TABLE resolutions (rung_slug TEXT PRIMARY KEY, outcome TEXT, resolved_ts INTEGER);
    """)
    # rung A: 3 snapshots (must collapse to ONE trial), TOUCHED, earliest ask 0.20
    con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        (100, "A", "BTC", 80000, 65000, 9999, 0.20, 0.19, 0.195, 0.45, 0.25, 0.5),
        (160, "A", "BTC", 80000, 65000, 9939, 0.22, 0.21, 0.215, 0.45, 0.25, 0.5),
        (220, "A", "BTC", 80000, 65000, 9879, 0.30, 0.29, 0.295, 0.45, 0.25, 0.5),
    ])
    # rung B: 1 snapshot, MISS, ask 0.50
    con.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (100, "B", "BTC", 90000, 65000, 9999, 0.50, 0.49, 0.495, 0.45, 0.40, 0.6))
    con.executemany("INSERT INTO resolutions VALUES (?,?,?)", [
        ("A", "TOUCHED", 11000), ("B", "MISS", 11000),
    ])
    con.commit()
    con.close()


def test_dedup_one_trial_per_rung(tmp_path):
    db = str(tmp_path / "t.sqlite")
    _make_db(db)
    trials = load_trials(db, entry="earliest")
    assert len(trials) == 2                       # 4 snapshots -> 2 rungs
    a = next(t for t in trials if t.rung_slug == "A")
    assert a.price == 0.20 and a.won == 1          # earliest ask, TOUCHED
    b = next(t for t in trials if t.rung_slug == "B")
    assert b.price == 0.50 and b.won == 0


def test_latest_entry_picks_last_ask(tmp_path):
    db = str(tmp_path / "t.sqlite")
    _make_db(db)
    a = next(t for t in load_trials(db, entry="latest") if t.rung_slug == "A")
    assert a.price == 0.30


def test_overall_edge_and_ci(tmp_path):
    db = str(tmp_path / "t.sqlite")
    _make_db(db)
    o = overall(load_trials(db))
    assert o.n == 2
    # win rate 0.5 (A touched, B missed), mean price (0.20+0.50)/2 = 0.35
    assert abs(o.win_rate - 0.5) < 1e-9
    assert abs(o.mean_price - 0.35) < 1e-9
    assert abs(o.edge - 0.15) < 1e-9
    assert o.ci_half > 0
