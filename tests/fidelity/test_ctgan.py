"""The optional CTGAN command: a contract test with ``sdv`` mocked, and graceful degradation."""

from __future__ import annotations

import sys
import types
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.builtins.ctgan import INSTALL_HINT, CtganCommand, CtganModel
from shape.cli.main import main
from shape.plugins import kit

CALLS: dict[str, Any] = {}


class _Metadata:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []

    def detect_from_dataframe(self, df: pd.DataFrame) -> None:
        CALLS["detected"] = list(df.columns)

    def update_column(self, column_name: str, sdtype: str) -> None:
        self.updated.append((column_name, sdtype))


class _Synth:
    def __init__(self, metadata: _Metadata, epochs: int, batch_size: int) -> None:
        CALLS["init"] = {"metadata": metadata, "epochs": epochs, "batch_size": batch_size}
        self.frame: pd.DataFrame | None = None

    def fit(self, df: pd.DataFrame) -> None:
        self.frame = df
        CALLS["fit_rows"] = len(df)

    def sample(self, num_rows: int) -> pd.DataFrame:
        assert self.frame is not None
        return self.frame.sample(num_rows, replace=True, random_state=0).reset_index(drop=True)


@pytest.fixture
def fake_sdv(monkeypatch: pytest.MonkeyPatch) -> None:
    CALLS.clear()
    sdv = types.ModuleType("sdv")
    meta = types.ModuleType("sdv.metadata")
    single = types.ModuleType("sdv.single_table")
    meta.SingleTableMetadata = _Metadata  # type: ignore[attr-defined]
    single.CTGANSynthesizer = _Synth  # type: ignore[attr-defined]
    sdv.metadata, sdv.single_table = meta, single  # type: ignore[attr-defined]
    for name, mod in (("sdv", sdv), ("sdv.metadata", meta), ("sdv.single_table", single)):
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def no_sdv(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [m for m in sys.modules if m == "sdv" or m.startswith("sdv.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "sdv", None)


TABLE = pa.table(
    {"age": [30, 41, 25, 60], "city": ["a", "b", "a", "c"], "score": [1.5, 2.5, 3.5, 4.5]}
)


def test_fit_and_sample_through_sdv(fake_sdv):
    assert CtganModel.is_available()
    model = CtganModel(epochs=7, batch_size=20).fit(TABLE)
    assert CALLS["init"]["epochs"] == 7 and CALLS["init"]["batch_size"] == 20
    assert CALLS["detected"] == ["age", "city", "score"] and CALLS["fit_rows"] == 4
    assert CALLS["init"]["metadata"].updated == [
        ("city", "categorical")
    ]  # non-numbers are discrete
    out = model.sample(10)
    assert out.num_rows == 10 and out.column_names == ["age", "city", "score"]
    assert set(out["city"].to_pylist()) <= {"a", "b", "c"}


def test_discrete_columns_replace_the_default(fake_sdv):
    CtganModel().fit(TABLE, discrete_columns=["age"])
    assert CALLS["init"]["metadata"].updated == [("age", "categorical")]
    with pytest.raises(ValueError, match="nope"):
        CtganModel().fit(TABLE, discrete_columns=["nope"])


def test_sampling_before_fitting_is_an_error(fake_sdv):
    with pytest.raises(RuntimeError, match="fitted"):
        CtganModel().sample(1)


def test_without_sdv_it_degrades_gracefully(no_sdv, tmp_path, capsys):
    assert not CtganModel.is_available()
    with pytest.raises(ImportError, match=r"sqllocks-shape\[ctgan\]"):
        CtganModel().fit(TABLE)
    path = tmp_path / "t.parquet"
    pq.write_table(TABLE, path)
    rc = main(["ctgan", str(path), "-o", str(tmp_path / "o.parquet")])
    assert rc == 2 and INSTALL_HINT in capsys.readouterr().err
    assert not (tmp_path / "o.parquet").exists()


def test_the_command_writes_parquet(fake_sdv, tmp_path):
    src, out = tmp_path / "people.parquet", tmp_path / "synthetic.parquet"
    pq.write_table(TABLE, src)
    rc = main(
        ["ctgan", str(src), "-n", "25", "-o", str(out), "--epochs", "3", "--discrete", "city"]
    )
    assert rc == 0 and pq.read_table(out).num_rows == 25 and CALLS["init"]["epochs"] == 3
    assert CALLS["init"]["metadata"].updated == [("city", "categorical")]


def test_the_command_wants_one_table(fake_sdv, tmp_path, capsys):
    d = tmp_path / "d"
    d.mkdir()
    pq.write_table(TABLE, d / "a.parquet")
    pq.write_table(TABLE, d / "b.parquet")
    assert main(["ctgan", str(d), "-o", str(tmp_path / "o.parquet")]) == 2
    assert "one table" in capsys.readouterr().err


def test_it_conforms_to_the_command_protocol_and_is_a_registered_builtin(no_sdv):
    kit.check_command(CtganCommand(), ["x.csv", "-o", "o.parquet"], expect_exit=2)
    from shape.plugins.host import default_host

    assert default_host().get("shape.commands", "ctgan").name == "ctgan"
