"""Event formats for ``kafka://``: Avro, Protobuf and JSON Schema through a schema registry.

``--event-format json`` (the default) sends the flat event's JSON, as always. The other three
derive a schema from the Arrow schema of the table's events, register it, and send the
Confluent wire format: byte 0 (the magic byte), the 4-byte big-endian schema id, then the payload
(Protobuf adds the message-index list ``[0]``, one zero byte, as the Confluent serializers do).

The type mapping is the documented contract (``plugins/shape-kafka/README.md``). Every column of a
table keeps its name and position; the event fields ``_shape_table`` and ``_shape_seq`` are never
null. A nullable Arrow column is a nullable field (Avro union with ``null``, Protobuf ``optional``,
JSON ``null``). A float column is always nullable, because a non-finite float is sent as ``null``
in every format (as in the flat JSON event).

====================  ===========================  ======================  =====================
Arrow                 Avro                         Protobuf                JSON Schema
====================  ===========================  ======================  =====================
bool                  boolean                      bool                    boolean
int8/16/32, uint8/16  int                          int32 / uint32          integer
int64, uint32         long                         int64 / uint32          integer
uint64                long (above 2**63-1: fails)  uint64                  integer
float32 / float64     float / double               float / double          number
string                string                       string                  string
uuid (string)         string, logicalType uuid     string                  string, format uuid
binary                bytes                        bytes                   string, base64
decimal(p, s)         bytes, logicalType decimal   string (canonical)      string (canonical)
date                  int, logicalType date        int32 (days)            string, format date
time                  time-millis / time-micros    int64 (microseconds)    string
timestamp, no zone    local-timestamp-millis/      int64 (microseconds,    string, local
                      -micros                      wall clock as UTC)
timestamp with zone   timestamp-millis/-micros     int64 (microseconds,    string, format
                                                   UTC)                    date-time
====================  ===========================  ======================  =====================

A timestamp of unit second or millisecond is Avro ``-millis``; microsecond is ``-micros``;
nanosecond is ``-micros`` with the nanoseconds cut off. A dictionary column is its value type.
Lists, structs and maps are not mapped: such a table is refused (exit 2) naming the column.
A field name must be a valid identifier (letters, digits, underscore; not starting with a digit)
in Avro and Protobuf, and the table name is the record or message name.
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from shape.errors import ShapeError
from shape.streaming.emit.formats import FIELD_POISON, FIELD_SEQ, FIELD_TABLE

EVENT_FORMATS = ("json", "avro", "protobuf", "json-schema")
RECORD_NAMESPACE = "shape.events"
MAGIC = b"\x00"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NEVER_NULL = (FIELD_TABLE, FIELD_SEQ)
INSTALL = {
    "avro": "pip install 'sqllocks-shape-kafka[avro]'",
    "protobuf": "pip install 'sqllocks-shape-kafka[protobuf]'",
}


class EncodeError(ValueError):
    """One event cannot be encoded in the chosen format (the run may dead-letter it)."""


@dataclass(frozen=True, slots=True)
class Col:
    """One column of the events: its name and nullability, and the mapped kind (``bool``, ``int``,
    ``long``, ``uint32``, ``uint64``, ``float``, ``double``, ``string``, ``uuid``, ``bytes``,
    ``decimal``, ``date``, ``time``, ``timestamp``, ``local_timestamp``) with its parameters."""

    name: str
    kind: str
    nullable: bool
    precision: int = 0
    scale: int = 0
    unit: str = "us"  # the Arrow unit of a time or timestamp
    arrow: pa.DataType = pa.null()


def _kind(name: str, t: pa.DataType) -> tuple[str, dict[str, Any]]:
    if pa.types.is_dictionary(t):
        return _kind(name, t.value_type)
    if pa.types.is_null(t):
        return "null", {}
    if pa.types.is_boolean(t):
        return "bool", {}
    if pa.types.is_int8(t) or pa.types.is_int16(t) or pa.types.is_int32(t):
        return "int", {}
    if pa.types.is_uint8(t) or pa.types.is_uint16(t):
        return "uint32small", {}
    if pa.types.is_int64(t):
        return "long", {}
    if pa.types.is_uint32(t):
        return "uint32", {}
    if pa.types.is_uint64(t):
        return "uint64", {}
    if pa.types.is_float32(t):
        return "float", {}
    if pa.types.is_floating(t):
        return "double", {}
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "string", {}
    if isinstance(t, pa.BaseExtensionType) and t.extension_name == "arrow.uuid":
        return "uuid", {}
    if pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        return "bytes", {}
    if pa.types.is_decimal(t):
        return "decimal", {"precision": t.precision, "scale": t.scale}
    if pa.types.is_date(t):
        return "date", {}
    if pa.types.is_time(t):
        return "time", {"unit": t.unit}
    if pa.types.is_timestamp(t):
        return ("timestamp" if t.tz is not None else "local_timestamp"), {"unit": t.unit}
    raise ShapeError(
        f"cannot map column {name!r} of type {t} to a registry format "
        "(lists, structs and maps are not supported); use --event-format json"
    )


def columns_of(table: str, schema: pa.Schema, *, ident: bool) -> list[Col]:
    """The mapped columns of ``schema`` (the event fields included, a poison marker not).
    ``ident`` checks that every name is a valid Avro/Protobuf identifier."""
    if ident and not _IDENT.match(table):
        raise ShapeError(
            f"table name {table!r} is not a valid record name (letters, digits and underscore); "
            "use --event-format json or json-schema"
        )
    cols: list[Col] = []
    for f in schema:
        if f.name == FIELD_POISON:
            continue
        if ident and not _IDENT.match(f.name):
            raise ShapeError(
                f"column {f.name!r} of table {table!r} is not a valid field name in Avro and "
                "Protobuf (letters, digits and underscore); use --event-format json or json-schema"
            )
        kind, extra = _kind(f.name, f.type)
        nullable = bool(f.nullable) and f.name not in _NEVER_NULL
        if kind in ("float", "double"):
            nullable = True  # a non-finite float is null
        cols.append(
            Col(
                f.name,
                kind,
                nullable or kind == "null",
                precision=extra.get("precision", 0),
                scale=extra.get("scale", 0),
                unit=extra.get("unit", "us"),
                arrow=f.type.value_type if pa.types.is_dictionary(f.type) else f.type,
            )
        )
    return cols


# ---- values --------------------------------------------------------------------------------


def _as_int(col: pa.Array, to: pa.DataType) -> list[Any]:
    return col.cast(to, safe=False).to_pylist()  # type: ignore[no-any-return]


def _plain(col: pa.Array) -> pa.Array:
    return col.cast(col.type.value_type) if pa.types.is_dictionary(col.type) else col


def _float_values(col: pa.Array) -> list[Any]:
    return pc.if_else(pc.is_finite(col), col, pa.scalar(None, col.type)).to_pylist()  # type: ignore[no-any-return]


def values_for(column: Col, array: pa.Array, fmt: str) -> list[Any]:
    """The Python values of ``array`` as ``fmt`` (``avro``, ``protobuf``) takes them."""
    array = _plain(array)
    k = column.kind
    if k in ("float", "double"):
        return _float_values(array)
    if k == "uuid":
        return [None if v is None else str(v) for v in array.to_pylist()]
    if k == "decimal":
        if fmt == "avro":
            return array.to_pylist()  # type: ignore[no-any-return]
        return array.cast(pa.string()).to_pylist()  # type: ignore[no-any-return]
    if k == "date":
        return _as_int(array.cast(pa.date32()), pa.int32())
    if k == "time":
        if fmt == "avro":
            if column.unit in ("s", "ms"):
                return _as_int(array.cast(pa.time32("ms"), safe=False), pa.int32())
            return _as_int(array.cast(pa.time64("us"), safe=False), pa.int64())
        return _as_int(array.cast(pa.time64("us"), safe=False), pa.int64())
    if k in ("timestamp", "local_timestamp"):
        unit = "ms" if fmt == "avro" and column.unit in ("s", "ms") else "us"
        tz = array.type.tz
        return _as_int(array.cast(pa.timestamp(unit, tz), safe=False), pa.int64())
    return array.to_pylist()  # type: ignore[no-any-return]


# ---- Avro ----------------------------------------------------------------------------------


def _avro_type(c: Col) -> Any:
    k = c.kind
    simple = {
        "null": "null",
        "bool": "boolean",
        "int": "int",
        "uint32small": "int",
        "long": "long",
        "uint32": "long",
        "uint64": "long",
        "float": "float",
        "double": "double",
        "string": "string",
        "bytes": "bytes",
    }
    if k in simple:
        return simple[k]
    if k == "uuid":
        return {"type": "string", "logicalType": "uuid"}
    if k == "decimal":
        return {
            "type": "bytes",
            "logicalType": "decimal",
            "precision": c.precision,
            "scale": c.scale,
        }
    if k == "date":
        return {"type": "int", "logicalType": "date"}
    millis = c.unit in ("s", "ms")
    if k == "time":
        return (
            {"type": "int", "logicalType": "time-millis"}
            if millis
            else {"type": "long", "logicalType": "time-micros"}
        )
    prefix = "timestamp" if k == "timestamp" else "local-timestamp"
    return {"type": "long", "logicalType": f"{prefix}-{'millis' if millis else 'micros'}"}


def avro_schema(table: str, cols: Sequence[Col]) -> dict[str, Any]:
    fields = []
    for c in cols:
        t = _avro_type(c)
        if c.nullable and c.kind != "null":
            fields.append({"name": c.name, "type": ["null", t], "default": None})
        else:
            fields.append({"name": c.name, "type": t})
    return {"type": "record", "name": table, "namespace": RECORD_NAMESPACE, "fields": fields}


# ---- Protobuf ------------------------------------------------------------------------------

_PROTO_SCALAR = {
    "bool": "bool",
    "int": "int32",
    "uint32small": "uint32",
    "long": "int64",
    "uint32": "uint32",
    "uint64": "uint64",
    "float": "float",
    "double": "double",
    "string": "string",
    "uuid": "string",
    "bytes": "bytes",
    "decimal": "string",
    "date": "int32",
    "time": "int64",
    "timestamp": "int64",
    "local_timestamp": "int64",
}
_PROTO_NOTE = {
    "uuid": "uuid",
    "decimal": "decimal(%d, %d), canonical text",
    "date": "date, days since 1970-01-01",
    "time": "time of day, microseconds",
    "timestamp": "timestamp with time zone, microseconds since the epoch (UTC)",
    "local_timestamp": "timestamp without time zone, wall-clock microseconds since the epoch",
}


def _proto_kind(c: Col) -> str:
    if c.kind == "null":
        return "string"  # a column with no values at all: never set
    return _PROTO_SCALAR[c.kind]


def proto_text(table: str, cols: Sequence[Col]) -> str:
    lines = ['syntax = "proto3";', "", f"package {RECORD_NAMESPACE};", "", f"message {table} {{"]
    for i, c in enumerate(cols, 1):
        note = ""
        if c.kind in _PROTO_NOTE:
            note = "  // " + (
                _PROTO_NOTE[c.kind] % (c.precision, c.scale)
                if c.kind == "decimal"
                else _PROTO_NOTE[c.kind]
            )
        opt = "optional " if c.nullable else ""
        lines.append(f"  {opt}{_proto_kind(c)} {c.name} = {i};{note}")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---- JSON Schema ---------------------------------------------------------------------------


def _json_type(c: Col) -> dict[str, Any]:
    k = c.kind
    if k == "null":
        return {"type": "null"}
    if k == "bool":
        return {"type": "boolean"}
    if k in ("int", "uint32small", "long", "uint32", "uint64"):
        return {"type": "integer"}
    if k in ("float", "double"):
        return {"type": "number"}
    if k == "string":
        return {"type": "string"}
    if k == "uuid":
        return {"type": "string", "format": "uuid"}
    if k == "bytes":
        return {"type": "string", "contentEncoding": "base64"}
    if k == "decimal":
        return {
            "type": "string",
            "pattern": r"^-?[0-9]+(\.[0-9]+)?$",
            "description": f"decimal({c.precision}, {c.scale}), canonical text",
        }
    if k == "date":
        return {"type": "string", "format": "date"}
    if k == "time":
        return {"type": "string", "description": "time of day"}
    if k == "timestamp":
        return {"type": "string", "format": "date-time"}
    return {"type": "string", "description": "timestamp without time zone"}


def json_schema(table: str, cols: Sequence[Col]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for c in cols:
        t = _json_type(c)
        if c.nullable and c.kind != "null":
            t = {**t, "type": [t["type"], "null"]}
        props[c.name] = t
        if not c.nullable:
            required.append(c.name)
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": table,
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


# ---- codecs --------------------------------------------------------------------------------


@dataclass(slots=True)
class TableCodec:
    """The schema of one table in one format, and its row encoder."""

    table: str
    fmt: str
    columns: list[Col]
    schema_type: str  # the registry's schemaType: AVRO, PROTOBUF or JSON
    schema_text: str
    record: str  # the record's full name (subject naming)
    encode_batch: Callable[[pa.RecordBatch], list[bytes | EncodeError]]


def _payload_header(schema_id: int) -> bytes:
    return MAGIC + struct.pack(">I", schema_id)


def wire(schema_id: int, payload: bytes, fmt: str) -> bytes:
    """The Confluent wire format of ``payload``."""
    head = _payload_header(schema_id)
    return head + (b"\x00" + payload if fmt == "protobuf" else payload)


def _need(module: str, fmt: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ShapeError(
            f"--event-format {fmt} needs the {module.split('.')[0]} package: {INSTALL[fmt]}"
        ) from exc


def _rows(cols: Sequence[Col], batch: pa.RecordBatch, fmt: str) -> list[list[Any]]:
    by_name = {n: i for i, n in enumerate(batch.schema.names)}
    columns = []
    for c in cols:
        if c.name not in by_name:
            raise ShapeError(f"the events of a table lost the column {c.name!r} during the run")
        columns.append(values_for(c, batch.column(by_name[c.name]), fmt))
    return [list(r) for r in zip(*columns, strict=True)] if columns else []


def make_avro(table: str, schema: pa.Schema) -> TableCodec:
    fastavro = _need("fastavro", "avro")
    cols = columns_of(table, schema, ident=True)
    doc = avro_schema(table, cols)
    parsed = fastavro.parse_schema(doc)
    import io

    def encode(batch: pa.RecordBatch) -> list[bytes | EncodeError]:
        out: list[bytes | EncodeError] = []
        for row in _rows(cols, batch, "avro"):
            record = {c.name: v for c, v in zip(cols, row, strict=True)}
            try:
                _check_avro_ranges(cols, row)
                buf = io.BytesIO()
                fastavro.schemaless_writer(buf, parsed, record)
                out.append(buf.getvalue())
            except EncodeError as exc:
                out.append(exc)
            except Exception as exc:  # fastavro reports a bad value in many exception types
                out.append(EncodeError(f"{type(exc).__name__}: {str(exc)[:200]}"))
        return out

    return TableCodec(
        table,
        "avro",
        cols,
        "AVRO",
        json.dumps(doc, separators=(",", ":")),
        f"{RECORD_NAMESPACE}.{table}",
        encode,
    )


_LONG_MAX = (1 << 63) - 1


def _check_avro_ranges(cols: Sequence[Col], row: Sequence[Any]) -> None:
    for c, v in zip(cols, row, strict=True):
        if c.kind == "uint64" and v is not None and v > _LONG_MAX:
            raise EncodeError(f"column {c.name!r} does not fit a long")
        if c.kind == "decimal" and v is not None and len(v.as_tuple().digits) > c.precision:
            raise EncodeError(f"column {c.name!r} exceeds precision {c.precision}")


def make_protobuf(table: str, schema: pa.Schema) -> TableCodec:
    _need("google.protobuf", "protobuf")
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    cols = columns_of(table, schema, ident=True)
    fdp = descriptor_pb2.FileDescriptorProto(
        name=f"{table}.proto", package=RECORD_NAMESPACE, syntax="proto3"
    )
    msg = fdp.message_type.add(name=table)
    types = descriptor_pb2.FieldDescriptorProto
    scalar = {
        "bool": types.TYPE_BOOL,
        "int32": types.TYPE_INT32,
        "int64": types.TYPE_INT64,
        "uint32": types.TYPE_UINT32,
        "uint64": types.TYPE_UINT64,
        "float": types.TYPE_FLOAT,
        "double": types.TYPE_DOUBLE,
        "string": types.TYPE_STRING,
        "bytes": types.TYPE_BYTES,
    }
    for i, c in enumerate(cols, 1):
        f = msg.field.add(
            name=c.name, number=i, type=scalar[_proto_kind(c)], label=types.LABEL_OPTIONAL
        )
        if c.nullable:
            f.proto3_optional = True
            f.oneof_index = len(msg.oneof_decl)
            msg.oneof_decl.add(name=f"_{c.name}")
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fdp)
    cls = message_factory.GetMessageClass(pool.FindMessageTypeByName(f"{RECORD_NAMESPACE}.{table}"))

    def encode(batch: pa.RecordBatch) -> list[bytes | EncodeError]:
        out: list[bytes | EncodeError] = []
        for row in _rows(cols, batch, "protobuf"):
            m = cls()
            try:
                for c, v in zip(cols, row, strict=True):
                    if v is None:
                        if not c.nullable:
                            raise EncodeError(f"column {c.name!r} is null but required")
                        continue
                    setattr(m, c.name, v)
                out.append(m.SerializeToString())
            except EncodeError as exc:
                out.append(exc)
            except (ValueError, TypeError) as exc:
                out.append(EncodeError(f"{type(exc).__name__}: {str(exc)[:200]}"))
        return out

    return TableCodec(
        table,
        "protobuf",
        cols,
        "PROTOBUF",
        proto_text(table, cols),
        f"{RECORD_NAMESPACE}.{table}",
        encode,
    )


def make_json_schema(table: str, schema: pa.Schema) -> TableCodec:
    from shape.streaming.emit.formats import encode_events

    cols = columns_of(table, schema, ident=False)
    doc = json_schema(table, cols)

    def encode(batch: pa.RecordBatch) -> list[bytes | EncodeError]:
        return [e.body for e in encode_events(batch)]

    return TableCodec(
        table,
        "json-schema",
        cols,
        "JSON",
        json.dumps(doc, separators=(",", ":")),
        f"{RECORD_NAMESPACE}.{table}",
        encode,
    )


_MAKERS = {"avro": make_avro, "protobuf": make_protobuf, "json-schema": make_json_schema}


def make_codec(fmt: str, table: str, schema: pa.Schema) -> TableCodec:
    try:
        maker = _MAKERS[fmt]
    except KeyError:
        raise ShapeError(
            f"unknown event format {fmt!r}; choose from {', '.join(EVENT_FORMATS)}"
        ) from None
    return maker(table, schema)
