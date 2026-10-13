"""Fixed stream workload, field identity and seeds, shared by both release interpreters."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR  # noqa: E402 (harness path bootstrap)

DOMAIN = "retail"
TABLE = "order"
SCALE = "medium"
REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
SHAPE_SEED = 1042
PREFIX_EVENTS = {"small": 3000, "medium": 100_000}
FIELD_TABLE, FIELD_SEQ, FIELD_TIME = "_shape_table", "_shape_seq", "_shape_event_time"
FIELD_MAP = {name: name for name in (FIELD_TABLE, FIELD_SEQ, FIELD_TIME)}


def out_dir(tool: str, scale: str) -> Path:
    return BENCH_OUT_DIR / "stream" / tool / (DOMAIN + "_" + TABLE + "_" + scale)


def out_file(tool: str, scale: str, seed: int, max_events: int | None = None) -> Path:
    suffix = "" if max_events is None else "_max" + str(max_events)
    return out_dir(tool, scale) / ("seed" + str(seed) + suffix + ".jsonl")
