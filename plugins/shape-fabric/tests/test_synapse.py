"""The ``synapse`` sink and ``SynapseWriter``: staging in ADLS Gen2, ``COPY INTO`` a dedicated SQL
pool, table options, verification, cleanup and secrets, against the in-repo fake pool."""

import io
import logging
import tomllib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shape_fabric import SynapseSink, SynapseWriter, WriteError, sinks
from shape_fabric.synapse import (
    AdlsPath,
    copy_into_sql,
    distribution_clause,
    index_clause,
    parse_staging,
    table_options,
)
from shape_fabric.testing import FakeSynapsePool, MemoryFS, sample_batch, sample_batches
from shape_fabric.warehouse import staging_slug

from shape.errors import ShapeError
from shape.plugins import kit

pytestmark = pytest.mark.contract

URI = "synapse://myws.sql.azuresynapse.net/pool1"
CS = (
    "Driver={ODBC Driver 18 for SQL Server};Server=myws.sql.azuresynapse.net;"
    "Database=pool1;UID=u;PWD=pw"
)
CS_ENTRA = "Driver={ODBC Driver 18 for SQL Server};Server=myws.sql.azuresynapse.net;Database=pool1"
STAGING = "abfss://stage@myacct.dfs.core.windows.net/shape"
HTTPS = "https://myacct.dfs.core.windows.net/stage/shape/staging/run000000001"
PASSWORD = "Tr0ub4dor&3-never-shown"


def make(connection_string=CS_ENTRA, **kw):
    fs = MemoryFS()
    pool = FakeSynapsePool(fs)
    writer = SynapseWriter(
        connection_string,
        STAGING,
        connect=pool.connect,
        filesystem=fs,
        run_id="run000000001",
        **kw,
    )
    return writer, pool, fs


# -- the flow -----------------------------------------------------------------------------
def test_stages_parquet_then_copies_once_with_the_managed_identity_and_cleans_up(batches):
    w, pool, fs = make()
    assert w.write_table("customer", batches, chunk_rows=5) == 7
    assert len(pool.rows("dbo", "customer")) == 7
    copies = [s for s in pool.statements if s.startswith("COPY INTO")]
    assert copies == [
        f"COPY INTO [dbo].[customer] FROM '{HTTPS}/customer-{staging_slug('customer')[-8:]}/' "
        "WITH (FILE_TYPE = 'PARQUET', CREDENTIAL = (IDENTITY = 'Managed Identity'))"
    ]
    assert fs.files == {}  # staging removed
    assert any("chunk_000001.parquet" in k for k in fs.opened)  # 7 rows at 5 per file: 2 files
    assert pool.copy_identities == ["managed_identity"]


def test_the_signed_in_identity_sends_no_credential_clause(batches):
    w, pool, _ = make(copy_identity="signed_in")
    w.write_table("customer", batches)
    copy = next(s for s in pool.statements if s.startswith("COPY INTO"))
    assert copy.endswith("WITH (FILE_TYPE = 'PARQUET')")
    assert pool.copy_identities == ["signed_in"]


def test_a_sql_login_cannot_use_the_signed_in_identity():
    with pytest.raises(ShapeError, match="SQL login"):
        make(CS, copy_identity="signed_in")
    with pytest.raises(ShapeError, match="copy_identity must be one of"):
        make(copy_identity="anyone")


def test_synapse_types_are_the_warehouse_ones_and_the_key_is_not_enforced(batches):
    w, pool, _ = make()
    w.write_table("customer", batches, primary_key=["id"])
    ddl = next(s for s in pool.statements if s.startswith("CREATE TABLE"))
    assert "VARCHAR(8000)" in ddl and "NVARCHAR" not in ddl and "DATETIME2(6)" in ddl
    assert "PRIMARY KEY NONCLUSTERED ([id]) NOT ENFORCED" in ddl


