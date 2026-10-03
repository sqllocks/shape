"""The Snowflake sink against the in-repo fake account (no cloud account needed)."""

import datetime as dt
import logging
import os
import sys
import warnings
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases import CredentialError, SnowflakeSink, WriteError
from shape_databases import _cloud as cloud
from shape_databases.snowflake import der_from_pem
from shape_databases.testing import SAMPLE_SCHEMA, FakeDriverError, FakeSnowflake, sample_batch

from shape.errors import ShapeError

URI = "snowflake://shape@xy12345.eu-west-1/SHAPE_DB/PUBLIC?warehouse=WH&role=LOADER"
COPY = (
    'COPY INTO "customer" FROM @%"customer" FILE_FORMAT = (TYPE = PARQUET) '
    "MATCH_BY_COLUMN_NAME = CASE_SENSITIVE PURGE = TRUE"
)
BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
END = "-----END " + "PRIVATE KEY-----"
SECRET = "Sn0w-s3cret/with:odd@chars"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)


@pytest.fixture
def server():
    return FakeSnowflake()


def make(server, **kw):
    return SnowflakeSink(connect=server.connect, **kw)


def write(server, batches=None, uri=URI, table="customer", **options):
    batches = [sample_batch()] if batches is None else batches
    return make(server).write(uri, table, iter(batches), **options)


def rows_of(*batches):
    return [tuple(r.values()) for b in batches for r in b.to_pylist()]


# -- the statements -----------------------------------------------------------------------
def test_create_stages_parquet_copies_once_and_removes_the_stage(server):
    assert write(server, [sample_batch(0, 3), sample_batch(3, 2)], password="pw") == 5
    statements = server.statements()
    assert [s.split(" ")[0] for s in statements] == [
        "SELECT",
        "CREATE",
        "PUT",
        "COPY",
        "REMOVE",
    ]
    assert statements[3] == COPY
    assert statements[2].endswith(' @%"customer" AUTO_COMPRESS = FALSE OVERWRITE = TRUE')
    assert statements[4].startswith('REMOVE @%"customer" PATTERN = ')
    assert server.rows("customer") == rows_of(sample_batch(0, 3), sample_batch(3, 2))
    assert server.events[-1] == ("close",)


def test_connection_parameters_come_from_the_uri(server):
    write(server, password="pw")
    (_, params) = server.events[0]
    assert params == {
        "account": "xy12345.eu-west-1",
        "user": "shape",
        "database": "SHAPE_DB",
        "schema": "PUBLIC",
        "warehouse": "WH",
        "role": "LOADER",
        "autocommit": False,
        "password": "pw",
    }


def test_a_uri_without_schema_or_options_is_enough(server):
    write(server, uri="snowflake://u@acct/DB", password="pw")
    params = server.events[0][1]
    assert params["database"] == "DB" and "schema" not in params and "warehouse" not in params


def test_the_default_mode_never_touches_an_existing_table(server):
    write(server)
    before = len(server.events)
    with pytest.raises(ShapeError, match="already exists"):
        write(server, [sample_batch(10, 2)])
    assert len(server.rows("customer")) == 3
    assert not any(
        s.startswith(("DROP", "TRUNCATE", "PUT", "COPY")) for s in server.statements()[before:]
    )


def test_append_creates_a_missing_table_and_adds_rows(server):
    write(server, write_mode="append")
    write(server, [sample_batch(10, 2)], write_mode="append")
    assert len(server.rows("customer")) == 5
    assert sum(s.startswith("CREATE TABLE") for s in server.statements()) == 1


def test_truncate_empties_then_writes(server):
    write(server)
    write(server, [sample_batch(10, 2)], write_mode="truncate")
    assert [r[0] for r in server.rows("customer")] == [10, 11]
    assert any(s == 'TRUNCATE TABLE "customer"' for s in server.statements())


