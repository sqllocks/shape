"""The result record of a live check (``format: shape-live-check``, ``version: 1``).

A live check runs against real Fabric items (the nightly job, or by hand with the O-02 secrets).
It records which steps ran and whether each passed, and nothing that identifies the tenant: no
secret, no tenant id, no workspace id. :class:`LiveCheck` collects the steps; :func:`load` reads a
result back and refuses a newer ``version`` than this code understands.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "shape-live-check"
VERSION = 1
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TOP = ("format", "version", "check", "passed", "shape_version", "started", "finished", "steps")
_MAX_ERROR = 300


class LiveCheckError(ValueError):
    """A result file that this code cannot read."""


def scrub(text: str, secrets: Iterable[str] = ()) -> str:
    """``text`` without any of ``secrets``, without GUIDs (tenant, workspace and item ids) and
    short enough for a result file."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "<secret>")
    text = _GUID.sub("<id>", text)
    return text if len(text) <= _MAX_ERROR else text[:_MAX_ERROR] + "..."


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveCheck:
    """Collects the steps of one check; :meth:`result` is the persisted document."""

    def __init__(self, check: str, shape_version: str, secrets: Iterable[str] = ()) -> None:
        self.check = check
        self.shape_version = shape_version
        self.started = _now()
        self.steps: list[dict[str, Any]] = []
        self._secrets = tuple(secrets)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        """Record ``name`` as passed, or as failed with the scrubbed error (which re-raises)."""
        began = time.monotonic()
        try:
            yield
        except BaseException as exc:
            self.steps.append(
                {
                    "name": name,
                    "passed": False,
                    "seconds": round(time.monotonic() - began, 1),
                    "error": scrub(f"{type(exc).__name__}: {exc}", self._secrets),
                }
            )
            raise
        self.steps.append(
            {"name": name, "passed": True, "seconds": round(time.monotonic() - began, 1)}
        )

    def result(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "version": VERSION,
            "check": self.check,
            "passed": bool(self.steps) and all(s["passed"] for s in self.steps),
            "shape_version": self.shape_version,
            "started": self.started,
            "finished": _now(),
            "steps": list(self.steps),
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.result(), indent=2) + "\n", encoding="utf-8")
        return target


def load(path: str | os.PathLike[str]) -> dict[str, Any]:
    """A result file, checked: its ``format``, an integer ``version`` this code understands and
    the fields of version 1."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LiveCheckError(f"{path}: not a readable JSON file ({exc})") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise LiveCheckError(f"{path}: not a {FORMAT} file")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise LiveCheckError(f"{path}: version must be an integer, got {version!r}")
    if version > VERSION:
        raise LiveCheckError(
            f"{path}: written by a newer Shape (format version {version}; this one reads "
            f"up to {VERSION}): upgrade sqllocks-shape-fabric"
        )
    if version < 1:
        raise LiveCheckError(f"{path}: version {version} is not valid")
    missing = [k for k in _TOP if k not in doc]
    if missing:
        raise LiveCheckError(f"{path}: missing {', '.join(missing)}")
    if not isinstance(doc["steps"], list) or any(
        not isinstance(s, dict) or "name" not in s or "passed" not in s for s in doc["steps"]
    ):
        raise LiveCheckError(f"{path}: steps must be a list of {{name, passed}} objects")
    return doc
