"""``ShapeMonitor``: rows arrive one at a time and, every ``every`` rows, the buffered rows are
compared with a reference by the drift engine (the rules and thresholds of ``shape.diff``)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shape.drift import Drift, compare
from shape.streaming.online import OnlineShape


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    """The comparison at one check. ``drifts`` are the changes that passed their threshold, each
    with its column, kind, severity and score; ``max_drift`` is the largest score (0 when
    nothing moved)."""

    rows_seen: int
    max_drift: float
    drifts: tuple[Drift, ...]

    @property
    def changes(self) -> list[dict[str, Any]]:
        """The drifts as ``shape.diff`` change records."""
        return [d.to_change() for d in self.drifts]


def _profile_of(rows: Iterable[Mapping[str, Any]]) -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    import shape

    rows = list(rows)
    names = list(dict.fromkeys(k for r in rows for k in r))
    columns = {}
    for name in names:
        values = [r.get(name) for r in rows]
        try:
            columns[name] = pa.array(values)
        except (pa.ArrowInvalid, pa.ArrowTypeError):  # mixed types: compare them as text
            columns[name] = pa.array([None if v is None else str(v) for v in values])
    return shape.profile(pa.table(columns), name="stream")


class ShapeMonitor:
    """Watch a stream of rows against ``reference`` (a profile, a v2 model or a v1 capture). The
    optional arguments are ``shape.diff``'s: ``thresholds``, ``ignore_columns``,
    ``column_thresholds``, ``only_columns`` and ``policy``."""

    def __init__(
        self,
        reference: Any,
        every: int = 1000,
        max_buffer: int = 10000,
        *,
        thresholds: Mapping[str, Any] | None = None,
        ignore_columns: Iterable[str] | None = None,
        column_thresholds: Mapping[str, Mapping[str, Any]] | None = None,
        only_columns: Iterable[str] | None = None,
        policy: Mapping[str, Any] | str | Path | None = None,
    ) -> None:
        if every < 1:
            raise ValueError("every")
        self.reference = reference
        self.every = every
        self.online = OnlineShape(max_buffer)
        self._options: dict[str, Any] = {
            "thresholds": thresholds,
            "ignore_columns": ignore_columns,
            "column_thresholds": column_thresholds,
            "only_columns": only_columns,
            "policy": policy,
        }

    def _snapshot(self) -> Any:
        # a profile reference is compared with a profile of the buffered rows, a model or capture
        # with a capture of them: the same kind of statistics on both sides
        if hasattr(self.reference, "is_dataset"):
            return _profile_of(self.online.buffer)
        return self.online.snapshot()

    def add(self, row: Mapping[str, Any]) -> MonitorEvent | None:
        self.online.add(row)
        if self.online.total % self.every:
            return None
        d = tuple(compare(self.reference, self._snapshot(), **self._options))
        return MonitorEvent(self.online.total, max((x.score for x in d), default=0), d)
