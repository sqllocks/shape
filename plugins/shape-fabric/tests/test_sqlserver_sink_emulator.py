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
