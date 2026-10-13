"""Recorded database profiles keep every T-22 field gate and fail closed."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

from .test_profile_verify_recorded import profile


def verifier():
    path = Path(__file__).resolve().parents[2] / "benchmarks/vs_refengine/db_1to1/verify.py"
    spec = importlib.util.spec_from_file_location("frz_db_verify", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("case", ["retail", "warehouse_empty", "retail_one_row_sample"])
@pytest.mark.parametrize("real", [False, True])
@pytest.mark.parametrize("damage", ["equal", "mean", "null_count", "column", "missing", "tamper"])
def test_database_recorded_verdict(case, real, damage, monkeypatch, capsys):
    v = verifier()
    base = {"tables": {"t": profile(v.profile_verify)}, "relationships": []}
    candidate = copy.deepcopy(base)
    col = candidate["tables"]["t"]["columns"]["x"]
    if damage == "mean":
        col["mean"] *= 1 + 1e-6
    if damage == "null_count":
        col["null_count"] += 1
    if damage == "column":
        candidate["tables"]["t"]["columns"] = {}
    calls = []

    def recording(identifier, mssql):
        calls.append((identifier, mssql))
        if damage in ("missing", "tamper"):
            raise v.RecordingError(f"db:{identifier}: {damage} recording")
        return base

    monkeypatch.setattr(v, "baseline_profile", recording)
    monkeypatch.setattr(v, "shape_side", lambda *args: candidate)
    monkeypatch.setattr(v, "load_real", lambda *args: None)
    monkeypatch.setattr(v, "drop_real", lambda *args: None)
    if real:
        # Cleanup is isolated from the emulator while exercising the real-case selection.
        monkeypatch.setattr(v, "cleanup_real", lambda *args: None, raising=False)
    args = ["--case", case] + (["--mssql", "stub"] if real else [])
    assert v.main(args) == (2 if damage in ("missing", "tamper") else int(damage != "equal"))
    assert calls == [(case, real)]
    output = capsys.readouterr()
    assert case in output.out + output.err


@pytest.mark.parametrize("damage", ["equal", "missing", "tamper"])
@pytest.mark.parametrize("real", [False, True])
def test_recorded_verdict_needs_no_odbc_driver(monkeypatch, capsys, damage, real):
    monkeypatch.setitem(sys.modules, "pyodbc", None)
    test_database_recorded_verdict("retail", real, damage, monkeypatch, capsys)
    assert sys.modules["pyodbc"] is None


def test_driver_is_imported_only_when_connecting(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyodbc", None)
    v = verifier()
    with pytest.raises(ModuleNotFoundError, match="pyodbc"):
        v.connect_real("stub")
