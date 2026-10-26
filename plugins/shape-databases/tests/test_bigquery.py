"""W9-13 SHAPE-BIGQUERY-001 offline conformance: actual Parquet and Storage Read Arrow transport."""

import json
from pathlib import Path

import pyarrow as pa
import pytest
from shape_databases.errors import WriteError
from shape_databases.sources import DatabaseSource

from shape.errors import ShapeError


@pytest.fixture(autouse=True)
def bigquery_api():
    global BigQuerySink, BigQuerySource, arrow_field, column_type, FakeBigQuery
    from shape_databases.bigquery import BigQuerySink, BigQuerySource, arrow_field, column_type
    from shape_databases.testing import FakeBigQuery


URI = "bigquery://test-project/dataset?location=US"


def batch(start=0, n=5):
    return pa.record_batch({"id": list(range(start, start + n)), "label": ["row"] * n})


def test_parquet_chunks_private_cleanup_and_declared_key():
    server = FakeBigQuery()
    sink = BigQuerySink(connect=server.connect)
    assert sink.write(URI, "items", [batch()], chunk_rows=2, primary_key=["id"]) == 5
    assert [j["rows"] for j in server.loads] == [2, 2, 1]
    assert all(j["format"] == "PARQUET" and j["private"] for j in server.loads), [
        j["privacy_diagnostics"] for j in server.loads
    ]
    assert all(not Path(j["path"]).exists() for j in server.loads)
    assert any("PRIMARY KEY (`id`) NOT ENFORCED" in q for q, _ in server.queries)
    assert sink.plan(URI, "x", {}).options["chunk_rows"] == 1_000_000


@pytest.mark.parametrize("mode,expected", [("append", 8), ("truncate", 3), ("replace", 3)])
def test_modes(mode, expected):
    server = FakeBigQuery()
    sink = BigQuerySink(connect=server.connect)
    sink.write(URI, "items", [batch()])
    sink.write(URI, "items", [batch(5, 3)], write_mode=mode)
    assert server.tables["test-project.dataset.items"].num_rows == expected
    assert server.loads[-1]["disposition"] == (
        "WRITE_TRUNCATE" if mode == "truncate" else "WRITE_APPEND"
    )
    with pytest.raises(ShapeError, match="already exists"):
        sink.write(URI, "items", [batch()], write_mode="fail")


def test_mismatch_drops_created_and_stream_failure_keeps_committed():
    server = FakeBigQuery(output_delta=1)
    sink = BigQuerySink(connect=server.connect)
    with pytest.raises(WriteError, match="output_rows") as err:
        sink.write(URI, "items", [batch()], chunk_rows=2)
    assert err.value.rows_committed == 0
    assert "test-project.dataset.items" not in server.tables
    assert not Path(server.loads[0]["path"]).exists()
    server = FakeBigQuery(fail_load=2)
    sink = BigQuerySink(connect=server.connect)
    with pytest.raises(WriteError) as err:
        sink.write(URI, "items", [batch()], commit_rows=2)
    assert err.value.rows_committed == 2
    assert server.tables["test-project.dataset.items"].num_rows == 2
    assert [j["rows"] for j in server.loads] == [2, 2]
    assert all(not Path(j["path"]).exists() for j in server.loads)


@pytest.mark.parametrize("suffix", ["?token=NEVER", "?credentials=NEVER", "?private_key=NEVER"])
def test_uri_credentials_refused_before_connection(suffix):
    server = FakeBigQuery()
    with pytest.raises(ShapeError) as err:
        BigQuerySink(connect=server.connect).write(
            "bigquery://test-project/dataset" + suffix, "t", [batch()]
        )
    assert "NEVER" not in str(err.value)
    assert server.connections == 0


def test_reference_only_and_scrub(monkeypatch):
    server = FakeBigQuery(fail_load=1, failure="KEY-MATERIAL")
    sink = BigQuerySink(connect=server.connect)
    with pytest.raises(ShapeError, match="reference"):
        sink.write(URI, "x", [batch()], credentials='{"private_key":"KEY-MATERIAL"}')
    assert server.connections == 0
    monkeypatch.setenv(
        "BQ_TEST_CREDENTIAL", json.dumps({"private_key": "KEY-MATERIAL", "type": "service_account"})
    )
    with pytest.raises(WriteError) as err:
        sink.write(URI, "x", [batch()], credentials="env://BQ_TEST_CREDENTIAL")
    assert "KEY-MATERIAL" not in str(err.value)
    assert err.value.__context__ is None