def test_replace_drops_and_recreates(server):
    write(server)
    write(server, [sample_batch(10, 1)], write_mode="replace")
    assert [r[0] for r in server.rows("customer")] == [10]
    assert 'DROP TABLE IF EXISTS "customer"' in server.statements()


def test_bad_options_are_refused_before_connecting(server):
    for bad in (
        {"write_mode": "upsert"},
        {"commit_rows": 0},
        {"commit_rows": "5"},
        {"chunk_rows": 0},
        {"chunk_rows": True},
        {"table_prefix": 3},
        {"primary_key": ["a" * 256]},
    ):
        with pytest.raises(ShapeError):
            write(server, **bad)
    assert server.events == []


def test_schema_name_table_prefix_primary_key_and_columns(server):
    write(
        server,
        schema_name="App",
        table_prefix="gen_",
        primary_key=["id"],
        columns={"name": {"max_length": 40, "nullable": False}},
    )
    create = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert create.startswith('CREATE TABLE "App"."gen_customer" (')
    assert '"id" NUMBER(38,0) NOT NULL' in create
    assert '"name" VARCHAR(40) NOT NULL' in create
    assert 'PRIMARY KEY ("id")' in create
    assert 'COPY INTO "App"."gen_customer" FROM @"App".%"gen_customer" ' in "\n".join(
        server.statements()
    )
    assert server.rows("gen_customer", "App")


def test_a_primary_key_that_is_not_a_column_is_refused(server):
    with pytest.raises(ShapeError, match="primary key column 'nope'"):
        write(server, primary_key=["nope"])
    assert server.events == []


def test_an_empty_table_needs_a_schema_and_is_created_without_staging(server):
    with pytest.raises(ShapeError, match="no batches and no schema"):
        write(server, [])
    assert server.events == []
    assert write(server, [], schema=SAMPLE_SCHEMA) == 0
    assert server.rows("customer") == [] and ("customer" in {k[1] for k in server.tables})
    assert not any(s.startswith(("PUT", "COPY", "REMOVE")) for s in server.statements())


def test_batches_with_different_columns_fail_and_clean_up(server):
    other = pa.RecordBatch.from_pydict({"x": [1]})
    with pytest.raises(ShapeError, match="has columns"):
        write(server, [sample_batch(), other])
    assert ("PUBLIC", "customer") not in server.tables
    assert server.staged_files("customer") == []


# -- chunks and commit_rows ---------------------------------------------------------------
@pytest.mark.parametrize(("rows", "files"), [(1, 1), (4, 1), (5, 2), (8, 2), (9, 3)])
def test_chunk_rows_boundaries(server, rows, files):
    write(server, [sample_batch(0, rows)], chunk_rows=4)
    puts = [s for s in server.statements() if s.startswith("PUT")]
    assert len(puts) == files
    assert [t.num_rows for _, t in server.uploaded] == [4] * (rows // 4) + (
        [rows % 4] if rows % 4 else []
    )
    assert sum(s == COPY for s in server.statements()) == 1  # one COPY INTO for all the files
    assert len(server.rows("customer")) == rows


def test_batches_are_regrouped_across_chunk_boundaries(server):
    write(server, [sample_batch(0, 3), sample_batch(3, 3), sample_batch(6, 3)], chunk_rows=4)
    assert [t.num_rows for _, t in server.uploaded] == [4, 4, 1]
    assert [r[0] for r in server.rows("customer")] == list(range(9))


def test_the_default_chunk_is_a_million_rows():
    assert cloud.DEFAULT_CHUNK_ROWS == 1_000_000


@pytest.mark.parametrize(
    ("sizes", "copies"),
    [([], 0), ([1], 1), ([4], 1), ([5], 1), ([4, 1], 2), ([4, 4], 2), ([2, 2, 1], 2), ([3, 3], 1)],
)
def test_commit_rows_boundaries(server, sizes, copies):
    """A commit falls on the end of an input batch once at least 4 rows are waiting."""
    batches = [sample_batch(sum(sizes[:i]), n) for i, n in enumerate(sizes)]
    assert write(server, batches, commit_rows=4, schema=SAMPLE_SCHEMA) == sum(sizes)
    assert sum(s == COPY for s in server.statements()) == copies
    assert len(server.rows("customer")) == sum(sizes)
    commits = [e for e in server.events if e == ("commit",)]
    assert len(commits) >= copies + 1


