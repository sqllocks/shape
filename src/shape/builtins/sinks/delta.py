"""Delta sink (``[delta]`` extra, deltalake): ``<uri>/<table>`` as a Delta table.

``uri`` is the directory that holds the tables: a local path, or ``delta+abfss://<container>@
<host>/<folder>`` for OneLake (``.../<lakehouse>.Lakehouse/Tables``) and ADLS Gen2, with the
credential options of the ``abfss`` source (:mod:`shape.builtins.sources._azure_auth`; a bearer
token, an account key or a SAS token; delta-rs takes no connection string).

Options: ``mode`` (``overwrite`` by default, or ``append``), ``partition_by`` (column names),
``commit_rows`` / ``commit_seconds`` (micro-batch mode: a Delta commit every N rows or seconds
instead of one at the end, so Fabric and Spark readers see rows while a stream runs; the first
commit applies ``mode``, later ones append), ``storage_options``, ``fingerprint`` (``True`` or the
run's context; a local table in mode ``overwrite``: after the write one more commit sets the table
property ``shape.fingerprint``, see ``docs/FINGERPRINT.md``).

Tables are written at Delta reader version 1 / writer version 2, with no table features and no
table configuration, so that every engine can read them (Python notebooks, pipelines, Spark,
the SQL endpoint). A timestamp without a time zone would make the writer ask for the
``timestampNtz`` feature (reader 3 / writer 7), so such columns are written as UTC timestamps
(the wall-clock value is kept, read as UTC).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.sources.files import output_path
from shape.plugins.schemes import require_scheme

CLOUD_PREFIX = "delta+"


def _deltalake() -> Any:
    try:
        import deltalake
    except ImportError as exc:
        raise ImportError(
            "writing Delta needs deltalake: pip install 'sqllocks-shape[delta]'"
        ) from exc
    return deltalake


def _fabric_token(credential: Any) -> Callable[[], str] | None:
    """A callable giving a Fabric-API bearer token for ``credential`` (or, in a Fabric notebook
    with none given, the notebook identity); ``None`` when there is nothing to ask with."""
    from shape.builtins.sources._azure_auth import NotebookUtilsCredential, _import_notebookutils
    from shape.integrations.fabric.lakehouse_kind import FABRIC_SCOPE

    if credential is None:
        utils = _import_notebookutils()
        has_identity = getattr(utils, "credentials", None) is not None  # a real notebook has it
        credential = NotebookUtilsCredential(utils) if has_identity else None
    if isinstance(credential, NotebookUtilsCredential):  # its storage token is the wrong audience
        return lambda: str(credential._utils.credentials.getToken("pbi"))
    if credential is None or not hasattr(credential, "get_token"):
        return None
    return lambda: str(credential.get_token(FABRIC_SCOPE).token)


def _raw_storage_token(options: dict[str, Any]) -> bool:
    """True when the caller gave a storage token (``token`` or one in ``storage_options``)."""
    from shape.builtins.sources.delta import _TOKEN_KEYS

    given = {str(k).lower() for k in dict(options.get("storage_options") or {})}
    return bool(options.get("token")) or bool(_TOKEN_KEYS & given)


def _lakehouse_tables(base: str, options: dict[str, Any]) -> str:
    """``base``, with the schema folder added for a schema-enabled OneLake Lakehouse.

    A OneLake ``.../Tables`` root is a classic Lakehouse's table folder, or a schema-enabled one's
    parent of its schema folders. ``schema_enabled`` says which; unset, Fabric is asked (see
    :mod:`shape.integrations.fabric.lakehouse_kind`) and an unknown kind is an error rather than a
    guess. ``schema_name`` (default ``dbo``) picks the schema folder.
    """
    from shape.builtins.sinks.azure import is_onelake
    from shape.builtins.sources.azure import parse
    from shape.integrations.fabric.lakehouse_kind import resolve_layout

    loc = parse(base)
    root = loc.path.rstrip("/")
    explicit = options.get("schema_enabled")
    name = options.get("schema_name") or None
    onelake_tables = (
        bool(loc.host) and is_onelake(loc.host) and (root == "Tables" or root.endswith("/Tables"))
    )
    if not onelake_tables:
        if explicit not in (None, "", False, "false", "False", "0", 0) or name:
            raise ValueError(
                "schema_enabled and schema_name need a OneLake Lakehouse target ending in /Tables"
            )
        return base
    lakehouse = root.removesuffix("Tables").rstrip("/")
    if not lakehouse or "/" in lakehouse:
        raise ValueError("a Lakehouse target is abfss://<workspace>@<host>/<lakehouse>/Tables")
    token = _fabric_token(options.get("credential"))
    if explicit in (None, "") and not name and token is None and _raw_storage_token(options):
        # The caller handed delta-rs its own storage token and there is no identity to ask Fabric
        # with: the location is used as written (a schema-enabled Lakehouse then needs the schema
        # in it, ``.../Tables/dbo``, or ``schema_enabled=true``). Nothing is guessed or looked up.
        return base
    layout = resolve_layout(
        loc.container,
        lakehouse,
        schema_enabled=explicit,
        schema_name=name,
        token=token,
    )
    if layout.schema_enabled:
        return base.rstrip("/") + "/" + str(layout.schema)
    return base


def _location(uri: str, table: str, options: dict[str, Any]) -> tuple[str, dict[str, str] | None]:
    """The table's location and delta-rs storage options."""
    from shape.security.names import safe_name

    prefix = options.get("table_prefix") or ""
    if not isinstance(prefix, str):
        raise ValueError("table_prefix must be a string")
    table = safe_name(prefix + table)
    if uri.startswith(CLOUD_PREFIX):
        from shape.builtins.sinks.azure import _with_environment_keys
        from shape.builtins.sources.azure import parse
        from shape.builtins.sources.delta import _storage_options

        base = uri[len(CLOUD_PREFIX) :]
        parse(base)  # validates the URI
        base = _lakehouse_tables(base, options)
        # a key in AZURE_STORAGE_* is used when no credential is given, as for abfss://
        return f"{base.rstrip('/')}/{safe_name(table)}", _storage_options(
            base, _with_environment_keys(options)
        )
    from shape.security.names import contained

    target = contained(output_path(uri), table)
    target.mkdir(parents=True, exist_ok=True)
    extra = {str(k): str(v) for k, v in dict(options.get("storage_options") or {}).items()}
    return str(target), extra or None


