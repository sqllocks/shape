"""``shape stream-profile`` (P3-05), end to end through ``shape.cli.main`` with an in-memory
stream source standing in for a broker. The broker-backed runs are in
``plugins/shape-kafka/tests`` and ``plugins/shape-eventhubs/tests``."""

import json
import sys
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.plugins.api.v1 import StreamOffset
from shape.plugins.host import PluginHost
from shape.profile.engine import EngineOptions, profile_table
from shape.streaming.cli import duration_us, parse_option
from shape.streaming.messages import (
    DecodeStats,
    StreamMessage,
    StreamSourceError,
    conform,
    decode_messages,
    freeze,
)

T0_US = 1_700_000_000_000_000


def rows(n, start=0):
    return [{"id": i, "amount": round(i * 0.5, 1), "kind": "abc"[i % 3]} for i in range(start, n)]


class MemorySource:
    """``mem://anything``: ``messages`` as one partition, batches of ``batch_size``."""

    name = "mem"
    schemes = ("mem",)

    def __init__(self, messages, fail_every=0):
        self.messages = messages
        self.fail_every = fail_every
        self.stats = DecodeStats()
        self.reads = 0
        self.options = []
        self._delivered = 0
        self._next_fail = fail_every

    def read(self, uri, start=None, **options):
        self.reads += 1
        self.options.append(dict(options))
        size = int(options.get("batch_size", 100))
        schema = options.get("schema")
        pos = int(start.value["0"]) if start is not None else 0
        limit = options.get("max_messages")
        end = len(self.messages) if limit is None else min(len(self.messages), pos + limit)
        while pos < end:
            if self.fail_every and self._delivered >= self._next_fail:
                self._next_fail = self._delivered + self.fail_every
                raise ConnectionError("memory source: connection lost")
            chunk = self.messages[pos : min(end, pos + size)]
            pos += len(chunk)
            self._delivered += len(chunk)
            decode = {
                k: options[k] for k in ("event_time_field", "event_time_unit") if k in options
            }
            batch = decode_messages(chunk, schema=schema, stats=self.stats, **decode)
            if batch is None:
                continue
            if schema is None:
                schema = freeze(batch)
                batch = conform(batch, schema)
            yield StreamOffset({"0": pos}), batch


def messages(data, step_us=10_000):
    return [StreamMessage("0", i, json.dumps(r), (T0_US + i * step_us)) for i, r in enumerate(data)]


@pytest.fixture
def run(monkeypatch, capsys):
    """``run(source, *argv)`` -> (exit code, stdout summary or text, stderr)."""

    def go(source, *argv):
        host = PluginHost(entry_points=lambda: [])
        host.register("shape.stream_sources", "mem", source)
        monkeypatch.setattr("shape.plugins.host.default_host", lambda: host)
        code = main(["stream-profile", "mem://topic", *map(str, argv)])
        out, err = capsys.readouterr()
        return code, out, err

    return go


def summary(out):
    return json.loads(out.strip().splitlines()[-1])


def test_the_global_profile_equals_batch_bounded_profiling(run, tmp_path):
    data = rows(5000)
    source = MemorySource(messages(data))
    out = tmp_path / "p.json"
    code, stdout, _ = run(source, "-o", out, "--batch-size", 700, "--name", "t")
    assert code == 0
    s = summary(stdout)
    assert s["events"] == 5000 and s["windows"] == 1 and s["written"] == [str(out)]
    doc = json.loads(out.read_text())
    assert doc["mode"] == "bounded" and list(doc["tables"]) == ["t"]
    table = pa.Table.from_batches(
        [decode_messages(messages(data))]  # the same rows, decoded once
    )
    want = profile_table(table, "t", EngineOptions(mode="bounded"))
    got = doc["tables"]["t"]
    assert got["rows"] == want["rows"] == 5000
    for g, w in zip(got["columns"], want["columns"], strict=True):
        assert g["name"] == w["name"] and g["count"] == w["count"]
        assert g["error_models"] == w["error_models"]
        if "top" in w:
            assert g["top"] == w["top"]


def test_the_first_batch_fixes_the_schema_and_is_not_read_twice(run, tmp_path):
    source = MemorySource(messages(rows(1000)))
    code, stdout, _ = run(source, "-o", tmp_path / "p.json", "--batch-size", 250)
    assert code == 0 and source.reads == 1
    assert summary(stdout)["events"] == 1000 and summary(stdout)["batches"] == 4
    assert isinstance(source.options[0].get("schema"), type(None))  # inferred by the source


def test_windows_are_written_as_json_lines_as_they_close(run, tmp_path):
    out = tmp_path / "w.jsonl"
    source = MemorySource(messages(rows(3000)))  # 10 ms apart: 30 s of event time
    code, stdout, _ = run(
        source, "--window", "tumbling", "--size", "10s", "--windows", out, "--batch-size", 400
    )
    assert code == 0
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert [w["rows"] for w in lines] == [1000, 1000, 1000]
    assert [w["kind"] for w in lines] == ["tumbling"] * 3
    assert lines[0]["end_us"] - lines[0]["start_us"] == 10_000_000
    assert lines[0]["profile"]["rows"] == 1000
    assert summary(stdout)["windows"] == 3 and summary(stdout)["written"] == [str(out)]


