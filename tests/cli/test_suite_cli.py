"""W5-05 items 2 and 3 on the command line: `shape suite run`, `shape pack list --library` and
`shape pack run library:NAME`."""

from __future__ import annotations

import json
import shutil

import pytest

from shape.cli.main import main
from shape.scenario.library import formats

pytest.importorskip("shape_domains")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def damaged(tmp_path, monkeypatch):
    """The library with one answer key made wrong on purpose, in place of the shipped one."""
    dest = tmp_path / "library"
    shutil.copytree(formats.ROOT, dest, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    key = dest / "scenarios/nulls_injected/expect.json"
    doc = json.loads(key.read_text())
    doc["gates_fail"] = []
    key.write_text(json.dumps(doc))
    monkeypatch.setattr(formats, "ROOT", dest)
    return dest


# ---- shape suite run ------------------------------------------------------------------------


def test_the_smoke_suite_exits_zero_and_says_so(capsys):
    code, out, _ = run(capsys, "suite", "run", "smoke")
    assert code == 0
    assert "suite smoke (scale small)" in out and "ok   clean_baseline" in out
    assert "5 of 5 scenarios met their answer key" in out


def test_json_output_has_every_scenario_and_its_outcome(capsys):
    code, out, _ = run(capsys, "suite", "run", "schema-evolution", "--scale", "tiny", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["suite"] == "schema-evolution" and doc["met"] is True
    assert doc["scale"] == "tiny" and len(doc["scenarios"]) == 5
    assert all(s["met"] and s["mismatches"] == [] for s in doc["scenarios"])
    window = doc["scenarios"][-1]["outcome"]["drift"][-1]
    assert window["between"] == [0, 29] and window["changes"]


def test_seed_and_output_directory_apply(capsys, tmp_path):
    code, out, _ = run(
        capsys, "suite", "run", "smoke", "--scale", "tiny", "--seed", 5, "-o", tmp_path, "--json"
    )
    doc = json.loads(out)
    assert code == 0 and {s["outcome"]["seed"] for s in doc["scenarios"]} == {5}
    assert (tmp_path / "clean_baseline" / "customer.parquet").is_file()
    assert (tmp_path / "schema_add_column" / "day_0" / "order.parquet").is_file()


def test_a_wrong_answer_key_exits_one_and_prints_scenario_expectation_and_observation(
    capsys, damaged
):
    code, out, _ = run(capsys, "suite", "run", "smoke", "--scale", "tiny")
    assert code == 1
    assert "FAIL nulls_injected" in out
    assert "scenario nulls_injected: expected gate null_check passes" in out
    assert "observed gate null_check failed (customer.last_name has nulls)" in out
    assert "4 of 5 scenarios met their answer key" in out
    code, out, _ = run(capsys, "suite", "run", "smoke", "--scale", "tiny", "--json")
    assert code == 1 and json.loads(out)["met"] is False


def test_a_suite_file_runs(capsys, tmp_path):
    path = tmp_path / "mine.json"
    path.write_text(
        json.dumps(
            {
                "format": "shape-suite",
                "version": 1,
                "scenarios": ["clean_baseline", "late_arriving_data"],
            }
        )
    )
    code, out, _ = run(capsys, "suite", "run", path, "--scale", "tiny")
    assert code == 0 and "suite mine" in out and "2 of 2" in out


def test_a_malformed_suite_an_unknown_scenario_or_suite_exit_two(capsys, tmp_path):
    cases = {
        "not_json.json": "{nope",
        "no_format.json": json.dumps({"scenarios": ["clean_baseline"]}),
        "newer.json": json.dumps({"format": "shape-suite", "version": 2, "scenarios": ["x"]}),
        "empty.json": json.dumps({"format": "shape-suite", "version": 1, "scenarios": []}),
        "unknown.json": json.dumps(
            {"format": "shape-suite", "version": 1, "scenarios": ["clean_baseline", "ghost"]}
        ),
    }
    for name, text in cases.items():
        path = tmp_path / name
        path.write_text(text)
        code, out, err = run(capsys, "suite", "run", path, "--scale", "tiny")
        assert code == 2 and out == "" and err.startswith("shape: error:"), name
    assert "ghost" in err and "clean_baseline" in err  # the unknown one lists the choices
    code, _, err = run(capsys, "suite", "run", "no-such-suite")
    assert code == 2 and "smoke" in err
    code, _, err = run(capsys, "suite", "run", "smoke", "--scale", "gigantic")
    assert code == 2 and "tiny" in err


# ---- shape pack list --library / run library:NAME -------------------------------------------


def test_pack_list_library_names_every_scenario_and_suite(capsys):
    code, out, _ = run(capsys, "pack", "list", "--library")
    assert code == 0 and "nulls_injected" in out and "schema_evolution_schedule" in out
    assert "suites: failure-modes, schema-evolution, smoke" in out
    code, out, _ = run(capsys, "pack", "list", "--library", "--json")
    doc = json.loads(out)
    assert (
        code == 0 and len(doc["scenarios"]) >= 8 and doc["suites"] == ["failure-modes", "schema-evolution", "smoke"]
    )
    assert all({"id", "domain", "description"} == set(s) for s in doc["scenarios"])
    code, _, err = run(capsys, "pack", "list", "--library", "somewhere")
    assert code == 2 and "takes no directories" in err


def test_plain_pack_list_points_at_the_library(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "pack", "list")
    assert code == 0 and "Shape ships none" in out and "pack list --library" in out


def test_pack_run_library_runs_the_scenario_and_checks_its_key(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", "library:orphaned_foreign_keys", "--scale", "tiny", "-o", tmp_path
    )
    assert code == 0
    assert "gate referential_integrity: FAIL" in out and "answer key: met" in out
    assert (tmp_path / "orphaned_foreign_keys" / "order.parquet").is_file()
    code, out, _ = run(
        capsys,
        "pack",
        "run",
        "library:schema_rename_column",
        "--json",
        "--scale",
        "tiny",
        "-o",
        tmp_path,
    )
    doc = json.loads(out)
    assert code == 0 and doc["met"] is True
    assert {"column": "order.status", "kind": "column_removed"} in doc["outcome"]["drift"][0][
        "changes"
    ]


def test_pack_run_library_exits_one_when_the_key_is_not_met(capsys, damaged, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", "library:nulls_injected", "--scale", "tiny", "-o", tmp_path
    )
    assert code == 1 and "NOT MET: expected gate null_check passes" in out
    assert "answer key: NOT met" in out


def test_pack_run_and_validate_library_refuse_bad_names_and_options(capsys):
    code, _, err = run(capsys, "pack", "run", "library:ghost")
    assert code == 2 and "unknown scenario 'ghost'" in err and "clean_baseline" in err
    code, _, err = run(capsys, "pack", "run", "library:clean_baseline", "--root", "x")
    assert code == 2 and "--root and --domain do not apply" in err
    code, out, _ = run(capsys, "pack", "validate", "library:clean_baseline")
    assert code == 0 and "valid" in out
    code, out, _ = run(capsys, "pack", "validate", "library:clean_baseline", "--json")
    assert code == 0 and json.loads(out)["valid"] is True
    code, _, err = run(capsys, "pack", "validate", "library:ghost")
    assert code == 2
