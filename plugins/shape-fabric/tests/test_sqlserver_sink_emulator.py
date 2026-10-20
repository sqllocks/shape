"""The ``sqlserver`` sink against a real SQL Server (the nightly ``sqlserver-e2e`` job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql
    SHAPE_TEST_MSSQL='Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=...;\
Encrypt=yes;TrustServerCertificate=yes' \
pytest -m emulator plugins/shape-fabric/tests/test_sqlserver_sink_emulator.py

The sink is reached the way users reach it: through the plugin host by URI scheme, with a
``mssql://`` URI (the login in the options, never on a command line). Each test works in its own
schema and drops it afterwards. Nothing is skipped when the server is missing: the nightly job
must fail if it cannot reach it. The second test reads from another connection *while the sink is
still consuming its batches*, which is what ``commit_rows`` promises.
"""

import os
import re
import uuid

import pyarrow as pa
import pytest
from shape_fabric import SqlServerSink, WriteError, _tsql
from shape_fabric.testing import sample_batches

from shape.plugins.host import PluginHost

pytestmark = pytest.mark.emulator

CS = os.environ.get(
    "SHAPE_TEST_MSSQL",
    "Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=Shape_Emulator_1;"
    "Encrypt=yes;TrustServerCertificate=yes",
)


def _field(name):
    match = re.search(rf"(?i)(?:^|;)\s*{name}\s*=\s*([^;]*)", CS)
    assert match, f"SHAPE_TEST_MSSQL has no {name}"
    return match.group(1).strip()


HOST = _field("Server")  # host,port
LOGIN = {"user": _field("UID"), "password": _field("PWD")}
URI = "mssql://" + HOST.replace(",", ":") + "/?trust_server_certificate=true"


@pytest.fixture
def schema():
    name = f"shape_s_{uuid.uuid4().hex[:10]}"
    yield name
    conn = _tsql.connect(CS)
    cursor = conn.cursor()
    # foreign keys first: a table another table references cannot be dropped before it
    for table, constraint in cursor.execute(
        "SELECT OBJECT_NAME(parent_object_id), name FROM sys.foreign_keys "
        "WHERE SCHEMA_NAME(schema_id) = ?",
        name,
    ).fetchall():
        cursor.execute(
            f"ALTER TABLE {_tsql.qualified(name, table)} DROP CONSTRAINT {_tsql.ident(constraint)}"
        )
    for (table,) in cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", name
    ).fetchall():
        cursor.execute(f"DROP TABLE {_tsql.qualified(name, table)}")
    cursor.execute(
        "IF EXISTS (SELECT 1 FROM sys.schemas WHERE name = ?) "
        f"EXEC('DROP SCHEMA {_tsql.ident(name)}')",
        name,
    )
    conn.commit()
    conn.close()


def uri_for(schema_name, **query):
    # the database comes from the path; the emulator's default database is master
    extra = "".join(f"&{k}={v}" for k, v in query.items())
    return URI.replace("/?", "/master?") + f"&schema={schema_name}{extra}"


def count(schema_name, table):
    """Rows another connection sees (READ COMMITTED: committed rows only)."""
    conn = _tsql.connect(CS)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {_tsql.qualified(schema_name, table)}")  # nosec B608
        return cursor.fetchone()[0]
    finally:
        conn.close()


def test_bulk_insert_through_the_plugin_host_by_uri_scheme(schema):
    sink = PluginHost().get("shape.sinks", "sqlserver")
    assert isinstance(sink, SqlServerSink)
    assert {"mssql", "sqlserver"} <= set(sink.schemes)
    assert sink.write(uri_for(schema), "customer", iter(sample_batches()), **LOGIN) == 7
    assert count(schema, "customer") == 7
    # the safe default refuses an existing table; append adds to it
    with pytest.raises(WriteError, match="already exists"):
        sink.write(uri_for(schema), "customer", iter(sample_batches()), **LOGIN)
    sink.write(uri_for(schema, write_mode="append"), "customer", iter(sample_batches()), **LOGIN)
    assert count(schema, "customer") == 14


def test_a_failed_login_never_shows_the_password(schema):
    bad = {"user": LOGIN["user"], "password": "Wr0ng-Passw0rd-not-shown"}
    with pytest.raises(Exception) as err:  # noqa: PT011
        SqlServerSink().write(uri_for(schema), "t", iter(sample_batches()), timeout=5, **bad)
    assert bad["password"] not in str(err.value) and bad["password"] not in repr(err.value)


def test_commit_rows_makes_rows_readable_from_another_connection_mid_write(schema):
    seen = []

    def stream():
        for i in range(4):
            if i:
                seen.append(count(schema, "ev"))
            yield pa.record_batch(
                [pa.array(range(i * 100, i * 100 + 100), pa.int64())], names=["id"]
            )

    rows = SqlServerSink().write(
        uri_for(schema, commit_rows=100, batch_size=100), "ev", stream(), **LOGIN
    )
    assert rows == 400 and seen == [100, 200, 300] and count(schema, "ev") == 400


