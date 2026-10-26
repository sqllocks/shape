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


@pytest.mark.parametrize("mode", ["truncate", "replace"])
@pytest.mark.parametrize("fail", [False, True])
def test_owned_truncate_transaction(monkeypatch, fail, mode):
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
            write_targets(replacement, [uri], TargetOptions(write_mode=mode))
        assert planted == [True]
        assert error.value.rows_committed == 0
        assert {name: list(server.rows("dbo", name)) for name in before} == before
    else:
        write_targets(replacement, [uri], TargetOptions(write_mode=mode))
        assert [row[0] for row in server.rows("dbo", "parent")] == [10, 11, 12]
    current = {name: list(server.rows("dbo", name)) for name in before}
    with pytest.raises(ShapeError, match="fk_child_parent"):
        write_targets(engine(20), [uri], TargetOptions(write_mode=mode, commit_rows=1))
    assert {name: list(server.rows("dbo", name)) for name in before} == current


def test_mixed_existing_new_preflights_external_references_before_mutation():
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    uri = "mssql://db.example/tx"
    data = pa.RecordBatch.from_pydict({"id": [2]})
    for name in ("a", "b"):
        sink.write(uri, name, [data], primary_key=["id"])
    sink.write(
        uri,
        "external",
        [data],
        foreign_keys=[
            {"name": "fk_external_ref", "columns": ["id"], "ref_table": "b", "ref_columns": ["id"]}
        ],
    )
    options = {"schema": data.schema, "write_mode": "truncate"}
    count = len(server.statements)
    with pytest.raises(ShapeError, match="fk_external_ref"):
        sink.prepare_constraints(uri, {"a": options, "b": options, "missing": options})
    assert server.rows("dbo", "a") == server.rows("dbo", "b") == [(2,)]
    assert not [
        sql
        for sql in server.statements[count:]
        if sql.startswith(("TRUNCATE", "DELETE", "DROP", "CREATE"))
    ]


def test_retained_fabric_commit_error_is_sanitized(monkeypatch):
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("sqlserver", sink))
    uri = "mssql://db.example/tx"
    initial = engine(1)
    write_targets(initial, [uri], TargetOptions())
    generated = engine(10).generate()
    options = TargetOptions(write_mode="truncate")
    transaction = sink.prepare_constraints(
        uri,
        {
            table: options.for_sink("sqlserver", initial.schema, table) | {"schema": data.schema}
            for table, data in generated.tables.items()
        },
    )
    assert transaction is not None

    def fail_commit():
        raise RuntimeError("commit failed PWD=benign_fabric_commit_secret;")

    transaction.connection.commit = fail_commit
    with pytest.raises(WriteError) as error:
        transaction.finish(True)
    assert "benign_fabric_commit_secret" not in str(error.value)
    assert error.value.rows_committed == 0
    assert error.value.__context__ is None
