"""The ``semantic-model://`` source: read a table of a Power BI / Fabric semantic model.

    semantic-model://<workspace>/<model>/<table>

``<workspace>`` and ``<model>`` are names or GUIDs, and any part with a ``/`` in it is percent-
encoded (``Sales%2FEU``). The data comes from ``sempy`` (semantic link, PyPI ``semantic-link-
sempy``), which signs in as the notebook's user, so this runs inside a Fabric notebook.
``shape profile semantic-model://Sales/Retail/Customer -o customer.shape`` works, and so does
every consumer of the ``shape.sources`` Protocol. ``shape profile-model`` profiles a whole model.

Options: ``columns`` (a subset, in the order given), ``batch_rows`` (default 65,536),
``max_rows`` (a cap, read as a DAX ``TOPN`` through ``evaluate_dax``) and ``mode`` (sempy's read
mode, ``xmla`` or ``onelake`` where this sempy has one; not with ``max_rows``). ``as_role``
reads through native role-filtered DAX and cannot be combined with ``mode``.

``sempy`` is imported only when a semantic-model URI is opened (:func:`fabric`), so this module
and the rest of the plugin import and run without it.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from .semantic_model import dax_column, dax_table

SHAPE_API = "1.0"
SCHEME = "semantic-model"
DEFAULT_BATCH_ROWS = 65_536
FORM = "semantic-model://<workspace>/<model>/<table>"
NEEDS_SEMPY = (
    "semantic-model:// needs sempy (pip install 'sqllocks-shape-fabric[semantic-link]') "
    "and runs inside a Fabric notebook"
)
_OPTIONS = {"columns", "batch_rows", "max_rows", "mode", "as_role"}

# Semantic model data types (the ``Data Type`` of ``sempy.fabric.list_columns``) to Arrow. Fixed
# decimal numbers (Power BI "currency") have four decimal places and 19 digits.
TYPE_MAP: dict[str, pa.DataType] = {
    "Int64": pa.int64(),
    "Double": pa.float64(),
    "Decimal": pa.decimal128(19, 4),
    "Currency": pa.decimal128(19, 4),
    "String": pa.string(),
    "Boolean": pa.bool_(),
    "DateTime": pa.timestamp("us"),
    "Binary": pa.binary(),
}
_BY_LOWER = {k.lower(): v for k, v in TYPE_MAP.items()}

_WARNED: set[tuple[str, str, str, str]] = set()


def _reset_warnings() -> None:
    """Forget which unknown types were reported (for tests)."""
    _WARNED.clear()


def arrow_type(data_type: str) -> pa.DataType | None:
    """The Arrow type of a semantic model data type, ``None`` for one the map does not know."""
    return _BY_LOWER.get(str(data_type).strip().lower())


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    hidden: bool
    arrow: pa.DataType | None  # None: unknown type, read as string


# --- URI ------------------------------------------------------------------------------


def parse(uri: str) -> tuple[str, str, str]:
    """``(workspace, model, table)`` of a ``semantic-model://`` URI, percent-decoded."""
    prefix = SCHEME + "://"
    if not uri.startswith(prefix):
        raise ShapeError(f"{uri!r} is not a {FORM} URI")
    parts = uri[len(prefix) :].split("/")
    if len(parts) != 3 or not all(parts):
        raise ShapeError(f"{uri!r} must be {FORM}")
    workspace, model, table = (unquote(p) for p in parts)
    return workspace, model, table


def build(workspace: str, model: str, table: str) -> str:
    """The URI of a table: parts are percent-encoded so a ``/`` in a name stays in it."""
    return f"{SCHEME}://{quote(workspace, safe='')}/{quote(model, safe='')}/{quote(table, safe='')}"


# --- sempy ----------------------------------------------------------------------------


def fabric() -> Any:
    """``sempy.fabric``, imported now and not before."""
    try:
        return importlib.import_module("sempy.fabric")
    except ImportError as exc:
        raise ShapeError(NEEDS_SEMPY) from exc


def _one_line(exc: BaseException) -> str:
    from shape.security.redact import redact_text

    return redact_text(" ".join(str(exc).split()))


