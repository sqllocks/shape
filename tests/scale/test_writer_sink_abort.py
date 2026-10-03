"""A stopped run does not hand a writer a clean end for a table it did not finish
(HUNT2-fabric #718)."""

from __future__ import annotations

import threading

import pytest

from shape.cli.generation import load_target
from shape.generation.engine import Engine
from shape.scale.router import ScaleCancelled, ScaleRouter
from shape.scale.sinks.writer import WriterSink
from tests.scale.test_sinks import batch


class Writer:
    """``write(uri, table, batches, **options)``; records how each table's iterator ended."""

    def __init__(self) -> None:
        self.ended: dict[str, str] = {}
        self.rows: dict[str, int] = {}

    def write(self, uri, table, batches, **options):
        rows = 0
        try:
            for item in batches:
                rows += item.num_rows
        except BaseException:
            self.ended[table] = "error"
            raise
        self.ended[table] = "clean"
        self.rows[table] = rows
        return rows


def test_abort_makes_the_writer_see_an_error_not_a_clean_end():
    writer = Writer()
    sink = WriterSink(writer, "x://y", name="w")
    sink.open(None)
    sink.write_batch("t", batch(0, 5))
    sink.abort()
    sink.close()
    assert writer.ended == {"t": "error"}


def test_a_finished_table_is_unaffected_by_a_later_abort():
    writer = Writer()
    sink = WriterSink(writer, "x://y", name="w")
    sink.open(None)
    sink.write_batch("done", batch(0, 5))
    sink.finish_table("done")
    sink.write_batch("open", batch(0, 3))
    sink.abort()
    sink.close()
    assert writer.ended == {"done": "clean", "open": "error"}
    assert writer.rows == {"done": 5}


def test_close_without_abort_still_finishes_the_open_tables():
    writer = Writer()
    sink = WriterSink(writer, "x://y", name="w")
    sink.open(None)
    sink.write_batch("t", batch(0, 5))
    sink.close()
    assert writer.ended == {"t": "clean"} and writer.rows == {"t": 5}


def test_a_cancelled_run_gives_the_open_table_an_error():
    writer = Writer()
    sink = WriterSink(writer, "x://y", name="w")
    cancel = threading.Event()
    seen = [0]

    def progress(info):
        seen[0] += 1
        if seen[0] == 7:
            cancel.set()

    engine = Engine(load_target("retail", None), scale="small")
    router = ScaleRouter(
        engine, [sink], mode="local_single", chunk_size=400, on_progress=progress, cancel=cancel
    )
    with pytest.raises(ScaleCancelled):
        router.run()
    assert "error" in writer.ended.values()
    for table, how in writer.ended.items():
        if how == "clean":
            assert writer.rows[table] == engine.row_counts[table]
