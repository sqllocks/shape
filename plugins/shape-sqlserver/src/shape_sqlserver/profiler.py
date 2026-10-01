"""Database profiling: the catalog supplies exact keys, types and row counts; a sample of rows
supplies distributions.

The result is an ordinary Shape profile (a multi-table dataset profile), so everything that
reads profiles reads it. Statistics describe the **sample** (``sample_rows`` rows per table,
default 1000): ``null_rate``, ``cardinality_ratio`` and ``is_unique`` divide by the number of
rows actually sampled, and the profile records that number (``sampled_rows`` on every table,
``sampling`` on the dataset) so a reader can tell them from whole-table figures. The table's
``row_count`` is the catalog's.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import numpy as np

from .auth import Credentials, connect
from .catalog import Catalog, ColumnInfo, read_catalog, table_stem
from .sql import (
    DEFAULT_SAMPLE_ROWS,
    DEFAULT_SCHEMA,
    SqlServerError,
    register_converters,
    sql_type_to_dtype,
    top_query,
)

log = logging.getLogger("shape_sqlserver")

# A column is an enumeration when it has at most this many distinct values in the sample, or
# when distinct values are under 5% of the rows sampled, and in either case its values repeat:
# distinct values are at most this fraction of the sample's non-null values (P1-18). A unique
# column is never an enumeration.
ENUM_MAX_CARDINALITY = 50
ENUM_MAX_RATIO = 0.05
ENUM_MAX_DISTINCT_PER_VALUE = 0.5
UNIQUE_RATIO = 0.99


def fetch_sample(cursor: Any, schema: str, table: str, rows: int) -> dict[str, list[Any]]:
    """The first ``rows`` rows of a table as ``{column: values}`` (``None`` for NULL)."""
    cursor.execute(top_query(schema, table, rows))
    names = [str(d[0]) for d in cursor.description]
    data = cursor.fetchall()
    return {n: [row[i] for row in data] for i, n in enumerate(names)}


def _sample_all(
    cursor: Any, catalog: Catalog, sample_rows: int
) -> dict[str, dict[str, list[Any]] | None]:
    out: dict[str, dict[str, list[Any]] | None] = {}
    for t in catalog.tables:
        out[t.name] = None
        if sample_rows > 0:
            try:
                out[t.name] = fetch_sample(cursor, catalog.schema, t.name, sample_rows)
            except Exception as exc:  # one unreadable table must not stop the walk
                log.warning("Could not sample %s: %s", t.name, exc)
    return out


def _sample_std(values: np.ndarray[Any, Any]) -> float:
    """Sample standard deviation (n - 1), NaN for fewer than two values."""
    n = len(values)
    if n < 2:
        return float("nan")
    avg = values.sum(dtype=np.float64) / n
    return float(np.sqrt(((avg - values) ** 2).sum(dtype=np.float64) / (n - 1)))


def _profile_column(
    info: ColumnInfo,
    values: list[Any] | None,
    sample_size: int,
    pk_cols: list[str],
    fks: dict[str, str],
) -> Any:
    from shape.profile.reference.model import ColumnProfile

    dtype = sql_type_to_dtype(info.type_name)
    cardinality = 0
    null_count = 0
    min_val = max_val = mean_val = std_val = None
    present: list[Any] = []
    if values is not None:
        present = [v for v in values if v is not None]
        cardinality = len(set(present))
        null_count = len(values) - len(present)
        if dtype in ("integer", "float") and present:
            nums = np.asarray([float(v) for v in present], dtype=np.float64)
            min_val, max_val = float(nums.min()), float(nums.max())
            mean_val = float(nums.sum(dtype=np.float64) / len(nums))
            std_val = _sample_std(nums)
    denominator = max(sample_size, 1)  # the rows actually sampled, not the table's row count
    ratio = cardinality / denominator
    is_unique = ratio > UNIQUE_RATIO if cardinality > 0 else False
    is_enum = (
        cardinality > 0
        and (cardinality <= ENUM_MAX_CARDINALITY or ratio < ENUM_MAX_RATIO)
        and cardinality <= ENUM_MAX_DISTINCT_PER_VALUE * len(present)
        and not is_unique
    )
    enum_values: dict[str, float] | None = None
    if is_enum and dtype in ("string", "boolean") and values is not None:
        counts = Counter(present)
        total = sum(counts.values())
        ordered = sorted(counts.items(), key=lambda kv: -kv[1])  # stable: ties keep first seen
        enum_values = {
            (str(k).lower() if dtype == "boolean" else str(k)): v / total for k, v in ordered
        }
    return ColumnProfile(
        name=info.name,
        dtype=dtype,
        null_count=null_count,
        null_rate=null_count / denominator,
        cardinality=cardinality,
        cardinality_ratio=ratio,
        is_unique=is_unique,
        is_enum=is_enum,
        enum_values=enum_values,
        min_value=min_val,
        max_value=max_val,
        mean=mean_val,
        std=std_val,
        distribution=None,
        distribution_params=None,
        pattern=None,
        is_primary_key=info.name in pk_cols,
        is_foreign_key=info.name in fks,
        fk_ref_table=fks.get(info.name),
    )


def _arrow_table(sample: dict[str, list[Any]]) -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    arrays = []
    for values in sample.values():
        try:
            arrays.append(pa.array(values))
        except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
            arrays.append(pa.array([None if v is None else str(v) for v in values]))
    return pa.table(dict(zip(sample.keys(), arrays, strict=True)))


def _links_from_data(
    samples: dict[str, dict[str, list[Any]] | None],
) -> dict[str, dict[str, Any]]:
    """Foreign keys that profiling the sampled tables together finds between them."""
    from shape.api import profile as shape_profile

    tables = {name: _arrow_table(s) for name, s in samples.items() if s is not None}
    if not tables:
        return {}
    found = shape_profile(tables).to_dict()
    return {
        rel["name"]: {
            "child_table": rel["child"],
            "parent_table": rel["parent"],
            "child_columns": list(rel["child_columns"]),
            "parent_columns": list(rel["parent_columns"]),
        }
        for rel in found["relationships"]
    }


def _infer_keys_from_names(tables: dict[str, Any], links: dict[str, dict[str, Any]]) -> None:
    """Link ``orders.customer_id`` to a table ``customer`` (or ``dimcustomer``) by name.

    A column the data already links (``links``) keeps the data's parent."""
    by_lower = {t.lower(): t for t in tables}
    linked = {(link["child_table"], c) for link in links.values() for c in link["child_columns"]}
    for tname, tp in tables.items():
        for col in tp.columns:
            if (tname, col) in linked:
                continue
            low = col.lower()
            candidate = None
            if low.endswith("_id"):
                candidate = low.rsplit("_id", 1)[0]
            elif low.endswith("id") and len(low) > 2:
                candidate = low[:-2]
            elif low.endswith("key") and len(low) > 3:
                candidate = low[:-3]
                if candidate.endswith("_"):
                    candidate = candidate[:-1]
            if not candidate or candidate == table_stem(tname):
                continue
            for lookup in (
                candidate,
                "dim" + candidate,
                candidate.capitalize(),
                "Dim" + candidate.capitalize(),
            ):
                parent = by_lower.get(lookup.lower())
                if parent is not None:
                    links[f"fk_{tname}_{col}"] = {
                        "child_table": tname,
                        "parent_table": parent,
                        "child_columns": [col],
                        "parent_columns": tables[parent].primary_key[:1] or [col],
                    }
                    break


