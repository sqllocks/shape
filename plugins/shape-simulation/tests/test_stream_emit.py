"""The stream emitter: envelope, order, replays, sinks and the emit runtime underneath."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from shape_simulation.stream_emit import (
    BurstWindow,
    StreamEmitConfig,
    StreamEmitter,
    TablesEventPlan,
    topic_map,
)

from shape.streaming.emit import MemorySink
from shape.streaming.emit.formats import rows_of

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def emit(
    tmp_path: Path, tables: dict[str, pa.Table], **kw: object
) -> tuple[Any, list[dict[str, Any]]]:
    path = tmp_path / "events.jsonl"
    cfg = StreamEmitConfig(sink_type="file", sink_connection={"path": str(path)}, **kw)  # type: ignore[arg-type]
    result = StreamEmitter(tables, cfg).emit()
    return result, read(path)


def key(e: dict[str, Any]) -> tuple[str, int]:
    return e["shapetable"], e["shapeseq"]


def test_every_row_is_one_event_with_the_envelope(
    tmp_path: Path, orders: pa.Table, products: pa.Table
) -> None:
    result, events = emit(tmp_path, {"orders": orders, "products": products})
    assert (result.events_sent, result.replay_events_sent) == (700, 0)
    assert [key(e) for e in events] == [("orders", i) for i in range(600)] + [
        ("products", i) for i in range(100)
    ]
    first = events[0]
    assert list(first)[:9] == [
        "specversion",
        "id",
        "source",
        "type",
        "time",
        "datacontenttype",
        "topic",
        "schemaversion",
        "correlationid",
    ]
    assert (first["specversion"], first["source"], first["type"], first["topic"]) == (
        "1.0",
        "shape",
        "shape.orders",
        "orders",
    )
    assert first["id"] == "orders/0" and first["schemaversion"] == "1.0"
    assert ISO_Z.match(first["time"]) and first["datacontenttype"] == "application/json"
    data = first["data"]
    assert data["_shape_table"] == "orders" and data["_shape_seq"] == 0
    assert data["_shape_event_time"] == data["ordered_at"]  # the table's first timestamp column
    assert "_shape_event_time" not in events[-1]["data"]  # no time column, no event time
    assert (
        data["order_id"] == 1
        and isinstance(data["total"], float)
        and isinstance(data["is_gift"], bool)
    )
    assert {e["correlationid"] for e in events} == {first["correlationid"]}


def test_values_are_json_native(tmp_path: Path) -> None:
    import datetime as dt
    import decimal

    table = pa.table(
        {
            "d": pa.array([dt.date(2024, 1, 2), None]),
            "amount": pa.array([decimal.Decimal("1.50"), None], pa.decimal128(5, 2)),
            "f": pa.array([float("nan"), 1.5]),
            "b": pa.array([b"ab", None]),
        }
    )
    _, events = emit(tmp_path, {"t": table})
    data = [e["data"] for e in events]
    assert data[0]["d"] == "2024-01-02" and data[0]["amount"] == "1.50" and data[0]["f"] is None
    assert data[0]["b"] == "YWI=" and data[1]["d"] is None and data[1]["amount"] is None
    assert data[0]["_shape_event_time"] == "2024-01-02"


def test_out_of_order_is_a_permutation_with_adjacent_swaps(
    tmp_path: Path, orders: pa.Table
) -> None:
    _, events = emit(tmp_path, {"orders": orders}, out_of_order_probability=0.2, seed=2)
    seqs = [e["shapeseq"] for e in events]
    assert sorted(seqs) == list(range(600)) and seqs != list(range(600))
    # each swap exchanges neighbours, so an event only strays by the length of a run of swaps
    assert max(abs(seqs[i] - i) for i in range(600)) <= 6
    again = emit(tmp_path / "again", {"orders": orders}, out_of_order_probability=0.2, seed=2)[1]
    assert [e["shapeseq"] for e in again] == seqs


def test_max_events_cuts_the_sequence_after_the_reordering(
    tmp_path: Path, orders: pa.Table
) -> None:
    result, events = emit(tmp_path, {"orders": orders}, max_events=50, out_of_order_probability=0.3)
    assert result.events_sent == len(events) == 50
    assert emit(tmp_path / "zero", {"orders": orders}, max_events=0)[0].events_sent == 0


def test_replays_repeat_recent_events_with_their_ids(tmp_path: Path, orders: pa.Table) -> None:
    result, events = emit(
        tmp_path,
        {"orders": orders},
        replay_enabled=True,
        replay_probability=0.3,
        replay_burst_size=4,
        max_events=300,
    )
    replays = [e for e in events if e.get("replay")]
    assert result.events_sent == 300 and result.replay_events_sent == len(replays) > 0
    assert len(events) == 300 + len(replays) and all(ISO_Z.match(e["replaytime"]) for e in replays)
    primary_ids = {e["id"] for e in events if not e.get("replay")}
    assert {e["id"] for e in replays} <= primary_ids  # a replay is the same event again
    for i, e in enumerate(events):
        if e.get("replay") and not events[i - 1].get(
            "replay"
        ):  # a burst follows the event it replays
            assert events[i]["id"] in {events[j]["id"] for j in range(max(0, i - 6), i)}


def test_the_replay_window_starts_empty_with_every_emit(tmp_path: Path, orders: pa.Table) -> None:
    """Regression (SE-4): the baseline's window survived between calls and could replay events
    of an earlier call."""
    sink = MemorySink()
    cfg = StreamEmitConfig(
        replay_enabled=True, replay_probability=0.9, replay_burst_size=5, max_events=30, seed=1
    )
    emitter = StreamEmitter({"orders": orders}, cfg, sink)
    emitter.emit(tables={"first": orders})
    start = sink.num_events
    emitter.emit(tables={"second": orders})
    everything = [e for b in sink.batches for e in rows_of(b)]
    later = everything[start:]
    assert start > 0 and later
    assert {e["shapetable"] for e in later} == {"second"}  # nothing of the first call came back


def test_topics_by_position_one_for_all_or_the_table_names(
    orders: pa.Table, products: pa.Table
) -> None:
    t = {"a": orders, "b": products}
    assert topic_map(t, StreamEmitConfig(topics=["x", "y"])) == {"a": "x", "b": "y"}
    assert topic_map(t, StreamEmitConfig(topics=["all"])) == {"a": "all", "b": "all"}
    assert topic_map(t, StreamEmitConfig(topics=["x", "y", "z"])) == {"a": "a", "b": "b"}
    assert topic_map(t, StreamEmitConfig()) == {"a": "a", "b": "b"}


def test_schema_version_source_and_result(tmp_path: Path, orders: pa.Table) -> None:
    result, events = emit(
        tmp_path,
        {"orders": orders},
        topics=["sales"],
        envelope_schema_version="2.1",
        envelope_source="myapp",
        max_events=3,
    )
    assert result.topics_used == {"sales"} and result.schema_versions == {"sales": "2.1"}
    assert (events[0]["source"], events[0]["type"], events[0]["schemaversion"]) == (
        "myapp",
        "shape.sales",
        "2.1",
    )
    assert result.total_events == 3 and "StreamEmitResult(sent=3" in repr(result)


def test_one_correlation_id_per_run_and_the_one_you_pass(tmp_path: Path, orders: pa.Table) -> None:
    """Regression (SE-3): the baseline gave every event a correlation id of its own."""
    path = tmp_path / "e.jsonl"
    cfg = StreamEmitConfig(sink_type="file", sink_connection={"path": str(path)}, max_events=5)
    StreamEmitter({"o": orders}, cfg, correlation_id="run-1").emit()
    assert {e["correlationid"] for e in read(path)} == {"run-1"}


def test_a_sink_you_pass_is_flushed_and_left_open(orders: pa.Table) -> None:
    closed: list[bool] = []

    class Sink(MemorySink):
        def close(self) -> None:
            closed.append(True)

    sink = Sink()
    emitter = StreamEmitter({"o": orders}, StreamEmitConfig(max_events=10), sink)
    emitter.emit()
    emitter.emit()
    assert sink.num_events == 20 and not closed


def test_a_second_emit_to_a_file_appends(tmp_path: Path, orders: pa.Table) -> None:
    path = tmp_path / "e.jsonl"
    cfg = StreamEmitConfig(
        sink_type="file", sink_connection={"path": str(path), "mode": "w"}, max_events=10
    )
    emitter = StreamEmitter({"o": orders}, cfg)
    emitter.emit()
    emitter.emit()
    assert len(read(path)) == 20


def test_unknown_sink_types_are_refused_not_turned_into_the_console(orders: pa.Table) -> None:
    """Regression (SE-2): the baseline sent 'eventstream' to standard output."""
    for kind in ("eventstream", "eventhub", "kafka"):
        with pytest.raises(ValueError, match="sink_type"):
            StreamEmitter({"o": orders}, StreamEmitConfig(sink_type=kind))
    with pytest.raises(Exception, match="unknown sink"):
        StreamEmitter({"o": orders}, StreamEmitConfig(sink_type="nope://x", max_events=1)).emit()


def test_console_sink_writes_events_on_standard_output(
    capsys: pytest.CaptureFixture[str], orders: pa.Table
) -> None:
    StreamEmitter({"o": orders}, StreamEmitConfig(max_events=2)).emit()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["id"] == "o/0"


def test_burst_windows_speed_up_a_paced_run(orders: pa.Table) -> None:
    """Regression (SE-1): the baseline accepted burst windows and ignored them."""
    sink = MemorySink()
    cfg = StreamEmitConfig(
        realtime=True, rate_per_sec=100, max_events=150, burst_windows=[BurstWindow(0, 1.0, 3.0)]
    )
    started = time.perf_counter()
    StreamEmitter({"o": orders}, cfg, sink).emit()
    elapsed = time.perf_counter() - started
    assert sink.num_events == 150 and 0.3 <= elapsed < 1.1  # 150 at 100/s would take 1.5 s


def test_jitter_pauses_a_paced_run(orders: pa.Table) -> None:
    sink = MemorySink()
    cfg = StreamEmitConfig(realtime=True, rate_per_sec=1000, jitter_ms=20, max_events=40, seed=1)
    started = time.perf_counter()
    StreamEmitter({"o": orders}, cfg, sink).emit()
    assert time.perf_counter() - started >= 0.2  # about 40 x 10 ms of jitter
    unpaced = StreamEmitConfig(jitter_ms=20, max_events=40)
    started = time.perf_counter()
    StreamEmitter({"o": orders}, unpaced, MemorySink()).emit()
    assert time.perf_counter() - started < 0.2  # without --realtime nobody waits


def test_the_plan_resumes_at_any_offset_with_the_same_events(orders: pa.Table) -> None:
    import numpy as np

    cfg = StreamEmitConfig(
        out_of_order_probability=0.2, replay_enabled=True, replay_probability=0.1, max_events=400
    )
    plan = TablesEventPlan({"o": orders}, cfg, {"o": "o"}, np.random.default_rng(5), "run", 300)
    whole = [e["id"] for b in plan.blocks() for e in rows_of(b.batch) if "id" in e]
    assert len(whole) == plan.total_events == 400 + plan.replay_events
    tail = [e["id"] for b in plan.blocks(137) for e in rows_of(b.batch)]
    assert tail == whole[137:]
    assert plan.fingerprint() == plan.fingerprint()
    with pytest.raises(ValueError, match="offset"):
        list(plan.blocks(-1))


def test_bad_settings_are_refused() -> None:
    for kw in (
        {"out_of_order_probability": 2.0},
        {"replay_probability": -0.1},
        {"max_events": -1},
        {"jitter_ms": -1},
    ):
        with pytest.raises(ValueError):
            StreamEmitConfig(**kw)  # type: ignore[arg-type]


def test_a_table_with_a_reserved_column_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="reserved"):
        emit(tmp_path, {"t": pa.table({"_shape_seq": [1, 2]})})