def test_write_modes_match_the_warehouse_writer(batches):
    w, pool, _ = make()
    w.write_table("t", batches)
    with pytest.raises(WriteError, match="already exists"):
        w.write_tables({"t": batches})
    assert len(pool.rows("dbo", "t")) == 7  # the default never touches a table
    w.write_table("t", batches, write_mode="append")
    assert len(pool.rows("dbo", "t")) == 14
    w.write_table("t", batches[:1], write_mode="truncate")
    assert len(pool.rows("dbo", "t")) == 4
    w.write_table("t", batches[1:], write_mode="replace")
    assert len(pool.rows("dbo", "t")) == 3
    with pytest.raises(ShapeError, match="unknown write mode"):
        w.write_table("t", batches, write_mode="upsert")


def test_a_table_that_exists_keeps_its_own_options(batches):
    w, pool, _ = make()
    w.write_table("t", batches, distribution="REPLICATE", index="HEAP")
    w.write_table("t", batches, write_mode="append", distribution="HASH(id)")
    assert pool.table_options[("dbo", "t")] == "DISTRIBUTION = REPLICATE, HEAP"
    assert sum(s.startswith("CREATE TABLE") for s in pool.statements) == 1


# -- table options ------------------------------------------------------------------------
def test_defaults_are_round_robin_and_a_clustered_columnstore_index(batches):
    w, pool, _ = make()
    w.write_table("t", batches)
    assert (
        pool.table_options[("dbo", "t")]
        == "DISTRIBUTION = ROUND_ROBIN, CLUSTERED COLUMNSTORE INDEX"
    )
    ddl = next(s for s in pool.statements if s.startswith("CREATE TABLE"))
    assert ddl.endswith("\nWITH (DISTRIBUTION = ROUND_ROBIN, CLUSTERED COLUMNSTORE INDEX)")


@pytest.mark.parametrize(
    ("distribution", "index", "expected"),
    [
        ("round_robin", "heap", "DISTRIBUTION = ROUND_ROBIN, HEAP"),
        (
            "REPLICATE",
            "clustered columnstore index",
            "DISTRIBUTION = REPLICATE, CLUSTERED COLUMNSTORE INDEX",
        ),
        ("HASH(id)", None, "DISTRIBUTION = HASH ([id]), CLUSTERED COLUMNSTORE INDEX"),
        ("hash( name )", "HEAP", "DISTRIBUTION = HASH ([name]), HEAP"),
        ("  HASH(id)  ", " HEAP ", "DISTRIBUTION = HASH ([id]), HEAP"),
    ],
)
def test_table_options_are_built_from_checked_text(batches, distribution, index, expected):
    w, pool, _ = make()
    w.write_table("t", batches, distribution=distribution, index=index)
    assert pool.table_options[("dbo", "t")] == expected


@pytest.mark.parametrize(
    ("kwargs", "text"),
    [
        ({"distribution": "HASH(nope)"}, "'nope' is not a column of the table"),
        ({"distribution": "HASH()"}, "is not a column"),
        ({"distribution": "HASH(id, name)"}, "is not a column"),
        ({"distribution": "HASH [id]"}, "is not ROUND_ROBIN"),
        ({"distribution": "ROUND_ROBIN; DROP TABLE x"}, "is not ROUND_ROBIN"),
        ({"distribution": "BROADCAST"}, "is not ROUND_ROBIN"),
        ({"distribution": 5}, "distribution must be"),
        ({"index": "CLUSTERED INDEX"}, "index must be one of"),
        ({"index": "HEAP; DROP TABLE x"}, "index must be one of"),
        ({"index": 5}, "index must be one of"),
    ],
)
def test_bad_table_options_are_refused_before_connecting(batches, kwargs, text):
    w, pool, fs = make()
    with pytest.raises(ShapeError, match=text):
        w.write_table("t", batches, **kwargs)
    assert pool.connections == 0 and pool.statements == [] and fs.files == {}


