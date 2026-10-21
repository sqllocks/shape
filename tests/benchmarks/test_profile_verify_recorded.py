"""FRZ-01 T-22: unchanged field gates catch independently perturbed profiles."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_refengine"


def verifier():
    p = BENCH / "profile_1to1" / "verify.py"
    spec = importlib.util.spec_from_file_location("frz_profile_verify", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def profile(v):
    col = {key: None for key in v.RULES}
    col.update(
        dtype="float64",
        null_count=0,
        null_rate=0.0,
        cardinality=2,
        cardinality_ratio=0.02,
        is_unique=False,
        is_enum=True,
        enum_values={"a": 0.5, "b": 0.5},
        mean=10.0,
        distribution="normal",
        pattern="number",
        value_counts_ext={"a": 0.5, "b": 0.5},
        value_counts_ext_order=["a", "b"],
    )
    return {
        "row_count": 100,
        "primary_key": [],
        "detected_fks": {},
        "correlation_matrix": {"x": {"x": 1.0, "y": 0.5}, "y": {"x": 0.5, "y": 1.0}},
        "columns": {"x": col},
    }


@pytest.mark.parametrize(
    "change",
    [
        "equal",
        "mean",
        "null_count",
        "enum_values",
        "value_counts_ext",
        "distribution",
        "pattern",
        "column",
        "correlation",
    ],
)
def test_profile_field_controls(change, monkeypatch, capsys):
    v = verifier()
    baseline = profile(v)
    candidate = copy.deepcopy(baseline)
    c = candidate["columns"]["x"]
    if change == "mean":
        c["mean"] *= 1 + 1e-6
    if change == "null_count":
        c["null_count"] += 1
    if change == "enum_values":
        c["enum_values"]["a"] += 2e-9
    if change == "value_counts_ext":
        del c["value_counts_ext"]["b"]
    if change == "distribution":
        c["distribution"] = "uniform"
    if change == "pattern":
        c["pattern"] = "other"
    if change == "column":
        candidate["columns"] = {}
    if change == "correlation":
        # §6.2 said 1e-6, which is inside corr_compare's 1e-4 tolerance; 1e-3 is the smallest round value outside it, corrected 2026-10-11 by the lead.  # noqa: E501
        candidate["correlation_matrix"]["x"]["y"] += 1e-3
    matrix, failures = {}, []
    v.check_table(baseline, candidate, "stub", matrix, failures)
    assert bool(failures) == (change != "equal")
    if change != "equal":
        assert any(("column" if change == "column" else change) in f for f in failures)
    monkeypatch.setattr(v, "baseline_profile", lambda ds: baseline)
    monkeypatch.setattr(v, "shape_profile", lambda ds: candidate)
    assert v.main(["--impl", "shape", "stub.csv"]) == int(change != "equal")
    output = capsys.readouterr().out
    assert "0 mismatches" in output if change == "equal" else "MISMATCH" in output


def test_recorded_profile_missing_and_digest_errors_are_unavailable(monkeypatch, tmp_path, capsys):
    v = verifier()
    v.DATA = tmp_path
    (tmp_path / "d1.csv").write_bytes(b"stub input")
    from recorded_store import RecordingError

    monkeypatch.setattr(
        v,
        "baseline_profile",
        lambda ds: (_ for _ in ()).throw(RecordingError("profile:d1.csv: digest mismatch")),
    )
    assert v.main(["--impl", "shape", "d1.csv"]) == 2
    assert "profile:d1.csv" in capsys.readouterr().err


def test_missing_recording_is_checked_before_missing_profile_input(monkeypatch, capsys):
    v = verifier()
    from recorded_store import RecordingError

    monkeypatch.setattr(
        v,
        "load",
        lambda *a, **kw: (_ for _ in ()).throw(RecordingError("profile:stub.csv: no recording")),
    )
    monkeypatch.setattr(
        v,
        "input_digests",
        lambda *a: (_ for _ in ()).throw(AssertionError("read input before recording")),
    )
    assert v.main(["stub.csv"]) == 2
    assert "no recording" in capsys.readouterr().err


@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
def test_profile_input_digest_keys_use_recorded_posix_paths(monkeypatch, path_type):
    v = verifier()
    v.DATA = path_type("fixtures")
    source = v.DATA / "edge" / "x_time_only.csv"
    monkeypatch.setattr(v, "input_files", lambda ds: [source])
    monkeypatch.setattr(v, "sha256", lambda path: "a" * 64)
    assert v.input_digests("edge/x_time_only.csv") == {"profile/edge/x_time_only.csv": "a" * 64}
