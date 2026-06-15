"""Print the validation report from the logger DB.

    python scripts/validate.py [config.yaml]

One Bernoulli trial per resolved rung (deduped), bucketed by price, with win% -
price% and a Wald 95% CI. Bands where |edge| < CI are NOISE. This is the gate the
trader's ValidationGate reads — run it as the data accumulates.
"""
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import format_report, load_trials  # noqa: E402


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    if not os.path.exists(cfg_path):
        cfg_path = "config.example.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    db_path = cfg.get("db_path", "data/ladders.sqlite")
    if not os.path.exists(db_path):
        print(f"no DB at {db_path} yet — run scripts/run_logger.py first")
        return
    trials = load_trials(db_path)
    print(f"resolved trials (deduped, one per rung): {len(trials)}\n")
    print(format_report(trials))


if __name__ == "__main__":
    main()
