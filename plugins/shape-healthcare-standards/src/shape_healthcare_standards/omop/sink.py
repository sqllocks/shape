"""The ``omop`` sink: payer tables to OMOP CDM v5.4 files, one file per table."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .. import common
from ..contract import ContractError
from .mapping import map_tables, to_arrow

FORMATS = ("csv", "parquet")


def write_omop(
    tables: Mapping[str, pa.Table | pa.RecordBatch],
    out_dir: str | Path,
    *,
    fmt: str = "csv",
    concept_map: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Map the payer ``tables`` to OMOP CDM v5.4 and write ``<table>.csv`` or ``<table>.parquet``.

    The ``member`` table is required: every other OMOP table hangs off PERSON. A table is
    written when the payer tables it derives from are present (so ``member`` alone gives
    ``person`` and ``location``, plus ``death`` when a death date exists). Returns the row
    count per OMOP table written. CSV has a header row, ISO dates, empty NULLs and UTF-8.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; use one of {list(FORMATS)}")
    ts = common.TableSet(tables)
    if not ts.has("member"):
        raise ContractError("the OMOP writer needs the member table (as the table or in tables=)")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mapped = {name: to_arrow(name, rows) for name, rows in map_tables(ts, concept_map).items()}
    staging = Path(tempfile.mkdtemp(prefix=".omop-", dir=out))
    try:
        for name, table in mapped.items():
            path = staging / f"{name}.{fmt}"
            if fmt == "csv":
                pacsv.write_csv(table, path)
            else:
                pq.write_table(table, path)
        for name in mapped:
            os.replace(staging / f"{name}.{fmt}", out / f"{name}.{fmt}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {name: t.num_rows for name, t in mapped.items()}


class OmopSink:
    """Writes OMOP CDM v5.4 tables into the directory ``uri``.

    Options: ``tables`` (companion payer tables by name), ``format`` (``csv`` or ``parquet``),
    ``concept_map`` (source value to standard concept id). Returns the total rows written.
    """

    name = "omop"
    schemes = ("file",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        ts = common.build_tables(table, batches, options.get("tables"))
        counts = write_omop(
            ts.tables,
            common.output_path(uri),
            fmt=str(options.get("format", "csv")),
            concept_map=options.get("concept_map"),
        )
        return sum(counts.values())
