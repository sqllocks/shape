"""W5-02: the ``shape design`` command."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from shape.cli.main import main


def run(capsys: pytest.CaptureFixture[str], *argv: Any) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def design_file(tmp_path: Path, doc: dict[str, Any]) -> Path:
    p = tmp_path / "retail.design.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_design_writes_ddl_to_a_file(
    capsys: pytest.CaptureFixture[str], design_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "star.sql"
    result = tmp_path / "star.json"
    code, _, _ = run(
        capsys,
        "design",
        design_file,
        "--mode",
        "star",
        "--dialect",
        "postgres",
        "-o",
        out,
        "--json",
        result,
    )
    assert code == 0
    assert 'CREATE TABLE "fact_sales"' in out.read_text(encoding="utf-8")
    assert json.loads(result.read_text(encoding="utf-8"))["mode"] == "star"


def test_design_prints_ddl_by_default(
    capsys: pytest.CaptureFixture[str], design_file: Path
) -> None:
    code, out, _ = run(capsys, "design", design_file)
    assert code == 0 and "CREATE TABLE [customer_city]" in out  # 3nf, tsql


def test_lint_only(capsys: pytest.CaptureFixture[str], design_file: Path) -> None:
    code, out, _ = run(capsys, "design", design_file, "--mode", "star", "--lint")
    assert code == 0 and json.loads(out) == []


def test_lint_errors_exit_one_and_no_ddl(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, doc: dict[str, Any]
) -> None:
    del doc["facts"][0]["grain"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    code, out, err = run(capsys, "design", p, "--mode", "star")
    assert code == 1 and "CREATE TABLE" not in out and "D001" in err
    code, out, _ = run(capsys, "design", p, "--mode", "star", "--lint")
    assert code == 1 and json.loads(out)[0]["code"] == "D001"


def test_strict_makes_warnings_fail(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, doc: dict[str, Any]
) -> None:
    del doc["facts"][0]["measures"][0]["additivity"]
    p = tmp_path / "warn.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert run(capsys, "design", p, "--mode", "star", "--lint")[0] == 0
    assert run(capsys, "design", p, "--mode", "star", "--lint", "--strict")[0] == 1


def test_bad_input_exits_two(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    p = tmp_path / "nope.json"
    p.write_text("{}", encoding="utf-8")
    code, _, err = run(capsys, "design", p)
    assert code == 2 and "format" in err
    assert run(capsys, "design", tmp_path / "missing.json")[0] == 2


def test_from_data_builds_a_design_input(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    data = tmp_path / "orders.csv"
    with data.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["order_id", "city", "region"])
        for i in range(40):
            city = ["Oslo", "Bergen", "Lyon"][i % 3]
            w.writerow([i, city, {"Oslo": "N", "Bergen": "W", "Lyon": "S"}[city]])
    out = tmp_path / "orders.design.json"
    code, _, _ = run(capsys, "design", data, "--from-data", "--name", "orders", "-o", out)
    assert code == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["format"] == "shape-design" and doc["entities"][0]["keys"] == [["order_id"]]
    code, sql, _ = run(capsys, "design", out, "--dialect", "mysql")
    assert code == 0 and "CREATE TABLE `orders_city`" in sql


def test_output_is_identical_across_runs(
    capsys: pytest.CaptureFixture[str], design_file: Path
) -> None:
    a = run(capsys, "design", design_file, "--mode", "snowflake")[1]
    b = run(capsys, "design", design_file, "--mode", "snowflake")[1]
    assert a == b and a
