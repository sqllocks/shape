"""The ``eventhubs://`` emitter (P5-02).

``eventhubs://<namespace>/<hub>`` sends each event as one Event Hubs message: the body is the
event's JSON (flat, or the CloudEvents envelope), the **idempotency key** ``<table>/<seq>`` is the
message property ``shape_key`` (with ``shape_table`` and ``shape_seq``), and the content type is
``application/json`` (``application/cloudevents+json`` for the envelope). Event Hubs does not
deduplicate: a consumer keeps the first message of each ``shape_key``.

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
from shape.streaming.emit.formats import ENVELOPES, EncodedEvent, encode_events

from .source import ENV_CONNECTION_STRING

PROP_KEY = "shape_key"
PROP_TABLE = "shape_table"
PROP_SEQ = "shape_seq"
_BUSY_PAUSE = 0.5  # seconds; doubles with each repeat


class HubTarget(NamedTuple):
    namespace: str
    hub: str | None


def parse_uri(uri: str) -> HubTarget:
    """The namespace and event hub of an ``eventhubs://`` URI."""
    parts = urlsplit(uri)
    if parts.scheme != "eventhubs" or not parts.netloc:
        raise ShapeError(f"not an eventhubs URI: {uri!r} (eventhubs://<namespace>/<hub>)")
    hub = unquote(parts.path.lstrip("/"))
    if not hub or "/" in hub:
        raise ShapeError(f"the eventhubs URI needs one event hub name: {uri!r}")
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
    return EventHubProducerClient(
        fully_qualified_namespace=target.namespace,
        eventhub_name=target.hub,
        credential=credential,
    )


def _is_busy(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "server-busy" in text or "serverbusy" in text or "throttl" in text


def _groups(events: list[EncodedEvent], by_table: bool) -> Iterator[tuple[str | None, list]]:
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
        **options: Any,
    ) -> int:
        """Send every batch; return the number of events, after the service accepted them."""
        connect = {k: options.pop(k) for k in ("connection_string", "credential") if k in options}
        if options:
            raise ShapeError(f"unknown {self.name} emitter options: {sorted(options)}")
        if envelope not in ENVELOPES:
            raise ShapeError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
        if partition_key not in ("table", "none"):
            raise ShapeError("partition_key must be 'table' or 'none'")
        target = self.parse(uri)
        client = self._client(target, connect)
        content_type = "application/json" if envelope == "flat" else "application/cloudevents+json"
        sent = 0
        for batch in batches:
            events = encode_events(batch, envelope)
            for key, group in _groups(events, partition_key == "table"):
                self._send_group(client, key, group, content_type, busy_retries)
            sent += len(events)
        return sent

    # ---------------------------------------------------------------- sending
    def _send_group(
        self, client: Any, key: str | None, group: list[EncodedEvent], content_type: str, busy: int
    ) -> None:
        from azure.eventhub import EventData

        def new_batch() -> Any:
            return client.create_batch(partition_key=key) if key else client.create_batch()

        def message(ev: EncodedEvent) -> Any:
            data = EventData(ev.body)
            data.content_type = content_type
            data.properties = {PROP_KEY: ev.key, PROP_TABLE: ev.table, PROP_SEQ: ev.seq}
            return data

        current = new_batch()
        count = 0
        for ev in group:
            data = message(ev)
            try:
                current.add(data)
            except ValueError:
                if count == 0:
                    raise ShapeError(
                        f"event {ev.key} ({len(ev.body)} bytes) is larger than an event hub batch"
                    ) from None
                self._send(client, current, busy)
                current = new_batch()
                current.add(data)
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
