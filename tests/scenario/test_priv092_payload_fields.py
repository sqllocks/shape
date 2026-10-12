"""PRIV-092 (old #668): a stream topic emits only its payload_fields; the validator checks them."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

from shape.scenario.loader import PackLoader
from shape.scenario.runner import PackRunner
from shape.scenario.validator import PackValidator
from tests.scenario.conftest import write

STREAM = """\
pack_version: 1
id: s
kind: stream
domain: retail
description: test
fabric_targets: {{x: 1}}
streaming:
  cadence: {{rate_per_sec: 5}}
  topics:
    - {{name: order, event_type: placed, payload_fields: {fields}}}
validation:
  required_gates: [row_count]
"""


def run(tmp_path, retail, text):
    pack = PackLoader().load(write(tmp_path / "p.yaml", text))
    return PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "out")


def _events(tmp_path: Path, retail, fields: str) -> list[dict]:
    result = run(tmp_path, retail, STREAM.format(fields=fields))
    assert result.is_success, result.errors
    lines = (tmp_path / "out" / "order_placed.jsonl").read_text().splitlines()
    return [json.loads(x) for x in lines]


def test_an_event_carries_only_the_listed_fields_in_order(tmp_path, retail):
    events = _events(tmp_path, retail, "[order_date, order_id]")
    assert len(events) == 1000
    assert all(list(e) == ["order_date", "order_id"] for e in events)


def test_a_topic_without_payload_fields_emits_every_column(tmp_path, retail):
    events = _events(tmp_path, retail, "[]")
    assert "order_id" in events[0] and len(events[0]) > 2


def test_no_field_left_is_one_empty_event_per_row(tmp_path):
    from shape.scenario.runner import _payload, _write_jsonl

    table = pa.table({"a": [1, 2, 3]})
    target = tmp_path / "t.jsonl"
    _write_jsonl(_payload(table, ["missing"]), target, table.num_rows)
    assert target.read_text() == "{}\n" * 3


def test_the_validator_refuses_a_payload_field_that_is_not_a_column(tmp_path, retail):
    path = tmp_path / "p.yaml"
    path.write_text(STREAM.format(fields="[order_id, no_such_column]"), "utf-8")
    result = PackValidator().validate(PackLoader().load(path), retail)
    assert any("payload field 'no_such_column' is not a column" in e for e in result.errors)


@pytest.mark.parametrize("fields", ["[order_id]", "[]"])
def test_the_validator_accepts_real_columns(tmp_path, retail, fields):
    path = tmp_path / "p.yaml"
    path.write_text(STREAM.format(fields=fields), "utf-8")
    assert PackValidator().validate(PackLoader().load(path), retail).errors == []
