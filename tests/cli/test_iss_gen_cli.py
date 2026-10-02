"""ISS-gen: CLI regressions from owner issues #25 and #26."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.main import main

DDL = """\
CREATE TABLE customer (customer_id INT PRIMARY KEY, email VARCHAR(100), segment VARCHAR(20), created_at DATETIME);
CREATE TABLE orders (order_id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customer(customer_id), total DECIMAL(10,2), status VARCHAR(20));
"""


@pytest.fixture
def ddl_schema(tmp_path: Path) -> Path:
    sql = tmp_path / "s.sql"
    sql.write_text(DDL, encoding="utf-8")
    out = tmp_path / "schema.json"
    assert main(["from-ddl", str(sql), "-o", str(out)]) == 0
    return out


def test_plan_reads_the_schema_from_ddl_writes(
    ddl_schema: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#26: ``shape plan`` crashed with ``AttributeError: 'list' object has no attribute 'get'``."""
    capsys.readouterr()
    assert main(["plan", str(ddl_schema)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total_rows"] > 0 and set(out["order"]) == {"customer", "orders"}


def test_unexpected_input_is_one_line_error_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#26: any unexpected failure is ``shape: error: ...`` with exit 2; ``--debug`` re-raises."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"columns": {"a": 1}, "relationships": []}), encoding="utf-8")
    assert main(["plan", str(bad)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("shape: error: ") and "Traceback" not in err
    with pytest.raises(Exception):  # noqa: B017 - whichever error the input provokes
        main(["--debug", "plan", str(bad)])


def test_generate_rows_override_per_table(
    ddl_schema: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#25: ``shape generate SCHEMA.json --rows TABLE=N --out DIR``."""
    import pyarrow.parquet as pq

    out = tmp_path / "out"
    code = main(
        [
            "generate",
            str(ddl_schema),
            "--rows",
            "customer=7",
            "--rows",
            "orders=11",
            "--format",
            "parquet",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert pq.read_metadata(out / "customer.parquet").num_rows == 7
    assert pq.read_metadata(out / "orders.parquet").num_rows == 11


def test_generate_rejects_bad_rows_forms(
    ddl_schema: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["generate", str(ddl_schema), "--rows", "5"]) == 2
    assert main(["generate", str(ddl_schema), "--rows", "ghost=5"]) == 2
    assert main(["generate", str(ddl_schema), "--rows", "customer=x"]) == 2
    err = capsys.readouterr().err
    assert err.count("shape: error:") == 3