def test_a_hostile_hash_column_name_stays_one_identifier():
    name = "x]; DROP TABLE y; --"
    batch = pa.RecordBatch.from_pydict({name: [1, 2]})
    w, pool, _ = make()
    w.write_table("t", [batch], distribution=f"HASH({name})")
    assert pool.table_options[("dbo", "t")] == (
        "DISTRIBUTION = HASH ([x]]; DROP TABLE y; --]), CLUSTERED COLUMNSTORE INDEX"
    )
    assert pool.rows("dbo", "t") == [(1,), (2,)]


def test_the_clauses():
    schema = pa.schema([("a", pa.int64())])
    assert distribution_clause(None, schema) == "DISTRIBUTION = ROUND_ROBIN"
    assert index_clause(None) == "CLUSTERED COLUMNSTORE INDEX"
    assert table_options("REPLICATE", "HEAP", schema) == "DISTRIBUTION = REPLICATE, HEAP"


# -- counting and cleaning up ---------------------------------------------------------------
def test_a_copy_that_loads_fewer_rows_than_were_staged_fails_and_drops_the_new_table(batches):
    w, pool, fs = make()
    pool.copy_loads_fewer = 2
    with pytest.raises(ShapeError, match="loaded 5 of the 7 staged rows"):
        w.write_table("customer", batches)
    assert ("dbo", "customer") not in pool.tables and fs.files == {}


def test_a_driver_without_a_rowcount_is_checked_by_counting_rows(batches):
    w, pool, _ = make()
    pool.copy_reports_rowcount = False
    assert w.write_table("customer", batches) == 7
    w.write_table("customer", batches, write_mode="append")
    assert len(pool.rows("dbo", "customer")) == 14
    pool.copy_loads_fewer = 1
    with pytest.raises(ShapeError, match="loaded 6 of the 7"):
        w.write_table("customer", batches, write_mode="append")


def test_staging_is_removed_when_the_copy_fails(batches):
    w, pool, fs = make()

    def fail(sql, params):
        if sql.startswith("COPY INTO"):
            raise RuntimeError("Bulk load failed")

    pool.fail = fail
    with pytest.raises(WriteError, match="Bulk load failed"):
        w.write_table("customer", batches)
    assert fs.files == {} and ("dbo", "customer") not in pool.tables


def test_staging_is_removed_when_the_source_fails_midway():
    w, pool, fs = make()

    def gen():
        yield sample_batch(0, 5)
        raise RuntimeError("source died")

    with pytest.raises(WriteError, match="source died"):
        w.write_table("t", gen(), chunk_rows=3)
    assert fs.files == {} and ("dbo", "t") not in pool.tables


@pytest.mark.parametrize(("rows", "files"), [(1, 1), (4, 1), (5, 2), (8, 2), (9, 3)])
def test_chunk_rows_boundaries(rows, files):
    w, pool, fs = make()
    seen = []
    orig = fs.rm

    def peek(path, recursive=False):
        seen.extend(sorted(fs.files))
        orig(path, recursive)

    fs.rm = peek
    w.write_table("t", [sample_batch(0, rows)], chunk_rows=4)
    assert len(seen) == files
    assert sum(s.startswith("COPY INTO") for s in pool.statements) == 1
    assert len(pool.rows("dbo", "t")) == rows


def test_no_rows_creates_the_table_and_stages_nothing():
    w, pool, fs = make()
    with pytest.raises(ShapeError, match="no batches and no schema"):
        w.write_table("t", [])
    schema = sample_batch().schema
    assert w.write_table("t", [], schema=schema) == 0
    assert ("dbo", "t") in pool.tables and pool.rows("dbo", "t") == []
    assert not any(s.startswith("COPY") for s in pool.statements) and fs.opened == []


def test_staged_timestamps_are_microseconds(batches):
    w, pool, fs = make()
    seen = {}
    orig = fs.rm

    def keep(path, recursive=False):
        for key, data in fs.files.items():
            seen[key] = pq.read_table(io.BytesIO(data)).schema
        orig(path, recursive)

    fs.rm = keep
    w.write_table("customer", batches)
    (schema,) = seen.values()
    assert schema.field("seen").type == pa.timestamp("us")
    assert schema.field("segment").type == pa.string()