def test_commit_rows_of_one_does_not_make_one_row_files(server):
    """``shape emit --to`` asks for a commit per batch (commit_rows=1)."""
    write(server, [sample_batch(0, 50), sample_batch(50, 50)], commit_rows=1)
    assert [t.num_rows for _, t in server.uploaded] == [50, 50]
    assert sum(s == COPY for s in server.statements()) == 2


def test_a_batch_larger_than_chunk_rows_is_loaded_by_one_copy_under_commit_rows(server):
    write(server, [sample_batch(0, 9)], commit_rows=4, chunk_rows=4)
    assert [t.num_rows for _, t in server.uploaded] == [4, 4, 1]
    assert sum(s == COPY for s in server.statements()) == 1


def test_a_failure_after_a_commit_keeps_the_rows_and_the_table(server):
    seen = {"copies": 0}

    def fail_second_copy(sql):
        if sql.startswith("COPY"):
            seen["copies"] += 1
            if seen["copies"] == 2:
                raise FakeDriverError("warehouse suspended")

    server.fail_on = fail_second_copy
    with pytest.raises(WriteError, match="4 rows were committed") as info:
        write(server, [sample_batch(0, 4), sample_batch(4, 4), sample_batch(8, 1)], commit_rows=4)
    assert info.value.rows_committed == 4
    assert len(server.rows("customer")) == 4
    assert server.staged_files("customer") == []


# -- row count check and clean-up ---------------------------------------------------------
def test_a_load_of_fewer_rows_than_staged_fails_and_drops_the_new_table(server):
    server.copy_loads_fewer = 2
    with pytest.raises(ShapeError, match="loaded 1 of the 3 staged rows"):
        write(server)
    assert ("PUBLIC", "customer") not in server.tables
    assert server.staged_files("customer") == []
    assert 'DROP TABLE IF EXISTS "customer"' in server.statements()


def test_a_mismatch_in_append_rolls_the_load_back_and_keeps_the_old_rows(server):
    write(server)
    server.copy_loads_fewer = 1
    with pytest.raises(ShapeError, match="loaded 1 of the 2"):
        write(server, [sample_batch(10, 2)], write_mode="append")
    assert len(server.rows("customer")) == 3
    assert server.staged_files("customer") == []


def test_a_copy_with_no_file_processed_fails(server):
    server.purge_fails = False
    original = server.fail_on

    def drop_stage(sql):
        if sql.startswith("COPY"):
            for files in server.stages.values():
                files.clear()
        if original:
            original(sql)

    server.fail_on = drop_stage
    with pytest.raises(ShapeError, match="loaded 0 of the 3"):
        write(server)


def test_a_file_that_did_not_load_fails(server):
    class Failed(FakeSnowflake):
        pass

    real = server.connect

    def connect(**params):
        conn = real(**params)
        cursor = conn.cursor

        def make_cursor():
            cur = cursor()
            execute = cur.execute

            def run(sql, params=None):
                execute(sql, params)
                if sql.startswith("COPY"):
                    cur._result = [(n, "LOAD_FAILED", 3, 0, 1, 3) for n, *_ in cur._result]

            cur.execute = run
            return cur

        conn.cursor = make_cursor
        return conn

    with pytest.raises(ShapeError, match="did not load a staged file"):
        make_with(connect).write(URI, "customer", iter([sample_batch()]))


def make_with(connect):
    return SnowflakeSink(connect=connect)


