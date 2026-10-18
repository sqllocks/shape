"""The ``eventhubs://`` emitter (P5-02).

``eventhubs://<namespace>/<hub>`` sends each event as one Event Hubs message: the body is the
event's JSON (flat, or the CloudEvents envelope), the **idempotency key** ``<table>/<seq>`` is the
default message property ``shape_key`` (with ``shape_table`` and ``shape_seq``). The content type is
``application/json`` (``application/cloudevents+json`` for the envelope). Event Hubs does not
deduplicate: a consumer keeps the first message of each replay key. With a column key,
``shape_key`` carries that selected key and ``shape-key`` retains the replay key.

Delivery is at-least-once. ``emit`` returns after the service has accepted every batch it sent
(``send_batch`` returns on the acknowledgement); a failed send raises ``ConnectionError``, which
the runtime retries, so a repeated message is possible and a lost one is not. A throttled service
(an ``EventHubError`` saying ``server-busy``) is waited for inside ``emit`` with a growing pause,
so the generator, not the event hub, absorbs the slowdown.

Messages are packed into service batches by size. By default a batch carries one table, sent with
that table as its *partition key*, so a table keeps its order on one partition; ``partition_key``
``"none"`` lets the service spread them.

Options of :meth:`EventHubsEmitter.emit`:

``envelope``           ``"flat"`` (default) or ``"cloudevents"``.
``connection_string``  a connection string; otherwise ``SHAPE_EVENTHUBS_CONNECTION_STRING``;
                       otherwise Microsoft Entra sign-in (``azure-identity``) to the namespace in
                       the URI.
``credential``         an ``azure-identity`` credential to use instead.
``partition_key``      ``"table"`` (default) or ``"none"``.
``busy_retries``       how many times a throttled send is repeated before it fails (default 6).
``resuming``           accepted and ignored.

``azure-eventhub`` is imported when the first message is sent, never at plugin load.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any, NamedTuple
from urllib.parse import unquote, urlsplit

import pyarrow as pa

from shape.errors import ShapeError
from shape.streaming.emit.formats import ENVELOPES, FIELD_DEAD_REASON, EncodedEvent, encode_events
from shape.streaming.emit.metadata import metadata

from .source import ENV_CONNECTION_STRING

PROP_KEY = "shape_key"
PROP_TABLE = "shape_table"
PROP_SYNTHETIC = "shape_synthetic"
PROP_SEQ = "shape_seq"
_BUSY_PAUSE = 0.5  # seconds; doubles with each repeat


class HubTarget(NamedTuple):
    namespace: str
    hub: str | None


def parse_uri(uri: str) -> HubTarget:
    """The namespace and event hub of an ``eventhubs://`` URI."""
    parts = urlsplit(uri)
    if parts.username is not None or parts.password is not None:
        raise ShapeError("credentials must be supplied outside the URI")
    if parts.scheme != "eventhubs" or not parts.netloc:
        raise ShapeError("not an eventhubs URI: the supplied URI (eventhubs://<namespace>/<hub>)")
    hub = unquote(parts.path.lstrip("/"))
    if not hub or "/" in hub:
        raise ShapeError("the eventhubs URI needs one event hub name: the supplied URI")
    return HubTarget(parts.netloc, hub)


def _sdk_producer(target: HubTarget, options: dict[str, Any], env: str) -> Any:
    try:
        from azure.eventhub import EventHubProducerClient
    except ImportError as exc:
        raise ShapeError(
            "emitting to Event Hubs needs the azure-eventhub package "
            "(pip install sqllocks-shape-eventhubs)"
        ) from exc
    conn = options.get("connection_string") or os.environ.get(env)
    if conn:
        return EventHubProducerClient.from_connection_string(conn, eventhub_name=target.hub)
    credential = options.get("credential")
    if credential is None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise ShapeError(
                f"give a connection string (the connection_string option or {env}) or install "
                "azure-identity for Microsoft Entra sign-in"
            ) from exc
        credential = DefaultAzureCredential()
    if target.hub is None:
        raise ShapeError("Microsoft Entra sign-in needs an event hub name")
    return EventHubProducerClient(
        fully_qualified_namespace=target.namespace,
        eventhub_name=target.hub,
        credential=credential,
    )