def test_a_staging_path_the_copy_cannot_take_fails_after_cleaning_up(batches):
    fs = MemoryFS()
    pool = FakeSynapsePool(fs)
    w = SynapseWriter(
        CS_ENTRA,
        "abfss://stage@myacct.dfs.core.windows.net/it's",
        connect=pool.connect,
        filesystem=fs,
    )
    with pytest.raises(ShapeError, match="not a usable staging location"):
        w.write_table("t", batches)
    assert fs.files == {} and ("dbo", "t") not in pool.tables


def test_copy_statement_quotes_names_and_validates_the_location():
    sql = copy_into_sql("s]x", "t]y", "https://acct.dfs.core.windows.net/c/p/", "signed_in")
    assert sql.startswith("COPY INTO [s]]x].[t]]y] FROM 'https://acct.dfs.core.windows.net/c/p/'")
    for bad in (
        "https://a.dfs.core.windows.net/c/p'; DROP TABLE x; --/",
        "http://a/b",
        "https://a/b--c",
    ):
        with pytest.raises(ShapeError, match="not a usable staging location"):
            copy_into_sql("dbo", "t", bad, "managed_identity")


# -- staging_path -------------------------------------------------------------------------
def test_staging_path_is_required_and_must_be_adls_gen2():
    with pytest.raises(ShapeError, match="needs staging_path"):
        SynapseWriter(CS_ENTRA, None)
    for bad in (
        "onelake://Analytics/Sales/Files",
        "abfss://ws@onelake.dfs.fabric.microsoft.com/Sales.Lakehouse/Files",
        "https://myacct.dfs.core.windows.net/stage",
        "abfss://stage@myacct.blob.core.windows.net/shape",
        "abfss://stage@MY_ACCT.dfs.core.windows.net/shape",
        "abfss://ST@myacct.dfs.core.windows.net/shape",
        "abfss://myacct.dfs.core.windows.net/shape",
        "/tmp/shape",
        5,
    ):
        with pytest.raises(ShapeError, match="ADLS Gen2"):
            SynapseWriter(CS_ENTRA, bad)


def test_parse_staging_builds_the_https_location():
    path = parse_staging("abfss://stage@MyAcct.dfs.core.windows.net/a/b/")
    assert path == AdlsPath("stage", "myacct.dfs.core.windows.net", "a/b")
    assert path.https() == "https://myacct.dfs.core.windows.net/stage/a/b"
    assert path.join("x", "y").abfss() == "abfss://stage@myacct.dfs.core.windows.net/a/b/x/y"
    assert parse_staging("abfss://stage@acct1.dfs.core.windows.net").https() == (
        "https://acct1.dfs.core.windows.net/stage"
    )
    with pytest.raises(ShapeError):
        path.join("..")


# -- the sink -----------------------------------------------------------------------------
class Cred:
    def get_token(self, *scopes, **kwargs):
        raise AssertionError("the fake pool never asks for a token")


class Spy:
    def __init__(self):
        self.fs = MemoryFS()
        self.pool = FakeSynapsePool(self.fs)
        self.calls = []

    def __call__(self, connection_string, credential=None, **kw):
        self.calls.append((connection_string, credential))
        return self.pool.connect(connection_string, credential, **kw)


def write(spy, uri=URI, batches=None, **options):
    options.setdefault("staging_path", STAGING)
    options.setdefault("filesystem", spy.fs)
    return SynapseSink(connect=spy).write(
        uri, "customer", iter(sample_batches() if batches is None else batches), **options
    )


def test_the_sink_is_registered_under_the_sinks_group():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    eps = tomllib.loads(pyproject.read_text())["project"]["entry-points"]["shape.sinks"]
    assert eps["synapse"] == "shape_fabric:SynapseSink"
    assert (SynapseSink.name, SynapseSink.schemes) == ("synapse", ("synapse",))
    assert sinks.SynapseSink is SynapseSink


