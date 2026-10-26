"""Existing declarations are compared before a PostgreSQL/MySQL rerun mutates rows."""

import copy
import os
import uuid

import pyarrow as pa
import pytest

from shape.errors import ShapeError

pytestmark = pytest.mark.emulator


@pytest.fixture(params=["postgres", "mysql"])
def catalog_table(request):
    dialect = request.param
    if dialect == "postgres":
        import psycopg

        conn = psycopg.connect(
            host="localhost",
            user="shape",
            dbname="shape",
            password=os.environ["SHAPE_POSTGRES_PASSWORD"],
            autocommit=True,
        )
    else:
        import pymysql

        conn = pymysql.connect(
            host="127.0.0.1",
            user="shape",
            database="shape",
            password=os.environ["SHAPE_MYSQL_PASSWORD"],
            autocommit=True,
        )
    suffix = uuid.uuid4().hex[:10]
    parent, child, check, foreign = (prefix + suffix for prefix in ("vp_", "vc_", "ck_", "fk_"))
    char = '"' if dialect == "postgres" else "`"

    def q(name):
        return char + name.replace(char, char + char) + char

    def execute(sql):
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            return cursor.fetchall() if cursor.description else []
        finally:
            cursor.close()

    try:
        execute(f"CREATE TABLE {q(parent)} (id BIGINT PRIMARY KEY)")
        execute(f"INSERT INTO {q(parent)} VALUES (1)")
        execute(
            f"CREATE TABLE {q(child)} (id BIGINT PRIMARY KEY, parent_id BIGINT, "
            "amount BIGINT DEFAULT 4, status VARCHAR(80) DEFAULT 'ready', "
            f"UNIQUE (parent_id,amount), CONSTRAINT {q(check)} CHECK (amount >= 0), "
            f"CONSTRAINT {q(foreign)} FOREIGN KEY (parent_id) REFERENCES {q(parent)} (id))"
        )
        execute(f"INSERT INTO {q(child)} (id,parent_id) VALUES (1,1)")
        options = {
            "unique": [["parent_id", "amount"]],
            "checks": [
                {
                    "name": check,
                    "expression": {
                        "op": ">=",
                        "left": {"column": "amount"},
                        "right": {"literal": 0},
                    },
                }
            ],
            "defaults": {"amount": 4, "status": "ready"},
            "foreign_keys": [
                {
                    "name": foreign,
                    "columns": ["parent_id"],
                    "ref_table": parent,
                    "ref_columns": ["id"],
                }
            ],
        }
        yield conn, dialect, child, options, execute, q
    finally:
        execute(f"DROP TABLE IF EXISTS {q(child)}")
        execute(f"DROP TABLE IF EXISTS {q(parent)}")
        conn.close()


def verify(catalog_table, options=None, primary_key=None):
    from shape_databases._constraint_catalog import verify_existing_constraints

    conn, dialect, table, original, _, _ = catalog_table
    verify_existing_constraints(
        conn,
        dialect=dialect,
        schema_name=None,
        table=table,
        schema=pa.schema(
            [
                ("id", pa.int64()),
                ("parent_id", pa.int64()),
                ("amount", pa.int64()),
                ("status", pa.string()),
            ]
        ),
        columns={"status": {"max_length": 80}},
        primary_key=["id"] if primary_key is None else primary_key,
        options=original if options is None else options,
    )


def test_matching_catalog_accepts_all_requested_constraints_without_changing_rows(catalog_table):
    verify(catalog_table)
    _, _, table, _, execute, q = catalog_table
    assert list(execute(f"SELECT id,parent_id,amount,status FROM {q(table)}")) == [
        (1, 1, 4, "ready")
    ]


@pytest.mark.parametrize("change", ["check", "default", "foreign", "unique", "primary"])
def test_changed_declaration_refuses_before_any_row_mutation(catalog_table, change):
    _, _, table, original, execute, q = catalog_table
    options = copy.deepcopy(original)
    primary = ["id"]
    if change == "check":
        options["checks"][0]["expression"]["right"]["literal"] = 1
        name = options["checks"][0]["name"]
    elif change == "default":
        options["defaults"]["amount"] = 5
        name = "DEFAULT amount"
    elif change == "foreign":
        options["foreign_keys"][0]["ref_columns"] = ["other"]
        name = options["foreign_keys"][0]["name"]
    elif change == "unique":
        options["unique"] = [["amount"]]
        name = "UNIQUE"
    else:
        primary = ["amount"]
        name = "PRIMARY KEY"
    with pytest.raises(ShapeError, match=name):
        verify(catalog_table, options, primary)
    assert list(execute(f"SELECT id,parent_id,amount,status FROM {q(table)}")) == [
        (1, 1, 4, "ready")
    ]


def test_non_enforced_or_unvalidated_check_does_not_count_as_matching(catalog_table):
    _, dialect, table, options, execute, q = catalog_table
    name = options["checks"][0]["name"]
    if dialect == "postgres":
        execute(f"ALTER TABLE {q(table)} DROP CONSTRAINT {q(name)}")
        execute(f"ALTER TABLE {q(table)} ADD CONSTRAINT {q(name)} CHECK (amount >= 0) NOT VALID")
    else:
        execute(f"ALTER TABLE {q(table)} ALTER CHECK {q(name)} NOT ENFORCED")
    with pytest.raises(ShapeError, match=name):
        verify(catalog_table)
    assert list(execute(f"SELECT amount FROM {q(table)}")) == [(4,)]


def test_non_enforced_foreign_key_is_not_accepted_by_catalog_verification(catalog_table):
    _, dialect, table, options, execute, q = catalog_table
    foreign = options["foreign_keys"][0]
    if dialect == "postgres":
        execute(f"ALTER TABLE {q(table)} DROP CONSTRAINT {q(foreign['name'])}")
        execute(
            f"ALTER TABLE {q(table)} ADD CONSTRAINT {q(foreign['name'])} "
            f"FOREIGN KEY (parent_id) REFERENCES {q(foreign['ref_table'])} (id) NOT VALID"
        )
    else:
        execute("SET SESSION foreign_key_checks=0")
    with pytest.raises(ShapeError, match=foreign["name"]):
        verify(catalog_table)
    assert list(execute(f"SELECT parent_id FROM {q(table)}")) == [(1,)]


def test_verification_preserves_the_callers_pending_transaction(catalog_table):
    conn, dialect, table, _, execute, q = catalog_table
    if dialect == "postgres":
        conn.autocommit = False
    else:
        conn.autocommit(False)
    try:
        execute(f"INSERT INTO {q(table)} (id,parent_id,amount) VALUES (2,1,5)")
        verify(catalog_table)
        conn.rollback()
        assert list(execute(f"SELECT id FROM {q(table)} ORDER BY id")) == [(1,)]
    finally:
        conn.rollback()
        if dialect == "postgres":
            conn.autocommit = True
        else:
            conn.autocommit(True)


@pytest.mark.parametrize("catalog_table", ["postgres"], indirect=True)
def test_replication_session_cannot_silently_disable_foreign_key_enforcement(catalog_table):
    _, _, table, options, execute, q = catalog_table
    execute("SET SESSION session_replication_role='replica'")
    with pytest.raises(ShapeError, match=options["foreign_keys"][0]["name"]):
        verify(catalog_table)
    assert list(execute(f"SELECT parent_id FROM {q(table)}")) == [(1,)]
