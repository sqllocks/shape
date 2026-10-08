"""W9-01 offline source and catalog acceptance."""

import json
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases.testing import FakeServer

from shape.errors import ShapeError


@pytest.fixture(params=["postgres", "mysql"])
def source(request):
    from shape_databases.sources import MySqlSource, PostgresSource

    server = FakeServer(request.param)
    server.seed(
        "accounts",
        [("id", "bigint", False), ("email", "text", True)],
        [(i, f"user{i}@example.com") for i in range(20)],
        primary_key=["id"],
    )
    cls = PostgresSource if request.param == "postgres" else MySqlSource
    return (
        cls(connect=server.connect),
        f"{request.param}://shape@localhost/{server.default_schema}",
        server,
    )


def test_source_batches_schema_and_owned_connection(source):
    src, uri, server = source
    uri += "?table=accounts"
    assert src.can_open(uri)
    assert not src.can_open("file:///tmp/a")
    schema = src.schema(uri)
    assert schema.field("id").type == pa.int64()
    assert not schema.field("id").nullable
    batches = list(src.read(uri, batch_size=7))
    assert [b.num_rows for b in batches] == [7, 7, 6]
    assert all(b.schema == schema for b in batches)
    assert server.events[-1][0] == "close"


@pytest.mark.parametrize(
    "suffix,options,reason",
    [
        ("", {}, "table"),
        ("?table=accounts&bad=1", {}, "unknown"),
        ("?table=accounts", {"bad": 1}, "unknown"),
        ("?table=accounts", {"batch_size": 0}, "batch_size"),
        ("?table=accounts", {"batch_size": True}, "batch_size"),
        ("?table=accounts", {"password": "a", "credential": "env://B"}, "not both"),
    ],
)
def test_source_refuses_before_connection(source, suffix, options, reason):
    src, uri, server = source
    with pytest.raises(ShapeError, match=reason):
        list(src.read(uri + suffix, **options))
    assert not server.events


def test_uri_password_refused_without_echo(source):
    src, uri, server = source
    with pytest.raises(ShapeError) as exc:
        src.schema(uri.replace("shape@", "shape:private-secret@") + "?table=accounts")
    assert "password" in str(exc.value)
    assert "private-secret" not in str(exc.value)
    assert not server.events


@pytest.mark.parametrize(
    "sql,arrow",
    [
        ("smallint", pa.int16()),
        ("integer", pa.int32()),
        ("bigint", pa.int64()),
        ("numeric(10,2)", pa.decimal128(10, 2)),
        ("timestamptz", pa.timestamp("us", tz="UTC")),
        ("timestamp", pa.timestamp("us")),
        ("datetime(6)", pa.timestamp("us")),
        ("date", pa.date32()),
        ("time", pa.time64("us")),
        ("boolean", pa.bool_()),
        ("tinyint(1)", pa.bool_()),
        ("bytea", pa.binary()),
        ("blob", pa.binary()),
        ("uuid", pa.string()),
        ("jsonb", pa.string()),
        ("json", pa.string()),
        ("integer[]", pa.string()),
    ],
)
def test_catalog_type_map(sql, arrow):
    from shape_databases.sources import catalog_field

    field = catalog_field("value", sql, True)
    assert field.type == arrow
    if sql == "uuid":
        assert field.metadata[b"shape.type"] == b"uuid"


def test_unknown_type_warns_once_and_is_text(source):
    src, uri, server = source
    server.seed("odd", [("mystery", "custom_type", True)], [(123,), (None,)])
    with pytest.warns(UserWarning, match="mystery") as warnings:
        batches = list(src.read(uri + "?table=odd", batch_size=1))
    assert len(warnings) == 1
    assert pa.Table.from_batches(batches).to_pydict() == {"mystery": ["123", None]}


def test_decimal_and_json_values(source):
    src, uri, server = source
    server.seed(
        "values",
        [("amount", "numeric(10,2)", True), ("doc", "json", True)],
        [(Decimal("1.20"), {"a": [1, 2]}), (None, None)],
    )
    got = pa.Table.from_batches(src.read(uri + "?table=values")).to_pydict()
    assert got["amount"] == [Decimal("1.20"), None]
    assert json.loads(got["doc"][0]) == {"a": [1, 2]}


