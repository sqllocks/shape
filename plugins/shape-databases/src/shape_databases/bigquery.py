"""BigQuery Parquet load sink and W9-01 catalog/Arrow source.

SDKs are optional and imported only when opening a connection. Credentials are ADC or
references; all SQL consists of validated identifiers and bound parameters.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _cloud, _sql
from ._auth import Secret, _resolve_reference, scrub
from ._base import DatabaseSink, Plan, Target, as_int, as_text, chain_batches, parse_uri
from .errors import CredentialError, WriteError
from .sources import DatabaseSource, _integer

SHAPE_API = "1.0"
INSTALL = "pip install 'sqllocks-shape-databases[bigquery]'"
DEFAULT_MAX_BYTES = 1 << 30


def identifier(value: Any, kind: str = "column") -> str:
    """Validate names with the common checker, then BigQuery's portable identifier subset."""
    value = _sql.check_identifier(value, kind, "mysql")
    pattern = r"[A-Za-z0-9][A-Za-z0-9_-]*" if kind == "project" else r"[A-Za-z_][A-Za-z0-9_]*"
    if not re.fullmatch(pattern, value):
        raise ShapeError(f"invalid BigQuery {kind} name")
    return str(value)


def quote(value: str) -> str:
    return "`" + identifier(value) + "`"


def column_type(field: pa.Field, options: Mapping[str, Any], truncate_ns: bool = False) -> str:
    """Return the exact BigQuery type, refusing lossy or invalid declarations."""
    t = field.type
    if (field.metadata or {}).get(b"shape.type") == b"uuid" or isinstance(
        t, getattr(pa, "UuidType", ())
    ):
        return "STRING"
    if pa.types.is_uint64(t):
        return "NUMERIC(20,0)"
    if pa.types.is_integer(t):
        return "INT64"
    if pa.types.is_floating(t):
        return "FLOAT64"
    if pa.types.is_decimal(t):
        p, s = t.precision, t.scale
        if not 0 <= s <= p:
            raise ShapeError(f"column {field.name!r}: decimal requires 0 <= scale <= precision")
        if p - s <= 29 and s <= 9:
            return f"NUMERIC({p},{s})"
        if p - s <= 38 and s <= 38:
            return f"BIGNUMERIC({p},{s})"
        raise ShapeError(f"column {field.name!r}: decimal exceeds BigQuery precision/scale")
    if pa.types.is_timestamp(t) or pa.types.is_time(t):
        if t.unit == "ns" and not truncate_ns:
            raise ShapeError(f"column {field.name!r}: nanoseconds require truncate_ns=true")
        return ("TIMESTAMP" if t.tz else "DATETIME") if pa.types.is_timestamp(t) else "TIME"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        length = options.get("max_length")
        if length is not None:
            _integer(length, f"column {field.name} max_length", 1)
            return f"STRING({length})"
        return "STRING"
    if pa.types.is_boolean(t):
        return "BOOL"
    if pa.types.is_date32(t):
        return "DATE"
    if pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        return "BYTES"
    if _cloud.is_nested(t):
        return "STRING"
    raise ShapeError(f"column {field.name!r}: unsupported BigQuery type {t}")


def arrow_field(name: str, declared: str, nullable: bool) -> pa.Field:
    """Map a catalog type back to Arrow without inventing decimal precision."""
    identifier(name)
    kind = declared.upper()
    types = {
        "INT64": pa.int64(),
        "INTEGER": pa.int64(),
        "FLOAT64": pa.float64(),
        "BOOL": pa.bool_(),
        "BOOLEAN": pa.bool_(),
        "STRING": pa.string(),
        "BYTES": pa.binary(),
        "DATE": pa.date32(),
        "TIME": pa.time64("us"),
        "DATETIME": pa.timestamp("us"),
        "TIMESTAMP": pa.timestamp("us", "UTC"),
        "NUMERIC": pa.decimal128(38, 9),
        "BIGNUMERIC": pa.decimal256(76, 38),
    }
    t = types.get(kind)
    if match := re.fullmatch(r"(NUMERIC|BIGNUMERIC)\((\d+),\s*(\d+)\)", kind):
        base, p, s = match.groups()
        precision, scale = _decimal_parameters(name, base, int(p), int(s))
        t = (pa.decimal128 if base == "NUMERIC" else pa.decimal256)(precision, scale)
    if match := re.fullmatch(r"(STRING|BYTES)\((\d+)\)", kind):
        _integer(int(match[2]), f"column {name!r} max_length", 1)
        t = pa.string() if match[1] == "STRING" else pa.binary()
    if t is None:
        # BigQuery native nested fields are outside this adapter's write scope; read as JSON.
        if kind == "JSON" or kind.startswith(("ARRAY<", "STRUCT<")):
            t = pa.string()
        else:
            raise ShapeError(f"column {name!r}: unsupported BigQuery catalog type")
    return pa.field(name, t, nullable=nullable, metadata={b"shape.sql_type": declared.encode()})


