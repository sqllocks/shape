"""#535: `verify` messages never quote a data value unless `options.include_raw_values` is set:
the range gate's actual minimum and maximum are withheld."""

from __future__ import annotations

import csv
import json

import pytest

OUTLIER = 1037913


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "vd"
    folder.mkdir()
    with open(folder / "people.csv", "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["id", "salary"])
        for i in range(500):
            out.writerow([i, 50000 + i * 37 + (OUTLIER - 50259 if i == 7 else 0)])
    config = tmp_path / "vc.json"
    ranges = {"people.salary": {"min": 60000, "max": 100000}}
    config.write_text(json.dumps({"format": "shape-verify-config", "version": 1, "ranges": ranges}))
    return str(folder), str(config)


def _range_gate(result):
    (gate,) = [g for g in result["gates"] if g["name"] == "range_constraint"]
    return gate


def test_the_range_gate_withholds_the_actual_extremes(api, data):
    path, config = data
    gate = _range_gate(api.ok("verify", path=path, config=config))
    assert not gate["passed"] and len(gate["errors"]) == 2
    text = json.dumps(gate)
    assert str(OUTLIER) not in text and "50000" not in text
    assert all(
        "values above maximum 100000" in e or "below minimum 60000" in e for e in gate["errors"]
    )
    assert all("withheld" in e for e in gate["errors"]) and gate["redacted"] is True


def test_include_raw_values_keeps_the_actual_extremes(api, data):
    path, config = data
    gate = _range_gate(api.ok("verify", {"include_raw_values": True}, path=path, config=config))
    assert str(OUTLIER) in json.dumps(gate) and "redacted" not in gate
