"""Offline read contracts for the Eventhouse source (W9-11)."""

import json

import pyarrow as pa
import pytest
from shape_fabric.eventhouse_source import EventhouseSource
from shape_fabric.testing import FakeKustoReadTransport

from shape.errors import ShapeError


def source(rows=None):
    fake = FakeKustoReadTransport(
        {
            "T": (
                [("id", "long"), ("payload", "dynamic")],
                rows or [[1, {"a": 2}], [2, None], [3, []]],
            )
        }
    )
    return EventhouseSource(transport=fake), fake


def test_schema_paging_and_json():
    reader, fake = source()
    uri = "eventhouse://localhost/db?table=T&tls=false"
    assert reader.schema(uri).types == [pa.int64(), pa.string()]
    batches = list(reader.read(uri, sample_rows=0, batch_size=2))
    assert [b.num_rows for b in batches] == [2, 1]
    assert json.loads(batches[0].column(1)[0].as_py()) == {"a": 2}
    assert any(
        "serialize" in q and "row_number()" in q and "ingestion_time()" in q for q in fake.queries
    )


@pytest.mark.parametrize(
    "option,value",
    [("sample_rows", -1), ("sample_rows", True), ("batch_size", 0), ("batch_size", 500001)],
)
def test_invalid_limits(option, value):
    reader, _ = source()
    with pytest.raises(ShapeError, match=option):
        list(reader.read("eventhouse://localhost/db?table=T&tls=false", **{option: value}))


def test_sampling_and_catalog_count():
    reader, fake = source()
    tables, metadata = reader.profile_tables(
        "eventhouse://localhost/db?table=T&tls=false", sample_rows=2
    )
    assert tables["T"].num_rows == 2
    assert metadata["T"]["sampled_rows"] == 2
    assert metadata["T"]["catalog_rows"] == 3
    assert metadata["T"]["sample_method"] == "sample"
    assert any("| sample 2" in q for q in fake.queries)


def test_whole_database_dedupe_and_internal_columns():
    fake = FakeKustoReadTransport(
        {
            "T": (
                [("id", "long"), ("_shape_table", "string"), ("_shape_seq", "long")],
                [[1, "T", 0], [1, "T", 0]],
            ),
            "empty": ([("id", "int")], []),
        }
    )
    reader = EventhouseSource(transport=fake)
    tables, _ = reader.profile_tables("eventhouse://localhost/db?tls=false&dedupe=true")
    assert set(tables) == {"T", "empty"}
    assert tables["T"].column_names == ["id"]
    assert tables["T"].num_rows == 1
    assert any("summarize take_any(*) by _shape_table, _shape_seq" in q for q in fake.queries)


@pytest.mark.parametrize(
    "uri",
    [
        "eventhouse://user:SECRET@host/db?table=T",
        "eventhouse://host/db?token=SECRET",
        "eventhouse://host/db?table=",
        "eventhouse://host/",
    ],
)
def test_bad_uri_does_not_echo_credentials(uri):
    reader, _ = source()
    with pytest.raises(ShapeError) as exc:
        reader.schema(uri)
    assert "SECRET" not in str(exc.value)


def test_missing_table_and_unsupported_type():
    reader, _ = source()
    with pytest.raises(ShapeError, match="not found"):
        reader.schema("eventhouse://localhost/db?tls=false&table=missing")
    bad = EventhouseSource(transport=FakeKustoReadTransport({"T": ([("x", "unknown")], [])}))
    with pytest.raises(ShapeError, match="unsupported"):
        bad.schema("eventhouse://localhost/db?tls=false&table=T")