def _decimal_parameters(name: str, kind: str, precision: Any, scale: Any) -> tuple[int, int]:
    p = _integer(precision, f"column {name!r} precision", 1)
    s = _integer(scale, f"column {name!r} scale", 0)
    if (
        s > p
        or (kind == "NUMERIC" and (p - s > 29 or s > 9))
        or (kind == "BIGNUMERIC" and (p - s > 38 or s > 38))
    ):
        raise ShapeError(f"column {name!r}: invalid {kind} precision/scale constraints")
    return p, s


def _catalog_declaration(name: str, declared: str, field: Any) -> str:
    """Retain explicit REST TableFieldSchema constraints without guessing DATA_TYPE output."""
    kind = declared.upper().strip()
    if field is None:
        return kind
    base = kind.partition("(")[0]
    aliases = {
        "INTEGER": "INT64",
        "FLOAT": "FLOAT64",
        "BOOLEAN": "BOOL",
        "DECIMAL": "NUMERIC",
        "BIGDECIMAL": "BIGNUMERIC",
    }
    supplied = str(getattr(field, "field_type", base)).upper()
    if aliases.get(supplied, supplied) != aliases.get(base, base):
        raise ShapeError(f"column {name!r}: inconsistent catalog type metadata")
    precision = getattr(field, "precision", None)
    scale = getattr(field, "scale", None)
    length = getattr(field, "max_length", None)
    if precision is not None or scale is not None:
        if base not in ("NUMERIC", "BIGNUMERIC") or precision is None:
            raise ShapeError(f"column {name!r}: precision is required with decimal scale")
        # REST explicitly defines omitted scale as zero when precision is supplied.
        p, s = _decimal_parameters(name, base, precision, 0 if scale is None else scale)
        if "(" in kind:
            match = re.fullmatch(r"(?:NUMERIC|BIGNUMERIC)\((\d+)(?:,\s*(\d+))?\)", kind)
            if not match or (int(match[1]), int(match[2] or 0)) != (p, s):
                raise ShapeError(f"column {name!r}: conflicting decimal catalog constraints")
        kind = f"{base}({p},{s})"
    if length is not None:
        n = _integer(length, f"column {name!r} max_length", 1)
        if base not in ("STRING", "BYTES"):
            raise ShapeError(f"column {name!r}: length constraints require STRING or BYTES")
        if "(" in kind and kind != f"{base}({n})":
            raise ShapeError(f"column {name!r}: conflicting length catalog constraints")
        kind = f"{base}({n})"
    return kind


class _Session:
    def __init__(self, client: Any, sdk: Any, storage: Any) -> None:
        self.client, self.sdk, self.storage = client, sdk, storage
        self._reader: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def query_config(self, **options: Any) -> Any:
        params = options.pop("parameters", {})
        return self.sdk.QueryJobConfig(
            query_parameters=[
                self.sdk.ScalarQueryParameter(k, "STRING", v) for k, v in params.items()
            ],
            **options,
        )

    def load_config(self, **options: Any) -> Any:
        return self.sdk.LoadJobConfig(source_format=self.sdk.SourceFormat.PARQUET, **options)

    def read_client(self) -> Any:
        if self._reader is None:
            self._reader = self.storage.BigQueryReadClient(credentials=self.client._credentials)
        return self._reader

    def close(self) -> None:
        try:
            if self._reader is not None:
                self._reader.close()
        finally:
            self.client.close()


