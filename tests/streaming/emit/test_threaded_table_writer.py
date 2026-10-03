"""``TableEventSink`` over a sink that has no ``open_table``: its ``write`` runs on a thread that
takes the batches as they arrive (``_ThreadedWriter``)."""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE, with_event_fields
from shape.streaming.emit.tables import SYNTHETIC_METADATA, TableEventSink


class WriteOnly:
    """A sink with only ``write``: it records what it took, batch by batch."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.tables: dict[str, list[pa.RecordBatch]] = {}
        self.options: dict[str, Any] = {}
        self.fail_after = fail_after

    def write(
        self, uri: str, table: str, batches: Any, *, schema: pa.Schema, **options: Any
    ) -> int:
        self.options[table] = options
        got = self.tables.setdefault(table, [])
        for batch in batches:
            if self.fail_after is not None and len(got) >= self.fail_after:
                raise OSError("the destination went away")
            assert batch.schema.equals(schema)
            got.append(batch)
        return sum(b.num_rows for b in got)


def events(table: str, n: int, start: int = 0) -> pa.RecordBatch:
    return with_event_fields(pa.record_batch({"v": list(range(start, start + n))}), table, start)


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> WriteOnly:
    target = WriteOnly()
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("mssql", target))
    return target


def test_batches_reach_the_write_thread_split_by_table(sink: WriteOnly) -> None:
    out = TableEventSink("mssql://host/db")
    both = pa.concat_batches([events("a", 3), events("b", 2)])
    out.send(both)
    out.send(events("a", 2, start=3))
    out.flush()  # everything sent so far was taken by the sink
    assert [b.num_rows for b in sink.tables["a"]] == [3, 2]
    assert [b.num_rows for b in sink.tables["b"]] == [2]
    out.close()
    assert out.rows == {"a": 5, "b": 2}
    first = sink.tables["a"][0]
    assert FIELD_TABLE not in first.schema.names and FIELD_SEQ in first.schema.names
    assert first.schema.metadata == SYNTHETIC_METADATA
    assert sink.options["a"]["commit_rows"] == 1  # a database commits every batch


def test_a_resumed_stream_appends(sink: WriteOnly) -> None:
    out = TableEventSink("mssql://host/db", resuming=True, synthetic=False)
    out.send(events("a", 2))
    out.close()
    assert sink.options["a"]["write_mode"] == "append"
    assert not sink.tables["a"][0].schema.metadata


def test_a_failing_write_is_raised_by_flush_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    target = WriteOnly(fail_after=1)
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("mssql", target))
    out = TableEventSink("mssql://host/db")
    out.send(events("a", 2))
    out.send(events("a", 2, start=2))  # the write thread fails on this one
    with pytest.raises(OSError, match="went away"):
        out.flush()
    with pytest.raises(OSError, match="went away"):
        out.send(events("a", 2, start=4))
    with pytest.raises(OSError, match="went away"):
        out.close()


def test_an_event_without_a_table_is_refused(sink: WriteOnly) -> None:
    from shape.errors import ShapeError

    with pytest.raises(ShapeError, match="_shape_table"):
        TableEventSink("mssql://host/db").send(pa.record_batch({"v": [1]}))
