"""The Kusto (Eventhouse) transport shared by the ``eventhouse://`` emitter and the batch writer.

One client: management commands (``/v1/rest/mgmt``), queries (``/v1/rest/query``) and streaming
ingestion (``/v1/rest/ingest``), with the same retry rules for both callers:

* HTTP 429 and 503 are waited for (``Retry-After`` honoured, doubling pause), up to
  ``busy_retries`` times, then ``ConnectionError``;
* 5xx, 408 and a dropped connection raise ``ConnectionError`` (the caller may retry);
* 400 and the other 4xx raise :class:`ShapeError`; 401 and 403 raise :class:`AuthError`.

Every name that reaches a command is quoted (``['...']``, ``\\`` and ``'`` escaped) and checked,
and the mapping document is a proper KQL string literal, so a column called ``a'b`` or ``a"b``
cannot change the command. Quoting does not make a name *valid*, though: the engine accepts only
letters, digits, ``_``, space, ``.`` and ``-`` in a column name, so any other character is
replaced by ``_`` in the column (see :func:`column_names`); the mapping's JSON path keeps the
event's own key, so the values still land in the column.

A table created a moment ago is not always ready: its first ingest request waits (with backoff,
up to ``ready_timeout``) while the answer is "entity not found" or a streaming-ingestion
initialisation error.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, NamedTuple
from urllib import error as urlerror
from urllib.parse import quote

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.scale.http import bearer_request, opener, redirected_away

from .errors import AuthError

# transport(method, url, headers, body, timeout) -> (status, response headers, response body)
Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]
MAPPING_NAME = "shape_json"
_BUSY_PAUSE = 0.5
_MAX_NAME = 1024


class KustoTarget(NamedTuple):
    host: str
    database: str
    tls: bool = True

    @property
    def base(self) -> str:
        return f"{'https' if self.tls else 'http'}://{self.host}"


# Redirects stay on the request's origin: urllib's default handler copies the Authorization
# header to any host a Location names (#275).
_OPENER = opener()


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    req = bearer_request(method, url, headers, body)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            if redirected_away(url, resp):
                raise ConnectionError(
                    "eventhouse: the request was redirected to another origin; its answer is "
                    "not used"
                )
            return resp.status, dict(resp.headers), resp.read()
    except urlerror.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except (urlerror.URLError, TimeoutError) as exc:
        raise ConnectionError(f"eventhouse: {exc}") from exc


# --- commands ----------------------------------------------------------------------------


def check_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > _MAX_NAME:
        raise ShapeError(f"not a usable KQL name: {name!r}")
    if any(ord(ch) < 32 for ch in name):
        raise ShapeError("a KQL name cannot contain control characters")
    return name


def q(name: str) -> str:
    """``name`` as a quoted KQL identifier: ``['name']``."""
    check_name(name)
    return "['" + name.replace("\\", "\\\\").replace("'", "\\'") + "']"


def string_literal(text: str) -> str:
    """``text`` as a single-quoted KQL string literal."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def kusto_type(t: pa.DataType) -> str:
    if pa.types.is_dictionary(t):
        return kusto_type(t.value_type)
    if pa.types.is_boolean(t):
        return "bool"
    if pa.types.is_uint64(t):
        return "decimal"  # above 2**63 - 1 a long overflows (the SQL writers use DECIMAL(20,0))
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


def column_name(name: str) -> str:
    """``name`` as a valid Kusto column name: every character other than a letter, digit, ``_``,
    space, ``.`` or ``-`` becomes ``_`` (``say "hi"`` -> ``say _hi_``)."""
    check_name(name)
    return "".join(ch if ch.isalnum() or ch in "_ .-" else "_" for ch in name)


def column_names(schema: pa.Schema) -> list[str]:
    """The column of each field of ``schema``: :func:`column_name`, made distinct by a numeric
    suffix when two names map to the same column (``a'b`` and ``a"b``)."""
    taken: set[str] = set()
    out: list[str] = []
    for f in schema:
        base = name = column_name(f.name)
        n = 1
        while name in taken:
            n += 1
            name = f"{base}_{n}"
        taken.add(name)
        out.append(name)
    return out


def _column_list(schema: pa.Schema) -> str:
    return ", ".join(
        f"{q(col)}:{kusto_type(f.type)}"
        for col, f in zip(column_names(schema), schema, strict=True)
    )


def create_table_command(table: str, schema: pa.Schema) -> str:
    return f".create-merge table {q(table)} ({_column_list(schema)})"


