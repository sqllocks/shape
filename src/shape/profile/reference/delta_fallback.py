"""Read a Delta table with DuckDB ``delta_scan`` when delta-rs cannot (#53).

delta-rs cannot read a table that uses deletion vectors (Fabric Spark writes them by default)
and, before 1.x, column mapping. The ``deltalake`` reader stays the default; this module is
used only after it has refused a table, and only when ``duckdb`` (extra ``delta-fallback``) is
installed. DuckDB is never imported otherwise.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import pyarrow as pa  # type: ignore[import-untyped]

EXTRA = "delta-fallback"
INSTALL_HINT = f"pip install 'sqllocks-shape[{EXTRA}]'"

_NOT_SUPPORTED = (
    "reader feature",
    "reader version",
    "not yet supported",
    "column mapping",
    "deletion vector",
)


class UnsupportedDeltaFeature(ValueError):
    """The table uses a Delta feature the ``deltalake`` reader cannot read, and the fallback
    reader (DuckDB) is not available."""


def is_unsupported_feature_error(exc: BaseException) -> bool:
    """True when ``exc`` is delta-rs refusing a table's protocol (not an I/O or data error)."""
    try:
        from deltalake.exceptions import DeltaProtocolError
    except ImportError:  # pragma: no cover - deltalake is required to get here
        return False
    if not isinstance(exc, DeltaProtocolError):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in _NOT_SUPPORTED)


def _named_in(error: BaseException | None) -> list[str]:
    """The features delta-rs names in its refusal (``{'deletionVectors'}``)."""
    if error is None:
        return []
    if "has set these reader features" not in str(error):  # other refusals list what IS supported
        return []
    named = re.search(r"\{([^}]*)\}", str(error))
    return [f.strip().strip("'\"") for f in named.group(1).split(",") if f.strip()] if named else []


def reader_features(table: Any, error: BaseException | None = None) -> list[str]:
    """The reader features a table asks of its reader: the protocol's feature list, those the
    refusal ``error`` names, and ``columnMapping`` for a table that maps columns (a legacy
    protocol does not list it)."""
    features: list[str] = _named_in(error)
    try:
        features.extend(str(f) for f in (table.protocol().reader_features or []))
    except Exception:  # an unreadable protocol is reported by the original error
        pass
    try:
        mode = (table.metadata().configuration or {}).get("delta.columnMapping.mode", "none")
    except Exception:
        mode = "none"
    if mode != "none" and "columnMapping" not in features:
        features.append("columnMapping")
    return sorted(set(features))


def never_read_by_delta_rs(table: Any) -> list[str]:
    """The reader features of ``table`` that delta-rs must not be asked to read, because it does
    not refuse them: for a column-mapped table it returns the right number of rows with every
    column null (measured with deltalake 1.6.6; older releases raise instead)."""
    return [f for f in reader_features(table) if f == "columnMapping"]


def _features_text(features: list[str]) -> str:
    return ", ".join(features) if features else "an unsupported reader feature"


def unavailable_message(name: str, features: list[str], problem: str) -> str:
    return (
        f"{name}: the Delta table uses reader feature(s) {_features_text(features)}, which the "
        f"deltalake reader cannot read, and the DuckDB fallback is not available ({problem}). "
        f"Install it with: {INSTALL_HINT}"
    )


def notice(name: str, features: list[str]) -> str:
    return (
        f"{name}: the Delta table uses reader feature(s) {_features_text(features)} that "
        "deltalake cannot read; reading it with DuckDB delta_scan instead"
    )


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _azure_secret(target: str, opts: Mapping[str, str]) -> str | None:
    """A DuckDB ``CREATE SECRET`` for the Azure options ``DeltaSource`` derived for delta-rs."""
    host = urlparse(target).netloc.partition("@")[2]
    account = host.partition(".")[0]
    key = opts.get("azure_storage_account_key")
    sas = opts.get("azure_storage_sas_key")
    token = opts.get("azure_storage_token")
    if key:
        conn = f"DefaultEndpointsProtocol=https;AccountName={account};AccountKey={key};"
        return (
            f"CREATE OR REPLACE SECRET shape_delta (TYPE azure, CONNECTION_STRING {_quote(conn)})"
        )
    if sas:
        conn = f"AccountName={account};SharedAccessSignature={sas.lstrip('?')};"
        return (
            f"CREATE OR REPLACE SECRET shape_delta (TYPE azure, CONNECTION_STRING {_quote(conn)})"
        )
    if token:
        return (
            "CREATE OR REPLACE SECRET shape_delta (TYPE azure, PROVIDER access_token, "
            f"ACCESS_TOKEN {_quote(token)}, ACCOUNT_NAME {_quote(account)})"
        )
    return None


