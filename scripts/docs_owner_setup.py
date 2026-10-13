"""Prepare the explicitly documented local inputs for owner transcript examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    """Write the small drift workload shown on the emit page."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=["drift"])
    parser.parse_args()
    plan = {
        "start": "2026-03-01",
        "days": 3,
        "events": [
            {
                "kind": "distribution",
                "table": "order_line",
                "column": "quantity",
                "start": "2026-03-02",
                "params": {"p": {"factor": 0.8}},
            }
        ],
    }
    Path("plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    print("Prepared three-day order_line.quantity drift plan")


if __name__ == "__main__":
    main()
