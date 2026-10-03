"""W1-04 deliverable 3: ``shape init`` scaffolds a project."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from shape.cli.main import main
from shape.project import load_project


def run_init(capsys, *args: str) -> tuple[int, dict | None]:
    capsys.readouterr()
    rc = main(["init", *args])
    cap = capsys.readouterr()
    run_init.err = cap.err  # type: ignore[attr-defined]
    out = cap.out.strip()
    return rc, (json.loads(out) if out else None)


def test_init_scaffolds_a_valid_project(tmp_path: Path, capsys):
    target = tmp_path / "my-feed"
    rc, out = run_init(capsys, str(target))
    assert rc == 0 and out is not None
    for d in ("data", "shapes", "contracts"):
        assert (target / d).is_dir()
    assert (target / ".gitattributes").read_text().splitlines() == ["*.shape diff=shape"]
    assert (target / ".github" / "workflows" / "shape.yml").is_file()
    p = load_project(target / "shape.yml")  # the scaffold passes the project validation
    assert p.name == "my-feed"
    assert list(p.sources) == ["example"]
    assert p.source("example").baseline.kind == "previous_run"
    assert sorted(out["created"]) == sorted(
        [
            "shape.yml",
            ".gitattributes",
            ".github/workflows/shape.yml",
            "data/.gitkeep",
            "shapes/.gitkeep",
            "contracts/.gitkeep",
            "contracts/consumers/.gitkeep",
        ]
    )
    assert out["skipped"] == [] and out["updated"] == []


def test_init_defaults_to_the_current_folder(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, _ = run_init(capsys)
    assert rc == 0 and (tmp_path / "shape.yml").is_file()


def test_sources_and_name(tmp_path: Path, capsys):
    rc, _ = run_init(
        capsys,
        str(tmp_path),
        "--name",
        "Retail Feed",
        "--source",
        "orders=data/in/orders",
        "--source",
        "customers",
    )
    assert rc == 0
    p = load_project(tmp_path / "shape.yml")
    assert p.name == "Retail Feed"
    assert p.source("orders").path == str(tmp_path / "data" / "in" / "orders")
    assert p.source("customers").path == str(tmp_path / "data" / "customers")
    flow = (tmp_path / ".github" / "workflows" / "shape.yml").read_text()
    assert "shape profile orders" in flow and "shape profile customers" in flow


@pytest.mark.parametrize("bad", ["bad name", "../x", "a=", "=data", ""])
def test_bad_source_is_refused_before_anything_is_written(tmp_path: Path, capsys, bad: str):
    target = tmp_path / "t"
    rc, _ = run_init(capsys, str(target), "--source", bad)
    assert rc == 2
    assert not target.exists()
    assert "--source" in run_init.err  # type: ignore[attr-defined]


def test_duplicate_source_is_refused(tmp_path: Path, capsys):
    rc, _ = run_init(capsys, str(tmp_path / "t"), "--source", "a", "--source", "a=other")
    assert rc == 2
    assert "twice" in run_init.err  # type: ignore[attr-defined]


def test_existing_project_is_never_overwritten(tmp_path: Path, capsys):
    (tmp_path / "shape.yml").write_text("mine: true\n", encoding="utf-8")
    rc, _ = run_init(capsys, str(tmp_path))
    assert rc == 2
    assert (tmp_path / "shape.yml").read_text() == "mine: true\n"
    assert not (tmp_path / "data").exists()  # nothing else was written either


def test_existing_project_error_text(tmp_path: Path, capsys):
    (tmp_path / "shape.yml").write_text("mine: true\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["init", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "already exists" in err and "--force" in err


def test_force_replaces_the_files_it_owns(tmp_path: Path, capsys):
    (tmp_path / "shape.yml").write_text("mine: true\n", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    rc, out = run_init(capsys, str(tmp_path), "--force")
    assert rc == 0 and out is not None
    load_project(tmp_path / "shape.yml")
    assert (tmp_path / "keep.txt").read_text() == "x"
    assert "shape.yml" in out["created"] or "shape.yml" in out["updated"]


def test_second_run_without_force_changes_nothing_else(tmp_path: Path, capsys):
    run_init(capsys, str(tmp_path))
    flow = tmp_path / ".github" / "workflows" / "shape.yml"
    flow.write_text("edited\n", encoding="utf-8")
    (tmp_path / "shape.yml").unlink()
    rc, out = run_init(capsys, str(tmp_path))
    assert rc == 0 and out is not None
    assert flow.read_text() == "edited\n"  # an edited workflow is kept
    assert ".github/workflows/shape.yml" in out["skipped"]
    assert "shape.yml" in out["created"]


def test_gitattributes_is_extended_not_replaced_and_idempotent(tmp_path: Path, capsys):
    attrs = tmp_path / ".gitattributes"
    attrs.write_text("*.csv text eol=lf", encoding="utf-8")  # no trailing newline
    rc, out = run_init(capsys, str(tmp_path))
    assert rc == 0 and out is not None
    assert attrs.read_text().splitlines() == ["*.csv text eol=lf", "*.shape diff=shape"]
    assert ".gitattributes" in out["updated"]
    (tmp_path / "shape.yml").unlink()
    rc, out = run_init(capsys, str(tmp_path))
    assert attrs.read_text().splitlines() == ["*.csv text eol=lf", "*.shape diff=shape"]
    assert ".gitattributes" not in out["updated"] + out["created"]


def test_gitattributes_symlink_is_not_written_through(tmp_path: Path, capsys):
    other = tmp_path / "elsewhere"
    other.write_text("x\n", encoding="utf-8")
    target = tmp_path / "t"
    target.mkdir()
    os.symlink(other, target / ".gitattributes")
    rc, _ = run_init(capsys, str(target))
    assert rc == 2
    assert other.read_text() == "x\n"


def test_ci_workflow_is_valid_yaml_using_existing_commands(tmp_path: Path, capsys):
    run_init(capsys, str(tmp_path), "--source", "orders")
    flow = yaml.safe_load((tmp_path / ".github" / "workflows" / "shape.yml").read_text())
    assert "jobs" in flow
    steps = next(iter(flow["jobs"].values()))["steps"]
    commands = "\n".join(s.get("run", "") for s in steps)
    assert 'pip install "sqllocks-shape[yaml]"' in commands
    assert "shape project validate" in commands
    assert "shape profile orders -o" in commands
    assert "shape diff --source orders" in commands and "--fail-on-drift" in commands


def test_scaffolded_commands_exist_and_parse(tmp_path: Path, capsys):
    """Every ``shape`` command line in the workflow example is accepted by the real parser."""
    from shape.cli.main import _build_parser

    run_init(capsys, str(tmp_path), "--source", "orders")
    flow = yaml.safe_load((tmp_path / ".github" / "workflows" / "shape.yml").read_text())
    parser = _build_parser()
    for step in next(iter(flow["jobs"].values()))["steps"]:
        for line in step.get("run", "").splitlines():
            line = line.strip().rstrip("\\").strip()
            if line.startswith("shape "):
                parser.parse_args(line.split()[1:])


def test_init_end_to_end_profile_with_a_source_name(tmp_path: Path, capsys, monkeypatch):
    run_init(capsys, str(tmp_path), "--source", "orders")
    # the scaffold points the source at data/orders, a folder of files
    (tmp_path / "data" / "orders").mkdir()
    (tmp_path / "data" / "orders" / "orders.csv").write_text("id,v\n1,a\n2,b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    assert main(["profile", "orders", "-o", "shapes/orders.shape"]) == 0
    assert (tmp_path / "shapes" / "orders.shape").is_file()


def test_init_loads_nothing_heavy():
    code = (
        "import sys, tempfile\n"
        "from shape.cli.main import main\n"
        "d = tempfile.mkdtemp()\n"
        "assert main(['init', d]) == 0\n"
        "heavy = [m for m in ('numpy', 'pyarrow', 'pandas') if m in sys.modules]\n"
        "print(heavy)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip().endswith("[]")


def test_init_creates_the_consumer_contracts_folder(tmp_path: Path, capsys):
    run_init(capsys, str(tmp_path))
    assert (tmp_path / "contracts" / "consumers" / ".gitkeep").is_file()


def test_ci_workflow_checks_the_consumer_contracts(tmp_path: Path, capsys):
    run_init(capsys, str(tmp_path), "--source", "orders", "--source", "billing")
    flow = yaml.safe_load((tmp_path / ".github" / "workflows" / "shape.yml").read_text())
    runs = [s.get("run", "") for s in next(iter(flow["jobs"].values()))["steps"]]
    for name in ("orders", "billing"):
        (profile,) = [i for i, r in enumerate(runs) if f"shape profile {name} -o" in r]
        (consumers,) = [
            i
            for i, r in enumerate(runs)
            if f"shape contracts check-consumers shapes/current/{name}.shape" in r
        ]
        assert consumers == profile + 1  # after the profile it checks, before the drift comparison
        assert f"--source {name}" in runs[consumers]
