"""Running the published test vectors (``docs/bridge/vectors``) against a bridge.

A vector file holds ``cases``: a ``request`` and the ``response`` that must come back. Strings may
contain ``${DIR}`` (a scratch directory that holds a copy of ``fixtures/``); ``"<any>"`` in a
response matches any value. A response matches when every key of the expected one is present with a
matching value (the bridge may add fields in a minor version, never remove one). ``setup`` requests
run first; ``jobs`` are job records placed in the jobs directory; ``needs: "fabric"`` runs the case
against the recorded Fabric interactions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

VECTOR_DIR = Path(__file__).resolve().parents[2] / "docs" / "bridge" / "vectors"
ANY = "<any>"
VOLATILE = (
    "elapsed_seconds",
    "throughput_rows_per_sec",
    "peak_rss_gb",
    "memory_peak_gb",
    "created_at",
    "updated_at",
    "threads",
    "processes",
    "spec_path",
    "content_id",
    "chunks",
    "parts_skipped",
    "summary",
    "proposed_at",
    "decided_at",
)


def substitute(value: Any, directory: str) -> Any:
    if isinstance(value, str):
        return value.replace("${DIR}", directory)
    if isinstance(value, list):
        return [substitute(v, directory) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, directory) for k, v in value.items()}
    return value


def abstract(value: Any, directory: str, key: str = "") -> Any:
    """A response as a vector stores it: the scratch directory as ``${DIR}``, volatile values as
    ``"<any>"``."""
    if key in VOLATILE:
        return ANY
    if key in ("job_id", "stream_id", "fabric_run_id") and isinstance(value, str):
        return value if value.startswith("job-0000000") else ANY
    if isinstance(value, str):
        return value.replace(directory, "${DIR}")
    if isinstance(value, list):
        return [abstract(v, directory) for v in value]
    if isinstance(value, dict):
        return {k: abstract(v, directory, k) for k, v in value.items()}
    return value


def matches(expected: Any, actual: Any, path: str = "$") -> list[str]:
    if expected == ANY:
        return []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {actual!r}"]
        out: list[str] = []
        for key, value in expected.items():
            if key not in actual:
                out.append(f"{path}: missing {key!r}")
            else:
                out.extend(matches(value, actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: expected {expected!r}, got {actual!r}"]
        return [
            p
            for i, (e, a) in enumerate(zip(expected, actual, strict=True))
            for p in matches(e, a, f"{path}[{i}]")
        ]
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def prepare(directory: Path, doc: dict[str, Any]) -> Path:
    """Copy the fixtures to ``directory`` and place the job records; returns the jobs directory."""
    shutil.copytree(VECTOR_DIR / "fixtures", directory, dirs_exist_ok=True)
    jobs = directory / "jobs"
    folder = jobs / "bridge"
    folder.mkdir(parents=True, exist_ok=True)
    for job_id, record in (doc.get("jobs") or {}).items():
        (folder / f"{job_id}.json").write_text(json.dumps(record))
    return jobs


def load(command: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((VECTOR_DIR / f"{command}.json").read_text())
    return doc
