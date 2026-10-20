"""Constraint DDL, preconnection validation, phase order and retained-row errors."""

import pyarrow as pa
import pytest
from shape_databases import WriteError

from shape.errors import ShapeError
from shape.generation.write_constraints import _PREPARED_CONSTRAINTS


def batch():
    return pa.RecordBatch.from_pydict({"id": [1], "parent_id": [1], "amount": [3]})


def declarations():
    return {
        "unique": [["id", "parent_id"]],
        "defaults": {"amount": 2},
        "checks": [
            {
                "name": "ck_amount",
                "expression": {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}},
            }
        ],
    }


def foreign():
    return [
        {
            "name": "fk_child_parent",
            "columns": ["parent_id"],
            "ref_table": "parent",
            "ref_columns": ["id"],
        }
    ]


def test_new_table_ddl_contains_unique_structured_checks_and_literal_defaults(flavour):
    sink, uri, server = flavour
    assert sink.write(uri, "child", [batch()], **declarations()) == 1
    ddl = next(sql for sql in server.statements() if sql.startswith("CREATE TABLE"))
    assert "UNIQUE" in ddl and "CHECK" in ddl and "ck_amount" in ddl
    assert "DEFAULT 2" in ddl


@pytest.mark.parametrize(
    "options",
    [
        {"checks": [{"name": "ck", "expression": "amount > 0"}]},
        {"unique": [["missing"]]},
        {"defaults": {"amount": float("nan")}},
    ],
)
def test_bad_constraints_fail_before_any_connection(flavour, options):
    sink, uri, server = flavour
    with pytest.raises(ShapeError):
        sink.write(uri, "child", [batch()], **options)
    assert server.events == []


def test_prepare_all_creates_both_tables_then_fk_before_any_data(flavour):
    sink, uri, server = flavour
    options = {"schema": batch().schema, "first_batch": batch(), "primary_key": ["id"]}
    sink.prepare_constraints(
        uri,
        {
            "parent": options,
            "child": options | declarations() | {"foreign_keys": foreign()},
        },
    )
    statements = server.statements()
    creates = [i for i, sql in enumerate(statements) if sql.startswith("CREATE TABLE")]
    fk = next(i for i, sql in enumerate(statements) if "ADD CONSTRAINT" in sql)
    assert len(creates) == 2 and max(creates) < fk
    assert server.rows("parent") == [] and server.rows("child") == []
    for table in ["parent", "child"]:
        opts = options | (declarations() | {"foreign_keys": foreign()} if table == "child" else {})
        opts.pop("first_batch")
        assert (
            sink.write(uri, table, [batch()], prepared_constraints=_PREPARED_CONSTRAINTS, **opts)
            == 1
        )
    assert len([sql for sql in server.statements() if sql.startswith("CREATE TABLE")]) == 2


def test_prepare_validates_every_table_before_any_connection(flavour):
    sink, uri, server = flavour
    opts = {"schema": batch().schema}
    with pytest.raises(ShapeError):
        sink.prepare_constraints(uri, {"parent": opts, "child": opts | {"unique": [["missing"]]}})
    assert server.events == []


def test_constraint_driver_error_keeps_constraint_name_and_exact_committed_rows(flavour):
    sink, uri, server = flavour
    server.fail_after_rows = 1
    server.fail_message = 'constraint "ck_amount" violated at row 2'
    rows = pa.RecordBatch.from_pydict({"id": [1, 2], "parent_id": [1, 1], "amount": [3, -1]})
    with pytest.raises(WriteError, match="ck_amount.*row 2") as error:
        sink.write(uri, "child", [rows], commit_rows=1, **declarations())
    assert error.value.rows_committed == 1
    assert len(server.committed_rows("child")) == 1


@pytest.mark.parametrize("mode", ["replace", "truncate"])
def test_invalid_ast_is_refused_before_existing_table_can_be_mutated(flavour, mode):
    sink, uri, server = flavour
    sink.write(uri, "child", [batch()])
    events = list(server.events)
    rows = list(server.rows("child"))
    with pytest.raises(ShapeError, match="structured"):
        sink.write(
            uri,
            "child",
            [batch()],
            write_mode=mode,
            checks=[{"name": "ck_bad", "expression": "amount > 0"}],
        )
    assert server.events == events
    assert server.rows("child") == rows


def test_preparation_accepts_equal_resolved_password_material(flavour):
    sink, uri, server = flavour
    opts = {"schema": batch().schema, "password": "fake-prepare-password"}
    sink.prepare_constraints(uri, {"parent": opts, "child": opts})
    assert len([sql for sql in server.statements() if sql.startswith("CREATE TABLE")]) == 2


def test_preparation_refuses_different_auth_before_connecting(flavour):
    sink, uri, server = flavour
    opts = {"schema": batch().schema, "password": "fake-first-password"}
    with pytest.raises(ShapeError, match="same connection options") as info:
        sink.prepare_constraints(
            uri, {"parent": opts, "child": opts | {"password": "fake-other-password"}}
        )
    assert "fake-first-password" not in str(info.value)
    assert "fake-other-password" not in str(info.value)
    assert server.events == []


def test_existing_constraints_refusal_precedes_truncate(flavour):
    sink, uri, server = flavour
    sink.write(uri, "child", [batch()])
    before = list(server.rows("child"))
    with pytest.raises(ShapeError, match="requested constraints cannot yet be verified"):
        sink.write(uri, "child", [batch()], write_mode="truncate", **declarations())
    assert server.rows("child") == before
    assert not any(sql.startswith("TRUNCATE") for sql in server.statements())


@pytest.mark.parametrize("mode", ["replace", "truncate"])
def test_referenced_table_refusal_names_constraint_and_preserves_rows(flavour, mode):
    sink, uri, server = flavour
    server.seed("parent", [("id", "bigint", False)], [(1,)], primary_key=["id"])
    server.seed(
        "child",
        [("parent_id", "bigint", False)],
        [],
        foreign_keys=[("parent", ["id"], ["parent_id"])],
    )
    rows = pa.RecordBatch.from_pydict({"id": [2]})
    with pytest.raises(ShapeError, match="fk_child_0"):
        sink.write(uri, "parent", [rows], write_mode=mode)
    assert server.rows("parent") == [(1,)]
    assert not any(sql.startswith(("DROP TABLE", "TRUNCATE")) for sql in server.statements())


def test_caller_cannot_spoof_prepared_constraints(flavour):
    sink, uri, server = flavour
    with pytest.raises(ShapeError, match="internal"):
        sink.write(uri, "child", [batch()], prepared_constraints=True)
    assert server.events == []
