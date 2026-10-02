"""Structured JSON logging and per-run metrics, available to every command (P4-10).

Standard library only, so importing it costs next to nothing. Turn it on for a run with the global
options ``shape --log-json --metrics RUN.json COMMAND ...`` or the environment variables
``SHAPE_LOG_JSON=1``, ``SHAPE_LOG_LEVEL`` and ``SHAPE_METRICS=FILE``; library code can use it
directly::

    from shape.runlog import configure_logging, RunMetrics

    configure_logging(level="INFO")
    metrics = RunMetrics("20240301_retail_small_s42")
    metrics.start_table("customer")
    metrics.end_table("customer", rows=1000, columns=8)
    metrics.record_event("chaos_injected", category="value", count=12)
    summary = metrics.finish()

Log records are single-line JSON objects (``timestamp``, ``level``, ``logger``, ``message`` and any
``extra`` keys). Metrics are never sent anywhere: they are written to the file you name.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

LOGGER = "shape"
_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, None, None, None).__dict__) | {"message"}


class JsonFormatter(logging.Formatter):
    """Format a log record as one JSON object: ``timestamp``, ``level``, ``logger``, ``message``,
    the record's ``extra`` keys and, when there is one, the ``exception``."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD:
                entry[key] = value
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(
    *, level: str = "INFO", stream: Any = None, logger_name: str = LOGGER
) -> logging.Logger:
    """Send ``logger_name``'s records to ``stream`` (default ``sys.stderr``) as JSON lines.
    Calling it again does not add a second handler."""
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


@dataclass
class TableMetric:
    """Timing and size of one table."""

    table_name: str
    started: float = 0.0
    elapsed_seconds: float = 0.0
    rows: int = 0
    columns: int = 0


@dataclass
class RunMetrics:
    """The operational metrics of one run: per-table timings, events and free-form fields."""

    run_id: str
    _start_time: float = field(default_factory=time.time, repr=False)
    _table_metrics: dict[str, TableMetric] = field(default_factory=dict, repr=False)
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _table_starts: dict[str, float] = field(default_factory=dict, repr=False)
    _fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def start_table(self, table_name: str) -> None:
        self._table_starts[table_name] = time.time()

    def end_table(self, table_name: str, rows: int = 0, columns: int = 0) -> None:
        start = self._table_starts.pop(table_name, time.time())
        self._table_metrics[table_name] = TableMetric(
            table_name, start, round(time.time() - start, 4), rows, columns
        )

    def record_event(self, event_type: str, **kwargs: Any) -> None:
        self._events.append(
            {"type": event_type, "timestamp": datetime.now(UTC).isoformat(), **kwargs}
        )

    def set(self, **fields: Any) -> None:
        """Attach fields to the run (the command, the domain, the rows written ...)."""
        self._fields.update(fields)

    def finish(self) -> dict[str, Any]:
        """The summary as a JSON-ready dict: ``run_id``, ``total_elapsed_seconds``,
        ``total_rows``, ``total_tables``, ``tables``, ``events`` and the fields set."""
        summary: dict[str, Any] = {
            "run_id": self.run_id,
            "total_elapsed_seconds": round(time.time() - self._start_time, 4),
            "total_rows": sum(m.rows for m in self._table_metrics.values()),
            "total_tables": len(self._table_metrics),
            "tables": {
                n: {"elapsed_seconds": m.elapsed_seconds, "rows": m.rows, "columns": m.columns}
                for n, m in self._table_metrics.items()
            },
            "events": self._events,
        }
        summary.update(self._fields)
        return summary

    def to_json(self) -> str:
        return json.dumps(self.finish(), indent=2, default=str)


_current = RunMetrics("")


def begin(run_id: str) -> RunMetrics:
    """Start the metrics of a new run; :func:`current` returns them from now on."""
    global _current
    _current = RunMetrics(run_id)
    return _current


def current() -> RunMetrics:
    """The run in progress. When none was begun the metrics are collected and dropped."""
    return _current


__all__ = [
    "JsonFormatter",
    "RunMetrics",
    "TableMetric",
    "begin",
    "configure_logging",
    "current",
]
