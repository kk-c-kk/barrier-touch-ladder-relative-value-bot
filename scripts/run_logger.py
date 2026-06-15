"""Entrypoint: start the touch-ladder logger.

    python scripts/run_logger.py [config.yaml]

Reads config.yaml (falls back to config.example.yaml), ensures the SQLite parent
dir exists, and runs the snapshot+resolution loop until interrupted.
"""
import logging
import os
import sys

import yaml

# Allow `python scripts/run_logger.py` from the repo root without installing.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.logger import LadderLogger  # noqa: E402


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    if not os.path.exists(cfg_path):
        cfg_path = "config.example.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    db_dir = os.path.dirname(cfg.get("db_path", "data/ladders.sqlite"))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    LadderLogger(cfg).run()


if __name__ == "__main__":
    main()