def _sempy_failed(workspace: str, model: str, exc: BaseException) -> ShapeError:
    return ShapeError(
        f"semantic model {model} in workspace {workspace}: sempy failed: {_one_line(exc)}"
    )


def _frame(fab: Any, name: str, ws: str, model: str, **kw: Any) -> Any:
    try:
        return getattr(fab, name)(**kw)
    except ShapeError:
        raise
    except Exception as exc:  # noqa: BLE001 - sempy raises its own and HTTP errors
        raise _sempy_failed(ws, model, exc) from None


def _values(frame: Any, column: str) -> list[Any]:
    return [v for v in frame[column].tolist()] if column in frame.columns else []


def check_model(workspace: str, model: str) -> Any:
    """``sempy.fabric`` once the workspace and the model are known to exist."""
    fab = fabric()
    try:
        workspaces = fab.list_workspaces()
    except Exception as exc:  # noqa: BLE001 - no sign-in, no network: one line, same fix
        raise ShapeError(f"{NEEDS_SEMPY}: {_one_line(exc)}") from None
    if workspace not in (*_values(workspaces, "Name"), *_values(workspaces, "Id")):
        raise ShapeError(f"semantic-model workspace {workspace} not found")
    datasets = _frame(fab, "list_datasets", workspace, model, workspace=workspace)
    if model not in (*_values(datasets, "Dataset Name"), *_values(datasets, "Dataset Id")):
        raise ShapeError(f"workspace {workspace} has no semantic model {model}")
    return fab


def table_names(workspace: str, model: str) -> list[str]:
    """Every table of the model (hidden ones too), in the model's order."""
    fab = check_model(workspace, model)
    frame = _frame(fab, "list_tables", workspace, model, dataset=model, workspace=workspace)
    return [str(v) for v in _values(frame, "Name")]


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def column_infos(workspace: str, model: str, table: str) -> list[ColumnInfo]:
    """The columns of a table from the model's metadata (no rows are read), hidden ones too.
    Measures are not columns and are not listed."""
    fab = check_model(workspace, model)
    if table not in [
        str(v)
        for v in _values(
            _frame(fab, "list_tables", workspace, model, dataset=model, workspace=workspace),
            "Name",
        )
    ]:
        raise ShapeError(f"semantic model {model} in workspace {workspace} has no table {table}")
    frame = _frame(
        fab, "list_columns", workspace, model, dataset=model, table=table, workspace=workspace
    )
    type_col = "Data Type" if "Data Type" in frame.columns else None
    out: list[ColumnInfo] = []
    for _, row in frame.iterrows():
        if str(row["Table Name"]) != table:
            continue
        dtype = str(row[type_col]) if type_col else ""
        out.append(
            ColumnInfo(
                str(row["Column Name"]),
                dtype,
                _truthy(row["Hidden"]) if "Hidden" in frame.columns else False,
                arrow_type(dtype),
            )
        )
    return out


def _check_options(options: dict[str, Any]) -> None:
    if "credential" in options or "token" in options:
        raise ShapeError(
            "semantic-model:// signs in through sempy as the notebook's user; --auth does not apply"
        )
    unknown = sorted(set(options) - _OPTIONS)
    if unknown:
        raise ShapeError(
            f"unknown option {unknown[0]!r} for semantic-model:// (options: "
            f"{', '.join(sorted(_OPTIONS))})"
        )


def _positive_int(options: dict[str, Any], key: str, default: int | None, minimum: int) -> Any:
    value = options.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ShapeError(f"{key} must be an integer of at least {minimum}, got {value!r}")
    return value


def _selected(
    infos: list[ColumnInfo], columns: Any, workspace: str, model: str, table: str
) -> list[ColumnInfo]:
    if columns is None:
        return infos
    if isinstance(columns, str) or not columns:
        raise ShapeError("columns must be a non-empty list of column names")
    by_name = {c.name: c for c in infos}
    chosen: list[ColumnInfo] = []
    for name in columns:
        if name not in by_name:
            raise ShapeError(
                f"semantic model {model} in workspace {workspace} has no column {table}[{name}]"
            )
        if by_name[name] in chosen:
            raise ShapeError(f"column {name} is given more than once")
        chosen.append(by_name[name])
    return chosen