def test_the_sink_writes_a_table_through_the_pool_endpoint():
    spy = Spy()
    cred = Cred()
    assert write(spy, credential=cred, distribution="HASH(id)", index="HEAP") == 7
    ((cs, credential),) = spy.calls
    assert credential is cred
    assert "Server=myws.sql.azuresynapse.net" in cs and "Database=pool1" in cs
    assert len(spy.pool.rows("dbo", "customer")) == 7
    assert spy.pool.table_options[("dbo", "customer")] == "DISTRIBUTION = HASH ([id]), HEAP"
    assert spy.fs.files == {}


def test_the_sink_takes_schema_name_modes_and_a_connection_string():
    spy = Spy()
    write(spy, connection_string=CS, schema_name="gen", primary_key=["id"])
    assert spy.calls[0][0].startswith("Driver={ODBC Driver 18 for SQL Server};Server=myws.sql")
    assert "UID=u" in spy.calls[0][0]
    assert len(spy.pool.rows("gen", "customer")) == 7
    write(spy, write_mode="append", schema_name="gen")
    assert len(spy.pool.rows("gen", "customer")) == 14


def test_the_sink_needs_a_staging_path_and_refuses_unknown_options():
    spy = Spy()
    with pytest.raises(ShapeError, match="needs staging_path"):
        SynapseSink(connect=spy).write(URI, "t", iter(sample_batches()))
    with pytest.raises(ShapeError, match="unknown synapse sink options"):
        write(spy, colour="red")
    assert spy.calls == []


@pytest.mark.parametrize(
    ("uri", "text"),
    [
        ("synapse://u:hunter2@myws.sql.azuresynapse.net/pool1", "no user part"),
        ("synapse://u@myws.sql.azuresynapse.net/pool1", "no user part"),
        (
            "synapse://myws.sql.azuresynapse.net/pool1?password=hunter2",
            "must never be part of the URI",
        ),
        (
            "synapse://myws.sql.azuresynapse.net/pool1?token=hunter2",
            "must never be part of the URI",
        ),
        ("synapse://myws.sql.azuresynapse.net/pool1?colour=red", "takes no query parameters"),
        ("synapse://myws-ondemand.sql.azuresynapse.net/db", "serverless"),
        ("synapse://myws.sql.azuresynapse.net/", "synapse://<workspace>"),
        ("synapse://myws.sql.azuresynapse.net", "synapse://<workspace>"),
        ("synapse://myws.sql.azuresynapse.net:1433/pool1", "synapse://<workspace>"),
        ("synapse://myws.sql.azuresynapse.net/a/b", "synapse://<workspace>"),
        ("synapse://myws.database.windows.net/pool1", "synapse://<workspace>"),
        ("synapse://evil.example/pool1", "synapse://<workspace>"),
        ("synapse://myws.sql.azuresynapse.net.evil.example/pool1", "synapse://<workspace>"),
        ("warehouse://myws.sql.azuresynapse.net/pool1", "scheme must be synapse"),
    ],
)
def test_bad_destinations_are_refused_before_connecting(uri, text):
    spy = Spy()
    with pytest.raises(ShapeError, match=text) as info:
        write(spy, uri=uri)
    assert "hunter2" not in str(info.value)
    assert spy.calls == [] and spy.fs.opened == []


def test_the_pool_name_cannot_add_connection_attributes():
    spy = Spy()
    write(spy, uri="synapse://myws.sql.azuresynapse.net/my%3BEncrypt%3Dno", credential=Cred())
    assert "Database={my;Encrypt=no};" in spy.calls[0][0]  # braces: no attribute is added


def test_a_secret_is_in_no_message_or_log(caplog):
    spy = Spy()

    def fail(sql, params):
        if sql.startswith("COPY INTO"):
            raise RuntimeError(f"Login failed PWD={PASSWORD};UID=u")

    spy.pool.fail = fail
    cs = CS_ENTRA + f";UID=u;PWD={PASSWORD}"
    with caplog.at_level(logging.DEBUG), pytest.raises(WriteError) as info:
        write(spy, connection_string=cs)
    assert PASSWORD not in str(info.value) + caplog.text + repr(info.value)
    assert spy.fs.files == {} and ("dbo", "customer") not in spy.pool.tables


