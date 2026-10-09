"""``shape stream``: one table's rows as events, in event-time order (P5-04).

``shape stream`` and ``shape emit`` are one implementation (``cli/emit.py`` and the runtime in
``streaming/emit``): the same options, sinks, formats, delivery guarantees, checkpoint and live
fidelity. What differs is the shape of the stream:

* ``shape stream`` streams **exactly one table** (``--table`` is required, with the short flags
  ``-t``, ``-s``, ``-m``) and delivers it **in event-time order**: the table is generated whole,
  stably sorted by ``_shape_event_time`` and delivered from the earliest. ``--max-events N`` is
  therefore the N earliest events, and ``--out-of-order`` delays events within that sequence.
  ``--rate`` defaults to 10 events per second, as ``--realtime`` pacing of a single table is
  usually slow.
* ``shape emit`` streams the tables of a schema one after another in dependency order, each in
  row order, with memory bounded by a block (a table a post-pass changes is generated whole).

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
from typing import Any

from shape.cli.emit import add_options

# Events per delivery when not paced: one table's events are encoded together, so a larger batch
# amortises the encoder's per-column work (a paced run keeps the runtime's fine-grained batches).
BULK_BATCH = 32768


def add_arguments(sub: Any) -> None:
    """Add the stream command options to an argparse parser."""
    st = sub.add_parser(
        "stream",
        help="stream one table's rows as events in event-time order",
        description="Stream one table of a domain (or of a generation schema file) as JSON-lines "
        "events, earliest event first: as fast as possible (the default) or paced in real time "
        "with --realtime. Each event is the row plus _shape_table, _shape_seq and (when the "
        "table has a date or timestamp column) _shape_event_time; (_shape_table, _shape_seq) is "
        "the idempotency key. Same runtime as `shape emit` (see docs/EMIT.md), which streams "
        "several tables in dependency order instead.",
    )
    add_options(st, stream=True)
    st.set_defaults(by_event_time=True)


def run(a: argparse.Namespace) -> int:
    """Dispatch parsed stream arguments and return the command exit code."""
    from shape.cli.emit import run as run_emit
    from shape.errors import ShapeError

    if len(a.table) != 1:
        raise ShapeError("shape stream streams one table: give --table exactly once")
    if a.batch_events is None and not a.realtime and not a.speed and a.max_rate is None:
        a.batch_events = BULK_BATCH
    return run_emit(a)


__all__ = ["add_arguments", "run"]