# --- W2-10: identity columns, constraint toggling, idempotent reruns ---------------------

ID_SCHEMA = pa.schema([("customer_id", pa.int64()), ("name", pa.string())])
ID_COLUMNS = {
    "customer_id": {"nullable": False, "identity": {"start": 1000, "step": 5}},
    "name": {"nullable": True, "max_length": 40},
}


def customers(first=0, n=10):
    ids = [1000 + 5 * i for i in range(first, first + n)]
    return pa.RecordBatch.from_pydict(
        {"customer_id": ids, "name": [f"c{i}" for i in ids]}, schema=ID_SCHEMA
    )


def query(sql, *params):
    conn = _tsql.connect(CS)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, *params)
        # pyodbc rows are not tuples, and a Row never equals the tuple the tests compare it with
        return [tuple(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def run_sql(sql):
    conn = _tsql.connect(CS)
    try:
        conn.cursor().execute(sql)
        conn.commit()
    finally:
        conn.close()


def test_identity_keep_inserts_the_generated_keys_with_identity_insert(schema):
    sink = SqlServerSink()
    rows = sink.write(
        uri_for(schema), "customer", iter([customers(0, 6), customers(6, 4)]),
        columns=ID_COLUMNS, primary_key=["customer_id"], **LOGIN,
    )  # fmt: skip
    assert rows == 10
    table = f"{_tsql.ident(schema)}.[customer]"
    assert (
        query("SELECT COLUMNPROPERTY(OBJECT_ID(?), 'customer_id', 'IsIdentity')", table)[0][0] == 1
    )
    assert query("SELECT IDENT_SEED(?), IDENT_INCR(?)", table, table)[0] == (1000, 5)
    keys = [r[0] for r in query(f"SELECT customer_id FROM {table} ORDER BY customer_id")]
    assert keys == [1000 + 5 * i for i in range(10)]  # the generated keys, not 1000, 1005 by luck
    # an append of keys that exist is a key violation, so IDENTITY_INSERT really was on
    with pytest.raises(WriteError, match=r"(?i)duplicate key|PRIMARY KEY"):
        sink.write(
            uri_for(schema, write_mode="append"), "customer", iter([customers(0, 2)]),
            columns=ID_COLUMNS, **LOGIN,
        )  # fmt: skip


def test_identity_server_lets_the_server_number_the_rows(schema):
    SqlServerSink().write(
        uri_for(schema, identity="server"), "customer", iter([customers(3, 5)]),
        columns=ID_COLUMNS, primary_key=["customer_id"], **LOGIN,
    )  # fmt: skip
    table = f"{_tsql.ident(schema)}.[customer]"
    keys = [r[0] for r in query(f"SELECT customer_id FROM {table} ORDER BY customer_id")]
    assert keys == [1000 + 5 * i for i in range(5)]  # numbered from the seed, not the batch's keys


def _family(schema):
    if not query("SELECT 1 FROM sys.schemas WHERE name = ?", schema):
        run_sql(f"CREATE SCHEMA {_tsql.ident(schema)}")
    run_sql(
        f"CREATE TABLE {_tsql.ident(schema)}.[parent] ([id] BIGINT NOT NULL PRIMARY KEY, "
        "[name] NVARCHAR(40) NULL)"
    )
    run_sql(
        f"CREATE TABLE {_tsql.ident(schema)}.[child] ([child_id] BIGINT NOT NULL PRIMARY KEY, "
        f"[parent_id] BIGINT NOT NULL, CONSTRAINT [FK_child_parent] FOREIGN KEY ([parent_id]) "
        f"REFERENCES {_tsql.ident(schema)}.[parent]([id]))"
    )
    SqlServerSink().write(
        uri_for(schema, write_mode="append"), "parent",
        iter([pa.RecordBatch.from_pydict({"id": [1, 2, 3], "name": ["a", "b", "c"]})]), **LOGIN,
    )  # fmt: skip


def children(ids, parents):
    return pa.RecordBatch.from_pydict({"child_id": ids, "parent_id": parents})


def test_constraints_disable_loads_a_violating_row_then_exits_1_naming_the_constraint(schema):
    from shape_fabric.errors import ConstraintError

    _family(schema)
    with pytest.raises(WriteError, match="FOREIGN KEY"):  # keep: the server refuses the row
        SqlServerSink().write(
            uri_for(schema, write_mode="append"),
            "child",
            iter([children([1, 2], [1, 99])]),
            **LOGIN,
        )
    assert count(schema, "child") == 0
    with pytest.raises(ConstraintError) as err:
        SqlServerSink().write(
            uri_for(schema, write_mode="append", constraints="disable"), "child",
            iter([children([1, 2], [1, 99])]), **LOGIN,
        )  # fmt: skip
    assert err.value.exit_code == 1
    assert "FK_child_parent" in str(err.value) and "left disabled" in str(err.value)
    assert count(schema, "child") == 2  # the rows are there
    disabled = query(
        "SELECT is_disabled FROM sys.foreign_keys WHERE name = 'FK_child_parent' "
        "AND parent_object_id = OBJECT_ID(?)",
        f"{_tsql.ident(schema)}.[child]",
    )
    assert disabled == [(1,)]


def test_constraints_disable_with_good_data_leaves_the_constraints_enabled_and_trusted(schema):
    _family(schema)
    SqlServerSink().write(
        uri_for(schema, write_mode="append", constraints="disable"), "child",
        iter([children([1, 2], [1, 3])]), **LOGIN,
    )  # fmt: skip
    state = query(
        "SELECT is_disabled, is_not_trusted FROM sys.foreign_keys WHERE name = 'FK_child_parent' "
        "AND parent_object_id = OBJECT_ID(?)",
        f"{_tsql.ident(schema)}.[child]",
    )
    assert state == [(0, 0)]


def test_truncate_of_a_table_a_foreign_key_references_uses_delete(schema):
    _family(schema)
    SqlServerSink().write(
        uri_for(schema, write_mode="truncate"), "parent",
        iter([pa.RecordBatch.from_pydict({"id": [7, 8], "name": ["x", "y"]})]), **LOGIN,
    )  # fmt: skip
    assert count(schema, "parent") == 2


def test_upsert_rerun_after_a_run_killed_mid_table_completes_it_without_duplicates(schema):
    columns = {"customer_id": {"nullable": False}, "name": {"nullable": True, "max_length": 40}}
    options = {"columns": columns, "primary_key": ["customer_id"], **LOGIN}

    def stream(die_after=None):
        for i in range(8):
            if die_after is not None and i == die_after:
                raise RuntimeError("killed")  # the process dies between two round trips
            yield pa.RecordBatch.from_pydict(
                {"customer_id": list(range(i * 50, i * 50 + 50)), "name": [f"n{i}"] * 50},
                schema=ID_SCHEMA,
            )

    uri = uri_for(schema, write_mode="upsert", commit_rows=50, batch_size=50)
    with pytest.raises(WriteError):
        SqlServerSink().write(uri, "customer", stream(die_after=5), **options)
    partial = count(schema, "customer")
    assert 0 < partial < 400  # the committed chunks stay
    assert SqlServerSink().write(uri, "customer", stream(), **options) == 400
    table = f"{_tsql.ident(schema)}.[customer]"
    assert query(f"SELECT COUNT(*), COUNT(DISTINCT customer_id) FROM {table}") == [(400, 400)]
    assert SqlServerSink().write(uri, "customer", stream(), **options) == 400  # a plain rerun
    assert query(f"SELECT COUNT(*), COUNT(DISTINCT customer_id) FROM {table}") == [(400, 400)]


def test_upsert_with_identity_keep_keeps_the_generated_identity_values(schema):
    options = {"columns": ID_COLUMNS, "primary_key": ["customer_id"], **LOGIN}
    uri = uri_for(schema, write_mode="upsert")
    for _ in range(2):
        SqlServerSink().write(uri, "customer", iter([customers(0, 10)]), **options)
    table = f"{_tsql.ident(schema)}.[customer]"
    keys = [r[0] for r in query(f"SELECT customer_id FROM {table} ORDER BY customer_id")]
    assert keys == [1000 + 5 * i for i in range(10)]


def test_w9_03_catalog_widths_decimal_and_datetimeoffset(schema):
    import datetime as dt
    from decimal import Decimal

    from shape.repro import dataset_id

    value = dt.datetime(2024, 1, 2, 3, 4, 5, 999999, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    arrow = pa.schema(
        [("small", pa.int16()), ("amount", pa.decimal128(12, 4)), ("at", pa.timestamp("us", "UTC"))]
    )
    batch = pa.RecordBatch.from_pydict(
        {
            "small": [-32768, 32767],
            "amount": [Decimal("-99999999.9999"), Decimal("0.0001")],
            "at": [value, None],
        },
        schema=arrow,
    )
    SqlServerSink().write(uri_for(schema), "types", [batch], **LOGIN)
    conn = _tsql.connect(CS)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
            "ORDER BY ORDINAL_POSITION",
            schema,
            "types",
        )
        types = cursor.fetchall()
        assert types[0][0] == "smallint"
        assert tuple(types[1]) == ("decimal", 12, 4)
        assert types[2][0] == "datetimeoffset"
        from shape_sqlserver.source import SqlServerSource

        read = pa.Table.from_batches(
            SqlServerSource().read(uri_for(schema, table="types"), connection=conn)
        )
        assert read["at"][0].as_py() == value.astimezone(dt.UTC)
        assert dataset_id({"items": read}) == dataset_id({"items": pa.Table.from_batches([batch])})
    finally:
        conn.close()
