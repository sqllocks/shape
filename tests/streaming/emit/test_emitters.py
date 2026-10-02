"""The core emitters (console, file, jsonl) and the emitter contract they and the plugins share
(P5-02)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from shape.builtins.emitters import ConsoleEmitter, FileEmitter, JsonlEmitter, uri_path
from shape.errors import ShapeError
from shape.plugins import kit
from shape.plugins.host import default_host
from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan, contract, read_events
from shape.streaming.emit.formats import encode_events

from .conftest import make_engine

pytestmark = pytest.mark.contract


class FileHarness:
    """A file emitter whose failures and congestion are injected: a file does not refuse a
    write, so a wrapper raises a transient ``OSError`` or waits, as a full disk or a slow volume
    would."""

    def __init__(self, directory: Path, emitter_cls: Any = FileEmitter) -> None:
        self.dir = directory
        self.cls = emitter_cls
        self.uri = (
            f"file://{directory}/events.jsonl"
            if emitter_cls is FileEmitter
            else f"jsonl://{directory}/out"
        )
        self.failures = 0
        self.full = 0
        self.hits = 0

    def make(self) -> Any:
        harness = self

        class Faulty(self.cls):  # type: ignore[name-defined, misc]
            def emit(self, uri: str, batches: Any, **options: Any) -> int:
                if harness.failures > 0:
                    harness.failures -= 1
                    raise OSError("injected transient failure")
                if harness.full > 0:
                    harness.full -= 1
                    harness.hits += 1
                    time.sleep(0.005)  # the volume is slow: wait, do not drop or fail
                return super().emit(uri, batches, **options)  # type: ignore[no-any-return]

        return Faulty()

    def delivered(self) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        files = (
            [self.dir / "events.jsonl"]
            if self.cls is FileEmitter
            else sorted((self.dir / "out").glob("*.jsonl"))
        )
        for f in files:
            if not f.exists():
                continue
            for line in f.read_bytes().splitlines():
                body = json.loads(line)
                data = body.get("data", body)
                out.append((f"{data['_shape_table']}/{data['_shape_seq']}", line))
        return out

    def inject_failures(self, n: int) -> None:
        self.failures = n

    def congest(self, n: int) -> None:
        self.full = n

    def congestion_hits(self) -> int:
        return self.hits


@pytest.fixture
def plan() -> Any:
    return lambda: EventPlan(make_engine(), tables=["order_line"])


@pytest.fixture(params=[FileEmitter, JsonlEmitter], ids=["file", "jsonl"])
def new_harness(request: Any, tmp_path: Path) -> Any:
    count = [0]

    def make() -> FileHarness:
        count[0] += 1
        d = tmp_path / f"h{count[0]}"
        d.mkdir()
        return FileHarness(d, request.param)

    return make


def test_idempotency_key(new_harness: Any, plan: Any) -> None:
    contract.check_idempotency_key(new_harness, plan)


def test_at_least_once(new_harness: Any, plan: Any) -> None:
    contract.check_at_least_once(new_harness, plan)


def test_backpressure(new_harness: Any, plan: Any) -> None:
    contract.check_backpressure(new_harness, plan)


def test_checkpoint(new_harness: Any, plan: Any, tmp_path: Path) -> None:
    contract.check_checkpoint(new_harness, plan, directory=tmp_path)


def test_the_core_emitters_are_registered_and_pass_the_kit(tmp_path: Path, plan: Any) -> None:
    host = default_host()
    batch = next(iter(plan().blocks(0))).batch.slice(0, 20)
    for name, uri in (
        ("console", "console://"),
        ("file", f"file://{tmp_path}/a.jsonl"),
        ("jsonl", f"jsonl://{tmp_path}/d"),
    ):
        emitter = host.get("shape.emitters", name)
        kit.check_emitter(emitter, uri, [batch])
        emitter.close()


def test_file_emitter_resumes_after_a_torn_line(tmp_path: Path, plan: Any) -> None:
    out = tmp_path / "e.jsonl"
    uri = f"file://{out}"
    batch = next(iter(plan().blocks(0))).batch.slice(0, 10)
    e = FileEmitter()
    e.emit(uri, [batch])
    e.close()
    with open(out, "ab") as f:
        f.write(b'{"torn":')
    e = FileEmitter()
    e.emit(uri, [batch], resuming=True)
    e.close()
    assert len(list(read_events(str(out)))) == 20  # the torn line was cut


def test_a_new_run_without_resuming_starts_the_file_again(tmp_path: Path, plan: Any) -> None:
    out = tmp_path / "e.jsonl"
    batch = next(iter(plan().blocks(0))).batch.slice(0, 10)
    for _ in range(2):
        e = FileEmitter()
        e.emit(f"file://{out}", [batch])
        e.close()
    assert len(list(read_events(str(out)))) == 10


def test_jsonl_emitter_splits_by_table(tmp_path: Path) -> None:
    engine = make_engine()
    p = EventPlan(engine, tables=["customer", "order_line"])
    sink_dir = tmp_path / "t"
    from shape.streaming.emit import EmitterSink

    sink = EmitterSink(JsonlEmitter(), f"jsonl://{sink_dir}")
    EmitRunner(p, sink, EmitConfig(max_events=p.counts["customer"] + 50)).run()
    assert sorted(f.name for f in sink_dir.iterdir()) == ["customer.jsonl", "order_line.jsonl"]
    assert len(list(read_events(str(sink_dir / "order_line.jsonl")))) == 50


def test_options_and_envelopes_are_checked(tmp_path: Path, plan: Any) -> None:
    batch = next(iter(plan().blocks(0))).batch.slice(0, 3)
    with pytest.raises(ShapeError, match="unknown file emitter options"):
        FileEmitter().emit(f"file://{tmp_path}/x", [batch], bogus=1)
    with pytest.raises(ShapeError, match="unknown envelope"):
        ConsoleEmitter().emit("console://", [batch], envelope="xml")
    with pytest.raises(ShapeError, match="not a file URI"):
        uri_path("kafka://x/y", "file")


def test_console_emitter_writes_json_lines(capfdbinary: Any, plan: Any) -> None:
    batch = next(iter(plan().blocks(0))).batch.slice(0, 4)
    assert ConsoleEmitter().emit("console://", [batch]) == 4
    lines = capfdbinary.readouterr().out.splitlines()
    assert len(lines) == 4 and json.loads(lines[0])["_shape_seq"] == 0


def test_encode_events_matches_the_json_lines(plan: Any) -> None:
    from shape.streaming.emit import encode_batch

    batch = next(iter(plan().blocks(0))).batch.slice(0, 30)
    for envelope in ("flat", "cloudevents"):
        events = encode_events(batch, envelope)
        assert b"\n".join(e.body for e in events) + b"\n" == encode_batch(batch, envelope)
        assert [e.key for e in events] == [f"order_line/{i}" for i in range(30)]
