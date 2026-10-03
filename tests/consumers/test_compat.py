"""W3-13: compatibility of the persisted formats ``shape-consumer-contract`` and
``shape-consumer-check`` (frozen version 1 documents)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from consumer_helpers import FINANCE, contract, make_profile

from shape.cli.main import main
from shape.consumers import CHECK_FORMAT, CHECK_VERSION, FORMAT, VERSION, load, problems
from shape.consumers import schema as contract_schema
from shape.consumers.check import render_text as render_consumers
from shape.schemacheck import validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def shipped(name: str) -> dict:
    return json.loads(resources.files("shape").joinpath(f"schemas/{name}").read_text("utf-8"))


def test_the_frozen_consumer_contract_still_loads():
    path = FIXTURES / "consumers" / "v1" / "contract.json"
    c = load(path)
    assert (c.consumer, c.source) == ("finance", "orders")
    doc = json.loads(path.read_text())
    assert doc["format"] == FORMAT and doc["version"] == 1 <= VERSION
    assert problems(doc) == []
    assert validate(doc, contract_schema()) == []
    assert shipped("shape-consumer-contract-v1.schema.json") == contract_schema()


def test_the_frozen_consumer_check_report_still_validates_and_renders():
    path = FIXTURES / "consumers" / "v1" / "check-report.json"
    doc = json.loads(path.read_text())
    assert doc["format"] == CHECK_FORMAT == "shape-consumer-check"
    assert doc["version"] == 1 <= CHECK_VERSION
    assert validate(doc, shipped("shape-consumer-check-v1.schema.json")) == []
    text = render_consumers(doc)
    assert "FAIL  finance (broken by this change)" in text
    assert "PASS  marketing" in text


def test_a_newer_consumer_contract_is_refused_not_misread(tmp_path):
    doc = json.loads((FIXTURES / "consumers" / "v1" / "contract.json").read_text())
    doc["version"] = VERSION + 1
    doc["something_new"] = {"a": 1}
    (found,) = problems(doc)
    assert "newer Shape" in found


def test_fields_added_later_are_not_silently_accepted_in_v1():
    doc = json.loads((FIXTURES / "consumers" / "v1" / "contract.json").read_text())
    doc["something_new"] = 1
    assert any("something_new" in p for p in problems(doc))


def test_written_consumer_report_has_the_frozen_keys(tmp_path, capsys):
    frozen = json.loads((FIXTURES / "consumers" / "v1" / "check-report.json").read_text())
    d = tmp_path / "c"
    d.mkdir()
    (d / "f.json").write_text(json.dumps(contract("finance", FINANCE)))
    base = make_profile(tmp_path / "base.shape")
    new = make_profile(tmp_path / "new.shape", drop=("orders", "amount"))
    rc = main(
        [
            "contracts", "check-consumers", str(new), "--baseline", str(base), "--source",
            "orders", "--consumers", str(d), "--no-project", "--json",
        ]
    )  # fmt: skip
    now = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert set(now) == set(frozen)
    assert set(now["consumers"][0]) == set(frozen["consumers"][0])
    assert set(now["summary"]) == set(frozen["summary"])
    assert validate(now, shipped("shape-consumer-check-v1.schema.json")) == []
