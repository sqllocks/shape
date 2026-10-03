"""W1-14: compatibility tests of the persisted formats this package adds: ``shape-result``,
``shape-dry-run`` (frozen version 1 documents) and the ``ci:`` block of ``shape-project``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli import machine
from shape.cli.main import main
from shape.project import load_project, problems, schema
from shape.schemacheck import validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.parametrize("name", ["shape-result-v1.json", "shape-dry-run-v1.json"])
def test_frozen_version_1_documents_are_still_read(name):
    doc = json.loads((FIXTURES / "ci" / name).read_text())
    assert doc["version"] == 1
    assert machine.check_document(doc) == doc["format"]


def test_a_newer_version_is_refused_with_the_usual_message():
    doc = json.loads((FIXTURES / "ci" / "shape-result-v1.json").read_text())
    doc["version"] = 2
    with pytest.raises(ValueError, match="newer than this Shape understands"):
        machine.check_document(doc)


@pytest.mark.parametrize(
    "bad",
    [
        {"format": "other", "version": 1},
        {"format": "shape-result", "version": "1", "command": "x", "exit_code": 0},
        {"format": "shape-result", "version": True, "command": "x", "exit_code": 0},
        {"format": "shape-result", "version": 0, "command": "x", "exit_code": 0},
        {"format": "shape-result", "version": 1, "command": "x"},
        {"format": "shape-dry-run", "version": 1, "command": "x"},
        [],
    ],
)
def test_malformed_documents_are_refused(bad):
    with pytest.raises(ValueError):
        machine.check_document(bad)


def test_what_the_commands_print_today_is_what_the_frozen_documents_look_like(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.csv").write_text("id,v\n1,2\n3,4\n")
    assert main(["profile", "a.csv", "-o", "a.shape"]) == 0
    (tmp_path / "c.json").write_text(json.dumps({"columns": {"zz": {}}}))
    capsys.readouterr()
    assert main(["check", "a.shape", "c.json", "--json", "-"]) == 1
    live = json.loads(capsys.readouterr().out)
    frozen = json.loads((FIXTURES / "ci" / "shape-result-v1.json").read_text())
    assert set(frozen) <= set(live)
    assert {k: live[k] for k in ("format", "version", "command", "exit_code")} == {
        k: frozen[k] for k in ("format", "version", "command", "exit_code")
    }
    assert main(["capture", "a.csv", "-o", "x.shape", "--dry-run", "--json"]) == 0
    live = json.loads(capsys.readouterr().out)
    frozen = json.loads((FIXTURES / "ci" / "shape-dry-run-v1.json").read_text())
    assert set(live) == set(frozen)
    assert machine.check_document(live) == "shape-dry-run"


def test_frozen_project_with_a_ci_block_still_loads_and_validates():
    path = FIXTURES / "project" / "v1_ci" / "shape.yml"
    project = load_project(path)
    assert project.version == 1
    assert project.ci == {
        "junit": "reports/{command}.xml",
        "sarif": "reports/{command}.sarif",
        "json": "reports/{command}.json",
    }
    assert validate(project.document, schema()) == []
    assert problems(project.document) == []


def test_a_project_without_the_ci_block_has_no_defaults():
    project = load_project(FIXTURES / "project" / "v1" / "shape.yml")
    assert project.ci == {}
