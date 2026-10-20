"""Oracle catalog, bounded transport and safe profile acceptance (W9-15)."""

import datetime as dt
import decimal

import pyarrow as pa
import pytest
from shape_databases.oracle_sources import OracleSource, catalog_field
from shape_databases.oracle_testing import FakeOracle

from shape.errors import ShapeError


@pytest.mark.parametrize(
    "sql_type,p,s,expected",
    [
        ("NUMBER", 2, 0, pa.int8()),
        ("NUMBER", 4, 0, pa.int16()),
        ("NUMBER", 9, 0, pa.int32()),
        ("NUMBER", 18, 0, pa.int64()),
        ("NUMBER", 19, 0, pa.decimal128(19, 0)),
        ("NUMBER", 12, 4, pa.decimal128(12, 4)),
        ("BINARY_FLOAT", None, None, pa.float32()),
        ("BINARY_DOUBLE", None, None, pa.float64()),
        ("BOOLEAN", None, None, pa.bool_()),
        ("VARCHAR2", None, None, pa.string()),
        ("CLOB", None, None, pa.string()),
        ("NCLOB", None, None, pa.string()),
        ("RAW", None, None, pa.binary()),
        ("BLOB", None, None, pa.binary()),
        ("DATE", None, None, pa.timestamp("us")),
        ("TIMESTAMP(9)", None, 9, pa.timestamp("ns")),
        ("TIMESTAMP(6) WITH TIME ZONE", None, 6, pa.timestamp("us", "UTC")),
        ("INTERVAL DAY(0) TO SECOND(9)", None, 9, pa.time64("ns")),
    ],
)
def test_oracle_catalog_types(sql_type, p, s, expected):
    field = catalog_field("value", sql_type, True, p, s)
    assert field.type == expected and field.nullable is True
    assert field.metadata[b"shape.sql_type"] == sql_type.encode()


def test_oracle_unconstrained_number_warns_and_uses_documented_decimal():
    with pytest.warns(UserWarning, match="amount.*38,10"):
        field = catalog_field("amount", "NUMBER", False, None, None)
    assert field.type == pa.decimal128(38, 10) and not field.nullable


def fake_source(*, lob=False, rows=1000, arrow_batches=None):
    fields = [
        ("id", "VARCHAR2", "N", None, None),
        ("amount", "NUMBER", "Y", 12, 4),
        ("at", "TIMESTAMP(6) WITH TIME ZONE", "Y", None, 6),
    ]
    if lob:
        fields.append(("payload", "BLOB", "Y", None, None))
    fake = FakeOracle(
        catalog_answers={
            "SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL": [("CaseSchema",)],
            "FROM ALL_TAB_COLUMNS": fields,
            "SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED": [
                ("CaseTable", rows, dt.datetime(2026, 1, 1))
            ],
            "CONSTRAINT_TYPE IN ('P','U')": [("pk", "P", "id", 1), ("uq", "U", "amount", 1)],
            "CONSTRAINT_TYPE = 'R'": [("fk", "Parent", "id", "id", 1)],
            "SELECT SEARCH_CONDITION_VC": [],
            'SELECT COUNT(*) FROM "CaseSchema"."CaseTable"': [(7,)],
        },
        arrow_batches=arrow_batches,
    )
    return OracleSource(connect=fake.connect), fake


def test_oracle_arrow_read_preserves_decimal_and_utc_in_bounded_batches():
    data = pa.table(
        {
            "id": ["a", "b"],
            "amount": ["12345678.1234", "-0.0001"],
            "at": pa.array([dt.datetime(2026, 1, 1, 1, 2, 3, 456789)] * 2, type=pa.timestamp("us")),
        }
    )
    source, fake = fake_source(arrow_batches=[data])
    batches = list(source.read("oracle://shape@localhost/service?table=CaseTable", batch_size=1))
    assert [b.num_rows for b in batches] == [1, 1]
    assert batches[0].column(1)[0].as_py() == decimal.Decimal("12345678.1234")
    assert batches[0].schema.field("at").type == pa.timestamp("us", "UTC")
    statement = fake.statements[-1][0]
    assert 'TO_CHAR("amount"' in statement and 'SYS_EXTRACT_UTC("at")' in statement
    assert fake.fetch_sizes == [1]


def test_oracle_cursor_fallback_is_whole_read_and_reports_path():
    source, fake = fake_source(lob=True, rows=2)
    fake.catalog_answers['SELECT "id"'] = [
        ("a", "1.2300", dt.datetime(2026, 1, 1), b"payload"),
        ("b", "2.3400", dt.datetime(2026, 1, 1), None),
    ]
    read = list(source.read("oracle://shape@localhost/service?table=CaseTable", batch_size=1))
    assert len(read) == 2 and read[0].column(3)[0].as_py() == b"payload"
    profile = source.profile_database(
        "oracle://shape@localhost/service", sample_rows=2, batch_size=1
    )
    assert profile.provenance["read_path"] == "cursor"
    assert all(size == 1 for size in fake.fetch_sizes)


def test_oracle_schema_profile_catalog_keys_sampling_and_statistics():
    data = pa.table(
        {"id": ["a"], "amount": ["1.2300"], "at": pa.array([None], type=pa.timestamp("us"))}
    )
    source, fake = fake_source(arrow_batches=[data])
    profile = source.profile_database(
        "oracle://shape@localhost/service?schema=CaseSchema", tables=["CaseTable"], sample_rows=1
    )
    table = profile.tables["CaseTable"]
    assert table["row_count"] == 1000 and table["row_count_source"] == "statistics"
    assert table["last_analyzed"] == "2026-01-01T00:00:00"
    assert table["primary_key"] == ["id"]
    assert table["unique_keys"] == [{"name": "uq", "columns": ["amount"], "evidence": "declared"}]
    assert table["sampled_rows"] == 1 and table["sample_method"] == "oracle_sample"
    assert table["sampling"]["population_rows"] == 1000
    assert table["sampling"]["sampled_rows"] == 1
    assert table["columns"]["id"]["nullable"] is False
    assert profile.to_dict()["relationships"][0]["evidence"] == "declared"
    assert any(" SAMPLE (" in sql for sql, _ in fake.statements)


