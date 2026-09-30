"""Shape as Fabric User Data Functions (DM-06).

Five functions, all camelCase parameters and ``dict`` results:

* ``profileLakehouseFile``   profile a CSV / Parquet / JSONL file in the lakehouse Files area
* ``profileLakehouseTable``  profile a lakehouse table through the SQL endpoint (row-capped)
* ``checkProfile``           check a saved ``.shape`` profile against a contract
* ``diffProfiles``           diff two saved ``.shape`` profiles
* ``profileDataFrame``       profile inline data (request limit 4 MB)

Design rules (COMPLETION_PLAN section 12.4, DM-5 and DM-6):

* The function host allows 240 s (100 s through the public endpoint), so heavy profiling
  is refused with a ``UserThrownError`` that points to the notebook path.
* ``fail*=True`` raises ``UserThrownError`` carrying the violations, so a pipeline Functions
  activity fails visibly.
* Every result is kept under 1 MB (the response limit is 30 MB).
"""

import io
import json
import logging
import math
import re
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import fabric.functions as fn
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.json as pajson
import pyarrow.parquet as pq

import shape

udf = fn.UserDataFunctions()
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


# ------------------------------------------------------------------------- helpers


def _fail(message: str, **properties: Any) -> fn.UserThrownError:
    return fn.UserThrownError(message, _json_safe(properties))


