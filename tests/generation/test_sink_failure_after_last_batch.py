"""W2-10: a sink that fails *after* it consumed its last batch (a constraint check that runs when
the load is over) must end the run with its error, not leave it waiting for a batch that never
comes. ``write_targets`` and the emit table writer both drain their queue after a sink error, and
used to wait for an end marker the sink had already taken."""

from __future__ import annotations

import threading

import pyarrow as pa
import pytest

from shape.generation.engine import Engine
from shape.generation.output import TargetOptions, write_targets
from shape.generation.schema import GenSchema
from shape.streaming.emit.tables import _ThreadedWriter


DOC = {
    "schema_version": 1,
    "model": {"name": "t", "seed": 5},
    "tables": {
        "t": {
            "name": "t",
            "primary_key": ["id"],
            "columns": {
                "id": {
                    "name": "id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                    "nullable": False,
                    "null_rate": 0.0,
                }
            },
        }
    },
    "relationships": [],
    "generation": {"scale": "small", "scales": {"small": {"t": 5000}}},
}


def engine():
    return Engine(GenSchema.from_dict(DOC), scale="small", seed=3)


class FailsAtTheEnd:
    name = "fake"
    schemes = ("fake",)

    def write(self, uri, table, batches, **options):
        rows = sum(b.num_rows for b in batches)  # consumes everything, including the end marker
        raise RuntimeError(f"{table}: {rows} rows loaded, then a check failed")


def within(seconds, fn):
    box = {}

    def run():
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - the test reports what the call raised
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "the call did not return: it waits for a batch that never comes"
    return box.get("error")


def test_write_targets_reports_a_sink_that_fails_after_the_last_batch(monkeypatch):
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("fake", FailsAtTheEnd()))
    error = within(20, lambda: write_targets(engine(), ["fake://x/y"], TargetOptions()))
    assert isinstance(error, RuntimeError) and "then a check failed" in str(error)


def test_the_emit_table_writer_reports_a_sink_that_fails_after_the_last_batch():
    schema = pa.schema([("a", pa.int64())])
    writer = _ThreadedWriter(FailsAtTheEnd(), "fake://x", "t", schema, {})
    writer.write_batch(pa.record_batch([[1, 2]], schema=schema))

    def finish():
        writer.close()

    error = within(20, finish)
    assert isinstance(error, RuntimeError) and "then a check failed" in str(error)


def test_a_sink_that_fails_before_the_end_still_drains_the_producer(monkeypatch):
    class FailsEarly:
        def write(self, uri, table, batches, **options):
            next(iter(batches))
            raise RuntimeError("early")

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("fake", FailsEarly()))
    error = within(20, lambda: write_targets(engine(), ["fake://x/y"], TargetOptions()))
    assert isinstance(error, RuntimeError) and str(error) == "early"
