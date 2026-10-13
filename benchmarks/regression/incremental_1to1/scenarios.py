"""The scenarios both tools run (standard library only; imported from both venvs).

Every scenario starts from the same dataset: retail, small scale, generated once by Shape with
seed 1042 (the T-21 Shape seed). Month 0 of ``shape time-travel retail --scale small --seed 1042``
is that same dataset, so the product command can run unchanged.
"""

from __future__ import annotations

from typing import Any

# T-21: the reference baseline seed, the baseline seeds that give its own spread (exactly these,
# no option to change them: a verdict from any other set counts for nothing) and Shape's seed.
REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
SHAPE_SEED = 1042

DOMAIN = "retail"
SCALE = "small"

ORDER_STATES = {
    "order.status": {
        "processing": {"shipped": 0.7, "cancelled": 0.3},
        "shipped": {"completed": 0.9, "returned": 0.1},
    }
}

# What one `continue` run changes (growth = inserts, churn = deletes, updates, state changes).
CONTINUE: dict[str, dict[str, Any]] = {
    "default": {"inserts": 100, "update_fraction": 0.1, "delete_fraction": 0.02},
    "growth": {"inserts": 2000, "update_fraction": 0.02, "delete_fraction": 0.005},
    "churn": {"inserts": 500, "update_fraction": 0.05, "delete_fraction": 0.25},
    "updates": {"inserts": 0, "update_fraction": 0.5, "delete_fraction": 0.01},
    "transitions": {
        "inserts": 100,
        "update_fraction": 0.4,
        "delete_fraction": 0.02,
        "transitions": ORDER_STATES,
    },
    # A fraction of 0 asks for no change: the reference changes one row per table anyway
    # (CONT-ZERO).
    "zero": {"inserts": 0, "update_fraction": 0.0, "delete_fraction": 0.0},
}

# Monthly evolution: growth, seasonality, churn and updates.
TIME_TRAVEL: dict[str, dict[str, Any]] = {
    "default": {"months": 12, "growth_rate": 0.05, "churn_rate": 0.02, "update_fraction": 0.1},
    "seasonal": {
        "months": 12,
        "growth_rate": 0.05,
        "churn_rate": 0.02,
        "update_fraction": 0.1,
        "seasonality": {11: 1.5, 12: 2.0, 1: 0.5, 7: 1.25},
    },
    "churn": {"months": 12, "growth_rate": 0.02, "churn_rate": 0.10, "update_fraction": 0.2},
    # A growth rate of 0 asks for no new rows: the reference adds one per table per month (TT-ZERO).
    "zero_growth": {"months": 6, "growth_rate": 0.0, "churn_rate": 0.02, "update_fraction": 0.1},
}


def seasonality_arg(scenario: dict[str, Any]) -> str | None:
    season = scenario.get("seasonality")
    if not season:
        return None
    return ",".join(f"{m}={v}" for m, v in sorted(season.items()))
