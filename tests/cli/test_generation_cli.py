"""P4-10: an end-to-end test per generation command, for retail (needs the shape-domains plugin)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

pytest.importorskip("shape_domains")

RETAIL_TABLES = {
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


def test_list_shows_retail(capsys):
    code, out, _ = run(capsys, "list")
    assert code == 0
    assert "retail" in out
    code, out, _ = run(capsys, "list", "--json")
    assert code == 0
    assert {"name": "retail", "modes": ["3nf", "star"]} in json.loads(out)


def test_presets_for_retail(capsys):
    code, out, _ = run(capsys, "presets", "retail", "--json")
    assert code == 0
    counts = json.loads(out)
    assert {"small", "medium", "large", "xlarge"} <= set(counts)
    assert sum(counts["medium"].values()) == 1_965_400
    code, out, _ = run(capsys, "presets", "retail")
    assert code == 0 and "medium" in out and "order_line" in out
    code, out, _ = run(capsys, "presets")
    assert code == 0 and out.startswith("retail")


def test_describe_retail(capsys):
    code, out, _ = run(capsys, "describe", "retail", "--json", "--scale", "medium")
    assert code == 0
    d = json.loads(out)
    assert set(d["tables"]) == RETAIL_TABLES
    assert d["tables"]["order_line"]["rows"] == 1_250_000
    assert d["mode"] == "3nf"
    code, out, _ = run(capsys, "describe", "retail")
    assert code == 0 and "order_line" in out and "relationships" in out


def test_generate_dry_run_generates_nothing(capsys, tmp_path):
    code, out, _ = run(
        capsys,
        "generate",
        "retail",
        "--scale",
        "small",
        "--dry-run",
        "-f",
        "parquet",
        "-o",
        tmp_path,
    )
    assert code == 0
    assert "nothing was generated" in out
    assert list(tmp_path.iterdir()) == []
    code, out, _ = run(capsys, "generate", "retail", "--dry-run", "--json")
    assert json.loads(out)["ok"] is True


def test_generate_summary(capsys):
    code, out, _ = run(capsys, "generate", "retail", "--scale", "small", "--seed", "7")
    assert code == 0
    assert "Seed:   7" in out and "order_line" in out and "TOTAL" in out


@pytest.mark.parametrize(
    ("fmt", "suffix"),
    [("csv", "csv"), ("tsv", "tsv"), ("jsonl", "jsonl"), ("parquet", "parquet"), ("sql", "sql")],
)
def test_generate_formats(capsys, tmp_path, fmt, suffix):
    code, out, _ = run(
        capsys, "generate", "retail", "--scale", "small", "-f", fmt, "-o", tmp_path, "--json"
    )
    assert code == 0, out
    files = {p.stem for p in tmp_path.glob(f"*.{suffix}")}
    assert files == RETAIL_TABLES
    counts = json.loads(out)["counts"]
    if fmt == "parquet":
        for name in RETAIL_TABLES:
            assert pq.read_metadata(tmp_path / f"{name}.parquet").num_rows == counts[name]


def test_generate_is_deterministic_and_seed_changes_it(capsys, tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d, seed in ((a, 5), (b, 5), (c, 6)):
        code, *_ = run(
            capsys,
            "generate",
            "retail",
            "--scale",
            "small",
            "--seed",
            seed,
            "-f",
            "parquet",
            "-o",
            d,
        )
        assert code == 0
    assert pq.read_table(a / "customer.parquet").equals(pq.read_table(b / "customer.parquet"))
    assert not pq.read_table(a / "customer.parquet").equals(pq.read_table(c / "customer.parquet"))


def test_generate_sql_options(capsys, tmp_path):
    code, *_ = run(
        capsys,
        "generate",
        "retail",
        "--scale",
        "small",
        "-f",
        "sql",
        "-o",
        tmp_path,
        "--sql-dialect",
        "postgres",
        "--schema-name",
        "shop",
        "--no-sql-drop",
    )
    assert code == 0
    text = (tmp_path / "customer.sql").read_text()
    assert '"shop"."customer"' in text
    assert "DROP TABLE" not in text
    assert "\nGO" not in text


def test_generate_excel(capsys, tmp_path):
    pytest.importorskip("openpyxl")
    code, *_ = run(capsys, "generate", "retail", "--scale", "small", "-f", "excel", "-o", tmp_path)
    assert code == 0
    assert {p.stem for p in tmp_path.glob("*.xlsx")} == RETAIL_TABLES


def test_generate_delta(capsys, tmp_path):
    deltalake = pytest.importorskip("deltalake")
    code, *_ = run(capsys, "generate", "retail", "--scale", "small", "-f", "delta", "-o", tmp_path)
    assert code == 0
    assert deltalake.DeltaTable(str(tmp_path / "customer")).to_pyarrow_table().num_rows == 1000


def test_generate_needs_an_output_directory(capsys):
    code, _, err = run(capsys, "generate", "retail", "-f", "csv")
    assert code == 2
    assert "-o DIR" in err


def test_unknown_domain_scale_and_mode_exit_2(capsys):
    assert run(capsys, "generate", "nope")[0] == 2
    code, _, err = run(capsys, "generate", "retail", "--scale", "huge")
    assert code == 2 and "small" in err
    assert run(capsys, "describe", "nope")[0] == 2


def test_star_mode(capsys, tmp_path):
    code, out, _ = run(capsys, "describe", "retail", "--mode", "star", "--json")
    assert code == 0
    assert json.loads(out)["mode"] == "star"
    code, *_ = run(
        capsys,
        "generate",
        "retail",
        "--mode",
        "star",
        "--scale",
        "small",
        "-f",
        "parquet",
        "-o",
        tmp_path,
    )
    assert code == 0
    assert any(tmp_path.glob("*.parquet"))


def test_generate_from_a_missing_profile_exits_2(capsys):
    code, _, err = run(capsys, "generate", "--from", "x.shape")
    assert code == 2
    assert "x.shape" in err


def test_demo_rows_still_work(capsys):
    code, out, _ = run(capsys, "generate", "--rows", "3", "--seed", "1")
    assert code == 0
    assert len(out.strip().splitlines()) == 3


def test_from_ddl_then_generate_describe_validate(capsys, tmp_path):
    ddl = tmp_path / "shop.sql"
    ddl.write_text(
        "CREATE TABLE customer (customer_id INT PRIMARY KEY, email VARCHAR(80), "
        "created_at DATE);\n"
        "CREATE TABLE orders (order_id INT PRIMARY KEY, customer_id INT NOT NULL, "
        "total DECIMAL(10,2), FOREIGN KEY (customer_id) REFERENCES customer(customer_id));\n"
    )
    schema = tmp_path / "shop.gen.json"
    assert run(capsys, "from-ddl", ddl, "-o", schema)[0] == 0
    assert run(capsys, "validate", schema)[0] == 0
    code, out, _ = run(capsys, "describe", schema, "--json")
    assert code == 0 and set(json.loads(out)["tables"]) == {"customer", "orders"}
    out_dir = tmp_path / "out"
    code, *_ = run(capsys, "generate", schema, "-f", "csv", "-o", out_dir, "--seed", 3)
    assert code == 0
    assert (out_dir / "orders.csv").stat().st_size > 0


def test_python_api_returns_arrow_tables():
    import pyarrow as pa

    import shape.api as api

    result = api.generate("retail", scale="small", seed=3)
    assert set(result.tables) == RETAIL_TABLES
    assert isinstance(result["customer"], pa.Table)
    assert result["customer"].num_rows == 1000
    again = api.generate("retail", scale="small", seed=3)
    assert result["order"].equals(again["order"])
    star = api.generate("retail", scale="small", seed=3, mode="star")
    assert star.schema.model.schema_mode == "star"


def test_version_stays_light():
    """`shape version` must not import numpy, pyarrow or the engine (T-18)."""
    code = (
        "import sys; from shape.cli.main import main; main(['version']); "
        "bad = [m for m in ('numpy', 'pyarrow', 'shape.generation', 'shape.runlog') "
        "if m in sys.modules]; sys.exit(1 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_output_dir_is_created(capsys, tmp_path):
    out = Path(tmp_path) / "deep" / "er"
    assert run(capsys, "generate", "retail", "--scale", "small", "-f", "csv", "-o", out)[0] == 0
    assert out.is_dir()


def test_validate_dispatches_on_content(capsys, tmp_path):
    schema = tmp_path / "s.json"
    from shape.cli.generation import load_target

    schema.write_text(json.dumps(load_target("retail").to_dict()))
    code, out, _ = run(capsys, "validate", schema)
    assert code == 0
    result = json.loads(out)
    assert result["kind"] == "generation-schema" and result["valid"] and result["tables"] == 9

    broken = json.loads(schema.read_text())
    broken["tables"]["customer"]["primary_key"] = ["nope"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(broken))
    code, out, _ = run(capsys, "validate", bad)
    assert code == 1
    assert any("nope" in e for e in json.loads(out)["errors"])

    code, out, _ = run(capsys, "validate", "examples/customer.shape.json")
    assert code == 0 and json.loads(out)["kind"] == "contract"

    other = tmp_path / "other.json"
    other.write_text(json.dumps({"hello": "world"}))
    code, _, err = run(capsys, "validate", other)
    assert code == 2 and "neither" in err
    # a schema without schema_version is not a Shape generation schema
    legacy = {k: v for k, v in json.loads(schema.read_text()).items() if k != "schema_version"}
    other.write_text(json.dumps(legacy))
    assert run(capsys, "validate", other)[0] == 2
    assert run(capsys, "validate", tmp_path / "missing.json")[0] == 2


def test_log_json_and_metrics_for_any_command(capsys, tmp_path):
    metrics = tmp_path / "m.json"
    code = main(["--log-json", "--metrics", str(metrics), "generate", "retail", "--scale", "small"])
    err = capsys.readouterr().err
    assert code == 0
    records = [json.loads(line) for line in err.splitlines()]
    assert [r["message"] for r in records] == ["command started", "command finished"]
    assert records[1]["exit_code"] == 0 and records[1]["level"] == "INFO"
    summary = json.loads(metrics.read_text())
    assert summary["command"] == "generate" and summary["exit_code"] == 0
    assert summary["domain"] == "retail" and summary["rows"] > 0
    # another command, a failing one
    code = main(["--metrics", str(metrics), "list"])
    assert code == 0 and json.loads(metrics.read_text())["command"] == "list"
    code = main(["--metrics", str(metrics), "generate", "nope"])
    assert code == 2 and json.loads(metrics.read_text())["exit_code"] == 2


def test_run_metrics_collector():
    from shape.runlog import RunMetrics

    m = RunMetrics("r1")
    m.start_table("t")
    m.end_table("t", rows=5, columns=2)
    m.record_event("chaos_injected", count=3)
    s = m.finish()
    assert s["total_rows"] == 5 and s["tables"]["t"]["columns"] == 2
    assert s["events"][0]["type"] == "chaos_injected"