def test_profile_diff_and_bridge_accept_uri(monkeypatch, tmp_path):
    import shape
    from shape.plugins.host import default_host
    from shape.profile.reference.sources import source_options

    reader, _ = source()
    host = default_host()
    original_records = host.records
    original_get = host.try_get
    from types import SimpleNamespace

    monkeypatch.setattr(
        host,
        "records",
        lambda group: (
            [SimpleNamespace(group=group, name="eventhouse")]
            if group == "shape.sources"
            else original_records(group)
        ),
    )
    monkeypatch.setattr(
        host,
        "try_get",
        lambda group, name: (
            reader
            if (group, name) == ("shape.sources", "eventhouse")
            else original_get(group, name)
        ),
    )
    uri = "eventhouse://localhost/db?tls=false"
    with source_options(sample_rows=2):
        profile = shape.profile(uri)
        assert profile.tables["T"]["source_sampling"]["sampled_rows"] == 2
        assert not shape.diff(profile, shape.profile(uri)).drifted
    # The bridge uses the same public profiling API.
    from shape.bridge.handlers.flow import cmd_profile

    ctx = SimpleNamespace(
        jobs_dir=tmp_path,
        include_raw=True,
        warn=lambda *args: None,
        spill=lambda label, value: value,
    )
    assert cmd_profile({"source": uri}, ctx)["tables"]["T"]["rows"] == 3
    from shape.cli.main import main

    assert main(["profile", uri, "-o", str(tmp_path / "cli.shape")]) == 0
    assert main(["diff", str(tmp_path / "cli.shape"), uri]) == 0
    from shape.bridge.handlers.flow import _load_profile

    assert _load_profile(uri, ctx).tables["T"]["row_count"] == 3


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("long", 2**63 - 1, pa.int64()),
        ("int", -(2**31), pa.int32()),
        ("real", 1.5, pa.float64()),
        ("decimal", "12.250000000000000000", pa.decimal256(57, 28)),
        ("datetime", "2026-10-08T00:00:00Z", pa.timestamp("us", tz="UTC")),
        ("timespan", "1.02:03:04.1234567", pa.duration("ns")),
        ("bool", True, pa.bool_()),
        ("string", "", pa.string()),
        ("guid", "00000000-0000-0000-0000-000000000000", pa.string()),
    ],
)
def test_kql_type_map_and_nulls(kind, value, expected):
    reader = EventhouseSource(
        transport=FakeKustoReadTransport({"T": ([("x", kind)], [[value], [None]])})
    )
    batch = next(reader.read("eventhouse://localhost/db?tls=false&table=T"))
    assert batch.schema.types == [expected]
    assert batch.column(0)[1].as_py() is None


@pytest.mark.parametrize(
    "value", ["79228162514264337593543950335", "0.0000000000000000000000000001"]
)
def test_decimal_full_kql_range(value):
    from decimal import Decimal

    reader = EventhouseSource(
        transport=FakeKustoReadTransport({"T": ([("x", "decimal")], [[value]])})
    )
    batch = next(reader.read("eventhouse://localhost/db?tls=false&table=T"))
    assert batch.column(0)[0].as_py() == Decimal(value)


def test_docs_and_entrypoint():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    config = tomllib.loads((root / "plugins/shape-fabric/pyproject.toml").read_text())
    assert (
        config["project"]["entry-points"]["shape.sources"]["eventhouse"]
        == "shape_fabric.eventhouse_source:EventhouseSource"
    )
    for name, phrases in [
        ("docs/plugins/fabric-writers.md", ("sample_rows", "dedupe=true", "decimal256(57,28)")),
        ("docs/FABRIC_PLATFORM.md", ("500,000", "row_number()", "approximate")),
        ("CHANGELOG.md", ("W9-11",)),
    ]:
        text = (root / name).read_text()
        assert all(phrase in text for phrase in phrases)


def test_partial_results_and_secret_error_are_rejected():
    for status, body in [
        (400, b"SECRET"),
        (200, b'{"Tables":[],"Exceptions":["SECRET"]}'),
        (
            200,
            json.dumps(
                {
                    "Tables": [
                        {"Rows": [[1, None]]},
                        {
                            "TableName": "QueryStatus",
                            "Columns": [
                                {"ColumnName": "StatusCode"},
                                {"ColumnName": "StatusDescription"},
                            ],
                            "Rows": [[1, "E_QUERY_RESULT_SET_TOO_LARGE SECRET"]],
                        },
                    ]
                }
            ).encode(),
        ),
    ]:

        def transport(method, url, headers, request, timeout, status=status, body=body):
            if b"QueryStatus" in body and json.loads(request)["csl"].startswith(".show"):
                _, fake = source()
                return fake(method, url, headers, request, timeout)
            return status, {}, body

        reader = EventhouseSource(transport=transport)
        with pytest.raises(ShapeError) as exc:
            list(reader.read("eventhouse://localhost/db?tls=false&table=T", token="SECRET"))
        assert "SECRET" not in str(exc.value)


