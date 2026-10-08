"""A stopped or failed run does not mark the table it was writing complete (HUNT2-fabric #715)."""

from __future__ import annotations

import json
import threading

import pytest

from shape.generation.engine import Engine
from shape.scale.api import run_local
from shape.scale.router import ScaleCancelled
from shape.scale.sinks.parquet import COMPLETE, ParquetSink
from tests.scale.test_sinks import batch


def _request(out):
    return {
        "domain": "retail",
        "scale": "small",
        "scale_mode": "local_single",
        "sinks": ["parquet"],
        "sink_config": {"parquet": {"output_dir": str(out), "chunk_rows": 300}},
        "chunk_size": 400,
    }


def test_a_cancelled_run_marks_only_the_tables_it_finished(tmp_path):
    cancel = threading.Event()
    seen = [0]

    def progress(info):
        seen[0] += 1
        if seen[0] == 8:
            cancel.set()

    with pytest.raises(ScaleCancelled):
        run_local(_request(tmp_path / "out"), cancel, progress)
    from shape.cli.generation import load_target

    expected = Engine(load_target("retail", None), scale="small").row_counts
    marked = {}
    for table in (tmp_path / "out").iterdir():
        marker = table / COMPLETE
        if marker.exists():
            marked[table.name] = json.loads(marker.read_text())["rows"]
    assert marked  # tables that were finished before the stop keep their marker
    assert all(rows == expected[name] for name, rows in marked.items())
    assert "address" not in marked  # it was being written when the run stopped


def test_a_sink_failure_elsewhere_leaves_no_marker_on_the_open_table(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=10)
    sink.open(None)
    sink.write_batch("t", batch(0, 4))
    sink.abort()
    sink.close()
    assert not (tmp_path / "t" / COMPLETE).exists()


def test_parts_written_before_the_stop_are_kept_and_resume_completes_the_table(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=10)
    sink.open(None)
    sink.write_batch("t", batch(0, 25))
    sink.abort()
    sink.close()
    assert len(list((tmp_path / "t").glob("part-*.parquet"))) == 2  # the two full parts
    again = ParquetSink(tmp_path, chunk_rows=10, resume=True)
    again.open(None)
    again.write_batch("t", batch(0, 25))
    again.finish_table("t")
    again.close()
    assert again.parts_skipped == 2
    assert json.loads((tmp_path / "t" / COMPLETE).read_text()) == {"rows": 25, "parts": 3}


def test_close_without_abort_still_finishes_a_table_nobody_finished(tmp_path):
    sink = ParquetSink(tmp_path, chunk_rows=10)
    sink.open(None)
    sink.write_batch("t", batch(0, 4))
    sink.close()
    assert (tmp_path / "t" / COMPLETE).exists()
