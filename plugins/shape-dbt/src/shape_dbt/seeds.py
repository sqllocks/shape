"""The dbt seeds sink: generated tables as CSV files in a dbt project's ``seeds/`` folder.

``shape.sinks`` ``dbt-seeds`` writes ``<project>/seeds/<table>.csv`` and adds the table to
``<project>/seeds/_shape_seeds.yml``, a ``seeds:`` block whose ``config.column_types`` states the
type of every column. The block is what keeps an identifier that has leading zeros (a ZIP code,
an NDC, a member number) as text: without it, dbt reads ``02134`` as the number 2134.

Seeds are for small, static files (dev, CI, demos). dbt itself stops comparing the *content* of a
seed larger than 1 MiB (``state:modified`` sees only its path), and loading is one ``INSERT`` per
batch through the adapter, so the sink refuses a table whose file is larger than ``max_bytes``
(1 MiB by default) unless ``allow_large`` is set. Volumes beyond that need a real sink (Delta,
Parquet, a database); see ``docs/DBT.md``.

    shape dbt-seeds retail.gen.json --project ./my_dbt_project --dialect duckdb
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]

SEEDS_FILE = "_shape_seeds.yml"
DEFAULT_MAX_BYTES = 1024 * 1024  # dbt's own limit for comparing a seed's content (state:modified)

# Logical column type -> the SQL type of ``column_types``, per dialect.
DIALECTS: dict[str, dict[str, str]] = {
    "ansi": {
        "integer": "bigint",
        "float": "double precision",
        "decimal": "numeric",
        "string": "varchar",
        "boolean": "boolean",
        "date": "date",
        "timestamp": "timestamp",
        "time": "time",
        "uuid": "varchar",
    },
    "duckdb": {
        "integer": "bigint",
        "float": "double",
        "decimal": "decimal",
        "string": "varchar",
        "boolean": "boolean",
        "date": "date",
        "timestamp": "timestamp",
        "time": "time",
        "uuid": "varchar",
    },
    "tsql": {  # Fabric Data Warehouse, SQL Server, Azure SQL
        "integer": "bigint",
        "float": "float",
        "decimal": "decimal",
        "string": "varchar",
        "boolean": "bit",
        "date": "date",
        "timestamp": "datetime2(6)",
        "time": "time(6)",
        "uuid": "varchar(36)",
    },
    "spark": {  # Fabric Lakehouse (dbt-fabricspark)
        "integer": "bigint",
        "float": "double",
        "decimal": "decimal",
        "string": "string",
        "boolean": "boolean",
        "date": "date",
        "timestamp": "timestamp",
        "time": "string",
        "uuid": "string",
    },
}
_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SeedError(ValueError):
    """A table cannot be written as a seed."""


def _logical(t: pa.DataType) -> str:
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_decimal(t):
        return "decimal"
    if pa.types.is_floating(t):
        return "float"
    if pa.types.is_timestamp(t):
        return "timestamp"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_time(t):
        return "time"
    return "string"


def column_type(
    arrow_type: pa.DataType, info: Mapping[str, Any] | None = None, *, dialect: str = "ansi"
) -> str:
    """The SQL type to declare for a column: from the schema's column (``info``: ``type``,
    ``precision``, ``scale``, ``max_length``) when it gives one, else from the Arrow type. Text
    is always ``varchar`` (never inferred as a number), a decimal keeps its precision and scale."""
    try:
        names = DIALECTS[dialect]
    except KeyError:
        raise SeedError(f"unknown dialect {dialect!r}; choose one of {', '.join(DIALECTS)}") from None
    info = info or {}
    declared = str(info.get("type") or "").lower()
    if declared in ("decimal", "numeric") and info.get("precision"):
        scale = int(info.get("scale") or 0)
        return f"{names['decimal']}({int(info['precision'])},{scale})"
    if pa.types.is_decimal(arrow_type):
        return f"{names['decimal']}({arrow_type.precision},{arrow_type.scale})"
    logical = _logical(arrow_type)
    if declared in ("decimal", "numeric") and logical == "float":
        return names["float"]
    if logical == "string":
        base = names["string"]
        length = info.get("max_length")
        if length and base == "varchar":
            return f"varchar({int(length)})" if dialect == "tsql" else "varchar"
        return base
    return names[logical]


def _is_date(info: Mapping[str, Any]) -> bool:
    return str(info.get("type") or "").lower() == "date"


def _prepare(batch: pa.RecordBatch, columns: Mapping[str, Any]) -> pa.RecordBatch:
    """Round a double to the declared scale of its column (so ``12.340000000000001`` is not
    written for a ``numeric(10,2)``) and cut a timestamp to a date where the column is a date."""
    arrays = []
    fields = []
    for field, arr in zip(batch.schema, batch.columns, strict=True):
        info = columns.get(field.name) or {}
        scale = info.get("scale")
        if scale is not None and pa.types.is_floating(field.type):
            arr = pc.round(arr, ndigits=int(scale))
        elif _is_date(info) and pa.types.is_timestamp(field.type):
            arr = arr.cast(pa.date32())
        arrays.append(arr)
        fields.append(pa.field(field.name, arr.type, field.nullable))
    return pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))


def _local(uri: str) -> Path:
    """``dbt:///abs/project`` and ``dbt://relative/project`` name a directory; so does a path."""
    return Path(uri[len("dbt://") :] if uri.startswith("dbt://") else uri)


