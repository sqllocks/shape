"""Windows-style staging cleanup: a live read handle prevents deletion."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from shape_databases import SnowflakeSink
from shape_databases.testing import FakeSnowflake, sample_batch


def test_snowflake_upload_closes_its_staging_reader_before_delete(monkeypatch):
    read_table = pq.read_table
    unlink = Path.unlink
    handles = []

    def read(source, *args, **kwargs):
        # A path-based Dataset scan may return before its background reader closes.
        # Keep that handle alive to make the Windows sharing violation deterministic.
        if isinstance(source, (str, Path)):
            source = pa.OSFile(str(source), "rb")
        handles.append(source)
        return read_table(source, *args, **kwargs)

    def delete(path, *args, **kwargs):
        if path.suffix == ".parquet" and any(not handle.closed for handle in handles):
            raise PermissionError("Windows sharing violation: staging reader is still open")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(pq, "read_table", read)
    monkeypatch.setattr(Path, "unlink", delete)
    server = FakeSnowflake()
    try:
        assert (
            SnowflakeSink(connect=server.connect).write(
                "snowflake://shape@acct/DB", "customer", iter([sample_batch()])
            )
            == 3
        )
        assert handles and all(handle.closed for handle in handles)
        assert len(server.rows("customer")) == 3
        assert all(not path.exists() for path in server.put_paths)
    finally:
        for handle in handles:
            handle.close()
