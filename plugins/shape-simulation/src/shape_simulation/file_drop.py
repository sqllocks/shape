"""File-drop simulator: an upstream source landing files over a date range (P6-04a).

For each time slot (a day, an hour or a quarter of an hour) of the range, the rows of each entity
whose time falls in the slot are written as partitioned files, with an optional manifest and
``_done`` flag; late arrivals, duplicates, backfills, restatements and multi-file drops are
optional anomalies. The random draws are made in a fixed order (per entity: per slot lateness,
then duplicates; then the backfill, then the restatements), so a seed reproduces a drop.

Tables are Arrow tables (see :mod:`shape_simulation._tables`); files are written through the
``shape.sinks`` plugins.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape_simulation import _tables as tb

LATE_SEQ = 900
"""Sequence number of the file that holds a partition's late arrivals."""
RESTATEMENT_SEQ = 980
BACKFILL_SEQ = 990


@dataclass
class FileDropConfig:
    """Configuration for :class:`FileDropSimulator`.

    Args:
        domain: Domain name used in path construction (e.g. ``"retail"``).
        base_path: Root directory for landing files (``Files/landing`` in a lakehouse).
        cadence: Drop cadence: ``"daily"``, ``"hourly"`` or ``"every_15m"``.
        date_range_start: Inclusive start date as ``YYYY-MM-DD``.
        date_range_end: Inclusive end date as ``YYYY-MM-DD``.
        partitioning: Partition folder template (``YYYY-MM-DD`` is replaced by the day).
        formats: File formats to write (``"parquet"``, ``"csv"``, ``"jsonl"``).
        file_naming: File name template with ``{domain}``, ``{entity}``, ``{dt}``, ``{seq}`` and
            ``{ext}``.
        entities: Restrict the drop to these tables; empty means all of them.
        manifest_enabled: Write a ``_manifest.json`` in each partition folder.
        done_flag_enabled: Write a ``_done`` sentinel in each partition folder.
        lateness_enabled: Move some rows to a later partition (late arrivals).
        lateness_probability: Per-row probability of arriving late.
        max_days_late: The most days a late row can be late.
        duplicates_enabled: Repeat some rows.
        duplicate_probability: Per-row probability of being repeated.
        backfill_enabled: Re-drop one historical partition.
        max_days_back: How far back a backfill can reach.
        restatement_enabled: Re-drop historical partitions with corrected numbers.
        restatement_probability: Per-slot probability of a restatement.
        restatement_max_correction_pct: Largest correction of a number (0.10 is +-10%).
        multi_file_enabled: Split each partition into several files.
        multi_file_chunks: Files per partition when multi-file is on.
        multi_file_checksum: Put each file's SHA-256 and size in the manifest (multi-file only).
        seed: Random seed.
    """

    domain: str = "default"
    base_path: str = "Files/landing"
    cadence: str = "daily"
    date_range_start: str = ""
    date_range_end: str = ""
    partitioning: str = "dt=YYYY-MM-DD"
    formats: list[str] = field(default_factory=lambda: ["parquet"])
    file_naming: str = "{domain}_{entity}_{dt}_{seq}.{ext}"
    entities: list[str] = field(default_factory=list)
    manifest_enabled: bool = True
    done_flag_enabled: bool = True
    lateness_enabled: bool = True
    lateness_probability: float = 0.10
    max_days_late: int = 3
    duplicates_enabled: bool = False
    duplicate_probability: float = 0.02
    backfill_enabled: bool = False
    max_days_back: int = 0
    restatement_enabled: bool = False
    restatement_probability: float = 0.05
    restatement_max_correction_pct: float = 0.10
    multi_file_enabled: bool = False
    multi_file_chunks: int = 4
    multi_file_checksum: bool = True
    seed: int = 42

    def __post_init__(self) -> None:
        tb.check_cadence(self.cadence)
        for fmt in self.formats:
            tb.check_format(fmt)
        if not self.formats:
            raise ValueError("formats must name at least one file format")
        if self.lateness_enabled and self.max_days_late < 1:
            raise ValueError("max_days_late must be at least 1 when lateness is enabled")