@pytest.mark.parametrize("n", [0, 1, 7, 1000])
def test_schema_profile_catalog_sampling_and_declared_keys(source, n):
    src, uri, server = source
    server.seed(
        "orders",
        [("account_id", "bigint", False)],
        [(i,) for i in range(20)],
        foreign_keys=[("accounts", ["id"], ["account_id"])],
    )
    prof = src.profile_database(uri, sample_rows=n)
    assert set(prof.tables) == {"accounts", "orders"}
    accounts = prof.tables["accounts"]
    assert accounts["row_count"] == 20
    assert accounts["primary_key"] == ["id"]
    assert accounts["sampled_rows"] == min(n, 20)
    assert accounts["sample_method"]
    assert prof.to_dict()["sampling"]["requested_rows"] == n
    assert prof.to_dict()["relationships"][0]["evidence"] == "declared"
    if n == 0:
        assert accounts["columns"]["email"]["null_rate"] is None
    statements = "\n".join(server.statements())
    if n and n < 20:
        assert ("TABLESAMPLE SYSTEM" if server.dialect == "postgres" else "CRC32") in statements


def test_table_selection_missing_table_and_negative_sample(source):
    src, uri, server = source
    assert list(src.profile_database(uri, tables=["accounts"]).tables) == ["accounts"]
    with pytest.raises(ShapeError, match="missing"):
        src.profile_database(uri, tables=["missing"])
    for n in [-1, True]:
        with pytest.raises(ShapeError, match="sample_rows"):
            src.profile_database(uri, sample_rows=n)


def test_password_tls_and_error_redaction(source):
    src, uri, server = source
    server.fail_connect = "password=my-private-password SELECT denied"
    with pytest.raises(ShapeError) as exc:
        src.profile_database(uri, password="my-private-password")
    assert "my-private-password" not in str(exc.value)
    assert exc.value.__context__ is None


def test_safe_capture_and_persisted_compatibility(source, tmp_path):
    import shape

    src, uri, _ = source
    prof = src.profile_database(uri)
    path = tmp_path / "profile.shape"
    shape.save(prof, path)
    loaded = shape.load(path)
    assert loaded.tables["accounts"]["primary_key"] == ["id"]
    assert loaded.tables["accounts"]["sampled_rows"] == 20
    assert "user0@example.com" not in json.dumps(loaded.to_dict())


def test_public_profile_dispatch_is_bounded(source, monkeypatch):
    import shape
    from shape.plugins.host import default_host

    src, uri, _ = source
    host = default_host()
    monkeypatch.setattr(
        host, "records", lambda group: [type("Record", (), {"group": group, "name": "db"})()]
    )
    monkeypatch.setattr(host, "try_get", lambda group, name: src)
    prof = shape.profile(uri, sample_rows=3, tables=["accounts"])
    assert prof.tables["accounts"]["sampled_rows"] == 3
    assert prof.tables["accounts"]["row_count"] == 20
    assert (
        shape.profile(uri + "?table=accounts", sample_rows=0).tables["accounts"]["sampled_rows"]
        == 0
    )


def test_cli_database_flags_and_safe_output(source, monkeypatch, tmp_path, capsys):
    from shape.cli.main import main

    src, uri, server = source
    monkeypatch.setattr(src.sink_class, "default_connect", lambda self, **kw: server.connect(**kw))
    target = tmp_path / "cli.shape"
    assert (
        main(["profile", uri, "--sample-rows", "2", "--tables", "accounts", "-o", str(target)]) == 0
    )
    import shape

    loaded = shape.load(target)
    assert loaded.tables["accounts"]["sampled_rows"] == 2
    assert "user0@example.com" not in json.dumps(loaded.to_dict())
    assert "password" not in capsys.readouterr().out


def test_close_failure_redacts_credential(source):
    src, uri, server = source
    conn = server.connect()

    def close():
        raise RuntimeError("close my-private-password")

    conn.close = close
    with pytest.raises(ShapeError) as exc:
        src.profile_database(uri, password="my-private-password", connect=lambda **kw: conn)
    assert "my-private-password" not in str(exc.value)
    assert exc.value.__context__ is None


