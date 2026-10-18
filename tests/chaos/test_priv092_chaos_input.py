"""PRIV-092: regression tests for the chaos --input fix (old #722)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.chaos.test_input_check import generate, run
from tests.chaos.test_input_check import schema_file as schema_file  # a fixture

# ---- #722: the output of one chaos run is the input of the next ----------------------------


@pytest.mark.parametrize("fmt", ["csv", "jsonl", "parquet"])
def test_a_folder_holding_a_ground_truth_log_is_read_again(
    capsys: Any, tmp_path: Path, schema_file: Path, fmt: str
) -> None:
    src, first, second = tmp_path / "gen", tmp_path / "chaos1", tmp_path / "chaos2"
    generate(capsys, schema_file, src, fmt)
    code, _, err = run(
        capsys, "chaos", "--input", src, "-o", first, "-f", fmt, "--corrupt",
        "type_change=1@order.customer_id", "--corrupt", "duplicates=0.05",
    )  # fmt: skip
    assert code == 0, err
    logs = list(first.glob("_chaos_ground_truth*.jsonl"))
    assert [p.name for p in logs] == ["_chaos_ground_truth.jsonl"]
    code, _, err = run(
        capsys, "chaos", "--input", first, "-o", second, "-f", fmt, "--corrupt", "duplicates=0.1"
    )
    assert code == 0, err
    tables = {p.stem for p in second.iterdir() if not p.name.startswith("_")}
    assert tables == {"customer", "order", "order_line"}


def test_the_log_is_not_read_as_a_table(capsys: Any, tmp_path: Path, schema_file: Path) -> None:
    from shape.cli.incremental import read_tables

    src, out = tmp_path / "gen", tmp_path / "chaos"
    generate(capsys, schema_file, src, "jsonl")
    code, _, err = run(capsys, "chaos", "--input", src, "-o", out, "--corrupt", "duplicates=0.05")
    assert code == 0, err
    assert set(read_tables(out)) == {"customer", "order", "order_line"}


def test_a_dated_log_is_not_read_as_a_table(tmp_path: Path) -> None:
    from shape.cli.incremental import read_tables

    (tmp_path / "t.jsonl").write_text('{"a": 1}\n')
    (tmp_path / "_chaos_ground_truth_20260105.jsonl").write_text('{"after": 1}\n{"after": "x"}\n')
    assert set(read_tables(tmp_path)) == {"t"}


def test_a_file_that_cannot_be_read_is_named(tmp_path: Path) -> None:
    from shape.cli.incremental import read_tables

    (tmp_path / "good.jsonl").write_text('{"a": 1}\n')
    (tmp_path / "bad.jsonl").write_text('{"a": 1}\n{"a": [1]}\n')
    with pytest.raises(ValueError, match=r"bad\.jsonl"):
        read_tables(tmp_path)
