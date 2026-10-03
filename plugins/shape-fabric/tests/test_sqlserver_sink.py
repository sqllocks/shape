"""The ``sqlserver`` sink (``mssql://`` and ``sqlserver://``): URIs, options, write modes,
incremental commits, secrets, and the plugin conformance kit, against the in-repo fake server."""

import logging
import tomllib
import traceback
from pathlib import Path

import pyarrow as pa
import pytest
from shape_fabric import SqlServerSink, WarehouseSink, WriteError, sinks
from shape_fabric.testing import FakeSqlServer, sample_batches

from shape.errors import ShapeError
from shape.plugins import kit

pytestmark = pytest.mark.contract

URI = "mssql://db.example.test/appdb"
PASSWORD = "Tr0ub4dor&3-never-shown"


class Spy:
    """``connect`` of the fake server that remembers what it was called with."""

    def __init__(self, server=None):
        self.server = server or FakeSqlServer()
        self.calls = []

    def __call__(self, connection_string, credential=None, **kw):
        self.calls.append((connection_string, credential))
        return self.server.connect(connection_string, credential, **kw)


def sink(spy):
    return SqlServerSink(connect=spy)


def test_registered_under_the_sinks_group_with_both_schemes():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    eps = tomllib.loads(pyproject.read_text())["project"]["entry-points"]["shape.sinks"]
    assert eps == {
        "sqlserver": "shape_fabric:SqlServerSink",
        "warehouse": "shape_fabric:WarehouseSink",
    }
    assert SqlServerSink.name == "sqlserver" and SqlServerSink.schemes == ("mssql", "sqlserver")
    assert WarehouseSink.name == "warehouse"


def test_writes_a_table_and_reports_the_row_count(batches):
    spy = Spy()
    assert sink(spy).write(URI + "?user=sa", "customer", iter(batches), password="x1") == 7
    assert len(spy.server.rows("dbo", "customer")) == 7
    ((cs, credential),) = spy.calls
    assert credential is None
    assert "Server=db.example.test" in cs and "Database=appdb" in cs and "UID=sa" in cs
    assert spy.server.connections == 1


def test_the_sqlserver_scheme_port_userinfo_and_driver_options():
    spy = Spy()
    uri = "sqlserver://app%40corp:p%40ss@db.example.test:1444/appdb?trust_server_certificate=true"
    sink(spy).write(uri, "t", iter(sample_batches()), encrypt="false", timeout="5")
    cs = spy.calls[0][0]
    assert "Server=db.example.test,1444" in cs and "UID=app@corp" in cs and "PWD=p@ss" in cs
    assert "TrustServerCertificate=yes" in cs and "Encrypt=no" in cs
    assert "Connection Timeout=5" in cs


def test_schema_and_write_mode_come_from_the_uri_and_options_win(batches):
    spy = Spy()
    s = sink(spy)
    s.write(URI + "?schema=app&write_mode=create", "t", iter(batches))
    assert "app" in spy.server.schemas and len(spy.server.rows("app", "t")) == 7
    with pytest.raises(ShapeError, match="already exists"):
        s.write(URI + "?schema=app", "t", iter(batches))
    s.write(URI + "?schema=app&write_mode=append", "t", iter(batches))
    assert len(spy.server.rows("app", "t")) == 14
    s.write(
        URI + "?write_mode=append", "t", iter(batches), write_mode="truncate", schema_name="app"
    )
    assert len(spy.server.rows("app", "t")) == 7


def test_a_credential_signs_in_and_the_connection_string_has_no_login(batches):
    spy = Spy()
    cred = lambda scope: "token-for-" + scope  # noqa: E731
    sink(spy).write(URI, "t", iter(batches), credential=cred)
    cs, passed = spy.calls[0]
    assert passed is cred and "UID" not in cs and "PWD" not in cs
    with pytest.raises(ShapeError, match="either credential"):
        sink(spy).write(URI, "u", iter(batches), credential=cred, user="sa", password="x")


