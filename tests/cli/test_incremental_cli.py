"""P6-05: the continue and time-travel commands end to end, on retail (needs shape-domains)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

pytest.importorskip("shape_domains")

TABLES = {
    "address",
    "customer",
    "order",
    "order_line",
    "product",
    "product_category",
    "promotion",
    "return",
    "store",
}


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture(scope="module")
def start(tmp_path_factory) -> Path:
    """Retail at small scale, written once, as the data `continue` extends."""
    out = tmp_path_factory.mktemp("start")
    assert (
        main(["generate", "retail", "--scale", "small", "--format", "parquet", "-o", str(out)]) == 0
    )
    return out


def rows(directory: Path, table: str):
    return pq.read_table(directory / f"{table}.parquet").to_pylist()


def test_continue_writes_a_tagged_delta_per_table(capsys, start, tmp_path):
    out = tmp_path / "delta"
    code, text, _ = run(
        capsys, "continue", "retail", "--input", start, "-o", out, "--format", "parquet",
        "--inserts", 20, "--seed", 5, "--as-of", "2026-03-04T05:06:07", "--json",
    )  # fmt: skip
    assert code == 0
    report = json.loads(text)
    assert report["seed"] == 5 and set(report["stats"]) == TABLES
    assert report["stats"]["order"] == {"inserts": 20, "updates": 500, "deletes": 100}
    assert {p.stem for p in out.glob("*.parquet")} == TABLES
    delta = pq.read_table(out / "order.parquet")
    original = pq.read_table(start / "order.parquet")
    assert delta.column_names == original.column_names + [
        "_shape_delta_type",
        "_shape_delta_timestamp",
    ]
    assert delta.schema.types[: len(original.schema)] == original.schema.types
    kinds = delta.column("_shape_delta_type").to_pylist()
    assert (kinds.count("INSERT"), kinds.count("UPDATE"), kinds.count("DELETE")) == (20, 500, 100)
    assert {str(v) for v in delta.column("_shape_delta_timestamp").to_pylist()} == {
        "2026-03-04 05:06:07"
    }
    new_ids = sorted(r["order_id"] for r in delta.to_pylist() if r["_shape_delta_type"] == "INSERT")
    top = max(r["order_id"] for r in original.to_pylist())
    assert new_ids == list(range(top + 1, top + 21))


def test_continue_is_reproducible_and_seed_defaults_to_the_schemas(capsys, start, tmp_path):
    common = [
        "--input",
        start,
        "--format",
        "csv",
        "--inserts",
        10,
        "--as-of",
        "2026-01-01",
        "--json",
    ]
    code, text, _ = run(capsys, "continue", "retail", *common, "-o", tmp_path / "a")
    assert code == 0 and json.loads(text)["seed"] == 42  # retail's own seed
    run(capsys, "continue", "retail", *common, "-o", tmp_path / "b")
    for table in ("order", "customer"):
        a = (tmp_path / "a" / f"{table}.csv").read_bytes()
        assert a == (tmp_path / "b" / f"{table}.csv").read_bytes()
    code, _, _ = run(capsys, "continue", "retail", *common, "--seed", 7, "-o", tmp_path / "c")
    assert (tmp_path / "c" / "order.csv").read_bytes() != (
        tmp_path / "a" / "order.csv"
    ).read_bytes()


def test_continue_csv_and_jsonl_round_trip(capsys, start, tmp_path):
    csv_in = tmp_path / "csv_in"
    csv_in.mkdir()
    for table in ("customer", "address"):
        pacsv.write_csv(pq.read_table(start / f"{table}.parquet"), csv_in / f"{table}.csv")
    out = tmp_path / "jl"
    code, text, _ = run(
        capsys, "continue", "retail", "--input", csv_in, "-o", out, "--format", "jsonl",
        "--inserts", 5, "--delete-fraction", 0, "--seed", 1, "--json",
    )  # fmt: skip
    assert code == 0
    assert set(json.loads(text)["stats"]) == {"customer", "address"}
    first = json.loads((out / "customer.jsonl").read_text().splitlines()[0])
    assert "_shape_delta_type" in first


def test_continue_zero_changes_writes_no_files(capsys, start, tmp_path):
    out = tmp_path / "none"
    code, text, _ = run(
        capsys, "continue", "retail", "--input", start, "-o", out, "--inserts", 0,
        "--update-fraction", 0, "--delete-fraction", 0, "--json",
    )  # fmt: skip
    assert code == 0
    assert all(
        s == {"inserts": 0, "updates": 0, "deletes": 0} for s in json.loads(text)["stats"].values()
    )
    assert not list(out.glob("*"))


def test_continue_state_transitions_file(capsys, start, tmp_path):
    spec = tmp_path / "t.json"
    spec.write_text(json.dumps({"order.status": {"processing": {"shipped": 1.0}}}))
    out = tmp_path / "tr"
    code, _, _ = run(
        capsys, "continue", "retail", "--input", start, "-o", out, "--format", "parquet",
        "--inserts", 0, "--update-fraction", 1.0, "--delete-fraction", 0,
        "--transitions", spec, "--seed", 3,
    )  # fmt: skip
    assert code == 0
    before = {r["order_id"]: r["status"] for r in rows(start, "order")}
    for r in rows(out, "order"):
        if before[r["order_id"]] == "processing":
            assert r["status"] == "shipped"
        else:  # a state with no transition stays as it is
            assert r["status"] == before[r["order_id"]]


@pytest.mark.parametrize(
    "args, message",
    [
        (["--input", "/no/such/dir"], "input directory not found"),
        (["--update-fraction", 2], "between 0.0 and 1.0"),
        (["--as-of", "yesterday"], "--as-of"),
    ],
)
def test_continue_errors_exit_2(capsys, start, tmp_path, args, message):
    base = ["--input", start, "-o", tmp_path / "o"]
    code, _, err = run(capsys, "continue", "retail", *base, *args)
    assert code == 2 and message in err


def test_continue_empty_input_directory(capsys, tmp_path):
    (tmp_path / "empty").mkdir()
    code, _, err = run(
        capsys, "continue", "retail", "--input", tmp_path / "empty", "-o", tmp_path / "o"
    )
    assert code == 2 and "no CSV, Parquet or JSON Lines files" in err


def test_time_travel_writes_a_snapshot_per_month(capsys, tmp_path):
    out = tmp_path / "tt"
    code, text, _ = run(
        capsys, "time-travel", "retail", "-o", out, "--months", 3, "--scale", "small",
        "--seasonality", "3=2.0", "--seed", 9, "--json",
    )  # fmt: skip
    assert code == 0
    report = json.loads(text)
    assert [s["month"] for s in report["snapshots"]] == [0, 1, 2, 3]
    assert [s["date"] for s in report["snapshots"]] == [
        "2023-01-01",
        "2023-02-01",
        "2023-03-01",
        "2023-04-01",
    ]
    assert len(report["files"]) == 4 * len(TABLES)
    counts = [s["row_counts"]["order"] for s in report["snapshots"]]
    assert counts[0] == 5000
    assert counts[1] == 5000 - 100 + 250  # growth 5%, churn 2%
    assert counts[2] == counts[1] - int(counts[1] * 0.02) + int(counts[1] * 0.05 * 2.0)  # March x2
    for month in range(4):
        assert {p.stem for p in (out / f"month_{month}").glob("*.parquet")} == TABLES
    month3 = {n: pq.read_table(out / "month_3" / f"{n}.parquet") for n in TABLES}
    parents = set(month3["customer"].column("customer_id").to_pylist())
    assert set(month3["order"].column("customer_id").to_pylist()) <= parents  # full integrity


def test_time_travel_month_zero_is_the_generated_dataset(capsys, start, tmp_path):
    out = tmp_path / "tt0"
    code, _, _ = run(
        capsys, "time-travel", "retail", "-o", out, "--months", 1, "--scale", "small",
        "--seed", 42, "--format", "csv",
    )  # fmt: skip
    assert code == 0
    # `generate` defaults to retail's seed, 42, which is the seed passed here.
    original = pq.read_table(start / "customer.parquet")
    month0 = pacsv.read_csv(out / "month_0" / "customer.csv")
    assert month0.column("customer_id").to_pylist() == original.column("customer_id").to_pylist()


def test_time_travel_text_output(capsys, tmp_path):
    code, text, _ = run(
        capsys, "time-travel", "retail", "-o", tmp_path / "x", "--months", 1, "--scale", "small"
    )
    assert code == 0
    assert "Time-Travel Result" in text and "Snapshots: 2" in text and "Written 18 files" in text


@pytest.mark.parametrize(
    "args, message",
    [
        (["--scale", "enormous"], "unknown scale"),
        (["--seasonality", "november=2"], "MONTH=MULTIPLIER"),
        (["--months", -1], "months must be"),
        (["--churn-rate", 2], "churn_rate"),
        (["--start-date", "tomorrow"], "start_date"),
    ],
)
def test_time_travel_errors_exit_2(capsys, tmp_path, args, message):
    code, _, err = run(capsys, "time-travel", "retail", "-o", tmp_path / "o", *args)
    assert code == 2 and message in err


def test_both_commands_are_listed_in_help(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    text = capsys.readouterr().out
    assert "continue" in text and "time-travel" in text