@pytest.mark.parametrize(
    "kind,expected",
    [
        (pa.int8(), "INT64"),
        (pa.int64(), "INT64"),
        (pa.uint32(), "INT64"),
        (pa.uint64(), "NUMERIC(20,0)"),
        (pa.float32(), "FLOAT64"),
        (pa.float64(), "FLOAT64"),
        (pa.bool_(), "BOOL"),
        (pa.binary(), "BYTES"),
        (pa.date32(), "DATE"),
        (pa.time64("us"), "TIME"),
        (pa.timestamp("us"), "DATETIME"),
        (pa.timestamp("us", "Europe/London"), "TIMESTAMP"),
        (pa.decimal128(38, 9), "NUMERIC(38,9)"),
        (pa.decimal128(38, 0), "BIGNUMERIC(38,0)"),
        (pa.decimal128(38, 38), "BIGNUMERIC(38,38)"),
        (pa.list_(pa.int32()), "STRING"),
    ],
)
def test_type_boundaries(kind, expected):
    assert column_type(pa.field("value", kind), {}) == expected


@pytest.mark.parametrize(
    "kind",
    [
        pa.decimal128(10, -1),
        pa.decimal128(2, 3),
        pa.decimal256(76, 37),
        pa.timestamp("ns"),
        pa.time64("ns"),
    ],
)
def test_invalid_type_fails_before_connection(kind):
    server = FakeBigQuery()
    with pytest.raises(ShapeError, match="bad_column"):
        BigQuerySink(connect=server.connect).write(
            URI, "x", [], schema=pa.schema([("bad_column", kind)])
        )
    assert server.connections == 0


def test_length_uuid_nested_and_ns_conversion():
    assert column_type(pa.field("s", pa.string()), {"max_length": 8}) == "STRING(8)"
    assert (
        column_type(pa.field("u", pa.binary(16), metadata={b"shape.type": b"uuid"}), {}) == "STRING"
    )
    server = FakeBigQuery()
    b = pa.record_batch(
        {
            "nested": [[1, 2], None],
            "stamp": pa.array([1001, None], type=pa.timestamp("ns")),
            "u": pa.array([2**64 - 1, None], type=pa.uint64()),
        }
    )
    BigQuerySink(connect=server.connect).write(URI, "x", [b], truncate_ns=True)
    result = server.tables["test-project.dataset.x"]
    assert result["nested"].to_pylist() == ["[1,2]", None]
    assert result["stamp"].type == pa.timestamp("us")
    assert str(result["u"][0].as_py()) == str(2**64 - 1)


@pytest.mark.parametrize(
    "option",
    [
        {"chunk_rows": 0},
        {"commit_rows": -1},
        {"max_bytes_billed": 0},
        {"columns": {"label": {"max_length": 0}}},
    ],
)
def test_bad_options_before_connection(option):
    server = FakeBigQuery()
    with pytest.raises(ShapeError):
        BigQuerySink(connect=server.connect).write(URI, "x", [batch()], **option)
    assert server.connections == 0


def test_source_shared_model_catalog_storage_sampling_cost_and_compatibility(tmp_path):
    server = FakeBigQuery()
    sink = BigQuerySink(connect=server.connect)
    sink.write(URI, "items", [batch(0, 10)], primary_key=["id"])
    source = BigQuerySource(connect=server.connect)
    assert isinstance(source, DatabaseSource)
    got = pa.Table.from_batches(source.read(URI + "&table=items"))
    assert got.num_rows == 10 and server.storage_reads
    prof = source.profile_database(URI, sample_rows=3, tables=["items"])
    item = prof.tables["items"]
    assert item["row_count"] == 10
    assert item["sampled_rows"] == 3
    assert item["sample_method"] == "tablesample_system"
    assert item["sampling"]["population_rows"] == 10
    assert item["primary_key"] == ["id"]
    assert item["primary_key_evidence"] == "declared"
    assert item["columns"]["id"]["declared_type"] == "INT64"
    assert prof.provenance["bytes_processed"] > 0
    assert any("TABLESAMPLE SYSTEM (30" in q for q, cfg in server.queries)
    assert all(cfg.maximum_bytes_billed == 2**30 for q, cfg in server.queries)
    actual = [(q, c) for q, c in server.queries if not c.dry_run]
    assert all(any(p == q and c.dry_run for p, c in server.queries) for q, cfg in actual)
    from shape import load, save

    path = tmp_path / "bq.shape"
    save(prof, path)
    assert load(path).tables["items"]["sampling"] == item["sampling"]


