"""W3-13: compatibility of the persisted formats ``shape-parity-report``,
``shape-consumer-contract`` and ``shape-consumer-check`` (frozen version 1 documents)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from shape.parity import FORMAT as PARITY_FORMAT
from shape.parity import VERSION as PARITY_VERSION
from shape.parity import render_text
from shape.schemacheck import validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def shipped(name: str) -> dict:
    return json.loads(resources.files("shape").joinpath(f"schemas/{name}").read_text("utf-8"))


def test_the_frozen_parity_report_still_validates_and_renders():
    path = FIXTURES / "parity" / "v1" / "report.json"
    doc = json.loads(path.read_text())
    assert doc["format"] == PARITY_FORMAT == "shape-parity-report"
    assert doc["version"] == 1 <= PARITY_VERSION
    assert validate(doc, shipped("shape-parity-report-v1.schema.json")) == []
    assert doc["parity"] is False
    text = render_text(doc)
    assert text.index("FAIL (") < text.index("NO parity")
    assert "orders.note" in text


def test_written_documents_have_the_frozen_keys(env, tmp_path, capsys):
    """What the commands write today has exactly the keys of the frozen documents, so a key that
    is renamed or removed breaks this test and not a reader in another repository."""
    from shape.cli.main import main

    frozen = json.loads((FIXTURES / "parity" / "v1" / "report.json").read_text())
    rc = main(["parity", str(env("a")), str(env("b")), "--dataset", "--json"])
    now = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert set(now) == set(frozen)
    assert set(now["inputs"]["a"]) == set(frozen["inputs"]["a"])
    assert set(now["options"]) == set(frozen["options"])
    assert set(now["summary"]) == set(frozen["summary"])
    assert set(now["summary"]["by_category"]) == set(frozen["summary"]["by_category"])
    assert {frozenset(c) - {"owner"} for c in now["checks"]} == {
        frozenset(c) - {"owner"} for c in frozen["checks"]
    }
    assert validate(now, shipped("shape-parity-report-v1.schema.json")) == []


@pytest.mark.parametrize(
    "name",
    [
        "shape-parity-report-v1.schema.json",
        "shape-consumer-contract-v1.schema.json",
        "shape-consumer-check-v1.schema.json",
    ],
)
def test_schemas_declare_format_and_integer_version(name):
    s = shipped(name)
    assert s["properties"]["format"]["const"].startswith("shape-")
    assert s["properties"]["version"]["type"] == "integer"
    assert {"format", "version"} <= set(s["required"])