def warn_unknown(workspace: str, model: str, table: str, infos: list[ColumnInfo]) -> None:
    for c in infos:
        key = (workspace, model, table, c.name)
        if c.arrow is None and key not in _WARNED:
            _WARNED.add(key)
            shown = c.data_type or "(none given)"
            print(
                f"shape: warning: semantic-model column {table}[{c.name}] has type {shown}; "
                "read as string",
                file=sys.stderr,
            )


def to_schema(infos: list[ColumnInfo]) -> pa.Schema:
    """The Arrow schema of the columns; a hidden column carries ``hidden: true`` as field
    metadata."""
    return pa.schema(
        [
            pa.field(
                c.name,
                c.arrow if c.arrow is not None else pa.string(),
                metadata={b"hidden": b"true"} if c.hidden else None,
            )
            for c in infos
        ]
    )


# --- reading --------------------------------------------------------------------------


def _result_column(frame: Any, table: str, name: str, alias: str | None) -> Any:
    if alias is not None:  # a DAX result: columns are the positional aliases, bracketed or not
        for candidate in (f"[{alias}]", alias):
            if candidate in frame.columns:
                return frame[candidate]
        raise ShapeError(f"sempy returned no column {table}[{name}]")
    escaped = name.replace("]", "]]")
    for candidate in (name, f"{table}[{escaped}]", f"[{escaped}]"):
        if candidate in frame.columns:
            return frame[candidate]
    raise ShapeError(f"sempy returned no column {table}[{name}]")


def _to_array(series: Any, field: pa.Field, table: str) -> pa.Array:
    import pandas as pd  # type: ignore[import-untyped]

    typ = field.type
    try:
        if pa.types.is_string(typ):
            return pa.array(
                [None if pd.isna(v) else str(v) for v in series.tolist()], type=pa.string()
            )
        if pa.types.is_timestamp(typ) and getattr(series.dt, "tz", None) is not None:
            series = series.dt.tz_convert("UTC").dt.tz_localize(None)
        array = pa.Array.from_pandas(series)
        if array.type == typ:
            return array
        loose = pa.types.is_decimal(typ) or pa.types.is_timestamp(typ)
        return array.cast(typ, safe=not loose)
    except (
        pa.ArrowInvalid,
        pa.ArrowTypeError,
        pa.ArrowNotImplementedError,
        TypeError,
        ValueError,
    ) as exc:
        raise ShapeError(
            f"semantic-model column {table}[{field.name}] has a value that is not "
            f"{typ} ({_one_line(exc)})"
        ) from exc