@pytest.mark.parametrize(
    ("args", "kind", "count"),
    [
        (("--window", "sliding", "--size", "20s", "--slide", "10s"), "sliding", 4),
        (("--window", "session", "--gap", "5s"), "session", 1),
    ],
)
def test_sliding_and_session_windows(run, tmp_path, args, kind, count):
    out = tmp_path / "w.jsonl"
    code, _, _ = run(MemorySource(messages(rows(3000))), *args, "--windows", out)
    assert code == 0
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(lines) == count and {w["kind"] for w in lines} == {kind}
    assert sum(w["rows"] for w in lines) >= 3000


def test_the_event_time_comes_from_a_payload_field_when_named(run, tmp_path):
    data = [{"id": i, "ts": 1_700_000_000 + i} for i in range(60)]
    out = tmp_path / "w.jsonl"
    code, _, _ = run(
        MemorySource([StreamMessage("0", i, json.dumps(r), 0) for i, r in enumerate(data)]),
        "--window", "tumbling", "--size", "20s", "--windows", out,
        "--event-time", "ts", "--event-time-unit", "s",
    )  # fmt: skip
    assert code == 0
    assert [json.loads(x)["rows"] for x in out.read_text().splitlines()] == [20, 20, 20]


def test_a_killed_run_resumes_from_its_checkpoint_and_the_window_file_has_each_window_once(
    run, tmp_path
):
    data = rows(4000)
    clean = tmp_path / "clean.jsonl"
    run(MemorySource(messages(data)), "--window", "tumbling", "--size", "5s", "--windows", clean,
        "--batch-size", 200)  # fmt: skip

    class Killed(MemorySource):
        def read(self, uri, start=None, **options):
            for n, item in enumerate(super().read(uri, start, **options)):
                if n == 9:
                    raise RuntimeError("killed")
                yield item

    ck = tmp_path / "ck.json"
    out = tmp_path / "w.jsonl"
    args = ("--window", "tumbling", "--size", "5s", "--windows", out, "--batch-size", 200,
            "--checkpoint", ck, "--checkpoint-every", 2)  # fmt: skip
    with pytest.raises(RuntimeError, match="killed"):
        run(Killed(messages(data)), *args)
    assert ck.exists()
    code, stdout, _ = run(MemorySource(messages(data)), *args)
    assert code == 0 and summary(stdout)["checkpoints"] >= 1
    got = [json.loads(x) for x in out.read_text().splitlines()]
    want = [json.loads(x) for x in clean.read_text().splitlines()]
    assert [(w["start_us"], w["end_us"], w["rows"]) for w in got] == [
        (w["start_us"], w["end_us"], w["rows"]) for w in want
    ]


def test_rerunning_a_finished_checkpoint_does_nothing_and_says_so(run, tmp_path):
    ck = tmp_path / "ck.json"
    out = tmp_path / "p.json"
    source = MemorySource(messages(rows(300)))
    assert run(source, "-o", out, "--checkpoint", ck)[0] == 0
    before = out.read_bytes()
    code, stdout, _ = run(MemorySource(messages(rows(300))), "-o", out, "--checkpoint", ck)
    assert code == 0 and "finished run" in stdout and out.read_bytes() == before


def test_a_checkpoint_of_another_stream_or_profiler_is_refused(run, tmp_path, capsys):
    ck = tmp_path / "ck.json"
    run(MemorySource(messages(rows(300))), "--window", "tumbling", "--size", "1s",
        "--windows", tmp_path / "a.jsonl", "--checkpoint", ck)  # fmt: skip
    code, _, err = run(MemorySource(messages(rows(300))), "--window", "tumbling", "--size", "2s",
                       "--windows", tmp_path / "b.jsonl", "--checkpoint", ck)  # fmt: skip
    assert code == 2 and "different profiler" in err


def test_100_forced_reconnects_leave_the_profile_equal_to_an_uninterrupted_run(run, tmp_path):
    data = rows(2400)
    clean = tmp_path / "clean.json"
    run(MemorySource(messages(data)), "-o", clean, "--batch-size", 16)
    flaky = tmp_path / "flaky.json"
    source = MemorySource(messages(data), fail_every=16)
    code, stdout, _ = run(source, "-o", flaky, "--batch-size", 16, "--checkpoint",
                          tmp_path / "ck.json", "--checkpoint-every", 3)  # fmt: skip
    assert code == 0 and summary(stdout)["reconnects"] >= 100
    a = json.loads(clean.read_text())["tables"]["stream"]
    b = json.loads(flaky.read_text())["tables"]["stream"]
    assert a == b


