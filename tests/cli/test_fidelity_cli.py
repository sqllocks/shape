"""P4-09: ``shape fidelity`` (alias ``compare``): reports, pass marks and exit codes."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

RNG = np.random.default_rng(3)


def _data(n=300, shift=0.0):
    return pa.table(
        {
            "id": np.arange(n),
            "amount": RNG.normal(50 + shift, 5, n),
            "kind": ["a", "b", "c", "d"] * (n // 4),
        }
    )


@pytest.fixture
def dirs(tmp_path):
    real, synth = tmp_path / "real", tmp_path / "synth"
    for d, shift in ((real, 0.0), (synth, 0.0)):
        d.mkdir()
        pq.write_table(_data(shift=shift), d / "orders.parquet")
        pq.write_table(_data(100, shift), d / "items.parquet")
    return real, synth


def test_passes_with_exit_0_and_prints_json(dirs, capsys):
    real, synth = dirs
    assert main(["fidelity", str(real), str(synth)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] and set(out["tables"]) == {"items", "orders"}


def test_compare_is_an_alias(dirs, capsys):
    real, synth = dirs
    assert main(["compare", str(real), str(synth)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"]


def test_below_the_pass_mark_exits_1(dirs):
    real, synth = dirs
    assert main(["fidelity", str(real), str(synth), "--min-score", "100.5"]) == 1
    assert main(["fidelity", str(real), str(synth), "--min-table-score", "100.5"]) == 1
    assert main(["fidelity", str(real), str(synth), "--min-column-score", "100.5"]) == 1
    assert main(["fidelity", str(real), str(synth), "--min-score", "0"]) == 0


def test_a_missing_column_fails_and_is_named(dirs, capsys):
    real, synth = dirs
    pq.write_table(_data().drop_columns(["amount"]), synth / "orders.parquet")
    assert (
        main(["fidelity", str(real), str(synth), "--min-score", "0", "--min-table-score", "0"]) == 1
    )
    out = json.loads(capsys.readouterr().out)
    assert out["tables"]["orders"]["columns"]["amount"]["score"] == 0.0
    assert any("amount" in f for f in out["failures"])


def test_an_empty_reference_fails(tmp_path, dirs):
    _, synth = dirs
    real = tmp_path / "empty"
    real.mkdir()
    pq.write_table(_data().slice(0, 0), real / "orders.parquet")
    pq.write_table(_data().slice(0, 0), real / "items.parquet")
    assert (
        main(["fidelity", str(real), str(synth), "--min-score", "0", "--min-table-score", "0"]) == 1
    )


def test_no_reference_files_is_an_input_error(tmp_path, dirs, capsys):
    _, synth = dirs
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert main(["fidelity", str(empty), str(synth)]) == 2
    assert main(["fidelity", str(tmp_path / "absent"), str(synth)]) == 2
    capsys.readouterr()


def test_writes_every_format_by_extension(dirs, tmp_path, capsys):
    real, synth = dirs
    outs = [tmp_path / "r.json", tmp_path / "r.md", tmp_path / "r.html"]
    args = ["fidelity", str(real), str(synth)]
    for o in outs:
        args += ["-o", str(o)]
    assert main(args) == 0
    assert json.loads(outs[0].read_text())["passed"]
    assert outs[1].read_text().startswith("# Fidelity report")
    assert outs[2].read_text().startswith("<!DOCTYPE html>")
    capsys.readouterr()


def test_an_unknown_report_extension_is_an_input_error(dirs, tmp_path):
    real, synth = dirs
    assert main(["fidelity", str(real), str(synth), "-o", str(tmp_path / "r.txt")]) == 2


def test_format_selects_what_is_printed(dirs, capsys):
    real, synth = dirs
    assert main(["fidelity", str(real), str(synth), "--format", "md"]) == 0
    assert capsys.readouterr().out.startswith("# Fidelity report")


def test_two_single_files_compare_whatever_they_are_called(tmp_path, capsys):
    a, b = tmp_path / "reference.csv", tmp_path / "generated.csv"
    pacsv.write_csv(_data(), a)
    pacsv.write_csv(_data(), b)
    assert main(["fidelity", str(a), str(b), "--min-score", "0"]) == 0
    assert list(json.loads(capsys.readouterr().out)["tables"]) == ["reference"]


def test_a_captured_profile_still_certifies_a_csv(tmp_path, capsys):
    rows = tmp_path / "rows.csv"
    pacsv.write_csv(_data(), rows)
    prof = tmp_path / "ref.json"
    assert main(["capture", str(rows), "-o", str(prof)]) == 0
    capsys.readouterr()
    assert main(["fidelity", str(prof), str(rows), "--tolerance", "0.01"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_a_profile_without_columns_no_longer_passes(tmp_path):
    prof = tmp_path / "empty.json"
    prof.write_text(json.dumps({"columns": {}}))
    rows = tmp_path / "rows.csv"
    pacsv.write_csv(_data(), rows)
    assert main(["fidelity", str(prof), str(rows)]) == 3