def _update_properties(
    path: Path, name: str, types: Mapping[str, str], description: str, column_docs: Mapping[str, str]
) -> None:
    import yaml

    doc: dict[str, Any] = {"version": 2, "seeds": []}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            doc = {**loaded, "version": 2, "seeds": list(loaded.get("seeds") or [])}
    entry: dict[str, Any] = {"name": name}
    if description:
        entry["description"] = description
    entry["config"] = {"column_types": dict(types)}
    docs = [{"name": c, "description": d} for c, d in column_docs.items() if d]
    if docs:
        entry["columns"] = docs
    doc["seeds"] = [s for s in doc["seeds"] if s.get("name") != name] + [entry]
    doc["seeds"].sort(key=lambda s: str(s.get("name")))
    header = "# Generated by the Shape dbt seeds sink: the types keep identifiers as text.\n"
    text = header + yaml.safe_dump(doc, sort_keys=False, width=100)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".seeds-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class DbtSeedsSink:
    """``shape.sinks`` ``dbt-seeds``: ``uri`` is a dbt project directory (a path or ``dbt://``).

    Options: ``seeds_dir`` (``seeds``), ``columns`` (``{column: {type, precision, scale,
    max_length}}`` from the generation schema), ``dialect`` (``ansi``, ``duckdb``, ``tsql`` or
    ``spark``), ``max_bytes`` (1 MiB), ``allow_large``, ``description``, ``column_descriptions`` and
    ``schema`` (the Arrow schema, for a table with no rows).
    """

    name = "dbt-seeds"
    schemes = ("dbt",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        if not _SAFE_NAME.match(table):
            raise SeedError(f"{table!r} is not a valid seed name (letters, digits and _)")
        project = _local(uri)
        if not project.is_dir():
            raise SeedError(f"{project} is not a directory: give the dbt project's root")
        seeds = (project / str(options.get("seeds_dir", "seeds"))).resolve()
        if not seeds.is_relative_to(project.resolve()):
            raise SeedError("seeds_dir leaves the dbt project")
        seeds.mkdir(parents=True, exist_ok=True)
        columns: Mapping[str, Any] = options.get("columns") or {}
        dialect = str(options.get("dialect", "ansi"))
        limit = int(options.get("max_bytes", DEFAULT_MAX_BYTES))
        target = seeds / f"{table}.csv"
        fd, tmp_name = tempfile.mkstemp(dir=seeds, prefix=f".{table}-", suffix=".tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        rows = 0
        schema: pa.Schema | None = None
        try:
            writer: Any = None
            try:
                for batch in batches:
                    prepared = _prepare(batch, columns)
                    if writer is None:
                        schema = prepared.schema
                        writer = pacsv.CSVWriter(str(tmp), schema)
                    writer.write_batch(prepared)
                    rows += batch.num_rows
                    if not options.get("allow_large") and tmp.stat().st_size > limit:
                        break
                if writer is None and options.get("schema") is not None:  # no rows: the header
                    schema = options["schema"]
                    writer = pacsv.CSVWriter(str(tmp), schema)
            finally:
                if writer is not None:
                    writer.close()
            if schema is None:
                raise SeedError(f"{table}: no batches, so no columns to declare")
            size = tmp.stat().st_size
            if not options.get("allow_large") and size > limit:
                raise SeedError(
                    f"{table}: the seed file is larger than {limit:,} bytes "
                    f"(dbt compares a seed's content only up to 1 MiB, and loads it through "
                    f"INSERT statements). Generate fewer rows, write Parquet or Delta and "
                    f"load it as a source, or pass allow_large / --allow-large"
                )
            os.replace(tmp, target)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        types = {
            f.name: column_type(f.type, columns.get(f.name), dialect=dialect) for f in schema
        }
        _update_properties(
            seeds / SEEDS_FILE,
            table,
            types,
            str(options.get("description") or ""),
            options.get("column_descriptions") or {},
        )
        return rows
