"""PRIV-092 (old #603): the registry leak scan at commit does not depend on ``redaction_manifest``.

Deleting that key (or any other safe-profile marker) from a full-fidelity safe profile must not
let it into a registry that ``shape profile validate --safe`` would refuse it from."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.main import main


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    rows = "\n".join(f"{i},user{i}@example.com,{20 + i % 40}" for i in range(500))
    (tmp_path / "customers.csv").write_text("id,email,age\n" + rows + "\n")
    assert main(["profile", "customers.csv", "-o", "cust.shape", "--capture", "full"]) == 0
    assert (
        main(["profile", "safe", "cust.shape", "-o", "unsafe.json", "--unsafe-full-fidelity"]) == 0
    )
    assert main(["profile", "safe", "cust.shape", "-o", "clean.json"]) == 0
    return tmp_path


def _objects(work: Path) -> list[Path]:
    return list((work / "reg" / "objects").iterdir()) if (work / "reg").exists() else []


def _without(work: Path, source: str, *keys: str, **set_: object) -> Path:
    doc = json.loads((work / source).read_text("utf-8"))
    for k in keys:
        doc.pop(k, None)
    doc.update(set_)
    out = work / "edited.json"
    out.write_text(json.dumps(doc), "utf-8")
    return out


STRIPS = {
    "no-redaction-manifest": (("redaction_manifest",), {}),
    "no-markers-no-format": (
        ("redaction_manifest", "schema_version", "format", "version", "unsafe"),
        {},
    ),
    "no-manifest-other-format": (("redaction_manifest",), {"format": "something-else"}),
}


@pytest.mark.parametrize(("keys", "set_"), STRIPS.values(), ids=STRIPS.keys())
def test_an_unsafe_profile_without_its_markers_is_still_refused(work, capsys, keys, set_):
    edited = _without(work, "unsafe.json", *keys, **set_)
    assert main(["profile", "validate", "--safe", str(edited)]) == 1
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "x", str(edited)]) == 1
    assert "leak" in capsys.readouterr().err.lower()
    assert _objects(work) == []


def test_a_clean_profile_still_commits(work):
    assert main(["registry", "reg", "commit", "x", "clean.json"]) == 0
    assert len(_objects(work)) == 1


def test_a_document_of_another_format_with_tables_is_not_scanned(work):
    gates = {"format": "shape-gates", "version": 1, "tables": {"t": {"columns": {"a": {}}}}}
    (work / "gates.json").write_text(json.dumps(gates), "utf-8")
    assert main(["registry", "reg", "commit", "g", "gates.json"]) == 0
    assert len(_objects(work)) == 1
