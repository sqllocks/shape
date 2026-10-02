"""Shared definitions of the STREAM-EMIT harness (P5-04): the workload, the seeds, the
baseline-name to Shape-name field map (D-13) and the named allow-list of baseline defects that
Shape fixes. Standard library only: imported from both the baseline venv and the Shape venv.

The workload (plan section 3.4, STREAM-EMIT)::

    "$SPINDLE_VENV/bin/spindle" stream retail --table order --scale medium --no-realtime \\
        --sink file -o F --seed 42 [--max-events N]
    "$SHAPE_VENV/bin/shape"     stream retail --table order --scale medium --no-realtime \\
        --sink file -o F --seed 1042 [--max-events N]
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR  # noqa: E402

DOMAIN = "retail"
TABLE = "order"
SCALE = "medium"

# T-21: the reference seed, the baseline's own spread (exactly 43-46) and the implementation's.
REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
SHAPE_SEED = 1042

# A prefix run (the first N events in event-time order) is verified as well as the full table.
PREFIX_EVENTS = {"small": 3000, "medium": 100_000}

# D-13: the baseline's field names and the Shape names they stand for. The harness maps a
# baseline name to Shape's before it compares names and order; nothing user-facing names the
# baseline.
FIELD_MAP: dict[str, str] = {
    "_spindle_table": "_shape_table",
    "_spindle_seq": "_shape_seq",
    "_spindle_event_time": "_shape_event_time",
}
FIELD_TABLE, FIELD_SEQ, FIELD_TIME = FIELD_MAP.values()

# Trust-harming defects of the baseline that Shape fixes (owner's standing decision, 2026-10-01).
# Each is a narrow, named exception; anything else that differs fails the verifier. Every entry
# is shown by a probe in verify.py (a stale entry fails it too).
ALLOWED: dict[str, dict[str, str]] = {
    "ST-1": {
        "what": "--anomaly-fraction",
        "baseline": "the flag is accepted and silently ignored (its stream command passes no "
        "anomaly registry when the fraction is above 0): the output is identical with and "
        "without it, and the summary reports 0 anomalies",
        "shape": "the flag is honoured: about that share of the events carry mutated values, "
        "the same ones on every run",
    },
}


def out_dir(tool: str, scale: str) -> Path:
    return BENCH_OUT_DIR / "stream" / tool / f"{DOMAIN}_{TABLE}_{scale}"


def out_file(tool: str, scale: str, seed: int, max_events: int | None = None) -> Path:
    suffix = "" if max_events is None else f"_max{max_events}"
    return out_dir(tool, scale) / f"seed{seed}{suffix}.jsonl"
