"""Entrypoint: run the trader.

    python scripts/run_trader.py [config.yaml] [--live]

PAPER by default. `--live` is accepted but the live executor/hedge are not wired
(they raise), and the ValidationGate must pass first regardless — both are
deliberate guards against sizing money before the edge survives real asks.
"""
import logging
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trader import Trader  # noqa: E402


def main():
    args = [a for a in sys.argv[1:]]
    live = "--live" in args
    args = [a for a in args if a != "--live"]
    cfg_path = args[0] if args else "config.yaml"
    if not os.path.exists(cfg_path):
        cfg_path = "config.example.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    Trader(cfg, live=live).run()


if __name__ == "__main__":
    main()