def run_query(
    conn: Any, plan: Plan, sql: str, *, table: str, parameters: Mapping[str, Any] | None = None
) -> Any:
    """Guard every query, including catalog and DDL, before executing it."""
    cap = plan.options["max_bytes_billed"]
    config = dict(maximum_bytes_billed=cap, use_query_cache=False, parameters=parameters or {})
    dry = conn.query(sql, job_config=conn.query_config(dry_run=True, **config))
    estimate = int(dry.total_bytes_processed or 0)
    if estimate > cap:
        raise ShapeError(
            f"table {table!r}: estimate {estimate} bytes exceeds max_bytes_billed={cap}"
        )
    job = conn.query(sql, job_config=conn.query_config(dry_run=False, **config))
    result = job.result()
    plan.auth["bytes_processed"] = plan.auth.get("bytes_processed", 0) + int(
        job.total_bytes_processed or 0
    )
    return result


class BigQuerySink(DatabaseSink):
    """Write generated Arrow batches using bounded Parquet load jobs."""

    name = "bigquery"
    schemes = ("bigquery",)
    dialect = "mysql"  # shared name validation; BigQuery-specific checks follow it
    uri_params = {"location": as_text, "max_bytes_billed": as_int}

    def parse_target(self, uri: str) -> Target:
        target = parse_uri(uri, self.schemes, self.uri_params)
        if urlsplit(uri).fragment:
            raise ShapeError("BigQuery URI fragments are refused")
        if target.user or target.port or not target.host or not target.database:
            raise ShapeError("use bigquery://<project>/<dataset>?location=US")
        identifier(target.host, "project")
        identifier(target.database, "dataset")
        location = target.params.get("location", "US")
        if not re.fullmatch(r"[A-Za-z0-9-]+", location):
            raise ShapeError("invalid BigQuery location")
        return target

    def resolve_auth(self, options: Mapping[str, Any]) -> tuple[Secret | None, dict[str, Any]]:
        ref = options.get("credentials")
        if ref is None and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            ref = "file://" + os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        if ref is None:
            return None, {}
        if not isinstance(ref, str) or not ref.startswith(("env://", "file://")):
            raise CredentialError("credentials must be a file:// or env:// reference")
        value = _resolve_reference(ref)
        try:
            info = json.loads(value or "")
        except (ValueError, TypeError):
            raise CredentialError(
                "credentials reference must contain service-account JSON"
            ) from None
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise CredentialError("credentials reference must contain service-account JSON")
        secrets = {"credentials_json": Secret(value or "")}
        secrets.update(
            {"credential_" + str(k): Secret(v) for k, v in info.items() if isinstance(v, str) and v}
        )
        return None, secrets

    def plan(self, uri: str, table: str, options: Mapping[str, Any]) -> Plan:
        opts = dict(options)
        if opts.get("write_mode") == "fail":
            opts["write_mode"] = "create"
        opts["chunk_rows"] = _integer(opts.get("chunk_rows", 1_000_000), "chunk_rows", 1)
        opts["max_bytes_billed"] = _integer(
            opts.get("max_bytes_billed", DEFAULT_MAX_BYTES), "max_bytes_billed", 1
        )
        if not isinstance(opts.get("truncate_ns", False), bool):
            raise ShapeError("truncate_ns must be true or false")
        plan = super().plan(uri, table, opts)
        if "max_bytes_billed" not in options and "max_bytes_billed" in plan.target.params:
            opts["max_bytes_billed"] = _integer(
                plan.target.params["max_bytes_billed"], "max_bytes_billed", 1
            )
        identifier(plan.table, "table")
        if plan.schema_name:
            identifier(plan.schema_name, "dataset")
        for name in plan.primary_key:
            identifier(name)
        return plan

    def check_schema(self, schema: pa.Schema, plan: Plan) -> None:
        _sql.check_unique(schema.names, "mysql")
        for field in schema:
            identifier(field.name)
            column_type(
                field, plan.columns.get(field.name, {}), plan.options.get("truncate_ns", False)
            )
        if set(plan.primary_key) - set(schema.names):
            raise ShapeError("primary_key must name columns in the schema")
        if set(plan.columns) - set(schema.names):
            raise ShapeError("columns must name columns in the schema")

    def connect_params(self, plan: Plan) -> dict[str, Any]:
        params: dict[str, Any] = {
            "project": plan.target.host,
            "location": plan.target.params.get("location", "US"),
        }
        if "credentials_json" in plan.auth:
            params["credentials_json"] = plan.auth["credentials_json"].reveal()
        return params

    def default_connect(self, **params: Any) -> Any:
        try:
            sdk = importlib.import_module("google.cloud.bigquery")
            storage = importlib.import_module("google.cloud.bigquery_storage")
            if text := params.pop("credentials_json", None):
                account = importlib.import_module("google.oauth2.service_account")
                params["credentials"] = account.Credentials.from_service_account_info(
                    json.loads(text)
                )
            return _Session(sdk.Client(**params), sdk, storage)
        except ImportError:
            raise ShapeError(f"BigQuery needs its optional SDKs: {INSTALL}") from None

    def table_id(self, plan: Plan, table: str | None = None) -> str:
        return ".".join(
            [
                str(plan.target.host),
                str(plan.schema_name or plan.target.database),
                identifier(table or plan.table, "table"),
            ]
        )

    def create_table_sql(self, plan: Plan, schema: pa.Schema, first: pa.RecordBatch | None) -> str:
        columns = [
            quote(f.name)
            + " "
            + column_type(f, plan.columns.get(f.name, {}), plan.options.get("truncate_ns", False))
            + (" NOT NULL" if not f.nullable else "")
            for f in schema
        ]
        if plan.primary_key:
            columns.append(
                "PRIMARY KEY (" + ", ".join(quote(k) for k in plan.primary_key) + ") NOT ENFORCED"
            )
        return f"CREATE TABLE `{self.table_id(plan)}` (" + ", ".join(columns) + ")"

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        plan = self.plan(uri, table, options)
        first, rest = self._peek(batches, plan)
        schema = first.schema if first is not None else plan.schema
        if schema is None:
            raise ShapeError("empty input requires schema")
        self.check_schema(schema, plan)
        conn = None
        created = False
        progress = [0]
        secrets = [plan.secret, *plan.secrets]
        failure: ShapeError | None = None
        injected = options.get("connection")
        try:
            conn = (
                injected
                if injected is not None
                else self._open(plan, plan.target.label, secrets, options.get("connect"))
            )
            table_id = self.table_id(plan)
            try:
                conn.get_table(table_id)
                exists = True
            except Exception as exc:
                if type(exc).__name__ != "NotFound":
                    raise
                exists = False
            if exists and plan.mode == "create":
                raise ShapeError(f"table {table!r} already exists")
            if exists and plan.mode == "replace":
                conn.delete_table(table_id)
                exists = False
            if not exists:
                run_query(conn, plan, self.create_table_sql(plan, schema, first), table=table)
                created = True
            total = self.load(conn, table_id, plan, first, rest, progress)
            return total
        except Exception as exc:
            kept = progress[0]
            if conn is not None and created and (not plan.commit_rows or not progress[0]):
                try:
                    conn.delete_table(self.table_id(plan), not_found_ok=True)
                    kept = 0
                except Exception:  # noqa: S110  # nosec B110
                    pass
            failure = WriteError(
                f"BigQuery table {table!r} write failed: {scrub(str(exc), secrets)}", kept
            )
        finally:
            if conn is not None and injected is None:
                try:
                    conn.close()
                except Exception:  # noqa: S110  # nosec B110
                    pass
        assert failure is not None
        raise failure

    def load(
        self,
        conn: Any,
        qualified: str,
        plan: Plan,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        progress: list[int],
    ) -> int:
        if first is None:
            if plan.mode != "truncate":
                return 0
            assert plan.schema is not None
            first = pa.RecordBatch.from_pydict(
                {field.name: [] for field in plan.schema}, schema=plan.schema
            )
        size = min(plan.options["chunk_rows"], plan.commit_rows or plan.options["chunk_rows"])
        pending: list[pa.RecordBatch] = []
        count = 0
        total = 0
        disposition = "WRITE_TRUNCATE" if plan.mode == "truncate" else "WRITE_APPEND"
        from shape.scale.jobs import restrict_to_current_user

        def protect_staging(path: Path) -> None:
            if sys.platform == "win32":
                # CPython 3.11.10+ mkdir(0700) explicitly grants SYSTEM, Administrators,
                # and OWNER RIGHTS. Remove only these fresh-directory bootstrap grants;
                # the final existing helper regrants this identity even when it is SYSTEM.
                subprocess.run(
                    ["icacls", str(path), "/remove:g", "*S-1-5-18", "*S-1-5-32-544", "*S-1-3-4"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
            restrict_to_current_user(path)

        with tempfile.TemporaryDirectory(prefix="shape-bigquery-") as folder:
            protect_staging(Path(folder))

            def flush() -> int:
                nonlocal disposition
                staged = pa.Table.from_batches(pending)
                arrays = []
                for field, array in zip(staged.schema, staged.columns, strict=True):
                    t = field.type
                    if (field.metadata or {}).get(b"shape.type") == b"uuid" or isinstance(
                        t, pa.UuidType
                    ):
                        array = pa.array(
                            [
                                None
                                if v is None
                                else str(uuid.UUID(bytes=v))
                                if isinstance(v, bytes)
                                else str(v)
                                for v in array.to_pylist()
                            ],
                            type=pa.string(),
                        )
                    elif _cloud.is_nested(t):
                        array = pa.array(
                            [
                                None
                                if v is None
                                else json.dumps(v, default=str, separators=(",", ":"))
                                for v in array.to_pylist()
                            ],
                            type=pa.string(),
                        )
                    elif pa.types.is_uint64(t):
                        array = pc.cast(array, pa.decimal128(20, 0))
                    elif (pa.types.is_timestamp(t) or pa.types.is_time(t)) and t.unit == "ns":
                        target = (
                            pa.timestamp("us", t.tz)
                            if pa.types.is_timestamp(t)
                            else pa.time64("us")
                        )
                        array = pc.cast(array, target, safe=False)
                    arrays.append(array)
                staged = pa.Table.from_arrays(arrays, names=staged.schema.names)
                path = Path(folder) / "chunk.parquet"
                # POSIX modes and the existing Windows ACL helper both fail closed.
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.close(fd)
                protect_staging(path)
                pq.write_table(staged, path)
                with path.open("rb") as stream:
                    job = conn.load_table_from_file(
                        stream,
                        qualified,
                        job_config=conn.load_config(
                            write_disposition=disposition,
                            decimal_target_types=["NUMERIC", "BIGNUMERIC"],
                        ),
                    )
                    job.result()
                if job.output_rows != staged.num_rows:
                    raise RuntimeError(
                        f"output_rows={job.output_rows} differs from staged rows={staged.num_rows}"
                    )
                disposition = "WRITE_APPEND"
                progress[0] += staged.num_rows
                return int(staged.num_rows)

            for batch in chain_batches(first, rest):
                _sql.check_batch(first.schema, batch, plan.table)
                offset = 0
                while offset < batch.num_rows:
                    piece = batch.slice(offset, min(size - count, batch.num_rows - offset))
                    pending.append(piece)
                    offset += piece.num_rows
                    count += piece.num_rows
                    if count == size:
                        total += flush()
                        pending, count = [], 0
            if pending or (not total and plan.mode == "truncate"):
                if not pending:
                    pending = [first.slice(0, 0)]
                total += flush()
        return total


class _Catalog:
    """A cursor-shaped holder reusing DatabaseSource.profile_database unchanged."""

    def __init__(self, client: Any, plan: Plan) -> None:
        self.client, self.plan = client, plan
        self.table_metadata: dict[str, Any] = {}

    def cursor(self) -> _Catalog:
        return self

    def close(self) -> None:
        pass


class BigQuerySource(DatabaseSource):
    """Storage Read Arrow transport using the shared W9-01 multi-table profile model."""

    name = "bigquery"
    schemes = ("bigquery",)
    sink_class = BigQuerySink

    def _plan(self, uri: str, options: Mapping[str, Any], require_table: bool) -> Plan:
        allowed = {
            "credentials",
            "connection",
            "connect",
            "batch_size",
            "sample_rows",
            "tables",
            "name",
            "max_bytes_billed",
            "max_depth",
        }
        if set(options) - allowed:
            raise ShapeError("unknown source option: " + ", ".join(sorted(set(options) - allowed)))
        target = parse_uri(
            uri,
            self.sink.schemes,
            {"location": as_text, "table": as_text, "max_bytes_billed": as_int},
        )
        if urlsplit(uri).fragment:
            raise ShapeError("BigQuery URI fragments are refused")
        if target.user or target.port:
            raise ShapeError("BigQuery URIs cannot contain a user or port")
        table = target.params.pop("table", None)
        if require_table and not table:
            raise ShapeError("a table source needs the URI parameter table=T")
        # Parse the original URI without the table through the common sink validator.
        clean_uri = f"bigquery://{target.host}/{target.database}?" + urlencode(target.params)
        plan = cast(Plan, self.sink.plan(clean_uri, table or "catalog", options))
        plan.table = table or ""
        plan.batch_size = _integer(options.get("batch_size", 65536), "batch_size", 1)
        _integer(options.get("sample_rows", 1000), "sample_rows", 0)
        depth = _integer(options.get("max_depth", 8), "max_depth", 0)
        if depth > 64:
            raise ShapeError("max_depth must be at most 64")
        selected = options.get("tables")
        if selected is not None:
            if not isinstance(selected, list) or not selected:
                raise ShapeError("tables must be a non-empty list of table names")
            for value in selected:
                identifier(value, "table")
        return plan

    @contextmanager
    def _connection(self, plan: Plan) -> Iterator[Any]:
        with super()._connection(plan) as client:
            yield _Catalog(client, plan)

    def _schema_name(self, cur: Any, plan: Plan) -> str:
        return str(plan.target.database)

    def _query(
        self, cur: Any, sql: str, table: str, parameters: Mapping[str, Any] | None = None
    ) -> Any:
        return run_query(cur.client, cur.plan, sql, table=table, parameters=parameters)

    def _info(self, cur: Any, view: str) -> str:
        return f"`{cur.plan.target.host}.{cur.plan.target.database}.INFORMATION_SCHEMA.{view}`"

    def _table_metadata(self, cur: Any, table: str) -> Any:
        if table not in cur.table_metadata:
            cur.table_metadata[table] = cur.client.get_table(self.sink.table_id(cur.plan, table))
        return cur.table_metadata[table]

    def _tables(self, cur: Any, schema: str) -> list[tuple[str, int]]:
        rows = self._query(
            cur,
            "SELECT table_name FROM "
            + self._info(cur, "TABLES")
            + " WHERE table_type='BASE TABLE' ORDER BY table_name",
            schema,
        )
        return [
            (identifier(str(r[0]), "table"), int(self._table_metadata(cur, str(r[0])).num_rows))
            for r in rows
        ]

    def _columns(self, cur: Any, schema: str, table: str) -> pa.Schema:
        rows = self._query(
            cur,
            "SELECT column_name, data_type, is_nullable FROM "
            + self._info(cur, "COLUMNS")
            + " WHERE table_name=@table ORDER BY ordinal_position",
            table,
            {"table": table},
        )
        schema_fields = {
            field.name: field for field in getattr(self._table_metadata(cur, table), "schema", ())
        }
        fields = [
            arrow_field(
                str(r[0]),
                _catalog_declaration(str(r[0]), str(r[1]), schema_fields.get(str(r[0]))),
                str(r[2]) == "YES",
            )
            for r in rows
        ]
        if not fields:
            raise ShapeError(f"table {table!r} is missing or has no readable columns")
        return pa.schema(fields)

    def _keys(self, cur: Any, schema: str, table: str) -> tuple[list[str], list[Any]]:
        sql = (
            "SELECT k.column_name FROM "
            + self._info(cur, "TABLE_CONSTRAINTS")
            + " t JOIN "
            + self._info(cur, "KEY_COLUMN_USAGE")
            + " k USING (constraint_catalog, constraint_schema, constraint_name, table_name) "
            "WHERE t.table_name=@table AND t.constraint_type='PRIMARY KEY' "
            "ORDER BY k.ordinal_position"
        )
        pk = [str(r[0]) for r in self._query(cur, sql, table, {"table": table})]
        # BigQuery exposes referenced columns through CONSTRAINT_COLUMN_USAGE; its
        # KEY_COLUMN_USAGE position_in_unique_constraint preserves composite ordering.
        sql = (
            "SELECT f.constraint_name, p.table_name, p.column_name, f.column_name FROM "
            + self._info(cur, "KEY_COLUMN_USAGE")
            + " f JOIN "
            + self._info(cur, "TABLE_CONSTRAINTS")
            + " t USING (constraint_catalog, constraint_schema, constraint_name, table_name) "
            "JOIN (SELECT DISTINCT constraint_catalog, constraint_schema, constraint_name, "
            "table_catalog, table_schema, table_name FROM "
            + self._info(cur, "CONSTRAINT_COLUMN_USAGE")
            + ") r USING (constraint_catalog, constraint_schema, constraint_name) JOIN "
            + self._info(cur, "TABLE_CONSTRAINTS")
            + " pk ON pk.table_catalog=r.table_catalog AND pk.table_schema=r.table_schema "
            "AND pk.table_name=r.table_name AND pk.constraint_type='PRIMARY KEY' JOIN "
            + self._info(cur, "KEY_COLUMN_USAGE")
            + " p ON p.constraint_catalog=pk.constraint_catalog "
            "AND p.constraint_schema=pk.constraint_schema "
            "AND p.constraint_name=pk.constraint_name "
            "AND p.ordinal_position=f.position_in_unique_constraint "
            "WHERE f.table_name=@table AND t.constraint_type='FOREIGN KEY' "
            "ORDER BY f.constraint_name, f.ordinal_position"
        )
        return pk, list(self._query(cur, sql, table, {"table": table}))

    def _arrow(self, cur: Any, sql: str, table: str, fields: pa.Schema) -> Iterator[pa.RecordBatch]:
        result = self._query(cur, sql, table)
        for piece in result.to_arrow_iterable(
            bqstorage_client=cur.client.read_client(), max_queue_size=1, max_stream_count=1
        ):
            table_data = (
                pa.Table.from_batches([piece]) if isinstance(piece, pa.RecordBatch) else piece
            )
            # Arrow transport preserves the catalog nullability and declared-type metadata.
            table_data = table_data.cast(fields)
            yield from table_data.to_batches(max_chunksize=cur.plan.batch_size)

    def _sample(
        self,
        cur: Any,
        plan: Plan,
        schema: str,
        table: str,
        fields: pa.Schema,
        n: int,
        catalog_rows: int,
        pk: list[str],
    ) -> tuple[pa.Table, str]:
        if not n:
            return pa.Table.from_batches([], schema=fields), "catalog_only"
        sql = (
            "SELECT "
            + ", ".join(quote(f.name) for f in fields)
            + f" FROM `{self.sink.table_id(plan, table)}`"
        )
        method = "whole_table"
        if catalog_rows > n:
            percentage = min(100.0, n * 100.0 / catalog_rows)
            sql += f" TABLESAMPLE SYSTEM ({percentage:.12g} PERCENT)"
            method = "tablesample_system"
        # n is strictly validated as an integer, never raw URI input.
        sql += f" LIMIT {n}"
        return pa.Table.from_batches(
            list(self._arrow(cur, sql, table, fields)), schema=fields
        ), method

    def _provenance(self, cur: Any, plan: Plan) -> dict[str, Any]:
        return {"source": "bigquery", "bytes_processed": plan.auth.get("bytes_processed", 0)}

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        plan = self._plan(uri, options, True)
        with self._connection(plan) as cur:
            fields = self._columns(cur, str(plan.target.database), plan.table)
            sql = (
                "SELECT "
                + ", ".join(quote(f.name) for f in fields)
                + f" FROM `{self.sink.table_id(plan)}`"
            )
            yield from self._arrow(cur, sql, plan.table, fields)

    def profile_database(self, uri: str, **options: Any) -> Any:
        result = super().profile_database(uri, **options)
        from shape.profile.reference import Profile

        data = result.to_dict()
        for table in data["tables"].values():
            table["primary_key_evidence"] = "declared" if table.get("primary_key") else None
        data["sampling"]["note"] = (
            "Row counts come from table metadata; distributions describe sampled rows."
        )
        return Profile(
            data, name=result.name, provenance=result.provenance, sketches=result.sketches
        )