def test_profile_source_metadata_roundtrip(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import shape
    from shape.plugins.host import default_host

    host = default_host()
    reader, _ = source()
    monkeypatch.setattr(
        host,
        "records",
        lambda group: (
            [SimpleNamespace(group=group, name="eventhouse")] if group == "shape.sources" else []
        ),
    )
    monkeypatch.setattr(host, "try_get", lambda *args: reader)
    prof = shape.profile("eventhouse://localhost/db?tls=false")
    path = tmp_path / "eventhouse.shape"
    shape.save(prof, path)
    loaded = shape.load(path)
    assert loaded.tables["T"]["source_sampling"] == prof.tables["T"]["source_sampling"]


@pytest.mark.parametrize(
    "kind,value",
    [
        ("int", 2**31),
        ("long", 2**63),
        ("datetime", "not-date"),
        ("timespan", "bad"),
        ("decimal", "0." + "0" * 28 + "1"),
    ],
)
def test_invalid_typed_values_fail(kind, value):
    reader = EventhouseSource(transport=FakeKustoReadTransport({"T": ([("x", kind)], [[value]])}))
    with pytest.raises(ShapeError, match="incompatible"):
        list(reader.read("eventhouse://localhost/db?tls=false&table=T"))


@pytest.mark.parametrize("auth", ["token", "env", "entra"])
def test_auth_uses_emitter_signin_without_persisting_token(auth, monkeypatch):
    from types import SimpleNamespace

    reader, fake = source()
    headers_seen = []

    def transport(method, url, headers, body, timeout):
        headers_seen.append(headers)
        return fake(method, url, headers, body, timeout)

    reader = EventhouseSource(transport=transport)
    monkeypatch.delenv("SHAPE_EVENTHOUSE_TOKEN", raising=False)
    options = {}
    if auth == "token":
        options["token"] = "SECRET"
    elif auth == "env":
        monkeypatch.setenv("SHAPE_EVENTHOUSE_TOKEN", "SECRET")
    else:

        class Credential:
            def get_token(self, scope):
                assert scope == "https://example.test/.default"
                return SimpleNamespace(token="SECRET")

        options["credential"] = Credential()
    _, metadata = reader.profile_tables("eventhouse://example.test/db?table=T", **options)
    assert headers_seen and all(h["Authorization"] == "Bearer SECRET" for h in headers_seen)
    assert "SECRET" not in repr(metadata)


def test_entrypoint_loads_through_plugin_host():
    from importlib.metadata import EntryPoint

    from shape.plugins.host import PluginHost

    host = PluginHost(
        entry_points=lambda: [
            EntryPoint(
                name="eventhouse",
                value="shape_fabric.eventhouse_source:EventhouseSource",
                group="shape.sources",
            )
        ]
    )
    assert isinstance(host.get("shape.sources", "eventhouse"), EventhouseSource)


def test_all_kql_types_profile_and_source_contract(monkeypatch):
    from types import SimpleNamespace

    import shape
    from shape.plugins import kit
    from shape.plugins.host import default_host

    values = {
        "long": 2**63 - 1,
        "int": -(2**31),
        "real": 1.25,
        "decimal": "79228162514264337593543950335",
        "datetime": "2026-10-08T00:00:00Z",
        "timespan": "1.02:03:04.1234567",
        "bool": True,
        "string": "",
        "guid": "00000000-0000-0000-0000-000000000000",
        "dynamic": {"x": [1]},
    }
    fields = [(kind, kind) for kind in values]
    fake = FakeKustoReadTransport({"T": (fields, [list(values.values()), [None] * len(fields)])})
    reader = EventhouseSource(transport=fake)
    kit.check_source(reader, "eventhouse://localhost/db?table=T&tls=false")
    host = default_host()
    monkeypatch.setattr(
        host,
        "records",
        lambda group: (
            [SimpleNamespace(group=group, name="eventhouse")] if group == "shape.sources" else []
        ),
    )
    monkeypatch.setattr(host, "try_get", lambda *args: reader)
    profile = shape.profile("eventhouse://localhost/db?table=T&tls=false")
    assert profile.tables["T"]["row_count"] == 2
    assert set(profile.tables["T"]["columns"]) == set(values)


@pytest.mark.parametrize(
    "value,expected",
    [("hello", "hello"), ('"hello"', "hello"), ("{}", {}), ("[]", []), (False, False), (0, 0)],
)
def test_dynamic_scalars_and_empty_containers(value, expected):
    reader = EventhouseSource(
        transport=FakeKustoReadTransport({"T": ([("x", "dynamic")], [[value]])})
    )
    batch = next(reader.read("eventhouse://localhost/db?tls=false&table=T"))
    assert json.loads(batch.column(0)[0].as_py()) == expected


def test_equal_ingestion_time_pages_have_no_gaps_or_repeats():
    fake = FakeKustoReadTransport({"T": ([("id", "long")], [[3], [1], [2], [1]])})

    def transport(method, url, headers, request, timeout):
        query = json.loads(request)["csl"]
        if not query.startswith(".show"):
            fields, rows = fake.tables["T"]
            fake.tables["T"] = (fields, rows[1:] + rows[:1])
        return fake(method, url, headers, request, timeout)

    reader = EventhouseSource(transport=transport)
    batches = list(
        reader.read("eventhouse://localhost/db?table=T&tls=false", sample_rows=0, batch_size=2)
    )
    assert [value for batch in batches for value in batch.column(0).to_pylist()] == [1, 1, 2, 3]
    assert all(
        "__shape_read_order" in query and "pack_array(" in query
        for query in fake.queries
        if not query.startswith(".show")
    )


@pytest.mark.parametrize("column", ["__shape_read_row", "__shape_read_order"])
def test_paging_helper_columns_are_reserved(column):
    fake = FakeKustoReadTransport({"T": ([(column, "long")], [[1]])})
    reader = EventhouseSource(transport=fake)
    with pytest.raises(ShapeError, match=column):
        list(reader.read("eventhouse://localhost/db?table=T&tls=false", sample_rows=0))
    assert all(query.startswith(".show") for query in fake.queries)
