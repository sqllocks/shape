"""P4-08 commands: ``shape learn``, ``shape generate --from`` and ``shape plan``."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyarrow import csv

from shape.cli.main import main


def run(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def sales(tmp_path: Path) -> Path:
    rng = np.random.default_rng(1)
    n = 3000
    table = pa.table(
        {
            "sale_id": np.arange(1, n + 1),
            "region": rng.choice(["north", "south", "east"], n, p=[0.5, 0.3, 0.2]),
            "amount": pa.array(np.round(rng.gamma(2.0, 30.0, n), 2), mask=rng.random(n) < 0.05),
            "qty": rng.integers(1, 9, n),
        }
    )
    path = tmp_path / "sales.csv"
    csv.write_csv(table, str(path))
    return path


@pytest.fixture
def sales_profile(sales: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    out = tmp_path / "sales.shape"
    assert run(capsys, "profile", sales, "-o", out)[0] == 0
    return out


# ---- learn ----------------------------------------------------------------------------------


def test_learn_writes_a_schema_that_generates(
    sales: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = tmp_path / "sales.schema.json"
    code, out, _ = run(capsys, "learn", sales, "-o", schema)
    assert code == 0
    assert "Schema written to" in out and "sales: 4 columns (key: sale_id)" in out
    doc = json.loads(schema.read_text())
    assert doc["tables"]["sales"]["primary_key"] == ["sale_id"]
    assert doc["generation"]["scales"]["small"] == {"sales": 3000}
    assert run(capsys, "validate", schema)[0] == 0
    target = tmp_path / "out"
    assert run(capsys, "generate", schema, "-f", "csv", "-o", target)[0] == 0
    assert (target / "sales.csv").exists()


def test_learn_defaults_the_output_path_and_prints_json(
    sales: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "learn", sales, "--json", "--domain", "shop")
    assert code == 0
    summary = json.loads(out)
    assert summary["domain"] == "shop" and summary["tables"]["sales"]["columns"] == 4
    assert Path(summary["output"]) == sales.with_suffix(".schema.json")
    assert json.loads(sales.with_suffix(".schema.json").read_text())["model"]["domain"] == "shop"


def test_learn_a_directory_is_one_table_per_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    csv.write_csv(
        pa.table({"customer_id": np.arange(1, 101), "name": ["x"] * 100}),
        str(data / "customer.csv"),
    )
    csv.write_csv(
        pa.table({"order_id": np.arange(1, 501), "customer_id": 1 + np.arange(500) % 100}),
        str(data / "orders.csv"),
    )
    code, out, _ = run(capsys, "learn", data, "-o", tmp_path / "s.json")
    assert code == 0 and "2 tables, 1 relationships" in out and "1 foreign keys" in out


def test_learn_formats_and_errors(
    sales: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parquet = tmp_path / "sales.parquet"
    pq.write_table(csv.read_csv(str(sales)), str(parquet))
    assert run(capsys, "learn", parquet, "-o", tmp_path / "p.json")[0] == 0
    assert run(capsys, "learn", sales, "--format", "parquet")[0] == 2  # a csv file is not parquet
    assert run(capsys, "learn", tmp_path / "missing.csv")[0] == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(capsys, "learn", empty)[0] == 2


# ---- generate --from ------------------------------------------------------------------------


def test_generate_from_a_profile_writes_the_profiles_row_count(
    sales_profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "gen"
    code, out, err = run(
        capsys, "generate", "--from", sales_profile, "-f", "parquet", "-o", target, "--seed", 3
    )
    assert code == 0, err
    assert "3,000 rows" in out and "profile fit:" in err
    table = pq.read_table(target / "sales.parquet")
    assert table.num_rows == 3000 and table.column_names == ["sale_id", "region", "amount", "qty"]
    assert table["amount"].null_count > 0 and table["qty"].type == pa.int64()
    again = tmp_path / "gen2"
    run(capsys, "generate", "--from", sales_profile, "-f", "parquet", "-o", again, "--seed", 3)
    assert pq.read_table(again / "sales.parquet").equals(table)  # seeded


def test_generate_from_rows_dry_run_and_errors(
    sales_profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "generate", "--from", sales_profile, "--rows", 500, "--dry-run")
    assert code == 0 and "500" in out
    code, out, _ = run(capsys, "generate", "--from", sales_profile, "--json")
    assert code == 0 and json.loads(out)["counts"] == {"sales": 3000}
    assert run(capsys, "generate", "retail", "--from", sales_profile)[0] == 2
    assert run(capsys, "generate", "--from", sales_profile, "--scale", "nope")[0] == 2
    assert run(capsys, "generate", "--from", tmp_path / "sales.json")[0] == 2


# ---- plan -----------------------------------------------------------------------------------


def test_plan_lists_every_field_and_filters_by_status(
    sales_profile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "plan", sales_profile)
    assert code == 0
    plan = json.loads(out)
    assert plan["executable"] is True
    evidence = {i["evidence"] for i in plan["items"]}
    assert {
        "sales.amount.null_rate",
        "sales.region.value_counts_ext",
        "sales.qty.dtype",
    } <= evidence
    assert {"dataset.missingness_joint", "sales.row_count"} <= evidence
    code, out, _ = run(capsys, "plan", sales_profile, "--status", "not_modelled")
    flagged = json.loads(out)["items"]
    assert flagged and {i["status"] for i in flagged} == {"not_modelled"}
    assert "dataset.missingness_joint" in {i["evidence"] for i in flagged}
    code, out, _ = run(capsys, "plan", sales_profile, "--rows", 10)
    statuses = {i["evidence"]: i["status"] for i in json.loads(out)["items"]}
    assert statuses["sales.sale_id.cardinality"] == "approximate"


def test_plan_still_reads_a_captured_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps({"columns": {"name": {"kind": "text"}, "x": {"kind": "numeric", "mean": 1}}})
    )
    code, out, _ = run(capsys, "plan", evidence)
    assert code == 0
    items = {i["evidence"]: i["status"] for i in json.loads(out)["items"]}
    assert items == {"column:name": "not_modelled", "column:x": "approximate"}
