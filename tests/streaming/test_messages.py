"""Broker messages to Arrow batches (P3-04): the decoding rules every stream source shares."""

from datetime import UTC, datetime

import pyarrow as pa
import pytest

from shape.streaming.messages import (
    EVENT_TIME,
    OFFSET,
    PARTITION,
    DecodeStats,
    StreamMessage,
    StreamSourceError,
    conform,
    decode_messages,
    freeze,
    group_by_partition,
    partition_offsets,
)


def msgs(*bodies, partition="0", ts=None):
    return [StreamMessage(partition, i, b, ts) for i, b in enumerate(bodies)]


def test_fields_become_columns_in_order_of_first_appearance():
    batch = decode_messages(msgs('{"b": 1, "a": "x"}', '{"c": 2.5, "a": "y"}'))
    assert batch.schema.names == ["b", "a", "c", EVENT_TIME]
    assert batch.column("b").to_pylist() == [1, None]
    assert batch.column("c").to_pylist() == [None, 2.5]
    assert batch.schema.field(EVENT_TIME).type == pa.timestamp("us", tz="UTC")


def test_bytes_and_text_bodies_both_decode():
    batch = decode_messages(msgs(b'{"a": 1}', '{"a": 2}'))
    assert batch.column("a").to_pylist() == [1, 2]


def test_event_time_prefers_the_payload_then_the_broker_then_null():
    ms = 1_700_000_000_000
    batch = decode_messages(
        [
            StreamMessage("0", 0, '{"_shape_event_time": "2024-03-01T12:00:00Z"}', ms * 1000),
            StreamMessage("0", 1, '{"_shape_event_time": "garbage"}', ms * 1000),
            StreamMessage("0", 2, "{}", ms * 1000),
            StreamMessage("0", 3, "{}", None),
        ]
    )
    got = batch.column(EVENT_TIME).to_pylist()
    assert got[0] == datetime(2024, 3, 1, 12, tzinfo=UTC)
    assert got[1] == got[2] == datetime.fromtimestamp(ms / 1000, UTC)
    assert got[3] is None
    assert "_shape_event_time" in batch.schema.names and batch.schema.names.count(EVENT_TIME) == 1


@pytest.mark.parametrize(
    ("unit", "value", "expect"),
    [
        ("s", 1700000000, 1_700_000_000),
        ("ms", 1700000000123, 1_700_000_000),
        ("us", 1700000000000000, 1_700_000_000),
    ],
)
def test_numeric_event_times_use_the_given_unit(unit, value, expect):
    batch = decode_messages(msgs(f'{{"t": {value}}}'), event_time_field="t", event_time_unit=unit)
    assert batch.schema.names == [EVENT_TIME]
    assert int(batch.column(EVENT_TIME)[0].as_py().timestamp()) == expect


def test_a_bad_event_time_unit_or_error_mode_is_refused():
    with pytest.raises(ValueError, match="event_time_unit"):
        decode_messages(msgs("{}"), event_time_unit="ns")
    with pytest.raises(ValueError, match="on_error"):
        decode_messages(msgs("{}"), on_error="ignore")


def test_booleans_and_infinities_are_not_event_times():
    batch = decode_messages(msgs('{"_shape_event_time": true}', '{"_shape_event_time": 1e999}'))
    assert batch.column(EVENT_TIME).to_pylist() == [None, None]


def test_undecodable_messages_are_counted_and_skipped():
    stats = DecodeStats()
    batch = decode_messages(
        msgs('{"a": 1}', "nope", "[1]", None, b"\xff\xfe", '{"a": 2}'), stats=stats
    )
    assert batch.column("a").to_pylist() == [1, 2]
    assert (stats.messages, stats.rows, stats.undecodable) == (6, 2, 4)
    assert decode_messages(msgs("nope", "7")) is None


def test_on_error_raise_names_the_partition_and_offset():
    with pytest.raises(StreamSourceError, match="partition 3 offset 1"):
        decode_messages(msgs('{"a": 1}', "nope", partition="3"), on_error="raise")