@pytest.mark.parametrize("uppercase", [False, True])
def test_bridge_database_profile_safe_artifact_and_result(source, monkeypatch, tmp_path, uppercase):
    import shape
    from shape.bridge.core import Bridge

    src, uri, server = source
    monkeypatch.setattr(src.sink_class, "default_connect", lambda self, **kw: server.connect(**kw))
    if uppercase:
        uri = uri.replace(src.schemes[0], src.schemes[0].upper(), 1)
    bridge = Bridge(tmp_path / "jobs")
    try:
        out = tmp_path / "bridge.shape"
        response = bridge.handle(
            {
                "api_version": "1.2",
                "command": "profile",
                "args": {
                    "source": uri,
                    "output": str(out),
                    "sample_rows": 3,
                    "tables": ["accounts"],
                },
            }
        )
        assert response["ok"], response
        assert response["result"]["tables"]["accounts"]["rows"] == 20
        assert "user0@example.com" not in json.dumps(response)
        assert "user0@example.com" not in json.dumps(shape.load(out).to_dict())
    finally:
        bridge.close()


def test_docs_cover_sources_type_map_sampling_and_bridge_security():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for relative in [
        "plugins/shape-databases/README.md",
        "docs/CLI.md",
        "docs/BRIDGE.md",
        "CHANGELOG.md",
    ]:
        doc = (root / relative).read_text()
        assert "postgresql://" in doc and "mysql://" in doc
    readme = (root / "plugins/shape-databases/README.md").read_text()
    assert "decimal128" in readme and "TABLESAMPLE SYSTEM" in readme
    assert "database connection" in (root / "docs/BRIDGE.md").read_text()


@pytest.mark.parametrize(
    "sql, arrow",
    [
        ("timestamp(0) with time zone", pa.timestamp("us", tz="UTC")),
        ("timestamp(6) without time zone", pa.timestamp("us")),
        ("time(6) without time zone", pa.time64("us")),
        ("time(0)", pa.time64("us")),
    ],
)
def test_catalog_precision_variants(sql, arrow):
    from shape_databases.sources import catalog_field

    assert catalog_field("c", sql, True).type == arrow


def test_empty_table_and_empty_schema_boundary(source):
    src, uri, server = source
    server.seed("empty", [("id", "integer", False)], [], primary_key=["id"])
    assert list(src.read(uri + "?table=empty")) == []
    prof = src.profile_database(uri, tables=["empty"])
    assert prof.tables["empty"]["row_count"] == 0
    assert prof.tables["empty"]["primary_key"] == ["id"]
    server.tables.clear()
    with pytest.raises(ShapeError, match="no readable tables"):
        src.profile_database(uri)


def test_source_conformance_and_tls_defaults(source):
    from shape.plugins.api.v1 import Source

    src, uri, server = source
    assert isinstance(src, Source)
    src.schema(uri.replace("localhost", "remote.example") + "?table=accounts", password="secret")
    params = next(e[1] for e in server.events if e[0] == "connect")
    assert params["password"] == "secret"
    if server.dialect == "postgres":
        assert params["sslmode"] == "verify-full"
    else:
        assert params["ssl"].check_hostname
        assert params["local_infile"] is False


def test_password_env_sources(source, monkeypatch):
    src, uri, server = source
    for name in src.sink.password_env:
        monkeypatch.delenv(name, raising=False)
    for name in reversed(src.sink.password_env):
        monkeypatch.setenv(name, "env-password")
        src.schema(uri + "?table=accounts")
        assert [e[1] for e in server.events if e[0] == "connect"][-1]["password"] == "env-password"
        monkeypatch.delenv(name)