def test_dry_run_refuses_before_execution_and_catalog_only():
    server = FakeBigQuery(estimated_bytes=101)
    with pytest.raises(ShapeError, match="items.*101.*max_bytes_billed"):
        BigQuerySink(connect=server.connect).write(URI, "items", [batch()], max_bytes_billed=100)
    assert all(c.dry_run for q, c in server.queries)
    server = FakeBigQuery()
    BigQuerySink(connect=server.connect).write(URI, "items", [batch()])
    server.storage_reads.clear()
    source = BigQuerySource(connect=server.connect)
    prof = source.profile_database(URI, sample_rows=0)
    assert prof.tables["items"]["sampled_rows"] == 0
    assert not server.storage_reads
    with pytest.raises(ShapeError, match="missing tables"):
        source.profile_database(URI, tables=["missing"])


def test_reverse_types_and_entrypoints():
    assert arrow_field("x", "INT64", True).type == pa.int64()
    assert arrow_field("x", "TIMESTAMP", True).type == pa.timestamp("us", "UTC")
    assert arrow_field("x", "NUMERIC(20,0)", True).type == pa.decimal128(20, 0)
    assert arrow_field("x", "BIGNUMERIC(76,38)", True).type == pa.decimal256(76, 38)
    import tomllib

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    assert "bigquery" in project["entry-points"]["shape.sources"]
    assert "bigquery" in project["entry-points"]["shape.sinks"]
    assert len(project["optional-dependencies"]["bigquery"]) == 2


def test_decimal_upper_bound_valid_and_invalid_neighbors():
    assert column_type(pa.field("edge", pa.decimal256(76, 38)), {}) == "BIGNUMERIC(76,38)"
    for typ in [pa.decimal256(76, 37), pa.decimal256(76, 39)]:
        with pytest.raises(ShapeError, match="edge"):
            column_type(pa.field("edge", typ), {})


@pytest.mark.parametrize(
    "options",
    [
        {"sample_rows": -1},
        {"sample_rows": True},
        {"tables": []},
        {"tables": ["bad.name"]},
        {"surprise": 1},
        {"max_bytes_billed": False},
    ],
)
def test_source_invalid_options_before_connection(options):
    server = FakeBigQuery()
    with pytest.raises(ShapeError):
        BigQuerySource(connect=server.connect).profile_database(URI, **options)
    assert server.connections == 0


def test_source_foreign_keys_custom_cost_and_refusal():
    server = FakeBigQuery()
    sink = BigQuerySink(connect=server.connect)
    sink.write(URI, "parent", [batch()], primary_key=["id"])
    sink.write(URI, "child", [batch()])
    server.foreign_keys["child"] = [("fk_parent", "parent", "id", "id")]
    source = BigQuerySource(connect=server.connect)
    prof = source.profile_database(URI, sample_rows=0, max_bytes_billed=100)
    rel = prof.to_dict()["relationships"][0]
    assert rel["evidence"] == "declared" and rel["parent"] == "parent"
    assert prof.tables["child"]["columns"]["id"]["fk_evidence"] == "declared"
    server.queries.clear()
    with pytest.raises(ShapeError, match="dataset.*64.*max_bytes_billed"):
        source.profile_database(URI, max_bytes_billed=63)
    assert len(server.queries) == 1 and server.queries[0][1].dry_run


def test_adc_and_file_reference(monkeypatch, tmp_path):
    path = tmp_path / "account.json"
    path.write_text(json.dumps({"type": "service_account", "private_key": "KEY-MATERIAL"}))
    path.chmod(0o600)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    server = FakeBigQuery()
    assert BigQuerySink(connect=server.connect).write(URI, "items", [batch()]) == 5
    assert "KEY-MATERIAL" not in repr(BigQuerySink().plan(URI, "items", {}).auth)