def test_staged_files_are_removed_when_a_step_fails(server):
    def fail(sql):
        if sql.startswith("COPY"):
            raise FakeDriverError("SQL compilation error")

    server.fail_on = fail
    with pytest.raises(WriteError, match="SQL compilation error"):
        write(server, [sample_batch(0, 9)], chunk_rows=4)
    assert server.staged_files("customer") == []
    assert ("PUBLIC", "customer") not in server.tables
    assert any(s.startswith("REMOVE") for s in server.statements())


def test_staged_files_are_removed_when_the_put_fails_midway(server):
    puts = {"n": 0}

    def fail(sql):
        if sql.startswith("PUT"):
            puts["n"] += 1
            if puts["n"] == 2:
                raise FakeDriverError("stage full")

    server.fail_on = fail
    with pytest.raises(WriteError, match="stage full"):
        write(server, [sample_batch(0, 9)], chunk_rows=4)
    assert server.staged_files("customer") == []
    assert ("PUBLIC", "customer") not in server.tables


def test_staged_files_are_removed_when_the_source_dies_midway(server):
    def gen():
        yield sample_batch(0, 5)
        raise RuntimeError("source died")

    with pytest.raises(WriteError, match="source died"):
        write(server, gen(), chunk_rows=3)
    assert server.staged_files("customer") == []
    assert ("PUBLIC", "customer") not in server.tables


def test_local_files_are_removed_in_every_case(server):
    write(server, [sample_batch(0, 9)], chunk_rows=4)
    paths = list(server.put_paths)
    assert paths and not any(p.exists() for p in paths)
    assert not any(p.parent.exists() for p in paths)
    server.put_paths.clear()
    server.fail_on = lambda sql: (
        (_ for _ in ()).throw(FakeDriverError("x")) if sql.startswith("COPY") else None
    )
    with pytest.raises(WriteError):
        write(server, [sample_batch(0, 9)], chunk_rows=4, write_mode="append")
    assert not any(p.exists() or p.parent.exists() for p in server.put_paths)


def test_a_remove_that_fails_is_a_warning_not_a_failure(server):
    def fail(sql):
        if sql.startswith("REMOVE"):
            raise FakeDriverError("no privilege")

    server.fail_on = fail
    with pytest.warns(RuntimeWarning, match="could not remove the staged files"):
        assert write(server) == 3


def test_remove_is_issued_even_when_purge_worked(server):
    write(server)
    assert any(s.startswith("REMOVE") for s in server.statements())


# -- staged data --------------------------------------------------------------------------
def test_staged_parquet_has_utc_microseconds_and_decoded_dictionaries(server):
    batch = pa.RecordBatch.from_pydict(
        {
            "ts_ns": pa.array([dt.datetime(2024, 1, 1, 12, 30, 5, 123456)], pa.timestamp("ns")),
            "ts_tz": pa.array(
                [dt.datetime(2024, 1, 1, 12, tzinfo=dt.timezone(dt.timedelta(hours=2)))],
                pa.timestamp("us", "Europe/Berlin"),
            ),
            "kind": pa.array(["a"]).dictionary_encode(),
        }
    )
    write(server, [batch])
    (_, staged) = server.uploaded[0]
    assert staged.schema.field("ts_ns").type == pa.timestamp("us")
    assert staged.schema.field("ts_tz").type == pa.timestamp("us", "UTC")
    assert staged.column("ts_tz").to_pylist() == [dt.datetime(2024, 1, 1, 10, tzinfo=dt.UTC)]
    assert staged.schema.field("kind").type == pa.string()


def test_values_are_never_part_of_a_statement(server):
    batch = sample_batch(0, 3)
    write(server, [batch])
    text = "\n".join(server.statements())
    for value in ("n0", "n1", "n2", "2000-01-01"):
        assert value not in text


