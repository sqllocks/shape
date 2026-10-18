"""Native Iceberg writes with explicit schema checks and one snapshot per micro-batch."""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins._iceberg import (
    SCHEMA_KEY,
    catalog,
    convert_batch,
    data_files,
    decode_schema,
    dependency,
    encode_schema,
    iceberg_schema,
    mapped_schema,
    parse,
    partition_spec,
)
from shape.plugins.schemes import require_scheme


class IcebergTableWriter:
    def __init__(self, uri: str, schema: pa.Schema, options: dict[str, Any]) -> None:
        parse(uri, writing=True)
        mode = options.get("mode", "overwrite")
        if mode not in ("overwrite", "append"):
            raise ValueError("Iceberg mode must be overwrite or append")
        version = options.get("format_version", 2)
        if isinstance(version, bool) or not isinstance(version, int) or version not in (1, 2):
            raise ValueError("Iceberg format_version must be 1 or 2")
        self._schema = schema
        truncate_ns = options.get("truncate_ns", False)
        if not isinstance(truncate_ns, bool):
            raise ValueError("truncate_ns must be a boolean")
        self._mapped = mapped_schema(schema, truncate_ns)
        dependency()
        spec = partition_spec(self._mapped, options.get("partition_by"))
        self.commit_rows = options.get("commit_rows")
        self.commit_seconds = options.get("commit_seconds")
        if self.commit_rows is not None and (
            isinstance(self.commit_rows, bool)
            or not isinstance(self.commit_rows, int)
            or self.commit_rows <= 0
        ):
            raise ValueError("commit_rows must be a positive integer")
        if self.commit_seconds is not None and (
            isinstance(self.commit_seconds, bool)
            or not isinstance(self.commit_seconds, (int, float))
            or not math.isfinite(self.commit_seconds)
            or self.commit_seconds <= 0
        ):
            raise ValueError("commit_seconds must be positive")
        self._clock = options.get("clock", time.monotonic)
        self._since = self._clock()
        self._pending: list[pa.RecordBatch] = []
        self._pending_rows = 0
        self.rows = self.commits = 0
        self._mode = mode
        self._stamp = options.get("fingerprint")
        self._name = parse(uri, writing=True)[1][-1]
        cat, identifier = catalog(uri, options, writing=True)
        if len(identifier) != 2:
            raise ValueError("an Iceberg sink URI must name a table")
        from pyiceberg.exceptions import NoSuchTableError

        try:
            cat.create_namespace_if_not_exists(identifier[:-1])
        except Exception:
            raise ValueError(
                "Iceberg namespace could not be created; check catalog access"
            ) from None
        try:
            self.table = cat.load_table(identifier)
        except NoSuchTableError:
            try:
                self.table = cat.create_table(
                    identifier,
                    iceberg_schema(self._mapped),
                    partition_spec=spec,
                    properties={"format-version": str(version), SCHEMA_KEY: encode_schema(schema)},
                )
            except Exception:
                raise ValueError(
                    "Iceberg table could not be created; check schema and access"
                ) from None
        except Exception:
            raise ValueError("Iceberg table could not be loaded; check catalog access") from None
        else:
            old = (
                decode_schema(self.table.properties[SCHEMA_KEY])
                if SCHEMA_KEY in self.table.properties
                else self.table.schema().as_arrow()
            )
            for name in dict.fromkeys([*old.names, *schema.names]):
                if (
                    name not in old.names
                    or name not in schema.names
                    or not old.field(name).equals(schema.field(name), check_metadata=True)
                ):
                    raise ValueError(
                        f"Iceberg schema differs at column {name}; "
                        "schema evolution is not supported"
                    )
            if "partition_by" in options:
                wanted = [
                    (iceberg_schema(self._mapped).find_field(f.source_id).name, str(f.transform))
                    for f in spec.fields
                ]
                existing = [
                    (self.table.schema().find_field(f.source_id).name, str(f.transform))
                    for f in self.table.spec().fields
                ]
                if wanted != existing:
                    raise ValueError(
                        "Iceberg partition schema differs; partition evolution is not supported"
                    )
            if old.names != schema.names:
                raise ValueError(f"Iceberg schema differs at column {schema.names[0]}: field order")

    def write_batch(self, batch: pa.RecordBatch) -> None:
        if not batch.schema.equals(self._schema, check_metadata=True):
            column = next(
                (
                    name
                    for name in dict.fromkeys([*self._schema.names, *batch.schema.names])
                    if name not in self._schema.names
                    or name not in batch.schema.names
                    or not self._schema.field(name).equals(
                        batch.schema.field(name), check_metadata=True
                    )
                ),
                next(iter(self._schema.names), "<schema>"),
            )
            raise ValueError(f"Iceberg batch schema differs at column {column}")
        if batch.num_rows:
            self._pending.append(batch)
            self._pending_rows += batch.num_rows
        if (self.commit_rows is not None and self._pending_rows >= self.commit_rows) or (
            self.commit_seconds is not None and self._clock() - self._since >= self.commit_seconds
        ):
            self.flush()

    def write_all(self, batches: Iterable[pa.RecordBatch]) -> None:
        for batch in batches:
            self.write_batch(batch)

    def flush(self) -> None:
        if not self._pending:
            return
        self._commit()

    def _commit(self) -> None:
        whole = pa.Table.from_batches(self._pending, schema=self._schema)
        # Explicit, opt-in nanosecond truncation; all other conversions retain their values.
        mapped = pa.Table.from_batches(
            [convert_batch(batch, self._mapped, writing=True) for batch in whole.to_batches()],
            schema=self._mapped,
        )
        properties: dict[str, str] = {}
        if self._stamp:
            from shape.fingerprint import KEY, dump, for_sink

            properties[KEY] = dump(for_sink(self._name, whole, self._stamp))
        try:
            if self._mode == "append" and "uuid" not in str(self.table.schema()):
                self.table.append(mapped, snapshot_properties=properties)
            else:
                # Table.overwrite emits DELETE and APPEND snapshots. A single overwrite
                # producer commits both file changes atomically in one snapshot instead.
                with self.table.transaction() as tx:
                    snapshots = tx.update_snapshot(snapshot_properties=properties)
                    producer = (
                        snapshots.fast_append() if self._mode == "append" else snapshots.overwrite()
                    )
                    with producer as update:
                        if self._mode == "overwrite":
                            for task in self.table.scan().plan_files():
                                update.delete_data_file(task.file)
                        for data_file in data_files(
                            table_metadata=tx.table_metadata,
                            write_uuid=update.commit_uuid,
                            df=mapped,
                            io=self.table.io,
                        ):
                            update.append_data_file(data_file)
        except Exception:
            raise ValueError(
                "Iceberg write failed; check schema, catalog access and storage configuration"
            ) from None
        self.rows += self._pending_rows
        self.commits += 1
        self._pending = []
        self._pending_rows = 0
        self._since = self._clock()
        self._mode = "append"

    def close(self, *, schema: pa.Schema | None = None) -> int:
        if self._pending or self.commits == 0:
            self._commit()
        return self.rows

    def abort(self) -> None:
        self._pending = []
        self._pending_rows = 0


class IcebergSink:
    name = "iceberg"
    schemes = ("iceberg", "iceberg+file")
    extension = ""

    def preflight(self, uri: str, tables: dict[str, Any]) -> None:
        parse(uri, writing=True)
        if len(tables) != 1:
            raise ValueError("an Iceberg table URI accepts one generated table per target")

    def open_table(
        self, uri: str, table: str, schema: pa.Schema | None = None, **options: Any
    ) -> IcebergTableWriter:
        require_scheme(self, uri)
        if schema is None:
            raise ValueError("an Iceberg table needs schema")
        return IcebergTableWriter(uri, schema, options)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        stream = iter(batches)
        first = next(stream, None)
        supplied = options.pop("schema", None)
        schema = first.schema if first is not None else supplied
        if supplied is not None and first is not None and not supplied.equals(first.schema):
            raise ValueError("Iceberg first batch schema differs from the supplied schema")
        writer = self.open_table(uri, table, schema, **options)
        try:
            if first is not None:
                writer.write_batch(first)
            writer.write_all(stream)
            return writer.close()
        except BaseException:
            writer.abort()
            raise
