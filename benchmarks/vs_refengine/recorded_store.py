"""Authenticated release recordings. Standard library only; never a best-effort load."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from paths import BASELINE_VERSION, HERE, RECORDED

SHAPE_VERSION = BASELINE_VERSION
LIMIT_BYTES = 50_000_000


class RecordingError(ValueError):
    """A workload has no trustworthy recording."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest(root: Path = RECORDED) -> dict[str, Any]:
    try:
        doc = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RecordingError("no recording: MANIFEST.json is missing") from None
    except (OSError, ValueError):
        raise RecordingError("MANIFEST.json is malformed or unreadable") from None
    if (
        not isinstance(doc, dict)
        or doc.get("format") != "shape-baseline-record"
        or type(doc.get("version")) is not int
        or doc.get("version") != 1
    ):
        raise RecordingError("MANIFEST.json: malformed format or version")
    if doc.get("shape_version") != SHAPE_VERSION:
        raise RecordingError(f"MANIFEST.json: expected shape_version {SHAPE_VERSION}")
    if (
        not isinstance(doc.get("files"), dict)
        or not isinstance(doc.get("fixtures"), dict)
        or not isinstance(doc.get("workloads"), list)
    ):
        raise RecordingError("MANIFEST.json: malformed files, fixtures or workloads")
    if not isinstance(doc.get("shape_commit"), str) or not re.fullmatch(
        r"[0-9a-f]{40}", doc["shape_commit"]
    ):
        raise RecordingError("MANIFEST.json: malformed release commit")
    if any(not isinstance(w, str) for w in doc["workloads"]) or len(set(doc["workloads"])) != len(
        doc["workloads"]
    ):
        raise RecordingError("MANIFEST.json: malformed or duplicate workloads")
    for name, entry in doc["fixtures"].items():
        safe_path(HERE, name)
        if (
            not name.startswith("fixtures/")
            or not isinstance(entry, dict)
            or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise RecordingError("MANIFEST.json: malformed fixture entry")
    if not isinstance(doc.get("recorded_utc"), str) or not isinstance(doc.get("machine"), dict):
        raise RecordingError("MANIFEST.json: malformed recording date or machine")
    if not isinstance(doc.get("baseline_freeze_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", doc["baseline_freeze_sha256"]
    ):
        raise RecordingError("MANIFEST.json: malformed baseline freeze digest")
    source = doc.get("source_release")
    if (
        not isinstance(source, dict)
        or source.get("version") != doc["shape_version"]
        or source.get("commit") != doc.get("shape_commit")
    ):
        raise RecordingError("MANIFEST.json: source release version/commit disagree")
    for name, entry in doc["files"].items():
        safe_path(root, name)
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            or not isinstance(entry.get("bytes"), int)
            or isinstance(entry["bytes"], bool)
            or entry["bytes"] < 0
        ):
            raise RecordingError(f"MANIFEST.json: malformed entry {name}")
    return doc


def safe_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or "\\" in name:
        raise RecordingError("MANIFEST.json: unsafe recording path")
    p = Path(name)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise RecordingError("MANIFEST.json: unsafe recording path")
    target = root / p
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise RecordingError("MANIFEST.json: recording escapes store")
    return target


def filename(kind: str, workload_id: str) -> str:
    identifier = workload_id.removeprefix(kind + ":")
    if kind == "timings":
        return "timings_" + identifier + ".json"
    if kind in ("generate", "stream"):
        identifier = identifier.replace(":", "_")
    elif kind == "db":
        identifier = identifier.replace(":mssql", ".mssql")
    identifier = identifier.replace("/", "_")
    return f"{kind}/{identifier}.json"


def load(
    kind: str, workload_id: str, *, root: Path = RECORDED, inputs: dict[str, str] | None = None
) -> dict[str, Any]:
    workload = workload_id if workload_id.startswith(kind + ":") else kind + ":" + workload_id
    name = filename(kind, workload_id)
    try:
        doc = manifest(root)
        entry = doc["files"].get(name)
        if entry is None:
            raise RecordingError(f"recorded/{name}: no recording")
        p = safe_path(root, name)
        if not p.is_file():
            raise RecordingError(f"recorded/{name}: missing file")
        if sha256(p) != entry["sha256"]:
            raise RecordingError(f"recorded/{name}: digest mismatch")
        if p.stat().st_size != entry["bytes"]:
            raise RecordingError(f"recorded/{name}: size mismatch")
        record = json.loads(p.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or type(record.get("version")) is not int
            or record.get("version") != 1
            or record.get("format") != "shape-" + kind + "-record"
            or record.get("workload") != workload
            or record.get("shape_version") != SHAPE_VERSION
            or record.get("shape_commit") != doc["shape_commit"]
        ):
            raise RecordingError(f"recorded/{name}: recording metadata mismatch")
        if inputs is not None and record.get("inputs") != inputs:
            raise RecordingError(f"recorded/{name}: input digest mismatch")
        if workload not in doc["workloads"]:
            raise RecordingError(f"recorded/{name}: workload absent from manifest")
        return record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, RecordingError) else "malformed recording"
        raise RecordingError(f"{workload}: {message}") from None


def check_all(root: Path = RECORDED, *, fixture_root: Path = HERE) -> list[str]:
    try:
        doc = manifest(root)
    except RecordingError as exc:
        return [str(exc)]
    errors = []
    if not doc["files"]:
        errors.append("MANIFEST.json: empty files map")
    total = (root / "MANIFEST.json").stat().st_size
    for name, entry in doc["files"].items():
        p = safe_path(root, name)
        if not p.is_file():
            errors.append(f"recorded/{name}: missing file")
            continue
        total += p.stat().st_size
        if p.stat().st_size != entry["bytes"]:
            errors.append(f"recorded/{name}: size mismatch")
        if sha256(p) != entry["sha256"]:
            errors.append(f"recorded/{name}: digest mismatch")
    # The manifest cannot digest itself. The checked-in checker is code, not a recording.
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.name not in ("MANIFEST.json", "check.py")
    }
    for name in sorted(actual - set(doc["files"])):
        errors.append(f"{name}: not listed in manifest")
    if total >= LIMIT_BYTES:
        errors.append(f"recordings exceed budget: {total} bytes (must be under {LIMIT_BYTES})")
    for name, entry in doc["fixtures"].items():
        try:
            p = safe_path(fixture_root, name)
            if not p.is_file() or sha256(p) != entry["sha256"]:
                errors.append(f"{name}: fixture digest mismatch")
        except (OSError, KeyError, TypeError, RecordingError):
            errors.append(f"{name}: invalid fixture entry")
    return errors