# -- names --------------------------------------------------------------------------------
HOSTILE = [
    'a"b',
    'x"; DROP TABLE y; --',
    "it's",
    "%s",
    "`tick`",
    "ünï cödé",
    "sp ace",
    "a.b",
    "-- c",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_table_and_column_names_are_quoted(server, name):
    batch = pa.RecordBatch.from_pydict({name: [1, 2], "ok": ["a", "b"]})
    write(server, [batch], table=name, schema_name=name)
    quoted = '"' + name.replace('"', '""') + '"'
    statements = server.statements()
    create = next(s for s in statements if s.startswith("CREATE TABLE"))
    assert create.startswith(f"CREATE TABLE {quoted}.{quoted} (")
    assert f"  {quoted} NUMBER(38,0)," in create
    assert f"COPY INTO {quoted}.{quoted} FROM @{quoted}.%{quoted} " in "\n".join(statements)
    assert server.tables[(name, name)] == [(1, "a"), (2, "b")]


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("a" * 256, "longer than the 255"),
        ("", "non-empty"),
        ("bad\x00name", "NUL"),
        ("bad\nname", "control character"),
        (" lead", "start or end with a space"),
        ("trail ", "start or end with a space"),
    ],
)
@pytest.mark.parametrize("kind", ["table", "column", "schema"])
def test_names_the_database_would_change_are_refused_before_connecting(server, name, text, kind):
    batch = pa.RecordBatch.from_pydict({(name if kind == "column" else "c"): [1]})
    options = {"schema_name": name} if kind == "schema" else {}
    with pytest.raises(ShapeError, match=text) as info:
        write(server, [batch], table=name if kind == "table" else "t", **options)
    assert name[:30] in str(info.value) or repr(name) in str(info.value) or not name
    assert server.events == []


def test_255_characters_is_accepted(server):
    name = "a" * 255
    write(server, [pa.RecordBatch.from_pydict({name: [1]})], table=name)
    assert (("PUBLIC", name)) in server.tables


def test_duplicate_column_names_are_refused(server):
    batch = pa.RecordBatch.from_arrays([pa.array([1]), pa.array([2])], names=["a", "a"])
    with pytest.raises(ShapeError, match="appears twice"):
        write(server, [batch])
    assert server.events == []


# -- types --------------------------------------------------------------------------------
def test_type_map():
    f = pa.field
    cases = [
        (pa.int8(), "NUMBER(38,0)"),
        (pa.int64(), "NUMBER(38,0)"),
        (pa.uint64(), "NUMBER(38,0)"),
        (pa.float32(), "FLOAT"),
        (pa.float64(), "FLOAT"),
        (pa.decimal128(10, 2), "NUMBER(10,2)"),
        (pa.decimal128(38, 0), "NUMBER(38,0)"),
        (pa.string(), "VARCHAR"),
        (pa.large_string(), "VARCHAR"),
        (pa.bool_(), "BOOLEAN"),
        (pa.date32(), "DATE"),
        (pa.timestamp("us"), "TIMESTAMP_NTZ"),
        (pa.timestamp("ns"), "TIMESTAMP_NTZ"),
        (pa.timestamp("us", "UTC"), "TIMESTAMP_TZ"),
        (pa.timestamp("us", "Europe/Berlin"), "TIMESTAMP_TZ"),
        (pa.binary(), "BINARY"),
        (pa.large_binary(), "BINARY"),
        (pa.list_(pa.int32()), "VARIANT"),
        (pa.struct([("a", pa.int32())]), "VARIANT"),
        (pa.map_(pa.string(), pa.int32()), "VARIANT"),
        (pa.dictionary(pa.int8(), pa.string()), "VARCHAR"),
    ]
    for arrow, expected in cases:
        assert cloud.snowflake_type(f("c", arrow)) == expected, arrow


@pytest.mark.parametrize(
    "arrow", [pa.duration("us"), pa.time64("us"), pa.null(), pa.decimal256(40, 2)]
)
def test_a_type_that_cannot_be_stored_is_refused_before_connecting(server, arrow):
    batch = pa.RecordBatch.from_arrays([pa.array([None], arrow)], names=["weird"])
    with pytest.raises(ShapeError, match="weird"):
        write(server, [batch])
    assert server.events == []


