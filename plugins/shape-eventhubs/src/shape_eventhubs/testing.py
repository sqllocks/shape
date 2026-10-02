"""An in-memory event hub with the slice of ``EventHubConsumerClient`` the source uses.

For contract tests and the plugin kit: ``FakeHub.source()`` is an ``EventHubsStreamSource``
that reads the hub. Like the real client, ``receive_batch`` blocks until ``close()`` and calls
the callback with an empty batch when nothing arrives within ``max_wait_time``. ``fail_at``
reports a connection error through ``on_error`` once that many events were delivered (the real
client keeps retrying; the source turns it into a reconnect of its own).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from .source import EventHubsStreamSource, HubUri


class FakeEvent:
    def __init__(self, sequence_number: int, body: str, enqueued: datetime | None) -> None:
        self.sequence_number = sequence_number
        self.body = body
        self.enqueued_time = enqueued

    def body_as_str(self) -> str:
        return self.body


class FakeContext:
    def __init__(self, partition_id: str) -> None:
        self.partition_id = partition_id


class FakeClient:
    def __init__(self, hub: FakeHub) -> None:
        self.hub = hub
        self.closed = threading.Event()

    def get_partition_ids(self) -> list[str]:
        return sorted(self.hub.partitions)

    def get_partition_properties(self, partition_id: str) -> dict[str, Any]:
        log = self.hub.partitions[partition_id]
        if not log:
            return {
                "is_empty": True,
                "beginning_sequence_number": -1,
                "last_enqueued_sequence_number": -1,
            }
        return {
            "is_empty": False,
            "beginning_sequence_number": 0,
            "last_enqueued_sequence_number": len(log) - 1,
        }

    def receive_batch(
        self,
        on_event_batch: Callable[[Any, list[Any]], None],
        *,
        max_batch_size: int = 300,
        max_wait_time: float | None = None,
        partition_id: str | None = None,
        starting_position: Mapping[str, int] | int | None = None,
        starting_position_inclusive: bool = False,
        on_error: Callable[[Any, Exception], None] | None = None,
        **_: Any,
    ) -> None:
        hub = self.hub
        hub.receives += 1
        if partition_id is not None:
            nxt = {partition_id: int(starting_position or 0)}  # type: ignore[arg-type]
        else:
            nxt = {p: int(v) for p, v in (starting_position or {}).items()}  # type: ignore[union-attr]
        size = min(max_batch_size, hub.chunk) if hub.chunk else max_batch_size
        while not self.closed.is_set():
            idle = True
            for pid in sorted(nxt):
                if hub.fail_at is not None and hub.delivered >= hub.fail_at:
                    hub.fail_at = None if hub.fail_every is None else hub.delivered + hub.fail_every
                    if on_error is not None:
                        on_error(FakeContext(pid), ConnectionError("fake: connection lost"))
                    return
                log = hub.partitions[pid]
                chunk = log[nxt[pid] : nxt[pid] + size]
                if chunk:
                    nxt[pid] += len(chunk)
                    hub.delivered += len(chunk)
                    on_event_batch(FakeContext(pid), chunk)
                    idle = False
            if idle:
                time.sleep(0.01)
                for pid in sorted(nxt):
                    on_event_batch(FakeContext(pid), [])

    def close(self) -> None:
        self.closed.set()


class FakeHub:
    """``partitions`` maps a partition id to ``[(body, enqueued time or None), ...]``."""

    def __init__(
        self,
        partitions: Mapping[str, Sequence[tuple[str, datetime | None]]],
        *,
        chunk: int = 0,
        fail_at: int | None = None,
        fail_every: int | None = None,
    ) -> None:
        self.partitions = {
            p: [FakeEvent(i, body, when) for i, (body, when) in enumerate(events)]
            for p, events in partitions.items()
        }
        self.chunk = chunk
        self.fail_at = fail_at
        self.fail_every = fail_every
        self.delivered = 0
        self.receives = 0
        self.clients: list[FakeClient] = []

    def client(self, target: HubUri, options: Mapping[str, Any]) -> FakeClient:
        c = FakeClient(self)
        self.clients.append(c)
        return c

    def source(self) -> EventHubsStreamSource:
        return EventHubsStreamSource(self.client)


def json_events(
    rows: Sequence[Mapping[str, Any]],
    start: datetime | None = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
    step: timedelta = timedelta(milliseconds=10),
) -> list[tuple[str, datetime | None]]:
    """JSON-object events for ``rows``, enqueued ``step`` apart."""
    return [
        (json.dumps(r), None if start is None else start + i * step) for i, r in enumerate(rows)
    ]


class FakeEventBatch:
    """``EventDataBatch``: ``add`` raises ``ValueError`` once the batch is full."""

    def __init__(self, max_bytes: int, partition_key: str | None) -> None:
        self.max_bytes = max_bytes
        self.partition_key = partition_key
        self.events: list[Any] = []
        self.size = 0

    def add(self, event: Any) -> None:
        n = len(b"".join(event.body)) + 64  # the body and a fixed overhead
        if self.size + n > self.max_bytes:
            raise ValueError("EventDataBatch has reached its size limit")
        self.events.append(event)
        self.size += n

    def __len__(self) -> int:
        return len(self.events)


class FakeProducerClient:
    """The slice of ``EventHubProducerClient`` the emitter uses, over an in-memory hub.

    ``send_batch`` accepts a batch whole or not at all. ``failures`` makes the next sends raise a
    transport error; ``busy`` makes them raise the service's ``server-busy`` error (throttling).
    """

    def __init__(self, hub: FakeProducerHub) -> None:
        self.hub = hub
        self.closed = False

    def create_batch(self, partition_key: str | None = None) -> FakeEventBatch:
        return FakeEventBatch(self.hub.max_batch_bytes, partition_key)

    def send_batch(self, batch: FakeEventBatch) -> None:
        from azure.eventhub.exceptions import EventHubError

        hub = self.hub
        if hub.busy > 0:
            hub.busy -= 1
            hub.hits += 1
            raise EventHubError("amqp:server-busy: the service is busy")
        if hub.failures > 0:
            hub.failures -= 1
            raise EventHubError("connection dropped")
        hub.batches.append(batch)

    def close(self) -> None:
        self.closed = True


class FakeProducerHub:
    def __init__(self, max_batch_bytes: int = 16_384) -> None:
        self.max_batch_bytes = max_batch_bytes
        self.batches: list[FakeEventBatch] = []
        self.failures = 0
        self.busy = 0
        self.hits = 0
        self.clients: list[FakeProducerClient] = []

    def factory(self, target: Any, options: Mapping[str, Any]) -> FakeProducerClient:
        client = FakeProducerClient(self)
        self.clients.append(client)
        return client


class EmitterHarness:
    """The emitter contract's harness (``shape.streaming.emit.contract``) over a fake hub."""

    scheme = "eventhubs"

    def __init__(self, hub_name: str = "eh1", max_batch_bytes: int = 16_384) -> None:
        self.hub = FakeProducerHub(max_batch_bytes)
        self.uri = f"{self.scheme}://ns.servicebus.example/{hub_name}"

    def make(self) -> Any:
        from .emitter import EventHubsEmitter

        return EventHubsEmitter(self.hub.factory, busy_pause=0.001)

    def delivered(self) -> list[tuple[str, bytes]]:
        return [
            (e.properties["shape_key"], b"".join(e.body))
            for batch in self.hub.batches
            for e in batch.events
        ]

    def inject_failures(self, n: int) -> None:
        self.hub.failures = n

    def congest(self, n: int) -> None:
        self.hub.busy = n

    def congestion_hits(self) -> int:
        return self.hub.hits
