"""Lazy Iceberg catalog, URI and lossless Arrow schema helpers."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pyarrow as pa  # type: ignore[import-untyped]

SCHEMA_KEY = "shape.arrow-schema"


def dependency() -> Any:
    try:
        import pyiceberg.catalog
    except ImportError:
        raise ImportError(
            "Iceberg needs the optional extra: pip install 'sqllocks-shape[iceberg]'"
        ) from None
    return pyiceberg.catalog


def parse(uri: str, *, writing: bool = False) -> tuple[str, tuple[str, ...], Path | None]:
    parsed = urlparse(uri)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Iceberg URIs cannot contain credentials, query parameters or fragments")
    if parsed.scheme == "iceberg":
        parts = tuple(unquote(x) for x in parsed.path.split("/") if x)
        if not parsed.netloc or len(parts) not in (1, 2):
            raise ValueError("iceberg URI needs a catalog, namespace and optional table")
        return parsed.netloc, parts, None
    if parsed.scheme != "iceberg+file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("Iceberg needs iceberg:// or a local iceberg+file:// URI")
    path = Path(url2pathname(parsed.path)).resolve()
    warehouse = next((p for p in path.parents if (p / "catalog.db").is_file()), None)
    if warehouse is None:
        if not writing:
            raise ValueError("local Iceberg catalog.db does not exist; write a table first")
        warehouse = path.parent.parent
    parts = path.relative_to(warehouse).parts
    if len(parts) not in (1, 2) or any(x in (".", "..", "") for x in parts):
        raise ValueError("local Iceberg URI needs a namespace and optional table")
    return "local", parts, warehouse


def catalog(
    uri: str, options: dict[str, Any], *, writing: bool = False
) -> tuple[Any, tuple[str, ...]]:
    name, identifier, warehouse = parse(uri, writing=writing)
    config: dict[str, Any] = {}
    if warehouse is not None:
        if writing:
            warehouse.mkdir(parents=True, exist_ok=True)
        config = {
            "type": "sql",
            "uri": f"sqlite:///{(warehouse / 'catalog.db').as_posix()}",
            "warehouse": warehouse.as_uri(),
        }
    else:
        kind = options.get("catalog_type")
        if kind is not None:
            if kind not in ("rest", "sql", "glue", "hive"):
                raise ValueError("catalog_type must be rest, sql, glue or hive")
            config["type"] = kind
        for key in ("uri", "warehouse"):
            if options.get(key) is not None:
                value = str(options[key])
                parsed = urlparse(value)
                if parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError(
                        f"Iceberg {key} cannot contain credentials or query parameters"
                    )
                config[key] = value
        from shape.security.credrefs import resolve_reference

        for key in ("credential", "token"):
            if options.get(key) is not None:
                config[key] = resolve_reference(str(options[key]))
    try:
        return dependency().load_catalog(name, **config), identifier
    except ImportError:
        raise
    except Exception:
        raise ValueError(
            "could not open Iceberg catalog; check catalog configuration and credential references"
        ) from None


def encode_schema(schema: pa.Schema) -> str:
    return json.dumps(
        {
            "format": "shape.iceberg.arrow-schema",
            "version": 1,
            "ipc": base64.b64encode(schema.serialize().to_pybytes()).decode("ascii"),
        },
        sort_keys=True,
    )


def decode_schema(value: str) -> pa.Schema:
    record = json.loads(value)
    if record.get("format") != "shape.iceberg.arrow-schema" or record.get("version") != 1:
        raise ValueError("unsupported shape.iceberg.arrow-schema format or version")
    return pa.ipc.read_schema(pa.BufferReader(base64.b64decode(record["ipc"])))


def mapped_field(field: pa.Field, truncate_ns: bool) -> pa.Field:
    t = field.type
    if (
        (field.metadata or {}).get(b"uuid") == b"true"
        or (field.metadata or {}).get(b"shape.type") == b"uuid"
        or t == pa.uuid()
    ):
        target = pa.binary(16)
    elif pa.types.is_int8(t) or pa.types.is_int16(t):
        target = pa.int32()
    elif pa.types.is_uint32(t):
        target = pa.int64()
    elif pa.types.is_uint64(t):
        target = pa.decimal128(20, 0)
    elif pa.types.is_timestamp(t):
        if t.unit == "ns" and not truncate_ns:
            raise ValueError(f"column {field.name}: nanosecond timestamp needs truncate_ns=true")
        target = pa.timestamp("us", tz="UTC" if t.tz else None)
    elif pa.types.is_time64(t):
        if t.unit == "ns" and not truncate_ns:
            raise ValueError(f"column {field.name}: nanosecond time needs truncate_ns=true")
        target = pa.time64("us")
    elif pa.types.is_date(t):
        target = pa.date32()
    elif pa.types.is_decimal(t):
        if t.scale < 0 or t.scale > t.precision:
            raise ValueError(
                f"column {field.name}: Iceberg decimal scale must be between 0 and precision"
            )
        if t.precision > 38:
            raise ValueError(f"column {field.name}: decimal precision exceeds 38")
        target = pa.decimal128(t.precision, t.scale)
    elif pa.types.is_list(t) or pa.types.is_large_list(t):
        target = pa.list_(mapped_field(t.value_field, truncate_ns))
    elif pa.types.is_struct(t):
        target = pa.struct([mapped_field(child, truncate_ns) for child in t])
    elif pa.types.is_map(t):
        target = pa.map_(
            mapped_field(t.key_field, truncate_ns), mapped_field(t.item_field, truncate_ns)
        )
    elif pa.types.is_integer(t) and t.bit_width < 32 and pa.types.is_unsigned_integer(t):
        raise ValueError(f"column {field.name}: unsupported unsigned width")
    elif (
        pa.types.is_boolean(t)
        or pa.types.is_integer(t)
        or pa.types.is_floating(t)
        or pa.types.is_string(t)
        or pa.types.is_binary(t)
        or isinstance(t, pa.BaseExtensionType)
    ):
        target = t
    else:
        raise ValueError(f"column {field.name}: unsupported Iceberg type {t}")
    metadata = dict(field.metadata or {})
    if t == pa.uuid():
        metadata[b"uuid"] = b"true"
    metadata[b"doc"] = encode_schema(pa.schema([field])).encode()
    return field.with_type(target).with_metadata(metadata)


def mapped_schema(schema: pa.Schema, truncate_ns: bool) -> pa.Schema:
    return pa.schema([mapped_field(f, truncate_ns) for f in schema])


def partition_spec(schema: pa.Schema, transforms: Any) -> Any:
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.transforms import (
        BucketTransform,
        DayTransform,
        IdentityTransform,
        MonthTransform,
        TruncateTransform,
    )

    native_schema = iceberg_schema(schema)
    if isinstance(transforms, str):
        transforms = [transforms]
    fields = []
    for index, text in enumerate(transforms or ()):
        match = re.fullmatch(
            r"\s*(?:(day|month)\(\s*([\w]+)\s*\)|(bucket|truncate)\(\s*(\d+)\s*,\s*([\w]+)\s*\)|([\w]+))\s*",
            str(text),
        )
        if not match:
            raise ValueError("invalid Iceberg partition transform")
        temporal, temporal_col, numbered, number, numbered_col, identity = match.groups()
        column = temporal_col or numbered_col or identity
        if column not in schema.names:
            raise ValueError(f"partition column {column!r} does not exist")
        transform = (
            {"day": DayTransform, "month": MonthTransform}.get(temporal, IdentityTransform)()
            if not numbered
            else (BucketTransform if numbered == "bucket" else TruncateTransform)(int(number))
        )
        if numbered and int(number) <= 0:
            raise ValueError("Iceberg bucket and truncate widths must be positive")
        source = native_schema.find_field(column)
        if not transform.can_transform(source.field_type):
            raise ValueError(f"partition transform is invalid for column {column}")
        fields.append(
            PartitionField(
                source_id=source.field_id,
                field_id=1000 + index,
                transform=transform,
                name=f"{column}_{index}",
            )
        )
    return PartitionSpec(*fields)


def restored_field(field: pa.Field) -> pa.Field:
    """Restore recorded widths while retaining Iceberg's recursive temporal normalization."""
    t = field.type
    if pa.types.is_timestamp(t):
        target = pa.timestamp("us", tz="UTC" if t.tz else None)
    elif pa.types.is_time64(t):
        target = pa.time64("us")
    elif pa.types.is_struct(t):
        target = pa.struct([restored_field(child) for child in t])
    elif pa.types.is_list(t) or pa.types.is_large_list(t):
        target = pa.list_(restored_field(t.value_field))
    elif pa.types.is_map(t):
        target = pa.map_(restored_field(t.key_field), restored_field(t.item_field))
    else:
        target = t
    return field.with_type(target)


