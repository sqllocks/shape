"""One side of a parity check: data, a profile, a profile export or a share-safe profile.

Every kind is reduced to the same small model (tables, columns, keys, relationships, and a
statistical *view* of each column), so the comparison does not care where a side came from.
What a kind cannot hold is ``None`` in the model, and the check that needs it reports
``not measured``.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.drift.engine import View, view_of_profile_column, view_of_safe_column

KINDS = ("data", "profile", "profile-export", "safe-profile")
_MAX_JSON_BYTES = 256 << 20


class ParityInputError(ValueError):
    """A side cannot be read, or is not something parity can compare (exit 2)."""


@dataclass(slots=True)
class ColumnSide:
    name: str
    dtype: str
    view: View
    key: bool = False  # a primary key, or a column that references one


@dataclass(slots=True)
class TableSide:
    name: str
    row_count: int
    columns: dict[str, ColumnSide] = field(default_factory=dict)
    primary_key: list[str] | None = None  # None: this kind does not record keys
    foreign_keys: dict[str, str] | None = None


@dataclass(slots=True)
class Side:
    kind: str
    content_id: str
    name: str
    path: str
    tables: dict[str, TableSide]
    relationships: list[dict[str, Any]] | None
    single_table: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "content_id": self.content_id,
            "name": self.name,
            "path": self.path,
            "tables": len(self.tables),
        }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_id(doc: Any) -> str:
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return _sha(text.encode("utf-8"))


def _profile_content_id(profile: Any) -> str:
    """The content id ``shape profile`` would give this profile (the hash of its body)."""
    from shape.artifact import codec

    return _sha(codec.dumps(profile.to_dict(), sort_keys=False))


def _side_of_profile(
    kind: str, content_id: str, path: str, profile: Any, relationships: bool = True
) -> Side:
    data = profile.to_dict()
    tables_doc = data["tables"] if profile.is_dataset else {profile.name: _one(profile)}
    tables: dict[str, TableSide] = {}
    for tname, t in tables_doc.items():
        rows = int(t["row_count"])
        pk = list(t.get("primary_key") or [])
        fks = dict(t.get("detected_fks") or {})
        cols = {
            c: ColumnSide(
                c,
                str(col["dtype"]),
                view_of_profile_column(col, rows),
                key=c in pk or c in fks,
            )
            for c, col in t["columns"].items()
        }
        tables[tname] = TableSide(tname, rows, cols, pk, fks)
    rels = list(data.get("relationships") or []) if profile.is_dataset else []
    return Side(
        kind,
        content_id,
        profile.name,
        path,
        tables,
        rels if relationships else None,
        single_table=not profile.is_dataset,
    )


def _one(profile: Any) -> dict[str, Any]:
    ((_, table),) = profile.tables.items()
    return table  # type: ignore[no-any-return]


def _side_of_safe(doc: dict[str, Any], path: str) -> Side:
    from shape.privacy.safe_profile import SafeProfile

    tables_doc = doc.get("tables")
    if not isinstance(tables_doc, dict) or not tables_doc:
        raise ParityInputError(f"{path}: a safe profile needs a non-empty 'tables' object")
    for tname, t in tables_doc.items():
        if not isinstance(t, dict) or not isinstance(t.get("row_count"), int):
            raise ParityInputError(f"{path}: table {tname!r} has no integer row_count")
        if not isinstance(t.get("columns", {}), dict):
            raise ParityInputError(f"{path}: table {tname!r}: columns must be an object")
    try:
        safe = SafeProfile.from_dict(doc)
    except (KeyError, TypeError, ValueError) as exc:
        raise ParityInputError(f"{path}: not a readable safe profile ({exc})") from exc
    tables: dict[str, TableSide] = {}
    for tname, t in safe.tables.items():
        raw = tables_doc[tname]
        known_keys = "primary_key" in raw or "detected_fks" in raw
        pk = list(t.primary_key)
        fks = dict(t.detected_fks)
        tables[tname] = TableSide(
            tname,
            t.row_count,
            {
                c: ColumnSide(
                    c,
                    col.dtype,
                    view_of_safe_column(col.to_dict(), t.row_count, primary_key=c in pk),
                    key=c in pk or c in fks,
                )
                for c, col in t.columns.items()
            },
            pk if known_keys else None,
            fks if known_keys else None,
        )
    rels = list(safe.relationships) if "relationships" in doc else None
    return Side(
        "safe-profile",
        _canonical_id(doc),
        str(doc.get("name") or Path(path).stem),
        path,
        tables,
        rels,
        single_table=len(tables) == 1 and not rels,
    )


def _read_json(path: Path) -> Any:
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ParityInputError(f"{path}: too large to read as a profile")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _is_profile_artifact(path: Path) -> bool:
    from shape.artifact.io import read_manifest_bytes

    try:
        manifest = json.loads(read_manifest_bytes(str(path)))
    except (OSError, ValueError, KeyError, AttributeError, zipfile.BadZipFile, RecursionError):
        return False
    return isinstance(manifest, dict) and manifest.get("kind") == "profile"


def _data_source(path: Path, dataset: bool) -> Any:
    from shape.profile.reference.sources import folder_is_one_table, folder_tables

    if not path.is_dir():
        if dataset:
            raise ParityInputError(
                f"--dataset needs a folder of table files, and {path} is not one"
            )
        return str(path)
    if dataset:
        tables = folder_tables(str(path))
        if not tables:
            raise ParityInputError(f"{path} holds no table files")
        return {n: str(p) for n, p in tables.items()}
    if not folder_is_one_table(str(path), None):
        raise ParityInputError(
            f"the files in {path} do not share their columns, so the folder is not one table: "
            "add --dataset to compare it as several tables (one per file)"
        )
    return str(path)


def load_side(path: str | os.PathLike[str], *, dataset: bool = False) -> Side:
    """Read ``path``: a ``.shape`` profile, a ``shape-profile`` export, a share-safe profile, or
    data (a file, or a folder; ``dataset`` reads one table per file). Raises
    :class:`ParityInputError` for anything else."""
    p = Path(path)
    if not p.exists():
        raise ParityInputError(f"{path}: no such file or folder")
    if p.is_file() and zipfile.is_zipfile(p) and p.suffix == ".shape":
        if not _is_profile_artifact(p):
            raise ParityInputError(f"{path} is a Shape artifact but not a profile")
        import shape

        profile = shape.load(str(p))
        return _side_of_profile("profile", _artifact_id(p, profile), str(p), profile)
    if p.is_file() and p.suffix.lower() == ".json":
        doc = _read_json(p)
        if isinstance(doc, dict):
            if doc.get("format") == "shape-profile":
                from shape.cli.profiles import read_export

                try:
                    profile = read_export(str(p))
                except ValueError as exc:
                    raise ParityInputError(str(exc)) from exc
                return _side_of_profile("profile-export", _canonical_id(doc), str(p), profile)
            if "redaction_manifest" in doc and "tables" in doc:
                return _side_of_safe(doc, str(p))
            raise ParityInputError(
                f"{path} is JSON but not a Shape profile (a `.shape` file, a "
                "`shape profile export` file or a `shape profile safe` file)"
            )
    if p.is_file() and p.suffix == ".shape":
        raise ParityInputError(f"{path} is not a readable Shape artifact")
    import shape

    source = _data_source(p, dataset)
    try:
        profile = shape.profile(source)
    except (OSError, ValueError, KeyError, ImportError) as exc:
        raise ParityInputError(f"{path}: cannot be read as data ({exc})") from exc
    if not profile.tables or all(not t["columns"] for t in profile.tables.values()):
        raise ParityInputError(f"{path}: holds no columns")
    return _side_of_profile("data", _profile_content_id(profile), str(p), profile)


def _artifact_id(path: Path, profile: Any) -> str:
    from shape.artifact.io import read_manifest_bytes

    try:
        cid = json.loads(read_manifest_bytes(str(path))).get("shape_content_id")
    except (OSError, ValueError, KeyError, AttributeError):
        cid = None
    return str(cid) if cid else _profile_content_id(profile)