def create_strict_table_command(table: str, schema: pa.Schema) -> str:
    return f".create table {q(table)} ({_column_list(schema)})"


def mapping_name(table: str) -> str:
    return MAPPING_NAME


def create_mapping_command(table: str, schema: pa.Schema) -> str:
    cols = [
        {"column": col, "path": "$[" + json.dumps(f.name) + "]", "datatype": kusto_type(f.type)}
        for col, f in zip(column_names(schema), schema, strict=True)
    ]
    return (
        f".create-or-alter table {q(table)} ingestion json mapping "
        f"'{mapping_name(table)}' {string_literal(json.dumps(cols))}"
    )


def streaming_policy_command(table: str) -> str:
    return f".alter table {q(table)} policy streamingingestion enable"


def clear_schema_cache_command(table: str) -> str:
    return f".clear table {q(table)} cache streamingingestion schema"


def drop_table_command(table: str) -> str:
    return f".drop table {q(table)} ifexists"


def show_table_command(table: str) -> str:
    # a literal, not an identifier: .show tables | where TableName == '<name>'
    return f".show tables | where TableName == {string_literal(check_name(table))} | count"


def dedupe_query(table: str) -> str:
    """The KQL that reads ``table`` with at-least-once repeats collapsed to one row per key."""
    return f"{q(table)} | summarize take_any(*) by _shape_table, _shape_seq"


def not_ready(exc: Exception) -> bool:
    """A new table the service has not finished setting up: it is not found yet, or streaming
    ingestion on it is still initialising."""
    text = str(exc)
    return "EntityNotFound" in text or "StreamingIngestion" in text


# --- client ------------------------------------------------------------------------------


