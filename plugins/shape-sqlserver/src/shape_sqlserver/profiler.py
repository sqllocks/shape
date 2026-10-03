"""Database profiling: the catalog supplies exact keys, types and row counts; a sample of rows
supplies distributions.

The result is an ordinary Shape profile (a multi-table dataset profile), so everything that
reads profiles reads it. Statistics describe the **sample** (``sample_rows`` rows per table,
default 1000, spread over the whole table: see :func:`fetch_sample`): ``null_rate``,
``cardinality_ratio`` and ``is_unique`` use the rows actually sampled, and the profile records
that number (``sampled_rows`` on every table, ``sampling`` on the dataset) so a reader can tell
them from whole-table figures. Where no row was sampled they are ``None`` (unknown). The table's
``row_count`` is the catalog's.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import numpy as np

from .auth import Credentials, connect
from .catalog import (
    Catalog,
    ColumnInfo,
    TableInfo,
    guess_primary_key,
    key_stem,
    read_catalog,
    table_stem,
)
from .sql import (
    DEFAULT_SAMPLE_ROWS,
    DEFAULT_SCHEMA,
    NOT_HASHABLE_TYPES,
    TABLES_QUERY,
    SqlServerError,
    fetch_dicts,
    register_converters,
    spread_query,
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
# ``is_unique`` needs at least this many non-null sampled values: one value is trivially distinct.
UNIQUE_MIN_VALUES = 2

# How a table was sampled (``tables[...].sample_method`` in the profile).
SAMPLE_ALL = "all rows"
SAMPLE_SPREAD = "checksum spread"
SAMPLE_FALLBACK = "first rows (fallback)"
SAMPLE_NONE = "none"

# Where a foreign key came from (``evidence`` of a relationship, ``fk_evidence`` of a column).
DECLARED, DATA, NAME = "declared", "data", "name"


def fetch_sample(
    cursor: Any,
    schema: str,
    table: str,
    rows: int,
    *,
    hash_columns: list[str] | None = None,
    tie_break: list[str] | None = None,
) -> dict[str, list[Any]]:
    """``rows`` rows of a table as ``{column: values}`` (``None`` for NULL).

    With ``hash_columns`` the rows are spread over the whole table and the same every time for
    the same data: ``SELECT TOP n * ... ORDER BY CHECKSUM(hash_columns)`` (one scan of the
    table; the server keeps only ``n`` rows). Without it, the first ``rows`` rows the server
    returns."""
    if hash_columns:
        cursor.execute(spread_query(schema, table, rows, hash_columns, tie_break))
    else:
        cursor.execute(top_query(schema, table, rows))
    names = [str(d[0]) for d in cursor.description]
    data = cursor.fetchall()
    return {n: [row[i] for row in data] for i, n in enumerate(names)}


def _read_sample(
    cursor: Any, catalog: Catalog, t: TableInfo, sample_rows: int
) -> tuple[dict[str, list[Any]], str]:
    if t.row_count <= sample_rows:  # the whole table: no choice to make
        return fetch_sample(cursor, catalog.schema, t.name, sample_rows), SAMPLE_ALL
    # A declared key is unique, so it spreads the sample; without one (a heap, a Fabric
    # warehouse) every column the server can hash is hashed, so duplicate keys do not cluster.
    hashed = t.primary_key or [
        c.name for c in t.columns if c.type_name.lower() not in NOT_HASHABLE_TYPES
    ]
    if hashed:
        try:
            sample = fetch_sample(
                cursor,
                catalog.schema,
                t.name,
                sample_rows,
                hash_columns=hashed,
                tie_break=t.primary_key,
            )
            return sample, SAMPLE_SPREAD
        except Exception as exc:  # e.g. a server that cannot ORDER BY CHECKSUM
            log.warning(
                "Could not spread the sample of %s (%s); reading its first rows", t.name, exc
            )
    return fetch_sample(cursor, catalog.schema, t.name, sample_rows), SAMPLE_FALLBACK


def _sample_all(
    cursor: Any, catalog: Catalog, sample_rows: int
) -> tuple[dict[str, dict[str, list[Any]] | None], dict[str, str]]:
    out: dict[str, dict[str, list[Any]] | None] = {}
    methods: dict[str, str] = {}
    for t in catalog.tables:
        out[t.name] = None
        methods[t.name] = SAMPLE_NONE
        if sample_rows > 0:
            try:
                out[t.name], methods[t.name] = _read_sample(cursor, catalog, t, sample_rows)
            except Exception as exc:  # one unreadable table must not stop the walk
                log.warning("Could not sample %s: %s", t.name, exc)
    return out, methods


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
    # No sampled row: the rates are unknown, not zero. Unique among the non-null sampled values,
    # and only claimed from at least UNIQUE_MIN_VALUES of them.
    known = values is not None and sample_size > 0
    non_null = len(present)
    is_unique: bool | None = None
    if known and non_null >= UNIQUE_MIN_VALUES:
        is_unique = cardinality / non_null > UNIQUE_RATIO
    is_enum = (
        cardinality > 0
        and (cardinality <= ENUM_MAX_CARDINALITY or ratio < ENUM_MAX_RATIO)
        and cardinality <= ENUM_MAX_DISTINCT_PER_VALUE * non_null
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
        null_rate=null_count / denominator if known else None,
        cardinality=cardinality,
        cardinality_ratio=ratio if known else None,
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


def _norm(name: str) -> str:
    return name.lower().replace("_", "")


def _infer_keys_from_names(
    catalog: Catalog, links: dict[str, dict[str, Any]], skip: set[tuple[str, str]]
) -> None:
    """Link ``orders.customer_id`` to a table ``customer`` (or ``dimcustomer``) by name.

    Only a column that ends in a whole ``id`` or ``key`` word counts (``customer_id``,
    ``CustomerId``, ``customerKey``; never ``paid`` or ``valid``). Columns in ``skip`` (already
    linked by a declared key or by the data) are left alone, and so is a table's own
    single-column declared key. The parent's columns are filled in later (``None`` here)."""
    by_norm = {_norm(t.name): t.name for t in catalog.tables}
    for t in catalog.tables:
        own_key = t.primary_key if len(t.primary_key) == 1 else []
        for c in t.columns:
            col = c.name
            if (t.name, col) in skip or col in own_key:
                continue
            candidate = key_stem(col)
            if candidate is None or _norm(candidate) == _norm(table_stem(t.name)):
                continue
            parent = by_norm.get(_norm(candidate)) or by_norm.get("dim" + _norm(candidate))
            if parent is not None:
                links[f"fk_{t.name}_{col}"] = {
                    "child_table": t.name,
                    "parent_table": parent,
                    "child_columns": [col],
                    "parent_columns": None,
                    "evidence": NAME,
                }