def _json_safe(obj: Any) -> Any:
    """Make a value strictly JSON-safe: no NaN/inf, no numpy scalars."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item") and not isinstance(obj, str | bytes):
        try:
            return _json_safe(obj.item())
        except (ValueError, TypeError):
            return str(obj)
    if obj is None or isinstance(obj, str | int | bool):
        return obj
    return str(obj)


def _bounded(result: dict[str, Any], droppable: str = "summary") -> dict[str, Any]:
    """Return ``result`` JSON-safe and under MAX_RESULT_BYTES, dropping ``droppable`` if needed."""
    result = _json_safe(result)
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


def _file_client(lakehouse: fn.FabricLakehouseClient, path: str) -> Any:
    return lakehouse.connectToFiles().get_file_client(_clean_path(path))


def _read_bytes(
    lakehouse: fn.FabricLakehouseClient, path: str, max_bytes: int | None = None
) -> bytes:
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


def _write_bytes(lakehouse: fn.FabricLakehouseClient, path: str, data: bytes) -> str:
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


def _save_profile(lakehouse: fn.FabricLakehouseClient, profile: Any, output_path: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "profile.shape"
        shape.save(profile, str(local))
        return _write_bytes(lakehouse, output_path, local.read_bytes())


def _load_profile(lakehouse: fn.FabricLakehouseClient, path: str) -> Any:
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
    return _bounded(
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


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def profileLakehouseFile(
    lakehouse: fn.FabricLakehouseClient,
    filePath: str,
    outputPath: str = "",
    maxMegabytes: int = 50,
) -> dict:
    """Profile a CSV, Parquet or JSONL file under the lakehouse Files area.

    ``filePath`` is relative to Files/. ``outputPath`` (optional) receives the ``.shape``
    artifact. Files above ``maxMegabytes`` are refused: use the notebook.
    """
    started = time.perf_counter()
    if maxMegabytes < 1:
        raise _fail("maxMegabytes must be at least 1")
    data = _read_bytes(lakehouse, filePath, max_bytes=int(maxMegabytes * 1_000_000))
    table = _table_from_bytes(data, filePath)
    profile = shape.profile(table, name=PurePosixPath(filePath).stem)
    saved = _save_profile(lakehouse, profile, outputPath) if outputPath else ""
    return _profile_result(
        profile,
        source=_clean_path(filePath),
        elapsed=time.perf_counter() - started,
        output=saved,
        extra={"sizeMegabytes": round(len(data) / 1e6, 3), "sampled": False},
    )


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def profileLakehouseTable(
    lakehouse: fn.FabricLakehouseClient,
    tableName: str,
    maxRows: int = 1000000,
    outputPath: str = "",
) -> dict:
    """Profile the first ``maxRows`` rows of a lakehouse table via its SQL endpoint.

    ``sampled`` is true when the table has more rows than ``maxRows``: the profile then covers
    only the first ``maxRows`` rows the endpoint returned, not a random sample.
    """
    started = time.perf_counter()
    if not _TABLE_NAME.match(tableName):
        raise _fail("tableName must be 'table' or 'schema.table' (letters, digits, underscore)")
    if not 1 <= maxRows <= MAX_TABLE_ROWS_CAP:
        raise _fail(f"maxRows must be between 1 and {MAX_TABLE_ROWS_CAP:,}. {NOTEBOOK_HINT}")
    quoted = ".".join(f"[{part}]" for part in tableName.split("."))
    conn = lakehouse.connectToSql()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP ({maxRows + 1}) * FROM {quoted}")
        columns = [d[0] for d in cursor.description]
        rows = [tuple(r) for r in cursor.fetchall()]
    except Exception as e:
        raise _fail(f"Could not read table {tableName!r} through the SQL endpoint: {e}") from e
    finally:
        conn.close()
    sampled = len(rows) > maxRows
    rows = rows[:maxRows]
    if len(rows) * max(len(columns), 1) > MAX_TABLE_CELLS:
        raise _fail(
            f"{len(rows):,} rows x {len(columns)} columns exceeds the {MAX_TABLE_CELLS:,}-cell "
            f"limit for User Data Functions. Lower maxRows. {NOTEBOOK_HINT}"
        )
    frame = pd.DataFrame.from_records(rows, columns=columns)
    profile = shape.profile(frame, name=tableName)
    saved = _save_profile(lakehouse, profile, outputPath) if outputPath else ""
    return _profile_result(
        profile,
        source=tableName,
        elapsed=time.perf_counter() - started,
        output=saved,
        extra={"sampled": sampled, "maxRows": maxRows},
    )


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def checkProfile(
    lakehouse: fn.FabricLakehouseClient,
    profilePath: str,
    contract: dict,
    failOnViolation: bool = False,
) -> dict:
    """Check a saved ``.shape`` profile against a contract (COMPLETION_PLAN section 12.3)."""
    profile = _load_profile(lakehouse, profilePath)
    result = shape.check(profile, contract)
    violations = _json_safe(list(result.violations))
    if failOnViolation and not result.passed:
        raise _fail(
            f"Contract check failed with {len(violations)} violation(s): {_describe(violations)}",
            violations=violations[:MAX_LISTED],
            violationCount=len(violations),
        )
    return _bounded(
        {
            "passed": bool(result.passed),
            "profilePath": _clean_path(profilePath),
            "violationCount": len(violations),
            "violations": violations[:MAX_LISTED],
            "truncated": len(violations) > MAX_LISTED,
        },
        droppable="violations",
    )


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def diffProfiles(
    lakehouse: fn.FabricLakehouseClient,
    baselinePath: str,
    currentPath: str,
    failOnDrift: bool = False,
) -> dict:
    """Diff two saved ``.shape`` profiles with the section 12.3 default thresholds."""
    baseline = _load_profile(lakehouse, baselinePath)
    current = _load_profile(lakehouse, currentPath)
    result = shape.diff(baseline, current)
    changes = _json_safe(list(result.changes))
    if failOnDrift and result.drifted:
        raise _fail(
            f"Drift detected: {len(changes)} change(s): {_describe(changes)}",
            changes=changes[:MAX_LISTED],
            changeCount=len(changes),
        )
    return _bounded(
        {
            "drifted": bool(result.drifted),
            "baselinePath": _clean_path(baselinePath),
            "currentPath": _clean_path(currentPath),
            "changeCount": len(changes),
            "changes": changes[:MAX_LISTED],
            "truncated": len(changes) > MAX_LISTED,
        },
        droppable="changes",
    )


@udf.function()
def profileDataFrame(data: pd.DataFrame) -> dict:
    """Profile inline data. The request body is limited to 4 MB."""
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