def _mark_foreign_keys(tables: dict[str, Any], links: dict[str, dict[str, Any]]) -> None:
    """Record every link on its child table (``detected_fks``) and child columns
    (``is_foreign_key``, ``fk_ref_table``)."""
    for link in links.values():
        child = tables.get(link["child_table"])
        if child is None:
            continue
        for col in link["child_columns"]:
            child.detected_fks[col] = link["parent_table"]
            column = child.columns.get(col)
            if column is not None:
                column.is_foreign_key = True
                column.fk_ref_table = link["parent_table"]


def _profile_tables(cursor: Any, catalog: Catalog, sample_rows: int) -> tuple[Any, dict[str, int]]:
    from shape.profile.reference.model import DatasetProfile, TableProfile

    samples = _sample_all(cursor, catalog, sample_rows)
    declared: dict[str, dict[str, str]] = {}
    for fk in catalog.foreign_keys.values():
        for col in fk.child_columns:
            declared.setdefault(fk.child_table, {})[col] = fk.parent_table

    profiles: dict[str, Any] = {}
    sampled: dict[str, int] = {}
    for t in catalog.tables:
        sample = samples[t.name]
        size = len(next(iter(sample.values()), [])) if sample is not None else 0
        sampled[t.name] = size
        fks = declared.get(t.name, {})
        columns = {
            c.name: _profile_column(
                c,
                sample.get(c.name) if sample is not None else None,
                size,
                t.primary_key,
                fks,
            )
            for c in t.columns
        }
        profiles[t.name] = TableProfile(
            name=t.name,
            row_count=t.row_count,
            columns=columns,
            primary_key=t.primary_key,
            detected_fks=dict(fks),
        )
        log.info("Profiled %s: %d columns, %d rows", t.name, len(columns), t.row_count)

    links: dict[str, dict[str, Any]] = {
        name: {
            "child_table": fk.child_table,
            "parent_table": fk.parent_table,
            "child_columns": fk.child_columns,
            "parent_columns": fk.parent_columns,
        }
        for name, fk in catalog.foreign_keys.items()
    }
    if not links:
        # No declared keys: the sampled data decides first, then names fill the columns the
        # data leaves unlinked.
        links.update(_links_from_data(samples))
        _infer_keys_from_names(profiles, links)
    _mark_foreign_keys(profiles, links)

    names = {t.name for t in catalog.tables}
    relationships = [
        {
            "name": name,
            "parent": fk["parent_table"],
            "child": fk["child_table"],
            "parent_columns": fk["parent_columns"],
            "child_columns": fk["child_columns"],
            "type": "one_to_many",
        }
        for name, fk in links.items()
        if fk["child_table"] in names
    ]
    return DatasetProfile(tables=profiles, relationships=relationships), sampled


