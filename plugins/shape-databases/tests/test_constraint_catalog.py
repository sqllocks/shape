"""Quoted catalog literals must stay opaque when comparing server expressions."""

import copy

import pyarrow as pa
import pytest

from shape.errors import ShapeError


def test_catalog_comparison_never_removes_literal_whitespace_or_parentheses():
    from shape_databases._constraint_catalog import _expression

    assert _expression("((amount >= 0))") == _expression("amount>=0")
    assert _expression("status = 'a b'") != _expression("status = 'ab'")
    assert _expression("status = '(a)'") != _expression("status = 'a'")


def test_mysql_show_create_only_reads_real_constraint_tokens():
    from shape_databases._constraint_catalog import _mysql_checks

    ddl = """CREATE TABLE `t` (
        `text` varchar(80) DEFAULT 'CONSTRAINT `fake` CHECK (1)',
        CONSTRAINT `real``name` CHECK ((`text` <> 'a) b'))
    )"""
    checks = _mysql_checks(ddl, backslashes=True)
    assert set(checks) == {"real`name"}
    assert "'a) b'" in checks["real`name"]


def test_mysql_backslash_modes_preserve_quoted_check_literals():
    from shape_databases._constraint_catalog import _mysql_checks

    for backslashes, value in [(True, r"'a\' b'"), (False, "'a\\'' b'")]:
        ddl = f"CREATE TABLE `t` (`s` text, CONSTRAINT `ck` CHECK (`s` <> {value}))"
        assert set(_mysql_checks(ddl, backslashes=backslashes)) == {"ck"}


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
@pytest.mark.parametrize("changed", [False, True])
def test_fake_catalog_verification_has_no_commits_or_target_mutation(dialect, changed):
    from shape_databases import MySqlSink, PostgresSink
    from shape_databases._constraint_catalog import verify_existing_constraints
    from shape_databases.testing import FakeServer

    server = FakeServer(dialect)
    sink = (
        PostgresSink(connect=server.connect)
        if dialect == "postgres"
        else MySqlSink(connect=server.connect)
    )
    uri = (
        "postgresql://shape@localhost/db" if dialect == "postgres" else "mysql://shape@localhost/db"
    )
    schema = pa.schema([("id", pa.int64()), ("parent_id", pa.int64()), ("amount", pa.int64())])
    options = {
        "schema": schema,
        "primary_key": ["id"],
        "unique": [["parent_id", "amount"]],
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
        "defaults": {"amount": 4},
        "foreign_keys": [
            {
                "name": "fk_child_parent",
                "columns": ["parent_id"],
                "ref_table": "parent",
                "ref_columns": ["id"],
            }
        ],
    }
    sink.prepare_constraints(
        uri,
        {
            "parent": {"schema": pa.schema([("id", pa.int64())]), "primary_key": ["id"]},
            "child": options,
        },
    )
    key = server.key(None, "child")
    server.tables[key] = [(1, 1, 4)]
    before = copy.deepcopy(server.tables)
    conn = server.connect()
    count = len(server.events)
    requested = copy.deepcopy(options)
    if changed:
        requested["checks"][0]["expression"]["right"]["literal"] = 1

    def verify():
        verify_existing_constraints(
            conn,
            dialect=dialect,
            schema_name=None,
            table="child",
            schema=schema,
            columns={},
            primary_key=["id"],
            options=requested,
        )

    if changed:
        with pytest.raises(ShapeError, match="ck_amount"):
            verify()
    else:
        verify()
    assert server.tables == before and not server.temporary_tables
    assert not any(event[0] in {"commit", "rollback"} for event in server.events[count:])