def iceberg_schema(schema: pa.Schema) -> Any:
    """Assign native UUID types even on the supported 0.9 Arrow adapter."""
    from pyiceberg.io.pyarrow import _pyarrow_to_schema_without_ids
    from pyiceberg.schema import Schema, assign_fresh_schema_ids
    from pyiceberg.types import ListType, MapType, StructType, UUIDType

    native = assign_fresh_schema_ids(_pyarrow_to_schema_without_ids(schema))

    def restore(t: Any, arrow: Any, metadata: Any = None) -> Any:
        if (metadata or {}).get(b"uuid") == b"true" or (metadata or {}).get(
            b"shape.type"
        ) == b"uuid":
            return UUIDType()
        if pa.types.is_struct(arrow):
            return StructType(
                *[
                    f.model_copy(update={"field_type": restore(f.field_type, a.type, a.metadata)})
                    for f, a in zip(t.fields, arrow, strict=True)
                ]
            )
        if pa.types.is_list(arrow):
            return ListType(
                t.element_id,
                restore(t.element_type, arrow.value_type, arrow.value_field.metadata),
                t.element_required,
            )
        if pa.types.is_map(arrow):
            return MapType(
                t.key_id,
                restore(t.key_type, arrow.key_type, arrow.key_field.metadata),
                t.value_id,
                restore(t.value_type, arrow.item_type, arrow.item_field.metadata),
                t.value_required,
            )
        return t

    return Schema(
        *[
            f.model_copy(update={"field_type": restore(f.field_type, a.type, a.metadata)})
            for f, a in zip(native.fields, schema, strict=True)
        ]
    )