def _to_table(frame: Any, schema: pa.Schema, table: str, *, dax: bool = False) -> pa.Table:
    arrays = [
        _to_array(_result_column(frame, table, f.name, f"c{i}" if dax else None), f, table)
        for i, f in enumerate(schema)
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


def topn_dax(table: str, columns: list[str], n: int) -> str:
    """``EVALUATE`` the first ``n`` rows of the columns, each result column named ``c0``,
    ``c1``, ... by position. Every name is quoted by the rule of :mod:`shape_fabric.
    semantic_model`: a quote doubled in the table, a closing bracket doubled in a column, so no
    name can end the expression."""
    pairs = ", ".join(f'"c{i}", {dax_column(table, c)}' for i, c in enumerate(columns))
    return f"EVALUATE SELECTCOLUMNS(TOPN({int(n)}, {dax_table(table)}), {pairs})"


def select_dax(table: str, columns: list[str]) -> str:
    pairs = ", ".join(f'"c{i}", {dax_column(table, c)}' for i, c in enumerate(columns))
    return f"EVALUATE SELECTCOLUMNS({dax_table(table)}, {pairs})"


def read_table(
    workspace: str,
    model: str,
    table: str,
    infos: list[ColumnInfo],
    *,
    max_rows: int | None = None,
    mode: str | None = None,
    as_role: str | None = None,
) -> pa.Table:
    """The rows of ``infos``' columns as one Arrow table."""
    schema = to_schema(infos)
    if as_role is not None:
        from .semantic_metadata import check_role

        as_role = check_role(workspace, model, as_role)
        if mode is not None:
            raise ShapeError("mode does not apply to an as_role DAX read")
    if max_rows == 0:
        return schema.empty_table()
    fab = check_model(workspace, model)
    if max_rows is not None or as_role is not None:
        if mode is not None:
            raise ShapeError("mode applies to reading a whole table, not with max_rows")
        names = [c.name for c in infos]
        dax = topn_dax(table, names, max_rows) if max_rows is not None else select_dax(table, names)
        dax_options: dict[str, Any] = {"dataset": model, "dax_string": dax, "workspace": workspace}
        if as_role is None:
            frame = _frame(fab, "evaluate_dax", workspace, model, **dax_options)
        else:
            try:
                frame = fab.evaluate_dax(**dax_options, role=as_role)
            except Exception:  # noqa: BLE001 - remote exceptions can carry credentials
                raise ShapeError(
                    f"semantic model {model} in workspace {workspace}: role DAX read failed"
                ) from None
        return _to_table(frame, schema, table, dax=True)
    kw: dict[str, Any] = {"dataset": model, "table": table, "workspace": workspace}
    if mode is not None:
        if "mode" not in inspect.signature(fab.read_table).parameters:
            raise ShapeError("this sempy has no read mode; leave mode out")
        kw["mode"] = mode
    return _to_table(_frame(fab, "read_table", workspace, model, **kw), schema, table)


class SemanticModelSource:
    """Tables of a Power BI / Fabric semantic model, by ``semantic-model://`` URI."""

    name = "semantic-model"
    schemes = (SCHEME,)

    def can_open(self, uri: str) -> bool:
        return uri.startswith(SCHEME + "://")

    def _plan(
        self, uri: str, options: dict[str, Any]
    ) -> tuple[tuple[str, str, str], list[ColumnInfo]]:
        _check_options(options)
        parts = parse(uri)
        if "as_role" in options:
            from .semantic_metadata import check_role

            check_role(parts[0], parts[1], options["as_role"])
        infos = _selected(column_infos(*parts), options.get("columns"), *parts)
        warn_unknown(*parts, infos)
        return parts, infos

    def profile_metadata(self, uri: str, profile: Any) -> Any:
        """Attach hidden metadata outside the unchanged reference profiler (T-03)."""
        from shape.profile.reference import Profile

        infos = column_infos(*parse(uri))
        doc = profile.to_dict()
        for column in infos:
            if column.hidden and column.name in doc["columns"]:
                doc["columns"][column.name]["hidden"] = True
        return Profile(
            doc,
            name=profile.name,
            provenance=profile.provenance,
            sketches=profile.sketches,
            capture=profile.capture if profile.capture_declared else None,
            redaction_manifest=profile.redaction_manifest,
        )

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        """The Arrow schema from the model's column metadata; no rows are read."""
        _, infos = self._plan(uri, options)
        return to_schema(infos)

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        """The table as record batches of at most ``batch_rows`` rows."""
        batch_rows = _positive_int(options, "batch_rows", DEFAULT_BATCH_ROWS, 1)
        max_rows = _positive_int(options, "max_rows", None, 0)
        (workspace, model, table), infos = self._plan(uri, options)
        data = read_table(
            workspace,
            model,
            table,
            infos,
            max_rows=max_rows,
            mode=options.get("mode"),
            as_role=options.get("as_role"),
        )
        return iter(data.to_batches(max_chunksize=batch_rows))


__all__ = [
    "DEFAULT_BATCH_ROWS",
    "SHAPE_API",
    "NEEDS_SEMPY",
    "TYPE_MAP",
    "ColumnInfo",
    "SemanticModelSource",
    "arrow_type",
    "build",
    "column_infos",
    "parse",
    "read_table",
    "table_names",
    "to_schema",
    "topn_dax",
]
