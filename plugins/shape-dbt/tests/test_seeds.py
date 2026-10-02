"""The dbt seeds sink: CSV files in `seeds/` and a `seeds:` block of column types."""

from __future__ import annotations

import csv

import pyarrow as pa
import pytest
import yaml
from shape_dbt.seeds import DEFAULT_MAX_BYTES, DbtSeedsSink, SeedError, column_type


def batch(**cols):
    return pa.RecordBatch.from_pydict(cols)


def types_of(project, table):
    doc = yaml.safe_load((project / "seeds" / "_shape_seeds.yml").read_text(encoding="utf-8"))
    return next(s for s in doc["seeds"] if s["name"] == table)["config"]["column_types"]


@pytest.fixture
def project(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: p\n", encoding="utf-8")
    return tmp_path


def test_a_table_is_a_csv_and_a_seeds_block(project):
    rows = DbtSeedsSink().write(
        f"dbt://{project}",
        "people",
        [batch(id=[1, 2], name=["ann", "bo"], score=[1.5, 2.0], ok=[True, False])],
    )
    assert rows == 2
    text = (project / "seeds" / "people.csv").read_text(encoding="utf-8")
    assert list(csv.reader(text.splitlines())) == [
        ["id", "name", "score", "ok"],
        ["1", "ann", "1.5", "true"],
        ["2", "bo", "2", "false"],
    ]
    assert types_of(project, "people") == {
        "id": "bigint",
        "name": "varchar",
        "score": "double precision",
        "ok": "boolean",
    }


def test_identifiers_with_leading_zeros_stay_text(project):
    DbtSeedsSink().write(
        str(project), "places", [batch(zip=["02134", "00000", "98101"], n=[1, 2, 3])]
    )
    assert "02134" in (project / "seeds" / "places.csv").read_text(encoding="utf-8")
    assert types_of(project, "places")["zip"] == "varchar"


def test_a_decimal_keeps_its_precision_and_scale(project):
    DbtSeedsSink().write(
        str(project),
        "pay",
        [batch(amount=[12.340000000000001, 3.0])],
        columns={"amount": {"type": "decimal", "precision": 10, "scale": 2}},
        dialect="duckdb",
    )
    assert types_of(project, "pay") == {"amount": "decimal(10,2)"}
    lines = (project / "seeds" / "pay.csv").read_text(encoding="utf-8").splitlines()
    assert lines[1:] == ["12.34", "3"]  # rounded to the scale, not 12.340000000000001


def test_an_arrow_decimal_is_declared_from_its_own_type(project):
    from decimal import Decimal

    arr = pa.array([Decimal("1.50"), Decimal("2.25")], pa.decimal128(8, 2))
    DbtSeedsSink().write(str(project), "d", [pa.RecordBatch.from_arrays([arr], ["v"])])
    assert types_of(project, "d") == {"v": "numeric(8,2)"}


def test_a_timestamp_in_a_date_column_is_cut_to_a_date(project):
    import datetime as dt

    ts = pa.array([dt.datetime(2024, 3, 1, 4, 5, 6), None], pa.timestamp("us"))
    DbtSeedsSink().write(
        str(project),
        "d",
        [pa.RecordBatch.from_arrays([ts], ["day"])],
        columns={"day": {"type": "date"}},
    )
    assert types_of(project, "d") == {"day": "date"}
    assert (project / "seeds" / "d.csv").read_text(encoding="utf-8").splitlines()[1] == "2024-03-01"


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        ("ansi", ("bigint", "double precision", "varchar", "boolean", "timestamp")),
        ("duckdb", ("bigint", "double", "varchar", "boolean", "timestamp")),
        ("tsql", ("bigint", "float", "varchar", "bit", "datetime2(6)")),
        ("spark", ("bigint", "double", "string", "boolean", "timestamp")),
    ],
)
def test_the_dialect_picks_the_type_names(dialect, expected):
    arrow = [pa.int64(), pa.float64(), pa.string(), pa.bool_(), pa.timestamp("us")]
    assert tuple(column_type(t, dialect=dialect) for t in arrow) == expected


def test_a_string_length_is_declared_for_tsql_only():
    info = {"type": "string", "max_length": 40}
    assert column_type(pa.string(), info, dialect="tsql") == "varchar(40)"
    assert column_type(pa.string(), info, dialect="duckdb") == "varchar"


def test_an_unknown_dialect_is_refused():
    with pytest.raises(SeedError, match="unknown dialect"):
        column_type(pa.int64(), dialect="oracle")


def test_a_second_table_is_added_and_a_rewrite_replaces_its_entry(project):
    sink = DbtSeedsSink()
    sink.write(str(project), "a", [batch(x=[1])])
    sink.write(str(project), "b", [batch(y=["u"])])
    sink.write(str(project), "a", [batch(x=[1], z=["v"])])
    doc = yaml.safe_load((project / "seeds" / "_shape_seeds.yml").read_text(encoding="utf-8"))
    assert [s["name"] for s in doc["seeds"]] == ["a", "b"]
    assert types_of(project, "a") == {"x": "bigint", "z": "varchar"}


