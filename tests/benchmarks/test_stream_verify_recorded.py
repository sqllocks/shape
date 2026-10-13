"""FRZ-01 stream controls preserve all seven existing perturbations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "regression"


def verifier():
    p = BENCH / "stream_1to1" / "verify.py"
    spec = importlib.util.spec_from_file_location("frz_stream_verify", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def events(v):
    n = 10000
    ts = pd.date_range("2020-01-01", periods=n, freq="s")
    df = pd.DataFrame(
        {
            "amount": np.linspace(1.0, 1.01, n),
            "n": np.arange(n),
            "category": ["a", "b"] * (n // 2),
            "ts": ts,
            "_shape_table": ["order"] * n,
            "_shape_event_time": ts,
            "_shape_seq": np.arange(n),
        }
    )
    types = {
        k: ["int" if k in ("n", "_shape_seq") else "float" if k == "amount" else "str"] for k in df
    }
    return v.Events("shape", "stub", list(df), 0, types, df, df["_shape_event_time"])


def test_all_existing_stream_perturbations_caught_and_no_allowance():
    v = verifier()
    a = events(v)
    assert v.compare_events(a, a, [a] * 4, len(a.df), len(a.df))["ok"]
    controls = v.perturbations(a, a, [a] * 4)
    assert len(controls) == 7
    for name, bad in controls.items():
        assert not v.compare_events(bad, a, [a] * 4, len(a.df), len(a.df))["ok"], name
    assert all(k == value for k, value in v.sc.FIELD_MAP.items())
    assert not hasattr(v.sc, "ALLOWED")


@pytest.mark.parametrize("damage", ["equal", "numeric", "missing", "tamper", "short_prefix"])
def test_stream_cli_verdicts_are_fail_closed(monkeypatch, tmp_path, capsys, damage):
    import copy

    v = verifier()
    base = events(v)
    current = copy.deepcopy(base)
    if damage == "numeric":
        current.df["amount"] *= 1.2
    record = {
        "seeds": {
            str(s): {"whole": {"events": len(base.df)}, "prefix": {}} for s in (42, 43, 44, 45, 46)
        }
    }

    def recording(*args, **kw):
        if damage in ("missing", "tamper"):
            raise v.RecordingError(f"stream:retail:order:small: {damage} recording")
        return record

    def loaded(tool, path):
        e = copy.deepcopy(current if tool == "shape" else base)
        if "prefix" in str(path):
            count = 2999 if damage == "short_prefix" else 3000
            e.df = e.df.iloc[:count]
            e.raw_time = e.raw_time.iloc[:count]
        return e

    monkeypatch.setattr(v, "recording", recording)
    monkeypatch.setattr(
        v,
        "ensure",
        lambda tool, scale, seed, n, generate: Path(tool + ("_prefix" if n else "_whole")),
    )
    monkeypatch.setattr(v, "authenticate_events", lambda *a: None)
    monkeypatch.setattr(v, "load", loaded)
    assert v.main(["--scale", "small", "--report", str(tmp_path / "report.json")]) == (
        2 if damage in ("missing", "tamper") else int(damage != "equal")
    )
    output = capsys.readouterr()
    assert "small" in output.err if damage in ("missing", "tamper") else "whole:" in output.out

    if damage == "short_prefix":
        monkeypatch.setattr(v, "perturbations", lambda *a: {"numeric": current})
        original = v.compare_events
        monkeypatch.setattr(
            v,
            "compare_events",
            lambda *a: {"ok": False} if a[0] is current else original(*a),
        )
        assert (
            v.main(
                [
                    "--scale",
                    "small",
                    "--negative-control",
                    "--report",
                    str(tmp_path / "negative.json"),
                ]
            )
            == 1
        )


def test_failed_stream_worker_reports_command_exit_and_output_tail(monkeypatch, tmp_path, capsys):
    import subprocess

    v = verifier()
    cmd = ["baseline-shape", "stream", "retail"]
    monkeypatch.setattr(
        v.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            cmd, 23, "\n".join(f"stream-line-{i}" for i in range(240)), "stream diagnostic"
        ),
    )
    with pytest.raises(v.MissingInput, match="exit 23"):
        v.run_command(cmd, tmp_path / "events.jsonl")
    output = capsys.readouterr().err
    assert "baseline-shape stream retail" in output
    assert "stream-line-239" in output and "stream diagnostic" in output
    assert "stream-line-0\n" not in output
