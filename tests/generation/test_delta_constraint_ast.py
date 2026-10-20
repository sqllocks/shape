"""W9-07 integration of safe schema-derived Delta constraints (depends W9-04)."""

import deltalake
import pyarrow as pa
import pytest

from shape.builtins.sinks import delta
from shape.builtins.sinks.delta import DeltaSink


def batch(ids=(1, 2)):
    return pa.record_batch({"id": list(ids), "label": ["a"] * len(ids)})


def write(tmp_path, **options):
    DeltaSink().write(str(tmp_path), "t", [batch()], **options)
    return deltalake.DeltaTable(str(tmp_path / "t"))


def read(table):
    return pa.table(
        deltalake.QueryBuilder().register("t", table).execute("SELECT * FROM t").read_all()
    )


@pytest.mark.conformance
def test_wanted2_schema_derived_ast_checks_enforced_atomically(tmp_path):
    """SHAPE-SINK-002: the write path consumes schema-derived safe expressions."""
    expression = {"op": ">", "left": {"column": "id"}, "right": {"literal": 0}}
    table = write(tmp_path, constraints={"check": {"positive": expression}})
    assert "delta.constraints.positive" in table.metadata().configuration
    before = table.version()
    with pytest.raises(ValueError, match="positive"):
        DeltaSink().write(str(tmp_path), "t", [batch((0,))], mode="append")
    actual = deltalake.DeltaTable(str(tmp_path / "t"))
    assert actual.version() == before
    assert read(actual).to_pydict() == batch().to_pydict()


def test_wanted2_free_text_check_refused_before_storage(tmp_path, monkeypatch):
    def no_storage(*args, **kwargs):
        pytest.fail("free-text check must be refused before opening storage")

    monkeypatch.setattr(delta, "_location", no_storage)
    with pytest.raises(ValueError, match="constraint.*structured"):
        write(tmp_path, constraints={"check": {"positive": "id > 0"}})
