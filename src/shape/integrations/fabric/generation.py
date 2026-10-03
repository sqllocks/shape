"""Generation inside pipelines: the helpers behind the generate notebooks, the ``generateSample``
User Data Function and the container script.

Everything here runs on the generation engine (``shape.generation``), so a domain generated in
a Fabric notebook, a Synapse Spark pool, an ADF Batch node or a User Data Function is the same
data as ``shape generate`` writes. The module needs only numpy and pyarrow; ``deltalake`` is
imported when Delta tables are written and pandas by :func:`sample_to_pandas`.

* :func:`generate_domain` runs a domain at a scale preset, with a seed and optional row counts.
* :func:`domain_contract` is the contract (``shape.check``, contract format v1) that a domain's
  own schema implies for what it generates: row counts, column set, types, nullability, primary
  keys and the values of enumerated columns. A pipeline checks the profile of the generated
  tables against it, so a generation that drifts from its schema fails the pipeline.
* :func:`write_delta_tables` writes the tables as Delta tables.
* :func:`generate_sample` and :func:`sample_to_pandas` are the single-table sample behind
  ``generateSample``; the size of the response is capped.

Names that reach a path or a table name (domain, table, prefix) must match ``[A-Za-z_]\\w*``
and, for domains and tables, be installed or defined by the schema: nothing a caller writes is
used to build a path, a statement or an import.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]

import shape
from shape import compat

if TYPE_CHECKING:
    from shape.generation.engine import GenerationResult
    from shape.generation.schema import GenSchema

__all__ = [
    "MAX_RESPONSE_BYTES",
    "MAX_SAMPLE_ROWS",
    "GenerationRequestError",
    "check_name",
    "contract_for_domain",
    "delta_ready",
    "domain_contract",
    "fit_rows",
    "generate_domain",
    "generate_sample",
    "plan_row_counts",
    "profile_tables",
    "sample_row_counts",
    "sample_to_pandas",
    "write_contract",
    "write_delta_tables",
]

MAX_SAMPLE_ROWS = 500_000  # hard ceiling for generateSample, whatever the response size
MAX_RESPONSE_BYTES = 25_000_000  # the Functions response limit is 30 MB; leave headroom
_PROBE_ROWS = 500  # rows serialized to estimate a row's size
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_RATE_SIGMAS = 5.0  # null-rate slack: this many standard errors of the observed rate

# The profiler's dtype for a column of a given schema type (``shape.profile``'s vocabulary).
_DTYPE = {
    "integer": "integer",
    "decimal": "float",
    "timestamp": "datetime",
    "date": "datetime",
    "boolean": "boolean",
    "string": "string",
}


class GenerationRequestError(ValueError):
    """The request names something that cannot be generated (a bad name, an unknown domain or
    table, an out-of-range value)."""


def check_name(value: Any, what: str) -> str:
    """``value`` if it is an identifier (letters, digits, underscore; at most 64), else raise."""
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise GenerationRequestError(
            f"{what} must be letters, digits and underscores (at most 64), starting with a "
            f"letter or underscore: got {value!r}"
        )
    return value


# --------------------------------------------------------------------------- generation


def generate_domain(
    domain: str,
    *,
    scale: str | None = None,
    seed: int | None = None,
    mode: str | None = None,
    row_counts: dict[str, int] | None = None,
) -> GenerationResult:
    """Generate every table of an installed domain.

    ``scale`` is a preset of the domain (default: the schema's), ``seed`` defaults to the
    schema's and ``mode`` (``3nf`` or ``star``) picks the schema of a domain that offers both.
    ``row_counts`` overrides single tables. Unknown names raise :class:`GenerationRequestError`.
    """
    from shape.generation.engine import Engine

    loaded = _load(domain, mode)
    if scale is not None:
        check_name(scale, "scale")
        presets = loaded.schema.generation.scales
        if scale not in presets:
            raise GenerationRequestError(
                f"domain {domain!r} has no scale preset {scale!r} (presets: {', '.join(presets)})"
            )
    if row_counts:
        for name, rows in row_counts.items():
            if name not in loaded.schema.tables:
                raise GenerationRequestError(f"domain {domain!r} has no table {name!r}")
            if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
                raise GenerationRequestError(f"row count of {name!r} must be a whole number >= 0")
    return Engine(loaded.schema, scale=scale, seed=seed, row_counts=row_counts).generate()


# ------------------------------------------------------------------------------ contract


def _null_slack(rate: float, rows: int) -> float:
    """Largest null rate worth flagging: the schema's rate plus five standard errors."""
    if rows <= 0:
        return 1.0
    return min(1.0, rate + _RATE_SIGMAS * math.sqrt(rate * (1 - rate) / rows) + 1.0 / rows)


def _numeric_text(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _enum_values(generator: dict[str, Any]) -> list[Any] | None:
    values = generator.get("values")
    if isinstance(values, dict) and values:
        return list(values)
    if isinstance(values, list) and values:
        return list(values)
    return None


def _column_rules(column: Any, rows: int, primary_key: list[str]) -> dict[str, Any]:
    rules: dict[str, Any] = {}
    values = _enum_values(column.generator) if column.strategy == "weighted_enum" else None
    dtype = _DTYPE.get(str(column.type))
    if column.type == "string":
        # The profiler reads values, not declared types: digit-only text is an "integer" and
        # "true"/"false" text a "boolean". A string dtype is only stated where the values decide.
        dtype = None
        if values is not None:
            texts = [str(v) for v in values]
            if {t.lower() for t in texts} <= {"true", "false"}:
                dtype = "boolean"
            elif not all(_numeric_text(t) for t in texts):
                dtype = "string"
                rules["allowed_values"] = texts
    elif column.type == "decimal":
        dtype = None  # whole-number decimals profile as "integer"
    if dtype is not None:
        rules["dtype"] = dtype
    if not column.nullable:
        rules["nullable"] = False
    elif column.null_rate > 0:
        rules["max_null_rate"] = round(_null_slack(float(column.null_rate), rows), 6)
    if (
        primary_key == [column.name]
        and column.strategy == "sequence"
        and not column.nullable
        and rows > 1
    ):
        rules["unique"] = True
    return rules


def domain_contract(schema: GenSchema, row_counts: dict[str, int]) -> dict[str, Any]:
    """The multi-table contract (``{"tables": {name: contract}}``, contract format v1) that
    ``schema`` implies for a generation with ``row_counts``.

    Per table: the exact row count, every column required, no extra columns, and per column the
    profiler's dtype for the declared type, ``nullable: false`` where the schema says so, a
    ``max_null_rate`` of the schema's null rate plus five standard errors, ``unique`` for a
    single-column sequence primary key and ``allowed_values`` for an enumerated string column.
    """
    tables: dict[str, Any] = {}
    for name, table in schema.tables.items():
        rows = int(row_counts[name])
        columns = {
            cname: rules
            for cname, col in table.columns.items()
            if (rules := _column_rules(col, rows, list(table.primary_key)))
        }
        tables[name] = {
            "row_count": {"min": rows, "max": rows},
            "required_columns": list(table.columns),
            "allow_extra_columns": False,
            "columns": columns,
        }
    return compat.stamp("contract", {"tables": tables}, aliases=False)


def _load(domain: str, mode: str | None) -> Any:
    from shape.generation.domains import DomainModeError, domain_names, load_domain

    check_name(domain, "domain")
    if domain not in domain_names():
        known = ", ".join(domain_names()) or "none installed"
        raise GenerationRequestError(f"no domain named {domain!r} (installed: {known})")
    if mode is not None and mode not in ("3nf", "star"):
        raise GenerationRequestError(f"mode must be '3nf' or 'star', got {mode!r}")
    try:
        return load_domain(domain, mode=mode)
    except DomainModeError as exc:
        raise GenerationRequestError(str(exc)) from exc


def plan_row_counts(
    domain: str, scale: str | None = None, mode: str | None = None
) -> dict[str, int]:
    """Rows per table that :func:`generate_domain` would generate, without generating: for
    refusing a run that is too large for the machine before it starts."""
    from shape.generation.engine import calculate_row_counts

    schema = _load(domain, mode).schema
    if scale is not None:
        check_name(scale, "scale")
        if scale not in schema.generation.scales:
            raise GenerationRequestError(
                f"domain {domain!r} has no scale preset {scale!r} "
                f"(presets: {', '.join(schema.generation.scales)})"
            )
        schema.generation.scale = scale
    return calculate_row_counts(schema)


def contract_for_domain(
    domain: str, row_counts: dict[str, int], mode: str | None = None
) -> dict[str, Any]:
    """:func:`domain_contract` for the schema of the installed domain ``domain``."""
    return domain_contract(_load(domain, mode).schema, row_counts)


def profile_tables(folder: str | Path, name: str | None = None) -> Any:
    """Profile the Parquet files of ``folder`` together, one table per file (named by the file),
    as a dataset profile: what a multi-table contract is checked against (``shape profile
    --dataset FOLDER`` is the command-line form; without ``--dataset`` a folder is one table)."""
    files = sorted(Path(folder).glob("*.parquet"))
    if not files:
        raise GenerationRequestError(f"no Parquet files in {folder}")
    return shape.profile({f.stem: str(f) for f in files}, name=name)


def write_contract(contract: dict[str, Any], path: str | Path) -> Path:
    """Write ``contract`` as JSON at ``path`` (parent folders are created)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(contract, indent=1), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------------- Delta


def delta_ready(table: pa.Table) -> pa.Table:
    """``table`` with the types Delta Lake stores: nanosecond timestamps become microseconds
    (Delta has no nanosecond timestamp), and large strings become strings."""
    fields = []
    for field in table.schema:
        ftype = field.type
        if pa.types.is_timestamp(ftype) and ftype.unit == "ns":
            ftype = pa.timestamp("us", tz=ftype.tz)
        elif pa.types.is_large_string(ftype):
            ftype = pa.string()
        fields.append(pa.field(field.name, ftype, field.nullable))
    return table.cast(pa.schema(fields), safe=False)


def write_delta_tables(
    result: GenerationResult,
    tables_dir: str | Path,
    *,
    prefix: str = "",
    mode: str = "overwrite",
) -> list[dict[str, Any]]:
    """Write every table of ``result`` as the Delta table ``<tables_dir>/<prefix><table>``.

    ``mode`` is ``overwrite`` or ``append``. Returns ``[{"table", "deltaTable", "rows"}]`` in
    generation order. In a Fabric notebook ``tables_dir`` is ``/lakehouse/default/Tables``.
    """
    from shape.plugins.host import default_host

    if prefix:
        check_name(prefix, "tablePrefix")
    if mode not in ("overwrite", "append"):
        raise GenerationRequestError(f"mode must be 'overwrite' or 'append', got {mode!r}")
    sink = default_host().get("shape.sinks", "delta")  # built-ins load through the registry
    base = Path(tables_dir)
    written: list[dict[str, Any]] = []
    for name in result.generation_order:
        table = delta_ready(result.tables[name])
        delta_name = f"{prefix}{check_name(name, 'table')}"
        sink.write(
            str(base),
            delta_name,
            iter(table.to_batches()),
            schema=table.schema,
            mode=mode,
        )
        written.append({"table": name, "deltaTable": delta_name, "rows": table.num_rows})
    return written


# ------------------------------------------------------------------------------- samples


def sample_row_counts(schema: GenSchema, table: str, rows: int) -> dict[str, int]:
    """Row counts for a one-table sample: ``rows`` for ``table`` and every other table at its count
    at the schema's scale preset (at most :data:`MAX_SAMPLE_ROWS`).

    The other tables are the sample's context: the foreign keys of ``table`` draw from their parents
    and its computed columns and business rules read its children, so they are generated as the
    domain generates them. With ``rows`` equal to the table's count at that preset, the sample is
    exactly that table of a full run with the same seed."""
    from shape.generation.engine import calculate_row_counts

    counts = calculate_row_counts(schema)
    return {
        name: (rows if name == table else min(count, MAX_SAMPLE_ROWS))
        for name, count in counts.items()
    }


def generate_sample(domain: str, table: str, rows: int = 10_000, seed: int = 42) -> pa.Table:
    """``rows`` rows of ``table`` from an installed domain, as an Arrow table.

    The whole domain is generated at its default scale (the compute and rule passes need the related
    tables; see :func:`sample_row_counts`), with ``table`` at ``rows`` rows, and only ``table`` is
    returned. ``rows`` is capped at :data:`MAX_SAMPLE_ROWS`; :func:`fit_rows` caps it further for a
    response size.
    """
    from shape.generation.domains import load_domain

    check_name(domain, "domain")
    check_name(table, "table")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise GenerationRequestError("rows must be a whole number of at least 1")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GenerationRequestError("seed must be a whole number")
    # The domain and table names are matched against what is installed before any use.
    schema = load_domain(_installed(domain)).schema
    if table not in schema.tables:
        raise GenerationRequestError(
            f"domain {domain!r} has no table {table!r} (tables: {', '.join(schema.tables)})"
        )
    wanted = min(rows, MAX_SAMPLE_ROWS)
    result = generate_domain(domain, seed=seed, row_counts=sample_row_counts(schema, table, wanted))
    out: pa.Table = result.tables[table]
    return out


def _installed(domain: str) -> str:
    from shape.generation.domains import domain_names

    for name in domain_names():
        if name == domain:
            return name
    known = ", ".join(domain_names()) or "none installed"
    raise GenerationRequestError(f"no domain named {domain!r} (installed: {known})")


def fit_rows(frame: Any, limit: int | None = None) -> Any:
    """The leading rows of the DataFrame ``frame`` whose JSON form fits in ``limit`` bytes.

    The size of a row is estimated from the first rows (a probe of ``_PROBE_ROWS``), so
    the result is under the limit for data of an even width and cut with a margin otherwise
    (the estimate is raised by 10%). ``limit`` defaults to :data:`MAX_RESPONSE_BYTES`."""
    limit = MAX_RESPONSE_BYTES if limit is None else limit
    if len(frame) == 0:
        return frame
    probe = frame.head(_PROBE_ROWS)
    per_row = len(probe.to_json(orient="split", date_format="iso")) / len(probe)
    fits = max(1, int(limit / (per_row * 1.1)))
    return frame if len(frame) <= fits else frame.head(fits)


def _nullable_types(arrow_type: Any) -> Any:
    """pandas' nullable dtype for an Arrow integer or boolean type, so a column with nulls keeps its
    integers (``1, <NA>``, not ``1.0, NaN``); ``None`` leaves every other type to pandas."""
    import pandas as pd  # type: ignore[import-untyped]

    if pa.types.is_boolean(arrow_type):
        return pd.BooleanDtype()
    if pa.types.is_integer(arrow_type):
        unsigned = "U" if pa.types.is_unsigned_integer(arrow_type) else ""
        return pd.api.types.pandas_dtype(f"{unsigned}Int{arrow_type.bit_width}")
    return None


def sample_to_pandas(domain: str, table: str, rows: int = 10_000, seed: int = 42) -> Any:
    """:func:`generate_sample` as a pandas DataFrame, cut to fit :data:`MAX_RESPONSE_BYTES`.

    Integer and boolean columns use pandas' nullable dtypes, so a column that has nulls keeps its
    integer values and its Arrow type when the frame is converted back."""
    sample = generate_sample(domain, table, rows, seed)
    return fit_rows(sample.to_pandas(types_mapper=_nullable_types))