def _profile_tables(
    cursor: Any, catalog: Catalog, sample_rows: int
) -> tuple[Any, dict[str, int], dict[str, str], dict[tuple[str, str], str]]:
    from shape.profile.reference.model import DatasetProfile, TableProfile

    samples, methods = _sample_all(cursor, catalog, sample_rows)

    # Foreign keys. Declared keys are authoritative for the columns they cover. Beside them
    # (and when the schema declares none) the sampled data decides first, then names fill the
    # columns the data left unlinked. Every link records where it came from.
    links: dict[str, dict[str, Any]] = {
        name: {
            "child_table": fk.child_table,
            "parent_table": fk.parent_table,
            "child_columns": fk.child_columns,
            "parent_columns": fk.parent_columns,
            "evidence": DECLARED,
        }
        for name, fk in catalog.foreign_keys.items()
    }
    covered = {(lk["child_table"], c) for lk in links.values() for c in lk["child_columns"]}
    for name, lk in _links_from_data(samples).items():
        if any((lk["child_table"], c) in covered for c in lk["child_columns"]):
            continue
        links[name] = {**lk, "evidence": DATA}
        covered.update((lk["child_table"], c) for c in lk["child_columns"])
    _infer_keys_from_names(catalog, links, covered)

    fk_columns: dict[str, dict[str, str]] = {}
    evidence: dict[tuple[str, str], str] = {}
    for lk in links.values():
        for col in lk["child_columns"]:
            fk_columns.setdefault(lk["child_table"], {})[col] = lk["parent_table"]
            evidence[(lk["child_table"], col)] = lk["evidence"]

    profiles: dict[str, Any] = {}
    sampled: dict[str, int] = {}
    for t in catalog.tables:
        sample = samples[t.name]
        size = len(next(iter(sample.values()), [])) if sample is not None else 0
        sampled[t.name] = size
        fks = fk_columns.get(t.name, {})
        pk = t.primary_key or guess_primary_key(t.name, t.columns, sample, fks)
        columns = {
            c.name: _profile_column(
                c,
                sample.get(c.name) if sample is not None else None,
                size,
                pk,
                fks,
            )
            for c in t.columns
        }
        profiles[t.name] = TableProfile(
            name=t.name,
            row_count=t.row_count,
            columns=columns,
            primary_key=pk,
            detected_fks=dict(fks),
        )
        log.info("Profiled %s: %d columns, %d rows", t.name, len(columns), t.row_count)

    names = {t.name for t in catalog.tables}
    relationships = []
    for name, lk in links.items():
        if lk["child_table"] not in names:
            continue
        parent_columns = lk["parent_columns"]
        if parent_columns is None:  # name-inferred: the parent's key, else the column's name
            parent_columns = profiles[lk["parent_table"]].primary_key[:1] or lk["child_columns"]
        relationships.append(
            {
                "name": name,
                "parent": lk["parent_table"],
                "child": lk["child_table"],
                "parent_columns": parent_columns,
                "child_columns": lk["child_columns"],
                "type": "one_to_many",
                "evidence": lk["evidence"],
            }
        )
    return DatasetProfile(tables=profiles, relationships=relationships), sampled, methods, evidence