def test_a_connect_failure_hides_the_password():
    def boom(connection_string, credential=None, **kw):
        raise ShapeError(f"could not connect: PWD={PASSWORD}")

    with pytest.raises(ShapeError) as info:
        SynapseSink(connect=boom).write(
            URI,
            "t",
            iter(sample_batches()),
            staging_path=STAGING,
            filesystem=MemoryFS(),
            connection_string=CS_ENTRA + f";PWD={PASSWORD}",
        )
    assert PASSWORD not in str(info.value) and info.value.__context__ is None


def test_conforms_to_the_sink_protocol_with_the_kit():
    spy = Spy()

    class Configured:
        """The sink with the options it needs (the kit calls ``write`` with none)."""

        name = SynapseSink.name
        schemes = SynapseSink.schemes

        def write(self, uri, table, batches, **options):
            return SynapseSink(connect=spy).write(
                uri, table, batches, staging_path=STAGING, filesystem=spy.fs, **options
            )

    def read_back():
        table = spy.pool.tables[("dbo", "kit_table")]
        rows = spy.pool.rows("dbo", "kit_table")
        names = [c[0] for c in table.columns]
        return pa.table({n: [r[i] for r in rows] for i, n in enumerate(names)})

    kit.check_sink(Configured(), URI, sample_batches(), read_back=read_back)


