"""CLI end to end: shape profile / check / diff and their exit codes 0, 1 and 2."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture()
def day1(tmp_path: Path, orders) -> Path:
    path = tmp_path / "day1.parquet"
    pq.write_table(orders, path)
    return path


def test_profile_writes_all_outputs(tmp_path: Path, day1: Path, capsys):
    out = tmp_path / "day1.shape"
    html = tmp_path / "r.html"
    js = tmp_path / "s.json"
    rc = main(
        [
            "profile",
            str(day1),
            "-o",
            str(out),
            "--html",
            str(html),
            "--json",
            str(js),
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["written"] == str(out)
    p = shape.load(out)
    assert p.to_dict()["row_count"] == 400
    assert "<html" in html.read_text()
    assert json.loads(js.read_text()) == p.summary()
    assert p.to_dict()["columns"]["order_id"]["dtype"] == "integer"


def test_check_exit_codes(tmp_path: Path, day1: Path, capsys):
    out = tmp_path / "p.shape"
    assert main(["profile", str(day1), "-o", str(out)]) == 0
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"row_count": {"min": 1}, "columns": {"order_id": {"unique": True}}}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"row_count": {"min": 10**6}}))
    result = tmp_path / "res.json"
    capsys.readouterr()
    assert main(["check", str(out), str(ok)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert main(["check", str(out), str(bad), "--json", str(result)]) == 1
    saved = json.loads(result.read_text())
    assert saved["passed"] is False and saved["violations"][0]["rule"] == "row_count.min"


def test_diff_exit_codes(tmp_path: Path, orders, capsys):
    a, b = tmp_path / "a.shape", tmp_path / "b.shape"
    shape.save(shape.profile(orders), a)
    drifted = orders.drop_columns(["status"])
    shape.save(shape.profile(drifted), b)
    result = tmp_path / "d.json"
    assert main(["diff", str(a), str(a), "--fail-on-drift"]) == 0
    assert (
        main(["diff", str(a), str(b), "--json", str(result)]) == 0
    )  # drift alone is not a failure
    assert json.loads(result.read_text())["drifted"] is True
    assert main(["diff", str(a), str(b), "--fail-on-drift"]) == 1
    capsys.readouterr()


def test_input_errors_exit_2(tmp_path: Path, day1: Path, capsys):
    out = tmp_path / "p.shape"
    assert main(["profile", str(tmp_path / "missing.csv"), "-o", str(out)]) == 2
    assert main(["profile", str(day1), "-o", str(out)]) == 0
    assert main(["check", str(out), str(tmp_path / "missing.json")]) == 2
    malformed = tmp_path / "m.json"
    malformed.write_text(json.dumps({"colums": {}}))
    assert main(["check", str(out), str(malformed)]) == 2
    notjson = tmp_path / "n.json"
    notjson.write_text("{")
    assert main(["check", str(out), str(notjson)]) == 2
    assert "error" in capsys.readouterr().err


def test_subprocess_exit_codes(tmp_path: Path, day1: Path):
    out = tmp_path / "p.shape"
    r = run_cli("profile", str(day1), "-o", str(out))
    assert r.returncode == 0, r.stderr
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"row_count": {"min": 10**6}}))
    assert run_cli("check", str(out), str(bad)).returncode == 1
    assert run_cli("check", str(out), str(tmp_path / "nope.json")).returncode == 2
    assert run_cli("profile").returncode == 2  # usage error
    assert run_cli("diff", str(out)).returncode == 2  # usage error
    assert run_cli("diff", str(out), str(out), "--fail-on-drift").returncode == 0


def test_delta_directory_through_cli(tmp_path: Path, orders):
    from deltalake import write_deltalake

    table_dir = tmp_path / "t"
    write_deltalake(str(table_dir), orders)
    out = tmp_path / "t.shape"
    assert main(["profile", str(table_dir), "-o", str(out)]) == 0
    assert shape.load(out).to_dict()["row_count"] == 400


def test_legacy_diff_and_check_still_work(tmp_path: Path, capsys):
    # non-profile inputs keep the pre-existing behaviour
    s = tmp_path / "x.json"
    s.write_text(json.dumps({"rows": 1, "columns": {}}))
    assert main(["diff", str(s), str(s)]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_missing_first_shape_exits_2(tmp_path: Path):
    """Section 12.2: a missing .shape is an input error (exit 2), not a failed check (1)."""
    contract = tmp_path / "c.json"
    contract.write_text("{}", encoding="utf-8")
    assert main(["check", str(tmp_path / "no-such.shape"), str(contract)]) == 2
    assert main(["diff", str(tmp_path / "no-such.shape"), str(tmp_path / "b.shape")]) == 2


def test_plan_and_query_on_a_profile_exit_2_with_a_message(tmp_path: Path, day1: Path, capsys):
    """Legacy commands must not crash on a 0.9 profile: exit 2 and say it is not supported yet."""
    out = tmp_path / "p.shape"
    assert main(["profile", str(day1), "-o", str(out)]) == 0
    capsys.readouterr()
    assert main(["plan", str(out)]) == 2
    assert "not read profiles" in capsys.readouterr().err
    assert main(["query", str(out), "rows"]) == 2
