"""The ``eventhouse://`` emitter: a Fabric Eventhouse (KQL database) (P5-02).

    eventhouse://<query-uri host>/<database>[/<table>][?tls=false]

sends events with Kusto *streaming ingestion* (``/v1/rest/ingest``), one JSON-lines request per
batch of up to ``max_request_bytes``. Without ``<table>``, each Shape table goes to the KQL table
of the same name, created (``.create-merge table``, with a JSON ingestion mapping) from the batch's
schema on first use; the event columns ``_shape_table``, ``_shape_seq`` and, when the table has
one, ``_shape_event_time`` are columns too. With ``<table>``, every event goes there.

**The idempotency key is the pair of columns ``_shape_table``, ``_shape_seq``.** Streaming
ingestion does not deduplicate, so a batch the runtime repeats after a failure is in the table
twice; read it with :func:`dedupe_query` (``summarize take_any(*) by _shape_table, _shape_seq``),
which is exact because a repeat is the same row.

Delivery is at-least-once: ``emit`` returns after the service has answered 200 for every request.
A throttled service (HTTP 429 or 503) is waited for, with ``Retry-After`` when given; a dropped
connection or a 5xx raises ``ConnectionError`` (the runtime retries the batch); a 400 (a malformed
event, or streaming ingestion not enabled on the table) and a 401/403 are not retried and raise
``ShapeError``.

Sign-in: ``token`` (a bearer token, or a function returning one), else the environment variable
``SHAPE_EVENTHOUSE_TOKEN``, else Microsoft Entra through ``azure-identity`` (scope
``https://<host>/.default``). ``?tls=false`` talks plain HTTP without a token (a local emulator).

Options of :meth:`EventhouseEmitter.emit`: ``envelope`` (only ``"flat"``; a KQL table holds the
flat event), ``token``, ``max_request_bytes`` (default 3,000,000; the service limit is 4 MB),
``busy_retries`` (default 6), ``timeout`` (seconds per request, default 100), ``resuming``
(ignored).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple
from urllib.parse import parse_qs, unquote, urlsplit

import pyarrow as pa

from shape.errors import ShapeError
from shape.streaming.emit.formats import FIELD_TABLE, encode_events

from ._auth import default_credential, token_for
from .errors import AuthError
from .kusto import (
    KustoClient,
    KustoTarget,
    Transport,
    create_mapping_command,
    create_table_command,
    dedupe_query,
    kusto_type,
    mapping_name,
    streaming_policy_command,
)

__all__ = [
    "ENV_TOKEN",
    "EventhouseEmitter",
    "EventhouseTarget",
    "Transport",
    "create_mapping_command",
    "create_table_command",
    "dedupe_query",
    "kusto_type",
    "mapping_name",
    "parse_uri",
    "streaming_policy_command",
]

ENV_TOKEN = "SHAPE_EVENTHOUSE_TOKEN"
_BUSY_PAUSE = 0.5


class EventhouseTarget(NamedTuple):
    host: str
    database: str
    table: str | None
    tls: bool

    @property
    def base(self) -> str:
        return f"{'https' if self.tls else 'http'}://{self.host}"

    @property
    def kusto(self) -> KustoTarget:
        return KustoTarget(self.host, self.database, self.tls)


def parse_uri(uri: str) -> EventhouseTarget:
    parts = urlsplit(uri)
    segments = [unquote(s) for s in parts.path.split("/") if s]
    if parts.scheme != "eventhouse" or not parts.netloc or not 1 <= len(segments) <= 2:
        raise ShapeError(
            f"not an eventhouse URI: {uri!r} (eventhouse://<query-uri host>/<database>[/<table>])"
        )
    tls = parse_qs(parts.query).get("tls", ["true"])[0].lower() not in ("false", "0", "no")
    return EventhouseTarget(
        parts.netloc, segments[0], segments[1] if len(segments) == 2 else None, tls
    )


def token_source(target: KustoTarget, token: Any = None, credential: Any = None) -> Any:
    """A function giving the bearer token of each request: ``token`` (a string, or a function),
    else ``credential``, else ``SHAPE_EVENTHOUSE_TOKEN``, else Microsoft Entra through
    ``azure-identity`` (scope ``https://<host>/.default``). ``None`` over plain HTTP."""
    if not target.tls:
        return None
    scope = f"https://{target.host}/.default"
    if callable(token):
        return lambda: str(token())
    given = token or os.environ.get(ENV_TOKEN)
    if given:
        return lambda: str(given)
    if credential is None:
        try:
            credential = default_credential()
        except AuthError as exc:
            raise ShapeError(
                f"give a token (the token option or {ENV_TOKEN}) or install azure-identity for "
                "Microsoft Entra sign-in"
            ) from exc
    return lambda: token_for(credential, scope)


class EventhouseEmitter:
    """Events to a KQL database by streaming ingestion.

    ``transport`` is for tests: it stands in for the HTTP call.
    """

    name = "eventhouse"
    schemes = ("eventhouse",)

    def __init__(self, transport: Transport | None = None, *, busy_pause: float = _BUSY_PAUSE):
        self._transport = transport
        self._busy_pause = busy_pause
        self._clients: dict[tuple[str, str, bool], KustoClient] = {}

    def _client(
        self,
        target: EventhouseTarget,
        token: Any,
        credential: Any,
        timeout: float,
        busy: int,
    ) -> KustoClient:
        key = (target.host, target.database, target.tls)
        client = self._clients.get(key)
        if client is None:
            client = KustoClient(
                target.kusto,
                token_source(target.kusto, token, credential),
                transport=self._transport,
                busy_pause=self._busy_pause,
                busy_retries=busy,
                timeout=timeout,
            )
            self._clients[key] = client
        return client

    def emit(
        self,
        uri: str,
        batches: Iterable[pa.RecordBatch],
        *,
        envelope: str = "flat",
        resuming: bool = False,
        token: Any = None,
        credential: Any = None,
        max_request_bytes: int = 3_000_000,
        busy_retries: int = 6,
        timeout: float = 100.0,
        **options: Any,
    ) -> int:
        """Send every batch; return the number of events, after the service accepted them."""
        if options:
            raise ShapeError(f"unknown eventhouse emitter options: {sorted(options)}")
        if envelope != "flat":
            raise ShapeError("the eventhouse emitter sends flat events (a KQL table holds columns)")
        target = parse_uri(uri)
        client = self._client(target, token, credential, timeout, busy_retries)
        sent = 0
        for batch in batches:
            if batch.num_rows == 0:
                continue
            tables = batch.column(FIELD_TABLE).to_pylist()
            start = 0
            for i in range(1, len(tables) + 1):
                if i == len(tables) or tables[i] != tables[start]:
                    part = batch.slice(start, i - start)
                    table = target.table or str(tables[start])
                    client.prepare(table, part.schema)
                    client.ingest_lines(
                        table, (ev.body for ev in encode_events(part, "flat")), max_request_bytes
                    )
                    start = i
            sent += batch.num_rows
        return sent

    def flush(self) -> None:
        return None  # every emit() returns after the service answered

    def close(self) -> None:
        self._clients.clear()
