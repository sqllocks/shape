"""DuckDB's approved parent-first inline foreign keys, before any row loads."""

import duckdb
import pyarrow as pa
import pytest
from shape_databases import DuckDbSink, WriteError

from shape.errors import ShapeError
from shape.generation.write_constraints import _PREPARED_CONSTRAINTS


def relation():
    return [
        {
            "name": "fk_child_parent",
            "columns": ["parent_id"],
            "ref_table": "parent",
            "ref_columns": ["id"],
        }
    ]


def plans():
    return {
        "child": {
            "schema": pa.schema(
                [("id", pa.int64()), ("parent_id", pa.int64()), ("amount", pa.int64())]
            ),
            "primary_key": ["id"],
            "foreign_keys": relation(),
            "unique": [["parent_id"]],
            "defaults": {"amount": 4},
            "checks": [
                {
                    "name": "ck_amount",
                    "expression": {
                        "op": ">=",
                        "left": {"column": "amount"},
                        "right": {"literal": 0},
                    },
                }
            ],
        },
        "parent": {"schema": pa.schema([("id", pa.int64())]), "primary_key": ["id"]},
    }


def test_prepare_parent_first_inline_constraints_before_rows(tmp_path):
    path = tmp_path / "constraints.duckdb"
    uri = "duckdb:///" + path.as_posix()
    sink = DuckDbSink()
    declarations = plans()
    sink.prepare_constraints(uri, declarations)
    with duckdb.connect(str(path)) as con:
        assert con.execute("SELECT count(*) FROM parent").fetchone() == (0,)
        assert con.execute("SELECT count(*) FROM child").fetchone() == (0,)
        types = {
            row[0]
            for row in con.execute(
                "SELECT constraint_type FROM duckdb_constraints() WHERE table_name = 'child'"
            ).fetchall()
        }
        assert {"PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK"} <= types
    sink.write(
        uri,
        "parent",
        [pa.RecordBatch.from_pydict({"id": [1]})],
        **declarations["parent"],
        prepared_constraints=_PREPARED_CONSTRAINTS,
    )
    sink.write(
        uri,
        "child",
        [pa.RecordBatch.from_pydict({"id": [1], "parent_id": [1], "amount": [4]})],
        **declarations["child"],
        prepared_constraints=_PREPARED_CONSTRAINTS,
    )
    with duckdb.connect(str(path)) as con:
        con.execute("INSERT INTO child (id, parent_id) VALUES (2, NULL)")
        assert con.execute("SELECT amount FROM child WHERE id=2").fetchone() == (4,)
        for sql in [
            "INSERT INTO child VALUES (3, 1, 4)",
            "INSERT INTO child VALUES (3, NULL, -1)",
            "INSERT INTO child VALUES (3, 999, 4)",
        ]:
            with pytest.raises(duckdb.ConstraintException):
                con.execute(sql)


def test_duckdb_constraint_failure_retains_exact_committed_rows(tmp_path):
    path = tmp_path / "partial.duckdb"
    uri = "duckdb:///" + path.as_posix()
    options = plans()["child"]
    options.pop("foreign_keys")
    options.pop("unique")
    good = pa.RecordBatch.from_pydict({"id": [1], "parent_id": [1], "amount": [4]})
    bad = pa.RecordBatch.from_pydict({"id": [2], "parent_id": [1], "amount": [-1]})
    with pytest.raises(WriteError, match="ck_amount") as error:
        DuckDbSink().write(uri, "child", [good, bad], commit_rows=1, **options)
    assert error.value.rows_committed == 1
    with duckdb.connect(str(path)) as con:
        assert con.execute("SELECT id FROM child").fetchall() == [(1,)]


def test_invalid_duckdb_constraints_refused_before_file_open(tmp_path):
    path = tmp_path / "invalid.duckdb"
    with pytest.raises(ShapeError, match="structured"):
        DuckDbSink().write(
            "duckdb:///" + path.as_posix(),
            "child",
            [],
            schema=pa.schema([("id", pa.int64())]),
            checks=[{"name": "unsafe", "expression": "id > 0"}],
        )
    assert not path.exists()


def test_matching_duckdb_checks_defaults_allow_append_and_changed_check_refuses(tmp_path):
    path = tmp_path / "rerun.duckdb"
    uri = "duckdb:///" + path.as_posix()
    sink = DuckDbSink()
    options = plans()["child"]
    options.pop("foreign_keys")
    options.pop("unique")
    first = pa.RecordBatch.from_pydict({"id": [1], "parent_id": [1], "amount": [4]})
    sink.write(uri, "child", [first], **options)
    second = pa.RecordBatch.from_pydict({"id": [2], "parent_id": [1], "amount": [5]})
    sink.write(uri, "child", [second], write_mode="append", **options)
    options["checks"][0]["expression"]["right"]["literal"] = 3
    with pytest.raises(ShapeError, match="ck_amount"):
        sink.write(uri, "child", [first], write_mode="truncate", **options)
    with duckdb.connect(str(path)) as con:
        assert con.execute("SELECT id FROM child ORDER BY id").fetchall() == [(1,), (2,)]


def test_caller_cannot_spoof_duckdb_preparation(tmp_path):
    path = tmp_path / "spoof.duckdb"
    with pytest.raises(ShapeError, match="internal"):
        DuckDbSink().write(
            "duckdb:///" + path.as_posix(),
            "t",
            [],
            schema=pa.schema([("id", pa.int64())]),
            prepared_constraints=True,
        )
    assert not path.exists()


def test_duckdb_unique_failure_names_generated_constraint_and_prior_commits(tmp_path):
    uri = "duckdb:///" + (tmp_path / "unique.duckdb").as_posix()
    first = pa.RecordBatch.from_pydict({"id": [1], "code": ["x"]})
    second = pa.RecordBatch.from_pydict({"id": [2], "code": ["x"]})
    with pytest.raises(WriteError, match="uq_t_0") as info:
        DuckDbSink().write(uri, "t", [first, second], unique=[["code"]], commit_rows=1)
    assert info.value.rows_committed == 1
