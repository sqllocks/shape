"""Helpers behind the Fabric User Data Functions in ``function_app.py``.

The ``function_app.py`` template (``integrations/fabric/udf/function_app.py``) only declares the
Fabric bindings (camelCase parameters, ``@udf.connection``) and calls the functions here, so the
logic can be imported, tested and versioned with the Shape wheel. This module needs no Fabric
SDK: errors are raised as ``fabric.functions.UserThrownError`` when the SDK is installed (inside
Fabric it always is) and as the equivalent :class:`UserThrownError` below otherwise.

Design rules (COMPLETION_PLAN section 12.4, DM-5 and DM-6):

* The function host allows 240 s (100 s through the public endpoint), so heavy profiling
  is refused with a user-visible error that points to the notebook path.
* ``fail_on_*=True`` raises the error carrying the violations, so a pipeline Functions
  activity fails visibly.
* Every result is kept under 1 MB (the response limit is 30 MB).
* Results are safe by default, as the bridge's ``profile``, ``check`` and ``diff`` are: a column
  the safe-profile gate classifies (``pii_gate_fires``: a personal-data pattern, or nearly every
  value distinct) has its raw ``min`` and ``max`` withheld from a profile summary, and its
  observed values withheld from check violations and diff changes. A withheld value is ``null``
  and the column or entry says ``"redacted": true``. ``include_raw_values=True`` returns the raw
  values. The ``.shape`` file written to ``output_path`` is the full profile and holds them.

A ``lakehouse`` argument is any object with ``connectToFiles()`` (an Azure Data Lake
``FileSystemClient``-like object) and ``connectToSql()`` (a DB-API connection), which is what
``fabric.functions.FabricLakehouseClient`` provides.
"""

from __future__ import annotations

import importlib
import io
import json
import logging
import math
import re
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.json as pajson  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

import shape
from shape.bridge.handlers.flow import classified_columns, redact_entries, redact_summary
from shape.integrations.fabric import generation
from shape.io.identifiers import read_csv_keeping_identifiers
from shape.security.jsondepth import check_json_depth
from shape.security.redact import redact_text

__all__ = [
    "LAKEHOUSE_ALIAS",
    "UserThrownError",
    "bounded",
    "cap_values",
    "check_profile",
    "diff_profiles",
    "generate_sample",
    "json_safe",
    "profile_data_frame",
    "profile_lakehouse_file",
    "profile_lakehouse_table",
    "sql_table_name",
]

log = logging.getLogger("shape.udf")

# Alias of the Lakehouse data connection added under "Manage connections" in the UDF item.
LAKEHOUSE_ALIAS = "shapeLakehouse"

MAX_RESULT_BYTES = 900_000  # results stay under 1 MB
MAX_LISTED = 100  # violations / changes returned inline
MAX_VALUES = 20  # values kept of one change's or violation's own list (new categories, ...)
MAX_TABLE_ROWS_CAP = 5_000_000  # hard ceiling for maxRows
MAX_TABLE_CELLS = 30_000_000  # rows x columns guard for the table path
NOTEBOOK_HINT = (
    "Use the Shape profile notebook (integrations/fabric/notebooks/shape_profile.ipynb) "
    "for inputs this large; User Data Functions stop at 240 seconds."
)
_TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")


