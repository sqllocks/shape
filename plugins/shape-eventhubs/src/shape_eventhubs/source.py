"""The ``eventhubs://`` stream source (P3-04).

``eventhubs://<namespace>/<hub>[?consumer_group=NAME]`` reads one event hub, for example
``eventhubs://contoso.servicebus.windows.net/telemetry``. Shape keeps its own position: each
partition is read from an explicit *sequence number* and nothing is checkpointed in Azure Blob
Storage, so a ``StreamOffset`` is ``{partition id: next sequence number}`` and a Shape
checkpoint resumes exactly where it left off.

Connect with a connection string (the ``connection_string`` option, or the
``SHAPE_EVENTHUBS_CONNECTION_STRING`` environment variable; the entity name comes from the URI),
or with a ``credential`` (any ``azure-identity`` credential; ``DefaultAzureCredential`` when
``azure-identity`` is installed and no connection string is given).

Options of :meth:`EventHubsStreamSource.read`:

``start_at``       ``"earliest"`` (default) or ``"latest"``: where partitions without a start
                   position begin.
``stop_at_end``    ``True`` (default): stop once every partition has reached the last sequence
                   number it had when the read began. ``False`` follows the hub until
                   ``idle_timeout`` or ``max_messages``, or forever.
``idle_timeout``   stop after this many seconds without an event (``None``: keep waiting).
``max_messages``   stop after this many events.
``batch_size``     events per micro-batch, at most (default 65,536; the Event Hubs client
                   delivers up to its own ``max_batch_size``, which this sets).
``schema``, ``on_error``, ``event_time_field``, ``event_time_unit``, ``with_offsets``:
                   as for ``shape.streaming.messages.decode_messages``; the event time of an
                   event without one in its body is its enqueued time.

``azure-eventhub`` is imported when a read starts, never at plugin load. The client delivers
events on a worker thread; they reach the reader through a bounded queue, so a slow profiler
slows the receiving down instead of growing memory.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, NamedTuple, NoReturn
from urllib.parse import parse_qs, unquote, urlsplit

import pyarrow as pa

from shape.plugins.api.v1 import StreamOffset
from shape.streaming.messages import (
    DecodeStats,
    StreamMessage,
    StreamSourceError,
    conform,
    decode_messages,
    freeze,
    partition_offsets,
)

ENV_CONNECTION_STRING = "SHAPE_EVENTHUBS_CONNECTION_STRING"
DEFAULT_BATCH = 65_536
DEFAULT_GROUP = "$Default"
_POLL = 1.0  # seconds a queue read or an empty receive waits
_QUEUE_DEPTH = 8
_METADATA_RETRIES = 3

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class HubUri(NamedTuple):
    namespace: str
    hub: str
    consumer_group: str


def parse_uri(uri: str) -> HubUri:
    """The namespace, event hub and consumer group of an ``eventhubs://`` URI."""
    parts = urlsplit(uri)
    if parts.scheme != "eventhubs" or not parts.netloc:
        raise StreamSourceError(
            f"not an eventhubs URI: {uri!r} (eventhubs://<namespace>/<hub>[?consumer_group=NAME])"
        )
    hub = unquote(parts.path.lstrip("/"))
    if not hub or "/" in hub:
        raise StreamSourceError(f"the eventhubs URI needs one event hub name: {uri!r}")
    query = parse_qs(parts.query)
    group = query.get("consumer_group", [DEFAULT_GROUP])[0]
    return HubUri(parts.netloc, hub, group)


def _sdk_client(target: HubUri, options: Mapping[str, Any]) -> Any:
    try:
        from azure.eventhub import EventHubConsumerClient
    except ImportError as exc:
        raise StreamSourceError(
            "reading Event Hubs needs the azure-eventhub package "
            "(pip install sqllocks-shape-eventhubs)"
        ) from exc
    conn = options.get("connection_string") or os.environ.get(ENV_CONNECTION_STRING)
    if conn:
        return EventHubConsumerClient.from_connection_string(
            conn, consumer_group=target.consumer_group, eventhub_name=target.hub
        )
    credential = options.get("credential")
    if credential is None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise StreamSourceError(
                "give a connection string (the connection_string option or "
                f"{ENV_CONNECTION_STRING}) or install azure-identity for Microsoft Entra sign-in"
            ) from exc
        credential = DefaultAzureCredential()
    return EventHubConsumerClient(
        fully_qualified_namespace=target.namespace,
        eventhub_name=target.hub,
        consumer_group=target.consumer_group,
        credential=credential,
    )


