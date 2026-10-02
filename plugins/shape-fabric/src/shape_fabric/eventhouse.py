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

import json
import os
import time
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, quote, unquote, urlsplit

import pyarrow as pa

from shape.errors import ShapeError
from shape.streaming.emit.formats import FIELD_TABLE, encode_events

ENV_TOKEN = "SHAPE_EVENTHOUSE_TOKEN"
_BUSY_PAUSE = 0.5

# transport(method, url, headers, body, timeout) -> (status, response headers, response body)
Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]


class EventhouseTarget(NamedTuple):
    host: str
    database: str
    table: str | None
    tls: bool

    @property
    def base(self) -> str:
        return f"{'https' if self.tls else 'http'}://{self.host}"


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


def kusto_type(t: pa.DataType) -> str:
    if pa.types.is_dictionary(t):
        return kusto_type(t.value_type)
    if pa.types.is_boolean(t):
        return "bool"
    if pa.types.is_integer(t):
        return "int" if t.bit_width <= 32 and pa.types.is_signed_integer(t) else "long"
    if pa.types.is_floating(t):
        return "real"
    if pa.types.is_decimal(t):
        return "decimal"
    if pa.types.is_timestamp(t) or pa.types.is_date(t):
        return "datetime"
    if pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_struct(t):
        return "dynamic"
    return "string"  # strings, time of day, binary (base64)


def _q(name: str) -> str:
    return "['" + name.replace("\\", "\\\\").replace("'", "\\'") + "']"


def create_table_command(table: str, schema: pa.Schema) -> str:
    cols = ", ".join(f"{_q(f.name)}:{kusto_type(f.type)}" for f in schema)
    return f".create-merge table {_q(table)} ({cols})"


def mapping_name(table: str) -> str:
    return "shape_json"


def create_mapping_command(table: str, schema: pa.Schema) -> str:
    cols = [
        {"column": f.name, "path": "$[" + json.dumps(f.name) + "]", "datatype": kusto_type(f.type)}
        for f in schema
    ]
    # The mapping is a KQL string literal, where a backslash starts an escape: the JSON's own
    # backslashes (the quotes inside each path) must be doubled or the service reads broken JSON.
    body = json.dumps(cols).replace("\\", "\\\\").replace("'", "\\'")
    return (
        f".create-or-alter table {_q(table)} ingestion json mapping '{mapping_name(table)}' "
        f"'{body}'"
    )


def streaming_policy_command(table: str) -> str:
    return f".alter table {_q(table)} policy streamingingestion enable"


def dedupe_query(table: str) -> str:
    """The KQL that reads ``table`` with at-least-once repeats collapsed to one row per key."""
    return f"{_q(table)} | summarize take_any(*) by _shape_table, _shape_seq"


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    req = urlrequest.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            return resp.status, dict(resp.headers), resp.read()
    except urlerror.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except (urlerror.URLError, TimeoutError) as exc:
        raise ConnectionError(f"eventhouse: {exc}") from exc


class EventhouseEmitter:
    """Events to a KQL database by streaming ingestion.

    ``transport`` is for tests: it stands in for the HTTP call.
    """

    name = "eventhouse"
    schemes = ("eventhouse",)

    def __init__(self, transport: Transport | None = None, *, busy_pause: float = _BUSY_PAUSE):
        self._transport = transport or _urllib_transport
        self._busy_pause = busy_pause
        self._prepared: set[tuple[str, str, str, str]] = set()

    # ------------------------------------------------------------------ auth
    def _token(self, target: EventhouseTarget, given: Any) -> str | None:
        if not target.tls:
            return None
        if callable(given):
            return str(given())
        token = given or os.environ.get(ENV_TOKEN)
        if token:
            return str(token)
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise ShapeError(
                f"give a token (the token option or {ENV_TOKEN}) or install azure-identity for "
                "Microsoft Entra sign-in"
            ) from exc
        return str(DefaultAzureCredential().get_token(f"https://{target.host}/.default").token)

    # ------------------------------------------------------------------ http
    def _call(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
        busy: int,
    ) -> bytes:
        pause = self._busy_pause
        attempt = 0
        while True:
            status, resp_headers, data = self._transport(method, url, headers, body, timeout)
            if status < 300:
                return data
            text = data[:500].decode("utf-8", "replace")
            if status in (429, 503):
                attempt += 1
                if attempt > busy:
                    raise ConnectionError(f"eventhouse: the service stayed busy ({status})")
                retry_after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else pause)
                pause *= 2
            elif status in (401, 403):
                raise ShapeError(f"eventhouse: not authorised ({status}): {text}")
            elif status >= 500 or status == 408:
                raise ConnectionError(f"eventhouse: the service failed ({status}): {text}")
            else:
                raise ShapeError(f"eventhouse: request refused ({status}): {text}")

    def _prepare(
        self,
        target: EventhouseTarget,
        table: str,
        schema: pa.Schema,
        headers: dict[str, str],
        timeout: float,
        busy: int,
    ) -> None:
        mark = (target.base, target.database, table, schema.to_string())
        if mark in self._prepared:
            return
        for command, required in (
            (create_table_command(table, schema), True),
            (create_mapping_command(table, schema), True),
            # Streaming ingestion is on by default in a Fabric Eventhouse; where it is not, this
            # enables it. A principal that may ingest but not alter policies is not an error.
            (streaming_policy_command(table), False),
        ):
            body = json.dumps({"db": target.database, "csl": command}).encode("utf-8")
            try:
                self._call("POST", f"{target.base}/v1/rest/mgmt", headers, body, timeout, busy)
            except ShapeError:
                if required:
                    raise
        self._prepared.add(mark)

    # ------------------------------------------------------------------ emit
    def emit(
        self,
        uri: str,
        batches: Iterable[pa.RecordBatch],
        *,
        envelope: str = "flat",
        resuming: bool = False,
        token: Any = None,
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
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        bearer = self._token(target, token)
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
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
                    self._ingest(
                        target, table, part, headers, timeout, busy_retries, max_request_bytes
                    )
                    start = i
            sent += batch.num_rows
        return sent

    def _ingest(
        self,
        target: EventhouseTarget,
        table: str,
        batch: pa.RecordBatch,
        headers: dict[str, str],
        timeout: float,
        busy: int,
        max_bytes: int,
    ) -> None:
        self._prepare(target, table, batch.schema, headers, timeout, busy)
        url = (
            f"{target.base}/v1/rest/ingest/{quote(target.database, safe='')}/"
            f"{quote(table, safe='')}?streamFormat=JSON&mappingName={mapping_name(table)}"
        )
        ingest_headers = {**headers, "Content-Type": "application/json; charset=utf-8"}
        chunk: list[bytes] = []
        size = 0
        for ev in encode_events(batch, "flat"):
            if chunk and size + len(ev.body) + 1 > max_bytes:
                self._call("POST", url, ingest_headers, b"\n".join(chunk) + b"\n", timeout, busy)
                chunk, size = [], 0
            chunk.append(ev.body)
            size += len(ev.body) + 1
        if chunk:
            self._call("POST", url, ingest_headers, b"\n".join(chunk) + b"\n", timeout, busy)

    def flush(self) -> None:
        return None  # every emit() returns after the service answered

    def close(self) -> None:
        self._prepared.clear()
