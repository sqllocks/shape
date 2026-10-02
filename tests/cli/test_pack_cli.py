"""P6-14: `shape pack run|validate|list` end to end (needs shape-domains)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

pytest.importorskip("shape_domains")
pytest.importorskip("yaml")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "packs"


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- run --------------------------------------------------------------------------------------


def test_run_a_pack_writes_files_and_a_manifest(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "tutorial_custom_pack.yaml",
        "--scale", "fabric_demo", "--seed", 42, "-o", tmp_path,
    )  # fmt: skip
    assert code == 0
    assert "Pack Run: SUCCESS" in out and "schema_conformance: PASS" in out
    assert pq.read_table(tmp_path / "Files/landing/retail/customer.parquet").num_rows == 200
    assert pq.read_table(tmp_path / "Files/landing/retail/order.parquet").num_rows == 1000
    (manifest,) = tmp_path.glob("*_retail_fabric_demo_s42_manifest.json")
    assert json.loads(manifest.read_text())["pack_id"] == "my_custom_pack"


def test_run_json_for_the_notebook_pack(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "notebook_custom_pack.yaml",
        "--scale", "fabric_demo", "-o", tmp_path, "--json",
    )  # fmt: skip
    doc = json.loads(out)
    assert code == 0 and doc["success"] and doc["scale"] == "fabric_demo"
    assert doc["gates"] == {"schema_conformance": True}
    assert doc["manifest"]["seed"] == 42 and doc["manifest"]["tables"]["order"]["rows"] == 1000
    assert len(doc["files"]) == 3


def test_run_a_spec_uses_its_scale_seed_gates_and_pack(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "retail_basic.gsl.yaml", "-o", tmp_path, "--json"
    )
    doc = json.loads(out)
    assert code == 0 and doc["scale"] == "fabric_demo" and doc["manifest"]["seed"] == 42
    assert set(doc["gates"]) == {"schema_conformance", "referential_integrity"}
    assert len(doc["manifest"]["spec_hash"]) == 64
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "retail_basic.gsl.yaml",
        "--scale", "small", "--seed", 7, "-o", tmp_path / "o2", "--json",
    )  # fmt: skip
    assert json.loads(out)["manifest"]["seed"] == 7 and json.loads(out)["scale"] == "small"


def test_run_a_chaos_spec_reports_failed_gates_and_exits_zero(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "retail_chaos.gsl.yaml",
        "--scale", "fabric_demo", "-o", tmp_path,
    )  # fmt: skip
    assert code == 0
    assert "Chaos:" in out and "schema_conformance: FAIL" in out


def test_run_the_hybrid_spec_warns_but_runs(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "retail_hybrid.gsl.yaml",
        "--scale", "fabric_demo", "-o", tmp_path, "--json",
    )  # fmt: skip
    assert code == 0 and json.loads(out)["success"]


def test_run_exit_codes(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: b\nkind: file_drop\ndomain: retail\nfile_drop: {entities: [ghost]}\n")
    code, out, _ = run(capsys, "pack", "run", bad, "-o", tmp_path / "o")
    assert code == 1 and "Entity 'ghost' referenced in pack but not found" in out
    code, _, err = run(capsys, "pack", "run", tmp_path / "missing.yaml")
    assert code == 2 and "no pack or spec at" in err
    nodomain = tmp_path / "n.yaml"
    nodomain.write_text("id: n\nkind: file_drop\nfile_drop: {entities: [store]}\n")
    code, _, err = run(capsys, "pack", "run", nodomain)
    assert code == 2 and "names no domain" in err
    nodomain_ok = run(capsys, "pack", "run", nodomain, "--domain", "retail", "-o", tmp_path / "o3")
    assert nodomain_ok[0] == 0
    code, _, err = run(
        capsys, "pack", "run", FIXTURES / "tutorial_custom_pack.yaml", "--domain", "nope"
    )
    assert code == 2 and "no domain named 'nope'" in err
    code, out, _ = run(
        capsys,
        "pack",
        "run",
        FIXTURES / "tutorial_custom_pack.yaml",
        "--scale",
        "gigantic",
        "-o",
        tmp_path / "o4",
    )
    assert code == 1 and "unknown scale 'gigantic'" in out


def test_run_by_root_and_name(capsys, tmp_path):
    root = tmp_path / "packs"
    (root / "retail").mkdir(parents=True)
    (root / "retail" / "daily.yaml").write_text(
        (FIXTURES / "tutorial_custom_pack.yaml").read_text()
    )
    code, out, _ = run(
        capsys,
        "pack",
        "run",
        "retail/daily",
        "--root",
        root,
        "--scale",
        "fabric_demo",
        "-o",
        tmp_path / "o",
    )
    assert code == 0 and "Pack Run: SUCCESS" in out
    code, _, err = run(capsys, "pack", "run", "retail/weekly", "--root", root)
    assert code == 2 and "Available: daily" in err


# ---- validate ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["tutorial_custom_pack.yaml", "notebook_custom_pack.yaml"])
def test_validate_a_valid_pack(capsys, name):
    code, out, _ = run(capsys, "pack", "validate", FIXTURES / name)
    assert (
        code == 0 and out.strip().endswith("Pack validation: PASS") and "pack my_custom_pack" in out
    )


@pytest.mark.parametrize("name", ["retail_basic", "retail_chaos", "retail_hybrid"])
def test_validate_a_spec(capsys, name):
    code, out, _ = run(capsys, "pack", "validate", FIXTURES / f"{name}.gsl.yaml", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["valid"] and doc["kind"] == "spec" and doc["errors"] == []


def test_validate_reports_errors_with_exit_one(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: b\nkind: sideways\ndomain: retail\n")
    code, out, _ = run(capsys, "pack", "validate", bad)
    assert code == 1 and "Invalid kind 'sideways'" in out and "Pack validation: FAIL" in out
    code, out, _ = run(capsys, "pack", "validate", bad, "--json")
    assert code == 1 and json.loads(out)["valid"] is False
    spec = tmp_path / "s.gsl.yaml"
    spec.write_text("schema: {domain: retail}\nscenario: {pack: missing.yaml}\n")
    code, out, _ = run(capsys, "pack", "validate", spec)
    assert code == 1 and "scenario: Scenario pack not found" in out


def test_validate_bad_input_exits_two(capsys, tmp_path):
    code, _, err = run(capsys, "pack", "validate", tmp_path / "none.yaml")
    assert code == 2 and "no pack or spec at" in err
    empty = tmp_path / "e.yaml"
    empty.write_text("")
    code, _, err = run(capsys, "pack", "validate", empty)
    assert code == 2 and "is empty" in err


# ---- list -------------------------------------------------------------------------------------


def test_list_the_reference_inputs(capsys):
    code, out, _ = run(capsys, "pack", "list", FIXTURES, "--json")
    rows = json.loads(out)
    assert code == 0
    assert sorted((r["kind"], r["id"]) for r in rows) == [
        ("file_drop", "my_custom_pack"),
        ("file_drop", "my_custom_pack"),
        ("spec", "retail_basic.gsl"),
        ("spec", "retail_chaos.gsl"),
        ("spec", "retail_hybrid.gsl"),
    ]
    code, out, _ = run(capsys, "pack", "list", FIXTURES)
    assert code == 0 and out.splitlines()[0].startswith("kind") and "my_custom_pack" in out


def test_list_with_nothing_says_that_shape_ships_none(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "pack", "list")
    assert code == 0 and "Shape ships none" in out
    code, out, _ = run(capsys, "pack", "list", "--json")
    assert code == 0 and json.loads(out) == []
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "x.yaml").write_text("id: x\nkind: stream\ndomain: hr\n")
    (tmp_path / "packs" / "broken.yaml").write_text("- 1\n")
    code, out, _ = run(capsys, "pack", "list", "--json")
    rows = {r["id"]: r for r in json.loads(out)}
    assert code == 0 and rows["x"]["kind"] == "stream" and rows["broken"]["kind"] == "invalid"
    code, _, err = run(capsys, "pack", "list", tmp_path / "nodir")
    assert code == 2 and "not a directory" in err


def test_pack_is_listed_in_help(capsys):
    with pytest.raises(SystemExit):
        main(["pack", "--help"])
    out = capsys.readouterr().out
    assert "run" in out and "validate" in out and "list" in out