def _is_busy(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "server-busy" in text or "serverbusy" in text or "throttl" in text


def _groups(
    events: list[EncodedEvent], by_table: bool
) -> Iterator[tuple[str | None, list[EncodedEvent]]]:
    """Consecutive events of one table (or all of them), each group sent with one partition key."""
    if not by_table:
        yield None, events
        return
    start = 0
    for i in range(1, len(events) + 1):
        if i == len(events) or events[i].table != events[start].table:
            yield events[start].table, events[start:i]
            start = i


class EventHubsEmitter:
    """Events to an event hub, each carrying the idempotency key as a message property.

    ``client_factory(target, options)`` is for tests: it stands in for building an
    ``azure.eventhub.EventHubProducerClient``.
    """

    name = "eventhubs"
    accepts_poison = True  # one JSON text per message: a cut-off body is a poison message
    supports_synthetic = True  # the `synthetic` option marks every message with a header
    schemes = ("eventhubs",)
    env_connection_string = ENV_CONNECTION_STRING

    def __init__(
        self,
        client_factory: Callable[[HubTarget, dict[str, Any]], Any] | None = None,
        *,
        busy_pause: float = _BUSY_PAUSE,
    ) -> None:
        self._client_factory = client_factory
        self._busy_pause = busy_pause
        self._clients: dict[tuple[str, str | None], Any] = {}

    def parse(self, uri: str) -> HubTarget:
        return parse_uri(uri)

    def _client(self, target: HubTarget, options: dict[str, Any]) -> Any:
        client = self._clients.get(target)
        if client is None:
            if self._client_factory is not None:
                client = self._client_factory(target, options)
            else:
                client = _sdk_producer(target, options, self.env_connection_string)
            self._clients[target] = client
        return client

    def emit(
        self,
        uri: str,
        batches: Iterable[pa.RecordBatch],
        *,
        envelope: str = "flat",
        resuming: bool = False,
        partition_key: str = "table",
        busy_retries: int = 6,
        synthetic: bool = False,
        key: str | None = None,
        headers: dict[str, Any] | list[str] | None = None,
        timestamp: str = "broker",
        **options: Any,
    ) -> int:
        """Send every batch; return the number of events, after the service accepted them."""
        connect: dict[str, Any] = {
            k: options.pop(k) for k in ("connection_string", "credential") if k in options
        }
        if options:
            raise ShapeError(f"unknown {self.name} emitter options: {sorted(options)}")
        if envelope not in ENVELOPES:
            raise ShapeError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
        target = self.parse(uri)
        client = self._client(target, connect)
        content_type = "application/json" if envelope == "flat" else "application/cloudevents+json"
        sent = 0
        saw_batch = False
        for batch in batches:
            saw_batch = True
            meta = metadata(
                batch, key=key, headers=headers, timestamp=timestamp, partition_key=partition_key
            )
            events = encode_events(batch, envelope)
            by_event = {e.key: m for e, m in zip(events, meta, strict=True)}
            if FIELD_DEAD_REASON in batch.schema.names:
                for event, reason in zip(
                    events, batch.column(FIELD_DEAD_REASON).to_pylist(), strict=True
                ):
                    by_event[event.key][1].append(
                        ("shape-dead-letter-reason", str(reason).encode())
                    )
            if partition_key not in ("table", "none"):
                groups = [(m[3], [e]) for e, m in zip(events, meta, strict=True)]
            else:
                groups = list(_groups(events, partition_key == "table"))
            for route, group in groups:
                self._send_group(
                    client, route, group, content_type, busy_retries, synthetic, by_event
                )
            sent += len(events)
        if not saw_batch and partition_key not in ("table", "none"):
            raise ShapeError("partition_key column cannot be validated without a batch")
        return sent

    # ---------------------------------------------------------------- sending
    def _send_group(
        self,
        client: Any,
        key: str | None,
        group: list[EncodedEvent],
        content_type: str,
        busy: int,
        synthetic: bool = False,
        message_metadata: dict[str, Any] | None = None,
    ) -> None:
        from azure.eventhub import EventData

        def new_batch() -> Any:
            return client.create_batch(partition_key=key) if key else client.create_batch()

        def message(ev: EncodedEvent) -> Any:
            data = EventData(ev.body)
            data.content_type = content_type
            data.properties = {PROP_KEY: ev.key, PROP_TABLE: ev.table, PROP_SEQ: ev.seq}
            if message_metadata is not None:
                message_key, user_headers, when, _ = message_metadata[ev.key]
                data.properties.update(dict(user_headers))
                if message_key is not None:
                    data.properties["shape-key"] = ev.key
                    data.properties[PROP_KEY] = message_key
                if when is not None:
                    properties = data.raw_amqp_message.properties
                    assert properties is not None
                    properties.creation_time = when
            if synthetic:
                data.properties[PROP_SYNTHETIC] = True
            return data

        def too_large(ev: EncodedEvent) -> ShapeError:
            return ShapeError(
                f"event {ev.key} ({len(ev.body)} bytes) is larger than an event hub batch; "
                "make the rows smaller or emit to a hub tier with a larger message size"
            )

        current = new_batch()
        count = 0
        for ev in group:
            data = message(ev)
            try:
                current.add(data)
            except ValueError:
                if count == 0:
                    raise too_large(ev) from None
                self._send(client, current, busy)
                current = new_batch()
                try:
                    current.add(data)
                except ValueError:
                    raise too_large(ev) from None
                count = 0
            count += 1
        if count:
            self._send(client, current, busy)

    def _send(self, client: Any, batch: Any, busy: int) -> None:
        from azure.eventhub.exceptions import AuthenticationError, EventDataError, EventHubError

        pause = self._busy_pause
        attempt = 0
        while True:
            try:
                client.send_batch(batch)
                return
            except AuthenticationError as exc:
                raise ShapeError(f"{self.name}: not authorised: {exc}") from exc
            except EventDataError as exc:
                raise ShapeError(f"{self.name}: the service refused the data: {exc}") from exc
            except EventHubError as exc:
                if not _is_busy(exc):
                    raise ConnectionError(f"{self.name}: send failed: {exc}") from exc
                # Throttled: back off and repeat; the stall reaches the generator as backpressure.
                attempt += 1
                if attempt > busy:
                    raise ConnectionError(f"{self.name}: the service stayed busy: {exc}") from exc
                time.sleep(pause)
                pause *= 2

    def flush(self) -> None:
        return None  # every emit() returns after acknowledgement

    def close(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            client.close()
