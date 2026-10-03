"""The eventhouse emitter: the JSON mapping in force always fits the batch ingested (#419), and
each emit's connection options apply (#444)."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pyarrow as pa
from shape_fabric import EventhouseEmitter
from shape_fabric.testing import FakeKusto

from shape.streaming.emit.formats import with_event_fields


class MappingKusto(FakeKusto):
    """A fake Kusto that also keeps each table's ingestion mappings, as the service does: one
    mapping per (table, name), replaced by ``.create-or-alter``."""

    def __init__(self) -> None:
        super().__init__()
        self.mappings: dict[tuple[str, str], list[str]] = {}
        self.unmapped: list[tuple[str, list[str]]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        parts = urlsplit(url)
        if parts.path == "/v1/rest/mgmt":
            csl = json.loads(body)["csl"]
            found = re.match(
                r"\.create-or-alter table \['((?:[^'\\]|\\.)*)'\] ingestion json mapping "
                r"'([^']*)' '(.*)'$",
                csl,
            )
            if found:
                table, name, literal = found.groups()
                cols = json.loads(re.sub(r"\\(.)", r"\1", literal))
                self.mappings[(table, name)] = [c["column"] for c in cols]
        elif parts.path.startswith("/v1/rest/ingest/"):
            table = unquote(parts.path.rsplit("/", 1)[1])
            name = parse_qs(parts.query)["mappingName"][0]
            mapped = self.mappings.get((table, name), [])
            for line in body.splitlines():
                missing = [k for k in json.loads(line) if k not in mapped]
                if missing:
                    self.unmapped.append((table, missing))
        return super().__call__(method, url, headers, body, timeout)


def _event(table: str, seq: int, **cols: Any) -> pa.RecordBatch:
    return with_event_fields(
        pa.record_batch({k: pa.array([v]) for k, v in cols.items()}), table, seq
    )


def test_alternating_tables_into_one_kql_table_keep_their_columns() -> None:
    kusto = MappingKusto()
    batches = [
        _event("A", 0, a_col=1),
        _event("B", 0, b_col="x"),
        _event("A", 1, a_col=2),
        _event("B", 1, b_col="y"),
    ]
    sent = EventhouseEmitter(kusto).emit(
        "eventhouse://kql.example.test/db1/AllEvents?tls=false", batches
    )
    assert sent == 4
    assert kusto.unmapped == []


def test_one_table_per_shape_table_needs_one_mapping_each() -> None:
    kusto = MappingKusto()
    batches = [_event("A", 0, a_col=1), _event("B", 0, b_col="x"), _event("A", 1, a_col=2)]
    EventhouseEmitter(kusto).emit("eventhouse://kql.example.test/db1?tls=false", batches)
    assert kusto.unmapped == []
    assert sum("mapping" in c for _, c in kusto.commands) == 2  # nothing to redo


def test_each_emit_uses_its_own_token_retries_and_timeout() -> None:
    kusto = FakeKusto()
    emitter = EventhouseEmitter(kusto, busy_pause=0.0)
    uri = "eventhouse://kql.example.test/db1?tls=false"
    emitter.emit(uri, [_event("A", 0, a_col=1)], token="tok-1")
    before = len(kusto.auth)
    kusto.busy = 1  # one throttled answer: with busy_retries=0 it is not waited out
    try:
        emitter.emit(uri, [_event("A", 1, a_col=2)], token="tok-2", busy_retries=0, timeout=1)
    except Exception:
        pass
    else:
        raise AssertionError("busy_retries=0 was ignored: the throttled request was repeated")
    assert set(kusto.auth[before:]) == {"Bearer tok-2"}
