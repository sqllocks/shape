"""Issue #312: the streaming demo streams the first table, in dependency order, that has an event
time, so its events come in event-time order and carry ``_shape_event_time``; the first table
when no table has one."""

from __future__ import annotations

import json
from pathlib import Path

from demo_helpers import ROWS
from scale_schemas import plain_doc


def _timed_order_schema(path: Path) -> Path:
    doc = plain_doc(ROWS)
    doc["tables"]["order"]["columns"]["ordered_at"] = {
        "name": "ordered_at",
        "type": "timestamp",
        "generator": {"strategy": "temporal", "start": "2024-01-01", "end": "2024-12-31"},
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_streaming_takes_the_first_table_with_an_event_time(run, tmp_path):
    schema = _timed_order_schema(tmp_path / "shop.json")
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "streaming", "--domain", schema,
        "--max-events", "20", "--seed", "2",
    )  # fmt: skip
    assert code == 0, out
    events = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    assert len(events) == 20
    assert {e["_shape_table"] for e in events} == {"order"}
    times = [e["_shape_event_time"] for e in events]
    assert times == sorted(times)
    assert all(e["_shape_event_time"] == e["ordered_at"] for e in events)


def test_streaming_falls_back_to_the_first_table_without_any_event_time(run, schema_file):
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "streaming", "--domain", schema_file,
        "--max-events", "3", "--seed", "2",
    )  # fmt: skip
    assert code == 0, out
    events = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    assert {e["_shape_table"] for e in events} == {"customer"}
    assert all("_shape_event_time" not in e for e in events)
