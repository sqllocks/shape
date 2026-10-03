"""Test helpers: a real run manifest with output files (importable from any test)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.scenario.manifest import ManifestBuilder, RunManifest

REPRO = {
    "schema_version": 1,
    "profile_version": 1,
    "seed": 42,
    "scale": "small",
    "shape_version": "0.9.0",
    "kernel": "python",
    "platform": "linux-x86_64",
}


def write_manifest(
    directory: Path,
    *,
    failed: bool = False,
    with_repro: bool = True,
    with_files: bool = True,
    run_id: str = "20261003_120000_retail_small_s42",
    name: str = "manifest.json",
) -> Path:
    """A run manifest as ``ManifestBuilder`` writes it, with two Parquet outputs."""
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        "customer": pa.table({"id": [1, 2, 3], "email": ["a@x.io", "b@x.io", "c@x.io"]}),
        "orders": pa.table({"id": [1, 2], "customer_id": [1, 2], "total": [9.5, 3.0]}),
    }
    info: dict[str, Any] = {}
    for tname, t in tables.items():
        rel = f"{tname}.parquet"
        if with_files:
            pq.write_table(t, directory / rel)
        info[tname] = {"rows": t.num_rows, "columns": t.num_columns, "file_paths": [rel]}
    m = RunManifest(
        run_id=run_id,
        spec_hash="",
        pack_id="retail",
        domain="retail",
        scale="small",
        seed=42,
        engine_version="0.9.0",
        tables=info,
        validation={"schema_conformance": True, "referential_integrity": not failed},
        timestamps={
            "started": "2026-10-03T12:00:00+00:00",
            "finished": "2026-10-03T12:00:05+00:00",
            "elapsed_seconds": 5.0,
        },
        reproducibility=dict(REPRO) if with_repro else {},
        dataset_id="sha256:" + "ab" * 32 if with_repro else "",
    )
    path = directory / name
    ManifestBuilder.to_file(m, path)
    return path
