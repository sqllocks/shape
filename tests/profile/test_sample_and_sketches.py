"""INT-18: W2-07's sampling and W2-01's sketch state. The sketch state reads every row, so a
sampled profile cannot carry it: merged, the two would describe different rows."""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape


def test_sketches_and_sample_are_refused_together() -> None:
    table = pa.table({"x": list(range(1000))})
    with pytest.raises(ValueError, match="sketches and sample cannot be combined"):
        shape.profile(table, sketches=True, sample=100)
    assert shape.profile(table, sample=100).sketches is None
    assert shape.profile(table, sketches=True).sketches is not None


def test_cli_sketches_with_sample_exits_2(tmp_path, monkeypatch, capsys) -> None:
    from shape.cli.main import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "d.csv").write_text("x\n" + "\n".join(str(i) for i in range(100)) + "\n")
    args = ["profile", "d.csv", "-o", "p.shape", "--sketches", "--capture", "full"]
    assert main([*args, "--sample", "10"]) == 2
    assert "sketches and sample" in capsys.readouterr().err
    assert not (tmp_path / "p.shape").exists()
