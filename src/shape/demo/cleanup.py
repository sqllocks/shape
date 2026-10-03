"""``CleanupEngine``: remove what a demo session created, and say exactly what happened.

Every artifact of the manifest ends up in one of three lists: ``removed`` (gone), ``failed`` (the
removal raised) or ``skipped`` (nothing to remove, or not safe to remove). An artifact is never
reported as removed unless it is.

Safety rules:

* A local folder is removed only when it lies inside a *session folder*: a folder the run made,
  marked by ``.shape-demo-session`` holding this session's id. A single local file (a chart, a
  semantic model) is removed only when it lies inside the output folder the manifest records.
* A database table is removed by its recorded ``schema.table`` with both parts quoted; the run
  records a table only after the destination accepted it (the default write mode refuses an
  existing table), so a table that was already there is never in the manifest.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.demo.connections import ConnectionProfile
from shape.demo.manifest import ArtifactRecord, DemoManifest

logger = logging.getLogger(__name__)

MARKER = ".shape-demo-session"
_QUALIFIED = re.compile(r"^(\w+)\.(\w+)$")
_KQL_NAME = re.compile(r"^[\w.-]+$")
_IN_MEMORY = ("synthetic", "generated")


@dataclass
class CleanupResult:
    """What a cleanup did: ``removed`` maps a target to the names removed."""

    removed: dict[str, list[str]] = field(default_factory=dict)
    failed: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {"removed": self.removed, "failed": self.failed, "skipped": self.skipped}


def mark_session_folder(folder: Path, session_id: str) -> None:
    """Make ``folder`` (and its parents) and write the marker that lets cleanup remove it."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MARKER).write_text(session_id + "\n", encoding="utf-8")


def _session_folder(path: Path, session_id: str) -> Path | None:
    """The folder that holds ``path`` and carries this session's marker, or ``None``."""
    for parent in (path, *path.parents):
        marker = parent / MARKER
        try:
            if marker.is_file() and marker.read_text(encoding="utf-8").strip() == session_id:
                return parent
        except OSError:
            return None
    return None


class CleanupEngine:
    def __init__(
        self, connection_profile: ConnectionProfile | None = None, *, services: Any = None
    ):
        self._conn = connection_profile
        self._services = services

    def _svc(self) -> Any:
        if self._conn is None:
            raise ValueError("no connection profile: give --connection to reach this target")
        if self._services is None:
            from shape.demo.services import FabricServices

            self._services = FabricServices(self._conn)
        return self._services

    def cleanup(self, manifest: DemoManifest, dry_run: bool = False) -> CleanupResult:
        result = CleanupResult()
        folders: set[Path] = set()
        for artifact in manifest.artifacts:
            target, name = artifact.target, artifact.name
            if target in _IN_MEMORY:
                result.skipped.append(
                    {
                        "target": target,
                        "name": name,
                        "reason": "held in memory only; nothing written",
                    }
                )
                continue
            if dry_run and target != "file":  # a remote target cannot be read without removing
                result.removed.setdefault(target, []).append(name)
                continue
            try:
                outcome = self._remove(manifest, artifact, folders, dry_run=dry_run)
            except Exception as exc:
                from shape.security.redact import redact_text

                message = redact_text(" ".join(str(exc).split()) or type(exc).__name__)
                logger.error("failed to remove %s %s: %s", target, name, message)
                result.failed.append({"target": target, "name": name, "error": message})
                continue
            if outcome is None:
                result.removed.setdefault(target, []).append(name)
            else:
                result.skipped.append({"target": target, "name": name, "reason": outcome})
        if not dry_run:
            for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
                _drop_session_folder(folder)
        return result

    def _remove(
        self,
        manifest: DemoManifest,
        artifact: ArtifactRecord,
        folders: set[Path],
        *,
        dry_run: bool = False,
    ) -> str | None:
        """Remove one artifact; ``None`` when removed, else the reason it was left alone. With
        ``dry_run`` a local file is judged by the same rules and left where it is."""
        target = artifact.target
        if target == "file":
            return self._remove_file(manifest, artifact, folders, dry_run=dry_run)
        if target in ("warehouse", "sql_db"):
            match = _QUALIFIED.match(artifact.detail or f"dbo.{artifact.name}")
            if match is None:
                raise ValueError(f"unsafe table name: {artifact.detail or artifact.name!r}")
            self._svc().drop_sql_table(target, match.group(1), match.group(2))
            return None
        if target == "lakehouse":
            path = artifact.detail
            if not path.startswith("onelake://"):
                return "no OneLake path was recorded for this table"
            self._svc().remove_files(path)
            return None
        if target == "eventhouse":
            if not _KQL_NAME.match(artifact.name):
                raise ValueError(f"unsafe table name: {artifact.name!r}")
            self._svc().drop_kql_table(artifact.name)
            return None
        return f"unknown target type {target!r}"

    def _remove_file(
        self,
        manifest: DemoManifest,
        artifact: ArtifactRecord,
        folders: set[Path],
        *,
        dry_run: bool = False,
    ) -> str | None:
        raw = artifact.detail or artifact.name
        path = Path(raw).resolve()
        folder = _session_folder(path, manifest.session_id)
        if folder is not None:
            folders.add(folder)
            if not path.exists():
                return "already gone"
            if dry_run:
                return None
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return None
        out = manifest.params.get("output_dir")
        if out and path.is_file() and Path(out).resolve() in path.parents:
            if not dry_run:
                path.unlink()
            return None
        if not path.exists():
            return "already gone"
        return "not inside a folder this session created"


def _drop_session_folder(folder: Path) -> None:
    """Remove the marker, then the folder itself when nothing else is in it."""
    try:
        (folder / MARKER).unlink(missing_ok=True)
        if not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        logger.warning("could not remove the session folder %s", folder)