class DeltaTableWriter:
    """Batches in, Delta commits out: ``flush`` and the thresholds each make one commit."""

    def __init__(
        self,
        location: str,
        schema: pa.Schema,
        options: dict[str, Any],
        storage: dict[str, str] | None,
    ) -> None:
        self._write = _deltalake().write_deltalake
        self._location = location
        self._schema = _utc_timestamps(schema)
        self._storage = storage
        self._partition_by = options.get("partition_by") or None
        self._mode = options.get("mode", "overwrite")
        self.commit_rows = int(options["commit_rows"]) if options.get("commit_rows") else None
        self.commit_seconds = (
            float(options["commit_seconds"]) if options.get("commit_seconds") else None
        )
        self._clock: Callable[[], float] = options.get("clock") or time.monotonic
        self._pending: list[pa.RecordBatch] = []
        self._pending_rows = 0
        self._since = self._clock()
        self.rows = 0
        self.commits = 0

    def write_batch(self, batch: pa.RecordBatch) -> None:
        if batch.num_rows:
            self._pending.append(batch)
            self._pending_rows += batch.num_rows
        if self.commit_rows is None and self.commit_seconds is None:
            return
        if (self.commit_rows is not None and self._pending_rows >= self.commit_rows) or (
            self.commit_seconds is not None and self._clock() - self._since >= self.commit_seconds
        ):
            self.flush()

    def write_all(self, batches: Iterable[pa.RecordBatch]) -> None:
        for batch in batches:
            self.write_batch(batch)

    def flush(self) -> None:
        """Commit what is pending (nothing when it is empty)."""
        self._since = self._clock()
        if not self._pending:
            return
        self._commit(self._pending)

    def _commit(self, batches: list[pa.RecordBatch]) -> None:
        reader = pa.RecordBatchReader.from_batches(
            self._schema, iter([b.cast(self._schema) for b in batches])
        )
        self._write(
            self._location,
            reader,
            mode=self._mode,
            partition_by=self._partition_by,
            storage_options=self._storage,
            **_schema_mode(self._mode),
        )
        self.rows += self._pending_rows
        self.commits += 1
        self._pending, self._pending_rows = [], 0
        self._mode = "append"  # only the first commit replaces

    def close(self, *, schema: pa.Schema | None = None) -> int:
        if self._pending or self.commits == 0:
            self._commit(self._pending)
        return self.rows

    def abort(self) -> None:
        self._pending, self._pending_rows = [], 0