def _connect(duckdb: Any, target: str, storage_options: Mapping[str, str] | None) -> Any:
    con = duckdb.connect()
    try:
        try:
            con.execute("LOAD delta")
        except Exception:
            try:
                con.execute("INSTALL delta")
                con.execute("LOAD delta")
            except Exception as exc:
                raise UnsupportedDeltaFeature(
                    "DuckDB is installed but its delta extension cannot be loaded "
                    f"({exc}); run `INSTALL delta` in DuckDB once, on a machine that can "
                    "download extensions"
                ) from exc
        if urlparse(target).scheme in ("abfss", "abfs", "az", "azure"):
            con.execute("LOAD azure")  # a missing extension is fetched by autoinstall
            secret = _azure_secret(target, storage_options or {})
            if secret is not None:
                con.execute(secret)
        return con
    except BaseException:
        con.close()
        raise


def read_via_duckdb(
    target: str,
    *,
    version: int | None = None,
    columns: list[str] | None = None,
    storage_options: Mapping[str, str] | None = None,
    schema: pa.Schema | None = None,
) -> pa.Table:
    """The rows of the Delta table at ``target`` (latest, or ``version``) through ``delta_scan``.

    ``schema`` (the table's logical Arrow schema, from delta-rs) makes the column types match
    what delta-rs would return; without it DuckDB's own types are used.
    """
    try:
        import duckdb
    except ImportError as exc:
        raise UnsupportedDeltaFeature(
            f"reading this Delta table needs DuckDB: {INSTALL_HINT}"
        ) from exc
    con = _connect(duckdb, target, storage_options)
    try:
        select = (
            "*" if not columns else ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
        )
        args = _quote(target) + ("" if version is None else f", version={int(version)}")
        result = con.execute(f"SELECT {select} FROM delta_scan({args})")
        fetch = getattr(result, "to_arrow_table", None) or result.fetch_arrow_table
        table = fetch()
    finally:
        con.close()
    if not isinstance(table, pa.Table):  # a reader on newer DuckDB
        table = pa.Table.from_batches(list(table))
    return _like(table, schema)


def _like(table: pa.Table, schema: pa.Schema | None) -> pa.Table:
    """``table`` cast to the logical types of ``schema`` when the columns line up."""
    if schema is None or table.column_names != [f.name for f in schema if f.name in table.names]:
        return table
    wanted = pa.schema([schema.field(n).remove_metadata() for n in table.column_names])
    try:
        return table.cast(wanted)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return table


def fallback_read(
    name: str,
    target: str,
    delta: Any,
    error: BaseException | None,
    *,
    columns: list[str] | None = None,
    storage_options: Mapping[str, str] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    """Read the version ``delta`` (an open ``DeltaTable``) is at with DuckDB, after delta-rs
    refused it with ``error`` (``None`` when the table's features ruled delta-rs out). Prints the
    notice to stderr and returns the table and the provenance fields that record the fallback."""
    features = reader_features(delta, error)
    try:
        import duckdb  # noqa: F401
    except ImportError as exc:
        raise UnsupportedDeltaFeature(
            unavailable_message(name, features, "the duckdb package is not installed")
        ) from exc
    text = notice(name, features)
    print(f"shape: note: {text}", file=sys.stderr)
    try:
        schema = delta.schema().to_pyarrow()
    except Exception:
        schema = None
    table = read_via_duckdb(
        target,
        version=int(delta.version()),
        columns=columns,
        storage_options=storage_options,
        schema=schema,
    )
    return table, {"reader": "duckdb", "reader_features": features, "fallback_reason": text}
