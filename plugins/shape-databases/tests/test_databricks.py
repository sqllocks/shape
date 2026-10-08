"""The Databricks sink against the in-repo fake SQL warehouse (no workspace needed)."""

import datetime as dt
import io
import logging
import sys
import urllib.error
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases import CredentialError, DatabricksSink, WriteError
from shape_databases import _cloud as cloud
from shape_databases.databricks import binding, oauth_token
from shape_databases.testing import SAMPLE_SCHEMA, FakeDatabricks, sample_batch

from shape.errors import ShapeError

HOST = "adb-123.4.azuredatabricks.net"
URI = f"databricks://{HOST}/sql/1.0/warehouses/abc123?catalog=main&schema=demo"
TOKEN = "dapi-s3cret-token-0001"
MARKS = "(?, ?, ?, ?, ?, CAST(? AS DATE), CAST(? AS TIMESTAMP), UNHEX(?))"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)


@pytest.fixture
def server():
    return FakeDatabricks()


def write(server, batches=None, uri=URI, table="customer", **options):
    batches = [sample_batch()] if batches is None else batches
    options.setdefault("token", TOKEN)
    return DatabricksSink(connect=server.connect).write(uri, table, iter(batches), **options)


# -- statements ---------------------------------------------------------------------------
def test_create_writes_a_delta_table_with_one_bound_insert(server):
    assert write(server, [sample_batch(0, 3), sample_batch(3, 2)]) == 5
    statements = server.statements()
    assert statements[0].startswith("SELECT 1 FROM information_schema.tables")
    create = statements[1]
    assert create.startswith("CREATE TABLE `customer` (") and create.endswith(") USING DELTA")
    (insert, params) = server.inserts()[0]
    cols = "`id`, `name`, `score`, `price`, `active`, `born`, `seen`, `blob`"
    assert insert == f"INSERT INTO `customer` ({cols}) VALUES " + ", ".join([MARKS] * 5)
    assert len(params) == 5 * 8
    assert server.events[-1] == ("close",)


def test_every_value_is_a_bound_parameter(server):
    write(server)
    text = "\n".join(server.statements())
    for value in ("n0", "n1", "n2", "2000-01-0", "0.25"):
        assert value not in text
    (_, params) = server.inserts()[0]
    first = params[:8]
    assert first == [
        0,
        "n0",
        0.0,
        Decimal("0"),
        True,
        "2000-01-01",
        "2024-01-01T12:00:00+00:00",
        "00",
    ]


def test_the_connection_parameters(server):
    write(server)
    assert server.events[0][1] == {
        "server_hostname": HOST,
        "http_path": "/sql/1.0/warehouses/abc123",
        "catalog": "main",
        "schema": "demo",
        "access_token": TOKEN,
    }


def test_exists_query_is_parameterised(server):
    write(server, schema_name="app")
    assert server.events[1][2] == ["app", "customer"]


def test_the_default_mode_never_touches_an_existing_table(server):
    write(server)
    before = len(server.statements())
    with pytest.raises(ShapeError, match="already exists"):
        write(server, [sample_batch(10, 2)])
    assert len(server.rows("customer")) == 3
    assert not any(
        s.startswith(("DROP", "TRUNCATE", "INSERT")) for s in server.statements()[before:]
    )


def test_append_truncate_and_replace(server):
    write(server, write_mode="append")
    write(server, [sample_batch(10, 2)], write_mode="append")
    assert len(server.rows("customer")) == 5
    write(server, [sample_batch(20, 1)], write_mode="truncate")
    assert [r[0] for r in server.rows("customer")] == [20]
    assert "TRUNCATE TABLE `customer`" in server.statements()
    write(server, [sample_batch(30, 2)], write_mode="replace")
    assert [r[0] for r in server.rows("customer")] == [30, 31]
    assert "DROP TABLE IF EXISTS `customer`" in server.statements()
    assert sum(s.startswith("CREATE TABLE") for s in server.statements()) == 2


