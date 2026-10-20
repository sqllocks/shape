"""All-owned constrained reruns retain the old dataset on a later batch failure."""

import pyarrow as pa
import pytest
from shape_fabric import SqlServerSink, WriteError
from shape_fabric.testing import FakeSqlServer

from shape.errors import ShapeError
from shape.generation.engine import Engine
from shape.generation.output import TargetOptions, write_targets
from shape.generation.schema import Column, GenSchema, Model, Relationship, Table


def engine(start):
    parent = Table(
        "parent", {"id": Column("id", "integer", {"strategy": "sequence", "start": start})}, ["id"]
    )
    child = Table(
        "child",
        {
            "id": Column("id", "integer", {"strategy": "sequence", "start": start}),
            "parent_id": Column(
                "parent_id", "integer", {"strategy": "foreign_key", "ref": "parent.id"}
            ),
        },
        ["id"],
    )
    schema = GenSchema(
        Model("tx", "tx"),
        {"parent": parent, "child": child},
        [Relationship("fk_child_parent", "parent", "child", ["id"], ["parent_id"])],
        [],
    )
    return Engine(schema, row_counts={"parent": 3, "child": 3}, chunk_rows=2)


@pytest.mark.parametrize("fail", [False, True])
def test_owned_truncate_transaction(monkeypatch, fail):
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("sqlserver", sink))
    uri = "mssql://db.example/tx"
    write_targets(engine(1), [uri], TargetOptions())
    before = {name: list(server.rows("dbo", name)) for name in ("parent", "child")}
    replacement = engine(10)
    original = replacement.iter_chunks
    planted = []

    def batches(name, size=None):
        for index, batch in enumerate(original(name, size)):
            if fail and name == "child" and index:
                batch = batch.set_column(1, "parent_id", pa.array([999999], type=pa.int64()))
                planted.append(True)
            yield batch

    monkeypatch.setattr(replacement, "iter_chunks", batches)
    if fail:
        with pytest.raises(WriteError, match="fk_child_parent") as error:
            write_targets(replacement, [uri], TargetOptions(write_mode="truncate"))
        assert planted == [True]
        assert error.value.rows_committed == 0
        assert {name: list(server.rows("dbo", name)) for name in before} == before
    else:
        write_targets(replacement, [uri], TargetOptions(write_mode="truncate"))
        assert [row[0] for row in server.rows("dbo", "parent")] == [10, 11, 12]
    current = {name: list(server.rows("dbo", name)) for name in before}
    with pytest.raises(ShapeError, match="fk_child_parent"):
        write_targets(engine(20), [uri], TargetOptions(write_mode="truncate", commit_rows=1))
    assert {name: list(server.rows("dbo", name)) for name in before} == current
