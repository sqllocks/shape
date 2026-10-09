"""Iceberg Arrow batch scans with snapshot selection and width restoration."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins._iceberg import SCHEMA_KEY, catalog, convert_batch, decode_schema, parse


class IcebergSource:
    name = "iceberg"
    schemes = ("iceberg", "iceberg+file")

    def can_open(self, uri: str) -> bool:
        from shape.plugins.schemes import uri_scheme

        return uri_scheme(uri) in self.schemes

    def _table(self, uri: str, options: dict[str, Any]) -> Any:
        cat, identifier = catalog(uri, options)
        if len(identifier) != 2:
            raise ValueError("Iceberg scan needs a table URI")
        try:
            return cat.load_table(identifier)
        except Exception:
            raise ValueError(
                "Iceberg table could not be loaded; check namespace, table and access"
            ) from None

    def _snapshot(self, table: Any, options: dict[str, Any]) -> Any:
        snapshot_id, as_of = options.get("snapshot_id"), options.get("as_of")
        if snapshot_id is not None and as_of is not None:
            raise ValueError("pass snapshot_id or as_of, not both")
        if as_of is not None:
            try:
                stamp = (
                    as_of
                    if isinstance(as_of, datetime)
                    else datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
                )
                stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
                millis = int(stamp.timestamp() * 1000)
            except (ValueError, TypeError, OverflowError):
                raise ValueError("as_of needs an ISO timestamp") from None
            candidates = [s for s in table.metadata.snapshots if s.timestamp_ms <= millis]
            if not candidates:
                raise ValueError("no Iceberg snapshot exists at as_of")
            return max(candidates, key=lambda s: s.timestamp_ms)
        if snapshot_id is not None and (
            isinstance(snapshot_id, bool) or not isinstance(snapshot_id, int)
        ):
            raise ValueError("snapshot_id needs an integer")
        snapshot = (
            table.snapshot_by_id(int(snapshot_id))
            if snapshot_id is not None
            else table.current_snapshot()
        )
        if snapshot is None and snapshot_id is not None:
            raise ValueError("Iceberg snapshot_id does not exist")
        return snapshot

    def _schema(self, table: Any) -> pa.Schema:
        original = table.properties.get(SCHEMA_KEY)
        if original:
            schema = decode_schema(original)
            from shape.builtins._iceberg import restored_field

            return pa.schema([restored_field(f) for f in schema], metadata=schema.metadata)
        native = table.schema()
        arrow = native.as_arrow()
        from shape.builtins._iceberg import restored_field

        restored = []
        for field in native.fields:
            if field.doc and '"shape.iceberg.arrow-schema"' in field.doc:
                restored.append(restored_field(decode_schema(field.doc).field(0)))
            else:
                restored.append(arrow.field(field.name))
        return pa.schema(restored, metadata=arrow.metadata)

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        table = self._table(uri, options)
        self._snapshot(table, options)
        schema = self._schema(table)
        columns = options.get("columns")
        return pa.schema([schema.field(c) for c in columns]) if columns else schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        table = self._table(uri, options)
        snapshot = self._snapshot(table, options)
        schema = self.schema(uri, **options)
        if snapshot is None:
            return
        scan = table.scan(
            snapshot_id=snapshot.snapshot_id,
            selected_fields=tuple(options.get("columns") or ("*",)),
        )
        try:
            for batch in scan.to_arrow_batch_reader():
                yield convert_batch(batch, schema, writing=False)
        except Exception:
            raise ValueError("Iceberg scan failed; check snapshot and storage access") from None

    def profile_source(self, uri: str, **options: Any) -> Any:
        import shape

        profile_options = options.pop("profile_options", {})
        from shape.profile.reference.sources import source_options

        cat, identifier = catalog(uri, options)
        if len(identifier) == 1:
            try:
                identifiers = cat.list_tables(identifier)
            except Exception:
                raise ValueError(
                    "Iceberg namespace could not be listed; check catalog access"
                ) from None
            if not identifiers:
                raise ValueError("Iceberg namespace contains no tables")
            tables = {}
            provenance = {}
            for item in identifiers:
                target = uri.rstrip("/") + "/" + item[-1]
                table = self._table(target, options)
                snapshot = self._snapshot(table, options)
                tables[item[-1]] = self._profile_table(
                    pa.Table.from_batches(
                        self.read(target, **options), schema=self.schema(target, **options)
                    )
                )
                provenance[item[-1]] = self._provenance(target, snapshot)
            result = shape.profile(tables, **profile_options)
            for table_name, evidence in provenance.items():
                result.tables[table_name]["row_count"] = evidence["total_records"]
            result._provenance = {"format": "iceberg", "version": 1, "tables": provenance}
            return result
        table = self._table(uri, options)
        snapshot = self._snapshot(table, options)
        with source_options():
            result = shape.profile(
                self._profile_table(
                    pa.Table.from_batches(
                        self.read(uri, **options), schema=self.schema(uri, **options)
                    )
                ),
                **{**profile_options, "name": profile_options.get("name") or identifier[-1]},
            )
        result._provenance = self._provenance(uri, snapshot)
        next(iter(result.tables.values()))["row_count"] = result._provenance["total_records"]
        return result

    def _profile_table(self, table: pa.Table) -> pa.Table:
        """The profiler consumes UUID text; scans retain the original Arrow representation."""
        from uuid import UUID

        for index, field in enumerate(table.schema):
            metadata = field.metadata or {}
            logical_uuid = (
                field.type == pa.uuid()
                or metadata.get(b"uuid") == b"true"
                or metadata.get(b"shape.type") == b"uuid"
            )
            if logical_uuid and not pa.types.is_string(field.type):
                values = [
                    None if x is None else str(UUID(bytes=x)) if isinstance(x, bytes) else str(x)
                    for x in table.column(index).to_pylist()
                ]
                target = field.with_type(pa.string()).with_metadata(
                    {**metadata, b"shape.type": b"uuid"}
                )
                table = table.set_column(index, target, pa.array(values, pa.string()))
        return table

    def _provenance(self, uri: str, snapshot: Any) -> dict[str, Any]:
        name, identifier, _ = parse(uri)
        return {
            "format": "iceberg",
            "version": 1,
            "catalog": name,
            "table": ".".join(identifier),
            "snapshot_id": None if snapshot is None else snapshot.snapshot_id,
            "total_records": 0
            if snapshot is None
            else int(snapshot.summary.additional_properties.get("total-records", 0)),
        }
