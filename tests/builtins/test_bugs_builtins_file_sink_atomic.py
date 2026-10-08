"""#297: a file sink replaces the target path instead of writing through a hardlink."""

from __future__ import annotations

import os
import stat

import pyarrow as pa
import pytest

from shape.plugins.host import default_host


@pytest.mark.parametrize("sink_name", ["csv", "jsonl", "parquet"])
def test_planted_hardlink_is_replaced_not_written_through(tmp_path, sink_name):
    sink = default_host().get("shape.sinks", sink_name)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    out = tmp_path / "out"
    out.mkdir()
    os.link(victim, out / f"customer.{sink.extension}")
    t = pa.table({"id": [1, 2]})
    assert sink.write(f"{out}/", "customer", iter(t.to_batches())) == 2
    assert victim.read_text() == "keep me"
    assert (out / f"customer.{sink.extension}").stat().st_size > 0
    assert not [p for p in out.iterdir() if p.name.endswith(".tmp")]


def test_new_file_has_umask_permissions_and_no_temp_left(tmp_path):
    sink = default_host().get("shape.sinks", "csv")
    sink.write(f"{tmp_path}/", "t", iter(pa.table({"id": [1]}).to_batches()))
    mode = stat.S_IMODE((tmp_path / "t.csv").stat().st_mode)
    assert mode != 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["t.csv"]


def test_failed_write_leaves_no_temp_file(tmp_path):
    sink = default_host().get("shape.sinks", "csv")

    def boom():
        yield pa.table({"id": [1]}).to_batches()[0]
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError):
        sink.write(f"{tmp_path}/", "t", boom())
    assert list(tmp_path.iterdir()) == []
