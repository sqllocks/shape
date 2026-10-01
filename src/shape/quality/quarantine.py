"""Quarantine: set aside the files and tables that failed a validation gate.

Layout::

    <quarantine_root>/<domain>/<run_id>/
        <filename>
        <filename>._quarantine_meta.json

``domain``, ``run_id`` and table names become path components, so each must be a plain name
(letters, digits, ``.``, ``_`` and ``-``, never ``.`` or ``..``): a name cannot place a file
outside the quarantine root.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

META_SUFFIX = "._quarantine_meta.json"
_NAME = re.compile(r"[A-Za-z0-9._-]+")
_EXTENSIONS = {"parquet": ".parquet", "csv": ".csv", "jsonl": ".jsonl"}


@dataclass
class QuarantineEntry:
    """Metadata of one quarantined artifact."""

    original_path: str | None
    reason: str
    gate_name: str
    timestamp: str
    run_id: str
    table_name: str | None = None
    extra: dict[str, Any] | None = None


def _check_name(kind: str, value: str) -> str:
    if not _NAME.fullmatch(value) or value in (".", ".."):
        raise ValueError(f"invalid {kind} {value!r}: use letters, digits, '.', '_' and '-' only")
    return value


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


class QuarantineManager:
    """Copy failed files, or write failed tables, into a quarantine directory with metadata."""

    def __init__(self, domain: str = "default") -> None:
        self.domain = _check_name("domain", domain)

    def _run_dir(self, quarantine_root: str | Path, run_id: str) -> Path:
        return Path(quarantine_root) / self.domain / _check_name("run_id", run_id)

    @staticmethod
    def _write_meta(meta_path: Path, entry: QuarantineEntry) -> None:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(asdict(entry), fh, indent=2)

    def quarantine_file(
        self,
        source_path: str | Path,
        quarantine_root: str | Path,
        run_id: str,
        reason: str,
        gate_name: str = "unknown",
    ) -> Path:
        """Copy a file into quarantine with metadata; returns the quarantined copy."""
        source = Path(source_path)
        dest_dir = self._run_dir(quarantine_root, run_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        shutil.copy2(source, dest)
        entry = QuarantineEntry(
            original_path=str(source.resolve()),
            reason=reason,
            gate_name=gate_name,
            timestamp=datetime.now(UTC).isoformat(),
            run_id=run_id,
        )
        self._write_meta(dest_dir / f"{source.name}{META_SUFFIX}", entry)
        return dest

    def quarantine_table(
        self,
        table: pa.Table,
        quarantine_root: str | Path,
        run_id: str,
        table_name: str,
        reason: str,
        gate_name: str = "unknown",
        fmt: str = "parquet",
    ) -> Path:
        """Write an Arrow table to quarantine (``parquet``, ``csv`` or ``jsonl``) with
        metadata; returns the quarantined file."""
        if fmt not in _EXTENSIONS:
            raise ValueError(f"unsupported format {fmt!r}; choose from {sorted(_EXTENSIONS)}")
        _check_name("table name", table_name)
        dest_dir = self._run_dir(quarantine_root, run_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{table_name}{_EXTENSIONS[fmt]}"
        dest = dest_dir / filename
        if fmt == "csv":
            import pyarrow.csv as pacsv  # type: ignore[import-untyped]

            pacsv.write_csv(table, dest)
        elif fmt == "jsonl":
            with open(dest, "w", encoding="utf-8") as fh:
                for row in table.to_pylist():
                    fh.write(json.dumps(row, default=_json_default) + "\n")
        else:
            import pyarrow.parquet as pq  # type: ignore[import-untyped]

            pq.write_table(table, dest)
        entry = QuarantineEntry(
            original_path=None,
            reason=reason,
            gate_name=gate_name,
            timestamp=datetime.now(UTC).isoformat(),
            run_id=run_id,
            table_name=table_name,
            extra={"rows": table.num_rows, "columns": table.num_columns, "format": fmt},
        )
        self._write_meta(dest_dir / f"{filename}{META_SUFFIX}", entry)
        return dest

    def list_quarantined(self, quarantine_root: str | Path) -> list[dict[str, Any]]:
        """Metadata of every quarantined item under the root (all domains and runs)."""
        root = Path(quarantine_root)
        items: list[dict[str, Any]] = []
        if not root.exists():
            return items
        for meta_path in sorted(root.rglob(f"*{META_SUFFIX}")):
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (ValueError, OSError):
                continue
            artifact = meta_path.parent / meta_path.name[: -len(META_SUFFIX)]
            meta["quarantine_path"] = str(artifact)
            meta["exists"] = artifact.exists()
            items.append(meta)
        return items

    def get_quarantine_report(self, quarantine_root: str | Path, run_id: str) -> dict[str, Any]:
        """Summary of one run's quarantined artifacts, by the gate that triggered each."""
        run_items = [i for i in self.list_quarantined(quarantine_root) if i.get("run_id") == run_id]
        gates: dict[str, int] = {}
        for item in run_items:
            gate = item.get("gate_name", "unknown")
            gates[gate] = gates.get(gate, 0) + 1
        return {
            "run_id": run_id,
            "domain": self.domain,
            "total_quarantined": len(run_items),
            "gates_triggered": gates,
            "artifacts": run_items,
        }
