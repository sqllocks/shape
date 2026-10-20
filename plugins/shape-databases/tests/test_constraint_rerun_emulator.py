"""W9-04: coordinated reruns preserve the complete prior dataset on failure."""

import os
import uuid

import pyarrow as pa
import pytest
from shape_databases import WriteError

from shape.errors import ShapeError
from shape.generation.engine import Engine
from shape.generation.output import TargetOptions, write_targets
from shape.generation.schema import Column, GenSchema, Model, Relationship, Table

pytestmark = pytest.mark.emulator


def definition(start):
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
            "amount": Column("amount", "integer", {"strategy": "constant", "value": 2}),
        },
        ["id"],
    )
    return GenSchema(
        Model("rerun", "rerun"),
        {"parent": parent, "child": child},
        [Relationship("fk_child_parent", "parent", "child", ["id"], ["parent_id"])],
        [],
    )


def engine(start):
    return Engine(definition(start), row_counts={"parent": 3, "child": 3}, seed=42, chunk_rows=2)


@pytest.fixture
def rerun_database():
    import psycopg

    conn = psycopg.connect(
        host="localhost",
        user="shape",
        dbname="shape",
        password=os.environ["SHAPE_POSTGRES_PASSWORD"],
        autocommit=True,
    )
    prefix = "rr_" + uuid.uuid4().hex[:10] + "_"
    uri = "postgresql://shape@localhost:5432/shape"

    def execute(sql):
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall() if cursor.description else []

    def rows():
        return {
            name: list(execute(f'SELECT * FROM "{prefix}{name}" ORDER BY id'))
            for name in ("parent", "child")
        }

    def options(**kwargs):
        return TargetOptions(
            constraints="all", extra={"postgres": {"table_prefix": prefix}}, **kwargs
        )

    try:
        assert write_targets(engine(1), [uri], options()) == {uri: {"parent": 3, "child": 3}}
        yield uri, prefix, execute, rows, options
    finally:
        for name in ("external", "child", "parent"):
            execute(f'DROP TABLE IF EXISTS "{prefix}{name}"')
        conn.close()


def test_all_owned_truncate_rerun_keeps_constraints_and_replaces_all_rows(rerun_database):
    uri, _, _, rows, options = rerun_database
    assert write_targets(engine(10), [uri], options(write_mode="truncate")) == {
        uri: {"parent": 3, "child": 3}
    }
    found = rows()
    assert found["parent"] == [(10,), (11,), (12,)]
    assert [row[0] for row in found["child"]] == [10, 11, 12]
    assert {row[1] for row in found["child"]} <= {10, 11, 12}


def test_later_child_failure_rolls_back_the_whole_coordinated_rerun(rerun_database, monkeypatch):
    uri, _, _, rows, options = rerun_database
    before = rows()
    replacement = engine(10)
    original = replacement.iter_chunks
    planted_rows = []

    def planted(name, size=None):
        for number, batch in enumerate(original(name, size)):
            if name == "child" and number:
                index = batch.schema.get_field_index("parent_id")
                batch = batch.set_column(
                    index, "parent_id", pa.array([999999] * batch.num_rows, type=pa.int64())
                )
                planted_rows.append(batch.num_rows)
            yield batch

    monkeypatch.setattr(replacement, "iter_chunks", planted)
    with pytest.raises(WriteError, match="fk_child_parent") as error:
        write_targets(replacement, [uri], options(write_mode="truncate"))
    assert planted_rows == [1]
    assert error.value.rows_committed == 0
    assert rows() == before


def test_incompatible_chunk_commits_refuse_before_coordinated_mutation(rerun_database):
    uri, _, _, rows, options = rerun_database
    before = rows()
    with pytest.raises(ShapeError, match="fk_child_parent"):
        write_targets(engine(10), [uri], options(write_mode="truncate", commit_rows=1))
    assert rows() == before


def test_external_incoming_foreign_key_is_named_and_every_table_is_preserved(rerun_database):
    uri, prefix, execute, rows, options = rerun_database
    execute(
        f'CREATE TABLE "{prefix}external" (parent_id BIGINT, CONSTRAINT fk_external_ref '
        f'FOREIGN KEY (parent_id) REFERENCES "{prefix}parent" (id))'
    )
    execute(f'INSERT INTO "{prefix}external" VALUES (1)')
    before = rows()
    with pytest.raises(ShapeError, match="fk_external_ref"):
        write_targets(engine(10), [uri], options(write_mode="truncate"))
    assert rows() == before
    assert list(execute(f'SELECT parent_id FROM "{prefix}external"')) == [(1,)]