class KustoClient:
    """Calls to one Kusto database.

    ``token`` is a function returning the bearer token for each request (``None`` when the
    service wants none, as an emulator over plain HTTP). ``transport`` stands in for HTTP.
    """

    def __init__(
        self,
        target: KustoTarget,
        token: Callable[[], str | None] | None = None,
        *,
        transport: Transport | None = None,
        busy_pause: float = _BUSY_PAUSE,
        busy_retries: int = 6,
        timeout: float = 100.0,
        ready_timeout: float = 120.0,
    ) -> None:
        self.target = target
        self._token = token
        self._transport = transport or urllib_transport
        self._busy_pause = busy_pause
        self.busy_retries = busy_retries
        self.timeout = timeout
        self.ready_timeout = ready_timeout
        self._warm: set[str] = set()  # tables that have accepted a request
        self._prepared: set[tuple[str, str]] = set()
        self.accepted = 0  # ingestion requests the service has accepted, over the client's life

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        bearer = self._token() if self._token else None
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        return headers

    def _call(self, method: str, url: str, content_type: str, body: bytes) -> bytes:
        pause = self._busy_pause
        attempt = 0
        while True:
            status, resp_headers, data = self._transport(
                method, url, self._headers(content_type), body, self.timeout
            )
            if status < 300:
                return data
            text = data[:500].decode("utf-8", "replace")
            if status in (429, 503):
                attempt += 1
                if attempt > self.busy_retries:
                    raise ConnectionError(f"eventhouse: the service stayed busy ({status})")
                retry_after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else pause)
                pause *= 2
            elif status in (401, 403):
                raise AuthError(f"eventhouse: not authorised ({status}): {text}")
            elif status >= 500 or status == 408:
                raise ConnectionError(f"eventhouse: the service failed ({status}): {text}")
            else:
                raise ShapeError(f"eventhouse: request refused ({status}): {text}")

    def _json_call(self, path: str, csl: str) -> Any:
        body = json.dumps({"db": self.target.database, "csl": csl}).encode("utf-8")
        data = self._call("POST", f"{self.target.base}{path}", "application/json", body)
        try:
            return json.loads(data or b"{}")
        except ValueError:
            return {}

    def mgmt(self, csl: str) -> Any:
        """Run a management command (``.create table ...``)."""
        return self._json_call("/v1/rest/mgmt", csl)

    def query(self, csl: str) -> list[list[Any]]:
        """The rows of the first table of a query's answer."""
        doc = self._json_call("/v1/rest/query", csl)
        tables = doc.get("Tables") or []
        return list(tables[0].get("Rows") or []) if tables else []

    def table_exists(self, table: str) -> bool:
        doc = self.mgmt(show_table_command(table))
        tables = doc.get("Tables") or []
        rows = tables[0].get("Rows") if tables else None
        return bool(rows and rows[0] and int(rows[0][0]) > 0)

    def prepare(self, table: str, schema: pa.Schema, *, create: str = "merge") -> None:
        """Create (``create="merge"``: or extend) the table, its JSON mapping and its streaming
        policy; again only when another schema was prepared for the table since (its JSON mapping
        replaced this one). ``create="strict"`` fails if the table exists."""
        mark = (table, schema.to_string())
        if mark in self._prepared:
            return
        first = (
            create_strict_table_command(table, schema)
            if create == "strict"
            else create_table_command(table, schema)
        )
        self.mgmt(first)
        self.mgmt(create_mapping_command(table, schema))
        try:
            # Streaming ingestion is on by default in a Fabric Eventhouse; where it is not, this
            # enables it. A principal that may ingest but not alter policies is not an error.
            self.mgmt(streaming_policy_command(table))
        except (ShapeError, AuthError):
            pass
        # Streaming nodes cache schema independently of successful management commands.
        # Synchronize the table and mapping before sending data; otherwise the first ingest
        # can still report EntityNotFound for minutes after creation.
        self._clear_schema_cache(table)
        # A table has one mapping by that name, and this one replaced it: another schema
        # prepared for the same table must send its own mapping again.
        self.forget(table)
        self._prepared.add(mark)

    def _clear_schema_cache(self, table: str) -> None:
        deadline = time.monotonic() + self.ready_timeout
        pause = self._busy_pause
        while True:
            doc = self.mgmt(clear_schema_cache_command(table))
            try:
                result = doc["Tables"][0]
                names = [column["ColumnName"] for column in result["Columns"]]
                status_column = names.index("Status")
                statuses = [row[status_column] for row in result["Rows"]]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ShapeError("eventhouse: invalid streaming schema-cache response") from exc
            if not statuses or any(status not in ("Succeeded", "Failed") for status in statuses):
                raise ShapeError("eventhouse: invalid streaming schema-cache node status")
            if all(status == "Succeeded" for status in statuses):
                return
            # The command is safe to repeat when any node reports Failed. Keep the existing
            # readiness bound and refuse to ingest until every node has synchronized.
            if time.monotonic() + pause > deadline:
                raise ShapeError("eventhouse: streaming schema-cache synchronization failed")
            time.sleep(pause)
            pause = min(pause * 2, 10.0)

    def forget(self, table: str) -> None:
        self._prepared = {m for m in self._prepared if m[0] != table}
        self._warm.discard(table)

    def ingest(self, table: str, body: bytes, *, wait_ready: bool = False) -> None:
        """One streaming-ingestion request: JSON lines for ``table`` (at most 4 MB). With
        ``wait_ready``, the first request to a table waits up to ``ready_timeout`` for a table
        created a moment ago (nothing was ingested by a request answered "not ready")."""
        if wait_ready and table not in self._warm:
            deadline = time.monotonic() + self.ready_timeout
            pause = self._busy_pause
            while True:
                try:
                    self._ingest_once(table, body)
                    break
                except (ShapeError, ConnectionError) as exc:
                    if not not_ready(exc) or time.monotonic() + pause > deadline:
                        raise
                    time.sleep(pause)
                    pause = min(pause * 2, 10.0)
            self._warm.add(table)
            return
        self._ingest_once(table, body)

    def _ingest_once(self, table: str, body: bytes) -> None:
        url = (
            f"{self.target.base}/v1/rest/ingest/{quote(self.target.database, safe='')}/"
            f"{quote(table, safe='')}?streamFormat=JSON&mappingName={mapping_name(table)}"
        )
        self._call("POST", url, "application/json; charset=utf-8", body)
        self.accepted += 1

    def ingest_lines(self, table: str, lines: Any, max_bytes: int) -> int:
        """Send JSON ``lines`` (bytes each) in requests of at most ``max_bytes``; the number of
        requests made. A single line above the limit goes alone (the service decides)."""
        requests = 0
        chunk: list[bytes] = []
        size = 0
        for line in lines:
            if chunk and size + len(line) + 1 > max_bytes:
                self.ingest(table, b"\n".join(chunk) + b"\n", wait_ready=True)
                requests += 1
                chunk, size = [], 0
            chunk.append(line)
            size += len(line) + 1
        if chunk:
            self.ingest(table, b"\n".join(chunk) + b"\n", wait_ready=True)
            requests += 1
        return requests
