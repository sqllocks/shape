"""``shape behave``: run, check, import-gmf, examples (exit codes and files written)."""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shape_behavior import EVENT_SCHEMA
from shape_behavior.cli import BehaveCommand

FIXTURES = Path(__file__).parent / "fixtures"


def _main(*argv: str) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="shape behave")
    cmd = BehaveCommand()
    cmd.configure(parser)
    return cmd.run(parser.parse_args(list(argv)))


def test_run_writes_events_entities_and_manifest(tmp_path, capsys):
    out = tmp_path / "out"
    code = _main(
        "run", "subscription", "equipment_maintenance",
        "--population", "400", "--years", "2.5", "--seed", "7", "-o", str(out),
    )  # fmt: skip
    assert code == 0
    parts = sorted((out / "events").glob("part-*.parquet"))
    assert len(parts) == 3  # 1-year windows over 2.5 years
    events = [pq.read_table(p) for p in parts]
    assert all(t.schema.equals(EVENT_SCHEMA) for t in events)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 7 and manifest["population"]["size"] == 400
    assert [m["name"] for m in manifest["modules"]] == ["subscription", "equipment_maintenance"]
    assert manifest["events"] == sum(t.num_rows for t in events) > 0
    assert (out / "entities.parquet").is_file()
    assert "events for 400 entities" in capsys.readouterr().out


def test_run_is_deterministic_per_seed(tmp_path):
    def run(name, seed):
        out = tmp_path / name
        assert _main("run", "subscription", "--population", "300", "--years", "2",
                     "--seed", str(seed), "-o", str(out)) == 0  # fmt: skip
        return [pq.read_table(p) for p in sorted((out / "events").glob("*.parquet"))]

    a, b, c = run("a", 1), run("b", 1), run("c", 2)
    assert all(x.equals(y) for x, y in zip(a, b, strict=True))
    assert not all(x.equals(y) for x, y in zip(a, c, strict=True))


def test_run_a_gmf_file_and_report_on_stderr(tmp_path, capsys):
    out = tmp_path / "gmf"
    assert _main("run", str(FIXTURES / "gmf_unsupported.json"), "--population", "20",
                 "--years", "1", "-o", str(out)) == 0  # fmt: skip
    err = capsys.readouterr().err
    assert "Device" in err and "CallSubmodule" in err
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert len(manifest["unsupported"]) == 3


def test_strict_run_fails_with_exit_1(tmp_path, capsys):
    code = _main("run", str(FIXTURES / "gmf_unsupported.json"), "--strict", "--population", "5",
                 "--years", "1", "-o", str(tmp_path / "x"))  # fmt: skip
    assert code == 1 and "Wear_Device" in capsys.readouterr().err


def test_population_spec_file(tmp_path):
    spec = tmp_path / "pop.json"
    spec.write_text(
        json.dumps({"age_at_start": {"kind": "uniform", "low": 30, "high": 40}}), encoding="utf-8"
    )
    out = tmp_path / "o"
    assert _main("run", "subscription", "--population", "50", "--years", "1",
                 "--population-spec", str(spec), "-o", str(out)) == 0  # fmt: skip
    assert json.loads((out / "run.json").read_text())["population"]["age_at_start"]["low"] == 30


@pytest.mark.parametrize(
    "argv",
    [
        ("run", "subscription", "--population", "10", "--years", "0", "-o", "x"),
        ("run", "subscription", "--population", "-1", "--years", "1", "-o", "x"),
    ],
)
def test_bad_numbers_are_a_usage_error(argv, tmp_path):
    assert _main(*argv[:-1], str(tmp_path / "x")) == 2


def test_missing_module_is_a_usage_error(tmp_path, capsys):
    assert (
        _main("run", "no_such_module", "--population", "5", "--years", "1", "-o", str(tmp_path))
        == 2
    )
    assert "no_such_module" in capsys.readouterr().err


def test_check_valid_and_invalid(tmp_path, capsys):
    assert _main("check", "subscription") == 0
    assert "ok  subscription" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "shape-behavior/1", "name": "bad", "states": {
        "s": {"type": "initial", "transition": {"direct": "nowhere"}}}}), encoding="utf-8")  # fmt: skip
    assert _main("check", str(bad)) == 1
    assert "nowhere" in capsys.readouterr().err


def test_import_gmf_converts_and_reports(tmp_path, capsys):
    out = tmp_path / "native.json"
    assert _main("import-gmf", str(FIXTURES / "gmf_example.json"), "-o", str(out)) == 0
    assert "15 states imported" in capsys.readouterr().out
    native = json.loads(out.read_text(encoding="utf-8"))
    assert native["format"] == "shape-behavior/1"
    assert _main("check", str(out)) == 0  # the converted module is a valid native module


def test_examples_lists_and_writes(tmp_path, capsys):
    assert _main("examples") == 0
    assert capsys.readouterr().out.split() == [
        "equipment_maintenance",
        "healthcare_screening",
        "subscription",
    ]
    assert _main("examples", "-o", str(tmp_path / "m")) == 0
    assert len(list((tmp_path / "m").glob("*.json"))) == 3
