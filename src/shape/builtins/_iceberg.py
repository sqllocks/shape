"""Lazy Iceberg catalog, URI and lossless Arrow schema helpers."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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
    path = Path(unquote(parsed.path)).resolve()
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
            "uri": f"sqlite:///{warehouse / 'catalog.db'}",
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
    if pa.types.is_int8(t) or pa.types.is_int16(t):
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
        target = pa.time64("us")
    elif pa.types.is_date(t):
        target = pa.date32()
    elif pa.types.is_decimal(t):
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
    elif (field.metadata or {}).get(b"uuid") == b"true":
        target = pa.uuid()
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
    metadata[b"doc"] = encode_schema(pa.schema([field])).encode()
    return field.with_type(target).with_metadata(metadata)


def mapped_schema(schema: pa.Schema, truncate_ns: bool) -> pa.Schema:
    return pa.schema([mapped_field(f, truncate_ns) for f in schema])


def partition_spec(schema: pa.Schema, transforms: Any) -> Any:
    from pyiceberg.io.pyarrow import _pyarrow_to_schema_without_ids
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import assign_fresh_schema_ids
    from pyiceberg.transforms import (
        BucketTransform,
        DayTransform,
        IdentityTransform,
        MonthTransform,
        TruncateTransform,
    )

    iceberg_schema = assign_fresh_schema_ids(_pyarrow_to_schema_without_ids(schema))
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
        source = iceberg_schema.find_field(column)
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
