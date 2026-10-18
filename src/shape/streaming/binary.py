"""Optional Avro and Protobuf codecs shared by broker plugins (W9-09).

Raw payloads have no registry header. Protobuf descriptors are built from the
W5-06 parser; no compiler or network access is used to load a local schema.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError


def _need(name: str) -> Any:
    import importlib

    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise ShapeError(f"binary format requires optional package {name}") from exc


class BinaryCodec:
    """A local schema and a plain binary encoder/decoder, without a wire prefix."""

    def __init__(self, fmt: str, text: str, *, message: str | None = None) -> None:
        self.fmt, self.text = fmt, text
        self._cls: Any = None
        try:
            if fmt == "avro":
                avro = _need("fastavro")
                self._doc = avro.parse_schema(json.loads(text))
                self.schema = _avro_arrow(self._doc)
            elif fmt == "protobuf":
                self._cls = _proto_class(text, message)
                self.schema = _proto_arrow(self._cls.DESCRIPTOR)
            else:
                raise ShapeError(f"unsupported binary format {fmt!r}")
        except (ValueError, KeyError, TypeError) as exc:
            raise ShapeError(f"invalid {fmt} schema") from exc

    @classmethod
    def from_file(cls, fmt: str, path: str, *, message: str | None = None) -> BinaryCodec:
        """Read a UTF-8 schema from a local file."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ShapeError(f"cannot read {fmt} schema file") from exc
        return cls(fmt, text, message=message)

    def validate_schema(self, schema: pa.Schema) -> None:
        """Refuse a field mismatch before sending the first event in a batch."""
        for f in self.schema:
            if f.name not in schema.names:
                raise ShapeError(f"schema field {f.name!r} is missing from the table")
            _compatible(schema.field(f.name).type, f.type, f.name)
        for f in schema:
            if f.name not in self.schema.names and not f.name.startswith("_shape_"):
                raise ShapeError(f"table field {f.name!r} is missing from the schema")

    def encode(self, row: Mapping[str, Any]) -> bytes:
        record = {f.name: _normal(row.get(f.name), f.type) for f in self.schema}
        try:
            if self.fmt == "avro":
                buf = io.BytesIO()
                _need("fastavro").schemaless_writer(buf, self._doc, record, strict=True)
                return buf.getvalue()
            msg = self._cls()
            _fill_proto(msg, record)
            return bytes(msg.SerializeToString())
        except Exception as exc:
            raise ValueError(f"cannot encode {self.fmt} row") from exc

    def decode(self, body: bytes | str) -> dict[str, Any]:
        if not isinstance(body, bytes):
            raise ValueError("binary payload must be bytes")
        try:
            if self.fmt == "avro":
                buf = io.BytesIO(body)
                row = _need("fastavro").schemaless_reader(buf, self._doc)
                if buf.read(1):
                    raise ValueError("trailing bytes")
                return dict(row)
            msg = self._cls()
            msg.ParseFromString(body)
            return _read_proto(msg)
        except Exception as exc:
            raise ValueError(f"invalid {self.fmt} payload") from exc


