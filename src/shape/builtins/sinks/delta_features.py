"""Opt-in Delta writer features, using the supported delta-rs transaction APIs.

The dependency floor is unchanged. Capabilities are checked against public APIs;
1.6.6 is the verified version, not an asserted minimum for earlier releases.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from tempfile import TemporaryDirectory
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

FEATURE_OPTIONS = frozenset(
    {
        "table_properties",
        "constraints",
        "column_mapping",
        "deletion_vectors",
        "generated_columns",
        "timestamp_ntz",
    }
)
_RESERVED = (
    "delta.columnMapping.",
    "delta.constraints.",
    "delta.feature.",
    "delta.minReaderVersion",
    "delta.minWriterVersion",
    "delta.enableDeletionVectors",
    "shape.fingerprint",
)
_VERIFIED_VERSION = "1.6.6"


def _object(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError(f"{name} must be a JSON object") from None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _boolean(value: Any, name: str) -> bool:
    if value in ("true", "false") and isinstance(value, str):
        return value == "true"
    if type(value) is not bool:
        raise ValueError(f"{name} must be true or false")
    return bool(value)


def _expressions(value: Any, name: str) -> dict[str, str]:
    obj = _object(value, name)
    if any(
        not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
        for k, v in obj.items()
    ):
        raise ValueError(f"{name} needs nonempty names and SQL expressions")
    return dict(obj)


@dataclass
class Features:
    properties: dict[str, str] = field(default_factory=dict)
    constraints: dict[str, str] = field(default_factory=dict)
    generated: dict[str, str] = field(default_factory=dict)
    mapping: bool = False
    vectors: bool = False
    ntz: bool = False
    derive_constraints: bool = False
    not_null: list[str] = field(default_factory=list)

    def schema(self, schema: pa.Schema) -> pa.Schema:
        for name in self.not_null:
            if name not in schema.names:
                raise ValueError(f"constraint not_null_{name}: column does not exist")
            escaped = name.replace('"', '""')
            self.constraints[f"not_null_{name}"] = f'"{escaped}" IS NOT NULL'
        if self.derive_constraints:
            for f in schema:
                if not f.nullable:
                    escaped = f.name.replace('"', '""')
                    self.constraints[f"not_null_{f.name}"] = f'"{escaped}" IS NOT NULL'
        for name in self.generated:
            if name not in schema.names:
                raise ValueError(f"generated_columns: column {name!r} does not exist")
        return pa.schema(
            [
                f.with_metadata(
                    {
                        **(f.metadata or {}),
                        b"delta.generationExpression": self.generated[f.name].encode(),
                    }
                )
                if f.name in self.generated
                else f
                for f in schema
            ],
            metadata=schema.metadata,
        )

    @property
    def active(self) -> bool:
        return bool(
            self.properties
            or self.constraints
            or self.generated
            or self.mapping
            or self.vectors
            or self.ntz
            or self.derive_constraints
            or self.not_null
        )

    def configuration(self) -> dict[str, str]:
        out = dict(self.properties)
        if self.mapping:
            out["delta.columnMapping.mode"] = "name"
        if self.vectors:
            out["delta.enableDeletionVectors"] = "true"
        return out


def parse(options: dict[str, Any]) -> Features:
    f = Features()
    if "table_properties" in options:
        props = _object(options["table_properties"], "table_properties")
        for key, value in props.items():
            if not isinstance(key, str) or not key or not isinstance(value, str):
                raise ValueError("table_properties need nonempty string keys and string values")
            if key == "delta.appendOnly" and value not in ("true", "false"):
                raise ValueError("table_properties delta.appendOnly must be true or false")
            if any(key.startswith(p) for p in _RESERVED):
                raise ValueError(
                    "table_properties cannot set reserved writer feature or fingerprint properties"
                )
        f.properties = dict(props)
    if "constraints" in options:
        raw = options["constraints"]
        if raw is True or raw == "true":
            f.derive_constraints = True
        elif raw is False or raw == "false":
            pass
        else:
            c = _object(raw, "constraints")
            if set(c) - {"not_null", "check"}:
                raise ValueError("constraints take not_null and check only")
            nn = c.get("not_null", [])
            if not isinstance(nn, list) or any(not isinstance(n, str) or not n for n in nn):
                raise ValueError("constraint not_null must be a list of column names")
            f.not_null = list(nn)
            f.constraints = _expressions(c.get("check", {}), "constraint check")
    if "column_mapping" in options:
        if options["column_mapping"] not in ("name", "none"):
            raise ValueError("column_mapping must be name or none")
        f.mapping = options["column_mapping"] == "name"
    if "deletion_vectors" in options:
        f.vectors = _boolean(options["deletion_vectors"], "deletion_vectors")
    if "timestamp_ntz" in options:
        f.ntz = _boolean(options["timestamp_ntz"], "timestamp_ntz")
    if "generated_columns" in options:
        f.generated = _expressions(options["generated_columns"], "generated_columns")
    if options.get("mode", "overwrite") not in ("overwrite", "append"):
        raise ValueError("Delta mode must be overwrite or append")
    return f


def _has_parameter(call: Any, parameter: str) -> bool:
    if not callable(call):
        return False
    try:
        return parameter in inspect.signature(call).parameters
    except (TypeError, ValueError):
        return False


def require_capabilities(dl: Any, options: dict[str, Any], features: Features) -> None:
    """Refuse an unavailable public API before a directory, table or commit is created."""
    table = getattr(dl, "DeltaTable", None)
    alter = getattr(getattr(dl, "table", None), "TableAlterer", None)
    enums = getattr(dl, "TableFeatures", None)
    checks = {
        "table_properties": _has_parameter(
            getattr(table, "create", None), "raise_if_key_not_exists"
        )
        and _has_parameter(getattr(alter, "set_table_properties", None), "raise_if_not_exists"),
        "constraints": callable(getattr(alter, "add_constraint", None))
        and callable(getattr(dl, "QueryBuilder", None)),
        "column_mapping": hasattr(enums, "ColumnMapping")
        and _has_parameter(getattr(table, "create", None), "configuration")
        and _has_parameter(getattr(alter, "add_feature", None), "allow_protocol_versions_increase"),
        "deletion_vectors": hasattr(enums, "DeletionVectors")
        and _has_parameter(getattr(table, "create", None), "configuration"),
        "generated_columns": hasattr(enums, "GeneratedColumns")
        and _has_parameter(getattr(dl, "write_deltalake", None), "schema_mode"),
        "timestamp_ntz": hasattr(enums, "TimestampWithoutTimezone")
        and _has_parameter(getattr(dl, "write_deltalake", None), "schema_mode")
        and _has_parameter(getattr(alter, "add_feature", None), "allow_protocol_versions_increase"),
    }
    active = {
        "table_properties": bool(features.properties),
        "constraints": bool(
            features.constraints or features.not_null or features.derive_constraints
        ),
        "column_mapping": features.mapping,
        "deletion_vectors": features.vectors,
        "generated_columns": bool(features.generated),
        "timestamp_ntz": features.ntz,
    }
    for name in options:
        if active.get(name) and not checks[name]:
            raise ValueError(
                f"{name}: installed deltalake {getattr(dl, '__version__', 'unknown')} "
                "lacks the required public API; use a release providing that API "
                f"(verified deltalake {_VERIFIED_VERSION})"
            )


def feature_plan(options: dict[str, Any]) -> dict[str, Any]:
    """Minimum protocol and feature union, without importing deltalake or accessing storage."""
    f = parse(options)
    reader, writer = 1, 2
    rf: set[str] = set()
    wf: set[str] = set()
    if f.constraints or f.derive_constraints or f.not_null:
        writer = 3
        wf.add("checkConstraints")
    if f.generated:
        writer = max(writer, 4)
        wf.add("generatedColumns")
    if f.mapping:
        reader, writer = 2, max(writer, 5)
        rf.add("columnMapping")
        wf.add("columnMapping")
    if f.vectors or f.ntz:
        reader, writer = 3, 7
    if f.vectors:
        rf.add("deletionVectors")
        wf.add("deletionVectors")
    if f.ntz:
        rf.add("timestampNtz")
        wf.add("timestampNtz")
    return {
        "min_reader_version": reader,
        "min_writer_version": writer,
        "reader_features": sorted(rf),
        "writer_features": sorted(wf),
    }


def existing_table(dl: Any, location: str, storage: dict[str, str] | None) -> Any:
    try:
        return dl.DeltaTable(location, storage_options=storage)
    except getattr(getattr(dl, "exceptions", None), "TableNotFoundError", FileNotFoundError):
        return None
    except Exception:
        raise ValueError("could not inspect the Delta table; check the path and sign-in") from None


def persisted_rules(table: Any) -> tuple[dict[str, str], dict[str, str]]:
    if table is None:
        return {}, {}
    constraints = {
        k.removeprefix("delta.constraints."): v
        for k, v in table.metadata().configuration.items()
        if k.startswith("delta.constraints.")
    }
    fields = json.loads(table.schema().to_json())["fields"]
    generated = {
        f["name"]: f["metadata"]["delta.generationExpression"]
        for f in fields
        if "delta.generationExpression" in f.get("metadata", {})
    }
    return constraints, generated


def preflight(
    dl: Any,
    batches: list[pa.RecordBatch],
    schema: pa.Schema,
    constraints: dict[str, str],
    existing: Any,
    generated: dict[str, str],
) -> dict[str, str]:
    """Validate requested checks against existing and incoming rows before destination writes."""
    if not constraints and not generated:
        return {}
    with TemporaryDirectory(prefix="shape-delta-constraints-") as scratch:
        incoming = pa.Table.from_batches(batches, schema=schema)
        if existing is not None:
            old = pa.table(
                dl.QueryBuilder().register("t", existing).execute("SELECT * FROM t").read_all()
            )
            incoming = pa.concat_tables([old.cast(schema), incoming])
        try:
            dl.write_deltalake(scratch, incoming)
        except Exception:
            if generated:
                raise ValueError(
                    f"generated_columns {', '.join(sorted(generated))}: "
                    "invalid expression or mismatching batch"
                ) from None
            raise
        staged = dl.DeltaTable(scratch)
        for name, expression in constraints.items():
            try:
                staged.alter.add_constraint({name: expression})
            except Exception:
                raise ValueError(
                    f"constraint {name!r} failed validation or has an invalid SQL expression"
                ) from None
        configuration = staged.metadata().configuration
        return {name: configuration["delta.constraints." + name] for name in constraints}


def _add_feature(dl: Any, table: Any, feature: Any, name: str) -> None:
    try:
        table.alter.add_feature(feature, allow_protocol_versions_increase=True)
    except Exception:
        raise ValueError(
            f"{name}: installed deltalake {getattr(dl, '__version__', 'unknown')} "
            "could not commit the required feature API "
            f"(verified deltalake {_VERIFIED_VERSION})"
        ) from None


def _explicit_mapping(dl: Any, table: Any) -> None:
    protocol = table.protocol()
    if (
        protocol.min_reader_version >= 3
        and table.metadata().configuration.get("delta.columnMapping.mode") == "name"
        and "columnMapping" not in (protocol.reader_features or [])
    ):
        _add_feature(dl, table, dl.TableFeatures.ColumnMapping, "column_mapping")


def write_features(
    dl: Any,
    location: str,
    batches: Iterable[pa.RecordBatch],
    schema: pa.Schema,
    options: dict[str, Any],
    storage: dict[str, str] | None,
) -> None:
    features = parse(options)
    require_capabilities(dl, options, features)
    schema = features.schema(schema)
    prior = existing_table(dl, location, storage)
    old_constraints, old_generated = persisted_rules(prior)
    mode = options.get("mode", "overwrite")
    if (
        features.mapping
        and prior is not None
        and prior.metadata().configuration.get("delta.columnMapping.mode", "none") != "name"
    ):
        raise ValueError(
            "column_mapping name needs a new table; existing column_mapping mode is none"
        )
    constraints = {**old_constraints, **features.constraints}
    generated = {**old_generated, **features.generated}
    for name, expression in features.generated.items():
        if name in old_generated and expression != old_generated[name]:
            raise ValueError(
                f"generated_columns {name!r}: changing an expression is table maintenance"
            )
    if generated:
        for name in generated:
            if name not in schema.names:
                raise ValueError(f"generated_columns {name!r}: column is missing")
        schema = pa.schema(
            [
                f.with_metadata(
                    {
                        **(f.metadata or {}),
                        b"delta.generationExpression": generated[f.name].encode(),
                    }
                )
                if f.name in generated
                else f
                for f in schema
            ],
            metadata=schema.metadata,
        )
    # Feature validation is deliberately eager: no destination is mutated before all
    # requested checks pass, even when they would be added after the first commit.
    try:
        kept = [b.cast(schema) for b in batches]
    except Exception:
        labels = []
        if constraints:
            labels.append(f"constraint {', '.join(sorted(constraints))}")
        if generated:
            labels.append(f"generated_columns {', '.join(sorted(generated))}")
        context = "; " + "; ".join(labels) if labels else ""
        raise ValueError(f"Delta batch schema validation failed{context}") from None
    try:
        features.constraints = preflight(
            dl,
            kept,
            schema,
            features.constraints,
            prior if mode == "append" and features.constraints else None,
            generated,
        )
    except ValueError:
        raise
    except Exception:
        if generated:
            raise ValueError(
                f"generated_columns {', '.join(sorted(generated))}: "
                "invalid expression or mismatching batch"
            ) from None
        raise ValueError("constraint validation could not read the input") from None
    for name, expression in features.constraints.items():
        if name in old_constraints and expression != old_constraints[name]:
            raise ValueError(
                f"constraint {name!r}: changing an existing expression is table maintenance"
            )
    configuration = features.configuration()
    if configuration:
        with TemporaryDirectory(prefix="shape-delta-properties-") as scratch:
            try:
                staged_configuration = dl.DeltaTable.create(
                    scratch, schema, configuration=configuration, raise_if_key_not_exists=False
                )
                _explicit_mapping(dl, staged_configuration)
            except Exception:
                raise ValueError(
                    "table_properties or writer feature configuration is invalid"
                ) from None
    creating = prior is None
    if creating and configuration:
        try:
            prior = dl.DeltaTable.create(
                location,
                schema,
                partition_by=options.get("partition_by") or None,
                configuration=configuration,
                storage_options=storage,
                raise_if_key_not_exists=False,
            )
        except Exception:
            raise ValueError(
                "Delta table creation failed; check the schema, path and sign-in"
            ) from None
    if creating and configuration:
        _explicit_mapping(dl, prior)
    try:
        dl.write_deltalake(
            location,
            pa.RecordBatchReader.from_batches(schema, kept),
            mode="append" if creating and configuration else mode,
            partition_by=options.get("partition_by") or None,
            storage_options=storage,
            **(
                {"schema_mode": "overwrite"}
                if mode == "overwrite" and not (creating and configuration)
                else {}
            ),
        )
    except Exception:
        if generated:
            raise ValueError(
                f"generated_columns {', '.join(sorted(generated))}: "
                "invalid expression or mismatching batch"
            ) from None
        if constraints:
            raise ValueError(
                f"constraint {', '.join(sorted(constraints))}: batch rejected"
            ) from None
        raise ValueError(
            "Delta feature write failed; check the input schema and writer capabilities"
        ) from None
    table = existing_table(dl, location, storage)
    if table is None:
        raise ValueError("the Delta writer did not publish the table")
    missing = {
        k: v
        for k, v in features.constraints.items()
        if table.metadata().configuration.get("delta.constraints." + k) != v
    }
    if missing:
        try:
            table.alter.add_constraint(missing)
        except Exception:
            raise ValueError(
                f"constraint {', '.join(sorted(missing))}: could not commit validated checks"
            ) from None
    if not creating and configuration:
        changed = {
            k: v for k, v in configuration.items() if table.metadata().configuration.get(k) != v
        }
        if changed:
            try:
                table.alter.set_table_properties(changed, raise_if_not_exists=False)
            except Exception:
                raise ValueError(
                    "table_properties could not be committed; check writer capabilities"
                ) from None
    if features.ntz and "timestampNtz" not in (table.protocol().reader_features or []):
        _add_feature(dl, table, dl.TableFeatures.TimestampWithoutTimezone, "timestamp_ntz")
    _explicit_mapping(dl, table)
