"""The emitter contract (P5-02): what every ``shape.emitters`` plugin owes the runtime.

The runtime (:mod:`shape.streaming.emit.runtime`) promises at-least-once delivery with the D-12
idempotency key, bounded memory under a slow destination, and a checkpoint that never moves past
an undelivered event. An emitter keeps its half of those promises when:

* **idempotency key** — every message it sends carries the key ``<table>/<seq>`` on the wire (a
  message key, a property, the CloudEvents ``id``), the same key for the same row on every run,
  and it agrees with the key inside the body;
* **at-least-once** — ``emit`` returns the number of events and returns *only after* the
  destination has acknowledged them; a transient failure raises ``OSError`` (or a subclass, such
  as ``ConnectionError``) so the runtime retries the batch; a repeat is allowed, a loss is not;
* **backpressure** — a destination that is full or slow is waited for inside ``emit``; no event is
  dropped and no error is raised just because the destination was busy;
* **checkpoint** — through the real runner and :class:`EmitterSink`, the checkpoint never gets
  ahead of the events the destination holds, a crashed run restarted from a stale checkpoint
  sends repeats, and a consumer that keeps the first event of each key sees the uninterrupted
  stream.

An emitter's tests build a *harness* over a fake client (or a real service) and call
:func:`check_contract`. A harness has:

``uri``                 the destination URI;
``make()``              a new emitter instance connected to the same destination (a restarted
                        process), with its client built over the harness's fake;
``delivered()``         ``[(wire key, body bytes), ...]``: everything the destination holds, in
                        the order it was delivered;
``inject_failures(n)``  the next ``n`` delivery attempts fail transiently (the destination
                        rejects the batch, or the connection drops);
``congest(n)``          the next ``n`` attempts find the destination full and clear only when the
                        emitter waits (polls, backs off); returns nothing;
``congestion_hits()``   how many times the destination reported full.

The harness is new for every check (``new_harness()``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from shape.errors import ShapeError
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE, decode_line, encode_batch
from shape.streaming.emit.runtime import EmitConfig, EmitRunner
from shape.streaming.emit.sinks import EmitterSink, MemorySink
from shape.streaming.emit.source import EventPlan

EVENTS = 2500  # events a contract run sends
BATCH = 500  # events per delivery
Delivered = list[tuple[str, bytes]]


class Harness(Protocol):
    uri: str

    def make(self) -> Any: ...

    def delivered(self) -> Delivered: ...

    def inject_failures(self, n: int) -> None: ...

    def congest(self, n: int) -> None: ...

    def congestion_hits(self) -> int: ...


def default_plan() -> EventPlan:
    """The sequence the contract runs send: retail's ``order_line``, small scale, seed 11."""
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine

    return EventPlan(Engine(load_target("retail"), scale="small", seed=11), tables=["order_line"])


def _config(**kw: Any) -> EmitConfig:
    kw.setdefault("max_events", EVENTS)
    kw.setdefault("batch_events", BATCH)
    kw.setdefault("retry_backoff", 0.0)
    return EmitConfig(**kw)


def reference(plan: EventPlan) -> list[dict[str, Any]]:
    """The uninterrupted stream: the first ``EVENTS`` flat events, in order."""
    sink = MemorySink()
    EmitRunner(plan, sink, _config()).run()
    out: list[dict[str, Any]] = []
    for b in sink.batches:
        out.extend(json.loads(line) for line in encode_batch(b).splitlines())
    return out


def flat(delivered: Delivered) -> list[dict[str, Any]]:
    return [decode_line(body) for _, body in delivered]