def test_an_open_connection_or_a_connection_string_replace_the_uri(batches):
    server = FakeSqlServer()
    conn = server.connect("")
    SqlServerSink().write("mssql://ignored", "t", iter(batches), connection=conn)
    assert len(server.rows("dbo", "t")) == 7 and not conn.closed  # never closes a borrowed one
    spy = Spy(server)
    sink(spy).write("whatever", "u", iter(batches), connection_string="Server=h;Database=d;UID=a")
    assert "Server=h;Database=d;UID=a" in spy.calls[0][0] and "db.example" not in spy.calls[0][0]


def test_bad_uris_and_options_are_refused_without_echoing_values(batches):
    s = sink(Spy())
    for uri in ("mssql://db.example.test", "mssql:///appdb", "postgres://h/d", "x"):
        with pytest.raises(ShapeError, match="URI of the form"):
            s.write(uri, "t", iter(batches))
    with pytest.raises(ShapeError, match="unknown query parameter"):
        s.write(URI + "?bogus=1", "t", iter(batches))
    with pytest.raises(ShapeError) as err:
        s.write(URI + "?password=" + PASSWORD, "t", iter(batches))
    assert "never accepted in the query" in str(err.value) and PASSWORD not in str(err.value)
    with pytest.raises(ShapeError, match="unknown sqlserver sink options"):
        s.write(URI, "t", iter(batches), nope=1)
    with pytest.raises(ShapeError, match="commit_rows must be a positive integer"):
        s.write(URI, "t", iter(batches), commit_rows=0)
    with pytest.raises(ShapeError, match="batch_size must be a positive integer"):
        s.write(URI + "?batch_size=abc", "t", iter(batches))
    with pytest.raises(ShapeError, match="Microsoft Entra"):
        s.write(URI + "?auth=cli", "t", iter(batches))
    with pytest.raises(ShapeError, match="true or false"):
        s.write(URI + "?encrypt=maybe", "t", iter(batches))


# --- streaming ---------------------------------------------------------------------------


def numbered(start, n):
    return pa.record_batch([pa.array(range(start, start + n), pa.int64())], names=["id"])


def test_commit_rows_makes_rows_visible_while_the_iterable_is_still_being_consumed():
    spy = Spy()
    seen = []

    def stream():
        for i in range(4):
            if i:  # what a second connection would read right now: the committed snapshot
                seen.append(len(spy.server._committed[0][("dbo", "ev")].rows))
            yield numbered(i * 5, 5)

    n = sink(spy).write(URI + "?user=a", "ev", stream(), password="p", commit_rows=5, batch_size=5)
    assert n == 20 and seen == [5, 10, 15]
    assert len(spy.server.rows("dbo", "ev")) == 20


def test_without_commit_rows_nothing_is_visible_until_the_end():
    spy = Spy()
    seen = []

    def stream():
        for i in range(3):
            if i:
                committed = spy.server._committed[0].get(("dbo", "ev"))
                seen.append(len(committed.rows) if committed else None)
            yield numbered(i * 5, 5)

    sink(spy).write(URI, "ev", stream(), user="a", password="p")
    assert seen == [0, 0]  # the table exists (DDL commits) but holds no committed rows


def test_a_failure_keeps_the_committed_chunks_and_drops_nothing_with_commit_rows():
    spy = Spy()

    def stream():
        yield numbered(0, 5)
        yield numbered(5, 5)
        raise RuntimeError("source went away")

    with pytest.raises(WriteError, match="source went away"):
        sink(spy).write(URI, "ev", stream(), user="a", password="p", commit_rows=5, batch_size=5)
    assert len(spy.server.rows("dbo", "ev")) == 10  # both chunks were committed, the table stays