def data_files(*, table_metadata: Any, write_uuid: Any, df: pa.Table, io: Any) -> Any:
    """Write native UUID tasks without Arrow UUID inference or grouping limitations."""
    from uuid import UUID

    from pyiceberg.io.pyarrow import (
        _dataframe_to_data_files,
        _determine_partitions,
        bin_pack_arrow_table,
        write_file,
    )
    from pyiceberg.partitioning import PartitionKey, partition_record_value
    from pyiceberg.table import WriteTask
    from pyiceberg.typedef import Record
    from pyiceberg.types import UUIDType

    class TypedUUIDPartitionKey(PartitionKey):
        @property
        def partition(self) -> Any:
            values = {}
            for item in self.field_values:
                source = self.schema.find_field(item.field.source_id).field_type
                value = item.value
                if isinstance(source, UUIDType):
                    if value is not None and isinstance(
                        item.field.transform.result_type(source), UUIDType
                    ):
                        value = UUID(bytes=value) if isinstance(value, bytes) else value
                else:
                    value = partition_record_value(item.field, value, self.schema)
                values[item.field.name] = value
            return Record(*values.values())

    if df.num_rows == 0:
        return iter(())
    if "uuid" not in str(table_metadata.schema()):
        return _dataframe_to_data_files(
            table_metadata=table_metadata, write_uuid=write_uuid, df=df, io=io
        )
    size = int(table_metadata.properties.get("write.target-file-size-bytes", 512 * 1024 * 1024))
    spec = table_metadata.spec()
    groups: list[tuple[Any, Any]]
    if spec.is_unpartitioned():
        groups = [(None, df)]
    else:
        groups = [
            (p.partition_key, p.arrow_table_partition)
            for p in _determine_partitions(spec, table_metadata.schema(), df)
        ]
    tasks: list[Any] = []
    for key, group in groups:
        if key is not None:
            # 0.9 converts already-transformed UUID values to strings. Preserve
            # their physical Arrow values in a per-write partition key instead.
            key = TypedUUIDPartitionKey(key.field_values, key.partition_spec, key.schema)
        for batches in bin_pack_arrow_table(group, size):
            tasks.append(
                WriteTask(
                    write_uuid=write_uuid,
                    task_id=len(tasks),
                    record_batches=batches,
                    schema=table_metadata.schema(),
                    partition_key=key,
                )
            )
    return write_file(io=io, table_metadata=table_metadata, tasks=iter(tasks))


def convert_batch(batch: pa.RecordBatch, schema: pa.Schema, *, writing: bool) -> pa.RecordBatch:
    """Preserve nested Arrow values while translating logical UUID strings to native bytes."""
    from uuid import UUID

    def array(value: Any, field: pa.Field) -> Any:
        t = field.type
        is_uuid = (field.metadata or {}).get(b"uuid") == b"true" or (field.metadata or {}).get(
            b"shape.type"
        ) == b"uuid"
        try:
            if is_uuid and pa.types.is_string(value.type) and t == pa.binary(16):
                return pa.array(
                    [None if x is None else UUID(x).bytes for x in value.to_pylist()], type=t
                )
            if is_uuid and pa.types.is_string(t):
                raw = value.cast(pa.binary(16))
                return pa.array(
                    [None if x is None else str(UUID(bytes=x)) for x in raw.to_pylist()], type=t
                )
            if pa.types.is_struct(t):
                return pa.StructArray.from_arrays(
                    [array(value.field(i), f) for i, f in enumerate(t)],
                    fields=list(t),
                    mask=value.is_null(),
                )
            if pa.types.is_list(t):
                return pa.ListArray.from_arrays(
                    value.offsets.cast(pa.int32()),
                    array(value.values, t.value_field),
                    type=t,
                    mask=value.is_null(),
                )
            if pa.types.is_map(t):
                return pa.MapArray.from_arrays(
                    value.offsets,
                    array(value.keys, t.key_field),
                    array(value.items, t.item_field),
                    type=t,
                    mask=value.is_null(),
                )
            return value.cast(t, safe=not writing)
        except (ValueError, TypeError, pa.ArrowException):
            raise ValueError(f"column {field.name}: invalid value for Iceberg {t}") from None

    return pa.RecordBatch.from_arrays(
        [array(batch.column(i), f) for i, f in enumerate(schema)], schema=schema
    )
