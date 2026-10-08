"""A run manifest as the adapters read it: run id, tables with schemas, outcome, tuple.

Only what the run manifest holds (``src/shape/scenario/manifest.py``) is used. Its reproducibility
tuple and ``dataset_id`` come from W1-03 and are absent from older manifests; then they are
empty here and the adapters leave them out.

The manifest records a column *count* per table, not column names, so a table's schema is read
from the schema of its output files (``file_paths``, relative to the manifest or absolute) when
they can still be read. A table whose files are gone has no schema, and an adapter omits the
schema facet for it rather than invent one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.scenario.manifest import ManifestBuilder


class ManifestError(ValueError):
    """The manifest file cannot be used (missing, not a manifest, or from a newer Shape)."""


@dataclass(frozen=True)
class TableView:
    name: str
    rows: int
    columns: tuple[tuple[str, str], ...]  # (name, Arrow type) pairs; empty when unknown
    file_paths: tuple[str, ...]


@dataclass(frozen=True)
class ManifestView:
    path: Path
    run_id: str
    tables: tuple[TableView, ...]
    failed: bool
    failed_gates: tuple[str, ...]
    started: str
    finished: str
    reproducibility: dict[str, Any] = field(default_factory=dict)
    dataset_id: str = ""
    engine_version: str = ""
    domain: str = ""
    scale: str = ""
    seed: int = 0


def _schema_of(files: list[Path]) -> tuple[tuple[str, str], ...]:
    for fp in files:
        try:
            if fp.suffix.lower() == ".parquet":
                import pyarrow.parquet as pq  # type: ignore[import-untyped]

                schema = pq.read_schema(fp)
            else:
                from shape.io import open_source

                schema = open_source(fp).schema
            return tuple((f.name, str(f.type)) for f in schema)
        except Exception:  # an unreadable file only means: no schema for this table
            continue
    return ()


def load(path: str | Path) -> ManifestView:
    """Read ``path``; raises :class:`ManifestError` with a reason a user can act on."""
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"manifest not found: {p}")
    try:
        m = ManifestBuilder.from_file(p)
    except (ValueError, OSError) as exc:  # includes JSON errors and ManifestVersionError
        raise ManifestError(str(exc)) from None
    if not m.run_id:
        raise ManifestError(f"{p} has no run_id; it is not a run manifest")
    tables = []
    for name, info in sorted(m.tables.items()):
        paths = [str(x) for x in info.get("file_paths", [])]
        found = [(fp if fp.is_absolute() else p.parent / fp) for fp in map(Path, paths)]
        tables.append(
            TableView(
                name=name,
                rows=int(info.get("rows", 0)),
                columns=_schema_of([f for f in found if f.is_file()]),
                file_paths=tuple(paths),
            )
        )
    failed = tuple(sorted(g for g, ok in m.validation.items() if not ok))
    return ManifestView(
        path=p,
        run_id=m.run_id,
        tables=tuple(tables),
        failed=bool(failed),
        failed_gates=failed,
        started=str(m.timestamps.get("started", "")),
        finished=str(m.timestamps.get("finished", "")),
        reproducibility=dict(m.reproducibility),
        dataset_id=m.dataset_id,
        engine_version=m.engine_version,
        domain=m.domain,
        scale=m.scale,
        seed=int(m.seed),
    )
