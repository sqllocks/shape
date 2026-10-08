"""``shape stream`` (P5-04): one table in event-time order, on the ``shape emit`` runtime, and the
vectorised JSON-lines encoder the stream relies on."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import random
from pathlib import Path

import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.errors import ShapeError
from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan, MemorySink
from shape.streaming.emit.formats import (
    FIELD_SEQ,
    FIELD_TIME,
    _encode_batch_rows,
    encode_batch,
    with_event_fields,
)

from .conftest import make_engine

BASE = ["stream", "retail", "--scale", "small", "--seed", "3", "--table", "order"]


def _events(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_bytes().splitlines()]


def _run(plan: EventPlan, **config) -> list[pa.RecordBatch]:
    sink = MemorySink()
    EmitRunner(plan, sink, EmitConfig(**config)).run()
    return sink.batches


def _times(batches: list[pa.RecordBatch]) -> list:
    return pa.Table.from_batches(batches).column(FIELD_TIME).to_pylist()


# ---- the encoder ---------------------------------------------------------------------------


def _random_batch(rng: random.Random, n: int) -> pa.RecordBatch:
    def floats() -> list:
        out: list = []
        for _ in range(n):
            kind = rng.randrange(8)
            if kind == 0:
                out.append(None)
            elif kind == 1:
                out.append(float(rng.randrange(-5, 5000)))
            elif kind == 2:
                out.append(rng.choice([math.nan, math.inf, -math.inf, 0.0, -0.0]))
            elif kind == 3:
                out.append(rng.uniform(-1, 1) * 10 ** rng.randrange(-8, 22))
            else:
                out.append(round(rng.uniform(0, 1000), rng.randrange(0, 6)))
        return out

    alphabet = ["a", "Z", " ", "é", "漢", "😀", '"', "\\", "\n", "\t", "\x01", "'", "/"]
    strings = [
        None
        if rng.random() < 0.1
        else "".join(rng.choice(alphabet) for _ in range(rng.randrange(6)))
        for _ in range(n)
    ]
    plain = [None if rng.random() < 0.1 else rng.choice(["ab", "cd", "x y", "é"]) for _ in range(n)]
    base = dt.datetime(2020, 1, 1)
    return pa.RecordBatch.from_pydict(
        {
            "i": pa.array(
                [None if rng.random() < 0.1 else rng.randrange(-(2**62), 2**62) for _ in range(n)]
            ),
            "u": pa.array([rng.randrange(0, 2**63) for _ in range(n)], pa.uint64()),
            "f": pa.array(floats(), pa.float64()),
            "f32": pa.array([rng.uniform(0, 1) for _ in range(n)], pa.float32()),
            "b": pa.array([rng.choice([True, False, None]) for _ in range(n)]),
            "s": pa.array(strings, pa.string()),
            "plain": pa.array(plain, pa.string()),
            "ls": pa.array(plain, pa.large_string()),
            "d": pa.array(plain, pa.string()).dictionary_encode(),
            "ts": pa.array(
                [
                    None
                    if rng.random() < 0.1
                    else base
                    + dt.timedelta(seconds=rng.randrange(10**8), microseconds=rng.randrange(10**6))
                    for _ in range(n)
                ]
            ),
            "tz": pa.array(
                [base + dt.timedelta(hours=i) for i in range(n)], pa.timestamp("us", "UTC")
            ),
            "day": pa.array(
                [
                    None
                    if rng.random() < 0.1
                    else dt.date(2020, 1, 1) + dt.timedelta(days=rng.randrange(900))
                    for _ in range(n)
                ]
            ),
            "dec": pa.array(
                [decimal.Decimal(rng.randrange(-(10**6), 10**6)) / 100 for _ in range(n)],
                pa.decimal128(12, 2),
            ),
            "raw": pa.array([rng.choice([b"\x00\x01", b"abc", None]) for _ in range(n)]),
            "nul": pa.nulls(n),
            "lst": pa.array([[1, 2], None, []][i % 3] for i in range(n)),
            'weird name "q"': pa.array(range(n)),
        }
    )


@pytest.mark.parametrize("seed", range(12))
def test_vectorised_encoder_equals_the_row_encoder(seed: int) -> None:
    rng = random.Random(seed)
    batch = with_event_fields(_random_batch(rng, rng.randrange(1, 200)), "t", 7)
    assert encode_batch(batch) == _encode_batch_rows(batch, "flat", "shape")


def test_encoder_edge_values() -> None:
    batch = pa.RecordBatch.from_pydict(
        {
            "f": pa.array(
                [
                    100.0,
                    1e-4,
                    9.999e-5,
                    1e16,
                    1e15,
                    123456789012345.6,
                    5e-324,
                    1.7976931348623157e308,
                    -0.0,
                    0.1 + 0.2,
                ],
                pa.float64(),
            ),
            "s": pa.array(["", " ", "\x7f", "\\", '"', "a\u0000b", "é", "\r\n", "ok", "tab\t"]),
        }
    )
    assert encode_batch(batch) == _encode_batch_rows(batch, "flat", "shape")
    assert encode_batch(batch.slice(0, 0)) == b""
    for line in encode_batch(batch).splitlines():
        json.loads(line)


def test_encoder_handles_a_sliced_batch() -> None:
    batch = _random_batch(random.Random(1), 50).slice(13, 20)
    assert encode_batch(batch) == _encode_batch_rows(batch, "flat", "shape")


# ---- the plan ------------------------------------------------------------------------------


def test_plan_is_in_event_time_order_and_has_the_table_row_keys() -> None:
    engine = make_engine()
    plan = EventPlan(engine, tables=["order"], by_event_time=True)
    batches = _run(plan)
    table = pa.Table.from_batches(batches)
    times = table.column(FIELD_TIME).to_pylist()
    assert len(times) == engine.row_counts["order"]
    assert times == sorted(t for t in times)
    # every row once; _shape_seq is the row position, so the key matches `shape emit`'s
    seqs = table.column(FIELD_SEQ).to_pylist()
    assert sorted(seqs) == list(range(len(seqs)))
    plain = pa.Table.from_batches(_run(EventPlan(make_engine(), tables=["order"])))
    by_seq = plain.sort_by(FIELD_SEQ)
    assert table.sort_by(FIELD_SEQ).equals(by_seq)


def test_ties_keep_row_order_and_nulls_come_first() -> None:
    # a stable sort: rows with an equal time stay in row order
    engine = make_engine()
    batches = _run(EventPlan(engine, tables=["order"], by_event_time=True))
    t = pa.Table.from_batches(batches)
    last: tuple | None = None
    for time_, seq in zip(
        t.column(FIELD_TIME).to_pylist(), t.column(FIELD_SEQ).to_pylist(), strict=True
    ):
        if last is not None and last[0] == time_:
            assert seq > last[1]
        last = (time_, seq)


def test_a_table_without_a_time_column_stays_in_row_order() -> None:
    engine = make_engine()
    batches = _run(EventPlan(engine, tables=["store"], by_event_time=True))
    t = pa.Table.from_batches(batches)
    if FIELD_TIME not in t.schema.names:
        assert t.column(FIELD_SEQ).to_pylist() == list(range(t.num_rows))


def test_one_table_is_required() -> None:
    with pytest.raises(ShapeError, match="exactly one table"):
        EventPlan(make_engine(), by_event_time=True)
    with pytest.raises(ShapeError, match="exactly one table"):
        EventPlan(make_engine(), tables=["order", "customer"], by_event_time=True)


def test_resume_is_the_suffix_and_max_events_the_prefix() -> None:
    plan = EventPlan(make_engine(), tables=["order"], by_event_time=True, out_of_order=0.2)
    whole = pa.Table.from_batches(_run(plan))
    for offset in (0, 1, 999, 2500, whole.num_rows - 1):
        got = pa.Table.from_batches(list(b.batch for b in plan.blocks(offset)))
        assert got.equals(whole.slice(offset))
    first = pa.Table.from_batches(_run(plan, max_events=700))
    assert first.equals(whole.slice(0, 700))


def test_out_of_order_delays_events_and_is_deterministic() -> None:
    plain = pa.Table.from_batches(
        _run(EventPlan(make_engine(), tables=["order"], by_event_time=True))
    )
    a = pa.Table.from_batches(
        _run(EventPlan(make_engine(), tables=["order"], by_event_time=True, out_of_order=0.1))
    )
    b = pa.Table.from_batches(
        _run(EventPlan(make_engine(), tables=["order"], by_event_time=True, out_of_order=0.1))
    )
    assert a.equals(b) and not a.equals(plain)
    assert sorted(a.column(FIELD_SEQ).to_pylist()) == sorted(plain.column(FIELD_SEQ).to_pylist())
    moved = sum(
        x != y
        for x, y in zip(
            a.column(FIELD_SEQ).to_pylist(), plain.column(FIELD_SEQ).to_pylist(), strict=True
        )
    )
    assert 0 < moved < a.num_rows


def test_the_time_order_is_part_of_the_checkpoint_identity() -> None:
    row = EventPlan(make_engine(), tables=["order"])
    timed = EventPlan(make_engine(), tables=["order"], by_event_time=True)
    assert row.fingerprint() != timed.fingerprint()


# ---- the command ---------------------------------------------------------------------------


def test_stream_file_is_the_emit_table_in_time_order(tmp_path: Path, capsys) -> None:
    out, ref = tmp_path / "s.jsonl", tmp_path / "e.jsonl"
    assert main([*BASE, "--no-realtime", "--sink", "file", "-o", str(out)]) == 0
    assert "shape stream:" in capsys.readouterr().out
    assert (
        main(
            [
                "emit",
                "retail",
                "--scale",
                "small",
                "--seed",
                "3",
                "--table",
                "order",
                "--sink",
                "file",
                "-o",
                str(ref),
            ]
        )
        == 0
    )
    s, e = _events(out), _events(ref)
    assert sorted(x[FIELD_SEQ] for x in s) == sorted(x[FIELD_SEQ] for x in e)
    assert sorted(map(json.dumps, s)) == sorted(map(json.dumps, e))
    times = [x[FIELD_TIME] for x in s]
    assert times == sorted(times)
    assert list(s[0])[-3:] == ["_shape_table", "_shape_seq", "_shape_event_time"]


def test_max_events_is_the_earliest_events(tmp_path: Path) -> None:
    whole, part = tmp_path / "w.jsonl", tmp_path / "p.jsonl"
    assert main([*BASE, "--sink", "file", "-o", str(whole)]) == 0
    assert main([*BASE, "--sink", "file", "-o", str(part), "--max-events", "123"]) == 0
    assert part.read_bytes().splitlines() == whole.read_bytes().splitlines()[:123]


def test_short_flags_and_defaults(tmp_path: Path) -> None:
    long_, short = tmp_path / "l.jsonl", tmp_path / "s.jsonl"
    assert (
        main([*BASE, "--mode", "3nf", "--sink", "file", "-o", str(long_), "--max-events", "50"])
        == 0
    )
    assert (
        main(
            [
                "stream",
                "retail",
                "-s",
                "small",
                "--seed",
                "3",
                "-t",
                "order",
                "-m",
                "3nf",
                "--sink",
                "file",
                "-o",
                str(short),
                "--max-events",
                "50",
            ]
        )
        == 0
    )
    assert long_.read_bytes() == short.read_bytes()


def test_table_is_required_and_single(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["stream", "retail", "--scale", "small"])
    assert exc.value.code == 2
    assert main(["stream", "retail", "--scale", "small", "-t", "order", "-t", "customer"]) == 2
    assert "exactly once" in capsys.readouterr().err


def test_rate_defaults_to_ten_and_burst_needs_realtime(capsys) -> None:
    from shape.cli.main import _build_parser

    ns = _build_parser().parse_args(["stream", "retail", "--table", "order"])
    assert ns.rate == 10.0 and ns.by_event_time is True
    assert main([*BASE, "--burst", "1:1:2"]) == 2
    emit = _build_parser().parse_args(["emit", "retail"])
    assert emit.rate == 100.0 and not getattr(emit, "by_event_time", False)


@pytest.mark.realtime
@pytest.mark.parametrize(
    ("burst", "scheduled"),
    [((), 1000), (("--burst", "0:0.5:3"), 1500 + 500)],  # 3x the rate for the first half second
)
def test_realtime_rate_duration_and_burst(tmp_path: Path, capsys, burst, scheduled) -> None:
    """G6 (§10 `stream` row): a paced run, end to end. `--duration` stops it, `--rate` and
    `--burst` decide how many events were due by then, and what was sent is the head of the
    unpaced stream."""
    whole, out = tmp_path / "w.jsonl", tmp_path / "s.jsonl"
    assert main([*BASE, "--no-realtime", "--sink", "file", "-o", str(whole)]) == 0
    capsys.readouterr()
    paced = ["--realtime", "--rate", "1000", "--duration", "1", *burst]
    assert main([*BASE, *paced, "--sink", "file", "-o", str(out), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stopped_by"] == "duration" and report["complete"] is False
    assert 0.9 * scheduled <= report["events"] <= 1.1 * scheduled, report
    sent = out.read_bytes().splitlines()
    assert len(sent) == report["events"]
    assert sent == whole.read_bytes().splitlines()[: len(sent)]


def test_anomaly_fraction_is_honoured(tmp_path: Path) -> None:
    plain, anom = tmp_path / "p.jsonl", tmp_path / "a.jsonl"
    assert main([*BASE, "--sink", "file", "-o", str(plain)]) == 0
    assert main([*BASE, "--sink", "file", "-o", str(anom), "--anomaly-fraction", "0.1"]) == 0
    p = {x[FIELD_SEQ]: x for x in _events(plain)}
    a = {x[FIELD_SEQ]: x for x in _events(anom)}
    changed = sum(p[k] != a[k] for k in p)
    assert 0.05 * len(p) < changed < 0.15 * len(p)


def test_stream_resumes_from_its_checkpoint(tmp_path: Path) -> None:
    out, ck = tmp_path / "s.jsonl", tmp_path / "s.ck"
    whole = tmp_path / "w.jsonl"
    assert main([*BASE, "--sink", "file", "-o", str(whole), "--out-of-order", "0.1"]) == 0
    args = [
        *BASE,
        "--sink",
        "file",
        "-o",
        str(out),
        "--checkpoint",
        str(ck),
        "--out-of-order",
        "0.1",
    ]
    assert main([*args, "--max-events", "3000"]) == 0
    assert len(out.read_bytes().splitlines()) == 3000
    assert main(args) == 0
    assert out.read_bytes() == whole.read_bytes()


@pytest.mark.parametrize("threads", ["1", "3", "4"])
def test_large_batches_encode_identically_on_any_thread_count(monkeypatch, threads: str) -> None:
    from shape.streaming.emit import formats

    monkeypatch.setenv("SHAPE_THREADS", threads)
    n = formats.PARALLEL_ROWS + 1234
    batch = with_event_fields(_random_batch(random.Random(5), n), "t", 0)
    assert encode_batch(batch) == _encode_batch_rows(batch, "flat", "shape")


def test_a_batch_the_kernels_cannot_take_falls_back_to_the_row_encoder(monkeypatch) -> None:
    from shape.streaming.emit import formats

    def refuse(batch):
        raise pa.ArrowNotImplementedError("no kernel")

    monkeypatch.setattr(formats, "_encode_flat", refuse)
    batch = with_event_fields(_random_batch(random.Random(6), 20), "t", 0)
    assert encode_batch(batch) == _encode_batch_rows(batch, "flat", "shape")