def test_unsupported_public_profile_options_are_refused(source, monkeypatch):
    import shape
    from shape.plugins.host import default_host
    from shape.profile.reference.sources import SourceError

    src, uri, _ = source
    host = default_host()
    monkeypatch.setattr(
        host, "records", lambda group: [type("Record", (), {"group": group, "name": "db"})()]
    )
    monkeypatch.setattr(host, "try_get", lambda group, name: src)
    for options in [{"version": 0}, {"sample": 4}, {"sketches": True}]:
        with pytest.raises(SourceError, match="does not support"):
            shape.profile(uri, **options)


def test_sampled_values_count_floor_uses_sample_not_catalog(source, tmp_path):
    import shape

    src, uri, server = source
    # A catalog count must never inflate the support for a released sample value.
    server.seed("labels", [("category", "text", True)], [("Green",)] * 20)
    prof = src.profile_database(uri, tables=["labels"], sample_rows=2)
    out = tmp_path / "labels.shape"
    shape.save(prof, out)
    column = shape.load(out).tables["labels"]["columns"]["category"]
    assert "Green" not in json.dumps(column)
    assert prof.tables["labels"]["row_count"] == 20


def test_fake_generate_write_read_dataset_identity(source):
    from shape_databases.testing import sample_batch

    from shape.repro import dataset_id

    src, uri, server = source
    # Existing SQL sinks declare zone-less timestamps (write fidelity is W9-03).
    # Use a zone-less timestamp fixture; zoned catalog reads are tested independently.
    original = sample_batch()
    fields = pa.schema(
        [
            pa.field(
                f.name, pa.timestamp("us") if f.name == "seen" else f.type, nullable=f.nullable
            )
            for f in original.schema
        ]
    )
    batch = pa.Table.from_batches([original]).cast(fields).to_batches()[0]
    src.sink.write(uri, "roundtrip", [batch], primary_key=["id"])
    got = pa.Table.from_batches(src.read(uri + "?table=roundtrip"))
    assert dataset_id({"roundtrip": got}) == dataset_id(
        {"roundtrip": pa.Table.from_batches([batch])}
    )
    prof = src.profile_database(uri, tables=["roundtrip"])
    assert prof.tables["roundtrip"]["primary_key"] == ["id"]


def test_composite_catalog_primary_and_foreign_keys(source):
    src, uri, server = source
    server.seed(
        "parent",
        [("a", "integer", False), ("b", "integer", False)],
        [(1, 2)],
        primary_key=["a", "b"],
    )
    server.seed(
        "child",
        [("x", "integer", False), ("y", "integer", False)],
        [(1, 2)],
        foreign_keys=[("parent", ["a", "b"], ["x", "y"])],
    )
    prof = src.profile_database(uri, tables=["parent", "child"], sample_rows=0)
    assert prof.tables["parent"]["primary_key"] == ["a", "b"]
    relationship = prof.to_dict()["relationships"][0]
    assert relationship["parent_columns"] == ["a", "b"]
    assert relationship["child_columns"] == ["x", "y"]


@pytest.mark.parametrize(
    "secret_suffix",
    [
        ":private-secret@localhost/db",
        "@localhost/db?%70assword=private-secret",
        "@localhost/db?%63redential=private-secret",
        "@localhost/db?%74oken=private-secret",
    ],
)
def test_bridge_rejects_uri_credentials_without_job_file_leak(source, tmp_path, secret_suffix):
    import time

    from shape.bridge.core import Bridge

    src, _, server = source
    bridge = Bridge(tmp_path / "jobs")
    try:
        uri = src.schemes[0] + "://shape" + secret_suffix
        response = bridge.handle(
            {
                "api_version": "1.0",
                "command": "profile",
                "args": {"source": uri},
                "options": {"async": True},
            }
        )
        assert response["ok"]
        job_id = response["result"]["job_id"]
        deadline = time.monotonic() + 5
        while True:
            status = bridge.handle(
                {"api_version": "1.0", "command": "job_status", "args": {"job_id": job_id}}
            )
            if status["result"]["status"] != "running":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status["result"]["status"] == "failed"
        assert "private-secret" not in json.dumps(status)
        records = list((tmp_path / "jobs").rglob("*.json"))
        assert records
        assert all("private-secret" not in record.read_text() for record in records)
        assert not server.events
    finally:
        bridge.close()


