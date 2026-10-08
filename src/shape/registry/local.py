"""Local-first immutable Shape registry."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shape import compat
from shape.errors import ShapeError
from shape.registry.layout import open_layout

LAYOUT = "layout.json"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONTENT_ID = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
PRUNE_LOCK = "prune.lock"
PRUNE_FORMAT = "shape-registry-prune"
PRUNE_VERSION = 1
#: how long a prune waits for the commits already writing to finish (seconds)
_WRITER_WAIT = 30.0
_NOT_A_CONTENT_ID = "not a content id (64 lowercase hexadecimal characters): left alone"


def _now() -> float:
    """The commit time (seconds since the epoch); the tests set it."""
    return time.time()


def _publish(tmp: str, target: Path) -> None:
    """Replace ``target`` by the finished temporary file ``tmp`` (same directory: atomic)."""
    os.replace(tmp, target)


def _cutoff(before: datetime | str) -> datetime:
    """``before`` as an aware UTC datetime: an aware datetime, an ISO 8601 timestamp with ``Z`` or
    an offset, or a date ``YYYY-MM-DD`` (midnight UTC). A naive datetime or timestamp is refused:
    it names no instant."""
    if isinstance(before, datetime):
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError(
                "before: a naive datetime names no instant: give it a timezone (e.g. tzinfo=UTC)"
            )
        return before.astimezone(UTC)
    if not isinstance(before, str):
        raise TypeError(
            f"before must be a datetime or an ISO 8601 string, not {type(before).__name__}"
        )
    if _DATE.fullmatch(before):
        try:
            return datetime.fromisoformat(before).replace(tzinfo=UTC)
        except ValueError:
            raise ValueError(f"before: not a date: {before!r}") from None
    try:
        return compat.parse_utc_iso(before)
    except ValueError:
        raise ValueError(
            f"before: {before!r} is not a UTC ISO 8601 timestamp (e.g. 2026-06-01T00:00:00Z) "
            "or a date (YYYY-MM-DD); a timestamp needs Z or an offset"
        ) from None


class RegistryError(ShapeError):
    """A registry name, ref or path is invalid, or a ref is not recorded for that name."""


class RawProfileError(RegistryError):
    """A raw profile (real values) was offered to a registry without ``allow_raw``."""


def _profile_document(data: bytes) -> dict[str, Any] | None:
    """The manifest of a ``.shape`` profile artifact, or the document of ``shape profile export``
    JSON, in ``data``; ``None`` for anything else. JSON is recognised in every encoding
    ``json.loads`` reads (UTF-8 with or without a byte-order mark, UTF-16, UTF-32), and the
    manifest of a zip is read within the artifact reader's size limit: a manifest that reader
    refuses (one over the limit) is a :class:`RegistryError`, never inflated (#283). Other bytes
    give ``None``, never an exception (#396)."""
    import io
    import zipfile

    if zipfile.is_zipfile(io.BytesIO(data)):
        from shape.artifact.io import ArtifactError, read_manifest_bytes

        try:
            manifest = json.loads(read_manifest_bytes(io.BytesIO(data)))
        except ArtifactError as exc:  # a manifest no Shape reader accepts: never inflate it (#283)
            raise RegistryError(f"not a Shape container: {exc}") from exc
        except (
            OSError,
            ValueError,
            KeyError,
            EOFError,
            RuntimeError,
            NotImplementedError,
            zipfile.BadZipFile,
        ):
            manifest = None  # RecursionError is a RuntimeError
        if isinstance(manifest, dict) and manifest.get("kind") == "profile":
            return manifest
    if not _starts_like_an_object(data):
        return None
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) and doc.get("format") == "shape-profile" else None


def profile_capture(data: bytes) -> str | None:
    """How the profile in ``data`` was captured (``"safe"`` or ``"full"``), or ``None`` when it is
    not a profile (see :func:`_profile_document`). A profile that does not say (written before
    capture modes existed) was captured full."""
    doc = _profile_document(data)
    if doc is None:
        return None
    capture = doc.get("capture")
    mode = capture.get("mode") if isinstance(capture, dict) else None
    return "safe" if mode == "safe" else "full"


def is_raw_profile(data: bytes) -> bool:
    """True for a raw profile: a full capture, as a ``.shape`` profile artifact or ``shape profile
    export`` JSON, which holds up to 500 real values per column.

    A safe capture (the default of ``shape profile``) is not raw, nor is the safe form
    (``shape profile safe``), nor anything else."""
    return profile_capture(data) == "full"


def _starts_like_an_object(data: bytes) -> bool:
    """Whether the first non-blank character of ``data``, in the encoding ``json.loads`` would
    detect, is ``{`` (so a large file of another kind is never parsed whole)."""
    from json import detect_encoding

    head = data[:256].decode(detect_encoding(data[:4]), errors="ignore")
    return head.lstrip("\ufeff \t\r\n")[:1] == "{"


_CONTENT_ID = re.compile(r"^[0-9a-f]{64}$")


def _content_id(path: Path) -> str:
    """The content id a ref or tag file holds; anything else is a corrupt ref."""
    h = path.read_text(encoding="utf-8").strip()
    if not _CONTENT_ID.fullmatch(h):
        raise RegistryError(f"{path} is corrupt: it does not hold a content id")
    return h


def json_object(data: bytes) -> dict[str, Any] | None:
    """The parsed JSON object in ``data``, or None when it is not one.

    Reads the bytes the way ``json.loads`` does, so a UTF-8 byte-order mark or a UTF-16 encoding
    cannot hide a document from the checks that look at its content."""
    if data[:4] == b"PK\x03\x04":
        return None
    try:
        doc = json.loads(data)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _check(kind: str, value: object) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise RegistryError(f"invalid registry {kind}: {value!r}")
    return value


class LocalRegistry:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        for x in ("objects", "refs", "tags", "logs"):
            (self.root / x).mkdir(parents=True, exist_ok=True)
        self._real_root = self.root.resolve()
        self.layout_version = open_layout(self.root, LAYOUT, "registry-layout", RegistryError)

    def _path(self, *parts: str) -> Path:
        """A path under the root; anything that resolves outside it raises RegistryError."""
        p = self.root.joinpath(*parts)
        if not p.resolve().is_relative_to(self._real_root):
            raise RegistryError(f"path escapes the registry root: {'/'.join(parts)}")
        return p

    def commit(
        self,
        name: str,
        data: str | bytes,
        metadata: dict[str, Any] | None = None,
        *,
        allow_raw: bool = False,
    ) -> str:
        """Store ``data`` under ``name`` and return its content id (the sha256 of the bytes).

        A raw profile holds real values, so it is refused unless ``allow_raw`` is true: commit the
        safe form (``shape profile safe``) instead."""
        _check("name", name)
        for other in self.names():
            if other != name and other.casefold() == name.casefold():
                raise RegistryError(
                    f"{name!r} differs only in case from {other!r}: they share a log and refs "
                    "on Windows and macOS"
                )
        log = self._path("logs", f"{name}.jsonl")
        self._path("refs", name)
        raw = data.encode() if isinstance(data, str) else data
        if not allow_raw and is_raw_profile(raw):
            raise RawProfileError(
                f"{name}: this is a raw profile: it holds real values from the data (up to 500 per "
                "column). Commit the safe form (`shape profile safe`), or pass allow_raw=True"
            )
        with self._writing():
            return self._commit(name, log, raw, metadata)

    def _commit(self, name: str, log: Path, raw: bytes, metadata: dict[str, Any] | None) -> str:
        h = hashlib.sha256(raw).hexdigest()
        now = _now()
        e = {
            "name": name,
            "content_id": h,
            "created_at": now,
            "created": compat.utc_iso(datetime.fromtimestamp(now, UTC)),
            "metadata": metadata or {},
        }
        try:  # before anything is written, so a bad value leaves no orphan object
            line = json.dumps(e, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RegistryError(f"{name}: the metadata is not JSON-serializable ({exc})") from exc
        p = self._path("objects", h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                os.replace(tmp, p)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        with log.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        self._write_ref(name, "latest", h)
        return h

    @staticmethod
    def _replace_text(directory: Path, filename: str, text: str) -> None:
        """Write ``directory/filename`` via a temporary file: a reader never sees it empty."""
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, directory / filename)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _write_ref(self, name: str, ref: str, h: str) -> None:
        _check("ref", ref)
        self._replace_text(self._path("refs", name), ref, h)

    def _read_id(self, path: Path, name: str, ref: str) -> str:
        text = path.read_text(encoding="utf-8").strip()
        if not _CONTENT_ID.fullmatch(text):
            raise RegistryError(
                f"{name}@{ref} is damaged: {path} is corrupt, it does not hold a content id "
                f"(point it at a version again with promote or tag)"
            )
        return text

    def resolve(self, name: str, ref: str = "latest") -> str:
        """Resolve a ref, tag or content hash that is recorded for ``name`` (SEC4)."""
        _check("name", name)
        _check("ref", ref)
        p = self._path("refs", name, ref)
        if p.is_file():
            return self._read_id(p, name, ref)
        t = self._path("tags", name, ref)
        if t.is_file():
            return self._read_id(t, name, ref)
        if any(e.get("content_id") == ref for e in self.log(name)):
            return ref
        raise RegistryError(f"{name}@{ref} is not recorded in the registry")

    def checkout(self, name: str, ref: str = "latest") -> bytes:
        h = self.resolve(name, ref)
        try:
            raw = self._path("objects", h).read_bytes()
        except FileNotFoundError:
            raise RegistryError(
                f"object {h} of {name}@{ref} is missing from {self.root / 'objects'}: restore it "
                "from a backup, or commit the content again"
            ) from None
        if hashlib.sha256(raw).hexdigest() != h:
            raise RegistryError(f"object {h} is corrupt: its content does not match its id")
        return raw

    def tag(self, name: str, tag: str, ref: str = "latest") -> str:
        with self._writing():
            h = self.resolve(name, ref)
            _check("tag", tag)
            self._path("tags", name, tag)
            self._replace_text(self._path("tags", name), tag, h)
            return h

    def promote(self, name: str, source: str, target: str) -> str:
        with self._writing():
            h = self.resolve(name, source)
            self._write_ref(name, target, h)
            return h

    def log(self, name: str) -> list[dict[str, Any]]:
        _check("name", name)
        p = self._path("logs", f"{name}.jsonl")
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if not isinstance(entry, dict):
                raise RegistryError(
                    f"{p}: line {i} is not a log entry (an interrupted write?); "
                    "remove or repair that line"
                )
            out.append(entry)
        return out

    def refs(self, name: str) -> dict[str, str]:
        _check("name", name)
        p = self._path("refs", name)
        if not p.exists():
            return {}
        # a .tmp- file is a write that was interrupted, never a ref (refs start alphanumeric)
        return {
            x.name: x.read_text(encoding="utf-8").strip()
            for x in p.iterdir()
            if _NAME.fullmatch(x.name)
        }

    def names(self) -> list[str]:
        """The names with at least one commit, sorted."""
        return sorted(p.name[: -len(".jsonl")] for p in self._path("logs").glob("*.jsonl"))

    def tags(self, name: str) -> dict[str, str]:
        """The tags of ``name`` and the content id each points at."""
        _check("name", name)
        p = self._path("tags", name)
        if not p.exists():
            return {}
        return {
            x.name: x.read_text(encoding="utf-8").strip()
            for x in sorted(p.iterdir())
            if _NAME.fullmatch(x.name)
        }

    def entry(self, name: str, ref: str = "latest") -> dict[str, Any]:
        """The newest log entry whose content is ``ref`` (a ref, tag or content id) of ``name``."""
        cid = self.resolve(name, ref)
        entries: list[dict[str, Any]] = self.log(name)
        for e in reversed(entries):
            if e.get("content_id") == cid:
                return dict(e)
        raise RegistryError(f"{name}@{ref} is not recorded in the registry")

    # ---- the lock shared by writers and prune -----------------------------------------------

    def _lock_info(self) -> str:
        return json.dumps({"pid": os.getpid(), "started": compat.utc_iso()}, sort_keys=True)

    @contextlib.contextmanager
    def _writing(self) -> Iterator[None]:
        """Mark a commit, tag or promote as writing, and refuse it while a prune runs.

        The writer announces itself (``.commit-<id>.lock`` at the root) before it looks for
        ``prune.lock``, and a prune takes ``prune.lock`` before it waits for the announced
        writers to finish, so at least one of the two always sees the other."""
        marker = self.root / f".commit-{uuid.uuid4().hex}.lock"
        marker.write_text(self._lock_info(), encoding="utf-8", newline="\n")
        try:
            lock = self.root / PRUNE_LOCK
            if lock.exists():
                raise RegistryError(
                    f"a prune of this registry is running ({lock} is held): retry when it has "
                    f"finished. If no prune is running (one was killed), delete {lock}"
                )
            yield
        finally:
            with contextlib.suppress(OSError):
                marker.unlink()

    @contextlib.contextmanager
    def _prune_lock(self) -> Iterator[None]:
        lock = self.root / PRUNE_LOCK
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise RegistryError(
                f"{lock} is held: another prune of this registry is running. If none is (one "
                f"was killed), delete {lock} and run the prune again"
            ) from None
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self._lock_info())
            self._wait_for_writers()
            yield
        finally:
            with contextlib.suppress(OSError):
                lock.unlink()

    def _wait_for_writers(self) -> None:
        deadline = time.monotonic() + _WRITER_WAIT
        while True:
            busy = sorted(self.root.glob(".commit-*.lock"))
            if not busy:
                return
            if time.monotonic() >= deadline:
                names = ", ".join(p.name for p in busy)
                raise RegistryError(
                    f"a commit is still writing to this registry ({names}) after "
                    f"{_WRITER_WAIT:g} seconds: run the prune again when it has finished. If no "
                    f"commit is running (one was killed), delete those files from {self.root}"
                )
            time.sleep(min(0.05, _WRITER_WAIT))

    # ---- pruning ----------------------------------------------------------------------------

    def prune(
        self,
        before: datetime | str,
        *,
        names: Sequence[str] | None = None,
        keep_last: int = 1,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove the log entries committed before ``before``, then the objects nothing points at.

        ``before`` is an aware ``datetime``, an ISO 8601 timestamp with ``Z`` or an offset, or a
        date ``YYYY-MM-DD`` (midnight UTC); a naive datetime raises ``ValueError``. An entry is
        removed when its ``created_at`` is strictly before the cutoff, unless it is one of the
        newest ``keep_last`` entries of its name, or a ref (``latest``, promoted refs) or a tag of
        its name points at its content id. ``names`` limits the logs pruned (an unknown name raises
        :class:`RegistryError`). An object is deleted only when no remaining log entry, ref or tag
        of any name points at it. Returns the ``shape-registry-prune`` report (version 1);
        ``dry_run`` computes it and changes nothing. Pruning cannot be undone."""
        cutoff = _cutoff(before)
        if isinstance(keep_last, bool) or not isinstance(keep_last, int):
            raise TypeError(f"keep_last must be an integer, not {type(keep_last).__name__}")
        if keep_last < 1:
            raise ValueError(
                f"keep_last must be at least 1 (the newest entry of a name is always kept), got "
                f"{keep_last}"
            )
        chosen = self._prune_names(names)
        if dry_run:
            return self._prune(cutoff, chosen, keep_last, dry_run=True)
        with self._prune_lock():
            return self._prune(cutoff, chosen, keep_last, dry_run=False)

    def _prune_names(self, names: Sequence[str] | None) -> list[str]:
        known = self.names()
        if names is None:
            return known
        wanted = [names] if isinstance(names, str) else list(names)
        if not wanted:
            raise ValueError("names is empty: give at least one name, or None for every name")
        for n in wanted:
            _check("name", n)
            if n not in known:
                listed = ", ".join(known) or "none yet"
                raise RegistryError(
                    f"nothing is recorded for {n!r} (names in this registry: {listed})"
                )
        return sorted(set(wanted))

    def _pointers(self, kind: str) -> dict[str, dict[str, str]]:
        """Every ref (``kind`` ``refs``) or tag (``tags``) of every name: name -> label -> id."""
        out: dict[str, dict[str, str]] = {}
        top = self._path(kind)
        for d in sorted(top.iterdir()) if top.is_dir() else []:
            if not d.is_dir():
                continue
            found = {}
            for f in sorted(d.iterdir()):
                if f.is_file() and not f.name.startswith(".tmp-"):
                    found[f.name] = f.read_text(encoding="utf-8", errors="replace").strip()
            out[d.name] = found
        return out

    def _read_log(
        self, name: str, skipped: list[dict[str, str]] | None
    ) -> list[tuple[str, str | None, float | None]]:
        """Each line of the log of ``name``: (the line, its content id, its ``created_at``).
        A line without a readable entry or a time is reported in ``skipped`` (when given)."""
        path = self._path("logs", f"{name}.jsonl")
        if not path.exists():
            return []
        out: list[tuple[str, str | None, float | None]] = []
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"logs/{name}.jsonl:{i}"
            try:
                e = json.loads(line)
            except ValueError:
                e = None
            cid = e.get("content_id") if isinstance(e, dict) else None
            if not isinstance(cid, str):
                if skipped is not None:
                    skipped.append(
                        {"path": where, "reason": "not a log entry this release reads: kept"}
                    )
                out.append((line, None, None))
                continue
            t = e.get("created_at") if isinstance(e, dict) else None
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                if skipped is not None:
                    skipped.append({"path": where, "reason": "no created_at: kept"})
                out.append((line, cid, None))
                continue
            out.append((line, cid, float(t)))
        return out

    def _prune(
        self, cutoff: datetime, chosen: list[str], keep_last: int, *, dry_run: bool
    ) -> dict[str, Any]:
        threshold = cutoff.timestamp()
        skipped: list[dict[str, str]] = []
        refs, tags = self._pointers("refs"), self._pointers("tags")
        report_names: dict[str, Any] = {}
        rewrites: list[tuple[Path, list[str]]] = []
        remaining: set[str] = set()
        for name in self.names():
            lines = self._read_log(name, skipped if name in chosen else None)
            if name not in chosen:
                remaining.update(cid for _, cid, _ in lines if cid)
                continue
            ref_ids = set(refs.get(name, {}).values())
            tag_ids = set(tags.get(name, {}).values())
            timed = [i for i, (_, cid, t) in enumerate(lines) if cid and t is not None]
            newest = set(timed[-keep_last:])
            because: dict[str, list[str]] = {"ref": [], "tag": [], "keep_last": []}
            kept: list[str] = []
            removed = 0
            for i, (line, cid, t) in enumerate(lines):
                if cid is None or t is None or t >= threshold:
                    kept.append(line)
                    if cid:
                        remaining.add(cid)
                    continue
                reasons = [
                    reason
                    for reason, holds in (
                        ("ref", cid in ref_ids),
                        ("tag", cid in tag_ids),
                        ("keep_last", i in newest),
                    )
                    if holds
                ]
                if not reasons:
                    removed += 1
                    continue
                kept.append(line)
                remaining.add(cid)
                for reason in reasons:
                    if cid not in because[reason]:
                        because[reason].append(cid)
            report_names[name] = {
                "entries_removed": removed,
                "entries_kept": len(kept),
                "kept_because": because,
            }
            if removed:
                rewrites.append((self._path("logs", f"{name}.jsonl"), kept))
        for pointers in (refs, tags):
            for found in pointers.values():
                remaining.update(found.values())
        doomed: list[tuple[str, Path, int]] = []
        for p in sorted(self._path("objects").iterdir()):
            if p.is_dir() or not _CONTENT_ID.fullmatch(p.name):
                skipped.append({"path": f"objects/{p.name}", "reason": _NOT_A_CONTENT_ID})
            elif p.name not in remaining:
                doomed.append((p.name, p, p.stat().st_size))
        report = {
            "format": PRUNE_FORMAT,
            "version": PRUNE_VERSION,
            "cutoff": compat.utc_iso(cutoff),
            "dry_run": dry_run,
            "names": report_names,
            "objects_removed": [cid for cid, _, _ in doomed],
            "bytes_freed": sum(size for _, _, size in doomed),
            "skipped": skipped,
        }
        if dry_run:
            return report
        for path, kept in rewrites:  # every log first ...
            _rewrite(path, kept)
        for _, p, _ in doomed:  # ... then the objects nothing points at any more
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        return report


def _rewrite(path: Path, lines: list[str]) -> None:
    """Write ``lines`` to a temporary file beside ``path``, then replace ``path`` with it."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(line + "\n" for line in lines))
            fh.flush()
            os.fsync(fh.fileno())
        _publish(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
