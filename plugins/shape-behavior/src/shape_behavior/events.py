"""The event stream: the fixed Arrow schema and the buffer the simulator fills."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

TEXT_COLUMNS = (
    "module",
    "state",
    "kind",
    "code",
    "system",
    "display",
    "ref",
    "unit",
    "text",
    "payload",
)
EVENT_SCHEMA = pa.schema(
    [
        pa.field("entity_id", pa.int64(), nullable=False),
        pa.field("seq", pa.int32(), nullable=False),
        pa.field("time", pa.timestamp("us"), nullable=False),
        pa.field("module", pa.string(), nullable=False),
        pa.field("state", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),
        pa.field("code", pa.string()),
        pa.field("system", pa.string()),
        pa.field("display", pa.string()),
        pa.field("ref", pa.string()),
        pa.field("value", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("text", pa.string()),
        pa.field("payload", pa.string()),
    ]
)


class EventBuffer:
    """Collects the events of one ``run_until`` call as integer-coded chunks."""

    def __init__(self) -> None:
        self._strings: dict[str, list[str]] = {c: [] for c in TEXT_COLUMNS}
        self._index: dict[str, dict[str, int]] = {c: {} for c in TEXT_COLUMNS}
        self._chunks: list[dict[str, Any]] = []

    def intern(self, column: str, value: str) -> int:
        idx = self._index[column].get(value)
        if idx is None:
            idx = len(self._strings[column])
            self._strings[column].append(value)
            self._index[column][value] = idx
        return idx

    def _codes(self, column: str, v: Any, n: int) -> Any:
        if v is None:
            return np.full(n, -1, np.int32)
        if isinstance(v, str):
            return np.full(n, self.intern(column, v), np.int32)
        arr = np.asarray(v, dtype=object)
        if len(arr) != n:
            raise ValueError(f"event column {column!r} has {len(arr)} values for {n} rows")
        codes = np.empty(n, np.int32)
        for x in set(arr.tolist()):
            codes[arr == x] = -1 if x is None else self.intern(column, str(x))
        return codes

    def add(self, entity_id: Any, seq: Any, time: Any, columns: dict[str, Any], value: Any) -> None:
        n = len(entity_id)
        if n == 0:
            return
        chunk: dict[str, Any] = {"entity_id": entity_id, "seq": seq, "time": time}
        for column in TEXT_COLUMNS:
            chunk[column] = self._codes(column, columns.get(column), n)
        if value is None:
            chunk["value"] = np.full(n, np.nan)
        else:
            chunk["value"] = np.broadcast_to(np.asarray(value, dtype=np.float64), (n,)).copy()
        self._chunks.append(chunk)

    def table(self) -> Any:
        """The buffered events as an Arrow table sorted by ``(time, entity_id, seq)``."""
        if not self._chunks:
            return EVENT_SCHEMA.empty_table()
        cols = {k: np.concatenate([c[k] for c in self._chunks]) for k in self._chunks[0]}
        order = np.lexsort((cols["seq"], cols["entity_id"], cols["time"]))
        arrays = [
            pa.array(cols["entity_id"][order], pa.int64()),
            pa.array(cols["seq"][order], pa.int32()),
            pa.array(cols["time"][order].astype("datetime64[us]")),
        ]
        for column in TEXT_COLUMNS:
            idx = cols[column][order]
            dictionary = pa.array(self._strings[column], pa.string())
            arrays.append(
                pa.DictionaryArray.from_arrays(
                    pa.array(idx, pa.int32(), mask=idx < 0), dictionary
                ).cast(pa.string())
            )
        value = cols["value"][order]
        arrays.insert(10, pa.array(value, pa.float64(), mask=np.isnan(value)))
        return pa.Table.from_arrays(arrays, schema=EVENT_SCHEMA)