def _normal(value: Any, typ: pa.DataType) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pa.types.is_integer(typ):
        if isinstance(value, datetime):
            delta = (
                value.replace(tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
                if value.tzinfo is None
                else value - datetime(1970, 1, 1, tzinfo=UTC)
            )
            return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        if isinstance(value, date):
            return (value - date(1970, 1, 1)).days
        if isinstance(value, time):
            return (
                (value.hour * 60 + value.minute) * 60 + value.second
            ) * 1_000_000 + value.microsecond
    if pa.types.is_string(typ) and isinstance(value, Decimal | UUID):
        return str(value)
    if pa.types.is_map(typ):
        return {k: _normal(v, typ.item_type) for k, v in dict(value).items()}
    if pa.types.is_struct(typ):
        return {f.name: _normal(value.get(f.name), f.type) for f in typ}
    if pa.types.is_list(typ):
        return [_normal(v, typ.value_type) for v in value]
    return value


def _compatible(actual: pa.DataType, expected: pa.DataType, path: str) -> None:
    if pa.types.is_dictionary(actual):
        actual = actual.value_type
    if pa.types.is_struct(expected) and pa.types.is_struct(actual):
        for f in expected:
            if f.name not in [a.name for a in actual]:
                raise ShapeError(f"schema field {path}.{f.name} is missing")
            _compatible(actual.field(f.name).type, f.type, f"{path}.{f.name}")
        for f in actual:
            if f.name not in [e.name for e in expected]:
                raise ShapeError(f"table field {path}.{f.name} is missing from the schema")
        return
    if pa.types.is_list(expected) and (pa.types.is_list(actual) or pa.types.is_large_list(actual)):
        return _compatible(actual.value_type, expected.value_type, f"{path}[]")
    if pa.types.is_map(expected) and pa.types.is_map(actual):
        _compatible(actual.key_type, expected.key_type, f"{path}.key")
        return _compatible(actual.item_type, expected.item_type, f"{path}.value")
    if actual == expected or pa.types.is_null(actual):
        return
    if pa.types.is_integer(actual) and pa.types.is_integer(expected):
        if (
            pa.types.is_signed_integer(actual) == pa.types.is_signed_integer(expected)
            and actual.bit_width <= expected.bit_width
        ):
            return
    raise ShapeError(f"schema mismatch at field {path!r}: {actual} cannot be encoded as {expected}")


def _avro_arrow(doc: dict[str, Any]) -> pa.Schema:
    named: dict[str, Any] = dict(doc.get("__named_schemas", {}))
    active: set[str] = set()

    def typ(node: Any) -> tuple[pa.DataType, bool]:
        nullable = isinstance(node, list) and "null" in node
        if isinstance(node, list):
            members = [v for v in node if v != "null"]
            if len(members) != 1:
                raise ShapeError("Avro unions require one non-null type")
            return typ(members[0])[0], nullable
        if isinstance(node, str):
            scalars = {
                "null": pa.null(),
                "boolean": pa.bool_(),
                "int": pa.int32(),
                "long": pa.int64(),
                "float": pa.float32(),
                "double": pa.float64(),
                "string": pa.string(),
                "bytes": pa.binary(),
            }
            if node in scalars:
                return scalars[node], node == "null"
            if node in active:
                raise ShapeError("recursive Avro records cannot form an Arrow schema")
            return typ(named[node])
        kind = node["type"]
        logical = node.get("logicalType")
        if logical == "decimal":
            return pa.decimal128(node["precision"], node.get("scale", 0)), False
        if logical == "date":
            return pa.date32(), False
        if logical in ("time-millis", "time-micros"):
            return (pa.time32("ms") if logical == "time-millis" else pa.time64("us")), False
        if logical and "timestamp-" in logical:
            unit = "ms" if logical.endswith("millis") else "us"
            return pa.timestamp(unit, None if logical.startswith("local-") else "UTC"), False
        if kind == "record":
            name = node["name"]
            named[name] = node
            active.add(name)
            fields = []
            for f in node["fields"]:
                t, null = typ(f["type"])
                fields.append(pa.field(f["name"], t, nullable=null))
            active.remove(name)
            return pa.struct(fields), False
        if kind == "array":
            t, null = typ(node["items"])
            return pa.list_(pa.field("item", t, nullable=null)), False
        if kind == "map":
            t, null = typ(node["values"])
            return pa.map_(pa.string(), pa.field("value", t, nullable=null)), False
        if kind == "enum":
            return pa.string(), False
        if kind == "fixed":
            return pa.binary(node["size"]), False
        return typ(kind)

    root, _ = typ(doc)
    if not pa.types.is_struct(root):
        raise ShapeError("Avro event schema must be a record")
    return pa.schema(list(root))


def _proto_class(text: str, message: str | None) -> Any:
    _need("google.protobuf")
    from google.protobuf import (  # type: ignore[import-untyped]
        descriptor_pb2,
        descriptor_pool,
        message_factory,
    )

    from shape.importers.protobuf import parse_proto

    proto = parse_proto(text, "<event schema>")
    if proto.imports:
        raise ShapeError("Protobuf event schemas must declare their message types in one file")
    types = descriptor_pb2.FieldDescriptorProto
    scalars = {
        n.lower().removeprefix("type_"): v
        for n, v in types.Type.items()
        if n not in ("TYPE_MESSAGE", "TYPE_GROUP", "TYPE_ENUM")
    }
    file = descriptor_pb2.FileDescriptorProto(
        name="events.proto", package=proto.package, syntax="proto3"
    )
    names: dict[str, str] = {}

    def register(messages: Any) -> None:
        for msg in messages:
            names[msg.full] = "message"
            for e in msg.enums:
                names[e.full] = "enum"
            register(msg.nested)

    register(proto.messages)
    for e in proto.enums:
        names[e.full] = "enum"

    def enum(target: Any, e: Any) -> None:
        item = target.add(name=e.name)
        for i, name in enumerate(e.values):
            item.value.add(name=name, number=e.numbers[i])

    def field(
        target: Any, src: Any, scope: str, *, name: str | None = None, number: int | None = None
    ) -> Any:
        dst = target.add(
            name=name or src.name, number=number or src.number, label=types.LABEL_OPTIONAL
        )
        if src.type in scalars:
            dst.type = scalars[src.type]
        else:
            parts = scope.split(".")
            candidates = (
                [src.type.lstrip(".")]
                if src.type.startswith(".")
                else [".".join(parts[:i] + [src.type]) for i in range(len(parts), -1, -1)]
            )
            full = next((n for n in candidates if n in names), None)
            if full is None:
                raise ShapeError(
                    f"unsupported or unknown Protobuf field {scope}.{src.name}: {src.type}"
                )
            dst.type = types.TYPE_MESSAGE if names[full] == "message" else types.TYPE_ENUM
            dst.type_name = "." + full
        return dst

    def build(target: Any, src: Any) -> None:
        msg = target.add(name=src.name)
        for child in src.nested:
            build(msg.nested_type, child)
        for e in src.enums:
            enum(msg.enum_type, e)
        for f in src.fields:
            if f.key_type is not None:
                entry_name = "".join(part.capitalize() for part in f.name.split("_")) + "Entry"
                entry = msg.nested_type.add(name=entry_name)
                entry.options.map_entry = True
                from shape.importers.protobuf import PField

                field(entry.field, PField("key", f.key_type, 1, f.line), src.full)
                field(entry.field, f, src.full, name="value", number=2)
                msg.field.add(
                    name=f.name,
                    number=f.number,
                    label=types.LABEL_REPEATED,
                    type=types.TYPE_MESSAGE,
                    type_name=f".{src.full}.{entry_name}",
                )
            else:
                dst = field(msg.field, f, src.full)
                if f.label == "repeated":
                    dst.label = types.LABEL_REPEATED
                elif f.label == "optional" and dst.type != types.TYPE_MESSAGE:
                    dst.proto3_optional = True
                    dst.oneof_index = len(msg.oneof_decl)
                    msg.oneof_decl.add(name="_" + f.name)
                if f.oneof is not None:
                    raise ShapeError(f"Protobuf oneof field {src.full}.{f.name} is unsupported")

    for src in proto.messages:
        build(file.message_type, src)
    for e in proto.enums:
        enum(file.enum_type, e)
    matches = [
        n
        for n, k in names.items()
        if k == "message" and (n == message or n.rsplit(".", 1)[-1] == message)
    ]
    if len(matches) != 1:
        raise ShapeError("protobuf needs an unambiguous message name")
    pool = descriptor_pool.DescriptorPool()
    try:
        pool.Add(file)
        return message_factory.GetMessageClass(pool.FindMessageTypeByName(matches[0]))
    except (TypeError, ValueError) as exc:
        raise ShapeError("invalid Protobuf event schema") from exc


def _proto_arrow(desc: Any, active: frozenset[str] = frozenset()) -> pa.Schema:
    if desc.full_name in active:
        raise ShapeError("recursive Protobuf messages cannot form an Arrow schema")
    active = active | {desc.full_name}
    scalar = {
        1: pa.float64(),
        2: pa.float32(),
        3: pa.int64(),
        4: pa.uint64(),
        5: pa.int32(),
        6: pa.uint64(),
        7: pa.uint32(),
        8: pa.bool_(),
        9: pa.string(),
        12: pa.binary(),
        13: pa.uint32(),
        14: pa.string(),
        15: pa.int32(),
        16: pa.int64(),
        17: pa.int32(),
        18: pa.int64(),
    }

    def typ(f: Any) -> pa.DataType:
        return (
            pa.struct(list(_proto_arrow(f.message_type, active)))
            if f.type == 11
            else scalar[f.type]
        )

    fields = []
    for f in desc.fields:
        if f.type == 11 and f.message_type.GetOptions().map_entry:
            t = pa.map_(typ(f.message_type.fields[0]), typ(f.message_type.fields[1]))
        else:
            t = typ(f)
            if f.is_repeated:
                t = pa.list_(t)
        fields.append(pa.field(f.name, t))
    return pa.schema(fields)


def _fill_proto(msg: Any, row: Mapping[str, Any]) -> None:
    for f in msg.DESCRIPTOR.fields:
        value = row.get(f.name)
        if value is None:
            continue
        target = getattr(msg, f.name)
        if f.type == 11 and f.message_type.GetOptions().map_entry:
            child = f.message_type.fields[1].type == 11
            for k, v in value.items():
                if child:
                    _fill_proto(target[k], v)
                else:
                    target[k] = v
        elif f.is_repeated:
            if f.type == 11:
                for v in value:
                    _fill_proto(target.add(), v)
            else:
                target.extend(value)
        elif f.type == 11:
            _fill_proto(target, value)
            target.SetInParent()
        else:
            setattr(msg, f.name, value)


def _proto_value(field: Any, value: Any) -> Any:
    if field.type == 11:
        return _read_proto(value)
    if field.type == 14:
        member = field.enum_type.values_by_number.get(value)
        return member.name if member is not None else str(value)
    return value


def _read_proto(msg: Any) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for f in msg.DESCRIPTOR.fields:
        value = getattr(msg, f.name)
        if f.type == 11 and f.message_type.GetOptions().map_entry:
            member = f.message_type.fields[1]
            row[f.name] = {k: _proto_value(member, v) for k, v in value.items()}
        elif f.is_repeated:
            row[f.name] = [_proto_value(f, v) for v in value]
        elif f.has_presence and not msg.HasField(f.name):
            row[f.name] = None
        else:
            row[f.name] = _proto_value(f, value)
    return row


def _promote_schema(current: pa.Schema | None, writer: pa.Schema) -> pa.Schema:
    if current is None:
        return writer
    try:
        return pa.unify_schemas([current, writer], promote_options="permissive")
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        return writer


class RegistryAvroDecoder:
    """Resolve a Confluent Avro schema per message id, once per read."""

    def __init__(self, resolve: Callable[[int], str]) -> None:
        self.resolve = resolve
        self.cache: dict[int, BinaryCodec] = {}
        self.schema: pa.Schema | None = None

    def __call__(self, body: bytes | str) -> dict[str, Any]:
        if not isinstance(body, bytes) or len(body) < 5 or body[0] != 0:
            raise ValueError("invalid Avro registry prefix")
        sid = int.from_bytes(body[1:5], "big")
        if sid not in self.cache:
            self.cache[sid] = BinaryCodec("avro", self.resolve(sid))
        self.schema = _promote_schema(self.schema, self.cache[sid].schema)
        return self.cache[sid].decode(body[5:])


class AzureAvroCodec:
    """Adapter for the Azure AvroEncoder dictionary message protocol.

    The SDK caches schema resolution by id from ``avro/binary+<id>`` content types.
    """

    def __init__(
        self,
        encoder: Any,
        schema_text: str | None = None,
        resolve: Callable[[str], str] | None = None,
    ) -> None:
        self.encoder, self.schema_text = encoder, schema_text
        self.resolve = resolve
        self.cache: dict[str, BinaryCodec] = {}
        self.schema = BinaryCodec("avro", schema_text).schema if schema_text else None

    def __call__(self, body: bytes | str) -> dict[str, Any]:
        raise ValueError("Azure Avro needs the message content type")

    def decode_message(self, message: Any) -> dict[str, Any]:
        return self.decode(message.value, message.content_type or "")

    def encode(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.encoder.encode(dict(row), schema=self.schema_text))

    def decode(self, body: bytes, content_type: str) -> dict[str, Any]:
        if not content_type.startswith("avro/binary+") or not content_type.removeprefix(
            "avro/binary+"
        ):
            raise ValueError("invalid Azure Avro content type")
        schema_id = content_type.removeprefix("avro/binary+")
        if self.resolve is not None and schema_id not in self.cache:
            self.cache[schema_id] = BinaryCodec("avro", self.resolve(schema_id))
        if schema_id in self.cache:
            self.schema = _promote_schema(self.schema, self.cache[schema_id].schema)
        try:
            return dict(self.encoder.decode({"content": body, "content_type": content_type}))
        except Exception as exc:
            raise ValueError("invalid Azure Avro message") from exc


def source_decoder(
    options: dict[str, Any], *, registry_resolve: Callable[[int], str] | None = None
) -> Any:
    """Consume common local binary source options before the broker is opened."""
    fmt = options.pop("format", "json")
    schema_file = options.pop("schema_file", None)
    message = options.pop("message", None)
    if fmt == "json":
        if schema_file is not None or message is not None:
            raise ShapeError("schema_file and message require a binary format")
        return None
    if fmt == "avro" and registry_resolve is not None:
        if schema_file:
            raise ShapeError("choose schema_file or schema_registry_url")
        return RegistryAvroDecoder(registry_resolve)
    if fmt not in ("avro", "protobuf") or not schema_file:
        raise ShapeError("format must be json, avro or protobuf; binary formats need schema_file")
    codec = BinaryCodec.from_file(fmt, schema_file, message=message)
    return codec.decode


def schema_groups(messages: list[Any], decoder: Any, schema: pa.Schema | None) -> list[list[Any]]:
    """Keep unpinned writer ids in separate batches, with per-batch offsets."""
    if schema is not None or not isinstance(decoder, RegistryAvroDecoder | AzureAvroCodec):
        return [messages]
    groups: list[list[Any]] = []
    previous: Any = None
    for message in messages:
        key = (
            message.content_type
            if isinstance(decoder, AzureAvroCodec)
            else (message.value[:5] if isinstance(message.value, bytes) else None)
        )
        if not groups or key != previous:
            groups.append([])
        groups[-1].append(message)
        previous = key
    return groups
