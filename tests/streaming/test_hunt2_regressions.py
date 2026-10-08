"""Regression tests for the defects the second streaming bug hunt (HUNT2-streaming) found; each
test names its issue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.generation import load_target
from shape.cli.main import main
from shape.generation.engine import Engine
from shape.streaming.emit import (
    AnomalyInjector,
    AnswerKey,
    EmitConfig,
    EmitRunner,
    EventPlan,
    read_answer_key,
    resolve_mutators,
)
from shape.streaming.emit.sinks import MemorySink

FAULTS = [
    "--out-of-order",
    "0.2",
    "--ooo-window",
    "50",
    "--anomaly-fraction",
    "0.1",
    "--duplicate-fraction",
    "0.1",
    "--duplicate-window",
    "30",
    "--poison-fraction",
    "0.05",
    "--seed",
    "7",
    "--batch-events",
    "40",
]


def _emit(out: Path, key: Path, *extra: str) -> int:
    args = ["emit", "retail", "--scale", "fabric_demo", *FAULTS, "--sink", "file", "-o", str(out)]
    return main([*args, "--answer-key", str(key), *extra])


def _idents(path: Path) -> set[tuple[str, str, int]]:
    return {(r["kind"], r["table"], r["seq"]) for r in read_answer_key(str(path))}


def test_694_a_resumed_run_keeps_the_answer_key_of_the_first_part(tmp_path, capsys):
    whole_out, whole_key = tmp_path / "whole.jsonl", tmp_path / "whole.key"
    assert _emit(whole_out, whole_key, "--fresh") == 0
    part_out, part_key = tmp_path / "part.jsonl", tmp_path / "part.key"
    assert _emit(part_out, part_key, "--max-events", "333", "--fresh") == 0
    first_part = _idents(part_key)
    assert _emit(part_out, part_key) == 0  # resumes from the checkpoint
    capsys.readouterr()
    assert first_part <= _idents(part_key), "the first run's records were erased"
    assert _idents(part_key) == _idents(whole_key)
    # every duplicate in the stream is in the key
    seen: dict[tuple[str, int], int] = {}
    for line in part_out.read_bytes().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        k = (e["_shape_table"], e["_shape_seq"])
        seen[k] = seen.get(k, 0) + 1
    duplicated = {k for k, n in seen.items() if n > 1}
    assert duplicated <= {(t, s) for kind, t, s in _idents(part_key) if kind == "duplicate"}


def test_694_a_fresh_run_still_starts_the_answer_key_empty(tmp_path):
    key = tmp_path / "k.key"
    key.write_text('{"kind": "late", "table": "stale", "seq": 1}\n')
    assert _emit(tmp_path / "e.jsonl", key, "--max-events", "50", "--fresh") == 0
    assert all(r["table"] != "stale" for r in read_answer_key(str(key)))


def test_695_the_answer_key_names_only_events_the_run_delivered(tmp_path):
    out, key = tmp_path / "k.jsonl", tmp_path / "k.key"
    args = ["emit", "retail", "--scale", "fabric_demo", "--anomaly-fraction", "0.2"]
    args += ["--out-of-order", "0.3", "--ooo-window", "20", "--seed", "3", "--sink", "file"]
    args += ["-o", str(out), "--answer-key", str(key), "--max-events", "100", "--fresh"]
    assert main(args) == 0
    sent = {
        (e["_shape_table"], e["_shape_seq"]) for e in map(json.loads, out.read_text().splitlines())
    }
    assert len(sent) == 100
    records = read_answer_key(str(key))
    assert records, "the faults of the delivered events are listed"
    assert [r for r in records if (r["table"], r["seq"]) not in sent] == []


def test_695_a_complete_run_lists_every_fault():
    def run(staged: bool) -> set[tuple[str, str, int]]:
        engine = Engine(load_target("retail", None), scale="fabric_demo", seed=5)
        key = AnswerKey(staged=staged)
        injector = AnomalyInjector(0.1, resolve_mutators(()), engine.seed)
        plan = EventPlan(engine, out_of_order=0.2, ooo_window=30, anomaly=injector, answer_key=key)
        EmitRunner(plan, MemorySink(), EmitConfig(batch_events=64)).run()
        return {(r["kind"], r["table"], r["seq"]) for r in key.records}

    assert run(staged=True) == run(staged=False) != set()


def test_697_input_without_a_decodable_event_is_an_error(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n[1,2]\n42\n\n")
    out = tmp_path / "out.json"
    assert main(["stream-profile", str(bad), "-o", str(out)]) == 2
    err = capsys.readouterr().err
    assert "3" in err and "undecodable" in err and "--option format=" in err
    assert not out.exists()


def test_697_empty_and_blank_input_is_still_no_events(tmp_path, capsys):
    for text in ("", "\n\n  \n"):
        empty = tmp_path / "empty.jsonl"
        empty.write_text(text)
        assert main(["stream-profile", str(empty), "-o", str(tmp_path / "o.json")]) == 0
        assert "no events" in capsys.readouterr().err


def test_697_one_decodable_event_among_bad_lines_is_profiled(tmp_path, capsys):
    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text('garbage\n{"v": 1}\n[2]\n')
    assert main(["stream-profile", str(mixed), "-o", str(tmp_path / "o.json")]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["events"] == 1 and summary["undecodable"] == 2


def _poisoned(values: dict, *, envelope: str = "flat", time: bool = False) -> bytes:
    import pyarrow as pa

    from shape.streaming.emit.formats import (
        FIELD_POISON,
        FIELD_SEQ,
        FIELD_TABLE,
        FIELD_TIME,
        encode_batch,
    )

    cols = {**{k: [v] for k, v in values.items()}, FIELD_TABLE: ["t"], FIELD_SEQ: [7]}
    if time:
        cols[FIELD_TIME] = ["2024-01-01T00:00:00"]
    cols[FIELD_POISON] = [True]
    return encode_batch(pa.RecordBatch.from_pydict(cols), envelope).rstrip(b"\n")


def test_699_a_poison_event_is_not_json_but_keeps_its_key():
    for time in (False, True):
        line = _poisoned({"name": "x" * 30}, time=time)
        with pytest.raises(ValueError):
            json.loads(line)
        assert b'"_shape_table":"t","_shape_seq":7' in line


def test_699_a_poison_event_is_valid_utf8_whatever_it_holds():
    for n in range(1, 80):
        for time in (False, True):
            _poisoned({"name": "é" * n}, time=time).decode("utf-8")
            _poisoned({"name": "☃x" * n}, time=time).decode("utf-8")


def test_699_a_poison_cloudevent_keeps_its_id():
    line = _poisoned({"name": "x" * 30}, envelope="cloudevents")
    with pytest.raises(ValueError):
        json.loads(line)
    assert b'"id":"t/7"' in line


def test_699_poison_stays_a_strict_prefix():
    from shape.streaming.emit.formats import poison_body

    for body in (b'{"a":1}', b'{"_shape_seq":3}', b'{"x":"\xc3\xa9","_shape_seq":10,"b":2}'):
        cut = poison_body(body)
        assert body.startswith(cut) and len(cut) < len(body)


def test_707_a_window_ending_after_year_9999_is_written(tmp_path, capsys):
    src = tmp_path / "far.jsonl"
    src.write_text(
        '{"_shape_event_time":"2024-01-01T00:00:00Z","v":1}\n'
        '{"_shape_event_time":"9999-12-31T12:00:00Z","v":2}\n'
    )
    out = tmp_path / "w.jsonl"
    args = ["stream-profile", str(src), "--window", "tumbling", "--size", "1d"]
    assert main([*args, "--windows", str(out)]) == 0
    windows = [json.loads(x) for x in out.read_text().splitlines()]
    assert [w["rows"] for w in windows] == [1, 1]
    last = windows[-1]
    assert last["start"] == "9999-12-31T00:00:00+00:00"
    assert last["end"] == "9999-12-31T23:59:59.999999+00:00"  # clamped text ...
    assert last["end_us"] - last["start_us"] == 86_400_000_000  # ... exact integers


def test_707_window_bounds_outside_the_calendar_do_not_raise():
    from shape.streaming.runtime import WindowProfile

    big = 2**62
    w = WindowProfile("tumbling", -big, big, 0, {})
    doc = w.to_dict()
    assert doc["start_us"] == -big and doc["end_us"] == big
    assert doc["start"].startswith("0001-01-01") and doc["end"].startswith("9999-12-31")
    assert w.start_time.year == 1 and w.end_time.year == 9999


# ---- persisted formats declare an integer version (#678, #708) ------------------------------


def _checkpoint_run(tmp_path, *extra):
    src = tmp_path / "ev.jsonl"
    if not src.exists():
        src.write_text(
            "".join(
                json.dumps({"_shape_event_time": f"2024-01-01T00:{i:02d}:00Z", "v": i}) + "\n"
                for i in range(40)
            )
        )
    ck = tmp_path / "ck.json"
    args = ["stream-profile", str(src), "--window", "tumbling", "--size", "10m"]
    args += ["--windows", str(tmp_path / f"w{len(list(tmp_path.glob('w*')))}.jsonl")]
    return main([*args, "--checkpoint", str(ck), *extra]), ck


def test_678_the_stream_checkpoint_and_its_window_snapshot_declare_a_version(tmp_path):
    code, ck = _checkpoint_run(tmp_path)
    assert code == 0
    doc = json.loads(ck.read_text())
    assert doc["format"] == "shape-stream-checkpoint-v1" and doc["version"] == 1
    assert doc["profiler"]["format"] == "shape-stream-window-v1" and doc["profiler"]["version"] == 1
    assert doc["shape_version"] and doc["min_shape_version"]


def test_678_a_checkpoint_of_a_newer_version_is_refused_as_newer(tmp_path, capsys):
    _, ck = _checkpoint_run(tmp_path)
    doc = json.loads(ck.read_text())
    doc["finished"] = False
    doc["profiler"]["finished"] = False
    doc["version"] = 2
    ck.write_text(json.dumps(doc))
    capsys.readouterr()
    assert _checkpoint_run(tmp_path)[0] == 2
    err = capsys.readouterr().err
    assert "version 2" in err and "upgrade" in err.lower() and "not a stream" not in err


def test_678_a_newer_window_snapshot_is_refused_as_newer():
    import pyarrow as pa

    from shape.compat import UnsupportedVersionError
    from shape.streaming.runtime import GlobalProfiler, restore_profiler

    snap = GlobalProfiler(pa.schema([("x", pa.int64())])).snapshot()
    assert snap["version"] == 1
    restore_profiler(snap)
    snap["version"] = 2
    with pytest.raises(UnsupportedVersionError, match="version 2"):
        restore_profiler(snap)
    with pytest.raises(ValueError, match="version 2"):  # still the ValueError callers catch
        restore_profiler(snap)


def test_678_a_checkpoint_written_before_versions_still_resumes(tmp_path, capsys):
    _, ck = _checkpoint_run(tmp_path)
    doc = json.loads(ck.read_text())
    for d in (doc, doc["profiler"]):
        for key in ("version", "shape_version", "min_shape_version"):
            d.pop(key, None)
    doc["finished"] = doc["profiler"]["finished"] = False
    ck.write_text(json.dumps(doc))
    capsys.readouterr()
    assert _checkpoint_run(tmp_path)[0] == 0


def test_678_a_damaged_checkpoint_names_the_file_and_says_what_to_do(tmp_path, capsys):
    src = tmp_path / "ev.jsonl"
    src.write_text("".join(json.dumps({"v": i}) + "\n" for i in range(10)))
    ck = tmp_path / "ck.json"
    args = ["stream-profile", str(src), "-o", str(tmp_path / "o.json"), "--checkpoint", str(ck)]
    assert main(args) == 0
    doc = json.loads(ck.read_text())
    doc["profiler"]["finished"] = False
    doc["profiler"]["state"]["state"] = "AAAA"
    ck.write_text(json.dumps(doc))
    capsys.readouterr()
    assert main(args) == 2
    err = capsys.readouterr().err
    assert str(ck) in err and "cannot read" in err and "error: error" not in err
    assert "--checkpoint" in err


def test_708_dedupe_and_sketch_snapshots_declare_a_version():
    import numpy as np

    from shape.compat import UnsupportedVersionError
    from shape.streaming.dedupe import Deduplicator
    from shape.streaming.keyed import KeyedSketches

    d = Deduplicator()
    d.filter(np.array([1, 2, 2]))
    s = KeyedSketches(10)
    for snap, cls in ((d.snapshot(), Deduplicator), (s.snapshot(), KeyedSketches)):
        assert snap["version"] == 1
        cls.restore(snap)
        old = {k: v for k, v in snap.items() if k not in ("version", "shape_version")}
        cls.restore(old)  # a snapshot from before the declaration
        with pytest.raises(UnsupportedVersionError):
            cls.restore({**snap, "version": 2})


def test_708_keyed_state_snapshots_declare_format_and_version_and_survive_json():
    from shape.compat import UnsupportedVersionError
    from shape.streaming.keyed import KeyedState, PartitionedKeyedState

    s = KeyedState(60, 10)
    s.put(("customer", 1), {"n": 1}, 5.0)
    s.put("plain", 2, 5.0)
    s.put(7, 3, 5.0)
    snap = json.loads(json.dumps(s.snapshot()))
    assert snap["format"] == "shape-keyed-state-v1" and snap["version"] == 1
    r = KeyedState.restore(snap)
    assert (
        r.get(("customer", 1), 6.0) == {"n": 1} and r.get("plain", 6.0) == 2 and r.get(7, 6.0) == 3
    )
    with pytest.raises(UnsupportedVersionError):
        KeyedState.restore({**snap, "version": 2})
    # a snapshot from before the declaration (no format, no version) restores
    legacy = {k: v for k, v in s.snapshot().items() if k in ("ttl_seconds", "max_keys", "items")}
    assert KeyedState.restore(legacy).get("plain", 6.0) == 2

    p = PartitionedKeyedState(4, 60)
    p.put(("x", 2), 1, 1.0)
    psnap = json.loads(json.dumps(p.snapshot()))
    assert psnap["format"] == "shape-keyed-state-partitioned-v1" and psnap["version"] == 1
    assert PartitionedKeyedState.restore(psnap).get(("x", 2), 2.0) == 1


def test_678_the_emit_checkpoint_declares_a_version(tmp_path, capsys):
    out = tmp_path / "e.jsonl"
    args = ["emit", "retail", "--scale", "fabric_demo", "--max-events", "20", "--sink", "file"]
    assert main([*args, "-o", str(out)]) == 0
    ck = Path(f"{out}.checkpoint")
    doc = json.loads(ck.read_text())
    assert doc["format"] == "shape-emit-v1" and doc["version"] == 1
    doc["version"] = 2
    ck.write_text(json.dumps(doc))
    capsys.readouterr()
    assert main([*args, "-o", str(out), "--max-events", "40"]) == 2
    err = capsys.readouterr().err
    assert "version 2" in err and "upgrade" in err.lower()


def test_709_a_csv_with_a_repeated_column_name_is_refused(tmp_path, capsys):
    dup = tmp_path / "dup.csv"
    dup.write_text("a,b,a\n1,2,3\n")
    assert main(["stream-profile", str(dup), "-o", str(tmp_path / "o.json")]) == 2
    err = capsys.readouterr().err
    assert "'a'" in err and "more than once" in err and "dup.csv" in err
    assert not (tmp_path / "o.json").exists()


def test_709_a_parquet_file_with_a_repeated_column_name_is_refused(tmp_path, capsys):
    import pyarrow as pa
    import pyarrow.parquet as pq

    dup = tmp_path / "dup.parquet"
    pq.write_table(pa.table([[1], [2]], names=["a", "a"]), dup)
    assert main(["stream-profile", str(dup), "-o", str(tmp_path / "o.json")]) == 2
    assert "more than once" in capsys.readouterr().err


def test_709_distinct_names_still_profile(tmp_path, capsys):
    ok = tmp_path / "ok.csv"
    ok.write_text("a,b,A\n1,2,3\n")  # names differing in case are different names
    assert main(["stream-profile", str(ok), "-o", str(tmp_path / "o.json")]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 1


def test_696_emit_to_a_closed_pipe_stops_quietly():
    import subprocess
    import sys

    code = "import sys; from shape.cli.main import main; sys.exit(main())"
    cmd = [sys.executable, "-c", code, "emit", "retail", "--scale", "fabric_demo", "--seed", "1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    first = proc.stdout.readline()
    proc.stdout.close()  # the reader (`head -1`) goes away
    err = proc.stderr.read().decode()
    assert proc.wait(timeout=60) == 0, err
    assert json.loads(first)["_shape_seq"] == 0
    assert "Broken pipe" not in err and "error" not in err and "Traceback" not in err
    assert "reader closed" in err


def test_696_a_gone_reader_is_not_retried():
    from shape.streaming.emit.runtime import EmitConfig, EmitRunner
    from shape.streaming.emit.sinks import ReaderGone

    class Gone:
        calls = 0

        def send(self, batch):
            Gone.calls += 1
            raise ReaderGone("the reader closed")

        def flush(self): ...
        def close(self): ...

    engine = Engine(load_target("retail", None), scale="fabric_demo", seed=1)
    report = EmitRunner(EventPlan(engine), Gone(), EmitConfig(retries=3)).run()
    assert Gone.calls == 1 and report.retries == 0
    assert report.stopped_by == "reader-closed" and report.events == 0 and not report.complete


def _read_typed(body_values, typ):
    import pyarrow as pa

    from shape.streaming.messages import (
        EVENT_TIME_TYPE,
        DecodeStats,
        StreamMessage,
        decode_messages,
    )

    schema = pa.schema([("x", typ), ("_shape_event_time", EVENT_TIME_TYPE)])
    stats = DecodeStats()
    msgs = [StreamMessage("0", i, json.dumps({"x": v})) for i, v in enumerate(body_values)]
    batch = decode_messages(msgs, schema=schema, stats=stats)
    return (None if batch is None else batch.to_pydict()["x"]), stats.rejected


def test_698_a_boolean_does_not_fit_a_numeric_column():
    import pyarrow as pa

    assert _read_typed([True, 5], pa.int64()) == ([5], 1)
    assert _read_typed([False, 2.5], pa.float64()) == ([2.5], 1)
    assert _read_typed([True], pa.int64()) == (None, 1)
    assert _read_typed([True, False], pa.bool_()) == ([True, False], 0)  # it fits a boolean column


def test_735_a_fractional_number_does_not_fit_an_integer_column():
    import pyarrow as pa

    assert _read_typed([1.5, -1.9, 2.0], pa.int64()) == ([2], 2)
    assert _read_typed([1e30, 7], pa.int64()) == ([7], 1)
    assert _read_typed([2**70, 7], pa.int64()) == ([7], 1)
    assert _read_typed([3, None, 4.0], pa.int32()) == ([3, None, 4], 0)
    assert _read_typed([1, 2.25], pa.float64()) == ([1.0, 2.25], 0)  # a float column takes both


class _Down:
    def read(self, uri, start=None, **options):
        raise ConnectionError("broker down")
        yield


def _consumer(source, **kw):
    import pyarrow as pa

    from shape.streaming.consumer import StreamConsumer
    from shape.streaming.runtime import GlobalProfiler

    return StreamConsumer(
        source, "kafka://x/t", GlobalProfiler(pa.schema([("x", pa.int64())])), **kw
    )


def test_700_consecutive_failed_reconnects_wait_longer_each_time():
    pauses: list[float] = []
    c = _consumer(_Down(), max_attempts=6, backoff=1.0, max_backoff=3.0, sleep=pauses.append)
    with pytest.raises(ConnectionError):
        list(c.run())
    assert pauses == [1.0, 2.0, 3.0, 3.0] and c.reconnects == 6  # the first retry is immediate


def test_700_a_reconnect_after_progress_does_not_wait():
    import pyarrow as pa

    class Blip:
        calls = 0

        def read(self, uri, start=None, **options):
            Blip.calls += 1
            if Blip.calls == 1:
                from shape.plugins.api.v1 import StreamOffset

                yield StreamOffset({"0": 1}), pa.record_batch([pa.array([1])], names=["x"])
                raise ConnectionError("blip")

    pauses: list[float] = []
    c = _consumer(Blip(), backoff=1.0, sleep=pauses.append)
    list(c.run())
    assert c.reconnects == 1 and pauses == []


def test_700_the_library_default_is_unchanged_and_the_command_backs_off():
    from shape.streaming import cli

    c = _consumer(_Down(), max_attempts=3)
    assert c.backoff == 0.0
    assert cli.RECONNECT_BACKOFF > 0


def _live_emit(tmp_path, *extra):
    out = tmp_path / "e.jsonl"
    args = ["emit", "retail", "--scale", "fabric_demo", "--max-events", "30", "--sink", "file"]
    args += ["-o", str(out), "--live-target", "retail", "--fresh", *extra]
    return main(args), out


def test_702_an_unusable_live_report_is_refused_before_any_event(tmp_path, capsys):
    code, out = _live_emit(tmp_path, "--live-report", str(tmp_path / "r.txt"))
    assert code == 2
    assert "cannot tell the format" in capsys.readouterr().err
    assert not out.exists() and not Path(f"{out}.checkpoint").exists()


def test_702_a_live_output_that_is_a_directory_is_refused_before_any_event(tmp_path, capsys):
    (tmp_path / "dir.json").mkdir()
    for flag in ("--live-report", "--live-profile"):
        code, out = _live_emit(tmp_path, flag, str(tmp_path / "dir.json"))
        assert code == 2 and not out.exists()
        assert "is a directory" in capsys.readouterr().err


def test_702_live_outputs_in_a_new_folder_are_written(tmp_path):
    report = tmp_path / "new" / "live" / "r.json"
    profile = tmp_path / "new" / "p.json"
    code, _ = _live_emit(tmp_path, "--live-report", str(report), "--live-profile", str(profile))
    assert code == 0 and report.exists() and profile.exists()


def test_741_live_options_without_a_live_target_are_refused(tmp_path, capsys):
    base = [
        "emit",
        "retail",
        "--scale",
        "fabric_demo",
        "--max-events",
        "10",
        "--sink",
        "file",
        "--fresh",
    ]
    for extra in (
        ["--live-report", str(tmp_path / "r.json")],
        ["--live-profile", str(tmp_path / "p.json")],
        ["--live-alerts", str(tmp_path / "a.jsonl")],
        ["--live-fail"],
        ["--live-min-column-score", "50"],
        ["--no-live-profile"],
    ):
        out = tmp_path / "e.jsonl"
        assert main([*base, "-o", str(out), *extra]) == 2, extra
        err = capsys.readouterr().err
        assert "--live-target" in err and extra[0] in err
        assert not out.exists()
    # with a target the same options are accepted
    assert main(
        [*base, "-o", str(tmp_path / "ok.jsonl"), "--live-target", "retail", "--live-fail"]
    ) in (0, 1)


def test_710_argument_errors_say_what_is_wrong_and_what_to_pass():
    from shape.streaming import ShapeMonitor
    from shape.streaming.keyed import KeyedState, PartitionedKeyedState
    from shape.streaming.online import OnlineShape

    cases = [
        (lambda: ShapeMonitor(object(), every=0), "every must be at least 1"),
        (lambda: OnlineShape(max_buffer=0), "max_buffer must be at least 1"),
        (lambda: PartitionedKeyedState(0, 60), "partitions must be at least 1"),
        (lambda: KeyedState(0, 5), "ttl_seconds must be positive"),
        (lambda: KeyedState(60, 0), "max_keys must be at least 1"),
    ]
    for build, text in cases:
        with pytest.raises(ValueError, match=text):
            build()
    ShapeMonitor(object(), every=1)  # the boundary is accepted
    OnlineShape(max_buffer=1)
    KeyedState(0.001, 1)


def test_118_negative_or_zero_delivery_options_are_refused(tmp_path, capsys):
    base = [
        "emit",
        "retail",
        "--scale",
        "fabric_demo",
        "--max-events",
        "10",
        "--sink",
        "file",
        "--fresh",
    ]
    for extra, text in (
        (["--poison-fraction", "-1"], "poison fraction"),
        (["--duplicate-fraction", "-0.5"], "duplicate fraction"),
        (["--retries", "-1"], "retries"),
        (["--checkpoint-every", "0"], "checkpoint_every"),
        (["--checkpoint-seconds", "-1"], "checkpoint_seconds"),
    ):
        out = tmp_path / "e.jsonl"
        assert main([*base, "-o", str(out), *extra]) == 2, extra
        assert text in capsys.readouterr().err
        assert not out.exists()  # refused before the sink was opened
    for extra in (
        ["--poison-fraction", "0"],
        ["--retries", "0"],
        ["--checkpoint-every", "1"],
        ["--checkpoint-seconds", "0"],
    ):
        assert main([*base, "-o", str(tmp_path / "ok.jsonl"), *extra]) == 0, extra


def test_298_compressed_state_is_inflated_within_its_declared_size():
    import base64
    import zlib

    import numpy as np

    from shape.streaming.checkpoint import inflate
    from shape.streaming.dedupe import Deduplicator
    from shape.streaming.keyed import KeyedSketches, _pack, _unpack

    raw = zlib.compress(bytes(4 << 20))  # 4 MiB of zeros in a few KiB
    assert len(raw) < 10_000
    assert inflate(raw, 4 << 20) == bytes(4 << 20)  # exactly the limit is fine
    with pytest.raises(ValueError, match="size limit"):
        inflate(raw, (4 << 20) - 1)
    with pytest.raises(ValueError, match="truncated"):
        inflate(raw[:-8], 8 << 20)
    # an array field holds at most max_items values of its dtype
    text = base64.b64encode(raw).decode()
    with pytest.raises(ValueError, match="size limit"):
        _unpack(text, np.int64, max_items=1000)
    assert len(_unpack(_pack(np.arange(5)), np.int64, max_items=5)) == 5
    # restore bounds the arrays of a snapshot by the cap the snapshot declares
    d = Deduplicator(max_keys=10)
    d.filter(np.arange(5))
    snap = d.snapshot()
    snap["runs"][0]["keys"] = text
    with pytest.raises(ValueError, match="size limit"):
        Deduplicator.restore(snap)
    s = KeyedSketches(10)
    snap = s.snapshot()
    snap["arrays"]["count"] = text
    with pytest.raises(ValueError, match="size limit"):
        KeyedSketches.restore(snap)
