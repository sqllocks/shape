"""The versions of one name in a registry, in history order, and how a version is read."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from shape.errors import ShapeError
from shape.registry.local import LocalRegistry


class HistoryError(ShapeError, ValueError):
    """A history cannot be searched, or a result cannot be built from it (exit 2 on the command
    line): an unknown name or ref, a version that cannot be tested, a bad range."""


@dataclass(frozen=True, slots=True)
class Version:
    """One commit of a name. ``date`` is its ``business_date`` when it has one, else the UTC date
    of the commit; ``position`` is its place in the registry log."""

    position: int
    content_id: str
    date: date
    business_date: str | None
    created_at: float
    form: str | None = None


def _version_date(entry: dict[str, Any]) -> tuple[date, str | None]:
    given = (entry.get("metadata") or {}).get("business_date")
    if isinstance(given, str):
        try:
            return date.fromisoformat(given), given
        except ValueError:
            pass
    return datetime.fromtimestamp(float(entry.get("created_at", 0)), UTC).date(), None


class Versions:
    """Every version of ``name`` in the registry at ``registry``, ordered by ``business_date``
    (else commit time), and the profiles read from them (each one at most once)."""

    def __init__(self, registry: str | Path | LocalRegistry, name: str) -> None:
        if isinstance(registry, LocalRegistry):
            self.registry = registry
        else:
            root = Path(registry)
            if not (root / "logs").is_dir():
                raise HistoryError(
                    f"{root} is not a registry (it has no logs/ folder): `shape registry "
                    f"{root} commit {name} FILE.shape --allow-raw` creates one"
                )
            self.registry = LocalRegistry(root)
        self.name = name
        log = self.registry.log(name)
        if not log:
            raise HistoryError(f"no versions of {name!r} in the registry {self.registry.root}")
        found = []
        for position, entry in enumerate(log):
            day, business = _version_date(entry)
            created = float(entry.get("created_at", 0))
            found.append(
                Version(
                    position,
                    entry["content_id"],
                    day,
                    business,
                    created,
                    (entry.get("metadata") or {}).get("profile_form"),
                )
            )
        self.versions = sorted(found, key=lambda v: (v.date, v.created_at, v.position))
        self._order = {v.position: i for i, v in enumerate(self.versions)}
        self._tags: dict[str, str] | None = None
        self._cache: dict[str, Any] = {}
        self.reads = 0

    def index(self, version: Version) -> int:
        return self._order[version.position]

    def resolve(self, ref: str) -> int:
        """The index (in history order) of the version a ref names. A content id committed more
        than once names its newest commit."""
        content_id = self.registry.resolve(self.name, ref)
        same = [i for i, v in enumerate(self.versions) if v.content_id == content_id]
        if not same:
            raise HistoryError(f"{self.name}@{ref} is not recorded in the registry")
        return same[-1]

    def ref_of(self, version: Version) -> str:
        """A name for a version that ``--good`` and ``--bad`` accept: a tag, else the content id."""
        if self._tags is None:
            tags = self.registry.tags(self.name)
            self._tags = {}
            for tag in sorted(tags):
                self._tags.setdefault(tags[tag], tag)
        return self._tags.get(version.content_id, version.content_id)

    def describe(self, version: Version, ref: str | None = None) -> dict[str, Any]:
        return {
            "ref": ref if ref is not None else self.ref_of(version),
            "content_id": version.content_id,
            "business_date": version.business_date,
        }

    def profile(self, version: Version) -> Any:
        """The raw profile of a version. A share-safe profile, or anything that is not a profile,
        cannot be tested and is a :class:`HistoryError`."""
        found = self._cache.get(version.content_id)
        if found is not None:
            return found
        data = self.registry.checkout(self.name, version.content_id)
        label = f"{self.name}@{version.content_id[:12]} ({version.date})"
        if data[:4] == b"PK\x03\x04":
            import warnings

            import shape
            from shape.artifact.io import ArtifactError, ArtifactNotVerifiedWarning

            with tempfile.TemporaryDirectory(prefix="shape-history-") as work:
                path = Path(work) / "version.shape"
                path.write_bytes(data)
                try:
                    with warnings.catch_warnings():  # the registry addresses content by sha256
                        warnings.simplefilter("ignore", ArtifactNotVerifiedWarning)
                        profile = shape.load(path)
                except ArtifactError as exc:
                    raise HistoryError(f"{label} is not a profile: {exc}") from exc
        elif data.lstrip()[:1] == b"{":
            profile = self._read_json(data, label)
        else:
            raise HistoryError(f"{label} is not a profile")
        self.reads += 1
        self._cache[version.content_id] = profile
        return profile

    @staticmethod
    def _read_json(data: bytes, label: str) -> Any:
        try:
            doc = json.loads(data)
        except ValueError as exc:
            raise HistoryError(f"{label} is not a profile: {exc}") from exc
        if isinstance(doc, dict) and "redaction_manifest" in doc and "tables" in doc:
            raise HistoryError(
                f"{label} is a share-safe profile, which holds no values to compare: commit "
                "raw profiles (`shape registry ROOT commit NAME FILE.shape --allow-raw`; "
                "--contract needs a raw profile too)"
            )
        if isinstance(doc, dict) and doc.get("format") == "shape-profile":
            from shape.cli.profiles import read_export

            with tempfile.TemporaryDirectory(prefix="shape-history-") as work:
                path = Path(work) / "version.json"
                path.write_bytes(data)
                try:
                    return read_export(str(path))
                except ValueError as exc:
                    raise HistoryError(f"{label} is not a profile: {exc}") from exc
        raise HistoryError(f"{label} is not a profile")

    def safe_document(self, version: Version) -> dict[str, Any] | None:
        """The parsed share-safe profile of a version, or ``None`` when it is not one."""
        data = self.registry.checkout(self.name, version.content_id)
        if data.lstrip()[:1] != b"{":
            return None
        try:
            doc = json.loads(data)
        except ValueError:
            return None
        ok = isinstance(doc, dict) and "redaction_manifest" in doc and "tables" in doc
        return doc if ok else None