def test_descriptions_go_to_the_seeds_block(project):
    DbtSeedsSink().write(
        str(project),
        "a",
        [batch(x=[1])],
        description="Table a.",
        column_descriptions={"x": "The key."},
    )
    entry = yaml.safe_load((project / "seeds" / "_shape_seeds.yml").read_text(encoding="utf-8"))[
        "seeds"
    ][0]
    assert entry["description"] == "Table a." and entry["columns"] == [
        {"name": "x", "description": "The key."}
    ]


def test_a_seed_over_the_limit_is_refused_and_leaves_nothing_behind(project):
    big = batch(s=["x" * 100] * 20_000)  # 2 MB
    with pytest.raises(SeedError, match="larger than 1,048,576 bytes"):
        DbtSeedsSink().write(str(project), "big", [big])
    assert not (project / "seeds" / "big.csv").exists()
    assert [p.name for p in (project / "seeds").iterdir()] == []  # no temp file left


def test_the_limit_can_be_lifted_or_changed(project):
    big = batch(s=["x" * 100] * 20_000)
    assert DbtSeedsSink().write(str(project), "big", [big], allow_large=True) == 20_000
    assert (project / "seeds" / "big.csv").stat().st_size > DEFAULT_MAX_BYTES
    with pytest.raises(SeedError, match="larger than 100 bytes"):
        DbtSeedsSink().write(str(project), "small", [batch(s=["x" * 100] * 10)], max_bytes=100)


def test_an_empty_table_writes_its_header(project):
    schema = pa.schema([("id", pa.int64()), ("name", pa.string())])
    assert DbtSeedsSink().write(str(project), "none", [], schema=schema) == 0
    assert (project / "seeds" / "none.csv").read_text(encoding="utf-8").splitlines() == [
        '"id","name"'
    ]
    with pytest.raises(SeedError, match="no batches"):
        DbtSeedsSink().write(str(project), "none2", [])


@pytest.mark.parametrize("name", ["../x", "a b", "a-b", "1a", ""])
def test_a_table_name_that_is_not_a_seed_name_is_refused(project, name):
    with pytest.raises(SeedError, match="not a valid seed name"):
        DbtSeedsSink().write(str(project), name, [batch(x=[1])])


def test_the_target_must_be_a_directory_inside_the_project(project):
    with pytest.raises(SeedError, match="not a directory"):
        DbtSeedsSink().write(str(project / "missing"), "a", [batch(x=[1])])
    with pytest.raises(SeedError, match="leaves the dbt project"):
        DbtSeedsSink().write(str(project), "a", [batch(x=[1])], seeds_dir="../out")


def test_the_cli_generates_a_schema_into_seeds(tmp_path, project, jaffle):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    from_dbt = cli.run_command(
        default_host(),
        "from-dbt",
        [str(jaffle), "-o", str(tmp_path / "j.gen.json")],
    )
    assert from_dbt == 0
    code = cli.run_command(
        default_host(),
        "dbt-seeds",
        [
            str(tmp_path / "j.gen.json"),
            "--project",
            str(project),
            "--rows",
            "raw_customers=20,raw_orders=50,raw_payments=80",
            "--seed",
            "5",
            "--dialect",
            "duckdb",
            "--metadata",
            str(tmp_path / "j.gen.dbt-meta.json"),
        ],
    )
    assert code == 0
    for name, n in (("raw_customers", 20), ("raw_orders", 50), ("raw_payments", 80)):
        lines = (project / "seeds" / f"{name}.csv").read_text(encoding="utf-8").splitlines()
        assert len(lines) == n + 1
    assert types_of(project, "raw_payments")["amount"] == "decimal(10,2)"
    assert types_of(project, "raw_orders")["order_date"] == "date"
    entry = yaml.safe_load((project / "seeds" / "_shape_seeds.yml").read_text(encoding="utf-8"))
    cust = next(s for s in entry["seeds"] if s["name"] == "raw_customers")
    assert cust["description"] == "One row per customer."


def test_the_cli_refuses_a_bad_row_count_and_a_bad_schema(tmp_path, project, capsys, jaffle):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    schema = tmp_path / "s.json"
    schema.write_text("{", encoding="utf-8")
    assert (
        cli.run_command(default_host(), "dbt-seeds", [str(schema), "--project", str(project)]) == 2
    )
    from_dbt = cli.run_command(default_host(), "from-dbt", [str(jaffle), "-o", str(schema)])
    assert from_dbt == 0
    code = cli.run_command(
        default_host(),
        "dbt-seeds",
        [str(schema), "--project", str(project), "--rows", "raw_orders=x"],
    )
    assert code == 2 and "bad row count" in capsys.readouterr().err


def test_the_sink_is_registered_for_the_host():
    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", "dbt-seeds")
    assert isinstance(sink, DbtSeedsSink) and sink.schemes == ("dbt",)
