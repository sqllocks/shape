"""Issues #15 and #16: the landing layout and the daily batches (library level)."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest
from iss_gaps_schemas import daily_gen_schema, daily_schema

from shape.builtins.sinks import CsvSink, ParquetSink
from shape.generation.batches import BatchGenerator
from shape.generation.landing import write_landing
from shape.io.landing import DEFAULT_TEMPLATE, parse_pairs, parse_table_formats, render_path

ROWS = {"customer": 30, "order": 80}


def table(n: int = 3) -> pa.Table:
    return pa.table({"id": list(range(n)), "v": [f"x{i}" for i in range(n)]})


# ---- path template ----------------------------------------------------------------------------


def test_template_of_the_issue_renders_the_documented_path() -> None:
    assert (
        render_path(DEFAULT_TEMPLATE, "orders", "parquet", "2026-08-04")
        == "orders/ingest_date=2026-08-04/orders_20260804.parquet"
    )


def test_every_token() -> None:
    got = render_path("{table}/{yyyy}/{mm}/{dd}/{date}/{yyyymmdd}.{ext}", "t", "csv", "2026-08-04")
    assert got == "t/2026/08/04/2026-08-04/20260804.csv"


@pytest.mark.parametrize(
    "template",
    ["{table}/{nope}.{ext}", "../{table}.{ext}", "/abs/{table}.{ext}", "{ext}", "{table}/{date"],
)
def test_bad_templates_are_rejected(template: str) -> None:
    with pytest.raises(ValueError):
        render_path(template, "t", "csv", "2026-08-04")


def test_a_date_token_needs_a_date_and_the_clock_is_never_used() -> None:
    with pytest.raises(ValueError, match="batch date"):
        render_path(DEFAULT_TEMPLATE, "t", "csv", None)
    assert render_path("{table}.{ext}", "t", "csv", None) == "t.csv"  # no date token, no date


def test_a_bad_date_is_an_error() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        render_path(DEFAULT_TEMPLATE, "t", "csv", "08/04/2026")


def test_pair_parsers() -> None:
    assert parse_table_formats(["a=csv", "b=parquet"]) == {"a": "csv", "b": "parquet"}
    assert parse_pairs(["a=3"], "x") == {"a": 3}
    for bad in (["a"], ["a="], ["=csv"]):
        with pytest.raises(ValueError):
            parse_table_formats(bad)
    for bad in (["a=x"], ["a=-1"], ["a"]):
        with pytest.raises(ValueError):
            parse_pairs(bad, "x")


# ---- the sinks' options -----------------------------------------------------------------------


def test_file_sinks_take_path_template_and_batch_date(tmp_path) -> None:
    opts = {"path_template": DEFAULT_TEMPLATE, "batch_date": "2026-08-04"}
    t = table()
    assert CsvSink().write(str(tmp_path), "customers", iter(t.to_batches()), **opts) == 3
    ParquetSink().write(str(tmp_path), "orders", iter(t.to_batches()), **opts)
    csv = tmp_path / "customers/ingest_date=2026-08-04/customers_20260804.csv"
    parquet = tmp_path / "orders/ingest_date=2026-08-04/orders_20260804.parquet"
    assert pacsv.read_csv(csv).num_rows == 3
    assert pq.ParquetFile(parquet).read().equals(t)  # the file alone: no hive columns (#333)


def test_a_sink_without_the_option_writes_as_before(tmp_path) -> None:
    CsvSink().write(str(tmp_path) + "/", "t", iter(table().to_batches()))
    assert (tmp_path / "t.csv").is_file()


def test_a_template_cannot_leave_the_root(tmp_path) -> None:
    with pytest.raises(ValueError):
        CsvSink().write(
            str(tmp_path), "t", iter(table().to_batches()), path_template="../{table}.{ext}"
        )


# ---- write_landing ----------------------------------------------------------------------------


def test_a_format_per_table(tmp_path) -> None:
    tables = {"orders": table(4), "customers": table(2)}
    landed = write_landing(
        tables,
        tmp_path,
        default_format="parquet",
        batch_date="2026-08-04",
        formats={"customers": "csv"},
    )
    paths = {f.table: f.path.relative_to(tmp_path).as_posix() for f in landed}
    assert paths == {
        "orders": "orders/ingest_date=2026-08-04/orders_20260804.parquet",
        "customers": "customers/ingest_date=2026-08-04/customers_20260804.csv",
    }
    assert [f.rows for f in landed] == [4, 2]
    assert pq.read_table(tmp_path / paths["orders"]).num_rows == 4


def test_a_misspelt_table_format_is_an_error(tmp_path) -> None:
    with pytest.raises(ValueError, match="not among the tables"):
        write_landing({"a": table()}, tmp_path, batch_date="2026-08-04", formats={"b": "csv"})


def test_delta_is_not_a_landing_file(tmp_path) -> None:
    with pytest.raises(ValueError, match="landing file"):
        write_landing({"a": table()}, tmp_path, batch_date="2026-08-04", formats={"a": "delta"})


def test_the_template_must_name_the_table(tmp_path) -> None:
    with pytest.raises(ValueError, match="must contain"):
        write_landing(
            {"a": table(), "b": table()}, tmp_path, template="all.{ext}", batch_date="2026-08-04"
        )


def test_a_backfill_writes_one_file_per_day(tmp_path) -> None:
    for day in range(3):
        d = dt.date(2026, 8, 1) + dt.timedelta(days=day)
        write_landing({"orders": table()}, tmp_path, batch_date=d)
    assert sorted(p.name for p in tmp_path.glob("orders/*/*.csv")) == [
        "orders_20260801.csv",
        "orders_20260802.csv",
        "orders_20260803.csv",
    ]


# ---- daily batches ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generator() -> BatchGenerator:
    return BatchGenerator(
        daily_gen_schema(),
        {"customer": 30, "order": 80},
        start_date="2026-08-01",
        date_columns={"order": "order_date"},
    )


def test_a_batch_holds_only_its_new_rows(generator: BatchGenerator) -> None:
    b = generator.generate("2026-08-04")  # batch 3
    assert b.batch_index == 3 and b.batch_date == dt.date(2026, 8, 4)
    assert b.row_ranges == {"customer": (90, 120), "order": (240, 320)}
    assert b.tables["customer"].num_rows == 30 and b.tables["order"].num_rows == 80
    keys = b.tables["customer"]["customer_id"].to_pylist()
    assert keys[0] == "C000000091" and keys[-1] == "C000000120"  # stable pattern keys, no overlap


def test_keys_are_stable_across_batches(generator: BatchGenerator) -> None:
    seen: set[str] = set()
    for i in range(5):
        keys = set(generator.generate(i).tables["customer"]["customer_id"].to_pylist())
        assert not keys & seen
        seen |= keys
    assert len(seen) == 150


def test_every_order_references_a_customer_that_exists_and_earlier_ones_are_reached(
    generator: BatchGenerator,
) -> None:
    parents: set[str] = set()
    reached_earlier = 0
    for i in range(6):
        b = generator.generate(i)
        parents |= set(b.tables["customer"]["customer_id"].to_pylist())
        fk = b.tables["order"]["customer_id"].to_pylist()
        assert set(fk) <= parents  # full FK integrity, parents of this and earlier batches
        if i:
            today = set(b.tables["customer"]["customer_id"].to_pylist())
            reached_earlier += sum(1 for v in fk if v not in today)
    assert reached_earlier > 0


def test_a_batch_is_regenerable_alone_and_byte_identical(generator: BatchGenerator) -> None:
    first = generator.generate(4)
    again = BatchGenerator(
        daily_gen_schema(),
        {"customer": 30, "order": 80},
        start_date="2026-08-01",
        date_columns={"order": "order_date"},
    ).generate("2026-08-05")
    for name in ("customer", "order"):
        assert first.tables[name].equals(again.tables[name])


def test_the_seed_changes_the_batch(generator: BatchGenerator) -> None:
    other = BatchGenerator(
        daily_gen_schema(),
        ROWS,
        start_date="2026-08-01",
        seed=99,
        date_columns={"order": "order_date"},
    )
    assert not other.generate(2).tables["order"].equals(generator.generate(2).tables["order"])


def test_the_date_column_is_the_batch_date(generator: BatchGenerator) -> None:
    dates = generator.generate("2026-08-04").tables["order"]["order_date"].to_pylist()
    assert {d.date() for d in dates} == {dt.date(2026, 8, 4)}
    assert len({d.time() for d in dates}) > 1  # the time of day is kept


def test_the_fast_path_equals_the_whole_table_slice() -> None:
    """The rows `generate_chunk` makes are the rows of the full table at the same totals."""
    schema = daily_gen_schema()
    g = BatchGenerator(schema, ROWS, start_date="2026-08-01")
    b = g.generate(3)
    from shape.generation.engine import Engine

    whole = Engine(schema, row_counts={"customer": 120, "order": 320}).generate().tables
    assert b.tables["customer"].equals(whole["customer"].slice(90, 30))
    assert b.tables["order"].equals(whole["order"].slice(240, 80))


def test_a_schema_with_a_post_pass_still_batches() -> None:
    from shape.generation.schema import GenSchema

    doc = daily_schema()
    doc["tables"]["order"]["columns"]["double"] = {
        "name": "double",
        "type": "float",
        "generator": {"strategy": "derived", "source": "amount"},
    }
    doc["tables"]["customer"]["columns"]["order_total"] = {
        "name": "order_total",
        "type": "float",
        "generator": {
            "strategy": "computed",
            "rule": "sum_children",
            "child_table": "order",
            "child_column": "amount",
        },
    }
    schema = GenSchema.from_dict(doc)
    from shape.generation.output import needs_post_pass

    assert needs_post_pass(schema)
    b = BatchGenerator(schema, ROWS, start_date="2026-08-01").generate(2)
    assert b.tables["customer"].num_rows == 30
    assert b.tables["customer"]["order_total"].null_count == 0


@pytest.mark.parametrize(
    "rows, kwargs, message",
    [
        ({}, {}, "TABLE=N"),
        ({"nope": 1}, {}, "not in the schema"),
        ({"order": 0}, {}, "at least 1"),
        ({"order": 5}, {"date_columns": {"customer": "x"}}, "not part of a batch"),
        ({"order": 5}, {"date_columns": {"order": "x"}}, "no column"),
    ],
)
def test_bad_batch_definitions(rows, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        BatchGenerator(daily_gen_schema(), rows, start_date="2026-08-01", **kwargs)


def test_a_date_before_the_start_is_an_error(generator: BatchGenerator) -> None:
    with pytest.raises(ValueError, match="before the start"):
        generator.generate("2026-07-31")