@dataclass
class FileDropResult:
    """Result of a file-drop run.

    Attributes:
        files_written: Every data file written (each once).
        manifest_paths: The ``_manifest.json`` files.
        done_flag_paths: The ``_done`` sentinel files.
        stats: Per entity: ``files``, ``rows_written`` (rows in the on-time partitions, after
            duplicates, without late arrivals) and ``formats``.
    """

    files_written: list[Path]
    manifest_paths: list[Path]
    done_flag_paths: list[Path]
    stats: dict[str, Any]

    def __repr__(self) -> str:
        return (
            f"FileDropResult(files={len(self.files_written)}, "
            f"manifests={len(self.manifest_paths)}, entities={list(self.stats.keys())})"
        )


class FileDropSimulator:
    """Simulate an upstream source dropping files on a cadence over a date range.

    Args:
        tables: ``{table name: table}`` (Arrow tables, pandas frames or a generation result).
        config: What to write where, and which anomalies to inject.
        correlation_id: Put this id in every manifest (a new id per manifest by default); the
            hybrid simulator passes its run id.

    Example::

        cfg = FileDropConfig(domain="retail", date_range_start="2024-01-01",
                             date_range_end="2024-01-31")
        result = FileDropSimulator(tables=generated.tables, config=cfg).run()
    """

    def __init__(
        self,
        tables: Any,
        config: FileDropConfig,
        *,
        correlation_id: str | None = None,
    ) -> None:
        self._tables = tb.as_tables(tables)
        self._config = config
        self._delta = tb.check_cadence(config.cadence)
        self._rng = np.random.default_rng(config.seed)
        self._correlation_id = correlation_id

    # ---- public API ---------------------------------------------------------------------

    def run(self) -> FileDropResult:
        """Execute the simulation and return what was written."""
        cfg = self._config
        slots = self._build_time_slots()
        entity_names = cfg.entities if cfg.entities else list(self._tables)

        files_written: list[Path] = []
        manifest_paths: list[Path] = []
        done_flag_paths: list[Path] = []
        stats: dict[str, Any] = {}

        for entity in entity_names:
            if entity not in self._tables:
                continue
            table = self._tables[entity]
            ts_col = tb.temporal_column(table)
            sliced = self._slice_by_slots(table, ts_col, slots)

            entity_files: list[Path] = []
            entity_rows = 0
            late: dict[dt.datetime, list[pa.Table]] = {}

            for slot_dt, slot_table in sliced.items():
                if slot_table.num_rows == 0:
                    continue
                partition_dir = self._partition_path(entity, slot_dt)
                partition_dir.mkdir(parents=True, exist_ok=True)

                if cfg.lateness_enabled and len(slots) > 1:
                    slot_table, late_rows = self._extract_late_rows(slot_table, slot_dt)
                    for late_dt, late_table in late_rows.items():
                        late.setdefault(late_dt, []).append(late_table)

                if cfg.duplicates_enabled and slot_table.num_rows:
                    slot_table = self._inject_duplicates(slot_table)

                if cfg.multi_file_enabled and slot_table.num_rows > cfg.multi_file_chunks > 1:
                    written: list[Path] = []
                    for index, chunk in enumerate(_split(slot_table, cfg.multi_file_chunks)):
                        written.extend(
                            self._write_data(chunk, entity, slot_dt, partition_dir, index + 1)
                        )
                else:
                    written = self._write_data(slot_table, entity, slot_dt, partition_dir)
                entity_files.extend(written)
                entity_rows += slot_table.num_rows

                if cfg.manifest_enabled:
                    manifest_paths.append(
                        self._write_manifest(
                            partition_dir,
                            written,
                            slot_dt,
                            entity,
                            include_checksums=cfg.multi_file_enabled and cfg.multi_file_checksum,
                        )
                    )
                if cfg.done_flag_enabled:
                    done = partition_dir / "_done"
                    done.write_text(tb.iso_utc_now(), encoding="utf-8")
                    done_flag_paths.append(done)

            # Late arrivals of every slot that land in one partition share one file, so none of
            # them replaces another.
            for late_dt, parts in late.items():
                late_dir = self._partition_path(entity, late_dt)
                entity_files.extend(
                    self._write_data(tb.concat(parts), entity, late_dt, late_dir, LATE_SEQ)
                )

            if cfg.backfill_enabled and cfg.max_days_back > 0 and slots:
                entity_files.extend(self._generate_backfills(entity, table, ts_col, slots))
            if cfg.restatement_enabled and slots:
                entity_files.extend(self._generate_restatements(entity, table, ts_col, slots))

            files_written.extend(entity_files)
            stats[entity] = {
                "files": len(entity_files),
                "rows_written": entity_rows,
                "formats": cfg.formats,
            }

        return FileDropResult(files_written, manifest_paths, done_flag_paths, stats)

    # ---- time slots ---------------------------------------------------------------------

    def _build_time_slots(self) -> list[dt.datetime]:
        cfg = self._config
        start = tb.parse_day(cfg.date_range_start, "date_range_start")
        end = tb.parse_day(cfg.date_range_end, "date_range_end")
        if end < start:
            raise ValueError("date_range_end is before date_range_start")
        slots: list[dt.datetime] = []
        current = start
        while current <= end:
            slots.append(current)
            current += self._delta
        return slots

    def _slice_by_slots(
        self, table: pa.Table, ts_col: str | None, slots: list[dt.datetime]
    ) -> dict[dt.datetime, pa.Table]:
        """The rows of each slot: by time when the table has a time column, else round robin."""
        if not slots:
            return {}
        n = table.num_rows
        if ts_col is None:
            assignment = np.arange(n) % len(slots)
            valid = np.ones(n, dtype=bool)
        else:
            stamps = tb.to_timestamps(table.column(ts_col))
            micros = pc.fill_null(stamps.cast(pa.int64()), 0).to_numpy(zero_copy_only=False)
            known = ~np.asarray(stamps.is_null().to_numpy(zero_copy_only=False), dtype=bool)
            origin = int(np.datetime64(slots[0], "us").astype("int64"))
            step = int(self._delta.total_seconds() * 1_000_000)
            assignment = (micros.astype(np.int64) - origin) // step
            valid = known & (assignment >= 0) & (assignment < len(slots))
        rows = np.flatnonzero(valid)
        order = rows[np.argsort(assignment[rows], kind="stable")]
        cuts = np.searchsorted(assignment[order], np.arange(len(slots) + 1))
        return {
            slot: table.take(pa.array(order[cuts[i] : cuts[i + 1]]))
            for i, slot in enumerate(slots)
        }

    def _slot_rows(
        self, table: pa.Table, ts_col: str | None, slots: list[dt.datetime], index: int
    ) -> pa.Table:
        """The rows of slot ``index`` alone (backfills and restatements)."""
        slot_dt = slots[index]
        if ts_col is not None:
            stamps = tb.to_timestamps(table.column(ts_col))
            return tb.take_mask(table, tb.window_mask(stamps, slot_dt, slot_dt + self._delta))
        chunk = max(1, table.num_rows // len(slots))
        return table.slice(index * chunk, chunk)

    # ---- writing ------------------------------------------------------------------------

    def _partition_path(self, entity: str, slot_dt: dt.datetime) -> Path:
        cfg = self._config
        folder = cfg.partitioning.replace("YYYY-MM-DD", slot_dt.strftime("%Y-%m-%d"))
        if cfg.cadence in ("hourly", "every_15m"):
            folder += f"/hr={slot_dt.strftime('%H')}"
            if cfg.cadence == "every_15m":
                folder += f"/m={slot_dt.strftime('%M')}"
        return Path(cfg.base_path) / cfg.domain / entity / folder

    def _write_data(
        self,
        table: pa.Table,
        entity: str,
        slot_dt: dt.datetime,
        partition_dir: Path,
        seq_start: int = 1,
    ) -> list[Path]:
        cfg = self._config
        partition_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for fmt in cfg.formats:
            dt_str = slot_dt.strftime("%Y-%m-%d")
            if cfg.cadence in ("hourly", "every_15m"):
                dt_str = slot_dt.strftime("%Y-%m-%dT%H%M")
            name = cfg.file_naming.format(
                domain=cfg.domain, entity=entity, dt=dt_str, seq=f"{seq_start:05d}", ext=fmt
            )
            path = partition_dir / name
            tb.write_table(table, path, fmt)
            written.append(path)
        return written

    def _write_manifest(
        self,
        partition_dir: Path,
        data_files: list[Path],
        slot_dt: dt.datetime,
        entity: str,
        include_checksums: bool = False,
    ) -> Path:
        manifest: dict[str, Any] = {
            "entity": entity,
            "domain": self._config.domain,
            "slot": slot_dt.isoformat(),
            "cadence": self._config.cadence,
            "files": [f.name for f in data_files],
            "file_count": len(data_files),
            "created_utc": tb.iso_utc_now(),
            "correlation_id": self._correlation_id or str(uuid.uuid4()),
        }
        if include_checksums:
            manifest["file_details"] = [
                {
                    "name": f.name,
                    "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                    "size_bytes": f.stat().st_size,
                }
                for f in data_files
                if f.exists()
            ]
        path = partition_dir / "_manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    # ---- anomalies ----------------------------------------------------------------------

    def _extract_late_rows(
        self, table: pa.Table, slot_dt: dt.datetime
    ) -> tuple[pa.Table, dict[dt.datetime, pa.Table]]:
        """Take out the rows that arrive late: the rest, and the late rows by the slot they
        land in (1 to ``max_days_late`` days after their own)."""
        cfg = self._config
        if table.num_rows == 0:
            return table, {}
        is_late = self._rng.random(table.num_rows) < cfg.lateness_probability
        late = tb.take_mask(table, is_late)
        remaining = tb.take_mask(table, ~is_late)
        if late.num_rows == 0:
            return remaining, {}
        delays = self._rng.integers(1, cfg.max_days_late + 1, size=late.num_rows)
        buckets: dict[dt.datetime, pa.Table] = {}
        for days in np.unique(delays):
            buckets[slot_dt + dt.timedelta(days=int(days))] = tb.take_mask(late, delays == days)
        return remaining, buckets

    def _inject_duplicates(self, table: pa.Table) -> pa.Table:
        picked = self._rng.random(table.num_rows) < self._config.duplicate_probability
        dupes = tb.take_mask(table, picked)
        if dupes.num_rows == 0:
            return table
        return tb.concat([table, dupes])

    def _generate_backfills(
        self, entity: str, table: pa.Table, ts_col: str | None, slots: list[dt.datetime]
    ) -> list[Path]:
        """Re-drop one historical partition (one of the first ``max_days_back`` slots)."""
        cfg = self._config
        if len(slots) <= cfg.max_days_back:
            return []
        index = int(self._rng.integers(0, min(cfg.max_days_back, len(slots))))
        rows = self._slot_rows(table, ts_col, slots, index)
        if rows.num_rows == 0:
            return []
        slot_dt = slots[index]
        return self._write_data(
            rows, entity, slot_dt, self._partition_path(entity, slot_dt), BACKFILL_SEQ
        )

    def _generate_restatements(
        self, entity: str, table: pa.Table, ts_col: str | None, slots: list[dt.datetime]
    ) -> list[Path]:
        """Re-drop some partitions with their numbers corrected (a restated fact table)."""
        cfg = self._config
        if len(slots) < 2:
            return []
        chosen = np.flatnonzero(self._rng.random(len(slots)) < cfg.restatement_probability)
        files: list[Path] = []
        for index in chosen:
            rows = self._slot_rows(table, ts_col, slots, int(index))
            if rows.num_rows == 0:
                continue
            first = rows.column_names[0]
            for name in rows.column_names:
                col_type = rows.schema.field(name).type
                numeric = pa.types.is_integer(col_type) or pa.types.is_floating(col_type)
                if not numeric or name.endswith("_id") or name == first:
                    continue
                correction = self._rng.uniform(
                    -cfg.restatement_max_correction_pct,
                    cfg.restatement_max_correction_pct,
                    size=rows.num_rows,
                )
                scaled = pc.multiply(rows.column(name).cast(pa.float64()), pa.array(1 + correction))
                rows = rows.set_column(rows.schema.get_field_index(name), name, scaled)
            rows = rows.append_column("_restatement", pa.array([True] * rows.num_rows))
            rows = rows.append_column(
                "_restated_at", pa.array([tb.iso_utc_now()] * rows.num_rows, pa.string())
            )
            slot_dt = slots[int(index)]
            files.extend(
                self._write_data(
                    rows, entity, slot_dt, self._partition_path(entity, slot_dt), RESTATEMENT_SEQ
                )
            )
        return files


def _split(table: pa.Table, parts: int) -> list[pa.Table]:
    """``table`` as ``parts`` consecutive pieces of nearly equal size (the first ones are one
    row larger when the rows do not divide evenly)."""
    size, extra = divmod(table.num_rows, parts)
    pieces: list[pa.Table] = []
    start = 0
    for i in range(parts):
        n = size + (1 if i < extra else 0)
        pieces.append(table.slice(start, n))
        start += n
    return pieces