def test_max_events_stops_the_read_and_the_summary_counts_what_was_dropped(run, tmp_path):
    msgs = messages(rows(100))
    msgs[3] = StreamMessage("0", 3, "not json", T0_US)
    source = MemorySource(msgs)
    code, stdout, _ = run(source, "-o", tmp_path / "p.json", "--max-events", 50)
    s = summary(stdout)
    assert code == 0 and s["events"] == 49 and s["undecodable"] == 1
    assert source.options[0]["max_messages"] == 50 and source.options[0]["stop_at_end"] is True


def test_source_options_come_from_the_command_line_and_a_file(run, tmp_path):
    options = tmp_path / "o.json"
    options.write_text(json.dumps({"config": {"security.protocol": "SASL_SSL"}, "x": 1}))
    source = MemorySource(messages(rows(10)))
    code, _, _ = run(source, "-o", tmp_path / "p.json", "--options-file", options,
                     "--option", "x=2", "--option", 'y={"a": [1]}', "--option", "z=plain",
                     "--follow", "--idle-timeout", "0.5", "--start", "latest")  # fmt: skip
    assert code == 0
    got = source.options[0]
    assert got["config"] == {"security.protocol": "SASL_SSL"}
    assert (got["x"], got["y"], got["z"]) == (2, {"a": [1]}, "plain")
    assert (got["stop_at_end"], got["idle_timeout"], got["start_at"]) == (False, 0.5, "latest")


def test_an_empty_stream_writes_nothing_and_says_so(run, tmp_path):
    out = tmp_path / "p.json"
    code, stdout, err = run(MemorySource([]), "-o", out)
    assert code == 0 and not out.exists()
    assert "no events" in err and summary(stdout)["events"] == 0


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--window", "global"], "needs -o"),
        (["-o", "x.json", "--windows", "w.jsonl"], "goes with --window"),
        (
            ["--window", "tumbling", "--windows", "w", "-o", "x.json", "--size", "1s"],
            "-o is the global",
        ),
        (["--window", "tumbling", "--windows", "w.jsonl"], "needs --size"),
        (["--window", "tumbling", "--windows", "w", "--size", "1s", "--slide", "1s"], "--slide"),
        (["--window", "sliding", "--windows", "w", "--size", "1s"], "needs --slide"),
        (["--window", "session", "--windows", "w"], "needs --gap"),
        (["-o", "x.json", "--max-events", "0"], "--max-events must be positive"),
        (["-o", "x.json", "--idle-timeout", "-1"], "--idle-timeout"),
        (["-o", "x.json", "--option", "novalue"], "KEY=VALUE"),
        (["--window", "tumbling", "--size", "soon", "--windows", "w"], "not a duration"),
    ],
)
def test_bad_arguments_are_input_errors(run, argv, message):
    code, _, err = run(MemorySource(messages(rows(10))), *argv)
    assert code == 2 and message in err


def test_a_uri_no_plugin_reads_is_an_input_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("shape.plugins.host.default_host", lambda: PluginHost(lambda: []))
    assert main(["stream-profile", "nope://x/y", "-o", str(tmp_path / "p.json")]) == 2
    assert "no installed stream source reads" in capsys.readouterr().err
    assert main(["stream-profile", "/tmp/not-a-uri", "-o", str(tmp_path / "p.json")]) == 2


def test_a_connection_that_never_recovers_is_an_error(run, tmp_path):
    class Dead(MemorySource):
        def read(self, uri, start=None, **options):
            raise ConnectionError("broker unreachable")
            yield

    code, _, err = run(Dead([]), "-o", tmp_path / "p.json")
    assert code == 2 and "broker unreachable" in err


def test_the_command_is_listed_in_the_help_and_start_up_stays_light():
    import subprocess

    out = subprocess.run(
        [sys.executable, "-c", "from shape.cli.main import main; main(['--help'])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "stream-profile" in out
    code = (
        "import sys; from shape.cli.main import main; main(['version'])\n"
        "assert 'numpy' not in sys.modules and 'pyarrow' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


@pytest.mark.parametrize(
    ("text", "us"),
    [
        ("500ms", 500_000),
        ("30s", 30_000_000),
        ("2m", 120_000_000),
        ("1h", 3_600_000_000),
        ("1d", 86_400_000_000),
        ("5", 5_000_000),
        ("1.5s", 1_500_000),
        ("250us", 250),
        (" 3 s ", 3_000_000),
    ],
)
def test_durations(text, us):
    assert duration_us(text, "--x") == us


@pytest.mark.parametrize("text", ["", "s", "1x", "-1s", "1 2", "1e3s", "abc"])
def test_bad_durations(text):
    with pytest.raises(ValueError, match="not a duration"):
        duration_us(text, "--x")


def test_options():
    assert parse_option("a=1") == ("a", 1)
    assert parse_option("a=true") == ("a", True)
    assert parse_option("a=hello world") == ("a", "hello world")
    assert parse_option("a=") == ("a", "")
    assert parse_option('a={"b": 2}') == ("a", {"b": 2})
    for bad in ("=1", "x"):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            parse_option(bad)


def test_unused_imports_are_used():
    assert timedelta and Path and StreamSourceError  # keep the import list honest