def test_nested_values_are_kept_as_json_text_with_stable_key_order():
    batch = decode_messages(msgs('{"a": {"y": 1, "x": [1, {"z": null}]}}'))
    assert batch.column("a").to_pylist() == ['{"x":[1,{"z":null}],"y":1}']


def test_a_column_with_mixed_types_becomes_a_string_column():
    batch = decode_messages(msgs('{"a": 1}', '{"a": "two"}', '{"a": 3.5}', '{"a": null}'))
    assert batch.column("a").to_pylist() == ["1", "two", "3.5", None]


def test_with_a_schema_rows_that_do_not_fit_are_rejected_not_coerced():
    schema = pa.schema(
        [("id", pa.int64()), ("v", pa.float64()), (EVENT_TIME, pa.timestamp("us", tz="UTC"))]
    )
    stats = DecodeStats()
    batch = decode_messages(
        msgs('{"id": 1, "v": 1.5}', '{"id": "x", "v": 2.5}', '{"id": 3}', '{"id": 4, "v": "bad"}'),
        schema=schema,
        stats=stats,
    )
    assert batch.schema.equals(schema)
    assert batch.column("id").to_pylist() == [1, 3]
    assert batch.column("v").to_pylist() == [1.5, None]
    assert (stats.rejected, stats.rows) == (2, 2)
    with pytest.raises(StreamSourceError, match="offset 1"):
        decode_messages(msgs('{"id": 1}', '{"id": "x"}'), schema=schema, on_error="raise")


def test_with_a_schema_unknown_fields_are_dropped_and_missing_ones_are_null():
    schema = pa.schema(
        [("a", pa.int64()), ("b", pa.string()), (EVENT_TIME, pa.timestamp("us", tz="UTC"))]
    )
    batch = decode_messages(msgs('{"a": 1, "zzz": 9}', '{"b": "q"}'), schema=schema)
    assert batch.to_pydict()["a"] == [1, None] and batch.to_pydict()["b"] == [None, "q"]


def test_every_row_rejected_gives_no_batch():
    schema = pa.schema([("a", pa.int64()), (EVENT_TIME, pa.timestamp("us", tz="UTC"))])
    assert decode_messages(msgs('{"a": "x"}'), schema=schema) is None


def test_with_offsets_adds_partition_and_offset_columns_last():
    batch = decode_messages(
        [StreamMessage("2", 40, '{"a": 1}'), StreamMessage("2", 41, '{"a": 2}')], with_offsets=True
    )
    assert batch.schema.names == ["a", EVENT_TIME, PARTITION, OFFSET]
    assert batch.column(OFFSET).to_pylist() == [40, 41]
    assert batch.column(PARTITION).to_pylist() == ["2", "2"]
    schema = batch.schema
    again = decode_messages([StreamMessage("2", 42, '{"a": 3}')], schema=schema, with_offsets=True)
    assert again.schema.equals(schema) and again.column(OFFSET).to_pylist() == [42]


def test_freeze_turns_all_null_columns_into_strings_and_conform_casts():
    first = decode_messages(msgs('{"a": 1, "n": null}'))
    assert first.schema.field("n").type == pa.null()
    frozen = freeze(first)
    assert frozen.field("n").type == pa.string() and frozen.field("a").type == pa.int64()
    assert conform(first, frozen).schema.equals(frozen)
    later = decode_messages(msgs('{"a": 2, "n": "now text"}'), schema=frozen)
    assert later.column("n").to_pylist() == ["now text"]


def test_partition_offsets_are_sorted_numerically_and_json_friendly():
    assert list(partition_offsets({"10": 1, "2": 5, "a": 7, "0": 3})) == ["0", "2", "10", "a"]
    assert partition_offsets({"1": 2}) == {"1": 2}


def test_group_by_partition_keeps_arrival_order_inside_each_partition():
    m = [StreamMessage(p, i, "{}") for i, p in enumerate(["1", "0", "1", "10", "0"])]
    groups = group_by_partition(m)
    assert [[x.offset for x in g] for g in groups] == [[1, 4], [0, 2], [3]]


def test_decoding_is_deterministic():
    body = msgs('{"a": 1, "b": "x"}', '{"a": 2, "b": "y"}', ts=5_000_000)
    assert decode_messages(body).equals(decode_messages(body))