def test_oracle_exact_counts_and_catalog_only_never_fetch_data():
    source, fake = fake_source(rows=None)
    with pytest.raises(ShapeError, match="CaseTable.*exact_counts"):
        source.profile_database("oracle://shape@localhost/service", sample_rows=0)
    profile = source.profile_database(
        "oracle://shape@localhost/service?exact_counts=true", sample_rows=0
    )
    table = profile.tables["CaseTable"]
    assert table["row_count"] == 7 and table["row_count_source"] == "exact_count"
    assert table["sampled_rows"] == 0 and fake.fetch_sizes == []


@pytest.mark.parametrize(
    "uri,options,reason",
    [
        ("oracle://shape:fixture@localhost/service?table=t", {}, "password"),
        ("oracle://shape@localhost/service", {}, "table"),
        ("oracle://shape@localhost/service?table=t&unknown=x", {}, "unknown"),
        ("oracle://shape@localhost/service?table=t", {"batch_size": 0}, "batch_size"),
        ("oracle://shape@localhost/service?table=t", {"unknown": "x"}, "unknown"),
    ],
)
def test_oracle_source_refusals_before_connect(uri, options, reason):
    source, fake = fake_source()
    with pytest.raises(ShapeError, match=reason):
        list(source.read(uri, **options))
    assert fake.connections == []


def test_oracle_registered_source_and_sink():
    from shape.plugins.host import default_host

    host = default_host()
    assert host.get("shape.sources", "oracle").name == "oracle"
    assert host.get("shape.sinks", "oracle").name == "oracle"


def test_oracle_arrow_cursor_parity_and_dataframe_protocol(monkeypatch):
    data = pa.table(
        {"id": ["a"], "amount": ["1.2300"], "at": pa.array([None], type=pa.timestamp("us"))}
    )

    class Frame:
        def column_arrays(self):
            return [data.column(i).combine_chunks() for i in range(data.num_columns)]

        def column_names(self):
            return data.column_names

    source, fake = fake_source(arrow_batches=[Frame()])
    arrow = pa.Table.from_batches(source.read("oracle://shape@localhost/service?table=CaseTable"))
    fake.catalog_answers['SELECT "id"'] = [("a", "1.2300", None)]
    monkeypatch.setattr(source, "_cursor_path", lambda fields: True)
    cursor = pa.Table.from_batches(source.read("oracle://shape@localhost/service?table=CaseTable"))
    assert arrow.equals(cursor, check_metadata=True)


def test_oracle_unconstrained_precision_overflow_and_profile_roundtrip(tmp_path):
    import shape

    source, _ = fake_source()
    with pytest.warns(UserWarning):
        field = catalog_field("amount", "NUMBER", True, None, None)
    for text in ("12345678901234567890123456789.1234567890", "0.12345678901"):
        with pytest.raises(ShapeError, match="amount.*decimal128"):
            source._batch([(text,)], pa.schema([field]))
    prof = source.profile_database("oracle://shape@localhost/service", sample_rows=0)
    path = tmp_path / "catalog.shape"
    shape.save(prof, path)
    loaded = shape.load(path)
    assert loaded.tables["CaseTable"]["row_count_source"] == "statistics"
    assert loaded.tables["CaseTable"]["last_analyzed"] == "2026-01-01T00:00:00"
    assert loaded.tables["CaseTable"]["unique_keys"] == prof.tables["CaseTable"]["unique_keys"]


@pytest.mark.parametrize("population", [0, 1, 1000, 1999, 2000])
def test_oracle_sample_percentage_never_includes_100(population):
    source, fake = fake_source(rows=population)
    profile = source.profile_database("oracle://shape@localhost/service", sample_rows=1000)
    queries = [sql for sql, _ in fake.statements if "FETCH FIRST" in sql]
    assert len(queries) == 1 and " SAMPLE (100" not in queries[0]
    assert profile.tables["CaseTable"]["sample_method"] == "whole_table"


def test_oracle_mixed_lob_timestamp_ns_fallback_is_lossless():
    source, fake = fake_source(lob=True)
    fake.catalog_answers["FROM ALL_TAB_COLUMNS"] = [
        ("at", "TIMESTAMP(9) WITH TIME ZONE", "N", None, 9),
        ("payload", "BLOB", "Y", None, None),
    ]
    fake.catalog_answers['SELECT TO_CHAR(SYS_EXTRACT_UTC("at")'] = [
        ("2026-01-01T00:00:00.123456789", b"x")
    ]
    batches = list(source.read("oracle://shape@localhost/service?table=CaseTable"))
    assert len(batches) == 1
    expected = pa.array(["2026-01-01T00:00:00.123456789Z"]).cast(pa.timestamp("ns", "UTC"))
    assert batches[0].column(0).cast(pa.int64()).equals(expected.cast(pa.int64()))


def test_oracle_invalid_boolean_and_fractional_integer_never_coerce():
    source, _ = fake_source()
    for field, value in ((pa.field("flag", pa.bool_()), "2"), (pa.field("id", pa.int64()), "1.5")):
        with pytest.raises(ShapeError, match=f"{field.name}.*cannot fit"):
            source._batch([(value,)], pa.schema([field]))