def test_max_length_must_fit_a_varchar(server):
    for bad in (0, -1, 16_777_217, True, "10"):
        with pytest.raises(ShapeError, match="max_length"):
            write(server, columns={"name": {"max_length": bad}})
    assert server.events == []


# -- the destination ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("uri", "text"),
    [
        ("snowflake://shape:hunter2@acct/DB", "password must not be part of the URI"),
        ("snowflake://shape@acct/DB?password=hunter2", "password must not be part of the URI"),
        ("snowflake://shape@acct/DB?private_key=abc", "private_key must not be part of the URI"),
        ("snowflake://shape@acct/DB?token=abc", "token must not be part of the URI"),
        ("snowflake://shape@acct/DB?colour=red", "unknown URI parameter 'colour'"),
        ("snowflake://shape@acct/DB/S/extra", "too many path segments"),
        ("snowflake://acct/DB", "needs a user"),
        ("snowflake://shape@/DB", "needs an account"),
        ("snowflake://shape@acct:443/DB", "no port"),
        ("postgresql://shape@acct/DB", "scheme must be one of snowflake"),
        ("snowflake://shape@acct/D" + "B" * 255, "longer than the 255"),
    ],
)
def test_bad_destinations_are_refused_before_connecting(server, uri, text):
    with pytest.raises(ShapeError, match=text) as info:
        write(server, uri=uri)
    assert "hunter2" not in str(info.value)
    assert server.events == []


# -- credentials --------------------------------------------------------------------------
def _key_pem(passphrase=None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    enc = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc
    ).decode()


def sent(server):
    return server.events[0][1]


def test_password_option_reference_and_environment(server, monkeypatch, tmp_path):
    write(server, password="explicit")
    assert sent(server)["password"] == "explicit"
    monkeypatch.setenv("SF_PW", SECRET)
    write(server, password="env://SF_PW", write_mode="append")
    assert server.events[[e[0] for e in server.events].index("connect", 1)][1]["password"] == SECRET
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "from-env")
    other = FakeSnowflake()
    write(other)
    assert sent(other)["password"] == "from-env"
    path = tmp_path / "pw"
    path.write_text(SECRET + "\n", encoding="utf-8")
    path.chmod(0o600)
    third = FakeSnowflake()
    write(third, password=f"file://{path}")
    assert sent(third)["password"] == SECRET


def test_key_pair_from_a_file_reference_with_a_passphrase(server, tmp_path):
    pem = _key_pem("pass-phrase-1")
    path = tmp_path / "rsa_key.p8"
    path.write_text(pem, encoding="utf-8")
    path.chmod(0o600)
    os.environ["SF_KEY_PASS"] = "pass-phrase-1"
    try:
        write(server, private_key=f"file://{path}", private_key_passphrase="env://SF_KEY_PASS")
    finally:
        del os.environ["SF_KEY_PASS"]
    params = sent(server)
    assert params["private_key_pem"] == pem.strip()
    assert params["private_key_passphrase"] == "pass-phrase-1"
    assert "password" not in params
    der = der_from_pem(params["private_key_pem"], params["private_key_passphrase"])
    assert isinstance(der, bytes) and len(der) > 1000


def test_key_pair_from_the_environment_and_from_resolved_pem(server, monkeypatch):
    pem = _key_pem()
    monkeypatch.setenv("SF_KEY", pem)
    write(server, private_key="env://SF_KEY")
    assert sent(server)["private_key_pem"] == pem
    other = FakeSnowflake()
    write(other, private_key=pem)  # what --sink-config hands over after resolving the reference
    assert sent(other)["private_key_pem"] == pem


