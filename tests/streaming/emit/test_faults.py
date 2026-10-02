"""Duplicates, poison messages, the answer key, fan-out, the virtual clock and the rate cap."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shape.cli.main import main
from shape.errors import ShapeError
from shape.streaming.emit import (
    AnswerKey,
    EmitConfig,
    EmitRunner,
    EventPlan,
    FanOutSink,
    FaultSink,
    MemorySink,
    VirtualClock,
    parse_speed,
    read_answer_key,
)
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE, decode_line

from .conftest import make_engine

BASE = ["emit", "retail", "--scale", "small", "--seed", "3", "--table", "customer"]


def keys_of(sink: MemorySink) -> list[tuple[str, int]]:
    return [
        (t, s)
        for b in sink.batches
        for t, s in zip(
            b.column(FIELD_TABLE).to_pylist(), b.column(FIELD_SEQ).to_pylist(), strict=True
        )
    ]


def run_plan(
    n: int = 3000, *, answer_key: AnswerKey | None = None, **faults: Any
) -> tuple[MemorySink, Any]:
    engine = make_engine()
    plan = EventPlan(engine, tables=["customer"], answer_key=answer_key)
    memory = MemorySink()
    sink = FaultSink(memory, seed=engine.seed, answer_key=answer_key, **faults)
    EmitRunner(plan, sink, EmitConfig(max_events=n, batch_events=100)).run()
    return memory, sink


def test_duplicates_are_extra_copies_and_removing_repeats_gives_the_original() -> None:
    clean, _ = run_plan(duplicate_fraction=0.0)
    key = AnswerKey()
    dup, _ = run_plan(duplicate_fraction=0.05, duplicate_window=300, answer_key=key)
    original = keys_of(clean)
    seen = keys_of(dup)
    assert len(seen) > len(original)
    first: list[tuple[str, int]] = []
    done: set[tuple[str, int]] = set()
    for k in seen:
        if k not in done:
            done.add(k)
            first.append(k)
    assert first == original  # keep-first per key is the uninterrupted stream
    repeated = {k for k in set(seen) if seen.count(k) > 1}
    logged = {(r["table"], r["seq"]) for r in key.records if r["kind"] == "duplicate"}
    assert repeated == logged and len(logged) > 20  # the answer key matches the consumer


def test_the_same_events_are_chosen_every_run_and_after_a_restart() -> None:
    a, _ = run_plan(duplicate_fraction=0.05)
    b, _ = run_plan(duplicate_fraction=0.05)
    assert keys_of(a) == keys_of(b)
    engine = make_engine()
    plan = EventPlan(engine, tables=["customer"])
    tail = MemorySink()
    sink = FaultSink(tail, seed=engine.seed, duplicate_fraction=1.0, duplicate_window=1)
    # a full duplicate rate: every event is delivered twice, the copy right behind it
    EmitRunner(plan, sink, EmitConfig(max_events=200, batch_events=50)).run()
    assert len(keys_of(tail)) == 400


def test_duplicate_arrives_within_the_window() -> None:
    mem, _ = run_plan(500, duplicate_fraction=0.2, duplicate_window=40)
    pos: dict[tuple[str, int], int] = {}
    for i, k in enumerate(keys_of(mem)):
        if k in pos:
            assert 0 < i - pos[k] <= 40 + 200  # window plus the batches it rides in
        else:
            pos[k] = i


def test_poison_events_are_unparseable_and_listed() -> None:
    key = AnswerKey()
    engine = make_engine()
    plan = EventPlan(engine, tables=["customer"])

    class Lines:
        accepts_poison = True

        def __init__(self) -> None:
            self.data = b""

        def send(self, batch: Any) -> None:
            from shape.streaming.emit.formats import encode_batch

            self.data += encode_batch(batch)

        def flush(self) -> None: ...
        def close(self) -> None: ...

    out = Lines()
    sink = FaultSink(out, seed=engine.seed, poison_fraction=0.05, answer_key=key)
    EmitRunner(plan, sink, EmitConfig(max_events=1000, batch_events=100)).run()
    bad = 0
    for line in out.data.splitlines():
        try:
            decode_line(line)
        except ValueError:
            bad += 1
    logged = [r for r in key.records if r["kind"] == "poison"]
    assert bad == len(logged) and 20 < bad < 100


def test_poison_needs_a_sink_that_sends_json_text() -> None:
    with pytest.raises(ShapeError, match="JSON text"):
        FaultSink(MemorySink(), seed=1, poison_fraction=0.1)


def test_fractions_are_checked() -> None:
    with pytest.raises(ShapeError):
        FaultSink(MemorySink(), seed=1, duplicate_fraction=1.5)
    with pytest.raises(ShapeError):
        FaultSink(MemorySink(), seed=1, duplicate_window=0)


def test_answer_key_lists_late_and_anomalous_events(tmp_path: Path) -> None:
    path = tmp_path / "key.jsonl"
    code = main(
        [
            *BASE,
            "--max-events",
            "2000",
            "--out-of-order",
            "0.1",
            "--anomaly-fraction",
            "0.05",
            "--duplicate-fraction",
            "0.02",
            "--answer-key",
            str(path),
            "--sink",
            "file",
            "-o",
            str(tmp_path / "out.jsonl"),
        ]
    )
    assert code == 0
    records = read_answer_key(str(path))
    kinds = {r["kind"] for r in records}
    assert kinds == {"late", "anomaly", "duplicate"}
    late = [r for r in records if r["kind"] == "late"]
    assert 40 < len(late) < 150 and all(r["moved_up_to"] >= 1 for r in late)
    events = [decode_line(x) for x in (tmp_path / "out.jsonl").read_bytes().splitlines()]
    delivered = {e[FIELD_SEQ] for e in events}
    assert {r["seq"] for r in records} <= delivered
    # a late event really arrives after events that follow it in row order
    order = [e[FIELD_SEQ] for e in events]
    late_seq = late[0]["seq"]
    assert any(s > late_seq for s in order[: order.index(late_seq)])


def test_answer_key_is_deterministic_and_deduplicated(tmp_path: Path) -> None:
    paths = [tmp_path / f"k{i}.jsonl" for i in (1, 2)]
    for p in paths:
        main(
            [
                *BASE,
                "--max-events",
                "800",
                "--duplicate-fraction",
                "0.05",
                "--out-of-order",
                "0.05",
                "--answer-key",
                str(p),
                "--sink",
                "file",
                "-o",
                str(tmp_path / "o.jsonl"),
                "--fresh",
            ]
        )
    assert paths[0].read_text() == paths[1].read_text()
    with open(paths[0], "a", encoding="utf-8") as f:
        f.write(paths[0].read_text().splitlines()[0] + "\n")  # a resumed run repeats a record
    assert len(read_answer_key(str(paths[0]))) == len(paths[0].read_text().splitlines()) - 1


def test_fan_out_sends_to_every_sink_and_retries_only_the_one_that_failed() -> None:
    class Flaky(MemorySink):
        def __init__(self) -> None:
            super().__init__()
            self.fail = 1

        def send(self, batch: Any) -> None:
            if self.fail:
                self.fail -= 1
                raise ConnectionError("down")
            super().send(batch)

    good, flaky = MemorySink(), Flaky()
    plan = EventPlan(make_engine(), tables=["customer"])
    report = EmitRunner(
        plan,
        FanOutSink([good, flaky]),
        EmitConfig(max_events=300, batch_events=100, retry_backoff=0),
    ).run()
    assert report.retries == 1
    assert good.num_events == flaky.num_events == 300  # not 400: `good` was not sent it twice


def test_emit_to_two_files(tmp_path: Path) -> None:
    code = main(
        [
            *BASE,
            "--max-events",
            "50",
            "--to",
            f"file://{tmp_path}/a.jsonl",
            "--to",
            f"jsonl://{tmp_path}/out",
        ]
    )
    assert code == 0
    assert len((tmp_path / "a.jsonl").read_bytes().splitlines()) == 50
    assert len((tmp_path / "out" / "customer.jsonl").read_bytes().splitlines()) == 50


# ---- virtual clock and rate cap ---------------------------------------------------------


def test_parse_speed() -> None:
    assert parse_speed("60x") == 60 and parse_speed("0.5") == 0.5
    for bad in ("fast", "0x", "-3x", "nanx"):
        with pytest.raises(ValueError):
            parse_speed(bad)


def test_virtual_clock_scales_event_time_and_never_goes_back() -> None:
    clock = VirtualClock(60)
    assert clock.due(1000.0) == 0.0
    assert clock.due(1060.0) == 1.0  # a minute of events is a second
    assert clock.due(1030.0) == 1.0  # out of order: due at once, never earlier
    assert clock.due(None) == 1.0
    assert clock.due(1600.0) == 10.0
    assert clock.span == 600.0


def stream_deadlines(**config: Any) -> tuple[list[float], Any]:
    plan = EventPlan(make_engine(), tables=["order"], by_event_time=True)
    waits: list[float] = []

    def sleep_until(_stop: Any, deadline: float) -> None:
        waits.append(deadline)

    runner = EmitRunner(plan, MemorySink(), EmitConfig(**config), sleep_until=sleep_until)
    return waits, runner


def test_speed_paces_by_event_time() -> None:
    waits, runner = stream_deadlines(speed=3600.0, max_events=400, batch_events=50)
    report = runner.run()
    assert report.events == 400 and report.virtual_span > 0
    # the waits are monotonically non-decreasing absolute times, spread over about
    # span / speed seconds
    rel = [w - waits[0] for w in waits]
    assert rel == sorted(rel)
    assert rel[-1] == pytest.approx(report.virtual_span / 3600.0, rel=0.35, abs=2)


def test_speed_needs_event_times() -> None:
    plan = EventPlan(make_engine(), tables=["address"])
    runner = EmitRunner(plan, MemorySink(), EmitConfig(speed=10.0, max_events=10))
    with pytest.raises(ShapeError, match="event time"):
        runner.run()


def test_speed_and_realtime_do_not_combine() -> None:
    with pytest.raises(ValueError, match="choose one"):
        EmitRunner(EventPlan(make_engine()), MemorySink(), EmitConfig(realtime=True, speed=2.0))


def test_max_rate_is_a_hard_cap() -> None:
    waits, runner = stream_deadlines(max_rate=100.0, max_events=500, batch_events=50)
    runner.run()
    # after k batches of 50 events the cap allows them only at (50 * k) / 100 seconds
    t0 = None
    for i, deadline in enumerate(waits, start=1):
        t0 = deadline - (50 * i) / 100.0 if t0 is None else t0
        assert deadline - t0 == pytest.approx(50 * i / 100.0, abs=1e-6)
    assert len(waits) == 10


def test_cap_holds_over_a_faster_schedule() -> None:
    waits, runner = stream_deadlines(
        realtime=True, rate=1000.0, max_rate=100.0, max_events=200, batch_events=50
    )
    runner.run()
    assert waits[-1] - waits[0] >= 1.4  # 200 events at 100/s take 2 s, the first batch aside


def test_cli_rejects_bad_speed_and_cap(capsys: Any) -> None:
    assert main([*BASE, "--speed", "fast"]) != 0
    assert main([*BASE, "--max-rate", "0"]) != 0
    assert main([*BASE, "--speed", "2x", "--realtime"]) != 0


def test_json_report_includes_the_span(capsys: Any) -> None:
    assert (
        main(
            [
                "stream",
                "retail",
                "--scale",
                "small",
                "--seed",
                "3",
                "-t",
                "order",
                "--max-events",
                "200",
                "--speed",
                "1000000x",
                "--json",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert doc["virtual_span"] > 0
