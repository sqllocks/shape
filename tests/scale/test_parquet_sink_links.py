"""AUD-security2 #288: the scale Parquet sink writes only inside its output directory and never
through a link someone planted there."""

from __future__ import annotations

import sys

import pyarrow as pa
import pytest

from shape.scale.sinks.parquet import COMPLETE, ParquetSink

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")


def _write(base, table="customer"):
    sink = ParquetSink(base, chunk_rows=10)
    sink.open(None)
    sink.write_batch(table, pa.RecordBatch.from_pydict({"id": list(range(25))}))
    sink.finish_table(table)
    sink.close()


def test_a_planted_temp_file_link_is_not_written_through(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    out = tmp_path / "out"
    (out / "customer").mkdir(parents=True)
    (out / "customer" / (COMPLETE + ".tmp")).symlink_to(victim)
    (out / "customer" / "part-000000.parquet.tmp").symlink_to(victim)
    _write(out)
    assert victim.read_text() == "keep me"
    assert (out / "customer" / COMPLETE).is_file()


def test_a_table_directory_that_links_outside_is_refused(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (out / "customer").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="leaves"):
        _write(out)
    assert list(elsewhere.iterdir()) == []


def test_the_chunk_worker_does_not_write_through_a_planted_link(tmp_path, monkeypatch):
    import os

    from shape.scale import chunk_worker

    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    out = tmp_path / "out"
    (out / "t").mkdir(parents=True)
    (out / "t" / f"part-000000.parquet.tmp{os.getpid()}").symlink_to(victim)

    class Engine:
        def generate_chunk(self, table, start, rows, chunk):
            return pa.RecordBatch.from_pydict({"id": list(range(start, start + rows))})

        def finalize(self, table, batch):  # the engine's output-type pass (no types declared)
            return batch

    monkeypatch.setattr(chunk_worker, "_engine", lambda spec: Engine())
    chunk_worker.generate_chunk_file({"chunk_rows": 10}, "t", 0, 0, 10, str(out), False)
    assert victim.read_text() == "keep me"
    assert (out / "t" / "part-000000.parquet").is_file()