def test_a_wrong_passphrase_or_a_bad_key_says_so_without_the_key():
    pem = _key_pem("right")
    with pytest.raises(CredentialError, match="could not be read") as info:
        der_from_pem(pem, "wrong")
    assert "right" not in str(info.value) and "BEGIN" not in str(info.value)
    with pytest.raises(CredentialError, match="could not be read"):
        der_from_pem(f"{BEGIN}\nnot a key\n{END}", None)


@pytest.mark.parametrize(
    ("options", "text"),
    [
        ({"private_key": "just some text"}, "reference to a PEM"),
        ({"private_key": ""}, "file:// or env://"),
        ({"private_key": 5}, "file:// or env://"),
        ({"private_key": "env://SF_NOT_SET_ANYWHERE"}, "SF_NOT_SET_ANYWHERE"),
        ({"private_key_passphrase": "x"}, "without private_key"),
        ({"private_key": BEGIN, "password": "pw"}, "not both"),
        ({"password": 5}, "password must be"),
    ],
)
def test_credential_mistakes_are_refused_before_connecting(server, options, text):
    with pytest.raises(CredentialError, match=text):
        write(server, **options)
    assert server.events == []


def test_no_credentials_fails_with_the_options_named(monkeypatch):
    sink = SnowflakeSink()
    monkeypatch.setitem(sys.modules, "snowflake", type(sys)("snowflake"))
    fake_connector = type(sys)("snowflake.connector")
    fake_connector.connect = lambda **kw: pytest.fail("must not connect")
    monkeypatch.setitem(sys.modules, "snowflake.connector", fake_connector)
    with pytest.raises(CredentialError, match="SNOWFLAKE_PASSWORD"):
        sink.write(URI, "t", iter([sample_batch()]))


def test_the_driver_receives_the_key_as_der(monkeypatch):
    got = {}
    monkeypatch.setitem(sys.modules, "snowflake", type(sys)("snowflake"))
    connector = type(sys)("snowflake.connector")

    def connect(**params):
        got.update(params)
        return FakeSnowflake().connect()

    connector.connect = connect
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    sink = SnowflakeSink()
    sink.default_connect(account="a", user="u", private_key_pem=_key_pem(), autocommit=False)
    assert isinstance(got["private_key"], bytes) and "private_key_pem" not in got
    assert got["application"] == "sqllocks-shape"


def test_a_secret_is_in_no_message_log_or_repr(server, caplog, tmp_path):
    pem = _key_pem("phrase-x")
    server.fail_on = lambda sql: (
        (_ for _ in ()).throw(
            FakeDriverError(f"auth failed password={SECRET} private_key={pem} phrase-x")
        )
        if sql.startswith("PUT")
        else None
    )
    with caplog.at_level(logging.DEBUG), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(WriteError) as info:
            write(server, private_key=pem, private_key_passphrase="phrase-x")
    everything = str(info.value) + caplog.text + repr(info.value)
    for secret in (SECRET, "phrase-x", pem, pem.splitlines()[1]):
        assert secret not in everything
    pw = FakeSnowflake(fail_connect=f"login failed for {SECRET}")
    with pytest.raises(WriteError, match="could not connect") as info2:
        write(pw, password=SECRET)
    assert SECRET not in str(info2.value) and info2.value.__context__ is None


def test_a_connect_failure_names_the_account_not_the_secret(server):
    down = FakeSnowflake(fail_connect="Failed to connect")
    with pytest.raises(WriteError, match=r"could not connect to shape@xy12345.eu-west-1/SHAPE_DB"):
        write(down, password="pw")


# -- the driver -----------------------------------------------------------------------------
def test_a_missing_driver_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "snowflake", None)
    monkeypatch.setitem(sys.modules, "snowflake.connector", None)
    with pytest.raises(ShapeError, match=r"pip install 'sqllocks-shape-databases\[snowflake\]'"):
        SnowflakeSink().write(URI, "t", iter([sample_batch()]), password="pw")


def test_decimal_values_survive(server):
    write(server)
    assert server.rows("customer")[1][3] == Decimal("0.25")