# -- the command line -------------------------------------------------------------------------
def test_generate_to_synapse_with_a_sql_login(monkeypatch, capsys, tmp_path):
    import json
    import sys

    from shape_fabric import _tsql, synapse

    from shape.cli.main import main

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "scale"))
    from scale_schemas import plain_doc

    rows = {"customer": 40, "order": 1200, "order_line": 3100}
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(plain_doc(rows)))
    fs = MemoryFS()
    pool = FakeSynapsePool(fs)
    monkeypatch.setattr(_tsql, "connect", pool.connect)
    real_storage = synapse.Storage
    monkeypatch.setattr(synapse, "Storage", lambda **kw: real_storage(filesystem=fs))
    monkeypatch.setenv("SYN_PW", PASSWORD)
    uri = "synapse://myws.sql.azuresynapse.net/pool1"
    code = main(
        [
            "generate",
            str(schema_file),
            "--to",
            uri,
            "--auth",
            "sql",
            "--sql-user",
            "loader",
            "--sql-password",
            "env://SYN_PW",
            "--connection-string",
            uri,
            "--sink-config",
            "synapse.staging_path=" + STAGING,
            "--sink-config",
            "synapse.distribution=ROUND_ROBIN",
            "--json",
        ]
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    assert json.loads(out.out)["targets"][uri] == rows
    assert [len(pool.rows("dbo", t)) for t in rows] == list(rows.values())
    assert fs.files == {}
    assert PASSWORD not in out.out + out.err
    assert pool.copy_identities == ["managed_identity"] * 3


def test_a_secret_in_the_uri_is_refused_on_the_command_line(capsys, tmp_path):
    from shape.cli.main import main

    code = main(
        [
            "generate",
            str(tmp_path / "x.json"),
            "--to",
            "synapse://u:hunter2@w.sql.azuresynapse.net/p",
        ]
    )
    out = capsys.readouterr()
    assert code == 2 and "hunter2" not in out.out + out.err


def test_synapse_is_listed_by_plugins_list():
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.cli.main import main; sys.exit(main())",
            "plugins",
            "list",
            "--json",
            "--group",
            "shape.sinks",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = {x["name"]: x for x in json.loads(result.stdout)}
    assert rows["synapse"]["source"] == "sqllocks-shape-fabric"


# -- commit_rows ------------------------------------------------------------------------------
def copies(spy):
    return sum(s.startswith("COPY INTO") for s in spy.pool.statements)


@pytest.mark.parametrize(
    ("sizes", "loads"),
    [([], 0), ([1], 1), ([4], 1), ([5], 1), ([4, 1], 2), ([4, 4], 2), ([2, 2, 1], 2), ([3, 3], 1)],
)
def test_commit_rows_boundaries(sizes, loads):
    """A load (staging, COPY INTO, commit) falls on the end of an input batch once at least 4 rows
    are waiting; no rows at all still creates the table from the schema."""
    spy = Spy()
    batches = [sample_batch(sum(sizes[:i]), n) for i, n in enumerate(sizes)]
    schema = sample_batch().schema
    assert write(spy, batches=batches, commit_rows=4, schema=schema) == sum(sizes)
    assert copies(spy) == loads
    assert len(spy.pool.rows("dbo", "customer")) == sum(sizes)
    assert spy.fs.files == {}
    assert ("dbo", "customer") in spy.pool.tables


def test_commit_rows_applies_the_write_mode_once():
    spy = Spy()
    write(spy, batches=[sample_batch(0, 4), sample_batch(4, 4)], commit_rows=4)
    assert sum(s.startswith("CREATE TABLE") for s in spy.pool.statements) == 1
    write(
        spy,
        batches=[sample_batch(10, 4), sample_batch(14, 4)],
        commit_rows=4,
        write_mode="replace",
    )
    assert [r[0] for r in spy.pool.rows("dbo", "customer")] == list(range(10, 18))
    assert sum(s.startswith("DROP TABLE") for s in spy.pool.statements) == 1


def test_a_failure_after_a_load_keeps_the_rows_and_the_table_and_says_how_many():
    spy = Spy()
    seen = {"copies": 0}

    def fail(sql, params):
        if sql.startswith("COPY INTO"):
            seen["copies"] += 1
            if seen["copies"] == 2:
                raise RuntimeError("pool paused")

    spy.pool.fail = fail
    batches = [sample_batch(0, 4), sample_batch(4, 4), sample_batch(8, 1)]
    with pytest.raises(WriteError, match="4 rows were committed") as info:
        write(spy, batches=batches, commit_rows=4)
    assert info.value.rows_committed == 4
    assert len(spy.pool.rows("dbo", "customer")) == 4
    assert spy.fs.files == {}


def test_a_failure_in_the_first_load_drops_the_new_table():
    spy = Spy()
    spy.pool.copy_loads_fewer = 1
    with pytest.raises(ShapeError, match="loaded 3 of the 4"):
        write(spy, batches=[sample_batch(0, 4)], commit_rows=4)
    assert ("dbo", "customer") not in spy.pool.tables and spy.fs.files == {}


def test_commit_rows_must_be_a_positive_integer():
    spy = Spy()
    for bad in (0, -1, True, "5"):
        with pytest.raises(ShapeError, match="commit_rows must be a positive integer"):
            write(spy, commit_rows=bad)
    assert spy.calls == []


def test_emit_to_synapse_commits_while_the_stream_runs(monkeypatch, capsys):
    from shape_fabric import _tsql, synapse

    from shape.cli.main import main

    fs = MemoryFS()
    pool = FakeSynapsePool(fs)
    monkeypatch.setattr(_tsql, "connect", pool.connect)
    real_storage = synapse.Storage
    monkeypatch.setattr(synapse, "Storage", lambda **kw: real_storage(filesystem=fs))
    monkeypatch.setenv("SYN_PW", PASSWORD)
    uri = "synapse://myws.sql.azuresynapse.net/pool1"
    code = main(
        [
            "emit",
            "retail",
            "--scale",
            "small",
            "--seed",
            "3",
            "--table",
            "customer",
            "--max-events",
            "250",
            "--to",
            uri,
            "--auth",
            "sql",
            "--sql-user",
            "loader",
            "--sql-password",
            "env://SYN_PW",
            "--connection-string",
            uri,
            "--sink-config",
            "synapse.staging_path=" + STAGING,
        ]
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    assert len(pool.rows("dbo", "customer")) == 250
    assert sum(s.startswith("COPY INTO") for s in pool.statements) >= 1
    assert fs.files == {} and PASSWORD not in out.out + out.err