class _StandInError(Exception):
    """Stand-in for ``fabric.functions.UserThrownError`` (same ``message``/``properties``)."""

    def __init__(self, message: str, properties: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.properties = properties or {}


def _error_type() -> type[Exception]:
    # Inside Fabric (and in the SDK tests) the platform's own error type is the one that counts.
    try:
        module = importlib.import_module("fabric.functions")
        found: type[Exception] = module.UserThrownError
        return found
    except (ImportError, AttributeError):
        return _StandInError


UserThrownError: type[Exception] = _error_type()


# ------------------------------------------------------------------------- helpers


def _fail(message: str, **properties: Any) -> Exception:
    """The error the function's caller gets. Messages quote exceptions from storage and SQL
    drivers, which can echo a SAS URL or a connection string, so the text is redacted."""
    return UserThrownError(redact_text(message), json_safe(properties))


def json_safe(obj: Any) -> Any:
    """Make a value strictly JSON-safe: no NaN/inf, no numpy scalars."""
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item") and not isinstance(obj, str | bytes):
        try:
            return json_safe(obj.item())
        except (ValueError, TypeError):
            return str(obj)
    if obj is None or isinstance(obj, str | int | bool):
        return obj
    return str(obj)


def bounded(result: dict[str, Any], droppable: str = "summary") -> dict[str, Any]:
    """Return ``result`` JSON-safe and under MAX_RESULT_BYTES, dropping ``droppable`` if needed."""
    result = json_safe(result)
    if len(json.dumps(result)) > MAX_RESULT_BYTES and droppable in result:
        result[droppable] = None
        result[f"{droppable}Omitted"] = True
    return result


def cap_values(entry: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """``entry`` (a diff change or a check violation) with each list or dict value cut to its first
    ``limit`` items (default :data:`MAX_VALUES`). A cut value gets ``<key>Count`` (its full length)
    and the entry ``valuesTruncated: true``: ``new_categorical_values`` on a float column lists
    every new value, thousands of them."""
    limit = MAX_VALUES if limit is None else limit
    out: dict[str, Any] = {}
    cut = False
    for key, value in entry.items():
        if isinstance(value, list | dict) and len(value) > limit:
            out[key] = (
                value[:limit] if isinstance(value, list) else dict(list(value.items())[:limit])
            )
            out[f"{key}Count"] = len(value)
            cut = True
        else:
            out[key] = value
    if cut:
        out["valuesTruncated"] = True
    return out


def _clean_path(path: str) -> str:
    p = str(PurePosixPath("/" + path.strip().replace("\\", "/"))).lstrip("/")
    if p.startswith("Files/"):
        p = p[len("Files/") :]
    if not p or ".." in PurePosixPath(p).parts:
        raise _fail(f"Invalid lakehouse path: {path!r}")
    return p


def _file_client(lakehouse: Any, path: str) -> Any:
    return lakehouse.connectToFiles().get_file_client(_clean_path(path))


def _read_bytes(lakehouse: Any, path: str, max_bytes: int | None = None) -> bytes:
    client = _file_client(lakehouse, path)
    try:
        size = int(client.get_file_properties().size)
    except Exception as e:
        raise _fail(f"Cannot read {path!r} from the lakehouse Files area: {e}") from e
    if max_bytes is not None and size > max_bytes:
        raise _fail(
            f"{path!r} is {size / 1e6:.1f} MB, above the {max_bytes / 1e6:.0f} MB limit for "
            f"User Data Functions. {NOTEBOOK_HINT}",
            sizeMegabytes=round(size / 1e6, 2),
            limitMegabytes=round(max_bytes / 1e6, 2),
        )
    try:
        return bytes(client.download_file().readall())
    except Exception as e:
        raise _fail(f"Cannot download {path!r}: {e}") from e


def _write_bytes(lakehouse: Any, path: str, data: bytes) -> str:
    clean = _clean_path(path)
    try:
        lakehouse.connectToFiles().get_file_client(clean).upload_data(data, overwrite=True)
    except Exception as e:
        raise _fail(f"Cannot write {path!r} to the lakehouse Files area: {e}") from e
    return clean


def _table_from_bytes(data: bytes, path: str) -> pa.Table:
    suffix = PurePosixPath(path).suffix.lower()
    buf = io.BytesIO(data)
    try:
        if suffix == ".parquet":
            return pq.read_table(buf)
        if suffix == ".csv":
            return read_csv_keeping_identifiers(buf)
        if suffix in (".jsonl", ".ndjson"):
            check_json_depth(data)
            return pajson.read_json(buf)
    except Exception as e:
        raise _fail(f"Could not parse {path!r} as {suffix[1:]}: {e}") from e
    raise _fail(f"Unsupported file type {suffix!r}: use .csv, .parquet or .jsonl")


def _save_profile(lakehouse: Any, profile: Any, output_path: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "profile.shape"
        # check_profile and diff_profiles read the file back and need the real extremes and values
        # (W1-11 made the default capture safe): this file is as private as the lakehouse folder
        shape.save(profile, str(local), capture="full")
        return _write_bytes(lakehouse, output_path, local.read_bytes())


def _load_profile(lakehouse: Any, path: str) -> Any:
    data = _read_bytes(lakehouse, path, max_bytes=50_000_000)
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "profile.shape"
        local.write_bytes(data)
        try:
            return shape.load(str(local))
        except Exception as e:
            raise _fail(f"{path!r} is not a readable .shape profile: {e}") from e


def _describe(items: list[dict[str, Any]], limit: int = 5) -> str:
    return "; ".join(
        f"{i.get('column') or '*'}: {i.get('rule') or i.get('kind')}" for i in items[:limit]
    ) + ("; ..." if len(items) > limit else "")


def _profile_result(
    profile: Any,
    *,
    source: str,
    elapsed: float,
    output: str,
    extra: dict[str, Any],
    include_raw_values: bool,
) -> dict[str, Any]:
    summary = profile.summary()
    if not include_raw_values:
        summary = redact_summary(summary, classified_columns(profile))
    log.info(
        "shape.udf source=%s rows=%s elapsed_seconds=%.3f",
        source,
        summary.get("row_count"),
        elapsed,
    )
    return bounded(
        {
            "source": source,
            "rows": summary.get("row_count"),
            "columns": len(summary.get("columns", {})),
            "elapsedSeconds": round(elapsed, 3),
            "outputPath": output,
            "summary": summary,
            **extra,
        }
    )


DEFAULT_SCHEMA = "dbo"  # a lakehouse's SQL analytics endpoint puts its tables in dbo


def sql_table_name(table_name: str) -> str:
    """``table`` or ``schema.table`` as the SQL analytics endpoint's quoted two-part name.

    A name without a schema gets ``dbo``: the endpoint does not resolve an unqualified name
    (``SELECT ... FROM [orders_day1]`` fails with 42S02, ``[dbo].[orders_day1]`` works). Each part
    is bracket-quoted with ``]`` doubled; callers pass only names that match ``_TABLE_NAME``.
    """
    parts = table_name.split(".")
    if len(parts) == 1:
        parts = [DEFAULT_SCHEMA, *parts]
    return ".".join("[" + part.replace("]", "]]") + "]" for part in parts)


_FETCH_ROWS = 10_000  # rows per fetchmany() while reading a table


class _CellLimitError(Exception):
    def __init__(self, rows: int) -> None:
        super().__init__(rows)
        self.rows = rows


def _fetch_within_limit(cursor: Any, n_columns: int, max_rows: int) -> list[tuple[Any, ...]]:
    """Every row of ``cursor``, read in batches, raising :class:`_CellLimitError` as soon as the
    first ``max_rows`` rows read pass MAX_TABLE_CELLS: a table too large is refused without being
    read in full. (The row after ``max_rows`` only tells that the table has more.)"""
    if not hasattr(cursor, "fetchmany"):
        return [tuple(r) for r in cursor.fetchall()]
    rows: list[tuple[Any, ...]] = []
    while True:
        batch = cursor.fetchmany(_FETCH_ROWS)
        if not batch:
            return rows
        rows.extend(tuple(r) for r in batch)
        if min(len(rows), max_rows) * n_columns > MAX_TABLE_CELLS:
            raise _CellLimitError(len(rows))


# ----------------------------------------------------------------------- functions


def profile_lakehouse_file(
    lakehouse: Any,
    file_path: str,
    output_path: str = "",
    max_megabytes: int = 50,
    include_raw_values: bool = False,
) -> dict[str, Any]:
    """Profile a CSV, Parquet or JSONL file under the lakehouse Files area.

    ``file_path`` is relative to Files/. ``output_path`` (optional) receives the ``.shape``
    artifact. Files above ``max_megabytes`` are refused: use the notebook. The raw ``min`` and
    ``max`` of classified columns are withheld unless ``include_raw_values`` is true.
    """
    started = time.perf_counter()
    if max_megabytes < 1:
        raise _fail("maxMegabytes must be at least 1")
    data = _read_bytes(lakehouse, file_path, max_bytes=int(max_megabytes * 1_000_000))
    table = _table_from_bytes(data, file_path)
    profile = shape.profile(table, name=PurePosixPath(file_path).stem)
    saved = _save_profile(lakehouse, profile, output_path) if output_path else ""
    return _profile_result(
        profile,
        source=_clean_path(file_path),
        elapsed=time.perf_counter() - started,
        output=saved,
        extra={"sizeMegabytes": round(len(data) / 1e6, 3), "sampled": False},
        include_raw_values=include_raw_values,
    )


def profile_lakehouse_table(
    lakehouse: Any,
    table_name: str,
    max_rows: int = 1000000,
    output_path: str = "",
    include_raw_values: bool = False,
) -> dict[str, Any]:
    """Profile the first ``max_rows`` rows of a lakehouse table via its SQL endpoint.

    ``sampled`` is true when the table has more rows than ``max_rows``: the profile then covers
    only the first ``max_rows`` rows the endpoint returned, not a random sample. The raw ``min``
    and ``max`` of classified columns are withheld unless ``include_raw_values`` is true.
    """
    started = time.perf_counter()
    if not _TABLE_NAME.fullmatch(table_name):
        raise _fail("tableName must be 'table' or 'schema.table' (letters, digits, underscore)")
    if not 1 <= max_rows <= MAX_TABLE_ROWS_CAP:
        raise _fail(f"maxRows must be between 1 and {MAX_TABLE_ROWS_CAP:,}. {NOTEBOOK_HINT}")
    quoted = sql_table_name(table_name)
    conn = lakehouse.connectToSql()
    try:
        cursor = conn.cursor()
        # Only identifiers that fully match _TABLE_NAME (letters, digits, underscore) reach here,
        # each part bracket-quoted, and max_rows is a range-checked int: nothing user-written
        # beyond those is in the statement. Endpoints take no parameters for identifiers.
        cursor.execute(f"SELECT TOP ({max_rows + 1}) * FROM {quoted}")  # nosec B608
        columns = [d[0] for d in cursor.description]
        rows = _fetch_within_limit(cursor, max(len(columns), 1), max_rows)
    except _CellLimitError as e:
        raise _fail(
            f"{e.rows:,}+ rows x {len(columns)} columns exceeds the {MAX_TABLE_CELLS:,}-cell "
            f"limit for User Data Functions. Lower maxRows. {NOTEBOOK_HINT}"
        ) from None
    except Exception as e:
        raise _fail(f"Could not read table {table_name!r} through the SQL endpoint: {e}") from e
    finally:
        conn.close()
    sampled = len(rows) > max_rows
    rows = rows[:max_rows]
    if len(rows) * max(len(columns), 1) > MAX_TABLE_CELLS:
        raise _fail(
            f"{len(rows):,} rows x {len(columns)} columns exceeds the {MAX_TABLE_CELLS:,}-cell "
            f"limit for User Data Functions. Lower maxRows. {NOTEBOOK_HINT}"
        )
    import pandas as pd  # type: ignore[import-untyped]

    frame = pd.DataFrame.from_records(rows, columns=columns)
    profile = shape.profile(frame, name=table_name)
    saved = _save_profile(lakehouse, profile, output_path) if output_path else ""
    return _profile_result(
        profile,
        source=table_name,
        elapsed=time.perf_counter() - started,
        output=saved,
        extra={"sampled": sampled, "maxRows": max_rows},
        include_raw_values=include_raw_values,
    )


def check_profile(
    lakehouse: Any,
    profile_path: str,
    contract: dict[str, Any],
    fail_on_violation: bool = False,
    include_raw_values: bool = False,
) -> dict[str, Any]:
    """Check a saved ``.shape`` profile against a contract (COMPLETION_PLAN section 12.3).

    The observed values of violations about classified columns are withheld unless
    ``include_raw_values`` is true, in the result and in the error ``fail_on_violation`` raises.
    """
    profile = _load_profile(lakehouse, profile_path)
    result = shape.check(profile, contract)
    violations = json_safe(list(result.violations))
    if not include_raw_values:
        violations = redact_entries(violations, classified_columns(profile))
    violations = [cap_values(v) for v in violations]
    # Rules a safe-capture profile cannot answer (its values were left out): the check does not
    # pass, and saying why is the whole answer when there is no violation. Entries name a column
    # and a rule, never a value.
    gaps = json_safe(list(result.not_evaluable))
    if fail_on_violation and not result.passed:
        raise _fail(
            _check_failure(violations, gaps),
            **bounded(
                {
                    "violations": violations[:MAX_LISTED],
                    "violationCount": len(violations),
                    "notEvaluable": gaps[:MAX_LISTED],
                    "notEvaluableCount": len(gaps),
                },
                droppable="violations",
            ),
        )
    return bounded(
        {
            "passed": bool(result.passed),
            "profilePath": _clean_path(profile_path),
            "violationCount": len(violations),
            "violations": violations[:MAX_LISTED],
            "notEvaluableCount": len(gaps),
            "notEvaluable": gaps[:MAX_LISTED],
            "truncated": len(violations) > MAX_LISTED or len(gaps) > MAX_LISTED,
        },
        droppable="violations",
    )


SAFE_CAPTURE_HINT = (
    "The profile was saved with the default safe capture, which leaves out the values these "
    "rules need: check a full-capture .shape, such as profileLakehouseFile writes to outputPath."
)


def _check_failure(violations: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> str:
    """The message of a failed check: the violations, then the rules that could not be
    evaluated and why (a check with no violation can still fail on those)."""
    message = f"Contract check failed with {len(violations)} violation(s)"
    if violations:
        message += f": {_describe(violations)}"
    if gaps:
        message += f"; {len(gaps)} rule(s) not evaluable: {_describe(gaps)}. {SAFE_CAPTURE_HINT}"
    return message


def diff_profiles(
    lakehouse: Any,
    baseline_path: str,
    current_path: str,
    fail_on_drift: bool = False,
    include_raw_values: bool = False,
) -> dict[str, Any]:
    """Diff two saved ``.shape`` profiles with the section 12.3 default thresholds.

    The ``baseline`` and ``current`` values of changes about columns classified in either
    profile are withheld unless ``include_raw_values`` is true, in the result and in the error
    ``fail_on_drift`` raises.
    """
    baseline = _load_profile(lakehouse, baseline_path)
    current = _load_profile(lakehouse, current_path)
    result = shape.diff(baseline, current)
    changes = json_safe(list(result.changes))
    if not include_raw_values:
        classified = classified_columns(baseline) | classified_columns(current)
        changes = redact_entries(changes, classified)
    changes = [cap_values(c) for c in changes]
    if fail_on_drift and result.drifted:
        # the same bound as the result: the error reaches the pipeline as its message and payload
        raise _fail(
            f"Drift detected: {len(changes)} change(s): {_describe(changes)}",
            **bounded(
                {"changes": changes[:MAX_LISTED], "changeCount": len(changes)},
                droppable="changes",
            ),
        )
    return bounded(
        {
            "drifted": bool(result.drifted),
            "baselinePath": _clean_path(baseline_path),
            "currentPath": _clean_path(current_path),
            "changeCount": len(changes),
            "changes": changes[:MAX_LISTED],
            "truncated": len(changes) > MAX_LISTED,
        },
        droppable="changes",
    )


def profile_data_frame(data: Any, include_raw_values: bool = False) -> dict[str, Any]:
    """Profile inline data (a pandas DataFrame). The request body is limited to 4 MB.

    The raw ``min`` and ``max`` of classified columns are withheld unless ``include_raw_values``
    is true.
    """
    started = time.perf_counter()
    if data is None or len(data.columns) == 0:
        raise _fail("data must contain at least one column")
    profile = shape.profile(data, name="inline")
    return _profile_result(
        profile,
        source="inline",
        elapsed=time.perf_counter() - started,
        output="",
        extra={"sampled": False},
        include_raw_values=include_raw_values,
    )


def generate_sample(domain: str, table: str, rows: int = 10000, seed: int = 42) -> Any:
    """Generate ``rows`` rows of ``table`` from an installed domain, as a pandas DataFrame.

    The domain and table must be installed names (letters, digits, underscores): they are looked
    up, never used to build a path or a statement. ``rows`` is capped at
    ``generation.MAX_SAMPLE_ROWS`` and the frame is cut to the leading rows whose JSON stays under
    ``generation.MAX_RESPONSE_BYTES`` (25 MB; the response limit is 30 MB). The same arguments
    always return the same rows.
    """
    started = time.perf_counter()
    try:
        frame = generation.sample_to_pandas(domain, table, rows, seed)
    except generation.GenerationRequestError as e:
        raise _fail(str(e)) from e
    log.info(
        "shape.udf generate domain=%s table=%s requested=%s returned=%s elapsed_seconds=%.3f",
        domain,
        table,
        rows,
        len(frame),
        time.perf_counter() - started,
    )
    return frame