def test_missing_sdk_lazy_install_message(monkeypatch):
    import importlib

    sink = BigQuerySink()
    assert sink.plan(URI, "items", {}).table == "items"
    original = importlib.import_module

    def missing(name, *args, **kwargs):
        if name.startswith("google."):
            raise ImportError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(ShapeError, match=r"pip install .*\[bigquery\]"):
        sink.write(URI, "items", [batch()])


def test_profile_cli_bridge_and_generate_target(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import shape
    from shape.bridge.core import Bridge
    from shape.cli.main import main
    from shape.io.targets import sink_for_target
    from shape.plugins.api.v1 import Sink, Source
    from shape.plugins.host import default_host

    server = FakeBigQuery()
    sink = BigQuerySink(connect=server.connect)
    source = BigQuerySource(connect=server.connect)
    assert isinstance(source, Source) and isinstance(sink, Sink)
    sink.write(URI, "items", [batch()])
    host = default_host()
    names = host.names
    monkeypatch.setattr(host, "names", lambda group: [*names(group), "bigquery"])
    records = host.records
    get = host.get
    try_get = host.try_get
    monkeypatch.setattr(
        host,
        "records",
        lambda group: [*records(group), SimpleNamespace(group=group, name="bigquery")],
    )
    monkeypatch.setattr(
        host,
        "try_get",
        lambda group, name: (
            (source if group == "shape.sources" else sink)
            if name == "bigquery"
            else try_get(group, name)
        ),
    )
    monkeypatch.setattr(
        host,
        "get",
        lambda group, name: (
            (source if group == "shape.sources" else sink)
            if name == "bigquery"
            else get(group, name)
        ),
    )
    assert sink_for_target(URI)[0] == "bigquery"
    schema = {
        "schema_version": 1,
        "model": {"name": "bq_cli", "seed": 7},
        "tables": {
            "generated": {
                "name": "generated",
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}
                },
            }
        },
        "generation": {"scale": "small", "scales": {"small": {"generated": 3}}},
    }
    spec = tmp_path / "generation.json"
    spec.write_text(json.dumps(schema))
    assert main(["generate", str(spec), "--to", URI, "--table-prefix", "prefix_", "--yes"]) == 0
    assert server.tables["test-project.dataset.prefix_generated"].num_rows == 3
    target = tmp_path / "cli.shape"
    assert main(["profile", URI, "--tables", "items", "--sample-rows", "2", "-o", str(target)]) == 0
    assert shape.load(target).tables["items"]["sampled_rows"] == 2
    bridge = Bridge(tmp_path / "jobs")
    try:
        out = tmp_path / "bridge.shape"
        response = bridge.handle(
            {
                "api_version": "1.2",
                "command": "profile",
                "args": {"source": URI, "tables": ["items"], "sample_rows": 2, "output": str(out)},
            }
        )
        assert response["ok"], response
        assert shape.load(out).capture["mode"] == "safe"
    finally:
        bridge.close()


def test_cli_reference_selectors_and_uri_cost_guard(monkeypatch):
    from shape.cli.source_credentials import extract
    from shape.cli.to import sink_config

    assert extract(["--source-config", "credentials=env://BQ_TEST"])[1] == {
        "credentials": "env://BQ_TEST"
    }
    assert (
        sink_config(["bigquery.credentials=env://BQ_TEST"])["bigquery"]["credentials"]
        == "env://BQ_TEST"
    )
    for bad in ["inline-key", "kv://vault/key"]:
        with pytest.raises(ValueError):
            extract(["--source-config", "credentials=" + bad])
        with pytest.raises(ValueError):
            sink_config(["bigquery.credentials=" + bad])
    server = FakeBigQuery(estimated_bytes=65)
    with pytest.raises(ShapeError, match="max_bytes_billed=64"):
        BigQuerySink(connect=server.connect).write(URI + "&max_bytes_billed=64", "items", [batch()])


def test_docs_required_surfaces_and_security():
    root = Path(__file__).resolve().parents[3]
    for rel in [
        "plugins/shape-databases/README.md",
        "docs/SINKS.md",
        "docs/CLI.md",
        "docs/BRIDGE.md",
        "CHANGELOG.md",
    ]:
        assert "BigQuery" in (root / rel).read_text()
    readme = (root / "plugins/shape-databases/README.md").read_text()
    for term in [
        "BIGNUMERIC",
        "NUMERIC(20,0)",
        "TABLESAMPLE SYSTEM",
        "max_bytes_billed",
        "credentials",
        "Storage Read",
    ]:
        assert term in readme