def test_a_failure_without_commit_rows_rolls_everything_back_and_drops_the_new_table():
    spy = Spy()

    def stream():
        yield numbered(0, 5)
        raise RuntimeError("source went away")

    with pytest.raises(WriteError):
        sink(spy).write(URI, "ev", stream(), user="a", password="p")
    assert ("dbo", "ev") not in spy.server.tables


def test_micro_batches_append_to_one_table():
    spy = Spy()
    s = sink(spy)
    for i in range(3):
        s.write(
            URI + "?write_mode=append", "ev", iter([numbered(i * 4, 4)]), user="a", password="p"
        )
    assert [r[0] for r in spy.server.rows("dbo", "ev")] == list(range(12))


# --- secrets -----------------------------------------------------------------------------


def leaking_connect(secret):
    def connect(connection_string, credential=None, **_kw):
        raise RuntimeError(f"Login failed; the driver echoed {secret} and {connection_string}")

    return connect


def rendered(exc):
    return "\n".join(
        [str(exc), repr(exc), *traceback.format_exception(exc)]
        + [str(e) for e in (exc.__cause__, exc.__context__) if e is not None]
    )


@pytest.mark.parametrize(
    "uri, options",
    [
        (URI, {"user": "sa", "password": PASSWORD}),
        (f"mssql://sa:{PASSWORD.replace('&', '%26')}@db.example.test/appdb", {}),
        ("mssql://ignored/x", {"connection_string": f"Server=h;Database=d;UID=a;PWD={PASSWORD}"}),
    ],
)
def test_a_secret_never_appears_in_an_exception_or_a_log_record(uri, options, batches, caplog):
    caplog.set_level(logging.DEBUG)
    s = SqlServerSink(connect=leaking_connect(PASSWORD))
    with pytest.raises(Exception) as err:  # noqa: PT011
        s.write(uri, "t", iter(batches), **options)
    assert PASSWORD not in rendered(err.value)
    assert err.value.__cause__ is None
    assert PASSWORD not in caplog.text


def test_a_secret_is_masked_in_a_write_failure_and_in_the_logs(batches, caplog):
    caplog.set_level(logging.DEBUG)
    server = FakeSqlServer()

    def fail(sql, params):
        if sql.startswith("CREATE TABLE"):
            raise RuntimeError(f"denied for {PASSWORD}")

    server.fail = fail
    with pytest.raises(WriteError) as err:
        SqlServerSink(connect=server.connect).write(
            URI, "t", iter(batches), user="sa", password=PASSWORD
        )
    assert PASSWORD not in rendered(err.value) and "***" in str(err.value)
    # a good write logs its destination, redacted, and never the secret
    SqlServerSink(connect=FakeSqlServer().connect).write(
        URI, "t", iter(batches), user="sa", password=PASSWORD
    )
    assert "db.example.test" in caplog.text and PASSWORD not in caplog.text


def test_a_secret_in_an_unknown_option_value_is_not_echoed(batches):
    with pytest.raises(ShapeError) as err:
        sink(Spy()).write(URI, "t", iter(batches), user="a", password="p", token=PASSWORD)
    assert PASSWORD not in rendered(err.value)


# --- conformance -------------------------------------------------------------------------


def test_conforms_to_the_sink_protocol_with_the_kit():
    spy = Spy()
    s = sink(spy)
    kit.check_sink(s, URI + "?user=sa", sample_batches(), table="kit_a")

    def read_back():
        table = spy.server.tables[("dbo", "kit_b")]
        rows = spy.server.rows("dbo", "kit_b")
        names = [c[0] for c in table.columns]
        return pa.table({n: [r[i] for r in rows] for i, n in enumerate(names)})

    kit.check_sink(
        s, URI + "?write_mode=append&user=sa", sample_batches(), table="kit_b", read_back=read_back
    )


def test_the_module_exports_what_the_entry_points_name():
    assert sinks.SqlServerSink is SqlServerSink and sinks.WarehouseSink is WarehouseSink