def _schema_mode(mode: str) -> dict[str, str]:
    """Overwriting a table replaces its schema too (a regenerated model may have other columns);
    appending keeps the table's schema and refuses data that does not fit."""
    return {"schema_mode": "overwrite"} if mode == "overwrite" else {}


def _utc_timestamps(schema: pa.Schema) -> pa.Schema:
    """``schema`` with every time-zone-less timestamp (at any nesting depth) made a UTC one."""
    return pa.schema([f.with_type(_utc_type(f.type)) for f in schema], metadata=schema.metadata)


def _utc_type(t: pa.DataType) -> pa.DataType:
    if pa.types.is_timestamp(t) and t.tz is None:
        return pa.timestamp(t.unit, tz="UTC")
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return type(t)(t.value_field.with_type(_utc_type(t.value_type)))
    if pa.types.is_struct(t):
        return pa.struct(
            [t.field(i).with_type(_utc_type(t.field(i).type)) for i in range(t.num_fields)]
        )
    return t


class DeltaSink:
    """Write Arrow batches to a Delta table directory."""

    name = "delta"
    schemes = ("file", "delta+abfss", "delta+abfs")
    extension = ""  # no file extension: a directory per table

    def open_table(
        self, uri: str, table: str, schema: pa.Schema | None = None, **options: Any
    ) -> DeltaTableWriter:
        """A streaming writer for ``table``; the schema comes from ``schema``."""
        require_scheme(self, uri)
        if schema is None:
            schema = options.get("schema")
        if schema is None:
            raise ValueError("a Delta table needs a schema: pass `schema`")
        location, storage = _location(uri, table, options)
        return DeltaTableWriter(location, schema, options, storage)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        require_scheme(self, uri)
        stream = iter(batches)
        first = next(stream, None)
        schema = first.schema if first is not None else options.get("schema")
        if schema is None:
            raise ValueError(
                "a Delta table needs a schema: pass `schema` when there are no batches"
            )
        schema = _utc_timestamps(schema)
        if options.get("fingerprint") and (
            options.get("commit_rows") or options.get("commit_seconds")
        ):
            raise ValueError("fingerprint does not combine with micro-batch commits")
        if options.get("commit_rows") or options.get("commit_seconds"):
            rest = {k: v for k, v in options.items() if k != "schema"}
            writer = self.open_table(uri, table, schema, **rest)

            def chained() -> Iterator[pa.RecordBatch]:
                if first is not None:
                    yield first
                yield from stream

            try:
                writer.write_all(chained())
            except BaseException:
                writer.abort()
                raise
            return writer.close()
        stamp = options.get("fingerprint")
        if stamp:
            if options.get("mode", "overwrite") != "overwrite":
                raise ValueError(
                    "fingerprint needs mode overwrite: the table_id covers the whole table"
                )
            if uri.startswith(CLOUD_PREFIX):
                raise ValueError("fingerprint writes a local Delta table only")
        location, storage = _location(uri, table, options)
        write_deltalake = _deltalake().write_deltalake
        rows = 0
        kept: list[pa.RecordBatch] = []

        def counted() -> Iterator[pa.RecordBatch]:
            nonlocal rows
            if first is not None:
                rows += first.num_rows
                cast = first.cast(schema)
                if stamp:
                    kept.append(cast)
                yield cast
            for batch in stream:
                rows += batch.num_rows
                cast = batch.cast(schema)
                if stamp:
                    kept.append(cast)
                yield cast

        mode = options.get("mode", "overwrite")
        write_deltalake(
            location,
            pa.RecordBatchReader.from_batches(schema, counted()),
            mode=mode,
            partition_by=options.get("partition_by") or None,
            storage_options=storage,
            **_schema_mode(mode),
        )
        if stamp:
            from shape.fingerprint import KEY, dump, for_sink, set_delta_property

            whole = pa.Table.from_batches(kept, schema=schema)
            set_delta_property(location, KEY, dump(for_sink(table, whole, stamp)))
        return rows