@pytest.mark.parametrize(
    "sql,p,s",
    [
        ("numeric(2,-3)", 2, -3),
        ("numeric(3,5)", 3, 5),
        ("numeric(1,0)", 1, 0),
        ("numeric(38,38)", 38, 38),
    ],
)
def test_decimal_catalog_boundaries(sql, p, s):
    from shape_databases.sources import catalog_field

    assert catalog_field("amount", sql, False).type == pa.decimal128(p, s)


@pytest.mark.parametrize("sql", ["numeric(0,0)", "numeric(39,0)"])
def test_decimal_catalog_unrepresentable_precision_is_explicit(sql):
    from shape_databases.sources import catalog_field

    with pytest.raises(ShapeError, match="amount.*decimal128"):
        catalog_field("amount", sql, False)


def test_source_passes_plugin_kit(source):
    from shape.plugins import kit

    src, uri, _ = source
    kit.check_source(src, uri + "?table=accounts")


def test_service_helper_keeps_tls_and_schema_query_options(source, monkeypatch):
    import runpy
    from pathlib import Path

    src, uri, server = source
    monkeypatch.setattr(src.sink_class, "default_connect", lambda self, **kw: server.connect(**kw))
    helper = runpy.run_path(str(Path(__file__).with_name("test_sources_services.py")))["roundtrip"]
    extra = "&sslmode=disable" if server.dialect == "postgres" else "&ssl=false"
    helper(src, uri + "?schema=chosen" + extra)
    assert list(server.tables) == [(server.default_schema, "accounts")]


def test_catalog_does_not_infer_undeclared_primary_key(source):
    src, uri, server = source
    server.seed("no_key", [("id", "integer", False)], [(1,), (2,)])
    prof = src.profile_database(uri, tables=["no_key"])
    assert prof.tables["no_key"]["primary_key"] == []
    assert not prof.tables["no_key"]["columns"]["id"]["is_primary_key"]


@pytest.mark.parametrize("key", ["%70assword", "%74oken", "%63redential", "private_key"])
def test_uri_query_credentials_are_redacted_with_encoded_keys(key):
    from shape.security.redact import redact_text

    text = f"postgresql://shape@localhost/db?{key}=private-secret&sslmode=require"
    redacted = redact_text(text)
    assert "private-secret" not in redacted
    assert "sslmode=require" in redacted


@pytest.mark.parametrize("whitespace", [" ", "\n", "\t"])
@pytest.mark.parametrize("username", ["shape", "", "shape user", "shape\tuser"])
def test_async_userinfo_password_with_whitespace_never_reaches_job(
    source, tmp_path, whitespace, username
):
    import time

    from shape.bridge.core import Bridge

    src, _, server = source
    secret = "fixture" + whitespace + "secret"
    uri = src.schemes[0] + "://" + username + ":" + secret + "@localhost/db?table=accounts"
    bridge = Bridge(tmp_path / "jobs")
    try:
        response = bridge.handle(
            {
                "api_version": "1.0",
                "command": "profile",
                "args": {"source": uri},
                "options": {"async": True},
            }
        )
        assert response["ok"]
        job_id = response["result"]["job_id"]
        deadline = time.monotonic() + 5
        while True:
            status = bridge.handle(
                {"api_version": "1.0", "command": "job_status", "args": {"job_id": job_id}}
            )
            if status["result"]["status"] != "running":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status["result"]["status"] == "failed"
        records = list((tmp_path / "jobs").rglob("*.json"))
        assert records
        for record in records:
            doc = json.loads(record.read_text())
            assert secret not in doc["request"]["args"]["source"]
            assert "fixture" not in doc["request"]["args"]["source"]
            assert "secret" not in doc["request"]["args"]["source"]
        assert not server.events
    finally:
        bridge.close()


def test_malformed_uri_query_redaction_is_bounded():
    import subprocess
    import sys

    payload = "postgresql://shape@localhost/db" + "?" * 100000
    code = (
        "import sys; from shape.security.redact import redact_text; "
        "text=sys.stdin.read(); assert redact_text(text)==text"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        input=payload,
        text=True,
        capture_output=True,
        timeout=3,
        check=True,
    )
