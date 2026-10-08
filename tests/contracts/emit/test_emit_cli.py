"""W5-04 item 2: ``shape contract emit``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shape.cli.main import main


def run(capsys: pytest.CaptureFixture[str], *argv: Any) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize(
    ("target", "needle"),
    [
        ("ddl", "CREATE TABLE [orders]"),
        ("jsonschema", "draft/2020-12"),
        ("pandera", "DataFrameSchema"),
        ("gx", "expectations"),
    ],
)
def test_each_target_is_written(
    capsys: pytest.CaptureFixture[str], orders_path: Path, tmp_path: Path, target: str, needle: str
) -> None:
    out = tmp_path / "out.txt"
    extra = ["--table", "orders"]
    code, stdout, _ = run(
        capsys, "contract", "emit", orders_path, "--to", target, "-o", out, *extra
    )
    assert code == 0
    assert needle in out.read_text(encoding="utf-8")
    assert stdout == ""


def test_the_table_name_defaults_to_the_file_name(
    capsys: pytest.CaptureFixture[str], orders_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o.sql"
    assert run(capsys, "contract", "emit", orders_path, "--to", "ddl", "-o", out)[0] == 0
    assert "CREATE TABLE [orders]" in out.read_text(encoding="utf-8")


def test_dialect(capsys: pytest.CaptureFixture[str], orders_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "o.sql"
    code, _, _ = run(
        capsys, "contract", "emit", orders_path, "--to", "ddl", "--dialect", "postgres", "-o", out
    )
    assert code == 0 and 'CREATE TABLE "orders"' in out.read_text(encoding="utf-8")
    code, _, err = run(
        capsys, "contract", "emit", orders_path, "--to", "ddl", "--dialect", "oracle", "-o", out
    )
    assert code == 2 and "oracle" in err


def test_dialect_with_another_target_is_exit_2(
    capsys: pytest.CaptureFixture[str], orders_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o.json"
    code, _, err = run(
        capsys, "contract", "emit", orders_path, "--to", "gx", "--dialect", "tsql", "-o", out
    )
    assert code == 2 and "dialect" in err and not out.exists()


def test_unknown_target_is_exit_2(
    capsys: pytest.CaptureFixture[str], orders_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o"
    code, _, _ = run(capsys, "contract", "emit", orders_path, "--to", "xml", "-o", out)
    assert code == 2 and not out.exists()


def test_strict_fails_with_the_list_and_writes_nothing(
    capsys: pytest.CaptureFixture[str], orders_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o.json"
    code, _, err = run(
        capsys, "contract", "emit", orders_path, "--to", "jsonschema", "-o", out, "--strict"
    )
    assert code == 1 and not out.exists()
    assert "unique" in err and "order_id" in err and "row_count" in err


def test_strict_passes_when_everything_is_expressible(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    c = tmp_path / "c.contract.json"
    c.write_text(json.dumps({"columns": {"a": {"dtype": "integer", "min": 0}}}), encoding="utf-8")
    out = tmp_path / "o.json"
    code, _, _ = run(capsys, "contract", "emit", c, "--to", "jsonschema", "-o", out, "--strict")
    assert code == 0 and out.exists()


def test_json_prints_the_result(
    capsys: pytest.CaptureFixture[str], orders_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o.json"
    code, stdout, _ = run(
        capsys, "contract", "emit", orders_path, "--to", "gx", "-o", out, "--json", "--table", "t"
    )
    assert code == 0
    envelope = json.loads(stdout)  # W1-14: the shape-result document, the result under payload
    assert envelope["format"] == "shape-result" and envelope["command"] == "contract emit"
    doc = envelope["payload"]
    assert doc["format"] == "shape-contract-emit" and doc["version"] == 1
    assert doc["target"] == "gx" and doc["text"] == out.read_text(encoding="utf-8")
    assert doc["not_expressed"] and set(doc["not_expressed"][0]) == {
        "table",
        "column",
        "rule",
        "reason",
    }


def test_json_without_an_output_file_writes_nothing_else(
    capsys: pytest.CaptureFixture[str], orders_path: Path
) -> None:
    code, stdout, _ = run(capsys, "contract", "emit", orders_path, "--to", "ddl", "--json")
    assert code == 0 and json.loads(stdout)["target"] == "ddl"


def test_no_output_file_prints_the_text(
    capsys: pytest.CaptureFixture[str], orders_path: Path
) -> None:
    code, stdout, _ = run(capsys, "contract", "emit", orders_path, "--to", "pandera")
    assert code == 0 and stdout.startswith("# ") and "DataFrameSchema" in stdout


def test_a_malformed_contract_is_exit_2_with_the_contract_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"columns": {"a": {"mystery": 1}}}), encoding="utf-8")
    out = tmp_path / "o"
    code, _, err = run(capsys, "contract", "emit", bad, "--to", "ddl", "-o", out)
    assert code == 2 and "unknown rules for column 'a'" in err and not out.exists()
    bad.write_text("{nope", encoding="utf-8")
    code, _, err = run(capsys, "contract", "emit", bad, "--to", "ddl", "-o", out)
    assert code == 2 and "not valid JSON" in err
    code, _, err = run(capsys, "contract", "emit", tmp_path / "missing.json", "--to", "ddl")
    assert code == 2 and "missing.json" in err


def test_table_must_exist_and_is_needed_for_one_document_targets(
    capsys: pytest.CaptureFixture[str], two_tables: dict[str, Any], tmp_path: Path
) -> None:
    c = tmp_path / "multi.contract.json"
    c.write_text(json.dumps(two_tables), encoding="utf-8")
    out = tmp_path / "o"
    assert run(capsys, "contract", "emit", c, "--to", "gx", "-o", out)[0] == 2
    assert run(capsys, "contract", "emit", c, "--to", "ddl", "--table", "nope", "-o", out)[0] == 2
    assert not out.exists()
    assert (
        run(capsys, "contract", "emit", c, "--to", "gx", "--table", "customers", "-o", out)[0] == 0
    )
