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
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.json as pajson  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

import shape

__all__ = [
    "LAKEHOUSE_ALIAS",
    "UserThrownError",
    "bounded",
    "check_profile",
    "diff_profiles",
    "json_safe",
    "profile_data_frame",
    "profile_lakehouse_file",
    "profile_lakehouse_table",
]

log = logging.getLogger("shape.udf")

# Alias of the Lakehouse data connection added under "Manage connections" in the UDF item.
LAKEHOUSE_ALIAS = "shapeLakehouse"

MAX_RESULT_BYTES = 900_000  # results stay under 1 MB
MAX_LISTED = 100  # violations / changes returned inline
MAX_TABLE_ROWS_CAP = 5_000_000  # hard ceiling for maxRows
MAX_TABLE_CELLS = 30_000_000  # rows x columns guard for the table path
NOTEBOOK_HINT = (
    "Use the Shape profile notebook (integrations/fabric/notebooks/shape_profile.ipynb) "
    "for inputs this large; User Data Functions stop at 240 seconds."
)
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


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
    return UserThrownError(message, json_safe(properties))


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
            return pacsv.read_csv(buf)
        if suffix in (".jsonl", ".ndjson"):
            return pajson.read_json(buf)
    except Exception as e:
        raise _fail(f"Could not parse {path!r} as {suffix[1:]}: {e}") from e
    raise _fail(f"Unsupported file type {suffix!r}: use .csv, .parquet or .jsonl")


def _save_profile(lakehouse: Any, profile: Any, output_path: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "profile.shape"
        shape.save(profile, str(local))
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
    profile: Any, *, source: str, elapsed: float, output: str, extra: dict[str, Any]
) -> dict[str, Any]:
    summary = profile.summary()
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


# ----------------------------------------------------------------------- functions


def profile_lakehouse_file(
    lakehouse: Any,
    file_path: str,
    output_path: str = "",
    max_megabytes: int = 50,
) -> dict[str, Any]:
    """Profile a CSV, Parquet or JSONL file under the lakehouse Files area.

    ``file_path`` is relative to Files/. ``output_path`` (optional) receives the ``.shape``
    artifact. Files above ``max_megabytes`` are refused: use the notebook.
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
    )


def profile_lakehouse_table(
    lakehouse: Any,
    table_name: str,
    max_rows: int = 1000000,
    output_path: str = "",
) -> dict[str, Any]:
    """Profile the first ``max_rows`` rows of a lakehouse table via its SQL endpoint.

    ``sampled`` is true when the table has more rows than ``max_rows``: the profile then covers
    only the first ``max_rows`` rows the endpoint returned, not a random sample.
    """
    started = time.perf_counter()
    if not _TABLE_NAME.match(table_name):
        raise _fail("tableName must be 'table' or 'schema.table' (letters, digits, underscore)")
    if not 1 <= max_rows <= MAX_TABLE_ROWS_CAP:
        raise _fail(f"maxRows must be between 1 and {MAX_TABLE_ROWS_CAP:,}. {NOTEBOOK_HINT}")
    quoted = ".".join(f"[{part}]" for part in table_name.split("."))
    conn = lakehouse.connectToSql()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP ({max_rows + 1}) * FROM {quoted}")
        columns = [d[0] for d in cursor.description]
        rows = [tuple(r) for r in cursor.fetchall()]
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
    )


def check_profile(
    lakehouse: Any,
    profile_path: str,
    contract: dict[str, Any],
    fail_on_violation: bool = False,
) -> dict[str, Any]:
    """Check a saved ``.shape`` profile against a contract (COMPLETION_PLAN section 12.3)."""
    profile = _load_profile(lakehouse, profile_path)
    result = shape.check(profile, contract)
    violations = json_safe(list(result.violations))
    if fail_on_violation and not result.passed:
        raise _fail(
            f"Contract check failed with {len(violations)} violation(s): {_describe(violations)}",
            violations=violations[:MAX_LISTED],
            violationCount=len(violations),
        )
    return bounded(
        {
            "passed": bool(result.passed),
            "profilePath": _clean_path(profile_path),
            "violationCount": len(violations),
            "violations": violations[:MAX_LISTED],
            "truncated": len(violations) > MAX_LISTED,
        },
        droppable="violations",
    )


def diff_profiles(
    lakehouse: Any,
    baseline_path: str,
    current_path: str,
    fail_on_drift: bool = False,
) -> dict[str, Any]:
    """Diff two saved ``.shape`` profiles with the section 12.3 default thresholds."""
    baseline = _load_profile(lakehouse, baseline_path)
    current = _load_profile(lakehouse, current_path)
    result = shape.diff(baseline, current)
    changes = json_safe(list(result.changes))
    if fail_on_drift and result.drifted:
        raise _fail(
            f"Drift detected: {len(changes)} change(s): {_describe(changes)}",
            changes=changes[:MAX_LISTED],
            changeCount=len(changes),
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


def profile_data_frame(data: Any) -> dict[str, Any]:
    """Profile inline data (a pandas DataFrame). The request body is limited to 4 MB."""
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
    )