def _enqueued_us(when: datetime | None) -> int | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = when - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


class _Failure:
    """A worker-thread error, handed to the reader."""

    def __init__(self, error: BaseException) -> None:
        self.error = error


class EventHubsStreamSource:
    """An event hub as ``(offset, record batch)`` pairs.

    ``client_factory(target, options)`` is for tests: it stands in for building an
    ``azure.eventhub.EventHubConsumerClient``.
    """

    name = "eventhubs"
    schemes = ("eventhubs",)

    def __init__(self, client_factory: Callable[[HubUri, Mapping[str, Any]], Any] | None = None):
        self._client_factory = client_factory or _sdk_client
        self.stats = DecodeStats()

    def can_open(self, uri: str) -> bool:
        try:
            parse_uri(uri)
        except StreamSourceError:
            return False
        return True

    # ------------------------------------------------------------------ read
    def read(
        self, uri: str, start: StreamOffset | None = None, **options: Any
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        """Yield each batch with the sequence numbers just after it."""
        target = parse_uri(uri)
        opts = dict(options)
        start_at = opts.pop("start_at", "earliest")
        stop_at_end = bool(opts.pop("stop_at_end", True))
        idle_timeout = opts.pop("idle_timeout", None)
        max_messages = opts.pop("max_messages", None)
        batch_size = int(opts.pop("batch_size", DEFAULT_BATCH))
        schema: pa.Schema | None = opts.pop("schema", None)
        connect: dict[str, Any] = {
            k: opts.pop(k) for k in ("connection_string", "credential") if k in opts
        }
        decode: dict[str, Any] = {
            k: opts.pop(k)
            for k in ("event_time_field", "event_time_unit", "with_offsets", "on_error")
            if k in opts
        }
        if opts:
            raise StreamSourceError(f"unknown eventhubs source options: {sorted(opts)}")
        if start_at not in ("earliest", "latest"):
            raise StreamSourceError("start_at must be 'earliest' or 'latest'")
        if batch_size < 1:
            raise StreamSourceError("batch_size must be positive")
        yield from self._read(
            target,
            connect,
            start,
            start_at=start_at,
            stop_at_end=stop_at_end,
            idle_timeout=idle_timeout,
            max_messages=max_messages,
            batch_size=batch_size,
            schema=schema,
            decode=decode,
        )

    def _positions(
        self, client: Any, start: StreamOffset | None, start_at: str
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Where each partition starts, and the next sequence number after its last event."""
        given = {str(k): int(v) for k, v in (start.value if start is not None else {}).items()}
        positions: dict[str, int] = {}
        ends: dict[str, int] = {}
        for pid in sorted(str(p) for p in client.get_partition_ids()):
            props = client.get_partition_properties(pid)
            high = 0 if props.get("is_empty") else int(props["last_enqueued_sequence_number"]) + 1
            low = high if props.get("is_empty") else int(props["beginning_sequence_number"])
            if pid in given:
                positions[pid] = min(max(given[pid], low), high)
            else:
                positions[pid] = low if start_at == "earliest" else high
            ends[pid] = high
        if not positions:
            raise StreamSourceError("the event hub has no partitions")
        return positions, ends

    def _read(
        self,
        target: HubUri,
        connect: Mapping[str, Any],
        start: StreamOffset | None,
        *,
        start_at: str,
        stop_at_end: bool,
        idle_timeout: float | None,
        max_messages: int | None,
        batch_size: int,
        schema: pa.Schema | None,
        decode: Mapping[str, Any],
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        client = self._client_factory(target, connect)
        try:
            positions, ends = self._positions(client, start, start_at)
        except Exception as exc:
            self._raise(exc)
        finally:
            client.close()
        ids = sorted(positions)
        # A bounded read takes the partitions one after the other, in order, so the batches (and
        # the profile built from them) are the same on every run; following a hub reads all
        # partitions together, in arrival order.
        groups = [[p] for p in ids if positions[p] < ends[p]] if stop_at_end else [ids]
        for group in groups:
            receiver = self._client_factory(target, connect)
            for offset, batch in self._receive(
                receiver,
                group,
                positions,
                ends,
                stop_at_end=stop_at_end,
                idle_timeout=idle_timeout,
                budget=None if max_messages is None else max_messages - self.stats.messages,
                batch_size=batch_size,
                schema=schema,
                decode=decode,
            ):
                schema = batch.schema
                yield offset, batch
            if max_messages is not None and self.stats.messages >= max_messages:
                return

    def _receive(
        self,
        client: Any,
        group: list[str],
        positions: dict[str, int],
        ends: dict[str, int],
        *,
        stop_at_end: bool,
        idle_timeout: float | None,
        budget: int | None,
        batch_size: int,
        schema: pa.Schema | None,
        decode: Mapping[str, Any],
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        """Receive ``group``'s partitions on a worker thread until a stop condition holds."""
        inbox: queue.Queue[tuple[str, list[StreamMessage]] | _Failure] = queue.Queue(_QUEUE_DEPTH)
        stop = threading.Event()

        def deliver(item: tuple[str, list[StreamMessage]] | _Failure) -> None:
            while not stop.is_set():
                try:
                    inbox.put(item, timeout=_POLL)
                    return
                except queue.Full:
                    continue

        def on_batch(ctx: Any, events: list[Any]) -> None:
            if not events:
                return
            pid = str(ctx.partition_id)
            deliver(
                (
                    pid,
                    [
                        StreamMessage(
                            pid,
                            int(e.sequence_number),
                            e.body_as_str() if hasattr(e, "body_as_str") else e.body,
                            _enqueued_us(e.enqueued_time),
                        )
                        for e in events
                    ],
                )
            )

        def on_error(ctx: Any, error: Exception) -> None:
            deliver(_Failure(error))

        def work() -> None:
            try:
                # Without a partition id the client reads every partition, those missing from
                # the dict from their start: one partition is asked for by id.
                where: dict[str, Any] = (
                    {"partition_id": group[0], "starting_position": positions[group[0]]}
                    if len(group) == 1
                    else {"starting_position": {p: positions[p] for p in group}}
                )
                client.receive_batch(
                    on_batch,
                    max_batch_size=batch_size,
                    max_wait_time=_POLL,
                    starting_position_inclusive=True,
                    on_error=on_error,
                    **where,
                )
            except BaseException as exc:  # handed to the reader, which raises it
                deliver(_Failure(exc))

        worker = threading.Thread(target=work, name="shape-eventhubs-receive", daemon=True)
        received = 0
        last = time.monotonic()
        try:
            worker.start()
            while True:
                if budget is not None and received >= budget:
                    return
                if stop_at_end and all(positions[p] >= ends[p] for p in group):
                    return
                try:
                    item = inbox.get(timeout=_POLL)
                except queue.Empty:
                    if not worker.is_alive():
                        raise ConnectionError("eventhubs: the receiver stopped") from None
                    if idle_timeout is not None and time.monotonic() - last >= idle_timeout:
                        return
                    continue
                if isinstance(item, _Failure):
                    self._raise(item.error)
                pid, messages = item
                # Events already read (a retry inside the client) are dropped by position.
                messages = [m for m in messages if m.offset >= positions[pid]]
                if stop_at_end:
                    # Enqueued after the read began: past this read's end.
                    messages = [m for m in messages if m.offset < ends[pid]]
                if budget is not None:
                    messages = messages[: budget - received]
                if not messages:
                    continue
                last = time.monotonic()
                received += len(messages)
                positions[pid] = max(positions[pid], messages[-1].offset + 1)
                batch = decode_messages(messages, schema=schema, stats=self.stats, **decode)
                if batch is None:
                    continue
                if schema is None:
                    schema = freeze(batch)
                    batch = conform(batch, schema)
                yield StreamOffset(partition_offsets(positions)), batch
        finally:
            stop.set()
            try:
                client.close()
            finally:
                worker.join(timeout=10)

    @staticmethod
    def _raise(error: BaseException) -> NoReturn:
        if isinstance(error, StreamSourceError):
            raise error
        # The SDK reports connection loss, throttling and service errors through the same
        # callback; none of them is the caller's fault, so the consumer reconnects.
        module = type(error).__module__
        if module.startswith(("azure.eventhub", "uamqp", "azure.core")) or isinstance(
            error, ConnectionError | TimeoutError
        ):
            raise ConnectionError(f"eventhubs: {type(error).__name__}: {error}") from error
        raise StreamSourceError(f"eventhubs: {type(error).__name__}: {error}") from error
