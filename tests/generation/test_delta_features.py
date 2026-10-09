"""W9-07 opt-in Delta features: native local tables, transactions and refusals."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import deltalake
import pyarrow as pa
import pytest

from shape.builtins.sinks import delta
from shape.builtins.sinks.delta import DeltaSink
from shape.cli import doctor_checks
from shape.cli.main import main


def batch(ids=(1, 2)):
    return pa.record_batch({"id": list(ids), "label": ["a"] * len(ids)})


def read(table):
    # DataFusion's deltalake reader supports mapped columns and advertised DV tables.
    return pa.table(
        deltalake.QueryBuilder().register("t", table).execute("SELECT * FROM t").read_all()
    )


def actions(path):
    return [
        json.loads(line)
        for f in sorted((path / "_delta_log").glob("*.json"))
        for line in f.read_text().splitlines()
    ]


def write(tmp_path, **options):
    DeltaSink().write(str(tmp_path), "t", [batch()], **options)
    return deltalake.DeltaTable(str(tmp_path / "t"))


def test_wanted1_properties_create_append_and_fingerprint(tmp_path):
    t = write(
        tmp_path, table_properties={"owner": "test", "delta.appendOnly": "true"}, fingerprint=True
    )
    assert t.metadata().configuration["owner"] == "test"
    assert "shape.fingerprint" in t.metadata().configuration
    assert read(t).column("id").to_pylist() == [1, 2]
    version = t.version()
    DeltaSink().write(
        str(tmp_path), "t", [batch((3,))], mode="append", table_properties={"owner": "next"}
    )
    t = deltalake.DeltaTable(str(tmp_path / "t"))
    assert t.version() > version
    assert t.metadata().configuration["owner"] == "next"
    assert "shape.fingerprint" in t.metadata().configuration
    assert sorted(read(t).column("id").to_pylist()) == [1, 2, 3]


@pytest.mark.parametrize(
    "properties",
    [[], {"owner": None}, {"shape.fingerprint": "spoof"}, {"delta.enableDeletionVectors": "true"}],
)
def test_wanted1_properties_invalid_or_reserved(tmp_path, properties):
    with pytest.raises(ValueError, match="table_properties"):
        write(tmp_path, table_properties=properties)
    assert not (tmp_path / "t" / "_delta_log").exists()


def test_wanted2_constraints_and_violating_append_atomic(tmp_path):
    constraints = {"not_null": ["id"], "check": {"positive": "id > 0"}}
    t = write(tmp_path, constraints=constraints)
    assert t.metadata().configuration["delta.constraints.positive"] == "id > 0"
    assert t.protocol().min_writer_version == 3
    version = t.version()
    for ids, name in [((0,), "positive"), ((None,), "not_null_id")]:
        with pytest.raises(ValueError, match=name):
            DeltaSink().write(
                str(tmp_path),
                "t",
                [batch(ids)],
                mode="append",
                table_properties={"owner": "not-written"},
            )
        t = deltalake.DeltaTable(str(tmp_path / "t"))
        assert t.version() == version
        assert "owner" not in t.metadata().configuration
        assert read(t).num_rows == 2


def test_wanted2_new_constraint_checks_existing_before_write(tmp_path):
    t = write(tmp_path)
    with pytest.raises(ValueError, match="large"):
        DeltaSink().write(
            str(tmp_path),
            "t",
            [batch((10,))],
            mode="append",
            constraints={"check": {"large": "id > 5"}},
        )
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == t.version()
    assert read(deltalake.DeltaTable(str(tmp_path / "t"))).num_rows == 2


@pytest.mark.parametrize(
    "constraints",
    [
        {"not_null": ["missing"]},
        {"check": {"bad": "unknown > 0"}},
        {"check": {"bad": "id >"}},
        {"check": {"": "id > 0"}},
        {"not_null": "id"},
    ],
)
def test_wanted2_invalid_constraints_leave_no_table(tmp_path, constraints):
    with pytest.raises(ValueError, match="constraint"):
        write(tmp_path, constraints=constraints)
    assert not (tmp_path / "t" / "_delta_log").exists()


def test_wanted2_boundary_empty_null_check_and_schema_derivation(tmp_path):
    t = write(tmp_path, constraints={"check": {"boundary": "id >= 1"}})
    assert read(t).num_rows == 2
    root = tmp_path / "derived"
    b = pa.record_batch(
        [pa.array([], pa.int64())], schema=pa.schema([pa.field("id", pa.int64(), nullable=False)])
    )
    DeltaSink().write(str(root), "t", [b], constraints=True)
    d = deltalake.DeltaTable(str(root / "t"))
    assert "delta.constraints.not_null_id" in d.metadata().configuration
    assert read(d).num_rows == 0


def test_wanted3_mapping_protocol_physical_metadata_and_native_read(tmp_path):
    t = write(tmp_path, column_mapping="name")
    assert (t.protocol().min_reader_version, t.protocol().min_writer_version) == (2, 5)
    assert t.metadata().configuration["delta.columnMapping.mode"] == "name"
    fields = json.loads(t.schema().to_json())["fields"]
    ids = [f["metadata"]["delta.columnMapping.id"] for f in fields]
    physical = [f["metadata"]["delta.columnMapping.physicalName"] for f in fields]
    assert len(set(ids)) == len(ids) and all(i > 0 for i in ids)
    assert len(set(physical)) == len(physical)
    assert read(t).to_pydict() == batch().to_pydict()
    DeltaSink().write(str(tmp_path), "t", [batch((3,))], mode="append", column_mapping="name")
    assert sorted(read(deltalake.DeltaTable(str(tmp_path / "t"))).column("id").to_pylist()) == [
        1,
        2,
        3,
    ]


@pytest.mark.parametrize("mapping", ["id", True, "bogus"])
def test_wanted3_invalid_mapping(tmp_path, mapping):
    with pytest.raises(ValueError, match="column_mapping"):
        write(tmp_path, column_mapping=mapping)


def test_wanted4_feature_only_no_delete_or_rewrite_native_read(tmp_path):
    t = write(tmp_path, deletion_vectors=True)
    p = t.protocol()
    assert (p.min_reader_version, p.min_writer_version) == (3, 7)
    assert "deletionVectors" in p.reader_features and "deletionVectors" in p.writer_features
    assert t.metadata().configuration["delta.enableDeletionVectors"] == "true"
    first = set(t.file_uris())
    DeltaSink().write(str(tmp_path), "t", [batch((3,))], mode="append", deletion_vectors=True)
    t = deltalake.DeltaTable(str(tmp_path / "t"))
    assert first.issubset(t.file_uris())
    assert sorted(read(t).column("id").to_pylist()) == [1, 2, 3]
    log = actions(tmp_path / "t")
    assert not any("remove" in a for a in log)
    assert not any(a.get("add", {}).get("deletionVector") for a in log)
    with pytest.raises(ValueError, match="mode"):
        write(tmp_path, deletion_vectors=True, mode="upsert")


def test_wanted5_generated_date_partition_and_disagreement(tmp_path):
    b = pa.record_batch(
        {
            "ts": pa.array([datetime(2026, 1, 2, tzinfo=UTC)], pa.timestamp("us", tz="UTC")),
            "day": pa.array([date(2026, 1, 2)], pa.date32()),
        }
    )
    opts = {"generated_columns": {"day": "CAST(ts AS DATE)"}, "partition_by": ["day"]}
    DeltaSink().write(str(tmp_path), "t", [b], **opts)
    t = deltalake.DeltaTable(str(tmp_path / "t"))
    assert (
        json.loads(t.schema().to_json())["fields"][1]["metadata"]["delta.generationExpression"]
        == "CAST(ts AS DATE)"
    )
    assert t.protocol().min_writer_version == 4
    assert read(t).column("day").to_pylist() == [date(2026, 1, 2)]
    bad = pa.record_batch([b.column(0), pa.array([date(2026, 1, 3)], pa.date32())], schema=b.schema)
    for extra in [opts, {}]:
        with pytest.raises(ValueError, match="day"):
            DeltaSink().write(str(tmp_path), "t", [bad], mode="append", **extra)
        assert deltalake.DeltaTable(str(tmp_path / "t")).version() == t.version()


@pytest.mark.parametrize(
    "generated", [{"unknown": "id+1"}, {"label": ""}, {"label": "unknown+"}, []]
)
def test_wanted5_invalid_generated_columns(tmp_path, generated):
    with pytest.raises(ValueError, match="generated_columns"):
        write(tmp_path, generated_columns=generated)
    assert not (tmp_path / "t" / "_delta_log").exists()


def test_wanted6_ntz_opt_in_and_default_utc(tmp_path):
    b = pa.record_batch({"ts": pa.array([0, None, 1], pa.timestamp("us"))})
    for flag in [False, True]:
        root = tmp_path / str(flag)
        DeltaSink().write(str(root), "t", [b], timestamp_ntz=flag)
        t = deltalake.DeltaTable(str(root / "t"))
        p = t.protocol()
        assert (p.min_reader_version, p.min_writer_version) == ((3, 7) if flag else (1, 2))
        actual = read(t).schema.field("ts").type
        assert actual.tz == (None if flag else "UTC")
        assert read(t).column("ts").null_count == 1
        if flag:
            assert "timestampNtz" in p.reader_features


def test_wanted7_combined_plan_and_doctor(tmp_path):
    options = {
        "column_mapping": "name",
        "deletion_vectors": True,
        "timestamp_ntz": True,
        "constraints": {"check": {"positive": "id > 0"}},
        "generated_columns": {"x": "id + 1"},
    }
    plan = delta.feature_plan(options)
    assert (plan["min_reader_version"], plan["min_writer_version"]) == (3, 7)
    assert {"columnMapping", "deletionVectors", "timestampNtz"} <= set(plan["reader_features"])
    assert {"checkConstraints", "generatedColumns"} <= set(plan["writer_features"])
    assert delta.feature_plan({}) == {
        "min_reader_version": 1,
        "min_writer_version": 2,
        "reader_features": [],
        "writer_features": [],
    }
    write(tmp_path, column_mapping="name", deletion_vectors=True)
    f = doctor_checks.delta_features(str(tmp_path / "t"))
    assert f["min_reader_version"] == 3 and f["min_writer_version"] == 7
    assert "deletionVectors" in f["writer_features"]
    check = doctor_checks.check_delta_limits(lambda: f, version=deltalake.__version__)
    assert "reader 3 / writer 7" in check.message


def test_wanted7_cli_dry_run_reports_features_without_writing(tmp_path, capsys):
    output = tmp_path / "absent"
    rc = main(
        [
            "generate",
            "retail",
            "--rows",
            "customer=1",
            "--format",
            "delta",
            "-o",
            str(output),
            "--dry-run",
            "--json",
            "--sink-config",
            "delta.deletion_vectors=true",
            "--sink-config",
            "delta.column_mapping=name",
        ]
    )
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["delta_protocol"]["min_reader_version"] == 3
    assert "columnMapping" in doc["delta_protocol"]["reader_features"]
    assert not output.exists()


@pytest.mark.parametrize(
    "name,value",
    [
        ("table_properties", {"owner": "a"}),
        ("constraints", {"not_null": ["id"]}),
        ("column_mapping", "name"),
        ("deletion_vectors", True),
        ("generated_columns", {"label": "'a'"}),
        ("timestamp_ntz", True),
    ],
)
def test_wanted8_unavailable_version_names_installed_required_api(
    tmp_path, monkeypatch, name, value
):
    fake = SimpleNamespace(
        __version__="0.17.0", write_deltalake=lambda *a, **k: pytest.fail("writer must not run")
    )
    monkeypatch.setattr(delta, "_deltalake", lambda: fake)
    with pytest.raises(ValueError, match=rf"{name}.*0\.17\.0.*1\.6\.6"):
        write(tmp_path, **{name: value})
    assert not (tmp_path / "t" / "_delta_log").exists()


@pytest.mark.parametrize("flag", ["deletion_vectors", "timestamp_ntz"])
@pytest.mark.parametrize("value", ["yes", 1, None])
def test_boolean_options_refuse_invalid_values(tmp_path, flag, value):
    with pytest.raises(ValueError, match=flag):
        write(tmp_path, **{flag: value})


def test_features_streaming_and_empty_schema(tmp_path):
    b = batch()
    writer = DeltaSink().open_table(
        str(tmp_path),
        "t",
        b.schema,
        commit_rows=1,
        table_properties={"owner": "stream"},
        constraints={"not_null": ["id"]},
    )
    writer.write_batch(b)
    writer.write_batch(batch((3,)))
    assert writer.close() == 3
    assert sorted(read(deltalake.DeltaTable(str(tmp_path / "t"))).column("id").to_pylist()) == [
        1,
        2,
        3,
    ]
    DeltaSink().write(str(tmp_path), "empty", [], schema=b.schema, column_mapping="name")
    assert read(deltalake.DeltaTable(str(tmp_path / "empty"))).num_rows == 0


def test_wanted9_docs_and_no_default_configuration_change():
    root = Path(__file__).resolve().parents[2]
    for name in ["docs/SINKS.md", "docs/FABRIC_PLATFORM.md", "CHANGELOG.md"]:
        text = (root / name).read_text()
        assert "column_mapping" in text and "timestamp_ntz" in text and "deletion_vectors" in text


@pytest.mark.parametrize("feature", ["column_mapping", "table_properties", "deletion_vectors"])
def test_generated_failure_with_feature_creates_no_destination(tmp_path, feature):
    options = (
        {"column_mapping": "name"}
        if feature == "column_mapping"
        else {"table_properties": {"owner": "a"}}
        if feature == "table_properties"
        else {"deletion_vectors": True}
    )
    with pytest.raises(ValueError, match="label"):
        write(tmp_path, generated_columns={"label": "'different'"}, **options)
    assert not (tmp_path / "t" / "_delta_log").exists()


def test_constraint_conflicting_name_and_property_failure_atomic(tmp_path):
    t = write(tmp_path, constraints={"check": {"positive": "id > 0"}})
    with pytest.raises(ValueError, match="positive"):
        DeltaSink().write(
            str(tmp_path),
            "t",
            [batch((3,))],
            mode="append",
            constraints={"check": {"positive": "id >= 0"}},
        )
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == t.version()
    with pytest.raises(ValueError, match="table_properties"):
        DeltaSink().write(
            str(tmp_path),
            "t",
            [batch((3,))],
            mode="append",
            table_properties={"delta.appendOnly": "invalid"},
        )
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == t.version()


def test_combined_features_real_protocol_and_native_readback(tmp_path):
    b = pa.record_batch({"id": [1, 2], "twice": [2, 4], "ts": pa.array([0, 1], pa.timestamp("us"))})
    options = {
        "column_mapping": "name",
        "deletion_vectors": True,
        "timestamp_ntz": True,
        "generated_columns": {"twice": "id * 2"},
        "constraints": {"not_null": ["id"], "check": {"positive": "id > 0"}},
        "table_properties": {"owner": "combined"},
    }
    DeltaSink().write(str(tmp_path), "t", [b], **options)
    t = deltalake.DeltaTable(str(tmp_path / "t"))
    assert (t.protocol().min_reader_version, t.protocol().min_writer_version) == (3, 7)
    assert set(t.protocol().reader_features) >= {"columnMapping", "deletionVectors", "timestampNtz"}
    assert set(t.protocol().writer_features) >= {
        "columnMapping",
        "deletionVectors",
        "timestampNtz",
        "generatedColumns",
        "checkConstraints",
    }
    assert read(t).to_pydict() == b.to_pydict()


def test_mapping_nullable_values_and_logical_schema(tmp_path):
    b = pa.record_batch({"id": pa.array([None, 1], pa.int64()), "label": ["a", None]})
    DeltaSink().write(str(tmp_path), "t", [b], column_mapping="name")
    t = deltalake.DeltaTable(str(tmp_path / "t"))
    actual = pa.table(
        deltalake.QueryBuilder().register("t", t).execute("SELECT id, label FROM t").read_all()
    )
    assert actual.to_pydict() == b.to_pydict()
    actual = actual.cast(pa.schema(t.schema().to_arrow()))
    assert actual.schema.names == b.schema.names and [f.type for f in actual.schema] == [
        f.type for f in b.schema
    ]
    assert actual.to_pydict() == b.to_pydict()


@pytest.mark.parametrize(
    "feature,options",
    [
        ("columnMapping", {"column_mapping": "name"}),
        ("checkConstraints", {"constraints": {"check": {"positive": "id > 0"}}}),
        ("generatedColumns", {"generated_columns": {"label": "'a'"}}),
    ],
)
def test_doctor_reports_legacy_implicit_features(tmp_path, feature, options):
    write(tmp_path, **options)
    found = doctor_checks.delta_features(str(tmp_path / "t"))
    check = doctor_checks.check_delta_limits(lambda: found, version=deltalake.__version__)
    assert feature in check.message


def test_same_version_missing_public_api_and_noop_options(tmp_path, monkeypatch):
    fake = SimpleNamespace(**deltalake.__dict__)
    fake.TableFeatures = SimpleNamespace()
    monkeypatch.setattr(delta, "_deltalake", lambda: fake)
    with pytest.raises(ValueError, match=r"column_mapping.*1\.6\.6.*required public API"):
        write(tmp_path, column_mapping="name")
    # Disabled flags require no feature API and preserve the ordinary write.
    t = write(tmp_path, timestamp_ntz=False, deletion_vectors=False, column_mapping="none")
    assert (t.protocol().min_reader_version, t.protocol().min_writer_version) == (1, 2)


def test_generated_errors_never_echo_row_values(tmp_path, monkeypatch):
    fake = SimpleNamespace(**deltalake.__dict__)
    original = deltalake.write_deltalake

    def failing(table_or_uri, data, *, schema_mode=None, **kwargs):
        raise RuntimeError("sign-in-secret and row private-value")

    fake.write_deltalake = failing
    monkeypatch.setattr(delta, "_deltalake", lambda: fake)
    with pytest.raises(ValueError, match="generated_columns") as err:
        write(tmp_path, generated_columns={"label": "'a'"})
    assert "sign-in-secret" not in str(err.value) and "private-value" not in str(err.value)
    assert original is deltalake.write_deltalake


def test_unmapped_conversion_refused_before_existing_version_changes(tmp_path):
    t = write(tmp_path)
    with pytest.raises(ValueError, match="column_mapping.*none"):
        DeltaSink().write(str(tmp_path), "t", [batch((3,))], mode="append", column_mapping="name")
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == t.version()


def test_cli_non_delta_dry_run_does_not_advertise_unused_features(capsys):
    assert (
        main(
            [
                "generate",
                "retail",
                "--format",
                "parquet",
                "--dry-run",
                "--json",
                "--sink-config",
                "delta.deletion_vectors=true",
            ]
        )
        == 0
    )
    assert "delta_protocol" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("options", [{"column_mapping": "name"}, {"deletion_vectors": True}])
def test_wanted7_existing_duckdb_fallback_reads_new_feature_table(tmp_path, options):
    from shape.profile.reference.delta_fallback import read_via_duckdb

    t = write(tmp_path, **options)
    actual = read_via_duckdb(str(tmp_path / "t"), schema=pa.schema(t.schema().to_arrow()))
    assert actual.to_pydict() == batch().to_pydict()


def test_wanted8_missing_add_feature_refused_for_mapping(tmp_path, monkeypatch):
    fake = SimpleNamespace(**deltalake.__dict__)
    alter = SimpleNamespace(set_table_properties=deltalake.table.TableAlterer.set_table_properties)
    fake.table = SimpleNamespace(TableAlterer=alter)
    monkeypatch.setattr(delta, "_deltalake", lambda: fake)
    with pytest.raises(ValueError, match="column_mapping.*required public API"):
        write(tmp_path, column_mapping="name")
    assert not (tmp_path / "t" / "_delta_log").exists()


def test_generated_null_expression_and_empty_object_boundary(tmp_path):
    b = pa.record_batch(
        {"id": pa.array([None, 1], pa.int64()), "next_id": pa.array([None, 2], pa.int64())}
    )
    DeltaSink().write(
        str(tmp_path), "t", [b], generated_columns={"next_id": "id + 1"}, table_properties={}
    )
    assert read(deltalake.DeltaTable(str(tmp_path / "t"))).to_pydict() == b.to_pydict()


def test_timestamp_ntz_opt_in_without_timestamp_still_declares_feature(tmp_path):
    t = write(tmp_path, timestamp_ntz=True)
    assert (t.protocol().min_reader_version, t.protocol().min_writer_version) == (3, 7)
    assert "timestampNtz" in t.protocol().reader_features
    assert read(t).to_pydict() == batch().to_pydict()


def test_table_inspection_errors_never_echo_credentials(tmp_path, monkeypatch):
    fake = SimpleNamespace(**deltalake.__dict__)

    class BrokenTable(deltalake.DeltaTable):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("storage-key-private and private-sign-in")

    fake.DeltaTable = BrokenTable
    monkeypatch.setattr(delta, "_deltalake", lambda: fake)
    with pytest.raises(ValueError, match="sign-in") as err:
        write(tmp_path, table_properties={"owner": "test"})
    assert "storage-key-private" not in str(err.value)
    assert "private-sign-in" not in str(err.value)
    assert not (tmp_path / "t" / "_delta_log").exists()


@pytest.mark.parametrize(
    "options,feature",
    [
        ({"column_mapping": "name", "deletion_vectors": True}, "column_mapping"),
        ({"timestamp_ntz": True}, "timestamp_ntz"),
    ],
)
def test_feature_commit_errors_never_echo_credentials(tmp_path, monkeypatch, options, feature):
    import traceback

    original = deltalake.table.TableAlterer.add_feature
    destination = str(tmp_path / "t")

    def fail_at_destination(self, table_feature, allow_protocol_versions_increase=False, **kwargs):
        from urllib.parse import urlsplit

        if urlsplit(self.table.table_uri).path.rstrip("/") == destination:
            raise RuntimeError("dummy-private-auth-body")
        return original(
            self,
            table_feature,
            allow_protocol_versions_increase=allow_protocol_versions_increase,
            **kwargs,
        )

    monkeypatch.setattr(deltalake.table.TableAlterer, "add_feature", fail_at_destination)
    with pytest.raises(ValueError, match=feature) as err:
        write(tmp_path, **options)
    rendered = "".join(traceback.format_exception(err.type, err.value, err.tb))
    assert (
        "dummy-private-auth-body" not in str(err.value)
        and "dummy-private-auth-body" not in rendered
    )


def test_default_cloud_failure_inspects_rules_only_after_writer_failure(monkeypatch):
    import traceback

    events = []
    fake = SimpleNamespace(__version__=deltalake.__version__)

    def failing_writer(*args, **kwargs):
        events.append("write")
        raise RuntimeError("dummy-private-auth-body")

    class RuleTable:
        def __init__(self, *args, **kwargs):
            events.append("inspect")

        def metadata(self):
            return SimpleNamespace(configuration={"delta.constraints.positive": "id > 0"})

        def schema(self):
            return SimpleNamespace(to_json=lambda: json.dumps({"fields": []}))

    fake.write_deltalake = failing_writer
    fake.DeltaTable = RuleTable
    monkeypatch.setattr(delta, "_deltalake", lambda: fake)
    with pytest.raises(ValueError, match="positive") as err:
        DeltaSink().write(
            "delta+abfss://acct@acct.dfs.core.windows.net/lake/Tables",
            "t",
            [batch()],
            account_key="dummy-key",
        )
    rendered = "".join(traceback.format_exception(err.type, err.value, err.tb))
    assert events == ["write", "inspect"]
    assert "dummy-private-auth-body" not in rendered
    assert "dummy-key" not in rendered


def test_declared_nonnullable_null_names_constraint_before_commit(tmp_path):
    schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
    good = pa.record_batch([pa.array([1], pa.int64())], schema=schema)
    bad = pa.record_batch([pa.array([None], pa.int64())], schema=schema)
    for name in ["new", "existing"]:
        if name == "existing":
            DeltaSink().write(str(tmp_path), name, [good], constraints=True)
            version = deltalake.DeltaTable(str(tmp_path / name)).version()
        with pytest.raises(ValueError, match="not_null_id"):
            DeltaSink().write(str(tmp_path), name, [bad], constraints=True, mode="append")
        if name == "existing":
            assert deltalake.DeltaTable(str(tmp_path / name)).version() == version
        else:
            assert not (tmp_path / name / "_delta_log").exists()


def test_streaming_declared_nonnullable_null_names_constraint(tmp_path):
    schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
    good = pa.record_batch([pa.array([1], pa.int64())], schema=schema)
    bad = pa.record_batch([pa.array([None], pa.int64())], schema=schema)
    writer = DeltaSink().open_table(str(tmp_path), "t", schema, commit_rows=1, constraints=True)
    writer.write_batch(good)
    before = deltalake.DeltaTable(str(tmp_path / "t")).version()
    with pytest.raises(ValueError, match="not_null_id"):
        writer.write_batch(bad)
    writer.abort()
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == before
