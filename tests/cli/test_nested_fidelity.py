"""``shape fidelity`` compares flat columns: a nested column is refused by name (issue #1)."""

from __future__ import annotations

from pathlib import Path

import nested_inputs as ni
import pytest

from shape.cli.main import main


@pytest.mark.parametrize("extra", [[], ["--tier", "1"]], ids=["scores", "tier"])
def test_fidelity_refuses_a_nested_column_by_name(
    extra: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = ni.write_parquet(tmp_path / "a.parquet", ni.table_with("li"))
    b = ni.write_parquet(tmp_path / "b.parquet", ni.table_with("li"))
    code = main(["fidelity", str(a), str(b), *extra])
    err = capsys.readouterr().err
    assert code == 2
    lines = [x for x in err.splitlines() if x.startswith("shape: error:")]
    assert len(lines) == 1, err
    assert "a.li is a nested column" in lines[0] or "li" in lines[0]
    assert "nested" in lines[0] and "flat" in lines[0]
    assert "kernel" not in lines[0] and "unhashable" not in lines[0]


def test_fidelity_still_scores_flat_tables(tmp_path: Path) -> None:
    a = ni.write_parquet(tmp_path / "a.parquet", ni.flat_table())
    b = ni.write_parquet(tmp_path / "b.parquet", ni.flat_table())
    assert main(["fidelity", str(a), str(b)]) == 0
