"""Running the published test vectors (``docs/bridge/vectors``) against a bridge.

A vector file holds ``cases``: a ``request`` and the ``response`` that must come back. Strings may
contain ``${DIR}`` (a scratch directory that holds a copy of ``fixtures/``; a path under it is
written with ``/`` and compared with the platform's separator); ``"<any>"`` in a
response matches any value. A response matches when every key of the expected one is present with a
matching value (the bridge may add fields in a minor version, never remove one). ``setup`` requests
run first; ``jobs`` are job records placed in the jobs directory; ``sessions`` are demo session
records placed in ``${DIR}/shape-home/sessions`` (the run sets ``SHAPE_HOME=${DIR}/shape-home``);
``needs: "fabric"`` runs the case against the recorded Fabric interactions. A file with
``needs: "no-advanced"`` was recorded without the optional scikit-learn (extra ``advanced``): its
setup and cases run with it hidden, so the answer is the same whether or not it is installed."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

NO_ADVANCED = "no-advanced"
_ADVANCED = ("sklearn", "sklearn.ensemble", "sklearn.metrics", "sklearn.mixture")


@contextlib.contextmanager
def environment(doc: dict[str, Any]) -> Iterator[None]:
    """The environment a vector file was recorded in: with ``needs: "no-advanced"``, importing
    scikit-learn or any part of it fails as if it were not installed (also when an earlier test
    imported it)."""
    if doc.get("needs") != NO_ADVANCED:
        yield
        return
    hidden = set(_ADVANCED) | {n for n in sys.modules if n.split(".")[0] == "sklearn"}
    saved = {name: sys.modules[name] for name in hidden if name in sys.modules}
    try:
        for name in hidden:
            sys.modules[name] = None  # type: ignore[assignment]  # import raises ImportError
        yield
    finally:
        for name in hidden:
            del sys.modules[name]
        sys.modules.update(saved)


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
    "session_id",
    "started_at",
    "finished_at",
)


_UNDER_DIR = re.compile(r"\$\{DIR\}((?:/[\w.-]+)*)")  # ${DIR} and the path written after it


def substitute(value: Any, directory: str, sep: str = os.sep) -> Any:
    """``value`` with ``${DIR}`` replaced by ``directory``. A path under it is written with ``/`` in
    a vector; the bridge answers with the platform's own paths, so ``/`` becomes ``sep`` there
    (``\\`` on Windows)."""
    if isinstance(value, str):
        return _UNDER_DIR.sub(lambda m: directory + m.group(1).replace("/", sep), value)
    if isinstance(value, list):
        return [substitute(v, directory, sep) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, directory, sep) for k, v in value.items()}
    return value


def abstract(value: Any, directory: str, key: str = "", sep: str = os.sep) -> Any:
    """A response as a vector stores it: the scratch directory as ``${DIR}`` (and a path under it
    with ``/``), volatile values as ``"<any>"``."""
    if key in VOLATILE:
        return ANY
    if key in ("job_id", "stream_id", "fabric_run_id") and isinstance(value, str):
        return value if value.startswith("job-0000000") else ANY
    if isinstance(value, str):
        under = re.compile(re.escape(directory) + r"((?:" + re.escape(sep) + r"[\w.-]+)*)")
        return under.sub(lambda m: "${DIR}" + m.group(1).replace(sep, "/"), value)
    if isinstance(value, list):
        return [abstract(v, directory, sep=sep) for v in value]
    if isinstance(value, dict):
        return {k: abstract(v, directory, k, sep) for k, v in value.items()}
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
    sessions = directory / "shape-home" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    for session_id, record in (doc.get("sessions") or {}).items():
        (sessions / f"demo-{session_id}.json").write_text(json.dumps(record))
    return jobs


def load(command: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((VECTOR_DIR / f"{command}.json").read_text())
    return doc
