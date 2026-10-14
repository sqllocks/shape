"""FRZ-01 T-22: unchanged field gates catch independently perturbed profiles."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

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


@pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
@pytest.mark.parametrize("change_hour", [False, True])
def test_time_only_profiles_on_a_different_weekday(
    dataset, change_hour, monkeypatch, tmp_path, capsys
):
    import datetime as dt

    from shape.profile.reference import dtparse

    v = verifier()
    baseline = v.sd.restore_order(v.load("profile", dataset)["profile"])
    # Recordings date time-only values to Tuesday in October 2026 (AM/PM
    # also contains year-1 values). Pin a Thursday in November of another year.
    today = dt.datetime(2027, 11, 4)
    assert baseline["columns"]["t"]["dow_histogram"][today.weekday()] == 0.0
    monkeypatch.setattr(dtparse, "_midnight", lambda supplied_today: today)
    assert dtparse.parse_mixed("09:00:00").date() == today.date()

    times = (
        [f"{h:02d}:{m:02d}:00" for h in (9, 10, 13) for m in (0, 30)]
        if dataset == "edge/x_time_only.csv"
        else ["3pm", "10am", "11:45 PM", "7:05 am"]
    )
    path = tmp_path / dataset
    path.parent.mkdir()
    path.write_text("id,t\n" + "".join(f"{i},{times[i % len(times)]}\n" for i in range(60)))
    monkeypatch.setattr(v, "DATA", tmp_path)
    candidate = v.shape_profile(dataset)
    assert candidate["columns"]["t"]["dow_histogram"] != baseline["columns"]["t"]["dow_histogram"]
    assert (
        candidate["columns"]["t"]["temporal_histogram"]
        != baseline["columns"]["t"]["temporal_histogram"]
    )
    if change_hour:
        candidate["columns"]["t"]["hour_histogram"][0] += 2e-9
    monkeypatch.setattr(v, "shape_profile", lambda ds: candidate)
    assert v.main([dataset]) == int(change_hour)
    output = capsys.readouterr().out
    if change_hour:
        assert "1 mismatches" in output
        assert ".hour_histogram" in output
    else:
        assert "0 mismatches" in output
    assert "hour_histogram: 2/2" in output if not change_hour else "hour_histogram: 1/2" in output


@pytest.mark.parametrize(
    "dataset", ["edge/x_dates_a.csv", "x_time_only.csv", "other/x_time_ampm.csv"]
)
@pytest.mark.parametrize("field", ["dow_histogram", "temporal_histogram"])
def test_date_derived_fields_are_compared_for_every_other_fixture(dataset, field):
    v = verifier()
    baseline = profile(v)
    candidate = copy.deepcopy(baseline)
    baseline["columns"]["x"][field] = [1.0, 0.0] if field == "dow_histogram" else {"year": [2026]}
    candidate["columns"]["x"][field] = [0.0, 1.0] if field == "dow_histogram" else {"year": [2027]}
    matrix, failures = {}, []
    v.compare_profile(baseline, candidate, dataset, matrix, failures)
    assert len(failures) == 1
    assert field in failures[0]
    assert matrix[field] == [1, 0, 0]


@pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
def test_time_only_exception_keeps_every_other_field_compared(dataset):
    v = verifier()
    baseline = v.sd.restore_order(v.load("profile", dataset)["profile"])
    for field in v.RULES:
        if field in {"dow_histogram", "temporal_histogram"}:
            continue
        candidate = copy.deepcopy(baseline)
        candidate["columns"]["t"][field] = (
            "changed" if baseline["columns"]["t"][field] is None else None
        )
        matrix, failures = {}, []
        v.compare_profile(baseline, candidate, dataset, matrix, failures)
        assert len(failures) == 1, (field, failures)
        assert f".t.{field}" in failures[0]
        assert matrix[field] == [2, 1, 1]
        assert "dow_histogram" not in matrix
        assert "temporal_histogram" not in matrix
