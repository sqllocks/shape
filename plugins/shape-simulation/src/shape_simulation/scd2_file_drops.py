"""SCD Type 2 file-drop simulator: a full load, then daily deltas with versioned rows (P6-04a).

An initial snapshot is written first, then ``num_delta_days`` daily delta files. A delta holds
INSERT rows for new business entities and UPDATE pairs for changed ones: the expired old version
(``valid_to`` set, ``is_current`` false) and the new current version. Every row of the snapshot
and of the deltas carries the same columns in the same order: the table's own columns, the three
version columns (``valid_from``, ``valid_to``, ``is_current`` by default) and, in deltas,
``_delta_type``.

The random draws are made in the order the changes are made (which rows change, then how, then
the inserts), so a seed reproduces a run.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape_simulation import _tables as tb

DELTA_TYPE = "_delta_type"


@dataclass
class SCD2FileDropConfig:
    """Configuration for :class:`SCD2FileDropSimulator`.

    Args:
        domain: Domain name used in path construction (e.g. ``"retail"``).
        base_path: Root directory for landing files.
        business_key_column: Column that identifies the business entity.
        scd2_columns: Columns whose changes create a new version.
        effective_date_column: Name of the valid-from column.
        end_date_column: Name of the valid-to column.
        is_current_column: Name of the is-current flag column.
        initial_load_date: Date of the initial snapshot (``YYYY-MM-DD``).
        num_delta_days: Number of daily delta files.
        daily_change_rate: Fraction of the entities that change each day.
        daily_new_rate: Fraction of new entities each day (of the initial count).
        formats: File formats to write (``"parquet"``, ``"csv"``, ``"jsonl"``).
        manifest_enabled: Write a ``_manifest.json`` next to each drop.
        seed: Random seed.
    """

    domain: str = "default"
    base_path: str = "Files/landing"
    business_key_column: str = "id"
    scd2_columns: list[str] = field(default_factory=list)
    effective_date_column: str = "valid_from"
    end_date_column: str = "valid_to"
    is_current_column: str = "is_current"
    initial_load_date: str = "2024-01-01"
    num_delta_days: int = 30
    daily_change_rate: float = 0.05
    daily_new_rate: float = 0.02
    formats: list[str] = field(default_factory=lambda: ["parquet"])
    manifest_enabled: bool = True
    seed: int = 42

    def __post_init__(self) -> None:
        for fmt in self.formats:
            tb.check_format(fmt)
        if not self.formats:
            raise ValueError("formats must name at least one file format")
        if self.num_delta_days < 0:
            raise ValueError("num_delta_days must be 0 or more")


@dataclass
class SCD2FileDropResult:
    """Result of an SCD2 file-drop run.

    Attributes:
        initial_load_path: The initial full-load file (the first table's, first format).
        delta_paths: The daily delta files.
        manifest_paths: The ``_manifest.json`` files.
        stats: ``initial_rows``, ``total_deltas`` (delta rows), ``total_new``, ``total_updates``
            and ``days_simulated``.
    """

    initial_load_path: Path
    delta_paths: list[Path]
    manifest_paths: list[Path]
    stats: dict[str, Any]

    def __repr__(self) -> str:
        return (
            f"SCD2FileDropResult(initial={self.initial_load_path.name}, "
            f"deltas={len(self.delta_paths)}, stats={self.stats})"
        )


class SCD2FileDropSimulator:
    """Simulate an upstream source landing SCD2-versioned files over time.

    Args:
        tables: ``{entity name: table}`` (Arrow tables, pandas frames or a generation result).
            Every table is processed and needs the business key column.
        config: Paths and simulation parameters.

    Example::

        cfg = SCD2FileDropConfig(domain="retail", business_key_column="customer_id",
                                 scd2_columns=["loyalty_tier", "email"])
        result = SCD2FileDropSimulator(tables=generated.tables, config=cfg).run()
    """

    def __init__(self, tables: Any, config: SCD2FileDropConfig) -> None:
        self._tables = tb.as_tables(tables)
        self._config = config
        self._rng = np.random.default_rng(config.seed)
        self._nan_columns: set[str] = set()

    # ---- public API ---------------------------------------------------------------------

    def run(self) -> SCD2FileDropResult:
        cfg = self._config
        base_date = tb.parse_day(cfg.initial_load_date, "initial_load_date")
        delta_paths: list[Path] = []
        manifest_paths: list[Path] = []
        initial_load_path: Path | None = None
        initial_rows = new_total = update_total = delta_total = 0

        for entity, table in self._tables.items():
            if cfg.business_key_column not in table.column_names:
                raise ValueError(
                    f"table {entity!r} has no business key column {cfg.business_key_column!r}"
                )
            key_type = table.schema.field(cfg.business_key_column).type
            if not (pa.types.is_integer(key_type) or pa.types.is_floating(key_type)):
                raise ValueError(
                    f"the business key column {cfg.business_key_column!r} of table {entity!r} "
                    "must be numeric (new entities get the next integer key)"
                )
            if table.num_rows == 0:
                raise ValueError(f"table {entity!r} has no rows to version")
            schema = self._versioned_schema(table.schema)
            # A missing number or text draws like a NaN does; a missing date or flag does not.
            self._nan_columns = {
                f.name
                for f in table.schema
                if pa.types.is_integer(f.type)
                or pa.types.is_floating(f.type)
                or pa.types.is_string(f.type)
                or pa.types.is_large_string(f.type)
            }
            snapshot = self._with_versions(table, schema, base_date)
            init_path, init_manifests = self._write_initial_load(snapshot, entity, base_date)
            initial_load_path = initial_load_path or init_path
            manifest_paths.extend(init_manifests)
            initial_rows += table.num_rows

            state = self._initial_state(snapshot)
            versions: dict[Any, int] = {key: 1 for key in state}
            next_key = self._next_business_key(state)

            for day_offset in range(1, cfg.num_delta_days + 1):
                day = base_date + dt.timedelta(days=day_offset)
                rows, day_new, day_updates, next_key = self._daily_delta(
                    day, state, versions, next_key
                )
                if not rows:
                    continue
                delta = pa.Table.from_pylist(
                    rows, schema=schema.append(pa.field(DELTA_TYPE, pa.string()))
                )
                files, manifests = self._write_delta(delta, day, entity)
                delta_paths.extend(files)
                manifest_paths.extend(manifests)
                new_total += day_new
                update_total += day_updates
                delta_total += len(rows)

        stats: dict[str, Any] = {
            "initial_rows": initial_rows,
            "total_deltas": delta_total,
            "total_new": new_total,
            "total_updates": update_total,
            "days_simulated": cfg.num_delta_days,
        }
        return SCD2FileDropResult(
            initial_load_path=initial_load_path or Path(cfg.base_path),
            delta_paths=delta_paths,
            manifest_paths=manifest_paths,
            stats=stats,
        )

    # ---- schema and state ---------------------------------------------------------------

    def _version_columns(self) -> tuple[str, str, str]:
        cfg = self._config
        return cfg.effective_date_column, cfg.end_date_column, cfg.is_current_column

    def _versioned_schema(self, schema: pa.Schema) -> pa.Schema:
        """The table's columns, then the version columns it does not have, in a fixed order."""
        effective, end, current = self._version_columns()
        types = {effective: pa.timestamp("us"), end: pa.timestamp("us"), current: pa.bool_()}
        fields = [pa.field(f.name, types[f.name]) if f.name in types else f for f in schema]
        for name in (effective, end, current):
            if name not in schema.names:
                fields.append(pa.field(name, types[name]))
        return pa.schema(fields)

    def _with_versions(self, table: pa.Table, schema: pa.Schema, day: dt.datetime) -> pa.Table:
        """``table`` as the initial snapshot: every row current since ``day``."""
        effective, end, current = self._version_columns()
        n = table.num_rows
        columns: list[pa.Array] = []
        for f in schema:
            if f.name == effective:
                columns.append(pa.array([day] * n, pa.timestamp("us")))
            elif f.name == end:
                columns.append(pa.nulls(n, pa.timestamp("us")))
            elif f.name == current:
                columns.append(pa.array([True] * n, pa.bool_()))
            else:
                columns.append(table.column(f.name).combine_chunks())
        return pa.Table.from_arrays(columns, schema=schema)

    def _initial_state(self, snapshot: pa.Table) -> dict[Any, dict[str, Any]]:
        """The current row of each business key (a repeated key keeps its last row)."""
        key = self._config.business_key_column
        state: dict[Any, dict[str, Any]] = {}
        for row in snapshot.to_pylist():
            state[row[key]] = row
        return state

    @staticmethod
    def _next_business_key(state: dict[Any, dict[str, Any]]) -> int:
        """The next integer key for an insert: past the largest numeric key."""
        numeric = [
            k for k in state if isinstance(k, (int, float, np.integer)) and not isinstance(k, bool)
        ]
        if numeric:
            return int(max(numeric)) + 1
        return len(state) + 1

    # ---- daily delta --------------------------------------------------------------------

    def _daily_delta(
        self,
        day: dt.datetime,
        state: dict[Any, dict[str, Any]],
        versions: dict[Any, int],
        next_key: int,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """One day's delta rows (updates, then inserts), the counts, and the next free key."""
        cfg = self._config
        effective, end, current = self._version_columns()
        keys = list(state)
        rows: list[dict[str, Any]] = []

        count = min(max(1, int(len(keys) * cfg.daily_change_rate)), len(keys))
        chosen = self._rng.choice(keys, size=count, replace=False).tolist()
        for key in chosen:
            old = state[key]
            versions[key] = versions.get(key, 1) + 1
            expired = dict(old)
            expired[end] = day
            expired[current] = False
            expired[DELTA_TYPE] = "update"
            rows.append(expired)

            new = self._mutate_columns(dict(old), versions[key])
            new[effective] = day
            new[end] = None
            new[current] = True
            new[DELTA_TYPE] = "update"
            rows.append(new)
            state[key] = {k: v for k, v in new.items() if k != DELTA_TYPE}

        inserts = max(1, int(len(keys) * cfg.daily_new_rate))
        template = state[keys[0]]
        for _ in range(inserts):
            row = self._new_row(template, next_key, day)
            row[DELTA_TYPE] = "insert"
            rows.append(row)
            state[next_key] = {k: v for k, v in row.items() if k != DELTA_TYPE}
            versions[next_key] = 1
            next_key += 1
        return rows, inserts, count, next_key

    def _mutate_columns(self, row: dict[str, Any], version: int) -> dict[str, Any]:
        for col in self._config.scd2_columns:
            if col in row:
                row[col] = self._mutate_value(row[col], col, version)
        return row

    def _mutate_value(self, value: Any, col: str, version: int) -> Any:
        """A changed value: text gets a ``_v<N>`` suffix, a number is scaled by a factor in
        [0.8, 1.2], a flag flips; anything else stays. A missing value draws its factor like any
        other number would (so one seed gives one run), and stays missing."""
        if isinstance(value, bool):
            return not value
        if isinstance(value, str):
            base = value
            for i in range(version, 0, -1):
                suffix = f"_v{i}"
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            return f"{base}_v{version}"
        if value is None:
            if col in self._nan_columns:
                self._rng.uniform(0.8, 1.2)
            return None
        if isinstance(value, (int, float)):
            factor = float(self._rng.uniform(0.8, 1.2))
            mutated = value * factor
            return int(round(mutated)) if isinstance(value, int) else mutated
        return value

    def _new_row(self, template: dict[str, Any], key: int, day: dt.datetime) -> dict[str, Any]:
        """A new entity shaped like ``template``: strings get the key as a suffix, numbers are
        redrawn around the template's value, flags are drawn."""
        cfg = self._config
        effective, end, current = self._version_columns()
        row: dict[str, Any] = {}
        for col, value in template.items():
            if col == cfg.business_key_column:
                row[col] = key
            elif col == effective:
                row[col] = day
            elif col == end:
                row[col] = None
            elif col == current:
                row[col] = True
            elif col == DELTA_TYPE:
                continue
            elif isinstance(value, bool):
                row[col] = bool(self._rng.choice([True, False]))
            elif isinstance(value, str):
                row[col] = f"{value}_new{key}"
            elif isinstance(value, int):
                row[col] = int(self._rng.integers(1, max(value * 2, 10)))
            elif isinstance(value, float):
                row[col] = (
                    float(self._rng.uniform(0.5, 1.5) * value)
                    if value
                    else float(self._rng.uniform(0.0, 100.0))
                )
            elif value is None and col in self._nan_columns:
                self._rng.uniform(0.5, 1.5)
                row[col] = None
            else:
                row[col] = value
        return row

    # ---- files --------------------------------------------------------------------------

    def _write_initial_load(
        self, snapshot: pa.Table, entity: str, day: dt.datetime
    ) -> tuple[Path, list[Path]]:
        cfg = self._config
        folder = Path(cfg.base_path) / cfg.domain / entity / "initial"
        files = []
        for fmt in cfg.formats:
            path = folder / f"{entity}_initial.{fmt}"
            tb.write_table(snapshot, path, fmt)
            files.append(path)
        manifests = (
            [self._write_manifest(folder, files, day, entity)] if cfg.manifest_enabled else []
        )
        return files[0], manifests

    def _write_delta(
        self, delta: pa.Table, day: dt.datetime, entity: str
    ) -> tuple[list[Path], list[Path]]:
        cfg = self._config
        folder = (
            Path(cfg.base_path) / cfg.domain / entity / "delta" / f"dt={day.strftime('%Y-%m-%d')}"
        )
        files = []
        for fmt in cfg.formats:
            path = folder / f"{entity}_delta.{fmt}"
            tb.write_table(delta, path, fmt)
            files.append(path)
        manifests = (
            [self._write_manifest(folder, files, day, entity)] if cfg.manifest_enabled else []
        )
        return files, manifests

    def _write_manifest(
        self, directory: Path, data_files: list[Path], day: dt.datetime, entity: str
    ) -> Path:
        manifest = {
            "entity": entity,
            "domain": self._config.domain,
            "date": day.isoformat(),
            "files": [f.name for f in data_files],
            "file_count": len(data_files),
            "created_utc": tb.iso_utc_now(),
            "correlation_id": str(uuid.uuid4()),
        }
        path = directory / "_manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
        return path
