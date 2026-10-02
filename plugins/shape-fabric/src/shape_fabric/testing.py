"""In-memory stand-ins for the contract tests and the plugin kit.

``EventstreamHarness`` is the Event Hubs fake behind an ``EventstreamEmitter``;
``EventhouseHarness`` is a fake Kusto service (management commands and streaming ingestion) behind
an ``EventhouseEmitter``. Both follow the emitter contract's harness protocol
(``shape.streaming.emit.contract``).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from shape_eventhubs.testing import EmitterHarness as _HubHarness

from .eventhouse import EventhouseEmitter
from .eventstream import EventstreamEmitter


class EventstreamHarness(_HubHarness):
    scheme = "eventstream"

    def __init__(self, max_batch_bytes: int = 16_384) -> None:
        super().__init__("es", max_batch_bytes)
        self.uri = "eventstream://my-eventstream"

    def make(self) -> Any:
        return EventstreamEmitter(self.hub.factory, busy_pause=0.001)


class FakeKusto:
    """What a Kusto service does with the two calls the emitter makes."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []  # (database, csl)
        self.rows: list[dict[str, Any]] = []  # delivered rows, in order
        self.requests: list[tuple[str, int]] = []  # (table, bytes) of each accepted ingest
        self.auth: list[str | None] = []
        self.failures = 0
        self.busy = 0
        self.hits = 0
        self.calls = 0

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls += 1
        self.auth.append(headers.get("Authorization"))
        parts = urlsplit(url)
        if parts.path == "/v1/rest/mgmt":
            doc = json.loads(body)
            self.commands.append((doc["db"], doc["csl"]))
            return 200, {}, b"{}"
        if self.busy > 0:
            self.busy -= 1
            self.hits += 1
            return 429, {"Retry-After": "0"}, b"throttled"
        if self.failures > 0:
            self.failures -= 1
            return 503 if self.failures % 2 else 500, {}, b"service failure"
        _, _, _, _, _db, table = parts.path.split("/")
        query = parse_qs(parts.query)
        assert query["streamFormat"] == ["JSON"] and query["mappingName"], query
        lines = [json.loads(x) for x in body.splitlines()]
        self.rows.extend(lines)
        self.requests.append((unquote(table), len(body)))
        return 200, {}, b"{}"


class EventhouseHarness:
    def __init__(self, uri: str = "eventhouse://kql.example.test/db1?tls=false", **kw: Any) -> None:
        self.kusto = FakeKusto()
        self.uri = uri
        self.kw = kw

    def make(self) -> Any:
        return EventhouseEmitter(self.kusto, busy_pause=0.001)

    def delivered(self) -> list[tuple[str, bytes]]:
        return [
            (f"{r['_shape_table']}/{r['_shape_seq']}", json.dumps(r).encode())
            for r in self.kusto.rows
        ]

    def inject_failures(self, n: int) -> None:
        self.kusto.failures = n

    def congest(self, n: int) -> None:
        self.kusto.busy = n

    def congestion_hits(self) -> int:
        return self.kusto.hits