def profile_database(
    connection_string: str | None = None,
    *,
    credentials: Credentials | None = None,
    connection: Any = None,
    schema: str = DEFAULT_SCHEMA,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    tables: list[str] | None = None,
    name: str | None = None,
) -> Any:
    """Profile one schema of a SQL Server, Azure SQL or Fabric SQL database.

    Pass ``connection`` (an open DB-API connection, which is reused and left open) or a
    ``connection_string`` with ``credentials`` (the default is the Azure CLI's account; use
    ``Credentials("sql")`` when the connection string holds a login). Returns a
    ``shape.profile.reference.Profile``.
    """
    if isinstance(sample_rows, bool) or not isinstance(sample_rows, int) or sample_rows < 0:
        raise SqlServerError(f"sample_rows must be a non-negative integer, got {sample_rows!r}")
    if connection is None:
        if not connection_string:
            raise SqlServerError("give a connection string or an open connection")
        conn = connect(connection_string, credentials)
    else:
        conn = connection
        register_converters(conn)
    cursor = conn.cursor()
    try:
        catalog = read_catalog(cursor, schema, tables)
        dataset, sampled = _profile_tables(cursor, catalog, sample_rows)
    finally:
        cursor.close()
        if connection is None:
            conn.close()
    from shape.profile.reference import Profile
    from shape.profile.reference.profile import dataset_to_dict

    data = dataset_to_dict(dataset)
    for table_name, rows in sampled.items():
        data["tables"][table_name]["sampled_rows"] = rows
    data["sampling"] = {
        "method": "first rows (SELECT TOP n)",
        "requested_rows": sample_rows,
        "note": (
            "null_count, cardinality, null_rate, cardinality_ratio, is_unique, is_enum, "
            "enum_values, min_value, max_value, mean and std describe the sampled rows "
            "(tables[...].sampled_rows), not the whole table; row_count is the catalog's."
        ),
    }
    return Profile(data, name=name or schema)


__all__ = ["fetch_sample", "profile_database"]
