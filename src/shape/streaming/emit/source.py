"""The deterministic event sequence of a generation schema (P5-01).

An :class:`EventPlan` is a pure function of the schema, the seed, the scale and the stream
options: the same plan always yields the same events in the same order, and it can start at any
*offset* (the number of events already delivered) without replaying what came before. That is what
makes delivery at-least-once with a checkpoint: after a restart the plan resumes at the
checkpoint's offset and the events after it are the ones the first run would have sent.

* Tables are streamed one after the other, in dependency order (parents first); within a table,
  rows in row order. A row is event number ``_shape_seq`` of its table.
* Rows come from the generation engine. A table that a post-pass changes (computed columns,
  rule repair, correlated columns) is generated whole, once, so its rows equal ``shape generate``'s;
  every other table is read chunk by chunk (random access), so memory stays bounded.
* ``out_of_order``: each row is, with that probability, delivered late: moved later within its
  *window* of ``ooo_window`` rows by 1 to ``ooo_window`` positions. Its event time does not change.
  The draw is by row, so it does not depend on chunking.
* ``anomaly``: see :mod:`shape.streaming.emit.anomaly`; applied to the rows before they are
  reordered.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.rng import RowStream
from shape.streaming.emit.anomaly import AnomalyInjector
from shape.streaming.emit.formats import with_event_fields

if TYPE_CHECKING:
    from shape.generation.engine import Engine

BLOCK_TARGET = 8192


@dataclass(frozen=True, slots=True)
class EventBlock:
    """Events ``offset .. offset + num_rows - 1`` of the whole sequence, all of one table."""

    offset: int
    table: str
    batch: pa.RecordBatch

    @property
    def num_rows(self) -> int:
        return int(self.batch.num_rows)


class EventPlan:
    def __init__(
        self,
        engine: Engine,
        *,
        tables: Sequence[str] | None = None,
        out_of_order: float = 0.0,
        ooo_window: int = 1000,
        anomaly: AnomalyInjector | None = None,
        envelope: str = "flat",
    ) -> None:
        if not 0.0 <= out_of_order <= 1.0:
            raise ValueError("out-of-order fraction must be between 0 and 1")
        if ooo_window < 1:
            raise ValueError("out-of-order window must be at least 1")
        engine.schema.validate_or_raise()
        order = engine.order
        if tables:
            unknown = [t for t in tables if t not in engine.schema.tables]
            if unknown:
                raise ShapeError(
                    f"unknown table {unknown[0]!r}; the schema has {', '.join(sorted(order))}"
                )
            wanted = set(tables)
            order = [t for t in order if t in wanted]
        self.engine = engine
        self.tables = order
        self.out_of_order = float(out_of_order)
        self.ooo_window = int(ooo_window)
        self.block_rows = self.ooo_window * max(1, BLOCK_TARGET // self.ooo_window)
        self.anomaly = anomaly
        self.envelope = envelope
        self.counts = {t: int(engine.row_counts.get(t, 0)) for t in order}
        self.starts: dict[str, int] = {}
        total = 0
        for t in order:
            self.starts[t] = total
            total += self.counts[t]
        self.total_events = total
        self._whole: dict[str, pa.Table] | None = None
        self._touched = engine._post_pass_tables()

    # ---- identity -----------------------------------------------------------------------

    def fingerprint(self) -> str:
        """A hash of everything that decides the event sequence; a checkpoint of another
        fingerprint is refused."""
        document: dict[str, Any] = {
            "schema": json.dumps(self.engine.schema.to_dict(), sort_keys=True, default=str),
            "seed": self.engine.seed,
            "counts": self.counts,
            "tables": self.tables,
            "out_of_order": self.out_of_order,
            "ooo_window": self.ooo_window,
            "anomaly": 0.0 if self.anomaly is None else self.anomaly.fraction,
            "mutators": [] if self.anomaly is None else [m.name for m in self.anomaly.mutators],
            "envelope": self.envelope,
        }
        blob = json.dumps(document, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()

    # ---- rows ---------------------------------------------------------------------------

    def _rows(self, table: str, start: int, n: int) -> pa.RecordBatch:
        if table not in self._touched:
            return self.engine.generate_chunk(table, start, n)
        if self._whole is None:
            self._whole = {
                t: tab for t, tab in self.engine.generate().tables.items() if t in self._touched
            }
        tab = self._whole[table].slice(start, n)
        return pa.RecordBatch.from_arrays(
            [c.combine_chunks() for c in tab.columns], schema=tab.schema
        )

    def _reorder(self, table: str, row_start: int, n: int) -> np.ndarray | None:
        """The permutation that delivers late the rows chosen to be late, or ``None``."""
        if self.out_of_order <= 0 or n < 2:
            return None
        w = self.ooo_window
        pick = RowStream(self.engine.seed, table, "_ooo", "late").uniform(row_start, n)
        shift = RowStream(self.engine.seed, table, "_ooo", "shift").uniform(row_start, n)
        late = pick < self.out_of_order
        if not late.any():
            return None
        index = np.arange(n)
        position = index.astype(np.float64)
        position[late] += 1.5 + np.floor(shift[late] * w)
        # A block starts on a window edge, so a row's window is its index // w. A late row stays in
        # its window (the cap is past every other position in it); ties keep row order.
        window_of = index // w
        position = np.minimum(position, (window_of + 1) * w - 0.25)
        order = np.lexsort((index, position, window_of))
        return order

    def block(self, table: str, index: int) -> EventBlock:
        """Block ``index`` of ``table`` (rows ``index * block_rows ..``) as events."""
        start = index * self.block_rows
        n = min(self.block_rows, self.counts[table] - start)
        batch = self._rows(table, start, n)
        if self.anomaly is not None:
            batch = self.anomaly.apply(batch, table, start)
        events = with_event_fields(batch, table, start)
        order = self._reorder(table, start, n)
        if order is not None:
            events = events.take(pa.array(order))
        return EventBlock(self.starts[table] + start, table, events)

    def locate(self, offset: int) -> tuple[str, int] | None:
        """``(table, block index)`` of the block holding event ``offset``, or ``None`` past the
        end."""
        for t in self.tables:
            if offset < self.starts[t] + self.counts[t]:
                return t, max(0, (offset - self.starts[t]) // self.block_rows)
        return None

    def blocks(self, offset: int = 0) -> Iterator[EventBlock]:
        """The blocks from event ``offset`` on; the first is cut so it starts exactly there."""
        if offset < 0:
            raise ValueError("offset must be 0 or more")
        for t in self.tables:
            end = self.starts[t] + self.counts[t]
            if offset >= end:
                continue
            first = max(0, (offset - self.starts[t]) // self.block_rows)
            for index in range(first, -(-self.counts[t] // self.block_rows)):
                blk = self.block(t, index)
                if blk.offset < offset:
                    cut = offset - blk.offset
                    blk = EventBlock(offset, t, blk.batch.slice(cut))
                yield blk
