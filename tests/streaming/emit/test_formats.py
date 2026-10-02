"""D-12 event fields and formats (P5-01)."""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json

import pyarrow as pa
import pytest

from shape.errors import ShapeSchemaError
from shape.streaming.emit import decode_line, encode_batch, event_key
from shape.streaming.emit.formats import (
    FIELD_SEQ,
    FIELD_TABLE,
    FIELD_TIME,
    event_time_column,
    read_events,
    with_event_fields,
)


def _batch() -> pa.RecordBatch:
    return pa.RecordBatch.from_pydict(
        {
            "id": pa.array([1, 2, 3], pa.int64()),
            "x": pa.array([1.5, float("nan"), float("inf")], pa.float64()),
            "when": pa.array([dt.datetime(2024, 1, 2, 3, 4, 5, 678901), None, dt.datetime(2000, 1, 1)]),
            "day": pa.array([dt.date(2024, 5, 6), None, dt.date(1999, 12, 31)]),
            "amount": pa.array([decimal.Decimal("1.10"), None, decimal.Decimal("-3.25")], pa.decimal128(9, 2)),
            "raw": pa.array([b"\x00\x01", None, b"abc"]),
            "s": pa.array(["a", "é", None]),
        }
    )


def test_event_fields_and_key() -> None:
    events = with_event_fields(_batch(), "t", 40)
    assert events.schema.names[-3:] == [FIELD_TABLE, FIELD_SEQ, FIELD_TIME]
    assert events.column(FIELD_SEQ).to_pylist() == [40, 41, 42]
    assert events.column(FIELD_TABLE).to_pylist() == ["t"] * 3
    assert events.column(FIELD_TIME).to_pylist() == _batch().column("when").to_pylist()


def test_no_event_time_without_a_datetime_column() -> None:
    b = pa.RecordBatch.from_pydict({"a": [1, 2]})
    assert event_time_column(b.schema) is None
    assert with_event_fields(b, "t", 0).schema.names == ["a", FIELD_TABLE, FIELD_SEQ]


def test_event_time_is_the_first_date_or_timestamp_column() -> None:
    assert event_time_column(_batch().schema) == "when"


def test_reserved_names_are_refused() -> None:
    b = pa.RecordBatch.from_pydict({FIELD_SEQ: [1]})
    with pytest.raises(ShapeSchemaError, match="reserved"):
        with_event_fields(b, "t", 0)


def test_flat_lines() -> None:
    lines = encode_batch(with_event_fields(_batch(), "t", 0)).decode().splitlines()
    rows = [json.loads(x) for x in lines]
    assert rows[0] == {
        "id": 1,
        "x": 1.5,
        "when": "2024-01-02T03:04:05.678901",
        "day": "2024-05-06",
        "amount": "1.10",
        "raw": base64.b64encode(b"\x00\x01").decode(),
        "s": "a",
        FIELD_TABLE: "t",
        FIELD_SEQ: 0,
        FIELD_TIME: "2024-01-02T03:04:05.678901",
    }
    assert rows[1]["x"] is None and rows[2]["x"] is None  # non-finite floats are null
    assert rows[1]["when"] is None and rows[1]["s"] == "é"
    assert encode_batch(_batch().slice(0, 0)) == b""


def test_cloudevents_envelope() -> None:
    line = encode_batch(with_event_fields(_batch(), "t", 7), "cloudevents", "demo").splitlines()[0]
    ce = json.loads(line)
    assert ce["specversion"] == "1.0"
    assert ce["id"] == "t/7" and ce["type"] == "shape.t.row" and ce["source"] == "shape://demo"
    assert ce["shapetable"] == "t" and ce["shapeseq"] == 7
    assert ce["time"] == "2024-01-02T03:04:05.678901"
    assert ce["data"][FIELD_SEQ] == 7
    assert event_key(decode_line(line)) == ("t", 7)


def test_unknown_envelope() -> None:
    with pytest.raises(ValueError, match="envelope"):
        encode_batch(with_event_fields(_batch(), "t", 0), "spindle")


def test_read_events_dedupes_on_the_key(tmp_path) -> None:
    ev = with_event_fields(pa.RecordBatch.from_pydict({"a": [1, 2, 3]}), "t", 0)
    p = tmp_path / "e.jsonl"
    p.write_bytes(encode_batch(ev) + encode_batch(ev.slice(1)) + encode_batch(ev, "cloudevents"))
    assert [e[FIELD_SEQ] for e in read_events(str(p))] == [0, 1, 2, 1, 2, 0, 1, 2]
    assert [e[FIELD_SEQ] for e in read_events(str(p), dedupe=True)] == [0, 1, 2]