def test_bad_options_are_refused_before_connecting(server):
    for bad in (
        {"write_mode": "upsert"},
        {"batch_size": 0},
        {"batch_size": True},
        {"commit_rows": 0},
        {"commit_rows": "5"},
        {"primary_key": ["nope"]},
        {"table_prefix": 1},
    ):
        with pytest.raises(ShapeError):
            write(server, **bad)
    assert server.events == []


def test_schema_name_table_prefix_primary_key_and_columns(server):
    write(
        server,
        schema_name="app",
        table_prefix="gen_",
        primary_key=["id"],
        columns={"name": {"nullable": False}},
    )
    create = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert create.startswith("CREATE TABLE `app`.`gen_customer` (")
    assert "`id` BIGINT NOT NULL" in create and "`name` STRING NOT NULL" in create
    assert "PRIMARY KEY (`id`)" in create
    assert server.rows("gen_customer", "app")


# -- batch boundaries ---------------------------------------------------------------------
def test_the_default_batch_is_a_thousand_rows(server):
    write(server, [sample_batch(0, 1001)])
    sizes = [len(p) // 8 for _, p in server.inserts()]
    assert sizes == [1000, 1]


@pytest.mark.parametrize(
    ("rows", "sizes"),
    [(1, [1]), (3, [3]), (4, [3, 1]), (6, [3, 3]), (7, [3, 3, 1])],
)
def test_batch_size_boundaries(server, rows, sizes):
    write(server, [sample_batch(0, rows)], batch_size=3)
    assert [len(p) // 8 for _, p in server.inserts()] == sizes
    assert len(server.rows("customer")) == rows


def test_no_rows_creates_the_table_and_inserts_nothing(server):
    with pytest.raises(ShapeError, match="no batches and no schema"):
        write(server, [])
    assert server.events == []
    assert write(server, [], schema=SAMPLE_SCHEMA) == 0
    assert server.inserts() == [] and server.rows("customer") == []
    assert ("default", "customer") in server.tables


def test_batches_are_regrouped_across_batch_boundaries(server):
    write(server, [sample_batch(0, 2), sample_batch(2, 2), sample_batch(4, 2)], batch_size=4)
    assert [len(p) // 8 for _, p in server.inserts()] == [4, 2]


@pytest.mark.parametrize(
    ("input_sizes", "statements"),
    [
        ([1], [1]),
        ([4], [4]),
        ([5], [4, 1]),
        ([2, 2], [2, 2]),
        ([4, 4, 1], [4, 4, 1]),
        ([9], [4, 4, 1]),
    ],
)
def test_commit_rows_is_the_batch_boundary(server, input_sizes, statements):
    """With commit_rows a statement never carries rows of the next input batch."""
    batches = [sample_batch(sum(input_sizes[:i]), n) for i, n in enumerate(input_sizes)]
    write(server, batches, commit_rows=100, batch_size=4)
    assert [len(p) // 8 for _, p in server.inserts()] == statements
    assert len(server.rows("customer")) == sum(input_sizes)


def test_commit_rows_does_not_shrink_statements(server):
    write(server, [sample_batch(0, 5)], batch_size=3, commit_rows=1)
    assert [len(p) // 8 for _, p in server.inserts()] == [3, 2]


# -- failures ------------------------------------------------------------------------------
def test_a_failure_drops_a_table_this_call_created_and_never_rolls_back():
    server = FakeDatabricks(fail_after_rows=4)
    with pytest.raises(WriteError, match="simulated driver failure") as info:
        write(server, [sample_batch(0, 9)], batch_size=3)
    assert ("default", "customer") not in server.tables
    assert ("rollback",) not in server.events  # the connector refuses a rollback
    assert info.value.rows_committed == 0 and "committed" not in str(info.value)


def test_a_failed_append_says_how_many_rows_stayed():
    server = FakeDatabricks()
    write(server)
    server.fail_after_rows = 3 + 4
    with pytest.raises(WriteError, match="4 rows were committed") as info:
        write(server, [sample_batch(10, 9)], batch_size=2, write_mode="append")
    assert info.value.rows_committed == 4
    assert len(server.rows("customer")) == 7


def test_with_commit_rows_a_failure_keeps_the_table_and_the_rows():
    server = FakeDatabricks(fail_after_rows=4)
    with pytest.raises(WriteError, match="4 rows were committed") as info:
        write(server, [sample_batch(0, 2), sample_batch(2, 2), sample_batch(4, 5)], commit_rows=2)
    assert info.value.rows_committed == 4
    assert len(server.rows("customer")) == 4


def test_a_source_that_dies_midway_fails_cleanly():
    server = FakeDatabricks()

    def gen():
        yield sample_batch(0, 5)
        raise RuntimeError("source died")

    with pytest.raises(WriteError, match="source died"):
        write(server, gen(), batch_size=2)
    assert ("default", "customer") not in server.tables


def test_batches_with_different_columns_fail(server):
    with pytest.raises(ShapeError, match="has columns"):
        write(server, [sample_batch(), pa.RecordBatch.from_pydict({"x": [1]})])
    assert ("default", "customer") not in server.tables


# -- names and types ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name", ["a\"b'c", "back`tick", "%s", "ünïcödé", "--c", 'x"DROP--', "q'--"]
)
def test_hostile_column_names_are_quoted(server, name):
    write(server, [pa.RecordBatch.from_pydict({name: [1, 2], "ok": ["a", "b"]})])
    quoted = "`" + name.replace("`", "``") + "`"
    create = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert f"  {quoted} BIGINT," in create
    (insert, params) = server.inserts()[0]
    assert insert.startswith(f"INSERT INTO `customer` ({quoted}, `ok`) VALUES ")
    assert params == [1, "a", 2, "b"]


@pytest.mark.parametrize(
    ("kind", "name", "text"),
    [
        ("table", "Customer", "stores table names in lower case"),
        ("table", "a b", "cannot contain a space"),
        ("table", "a.b", "cannot contain"),
        ("table", "a/b", "cannot contain"),
        ("table", "a`b", "cannot contain"),
        ("table", "x" * 256, "longer than the 255"),
        ("table", "a\x00b", "NUL"),
        ("table", "a\nb", "control character"),
        ("schema", "Demo", "stores schema names in lower case"),
        ("schema", "x" * 256, "longer than the 255"),
        ("column", "a b", "cannot hold"),
        ("column", "a,b", "cannot hold"),
        ("column", "a;b", "cannot hold"),
        ("column", "a{b", "cannot hold"),
        ("column", "a(b", "cannot hold"),
        ("column", "a=b", "cannot hold"),
        ("column", "a\tb", "control character"),
        ("column", "c" * 256, "longer than the 255"),
    ],
)
def test_names_the_database_would_change_or_refuse_are_named_and_refused(server, kind, name, text):
    batch = pa.RecordBatch.from_pydict({(name if kind == "column" else "c"): [1]})
    options = {"schema_name": name} if kind == "schema" else {}
    with pytest.raises(ShapeError, match=text) as info:
        write(server, [batch], table=name if kind == "table" else "t", **options)
    assert name[:30] in str(info.value) or repr(name) in str(info.value)
    assert server.events == []


def test_the_table_prefix_is_part_of_the_checked_name(server):
    with pytest.raises(ShapeError, match="Gen_customer"):
        write(server, table_prefix="Gen_")
    assert server.events == []


def test_columns_that_differ_only_in_case_are_refused(server):
    batch = pa.RecordBatch.from_arrays([pa.array([1]), pa.array([2])], names=["Id", "id"])
    with pytest.raises(ShapeError, match="appears twice"):
        write(server, [batch])
    assert server.events == []


def test_255_characters_is_accepted(server):
    name = "c" * 255
    write(server, [pa.RecordBatch.from_pydict({name: [1]})], table="t" * 255)
    assert ("default", "t" * 255) in server.tables


def test_type_map():
    f = pa.field
    cases = [
        (pa.int8(), "INT"),
        (pa.int16(), "INT"),
        (pa.int32(), "INT"),
        (pa.uint8(), "INT"),
        (pa.uint16(), "INT"),
        (pa.int64(), "BIGINT"),
        (pa.uint32(), "BIGINT"),
        (pa.uint64(), "DECIMAL(20,0)"),
        (pa.float32(), "DOUBLE"),
        (pa.float64(), "DOUBLE"),
        (pa.decimal128(10, 2), "DECIMAL(10,2)"),
        (pa.string(), "STRING"),
        (pa.large_string(), "STRING"),
        (pa.bool_(), "BOOLEAN"),
        (pa.date32(), "DATE"),
        (pa.timestamp("us"), "TIMESTAMP_NTZ"),
        (pa.timestamp("us", "UTC"), "TIMESTAMP"),
        (pa.timestamp("ns", "Asia/Tokyo"), "TIMESTAMP"),
        (pa.binary(), "BINARY"),
        (pa.list_(pa.int32()), "STRING"),
        (pa.struct([("a", pa.int32())]), "STRING"),
        (pa.map_(pa.string(), pa.int32()), "STRING"),
        (pa.dictionary(pa.int8(), pa.string()), "STRING"),
    ]
    for arrow, expected in cases:
        assert cloud.databricks_type(f("c", arrow)) == expected, arrow


@pytest.mark.parametrize(
    "arrow", [pa.duration("us"), pa.time64("us"), pa.null(), pa.decimal256(40, 2)]
)
def test_a_type_that_cannot_be_stored_is_refused_before_connecting(server, arrow):
    batch = pa.RecordBatch.from_arrays([pa.array([None], arrow)], names=["weird"])
    with pytest.raises(ShapeError, match="weird"):
        write(server, [batch])
    assert server.events == []


def test_values_are_converted_for_their_placeholders(server):
    batch = pa.RecordBatch.from_pydict(
        {
            "ntz": pa.array([dt.datetime(2024, 5, 6, 7, 8, 9, 123456)], pa.timestamp("ns")),
            "tz": pa.array(
                [dt.datetime(2024, 1, 1, 12, tzinfo=dt.timezone(dt.timedelta(hours=2)))],
                pa.timestamp("us", "Europe/Berlin"),
            ),
            "d": pa.array([dt.date(1999, 12, 31)]),
            "b": pa.array([b"\x00\xff"]),
            "big": pa.array([2**63 + 5], pa.uint64()),
            "nested": pa.array([{"a": [1, 2], "when": dt.date(2024, 1, 1)}]),
            "kind": pa.array(["x"]).dictionary_encode(),
            "nothing": pa.array([None], pa.string()),
        }
    )
    write(server, [batch])
    (insert, params) = server.inserts()[0]
    assert "CAST(? AS TIMESTAMP_NTZ), CAST(? AS TIMESTAMP), CAST(? AS DATE), UNHEX(?)" in insert
    assert "CAST(? AS DECIMAL(20,0)), ?, ?, ?)" in insert
    assert params == [
        "2024-05-06 07:08:09.123456",
        "2024-01-01T10:00:00+00:00",
        "1999-12-31",
        "00ff",
        str(2**63 + 5),
        '{"a": [1, 2], "when": "2024-01-01"}',
        "x",
        None,
    ]


def test_bindings():
    assert binding(pa.field("c", pa.int32())) == ("?", None)
    assert binding(pa.field("c", pa.string())) == ("?", None)
    assert binding(pa.field("c", pa.date32()))[0] == "CAST(? AS DATE)"
    assert binding(pa.field("c", pa.binary()))[1](None) is None


# -- the destination -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("uri", "text"),
    [
        (f"databricks://token:{TOKEN}@{HOST}/sql/1.0/warehouses/a", "password must not be part"),
        (f"databricks://{HOST}/sql/1.0/warehouses/a?token={TOKEN}", "token must not be part"),
        (f"databricks://{HOST}/sql/1.0/warehouses/a?access_token=x", "access_token must not be"),
        (f"databricks://{HOST}/sql/1.0/warehouses/a?client_secret=x", "client_secret must not be"),
        (f"databricks://{TOKEN}@{HOST}/sql/1.0/warehouses/a", "no user and no port"),
        (f"databricks://{HOST}:443/sql/1.0/warehouses/a", "no user and no port"),
        (f"databricks://{HOST}/", "HTTP path"),
        (f"databricks://{HOST}", "HTTP path"),
        ("databricks:///sql/1.0/warehouses/a", "workspace host"),
        (f"databricks://{HOST}/sql/1.0/warehouses/a?colour=red", "unknown URI parameter"),
        (f"databricks://{HOST}/sql/1.0/warehouses/a?catalog=Main", "lower case"),
        (f"databricks://{HOST}/sql/1.0/warehouses/a?schema=a.b", "cannot contain"),
        (f"postgresql://{HOST}/sql/1.0/warehouses/a", "scheme must be one of databricks"),
    ],
)
def test_bad_destinations_are_refused_before_connecting(server, uri, text):
    with pytest.raises(ShapeError, match=text) as info:
        write(server, uri=uri, token=None)
    assert TOKEN not in str(info.value)
    assert server.events == []


# -- credentials ---------------------------------------------------------------------------
def test_token_option_reference_and_environment(server, monkeypatch, tmp_path):
    assert server.events == []
    write(server)
    assert server.events[0][1]["access_token"] == TOKEN
    monkeypatch.setenv("DBX_TOKEN", "from-ref")
    ref = FakeDatabricks()
    write(ref, token="env://DBX_TOKEN")
    assert ref.events[0][1]["access_token"] == "from-ref"
    env = FakeDatabricks()
    monkeypatch.setenv("DATABRICKS_TOKEN", "from-env")
    write(env, token=None)
    assert env.events[0][1]["access_token"] == "from-env"
    path = tmp_path / "t"
    path.write_text("from-file\n", encoding="utf-8")
    path.chmod(0o600)
    file = FakeDatabricks()
    write(file, token=f"file://{path}")
    assert file.events[0][1]["access_token"] == "from-file"


def test_oauth_machine_to_machine_parameters(server, monkeypatch):
    monkeypatch.setenv("DBX_SECRET", "m2m-secret")
    write(server, token=None, client_id="sp-1", client_secret="env://DBX_SECRET")  # nosec
    params = server.events[0][1]
    assert params["client_id"] == "sp-1" and params["client_secret"] == "m2m-secret"
    assert "access_token" not in params
    other = FakeDatabricks()
    write(other, uri=URI + "&client_id=sp-2", token=None, client_secret="v")
    assert other.events[0][1]["client_id"] == "sp-2"


@pytest.mark.parametrize(
    ("options", "text"),
    [
        ({"client_secret": "s"}, "needs a client_id"),
        ({"client_id": "sp"}, "needs a client_secret"),
        ({"client_secret": "s", "client_id": "sp", "token": "t"}, "not both"),
        ({"client_secret": ""}, "non-empty"),
        ({"token": 5}, "password must be"),
        ({"token": "env://DBX_NOT_SET_ANYWHERE"}, "DBX_NOT_SET_ANYWHERE"),
    ],
)
def test_credential_mistakes_are_refused_before_connecting(server, options, text):
    options.setdefault("token", None)
    with pytest.raises(CredentialError, match=text):
        write(server, **options)
    assert server.events == []


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_oauth_token_exchange():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = request.data
        seen["timeout"] = timeout
        return _Response(b'{"access_token": "tok-123", "expires_in": 3600}')

    assert oauth_token(HOST, "sp", "sec", opener=opener) == "tok-123"
    assert seen["url"] == f"https://{HOST}/oidc/v1/token"
    assert seen["auth"] == "Basic c3A6c2Vj"
    assert seen["body"] == b"grant_type=client_credentials&scope=all-apis"
    assert seen["timeout"] == 30


def test_oauth_token_failures_never_echo_what_was_sent():
    def refused(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "bad sec", {}, io.BytesIO(b"sec"))

    with pytest.raises(CredentialError, match="HTTP 401") as info:
        oauth_token(HOST, "sp", "sec", opener=refused)
    assert "sec" not in str(info.value).replace("OAuth sign-in", "")

    def empty(request, timeout):
        return _Response(b"{}")

    with pytest.raises(CredentialError, match="no access token"):
        oauth_token(HOST, "sp", "sec", opener=empty)

    def down(request, timeout):
        raise OSError("host sec unreachable")

    with pytest.raises(CredentialError, match="OSError") as info2:
        oauth_token(HOST, "sp", "sec", opener=down)
    assert "unreachable" not in str(info2.value)
    with pytest.raises(ShapeError, match="not a usable Databricks host"):
        oauth_token("evil.example/../x", "sp", "sec", opener=down)


def test_a_secret_is_in_no_message_or_log(caplog):
    server = FakeDatabricks(fail_after_rows=0, fail_message=f"denied token={TOKEN} {TOKEN}")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(WriteError) as info:
            write(server)
    assert TOKEN not in str(info.value) + caplog.text + repr(info.value)
    down = FakeDatabricks(fail_connect=f"401 for {TOKEN}")
    with pytest.raises(WriteError, match="could not connect") as info2:
        write(down)
    assert TOKEN not in str(info2.value) and info2.value.__context__ is None


# -- the driver ----------------------------------------------------------------------------
def _fake_driver(monkeypatch, calls):
    monkeypatch.setitem(sys.modules, "databricks", type(sys)("databricks"))
    module = type(sys)("databricks.sql")

    def connect(**params):
        calls.append(params)
        return FakeDatabricks().connect()

    module.connect = connect
    monkeypatch.setitem(sys.modules, "databricks.sql", module)


def test_a_missing_driver_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "databricks", None)
    monkeypatch.setitem(sys.modules, "databricks.sql", None)
    with pytest.raises(ShapeError, match=r"pip install 'sqllocks-shape-databases\[databricks\]'"):
        DatabricksSink().write(URI, "t", iter([sample_batch()]), token="x")


def test_the_driver_connection_has_no_rollback_to_fail(monkeypatch):
    calls = []
    _fake_driver(monkeypatch, calls)
    session = DatabricksSink().default_connect(
        server_hostname=HOST, http_path="/p", access_token="t"
    )
    assert calls == [{"server_hostname": HOST, "http_path": "/p", "access_token": "t"}]
    session.commit()
    session.rollback()


def test_the_driver_gets_an_exchanged_token_for_m2m(monkeypatch):
    calls = []
    _fake_driver(monkeypatch, calls)
    from shape_databases import databricks

    monkeypatch.setattr(
        databricks, "oauth_token", lambda host, cid, secret: f"{host}|{cid}|{secret}"
    )
    DatabricksSink().default_connect(
        server_hostname=HOST, http_path="/p", client_id="sp", client_secret="sec"
    )
    assert calls[0]["access_token"] == f"{HOST}|sp|sec"
    assert "client_secret" not in calls[0] and "client_id" not in calls[0]


def test_no_credentials_fails_with_the_options_named(monkeypatch):
    _fake_driver(monkeypatch, [])
    with pytest.raises(CredentialError, match="DATABRICKS_TOKEN"):
        DatabricksSink().write(URI, "t", iter([sample_batch()]))