def _check_found(cursor: Any, catalog: Catalog, tables: list[str] | None) -> None:
    """An empty profile is never a success: a schema with no tables, or a requested table the
    schema does not have, is an error that lists what the schema does have."""
    found = {t.name for t in catalog.tables}
    missing = [t for t in tables or () if t not in found]
    if found and not missing:
        return
    every = sorted(r["table_name"] for r in _schema_tables(cursor, catalog.schema))
    if not every:
        raise SqlServerError(
            f"schema {catalog.schema!r} has no tables (or the login cannot see them); "
            "give the schema with --schema"
        )
    names = ", ".join(repr(t) for t in missing)
    raise SqlServerError(
        f"schema {catalog.schema!r} has no table {names}; its tables are: {', '.join(every)}"
    )


def _schema_tables(cursor: Any, schema: str) -> list[dict[str, Any]]:
    cursor.execute(TABLES_QUERY, (schema,))
    return fetch_dicts(cursor)


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
        _check_found(cursor, catalog, tables)
        dataset, sampled, methods, evidence = _profile_tables(cursor, catalog, sample_rows)
    finally:
        cursor.close()
        if connection is None:
            conn.close()
    from shape.profile.reference import Profile
    from shape.profile.reference.profile import dataset_to_dict

    data = dataset_to_dict(dataset)
    for table_name, rows in sampled.items():
        table = data["tables"][table_name]
        table["sampled_rows"] = rows
        table["sample_method"] = methods[table_name]
        for col_name, column in table["columns"].items():
            column["fk_evidence"] = evidence.get((table_name, col_name))
    data["sampling"] = {
        "method": (
            "spread over the table: SELECT TOP n ... ORDER BY a scrambled CHECKSUM(primary key, "
            "or every hashable column when there is no declared key); a table no larger than n "
            "is read whole"
        ),
        "requested_rows": sample_rows,
        "note": (
            "null_count, cardinality, null_rate, cardinality_ratio, is_unique, is_enum, "
            "enum_values, min_value, max_value, mean and std describe the sampled rows "
            "(tables[...].sampled_rows), not the whole table; row_count is the catalog's. "
            "null_rate, cardinality_ratio and is_unique are null where no row was sampled; "
            "is_unique means unique among the non-null sampled values (null below 2 of them). "
            "tables[...].sample_method says how each table was read."
        ),
    }
    return Profile(data, name=name or schema)


__all__ = ["fetch_sample", "profile_database"]