def first_per_key(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a consumer that keeps the first event of each key sees."""
    seen: set[tuple[str, int]] = set()
    out = []
    for e in events:
        key = (e[FIELD_TABLE], e[FIELD_SEQ])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def _run(
    h: Harness, plan: EventPlan, envelope: str = "flat", *, resuming: bool = False, **cfg: Any
) -> Any:
    sink = EmitterSink(h.make(), h.uri, envelope=envelope, resuming=resuming)
    return EmitRunner(plan, sink, _config(**cfg)).run()


def check_idempotency_key(
    new_harness: Callable[[], Harness],
    plan: Callable[[], EventPlan] = default_plan,
    envelopes: tuple[str, ...] = ("flat", "cloudevents"),
) -> None:
    ref = reference(plan())
    for envelope in envelopes:
        runs: list[Delivered] = []
        for _ in range(2):
            h = new_harness()
            _run(h, plan(), envelope)
            runs.append(h.delivered())
        first, again = runs
        _require(len(first) == EVENTS, f"{envelope}: {len(first)} messages for {EVENTS} events")
        events = flat(first)
        _require(events == ref, f"{envelope}: the bodies are not the runtime's flat events")
        for (wire, _), e in zip(first, events, strict=True):
            _require(
                wire == f"{e[FIELD_TABLE]}/{e[FIELD_SEQ]}",
                f"{envelope}: wire key {wire!r} is not <table>/<seq> of its body",
            )
        _require(len({k for k, _ in first}) == EVENTS, f"{envelope}: duplicate keys in one run")
        _require(
            [k for k, _ in first] == [k for k, _ in again],
            f"{envelope}: a replay produced different keys",
        )
        if envelope == "cloudevents":
            _require(
                all(json.loads(body)["id"] == k for k, body in first),
                "cloudevents: the envelope id is not the wire key",
            )
    # emit() itself: returns the count, and everything is already delivered when it returns
    h = new_harness()
    emitter = h.make()
    sample = plan().blocks(0)
    batch = next(iter(sample)).batch.slice(0, 300)
    sent = emitter.emit(h.uri, [batch])
    _require(sent == 300, f"emit() returned {sent} for 300 events")
    _require(len(h.delivered()) == 300, "emit() returned before its events were delivered")
    _close(emitter)


def check_at_least_once(
    new_harness: Callable[[], Harness], plan: Callable[[], EventPlan] = default_plan
) -> None:
    ref = reference(plan())
    # transient failures: the runtime retries, nothing is lost, repeats are allowed
    h = new_harness()
    h.inject_failures(2)
    report = _run(h, plan(), retries=3)
    _require(report.retries >= 1, "the injected failures never reached the runtime")
    _require(report.complete and report.events == EVENTS, "the run did not complete")
    _require(
        first_per_key(flat(h.delivered())) == ref,
        "after retries, the first event of each key is not the uninterrupted stream",
    )
    # a failure that outlasts the retries stops the run with an error, delivering nothing silently
    h = new_harness()
    h.inject_failures(10_000)
    try:
        _run(h, plan(), retries=1)
    except ShapeError:
        pass
    else:
        raise AssertionError("a destination that always fails did not stop the run")


def check_backpressure(
    new_harness: Callable[[], Harness], plan: Callable[[], EventPlan] = default_plan
) -> None:
    ref = reference(plan())
    h = new_harness()
    h.congest(5)
    emitter = h.make()
    batch = next(iter(plan().blocks(0))).batch.slice(0, 400)
    sent = emitter.emit(h.uri, [batch])
    _close(emitter)
    _require(h.congestion_hits() > 0, "the destination never reported full: the check is vacuous")
    _require(sent == 400 and len(h.delivered()) >= 400, "a full destination lost events")
    # through the runner: the congestion costs no retry, no event
    h = new_harness()
    h.congest(5)
    report = _run(h, plan(), retries=0)
    _require(report.retries == 0, "a busy destination was reported as a failure")
    _require(first_per_key(flat(h.delivered())) == ref, "a busy destination changed the stream")


def check_checkpoint(
    new_harness: Callable[[], Harness],
    plan: Callable[[], EventPlan] = default_plan,
    *,
    directory: Path,
) -> None:
    ref = reference(plan())
    ck = directory / "emit.checkpoint"
    # a failed run: the checkpoint never gets ahead of what the destination holds
    h = new_harness()
    h.inject_failures(10_000)
    try:
        _run(h, plan(), retries=0, checkpoint_path=str(ck), checkpoint_every=BATCH)
    except ShapeError:
        pass
    offset = json.loads(ck.read_text())["offset"] if ck.exists() else 0
    _require(offset <= len({k for k, _ in h.delivered()}), "the checkpoint got ahead of delivery")
    # a clean restart completes the stream; the destination holds exactly the stream
    h = new_harness()
    _run(h, plan(), checkpoint_path=str(ck), checkpoint_every=BATCH, fresh=True)
    doc = json.loads(ck.read_text())
    _require(doc["complete"] and doc["offset"] == EVENTS, "no checkpoint at the end of the run")
    _require(flat(h.delivered()) == ref, "a clean run is not the uninterrupted stream")
    # a crash: the checkpoint is behind what was delivered, the restart sends repeats, and a
    # consumer that keeps the first event of each key sees the uninterrupted stream
    h = new_harness()
    _run(h, plan(), checkpoint_path=str(ck), checkpoint_every=BATCH, fresh=True, max_events=1800)
    doc = json.loads(ck.read_text())
    _require(doc["offset"] == 1800, f"shutdown checkpoint is {doc['offset']}, not 1800")
    doc.update(offset=700, complete=False)
    ck.write_text(json.dumps(doc))
    _run(h, plan(), resuming=True, checkpoint_path=str(ck), checkpoint_every=BATCH)
    events = flat(h.delivered())
    _require(len(events) > EVENTS, "a stale checkpoint should have produced repeats")
    _require(first_per_key(events) == ref, "restart + dedupe on the key is not the stream")


def check_contract(
    new_harness: Callable[[], Harness],
    *,
    directory: Path,
    plan: Callable[[], EventPlan] = default_plan,
    envelopes: tuple[str, ...] = ("flat", "cloudevents"),
) -> None:
    """All four checks; each uses fresh harnesses. ``envelopes`` are the formats the emitter
    accepts (a destination of typed columns takes only ``flat``)."""
    check_idempotency_key(new_harness, plan, envelopes)
    check_at_least_once(new_harness, plan)
    check_backpressure(new_harness, plan)
    check_checkpoint(new_harness, plan, directory=directory)


def _close(emitter: Any) -> None:
    close = getattr(emitter, "close", None)
    if callable(close):
        close()
