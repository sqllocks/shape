"""Paged Fabric Eventhouse tables through the KQL query endpoint."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from .eventhouse import token_source
from .kusto import KustoClient, KustoTarget, Transport, dedupe_query, q

SHAPE_API = "1.0"
TYPE_MAP = {
    "long": pa.int64(),
    "int": pa.int32(),
    "real": pa.float64(),
    "decimal": pa.decimal256(57, 28),
    "datetime": pa.timestamp("us", tz="UTC"),
    "timespan": pa.duration("ns"),
    "bool": pa.bool_(),
    "string": pa.string(),
    "guid": pa.string(),
    "dynamic": pa.string(),
}
_INTERNAL = {"_shape_table", "_shape_seq"}


def parse(uri: str) -> tuple[KustoTarget, str | None, bool]:
    try:
        parts = urlsplit(uri)
        query = parse_qs(parts.query, keep_blank_values=True)
        if (
            parts.scheme != "eventhouse"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
            or len(parts.path.strip("/").split("/")) != 1
            or not parts.path.strip("/")
        ):
            raise ValueError
        if set(query) - {"table", "tls", "dedupe"} or any(len(v) != 1 for v in query.values()):
            raise ValueError
        for key in ("tls", "dedupe"):
            if key in query and query[key][0].lower() not in ("true", "false"):
                raise ValueError
        table = query.get("table", [None])[0]
        if table == "":
            raise ValueError
        return (
            KustoTarget(
                parts.netloc,
                unquote(parts.path.strip("/")),
                query.get("tls", ["true"])[0].lower() == "true",
            ),
            table,
            query.get("dedupe", ["false"])[0].lower() == "true",
        )
    except ValueError:
        raise ShapeError(
            "eventhouse source URI must be eventhouse://<host>/<database>?table=T"
            "[&tls=false][&dedupe=true]; credentials belong in options or the environment"
        ) from None


def _limit(
    options: dict[str, Any], key: str, default: int, low: int, high: int | None = None
) -> int:
    value = options.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < low
        or (high is not None and value > high)
    ):
        raise ShapeError(f"{key} must be an integer between {low} and {high or 'unlimited'}")
    return value


def _convert(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "dynamic":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass  # a transport may already have decoded a dynamic string scalar
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if kind == "decimal":
        return Decimal(str(value))
    if kind == "datetime":
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    if kind == "timespan":
        match = re.fullmatch(r"(-)?(?:(\d+)\.)?(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,7}))?", str(value))
        if not match:
            raise ValueError("invalid timespan")
        sign, days, hours, minutes, seconds, fraction = match.groups()
        if int(hours) > 23 or int(minutes) > 59 or int(seconds) > 59:
            raise ValueError("invalid timespan")
        nanos = (
            ((int(days or 0) * 24 + int(hours)) * 60 + int(minutes)) * 60 + int(seconds)
        ) * 10**9 + int((fraction or "").ljust(9, "0"))
        return -nanos if sign else nanos
    return value


class _ReadClient(KustoClient):
    """Reject partial results and keep service error bodies out of user errors."""

    def _json_call(self, path: str, csl: str) -> Any:
        try:
            doc = super()._json_call(path, csl)
        except (ShapeError, ConnectionError):
            raise ShapeError("eventhouse read request failed or table not found") from None
        if not isinstance(doc, dict) or doc.get("error") or doc.get("Exceptions"):
            raise ShapeError("eventhouse read failed; no partial result is used")
        tables = doc.get("Tables", [])
        if not tables or any(
            t.get("TableKind") == "QueryCompletionInformation"
            and any("error" in str(r).lower() for r in t.get("Rows", []))
            for t in tables
        ):
            raise ShapeError("eventhouse read failed; no partial result is used")
        for table in tables:
            if table.get("TableName") != "QueryStatus":
                continue
            names = [c.get("ColumnName") for c in table.get("Columns", [])]
            for row in table.get("Rows", []):
                status = dict(zip(names, row, strict=True))
                if status.get("StatusCode") not in (None, 0, "0") or str(
                    status.get("SeverityName", "")
                ).lower() in ("error", "fatal", "critical"):
                    raise ShapeError(
                        "eventhouse query was truncated or failed; no partial result is used; "
                        "reduce batch_size for the 64 MB result limit"
                    )
        return doc


class EventhouseSource:
    name = "eventhouse"
    schemes = ("eventhouse",)

    def __init__(self, transport: Transport | None = None):
        self._transport = transport

    def can_open(self, uri: str) -> bool:
        return uri.startswith("eventhouse://")

    def _client(self, uri: str, options: dict[str, Any]) -> tuple[KustoClient, str | None, bool]:
        target, table, dedupe = parse(uri)
        return (
            _ReadClient(
                target,
                token_source(target, options.get("token"), options.get("credential")),
                transport=self._transport,
            ),
            table,
            dedupe,
        )

    def _schema(self, client: KustoClient, table: str) -> tuple[pa.Schema, list[str]]:
        doc = client.mgmt(f".show table {q(table)} schema as json")
        try:
            response = doc["Tables"][0]
            index = next(
                i for i, c in enumerate(response["Columns"]) if c["ColumnName"] == "Schema"
            )
            cols = json.loads(response["Rows"][0][index])["OrderedColumns"]
            kinds = [c["CslType"].lower() for c in cols]
            if any(k not in TYPE_MAP for k in kinds):
                raise ShapeError("eventhouse schema contains an unsupported KQL type")
            return pa.schema(
                [pa.field(c["Name"], TYPE_MAP[k]) for c, k in zip(cols, kinds, strict=True)]
            ), kinds
        except (KeyError, IndexError, TypeError, ValueError, StopIteration):
            raise ShapeError("eventhouse table schema is missing or malformed") from None

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        client, table, _ = self._client(uri, options)
        if table is None:
            raise ShapeError(
                "eventhouse schema needs table=; profiling without table reads every table"
            )
        return self._schema(client, table)[0]

    def _read(
        self, client: KustoClient, table: str, dedupe: bool, options: dict[str, Any]
    ) -> Iterator[pa.RecordBatch]:
        size = _limit(options, "batch_size", 65536, 1, 500000)
        sample = _limit(options, "sample_rows", 1000, 0)
        schema, kinds = self._schema(client, table)
        base = dedupe_query(table) if dedupe and _INTERNAL <= set(schema.names) else q(table)
        if dedupe and _INTERNAL <= set(schema.names):
            base += " | project " + ", ".join(q(n) for n in schema.names)
        if {"__shape_read_row", "__shape_read_order"} & set(schema.names):
            raise ShapeError("eventhouse source reserves __shape_read_row and __shape_read_order")
        if sample:
            base += f" | sample {sample}"
            # Materialize the sample once so paging cannot independently resample each page.
            size = min(size, sample)
        offset = 0
        # Sampling is fetched once (up to the service row cap), then split into batches.
        if sample:
            if sample > 500000:
                raise ShapeError(
                    "sample_rows exceeds the 500000-row query cap; use sample_rows=0 for paging"
                )
            rows = client.query(base)
            for start in range(0, len(rows), size):
                yield self._batch(rows[start : start + size], schema, kinds)
            return
        order = "tostring(pack_array(" + ", ".join(q(n) for n in schema.names) + "))"
        while True:
            rows = client.query(
                f"{base} | extend __shape_read_order = {order} "
                f"| sort by ingestion_time() asc, __shape_read_order asc | serialize "
                f"| extend __shape_read_row = row_number() "
                f"| where __shape_read_row > {offset} "
                f"and __shape_read_row <= {offset + size} "
                "| project-away __shape_read_row, __shape_read_order"
            )
            if len(rows) > size:
                raise ShapeError("eventhouse query returned more rows than the requested page")
            if not rows:
                return
            yield self._batch(rows, schema, kinds)
            offset += len(rows)
            if len(rows) < size:
                return

    def _batch(self, rows: list[list[Any]], schema: pa.Schema, kinds: list[str]) -> pa.RecordBatch:
        try:
            if any(len(row) != len(schema) for row in rows):
                raise ValueError
            return pa.RecordBatch.from_arrays(
                [
                    pa.array([_convert(row[i], kind) for row in rows], type=schema[i].type)
                    for i, kind in enumerate(kinds)
                ],
                schema=schema,
            )
        except (ValueError, TypeError, OverflowError, pa.ArrowException):
            raise ShapeError(
                "eventhouse returned a value incompatible with its declared schema"
            ) from None

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        client, table, dedupe = self._client(uri, options)
        if table is None:
            raise ShapeError("eventhouse read needs table=")
        return self._read(client, table, dedupe, options)

    def profile_tables(
        self, uri: str, **options: Any
    ) -> tuple[dict[str, pa.Table], dict[str, dict[str, Any]]]:
        client, table, dedupe = self._client(uri, options)
        names = (
            [table]
            if table
            else [row[0] for row in client.mgmt(".show tables")["Tables"][0]["Rows"]]
        )
        if not names:
            raise ShapeError("eventhouse database has no tables")
        tables, metadata = {}, {}
        for name in names:
            schema, _ = self._schema(client, name)
            data = pa.Table.from_batches(
                list(self._read(client, name, dedupe, options)), schema=schema
            )
            details = client.mgmt(f".show table {q(name)} details")["Tables"][0]
            count_index = next(
                (
                    i
                    for i, c in enumerate(details["Columns"])
                    if c["ColumnName"] in ("TotalRowCount", "RowCount")
                ),
                None,
            )
            count = (
                details["Rows"][0][count_index]
                if count_index is not None and details["Rows"]
                else None
            )
            metadata[name] = {
                "sampled_rows": data.num_rows,
                "sample_method": "sample" if options.get("sample_rows", 1000) else "none",
                "catalog_rows": count,
            }
            tables[name] = data.drop([n for n in data.column_names if n in _INTERNAL])
        return tables, metadata
