"""Issues #13, #15 and #16 through the command line: ``shape chaos``, the landing options of
``shape generate`` and the daily batches of ``shape continue``."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest
from iss_gaps_schemas import daily_schema

from shape.chaos.groundtruth import read_ground_truth
from shape.cli.main import main


def run(capsys, *argv) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture(scope="module")
def schema_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("schema") / "daily.json"
    path.write_text(json.dumps(daily_schema()), encoding="utf-8")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


# ---- #15: the landing layout on generate ------------------------------------------------------


def test_generate_lands_one_file_per_table_in_its_format(capsys, schema_file, tmp_path) -> None:
    code, out, _ = run(
        capsys, "generate", schema_file, "--format", "parquet", "--table-format", "customer=csv",
        "--batch-date", "2026-08-04", "-o", tmp_path, "--json",
    )  # fmt: skip
    assert code == 0
    csv = tmp_path / "customer/ingest_date=2026-08-04/customer_20260804.csv"
    parquet = tmp_path / "order/ingest_date=2026-08-04/order_20260804.parquet"
    assert pacsv.read_csv(csv).num_rows == 10 and pq.read_table(parquet).num_rows == 20
    files = {f["table"]: f["format"] for f in json.loads(out)["files"]}
    assert files == {"customer": "csv", "order": "parquet"}


def test_generate_with_a_custom_template(capsys, schema_file, tmp_path) -> None:
    template = "{table}/{yyyy}/{mm}/{dd}/part-000000.{ext}"
    code, *_ = run(
        capsys, "generate", schema_file, "--format", "jsonl", "--path-template", template,
        "--batch-date", "2026-08-04", "-o", tmp_path,
    )  # fmt: skip
    assert code == 0 and (tmp_path / "order/2026/08/04/part-000000.jsonl").is_file()


def test_generate_without_the_options_is_unchanged(capsys, schema_file, tmp_path) -> None:
    code, *_ = run(capsys, "generate", schema_file, "--format", "parquet", "-o", tmp_path)
    assert code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["customer.parquet", "order.parquet"]


def test_a_dated_template_without_a_date_is_an_error(capsys, schema_file, tmp_path) -> None:
    code, _, err = run(
        capsys,
        "generate",
        schema_file,
        "--format",
        "csv",
        "--table-format",
        "order=csv",
        "-o",
        tmp_path,
    )
    assert code == 2 and "batch date" in err
    code, _, err = run(
        capsys, "generate", schema_file, "--batch-date", "2026-08-04", "-o", tmp_path
    )
    assert code == 2 and "--format" in err  # the default format, summary, writes nothing


def test_a_misspelt_table_format_is_an_error(capsys, schema_file, tmp_path) -> None:
    code, _, err = run(
        capsys, "generate", schema_file, "--format", "csv", "--table-format", "orders=csv", "--batch-date",
        "2026-08-04", "-o", tmp_path,
    )  # fmt: skip
    assert code == 2 and "orders" in err


def test_the_landing_bytes_are_the_plain_writers_bytes(capsys, schema_file, tmp_path) -> None:
    run(capsys, "generate", schema_file, "--format", "csv", "-o", tmp_path / "plain", "--seed", 3)
    run(
        capsys, "generate", schema_file, "--format", "csv", "--batch-date", "2026-08-04", "-o",
        tmp_path / "land", "--seed", 3,
    )  # fmt: skip
    plain = (tmp_path / "plain/order.csv").read_bytes()
    assert (tmp_path / "land/order/ingest_date=2026-08-04/order_20260804.csv").read_bytes() == plain


# ---- #16: daily batches on continue -----------------------------------------------------------


def daily(capsys, schema_file, out: Path, *extra) -> int:
    code, _, err = run(
        capsys, "continue", schema_file, "--daily-rows", "customer=30", "--daily-rows", "order=80",
        "--start-date", "2026-08-01", "--date-column", "order.order_date", "-o", out, *extra,
    )  # fmt: skip
    assert code == 0, err
    return code


def read_day(root: Path, table: str, day: str, ext: str):
    path = root / f"{table}/ingest_date={day}/{table}_{day.replace('-', '')}.{ext}"
    return pq.read_table(path) if ext == "parquet" else pacsv.read_csv(path)


def test_a_backfill_references_earlier_batches_with_full_integrity(
    capsys, schema_file, tmp_path
) -> None:
    daily(
        capsys, schema_file, tmp_path, "--batch-date", "2026-08-01", "--end-date", "2026-08-06",
        "--format", "parquet", "--table-format", "customer=csv",
    )  # fmt: skip
    parents: set[str] = set()
    earlier = 0
    for n in range(6):
        day = f"2026-08-0{n + 1}"
        customers = read_day(tmp_path, "customer", day, "csv")["customer_id"].to_pylist()
        assert len(customers) == 30 and not set(customers) & parents
        parents |= set(customers)
        fk = read_day(tmp_path, "order", day, "parquet")["customer_id"].to_pylist()
        assert len(fk) == 80 and set(fk) <= parents
        earlier += sum(1 for v in fk if v not in customers)
        dates = read_day(tmp_path, "order", day, "parquet")["order_date"].to_pylist()
        assert {d.date().isoformat() for d in dates} == {day}
    assert earlier > 0
    assert len(parents) == 180


def test_one_day_alone_is_the_same_bytes_as_that_day_in_the_run(
    capsys, schema_file, tmp_path
) -> None:
    daily(
        capsys,
        schema_file,
        tmp_path / "all",
        "--batch-date",
        "2026-08-01",
        "--end-date",
        "2026-08-05",
    )
    daily(capsys, schema_file, tmp_path / "one", "--batch-date", "2026-08-04")
    daily(capsys, schema_file, tmp_path / "again", "--batch-date", "2026-08-04")
    one = tree(tmp_path / "one")
    assert one == tree(tmp_path / "again")  # same seed and date, same bytes
    whole = tree(tmp_path / "all")
    assert one and all(whole[k] == v for k, v in one.items())  # and the same as in the full run


def test_a_different_seed_is_a_different_day(capsys, schema_file, tmp_path) -> None:
    daily(capsys, schema_file, tmp_path / "a", "--batch-date", "2026-08-03", "--seed", 1)
    daily(capsys, schema_file, tmp_path / "b", "--batch-date", "2026-08-03", "--seed", 2)
    assert tree(tmp_path / "a") != tree(tmp_path / "b")


def test_the_json_result_names_the_rows_of_each_day(capsys, schema_file, tmp_path) -> None:
    code, out, _ = run(
        capsys, "continue", schema_file, "--daily-rows", "order=80", "--start-date", "2026-08-01",
        "--batch-date", "2026-08-02", "-o", tmp_path, "--json",
    )  # fmt: skip
    assert code == 0
    (day,) = json.loads(out)["days"]
    assert day["batch_index"] == 1 and day["row_ranges"] == {"order": [80, 160]}


@pytest.mark.parametrize(
    "args, message",
    [
        (["--batch-date", "2026-08-02"], "--start-date"),
        (["--start-date", "2026-08-01"], "--batch-date"),
        (["--start-date", "2026-08-05", "--batch-date", "2026-08-02"], "before the start"),
        (
            [
                "--start-date",
                "2026-08-01",
                "--batch-date",
                "2026-08-03",
                "--end-date",
                "2026-08-02",
            ],
            "--end-date",
        ),
        (["--start-date", "2026-08-01", "--batch-date", "2026-08-03", "--input", "x"], "--input"),
    ],
)
def test_daily_argument_errors(capsys, schema_file, tmp_path, args, message) -> None:
    code, _, err = run(
        capsys, "continue", schema_file, "--daily-rows", "order=5", "-o", tmp_path, *args
    )
    assert code == 2 and message in err


def test_change_mode_needs_input_and_rejects_daily_flags(capsys, schema_file, tmp_path) -> None:
    code, _, err = run(capsys, "continue", schema_file, "-o", tmp_path)
    assert code == 2 and "--input" in err
    code, _, err = run(
        capsys,
        "continue",
        schema_file,
        "--input",
        tmp_path,
        "--start-date",
        "2026-08-01",
        "-o",
        tmp_path / "o",
    )
    assert code == 2 and "--daily-rows" in err


def test_change_mode_can_land_its_delta_in_the_layout(capsys, tmp_path) -> None:
    pytest.importorskip("shape_domains")  # a change delta needs integer keys: retail has them
    base = tmp_path / "base"
    args = ["generate", "retail", "--scale", "small", "--format", "parquet", "-o", base]
    assert run(capsys, *args)[0] == 0
    code, *_ = run(
        capsys, "continue", "retail", "--input", base, "--format", "parquet", "--inserts", "5",
        "--batch-date", "2026-08-04", "--as-of", "2026-08-04", "-o", tmp_path / "delta",
    )  # fmt: skip
    assert code == 0
    assert (tmp_path / "delta/order/ingest_date=2026-08-04/order_20260804.parquet").is_file()


# ---- #13: shape chaos -------------------------------------------------------------------------

CHAOS = [
    "--corrupt", "duplicates=0.05@order",
    "--corrupt", "orphan_keys=0.05@order.customer_id",
    "--corrupt", "date_shift=0.1@order.order_date:days=10",
    "--corrupt", "negative_amounts=0.1@order.amount",
    "--corrupt", "case_whitespace=0.2@order.status",
    "--corrupt", "pii_fill=0.3@customer.notes",
]  # fmt: skip


def test_chaos_writes_corrupted_tables_and_a_ground_truth_log(
    capsys, schema_file, tmp_path
) -> None:
    code, out, err = run(
        capsys,
        "chaos",
        schema_file,
        "-o",
        tmp_path,
        "--seed",
        5,
        "--scale",
        "small",
        *CHAOS,
        "--json",
    )
    assert code == 0, err
    result = json.loads(out)
    header, changes = read_ground_truth(result["ground_truth"])
    assert header["seed"] == 5 and result["changes"] == len(changes) == header["changes"]
    orders = pacsv.read_csv(tmp_path / "order.csv")
    assert orders.num_rows == 21  # 20 + 1 duplicate (5%)
    # score a quality check against the truth: find the orphans the log says exist
    parents = set(pacsv.read_csv(tmp_path / "customer.csv")["customer_id"].to_pylist())
    found = {i for i, v in enumerate(orders["customer_id"].to_pylist()) if v not in parents}
    truth = {c["row"] for c in changes if c["kind"] == "orphan_keys"}
    assert found == truth and truth
    # the duplicate the log names is a copy of its source row
    dup = next(c for c in changes if c["kind"] == "duplicates")
    assert orders.slice(dup["row"], 1).to_pylist() == orders.slice(dup["source_row"], 1).to_pylist()
    # PII in a free-text column
    notes = pacsv.read_csv(tmp_path / "customer.csv")["notes"].to_pylist()
    ssn = {i for i, v in enumerate(notes) if v and v[:3].isdigit() and v[3] == "-"}
    assert ssn == {c["row"] for c in changes if c["kind"] == "pii_fill"}


def test_chaos_is_deterministic_to_the_byte(capsys, schema_file, tmp_path) -> None:
    for name in ("a", "b"):
        assert run(capsys, "chaos", schema_file, "-o", tmp_path / name, "--seed", 5, *CHAOS)[0] == 0
    assert tree(tmp_path / "a") == tree(tmp_path / "b")
    assert run(capsys, "chaos", schema_file, "-o", tmp_path / "c", "--seed", 6, *CHAOS)[0] == 0
    assert tree(tmp_path / "c") != tree(tmp_path / "a")


def test_the_daily_pipeline_generate_corrupt_land(capsys, schema_file, tmp_path) -> None:
    """The issue's use case: one day's batch, corrupted, landed as a source drop, with its log."""
    flat = tmp_path / "flat"
    code, _, err = run(
        capsys, "continue", schema_file, "--daily-rows", "customer=30", "--daily-rows", "order=80",
        "--start-date", "2026-08-01", "--batch-date", "2026-08-04", "--path-template",
        "{table}.{ext}", "-o", flat,
    )  # fmt: skip
    assert code == 0, err
    landing = tmp_path / "landing"
    code, _, err = run(
        capsys, "chaos", schema_file, "--input", flat, "-o", landing, "--seed", 5, "--start-date",
        "2026-08-01", "--batch-date", "2026-08-04", "--format", "parquet", "--table-format",
        "customer=csv", "--corrupt", "null_creep=0.02@order.status:step=0.05",
        "--corrupt", "duplicates=0.05@order", "--corrupt", "type_change=1@order.amount",
    )  # fmt: skip
    assert code == 0, err
    orders = pq.read_table(landing / "order/ingest_date=2026-08-04/order_20260804.parquet")
    assert orders.num_rows == 84 and str(orders.schema.field("amount").type) == "string"
    header, changes = read_ground_truth(landing / "_chaos_ground_truth_20260804.jsonl")
    assert header["batch"] == 3  # derived from the dates
    # null creep at batch 3: 2% + 3 x 5% = 17% of 84 rows
    assert (
        orders["status"].null_count
        == round(84 * 0.17)
        == len([c for c in changes if c["kind"] == "null_creep"])
    )


def test_chaos_errors(capsys, schema_file, tmp_path) -> None:
    code, _, err = run(capsys, "chaos", schema_file, "-o", tmp_path)
    assert code == 2 and "corruption" in err
    code, _, err = run(capsys, "chaos", "-o", tmp_path, "--corrupt", "duplicates")
    assert code == 2 and "--input" in err
    code, _, err = run(capsys, "chaos", schema_file, "-o", tmp_path, "--corrupt", "shred=1")
    assert code == 2 and "unknown corruption" in err
    code, _, err = run(capsys, "chaos", schema_file, "-o", tmp_path, "--corrupt", "duplicates@nope")
    assert code == 2 and "nope" in err
