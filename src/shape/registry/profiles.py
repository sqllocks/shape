"""The profile registry: named, tagged Shape profiles under ``system/table/name`` (P6-10).

Layout::

    <root>/
      <system>/<table>/<name>.shape     one table profile, a normal Shape profile artifact
      _index.json                       maintained by save/delete/tag, rebuilt by ``reindex``

The description and tags travel in the artifact's manifest, so ``reindex`` rebuilds the index
from the files alone. This store holds Shape profiles only; it is not the content-addressed
``.shape`` artifact registry of :mod:`shape.registry.local` and the two are never merged.

Hardening over a plain directory of JSON files: every identity part is a plain name (it cannot
leave the root), files and the index are written atomically, ``save`` never replaces a profile
silently, and ``reindex`` / ``import_dir`` report what they skipped instead of dropping it.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

INDEX = "_index.json"
SUFFIX = ".shape"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProfileRegistryError(ShapeError, ValueError):
    """A bad identity, a missing or existing profile, or an unreadable store."""


def default_root() -> Path:
    env = os.environ.get("SHAPE_PROFILE_REGISTRY")
    return Path(env) if env else Path.home() / ".shape" / "profiles"


def _part(kind: str, value: object) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value) or value.endswith(SUFFIX):
        raise ProfileRegistryError(f"invalid {kind} {value!r}: use letters, digits, . _ - only")
    return value


def split_identity(identity: str) -> tuple[str, str, str]:
    parts = identity.split("/")
    if len(parts) != 3:
        raise ProfileRegistryError(f"invalid identity {identity!r}: expected system/table/name")
    return _part("system", parts[0]), _part("table", parts[1]), _part("name", parts[2])


def atomic_write_text(path: Path, text: str) -> None:
    _atomic_write(path, text.encode("utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class ProfileRegistry:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root) if root else default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._real_root = self.root.resolve()

    # -- paths ----------------------------------------------------------------

    def _path(self, identity: str) -> Path:
        system, table, name = split_identity(identity)
        p = self.root / system / table / f"{name}{SUFFIX}"
        if not p.resolve().is_relative_to(self._real_root):
            raise ProfileRegistryError(f"path escapes the registry root: {identity}")
        return p

    def _files(self) -> Iterator[Path]:
        yield from sorted(self.root.glob(f"*/*/*{SUFFIX}"))

    # -- index ----------------------------------------------------------------

    def _read_index(self) -> dict[str, dict[str, Any]]:
        p = self.root / INDEX
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ProfileRegistryError(f"{p} is not valid JSON ({e}); run `reindex`") from e
        if not isinstance(data, dict):
            raise ProfileRegistryError(f"{p} is not an index; run `reindex`")
        return data

    def _write_index(self, index: dict[str, dict[str, Any]]) -> None:
        body = json.dumps(index, indent=2, sort_keys=True, allow_nan=False) + "\n"
        _atomic_write(self.root / INDEX, body.encode("utf-8"))

    # -- artifacts --------------------------------------------------------------

    @staticmethod
    def _write(
        path: Path, table: dict[str, Any], name: str, description: str, tags: list[str]
    ) -> None:
        import hashlib
        import importlib

        from shape.artifact import codec
        from shape.artifact.io import write_artifact

        ref = importlib.import_module("shape.profile.reference.profile")

        body = codec.dumps(table, sort_keys=False)
        manifest = {
            "format": ref.ARTIFACT_FORMAT,
            "format_version": ref.ARTIFACT_FORMAT_VERSION,
            "kind": ref.ARTIFACT_KIND,
            "name": name,
            "shape_content_id": hashlib.sha256(body).hexdigest(),
            "registry": {"description": description, "tags": sorted(set(tags))},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=SUFFIX)
        os.close(fd)
        try:
            write_artifact(tmp, manifest, {ref.PROFILE_COMPONENT: body})
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @staticmethod
    def _manifest(path: Path) -> dict[str, Any]:
        from shape.artifact.io import read_manifest_bytes

        m = json.loads(read_manifest_bytes(path))
        if not isinstance(m, dict):
            raise ProfileRegistryError(f"{path} has no manifest")
        return m

    def _entry(self, system: str, table: str, name: str, path: Path) -> dict[str, Any]:
        import shape

        prof = shape.load(path)  # verifies the content id
        meta = self._manifest(path).get("registry") or {}
        data = prof.to_dict()
        return {
            "system": system,
            "table": table,
            "name": name,
            "description": str(meta.get("description", "")),
            "tags": [str(t) for t in meta.get("tags", [])],
            "source_rows": int(data.get("row_count", 0)),
            "path": str(path.relative_to(self.root)),
        }

    # -- CRUD -----------------------------------------------------------------

    def exists(self, identity: str) -> bool:
        return self._path(identity).is_file()

    def save(
        self,
        profile: Any,
        *,
        system: str,
        name: str,
        tags: list[str] | None = None,
        description: str = "",
        overwrite: bool = False,
    ) -> list[str]:
        """Store ``profile`` (a ``Profile``) as one entry per table; returns the identities.

        Nothing is written when any entry exists and ``overwrite`` is false."""
        _part("system", system)
        _part("name", name)
        tables = profile.tables
        for t in tables:
            _part("table", t)
        tag_list = [_part("tag", t) for t in (tags or [])]
        targets = {t: f"{system}/{t}/{name}" for t in tables}
        if not overwrite:
            taken = [i for i in targets.values() if self.exists(i)]
            if taken:
                raise ProfileRegistryError(
                    f"already in the registry: {', '.join(taken)} (use --overwrite to replace)"
                )
        index = self._read_index()
        for t, data in tables.items():
            identity = targets[t]
            path = self._path(identity)
            self._write(path, data, name, description, tag_list)
            index[identity] = self._entry(system, t, name, path)
        self._write_index(index)
        return list(targets.values())

    def load(self, identity: str) -> Any:
        import shape

        path = self._path(identity)
        if not path.is_file():
            raise ProfileRegistryError(f"profile not found: {identity}")
        return shape.load(path)

    def delete(self, identity: str) -> None:
        path = self._path(identity)
        if not path.is_file():
            raise ProfileRegistryError(f"profile not found: {identity}")
        path.unlink()
        for d in (path.parent, path.parent.parent):  # empty table and system folders go too
            with contextlib.suppress(OSError):
                d.rmdir()
        index = self._read_index()
        index.pop(identity, None)
        self._write_index(index)

    def entries(
        self,
        *,
        system: str | None = None,
        table: str | None = None,
        tags: list[str] | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = list(self._read_index().values())
        if system:
            rows = [e for e in rows if e["system"] == system]
        if table:
            rows = [e for e in rows if e["table"] == table]
        for t in tags or []:
            rows = [e for e in rows if t in e.get("tags", [])]
        if query:
            q = query.lower()
            rows = [
                e
                for e in rows
                if any(
                    q in str(e.get(k, "")).lower()
                    for k in ("name", "description", "system", "table")
                )
            ]
        return sorted(rows, key=lambda e: (e["system"], e["table"], e["name"]))

    def tag(self, identity: str, tags: list[str], *, remove: bool = False) -> list[str]:
        """Add (or remove) tags; returns the profile's tags afterwards."""
        for t in tags:
            _part("tag", t)
        path = self._path(identity)
        if not path.is_file():
            raise ProfileRegistryError(f"profile not found: {identity}")
        system, table, name = split_identity(identity)
        meta = self._manifest(path).get("registry") or {}
        current = {str(t) for t in meta.get("tags", [])}
        current = current - set(tags) if remove else current | set(tags)
        prof = self.load(identity)
        self._write(
            path, prof.tables[table], name, str(meta.get("description", "")), sorted(current)
        )
        index = self._read_index()
        index[identity] = self._entry(system, table, name, path)
        self._write_index(index)
        return sorted(current)

    # -- diff and maintenance -------------------------------------------------

    def diff(self, a: str, b: str) -> dict[str, Any]:
        """Column-by-column comparison: ``added``, ``removed`` and ``changed`` (per field)."""
        ta = self.load(a).tables[split_identity(a)[1]]
        tb = self.load(b).tables[split_identity(b)[1]]
        ca, cb = ta["columns"], tb["columns"]
        changed: dict[str, dict[str, Any]] = {}
        for col in sorted(set(ca) & set(cb)):
            fields = {
                k: {"from": ca[col].get(k), "to": cb[col].get(k)}
                for k in sorted(set(ca[col]) | set(cb[col]))
                if ca[col].get(k) != cb[col].get(k)
            }
            if fields:
                changed[col] = fields
        return {
            "added": sorted(set(cb) - set(ca)),
            "removed": sorted(set(ca) - set(cb)),
            "changed": changed,
            "rows": {"from": ta["row_count"], "to": tb["row_count"]},
        }

    def reindex(self) -> tuple[int, list[str]]:
        """Rebuild the index from the files: ``(profiles indexed, "path: reason" skipped)``."""
        index: dict[str, dict[str, Any]] = {}
        skipped: list[str] = []
        for path in self._files():
            rel = path.relative_to(self.root)
            system, table = rel.parts[0], rel.parts[1]
            name = path.name[: -len(SUFFIX)]
            try:
                _part("system", system)
                _part("table", table)
                _part("name", name)
                index[f"{system}/{table}/{name}"] = self._entry(system, table, name, path)
            except (OSError, ValueError, KeyError, ShapeError) as e:
                skipped.append(f"{rel}: {e}")
        self._write_index(index)
        return len(index), skipped

    def validate(self, identity: str | None = None) -> list[str]:
        """Problems found: unreadable or tampered profiles, and an index that disagrees with the
        files. An empty list means the store is sound."""
        problems: list[str] = []
        index = self._read_index()
        wanted = [identity] if identity else sorted(index)
        if identity:
            if not self.exists(identity):
                raise ProfileRegistryError(f"profile not found: {identity}")
        else:
            on_disk = {
                f"{p.relative_to(self.root).parts[0]}/{p.relative_to(self.root).parts[1]}/"
                f"{p.name[: -len(SUFFIX)]}"
                for p in self._files()
            }
            problems += [
                f"{i}: on disk but not in the index (run reindex)"
                for i in sorted(on_disk - set(index))
            ]
            wanted = sorted(on_disk | set(index))
        for i in wanted:
            try:
                path = self._path(i)
                if not path.is_file():
                    problems.append(f"{i}: in the index but the file is missing (run reindex)")
                    continue
                entry = self._entry(*split_identity(i), path)
                if identity is None and index.get(i) not in (None, entry):
                    problems.append(f"{i}: index entry differs from the file (run reindex)")
            except (OSError, ValueError, KeyError, ShapeError) as e:
                problems.append(f"{i}: {e}")
        return problems