@pytest.mark.parametrize("depth", [-1, 65, True, "1"])
def test_source_depth_validation_before_connection(depth):
    server = FakeBigQuery()
    with pytest.raises((ShapeError, ValueError), match="max_depth"):
        BigQuerySource(connect=server.connect).profile_database(URI, max_depth=depth)
    assert server.connections == 0


def test_live_round_trip_fixture_offline(monkeypatch):
    """Exercise the account test's body using the same fake, without a live claim."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bigquery_live_fixture", Path(__file__).with_name("test_live.py")
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    server = FakeBigQuery()
    monkeypatch.setattr(fixture, "need", lambda *names: [URI])
    monkeypatch.setattr(
        BigQuerySink, "default_connect", lambda self, **params: server.connect(**params)
    )
    fixture.test_bigquery_round_trip()
    assert not server.tables


def test_empty_truncate_and_stream_input_error_accounting():
    server = FakeBigQuery()
    sink = BigQuerySink(connect=server.connect)
    sink.write(URI, "items", [batch()])
    sink.write(URI, "items", [], schema=batch().schema, write_mode="truncate")
    assert server.tables["test-project.dataset.items"].num_rows == 0
    assert server.loads[-1]["rows"] == 0
    assert server.loads[-1]["disposition"] == "WRITE_TRUNCATE"

    def broken():
        yield batch(0, 2)
        raise ShapeError("input stopped")

    with pytest.raises(WriteError) as err:
        sink.write(URI, "items", broken(), commit_rows=2, write_mode="append")
    assert err.value.rows_committed == 2


@pytest.mark.parametrize("key", ["credentials", "%63redentials", "private_key", "token"])
@pytest.mark.parametrize("separator", ["&", "#"])
def test_remote_confirmation_never_echoes_uri_key(key, separator):
    import io

    from shape.io.targets import confirm_remote_targets
    from shape.plugins.schemes import redact

    uri = URI + separator + key + "=KEY-MATERIAL"
    assert "KEY-MATERIAL" not in redact(uri)
    with pytest.raises(ShapeError) as error:
        confirm_remote_targets([uri], stdin=io.StringIO("n\n"), stderr=io.StringIO())
    assert "KEY-MATERIAL" not in str(error.value)


@pytest.mark.parametrize("fragment", ["token=KEY-MATERIAL", "private_key=KEY-MATERIAL"])
@pytest.mark.parametrize("source", [False, True])
def test_uri_fragment_refused_before_connection(fragment, source):
    server = FakeBigQuery()
    uri = URI + "#" + fragment
    with pytest.raises(ShapeError, match="fragment") as error:
        if source:
            BigQuerySource(connect=server.connect).profile_database(uri)
        else:
            BigQuerySink(connect=server.connect).write(uri, "items", [batch()])
    assert "KEY-MATERIAL" not in str(error.value)
    assert server.connections == 0


def test_first_mismatch_with_commit_rows_drops_unconfirmed_created_table():
    server = FakeBigQuery(output_delta=1)
    with pytest.raises(WriteError) as error:
        BigQuerySink(connect=server.connect).write(URI, "items", [batch()], commit_rows=2)
    assert error.value.rows_committed == 0
    assert "test-project.dataset.items" not in server.tables


def test_parquet_bignumeric_decimal_targets_and_values():
    from decimal import Decimal

    values = {
        "fraction": pa.array([Decimal("1.0000000001")], type=pa.decimal128(38, 10)),
        "integer": pa.array([Decimal("9" * 38)], type=pa.decimal128(38, 0)),
        "wide": pa.array([Decimal("9" * 38 + "." + "9" * 38)], type=pa.decimal256(76, 38)),
    }
    original = pa.record_batch(values)
    server = FakeBigQuery()
    assert BigQuerySink(connect=server.connect).write(URI, "decimals", [original]) == 1
    assert server.loads[0].get("decimal_target_types") == ["NUMERIC", "BIGNUMERIC"]
    assert server.tables["test-project.dataset.decimals"].to_pydict() == original.to_pydict()
    assert all(
        t.startswith("BIGNUMERIC") for _, t, _ in server.declared["test-project.dataset.decimals"]
    )


def test_explicit_sdk_catalog_constraints_with_bare_columns_and_cached_metadata():
    from decimal import Decimal
    from types import SimpleNamespace

    server = FakeBigQuery()
    b = pa.record_batch(
        {
            "money": pa.array([Decimal(1)], type=pa.decimal128(20, 0)),
            "wide": pa.array([Decimal("1.0000000001")], type=pa.decimal128(38, 10)),
            "label": ["label"],
            "payload": [b"data"],
        }
    )
    BigQuerySink(connect=server.connect).write(URI, "typed", [b])
    table_id = "test-project.dataset.typed"
    server.declared[table_id] = [
        ("money", "NUMERIC", ""),
        ("wide", "BIGNUMERIC", ""),
        ("label", "STRING", ""),
        ("payload", "BYTES", ""),
    ]
    server.catalog_schemas[table_id] = [
        SimpleNamespace(
            name="payload", field_type="BYTES", precision=None, scale=None, max_length=8
        ),
        SimpleNamespace(
            name="label", field_type="STRING", precision=None, scale=None, max_length=12
        ),
        SimpleNamespace(
            name="wide", field_type="BIGNUMERIC", precision=38, scale=10, max_length=None
        ),
        SimpleNamespace(
            name="money", field_type="NUMERIC", precision=20, scale=None, max_length=None
        ),
    ]
    source = BigQuerySource(connect=server.connect)
    schema = source.schema(URI + "&table=typed")
    assert schema.names == ["money", "wide", "label", "payload"]
    assert schema.field("money").type == pa.decimal128(20, 0)
    assert schema.field("wide").type == pa.decimal256(38, 10)
    assert schema.field("label").metadata[b"shape.sql_type"] == b"STRING(12)"
    assert schema.field("payload").metadata[b"shape.sql_type"] == b"BYTES(8)"
    server.get_table_calls.clear()
    prof = source.profile_database(URI, sample_rows=0)
    assert prof.tables["typed"]["columns"]["money"]["declared_type"] == "NUMERIC(20,0)"
    assert server.get_table_calls == [table_id]


@pytest.mark.parametrize(
    "declared,metadata",
    [
        ("NUMERIC", {"precision": 0}),
        ("NUMERIC", {"precision": 20, "scale": -1}),
        ("NUMERIC", {"precision": 2, "scale": 3}),
        ("NUMERIC", {"scale": 2}),
        ("NUMERIC", {"precision": True}),
        ("NUMERIC", {"precision": 20, "scale": 10}),
        ("STRING", {"max_length": 0}),
        ("STRING", {"max_length": True}),
        ("STRING", {"precision": 20}),
        ("NUMERIC(20,0)", {"precision": 21, "scale": 0}),
        ("STRING(12)", {"max_length": 13}),
        ("NUMERIC", {"field_type": "STRING"}),
    ],
)
def test_catalog_invalid_or_conflicting_explicit_metadata_fails_closed(declared, metadata):
    from types import SimpleNamespace

    server = FakeBigQuery()
    BigQuerySink(connect=server.connect).write(URI, "typed", [pa.record_batch({"value": [1]})])
    table_id = "test-project.dataset.typed"
    server.declared[table_id] = [("value", declared, "")]
    data = {
        "name": "value",
        "field_type": declared.split("(")[0],
        "precision": None,
        "scale": None,
        "max_length": None,
        **metadata,
    }
    server.catalog_schemas[table_id] = [SimpleNamespace(**data)]
    with pytest.raises(ShapeError, match="value"):
        BigQuerySource(connect=server.connect).schema(URI + "&table=typed")


@pytest.mark.parametrize(
    "declared,metadata,expected",
    [
        ("NUMERIC(20,0)", {"precision": 20}, pa.decimal128(20, 0)),
        ("BIGNUMERIC(38,10)", {"precision": 38, "scale": 10}, pa.decimal256(38, 10)),
        ("STRING(12)", {"max_length": 12}, pa.string()),
        ("NUMERIC", {}, pa.decimal128(38, 9)),
    ],
)
def test_catalog_matching_or_unspecified_metadata_preserves_mapping(declared, metadata, expected):
    from types import SimpleNamespace

    server = FakeBigQuery()
    BigQuerySink(connect=server.connect).write(URI, "typed", [pa.record_batch({"value": [1]})])
    table_id = "test-project.dataset.typed"
    server.declared[table_id] = [("value", declared, "")]
    data = {
        "name": "value",
        "field_type": declared.split("(")[0],
        "precision": None,
        "scale": None,
        "max_length": None,
        **metadata,
    }
    server.catalog_schemas[table_id] = [SimpleNamespace(**data)]
    field = BigQuerySource(connect=server.connect).schema(URI + "&table=typed").field("value")
    assert field.type == expected
    assert field.nullable


@pytest.mark.parametrize(
    "options,expected_depth",
    [
        pytest.param({}, 8, id="default"),
        pytest.param({"max_depth": 0}, 0, id="zero"),
        pytest.param({"max_depth": 1}, 1, id="one"),
        pytest.param({"max_depth": 64}, 64, id="upper-bound"),
    ],
)
def test_public_profile_forwards_depth_through_bigquery_catalog(
    monkeypatch, options, expected_depth
):
    from types import SimpleNamespace

    import shape
    import shape.api  # Initialize the lazy public wrapper before observing the sample profiler.
    import shape.profile.reference as reference
    from shape.plugins.host import default_host

    server = FakeBigQuery()
    BigQuerySink(connect=server.connect).write(URI, "items", [batch(0, 10)])
    source = BigQuerySource(connect=server.connect)
    host = default_host()
    records, try_get = host.records, host.try_get
    monkeypatch.setattr(
        host,
        "records",
        lambda group: (
            [SimpleNamespace(group=group, name="bigquery")]
            if group == "shape.sources"
            else records(group)
        ),
    )
    monkeypatch.setattr(
        host,
        "try_get",
        lambda group, name: (
            source if (group, name) == ("shape.sources", "bigquery") else try_get(group, name)
        ),
    )
    catalog_depths, sample_depths = [], []
    catalog_profile, sample_profile = source.profile_database, reference.profile

    def record_catalog(uri, **kwargs):
        catalog_depths.append(kwargs["max_depth"])
        return catalog_profile(uri, **kwargs)

    def record_sample(sampled, **kwargs):
        if isinstance(sampled, pa.Table):
            sample_depths.append(kwargs["max_depth"])
            assert sampled.num_rows == 3
        return sample_profile(sampled, **kwargs)

    monkeypatch.setattr(source, "profile_database", record_catalog)
    monkeypatch.setattr(reference, "profile", record_sample)
    prof = shape.profile(URI, tables=["items"], sample_rows=3, **options)
    assert catalog_depths == sample_depths == [expected_depth]
    assert server.storage_reads == [3]
    table = prof.tables["items"]
    assert table["row_count"] == 10
    assert table["sampled_rows"] == 3
    assert table["sample_method"] == "tablesample_system"
    assert table["sampling"]["population_rows"] == 10
    assert table["sampling"]["sampled_rows"] == table["sampling"]["requested"] == 3
    assert table["columns"]["id"]["declared_type"] == "INT64"
    assert prof.provenance["source"] == "bigquery"
    assert prof.provenance["bytes_processed"] > 0


def test_staging_privacy_is_applied_before_parquet_and_load(monkeypatch):
    from shape.scale import jobs

    protected = []
    restrict = jobs.restrict_to_current_user

    def protect(path, *, remove_bootstrap=False):
        assert remove_bootstrap is True
        assert path.exists()
        if path.is_file():
            assert path.stat().st_size == 0
        restrict(path, remove_bootstrap=remove_bootstrap)
        protected.append(path)

    monkeypatch.setattr(jobs, "restrict_to_current_user", protect)
    server = FakeBigQuery()

    def connect(**options):
        client = server.connect(**options)
        load = client.load_table_from_file

        def checked_load(stream, table, **kwargs):
            path = Path(stream.name)
            assert path.parent in protected and path in protected
            return load(stream, table, **kwargs)

        client.load_table_from_file = checked_load
        return client

    assert BigQuerySink(connect=connect).write(URI, "items", [batch()], chunk_rows=2) == 5
    assert len(protected) == 4
    assert all(not path.exists() for path in protected)


@pytest.mark.parametrize("blocked", ["directory", "file"])
def test_staging_acl_failure_refuses_load_and_cleans_created_table(monkeypatch, blocked):
    from shape.scale import jobs

    protected = []

    def refuse(path, *, remove_bootstrap=False):
        assert remove_bootstrap is True
        protected.append(path)
        if path.is_dir() == (blocked == "directory"):
            if path.is_file():
                assert path.stat().st_size == 0
            raise OSError("staging ACL unavailable")

    monkeypatch.setattr(jobs, "restrict_to_current_user", refuse)
    server = FakeBigQuery()
    with pytest.raises(WriteError, match="staging ACL unavailable") as error:
        BigQuerySink(connect=server.connect).write(URI, "items", [batch()])
    assert error.value.rows_committed == 0
    assert not server.loads
    assert "test-project.dataset.items" not in server.tables
    assert protected and all(not path.exists() for path in protected)


@pytest.mark.parametrize("fail_target", [None, "directory", "file"])
@pytest.mark.parametrize("principal", ["current-user", "*S-1-5-18"])
def test_windows_staging_removes_explicit_python_acl_grants(monkeypatch, fail_target, principal):
    import subprocess
    from types import SimpleNamespace

    from shape.scale import jobs

    python_grants = {"*S-1-5-18", "*S-1-5-32-544", "*S-1-3-4"}
    grants, commands, paths = {}, [], []

    def update_acl(command, **kwargs):
        path = Path(command[1])
        paths.append(path)
        assert command[:1] == ["icacls"]
        assert kwargs["capture_output"] and kwargs["timeout"] == 30
        if command[2] == "/remove:g":
            assert set(command[3:]) == python_grants and kwargs["check"]
            if (fail_target == "directory" and path.is_dir()) or (
                fail_target == "file" and path.is_file()
            ):
                raise OSError("explicit staging ACL unavailable")
            commands.append(command)
            grants.setdefault(path, set(python_grants)).difference_update(python_grants)
        else:
            assert command[2:4] == ["/inheritance:r", "/grant:r"]
            assert command[4] == principal + (":(OI)(CI)F" if path.is_dir() else ":F")
            assert kwargs["check"] is False
            grants.setdefault(path, set(python_grants)).add(principal)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(jobs, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(jobs, "windows_current_user", lambda: principal)
    monkeypatch.setattr(
        jobs,
        "subprocess",
        SimpleNamespace(run=update_acl, SubprocessError=subprocess.SubprocessError),
    )
    server = FakeBigQuery()

    def connect(**options):
        client = server.connect(**options)
        load = client.load_table_from_file

        def checked_load(stream, table, **kwargs):
            path = Path(stream.name)
            assert grants[path] == grants[path.parent] == {principal}
            return load(stream, table, **kwargs)

        client.load_table_from_file = checked_load
        return client

    if fail_target is None:
        assert BigQuerySink(connect=connect).write(URI, "items", [batch()]) == 5
        assert len(commands) == 2
    else:
        with pytest.raises(WriteError, match="explicit staging ACL unavailable") as error:
            BigQuerySink(connect=connect).write(URI, "items", [batch()])
        assert error.value.rows_committed == 0
        assert not server.loads and "test-project.dataset.items" not in server.tables
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize("explicit,expected", [(None, 64), (32, 32), (128, 128)])
def test_sink_plan_retains_uri_cap_and_explicit_precedence_without_caller_alias(explicit, expected):
    options = {} if explicit is None else {"max_bytes_billed": explicit}
    original = dict(options)
    plan = BigQuerySink().plan(URI + "&max_bytes_billed=64", "items", options)
    assert options == original
    assert plan.options["max_bytes_billed"] == expected
    assert plan.options["nested"] == "native"
    options["max_bytes_billed"] = 1
    assert plan.options["max_bytes_billed"] == expected


@pytest.mark.parametrize("estimate", [63, 64, 65])
def test_uri_cap_exact_boundary_guards_before_paid_queries_and_loads(estimate):
    server = FakeBigQuery(estimated_bytes=estimate)
    sink = BigQuerySink(connect=server.connect)
    if estimate > 64:
        with pytest.raises(ShapeError, match="estimate 65 bytes exceeds max_bytes_billed=64"):
            sink.write(URI + "&max_bytes_billed=64", "items", [batch()])
        assert server.queries and all(config.dry_run for _, config in server.queries)
        assert not server.loads and not server.tables
    else:
        assert sink.write(URI + "&max_bytes_billed=64", "items", [batch()]) == 5
        assert any(not config.dry_run for _, config in server.queries)
    assert all(config.maximum_bytes_billed == 64 for _, config in server.queries)
